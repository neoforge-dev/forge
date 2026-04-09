# FORGE CLI Reference

> Auto-generated 2026-03-30 from `cmd/forge/` source. Regenerate when commands change.

## Global Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | `.forge/forge.yaml` | Config file path |
| `--format` | `table` | Output format (`table`, `json`, `csv`, `quiet`) |
| `-l`, `--log-level` | `info` | Log level (`debug`, `info`, `warn`, `error`) |

---

## 1. Core Operations

### forge task

Task management -- units of work in the queue. Alias: `t`

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `task list` | List active tasks (queued, assigned, running) | `--status`, `--domain`, `--limit 50`, `--all` |
| `task show <id>` | Show task details | |
| `task create` | Create a new task | `--title` (required), `--domain`, `--product`, `--type`, `--priority`, `--lane`, `--metadata`, `--portfolio`, `--description` |
| `task update <id>` | Update a task's properties | `--subject`, `--description`, `--priority`, `--status` |
| `task delete <id>` | Delete a task | `--force` |
| `task claim <id>` | Claim a task for an agent | `--agent`, `--next` |
| `task ack <id>` | Acknowledge a dispatched task (DISPATCHED -> RUNNING) | `--agent` |
| `task complete <id>` | Mark a task as complete | `--result`, `--result-file` |
| `task abandon <id>` | Mark a task as abandoned | |
| `task history <id>` | Show state transition history for a task | |
| `task logs [id]` | Show task state transition history / event log | `--recent N`, `--agent`, `--format` |
| `task quality-gate <id>` | Record quality gate results for a task | `--test-pass-rate`, `--coverage`, `--lint-issues` |
| `task prune` | Prune completed/failed tasks and zombie assigned tasks | `--older-than`, `--dry-run` |
| `task watch <id>` | Watch a task until acceptance criteria pass | `--cmd`, `--interval`, `--max`, `--retries` |

### forge agent

Agent management. Alias: `ag`

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `agent list` | List all connected agents | |
| `agent show <id>` | Show agent details | |
| `agent tasks <id>` | List tasks for an agent | |
| `agent heartbeat <id>` | Send a heartbeat for an agent | `--context-pct` |
| `agent ping <id>` | Check agent liveness (daemon + tmux) | |

### forge dispatch

Send work to agents.

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `dispatch send <agent> [message]` | Send a task to an agent | `--file`, `--domain`, `--project`, `--priority`, `--tmux-session`, `--no-tmux`, `--wait-ack`, `--require-live`, `--check-tmux` |
| `dispatch auto [message]` | Dispatch to best agent by task type | `--task-type`, `--domain` |
| `dispatch status` | Show dispatch feedback (outbox + relay) | `--agent` |
| `dispatch show <task-id>` | Show dispatch (task) status | |
| `dispatch clean` | Archive stale dispatch files | `--dry-run` |
| `dispatch check-results` | Quality-gate result files | `--min-bytes`, `--since` |
| `dispatch reassign-stale` | Re-dispatch tasks with no result after timeout | `--timeout`, `--dry-run` |

### forge queue

Inspect the task queue. Alias: `q`

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `queue` (default) | Show queue summary (counts by state) | `--format` |
| `queue list` | List queued tasks, priority-ordered | `--format`, `--limit` |
| `queue depth` | Print the number of queued tasks (scripting) | `--format` |
| `queue status` | Show queue status summary (alias for `forge queue`) | `--format` |
| `queue priority <task-id>` | Change the priority of a queued task | `--priority` (required) |
| `queue cancel <task-id>` | Cancel a queued task | |

### forge complete

Full task completion workflow: test -> commit -> push -> mark complete. *Hidden.*

```
forge complete <task-id> [--run-tests] [--message "..."] [--no-push] [--dry-run]
```

---

## 2. Portfolio

### forge domain

Manage domains -- business domains in the portfolio.

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `domain list` | List all domains | `--active` |
| `domain show <key>` | Show domain details | |
| `domain create` | Create a new domain | `--key` (required), `--name` (required) |
| `domain update <key>` | Update domain metadata | `--score`, `--status` |
| `domain validate <domain>` | Show validation stage and blockers | |
| `domain assumptions <domain>` | List P0 assumptions with test status | |
| `domain decide <domain> <GO\|KILL>` | Record a go/kill decision | |

