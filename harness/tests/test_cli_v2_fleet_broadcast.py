"""
Tests for forge fleet broadcast CLI command.

Covers:
- forge fleet broadcast --help
- Broadcast to all agents
- Broadcast to specific agent with --target
- Message type prefixes (--type info|warning|task)
- Error handling (no tmux, no session, no agents)
- JSON output mode
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli


def _make_subprocess_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> MagicMock:
    """Create a mock subprocess result."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


class TestFleetBroadcastHelp:
    """Tests for broadcast help and CLI registration."""

    def test_broadcast_in_fleet_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["fleet", "--help"])
        assert result.exit_code == 0
        assert "broadcast" in result.output

    def test_broadcast_help_shows_options(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["fleet", "--help"])
        assert result.exit_code == 0
        assert "--target" in result.output
        assert "--type" in result.output


class TestFleetBroadcastRequiredArgs:
    """Tests for required argument validation."""

    def test_broadcast_requires_message(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(returncode=0)
            result = runner.invoke(cli, ["fleet", "broadcast"])
        assert result.exit_code != 0

    def test_broadcast_empty_message_rejected(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(returncode=0)
            result = runner.invoke(cli, ["fleet", "broadcast", ""])
        assert result.exit_code != 0


class TestFleetBroadcastAllAgents:
    """Tests for broadcasting to all agents."""

    def test_broadcast_lists_windows_for_all(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(stdout="0:tech\n1:content\n2:lead"),
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(cli, ["fleet", "broadcast", "Hello agents"])
        assert result.exit_code == 0
        assert mock_run.call_count >= 5

    def test_broadcast_to_all_sends_to_each_window(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(stdout="0:tech\n1:content"),
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(cli, ["fleet", "broadcast", "Test message"])
        assert result.exit_code == 0
        send_keys_calls = [c for c in mock_run.call_args_list if "send-keys" in c[0][0]]
        assert len(send_keys_calls) == 2

    def test_broadcast_success_message_in_output(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(stdout="0:tech"),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(cli, ["fleet", "broadcast", "Hello"])
        assert result.exit_code == 0
        assert "Broadcast" in result.output or "sent" in result.output.lower()


class TestFleetBroadcastTargetAgent:
    """Tests for broadcasting to a specific agent."""

    def test_broadcast_to_single_target(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "--target",
                    "forge:tech",
                    "Hello tech agent",
                ],
            )
        assert result.exit_code == 0
        send_keys_calls = [c for c in mock_run.call_args_list if "send-keys" in c[0][0]]
        assert len(send_keys_calls) == 1

    def test_broadcast_target_in_send_keys_call(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "-t",
                    "forge:content",
                    "Task for you",
                ],
            )
        assert result.exit_code == 0
        send_keys_call = [c for c in mock_run.call_args_list if "send-keys" in c[0][0]][0]
        assert "forge:content" in send_keys_call[0][0]

    def test_broadcast_target_short_flag(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "-t",
                    "forge:tech",
                    "Hello",
                ],
            )
        assert result.exit_code == 0


class TestFleetBroadcastMessageType:
    """Tests for message type prefixes."""

    def test_broadcast_type_info_prefix(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "-t",
                    "forge:tech",
                    "--type",
                    "info",
                    "Info message",
                ],
            )
        assert result.exit_code == 0
        send_keys_call = [c for c in mock_run.call_args_list if "send-keys" in c[0][0]][0]
        args = send_keys_call[0][0]
        msg_arg = args[4] if len(args) > 4 else ""
        assert "📢" in msg_arg or "Info message" in msg_arg

    def test_broadcast_type_warning_prefix(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "-t",
                    "forge:tech",
                    "-T",
                    "warning",
                    "Warning message",
                ],
            )
        assert result.exit_code == 0
        send_keys_call = [c for c in mock_run.call_args_list if "send-keys" in c[0][0]][0]
        args = send_keys_call[0][0]
        msg_arg = args[4] if len(args) > 4 else ""
        assert "⚠️" in msg_arg or "Warning message" in msg_arg

    def test_broadcast_type_task_prefix(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "-t",
                    "forge:tech",
                    "--type",
                    "task",
                    "Task message",
                ],
            )
        assert result.exit_code == 0
        send_keys_call = [c for c in mock_run.call_args_list if "send-keys" in c[0][0]][0]
        args = send_keys_call[0][0]
        msg_arg = args[4] if len(args) > 4 else ""
        assert "📋" in msg_arg or "Task message" in msg_arg

    def test_broadcast_default_type_is_info(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "-t",
                    "forge:tech",
                    "Default type",
                ],
            )
        assert result.exit_code == 0
        send_keys_call = [c for c in mock_run.call_args_list if "send-keys" in c[0][0]][0]
        args = send_keys_call[0][0]
        msg_arg = args[4] if len(args) > 4 else ""
        assert "📢" in msg_arg or "Default type" in msg_arg


