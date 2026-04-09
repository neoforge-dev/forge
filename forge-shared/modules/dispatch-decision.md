# Dispatch Decision — Single Source of Truth

> **Every dispatch decision in FORGE starts here.** CLAUDE.md, AGENTS.md, and AGENT_QUICK_START.md all point here. Do not duplicate this content elsewhere. Previously `fleet-dispatch.md`.

## Reliability Summary (read this first)

| Method | Reliability | Use When |
|--------|-------------|----------|
| **`forge work --daemon --interval 15s`** | **~99%** | **PRIMARY: agent self-assigns from queue — default agent mode** |
| Task tool (subagent) | **100%** | All code implementation, debug, tests (worktree isolation) |
| `forge dispatch send` | **~95%** | SECONDARY: manual operator assignment to a specific named agent |
| Raw `tmux send-keys` | **~25%** | **NEVER for task delivery** — git locks + input race |

**Council-approved default (S130):** Fleet agents boot into `forge work --daemon --interval 15s`.
Orchestrators add tasks to the queue; agents self-assign. No tmux dispatch needed for routine work.

`forge dispatch send` is now the **secondary** path — use it only when you need to direct a specific
task to a specific named agent window (manual override). For everything else, add to queue and let
agents claim.

**Agent target format:** use the tmux window name / agent ID only, for example `kimi`, `gemini`,
`glm`, `minimax`. Do **not** include the `forge:` tmux session prefix in `forge dispatch send`.

Raw tmux fails ~75% of the time for task delivery due to git lock collisions and input buffer
races. Use `forge dispatch send` instead. tmux is only acceptable for interactive approvals or
restarts when an agent is already waiting at its own shell prompt.

---

## Quick Decision Tree

```
Need to assign work?
│
├─ Writing/testing CODE? (isolated, reproducible)
│   └─ Use Task tool (subagent/worktree). Always. 100% reliable.
│   ⚠️  NEVER use `forge dispatch send` for code changes.
│       Fleet agents writing code on main causes index.lock contention
│       and dirty working tree. Council S164: hard-prohibit code via dispatch.
│
├─ Routine fleet work (research, docs, analysis, coverage)?
│   └─ PRIMARY: forge task create --title "..." → queue it
│       Agents running in daemon mode self-assign automatically.
│       No dispatch needed. Orchestrator just adds to queue.
│
├─ Need a SPECIFIC named agent for non-code work?
│   └─ SECONDARY: forge dispatch send AGENT "message" (manual override)
│       ├─ Simple message   → forge dispatch send AGENT "message"
│       └─ Complex task     → Write dispatch file first, then notify
│           1. Create: .forge/dispatches/AGENT-TASKID-DATE.md
│           2. Notify: forge dispatch send AGENT "Read .forge/dispatches/FILE.md -- EXECUTE now"
│       ⚠️  PREFLIGHT: If your task references a specific file path, verify
│           it exists with Glob/ls BEFORE dispatching. Bad paths cause agents
│           to spin in tool-call loops (Council S164, kilo incident).
│
└─ Cross-node work?
    └─ forge task create --title "..." → agents on any node claim from shared queue
```

**Rule of thumb:** If you're writing a dispatch message, ask yourself — could I just add this to
the task queue and let an agent claim it? If yes, prefer the queue. Reserve `forge dispatch send`
for cases where agent identity matters (e.g., "run this on the sati kimi window specifically").

**Hard rule (Council S164):** `forge dispatch send` is for research, analysis, audits, docs, and
content ONLY. All code changes go through worktree-isolated Task tool agents. No exceptions.

---

## Dispatch Methods (with reliability data)

### 0. Daemon Polling Mode — ✅ PRIMARY (~99% reliable, S130+)
**Use for:** All routine fleet work. Agents self-assign from the shared queue.

```bash
# On orchestrator: add work to queue
forge task create --title "Update ADR index with S130 progress" --priority 5

# Agents (already running in daemon mode) auto-claim and execute.
# No dispatch needed.

# To start an agent in daemon mode manually (agent-start.sh does this automatically):
forge work --daemon --interval 15s
```

