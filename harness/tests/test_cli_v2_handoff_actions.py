"""Tests for forge handoff accept/reject/complete CLI commands.

Covers new lifecycle actions added to forge_harness/cli_v2/handoff.py:
    - handoff accept HANDOFF_ID [--agent AGENT_ID]
    - handoff reject HANDOFF_ID --reason REASON
    - handoff complete HANDOFF_ID

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
        with patch("forge_harness.cli_v2.handoff.CommandCenterClient") as cls:
            cls.from_env.return_value = client_mock
            return runner.invoke(cli, args, input=input_text)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

_ACCEPT_RESPONSE = {
    "id": "handoff-abc123",
    "status": "accepted",
    "accepted_by": "forge:kilo",
}

_REJECT_RESPONSE = {
    "id": "handoff-abc123",
    "status": "rejected",
    "reason": "Out of scope",
}

_COMPLETE_RESPONSE = {
    "id": "handoff-abc123",
    "status": "completed",
}


# ===========================================================================
# forge handoff accept
# ===========================================================================


class TestHandoffAccept:
    """Tests for 'forge handoff accept HANDOFF_ID'."""

    def test_accept_success_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_accept_handoff.return_value = _ACCEPT_RESPONSE
        result = _invoke(runner, forge_root, ["handoff", "accept", "handoff-abc123"], mock_client)

        assert result.exit_code == 0
        assert "accepted" in result.output

    def test_accept_success_shows_handoff_id(self, runner, forge_root, mock_client):
        mock_client.sync_accept_handoff.return_value = _ACCEPT_RESPONSE
        result = _invoke(runner, forge_root, ["handoff", "accept", "handoff-abc123"], mock_client)

        assert result.exit_code == 0
        assert "handoff-abc123" in result.output

    def test_accept_with_agent_option(self, runner, forge_root, mock_client):
        mock_client.sync_accept_handoff.return_value = _ACCEPT_RESPONSE
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "accept", "handoff-abc123", "--agent", "forge:kilo"],
            mock_client,
        )

        assert result.exit_code == 0
        mock_client.sync_accept_handoff.assert_called_once_with("handoff-abc123", "forge:kilo")

    def test_accept_without_agent_option_passes_empty_string(
        self, runner, forge_root, mock_client
    ):
        mock_client.sync_accept_handoff.return_value = _ACCEPT_RESPONSE
        result = _invoke(runner, forge_root, ["handoff", "accept", "handoff-abc123"], mock_client)

        assert result.exit_code == 0
        mock_client.sync_accept_handoff.assert_called_once_with("handoff-abc123", "")

    def test_accept_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_accept_handoff.return_value = _ACCEPT_RESPONSE
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "--json", "accept", "handoff-abc123"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge handoff accept"
        assert data["data"]["status"] == "accepted"

    def test_accept_json_mode_with_agent(self, runner, forge_root, mock_client):
        mock_client.sync_accept_handoff.return_value = _ACCEPT_RESPONSE
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "--json", "accept", "handoff-abc123", "--agent", "forge:kilo"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_accept_requires_handoff_id(self, runner, forge_root, mock_client):
        result = _invoke(runner, forge_root, ["handoff", "accept"], mock_client)
        assert result.exit_code == 2

    def test_accept_exception_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_accept_handoff.side_effect = RuntimeError("API down")
        result = _invoke(runner, forge_root, ["handoff", "accept", "handoff-abc123"], mock_client)

        assert result.exit_code == 1

    def test_accept_exception_json_mode_outputs_json(self, runner, forge_root, mock_client):
        mock_client.sync_accept_handoff.side_effect = RuntimeError("connection refused")
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "--json", "accept", "handoff-abc123"],
            mock_client,
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False

    def test_accept_shows_agent_in_confirmation(self, runner, forge_root, mock_client):
        mock_client.sync_accept_handoff.return_value = _ACCEPT_RESPONSE
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "accept", "handoff-abc123", "--agent", "forge:kilo"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "forge:kilo" in result.output


# ===========================================================================
# forge handoff reject
# ===========================================================================


class TestHandoffReject:
    """Tests for 'forge handoff reject HANDOFF_ID --reason REASON'."""

    def test_reject_success_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_reject_handoff.return_value = _REJECT_RESPONSE
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "reject", "handoff-abc123", "--reason", "Out of scope"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "rejected" in result.output

    def test_reject_shows_handoff_id_in_output(self, runner, forge_root, mock_client):
        mock_client.sync_reject_handoff.return_value = _REJECT_RESPONSE
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "reject", "handoff-abc123", "--reason", "Out of scope"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "handoff-abc123" in result.output

    def test_reject_passes_reason_to_client(self, runner, forge_root, mock_client):
        mock_client.sync_reject_handoff.return_value = _REJECT_RESPONSE
        _invoke(
            runner,
            forge_root,
            ["handoff", "reject", "handoff-abc123", "--reason", "Not prioritized"],
            mock_client,
        )

        mock_client.sync_reject_handoff.assert_called_once_with(
            "handoff-abc123", "Not prioritized"
        )

    def test_reject_shows_reason_in_output(self, runner, forge_root, mock_client):
        mock_client.sync_reject_handoff.return_value = _REJECT_RESPONSE
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "reject", "handoff-abc123", "--reason", "Out of scope"],
            mock_client,
        )

        assert result.exit_code == 0
        assert "Out of scope" in result.output

    def test_reject_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_reject_handoff.return_value = _REJECT_RESPONSE
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "--json", "reject", "handoff-abc123", "--reason", "Out of scope"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge handoff reject"
        assert data["data"]["status"] == "rejected"

    def test_reject_requires_handoff_id(self, runner, forge_root, mock_client):
        result = _invoke(
            runner, forge_root, ["handoff", "reject", "--reason", "reason"], mock_client
        )
        assert result.exit_code == 2

    def test_reject_reason_is_required(self, runner, forge_root, mock_client):
        """Missing --reason should produce usage error."""
        result = _invoke(
            runner, forge_root, ["handoff", "reject", "handoff-abc123"], mock_client
        )
        assert result.exit_code == 2

    def test_reject_exception_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_reject_handoff.side_effect = ConnectionError("timeout")
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "reject", "handoff-abc123", "--reason", "Timeout"],
            mock_client,
        )

        assert result.exit_code == 1

    def test_reject_exception_json_mode_outputs_json(self, runner, forge_root, mock_client):
        mock_client.sync_reject_handoff.side_effect = RuntimeError("boom")
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "--json", "reject", "handoff-abc123", "--reason", "err"],
            mock_client,
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False


# ===========================================================================
# forge handoff complete
# ===========================================================================


class TestHandoffComplete:
    """Tests for 'forge handoff complete HANDOFF_ID'."""

    def test_complete_success_human_mode(self, runner, forge_root, mock_client):
        mock_client.sync_complete_handoff.return_value = _COMPLETE_RESPONSE
        result = _invoke(
            runner, forge_root, ["handoff", "complete", "handoff-abc123"], mock_client
        )

        assert result.exit_code == 0
        assert "complete" in result.output.lower()

    def test_complete_shows_handoff_id(self, runner, forge_root, mock_client):
        mock_client.sync_complete_handoff.return_value = _COMPLETE_RESPONSE
        result = _invoke(
            runner, forge_root, ["handoff", "complete", "handoff-abc123"], mock_client
        )

        assert result.exit_code == 0
        assert "handoff-abc123" in result.output

    def test_complete_calls_correct_id(self, runner, forge_root, mock_client):
        mock_client.sync_complete_handoff.return_value = _COMPLETE_RESPONSE
        _invoke(runner, forge_root, ["handoff", "complete", "handoff-abc123"], mock_client)

        mock_client.sync_complete_handoff.assert_called_once_with("handoff-abc123")

    def test_complete_json_mode(self, runner, forge_root, mock_client):
        mock_client.sync_complete_handoff.return_value = _COMPLETE_RESPONSE
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "--json", "complete", "handoff-abc123"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["command"] == "forge handoff complete"
        assert data["data"]["status"] == "completed"

    def test_complete_requires_handoff_id(self, runner, forge_root, mock_client):
        result = _invoke(runner, forge_root, ["handoff", "complete"], mock_client)
        assert result.exit_code == 2

    def test_complete_exception_exits_1(self, runner, forge_root, mock_client):
        mock_client.sync_complete_handoff.side_effect = RuntimeError("API error")
        result = _invoke(
            runner, forge_root, ["handoff", "complete", "handoff-abc123"], mock_client
        )

        assert result.exit_code == 1

    def test_complete_exception_json_mode_outputs_json(self, runner, forge_root, mock_client):
        mock_client.sync_complete_handoff.side_effect = RuntimeError("crash")
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "--json", "complete", "handoff-abc123"],
            mock_client,
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False

    def test_complete_empty_response_still_succeeds(self, runner, forge_root, mock_client):
        """Empty dict response from API should not cause a crash."""
        mock_client.sync_complete_handoff.return_value = {}
        result = _invoke(
            runner, forge_root, ["handoff", "complete", "handoff-abc123"], mock_client
        )

        assert result.exit_code == 0


# ===========================================================================
# Help text
# ===========================================================================


class TestHandoffHelp:
    """Ensure help text renders for all subcommands."""

    def test_handoff_group_help(self, runner):
        result = runner.invoke(cli, ["handoff", "--help"])
        assert result.exit_code == 0
        assert "create" in result.output
        assert "read" in result.output
        assert "accept" in result.output
        assert "reject" in result.output
        assert "complete" in result.output

    def test_accept_help(self, runner):
        result = runner.invoke(cli, ["handoff", "accept", "--help"])
        assert result.exit_code == 0
        assert "HANDOFF_ID" in result.output
        assert "--agent" in result.output

    def test_reject_help(self, runner):
        result = runner.invoke(cli, ["handoff", "reject", "--help"])
        assert result.exit_code == 0
        assert "HANDOFF_ID" in result.output
        assert "--reason" in result.output

    def test_complete_help(self, runner):
        result = runner.invoke(cli, ["handoff", "complete", "--help"])
        assert result.exit_code == 0
        assert "HANDOFF_ID" in result.output


# ===========================================================================
# JSON flag propagation
# ===========================================================================


class TestHandoffJsonPropagation:
    """Confirm --json on the group propagates to subcommands."""

    def test_json_flag_on_group_accept(self, runner, forge_root, mock_client):
        mock_client.sync_accept_handoff.return_value = _ACCEPT_RESPONSE
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "--json", "accept", "h-001"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_json_flag_on_group_reject(self, runner, forge_root, mock_client):
        mock_client.sync_reject_handoff.return_value = _REJECT_RESPONSE
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "--json", "reject", "h-001", "--reason", "test"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_json_flag_on_group_complete(self, runner, forge_root, mock_client):
        mock_client.sync_complete_handoff.return_value = _COMPLETE_RESPONSE
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "--json", "complete", "h-001"],
            mock_client,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_json_flag_error_produces_parseable_json(self, runner, forge_root, mock_client):
        mock_client.sync_complete_handoff.side_effect = RuntimeError("boom")
        result = _invoke(
            runner,
            forge_root,
            ["handoff", "--json", "complete", "h-001"],
            mock_client,
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False


# ===========================================================================
# CommandCenterClient handoff methods (unit tests)
# ===========================================================================


class TestCommandCenterClientHandoffMethods:
    """Unit tests for the new sync/async handoff methods on CommandCenterClient."""

    def test_sync_accept_handoff_method_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_accept_handoff")
        assert callable(client.sync_accept_handoff)

    def test_sync_reject_handoff_method_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_reject_handoff")
        assert callable(client.sync_reject_handoff)

    def test_sync_complete_handoff_method_exists(self):
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "sync_complete_handoff")
        assert callable(client.sync_complete_handoff)

    def test_async_accept_handoff_method_exists(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "accept_handoff")
        assert inspect.iscoroutinefunction(client.accept_handoff)

    def test_async_reject_handoff_method_exists(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "reject_handoff")
        assert inspect.iscoroutinefunction(client.reject_handoff)

    def test_async_complete_handoff_method_exists(self):
        import inspect

        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient(base_url="http://localhost:9999")
        assert hasattr(client, "complete_handoff")
        assert inspect.iscoroutinefunction(client.complete_handoff)
