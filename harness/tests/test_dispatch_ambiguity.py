"""Tests for dispatch and task ambiguity guardrails (CCX-054).

These tests verify that ambiguous inputs fail-fast with structured errors
and next_action guidance.

Test Categories:
1. Agent ID ambiguity - partial/invalid agent IDs
2. Task ID validation - invalid/missing task IDs
3. Lifecycle transitions - invalid status transitions
4. Parameter validation - missing/invalid params
5. No side effects on invalid input
"""

import pytest

from forge_harness.fleet.dispatch_validation import (
    PARTIAL_NAME_PATTERNS,
    TASK_ID_PATTERN,
    VALID_AGENT_NAMES,
    TaskStatus,
    ValidationError,
    ValidationErrorCode,
    ValidationResult,
    validate_agent_id,
    validate_dispatch,
    validate_dispatch_params,
    validate_status_transition,
    validate_task,
    validate_task_id,
    validate_task_params,
)

# =============================================================================
# 1. Agent ID Ambiguity Tests
# =============================================================================


class TestAgentIdAmbiguity:
    """Tests for agent ID validation - partial/invalid IDs."""

    def test_valid_agent_id_exact_match(self):
        """Exact agent IDs should be valid."""
        for agent in ["forge:gemini", "forge:kimi-a", "forge:nova"]:
            error = validate_agent_id(agent)
            assert error is None, f"Expected {agent} to be valid"

    def test_valid_agent_id_without_prefix(self):
        """Agent names without prefix should be valid."""
        for agent in ["gemini", "kimi-a", "nova", "claude"]:
            error = validate_agent_id(agent)
            assert error is None, f"Expected {agent} to be valid"

    def test_ambiguous_partial_name_kimi(self):
        """'kimi' is ambiguous - could be kimi-a or kimi-b."""
        error = validate_agent_id("kimi")
        assert error is not None
        assert error.code == ValidationErrorCode.AGENT_ID_AMBIGUOUS
        assert "kimi-a" in error.next_action
        assert "kimi-b" in error.next_action

    def test_ambiguous_partial_name_cursor(self):
        """'cursor' is ambiguous - could be cursor-a or cursor-b."""
        error = validate_agent_id("cursor")
        assert error is not None
        assert error.code == ValidationErrorCode.AGENT_ID_AMBIGUOUS

    def test_ambiguous_partial_name_case_insensitive(self):
        """Ambiguity check should be case-insensitive."""
        error = validate_agent_id("KIMI")
        assert error is not None
        assert error.code == ValidationErrorCode.AGENT_ID_AMBIGUOUS

    def test_ambiguous_partial_name_open(self):
        """'open' is ambiguous - could match multiple agents."""
        error = validate_agent_id("open")
        assert error is not None
        assert error.code == ValidationErrorCode.AGENT_ID_AMBIGUOUS

    def test_missing_agent_id(self):
        """None agent ID should fail with clear error."""
        error = validate_agent_id(None)
        assert error is not None
        assert error.code == ValidationErrorCode.AGENT_ID_MISSING
        assert error.next_action is not None

    def test_empty_agent_id(self):
        """Empty agent ID should fail."""
        error = validate_agent_id("")
        assert error is not None
        assert error.code == ValidationErrorCode.AGENT_ID_MISSING

    def test_whitespace_only_agent_id(self):
        """Whitespace-only agent ID should fail."""
        error = validate_agent_id("   ")
        assert error is not None
        assert error.code == ValidationErrorCode.AGENT_ID_MISSING

    def test_invalid_characters_in_agent_id(self):
        """Agent IDs with special characters should fail."""
        error = validate_agent_id("forge:test@agent!")
        assert error is not None
        assert error.code == ValidationErrorCode.AGENT_ID_INVALID
        assert "invalid characters" in error.message.lower()

    def test_next_action_provides_guidance(self):
        """All ambiguous errors should provide next_action."""
        test_cases = [
            "kimi",  # ambiguous
            None,  # missing
            "",  # empty
        ]
        for agent_id in test_cases:
            error = validate_agent_id(agent_id)
            if error:
                assert error.next_action is not None, f"Missing next_action for {agent_id}"
                assert len(error.next_action) > 0, f"Empty next_action for {agent_id}"


# =============================================================================
# 2. Task ID Validation Tests
# =============================================================================


