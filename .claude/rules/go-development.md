---
description: Go development conventions for cmd/forge and cmd/forged
globs:
  - "cmd/**/*.go"
  - "**/*.go"
---

# Go Development Rules

## Test File Convention (Council S118)

When writing tests for `cmd/forged/`, **ALWAYS check if a canonical test file exists**. Extend the existing file — never create a new `coverage_wave*_test.go`.

### Canonical Test File Map

| Module / Source File | Canonical Test File |
|---------------------|---------------------|
| `main.go` | `main_test.go` |
| `handlers.go` | `handlers_test.go` |
| `task_queue.go` | `task_queue_test.go` |
| `patrol.go` | `patrol_test.go`, `patrol_consolidated_test.go` |
| `fleet_scaler.go` | `fleet_scaler_test.go` |
| `context_sync.go` | `context_sync_test.go` |
| `xnode.go` | `xnode_test.go`, `xnode_additional_test.go` |
| `gitguard.go` | `gitguard_test.go` |
| `lease.go` | `lease_test.go` |
| `websocket.go` | `websocket_test.go` |
| `auth.go` | `auth_test.go` |
| `blueprint.go` | `blueprint_runtime_test.go` |
| `approval*.go` | `approvals_test.go` |
| `event_bus.go` | `event_bus_test.go` |
| `royal_jelly.go` | `royal_jelly_test.go` |
| `worktree_mgr.go` | `worktree_manager_test.go` |
| `task_store.go` | `task_store_test.go` |
| `openclaw.go` | `openclaw_test.go` |
| `completion.go` | `completion_test.go` |
| `claim_handler.go` | `claim_handler_test.go` |
| `create_handler.go` | `create_handler_test.go` |

**Full map:** See `cmd/forged/TEST_MAP.md`

### Coverage Skip-List (Structural Ceiling — 83.4%)

These functions are **intentionally untested** — do NOT try to cover them:

| Function | File | Reason |
|----------|------|--------|
| `spawnAgent()` | `fleet_scaler.go` | Calls `tmux new-window` — spawns real tmux windows |
| `readLiveRAMMB()` | `fleet_scaler.go` | Reads `/proc/meminfo` — OS-dependent |
| `readLoadAverage()` | `fleet_scaler.go` | Reads `/proc/loadavg` — OS-dependent |
| `tmuxSendKeys()` | `dispatch.go` | Requires live tmux session |
| `startDaemon()` | `main.go` | Binds ports, starts goroutines |
| `InitMetrics()` | `metrics.go` | Starts background goroutine with no stop mechanism |

## Build & Test Commands

```bash
# Build forge CLI
cd cmd/forge && go build -o forge .

# Build forged daemon
cd cmd/forged && go build -o forged .

# Run all tests
cd cmd/forged && go test ./... -v

# Run specific test file
cd cmd/forged && go test -run TestFunctionName -v

# Check for race conditions
cd cmd/forged && go test -race ./...
```

## The Two Canonical Binaries

| Binary | Source | Role |
|--------|--------|------|
| `forge` | `cmd/forge/` | CLI — all fleet operations |
| `forged` | `cmd/forged/` | Daemon — HTTP API :8081, SQLite |

Everything else is either deleted or iOS/portfolio-specific Python harness.

## Key Commands

```bash
forge up              # Start services
forge down            # Stop services
forge monitor         # Open monitor session
forge daemon restart  # Restart daemon after code changes
```

## Error Handling Rules

- Error messages **must include recovery steps**
- Every CLI error should tell the user what went wrong AND how to fix it

## Tools

- `go build` / `go test` — for building and testing
- `qmd search` — find architecture docs before using Grep