**How it works:** `agent-start.sh` automatically sends `forge work --daemon --interval 15s` to each
agent after it boots. Agents poll the queue every 15 seconds and claim available tasks. The
orchestrator only needs to add tasks to the queue.

**When to use:** Any time agent identity doesn't matter. Orchestrator delegates to "any available
agent" — the queue assigns naturally based on availability.

### 1. Task Tool (Subagents) — ✅ 100% reliable
**Use for:** All code implementation, debugging, test writing, multi-step tasks.

```python
# Worktree isolation (preferred for code changes)
Task(subagent_type="backend-engineer", isolation="worktree", prompt="...")

# Background (when you don't need results immediately)
Task(subagent_type="Explore", run_in_background=True, prompt="...")
```

Available subagent types: `general-purpose`, `Explore`, `Plan`, `backend-engineer`,
`frontend-builder`, `qa-test-guardian`, `architect-advisor`, `code-reviewer`,
`debug-detective`, `security-auditor`, `performance-optimizer`, `refactor-surgeon`

### 2. CLI Dispatch — ✅ ~95% reliable (SECONDARY — manual override only)
**Use for:** Directing a specific task to a specific named agent when agent identity matters.

Target format is the **window name**, not `forge:<agent>`.

```bash
forge dispatch send glm "Fix the failing test in coverage_wave6_test.go"
forge dispatch send minimax "Update ADR index with S80 progress"
forge dispatch send kimi --file .forge/dispatches/TASK-123-2026-03-07.md
```

### 3. Dispatch File Pattern — ✅ Required for complex tasks
**Use for:** Tasks requiring context, multiple steps, or file references.

```bash
# Step 1: Write the dispatch file
# Step 2: Notify agent
forge dispatch send kimi "Read .forge/dispatches/kimi-TASK-001-2026-03-07.md -- EXECUTE now"
```

### 4. ❌ FORBIDDEN — Raw tmux for task delivery
```bash
# NEVER DO THIS for task delivery — ~25% failure rate from git locks / input race
tmux send-keys -t forge:glm "..." Enter
```

**Exception:** tmux `send-keys` IS acceptable for interactive approvals or restarts when an agent
is already at its shell prompt and waiting. Use `-l` flag and send Enter as a separate call:
```bash
tmux send-keys -t forge:agent C-u          # clear partial input first
tmux send-keys -t forge:agent -l "y"       # -l prevents escape sequence misinterpretation
tmux send-keys -t forge:agent "" Enter     # Enter MUST be a separate call
```

---

## Results Contract (Heartbeat)

Every task MUST produce a result file:
- **Path**: `.forge/heartbeat/results/AGENTNAME-TASKID.md`
- **Content**: Summary of work done, tests passed/failed, files changed.
- **Timeout**: Tasks without results after 2h are auto-marked TIMEOUT by patrol.

---

## Fleet Agent Roster

### sati node (64GB RAM — all fleet agents)
| Agent | Window | Best For | Avoid |
|-------|--------|----------|-------|
| **claude** | `claude` | Features, refactors, hard bugs, production code | — |
| **gemini** | `gemini` | Research, audits, architecture, planning | Long-running code tasks |
| **minimax** | `minimax` | Implementation, docs, runbooks, multi-file features | go-test |
| **glm** | `glm` | Implementation, scaffolding, refactors | go-test, ios-builds |
| **opencode** | `opencode` | Implementation, multi-file refactors | — |
| **kilo** | `kilo` | Implementation, multi-file features, docs | — |
| **kimi** | `kimi` | Coverage, triage, rapid fix loops | Large refactors |
| **kimi-2** | `kimi-2` | Parallel coverage/triage | Large refactors |
| **pi** | `pi` | Fast triage, quick edits, analysis | Heavy code tasks |
| **cursor** | `cursor` | Human-steered interactive editing | Autonomous tasks |
| **cursor-2** | `cursor-2` | Parallel human-steered editing | Autonomous tasks |

> All fleet agents run on **sati** (64GB). prya is orchestrator-only (daemon + lead).
> **Hotswap rule:** if primary agent is offline, pick next in the same row (e.g. `kimi` → `kimi-2`).