### forge product

Manage products (repositories/projects within domains).

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `product list` | List all products | `--domain` |
| `product show <key>` | Show product details | |

### forge project

*Deprecated: use `forge product` instead.*

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `project list` | List all projects | `--domain` |
| `project show <key>` | Show project details | |
| `project create` | Create a new project | `--name`, `--key`, `--domain`, `--description`, `--type` |

### forge portfolio

Manage the portfolio operating loop (idea -> validate -> build -> deploy -> measure -> monetize -> scale).

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `portfolio status` | Show the portfolio operating-loop summary | |
| `portfolio list` | List tracked portfolio products | `--stage` |
| `portfolio show <key>` | Show portfolio product details | |
| `portfolio advance <key>` | Advance a product to the next stage | `--to`, `--dry-run`, `--force` |

---

## 3. Fleet & Monitoring

### forge fleet

Fleet-wide operations. Alias: `fl`

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `fleet list` | List all fleet agents | |
| `fleet broadcast <message>` | Broadcast message to all agents | |
| `fleet windows` | Show live tmux agent windows | |
| `fleet spawn <agent>` | Start a named agent in a tmux window | |
| `fleet kill <agent>` | Kill a named agent tmux window | |
| `fleet budget` | Token budget management (parent) | |
| `fleet budget show` | Show token budget status | |
| `fleet budget set <provider>` | Set token budget usage | |
| `fleet budget reset <provider>` | Reset token budget to zero | |
| `fleet metrics` | Show fleet-wide metrics from the daemon | |
| `fleet recommendations` | Show fleet scale recommendations (ADR-034) | |
| `fleet plan` | Show next planned fleet action | |

### forge status

Show fleet health snapshot (morning standup).

| Flag | Description |
|------|-------------|
| `--full` | Show full fleet standup |
| `--json` | Output as JSON (deprecated: use `--format json`) |
| `--format` | Output format: `table` or `json` |

### forge patrol

Background monitors and patrols. Alias: `pt`

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `patrol list` | List all patrols (grouped by function) | `--type`, `--all` |
| `patrol status <id>` | Show patrol status | |
| `patrol logs <id>` | Show patrol logs | `-n`, `--lines` |
| `patrol task-timeout` | Abandon stale assigned tasks (auto-recovery patrol) | `--daemon`, `--interval` |

### forge heartbeat

Send agent heartbeats to the daemon.

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `heartbeat` (root) | Send a single heartbeat | `--agent` (required), `--interval`, `--daemon`, `--context-pct` |
| `heartbeat eval` | Increment session counter and print trigger recommendations | |
| `heartbeat status` | Print current heartbeat state without incrementing | |
| `heartbeat reset` | Zero all counters and start a new session | |
| `heartbeat retro` | Mark a retro as done at the current heartbeat count | |
| `heartbeat crossnode` | Mark a crossnode sync as done | |
| `heartbeat run` | Autonomous eval loop with optional auto-commit | `--interval`, `--max-iterations`, `--commit`, `--context-max` |

### forge doctor

Run full health checks on the FORGE installation (daemon, SQLite, git lock, .forge/ structure, fleet, token budget).

### forge preflight

Run session boot health checks (git, daemon, fleet, disk, queue, gates).

| Flag | Description |
|------|-------------|
| `--fix` | Auto-fix stale git locks |
| `--json` | Machine-readable JSON output |

### forge dashboard

Generate a markdown status board (active work, queue, completions, fleet health, gates).

| Flag | Description |
|------|-------------|
| `--file` | Write output to file instead of stdout |
| `--json` | Output raw JSON instead of markdown |

---

## 4. Infrastructure

### forge daemon

Manage the FORGE daemon. Alias: `dm`

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `daemon start` | Start the FORGE daemon | `--foreground` |
| `daemon stop` | Stop the FORGE daemon | `--force` |
| `daemon status` | Check daemon status | |
| `daemon restart` | Rebuild (optional) and restart the daemon | `--skip-build` |
| `daemon install` | Install daemon as a systemd service | `--enable`, `--user` |
| `daemon logs` | View daemon log output | `-f`/`--follow`, `-n`/`--lines` |

