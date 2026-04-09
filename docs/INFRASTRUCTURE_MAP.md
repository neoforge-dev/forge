# FORGE Infrastructure Map

> Start here. Everything else is detail.

A progressive disclosure guide to all FORGE infrastructure — learn what you need, when you need it.

---

## Quick Reference (30 Seconds)

### Who Am I?

```bash
echo $FORGE_AGENT_TYPE    # fleet | orchestrator
echo $FORGE_AGENT_NAME    # kimi, gemini, pi, etc.
```

| Role | Window | Actions |
|------|--------|---------|
| Fleet Agent | Agent name (kimi, gemini...) | Execute tasks, write results |
| Orchestrator | Hostname (prya, sati...) | Dispatch, review, commit |
| Worktree Agent | (none - spawned by Task) | Code changes on branch |

---

### Operator Console

**Primary:** `http://<control-plane>:8081/ui` — HTMX fleet dashboard (auto-refresh 20s)
**Debug only:** `/dashboard` and `/tui` — compatibility HTML views

### Essential Commands

| Command | Purpose |
|---------|---------|
| `forge status` | Fleet health snapshot |
| `forge fleet windows` | Live tmux agent windows |
| `forge task list` | Available tasks |
| `forge config list` | Show active control-plane target |
| `ls .forge/dispatches/` | Find your dispatch file |
| `cat .forge/dispatches/YOUR-FILE.md` | Read your task |

---

### Write Results

**Path:** `.forge/heartbeat/results/AGENT-TASKID.md`

```markdown
## Status: COMPLETE | BLOCKED | FAILED

## Deliverables
- [x] item completed

## Evidence
- Build: OK
- Tests: PASS
```

---

### Absolute Rules

| Fleet Agents | Orchestrator |
|--------------|--------------|
| ❌ NEVER commit | ✅ Commits all work |
| ❌ NEVER push | ✅ Pushes after review |
| ❌ NEVER dispatch | ✅ Delegates via dispatch |
| ✅ ALWAYS write results | ✅ Reviews before commit |

---

## Tier 1: Essential Knowledge

Everything you need for your first task.

### 1. Role Detection

```bash
echo $FORGE_AGENT_TYPE    # "fleet" or "orchestrator"
echo $FORGE_AGENT_NAME    # your name (kimi, gemini, pi, etc.)

# Fallback if unset:
tmux display-message -p '#W'
# hostname → Orchestrator | agent name → Fleet Agent
```

### 2. Task Pickup

```bash
ls .forge/dispatches/                              # List all dispatches
cat .forge/dispatches/AGENT-TASKID-DATE.md        # Read your task
```

Your dispatch file contains:
- Task description
- Files to modify
- Acceptance criteria
- Where to write results

### 3. Key CLI Commands

| Command | Purpose | When to Use |
|---------|---------|-------------|
| `forge status` | Fleet health | Session start, debugging |
| `forge task list` | Available tasks | Finding work |
| `forge agent list` | Connected agents | Checking availability |
| `forge daemon status` | Daemon health | Infrastructure tasks |

### 4. Result Format

**Write to:** `.forge/heartbeat/results/AGENT-TASKID.md`

```markdown
## Status: COMPLETE

## Deliverables
- [x] path/to/file.go — created/modified
- [x] go build ./... passes

## Evidence
- File: path/to/file.go (42 lines)
- Test: go test ./... PASS
```

| Section | Required? | Purpose |
|---------|-----------|---------|
| `## Status` | **YES** | COMPLETE / BLOCKED / FAILED |
| `## Deliverables` | If COMPLETE | What was done |
| `## Evidence` | If COMPLETE | Proof of success |
| `## Blockers` | If BLOCKED/FAILED | What's blocking |

### 5. Context Budget

| Context % | Action |
|-----------|--------|
| > 50% | Run `/handoff-clean` |
| > 75% | **HARD STOP** — run `/handoff` immediately |

### 6. Forbidden Actions (Fleet Agents)

| Action | Why Forbidden |
|--------|---------------|
| `git commit` | Orchestrator commits all work |
| `git push` | Prevents conflicts |
| `forge dispatch send` | Only orchestrator delegates |
| Editing source code | Unless dispatch explicitly allows |

