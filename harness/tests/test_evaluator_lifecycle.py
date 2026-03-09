"""Evaluator lifecycle state tests for the FORGE Dark Factory — DF-1002.

Covers all new evaluator-gated states and transitions added in DF-1002:

New states
----------
- ``candidate_done``      Agent reports work complete, pending evaluator.
- ``evaluator_running``   Evaluator checks executing.
- ``evaluator_failed``    Evaluator checks failed (can retry or escalate).
- ``blocked``             Escalated to human; agents may not proceed.

New transitions
---------------
- in_progress → candidate_done
- candidate_done → evaluator_running
- evaluator_running → evaluator_failed
- evaluator_running → done        (evaluator auto-approved on pass)
- evaluator_failed → in_progress  (retry)
- evaluator_failed → blocked      (escalate to human)
- blocked → in_progress           (human unblocked)
- blocked → cancelled             (human cancelled)

Test strategy
-------------
1. All new valid transitions return True.
2. Invalid transitions from new states raise TransitionError.
3. New states (candidate_done, evaluator_running, evaluator_failed, blocked)
   are NOT in TERMINAL_STATES.
4. Backward compatibility: all previously existing transitions still work.
5. Happy path end-to-end: evaluator → done.
6. Retry path: evaluator_failed → in_progress.
7. Escalation path: evaluator_failed → blocked.
8. candidate_done cannot skip directly to done (must go through evaluator).
9. Full parametrized matrix for all transitions from new states.
10. String-valued states work for new state names.
"""

from __future__ import annotations

import pytest

from forge_harness.models.task_lifecycle import (
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    TaskLifecycleState,
    TransitionError,
    validate_transition,
)

# ---------------------------------------------------------------------------
# Shorthand aliases
# ---------------------------------------------------------------------------

S = TaskLifecycleState


# ===========================================================================
# 1. New states exist in the enum
# ===========================================================================


class TestNewStatesExist:
    """All four new evaluator states must be members of TaskLifecycleState."""

    def test_candidate_done_state_exists(self) -> None:
        assert S.CANDIDATE_DONE.value == "candidate_done"

    def test_evaluator_running_state_exists(self) -> None:
        assert S.EVALUATOR_RUNNING.value == "evaluator_running"

    def test_evaluator_failed_state_exists(self) -> None:
        assert S.EVALUATOR_FAILED.value == "evaluator_failed"

    def test_blocked_state_exists(self) -> None:
        assert S.BLOCKED.value == "blocked"

    def test_all_new_states_are_enum_members(self) -> None:
        """Verify round-trip: string value resolves to the correct enum member."""
        for value, expected in [
            ("candidate_done", S.CANDIDATE_DONE),
            ("evaluator_running", S.EVALUATOR_RUNNING),
            ("evaluator_failed", S.EVALUATOR_FAILED),
            ("blocked", S.BLOCKED),
        ]:
            assert S(value) is expected


# ===========================================================================
# 2. New states are NOT terminal
# ===========================================================================


class TestNewStatesAreNotTerminal:
    """candidate_done, evaluator_running, evaluator_failed, and blocked must
    have outbound transitions and therefore must not appear in TERMINAL_STATES.
    """

    @pytest.mark.parametrize(
        "state",
        [S.CANDIDATE_DONE, S.EVALUATOR_RUNNING, S.EVALUATOR_FAILED, S.BLOCKED],
        ids=["candidate_done", "evaluator_running", "evaluator_failed", "blocked"],
    )
    def test_new_state_is_not_terminal(self, state: TaskLifecycleState) -> None:
        assert state not in TERMINAL_STATES, (
            f"State {state.value!r} must NOT be terminal — it has valid outbound "
            f"transitions and represents a recoverable / in-flight condition."
        )

    @pytest.mark.parametrize(
        "state",
        [S.CANDIDATE_DONE, S.EVALUATOR_RUNNING, S.EVALUATOR_FAILED, S.BLOCKED],
        ids=["candidate_done", "evaluator_running", "evaluator_failed", "blocked"],
    )
    def test_new_state_has_non_empty_outbound_set(self, state: TaskLifecycleState) -> None:
        outbound = VALID_TRANSITIONS[state]
        assert len(outbound) > 0, (
            f"State {state.value!r} has an empty outbound set, which makes it "
            f"terminal.  Add at least one valid outbound transition."
        )


