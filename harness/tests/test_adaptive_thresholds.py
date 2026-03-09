"""Comprehensive tests for Dark Factory Phase 5 — AdaptiveThresholds (DF-5001).

Coverage:
- AdaptiveThresholds init with default and custom config
- compute_adjustments() for every rule branch
- apply_adjustments(): snapshot, config mutation, record creation
- revert_last(): config restoration, record marking, no-op
- get_history(): ordering and limit
- get_threshold(): lookup and unknown key
- Singleton: get_adaptive_thresholds / reset_adaptive_thresholds
- ThresholdAdjustment.to_dict(), AdjustmentRecord.to_dict(), ObservedMetrics.to_dict()
- Error cases: empty adjustments list, empty operator
- Internal _clamp and _step helpers
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone

import pytest

from forge_harness.dark_factory.adaptive_thresholds import (
    DEFAULT_CONFIG,
    AdaptiveThresholds,
    AdjustmentRecord,
    ObservedMetrics,
    ThresholdAdjustment,
    get_adaptive_thresholds,
    reset_adaptive_thresholds,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_metrics(**overrides) -> ObservedMetrics:
    """Return a neutral set of metrics that triggers no adjustments."""
    defaults = dict(
        evaluator_pass_rate=0.97,
        audit_pass_rate=0.97,
        escaped_defects=0,
        requeue_rate=0.05,
        total_tasks=60,
    )
    defaults.update(overrides)
    return ObservedMetrics(**defaults)


def _make_adj(
    parameter: str = "audit_sample_rate",
    old_value: float = 0.125,
    new_value: float = 0.145,
    direction: str = "tighten",
) -> ThresholdAdjustment:
    """Create a minimal ThresholdAdjustment for apply/revert tests."""
    return ThresholdAdjustment(
        adjustment_id="test-adj-id",
        parameter=parameter,
        old_value=old_value,
        new_value=new_value,
        direction=direction,
        reason="unit test",
        confidence=0.8,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure the singleton is torn down before and after every test."""
    reset_adaptive_thresholds()
    yield
    reset_adaptive_thresholds()


@pytest.fixture()
def tuner() -> AdaptiveThresholds:
    return AdaptiveThresholds()


# ===========================================================================
# 1. Initialisation
# ===========================================================================

class TestInit:
    def test_default_config_matches_module_constant(self, tuner):
        assert tuner.config == DEFAULT_CONFIG

    def test_custom_config_is_used(self):
        custom = dict(DEFAULT_CONFIG)
        custom["audit_sample_rate"] = 0.30
        t = AdaptiveThresholds(config=custom)
        assert t.get_threshold("audit_sample_rate") == pytest.approx(0.30)

    def test_custom_config_does_not_mutate_module_constant(self):
        custom = dict(DEFAULT_CONFIG)
        custom["audit_sample_rate"] = 0.42
        AdaptiveThresholds(config=custom)
        assert DEFAULT_CONFIG["audit_sample_rate"] == pytest.approx(0.125)

    def test_history_is_empty_after_init(self, tuner):
        assert tuner.get_history() == []

    def test_config_property_returns_copy(self, tuner):
        cfg = tuner.config
        cfg["audit_sample_rate"] = 9999.0
        assert tuner.config["audit_sample_rate"] == pytest.approx(DEFAULT_CONFIG["audit_sample_rate"])

    def test_none_config_uses_defaults(self):
        t = AdaptiveThresholds(config=None)
        assert t.config == DEFAULT_CONFIG


# ===========================================================================
# 2. compute_adjustments — insufficient observations
# ===========================================================================

class TestInsufficientObservations:
    def test_below_min_observations_returns_empty_list(self, tuner):
        metrics = _base_metrics(total_tasks=29)
        assert tuner.compute_adjustments(metrics) == []

    def test_zero_tasks_returns_empty_list(self, tuner):
        metrics = _base_metrics(total_tasks=0)
        assert tuner.compute_adjustments(metrics) == []

    def test_exactly_at_min_observations_is_evaluated(self, tuner):
        # At exactly 30 tasks, rules fire
        metrics = _base_metrics(total_tasks=30, escaped_defects=1)
        adjustments = tuner.compute_adjustments(metrics)
        assert any(a.parameter == "audit_sample_rate" for a in adjustments)

    def test_above_min_observations_is_evaluated(self, tuner):
        metrics = _base_metrics(total_tasks=100, escaped_defects=1)
        assert len(tuner.compute_adjustments(metrics)) >= 1