### 7. PROMPT File L0 Header Schema

Every `docs/PROMPT-{node}.md` must start with this mandatory header:

```markdown
# FORGE — {node} Node State

**Updated:** {YYYY-MM-DD} ({sprint} session {N})
**Node:** {node} ({RAM}, {role description})
**Owner Domains:** {comma-separated domain slugs}
**Git:** {branch}, {clean|dirty}
```

| Field | Required | Format | Example |
|-------|----------|--------|---------|
| Updated | YES | `YYYY-MM-DD (S{sprint} session {N})` | `2026-04-09 (S199 session 6)` |
| Node | YES | `{hostname} ({RAM}, {role})` | `sati (64GB, primary fleet host)` |
| Owner Domains | YES | Domain slugs from `config/domains.yaml` | `codeswiftr-com, brandfocus-ai` |
| Git | YES | `{branch}, {status}` | `main, clean` |

Each node's orchestrator owns its own PROMPT file. Do not edit another node's file.

---

## Architecture Diagrams

Visual maps of the system — start here for quick comprehension.

| Diagram | File | Shows |
|---------|------|-------|
| System Map (C4) | [`docs/architecture/SYSTEM-MAP.md`](architecture/SYSTEM-MAP.md) | Nodes, services, external deps |
| Task FSM | [`docs/architecture/TASK-FSM.md`](architecture/TASK-FSM.md) | Task lifecycle states (ADR-028) |
| Dispatch Flow | [`docs/architecture/DISPATCH-FLOW.md`](architecture/DISPATCH-FLOW.md) | Task creation → execution → commit |

See also: [`V3_ARCHITECTURE.md`](architecture/V3_ARCHITECTURE.md) (prose), [`NODE-LEAD-FSM.md`](architecture/NODE-LEAD-FSM.md) (agent FSM), [`APPROVAL-TIERS.md`](architecture/APPROVAL-TIERS.md) (approval routing)

### Patrol System

Patrols are defined in **`StandardPatrols()`** in `cmd/forged/patrol.go`. IDs and schedules change when patrols are merged or added — **do not rely on a fixed count in prose**.

```bash
forge patrol list    # Canonical list for your installed binary
```

---

## Tier 2: Workflow-Specific Knowledge

What you need for specific types of work.

### Code Tasks

**Read first:**
- `forge-shared/modules/git-workflow.md` — Branch naming, commit conventions
- `forge-shared/modules/code-quality.md` — Linting, testing requirements
- `forge-shared/modules/tech-stack.md` — Python/TS/Go standards

**Key commands:**

```bash
# Git workflow
git checkout -b feat/TASK-ID-description
git add -p
git commit -m "feat(scope): description"

# Testing
go test ./...                    # Go
uv run pytest                    # Python
npm test                         # Node

# Quality
go vet ./...                     # Go lint
uv run ruff check .              # Python lint
npm run lint                     # Node lint
```

**Key paths:**
- `cmd/forge/` — Go CLI (33+ nouns)
- `cmd/forged/` — Go daemon (HTTP API :8081)
- `harness/` — Python harness (iOS only)

### Content Tasks

**Result format for content:**

```markdown
## Status: COMPLETE

## Deliverables
- [x] Blog post: services/{project}/docs/TITLE.md (1,500 words)
- [x] Social content: 5 posts written

## Evidence
- Word count: verified with `wc -w`
- Links: all internal links validated
```

**Content directories:**
- `services/{project}/docs/` or `apps/{project}/docs/` — Project documentation
- `.forge/heartbeat/results/` — Your output goes here

### Infrastructure Tasks

**Daemon management:**

```bash
forge daemon status      # Check health (:8081)
forge daemon start       # Start v3 daemon
forge daemon restart     # After code changes
forge daemon stop        # Graceful shutdown
```

**Patrols (background monitors):**

```bash
forge patrol list                    # List registered patrols
```

**Node management:**

```bash
forge node list              # Mesh status
forge node status <node>     # Ping specific node
```

### iOS Tasks

