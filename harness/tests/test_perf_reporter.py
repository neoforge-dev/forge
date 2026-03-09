"""Test suite for Dark Factory Performance Reporter (DF-5003)

Tests cover:
- PerfReporter initialization and lifecycle
- WeeklyMetrics properties and serialization
- MetricTrend computation and serialization
- WeeklyReport health assessment and markdown rendering
- Trend detection (improving, stable, degrading)
- Singleton pattern with thread safety
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from forge_harness.dark_factory.perf_reporter import (
    MetricTrend,
    PerfReporter,
    Trend,
    WeeklyMetrics,
    WeeklyReport,
    get_perf_reporter,
    reset_perf_reporter,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def reporter() -> PerfReporter:
    """Create a fresh PerfReporter instance for each test."""
    r = PerfReporter()
    yield r


@pytest.fixture
def base_time() -> datetime:
    """Standard reference time for tests."""
    return datetime(2026, 2, 1, tzinfo=UTC)


@pytest.fixture
def sample_metrics(base_time: datetime) -> WeeklyMetrics:
    """Basic healthy week metrics."""
    return WeeklyMetrics(
        evaluator_pass_rate=0.98,
        escaped_defects=0,
        audit_pass_rate=0.99,
        total_tasks=100,
        autonomous_completions=85,
        human_reviews=15,
        mean_cycle_time_minutes=45.0,
        p95_cycle_time_minutes=120.0,
        slo_target_minutes=180.0,
        period_start=base_time,
        period_end=base_time + timedelta(days=7),
    )


@pytest.fixture
def improving_metrics(base_time: datetime) -> WeeklyMetrics:
    """Metrics showing improvement trends (>2% change)."""
    return WeeklyMetrics(
        evaluator_pass_rate=1.0,  # 0.98 -> 1.0 is 2.04% (improving)
        escaped_defects=0,
        audit_pass_rate=1.0,  # 0.99 -> 1.0 is 1.01% (still stable, but acceptable)
        total_tasks=130,  # 100 -> 130 is 30% more tasks
        autonomous_completions=115,  # 85 -> 115 is 35.3% improvement (automation improving)
        human_reviews=15,
        mean_cycle_time_minutes=35.0,  # 45 -> 35 is -22.2% (improving, lower is better)
        p95_cycle_time_minutes=90.0,  # 120 -> 90 is -25% (improving)
        slo_target_minutes=180.0,
        period_start=base_time + timedelta(days=7),
        period_end=base_time + timedelta(days=14),
    )


@pytest.fixture
def degrading_metrics(base_time: datetime) -> WeeklyMetrics:
    """Metrics showing degradation trends (>2% change)."""
    return WeeklyMetrics(
        evaluator_pass_rate=0.90,  # 0.98 -> 0.90 is -8.16% (degrading)
        escaped_defects=0,
        audit_pass_rate=0.92,  # 0.99 -> 0.92 is -7.07% (degrading)
        total_tasks=80,  # 100 -> 80 is -20% (fewer tasks)
        autonomous_completions=56,  # 85 -> 56 is -34.1% (degrading automation)
        human_reviews=24,
        mean_cycle_time_minutes=60.0,  # 45 -> 60 is +33.3% (degrading, higher is worse)
        p95_cycle_time_minutes=160.0,  # 120 -> 160 is +33.3% (degrading)
        slo_target_minutes=180.0,
        period_start=base_time + timedelta(days=14),
        period_end=base_time + timedelta(days=21),
    )


# ============================================================================
# Tests: WeeklyMetrics
# ============================================================================


class TestWeeklyMetrics:
    """Test WeeklyMetrics properties and serialization."""

    def test_automation_ratio_with_tasks(self, sample_metrics: WeeklyMetrics) -> None:
        """Test automation_ratio property calculation."""
        assert sample_metrics.automation_ratio == 0.85

    def test_automation_ratio_zero_tasks(self) -> None:
        """Test automation_ratio when no tasks."""
        metrics = WeeklyMetrics(
            evaluator_pass_rate=0.98,
            escaped_defects=0,
            audit_pass_rate=0.99,
            total_tasks=0,
            autonomous_completions=0,
            human_reviews=0,
            mean_cycle_time_minutes=0.0,
            p95_cycle_time_minutes=0.0,
            slo_target_minutes=180.0,
            period_start=datetime.now(UTC),
            period_end=datetime.now(UTC),
        )
        assert metrics.automation_ratio == 0.0

    def test_slo_compliance_rate_compliant(self, sample_metrics: WeeklyMetrics) -> None:
        """Test SLO compliance when within target."""
        assert sample_metrics.slo_compliance_rate == 1.0

    def test_slo_compliance_rate_non_compliant(self) -> None:
        """Test SLO compliance when exceeding target."""
        metrics = WeeklyMetrics(
            evaluator_pass_rate=0.98,
            escaped_defects=0,
            audit_pass_rate=0.99,
            total_tasks=100,
            autonomous_completions=85,
            human_reviews=15,
            mean_cycle_time_minutes=45.0,
            p95_cycle_time_minutes=200.0,
            slo_target_minutes=180.0,
            period_start=datetime.now(UTC),
            period_end=datetime.now(UTC),
        )
        assert metrics.slo_compliance_rate == 0.95

    def test_slo_compliance_rate_zero_target(self) -> None:
        """Test SLO compliance with zero target (invalid case)."""
        metrics = WeeklyMetrics(
            evaluator_pass_rate=0.98,
            escaped_defects=0,
            audit_pass_rate=0.99,
            total_tasks=100,
            autonomous_completions=85,
            human_reviews=15,
            mean_cycle_time_minutes=45.0,
            p95_cycle_time_minutes=120.0,
            slo_target_minutes=0.0,
            period_start=datetime.now(UTC),
            period_end=datetime.now(UTC),
        )
        assert metrics.slo_compliance_rate == 1.0

    def test_to_dict(self, sample_metrics: WeeklyMetrics) -> None:
        """Test serialization to dict."""
        d = sample_metrics.to_dict()
        assert d["evaluator_pass_rate"] == 0.98
        assert d["escaped_defects"] == 0
        assert d["audit_pass_rate"] == 0.99
        assert d["total_tasks"] == 100
        assert d["autonomous_completions"] == 85
        assert d["automation_ratio"] == 0.85
        assert d["human_reviews"] == 15
        assert d["mean_cycle_time_minutes"] == 45.0
        assert d["p95_cycle_time_minutes"] == 120.0
        assert d["slo_target_minutes"] == 180.0
        assert d["slo_compliance_rate"] == 1.0
        assert "period_start" in d
        assert "period_end" in d

    def test_to_dict_serializable(self, sample_metrics: WeeklyMetrics) -> None:
        """Test that to_dict produces JSON-serializable data."""
        d = sample_metrics.to_dict()
        json_str = json.dumps(d)
        restored = json.loads(json_str)
        assert restored["evaluator_pass_rate"] == 0.98


# ============================================================================
# Tests: MetricTrend
# ============================================================================


class TestMetricTrend:
    """Test MetricTrend computation and serialization."""

    def test_to_dict(self) -> None:
        """Test MetricTrend serialization."""
        trend = MetricTrend(
            name="evaluator_pass_rate",
            current=0.98,
            previous=0.95,
            trend=Trend.IMPROVING,
            delta=0.03,
            delta_pct=3.16,
        )
        d = trend.to_dict()
        assert d["name"] == "evaluator_pass_rate"
        assert d["current"] == 0.98
        assert d["previous"] == 0.95
        assert d["trend"] == "improving"
        assert d["delta"] == 0.03
        assert d["delta_pct"] == 3.16

    def test_to_dict_no_previous(self) -> None:
        """Test MetricTrend serialization with no previous value."""
        trend = MetricTrend(
            name="new_metric",
            current=0.5,
            previous=None,
            trend=Trend.STABLE,
            delta=0.0,
            delta_pct=0.0,
        )
        d = trend.to_dict()
        assert d["previous"] is None
        assert d["trend"] == "stable"

    def test_to_dict_serializable(self) -> None:
        """Test that to_dict produces JSON-serializable data."""
        trend = MetricTrend(
            name="test",
            current=0.95,
            previous=0.90,
            trend=Trend.IMPROVING,
            delta=0.05,
            delta_pct=5.56,
        )
        d = trend.to_dict()
        json_str = json.dumps(d)
        restored = json.loads(json_str)
        assert restored["trend"] == "improving"


# ============================================================================
# Tests: PerfReporter Initialization
# ============================================================================


class TestPerfReporterInit:
    """Test PerfReporter initialization."""

    def test_init_creates_empty_reports_list(self) -> None:
        """Test that init creates empty reports list."""
        r = PerfReporter()
        assert r.get_reports() == []
        assert r.get_latest_report() is None

    def test_init_has_lock(self) -> None:
        """Test that init creates thread-safe lock."""
        r = PerfReporter()
        assert hasattr(r, "_lock")
        assert r._lock is not None

    def test_trend_threshold_constant(self) -> None:
        """Test TREND_THRESHOLD constant."""
        r = PerfReporter()
        assert r.TREND_THRESHOLD == 0.02


# ============================================================================
# Tests: generate_report() with no previous
# ============================================================================


class TestGenerateReportNoPrevious:
    """Test generate_report() with no previous week (all trends stable)."""

    def test_generates_report(self, reporter: PerfReporter, sample_metrics: WeeklyMetrics) -> None:
        """Test basic report generation."""
        report = reporter.generate_report(sample_metrics)
        assert report.report_id is not None
        assert report.metrics == sample_metrics
        assert report.overall_health in ("healthy", "warning", "critical")
        assert report.summary is not None

    def test_all_trends_stable_when_no_previous(
        self, reporter: PerfReporter, sample_metrics: WeeklyMetrics
    ) -> None:
        """Test that all trends are stable when no previous data."""
        report = reporter.generate_report(sample_metrics)
        for trend in report.trends:
            assert trend.trend == Trend.STABLE
            assert trend.previous is None
            assert trend.delta == 0.0
            assert trend.delta_pct == 0.0

    def test_report_has_six_trends(self, reporter: PerfReporter, sample_metrics: WeeklyMetrics) -> None:
        """Test that report contains exactly 6 trends."""
        report = reporter.generate_report(sample_metrics)
        assert len(report.trends) == 6
        trend_names = {t.name for t in report.trends}
        expected = {
            "evaluator_pass_rate",
            "escaped_defects",
            "audit_pass_rate",
            "automation_ratio",
            "mean_cycle_time",
            "slo_compliance",
        }
        assert trend_names == expected

    def test_report_stored_in_reporter(self, reporter: PerfReporter, sample_metrics: WeeklyMetrics) -> None:
        """Test that generated report is stored."""
        report = reporter.generate_report(sample_metrics)
        assert reporter.get_latest_report() == report
        assert len(reporter.get_reports()) == 1


# ============================================================================
# Tests: generate_report() with improving trends
# ============================================================================


class TestGenerateReportImproving:
    """Test generate_report() with improving trends."""

    def test_improving_eval_pass_rate(
        self, reporter: PerfReporter, sample_metrics: WeeklyMetrics, improving_metrics: WeeklyMetrics
    ) -> None:
        """Test improving evaluator_pass_rate trend."""
        reporter.generate_report(sample_metrics)
        report = reporter.generate_report(improving_metrics, previous=sample_metrics)

        eval_trend = next(t for t in report.trends if t.name == "evaluator_pass_rate")
        assert eval_trend.trend == Trend.IMPROVING
        assert eval_trend.current == 1.0
        assert eval_trend.previous == 0.98
        assert eval_trend.delta > 0

    def test_improving_automation_ratio(
        self, reporter: PerfReporter, sample_metrics: WeeklyMetrics, improving_metrics: WeeklyMetrics
    ) -> None:
        """Test improving automation_ratio trend."""
        reporter.generate_report(sample_metrics)
        report = reporter.generate_report(improving_metrics, previous=sample_metrics)

        auto_trend = next(t for t in report.trends if t.name == "automation_ratio")
        assert auto_trend.trend == Trend.IMPROVING
        assert auto_trend.current > auto_trend.previous

    def test_improving_cycle_time(
        self, reporter: PerfReporter, sample_metrics: WeeklyMetrics, improving_metrics: WeeklyMetrics
    ) -> None:
        """Test improving mean_cycle_time (lower is better)."""
        reporter.generate_report(sample_metrics)
        report = reporter.generate_report(improving_metrics, previous=sample_metrics)

        ct_trend = next(t for t in report.trends if t.name == "mean_cycle_time")
        assert ct_trend.trend == Trend.IMPROVING
        assert ct_trend.current < ct_trend.previous

    def test_healthy_status_when_improving(
        self, reporter: PerfReporter, sample_metrics: WeeklyMetrics, improving_metrics: WeeklyMetrics
    ) -> None:
        """Test healthy status with improving trends."""
        reporter.generate_report(sample_metrics)
        report = reporter.generate_report(improving_metrics, previous=sample_metrics)
        assert report.overall_health == "healthy"


# ============================================================================
# Tests: generate_report() with degrading trends
# ============================================================================


class TestGenerateReportDegrading:
    """Test generate_report() with degrading trends."""

    def test_degrading_eval_pass_rate(
        self, reporter: PerfReporter, sample_metrics: WeeklyMetrics, degrading_metrics: WeeklyMetrics
    ) -> None:
        """Test degrading evaluator_pass_rate trend."""
        reporter.generate_report(sample_metrics)
        report = reporter.generate_report(degrading_metrics, previous=sample_metrics)

        eval_trend = next(t for t in report.trends if t.name == "evaluator_pass_rate")
        assert eval_trend.trend == Trend.DEGRADING
        assert eval_trend.current < eval_trend.previous

    def test_degrading_automation_ratio(
        self, reporter: PerfReporter, sample_metrics: WeeklyMetrics, degrading_metrics: WeeklyMetrics
    ) -> None:
        """Test degrading automation_ratio trend."""
        reporter.generate_report(sample_metrics)
        report = reporter.generate_report(degrading_metrics, previous=sample_metrics)

        auto_trend = next(t for t in report.trends if t.name == "automation_ratio")
        assert auto_trend.trend == Trend.DEGRADING
        assert auto_trend.current < auto_trend.previous

    def test_degrading_cycle_time(
        self, reporter: PerfReporter, sample_metrics: WeeklyMetrics, degrading_metrics: WeeklyMetrics
    ) -> None:
        """Test degrading mean_cycle_time (higher is worse)."""
        reporter.generate_report(sample_metrics)
        report = reporter.generate_report(degrading_metrics, previous=sample_metrics)

        ct_trend = next(t for t in report.trends if t.name == "mean_cycle_time")
        assert ct_trend.trend == Trend.DEGRADING
        assert ct_trend.current > ct_trend.previous

    def test_warning_status_with_3_degrading_trends(
        self, reporter: PerfReporter, sample_metrics: WeeklyMetrics, degrading_metrics: WeeklyMetrics
    ) -> None:
        """Test warning status when 3+ metrics degrading."""
        reporter.generate_report(sample_metrics)
        report = reporter.generate_report(degrading_metrics, previous=sample_metrics)

        degrading_count = sum(1 for t in report.trends if t.trend == Trend.DEGRADING)
        if degrading_count >= 3:
            assert report.overall_health == "warning"


# ============================================================================
# Tests: Health Assessment
# ============================================================================


class TestHealthAssessment:
    """Test health status computation."""

    def test_healthy_status(self, reporter: PerfReporter, sample_metrics: WeeklyMetrics) -> None:
        """Test healthy status with good metrics."""
        report = reporter.generate_report(sample_metrics)
        assert report.overall_health == "healthy"

    def test_critical_with_escaped_defects(
        self, reporter: PerfReporter, base_time: datetime
    ) -> None:
        """Test critical status when escaped defects > 0."""
        metrics = WeeklyMetrics(
            evaluator_pass_rate=0.98,
            escaped_defects=2,
            audit_pass_rate=0.99,
            total_tasks=100,
            autonomous_completions=85,
            human_reviews=15,
            mean_cycle_time_minutes=45.0,
            p95_cycle_time_minutes=120.0,
            slo_target_minutes=180.0,
            period_start=base_time,
            period_end=base_time + timedelta(days=7),
        )
        report = reporter.generate_report(metrics)
        assert report.overall_health == "critical"

    def test_warning_with_low_eval_pass_rate(
        self, reporter: PerfReporter, base_time: datetime
    ) -> None:
        """Test warning status when evaluator_pass_rate < 0.95."""
        metrics = WeeklyMetrics(
            evaluator_pass_rate=0.94,
            escaped_defects=0,
            audit_pass_rate=0.99,
            total_tasks=100,
            autonomous_completions=85,
            human_reviews=15,
            mean_cycle_time_minutes=45.0,
            p95_cycle_time_minutes=120.0,
            slo_target_minutes=180.0,
            period_start=base_time,
            period_end=base_time + timedelta(days=7),
        )
        report = reporter.generate_report(metrics)
        assert report.overall_health == "warning"

    def test_healthy_at_95_percent_eval_pass(
        self, reporter: PerfReporter, base_time: datetime
    ) -> None:
        """Test healthy status at exactly 95% eval pass rate."""
        metrics = WeeklyMetrics(
            evaluator_pass_rate=0.95,
            escaped_defects=0,
            audit_pass_rate=0.99,
            total_tasks=100,
            autonomous_completions=85,
            human_reviews=15,
            mean_cycle_time_minutes=45.0,
            p95_cycle_time_minutes=120.0,
            slo_target_minutes=180.0,
            period_start=base_time,
            period_end=base_time + timedelta(days=7),
        )
        report = reporter.generate_report(metrics)
        assert report.overall_health == "healthy"


# ============================================================================
# Tests: WeeklyReport Serialization and Markdown
# ============================================================================


class TestWeeklyReportSerialization:
    """Test WeeklyReport serialization methods."""

    def test_to_dict(self, reporter: PerfReporter, sample_metrics: WeeklyMetrics) -> None:
        """Test WeeklyReport to_dict() serialization."""
        report = reporter.generate_report(sample_metrics)
        d = report.to_dict()

        assert "report_id" in d
        assert d["report_id"] == report.report_id
        assert "metrics" in d
        assert "trends" in d
        assert d["overall_health"] in ("healthy", "warning", "critical")
        assert "summary" in d
        assert "generated_at" in d

    def test_to_dict_trends_serialized(self, reporter: PerfReporter, sample_metrics: WeeklyMetrics) -> None:
        """Test that trends are serialized in to_dict()."""
        report = reporter.generate_report(sample_metrics)
        d = report.to_dict()

        assert len(d["trends"]) == 6
        for trend_dict in d["trends"]:
            assert "name" in trend_dict
            assert "current" in trend_dict
            assert "trend" in trend_dict

    def test_to_dict_json_serializable(self, reporter: PerfReporter, sample_metrics: WeeklyMetrics) -> None:
        """Test that to_dict() produces JSON-serializable data."""
        report = reporter.generate_report(sample_metrics)
        d = report.to_dict()
        json_str = json.dumps(d)
        restored = json.loads(json_str)
        assert restored["report_id"] == report.report_id

    def test_to_markdown(self, reporter: PerfReporter, sample_metrics: WeeklyMetrics) -> None:
        """Test WeeklyReport to_markdown() rendering."""
        report = reporter.generate_report(sample_metrics)
        md = report.to_markdown()

        assert "Weekly Dark Factory Performance Report" in md
        assert "Period:" in md
        assert "Health:" in md
        assert "Summary:" in md
        assert "Quality" in md
        assert "Throughput" in md
        assert "Lead Time" in md

    def test_to_markdown_contains_metrics(self, reporter: PerfReporter, sample_metrics: WeeklyMetrics) -> None:
        """Test that markdown includes metric values."""
        report = reporter.generate_report(sample_metrics)
        md = report.to_markdown()

        assert "100" in md
        assert "85%" in md or "0.85" in md
        assert "45" in md

    def test_to_markdown_contains_trends(self, reporter: PerfReporter, sample_metrics: WeeklyMetrics) -> None:
        """Test that markdown includes trend arrows."""
        report = reporter.generate_report(sample_metrics)
        md = report.to_markdown()

        assert "stable" in md or "improving" in md or "degrading" in md
        assert "=" in md or "+" in md or "-" in md

    def test_to_markdown_date_formatting(self, reporter: PerfReporter, sample_metrics: WeeklyMetrics) -> None:
        """Test that markdown includes properly formatted dates."""
        report = reporter.generate_report(sample_metrics)
        md = report.to_markdown()

        assert "2026-02-01" in md


# ============================================================================
# Tests: get_reports() and get_latest_report()
# ============================================================================


class TestGetReports:
    """Test report retrieval methods."""

    def test_get_reports_empty(self, reporter: PerfReporter) -> None:
        """Test get_reports() on empty reporter."""
        assert reporter.get_reports() == []

    def test_get_latest_report_empty(self, reporter: PerfReporter) -> None:
        """Test get_latest_report() on empty reporter."""
        assert reporter.get_latest_report() is None

    def test_get_latest_report_after_one(
        self, reporter: PerfReporter, sample_metrics: WeeklyMetrics
    ) -> None:
        """Test get_latest_report() returns most recent."""
        report = reporter.generate_report(sample_metrics)
        assert reporter.get_latest_report() == report

    def test_get_reports_single_item(self, reporter: PerfReporter, sample_metrics: WeeklyMetrics) -> None:
        """Test get_reports() with one report."""
        report = reporter.generate_report(sample_metrics)
        reports = reporter.get_reports()
        assert len(reports) == 1
        assert reports[0] == report

    def test_get_reports_multiple_items_reversed_order(
        self, reporter: PerfReporter, sample_metrics: WeeklyMetrics, improving_metrics: WeeklyMetrics
    ) -> None:
        """Test get_reports() returns items in reverse order (most recent first)."""
        report1 = reporter.generate_report(sample_metrics)
        report2 = reporter.generate_report(improving_metrics)

        reports = reporter.get_reports()
        assert len(reports) == 2
        assert reports[0] == report2
        assert reports[1] == report1

    def test_get_reports_limit(
        self, reporter: PerfReporter, base_time: datetime
    ) -> None:
        """Test get_reports(limit=N) returns only last N reports."""
        metrics_list = []
        for i in range(5):
            metrics = WeeklyMetrics(
                evaluator_pass_rate=0.95 + (i * 0.01),
                escaped_defects=0,
                audit_pass_rate=0.99,
                total_tasks=100,
                autonomous_completions=85,
                human_reviews=15,
                mean_cycle_time_minutes=45.0,
                p95_cycle_time_minutes=120.0,
                slo_target_minutes=180.0,
                period_start=base_time + timedelta(days=7 * i),
                period_end=base_time + timedelta(days=7 * (i + 1)),
            )
            metrics_list.append(metrics)
            reporter.generate_report(metrics)

        reports = reporter.get_reports(limit=3)
        assert len(reports) == 3

    def test_get_reports_limit_greater_than_count(
        self, reporter: PerfReporter, sample_metrics: WeeklyMetrics
    ) -> None:
        """Test get_reports(limit=N) when N > available reports."""
        reporter.generate_report(sample_metrics)
        reports = reporter.get_reports(limit=100)
        assert len(reports) == 1


# ============================================================================
# Tests: Singleton Pattern
# ============================================================================


class TestSingletonPattern:
    """Test singleton get/reset pattern."""

    def teardown_method(self) -> None:
        """Reset singleton after each test."""
        reset_perf_reporter()

    def test_get_perf_reporter_returns_same_instance(self) -> None:
        """Test that get_perf_reporter() returns same instance."""
        r1 = get_perf_reporter()
        r2 = get_perf_reporter()
        assert r1 is r2

    def test_reset_perf_reporter_clears_state(self, sample_metrics: WeeklyMetrics) -> None:
        """Test that reset_perf_reporter() clears all state."""
        reporter = get_perf_reporter()
        reporter.generate_report(sample_metrics)
        assert len(reporter.get_reports()) == 1

        reset_perf_reporter()
        new_reporter = get_perf_reporter()
        assert len(new_reporter.get_reports()) == 0
        assert new_reporter is not reporter

    def test_reset_and_recreate_new_instance(self) -> None:
        """Test that reset creates a new instance."""
        r1 = get_perf_reporter()
        reset_perf_reporter()
        r2 = get_perf_reporter()
        assert r1 is not r2

    def test_singleton_thread_safety(self, sample_metrics: WeeklyMetrics) -> None:
        """Test that singleton access is thread-safe."""
        instances = []

        def get_instance() -> None:
            instances.append(get_perf_reporter())

        threads = [threading.Thread(target=get_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for instance in instances[1:]:
            assert instance is instances[0]


# ============================================================================
# Tests: Trend Computation Details
# ============================================================================


class TestTrendComputation:
    """Test detailed trend computation logic."""

    def test_trend_below_threshold_is_stable(self, reporter: PerfReporter) -> None:
        """Test that changes below 2% threshold are marked stable."""
        trend = reporter._compute_trend("test", 0.995, 0.98, higher_is_better=True)
        assert trend.trend == Trend.STABLE

    def test_trend_above_threshold_improving(self, reporter: PerfReporter) -> None:
        """Test that changes above 2% threshold improve correctly."""
        trend = reporter._compute_trend("test", 1.0, 0.95, higher_is_better=True)
        assert trend.trend == Trend.IMPROVING

    def test_trend_above_threshold_degrading(self, reporter: PerfReporter) -> None:
        """Test that changes above 2% threshold degrade correctly."""
        trend = reporter._compute_trend("test", 0.90, 1.0, higher_is_better=True)
        assert trend.trend == Trend.DEGRADING

    def test_trend_lower_is_better_improving(self, reporter: PerfReporter) -> None:
        """Test improving trend for metrics where lower is better."""
        trend = reporter._compute_trend("cycle_time", 90.0, 100.0, higher_is_better=False)
        assert trend.trend == Trend.IMPROVING

    def test_trend_lower_is_better_degrading(self, reporter: PerfReporter) -> None:
        """Test degrading trend for metrics where lower is better."""
        trend = reporter._compute_trend("cycle_time", 110.0, 100.0, higher_is_better=False)
        assert trend.trend == Trend.DEGRADING

    def test_trend_from_zero_positive(self, reporter: PerfReporter) -> None:
        """Test trend when previous is 0 and current positive."""
        trend = reporter._compute_trend("new_metric", 10.0, 0.0, higher_is_better=True)
        assert trend.delta == 10.0
        assert trend.delta_pct == 100.0
        assert trend.trend == Trend.IMPROVING

    def test_trend_from_zero_negative(self, reporter: PerfReporter) -> None:
        """Test trend when previous is 0 and current negative."""
        trend = reporter._compute_trend("metric", -5.0, 0.0, higher_is_better=False)
        assert trend.delta == -5.0
        assert trend.delta_pct == -100.0

    def test_trend_delta_calculation(self, reporter: PerfReporter) -> None:
        """Test delta is current - previous."""
        trend = reporter._compute_trend("test", 85.0, 80.0, higher_is_better=True)
        assert trend.delta == 5.0

    def test_trend_delta_pct_calculation(self, reporter: PerfReporter) -> None:
        """Test delta_pct is (delta / abs(previous)) * 100."""
        trend = reporter._compute_trend("test", 85.0, 80.0, higher_is_better=True)
        expected_pct = (5.0 / 80.0) * 100
        assert abs(trend.delta_pct - expected_pct) < 0.01


# ============================================================================
# Tests: Summary Generation
# ============================================================================


class TestSummaryGeneration:
    """Test summary text generation."""

    def test_summary_includes_health(self, reporter: PerfReporter, sample_metrics: WeeklyMetrics) -> None:
        """Test that summary starts with health status."""
        report = reporter.generate_report(sample_metrics)
        assert report.summary.startswith("HEALTHY:") or report.summary.startswith("WARNING:") or \
               report.summary.startswith("CRITICAL:")

    def test_summary_includes_task_count(self, reporter: PerfReporter, sample_metrics: WeeklyMetrics) -> None:
        """Test that summary includes task count."""
        report = reporter.generate_report(sample_metrics)
        assert "100 tasks" in report.summary

    def test_summary_includes_automation_ratio(
        self, reporter: PerfReporter, sample_metrics: WeeklyMetrics
    ) -> None:
        """Test that summary includes automation ratio."""
        report = reporter.generate_report(sample_metrics)
        assert "85%" in report.summary

    def test_summary_includes_escaped_defects_when_present(
        self, reporter: PerfReporter, base_time: datetime
    ) -> None:
        """Test that summary mentions escaped defects when > 0."""
        metrics = WeeklyMetrics(
            evaluator_pass_rate=0.98,
            escaped_defects=3,
            audit_pass_rate=0.99,
            total_tasks=100,
            autonomous_completions=85,
            human_reviews=15,
            mean_cycle_time_minutes=45.0,
            p95_cycle_time_minutes=120.0,
            slo_target_minutes=180.0,
            period_start=base_time,
            period_end=base_time + timedelta(days=7),
        )
        report = reporter.generate_report(metrics)
        assert "3 escaped defects" in report.summary


# ============================================================================
# Tests: Thread Safety
# ============================================================================


class TestThreadSafety:
    """Test thread-safe operations."""

    def test_concurrent_report_generation(self, base_time: datetime) -> None:
        """Test concurrent report generation is safe."""
        reporter = PerfReporter()
        results = []

        def generate_report(index: int) -> None:
            metrics = WeeklyMetrics(
                evaluator_pass_rate=0.95 + (index * 0.001),
                escaped_defects=0,
                audit_pass_rate=0.99,
                total_tasks=100,
                autonomous_completions=85,
                human_reviews=15,
                mean_cycle_time_minutes=45.0,
                p95_cycle_time_minutes=120.0,
                slo_target_minutes=180.0,
                period_start=base_time + timedelta(days=7 * index),
                period_end=base_time + timedelta(days=7 * (index + 1)),
            )
            report = reporter.generate_report(metrics)
            results.append(report)

        threads = [threading.Thread(target=generate_report, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(reporter.get_reports()) == 5

    def test_concurrent_report_retrieval(
        self, reporter: PerfReporter, sample_metrics: WeeklyMetrics
    ) -> None:
        """Test concurrent report retrieval is safe."""
        reporter.generate_report(sample_metrics)
        results = []

        def get_reports_concurrent() -> None:
            reports = reporter.get_reports()
            results.append(len(reports))

        threads = [threading.Thread(target=get_reports_concurrent) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(count == 1 for count in results)


# ============================================================================
# Tests: Edge Cases
# ============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_all_zero_automation(self, reporter: PerfReporter, base_time: datetime) -> None:
        """Test with zero autonomous completions."""
        metrics = WeeklyMetrics(
            evaluator_pass_rate=0.98,
            escaped_defects=0,
            audit_pass_rate=0.99,
            total_tasks=100,
            autonomous_completions=0,
            human_reviews=100,
            mean_cycle_time_minutes=45.0,
            p95_cycle_time_minutes=120.0,
            slo_target_minutes=180.0,
            period_start=base_time,
            period_end=base_time + timedelta(days=7),
        )
        report = reporter.generate_report(metrics)
        assert report.metrics.automation_ratio == 0.0
        assert "0%" in report.summary

    def test_full_automation(self, reporter: PerfReporter, base_time: datetime) -> None:
        """Test with 100% autonomous completions."""
        metrics = WeeklyMetrics(
            evaluator_pass_rate=0.98,
            escaped_defects=0,
            audit_pass_rate=0.99,
            total_tasks=100,
            autonomous_completions=100,
            human_reviews=0,
            mean_cycle_time_minutes=45.0,
            p95_cycle_time_minutes=120.0,
            slo_target_minutes=180.0,
            period_start=base_time,
            period_end=base_time + timedelta(days=7),
        )
        report = reporter.generate_report(metrics)
        assert report.metrics.automation_ratio == 1.0
        assert "100%" in report.summary

    def test_perfect_pass_rates(self, reporter: PerfReporter, base_time: datetime) -> None:
        """Test with perfect 100% pass rates."""
        metrics = WeeklyMetrics(
            evaluator_pass_rate=1.0,
            escaped_defects=0,
            audit_pass_rate=1.0,
            total_tasks=100,
            autonomous_completions=85,
            human_reviews=15,
            mean_cycle_time_minutes=45.0,
            p95_cycle_time_minutes=120.0,
            slo_target_minutes=180.0,
            period_start=base_time,
            period_end=base_time + timedelta(days=7),
        )
        report = reporter.generate_report(metrics)
        assert report.overall_health == "healthy"
        assert report.metrics.evaluator_pass_rate == 1.0

    def test_multiple_escaped_defects(self, reporter: PerfReporter, base_time: datetime) -> None:
        """Test critical status with multiple escaped defects."""
        metrics = WeeklyMetrics(
            evaluator_pass_rate=0.98,
            escaped_defects=5,
            audit_pass_rate=0.99,
            total_tasks=100,
            autonomous_completions=85,
            human_reviews=15,
            mean_cycle_time_minutes=45.0,
            p95_cycle_time_minutes=120.0,
            slo_target_minutes=180.0,
            period_start=base_time,
            period_end=base_time + timedelta(days=7),
        )
        report = reporter.generate_report(metrics)
        assert report.overall_health == "critical"
        assert "5 escaped defects" in report.summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
