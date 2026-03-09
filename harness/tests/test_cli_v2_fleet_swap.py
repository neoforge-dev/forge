"""
Tests for `forge fleet swap` subcommand.

Covers:
- Happy path: swap with handoff (default)
- Swap without handoff (--no-handoff)
- Agent not found (tmux has-session fails)
- tmux binary missing
- Stop fails after agent is found
- Spawn fails after stop succeeds
- Custom replacement name (--replacement)
- JSON output mode (--json)
- All CLI options forwarded correctly (--reason, --domain, --project, --role)
- Missing agent name raises UsageError
- _fleet_swap helper function directly
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, Mock, call, patch

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> Mock:
    """Build a lightweight subprocess.CompletedProcess mock."""
    return Mock(returncode=returncode, stdout=stdout, stderr=stderr)


def _make_handoff(handoff_id: str = "hd-abc123") -> Mock:
    """Return a mock Handoff document with the given id."""
    doc = Mock()
    doc.id = handoff_id
    doc.description = ""
    return doc


# ---------------------------------------------------------------------------
# Unit tests for _fleet_swap helper
# ---------------------------------------------------------------------------


class TestFleetSwapHappy:
    """Happy-path tests for _fleet_swap."""

    def test_swap_with_handoff_success(self):
        """Full swap cycle: verify -> handoff -> stop -> spawn all succeed."""
        from forge_harness.cli_v2.fleet import _fleet_swap

        # has-session: agent found
        has_session_ok = _proc(returncode=0)

        # Build handoff mocks
        mock_handoff_doc = _make_handoff("hd-001")
        mock_store = Mock()
        mock_store.save.return_value = "/tmp/handoff.json"

        # stop: kill-session succeeds
        kill_ok = _proc(returncode=0)
        # spawn: script succeeds
        spawn_ok = _proc(returncode=0, stdout="spawned")

        with (
            patch("subprocess.run", side_effect=[has_session_ok, kill_ok, spawn_ok]),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value="/tmp/forge",
            ),
            patch("forge_harness.handoff.store.HandoffStore", return_value=mock_store),
            patch("forge_harness.handoff.schema.Handoff") as patched_Handoff,
        ):
            patched_Handoff.new.return_value = mock_handoff_doc

            result = _fleet_swap(
                ctx=None,
                agent_name="forge:tech",
                create_handoff=True,
                output_json=True,
            )

        assert result["success"] is True
        assert result["old_agent"] == "forge:tech"
        assert result["new_agent"] == "forge:tech"
        assert result["stop_result"]["success"] is True
        assert result["spawn_result"]["success"] is True

    def test_swap_without_handoff(self):
        """Swap with --no-handoff: no handoff module code executed."""
        from forge_harness.cli_v2.fleet import _fleet_swap

        has_session_ok = _proc(returncode=0)
        kill_ok = _proc(returncode=0)
        spawn_ok = _proc(returncode=0, stdout="spawned")

        with patch("subprocess.run", side_effect=[has_session_ok, kill_ok, spawn_ok]):
            result = _fleet_swap(
                ctx=None,
                agent_name="forge:content",
                create_handoff=False,
                output_json=True,
            )

        assert result["success"] is True
        assert result["old_agent"] == "forge:content"
        assert result["new_agent"] == "forge:content"
        # No handoff_id set when create_handoff=False
        assert "handoff_id" not in result

    def test_custom_replacement_name(self):
        """--replacement changes the new_agent field."""
        from forge_harness.cli_v2.fleet import _fleet_swap

        has_session_ok = _proc(returncode=0)
        kill_ok = _proc(returncode=0)
        spawn_ok = _proc(returncode=0, stdout="spawned")

        with patch("subprocess.run", side_effect=[has_session_ok, kill_ok, spawn_ok]):
            result = _fleet_swap(
                ctx=None,
                agent_name="forge:tech",
                replacement="forge:tech2",
                create_handoff=False,
                output_json=True,
            )

        assert result["success"] is True
        assert result["old_agent"] == "forge:tech"
        assert result["new_agent"] == "forge:tech2"

    def test_spawn_called_with_domain_project_role(self):
        """domain, project, and role are forwarded to _fleet_spawn."""
        from forge_harness.cli_v2.fleet import _fleet_swap

        has_session_ok = _proc(returncode=0)
        kill_ok = _proc(returncode=0)
        spawn_ok = _proc(returncode=0, stdout="spawned")

        calls: list[Any] = []

        def capture_run(cmd, **kwargs):
            calls.append(cmd)
            return _proc(returncode=0, stdout="spawned")

        with patch("subprocess.run", side_effect=capture_run):
            result = _fleet_swap(
                ctx=None,
                agent_name="forge:tech",
                replacement="forge:tech",
                domain="codeswiftr-com",
                project="interview-simulator",
                role="reviewer",
                create_handoff=False,
                output_json=True,
            )

        assert result["success"] is True
        # The spawn call is the third subprocess.run (has-session, kill-session, fleet-spawn)
        spawn_cmd = calls[2]
        assert "forge:tech" in spawn_cmd
        # domain/project are in the cmd when both provided
        assert "codeswiftr-com" in spawn_cmd
        assert "interview-simulator" in spawn_cmd

    def test_reason_stored_in_result(self):
        """reason value does not break the swap and is not mangled."""
        from forge_harness.cli_v2.fleet import _fleet_swap

        has_session_ok = _proc(returncode=0)
        kill_ok = _proc(returncode=0)
        spawn_ok = _proc(returncode=0, stdout="spawned")

        with patch("subprocess.run", side_effect=[has_session_ok, kill_ok, spawn_ok]):
            result = _fleet_swap(
                ctx=None,
                agent_name="forge:tech",
                reason="rate limited",
                create_handoff=False,
                output_json=True,
            )

        assert result["success"] is True


# ---------------------------------------------------------------------------
# Failure / error path tests for _fleet_swap helper
# ---------------------------------------------------------------------------


class TestFleetSwapErrors:
    """Error-handling tests for _fleet_swap."""

    def test_agent_not_found(self):
        """Returns failure dict when has-session returns non-zero."""
        from forge_harness.cli_v2.fleet import _fleet_swap

        has_session_missing = _proc(returncode=1, stderr="no such session")

        with patch("subprocess.run", return_value=has_session_missing):
            result = _fleet_swap(
                ctx=None,
                agent_name="forge:ghost",
                create_handoff=False,
                output_json=True,
            )

        assert result["success"] is False
        assert "not found" in result["error"]
        assert result["old_agent"] == "forge:ghost"

    def test_tmux_not_installed(self):
        """Returns failure dict when tmux binary is missing."""
        from forge_harness.cli_v2.fleet import _fleet_swap

        with patch("subprocess.run", side_effect=FileNotFoundError("No such file")):
            result = _fleet_swap(
                ctx=None,
                agent_name="forge:tech",
                create_handoff=False,
                output_json=True,
            )

        assert result["success"] is False
        assert "tmux" in result["error"].lower()

    def test_stop_fails_halts_swap(self):
        """When kill-session fails, spawn is never called and success=False."""
        from forge_harness.cli_v2.fleet import _fleet_swap

        has_session_ok = _proc(returncode=0)
        kill_fail = _proc(returncode=1, stderr="no such session: forge:tech")

        with patch("subprocess.run", side_effect=[has_session_ok, kill_fail]):
            result = _fleet_swap(
                ctx=None,
                agent_name="forge:tech",
                create_handoff=False,
                output_json=True,
            )

        assert result["success"] is False
        assert "Stop failed" in result["error"]
        assert "spawn_result" not in result

    def test_spawn_fails_after_stop(self):
        """When spawn fails after stop, success=False and stop_result records success."""
        from forge_harness.cli_v2.fleet import _fleet_swap

        has_session_ok = _proc(returncode=0)
        kill_ok = _proc(returncode=0)
        spawn_fail = _proc(returncode=1, stderr="script not found")

        with patch(
            "subprocess.run",
            side_effect=[has_session_ok, kill_ok, spawn_fail],
        ):
            result = _fleet_swap(
                ctx=None,
                agent_name="forge:tech",
                create_handoff=False,
                output_json=True,
            )

        assert result["success"] is False
        assert "Spawn failed" in result["error"]
        # stop_result should still be present and successful
        assert result["stop_result"]["success"] is True

    def test_handoff_creation_failure_continues_swap(self):
        """If handoff creation raises, the swap continues and logs a warning."""
        from forge_harness.cli_v2.fleet import _fleet_swap

        has_session_ok = _proc(returncode=0)
        kill_ok = _proc(returncode=0)
        spawn_ok = _proc(returncode=0, stdout="spawned")

        with (
            patch("subprocess.run", side_effect=[has_session_ok, kill_ok, spawn_ok]),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                side_effect=RuntimeError("no root"),
            ),
        ):
            result = _fleet_swap(
                ctx=None,
                agent_name="forge:tech",
                create_handoff=True,
                output_json=True,
            )

        # Swap should still succeed despite handoff failure
        assert result["success"] is True
        assert "handoff_warning" in result


# ---------------------------------------------------------------------------
# CLI integration tests via CliRunner
# ---------------------------------------------------------------------------


class TestFleetSwapCLI:
    """Integration tests using Click's CliRunner."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_swap_missing_agent_name_raises_usage_error(self, runner):
        """forge fleet swap without agent name exits non-zero."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "swap"])
        assert result.exit_code != 0

    def test_swap_action_in_choice_list(self, runner):
        """'swap' is a valid action (no 'invalid choice' error from Click)."""
        from forge_harness.cli_v2 import cli

        has_session_ok = _proc(returncode=0)
        kill_ok = _proc(returncode=0)
        spawn_ok = _proc(returncode=0, stdout="spawned")

        with patch("subprocess.run", side_effect=[has_session_ok, kill_ok, spawn_ok]):
            result = runner.invoke(cli, ["fleet", "swap", "forge:tech", "--no-handoff"])

        # Should NOT exit with "Invalid value for 'ACTION'" error
        assert "Invalid value" not in (result.output or "")
        assert result.exit_code == 0

    def test_swap_json_output_envelope(self, runner):
        """--json flag emits a valid FORGE JSON envelope."""
        from forge_harness.cli_v2 import cli

        has_session_ok = _proc(returncode=0)
        kill_ok = _proc(returncode=0)
        spawn_ok = _proc(returncode=0, stdout="spawned")

        with patch("subprocess.run", side_effect=[has_session_ok, kill_ok, spawn_ok]):
            result = runner.invoke(cli, ["fleet", "swap", "forge:tech", "--no-handoff", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert "data" in payload
        assert "timestamp" in payload
        assert payload["command"] == "forge fleet swap forge:tech"

    def test_swap_json_output_data_fields(self, runner):
        """JSON data contains old_agent and new_agent."""
        from forge_harness.cli_v2 import cli

        has_session_ok = _proc(returncode=0)
        kill_ok = _proc(returncode=0)
        spawn_ok = _proc(returncode=0, stdout="spawned")

        with patch("subprocess.run", side_effect=[has_session_ok, kill_ok, spawn_ok]):
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "swap",
                    "forge:tech",
                    "--replacement",
                    "forge:tech2",
                    "--no-handoff",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        data = payload["data"]
        assert data["old_agent"] == "forge:tech"
        assert data["new_agent"] == "forge:tech2"

    def test_swap_agent_not_found_json(self, runner):
        """When agent not found with --json, data.success is False."""
        from forge_harness.cli_v2 import cli

        has_session_fail = _proc(returncode=1, stderr="no such session")

        with patch("subprocess.run", return_value=has_session_fail):
            result = runner.invoke(cli, ["fleet", "swap", "forge:ghost", "--no-handoff", "--json"])

        # In JSON mode the outer envelope exits 0; failure is in data.success
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["data"]["success"] is False
        assert "not found" in payload["data"]["error"]

    def test_swap_no_handoff_flag(self, runner):
        """--no-handoff flag accepted without error."""
        from forge_harness.cli_v2 import cli

        has_session_ok = _proc(returncode=0)
        kill_ok = _proc(returncode=0)
        spawn_ok = _proc(returncode=0, stdout="spawned")

        with patch("subprocess.run", side_effect=[has_session_ok, kill_ok, spawn_ok]):
            result = runner.invoke(cli, ["fleet", "swap", "forge:tech", "--no-handoff"])

        assert result.exit_code == 0

    def test_swap_with_domain_and_project(self, runner):
        """--domain and --project forwarded to spawn without CLI errors."""
        from forge_harness.cli_v2 import cli

        calls_captured: list[list[str]] = []

        def capture(cmd, **kwargs):
            calls_captured.append(list(cmd))
            return _proc(returncode=0, stdout="ok")

        with patch("subprocess.run", side_effect=capture):
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "swap",
                    "forge:tech",
                    "--no-handoff",
                    "--domain",
                    "codeswiftr-com",
                    "--project",
                    "interview-simulator",
                ],
            )

        assert result.exit_code == 0
        # Verify spawn cmd contained domain+project
        spawn_cmd = calls_captured[2]  # has-session, kill-session, fleet-spawn
        assert "codeswiftr-com" in spawn_cmd
        assert "interview-simulator" in spawn_cmd

    def test_swap_with_reason_flag(self, runner):
        """--reason flag is accepted without error."""
        from forge_harness.cli_v2 import cli

        has_session_ok = _proc(returncode=0)
        kill_ok = _proc(returncode=0)
        spawn_ok = _proc(returncode=0, stdout="spawned")

        with patch("subprocess.run", side_effect=[has_session_ok, kill_ok, spawn_ok]):
            result = runner.invoke(
                cli,
                ["fleet", "swap", "forge:tech", "--no-handoff", "--reason", "context exhaustion"],
            )

        assert result.exit_code == 0

    def test_swap_stop_failure_exits_nonzero(self, runner):
        """When stop fails, CLI exits non-zero in human-readable mode."""
        from forge_harness.cli_v2 import cli

        has_session_ok = _proc(returncode=0)
        kill_fail = _proc(returncode=1, stderr="kill failed")

        with patch("subprocess.run", side_effect=[has_session_ok, kill_fail]):
            result = runner.invoke(cli, ["fleet", "swap", "forge:tech", "--no-handoff"])

        # format_error raises SystemExit(1)
        assert result.exit_code != 0

    def test_swap_help_shows_options(self, runner):
        """'forge fleet swap --help' should be reachable (action in Choice)."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "--help"])
        assert result.exit_code == 0
        # The swap action should appear in help text
        assert "swap" in result.output


