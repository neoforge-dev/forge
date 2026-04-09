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
DB_PATH=.forge/forge-v3.db ./forged
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

The XNode controller exposes additional routes under `/api/xnode/**` for cross‑node directives and heartbeats.

- **GET** `/api/xnode/inbox` — List inbox JSONL files per remote node.
- **GET** `/api/xnode/inbox/{node}` — Inspect messages for a specific node.
- **GET** `/api/xnode/acks` — List acknowledgment files by message ID.

### GitGuard (single‑writer git)

- **POST** `/api/gitguard`
  Enforce single‑writer git operations with idempotency and branch‑per‑task locks.

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

- **GET** `/api/gitguard?task_id={id}&limit={n}` — List recorded git actions.

### Notifications

- **GET** `/api/notifications` — List in‑memory notifications.
- **POST** `/api/notifications` — Create a notification.
- **POST** `/api/notifications/{id}/read` — Mark as read.

### TUI & metrics

- **GET** `/tui` — HTML TUI dashboard.
- **GET** `/api/tui/dashboard` — JSON dashboard snapshot.
- **GET** `/api/tui/logs?limit={n}` — Recent log entries.
- **GET** `/metrics` — Prometheus‑style metrics.
- **GET** `/debug` — Lightweight JSON debug snapshot.

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
    "node": "sati",
    "tier": "standard",
    "capabilities": ["code", "tests"]
  },
  "time": "2026-03-04T13:37:00Z"
}
```

### Message types

**Orchestrator → Agent:** `agent.register_ack`, `task.assigned`, `generate_envelope`, `bootstrap`, `ping`, `task.pause`, `task.resume`, `task.cancel`, `error`

**Agent → Orchestrator:** `agent.register`, `task.started`, `task.completed`, `task.failed`, `envelope.generated`, `pong`, `task.progress`

### Heartbeat

- **Ping Interval:** 30 seconds
- **Pong Timeout:** 60 seconds without response triggers disconnect

---

## Task lifecycle

`requested` → `planned` → `queued` → `assigned` → `executing` → `completed` / `failed`

See `CLAUDE.md` for the FSM state machine (ADR-028) which runs in parallel.

---

## Features Not Yet Documented Here

The following daemon features are implemented but documented in `CLAUDE.md`:
- Blueprint runtime (`blueprint.go`) — durable task execution flows
- Stage gate enforcement (`stage_gate.go`) — task type restrictions per portfolio stage
- Portfolio API (`/api/openclaw/portfolio`) — product lifecycle state
- Fleet scaler (`fleet_scaler.go`) — agent scaling recommendations
- Patrol orchestrator (`patrol_orchestrator.go`) — 31 background patrols

See `cmd/forged/CLAUDE.md` for details on these features.

## See Also

- [Canonical Flow](../../docs/CANONICAL_FLOW.md) — product lifecycle
- [CLAUDE.md](CLAUDE.md) — agent guide with blueprint/stage-gate docs
- [TEST_MAP.md](TEST_MAP.md) — canonical test file map
