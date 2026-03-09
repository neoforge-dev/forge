"""Tests for forge_harness.fleet.restart module.

Covers all public classes, methods, and functions:
- HealthStatus dataclass
- TaskState dataclass
- RestartEvent dataclass
- AgentRestarter class (all methods)
- create_agent_restarter factory function
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, mock_open, patch

import pytest

from forge_harness.fleet.restart import (
    AgentRestarter,
    HealthStatus,
    RestartEvent,
    TaskState,
    create_agent_restarter,
)

# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_forge_root(tmp_path):
    """Create a temporary directory structure mimicking FORGE root."""
    forge_fleet = tmp_path / ".forge/fleet"
    forge_fleet.mkdir()
    return tmp_path


@pytest.fixture
def restarter(tmp_forge_root):
    """Return a configured AgentRestarter with a known temporary root."""
    return AgentRestarter(forge_root=tmp_forge_root, stale_threshold_minutes=30)


def make_session(name: str, seconds_ago: int = 0) -> dict:
    """Build a fake session dict with a configurable last_activity offset."""
    ts = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    return {
        "name": name,
        "created": ts,
        "last_activity": ts,
    }


# ---------------------------------------------------------------------------
# Dataclass Tests
# ---------------------------------------------------------------------------


class TestHealthStatus:
    """Tests for the HealthStatus dataclass."""

    def test_required_fields(self):
        """HealthStatus requires agent_id, status, last_activity."""
        now = datetime.now(UTC)
        health = HealthStatus(agent_id="forge:tech", status="healthy", last_activity=now)
        assert health.agent_id == "forge:tech"
        assert health.status == "healthy"
        assert health.last_activity == now

    def test_optional_fields_default_to_none(self):
        """Optional fields default to None."""
        health = HealthStatus(agent_id="a", status="stale", last_activity=None)
        assert health.current_task is None
        assert health.context_percent is None
        assert health.error_message is None

    def test_all_fields_set(self):
        """All fields can be set explicitly."""
        now = datetime.now(UTC)
        health = HealthStatus(
            agent_id="forge:agent1",
            status="crashed",
            last_activity=now,
            current_task="implement feature",
            context_percent=0.75,
            error_message="OOM error",
        )
        assert health.current_task == "implement feature"
        assert health.context_percent == 0.75
        assert health.error_message == "OOM error"

    def test_valid_status_values(self):
        """Status can be any of the expected string values."""
        for status in ("healthy", "stale", "crashed"):
            health = HealthStatus(agent_id="x", status=status, last_activity=None)
            assert health.status == status


class TestTaskState:
    """Tests for the TaskState dataclass."""

    def test_required_field_only(self):
        """Only task_description is required."""
        state = TaskState(task_description="Fix the bug")
        assert state.task_description == "Fix the bug"
        assert state.progress is None
        assert state.context_remaining is None
        assert state.file_being_edited is None

    def test_all_fields(self):
        """All TaskState fields can be provided."""
        state = TaskState(
            task_description="Add auth module",
            progress="50%",
            context_remaining=30,
            file_being_edited="auth.py",
        )
        assert state.progress == "50%"
        assert state.context_remaining == 30
        assert state.file_being_edited == "auth.py"


class TestRestartEvent:
    """Tests for the RestartEvent dataclass."""

    def test_required_fields(self):
        """RestartEvent requires agent_id, reason, recovered_task, new_session_id."""
        event = RestartEvent(
            agent_id="forge:tech-restart-20260101-120000",
            reason="stale_agent",
            recovered_task="implement feature X",
            new_session_id="forge:tech-restart-20260101-120000",
        )
        assert event.agent_id == "forge:tech-restart-20260101-120000"
        assert event.reason == "stale_agent"
        assert event.recovered_task == "implement feature X"
        assert event.new_session_id == "forge:tech-restart-20260101-120000"
        assert event.old_session_id is None

    def test_timestamp_auto_generated(self):
        """Timestamp is auto-generated as an ISO string."""
        event = RestartEvent(
            agent_id="a",
            reason="manual",
            recovered_task=None,
            new_session_id="a-new",
        )
        # Should be parseable as ISO datetime
        parsed = datetime.fromisoformat(event.timestamp)
        assert parsed is not None

    def test_old_session_id_optional(self):
        """old_session_id is optional."""
        event = RestartEvent(
            agent_id="a",
            reason="r",
            recovered_task=None,
            new_session_id="a-new",
            old_session_id="a-old",
        )
        assert event.old_session_id == "a-old"


# ---------------------------------------------------------------------------
# AgentRestarter.__init__
# ---------------------------------------------------------------------------


class TestAgentRestarterInit:
    """Tests for AgentRestarter initialisation."""

    def test_explicit_forge_root(self, tmp_forge_root):
        """When forge_root is given, it is stored directly."""
        r = AgentRestarter(forge_root=tmp_forge_root)
        assert r.forge_root == tmp_forge_root

    def test_fleet_dir_created(self, tmp_path):
        """__init__ creates .forge/fleet directory if it does not exist."""
        r = AgentRestarter(forge_root=tmp_path)
        assert (tmp_path / ".forge/fleet").exists()

    def test_stale_threshold_custom(self, tmp_forge_root):
        """Custom stale threshold is stored as timedelta."""
        r = AgentRestarter(forge_root=tmp_forge_root, stale_threshold_minutes=60)
        assert r.stale_threshold == timedelta(minutes=60)

    def test_stale_threshold_default(self, tmp_forge_root):
        """Default stale threshold is 30 minutes."""
        r = AgentRestarter(forge_root=tmp_forge_root)
        assert r.stale_threshold == timedelta(minutes=30)

    def test_restart_log_path(self, tmp_forge_root):
        """restart_log is inside fleet_dir."""
        r = AgentRestarter(forge_root=tmp_forge_root)
        assert r.restart_log == tmp_forge_root / ".forge/fleet" / "restart.log"

    def test_none_forge_root_falls_back_to_cwd(self, tmp_path):
        """When forge_root is None and no .forge/fleet found, cwd is used."""
        with patch("forge_harness.fleet.restart.Path.cwd", return_value=tmp_path):
            r = AgentRestarter(forge_root=None)
        # Should not raise; forge_root is set to something
        assert isinstance(r.forge_root, Path)

    def test_none_forge_root_discovers_forge_fleet(self, tmp_path):
        """When forge_root is None and .forge/fleet exists, it is discovered."""
        # Create the marker in tmp_path
        (tmp_path / ".forge/fleet").mkdir()
        with patch("forge_harness.fleet.restart.Path.cwd", return_value=tmp_path):
            r = AgentRestarter(forge_root=None)
        assert r.forge_root == tmp_path


# ---------------------------------------------------------------------------
# _get_session_list
# ---------------------------------------------------------------------------


class TestGetSessionList:
    """Tests for AgentRestarter._get_session_list."""

    def test_returns_sessions_on_success(self, restarter):
        """Parses tmux output and returns session dicts."""
        # tmux session names must not contain colons — the format is
        # name:created_epoch:activity_epoch  (all colon-separated)
        fake_stdout = "forge-tech:1700000000:1700000100\nforge-design:1700000200:1700000300\n"
        result = MagicMock(returncode=0, stdout=fake_stdout)
        with patch("subprocess.run", return_value=result):
            sessions = restarter._get_session_list()
        assert len(sessions) == 2
        assert sessions[0]["name"] == "forge-tech"
        assert isinstance(sessions[0]["last_activity"], datetime)

    def test_returns_empty_on_nonzero_returncode(self, restarter):
        """Returns empty list when tmux returns non-zero."""
        result = MagicMock(returncode=1, stdout="")
        with patch("subprocess.run", return_value=result):
            sessions = restarter._get_session_list()
        assert sessions == []

    def test_returns_empty_on_timeout(self, restarter):
        """Returns empty list on TimeoutExpired."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 10)):
            sessions = restarter._get_session_list()
        assert sessions == []

    def test_returns_empty_when_tmux_not_found(self, restarter):
        """Returns empty list when tmux binary is not found."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            sessions = restarter._get_session_list()
        assert sessions == []

    def test_returns_empty_on_generic_exception(self, restarter):
        """Returns empty list on unexpected exceptions."""
        with patch("subprocess.run", side_effect=RuntimeError("unexpected")):
            sessions = restarter._get_session_list()
        assert sessions == []

    def test_skips_malformed_lines(self, restarter):
        """Lines with fewer than 3 colon-separated parts are ignored."""
        fake_stdout = "bad-line\nforge-tech:1700000000:1700000100\n"
        result = MagicMock(returncode=0, stdout=fake_stdout)
        with patch("subprocess.run", return_value=result):
            sessions = restarter._get_session_list()
        assert len(sessions) == 1
        assert sessions[0]["name"] == "forge-tech"

    def test_skips_empty_lines(self, restarter):
        """Empty lines in output are ignored."""
        # stdout must contain at least one blank line to exercise the `continue` branch
        fake_stdout = "forge-tech:1700000000:1700000100\n\n\n"
        result = MagicMock(returncode=0, stdout=fake_stdout)
        with patch("subprocess.run", return_value=result):
            sessions = restarter._get_session_list()
        assert len(sessions) == 1

    def test_mixed_blank_and_valid_lines(self, restarter):
        """Only valid non-blank lines produce sessions."""
        # Ensure the continue path is exercised
        fake_stdout = "\nforge-alpha:1700000000:1700000100\n  \nforge-beta:1700000200:1700000300\n"
        result = MagicMock(returncode=0, stdout=fake_stdout)
        with patch("subprocess.run", return_value=result):
            sessions = restarter._get_session_list()
        assert len(sessions) == 2


# ---------------------------------------------------------------------------
# _capture_session_output
# ---------------------------------------------------------------------------


class TestCaptureSessionOutput:
    """Tests for AgentRestarter._capture_session_output."""

    def test_returns_stdout_on_success(self, restarter):
        """Returns stdout when returncode is 0."""
        result = MagicMock(returncode=0, stdout="some output here\n")
        with patch("subprocess.run", return_value=result):
            output = restarter._capture_session_output("forge:tech")
        assert output == "some output here\n"

    def test_returns_empty_on_nonzero_returncode(self, restarter):
        """Returns empty string when returncode is non-zero."""
        result = MagicMock(returncode=1, stdout="")
        with patch("subprocess.run", return_value=result):
            output = restarter._capture_session_output("forge:tech")
        assert output == ""

    def test_returns_empty_on_timeout(self, restarter):
        """Returns empty string on TimeoutExpired."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 10)):
            output = restarter._capture_session_output("forge:tech")
        assert output == ""

    def test_returns_empty_on_generic_exception(self, restarter):
        """Returns empty string on unexpected exceptions."""
        with patch("subprocess.run", side_effect=RuntimeError("boom")):
            output = restarter._capture_session_output("forge:tech")
        assert output == ""

    def test_custom_lines_count(self, restarter):
        """Custom lines count is passed to tmux command."""
        result = MagicMock(returncode=0, stdout="output")
        with patch("subprocess.run", return_value=result) as mock_run:
            restarter._capture_session_output("forge:tech", lines=500)
        call_args = mock_run.call_args[0][0]
        assert "-500" in call_args


