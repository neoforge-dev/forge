"""
Tests for forge_harness/webhook_server/services/performance_reporter.py
=========================================================================

Coverage targets (90%+):
- generate_weekly_report: nominal path with tasks
- generate_weekly_report: empty task list
- generate_weekly_report: task source failure propagation
- Report storage to disk
- SSE event emission
- Lane-level metrics computation
- Health score calculation
- schedule_weekly scheduling
- Singleton factory: get_performance_reporter, reset_performance_reporter
- Report loading and listing
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.webhook_server.models.lane_policy import Lane
from forge_harness.webhook_server.services.performance_reporter import (
    _WEEK_DURATION,
    _WEEKLY_REPORT_EVENT_TYPE,
    HIGH_RISK_LANES,
    LOW_RISK_LANES,
    LaneMetrics,
    PerformanceReport,
    PerformanceReporter,
    PerformanceReportEvent,
    classify_lane,
    compute_lane_metrics,
    compute_weekly_report,
    get_performance_reporter,
    next_sunday_midnight_utc,
    reset_performance_reporter,
)

# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------

T0 = datetime(2026, 2, 16, 0, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=7)


def _make_task(
    *,
    dispatch_acked: bool = True,
    evaluator_passed: bool = True,
    requeued: bool = False,
    human_touched: bool = False,
    escaped_defect: bool = False,
    completed: bool = True,
    cycle_time_minutes: float | None = 30.0,
    lane: str = "autonomous",
) -> dict:
    """Minimal task dict compatible with compute_weekly_report()."""
    return {
        "dispatch_acked": dispatch_acked,
        "evaluator_passed": evaluator_passed,
        "requeued": requeued,
        "human_touched": human_touched,
        "escaped_defect": escaped_defect,
        "completed": completed,
        "cycle_time_minutes": cycle_time_minutes,
        "lane": lane,
    }


def _mock_event_bus() -> MagicMock:
    """Return a MagicMock EventBus with an async publish method."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


def _make_reporter(
    tasks: list[dict] | None = None,
    task_source_side_effect: Exception | None = None,
    node_id: str = "forge:test",
    reports_dir: Path | None = None,
    markdown_dir: Path | None = None,
) -> tuple[PerformanceReporter, MagicMock]:
    """Create a reporter with a mock event bus and controlled task source.

    Returns:
        (reporter, mock_bus)
    """
    bus = _mock_event_bus()

    if task_source_side_effect is not None:

        async def _failing_source() -> list[dict]:
            raise task_source_side_effect

        source = _failing_source
    else:
        _task_list = tasks if tasks is not None else []

        async def _source() -> list[dict]:
            return _task_list

        source = _source

    reporter = PerformanceReporter(
        task_source=source,
        event_bus=bus,
        node_id=node_id,
        reports_dir=reports_dir,
        markdown_dir=markdown_dir,
    )
    return reporter, bus


# ---------------------------------------------------------------------------
# compute_weekly_report — nominal paths
# ---------------------------------------------------------------------------


