## FORGE v3 Control Plane

This service is the FORGE v3 control-plane API and WebSocket hub. It manages tasks, agents, context envelopes, GitGuard single‑writer operations, and observability for the Dark Factory.

---

## Quick start

### Build and run locally

```bash
cd cmd/forged

# Build
go build -o forged .

# Run with default SQLite DB
./forged

# Or with explicit DB path
DB_PATH=./.forge/forge-v3.db ./forged
```

By default:

- **HTTP API** listens on `:8081` (override with `PORT`).
- **WebSocket** listens on `:8082` (override with `WS_PORT`).

### Database migrations only

```bash
cd cmd/forged

# Use default DB path
go run . migrate up

# Custom DB path
go run . migrate up -db-path /tmp/forged.db
```

---

## API endpoints

Base URL examples assume `http://localhost:8081`.

### Health & status

- **GET** `/health`  
  Simple liveness probe.

  ```bash
  curl http://localhost:8081/health
  ```

- **GET** `/api/health`  
  JSON health summary (alias of `/health`).

- **GET** `/api/health/detailed`  
  Structured, component‑level health information.

  ```bash
  curl http://localhost:8081/api/health/detailed | jq .
  ```

- **GET** `/api/status`  
  Returns version/phase/status metadata for the control plane.

  ```bash
  curl http://localhost:8081/api/status
  ```

### Tasks

Tasks are the core work units managed by FORGE v3.

- **POST** `/api/tasks`  
  Create (enqueue) a task.

  ```bash
  curl -X POST http://localhost:8081/api/tasks \
    -H 'Content-Type: application/json' \
    -d '{
      "id": "TASK-EXAMPLE-001",
      "domain": "test-domain",
      "project": "test-project",
      "type": "feature",
      "priority": 50
    }'
  ```

- **GET** `/api/tasks`  
  List tasks, optionally filtered by status.

  Query parameters:

  - `status` – optional (`requested`, `planned`, `queued`, `assigned`, `executing`, `paused`, `completed`, `failed`)
  - `limit` – optional (default `50`, max `100`)

  ```bash
  curl 'http://localhost:8081/api/tasks?status=queued&limit=20'
  ```

- **GET** `/api/tasks/{id}`  
  Fetch a single task by ID.

  ```bash
  curl http://localhost:8081/api/tasks/TASK-EXAMPLE-001
  ```

- **GET** `/api/tasks/claimable`  
  List tasks that are currently claimable by agents.

  ```bash
  curl 'http://localhost:8081/api/tasks/claimable?limit=10'
  ```

- **POST** `/api/tasks/{id}/plan`  
  Attach an initial plan to a task and move it to `planned`.

  ```bash
  curl -X POST http://localhost:8081/api/tasks/TASK-EXAMPLE-001/plan \
    -H 'Content-Type: application/json' \
    -d '{
      "plan": "{\"steps\": [\"scan\", \"implement\", \"test\"]}",
      "reason": "initial plan"
    }'
  ```

- **POST** `/api/tasks/{id}/replan`  
  Add a new plan version, preserving history.

- **GET** `/api/tasks/{id}/plans`  
  Return plan history (`plan_versions`) for a task.

- **POST** `/api/tasks/{id}/queue`  
  Move a `planned` task into the `queued` state and attempt assignment.

  ```bash
  curl -X POST http://localhost:8081/api/tasks/TASK-EXAMPLE-001/queue
  ```

- **POST** `/api/tasks/{id}/pause`  
  Pause a non‑terminal task.

- **POST** `/api/tasks/{id}/resume`  
  Resume a paused task, back to `executing`.

- **POST** `/api/tasks/{id}/claim`  
  Claim a queued/requested task for an agent.

  ```bash
  curl -X POST http://localhost:8081/api/tasks/TASK-EXAMPLE-001/claim \
    -H 'Content-Type: application/json' \
    -d '{"agent_id": "forge:kimi"}'
  ```

- **POST** `/api/tasks/{id}/release`  
  Release a claimed task back to `queued`.

- **POST** `/api/tasks/{id}/extend-lease`  
  Extend the lease/`updated_at` timestamp for an assigned task.

