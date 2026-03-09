"""Contract tests for the FORGE Dark Factory task lifecycle.

Validates every cell of the VALID_TRANSITIONS table:
  - All valid transitions succeed (return True).
  - All invalid transitions raise TransitionError.
  - Terminal states block every outbound move.
  - Requeue paths are available from failed / expired.
  - The full evaluator pipeline path is coherent.
  - The human-review bypass (candidate_done -> completed) is permitted.
  - String-valued states are accepted in addition to enum members.
"""

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
# 1. All valid transitions succeed
# ===========================================================================


class TestValidTransitions:
    """Every (from, to) pair listed in VALID_TRANSITIONS must pass."""

    @pytest.mark.parametrize(
        "from_state, to_state",
        [(from_s, to_s) for from_s, targets in VALID_TRANSITIONS.items() for to_s in targets],
    )
    def test_valid_pair_returns_true(self, from_state, to_state):
        assert validate_transition(from_state, to_state) is True

    def test_pending_to_queued(self):
        assert validate_transition(S.PENDING, S.QUEUED) is True

    def test_pending_to_cancelled(self):
        assert validate_transition(S.PENDING, S.CANCELLED) is True

    def test_queued_to_assigned(self):
        assert validate_transition(S.QUEUED, S.ASSIGNED) is True

    def test_queued_to_expired(self):
        assert validate_transition(S.QUEUED, S.EXPIRED) is True

    def test_queued_to_cancelled(self):
        assert validate_transition(S.QUEUED, S.CANCELLED) is True

    def test_assigned_to_in_progress(self):
        assert validate_transition(S.ASSIGNED, S.IN_PROGRESS) is True

    def test_assigned_to_cancelled(self):
        assert validate_transition(S.ASSIGNED, S.CANCELLED) is True

    def test_assigned_to_expired(self):
        assert validate_transition(S.ASSIGNED, S.EXPIRED) is True

    def test_in_progress_to_candidate_done(self):
        assert validate_transition(S.IN_PROGRESS, S.CANDIDATE_DONE) is True

    def test_in_progress_to_failed(self):
        assert validate_transition(S.IN_PROGRESS, S.FAILED) is True

    def test_in_progress_to_cancelled(self):
        assert validate_transition(S.IN_PROGRESS, S.CANCELLED) is True

    def test_candidate_done_to_evaluator_running(self):
        assert validate_transition(S.CANDIDATE_DONE, S.EVALUATOR_RUNNING) is True

    def test_candidate_done_to_completed_human_review_bypass(self):
        assert validate_transition(S.CANDIDATE_DONE, S.COMPLETED) is True

    def test_evaluator_running_to_evaluator_passed(self):
        assert validate_transition(S.EVALUATOR_RUNNING, S.EVALUATOR_PASSED) is True

    def test_evaluator_running_to_evaluator_failed(self):
        assert validate_transition(S.EVALUATOR_RUNNING, S.EVALUATOR_FAILED) is True

    def test_evaluator_passed_to_completed(self):
        assert validate_transition(S.EVALUATOR_PASSED, S.COMPLETED) is True

    def test_evaluator_failed_to_in_progress_retry(self):
        assert validate_transition(S.EVALUATOR_FAILED, S.IN_PROGRESS) is True

    def test_evaluator_failed_to_cancelled_abandon(self):
        assert validate_transition(S.EVALUATOR_FAILED, S.CANCELLED) is True

    def test_failed_to_queued_requeue(self):
        assert validate_transition(S.FAILED, S.QUEUED) is True

    def test_expired_to_queued_requeue(self):
        assert validate_transition(S.EXPIRED, S.QUEUED) is True


# ===========================================================================
# 2. Invalid transitions raise TransitionError
# ===========================================================================


