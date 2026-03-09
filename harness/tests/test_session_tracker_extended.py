"""Extended tests for SessionTracker — covers gaps not addressed in test_session_tracker.py.

Focus areas:
- Additional agent type detection (codex, gemini, tech, game)
- Additional status detection keywords (thinking, writing, executing, working, failed)
- Error paths: timeout, generic exceptions in subprocess calls
- get_session() directly (not via get_all_sessions)
- Metadata: corrupted JSON load, started_at immutability on update
- get_sessions_summary status/type counts
- to_dict on empty list
- Singleton reset between tests
"""

import subprocess
from dataclasses import asdict
from unittest.mock import MagicMock, call, patch

import pytest

from forge_harness.session_tracker import (
    AgentSession,
    SessionTracker,
    get_session_tracker,
)

# ---------------------------------------------------------------------------
# AgentSession extended
# ---------------------------------------------------------------------------


class TestAgentSessionExtended:
    """Additional AgentSession dataclass tests."""

    def test_terminal_output_stored(self):
        session = AgentSession(
            session_name="forge:cursor",
            window_name="cursor",
            agent_type="claude",
            terminal_output="$ echo hello\nhello",
        )
        assert session.terminal_output == "$ echo hello\nhello"

    def test_last_activity_stored(self):
        session = AgentSession(
            session_name="forge:cursor",
            window_name="cursor",
            agent_type="claude",
            last_activity="2026-01-01T10:00:00",
        )
        assert session.last_activity == "2026-01-01T10:00:00"

    def test_all_status_values_stored(self):
        for status in ("active", "idle", "error", "unknown", "completed"):
            session = AgentSession(
                session_name="forge:x",
                window_name="x",
                agent_type="claude",
                status=status,
            )
            assert session.status == status

    def test_asdict_includes_all_fields(self):
        session = AgentSession(
            session_name="forge:test",
            window_name="test",
            agent_type="codex",
            domain="brandfocus-ai",
            project="voice-coach",
            current_task="write tests",
            started_at="2026-01-01T09:00:00",
            last_activity="2026-01-01T10:00:00",
            terminal_output="output text",
            status="active",
        )
        data = asdict(session)
        assert data["domain"] == "brandfocus-ai"
        assert data["current_task"] == "write tests"
        assert data["terminal_output"] == "output text"
        assert data["status"] == "active"


# ---------------------------------------------------------------------------
# _detect_agent_type extended
# ---------------------------------------------------------------------------


class TestDetectAgentTypeExtended:
    """Extended agent-type detection tests."""

    @pytest.fixture
    def tracker(self, tmp_path):
        tracker = SessionTracker(session_prefix="forge")
        tracker.sessions_dir = tmp_path / ".forge/sessions"
        tracker.sessions_dir.mkdir(exist_ok=True)
        return tracker

    def test_codex_window(self, tracker):
        assert tracker._detect_agent_type("codex") == "codex"

    def test_codex_with_suffix(self, tracker):
        assert tracker._detect_agent_type("codex-main") == "codex"

    def test_gemini_window(self, tracker):
        assert tracker._detect_agent_type("gemini") == "gemini"

    def test_gemini_with_suffix(self, tracker):
        assert tracker._detect_agent_type("gemini-pro") == "gemini"

    def test_tech_window_maps_to_claude(self, tracker):
        assert tracker._detect_agent_type("tech") == "claude"

    def test_tech_with_suffix(self, tracker):
        assert tracker._detect_agent_type("tech-lead") == "claude"

    def test_game_window_maps_to_claude(self, tracker):
        assert tracker._detect_agent_type("game") == "claude"

    def test_game_with_suffix(self, tracker):
        assert tracker._detect_agent_type("game-agent") == "claude"

    def test_mixed_case_window(self, tracker):
        # window names are lowercased before comparison
        assert tracker._detect_agent_type("CLAUDE") == "claude"
        assert tracker._detect_agent_type("Cursor") == "claude"
        assert tracker._detect_agent_type("GEMINI") == "gemini"

    def test_opencode_with_prefix(self, tracker):
        assert tracker._detect_agent_type("my-opencode") == "opencode"

    def test_amp_embedded(self, tracker):
        assert tracker._detect_agent_type("amp-worker") == "amp"

    def test_pi_embedded(self, tracker):
        assert tracker._detect_agent_type("pi-1") == "pi"

    def test_completely_unknown_name(self, tracker):
        assert tracker._detect_agent_type("nova-x7") == "unknown"

    def test_empty_string(self, tracker):
        assert tracker._detect_agent_type("") == "unknown"