class TestComputeWeeklyReport:
    """Tests for the compute_weekly_report function."""

    def test_returns_performance_report(self):
        tasks = [_make_task() for _ in range(3)]
        result = compute_weekly_report(tasks, T0, T1, node_id="forge:test")

        assert isinstance(result, PerformanceReport)
        assert result.node_id == "forge:test"

    def test_period_fields_populated(self):
        tasks = [_make_task()]
        result = compute_weekly_report(tasks, T0, T1)

        assert result.period_start == T0
        assert result.period_end == T1

    def test_total_tasks_counted(self):
        tasks = [_make_task() for _ in range(5)]
        result = compute_weekly_report(tasks, T0, T1)

        assert result.total_tasks == 5

    def test_evaluator_pass_rate_computed(self):
        tasks = [
            _make_task(evaluator_passed=True),
            _make_task(evaluator_passed=True),
            _make_task(evaluator_passed=False),
        ]
        result = compute_weekly_report(tasks, T0, T1)

        assert result.evaluator_pass_rate == pytest.approx(2 / 3)

    def test_dispatch_ack_rate_computed(self):
        tasks = [
            _make_task(dispatch_acked=True),
            _make_task(dispatch_acked=False),
        ]
        result = compute_weekly_report(tasks, T0, T1)

        assert result.dispatch_ack_rate == pytest.approx(0.5)

    def test_human_touch_ratio_computed(self):
        tasks = [
            _make_task(human_touched=False),
            _make_task(human_touched=False),
            _make_task(human_touched=True),
            _make_task(human_touched=True),
        ]
        result = compute_weekly_report(tasks, T0, T1)

        assert result.human_touch_ratio == pytest.approx(0.5)

    def test_requeue_rate_computed(self):
        tasks = [
            _make_task(requeued=False),
            _make_task(requeued=True),
            _make_task(requeued=True),
            _make_task(requeued=True),
        ]
        result = compute_weekly_report(tasks, T0, T1)

        assert result.requeue_rate == pytest.approx(0.75)

    def test_escaped_defects_counted(self):
        tasks = [
            _make_task(escaped_defect=False),
            _make_task(escaped_defect=True),
            _make_task(escaped_defect=True),
            _make_task(escaped_defect=True),
        ]
        result = compute_weekly_report(tasks, T0, T1)

        assert result.escaped_defects == 3

    def test_mean_cycle_time_computed(self):
        tasks = [
            _make_task(completed=True, cycle_time_minutes=10.0),
            _make_task(completed=True, cycle_time_minutes=20.0),
            _make_task(completed=False, cycle_time_minutes=999.0),  # not counted
        ]
        result = compute_weekly_report(tasks, T0, T1)

        assert result.mean_cycle_time_minutes == pytest.approx(15.0)

    def test_autonomous_completions_counted(self):
        tasks = [
            _make_task(completed=True, human_touched=False),
            _make_task(completed=True, human_touched=False),
            _make_task(completed=True, human_touched=True),  # not autonomous
            _make_task(completed=False, human_touched=False),  # not completed
        ]
        result = compute_weekly_report(tasks, T0, T1)

        assert result.autonomous_completions == 2

    def test_automation_ratio_property(self):
        tasks = [
            _make_task(completed=True, human_touched=False),
            _make_task(completed=True, human_touched=True),
        ]
        result = compute_weekly_report(tasks, T0, T1)

        assert result.automation_ratio == pytest.approx(0.5)

    def test_empty_tasks_returns_zero_report(self):
        result = compute_weekly_report([], T0, T1)

        assert result.total_tasks == 0
        assert result.evaluator_pass_rate == 0.0
        assert result.escaped_defects == 0

    def test_period_days_property(self):
        tasks = [_make_task()]
        result = compute_weekly_report(tasks, T0, T1)

        assert result.period_days == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# Lane metrics
# ---------------------------------------------------------------------------


class TestLaneMetrics:
    """Tests for lane-level metrics computation."""

    def test_lane_metrics_in_report(self):
        tasks = [
            _make_task(lane="autonomous", completed=True, evaluator_passed=True),
            _make_task(lane="human-review", completed=True, human_touched=True),
        ]
        result = compute_weekly_report(tasks, T0, T1)

        assert "autonomous" in result.lane_metrics
        assert "human-review" in result.lane_metrics

    def test_autonomous_lane_metrics(self):
        tasks = [
            _make_task(lane="autonomous", completed=True, evaluator_passed=True),
            _make_task(lane="autonomous", completed=True, evaluator_passed=False),
        ]
        result = compute_weekly_report(tasks, T0, T1)

        auto_metrics = result.lane_metrics["autonomous"]
        assert auto_metrics.total_tasks == 2
        assert auto_metrics.evaluator_pass_rate == pytest.approx(0.5)

    def test_compute_lane_metrics_empty(self):
        metrics = compute_lane_metrics([], "autonomous")

        assert metrics.total_tasks == 0
        assert metrics.evaluator_pass_rate == 0.0

    def test_compute_lane_metrics_with_tasks(self):
        tasks = [
            _make_task(completed=True, evaluator_passed=True, cycle_time_minutes=10.0),
            _make_task(completed=True, evaluator_passed=False, cycle_time_minutes=20.0),
            _make_task(completed=False, requeued=True),
        ]
        metrics = compute_lane_metrics(tasks, "test-lane")

        assert metrics.total_tasks == 3
        assert metrics.completed_tasks == 2
        assert metrics.autonomous_completions == 2
        assert metrics.evaluator_pass_rate == pytest.approx(1 / 3)
        assert metrics.requeue_rate == pytest.approx(1 / 3)
        assert metrics.mean_cycle_time_minutes == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# Health score