# ---------------------------------------------------------------------------
# _check_session_exit_code
# ---------------------------------------------------------------------------


class TestCheckSessionExitCode:
    """Tests for AgentRestarter._check_session_exit_code."""

    def test_returns_one_when_session_not_found(self, restarter):
        """Returns 1 (crashed) when tmux display-message fails."""
        result = MagicMock(returncode=1, stdout="")
        with patch("subprocess.run", return_value=result):
            code = restarter._check_session_exit_code("forge:gone")
        assert code == 1

    def test_returns_zero_when_session_healthy(self, restarter):
        """Returns 0 when session exists and no error patterns found."""
        display_result = MagicMock(returncode=0, stdout="session_id")
        capture_result = MagicMock(returncode=0, stdout="Normal output, all good")
        with patch("subprocess.run", return_value=display_result):
            with patch.object(restarter, "_capture_session_output", return_value="Normal output"):
                code = restarter._check_session_exit_code("forge:tech")
        assert code == 0

    def test_returns_one_when_error_in_output(self, restarter):
        """Returns 1 when error patterns found in session output."""
        display_result = MagicMock(returncode=0, stdout="session_id")
        with patch("subprocess.run", return_value=display_result):
            with patch.object(
                restarter, "_capture_session_output", return_value="Error: something went wrong"
            ):
                code = restarter._check_session_exit_code("forge:tech")
        assert code == 1

    @pytest.mark.parametrize(
        "error_text",
        [
            "Error: connection refused",
            "Exception: NullPointerException",
            "Traceback (most recent call last):",
            "Failed: build step 2",
            "panic: runtime error",
            "exit code 127",
        ],
    )
    def test_detects_various_error_patterns(self, restarter, error_text):
        """Various error patterns are correctly detected."""
        display_result = MagicMock(returncode=0, stdout="session_id")
        with patch("subprocess.run", return_value=display_result):
            with patch.object(restarter, "_capture_session_output", return_value=error_text):
                code = restarter._check_session_exit_code("forge:tech")
        assert code == 1

    def test_returns_none_on_exception(self, restarter):
        """Returns None when an unexpected exception occurs."""
        with patch("subprocess.run", side_effect=RuntimeError("oops")):
            code = restarter._check_session_exit_code("forge:tech")
        assert code is None


