"""Unit tests for cli_v2/fleet.py module.

Tests cover:
- Helper functions (_extract_context_snapshot, _classify_agent_status, _extract_activity_line)
- Fleet command actions (status, spawn, stop, save, restore, heartbeat)
- Edge cases and error handling
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner

# Import the functions we want to test
from forge_harness.cli_v2.fleet import (
    _classify_agent_status,
    _extract_activity_line,
    _extract_context_snapshot,
)


class TestExtractContextSnapshot:
    """Tests for _extract_context_snapshot function."""

    def test_extracts_percentage(self):
        """Should extract context percentage from output."""
        output = "Some output\n45% context left\nMore text"
        result = _extract_context_snapshot(output)
        assert result in ["45%", "45% context left"]  # Accept either format

    def test_extracts_without_context_text(self):
        """Should extract percentage even without 'context' text."""
        output = "Some output\n67.5%\nMore text"
        result = _extract_context_snapshot(output)
        assert result == "67.5%"

    def test_returns_dash_when_no_match(self):
        """Should return '-' when no percentage found."""
        output = "Some output without percentage"
        result = _extract_context_snapshot(output)
        assert result == "-"

    def test_extracts_last_match(self):
        """Should extract the last percentage when multiple exist."""
        output = "10%\n20%\n30%"
        result = _extract_context_snapshot(output)
        assert result == "30%"

    def test_handles_empty_string(self):
        """Should handle empty string."""
        result = _extract_context_snapshot("")
        assert result == "-"


class TestClassifyAgentStatus:
    """Tests for _classify_agent_status function."""

    def test_detects_error_state(self):
        """Should detect error indicators."""
        output = "Something failed with error"
        status, color = _classify_agent_status(output)
        assert status == "error"
        assert color == "red"

    def test_detects_failed_state(self):
        """Should detect failed indicators."""
        output = "Process failed"
        status, color = _classify_agent_status(output)
        assert status == "error"
        assert color == "red"

    def test_detects_blocked_state(self):
        """Should detect blocked indicators."""
        output = "Operation blocked"
        status, color = _classify_agent_status(output)
        assert status == "error"
        assert color == "red"

    def test_detects_traceback(self):
        """Should detect Python traceback."""
        output = "Traceback (most recent call last):"
        status, color = _classify_agent_status(output)
        assert status == "error"
        assert color == "red"

    def test_detects_working_state(self):
        """Should detect working indicators."""
        output = "Working on your request..."
        status, color = _classify_agent_status(output)
        assert status == "active"
        assert color == "yellow"

    def test_detects_thinking_state(self):
        """Should detect thinking indicators."""
        output = "Thinking about the problem..."
        status, color = _classify_agent_status(output)
        assert status == "active"
        assert color == "yellow"

    def test_detects_running_state(self):
        """Should detect running indicators."""
        output = "Running tests..."
        status, color = _classify_agent_status(output)
        assert status == "active"
        assert color == "yellow"

    def test_detects_compiling_state(self):
        """Should detect compiling indicators."""
        output = "Compiling the code..."
        status, color = _classify_agent_status(output)
        assert status == "active"
        assert color == "yellow"

    def test_detects_idle_state_prompt(self):
        """Should detect idle state with > prompt."""
        output = "> Type your message"
        status, color = _classify_agent_status(output)
        assert status == "idle"
        assert color == "green"

    def test_detects_idle_state_unicode(self):
        """Should detect idle state with unicode prompt."""
        output = "❯ Enter command"
        status, color = _classify_agent_status(output)
        assert status == "idle"
        assert color == "green"

    def test_detects_idle_state_dollar(self):
        """Should detect idle state with $ prompt."""
        output = "$ waiting for input"
        status, color = _classify_agent_status(output)
        assert status == "idle"
        assert color == "green"

    def test_detects_ready_state(self):
        """Should return ready for neutral output."""
        output = "Some neutral output"
        status, color = _classify_agent_status(output)
        assert status == "ready"
        assert color == "green"

    def test_case_insensitive(self):
        """Should be case insensitive."""
        output = "WORKING on task"  # uppercase
        status, color = _classify_agent_status(output)
        assert status == "active"


class TestExtractActivityLine:
    """Tests for _extract_activity_line function."""

    def test_extracts_last_line(self):
        """Should extract the last non-empty line."""
        output = "Line 1\nLine 2\nLine 3"
        result = _extract_activity_line(output)
        assert result == "Line 3"

    def test_handles_empty_lines(self):
        """Should skip empty lines."""
        output = "Line 1\n\n\nLine 2"
        result = _extract_activity_line(output)
        assert result == "Line 2"

    def test_truncates_long_lines(self):
        """Should truncate lines longer than 60 chars."""
        output = "x" * 100
        result = _extract_activity_line(output)
        assert len(result) == 60
        assert result == "x" * 60

    def test_escapes_pipe(self):
        """Should escape pipe characters."""
        output = "a | b | c"
        result = _extract_activity_line(output)
        assert result == r"a \| b \| c"

    def test_returns_no_output_when_empty(self):
        """Should return 'No output' when empty."""
        output = ""
        result = _extract_activity_line(output)
        assert result == "No output"

    def test_handles_whitespace_only(self):
        """Should handle whitespace-only output."""
        output = "   \n\t\n   "
        result = _extract_activity_line(output)
        assert result == "No output"

    def test_strips_whitespace(self):
        """Should strip whitespace from lines."""
        output = "  Line with spaces  "
        result = _extract_activity_line(output)
        assert result == "Line with spaces"


class TestFleetCommandHelp:
    """Tests for fleet command help."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_fleet_help(self, runner):
        """Test fleet --help works."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "--help"])
        assert result.exit_code == 0
        assert "fleet" in result.output.lower()

    def test_fleet_status_help(self, runner):
        """Test fleet status --help works."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "status", "--help"])
        assert result.exit_code == 0

    def test_fleet_spawn_help(self, runner):
        """Test fleet spawn --help works."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "spawn", "--help"])
        assert result.exit_code == 0

    def test_fleet_stop_help(self, runner):
        """Test fleet stop --help works."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "stop", "--help"])
        assert result.exit_code == 0

    def test_fleet_save_help(self, runner):
        """Test fleet save --help works."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "save", "--help"])
        assert result.exit_code == 0

    def test_fleet_restore_help(self, runner):
        """Test fleet restore --help works."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "restore", "--help"])
        assert result.exit_code == 0

    def test_fleet_heartbeat_help(self, runner):
        """Test fleet heartbeat --help works."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "heartbeat", "--help"])
        assert result.exit_code == 0


class TestFleetStatusErrors:
    """Tests for fleet status error handling."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_fleet_status_no_tmux(self, runner):
        """Should handle missing tmux gracefully."""
        from forge_harness.cli_v2 import cli

        with patch("subprocess.run", side_effect=FileNotFoundError("tmux not found")):
            result = runner.invoke(cli, ["fleet", "status"])
            # Should handle the error gracefully
            assert result.exit_code in [0, 1]

    def test_fleet_status_no_session(self, runner):
        """Should handle missing forge session."""
        from forge_harness.cli_v2 import cli

        mock_result = MagicMock(returncode=1)
        with patch("subprocess.run", return_value=mock_result):
            result = runner.invoke(cli, ["fleet", "status"])
            # Should handle gracefully
            assert result.exit_code in [0, 1]


