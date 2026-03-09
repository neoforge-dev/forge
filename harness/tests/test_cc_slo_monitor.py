"""Comprehensive tests for SLOMonitor service (DF-2004).

Targets forge_harness/webhook_server/services/slo_monitor.py.

Coverage goals (100% statement coverage):
  - All public methods: record_task_start, record_task_complete, record_requeue,
    check_slo, check_all_slos, get_breaches, reset
  - Private helpers: _classify_status, _emit_if_needed
  - Singleton: get_slo_monitor, reset_slo_monitor
  - All SLO status transitions: healthy -> warning -> breached
  - Edge cases: empty data, boundary values, None metrics
  - Error paths: unknown task completion, duplicate starts
  - Thread safety: concurrent record_task_start/complete/requeue, check_slo
  - Event emission: warning / breached events, healthy suppression, import failure
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
# Helpers
# ---------------------------------------------------------------------------


def _make_tight_slo(
    lane: WorkCellLane = WorkCellLane.api_simple,
    max_lead_time_seconds: int = 100,
    target_pass_rate: float = 0.90,
    max_requeue_count: int = 3,
    alert_threshold_pct: float = 0.80,
) -> dict[WorkCellLane, LaneSLO]:
    """Build a single-lane SLO dict with controllable thresholds for all lanes."""
    slos: dict[WorkCellLane, LaneSLO] = {}
    for wl in WorkCellLane:
        slos[wl] = LaneSLO(
            lane=wl,
            max_lead_time_seconds=max_lead_time_seconds,
            target_pass_rate=target_pass_rate,
            max_requeue_count=max_requeue_count,
            alert_threshold_pct=alert_threshold_pct,
        )
    return slos


def _fresh_monitor(**slo_kwargs) -> SLOMonitor:
    """Return a fresh SLOMonitor with optionally overridden SLO values."""
    if slo_kwargs:
        slos = _make_tight_slo(**slo_kwargs)
    else:
        slos = None
    return SLOMonitor(slos=slos)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure the global singleton is clean for every test."""
    reset_slo_monitor()
    yield
    reset_slo_monitor()


@pytest.fixture()
def monitor() -> SLOMonitor:
    """Fresh SLOMonitor with default SLOs."""
    return SLOMonitor()


@pytest.fixture()
def tight_monitor() -> SLOMonitor:
    """Monitor with tight, controllable SLOs (max_lead_time=100s, pass_rate=0.90)."""
    return _fresh_monitor()


# ===========================================================================
# SLOMonitor.__init__
# ===========================================================================


class TestInit:
    def test_default_slos_match_defaults(self, monitor: SLOMonitor):
        assert monitor._slos == dict(DEFAULT_LANE_SLOS)

    def test_custom_slos_injected(self):
        custom = _make_tight_slo(max_lead_time_seconds=42)
        m = SLOMonitor(slos=custom)
        assert m._slos[WorkCellLane.api_simple].max_lead_time_seconds == 42

    def test_starts_with_empty_state(self, monitor: SLOMonitor):
        assert monitor._start_times == {}
        assert monitor._records == []

    def test_requeue_counts_default_to_zero(self, monitor: SLOMonitor):
        # defaultdict — any lane should return 0 without explicit insertion
        assert monitor._requeue_counts[WorkCellLane.docs] == 0

    def test_lock_is_rlock(self, monitor: SLOMonitor):
        import threading

        assert isinstance(monitor._lock, type(threading.RLock()))


# ===========================================================================
# record_task_start
# ===========================================================================


class TestRecordTaskStart:
    def test_records_start_time(self, monitor: SLOMonitor):
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        assert "t1" in monitor._start_times

    def test_start_time_has_correct_lane(self, monitor: SLOMonitor):
        monitor.record_task_start("t2", WorkCellLane.docs)
        lane, _ = monitor._start_times["t2"]
        assert lane == WorkCellLane.docs

    def test_start_time_is_utc(self, monitor: SLOMonitor):
        before = datetime.now(UTC)
        monitor.record_task_start("t3", WorkCellLane.research)
        _, started_at = monitor._start_times["t3"]
        after = datetime.now(UTC)
        assert before <= started_at <= after

    def test_idempotent_duplicate_start_ignored(self, monitor: SLOMonitor):
        monitor.record_task_start("dup", WorkCellLane.api_simple)
        original_start = monitor._start_times["dup"][1]
        time.sleep(0.01)
        monitor.record_task_start("dup", WorkCellLane.docs)  # second call — ignored
        # Lane and timestamp must remain from the first call
        lane, started_at = monitor._start_times["dup"]
        assert lane == WorkCellLane.api_simple
        assert started_at == original_start

    def test_multiple_tasks_tracked_independently(self, monitor: SLOMonitor):
        for i in range(5):
            monitor.record_task_start(f"task-{i}", WorkCellLane.api_simple)
        assert len(monitor._start_times) == 5


# ===========================================================================
# record_task_complete
# ===========================================================================