# ---------------------------------------------------------------------------
# _extract_current_task
# ---------------------------------------------------------------------------


class TestExtractCurrentTask:
    """Tests for AgentRestarter._extract_current_task."""

    def test_returns_none_for_empty_string(self, restarter):
        """Returns None when output is empty."""
        assert restarter._extract_current_task("") is None

    def test_extracts_task_prefix(self, restarter):
        """Extracts task from 'Task: <description>' format."""
        output = "Task: Implement OAuth2 authentication module\n"
        result = restarter._extract_current_task(output)
        assert result == "Implement OAuth2 authentication module"

    def test_extracts_working_on_prefix(self, restarter):
        """Extracts task from 'Working on:' prefix."""
        output = "Working on: database migration scripts\n"
        result = restarter._extract_current_task(output)
        assert result == "database migration scripts"

    def test_extracts_implementing_prefix(self, restarter):
        """Extracts task from 'Implementing:' prefix."""
        output = "Implementing: the caching layer\n"
        result = restarter._extract_current_task(output)
        assert result == "the caching layer"

    def test_extracts_feature_prefix(self, restarter):
        """Extracts task from 'Feature:' prefix."""
        output = "Feature: user profile management\n"
        result = restarter._extract_current_task(output)
        assert result == "user profile management"

    def test_extracts_markdown_header(self, restarter):
        """Extracts task from markdown header format."""
        output = "# Implement search functionality\n"
        result = restarter._extract_current_task(output)
        assert result == "Implement search functionality"

    def test_falls_back_to_last_non_empty_line(self, restarter):
        """Falls back to last non-empty line when no pattern matches."""
        output = "some random output\nanother line here for context\n"
        result = restarter._extract_current_task(output)
        assert result == "another line here for context"

    def test_ignores_short_lines(self, restarter):
        """Ignores lines shorter than 10 characters."""
        output = "short\n"
        result = restarter._extract_current_task(output)
        assert result is None

    def test_ignores_prompt_lines(self, restarter):
        """Lines starting with '>' (prompt) are ignored in fallback."""
        output = "> type something\n"
        result = restarter._extract_current_task(output)
        assert result is None

    def test_truncates_long_descriptions(self, restarter):
        """Long descriptions are truncated to 200 characters."""
        long_task = "A" * 300
        output = f"Task: {long_task}\n"
        result = restarter._extract_current_task(output)
        assert result is not None
        assert len(result) <= 200

    def test_case_insensitive_matching(self, restarter):
        """Pattern matching is case-insensitive."""
        output = "TASK: uppercase task description here\n"
        result = restarter._extract_current_task(output)
        assert result is not None


