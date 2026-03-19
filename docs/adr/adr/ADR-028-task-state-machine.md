# ADR-028: Task Lifecycle State Machine

**Date:** 2026-03-05
**Status:** Accepted (partial — COMPLETED→APPROVED gate pending)
**Decision Makers:** Bogdan Veliscu (CTO, FORGE)

---

## Context

Previous ADRs defined the data structures for tasks and the architecture for dispatching them (ADR-010 Lease System, ADR-024 Worktree Isolation), but the system lacked a formalized State Machine. 

Without a strict state machine, edge cases were undefined:
- What happens if an agent disconnects mid-task?
- When exactly does worktree cleanup occur?
- How does the system handle an approval timeout?

### Alternatives Considered

| Alternative | Pros | Cons | Verdict |
|-------------|------|------|---------|
| Ad-hoc Status Strings | Simple to implement initially | Leads to inconsistent states, bugs, dangling worktrees | ❌ REJECTED |
| **Strict FSM in Go/SQLite** | **Predictable transitions, explicit error handling** | **Requires upfront design** | ✅ **ACCEPTED** |

---

## Decision

The `forge-v3` Go daemon will implement a strict Finite State Machine (FSM) for the Task Lifecycle. Every state transition will be an atomic SQLite transaction that potentially emits an Event Bus message.

### Task States

1. **`QUEUED`**: Task is created but has no active lease. No worktree exists.
2. **`DISPATCHED`**: Control Plane has selected an agent. Worktree is created (ADR-024). Subprocess/Adapter is spawning.
3. **`RUNNING`**: Agent is actively executing in the worktree. Telemetry is being received.
4. **`BLOCKED`**: Agent has hit an error or explicit `RequireHumanReview` hook. Execution pauses.
5. **`COMPLETED`**: Agent indicates task is finished (e.g., tests pass). Awaiting approval.
6. **`APPROVED`**: Confidence score met threshold OR human explicitly approved. Merge sequence begins.
7. **`FAILED`**: Task failed critically, timeout reached, or explicitly rejected by human.

### State Transitions & Hooks

| From | To | Trigger | Action Performed |
|------|----|---------|------------------|
| QUEUED | DISPATCHED | Lease Manager assigns task | Create `git worktree`, spawn Adapter |
| DISPATCHED | RUNNING | Adapter sends first telemetry | Update Lease `started_at` |
| RUNNING | RUNNING | Telemetry received | Update `last_heartbeat` |
| RUNNING | BLOCKED | Agent hits exception/prompt | Emit `NeedsAttention` event to UI |
| RUNNING | COMPLETED | Agent exits successfully | Calculate Confidence Score (ADR-012) |
| RUNNING | FAILED | Agent exits with code > 0 | Emit `TaskFailed` event |
| BLOCKED | RUNNING | Human provides input/resolves | Resume agent subprocess |
| COMPLETED | APPROVED | Confidence > 0.9 OR Human ACK | Push branch, Merge to main |
| COMPLETED | FAILED | Human REJECTS | Emit `TaskRejected` event |
| APPROVED | (Done) | Post-merge hook completes | Delete `git worktree`, close Lease |
| FAILED | QUEUED | Retry policy allows | Delete old worktree, reset Lease |
| FAILED | (Done) | Retry exhausted | Delete `git worktree`, mark as Dead |
| *ANY* | STALE | Patrol detects heartbeat timeout | Terminate Adapter, transition to FAILED |

---

## Consequences

### Positive

1. **Predictability:** The Control Plane knows exactly what to do when a task fails or a timeout occurs.
2. **Clean Teardown:** The explicit transition from `APPROVED` -> `Done` or `FAILED` -> `Done` guarantees that `git worktree remove` is always executed, preventing disk pollution.
3. **UI Reflection:** The HTMX UI can easily map these 7 states into visual badges and progress bars.

### Negative

1. **Rigidity:** Manual intervention to "force" a task into a specific state requires executing a specific Go CLI command or SQL mutation, rather than simply editing a JSON file.

## Related Decisions
- Ties into ADR-010 (Lease System).
- Controls the lifecycle of the environments described in ADR-024 (Worktree Isolation).

**Status: PROPOSED**