# ---------------------------------------------------------------------------


class TestHealthScore:
    """Tests for the health score calculation."""

    def test_perfect_health_score(self):
        report = PerformanceReport(
            period_start=T0,
            period_end=T1,
            evaluator_pass_rate=1.0,
            human_touch_ratio=0.0,
            requeue_rate=0.0,
            dispatch_ack_rate=1.0,
            total_tasks=10,
            autonomous_completions=10,
            escaped_defects=0,
        )

        score = report.get_health_score()
        assert score == pytest.approx(100.0)

    def test_defect_penalty_applied(self):
        report = PerformanceReport(
            period_start=T0,
            period_end=T1,
            evaluator_pass_rate=1.0,
            human_touch_ratio=0.0,
            requeue_rate=0.0,
            dispatch_ack_rate=1.0,
            total_tasks=10,
            autonomous_completions=10,
            escaped_defects=2,  # 2 * 5 = 10 penalty
        )

        score = report.get_health_score()
        assert score == pytest.approx(90.0)

    def test_defect_penalty_capped(self):
        report = PerformanceReport(
            period_start=T0,
            period_end=T1,
            evaluator_pass_rate=1.0,
            human_touch_ratio=0.0,
            requeue_rate=0.0,
            dispatch_ack_rate=1.0,
            total_tasks=10,
            autonomous_completions=10,
            escaped_defects=10,  # Would be 50 penalty, capped at 20
        )

        score = report.get_health_score()
        assert score == pytest.approx(80.0)

    def test_zero_health_with_failures(self):
        report = PerformanceReport(
            period_start=T0,
            period_end=T1,
            evaluator_pass_rate=0.0,
            human_touch_ratio=1.0,
            requeue_rate=1.0,
            dispatch_ack_rate=0.0,
            total_tasks=10,
            autonomous_completions=0,
            escaped_defects=0,
        )

        score = report.get_health_score()
        assert score == pytest.approx(0.0)

    def test_mixed_metrics_health_score(self):
        report = PerformanceReport(
            period_start=T0,
            period_end=T1,
            evaluator_pass_rate=0.8,  # 24 points
            human_touch_ratio=0.2,  # 20 points
            requeue_rate=0.1,  # 18 points
            dispatch_ack_rate=0.9,  # 13.5 points
            total_tasks=10,
            autonomous_completions=5,  # 5 points
            escaped_defects=0,
        )

        score = report.get_health_score()
        # 24 + 20 + 18 + 13.5 + 5 = 80.5
        assert score == pytest.approx(80.5)


# ---------------------------------------------------------------------------
# generate_weekly_report
# ---------------------------------------------------------------------------