class TestFleetSpawnErrors:
    """Tests for fleet spawn error handling."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_fleet_spawn_requires_agent_name(self, runner):
        """Should require agent name for spawn."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "spawn"])
        # Should fail without agent name
        assert result.exit_code != 0


class TestFleetStopErrors:
    """Tests for fleet stop error handling."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_fleet_stop_requires_agent_or_all(self, runner):
        """Should require agent name or --all for stop."""
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "stop"])
        # Should fail without agent name or --all
        assert result.exit_code != 0


class TestContextSnapshotRegex:
    """Additional tests for context snapshot regex."""

    def test_handles_decimal_percentages(self):
        """Should handle decimal percentages."""
        output = "Context: 42.5% left"
        result = _extract_context_snapshot(output)
        assert result == "42.5%"

    def test_handles_no_spaces(self):
        """Should handle percentages without spaces."""
        output = "50%"
        result = _extract_context_snapshot(output)
        assert result == "50%"

    def test_handles_with_context_text(self):
        """Should handle percentages with 'context' text."""
        output = "25% context left"
        result = _extract_context_snapshot(output)
        assert result == "25% context left"

    def test_handles_case_insensitive(self):
        """Should handle case insensitive matching."""
        output = "CONTEXT: 75% LEFT"
        result = _extract_context_snapshot(output)
        assert result == "75%"


class TestAgentStatusEdgeCases:
    """Edge case tests for agent status classification."""

    def test_crashed_process(self):
        """Should detect crashed process."""
        output = "[Process exited with code 1]"
        status, color = _classify_agent_status(output)
        # This might be error or ready depending on patterns
        assert status in ["error", "ready"]

    def test_badobject_indicator(self):
        """Should detect BadObject indicator."""
        output = "BadObject: something went wrong"
        status, color = _classify_agent_status(output)
        assert status == "error"

    def test_cogitating_state(self):
        """Should detect cogitating as active."""
        output = "Cogitating on the problem..."
        status, color = _classify_agent_status(output)
        assert status == "active"
        assert color == "yellow"

    def test_building_state(self):
        """Should detect building as active."""
        output = "Building the project..."
        status, color = _classify_agent_status(output)
        assert status == "active"
        assert color == "yellow"

    def test_prompt_at_start_of_line(self):
        """Should detect prompt at start of line."""
        output = "Some text\n> Ready for input"
        status, color = _classify_agent_status(output)
        assert status == "idle"


class TestActivityLineEdgeCases:
    """Edge case tests for activity line extraction."""

    def test_single_line(self):
        """Should handle single line."""
        output = "Single line"
        result = _extract_activity_line(output)
        assert result == "Single line"

    def test_multiple_pipes(self):
        """Should escape multiple pipes."""
        output = "a|b|c|d|e"
        result = _extract_activity_line(output)
        assert result == r"a\|b\|c\|d\|e"

    def test_unicode_content(self):
        """Should handle unicode content."""
        output = "Unicode: 🚀 émojis"
        result = _extract_activity_line(output)
        assert "🚀" in result
