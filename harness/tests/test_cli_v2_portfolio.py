"""Tests for forge portfolio CLI commands.

Covers all commands in forge_harness/cli_v2/portfolio.py:
    - forge portfolio list
    - forge portfolio show DOMAIN
    - forge portfolio detail DOMAIN PROJECT

Strategy: Click CliRunner for integration paths, CommandCenterClient fully
mocked via patch so no network I/O occurs.
"""

from __future__ import annotations

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
    """Pre-configured MagicMock for CommandCenterClient."""
    return MagicMock()


def _invoke(runner, forge_root, args, client_mock, input_text=None):
    """Helper: invoke CLI inside an isolated filesystem with a mocked client."""
    with runner.isolated_filesystem(temp_dir=forge_root):
        with patch("forge_harness.cli_v2.portfolio.CommandCenterClient") as cls:
            cls.from_env.return_value = client_mock
            return runner.invoke(cli, args, input=input_text)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

_DOMAIN_1 = {
    "domain": "codeswiftr-com",
    "project_count": 4,
    "mvp_status": "active",
    "description": "Code tools and developer productivity suite",
}

_DOMAIN_2 = {
    "domain": "brandfocus-ai",
    "project_count": 2,
    "mvp_status": "beta",
    "description": "AI-powered brand and voice tools",
}

_DOMAINS_LIST = [_DOMAIN_1, _DOMAIN_2]

_DOMAIN_DETAIL = {
    "domain": "codeswiftr-com",
    "description": "Code tools and developer productivity suite",
    "projects": [
        {
            "project": "interview-simulator",
            "status": "active",
            "priority": "high",
            "tech_stack": ["Python", "FastAPI", "React"],
            "last_activity": "2026-02-23T10:00:00+00:00",
        },
        {
            "project": "code-atlas",
            "status": "beta",
            "priority": "medium",
            "tech_stack": ["Python", "ChromaDB"],
            "last_activity": "2026-02-20T08:00:00+00:00",
        },
    ],
}

_PROJECT_DETAIL = {
    "domain": "codeswiftr-com",
    "project": "interview-simulator",
    "status": "active",
    "priority": "high",
    "description": "AI-powered interview practice tool",
    "tech_stack": ["Python", "FastAPI", "React", "PostgreSQL"],
    "tests_collected": 312,
    "coverage": 78,
    "ruff_clean": True,
    "mypy_clean": False,
    "last_activity": "2026-02-23T10:00:00+00:00",
}


# ===========================================================================
# forge portfolio list
# ===========================================================================


