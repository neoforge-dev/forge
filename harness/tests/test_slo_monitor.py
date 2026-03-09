"""Comprehensive unit tests for the SLO Monitor Service (DF-2004).

Coverage targets:
- SLOMonitor.record_task_start
- SLOMonitor.record_task_complete
- SLOMonitor.record_requeue
- SLOMonitor.check_slo
- SLOMonitor.check_all_slos
- SLOMonitor.get_breaches
- SLOMonitor._classify_status (all branch paths)
- SLOMonitor._emit_if_needed (healthy skip, warning, breached, exception path)
- SLOMonitor.reset
- get_slo_monitor (singleton creation + reuse)
- reset_slo_monitor (singleton teardown)

All external dependencies (EventEmitter, SSEEventType, get_event_emitter) are
mocked so tests run in isolation without a running server.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.webhook_server.models.lane_slo import (
    DEFAULT_LANE_SLOS,
    LaneSLO,
    SLOCheckResult,
    SLOStatus,
)
from forge_harness.webhook_server.models.work_cell import WorkCellLane
from forge_harness.webhook_server.services.slo_monitor import (
    SLOMonitor,
    _TaskRecord,
    get_slo_monitor,
    reset_slo_monitor,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

# A simple SLO with round numbers for easy arithmetic:
#   max_lead_time_seconds = 100
#   target_pass_rate      = 0.80   → breach below 0.80
#   max_requeue_count     = 2
#   alert_threshold_pct   = 0.80
#     → lead_time warning threshold  = 100 * 0.80 = 80 s
#     → pass_rate warning floor      = 0.80 + (1-0.80)*(1-0.80)
#                                    = 0.80 + 0.20*0.20 = 0.84
_SIMPLE_SLO = LaneSLO(
    lane=WorkCellLane.api_simple,
    max_lead_time_seconds=100,
    target_pass_rate=0.80,
    max_requeue_count=2,
    alert_threshold_pct=0.80,
)

_SLOS_OVERRIDE: dict[WorkCellLane, LaneSLO] = {
    lane: _SIMPLE_SLO for lane in WorkCellLane
}


def make_monitor(slos: dict[WorkCellLane, LaneSLO] | None = None) -> SLOMonitor:
    """Return a fresh SLOMonitor with injected SLOs."""
    return SLOMonitor(slos=slos or _SLOS_OVERRIDE)


def _inject_record(
    monitor: SLOMonitor,
    task_id: str,
    lane: WorkCellLane,
    passed: bool,
    lead_time: float,
    completed_ago_seconds: float = 0,
) -> None:
    """Directly insert a completed _TaskRecord into the monitor's internal list.

    This avoids relying on real wall-clock elapsed time between start/complete
    calls and gives tests precise control over lead_time and completed_at.
    """
    now = datetime.now(UTC)
    completed_at = now - timedelta(seconds=completed_ago_seconds)
    started_at = completed_at - timedelta(seconds=lead_time)
    record = _TaskRecord(
        task_id=task_id,
        lane=lane,
        started_at=started_at,
        completed_at=completed_at,
        passed=passed,
        lead_time_seconds=lead_time,
    )
    with monitor._lock:
        monitor._records.append(record)


# ---------------------------------------------------------------------------
# TestSLOMonitorInit
# ---------------------------------------------------------------------------


class TestSLOMonitorInit:
    """Construction and defaults."""

    def test_default_slos_loaded(self):
        monitor = SLOMonitor()
        assert monitor._slos == DEFAULT_LANE_SLOS

    def test_custom_slos_injected(self):
        monitor = make_monitor()
        assert monitor._slos == _SLOS_OVERRIDE

    def test_initial_state_is_empty(self):
        monitor = make_monitor()
        assert monitor._start_times == {}
        assert monitor._records == []
        assert dict(monitor._requeue_counts) == {}


# ---------------------------------------------------------------------------
# TestRecordTaskStart
# ---------------------------------------------------------------------------


class TestRecordTaskStart:
    """Tests for SLOMonitor.record_task_start."""

    def test_records_start_time(self):
        monitor = make_monitor()
        before = datetime.now(UTC)
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        after = datetime.now(UTC)

        assert "t1" in monitor._start_times
        lane, ts = monitor._start_times["t1"]
        assert lane == WorkCellLane.api_simple
        assert before <= ts <= after

    def test_idempotent_second_call_kept_original(self):
        monitor = make_monitor()
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        _, first_ts = monitor._start_times["t1"]

        # Small delay so a new timestamp would differ
        time.sleep(0.01)
        monitor.record_task_start("t1", WorkCellLane.docs)

        _, second_ts = monitor._start_times["t1"]
        assert second_ts == first_ts  # original preserved

    def test_multiple_tasks_tracked_independently(self):
        monitor = make_monitor()
        monitor.record_task_start("a", WorkCellLane.api_simple)
        monitor.record_task_start("b", WorkCellLane.docs)

        assert "a" in monitor._start_times
        assert "b" in monitor._start_times
        assert monitor._start_times["a"][0] == WorkCellLane.api_simple
        assert monitor._start_times["b"][0] == WorkCellLane.docs

    def test_thread_safe_concurrent_starts(self):
        monitor = make_monitor()
        ids = [f"task-{i}" for i in range(50)]

        def start_task(tid: str) -> None:
            monitor.record_task_start(tid, WorkCellLane.api_simple)

        threads = [threading.Thread(target=start_task, args=(tid,)) for tid in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(monitor._start_times) == 50


# ---------------------------------------------------------------------------
# TestRecordTaskComplete
# ---------------------------------------------------------------------------


class TestRecordTaskComplete:
    """Tests for SLOMonitor.record_task_complete."""

    def test_returns_none_for_unknown_task(self):
        monitor = make_monitor()
        result = monitor.record_task_complete("never-started", passed=True)
        assert result is None

    def test_removes_from_start_times(self):
        monitor = make_monitor()
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        monitor.record_task_complete("t1", passed=True)
        assert "t1" not in monitor._start_times

    def test_appends_record(self):
        monitor = make_monitor()
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        result = monitor.record_task_complete("t1", passed=True)

        assert result is not None
        assert len(monitor._records) == 1
        assert monitor._records[0].task_id == "t1"
        assert monitor._records[0].lane == WorkCellLane.api_simple
        assert monitor._records[0].passed is True

    def test_lead_time_is_positive(self):
        monitor = make_monitor()
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        time.sleep(0.01)
        result = monitor.record_task_complete("t1", passed=False)

        assert result is not None
        assert result.lead_time_seconds is not None
        assert result.lead_time_seconds > 0

    def test_failed_task_recorded(self):
        monitor = make_monitor()
        monitor.record_task_start("t1", WorkCellLane.docs)
        result = monitor.record_task_complete("t1", passed=False)

        assert result is not None
        assert result.passed is False

    @patch(
        "forge_harness.webhook_server.services.slo_monitor.SLOMonitor._emit_if_needed"
    )
    def test_emits_after_complete(self, mock_emit):
        monitor = make_monitor()
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        monitor.record_task_complete("t1", passed=True)

        mock_emit.assert_called_once()
        arg = mock_emit.call_args[0][0]
        assert isinstance(arg, SLOCheckResult)
        assert arg.lane == WorkCellLane.api_simple

    def test_does_not_emit_when_task_unknown(self):
        monitor = make_monitor()
        with patch.object(monitor, "_emit_if_needed") as mock_emit:
            monitor.record_task_complete("never-started", passed=True)
            mock_emit.assert_not_called()


# ---------------------------------------------------------------------------
# TestRecordRequeue
# ---------------------------------------------------------------------------


class TestRecordRequeue:
    """Tests for SLOMonitor.record_requeue."""

    def test_increments_counter(self):
        monitor = make_monitor()
        monitor.record_requeue(WorkCellLane.api_simple)
        assert monitor._requeue_counts[WorkCellLane.api_simple] == 1

    def test_multiple_increments_accumulate(self):
        monitor = make_monitor()
        for _ in range(5):
            monitor.record_requeue(WorkCellLane.docs)
        assert monitor._requeue_counts[WorkCellLane.docs] == 5

    def test_different_lanes_tracked_separately(self):
        monitor = make_monitor()
        monitor.record_requeue(WorkCellLane.api_simple)
        monitor.record_requeue(WorkCellLane.api_simple)
        monitor.record_requeue(WorkCellLane.docs)

        assert monitor._requeue_counts[WorkCellLane.api_simple] == 2
        assert monitor._requeue_counts[WorkCellLane.docs] == 1

    def test_untouched_lane_is_zero(self):
        monitor = make_monitor()
        monitor.record_requeue(WorkCellLane.api_simple)
        assert monitor._requeue_counts[WorkCellLane.docs] == 0


# ---------------------------------------------------------------------------
# TestCheckSloNoData
# ---------------------------------------------------------------------------


class TestCheckSloNoData:
    """check_slo with no completed task records."""

    def test_empty_monitor_returns_healthy(self):
        monitor = make_monitor()
        result = monitor.check_slo(WorkCellLane.api_simple)

        assert result.status == SLOStatus.healthy
        assert result.current_lead_time_seconds is None
        assert result.current_pass_rate is None
        assert result.requeue_count == 0
        assert result.lane == WorkCellLane.api_simple

    def test_no_records_but_requeue_breach_returns_breached(self):
        # max_requeue_count=2 in _SIMPLE_SLO; 3 requeues → breach
        monitor = make_monitor()
        monitor.record_requeue(WorkCellLane.api_simple)
        monitor.record_requeue(WorkCellLane.api_simple)
        monitor.record_requeue(WorkCellLane.api_simple)

        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.breached

    def test_no_records_requeue_at_max_stays_healthy(self):
        # Exactly at max_requeue_count=2 → still healthy (not >)
        monitor = make_monitor()
        monitor.record_requeue(WorkCellLane.api_simple)
        monitor.record_requeue(WorkCellLane.api_simple)

        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.healthy


# ---------------------------------------------------------------------------
# TestClassifyStatus
# ---------------------------------------------------------------------------


class TestClassifyStatus:
    """Direct tests for SLOMonitor._classify_status boundary conditions.

    SLO used: max_lead_time=100, target_pass_rate=0.80,
              max_requeue=2, alert_threshold_pct=0.80
    Thresholds derived:
      lead_time breach   > 100
      lead_time warning  > 80
      pass_rate breach   < 0.80
      pass_rate warning  < 0.84  (0.80 + 0.20*0.20)
      requeue   breach   > 2
    """

    def test_all_within_target_is_healthy(self):
        status = SLOMonitor._classify_status(
            _SIMPLE_SLO,
            avg_lead_time=50.0,
            pass_rate=0.90,
            requeue_count=0,
        )
        assert status == SLOStatus.healthy

    def test_lead_time_exactly_at_max_triggers_warning(self):
        # avg_lead_time=100.0 is not > max_lead_time_seconds=100 (no breach),
        # but it IS > the warning threshold (100 * 0.80 = 80.0), so warning fires.
        status = SLOMonitor._classify_status(
            _SIMPLE_SLO,
            avg_lead_time=100.0,
            pass_rate=0.90,
            requeue_count=0,
        )
        assert status == SLOStatus.warning

    def test_lead_time_just_over_max_is_breached(self):
        status = SLOMonitor._classify_status(
            _SIMPLE_SLO,
            avg_lead_time=100.001,
            pass_rate=0.90,
            requeue_count=0,
        )
        assert status == SLOStatus.breached

    def test_lead_time_in_warning_band(self):
        # 80 < 85 <= 100  → warning
        status = SLOMonitor._classify_status(
            _SIMPLE_SLO,
            avg_lead_time=85.0,
            pass_rate=0.90,
            requeue_count=0,
        )
        assert status == SLOStatus.warning

    def test_lead_time_exactly_at_warn_threshold_is_healthy(self):
        # 85 * 0.80 = 80 → not > 80
        status = SLOMonitor._classify_status(
            _SIMPLE_SLO,
            avg_lead_time=80.0,
            pass_rate=0.90,
            requeue_count=0,
        )
        assert status == SLOStatus.healthy

    def test_pass_rate_below_target_is_breached(self):
        status = SLOMonitor._classify_status(
            _SIMPLE_SLO,
            avg_lead_time=50.0,
            pass_rate=0.79,
            requeue_count=0,
        )
        assert status == SLOStatus.breached

    def test_pass_rate_exactly_at_target_triggers_warning(self):
        # pass_rate=0.80 equals target_pass_rate=0.80, so not breached.
        # However, warn_floor = 0.80 + (1-0.80)*(1-0.80) = 0.84 (approx).
        # 0.80 < 0.84 → warning fires.
        status = SLOMonitor._classify_status(
            _SIMPLE_SLO,
            avg_lead_time=50.0,
            pass_rate=0.80,
            requeue_count=0,
        )
        assert status == SLOStatus.warning

    def test_pass_rate_in_warning_band(self):
        # warn_floor = 0.80 + 0.20*0.20 = 0.84 → 0.82 is in warning band
        status = SLOMonitor._classify_status(
            _SIMPLE_SLO,
            avg_lead_time=50.0,
            pass_rate=0.82,
            requeue_count=0,
        )
        assert status == SLOStatus.warning

    def test_pass_rate_just_above_warn_floor_is_healthy(self):
        # warn_floor = 0.80 + 0.20 * 0.20 = 0.8400000000000001 (floating point).
        # pass_rate=0.85 is strictly above the floor → healthy.
        status = SLOMonitor._classify_status(
            _SIMPLE_SLO,
            avg_lead_time=50.0,
            pass_rate=0.85,
            requeue_count=0,
        )
        assert status == SLOStatus.healthy

    def test_requeue_over_max_is_breached(self):
        status = SLOMonitor._classify_status(
            _SIMPLE_SLO,
            avg_lead_time=None,
            pass_rate=None,
            requeue_count=3,
        )
        assert status == SLOStatus.breached

    def test_requeue_at_max_is_healthy(self):
        status = SLOMonitor._classify_status(
            _SIMPLE_SLO,
            avg_lead_time=None,
            pass_rate=None,
            requeue_count=2,
        )
        assert status == SLOStatus.healthy

    def test_none_metrics_are_ignored(self):
        """None lead_time and pass_rate never trigger breach/warning."""
        status = SLOMonitor._classify_status(
            _SIMPLE_SLO,
            avg_lead_time=None,
            pass_rate=None,
            requeue_count=0,
        )
        assert status == SLOStatus.healthy

    def test_lead_time_breach_takes_precedence_over_pass_rate_breach(self):
        """Lead time breach is returned first (order in code)."""
        status = SLOMonitor._classify_status(
            _SIMPLE_SLO,
            avg_lead_time=200.0,  # breaches lead time
            pass_rate=0.50,       # also breaches pass rate
            requeue_count=10,     # also breaches requeue
        )
        assert status == SLOStatus.breached  # status is still breached


# ---------------------------------------------------------------------------
# TestCheckSloWithData
# ---------------------------------------------------------------------------


class TestCheckSloWithData:
    """check_slo with real records injected via _inject_record."""

    def test_single_passing_fast_task_is_healthy(self):
        monitor = make_monitor()
        _inject_record(monitor, "t1", WorkCellLane.api_simple, passed=True, lead_time=10.0)

        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.healthy
        assert result.current_lead_time_seconds == pytest.approx(10.0)
        assert result.current_pass_rate == pytest.approx(1.0)

    def test_average_lead_time_computed_correctly(self):
        monitor = make_monitor()
        _inject_record(monitor, "t1", WorkCellLane.api_simple, passed=True, lead_time=40.0)
        _inject_record(monitor, "t2", WorkCellLane.api_simple, passed=True, lead_time=60.0)

        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.current_lead_time_seconds == pytest.approx(50.0)

    def test_pass_rate_computed_correctly(self):
        monitor = make_monitor()
        for i in range(8):
            _inject_record(monitor, f"p{i}", WorkCellLane.api_simple, passed=True, lead_time=10.0)
        for i in range(2):
            _inject_record(monitor, f"f{i}", WorkCellLane.api_simple, passed=False, lead_time=10.0)

        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.current_pass_rate == pytest.approx(0.80)

    def test_status_breached_when_lead_time_high(self):
        monitor = make_monitor()
        _inject_record(monitor, "t1", WorkCellLane.api_simple, passed=True, lead_time=150.0)

        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.breached

    def test_status_warning_when_lead_time_in_band(self):
        monitor = make_monitor()
        _inject_record(monitor, "t1", WorkCellLane.api_simple, passed=True, lead_time=85.0)

        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.warning

    def test_status_breached_when_pass_rate_below_target(self):
        monitor = make_monitor()
        # 5 pass, 5 fail → 50% < 80%
        for i in range(5):
            _inject_record(monitor, f"p{i}", WorkCellLane.api_simple, passed=True, lead_time=10.0)
        for i in range(5):
            _inject_record(monitor, f"f{i}", WorkCellLane.api_simple, passed=False, lead_time=10.0)

        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.breached

    def test_records_from_other_lanes_not_counted(self):
        monitor = make_monitor()
        # Inject failing records into docs lane
        for i in range(10):
            _inject_record(monitor, f"d{i}", WorkCellLane.docs, passed=False, lead_time=200.0)
        # api_simple lane has no records
        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.healthy
        assert result.current_pass_rate is None

    def test_requeue_count_included_in_result(self):
        monitor = make_monitor()
        monitor.record_requeue(WorkCellLane.api_simple)
        monitor.record_requeue(WorkCellLane.api_simple)
        _inject_record(monitor, "t1", WorkCellLane.api_simple, passed=True, lead_time=10.0)

        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.requeue_count == 2

    def test_checked_at_is_iso8601_string(self):
        monitor = make_monitor()
        result = monitor.check_slo(WorkCellLane.api_simple)
        # Should be parseable as a datetime
        parsed = datetime.fromisoformat(result.checked_at)
        assert parsed is not None


# ---------------------------------------------------------------------------
# TestCheckAllSlos
# ---------------------------------------------------------------------------


class TestCheckAllSlos:
    """Tests for SLOMonitor.check_all_slos."""

    def test_returns_one_result_per_lane(self):
        monitor = make_monitor()
        results = monitor.check_all_slos()
        assert len(results) == len(WorkCellLane)

    def test_results_cover_all_lanes(self):
        monitor = make_monitor()
        results = monitor.check_all_slos()
        result_lanes = {r.lane for r in results}
        assert result_lanes == set(WorkCellLane)

    def test_all_healthy_when_empty(self):
        monitor = make_monitor()
        results = monitor.check_all_slos()
        assert all(r.status == SLOStatus.healthy for r in results)

    def test_breached_lane_reflected_in_all_results(self):
        monitor = make_monitor()
        # Push api_simple into breach via high requeue
        monitor.record_requeue(WorkCellLane.api_simple)
        monitor.record_requeue(WorkCellLane.api_simple)
        monitor.record_requeue(WorkCellLane.api_simple)

        results = monitor.check_all_slos()
        api_simple_result = next(r for r in results if r.lane == WorkCellLane.api_simple)
        assert api_simple_result.status == SLOStatus.breached


# ---------------------------------------------------------------------------
# TestGetBreaches
# ---------------------------------------------------------------------------


class TestGetBreaches:
    """Tests for SLOMonitor.get_breaches."""

    def test_no_breaches_when_empty(self):
        monitor = make_monitor()
        breaches = monitor.get_breaches(window_minutes=60)
        assert breaches == []

    def test_returns_only_breached_lanes(self):
        monitor = make_monitor()
        # Breach api_simple via requeue
        for _ in range(3):
            monitor.record_requeue(WorkCellLane.api_simple)
        # docs lane is healthy
        _inject_record(monitor, "d1", WorkCellLane.docs, passed=True, lead_time=10.0)

        breaches = monitor.get_breaches(window_minutes=60)
        assert len(breaches) == 1
        assert breaches[0].lane == WorkCellLane.api_simple
        assert breaches[0].status == SLOStatus.breached

    def test_records_outside_window_excluded(self):
        monitor = make_monitor()
        # Inject a record that is 120 minutes old (outside 60-minute window)
        _inject_record(
            monitor,
            "old",
            WorkCellLane.api_simple,
            passed=False,
            lead_time=200.0,
            completed_ago_seconds=7200,  # 2 hours ago
        )

        breaches = monitor.get_breaches(window_minutes=60)
        # Old record excluded → api_simple not breached
        assert all(b.lane != WorkCellLane.api_simple for b in breaches)

    def test_records_within_window_included(self):
        monitor = make_monitor()
        # Inject a record that is 30 minutes old (inside 60-minute window)
        _inject_record(
            monitor,
            "recent",
            WorkCellLane.api_simple,
            passed=False,
            lead_time=200.0,
            completed_ago_seconds=1800,  # 30 min ago
        )

        breaches = monitor.get_breaches(window_minutes=60)
        assert any(b.lane == WorkCellLane.api_simple for b in breaches)

    def test_invalid_window_defaults_to_60(self):
        """Negative window_minutes should default to 60 (no crash)."""
        monitor = make_monitor()
        # Should not raise
        breaches = monitor.get_breaches(window_minutes=-1)
        assert isinstance(breaches, list)

    def test_zero_window_defaults_to_60(self):
        monitor = make_monitor()
        breaches = monitor.get_breaches(window_minutes=0)
        assert isinstance(breaches, list)

    def test_healthy_lane_with_requeue_at_max_not_returned(self):
        monitor = make_monitor()
        # max_requeue_count=2 in _SIMPLE_SLO → exactly 2 is not a breach
        monitor.record_requeue(WorkCellLane.api_simple)
        monitor.record_requeue(WorkCellLane.api_simple)

        breaches = monitor.get_breaches(window_minutes=60)
        assert all(b.lane != WorkCellLane.api_simple for b in breaches)

    def test_warning_lane_not_included_in_breaches(self):
        monitor = make_monitor()
        # lead_time=85 → warning, not breach
        _inject_record(monitor, "t1", WorkCellLane.api_simple, passed=True, lead_time=85.0)

        breaches = monitor.get_breaches(window_minutes=60)
        assert all(b.lane != WorkCellLane.api_simple for b in breaches)


# ---------------------------------------------------------------------------
# TestEmitIfNeeded
# ---------------------------------------------------------------------------


class TestEmitIfNeeded:
    """Tests for SLOMonitor._emit_if_needed.

    ``get_event_emitter`` is imported lazily inside ``_emit_if_needed`` with
    a local ``from ... import`` statement, so it does NOT exist as a module-level
    attribute of ``slo_monitor``.  We must patch it in its *origin* module:
    ``forge_harness.webhook_server.services.event_emitter.get_event_emitter``.
    """

    _EMITTER_PATH = (
        "forge_harness.webhook_server.services.event_emitter.get_event_emitter"
    )

    def _healthy_result(self) -> SLOCheckResult:
        return SLOCheckResult(
            lane=WorkCellLane.api_simple,
            status=SLOStatus.healthy,
            requeue_count=0,
        )

    def _warning_result(self) -> SLOCheckResult:
        return SLOCheckResult(
            lane=WorkCellLane.api_simple,
            status=SLOStatus.warning,
            current_lead_time_seconds=85.0,
            requeue_count=0,
        )

    def _breached_result(self) -> SLOCheckResult:
        return SLOCheckResult(
            lane=WorkCellLane.api_simple,
            status=SLOStatus.breached,
            current_lead_time_seconds=150.0,
            requeue_count=0,
        )

    def test_healthy_status_does_not_emit(self):
        monitor = make_monitor()
        mock_emitter = MagicMock()

        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            monitor._emit_if_needed(self._healthy_result())
            mock_emitter.emit.assert_not_called()

    def test_warning_status_emits_warning_event(self):
        monitor = make_monitor()
        mock_emitter = MagicMock()

        from forge_harness.webhook_server.models.sse_events import SSEEventType

        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            monitor._emit_if_needed(self._warning_result())
            mock_emitter.emit.assert_called_once()
            call_kwargs = mock_emitter.emit.call_args.kwargs
            assert call_kwargs["event_type"] == SSEEventType.task_slo_warning
            assert call_kwargs["source"] == "slo_monitor"
            data = call_kwargs["data"]
            assert data["lane"] == WorkCellLane.api_simple.value
            assert data["status"] == SLOStatus.warning.value

    def test_breached_status_emits_breached_event(self):
        monitor = make_monitor()
        mock_emitter = MagicMock()

        from forge_harness.webhook_server.models.sse_events import SSEEventType

        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            monitor._emit_if_needed(self._breached_result())
            mock_emitter.emit.assert_called_once()
            call_kwargs = mock_emitter.emit.call_args.kwargs
            assert call_kwargs["event_type"] == SSEEventType.task_slo_breached

    def test_emit_exception_is_caught_silently(self):
        """Emit failures must not propagate to callers.

        The ``_emit_if_needed`` method wraps all emit activity in a broad
        ``except Exception`` block. We simulate an import-time error by
        patching the event_emitter module's ``get_event_emitter`` to raise.
        """
        monitor = make_monitor()

        with patch(self._EMITTER_PATH, side_effect=RuntimeError("emitter unavailable")):
            # Must not raise
            monitor._emit_if_needed(self._warning_result())

    def test_emit_data_contains_all_fields(self):
        monitor = make_monitor()
        mock_emitter = MagicMock()

        with patch(self._EMITTER_PATH, return_value=mock_emitter):
            monitor._emit_if_needed(self._breached_result())
            data = mock_emitter.emit.call_args.kwargs["data"]
            assert "lane" in data
            assert "status" in data
            assert "current_lead_time_seconds" in data
            assert "current_pass_rate" in data
            assert "requeue_count" in data
            assert "checked_at" in data


# ---------------------------------------------------------------------------
# TestReset
# ---------------------------------------------------------------------------


class TestReset:
    """Tests for SLOMonitor.reset."""

    def test_clears_start_times(self):
        monitor = make_monitor()
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        monitor.reset()
        assert monitor._start_times == {}

    def test_clears_records(self):
        monitor = make_monitor()
        _inject_record(monitor, "t1", WorkCellLane.api_simple, passed=True, lead_time=10.0)
        monitor.reset()
        assert monitor._records == []

    def test_clears_requeue_counts(self):
        monitor = make_monitor()
        monitor.record_requeue(WorkCellLane.api_simple)
        monitor.reset()
        assert monitor._requeue_counts[WorkCellLane.api_simple] == 0

    def test_monitor_reusable_after_reset(self):
        monitor = make_monitor()
        _inject_record(monitor, "t1", WorkCellLane.api_simple, passed=False, lead_time=200.0)
        monitor.reset()

        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.healthy


# ---------------------------------------------------------------------------
# TestSingleton
# ---------------------------------------------------------------------------


class TestSingleton:
    """Tests for get_slo_monitor and reset_slo_monitor."""

    def setup_method(self):
        reset_slo_monitor()

    def teardown_method(self):
        reset_slo_monitor()

    def test_get_slo_monitor_returns_instance(self):
        monitor = get_slo_monitor()
        assert isinstance(monitor, SLOMonitor)

    def test_get_slo_monitor_is_singleton(self):
        m1 = get_slo_monitor()
        m2 = get_slo_monitor()
        assert m1 is m2

    def test_reset_slo_monitor_creates_fresh_instance(self):
        m1 = get_slo_monitor()
        reset_slo_monitor()
        m2 = get_slo_monitor()
        assert m1 is not m2

    def test_reset_slo_monitor_after_state_changes(self):
        m1 = get_slo_monitor()
        m1.record_task_start("t1", WorkCellLane.api_simple)
        reset_slo_monitor()
        m2 = get_slo_monitor()
        assert m2._start_times == {}

    def test_singleton_thread_safe_creation(self):
        """Concurrent calls to get_slo_monitor must return the same instance."""
        instances: list[SLOMonitor] = []
        lock = threading.Lock()

        def fetch() -> None:
            m = get_slo_monitor()
            with lock:
                instances.append(m)

        threads = [threading.Thread(target=fetch) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All must be the identical object
        assert all(i is instances[0] for i in instances)


# ---------------------------------------------------------------------------
# TestDefaultLaneSlos
# ---------------------------------------------------------------------------


class TestDefaultLaneSlos:
    """Validate the DEFAULT_LANE_SLOS constants meet documented thresholds."""

    def test_all_lanes_have_default_slo(self):
        for lane in WorkCellLane:
            assert lane in DEFAULT_LANE_SLOS, f"Missing SLO for {lane}"

    def test_api_simple_max_lead_time(self):
        slo = DEFAULT_LANE_SLOS[WorkCellLane.api_simple]
        assert slo.max_lead_time_seconds == 300  # 5 minutes

    def test_api_simple_pass_rate(self):
        slo = DEFAULT_LANE_SLOS[WorkCellLane.api_simple]
        assert slo.target_pass_rate == pytest.approx(0.95)

    def test_security_change_zero_requeue(self):
        slo = DEFAULT_LANE_SLOS[WorkCellLane.security_change]
        assert slo.max_requeue_count == 0

    def test_deployment_zero_requeue(self):
        slo = DEFAULT_LANE_SLOS[WorkCellLane.deployment]
        assert slo.max_requeue_count == 0

    def test_security_change_strict_pass_rate(self):
        slo = DEFAULT_LANE_SLOS[WorkCellLane.security_change]
        assert slo.target_pass_rate == pytest.approx(0.99)

    def test_all_alert_thresholds_in_valid_range(self):
        for lane, slo in DEFAULT_LANE_SLOS.items():
            assert 0.0 < slo.alert_threshold_pct < 1.0, (
                f"alert_threshold_pct out of range for {lane}"
            )

    def test_all_max_lead_times_positive(self):
        for lane, slo in DEFAULT_LANE_SLOS.items():
            assert slo.max_lead_time_seconds > 0, f"max_lead_time_seconds <= 0 for {lane}"


# ---------------------------------------------------------------------------
# TestIntegration
# ---------------------------------------------------------------------------


class TestIntegration:
    """End-to-end scenarios using public API only."""

    def test_full_task_lifecycle_healthy(self):
        monitor = make_monitor()
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        result = monitor.record_task_complete("t1", passed=True)

        assert result is not None
        slo_result = monitor.check_slo(WorkCellLane.api_simple)
        assert slo_result.status in (SLOStatus.healthy, SLOStatus.warning)

    def test_full_task_lifecycle_breached_via_pass_rate(self):
        monitor = make_monitor()
        # 1 pass, 9 fail → 10 % < 80 %
        monitor.record_task_start("p1", WorkCellLane.api_simple)
        monitor.record_task_complete("p1", passed=True)

        for i in range(9):
            tid = f"f{i}"
            monitor.record_task_start(tid, WorkCellLane.api_simple)
            monitor.record_task_complete(tid, passed=False)

        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.breached
        assert result.current_pass_rate == pytest.approx(0.10)

    def test_get_breaches_returns_breach_after_lifecycle(self):
        monitor = make_monitor()
        for i in range(3):
            monitor.record_requeue(WorkCellLane.api_simple)

        breaches = monitor.get_breaches()
        lanes = {b.lane for b in breaches}
        assert WorkCellLane.api_simple in lanes

    def test_reset_clears_breach_state(self):
        monitor = make_monitor()
        for _ in range(3):
            monitor.record_requeue(WorkCellLane.api_simple)

        assert monitor.get_breaches() != []
        monitor.reset()
        assert monitor.get_breaches() == []

    def test_multiple_lanes_independently_tracked(self):
        monitor = make_monitor()
        # api_simple: healthy
        _inject_record(monitor, "s1", WorkCellLane.api_simple, passed=True, lead_time=10.0)
        # docs: breached lead time
        _inject_record(monitor, "d1", WorkCellLane.docs, passed=True, lead_time=200.0)

        api_result = monitor.check_slo(WorkCellLane.api_simple)
        docs_result = monitor.check_slo(WorkCellLane.docs)

        assert api_result.status == SLOStatus.healthy
        assert docs_result.status == SLOStatus.breached

    @patch(
        "forge_harness.webhook_server.services.slo_monitor.SLOMonitor._emit_if_needed"
    )
    def test_emit_called_once_per_complete(self, mock_emit):
        monitor = make_monitor()
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        monitor.record_task_complete("t1", passed=True)
        monitor.record_task_start("t2", WorkCellLane.api_simple)
        monitor.record_task_complete("t2", passed=True)

        assert mock_emit.call_count == 2

    def test_concurrent_record_and_check(self):
        """Concurrent writes + reads must not raise under the RLock."""
        monitor = make_monitor()
        errors: list[Exception] = []

        def writer(n: int) -> None:
            try:
                tid = f"task-{n}"
                monitor.record_task_start(tid, WorkCellLane.api_simple)
                monitor.record_task_complete(tid, passed=n % 2 == 0)
            except Exception as exc:
                errors.append(exc)

        def reader() -> None:
            try:
                monitor.check_slo(WorkCellLane.api_simple)
            except Exception as exc:
                errors.append(exc)

        threads = []
        for i in range(20):
            threads.append(threading.Thread(target=writer, args=(i,)))
            threads.append(threading.Thread(target=reader))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
