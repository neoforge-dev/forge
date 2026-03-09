"""
Tests for forge balancer CLI v2 commands.

Covers:
    status      — forge balancer status
    show        — forge balancer show AGENT_ID
    recommend   — forge balancer recommend [--task-type T] [--domain D] [--count N]

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

_BALANCER_AGENT_1 = {
    "agent_id": "agent-backend-001",
    "model": "claude-sonnet-4-6",
    "role": "backend",
    "status": "available",
    "load": 0.3,
    "load_factor": 0.3,
    "active_tasks": 2,
    "capacity": 6,
    "node": "sati",
    "capabilities": ["backend", "testing"],
    "registered_at": "2026-02-23T08:00:00",
    "last_updated": "2026-02-23T10:30:00",
}

_BALANCER_AGENT_2 = {
    "agent_id": "agent-content-002",
    "model": "gemini-pro",
    "role": "content",
    "status": "busy",
    "load": 0.85,
    "load_factor": 0.85,
    "active_tasks": 5,
    "capacity": 6,
    "node": "nova",
    "capabilities": ["content", "docs"],
    "registered_at": "2026-02-23T09:00:00",
    "last_updated": "2026-02-23T10:45:00",
}

_BALANCER_RECOMMEND_RESULT = {
    "recommendations": [
        {
            "agent_id": "agent-backend-001",
            "model": "claude-sonnet-4-6",
            "load": 0.3,
            "score": 0.92,
            "reason": "Low load, strong backend capabilities",
        }
    ],
    "task_type": "backend",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    """MagicMock standing in for CommandCenterClient."""
    client = MagicMock()
    client.sync_get_balancer_agents.return_value = [_BALANCER_AGENT_1, _BALANCER_AGENT_2]
    client.sync_get_balancer_agent.return_value = _BALANCER_AGENT_1
    client.sync_balancer_recommend.return_value = _BALANCER_RECOMMEND_RESULT
    return client


@pytest.fixture
def patch_client(mock_client):
    """Patch _get_client in balancer module."""
    with patch(
        "forge_harness.cli_v2.balancer._get_client", return_value=mock_client
    ) as p:
        yield p, mock_client


# ===========================================================================
# forge balancer status
# ===========================================================================


class TestBalancerStatus:
    def test_status_shows_table(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "status"])
        assert result.exit_code == 0, result.output
        # Rich may truncate long IDs — check the prefix that survives
        assert "agent-back" in result.output
        assert "agent-cont" in result.output

    def test_status_shows_load_bar(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "status"])
        # Load bar contains '#' characters and a percentage
        assert "#" in result.output or "%" in result.output

    def test_status_shows_agent_model(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "status"])
        # Rich may truncate — check prefix that survives column width
        assert "claude-son" in result.output

    def test_status_shows_agent_status(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "status"])
        # Rich may truncate "available" to "availa" — check prefix
        assert "availa" in result.output
        assert "busy" in result.output

    def test_status_json_output(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "--json", "status"])
        assert result.exit_code == 0, result.output
        data = parse_json_output(result.output)
        assert data["success"] is True
        assert isinstance(data["data"], list)
        assert len(data["data"]) == 2

    def test_status_json_envelope_command(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "--json", "status"])
        data = parse_json_output(result.output)
        assert data["command"] == "forge balancer status"

    def test_status_empty_fleet(self, patch_client):
        _, client = patch_client
        client.sync_get_balancer_agents.return_value = []
        result = invoke(["balancer", "status"])
        assert result.exit_code == 0

    def test_status_empty_json(self, patch_client):
        _, client = patch_client
        client.sync_get_balancer_agents.return_value = []
        result = invoke(["balancer", "--json", "status"])
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["data"] == []

    def test_status_api_error_exits_1(self):
        with patch(
            "forge_harness.cli_v2.balancer._get_client",
            side_effect=ConnectionRefusedError("API offline"),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["balancer", "status"])
            assert result.exit_code == 1

    def test_status_shows_total_count(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "status"])
        assert "2" in result.output  # total count shown

    def test_status_calls_get_balancer_agents(self, patch_client):
        _, client = patch_client
        invoke(["balancer", "status"])
        client.sync_get_balancer_agents.assert_called_once()


# ===========================================================================
# forge balancer show AGENT_ID
# ===========================================================================


class TestBalancerShow:
    def test_show_displays_agent_id(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "show", "agent-backend-001"])
        assert result.exit_code == 0, result.output
        assert "agent-backend-001" in result.output

    def test_show_displays_model(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "show", "agent-backend-001"])
        assert "claude-sonnet-4-6" in result.output

    def test_show_displays_status(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "show", "agent-backend-001"])
        assert "available" in result.output

    def test_show_displays_load(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "show", "agent-backend-001"])
        # Load should be shown as bar and/or percentage
        assert "30" in result.output or "0.3" in result.output or "#" in result.output

    def test_show_displays_node(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "show", "agent-backend-001"])
        assert "sati" in result.output

    def test_show_displays_capabilities(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "show", "agent-backend-001"])
        assert "backend" in result.output
        assert "testing" in result.output

    def test_show_json_output(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "--json", "show", "agent-backend-001"])
        assert result.exit_code == 0, result.output
        data = parse_json_output(result.output)
        assert data["success"] is True
        assert data["data"]["agent_id"] == "agent-backend-001"

    def test_show_not_found(self, patch_client):
        _, client = patch_client
        client.sync_get_balancer_agent.return_value = None
        runner = CliRunner()
        result = runner.invoke(cli, ["balancer", "show", "no-such-agent"])
        assert result.exit_code != 0

    def test_show_requires_agent_id(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["balancer", "show"], catch_exceptions=False)
        assert result.exit_code == 2

    def test_show_calls_correct_id(self, patch_client):
        _, client = patch_client
        invoke(["balancer", "show", "agent-backend-001"])
        client.sync_get_balancer_agent.assert_called_once_with("agent-backend-001")

    def test_show_missing_optional_fields_does_not_crash(self, patch_client):
        _, client = patch_client
        agent = {
            "agent_id": "bare-agent",
            "status": "available",
            "load": 0.1,
        }
        client.sync_get_balancer_agent.return_value = agent
        result = invoke(["balancer", "show", "bare-agent"])
        assert result.exit_code == 0

    def test_show_capabilities_as_string(self, patch_client):
        _, client = patch_client
        agent = dict(_BALANCER_AGENT_1)
        agent["capabilities"] = "backend,testing"
        client.sync_get_balancer_agent.return_value = agent
        result = invoke(["balancer", "show", "agent-backend-001"])
        assert result.exit_code == 0


# ===========================================================================
# forge balancer recommend
# ===========================================================================


class TestBalancerRecommend:
    def test_recommend_shows_table(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "recommend"])
        assert result.exit_code == 0, result.output
        # Rich may truncate long IDs — check the prefix that survives
        assert "agent-backen" in result.output or "agent-backend" in result.output

    def test_recommend_with_task_type(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "recommend", "--task-type", "backend"])
        assert result.exit_code == 0, result.output
        client.sync_balancer_recommend.assert_called_once()
        call_kwargs = client.sync_balancer_recommend.call_args
        assert call_kwargs[1].get("task_type") == "backend" or (
            call_kwargs[0] and call_kwargs[0][0] == "backend"
        )

    def test_recommend_with_domain(self, patch_client):
        _, client = patch_client
        invoke(["balancer", "recommend", "--domain", "codeswiftr-com"])
        client.sync_balancer_recommend.assert_called_once()

    def test_recommend_with_count(self, patch_client):
        _, client = patch_client
        invoke(["balancer", "recommend", "--count", "3"])
        client.sync_balancer_recommend.assert_called_once()

    def test_recommend_shows_score(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "recommend"])
        assert "0.92" in result.output

    def test_recommend_shows_reason(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "recommend"])
        assert "Low load" in result.output

    def test_recommend_shows_task_type_info(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "recommend", "--task-type", "backend"])
        assert "backend" in result.output

    def test_recommend_json_output(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "--json", "recommend"])
        assert result.exit_code == 0, result.output
        data = parse_json_output(result.output)
        assert data["success"] is True
        assert "data" in data

    def test_recommend_json_envelope_command(self, patch_client):
        _, client = patch_client
        result = invoke(["balancer", "--json", "recommend"])
        data = parse_json_output(result.output)
        assert data["command"] == "forge balancer recommend"

    def test_recommend_empty_result(self, patch_client):
        _, client = patch_client
        client.sync_balancer_recommend.return_value = {}
        result = invoke(["balancer", "recommend"])
        assert result.exit_code == 0  # Should degrade gracefully

    def test_recommend_empty_json(self, patch_client):
        _, client = patch_client
        client.sync_balancer_recommend.return_value = {}
        result = invoke(["balancer", "--json", "recommend"])
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["success"] is True

    def test_recommend_api_error_exits_1(self):
        with patch(
            "forge_harness.cli_v2.balancer._get_client",
            side_effect=ConnectionRefusedError("API offline"),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["balancer", "recommend"])
            assert result.exit_code == 1

    def test_recommend_single_agent_result(self, patch_client):
        """Balancer can also return a single-agent dict without 'recommendations' key."""
        _, client = patch_client
        client.sync_balancer_recommend.return_value = {
            "agent_id": "agent-backend-001",
            "load": 0.3,
            "score": 0.9,
        }
        result = invoke(["balancer", "recommend"])
        assert result.exit_code == 0

    def test_recommend_multiple_results(self, patch_client):
        _, client = patch_client
        client.sync_balancer_recommend.return_value = {
            "recommendations": [
                {"agent_id": "agent-a", "load": 0.2, "score": 0.95, "reason": "Best fit"},
                {"agent_id": "agent-b", "load": 0.5, "score": 0.7, "reason": "Second choice"},
            ]
        }
        result = invoke(["balancer", "recommend", "--count", "2"])
        assert result.exit_code == 0, result.output
        assert "agent-a" in result.output
        assert "agent-b" in result.output


# ===========================================================================
# Help text
# ===========================================================================


class TestBalancerHelp:
    def test_group_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["balancer", "--help"])
        assert result.exit_code == 0
        assert "status" in result.output
        assert "show" in result.output
        assert "recommend" in result.output

    def test_status_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["balancer", "status", "--help"])
        assert result.exit_code == 0

    def test_show_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["balancer", "show", "--help"])
        assert result.exit_code == 0
        assert "AGENT_ID" in result.output

    def test_recommend_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["balancer", "recommend", "--help"])
        assert result.exit_code == 0
        assert "--task-type" in result.output
        assert "--domain" in result.output
        assert "--count" in result.output


# ===========================================================================
# _load_bar helper unit tests
# ===========================================================================


class TestLoadBar:
    def test_zero_load(self):
        from forge_harness.cli_v2.balancer import _load_bar

        bar = _load_bar(0.0)
        assert "0%" in bar
        assert "green" in bar

    def test_full_load(self):
        from forge_harness.cli_v2.balancer import _load_bar

        bar = _load_bar(1.0)
        assert "100%" in bar
        assert "red" in bar

    def test_medium_load_yellow(self):
        from forge_harness.cli_v2.balancer import _load_bar

        bar = _load_bar(0.65)
        assert "yellow" in bar

    def test_high_load_red(self):
        from forge_harness.cli_v2.balancer import _load_bar

        bar = _load_bar(0.9)
        assert "red" in bar

    def test_low_load_green(self):
        from forge_harness.cli_v2.balancer import _load_bar

        bar = _load_bar(0.2)
        assert "green" in bar

    def test_clamps_above_one(self):
        from forge_harness.cli_v2.balancer import _load_bar

        bar = _load_bar(2.5)
        assert "100%" in bar

    def test_clamps_below_zero(self):
        from forge_harness.cli_v2.balancer import _load_bar

        bar = _load_bar(-0.5)
        assert "0%" in bar

    def test_custom_width(self):
        from forge_harness.cli_v2.balancer import _load_bar

        bar = _load_bar(0.5, width=20)
        # Width affects the number of # and - characters
        assert "#" in bar or "-" in bar


# ===========================================================================
# CommandCenterClient balancer methods (unit level)
# ===========================================================================


class TestCommandCenterClientBalancerMethods:
    """Unit tests for the balancer async/sync methods on CommandCenterClient."""

    def test_sync_get_balancer_agents_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_get_balancer_agents")
        assert callable(client.sync_get_balancer_agents)

    def test_sync_register_balancer_agent_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_register_balancer_agent")
        assert callable(client.sync_register_balancer_agent)

    def test_sync_get_balancer_agent_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_get_balancer_agent")
        assert callable(client.sync_get_balancer_agent)

    def test_sync_update_balancer_agent_load_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_update_balancer_agent_load")
        assert callable(client.sync_update_balancer_agent_load)

    def test_sync_balancer_recommend_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_balancer_recommend")
        assert callable(client.sync_balancer_recommend)

    def test_async_get_balancer_agents_is_coroutine(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert inspect.iscoroutinefunction(client.get_balancer_agents)

    def test_async_get_balancer_agent_is_coroutine(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert inspect.iscoroutinefunction(client.get_balancer_agent)

    def test_async_balancer_recommend_is_coroutine(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert inspect.iscoroutinefunction(client.balancer_recommend)

    def _make_response(self, success=True, data=None, error=None):
        from forge_harness.command_center_client import APIResponse

        return APIResponse(success=success, data=data, error=error)

    def test_get_balancer_agents_returns_list(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        agents = [_BALANCER_AGENT_1, _BALANCER_AGENT_2]
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, {"agents": agents}),
        ):
            result = asyncio.run(client.get_balancer_agents())
            assert isinstance(result, list)
            assert len(result) == 2

    def test_get_balancer_agents_empty_on_failure(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(False, error="API offline"),
        ):
            result = asyncio.run(client.get_balancer_agents())
            assert result == []

    def test_get_balancer_agents_bare_list_response(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        agents = [_BALANCER_AGENT_1]
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, agents),
        ):
            result = asyncio.run(client.get_balancer_agents())
            assert result == agents

    def test_get_balancer_agent_returns_dict(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, _BALANCER_AGENT_1),
        ):
            result = asyncio.run(client.get_balancer_agent("agent-backend-001"))
            assert result["agent_id"] == "agent-backend-001"

    def test_get_balancer_agent_returns_none_on_failure(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(False, error="Not found"),
        ):
            result = asyncio.run(client.get_balancer_agent("no-such"))
            assert result is None

    def test_balancer_recommend_returns_dict(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, _BALANCER_RECOMMEND_RESULT),
        ):
            result = asyncio.run(client.balancer_recommend(task_type="backend"))
            assert "recommendations" in result

    def test_balancer_recommend_returns_empty_on_failure(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(False, error="Balancer offline"),
        ):
            result = asyncio.run(client.balancer_recommend())
            assert result == {}

    def test_update_balancer_agent_load_returns_dict(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        updated = dict(_BALANCER_AGENT_1)
        updated["load"] = 0.6
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, updated),
        ):
            result = asyncio.run(
                client.update_balancer_agent_load("agent-backend-001", 0.6, active_tasks=4)
            )
            assert result["load"] == 0.6

    def test_update_balancer_agent_load_returns_none_on_failure(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(False, error="Agent not found"),
        ):
            result = asyncio.run(
                client.update_balancer_agent_load("ghost-agent", 0.5)
            )
            assert result is None

    def test_register_balancer_agent_returns_dict(self):
        import asyncio
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        created = {"agent_id": "new-agent", "status": "available"}
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, created),
        ):
            result = asyncio.run(
                client.register_balancer_agent(
                    "new-agent",
                    model="claude-sonnet-4-6",
                    capabilities=["backend"],
                    capacity=5,
                    node="prya",
                )
            )
            assert result["agent_id"] == "new-agent"

    def test_sync_get_balancer_agents_wrapper(self):
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(client, "get_balancer_agents", return_value=None):
            with patch.object(
                client, "_run_sync", return_value=[_BALANCER_AGENT_1]
            ):
                result = client.sync_get_balancer_agents()
                assert result == [_BALANCER_AGENT_1]

    def test_sync_balancer_recommend_wrapper(self):
        from unittest.mock import patch

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        with patch.object(client, "balancer_recommend", return_value=None):
            with patch.object(
                client, "_run_sync", return_value=_BALANCER_RECOMMEND_RESULT
            ):
                result = client.sync_balancer_recommend(task_type="backend")
                assert "recommendations" in result