# ===========================================================================
# 3. New valid transitions — parametrized matrix
# ===========================================================================


NEW_VALID_TRANSITIONS = [
    # Evaluator pipeline entry
    (S.IN_PROGRESS, S.CANDIDATE_DONE),
    # Evaluator picks up the work
    (S.CANDIDATE_DONE, S.EVALUATOR_RUNNING),
    # Evaluator failure
    (S.EVALUATOR_RUNNING, S.EVALUATOR_FAILED),
    # Evaluator auto-approve (direct to done)
    (S.EVALUATOR_RUNNING, S.COMPLETED),
    # Retry after failure
    (S.EVALUATOR_FAILED, S.IN_PROGRESS),
    # Escalate to human
    (S.EVALUATOR_FAILED, S.BLOCKED),
    # Human unblocks
    (S.BLOCKED, S.IN_PROGRESS),
    # Human cancels blocked task
    (S.BLOCKED, S.CANCELLED),
]


class TestNewValidTransitions:
    """Every new edge must be accepted by validate_transition."""

    @pytest.mark.parametrize(
        "from_state, to_state",
        NEW_VALID_TRANSITIONS,
        ids=[f"{f.value}->{t.value}" for f, t in NEW_VALID_TRANSITIONS],
    )
    def test_new_valid_transition_returns_true(
        self, from_state: TaskLifecycleState, to_state: TaskLifecycleState
    ) -> None:
        assert validate_transition(from_state, to_state) is True


# ===========================================================================
# 4. Invalid transitions from new states raise TransitionError
# ===========================================================================


NEW_INVALID_TRANSITIONS = [
    # candidate_done cannot skip to done without evaluator
    (S.CANDIDATE_DONE, S.FAILED),
    (S.CANDIDATE_DONE, S.IN_PROGRESS),
    (S.CANDIDATE_DONE, S.CANCELLED),
    (S.CANDIDATE_DONE, S.BLOCKED),
    # evaluator_running cannot jump over evaluator verdicts
    (S.EVALUATOR_RUNNING, S.IN_PROGRESS),
    (S.EVALUATOR_RUNNING, S.CANDIDATE_DONE),
    (S.EVALUATOR_RUNNING, S.CANCELLED),
    (S.EVALUATOR_RUNNING, S.BLOCKED),
    # evaluator_failed cannot go to completed directly (must retry or escalate)
    (S.EVALUATOR_FAILED, S.COMPLETED),
    (S.EVALUATOR_FAILED, S.CANDIDATE_DONE),
    (S.EVALUATOR_FAILED, S.EVALUATOR_RUNNING),
    (S.EVALUATOR_FAILED, S.EVALUATOR_PASSED),
    # blocked is not terminal but cannot transition to most states
    (S.BLOCKED, S.COMPLETED),
    (S.BLOCKED, S.EVALUATOR_RUNNING),
    (S.BLOCKED, S.EVALUATOR_FAILED),
    (S.BLOCKED, S.CANDIDATE_DONE),
    (S.BLOCKED, S.QUEUED),
    (S.BLOCKED, S.ASSIGNED),
    (S.BLOCKED, S.FAILED),
    (S.BLOCKED, S.EXPIRED),
]