---

## Agent Start Commands

If an agent window is at a shell prompt, use these exact commands:

| Agent Window | Start Command |
|-------------|---------------|
| kimi / kimi-2 | `kimi -y` |
| minimax | `minimax` |
| gemini | `gemini -y` |
| pi | `pi` |
| opencode | `opencode` |
| kilo | `kilo` |
| cursor-agent | `cursor-agent -f` |
| amp | `amp --dangerously-allow-all` |
| claude | `claude --dangerously-skip-permissions` |
| codex | `codex --dangerously-bypass-approvals-and-sandbox` |

---

## Agent Rules (Non-Negotiable)

1. **DO NOT COMMIT** — Fleet agents never commit. Orchestrator commits all work.
2. **DO NOT PUSH** — Fleet agents never push.
3. **DO NOT DISPATCH** — Fleet agents do not dispatch to other agents.
4. **WRITE RESULTS** — Always write to `.forge/heartbeat/results/AGENTNAME-TASKID.md`.
5. **EXECUTE** — If told to execute, do the work. Do not write a research plan.
6. **CLEAR** — Run `/clear` periodically to keep context usage efficient.

---

## Dark Factory Boot Sequence (Council S164)

Run at the start of each orchestrator session to ensure fleet is operational:

```bash
# 1. Clean blockers (stale locks, rebase state)
forge recover

# 2. Fleet health check — verify agents online, queue depth
forge status

# 3. Check queue for stale tasks before dispatching new work
forge task list   # review and manually complete/cancel stale tasks

# 4. Verify agents are in daemon mode (check tmux panes)
forge fleet windows   # if agents are idle at prompt, restart via agent-start.sh

# 5. Dispatch or queue new work
forge dispatch send <agent> "..."   # for non-code work
# OR use Task tool with isolation="worktree" for code changes

# 6. After results come in, check quality
forge dispatch check-results
```

**Keep it short.** This is a 2-minute startup checklist, not a 12-step ritual.

---

## Common Failure Modes + Recovery

| Symptom | Cause | Recovery |
|---------|-------|----------|
| Agent doesn't respond to tmux dispatch | Input race / git lock | Use `forge dispatch send` instead |
| Task stuck in ASSIGNED after 2h | Agent died or missed message | `forge queue prune` then re-dispatch |
| `forge dispatch send` fails with "agent not found" | Agent window name mismatch | Check `forge fleet windows` for exact window name |
| Agent claims task but writes no results | Context overflow or confusion | Check `.forge/heartbeat/nodes/` for last heartbeat; re-dispatch with clearer scope |
| Git lock blocks agent | Concurrent git ops | `forge patrol task-timeout` or manual `rm .git/index.lock` |
| Double-claim on same task | Race between two agents | Lease system prevents this — if it happens, `forge task show <id>` to check state |

---

## Messaging Systems (S120 Phase 3.1 — which one does what)

FORGE has four messaging systems. They are NOT interchangeable.

| Command | System | Use When | Reliability |
|---------|--------|----------|-------------|
| `forge dispatch send AGENT "msg"` | HTTP → daemon → tmux | Assigning tasks to fleet agents | ~95% |
| `forge lead send NODE "msg"` | XNode mesh (file-based) | Orchestrator-to-orchestrator cross-node | ~80% (eventual) |
| `forge message send NODE "msg"` | Git-based message bus | **Deprecated** — git transport is unreliable for messaging | ~60% |
| `forge relay start` | File-polling relay worker | Internal daemon relay only — not for task dispatch | N/A (daemon internal) |

**Rules:**
- Use `forge dispatch send` for all agent task assignment. It is the only production-tested dispatch path.
- Use `forge lead send` only for orchestrator↔orchestrator coordination across nodes (e.g., prya → sati for handoff).
- Never use `forge message send` for task delivery — git transport is unreliable and slow.
- Never use `forge relay start` directly — it is started by `forged` internally.

---

*Last updated: 2026-03-27 S172. Clarified `forge dispatch send` target format uses tmux window name (not `forge:` prefix), added stale-task review note via `forge task list`, and preserved daemon polling as PRIMARY dispatch method.*