class TestRecordTaskComplete:
    def test_returns_none_for_unknown_task(self, monitor: SLOMonitor):
        result = monitor.record_task_complete("never-started", passed=True)
        assert result is None

    def test_returns_task_record_on_success(self, monitor: SLOMonitor):
        monitor.record_task_start("c1", WorkCellLane.api_simple)
        record = monitor.record_task_complete("c1", passed=True)
        assert record is not None
        assert isinstance(record, _TaskRecord)

    def test_record_fields_populated(self, monitor: SLOMonitor):
        monitor.record_task_start("c2", WorkCellLane.docs)
        record = monitor.record_task_complete("c2", passed=False)
        assert record is not None
        assert record.task_id == "c2"
        assert record.lane == WorkCellLane.docs
        assert record.passed is False
        assert record.completed_at is not None
        assert record.lead_time_seconds is not None
        assert record.lead_time_seconds >= 0.0

    def test_lead_time_positive(self, monitor: SLOMonitor):
        monitor.record_task_start("c3", WorkCellLane.test_writing)
        time.sleep(0.01)
        record = monitor.record_task_complete("c3", passed=True)
        assert record is not None
        assert record.lead_time_seconds > 0.0

    def test_record_appended_to_records_list(self, monitor: SLOMonitor):
        monitor.record_task_start("c4", WorkCellLane.api_simple)
        monitor.record_task_complete("c4", passed=True)
        assert len(monitor._records) == 1
        assert monitor._records[0].task_id == "c4"

    def test_removes_task_from_start_times(self, monitor: SLOMonitor):
        monitor.record_task_start("c5", WorkCellLane.api_simple)
        monitor.record_task_complete("c5", passed=True)
        assert "c5" not in monitor._start_times

    def test_multiple_completions_accumulate(self, monitor: SLOMonitor):
        for i in range(5):
            monitor.record_task_start(f"m{i}", WorkCellLane.api_simple)
            monitor.record_task_complete(f"m{i}", passed=True)
        assert len(monitor._records) == 5

    def test_unknown_task_does_not_affect_records(self, monitor: SLOMonitor):
        monitor.record_task_complete("ghost", passed=True)
        assert len(monitor._records) == 0

    def test_calls_check_slo_and_emit_after_completion(self, monitor: SLOMonitor):
        """Verify check_slo + _emit_if_needed are called after recording."""
        monitor.record_task_start("ce1", WorkCellLane.api_simple)
        with patch.object(monitor, "check_slo", wraps=monitor.check_slo) as mock_check:
            with patch.object(monitor, "_emit_if_needed") as mock_emit:
                monitor.record_task_complete("ce1", passed=True)
                mock_check.assert_called_once_with(WorkCellLane.api_simple)
                mock_emit.assert_called_once()


# ===========================================================================
# record_requeue
# ===========================================================================


class TestRecordRequeue:
    def test_increments_requeue_count(self, monitor: SLOMonitor):
        monitor.record_requeue(WorkCellLane.api_simple)
        assert monitor._requeue_counts[WorkCellLane.api_simple] == 1

    def test_multiple_requeues_accumulate(self, monitor: SLOMonitor):
        for _ in range(5):
            monitor.record_requeue(WorkCellLane.research)
        assert monitor._requeue_counts[WorkCellLane.research] == 5

    def test_requeue_independent_per_lane(self, monitor: SLOMonitor):
        monitor.record_requeue(WorkCellLane.docs)
        monitor.record_requeue(WorkCellLane.docs)
        monitor.record_requeue(WorkCellLane.refactor)
        assert monitor._requeue_counts[WorkCellLane.docs] == 2
        assert monitor._requeue_counts[WorkCellLane.refactor] == 1

    def test_fresh_lane_starts_at_zero(self, monitor: SLOMonitor):
        assert monitor._requeue_counts[WorkCellLane.deployment] == 0


# ===========================================================================
# check_slo — no data
# ===========================================================================


class TestCheckSloNoData:
    def test_healthy_when_no_records(self, monitor: SLOMonitor):
        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.healthy

    def test_none_metrics_when_no_records(self, monitor: SLOMonitor):
        result = monitor.check_slo(WorkCellLane.docs)
        assert result.current_lead_time_seconds is None
        assert result.current_pass_rate is None

    def test_zero_requeue_when_no_requeues(self, monitor: SLOMonitor):
        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.requeue_count == 0

    def test_lane_field_matches_requested_lane(self, monitor: SLOMonitor):
        result = monitor.check_slo(WorkCellLane.security_change)
        assert result.lane == WorkCellLane.security_change

    def test_returns_slo_check_result_type(self, monitor: SLOMonitor):
        result = monitor.check_slo(WorkCellLane.api_simple)
        assert isinstance(result, SLOCheckResult)

    def test_checked_at_is_set(self, monitor: SLOMonitor):
        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.checked_at is not None
        assert len(result.checked_at) > 0

    def test_breached_when_requeue_exceeds_limit_with_no_records(self):
        """Requeue breach on a lane with no task records is still a breach."""
        # security_change has max_requeue_count=0
        m = SLOMonitor()
        m.record_requeue(WorkCellLane.security_change)
        result = m.check_slo(WorkCellLane.security_change)
        assert result.status == SLOStatus.breached


# ===========================================================================
# check_slo — status transitions
# ===========================================================================


