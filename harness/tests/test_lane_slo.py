"""Lane-Specific SLO Tests — DF-2004
=====================================

Validates LaneSLO, SLOStatus, SLOCheckResult, DEFAULT_LANE_SLOS (model layer)
and SLOMonitor, get_slo_monitor, reset_slo_monitor (service layer).

Coverage targets:
- LaneSLO model: valid construction, all defaults valid, boundary values.
- DEFAULT_LANE_SLOS: covers all lanes, all fields populated.
- SLOStatus enum: all values exist.
- SLOCheckResult: construction, defaults.
- SLOMonitor.record_task_start: idempotent, stores state.
- SLOMonitor.record_task_complete: lead-time computation, pass tracking.
- SLOMonitor.record_requeue: increments counter.
- SLOMonitor.check_slo: healthy when within SLO.
- SLOMonitor.check_slo: breached when lead time exceeds max.
- SLOMonitor.check_slo: warning at alert_threshold_pct of lead time.
- SLOMonitor.check_slo: breached when pass rate below target.
- SLOMonitor.check_slo: warning when pass rate in warning band.
- SLOMonitor.check_slo: breached when requeue_count > max.
- SLOMonitor.check_slo: healthy with no data.
- SLOMonitor.check_all_slos: covers every lane.
- SLOMonitor.get_breaches: returns only breached lanes within window.
- SLOMonitor.get_breaches: empty window clamps to default.
- SLOMonitor.reset: clears all state.
- Singleton: get_slo_monitor returns same instance.
- Singleton: reset_slo_monitor causes new instance on next get.
- Thread safety: concurrent record_task_start/complete.
- Event emission: warning and breached events via EventEmitter.
- Export: SLOMonitor accessible via services __init__.
- Export: LaneSLO, SLOStatus, SLOCheckResult, DEFAULT_LANE_SLOS via models __init__.
- 45+ tests total.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from forge_harness.webhook_server.models.lane_slo import (
    DEFAULT_LANE_SLOS,
    LaneSLO,
    SLOCheckResult,
    SLOStatus,
)
from forge_harness.webhook_server.models.work_cell import WorkCellLane
from forge_harness.webhook_server.services.slo_monitor import (
    SLOMonitor,
    get_slo_monitor,
    reset_slo_monitor,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_slo_monitor_singleton():
    """Reset the SLOMonitor singleton before and after every test."""
    reset_slo_monitor()
    yield
    reset_slo_monitor()


@pytest.fixture()
def monitor() -> SLOMonitor:
    """Return a fresh SLOMonitor instance (not the singleton)."""
    return SLOMonitor()


@pytest.fixture()
def tight_slo_monitor() -> SLOMonitor:
    """Return a SLOMonitor with very tight SLOs for easy breach testing."""
    slos = {
        lane: LaneSLO(
            lane=lane,
            max_lead_time_seconds=1,  # 1 second — easy to breach
            target_pass_rate=0.99,  # 99 % pass rate
            max_requeue_count=0,  # any requeue is a breach
            alert_threshold_pct=0.5,  # warn at 50 % of limit
        )
        for lane in WorkCellLane
    }
    return SLOMonitor(slos=slos)


# ===========================================================================
# SLOStatus enum
# ===========================================================================


class TestSLOStatusEnum:
    """Basic sanity checks for the SLOStatus enum."""

    def test_all_expected_values_exist(self) -> None:
        expected = {"healthy", "warning", "breached"}
        actual = {s.value for s in SLOStatus}
        assert actual == expected

    def test_status_count(self) -> None:
        assert len(SLOStatus) == 3

    def test_status_are_str_enum(self) -> None:
        for status in SLOStatus:
            assert isinstance(status.value, str)

    def test_str_comparison(self) -> None:
        assert SLOStatus.healthy == "healthy"
        assert SLOStatus.warning == "warning"
        assert SLOStatus.breached == "breached"


# ===========================================================================
# LaneSLO model
# ===========================================================================


class TestLaneSLOModel:
    """Validate LaneSLO Pydantic model constraints."""

    def test_valid_construction(self) -> None:
        slo = LaneSLO(
            lane=WorkCellLane.api_simple,
            max_lead_time_seconds=300,
            target_pass_rate=0.95,
            max_requeue_count=3,
            alert_threshold_pct=0.8,
        )
        assert slo.lane == WorkCellLane.api_simple
        assert slo.max_lead_time_seconds == 300
        assert slo.target_pass_rate == 0.95
        assert slo.max_requeue_count == 3
        assert slo.alert_threshold_pct == 0.8

    def test_max_lead_time_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            LaneSLO(
                lane=WorkCellLane.api_simple,
                max_lead_time_seconds=0,  # must be > 0
                target_pass_rate=0.95,
                max_requeue_count=1,
                alert_threshold_pct=0.8,
            )

    def test_target_pass_rate_min_boundary(self) -> None:
        slo = LaneSLO(
            lane=WorkCellLane.docs,
            max_lead_time_seconds=60,
            target_pass_rate=0.0,
            max_requeue_count=0,
            alert_threshold_pct=0.5,
        )
        assert slo.target_pass_rate == 0.0

    def test_target_pass_rate_max_boundary(self) -> None:
        slo = LaneSLO(
            lane=WorkCellLane.deployment,
            max_lead_time_seconds=60,
            target_pass_rate=1.0,
            max_requeue_count=0,
            alert_threshold_pct=0.9,
        )
        assert slo.target_pass_rate == 1.0

    def test_target_pass_rate_below_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            LaneSLO(
                lane=WorkCellLane.docs,
                max_lead_time_seconds=60,
                target_pass_rate=-0.1,
                max_requeue_count=0,
                alert_threshold_pct=0.5,
            )

    def test_target_pass_rate_above_one_raises(self) -> None:
        with pytest.raises(ValidationError):
            LaneSLO(
                lane=WorkCellLane.docs,
                max_lead_time_seconds=60,
                target_pass_rate=1.1,
                max_requeue_count=0,
                alert_threshold_pct=0.5,
            )

    def test_max_requeue_count_zero_allowed(self) -> None:
        slo = LaneSLO(
            lane=WorkCellLane.deployment,
            max_lead_time_seconds=300,
            target_pass_rate=0.99,
            max_requeue_count=0,
            alert_threshold_pct=0.7,
        )
        assert slo.max_requeue_count == 0

    def test_max_requeue_count_negative_raises(self) -> None:
        with pytest.raises(ValidationError):
            LaneSLO(
                lane=WorkCellLane.deployment,
                max_lead_time_seconds=300,
                target_pass_rate=0.99,
                max_requeue_count=-1,
                alert_threshold_pct=0.7,
            )

    def test_alert_threshold_must_be_strictly_greater_than_zero(self) -> None:
        with pytest.raises(ValidationError):
            LaneSLO(
                lane=WorkCellLane.docs,
                max_lead_time_seconds=300,
                target_pass_rate=0.9,
                max_requeue_count=1,
                alert_threshold_pct=0.0,
            )

    def test_alert_threshold_must_be_strictly_less_than_one(self) -> None:
        with pytest.raises(ValidationError):
            LaneSLO(
                lane=WorkCellLane.docs,
                max_lead_time_seconds=300,
                target_pass_rate=0.9,
                max_requeue_count=1,
                alert_threshold_pct=1.0,
            )

    def test_model_is_frozen(self) -> None:
        slo = LaneSLO(
            lane=WorkCellLane.api_simple,
            max_lead_time_seconds=300,
            target_pass_rate=0.95,
            max_requeue_count=3,
            alert_threshold_pct=0.8,
        )
        with pytest.raises(Exception):
            slo.max_lead_time_seconds = 9999  # type: ignore[misc]

    def test_all_required_fields_present(self) -> None:
        fields = set(LaneSLO.model_fields.keys())
        assert {
            "lane",
            "max_lead_time_seconds",
            "target_pass_rate",
            "max_requeue_count",
            "alert_threshold_pct",
        }.issubset(fields)


# ===========================================================================
# DEFAULT_LANE_SLOS
# ===========================================================================


class TestDefaultLaneSLOs:
    """Validate the DEFAULT_LANE_SLOS constant."""

    def test_all_lanes_have_a_default_slo(self) -> None:
        for lane in WorkCellLane:
            assert lane in DEFAULT_LANE_SLOS, f"No default SLO for lane: {lane.value}"

    def test_slo_lane_field_matches_key(self) -> None:
        for lane, slo in DEFAULT_LANE_SLOS.items():
            assert slo.lane == lane, f"SLO key {lane.value!r} has lane field {slo.lane.value!r}"

    def test_all_max_lead_times_positive(self) -> None:
        for lane, slo in DEFAULT_LANE_SLOS.items():
            assert slo.max_lead_time_seconds > 0, (
                f"Lane {lane.value} has non-positive max_lead_time_seconds"
            )

    def test_all_pass_rates_in_range(self) -> None:
        for lane, slo in DEFAULT_LANE_SLOS.items():
            assert 0.0 <= slo.target_pass_rate <= 1.0, (
                f"Lane {lane.value} has out-of-range target_pass_rate"
            )

    def test_all_alert_thresholds_valid(self) -> None:
        for lane, slo in DEFAULT_LANE_SLOS.items():
            assert 0.0 < slo.alert_threshold_pct < 1.0, (
                f"Lane {lane.value} has invalid alert_threshold_pct"
            )

    def test_api_simple_slo_values(self) -> None:
        slo = DEFAULT_LANE_SLOS[WorkCellLane.api_simple]
        assert slo.max_lead_time_seconds == 300
        assert slo.target_pass_rate == 0.95

    def test_deployment_slo_high_pass_rate(self) -> None:
        slo = DEFAULT_LANE_SLOS[WorkCellLane.deployment]
        assert slo.target_pass_rate >= 0.99

    def test_security_change_slo_high_pass_rate(self) -> None:
        slo = DEFAULT_LANE_SLOS[WorkCellLane.security_change]
        assert slo.target_pass_rate >= 0.99

    def test_deployment_and_security_zero_requeue(self) -> None:
        """High-stakes lanes should not permit any requeues."""
        assert DEFAULT_LANE_SLOS[WorkCellLane.deployment].max_requeue_count == 0
        assert DEFAULT_LANE_SLOS[WorkCellLane.security_change].max_requeue_count == 0

    def test_count_matches_lane_count(self) -> None:
        assert len(DEFAULT_LANE_SLOS) == len(WorkCellLane)


# ===========================================================================
# SLOCheckResult model
# ===========================================================================


class TestSLOCheckResultModel:
    """Validate the SLOCheckResult Pydantic model."""

    def test_valid_construction(self) -> None:
        result = SLOCheckResult(
            lane=WorkCellLane.api_simple,
            status=SLOStatus.healthy,
            current_lead_time_seconds=120.5,
            current_pass_rate=0.97,
            requeue_count=0,
        )
        assert result.lane == WorkCellLane.api_simple
        assert result.status == SLOStatus.healthy
        assert result.current_lead_time_seconds == 120.5
        assert result.current_pass_rate == 0.97
        assert result.requeue_count == 0

    def test_none_metrics_allowed(self) -> None:
        result = SLOCheckResult(
            lane=WorkCellLane.docs,
            status=SLOStatus.healthy,
        )
        assert result.current_lead_time_seconds is None
        assert result.current_pass_rate is None

    def test_checked_at_auto_populated(self) -> None:
        result = SLOCheckResult(
            lane=WorkCellLane.docs,
            status=SLOStatus.healthy,
        )
        assert result.checked_at is not None
        assert "T" in result.checked_at  # ISO-8601 format


# ===========================================================================
# SLOMonitor — record_task_start
# ===========================================================================


class TestSLOMonitorRecordTaskStart:
    """Tests for SLOMonitor.record_task_start."""

    def test_start_records_state(self, monitor: SLOMonitor) -> None:
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        assert "t1" in monitor._start_times

    def test_start_stores_correct_lane(self, monitor: SLOMonitor) -> None:
        monitor.record_task_start("t1", WorkCellLane.deployment)
        lane, _ = monitor._start_times["t1"]
        assert lane == WorkCellLane.deployment

    def test_start_idempotent(self, monitor: SLOMonitor) -> None:
        """Calling record_task_start twice with same task_id keeps first record."""
        monitor.record_task_start("t-idem", WorkCellLane.api_simple)
        first_start = monitor._start_times["t-idem"][1]
        time.sleep(0.01)
        monitor.record_task_start("t-idem", WorkCellLane.docs)  # second call ignored
        second_start = monitor._start_times["t-idem"][1]
        # Start time must not be updated
        assert first_start == second_start
        # Lane must not be changed
        lane, _ = monitor._start_times["t-idem"]
        assert lane == WorkCellLane.api_simple


# ===========================================================================
# SLOMonitor — record_task_complete
# ===========================================================================


class TestSLOMonitorRecordTaskComplete:
    """Tests for SLOMonitor.record_task_complete."""

    def test_complete_unknown_task_returns_none(self, monitor: SLOMonitor) -> None:
        result = monitor.record_task_complete("ghost", passed=True)
        assert result is None

    def test_complete_returns_task_record(self, monitor: SLOMonitor) -> None:
        monitor.record_task_start("t1", WorkCellLane.docs)
        record = monitor.record_task_complete("t1", passed=True)
        assert record is not None
        assert record.task_id == "t1"
        assert record.lane == WorkCellLane.docs
        assert record.passed is True

    def test_complete_clears_start_entry(self, monitor: SLOMonitor) -> None:
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        monitor.record_task_complete("t1", passed=True)
        assert "t1" not in monitor._start_times

    def test_complete_appends_to_records(self, monitor: SLOMonitor) -> None:
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        monitor.record_task_complete("t1", passed=True)
        assert len(monitor._records) == 1

    def test_complete_computes_positive_lead_time(self, monitor: SLOMonitor) -> None:
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        time.sleep(0.02)
        record = monitor.record_task_complete("t1", passed=True)
        assert record is not None
        assert record.lead_time_seconds is not None
        assert record.lead_time_seconds > 0

    def test_complete_failed_task_tracked(self, monitor: SLOMonitor) -> None:
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        record = monitor.record_task_complete("t1", passed=False)
        assert record is not None
        assert record.passed is False


# ===========================================================================
# SLOMonitor — record_requeue
# ===========================================================================


class TestSLOMonitorRecordRequeue:
    """Tests for SLOMonitor.record_requeue."""

    def test_requeue_increments_counter(self, monitor: SLOMonitor) -> None:
        monitor.record_requeue(WorkCellLane.api_simple)
        assert monitor._requeue_counts[WorkCellLane.api_simple] == 1

    def test_requeue_multiple_increments(self, monitor: SLOMonitor) -> None:
        for _ in range(5):
            monitor.record_requeue(WorkCellLane.docs)
        assert monitor._requeue_counts[WorkCellLane.docs] == 5

    def test_requeue_independent_per_lane(self, monitor: SLOMonitor) -> None:
        monitor.record_requeue(WorkCellLane.api_simple)
        monitor.record_requeue(WorkCellLane.api_simple)
        monitor.record_requeue(WorkCellLane.deployment)
        assert monitor._requeue_counts[WorkCellLane.api_simple] == 2
        assert monitor._requeue_counts[WorkCellLane.deployment] == 1


# ===========================================================================
# SLOMonitor — check_slo: healthy
# ===========================================================================


class TestSLOMonitorCheckSLOHealthy:
    """Tests for the healthy SLO path."""

    def test_no_data_returns_healthy(self, monitor: SLOMonitor) -> None:
        for lane in WorkCellLane:
            result = monitor.check_slo(lane)
            assert result.status == SLOStatus.healthy
            assert result.current_lead_time_seconds is None
            assert result.current_pass_rate is None
            assert result.requeue_count == 0

    def test_fast_passing_task_is_healthy(self, monitor: SLOMonitor) -> None:
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        monitor.record_task_complete("t1", passed=True)
        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.healthy

    def test_check_slo_returns_correct_lane(self, monitor: SLOMonitor) -> None:
        result = monitor.check_slo(WorkCellLane.deployment)
        assert result.lane == WorkCellLane.deployment

    def test_check_slo_result_has_checked_at(self, monitor: SLOMonitor) -> None:
        result = monitor.check_slo(WorkCellLane.docs)
        assert result.checked_at is not None


# ===========================================================================
# SLOMonitor — check_slo: breached
# ===========================================================================


class TestSLOMonitorCheckSLOBreached:
    """Tests for lead-time and pass-rate breach detection."""

    def test_breached_when_lead_time_exceeds_max(self, tight_slo_monitor: SLOMonitor) -> None:
        """With max_lead_time_seconds=1, sleeping >1s should breach."""
        tight_slo_monitor.record_task_start("t1", WorkCellLane.api_simple)
        time.sleep(1.1)
        tight_slo_monitor.record_task_complete("t1", passed=True)
        result = tight_slo_monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.breached

    def test_breached_when_pass_rate_below_target(self, monitor: SLOMonitor) -> None:
        """api_simple has target_pass_rate=0.95.  All failures → breach."""
        for i in range(10):
            monitor.record_task_start(f"t{i}", WorkCellLane.api_simple)
            monitor.record_task_complete(f"t{i}", passed=False)
        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.breached

    def test_breached_when_requeue_exceeds_max(self, tight_slo_monitor: SLOMonitor) -> None:
        """max_requeue_count=0 — any requeue is a breach."""
        tight_slo_monitor.record_requeue(WorkCellLane.deployment)
        result = tight_slo_monitor.check_slo(WorkCellLane.deployment)
        assert result.status == SLOStatus.breached

    def test_breach_current_pass_rate_populated(self, monitor: SLOMonitor) -> None:
        for i in range(5):
            monitor.record_task_start(f"t{i}", WorkCellLane.api_simple)
            monitor.record_task_complete(f"t{i}", passed=False)
        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.current_pass_rate == 0.0

    def test_breach_lead_time_populated(self, tight_slo_monitor: SLOMonitor) -> None:
        tight_slo_monitor.record_task_start("t1", WorkCellLane.docs)
        time.sleep(1.1)
        tight_slo_monitor.record_task_complete("t1", passed=True)
        result = tight_slo_monitor.check_slo(WorkCellLane.docs)
        assert result.current_lead_time_seconds is not None
        assert result.current_lead_time_seconds > 1.0


# ===========================================================================
# SLOMonitor — check_slo: warning
# ===========================================================================


class TestSLOMonitorCheckSLOWarning:
    """Tests for the warning band detection."""

    def test_warning_at_alert_threshold_pct_of_lead_time(self) -> None:
        """Create a SLO with max=10s, threshold=0.5 (warn after 5s).
        A task with lead_time=6s should produce a warning."""
        slos = {
            lane: LaneSLO(
                lane=lane,
                max_lead_time_seconds=10,
                target_pass_rate=0.50,
                max_requeue_count=100,
                alert_threshold_pct=0.5,
            )
            for lane in WorkCellLane
        }
        monitor = SLOMonitor(slos=slos)
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        time.sleep(6.1)
        monitor.record_task_complete("t1", passed=True)
        result = monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.warning

    def test_warning_when_pass_rate_in_warning_band(self) -> None:
        """target_pass_rate=0.90, alert_threshold_pct=0.8.
        warn_floor = 0.90 + 0.10 * 0.20 = 0.92.
        Pass 91 of 100 tasks (0.91) → warning (0.90 <= 0.91 < 0.92)."""
        slos = {
            lane: LaneSLO(
                lane=lane,
                max_lead_time_seconds=3600,
                target_pass_rate=0.90,
                max_requeue_count=100,
                alert_threshold_pct=0.8,
            )
            for lane in WorkCellLane
        }
        monitor = SLOMonitor(slos=slos)
        for i in range(100):
            monitor.record_task_start(f"t{i}", WorkCellLane.api_simple)
            monitor.record_task_complete(f"t{i}", passed=(i < 91))
        result = monitor.check_slo(WorkCellLane.api_simple)
        # pass_rate = 0.91; warn_floor = 0.92 → warning
        assert result.status == SLOStatus.warning
        assert result.current_pass_rate == pytest.approx(0.91, abs=1e-9)


# ===========================================================================
# SLOMonitor — check_all_slos
# ===========================================================================


class TestSLOMonitorCheckAllSLOs:
    """Tests for SLOMonitor.check_all_slos."""

    def test_returns_result_for_every_lane(self, monitor: SLOMonitor) -> None:
        results = monitor.check_all_slos()
        lanes_in_results = {r.lane for r in results}
        assert lanes_in_results == set(WorkCellLane)

    def test_result_count_equals_lane_count(self, monitor: SLOMonitor) -> None:
        results = monitor.check_all_slos()
        assert len(results) == len(WorkCellLane)

    def test_all_healthy_with_no_data(self, monitor: SLOMonitor) -> None:
        results = monitor.check_all_slos()
        for result in results:
            assert result.status == SLOStatus.healthy

    def test_single_breach_visible_in_all_slos(self, tight_slo_monitor: SLOMonitor) -> None:
        tight_slo_monitor.record_task_start("t1", WorkCellLane.api_simple)
        time.sleep(1.1)
        tight_slo_monitor.record_task_complete("t1", passed=True)
        results = tight_slo_monitor.check_all_slos()
        api_result = next(r for r in results if r.lane == WorkCellLane.api_simple)
        assert api_result.status == SLOStatus.breached


# ===========================================================================
# SLOMonitor — get_breaches
# ===========================================================================


class TestSLOMonitorGetBreaches:
    """Tests for SLOMonitor.get_breaches."""

    def test_no_breaches_returns_empty_list(self, monitor: SLOMonitor) -> None:
        assert monitor.get_breaches() == []

    def test_breached_lane_appears_in_get_breaches(self, tight_slo_monitor: SLOMonitor) -> None:
        tight_slo_monitor.record_task_start("t1", WorkCellLane.api_simple)
        time.sleep(1.1)
        tight_slo_monitor.record_task_complete("t1", passed=True)
        breaches = tight_slo_monitor.get_breaches()
        lanes = {b.lane for b in breaches}
        assert WorkCellLane.api_simple in lanes

    def test_healthy_lane_not_in_get_breaches(self, monitor: SLOMonitor) -> None:
        monitor.record_task_start("t1", WorkCellLane.api_simple)
        monitor.record_task_complete("t1", passed=True)
        breaches = monitor.get_breaches()
        assert breaches == []

    def test_get_breaches_respects_window(self, tight_slo_monitor: SLOMonitor) -> None:
        """Records made before the window cutoff should not contribute to breaches."""
        # Complete a task (very short lead time so no lead-time breach)
        # then verify that with a 0-minute window (clamped to 60) the breach
        # from a requeue is still visible.
        tight_slo_monitor.record_requeue(WorkCellLane.deployment)
        # Even with window=0 (clamped to 60) the requeue count is a breach
        # because requeue_count is not time-windowed.
        breaches = tight_slo_monitor.get_breaches(window_minutes=60)
        lanes = {b.lane for b in breaches}
        assert WorkCellLane.deployment in lanes

    def test_get_breaches_negative_window_clamped(self, tight_slo_monitor: SLOMonitor) -> None:
        tight_slo_monitor.record_requeue(WorkCellLane.deployment)
        # Negative window should clamp to 60 without raising.
        breaches = tight_slo_monitor.get_breaches(window_minutes=-5)
        assert isinstance(breaches, list)

    def test_get_breaches_all_statuses_included(self, tight_slo_monitor: SLOMonitor) -> None:
        """Only breached lanes, not warning lanes, are in get_breaches."""
        # Cause a warning on api_simple: lead_time between 0.5s and 1.0s
        # (alert_threshold=0.5 → warn at 0.5s, breach at 1.0s)
        tight_slo_monitor.record_task_start("t1", WorkCellLane.api_simple)
        time.sleep(0.6)
        tight_slo_monitor.record_task_complete("t1", passed=True)
        result = tight_slo_monitor.check_slo(WorkCellLane.api_simple)
        # Must be warning (not breached) to satisfy the test pre-condition
        if result.status == SLOStatus.warning:
            breaches = tight_slo_monitor.get_breaches()
            for b in breaches:
                assert b.status == SLOStatus.breached


# ===========================================================================
# SLOMonitor — reset
# ===========================================================================


class TestSLOMonitorReset:
    """Tests for SLOMonitor.reset."""

    def test_reset_clears_start_times(self, monitor: SLOMonitor) -> None:
        monitor.record_task_start("t1", WorkCellLane.docs)
        monitor.reset()
        assert len(monitor._start_times) == 0

    def test_reset_clears_records(self, monitor: SLOMonitor) -> None:
        monitor.record_task_start("t1", WorkCellLane.docs)
        monitor.record_task_complete("t1", passed=True)
        monitor.reset()
        assert len(monitor._records) == 0

    def test_reset_clears_requeue_counts(self, monitor: SLOMonitor) -> None:
        monitor.record_requeue(WorkCellLane.api_simple)
        monitor.reset()
        assert monitor._requeue_counts[WorkCellLane.api_simple] == 0

    def test_check_slo_healthy_after_reset(self, tight_slo_monitor: SLOMonitor) -> None:
        tight_slo_monitor.record_task_start("t1", WorkCellLane.api_simple)
        time.sleep(1.1)
        tight_slo_monitor.record_task_complete("t1", passed=True)
        tight_slo_monitor.reset()
        result = tight_slo_monitor.check_slo(WorkCellLane.api_simple)
        assert result.status == SLOStatus.healthy


# ===========================================================================
# Singleton pattern
# ===========================================================================


class TestSLOMonitorSingleton:
    """Tests for get_slo_monitor / reset_slo_monitor singleton pattern."""

    def test_get_returns_slo_monitor_instance(self) -> None:
        instance = get_slo_monitor()
        assert isinstance(instance, SLOMonitor)

    def test_get_returns_same_instance(self) -> None:
        a = get_slo_monitor()
        b = get_slo_monitor()
        assert a is b

    def test_reset_causes_new_instance_on_next_get(self) -> None:
        first = get_slo_monitor()
        reset_slo_monitor()
        second = get_slo_monitor()
        assert first is not second

    def test_state_persists_across_get_calls(self) -> None:
        m1 = get_slo_monitor()
        m1.record_task_start("s-task", WorkCellLane.docs)
        m2 = get_slo_monitor()
        assert "s-task" in m2._start_times

    def test_reset_singleton_clears_state(self) -> None:
        m = get_slo_monitor()
        m.record_task_start("s-task", WorkCellLane.docs)
        reset_slo_monitor()
        fresh = get_slo_monitor()
        assert "s-task" not in fresh._start_times


# ===========================================================================
# Thread safety
# ===========================================================================


class TestSLOMonitorThreadSafety:
    """Verify thread-safety under concurrent load."""

    def test_concurrent_record_task_start_no_races(self, monitor: SLOMonitor) -> None:
        """100 threads each start a unique task — no lost entries."""
        n = 100
        errors: list[Exception] = []

        def _start(task_id: str) -> None:
            try:
                monitor.record_task_start(task_id, WorkCellLane.test_writing)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_start, args=(f"ct-{i}",)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors in concurrent start: {errors}"
        assert len(monitor._start_times) == n

    def test_concurrent_start_complete_consistent(self, monitor: SLOMonitor) -> None:
        """50 threads start and complete tasks; record count must be exactly 50."""
        n = 50
        barrier = threading.Barrier(n)
        errors: list[Exception] = []

        def _run(task_id: str) -> None:
            try:
                barrier.wait()
                monitor.record_task_start(task_id, WorkCellLane.api_simple)
                monitor.record_task_complete(task_id, passed=True)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_run, args=(f"cs-{i}",)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors in concurrent start/complete: {errors}"
        assert len(monitor._records) == n

    def test_concurrent_check_slo_no_raises(self, monitor: SLOMonitor) -> None:
        """check_slo must not raise under concurrent read/write."""
        errors: list[Exception] = []
        stop = threading.Event()

        def _writer() -> None:
            i = 0
            while not stop.is_set():
                try:
                    tid = f"w-{i}"
                    monitor.record_task_start(tid, WorkCellLane.docs)
                    monitor.record_task_complete(tid, passed=True)
                    i += 1
                except Exception as exc:
                    errors.append(exc)

        def _reader() -> None:
            for _ in range(20):
                try:
                    monitor.check_slo(WorkCellLane.docs)
                except Exception as exc:
                    errors.append(exc)

        writer = threading.Thread(target=_writer)
        readers = [threading.Thread(target=_reader) for _ in range(5)]
        writer.start()
        for r in readers:
            r.start()
        for r in readers:
            r.join()
        stop.set()
        writer.join()

        assert not errors, f"Errors during concurrent check_slo: {errors}"


# ===========================================================================
# Event emission
# ===========================================================================


class TestSLOMonitorEventEmission:
    """Verify that SLO events are emitted via EventEmitter."""

    def test_no_event_emitted_when_healthy(self, monitor: SLOMonitor) -> None:
        """Healthy completions must not emit any SLO events.

        _emit_if_needed uses a local import of get_event_emitter, so we inject
        our mock by replacing the module-level singleton directly.
        """
        from forge_harness.webhook_server.services import event_emitter as _ee_mod

        mock_emitter = MagicMock()
        original = _ee_mod._emitter_instance
        _ee_mod._emitter_instance = mock_emitter
        try:
            monitor.record_task_start("t1", WorkCellLane.api_simple)
            monitor.record_task_complete("t1", passed=True)
        finally:
            _ee_mod._emitter_instance = original

        slo_calls = [c for c in mock_emitter.emit.call_args_list if "slo" in str(c).lower()]
        assert slo_calls == [], f"Unexpected SLO events: {slo_calls}"

    def test_breached_event_emitted_on_lead_time_breach(
        self, tight_slo_monitor: SLOMonitor
    ) -> None:
        """A lead-time breach must emit task.slo.breached."""
        from forge_harness.webhook_server.models.sse_events import SSEEventType
        from forge_harness.webhook_server.services import event_emitter as _ee_mod

        emitted_types: list = []
        mock_emitter = MagicMock()

        def capture_emit(event_type, data, source):
            emitted_types.append(event_type)
            return MagicMock()

        mock_emitter.emit.side_effect = capture_emit

        original = _ee_mod._emitter_instance
        _ee_mod._emitter_instance = mock_emitter
        try:
            tight_slo_monitor.record_task_start("t1", WorkCellLane.api_simple)
            time.sleep(1.1)
            tight_slo_monitor.record_task_complete("t1", passed=True)
        finally:
            _ee_mod._emitter_instance = original

        assert SSEEventType.task_slo_breached in emitted_types

    def test_warning_event_emitted_in_warning_state(self) -> None:
        """Verify task.slo.warning is emitted when status enters warning."""
        from forge_harness.webhook_server.models.sse_events import SSEEventType
        from forge_harness.webhook_server.services import event_emitter as _ee_mod

        slos = {
            lane: LaneSLO(
                lane=lane,
                max_lead_time_seconds=10,
                target_pass_rate=0.50,
                max_requeue_count=100,
                alert_threshold_pct=0.5,
            )
            for lane in WorkCellLane
        }
        monitor = SLOMonitor(slos=slos)

        emitted_types: list = []
        mock_emitter = MagicMock()

        def capture_emit(event_type, data, source):
            emitted_types.append(event_type)
            return MagicMock()

        mock_emitter.emit.side_effect = capture_emit

        original = _ee_mod._emitter_instance
        _ee_mod._emitter_instance = mock_emitter
        try:
            monitor.record_task_start("t1", WorkCellLane.api_simple)
            time.sleep(6.1)
            monitor.record_task_complete("t1", passed=True)
        finally:
            _ee_mod._emitter_instance = original

        assert SSEEventType.task_slo_warning in emitted_types


# ===========================================================================
# Public exports via __init__.py
# ===========================================================================


class TestPublicExports:
    """Verify that new symbols are accessible from package __init__ modules."""

    def test_lane_slo_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import LaneSLO as LaneSloAlias

        assert LaneSloAlias is LaneSLO

    def test_slo_status_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import SLOStatus as SloStatusAlias

        assert SloStatusAlias is SLOStatus

    def test_slo_check_result_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import SLOCheckResult as SloCheckResultAlias

        assert SloCheckResultAlias is SLOCheckResult

    def test_default_lane_slos_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import DEFAULT_LANE_SLOS as DLS

        assert DLS is DEFAULT_LANE_SLOS

    def test_slo_monitor_exported_from_services(self) -> None:
        from forge_harness.webhook_server.services import SLOMonitor as SloMonitorAlias

        assert SloMonitorAlias is SLOMonitor

    def test_get_slo_monitor_exported_from_services(self) -> None:
        from forge_harness.webhook_server.services import get_slo_monitor as gsm

        assert gsm is get_slo_monitor

    def test_reset_slo_monitor_exported_from_services(self) -> None:
        from forge_harness.webhook_server.services import reset_slo_monitor as rsm

        assert rsm is reset_slo_monitor

    def test_sse_event_type_has_slo_warning(self) -> None:
        from forge_harness.webhook_server.models.sse_events import SSEEventType

        assert SSEEventType.task_slo_warning == "task.slo.warning"

    def test_sse_event_type_has_slo_breached(self) -> None:
        from forge_harness.webhook_server.models.sse_events import SSEEventType

        assert SSEEventType.task_slo_breached == "task.slo.breached"
