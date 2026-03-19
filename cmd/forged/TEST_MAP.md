# forged Test Map

**Council Decision S118** — canonical test file convention.

## Rule

When writing tests for `cmd/forged/`, **always extend an existing canonical test file**.
Never create new `coverage_wave*_test.go` files.

## Canonical Test Files by Module

| Module / Source File | Canonical Test File | Notes |
|---------------------|---------------------|-------|
| `main.go` | `main_test.go` | Server setup, CLI flags |
| `handlers.go` | `handlers_test.go` | HTTP handler routing |
| `handler_method_validation_test.go` | (self) | 229 MethodNotAllowed tests (consolidated S117) |
| `task_queue.go` | `task_queue_test.go` | Task CRUD, FSM transitions |
| `patrol.go` | `patrol_test.go`, `patrol_consolidated_test.go` | All patrol functions |
| `fleet_scaler.go` | `fleet_scaler_test.go` | Agent scaling, spawn logic |
| `context_sync.go` | `context_sync_test.go` | Context synchronization |
| `xnode.go` | `xnode_test.go`, `xnode_additional_test.go` | Cross-node communication |
| `gitguard.go` | `gitguard_test.go` | Git lock detection, cleanup |
| `lease.go` | `lease_test.go` | Distributed lease management |
| `websocket.go` | `websocket_test.go` | WebSocket connections |
| `auth.go` | `auth_test.go` | Authentication, API keys |
| `blueprint.go` | `blueprint_runtime_test.go` | Blueprint execution |
| `approval*.go` | `approvals_test.go` | Approval workflow |
| `event_bus.go` | `event_bus_test.go` | Event publishing |
| `royal_jelly.go` | `royal_jelly_test.go` | Context persistence |
| `worktree_mgr.go` | `worktree_manager_test.go` | Git worktree management |
| `task_store.go` | `task_store_test.go` | SQLite task persistence |
| `openclaw.go` | `openclaw_test.go` | OpenClaw API endpoints |
| `completion.go` | `completion_test.go` | Shell completion |
| `claim_handler.go` | `claim_handler_test.go` | Task claiming FSM |
| `create_handler.go` | `create_handler_test.go` | Task creation |

## Coverage Skip-List (Structural Ceiling — 83.4%)

These functions are **intentionally untested** because they depend on OS state,
live processes, or external systems that cannot be mocked without major refactoring.
Do NOT waste cycles trying to cover them.

| Function | File | Reason |
|----------|------|--------|
| `spawnAgent()` | `fleet_scaler.go` | Calls `tmux new-window` — spawns real tmux windows |
| `readLiveRAMMB()` | `fleet_scaler.go` | Reads `/proc/meminfo` — OS-dependent |
| `readLoadAverage()` | `fleet_scaler.go` | Reads `/proc/loadavg` — OS-dependent |
| `tmuxSendKeys()` | `dispatch.go` | Requires live tmux session |
| `startDaemon()` | `main.go` | Binds ports, starts goroutines |
| `InitMetrics()` | `metrics.go` | Starts background goroutine with no stop mechanism |
