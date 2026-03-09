"""Tests for forge decompose CLI commands.

Covers all commands in forge_harness/cli_v2/decomposition.py:
    - list_graphs
    - show_graph
    - create_graph
    - check_ready
    - create_subtasks
    - complete_subtask

Strategy: CLI Runner for integration paths with all HTTP calls mocked via
CommandCenterClient. Uses isolated_filesystem to avoid real filesystem
side-effects.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli

# ---------------------------------------------------------------------------
# Fixtures
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
        with patch("forge_harness.cli_v2.decomposition.CommandCenterClient") as cls:
            cls.from_env.return_value = client_mock
            return runner.invoke(cli, args, input=input_text)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

_SUBTASK_1 = {
    "id": "subtask-001",
    "title": "Set up database migrations",
    "status": "pending",
    "order": 1,
}

_SUBTASK_2 = {
    "id": "subtask-002",
    "title": "Implement auth endpoints",
    "status": "completed",
    "order": 2,
}

_GRAPH_1 = {
    "id": "graph-abc123",
    "task_id": "task-xyz789",
    "status": "in_progress",
    "description": "Build authentication module for interview simulator",
    "subtasks": [_SUBTASK_1, _SUBTASK_2],
    "created_at": "2026-02-23T09:00:00+00:00",
    "updated_at": "2026-02-23T10:30:00+00:00",
}

_GRAPH_2 = {
    "id": "graph-def456",
    "task_id": "task-qqq111",
    "status": "done",
    "description": "Migrate database schema to v2",
    "subtasks": [],
    "created_at": "2026-02-22T08:00:00+00:00",
    "updated_at": "2026-02-22T16:00:00+00:00",
}

_GRAPHS_LIST = [_GRAPH_1, _GRAPH_2]

_READY_RESPONSE = {"ready": True, "reason": "All dependencies met"}
_NOT_READY_RESPONSE = {"ready": False, "reason": "Subtask 001 has unresolved dependencies"}

_SUBTASKS_CREATED = {
    "subtasks": [_SUBTASK_1, _SUBTASK_2],
    "count": 2,
    "graph_id": "graph-abc123",
}

_COMPLETE_RESPONSE = {**_SUBTASK_1, "status": "completed"}


# ===========================================================================
# forge decompose list
# ===========================================================================


class TestForgeDecomposeList:
    """Tests for 'forge decompose list'."""

    def test_list_empty_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_decomposition_graphs.return_value = []
        result = _invoke(runner, forge_root, ["decompose", "list"], mock_client)

        assert result.exit_code == 0
        assert "No decomposition graphs found" in result.output

    def test_list_empty_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_decomposition_graphs.return_value = []
        result = _invoke(runner, forge_root, ["decompose", "--json", "list"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge decompose list"
        assert data["data"] == []

    def test_list_with_graphs_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_decomposition_graphs.return_value = _GRAPHS_LIST
        result = _invoke(runner, forge_root, ["decompose", "list"], mock_client)

        assert result.exit_code == 0
        assert "graph-abc123" in result.output
        assert "task-xyz789" in result.output
        assert "in_progress" in result.output

    def test_list_with_graphs_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_decomposition_graphs.return_value = _GRAPHS_LIST
        result = _invoke(runner, forge_root, ["decompose", "--json", "list"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert len(data["data"]) == 2
        assert data["data"][0]["id"] == "graph-abc123"

    def test_list_shows_subtask_count(self, runner, forge_root, mock_client):
        mock_client.sync_list_decomposition_graphs.return_value = _GRAPHS_LIST
        result = _invoke(runner, forge_root, ["decompose", "list"], mock_client)

        assert result.exit_code == 0
        assert "2" in result.output  # graph-abc123 has 2 subtasks

    def test_list_with_limit_flag(self, runner, forge_root, mock_client):
        mock_client.sync_list_decomposition_graphs.return_value = [_GRAPH_1]
        result = _invoke(
            runner, forge_root, ["decompose", "list", "--limit", "10"], mock_client
        )

        assert result.exit_code == 0
        mock_client.sync_list_decomposition_graphs.assert_called_once_with(limit=10)

    def test_list_default_limit_is_50(self, runner, forge_root, mock_client):
        mock_client.sync_list_decomposition_graphs.return_value = []
        _invoke(runner, forge_root, ["decompose", "list"], mock_client)

        mock_client.sync_list_decomposition_graphs.assert_called_once_with(limit=50)

    def test_list_api_error_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_list_decomposition_graphs.side_effect = RuntimeError("API down")
        result = _invoke(runner, forge_root, ["decompose", "list"], mock_client)

        assert result.exit_code == 1

    def test_list_api_error_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_decomposition_graphs.side_effect = RuntimeError("boom")
        result = _invoke(runner, forge_root, ["decompose", "--json", "list"], mock_client)

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False


# ===========================================================================
# forge decompose show
# ===========================================================================


class TestForgeDecomposeShow:
    """Tests for 'forge decompose show GRAPH_ID'."""

    def test_show_existing_graph_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_decomposition_graph.return_value = _GRAPH_1
        result = _invoke(
            runner, forge_root, ["decompose", "show", "graph-abc123"], mock_client
        )

        assert result.exit_code == 0
        assert "graph-abc123" in result.output
        assert "task-xyz789" in result.output
        assert "in_progress" in result.output
        assert "Build authentication" in result.output

    def test_show_existing_graph_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_decomposition_graph.return_value = _GRAPH_1
        result = _invoke(
            runner, forge_root, ["decompose", "--json", "show", "graph-abc123"], mock_client
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge decompose show"
        assert data["data"]["id"] == "graph-abc123"

    def test_show_not_found_exits_6(self, runner, forge_root, mock_client):
        mock_client.sync_get_decomposition_graph.return_value = None
        result = _invoke(
            runner, forge_root, ["decompose", "show", "nonexistent"], mock_client
        )

        assert result.exit_code == 6

    def test_show_not_found_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_decomposition_graph.return_value = None
        result = _invoke(
            runner, forge_root, ["decompose", "--json", "show", "nonexistent"], mock_client
        )

        assert result.exit_code == 6
        data = json.loads(result.output)
        assert data["success"] is False

    def test_show_calls_correct_id(self, runner, forge_root, mock_client):
        mock_client.sync_get_decomposition_graph.return_value = _GRAPH_1
        _invoke(runner, forge_root, ["decompose", "show", "graph-abc123"], mock_client)

        mock_client.sync_get_decomposition_graph.assert_called_once_with("graph-abc123")

    def test_show_displays_subtasks_table(self, runner, forge_root, mock_client):
        mock_client.sync_get_decomposition_graph.return_value = _GRAPH_1
        result = _invoke(
            runner, forge_root, ["decompose", "show", "graph-abc123"], mock_client
        )

        assert result.exit_code == 0
        assert "subtask-001" in result.output
        assert "Set up database migrations" in result.output
        assert "subtask-002" in result.output

    def test_show_no_subtasks_shows_none(self, runner, forge_root, mock_client):
        mock_client.sync_get_decomposition_graph.return_value = _GRAPH_2
        result = _invoke(
            runner, forge_root, ["decompose", "show", "graph-def456"], mock_client
        )

        assert result.exit_code == 0
        assert "None defined" in result.output

    def test_show_completed_subtask_highlighted(self, runner, forge_root, mock_client):
        """Completed subtask status appears in output."""
        mock_client.sync_get_decomposition_graph.return_value = _GRAPH_1
        result = _invoke(
            runner, forge_root, ["decompose", "show", "graph-abc123"], mock_client
        )

        assert result.exit_code == 0
        assert "completed" in result.output

    def test_show_requires_graph_id_argument(self, runner, forge_root, mock_client):
        result = _invoke(runner, forge_root, ["decompose", "show"], mock_client)
        assert result.exit_code == 2

    def test_show_exception_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_get_decomposition_graph.side_effect = ConnectionError("timeout")
        result = _invoke(
            runner, forge_root, ["decompose", "show", "graph-abc123"], mock_client
        )

        assert result.exit_code == 1


# ===========================================================================
# forge decompose create
# ===========================================================================


class TestForgeDecomposeCreate:
    """Tests for 'forge decompose create'."""

    def test_create_with_task_id_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_create_decomposition_graph.return_value = _GRAPH_1
        result = _invoke(
            runner,
            forge_root,
            ["decompose", "create", "--task-id", "task-xyz789"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "graph-abc123" in result.output

    def test_create_with_description_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_create_decomposition_graph.return_value = _GRAPH_1
        result = _invoke(
            runner,
            forge_root,
            ["decompose", "create", "--description", "Build auth module"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "graph-abc123" in result.output

    def test_create_with_both_options_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_create_decomposition_graph.return_value = _GRAPH_1
        result = _invoke(
            runner,
            forge_root,
            [
                "decompose",
                "create",
                "--task-id",
                "task-xyz789",
                "--description",
                "Build auth module",
            ],
            mock_client,
        )

        assert result.exit_code == 0
        mock_client.sync_create_decomposition_graph.assert_called_once_with(
            task_id="task-xyz789",
            description="Build auth module",
        )

    def test_create_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_create_decomposition_graph.return_value = _GRAPH_1
        result = _invoke(
            runner,
            forge_root,
            ["decompose", "--json", "create", "--task-id", "task-xyz789"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge decompose create"
        assert data["data"]["id"] == "graph-abc123"

    def test_create_no_options_exits_non_zero(self, runner, forge_root, mock_client):
        """create with no options should exit with a non-zero code."""
        result = _invoke(runner, forge_root, ["decompose", "create"], mock_client)

        assert result.exit_code != 0
        mock_client.sync_create_decomposition_graph.assert_not_called()

    def test_create_api_failure_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_create_decomposition_graph.return_value = None
        result = _invoke(
            runner,
            forge_root,
            ["decompose", "create", "--task-id", "task-xyz789"],
            mock_client,
        )

        assert result.exit_code == 1

    def test_create_api_failure_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_create_decomposition_graph.return_value = None
        result = _invoke(
            runner,
            forge_root,
            ["decompose", "--json", "create", "--task-id", "task-xyz789"],
            mock_client,
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False

    def test_create_exception_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_create_decomposition_graph.side_effect = RuntimeError("API down")
        result = _invoke(
            runner,
            forge_root,
            ["decompose", "create", "--task-id", "task-xyz789"],
            mock_client,
        )

        assert result.exit_code == 1


# ===========================================================================
# forge decompose ready
# ===========================================================================


class TestForgeDecomposeReady:
    """Tests for 'forge decompose ready GRAPH_ID'."""

    def test_ready_true_exits_0(self, runner, forge_root, mock_client):
        mock_client.sync_check_decomposition_ready.return_value = _READY_RESPONSE
        result = _invoke(
            runner, forge_root, ["decompose", "ready", "graph-abc123"], mock_client
        )

        assert result.exit_code == 0
        assert "ready" in result.output.lower()

    def test_ready_false_exits_3(self, runner, forge_root, mock_client):
        mock_client.sync_check_decomposition_ready.return_value = _NOT_READY_RESPONSE
        result = _invoke(
            runner, forge_root, ["decompose", "ready", "graph-abc123"], mock_client
        )

        assert result.exit_code == 3
        assert "Not ready" in result.output

    def test_ready_shows_reason_when_not_ready(self, runner, forge_root, mock_client):
        mock_client.sync_check_decomposition_ready.return_value = _NOT_READY_RESPONSE
        result = _invoke(
            runner, forge_root, ["decompose", "ready", "graph-abc123"], mock_client
        )

        assert "unresolved dependencies" in result.output

    def test_ready_not_found_exits_6(self, runner, forge_root, mock_client):
        mock_client.sync_check_decomposition_ready.return_value = None
        result = _invoke(
            runner, forge_root, ["decompose", "ready", "nonexistent"], mock_client
        )

        assert result.exit_code == 6

    def test_ready_json_mode_ready_true(self, runner, forge_root, mock_client):
        mock_client.sync_check_decomposition_ready.return_value = _READY_RESPONSE
        result = _invoke(
            runner, forge_root, ["decompose", "--json", "ready", "graph-abc123"], mock_client
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge decompose ready"
        assert data["data"]["ready"] is True

    def test_ready_json_mode_ready_false(self, runner, forge_root, mock_client):
        mock_client.sync_check_decomposition_ready.return_value = _NOT_READY_RESPONSE
        result = _invoke(
            runner, forge_root, ["decompose", "--json", "ready", "graph-abc123"], mock_client
        )

        assert result.exit_code == 3
        data = json.loads(result.output)
        assert data["success"] is True  # API call succeeded; graph just isn't ready
        assert data["data"]["ready"] is False

    def test_ready_calls_correct_id(self, runner, forge_root, mock_client):
        mock_client.sync_check_decomposition_ready.return_value = _READY_RESPONSE
        _invoke(runner, forge_root, ["decompose", "ready", "graph-abc123"], mock_client)

        mock_client.sync_check_decomposition_ready.assert_called_once_with("graph-abc123")

    def test_ready_requires_graph_id(self, runner, forge_root, mock_client):
        result = _invoke(runner, forge_root, ["decompose", "ready"], mock_client)
        assert result.exit_code == 2

    def test_ready_exception_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_check_decomposition_ready.side_effect = RuntimeError("boom")
        result = _invoke(
            runner, forge_root, ["decompose", "ready", "graph-abc123"], mock_client
        )

        assert result.exit_code == 1


# ===========================================================================
# forge decompose subtasks
# ===========================================================================


class TestForgeDecomposeSubtasks:
    """Tests for 'forge decompose subtasks GRAPH_ID'."""

    def test_subtasks_success_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_create_decomposition_subtasks.return_value = _SUBTASKS_CREATED
        result = _invoke(
            runner, forge_root, ["decompose", "subtasks", "graph-abc123"], mock_client
        )

        assert result.exit_code == 0
        assert "2" in result.output
        assert "graph-abc123" in result.output

    def test_subtasks_success_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_create_decomposition_subtasks.return_value = _SUBTASKS_CREATED
        result = _invoke(
            runner,
            forge_root,
            ["decompose", "--json", "subtasks", "graph-abc123"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge decompose subtasks"
        assert len(data["data"]["subtasks"]) == 2

    def test_subtasks_failure_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_create_decomposition_subtasks.return_value = None
        result = _invoke(
            runner, forge_root, ["decompose", "subtasks", "graph-abc123"], mock_client
        )

        assert result.exit_code == 1

    def test_subtasks_failure_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_create_decomposition_subtasks.return_value = None
        result = _invoke(
            runner,
            forge_root,
            ["decompose", "--json", "subtasks", "graph-abc123"],
            mock_client,
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False

    def test_subtasks_calls_correct_graph_id(self, runner, forge_root, mock_client):
        mock_client.sync_create_decomposition_subtasks.return_value = _SUBTASKS_CREATED
        _invoke(runner, forge_root, ["decompose", "subtasks", "graph-abc123"], mock_client)

        mock_client.sync_create_decomposition_subtasks.assert_called_once_with("graph-abc123")

    def test_subtasks_requires_graph_id(self, runner, forge_root, mock_client):
        result = _invoke(runner, forge_root, ["decompose", "subtasks"], mock_client)
        assert result.exit_code == 2

    def test_subtasks_exception_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_create_decomposition_subtasks.side_effect = RuntimeError("API down")
        result = _invoke(
            runner, forge_root, ["decompose", "subtasks", "graph-abc123"], mock_client
        )

        assert result.exit_code == 1


# ===========================================================================
# forge decompose complete
# ===========================================================================


class TestForgeDecomposeComplete:
    """Tests for 'forge decompose complete GRAPH_ID SUBTASK_ID'."""

    def test_complete_success_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_complete_decomposition_subtask.return_value = _COMPLETE_RESPONSE
        result = _invoke(
            runner,
            forge_root,
            ["decompose", "complete", "graph-abc123", "subtask-001"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "subtask-001" in result.output
        assert "graph-abc123" in result.output

    def test_complete_success_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_complete_decomposition_subtask.return_value = _COMPLETE_RESPONSE
        result = _invoke(
            runner,
            forge_root,
            ["decompose", "--json", "complete", "graph-abc123", "subtask-001"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge decompose complete"
        assert data["data"]["status"] == "completed"

    def test_complete_not_found_exits_6(self, runner, forge_root, mock_client):
        mock_client.sync_complete_decomposition_subtask.return_value = None
        result = _invoke(
            runner,
            forge_root,
            ["decompose", "complete", "graph-abc123", "subtask-999"],
            mock_client,
        )

        assert result.exit_code == 6

    def test_complete_not_found_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_complete_decomposition_subtask.return_value = None
        result = _invoke(
            runner,
            forge_root,
            ["decompose", "--json", "complete", "graph-abc123", "subtask-999"],
            mock_client,
        )

        assert result.exit_code == 6
        data = json.loads(result.output)
        assert data["success"] is False

    def test_complete_calls_correct_args(self, runner, forge_root, mock_client):
        mock_client.sync_complete_decomposition_subtask.return_value = _COMPLETE_RESPONSE
        _invoke(
            runner,
            forge_root,
            ["decompose", "complete", "graph-abc123", "subtask-001"],
            mock_client,
        )

        mock_client.sync_complete_decomposition_subtask.assert_called_once_with(
            "graph-abc123", "subtask-001"
        )

    def test_complete_requires_graph_id(self, runner, forge_root, mock_client):
        result = _invoke(runner, forge_root, ["decompose", "complete"], mock_client)
        assert result.exit_code == 2

    def test_complete_requires_subtask_id(self, runner, forge_root, mock_client):
        result = _invoke(
            runner, forge_root, ["decompose", "complete", "graph-abc123"], mock_client
        )
        assert result.exit_code == 2

    def test_complete_exception_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_complete_decomposition_subtask.side_effect = RuntimeError("boom")
        result = _invoke(
            runner,
            forge_root,
            ["decompose", "complete", "graph-abc123", "subtask-001"],
            mock_client,
        )

        assert result.exit_code == 1


# ===========================================================================
# forge decompose --help
# ===========================================================================


class TestForgeDecomposeHelp:
    """Ensure help text renders without errors."""

    def test_decompose_group_help(self, runner):
        result = runner.invoke(cli, ["decompose", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "show" in result.output
        assert "create" in result.output
        assert "ready" in result.output
        assert "subtasks" in result.output
        assert "complete" in result.output

    def test_decompose_list_help(self, runner):
        result = runner.invoke(cli, ["decompose", "list", "--help"])
        assert result.exit_code == 0
        assert "--limit" in result.output

    def test_decompose_show_help(self, runner):
        result = runner.invoke(cli, ["decompose", "show", "--help"])
        assert result.exit_code == 0
        assert "GRAPH_ID" in result.output

    def test_decompose_create_help(self, runner):
        result = runner.invoke(cli, ["decompose", "create", "--help"])
        assert result.exit_code == 0
        assert "--task-id" in result.output
        assert "--description" in result.output

    def test_decompose_ready_help(self, runner):
        result = runner.invoke(cli, ["decompose", "ready", "--help"])
        assert result.exit_code == 0
        assert "GRAPH_ID" in result.output

    def test_decompose_subtasks_help(self, runner):
        result = runner.invoke(cli, ["decompose", "subtasks", "--help"])
        assert result.exit_code == 0
        assert "GRAPH_ID" in result.output

    def test_decompose_complete_help(self, runner):
        result = runner.invoke(cli, ["decompose", "complete", "--help"])
        assert result.exit_code == 0
        assert "GRAPH_ID" in result.output
        assert "SUBTASK_ID" in result.output


# ===========================================================================
# forge decompose --json propagation
# ===========================================================================


class TestForgeDecomposeJsonPropagation:
    """Ensure --json on the group propagates to subcommands."""

    def test_group_json_flag_propagates_to_list(self, runner, forge_root, mock_client):
        mock_client.sync_list_decomposition_graphs.return_value = _GRAPHS_LIST
        result = _invoke(runner, forge_root, ["decompose", "--json", "list"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_group_json_flag_propagates_to_show(self, runner, forge_root, mock_client):
        mock_client.sync_get_decomposition_graph.return_value = _GRAPH_1
        result = _invoke(
            runner, forge_root, ["decompose", "--json", "show", "graph-abc123"], mock_client
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_group_json_error_is_parseable(self, runner, forge_root, mock_client):
        mock_client.sync_list_decomposition_graphs.side_effect = RuntimeError("boom")
        result = _invoke(runner, forge_root, ["decompose", "--json", "list"], mock_client)

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False


# ===========================================================================
# CommandCenterClient decomposition methods (unit tests)
# ===========================================================================


class TestCommandCenterClientDecompositionMethods:
    """Unit tests verifying decomposition methods exist on CommandCenterClient."""

    def _client(self):
        from forge_harness.command_center_client import CommandCenterClient
        return CommandCenterClient(base_url="http://localhost:9999")

    def test_sync_list_decomposition_graphs_exists(self):
        client = self._client()
        assert hasattr(client, "sync_list_decomposition_graphs")
        assert callable(client.sync_list_decomposition_graphs)

    def test_sync_get_decomposition_graph_exists(self):
        client = self._client()
        assert hasattr(client, "sync_get_decomposition_graph")
        assert callable(client.sync_get_decomposition_graph)

    def test_sync_create_decomposition_graph_exists(self):
        client = self._client()
        assert hasattr(client, "sync_create_decomposition_graph")
        assert callable(client.sync_create_decomposition_graph)

    def test_sync_check_decomposition_ready_exists(self):
        client = self._client()
        assert hasattr(client, "sync_check_decomposition_ready")
        assert callable(client.sync_check_decomposition_ready)

    def test_sync_create_decomposition_subtasks_exists(self):
        client = self._client()
        assert hasattr(client, "sync_create_decomposition_subtasks")
        assert callable(client.sync_create_decomposition_subtasks)

    def test_sync_complete_decomposition_subtask_exists(self):
        client = self._client()
        assert hasattr(client, "sync_complete_decomposition_subtask")
        assert callable(client.sync_complete_decomposition_subtask)

    def test_async_list_decomposition_graphs_exists(self):
        import inspect
        client = self._client()
        assert hasattr(client, "list_decomposition_graphs")
        assert inspect.iscoroutinefunction(client.list_decomposition_graphs)

    def test_async_get_decomposition_graph_exists(self):
        import inspect
        client = self._client()
        assert hasattr(client, "get_decomposition_graph")
        assert inspect.iscoroutinefunction(client.get_decomposition_graph)

    def test_async_create_decomposition_graph_exists(self):
        import inspect
        client = self._client()
        assert hasattr(client, "create_decomposition_graph")
        assert inspect.iscoroutinefunction(client.create_decomposition_graph)

    def test_async_check_decomposition_ready_exists(self):
        import inspect
        client = self._client()
        assert hasattr(client, "check_decomposition_ready")
        assert inspect.iscoroutinefunction(client.check_decomposition_ready)

    def test_async_create_decomposition_subtasks_exists(self):
        import inspect
        client = self._client()
        assert hasattr(client, "create_decomposition_subtasks")
        assert inspect.iscoroutinefunction(client.create_decomposition_subtasks)

    def test_async_complete_decomposition_subtask_exists(self):
        import inspect
        client = self._client()
        assert hasattr(client, "complete_decomposition_subtask")
        assert inspect.iscoroutinefunction(client.complete_decomposition_subtask)
