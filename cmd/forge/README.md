# FORGE V4 CLI

The unified command-line interface for FORGE. Talks to the V3 daemon over HTTP (`:8081`) and WebSocket (`:8082`).

## Install

```bash
cd cmd/forge
go build -o forge .
mv forge ~/.local/bin/forge
forge --version
```

## Quick Reference

```bash
# Daemon lifecycle
forge daemon start / stop / status / restart / logs
forge daemon install --enable          # systemd user service

# Tasks
forge task list
forge task list --status pending --domain codeswiftr-com --limit 20
forge task show TASK-BRAVE-PULSE-132
forge task create --domain codeswiftr-com --project my-proj \
  --title "Fix auth bug" --priority high --lane dev \
  --metadata key=value

# Dispatch to fleet agents
forge dispatch send forge:kimi "Read .forge/dispatches/task.md — EXECUTE now"

# Workers / Agents
forge worker up --id my-worker --capabilities code,test
forge agent list

# Lanes
forge lane list
forge lane show dev
forge lane promote TASK-001 --to test

# Queue
forge queue list
forge queue depth
forge queue prune --completed --older 7d

# Other
forge domain list
forge project list --domain codeswiftr-com
forge approval list --pending
forge context list
forge config get
```

## Output Formats

```bash
forge task list --format table   # default, human-readable
forge task list --format json    # machine-readable, pipe to jq
forge task list --format csv     # export to spreadsheet
forge task list --format quiet   # exit code only (0=ok)
```

## Configuration

```bash
export FORGE_API_URL=http://prya:8081        # default hub
export FORGE_WS_URL=ws://localhost:8082
export FORGE_NODE_ID=prya
export FORGED_BIN=/path/to/forged            # override daemon binary (FORGE_V3_BIN also accepted for backward compat)
```

The CLI also reads `~/.forge/config.toml` and project `.forge/config.toml` before falling back to the built-in hub default.

## Implementation Status

| Noun | Commands | Status |
|------|----------|--------|
| `task` | list, show, create, update | ✅ Done |
| `agent` | list, show | ✅ Done |
| `worker` | up (WebSocket, reconnect with backoff) | ✅ Done |
| `lane` | list, show, promote | ✅ Done |
| `dispatch` | send, list | ✅ Done |
| `daemon` | start, stop, status, restart, install, logs | ✅ Done |
| `queue` | list, depth, show, populate, prune, import-dispatches | ✅ Done |
| `domain` | list, show | ✅ Done |
| `project` | list, show | ✅ Done |
| `approval` | list, decide | ✅ Done |
| `context` | list, show | ✅ Done |
| `patrol` | list, show | ✅ Done |
| `pattern` | list, show | ✅ Done |
| `config` | get, set | ✅ Done |
| `fleet` | status | ✅ Done |

## Client Features

The internal HTTP client (`internal/client.go`) includes production-ready features:

- **Automatic Retries**: Exponential backoff for 5xx errors and transient network issues.
- **Connection Pooling**: Optimized for high-throughput fleet operations.
- **Authentication**: Supports `Authorization: Bearer` via `FORGE_API_TOKEN`.
- **Debug Mode**: Enable via `FORGE_DEBUG=1` to see request/response logs.
- **Context Support**: Full cancellation and timeout propagation.

## Internal Structure

```
cmd/forge/
├── main.go                  # Cobra root + all noun registration
├── task.go                  # task noun
├── agent.go                 # agent noun
├── worker.go                # worker noun (WebSocket client, auto-reconnect)
├── daemon.go                # daemon lifecycle (start/stop/PID/systemd)
├── lane.go                  # lane noun
├── dispatch*.go             # dispatch + workflow commands
├── queue.go                 # queue noun
├── domain.go / project.go   # domain + project nouns
├── approval.go              # approval noun
├── context.go               # context noun
├── patrol.go                # patrol noun
├── pattern.go               # pattern noun
├── config.go                # config noun + viper integration
├── fleet.go                 # fleet status
└── internal/
    ├── client.go            # HTTP client (all V3 API calls, DaemonUnreachableError)
    ├── types.go             # Shared types (Task, Agent, Lane, ...)
    ├── output.go            # Table/JSON/CSV/quiet formatters
    ├── services.go          # ClientFactory
    ├── errors/              # Structured exit codes + CLIError types
    ├── websocket/           # WebSocket client (worker connection)
    ├── heartbeat/           # Heartbeat monitor + context % tracking
    └── output/              # Color helpers + formatter utilities
```

## Tests

```bash
cd cmd/forge
go test ./... -count=1       # All 6 packages
go test ./... -run TestTask  # Run specific tests
```

All 6 packages pass: `forge`, `internal`, `internal/errors`, `internal/heartbeat`, `internal/output`, `internal/websocket`.

## Error Handling

When the control plane is unreachable:

```
Error: control plane unreachable
  URL: http://prya:8081/api
```

Exit codes (from `internal/errors`):
- `0` — success
- `1` — general error
- `2` — misuse / bad args
- `3` — daemon offline
- `4` — permission denied
- `5` — not found
- `6` — timeout

## License

MIT