class TestInvalidTransitions:
    """Non-existent edges must raise TransitionError."""

    @pytest.mark.parametrize(
        "from_state, to_state",
        [
            # Backward / skipping moves
            (S.PENDING, S.IN_PROGRESS),
            (S.PENDING, S.COMPLETED),
            (S.PENDING, S.FAILED),
            (S.QUEUED, S.IN_PROGRESS),
            (S.QUEUED, S.COMPLETED),
            (S.ASSIGNED, S.QUEUED),
            (S.ASSIGNED, S.COMPLETED),
            (S.IN_PROGRESS, S.QUEUED),
            (S.IN_PROGRESS, S.ASSIGNED),
            (S.IN_PROGRESS, S.COMPLETED),  # Must go via candidate_done
            # Evaluator pipeline violations
            # NOTE: evaluator_running → completed IS valid (DF-1002 auto-approve path).
            (S.EVALUATOR_PASSED, S.FAILED),
            (S.EVALUATOR_PASSED, S.EVALUATOR_RUNNING),
            (S.EVALUATOR_FAILED, S.COMPLETED),  # Must retry or cancel
            (S.EVALUATOR_FAILED, S.EVALUATOR_PASSED),
            # Recovery violations
            (S.FAILED, S.COMPLETED),
            (S.FAILED, S.CANCELLED),
            (S.EXPIRED, S.CANCELLED),
            (S.EXPIRED, S.COMPLETED),
            # Nonsensical self-transitions
            (S.PENDING, S.PENDING),
            (S.IN_PROGRESS, S.IN_PROGRESS),
            (S.COMPLETED, S.COMPLETED),
        ],
    )
    def test_invalid_pair_raises_transition_error(self, from_state, to_state):
        with pytest.raises(TransitionError):
            validate_transition(from_state, to_state)

    def test_transition_error_carries_from_state(self):
        with pytest.raises(TransitionError) as exc_info:
            validate_transition(S.PENDING, S.COMPLETED)
        assert exc_info.value.from_state == S.PENDING

    def test_transition_error_carries_to_state(self):
        with pytest.raises(TransitionError) as exc_info:
            validate_transition(S.PENDING, S.COMPLETED)
        assert exc_info.value.to_state == S.COMPLETED

    def test_transition_error_carries_allowed_set(self):
        with pytest.raises(TransitionError) as exc_info:
            validate_transition(S.PENDING, S.COMPLETED)
        # PENDING may go to QUEUED or CANCELLED
        assert S.QUEUED in exc_info.value.allowed
        assert S.CANCELLED in exc_info.value.allowed

    def test_transition_error_message_is_descriptive(self):
        with pytest.raises(TransitionError) as exc_info:
            validate_transition(S.IN_PROGRESS, S.COMPLETED)
        msg = str(exc_info.value)
        assert "in_progress" in msg
        assert "completed" in msg

    def test_unknown_from_state_string_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_transition("not_a_real_state", S.QUEUED)

    def test_unknown_to_state_string_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_transition(S.PENDING, "not_a_real_state")


# ===========================================================================
# 3. Terminal states have no valid outbound transitions
# ===========================================================================


class TestTerminalStates:
    """completed and cancelled are terminal; nothing can follow them."""

    def test_terminal_states_set_is_non_empty(self):
        assert len(TERMINAL_STATES) >= 2

    def test_completed_is_terminal(self):
        assert S.COMPLETED in TERMINAL_STATES

    def test_cancelled_is_terminal(self):
        assert S.CANCELLED in TERMINAL_STATES

    @pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES, key=lambda s: s.value))
    def test_terminal_state_has_empty_outbound_set(self, terminal):
        assert VALID_TRANSITIONS[terminal] == frozenset()

    @pytest.mark.parametrize(
        "terminal, target",
        [(terminal, target) for terminal in TERMINAL_STATES for target in TaskLifecycleState],
    )
    def test_every_move_from_terminal_raises(self, terminal, target):
        with pytest.raises(TransitionError):
            validate_transition(terminal, target)


# ===========================================================================
# 4. Requeue paths
# ===========================================================================


class TestRequeuePaths:
    """failed and expired can both return to the scheduler queue."""

    def test_failed_requeues_to_queued(self):
        assert validate_transition(S.FAILED, S.QUEUED) is True

    def test_expired_requeues_to_queued(self):
        assert validate_transition(S.EXPIRED, S.QUEUED) is True

    def test_failed_cannot_complete_directly(self):
        with pytest.raises(TransitionError):
            validate_transition(S.FAILED, S.COMPLETED)

    def test_expired_cannot_assign_directly(self):
        with pytest.raises(TransitionError):
            validate_transition(S.EXPIRED, S.ASSIGNED)


