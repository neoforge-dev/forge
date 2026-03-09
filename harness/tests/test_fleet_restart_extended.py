"""Extended tests for forge_harness.fleet.restart module.

Covers gaps left by test_fleet_restart.py:
- Edge cases in _extract_current_task (boundary length, multiple patterns)
- Edge cases in _extract_context_usage (boundary values, case variants)
- Edge cases in _extract_error_message (traceback multiline, truncation)
- _check_session_exit_code with opened_at=None edge cases
- recover_task_state with all supported file extensions
- restart_agent with None/empty recovered_context
- _log_restart_event appends correctly on multiple writes
- auto_restart_stale threshold_minutes pass-through
- auto_restart_stale with task_state having no file/context fields
- get_restart_history with large log files and JSON corruption
- create_agent_restarter with explicit forge_root discovery path
- AgentRestarter cwd-based auto-discovery walks parent dirs
- detect_stale_agents with context_percent extraction
- detect_crashed_agents with multiple sessions mixed
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.fleet.restart import (
    AgentRestarter,
    HealthStatus,
    RestartEvent,
    TaskState,
    create_agent_restarter,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_forge_root(tmp_path):
    (tmp_path / ".forge/fleet").mkdir()
    return tmp_path


@pytest.fixture
def restarter(tmp_forge_root):
    return AgentRestarter(forge_root=tmp_forge_root, stale_threshold_minutes=30)


def _make_session(name: str, seconds_ago: int = 0) -> dict:
    ts = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    return {"name": name, "created": ts, "last_activity": ts}


def _make_run_success():
    return MagicMock(returncode=0)


# ---------------------------------------------------------------------------
# _extract_current_task — boundary and multi-pattern cases
# ---------------------------------------------------------------------------


class TestExtractCurrentTaskEdgeCases:
    """Edge cases not covered by the base test suite."""

    def test_exactly_ten_char_line_ignored(self, restarter):
        """Lines with exactly 10 chars should be ignored (> 10 required)."""
        output = "1234567890\n"  # exactly 10 chars
        result = restarter._extract_current_task(output)
        assert result is None

    def test_eleven_char_line_returned(self, restarter):
        """Lines with 11 chars meet the minimum and are returned."""
        output = "12345678901\n"  # 11 chars
        result = restarter._extract_current_task(output)
        assert result == "12345678901"

    def test_prompt_line_skipped_in_fallback(self, restarter):
        """Fallback skips lines starting with '>'."""
        output = "> some prompt\nanother valid long line here\n"
        result = restarter._extract_current_task(output)
        assert result == "another valid long line here"

    def test_multiple_patterns_returns_first_match(self, restarter):
        """When multiple patterns exist, the first match wins."""
        output = "Task: first task description here\nWorking on: second task description\n"
        result = restarter._extract_current_task(output)
        assert result == "first task description here"

    def test_whitespace_only_output_returns_none(self, restarter):
        """Output with only whitespace/newlines returns None."""
        result = restarter._extract_current_task("   \n   \n   ")
        assert result is None

    def test_task_at_exact_200_chars_not_truncated(self, restarter):
        """Task description of exactly 200 chars is returned as-is."""
        desc = "A" * 200
        output = f"Task: {desc}\n"
        result = restarter._extract_current_task(output)
        assert result is not None
        assert len(result) == 200

    def test_task_at_201_chars_truncated(self, restarter):
        """Task description of 201+ chars is truncated to 200."""
        desc = "A" * 201
        output = f"Task: {desc}\n"
        result = restarter._extract_current_task(output)
        assert result is not None
        assert len(result) == 200

    def test_feature_prefix_case_insensitive(self, restarter):
        """FEATURE: prefix matches regardless of case."""
        output = "FEATURE: user authentication module\n"
        result = restarter._extract_current_task(output)
        assert result is not None
        assert "user authentication module" in result

    def test_implementing_prefix_extracts_task(self, restarter):
        """'Implementing:' prefix is matched correctly."""
        output = "Implementing: REST API endpoints for users\n"
        result = restarter._extract_current_task(output)
        assert result == "REST API endpoints for users"

    def test_output_with_only_short_lines_returns_none(self, restarter):
        """All lines <= 10 chars, no pattern match, returns None."""
        output = "abc\ndef\nghij\n"
        result = restarter._extract_current_task(output)
        assert result is None


# ---------------------------------------------------------------------------
# _extract_context_usage — boundary and variant patterns
# ---------------------------------------------------------------------------


class TestExtractContextUsageEdgeCases:
    """Edge cases for _extract_context_usage."""

    def test_zero_percent_context(self, restarter):
        """0% context usage returns 0.0."""
        result = restarter._extract_context_usage("context: 0%")
        assert result == pytest.approx(0.0)

    def test_context_with_spaces_variant(self, restarter):
        """'context 75%' with spaces matches."""
        result = restarter._extract_context_usage("context 75%")
        assert result == pytest.approx(0.75)

    def test_usage_colon_pattern(self, restarter):
        """'usage: 55%' pattern matches."""
        result = restarter._extract_context_usage("usage: 55%")
        assert result == pytest.approx(0.55)

    def test_percent_context_inline(self, restarter):
        """'80% context remaining' matches the percent-first pattern."""
        result = restarter._extract_context_usage("80% context remaining")
        assert result == pytest.approx(0.80)

    def test_context_in_multiline_output(self, restarter):
        """Pattern matched from a multiline output string."""
        output = "Task: do something important\nsome random line\ncontext: 45%\n"
        result = restarter._extract_context_usage(output)
        assert result == pytest.approx(0.45)

    def test_no_match_in_large_output(self, restarter):
        """Returns None when large output has no context pattern."""
        output = "\n".join(["line " + str(i) for i in range(50)])
        result = restarter._extract_context_usage(output)
        assert result is None


# ---------------------------------------------------------------------------
# _extract_error_message — boundary and variant patterns
# ---------------------------------------------------------------------------


class TestExtractErrorMessageEdgeCases:
    """Edge cases for _extract_error_message."""

    def test_truncates_to_500_chars(self, restarter):
        """Error message longer than 500 chars is truncated."""
        big_error = "Y" * 600
        result = restarter._extract_error_message(f"Error: {big_error}")
        assert result is not None
        assert len(result) == 500

    def test_exactly_500_chars_not_truncated(self, restarter):
        """Error message of exactly 500 chars is returned as-is."""
        exact_error = "Z" * 500
        result = restarter._extract_error_message(f"Error: {exact_error}")
        assert result is not None
        assert len(result) == 500

    def test_exception_case_insensitive(self, restarter):
        """EXCEPTION: prefix matches case-insensitively."""
        result = restarter._extract_error_message("EXCEPTION: NullPointerException")
        assert result is not None
        assert "NullPointerException" in result

    def test_failed_prefix_extracts_message(self, restarter):
        """Failed: prefix extracts the error message body."""
        result = restarter._extract_error_message("Failed: deployment step 2 timed out")
        assert result == "deployment step 2 timed out"

    def test_no_error_in_normal_output(self, restarter):
        """Normal output without error patterns returns None."""
        output = "All systems nominal. Task completed successfully."
        result = restarter._extract_error_message(output)
        assert result is None

    def test_multiple_error_lines_first_matched(self, restarter):
        """When multiple error patterns are present, first match is returned."""
        output = "Error: first error\nError: second error\n"
        result = restarter._extract_error_message(output)
        assert result is not None
        # First match returned
        assert "first error" in result


# ---------------------------------------------------------------------------
# _check_session_exit_code — additional branches
# ---------------------------------------------------------------------------


class TestCheckSessionExitCodeEdgeCases:
    """Additional branches for _check_session_exit_code."""

    def test_session_exists_with_clean_output(self, restarter):
        """Session with no error indicators returns 0."""
        display_result = MagicMock(returncode=0, stdout="12345")
        with patch("subprocess.run", return_value=display_result):
            with patch.object(restarter, "_capture_session_output", return_value="All good here"):
                code = restarter._check_session_exit_code("forge:clean")
        assert code == 0

    def test_session_exit_code_none_on_value_error(self, restarter):
        """Returns None when an unexpected exception is raised mid-call."""
        with patch("subprocess.run", side_effect=ValueError("unexpected")):
            code = restarter._check_session_exit_code("forge:weird")
        assert code is None

    def test_all_error_patterns_trigger_exit_one(self, restarter):
        """Each of the 6 error patterns returns exit code 1."""
        error_texts = [
            "some error occurred",
            "exception thrown in handler",
            "traceback (most recent call last)",
            "failed to execute command",
            "panic: unexpected nil pointer",
            "exit code 127",
        ]
        display_result = MagicMock(returncode=0, stdout="session_id")
        for text in error_texts:
            with patch("subprocess.run", return_value=display_result):
                with patch.object(restarter, "_capture_session_output", return_value=text):
                    code = restarter._check_session_exit_code("forge:session")
            assert code == 1, f"Expected code 1 for text: {text!r}"

    def test_session_gone_returns_one(self, restarter):
        """Non-zero returncode from display-message means session is gone → 1."""
        result = MagicMock(returncode=1, stdout="")
        with patch("subprocess.run", return_value=result):
            code = restarter._check_session_exit_code("forge:dead")
        assert code == 1


# ---------------------------------------------------------------------------
# recover_task_state — file extension coverage and context calc
# ---------------------------------------------------------------------------


class TestRecoverTaskStateEdgeCases:
    """Additional coverage for recover_task_state."""

    @pytest.mark.parametrize(
        "ext, expected_file",
        [
            ("py", "main_component.py"),
            ("ts", "main_component.ts"),
            # .tsx: regex alternation matches 'ts' before 'tsx', so captured
            # file is 'main_component.ts' (source behaviour — known regex quirk)
            ("tsx", "main_component.ts"),
            ("js", "main_component.js"),
            ("jsx", "main_component.js"),  # 'js' alternation wins over 'jsx'
            ("md", "main_component.md"),
        ],
    )
    def test_file_extensions_all_matched(self, restarter, ext, expected_file):
        """Supported file extensions are captured; documents actual regex behaviour."""
        output = (
            f"Task: Implement the core authentication module\n"
            f"editing: main_component.{ext}\n"
        )
        with patch.object(restarter, "_capture_session_output", return_value=output):
            state = restarter.recover_task_state("forge:session")
        assert state is not None
        assert state.file_being_edited == expected_file

    def test_context_remaining_computed_from_100_percent(self, restarter):
        """100% context usage → 0% remaining."""
        output = "Task: Complete the entire feature backlog\ncontext: 100%\n"
        with patch.object(restarter, "_capture_session_output", return_value=output):
            state = restarter.recover_task_state("forge:full")
        assert state is not None
        assert state.context_remaining == 0

    def test_context_remaining_computed_from_0_percent(self, restarter):
        """0% context usage → 100% remaining."""
        output = "Task: Complete the entire feature backlog\ncontext: 0%\n"
        with patch.object(restarter, "_capture_session_output", return_value=output):
            state = restarter.recover_task_state("forge:empty")
        assert state is not None
        assert state.context_remaining == 100

    def test_no_progress_field_is_none(self, restarter):
        """progress field is None when no progress/step/stage line exists."""
        output = "Task: Implement search feature in the app\n"
        with patch.object(restarter, "_capture_session_output", return_value=output):
            state = restarter.recover_task_state("forge:session")
        assert state is not None
        assert state.progress is None

    def test_progress_extracted_from_stage_keyword(self, restarter):
        """'stage: X' matches progress extraction."""
        output = "Task: Build out the API authentication layer\nstage: step 3\n"
        with patch.object(restarter, "_capture_session_output", return_value=output):
            state = restarter.recover_task_state("forge:session")
        assert state is not None
        assert state.progress == "step 3"

    def test_progress_extracted_from_step_keyword(self, restarter):
        """'step: X' matches progress extraction."""
        output = "Task: Refactor the database connection pool\nstep: migrating tables\n"
        with patch.object(restarter, "_capture_session_output", return_value=output):
            state = restarter.recover_task_state("forge:session")
        assert state is not None
        assert state.progress is not None

    def test_modifying_keyword_matches_file(self, restarter):
        """'modifying: file.py' is captured as file_being_edited."""
        output = "Task: Fix critical bug in payment processing module\nmodifying: payments.py\n"
        with patch.object(restarter, "_capture_session_output", return_value=output):
            state = restarter.recover_task_state("forge:session")
        assert state is not None
        assert state.file_being_edited == "payments.py"


# ---------------------------------------------------------------------------
# restart_agent — edge cases
# ---------------------------------------------------------------------------


class TestRestartAgentEdgeCases:
    """Edge cases for restart_agent."""

    def test_none_recovered_context_handled(self, restarter):
        """None recovered_context produces event with None recovered_task."""
        with patch("subprocess.run", return_value=_make_run_success()):
            event = restarter.restart_agent(
                session_id="forge:gone",
                recovered_context="",
                reason="crashed",
            )
        # Empty string is falsy, so recovered_task should be None or empty string
        # Based on source: `recovered_context[:200] if recovered_context else None`
        assert event.recovered_task is None

    def test_new_session_id_contains_timestamp(self, restarter):
        """new_session_id includes a timestamp suffix."""
        with patch("subprocess.run", return_value=_make_run_success()):
            event = restarter.restart_agent("forge:tech", "Task: do something useful")
        # Format: {session_id}-restart-{timestamp}
        assert "restart" in event.new_session_id

    def test_restart_dir_created(self, restarter, tmp_forge_root):
        """The .forge/restarts directory is created during restart_agent."""
        restart_dir = tmp_forge_root / ".forge/restarts"
        assert not restart_dir.exists()
        with patch("subprocess.run", return_value=_make_run_success()):
            restarter.restart_agent("forge:tech", "Task: important work to complete")
        assert restart_dir.exists()

    def test_event_old_session_id_matches_input(self, restarter):
        """RestartEvent.old_session_id equals the passed session_id."""
        with patch("subprocess.run", return_value=_make_run_success()):
            event = restarter.restart_agent("forge:my-agent", "Task: rebuild the pipeline")
        assert event.old_session_id == "forge:my-agent"

    def test_event_agent_id_equals_new_session_id(self, restarter):
        """event.agent_id equals event.new_session_id."""
        with patch("subprocess.run", return_value=_make_run_success()):
            event = restarter.restart_agent("forge:src", "Task: migration work here")
        assert event.agent_id == event.new_session_id

    def test_custom_reason_propagated(self, restarter):
        """Custom reason string is stored in RestartEvent."""
        with patch("subprocess.run", return_value=_make_run_success()):
            event = restarter.restart_agent("forge:x", "Task: do stuff here", reason="crash_recovery")
        assert event.reason == "crash_recovery"

    def test_context_file_has_reason_field(self, restarter, tmp_forge_root):
        """Context file contains the reason field."""
        with patch("subprocess.run", return_value=_make_run_success()):
            restarter.restart_agent("forge:tech", "Task: fix the pipeline steps", reason="stale_agent")
        restart_dir = tmp_forge_root / ".forge/restarts"
        context_file = next(restart_dir.glob("*.md"))
        content = context_file.read_text()
        assert "stale_agent" in content

    def test_context_file_uses_sanitized_session_name(self, restarter, tmp_forge_root):
        """Colons in session_id are replaced with underscores in filename."""
        with patch("subprocess.run", return_value=_make_run_success()):
            restarter.restart_agent("forge:tech", "Task: implement the auth module fully")
        restart_dir = tmp_forge_root / ".forge/restarts"
        files = list(restart_dir.glob("*.md"))
        assert len(files) == 1
        # Filename should have replaced ':' with '_'
        assert "forge_tech" in files[0].name


# ---------------------------------------------------------------------------
# _log_restart_event — edge cases
# ---------------------------------------------------------------------------


class TestLogRestartEventEdgeCases:
    """Additional edge cases for _log_restart_event."""

    def test_recovered_task_none_stored_as_null(self, restarter, tmp_forge_root):
        """recovered_task=None is serialized as JSON null."""
        event = RestartEvent(
            agent_id="forge:a-new",
            reason="manual",
            recovered_task=None,
            new_session_id="forge:a-new",
            old_session_id="forge:a",
        )
        restarter._log_restart_event(event)
        log_path = tmp_forge_root / ".forge/fleet" / "restart.log"
        data = json.loads(log_path.read_text().strip())
        assert data["recovered_task"] is None

    def test_log_entry_contains_all_required_keys(self, restarter, tmp_forge_root):
        """Log entry contains timestamp, old_session, new_session, reason, recovered_task."""
        event = RestartEvent(
            agent_id="forge:b-new",
            reason="stale_agent",
            recovered_task="Task: finish the job",
            new_session_id="forge:b-new",
            old_session_id="forge:b",
        )
        restarter._log_restart_event(event)
        log_path = tmp_forge_root / ".forge/fleet" / "restart.log"
        data = json.loads(log_path.read_text().strip())
        assert set(data.keys()) == {"timestamp", "old_session", "new_session", "reason", "recovered_task"}

    def test_log_file_grows_with_multiple_events(self, restarter, tmp_forge_root):
        """Log file grows by exactly one line per event."""
        for idx in range(5):
            event = RestartEvent(
                agent_id=f"forge:agent{idx}-new",
                reason="stale_agent",
                recovered_task=None,
                new_session_id=f"forge:agent{idx}-new",
                old_session_id=f"forge:agent{idx}",
            )
            restarter._log_restart_event(event)
        log_path = tmp_forge_root / ".forge/fleet" / "restart.log"
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 5


# ---------------------------------------------------------------------------
# auto_restart_stale — additional branches
# ---------------------------------------------------------------------------


class TestAutoRestartStaleEdgeCases:
    """Additional branches for auto_restart_stale."""

    def test_threshold_minutes_passed_to_detect(self, restarter):
        """threshold_minutes is forwarded to detect_stale_agents."""
        with patch.object(restarter, "detect_stale_agents", return_value=[]) as mock_detect:
            restarter.auto_restart_stale(threshold_minutes=45)
        mock_detect.assert_called_once_with(45)

    def test_no_threshold_passes_none_to_detect(self, restarter):
        """When threshold_minutes is not given, None is passed to detect_stale_agents."""
        with patch.object(restarter, "detect_stale_agents", return_value=[]) as mock_detect:
            restarter.auto_restart_stale()
        mock_detect.assert_called_once_with(None)

    def test_task_state_without_file_still_restarts(self, restarter):
        """TaskState without file_being_edited still produces a valid restart."""
        health = HealthStatus(
            agent_id="forge:stale",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
        )
        task_state = TaskState(
            task_description="Refactor the data pipeline module",
            progress="Step 1",
            file_being_edited=None,
            context_remaining=None,
        )
        fake_event = RestartEvent(
            agent_id="forge:stale-new",
            reason="stale_agent",
            recovered_task="Task: Refactor the data pipeline module",
            new_session_id="forge:stale-new",
            old_session_id="forge:stale",
        )
        with patch.object(restarter, "detect_stale_agents", return_value=[health]):
            with patch.object(restarter, "recover_task_state", return_value=task_state):
                with patch.object(restarter, "restart_agent", return_value=fake_event) as mock_restart:
                    events = restarter.auto_restart_stale()
        assert len(events) == 1
        # file should NOT be in context since file_being_edited is None
        context_arg = mock_restart.call_args[0][1]
        assert "File:" not in context_arg

    def test_unknown_current_task_used_as_fallback(self, restarter):
        """When health.current_task is None and recovery fails, context is 'Unknown'."""
        health = HealthStatus(
            agent_id="forge:stale",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
            current_task=None,
        )
        fake_event = RestartEvent(
            agent_id="forge:stale-new",
            reason="stale_agent",
            recovered_task=None,
            new_session_id="forge:stale-new",
            old_session_id="forge:stale",
        )
        with patch.object(restarter, "detect_stale_agents", return_value=[health]):
            with patch.object(restarter, "recover_task_state", return_value=None):
                with patch.object(restarter, "restart_agent", return_value=fake_event) as mock_restart:
                    restarter.auto_restart_stale()
        context_arg = mock_restart.call_args[0][1]
        assert "Unknown" in context_arg

    def test_reason_passed_as_stale_agent(self, restarter):
        """restart_agent is always called with reason='stale_agent'."""
        health = HealthStatus(
            agent_id="forge:stale",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
        )
        fake_event = RestartEvent(
            agent_id="forge:stale-new",
            reason="stale_agent",
            recovered_task=None,
            new_session_id="forge:stale-new",
        )
        with patch.object(restarter, "detect_stale_agents", return_value=[health]):
            with patch.object(restarter, "recover_task_state", return_value=None):
                with patch.object(restarter, "restart_agent", return_value=fake_event) as mock_restart:
                    restarter.auto_restart_stale()
        _, _, call_kwargs = mock_restart.call_args[0][0], mock_restart.call_args[0][1], mock_restart.call_args
        assert call_kwargs[1].get("reason") == "stale_agent" or call_kwargs[0][2] == "stale_agent"

    def test_context_includes_context_remaining_when_present(self, restarter):
        """recovered_context includes context_remaining when it is set."""
        health = HealthStatus(
            agent_id="forge:stale",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
        )
        task_state = TaskState(
            task_description="Write comprehensive unit tests for module",
            progress=None,
            file_being_edited="tests.py",
            context_remaining=35,
        )
        fake_event = RestartEvent(
            agent_id="forge:stale-new",
            reason="stale_agent",
            recovered_task="...",
            new_session_id="forge:stale-new",
        )
        with patch.object(restarter, "detect_stale_agents", return_value=[health]):
            with patch.object(restarter, "recover_task_state", return_value=task_state):
                with patch.object(restarter, "restart_agent", return_value=fake_event) as mock_restart:
                    restarter.auto_restart_stale()
        context_arg = mock_restart.call_args[0][1]
        assert "35" in context_arg
        assert "tests.py" in context_arg


# ---------------------------------------------------------------------------
# get_restart_history — additional edge cases
# ---------------------------------------------------------------------------


class TestGetRestartHistoryEdgeCases:
    """Additional edge cases for get_restart_history."""

    def test_corrupted_json_line_raises_on_read(self, restarter, tmp_forge_root):
        """JSON parse errors are handled and empty list returned."""
        restarter.restart_log.write_text("NOT VALID JSON\n")
        result = restarter.get_restart_history()
        # json.JSONDecodeError is an Exception, so the error handler returns []
        assert result == []

    def test_limit_one_returns_last_entry(self, restarter, tmp_forge_root):
        """limit=1 returns only the single most recent entry."""
        entries = [
            {"timestamp": f"t{i}", "old_session": f"o{i}", "new_session": f"n{i}",
             "reason": "r", "recovered_task": None}
            for i in range(5)
        ]
        restarter.restart_log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        result = restarter.get_restart_history(limit=1)
        assert len(result) == 1
        assert result[0]["old_session"] == "o4"

    def test_limit_larger_than_log_returns_all(self, restarter, tmp_forge_root):
        """limit larger than available entries returns all entries."""
        entries = [
            {"timestamp": "t", "old_session": f"o{i}", "new_session": f"n{i}",
             "reason": "r", "recovered_task": None}
            for i in range(3)
        ]
        restarter.restart_log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        result = restarter.get_restart_history(limit=100)
        assert len(result) == 3

    def test_empty_log_file_returns_empty_list(self, restarter, tmp_forge_root):
        """Empty log file (exists but no entries) returns empty list."""
        restarter.restart_log.write_text("")
        result = restarter.get_restart_history()
        assert result == []


# ---------------------------------------------------------------------------
# detect_stale_agents — additional edge cases
# ---------------------------------------------------------------------------


class TestDetectStaleAgentsEdgeCases:
    """Additional edge cases for detect_stale_agents."""

    def test_just_under_threshold_not_stale(self, restarter):
        """Agent inactive for just under threshold duration is NOT stale."""
        # 29 minutes 50 seconds — safely under the 30-minute threshold
        session = _make_session("forge:border", seconds_ago=1790)
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            stale = restarter.detect_stale_agents(threshold_minutes=30)
        assert stale == []

    def test_one_second_over_threshold_is_stale(self, restarter):
        """Agent inactive for threshold + 1 second is stale."""
        session = _make_session("forge:overdue", seconds_ago=1801)
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_capture_session_output", return_value="Task: long task here"):
                stale = restarter.detect_stale_agents(threshold_minutes=30)
        assert len(stale) == 1

    def test_context_percent_none_when_not_in_output(self, restarter):
        """context_percent is None when output has no context pattern."""
        session = _make_session("forge:stale", seconds_ago=2000)
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_capture_session_output", return_value="Task: long desc here"):
                stale = restarter.detect_stale_agents(threshold_minutes=30)
        assert stale[0].context_percent is None

    def test_last_activity_preserved_in_health_status(self, restarter):
        """HealthStatus.last_activity matches the session's last_activity."""
        ts = datetime.now(UTC) - timedelta(seconds=2000)
        session = {"name": "forge:stale", "created": ts, "last_activity": ts}
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_capture_session_output", return_value="Task: long task desc"):
                stale = restarter.detect_stale_agents(threshold_minutes=30)
        assert stale[0].last_activity == ts


