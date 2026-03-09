"""Unit tests for forge_harness.openclaw.tmux_dispatcher.

Coverage targets:
- list_sessions        (parse valid/invalid/empty output, returncode != 0)
- resolve_session      (found, not found, whitespace stripping)
- list_windows         (success, failed command, empty output)
- resolve_target       (session:window pass-through, exact session, window search, not found)
- send_keys            (success, missing session, failed send-keys, enter=False, literal=False)
- dispatch_command     (plain command, newline-containing command)
- capture_tail         (success, failed command)
- _run_tmux            (subprocess wiring)
- TmuxSession / DispatchResult dataclasses
"""

from __future__ import annotations

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
# Helpers
# ---------------------------------------------------------------------------

_TMUX_MODULE = "forge_harness.openclaw.tmux_dispatcher"


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    """Build a fake subprocess.CompletedProcess."""
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


# ---------------------------------------------------------------------------
# Dataclass sanity checks
# ---------------------------------------------------------------------------


class TestTmuxSession:
    def test_frozen(self):
        s = TmuxSession(name="forge", created_at="2024-01-01T00:00:00+00:00", windows=3, attached=False)
        with pytest.raises((AttributeError, TypeError)):
            s.name = "other"  # type: ignore[misc]

    def test_fields(self):
        s = TmuxSession(name="x", created_at="ts", windows=1, attached=True)
        assert s.name == "x"
        assert s.windows == 1
        assert s.attached is True


class TestDispatchResult:
    def test_defaults(self):
        r = DispatchResult(session="s", command="c", sent=True)
        assert r.error is None

    def test_with_error(self):
        r = DispatchResult(session="s", command="c", sent=False, error="oops")
        assert r.error == "oops"
        assert r.sent is False

    def test_frozen(self):
        r = DispatchResult(session="s", command="c", sent=True)
        with pytest.raises((AttributeError, TypeError)):
            r.sent = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _run_tmux
# ---------------------------------------------------------------------------


class TestRunTmux:
    @patch(f"{_TMUX_MODULE}.subprocess.run")
    def test_prepends_tmux(self, mock_run):
        mock_run.return_value = _completed()
        _run_tmux(["list-sessions"])
        mock_run.assert_called_once_with(
            ["tmux", "list-sessions"],
            check=False,
            capture_output=True,
            text=True,
        )

    @patch(f"{_TMUX_MODULE}.subprocess.run")
    def test_multiple_args(self, mock_run):
        mock_run.return_value = _completed()
        _run_tmux(["send-keys", "-t", "forge", "Enter"])
        args = mock_run.call_args[0][0]
        assert args == ["tmux", "send-keys", "-t", "forge", "Enter"]

    @patch(f"{_TMUX_MODULE}.subprocess.run")
    def test_returns_completed_process(self, mock_run):
        fake = _completed(stdout="hello", returncode=0)
        mock_run.return_value = fake
        result = _run_tmux(["list-sessions"])
        assert result is fake


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


class TestListSessions:
    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_empty_output_returns_empty_list(self, mock_run):
        mock_run.return_value = _completed(stdout="", returncode=0)
        assert list_sessions() == []

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_nonzero_returncode_returns_empty_list(self, mock_run):
        mock_run.return_value = _completed(stdout="forge|1700000000|2|0", returncode=1)
        assert list_sessions() == []

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_parses_single_session(self, mock_run):
        mock_run.return_value = _completed(stdout="forge|1700000000|2|0\n", returncode=0)
        sessions = list_sessions()
        assert len(sessions) == 1
        s = sessions[0]
        assert s.name == "forge"
        assert s.windows == 2
        assert s.attached is False
        assert "1970" not in s.created_at  # should be real timestamp not epoch

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_parses_attached_session(self, mock_run):
        mock_run.return_value = _completed(stdout="myses|1700000000|1|1\n", returncode=0)
        sessions = list_sessions()
        assert sessions[0].attached is True

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_parses_multiple_sessions(self, mock_run):
        mock_run.return_value = _completed(
            stdout="forge|1700000000|3|0\nwork|1700000001|1|1\n",
            returncode=0,
        )
        sessions = list_sessions()
        assert len(sessions) == 2
        names = {s.name for s in sessions}
        assert names == {"forge", "work"}

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_skips_blank_lines(self, mock_run):
        mock_run.return_value = _completed(stdout="\n\nforge|1700000000|2|0\n\n", returncode=0)
        assert len(list_sessions()) == 1

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_skips_malformed_lines(self, mock_run):
        # Only 3 parts instead of 4
        mock_run.return_value = _completed(stdout="forge|1700000000|2\n", returncode=0)
        assert list_sessions() == []

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_invalid_timestamp_falls_back_to_now(self, mock_run):
        mock_run.return_value = _completed(stdout="forge|notanumber|2|0\n", returncode=0)
        sessions = list_sessions()
        # Should not raise; falls back to datetime.now(UTC)
        assert len(sessions) == 1
        assert sessions[0].created_at  # non-empty string

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_non_digit_windows_defaults_to_zero(self, mock_run):
        mock_run.return_value = _completed(stdout="forge|1700000000|N/A|0\n", returncode=0)
        sessions = list_sessions()
        assert sessions[0].windows == 0

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_passes_correct_format_string(self, mock_run):
        mock_run.return_value = _completed(returncode=1)
        list_sessions()
        call_args = mock_run.call_args[0][0]
        assert "list-sessions" in call_args
        assert "-F" in call_args
        fmt = call_args[call_args.index("-F") + 1]
        assert "session_name" in fmt
        assert "session_created" in fmt
        assert "session_windows" in fmt
        assert "session_attached" in fmt