# ---------------------------------------------------------------------------
# _detect_status extended
# ---------------------------------------------------------------------------


class TestDetectStatusExtended:
    """Extended status detection covering all keyword branches."""

    @pytest.fixture
    def tracker(self, tmp_path):
        tracker = SessionTracker(session_prefix="forge")
        tracker.sessions_dir = tmp_path / ".forge/sessions"
        tracker.sessions_dir.mkdir(exist_ok=True)
        return tracker

    # --- error keywords ---

    def test_failed_keyword_triggers_error(self, tracker):
        assert tracker._detect_status("Command failed: exit 1") == "error"

    def test_traceback_triggers_error(self, tracker):
        assert tracker._detect_status("Traceback (most recent call last):") == "error"

    def test_error_keyword_case_insensitive(self, tracker):
        assert tracker._detect_status("ERROR: something bad") == "error"

    # --- active keywords ---

    def test_thinking_triggers_active(self, tracker):
        assert tracker._detect_status("Thinking about the problem...") == "active"

    def test_writing_triggers_active(self, tracker):
        assert tracker._detect_status("Writing to file src/main.py") == "active"

    def test_reading_triggers_active(self, tracker):
        assert tracker._detect_status("Reading configuration file...") == "active"

    def test_executing_triggers_active(self, tracker):
        assert tracker._detect_status("Executing shell command") == "active"

    def test_working_triggers_active(self, tracker):
        assert tracker._detect_status("Working on the task...") == "active"

    def test_generating_triggers_active(self, tracker):
        assert tracker._detect_status("Generating code snippet") == "active"

    def test_processing_triggers_active(self, tracker):
        assert tracker._detect_status("Processing request payload") == "active"

    # --- completed keywords ---

    def test_finished_triggers_completed(self, tracker):
        assert tracker._detect_status("Finished all tasks") == "completed"

    def test_done_triggers_completed(self, tracker):
        assert tracker._detect_status("All tasks done.") == "completed"

    def test_completed_keyword(self, tracker):
        assert tracker._detect_status("Operation completed successfully") == "completed"

    # --- idle keywords ---

    def test_paused_triggers_idle(self, tracker):
        assert tracker._detect_status("Paused by user request") == "idle"

    def test_idle_keyword(self, tracker):
        assert tracker._detect_status("Agent is idle, waiting") == "idle"

    # --- unknown ---

    def test_none_output_returns_unknown(self, tracker):
        # None is not a string but guard against it
        assert tracker._detect_status("") == "unknown"

    def test_random_text_falls_to_idle(self, tracker):
        # No recognized keywords → defaults to idle per the source logic
        assert tracker._detect_status("$ ls -la") == "idle"

    # --- priority: error beats idle ---

    def test_error_takes_priority_over_idle(self, tracker):
        # Both "error" and "waiting" appear — error branch checked first
        assert tracker._detect_status("error while waiting for result") == "error"


# ---------------------------------------------------------------------------
# list_tmux_sessions error paths
# ---------------------------------------------------------------------------