There is **no** `forge ios` in the **Go** CLI (`cmd/forge/`). iOS automation lives in the **Python harness** (ADR-040: `forge_harness.cli_v2` keeps **`ios` only**):

```bash
cd harness && uv run python -m forge_harness ios --help
# e.g. build / test / sim — see subcommands
```

See `harness/CLAUDE.md`, `harness/forge_harness/ios_harness/README.md`, and the `/ios-agent` skill.

### Crontab Management

**What:** Recurring background tasks managed via system crontab.

| Entry | Schedule | Purpose |
|-------|----------|---------|
| `heartbeat-refresh.sh` | `*/2 * * * *` | Sends agent pulses to daemon |
| `forge notify daily` | `03 07 * * *` | Daily Telegram status digest |
| `forge notify gates` | `17 */4 * * *` | Human gate alerts (revenue blockers) |

**Note:** Stale entries pointing to `/home/openclaw/work/FORGE/` should be updated to `/home/openclaw/work/forge-mono/`.

### Dispatch & Delegation (Orchestrator Only)

**Decision tree:**

```
Need to assign work?
│
├─ Code changes?
│   └─ Task tool (subagent) — 100% reliable
│
├─ Fleet agent task?
│   ├─ PRIMARY: forge task create → agents self-assign via daemon (~99%)
│   └─ SECONDARY: forge dispatch send AGENT "msg" (named agent required)
│
└─ Cross-node?
    └─ forge task create — agents claim from queue
```

**Key file:** `forge-shared/modules/dispatch-decision.md` — Single source of truth

---

## Tier 3: Advanced / Infrastructure Knowledge

For orchestrators and power users.

### Heartbeat System

**What:** Agent health monitoring via periodic pulses

**Key files:**
- `.forge/heartbeat/nodes/{node}.json` — Node health
- `.forge/heartbeat/assignments.log` — Task assignments
- `.forge/heartbeat/loop_state.json` — Work loop state

**Monitoring:**

```bash
forge status                    # Overall health (use this; there is no `forge fleet status`)
forge fleet windows             # Live tmux windows on this node
forge agent list                # Registered / heartbeating agents
```

### Royal Jelly (Persistent Context)

**What:** Domain knowledge that survives agent restarts

**Directory:** `.forge/context/{domain}/`

| File | Purpose |
|------|---------|
| `lead-context.md` | Current state, blockers, priorities |
| `decisions.md` | Architectural choices (append-only) |
| `failures.md` | What didn't work (append-only) |

**Protocol:**
1. Session start → read your domain's `lead-context.md`
2. Handoff → update `lead-context.md`
3. Decisions → append to `decisions.md`
4. Failures → append to `failures.md`

**Guide:** `forge-shared/modules/royal-jelly.md`

### Dark Factory (Autonomous Pipeline)

**What:** Autonomous task execution without human intervention

**Pipeline:**
1. Create → `forge task create`
2. Claim → `forge task claim ID`
3. Run → Agent executes
4. Report → Write result file
5. Auto-complete → System detects results (F2)
6. Auto-promote → Lane advancement (F1)
7. Confidence-approve → Quality gating (F3)
8. Done → Merged and archived

**Status:** ✅ Fully implemented (S158 verified)
- ✅ F1: Auto lane promotion (`autoPromoteCompletedTasksInLane`, patrol.go:958)
- ✅ F2: Result monitoring + auto-complete (`monitorResultFiles`, patrol.go:999)
- ✅ F3: Confidence scoring + auto-approve (`confidenceApproveCompletedTasks`, patrol.go:1166)
- ⚠️ Gap: `quality_gate_results` table exists but no code populates it yet (default score: 0.70)

**Guide:** `forge-shared/modules/dark-factory.md`

### Cross-Node Communication

**Preferred:** **`forge lead send`** — XNode via daemon (`POST /api/xnode/forward`).

```bash
forge lead send --to-node sati --task-id 42 --summary "run wave-99" --durable
forge lead inbox
```

**Legacy git outbox:** `forge message` is **deprecated** in the CLI (still present for older playbooks). Prefer **`lead`** for new cross-node directives.

