"""Tests for cli_v2/exit_codes.py"""
from unittest.mock import MagicMock, patch

import pytest
from forge_harness.cli_v2.exit_codes import (
    EXIT_CODES_BASIC,
    EXIT_CODES_DISPATCH,
    EXIT_CODES_FLEET,
    EXIT_CODES_STANDARD,
    EXIT_CODES_TASKS,
    ExitCode,
    exit_with_code,
    get_exit_code_description,
)


class TestExitCode:
    """Tests for ExitCode enum."""

    def test_universal_codes(self):
        """Should have correct universal exit codes."""
        assert ExitCode.SUCCESS == 0
        assert ExitCode.GENERAL_ERROR == 1
        assert ExitCode.USAGE_ERROR == 2
        assert ExitCode.OPERATION_FAILED == 3
        assert ExitCode.SERVICE_UNAVAILABLE == 4
        assert ExitCode.PERMISSION_DENIED == 5
        assert ExitCode.NOT_FOUND == 6
        assert ExitCode.TIMEOUT == 7
        assert ExitCode.CONFIG_ERROR == 8
        assert ExitCode.VALIDATION_ERROR == 9
        assert ExitCode.STATE_CONFLICT == 10

    def test_task_specific_codes(self):
        """Should have correct task-specific codes."""
        assert ExitCode.TASK_ALREADY_CLAIMED == 11
        assert ExitCode.TASK_NOT_CLAIMABLE == 12
        assert ExitCode.TASK_VALIDATION_FAILED == 13

    def test_fleet_specific_codes(self):
        """Should have correct fleet-specific codes."""
        assert ExitCode.AGENT_NOT_FOUND == 11
        assert ExitCode.AGENT_NOT_RESPONSIVE == 12
        assert ExitCode.FLEET_OPERATION_FAILED == 13

    def test_exit_code_is_intenum(self):
        """ExitCode should be IntEnum (comparable to int)."""
        assert isinstance(ExitCode.SUCCESS, int)
        assert ExitCode.SUCCESS == 0
        assert ExitCode.GENERAL_ERROR != 0


class TestExitWithCode:
    """Tests for exit_with_code function."""

    def test_exit_success_without_message(self):
        """Should exit with code 0 and no message."""
        with pytest.raises(SystemExit) as exc_info:
            exit_with_code(ExitCode.SUCCESS)
        assert exc_info.value.code == 0

    def test_exit_success_with_message(self):
        """Should exit with code 0 and print success message."""
        with patch("click.style") as mock_style:
            mock_style.return_value = "✓ Success message"
            with patch("click.echo") as mock_echo:
                with pytest.raises(SystemExit) as exc_info:
                    exit_with_code(ExitCode.SUCCESS, "Success message")
                assert exc_info.value.code == 0
                mock_style.assert_called_once_with("✓ Success message", fg="green")

    def test_exit_error_with_message(self):
        """Should exit with error code and print error message."""
        with patch("click.style") as mock_style:
            mock_style.return_value = "✗ Error message"
            with patch("click.echo") as mock_echo:
                with pytest.raises(SystemExit) as exc_info:
                    exit_with_code(ExitCode.GENERAL_ERROR, "Error message")
                assert exc_info.value.code == 1
                mock_style.assert_called_once_with("✗ Error message", fg="red")
                mock_echo.assert_called_once_with("✗ Error message", err=True)

    def test_exit_without_message(self):
        """Should exit silently without message."""
        with pytest.raises(SystemExit) as exc_info:
            exit_with_code(ExitCode.NOT_FOUND)
        assert exc_info.value.code == 6