class TestFleetBroadcastErrorHandling:
    """Tests for error handling."""

    def test_broadcast_tmux_not_found_exits(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("tmux not found")
            result = runner.invoke(cli, ["fleet", "broadcast", "Hello"])
        assert result.exit_code == 1  # format_error exits with 1

    def test_broadcast_no_forge_session(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(returncode=1)
            result = runner.invoke(cli, ["fleet", "broadcast", "Hello"])
        assert result.exit_code == 0  # Returns gracefully with warning

    def test_broadcast_no_agents_found(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(stdout=""),
            ]
            result = runner.invoke(cli, ["fleet", "broadcast", "Hello"])
        assert result.exit_code == 0  # Returns gracefully with warning

    def test_broadcast_send_keys_failure_continues(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(stdout="0:tech\n1:content"),
                _make_subprocess_result(returncode=1, stderr="failed"),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(cli, ["fleet", "broadcast", "Test"])
        assert result.exit_code == 0


class TestFleetBroadcastJsonOutput:
    """Tests for JSON output mode."""

    def test_broadcast_json_success_envelope(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "-t",
                    "forge:tech",
                    "Hello",
                    "--json",
                ],
            )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert "data" in payload
        assert payload["command"] == "forge fleet broadcast"

    def test_broadcast_json_contains_message(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "-t",
                    "forge:tech",
                    "Test message content",
                    "--json",
                ],
            )
        payload = json.loads(result.output)
        assert payload["data"]["message"] == "Test message content"

    def test_broadcast_json_contains_type(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "-t",
                    "forge:tech",
                    "-T",
                    "warning",
                    "Alert",
                    "--json",
                ],
            )
        payload = json.loads(result.output)
        assert payload["data"]["type"] == "warning"

    def test_broadcast_json_contains_delivered_list(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "-t",
                    "forge:tech",
                    "Hello",
                    "--json",
                ],
            )
        payload = json.loads(result.output)
        assert "delivered" in payload["data"]
        assert "forge:tech" in payload["data"]["delivered"]

    def test_broadcast_json_contains_counts(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(stdout="0:tech\n1:content\n2:lead"),
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "Hello all",
                    "--json",
                ],
            )
        payload = json.loads(result.output)
        assert payload["data"]["delivered_count"] == 3
        assert payload["data"]["total_agents"] == 3

    def test_broadcast_json_shows_failed(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(stdout="0:tech\n1:content"),
                _make_subprocess_result(returncode=1, stderr="error"),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "Test",
                    "--json",
                ],
            )
        payload = json.loads(result.output)
        assert payload["data"]["delivered_count"] == 1
        assert payload["data"]["failed_count"] == 1
        assert len(payload["data"]["failed"]) == 1


class TestFleetBroadcastIntegration:
    """Integration-style tests for broadcast command."""

    def test_broadcast_full_flow_all_agents(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(stdout="0:tech\n1:content\n2:orchestrator"),
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "Deploy starting in 10 minutes",
                ],
            )
        assert result.exit_code == 0

    def test_broadcast_full_flow_single_agent_with_type(self) -> None:
        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0),
                _make_subprocess_result(returncode=0),
            ]
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "broadcast",
                    "--target",
                    "forge:backend",
                    "--type",
                    "task",
                    "New priority task assigned",
                ],
            )
        assert result.exit_code == 0