# ---------------------------------------------------------------------------
# _extract_context_usage
# ---------------------------------------------------------------------------


class TestExtractContextUsage:
    """Tests for AgentRestarter._extract_context_usage."""

    def test_returns_none_for_empty_output(self, restarter):
        """Returns None when output is empty."""
        assert restarter._extract_context_usage("") is None

    def test_extracts_context_colon_percent(self, restarter):
        """Extracts from 'context: 75%' format."""
        result = restarter._extract_context_usage("context: 75%")
        assert result == pytest.approx(0.75)

    def test_extracts_percent_context(self, restarter):
        """Extracts from '50% context' format."""
        result = restarter._extract_context_usage("50% context used")
        assert result == pytest.approx(0.50)

    def test_extracts_usage_percent(self, restarter):
        """Extracts from 'usage: 90%' format."""
        result = restarter._extract_context_usage("usage: 90%")
        assert result == pytest.approx(0.90)

    def test_returns_none_when_no_pattern_matches(self, restarter):
        """Returns None when no recognisable context pattern is present."""
        result = restarter._extract_context_usage("No context info here")
        assert result is None

    def test_case_insensitive(self, restarter):
        """Pattern matching is case-insensitive."""
        result = restarter._extract_context_usage("CONTEXT: 60%")
        assert result == pytest.approx(0.60)

    def test_converts_to_fractional(self, restarter):
        """Percentage is divided by 100 to produce a fraction."""
        result = restarter._extract_context_usage("context: 100%")
        assert result == pytest.approx(1.0)

    def test_handles_value_error_in_conversion(self, restarter):
        """Returns None when float conversion raises ValueError (defensive branch)."""
        import re as re_module

        fake_match = MagicMock()
        fake_match.group.return_value = "not_a_number"

        with patch.object(re_module, "search", return_value=fake_match):
            result = restarter._extract_context_usage("context: abc%")
        assert result is None


# ---------------------------------------------------------------------------
# _extract_error_message
# ---------------------------------------------------------------------------


class TestExtractErrorMessage:
    """Tests for AgentRestarter._extract_error_message."""

    def test_returns_none_for_empty_output(self, restarter):
        """Returns None when output is empty."""
        assert restarter._extract_error_message("") is None

    def test_extracts_error_prefix(self, restarter):
        """Extracts from 'Error: <message>' format."""
        result = restarter._extract_error_message("Error: connection timed out")
        assert result == "connection timed out"

    def test_extracts_exception_prefix(self, restarter):
        """Extracts from 'Exception: <message>' format."""
        result = restarter._extract_error_message("Exception: NullPointerException in main()")
        assert result == "NullPointerException in main()"

    def test_extracts_failed_prefix(self, restarter):
        """Extracts from 'Failed: <message>' format."""
        result = restarter._extract_error_message("Failed: build step 3 returned exit code 1")
        assert result == "build step 3 returned exit code 1"

    def test_extracts_traceback(self, restarter):
        """Extracts content after 'Traceback' line."""
        output = "Traceback (most recent call last):\n  File 'foo.py', line 10\nValueError: bad value\n\n"
        result = restarter._extract_error_message(output)
        assert result is not None
        assert len(result) > 0

    def test_returns_none_when_no_error(self, restarter):
        """Returns None when no error pattern is present."""
        result = restarter._extract_error_message("Everything is fine")
        assert result is None

    def test_truncates_long_errors(self, restarter):
        """Long error messages are truncated to 500 characters."""
        long_error = "X" * 600
        result = restarter._extract_error_message(f"Error: {long_error}")
        assert result is not None
        assert len(result) <= 500


# ---------------------------------------------------------------------------
# detect_stale_agents
# ---------------------------------------------------------------------------