# ===========================================================================
# 3. compute_adjustments — audit_sample_rate
# ===========================================================================

class TestAuditSampleRateAdjustments:
    def test_escaped_defects_tightens_audit_sample_rate(self, tuner):
        metrics = _base_metrics(escaped_defects=3, total_tasks=60)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "audit_sample_rate")
        assert adj.direction == "tighten"
        assert adj.new_value > adj.old_value

    def test_tighten_step_bounded_by_max_step(self, tuner):
        metrics = _base_metrics(escaped_defects=100, total_tasks=60)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "audit_sample_rate")
        max_step = tuner.config["max_step"]
        assert adj.new_value <= adj.old_value + max_step + 1e-9

    def test_tighten_confidence_is_0_9(self, tuner):
        metrics = _base_metrics(escaped_defects=1, total_tasks=60)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "audit_sample_rate")
        assert adj.confidence == pytest.approx(0.9)

    def test_tighten_reason_mentions_defects(self, tuner):
        metrics = _base_metrics(escaped_defects=2, total_tasks=60)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "audit_sample_rate")
        assert "2" in adj.reason
        assert "defect" in adj.reason.lower()

    def test_high_audit_pass_rate_relaxes_sample_rate(self, tuner):
        metrics = _base_metrics(escaped_defects=0, audit_pass_rate=0.99, total_tasks=55)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "audit_sample_rate")
        assert adj.direction == "relax"
        assert adj.new_value < adj.old_value

    def test_relax_requires_at_least_50_tasks(self, tuner):
        metrics = _base_metrics(escaped_defects=0, audit_pass_rate=0.99, total_tasks=49)
        params = [a.parameter for a in tuner.compute_adjustments(metrics)]
        assert "audit_sample_rate" not in params

    def test_relax_requires_no_escaped_defects(self, tuner):
        metrics = _base_metrics(escaped_defects=1, audit_pass_rate=0.99, total_tasks=55)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "audit_sample_rate")
        assert adj.direction == "tighten"

    def test_relax_audit_rate_exactly_0_98_is_triggered(self, tuner):
        metrics = _base_metrics(escaped_defects=0, audit_pass_rate=0.98, total_tasks=50)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next((a for a in adjustments if a.parameter == "audit_sample_rate"), None)
        assert adj is not None
        assert adj.direction == "relax"

    def test_relax_audit_rate_below_0_98_not_triggered(self, tuner):
        metrics = _base_metrics(escaped_defects=0, audit_pass_rate=0.97, total_tasks=60)
        params = [a.parameter for a in tuner.compute_adjustments(metrics)]
        assert "audit_sample_rate" not in params

    def test_tighten_clamps_to_max_bound(self):
        config = dict(DEFAULT_CONFIG)
        config["audit_sample_rate"] = 0.49
        t = AdaptiveThresholds(config=config)
        metrics = _base_metrics(escaped_defects=5, total_tasks=60)
        adjustments = t.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "audit_sample_rate")
        assert adj.new_value <= 0.50

    def test_no_adjustment_when_already_at_ceiling(self):
        config = dict(DEFAULT_CONFIG)
        config["audit_sample_rate"] = 0.50
        t = AdaptiveThresholds(config=config)
        metrics = _base_metrics(escaped_defects=5, total_tasks=60)
        params = [a.parameter for a in t.compute_adjustments(metrics)]
        assert "audit_sample_rate" not in params

    def test_no_adjustment_when_already_at_floor_for_relax(self):
        config = dict(DEFAULT_CONFIG)
        config["audit_sample_rate"] = 0.05  # at min bound
        t = AdaptiveThresholds(config=config)
        metrics = _base_metrics(escaped_defects=0, audit_pass_rate=0.99, total_tasks=55)
        params = [a.parameter for a in t.compute_adjustments(metrics)]
        assert "audit_sample_rate" not in params


