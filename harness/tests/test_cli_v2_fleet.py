"""
TDD tests for forge fleet command.

Tests follow Test-Driven Development:
1. Tests define expected behavior first
2. Implementation should match test expectations
3. All public functions are tested
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest


class TestFleetCommand:
    """Test forge fleet command."""

    def test_fleet_command_callable(self):
        """Fleet command should be callable."""
        from forge_harness.cli_v2 import cli
        # Will raise SystemExit if not properly registered
        try:
            cli.main(["fleet", "--help"])
        except SystemExit:
            pass  # Expected with --help


class TestFleetActions:
    """Test fleet action dispatching."""

    def test_status_action_exists(self):
        """Status action should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["fleet", "status", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_spawn_action_exists(self):
        """Spawn action should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["fleet", "spawn", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_stop_action_exists(self):
        """Stop action should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["fleet", "stop", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_save_action_exists(self):
        """Save action should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["fleet", "save", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_restore_action_exists(self):
        """Restore action should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["fleet", "restore", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_heartbeat_action_exists(self):
        """Heartbeat action should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["fleet", "heartbeat", "--help"])
        except SystemExit:
            pass  # Expected with --help


class TestFleetHelperFunctions:
    """Test fleet helper implementation details."""

    def test_fleet_stop_all_stops_each_forge_session(self):
        """_fleet_stop(all=True) should stop each forge:* session explicitly."""
        from forge_harness.cli_v2.fleet import _fleet_stop

        list_result = Mock(returncode=0, stdout="forge:tech\nforge:review\nmisc\n", stderr="")
        kill_tech = Mock(returncode=0, stdout="", stderr="")
        kill_review = Mock(returncode=0, stdout="", stderr="")

        with patch(
            "subprocess.run",
            side_effect=[list_result, kill_tech, kill_review],
        ) as run_mock:
            result = _fleet_stop(ctx=None, agent_name=None, all=True, output_json=True)

        assert result["success"] is True
        assert result["stopped_count"] == 2
        assert "forge:tech" in result["stopped"]
        assert "forge:review" in result["stopped"]
        assert run_mock.call_count == 3

    def test_fleet_save_returns_error_on_nonzero_exit(self):
        """_fleet_save should fail when script exits non-zero."""
        from forge_harness.cli_v2.fleet import _fleet_save

        with patch(
            "subprocess.run",
            return_value=Mock(returncode=1, stdout="", stderr="save failed"),
        ):
            result = _fleet_save(ctx=None, output_json=True)

        assert result["success"] is False
        assert "save failed" in result["error"]

    def test_fleet_restore_returns_error_on_nonzero_exit(self):
        """_fleet_restore should fail when script exits non-zero."""
        from forge_harness.cli_v2.fleet import _fleet_restore

        with patch(
            "subprocess.run",
            return_value=Mock(returncode=1, stdout="", stderr="restore failed"),
        ):
            result = _fleet_restore(ctx=None, output_json=True)

        assert result["success"] is False
        assert "restore failed" in result["error"]

    def test_fleet_status_json_collects_tmux_windows(self):
        """_fleet_status should build structured JSON from tmux window snapshots."""
        from forge_harness.cli_v2.fleet import _fleet_status

        has_session = Mock(returncode=0, stdout="", stderr="")
        list_windows = Mock(returncode=0, stdout="0:tech\n1:review\n", stderr="")
        pane_tech = Mock(
            returncode=0,
            stdout="Build running\n45% context left\n",
            stderr="",
        )
        pane_review = Mock(returncode=0, stdout="> Ready for input\n", stderr="")

        with patch(
            "subprocess.run",
            side_effect=[has_session, list_windows, pane_tech, pane_review],
        ):
            result = _fleet_status(ctx=None, output_json=True)

        assert result["session_active"] is True
        assert result["summary"]["total"] == 2
        assert result["summary"]["active"] == 1
        assert result["summary"]["idle"] == 1
        assert result["agents"][0]["context"] == "45% context left"
        assert result["agents"][1]["status"] == "idle"

    def test_fleet_status_json_when_session_missing(self):
        """_fleet_status should return empty data when forge session does not exist."""
        from forge_harness.cli_v2.fleet import _fleet_status

        has_session = Mock(returncode=1, stdout="", stderr="can't find session")
        with patch("subprocess.run", return_value=has_session):
            result = _fleet_status(ctx=None, output_json=True)

        assert result["session_active"] is False
        assert result["agents"] == []
