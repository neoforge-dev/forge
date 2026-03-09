"""Performance Reporter Service
==============================

Generates weekly Dark Factory autonomous performance reports that aggregate
task metrics across quality, cost, and lead-time dimensions.

DF-5003: Weekly autonomous performance review (quality/cost/lead-time).

The report captures:
- Quality: evaluator_pass_rate, escaped_defects
- Cost: dispatch_ack_rate, human_touch_ratio
- Lead-time: mean_cycle_time_minutes, requeue_rate

Reports are stored in `.forge/reports/weekly/` and emitted as SSE events.

Usage
-----
Inject the singleton into startup routines::

    from forge_harness.webhook_server.services.performance_reporter import (
        get_performance_reporter,
    )

    reporter = get_performance_reporter()
    report = await reporter.generate_weekly_report()

Schedule weekly generation::

    await reporter.schedule_weekly()  # Runs every 7 days
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.services.event_bus import EventBus, get_event_bus

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

# A task source is any async callable that returns a list of task dicts
TaskSource = Callable[[], Coroutine[Any, Any, list[dict[str, Any]]]]

# Report storage directory (JSON snapshots)
_DEFAULT_REPORTS_DIR = Path(".forge/reports/weekly")

# Markdown report storage directory (spec: .forge/learning/weekly/YYYY-WW.md)
_DEFAULT_MARKDOWN_DIR = Path(".forge/learning/weekly")

# Event type for SSE emission
_WEEKLY_REPORT_EVENT_TYPE = "performance_report_weekly"

# Week duration
_WEEK_DURATION = timedelta(days=7)

# ---------------------------------------------------------------------------
# Lane risk classification
# ---------------------------------------------------------------------------

#: Lane names treated as low-risk (docs, test, simple API changes).
#: These lanes are aggregated into the "low_risk" bucket in the summary.
LOW_RISK_LANES: frozenset[str] = frozenset(
    {
        "docs",
        "test",
        "test-writing",
        "docs-update",
        "api_simple",
        "api-simple",
        "content",
        "content-generation",
    }
)

#: Lane names treated as high-risk (auth, security, deploy, billing).
#: These lanes are aggregated into the "high_risk" bucket in the summary.
HIGH_RISK_LANES: frozenset[str] = frozenset(
    {
        "auth",
        "security",
        "security-change",
        "deploy",
        "deployment",
        "billing",
        "database",
        "database-migration",
        "payment",
    }
)


def classify_lane(lane: str) -> str:
    """Return 'low_risk', 'high_risk', or 'other' for a lane name.

    Classification follows the DF-5003 policy matrix:
    - low_risk:  docs, test, api_simple, content
    - high_risk: auth, security, deploy, billing, database

    Args:
        lane: Lane name string (e.g. "security-change", "test-writing").

    Returns:
        One of "low_risk", "high_risk", or "other".
    """
    lane_lower = lane.lower()
    if lane_lower in LOW_RISK_LANES:
        return "low_risk"
    if lane_lower in HIGH_RISK_LANES:
        return "high_risk"
    return "other"


def next_sunday_midnight_utc(reference: datetime | None = None) -> datetime:
    """Return the next Sunday at 00:00:00 UTC after *reference*.

    Used by the scheduler to align weekly report generation with Sunday
    midnight UTC (the canonical Dark Factory reporting boundary).

    Args:
        reference: Reference datetime. Defaults to ``datetime.now(UTC)``.

    Returns:
        A timezone-aware UTC datetime at 00:00:00 on the next Sunday.
        If *reference* is already Sunday midnight, the **following** Sunday
        is returned (i.e., always strictly in the future).
    """
    if reference is None:
        reference = datetime.now(UTC)

    # ISO weekday: Monday=1 … Sunday=7
    days_until_sunday = (7 - reference.isoweekday()) % 7
    if days_until_sunday == 0:
        # Already Sunday — advance to next week to stay strictly future
        days_until_sunday = 7

    next_sunday = reference + timedelta(days=days_until_sunday)
    return next_sunday.replace(hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class LaneMetrics(BaseModel):
    """Aggregated metrics for a single lane.

    Attributes:
        total_tasks: Number of tasks processed in this lane.
        completed_tasks: Tasks that reached terminal state.
        autonomous_completions: Tasks completed without human touch.
        evaluator_pass_rate: Fraction of tasks that passed evaluator.
        mean_cycle_time_minutes: Average completion time.
        requeue_rate: Fraction of tasks requeued at least once.
    """

    total_tasks: int = Field(default=0, ge=0, description="Total tasks in lane.")
    completed_tasks: int = Field(default=0, ge=0, description="Completed tasks in lane.")
    autonomous_completions: int = Field(default=0, ge=0, description="Autonomous completions.")
    evaluator_pass_rate: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Evaluator pass rate."
    )
    mean_cycle_time_minutes: float = Field(default=0.0, ge=0.0, description="Mean cycle time.")
    requeue_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="Requeue rate.")


class PerformanceReport(BaseModel):
    """Weekly Dark Factory performance report.

    Captures quality, cost, and lead-time dimensions with lane-level breakdown
    and trend indicators.

    Attributes:
        period_start: Inclusive start of the reporting period (UTC).
        period_end: Exclusive end of the reporting period (UTC).
        node_id: Node identifier (e.g., "forge:nova").
        generated_at: When this report was generated.

        Quality Metrics:
            evaluator_pass_rate: Overall evaluator pass rate.
            escaped_defects: Defects that passed evaluator but failed in prod.

        Cost Metrics:
            dispatch_ack_rate: Fraction of dispatches acknowledged.
            human_touch_ratio: Fraction of tasks requiring human intervention.

        Lead-time Metrics:
            mean_cycle_time_minutes: Average task completion time.
            requeue_rate: Overall requeue rate.

        Aggregate Counts:
            total_tasks: Total tasks in the period.
            autonomous_completions: Tasks completed autonomously.
            automation_ratio: Fraction of tasks completed autonomously.

        Lane Breakdown:
            lane_metrics: Per-lane aggregated metrics.
    """

    # Period identification
    period_start: datetime = Field(description="Report period start (UTC).")
    period_end: datetime = Field(description="Report period end (UTC).")
    node_id: str = Field(default="unknown", description="Node identifier.")
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When report was generated.",
    )

    # Quality metrics
    evaluator_pass_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Overall evaluator pass rate.",
    )
    escaped_defects: int = Field(
        default=0,
        ge=0,
        description="Defects that escaped to production.",
    )

    # Cost metrics
    dispatch_ack_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of dispatches acknowledged.",
    )
    human_touch_ratio: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of tasks requiring human intervention.",
    )

    # Lead-time metrics
    mean_cycle_time_minutes: float = Field(
        default=0.0,
        ge=0.0,
        description="Mean cycle time in minutes.",
    )
    requeue_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Overall requeue rate.",
    )

    # Aggregate counts
    total_tasks: int = Field(default=0, ge=0, description="Total tasks processed.")
    autonomous_completions: int = Field(default=0, ge=0, description="Autonomous completions.")

    # Lane breakdown
    lane_metrics: dict[str, LaneMetrics] = Field(
        default_factory=dict,
        description="Per-lane metrics.",
    )

    # Validators
    @field_validator("period_start", "period_end", "generated_at", mode="before")
    @classmethod
    def ensure_utc(cls, v: Any) -> datetime:
        """Parse string datetimes and ensure UTC timezone."""
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime) and v.tzinfo is None:
            v = v.replace(tzinfo=UTC)
        return v

    @model_validator(mode="after")
    def validate_period_order(self) -> PerformanceReport:
        """Ensure period_start is before period_end."""
        if self.period_start >= self.period_end:
            raise ValueError(
                f"period_start ({self.period_start.isoformat()}) must be "
                f"before period_end ({self.period_end.isoformat()})"
            )
        return self

    @property
    def automation_ratio(self) -> float:
        """Fraction of total tasks completed autonomously."""
        if self.total_tasks == 0:
            return 0.0
        return self.autonomous_completions / self.total_tasks

    @property
    def period_days(self) -> float:
        """Duration of the reporting period in days."""
        return (self.period_end - self.period_start).total_seconds() / 86400.0

    def get_health_score(self) -> float:
        """Compute an overall health score (0-100).

        Weights:
        - Evaluator pass rate: 30%
        - Low human touch ratio: 25% (inverted)
        - Low requeue rate: 20% (inverted)
        - Dispatch ack rate: 15%
        - Automation ratio: 10%
        """
        score = (
            self.evaluator_pass_rate * 30.0
            + (1.0 - self.human_touch_ratio) * 25.0
            + (1.0 - self.requeue_rate) * 20.0
            + self.dispatch_ack_rate * 15.0
            + self.automation_ratio * 10.0
        )
        # Penalty for escaped defects
        defect_penalty = min(self.escaped_defects * 5.0, 20.0)
        return max(0.0, score - defect_penalty)


class PerformanceReportEvent(BaseModel):
    """SSE event envelope for weekly performance reports."""

    event_type: str = Field(default="performance_report_weekly")
    report: PerformanceReport
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When event was generated.",
    )

    @field_validator("generated_at", mode="before")
    @classmethod
    def ensure_utc(cls, v: Any) -> datetime:
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime) and v.tzinfo is None:
            v = v.replace(tzinfo=UTC)
        return v


# ---------------------------------------------------------------------------
# Computation helpers
# ---------------------------------------------------------------------------


def compute_lane_metrics(
    tasks: list[dict[str, Any]],
    lane: str,
) -> LaneMetrics:
    """Compute aggregated metrics for a specific lane.

    Args:
        tasks: List of task dicts filtered to the specific lane.
        lane: Lane identifier.

    Returns:
        LaneMetrics with aggregated statistics.
    """
    if not tasks:
        return LaneMetrics(total_tasks=0)

    total = len(tasks)
    completed = sum(1 for t in tasks if t.get("completed", False))
    autonomous = sum(
        1 for t in tasks if t.get("completed", False) and not t.get("human_touched", False)
    )
    evaluator_passed = sum(
        1 for t in tasks if t.get("completed", False) and t.get("evaluator_passed", False)
    )
    requeued = sum(1 for t in tasks if t.get("requeued", False))

    cycle_times = [
        float(t["cycle_time_minutes"])
        for t in tasks
        if t.get("completed", False) and t.get("cycle_time_minutes") is not None
    ]
    mean_cycle = sum(cycle_times) / len(cycle_times) if cycle_times else 0.0

    return LaneMetrics(
        total_tasks=total,
        completed_tasks=completed,
        autonomous_completions=autonomous,
        evaluator_pass_rate=evaluator_passed / total if total > 0 else 0.0,
        mean_cycle_time_minutes=mean_cycle,
        requeue_rate=requeued / total if total > 0 else 0.0,
    )


def compute_weekly_report(
    tasks: list[dict[str, Any]],
    period_start: datetime,
    period_end: datetime,
    node_id: str = "unknown",
) -> PerformanceReport:
    """Compute a PerformanceReport from a list of task dicts.

    Each task dict may include:
        - dispatch_acked: bool
        - evaluator_passed: bool
        - requeued: bool
        - human_touched: bool
        - escaped_defect: bool
        - completed: bool
        - cycle_time_minutes: float
        - lane: str ("autonomous", "human-review", "blocked")

    Args:
        tasks: List of task dicts for the period.
        period_start: Start of the reporting period.
        period_end: End of the reporting period.
        node_id: Node identifier.

    Returns:
        PerformanceReport with aggregated metrics.
    """
    total = len(tasks)

    if total == 0:
        return PerformanceReport(
            period_start=period_start,
            period_end=period_end,
            node_id=node_id,
            total_tasks=0,
        )

    # Aggregate overall metrics
    dispatch_acked = sum(1 for t in tasks if t.get("dispatch_acked", False))
    evaluator_passed = sum(1 for t in tasks if t.get("evaluator_passed", False))
    requeued = sum(1 for t in tasks if t.get("requeued", False))
    human_touched = sum(1 for t in tasks if t.get("human_touched", False))
    escaped = sum(1 for t in tasks if t.get("escaped_defect", False))
    autonomous = sum(
        1 for t in tasks if t.get("completed", False) and not t.get("human_touched", False)
    )

    cycle_times = [
        float(t["cycle_time_minutes"])
        for t in tasks
        if t.get("completed", False) and t.get("cycle_time_minutes") is not None
    ]
    mean_cycle = sum(cycle_times) / len(cycle_times) if cycle_times else 0.0

    # Compute lane-level metrics
    lane_groups: dict[str, list[dict]] = defaultdict(list)
    for task in tasks:
        lane = task.get("lane", "unknown")
        lane_groups[lane].append(task)

    lane_metrics = {
        lane: compute_lane_metrics(lane_tasks, lane) for lane, lane_tasks in lane_groups.items()
    }

    return PerformanceReport(
        period_start=period_start,
        period_end=period_end,
        node_id=node_id,
        evaluator_pass_rate=evaluator_passed / total,
        escaped_defects=escaped,
        dispatch_ack_rate=dispatch_acked / total,
        human_touch_ratio=human_touched / total,
        mean_cycle_time_minutes=mean_cycle,
        requeue_rate=requeued / total,
        total_tasks=total,
        autonomous_completions=autonomous,
        lane_metrics=lane_metrics,
    )


# ---------------------------------------------------------------------------
# Performance Reporter Service
# ---------------------------------------------------------------------------


class PerformanceReporter:
    """Generates and stores weekly Dark Factory performance reports.

    The reporter aggregates task data across quality, cost, and lead-time
    dimensions, stores reports to disk, and emits SSE events.

    Args:
        task_source: Async callable returning task dicts.
        event_bus: EventBus for SSE emission.
        reports_dir: Directory to store reports.
        node_id: Node identifier for reports.
    """

    def __init__(
        self,
        task_source: TaskSource,
        event_bus: EventBus | None = None,
        reports_dir: Path | None = None,
        markdown_dir: Path | None = None,
        node_id: str = "forge:unknown",
    ) -> None:
        self._task_source = task_source
        self._event_bus = event_bus or get_event_bus()
        self._reports_dir = reports_dir or _DEFAULT_REPORTS_DIR
        self._markdown_dir = markdown_dir or _DEFAULT_MARKDOWN_DIR
        self._node_id = node_id
        self._running_tasks: list[asyncio.Task[None]] = []
        logger.info(
            "PerformanceReporter initialized",
            extra={
                "node_id": node_id,
                "reports_dir": str(self._reports_dir),
                "markdown_dir": str(self._markdown_dir),
            },
        )

    # ------------------------------------------------------------------
    # Core report generation
    # ------------------------------------------------------------------

    async def generate_weekly_report(
        self,
        period_end: datetime | None = None,
    ) -> PerformanceReport:
        """Generate a weekly performance report.

        Args:
            period_end: End of the reporting period. Defaults to now.

        Returns:
            PerformanceReport with aggregated metrics.

        Raises:
            Exception: Propagates from task source after logging.
        """
        if period_end is None:
            period_end = datetime.now(UTC)
        period_start = period_end - _WEEK_DURATION

        logger.debug(
            "Generating weekly report",
            extra={
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "node_id": self._node_id,
            },
        )

        # Fetch tasks
        try:
            tasks = await self._task_source()
        except Exception as exc:
            logger.error(
                "Task source failed — report generation aborted",
                extra={"error": str(exc)},
            )
            raise

        task_count = len(tasks)
        logger.debug(
            "Task source returned %d tasks",
            task_count,
            extra={"task_count": task_count},
        )

        # Compute report
        report = compute_weekly_report(
            tasks=tasks,
            period_start=period_start,
            period_end=period_end,
            node_id=self._node_id,
        )

        # Store JSON snapshot to disk
        await self._store_report(report)

        # Store markdown report to .forge/learning/weekly/YYYY-WW.md
        await self._store_report_markdown(report)

        # Emit SSE event
        await self._emit_report(report)

        logger.info(
            "Weekly report generated",
            extra={
                "total_tasks": report.total_tasks,
                "automation_ratio": round(report.automation_ratio, 3),
                "health_score": round(report.get_health_score(), 1),
                "escaped_defects": report.escaped_defects,
            },
        )

        return report

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    async def schedule_weekly(
        self,
        *,
        interval_seconds: float = 604800.0,  # 7 days
        run_immediately: bool = True,
    ) -> None:
        """Run report generation on a weekly interval.

        Args:
            interval_seconds: Seconds between reports. Defaults to 604800 (7 days).
            run_immediately: Generate a report before waiting.
        """
        logger.info(
            "Starting weekly report schedule (interval=%.0fs)",
            interval_seconds,
            extra={"interval_seconds": interval_seconds},
        )

        if run_immediately:
            await self._safe_generate()

        while True:
            await asyncio.sleep(interval_seconds)
            await self._safe_generate()

    async def _safe_generate(self) -> None:
        """Generate report without propagating exceptions."""
        try:
            await self.generate_weekly_report()
        except Exception as exc:
            logger.error(
                "Report generation failed (will retry on next interval)",
                extra={"error": str(exc)},
            )

    async def schedule_weekly_aligned(self) -> None:
        """Run report generation aligned to Sunday midnight UTC.

        Computes the delay until the next Sunday 00:00:00 UTC, sleeps, then
        loops weekly.  This is the canonical Dark Factory reporting cadence
        described in DF-5003.

        The coroutine runs indefinitely.  Cancel the enclosing asyncio.Task to
        stop the loop.
        """
        while True:
            now = datetime.now(UTC)
            next_run = next_sunday_midnight_utc(now)
            delay_seconds = (next_run - now).total_seconds()
            logger.info(
                "PerformanceReporter: sleeping %.0fs until next Sunday midnight UTC (%s)",
                delay_seconds,
                next_run.isoformat(),
                extra={"next_run": next_run.isoformat(), "delay_seconds": delay_seconds},
            )
            await asyncio.sleep(delay_seconds)
            await self._safe_generate()

    # ------------------------------------------------------------------
    # Storage and emission
    # ------------------------------------------------------------------

    async def _store_report(self, report: PerformanceReport) -> Path:
        """Store report to disk as JSON.

        Args:
            report: The report to store.

        Returns:
            Path to the stored report file.
        """
        # Ensure directory exists
        self._reports_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename from period_end
        filename = f"report-{report.period_end.strftime('%Y-%m-%d')}.json"
        filepath = self._reports_dir / filename

        # Write report
        content = report.model_dump(mode="json")
        filepath.write_text(json.dumps(content, indent=2, default=str))

        logger.debug(
            "Report stored",
            extra={"path": str(filepath)},
        )

        return filepath

    async def _store_report_markdown(self, report: PerformanceReport) -> Path:
        """Store report to disk as a markdown file.

        The file is written to ``{markdown_dir}/YYYY-WW.md`` where ``YYYY``
        is the ISO year and ``WW`` is the zero-padded ISO week number derived
        from the report's ``period_end``.

        Lane metrics are grouped by risk classification (low_risk / high_risk /
        other) per the DF-5003 lane breakdown specification.

        Args:
            report: The report to serialise.

        Returns:
            Path to the written markdown file.
        """
        self._markdown_dir.mkdir(parents=True, exist_ok=True)

        iso_year, iso_week, _ = report.period_end.isocalendar()
        filename = f"{iso_year}-{iso_week:02d}.md"
        filepath = self._markdown_dir / filename

        # Group lane metrics by risk classification
        risk_groups: dict[str, list[tuple[str, LaneMetrics]]] = {
            "low_risk": [],
            "high_risk": [],
            "other": [],
        }
        for lane_name, metrics in sorted(report.lane_metrics.items()):
            bucket = classify_lane(lane_name)
            risk_groups[bucket].append((lane_name, metrics))

        health = report.get_health_score()
        lines: list[str] = [
            "# Dark Factory Weekly Performance Report",
            "",
            f"**Period**: {report.period_start.date()} — {report.period_end.date()}",
            f"**Node**: {report.node_id}",
            f"**Generated**: {report.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"**Health Score**: {health:.1f}/100",
            "",
            "## Overall Metrics",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total Tasks | {report.total_tasks} |",
            f"| Autonomous Completions | {report.autonomous_completions} |",
            f"| Automation Ratio | {report.automation_ratio:.1%} |",
            f"| Evaluator Pass Rate | {report.evaluator_pass_rate:.1%} |",
            f"| Human Touch Ratio | {report.human_touch_ratio:.1%} |",
            f"| Requeue Rate | {report.requeue_rate:.1%} |",
            f"| Dispatch Ack Rate | {report.dispatch_ack_rate:.1%} |",
            f"| Mean Cycle Time | {report.mean_cycle_time_minutes:.1f} min |",
            f"| Escaped Defects | {report.escaped_defects} |",
            "",
        ]

        for bucket_label, bucket_lanes in [
            ("Low-Risk Lanes", risk_groups["low_risk"]),
            ("High-Risk Lanes", risk_groups["high_risk"]),
            ("Other Lanes", risk_groups["other"]),
        ]:
            if not bucket_lanes:
                continue
            lines.append(f"## {bucket_label}")
            lines.append("")
            lines.append("| Lane | Tasks | Completed | Eval Pass | Requeue | Cycle (min) |")
            lines.append("|------|-------|-----------|-----------|---------|-------------|")
            for lane_name, m in bucket_lanes:
                lines.append(
                    f"| {lane_name} "
                    f"| {m.total_tasks} "
                    f"| {m.completed_tasks} "
                    f"| {m.evaluator_pass_rate:.1%} "
                    f"| {m.requeue_rate:.1%} "
                    f"| {m.mean_cycle_time_minutes:.1f} |"
                )
            lines.append("")

        filepath.write_text("\n".join(lines))

        logger.debug(
            "Markdown report stored",
            extra={"path": str(filepath), "week": f"{iso_year}-W{iso_week:02d}"},
        )

        return filepath

    async def _emit_report(self, report: PerformanceReport) -> None:
        """Emit report as an SSE event.

        Args:
            report: The report to emit.
        """
        event = PerformanceReportEvent(report=report)
        payload = event.model_dump(mode="json")

        await self._event_bus.publish(
            event_type=_WEEKLY_REPORT_EVENT_TYPE,
            data=payload,
            source="performance-reporter",
        )

        logger.debug(
            "Report emitted via EventBus",
            extra={"event_type": _WEEKLY_REPORT_EVENT_TYPE},
        )

    def load_report(self, filepath: Path) -> PerformanceReport | None:
        """Load a stored report from disk.

        Args:
            filepath: Path to the report JSON file.

        Returns:
            PerformanceReport if valid, None if file not found or invalid.
        """
        try:
            content = filepath.read_text()
            data = json.loads(content)
            return PerformanceReport.model_validate(data)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Failed to load report",
                extra={"path": str(filepath), "error": str(exc)},
            )
            return None

    def list_reports(self) -> list[Path]:
        """List all stored report files.

        Returns:
            List of report file paths, sorted by date (newest first).
        """
        if not self._reports_dir.exists():
            return []

        reports = sorted(
            self._reports_dir.glob("report-*.json"),
            key=lambda p: p.name,
            reverse=True,
        )
        return reports

    def get_latest_report(self) -> PerformanceReport | None:
        """Get the most recent stored report.

        Returns:
            PerformanceReport if available, None otherwise.
        """
        reports = self.list_reports()
        if not reports:
            return None
        return self.load_report(reports[0])


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_reporter_instance: PerformanceReporter | None = None
_reporter_lock: Lock = Lock()


def get_performance_reporter(
    task_source: TaskSource | None = None,
    event_bus: EventBus | None = None,
    reports_dir: Path | None = None,
    markdown_dir: Path | None = None,
    node_id: str = "forge:unknown",
) -> PerformanceReporter:
    """Return the global PerformanceReporter singleton.

    On the first call, *task_source* must be provided. Subsequent calls
    return the cached instance.

    Args:
        task_source: Async callable returning task dicts. Required on first call.
        event_bus: EventBus to use. Defaults to global singleton.
        reports_dir: Directory to store JSON reports.
        markdown_dir: Directory to store markdown reports (YYYY-WW.md files).
            Defaults to ``.forge/learning/weekly``.
        node_id: Node identifier for reports.

    Returns:
        The singleton PerformanceReporter.

    Raises:
        ValueError: When called first without task_source.
    """
    global _reporter_instance
    with _reporter_lock:
        if _reporter_instance is None:
            if task_source is None:
                raise ValueError(
                    "task_source is required when creating PerformanceReporter "
                    "singleton for the first time"
                )
            _reporter_instance = PerformanceReporter(
                task_source=task_source,
                event_bus=event_bus,
                reports_dir=reports_dir,
                markdown_dir=markdown_dir,
                node_id=node_id,
            )
        return _reporter_instance


def reset_performance_reporter() -> None:
    """Reset the singleton — intended for use in tests only."""
    global _reporter_instance
    with _reporter_lock:
        _reporter_instance = None
