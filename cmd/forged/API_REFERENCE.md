# FORGE Daemon (`forged`) HTTP API Reference

> **Auto-generated on 2026-03-31** from `cmd/forged/main.go` handler registrations.
> Regenerate this file when endpoints change.

Default port: `:8081` (configurable via `PORT` env var).
WebSocket port: `:8082` (configurable via `WS_PORT` env var).

Middleware chain (outermost first): `versionHeaderMiddleware` -> `RateLimitMiddleware` -> `TimeoutMiddleware` -> `LoggingMiddleware` -> `AuthMiddleware`.

---

## 1. Health & Status

| Path | Method | Description |
|------|--------|-------------|
| `/health` | GET | Basic health check (alias) |
| `/api/health` | GET | Basic health check |
| `/api/health/detailed` | GET | Detailed health with subsystem status |
| `/api/status` | GET | Daemon status overview |
| `/api/nodes/health` | GET | Health status of all registered nodes |
| `/api/metrics` | GET | Prometheus-style metrics |
| `/api/events` | GET | SSE event stream |
| `/api/lead-state` | GET | Current lead orchestrator state |
| `/api/coordination/status` | GET | Coordination dashboard status |

## 2. Tasks (CRUD + Actions)

### Core CRUD

| Path | Method | Description |
|------|--------|-------------|
| `/api/tasks` | GET | List tasks (supports `?limit=N`) |
| `/api/tasks` | POST | Create a new task |
| `/api/tasks/{id}` | GET | Get task by ID |
| `/api/tasks/{id}` | PUT | Update task |
| `/api/tasks/{id}` | DELETE | Delete task |
| `/api/tasks/claimable` | GET | List tasks available for claiming |
| `/api/tasks/prune` | POST | Prune stale/zombie tasks |
| `/api/tasks/approve` | POST | Approve task (guard for bare path) |

### Task Actions

| Path | Method | Description |
|------|--------|-------------|
| `/api/tasks/{id}/claim` | POST | Claim a task (assign to agent) |
| `/api/tasks/{id}/complete` | POST | Mark task as completed |
| `/api/tasks/{id}/complete-with-approval` | POST | Complete task with approval workflow |
| `/api/tasks/{id}/approve` | POST | Approve a completed task |
| `/api/tasks/{id}/pause` | POST | Pause a running task |
| `/api/tasks/{id}/resume` | POST | Resume a paused task |
| `/api/tasks/{id}/abandon` | POST | Abandon a claimed task |
| `/api/tasks/{id}/release` | POST | Release a claimed task back to queue |
| `/api/tasks/{id}/ack` | POST | Acknowledge task receipt |
| `/api/tasks/{id}/extend-lease` | POST | Extend task lease duration |
| `/api/tasks/{id}/plan` | POST | Submit execution plan for task |
| `/api/tasks/{id}/replan` | POST | Resubmit execution plan |
| `/api/tasks/{id}/queue` | POST | Queue a task for processing |

### Task Query Endpoints

| Path | Method | Description |
|------|--------|-------------|
| `/api/tasks/{id}/events` | GET | Get task event log |
| `/api/tasks/{id}/history` | GET | Get task state transition history |
| `/api/tasks/{id}/plans` | GET | Get plan history for task |
| `/api/tasks/{id}/quality-gates` | GET | Get quality gate status for task |
| `/api/tasks/{id}/lane/status` | GET | Get lane status for task |
| `/api/tasks/{id}/lane/complete` | POST | Complete lane for task |

## 3. Agents

| Path | Method | Description |
|------|--------|-------------|
| `/api/agents` | GET | List all registered agents |
| `/api/agents/{id}` | GET | Get agent by ID |
| `/api/agents/health` | GET | All agent heartbeat statuses |
| `/api/agents/{id}/heartbeat` | POST | Upsert agent heartbeat |
| `/api/agents/{id}/metrics` | GET | Get agent-specific metrics |
| `/api/agents/{id}/telemetry` | GET/POST | Agent telemetry data |
| `/api/agents/{id}/context` | GET | Get agent context |
| `/api/agents/{id}/tasks` | GET | List tasks assigned to agent |
| `/api/agents/telemetry/summary` | GET | Aggregated telemetry summary across agents |
| `/api/agents/stream` | GET | SSE stream of agent updates (ADR-014) |

## 4. Fleet