class TestGenerateWeeklyReport:
    """Tests for the generate_weekly_report method."""

    @pytest.mark.asyncio
    async def test_returns_performance_report(self, tmp_path: Path):
        tasks = [_make_task() for _ in range(3)]
        reporter, _ = _make_reporter(tasks=tasks, reports_dir=tmp_path / "reports")

        result = await reporter.generate_weekly_report()

        assert isinstance(result, PerformanceReport)

    @pytest.mark.asyncio
    async def test_uses_provided_period_end(self, tmp_path: Path):
        tasks = [_make_task()]
        reporter, _ = _make_reporter(tasks=tasks, reports_dir=tmp_path / "reports")

        custom_end = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        result = await reporter.generate_weekly_report(period_end=custom_end)

        assert result.period_end == custom_end
        assert result.period_end - result.period_start == _WEEK_DURATION

    @pytest.mark.asyncio
    async def test_event_bus_publish_called(self, tmp_path: Path):
        tasks = [_make_task()]
        reporter, bus = _make_reporter(tasks=tasks, reports_dir=tmp_path / "reports")

        await reporter.generate_weekly_report()

        bus.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_event_bus_correct_event_type(self, tmp_path: Path):
        tasks = [_make_task()]
        reporter, bus = _make_reporter(tasks=tasks, reports_dir=tmp_path / "reports")

        await reporter.generate_weekly_report()

        call_kwargs = bus.publish.await_args.kwargs
        assert call_kwargs["event_type"] == _WEEKLY_REPORT_EVENT_TYPE

    @pytest.mark.asyncio
    async def test_event_bus_source_identifier(self, tmp_path: Path):
        tasks = [_make_task()]
        reporter, bus = _make_reporter(tasks=tasks, reports_dir=tmp_path / "reports")

        await reporter.generate_weekly_report()

        call_kwargs = bus.publish.await_args.kwargs
        assert call_kwargs["source"] == "performance-reporter"

    @pytest.mark.asyncio
    async def test_stores_report_to_disk(self, tmp_path: Path):
        tasks = [_make_task()]
        reports_dir = tmp_path / "reports"
        reporter, _ = _make_reporter(tasks=tasks, reports_dir=reports_dir)

        await reporter.generate_weekly_report()

        # Should have created a report file
        report_files = list(reports_dir.glob("report-*.json"))
        assert len(report_files) == 1

    @pytest.mark.asyncio
    async def test_task_source_failure_propagates(self, tmp_path: Path):
        reporter, bus = _make_reporter(
            task_source_side_effect=RuntimeError("DB connection lost"),
            reports_dir=tmp_path / "reports",
        )

        with pytest.raises(RuntimeError, match="DB connection lost"):
            await reporter.generate_weekly_report()

        bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_tasks_returns_zero_report(self, tmp_path: Path):
        reporter, _ = _make_reporter(tasks=[], reports_dir=tmp_path / "reports")

        result = await reporter.generate_weekly_report()

        assert result.total_tasks == 0


# ---------------------------------------------------------------------------
# Report storage and loading
# ---------------------------------------------------------------------------


class TestReportStorage:
    """Tests for report storage, loading, and listing."""

    @pytest.mark.asyncio
    async def test_stored_report_is_loadable(self, tmp_path: Path):
        tasks = [_make_task(completed=True, evaluator_passed=True)]
        reports_dir = tmp_path / "reports"
        reporter, _ = _make_reporter(tasks=tasks, reports_dir=reports_dir)

        await reporter.generate_weekly_report()
        reports = reporter.list_reports()

        loaded = reporter.load_report(reports[0])

        assert loaded is not None
        assert loaded.total_tasks == 1

    @pytest.mark.asyncio
    async def test_list_reports_sorted_newest_first(self, tmp_path: Path):
        reports_dir = tmp_path / "reports"
        reporter, _ = _make_reporter(tasks=[], reports_dir=reports_dir)

        # Generate reports on different days
        await reporter.generate_weekly_report(period_end=datetime(2026, 2, 23, 0, 0, 0, tzinfo=UTC))
        await reporter.generate_weekly_report(period_end=datetime(2026, 3, 2, 0, 0, 0, tzinfo=UTC))

        reports = reporter.list_reports()
        assert len(reports) == 2
        # Newest first
        assert "2026-03-02" in reports[0].name
        assert "2026-02-23" in reports[1].name

    @pytest.mark.asyncio
    async def test_get_latest_report(self, tmp_path: Path):
        reports_dir = tmp_path / "reports"
        reporter, _ = _make_reporter(tasks=[], reports_dir=reports_dir, node_id="forge:latest")

        await reporter.generate_weekly_report(period_end=datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC))

        latest = reporter.get_latest_report()
        assert latest is not None
        assert latest.node_id == "forge:latest"

    def test_get_latest_report_none_when_empty(self, tmp_path: Path):
        reporter, _ = _make_reporter(tasks=[], reports_dir=tmp_path / "reports")

        latest = reporter.get_latest_report()
        assert latest is None

    def test_load_report_handles_missing_file(self, tmp_path: Path):
        reporter, _ = _make_reporter(tasks=[], reports_dir=tmp_path / "reports")

        loaded = reporter.load_report(tmp_path / "nonexistent.json")
        assert loaded is None

    def test_load_report_handles_invalid_json(self, tmp_path: Path):
        reporter, _ = _make_reporter(tasks=[], reports_dir=tmp_path / "reports")
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json")

        loaded = reporter.load_report(bad_file)
        assert loaded is None


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


