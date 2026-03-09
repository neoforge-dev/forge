"""Tests for SessionTracker module."""

from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.session_tracker import (
    AgentSession,
    SessionTracker,
    get_session_tracker,
)


class TestAgentSession:
    """Test AgentSession dataclass."""

    def test_creation_with_defaults(self):
        """Test creating a session with default values."""
        session = AgentSession(
            session_name="forge:test",
            window_name="test",
            agent_type="claude",
        )
        assert session.session_name == "forge:test"
        assert session.window_name == "test"
        assert session.agent_type == "claude"
        assert session.domain is None
        assert session.status == "unknown"

    def test_creation_with_all_fields(self):
        """Test creating a session with all fields."""
        session = AgentSession(
            session_name="forge:cursor",
            window_name="cursor",
            agent_type="claude",
            domain="codeswiftr-com",
            project="interview-simulator",
            current_task="Implement feature X",
            started_at="2025-01-27T10:00:00",
            status="active",
        )
        assert session.domain == "codeswiftr-com"
        assert session.project == "interview-simulator"
        assert session.status == "active"

    def test_to_dict(self):
        """Test converting session to dict."""
        session = AgentSession(
            session_name="forge:test",
            window_name="test",
            agent_type="claude",
        )
        data = asdict(session)
        assert data["session_name"] == "forge:test"
        assert "status" in data


