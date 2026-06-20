# FORGE

**Self-hosted AI Ops Platform for Teams Running Autonomous Agents**

[![Go CI](https://github.com/neoforge-dev/forge/actions/workflows/go.yml/badge.svg)](https://github.com/neoforge-dev/forge/actions/workflows/go.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Go Version](https://img.shields.io/badge/go-1.23+-blue.svg)](https://golang.org)

FORGE is not a coding assistant — it's the **operations layer** that runs under your agents. It manages multi-node fleets, approval-gated execution, YAML-backed agent routing, cross-node messaging, and persistent context across sessions.

| What FORGE does | What it doesn't do |
|---|---|
| Run 5+ agents across prya/sati/nova/vega/gaea | Write code itself |
| Route tasks by YAML capability config | Replace your IDE |
| Gate risky tasks behind human approval | Require cloud accounts |
| Track fleet metrics, budget, inventory | Lock you to any LLM |
| Self-heal via background patrols | — |

**Key differentiators vs Cursor/Copilot/Devin:**

- **Multi-node**: agents run on separate hardware, coordinated via a single hub
- **Approval-gated**: tier-based auto-approve for lightweight tasks, human gate for risky ones
- **YAML routing**: `config/routing/*.yaml` defines agent capabilities, node constraints, workload policy — `forge dispatch auto` reads it
- **Dark Factory**: background patrols that auto-fix, auto-approve, auto-scale (inventory: `forge patrol list`)
- **Self-hosted**: runs on your hardware, no cloud required, no per-seat pricing
- **Auditable**: SQLite event log, pattern library, Royal Jelly context persistence

**Stack:** V4 CLI (`cmd/forge`) + daemon (`cmd/forged`). Configure with `FORGE_API_URL` or `~/.forge/config.toml`.

**Start here:** [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md), [AGENTS.md](AGENTS.md), [docs/ACTIVE_SURFACES.md](docs/ACTIVE_SURFACES.md), [docs/STRATEGY.md](docs/STRATEGY.md)

## Current Public Boundary

This repository is the public FORGE orchestration distribution: CLI, daemon,
task queue, approvals, patrols, routing, and operator documentation. Private
portfolio products, revenue-sensitive state, local pod runtime data, and
research artifacts are intentionally outside the public boundary.

The most current internal operating truth currently lives in `forge-mono`.
Public docs here should be treated as the public distribution surface unless a
command has been verified against this repository. Local `.forge/` state,
coverage files, test binaries, and daemon databases are generated artifacts and
must not be committed.

---

## Quick Start

### Install

```bash
# Build the V4 CLI
cd cmd/forge
go build -o forge .
mv forge ~/.local/bin/forge   # or /usr/local/bin/forge

# Build the daemon
cd cmd/forged
go build -o forged .
mv forged ~/.local/bin/forged
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
forge agent list
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

# Dispatch work to a named agent override
forge dispatch send kimi --file .forge/dispatches/kimi-task.md
```

### Fleet Operations

```bash
# Multi-node fleet (several nouns are hidden — run `forge advanced` to list)
forge status                 # hub + queue snapshot (preferred)
forge node list              # mesh registration
forge fleet list             # same daemon source as agent list (prints alias note)
forge fleet windows          # live tmux agent windows
forge fleet metrics          # per-agent token/task metrics
forge fleet budget           # token budget by agent/provider
forge fleet recommendations  # scale-up/down suggestions

# Auto-route a task to the best agent (YAML-backed since S92)
forge dispatch auto "Run coverage wave on cmd/forged" --task-type coverage

# Bulk-approve queued approvals
forge approval list
forge approval bulk-decide --action approve --domain forge

# Smart routing query (direct API call)
curl -s -X POST http://localhost:8081/api/routing/resolve \
  -H "Content-Type: application/json" \
  -d '{"task_type":"coverage","weight":"heavy"}' | jq .
# → {"agent":"kimi","node":"sati","reason":"best_at match: go-test (node preference)"}
```

### GitHub Integration (Autonomous Issue → PR → Merge)

FORGE connects to GitHub via webhook to close the full issue lifecycle autonomously:

```bash
# 1. Set env vars on the daemon node
export GITHUB_TOKEN="ghp_..."              # PAT with repo + issues write
export GITHUB_WEBHOOK_SECRET="your-secret" # set in GitHub repo settings

# 2. Register the webhook in GitHub repo settings:
#    Payload URL: http://your-forge-node:8081/api/github/webhook
#    Events:      Issues (opened, reopened, labeled)

# 3. When an issue is opened, FORGE automatically:
#    - Creates a FORGE task (visible in `forge task list`)
#    - Posts a comment: "FORGE task created: TASK-XXXX"
#    - Agent claims and works on the task in a git worktree
#    - On completion: opens a PR and creates a merge approval
#    - Posts a comment: "✅ FORGE task completed. PR: https://github.com/..."

# 4. Review and approve the merge:
forge approval list                             # see pending PRs
forge approval decide <approval-id> --approve   # merge the PR
```

The full loop: **GitHub issue → FORGE task → autonomous agent → PR → approval gate → merged**. Requires `GITHUB_TOKEN` for PR creation and merge. All steps are best-effort — the pipeline degrades gracefully if tokens are absent.

---

### Join a Node

```bash
# Register this machine in the mesh
forge node join

# List connected agents
forge agent list
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              forge (V4 CLI)                          │
│   task · agent · dispatch · daemon · node · lead     │
└────────────────────┬────────────────────────────────┘
                     │  HTTP :8081 / WS :8082
┌────────────────────▼────────────────────────────────┐
│              forged (daemon, per-node)               │
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
| `agent` | Agent process inventory | `list`, `show` |
| `lane` | Dark Factory stage | `list`, `show`, `promote` |
| `dispatch` | Fleet messaging | `send`, `auto`, `list`, `status` |
| `fleet` | Multi-node ops | `status`, `metrics`, `inventory`, `budget`, `recommendations`, `windows` |
| `daemon` | Daemon lifecycle | `start`, `stop`, `status`, `restart`, `install`, `logs` |
| `domain` | Business domain | `list`, `show` |
| `project` | Repository | `list`, `show` |
| `approval` | Human checkpoint | `list`, `decide`, `bulk-decide` |
| `queue` | Task scheduling | `list`, `depth`, `show`, `prune` |
| `context` | Knowledge preservation | `list`, `show` |
| `patrol` | Background monitors | `list`, `show` |
| `pattern` | Reusable templates | `list`, `show` |
| `portfolio` | Project portfolio | `list`, `status` |
| `node` | Fleet node | `list`, `status` |
| `lead` | Cross-node lead messaging | `send`, `inbox`, `ack`, `preflight` |
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
FORGED_BIN=/path/to/forged            # Override daemon binary location (FORGE_V3_BIN also accepted for compatibility)
```

### Config File (`~/.forge/config.toml` or project `.forge/config.toml`)

```toml
[control_plane]
url = "http://forge-control-plane:8081"

[node]
id = "node-a"
```

---

## Daemon (forged)

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
| `/api/routing/resolve` | POST | YAML-backed agent routing (returns best agent for task type) |
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

# Daemon
cd cmd/forged
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
│   ├── lead.go         # cross-node lead messaging
│   ├── daemon.go       # daemon lifecycle
│   ├── dispatch*.go    # dispatch + workflow
│   └── internal/
│       ├── client.go   # HTTP client → V3 daemon
│       ├── types.go    # Shared types
│       ├── output.go   # Table/JSON/CSV formatters
│       ├── errors/     # Structured error types
│       ├── websocket/  # WebSocket client
│       └── heartbeat/  # Agent heartbeat monitor
├── cmd/forged/         # daemon (HTTP + WebSocket server)
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
├── harness/            # Python harness for iOS orchestration (private repo only; not in v1 public release — see SCOPE.md)
└── services/ apps/ ios/  # Products organized by type
```

---

## Deployment

Use a shared control-plane host for hub-first deployments. Local daemons are optional:

```bash
# 1. Build daemon on each node
cd cmd/forged && go build -o forged .

# 2. Start via forge daemon (manages PID + logs)
forge daemon start

# 3. Or install as systemd user service
forge daemon install --enable
systemctl --user start forged

# 4. Verify
forge status
forge daemon status
```

Example deployment status:
- control-plane: running
- node-a: connected
- node-b: connected
- node-c: optional

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
forge agent list
```

---

**Phase:** 1.0 (Production) | **CLI:** V4 | **Daemon:** forged | **Last Updated:** 2026-04-07
