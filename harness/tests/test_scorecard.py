"""
Tests for forge_harness/webhook_server/models/scorecard.py
===========================================================

Covers:
- Default values and field constraints on DarkFactoryScorecard
- ScorecardEvent construction and auto-generated_at
- compute_scorecard() from realistic task dicts
- Edge cases: empty list, all-autonomous, all-human
- Serialisation / JSON round-trip
- Validator rejection paths (period ordering, rate bounds, etc.)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from forge_harness.webhook_server.models.scorecard import (
    DarkFactoryScorecard,
    ScorecardEvent,
    compute_scorecard,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

T0 = datetime(2026, 2, 22, 0, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
T24 = T0 + timedelta(hours=24)


def _make_task(
    *,
    dispatch_acked: bool = True,
    evaluator_passed: bool = True,
    requeued: bool = False,
    human_touched: bool = False,
    escaped_defect: bool = False,
    completed: bool = True,
    cycle_time_minutes: float | None = 30.0,
) -> dict:
    """Return a minimal task dict with all supported keys."""
    return {
        "dispatch_acked": dispatch_acked,
        "evaluator_passed": evaluator_passed,
        "requeued": requeued,
        "human_touched": human_touched,
        "escaped_defect": escaped_defect,
        "completed": completed,
        "cycle_time_minutes": cycle_time_minutes,
    }


# ===========================================================================
# DarkFactoryScorecard — default values
# ===========================================================================


class TestDarkFactoryScorecardDefaults:
    """DarkFactoryScorecard should have safe zero-value defaults."""

    def test_all_rate_defaults_are_zero(self):
        sc = DarkFactoryScorecard(period_start=T0, period_end=T1)
        assert sc.dispatch_ack_rate == 0.0
        assert sc.evaluator_pass_rate == 0.0
        assert sc.requeue_rate == 0.0
        assert sc.human_touch_ratio == 0.0

    def test_count_defaults_are_zero(self):
        sc = DarkFactoryScorecard(period_start=T0, period_end=T1)
        assert sc.escaped_defects == 0
        assert sc.total_tasks == 0
        assert sc.autonomous_completions == 0

    def test_mean_cycle_time_default_is_zero(self):
        sc = DarkFactoryScorecard(period_start=T0, period_end=T1)
        assert sc.mean_cycle_time_minutes == 0.0

    def test_node_id_default(self):
        sc = DarkFactoryScorecard(period_start=T0, period_end=T1)
        assert sc.node_id == "unknown"

    def test_derived_automation_ratio_zero_when_no_tasks(self):
        sc = DarkFactoryScorecard(period_start=T0, period_end=T1)
        assert sc.automation_ratio == 0.0

    def test_derived_period_minutes(self):
        sc = DarkFactoryScorecard(period_start=T0, period_end=T1)
        assert sc.period_minutes == pytest.approx(60.0)


# ===========================================================================
# DarkFactoryScorecard — field validation
# ===========================================================================


class TestDarkFactoryScorecardFieldValidation:
    """Field-level Pydantic constraints must be enforced."""

    @pytest.mark.parametrize(
        "field_name",
        [
            "dispatch_ack_rate",
            "evaluator_pass_rate",
            "requeue_rate",
            "human_touch_ratio",
        ],
    )
    def test_rate_below_zero_is_rejected(self, field_name):
        with pytest.raises(ValidationError):
            DarkFactoryScorecard(
                period_start=T0,
                period_end=T1,
                **{field_name: -0.01},
            )

    @pytest.mark.parametrize(
        "field_name",
        [
            "dispatch_ack_rate",
            "evaluator_pass_rate",
            "requeue_rate",
            "human_touch_ratio",
        ],
    )
    def test_rate_above_one_is_rejected(self, field_name):
        with pytest.raises(ValidationError):
            DarkFactoryScorecard(
                period_start=T0,
                period_end=T1,
                **{field_name: 1.01},
            )

    def test_escaped_defects_negative_is_rejected(self):
        with pytest.raises(ValidationError):
            DarkFactoryScorecard(period_start=T0, period_end=T1, escaped_defects=-1)

    def test_total_tasks_negative_is_rejected(self):
        with pytest.raises(ValidationError):
            DarkFactoryScorecard(period_start=T0, period_end=T1, total_tasks=-1)

    def test_autonomous_completions_negative_is_rejected(self):
        with pytest.raises(ValidationError):
            DarkFactoryScorecard(period_start=T0, period_end=T1, autonomous_completions=-1)

    def test_mean_cycle_time_negative_is_rejected(self):
        with pytest.raises(ValidationError):
            DarkFactoryScorecard(period_start=T0, period_end=T1, mean_cycle_time_minutes=-1.0)

    def test_empty_node_id_is_rejected(self):
        with pytest.raises(ValidationError):
            DarkFactoryScorecard(period_start=T0, period_end=T1, node_id="")


# ===========================================================================
# DarkFactoryScorecard — model-level validators
# ===========================================================================


class TestDarkFactoryScorecardModelValidators:
    """Cross-field model validators must fire correctly."""

    def test_period_start_equal_to_end_is_rejected(self):
        with pytest.raises(ValidationError, match="period_start"):
            DarkFactoryScorecard(period_start=T0, period_end=T0)

    def test_period_start_after_end_is_rejected(self):
        with pytest.raises(ValidationError, match="period_start"):
            DarkFactoryScorecard(period_start=T1, period_end=T0)

    def test_autonomous_completions_exceeding_total_is_rejected(self):
        with pytest.raises(ValidationError, match="autonomous_completions"):
            DarkFactoryScorecard(
                period_start=T0,
                period_end=T1,
                total_tasks=5,
                autonomous_completions=6,
            )

    def test_naive_datetime_gets_utc_attached(self):
        """Naive datetimes should be silently upgraded to UTC."""
        naive_start = datetime(2026, 2, 22, 0, 0, 0)
        naive_end = datetime(2026, 2, 22, 1, 0, 0)
        sc = DarkFactoryScorecard(period_start=naive_start, period_end=naive_end)
        assert sc.period_start.tzinfo is not None
        assert sc.period_end.tzinfo is not None

    def test_string_datetimes_are_parsed(self):
        sc = DarkFactoryScorecard(
            period_start="2026-02-22T00:00:00+00:00",
            period_end="2026-02-22T01:00:00+00:00",
        )
        assert isinstance(sc.period_start, datetime)
        assert isinstance(sc.period_end, datetime)


# ===========================================================================
# ScorecardEvent
# ===========================================================================


class TestScorecardEvent:
    """ScorecardEvent envelope behaviour."""

    def _base_scorecard(self) -> DarkFactoryScorecard:
        return DarkFactoryScorecard(
            period_start=T0,
            period_end=T1,
            total_tasks=10,
            autonomous_completions=8,
            dispatch_ack_rate=0.9,
            evaluator_pass_rate=0.95,
            human_touch_ratio=0.2,
            requeue_rate=0.1,
            node_id="forge:nova",
        )

    def test_valid_daily_event(self):
        event = ScorecardEvent(
            event_type="scorecard_daily",
            scorecard=self._base_scorecard(),
        )
        assert event.event_type == "scorecard_daily"
        assert isinstance(event.generated_at, datetime)

    def test_valid_hourly_event(self):
        event = ScorecardEvent(
            event_type="scorecard_hourly",
            scorecard=self._base_scorecard(),
        )
        assert event.event_type == "scorecard_hourly"

    def test_generated_at_defaults_to_now(self):
        before = datetime.now(UTC)
        event = ScorecardEvent(
            event_type="scorecard_hourly",
            scorecard=self._base_scorecard(),
        )
        after = datetime.now(UTC)
        assert before <= event.generated_at <= after

    def test_generated_at_explicit_value(self):
        fixed = datetime(2026, 2, 22, 12, 0, 0, tzinfo=UTC)
        event = ScorecardEvent(
            event_type="scorecard_daily",
            scorecard=self._base_scorecard(),
            generated_at=fixed,
        )
        assert event.generated_at == fixed

    def test_invalid_event_type_is_rejected(self):
        with pytest.raises(ValidationError):
            ScorecardEvent(
                event_type="scorecard_weekly",  # not in Literal
                scorecard=self._base_scorecard(),
            )

    def test_naive_generated_at_gets_utc(self):
        naive = datetime(2026, 2, 22, 12, 0, 0)
        event = ScorecardEvent(
            event_type="scorecard_daily",
            scorecard=self._base_scorecard(),
            generated_at=naive,
        )
        assert event.generated_at.tzinfo is not None


# ===========================================================================
# compute_scorecard — happy path
# ===========================================================================


class TestComputeScorecard:
    """compute_scorecard() should aggregate task dicts correctly."""

    def test_basic_computation(self):
        tasks = [
            _make_task(
                dispatch_acked=True,
                evaluator_passed=True,
                requeued=False,
                human_touched=False,
                escaped_defect=False,
                cycle_time_minutes=20.0,
            ),
            _make_task(
                dispatch_acked=True,
                evaluator_passed=True,
                requeued=True,
                human_touched=False,
                escaped_defect=False,
                cycle_time_minutes=40.0,
            ),
            _make_task(
                dispatch_acked=False,
                evaluator_passed=False,
                requeued=False,
                human_touched=True,
                escaped_defect=True,
                cycle_time_minutes=None,
                completed=False,
            ),
            _make_task(
                dispatch_acked=True,
                evaluator_passed=True,
                requeued=False,
                human_touched=False,
                escaped_defect=False,
                cycle_time_minutes=30.0,
            ),
        ]
        sc = compute_scorecard(tasks, T0, T1, node_id="forge:nova")

        assert sc.total_tasks == 4
        assert sc.dispatch_ack_rate == pytest.approx(0.75)  # 3/4
        assert sc.evaluator_pass_rate == pytest.approx(0.75)  # 3/4
        assert sc.requeue_rate == pytest.approx(0.25)  # 1/4
        assert sc.human_touch_ratio == pytest.approx(0.25)  # 1/4
        assert sc.escaped_defects == 1
        # autonomous = completed AND NOT human_touched: tasks 0, 1, 3 = 3
        assert sc.autonomous_completions == 3
        # mean of 20, 40, 30 = 30 (task index 2 not completed)
        assert sc.mean_cycle_time_minutes == pytest.approx(30.0)
        assert sc.node_id == "forge:nova"
        assert sc.period_start == T0
        assert sc.period_end == T1

    def test_node_id_propagated(self):
        tasks = [_make_task()]
        sc = compute_scorecard(tasks, T0, T1, node_id="forge:prya")
        assert sc.node_id == "forge:prya"

    def test_default_node_id_is_unknown(self):
        tasks = [_make_task()]
        sc = compute_scorecard(tasks, T0, T1)
        assert sc.node_id == "unknown"


# ===========================================================================
# compute_scorecard — edge case: empty list
# ===========================================================================


class TestComputeScorecardEmpty:
    """An empty task list should produce an all-zero scorecard."""

    def test_empty_returns_zero_scorecard(self):
        sc = compute_scorecard([], T0, T1, node_id="forge:nova")
        assert sc.total_tasks == 0
        assert sc.dispatch_ack_rate == 0.0
        assert sc.evaluator_pass_rate == 0.0
        assert sc.requeue_rate == 0.0
        assert sc.human_touch_ratio == 0.0
        assert sc.escaped_defects == 0
        assert sc.autonomous_completions == 0
        assert sc.mean_cycle_time_minutes == 0.0

    def test_empty_automation_ratio_is_zero(self):
        sc = compute_scorecard([], T0, T1)
        assert sc.automation_ratio == 0.0


# ===========================================================================
# compute_scorecard — edge case: all autonomous
# ===========================================================================


class TestComputeScorecardAllAutonomous:
    """When all tasks complete without human touch, ratios should be perfect."""

    def test_all_autonomous_rates(self):
        tasks = [
            _make_task(
                dispatch_acked=True,
                evaluator_passed=True,
                requeued=False,
                human_touched=False,
                escaped_defect=False,
                completed=True,
                cycle_time_minutes=15.0,
            )
            for _ in range(10)
        ]
        sc = compute_scorecard(tasks, T0, T24, node_id="forge:nova")
        assert sc.dispatch_ack_rate == pytest.approx(1.0)
        assert sc.evaluator_pass_rate == pytest.approx(1.0)
        assert sc.requeue_rate == pytest.approx(0.0)
        assert sc.human_touch_ratio == pytest.approx(0.0)
        assert sc.escaped_defects == 0
        assert sc.autonomous_completions == 10
        assert sc.total_tasks == 10
        assert sc.automation_ratio == pytest.approx(1.0)
        assert sc.mean_cycle_time_minutes == pytest.approx(15.0)


# ===========================================================================
# compute_scorecard — edge case: all human
# ===========================================================================


class TestComputeScorecardAllHuman:
    """When every task requires human touch, human_touch_ratio must be 1.0."""

    def test_all_human_touched(self):
        tasks = [
            _make_task(
                dispatch_acked=False,
                evaluator_passed=False,
                requeued=True,
                human_touched=True,
                escaped_defect=False,
                completed=True,
                cycle_time_minutes=120.0,
            )
            for _ in range(5)
        ]
        sc = compute_scorecard(tasks, T0, T24)
        assert sc.human_touch_ratio == pytest.approx(1.0)
        assert sc.dispatch_ack_rate == pytest.approx(0.0)
        assert sc.requeue_rate == pytest.approx(1.0)
        # Completed + human_touched => NOT autonomous
        assert sc.autonomous_completions == 0
        assert sc.automation_ratio == 0.0
        assert sc.mean_cycle_time_minutes == pytest.approx(120.0)


# ===========================================================================
# compute_scorecard — tasks with missing keys
# ===========================================================================


class TestComputeScorecardMissingKeys:
    """Missing task dict keys must default to the worst-case (False/None)."""

    def test_empty_task_dicts_treated_as_worst_case(self):
        tasks = [{}, {}, {}]
        sc = compute_scorecard(tasks, T0, T1)
        assert sc.total_tasks == 3
        assert sc.dispatch_ack_rate == pytest.approx(0.0)
        assert sc.evaluator_pass_rate == pytest.approx(0.0)
        assert sc.autonomous_completions == 0
        assert sc.mean_cycle_time_minutes == pytest.approx(0.0)

    def test_partial_task_dicts(self):
        tasks = [
            {"dispatch_acked": True},
            {"dispatch_acked": True, "evaluator_passed": True},
        ]
        sc = compute_scorecard(tasks, T0, T1)
        assert sc.dispatch_ack_rate == pytest.approx(1.0)  # both acked
        assert sc.evaluator_pass_rate == pytest.approx(0.5)  # only second


# ===========================================================================
# Serialisation / JSON output
# ===========================================================================


class TestScorecardSerialisation:
    """Scorecard and event models must round-trip cleanly through JSON."""

    def _sample_scorecard(self) -> DarkFactoryScorecard:
        return DarkFactoryScorecard(
            dispatch_ack_rate=0.88,
            evaluator_pass_rate=0.92,
            requeue_rate=0.05,
            human_touch_ratio=0.12,
            escaped_defects=2,
            period_start=T0,
            period_end=T24,
            node_id="forge:nova",
            total_tasks=50,
            autonomous_completions=44,
            mean_cycle_time_minutes=27.5,
        )

    def test_scorecard_model_dump_json(self):
        sc = self._sample_scorecard()
        payload = sc.model_dump_json()
        assert isinstance(payload, str)
        data = json.loads(payload)
        assert data["node_id"] == "forge:nova"
        assert data["total_tasks"] == 50
        assert data["dispatch_ack_rate"] == pytest.approx(0.88)
        assert "period_start" in data
        assert "period_end" in data

    def test_scorecard_model_dump_dict(self):
        sc = self._sample_scorecard()
        data = sc.model_dump()
        assert data["autonomous_completions"] == 44
        assert data["escaped_defects"] == 2

    def test_event_model_dump_json(self):
        event = ScorecardEvent(
            event_type="scorecard_daily",
            scorecard=self._sample_scorecard(),
            generated_at=T0,
        )
        payload = event.model_dump_json()
        data = json.loads(payload)
        assert data["event_type"] == "scorecard_daily"
        assert "scorecard" in data
        assert data["scorecard"]["node_id"] == "forge:nova"
        assert "generated_at" in data

    def test_json_round_trip(self):
        """model_dump_json -> model_validate_json must produce an equal model."""
        sc = self._sample_scorecard()
        raw = sc.model_dump_json()
        restored = DarkFactoryScorecard.model_validate_json(raw)
        assert restored.node_id == sc.node_id
        assert restored.total_tasks == sc.total_tasks
        assert restored.dispatch_ack_rate == pytest.approx(sc.dispatch_ack_rate)
        assert restored.period_start == sc.period_start

    def test_event_json_round_trip(self):
        event = ScorecardEvent(
            event_type="scorecard_hourly",
            scorecard=self._sample_scorecard(),
            generated_at=T0,
        )
        raw = event.model_dump_json()
        restored = ScorecardEvent.model_validate_json(raw)
        assert restored.event_type == "scorecard_hourly"
        assert restored.scorecard.escaped_defects == 2

    def test_scorecard_from_compute_is_serialisable(self):
        tasks = [_make_task(cycle_time_minutes=45.0) for _ in range(3)]
        sc = compute_scorecard(tasks, T0, T1, node_id="forge:test")
        payload = sc.model_dump_json()
        data = json.loads(payload)
        assert data["total_tasks"] == 3
        assert data["node_id"] == "forge:test"


# ===========================================================================
# Derived properties
# ===========================================================================


class TestDerivedProperties:
    """Derived @property helpers on DarkFactoryScorecard."""

    def test_automation_ratio_partial(self):
        sc = DarkFactoryScorecard(
            period_start=T0,
            period_end=T1,
            total_tasks=10,
            autonomous_completions=7,
        )
        assert sc.automation_ratio == pytest.approx(0.7)

    def test_automation_ratio_full(self):
        sc = DarkFactoryScorecard(
            period_start=T0,
            period_end=T1,
            total_tasks=5,
            autonomous_completions=5,
        )
        assert sc.automation_ratio == pytest.approx(1.0)

    def test_period_minutes_one_hour(self):
        sc = DarkFactoryScorecard(period_start=T0, period_end=T1)
        assert sc.period_minutes == pytest.approx(60.0)

    def test_period_minutes_24_hours(self):
        sc = DarkFactoryScorecard(period_start=T0, period_end=T24)
        assert sc.period_minutes == pytest.approx(24 * 60.0)
