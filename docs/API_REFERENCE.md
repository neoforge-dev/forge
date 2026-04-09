# FORGE Daemon — API Reference

**Base URL:** `http://localhost:8081` (default PORT)
**WebSocket:** `ws://localhost:8082` (default WS_PORT)
**Auth:** `FORGE_API_TOKEN` header; `local` mode (no auth) when unset
**Version:** v3 (ADR-028 FSM: QUEUED → DISPATCHED → RUNNING → COMPLETED → APPROVED)

All endpoints return `Content-Type: application/json` unless noted. Errors return `{"error": "message"}`.

---

## Table of Contents

1. [Health & Status](#1-health--status)
2. [Tasks](#2-tasks)
3. [Agents](#3-agents)
4. [Notifications](#4-notifications)
5. [Projects & Workers](#5-projects--workers)
6. [Config, Dispatch & GitHub](#6-config-dispatch--github)
7. [Lanes](#7-lanes)
8. [Contexts & Patterns](#8-contexts--patterns)
9. [Context Management (Royal Jelly)](#9-context-management-royal-jelly)
10. [Fleet & Nodes](#10-fleet--nodes)
11. [Blueprints](#11-blueprints)
12. [Routing, Lead State & Messages](#12-routing-lead-state--messages)
13. [Coordination](#13-coordination)
14. [Dashboard & TUI](#14-dashboard--tui)
15. [Patrols](#15-patrols)
16. [Approvals](#16-approvals)
17. [Handoffs](#17-handoffs)
18. [Relay (ADR-014)](#18-relay-adr-014)
19. [OpenClaw](#19-openclaw)
20. [XNode (Cross-Node)](#20-xnode-cross-node)
21. [CLI Bridge](#21-cli-bridge)
22. [WebSocket](#22-websocket)
23. [Metrics, Debug & Auth](#23-metrics-debug--auth)
24. [GitGuard & Events](#24-gitguard--events)

---

## 1. Health & Status

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Basic health check — alias for `/api/health` |
| GET | `/api/health` | Daemon health |
| GET | `/api/health/detailed` | Extended health with component breakdown |
| GET | `/api/status` | Daemon version, phase, running status |
| GET | `/api/nodes/health` | Per-node health from agent heartbeats |
| GET | `/api/events` | SSE event stream for real-time fleet events |

---

## 2. Tasks

### Task CRUD

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tasks` | List tasks (query: `limit`, `status`, `assigned_to`) |
| POST | `/api/tasks` | Create task (`domain`, `project`, `type`, `title`, `priority 1-10`) |
| GET | `/api/tasks/claimable` | List tasks available for claiming |
| GET | `/api/tasks/prune` | Prune old completed/failed tasks |
| GET | `/api/tasks/{id}` | Get single task |
| PUT | `/api/tasks/{id}` | Update task fields |
| DELETE | `/api/tasks/{id}` | Soft-delete a task |

### Task Actions

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/tasks/{id}/claim` | Claim task (`{agent_id}` in body) |
| POST | `/api/tasks/{id}/complete` | Mark task completed |
| POST | `/api/tasks/{id}/complete-with-approval` | Complete with human approval gate |
| POST | `/api/tasks/{id}/approve` | Approve task completion |
| POST | `/api/tasks/{id}/pause` | Pause executing task |
| POST | `/api/tasks/{id}/resume` | Resume paused task |
| POST | `/api/tasks/{id}/abandon` | Abandon task (agent died) |
| POST | `/api/tasks/{id}/release` | Release claimed task back to queue |
| POST | `/api/tasks/{id}/extend-lease` | Extend task lease TTL |
| POST | `/api/tasks/{id}/queue` | Re-queue a task |

> Note: `/api/tasks/approve` (no ID) is a guard endpoint for the bare approve path.

### Task Plans

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/tasks/{id}/plan` | Generate plan for task |
| POST | `/api/tasks/{id}/replan` | Regenerate plan |
| GET | `/api/tasks/{id}/plans` | Get plan history |

### Task Lane

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tasks/{id}/lane/status` | Lane quality gate status |
| POST | `/api/tasks/{id}/lane/complete` | Complete lane |

### Task Audit

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tasks/{id}/events` | Task event log |
| GET | `/api/tasks/{id}/history` | Task state history |
| GET | `/api/tasks/{id}/quality-gates` | Quality gate results |

---

## 3. Agents

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agents` | List all agents with heartbeats |
| GET | `/api/agents/health` | Agent health summary |
| GET | `/api/agents/stream` | SSE stream of agent status updates |
| GET | `/api/agents/telemetry/summary` | Fleet-wide telemetry summary |
| GET | `/api/agents/dashboard` | Agents dashboard data |
| GET | `/api/agents/{id}` | Get agent by ID |
| GET | `/api/agents/{id}/context` | Agent context percentage |
| POST | `/api/agents/{id}/heartbeat` | Agent heartbeat upsert |
| GET | `/api/agents/{id}/telemetry` | Agent telemetry data |
| GET | `/api/agents/{id}/metrics` | Agent metrics |
| GET | `/api/agents/{id}/tasks` | Tasks assigned to agent |

---

## 4. Notifications

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/notifications` | List notifications |
| POST | `/api/notifications/{id}` | Perform action on notification |

---

## 5. Projects & Workers

### Projects

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/projects` | List projects |
| GET | `/api/projects/{id}` | Get project by ID |

### Workers

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/workers` | List workers |
| GET | `/api/workers/{id}` | Get worker by ID |

---

## 6. Config, Dispatch & GitHub

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/config` | Daemon configuration |
| POST | `/api/dispatch` | Dispatch task to agent |
| POST | `/api/github/webhook` | GitHub webhook receiver (no auth required) |

---

## 7. Lanes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/lanes` | List all lanes |
| GET | `/api/lanes/{id}` | Get lane by ID |

---

## 8. Contexts & Patterns

### Contexts

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/contexts` | List context domains |
| GET | `/api/contexts/{id}` | Get context domain by ID |

### Patterns

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/patterns` | List pattern library |
| GET | `/api/patterns/{id}` | Get pattern by ID |
| GET | `/api/patterns/{id}/runs` | Execution runs for a pattern |

---

## 9. Context Management (Royal Jelly)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/context/envelopes` | List context envelopes |
| GET | `/api/context/envelope` | Alias for `/api/context/envelopes` |
| GET | `/api/context/envelopes/{id}` | Get context envelope by ID |
| POST | `/api/context/bootstrap` | Bootstrap context for new agent |

---

## 10. Fleet & Nodes

### Fleet

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/fleet/snapshot` | Live fleet overview (ADR-014 CC retirement) |
| GET | `/api/fleet/recommendations` | Fleet scaling recommendations |
| GET | `/api/fleet/summary` | Fleet summary statistics |
| GET | `/api/fleet/node-capabilities` | Node agent type capabilities (ADR-035) |
| GET | `/api/fleet/metrics` | Aggregate fleet metrics |
| GET | `/api/fleet/aggregate` | Cross-node metric aggregation (ADR-027) |

### Nodes

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/nodes/{id}/metrics` | Worker node posts local metrics |

---

## 11. Blueprints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/blueprints` | List all blueprints |
| GET | `/api/blueprints/runs` | List blueprint execution runs |
| GET | `/api/blueprints/runs/{id}` | Get specific blueprint run |

---

## 12. Routing, Lead State & Messages

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/routing/resolve` | Resolve routing decision (YAML-backed) |
| GET | `/api/lead-state` | Lead orchestrator state |
| GET | `/api/messages` | List cross-node messages |
| GET | `/api/messages/{id}` | Get message by ID |

---

## 13. Coordination

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/coordination/status` | Sprint coordination status (query: `?sprint=N`) |

---

## 14. Dashboard & TUI

### Dashboard

| Method | Path | Description |
|--------|------|-------------|
| GET | `/ui` | Fleet UI dashboard (full HTML) |
| GET | `/dashboard` | Dashboard HTML page |
| GET | `/dash` | Dashboard HTML (alternate) |
| GET | `/api/dashboard` | Dashboard JSON |
| GET | `/api/dashboard/summary` | PWA bridge — dashboard summary |
| GET | `/api/dashboard/agents` | PWA bridge — agent list |
| GET | `/api/dashboard/agents/{id}` | Agent health for specific agent |
| GET | `/api/dashboard/throughput` | Task throughput metrics |
| GET | `/api/parity` | Parity check between daemon and harness |

### TUI

| Method | Path | Description |
|--------|------|-------------|
| GET | `/tui` | Terminal UI dashboard |
| GET | `/api/tui/dashboard` | TUI data as JSON |
| GET | `/api/tui/logs` | TUI log stream |

---

## 15. Patrols

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/patrols` | List all patrols and last run status |
| GET | `/api/patrols/{id}` | Get patrol run by ID |
| GET | `/api/patrol-executions` | Recent patrol execution history |
| GET | `/ui/patrol/{id}` | Patrol UI drill-down page |

---

## 16. Approvals

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/approvals` | List all approvals |
| GET | `/api/approvals/count` | Pending approval count (`{"pending": N}`) |
| GET | `/api/approvals/pending` | List pending approvals |
| POST | `/api/approvals/{id}/approve` | Approve |
| POST | `/api/approvals/{id}/reject` | Reject |
| POST | `/api/approvals/{id}/decide` | Decide — approve or reject via `decision` field |

---

## 17. Handoffs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/handoffs` | List handoffs |
| POST | `/api/handoffs` | Create handoff |
| POST | `/api/handoffs/{id}/accept` | Accept a handoff |
| POST | `/api/handoffs/{id}/reject` | Reject a handoff |
| POST | `/api/handoffs/{id}/complete` | Complete a handoff |

---

## 18. Relay (ADR-014)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/relay/deliveries` | List relay delivery receipts |
| POST | `/api/relay/dispatch` | Create relay delivery record |
| POST | `/api/relay/{id}/ack` | Acknowledge relay delivery |

---

## 19. OpenClaw

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/openclaw` | OpenClaw status |
| GET | `/api/openclaw/{id}` | OpenClaw instance |
| GET | `/api/openclaw/{id}/status` | Instance status |
| GET | `/api/openclaw/{id}/events` | Instance events |
| GET | `/api/openclaw/{id}/portfolio` | Portfolio data |
| POST | `/api/openclaw/{id}/ingest` | Ingest data |
| POST | `/api/openclaw/{id}/chat` | Chat message |
| POST | `/api/openclaw/{id}/dispatch` | Dispatch via OpenClaw |
| POST | `/api/openclaw/notify` | Send notification |

---

## 20. XNode (Cross-Node)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/xnode/nodes` | List registered nodes |
| POST | `/api/xnode/nodes/register` | Register a node |
| POST | `/api/xnode/forward` | Forward message to peer node |
| GET | `/api/xnode/status` | XNode subsystem status |
| GET | `/api/xnode/events` | SSE delivery for cross-node events |
| GET | `/api/xnode/inbox` | List inbox messages |
| GET | `/api/xnode/inbox/{id}` | Get inbox message by ID |
| GET | `/api/xnode/acks` | List acknowledgment records |

---

## 21. CLI Bridge

> Thin wrappers for the `forge` v2/v4 CLI (`/cli/*`). Support `?format=json|plain|csv`.

### Task Commands

| Method | Path | CLI Command |
|--------|------|-------------|
| POST | `/cli/task/create` | `forge task create` |
| GET | `/cli/task/list` | `forge task list` |
| GET | `/cli/task/show` | `forge task show` |
| GET | `/cli/task/logs` | `forge task logs` |

### Agent Commands

| Method | Path | CLI Command |
|--------|------|-------------|
| GET | `/cli/agent/list` | `forge agent list` |
| GET | `/cli/agent/status` | `forge agent status` |

### System Commands

| Method | Path | CLI Command |
|--------|------|-------------|
| GET | `/cli/system/health` | `forge system health` |

### Queue Commands

| Method | Path | CLI Command |
|--------|------|-------------|
| GET | `/cli/queue/depth` | `forge queue depth` |
| GET | `/cli/queue/status` | `forge queue status` |
| GET | `/cli/queue/list` | `forge queue list` |
| POST | `/cli/queue/priority` | `forge queue priority` |
| POST | `/cli/queue/cancel` | `forge queue cancel` |

---

## 22. WebSocket

| Method | Path | Description |
|--------|------|-------------|
| WS | `/ws` | WebSocket hub for real-time multinode communication |

---

## 23. Metrics, Debug & Auth

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/metrics` | Prometheus-format metrics |
| GET | `/api/debug` | Debug information |
| GET | `/api/auth/tokens` | Auth tokens |

---

## 24. GitGuard & Events

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/gitguard` | Git single-writer lock status |
| GET | `/api/events` | SSE event stream (fleet events) |

---

## Error Responses

All error responses use this shape:

```json
{"error": "description of what went wrong"}
```

**HTTP Status Codes:**
- `200` — OK
- `201` — Created
- `400` — Bad request
- `401` — Unauthorized
- `404` — Not found
- `405` — Method not allowed
- `429` — Rate limited
- `500` — Internal server error

---

## Authentication

Production: set `FORGE_API_TOKEN` env var on the daemon. Clients pass the token:

```
Authorization: Bearer <FORGE_API_TOKEN>
```

Dev (`local` mode): no token required. Daemon logs a warning at startup when `FORGE_API_TOKEN` is unset.

---

## Notes

- **Ports:** HTTP API defaults to `:8081` (PORT env), WebSocket defaults to `:8082` (WS_PORT env)
- **CLI endpoints** support `?format=plain|json|csv` for output formatting
- **XNode:** Cross-node messaging via JSONL files at `.forge/xnode/`; Tailscale HTTP for transport
- **FSM (ADR-028):** Tasks have both `status` (legacy) and `state` (FSM). FSM: `QUEUED → DISPATCHED → RUNNING → COMPLETED → APPROVED`