class TestCheckSloStatusTransitions:
    """All status paths: healthy, warning (lead time), warning (pass rate), breached."""

    # --- Healthy ---

    def test_healthy_when_all_metrics_good(self):
        # max_lead_time=100s, target_pass_rate=0.90, alert_threshold=0.80
        # avg_lead_time=50s (< 80% of 100), pass_rate=1.0 (> warn floor)
        m = _fresh_monitor(max_lead_time_seconds=100, target_pass_rate=0.90, alert_threshold_pct=0.80)
        m.record_task_start("h1", WorkCellLane.api_simple)
        time.sleep(0.01)
        m.record_task_complete("h1", passed=True)
        # Override lead time to be squarely within healthy zone
        m._records[-1] = m._records[-1]._replace(lead_time_seconds=50.0)
        result = m.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.healthy

    # --- Warning: lead time ---

    def test_warning_when_lead_time_exceeds_alert_threshold(self):
        # max=100, alert_threshold=0.80 -> warn when avg > 80
        m = _fresh_monitor(
            max_lead_time_seconds=100,
            target_pass_rate=0.90,
            max_requeue_count=5,
            alert_threshold_pct=0.80,
        )
        m.record_task_start("w1", WorkCellLane.api_simple)
        m.record_task_complete("w1", passed=True)
        m._records[-1] = m._records[-1]._replace(lead_time_seconds=85.0)  # > 80, <= 100
        result = m.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.warning

    def test_warning_at_exact_alert_threshold_boundary(self):
        # avg_lead_time == 80.0 (exactly 80% of 100) -> NOT a warning (must be strictly >)
        m = _fresh_monitor(
            max_lead_time_seconds=100,
            target_pass_rate=0.90,
            max_requeue_count=5,
            alert_threshold_pct=0.80,
        )
        m.record_task_start("wb", WorkCellLane.api_simple)
        m.record_task_complete("wb", passed=True)
        m._records[-1] = m._records[-1]._replace(lead_time_seconds=80.0)
        result = m.check_slo(WorkCellLane.api_simple)
        # 80.0 is not > 80.0, so healthy
        assert result.status == SLOStatus.healthy

    # --- Warning: pass rate ---

    def test_warning_when_pass_rate_below_warn_floor(self):
        # target=0.90, alert_threshold=0.80
        # warn_floor = 0.90 + (1-0.90)*(1-0.80) = 0.90 + 0.10*0.20 = 0.92
        # pass_rate=0.91 is below 0.92 (warn floor) but above 0.90 (breach floor) -> warning
        m = _fresh_monitor(
            max_lead_time_seconds=1000,
            target_pass_rate=0.90,
            max_requeue_count=100,
            alert_threshold_pct=0.80,
        )
        for i in range(100):
            m.record_task_start(f"pr-{i}", WorkCellLane.api_simple)
            passed = i < 91  # 91 pass out of 100 = 0.91 pass rate
            m.record_task_complete(f"pr-{i}", passed=passed)
        # Override all lead times to be safe
        for i, rec in enumerate(m._records):
            m._records[i] = rec._replace(lead_time_seconds=1.0)
        result = m.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.warning

    def test_healthy_when_pass_rate_above_warn_floor(self):
        # warn_floor = 0.92 (from above calculation)
        # pass_rate=1.0 is above warn_floor -> healthy
        m = _fresh_monitor(
            max_lead_time_seconds=1000,
            target_pass_rate=0.90,
            max_requeue_count=100,
            alert_threshold_pct=0.80,
        )
        for i in range(10):
            m.record_task_start(f"hpr-{i}", WorkCellLane.api_simple)
            m.record_task_complete(f"hpr-{i}", passed=True)
        for i, rec in enumerate(m._records):
            m._records[i] = rec._replace(lead_time_seconds=1.0)
        result = m.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.healthy

    # --- Breached: lead time ---

    def test_breached_when_lead_time_exceeds_max(self):
        m = _fresh_monitor(
            max_lead_time_seconds=100,
            target_pass_rate=0.90,
            max_requeue_count=10,
            alert_threshold_pct=0.80,
        )
        m.record_task_start("b1", WorkCellLane.api_simple)
        m.record_task_complete("b1", passed=True)
        m._records[-1] = m._records[-1]._replace(lead_time_seconds=150.0)
        result = m.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.breached

    def test_breached_at_exact_max_lead_time_boundary(self):
        # avg_lead_time == 100.0 is NOT a breach (must be strictly >)
        m = _fresh_monitor(
            max_lead_time_seconds=100,
            target_pass_rate=0.90,
            max_requeue_count=10,
            alert_threshold_pct=0.80,
        )
        m.record_task_start("bb", WorkCellLane.api_simple)
        m.record_task_complete("bb", passed=True)
        m._records[-1] = m._records[-1]._replace(lead_time_seconds=100.0)
        result = m.check_slo(WorkCellLane.api_simple)
        # 100.0 is not > 100 -> not breached via lead time
        assert result.status != SLOStatus.breached

    # --- Breached: pass rate ---

    def test_breached_when_pass_rate_below_target(self):
        # target_pass_rate=0.90 — 8/10 = 0.80 < 0.90 -> breach
        m = _fresh_monitor(
            max_lead_time_seconds=1000,
            target_pass_rate=0.90,
            max_requeue_count=100,
            alert_threshold_pct=0.80,
        )
        for i in range(10):
            m.record_task_start(f"bp-{i}", WorkCellLane.api_simple)
            m.record_task_complete(f"bp-{i}", passed=i < 8)  # 8/10 pass
        for i, rec in enumerate(m._records):
            m._records[i] = rec._replace(lead_time_seconds=1.0)
        result = m.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.breached

    # --- Breached: requeue count ---

    def test_breached_when_requeue_count_exceeds_max(self):
        m = _fresh_monitor(max_requeue_count=3)
        for _ in range(4):
            m.record_requeue(WorkCellLane.api_simple)
        m.record_task_start("rq1", WorkCellLane.api_simple)
        m.record_task_complete("rq1", passed=True)
        m._records[-1] = m._records[-1]._replace(lead_time_seconds=1.0)
        result = m.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.breached

    def test_not_breached_at_exact_max_requeue_count(self):
        # max_requeue_count=3, requeue_count=3 -> NOT breached (must be strictly >)
        m = _fresh_monitor(max_requeue_count=3)
        for _ in range(3):
            m.record_requeue(WorkCellLane.api_simple)
        m.record_task_start("rq-exact", WorkCellLane.api_simple)
        m.record_task_complete("rq-exact", passed=True)
        m._records[-1] = m._records[-1]._replace(lead_time_seconds=1.0)
        result = m.check_slo(WorkCellLane.api_simple)
        # requeue_count == max_requeue_count is not > -> not a requeue breach
        assert result.status != SLOStatus.breached

    # --- Metrics in result ---

    def test_result_contains_avg_lead_time(self):
        m = SLOMonitor()
        m.record_task_start("lt1", WorkCellLane.api_simple)
        m.record_task_complete("lt1", passed=True)
        m._records[-1] = m._records[-1]._replace(lead_time_seconds=42.0)
        result = m.check_slo(WorkCellLane.api_simple)
        assert result.current_lead_time_seconds == pytest.approx(42.0)

    def test_result_contains_avg_lead_time_across_multiple(self):
        m = SLOMonitor()
        for lead_time in [10.0, 20.0, 30.0]:
            task_id = f"avg-{lead_time}"
            m.record_task_start(task_id, WorkCellLane.api_simple)
            m.record_task_complete(task_id, passed=True)
            m._records[-1] = m._records[-1]._replace(lead_time_seconds=lead_time)
        result = m.check_slo(WorkCellLane.api_simple)
        assert result.current_lead_time_seconds == pytest.approx(20.0)  # (10+20+30)/3

    def test_result_contains_correct_pass_rate(self):
        m = SLOMonitor()
        for i in range(4):
            m.record_task_start(f"pas-{i}", WorkCellLane.api_simple)
            m.record_task_complete(f"pas-{i}", passed=i < 3)  # 3/4 = 0.75
        result = m.check_slo(WorkCellLane.api_simple)
        assert result.current_pass_rate == pytest.approx(0.75)

    def test_result_contains_correct_requeue_count(self):
        m = SLOMonitor()
        m.record_requeue(WorkCellLane.api_simple)
        m.record_requeue(WorkCellLane.api_simple)
        m.record_task_start("rqc", WorkCellLane.api_simple)
        m.record_task_complete("rqc", passed=True)
        result = m.check_slo(WorkCellLane.api_simple)
        assert result.requeue_count == 2

    def test_records_with_none_lead_time_excluded_from_avg(self):
        """Records with None lead_time_seconds are excluded from the average."""
        m = SLOMonitor()
        from forge_harness.webhook_server.services.slo_monitor import _TaskRecord

        # Inject a record with None lead time directly
        m._records.append(
            _TaskRecord(
                task_id="none-lt",
                lane=WorkCellLane.api_simple,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                passed=True,
                lead_time_seconds=None,
            )
        )
        m._records.append(
            _TaskRecord(
                task_id="valid-lt",
                lane=WorkCellLane.api_simple,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                passed=True,
                lead_time_seconds=20.0,
            )
        )
        result = m.check_slo(WorkCellLane.api_simple)
        assert result.current_lead_time_seconds == pytest.approx(20.0)

    def test_all_none_lead_times_yields_none_avg(self):
        """If all records have None lead_time_seconds, avg should be None."""
        m = SLOMonitor()
        from forge_harness.webhook_server.services.slo_monitor import _TaskRecord

        m._records.append(
            _TaskRecord(
                task_id="none-only",
                lane=WorkCellLane.api_simple,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                passed=True,
                lead_time_seconds=None,
            )
        )
        result = m.check_slo(WorkCellLane.api_simple)
        assert result.current_lead_time_seconds is None