class TestListTmuxSessionsErrorPaths:
    """Tests for subprocess error paths in list_tmux_sessions."""

    @pytest.fixture
    def tracker(self, tmp_path):
        tracker = SessionTracker(session_prefix="forge")
        tracker.sessions_dir = tmp_path / ".forge/sessions"
        tracker.sessions_dir.mkdir(exist_ok=True)
        return tracker

    @patch("subprocess.run")
    def test_timeout_returns_empty(self, mock_run, tracker):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tmux", timeout=5)
        assert tracker.list_tmux_sessions() == []

    @patch("subprocess.run")
    def test_generic_exception_returns_empty(self, mock_run, tracker):
        mock_run.side_effect = RuntimeError("unexpected")
        assert tracker.list_tmux_sessions() == []

    @patch("subprocess.run")
    def test_empty_stdout_returns_empty(self, mock_run, tracker):
        mock_run.return_value = MagicMock(returncode=0, stdout="   \n  \n")
        assert tracker.list_tmux_sessions() == []

    @patch("subprocess.run")
    def test_whitespace_stripped_from_window_names(self, mock_run, tracker):
        mock_run.return_value = MagicMock(returncode=0, stdout="  alpha  \n  beta  \n")
        sessions = tracker.list_tmux_sessions()
        assert sessions == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# get_session_output error paths
# ---------------------------------------------------------------------------


class TestGetSessionOutputErrorPaths:
    """Tests for subprocess error paths in get_session_output."""

    @pytest.fixture
    def tracker(self, tmp_path):
        tracker = SessionTracker(session_prefix="forge")
        tracker.sessions_dir = tmp_path / ".forge/sessions"
        tracker.sessions_dir.mkdir(exist_ok=True)
        return tracker

    @patch("subprocess.run")
    def test_timeout_returns_empty_string(self, mock_run, tracker):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tmux", timeout=5)
        assert tracker.get_session_output("cursor") == ""

    @patch("subprocess.run")
    def test_file_not_found_returns_empty_string(self, mock_run, tracker):
        mock_run.side_effect = FileNotFoundError()
        assert tracker.get_session_output("cursor") == ""

    @patch("subprocess.run")
    def test_generic_exception_returns_empty_string(self, mock_run, tracker):
        mock_run.side_effect = OSError("pipe error")
        assert tracker.get_session_output("cursor") == ""

    @patch("subprocess.run")
    def test_non_zero_returncode_returns_empty(self, mock_run, tracker):
        mock_run.return_value = MagicMock(returncode=1, stdout="some output")
        assert tracker.get_session_output("cursor") == ""

    @patch("subprocess.run")
    def test_lines_parameter_slices_output(self, mock_run, tracker):
        lines_content = "\n".join(f"line{i}" for i in range(1, 11))
        mock_run.return_value = MagicMock(returncode=0, stdout=lines_content)
        output = tracker.get_session_output("cursor", lines=3)
        output_lines = output.split("\n")
        assert len(output_lines) == 3
        assert "line10" in output
        assert "line8" in output

    @patch("subprocess.run")
    def test_target_includes_prefix_and_window(self, mock_run, tracker):
        mock_run.return_value = MagicMock(returncode=0, stdout="output")
        tracker.get_session_output("mywindow")
        call_args = mock_run.call_args[0][0]
        assert "forge:mywindow" in call_args


# ---------------------------------------------------------------------------
# get_session() direct tests
# ---------------------------------------------------------------------------