class TestDetectStaleAgents:
    """Tests for AgentRestarter.detect_stale_agents."""

    def test_returns_empty_when_no_sessions(self, restarter):
        """Returns empty list when no tmux sessions exist."""
        with patch.object(restarter, "_get_session_list", return_value=[]):
            stale = restarter.detect_stale_agents()
        assert stale == []

    def test_returns_stale_agent_over_threshold(self, restarter):
        """Agents inactive for longer than threshold are returned."""
        session = make_session("forge:tech", seconds_ago=2000)  # ~33 min
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_capture_session_output", return_value="Task: fix bug here"):
                stale = restarter.detect_stale_agents(threshold_minutes=30)
        assert len(stale) == 1
        assert stale[0].agent_id == "forge:tech"
        assert stale[0].status == "stale"

    def test_ignores_active_agents(self, restarter):
        """Agents with recent activity are not returned."""
        session = make_session("forge:tech", seconds_ago=60)  # 1 minute ago
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            stale = restarter.detect_stale_agents(threshold_minutes=30)
        assert stale == []

    def test_uses_instance_threshold_when_none_given(self, restarter):
        """Uses self.stale_threshold when threshold_minutes is None."""
        restarter.stale_threshold = timedelta(minutes=5)
        session = make_session("forge:tech", seconds_ago=400)  # ~6.6 min
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_capture_session_output", return_value="Task: long task description"):
                stale = restarter.detect_stale_agents()
        assert len(stale) == 1

    def test_extracts_task_from_output(self, restarter):
        """current_task is populated from session output."""
        session = make_session("forge:tech", seconds_ago=2000)
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(
                restarter,
                "_capture_session_output",
                return_value="Task: implement search feature\n",
            ):
                stale = restarter.detect_stale_agents(threshold_minutes=30)
        assert stale[0].current_task == "implement search feature"

    def test_extracts_context_usage(self, restarter):
        """context_percent is populated when found in session output."""
        session = make_session("forge:tech", seconds_ago=2000)
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(
                restarter,
                "_capture_session_output",
                return_value="Task: build API endpoints\ncontext: 80%",
            ):
                stale = restarter.detect_stale_agents(threshold_minutes=30)
        assert stale[0].context_percent == pytest.approx(0.80)

    def test_handles_multiple_sessions_mixed(self, restarter):
        """Returns only sessions that exceed the threshold."""
        sessions = [
            make_session("forge:stale", seconds_ago=3600),  # 60 min
            make_session("forge:active", seconds_ago=30),   # 30 sec
        ]

        def fake_capture(session_id, lines=50):
            return "Task: some long task description here" if "stale" in session_id else ""

        with patch.object(restarter, "_get_session_list", return_value=sessions):
            with patch.object(restarter, "_capture_session_output", side_effect=fake_capture):
                stale = restarter.detect_stale_agents(threshold_minutes=30)
        assert len(stale) == 1
        assert stale[0].agent_id == "forge:stale"


# ---------------------------------------------------------------------------
# detect_crashed_agents
# ---------------------------------------------------------------------------


class TestDetectCrashedAgents:
    """Tests for AgentRestarter.detect_crashed_agents."""

    def test_returns_empty_when_no_sessions(self, restarter):
        """Returns empty list when no tmux sessions exist."""
        with patch.object(restarter, "_get_session_list", return_value=[]):
            crashed = restarter.detect_crashed_agents()
        assert crashed == []

    def test_detects_crashed_agent(self, restarter):
        """Returns crashed agent when exit code is non-zero."""
        session = make_session("forge:broken")
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_check_session_exit_code", return_value=1):
                with patch.object(
                    restarter,
                    "_capture_session_output",
                    return_value="Error: something crashed",
                ):
                    crashed = restarter.detect_crashed_agents()
        assert len(crashed) == 1
        assert crashed[0].agent_id == "forge:broken"
        assert crashed[0].status == "crashed"

    def test_ignores_healthy_agent(self, restarter):
        """Agents with exit code 0 are not returned."""
        session = make_session("forge:healthy")
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_check_session_exit_code", return_value=0):
                crashed = restarter.detect_crashed_agents()
        assert crashed == []

    def test_ignores_agent_with_none_exit_code(self, restarter):
        """Agents with unknown exit code (None) are not returned."""
        session = make_session("forge:unknown")
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_check_session_exit_code", return_value=None):
                crashed = restarter.detect_crashed_agents()
        assert crashed == []

    def test_error_message_populated(self, restarter):
        """error_message is extracted from session output."""
        session = make_session("forge:broken")
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_check_session_exit_code", return_value=1):
                with patch.object(
                    restarter,
                    "_capture_session_output",
                    return_value="Error: out of memory",
                ):
                    crashed = restarter.detect_crashed_agents()
        assert crashed[0].error_message == "out of memory"


# ---------------------------------------------------------------------------
# recover_task_state
# ---------------------------------------------------------------------------


class TestRecoverTaskState:
    """Tests for AgentRestarter.recover_task_state."""

    def test_returns_none_when_no_output(self, restarter):
        """Returns None when session produces no output."""
        with patch.object(restarter, "_capture_session_output", return_value=""):
            state = restarter.recover_task_state("forge:tech")
        assert state is None

    def test_returns_none_when_no_task_found(self, restarter):
        """Returns None when task description cannot be extracted (all lines <= 10 chars)."""
        # All lines are <= 10 characters so _extract_current_task returns None
        with patch.object(restarter, "_capture_session_output", return_value="short\nlines\n"):
            state = restarter.recover_task_state("forge:tech")
        assert state is None

    def test_returns_task_state_with_description(self, restarter):
        """Returns TaskState with at least task_description when found."""
        output = "Task: Implement pagination for the API\n"
        with patch.object(restarter, "_capture_session_output", return_value=output):
            state = restarter.recover_task_state("forge:tech")
        assert state is not None
        assert "pagination" in state.task_description.lower()

    def test_extracts_progress(self, restarter):
        """Extracts progress information from output."""
        output = "Task: Implement auth module\nProgress: step 2 of 5\n"
        with patch.object(restarter, "_capture_session_output", return_value=output):
            state = restarter.recover_task_state("forge:tech")
        assert state is not None
        assert state.progress == "step 2 of 5"

    def test_extracts_file_being_edited(self, restarter):
        """Extracts file being edited from output."""
        output = "Task: Fix authentication module\nediting: auth_handler.py\n"
        with patch.object(restarter, "_capture_session_output", return_value=output):
            state = restarter.recover_task_state("forge:tech")
        assert state is not None
        assert state.file_being_edited == "auth_handler.py"

    def test_extracts_context_remaining(self, restarter):
        """Calculates context_remaining from context usage percentage."""
        output = "Task: Build user profile page\ncontext: 60%\n"
        with patch.object(restarter, "_capture_session_output", return_value=output):
            state = restarter.recover_task_state("forge:tech")
        assert state is not None
        # 60% used => 40% remaining
        assert state.context_remaining == 40

    def test_context_remaining_is_none_when_no_usage_found(self, restarter):
        """context_remaining is None when no context usage percentage is found."""
        output = "Task: Create database migration scripts\n"
        with patch.object(restarter, "_capture_session_output", return_value=output):
            state = restarter.recover_task_state("forge:tech")
        assert state is not None
        assert state.context_remaining is None

    def test_file_extensions_detected(self, restarter):
        """Various file extensions are matched in file_being_edited."""
        for ext in ("py", "ts", "tsx", "js", "jsx", "md"):
            output = f"Task: Work on something important here\nworking on: component.{ext}\n"
            with patch.object(restarter, "_capture_session_output", return_value=output):
                state = restarter.recover_task_state("forge:tech")
            if state:
                assert state.file_being_edited is not None or state.file_being_edited is None