class TestScheduling:
    """Tests for schedule_weekly."""

    @pytest.mark.asyncio
    async def test_schedule_weekly_emits_immediately(self, tmp_path: Path):
        reporter, bus = _make_reporter(
            tasks=[_make_task()],
            reports_dir=tmp_path / "reports",
        )

        task = asyncio.create_task(
            reporter.schedule_weekly(interval_seconds=9999, run_immediately=True)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        bus.publish.assert_awaited()

    @pytest.mark.asyncio
    async def test_schedule_weekly_skip_immediate(self, tmp_path: Path):
        reporter, bus = _make_reporter(
            tasks=[_make_task()],
            reports_dir=tmp_path / "reports",
        )

        task = asyncio.create_task(
            reporter.schedule_weekly(interval_seconds=9999, run_immediately=False)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_safe_generate_swallows_errors(self, tmp_path: Path):
        reporter, _ = _make_reporter(
            task_source_side_effect=OSError("disk full"),
            reports_dir=tmp_path / "reports",
        )

        # Should complete without raising
        await reporter._safe_generate()


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


class TestSingletonFactory:
    def setup_method(self):
        """Ensure a clean singleton before every test."""
        reset_performance_reporter()

    def teardown_method(self):
        """Leave a clean singleton after every test."""
        reset_performance_reporter()

    def test_first_call_without_task_source_raises(self):
        with pytest.raises(ValueError, match="task_source is required"):
            get_performance_reporter()

    def test_first_call_with_task_source_returns_reporter(self):
        async def source() -> list[dict]:
            return []

        reporter = get_performance_reporter(task_source=source)
        assert isinstance(reporter, PerformanceReporter)

    def test_second_call_returns_same_instance(self):
        async def source() -> list[dict]:
            return []

        first = get_performance_reporter(task_source=source)
        second = get_performance_reporter()
        assert first is second

    def test_reset_allows_new_singleton(self):
        async def source_a() -> list[dict]:
            return []

        async def source_b() -> list[dict]:
            return [_make_task()]

        first = get_performance_reporter(task_source=source_a)
        reset_performance_reporter()
        second = get_performance_reporter(task_source=source_b)

        assert first is not second

    def test_custom_node_id_applied(self):
        async def source() -> list[dict]:
            return []

        reporter = get_performance_reporter(task_source=source, node_id="forge:custom")
        assert reporter._node_id == "forge:custom"


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


class TestModelValidation:
    """Tests for Pydantic model validation."""

    def test_period_start_before_period_end_required(self):
        with pytest.raises(ValueError, match="period_start.*must be before period_end"):
            PerformanceReport(
                period_start=T1,  # After T0
                period_end=T0,
            )

    def test_period_start_equal_period_end_invalid(self):
        with pytest.raises(ValueError):
            PerformanceReport(
                period_start=T0,
                period_end=T0,
            )

    def test_datetime_strings_parsed(self):
        data = {
            "period_start": "2026-02-16T00:00:00Z",
            "period_end": "2026-02-23T00:00:00Z",
            "total_tasks": 5,
        }
        report = PerformanceReport.model_validate(data)

        assert report.period_start.year == 2026
        assert report.period_end.tzinfo is not None

    def test_lane_metrics_validation(self):
        metrics = LaneMetrics(
            total_tasks=10,
            completed_tasks=8,
            autonomous_completions=6,
            evaluator_pass_rate=0.75,
        )

        assert metrics.total_tasks == 10
        assert metrics.evaluator_pass_rate == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# PerformanceReportEvent
# ---------------------------------------------------------------------------


class TestPerformanceReportEvent:
    """Tests for the SSE event envelope."""

    def test_event_type_default(self):
        report = PerformanceReport(period_start=T0, period_end=T1)
        event = PerformanceReportEvent(report=report)

        assert event.event_type == "performance_report_weekly"

    def test_generated_at_auto_populated(self):
        report = PerformanceReport(period_start=T0, period_end=T1)
        event = PerformanceReportEvent(report=report)

        assert event.generated_at is not None
        assert event.generated_at.tzinfo is not None

    def test_model_dump_json_serializable(self):
        report = PerformanceReport(
            period_start=T0,
            period_end=T1,
            total_tasks=5,
            evaluator_pass_rate=0.8,
        )
        event = PerformanceReportEvent(report=report)

        json_str = event.model_dump_json()
        data = json.loads(json_str)

        assert data["event_type"] == "performance_report_weekly"
        assert data["report"]["total_tasks"] == 5


# ---------------------------------------------------------------------------
# Lane classification
# ---------------------------------------------------------------------------


class TestLaneClassification:
    """Tests for classify_lane() and the LOW/HIGH_RISK_LANES sets."""

    def test_docs_lane_is_low_risk(self):
        assert classify_lane("docs") == "low_risk"

    def test_test_writing_lane_is_low_risk(self):
        assert classify_lane("test-writing") == "low_risk"

    def test_docs_update_is_low_risk(self):
        assert classify_lane("docs-update") == "low_risk"

    def test_api_simple_is_low_risk(self):
        assert classify_lane("api_simple") == "low_risk"

    def test_content_generation_is_low_risk(self):
        assert classify_lane("content-generation") == "low_risk"

    def test_auth_lane_is_high_risk(self):
        assert classify_lane("auth") == "high_risk"

    def test_security_change_is_high_risk(self):
        assert classify_lane("security-change") == "high_risk"

    def test_deployment_is_high_risk(self):
        assert classify_lane("deployment") == "high_risk"

    def test_billing_is_high_risk(self):
        assert classify_lane("billing") == "high_risk"

    def test_database_migration_is_high_risk(self):
        assert classify_lane("database-migration") == "high_risk"

    def test_unknown_lane_is_other(self):
        assert classify_lane("autonomous") == "other"

    def test_unknown_lane_custom_is_other(self):
        assert classify_lane("blocked") == "other"

    def test_classify_lane_case_insensitive(self):
        assert classify_lane("AUTH") == "high_risk"
        assert classify_lane("DOCS") == "low_risk"

    def test_low_risk_lanes_is_frozenset(self):
        assert isinstance(LOW_RISK_LANES, frozenset)

    def test_high_risk_lanes_is_frozenset(self):
        assert isinstance(HIGH_RISK_LANES, frozenset)

    def test_low_high_risk_lanes_disjoint(self):
        assert LOW_RISK_LANES.isdisjoint(HIGH_RISK_LANES)


# ---------------------------------------------------------------------------
# Markdown report storage
# ---------------------------------------------------------------------------


class TestMarkdownReportStorage:
    """Tests for _store_report_markdown() and the YYYY-WW.md output."""

    @pytest.mark.asyncio
    async def test_markdown_file_created(self, tmp_path: Path):
        tasks = [_make_task(lane="docs", evaluator_passed=True)]
        md_dir = tmp_path / "weekly"
        reporter, _ = _make_reporter(
            tasks=tasks,
            reports_dir=tmp_path / "reports",
            markdown_dir=md_dir,
        )

        await reporter.generate_weekly_report(period_end=datetime(2026, 2, 23, 0, 0, 0, tzinfo=UTC))

        md_files = list(md_dir.glob("*.md"))
        assert len(md_files) == 1

    @pytest.mark.asyncio
    async def test_markdown_filename_uses_iso_week(self, tmp_path: Path):
        # 2026-02-23 is in ISO week 9 of 2026
        period_end = datetime(2026, 2, 23, 0, 0, 0, tzinfo=UTC)
        reporter, _ = _make_reporter(
            tasks=[_make_task()],
            reports_dir=tmp_path / "reports",
            markdown_dir=tmp_path / "weekly",
        )

        await reporter.generate_weekly_report(period_end=period_end)

        iso_year, iso_week, _ = period_end.isocalendar()
        expected_name = f"{iso_year}-{iso_week:02d}.md"
        assert (tmp_path / "weekly" / expected_name).exists()

    @pytest.mark.asyncio
    async def test_markdown_contains_health_score(self, tmp_path: Path):
        reporter, _ = _make_reporter(
            tasks=[_make_task(evaluator_passed=True, human_touched=False)],
            reports_dir=tmp_path / "reports",
            markdown_dir=tmp_path / "weekly",
        )

        await reporter.generate_weekly_report(period_end=datetime(2026, 2, 23, 0, 0, 0, tzinfo=UTC))

        md_file = next((tmp_path / "weekly").glob("*.md"))
        content = md_file.read_text()
        assert "Health Score" in content

    @pytest.mark.asyncio
    async def test_markdown_contains_overall_metrics_table(self, tmp_path: Path):
        reporter, _ = _make_reporter(
            tasks=[_make_task()],
            reports_dir=tmp_path / "reports",
            markdown_dir=tmp_path / "weekly",
        )

        await reporter.generate_weekly_report(period_end=datetime(2026, 2, 23, 0, 0, 0, tzinfo=UTC))

        md_file = next((tmp_path / "weekly").glob("*.md"))
        content = md_file.read_text()
        assert "Overall Metrics" in content
        assert "Evaluator Pass Rate" in content

    @pytest.mark.asyncio
    async def test_markdown_low_risk_section_for_docs_lane(self, tmp_path: Path):
        tasks = [_make_task(lane="docs"), _make_task(lane="auth")]
        reporter, _ = _make_reporter(
            tasks=tasks,
            reports_dir=tmp_path / "reports",
            markdown_dir=tmp_path / "weekly",
        )

        await reporter.generate_weekly_report(period_end=datetime(2026, 2, 23, 0, 0, 0, tzinfo=UTC))

        md_file = next((tmp_path / "weekly").glob("*.md"))
        content = md_file.read_text()
        assert "Low-Risk Lanes" in content
        assert "High-Risk Lanes" in content

    @pytest.mark.asyncio
    async def test_markdown_empty_week_writes_file(self, tmp_path: Path):
        reporter, _ = _make_reporter(
            tasks=[],
            reports_dir=tmp_path / "reports",
            markdown_dir=tmp_path / "weekly",
        )

        await reporter.generate_weekly_report(period_end=datetime(2026, 2, 23, 0, 0, 0, tzinfo=UTC))

        md_files = list((tmp_path / "weekly").glob("*.md"))
        assert len(md_files) == 1

    @pytest.mark.asyncio
    async def test_markdown_missing_lane_section_omitted(self, tmp_path: Path):
        # Only docs tasks — no high-risk lanes
        tasks = [_make_task(lane="docs")]
        reporter, _ = _make_reporter(
            tasks=tasks,
            reports_dir=tmp_path / "reports",
            markdown_dir=tmp_path / "weekly",
        )

        await reporter.generate_weekly_report(period_end=datetime(2026, 2, 23, 0, 0, 0, tzinfo=UTC))

        md_file = next((tmp_path / "weekly").glob("*.md"))
        content = md_file.read_text()
        assert "Low-Risk Lanes" in content
        assert "High-Risk Lanes" not in content


# ---------------------------------------------------------------------------
# Sunday midnight scheduler
# ---------------------------------------------------------------------------


class TestNextSundayMidnight:
    """Tests for next_sunday_midnight_utc() helper."""

    def test_returns_utc_datetime(self):
        result = next_sunday_midnight_utc()
        assert result.tzinfo is not None

    def test_result_is_sunday(self):
        result = next_sunday_midnight_utc()
        # ISO weekday: Sunday=7
        assert result.isoweekday() == 7

    def test_result_is_midnight(self):
        result = next_sunday_midnight_utc()
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0

    def test_monday_returns_next_sunday(self):
        # 2026-02-23 is a Monday
        monday = datetime(2026, 2, 23, 12, 0, 0, tzinfo=UTC)
        result = next_sunday_midnight_utc(monday)

        assert result == datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)

    def test_sunday_midnight_returns_following_sunday(self):
        # Already Sunday midnight — must advance to following Sunday
        sunday_midnight = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        result = next_sunday_midnight_utc(sunday_midnight)

        assert result == datetime(2026, 3, 8, 0, 0, 0, tzinfo=UTC)

    def test_sunday_noon_returns_following_sunday(self):
        # Sunday noon — next Sunday midnight is the following week
        sunday_noon = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        result = next_sunday_midnight_utc(sunday_noon)

        # Sunday noon ISO weekday is 7 → days_until = 0 → advances to next week
        assert result == datetime(2026, 3, 8, 0, 0, 0, tzinfo=UTC)

    def test_saturday_returns_tomorrow_sunday(self):
        saturday = datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC)
        result = next_sunday_midnight_utc(saturday)

        assert result == datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)

    def test_result_is_strictly_in_future(self):
        reference = datetime.now(UTC)
        result = next_sunday_midnight_utc(reference)
        assert result > reference


