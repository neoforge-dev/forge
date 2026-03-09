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
go test . -timeout 60s  # run main package tests
go test ./... -timeout 60s  # run all packages
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
- Commit or push — orchestrator does that

## Known Issues / Debt

- `integration/` tests: excluded (`//go:build ignore`) — `package main` not importable from
  subdirectory. To revive: extract tested types into a library package, then use `_test` package.
  56 unique test functions preserved (DarkFactory gate executors, lane integration tests).
- `TestDidYouMeanSuggestions/tas`: fixed (map iteration non-determinism was root cause)
- Coverage: ~24.4% — pre-commit notes 40% floor; S69 target: dispatch ClaimTask/createTaskHandler/UpdateTaskStatus tests
- `phase2_monitor.go`: skeleton only
- `patrol.go:232`: Royal Jelly bidirectional sync TODO (Task #7)