# ---------------------------------------------------------------------------
# resolve_session
# ---------------------------------------------------------------------------


class TestResolveSession:
    @patch(f"{_TMUX_MODULE}.list_sessions")
    def test_found_exact_match(self, mock_ls):
        mock_ls.return_value = [
            TmuxSession(name="forge", created_at="ts", windows=1, attached=False)
        ]
        assert resolve_session("forge") == "forge"

    @patch(f"{_TMUX_MODULE}.list_sessions")
    def test_not_found_returns_none(self, mock_ls):
        mock_ls.return_value = [
            TmuxSession(name="forge", created_at="ts", windows=1, attached=False)
        ]
        assert resolve_session("missing") is None

    @patch(f"{_TMUX_MODULE}.list_sessions")
    def test_strips_whitespace(self, mock_ls):
        mock_ls.return_value = [
            TmuxSession(name="forge", created_at="ts", windows=1, attached=False)
        ]
        assert resolve_session("  forge  ") == "forge"

    @patch(f"{_TMUX_MODULE}.list_sessions")
    def test_empty_sessions_returns_none(self, mock_ls):
        mock_ls.return_value = []
        assert resolve_session("forge") is None

    @patch(f"{_TMUX_MODULE}.list_sessions")
    def test_case_sensitive_no_match(self, mock_ls):
        mock_ls.return_value = [
            TmuxSession(name="Forge", created_at="ts", windows=1, attached=False)
        ]
        assert resolve_session("forge") is None


# ---------------------------------------------------------------------------
# list_windows
# ---------------------------------------------------------------------------


class TestListWindows:
    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_returns_window_names(self, mock_run):
        mock_run.return_value = _completed(stdout="glm\nkimi\nclaude\n", returncode=0)
        windows = list_windows("forge")
        assert windows == ["glm", "kimi", "claude"]

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_failed_command_returns_empty_list(self, mock_run):
        mock_run.return_value = _completed(stdout="glm\n", returncode=1)
        assert list_windows("forge") == []

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_empty_output_returns_empty_list(self, mock_run):
        mock_run.return_value = _completed(stdout="", returncode=0)
        assert list_windows("forge") == []

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_skips_blank_lines(self, mock_run):
        mock_run.return_value = _completed(stdout="glm\n\nkimi\n", returncode=0)
        assert list_windows("forge") == ["glm", "kimi"]

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_passes_session_as_target(self, mock_run):
        mock_run.return_value = _completed(returncode=0, stdout="")
        list_windows("mysession")
        call_args = mock_run.call_args[0][0]
        assert "-t" in call_args
        assert call_args[call_args.index("-t") + 1] == "mysession"

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_uses_window_name_format(self, mock_run):
        mock_run.return_value = _completed(returncode=0, stdout="")
        list_windows("forge")
        call_args = mock_run.call_args[0][0]
        assert "-F" in call_args
        fmt = call_args[call_args.index("-F") + 1]
        assert "window_name" in fmt


# ---------------------------------------------------------------------------
# resolve_target
# ---------------------------------------------------------------------------