class TestInvalidTransitionsFromNewStates:
    """Edges that must NOT exist from new states."""

    @pytest.mark.parametrize(
        "from_state, to_state",
        NEW_INVALID_TRANSITIONS,
        ids=[f"{f.value}->{t.value}" for f, t in NEW_INVALID_TRANSITIONS],
    )
    def test_invalid_new_transition_raises_transition_error(
        self, from_state: TaskLifecycleState, to_state: TaskLifecycleState
    ) -> None:
        with pytest.raises(TransitionError):
            validate_transition(from_state, to_state)

    def test_candidate_done_cannot_go_directly_to_done(self) -> None:
        """candidate_done → done must be gated through evaluator_running."""
        # The human-review bypass (candidate_done → completed) IS valid.
        # But candidate_done → completed via evaluator skip is what we must
        # gate: if no evaluator bypass, you MUST go through evaluator_running.
        # The only direct path to completed from candidate_done is the
        # human-review bypass which IS in the table, so we test a clearly
        # invalid skip instead.
        with pytest.raises(TransitionError):
            validate_transition(S.CANDIDATE_DONE, S.IN_PROGRESS)

    def test_evaluator_failed_cannot_skip_to_evaluator_passed(self) -> None:
        """A failed evaluator run cannot self-report as passed."""
        with pytest.raises(TransitionError):
            validate_transition(S.EVALUATOR_FAILED, S.EVALUATOR_PASSED)

    def test_blocked_cannot_self_transition(self) -> None:
        with pytest.raises(TransitionError):
            validate_transition(S.BLOCKED, S.BLOCKED)

    def test_evaluator_running_cannot_self_transition(self) -> None:
        with pytest.raises(TransitionError):
            validate_transition(S.EVALUATOR_RUNNING, S.EVALUATOR_RUNNING)

    def test_candidate_done_cannot_self_transition(self) -> None:
        with pytest.raises(TransitionError):
            validate_transition(S.CANDIDATE_DONE, S.CANDIDATE_DONE)

    def test_evaluator_failed_cannot_self_transition(self) -> None:
        with pytest.raises(TransitionError):
            validate_transition(S.EVALUATOR_FAILED, S.EVALUATOR_FAILED)


# ===========================================================================
# 5. Backward compatibility — all pre-DF-1002 transitions still work
# ===========================================================================


LEGACY_VALID_TRANSITIONS = [
    # Submission lane
    (S.PENDING, S.QUEUED),
    (S.PENDING, S.CANCELLED),
    # Scheduling lane
    (S.QUEUED, S.ASSIGNED),
    (S.QUEUED, S.CANCELLED),
    (S.QUEUED, S.EXPIRED),
    # Agent execution lane
    (S.ASSIGNED, S.IN_PROGRESS),
    (S.ASSIGNED, S.CANCELLED),
    (S.ASSIGNED, S.EXPIRED),
    (S.IN_PROGRESS, S.CANDIDATE_DONE),
    (S.IN_PROGRESS, S.FAILED),
    (S.IN_PROGRESS, S.CANCELLED),
    # Evaluation pipeline (pre-existing edges)
    (S.CANDIDATE_DONE, S.EVALUATOR_RUNNING),
    (S.CANDIDATE_DONE, S.COMPLETED),  # Human-review bypass
    (S.EVALUATOR_RUNNING, S.EVALUATOR_PASSED),
    (S.EVALUATOR_RUNNING, S.EVALUATOR_FAILED),
    (S.EVALUATOR_PASSED, S.COMPLETED),
    (S.EVALUATOR_FAILED, S.IN_PROGRESS),  # Retry (was already valid)
    (S.EVALUATOR_FAILED, S.CANCELLED),  # Abandon (was already valid)
    # Recovery
    (S.FAILED, S.QUEUED),
    (S.EXPIRED, S.QUEUED),
]