- **GET** `/api/tasks/{id}/events`  
  Event‑sourcing history for a task.

  Query parameters:

  - `limit` – optional, default `50`, max `100`

  ```bash
  curl 'http://localhost:8081/api/tasks/TASK-EXAMPLE-001/events?limit=20'
  ```

### Lanes (Dark Factory lifecycle)

Lane transitions track the multi‑lane pipeline (`dev → test → deploy → done`).

- **POST** `/api/tasks/{id}/lane/complete`  
  Attempt to advance a task to the next lane, running gate checks and (if needed) creating approval requests.

- **GET** `/api/tasks/{id}/lane/status`  
  Returns the current lane, next lane, and gate evaluation.

  ```bash
  curl http://localhost:8081/api/tasks/TASK-EXAMPLE-001/lane/status | jq .
  ```

### Agents & health

- **GET** `/api/agents/health`  
  List health information for all agents from `agent_heartbeats`.

  ```bash
  curl http://localhost:8081/api/agents/health | jq .
  ```

- **GET** `/api/agents/{id}/health`  
  Single agent heartbeat record.

- **GET** `/api/agents/{id}/context`  
  Agent context usage `%` and last update timestamp.

  ```bash
  curl http://localhost:8081/api/agents/kimi/context
  ```

### Context envelopes

Context envelopes capture portable agent context (decisions, failures, calibration, key files).

- **POST** `/api/context/envelopes`  
  Generate and persist a new context envelope.

  Request body:

  ```json
  {
    "agent_id": "forge:kimi",
    "domain": "test-domain",
    "project": "test-project",
    "task_id": "TASK-EXAMPLE-001",
    "reason": "handoff before context reset"
  }
  ```

  Example:

  ```bash
  curl -X POST http://localhost:8081/api/context/envelopes \
    -H 'Content-Type: application/json' \
    -d '{
      "agent_id": "forge:kimi",
      "domain": "test-domain",
      "project": "test-project",
      "task_id": "TASK-EXAMPLE-001",
      "reason": "handoff before context reset"
    }'
  ```

- **GET** `/api/context/envelopes/{id}`  
  Fetch a specific envelope (stored JSON content).

- **POST** `/api/context/bootstrap`  
  Bootstrap an agent from its latest envelope.

  ```bash
  curl -X POST http://localhost:8081/api/context/bootstrap \
    -H 'Content-Type: application/json' \
    -d '{"agent_id": "forge:kimi", "task_id": "TASK-EXAMPLE-001"}'
  ```

### XNode (cross‑node messaging)

The XNode controller exposes additional routes under `/api/xnode/**` for cross‑node directives and heartbeats. Key read‑only helpers:

- **GET** `/api/xnode/inbox`  
  List inbox JSONL files per remote node.

- **GET** `/api/xnode/inbox/{node}`  
  Inspect messages for a specific node.

- **GET** `/api/xnode/acks`  
  List acknowledgment files by message ID.

### GitGuard (single‑writer git)

- **POST** `/api/gitguard`  
  Enforce single‑writer git operations with idempotency and branch‑per‑task locks.

  Request body:

  ```json
  {
    "action": "commit",
    "task_id": "TASK-EXAMPLE-001",
    "branch": "feature/example",
    "message": "feat: example change",
    "files": ["file1.go", "file2.go"],
    "author": "forge:kimi"
  }
  ```

  Example:

  ```bash
  curl -X POST http://localhost:8081/api/gitguard \
    -H 'Content-Type: application/json' \
    -d '{
      "action": "commit",
      "task_id": "TASK-EXAMPLE-001",
      "branch": "feature/example",
      "message": "feat: example change",
      "files": ["file1.go"],
      "author": "forge:kimi"
    }'
  ```

  Response is a `GitResult`:

  - `success` (bool)
  - `output` (string)
  - `error` (string, optional)
  - `commit_id` (string, optional)
  - `timestamp` (RFC3339)

- **GET** `/api/gitguard?task_id={id}&limit={n}`  
  List recorded git actions, filtered by task.

### Notifications

- **GET** `/api/notifications`  
  List in‑memory notifications for the TUI.

