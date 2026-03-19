# FORGE Worktree Lifecycle Guide

## Overview

In the FORGE ecosystem, **Git Worktrees** are the primary mechanism for parallel agent execution and task isolation. By creating a separate working directory for each task, we prevent agents from interfering with each other's code changes, test runs, and dependency states.

### Core Architecture

1.  **Isolation**: When a Task tool or the `prime` command starts a task with `isolation: "worktree"`, the system creates a new git worktree.
2.  **Pathing**: Worktrees are located at `.claude/worktrees/agent-{ID}/`.
3.  **Branching**: Each worktree is associated with a task-specific branch (e.g., `agent-{ID}-task`).

---

## The Worktree Lifecycle

| Phase | Action | Responsibility |
| :--- | :--- | :--- |
| **1. Create** | `git worktree add .claude/worktrees/agent-{ID} {branch}` | Orchestrator / CLI |
| **2. Active** | Agent performs work strictly within the worktree path. | Agent |
| **3. Complete** | Agent commits changes and reports completion. | Agent |
| **4. Merge** | Lead agent reviews and merges the branch into `main`. | Lead Orchestrator |
| **5. Prune** | `git worktree remove` and `git branch -d`. | Cleanup Script / Lead |

---

## Critical Issues & Known Bugs

### 1. The "Path Leak" Bug (Frontend-Builder Pattern)
**Problem**: Some agents (especially those working on `harness/` or `scripts/` paths) incorrectly resolve absolute paths to the main repository root instead of the worktree root. This causes them to write changes to the `main` tree, bypassing isolation.
**Mitigation**:
- **Agents**: Always verify your current working directory (`pwd`) and ensure all write operations are relative to the worktree root.
- **Tools**: Use `FORGE_ROOT` environment variable which is updated to the worktree path by the task executor.

### 2. Context Exhaustion & "Prunable" State (Backend-Engineer Pattern)
**Problem**: If an agent's context exhausts before it can commit its work, the worktree enters a "dirty" state. If the agent process is terminated, git may report the worktree as "prunable" because its administrative metadata exists but the process is gone.
**Mitigation**:
- **Heartbeat Loop**: The heartbeat loop *should* alert on dirty files within `.claude/worktrees/`. (Current Gap: This requires manual check via `git worktree list`).
- **Atomic Commits**: Agents must commit frequently (at least after every successful test run) to ensure no more than 5-10 minutes of work is ever at risk.

---

## Recovery Procedures

If a worktree becomes stale or an agent fails to complete its task:

### 1. Identify Stale Worktrees
```bash
git worktree list
# Look for paths in .claude/worktrees/ that are not currently being worked on
```

### 2. Inspect and Recover Changes
If the worktree is still on disk:
```bash
cd .claude/worktrees/agent-{ID}
git status
# If changes exist:
git add .
git commit -m "chore: recover uncommitted work from stale agent"
git checkout main
git merge agent-{ID}-task
```

### 3. Handle "Prunable" Worktrees
If the directory was deleted but the entry remains in `git worktree list`:
```bash
git worktree prune
```

### 4. Forced Cleanup
If a worktree is stuck:
```bash
git worktree remove --force .claude/worktrees/agent-{ID}
git branch -D agent-{ID}-task
```

---

## Infrastructure Safeguards

### Heartbeat Monitoring
The **Heartbeat Loop** (`.forge/scripts/heartbeat-loop.sh`) is the system's watchman.
- **Alerting**: Future iterations will include a `WORKTREE_DIRTY` trigger that fires if uncommitted changes persist in any agent worktree for >10 minutes.
- **Nudges**: The Lead Orchestrator receives a nudge to "Rescue" or "Reap" stale worktrees during context rotation.

### Startup Cleanup
The `cleanup()` function in `.forge/scripts/forge-startup.sh` (lines ~274-296) handles basic housekeeping:
- Identifies orphaned `.claude/worktrees/agent-*` directories.
- Removes stale `.git/index.lock` files that block worktree operations.

---

## Best Practices for Agents

1.  **Verify Root**: Always check `git rev-parse --show-toplevel` to ensure you are in the correct worktree.
2.  **Commit Before Reporting**: Never report a task as "Complete" without running `git commit`. A "Complete" task with uncommitted changes is a failure.
3.  **Self-Cleanup**: If you have the permissions, remove your own worktree after a successful merge.