# ===========================================================================
# _classify_status (static method — tested directly)
# ===========================================================================


class TestClassifyStatus:
    """Exercise every branch of the static helper."""

    def _slo(self) -> LaneSLO:
        return LaneSLO(
            lane=WorkCellLane.api_simple,
            max_lead_time_seconds=100,
            target_pass_rate=0.90,
            max_requeue_count=3,
            alert_threshold_pct=0.80,
        )

    def test_healthy_all_none_metrics(self):
        result = SLOMonitor._classify_status(self._slo(), None, None, 0)
        assert result == SLOStatus.healthy

    def test_breach_lead_time(self):
        result = SLOMonitor._classify_status(self._slo(), 150.0, 1.0, 0)
        assert result == SLOStatus.breached

    def test_breach_pass_rate(self):
        result = SLOMonitor._classify_status(self._slo(), 1.0, 0.50, 0)
        assert result == SLOStatus.breached

    def test_breach_requeue_count(self):
        result = SLOMonitor._classify_status(self._slo(), None, None, 4)  # > 3
        assert result == SLOStatus.breached

    def test_warning_lead_time(self):
        # 85 > 80 (alert threshold) but <= 100 (max)
        result = SLOMonitor._classify_status(self._slo(), 85.0, 1.0, 0)
        assert result == SLOStatus.warning

    def test_warning_pass_rate(self):
        # warn_floor = 0.90 + 0.10 * 0.20 = 0.92
        # 0.91 is below warn_floor but above target
        result = SLOMonitor._classify_status(self._slo(), 1.0, 0.91, 0)
        assert result == SLOStatus.warning

    def test_healthy_exact_target_pass_rate(self):
        # pass_rate == warn_floor triggers warning, pass_rate > warn_floor is healthy
        # warn_floor = 0.92, pass_rate = 0.95 -> healthy
        result = SLOMonitor._classify_status(self._slo(), 1.0, 0.95, 0)
        assert result == SLOStatus.healthy

    def test_healthy_none_lead_time_and_good_pass_rate(self):
        result = SLOMonitor._classify_status(self._slo(), None, 1.0, 0)
        assert result == SLOStatus.healthy

    def test_breach_takes_priority_over_warning(self):
        # Both lead time and pass rate breach — result must be breached, not warning
        result = SLOMonitor._classify_status(self._slo(), 200.0, 0.50, 0)
        assert result == SLOStatus.breached


