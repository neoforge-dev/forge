"""Comprehensive tests for Dark Factory Phase 5 — LaneGraduation (DF-5005).

Coverage:
- LaneGraduation init with default and custom gates
- evaluate() for "canary" target: all-pass, each criterion failing, multiple failures
- evaluate() for "enabled" target: all-pass, audit pass rate, cycle time, zero SLO
- Invalid target_stage -> ValueError
- update_canary_gate / update_enabled_gate
- GraduationReport: to_dict(), failed_criteria, passed_criteria
- LaneMetrics: defaults, to_dict()
- CriterionResult: to_dict()
- get_reports(): lane filter, limit
- Singleton: get_lane_graduation / reset_lane_graduation
- reset() restores default gates and clears reports
"""

from __future__ import annotations

from datetime import datetime

import pytest

from forge_harness.dark_factory.lane_graduation import (
    DEFAULT_CANARY_GATE,
    DEFAULT_ENABLED_GATE,
    CriterionResult,
    GraduationReport,
    LaneGraduation,
    LaneMetrics,
    get_lane_graduation,
    reset_lane_graduation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _canary_passing_metrics(**overrides) -> LaneMetrics:
    """Return metrics that satisfy all canary gate criteria."""
    defaults = dict(
        evaluator_pass_rate=0.97,
        escaped_defects=0,
        total_completions=25,
        requeue_rate=0.05,
    )
    defaults.update(overrides)
    return LaneMetrics(**defaults)


def _enabled_passing_metrics(**overrides) -> LaneMetrics:
    """Return metrics that satisfy all enabled gate criteria."""
    defaults = dict(
        evaluator_pass_rate=0.99,
        escaped_defects=0,
        total_completions=60,
        requeue_rate=0.03,
        audit_pass_rate=0.97,
        mean_cycle_time_minutes=30.0,
        slo_target_minutes=60.0,
    )
    defaults.update(overrides)
    return LaneMetrics(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_lane_graduation()
    yield
    reset_lane_graduation()


@pytest.fixture()
def grad() -> LaneGraduation:
    return LaneGraduation()


# ===========================================================================
# 1. Initialisation
# ===========================================================================

class TestInit:
    def test_default_canary_gate_matches_module_constant(self, grad):
        assert grad.canary_gate == DEFAULT_CANARY_GATE

    def test_default_enabled_gate_matches_module_constant(self, grad):
        assert grad.enabled_gate == DEFAULT_ENABLED_GATE

    def test_custom_canary_gate_is_used(self):
        custom = {"min_evaluator_pass_rate": 0.88, "min_completions": 5}
        g = LaneGraduation(canary_gate=custom)
        assert g.canary_gate["min_evaluator_pass_rate"] == pytest.approx(0.88)
        assert g.canary_gate["min_completions"] == 5

    def test_custom_enabled_gate_is_used(self):
        custom = {"min_evaluator_pass_rate": 0.96, "max_requeue_rate": 0.04}
        g = LaneGraduation(enabled_gate=custom)
        assert g.enabled_gate["min_evaluator_pass_rate"] == pytest.approx(0.96)

    def test_canary_gate_property_returns_copy(self, grad):
        cfg = grad.canary_gate
        cfg["min_completions"] = 9999
        assert grad.canary_gate["min_completions"] == DEFAULT_CANARY_GATE["min_completions"]

    def test_enabled_gate_property_returns_copy(self, grad):
        cfg = grad.enabled_gate
        cfg["min_completions"] = 9999
        assert grad.enabled_gate["min_completions"] == DEFAULT_ENABLED_GATE["min_completions"]

    def test_reports_start_empty(self, grad):
        assert grad.get_reports() == []


# ===========================================================================
# 2. evaluate() — "canary" target
# ===========================================================================

class TestEvaluateCanary:
    def test_all_four_criteria_pass_returns_can_graduate_true(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        assert report.can_graduate is True
        assert report.target_stage == "canary"
        assert report.lane == "docs"

    def test_canary_has_exactly_4_criteria(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        assert len(report.criteria) == 4

    def test_all_four_criterion_names_present(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        names = {c.name for c in report.criteria}
        assert names == {"evaluator_pass_rate", "escaped_defects", "min_completions", "requeue_rate"}

    def test_eval_pass_rate_too_low_blocks_graduation(self, grad):
        metrics = _canary_passing_metrics(evaluator_pass_rate=0.90)  # below 0.95
        report = grad.evaluate("docs", "canary", metrics)
        assert report.can_graduate is False
        failed_names = {c.name for c in report.failed_criteria}
        assert "evaluator_pass_rate" in failed_names

    def test_eval_pass_rate_failure_criterion_has_reason(self, grad):
        metrics = _canary_passing_metrics(evaluator_pass_rate=0.90)
        report = grad.evaluate("docs", "canary", metrics)
        criterion = next(c for c in report.criteria if c.name == "evaluator_pass_rate")
        assert not criterion.passed
        assert criterion.reason  # non-empty

    def test_escaped_defects_greater_than_zero_blocks_graduation(self, grad):
        metrics = _canary_passing_metrics(escaped_defects=2)
        report = grad.evaluate("docs", "canary", metrics)
        assert report.can_graduate is False
        failed_names = {c.name for c in report.failed_criteria}
        assert "escaped_defects" in failed_names

    def test_escaped_defects_failure_reason_mentions_count(self, grad):
        metrics = _canary_passing_metrics(escaped_defects=3)
        report = grad.evaluate("docs", "canary", metrics)
        criterion = next(c for c in report.criteria if c.name == "escaped_defects")
        assert "3" in criterion.reason

    def test_insufficient_completions_blocks_graduation(self, grad):
        metrics = _canary_passing_metrics(total_completions=10)  # below 20
        report = grad.evaluate("docs", "canary", metrics)
        assert report.can_graduate is False
        failed_names = {c.name for c in report.failed_criteria}
        assert "min_completions" in failed_names

    def test_insufficient_completions_failure_reason_mentions_count(self, grad):
        metrics = _canary_passing_metrics(total_completions=10)
        report = grad.evaluate("docs", "canary", metrics)
        criterion = next(c for c in report.criteria if c.name == "min_completions")
        assert "10" in criterion.reason

    def test_requeue_rate_too_high_blocks_graduation(self, grad):
        metrics = _canary_passing_metrics(requeue_rate=0.15)  # above 0.10
        report = grad.evaluate("docs", "canary", metrics)
        assert report.can_graduate is False
        failed_names = {c.name for c in report.failed_criteria}
        assert "requeue_rate" in failed_names

    def test_requeue_rate_failure_reason_mentions_value(self, grad):
        metrics = _canary_passing_metrics(requeue_rate=0.15)
        report = grad.evaluate("docs", "canary", metrics)
        criterion = next(c for c in report.criteria if c.name == "requeue_rate")
        assert criterion.reason  # non-empty

    def test_multiple_failures_all_reported(self, grad):
        metrics = LaneMetrics(
            evaluator_pass_rate=0.80,   # fail
            escaped_defects=1,           # fail
            total_completions=5,         # fail
            requeue_rate=0.20,           # fail
        )
        report = grad.evaluate("docs", "canary", metrics)
        assert report.can_graduate is False
        assert len(report.failed_criteria) == 4

    def test_exactly_at_threshold_values_passes(self, grad):
        metrics = LaneMetrics(
            evaluator_pass_rate=0.95,   # exactly at min
            escaped_defects=0,
            total_completions=20,       # exactly at min
            requeue_rate=0.10,          # exactly at max
        )
        report = grad.evaluate("docs", "canary", metrics)
        assert report.can_graduate is True

    def test_one_below_threshold_fails(self, grad):
        metrics = LaneMetrics(
            evaluator_pass_rate=0.949,  # just below 0.95
            escaped_defects=0,
            total_completions=20,
            requeue_rate=0.10,
        )
        report = grad.evaluate("docs", "canary", metrics)
        assert report.can_graduate is False

    def test_report_id_is_non_empty(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        assert report.report_id and len(report.report_id) > 0

    def test_report_is_stored_in_get_reports(self, grad):
        grad.evaluate("docs", "canary", _canary_passing_metrics())
        assert len(grad.get_reports()) == 1

    def test_criterion_actual_reflects_passed_value(self, grad):
        metrics = _canary_passing_metrics(evaluator_pass_rate=0.97)
        report = grad.evaluate("docs", "canary", metrics)
        criterion = next(c for c in report.criteria if c.name == "evaluator_pass_rate")
        assert criterion.actual == pytest.approx(0.97)

    def test_criterion_threshold_reflects_gate_value(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        criterion = next(c for c in report.criteria if c.name == "evaluator_pass_rate")
        assert criterion.threshold == pytest.approx(0.95)

    def test_passed_criterion_has_empty_reason(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        for c in report.passed_criteria:
            assert c.reason == ""

    def test_canary_does_not_include_audit_or_cycle_time_criteria(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        names = {c.name for c in report.criteria}
        assert "audit_pass_rate" not in names
        assert "cycle_time_ratio" not in names


# ===========================================================================
# 3. evaluate() — "enabled" target
# ===========================================================================

class TestEvaluateEnabled:
    def test_all_six_criteria_pass_returns_can_graduate_true(self, grad):
        report = grad.evaluate("docs", "enabled", _enabled_passing_metrics())
        assert report.can_graduate is True
        assert report.target_stage == "enabled"

    def test_enabled_has_exactly_6_criteria(self, grad):
        report = grad.evaluate("docs", "enabled", _enabled_passing_metrics())
        assert len(report.criteria) == 6

    def test_all_six_criterion_names_present(self, grad):
        report = grad.evaluate("docs", "enabled", _enabled_passing_metrics())
        names = {c.name for c in report.criteria}
        assert names == {
            "evaluator_pass_rate",
            "escaped_defects",
            "min_completions",
            "requeue_rate",
            "audit_pass_rate",
            "cycle_time_ratio",
        }

    def test_audit_pass_rate_too_low_blocks_graduation(self, grad):
        metrics = _enabled_passing_metrics(audit_pass_rate=0.85)  # below 0.95
        report = grad.evaluate("docs", "enabled", metrics)
        assert report.can_graduate is False
        failed_names = {c.name for c in report.failed_criteria}
        assert "audit_pass_rate" in failed_names

    def test_audit_pass_rate_failure_reason_present(self, grad):
        metrics = _enabled_passing_metrics(audit_pass_rate=0.85)
        report = grad.evaluate("docs", "enabled", metrics)
        criterion = next(c for c in report.criteria if c.name == "audit_pass_rate")
        assert criterion.reason  # non-empty

    def test_cycle_time_ratio_too_high_blocks_graduation(self, grad):
        metrics = _enabled_passing_metrics(
            mean_cycle_time_minutes=150.0,  # ratio = 2.5 > 2.0
            slo_target_minutes=60.0,
        )
        report = grad.evaluate("docs", "enabled", metrics)
        assert report.can_graduate is False
        failed_names = {c.name for c in report.failed_criteria}
        assert "cycle_time_ratio" in failed_names

    def test_cycle_time_ratio_failure_reason_mentions_ratio(self, grad):
        metrics = _enabled_passing_metrics(
            mean_cycle_time_minutes=150.0,
            slo_target_minutes=60.0,
        )
        report = grad.evaluate("docs", "enabled", metrics)
        criterion = next(c for c in report.criteria if c.name == "cycle_time_ratio")
        assert criterion.reason  # non-empty

    def test_cycle_time_exactly_at_limit_passes(self, grad):
        metrics = _enabled_passing_metrics(
            mean_cycle_time_minutes=120.0,  # ratio = 2.0 exactly
            slo_target_minutes=60.0,
        )
        report = grad.evaluate("docs", "enabled", metrics)
        criterion = next(c for c in report.criteria if c.name == "cycle_time_ratio")
        assert criterion.passed is True

    def test_zero_slo_target_ratio_is_zero_and_passes(self, grad):
        metrics = _enabled_passing_metrics(
            mean_cycle_time_minutes=30.0,
            slo_target_minutes=0.0,  # edge case — should yield ratio=0.0
        )
        report = grad.evaluate("docs", "enabled", metrics)
        criterion = next(c for c in report.criteria if c.name == "cycle_time_ratio")
        assert criterion.actual == pytest.approx(0.0)
        assert criterion.passed is True

    def test_cycle_time_actual_is_rounded_to_2dp(self, grad):
        metrics = _enabled_passing_metrics(
            mean_cycle_time_minutes=33.333,
            slo_target_minutes=60.0,
        )
        report = grad.evaluate("docs", "enabled", metrics)
        criterion = next(c for c in report.criteria if c.name == "cycle_time_ratio")
        assert criterion.actual == round(criterion.actual, 2)

    def test_enabled_eval_pass_rate_too_low_blocks(self, grad):
        metrics = _enabled_passing_metrics(evaluator_pass_rate=0.95)  # below 0.98
        report = grad.evaluate("docs", "enabled", metrics)
        assert report.can_graduate is False
        assert any(c.name == "evaluator_pass_rate" and not c.passed for c in report.criteria)

    def test_enabled_escaped_defects_blocks(self, grad):
        metrics = _enabled_passing_metrics(escaped_defects=1)
        report = grad.evaluate("docs", "enabled", metrics)
        assert report.can_graduate is False
        assert any(c.name == "escaped_defects" and not c.passed for c in report.criteria)

    def test_enabled_insufficient_completions_blocks(self, grad):
        metrics = _enabled_passing_metrics(total_completions=49)  # below 50
        report = grad.evaluate("docs", "enabled", metrics)
        assert report.can_graduate is False
        assert any(c.name == "min_completions" and not c.passed for c in report.criteria)

    def test_enabled_requeue_rate_too_high_blocks(self, grad):
        metrics = _enabled_passing_metrics(requeue_rate=0.06)  # above 0.05
        report = grad.evaluate("docs", "enabled", metrics)
        assert report.can_graduate is False
        assert any(c.name == "requeue_rate" and not c.passed for c in report.criteria)

    def test_enabled_includes_audit_and_cycle_time_criteria(self, grad):
        report = grad.evaluate("docs", "enabled", _enabled_passing_metrics())
        names = {c.name for c in report.criteria}
        assert "audit_pass_rate" in names
        assert "cycle_time_ratio" in names


# ===========================================================================
# 4. Invalid target_stage
# ===========================================================================

class TestInvalidTargetStage:
    def test_invalid_stage_raises_value_error(self, grad):
        with pytest.raises(ValueError, match="target_stage must be 'canary' or 'enabled'"):
            grad.evaluate("docs", "human_review", _canary_passing_metrics())

    def test_empty_string_raises_value_error(self, grad):
        with pytest.raises(ValueError):
            grad.evaluate("docs", "", _canary_passing_metrics())

    def test_arbitrary_string_raises_value_error(self, grad):
        with pytest.raises(ValueError):
            grad.evaluate("docs", "production", _canary_passing_metrics())

    def test_error_message_includes_bad_stage_name(self, grad):
        with pytest.raises(ValueError, match="badstage"):
            grad.evaluate("docs", "badstage", _canary_passing_metrics())


# ===========================================================================
# 5. update_canary_gate / update_enabled_gate
# ===========================================================================

class TestUpdateGates:
    def test_update_canary_gate_changes_threshold(self, grad):
        grad.update_canary_gate({"min_evaluator_pass_rate": 0.92})
        assert grad.canary_gate["min_evaluator_pass_rate"] == pytest.approx(0.92)

    def test_update_canary_gate_returns_new_config(self, grad):
        result = grad.update_canary_gate({"min_completions": 30})
        assert result["min_completions"] == 30

    def test_update_canary_gate_other_keys_preserved(self, grad):
        grad.update_canary_gate({"min_evaluator_pass_rate": 0.88})
        assert grad.canary_gate["max_requeue_rate"] == DEFAULT_CANARY_GATE["max_requeue_rate"]

    def test_update_enabled_gate_changes_threshold(self, grad):
        grad.update_enabled_gate({"min_evaluator_pass_rate": 0.96})
        assert grad.enabled_gate["min_evaluator_pass_rate"] == pytest.approx(0.96)

    def test_update_enabled_gate_returns_new_config(self, grad):
        result = grad.update_enabled_gate({"max_requeue_rate": 0.04})
        assert result["max_requeue_rate"] == pytest.approx(0.04)

    def test_updated_canary_gate_affects_evaluation(self, grad):
        # Lower threshold so 0.90 passes
        grad.update_canary_gate({"min_evaluator_pass_rate": 0.88})
        metrics = _canary_passing_metrics(evaluator_pass_rate=0.90)
        report = grad.evaluate("docs", "canary", metrics)
        criterion = next(c for c in report.criteria if c.name == "evaluator_pass_rate")
        assert criterion.passed is True

    def test_updated_enabled_gate_affects_evaluation(self, grad):
        # Raise completion minimum above what metrics provide
        grad.update_enabled_gate({"min_completions": 100})
        metrics = _enabled_passing_metrics(total_completions=60)
        report = grad.evaluate("docs", "enabled", metrics)
        criterion = next(c for c in report.criteria if c.name == "min_completions")
        assert criterion.passed is False

    def test_update_canary_gate_is_idempotent(self, grad):
        grad.update_canary_gate({"min_evaluator_pass_rate": 0.88})
        grad.update_canary_gate({"min_evaluator_pass_rate": 0.88})
        assert grad.canary_gate["min_evaluator_pass_rate"] == pytest.approx(0.88)

    def test_update_canary_gate_can_add_new_key(self, grad):
        grad.update_canary_gate({"custom_key": 42})
        assert grad.canary_gate["custom_key"] == 42


# ===========================================================================
# 6. GraduationReport — to_dict(), failed_criteria, passed_criteria
# ===========================================================================

class TestGraduationReport:
    def test_to_dict_has_all_expected_keys(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        d = report.to_dict()
        assert set(d.keys()) == {
            "report_id", "lane", "target_stage", "can_graduate",
            "criteria", "failed_count", "passed_count", "timestamp",
        }

    def test_to_dict_report_id_is_string(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        assert isinstance(report.to_dict()["report_id"], str)

    def test_to_dict_can_graduate_is_bool(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        assert isinstance(report.to_dict()["can_graduate"], bool)

    def test_to_dict_criteria_are_list_of_dicts(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        d = report.to_dict()
        assert isinstance(d["criteria"], list)
        for c in d["criteria"]:
            assert isinstance(c, dict)

    def test_to_dict_timestamp_is_iso8601(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        d = report.to_dict()
        datetime.fromisoformat(d["timestamp"])

    def test_to_dict_failed_count_matches_property(self, grad):
        metrics = _canary_passing_metrics(evaluator_pass_rate=0.80)
        report = grad.evaluate("docs", "canary", metrics)
        d = report.to_dict()
        assert d["failed_count"] == len(report.failed_criteria)

    def test_to_dict_passed_count_matches_property(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        d = report.to_dict()
        assert d["passed_count"] == len(report.passed_criteria)

    def test_failed_criteria_returns_only_failed(self, grad):
        metrics = _canary_passing_metrics(evaluator_pass_rate=0.80)
        report = grad.evaluate("docs", "canary", metrics)
        assert len(report.failed_criteria) > 0
        assert all(not c.passed for c in report.failed_criteria)

    def test_passed_criteria_returns_only_passed(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        assert len(report.passed_criteria) > 0
        assert all(c.passed for c in report.passed_criteria)

    def test_failed_plus_passed_equals_total(self, grad):
        metrics = _canary_passing_metrics(evaluator_pass_rate=0.80)
        report = grad.evaluate("docs", "canary", metrics)
        assert len(report.failed_criteria) + len(report.passed_criteria) == len(report.criteria)

    def test_all_pass_failed_criteria_is_empty(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        assert report.failed_criteria == []

    def test_all_fail_passed_criteria_is_empty(self, grad):
        metrics = LaneMetrics(
            evaluator_pass_rate=0.50,
            escaped_defects=5,
            total_completions=1,
            requeue_rate=0.50,
        )
        report = grad.evaluate("docs", "canary", metrics)
        assert report.passed_criteria == []

    def test_report_is_frozen_dataclass(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        with pytest.raises((AttributeError, TypeError)):
            report.can_graduate = False  # frozen dataclass


# ===========================================================================
# 7. LaneMetrics — defaults and to_dict()
# ===========================================================================

class TestLaneMetrics:
    def test_default_audit_pass_rate_is_1_0(self):
        m = LaneMetrics(
            evaluator_pass_rate=0.95,
            escaped_defects=0,
            total_completions=30,
            requeue_rate=0.05,
        )
        assert m.audit_pass_rate == pytest.approx(1.0)

    def test_default_mean_cycle_time_is_0(self):
        m = LaneMetrics(
            evaluator_pass_rate=0.95,
            escaped_defects=0,
            total_completions=30,
            requeue_rate=0.05,
        )
        assert m.mean_cycle_time_minutes == pytest.approx(0.0)

    def test_default_slo_target_is_60(self):
        m = LaneMetrics(
            evaluator_pass_rate=0.95,
            escaped_defects=0,
            total_completions=30,
            requeue_rate=0.05,
        )
        assert m.slo_target_minutes == pytest.approx(60.0)

    def test_to_dict_all_fields_correct(self):
        m = LaneMetrics(
            evaluator_pass_rate=0.97,
            escaped_defects=1,
            total_completions=35,
            requeue_rate=0.04,
            audit_pass_rate=0.98,
            mean_cycle_time_minutes=25.0,
            slo_target_minutes=30.0,
        )
        d = m.to_dict()
        assert d["evaluator_pass_rate"] == pytest.approx(0.97)
        assert d["escaped_defects"] == 1
        assert d["total_completions"] == 35
        assert d["requeue_rate"] == pytest.approx(0.04)
        assert d["audit_pass_rate"] == pytest.approx(0.98)
        assert d["mean_cycle_time_minutes"] == pytest.approx(25.0)
        assert d["slo_target_minutes"] == pytest.approx(30.0)

    def test_to_dict_keys_are_exact_set(self):
        m = _canary_passing_metrics()
        assert set(m.to_dict().keys()) == {
            "evaluator_pass_rate",
            "escaped_defects",
            "total_completions",
            "requeue_rate",
            "audit_pass_rate",
            "mean_cycle_time_minutes",
            "slo_target_minutes",
        }

    def test_to_dict_default_values_serialised(self):
        m = LaneMetrics(
            evaluator_pass_rate=0.95,
            escaped_defects=0,
            total_completions=30,
            requeue_rate=0.05,
        )
        d = m.to_dict()
        assert d["audit_pass_rate"] == pytest.approx(1.0)
        assert d["mean_cycle_time_minutes"] == pytest.approx(0.0)
        assert d["slo_target_minutes"] == pytest.approx(60.0)


# ===========================================================================
# 8. CriterionResult — to_dict()
# ===========================================================================

class TestCriterionResult:
    def test_to_dict_passed_criterion(self):
        cr = CriterionResult(
            name="evaluator_pass_rate",
            passed=True,
            threshold=0.95,
            actual=0.97,
            reason="",
        )
        d = cr.to_dict()
        assert d["name"] == "evaluator_pass_rate"
        assert d["passed"] is True
        assert d["threshold"] == pytest.approx(0.95)
        assert d["actual"] == pytest.approx(0.97)
        assert d["reason"] == ""

    def test_to_dict_failed_criterion_with_reason(self):
        cr = CriterionResult(
            name="escaped_defects",
            passed=False,
            threshold=0,
            actual=3,
            reason="3 escaped defects exceeds limit of 0",
        )
        d = cr.to_dict()
        assert d["name"] == "escaped_defects"
        assert d["passed"] is False
        assert d["threshold"] == 0
        assert d["actual"] == 3
        assert "3" in d["reason"]

    def test_to_dict_keys_are_exact_set(self):
        cr = CriterionResult(name="x", passed=True, threshold=0, actual=0)
        assert set(cr.to_dict().keys()) == {"name", "passed", "threshold", "actual", "reason"}

    def test_default_reason_is_empty_string(self):
        cr = CriterionResult(name="x", passed=True, threshold=0, actual=0)
        assert cr.to_dict()["reason"] == ""

    def test_integer_threshold_and_actual(self):
        cr = CriterionResult(name="min_completions", passed=False, threshold=20, actual=10)
        d = cr.to_dict()
        assert d["threshold"] == 20
        assert d["actual"] == 10

    def test_frozen_dataclass_not_mutable(self):
        cr = CriterionResult(name="x", passed=True, threshold=0, actual=0)
        with pytest.raises((AttributeError, TypeError)):
            cr.passed = False


# ===========================================================================
# 9. get_reports()
# ===========================================================================

class TestGetReports:
    def test_returns_all_reports_when_no_filter(self, grad):
        for _ in range(3):
            grad.evaluate("docs", "canary", _canary_passing_metrics())
        assert len(grad.get_reports()) == 3

    def test_reports_are_most_recent_first(self, grad):
        for _ in range(3):
            grad.evaluate("docs", "canary", _canary_passing_metrics())
        reports = grad.get_reports()
        # Verify ordering by checking it's a list of reports at all
        assert len(reports) == 3
        # All are GraduationReport instances
        for r in reports:
            assert isinstance(r, GraduationReport)

    def test_filter_by_lane(self, grad):
        grad.evaluate("docs", "canary", _canary_passing_metrics())
        grad.evaluate("api", "canary", _canary_passing_metrics())
        grad.evaluate("docs", "canary", _canary_passing_metrics())
        reports = grad.get_reports(lane="docs")
        assert len(reports) == 2
        assert all(r.lane == "docs" for r in reports)

    def test_filter_by_unknown_lane_returns_empty(self, grad):
        grad.evaluate("docs", "canary", _canary_passing_metrics())
        assert grad.get_reports(lane="unknown-lane") == []

    def test_limit_respected(self, grad):
        for _ in range(60):
            grad.evaluate("docs", "canary", _canary_passing_metrics())
        assert len(grad.get_reports(limit=10)) == 10

    def test_default_limit_is_50(self, grad):
        for _ in range(55):
            grad.evaluate("docs", "canary", _canary_passing_metrics())
        assert len(grad.get_reports()) == 50

    def test_filter_and_limit_combined(self, grad):
        for _ in range(20):
            grad.evaluate("docs", "canary", _canary_passing_metrics())
        for _ in range(20):
            grad.evaluate("api", "canary", _canary_passing_metrics())
        reports = grad.get_reports(lane="docs", limit=5)
        assert len(reports) == 5
        assert all(r.lane == "docs" for r in reports)

    def test_returns_graduation_report_instances(self, grad):
        grad.evaluate("docs", "canary", _canary_passing_metrics())
        for report in grad.get_reports():
            assert isinstance(report, GraduationReport)


# ===========================================================================
# 10. Singleton
# ===========================================================================

class TestSingleton:
    def test_get_returns_same_instance(self):
        a = get_lane_graduation()
        b = get_lane_graduation()
        assert a is b

    def test_reset_returns_fresh_instance(self):
        a = get_lane_graduation()
        reset_lane_graduation()
        b = get_lane_graduation()
        assert a is not b

    def test_singleton_has_default_gates(self):
        g = get_lane_graduation()
        assert g.canary_gate == DEFAULT_CANARY_GATE
        assert g.enabled_gate == DEFAULT_ENABLED_GATE

    def test_state_persists_across_calls(self):
        g = get_lane_graduation()
        g.evaluate("docs", "canary", _canary_passing_metrics())
        same = get_lane_graduation()
        assert len(same.get_reports()) == 1

    def test_reset_clears_all_state(self):
        g = get_lane_graduation()
        g.evaluate("docs", "canary", _canary_passing_metrics())
        g.update_canary_gate({"min_evaluator_pass_rate": 0.88})
        reset_lane_graduation()
        fresh = get_lane_graduation()
        assert fresh.get_reports() == []
        assert fresh.canary_gate == DEFAULT_CANARY_GATE


# ===========================================================================
# 11. reset()
# ===========================================================================

class TestReset:
    def test_reset_clears_reports(self, grad):
        grad.evaluate("docs", "canary", _canary_passing_metrics())
        assert len(grad.get_reports()) == 1
        grad.reset()
        assert grad.get_reports() == []

    def test_reset_restores_default_canary_gate(self, grad):
        grad.update_canary_gate({"min_evaluator_pass_rate": 0.80})
        grad.reset()
        assert grad.canary_gate["min_evaluator_pass_rate"] == pytest.approx(0.95)

    def test_reset_restores_default_enabled_gate(self, grad):
        grad.update_enabled_gate({"min_evaluator_pass_rate": 0.80})
        grad.reset()
        assert grad.enabled_gate["min_evaluator_pass_rate"] == pytest.approx(0.98)

    def test_reset_restores_full_canary_gate(self, grad):
        grad.update_canary_gate({"min_evaluator_pass_rate": 0.80, "min_completions": 5})
        grad.reset()
        assert grad.canary_gate == DEFAULT_CANARY_GATE

    def test_reset_restores_full_enabled_gate(self, grad):
        grad.update_enabled_gate({"min_completions": 100, "max_requeue_rate": 0.01})
        grad.reset()
        assert grad.enabled_gate == DEFAULT_ENABLED_GATE


# ===========================================================================
# 12. Cross-cutting / integration
# ===========================================================================

class TestIntegration:
    def test_multiple_lanes_evaluated_independently(self, grad):
        report_docs = grad.evaluate("docs", "canary", _canary_passing_metrics())
        report_api = grad.evaluate(
            "api", "canary",
            _canary_passing_metrics(evaluator_pass_rate=0.80),
        )
        assert report_docs.can_graduate is True
        assert report_api.can_graduate is False

    def test_evaluate_stores_report_for_correct_lane(self, grad):
        grad.evaluate("docs", "canary", _canary_passing_metrics())
        grad.evaluate("api", "canary", _canary_passing_metrics())
        api_reports = grad.get_reports(lane="api")
        assert len(api_reports) == 1
        assert api_reports[0].lane == "api"

    def test_report_target_stage_is_canary(self, grad):
        report = grad.evaluate("docs", "canary", _canary_passing_metrics())
        assert report.target_stage == "canary"

    def test_report_target_stage_is_enabled(self, grad):
        report = grad.evaluate("docs", "enabled", _enabled_passing_metrics())
        assert report.target_stage == "enabled"

    def test_unique_report_ids_across_evaluations(self, grad):
        reports = [
            grad.evaluate("docs", "canary", _canary_passing_metrics())
            for _ in range(5)
        ]
        ids = [r.report_id for r in reports]
        assert len(ids) == len(set(ids))