### forge node

Manage XNode mesh nodes.

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `node list` | List all nodes in the XNode mesh | |
| `node status [node-id]` | Show mesh status or ping a specific node | |
| `node join` | Register this node with the FORGE hub and start fleet agents | `--node-id`, `--node-addr`, `--hub`, `--agents`, `--check`, `--no-pull`, `--no-build` |
| `node unregister <node-id>` | Remove a node from the XNode mesh | `--hub` |

### forge relay

Dispatch relay worker. *Hidden.*

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `relay start` | Start the relay worker | `--interval`, `--no-daemon` |
| `relay stop` | Stop the relay worker | |
| `relay status` | Show relay worker state and delivery statistics | |
| `relay deliveries` | List recent relay delivery records | `--tail`, `--state` |

### forge lead

Cross-node XNode messaging. *Hidden.*

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `lead send` | Send a directive to a remote node | `--to-node` (required), `--task-id`, `--summary` (required), `--durable` |
| `lead inbox` | List incoming XNode messages | `--node`, `--format` |
| `lead ack <message-id>` | Acknowledge an XNode message | `--node` |
| `lead acks` | List all XNode message acknowledgements | `--node`, `--format` |
| `lead preflight` | Verify a remote node is reachable | `--to-node` (required) |
| `lead swap` | Hot-swap the lead orchestrator to a different harness | `--to` (required), `--dry-run` |

### forge lock

Multi-scope file lock manager. *Hidden.*

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `lock acquire` | Acquire a lock for the given scope | `--scope` (required), `--path`, `--task`, `--agent`, `--ttl`, `--force` |
| `lock release` | Release a held lock | `--scope` (required), `--path`, `--task`, `--agent`, `--force` |
| `lock status` | Show the status of a single lock | `--scope` (required), `--path`, `--task` |
| `lock list` | List all active locks | |
| `lock cleanup` | Remove all expired locks | |

### forge config

Manage configuration.

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `config get [key]` | Get configuration value (or all settings) | |
| `config set <key> <value>` | Set configuration value | |
| `config list` | List all configuration | |

### forge message

Cross-node git-based message bus. *Deprecated: use `forge lead send`.*

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `message send` | Send a message to another node | `--to`, `--priority`, `--durable` |
| `message list` | List inbox (or outbox) messages | `--outbox`, `--limit` |

---

## 5. Workflow

### forge approval

Manage approvals -- human checkpoints.

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `approval list` | List approvals | `--pending`, `--domain` |
| `approval show <id>` | Show approval details | |
| `approval decide <id>` | Approve or reject an approval | `--approve`, `--reject`, `--reason` |
| `approval bulk-decide` | Auto-approve pending approvals above confidence threshold | `--threshold`, `--dry-run` |

### forge lane

Manage Dark Factory lanes. *Hidden.*

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `lane list` | List all lanes | |
| `lane show <key>` | Show lane details | |
| `lane status` | Show status of all lanes | |
| `lane promote <task-id>` | Promote a task to the next lane | `--to`, `--comment` |

### forge pattern

Manage reusable patterns and templates. *Hidden.*

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `pattern list` | List available patterns | `--category`, `--tag` |
| `pattern show <id>` | Show pattern details | `--render` |

### forge blueprint

Validate and run durable task blueprints.

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `blueprint validate <file-or-id>` | Validate a blueprint YAML file | |
| `blueprint run` | Start a blueprint run for a task | |
| `blueprint status` | Show the status of a blueprint run | |
| `blueprint list` | List all configured blueprints | |
| `blueprint resume` | Resume a paused or failed blueprint run | |
| `blueprint show` | Show blueprint details | |

### forge state

Lead orchestrator FSM state management. *Hidden.*

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `state show` | Display the current FSM state | `--json` |
| `state set <STATE>` | Transition to a new FSM state | `--reason`, `--json` |
| `state log` | Show recent FSM transitions | `-n`/`--limit`, `--json` |
| `state context` | Show or write context window usage percentage | `--pct`, `--json` |
| `state nudge` | Show or clear the lead nudge file (anti-thrash) | `--clear`, `--json` |
| `state escalate` | Show or set the HUMAN_ATTENTION_REQUIRED marker | `--clear`, `--message`, `--json` |

