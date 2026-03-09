"""Tests for forge features CLI commands.

Covers all commands in forge_harness/cli_v2/features.py:
    - list_features
    - show_feature
    - assign_feature
    - link_task
    - stats

Strategy: CLI Runner for integration paths, direct function calls where
possible. All HTTP calls are mocked via CommandCenterClient.
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
        with patch("forge_harness.cli_v2.features.CommandCenterClient") as cls:
            cls.from_env.return_value = client_mock
            return runner.invoke(cli, args, input=input_text)


# ---------------------------------------------------------------------------
# Sample data helpers
# ---------------------------------------------------------------------------

_FEATURE_1 = {
    "feature_id": "auth-module",
    "name": "Auth Module",
    "status": "in_progress",
    "completion_pct": 45.0,
    "domain": "codeswiftr-com",
    "project": "interview-simulator",
    "assigned_agent": "forge:opencode",
    "linked_task_ids": ["task-abc", "task-def"],
    "updated_at": "2026-02-23T10:00:00+00:00",
}

_FEATURE_2 = {
    "feature_id": "dashboard-ui",
    "name": "Dashboard UI",
    "status": "planned",
    "completion_pct": 0.0,
    "domain": "brandfocus-ai",
    "project": "voice-coach",
    "assigned_agent": None,
    "linked_task_ids": [],
    "updated_at": "2026-02-22T09:00:00+00:00",
}

_FEATURES_RESPONSE = {
    "features": [_FEATURE_1, _FEATURE_2],
    "count": 2,
}

_STATS_RESPONSE = {
    "total": 42,
    "by_status": {
        "planned": 10,
        "in_progress": 15,
        "testing": 7,
        "done": 10,
    },
    "by_domain": {
        "brandfocus-ai": 20,
        "codeswiftr-com": 22,
    },
}


# ===========================================================================
# forge features list
# ===========================================================================


class TestForgeFeaturesList:
    """Tests for 'forge features list'."""

    def test_list_empty_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_features.return_value = {"features": [], "count": 0}
        result = _invoke(runner, forge_root, ["features", "list"], mock_client)

        assert result.exit_code == 0
        assert "No features found" in result.output

    def test_list_empty_json_mode(self, runner, forge_root, mock_client):
        empty = {"features": [], "count": 0}
        mock_client.sync_list_features.return_value = empty
        result = _invoke(runner, forge_root, ["features", "--json", "list"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge features list"
        assert data["data"]["features"] == []

    def test_list_with_features_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_features.return_value = _FEATURES_RESPONSE
        result = _invoke(runner, forge_root, ["features", "list"], mock_client)

        assert result.exit_code == 0
        # Rich may truncate long IDs/values in the table; check for partial content
        assert "auth-modu" in result.output  # truncated by Rich column width
        assert "Auth" in result.output
        assert "in_progre" in result.output  # truncated by Rich column width

    def test_list_with_features_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_features.return_value = _FEATURES_RESPONSE
        result = _invoke(runner, forge_root, ["features", "--json", "list"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert len(data["data"]["features"]) == 2
        assert data["data"]["count"] == 2

    def test_list_with_status_filter(self, runner, forge_root, mock_client):
        filtered = {"features": [_FEATURE_1], "count": 1}
        mock_client.sync_list_features.return_value = filtered
        result = _invoke(
            runner, forge_root, ["features", "list", "--status", "in_progress"], mock_client
        )

        assert result.exit_code == 0
        mock_client.sync_list_features.assert_called_once_with(
            status="in_progress", domain=None, project=None
        )

    def test_list_with_domain_filter(self, runner, forge_root, mock_client):
        filtered = {"features": [_FEATURE_1], "count": 1}
        mock_client.sync_list_features.return_value = filtered
        result = _invoke(
            runner, forge_root, ["features", "list", "--domain", "codeswiftr-com"], mock_client
        )

        assert result.exit_code == 0
        mock_client.sync_list_features.assert_called_once_with(
            status=None, domain="codeswiftr-com", project=None
        )

    def test_list_with_project_filter(self, runner, forge_root, mock_client):
        filtered = {"features": [_FEATURE_1], "count": 1}
        mock_client.sync_list_features.return_value = filtered
        result = _invoke(
            runner,
            forge_root,
            ["features", "list", "--project", "interview-simulator"],
            mock_client,
        )

        assert result.exit_code == 0
        mock_client.sync_list_features.assert_called_once_with(
            status=None, domain=None, project="interview-simulator"
        )

    def test_list_invalid_status_rejected(self, runner, forge_root, mock_client):
        """Click should reject invalid status choices before calling the client."""
        result = _invoke(
            runner, forge_root, ["features", "list", "--status", "invalid_status"], mock_client
        )
        # Click returns exit code 2 for bad choices
        assert result.exit_code == 2
        mock_client.sync_list_features.assert_not_called()

    def test_list_api_error_exits_with_code_1(self, runner, forge_root, mock_client):
        mock_client.sync_list_features.side_effect = RuntimeError("API down")
        result = _invoke(runner, forge_root, ["features", "list"], mock_client)

        assert result.exit_code == 1

    def test_list_completion_pct_displayed(self, runner, forge_root, mock_client):
        mock_client.sync_list_features.return_value = _FEATURES_RESPONSE
        result = _invoke(runner, forge_root, ["features", "list"], mock_client)

        assert result.exit_code == 0
        # completion pct rendered as "45%"
        assert "45%" in result.output

    def test_list_shows_no_assigned_agent_dash(self, runner, forge_root, mock_client):
        mock_client.sync_list_features.return_value = _FEATURES_RESPONSE
        result = _invoke(runner, forge_root, ["features", "list"], mock_client)

        assert result.exit_code == 0
        # _FEATURE_2 has no assigned agent; should show "-"
        assert "-" in result.output

    def test_list_all_valid_status_values(self, runner, forge_root, mock_client):
        """All four valid statuses must be accepted by Click."""
        for status_val in ("planned", "in_progress", "testing", "done"):
            mock_client.sync_list_features.return_value = {"features": [], "count": 0}
            result = _invoke(
                runner, forge_root, ["features", "list", "--status", status_val], mock_client
            )
            assert result.exit_code == 0, f"Expected exit 0 for status={status_val}"


# ===========================================================================
# forge features show
# ===========================================================================


class TestForgeFeatureShow:
    """Tests for 'forge features show FEATURE_ID'."""

    def test_show_existing_feature_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature.return_value = _FEATURE_1
        result = _invoke(runner, forge_root, ["features", "show", "auth-module"], mock_client)

        assert result.exit_code == 0
        assert "Auth Module" in result.output
        assert "in_progress" in result.output
        assert "45.0%" in result.output
        assert "codeswiftr-com" in result.output
        assert "forge:opencode" in result.output

    def test_show_existing_feature_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature.return_value = _FEATURE_1
        result = _invoke(
            runner, forge_root, ["features", "--json", "show", "auth-module"], mock_client
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge features show"
        assert data["data"]["feature_id"] == "auth-module"

    def test_show_feature_not_found_exits_6(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature.return_value = None
        result = _invoke(runner, forge_root, ["features", "show", "nonexistent"], mock_client)

        assert result.exit_code == 6

    def test_show_feature_not_found_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature.return_value = None
        result = _invoke(
            runner, forge_root, ["features", "--json", "show", "nonexistent"], mock_client
        )

        assert result.exit_code == 6
        data = json.loads(result.output)
        assert data["success"] is False

    def test_show_feature_calls_correct_id(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature.return_value = _FEATURE_1
        _invoke(runner, forge_root, ["features", "show", "auth-module"], mock_client)

        mock_client.sync_get_feature.assert_called_once_with("auth-module")

    def test_show_feature_linked_tasks_displayed(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature.return_value = _FEATURE_1
        result = _invoke(runner, forge_root, ["features", "show", "auth-module"], mock_client)

        assert result.exit_code == 0
        assert "task-abc" in result.output
        assert "task-def" in result.output

    def test_show_feature_no_linked_tasks(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature.return_value = _FEATURE_2
        result = _invoke(runner, forge_root, ["features", "show", "dashboard-ui"], mock_client)

        assert result.exit_code == 0
        assert "None" in result.output

    def test_show_api_error_exits_with_code_1(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature.side_effect = RuntimeError("Connection error")
        result = _invoke(runner, forge_root, ["features", "show", "auth-module"], mock_client)

        assert result.exit_code == 1

    def test_show_requires_feature_id_argument(self, runner, forge_root, mock_client):
        result = _invoke(runner, forge_root, ["features", "show"], mock_client)
        # Missing required argument → exit code 2
        assert result.exit_code == 2

    def test_show_feature_updated_at_displayed(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature.return_value = _FEATURE_1
        result = _invoke(runner, forge_root, ["features", "show", "auth-module"], mock_client)

        assert result.exit_code == 0
        assert "2026-02-23" in result.output


# ===========================================================================
# forge features assign
# ===========================================================================


class TestForgeFeatureAssign:
    """Tests for 'forge features assign FEATURE_ID --to AGENT_ID'."""

    def test_assign_success_human_mode(self, runner, forge_root, mock_client):
        updated = {**_FEATURE_1, "status": "in_progress", "assigned_agent": "forge:opencode"}
        mock_client.sync_assign_feature.return_value = updated
        result = _invoke(
            runner,
            forge_root,
            ["features", "assign", "auth-module", "--to", "forge:opencode"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "auth-module" in result.output
        assert "forge:opencode" in result.output

    def test_assign_success_json_mode(self, runner, forge_root, mock_client):
        updated = {**_FEATURE_1, "assigned_agent": "forge:opencode"}
        mock_client.sync_assign_feature.return_value = updated
        result = _invoke(
            runner,
            forge_root,
            ["features", "--json", "assign", "auth-module", "--to", "forge:opencode"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge features assign"
        assert data["data"]["assigned_agent"] == "forge:opencode"

    def test_assign_api_failure_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_assign_feature.return_value = None
        result = _invoke(
            runner,
            forge_root,
            ["features", "assign", "auth-module", "--to", "forge:opencode"],
            mock_client,
        )

        assert result.exit_code == 1

    def test_assign_api_failure_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_assign_feature.return_value = None
        result = _invoke(
            runner,
            forge_root,
            ["features", "--json", "assign", "auth-module", "--to", "forge:opencode"],
            mock_client,
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False

    def test_assign_calls_correct_args(self, runner, forge_root, mock_client):
        mock_client.sync_assign_feature.return_value = _FEATURE_1
        _invoke(
            runner,
            forge_root,
            ["features", "assign", "auth-module", "--to", "forge:kilo"],
            mock_client,
        )

        mock_client.sync_assign_feature.assert_called_once_with("auth-module", "forge:kilo")

    def test_assign_requires_feature_id(self, runner, forge_root, mock_client):
        result = _invoke(
            runner, forge_root, ["features", "assign", "--to", "forge:opencode"], mock_client
        )
        assert result.exit_code == 2

    def test_assign_requires_agent_option(self, runner, forge_root, mock_client):
        result = _invoke(
            runner, forge_root, ["features", "assign", "auth-module"], mock_client
        )
        assert result.exit_code == 2

    def test_assign_exception_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_assign_feature.side_effect = ConnectionError("timeout")
        result = _invoke(
            runner,
            forge_root,
            ["features", "assign", "auth-module", "--to", "forge:opencode"],
            mock_client,
        )

        assert result.exit_code == 1


# ===========================================================================
# forge features link-task
# ===========================================================================


class TestForgeFeatureLinkTask:
    """Tests for 'forge features link-task FEATURE_ID --task-id TASK_ID'."""

    def test_link_task_success_human_mode(self, runner, forge_root, mock_client):
        updated = {**_FEATURE_1, "linked_task_ids": ["task-abc", "task-def", "task-xyz"]}
        mock_client.sync_link_feature_task.return_value = updated
        result = _invoke(
            runner,
            forge_root,
            ["features", "link-task", "auth-module", "--task-id", "task-xyz"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "task-xyz" in result.output
        assert "auth-module" in result.output

    def test_link_task_success_json_mode(self, runner, forge_root, mock_client):
        updated = {**_FEATURE_1, "linked_task_ids": ["task-abc", "task-def", "task-xyz"]}
        mock_client.sync_link_feature_task.return_value = updated
        result = _invoke(
            runner,
            forge_root,
            ["features", "--json", "link-task", "auth-module", "--task-id", "task-xyz"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge features link-task"
        assert "task-xyz" in data["data"]["linked_task_ids"]

    def test_link_task_api_failure_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_link_feature_task.return_value = None
        result = _invoke(
            runner,
            forge_root,
            ["features", "link-task", "auth-module", "--task-id", "task-xyz"],
            mock_client,
        )

        assert result.exit_code == 1

    def test_link_task_api_failure_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_link_feature_task.return_value = None
        result = _invoke(
            runner,
            forge_root,
            ["features", "--json", "link-task", "auth-module", "--task-id", "task-xyz"],
            mock_client,
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False

    def test_link_task_calls_correct_args(self, runner, forge_root, mock_client):
        mock_client.sync_link_feature_task.return_value = _FEATURE_1
        _invoke(
            runner,
            forge_root,
            ["features", "link-task", "auth-module", "--task-id", "task-999"],
            mock_client,
        )

        mock_client.sync_link_feature_task.assert_called_once_with("auth-module", "task-999")

    def test_link_task_requires_feature_id(self, runner, forge_root, mock_client):
        result = _invoke(
            runner, forge_root, ["features", "link-task", "--task-id", "task-999"], mock_client
        )
        assert result.exit_code == 2

    def test_link_task_requires_task_id_option(self, runner, forge_root, mock_client):
        result = _invoke(
            runner, forge_root, ["features", "link-task", "auth-module"], mock_client
        )
        assert result.exit_code == 2

    def test_link_task_exception_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_link_feature_task.side_effect = RuntimeError("unexpected")
        result = _invoke(
            runner,
            forge_root,
            ["features", "link-task", "auth-module", "--task-id", "task-999"],
            mock_client,
        )

        assert result.exit_code == 1


# ===========================================================================
# forge features stats
# ===========================================================================


class TestForgeFeatureStats:
    """Tests for 'forge features stats'."""

    def test_stats_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature_stats.return_value = _STATS_RESPONSE
        result = _invoke(runner, forge_root, ["features", "stats"], mock_client)

        assert result.exit_code == 0
        assert "42" in result.output  # total
        # by_status
        assert "planned" in result.output
        assert "in_progress" in result.output
        assert "testing" in result.output
        assert "done" in result.output
        # by_domain
        assert "brandfocus-ai" in result.output
        assert "codeswiftr-com" in result.output

    def test_stats_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature_stats.return_value = _STATS_RESPONSE
        result = _invoke(runner, forge_root, ["features", "--json", "stats"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge features stats"
        assert data["data"]["total"] == 42
        assert data["data"]["by_status"]["in_progress"] == 15

    def test_stats_empty_response_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature_stats.return_value = {}
        result = _invoke(runner, forge_root, ["features", "stats"], mock_client)

        assert result.exit_code == 0
        assert "No stats available" in result.output

    def test_stats_empty_response_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature_stats.return_value = {}
        result = _invoke(runner, forge_root, ["features", "--json", "stats"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"] == {}

    def test_stats_api_error_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature_stats.side_effect = RuntimeError("connection refused")
        result = _invoke(runner, forge_root, ["features", "stats"], mock_client)

        assert result.exit_code == 1

    def test_stats_by_status_counts(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature_stats.return_value = _STATS_RESPONSE
        result = _invoke(runner, forge_root, ["features", "stats"], mock_client)

        assert result.exit_code == 0
        assert "10" in result.output  # planned count
        assert "15" in result.output  # in_progress count
        assert "7" in result.output   # testing count

    def test_stats_no_by_domain_still_works(self, runner, forge_root, mock_client):
        no_domain = {"total": 5, "by_status": {"planned": 5}}
        mock_client.sync_get_feature_stats.return_value = no_domain
        result = _invoke(runner, forge_root, ["features", "stats"], mock_client)

        assert result.exit_code == 0
        assert "5" in result.output

    def test_stats_extra_status_values_rendered(self, runner, forge_root, mock_client):
        """Non-standard status values are rendered in addition to standard ones."""
        custom = {
            "total": 3,
            "by_status": {"planned": 1, "deprecated": 2},
        }
        mock_client.sync_get_feature_stats.return_value = custom
        result = _invoke(runner, forge_root, ["features", "stats"], mock_client)

        assert result.exit_code == 0
        assert "deprecated" in result.output


# ===========================================================================
# forge features --help / subcommand --help
# ===========================================================================


class TestForgeFeatureHelp:
    """Ensure help text renders without errors."""

    def test_features_group_help(self, runner):
        result = runner.invoke(cli, ["features", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "show" in result.output
        assert "assign" in result.output
        assert "link-task" in result.output
        assert "stats" in result.output

    def test_features_list_help(self, runner):
        result = runner.invoke(cli, ["features", "list", "--help"])
        assert result.exit_code == 0
        assert "--status" in result.output

    def test_features_show_help(self, runner):
        result = runner.invoke(cli, ["features", "show", "--help"])
        assert result.exit_code == 0
        assert "FEATURE_ID" in result.output

    def test_features_assign_help(self, runner):
        result = runner.invoke(cli, ["features", "assign", "--help"])
        assert result.exit_code == 0
        assert "--to" in result.output

    def test_features_link_task_help(self, runner):
        result = runner.invoke(cli, ["features", "link-task", "--help"])
        assert result.exit_code == 0
        assert "--task-id" in result.output

    def test_features_stats_help(self, runner):
        result = runner.invoke(cli, ["features", "stats", "--help"])
        assert result.exit_code == 0


# ===========================================================================
# forge features - group-level --json propagation
# ===========================================================================


class TestForgeFeatureJsonPropagation:
    """Ensure --json on the group propagates to subcommands."""

    def test_group_json_flag_propagates_to_list(self, runner, forge_root, mock_client):
        mock_client.sync_list_features.return_value = _FEATURES_RESPONSE
        result = _invoke(runner, forge_root, ["features", "--json", "list"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_group_json_flag_propagates_to_show(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature.return_value = _FEATURE_1
        result = _invoke(
            runner, forge_root, ["features", "--json", "show", "auth-module"], mock_client
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_group_json_flag_propagates_to_stats(self, runner, forge_root, mock_client):
        mock_client.sync_get_feature_stats.return_value = _STATS_RESPONSE
        result = _invoke(runner, forge_root, ["features", "--json", "stats"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_group_json_flag_error_output_is_json(self, runner, forge_root, mock_client):
        """Even on error, JSON mode should produce parseable JSON."""
        mock_client.sync_list_features.side_effect = RuntimeError("boom")
        result = _invoke(runner, forge_root, ["features", "--json", "list"], mock_client)

        assert result.exit_code == 1
        # JSON error output is written via handle_error_json which prints to stdout
        data = json.loads(result.output)
        assert data["success"] is False


# ===========================================================================
# CommandCenterClient feature methods (unit tests)
# ===========================================================================


class TestCommandCenterClientFeatureMethods:
    """Unit tests for the new sync/async feature methods on CommandCenterClient."""

    def test_sync_list_features_method_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_list_features")
        assert callable(client.sync_list_features)

    def test_sync_get_feature_method_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_get_feature")
        assert callable(client.sync_get_feature)

    def test_sync_assign_feature_method_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_assign_feature")
        assert callable(client.sync_assign_feature)

    def test_sync_link_feature_task_method_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_link_feature_task")
        assert callable(client.sync_link_feature_task)

    def test_sync_get_feature_stats_method_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_get_feature_stats")
        assert callable(client.sync_get_feature_stats)

    def test_async_list_features_method_exists(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "list_features")
        assert inspect.iscoroutinefunction(client.list_features)

    def test_async_get_feature_method_exists(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "get_feature")
        assert inspect.iscoroutinefunction(client.get_feature)

    def test_async_assign_feature_method_exists(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "assign_feature")
        assert inspect.iscoroutinefunction(client.assign_feature)

    def test_async_link_feature_task_method_exists(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "link_feature_task")
        assert inspect.iscoroutinefunction(client.link_feature_task)

    def test_async_get_feature_stats_method_exists(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "get_feature_stats")
        assert inspect.iscoroutinefunction(client.get_feature_stats)