# ---------------------------------------------------------------------------
# detect_crashed_agents — additional edge cases
# ---------------------------------------------------------------------------


class TestDetectCrashedAgentsEdgeCases:
    """Additional edge cases for detect_crashed_agents."""

    def test_multiple_sessions_only_crashed_returned(self, restarter):
        """When multiple sessions exist, only crashed ones are returned."""
        sessions = [
            _make_session("forge:healthy", seconds_ago=0),
            _make_session("forge:crashed", seconds_ago=0),
            _make_session("forge:unknown", seconds_ago=0),
        ]
        exit_codes = {
            "forge:healthy": 0,
            "forge:crashed": 1,
            "forge:unknown": None,
        }

        def check_exit(session_id):
            return exit_codes[session_id]

        with patch.object(restarter, "_get_session_list", return_value=sessions):
            with patch.object(restarter, "_check_session_exit_code", side_effect=check_exit):
                with patch.object(restarter, "_capture_session_output", return_value="Error: crashed"):
                    crashed = restarter.detect_crashed_agents()
        assert len(crashed) == 1
        assert crashed[0].agent_id == "forge:crashed"

    def test_crashed_agent_has_correct_status(self, restarter):
        """Crashed agent HealthStatus has status='crashed'."""
        session = _make_session("forge:broken")
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_check_session_exit_code", return_value=1):
                with patch.object(restarter, "_capture_session_output", return_value="Traceback error"):
                    crashed = restarter.detect_crashed_agents()
        assert crashed[0].status == "crashed"

    def test_current_task_extracted_for_crashed_agent(self, restarter):
        """current_task is extracted even for crashed agents."""
        session = _make_session("forge:broken")
        output = "Task: implement core authentication\nError: memory error"
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_check_session_exit_code", return_value=1):
                with patch.object(restarter, "_capture_session_output", return_value=output):
                    crashed = restarter.detect_crashed_agents()
        assert crashed[0].current_task is not None