class TestScheduleWeeklyAligned:
    """Tests for schedule_weekly_aligned()."""

    @pytest.mark.asyncio
    async def test_schedule_aligned_runs_after_sleep(self, tmp_path: Path, mocker):
        """Verify schedule_weekly_aligned sleeps then generates a report."""
        reporter, bus = _make_reporter(
            tasks=[_make_task()],
            reports_dir=tmp_path / "reports",
            markdown_dir=tmp_path / "weekly",
        )

        # Patch asyncio.sleep to avoid actually waiting
        sleep_mock = mocker.patch(
            "forge_harness.webhook_server.services.performance_reporter.asyncio.sleep",
            new_callable=AsyncMock,
        )

        task = asyncio.create_task(reporter.schedule_weekly_aligned())
        # Let it run one iteration (sleep then generate)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        sleep_mock.assert_awaited()

    @pytest.mark.asyncio
    async def test_scheduler_trigger_function_raises_without_singleton(self):
        """run_weekly_performance_report_trigger raises if no singleton set."""
        from forge_harness.webhook_server.services.scheduler_policy import (
            run_weekly_performance_report_trigger,
        )

        reset_performance_reporter()

        with pytest.raises(ValueError, match="task_source is required"):
            await run_weekly_performance_report_trigger()

    @pytest.mark.asyncio
    async def test_scheduler_trigger_delegates_to_reporter(self, tmp_path: Path, mocker):
        """run_weekly_performance_report_trigger calls schedule_weekly_aligned."""
        from forge_harness.webhook_server.services.scheduler_policy import (
            run_weekly_performance_report_trigger,
        )

        reset_performance_reporter()

        async def _source() -> list[dict]:
            return []

        reporter = get_performance_reporter(
            task_source=_source,
            reports_dir=tmp_path / "reports",
            markdown_dir=tmp_path / "weekly",
        )

        aligned_mock = mocker.patch.object(
            reporter,
            "schedule_weekly_aligned",
            new_callable=AsyncMock,
        )

        await run_weekly_performance_report_trigger()
        aligned_mock.assert_awaited_once()

        reset_performance_reporter()