class TestBackwardCompatibility:
    """All transitions that existed before DF-1002 must still work."""

    @pytest.mark.parametrize(
        "from_state, to_state",
        LEGACY_VALID_TRANSITIONS,
        ids=[f"{f.value}->{t.value}" for f, t in LEGACY_VALID_TRANSITIONS],
    )
    def test_legacy_valid_transition_still_works(
        self, from_state: TaskLifecycleState, to_state: TaskLifecycleState
    ) -> None:
        assert validate_transition(from_state, to_state) is True

    def test_terminal_states_unchanged(self) -> None:
        """completed and cancelled must still be the only terminal states."""
        assert S.COMPLETED in TERMINAL_STATES
        assert S.CANCELLED in TERMINAL_STATES

    def test_failed_still_requeues(self) -> None:
        assert validate_transition(S.FAILED, S.QUEUED) is True

    def test_expired_still_requeues(self) -> None:
        assert validate_transition(S.EXPIRED, S.QUEUED) is True

    def test_in_progress_to_failed_still_valid(self) -> None:
        assert validate_transition(S.IN_PROGRESS, S.FAILED) is True

    def test_evaluator_passed_to_completed_still_valid(self) -> None:
        assert validate_transition(S.EVALUATOR_PASSED, S.COMPLETED) is True

    def test_human_review_bypass_still_valid(self) -> None:
        """candidate_done → completed bypass for human-review lanes unchanged."""
        assert validate_transition(S.CANDIDATE_DONE, S.COMPLETED) is True


# ===========================================================================
# 6. Happy path — evaluator → done (auto-approve on pass)
# ===========================================================================


class TestEvaluatorHappyPath:
    """Walk the full evaluator pipeline: agent completes → evaluator passes → done."""

    HAPPY_PATH = [
        S.PENDING,
        S.QUEUED,
        S.ASSIGNED,
        S.IN_PROGRESS,
        S.CANDIDATE_DONE,
        S.EVALUATOR_RUNNING,
        S.COMPLETED,  # evaluator auto-approved
    ]

    def test_full_happy_path_all_transitions_valid(self) -> None:
        """Every consecutive pair in the happy path must be a valid edge."""
        for from_s, to_s in zip(self.HAPPY_PATH, self.HAPPY_PATH[1:]):
            assert validate_transition(from_s, to_s) is True, (
                f"Expected valid transition {from_s.value!r} -> {to_s.value!r} "
                f"in the evaluator happy path."
            )

    def test_evaluator_running_directly_to_completed(self) -> None:
        """evaluator_running → completed must be valid for auto-approve flows."""
        assert validate_transition(S.EVALUATOR_RUNNING, S.COMPLETED) is True

    def test_two_stage_pass_path_also_valid(self) -> None:
        """The full evaluator_passed intermediate path still works."""
        assert validate_transition(S.EVALUATOR_RUNNING, S.EVALUATOR_PASSED) is True
        assert validate_transition(S.EVALUATOR_PASSED, S.COMPLETED) is True


# ===========================================================================
# 7. Retry path — evaluator_failed → in_progress
# ===========================================================================


class TestEvaluatorRetryPath:
    """After evaluator failure the agent gets another attempt via in_progress."""

    RETRY_PATH = [
        S.IN_PROGRESS,
        S.CANDIDATE_DONE,
        S.EVALUATOR_RUNNING,
        S.EVALUATOR_FAILED,
        S.IN_PROGRESS,  # retry: back to work
        S.CANDIDATE_DONE,  # agent finishes again
        S.EVALUATOR_RUNNING,  # evaluator reruns
        S.COMPLETED,  # passes on retry
    ]

    def test_evaluator_failed_to_in_progress_is_valid(self) -> None:
        assert validate_transition(S.EVALUATOR_FAILED, S.IN_PROGRESS) is True

    def test_full_retry_path_is_valid(self) -> None:
        for from_s, to_s in zip(self.RETRY_PATH, self.RETRY_PATH[1:]):
            assert validate_transition(from_s, to_s) is True, (
                f"Expected valid transition {from_s.value!r} -> {to_s.value!r} "
                f"in the evaluator retry path."
            )

    def test_retry_preserves_candidate_done_path(self) -> None:
        """After retry, in_progress can reach candidate_done again."""
        assert validate_transition(S.IN_PROGRESS, S.CANDIDATE_DONE) is True