# ===========================================================================
# 4. compute_adjustments — min_evaluator_pass_rate
# ===========================================================================

class TestEvalPassRateAdjustments:
    def test_excellent_rate_zero_defects_tightens(self, tuner):
        metrics = _base_metrics(evaluator_pass_rate=0.99, escaped_defects=0, total_tasks=60)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "min_evaluator_pass_rate")
        assert adj.direction == "tighten"
        assert adj.new_value > adj.old_value

    def test_tighten_eval_confidence_is_0_6(self, tuner):
        metrics = _base_metrics(evaluator_pass_rate=0.99, escaped_defects=0, total_tasks=60)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "min_evaluator_pass_rate")
        assert adj.confidence == pytest.approx(0.6)

    def test_excellent_rate_with_defects_does_not_tighten(self, tuner):
        metrics = _base_metrics(evaluator_pass_rate=0.99, escaped_defects=1, total_tasks=60)
        params = [a.parameter for a in tuner.compute_adjustments(metrics)]
        assert "min_evaluator_pass_rate" not in params

    def test_quality_dropping_relaxes_threshold(self, tuner):
        # threshold is 0.95; relax triggers when < 0.95 * 0.95 = 0.9025
        metrics = _base_metrics(evaluator_pass_rate=0.89, escaped_defects=0, total_tasks=60)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "min_evaluator_pass_rate")
        assert adj.direction == "relax"
        assert adj.new_value < adj.old_value

    def test_relax_eval_confidence_is_0_5(self, tuner):
        metrics = _base_metrics(evaluator_pass_rate=0.85, escaped_defects=0, total_tasks=60)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "min_evaluator_pass_rate")
        assert adj.confidence == pytest.approx(0.5)

    def test_slight_drop_not_below_threshold_times_0_95_no_relax(self, tuner):
        # 0.91 > 0.9025 (=0.95*0.95), so relax should NOT trigger
        metrics = _base_metrics(evaluator_pass_rate=0.91, escaped_defects=0, total_tasks=60)
        params = [a.parameter for a in tuner.compute_adjustments(metrics)]
        assert "min_evaluator_pass_rate" not in params

    def test_tighten_clamps_to_max_bound(self):
        config = dict(DEFAULT_CONFIG)
        config["min_evaluator_pass_rate"] = 0.99  # already at max
        t = AdaptiveThresholds(config=config)
        metrics = _base_metrics(evaluator_pass_rate=0.99, escaped_defects=0, total_tasks=60)
        params = [a.parameter for a in t.compute_adjustments(metrics)]
        assert "min_evaluator_pass_rate" not in params

    def test_relax_clamps_to_min_bound(self):
        config = dict(DEFAULT_CONFIG)
        config["min_evaluator_pass_rate"] = 0.90  # at floor
        t = AdaptiveThresholds(config=config)
        # 0.85 < 0.90 * 0.95 = 0.855 → relax triggered; new = 0.89 < 0.90 → clamped → no change
        metrics = _base_metrics(evaluator_pass_rate=0.85, escaped_defects=0, total_tasks=60)
        params = [a.parameter for a in t.compute_adjustments(metrics)]
        assert "min_evaluator_pass_rate" not in params

    def test_eval_pass_rate_exactly_0_99_tightens(self, tuner):
        metrics = _base_metrics(evaluator_pass_rate=0.990, escaped_defects=0, total_tasks=60)
        assert any(
            a.parameter == "min_evaluator_pass_rate" and a.direction == "tighten"
            for a in tuner.compute_adjustments(metrics)
        )


# ===========================================================================
# 5. compute_adjustments — max_requeue_rate
# ===========================================================================

