# forged Test Map

**Council Decision S118** — canonical test file convention.

## Coverage Floor Policy (Council Decision)

### Minimum Coverage Requirement

- **Floor:** 60% coverage minimum for all new code
- **Structural Ceiling:** ~83.4% (due to OS-dependent functions in skip-list)
- **Target:** 70-80% for most modules

### How the Skip-List Works

The skip-list documents functions that are **intentionally untested** because they depend on OS state, live processes, or external systems that cannot be mocked without major refactoring. These functions create a **structural ceiling** on achievable coverage.

**Key principle:** Do NOT waste cycles trying to cover skip-listed functions. Focus testing effort on business logic, handlers, and state machines.

### Checking Coverage

```bash
# Generate coverage profile
go test -coverprofile=cover.out ./...

# View coverage by function
go tool cover -func=cover.out

# View HTML report
go tool cover -html=cover.out -o cover.html
```

### CI Integration

**Required:** CI must fail the build if coverage falls below 60%.

```bash
# Example CI check
coverage=$(go test -coverprofile=cover.out ./... 2>&1 | grep -oP '\d+(\.\d+)?%' | head -1 | tr -d '%')
if (( $(echo "$coverage < 60" | bc -l) )); then
    echo "FAIL: Coverage $coverage% is below 60% floor"
    exit 1
fi
```

### Skip-List (Structural Ceiling — 83.4%)

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

---

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