| Path | Method | Description |
|------|--------|-------------|
| `/api/fleet/snapshot` | GET | Point-in-time fleet snapshot |
| `/api/fleet/summary` | GET | Fleet summary overview |
| `/api/fleet/recommendations` | GET | Fleet scaling/optimization recommendations |
| `/api/fleet/metrics` | GET | Aggregated fleet metrics (ADR-027) |
| `/api/fleet/aggregate` | GET | Cross-node metric aggregation fan-out (ADR-027) |
| `/api/fleet/node-capabilities` | GET | Node capability manifest (ADR-035) |
| `/api/nodes/{id}/metrics` | POST | Receive local metric rollups from worker nodes (ADR-027) |

## 5. Queue (CLI Router)

These endpoints are consumed by the `forge` CLI v2 noun-verb router.

| Path | Method | Description |
|------|--------|-------------|
| `/cli/task/create` | POST | Create task via CLI |
| `/cli/task/list` | GET | List tasks via CLI |
| `/cli/task/show` | GET | Show task details via CLI (`?id=`) |
| `/cli/task/logs` | GET | Get task event logs via CLI (`?id=`) |
| `/cli/agent/list` | GET | List agents via CLI |
| `/cli/agent/status` | GET | Agent status via CLI |
| `/cli/system/health` | GET | System health via CLI |
| `/cli/queue/depth` | GET | Queue depth metric |
| `/cli/queue/status` | GET | Queue status overview |
| `/cli/queue/list` | GET | List queue contents |
| `/cli/queue/priority` | POST | Adjust queue priority |
| `/cli/queue/cancel` | POST | Cancel queued item |

## 6. Patrols & Executions

| Path | Method | Description |
|------|--------|-------------|
| `/api/patrols` | GET | List all configured patrols |
| `/api/patrols/{id}` | GET | Get patrol by ID / trigger patrol run |
| `/api/patrol-executions` | GET | List patrol execution history |

## 7. Blueprints & Runs

| Path | Method | Description |
|------|--------|-------------|
| `/api/blueprints` | GET/POST | List or create blueprints |
| `/api/blueprints/runs` | GET/POST | List or create blueprint runs |
| `/api/blueprints/runs/{id}` | GET | Get blueprint run by ID |

## 8. Lanes

| Path | Method | Description |
|------|--------|-------------|
| `/api/lanes` | GET | List all lanes |
| `/api/lanes/{id}` | GET | Get lane by ID |

## 9. Contexts & Envelopes

| Path | Method | Description |
|------|--------|-------------|
| `/api/contexts` | GET/POST | List or create contexts |
| `/api/contexts/{id}` | GET/PUT/DELETE | CRUD for individual context |
| `/api/context/envelopes` | GET/POST | List or create context envelopes |
| `/api/context/envelope` | GET/POST | Alias for `/api/context/envelopes` |
| `/api/context/envelopes/{id}` | GET/PUT/DELETE | CRUD for individual envelope |
| `/api/context/bootstrap` | GET/POST | Bootstrap context for new agent |

## 10. Patterns

| Path | Method | Description |
|------|--------|-------------|
| `/api/patterns` | GET/POST | List or create patterns (ADR-018) |
| `/api/patterns/{id}` | GET/PUT/DELETE | CRUD for individual pattern or list pattern runs |

## 11. XNode (Cross-Node Mesh)

| Path | Method | Description |
|------|--------|-------------|
| `/api/xnode/nodes` | GET | List registered nodes |
| `/api/xnode/nodes/register` | POST | Register a new node |
| `/api/xnode/nodes/{id}` | DELETE | Deregister a node |
| `/api/xnode/forward` | POST | Forward message to another node |
| `/api/xnode/status` | GET | XNode mesh status |
| `/api/xnode/events` | GET | SSE delivery of cross-node events |
| `/api/xnode/inbox` | GET | List inbox messages |
| `/api/xnode/inbox/{id}` | GET | List inbox messages for specific node |
| `/api/xnode/acks` | GET | List message acknowledgments |

## 12. Relay (Dispatch Relay)

| Path | Method | Description |
|------|--------|-------------|
| `/api/relay/deliveries` | GET | List relay deliveries (ADR-014) |
| `/api/relay/dispatch` | POST | Dispatch via relay (ADR-014) |
| `/api/relay/{id}/ack` | POST | Acknowledge relay delivery (ADR-014) |

## 13. Messages

| Path | Method | Description |
|------|--------|-------------|
| `/api/messages` | GET/POST | List or create messages |
| `/api/messages/{id}` | GET/PUT/DELETE | CRUD for individual message |

## 14. OpenClaw

| Path | Method | Description |
|------|--------|-------------|
| `/api/openclaw` | GET/POST | OpenClaw chat, dispatch, ingest, notify, events, portfolio |
| `/api/openclaw/{path}` | * | OpenClaw sub-routes (wildcard handler) |