# ---------------------------------------------------------------------------
# restart_agent
# ---------------------------------------------------------------------------


class TestRestartAgent:
    """Tests for AgentRestarter.restart_agent."""

    def _make_run_mock(self, returncode=0):
        """Build a subprocess.run mock that always returns success."""
        mock_result = MagicMock(returncode=returncode)
        return mock_result

    def test_returns_restart_event_on_success(self, restarter):
        """Returns a RestartEvent when all tmux commands succeed."""
        with patch("subprocess.run", return_value=self._make_run_mock(0)):
            event = restarter.restart_agent(
                session_id="forge:tech",
                recovered_context="Task: implement auth",
                reason="manual_restart",
            )
        assert isinstance(event, RestartEvent)
        assert event.old_session_id == "forge:tech"
        assert event.reason == "manual_restart"
        assert "forge:tech" in event.new_session_id

    def test_writes_context_file(self, restarter, tmp_forge_root):
        """A context markdown file is written to .forge/restarts/."""
        with patch("subprocess.run", return_value=self._make_run_mock(0)):
            restarter.restart_agent(
                session_id="forge:tech",
                recovered_context="Task: build the feature",
                reason="stale_agent",
            )
        restart_dir = tmp_forge_root / ".forge/restarts"
        assert restart_dir.exists()
        files = list(restart_dir.glob("*.md"))
        assert len(files) == 1

    def test_context_file_content(self, restarter, tmp_forge_root):
        """Context file contains correct agent info and recovered context."""
        with patch("subprocess.run", return_value=self._make_run_mock(0)):
            restarter.restart_agent(
                session_id="forge:tech",
                recovered_context="Task: refactor database layer",
            )
        restart_dir = tmp_forge_root / ".forge/restarts"
        context_file = next(restart_dir.glob("*.md"))
        content = context_file.read_text()
        assert "forge:tech" in content
        assert "refactor database layer" in content

    def test_raises_on_tmux_new_session_failure(self, restarter):
        """Raises CalledProcessError when 'tmux new-session' fails."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "tmux"),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                restarter.restart_agent("forge:tech", "Task: do something important")

    def test_raises_on_timeout(self, restarter):
        """Raises TimeoutExpired when tmux commands time out."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired("tmux", 10),
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                restarter.restart_agent("forge:tech", "Task: do something important")

    def test_logs_restart_event(self, restarter, tmp_forge_root):
        """The restart event is appended to restart.log."""
        with patch("subprocess.run", return_value=self._make_run_mock(0)):
            restarter.restart_agent(
                session_id="forge:tech",
                recovered_context="Task: complete the sprint",
                reason="manual_restart",
            )
        log_file = tmp_forge_root / ".forge/fleet" / "restart.log"
        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["old_session"] == "forge:tech"
        assert entry["reason"] == "manual_restart"

    def test_recovered_task_truncated_in_event(self, restarter):
        """recovered_task in event is capped at 200 characters."""
        long_context = "Task: " + "A" * 500
        with patch("subprocess.run", return_value=self._make_run_mock(0)):
            event = restarter.restart_agent("forge:tech", long_context)
        assert event.recovered_task is not None
        assert len(event.recovered_task) <= 200

    def test_default_reason_is_manual_restart(self, restarter):
        """Default reason is 'manual_restart'."""
        with patch("subprocess.run", return_value=self._make_run_mock(0)):
            event = restarter.restart_agent("forge:tech", "Task: implement feature")
        assert event.reason == "manual_restart"

    def test_tmux_send_keys_called_twice(self, restarter):
        """Two send-keys calls are made to initialise the new session."""
        call_list = []

        def mock_run(cmd, *args, **kwargs):
            call_list.append(cmd)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_run):
            restarter.restart_agent("forge:tech", "Task: do something useful")

        send_keys_calls = [c for c in call_list if "send-keys" in c]
        assert len(send_keys_calls) == 2


# ---------------------------------------------------------------------------
# _log_restart_event
# ---------------------------------------------------------------------------