class TestRequeueRateAdjustments:
    def test_low_requeue_with_50_tasks_tightens(self, tuner):
        # current max = 0.10; low means <= 0.10 * 0.3 = 0.03
        metrics = _base_metrics(requeue_rate=0.02, total_tasks=55)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "max_requeue_rate")
        assert adj.direction == "tighten"
        assert adj.new_value < adj.old_value

    def test_low_requeue_below_50_tasks_no_tighten(self, tuner):
        metrics = _base_metrics(requeue_rate=0.02, total_tasks=49)
        params = [a.parameter for a in tuner.compute_adjustments(metrics)]
        assert "max_requeue_rate" not in params

    def test_tighten_requeue_confidence_is_0_6(self, tuner):
        metrics = _base_metrics(requeue_rate=0.02, total_tasks=55)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "max_requeue_rate")
        assert adj.confidence == pytest.approx(0.6)

    def test_over_threshold_requeue_relaxes(self, tuner):
        metrics = _base_metrics(requeue_rate=0.15, total_tasks=60)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "max_requeue_rate")
        assert adj.direction == "relax"
        assert adj.new_value > adj.old_value

    def test_relax_requeue_confidence_is_0_8(self, tuner):
        metrics = _base_metrics(requeue_rate=0.20, total_tasks=60)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "max_requeue_rate")
        assert adj.confidence == pytest.approx(0.8)

    def test_relax_clamps_to_max_bound(self):
        config = dict(DEFAULT_CONFIG)
        config["max_requeue_rate"] = 0.20
        t = AdaptiveThresholds(config=config)
        metrics = _base_metrics(requeue_rate=0.25, total_tasks=60)
        params = [a.parameter for a in t.compute_adjustments(metrics)]
        assert "max_requeue_rate" not in params

    def test_mid_range_requeue_no_adjustment(self, tuner):
        # 0.05 is not <= 0.03 and not > 0.10
        metrics = _base_metrics(requeue_rate=0.05, total_tasks=60)
        params = [a.parameter for a in tuner.compute_adjustments(metrics)]
        assert "max_requeue_rate" not in params

    def test_requeue_exactly_at_0_3_of_threshold_tightens(self, tuner):
        metrics = _base_metrics(requeue_rate=0.03, total_tasks=50)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next((a for a in adjustments if a.parameter == "max_requeue_rate"), None)
        assert adj is not None
        assert adj.direction == "tighten"

    def test_tighten_requeue_clamps_to_min_bound(self):
        config = dict(DEFAULT_CONFIG)
        config["max_requeue_rate"] = 0.02  # at floor
        t = AdaptiveThresholds(config=config)
        # requeue_rate=0.005 <= 0.02*0.3=0.006, tighten fires, new=0.01 < min=0.02 → clamped → no change
        metrics = _base_metrics(requeue_rate=0.005, total_tasks=60)
        params = [a.parameter for a in t.compute_adjustments(metrics)]
        assert "max_requeue_rate" not in params


# ===========================================================================
# 6. compute_adjustments — multiple adjustments / all neutral
# ===========================================================================

class TestMultipleAdjustments:
    def test_multiple_parameters_adjusted_simultaneously(self, tuner):
        metrics = ObservedMetrics(
            evaluator_pass_rate=0.99,
            audit_pass_rate=0.98,
            escaped_defects=1,
            requeue_rate=0.02,
            total_tasks=55,
        )
        adjustments = tuner.compute_adjustments(metrics)
        params = {a.parameter for a in adjustments}
        assert "audit_sample_rate" in params
        assert "max_requeue_rate" in params

    def test_all_neutral_returns_empty_list(self, tuner):
        metrics = ObservedMetrics(
            evaluator_pass_rate=0.97,
            audit_pass_rate=0.97,
            escaped_defects=0,
            requeue_rate=0.05,
            total_tasks=60,
        )
        assert tuner.compute_adjustments(metrics) == []

    def test_adjustment_ids_are_unique(self, tuner):
        metrics = _base_metrics(escaped_defects=1, requeue_rate=0.20, total_tasks=60)
        adjustments = tuner.compute_adjustments(metrics)
        ids = [a.adjustment_id for a in adjustments]
        assert len(ids) == len(set(ids))

    def test_adjustment_old_value_matches_current_config(self, tuner):
        metrics = _base_metrics(escaped_defects=1, total_tasks=60)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "audit_sample_rate")
        assert adj.old_value == pytest.approx(tuner.config["audit_sample_rate"])

    def test_adjustment_new_value_rounded_to_4dp(self, tuner):
        metrics = _base_metrics(escaped_defects=1, total_tasks=60)
        adjustments = tuner.compute_adjustments(metrics)
        adj = next(a for a in adjustments if a.parameter == "audit_sample_rate")
        assert adj.new_value == round(adj.new_value, 4)

    def test_adjustments_do_not_mutate_config(self, tuner):
        before_rate = tuner.get_threshold("audit_sample_rate")
        metrics = _base_metrics(escaped_defects=1, total_tasks=60)
        tuner.compute_adjustments(metrics)
        assert tuner.get_threshold("audit_sample_rate") == pytest.approx(before_rate)


