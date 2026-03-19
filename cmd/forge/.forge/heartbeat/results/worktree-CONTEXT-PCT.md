# worktree-CONTEXT-PCT — Context% Wiring in forge work --daemon

**Status: COMPLETE**
**Date: 2026-03-07**

## Summary

Wired real context window percentage reporting into the `forge work --daemon` autonomous loop and `forge agent heartbeat` command so agents show live context% in `forge agent list`.

## Changes Made

### 1. `cmd/forge/internal/client.go`

Added `SendHeartbeat(ctx context.Context, agentID string, contextPct float64) error`:
- POSTs `{"context_pct": contextPct/100.0}` to `/api/agents/{agentID}/heartbeat`
- Converts the 0-100 CLI range to the 0.0-1.0 API range
- Inserted before `GetAgentTasks` in the Agent API section

### 2. `cmd/forge/agent.go`

Added `agentHeartbeatCmd` subcommand (`forge agent heartbeat <agent-id>`):
- Accepts `--context-pct float` flag (0-100 range, default 0.0)
- Calls `client.SendHeartbeat` and prints confirmation
- Registered in `init()` alongside existing agent subcommands

### 3. `cmd/forge/workflow_work.go`

Added `readContextPct() float64` helper (priority order):
1. `FORGE_CONTEXT_PCT` environment variable
2. `.forge/heartbeat/context_percent` sidecar file (written by `context_guard.sh` hook)
3. Defaults to 0.0

Modified `runWorkDaemon`:
- Added a `sendHeartbeat()` closure that reads context% and calls `client.SendHeartbeat`
- Called once after the initial claim attempt (on startup)
- Called after every ticker cycle (after each poll + optional claim)
- Heartbeat errors are logged to stderr but never fatal (daemon may not be running)

## Build Result

```
cd cmd/forge && go build ./...   # zero errors
```

## Test Result

```
go test ./... -count=1 -timeout 90s
ok  github.com/bogdan-velscu/FORGE/cmd/forge/internal           3.955s
ok  github.com/bogdan-velscu/FORGE/cmd/forge/internal/errors    0.004s
ok  github.com/bogdan-velscu/FORGE/cmd/forge/internal/heartbeat 0.009s
ok  github.com/bogdan-velscu/FORGE/cmd/forge/internal/output    0.004s
ok  github.com/bogdan-velscu/FORGE/cmd/forge/internal/websocket 0.565s
FAIL github.com/bogdan-velscu/FORGE/cmd/forge  (3 pre-existing failures)
```

Pre-existing failures (confirmed present on baseline before changes):
- `TestLoadPatrols` — patrol Type field empty in fixture
- `TestPatrolStatusFormats` — patrol-fleet not found
- `TestPatrolLogsFormats` — patrol-fleet not found

Zero new failures introduced.

## Files Modified

- `./cmd/forge/internal/client.go`
- `./cmd/forge/agent.go`
- `./cmd/forge/workflow_work.go`
