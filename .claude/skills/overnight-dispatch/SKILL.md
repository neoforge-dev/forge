---
name: overnight-dispatch
description: Automate fleet dispatch, result collection, and commit for overnight autonomous work — dispatch tasks to minimax/glm/kimi/Task-tool, monitor results, stage and commit fleet output.
auto_execute: false
allowed-tools: [Read, Write, Bash, Glob, Grep]
---

# Overnight Dispatch Skill

Automates the complete overnight fleet work loop: load pending tasks, match agents by capability, generate dispatch files, send, monitor for completion, collect results, and commit.

## Why This Skill Exists

Manual overnight dispatch involves repetitive, error-prone steps:
- Writing dispatch files by hand with inconsistent structure
- Forgetting to set results file paths agents must write to
- Missing the "DO NOT COMMIT" rule in dispatch files, causing accidental commits
- No structured way to collect and triage DONE vs BLOCKED results
- Commit messages that don't link work back to specific agents and wave IDs

This skill enforces the proven pattern documented in memory and `docs/PROMPT.md`.

## Usage

```bash
/overnight-dispatch                          # Interactive: review tasks, assign agents, send wave
/overnight-dispatch --task-ids T-001,T-002  # Dispatch specific task IDs
/overnight-dispatch --collect               # Collect results from last wave
/overnight-dispatch --commit                # Stage + commit all fleet results
/overnight-dispatch --status                # Show wave status (sent / done / blocked)
/overnight-dispatch --dry-run               # Show dispatch plan without sending
```

---

## Subcommand: (default — send wave)

Plan and dispatch a wave of tasks to fleet agents.

### Step 1: Load Pending Tasks

```bash
# List pending tasks from the task queue
cd ./harness && uv run forge tasks list --status pending

# Or read specific task IDs passed as args
# /overnight-dispatch --task-ids T-001,T-002,T-003
```

Tasks come from `.forge_tasks/` JSON files. Each task has:
- `id` — e.g. `T-042`
- `title` — short description
- `description` — full objective
- `priority` — high / medium / low
- `domain` / `project` — affected files
- `acceptance_criteria` — measurable done conditions

### Step 2: Match Tasks to Agents

Use this capability matrix to assign each task:

| Agent | Model | Specialty | Avoid |
|-------|-------|-----------|-------|
| `minimax` | MiniMax M2.5 | Docs, runbooks, CLAUDE.md updates, README edits | Code changes, multi-file logic |
| `glm` | GLM-4.7 | Fast single-file edits, small fixes, config tweaks | Large analysis, multi-file changes |
| `kimi` | Kimi K2.5 | Complex analysis, audits, multi-file investigation, design review | Gets stuck in "thinking" — check every 15 min |
| `Task tool` | Claude Sonnet | All source code changes — use `worktree` isolation | Anything minimax/glm/kimi can handle |

**Assignment rules:**
- If task touches `.py`, `.ts`, `.tsx`, `.sh` source files: use Task tool
- If task is docs-only (`.md`, `CLAUDE.md`, runbooks): use minimax
- If task is a single-file config/fix: use glm
- If task requires reading many files to analyze/report: use kimi
- Never assign the same agent more than 2 tasks per wave (context limits)
- Never spawn OpenCode or Kilo on node-1 (OOM risk — 93% RAM)

### Step 3: Generate Dispatch Files

For each agent assignment, write a dispatch file to `.forge/dispatches/`:

**File naming convention:**
```
.forge/dispatches/{agent}-wave{N}-{YYYY-MM-DD}.md
```

Where `N` is the wave number for the day (01, 02, ...).

**Dispatch file template:**

```markdown
# DISPATCH: {agent} — {task_title}

**Agent:** {agent} ({model}) — Best for: {specialty}
**Priority:** {priority}
**Wave:** wave{N}-{YYYY-MM-DD}
**Results file:** `.forge/heartbeat/results/{agent}-wave{N}-{task_id}.md`
**Depends on:** {dependencies or "none"}

## Objective

{description — copy from task, expand with context if needed}

## Tasks

1. {numbered task with specific file path}
2. {numbered task with specific file path}

## Acceptance Criteria

- {measurable criterion — e.g. "File X exists and passes linting"}
- {measurable criterion — e.g. "No new mypy errors introduced"}

## Rules

- **DO NOT COMMIT** — lead orchestrator commits all work
- **DO NOT PUSH**
- Write results to: `.forge/heartbeat/results/{agent}-wave{N}-{task_id}.md`
- Results file must include: status (DONE/BLOCKED), files modified, brief summary
```