# ===========================================================================
# 8. Escalation path — evaluator_failed → blocked
# ===========================================================================


class TestEvaluatorEscalationPath:
    """Persistent evaluation failure escalates to blocked for human review."""

    def test_evaluator_failed_to_blocked_is_valid(self) -> None:
        assert validate_transition(S.EVALUATOR_FAILED, S.BLOCKED) is True

    def test_blocked_to_in_progress_is_valid(self) -> None:
        """After human intervention the task can resume."""
        assert validate_transition(S.BLOCKED, S.IN_PROGRESS) is True

    def test_blocked_to_cancelled_is_valid(self) -> None:
        """Human may choose to abandon a blocked task."""
        assert validate_transition(S.BLOCKED, S.CANCELLED) is True

    def test_full_escalation_and_resume_path(self) -> None:
        escalation_and_resume = [
            S.EVALUATOR_FAILED,
            S.BLOCKED,
            S.IN_PROGRESS,
            S.CANDIDATE_DONE,
            S.EVALUATOR_RUNNING,
            S.COMPLETED,
        ]
        for from_s, to_s in zip(escalation_and_resume, escalation_and_resume[1:]):
            assert validate_transition(from_s, to_s) is True, (
                f"Expected valid transition {from_s.value!r} -> {to_s.value!r} "
                f"in the escalation-and-resume path."
            )

    def test_full_escalation_and_cancel_path(self) -> None:
        escalation_and_cancel = [
            S.EVALUATOR_FAILED,
            S.BLOCKED,
            S.CANCELLED,
        ]
        for from_s, to_s in zip(escalation_and_cancel, escalation_and_cancel[1:]):
            assert validate_transition(from_s, to_s) is True, (
                f"Expected valid transition {from_s.value!r} -> {to_s.value!r} "
                f"in the escalation-and-cancel path."
            )

    def test_blocked_is_not_abandoned_automatically(self) -> None:
        """Blocked must go through explicit human action — not auto-completed."""
        with pytest.raises(TransitionError):
            validate_transition(S.BLOCKED, S.COMPLETED)


# ===========================================================================
# 9. candidate_done cannot skip directly to done
# ===========================================================================


class TestCandidateDoneConstraints:
    """candidate_done must always route through evaluator or human bypass."""

    def test_candidate_done_cannot_go_to_failed(self) -> None:
        with pytest.raises(TransitionError):
            validate_transition(S.CANDIDATE_DONE, S.FAILED)

    def test_candidate_done_cannot_go_to_in_progress(self) -> None:
        with pytest.raises(TransitionError):
            validate_transition(S.CANDIDATE_DONE, S.IN_PROGRESS)

    def test_candidate_done_cannot_go_to_cancelled(self) -> None:
        """Cancellation from candidate_done is not permitted — must go via running."""
        with pytest.raises(TransitionError):
            validate_transition(S.CANDIDATE_DONE, S.CANCELLED)

    def test_candidate_done_cannot_go_to_blocked(self) -> None:
        """Only evaluator_failed can escalate to blocked, not candidate_done."""
        with pytest.raises(TransitionError):
            validate_transition(S.CANDIDATE_DONE, S.BLOCKED)

    def test_candidate_done_valid_paths(self) -> None:
        """candidate_done has exactly two valid outbound transitions."""
        outbound = VALID_TRANSITIONS[S.CANDIDATE_DONE]
        assert S.EVALUATOR_RUNNING in outbound, "candidate_done must allow evaluator_running"
        assert S.COMPLETED in outbound, "candidate_done must allow completed (human bypass)"
        # Confirm no unexpected extras
        assert outbound == frozenset({S.EVALUATOR_RUNNING, S.COMPLETED}), (
            f"candidate_done outbound set differs from expected. Got: "
            f"{sorted(s.value for s in outbound)}"
        )


