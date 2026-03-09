"""Tests for forge memories CLI commands.

Covers all commands in forge_harness/cli_v2/memories.py:
    - list_memories
    - show_memory

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
        with patch("forge_harness.cli_v2.memories.CommandCenterClient") as cls:
            cls.from_env.return_value = client_mock
            return runner.invoke(cli, args, input=input_text)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

_MEMORY_1 = {
    "id": "mem-abc123",
    "key": "auth_pattern",
    "agent_id": "forge:opencode",
    "type": "pattern",
    "value": {"approach": "JWT with refresh tokens", "tested": True},
    "created_at": "2026-02-23T10:00:00+00:00",
    "updated_at": "2026-02-23T11:30:00+00:00",
}

_MEMORY_2 = {
    "id": "mem-def456",
    "key": "database_schema_v2",
    "agent_id": "forge:kilo",
    "type": "snapshot",
    "value": "users table updated with soft-delete column",
    "created_at": "2026-02-22T09:00:00+00:00",
    "updated_at": "2026-02-22T09:00:00+00:00",
}

_MEMORIES_LIST = [_MEMORY_1, _MEMORY_2]


# ===========================================================================
# forge memories list
# ===========================================================================


class TestForgeMemoriesList:
    """Tests for 'forge memories list'."""

    def test_list_empty_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_memories.return_value = []
        result = _invoke(runner, forge_root, ["memories", "list"], mock_client)

        assert result.exit_code == 0
        assert "No memories found" in result.output

    def test_list_empty_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_memories.return_value = []
        result = _invoke(runner, forge_root, ["memories", "--json", "list"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge memories list"
        assert data["data"] == []

    def test_list_with_memories_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_memories.return_value = _MEMORIES_LIST
        result = _invoke(runner, forge_root, ["memories", "list"], mock_client)

        assert result.exit_code == 0
        assert "mem-abc123" in result.output
        assert "auth_pattern" in result.output

    def test_list_with_memories_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_memories.return_value = _MEMORIES_LIST
        result = _invoke(runner, forge_root, ["memories", "--json", "list"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert len(data["data"]) == 2
        assert data["data"][0]["id"] == "mem-abc123"

    def test_list_with_limit_flag(self, runner, forge_root, mock_client):
        mock_client.sync_list_memories.return_value = [_MEMORY_1]
        result = _invoke(
            runner, forge_root, ["memories", "list", "--limit", "10"], mock_client
        )

        assert result.exit_code == 0
        mock_client.sync_list_memories.assert_called_once_with(limit=10)

    def test_list_default_limit_is_50(self, runner, forge_root, mock_client):
        mock_client.sync_list_memories.return_value = []
        _invoke(runner, forge_root, ["memories", "list"], mock_client)

        mock_client.sync_list_memories.assert_called_once_with(limit=50)

    def test_list_api_error_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_list_memories.side_effect = RuntimeError("API down")
        result = _invoke(runner, forge_root, ["memories", "list"], mock_client)

        assert result.exit_code == 1

    def test_list_api_error_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_memories.side_effect = RuntimeError("connection refused")
        result = _invoke(runner, forge_root, ["memories", "--json", "list"], mock_client)

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False

    def test_list_shows_agent_id(self, runner, forge_root, mock_client):
        mock_client.sync_list_memories.return_value = _MEMORIES_LIST
        result = _invoke(runner, forge_root, ["memories", "list"], mock_client)

        assert result.exit_code == 0
        assert "forge:opencode" in result.output

    def test_list_shows_type(self, runner, forge_root, mock_client):
        mock_client.sync_list_memories.return_value = _MEMORIES_LIST
        result = _invoke(runner, forge_root, ["memories", "list"], mock_client)

        assert result.exit_code == 0
        assert "pattern" in result.output

    def test_list_shows_second_memory(self, runner, forge_root, mock_client):
        mock_client.sync_list_memories.return_value = _MEMORIES_LIST
        result = _invoke(runner, forge_root, ["memories", "list"], mock_client)

        assert result.exit_code == 0
        assert "mem-def456" in result.output
        # Rich may truncate long keys in the table column; check for the prefix
        assert "database_schema" in result.output


# ===========================================================================
# forge memories show
# ===========================================================================


class TestForgeMemoriesShow:
    """Tests for 'forge memories show MEMORY_ID'."""

    def test_show_existing_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_memory.return_value = _MEMORY_1
        result = _invoke(runner, forge_root, ["memories", "show", "mem-abc123"], mock_client)

        assert result.exit_code == 0
        assert "mem-abc123" in result.output
        assert "auth_pattern" in result.output
        assert "forge:opencode" in result.output
        assert "pattern" in result.output

    def test_show_existing_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_memory.return_value = _MEMORY_1
        result = _invoke(
            runner, forge_root, ["memories", "--json", "show", "mem-abc123"], mock_client
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge memories show"
        assert data["data"]["id"] == "mem-abc123"

    def test_show_not_found_exits_6(self, runner, forge_root, mock_client):
        mock_client.sync_get_memory.return_value = None
        result = _invoke(
            runner, forge_root, ["memories", "show", "nonexistent"], mock_client
        )

        assert result.exit_code == 6

    def test_show_not_found_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_memory.return_value = None
        result = _invoke(
            runner, forge_root, ["memories", "--json", "show", "nonexistent"], mock_client
        )

        assert result.exit_code == 6
        data = json.loads(result.output)
        assert data["success"] is False

    def test_show_calls_correct_id(self, runner, forge_root, mock_client):
        mock_client.sync_get_memory.return_value = _MEMORY_1
        _invoke(runner, forge_root, ["memories", "show", "mem-abc123"], mock_client)

        mock_client.sync_get_memory.assert_called_once_with("mem-abc123")

    def test_show_requires_memory_id_argument(self, runner, forge_root, mock_client):
        result = _invoke(runner, forge_root, ["memories", "show"], mock_client)
        assert result.exit_code == 2

    def test_show_displays_dict_value(self, runner, forge_root, mock_client):
        """Dict value is JSON-rendered in human mode."""
        mock_client.sync_get_memory.return_value = _MEMORY_1
        result = _invoke(runner, forge_root, ["memories", "show", "mem-abc123"], mock_client)

        assert result.exit_code == 0
        assert "JWT" in result.output

    def test_show_displays_string_value(self, runner, forge_root, mock_client):
        """String value is rendered directly in human mode."""
        mock_client.sync_get_memory.return_value = _MEMORY_2
        result = _invoke(runner, forge_root, ["memories", "show", "mem-def456"], mock_client)

        assert result.exit_code == 0
        assert "soft-delete" in result.output

    def test_show_displays_timestamps(self, runner, forge_root, mock_client):
        mock_client.sync_get_memory.return_value = _MEMORY_1
        result = _invoke(runner, forge_root, ["memories", "show", "mem-abc123"], mock_client)

        assert result.exit_code == 0
        assert "2026-02-23" in result.output

    def test_show_memory_without_value_shows_none(self, runner, forge_root, mock_client):
        """Memory with no 'value' key shows 'None' placeholder."""
        mem_no_value = {"id": "mem-xyz", "key": "empty_mem", "agent_id": "forge:test"}
        mock_client.sync_get_memory.return_value = mem_no_value
        result = _invoke(runner, forge_root, ["memories", "show", "mem-xyz"], mock_client)

        assert result.exit_code == 0
        assert "None" in result.output

    def test_show_exception_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_get_memory.side_effect = ConnectionError("timeout")
        result = _invoke(runner, forge_root, ["memories", "show", "mem-abc123"], mock_client)

        assert result.exit_code == 1


# ===========================================================================
# forge memories --help
# ===========================================================================


class TestForgeMemoriesHelp:
    """Ensure help text renders without errors."""

    def test_memories_group_help(self, runner):
        result = runner.invoke(cli, ["memories", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "show" in result.output

    def test_memories_list_help(self, runner):
        result = runner.invoke(cli, ["memories", "list", "--help"])
        assert result.exit_code == 0
        assert "--limit" in result.output

    def test_memories_show_help(self, runner):
        result = runner.invoke(cli, ["memories", "show", "--help"])
        assert result.exit_code == 0
        assert "MEMORY_ID" in result.output


# ===========================================================================
# forge memories --json propagation
# ===========================================================================


class TestForgeMemoriesJsonPropagation:
    """Ensure --json on the group propagates to subcommands."""

    def test_group_json_flag_propagates_to_list(self, runner, forge_root, mock_client):
        mock_client.sync_list_memories.return_value = _MEMORIES_LIST
        result = _invoke(runner, forge_root, ["memories", "--json", "list"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_group_json_flag_propagates_to_show(self, runner, forge_root, mock_client):
        mock_client.sync_get_memory.return_value = _MEMORY_1
        result = _invoke(
            runner, forge_root, ["memories", "--json", "show", "mem-abc123"], mock_client
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_group_json_error_output_is_parseable(self, runner, forge_root, mock_client):
        mock_client.sync_list_memories.side_effect = RuntimeError("boom")
        result = _invoke(runner, forge_root, ["memories", "--json", "list"], mock_client)

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False


# ===========================================================================
# CommandCenterClient memory methods (unit tests)
# ===========================================================================


class TestCommandCenterClientMemoryMethods:
    """Unit tests verifying the memory methods exist on CommandCenterClient."""

    def test_sync_list_memories_method_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_list_memories")
        assert callable(client.sync_list_memories)

    def test_sync_get_memory_method_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_get_memory")
        assert callable(client.sync_get_memory)

    def test_async_list_memories_method_exists(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "list_memories")
        assert inspect.iscoroutinefunction(client.list_memories)

    def test_async_get_memory_method_exists(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "get_memory")
        assert inspect.iscoroutinefunction(client.get_memory)