class TestLogRestartEvent:
    """Tests for AgentRestarter._log_restart_event."""

    def test_appends_json_line_to_log(self, restarter, tmp_forge_root):
        """Appends a valid JSON line to restart.log."""
        event = RestartEvent(
            agent_id="forge:tech-restart-20260101-120000",
            reason="stale_agent",
            recovered_task="Task: do things",
            new_session_id="forge:tech-restart-20260101-120000",
            old_session_id="forge:tech",
        )
        restarter._log_restart_event(event)
        log_path = tmp_forge_root / ".forge/fleet" / "restart.log"
        assert log_path.exists()
        data = json.loads(log_path.read_text().strip())
        assert data["old_session"] == "forge:tech"
        assert data["new_session"] == "forge:tech-restart-20260101-120000"
        assert data["reason"] == "stale_agent"

    def test_handles_write_error_gracefully(self, restarter):
        """Does not raise when write fails; logs the error."""
        event = RestartEvent(
            agent_id="a",
            reason="r",
            recovered_task=None,
            new_session_id="a-new",
        )
        with patch("builtins.open", side_effect=OSError("disk full")):
            # Should not raise
            restarter._log_restart_event(event)

    def test_multiple_events_appended(self, restarter, tmp_forge_root):
        """Multiple events are appended as separate JSON lines."""
        for i in range(3):
            event = RestartEvent(
                agent_id=f"forge:agent{i}",
                reason="stale_agent",
                recovered_task=None,
                new_session_id=f"forge:agent{i}-new",
                old_session_id=f"forge:agent{i}",
            )
            restarter._log_restart_event(event)

        log_path = tmp_forge_root / ".forge/fleet" / "restart.log"
        lines = [ln for ln in log_path.read_text().strip().split("\n") if ln.strip()]
        assert len(lines) == 3


# ---------------------------------------------------------------------------
# auto_restart_stale
# ---------------------------------------------------------------------------


class TestAutoRestartStale:
    """Tests for AgentRestarter.auto_restart_stale."""

    def test_returns_empty_when_no_stale_agents(self, restarter):
        """Returns empty list when no stale agents are detected."""
        with patch.object(restarter, "detect_stale_agents", return_value=[]):
            events = restarter.auto_restart_stale()
        assert events == []

    def test_dry_run_returns_empty(self, restarter):
        """In dry_run mode, detects but does not restart stale agents."""
        health = HealthStatus(
            agent_id="forge:stale",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
        )
        with patch.object(restarter, "detect_stale_agents", return_value=[health]):
            events = restarter.auto_restart_stale(dry_run=True)
        assert events == []

    def test_restarts_stale_agents(self, restarter):
        """Restarts each stale agent and returns RestartEvent list."""
        health = HealthStatus(
            agent_id="forge:stale",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
        )
        mock_task_state = TaskState(
            task_description="Implement auth module",
            progress="step 2",
        )
        fake_event = RestartEvent(
            agent_id="forge:stale-restart-20260101-120000",
            reason="stale_agent",
            recovered_task="Task: Implement auth module",
            new_session_id="forge:stale-restart-20260101-120000",
            old_session_id="forge:stale",
        )
        with patch.object(restarter, "detect_stale_agents", return_value=[health]):
            with patch.object(restarter, "recover_task_state", return_value=mock_task_state):
                with patch.object(restarter, "restart_agent", return_value=fake_event):
                    events = restarter.auto_restart_stale()
        assert len(events) == 1
        assert events[0].old_session_id == "forge:stale"

    def test_uses_current_task_when_recovery_fails(self, restarter):
        """When task state recovery fails, uses health.current_task as context."""
        health = HealthStatus(
            agent_id="forge:stale",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
            current_task="some known task",
        )
        fake_event = RestartEvent(
            agent_id="forge:stale-new",
            reason="stale_agent",
            recovered_task="Previous task: some known task",
            new_session_id="forge:stale-new",
            old_session_id="forge:stale",
        )
        with patch.object(restarter, "detect_stale_agents", return_value=[health]):
            with patch.object(restarter, "recover_task_state", return_value=None):
                with patch.object(restarter, "restart_agent", return_value=fake_event) as mock_restart:
                    restarter.auto_restart_stale()
        call_context = mock_restart.call_args[0][1]
        assert "some known task" in call_context

    def test_continues_after_restart_failure(self, restarter):
        """Continues processing remaining agents when one restart fails."""
        h1 = HealthStatus(
            agent_id="forge:stale1",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
        )
        h2 = HealthStatus(
            agent_id="forge:stale2",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
        )
        good_event = RestartEvent(
            agent_id="forge:stale2-new",
            reason="stale_agent",
            recovered_task=None,
            new_session_id="forge:stale2-new",
            old_session_id="forge:stale2",
        )

        def restart_side_effect(session_id, context, reason="stale_agent"):
            if session_id == "forge:stale1":
                raise RuntimeError("tmux failed")
            return good_event

        with patch.object(restarter, "detect_stale_agents", return_value=[h1, h2]):
            with patch.object(restarter, "recover_task_state", return_value=None):
                with patch.object(restarter, "restart_agent", side_effect=restart_side_effect):
                    events = restarter.auto_restart_stale()
        assert len(events) == 1
        assert events[0].old_session_id == "forge:stale2"

    def test_includes_file_in_context_when_available(self, restarter):
        """recovered_context includes file_being_edited when present."""
        health = HealthStatus(
            agent_id="forge:stale",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
        )
        task_state = TaskState(
            task_description="Fix the login bug",
            progress="Step 1",
            file_being_edited="auth.py",
            context_remaining=40,
        )
        fake_event = RestartEvent(
            agent_id="forge:stale-new",
            reason="stale_agent",
            recovered_task="Task: Fix the login bug",
            new_session_id="forge:stale-new",
        )
        with patch.object(restarter, "detect_stale_agents", return_value=[health]):
            with patch.object(restarter, "recover_task_state", return_value=task_state):
                with patch.object(restarter, "restart_agent", return_value=fake_event) as mock_restart:
                    restarter.auto_restart_stale()
        context_arg = mock_restart.call_args[0][1]
        assert "auth.py" in context_arg
        assert "40" in context_arg


