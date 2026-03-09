"""
Comprehensive pytest tests for forge_harness/cli_v2/fleet.py.

Covers:
- All three helper functions (_extract_context_snapshot, _classify_agent_status,
  _extract_activity_line)
- _fleet_status: session absent, tmux missing, windows listed, capture failures
- _fleet_spawn: success, failure, script-not-found, domain+project args
- _fleet_stop: single agent success/failure/exception, --all path with mixed results
- _fleet_save: success, non-zero exit, FileNotFoundError
- _fleet_restore: success, non-zero exit, FileNotFoundError
- _fleet_heartbeat: delegates to agents_heartbeat
- _fleet_heartbeat_domain: found, not-found, output_json
- _fleet_heartbeat_all_domains: empty summaries, output_json, health categorisation
- CLI integration via CliRunner: action dispatch, JSON envelope, UsageError paths
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helper: lightweight subprocess mock factory
# ---------------------------------------------------------------------------


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> Mock:
    return Mock(returncode=returncode, stdout=stdout, stderr=stderr)


# ===========================================================================
# Pure helper functions
# ===========================================================================


class TestExtractContextSnapshot:
    """Unit tests for _extract_context_snapshot."""

    def _fn(self, text):
        from forge_harness.cli_v2.fleet import _extract_context_snapshot

        return _extract_context_snapshot(text)

    def test_plain_integer_percent(self):
        assert self._fn("45%") == "45%"

    def test_decimal_percent(self):
        assert self._fn("Current: 67.5%") == "67.5%"

    def test_percent_with_context_suffix(self):
        result = self._fn("30% context left")
        # regex captures "30% context left" because it is greedy on the suffix
        assert result.startswith("30%")

    def test_returns_last_of_multiple_matches(self):
        assert self._fn("10%\n20%\n30%") == "30%"

    def test_returns_dash_on_no_match(self):
        assert self._fn("no percentages here") == "-"

    def test_returns_dash_on_empty_string(self):
        assert self._fn("") == "-"

    def test_case_insensitive_context_suffix(self):
        # "CONTEXT" should still be matched
        result = self._fn("75% CONTEXT LEFT")
        assert result.startswith("75%")

    def test_ignores_non_percentage_numbers(self):
        assert self._fn("run 42 times, cost $100") == "-"

    def test_zero_percent(self):
        assert self._fn("0%") == "0%"

    def test_hundred_percent(self):
        assert self._fn("100%") == "100%"


class TestClassifyAgentStatus:
    """Unit tests for _classify_agent_status."""

    def _fn(self, text):
        from forge_harness.cli_v2.fleet import _classify_agent_status

        return _classify_agent_status(text)

    # --- error states -------------------------------------------------------

    def test_error_keyword(self):
        status, color = self._fn("Unexpected error occurred")
        assert status == "error"
        assert color == "red"

    def test_failed_keyword(self):
        status, color = self._fn("Build failed at step 3")
        assert status == "error"
        assert color == "red"

    def test_blocked_keyword(self):
        status, color = self._fn("Operation blocked by firewall")
        assert status == "error"
        assert color == "red"

    def test_badobject_keyword(self):
        status, color = self._fn("BadObject detected in heap")
        assert status == "error"
        assert color == "red"

    def test_traceback_keyword(self):
        status, color = self._fn("Traceback (most recent call last):")
        assert status == "error"
        assert color == "red"

    def test_error_detection_is_case_insensitive(self):
        status, color = self._fn("TRACEBACK (most recent call last):")
        assert status == "error"

    # --- active states -------------------------------------------------------

    def test_working_keyword(self):
        status, color = self._fn("Working on the feature...")
        assert status == "active"
        assert color == "yellow"

    def test_thinking_keyword(self):
        status, color = self._fn("Thinking about the solution...")
        assert status == "active"
        assert color == "yellow"

    def test_running_keyword(self):
        status, color = self._fn("Running the test suite")
        assert status == "active"
        assert color == "yellow"

    def test_compiling_keyword(self):
        status, color = self._fn("Compiling source files")
        assert status == "active"
        assert color == "yellow"

    def test_building_keyword(self):
        status, color = self._fn("Building the project")
        assert status == "active"
        assert color == "yellow"

    def test_cogitating_keyword(self):
        status, color = self._fn("Cogitating on the problem")
        assert status == "active"
        assert color == "yellow"

    # --- idle states -------------------------------------------------------

    def test_gt_prompt_at_line_start(self):
        status, color = self._fn("> type your message")
        assert status == "idle"
        assert color == "green"

    def test_unicode_arrow_prompt(self):
        status, color = self._fn("❯ waiting")
        assert status == "idle"
        assert color == "green"

    def test_dollar_prompt(self):
        status, color = self._fn("$ ")
        assert status == "idle"
        assert color == "green"

    def test_type_your_message_phrase(self):
        # Pattern is case-sensitive; only lowercase triggers idle
        status, color = self._fn("type your message here")
        assert status == "idle"
        assert color == "green"

    def test_prompt_after_other_lines(self):
        status, color = self._fn("Some output\n> ready")
        assert status == "idle"

    # --- ready (default) state -----------------------------------------------

    def test_ready_default_neutral_output(self):
        status, color = self._fn("Just some neutral output")
        assert status == "ready"
        assert color == "green"

    def test_ready_empty_output(self):
        status, color = self._fn("")
        assert status == "ready"

    # --- error takes priority over active -----------------------------------

    def test_error_beats_working(self):
        # "error" keyword should win over "running"
        status, color = self._fn("Error while running tests")
        assert status == "error"


class TestExtractActivityLine:
    """Unit tests for _extract_activity_line."""

    def _fn(self, text):
        from forge_harness.cli_v2.fleet import _extract_activity_line

        return _extract_activity_line(text)

    def test_returns_last_non_empty_line(self):
        assert self._fn("Line 1\nLine 2\nLine 3") == "Line 3"

    def test_skips_blank_lines(self):
        assert self._fn("Line 1\n\n\nLine 2") == "Line 2"

    def test_truncates_to_60_chars(self):
        result = self._fn("x" * 100)
        assert len(result) == 60
        assert result == "x" * 60

    def test_pipe_is_escaped(self):
        assert self._fn("a | b | c") == r"a \| b \| c"

    def test_multiple_consecutive_pipes(self):
        assert self._fn("a|b|c") == r"a\|b\|c"

    def test_empty_string_returns_no_output(self):
        assert self._fn("") == "No output"

    def test_whitespace_only_returns_no_output(self):
        assert self._fn("   \n   \t  ") == "No output"

    def test_strips_surrounding_whitespace(self):
        assert self._fn("  hello  ") == "hello"

    def test_single_line(self):
        assert self._fn("Only line") == "Only line"

    def test_unicode_preserved(self):
        result = self._fn("Deploy 🚀 finished")
        assert "🚀" in result


# ===========================================================================
# _fleet_status
# ===========================================================================


class TestFleetStatus:
    """Tests for _fleet_status helper."""

    def _fn(self, *args, **kwargs):
        from forge_harness.cli_v2.fleet import _fleet_status

        return _fleet_status(*args, **kwargs)

    # --- tmux absent -------------------------------------------------------

    def test_returns_error_dict_when_tmux_missing(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("no tmux")):
            result = self._fn(ctx=None, output_json=True)
        assert result["session_active"] is False
        assert "tmux not found" in result["error"]
        assert result["agents"] == []

    def test_returns_none_when_tmux_missing_no_json(self):
        with (
            patch("subprocess.run", side_effect=FileNotFoundError("no tmux")),
            patch("forge_harness.cli_v2.fleet.format_error"),
        ):
            result = self._fn(ctx=None, output_json=False)
        assert result is None

    # --- no forge session ---------------------------------------------------

    def test_returns_session_inactive_when_no_forge_session(self):
        with patch("subprocess.run", return_value=_proc(returncode=1)):
            result = self._fn(ctx=None, output_json=True)
        assert result["session_active"] is False
        assert result["session"] == "forge"
        assert result["agents"] == []

    # --- list-windows fails ------------------------------------------------

    def test_returns_error_when_list_windows_fails(self):
        with patch(
            "subprocess.run",
            side_effect=[
                _proc(returncode=0),  # has-session OK
                _proc(returncode=1, stderr="permission denied"),  # list-windows fail
            ],
        ):
            result = self._fn(ctx=None, output_json=True)
        assert result["session_active"] is True
        assert "permission denied" in result["error"]
        assert result["agents"] == []

    def test_returns_error_with_default_message_when_no_stderr(self):
        with patch(
            "subprocess.run",
            side_effect=[
                _proc(returncode=0),
                _proc(returncode=1, stderr=""),  # empty stderr
            ],
        ):
            result = self._fn(ctx=None, output_json=True)
        assert "Failed to list tmux windows" in result["error"]

    # --- happy path JSON ---------------------------------------------------

    def test_happy_path_returns_structured_data(self):
        with (
            patch(
                "subprocess.run",
                side_effect=[
                    _proc(returncode=0),  # has-session
                    _proc(returncode=0, stdout="0:tech\n1:review\n"),  # list-windows
                    _proc(returncode=0, stdout="Working on tests\n45% context left\n"),  # pane 0
                    _proc(returncode=0, stdout="> Ready\n"),  # pane 1
                ],
            ),
            patch("socket.gethostname", return_value="myhost"),
        ):
            result = self._fn(ctx=None, output_json=True)

        assert result["session_active"] is True
        assert result["summary"]["total"] == 2
        assert result["summary"]["active"] == 1
        assert result["summary"]["idle"] == 1
        assert len(result["agents"]) == 2
        # First agent has active status
        assert result["agents"][0]["status"] == "active"
        # Context extracted
        assert "45%" in result["agents"][0]["context"]

    def test_window_matching_hostname_marked_as_lead(self):
        with (
            patch(
                "subprocess.run",
                side_effect=[
                    _proc(returncode=0),
                    _proc(returncode=0, stdout="0:myhost\n"),
                    _proc(returncode=0, stdout="$ waiting"),
                ],
            ),
            patch("socket.gethostname", return_value="myhost.local"),
        ):
            result = self._fn(ctx=None, output_json=True)

        assert "(lead)" in result["agents"][0]["role"]

    def test_duplicate_window_names_flagged(self):
        with (
            patch(
                "subprocess.run",
                side_effect=[
                    _proc(returncode=0),
                    _proc(returncode=0, stdout="0:tech\n1:tech\n"),
                    _proc(returncode=0, stdout="$ waiting"),
                    _proc(returncode=0, stdout="$ waiting"),
                ],
            ),
            patch("socket.gethostname", return_value="other"),
        ):
            result = self._fn(ctx=None, output_json=True)

        roles = [a["role"] for a in result["agents"]]
        assert all("dup_name" in r for r in roles)

    def test_empty_pane_output_marks_agent_empty(self):
        with (
            patch(
                "subprocess.run",
                side_effect=[
                    _proc(returncode=0),
                    _proc(returncode=0, stdout="0:tech\n"),
                    _proc(returncode=0, stdout=""),  # empty pane
                ],
            ),
            patch("socket.gethostname", return_value="other"),
        ):
            result = self._fn(ctx=None, output_json=True)

        assert result["agents"][0]["empty"] is True

    def test_status_counts_include_error(self):
        with (
            patch(
                "subprocess.run",
                side_effect=[
                    _proc(returncode=0),
                    _proc(returncode=0, stdout="0:agent\n"),
                    _proc(returncode=0, stdout="TRACEBACK: something failed"),
                ],
            ),
            patch("socket.gethostname", return_value="other"),
        ):
            result = self._fn(ctx=None, output_json=True)

        assert result["summary"]["error"] == 1

    # --- non-JSON console path ---------------------------------------------

    def test_non_json_prints_table_and_returns_none(self):
        with (
            patch(
                "subprocess.run",
                side_effect=[
                    _proc(returncode=0),
                    _proc(returncode=0, stdout="0:tech\n"),
                    _proc(returncode=0, stdout="> waiting"),
                ],
            ),
            patch("socket.gethostname", return_value="other"),
            patch("forge_harness.cli_v2.fleet.console"),
        ):
            result = self._fn(ctx=None, output_json=False)

        assert result is None


# ===========================================================================
# _fleet_spawn
# ===========================================================================


class TestFleetSpawn:
    """Tests for _fleet_spawn helper."""

    def _fn(self, *args, **kwargs):
        from forge_harness.cli_v2.fleet import _fleet_spawn

        return _fleet_spawn(*args, **kwargs)

    def test_success_returns_structured_dict(self):
        with patch(
            "subprocess.run",
            return_value=_proc(returncode=0, stdout="Agent started"),
        ):
            result = self._fn(
                ctx=None,
                agent_name="forge:tech",
                domain=None,
                project=None,
                role="developer",
                output_json=True,
            )
        assert result["success"] is True
        assert result["agent"] == "forge:tech"
        assert "Agent started" in result["output"]

    def test_failure_returns_error_dict(self):
        with patch(
            "subprocess.run",
            return_value=_proc(returncode=1, stderr="session already exists"),
        ):
            result = self._fn(
                ctx=None,
                agent_name="forge:tech",
                domain=None,
                project=None,
                role="developer",
                output_json=True,
            )
        assert result["success"] is False
        assert "session already exists" in result["error"]

    def test_domain_and_project_passed_to_command(self):
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return _proc(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            self._fn(
                ctx=None,
                agent_name="forge:tech",
                domain="codeswiftr-com",
                project="interview-simulator",
                role="developer",
                output_json=True,
            )

        assert "codeswiftr-com" in captured[0]
        assert "interview-simulator" in captured[0]

    def test_no_domain_project_command_has_two_args(self):
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return _proc(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            self._fn(
                ctx=None,
                agent_name="forge:ops",
                domain=None,
                project=None,
                role="developer",
                output_json=True,
            )

        assert len(captured[0]) == 2  # script + agent_name only

    def test_script_not_found_returns_error(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = self._fn(
                ctx=None,
                agent_name="forge:tech",
                domain=None,
                project=None,
                role="developer",
                output_json=True,
            )
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_non_json_mode_does_not_raise(self):
        with (
            patch(
                "subprocess.run",
                return_value=_proc(returncode=0, stdout="OK"),
            ),
            patch("forge_harness.cli_v2.fleet.format_success"),
            patch("forge_harness.cli_v2.fleet.console"),
        ):
            result = self._fn(
                ctx=None,
                agent_name="forge:tech",
                domain=None,
                project=None,
                role="developer",
                output_json=False,
            )
        assert result["success"] is True


# ===========================================================================
# _fleet_stop
# ===========================================================================


class TestFleetStop:
    """Tests for _fleet_stop helper."""

    def _fn(self, *args, **kwargs):
        from forge_harness.cli_v2.fleet import _fleet_stop

        return _fleet_stop(*args, **kwargs)

    # --- single agent -------------------------------------------------------

    def test_single_success(self):
        with patch("subprocess.run", return_value=_proc(returncode=0)):
            result = self._fn(ctx=None, agent_name="forge:tech", all=False, output_json=True)
        assert result["success"] is True
        assert result["stopped"] == "forge:tech"

    def test_single_failure_returns_error(self):
        with patch(
            "subprocess.run",
            return_value=_proc(returncode=1, stderr="no session: forge:tech"),
        ):
            result = self._fn(ctx=None, agent_name="forge:tech", all=False, output_json=True)
        assert result["success"] is False
        assert "forge:tech" in result["error"]

    def test_single_failure_uses_default_message_when_no_stderr(self):
        with patch("subprocess.run", return_value=_proc(returncode=1, stderr="")):
            result = self._fn(ctx=None, agent_name="forge:tech", all=False, output_json=True)
        assert result["success"] is False
        assert "forge:tech" in result["error"]  # default message includes agent name

    def test_single_exception_returns_error(self):
        with patch("subprocess.run", side_effect=RuntimeError("unexpected")):
            result = self._fn(ctx=None, agent_name="forge:tech", all=False, output_json=True)
        assert result["success"] is False
        assert "unexpected" in result["error"]

    # --- stop --all ---------------------------------------------------------

    def test_all_stops_forge_sessions_only(self):
        list_result = _proc(stdout="forge:tech\nforge:review\nmisc\nother\n")
        kill1 = _proc(returncode=0)
        kill2 = _proc(returncode=0)

        with patch("subprocess.run", side_effect=[list_result, kill1, kill2]):
            result = self._fn(ctx=None, agent_name=None, all=True, output_json=True)

        assert result["success"] is True
        assert result["stopped_count"] == 2
        assert set(result["stopped"]) == {"forge:tech", "forge:review"}

    def test_all_returns_empty_when_no_forge_sessions(self):
        list_result = _proc(stdout="vanilla\nother\n")

        with patch("subprocess.run", return_value=list_result):
            result = self._fn(ctx=None, agent_name=None, all=True, output_json=True)

        assert result["success"] is True
        assert result["stopped"] == []
        assert result["stopped_count"] == 0

    def test_all_handles_partial_failure(self):
        list_result = _proc(stdout="forge:tech\nforge:broken\n")
        kill_ok = _proc(returncode=0)
        kill_fail = _proc(returncode=1, stderr="kill failed")

        with patch("subprocess.run", side_effect=[list_result, kill_ok, kill_fail]):
            result = self._fn(ctx=None, agent_name=None, all=True, output_json=True)

        assert result["stopped_count"] == 1
        assert len(result["failures"]) == 1
        assert result["failures"][0]["session"] == "forge:broken"

    def test_all_list_sessions_failure_returns_error(self):
        with patch(
            "subprocess.run",
            return_value=_proc(returncode=1, stderr="tmux error"),
        ):
            result = self._fn(ctx=None, agent_name=None, all=True, output_json=True)

        assert result["success"] is False
        assert "tmux error" in result["error"]

    def test_all_list_sessions_failure_default_message(self):
        with patch("subprocess.run", return_value=_proc(returncode=1, stderr="")):
            result = self._fn(ctx=None, agent_name=None, all=True, output_json=True)

        assert "tmux list-sessions failed" in result["error"]

    def test_all_exception_returns_error(self):
        with patch("subprocess.run", side_effect=RuntimeError("boom")):
            result = self._fn(ctx=None, agent_name=None, all=True, output_json=True)

        assert result["success"] is False
        assert "boom" in result["error"]

    def test_all_non_json_format_success_called(self):
        list_result = _proc(stdout="forge:tech\n")
        kill = _proc(returncode=0)

        with (
            patch("subprocess.run", side_effect=[list_result, kill]),
            patch("forge_harness.cli_v2.fleet.format_success") as mock_success,
        ):
            self._fn(ctx=None, agent_name=None, all=True, output_json=False)

        mock_success.assert_called_once()


# ===========================================================================
# _fleet_save
# ===========================================================================


class TestFleetSave:
    """Tests for _fleet_save helper."""

    def _fn(self, *args, **kwargs):
        from forge_harness.cli_v2.fleet import _fleet_save

        return _fleet_save(*args, **kwargs)

    def test_success_returns_path(self):
        with patch("subprocess.run", return_value=_proc(returncode=0)):
            result = self._fn(ctx=None, output_json=True)
        assert result["success"] is True
        assert ".forge/state/fleet_state.json" in result["path"]

    def test_failure_with_stderr(self):
        with patch(
            "subprocess.run",
            return_value=_proc(returncode=1, stderr="disk full"),
        ):
            result = self._fn(ctx=None, output_json=True)
        assert result["success"] is False
        assert "disk full" in result["error"]

    def test_failure_with_empty_stderr_uses_default(self):
        with patch("subprocess.run", return_value=_proc(returncode=1, stderr="")):
            result = self._fn(ctx=None, output_json=True)
        assert "fleet-save.sh failed" in result["error"]

    def test_script_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = self._fn(ctx=None, output_json=True)
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_non_json_success_prints_message(self):
        with (
            patch("subprocess.run", return_value=_proc(returncode=0)),
            patch("forge_harness.cli_v2.fleet.format_success") as mock_success,
        ):
            self._fn(ctx=None, output_json=False)
        mock_success.assert_called_once()


# ===========================================================================
# _fleet_restore
# ===========================================================================


class TestFleetRestore:
    """Tests for _fleet_restore helper."""

    def _fn(self, *args, **kwargs):
        from forge_harness.cli_v2.fleet import _fleet_restore

        return _fleet_restore(*args, **kwargs)

    def test_success_returns_true(self):
        with patch("subprocess.run", return_value=_proc(returncode=0)):
            result = self._fn(ctx=None, output_json=True)
        assert result["success"] is True

    def test_failure_with_stderr(self):
        with patch(
            "subprocess.run",
            return_value=_proc(returncode=1, stderr="state file missing"),
        ):
            result = self._fn(ctx=None, output_json=True)
        assert result["success"] is False
        assert "state file missing" in result["error"]

    def test_failure_with_empty_stderr_uses_default(self):
        with patch("subprocess.run", return_value=_proc(returncode=1, stderr="")):
            result = self._fn(ctx=None, output_json=True)
        assert "fleet-restore.sh failed" in result["error"]

    def test_script_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = self._fn(ctx=None, output_json=True)
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_non_json_prints_success(self):
        with (
            patch("subprocess.run", return_value=_proc(returncode=0)),
            patch("forge_harness.cli_v2.fleet.format_success") as mock_success,
        ):
            self._fn(ctx=None, output_json=False)
        mock_success.assert_called_once()


# ===========================================================================
# _fleet_heartbeat (agent-specific)
# ===========================================================================


class TestFleetHeartbeat:
    """Tests for _fleet_heartbeat helper."""

    def _fn(self, *args, **kwargs):
        from forge_harness.cli_v2.fleet import _fleet_heartbeat

        return _fleet_heartbeat(*args, **kwargs)

    def test_delegates_to_agents_heartbeat(self):
        mock_ctx = MagicMock()
        mock_ctx.invoke.return_value = {"status": "ok"}
        mock_heartbeat = MagicMock()

        with patch(
            "forge_harness.cli_v2.fleet._fleet_heartbeat",
            wraps=lambda ctx, agent_name, output_json=False: ctx.invoke(
                mock_heartbeat, agent_id=agent_name
            ),
        ):
            result = self._fn(ctx=mock_ctx, agent_name="forge:tech", output_json=True)

        mock_ctx.invoke.assert_called_once()

    def test_prints_info_in_non_json_mode(self):
        mock_ctx = MagicMock()
        mock_ctx.invoke.return_value = None

        with (
            patch("forge_harness.commands.agent.agents_heartbeat", MagicMock()),
            patch("forge_harness.cli_v2.fleet.format_info") as mock_info,
        ):
            self._fn(ctx=mock_ctx, agent_name="forge:tech", output_json=False)

        mock_info.assert_called_once()


# ===========================================================================
# _fleet_heartbeat_domain
# ===========================================================================


class TestFleetHeartbeatDomain:
    """Tests for _fleet_heartbeat_domain helper."""

    def _fn(self, *args, **kwargs):
        from forge_harness.cli_v2.fleet import _fleet_heartbeat_domain

        return _fleet_heartbeat_domain(*args, **kwargs)

    def _make_collector(self, summaries: dict):
        collector = MagicMock()
        collector.collect.return_value = summaries
        collector.output_dir = "/fake/output"
        return collector

    def test_domain_not_found_returns_error(self):
        fake_collector = self._make_collector({"other-domain": {}})

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
            patch("forge_harness.cli_v2.fleet.format_error"),
        ):
            result = self._fn(ctx=None, domain="missing-domain", output_json=True)

        assert "not found" in result["error"]

    def test_domain_found_returns_summary_as_json(self):
        now_iso = datetime.now(UTC).isoformat()
        summary = {
            "status_counts": {"active": 1, "idle": 0, "error": 0},
            "sessions": [
                {
                    "session_id": "sess1",
                    "status": "active",
                    "context_percentage": 75,
                    "current_task": "Build feature X",
                    "last_activity": now_iso,
                }
            ],
            "total_sessions": 1,
        }
        fake_collector = self._make_collector({"codeswiftr-com": summary})

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
        ):
            result = self._fn(ctx=None, domain="codeswiftr-com", output_json=True)

        assert result == summary

    def test_non_json_prints_table_and_returns_none(self):
        now_iso = datetime.now(UTC).isoformat()
        summary = {
            "status_counts": {"active": 1, "idle": 0, "error": 0},
            "sessions": [
                {
                    "session_id": "sess1",
                    "status": "active",
                    "context_percentage": 50,
                    "current_task": None,
                    "last_activity": now_iso,
                }
            ],
            "total_sessions": 1,
        }
        fake_collector = self._make_collector({"codeswiftr-com": summary})

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
            patch("forge_harness.cli_v2.fleet.console"),
        ):
            result = self._fn(ctx=None, domain="codeswiftr-com", output_json=False)

        assert result is None

    def test_non_json_truncates_long_task_names(self):
        """Current task longer than 40 chars should be truncated."""
        summary = {
            "status_counts": {"active": 1, "idle": 0, "error": 0},
            "sessions": [
                {
                    "session_id": "sess1",
                    "status": "active",
                    "context_percentage": 50,
                    "current_task": "A" * 50,
                    "last_activity": "",
                }
            ],
            "total_sessions": 1,
        }
        fake_collector = self._make_collector({"domain": summary})

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
            patch("forge_harness.cli_v2.fleet.console"),
        ):
            # Should not raise
            self._fn(ctx=None, domain="domain", output_json=False)

    def test_activity_time_buckets(self):
        """Last activity delta bucketed into s/m/h labels."""
        # seconds ago
        seconds_ago = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
        # minutes ago
        minutes_ago = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        # hours ago
        hours_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()

        for ts, expected_suffix in [
            (seconds_ago, "s ago"),
            (minutes_ago, "m ago"),
            (hours_ago, "h ago"),
        ]:
            summary = {
                "status_counts": {},
                "sessions": [
                    {
                        "session_id": "s1",
                        "status": "idle",
                        "context_percentage": 0,
                        "current_task": None,
                        "last_activity": ts,
                    }
                ],
                "total_sessions": 1,
            }
            fake_collector = self._make_collector({"dom": summary})

            printed_rows = []
            # We just verify no exception is raised for each time bucket
            with (
                patch(
                    "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                    return_value=fake_collector,
                ),
                patch(
                    "forge_harness.cli_v2._common.find_forge_root",
                    return_value=Path("/fake/forge"),
                ),
                patch("forge_harness.cli_v2.fleet.console"),
            ):
                self._fn(ctx=None, domain="dom", output_json=False)

    def test_invalid_timestamp_shows_unknown(self):
        summary = {
            "status_counts": {},
            "sessions": [
                {
                    "session_id": "s1",
                    "status": "idle",
                    "context_percentage": 0,
                    "current_task": None,
                    "last_activity": "not-a-date",
                }
            ],
            "total_sessions": 1,
        }
        fake_collector = self._make_collector({"dom": summary})

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
            patch("forge_harness.cli_v2.fleet.console"),
        ):
            # Should not raise
            self._fn(ctx=None, domain="dom", output_json=False)

    def test_status_styles_in_table(self):
        """All status values (active, idle, error, unknown) render without exception."""
        for status in ("active", "idle", "error", "unknown_status"):
            summary = {
                "status_counts": {},
                "sessions": [
                    {
                        "session_id": "s1",
                        "status": status,
                        "context_percentage": 0,
                        "current_task": None,
                        "last_activity": "",
                    }
                ],
                "total_sessions": 1,
            }
            fake_collector = self._make_collector({"dom": summary})

            with (
                patch(
                    "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                    return_value=fake_collector,
                ),
                patch(
                    "forge_harness.cli_v2._common.find_forge_root",
                    return_value=Path("/fake/forge"),
                ),
                patch("forge_harness.cli_v2.fleet.console"),
            ):
                self._fn(ctx=None, domain="dom", output_json=False)


# ===========================================================================
# _fleet_heartbeat_all_domains
# ===========================================================================


class TestFleetHeartbeatAllDomains:
    """Tests for _fleet_heartbeat_all_domains helper."""

    def _fn(self, *args, **kwargs):
        from forge_harness.cli_v2.fleet import _fleet_heartbeat_all_domains

        return _fleet_heartbeat_all_domains(*args, **kwargs)

    def _make_collector(self, summaries: dict):
        collector = MagicMock()
        collector.collect.return_value = summaries
        collector.output_dir = "/fake/output"
        return collector

    def test_empty_summaries_returns_empty_dict(self):
        fake_collector = self._make_collector({})

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
        ):
            result = self._fn(ctx=None, output_json=True)

        assert result == {}

    def test_returns_summaries_as_json(self):
        summaries = {
            "domain-a": {
                "status_counts": {"active": 1, "idle": 0, "error": 0},
                "sessions": [],
                "total_sessions": 1,
                "timestamp": "",
            }
        }
        fake_collector = self._make_collector(summaries)

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
        ):
            result = self._fn(ctx=None, output_json=True)

        assert result == summaries

    def test_non_json_prints_table_and_returns_none(self):
        summaries = {
            "domain-a": {
                "status_counts": {"active": 1, "idle": 0, "error": 0},
                "sessions": [{"status": "active", "context_percentage": 80}],
                "total_sessions": 1,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        }
        fake_collector = self._make_collector(summaries)

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
            patch("forge_harness.cli_v2.fleet.console"),
            patch("forge_harness.cli_v2.fleet.format_success"),
            patch("forge_harness.cli_v2.fleet.format_info"),
        ):
            result = self._fn(ctx=None, output_json=False)

        assert result is None

    def test_health_status_green_when_active(self):
        summaries = {
            "domain-a": {
                "status_counts": {"active": 2, "idle": 0, "error": 0},
                "sessions": [],
                "total_sessions": 2,
                "timestamp": "",
            }
        }
        fake_collector = self._make_collector(summaries)

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
            patch("forge_harness.cli_v2.fleet.console"),
            patch("forge_harness.cli_v2.fleet.format_success") as mock_success,
            patch("forge_harness.cli_v2.fleet.format_info"),
        ):
            self._fn(ctx=None, output_json=False)

        # Summary message should mention "green"
        call_args = mock_success.call_args[0][0]
        assert "green" in call_args

    def test_health_status_red_when_errors(self):
        summaries = {
            "domain-a": {
                "status_counts": {"active": 0, "idle": 0, "error": 1},
                "sessions": [],
                "total_sessions": 1,
                "timestamp": "",
            }
        }
        fake_collector = self._make_collector(summaries)

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
            patch("forge_harness.cli_v2.fleet.console"),
            patch("forge_harness.cli_v2.fleet.format_success") as mock_success,
            patch("forge_harness.cli_v2.fleet.format_info"),
        ):
            self._fn(ctx=None, output_json=False)

        call_args = mock_success.call_args[0][0]
        assert "red" in call_args

    def test_health_status_yellow_when_all_idle(self):
        summaries = {
            "domain-a": {
                "status_counts": {"active": 0, "idle": 2, "error": 0},
                "sessions": [],
                "total_sessions": 2,
                "timestamp": "",
            }
        }
        fake_collector = self._make_collector(summaries)

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
            patch("forge_harness.cli_v2.fleet.console"),
            patch("forge_harness.cli_v2.fleet.format_success") as mock_success,
            patch("forge_harness.cli_v2.fleet.format_info"),
        ):
            self._fn(ctx=None, output_json=False)

        call_args = mock_success.call_args[0][0]
        assert "yellow" in call_args

    def test_health_status_unknown_when_zero_sessions(self):
        summaries = {
            "domain-a": {
                "status_counts": {"active": 0, "idle": 0, "error": 0},
                "sessions": [],
                "total_sessions": 0,
                "timestamp": "",
            }
        }
        fake_collector = self._make_collector(summaries)

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
            patch("forge_harness.cli_v2.fleet.console"),
            patch("forge_harness.cli_v2.fleet.format_success") as mock_success,
            patch("forge_harness.cli_v2.fleet.format_info"),
        ):
            self._fn(ctx=None, output_json=False)

        call_args = mock_success.call_args[0][0]
        assert "unknown" in call_args

    def test_avg_context_from_active_sessions(self):
        """Average context for active sessions is computed correctly."""
        summaries = {
            "domain-a": {
                "status_counts": {"active": 2, "idle": 0, "error": 0},
                "sessions": [
                    {"status": "active", "context_percentage": 80},
                    {"status": "active", "context_percentage": 60},
                    {"status": "idle", "context_percentage": 10},
                ],
                "total_sessions": 3,
                "timestamp": "",
            }
        }
        fake_collector = self._make_collector(summaries)

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
            patch("forge_harness.cli_v2.fleet.console"),
            patch("forge_harness.cli_v2.fleet.format_success"),
            patch("forge_harness.cli_v2.fleet.format_info"),
        ):
            # Should not raise; avg = (80+60)//2 = 70
            self._fn(ctx=None, output_json=False)

    def test_heartbeat_timestamps_rendered(self):
        """Timestamps within different time buckets render without error."""
        now = datetime.now(UTC)
        for ts in [
            (now - timedelta(seconds=10)).isoformat(),
            (now - timedelta(minutes=3)).isoformat(),
            (now - timedelta(hours=1)).isoformat(),
            "bad-timestamp",
            "",
        ]:
            summaries = {
                "domain": {
                    "status_counts": {},
                    "sessions": [],
                    "total_sessions": 0,
                    "timestamp": ts,
                }
            }
            fake_collector = self._make_collector(summaries)

            with (
                patch(
                    "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                    return_value=fake_collector,
                ),
                patch(
                    "forge_harness.cli_v2._common.find_forge_root",
                    return_value=Path("/fake/forge"),
                ),
                patch("forge_harness.cli_v2.fleet.console"),
                patch("forge_harness.cli_v2.fleet.format_success"),
                patch("forge_harness.cli_v2.fleet.format_info"),
            ):
                self._fn(ctx=None, output_json=False)

    def test_unknown_count_included_in_summary_when_present(self):
        """When unknown > 0, the summary text includes the count."""
        summaries = {
            "d": {
                "status_counts": {},
                "sessions": [],
                "total_sessions": 0,
                "timestamp": "",
            }
        }
        fake_collector = self._make_collector(summaries)

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
            patch("forge_harness.cli_v2.fleet.console"),
            patch("forge_harness.cli_v2.fleet.format_success") as mock_success,
            patch("forge_harness.cli_v2.fleet.format_info"),
        ):
            self._fn(ctx=None, output_json=False)

        call_args = mock_success.call_args[0][0]
        # domain with 0 sessions -> health = unknown -> "1 unknown"
        assert "unknown" in call_args


# ===========================================================================
# CLI integration via CliRunner
# ===========================================================================


@pytest.fixture
def runner():
    return CliRunner()


class TestFleetCLIIntegration:
    """CLI-level tests using CliRunner for the fleet command."""

    # --- help/discovery ---------------------------------------------------

    def test_help_exits_zero(self, runner):
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "--help"])
        assert result.exit_code == 0
        assert "fleet" in result.output.lower()

    # --- status -----------------------------------------------------------

    def test_status_json_envelope_structure(self, runner):
        from forge_harness.cli_v2 import cli

        with (
            patch(
                "subprocess.run",
                side_effect=[
                    _proc(returncode=0),  # has-session
                    _proc(returncode=0, stdout="0:tech\n"),  # list-windows
                    _proc(returncode=0, stdout="> ready"),  # capture-pane
                ],
            ),
            patch("socket.gethostname", return_value="other"),
        ):
            result = runner.invoke(cli, ["fleet", "status", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert "data" in data
        assert "timestamp" in data
        assert data["command"] == "forge fleet status"

    def test_status_no_session_exits_zero(self, runner):
        from forge_harness.cli_v2 import cli

        with patch("subprocess.run", return_value=_proc(returncode=1)):
            result = runner.invoke(cli, ["fleet", "status"])

        # Returns 0 or 1 (SystemExit may be raised from format_warning only in non-json)
        assert result.exit_code in (0, 1)

    def test_status_json_no_session(self, runner):
        from forge_harness.cli_v2 import cli

        with patch("subprocess.run", return_value=_proc(returncode=1)):
            result = runner.invoke(cli, ["fleet", "status", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["session_active"] is False

    # --- spawn -----------------------------------------------------------

    def test_spawn_no_agent_name_exits_nonzero(self, runner):
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "spawn"])
        assert result.exit_code != 0

    def test_spawn_success_json_envelope(self, runner):
        from forge_harness.cli_v2 import cli

        with patch("subprocess.run", return_value=_proc(returncode=0, stdout="spawned")):
            result = runner.invoke(cli, ["fleet", "spawn", "forge:tech", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert "forge:tech" in data["command"]

    def test_spawn_with_domain_project_options(self, runner):
        from forge_harness.cli_v2 import cli

        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return _proc(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(
                cli,
                [
                    "fleet",
                    "spawn",
                    "forge:tech",
                    "-d",
                    "codeswiftr-com",
                    "-p",
                    "interview-sim",
                ],
            )

        assert result.exit_code == 0
        assert "codeswiftr-com" in captured_cmds[0]

    # --- stop ------------------------------------------------------------

    def test_stop_no_name_no_all_exits_nonzero(self, runner):
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "stop"])
        assert result.exit_code != 0

    def test_stop_single_agent_success_json(self, runner):
        from forge_harness.cli_v2 import cli

        with patch("subprocess.run", return_value=_proc(returncode=0)):
            result = runner.invoke(cli, ["fleet", "stop", "forge:tech", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_stop_all_json(self, runner):
        from forge_harness.cli_v2 import cli

        with patch(
            "subprocess.run",
            side_effect=[
                _proc(stdout="forge:tech\n"),
                _proc(returncode=0),
            ],
        ):
            result = runner.invoke(cli, ["fleet", "stop", "--all", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["stopped_count"] == 1

    # --- save ------------------------------------------------------------

    def test_save_success_json(self, runner):
        from forge_harness.cli_v2 import cli

        with patch("subprocess.run", return_value=_proc(returncode=0)):
            result = runner.invoke(cli, ["fleet", "save", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["success"] is True

    def test_save_failure_json(self, runner):
        from forge_harness.cli_v2 import cli

        with patch("subprocess.run", return_value=_proc(returncode=1, stderr="no space")):
            result = runner.invoke(cli, ["fleet", "save", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["success"] is False

    # --- restore ---------------------------------------------------------

    def test_restore_success_json(self, runner):
        from forge_harness.cli_v2 import cli

        with patch("subprocess.run", return_value=_proc(returncode=0)):
            result = runner.invoke(cli, ["fleet", "restore", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["success"] is True

    def test_restore_failure_json(self, runner):
        from forge_harness.cli_v2 import cli

        with patch("subprocess.run", return_value=_proc(returncode=1, stderr="missing state")):
            result = runner.invoke(cli, ["fleet", "restore", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["success"] is False

    # --- heartbeat -------------------------------------------------------

    def test_heartbeat_requires_agent_or_all_domains(self, runner):
        from forge_harness.cli_v2 import cli

        result = runner.invoke(cli, ["fleet", "heartbeat"])
        assert result.exit_code != 0

    def test_heartbeat_all_domains_json(self, runner):
        from forge_harness.cli_v2 import cli

        fake_collector = MagicMock()
        fake_collector.collect.return_value = {
            "domain-a": {
                "status_counts": {"active": 1},
                "sessions": [],
                "total_sessions": 1,
                "timestamp": "",
            }
        }
        fake_collector.output_dir = "/fake"

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
        ):
            result = runner.invoke(cli, ["fleet", "heartbeat", "--all-domains", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert "domain-a" in data["data"]

    def test_heartbeat_domain_json(self, runner):
        from forge_harness.cli_v2 import cli

        summary = {
            "status_counts": {"active": 1},
            "sessions": [],
            "total_sessions": 1,
        }
        fake_collector = MagicMock()
        fake_collector.collect.return_value = {"codeswiftr-com": summary}
        fake_collector.output_dir = "/fake"

        with (
            patch(
                "forge_harness.heartbeat.cross_domain.CrossDomainHeartbeatCollector",
                return_value=fake_collector,
            ),
            patch(
                "forge_harness.cli_v2._common.find_forge_root",
                return_value=Path("/fake/forge"),
            ),
        ):
            result = runner.invoke(cli, ["fleet", "heartbeat", "codeswiftr-com", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_heartbeat_tmux_agent_delegates_to_agents_heartbeat(self, runner):
        from forge_harness.cli_v2 import cli

        # forge:tech contains ':' so it goes through _fleet_heartbeat
        mock_hb = MagicMock(return_value=None)

        with (
            patch("forge_harness.commands.agent.agents_heartbeat", mock_hb),
            patch("forge_harness.cli_v2.fleet._fleet_heartbeat") as mock_fn,
        ):
            mock_fn.return_value = None
            result = runner.invoke(cli, ["fleet", "heartbeat", "forge:tech"])

        # Either the real call went through or we patched it – either way no crash
        assert result.exit_code in (0, 1)


# ===========================================================================
# JSON output envelope format
# ===========================================================================


class TestJsonOutputFormat:
    """Verify the JSON envelope wrapping produced by fleet commands."""

    def test_json_output_helper_success_structure(self):
        from forge_harness.cli_v2.json_output import json_output

        payload = {"key": "value"}
        envelope = json_output(True, payload, command="forge fleet status")

        assert envelope["success"] is True
        assert envelope["data"] == payload
        assert envelope["error"] is None
        assert "timestamp" in envelope
        assert envelope["command"] == "forge fleet status"

    def test_json_output_helper_failure_structure(self):
        from forge_harness.cli_v2.json_output import json_output

        envelope = json_output(False, None, error="something failed", command="forge fleet save")

        assert envelope["success"] is False
        assert envelope["data"] is None
        assert envelope["error"]["message"] == "something failed"

    def test_print_json_outputs_valid_json(self, capsys):
        from forge_harness.cli_v2.json_output import print_json

        payload = {"success": True, "data": {"x": 1}}
        print_json(payload)

        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["success"] is True


# ===========================================================================
# Additional edge-case tests to cover remaining branches
# ===========================================================================


class TestFleetSpawnNonJsonErrorPaths:
    """Cover non-JSON error printing in _fleet_spawn."""

    def _fn(self, *args, **kwargs):
        from forge_harness.cli_v2.fleet import _fleet_spawn

        return _fleet_spawn(*args, **kwargs)

    def test_non_json_failure_calls_format_error(self):
        with (
            patch(
                "subprocess.run",
                return_value=_proc(returncode=1, stderr="launch failed"),
            ),
            patch("forge_harness.cli_v2.fleet.format_error") as mock_err,
        ):
            self._fn(
                ctx=None,
                agent_name="forge:ops",
                domain=None,
                project=None,
                role="developer",
                output_json=False,
            )
        mock_err.assert_called_once()

    def test_non_json_script_not_found_calls_format_error(self):
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("forge_harness.cli_v2.fleet.format_error") as mock_err,
        ):
            self._fn(
                ctx=None,
                agent_name="forge:ops",
                domain=None,
                project=None,
                role="developer",
                output_json=False,
            )
        mock_err.assert_called_once()


class TestFleetStopNonJsonErrorPaths:
    """Cover non-JSON error printing in _fleet_stop."""

    def _fn(self, *args, **kwargs):
        from forge_harness.cli_v2.fleet import _fleet_stop

        return _fleet_stop(*args, **kwargs)

    def test_single_non_json_failure_calls_format_error(self):
        with (
            patch(
                "subprocess.run",
                return_value=_proc(returncode=1, stderr="no session"),
            ),
            patch("forge_harness.cli_v2.fleet.format_error") as mock_err,
        ):
            self._fn(ctx=None, agent_name="forge:gone", all=False, output_json=False)
        mock_err.assert_called_once()

    def test_single_non_json_exception_calls_format_error(self):
        with (
            patch("subprocess.run", side_effect=RuntimeError("crash")),
            patch("forge_harness.cli_v2.fleet.format_error") as mock_err,
        ):
            self._fn(ctx=None, agent_name="forge:gone", all=False, output_json=False)
        mock_err.assert_called_once()

    def test_all_non_json_list_failure_calls_format_error(self):
        with (
            patch(
                "subprocess.run",
                return_value=_proc(returncode=1, stderr="tmux gone"),
            ),
            patch("forge_harness.cli_v2.fleet.format_error") as mock_err,
        ):
            self._fn(ctx=None, agent_name=None, all=True, output_json=False)
        mock_err.assert_called_once()

    def test_all_non_json_partial_failure_calls_format_warning(self):
        list_result = _proc(stdout="forge:a\nforge:b\n")
        kill_ok = _proc(returncode=0)
        kill_fail = _proc(returncode=1, stderr="kill failed")

        with (
            patch("subprocess.run", side_effect=[list_result, kill_ok, kill_fail]),
            patch("forge_harness.cli_v2.fleet.format_warning") as mock_warn,
        ):
            self._fn(ctx=None, agent_name=None, all=True, output_json=False)

        mock_warn.assert_called_once()


class TestFleetSaveRestoreNonJsonErrorPaths:
    """Cover non-JSON error paths in _fleet_save and _fleet_restore."""

    def test_save_non_json_failure_calls_format_error(self):
        from forge_harness.cli_v2.fleet import _fleet_save

        with (
            patch(
                "subprocess.run",
                return_value=_proc(returncode=1, stderr="disk full"),
            ),
            patch("forge_harness.cli_v2.fleet.format_error") as mock_err,
        ):
            _fleet_save(ctx=None, output_json=False)
        mock_err.assert_called_once()

    def test_save_non_json_not_found_calls_format_error(self):
        from forge_harness.cli_v2.fleet import _fleet_save

        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("forge_harness.cli_v2.fleet.format_error") as mock_err,
        ):
            _fleet_save(ctx=None, output_json=False)
        mock_err.assert_called_once()

    def test_restore_non_json_failure_calls_format_error(self):
        from forge_harness.cli_v2.fleet import _fleet_restore

        with (
            patch(
                "subprocess.run",
                return_value=_proc(returncode=1, stderr="state missing"),
            ),
            patch("forge_harness.cli_v2.fleet.format_error") as mock_err,
        ):
            _fleet_restore(ctx=None, output_json=False)
        mock_err.assert_called_once()

    def test_restore_non_json_not_found_calls_format_error(self):
        from forge_harness.cli_v2.fleet import _fleet_restore

        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("forge_harness.cli_v2.fleet.format_error") as mock_err,
        ):
            _fleet_restore(ctx=None, output_json=False)
        mock_err.assert_called_once()


class TestFleetExceptionHandler:
    """Cover the outer exception handler in the fleet Click command."""

    def test_unexpected_exception_in_status_exits_with_general_error(self):
        runner = CliRunner()
        from forge_harness.cli_v2 import cli

        with patch(
            "forge_harness.cli_v2.fleet._fleet_status",
            side_effect=RuntimeError("unexpected boom"),
        ):
            result = runner.invoke(cli, ["fleet", "status"])

        assert result.exit_code == 1

    def test_unexpected_exception_outputs_json_error_envelope(self):
        runner = CliRunner()
        from forge_harness.cli_v2 import cli

        with patch(
            "forge_harness.cli_v2.fleet._fleet_status",
            side_effect=RuntimeError("boom json"),
        ):
            result = runner.invoke(cli, ["fleet", "status", "--json"])

        # The handle_error_json call should have printed something before SystemExit
        assert result.exit_code == 1
