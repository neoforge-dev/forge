# ADR-024: Orchestration-Level Worktree Isolation

**Date:** 2026-03-05
**Status:** Proposed
**Decision Makers:** Bogdan Veliscu (CTO, FORGE)

---

## Context

The FORGE fleet consists of multiple agents (Claude Code, OpenCode) operating locally on the same node (e.g., Sati or Nova). Currently, when multiple agents are dispatched tasks, they operate in the same project directory (the `master` checkout).

`AGENTS.md` explicitly calls out `git index.lock` contention as a severe, recurring pain point. A 75% failure rate on `tmux send-keys` was measured during sprints 8-9 due to agents colliding in the same git working directory.

This architecture suffered from:
1. **Concurrency Crashes:** Multiple agents running `make test`, `ruff format`, or `git commit` simultaneously caused fatal filesystem lock errors.
2. **Artifact Pollution:** Test coverage HTML or compiled caches from one agent would pollute the workspace of another.

### Alternatives Considered

| Alternative | Pros | Cons | Verdict |
|-------------|------|------|---------|
| Retries on index.lock | Simple | Agents hang, tests still collide | ❌ REJECTED |
| Serial Execution | Safe | Kills fleet parallel throughput | ❌ REJECTED |
| **Git Worktrees per Lease** | **100% Isolation, Zero Lock Contention** | **Disk space, setup latency** | ✅ **ACCEPTED** |

---

## Decision

The `forge-v3` Go daemon will strictly enforce **Orchestration-Level Worktree Isolation** for every task lease. Multiple agents will NEVER execute in the same physical directory.

### Core Architecture

1. **Lease Initialization:** When an agent claims a task (e.g., `TASK-102`), the Control Plane executes `git worktree add .forge/worktrees/TASK-102 -b feature/TASK-102`.
2. **Execution Context:** The Subprocess Supervisor (ADR-016) spawns the agent CLI with its working directory set strictly to `.forge/worktrees/TASK-102/`.
3. **Complete Isolation:** The agent can safely run tests, format code, and make commits. The local `.git` file points back to the main repository, but the working tree is entirely isolated.
4. **Task Completion:** When the task is marked "completed" or "approved", the Control Plane pushes the branch, performs the merge via API, and executes `git worktree remove .forge/worktrees/TASK-102`.
5. **Race Mode:** If two agents are "racing" the same task (ADR-013), they get their own worktrees (e.g., `TASK-102-agentA` and `TASK-102-agentB`).

---

## Consequences

### Positive

1. **Solves the #1 Stability Issue:** Permanently eliminates the `git index.lock` crashes that plagued v2.
2. **Safe Parallelism:** Sati can theoretically run 16 agents simultaneously without any filesystem collisions.
3. **Clean Teardown:** If an agent hallucinates and destroys its repository, the Control Plane simply deletes the worktree. Main repository remains untouched.

### Negative

1. **Disk Space:** A project with large `node_modules` or `venv` might consume gigabytes per active worktree. (Mitigation: Hardlink `node_modules` or `.venv` during worktree creation).
2. **Setup Latency:** Creating a worktree and linking dependencies adds ~1-3 seconds to task dispatch time.

## Related Decisions
- Enhances ADR-010 (Lease System).
- Required for ADR-013 (Race Mode).

**Status: PROPOSED**
