"""Unit tests for tmux_dispatcher module."""

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from forge_harness.openclaw.tmux_dispatcher import (
    DispatchResult,
    TmuxSession,
    _run_tmux,
    capture_tail,
    dispatch_command,
    list_sessions,
    list_windows,
    resolve_session,
    resolve_target,
    send_keys,
)

# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_tmux_session_frozen(self):
        s = TmuxSession(name="forge", created_at="2026-01-01T00:00:00+00:00", windows=3, attached=True)
        assert s.name == "forge"
        assert s.windows == 3
        assert s.attached is True
        with pytest.raises(AttributeError):
            s.name = "other"

    def test_dispatch_result_defaults(self):
        r = DispatchResult(session="s", command="c", sent=True)
        assert r.error is None

    def test_dispatch_result_with_error(self):
        r = DispatchResult(session="s", command="c", sent=False, error="fail")
        assert r.error == "fail"


# ---------------------------------------------------------------------------
# _run_tmux
# ---------------------------------------------------------------------------

class TestRunTmux:
    @patch("forge_harness.openclaw.tmux_dispatcher.subprocess.run")
    def test_calls_subprocess(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        result = _run_tmux(["list-sessions"])
        mock_run.assert_called_once_with(
            ["tmux", "list-sessions"],
            check=False,
            capture_output=True,
            text=True,
        )


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------

class TestListSessions:
    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_parses_sessions(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="forge|1706745600|3|1\ndev|1706745700|1|0\n",
            stderr="",
        )
        sessions = list_sessions()
        assert len(sessions) == 2
        assert sessions[0].name == "forge"
        assert sessions[0].windows == 3
        assert sessions[0].attached is True
        assert sessions[1].name == "dev"
        assert sessions[1].attached is False

    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="no server"
        )
        assert list_sessions() == []

    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_skips_blank_lines(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="forge|1706745600|3|1\n\n\n",
            stderr="",
        )
        sessions = list_sessions()
        assert len(sessions) == 1

    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_skips_malformed_lines(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="forge|1706745600|3|1\nbad_line\ntoo|few|parts\n",
            stderr="",
        )
        sessions = list_sessions()
        assert len(sessions) == 1

    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_handles_invalid_timestamp(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="forge|notanumber|2|0\n",
            stderr="",
        )
        sessions = list_sessions()
        assert len(sessions) == 1
        # Should fall back to now() — just check it's a valid ISO string
        assert "T" in sessions[0].created_at

    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_handles_non_digit_windows(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="forge|1706745600|abc|0\n",
            stderr="",
        )
        sessions = list_sessions()
        assert sessions[0].windows == 0


# ---------------------------------------------------------------------------
# resolve_session
# ---------------------------------------------------------------------------

class TestResolveSession:
    @patch("forge_harness.openclaw.tmux_dispatcher.list_sessions")
    def test_finds_existing(self, mock_ls):
        mock_ls.return_value = [
            TmuxSession(name="forge", created_at="", windows=1, attached=False),
        ]
        assert resolve_session("forge") == "forge"

    @patch("forge_harness.openclaw.tmux_dispatcher.list_sessions")
    def test_returns_none_for_missing(self, mock_ls):
        mock_ls.return_value = []
        assert resolve_session("nope") is None

    @patch("forge_harness.openclaw.tmux_dispatcher.list_sessions")
    def test_strips_whitespace(self, mock_ls):
        mock_ls.return_value = [
            TmuxSession(name="forge", created_at="", windows=1, attached=False),
        ]
        assert resolve_session("  forge  ") == "forge"


# ---------------------------------------------------------------------------
# list_windows
# ---------------------------------------------------------------------------

class TestListWindows:
    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_parses_windows(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="glm\nkimi\nminimax\n", stderr=""
        )
        windows = list_windows("forge")
        assert windows == ["glm", "kimi", "minimax"]

    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error"
        )
        assert list_windows("nope") == []

    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_skips_blank_lines(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="glm\n\n\n", stderr=""
        )
        assert list_windows("forge") == ["glm"]


# ---------------------------------------------------------------------------
# resolve_target
# ---------------------------------------------------------------------------