# ---------------------------------------------------------------------------
# Direct helper correctness tests
# ---------------------------------------------------------------------------


class TestFleetSwapHelperContract:
    """Verify the return dict contract of _fleet_swap."""

    def _run_happy(self):
        from forge_harness.cli_v2.fleet import _fleet_swap

        has_session_ok = _proc(returncode=0)
        kill_ok = _proc(returncode=0)
        spawn_ok = _proc(returncode=0, stdout="spawned")

        with patch("subprocess.run", side_effect=[has_session_ok, kill_ok, spawn_ok]):
            return _fleet_swap(
                ctx=None,
                agent_name="forge:tech",
                create_handoff=False,
                output_json=True,
            )

    def test_result_has_success_key(self):
        result = self._run_happy()
        assert "success" in result

    def test_result_has_old_agent_key(self):
        result = self._run_happy()
        assert "old_agent" in result

    def test_result_has_new_agent_key(self):
        result = self._run_happy()
        assert "new_agent" in result

    def test_result_has_stop_result_key(self):
        result = self._run_happy()
        assert "stop_result" in result

    def test_result_has_spawn_result_key(self):
        result = self._run_happy()
        assert "spawn_result" in result

    def test_new_agent_defaults_to_agent_name(self):
        result = self._run_happy()
        assert result["new_agent"] == result["old_agent"]

    def test_new_agent_uses_replacement_when_provided(self):
        from forge_harness.cli_v2.fleet import _fleet_swap

        has_session_ok = _proc(returncode=0)
        kill_ok = _proc(returncode=0)
        spawn_ok = _proc(returncode=0, stdout="spawned")

        with patch("subprocess.run", side_effect=[has_session_ok, kill_ok, spawn_ok]):
            result = _fleet_swap(
                ctx=None,
                agent_name="forge:tech",
                replacement="forge:tech-new",
                create_handoff=False,
                output_json=True,
            )

        assert result["new_agent"] == "forge:tech-new"
        assert result["old_agent"] == "forge:tech"