class TestGetExitCodeDescription:
    """Tests for get_exit_code_description function."""

    def test_universal_descriptions(self):
        """Should return correct universal descriptions."""
        assert get_exit_code_description(ExitCode.SUCCESS) == "Success"
        assert get_exit_code_description(ExitCode.GENERAL_ERROR) == "General error"
        assert get_exit_code_description(ExitCode.USAGE_ERROR) == "Usage error (invalid arguments)"
        assert get_exit_code_description(ExitCode.NOT_FOUND) == "Not found"
        assert get_exit_code_description(ExitCode.TIMEOUT) == "Timeout"

    def test_command_specific_tasks(self):
        """Should return task-specific descriptions."""
        assert "already claimed" in get_exit_code_description(ExitCode.TASK_ALREADY_CLAIMED, "tasks")
        assert "not claimable" in get_exit_code_description(ExitCode.TASK_NOT_CLAIMABLE, "tasks")

    def test_command_specific_fleet(self):
        """Should return fleet-specific descriptions."""
        assert "Agent not found" in get_exit_code_description(ExitCode.AGENT_NOT_FOUND, "fleet")
        assert "not responsive" in get_exit_code_description(ExitCode.AGENT_NOT_RESPONSIVE, "fleet")

    def test_command_specific_orchestrator(self):
        """Should return orchestrator-specific descriptions."""
        desc = get_exit_code_description(ExitCode.CHECKPOINT_NOT_FOUND, "orchestrator")
        assert "Checkpoint" in desc

    def test_command_specific_git(self):
        """Should return git-specific descriptions."""
        desc = get_exit_code_description(ExitCode.GIT_LOCK_PRESENT, "git")
        assert "lock" in desc.lower()

    def test_command_specific_dispatch(self):
        """Should return dispatch-specific descriptions."""
        desc = get_exit_code_description(ExitCode.DISPATCH_FAILED, "dispatch")
        assert "Dispatch" in desc

    def test_command_specific_claims(self):
        """Should return claims-specific descriptions."""
        desc = get_exit_code_description(ExitCode.CLAIM_VERIFICATION_FAILED, "claims")
        assert "verification" in desc.lower()

    def test_command_specific_loop(self):
        """Should return loop-specific descriptions."""
        desc = get_exit_code_description(ExitCode.LOOP_ALREADY_RUNNING, "loop")
        assert "already running" in desc.lower()

    def test_command_specific_health(self):
        """Should return health-specific descriptions."""
        desc = get_exit_code_description(ExitCode.AGENT_UNHEALTHY, "health")
        assert "unhealthy" in desc.lower()

    def test_command_specific_doctor(self):
        """Should return doctor-specific descriptions."""
        desc = get_exit_code_description(ExitCode.ISSUES_FOUND, "doctor")
        assert "issues" in desc.lower()

    def test_command_specific_ship(self):
        """Should return ship-specific descriptions."""
        desc = get_exit_code_description(ExitCode.DEPLOYMENT_FAILED, "ship")
        assert "deployment" in desc.lower()

    def test_unknown_code(self):
        """Should handle unknown command-specific code."""
        # Use a valid ExitCode value but check unknown command
        desc = get_exit_code_description(ExitCode.SUCCESS, "unknown_cmd")
        assert "Success" in desc  # Should fall back to universal description

    def test_no_command_with_valid_code(self):
        """Should return description for valid code without command."""
        desc = get_exit_code_description(ExitCode.SUCCESS)
        assert desc == "Success"


class TestExitCodeTemplates:
    """Tests for exit code template strings."""

    def test_exit_codes_basic_contains_success(self):
        """BASIC template should mention success."""
        assert "0  Success" in EXIT_CODES_BASIC

    def test_exit_codes_basic_contains_error(self):
        """BASIC template should mention error."""
        assert "1  General error" in EXIT_CODES_BASIC

    def test_exit_codes_standard_contains_service_unavailable(self):
        """STANDARD template should mention service unavailable."""
        assert "4  Service unavailable" in EXIT_CODES_STANDARD

    def test_exit_codes_tasks_contains_task_codes(self):
        """TASKS template should mention task-specific codes."""
        assert "11  Task already claimed" in EXIT_CODES_TASKS

    def test_exit_codes_fleet_contains_agent_codes(self):
        """FLEET template should mention agent-specific codes."""
        assert "11  Agent not found" in EXIT_CODES_FLEET

    def test_exit_codes_dispatch_contains_dispatch_codes(self):
        """DISPATCH template should mention dispatch-specific codes."""
        assert "11  Dispatch failed" in EXIT_CODES_DISPATCH
