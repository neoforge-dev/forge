# Dispatch Decision — Single Source of Truth

> **Every dispatch decision in FORGE starts here.** CLAUDE.md, AGENTS.md, and AGENT_QUICK_START.md all point here. Do not duplicate this content elsewhere. Previously `fleet-dispatch.md`.

## Reliability Summary (read this first)

| Method | Reliability | Use When |
|--------|-------------|----------|
| Task tool (subagent) | **100%** | All code implementation, debug, tests |
| `forge dispatch send` | **~95%** | Assign work to a named fleet agent window |
| Raw `tmux send-keys` | **~25%** | **NEVER for task delivery** — git locks + input race |

Raw tmux fails ~75% of the time for task delivery due to git lock collisions and input buffer
races. Use `forge dispatch send` instead. tmux is only acceptable for interactive approvals or
restarts when an agent is already waiting at its own shell prompt.

---

## Quick Decision Tree

```
Need to assign work?
│
├─ Writing/testing CODE?
│   └─ Use Task tool (subagent). Always. 100% reliable.
│
├─ Assigning work to a FLEET AGENT (tmux window)?
│   ├─ Simple message   → forge dispatch send AGENT "message"
│   └─ Complex task     → Write dispatch file first, then notify
│       1. Create: .forge/dispatches/AGENT-TASKID-DATE.md
│       2. Notify: forge dispatch send AGENT "Read .forge/dispatches/FILE.md -- EXECUTE now"
│
└─ Cross-node work?
    └─ forge task create --title "..." → agents claim from their own nodes
```

---

## Dispatch Methods (with reliability data)

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

### 2. CLI Dispatch — ✅ ~95% reliable
**Use for:** Assigning work to a named fleet agent window.

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

### node-2 node (64GB RAM — all fleet agents)
| Agent | Window | Best For | Avoid |
|-------|--------|----------|-------|
| **claude** | forge:claude | Features, refactors, hard bugs, production code | — |
| **gemini** | forge:gemini | Research, audits, architecture, planning | Long-running code tasks |
| **minimax** | forge:minimax | Implementation, docs, runbooks, multi-file features | go-test |
| **glm** | forge:glm | Implementation, scaffolding, refactors | go-test, ios-builds |
| **opencode** | forge:opencode | Implementation, multi-file refactors | — |
| **kilo** | forge:kilo | Implementation, multi-file features, docs | — |
| **kimi** | forge:kimi | Coverage, triage, rapid fix loops | Large refactors |
| **kimi-2** | forge:kimi-2 | Parallel coverage/triage | Large refactors |
| **pi** | forge:pi | Fast triage, quick edits, analysis | Heavy code tasks |
| **cursor** | forge:cursor | Human-steered interactive editing | Autonomous tasks |
| **cursor-2** | forge:cursor-2 | Parallel human-steered editing | Autonomous tasks |

> All fleet agents run on **node-2** (64GB). node-1 is orchestrator-only (daemon + lead).
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

*Last updated: 2026-03-19. Added reliability summary, failure modes table, agent start commands, tmux interactive exception, and Avoid column to fleet roster.*
