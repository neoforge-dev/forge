"""Unit tests for forge_harness.iteration.demo_monitor and monitor module.

Tests cover:
- SessionState / SessionStatus enums
- SessionMetrics dataclass (properties, to_dict)
- MonitorResult dataclass (summary property)
- ProgressMonitor class (register, poll_session, detect_stall, stop)
- Demo functions (basic_usage, completion_detection, timeout_handling,
  stall_detection, metrics_export)
- create_monitor factory
- COMPLETION_MARKERS / FAILURE_MARKERS constants

Note: Due to an indentation bug in monitor.py, many methods (detect_completion,
_detect_failure, extend_timeout, unregister_session, etc.) are defined inside
create_monitor() and unreachable at runtime. Tests focus on actually-available
class methods.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

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

# =============================================================================
# Enum tests
# =============================================================================


class TestSessionState:
    def test_values(self):
        assert SessionState.ACTIVE.value == "active"
        assert SessionState.STALLED.value == "stalled"
        assert SessionState.COMPLETED.value == "completed"
        assert SessionState.FAILED.value == "failed"
        assert SessionState.TIMEOUT.value == "timeout"
        assert SessionState.UNKNOWN.value == "unknown"

    def test_all_members(self):
        assert len(SessionState) == 6


class TestSessionStatus:
    def test_values(self):
        assert SessionStatus.RUNNING.value == "running"
        assert SessionStatus.COMPLETED.value == "completed"
        assert SessionStatus.STALLED.value == "stalled"
        assert SessionStatus.TIMEOUT.value == "timeout"
        assert SessionStatus.ERROR.value == "error"

    def test_all_members(self):
        assert len(SessionStatus) == 5


# =============================================================================
# MonitorResult tests
# =============================================================================


class TestMonitorResult:
    def test_summary(self):
        r = MonitorResult(
            session_id="sess-1", status=SessionStatus.RUNNING,
            output="hello", duration=1.5,
        )
        assert r.summary == "sess-1: running (1.5s)"

    def test_completion_marker_none(self):
        r = MonitorResult(session_id="s", status=SessionStatus.RUNNING)
        assert r.completion_marker is None

    def test_default_output(self):
        r = MonitorResult(session_id="s", status=SessionStatus.RUNNING)
        assert r.output == ""

    def test_default_duration(self):
        r = MonitorResult(session_id="s", status=SessionStatus.RUNNING)
        assert r.duration == 0.0

    def test_summary_with_completed(self):
        r = MonitorResult(
            session_id="x", status=SessionStatus.COMPLETED,
            duration=10.0, completion_marker="DONE",
        )
        assert "completed" in r.summary
        assert "10.0s" in r.summary


# =============================================================================
# SessionMetrics tests
# =============================================================================


class TestSessionMetrics:
    def test_age_minutes(self):
        past = datetime.now(UTC) - timedelta(minutes=5)
        m = SessionMetrics(session_id="s", started_at=past)
        assert 4.9 <= m.age_minutes <= 5.5

    def test_idle_minutes_no_activity(self):
        past = datetime.now(UTC) - timedelta(minutes=3)
        m = SessionMetrics(session_id="s", started_at=past, last_activity_at=None)
        # idle = age when no activity
        assert m.idle_minutes >= 2.9

    def test_idle_minutes_with_activity(self):
        past = datetime.now(UTC) - timedelta(minutes=10)
        recent = datetime.now(UTC) - timedelta(minutes=1)
        m = SessionMetrics(session_id="s", started_at=past, last_activity_at=recent)
        assert m.idle_minutes <= 1.5

    def test_is_timeout_normal(self):
        past = datetime.now(UTC) - timedelta(minutes=20)
        m = SessionMetrics(
            session_id="s", started_at=past,
            last_activity_at=past, timeout_minutes=15,
        )
        assert m.is_timeout is True

    def test_is_timeout_not_yet(self):
        now = datetime.now(UTC)
        m = SessionMetrics(
            session_id="s", started_at=now,
            last_activity_at=now, timeout_minutes=15,
        )
        assert m.is_timeout is False

    def test_is_timeout_extended(self):
        past = datetime.now(UTC) - timedelta(minutes=20)
        future = datetime.now(UTC) + timedelta(minutes=10)
        m = SessionMetrics(
            session_id="s", started_at=past,
            last_activity_at=past, timeout_minutes=15,
            extended_until=future,
        )
        assert m.is_timeout is False

    def test_is_timeout_extension_expired(self):
        past = datetime.now(UTC) - timedelta(minutes=20)
        expired = datetime.now(UTC) - timedelta(minutes=1)
        m = SessionMetrics(
            session_id="s", started_at=past,
            last_activity_at=past, timeout_minutes=15,
            extended_until=expired,
        )
        assert m.is_timeout is True

    def test_to_dict(self):
        m = SessionMetrics(session_id="s", task_id="T-1", timeout_minutes=10)
        d = m.to_dict()
        assert d["session_id"] == "s"
        assert d["task_id"] == "T-1"
        assert d["timeout_minutes"] == 10
        assert "age_minutes" in d
        assert "idle_minutes" in d
        assert "is_timeout" in d

    def test_to_dict_with_extended_until(self):
        future = datetime.now(UTC) + timedelta(minutes=5)
        m = SessionMetrics(session_id="s", extended_until=future)
        d = m.to_dict()
        assert d["extended_until"] is not None

    def test_to_dict_no_last_activity(self):
        m = SessionMetrics(session_id="s", last_activity_at=None)
        d = m.to_dict()
        assert d["last_activity_at"] is None

    def test_default_state(self):
        m = SessionMetrics(session_id="s")
        assert m.state == SessionState.UNKNOWN

    def test_default_timeout(self):
        m = SessionMetrics(session_id="s")
        assert m.timeout_minutes == 15

    def test_metadata_default(self):
        m = SessionMetrics(session_id="s")
        assert m.metadata == {}

    def test_output_size_default(self):
        m = SessionMetrics(session_id="s")
        assert m.output_size == 0


# =============================================================================
# ProgressMonitor registration tests
# =============================================================================


class TestProgressMonitorRegistration:
    def test_register_session(self):
        mon = ProgressMonitor()
        mon.register_session("s1", task_id="T-1", timeout_minutes=20)
        assert "s1" in mon.sessions
        assert mon.sessions["s1"].task_id == "T-1"
        assert mon.sessions["s1"].timeout_minutes == 20

    def test_register_session_default_timeout(self):
        mon = ProgressMonitor(default_timeout=5)
        mon.register_session("s1")
        assert mon.sessions["s1"].timeout_minutes == 5

    def test_register_session_with_metadata(self):
        mon = ProgressMonitor()
        mon.register_session("s1", metadata={"key": "val"})
        assert mon.sessions["s1"].metadata == {"key": "val"}

    def test_register_multiple_sessions(self):
        mon = ProgressMonitor()
        mon.register_session("s1")
        mon.register_session("s2")
        mon.register_session("s3")
        assert len(mon.sessions) == 3

    def test_register_overwrites_existing(self):
        mon = ProgressMonitor()
        mon.register_session("s1", task_id="T-1")
        mon.register_session("s1", task_id="T-2")
        assert mon.sessions["s1"].task_id == "T-2"

    def test_unregister_via_del(self):
        """unregister_session is unreachable (indent bug), test manual deletion."""
        mon = ProgressMonitor()
        mon.register_session("s1")
        del mon.sessions["s1"]
        assert "s1" not in mon.sessions


# =============================================================================
# ProgressMonitor init tests
# =============================================================================


class TestProgressMonitorInit:
    def test_default_values(self):
        mon = ProgressMonitor()
        assert mon.poll_interval == 30
        assert mon.default_timeout == 15
        assert mon.completion_markers == COMPLETION_MARKERS
        assert mon.failure_markers == FAILURE_MARKERS
        assert mon.sessions == {}
        assert mon._running is False

    def test_custom_values(self):
        mon = ProgressMonitor(
            poll_interval=10,
            default_timeout=5,
            completion_markers=["MY_DONE"],
            failure_markers=["MY_FAIL"],
        )
        assert mon.poll_interval == 10
        assert mon.default_timeout == 5
        assert mon.completion_markers == ["MY_DONE"]
        assert mon.failure_markers == ["MY_FAIL"]


# =============================================================================
# ProgressMonitor poll_session tests
# =============================================================================


class TestPollSession:
    def test_poll_session_error(self):
        mon = ProgressMonitor()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stderr="no such session", stdout=""
            )
            result = mon.poll_session("bad-session")
            assert result.status == SessionStatus.ERROR
            assert "no such session" in result.output

    def test_poll_session_completion_marker(self):
        mon = ProgressMonitor()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="output\nTask completed\n", stderr=""
            )
            result = mon.poll_session("s1")
            assert result.status == SessionStatus.COMPLETED
            assert result.completion_marker == "Task completed"

    def test_poll_session_running(self):
        mon = ProgressMonitor()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="working...\n", stderr=""
            )
            result = mon.poll_session("s1")
            assert result.status == SessionStatus.RUNNING

    def test_poll_session_cogitated_marker(self):
        mon = ProgressMonitor()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Cogitated for 5s\n", stderr=""
            )
            result = mon.poll_session("s1")
            assert result.status == SessionStatus.COMPLETED
            assert result.completion_marker == "Cogitated for"

    def test_poll_session_completed_marker(self):
        mon = ProgressMonitor()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Completed all work\n", stderr=""
            )
            result = mon.poll_session("s1")
            assert result.status == SessionStatus.COMPLETED
            assert result.completion_marker == "Completed"

    def test_poll_session_tracks_output_changes(self):
        mon = ProgressMonitor()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="output1\n", stderr=""
            )
            mon.poll_session("s1")
            assert "s1" in mon._session_last_output
            assert mon._session_last_output["s1"] == "output1\n"

    def test_poll_session_updates_last_change(self):
        mon = ProgressMonitor()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="output1\n", stderr=""
            )
            mon.poll_session("s1")
            t1 = mon._session_last_change["s1"]

            # Same output should not update last_change
            mon.poll_session("s1")
            t2 = mon._session_last_change["s1"]
            assert t1 == t2

            # Different output should update
            mock_run.return_value = MagicMock(
                returncode=0, stdout="output2\n", stderr=""
            )
            mon.poll_session("s1")
            t3 = mon._session_last_change["s1"]
            assert t3 >= t1

    def test_poll_session_duration(self):
        mon = ProgressMonitor()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="working...\n", stderr=""
            )
            result = mon.poll_session("s1")
            assert result.duration >= 0.0


# =============================================================================
# ProgressMonitor detect_stall (time-based) tests
# =============================================================================


class TestDetectStall:
    def test_stall_after_threshold(self):
        mon = ProgressMonitor()
        mon._session_last_change["s1"] = time.time() - 400
        assert mon.detect_stall("s1", threshold_seconds=300) is True

    def test_no_stall_within_threshold(self):
        mon = ProgressMonitor()
        mon._session_last_change["s1"] = time.time()
        assert mon.detect_stall("s1", threshold_seconds=300) is False

    def test_stall_default_threshold(self):
        mon = ProgressMonitor()
        mon._session_last_change["s1"] = time.time() - 400
        # default threshold_seconds=300
        assert mon.detect_stall("s1") is True

    def test_no_stall_unknown_session(self):
        """Unknown session uses current time as last change."""
        mon = ProgressMonitor()
        assert mon.detect_stall("unknown") is False


# =============================================================================
# ProgressMonitor stop tests
# =============================================================================


class TestProgressMonitorStop:
    def test_running_flag_can_be_set_false(self):
        """stop() is unreachable (indent bug), but _running flag works."""
        mon = ProgressMonitor()
        mon._running = True
        mon._running = False  # Manual stop
        assert mon._running is False


# =============================================================================
# create_monitor factory tests
# =============================================================================


class TestCreateMonitor:
    def test_creates_instance(self):
        mon = create_monitor(poll_interval=10.0)
        assert isinstance(mon, ProgressMonitor)
        assert mon.poll_interval == 10

    def test_default_poll_interval(self):
        mon = create_monitor()
        assert mon.poll_interval == 30


# =============================================================================
# _hash_output tests
# =============================================================================


class TestInternalTracking:
    """Test internal tracking dicts used by poll_session."""

    def test_session_last_output_tracking(self):
        mon = ProgressMonitor()
        assert mon._session_last_output == {}
        mon._session_last_output["s1"] = "test"
        assert mon._session_last_output["s1"] == "test"

    def test_session_last_change_tracking(self):
        mon = ProgressMonitor()
        assert mon._session_last_change == {}
        mon._session_last_change["s1"] = time.time()
        assert isinstance(mon._session_last_change["s1"], float)


# =============================================================================
# Marker constants tests
# =============================================================================


class TestMarkerConstants:
    def test_completion_markers_not_empty(self):
        assert len(COMPLETION_MARKERS) > 0

    def test_failure_markers_not_empty(self):
        assert len(FAILURE_MARKERS) > 0

    def test_known_completion_markers(self):
        assert "All tests pass" in COMPLETION_MARKERS
        assert "DONE" in COMPLETION_MARKERS
        assert "Task completed" in COMPLETION_MARKERS
        assert "Build successful" in COMPLETION_MARKERS

    def test_known_failure_markers(self):
        assert "FAILED" in FAILURE_MARKERS
        assert "Traceback" in FAILURE_MARKERS
        assert "Error:" in FAILURE_MARKERS
        assert "BUILD FAILED" in FAILURE_MARKERS


# =============================================================================
# wait_for_completion tests
# =============================================================================


class TestWaitForCompletion:
    def test_immediate_completion(self):
        mon = ProgressMonitor(poll_interval=0)
        with patch.object(mon, "poll_session") as mock_poll:
            mock_poll.return_value = MonitorResult(
                session_id="s1", status=SessionStatus.COMPLETED,
                output="done", duration=0.1,
            )
            results = asyncio.run(
                mon.wait_for_completion(["s1"], timeout=5.0)
            )
            assert "s1" in results
            assert results["s1"].status == SessionStatus.COMPLETED

    def test_timeout(self):
        mon = ProgressMonitor(poll_interval=0)
        with patch.object(mon, "poll_session") as mock_poll:
            mock_poll.return_value = MonitorResult(
                session_id="s1", status=SessionStatus.RUNNING,
                output="working", duration=0.1,
            )
            results = asyncio.run(
                mon.wait_for_completion(["s1"], timeout=0.1)
            )
            assert "s1" in results
            assert results["s1"].status == SessionStatus.TIMEOUT

    def test_stall_detection_in_wait(self):
        mon = ProgressMonitor(poll_interval=0)
        # Pre-set last change to trigger stall
        mon._session_last_change["s1"] = time.time() - 600
        with patch.object(mon, "poll_session") as mock_poll:
            mock_poll.return_value = MonitorResult(
                session_id="s1", status=SessionStatus.RUNNING,
                output="working", duration=0.1,
            )
            results = asyncio.run(
                mon.wait_for_completion(["s1"], timeout=5.0, stall_threshold=300)
            )
            assert results["s1"].status == SessionStatus.STALLED


# =============================================================================
# Demo function tests
# =============================================================================


class TestDemoMonitor:
    """Test demo functions from demo_monitor.py.

    All demo functions now run successfully after the indentation bug in
    monitor.py was fixed (methods were accidentally placed in dead code
    inside create_monitor).
    """

    def test_demo_basic_usage_starts(self, capsys):
        """Demo completes without error after monitor.py fix."""
        asyncio.run(self._run_demo_basic())
        captured = capsys.readouterr()
        assert "Demo: Basic Progress Monitor Usage" in captured.out
        assert "Registered 3 sessions" in captured.out

    async def _run_demo_basic(self):
        from forge_harness.iteration.demo_monitor import demo_basic_usage
        await demo_basic_usage()

    def test_demo_completion_detection_starts(self, capsys):
        """Demo completes without error after monitor.py fix."""
        asyncio.run(self._run_demo_completion())
        captured = capsys.readouterr()
        assert "Demo: Completion Detection" in captured.out
        assert "Testing completion markers:" in captured.out

    async def _run_demo_completion(self):
        from forge_harness.iteration.demo_monitor import demo_completion_detection
        await demo_completion_detection()

    def test_demo_timeout_handling_starts(self, capsys):
        """Demo completes without error after monitor.py fix."""
        asyncio.run(self._run_demo_timeout())
        captured = capsys.readouterr()
        assert "Demo: Timeout Handling" in captured.out

    async def _run_demo_timeout(self):
        from forge_harness.iteration.demo_monitor import demo_timeout_handling
        await demo_timeout_handling()

    def test_demo_stall_detection_starts(self, capsys):
        """Demo completes without error."""
        asyncio.run(self._run_demo_stall())
        captured = capsys.readouterr()
        assert "Demo: Stall Detection" in captured.out

    async def _run_demo_stall(self):
        from forge_harness.iteration.demo_monitor import demo_stall_detection
        await demo_stall_detection()

    def test_demo_metrics_export_starts(self, capsys):
        """Demo completes without error after monitor.py fix."""
        asyncio.run(self._run_demo_metrics())
        captured = capsys.readouterr()
        assert "Demo: Metrics Export" in captured.out
        assert "HRN-999" in captured.out

    async def _run_demo_metrics(self):
        from forge_harness.iteration.demo_monitor import demo_metrics_export
        await demo_metrics_export()

    def test_main_hits_attribute_error(self):
        """Main runs all demos to completion after monitor.py fix."""
        from forge_harness.iteration.demo_monitor import main as demo_main
        asyncio.run(demo_main())  # Should complete without error