```bash
# forge message send --to node ...   # deprecated — see `forge message --help` if needed
```

**Node mesh:**

```bash
forge node list              # All nodes
forge node status prya       # Specific node
```

**Inbox:** `.forge/xnode/lead-inbox/{node}.jsonl`

### Council Process & ADR System

**What:** Architecture Decision Records for major changes

**ADR locations:**
- `docs/adr/ADR-XXX-title.md` — Decision records
- `docs/adr/INDEX.md` — ADR index

**Process:**
1. Propose → Create ADR draft
2. Review → Council discussion
3. Decide → Accept/Reject/Supersede
4. Implement → Execute decision

### Fleet Management

**Startup:**

```bash
.forge/scripts/forge-startup.sh    # Full stack bootstrap
```

**Operational commands (examples):**

```bash
forge fleet list              # Agents (daemon; mirrors `forge agent list` with a note)
forge fleet windows           # Live tmux agent windows
forge fleet broadcast "msg"   # Broadcast to fleet
```

**Guide:** `forge-shared/modules/fleet-management.md`

### Orchestrator-Only Operations

**What only the lead can do:**
- Commit code (`git commit`)
- Push changes (`git push`)
- Dispatch to agents (`forge dispatch send`)
- Approve tasks (`forge approval decide`)
- Promote lanes (`forge lane promote`)

**What the lead NEVER does:**
- Edit source code directly
- Run `sed`/`patch` on code files
- Implement features directly

**Guide:** `forge-shared/modules/orchestrator-rules.md`

### Blueprint Runtime

**What:** Durable task blueprints for repeatable workflows

**Location:** `config/blueprints/`

**Commands:**

```bash
forge blueprint list          # List blueprints
forge blueprint run <name>    # Execute blueprint
```

---

## Tool Quick Reference

### "I Need To..." Table

| I Need To... | Use This | NOT This |
|--------------|----------|----------|
| Read a file | `Read` tool | `cat` |
| Search code | `Grep` tool | `grep -E` (broken alias) |
| Find files | `Glob` tool | `find` |
| Edit files | `Edit` / `Write` | `sed` / `awk` |
| Search docs | `qmd search "query"` | Manual hunting |
| Run git commands | `Bash` + git | — |
| Run tests | `Bash` + test command | — |
| Understand architecture | `qmd search` | Grep (too noisy) |
| Dispatch code work | Task tool | `tmux send-keys` |
| Queue routine fleet task | `forge task create` | `tmux send-keys` |
| Dispatch named fleet task | `forge dispatch send` | `tmux send-keys` |
| Check fleet health | `forge status` | Manual checks |

### CLI Commands Reference

| Category | Command | Purpose |
|----------|---------|---------|
| **Tasks** | `forge task list` | Show queue |
| | `forge task create --title "..."` | Create task |
| | `forge task claim ID` | Claim task |
| | `forge task complete ID` | Complete task |
| **Agents** | `forge agent list` | Connected agents |
| **Daemon** | `forge daemon status` | Health check |
| | `forge daemon start` | Start daemon |
| | `forge daemon stop` | Stop daemon |
| **Fleet** | `forge status` | System health |
| | `forge fleet windows` | Live tmux windows |
| **Dispatch** | `forge dispatch send AGENT "msg"` | Send to agent |
| **Patrols** | `forge patrol list` | Background monitors |
| **Approvals** | `forge approval list` | Pending approvals |
| | `forge approval decide ID` | Approve/reject |
| **Nodes** | `forge node list` | Mesh status |
| **Cross-node** | `forge lead send --to-node ...` | Durable node directive |

---

## Reference Cards

### Fleet Agent Card

```
┌────────────────────────────────────────┐
│  FLEET AGENT QUICK CARD                │
├────────────────────────────────────────┤
│  1. FIND TASK                          │
│     ls .forge/dispatches/              │
│                                        │
│  2. READ TASK                          │
│     cat dispatch-file.md               │
│                                        │
│  3. EXECUTE                            │
│     Do the work in dispatch            │
│                                        │
│  4. WRITE RESULTS                      │
│     .forge/heartbeat/results/          │
│     YOURNAME-TASKID.md                 │
│                                        │
│  5. STOP                               │
│     ❌ No commit                       │
│     ❌ No push                         │
│     ❌ No dispatch                     │
└────────────────────────────────────────┘
```