# ===========================================================================
# 7. apply_adjustments
# ===========================================================================

class TestApplyAdjustments:
    def test_apply_updates_config_value(self, tuner):
        adj = _make_adj(parameter="audit_sample_rate", old_value=0.125, new_value=0.200)
        tuner.apply_adjustments([adj], operator="ops-bot")
        assert tuner.get_threshold("audit_sample_rate") == pytest.approx(0.200)

    def test_apply_saves_snapshot_before_change(self, tuner):
        assert len(tuner._snapshots) == 0
        tuner.apply_adjustments([_make_adj()], operator="ops-bot")
        assert len(tuner._snapshots) == 1

    def test_apply_returns_adjustment_record(self, tuner):
        record = tuner.apply_adjustments([_make_adj()], operator="ops-bot")
        assert isinstance(record, AdjustmentRecord)

    def test_record_operator_matches(self, tuner):
        record = tuner.apply_adjustments([_make_adj()], operator="my-service")
        assert record.operator == "my-service"

    def test_record_is_not_reverted_by_default(self, tuner):
        record = tuner.apply_adjustments([_make_adj()], operator="test")
        assert record.reverted is False
        assert record.reverted_at is None

    def test_record_contains_all_adjustments(self, tuner):
        adjs = [
            _make_adj(parameter="audit_sample_rate", new_value=0.145),
            _make_adj(parameter="min_evaluator_pass_rate", old_value=0.95, new_value=0.96),
        ]
        record = tuner.apply_adjustments(adjs, operator="test")
        assert len(record.adjustments) == 2

    def test_apply_adds_to_history(self, tuner):
        tuner.apply_adjustments([_make_adj()], operator="test")
        assert len(tuner.get_history()) == 1

    def test_apply_accumulates_history(self, tuner):
        for i in range(3):
            tuner.apply_adjustments([_make_adj(new_value=0.130 + i * 0.01)], operator="loop")
        assert len(tuner.get_history()) == 3

    def test_apply_empty_adjustments_raises_value_error(self, tuner):
        with pytest.raises(ValueError, match="No adjustments"):
            tuner.apply_adjustments([], operator="test")

    def test_apply_empty_operator_raises_value_error(self, tuner):
        with pytest.raises(ValueError, match="operator must be non-empty"):
            tuner.apply_adjustments([_make_adj()], operator="")

    def test_record_has_non_empty_record_id(self, tuner):
        record = tuner.apply_adjustments([_make_adj()], operator="test")
        assert record.record_id and len(record.record_id) > 0

    def test_record_has_applied_at_timestamp(self, tuner):
        before = datetime.now(UTC)
        record = tuner.apply_adjustments([_make_adj()], operator="test")
        after = datetime.now(UTC)
        assert before <= record.applied_at <= after

    def test_apply_unknown_parameter_is_silently_ignored(self, tuner):
        adj = _make_adj(parameter="nonexistent_param")
        record = tuner.apply_adjustments([adj], operator="test")
        assert record is not None

    def test_apply_multiple_parameters_all_updated(self, tuner):
        adjs = [
            _make_adj(parameter="audit_sample_rate", old_value=0.125, new_value=0.145),
            _make_adj(parameter="max_requeue_rate", old_value=0.10, new_value=0.12),
        ]
        tuner.apply_adjustments(adjs, operator="test")
        assert tuner.get_threshold("audit_sample_rate") == pytest.approx(0.145)
        assert tuner.get_threshold("max_requeue_rate") == pytest.approx(0.12)


# ===========================================================================
# 8. revert_last
# ===========================================================================