# ===========================================================================
# check_all_slos
# ===========================================================================


class TestCheckAllSlos:
    def test_returns_one_result_per_lane(self, monitor: SLOMonitor):
        results = monitor.check_all_slos()
        assert len(results) == len(WorkCellLane)

    def test_all_healthy_on_empty_monitor(self, monitor: SLOMonitor):
        for result in monitor.check_all_slos():
            assert result.status == SLOStatus.healthy

    def test_results_in_lane_enum_order(self, monitor: SLOMonitor):
        results = monitor.check_all_slos()
        for expected_lane, result in zip(WorkCellLane, results):
            assert result.lane == expected_lane

    def test_reflects_recorded_data(self):
        m = _fresh_monitor(
            max_lead_time_seconds=100,
            target_pass_rate=0.90,
            max_requeue_count=10,
            alert_threshold_pct=0.80,
        )
        # Breach api_simple with a bad lead time
        m.record_task_start("cas1", WorkCellLane.api_simple)
        m.record_task_complete("cas1", passed=True)
        m._records[-1] = m._records[-1]._replace(lead_time_seconds=200.0)

        results = m.check_all_slos()
        api_simple_result = next(r for r in results if r.lane == WorkCellLane.api_simple)
        assert api_simple_result.status == SLOStatus.breached


# ===========================================================================
# get_breaches
# ===========================================================================


class TestGetBreaches:
    def test_empty_monitor_returns_no_breaches(self, monitor: SLOMonitor):
        assert monitor.get_breaches() == []

    def test_returns_only_breached_lanes(self):
        m = _fresh_monitor(
            max_lead_time_seconds=100,
            target_pass_rate=0.90,
            max_requeue_count=10,
            alert_threshold_pct=0.80,
        )
        # Only api_simple gets a long lead time
        m.record_task_start("gb1", WorkCellLane.api_simple)
        m.record_task_complete("gb1", passed=True)
        m._records[-1] = m._records[-1]._replace(lead_time_seconds=999.0)

        breaches = m.get_breaches()
        assert len(breaches) == 1
        assert breaches[0].lane == WorkCellLane.api_simple
        assert breaches[0].status == SLOStatus.breached

    def test_warning_lanes_excluded_from_breaches(self):
        m = _fresh_monitor(
            max_lead_time_seconds=100,
            target_pass_rate=0.90,
            max_requeue_count=10,
            alert_threshold_pct=0.80,
        )
        # api_simple gets a warning-level lead time (85s)
        m.record_task_start("wb1", WorkCellLane.api_simple)
        m.record_task_complete("wb1", passed=True)
        m._records[-1] = m._records[-1]._replace(lead_time_seconds=85.0)

        breaches = m.get_breaches()
        assert all(r.lane != WorkCellLane.api_simple for r in breaches)

    def test_window_filters_old_records(self):
        m = SLOMonitor()
        # Add a record with completed_at in the distant past
        from forge_harness.webhook_server.services.slo_monitor import _TaskRecord

        old_time = datetime.now(UTC) - timedelta(hours=2)
        m._records.append(
            _TaskRecord(
                task_id="old-task",
                lane=WorkCellLane.api_simple,
                started_at=old_time - timedelta(seconds=1000),
                completed_at=old_time,
                passed=False,
                lead_time_seconds=9999.0,  # Would breach if included
            )
        )
        # With a 60-minute window, the old record is excluded
        breaches = m.get_breaches(window_minutes=60)
        assert all(b.lane != WorkCellLane.api_simple for b in breaches)

    def test_window_includes_recent_records(self):
        m = _fresh_monitor(
            max_lead_time_seconds=100,
            target_pass_rate=0.90,
            max_requeue_count=10,
            alert_threshold_pct=0.80,
        )
        m.record_task_start("recent", WorkCellLane.api_simple)
        m.record_task_complete("recent", passed=True)
        m._records[-1] = m._records[-1]._replace(lead_time_seconds=999.0)

        breaches = m.get_breaches(window_minutes=60)
        assert len(breaches) == 1
        assert breaches[0].lane == WorkCellLane.api_simple

    def test_zero_window_minutes_uses_default_60(self):
        """window_minutes <= 0 is replaced with 60."""
        m = SLOMonitor()
        # This should not raise
        result = m.get_breaches(window_minutes=0)
        assert isinstance(result, list)

    def test_negative_window_minutes_uses_default_60(self):
        m = SLOMonitor()
        result = m.get_breaches(window_minutes=-99)
        assert isinstance(result, list)

    def test_requeue_breach_included_in_window_with_no_records(self):
        """Requeue count exceeding max is included even when there are no records in window."""
        m = SLOMonitor()
        # security_change has max_requeue_count=0
        m.record_requeue(WorkCellLane.security_change)
        breaches = m.get_breaches()
        lanes = [b.lane for b in breaches]
        assert WorkCellLane.security_change in lanes

    def test_no_breach_when_only_records_outside_window(self):
        """Lane with only out-of-window records and no requeue excess is not included."""
        m = SLOMonitor()
        from forge_harness.webhook_server.services.slo_monitor import _TaskRecord

        old_time = datetime.now(UTC) - timedelta(hours=3)
        m._records.append(
            _TaskRecord(
                task_id="old-only",
                lane=WorkCellLane.docs,
                started_at=old_time,
                completed_at=old_time,
                passed=False,
                lead_time_seconds=9999.0,
            )
        )
        breaches = m.get_breaches(window_minutes=60)
        assert all(b.lane != WorkCellLane.docs for b in breaches)

    def test_breach_result_contains_correct_metrics(self):
        m = _fresh_monitor(
            max_lead_time_seconds=100,
            target_pass_rate=0.90,
            max_requeue_count=10,
            alert_threshold_pct=0.80,
        )
        m.record_task_start("bm1", WorkCellLane.api_simple)
        m.record_task_complete("bm1", passed=False)  # pass rate 0.0 -> breach
        m._records[-1] = m._records[-1]._replace(lead_time_seconds=1.0)

        breaches = m.get_breaches()
        assert len(breaches) >= 1
        b = next(b for b in breaches if b.lane == WorkCellLane.api_simple)
        assert b.current_pass_rate == pytest.approx(0.0)