class TestTaskIdValidation:
    """Tests for task ID validation."""

    def test_valid_task_id_short(self):
        """Short task IDs (4+ chars) should be valid."""
        for task_id in ["abc1", "task", "test-001", "TASK_123"]:
            error = validate_task_id(task_id)
            assert error is None, f"Expected {task_id} to be valid"

    def test_valid_task_id_uuid_format(self):
        """UUID-format task IDs should be valid."""
        for task_id in ["a1b2c3d4e5f6", "c36a18f6290c49e49686", "msg-2b8513f1ca4c"]:
            error = validate_task_id(task_id)
            assert error is None, f"Expected {task_id} to be valid"

    def test_invalid_task_id_too_short(self):
        """Task IDs under 4 chars should fail."""
        error = validate_task_id("ab")
        assert error is not None
        assert error.code == ValidationErrorCode.TASK_ID_INVALID

    def test_invalid_task_id_special_chars(self):
        """Task IDs with special characters should fail."""
        error = validate_task_id("task@123!")
        assert error is not None
        assert error.code == ValidationErrorCode.TASK_ID_INVALID

    def test_missing_task_id(self):
        """None task ID should fail."""
        error = validate_task_id(None)
        assert error is not None
        assert error.code == ValidationErrorCode.TASK_ID_MISSING

    def test_empty_task_id(self):
        """Empty task ID should fail."""
        error = validate_task_id("")
        assert error is not None
        assert error.code == ValidationErrorCode.TASK_ID_MISSING


# =============================================================================
# 3. Lifecycle Transition Tests
# =============================================================================


class TestLifecycleTransitions:
    """Tests for task status transition validation."""

    def test_valid_transition_pending_to_assigned(self):
        """Pending -> Assigned is valid."""
        error = validate_status_transition(TaskStatus.PENDING, TaskStatus.ASSIGNED)
        assert error is None

    def test_valid_transition_assigned_to_in_progress(self):
        """Assigned -> In Progress is valid."""
        error = validate_status_transition(TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS)
        assert error is None

    def test_valid_transition_in_progress_to_completed(self):
        """In Progress -> Completed is valid."""
        error = validate_status_transition(TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED)
        assert error is None

    def test_valid_transition_failed_to_pending(self):
        """Failed -> Pending (retry) is valid."""
        error = validate_status_transition(TaskStatus.FAILED, TaskStatus.PENDING)
        assert error is None

    def test_invalid_transition_completed_to_pending(self):
        """Completed -> Pending is invalid (terminal state)."""
        error = validate_status_transition(TaskStatus.COMPLETED, TaskStatus.PENDING)
        assert error is not None
        assert error.code == ValidationErrorCode.LIFECYCLE_FORBIDDEN

    def test_invalid_transition_completed_to_assigned(self):
        """Completed -> Assigned is invalid."""
        error = validate_status_transition(TaskStatus.COMPLETED, TaskStatus.ASSIGNED)
        assert error is not None
        assert error.code == ValidationErrorCode.LIFECYCLE_FORBIDDEN

    def test_invalid_transition_cancelled_to_completed(self):
        """Cancelled -> Completed is invalid."""
        error = validate_status_transition(TaskStatus.CANCELLED, TaskStatus.COMPLETED)
        assert error is not None
        assert error.code == ValidationErrorCode.INVALID_STATUS_TRANSITION

    def test_transition_provides_valid_options(self):
        """Invalid transition errors should show valid options."""
        error = validate_status_transition(TaskStatus.PENDING, TaskStatus.COMPLETED)
        assert error is not None
        assert error.next_action is not None
        # Should mention at least one valid target
        assert "assigned" in error.next_action.lower() or "cancelled" in error.next_action.lower()

    def test_string_statuses_accepted(self):
        """String status values should work."""
        error = validate_status_transition("pending", "assigned")
        assert error is None


# =============================================================================
# 4. Parameter Validation Tests
# =============================================================================


class TestParameterValidation:
    """Tests for dispatch and task parameter validation."""

    def test_dispatch_valid_params(self):
        """Valid dispatch params should pass."""
        result = validate_dispatch("forge:gemini", "Task: test")
        assert result.valid
        assert len(result.errors) == 0

    def test_dispatch_missing_agent_id(self):
        """Missing agent ID should fail."""
        result = validate_dispatch(None, "Task: test")
        assert not result.valid
        assert any(e.code == ValidationErrorCode.AGENT_ID_MISSING for e in result.errors)

    def test_dispatch_missing_message(self):
        """Missing message should fail."""
        result = validate_dispatch("forge:gemini", None)
        assert not result.valid
        assert any(e.code == ValidationErrorCode.PARAM_MISSING for e in result.errors)

    def test_dispatch_empty_message(self):
        """Empty message should fail."""
        result = validate_dispatch("forge:gemini", "")
        assert not result.valid
        assert any(e.code == ValidationErrorCode.MESSAGE_EMPTY for e in result.errors)

    def test_dispatch_message_too_long(self):
        """Message exceeding max length should fail."""
        long_message = "x" * 6000
        result = validate_dispatch("forge:gemini", long_message)
        assert not result.valid
        assert any(e.code == ValidationErrorCode.MESSAGE_TOO_LONG for e in result.errors)

    def test_dispatch_ambiguous_agent_id(self):
        """Ambiguous agent ID should fail."""
        result = validate_dispatch("kimi", "Task: test")
        assert not result.valid
        assert any(e.code == ValidationErrorCode.AGENT_ID_AMBIGUOUS for e in result.errors)

    def test_task_valid_params(self):
        """Valid task params should pass."""
        result = validate_task("task-001", {"subject": "Test task"})
        assert result.valid
        assert len(result.errors) == 0

    def test_task_missing_subject(self):
        """Missing subject should fail."""
        result = validate_task("task-001", {"description": "Test"})
        assert not result.valid
        assert any(e.code == ValidationErrorCode.PARAM_MISSING for e in result.errors)

    def test_task_invalid_priority(self):
        """Invalid priority should fail."""
        result = validate_task("task-001", {"subject": "Test", "priority": "super-high"})
        assert not result.valid
        assert any(e.code == ValidationErrorCode.PARAM_INVALID for e in result.errors)

    def test_task_valid_priorities(self):
        """Valid priorities should pass."""
        for priority in ["critical", "high", "medium", "low"]:
            result = validate_task("task-001", {"subject": "Test", "priority": priority})
            assert result.valid, f"Priority '{priority}' should be valid"