class TestPortfolioList:
    """Tests for 'forge portfolio list'."""

    def test_list_empty_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_portfolio.return_value = []
        result = _invoke(runner, forge_root, ["portfolio", "list"], mock_client)

        assert result.exit_code == 0
        assert "No domains found" in result.output

    def test_list_empty_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_portfolio.return_value = []
        result = _invoke(runner, forge_root, ["portfolio", "--json", "list"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge portfolio list"
        assert data["data"] == []

    def test_list_with_domains_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_list_portfolio.return_value = _DOMAINS_LIST
        result = _invoke(runner, forge_root, ["portfolio", "list"], mock_client)

        assert result.exit_code == 0
        assert "codeswiftr-com" in result.output
        assert "brandfocus-ai" in result.output

    def test_list_shows_mvp_status(self, runner, forge_root, mock_client):
        mock_client.sync_list_portfolio.return_value = _DOMAINS_LIST
        result = _invoke(runner, forge_root, ["portfolio", "list"], mock_client)

        assert result.exit_code == 0
        assert "active" in result.output
        assert "beta" in result.output

    def test_list_shows_project_count(self, runner, forge_root, mock_client):
        mock_client.sync_list_portfolio.return_value = _DOMAINS_LIST
        result = _invoke(runner, forge_root, ["portfolio", "list"], mock_client)

        assert result.exit_code == 0
        assert "4" in result.output
        assert "2" in result.output

    def test_list_json_mode_with_domains(self, runner, forge_root, mock_client):
        mock_client.sync_list_portfolio.return_value = _DOMAINS_LIST
        result = _invoke(runner, forge_root, ["portfolio", "--json", "list"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert len(data["data"]) == 2

    def test_list_api_error_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_list_portfolio.side_effect = RuntimeError("API down")
        result = _invoke(runner, forge_root, ["portfolio", "list"], mock_client)

        assert result.exit_code == 1

    def test_list_api_error_json_mode_outputs_json(self, runner, forge_root, mock_client):
        mock_client.sync_list_portfolio.side_effect = RuntimeError("boom")
        result = _invoke(runner, forge_root, ["portfolio", "--json", "list"], mock_client)

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False

    def test_list_calls_sync_list_portfolio(self, runner, forge_root, mock_client):
        mock_client.sync_list_portfolio.return_value = _DOMAINS_LIST
        _invoke(runner, forge_root, ["portfolio", "list"], mock_client)

        mock_client.sync_list_portfolio.assert_called_once()

    def test_list_description_shown(self, runner, forge_root, mock_client):
        mock_client.sync_list_portfolio.return_value = _DOMAINS_LIST
        result = _invoke(runner, forge_root, ["portfolio", "list"], mock_client)

        assert result.exit_code == 0
        # Description is truncated to 60 chars; first part should appear
        assert "Code tools" in result.output

    def test_list_project_count_as_list(self, runner, forge_root, mock_client):
        """projects field as a list should show the list length."""
        domains_with_list = [
            {
                "domain": "test-domain",
                "projects": [{"project": "p1"}, {"project": "p2"}],
                "mvp_status": "active",
            }
        ]
        mock_client.sync_list_portfolio.return_value = domains_with_list
        result = _invoke(runner, forge_root, ["portfolio", "list"], mock_client)

        assert result.exit_code == 0
        assert "2" in result.output


# ===========================================================================
# forge portfolio show
# ===========================================================================


class TestPortfolioShow:
    """Tests for 'forge portfolio show DOMAIN'."""

    def test_show_existing_domain_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_domain.return_value = _DOMAIN_DETAIL
        result = _invoke(
            runner, forge_root, ["portfolio", "show", "codeswiftr-com"], mock_client
        )

        assert result.exit_code == 0
        assert "codeswiftr-com" in result.output

    def test_show_lists_projects(self, runner, forge_root, mock_client):
        mock_client.sync_get_domain.return_value = _DOMAIN_DETAIL
        result = _invoke(
            runner, forge_root, ["portfolio", "show", "codeswiftr-com"], mock_client
        )

        assert result.exit_code == 0
        assert "interview-simulator" in result.output
        assert "code-atlas" in result.output

    def test_show_displays_project_status(self, runner, forge_root, mock_client):
        mock_client.sync_get_domain.return_value = _DOMAIN_DETAIL
        result = _invoke(
            runner, forge_root, ["portfolio", "show", "codeswiftr-com"], mock_client
        )

        assert result.exit_code == 0
        assert "active" in result.output
        assert "beta" in result.output

    def test_show_displays_tech_stack(self, runner, forge_root, mock_client):
        mock_client.sync_get_domain.return_value = _DOMAIN_DETAIL
        result = _invoke(
            runner, forge_root, ["portfolio", "show", "codeswiftr-com"], mock_client
        )

        assert result.exit_code == 0
        assert "Python" in result.output

    def test_show_domain_not_found_exits_6(self, runner, forge_root, mock_client):
        mock_client.sync_get_domain.return_value = None
        result = _invoke(
            runner, forge_root, ["portfolio", "show", "nonexistent"], mock_client
        )

        assert result.exit_code == 6

    def test_show_domain_not_found_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_domain.return_value = None
        result = _invoke(
            runner, forge_root, ["portfolio", "--json", "show", "nonexistent"], mock_client
        )

        assert result.exit_code == 6
        data = json.loads(result.output)
        assert data["success"] is False

    def test_show_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_domain.return_value = _DOMAIN_DETAIL
        result = _invoke(
            runner, forge_root, ["portfolio", "--json", "show", "codeswiftr-com"], mock_client
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge portfolio show"
        assert data["data"]["domain"] == "codeswiftr-com"

    def test_show_calls_sync_get_domain(self, runner, forge_root, mock_client):
        mock_client.sync_get_domain.return_value = _DOMAIN_DETAIL
        _invoke(runner, forge_root, ["portfolio", "show", "codeswiftr-com"], mock_client)

        mock_client.sync_get_domain.assert_called_once_with("codeswiftr-com")

    def test_show_requires_domain_argument(self, runner, forge_root, mock_client):
        result = _invoke(runner, forge_root, ["portfolio", "show"], mock_client)
        assert result.exit_code == 2

    def test_show_empty_projects_shows_message(self, runner, forge_root, mock_client):
        domain_no_projects = {"domain": "empty-domain", "projects": []}
        mock_client.sync_get_domain.return_value = domain_no_projects
        result = _invoke(
            runner, forge_root, ["portfolio", "show", "empty-domain"], mock_client
        )

        assert result.exit_code == 0
        assert "No projects found" in result.output

    def test_show_api_error_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_get_domain.side_effect = RuntimeError("connection error")
        result = _invoke(
            runner, forge_root, ["portfolio", "show", "codeswiftr-com"], mock_client
        )

        assert result.exit_code == 1

    def test_show_last_activity_truncated_to_date(self, runner, forge_root, mock_client):
        mock_client.sync_get_domain.return_value = _DOMAIN_DETAIL
        result = _invoke(
            runner, forge_root, ["portfolio", "show", "codeswiftr-com"], mock_client
        )

        assert result.exit_code == 0
        assert "2026-02-23" in result.output


# ===========================================================================
# forge portfolio detail
# ===========================================================================


class TestPortfolioDetail:
    """Tests for 'forge portfolio detail DOMAIN PROJECT'."""

    def test_detail_success_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_project.return_value = _PROJECT_DETAIL
        result = _invoke(
            runner,
            forge_root,
            ["portfolio", "detail", "codeswiftr-com", "interview-simulator"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "codeswiftr-com" in result.output
        assert "interview-simulator" in result.output

    def test_detail_shows_status(self, runner, forge_root, mock_client):
        mock_client.sync_get_project.return_value = _PROJECT_DETAIL
        result = _invoke(
            runner,
            forge_root,
            ["portfolio", "detail", "codeswiftr-com", "interview-simulator"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "active" in result.output

    def test_detail_shows_tech_stack(self, runner, forge_root, mock_client):
        mock_client.sync_get_project.return_value = _PROJECT_DETAIL
        result = _invoke(
            runner,
            forge_root,
            ["portfolio", "detail", "codeswiftr-com", "interview-simulator"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "FastAPI" in result.output

    def test_detail_shows_test_count(self, runner, forge_root, mock_client):
        mock_client.sync_get_project.return_value = _PROJECT_DETAIL
        result = _invoke(
            runner,
            forge_root,
            ["portfolio", "detail", "codeswiftr-com", "interview-simulator"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "312" in result.output

    def test_detail_shows_coverage(self, runner, forge_root, mock_client):
        mock_client.sync_get_project.return_value = _PROJECT_DETAIL
        result = _invoke(
            runner,
            forge_root,
            ["portfolio", "detail", "codeswiftr-com", "interview-simulator"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "78" in result.output

    def test_detail_shows_ruff_status(self, runner, forge_root, mock_client):
        mock_client.sync_get_project.return_value = _PROJECT_DETAIL
        result = _invoke(
            runner,
            forge_root,
            ["portfolio", "detail", "codeswiftr-com", "interview-simulator"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "clean" in result.output or "Ruff" in result.output

    def test_detail_project_not_found_exits_6(self, runner, forge_root, mock_client):
        mock_client.sync_get_project.return_value = None
        result = _invoke(
            runner,
            forge_root,
            ["portfolio", "detail", "codeswiftr-com", "nonexistent"],
            mock_client,
        )

        assert result.exit_code == 6

    def test_detail_project_not_found_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_project.return_value = None
        result = _invoke(
            runner,
            forge_root,
            ["portfolio", "--json", "detail", "codeswiftr-com", "nonexistent"],
            mock_client,
        )

        assert result.exit_code == 6
        data = json.loads(result.output)
        assert data["success"] is False

    def test_detail_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_get_project.return_value = _PROJECT_DETAIL
        result = _invoke(
            runner,
            forge_root,
            ["portfolio", "--json", "detail", "codeswiftr-com", "interview-simulator"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge portfolio detail"
        assert data["data"]["project"] == "interview-simulator"

    def test_detail_calls_sync_get_project(self, runner, forge_root, mock_client):
        mock_client.sync_get_project.return_value = _PROJECT_DETAIL
        _invoke(
            runner,
            forge_root,
            ["portfolio", "detail", "codeswiftr-com", "interview-simulator"],
            mock_client,
        )

        mock_client.sync_get_project.assert_called_once_with(
            "codeswiftr-com", "interview-simulator"
        )

    def test_detail_requires_domain_and_project(self, runner, forge_root, mock_client):
        result = _invoke(
            runner, forge_root, ["portfolio", "detail", "codeswiftr-com"], mock_client
        )
        assert result.exit_code == 2

    def test_detail_requires_both_arguments(self, runner, forge_root, mock_client):
        result = _invoke(runner, forge_root, ["portfolio", "detail"], mock_client)
        assert result.exit_code == 2

    def test_detail_api_error_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_get_project.side_effect = RuntimeError("API error")
        result = _invoke(
            runner,
            forge_root,
            ["portfolio", "detail", "codeswiftr-com", "interview-simulator"],
            mock_client,
        )

        assert result.exit_code == 1

    def test_detail_api_error_json_mode_outputs_json(self, runner, forge_root, mock_client):
        mock_client.sync_get_project.side_effect = RuntimeError("crash")
        result = _invoke(
            runner,
            forge_root,
            ["portfolio", "--json", "detail", "codeswiftr-com", "interview-simulator"],
            mock_client,
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False

    def test_detail_minimal_project_no_metrics(self, runner, forge_root, mock_client):
        """Project with only status should not crash."""
        minimal = {"domain": "d", "project": "p", "status": "planned"}
        mock_client.sync_get_project.return_value = minimal
        result = _invoke(
            runner, forge_root, ["portfolio", "detail", "d", "p"], mock_client
        )

        assert result.exit_code == 0
        assert "planned" in result.output

    def test_detail_shows_last_activity(self, runner, forge_root, mock_client):
        mock_client.sync_get_project.return_value = _PROJECT_DETAIL
        result = _invoke(
            runner,
            forge_root,
            ["portfolio", "detail", "codeswiftr-com", "interview-simulator"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "2026-02-23" in result.output


# ===========================================================================
# Help text
# ===========================================================================


class TestPortfolioHelp:
    """Ensure help text renders without errors for all subcommands."""

    def test_portfolio_group_help(self, runner):
        result = runner.invoke(cli, ["portfolio", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "show" in result.output
        assert "detail" in result.output

    def test_portfolio_list_help(self, runner):
        result = runner.invoke(cli, ["portfolio", "list", "--help"])
        assert result.exit_code == 0

    def test_portfolio_show_help(self, runner):
        result = runner.invoke(cli, ["portfolio", "show", "--help"])
        assert result.exit_code == 0
        assert "DOMAIN" in result.output

    def test_portfolio_detail_help(self, runner):
        result = runner.invoke(cli, ["portfolio", "detail", "--help"])
        assert result.exit_code == 0
        assert "DOMAIN" in result.output
        assert "PROJECT" in result.output


# ===========================================================================
# JSON flag propagation
# ===========================================================================


class TestPortfolioJsonPropagation:
    """Confirm --json on the group propagates to all subcommands."""

    def test_json_flag_propagates_to_list(self, runner, forge_root, mock_client):
        mock_client.sync_list_portfolio.return_value = _DOMAINS_LIST
        result = _invoke(runner, forge_root, ["portfolio", "--json", "list"], mock_client)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_json_flag_propagates_to_show(self, runner, forge_root, mock_client):
        mock_client.sync_get_domain.return_value = _DOMAIN_DETAIL
        result = _invoke(
            runner, forge_root, ["portfolio", "--json", "show", "codeswiftr-com"], mock_client
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_json_flag_propagates_to_detail(self, runner, forge_root, mock_client):
        mock_client.sync_get_project.return_value = _PROJECT_DETAIL
        result = _invoke(
            runner,
            forge_root,
            ["portfolio", "--json", "detail", "codeswiftr-com", "interview-simulator"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_json_flag_error_produces_parseable_json_for_list(
        self, runner, forge_root, mock_client
    ):
        mock_client.sync_list_portfolio.side_effect = RuntimeError("down")
        result = _invoke(runner, forge_root, ["portfolio", "--json", "list"], mock_client)

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False


# ===========================================================================
# CommandCenterClient portfolio methods (unit tests)
# ===========================================================================


class TestCommandCenterClientPortfolioMethods:
    """Verify existing portfolio methods are present and correctly typed."""

    def test_sync_list_portfolio_method_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_list_portfolio")
        assert callable(client.sync_list_portfolio)

    def test_sync_get_domain_method_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_get_domain")
        assert callable(client.sync_get_domain)

    def test_sync_get_project_method_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_get_project")
        assert callable(client.sync_get_project)

    def test_async_list_portfolio_method_exists(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "list_portfolio")
        assert inspect.iscoroutinefunction(client.list_portfolio)

    def test_async_get_domain_method_exists(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "get_domain")
        assert inspect.iscoroutinefunction(client.get_domain)

    def test_async_get_project_method_exists(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "get_project")
        assert inspect.iscoroutinefunction(client.get_project)