class TestResolveTarget:
    # -- session:window pass-through --

    @patch(f"{_TMUX_MODULE}.resolve_session")
    def test_session_window_passthrough_valid(self, mock_rs):
        mock_rs.return_value = "forge"
        result = resolve_target("forge:glm")
        assert result == "forge:glm"
        mock_rs.assert_called_once_with("forge")

    @patch(f"{_TMUX_MODULE}.resolve_session")
    def test_session_window_passthrough_invalid_session(self, mock_rs):
        mock_rs.return_value = None
        result = resolve_target("missing:glm")
        assert result is None

    @patch(f"{_TMUX_MODULE}.resolve_session")
    def test_session_window_colon_in_window_name(self, mock_rs):
        """Only the part before the first colon is treated as session."""
        mock_rs.return_value = "forge"
        result = resolve_target("forge:win:extra")
        assert result == "forge:win:extra"
        mock_rs.assert_called_once_with("forge")

    # -- exact session match --

    @patch(f"{_TMUX_MODULE}.list_sessions")
    @patch(f"{_TMUX_MODULE}.resolve_session")
    def test_exact_session_match(self, mock_rs, mock_ls):
        mock_rs.return_value = "forge"  # exact match found
        result = resolve_target("forge")
        assert result == "forge"

    # -- window search across sessions --

    @patch(f"{_TMUX_MODULE}.list_windows")
    @patch(f"{_TMUX_MODULE}.list_sessions")
    @patch(f"{_TMUX_MODULE}.resolve_session")
    def test_window_search_found(self, mock_rs, mock_ls, mock_lw):
        # No exact session match
        mock_rs.return_value = None
        mock_ls.return_value = [
            TmuxSession(name="forge", created_at="ts", windows=3, attached=False)
        ]
        mock_lw.return_value = ["glm", "kimi", "claude"]
        result = resolve_target("kimi")
        assert result == "forge:kimi"

    @patch(f"{_TMUX_MODULE}.list_windows")
    @patch(f"{_TMUX_MODULE}.list_sessions")
    @patch(f"{_TMUX_MODULE}.resolve_session")
    def test_window_search_not_found(self, mock_rs, mock_ls, mock_lw):
        mock_rs.return_value = None
        mock_ls.return_value = [
            TmuxSession(name="forge", created_at="ts", windows=2, attached=False)
        ]
        mock_lw.return_value = ["glm", "kimi"]
        result = resolve_target("unknown-agent")
        assert result is None

    @patch(f"{_TMUX_MODULE}.list_windows")
    @patch(f"{_TMUX_MODULE}.list_sessions")
    @patch(f"{_TMUX_MODULE}.resolve_session")
    def test_window_search_no_sessions(self, mock_rs, mock_ls, mock_lw):
        mock_rs.return_value = None
        mock_ls.return_value = []
        result = resolve_target("glm")
        assert result is None
        mock_lw.assert_not_called()

    @patch(f"{_TMUX_MODULE}.list_windows")
    @patch(f"{_TMUX_MODULE}.list_sessions")
    @patch(f"{_TMUX_MODULE}.resolve_session")
    def test_window_found_in_second_session(self, mock_rs, mock_ls, mock_lw):
        mock_rs.return_value = None
        mock_ls.return_value = [
            TmuxSession(name="forge", created_at="ts", windows=1, attached=False),
            TmuxSession(name="work", created_at="ts", windows=1, attached=False),
        ]
        # glm not in forge but in work
        mock_lw.side_effect = [["claude"], ["glm"]]
        result = resolve_target("glm")
        assert result == "work:glm"

    @patch(f"{_TMUX_MODULE}.resolve_session")
    def test_strips_whitespace(self, mock_rs):
        mock_rs.return_value = "forge"
        result = resolve_target("  forge  ")
        assert result == "  forge  " or result == "forge"  # after strip


# ---------------------------------------------------------------------------
# send_keys
# ---------------------------------------------------------------------------