# ===========================================================================
# _emit_if_needed
# ===========================================================================


class TestEmitIfNeeded:
    """Tests for _emit_if_needed.

    _emit_if_needed uses deferred local imports inside a try/except block.
    We patch the imported function at its source module path so that Python's
    import machinery picks up the mock when the lazy import runs.
    """

    _EMITTER_MODULE = "forge_harness.webhook_server.services.event_emitter"

    def _make_result(self, status: SLOStatus, lane: WorkCellLane = WorkCellLane.api_simple) -> SLOCheckResult:
        return SLOCheckResult(
            lane=lane,
            status=status,
            current_lead_time_seconds=1.0,
            current_pass_rate=0.95,
            requeue_count=0,
        )

    def test_healthy_status_does_not_emit(self, monitor: SLOMonitor):
        """Healthy results must short-circuit before any import or emit call."""
        mock_emitter = MagicMock()
        with patch(f"{self._EMITTER_MODULE}.get_event_emitter", return_value=mock_emitter):
            monitor._emit_if_needed(self._make_result(SLOStatus.healthy))
            mock_emitter.emit.assert_not_called()

    def test_warning_emits_task_slo_warning(self, monitor: SLOMonitor):
        mock_emitter = MagicMock()
        with patch(f"{self._EMITTER_MODULE}.get_event_emitter", return_value=mock_emitter):
            from forge_harness.webhook_server.models.sse_events import SSEEventType

            monitor._emit_if_needed(self._make_result(SLOStatus.warning))
            mock_emitter.emit.assert_called_once()
            call_kwargs = mock_emitter.emit.call_args
            assert call_kwargs.kwargs["event_type"] == SSEEventType.task_slo_warning

    def test_breached_emits_task_slo_breached(self, monitor: SLOMonitor):
        mock_emitter = MagicMock()
        with patch(f"{self._EMITTER_MODULE}.get_event_emitter", return_value=mock_emitter):
            from forge_harness.webhook_server.models.sse_events import SSEEventType

            monitor._emit_if_needed(self._make_result(SLOStatus.breached))
            mock_emitter.emit.assert_called_once()
            call_kwargs = mock_emitter.emit.call_args
            assert call_kwargs.kwargs["event_type"] == SSEEventType.task_slo_breached

    def test_emit_data_contains_expected_fields(self, monitor: SLOMonitor):
        mock_emitter = MagicMock()
        with patch(f"{self._EMITTER_MODULE}.get_event_emitter", return_value=mock_emitter):
            result = self._make_result(SLOStatus.warning)
            monitor._emit_if_needed(result)
            data_arg = mock_emitter.emit.call_args.kwargs["data"]
            assert "lane" in data_arg
            assert "status" in data_arg
            assert "requeue_count" in data_arg
            assert "checked_at" in data_arg

    def test_emit_source_is_slo_monitor(self, monitor: SLOMonitor):
        mock_emitter = MagicMock()
        with patch(f"{self._EMITTER_MODULE}.get_event_emitter", return_value=mock_emitter):
            monitor._emit_if_needed(self._make_result(SLOStatus.breached))
            source_arg = mock_emitter.emit.call_args.kwargs["source"]
            assert source_arg == "slo_monitor"

    def test_exception_in_emit_is_swallowed(self, monitor: SLOMonitor):
        """Any exception raised by emit() must not propagate out of _emit_if_needed."""
        mock_emitter = MagicMock()
        mock_emitter.emit.side_effect = RuntimeError("emitter exploded")
        with patch(f"{self._EMITTER_MODULE}.get_event_emitter", return_value=mock_emitter):
            # Must not raise
            monitor._emit_if_needed(self._make_result(SLOStatus.warning))

    def test_exception_in_get_emitter_is_swallowed(self, monitor: SLOMonitor):
        """RuntimeError from get_event_emitter() is caught by the bare except."""
        with patch(
            f"{self._EMITTER_MODULE}.get_event_emitter",
            side_effect=RuntimeError("emitter unavailable"),
        ):
            # Must not raise
            monitor._emit_if_needed(self._make_result(SLOStatus.warning))

    def test_import_error_is_swallowed(self, monitor: SLOMonitor):
        """ImportError during lazy import is caught and silently logged."""
        with patch.dict("sys.modules", {"forge_harness.webhook_server.models.sse_events": None}):
            # Must not raise
            monitor._emit_if_needed(self._make_result(SLOStatus.warning))

    def test_emit_triggered_from_record_task_complete(self):
        """Integration: _emit_if_needed is reached via record_task_complete."""
        m = _fresh_monitor(
            max_lead_time_seconds=1000,
            target_pass_rate=0.99,
            max_requeue_count=100,
            alert_threshold_pct=0.80,
        )
        mock_emitter = MagicMock()
        with patch(f"{self._EMITTER_MODULE}.get_event_emitter", return_value=mock_emitter):
            m.record_task_start("trig1", WorkCellLane.api_simple)
            m.record_task_complete("trig1", passed=False)
            # passed=False at pass_rate=0.0 < 0.99 target -> breach -> emit called
            assert mock_emitter.emit.called