**Results file format agents must follow:**

```markdown
# Results: {agent} — {task_title}

**Status:** DONE | BLOCKED
**Wave:** wave{N}-{YYYY-MM-DD}
**Task ID:** {task_id}
**Completed at:** {timestamp}

## Files Modified

- `path/to/file.md` — description of change
- `path/to/other.py` — description of change

## Summary

{2-3 sentences: what was done, any caveats}

## Blockers (if BLOCKED)

{reason blocked, what is needed to unblock}
```

### Step 4: Send Dispatches

For each fleet agent (minimax, glm, kimi):

```bash
# Primary method — forge dispatch send
forge dispatch send forge:{agent} "Read .forge/dispatches/{agent}-wave{N}-{date}.md — EXECUTE now"

# Verify the message was received (check pane output)
tmux capture-pane -t forge:{agent} -p | tail -10
```

For Task tool assignments (code changes):

```
Use the Task tool with:
  subagent_type: "backend-engineer"
  isolation: "worktree"
  run_in_background: true
  prompt: "{full task description with file paths and acceptance criteria}"
```

**Agent-specific notes:**
- **minimax / glm (Claude Code)**: Accept plain text — `forge dispatch send` handles readiness automatically
- **kimi**: Use `/clear` first if context >15%; sends via `forge dispatch send`
- **glm**: Context depletes fast; limit to 1-2 tasks before next `/clear`

### Step 5: Log the Wave

After sending, write a wave manifest to `.forge/dispatches/wave-manifest-{N}-{date}.json`:

```json
{
  "wave": "wave01-2026-02-23",
  "sent_at": "2026-02-23T22:00:00Z",
  "tasks": [
    {
      "task_id": "T-042",
      "agent": "minimax",
      "dispatch_file": ".forge/dispatches/minimax-wave01-2026-02-23.md",
      "results_file": ".forge/heartbeat/results/minimax-wave01-T-042.md",
      "status": "sent"
    }
  ]
}
```

### Example Output

```
Overnight Dispatch — Wave 01 — 2026-02-23
══════════════════════════════════════════════════════════════

Loading pending tasks...
Found 6 pending tasks.

Assignment Plan:
  T-042  minimax   [docs]   Update allergen-coach CLAUDE.md
  T-043  glm       [fix]    Fix mypy error in subscriptions.py (1 line)
  T-044  kimi      [audit]  Audit IS domain projects for test coverage
  T-045  Task      [code]   Implement JWT refresh endpoint in voice-coach
  T-046  minimax   [docs]   Write REVENUE_UNBLOCK_CHECKLIST runbook section
  T-047  glm       [fix]    Remove stale TODO marker in pkm-ai/models.py

Generating dispatch files...
  Writing .forge/dispatches/minimax-wave01-2026-02-23.md  (2 tasks)
  Writing .forge/dispatches/glm-wave01-2026-02-23.md      (2 tasks)
  Writing .forge/dispatches/kimi-wave01-2026-02-23.md     (1 task)

Sending...
  forge:minimax  sent   (forge dispatch send)
  forge:glm      sent   (forge dispatch send)
  forge:kimi     sent   (forge dispatch send)
  Task T-045     queued (Task tool, worktree isolation, background)

Wave manifest saved: .forge/dispatches/wave-manifest-01-2026-02-23.json

══════════════════════════════════════════════════════════════
Wave 01 dispatched — 6 tasks to 4 agents
Check results with: /overnight-dispatch --collect
══════════════════════════════════════════════════════════════
```

---

## Subcommand: --collect

Parse result files from the last wave and triage DONE vs BLOCKED.

### Step 1: Load Wave Manifest