## 15. Auth & Config

| Path | Method | Description |
|------|--------|-------------|
| `/api/auth/tokens` | GET/POST/DELETE | Manage authentication tokens |
| `/api/config` | GET | Get daemon configuration |
| `/api/gitguard` | POST | Git hygiene enforcement check |

## 16. Dashboard & UI

| Path | Method | Description |
|------|--------|-------------|
| `/dash` | GET | Legacy dashboard HTML page |
| `/dashboard` | GET | Fleet health dashboard HTML (ADR-027) |
| `/ui` | GET | Fleet UI HTML page |
| `/ui/domains` | GET | Domains UI page |
| `/ui/patrol/{id}` | GET | Patrol drill-down UI page |
| `/tui` | GET | TUI dashboard HTML |
| `/api/dashboard` | GET | Dashboard JSON data |
| `/api/dashboard/summary` | GET | PWA dashboard summary (ADR-014) |
| `/api/dashboard/agents` | GET | PWA dashboard agents list (ADR-014) |
| `/api/dashboard/agents/{id}` | GET | Agent health detail for dashboard |
| `/api/dashboard/throughput` | GET | Task throughput metrics |
| `/api/agents/dashboard` | GET | Agents dashboard JSON |
| `/api/tui/dashboard` | GET | TUI dashboard JSON |
| `/api/tui/logs` | GET | TUI log stream |

## 17. Other

### Approvals

| Path | Method | Description |
|------|--------|-------------|
| `/api/approvals` | GET/POST | List or create approvals |
| `/api/approvals/count` | GET | Get pending approval count |
| `/api/approvals/pending` | GET | List pending approvals |
| `/api/approvals/{id}` | GET/POST | Get or act on individual approval |

### Handoffs

| Path | Method | Description |
|------|--------|-------------|
| `/api/handoffs` | GET/POST | List or create handoffs |
| `/api/handoffs/{id}` | GET/POST | Get or act on individual handoff |

### Notifications

| Path | Method | Description |
|------|--------|-------------|
| `/api/notifications` | GET/POST | List or create notifications |
| `/api/notifications/{id}` | GET/PUT/DELETE | CRUD for individual notification |

### Projects

| Path | Method | Description |
|------|--------|-------------|
| `/api/projects` | GET/POST | List or create projects |
| `/api/projects/{id}` | GET/PUT/DELETE | CRUD for individual project |

### Domains

| Path | Method | Description |
|------|--------|-------------|
| `/api/domains/{id}` | PATCH | Update domain configuration |

### Workers

| Path | Method | Description |
|------|--------|-------------|
| `/api/workers` | GET/POST | List or register workers |
| `/api/workers/{id}` | GET/PUT/DELETE | CRUD for individual worker |

### Dispatch

| Path | Method | Description |
|------|--------|-------------|
| `/api/dispatch` | POST | Dispatch task to agent |

### GitHub

| Path | Method | Description |
|------|--------|-------------|
| `/api/github/webhook` | POST | GitHub webhook receiver (no auth required) |

### Routing

| Path | Method | Description |
|------|--------|-------------|
| `/api/routing/resolve` | POST | Resolve routing for task dispatch |

### Debug & Parity

| Path | Method | Description |
|------|--------|-------------|
| `/api/debug` | GET | Debug information dump |
| `/api/parity` | GET | Feature parity check |

### WebSocket

| Path | Method | Description |
|------|--------|-------------|
| `/ws` | GET | WebSocket endpoint for real-time multinode communication |

### Fallback

| Path | Method | Description |
|------|--------|-------------|
| `/` | * | Catch-all: proxy to harness if `FORGE_HARNESS_URL` set, else 404 |

---

## Summary Statistics

| Category | Endpoint Count |
|----------|---------------|
| 1. Health & Status | 9 |
| 2. Tasks (CRUD + Actions) | 24 |
| 3. Agents | 10 |
| 4. Fleet | 7 |
| 5. Queue (CLI Router) | 12 |
| 6. Patrols & Executions | 3 |
| 7. Blueprints & Runs | 3 |
| 8. Lanes | 2 |
| 9. Contexts & Envelopes | 6 |
| 10. Patterns | 2 |
| 11. XNode | 9 |
| 12. Relay | 3 |
| 13. Messages | 2 |
| 14. OpenClaw | 2 |
| 15. Auth & Config | 3 |
| 16. Dashboard & UI | 14 |
| 17. Other | 21 |
| **Total** | **132** |
