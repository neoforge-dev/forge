# FORGE Daemon — Fleet Agent Guide

This is the Go daemon that runs per-node. Read this before touching any code here.

## What is forged

Single Go binary per node. HTTP API on `:8081`, WebSocket hub on `:8082`.
SQLite is the source of truth (`forge-v3.db`). Cross-node sync via `.forge/xnode/` JSONL (Tailscale).

**Running the daemon:**
```bash
# From the FORGE root directory:
forge daemon start          # recommended — uses forge CLI
# or directly:
./cmd/forged/forged --port 8081 --ws-port 8082 --db .forge/forge-v3.db
# DB is at: .forge/forge-v3.db  (relative to FORGE root)
# Logs:    /tmp/forged.log
```

## Build

```bash
cd cmd/forged
go build ./...          # compile check
go test . -timeout 90s  # run main package tests (~78s)
go test ./... -timeout 90s  # run all packages
```

Build tag `!tmux_bridge` is on all source files — always compile without that tag (default).

## Environment Variables

| Var | Default | Purpose |
|-----|---------|---------|
| `PORT` | `8081` | HTTP API port |
| `WS_PORT` | `8082` | WebSocket port |
| `DB_PATH` | `./forge-v3.db` | SQLite path |
| `FORGE_ROOT` | `.` | Root for xnode file paths |
| `NODE_ID` | `os.Hostname()` | This node's identity |
| `FORGE_API_URL` | — | Remote API for CLI commands |
| `FORGE_TAILSCALE_IP` | *(unset)* | Override `detectTailscaleIP()` — set to `""` in tests to skip 2s exec call |
| `FORGE_OPENCLAW_TICK_MS` | `5000` | OpenClaw SSE ticker interval (ms) — tests use 200 |
| `FORGE_AGENTS_SSE_TICK_MS` | `5000` | Agents SSE ticker interval (ms) — tests use 200 |
| `FORGE_XNODE_CHECK_TICK_MS` | `1000` | XNode SSE check ticker interval (ms) — tests use 50 |
| `FORGE_TEST_BUILD_CMD` | `go build ./...` | Override build gate command — tests use `true` |
| `FORGE_CONTEXT_SYNC_DEBOUNCE_MS` | `500` | ContextSync fsnotify debounce (ms) — tests use 50 |

## Key Source Files

| File | What it does |
|------|-------------|
| `main.go` | All HTTP handlers + server startup (~3400 lines) |
| `queue.go` | TaskQueue interface, Task/TaskStatus/TaskState types |
| `websocket.go` | WebSocket hub, agent registration, heartbeat |
| `task_state_machine.go` | ADR-028 FSM: QUEUED→DISPATCHED→RUNNING→COMPLETED |
| `task_store.go` | SQLite CRUD for tasks |
| `xnode.go` | Cross-node messaging via file JSONL |
| `db_conn.go` | Atomic `*sql.DB` pointer: `getDBConn()` / `setDBConn()` |
| `lease.go` | Task lease system (prevents double-claim) |
| `gitguard.go` | Git hygiene enforcement |
| `migrate.go` | SQL migration runner |
| `blueprint.go` | Blueprint runtime: YAML loading, step execution, variable substitution |
| `stage_gate.go` | Stage gate policies: allowed task types per portfolio stage |
| `approvals.go` | Approval workflow: tier policies (watch/phone/desktop) |
| `handlers_openclaw.go` | OpenClaw: chat, dispatch, ingest, notify, events, portfolio |
| `fleet_scaler.go` | Fleet auto-scaling: node ceilings, agent tiers, spawn gates |
| `patrol.go` | 31 background patrols (health, cleanup, auto-promote, etc.) |
| `middleware/` | Timeout, rate limit middleware |
| `integration/` | Old test files — **excluded with `//go:build ignore`** |

## Task Lifecycle (FSM + legacy status)

Two parallel fields on every task:
- `status` (legacy): `requested` → `queued` → `assigned` → `executing` → `completed`
- `state` (FSM/ADR-028): `QUEUED` → `DISPATCHED` → `RUNNING` → `COMPLETED` → `APPROVED`

Both fields are written. New tasks get `state=QUEUED` at creation, `state=DISPATCHED` on claim.

## Key API Endpoints

```
GET  /health                    — daemon health
GET  /api/tasks?limit=N         — list tasks (returns {count, tasks})
POST /api/tasks                 — create task (requires: domain, project, type, title, priority 1-10)
GET  /api/tasks/{id}            — get task
POST /api/tasks/{id}/claim      — claim task {agent_id}
POST /api/tasks/{id}/complete   — complete task
GET  /api/agents/health         — all agent heartbeats
POST /api/agents/{id}/heartbeat — agent heartbeat upsert
GET  /api/tasks/claimable       — tasks available for claiming
```

## Do's and Don'ts

**DO:**
- Use `getDBConn()` / `setDBConn()` — never access `dbConn` directly
- Add `//go:build !tmux_bridge` to any new source files
- Write result files to `.forge/heartbeat/results/`
- `go build ./...` before claiming acceptance criteria

**DON'T:**
- Import `package main` from subdirectories (Go prohibits this)
- Use `log.Fatal` inside handlers — return error responses instead
- Touch `.forge/dispatches/` — gitignored, local-only
- Touch `docs/PROMPT-*.md` — orchestrator-only
- Commit or push — orchestrator does that

## Known Issues / Debt

- `integration/` tests: excluded (`//go:build ignore`) — `package main` not importable from
  subdirectory. To revive: extract tested types into a library package, then use `_test` package.
  56 unique test functions preserved (DarkFactory gate executors, lane integration tests).
- `TestDidYouMeanSuggestions/tas`: fixed (map iteration non-determinism was root cause)
- Coverage: ~24.4% — pre-commit notes 40% floor; S69 target: dispatch ClaimTask/createTaskHandler/UpdateTaskStatus tests
- `phase2_monitor.go`: skeleton only
- `patrol.go:232`: Royal Jelly bidirectional sync TODO (Task #7)

## Blueprint Runtime

Blueprints are durable, task-linked execution flows defined in `config/blueprints/*.yaml`.

**Step types:** `check` (run command, pass/fail), `shell` (run command, capture output),
`dispatch` (send to agent), `review` (human gate), `complete` (finalize with evidence).

**Variable substitution** in command strings:
- `${TASK_ID}` — task ULID
- `${RUN_ID}` — blueprint run ULID
- `${BLUEPRINT_ID}` — blueprint ID (e.g. `validation/problem-fit`)
- `${DOMAIN}` — task's domain field
- `${PRODUCT_KEY}` — task's product/project field
- `${PRODUCT_ALIAS}` — derived short alias (e.g. `voice-coach` → `vc`)

**Key files:** `blueprint.go` (runtime + variable substitution), `config/blueprints/` (YAML definitions)

## Stage Gates

`stage_gate.go` enforces what task types are allowed per portfolio stage. Enforcement is **advisory** —
it warns but does not block task creation. The policy map is `stageGatePolicies` (lines 34-75).

Example: A product in `idea` stage only allows `research`, `validation`, `docs` task types.

## Approval Tiers

`approvals.go` loads tier policies from `config/dark-factory/approval-tiers.yaml` at startup
(falls back to hardcoded defaults). Tiers: `watch` (auto-approve), `phone` (async), `desktop` (blocking).

## Related Docs
- `docs/CANONICAL_FLOW.md` — product lifecycle stages and gates
- `cmd/forge/README.md` — full CLI command reference
- `config/blueprints/` — blueprint YAML definitions
- `config/portfolio/portfolio-state.yaml` — product lifecycle state