```bash
# Find the most recent wave manifest
ls -t ./.forge/dispatches/wave-manifest-*.json | head -1
```

### Step 2: Check Result Files

For each task in the manifest, check whether the results file exists:

```bash
# Results land in .forge/heartbeat/results/
ls ./.forge/heartbeat/results/ | grep "wave{N}"
```

### Step 3: Parse Status

Read each results file and extract:
- `Status: DONE` or `Status: BLOCKED`
- List of files modified
- Summary text
- Blocker reason (if BLOCKED)

### Step 4: Report

```
Overnight Dispatch — Collect — Wave 01 — 2026-02-23
══════════════════════════════════════════════════════════════

DONE (4/6):
  T-042  minimax  Update allergen-coach CLAUDE.md
         Files: allergen-coach/CLAUDE.md
  T-043  glm      Fix mypy error in subscriptions.py
         Files: is-growth/subscriptions.py
  T-046  minimax  Write REVENUE_UNBLOCK_CHECKLIST section
         Files: docs/runbooks/REVENUE_UNBLOCK_CHECKLIST.md
  T-047  glm      Remove stale TODO in pkm-ai/models.py
         Files: pkm-ai/app/models.py

BLOCKED (1/6):
  T-044  kimi     Audit IS domain projects for test coverage
         Reason: IS domain has 52 mypy errors — can't assess coverage until fixed

PENDING / NO RESULT (1/6):
  T-045  Task     Implement JWT refresh endpoint
         Status:  Still running (worktree Task tool — check background job)

══════════════════════════════════════════════════════════════
Ready to commit: 4 tasks
Blockers to address: 1 task (T-044)
Still running: 1 task (T-045)

Run /overnight-dispatch --commit to stage and commit the DONE results.
══════════════════════════════════════════════════════════════
```

---

## Subcommand: --commit

Stage all fleet result files and modified source files, then commit with a structured message.

### Step 1: Identify Files to Stage

From the DONE results, collect:
- All files listed in "Files Modified" sections
- The dispatch files (`.forge/dispatches/*.md`)
- The result files (`.forge/heartbeat/results/*.md`)
- The wave manifest (`.forge/dispatches/wave-manifest-*.json`)

### Step 2: Stage

```bash
cd .

# Stage source files modified by fleet agents
git add {each modified file from results}

# Stage dispatch infrastructure files
git add .forge/dispatches/
git add .forge/heartbeat/results/
```

### Step 3: Commit

Use the `commit-with-retry.sh` script to avoid git lock conflicts:

```bash
./.forge/scripts/commit-with-retry.sh \
  "chore(fleet): overnight wave01 — 4 tasks complete, 1 blocked

Tasks completed:
- T-042 (minimax): Update allergen-coach CLAUDE.md
- T-043 (glm): Fix mypy error in subscriptions.py
- T-046 (minimax): Write REVENUE_UNBLOCK_CHECKLIST section
- T-047 (glm): Remove stale TODO in pkm-ai/models.py

Blocked:
- T-044 (kimi): IS audit blocked on 52 mypy errors

Wave: wave01-2026-02-23"
```

**Commit message format:**
```
chore(fleet): overnight wave{N} — {X} tasks complete[, {Y} blocked]

Tasks completed:
- {task_id} ({agent}): {title}

[Blocked:
- {task_id} ({agent}): {title} — {reason}]

Wave: wave{N}-{YYYY-MM-DD}
```

### Step 4: Verify

```bash
git log --oneline -3
git status
```

### Example Output

```
Overnight Dispatch — Commit — Wave 01
══════════════════════════════════════════════════════════════

Staging files...
  git add allergen-coach/CLAUDE.md                        ✓
  git add is-growth/subscriptions.py                      ✓
  git add docs/runbooks/REVENUE_UNBLOCK_CHECKLIST.md      ✓
  git add pkm-ai/app/models.py                            ✓
  git add .forge/dispatches/                              ✓
  git add .forge/heartbeat/results/                       ✓

Committing...
  ✓ Committed: a3f9d12

git log:
  a3f9d12 chore(fleet): overnight wave01 — 4 tasks complete, 1 blocked

══════════════════════════════════════════════════════════════
Wave 01 committed.
Blocked tasks remain in queue for follow-up.
══════════════════════════════════════════════════════════════
```