- **POST** `/api/notifications`  
  Create a notification.

  ```bash
  curl -X POST http://localhost:8081/api/notifications \
    -H 'Content-Type: application/json' \
    -d '{"type": "info", "title": "Test", "message": "Hello"}'
  ```

- **POST** `/api/notifications/{id}/read`  
  Mark a notification as read.

### TUI & metrics

- **GET** `/tui`  
  HTML TUI dashboard.

- **GET** `/api/tui/dashboard`  
  JSON snapshot of dashboard state.

- **GET** `/api/tui/logs?limit={n}`  
  Recent TUI log entries (agents, queue, server).

- **GET** `/metrics`  
  Prometheus‑style metrics for the v3 control plane.

  ```bash
  curl http://localhost:8081/metrics
  ```

- **GET** `/debug`  
  Lightweight JSON debug snapshot (uptime, request counts, active connections, workers).

---

## WebSocket protocol

WebSocket server listens on `ws://{host}:{WS_PORT}/ws` (default port `8082`).

### Connection and registration

Agents connect with a `worker_id` query parameter:

```bash
ws://localhost:8082/ws?worker_id=forge:kimi
```

Immediately after connecting, agents must send a registration message:

```json
{
  "version": "1",
  "type": "agent.register",
  "id": "reg-123",
  "payload": {
    "agent_id": "forge:kimi",
    "name": "forge:kimi",
    "node": "node-2",
    "tier": "standard",
    "capabilities": ["code", "tests"]
  },
  "time": "2026-03-04T13:37:00Z"
}
```

On success, the server responds with an acknowledgment:

```json
{
  "version": "1",
  "type": "agent.register.ack",
  "id": "reg-ack-123",
  "task_id": "",
  "payload": {},
  "time": "2026-03-04T13:37:00Z"
}
```

### Task assignment messages

When a task is assigned to an agent, the hub sends:

```json
{
  "version": "1",
  "type": "task.assigned",
  "id": "evt-001",
  "task_id": "TASK-EXAMPLE-001",
  "payload": {
    "id": "TASK-EXAMPLE-001",
    "domain": "test-domain",
    "project": "test-project",
    "type": "feature",
    "priority": 50,
    "status": "assigned",
    "assigned_to": "forge:kimi"
  },
  "time": "2026-03-04T13:38:00Z"
}
```

Agents are expected to:

1. Claim or accept the task.
2. Execute work.
3. Report status back via WebSocket and/or HTTP.

### Progress and completion messages

Agents send status updates as `WSMessage` frames:

- **Task started**

  ```json
  {
    "version": "1",
    "type": "task.started",
    "task_id": "TASK-EXAMPLE-001",
    "payload": {},
    "time": "2026-03-04T13:39:00Z"
  }
  ```

- **Task completed**

  ```json
  {
    "version": "1",
    "type": "task.completed",
    "task_id": "TASK-EXAMPLE-001",
    "payload": {
      "result": "ok"
    },
    "time": "2026-03-04T13:40:00Z"
  }
  ```

- **Task failed**

  ```json
  {
    "version": "1",
    "type": "task.failed",
    "task_id": "TASK-EXAMPLE-001",
    "payload": {
      "error": "reason here"
    },
    "time": "2026-03-04T13:40:00Z"
  }
  ```

The server updates task status and appends events in `task_events` for each of these messages.

---

## Task lifecycle

The `tasks` table and `TaskStatus` enum define the lifecycle:

- `requested` – task created (e.g. via `/api/tasks` or higher‑level tooling).
- `planned` – a plan has been attached via `/api/tasks/{id}/plan` or `/replan`.
- `queued` – ready to be picked up by agents (`/queue`, `/claimable`).
- `assigned` – assigned to an agent, waiting for work to start.
- `executing` – actively being worked on (worker heartbeat / WebSocket updates).
- `paused` – temporarily stopped (`/pause`), can be resumed.
- `completed` – finished successfully with `result` recorded.
- `failed` – terminal failure with `error` recorded.

### Typical flow

1. **Create task**

   ```bash
   curl -X POST http://localhost:8081/api/tasks \
     -H 'Content-Type: application/json' \
     -d '{"id":"TASK-EXAMPLE-001","domain":"test-domain","project":"test-project","type":"feature"}'
   ```