# ===========================================================================
# reset
# ===========================================================================


class TestReset:
    def test_clears_start_times(self, monitor: SLOMonitor):
        monitor.record_task_start("r1", WorkCellLane.api_simple)
        monitor.reset()
        assert monitor._start_times == {}

    def test_clears_records(self, monitor: SLOMonitor):
        monitor.record_task_start("r2", WorkCellLane.api_simple)
        monitor.record_task_complete("r2", passed=True)
        monitor.reset()
        assert monitor._records == []

    def test_clears_requeue_counts(self, monitor: SLOMonitor):
        monitor.record_requeue(WorkCellLane.docs)
        monitor.reset()
        assert monitor._requeue_counts[WorkCellLane.docs] == 0

    def test_all_lanes_healthy_after_reset(self, monitor: SLOMonitor):
        monitor.record_task_start("r3", WorkCellLane.api_simple)
        monitor.record_task_complete("r3", passed=False)
        monitor.reset()
        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.healthy
        assert result.current_lead_time_seconds is None
        assert result.current_pass_rate is None

    def test_reset_does_not_change_slos(self, monitor: SLOMonitor):
        original_slos = dict(monitor._slos)
        monitor.reset()
        assert monitor._slos == original_slos


# ===========================================================================
# Singleton: get_slo_monitor / reset_slo_monitor
# ===========================================================================


class TestSingleton:
    def test_returns_slo_monitor_instance(self):
        m = get_slo_monitor()
        assert isinstance(m, SLOMonitor)

    def test_same_instance_on_repeated_calls(self):
        m1 = get_slo_monitor()
        m2 = get_slo_monitor()
        assert m1 is m2

    def test_reset_destroys_instance(self):
        m1 = get_slo_monitor()
        reset_slo_monitor()
        m2 = get_slo_monitor()
        assert m1 is not m2

    def test_reset_sets_module_var_to_none(self):
        import forge_harness.webhook_server.services.slo_monitor as mod

        get_slo_monitor()
        assert mod._monitor_instance is not None
        reset_slo_monitor()
        assert mod._monitor_instance is None

    def test_fresh_instance_after_reset_starts_clean(self):
        m1 = get_slo_monitor()
        m1.record_task_start("sg1", WorkCellLane.api_simple)
        reset_slo_monitor()
        m2 = get_slo_monitor()
        assert "sg1" not in m2._start_times

    def test_lazy_creation_on_first_call(self):
        import forge_harness.webhook_server.services.slo_monitor as mod

        reset_slo_monitor()
        assert mod._monitor_instance is None
        get_slo_monitor()
        assert mod._monitor_instance is not None

    def test_thread_safe_singleton_creation(self):
        """All concurrent callers get the same instance."""
        instances: list[SLOMonitor] = []
        errors: list[Exception] = []

        def getter():
            try:
                instances.append(get_slo_monitor())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=getter) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(inst is instances[0] for inst in instances)


# ===========================================================================
# Thread safety
# ===========================================================================