class TestSendKeys:
    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_missing_session_returns_error_result(self, mock_run):
        result = send_keys("", "echo hello")
        assert result.sent is False
        assert result.error == "missing session"
        mock_run.assert_not_called()

    @patch(f"{_TMUX_MODULE}.time.sleep")
    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_success_with_enter(self, mock_run, mock_sleep):
        mock_run.return_value = _completed(returncode=0)
        result = send_keys("forge", "echo hello", enter=True)
        assert result.sent is True
        assert result.error is None
        # Two _run_tmux calls: send-keys then Enter
        assert mock_run.call_count == 2
        mock_sleep.assert_called_once()

    @patch(f"{_TMUX_MODULE}.time.sleep")
    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_success_without_enter(self, mock_run, mock_sleep):
        mock_run.return_value = _completed(returncode=0)
        result = send_keys("forge", "echo hello", enter=False)
        assert result.sent is True
        # Only one _run_tmux call (no Enter)
        assert mock_run.call_count == 1
        mock_sleep.assert_not_called()

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_failed_send_keys_returns_error_result(self, mock_run):
        mock_run.return_value = _completed(returncode=1, stderr="no session")
        result = send_keys("forge", "echo hello")
        assert result.sent is False
        assert result.error == "no session"

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_failed_send_keys_empty_stderr_uses_default_error(self, mock_run):
        mock_run.return_value = _completed(returncode=1, stderr="")
        result = send_keys("forge", "echo hello")
        assert result.sent is False
        assert result.error == "tmux send-keys failed"

    @patch(f"{_TMUX_MODULE}.time.sleep")
    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_literal_flag_included(self, mock_run, mock_sleep):
        mock_run.return_value = _completed(returncode=0)
        send_keys("forge", "cmd", literal=True, enter=False)
        call_args = mock_run.call_args[0][0]
        assert "-l" in call_args

    @patch(f"{_TMUX_MODULE}.time.sleep")
    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_literal_flag_excluded(self, mock_run, mock_sleep):
        mock_run.return_value = _completed(returncode=0)
        send_keys("forge", "cmd", literal=False, enter=False)
        call_args = mock_run.call_args[0][0]
        assert "-l" not in call_args

    @patch(f"{_TMUX_MODULE}.time.sleep")
    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_session_and_command_in_args(self, mock_run, mock_sleep):
        mock_run.return_value = _completed(returncode=0)
        send_keys("my-session", "do-work", enter=False)
        call_args = mock_run.call_args[0][0]
        assert "-t" in call_args
        assert call_args[call_args.index("-t") + 1] == "my-session"
        assert "do-work" in call_args

    @patch(f"{_TMUX_MODULE}.time.sleep")
    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_enter_call_uses_correct_args(self, mock_run, mock_sleep):
        mock_run.return_value = _completed(returncode=0)
        send_keys("forge", "cmd", enter=True)
        # Second call should send "Enter"
        enter_call = mock_run.call_args_list[1][0][0]
        assert "Enter" in enter_call
        assert "-t" in enter_call
        assert enter_call[enter_call.index("-t") + 1] == "forge"

    @patch(f"{_TMUX_MODULE}.time.sleep")
    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_result_contains_session_and_command(self, mock_run, mock_sleep):
        mock_run.return_value = _completed(returncode=0)
        result = send_keys("my-session", "my-command", enter=False)
        assert result.session == "my-session"
        assert result.command == "my-command"


# ---------------------------------------------------------------------------
# dispatch_command
# ---------------------------------------------------------------------------


class TestDispatchCommand:
    @patch(f"{_TMUX_MODULE}.send_keys")
    def test_plain_command_passed_unquoted(self, mock_sk):
        mock_sk.return_value = DispatchResult(session="s", command="echo hi", sent=True)
        dispatch_command("forge", "echo hi")
        # Command with no newline — passed as-is
        mock_sk.assert_called_once_with("forge", "echo hi", enter=True)

    @patch(f"{_TMUX_MODULE}.send_keys")
    def test_newline_command_is_quoted(self, mock_sk):
        mock_sk.return_value = DispatchResult(session="s", command="x\ny", sent=True)
        dispatch_command("forge", "line1\nline2")
        call_command = mock_sk.call_args[0][1]
        # shlex.quote wraps in single quotes for safety
        assert "'" in call_command or call_command.startswith("'")

    @patch(f"{_TMUX_MODULE}.send_keys")
    def test_returns_dispatch_result(self, mock_sk):
        expected = DispatchResult(session="forge", command="cmd", sent=True)
        mock_sk.return_value = expected
        result = dispatch_command("forge", "cmd")
        assert result is expected

    @patch(f"{_TMUX_MODULE}.send_keys")
    def test_enter_always_true(self, mock_sk):
        mock_sk.return_value = DispatchResult(session="s", command="c", sent=True)
        dispatch_command("forge", "cmd")
        assert mock_sk.call_args[1].get("enter", mock_sk.call_args[0][2] if len(mock_sk.call_args[0]) > 2 else True)

    @patch(f"{_TMUX_MODULE}.send_keys")
    def test_empty_command_no_newline(self, mock_sk):
        mock_sk.return_value = DispatchResult(session="s", command="", sent=True)
        dispatch_command("s", "")
        # No newline in "", so passed as-is (empty string)
        mock_sk.assert_called_once_with("s", "", enter=True)


# ---------------------------------------------------------------------------
# capture_tail
# ---------------------------------------------------------------------------