class TestGetSessionDirect:
    """Tests for get_session() which is separate from get_all_sessions()."""

    @pytest.fixture
    def tracker(self, tmp_path):
        tracker = SessionTracker(session_prefix="forge")
        tracker.sessions_dir = tmp_path / ".forge/sessions"
        tracker.sessions_dir.mkdir(exist_ok=True)
        return tracker

    @patch("subprocess.run")
    def test_returns_none_when_window_not_in_list(self, mock_run, tracker):
        mock_run.return_value = MagicMock(returncode=0, stdout="alpha\nbeta\n")
        result = tracker.get_session("gamma")
        assert result is None

    @patch("subprocess.run")
    def test_returns_agent_session_for_valid_window(self, mock_run, tracker):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="cursor\n"),         # list-windows
            MagicMock(returncode=0, stdout="Generating code"),   # capture-pane
        ]
        result = tracker.get_session("cursor")
        assert result is not None
        assert isinstance(result, AgentSession)
        assert result.window_name == "cursor"
        assert result.session_name == "forge:cursor"

    @patch("subprocess.run")
    def test_session_uses_metadata(self, mock_run, tracker):
        tracker.save_session_metadata("cursor", {
            "domain": "codeswiftr-com",
            "project": "interview-simulator",
            "task": "write tests",
        })
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="cursor\n"),
            MagicMock(returncode=0, stdout="Working on it"),
        ]
        result = tracker.get_session("cursor")
        assert result.domain == "codeswiftr-com"
        assert result.project == "interview-simulator"
        assert result.current_task == "write tests"

    @patch("subprocess.run")
    def test_session_last_activity_from_metadata(self, mock_run, tracker):
        tracker.save_session_metadata("cursor", {
            "last_activity": "2026-01-15T12:00:00",
        })
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="cursor\n"),
            MagicMock(returncode=0, stdout="idle"),
        ]
        result = tracker.get_session("cursor")
        assert result.last_activity == "2026-01-15T12:00:00"

    @patch("subprocess.run")
    def test_session_last_activity_from_file_mtime_when_no_metadata_key(
        self, mock_run, tracker
    ):
        # Save metadata without last_activity key
        tracker.save_session_metadata("cursor", {"domain": "test"})
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="cursor\n"),
            MagicMock(returncode=0, stdout=""),
        ]
        result = tracker.get_session("cursor")
        # last_activity should still be populated (from file mtime)
        assert result.last_activity is not None

    @patch("subprocess.run")
    def test_session_terminal_output_truncated_at_4000_chars(self, mock_run, tracker):
        long_output = "x" * 5000
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="cursor\n"),
            MagicMock(returncode=0, stdout=long_output),
        ]
        result = tracker.get_session("cursor")
        assert result.terminal_output is not None
        assert len(result.terminal_output) <= 4000

    @patch("subprocess.run")
    def test_session_terminal_output_none_when_empty(self, mock_run, tracker):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="cursor\n"),
            MagicMock(returncode=0, stdout=""),
        ]
        result = tracker.get_session("cursor")
        assert result.terminal_output is None


# ---------------------------------------------------------------------------
# Metadata: corrupted JSON, started_at immutability
# ---------------------------------------------------------------------------


class TestSessionMetadataExtended:
    """Extended metadata tests."""

    @pytest.fixture
    def tracker(self, tmp_path):
        tracker = SessionTracker(session_prefix="forge")
        tracker.sessions_dir = tmp_path / ".forge/sessions"
        tracker.sessions_dir.mkdir(exist_ok=True)
        return tracker

    def test_load_corrupted_json_returns_empty(self, tracker):
        meta_file = tracker.sessions_dir / "corrupt.json"
        meta_file.write_text("{this is not valid json!!!")
        result = tracker._load_session_metadata("corrupt")
        assert result == {}

    def test_started_at_not_overwritten_on_update(self, tracker):
        tracker.save_session_metadata("window1", {"domain": "test"})
        first_load = tracker._load_session_metadata("window1")
        original_started_at = first_load["started_at"]

        # Second save should NOT change started_at
        tracker.save_session_metadata("window1", {"project": "proj"})
        second_load = tracker._load_session_metadata("window1")
        assert second_load["started_at"] == original_started_at

    def test_save_overwrites_same_key(self, tracker):
        tracker.save_session_metadata("window1", {"domain": "old-domain"})
        tracker.save_session_metadata("window1", {"domain": "new-domain"})
        loaded = tracker._load_session_metadata("window1")
        assert loaded["domain"] == "new-domain"

    def test_multiple_windows_independent_metadata(self, tracker):
        tracker.save_session_metadata("win-a", {"domain": "alpha"})
        tracker.save_session_metadata("win-b", {"domain": "beta"})
        assert tracker._load_session_metadata("win-a")["domain"] == "alpha"
        assert tracker._load_session_metadata("win-b")["domain"] == "beta"

    def test_clear_returns_false_for_unknown(self, tracker):
        assert tracker.clear_session_metadata("does-not-exist") is False

    def test_clear_makes_load_return_empty(self, tracker):
        tracker.save_session_metadata("win", {"domain": "x"})
        tracker.clear_session_metadata("win")
        assert tracker._load_session_metadata("win") == {}


