"""Weekly Performance Reporter — DF-5003
=========================================

Generates weekly autonomous performance reviews covering quality, cost,
and lead-time metrics.  Reports are stored in-memory and can be serialised
to JSON for persistence or rendered to markdown for human review.

Metrics tracked
---------------
1. **Quality**: evaluator pass rate, escaped defects, audit pass rate.
2. **Cost**: total tasks, autonomous completions, human reviews,
   automation ratio.
3. **Lead time**: mean cycle time, p95 cycle time, SLO compliance rate.

Each report covers a 7-day window and compares against the previous
window to surface trends (improving, stable, degrading).

Design principles
-----------------
* **Stateless compute**: the reporter computes from input data, not from
  internal state.  This makes it testable and replayable.
* **Trend detection**: each metric is compared to the previous week.
* **Singleton**: :func:`get_perf_reporter` / :func:`reset_perf_reporter`.
* **Thread-safe**: ``RLock`` guarded.

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-5003)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WeeklyMetrics:
    """Input metrics for a single week.

    All rates are floats in [0, 1].
    """

    # Quality
    evaluator_pass_rate: float
    escaped_defects: int
    audit_pass_rate: float

    # Cost / throughput
    total_tasks: int
    autonomous_completions: int
    human_reviews: int

    # Lead time (minutes)
    mean_cycle_time_minutes: float
    p95_cycle_time_minutes: float
    slo_target_minutes: float

    # Period
    period_start: datetime
    period_end: datetime

    @property
    def automation_ratio(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return self.autonomous_completions / self.total_tasks

    @property
    def slo_compliance_rate(self) -> float:
        """Fraction of tasks completing within SLO (estimated from p95)."""
        if self.slo_target_minutes <= 0:
            return 1.0
        return 1.0 if self.p95_cycle_time_minutes <= self.slo_target_minutes else 0.95

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluator_pass_rate": self.evaluator_pass_rate,
            "escaped_defects": self.escaped_defects,
            "audit_pass_rate": self.audit_pass_rate,
            "total_tasks": self.total_tasks,
            "autonomous_completions": self.autonomous_completions,
            "human_reviews": self.human_reviews,
            "automation_ratio": round(self.automation_ratio, 4),
            "mean_cycle_time_minutes": self.mean_cycle_time_minutes,
            "p95_cycle_time_minutes": self.p95_cycle_time_minutes,
            "slo_target_minutes": self.slo_target_minutes,
            "slo_compliance_rate": round(self.slo_compliance_rate, 4),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
        }


class Trend:
    """Trend direction constants."""
    IMPROVING = "improving"
    STABLE = "stable"
    DEGRADING = "degrading"


@dataclass(frozen=True)
class MetricTrend:
    """Trend analysis for a single metric.

    Attributes:
        name:      Metric name.
        current:   Current week value.
        previous:  Previous week value (None if no history).
        trend:     "improving", "stable", or "degrading".
        delta:     Absolute change from previous week.
        delta_pct: Percentage change from previous week.
    """

    name: str
    current: float
    previous: float | None
    trend: str
    delta: float
    delta_pct: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "current": self.current,
            "previous": self.previous,
            "trend": self.trend,
            "delta": round(self.delta, 4),
            "delta_pct": round(self.delta_pct, 2),
        }


@dataclass(frozen=True)
class WeeklyReport:
    """Complete weekly performance report.

    Attributes:
        report_id:    Unique ID.
        metrics:      This week's metrics.
        trends:       Trend analysis vs previous week.
        overall_health: "healthy", "warning", or "critical".
        summary:      Human-readable one-line summary.
        generated_at: When the report was generated.
    """

    report_id: str
    metrics: WeeklyMetrics
    trends: tuple[MetricTrend, ...]
    overall_health: str
    summary: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "metrics": self.metrics.to_dict(),
            "trends": [t.to_dict() for t in self.trends],
            "overall_health": self.overall_health,
            "summary": self.summary,
            "generated_at": self.generated_at.isoformat(),
        }

    def to_markdown(self) -> str:
        """Render as a markdown report."""
        lines = [
            "# Weekly Dark Factory Performance Report",
            f"**Period:** {self.metrics.period_start.strftime('%Y-%m-%d')} → {self.metrics.period_end.strftime('%Y-%m-%d')}",
            f"**Health:** {self.overall_health.upper()}",
            f"**Summary:** {self.summary}",
            "",
            "## Quality",
            "| Metric | Value | Trend |",
            "|--------|-------|-------|",
        ]

        for t in self.trends:
            if t.name in ("evaluator_pass_rate", "escaped_defects", "audit_pass_rate"):
                val = f"{t.current:.1%}" if isinstance(t.current, float) and t.current <= 1.0 else str(t.current)
                arrow = {"improving": "+", "stable": "=", "degrading": "-"}.get(t.trend, "?")
                lines.append(f"| {t.name} | {val} | {arrow} {t.trend} |")

        lines.extend([
            "",
            "## Throughput",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total tasks | {self.metrics.total_tasks} |",
            f"| Autonomous | {self.metrics.autonomous_completions} ({self.metrics.automation_ratio:.0%}) |",
            f"| Human reviews | {self.metrics.human_reviews} |",
            "",
            "## Lead Time",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Mean cycle time | {self.metrics.mean_cycle_time_minutes:.0f}m |",
            f"| P95 cycle time | {self.metrics.p95_cycle_time_minutes:.0f}m |",
            f"| SLO target | {self.metrics.slo_target_minutes:.0f}m |",
            "",
        ])

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Performance Reporter
# ---------------------------------------------------------------------------


class PerfReporter:
    """Generates weekly Dark Factory performance reports."""

    # Thresholds for trend detection (percentage change)
    TREND_THRESHOLD = 0.02  # 2% change = significant

    def __init__(self) -> None:
        self._lock = RLock()
        self._reports: list[WeeklyReport] = []
        logger.info("PerfReporter initialised")

    def _compute_trend(
        self,
        name: str,
        current: float,
        previous: float | None,
        higher_is_better: bool = True,
    ) -> MetricTrend:
        """Compute trend for a single metric."""
        if previous is None:
            return MetricTrend(
                name=name, current=current, previous=None,
                trend=Trend.STABLE, delta=0.0, delta_pct=0.0,
            )

        delta = current - previous
        if previous != 0:
            delta_pct = (delta / abs(previous)) * 100
        else:
            delta_pct = 100.0 if delta > 0 else (0.0 if delta == 0 else -100.0)

        if abs(delta_pct) < self.TREND_THRESHOLD * 100:
            trend = Trend.STABLE
        elif higher_is_better:
            trend = Trend.IMPROVING if delta > 0 else Trend.DEGRADING
        else:
            trend = Trend.IMPROVING if delta < 0 else Trend.DEGRADING

        return MetricTrend(
            name=name, current=current, previous=previous,
            trend=trend, delta=delta, delta_pct=delta_pct,
        )

    def _assess_health(self, metrics: WeeklyMetrics, trends: list[MetricTrend]) -> str:
        """Determine overall health status."""
        if metrics.escaped_defects > 0:
            return "critical"
        if metrics.evaluator_pass_rate < 0.95:
            return "warning"

        degrading_count = sum(1 for t in trends if t.trend == Trend.DEGRADING)
        if degrading_count >= 3:
            return "warning"

        return "healthy"

    def _generate_summary(self, metrics: WeeklyMetrics, health: str) -> str:
        """Generate a one-line summary."""
        parts = [
            f"{metrics.total_tasks} tasks",
            f"{metrics.automation_ratio:.0%} automated",
            f"{metrics.evaluator_pass_rate:.1%} eval pass rate",
        ]
        if metrics.escaped_defects > 0:
            parts.append(f"{metrics.escaped_defects} escaped defects")
        return f"{health.upper()}: " + ", ".join(parts)

    def generate_report(
        self,
        current: WeeklyMetrics,
        previous: WeeklyMetrics | None = None,
    ) -> WeeklyReport:
        """Generate a weekly performance report.

        Args:
            current: This week's metrics.
            previous: Previous week's metrics (for trend analysis).

        Returns:
            A :class:`WeeklyReport`.
        """
        prev_eval = previous.evaluator_pass_rate if previous else None
        prev_defects = float(previous.escaped_defects) if previous else None
        prev_audit = previous.audit_pass_rate if previous else None
        prev_auto = previous.automation_ratio if previous else None
        prev_mean_ct = previous.mean_cycle_time_minutes if previous else None
        prev_requeue = previous.slo_compliance_rate if previous else None

        trends = [
            self._compute_trend("evaluator_pass_rate", current.evaluator_pass_rate, prev_eval),
            self._compute_trend("escaped_defects", float(current.escaped_defects), prev_defects, higher_is_better=False),
            self._compute_trend("audit_pass_rate", current.audit_pass_rate, prev_audit),
            self._compute_trend("automation_ratio", current.automation_ratio, prev_auto),
            self._compute_trend("mean_cycle_time", current.mean_cycle_time_minutes, prev_mean_ct, higher_is_better=False),
            self._compute_trend("slo_compliance", current.slo_compliance_rate, prev_requeue),
        ]

        health = self._assess_health(current, trends)
        summary = self._generate_summary(current, health)

        report = WeeklyReport(
            report_id=str(uuid.uuid4()),
            metrics=current,
            trends=tuple(trends),
            overall_health=health,
            summary=summary,
        )

        with self._lock:
            self._reports.append(report)

        logger.info(
            "Weekly report generated: %s (%d tasks, %s)",
            health,
            current.total_tasks,
            report.report_id[:8],
        )

        return report

    def get_reports(self, limit: int = 10) -> list[WeeklyReport]:
        """Get stored reports, most recent first."""
        with self._lock:
            return list(reversed(self._reports[-limit:]))

    def get_latest_report(self) -> WeeklyReport | None:
        """Get the most recent report."""
        with self._lock:
            return self._reports[-1] if self._reports else None

    def reset(self) -> None:
        """Reset all state. For testing."""
        with self._lock:
            self._reports.clear()
            logger.info("PerfReporter reset")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: PerfReporter | None = None
_instance_lock = RLock()


def get_perf_reporter() -> PerfReporter:
    """Get the shared PerfReporter singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = PerfReporter()
        return _instance


def reset_perf_reporter() -> None:
    """Reset the singleton (for tests)."""
    global _instance
    with _instance_lock:
        if _instance is not None:
            _instance.reset()
        _instance = None
