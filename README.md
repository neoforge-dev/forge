# FORGE

**Multi-Agent Orchestration Platform for Autonomous Software Development**

[![CI](https://github.com/neoforge-dev/forge/actions/workflows/ci.yml/badge.svg)](https://github.com/neoforge-dev/forge/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Go Version](https://img.shields.io/badge/go-1.23+-blue.svg)](https://golang.org)

FORGE manages fleets of AI coding agents across multiple nodes with durable task queues, real-time WebSocket dispatch, human-in-the-loop approvals, and context preservation across sessions.

**Stack:** V4 CLI (`cmd/forge`) + V3 daemon (`cmd/forge-v3`). The CLI supports hub-first control-plane operation and should be configured with your own `FORGE_API_URL` or `~/.forge/config.toml`.

**Start here:** [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md), [AGENTS.md](AGENTS.md), [docs/ACTIVE_SURFACES.md](docs/ACTIVE_SURFACES.md)

---

## Quick Start

### Install

```bash
# Build the V4 CLI
cd cmd/forge
go build -o forge .
mv forge ~/.local/bin/forge   # or /usr/local/bin/forge

# Build the V3 daemon
cd cmd/forge-v3
go build -o forge-v3 .
mv forge-v3 ~/.local/bin/forge-v3
```

### Start the Daemon

```bash
# Initialize config once (recommended)
forge init --node-id $(hostname) --control-plane http://forge-control-plane:8081

# Start, stop, restart, check status
forge daemon start
forge daemon status
forge daemon stop
forge daemon restart

# Install as a systemd user service (Linux)
forge daemon install --enable
```

### First Tasks

```bash
# Check system health
forge status
forge fleet status
forge portfolio status

# List tasks
forge task list
forge task list --status pending --domain sample-domain --limit 20

# Create a task
forge task create \
  --domain sample-domain --project demo-saas \
  --title "Fix OAuth2 redirect" --priority high --lane dev

# Inspect a task
forge task show TASK-BRAVE-PULSE-132

# Check the active product working set
forge portfolio list

# Dispatch work to an agent
forge dispatch send forge:kimi "Read .forge/dispatches/kimi-task.md — EXECUTE now"
```

### Connect a Worker

```bash
# Start a worker (connects via WebSocket to :8082)
forge worker up --id my-worker --capabilities code,test

# List connected workers/agents
forge agent list
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              forge (V4 CLI)                          │
│   task · agent · lane · worker · dispatch · daemon   │
└────────────────────┬────────────────────────────────┘
                     │  HTTP :8081 / WS :8082
┌────────────────────▼────────────────────────────────┐
│              forge-v3 (daemon, per-node)             │
│  SQLite · Task FSM · WebSocket Hub · Patrol · Leases │
└─────────────────────────────────────────────────────┘
```

Current operating model: hub-first, local daemons optional. See [docs/adr/INDEX.md](docs/adr/INDEX.md) for the current ADR status.

---

## CLI Reference

### Core Nouns

| Noun | Purpose | Key Commands |
|------|---------|-------------|
| `task` | Unit of work | `list`, `show`, `create`, `update` |
| `agent` | Worker process | `list`, `show` |
| `worker` | WebSocket worker | `up` |
| `lane` | Dark Factory stage | `list`, `show`, `promote` |
| `dispatch` | Fleet messaging | `send`, `list` |
| `daemon` | Daemon lifecycle | `start`, `stop`, `status`, `restart`, `install`, `logs` |
| `domain` | Business domain | `list`, `show` |
| `project` | Repository | `list`, `show` |
| `approval` | Human checkpoint | `list`, `decide` |
| `queue` | Task scheduling | `list`, `depth`, `show`, `prune` |
| `context` | Knowledge preservation | `list`, `show` |
| `patrol` | Background monitors | `list`, `show` |
| `pattern` | Reusable templates | `list`, `show` |
| `config` | Settings | `get`, `set` |

### Output Formats

All commands support `--format`:

```bash
forge task list --format json    # JSON for piping/jq
forge task list --format csv     # CSV for spreadsheets
forge task list --format table   # Human-readable (default)
forge task list --format quiet   # Exit code only
```

### Key Flags

```bash
forge task list --status pending|assigned|completed
forge task list --domain sample-domain
forge task list --limit 50

forge task create --priority high|medium|low
forge task create --lane dev --metadata key=value
```

---

## Configuration

### Environment Variables

```bash
FORGE_API_URL=http://forge-control-plane:8081
FORGE_WS_URL=ws://localhost:8082       # V3 daemon WebSocket endpoint
FORGE_NODE_ID=node-a                   # Node identity
FORGE_AGENT_ID=kimi                    # Agent identity
FORGE_V3_BIN=/path/to/forge-v3        # Override daemon binary location
```

### Config File (`~/.forge/config.toml` or project `.forge/config.toml`)

```toml
[control_plane]
url = "http://forge-control-plane:8081"

[node]
id = "node-a"
```

---

## V3 Daemon

The daemon exposes:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/tasks` | GET | List tasks (supports `?status=`, `?domain=`, `?limit=`) |
| `/api/tasks` | POST | Create task |
| `/api/tasks/{id}` | GET | Get task |
| `/api/tasks/{id}/claim` | POST | Claim task |
| `/api/tasks/{id}/complete` | POST | Complete task |
| `/api/tasks/{id}/promote` | POST | Promote to next lane |
| `/api/agents` | GET | List agents |
| `/api/agents/{id}/heartbeat` | POST | Agent heartbeat |
| `/api/lanes` | GET | List lanes |
| `/api/lanes/{key}` | GET | Get lane |
| `/api/contexts` | GET | List contexts |
| `/api/contexts/{key}` | GET | Get context |
| `/api/config` | GET | Daemon config |
| `/api/workers` | GET | List workers |
| `/ws` | WS | WebSocket hub (`:8082`) |

### WebSocket Protocol

```json
// Register agent (v=1 is current protocol version)
{ "v": "1", "type": "agent.register", "payload": { "agent_id": "kimi", "capabilities": ["code"] } }

// Server sends task assignment
{ "v": "1", "type": "task.assigned", "payload": { "task_id": "TASK-001" } }

// Heartbeat
{ "v": "1", "type": "heartbeat", "payload": { "agent_id": "kimi", "context_pct": 35 } }
```

---

## Development

### Build & Test

```bash
# V4 CLI
cd cmd/forge
go build ./...
go test ./... -count=1   # All 6 packages must pass

# V3 daemon
cd cmd/forge-v3
go build ./...
go test . -timeout 60s
```

### Project Structure

```
FORGE/
├── cmd/forge/          # V4 CLI (the primary interface)
│   ├── main.go         # Cobra command registration
│   ├── task.go         # task noun
│   ├── agent.go        # agent noun
│   ├── worker.go       # worker noun
│   ├── daemon.go       # daemon lifecycle
│   ├── dispatch*.go    # dispatch + workflow
│   └── internal/
│       ├── client.go   # HTTP client → V3 daemon
│       ├── types.go    # Shared types
│       ├── output.go   # Table/JSON/CSV formatters
│       ├── errors/     # Structured error types
│       ├── websocket/  # WebSocket client
│       └── heartbeat/  # Agent heartbeat monitor
├── cmd/forge-v3/       # V3 daemon (HTTP + WebSocket server)
│   ├── main.go         # All HTTP handlers (~3400 lines)
│   ├── websocket.go    # WebSocket hub + agent registration
│   ├── task_store.go   # SQLite CRUD
│   ├── queue.go        # Task queue types + FSM
│   ├── patrol.go       # 7 background patrol routines
│   ├── lease.go        # Exactly-once task claiming
│   ├── migrate.go      # SQL migration runner
│   └── db/             # Connection management (SQLite/Postgres)
├── .forge/             # Runtime state (gitignored: dispatches/, heartbeat/)
│   ├── context/        # Context envelopes (git-tracked)
│   └── xnode/          # Cross-node JSONL sync
├── docs/               # Architecture decisions, plans, runbooks
│   └── adr/            # ADRs 001–025
├── harness/            # Legacy V2 harness (maintenance mode)
└── portfolio/          # 95 managed projects (11 domains)
```

---

## Deployment

Use a shared control-plane host for hub-first deployments. Local daemons are optional:

```bash
# 1. Build daemon on each node
cd cmd/forge-v3 && go build -o forge-v3 .

# 2. Start via forge daemon (manages PID + logs)
forge daemon start

# 3. Or install as systemd user service
forge daemon install --enable
systemctl --user start forge-v3

# 4. Verify
forge status
forge daemon status
```

Example deployment status:
- control-plane: running
- worker-node-a: connected
- worker-node-b: connected
- worker-node-c: optional

---

## Troubleshooting

```bash
# Daemon not reachable
forge daemon start
# or check logs:
forge daemon logs

# Port in use
lsof -i :8081
forge daemon restart

# Check health
forge status
forge fleet status
```

---

**Phase:** 1.0 (Production) | **CLI:** V4 | **Daemon:** V3 | **Last Updated:** 2026-03-06