class TestRevertLast:
    def test_revert_restores_previous_config(self, tuner):
        original = tuner.get_threshold("audit_sample_rate")
        tuner.apply_adjustments([_make_adj(new_value=0.200)], operator="test")
        tuner.revert_last(operator="test")
        assert tuner.get_threshold("audit_sample_rate") == pytest.approx(original)

    def test_revert_marks_record_as_reverted(self, tuner):
        tuner.apply_adjustments([_make_adj()], operator="test")
        record = tuner.revert_last(operator="test")
        assert record is not None
        assert record.reverted is True

    def test_revert_sets_reverted_at_timestamp(self, tuner):
        tuner.apply_adjustments([_make_adj()], operator="test")
        before = datetime.now(UTC)
        record = tuner.revert_last(operator="test")
        after = datetime.now(UTC)
        assert record.reverted_at is not None
        assert before <= record.reverted_at <= after

    def test_revert_returns_the_applied_record(self, tuner):
        applied = tuner.apply_adjustments([_make_adj()], operator="test")
        reverted = tuner.revert_last(operator="test")
        assert reverted is not None
        assert reverted.record_id == applied.record_id

    def test_revert_nothing_to_revert_returns_none(self, tuner):
        assert tuner.revert_last(operator="test") is None

    def test_double_revert_second_call_returns_none(self, tuner):
        tuner.apply_adjustments([_make_adj()], operator="test")
        tuner.revert_last(operator="test")
        assert tuner.revert_last(operator="test") is None

    def test_revert_pops_snapshot(self, tuner):
        tuner.apply_adjustments([_make_adj()], operator="test")
        assert len(tuner._snapshots) == 1
        tuner.revert_last(operator="test")
        assert len(tuner._snapshots) == 0

    def test_revert_history_record_still_present_and_marked(self, tuner):
        tuner.apply_adjustments([_make_adj()], operator="test")
        tuner.revert_last(operator="test")
        assert len(tuner.get_history()) == 1
        assert tuner.get_history()[0].reverted is True

    def test_revert_full_round_trip_via_compute(self, tuner):
        before_rate = tuner.get_threshold("audit_sample_rate")
        metrics = _base_metrics(escaped_defects=1, total_tasks=60)
        adjustments = tuner.compute_adjustments(metrics)
        assert adjustments, "Expected at least one adjustment from escaped_defects=1"
        tuner.apply_adjustments(adjustments, operator="auto")
        assert tuner.get_threshold("audit_sample_rate") != pytest.approx(before_rate)
        tuner.revert_last(operator="auto")
        assert tuner.get_threshold("audit_sample_rate") == pytest.approx(before_rate)


# ===========================================================================
# 9. get_history
# ===========================================================================

class TestGetHistory:
    def _apply_n(self, tuner: AdaptiveThresholds, n: int) -> None:
        for i in range(n):
            tuner.apply_adjustments([_make_adj(new_value=0.130 + i * 0.001)], operator="loop")

    def test_most_recent_first(self, tuner):
        self._apply_n(tuner, 3)
        history = tuner.get_history()
        assert history[0].adjustments[0].new_value == pytest.approx(0.130 + 2 * 0.001)

    def test_limit_respected(self, tuner):
        self._apply_n(tuner, 10)
        assert len(tuner.get_history(limit=5)) == 5

    def test_default_limit_is_20(self, tuner):
        self._apply_n(tuner, 25)
        assert len(tuner.get_history()) == 20

    def test_empty_history(self, tuner):
        assert tuner.get_history() == []

    def test_all_records_are_adjustment_record_instances(self, tuner):
        self._apply_n(tuner, 3)
        for record in tuner.get_history():
            assert isinstance(record, AdjustmentRecord)


# ===========================================================================
# 10. get_threshold
# ===========================================================================

class TestGetThreshold:
    def test_returns_audit_sample_rate(self, tuner):
        assert tuner.get_threshold("audit_sample_rate") == pytest.approx(0.125)

    def test_returns_min_evaluator_pass_rate(self, tuner):
        assert tuner.get_threshold("min_evaluator_pass_rate") == pytest.approx(0.95)

    def test_returns_max_requeue_rate(self, tuner):
        assert tuner.get_threshold("max_requeue_rate") == pytest.approx(0.10)

    def test_unknown_parameter_returns_zero(self, tuner):
        assert tuner.get_threshold("nonexistent_param") == pytest.approx(0.0)

    def test_reflects_applied_adjustment(self, tuner):
        tuner.apply_adjustments([_make_adj(parameter="audit_sample_rate", new_value=0.200)], operator="test")
        assert tuner.get_threshold("audit_sample_rate") == pytest.approx(0.200)

    def test_reflects_revert(self, tuner):
        original = tuner.get_threshold("audit_sample_rate")
        tuner.apply_adjustments([_make_adj(new_value=0.200)], operator="test")
        tuner.revert_last(operator="test")
        assert tuner.get_threshold("audit_sample_rate") == pytest.approx(original)