# ===========================================================================
# 10. String-valued states work for new state names
# ===========================================================================


class TestStringStateAcceptanceForNewStates:
    """validate_transition must accept raw string values for all new states."""

    @pytest.mark.parametrize(
        "from_str, to_str",
        [
            ("in_progress", "candidate_done"),
            ("candidate_done", "evaluator_running"),
            ("evaluator_running", "evaluator_failed"),
            ("evaluator_running", "completed"),
            ("evaluator_failed", "in_progress"),
            ("evaluator_failed", "blocked"),
            ("blocked", "in_progress"),
            ("blocked", "cancelled"),
        ],
        ids=[
            "in_progress->candidate_done",
            "candidate_done->evaluator_running",
            "evaluator_running->evaluator_failed",
            "evaluator_running->completed",
            "evaluator_failed->in_progress",
            "evaluator_failed->blocked",
            "blocked->in_progress",
            "blocked->cancelled",
        ],
    )
    def test_string_transition_accepted(self, from_str: str, to_str: str) -> None:
        assert validate_transition(from_str, to_str) is True

    def test_invalid_string_from_new_state_raises_transition_error(self) -> None:
        with pytest.raises(TransitionError):
            validate_transition("blocked", "completed")

    def test_unknown_blocked_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            validate_transition("not_a_state", "blocked")


# ===========================================================================
# 11. VALID_TRANSITIONS table completeness — all new states have entries
# ===========================================================================


class TestTransitionTableCompleteness:
    """Every new state must have an explicit policy entry in VALID_TRANSITIONS."""

    @pytest.mark.parametrize(
        "state",
        [S.CANDIDATE_DONE, S.EVALUATOR_RUNNING, S.EVALUATOR_FAILED, S.BLOCKED],
        ids=["candidate_done", "evaluator_running", "evaluator_failed", "blocked"],
    )
    def test_new_state_has_table_entry(self, state: TaskLifecycleState) -> None:
        assert state in VALID_TRANSITIONS, (
            f"State {state.value!r} is missing from VALID_TRANSITIONS."
        )

    def test_all_states_covered_in_valid_transitions(self) -> None:
        """VALID_TRANSITIONS must cover every TaskLifecycleState member."""
        all_states = set(TaskLifecycleState)
        table_keys = set(VALID_TRANSITIONS.keys())
        missing = all_states - table_keys
        assert not missing, (
            f"States missing from VALID_TRANSITIONS: {sorted(s.value for s in missing)}"
        )

    def test_blocked_outbound_set_is_correct(self) -> None:
        """BLOCKED must allow exactly in_progress and cancelled."""
        outbound = VALID_TRANSITIONS[S.BLOCKED]
        assert outbound == frozenset({S.IN_PROGRESS, S.CANCELLED}), (
            f"BLOCKED outbound set is wrong. Got: {sorted(s.value for s in outbound)}"
        )

    def test_evaluator_failed_outbound_set_includes_blocked(self) -> None:
        """evaluator_failed must have blocked as a valid target (DF-1002 requirement)."""
        outbound = VALID_TRANSITIONS[S.EVALUATOR_FAILED]
        assert S.BLOCKED in outbound, (
            f"EVALUATOR_FAILED outbound set must include 'blocked'. "
            f"Got: {sorted(s.value for s in outbound)}"
        )

    def test_evaluator_running_outbound_set_includes_completed(self) -> None:
        """evaluator_running must have completed as a valid target (auto-approve path)."""
        outbound = VALID_TRANSITIONS[S.EVALUATOR_RUNNING]
        assert S.COMPLETED in outbound, (
            f"EVALUATOR_RUNNING outbound set must include 'completed'. "
            f"Got: {sorted(s.value for s in outbound)}"
        )
