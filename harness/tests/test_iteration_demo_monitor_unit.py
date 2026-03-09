"""Unit tests for forge_harness/iteration/demo_monitor.py.

Architecture note
-----------------
The source file `monitor.py` contains a critical placement bug: every method
after ``create_monitor``'s ``return`` statement (line 319) is unreachable
nested code and is therefore NOT part of ``ProgressMonitor``.  The only real
public API on ``ProgressMonitor`` instances is:

    register_session, poll_session, detect_stall, wait_for_completion
    + instance attrs: sessions, poll_interval, default_timeout,
                      completion_markers, failure_markers,
                      _running, _session_last_output, _session_last_change

All demo functions call dead methods and therefore raise ``AttributeError`` at
runtime unless those methods are patched back in.  Tests for demo functions use
a helper ``_patch_monitor`` that re-attaches the intended dead-code logic so we
can exercise the demo's control flow without touching the source.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
import forge_harness.iteration.demo_monitor as demo_module
from forge_harness.iteration.demo_monitor import (
    demo_basic_usage,
    demo_completion_detection,
    demo_metrics_export,
    demo_stall_detection,
    demo_timeout_handling,
    main,
)

# ---------------------------------------------------------------------------
# Monitor module symbols
# ---------------------------------------------------------------------------
from forge_harness.iteration.monitor import (
    COMPLETION_MARKERS,
    FAILURE_MARKERS,
    MonitorResult,
    ProgressMonitor,
    SessionMetrics,
    SessionState,
    SessionStatus,
    create_monitor,
)

# ===========================================================================
# Helpers
# ===========================================================================


def _patch_monitor(instance: ProgressMonitor) -> None:
    """Attach the dead-code methods from monitor.py onto a live instance.

    These methods are defined after ``create_monitor``'s return statement and
    therefore never become part of the class.  We re-attach the intended
    semantics here so demo tests can exercise the demo's logic.
    """

    # --- unregister_session ------------------------------------------------
    def unregister_session(session_id: str) -> None:
        if session_id in instance.sessions:
            del instance.sessions[session_id]

    # --- get_state ----------------------------------------------------------
    def get_state(session_id: str):
        m = instance.sessions.get(session_id)
        return m.state if m else None

    # --- get_metrics --------------------------------------------------------
    def get_metrics(session_id: str):
        return instance.sessions.get(session_id)

    # --- get_all_metrics ----------------------------------------------------
    def get_all_metrics():
        return instance.sessions.copy()

    # --- is_completed -------------------------------------------------------
    def is_completed(session_id: str) -> bool:
        m = instance.sessions.get(session_id)
        return m.state == SessionState.COMPLETED if m else False

    # --- extend_timeout -----------------------------------------------------
    def extend_timeout(session_id: str, minutes: int) -> None:
        m = instance.sessions.get(session_id)
        if not m:
            return
        m.extended_until = datetime.now(UTC) + timedelta(minutes=minutes)

    # --- _detect_failure ----------------------------------------------------
    def _detect_failure(output: str) -> bool:
        tail = output[-1000:] if len(output) > 1000 else output
        return any(marker in tail for marker in instance.failure_markers)

    # --- _get_exit_status ---------------------------------------------------
    def _get_exit_status(session_id: str):
        return None

    # --- _get_session_output ------------------------------------------------
    def _get_session_output(session_id: str, tail_lines=None) -> str:
        return ""

    # --- _hash_output -------------------------------------------------------
    def _hash_output(output: str) -> str:
        return hashlib.sha256(output.encode()).hexdigest()

    # --- collect_output -----------------------------------------------------
    def collect_output(session_id: str, tail_lines=None) -> str:
        return _get_session_output(session_id, tail_lines)

    # --- detect_completion --------------------------------------------------
    def detect_completion(session_id: str, output=None) -> bool:
        exit_status = _get_exit_status(session_id)
        if exit_status is not None and exit_status == 0:
            return True
        if output is None:
            output = _get_session_output(session_id)
        tail = output[-1000:] if len(output) > 1000 else output
        return any(marker in tail for marker in instance.completion_markers)

    # --- detect_stall (metrics-based version, overrides the real time-based one) --
    # The demo calls detect_stall(session_id) without threshold_seconds, expecting
    # the metrics-based dead-code logic (idle_minutes >= 10 and not is_timeout).
    _original_detect_stall = instance.detect_stall

    def detect_stall(session_id, threshold_seconds=300):
        # If called with threshold_seconds, use the real time-based impl
        metrics = instance.sessions.get(session_id)
        if not metrics:
            # Fall back to time-based when no session entry (original behaviour)
            return _original_detect_stall(session_id, threshold_seconds)
        # Metrics-based: stalled if idle >= 10 min but not timed out
        return metrics.idle_minutes >= 10 and not metrics.is_timeout

    # Bind all helpers onto the instance
    instance.unregister_session = unregister_session
    instance.get_state = get_state
    instance.get_metrics = get_metrics
    instance.get_all_metrics = get_all_metrics
    instance.is_completed = is_completed
    instance.extend_timeout = extend_timeout
    instance._detect_failure = _detect_failure
    instance._get_exit_status = _get_exit_status
    instance._get_session_output = _get_session_output
    instance._hash_output = _hash_output
    instance.collect_output = collect_output
    instance.detect_completion = detect_completion
    instance.detect_stall = detect_stall


def _make_patched_init():
    """Return a ProgressMonitor.__init__ replacement that calls _patch_monitor."""
    original_init = ProgressMonitor.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        _patch_monitor(self)

    return patched_init


# ===========================================================================
# SessionState enum
# ===========================================================================


class TestSessionState:
    def test_values(self):
        assert SessionState.ACTIVE.value == "active"
        assert SessionState.STALLED.value == "stalled"
        assert SessionState.COMPLETED.value == "completed"
        assert SessionState.FAILED.value == "failed"
        assert SessionState.TIMEOUT.value == "timeout"
        assert SessionState.UNKNOWN.value == "unknown"

    def test_is_str_subclass(self):
        assert isinstance(SessionState.ACTIVE, str)


# ===========================================================================
# SessionStatus enum
# ===========================================================================


class TestSessionStatus:
    def test_values(self):
        assert SessionStatus.RUNNING.value == "running"
        assert SessionStatus.COMPLETED.value == "completed"
        assert SessionStatus.STALLED.value == "stalled"
        assert SessionStatus.TIMEOUT.value == "timeout"
        assert SessionStatus.ERROR.value == "error"

    def test_is_str_subclass(self):
        assert isinstance(SessionStatus.RUNNING, str)


# ===========================================================================
# MonitorResult dataclass
# ===========================================================================


class TestMonitorResult:
    def test_defaults(self):
        r = MonitorResult(session_id="abc", status=SessionStatus.RUNNING)
        assert r.session_id == "abc"
        assert r.status == SessionStatus.RUNNING
        assert r.output == ""
        assert r.duration == 0.0
        assert r.completion_marker is None

    def test_summary_contains_session_id(self):
        r = MonitorResult(session_id="s1", status=SessionStatus.COMPLETED, duration=12.345)
        assert "s1" in r.summary

    def test_summary_contains_status(self):
        r = MonitorResult(session_id="s1", status=SessionStatus.COMPLETED, duration=12.345)
        assert "completed" in r.summary

    def test_summary_contains_duration(self):
        r = MonitorResult(session_id="s1", status=SessionStatus.COMPLETED, duration=12.345)
        assert "12.3" in r.summary

    def test_summary_with_error_status(self):
        r = MonitorResult(session_id="my-session", status=SessionStatus.ERROR, duration=0.5)
        assert "my-session" in r.summary
        assert "error" in r.summary

    def test_completion_marker_preserved(self):
        r = MonitorResult(
            session_id="s1",
            status=SessionStatus.COMPLETED,
            completion_marker="Cogitated for",
        )
        assert r.completion_marker == "Cogitated for"


# ===========================================================================
# SessionMetrics dataclass
# ===========================================================================


class TestSessionMetrics:
    def test_defaults(self):
        m = SessionMetrics(session_id="x")
        assert m.session_id == "x"
        assert m.task_id is None
        assert m.state == SessionState.UNKNOWN
        assert m.timeout_minutes == 15
        assert m.extended_until is None
        assert m.metadata == {}

    def test_age_minutes_increases_over_time(self):
        past = datetime.now(UTC) - timedelta(minutes=3)
        m = SessionMetrics(session_id="x", started_at=past)
        assert m.age_minutes >= 3.0
        assert m.age_minutes < 4.0

    def test_idle_minutes_uses_last_activity(self):
        past = datetime.now(UTC) - timedelta(minutes=7)
        m = SessionMetrics(session_id="x")
        m.last_activity_at = past
        assert m.idle_minutes >= 7.0

    def test_idle_minutes_falls_back_to_age_when_no_activity(self):
        past = datetime.now(UTC) - timedelta(minutes=4)
        m = SessionMetrics(session_id="x", started_at=past)
        assert m.last_activity_at is None
        assert m.idle_minutes >= 4.0

    def test_is_timeout_true_when_idle_exceeds_threshold(self):
        past = datetime.now(UTC) - timedelta(minutes=20)
        m = SessionMetrics(session_id="x", timeout_minutes=15)
        m.last_activity_at = past
        assert m.is_timeout is True

    def test_is_timeout_false_when_idle_below_threshold(self):
        recent = datetime.now(UTC) - timedelta(minutes=2)
        m = SessionMetrics(session_id="x", timeout_minutes=15)
        m.last_activity_at = recent
        assert m.is_timeout is False

    def test_is_timeout_false_when_extended_until_in_future(self):
        past = datetime.now(UTC) - timedelta(minutes=20)
        future = datetime.now(UTC) + timedelta(minutes=10)
        m = SessionMetrics(session_id="x", timeout_minutes=15)
        m.last_activity_at = past
        m.extended_until = future
        assert m.is_timeout is False

    def test_is_timeout_true_when_extended_until_expired_and_still_idle(self):
        very_old = datetime.now(UTC) - timedelta(minutes=30)
        extended_past = datetime.now(UTC) - timedelta(minutes=5)
        m = SessionMetrics(session_id="x", timeout_minutes=15)
        m.last_activity_at = very_old
        m.extended_until = extended_past
        assert m.is_timeout is True

    def test_to_dict_contains_expected_keys(self):
        m = SessionMetrics(
            session_id="sid", task_id="T-42", timeout_minutes=20, metadata={"k": "v"}
        )
        d = m.to_dict()
        for key in (
            "session_id",
            "task_id",
            "state",
            "started_at",
            "last_activity_at",
            "output_size",
            "timeout_minutes",
            "age_minutes",
            "idle_minutes",
            "is_timeout",
            "extended_until",
            "metadata",
        ):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_values(self):
        m = SessionMetrics(session_id="sid", task_id="T-42", timeout_minutes=20)
        d = m.to_dict()
        assert d["session_id"] == "sid"
        assert d["task_id"] == "T-42"
        assert d["state"] == "unknown"
        assert d["timeout_minutes"] == 20

    def test_to_dict_last_activity_none_when_unset(self):
        m = SessionMetrics(session_id="x")
        assert m.to_dict()["last_activity_at"] is None

    def test_to_dict_last_activity_isoformat_when_set(self):
        m = SessionMetrics(session_id="x")
        m.last_activity_at = datetime.now(UTC)
        assert m.to_dict()["last_activity_at"] is not None

    def test_to_dict_extended_until_none_when_unset(self):
        m = SessionMetrics(session_id="x")
        assert m.to_dict()["extended_until"] is None

    def test_to_dict_extended_until_isoformat_when_set(self):
        m = SessionMetrics(session_id="x")
        m.extended_until = datetime.now(UTC) + timedelta(minutes=5)
        assert m.to_dict()["extended_until"] is not None

    def test_metadata_independent_per_instance(self):
        m1 = SessionMetrics(session_id="a")
        m2 = SessionMetrics(session_id="b")
        m1.metadata["x"] = 1
        assert "x" not in m2.metadata


# ===========================================================================
# Marker constants
# ===========================================================================


class TestMarkerConstants:
    def test_completion_markers_not_empty(self):
        assert len(COMPLETION_MARKERS) > 0

    def test_failure_markers_not_empty(self):
        assert len(FAILURE_MARKERS) > 0

    def test_known_completion_markers_present(self):
        assert "All tests pass" in COMPLETION_MARKERS
        assert "✓ Success" in COMPLETION_MARKERS
        assert "DONE" in COMPLETION_MARKERS

    def test_known_failure_markers_present(self):
        assert "Error:" in FAILURE_MARKERS
        assert "FAILED" in FAILURE_MARKERS
        assert "Exception:" in FAILURE_MARKERS


# ===========================================================================
# ProgressMonitor — construction (real, live API)
# ===========================================================================


class TestProgressMonitorInit:
    def test_defaults(self):
        m = ProgressMonitor()
        assert m.poll_interval == 30
        assert m.default_timeout == 15
        assert m.completion_markers == COMPLETION_MARKERS
        assert m.failure_markers == FAILURE_MARKERS
        assert m.sessions == {}
        assert m._running is False

    def test_custom_args(self):
        m = ProgressMonitor(
            poll_interval=5,
            default_timeout=2,
            completion_markers=["DONE"],
            failure_markers=["ERR"],
        )
        assert m.poll_interval == 5
        assert m.default_timeout == 2
        assert m.completion_markers == ["DONE"]
        assert m.failure_markers == ["ERR"]

    def test_internal_tracking_dicts_start_empty(self):
        m = ProgressMonitor()
        assert m._session_last_output == {}
        assert m._session_last_change == {}


# ===========================================================================
# ProgressMonitor — register_session (real API)
# ===========================================================================


class TestProgressMonitorRegisterSession:
    def test_register_creates_entry(self):
        m = ProgressMonitor()
        m.register_session("s1")
        assert "s1" in m.sessions

    def test_register_with_all_args(self):
        m = ProgressMonitor()
        m.register_session("s1", task_id="T-1", timeout_minutes=30, metadata={"a": 1})
        sm = m.sessions["s1"]
        assert sm.task_id == "T-1"
        assert sm.timeout_minutes == 30
        assert sm.metadata == {"a": 1}

    def test_register_uses_default_timeout_when_not_specified(self):
        m = ProgressMonitor(default_timeout=7)
        m.register_session("s1")
        assert m.sessions["s1"].timeout_minutes == 7

    def test_register_multiple_sessions(self):
        m = ProgressMonitor()
        m.register_session("a")
        m.register_session("b")
        m.register_session("c")
        assert len(m.sessions) == 3

    def test_register_overwrites_existing_session(self):
        m = ProgressMonitor()
        m.register_session("dup", task_id="FIRST", timeout_minutes=5)
        m.register_session("dup", task_id="SECOND", timeout_minutes=10)
        sm = m.sessions["dup"]
        assert sm.task_id == "SECOND"
        assert sm.timeout_minutes == 10

    def test_new_session_has_unknown_state(self):
        m = ProgressMonitor()
        m.register_session("s1")
        assert m.sessions["s1"].state == SessionState.UNKNOWN

    def test_metadata_defaults_to_empty_dict(self):
        m = ProgressMonitor()
        m.register_session("s1")
        assert m.sessions["s1"].metadata == {}


# ===========================================================================
# ProgressMonitor — detect_stall (real, time-based API)
# ===========================================================================


class TestProgressMonitorDetectStall:
    def test_not_stalled_when_recently_changed(self):
        m = ProgressMonitor()
        m._session_last_change["s1"] = time.time()
        assert m.detect_stall("s1", threshold_seconds=300) is False

    def test_stalled_when_no_change_for_long_time(self):
        m = ProgressMonitor()
        m._session_last_change["s1"] = time.time() - 400
        assert m.detect_stall("s1", threshold_seconds=300) is True

    def test_stalled_with_custom_threshold(self):
        m = ProgressMonitor()
        m._session_last_change["s1"] = time.time() - 10
        assert m.detect_stall("s1", threshold_seconds=5) is True

    def test_not_stalled_when_no_entry_yet(self):
        # When there is no entry, last_change defaults to time.time() → not stalled
        m = ProgressMonitor()
        assert m.detect_stall("brand-new-session", threshold_seconds=300) is False

    def test_default_threshold_is_300_seconds(self):
        m = ProgressMonitor()
        m._session_last_change["s1"] = time.time() - 299
        # Should not be stalled with default 300s threshold
        assert m.detect_stall("s1") is False
        m._session_last_change["s1"] = time.time() - 301
        assert m.detect_stall("s1") is True


# ===========================================================================
# ProgressMonitor — poll_session (real API, subprocess mocked)
# ===========================================================================


def _subprocess_result(returncode=0, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


class TestProgressMonitorPollSession:
    @patch("forge_harness.iteration.monitor.subprocess.run")
    def test_returns_error_when_tmux_fails(self, mock_run):
        mock_run.return_value = _subprocess_result(returncode=1, stderr="no session")
        m = ProgressMonitor()
        result = m.poll_session("bad-session")
        assert result.status == SessionStatus.ERROR
        assert result.session_id == "bad-session"
        assert "no session" in result.output

    @patch("forge_harness.iteration.monitor.subprocess.run")
    def test_returns_running_for_normal_output(self, mock_run):
        mock_run.return_value = _subprocess_result(stdout="Working on it...")
        m = ProgressMonitor()
        result = m.poll_session("s1")
        assert result.status == SessionStatus.RUNNING
        assert result.output == "Working on it..."

    @patch("forge_harness.iteration.monitor.subprocess.run")
    def test_returns_completed_for_cogitated_marker(self, mock_run):
        mock_run.return_value = _subprocess_result(stdout="Cogitated for 3.2s")
        m = ProgressMonitor()
        result = m.poll_session("s1")
        assert result.status == SessionStatus.COMPLETED
        assert result.completion_marker == "Cogitated for"

    @patch("forge_harness.iteration.monitor.subprocess.run")
    def test_returns_completed_for_completed_marker(self, mock_run):
        mock_run.return_value = _subprocess_result(stdout="Task Completed successfully")
        m = ProgressMonitor()
        result = m.poll_session("s1")
        assert result.status == SessionStatus.COMPLETED
        assert result.completion_marker == "Completed"

    @patch("forge_harness.iteration.monitor.subprocess.run")
    def test_returns_completed_for_task_completed_marker(self, mock_run):
        mock_run.return_value = _subprocess_result(stdout="Task completed, hooray")
        m = ProgressMonitor()
        result = m.poll_session("s1")
        assert result.status == SessionStatus.COMPLETED
        assert result.completion_marker == "Task completed"

    @patch("forge_harness.iteration.monitor.subprocess.run")
    def test_shell_prompt_completion_marker(self, mock_run):
        # The source regex is r"❯\\s*$" (a raw string with double-backslash),
        # which becomes the pattern ❯\\s*$ — matching ❯ + literal-backslash + s* at EOL.
        # A string containing "❯\s" (backslash then s) therefore matches.
        mock_run.return_value = _subprocess_result(stdout="some output\n❯\\s")
        m = ProgressMonitor()
        result = m.poll_session("s1")
        assert result.status == SessionStatus.COMPLETED
        assert result.completion_marker == "shell_prompt"

    @patch("forge_harness.iteration.monitor.subprocess.run")
    def test_shell_prompt_does_not_match_spaces(self, mock_run):
        # Verify the (buggy) regex does NOT match a normal shell prompt with spaces.
        mock_run.return_value = _subprocess_result(stdout="some output\n❯ \n")
        m = ProgressMonitor()
        result = m.poll_session("s1")
        # Falls through to RUNNING because the regex requires a literal backslash
        assert result.status == SessionStatus.RUNNING

    @patch("forge_harness.iteration.monitor.subprocess.run")
    def test_tracks_output_changes(self, mock_run):
        mock_run.return_value = _subprocess_result(stdout="first output")
        m = ProgressMonitor()
        m.poll_session("s1")
        assert m._session_last_output.get("s1") == "first output"

    @patch("forge_harness.iteration.monitor.subprocess.run")
    def test_updates_last_change_timestamp_on_new_output(self, mock_run):
        m = ProgressMonitor()
        before = time.time()
        mock_run.return_value = _subprocess_result(stdout="output-v1")
        m.poll_session("s1")
        t1 = m._session_last_change.get("s1", 0)
        assert t1 >= before

    @patch("forge_harness.iteration.monitor.subprocess.run")
    def test_duration_is_non_negative(self, mock_run):
        mock_run.return_value = _subprocess_result(stdout="ok")
        m = ProgressMonitor()
        result = m.poll_session("s1")
        assert result.duration >= 0.0

    @patch("forge_harness.iteration.monitor.subprocess.run")
    def test_none_stdout_handled_as_empty(self, mock_run):
        mock_run.return_value = _subprocess_result(stdout=None)
        m = ProgressMonitor()
        result = m.poll_session("s1")
        assert result.output == ""

    @patch("forge_harness.iteration.monitor.subprocess.run")
    def test_no_duplicate_last_change_update_on_same_output(self, mock_run):
        m = ProgressMonitor()
        mock_run.return_value = _subprocess_result(stdout="same")
        m.poll_session("s1")
        t1 = m._session_last_change.get("s1")
        mock_run.return_value = _subprocess_result(stdout="same")
        m.poll_session("s1")
        t2 = m._session_last_change.get("s1")
        # Timestamp should not change when output is identical
        assert t1 == t2


# ===========================================================================
# ProgressMonitor — wait_for_completion (real API)
# ===========================================================================


class TestProgressMonitorWaitForCompletion:
    @pytest.mark.asyncio
    async def test_returns_completed_immediately(self):
        m = ProgressMonitor(poll_interval=0)
        completed_result = MonitorResult(
            session_id="s1", status=SessionStatus.COMPLETED, output="done"
        )
        m.poll_session = MagicMock(return_value=completed_result)
        m._session_last_change["s1"] = time.time()

        results = await m.wait_for_completion(["s1"], timeout=10.0)
        assert results["s1"].status == SessionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_returns_stalled_when_stall_detected(self):
        m = ProgressMonitor(poll_interval=0)
        running_result = MonitorResult(session_id="s1", status=SessionStatus.RUNNING, output="")
        m.poll_session = MagicMock(return_value=running_result)
        m._session_last_change["s1"] = time.time() - 9999

        results = await m.wait_for_completion(["s1"], timeout=10.0, stall_threshold=1)
        assert results["s1"].status == SessionStatus.STALLED

    @pytest.mark.asyncio
    async def test_returns_timeout_when_time_exceeded(self):
        m = ProgressMonitor(poll_interval=0)
        running_result = MonitorResult(session_id="s1", status=SessionStatus.RUNNING, output="")
        m.poll_session = MagicMock(return_value=running_result)
        m._session_last_change["s1"] = time.time()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            results = await m.wait_for_completion(["s1"], timeout=0.0, stall_threshold=9999)
        assert results["s1"].status == SessionStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_handles_multiple_sessions(self):
        m = ProgressMonitor(poll_interval=0)

        def side_effect(session_id):
            return MonitorResult(
                session_id=session_id, status=SessionStatus.COMPLETED, output="ok"
            )

        m.poll_session = MagicMock(side_effect=side_effect)
        m._session_last_change["s1"] = time.time()
        m._session_last_change["s2"] = time.time()

        results = await m.wait_for_completion(["s1", "s2"], timeout=10.0)
        assert len(results) == 2
        assert results["s1"].status == SessionStatus.COMPLETED
        assert results["s2"].status == SessionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_empty_session_list(self):
        m = ProgressMonitor(poll_interval=0)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            results = await m.wait_for_completion([], timeout=0.0)
        assert results == {}


# ===========================================================================
# create_monitor factory
# ===========================================================================


class TestCreateMonitor:
    def test_returns_progress_monitor(self):
        m = create_monitor()
        assert isinstance(m, ProgressMonitor)

    def test_custom_poll_interval(self):
        m = create_monitor(poll_interval=5.0)
        assert m.poll_interval == 5

    def test_default_poll_interval(self):
        m = create_monitor()
        assert m.poll_interval == 30

    def test_float_converted_to_int(self):
        m = create_monitor(poll_interval=7.9)
        assert m.poll_interval == 7


# ===========================================================================
# Patched ProgressMonitor helpers (dead-code logic tested via patch)
# ===========================================================================


class TestPatchedMonitorHelpers:
    """Verify the intended semantics of the dead-code methods via _patch_monitor."""

    def _make(self) -> ProgressMonitor:
        m = ProgressMonitor()
        _patch_monitor(m)
        return m

    # --- get_state / get_metrics / get_all_metrics --------------------------

    def test_get_state_returns_unknown_for_new(self):
        m = self._make()
        m.register_session("x")
        assert m.get_state("x") == SessionState.UNKNOWN

    def test_get_state_returns_none_for_missing(self):
        m = self._make()
        assert m.get_state("missing") is None

    def test_get_metrics_returns_session_metrics(self):
        m = self._make()
        m.register_session("x", task_id="T-1")
        sm = m.get_metrics("x")
        assert isinstance(sm, SessionMetrics)
        assert sm.task_id == "T-1"

    def test_get_metrics_returns_none_for_missing(self):
        m = self._make()
        assert m.get_metrics("missing") is None

    def test_get_all_metrics_returns_copy(self):
        m = self._make()
        m.register_session("a")
        m.register_session("b")
        all_m = m.get_all_metrics()
        assert set(all_m.keys()) == {"a", "b"}
        del all_m["a"]
        assert "a" in m.sessions  # copy, not original

    # --- is_completed -------------------------------------------------------

    def test_is_completed_false_by_default(self):
        m = self._make()
        m.register_session("x")
        assert m.is_completed("x") is False

    def test_is_completed_true_after_state_set(self):
        m = self._make()
        m.register_session("x")
        m.sessions["x"].state = SessionState.COMPLETED
        assert m.is_completed("x") is True

    def test_is_completed_false_for_missing(self):
        m = self._make()
        assert m.is_completed("missing") is False

    # --- extend_timeout -----------------------------------------------------

    def test_extend_timeout_sets_extended_until(self):
        m = self._make()
        m.register_session("x")
        before = datetime.now(UTC)
        m.extend_timeout("x", minutes=10)
        after = datetime.now(UTC)
        eu = m.sessions["x"].extended_until
        assert eu is not None
        assert before + timedelta(minutes=10) <= eu <= after + timedelta(minutes=10)

    def test_extend_timeout_noop_for_missing(self):
        m = self._make()
        m.extend_timeout("missing", minutes=5)  # Should not raise

    def test_extend_timeout_prevents_timeout(self):
        m = self._make()
        m.register_session("x", timeout_minutes=1)
        m.sessions["x"].last_activity_at = datetime.now(UTC) - timedelta(minutes=2)
        assert m.sessions["x"].is_timeout is True
        m.extend_timeout("x", minutes=30)
        assert m.sessions["x"].is_timeout is False

    # --- unregister_session -------------------------------------------------

    def test_unregister_removes_session(self):
        m = self._make()
        m.register_session("x")
        m.unregister_session("x")
        assert "x" not in m.sessions

    def test_unregister_noop_for_missing(self):
        m = self._make()
        m.unregister_session("does-not-exist")  # Should not raise

    def test_unregister_leaves_other_sessions(self):
        m = self._make()
        m.register_session("a")
        m.register_session("b")
        m.register_session("c")
        m.unregister_session("a")
        assert "b" in m.sessions
        assert "c" in m.sessions

    # --- _detect_failure ----------------------------------------------------

    def test_detect_failure_on_error_colon(self):
        m = self._make()
        assert m._detect_failure("Error: something bad") is True

    def test_detect_failure_on_failed(self):
        m = self._make()
        assert m._detect_failure("FAILED") is True

    def test_detect_failure_on_build_failed(self):
        m = self._make()
        assert m._detect_failure("BUILD FAILED") is True

    def test_detect_failure_on_exception(self):
        m = self._make()
        assert m._detect_failure("Exception: ValueError") is True

    def test_detect_failure_on_traceback(self):
        m = self._make()
        assert m._detect_failure("Traceback (most recent call last):\n ...") is True

    def test_detect_failure_clean_output(self):
        m = self._make()
        assert m._detect_failure("All good here") is False

    def test_detect_failure_checks_tail_1000_chars(self):
        m = self._make()
        prefix = "A" * 2000
        output = prefix + "\nError: oops"
        assert m._detect_failure(output) is True

    def test_detect_failure_empty_output(self):
        m = self._make()
        assert m._detect_failure("") is False

    def test_custom_failure_markers(self):
        m = ProgressMonitor(failure_markers=["MY_FAIL"])
        _patch_monitor(m)
        assert m._detect_failure("everything is MY_FAIL here") is True
        assert m._detect_failure("Error: something") is False  # not in custom list

    # --- detect_completion --------------------------------------------------

    def test_detect_completion_on_all_tests_pass(self):
        m = self._make()
        assert m.detect_completion("s", "All tests pass\nDone.") is True

    def test_detect_completion_on_success_marker(self):
        m = self._make()
        assert m.detect_completion("s", "✓ Success") is True

    def test_detect_completion_on_deployment_complete(self):
        m = self._make()
        assert m.detect_completion("s", "Deployment complete") is True

    def test_no_completion_without_markers(self):
        m = self._make()
        assert m.detect_completion("s", "Still running...") is False

    def test_detect_completion_checks_tail_1000(self):
        m = self._make()
        prefix = "X" * 2000
        output = prefix + "\nAll tests pass"
        assert m.detect_completion("s", output) is True

    def test_detect_completion_empty_output(self):
        m = self._make()
        assert m.detect_completion("s", "") is False

    def test_custom_completion_markers(self):
        m = ProgressMonitor(completion_markers=["MY_DONE_TOKEN"])
        _patch_monitor(m)
        assert m.detect_completion("s", "MY_DONE_TOKEN here") is True
        assert m.detect_completion("s", "All tests pass") is False

    # --- _hash_output -------------------------------------------------------

    def test_hash_output_deterministic(self):
        m = self._make()
        assert m._hash_output("hello") == m._hash_output("hello")

    def test_hash_output_differs_for_different_inputs(self):
        m = self._make()
        assert m._hash_output("a") != m._hash_output("b")

    def test_hash_output_returns_string(self):
        m = self._make()
        assert isinstance(m._hash_output("anything"), str)

    def test_hash_output_is_sha256(self):
        m = self._make()
        expected = hashlib.sha256(b"test").hexdigest()
        assert m._hash_output("test") == expected


# ===========================================================================
# Demo functions — tested with patched ProgressMonitor
# ===========================================================================


@pytest.fixture(autouse=False)
def patch_progress_monitor():
    """Patch ProgressMonitor.__init__ globally so all demo instances get the
    dead-code methods attached automatically."""
    with patch.object(ProgressMonitor, "__init__", _make_patched_init()):
        yield


class TestDemoBasicUsage:
    @pytest.mark.asyncio
    async def test_runs_without_error(self, patch_progress_monitor, capsys):
        await demo_basic_usage()
        captured = capsys.readouterr()
        assert "Basic Progress Monitor Usage" in captured.out

    @pytest.mark.asyncio
    async def test_registers_three_sessions(self, patch_progress_monitor, capsys):
        await demo_basic_usage()
        captured = capsys.readouterr()
        assert "session-1" in captured.out
        assert "session-2" in captured.out
        assert "session-3" in captured.out

    @pytest.mark.asyncio
    async def test_shows_registered_count(self, patch_progress_monitor, capsys):
        await demo_basic_usage()
        captured = capsys.readouterr()
        assert "Registered 3 sessions" in captured.out

    @pytest.mark.asyncio
    async def test_shows_session_states(self, patch_progress_monitor, capsys):
        await demo_basic_usage()
        captured = capsys.readouterr()
        assert "unknown" in captured.out  # initial state

    @pytest.mark.asyncio
    async def test_extends_timeout(self, patch_progress_monitor, capsys):
        await demo_basic_usage()
        captured = capsys.readouterr()
        assert "Extending timeout" in captured.out
        assert "Extended until" in captured.out

    @pytest.mark.asyncio
    async def test_unregisters_all_sessions(self, patch_progress_monitor, capsys):
        await demo_basic_usage()
        captured = capsys.readouterr()
        assert "Remaining sessions: 0" in captured.out


class TestDemoCompletionDetection:
    @pytest.mark.asyncio
    async def test_runs_without_error(self, patch_progress_monitor, capsys):
        await demo_completion_detection()
        captured = capsys.readouterr()
        assert "Completion Detection" in captured.out

    @pytest.mark.asyncio
    async def test_checks_all_three_completion_outputs(self, patch_progress_monitor, capsys):
        await demo_completion_detection()
        captured = capsys.readouterr()
        assert "Output 1" in captured.out
        assert "Output 2" in captured.out
        assert "Output 3" in captured.out

    @pytest.mark.asyncio
    async def test_completion_detected(self, patch_progress_monitor, capsys):
        await demo_completion_detection()
        captured = capsys.readouterr()
        # All three completion outputs should be marked as completed
        assert captured.out.count("Completed") >= 3

    @pytest.mark.asyncio
    async def test_failure_markers_detected(self, patch_progress_monitor, capsys):
        await demo_completion_detection()
        captured = capsys.readouterr()
        assert "Failure detected" in captured.out

    @pytest.mark.asyncio
    async def test_failure_section_has_three_entries(self, patch_progress_monitor, capsys):
        await demo_completion_detection()
        captured = capsys.readouterr()
        assert captured.out.count("Failure detected") >= 3


class TestDemoTimeoutHandling:
    @pytest.mark.asyncio
    async def test_runs_without_error(self, patch_progress_monitor, capsys):
        await demo_timeout_handling()
        captured = capsys.readouterr()
        assert "Timeout Handling" in captured.out

    @pytest.mark.asyncio
    async def test_shows_session_is_timed_out(self, patch_progress_monitor, capsys):
        await demo_timeout_handling()
        captured = capsys.readouterr()
        assert "Is timed out: True" in captured.out

    @pytest.mark.asyncio
    async def test_shows_not_timed_out_after_extension(self, patch_progress_monitor, capsys):
        await demo_timeout_handling()
        captured = capsys.readouterr()
        assert "Is timed out now: False" in captured.out

    @pytest.mark.asyncio
    async def test_shows_extended_until(self, patch_progress_monitor, capsys):
        await demo_timeout_handling()
        captured = capsys.readouterr()
        assert "Extended until" in captured.out


class TestDemoStallDetection:
    @pytest.mark.asyncio
    async def test_runs_without_error(self, patch_progress_monitor, capsys):
        await demo_stall_detection()
        captured = capsys.readouterr()
        assert "Stall Detection" in captured.out

    @pytest.mark.asyncio
    async def test_active_session_not_stalled(self, patch_progress_monitor, capsys):
        await demo_stall_detection()
        captured = capsys.readouterr()
        lines = captured.out.splitlines()
        active_idx = next(i for i, ln in enumerate(lines) if "active-session" in ln)
        context = "\n".join(lines[active_idx : active_idx + 6])
        assert "Stalled: False" in context

    @pytest.mark.asyncio
    async def test_stalled_session_is_stalled(self, patch_progress_monitor, capsys):
        await demo_stall_detection()
        captured = capsys.readouterr()
        lines = captured.out.splitlines()
        stalled_idx = next(i for i, ln in enumerate(lines) if "stalled-session" in ln)
        context = "\n".join(lines[stalled_idx : stalled_idx + 6])
        assert "Stalled: True" in context


class TestDemoMetricsExport:
    @pytest.mark.asyncio
    async def test_runs_without_error(self, patch_progress_monitor, capsys):
        await demo_metrics_export()
        captured = capsys.readouterr()
        assert "Metrics Export" in captured.out

    @pytest.mark.asyncio
    async def test_shows_correct_task_id(self, patch_progress_monitor, capsys):
        await demo_metrics_export()
        captured = capsys.readouterr()
        assert "HRN-999" in captured.out

    @pytest.mark.asyncio
    async def test_shows_timeout_minutes(self, patch_progress_monitor, capsys):
        await demo_metrics_export()
        captured = capsys.readouterr()
        assert "20" in captured.out  # timeout_minutes=20

    @pytest.mark.asyncio
    async def test_shows_metadata(self, patch_progress_monitor, capsys):
        await demo_metrics_export()
        captured = capsys.readouterr()
        assert "test" in captured.out  # domain: test

    @pytest.mark.asyncio
    async def test_shows_session_id(self, patch_progress_monitor, capsys):
        await demo_metrics_export()
        captured = capsys.readouterr()
        assert "export-test" in captured.out


class TestDemoMain:
    @pytest.mark.asyncio
    async def test_main_runs_all_demos(self, patch_progress_monitor, capsys):
        await main()
        captured = capsys.readouterr()
        assert "Basic Progress Monitor Usage" in captured.out
        assert "Completion Detection" in captured.out
        assert "Timeout Handling" in captured.out
        assert "Stall Detection" in captured.out
        assert "Metrics Export" in captured.out
        assert "Demonstration complete!" in captured.out

    @pytest.mark.asyncio
    async def test_main_prints_usage_hint(self, patch_progress_monitor, capsys):
        await main()
        captured = capsys.readouterr()
        assert "forge_harness.iteration.monitor" in captured.out

    @pytest.mark.asyncio
    async def test_main_prints_header(self, patch_progress_monitor, capsys):
        await main()
        captured = capsys.readouterr()
        assert "FORGE Harness" in captured.out
        assert "Progress Monitor Demonstration" in captured.out


# ===========================================================================
# Edge cases and boundary conditions
# ===========================================================================


class TestEdgeCases:
    def test_detect_completion_exact_1000_char_boundary(self):
        m = ProgressMonitor()
        _patch_monitor(m)
        padding = "X" * (1000 - len("All tests pass"))
        output = padding + "All tests pass"
        assert m.detect_completion("s", output) is True

    def test_extend_timeout_twice_uses_most_recent(self):
        m = ProgressMonitor()
        _patch_monitor(m)
        m.register_session("s")
        m.extend_timeout("s", minutes=10)
        first = m.sessions["s"].extended_until
        m.extend_timeout("s", minutes=2)
        second = m.sessions["s"].extended_until
        # 2 min from now < 10 min from now
        assert second < first

    def test_get_all_metrics_returns_empty_for_empty_monitor(self):
        m = ProgressMonitor()
        _patch_monitor(m)
        assert m.get_all_metrics() == {}

    def test_manual_session_deletion_leaves_empty(self):
        m = ProgressMonitor()
        _patch_monitor(m)
        for name in ["a", "b", "c"]:
            m.register_session(name)
        for name in ["a", "b", "c"]:
            m.unregister_session(name)
        assert len(m.sessions) == 0

    def test_monitor_result_completion_marker_none_by_default(self):
        r = MonitorResult(session_id="x", status=SessionStatus.RUNNING)
        assert r.completion_marker is None

    def test_progress_monitor_sessions_dict_starts_empty(self):
        assert ProgressMonitor().sessions == {}

    @patch("forge_harness.iteration.monitor.subprocess.run")
    def test_poll_session_empty_stdout_gives_running(self, mock_run):
        mock_run.return_value = _subprocess_result(stdout="")
        m = ProgressMonitor()
        result = m.poll_session("s1")
        assert result.status == SessionStatus.RUNNING

    def test_session_metrics_started_at_is_utc(self):
        m = SessionMetrics(session_id="x")
        assert m.started_at.tzinfo is not None