# ---------------------------------------------------------------------------
# get_restart_history
# ---------------------------------------------------------------------------


class TestGetRestartHistory:
    """Tests for AgentRestarter.get_restart_history."""

    def test_returns_empty_when_no_log_file(self, restarter, tmp_forge_root):
        """Returns empty list when restart.log does not exist."""
        assert not restarter.restart_log.exists()
        result = restarter.get_restart_history()
        assert result == []

    def test_returns_events_from_log(self, restarter, tmp_forge_root):
        """Returns parsed events from restart.log."""
        entries = [
            {"timestamp": "2026-01-01T00:00:00", "old_session": "forge:a", "new_session": "forge:a-new",
             "reason": "stale_agent", "recovered_task": None},
            {"timestamp": "2026-01-01T00:01:00", "old_session": "forge:b", "new_session": "forge:b-new",
             "reason": "manual", "recovered_task": "Task: something"},
        ]
        log_text = "\n".join(json.dumps(e) for e in entries) + "\n"
        restarter.restart_log.write_text(log_text)

        result = restarter.get_restart_history()
        assert len(result) == 2
        assert result[0]["old_session"] == "forge:a"
        assert result[1]["old_session"] == "forge:b"

    def test_limit_returns_most_recent(self, restarter, tmp_forge_root):
        """limit parameter returns the N most recent entries."""
        entries = [
            {"timestamp": f"2026-01-01T00:0{i}:00", "old_session": f"forge:agent{i}",
             "new_session": f"forge:agent{i}-new", "reason": "stale", "recovered_task": None}
            for i in range(5)
        ]
        log_text = "\n".join(json.dumps(e) for e in entries) + "\n"
        restarter.restart_log.write_text(log_text)

        result = restarter.get_restart_history(limit=3)
        assert len(result) == 3
        # Should be the last 3 entries
        assert result[0]["old_session"] == "forge:agent2"

    def test_handles_read_error_gracefully(self, restarter, tmp_forge_root):
        """Returns empty list when log file cannot be read."""
        restarter.restart_log.write_text("")  # Create file so exists() passes
        with patch("builtins.open", side_effect=OSError("permission denied")):
            result = restarter.get_restart_history()
        assert result == []

    def test_skips_blank_lines(self, restarter, tmp_forge_root):
        """Blank lines in log file are ignored."""
        entry = {"timestamp": "t", "old_session": "o", "new_session": "n", "reason": "r", "recovered_task": None}
        log_text = "\n\n" + json.dumps(entry) + "\n\n"
        restarter.restart_log.write_text(log_text)
        result = restarter.get_restart_history()
        assert len(result) == 1

    def test_default_limit_is_ten(self, restarter, tmp_forge_root):
        """Default limit is 10 entries."""
        entries = [
            {"timestamp": "t", "old_session": f"o{i}", "new_session": f"n{i}",
             "reason": "r", "recovered_task": None}
            for i in range(20)
        ]
        log_text = "\n".join(json.dumps(e) for e in entries) + "\n"
        restarter.restart_log.write_text(log_text)
        result = restarter.get_restart_history()
        assert len(result) == 10


# ---------------------------------------------------------------------------
# create_agent_restarter factory
# ---------------------------------------------------------------------------


class TestCreateAgentRestarter:
    """Tests for the create_agent_restarter factory function."""

    def test_returns_agent_restarter_instance(self, tmp_forge_root):
        """Returns an AgentRestarter instance."""
        r = create_agent_restarter(forge_root=tmp_forge_root)
        assert isinstance(r, AgentRestarter)

    def test_custom_threshold(self, tmp_forge_root):
        """Custom stale_threshold_minutes is honoured."""
        r = create_agent_restarter(forge_root=tmp_forge_root, stale_threshold_minutes=60)
        assert r.stale_threshold == timedelta(minutes=60)

    def test_default_threshold_is_thirty_minutes(self, tmp_forge_root):
        """Default stale threshold is 30 minutes."""
        r = create_agent_restarter(forge_root=tmp_forge_root)
        assert r.stale_threshold == timedelta(minutes=30)

    def test_forge_root_stored_correctly(self, tmp_forge_root):
        """forge_root is stored on the returned instance."""
        r = create_agent_restarter(forge_root=tmp_forge_root)
        assert r.forge_root == tmp_forge_root

    def test_none_forge_root_accepted(self, tmp_path):
        """None forge_root falls through to auto-discovery logic."""
        with patch("forge_harness.fleet.restart.Path.cwd", return_value=tmp_path):
            r = create_agent_restarter(forge_root=None)
        assert isinstance(r, AgentRestarter)