class TestSessionTracker:
    """Test SessionTracker class."""

    @pytest.fixture
    def tracker(self, tmp_path):
        """Create a tracker with temporary directory."""
        tracker = SessionTracker(session_prefix="test")
        tracker.sessions_dir = tmp_path / ".forge/sessions"
        tracker.sessions_dir.mkdir(exist_ok=True)
        return tracker

    def test_init(self, tracker, tmp_path):
        """Test tracker initialization."""
        assert tracker.session_prefix == "test"
        assert tracker.sessions_dir.exists()

    @patch("subprocess.run")
    def test_list_tmux_sessions_success(self, mock_run, tracker):
        """Test listing tmux sessions when tmux is running."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="claude\ncursor\ngame\n",
        )

        sessions = tracker.list_tmux_sessions()

        assert sessions == ["claude", "cursor", "game"]
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_list_tmux_sessions_no_sessions(self, mock_run, tracker):
        """Test listing tmux sessions when none exist."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        sessions = tracker.list_tmux_sessions()

        assert sessions == []

    @patch("subprocess.run")
    def test_list_tmux_sessions_tmux_not_installed(self, mock_run, tracker):
        """Test listing sessions when tmux is not installed."""
        mock_run.side_effect = FileNotFoundError()

        sessions = tracker.list_tmux_sessions()

        assert sessions == []

    @patch("subprocess.run")
    def test_get_session_output_success(self, mock_run, tracker):
        """Test getting terminal output from a session."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n",
        )

        output = tracker.get_session_output("claude", lines=3)

        assert "Line 3" in output
        assert "Line 4" in output
        assert "Line 5" in output

    @patch("subprocess.run")
    def test_get_session_output_failure(self, mock_run, tracker):
        """Test getting output when session doesn't exist."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        output = tracker.get_session_output("nonexistent")

        assert output == ""

    def test_detect_agent_type(self, tracker):
        """Test agent type detection from window name."""
        assert tracker._detect_agent_type("claude") == "claude"
        assert tracker._detect_agent_type("cursor") == "claude"
        assert tracker._detect_agent_type("pi-agent") == "pi"
        assert tracker._detect_agent_type("amp") == "amp"
        assert tracker._detect_agent_type("opencode-main") == "opencode"
        assert tracker._detect_agent_type("unknown-window") == "unknown"

    def test_detect_status_error(self, tracker):
        """Test status detection for errors."""
        assert tracker._detect_status("Error: Something failed") == "error"
        assert tracker._detect_status("Command failed with exit code 1") == "error"
        assert tracker._detect_status("Traceback (most recent call last):") == "error"

    def test_detect_status_active(self, tracker):
        """Test status detection for active state."""
        assert tracker._detect_status("Generating code...") == "active"
        assert tracker._detect_status("Running tests...") == "active"
        assert tracker._detect_status("Processing request...") == "active"

    def test_detect_status_idle(self, tracker):
        """Test status detection for idle state."""
        assert tracker._detect_status("Waiting for input...") == "idle"
        assert tracker._detect_status("Agent is idle") == "idle"
        assert tracker._detect_status("Paused by user") == "idle"

    def test_detect_status_completed(self, tracker):
        """Test status detection for completed state."""
        assert tracker._detect_status("Task completed successfully") == "completed"
        assert tracker._detect_status("All done!") == "completed"
        assert tracker._detect_status("Session finished") == "completed"

    def test_detect_status_unknown(self, tracker):
        """Test status detection for unknown state."""
        assert tracker._detect_status("") == "unknown"
        assert tracker._detect_status("Some random text") == "idle"  # Default to idle

    def test_save_and_load_session_metadata(self, tracker):
        """Test saving and loading session metadata."""
        metadata = {
            "domain": "codeswiftr-com",
            "project": "interview-simulator",
            "task": "Implement feature X",
        }

        tracker.save_session_metadata("claude", metadata)
        loaded = tracker._load_session_metadata("claude")

        assert loaded["domain"] == "codeswiftr-com"
        assert loaded["project"] == "interview-simulator"
        assert "started_at" in loaded  # Auto-added

    def test_save_metadata_merges_existing(self, tracker):
        """Test that saving metadata merges with existing."""
        tracker.save_session_metadata("claude", {"domain": "test"})
        tracker.save_session_metadata("claude", {"project": "new-project"})

        loaded = tracker._load_session_metadata("claude")

        assert loaded["domain"] == "test"
        assert loaded["project"] == "new-project"

    def test_load_nonexistent_metadata(self, tracker):
        """Test loading metadata for nonexistent session."""
        loaded = tracker._load_session_metadata("nonexistent")
        assert loaded == {}

    def test_clear_session_metadata(self, tracker):
        """Test clearing session metadata."""
        tracker.save_session_metadata("claude", {"domain": "test"})
        assert tracker.clear_session_metadata("claude") is True

        loaded = tracker._load_session_metadata("claude")
        assert loaded == {}

    def test_clear_nonexistent_metadata(self, tracker):
        """Test clearing metadata for nonexistent session."""
        assert tracker.clear_session_metadata("nonexistent") is False

    @patch("subprocess.run")
    def test_get_all_sessions(self, mock_run, tracker):
        """Test getting all sessions with metadata."""
        # Mock list-windows call
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="claude\ncursor\n"),  # list-windows
            MagicMock(returncode=0, stdout="Generating code..."),  # capture-pane claude
            MagicMock(returncode=0, stdout="Waiting for input..."),  # capture-pane cursor
        ]

        tracker.save_session_metadata(
            "claude",
            {
                "domain": "codeswiftr-com",
                "project": "test-project",
            },
        )

        sessions = tracker.get_all_sessions()

        assert len(sessions) == 2
        assert sessions[0].window_name == "claude"
        assert sessions[0].domain == "codeswiftr-com"
        assert sessions[0].status == "active"
        assert sessions[1].window_name == "cursor"
        assert sessions[1].status == "idle"

    @patch("subprocess.run")
    def test_get_sessions_summary(self, mock_run, tracker):
        """Test getting sessions summary."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="claude\ncursor\n"),
            MagicMock(returncode=0, stdout="Running..."),
            MagicMock(returncode=0, stdout="Error occurred"),
        ]

        summary = tracker.get_sessions_summary()

        assert summary["total"] == 2
        assert "by_status" in summary
        assert "by_type" in summary
        assert "sessions" in summary

    def test_to_dict(self, tracker):
        """Test converting sessions list to dict."""
        sessions = [
            AgentSession(session_name="forge:a", window_name="a", agent_type="claude"),
            AgentSession(session_name="forge:b", window_name="b", agent_type="pi"),
        ]

        result = tracker.to_dict(sessions)

        assert len(result) == 2
        assert result[0]["session_name"] == "forge:a"
        assert result[1]["agent_type"] == "pi"


class TestGetSessionTracker:
    """Test singleton getter."""

    def test_returns_same_instance(self):
        """Test that get_session_tracker returns singleton."""
        tracker1 = get_session_tracker()
        tracker2 = get_session_tracker()
        assert tracker1 is tracker2

    def test_returns_session_tracker_instance(self):
        """Test that it returns a SessionTracker instance."""
        tracker = get_session_tracker()
        assert isinstance(tracker, SessionTracker)
