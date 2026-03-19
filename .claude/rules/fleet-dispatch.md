---
description: Fleet agent dispatch rules and communication patterns
globs:
  - ".forge/dispatches/**"
  - ".forge/heartbeat/**"
---

# Fleet Dispatch Rules

## DO NOT COMMIT Rules (Fleet Agents)

Fleet agents must NEVER:
- `git commit`
- `git push`
- Create handoff documents
- Dispatch to other agents (`forge dispatch send`)
- Run `forge approval decide`
- Run `forge lane promote`

**Only the orchestrator commits work.**

## Dispatch Decision Tree

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

## Dispatch Methods (with reliability)

| Method | Reliability | Use For |
|--------|-------------|---------|
| **Task tool** | 100% | All code changes, multi-step tasks |
| **CLI Dispatch** | ~95% | Fleet agent messages |
| **Dispatch File** | Required | Complex tasks with context |
| **Raw tmux** | ~25% ❌ | NEVER use for dispatch |

## Dispatch File Pattern

**Write to:** `.forge/dispatches/AGENT-TASKID-DATE.md`

**Required sections:**
- Objective (one sentence)
- Tasks (numbered, specific)
- Acceptance criteria (measurable)
- Results file path
- DO NOT COMMIT reminder

**Results file:** `.forge/heartbeat/results/AGENTNAME-TASKID.md`

## tmux send-keys Rules (Interactive ONLY)

For approvals or restarts only — never for task dispatch:

```bash
# Rule 1: Enter MUST be a separate call
tmux send-keys -t forge:agent "y"
tmux send-keys -t forge:agent "" Enter     # ← separate call

# Rule 2: For text >1 char, use -l (literal) + C-u clear first
tmux send-keys -t forge:agent C-u          # clear partial input
tmux send-keys -t forge:agent -l "claude"  # -l prevents escape sequences
tmux send-keys -t forge:agent "" Enter     # ← separate call
```

**Why**: Claude CLI has its own input buffer — appending `Enter` to a message races with buffer processing and fails ~75% of the time.

## Agent Start Commands

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

## Fleet Agent Capability Matrix

| Agent | Best For | Avoid |
|-------|----------|-------|
| `claude` | Primary implementation: features, refactors, hard bugs | — |
| `gemini*` | Research, audits, architecture, planning | Long-running code |
| `minimax*` | Implementation, docs, runbooks, multi-file features | go-test |
| `glm*` | Implementation, scaffolding, refactors | go-test, ios-builds |
| `opencode*` | Implementation, multi-file refactors | — |
| `kilo*` | Implementation, multi-file features, docs | — |
| `kimi*` | Coverage, triage, rapid fix loops | Large refactors |
| `pi*` | Fast triage, quick edits, analysis | Heavy code tasks |
| `cursor*` | Human-steered interactive editing | Autonomous tasks |

**Hotswap rule:** if primary agent offline, pick next in same row (e.g., `kimi` → `kimi-2`).

## Node Resource Budgets

| Node | RAM | Max Agents | Allowed Models |
|------|-----|------------|----------------|
| **node-1** | 16 GB | 2 max | Claude Code (minimax/glm), Kimi — NO OpenCode/Kilo |
| **node-2** | 64 GB | 5-6 | OpenCode, Kilo, Kimi, GLM — heavy workloads |
| **node-3** | 48 GB | 3-4 | Worktree agents, iOS builds |
| **node-4** | 16 GB | 1-2 | Auxiliary only (no iOS) |
| **node-5** | 16 GB | 2-3 | M1 Pro laptop, off-hours only |

**CRITICAL:** NEVER spawn OpenCode or Kilo on node-1 — causes OOM at 93% RAM.

## Results Contract

Every task MUST produce a result file:
- **Path:** `.forge/heartbeat/results/AGENTNAME-TASKID.md`
- **Content:** Summary of work done, tests passed/failed, files changed
- **Timeout:** Tasks without results after 2h are auto-marked TIMEOUT by patrol