### forge workflow

Manage and run workflow definitions. *Hidden.*

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `workflow validate <file>` | Validate a workflow definition file | |
| `workflow run <file>` | Execute a workflow definition | |

### forge work

Enter project context / autonomous claim loop.

| Flag | Description |
|------|-------------|
| `--show` | Show current context |
| `--clear` | Clear current context |
| `-e`, `--export` | Print export statements for current shell |
| `--daemon` | Autonomous claim loop: poll and claim tasks |
| `--agent` | Agent ID for claim loop |
| `--interval` | Poll interval for `--daemon` mode |
| `--max-tasks` | Max tasks to claim (0 = unlimited) |
| `--execute` | Execute tasks after claiming |

### forge ship

Complete and ship current work (quality gates -> commit -> push). *Hidden.*

```
forge ship run -m "commit message" [--dry-run] [--skip-checks] [--approve]
```

### forge handoff

Create and manage agent handoff documents.

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `handoff clean` | Create a clean handoff with session state | `-a`/`--agent`, `-r`/`--reason`, `--skip-commit`, `--json` |
| `handoff read <id>` | Read a handoff document | `--json` |

---

## 6. Context

### forge context

Manage knowledge contexts. *Hidden.*

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `context list` | List all contexts | `--domain`, `--project` |
| `context show <key>` | Show context details | |
| `context create` | Create a new context | `--key`, `--domain`, `--project`, `--type`, `--file`, `--content` |
| `context envelope` | Create a context envelope for a task | `--task`, `--file` |
| `context status` | Show context window usage and thresholds | |
| `context update` | Write context% to status file | `--percentage` |
| `context recover` | Send /handoff + /clear + /continue to agent window | `--agent` |
| `context handoff-agent` | Send /handoff to agent window | `--agent` |
| `context clear-agent` | Send /clear to agent window | `--agent` |

### forge council

Council-as-a-service (ADR-035). *Hidden.*

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `council start` | Start a council session | `--size`, `--ttl`, `--agents` |
| `council status` | Show active council sessions | |
| `council stop [agent]` | End a council session | `--all` |

---

## 7. External

### forge notify

Send FORGE status/alerts to Telegram via openclaw.

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `notify status` | Send fleet health snapshot to Telegram | `--channel`, `--target`, `--dry-run`, `--quiet` |
| `notify gates` | Send pending human gates to Telegram | (same) |
| `notify fleet` | Send agent list with context% to Telegram | (same) |
| `notify tasks` | Send recent tasks to Telegram | (same) |
| `notify daily` | Send full daily digest to Telegram | (same) |
| `notify alert [message]` | Send custom alert to Telegram | (same) |
| `notify test` | Send test message to verify setup | (same) |

### forge notion

Sync portfolio data to Notion.

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `notion status` | Verify Notion API connection and database access | `--database` |
| `notion sync` | Sync portfolio or task data to Notion | `--type` (`portfolio`/`tasks`), `--dry-run`, `--database` |

### forge seo

SEO tools for portfolio landing pages.

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `seo audit <domain>` | Audit SEO readiness of a domain's landing page | |

### forge leads

Query lead capture data from Cloudflare KV.

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `leads list` | List captured leads | `--domain`, `--format` |
| `leads count` | Count leads by domain | `--format` |
| `leads get <email>` | Find a lead by email address | `--format` |
| `leads delete <email>` | Delete a test lead by email (GDPR / cleanup) | |

### forge trinity

Trinity -> Prya delegation bridge.

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `trinity delegate [task]` | Delegate a task from Trinity to Prya | `--priority`, `--context`, `--notify`, `--dry-run` |
| `trinity list` | Show recent delegations | `--last` |

---

## 8. Utility

### forge version

Show version information. *Hidden.*

### forge completion

Generate shell completion script. *Hidden.*

```
forge completion [bash|zsh|fish|powershell]
```

### forge self-update

Rebuild and reinstall the forge CLI binary. *Hidden.* (Build tag: `private`)