### Orchestrator Card

```
┌────────────────────────────────────────┐
│  ORCHESTRATOR QUICK CARD               │
├────────────────────────────────────────┤
│  1. READ STATE                         │
│     docs/PROMPT.md                     │
│                                        │
│  2. CHECK FLEET                        │
│     forge status                       │
│                                        │
│  3. DELEGATE                           │
│     Task tool OR forge dispatch send   │
│                                        │
│  4. REVIEW                             │
│     .forge/heartbeat/results/          │
│                                        │
│  5. COMMIT                             │
│     git commit + git push              │
│                                        │
│  NEVER: Edit source code directly      │
└────────────────────────────────────────┘
```

---

### Script Locations

FORGE infrastructure is managed via scripts in three canonical locations:

1.  **`bin/`** (19 scripts): High-level deployment and operational tools (e.g., `deploy-gate-runner.sh`, `gitsafe.sh`, `deploy-smoke-test.sh`).
2.  **`.forge/scripts/`** (13 scripts): Internal infrastructure management (e.g., `heartbeat-refresh.sh`, `dark-factory-v3.sh`, `agent-start.sh`).
3.  **`.claude/hooks/`** (8 hooks): Programmatic guardrails for agents (e.g., `pre-commit-hook.sh`).

---

## Module Index

| Module | Purpose | Tier |
|--------|---------|------|
| `dispatch-decision.md` | How to delegate work | 1 |
| `git-workflow.md` | Branch naming, commit conventions | 2 |
| `code-quality.md` | Linting, testing standards | 2 |
| `tech-stack.md` | Python/TS/Go standards | 2 |
| `orchestrator-rules.md` | What lead can/cannot do | 3 |
| `fleet-management.md` | Fleet save/restore | 3 |
| `royal-jelly.md` | Persistent domain context | 3 |
| `dark-factory.md` | Autonomous task pipeline | 3 |
| `human-gates.md` | When to escalate to human | 1 |
| `browser-automation.md` | agent-browser CLI usage | 2 |
| `project-registry.md` | Active portfolio projects | 1 |

---

## Scripts Index (.forge/scripts/)

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `forge-startup.sh` | Full stack bootstrap | System restart |
| `heartbeat-refresh.sh` | Agent heartbeat pulses | Crontab (every 2m) |
| `lead-agent-wrapper.sh` | Auto-restart on context | Lead window |
| `agent-start.sh` | Start fleet agent | New agent spawn |
| `check-docs.sh` | Validate CLI docs match binary | CI / on-demand |
| `install-hooks.sh` | Install git hooks | One-time setup |
| `verify-files.sh` | File location validation | Pre-commit |
| `smart-dispatch.sh` | Logic for auto-routing tasks | Internal use |
| `notify-telegram.sh` | Sends alerts to Telegram | Alerts/Patrols |

---

## State File Locations

| Purpose | Path |
|---------|------|
| Domain context | `.forge/context/{domain}/lead-context.md` |
| Dispatch files | `.forge/dispatches/FILENAME.md` |
| Agent results | `.forge/heartbeat/results/AGENT-TASKID.md` |
| Node heartbeats | `.forge/heartbeat/nodes/{node}.json` |
| Cross-node inbox | `.forge/xnode/lead-inbox/{node}.jsonl` |
| Task queue | `.forge/heartbeat/task_queue.json` |
| Orchestrator state | `.forge/heartbeat/orchestrator-state.json` |

---

## Next Steps

1. **New agent?** → Read `docs/AGENT_QUICK_START.md` (5 minutes)
2. **First task?** → Follow Tier 1 above
3. **Code task?** → Read git-workflow + code-quality modules
4. **Orchestrator?** → Read orchestrator-rules module
5. **Need to find something?** → `qmd search "topic"`

---

## Version

- **Created:** 2026-03-19
- **Last Updated:** 2026-04-04 (S188 — script cleanup, doc consolidation)
- **Status:** Active