2. **Plan**

   ```bash
   curl -X POST http://localhost:8081/api/tasks/TASK-EXAMPLE-001/plan \
     -H 'Content-Type: application/json' \
     -d '{"plan":"{\"steps\": [\"scan\",\"implement\",\"test\"]}","reason":"initial"}'
   ```

3. **Queue**

   ```bash
   curl -X POST http://localhost:8081/api/tasks/TASK-EXAMPLE-001/queue
   ```

4. **Assignment**

   - Either via HTTP claim (`/claim`) or via WebSocket assignment from the hub.

5. **Execution**

   - Agent sends `task.started` / `task.completed` / `task.failed` events over WebSocket.

6. **Lane progression**

   - Use `/lane/status` to see readiness and `/lane/complete` to advance `dev → test → deploy → done`, with approvals where required.

Throughout this flow, `/api/tasks/{id}/events` provides a full event‑sourced history for auditing and debugging.

# FORGE v3 API Documentation

**Version:** 3.0.0  
**Phase:** 1 (Active Development)  
**Base URL:** `http://localhost:8081`  
**WebSocket:** `ws://localhost:8082`

---

## Table of Contents

- [Overview](#overview)
- [HTTP API](#http-api)
  - [Health & Status](#health--status)
  - [Tasks](#tasks)
  - [Context Envelopes](#context-envelopes)
  - [Approvals](#approvals)
  - [GitGuard](#gitguard)
  - [XNode (Cross-Node)](#xnode-cross-node)
  - [Agents](#agents)
  - [Metrics](#metrics)
  - [TUI Dashboard](#tui-dashboard)
- [WebSocket Protocol](#websocket-protocol)
  - [Message Format](#message-format)
  - [Handshake](#handshake)
  - [Message Types](#message-types)
  - [Heartbeat](#heartbeat)
- [Data Models](#data-models)
- [Error Handling](#error-handling)
- [Examples](#examples)

---

## Overview

FORGE v3 is a distributed task orchestration system for AI agent fleets. It provides:

- **Task Management**: Lifecycle management with states (requested → planned → queued → assigned → executing → completed/failed)
- **Context Envelopes**: Portable state packages for agent handoffs
- **Bidirectional Sync**: Filesystem ↔ SQLite synchronization for context
- **WebSocket Communication**: Real-time agent orchestration
- **Approval System**: Human-in-the-loop gates with confidence scoring
- **Cross-Node Messaging**: Multi-node mesh network support
- **GitGuard**: Single-writer git operation coordination

---

## HTTP API

### Health & Status

#### GET /health
Basic health check.

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2026-03-04T10:00:00Z"
}
```

#### GET /api/health
Detailed health status.

#### GET /api/status
Service status and version.

**Response:**
```json
{
  "version": "3.0.0",
  "phase": "1",
  "status": "running"
}
```

---

### Tasks

#### GET /api/tasks
List all tasks.

**Query Parameters:**
- `status` (optional): Filter by status (requested, planned, queued, assigned, executing, paused, completed, failed)
- `limit` (optional): Maximum results (default: 50, max: 100)

**Response:**
```json
{
  "tasks": [
    {
      "id": "TASK-BOLD-NODE-042",
      "domain": "codeswiftr-com",
      "project": "interview-simulator",
      "type": "feature",
      "priority": 1,
      "status": "executing",
      "assigned_to": "kimi",
      "created_at": "2026-03-04T09:00:00Z",
      "updated_at": "2026-03-04T09:05:00Z"
    }
  ],
  "count": 1
}
```

#### POST /api/tasks
Create a new task.

**Request:**
```json
{
  "id": "TASK-CUSTOM-ID-001",
  "domain": "codeswiftr-com",
  "project": "interview-simulator",
  "type": "feature",
  "priority": 1,
  "status": "requested",
  "lane": "dev"
}
```

#### GET /api/tasks/{id}
Get a specific task.

#### POST /api/tasks/{id}/plan
Create a plan for a task.

**Request:**
```json
{
  "plan": "1. Analyze codebase\n2. Implement feature\n3. Add tests",
  "reason": "Feature implementation"
}
```

#### POST /api/tasks/{id}/replan
Revise an existing plan.

#### POST /api/tasks/{id}/queue
Move a planned task to the queue.

#### POST /api/tasks/{id}/claim
Claim a task for an agent.

**Request:**
```json
{
  "agent_id": "kimi"
}
```

**Response:**
```json
{
  "status": "claimed",
  "task": { /* task object */ },
  "agent_id": "kimi"
}
```

#### POST /api/tasks/{id}/release
Release a claimed task.

**Request:**
```json
{
  "agent_id": "kimi",
  "reason": "Context exhausted"
}
```

#### POST /api/tasks/{id}/pause
Pause a task.

#### POST /api/tasks/{id}/resume
Resume a paused task.

#### POST /api/tasks/{id}/extend-lease
Extend task lease (5-minute TTL).

#### GET /api/tasks/{id}/events
Get task event sourcing history.

**Query Parameters:**
- `limit` (optional): Number of events (default: 50, max: 100)

#### GET /api/tasks/claimable
Get list of claimable tasks.

#### GET /api/tasks/{id}/plans
Get plan history for a task.

---

### Context Envelopes

Context envelopes are portable state packages for agent handoffs, enabling session continuity across agent restarts.

#### POST /api/context/envelopes
Generate a new context envelope.

**Request:**
```json
{
  "agent_id": "kimi",
  "domain": "codeswiftr-com",
  "project": "interview-simulator",
  "task_id": "TASK-BOLD-NODE-042",
  "reason": "Context > 50%, initiating handoff"
}
```

**Response:**
```json
{
  "id": "01JQPMN7R5FTWJ9HG2B8Z7KFVR",
  "agent_id": "kimi",
  "domain": "codeswiftr-com",
  "project": "interview-simulator",
  "task_id": "TASK-BOLD-NODE-042",
  "created_at": "2026-03-04T10:00:00Z",
  "expires_at": "2026-03-11T10:00:00Z",
  "summary": "Context > 50%, initiating handoff",
  "decisions": [],
  "failures": [],
  "calibration": {},
  "key_files": [],
  "metadata": {
    "lead_context": "# Interview Simulator\n\n**Lead:** kimi..."
  }
}
```

#### GET /api/context/envelopes/{id}
Retrieve a specific envelope.

#### POST /api/context/bootstrap
Bootstrap an agent from the latest envelope.

**Request:**
```json
{
  "agent_id": "kimi",
  "task_id": "TASK-BOLD-NODE-042"
}
```

---

### Approvals

Human-in-the-loop approval system with confidence scoring.

#### GET /api/approvals
List all approvals.

**Query Parameters:**
- `status` (optional): pending, approved, rejected, expired, auto_approved
- `type` (optional): task_completion, merge, deploy, security, budget, pattern, lane, destructive
- `agent_id` (optional): Filter by requesting agent

#### POST /api/approvals
Create a new approval request.

**Request:**
```json
{
  "type": "task_completion",
  "agent_id": "kimi",
  "domain": "codeswiftr-com",
  "title": "Complete feature implementation",
  "description": "Implementation complete with tests",
  "task_id": "TASK-BOLD-NODE-042",
  "risk_score": 0.3,
  "confidence_score": 0.85
}
```

**Response:**
```json
{
  "id": "apr_01JQP...",
  "type": "task_completion",
  "status": "auto_approved",
  "tier": "watch",
  "confidence_score": 0.85,
  "recommendation": "Auto-approve: High confidence, low risk"
}
```

#### GET /api/approvals/{id}
Get approval details.

#### POST /api/approvals/{id}/approve
Approve a request.

**Request:**
```json
{
  "resolved_by": "human@example.com",
  "notes": "Looks good"
}
```

#### POST /api/approvals/{id}/reject
Reject a request.

**Request:**
```json
{
  "resolved_by": "human@example.com",
  "notes": "Needs more tests"
}
```

---

### GitGuard

Single-writer git operation coordination to prevent conflicts.

#### POST /api/gitguard
Execute a git action with idempotency guarantees.

**Request:**
```json
{
  "action": "commit",
  "task_id": "TASK-BOLD-NODE-042",
  "message": "feat: add user authentication",
  "files": ["backend/app/auth.py", "backend/tests/test_auth.py"],
  "author": "kimi <kimi@forge.ai>"
}
```

**Actions:**
- `commit`: Create a commit
- `push`: Push to remote
- `branch`: Create/switch branch
- `merge`: Merge branches
- `pull`: Pull latest changes

**Response:**
```json
{
  "success": true,
  "commit_id": "a1b2c3d4...",
  "output": "[main a1b2c3d] feat: add user authentication",
  "timestamp": "2026-03-04T10:00:00Z"
}
```

---

### XNode (Cross-Node)

Multi-node mesh network for distributed agent fleets.

#### GET /api/xnode/nodes
List all known nodes in the mesh.

**Response:**
```json
{
  "nodes": [
    {
      "id": "node-3",
      "hostname": "node-3.local",
      "address": "100.64.0.1",
      "status": "online",
      "last_heartbeat": "2026-03-04T10:00:00Z"
    }
  ]
}
```

#### POST /api/xnode/nodes/register
Register a new node.

**Request:**
```json
{
  "id": "node-2",
  "hostname": "node-2.local",
  "address": "100.64.0.2"
}
```

#### POST /api/xnode/forward
Forward a message to another node.

**Request:**
```json
{
  "target_node": "node-3",
  "message": {
    "type": "task.assigned",
    "task_id": "TASK-BOLD-NODE-042"
  }
}
```

#### GET /api/xnode/status
Get cross-node communication status.

#### GET /api/xnode/inbox
List XNode inbox messages.

#### GET /api/xnode/inbox/{node}
Get messages for a specific node.

#### GET /api/xnode/acks
List acknowledgment records.

---

### Agents

#### GET /api/agents/health
List all connected agents.

**Response:**
```json
{
  "agents": [
    {
      "agent_id": "kimi",
      "node": "node-2",
      "status": "busy",
      "current_task_id": "TASK-BOLD-NODE-042",
      "context_pct": 45.5,
      "capabilities": ["coding", "testing"],
      "last_seen": "2026-03-04T10:00:00Z",
      "connected_at": "2026-03-04T09:00:00Z"
    }
  ],
  "count": 1
}
```

#### GET /api/agents/{id}/health
Get health for a specific agent.

#### GET /api/agents/{id}/context
Get context percentage for an agent.

**Response:**
```json
{
  "agent_id": "kimi",
  "context_pct": 45.5,
  "last_updated": "2026-03-04T10:00:00Z"
}
```

---

### Metrics

#### GET /metrics
Prometheus-compatible metrics endpoint.

#### GET /api/parity
Run parity check between database and filesystem state.

**Response:**
```json
{
  "consistent": true,
  "tasks_checked": 100,
  "envelopes_checked": 50,
  "mismatches": []
}
```

---

### TUI Dashboard

#### GET /api/tui/logs
Get recent log entries.

**Query Parameters:**
- `limit` (optional): Number of entries (default: 50, max: 100)

#### GET /api/tui/dashboard
Get dashboard data (JSON).

#### GET /tui
Terminal User Interface (interactive).

---

## WebSocket Protocol

The WebSocket protocol enables real-time bidirectional communication between the orchestrator and agent workers.

### Connection

- **URL:** `ws://localhost:8082/ws`
- **Protocol Version:** `1`

### Message Format

All messages use the following envelope format:

```json
{
  "v": "1",
  "type": "agent.register",
  "id": "msg_1234567890",
  "task_id": "TASK-BOLD-NODE-042",
  "payload": { /* type-specific data */ },
  "ts": "2026-03-04T10:00:00Z"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `v` | string | Protocol version: `"1"` |
| `type` | string | Message type (see below) |
| `id` | string | Unique message ID |
| `task_id` | string | Associated task ID (optional) |
| `payload` | object | Type-specific payload |
| `ts` | string | ISO 8601 timestamp |

### Handshake

After connecting, the agent must send a registration message within 10 seconds:

**Agent → Orchestrator:**
```json
{
  "v": "1",
  "type": "agent.register",
  "id": "msg_1234567890",
  "payload": {
    "agent_id": "kimi",
    "name": "kimi (node-2)",
    "node": "node-2",
    "tier": "tier-1",
    "capabilities": ["coding", "testing", "review"]
  }
}
```

**Orchestrator → Agent:**
```json
{
  "v": "1",
  "type": "agent.register_ack",
  "id": "msg_0987654321",
  "payload": {
    "agent_id": "kimi",
    "capabilities": ["coding", "testing", "review"],
    "connected_nodes": ["kimi", "cursor", "gemini"]
  }
}
```

### Message Types

#### Orchestrator → Worker

| Type | Description | Payload |
|------|-------------|---------|
| `agent.register_ack` | Registration acknowledged | `{ agent_id, capabilities, connected_nodes }` |
| `task.assigned` | Task assigned to agent | Task object |
| `generate_envelope` | Request context envelope | `{ agent_id, domain, project, reason }` |
| `bootstrap` | Bootstrap agent with context | `{ agent_id, task_id }` |
| `ping` | Health check ping | `{}` |
| `task.pause` | Pause current task | `{ task_id }` |
| `task.resume` | Resume paused task | `{ task_id }` |
| `task.cancel` | Cancel task | `{ task_id, reason }` |
| `error` | Error notification | `{ message, code }` |

#### Worker → Orchestrator

| Type | Description | Payload |
|------|-------------|---------|
| `agent.register` | Initial handshake | `{ agent_id, name, node, tier, capabilities }` |
| `task.started` | Task execution started | `{ task_id, timestamp }` |
| `task.completed` | Task completed successfully | `{ task_id, result, timestamp }` |
| `task.failed` | Task execution failed | `{ task_id, error, timestamp }` |
| `envelope.generated` | Context envelope created | `{ envelope_id, path }` |
| `pong` | Ping response | `{}` |
| `task.progress` | Progress update | `{ task_id, progress, message }` |

### Heartbeat

- **Ping Interval:** 30 seconds (orchestrator sends ping frame)
- **Pong Response:** Worker must respond with `pong` message or WebSocket pong frame
- **Timeout:** 60 seconds without response triggers disconnect

### Example Flow

```
Agent                      Orchestrator
  |                            |
  |---- agent.register -------->|
  |<--- agent.register_ack ----|
  |                            |
  |<--- task.assigned ---------|
  |---- task.started --------->|
  |                            |
  |---- task.progress -------->|
  |---- task.progress -------->|
  |                            |
  |---- task.completed ------->|
  |                            |
  |<--- generate_envelope -----|
  |---- envelope.generated --->|
```

---

## Data Models

### Task

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique task ID (e.g., `TASK-BOLD-NODE-042`) |
| `domain` | string | Domain (e.g., `codeswiftr-com`) |
| `project` | string | Project name |
| `type` | string | Type: `feature`, `bugfix`, `research`, `refactor` |
| `priority` | int | Priority (1-5, lower is higher) |
| `status` | string | Current status |
| `lane` | string | Execution lane (optional) |
| `assigned_to` | string | Agent ID (when assigned) |
| `plan_version` | int | Current plan version |
| `plan_id` | string | Active plan ID |
| `dependencies` | array | Task dependencies |
| `result` | string | Execution result |
| `error` | string | Error message (if failed) |
| `created_at` | string | Creation timestamp |
| `started_at` | string | Start timestamp (optional) |
| `updated_at` | string | Last update timestamp |

### Context Envelope

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | ULID identifier |
| `agent_id` | string | Creating agent |
| `domain` | string | Associated domain |
| `project` | string | Associated project |
| `task_id` | string | Associated task |
| `created_at` | string | Creation timestamp |
| `expires_at` | string | Expiration (7 days) |
| `summary` | string | Handoff reason |
| `decisions` | array | Key decisions |
| `failures` | array | Failure records |
| `calibration` | object | Calibration data |
| `key_files` | array | Important files |
| `metadata` | object | Additional metadata |

### Approval

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Approval ID |
| `type` | string | Approval type |
| `task_id` | string | Associated task (optional) |
| `agent_id` | string | Requesting agent |
| `domain` | string | Domain |
| `title` | string | Short title |
| `description` | string | Detailed description |
| `risk_score` | float | Risk assessment (0-1) |
| `confidence_score` | float | Confidence score (0-1) |
| `tier` | string | `watch`, `phone`, `desktop` |
| `status` | string | Current status |
| `created_at` | string | Creation timestamp |
| `expires_at` | string | Expiration timestamp |

---

## Error Handling

### HTTP Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Bad Request - Invalid input |
| 404 | Not Found - Resource doesn't exist |
| 409 | Conflict - State conflict (e.g., task not claimable) |
| 429 | Too Many Requests - Rate limited |
| 500 | Internal Server Error |

### Error Response Format

```json
{
  "error": "task_not_claimable",
  "message": "Task not in claimable state, status: executing"
}
```

### WebSocket Errors

Errors are sent as `error` messages:

```json
{
  "v": "1",
  "type": "error",
  "id": "msg_1234567890",
  "payload": {
    "code": "task_not_found",
    "message": "Task TASK-BOLD-NODE-042 not found"
  }
}
```

---

## Examples

### Create and Assign a Task

```bash
# Create task
curl -X POST http://localhost:8081/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "codeswiftr-com",
    "project": "interview-simulator",
    "type": "feature",
    "priority": 1
  }'

# Plan the task
curl -X POST http://localhost:8081/api/tasks/TASK-BOLD-NODE-042/plan \
  -H "Content-Type: application/json" \
  -d '{
    "plan": "1. Research OAuth2\n2. Implement login\n3. Add tests",
    "reason": "User authentication feature"
  }'

# Queue the task
curl -X POST http://localhost:8081/api/tasks/TASK-BOLD-NODE-042/queue

# Agent claims the task
curl -X POST http://localhost:8081/api/tasks/TASK-BOLD-NODE-042/claim \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "kimi"}'
```

### Generate Context Envelope

```bash
curl -X POST http://localhost:8081/api/context/envelopes \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "kimi",
    "domain": "codeswiftr-com",
    "project": "interview-simulator",
    "task_id": "TASK-BOLD-NODE-042",
    "reason": "Context > 50%, initiating handoff"
  }'
```

### WebSocket Client (JavaScript)

```javascript
const ws = new WebSocket('ws://localhost:8082/ws');

ws.onopen = () => {
  // Register
  ws.send(JSON.stringify({
    v: '1',
    type: 'agent.register',
    id: 'msg_' + Date.now(),
    payload: {
      agent_id: 'kimi',
      name: 'kimi (node-2)',
      node: 'node-2',
      tier: 'tier-1',
      capabilities: ['coding', 'testing']
    }
  }));
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  
  switch (msg.type) {
    case 'task.assigned':
      const task = JSON.parse(msg.payload);
      console.log('Task assigned:', task.id);
      
      // Acknowledge start
      ws.send(JSON.stringify({
        v: '1',
        type: 'task.started',
        id: 'msg_' + Date.now(),
        task_id: task.id,
        payload: { timestamp: new Date().toISOString() }
      }));
      break;
      
    case 'ping':
      // Respond with pong
      ws.send(JSON.stringify({
        v: '1',
        type: 'pong',
        id: 'msg_' + Date.now(),
        payload: {}
      }));
      break;
  }
};
```

---

## CLI Commands

The FORGE v3 CLI provides additional commands:

```bash
# System health
forge system health

# Run patrol checks
forge system patrol

# Check git guard status
forge git guard

# Database migrations
forge migrate up
forge migrate down 1
```

---

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | HTTP API port | `8081` |
| `WS_PORT` | WebSocket port | `8082` |
| `DB_PATH` | SQLite database path | `./.forge/forge-v3.db` |
| `DB_TYPE` | Database type (`sqlite` or `postgres`) | `sqlite` |
| `NODE_ID` | Node identifier | Hostname |

---

## See Also

- [Architecture Documentation](../../docs/architecture/V3_ARCHITECTURE.md)
- [E2E Test Plan](../../.forge/v3/E2E_TEST_PLAN.md)
- [WebSocket ADR](../../docs/architecture/ADR-011-websocket-protocol.md)