# ---------------------------------------------------------------------------
# AgentRestarter init — auto-discovery walk
# ---------------------------------------------------------------------------


class TestAgentRestarterAutoDiscovery:
    """Tests for the forge_root auto-discovery path walk."""

    def test_discovers_forge_fleet_in_parent(self, tmp_path):
        """If .forge/fleet is in parent dir, it is discovered."""
        (tmp_path / ".forge/fleet").mkdir()
        child = tmp_path / "child_dir"
        child.mkdir()
        with patch("forge_harness.fleet.restart.Path.cwd", return_value=child):
            r = AgentRestarter(forge_root=None)
        assert r.forge_root == tmp_path

    def test_falls_back_to_cwd_when_no_forge_fleet_found(self, tmp_path):
        """When .forge/fleet is never found, forge_root defaults to cwd."""
        with patch("forge_harness.fleet.restart.Path.cwd", return_value=tmp_path):
            r = AgentRestarter(forge_root=None)
        # Should not raise; fleet_dir is created
        assert (r.forge_root / ".forge/fleet").exists()


# ---------------------------------------------------------------------------
# create_agent_restarter factory
# ---------------------------------------------------------------------------


class TestCreateAgentRestarterExtended:
    """Additional tests for create_agent_restarter factory."""

    def test_fleet_dir_created_by_factory(self, tmp_path):
        """Fleet directory is created when using factory with a new tmp_path."""
        r = create_agent_restarter(forge_root=tmp_path, stale_threshold_minutes=15)
        assert (tmp_path / ".forge/fleet").exists()
        assert r.stale_threshold == timedelta(minutes=15)

    def test_factory_restart_log_path(self, tmp_path):
        """restart_log path is correct via factory function."""
        r = create_agent_restarter(forge_root=tmp_path)
        assert r.restart_log == tmp_path / ".forge/fleet" / "restart.log"
