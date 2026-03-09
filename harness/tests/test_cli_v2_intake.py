"""
Tests for forge intake CLI v2 commands.

Covers:
    list    — forge intake list [--status S] [--limit N]
    show    — forge intake show ITEM_ID
    create  — forge intake create --title T [--description D] [--priority P]
              [--source S] [--tags TAG1,TAG2]
    stats   — forge intake stats

All external I/O (CommandCenterClient) is mocked so these tests run fully
offline without a live Command Center server.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def invoke(args: list[str], input_text: str | None = None):
    """Run the forge CLI and return the result."""
    runner = CliRunner()
    return runner.invoke(cli, args, input=input_text, catch_exceptions=False)


def parse_json_output(output: str) -> dict:
    """Extract and parse the JSON envelope from CLI output."""
    lines = output.strip().splitlines()
    json_start = next((i for i, l in enumerate(lines) if l.strip().startswith("{")), None)
    assert json_start is not None, f"No JSON found in output:\n{output}"
    return json.loads("\n".join(lines[json_start:]))


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

_INTAKE_ITEM_1 = {
    "id": "intake-001",
    "title": "Add OAuth2 support",
    "description": "Implement OAuth2 login flow for the API",
    "status": "pending",
    "priority": "high",
    "source": "slack",
    "tags": ["auth", "backend"],
    "created_at": "2026-02-23T10:00:00",
    "updated_at": "2026-02-23T10:00:00",
}

_INTAKE_ITEM_2 = {
    "id": "intake-002",
    "title": "Fix pagination bug",
    "description": "Offset calculation is off by one",
    "status": "in_review",
    "priority": "medium",
    "source": "github",
    "tags": ["bug", "api"],
    "created_at": "2026-02-23T11:00:00",
    "updated_at": "2026-02-23T11:30:00",
}

_INTAKE_STATS = {
    "total": 42,
    "pending": 20,
    "in_review": 10,
    "processed": 10,
    "rejected": 2,
    "by_priority": {"high": 8, "medium": 25, "low": 9},
    "by_status": {"pending": 20, "in_review": 10, "processed": 10, "rejected": 2},
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    """MagicMock standing in for CommandCenterClient."""
    client = MagicMock()
    client.sync_list_intake.return_value = [_INTAKE_ITEM_1, _INTAKE_ITEM_2]
    client.sync_get_intake_item.return_value = _INTAKE_ITEM_1
    client.sync_create_intake_item.return_value = {
        "id": "intake-new-999",
        "title": "New intake item",
        "status": "pending",
        "priority": "medium",
    }
    client.sync_get_intake_stats.return_value = _INTAKE_STATS
    return client


@pytest.fixture
def patch_client(mock_client):
    """Patch CommandCenterClient.from_env in intake module."""
    with patch(
        "forge_harness.cli_v2.intake._get_client", return_value=mock_client
    ) as p:
        yield p, mock_client


# ===========================================================================
# forge intake list
# ===========================================================================


class TestIntakeList:
    def test_list_shows_table(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "list"])
        assert result.exit_code == 0, result.output
        assert "intake-001" in result.output
        assert "Add OAuth2 support" in result.output

    def test_list_shows_second_item(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "list"])
        assert "intake-002" in result.output
        assert "Fix pagination bug" in result.output

    def test_list_json_output(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "--json", "list"])
        assert result.exit_code == 0, result.output
        data = parse_json_output(result.output)
        assert data["success"] is True
        assert isinstance(data["data"], list)
        assert len(data["data"]) == 2

    def test_list_json_envelope_command(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "--json", "list"])
        data = parse_json_output(result.output)
        assert data["command"] == "forge intake list"

    def test_list_passes_status_filter(self, patch_client):
        _, client = patch_client
        invoke(["intake", "list", "--status", "pending"])
        client.sync_list_intake.assert_called_once_with(status="pending", limit=50)

    def test_list_passes_limit(self, patch_client):
        _, client = patch_client
        invoke(["intake", "list", "--limit", "10"])
        client.sync_list_intake.assert_called_once_with(status=None, limit=10)

    def test_list_empty_result(self, patch_client):
        _, client = patch_client
        client.sync_list_intake.return_value = []
        result = invoke(["intake", "list"])
        assert result.exit_code == 0

    def test_list_empty_json(self, patch_client):
        _, client = patch_client
        client.sync_list_intake.return_value = []
        result = invoke(["intake", "--json", "list"])
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["data"] == []

    def test_list_api_error_exits_1(self):
        with patch(
            "forge_harness.cli_v2.intake._get_client",
            side_effect=ConnectionRefusedError("API offline"),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["intake", "list"])
            assert result.exit_code == 1

    def test_list_shows_total_info(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "list"])
        assert "2" in result.output  # total count shown


# ===========================================================================
# forge intake show ITEM_ID
# ===========================================================================


class TestIntakeShow:
    def test_show_displays_fields(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "show", "intake-001"])
        assert result.exit_code == 0, result.output
        assert "intake-001" in result.output
        assert "Add OAuth2 support" in result.output

    def test_show_displays_status(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "show", "intake-001"])
        assert "pending" in result.output

    def test_show_displays_priority(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "show", "intake-001"])
        assert "high" in result.output

    def test_show_displays_description(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "show", "intake-001"])
        assert "OAuth2 login flow" in result.output

    def test_show_displays_source(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "show", "intake-001"])
        assert "slack" in result.output

    def test_show_displays_tags(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "show", "intake-001"])
        assert "auth" in result.output

    def test_show_json_output(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "--json", "show", "intake-001"])
        assert result.exit_code == 0, result.output
        data = parse_json_output(result.output)
        assert data["success"] is True
        assert data["data"]["id"] == "intake-001"

    def test_show_not_found(self, patch_client):
        _, client = patch_client
        client.sync_get_intake_item.return_value = None
        runner = CliRunner()
        result = runner.invoke(cli, ["intake", "show", "no-such-item"])
        assert result.exit_code != 0

    def test_show_requires_item_id(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["intake", "show"], catch_exceptions=False)
        assert result.exit_code == 2

    def test_show_calls_correct_id(self, patch_client):
        _, client = patch_client
        invoke(["intake", "show", "intake-001"])
        client.sync_get_intake_item.assert_called_once_with("intake-001")

    def test_show_item_without_tags_does_not_crash(self, patch_client):
        _, client = patch_client
        item = dict(_INTAKE_ITEM_1)
        del item["tags"]
        del item["source"]
        client.sync_get_intake_item.return_value = item
        result = invoke(["intake", "show", "intake-001"])
        assert result.exit_code == 0


# ===========================================================================
# forge intake create
# ===========================================================================


class TestIntakeCreate:
    def test_create_basic(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "create", "--title", "Add rate limiting"])
        assert result.exit_code == 0, result.output
        assert "intake-new-999" in result.output or "created" in result.output.lower()

    def test_create_calls_client_with_title(self, patch_client):
        _, client = patch_client
        invoke(["intake", "create", "--title", "Add rate limiting"])
        client.sync_create_intake_item.assert_called_once()
        call_kwargs = client.sync_create_intake_item.call_args
        assert call_kwargs[1]["title"] == "Add rate limiting" or call_kwargs[0][0] == "Add rate limiting"

    def test_create_with_description(self, patch_client):
        _, client = patch_client
        invoke([
            "intake", "create",
            "--title", "Add rate limiting",
            "--description", "Throttle API calls per IP",
        ])
        client.sync_create_intake_item.assert_called_once()

    def test_create_with_priority(self, patch_client):
        _, client = patch_client
        invoke(["intake", "create", "--title", "Urgent bug fix", "--priority", "critical"])
        client.sync_create_intake_item.assert_called_once()

    def test_create_with_source(self, patch_client):
        _, client = patch_client
        invoke(["intake", "create", "--title", "From email", "--source", "email"])
        client.sync_create_intake_item.assert_called_once()

    def test_create_with_tags(self, patch_client):
        _, client = patch_client
        invoke(["intake", "create", "--title", "Tag test", "--tags", "backend,api"])
        client.sync_create_intake_item.assert_called_once()

    def test_create_json_output(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "--json", "create", "--title", "JSON test intake"])
        assert result.exit_code == 0, result.output
        data = parse_json_output(result.output)
        assert data["success"] is True
        assert "data" in data

    def test_create_title_required(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["intake", "create"], catch_exceptions=False)
        assert result.exit_code == 2

    def test_create_title_too_short_exits(self, patch_client):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["intake", "create", "--title", "AB"], catch_exceptions=False
        )
        assert result.exit_code != 0

    def test_create_rejects_invalid_priority(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["intake", "create", "--title", "Test", "--priority", "urgent"],
            catch_exceptions=False,
        )
        assert result.exit_code == 2

    def test_create_api_returns_none_exits(self, patch_client):
        _, client = patch_client
        client.sync_create_intake_item.return_value = None
        runner = CliRunner()
        result = runner.invoke(
            cli, ["intake", "create", "--title", "Should fail"]
        )
        assert result.exit_code != 0


# ===========================================================================
# forge intake stats
# ===========================================================================


class TestIntakeStats:
    def test_stats_shows_table(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "stats"])
        assert result.exit_code == 0, result.output

    def test_stats_shows_total(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "stats"])
        assert "42" in result.output

    def test_stats_shows_pending(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "stats"])
        assert "20" in result.output

    def test_stats_json_output(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "--json", "stats"])
        assert result.exit_code == 0, result.output
        data = parse_json_output(result.output)
        assert data["success"] is True
        assert data["data"]["total"] == 42

    def test_stats_json_envelope_command(self, patch_client):
        _, client = patch_client
        result = invoke(["intake", "--json", "stats"])
        data = parse_json_output(result.output)
        assert data["command"] == "forge intake stats"

    def test_stats_empty_result(self, patch_client):
        _, client = patch_client
        client.sync_get_intake_stats.return_value = {}
        result = invoke(["intake", "stats"])
        assert result.exit_code == 0

    def test_stats_api_error_exits_1(self):
        with patch(
            "forge_harness.cli_v2.intake._get_client",
            side_effect=ConnectionRefusedError("API offline"),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["intake", "stats"])
            assert result.exit_code == 1


# ===========================================================================
# Help text
# ===========================================================================


class TestIntakeHelp:
    def test_group_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["intake", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "show" in result.output
        assert "create" in result.output
        assert "stats" in result.output

    def test_list_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["intake", "list", "--help"])
        assert result.exit_code == 0
        assert "--status" in result.output
        assert "--limit" in result.output

    def test_create_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["intake", "create", "--help"])
        assert result.exit_code == 0
        assert "--title" in result.output
        assert "--description" in result.output
        assert "--priority" in result.output
        assert "--source" in result.output
        assert "--tags" in result.output

    def test_show_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["intake", "show", "--help"])
        assert result.exit_code == 0
        assert "ITEM_ID" in result.output


# ===========================================================================
# CommandCenterClient intake methods (unit level)
# ===========================================================================


class TestCommandCenterClientIntakeMethods:
    """Unit tests for the intake async/sync methods on CommandCenterClient."""

    def test_sync_list_intake_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_list_intake")
        assert callable(client.sync_list_intake)

    def test_sync_get_intake_item_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_get_intake_item")
        assert callable(client.sync_get_intake_item)

    def test_sync_create_intake_item_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_create_intake_item")
        assert callable(client.sync_create_intake_item)

    def test_sync_get_intake_stats_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_get_intake_stats")
        assert callable(client.sync_get_intake_stats)

    def test_async_list_intake_is_coroutine(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert inspect.iscoroutinefunction(client.list_intake)

    def test_async_get_intake_item_is_coroutine(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert inspect.iscoroutinefunction(client.get_intake_item)

    def test_async_create_intake_item_is_coroutine(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert inspect.iscoroutinefunction(client.create_intake_item)

    def test_async_get_intake_stats_is_coroutine(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert inspect.iscoroutinefunction(client.get_intake_stats)

    def _make_response(self, success=True, data=None, error=None):
        from forge_harness.command_center_client import APIResponse

        return APIResponse(success=success, data=data, error=error)

    def test_list_intake_returns_list(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        items = [_INTAKE_ITEM_1, _INTAKE_ITEM_2]
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, {"items": items}),
        ):
            result = asyncio.run(client.list_intake())
            assert isinstance(result, list)
            assert len(result) == 2

    def test_list_intake_empty_on_failure(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(False, error="Service unavailable"),
        ):
            result = asyncio.run(client.list_intake())
            assert result == []

    def test_list_intake_bare_list_response(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        items = [_INTAKE_ITEM_1]
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, items),
        ):
            result = asyncio.run(client.list_intake())
            assert result == items

    def test_get_intake_item_returns_dict(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, _INTAKE_ITEM_1),
        ):
            result = asyncio.run(client.get_intake_item("intake-001"))
            assert result["id"] == "intake-001"

    def test_get_intake_item_returns_none_on_failure(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(False, error="Not found"),
        ):
            result = asyncio.run(client.get_intake_item("no-such"))
            assert result is None

    def test_create_intake_item_returns_dict(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        created = {"id": "intake-new", "title": "New item", "status": "pending"}
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, created),
        ):
            result = asyncio.run(
                client.create_intake_item(title="New item", description="Desc", priority="high")
            )
            assert result["id"] == "intake-new"

    def test_create_intake_item_returns_none_on_failure(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(False, error="Validation error"),
        ):
            result = asyncio.run(
                client.create_intake_item(title="Bad item")
            )
            assert result is None

    def test_get_intake_stats_returns_dict(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, _INTAKE_STATS),
        ):
            result = asyncio.run(client.get_intake_stats())
            assert result["total"] == 42

    def test_get_intake_stats_returns_empty_on_failure(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(False, error="API offline"),
        ):
            result = asyncio.run(client.get_intake_stats())
            assert result == {}

    def test_sync_list_intake_wrapper(self):
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(client, "list_intake", return_value=None):
            with patch.object(client, "_run_sync", return_value=[_INTAKE_ITEM_1]):
                result = client.sync_list_intake(status="pending", limit=10)
                assert result == [_INTAKE_ITEM_1]

    def test_sync_get_intake_stats_wrapper(self):
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(client, "get_intake_stats", return_value=None):
            with patch.object(client, "_run_sync", return_value=_INTAKE_STATS):
                result = client.sync_get_intake_stats()
                assert result["total"] == 42