# ===========================================================================
# 11. Singleton
# ===========================================================================

class TestSingleton:
    def test_get_returns_same_instance(self):
        a = get_adaptive_thresholds()
        b = get_adaptive_thresholds()
        assert a is b

    def test_reset_returns_fresh_instance(self):
        a = get_adaptive_thresholds()
        reset_adaptive_thresholds()
        b = get_adaptive_thresholds()
        assert a is not b

    def test_singleton_has_default_config(self):
        tuner = get_adaptive_thresholds()
        assert tuner.config == DEFAULT_CONFIG

    def test_state_persists_across_calls(self):
        tuner = get_adaptive_thresholds()
        tuner.apply_adjustments([_make_adj()], operator="test")
        same = get_adaptive_thresholds()
        assert len(same.get_history()) == 1

    def test_reset_clears_state(self):
        tuner = get_adaptive_thresholds()
        tuner.apply_adjustments([_make_adj()], operator="test")
        reset_adaptive_thresholds()
        fresh = get_adaptive_thresholds()
        assert fresh.get_history() == []
        assert fresh.config == DEFAULT_CONFIG


# ===========================================================================
# 12. to_dict() on all data classes
# ===========================================================================

class TestObservedMetricsToDict:
    def test_all_fields_present(self):
        m = ObservedMetrics(
            evaluator_pass_rate=0.97,
            audit_pass_rate=0.98,
            escaped_defects=2,
            requeue_rate=0.04,
            total_tasks=80,
        )
        d = m.to_dict()
        assert d["evaluator_pass_rate"] == pytest.approx(0.97)
        assert d["audit_pass_rate"] == pytest.approx(0.98)
        assert d["escaped_defects"] == 2
        assert d["requeue_rate"] == pytest.approx(0.04)
        assert d["total_tasks"] == 80

    def test_keys_are_exact_set(self):
        assert set(_base_metrics().to_dict().keys()) == {
            "evaluator_pass_rate",
            "audit_pass_rate",
            "escaped_defects",
            "requeue_rate",
            "total_tasks",
        }

    def test_escaped_defects_zero_serialised(self):
        assert _base_metrics(escaped_defects=0).to_dict()["escaped_defects"] == 0


class TestThresholdAdjustmentToDict:
    def _adj(self) -> ThresholdAdjustment:
        return ThresholdAdjustment(
            adjustment_id="id-123",
            parameter="audit_sample_rate",
            old_value=0.125,
            new_value=0.145,
            direction="tighten",
            reason="test reason",
            confidence=0.9,
        )

    def test_all_fields_present(self):
        d = self._adj().to_dict()
        assert d["adjustment_id"] == "id-123"
        assert d["parameter"] == "audit_sample_rate"
        assert d["old_value"] == pytest.approx(0.125)
        assert d["new_value"] == pytest.approx(0.145)
        assert d["direction"] == "tighten"
        assert d["reason"] == "test reason"
        assert d["confidence"] == pytest.approx(0.9)
        assert "timestamp" in d

    def test_timestamp_is_iso8601_string(self):
        d = self._adj().to_dict()
        datetime.fromisoformat(d["timestamp"])

    def test_keys_are_exact_set(self):
        assert set(self._adj().to_dict().keys()) == {
            "adjustment_id", "parameter", "old_value", "new_value",
            "direction", "reason", "confidence", "timestamp",
        }

    def test_relax_direction_serialised_correctly(self):
        adj = ThresholdAdjustment(
            adjustment_id="x",
            parameter="audit_sample_rate",
            old_value=0.2,
            new_value=0.19,
            direction="relax",
            reason="r",
        )
        assert adj.to_dict()["direction"] == "relax"