# ===========================================================================
# 5. Full evaluator pipeline path
# ===========================================================================


class TestEvaluatorPipelinePath:
    """Walk the nominal evaluator pipeline end-to-end."""

    NOMINAL_PATH = [
        S.PENDING,
        S.QUEUED,
        S.ASSIGNED,
        S.IN_PROGRESS,
        S.CANDIDATE_DONE,
        S.EVALUATOR_RUNNING,
        S.EVALUATOR_PASSED,
        S.COMPLETED,
    ]

    def test_full_evaluator_pipeline_path_is_valid(self):
        """Every consecutive pair in the nominal path must be a valid edge."""
        for from_s, to_s in zip(self.NOMINAL_PATH, self.NOMINAL_PATH[1:]):
            assert validate_transition(from_s, to_s) is True, (
                f"Expected valid transition {from_s.value!r} -> {to_s.value!r}"
            )

    def test_evaluator_running_can_go_directly_to_completed(self):
        # DF-1002: evaluator_running → completed is valid for auto-approve on pass.
        # The two-stage path (evaluator_running → evaluator_passed → completed)
        # remains valid, but so does the direct path.
        assert validate_transition(S.EVALUATOR_RUNNING, S.COMPLETED) is True

    def test_evaluator_failed_allows_retry(self):
        assert validate_transition(S.EVALUATOR_FAILED, S.IN_PROGRESS) is True

    def test_retry_path_continues_to_candidate_done(self):
        """After a retry, the agent can finish again."""
        assert validate_transition(S.IN_PROGRESS, S.CANDIDATE_DONE) is True

    def test_evaluator_failed_abandon_path(self):
        """Repeated failures can legitimately end in cancellation."""
        assert validate_transition(S.EVALUATOR_FAILED, S.CANCELLED) is True


# ===========================================================================
# 6. Human-review bypass
# ===========================================================================


class TestHumanReviewBypass:
    """candidate_done -> completed is valid when no evaluator is configured."""

    def test_human_review_bypass_is_valid(self):
        assert validate_transition(S.CANDIDATE_DONE, S.COMPLETED) is True

    def test_candidate_done_still_allows_evaluator_route(self):
        assert validate_transition(S.CANDIDATE_DONE, S.EVALUATOR_RUNNING) is True

    def test_candidate_done_cannot_go_back_to_in_progress(self):
        with pytest.raises(TransitionError):
            validate_transition(S.CANDIDATE_DONE, S.IN_PROGRESS)

    def test_candidate_done_cannot_go_to_failed(self):
        with pytest.raises(TransitionError):
            validate_transition(S.CANDIDATE_DONE, S.FAILED)


# ===========================================================================
# 7. String-valued state acceptance
# ===========================================================================


class TestStringStateAcceptance:
    """validate_transition must work with raw string values (e.g., from JSON)."""

    def test_string_from_and_to_states_accepted(self):
        assert validate_transition("pending", "queued") is True

    def test_mixed_string_and_enum_accepted(self):
        assert validate_transition("pending", S.QUEUED) is True
        assert validate_transition(S.PENDING, "queued") is True

    def test_invalid_string_transition_raises_transition_error(self):
        with pytest.raises(TransitionError):
            validate_transition("pending", "completed")

    def test_unknown_string_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_transition("ghost_state", "queued")


# ===========================================================================
# 8. Contract completeness — every state has an entry in the table
# ===========================================================================


class TestContractCompleteness:
    """The transition table must cover every member of TaskLifecycleState."""

    def test_every_state_has_a_table_entry(self):
        for state in TaskLifecycleState:
            assert state in VALID_TRANSITIONS, (
                f"State {state.value!r} is missing from VALID_TRANSITIONS"
            )

    def test_all_target_states_are_valid_enum_members(self):
        for from_s, targets in VALID_TRANSITIONS.items():
            for to_s in targets:
                assert isinstance(to_s, TaskLifecycleState), (
                    f"Target {to_s!r} for {from_s.value!r} is not a TaskLifecycleState"
                )