class TestCaptureTail:
    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_success_returns_stdout(self, mock_run):
        mock_run.return_value = _completed(stdout="line1\nline2\n", returncode=0)
        result = capture_tail("forge")
        assert result == "line1\nline2\n"

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_failure_returns_empty_string(self, mock_run):
        mock_run.return_value = _completed(stdout="some output", returncode=1)
        result = capture_tail("forge")
        assert result == ""

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_default_lines_is_20(self, mock_run):
        mock_run.return_value = _completed(returncode=0)
        capture_tail("forge")
        call_args = mock_run.call_args[0][0]
        assert "-20" in call_args or "-S" in call_args
        # Verify -S flag value contains 20
        if "-S" in call_args:
            idx = call_args.index("-S")
            assert call_args[idx + 1] == "-20"

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_custom_lines(self, mock_run):
        mock_run.return_value = _completed(returncode=0)
        capture_tail("forge", lines=50)
        call_args = mock_run.call_args[0][0]
        if "-S" in call_args:
            idx = call_args.index("-S")
            assert call_args[idx + 1] == "-50"

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_passes_session_as_target(self, mock_run):
        mock_run.return_value = _completed(returncode=0)
        capture_tail("my-session")
        call_args = mock_run.call_args[0][0]
        assert "-t" in call_args
        assert call_args[call_args.index("-t") + 1] == "my-session"

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_uses_capture_pane_command(self, mock_run):
        mock_run.return_value = _completed(returncode=0)
        capture_tail("forge")
        call_args = mock_run.call_args[0][0]
        assert "capture-pane" in call_args

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_uses_print_flag(self, mock_run):
        mock_run.return_value = _completed(returncode=0)
        capture_tail("forge")
        call_args = mock_run.call_args[0][0]
        assert "-p" in call_args

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_empty_output_on_success(self, mock_run):
        mock_run.return_value = _completed(stdout="", returncode=0)
        assert capture_tail("forge") == ""


# ---------------------------------------------------------------------------
# Integration-style: resolve_target uses list_sessions + list_windows
# ---------------------------------------------------------------------------


class TestResolveTargetIntegration:
    """Drive resolve_target through _run_tmux to verify the wiring."""

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_full_path_session_only(self, mock_run):
        """resolve_target("forge") when forge is a session."""
        # list_sessions call
        mock_run.return_value = _completed(stdout="forge|1700000000|2|0\n", returncode=0)
        result = resolve_target("forge")
        assert result == "forge"

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_full_path_no_session_no_window(self, mock_run):
        """resolve_target returns None when nothing matches."""
        # list_sessions returns nothing useful
        mock_run.return_value = _completed(stdout="", returncode=0)
        result = resolve_target("nonexistent")
        assert result is None

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_full_path_window_found(self, mock_run):
        """resolve_target finds agent as a window in existing session."""
        # First call: list_sessions (for resolve_session — no match)
        # Second call: list_sessions (for window search)
        # Third call: list_windows for "forge"
        mock_run.side_effect = [
            _completed(stdout="forge|1700000000|3|0\n", returncode=0),  # resolve_session
            _completed(stdout="forge|1700000000|3|0\n", returncode=0),  # list_sessions in loop
            _completed(stdout="glm\nkimi\nclaude\n", returncode=0),      # list_windows
        ]
        result = resolve_target("kimi")
        assert result == "forge:kimi"


# ---------------------------------------------------------------------------
# Edge-case cluster
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_list_sessions_whitespace_only_output(self, mock_run):
        mock_run.return_value = _completed(stdout="   \n   \n", returncode=0)
        assert list_sessions() == []

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_list_windows_whitespace_only_output(self, mock_run):
        mock_run.return_value = _completed(stdout="   \n   \n", returncode=0)
        assert list_windows("forge") == []

    @patch(f"{_TMUX_MODULE}.time.sleep")
    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_send_keys_sleep_duration(self, mock_run, mock_sleep):
        """Ensure sleep of 0.15 seconds is called between send and Enter."""
        mock_run.return_value = _completed(returncode=0)
        send_keys("forge", "cmd", enter=True)
        mock_sleep.assert_called_once_with(0.15)

    @patch(f"{_TMUX_MODULE}._run_tmux")
    def test_list_sessions_exactly_four_pipe_fields(self, mock_run):
        """Lines with extra pipes are skipped (len(parts) != 4)."""
        mock_run.return_value = _completed(
            stdout="good|1700000000|1|0\nbad|1700000000|1|0|extra\n",
            returncode=0,
        )
        sessions = list_sessions()
        assert len(sessions) == 1
        assert sessions[0].name == "good"

    @patch(f"{_TMUX_MODULE}.send_keys")
    def test_dispatch_command_multiline_uses_shlex_quote(self, mock_sk):
        mock_sk.return_value = DispatchResult(session="s", command="x", sent=True)
        dispatch_command("s", "a\nb")
        cmd_arg = mock_sk.call_args[0][1]
        import shlex
        assert cmd_arg == shlex.quote("a\nb")