class TestAdjustmentRecordToDict:
    def _make_record(self, reverted: bool = False) -> AdjustmentRecord:
        now = datetime.now(UTC)
        record = AdjustmentRecord(
            record_id="rec-1",
            adjustments=[_make_adj()],
            operator="system",
            applied_at=now,
        )
        if reverted:
            record.reverted = True
            record.reverted_at = datetime.now(UTC)
        return record

    def test_non_reverted_record_fields(self):
        d = self._make_record(reverted=False).to_dict()
        assert d["record_id"] == "rec-1"
        assert d["operator"] == "system"
        assert d["reverted"] is False
        assert d["reverted_at"] is None
        assert isinstance(d["adjustments"], list)
        assert len(d["adjustments"]) == 1

    def test_reverted_record_has_reverted_at(self):
        d = self._make_record(reverted=True).to_dict()
        assert d["reverted"] is True
        assert d["reverted_at"] is not None
        datetime.fromisoformat(d["reverted_at"])

    def test_applied_at_is_iso8601_string(self):
        d = self._make_record().to_dict()
        datetime.fromisoformat(d["applied_at"])

    def test_nested_adjustments_are_dicts_with_parameter_key(self):
        for adj_dict in self._make_record().to_dict()["adjustments"]:
            assert isinstance(adj_dict, dict)
            assert "parameter" in adj_dict

    def test_keys_are_exact_set(self):
        assert set(self._make_record().to_dict().keys()) == {
            "record_id", "adjustments", "operator", "applied_at", "reverted", "reverted_at",
        }

    def test_via_apply_adjustments_integration(self, tuner):
        record = tuner.apply_adjustments([_make_adj()], operator="auto-system")
        d = record.to_dict()
        assert d["operator"] == "auto-system"
        assert d["reverted"] is False


# ===========================================================================
# 13. Internal helpers — _clamp and _step
# ===========================================================================

class TestClampAndStep:
    def test_clamp_value_below_min(self, tuner):
        assert tuner._clamp("audit_sample_rate", 0.01) == pytest.approx(0.05)

    def test_clamp_value_above_max(self, tuner):
        assert tuner._clamp("audit_sample_rate", 0.99) == pytest.approx(0.50)

    def test_clamp_value_in_range_unchanged(self, tuner):
        assert tuner._clamp("audit_sample_rate", 0.25) == pytest.approx(0.25)

    def test_clamp_unknown_param_uses_0_and_1(self, tuner):
        assert tuner._clamp("nonexistent", 1.5) == pytest.approx(1.0)
        assert tuner._clamp("nonexistent", -0.5) == pytest.approx(0.0)

    def test_step_positive_direction_capped_at_max_step(self, tuner):
        result = tuner._step(0.10, 1.0)  # want +1.0, capped at +0.02
        assert result == pytest.approx(0.12)

    def test_step_negative_direction_capped_at_max_step(self, tuner):
        result = tuner._step(0.10, -1.0)  # want -1.0, capped at -0.02
        assert result == pytest.approx(0.08)

    def test_step_small_positive_uses_exact_value(self, tuner):
        assert tuner._step(0.10, 0.01) == pytest.approx(0.11)

    def test_step_small_negative_uses_exact_value(self, tuner):
        assert tuner._step(0.10, -0.01) == pytest.approx(0.09)

    def test_step_zero_direction_no_change(self, tuner):
        assert tuner._step(0.10, 0.0) == pytest.approx(0.10)


# ===========================================================================
# 14. reset()
# ===========================================================================

class TestReset:
    def test_reset_clears_history(self, tuner):
        tuner.apply_adjustments([_make_adj()], operator="test")
        tuner.reset()
        assert tuner.get_history() == []

    def test_reset_clears_snapshots(self, tuner):
        tuner.apply_adjustments([_make_adj()], operator="test")
        tuner.reset()
        assert tuner._snapshots == []

    def test_reset_restores_default_config(self, tuner):
        tuner.apply_adjustments([_make_adj(new_value=0.30)], operator="test")
        tuner.reset()
        assert tuner.config == DEFAULT_CONFIG
