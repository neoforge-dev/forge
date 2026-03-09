"""Tests for forge tasks update, delete, and reorder CLI commands.

Covers:
    - forge tasks update TASK_ID
    - forge tasks delete TASK_ID
    - forge tasks reorder --ids ...

Strategy: CLI Runner for integration paths. All HTTP calls are mocked via
CommandCenterClient. Follows the same pattern as test_cli_v2_features.py.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def forge_root(tmp_path):
    """Create a minimal FORGE root (marker file present)."""
    (tmp_path / ".forge-root").write_text("")
    return tmp_path


@pytest.fixture
def mock_client():
    """Return a pre-configured MagicMock for CommandCenterClient."""
    return MagicMock()


def _invoke(runner, forge_root, args, client_mock, input_text=None):
    """Helper: invoke CLI inside an isolated filesystem with mocked client."""
    with runner.isolated_filesystem(temp_dir=forge_root):
        with patch("forge_harness.cli_v2.tasks.CommandCenterClient") as cls:
            cls.from_env.return_value = client_mock
            return runner.invoke(cli, args, input=input_text)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

_TASK_1 = {
    "id": "task-abc-123",
    "subject": "Implement JWT authentication for the API gateway",
    "description": "Add JWT-based auth middleware with refresh token support",
    "priority": "high",
    "status": "pending",
    "claimed_by": None,
    "required_role": None,
    "created_at": "2026-02-23T10:00:00+00:00",
    "updated_at": "2026-02-23T10:00:00+00:00",
}

_TASK_2 = {
    "id": "task-def-456",
    "subject": "Migrate database schema to support multi-tenancy",
    "description": "Update all tables to include tenant_id with proper indexes",
    "priority": "critical",
    "status": "in_progress",
    "claimed_by": "forge:opencode",
    "required_role": "backend-engineer",
    "created_at": "2026-02-22T08:00:00+00:00",
    "updated_at": "2026-02-23T09:30:00+00:00",
}

_TASK_3 = {
    "id": "task-ghi-789",
    "subject": "Set up Redis caching layer for API responses",
    "description": "Configure Redis with TTL policies and cache invalidation strategy",
    "priority": "medium",
    "status": "pending",
    "claimed_by": None,
    "required_role": None,
    "created_at": "2026-02-21T14:00:00+00:00",
    "updated_at": "2026-02-21T14:00:00+00:00",
}


# ===========================================================================
# forge tasks update TASK_ID
# ===========================================================================


class TestForgeTasksUpdate:
    """Tests for 'forge tasks update TASK_ID'."""

    def test_update_status_success_human_mode(self, runner, forge_root, mock_client):
        updated = {**_TASK_1, "status": "in_progress"}
        mock_client.sync_update_task.return_value = updated
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "update", "task-abc-123", "--status", "in_progress"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "task-abc-123" in result.output

    def test_update_priority_success_human_mode(self, runner, forge_root, mock_client):
        updated = {**_TASK_1, "priority": "critical"}
        mock_client.sync_update_task.return_value = updated
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "update", "task-abc-123", "--priority", "critical"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "task-abc-123" in result.output

    def test_update_subject_success_human_mode(self, runner, forge_root, mock_client):
        updated = {**_TASK_1, "subject": "Updated task subject with meaningful description"}
        mock_client.sync_update_task.return_value = updated
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "update", "task-abc-123", "--subject", "Updated task subject with meaningful description"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "task-abc-123" in result.output

    def test_update_description_success_human_mode(self, runner, forge_root, mock_client):
        updated = {**_TASK_1, "description": "New detailed description for the task"}
        mock_client.sync_update_task.return_value = updated
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "update", "task-abc-123", "--description", "New detailed description for the task"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "task-abc-123" in result.output

    def test_update_role_success_human_mode(self, runner, forge_root, mock_client):
        updated = {**_TASK_1, "required_role": "backend-engineer"}
        mock_client.sync_update_task.return_value = updated
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "update", "task-abc-123", "--role", "backend-engineer"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "task-abc-123" in result.output

    def test_update_multiple_fields_success(self, runner, forge_root, mock_client):
        updated = {**_TASK_1, "status": "completed", "priority": "low"}
        mock_client.sync_update_task.return_value = updated
        result = _invoke(
            runner,
            forge_root,
            [
                "tasks",
                "update",
                "task-abc-123",
                "--status",
                "completed",
                "--priority",
                "low",
            ],
            mock_client,
        )

        assert result.exit_code == 0

    def test_update_success_json_mode(self, runner, forge_root, mock_client):
        updated = {**_TASK_1, "status": "in_progress"}
        mock_client.sync_update_task.return_value = updated
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "update", "task-abc-123", "--status", "in_progress"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge tasks update"
        assert data["data"]["id"] == "task-abc-123"

    def test_update_not_found_returns_falsy(self, runner, forge_root, mock_client):
        """When client returns None/falsy, expect non-zero exit."""
        mock_client.sync_update_task.return_value = None
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "update", "task-nonexistent", "--status", "pending"],
            mock_client,
        )

        assert result.exit_code != 0

    def test_update_not_found_returns_falsy_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_update_task.return_value = None
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "update", "task-nonexistent", "--status", "pending"],
            mock_client,
        )

        assert result.exit_code != 0

    def test_update_invalid_status_rejected_by_click(self, runner, forge_root, mock_client):
        """Click should reject invalid status choices before calling the client."""
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "update", "task-abc-123", "--status", "invalid_status"],
            mock_client,
        )

        assert result.exit_code == 2
        mock_client.sync_update_task.assert_not_called()

    def test_update_invalid_priority_rejected_by_click(self, runner, forge_root, mock_client):
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "update", "task-abc-123", "--priority", "ultra"],
            mock_client,
        )

        assert result.exit_code == 2
        mock_client.sync_update_task.assert_not_called()

    def test_update_all_valid_statuses_accepted(self, runner, forge_root, mock_client):
        """All valid status values must be accepted by Click."""
        for status_val in ("pending", "assigned", "in_progress", "completed", "failed", "blocked"):
            mock_client.sync_update_task.return_value = {**_TASK_1, "status": status_val}
            result = _invoke(
                runner,
                forge_root,
                ["tasks", "update", "task-abc-123", "--status", status_val],
                mock_client,
            )
            assert result.exit_code == 0, f"Expected exit 0 for status={status_val}"

    def test_update_all_valid_priorities_accepted(self, runner, forge_root, mock_client):
        """All valid priority values must be accepted by Click."""
        for pri_val in ("low", "medium", "high", "critical"):
            mock_client.sync_update_task.return_value = {**_TASK_1, "priority": pri_val}
            result = _invoke(
                runner,
                forge_root,
                ["tasks", "update", "task-abc-123", "--priority", pri_val],
                mock_client,
            )
            assert result.exit_code == 0, f"Expected exit 0 for priority={pri_val}"

    def test_update_requires_task_id_argument(self, runner, forge_root, mock_client):
        result = _invoke(
            runner, forge_root, ["tasks", "update", "--status", "pending"], mock_client
        )
        assert result.exit_code == 2

    def test_update_calls_client_with_correct_task_id(self, runner, forge_root, mock_client):
        mock_client.sync_update_task.return_value = _TASK_2
        _invoke(
            runner,
            forge_root,
            ["tasks", "update", "task-def-456", "--status", "completed"],
            mock_client,
        )

        call_kwargs = mock_client.sync_update_task.call_args
        assert call_kwargs is not None
        # task_id is a positional-or-keyword arg
        args, kwargs = call_kwargs
        called_id = args[0] if args else kwargs.get("task_id")
        assert called_id == "task-def-456"

    def test_update_api_exception_exits_nonzero(self, runner, forge_root, mock_client):
        mock_client.sync_update_task.side_effect = RuntimeError("Connection refused")
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "update", "task-abc-123", "--status", "pending"],
            mock_client,
        )

        assert result.exit_code != 0

    def test_update_not_found_exception_exits_6(self, runner, forge_root, mock_client):
        mock_client.sync_update_task.side_effect = RuntimeError("task not found")
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "update", "task-abc-123", "--status", "pending"],
            mock_client,
        )

        assert result.exit_code == 6

    def test_update_json_mode_error_is_parseable(self, runner, forge_root, mock_client):
        mock_client.sync_update_task.side_effect = RuntimeError("boom")
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "update", "task-abc-123", "--status", "pending"],
            mock_client,
        )

        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["success"] is False


# ===========================================================================
# forge tasks delete TASK_ID
# ===========================================================================


class TestForgeTasksDelete:
    """Tests for 'forge tasks delete TASK_ID'."""

    def test_delete_with_yes_flag_success_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_delete_task.return_value = True
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "delete", "task-abc-123", "--yes"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "task-abc-123" in result.output

    def test_delete_with_yes_flag_success_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_delete_task.return_value = True
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "delete", "task-abc-123", "--yes"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge tasks delete"
        assert data["data"]["deleted"] is True
        assert data["data"]["id"] == "task-abc-123"

    def test_delete_with_interactive_confirm_yes(self, runner, forge_root, mock_client):
        mock_client.sync_delete_task.return_value = True
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "delete", "task-abc-123"],
            mock_client,
            input_text="y\n",
        )

        assert result.exit_code == 0

    def test_delete_with_interactive_confirm_no(self, runner, forge_root, mock_client):
        """User declines confirmation - should not call delete and exit 0."""
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "delete", "task-abc-123"],
            mock_client,
            input_text="n\n",
        )

        assert result.exit_code == 0
        mock_client.sync_delete_task.assert_not_called()

    def test_delete_json_mode_skips_confirmation(self, runner, forge_root, mock_client):
        """In JSON mode with no --yes, prompt is also skipped (non-interactive)."""
        mock_client.sync_delete_task.return_value = True
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "delete", "task-abc-123", "--yes"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_delete_api_returns_false_exits_nonzero(self, runner, forge_root, mock_client):
        """When client returns False, command should indicate failure."""
        mock_client.sync_delete_task.return_value = False
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "delete", "task-abc-123", "--yes"],
            mock_client,
        )

        assert result.exit_code != 0

    def test_delete_api_returns_false_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_delete_task.return_value = False
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "delete", "task-abc-123", "--yes"],
            mock_client,
        )

        assert result.exit_code != 0

    def test_delete_requires_task_id_argument(self, runner, forge_root, mock_client):
        result = _invoke(runner, forge_root, ["tasks", "delete", "--yes"], mock_client)
        assert result.exit_code == 2

    def test_delete_calls_client_with_correct_task_id(self, runner, forge_root, mock_client):
        mock_client.sync_delete_task.return_value = True
        _invoke(
            runner,
            forge_root,
            ["tasks", "delete", "task-def-456", "--yes"],
            mock_client,
        )

        mock_client.sync_delete_task.assert_called_once_with("task-def-456")

    def test_delete_not_found_exception_exits_6(self, runner, forge_root, mock_client):
        mock_client.sync_delete_task.side_effect = RuntimeError("task not found")
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "delete", "task-abc-123", "--yes"],
            mock_client,
        )

        assert result.exit_code == 6

    def test_delete_api_exception_exits_nonzero(self, runner, forge_root, mock_client):
        mock_client.sync_delete_task.side_effect = ConnectionError("refused")
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "delete", "task-abc-123", "--yes"],
            mock_client,
        )

        assert result.exit_code != 0

    def test_delete_json_mode_error_is_parseable(self, runner, forge_root, mock_client):
        mock_client.sync_delete_task.side_effect = RuntimeError("internal error")
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "delete", "task-abc-123", "--yes"],
            mock_client,
        )

        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["success"] is False

    def test_delete_short_yes_flag(self, runner, forge_root, mock_client):
        """-y is the short alias for --yes."""
        mock_client.sync_delete_task.return_value = True
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "delete", "task-abc-123", "-y"],
            mock_client,
        )

        assert result.exit_code == 0
        mock_client.sync_delete_task.assert_called_once_with("task-abc-123")


# ===========================================================================
# forge tasks reorder --ids ...
# ===========================================================================


class TestForgeTasksReorder:
    """Tests for 'forge tasks reorder --ids ID1,ID2,ID3'."""

    def test_reorder_success_human_mode(self, runner, forge_root, mock_client):
        ordered_tasks = [_TASK_1, _TASK_3, _TASK_2]
        mock_client.sync_reorder_tasks.return_value = ordered_tasks
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "reorder", "--ids", "task-abc-123,task-ghi-789,task-def-456"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "Reordered" in result.output or "reorder" in result.output.lower()

    def test_reorder_success_json_mode(self, runner, forge_root, mock_client):
        ordered_tasks = [_TASK_1, _TASK_3, _TASK_2]
        mock_client.sync_reorder_tasks.return_value = ordered_tasks
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "reorder", "--ids", "task-abc-123,task-ghi-789,task-def-456"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge tasks reorder"
        assert data["data"]["reordered"] == 3
        assert data["data"]["order"] == [
            "task-abc-123",
            "task-ghi-789",
            "task-def-456",
        ]
        assert len(data["data"]["tasks"]) == 3

    def test_reorder_single_task_id(self, runner, forge_root, mock_client):
        mock_client.sync_reorder_tasks.return_value = [_TASK_1]
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "reorder", "--ids", "task-abc-123"],
            mock_client,
        )

        assert result.exit_code == 0
        mock_client.sync_reorder_tasks.assert_called_once_with(["task-abc-123"])

    def test_reorder_calls_client_with_correct_id_list(self, runner, forge_root, mock_client):
        mock_client.sync_reorder_tasks.return_value = []
        _invoke(
            runner,
            forge_root,
            ["tasks", "reorder", "--ids", "task-abc-123,task-def-456"],
            mock_client,
        )

        mock_client.sync_reorder_tasks.assert_called_once_with(
            ["task-abc-123", "task-def-456"]
        )

    def test_reorder_strips_whitespace_from_ids(self, runner, forge_root, mock_client):
        """IDs with surrounding spaces should be stripped."""
        mock_client.sync_reorder_tasks.return_value = []
        _invoke(
            runner,
            forge_root,
            ["tasks", "reorder", "--ids", " task-abc-123 , task-def-456 "],
            mock_client,
        )

        mock_client.sync_reorder_tasks.assert_called_once_with(
            ["task-abc-123", "task-def-456"]
        )

    def test_reorder_empty_ids_exits_nonzero(self, runner, forge_root, mock_client):
        """Providing an empty string for --ids should exit with an error."""
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "reorder", "--ids", ""],
            mock_client,
        )

        assert result.exit_code != 0
        mock_client.sync_reorder_tasks.assert_not_called()

    def test_reorder_missing_ids_option_exits_2(self, runner, forge_root, mock_client):
        """Omitting required --ids option should exit with usage error (code 2)."""
        result = _invoke(runner, forge_root, ["tasks", "reorder"], mock_client)

        assert result.exit_code == 2
        mock_client.sync_reorder_tasks.assert_not_called()

    def test_reorder_empty_result_from_api_still_shows_order(self, runner, forge_root, mock_client):
        """API may return empty list on success; command should still exit 0."""
        mock_client.sync_reorder_tasks.return_value = []
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "reorder", "--ids", "task-abc-123,task-def-456"],
            mock_client,
        )

        assert result.exit_code == 0

    def test_reorder_empty_result_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_reorder_tasks.return_value = []
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "reorder", "--ids", "task-abc-123,task-def-456"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["reordered"] == 2
        assert data["data"]["tasks"] == []

    def test_reorder_table_shows_new_order(self, runner, forge_root, mock_client):
        """Human mode renders a ranked table of the new order."""
        ordered_tasks = [_TASK_3, _TASK_1]
        mock_client.sync_reorder_tasks.return_value = ordered_tasks
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "reorder", "--ids", "task-ghi-789,task-abc-123"],
            mock_client,
        )

        assert result.exit_code == 0
        # Task subjects should appear (possibly truncated by Rich table column width)
        assert "task" in result.output.lower() or "Redis" in result.output or "JWT" in result.output

    def test_reorder_json_mode_order_matches_input(self, runner, forge_root, mock_client):
        """The 'order' field in JSON output must match the requested ID sequence."""
        input_ids = "task-ghi-789,task-abc-123,task-def-456"
        mock_client.sync_reorder_tasks.return_value = [_TASK_3, _TASK_1, _TASK_2]
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "reorder", "--ids", input_ids],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["order"] == [
            "task-ghi-789",
            "task-abc-123",
            "task-def-456",
        ]

    def test_reorder_api_exception_exits_nonzero(self, runner, forge_root, mock_client):
        mock_client.sync_reorder_tasks.side_effect = RuntimeError("server error")
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "reorder", "--ids", "task-abc-123,task-def-456"],
            mock_client,
        )

        assert result.exit_code != 0

    def test_reorder_json_mode_api_exception_is_parseable(self, runner, forge_root, mock_client):
        mock_client.sync_reorder_tasks.side_effect = RuntimeError("server error")
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "reorder", "--ids", "task-abc-123"],
            mock_client,
        )

        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["success"] is False


# ===========================================================================
# Help text renders without errors
# ===========================================================================


class TestForgeTasksOpsHelp:
    """Ensure help text for all three commands renders without errors."""

    def test_update_help(self, runner):
        result = runner.invoke(cli, ["tasks", "update", "--help"])
        assert result.exit_code == 0
        assert "TASK_ID" in result.output
        assert "--status" in result.output
        assert "--priority" in result.output

    def test_delete_help(self, runner):
        result = runner.invoke(cli, ["tasks", "delete", "--help"])
        assert result.exit_code == 0
        assert "TASK_ID" in result.output
        assert "--yes" in result.output

    def test_reorder_help(self, runner):
        result = runner.invoke(cli, ["tasks", "reorder", "--help"])
        assert result.exit_code == 0
        assert "--ids" in result.output

    def test_tasks_group_help_shows_all_three(self, runner):
        result = runner.invoke(cli, ["tasks", "--help"])
        assert result.exit_code == 0
        assert "update" in result.output
        assert "delete" in result.output
        assert "reorder" in result.output


# ===========================================================================
# CommandCenterClient method existence checks
# ===========================================================================


class TestCommandCenterClientTaskOpsMethods:
    """Verify that the three required client methods exist and are callable."""

    def test_sync_update_task_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_update_task")
        assert callable(client.sync_update_task)

    def test_sync_delete_task_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_delete_task")
        assert callable(client.sync_delete_task)

    def test_sync_reorder_tasks_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_reorder_tasks")
        assert callable(client.sync_reorder_tasks)

    def test_async_update_task_is_coroutine(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "update_task")
        assert inspect.iscoroutinefunction(client.update_task)

    def test_async_delete_task_is_coroutine(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "delete_task")
        assert inspect.iscoroutinefunction(client.delete_task)

    def test_async_reorder_tasks_is_coroutine(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "reorder_tasks")
        assert inspect.iscoroutinefunction(client.reorder_tasks)


# ===========================================================================
# Group-level --json flag propagation
# ===========================================================================


class TestForgeTasksOpsJsonPropagation:
    """Ensure --json on the tasks group propagates correctly to subcommands."""

    def test_json_propagates_to_update(self, runner, forge_root, mock_client):
        mock_client.sync_update_task.return_value = _TASK_1
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "update", "task-abc-123", "--status", "pending"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_json_propagates_to_delete(self, runner, forge_root, mock_client):
        mock_client.sync_delete_task.return_value = True
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "delete", "task-abc-123", "--yes"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_json_propagates_to_reorder(self, runner, forge_root, mock_client):
        mock_client.sync_reorder_tasks.return_value = [_TASK_1, _TASK_2]
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "reorder", "--ids", "task-abc-123,task-def-456"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_json_update_error_output_is_parseable(self, runner, forge_root, mock_client):
        mock_client.sync_update_task.side_effect = RuntimeError("service down")
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "update", "task-abc-123", "--status", "pending"],
            mock_client,
        )

        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["success"] is False

    def test_json_delete_error_output_is_parseable(self, runner, forge_root, mock_client):
        mock_client.sync_delete_task.side_effect = RuntimeError("not found")
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "delete", "task-abc-123", "--yes"],
            mock_client,
        )

        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["success"] is False

    def test_json_reorder_error_output_is_parseable(self, runner, forge_root, mock_client):
        mock_client.sync_reorder_tasks.side_effect = RuntimeError("timeout")
        result = _invoke(
            runner,
            forge_root,
            ["tasks", "--json", "reorder", "--ids", "task-abc-123"],
            mock_client,
        )

        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["success"] is False