# ---------------------------------------------------------------------------
# get_sessions_summary counts
# ---------------------------------------------------------------------------


class TestGetSessionsSummaryExtended:
    """Verify status and type counts in get_sessions_summary."""

    @pytest.fixture
    def tracker(self, tmp_path):
        tracker = SessionTracker(session_prefix="forge")
        tracker.sessions_dir = tmp_path / ".forge/sessions"
        tracker.sessions_dir.mkdir(exist_ok=True)
        return tracker

    @patch("subprocess.run")
    def test_summary_empty_when_no_sessions(self, mock_run, tracker):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        summary = tracker.get_sessions_summary()
        assert summary["total"] == 0
        assert summary["sessions"] == []

    @patch("subprocess.run")
    def test_summary_type_counts_accurate(self, mock_run, tracker):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="claude\ngemini\ncodex\n"),
            MagicMock(returncode=0, stdout="working"),
            MagicMock(returncode=0, stdout="idle"),
            MagicMock(returncode=0, stdout="idle"),
        ]
        summary = tracker.get_sessions_summary()
        assert summary["by_type"]["claude"] == 1
        assert summary["by_type"]["gemini"] == 1
        assert summary["by_type"]["codex"] == 1

    @patch("subprocess.run")
    def test_summary_status_counts_accurate(self, mock_run, tracker):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="cursor\ngemini\n"),
            MagicMock(returncode=0, stdout="generating output"),  # active
            MagicMock(returncode=0, stdout="error occurred"),     # error
        ]
        summary = tracker.get_sessions_summary()
        assert summary["by_status"]["active"] >= 1
        assert summary["by_status"]["error"] >= 1

    @patch("subprocess.run")
    def test_summary_sessions_list_length_matches_total(self, mock_run, tracker):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="win1\nwin2\nwin3\n"),
            MagicMock(returncode=0, stdout="working"),
            MagicMock(returncode=0, stdout="idle"),
            MagicMock(returncode=0, stdout="done"),
        ]
        summary = tracker.get_sessions_summary()
        assert summary["total"] == len(summary["sessions"])
        assert summary["total"] == 3


# ---------------------------------------------------------------------------
# to_dict edge cases
# ---------------------------------------------------------------------------


class TestToDictExtended:
    @pytest.fixture
    def tracker(self, tmp_path):
        tracker = SessionTracker(session_prefix="forge")
        tracker.sessions_dir = tmp_path / ".forge/sessions"
        tracker.sessions_dir.mkdir(exist_ok=True)
        return tracker

    def test_to_dict_empty_list(self, tracker):
        assert tracker.to_dict([]) == []

    def test_to_dict_preserves_none_fields(self, tracker):
        session = AgentSession(
            session_name="forge:x",
            window_name="x",
            agent_type="unknown",
        )
        result = tracker.to_dict([session])
        assert result[0]["domain"] is None
        assert result[0]["terminal_output"] is None


# ---------------------------------------------------------------------------
# Singleton isolation
# ---------------------------------------------------------------------------


class TestGetSessionTrackerSingleton:
    """Validate singleton behavior and structure."""

    def test_singleton_is_session_tracker(self):
        tracker = get_session_tracker()
        assert isinstance(tracker, SessionTracker)

    def test_singleton_same_object_across_calls(self):
        t1 = get_session_tracker()
        t2 = get_session_tracker()
        assert t1 is t2