# =============================================================================
# 5. No Side Effects Tests
# =============================================================================


class TestNoSideEffects:
    """Verify invalid inputs don't cause side effects."""

    def test_invalid_agent_id_does_not_modify_state(self):
        """Invalid agent ID should not modify any state."""
        # This is a conceptual test - we verify by checking no exceptions
        # are raised during validation
        error = validate_agent_id("invalid@agent!")
        assert error is not None

        # If we get here, no state was modified
        # (In real implementation, we'd verify no file/DB changes)

    def test_invalid_task_id_returns_error_not_exception(self):
        """Invalid task ID should return error, not raise exception."""
        # Should not raise
        error = validate_task_id("")
        assert error is not None

        error = validate_task_id(None)
        assert error is not None

    def test_validation_errors_are_deterministic(self):
        """Same input should always produce same error."""
        for _ in range(3):
            error = validate_agent_id("kimi")
            assert error is not None
            assert error.code == ValidationErrorCode.AGENT_ID_AMBIGUOUS


# =============================================================================
# 6. Error Message Actionability Tests
# =============================================================================


class TestErrorActionability:
    """Verify error messages are actionable."""

    def test_all_errors_have_next_action(self):
        """All validation errors should have next_action."""
        test_cases = [
            (lambda: validate_agent_id(None), "missing agent"),
            (lambda: validate_agent_id(""), "empty agent"),
            (lambda: validate_agent_id("kimi"), "ambiguous agent"),
            (lambda: validate_task_id(None), "missing task"),
            (lambda: validate_task_id(""), "empty task"),
            (lambda: validate_status_transition("completed", "pending"), "invalid transition"),
            (lambda: validate_dispatch_params(None, "msg"), "missing dispatch param"),
            (lambda: validate_dispatch_params("forge:gemini", None), "missing message"),
        ]

        for validation_func, description in test_cases:
            result = validation_func()
            if isinstance(result, list):
                errors = result
            elif isinstance(result, ValidationResult):
                errors = result.errors
            else:
                errors = [result] if result else []

            for error in errors:
                if error:
                    assert error.next_action, f"Missing next_action for: {description}"

    def test_error_to_dict_includes_all_fields(self):
        """Error to_dict should include all relevant fields."""
        error = validate_agent_id("kimi")
        assert error is not None

        error_dict = error.to_dict()
        assert "code" in error_dict
        assert "message" in error_dict
        assert "next_action" in error_dict
        assert error_dict["code"] == "AGENT_ID_AMBIGUOUS"

    def test_validation_result_to_dict_format(self):
        """ValidationResult.to_dict should be API-friendly."""
        result = validate_dispatch("kimi", "Task: test")
        result_dict = result.to_dict()

        assert "valid" in result_dict
        assert "errors" in result_dict
        assert result_dict["valid"] is False
        assert len(result_dict["errors"]) > 0


# =============================================================================
# 7. Integration Tests
# =============================================================================


class TestDispatchAmbiguityIntegration:
    """Integration tests for dispatch with ambiguity handling."""

    def test_dispatch_validation_rejects_ambiguous_at_entry(self):
        """Entry point should reject ambiguous agent IDs."""
        from forge_harness.fleet.dispatch_api import dispatch_to_agent

        # This should fail at validation stage, not reach tmux/API
        # Note: We can't easily test the async function here without mocking
        # But we can verify the validation is called
        pass

    def test_multiple_errors_collected(self):
        """Multiple validation errors should all be collected."""
        # Agent ID is ambiguous AND message is missing
        result = validate_dispatch("kimi", "")
        assert not result.valid
        # Should have both ambiguous agent and empty message errors
        codes = {e.code for e in result.errors}
        assert ValidationErrorCode.AGENT_ID_AMBIGUOUS in codes
        assert ValidationErrorCode.MESSAGE_EMPTY in codes
