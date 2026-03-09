"""Metrics exporter for FORGE Harness.

Exports iteration and fleet metrics in JSON and Prometheus formats.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from forge_harness.logging_config import get_logger

from .tracker import IterationTracker, create_tracker

logger = get_logger(__name__)


@dataclass
class MetricPoint:
    """A single metric data point."""

    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    help_text: str = ""
    metric_type: str = "gauge"  # gauge, counter, histogram


@dataclass
class MetricsSnapshot:
    """A snapshot of all metrics."""

    timestamp: datetime
    metrics: list[MetricPoint] = field(default_factory=list)


class MetricsExporter:
    """Exports metrics in multiple formats."""

    def __init__(
        self,
        project_name: str,
        tracker: IterationTracker | None = None,
        output_dir: Path | None = None,
    ):
        self.project_name = project_name
        self.tracker = tracker or create_tracker()
        self.output_dir = output_dir or Path(".forge/metrics")

    def collect(self) -> MetricsSnapshot:
        """Collect current metrics."""
        stats = self.tracker.get_stats()
        metrics: list[MetricPoint] = []

        # Iteration metrics
        metrics.append(
            MetricPoint(
                name="forge_iterations_total",
                value=float(stats.total_iterations),
                labels={"project": self.project_name},
                help_text="Total number of iterations",
                metric_type="counter",
            )
        )

        metrics.append(
            MetricPoint(
                name="forge_iterations_completed",
                value=float(stats.completed_iterations),
                labels={"project": self.project_name},
                help_text="Number of completed iterations",
                metric_type="counter",
            )
        )

        metrics.append(
            MetricPoint(
                name="forge_iterations_failed",
                value=float(stats.failed_iterations),
                labels={"project": self.project_name},
                help_text="Number of failed iterations",
                metric_type="counter",
            )
        )

        metrics.append(
            MetricPoint(
                name="forge_features_total",
                value=float(stats.total_features),
                labels={"project": self.project_name},
                help_text="Total features completed",
                metric_type="counter",
            )
        )

        metrics.append(
            MetricPoint(
                name="forge_tests_total",
                value=float(stats.total_tests),
                labels={"project": self.project_name},
                help_text="Total tests added",
                metric_type="counter",
            )
        )

        metrics.append(
            MetricPoint(
                name="forge_lines_total",
                value=float(stats.total_lines),
                labels={"project": self.project_name},
                help_text="Total lines of code added",
                metric_type="counter",
            )
        )

        metrics.append(
            MetricPoint(
                name="forge_iteration_duration_avg_seconds",
                value=stats.avg_duration,
                labels={"project": self.project_name},
                help_text="Average iteration duration in seconds",
                metric_type="gauge",
            )
        )

        # Success rate
        if stats.total_iterations > 0:
            success_rate = stats.completed_iterations / stats.total_iterations
        else:
            success_rate = 0.0

        metrics.append(
            MetricPoint(
                name="forge_success_rate",
                value=success_rate,
                labels={"project": self.project_name},
                help_text="Success rate (0.0 to 1.0)",
                metric_type="gauge",
            )
        )

        return MetricsSnapshot(
            timestamp=datetime.now(),
            metrics=metrics,
        )

    def to_json(self, snapshot: MetricsSnapshot | None = None) -> str:
        """Export metrics to JSON format."""
        snapshot = snapshot or self.collect()

        data = {
            "timestamp": snapshot.timestamp.isoformat(),
            "project": self.project_name,
            "metrics": {},
        }

        for metric in snapshot.metrics:
            data["metrics"][metric.name] = {
                "value": metric.value,
                "labels": metric.labels,
                "type": metric.metric_type,
                "help": metric.help_text,
            }

        return json.dumps(data, indent=2)

    def to_prometheus(self, snapshot: MetricsSnapshot | None = None) -> str:
        """Export metrics to Prometheus text format."""
        snapshot = snapshot or self.collect()
        lines: list[str] = []

        for metric in snapshot.metrics:
            # HELP line
            if metric.help_text:
                lines.append(f"# HELP {metric.name} {metric.help_text}")

            # TYPE line
            lines.append(f"# TYPE {metric.name} {metric.metric_type}")

            # Metric line with labels
            if metric.labels:
                label_str = ",".join(f'{k}="{v}"' for k, v in metric.labels.items())
                lines.append(f"{metric.name}{{{label_str}}} {metric.value}")
            else:
                lines.append(f"{metric.name} {metric.value}")

            lines.append("")  # Empty line between metrics

        return "\n".join(lines)

    def write_json(self, filename: str | None = None) -> Path:
        """Write metrics to JSON file."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = filename or f"metrics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        output_path = self.output_dir / filename
        output_path.write_text(self.to_json())
        return output_path

    def write_prometheus(self, filename: str = "metrics.prom") -> Path:
        """Write metrics to Prometheus file."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / filename
        output_path.write_text(self.to_prometheus())
        return output_path

    def write_all(self) -> dict[str, Path]:
        """Write metrics in all formats."""
        snapshot = self.collect()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        self.output_dir.mkdir(parents=True, exist_ok=True)

        json_path = self.output_dir / f"metrics_{timestamp}.json"
        json_path.write_text(self.to_json(snapshot))

        prom_path = self.output_dir / "metrics.prom"
        prom_path.write_text(self.to_prometheus(snapshot))

        return {
            "json": json_path,
            "prometheus": prom_path,
        }


def create_metrics_exporter(
    project_name: str,
    tracker: IterationTracker | None = None,
    output_dir: Path | None = None,
) -> MetricsExporter:
    """Factory function to create a MetricsExporter."""
    return MetricsExporter(
        project_name=project_name,
        tracker=tracker,
        output_dir=output_dir,
    )
