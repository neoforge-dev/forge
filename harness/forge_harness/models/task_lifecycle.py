"""Task Lifecycle Contract for the FORGE Dark Factory.

Defines the canonical set of task states and the legally valid transitions
between them.  Any code that mutates task.status MUST call
``validate_transition`` before persisting the change.

State machine overview
-----------------------

    pending ──► queued ──► assigned ──► in_progress ──► candidate_done ──► evaluator_running ──► evaluator_passed ──► completed
       │           │           │             │                 │                    │                     │
       │           │           │             ▼                 └────────────────────┼────────────────────► completed (human-review bypass)
       │           │           │           failed ──► queued                        │
       │           │           │             │                              evaluator_failed ──► in_progress (retry)
       │           │           └──► cancelled                                       ├──► blocked ──► in_progress (unblocked)
       │           └──────────────► cancelled / expired ──► queued                 │           └──► cancelled
       └──────────────────────────► cancelled                                      └──► cancelled (abandon)

Terminal states: completed, cancelled
Requeue-eligible states: failed, expired
Non-terminal evaluator states: candidate_done, evaluator_running, evaluator_failed, blocked
"""

from __future__ import annotations

from enum import Enum

# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------

class TaskLifecycleState(str, Enum):
    """Canonical lifecycle states for a FORGE Dark Factory task.

    String-valued so that states can be used directly as JSON strings without
    extra serialisation plumbing.
    """

    # --- submission / scheduling ---
    PENDING = "pending"              # Created, not yet visible to the scheduler.
    QUEUED = "queued"                # Scheduler has accepted it; awaiting an agent.

    # --- agent execution ---
    ASSIGNED = "assigned"            # An agent has been nominated; not yet running.
    IN_PROGRESS = "in_progress"      # Agent is actively executing the task.

    # --- evaluation pipeline ---
    CANDIDATE_DONE = "candidate_done"          # Agent finished; awaiting evaluation.
    EVALUATOR_RUNNING = "evaluator_running"    # Automated evaluator is running.
    EVALUATOR_PASSED = "evaluator_passed"      # Evaluator verdict: pass.
    EVALUATOR_FAILED = "evaluator_failed"      # Evaluator verdict: fail.

    # --- evaluator escalation ---
    BLOCKED = "blocked"        # Escalated to human; agents may not proceed.

    # --- terminal / recovery ---
    COMPLETED = "completed"    # Task finished successfully (terminal).
    FAILED = "failed"          # Unrecoverable agent failure.
    CANCELLED = "cancelled"    # Explicitly cancelled (terminal).
    EXPIRED = "expired"        # Timed-out before an agent could be assigned.


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

#: Maps each state to the frozenset of states it is legally allowed to
#: transition into.  An empty frozenset marks a terminal state.
VALID_TRANSITIONS: dict[TaskLifecycleState, frozenset[TaskLifecycleState]] = {
    # Submission lane
    TaskLifecycleState.PENDING: frozenset({
        TaskLifecycleState.QUEUED,
        TaskLifecycleState.CANCELLED,
    }),

    TaskLifecycleState.QUEUED: frozenset({
        TaskLifecycleState.ASSIGNED,
        TaskLifecycleState.CANCELLED,
        TaskLifecycleState.EXPIRED,
    }),

    # Agent execution lane
    TaskLifecycleState.ASSIGNED: frozenset({
        TaskLifecycleState.IN_PROGRESS,
        TaskLifecycleState.CANCELLED,
        TaskLifecycleState.EXPIRED,
    }),

    TaskLifecycleState.IN_PROGRESS: frozenset({
        TaskLifecycleState.CANDIDATE_DONE,
        TaskLifecycleState.FAILED,
        TaskLifecycleState.CANCELLED,
    }),

    # Evaluation pipeline
    # NOTE: completed is only valid here in *human-review* lanes (no evaluator).
    TaskLifecycleState.CANDIDATE_DONE: frozenset({
        TaskLifecycleState.EVALUATOR_RUNNING,
        TaskLifecycleState.COMPLETED,       # Human-review bypass / direct approval
    }),

    TaskLifecycleState.EVALUATOR_RUNNING: frozenset({
        TaskLifecycleState.EVALUATOR_PASSED,
        TaskLifecycleState.EVALUATOR_FAILED,
        TaskLifecycleState.COMPLETED,     # Evaluator passed: auto-approve on pass
    }),

    TaskLifecycleState.EVALUATOR_PASSED: frozenset({
        TaskLifecycleState.COMPLETED,
    }),

    # Retry, escalate, or abandon after evaluation failure
    TaskLifecycleState.EVALUATOR_FAILED: frozenset({
        TaskLifecycleState.IN_PROGRESS,   # Retry: agent gets another attempt
        TaskLifecycleState.BLOCKED,       # Escalate: requires human intervention
        TaskLifecycleState.CANCELLED,     # Abandon: give up on this task
    }),

    # Blocked: human must intervene before execution can resume
    TaskLifecycleState.BLOCKED: frozenset({
        TaskLifecycleState.IN_PROGRESS,   # Human unblocked: resume agent execution
        TaskLifecycleState.CANCELLED,     # Human cancelled: abandon the task
    }),

    # --- Terminal states (empty transition sets) ---
    TaskLifecycleState.COMPLETED: frozenset(),

    # Recovery / requeue paths
    TaskLifecycleState.FAILED: frozenset({
        TaskLifecycleState.QUEUED,        # Requeue for another agent
    }),

    TaskLifecycleState.CANCELLED: frozenset(),  # terminal

    TaskLifecycleState.EXPIRED: frozenset({
        TaskLifecycleState.QUEUED,        # Requeue after TTL expiry
    }),
}

#: Convenience set of states from which no further transitions are allowed.
TERMINAL_STATES: frozenset[TaskLifecycleState] = frozenset(
    state for state, targets in VALID_TRANSITIONS.items() if not targets
)


# ---------------------------------------------------------------------------
# Contract helpers
# ---------------------------------------------------------------------------

class TransitionError(ValueError):
    """Raised when a requested state transition violates the lifecycle contract.

    Attributes:
        from_state: The state the task is currently in.
        to_state: The state that was requested.
        allowed: The set of states that would have been valid.
    """

    def __init__(
        self,
        from_state: TaskLifecycleState,
        to_state: TaskLifecycleState,
        allowed: frozenset[TaskLifecycleState],
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.allowed = allowed

        if allowed:
            allowed_names = ", ".join(sorted(s.value for s in allowed))
            detail = f"allowed next states: [{allowed_names}]"
        else:
            detail = "state is terminal; no further transitions are permitted"

        super().__init__(
            f"Invalid transition {from_state.value!r} -> {to_state.value!r}: {detail}"
        )


def validate_transition(
    from_state: TaskLifecycleState | str,
    to_state: TaskLifecycleState | str,
) -> bool:
    """Assert that a state transition is valid under the lifecycle contract.

    Args:
        from_state: Current state of the task (``TaskLifecycleState`` or its
            string value).
        to_state: Requested next state.

    Returns:
        ``True`` when the transition is valid.

    Raises:
        TransitionError: When the requested transition is not in the contract.
        ValueError: When either argument is not a recognised state value.
    """
    # Normalise to enum members so callers can pass raw strings from JSON/DB.
    if not isinstance(from_state, TaskLifecycleState):
        from_state = TaskLifecycleState(from_state)
    if not isinstance(to_state, TaskLifecycleState):
        to_state = TaskLifecycleState(to_state)

    allowed = VALID_TRANSITIONS[from_state]
    if to_state not in allowed:
        raise TransitionError(from_state, to_state, allowed)

    return True
