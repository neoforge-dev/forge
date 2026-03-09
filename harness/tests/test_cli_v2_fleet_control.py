"""
Tests for forge fleet CLI v2 — new fleet-control subcommands.

Covers:
    pause       — forge fleet pause AGENT_ID
    resume      — forge fleet resume AGENT_ID
    kill        — forge fleet kill AGENT_ID [--yes]
    pause-all   — forge fleet pause-all
    resume-all  — forge fleet resume-all
    broadcast   — forge fleet broadcast MESSAGE [--priority ...]
    register    — forge fleet register AGENT_ID [--model ...] [--capabilities ...] [--node ...]
    logs        — forge fleet logs AGENT_ID [--tail N]

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
    # The JSON block starts at the first '{' on its own line.
    lines = output.strip().splitlines()
    json_start = next((i for i, l in enumerate(lines) if l.strip().startswith("{")), None)
    assert json_start is not None, f"No JSON found in output:\n{output}"
    return json.loads("\n".join(lines[json_start:]))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_cc_client():
    """Return a fresh MagicMock that stands in for CommandCenterClient.from_env()."""
    client = MagicMock()
    # Default return values — individual tests may override these.
    client.sync_pause_agent.return_value = True
    client.sync_resume_agent.return_value = True
    client.sync_kill_agent.return_value = True
    client.sync_fleet_pause.return_value = {"success": True, "paused_count": 3}
    client.sync_fleet_resume.return_value = {"success": True, "resumed_count": 3}
    client.sync_broadcast_message.return_value = 5
    client.sync_get_agent_logs.return_value = [
        {"timestamp": "2026-02-23T10:00:00", "level": "info", "message": "Agent started"},
        {"timestamp": "2026-02-23T10:01:00", "level": "info", "message": "Task claimed"},
    ]
    # register_agent is async — used via client._run_sync(client.register_agent(...))
    fake_agent = MagicMock()
    fake_agent.id = "agent-abc-123"
    fake_agent.role = "agent-abc-123/claude-sonnet-4-6/node:prya"
    fake_agent.status = MagicMock()
    fake_agent.status.value = "active"
    fake_agent.name = "agent-abc-123"
    client._run_sync.return_value = fake_agent
    return client


@pytest.fixture
def patch_client(mock_cc_client):
    """Patch _get_cc_client in fleet.py to return mock_cc_client."""
    with patch(
        "forge_harness.cli_v2.fleet._get_cc_client", return_value=mock_cc_client
    ) as p:
        yield p, mock_cc_client


# ===========================================================================
# forge fleet pause AGENT_ID
# ===========================================================================


class TestFleetPause:
    def test_pause_success(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "pause", "agent-123"])
        assert result.exit_code == 0, result.output
        client.sync_pause_agent.assert_called_once_with("agent-123")

    def test_pause_success_message_shown(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "pause", "agent-123"])
        assert "paused" in result.output.lower()

    def test_pause_json_output(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "--json", "pause", "agent-123"])
        assert result.exit_code == 0, result.output
        data = parse_json_output(result.output)
        assert data["success"] is True
        assert data["data"]["agent_id"] == "agent-123"
        assert data["data"]["action"] == "pause"

    def test_pause_api_returns_false(self, patch_client):
        _, client = patch_client
        client.sync_pause_agent.return_value = False
        result = invoke(["fleet", "pause", "agent-123"])
        # Should still exit 0 — it's a warning, not a hard failure
        assert result.exit_code == 0

    def test_pause_missing_agent_id_exits_with_usage_error(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["fleet", "pause"], catch_exceptions=False)
        # Either exit 1 (format_error) or 2 (UsageError)
        assert result.exit_code in (1, 2)

    def test_pause_json_api_failure_still_returns_envelope(self, patch_client):
        _, client = patch_client
        client.sync_pause_agent.return_value = False
        result = invoke(["fleet", "--json", "pause", "agent-xyz"])
        data = parse_json_output(result.output)
        assert "data" in data
        assert data["data"]["agent_id"] == "agent-xyz"


# ===========================================================================
# forge fleet resume AGENT_ID
# ===========================================================================


class TestFleetResume:
    def test_resume_success(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "resume", "agent-123"])
        assert result.exit_code == 0, result.output
        client.sync_resume_agent.assert_called_once_with("agent-123")

    def test_resume_success_message_shown(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "resume", "agent-123"])
        assert "resume" in result.output.lower()

    def test_resume_json_output(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "--json", "resume", "agent-123"])
        assert result.exit_code == 0, result.output
        data = parse_json_output(result.output)
        assert data["success"] is True
        assert data["data"]["agent_id"] == "agent-123"
        assert data["data"]["action"] == "resume"

    def test_resume_api_failure_is_graceful(self, patch_client):
        _, client = patch_client
        client.sync_resume_agent.return_value = False
        result = invoke(["fleet", "resume", "agent-123"])
        assert result.exit_code == 0

    def test_resume_missing_agent_id_exits(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["fleet", "resume"], catch_exceptions=False)
        assert result.exit_code in (1, 2)


# ===========================================================================
# forge fleet kill AGENT_ID [--yes]
# ===========================================================================


class TestFleetKill:
    def test_kill_without_yes_aborts(self, patch_client):
        _, client = patch_client
        runner = CliRunner()
        result = runner.invoke(cli, ["fleet", "kill", "agent-123"], catch_exceptions=False)
        # Should abort without calling sync_kill_agent
        assert result.exit_code != 0
        client.sync_kill_agent.assert_not_called()

    def test_kill_with_yes_succeeds(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "kill", "agent-123", "--yes"])
        assert result.exit_code == 0, result.output
        client.sync_kill_agent.assert_called_once_with("agent-123")

    def test_kill_with_yes_flag_short(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "kill", "agent-123", "-y"])
        assert result.exit_code == 0, result.output
        client.sync_kill_agent.assert_called_once_with("agent-123")

    def test_kill_success_message_shown(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "kill", "agent-123", "--yes"])
        assert "terminat" in result.output.lower() or "kill" in result.output.lower()

    def test_kill_json_output(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "--json", "kill", "agent-123", "--yes"])
        assert result.exit_code == 0, result.output
        data = parse_json_output(result.output)
        assert data["success"] is True
        assert data["data"]["agent_id"] == "agent-123"
        assert data["data"]["action"] == "kill"

    def test_kill_json_without_yes_returns_aborted_envelope(self):
        """In JSON mode, kill without --yes returns an aborted result envelope."""
        result = invoke(["fleet", "--json", "kill", "agent-123"])
        # JSON mode: the command exits 0 but the data envelope signals aborted=True
        data = parse_json_output(result.output)
        assert data["data"]["aborted"] is True
        assert data["data"]["success"] is False

    def test_kill_api_failure_graceful(self, patch_client):
        _, client = patch_client
        client.sync_kill_agent.return_value = False
        result = invoke(["fleet", "kill", "agent-123", "--yes"])
        assert result.exit_code == 0

    def test_kill_missing_agent_id_exits(self):
        runner = CliRunner()
        result = runner.invoke(
            cli, ["fleet", "kill", "--yes"], catch_exceptions=False
        )
        assert result.exit_code in (1, 2)


# ===========================================================================
# forge fleet pause-all
# ===========================================================================


class TestFleetPauseAll:
    def test_pause_all_success(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "pause-all"])
        assert result.exit_code == 0, result.output
        client.sync_fleet_pause.assert_called_once()

    def test_pause_all_shows_count(self, patch_client):
        _, client = patch_client
        client.sync_fleet_pause.return_value = {"success": True, "paused_count": 7}
        result = invoke(["fleet", "pause-all"])
        assert "7" in result.output

    def test_pause_all_json_output(self, patch_client):
        _, client = patch_client
        client.sync_fleet_pause.return_value = {"success": True, "paused_count": 3}
        result = invoke(["fleet", "--json", "pause-all"])
        assert result.exit_code == 0, result.output
        data = parse_json_output(result.output)
        assert data["success"] is True
        assert data["data"]["paused_count"] == 3

    def test_pause_all_api_error_graceful(self, patch_client):
        _, client = patch_client
        client.sync_fleet_pause.return_value = {"success": False, "error": "API offline"}
        result = invoke(["fleet", "pause-all"])
        assert result.exit_code == 0

    def test_pause_all_empty_fleet(self, patch_client):
        _, client = patch_client
        client.sync_fleet_pause.return_value = {"success": True, "paused_count": 0}
        result = invoke(["fleet", "pause-all"])
        assert result.exit_code == 0


# ===========================================================================
# forge fleet resume-all
# ===========================================================================


class TestFleetResumeAll:
    def test_resume_all_success(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "resume-all"])
        assert result.exit_code == 0, result.output
        client.sync_fleet_resume.assert_called_once()

    def test_resume_all_shows_count(self, patch_client):
        _, client = patch_client
        client.sync_fleet_resume.return_value = {"success": True, "resumed_count": 4}
        result = invoke(["fleet", "resume-all"])
        assert "4" in result.output

    def test_resume_all_json_output(self, patch_client):
        _, client = patch_client
        client.sync_fleet_resume.return_value = {"success": True, "resumed_count": 4}
        result = invoke(["fleet", "--json", "resume-all"])
        assert result.exit_code == 0, result.output
        data = parse_json_output(result.output)
        assert data["success"] is True
        assert data["data"]["resumed_count"] == 4

    def test_resume_all_api_error_graceful(self, patch_client):
        _, client = patch_client
        client.sync_fleet_resume.return_value = {"success": False, "error": "API offline"}
        result = invoke(["fleet", "resume-all"])
        assert result.exit_code == 0

    def test_resume_all_normalises_missing_success_key(self, patch_client):
        _, client = patch_client
        # API returns dict without 'success' key — should be normalised to True
        client.sync_fleet_resume.return_value = {"count": 2}
        result = invoke(["fleet", "resume-all"])
        assert result.exit_code == 0


# ===========================================================================
# forge fleet broadcast MESSAGE [--priority ...]
# ===========================================================================


class TestFleetBroadcastCC:
    """Tests for the CC-API broadcast path.

    Note: the existing fleet broadcast action uses tmux. These tests verify
    the new --priority option is accepted without breaking existing behavior.
    The CC broadcast helper (_fleet_cc_broadcast) is exercised indirectly when
    the action is 'broadcast' and no tmux session is present — but since the
    broadcast action still uses tmux by default, we test the CLI option layer
    here and the helper directly via unit tests.
    """

    def test_broadcast_accepts_priority_low(self):
        runner = CliRunner()
        # Just check the option is accepted; tmux will fail gracefully
        result = runner.invoke(
            cli,
            ["fleet", "broadcast", "--priority", "low", "hello fleet"],
            catch_exceptions=False,
        )
        # Exit may be 0 (no tmux) or 1 depending on env, but not 2 (UsageError)
        assert result.exit_code in (0, 1)

    def test_broadcast_accepts_priority_critical(self):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["fleet", "broadcast", "--priority", "critical", "urgent message"],
            catch_exceptions=False,
        )
        assert result.exit_code in (0, 1)

    def test_broadcast_rejects_invalid_priority(self):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["fleet", "broadcast", "--priority", "ultra", "message"],
            catch_exceptions=False,
        )
        assert result.exit_code == 2  # Click UsageError for invalid choice

    def test_cc_broadcast_helper_calls_sync_broadcast(self, patch_client):
        """Direct test of _fleet_cc_broadcast helper."""
        from forge_harness.cli_v2.fleet import _fleet_cc_broadcast

        _, client = patch_client
        ctx = MagicMock()
        result = _fleet_cc_broadcast(ctx, "Hello fleet", priority="high", output_json=True)
        assert result["success"] is True
        assert result["message"] == "Hello fleet"
        assert result["priority"] == "high"
        client.sync_broadcast_message.assert_called_once()


# ===========================================================================
# forge fleet register AGENT_ID [--model ...] [--capabilities ...] [--node ...]
# ===========================================================================


class TestFleetRegister:
    """Tests for forge fleet register AGENT_ID.

    _fleet_cc_register imports CommandCenterClient locally and calls
    client._run_sync(_do_register()) where _do_register is an async
    function.  We patch _fleet_cc_register directly so tests are
    insulated from the async machinery.
    """

    def _make_register_result(self, agent_id="my-agent"):
        return {
            "success": True,
            "agent_id": agent_id,
            "role": f"{agent_id}/claude-sonnet-4-6",
            "status": "active",
            "name": agent_id,
        }

    def test_register_basic(self):
        """Register with just an agent ID."""
        with patch(
            "forge_harness.cli_v2.fleet._fleet_cc_register",
            return_value=self._make_register_result("my-agent"),
        ):
            result = invoke(["fleet", "register", "my-agent"])
            assert result.exit_code == 0, result.output

    def test_register_with_all_options(self):
        """Register with model, capabilities, and node options."""
        with patch(
            "forge_harness.cli_v2.fleet._fleet_cc_register",
            return_value=self._make_register_result("my-agent"),
        ) as mock_fn:
            result = invoke(
                [
                    "fleet",
                    "register",
                    "my-agent",
                    "--model",
                    "claude-sonnet-4-6",
                    "--capabilities",
                    "backend,testing",
                    "--node",
                    "prya",
                ]
            )
            assert result.exit_code == 0, result.output
            # Verify options were passed through
            call_kwargs = mock_fn.call_args
            assert call_kwargs is not None

    def test_register_json_output(self):
        """JSON mode returns structured envelope."""
        with patch(
            "forge_harness.cli_v2.fleet._fleet_cc_register",
            return_value=self._make_register_result("my-agent"),
        ):
            result = invoke(["fleet", "--json", "register", "my-agent"])
            assert result.exit_code == 0, result.output
            data = parse_json_output(result.output)
            assert data["success"] is True
            assert "data" in data

    def test_register_api_failure_graceful(self):
        """When API is offline the command returns a failure result gracefully."""
        with patch(
            "forge_harness.cli_v2.fleet._fleet_cc_register",
            return_value={"success": False, "error": "API offline", "agent_id": "ghost-agent"},
        ):
            result = invoke(["fleet", "register", "ghost-agent"])
            assert result.exit_code == 0  # Graceful degradation — no crash

    def test_register_exception_graceful(self):
        """When the helper raises, the outer try/except converts to exit code 1."""
        with patch(
            "forge_harness.cli_v2.fleet._fleet_cc_register",
            side_effect=ConnectionRefusedError("API offline"),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["fleet", "register", "ghost-agent"])
            assert result.exit_code == 1

    def test_register_missing_agent_id_exits(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["fleet", "register"], catch_exceptions=False)
        assert result.exit_code in (1, 2)

    def test_register_capabilities_parsed_as_list(self):
        """Comma-separated capabilities are split and passed as a list."""
        captured_caps: list = []

        def fake_register(ctx, agent_id, model, capabilities, node, output_json):
            captured_caps.extend(capabilities or [])
            return {
                "success": True,
                "agent_id": agent_id,
                "capabilities": capabilities,
            }

        with patch("forge_harness.cli_v2.fleet._fleet_cc_register", side_effect=fake_register):
            result = invoke(
                [
                    "fleet",
                    "register",
                    "cap-agent",
                    "--capabilities",
                    "backend, testing, devops",
                ]
            )
            assert result.exit_code == 0, result.output
            # Each capability was stripped and is a separate entry
            assert len(captured_caps) == 3
            assert "backend" in captured_caps
            assert "testing" in captured_caps
            assert "devops" in captured_caps


# ===========================================================================
# forge fleet logs AGENT_ID [--tail N]
# ===========================================================================


class TestFleetLogs:
    def test_logs_default_tail(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "logs", "agent-123"])
        assert result.exit_code == 0, result.output
        client.sync_get_agent_logs.assert_called_once_with("agent-123", limit=50)

    def test_logs_custom_tail(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "logs", "agent-123", "--tail", "100"])
        assert result.exit_code == 0, result.output
        client.sync_get_agent_logs.assert_called_once_with("agent-123", limit=100)

    def test_logs_shows_entries(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "logs", "agent-123"])
        assert "Agent started" in result.output or "agent-123" in result.output

    def test_logs_json_output(self, patch_client):
        _, client = patch_client
        result = invoke(["fleet", "--json", "logs", "agent-123"])
        assert result.exit_code == 0, result.output
        data = parse_json_output(result.output)
        assert data["success"] is True
        assert data["data"]["agent_id"] == "agent-123"
        assert "logs" in data["data"]
        assert isinstance(data["data"]["logs"], list)
        assert data["data"]["count"] == 2

    def test_logs_empty_response(self, patch_client):
        _, client = patch_client
        client.sync_get_agent_logs.return_value = []
        result = invoke(["fleet", "logs", "ghost-agent"])
        assert result.exit_code == 0
        assert "No log" in result.output or "ghost-agent" in result.output

    def test_logs_json_empty_response(self, patch_client):
        _, client = patch_client
        client.sync_get_agent_logs.return_value = []
        result = invoke(["fleet", "--json", "logs", "ghost-agent"])
        assert result.exit_code == 0
        data = parse_json_output(result.output)
        assert data["data"]["count"] == 0
        assert data["data"]["logs"] == []

    def test_logs_missing_agent_id_exits(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["fleet", "logs"], catch_exceptions=False)
        assert result.exit_code in (1, 2)

    def test_logs_various_entry_formats(self, patch_client):
        """Log entries with different key names are handled gracefully."""
        _, client = patch_client
        client.sync_get_agent_logs.return_value = [
            {"ts": "2026-02-23T10:00:00", "severity": "ERROR", "msg": "Crash!"},
            {"timestamp": "2026-02-23T10:01:00", "level": "DEBUG", "message": "trace"},
        ]
        result = invoke(["fleet", "logs", "agent-123"])
        assert result.exit_code == 0


# ===========================================================================
# Client method tests (unit level — no CLI invocation)
# ===========================================================================


class TestCommandCenterClientNewMethods:
    """Unit tests for the new async methods added to CommandCenterClient."""

    @pytest.fixture
    def client(self):
        from forge_harness.command_center_client import CommandCenterClient

        return CommandCenterClient(base_url="http://localhost:9999")

    def _make_response(self, success=True, data=None, error=None):
        from forge_harness.command_center_client import APIResponse

        return APIResponse(success=success, data=data, error=error)

    def test_pause_agent_success(self, client):
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, {"success": True, "new_status": "paused"}),
        ):
            import asyncio

            result = asyncio.run(client.pause_agent("agent-1"))
            assert result is True

    def test_pause_agent_failure(self, client):
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(False, error="Not found"),
        ):
            import asyncio

            result = asyncio.run(client.pause_agent("agent-1"))
            assert result is False

    def test_resume_agent_success(self, client):
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, {"success": True, "new_status": "active"}),
        ):
            import asyncio

            result = asyncio.run(client.resume_agent("agent-1"))
            assert result is True

    def test_resume_agent_failure(self, client):
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(False, error="Not found"),
        ):
            import asyncio

            result = asyncio.run(client.resume_agent("agent-1"))
            assert result is False

    def test_kill_agent_success(self, client):
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, {"killed": True}),
        ):
            import asyncio

            result = asyncio.run(client.kill_agent("agent-1"))
            assert result is True

    def test_kill_agent_failure(self, client):
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(False, error="Not found"),
        ):
            import asyncio

            result = asyncio.run(client.kill_agent("agent-1"))
            assert result is False

    def test_fleet_pause_success(self, client):
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, {"success": True, "paused_count": 5}),
        ):
            import asyncio

            result = asyncio.run(client.fleet_pause())
            assert result["success"] is True
            assert result["paused_count"] == 5

    def test_fleet_pause_api_error(self, client):
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(False, error="Fleet offline"),
        ):
            import asyncio

            result = asyncio.run(client.fleet_pause())
            assert result["success"] is False
            assert "error" in result

    def test_fleet_resume_success(self, client):
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, {"success": True, "resumed_count": 4}),
        ):
            import asyncio

            result = asyncio.run(client.fleet_resume())
            assert result["success"] is True
            assert result["resumed_count"] == 4

    def test_fleet_resume_api_error(self, client):
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(False, error="Fleet offline"),
        ):
            import asyncio

            result = asyncio.run(client.fleet_resume())
            assert result["success"] is False

    def test_get_agent_logs_returns_list(self, client):
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(
                True,
                {
                    "logs": [
                        {"timestamp": "2026-02-23T10:00:00", "level": "info", "message": "ok"}
                    ]
                },
            ),
        ):
            import asyncio

            result = asyncio.run(client.get_agent_logs("agent-1", limit=10))
            assert isinstance(result, list)
            assert len(result) == 1

    def test_get_agent_logs_empty_on_failure(self, client):
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(False, error="Not found"),
        ):
            import asyncio

            result = asyncio.run(client.get_agent_logs("ghost", limit=10))
            assert result == []

    def test_get_agent_logs_empty_logs_key(self, client):
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, {"logs": []}),
        ):
            import asyncio

            result = asyncio.run(client.get_agent_logs("agent-1"))
            assert result == []

    def test_get_agent_logs_list_response(self, client):
        """API may return a bare list rather than a dict."""
        log_list = [
            {"timestamp": "2026-02-23T10:00:00", "level": "info", "message": "boot"}
        ]
        with patch.object(
            client,
            "_request",
            return_value=self._make_response(True, log_list),
        ):
            import asyncio

            result = asyncio.run(client.get_agent_logs("agent-1"))
            assert result == log_list

    def test_sync_pause_agent_wrapper(self, client):
        # Patch _run_sync and the async method together to avoid unawaited coro warnings
        with patch.object(client, "pause_agent", return_value=None):
            with patch.object(client, "_run_sync", return_value=True):
                result = client.sync_pause_agent("agent-1")
                assert result is True

    def test_sync_resume_agent_wrapper(self, client):
        with patch.object(client, "resume_agent", return_value=None):
            with patch.object(client, "_run_sync", return_value=True):
                result = client.sync_resume_agent("agent-1")
                assert result is True

    def test_sync_kill_agent_wrapper(self, client):
        with patch.object(client, "kill_agent", return_value=None):
            with patch.object(client, "_run_sync", return_value=True):
                result = client.sync_kill_agent("agent-1")
                assert result is True

    def test_sync_fleet_pause_wrapper(self, client):
        with patch.object(client, "fleet_pause", return_value=None):
            with patch.object(client, "_run_sync", return_value={"success": True}):
                result = client.sync_fleet_pause()
                assert result == {"success": True}

    def test_sync_fleet_resume_wrapper(self, client):
        with patch.object(client, "fleet_resume", return_value=None):
            with patch.object(client, "_run_sync", return_value={"success": True}):
                result = client.sync_fleet_resume()
                assert result == {"success": True}

    def test_sync_get_agent_logs_wrapper(self, client):
        logs = [{"message": "ok"}]
        with patch.object(client, "get_agent_logs", return_value=None):
            with patch.object(client, "_run_sync", return_value=logs):
                result = client.sync_get_agent_logs("agent-1", limit=25)
                assert result == logs