### forge recover

Clean up common blockers (stale locks, rebase state).

| Flag | Description |
|------|-------------|
| `--dry-run` | Show what would be fixed without changes |

### forge init

Initialize FORGE configuration (setup wizard). Creates `~/.forge/forge.toml`.

| Flag | Description |
|------|-------------|
| `--node-id` | Node ID (default: hostname) |
| `--control-plane` | Control plane URL |
| `--forge-root` | FORGE_ROOT directory |
| `--agent` | Agent setup mode |

### forge env

Print FORGE environment variables. *Hidden.*

### forge git

Git operations with fleet-safe retry. *Hidden.*

| Subcommand | Description | Key Flags |
|------------|-------------|-----------|
| `git push` | Pull-rebase-push with fleet-safe retry | `--retry`, `--branch`, `--remote`, `--target` |
| `git guard` | Check repository for unsafe git state | `--strict` |
| `git commit` | Conventional commit with retry + mutex | `-m`/`--message` (required), `--allow-empty`, `--skip-guard`, `--max-attempts` |
| `git mutex wait` | Poll until .git/index.lock is gone | `--timeout` |
| `git clear-locks` | Remove stale .git/index.lock (>120s old) | |

### forge gate

Human gates blocking revenue.

| Subcommand | Description |
|------------|-------------|
| `gate status` | Show all human gates blocking revenue |

### forge deploy

Deploy one or all products (reads `config/deploys.yaml`).

| Flag | Description |
|------|-------------|
| `--all` | Deploy all products |
| `--dry-run` | Print steps without executing |
| `--backend-only` | Skip frontend deployment |
| `--frontend-only` | Skip backend deployment |

### forge check

Local validation before push (lint, format, type checks, tests). *Hidden.*

```
forge check [--quick] [--full] [--fix] [--json]
```

### forge plugin

Manage FORGE plugins (git-style subprocess dispatch). *Hidden.*

| Subcommand | Description |
|------------|-------------|
| `plugin list` | List installed plugins |
| `plugin install <path>` | Install plugin from file or URL |
| `plugin remove <name>` | Remove an installed plugin |
| `plugin info <name>` | Show plugin information |

### forge advanced

Show all advanced and internal commands (lists hidden commands).

### forge up / forge down

Start/stop the FORGE daemon and agent windows. *Hidden.*

### forge monitor

Live TUI dashboard for fleet and task monitoring. *Hidden.*

### forge tui

Rich 7-view terminal dashboard (Overview/Agents/Tasks/Approvals/Sprints/Logs/Detail). *Hidden.*

---

## Summary Statistics

| Metric | Count |
|--------|-------|
| **Top-level commands** | 47 |
| **Total commands (including subcommands)** | ~160 |
| **Visible in `forge --help`** | 24 |
| **Hidden (visible via `forge advanced`)** | 23 |
| **Deprecated** | 2 (`project`, `message`) |

### Command Count by Category

| Category | Top-level | Subcommands | Total |
|----------|-----------|-------------|-------|
| Core Operations | 5 (`task`, `agent`, `dispatch`, `queue`, `complete`) | 33 | 38 |
| Portfolio | 4 (`domain`, `product`, `project`, `portfolio`) | 18 | 22 |
| Fleet & Monitoring | 6 (`fleet`, `status`, `patrol`, `heartbeat`, `doctor`, `preflight`, `dashboard`) | 23 | 30 |
| Infrastructure | 7 (`daemon`, `node`, `relay`, `lead`, `lock`, `config`, `message`) | 32 | 39 |
| Workflow | 8 (`approval`, `lane`, `pattern`, `blueprint`, `state`, `workflow`, `work`, `ship`, `handoff`) | 27 | 35 |
| Context | 2 (`context`, `council`) | 12 | 14 |
| External | 5 (`notify`, `notion`, `seo`, `leads`, `trinity`) | 14 | 19 |
| Utility | 12 (`version`, `completion`, `self-update`, `recover`, `init`, `env`, `git`, `gate`, `deploy`, `check`, `plugin`, `advanced`, `up`, `down`, `monitor`, `tui`) | 10 | 22 |