class TestThreadSafety:
    def test_concurrent_start_and_complete_no_exceptions(self, monitor: SLOMonitor):
        errors: list[Exception] = []

        def worker(task_id: str):
            try:
                monitor.record_task_start(task_id, WorkCellLane.api_simple)
                time.sleep(0.001)
                monitor.record_task_complete(task_id, passed=True)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"ct-{i}",)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors

    def test_concurrent_requeue_increments_correctly(self, monitor: SLOMonitor):
        errors: list[Exception] = []

        def rq_worker():
            try:
                for _ in range(10):
                    monitor.record_requeue(WorkCellLane.docs)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=rq_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # 5 threads * 10 increments = 50
        assert monitor._requeue_counts[WorkCellLane.docs] == 50

    def test_concurrent_check_slo_no_exceptions(self, monitor: SLOMonitor):
        # Pre-populate some records
        for i in range(10):
            monitor.record_task_start(f"ccs-{i}", WorkCellLane.api_simple)
            monitor.record_task_complete(f"ccs-{i}", passed=True)

        errors: list[Exception] = []

        def check_worker():
            try:
                for _ in range(10):
                    monitor.check_slo(WorkCellLane.api_simple)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=check_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors

    def test_concurrent_start_complete_and_check_no_data_races(self, monitor: SLOMonitor):
        """Mix of writes and reads should never raise."""
        errors: list[Exception] = []

        def write_worker(prefix: str):
            try:
                for i in range(5):
                    tid = f"{prefix}-{i}"
                    monitor.record_task_start(tid, WorkCellLane.docs)
                    monitor.record_task_complete(tid, passed=True)
            except Exception as exc:
                errors.append(exc)

        def read_worker():
            try:
                for _ in range(20):
                    monitor.check_slo(WorkCellLane.docs)
            except Exception as exc:
                errors.append(exc)

        threads = (
            [threading.Thread(target=write_worker, args=(f"w{i}",)) for i in range(4)]
            + [threading.Thread(target=read_worker) for _ in range(3)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors

    def test_concurrent_reset_and_record_no_exceptions(self, monitor: SLOMonitor):
        errors: list[Exception] = []

        def record_worker(prefix: str):
            try:
                for i in range(5):
                    tid = f"{prefix}-{i}"
                    monitor.record_task_start(tid, WorkCellLane.api_simple)
                    monitor.record_task_complete(tid, passed=True)
            except Exception as exc:
                errors.append(exc)

        def reset_worker():
            try:
                for _ in range(3):
                    monitor.reset()
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=record_worker, args=("rw",)),
            threading.Thread(target=reset_worker),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    def test_single_task_all_metrics_computed(self, monitor: SLOMonitor):
        monitor.record_task_start("single", WorkCellLane.api_simple)
        monitor.record_task_complete("single", passed=True)
        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.current_lead_time_seconds is not None
        assert result.current_pass_rate == pytest.approx(1.0)
        assert result.requeue_count == 0

    def test_all_failures_yields_zero_pass_rate(self, monitor: SLOMonitor):
        for i in range(3):
            monitor.record_task_start(f"fail-{i}", WorkCellLane.api_simple)
            monitor.record_task_complete(f"fail-{i}", passed=False)
        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.current_pass_rate == pytest.approx(0.0)

    def test_lane_isolation_different_lanes_do_not_affect_each_other(self, monitor: SLOMonitor):
        monitor.record_task_start("doc1", WorkCellLane.docs)
        monitor.record_task_complete("doc1", passed=True)
        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.current_lead_time_seconds is None
        assert result.current_pass_rate is None

    def test_check_all_slos_on_large_record_set(self, monitor: SLOMonitor):
        for i in range(50):
            monitor.record_task_start(f"large-{i}", WorkCellLane.test_writing)
            monitor.record_task_complete(f"large-{i}", passed=True)
        results = monitor.check_all_slos()
        assert len(results) == len(WorkCellLane)

    def test_default_slos_none_constructor(self):
        """Passing slos=None explicitly should use DEFAULT_LANE_SLOS."""
        m = SLOMonitor(slos=None)
        assert m._slos == dict(DEFAULT_LANE_SLOS)

    def test_very_high_requeue_count_still_breaches(self, monitor: SLOMonitor):
        for _ in range(1000):
            monitor.record_requeue(WorkCellLane.security_change)
        result = monitor.check_slo(WorkCellLane.security_change)
        assert result.status == SLOStatus.breached
        assert result.requeue_count == 1000

    def test_task_record_is_named_tuple(self, monitor: SLOMonitor):
        monitor.record_task_start("nt1", WorkCellLane.api_simple)
        record = monitor.record_task_complete("nt1", passed=True)
        assert record is not None
        assert hasattr(record, "_fields")
        assert "task_id" in record._fields
        assert "lane" in record._fields
        assert "lead_time_seconds" in _TaskRecord._fields

    def test_check_slo_for_all_lanes_individually(self, monitor: SLOMonitor):
        for lane in WorkCellLane:
            result = monitor.check_slo(lane)
            assert result.lane == lane
            assert result.status == SLOStatus.healthy

    def test_completed_at_is_utc_aware(self, monitor: SLOMonitor):
        monitor.record_task_start("utc-check", WorkCellLane.api_simple)
        record = monitor.record_task_complete("utc-check", passed=True)
        assert record is not None
        assert record.completed_at is not None
        assert record.completed_at.tzinfo is not None

    def test_get_breaches_no_completed_at_in_record_is_skipped(self):
        """A record with completed_at=None is treated as outside any window."""
        m = SLOMonitor()
        from forge_harness.webhook_server.services.slo_monitor import _TaskRecord

        m._records.append(
            _TaskRecord(
                task_id="no-completed",
                lane=WorkCellLane.api_simple,
                started_at=datetime.now(UTC),
                completed_at=None,
                passed=False,
                lead_time_seconds=99999.0,
            )
        )
        breaches = m.get_breaches(window_minutes=60)
        # The None completed_at means it is excluded from the windowed check
        assert all(b.lane != WorkCellLane.api_simple for b in breaches)