class TestResolveTarget:
    @patch("forge_harness.openclaw.tmux_dispatcher.list_windows")
    @patch("forge_harness.openclaw.tmux_dispatcher.list_sessions")
    @patch("forge_harness.openclaw.tmux_dispatcher.resolve_session")
    def test_session_colon_window(self, mock_resolve, mock_ls, mock_lw):
        mock_resolve.return_value = "forge"
        result = resolve_target("forge:glm")
        assert result == "forge:glm"

    @patch("forge_harness.openclaw.tmux_dispatcher.resolve_session")
    def test_session_colon_window_no_session(self, mock_resolve):
        mock_resolve.return_value = None
        assert resolve_target("bad:glm") is None

    @patch("forge_harness.openclaw.tmux_dispatcher.list_windows")
    @patch("forge_harness.openclaw.tmux_dispatcher.list_sessions")
    @patch("forge_harness.openclaw.tmux_dispatcher.resolve_session")
    def test_exact_session_match(self, mock_resolve, mock_ls, mock_lw):
        mock_resolve.return_value = "forge"
        result = resolve_target("forge")
        assert result == "forge"

    @patch("forge_harness.openclaw.tmux_dispatcher.list_windows")
    @patch("forge_harness.openclaw.tmux_dispatcher.list_sessions")
    @patch("forge_harness.openclaw.tmux_dispatcher.resolve_session")
    def test_window_lookup(self, mock_resolve, mock_ls, mock_lw):
        mock_resolve.return_value = None
        mock_ls.return_value = [
            TmuxSession(name="forge", created_at="", windows=3, attached=True),
        ]
        mock_lw.return_value = ["glm", "kimi", "minimax"]
        result = resolve_target("glm")
        assert result == "forge:glm"

    @patch("forge_harness.openclaw.tmux_dispatcher.list_windows")
    @patch("forge_harness.openclaw.tmux_dispatcher.list_sessions")
    @patch("forge_harness.openclaw.tmux_dispatcher.resolve_session")
    def test_window_not_found(self, mock_resolve, mock_ls, mock_lw):
        mock_resolve.return_value = None
        mock_ls.return_value = [
            TmuxSession(name="forge", created_at="", windows=1, attached=True),
        ]
        mock_lw.return_value = ["other"]
        assert resolve_target("glm") is None

    @patch("forge_harness.openclaw.tmux_dispatcher.list_sessions")
    @patch("forge_harness.openclaw.tmux_dispatcher.resolve_session")
    def test_strips_whitespace(self, mock_resolve, mock_ls):
        mock_resolve.return_value = "forge"
        result = resolve_target("  forge  ")
        assert result == "forge"


# ---------------------------------------------------------------------------
# send_keys
# ---------------------------------------------------------------------------

class TestSendKeys:
    def test_empty_session_returns_error(self):
        result = send_keys("", "cmd")
        assert result.sent is False
        assert result.error == "missing session"

    @patch("forge_harness.openclaw.tmux_dispatcher.time.sleep")
    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_sends_literal_with_enter(self, mock_run, mock_sleep):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        result = send_keys("forge:glm", "hello")
        assert result.sent is True
        assert result.session == "forge:glm"
        assert result.command == "hello"
        # First call: send-keys -t forge:glm -l hello
        # Second call: send-keys -t forge:glm Enter
        assert mock_run.call_count == 2
        mock_sleep.assert_called_once_with(0.15)

    @patch("forge_harness.openclaw.tmux_dispatcher.time.sleep")
    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_sends_without_enter(self, mock_run, mock_sleep):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        result = send_keys("s", "cmd", enter=False)
        assert result.sent is True
        assert mock_run.call_count == 1
        mock_sleep.assert_not_called()

    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_sends_without_literal(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        send_keys("s", "cmd", literal=False, enter=False)
        args_used = mock_run.call_args[0][0]
        assert "-l" not in args_used

    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_returns_error_on_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="session not found"
        )
        result = send_keys("bad", "cmd")
        assert result.sent is False
        assert "session not found" in result.error

    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_returns_generic_error_when_stderr_empty(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=""
        )
        result = send_keys("bad", "cmd")
        assert result.sent is False
        assert result.error == "tmux send-keys failed"


# ---------------------------------------------------------------------------
# dispatch_command
# ---------------------------------------------------------------------------

class TestDispatchCommand:
    @patch("forge_harness.openclaw.tmux_dispatcher.send_keys")
    def test_dispatches_simple_command(self, mock_sk):
        mock_sk.return_value = DispatchResult(session="s", command="ls", sent=True)
        result = dispatch_command("s", "ls")
        mock_sk.assert_called_once_with("s", "ls", enter=True)
        assert result.sent is True

    @patch("forge_harness.openclaw.tmux_dispatcher.send_keys")
    def test_quotes_multiline_command(self, mock_sk):
        mock_sk.return_value = DispatchResult(session="s", command="quoted", sent=True)
        dispatch_command("s", "line1\nline2")
        args = mock_sk.call_args[0]
        # shlex.quote wraps in single quotes
        assert "'" in args[1]


# ---------------------------------------------------------------------------
# capture_tail
# ---------------------------------------------------------------------------

class TestCaptureTail:
    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_returns_output(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="line1\nline2\n", stderr=""
        )
        output = capture_tail("forge:glm", lines=10)
        assert "line1" in output
        mock_run.assert_called_once_with(
            ["capture-pane", "-t", "forge:glm", "-p", "-S", "-10"]
        )

    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error"
        )
        assert capture_tail("bad") == ""

    @patch("forge_harness.openclaw.tmux_dispatcher._run_tmux")
    def test_default_lines(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="out", stderr=""
        )
        capture_tail("s")
        mock_run.assert_called_once_with(
            ["capture-pane", "-t", "s", "-p", "-S", "-20"]
        )