---

## Subcommand: --status

Show live status of all waves for the current date.

```bash
/overnight-dispatch --status
```

Reads wave manifests and result files, shows:
- Wave ID, sent time
- Per-task: agent, status (sent / done / blocked / pending-result)
- Summary counts

---

## Complete Overnight Workflow

The standard end-to-end pattern used since Session 16:

```
22:00  /overnight-dispatch                   # Plan and send wave
       (agents work autonomously overnight)
08:00  /overnight-dispatch --collect         # Triage results
       (review blocked items, note follow-ups)
08:10  /overnight-dispatch --commit          # Commit DONE results
08:15  forge tasks list --status blocked     # Address blockers
```

For multi-wave nights (heavy sprint work):

```
22:00  /overnight-dispatch --task-ids T-040,T-041,T-042   # Wave 01
00:00  /overnight-dispatch --task-ids T-043,T-044          # Wave 02 (after wave 01 clears)
```

---

## Dispatch File Quick Reference

Key fields every dispatch file must include:

| Field | Purpose | If missing |
|-------|---------|------------|
| `Results file:` | Path agent writes output to | Collect step fails silently |
| `DO NOT COMMIT` | Prevents agent from committing | Lead loses track of what changed |
| `DO NOT PUSH` | Prevents premature push | Branch diverges unexpectedly |
| `Acceptance Criteria` | Measurable done signal | Agent declares DONE prematurely |
| `Tasks` (numbered) | Specific files + actions | Agent guesses scope |

---

## Agent Capability Quick Reference

```
minimax  →  docs, CLAUDE.md, runbooks, README        (~30 sec turnaround)
glm      →  single-file fixes, config, small edits   (<1 min turnaround)
kimi     →  multi-file analysis, audits, design       (may "think" 5-15 min)
Task     →  ALL source code changes (Python, TS, etc) (use worktree isolation)
```

**Never use fleet agents for source code.** Use the Task tool (worktree isolation) for anything that modifies `.py`, `.ts`, `.tsx`, `.go`, `.sh` files outside of docs.

---

## Error Handling

| Scenario | Recovery |
|----------|----------|
| Agent shows no results after 8 hours | Check `tmux capture-pane -t forge:{agent} -p` — may be stuck; re-send dispatch |
| Results file missing but agent claims done | Check git status for untracked files; agent may have saved elsewhere |
| BLOCKED with "context limit" | Break task into smaller sub-tasks; re-dispatch to fresh agent |
| Git index lock on commit | `rm -f ./.git/index.lock` then retry |
| glm context depleted mid-task | Send `/clear` to forge:glm, re-read dispatch file from scratch |
| kimi stuck thinking | Wait 5 min; if no output, send `Ctrl-C` and re-dispatch |
| Task tool worktree conflict | Worktree agents write to main tree for `harness/` files — normal behavior |

---

## File Locations

```
.forge/dispatches/
├── {agent}-wave{N}-{YYYY-MM-DD}.md       # Dispatch instructions (per agent)
└── wave-manifest-{N}-{YYYY-MM-DD}.json  # Wave tracking manifest

.forge/heartbeat/results/
└── {agent}-wave{N}-{task_id}.md          # Agent result files (agent writes here)
```

---

## Related Skills

- `/dispatch` — Lower-level single-agent dispatch with quota detection
- `/fleet-ops status` — Live fleet health dashboard
- `/fleet-watch` — Continuous monitoring with alerts
- `/complete-task` — Lightweight commit + heartbeat for individual task completion
- `/handoff-clean` — Session handoff before overnight run

## See Also

- `CLAUDE.md` — Agent capability matrix and node resource budgets
- `.forge/memories/INDEX.md` — Learned patterns from past overnight runs
- `forge-shared/modules/fleet-dispatch.md` — Fleet dispatch architecture
- `docs/PROMPT.md` — Current sprint state and overnight results log
