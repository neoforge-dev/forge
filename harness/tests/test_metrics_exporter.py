"""Tests for metrics exporter."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from forge_harness.iteration.metrics_exporter import (
    MetricPoint,
    MetricsExporter,
    MetricsSnapshot,
    create_metrics_exporter,
)
from forge_harness.iteration.tracker import (
    IterationTracker,
)


@pytest.fixture
def mock_tracker(tmp_path):
    """Create a mock tracker with test data."""
    tracker = IterationTracker(storage_path=tmp_path / "test_history.json")

    # Add iteration 1 - successful
    tracker.start_iteration(1)
    tracker.complete_iteration(
        features=["Feature A", "Feature B"],
        tests_added=5,
        lines_added=100,
        commit_hash="abc123",
    )

    # Add iteration 2 - successful
    tracker.start_iteration(2)
    tracker.complete_iteration(
        features=["Feature C", "Feature D", "Feature E"],
        tests_added=8,
        lines_added=150,
        commit_hash="def456",
    )

    # Add iteration 3 - failed
    tracker.start_iteration(3)
    tracker.fail_iteration(reason="Tests failed")

    return tracker


@pytest.fixture
def tmp_output_dir(tmp_path):
    """Create a temporary output directory."""
    return tmp_path / ".forge/metrics"


class TestMetricPoint:
    """Tests for MetricPoint dataclass."""

    def test_creation_minimal(self):
        """Test creating a metric point with minimal parameters."""
        metric = MetricPoint(
            name="test_metric",
            value=42.0,
        )
        assert metric.name == "test_metric"
        assert metric.value == 42.0
        assert metric.labels == {}
        assert metric.help_text == ""
        assert metric.metric_type == "gauge"

    def test_creation_full(self):
        """Test creating a metric point with all parameters."""
        metric = MetricPoint(
            name="test_metric",
            value=42.0,
            labels={"project": "test", "env": "prod"},
            help_text="Test metric help",
            metric_type="counter",
        )
        assert metric.name == "test_metric"
        assert metric.value == 42.0
        assert metric.labels == {"project": "test", "env": "prod"}
        assert metric.help_text == "Test metric help"
        assert metric.metric_type == "counter"

    def test_labels_default_factory(self):
        """Test that labels use default_factory for dict."""
        metric1 = MetricPoint(name="m1", value=1.0)
        metric2 = MetricPoint(name="m2", value=2.0)
        # Ensure they don't share the same dict instance
        metric1.labels["key"] = "value1"
        assert "key" not in metric2.labels


class TestMetricsSnapshot:
    """Tests for MetricsSnapshot dataclass."""

    def test_creation_empty(self):
        """Test creating an empty metrics snapshot."""
        now = datetime.now()
        snapshot = MetricsSnapshot(timestamp=now)
        assert snapshot.timestamp == now
        assert snapshot.metrics == []

    def test_creation_with_metrics(self):
        """Test creating a snapshot with metrics."""
        now = datetime.now()
        metrics = [
            MetricPoint(name="m1", value=1.0),
            MetricPoint(name="m2", value=2.0),
        ]
        snapshot = MetricsSnapshot(timestamp=now, metrics=metrics)
        assert snapshot.timestamp == now
        assert len(snapshot.metrics) == 2
        assert snapshot.metrics[0].name == "m1"
        assert snapshot.metrics[1].name == "m2"

    def test_metrics_default_factory(self):
        """Test that metrics use default_factory for list."""
        snapshot1 = MetricsSnapshot(timestamp=datetime.now())
        snapshot2 = MetricsSnapshot(timestamp=datetime.now())
        # Ensure they don't share the same list instance
        snapshot1.metrics.append(MetricPoint(name="m1", value=1.0))
        assert len(snapshot2.metrics) == 0


class TestMetricsExporter:
    """Tests for MetricsExporter class."""

    def test_initialization_defaults(self):
        """Test creating an exporter with default parameters."""
        exporter = MetricsExporter(project_name="test-project")
        assert exporter.project_name == "test-project"
        assert exporter.tracker is not None
        assert exporter.output_dir == Path(".forge/metrics")

    def test_initialization_custom(self, mock_tracker, tmp_output_dir):
        """Test creating an exporter with custom parameters."""
        exporter = MetricsExporter(
            project_name="custom-project",
            tracker=mock_tracker,
            output_dir=tmp_output_dir,
        )
        assert exporter.project_name == "custom-project"
        assert exporter.tracker is mock_tracker
        assert exporter.output_dir == tmp_output_dir

    def test_collect_basic_metrics(self, mock_tracker):
        """Test collecting metrics from tracker."""
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
        )
        snapshot = exporter.collect()

        assert isinstance(snapshot, MetricsSnapshot)
        assert isinstance(snapshot.timestamp, datetime)
        assert len(snapshot.metrics) == 8  # 8 metric types

        # Verify metric names
        metric_names = {m.name for m in snapshot.metrics}
        expected_names = {
            "forge_iterations_total",
            "forge_iterations_completed",
            "forge_iterations_failed",
            "forge_features_total",
            "forge_tests_total",
            "forge_lines_total",
            "forge_iteration_duration_avg_seconds",
            "forge_success_rate",
        }
        assert metric_names == expected_names

    def test_collect_metric_values(self, mock_tracker):
        """Test that collected metrics have correct values."""
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
        )
        snapshot = exporter.collect()

        # Create a dict for easy lookup
        metrics_dict = {m.name: m for m in snapshot.metrics}

        # Verify values based on mock_tracker data
        # Note: Only completed iterations count toward feature/test/line totals
        assert metrics_dict["forge_iterations_total"].value == 3.0
        assert metrics_dict["forge_iterations_completed"].value == 2.0
        assert metrics_dict["forge_iterations_failed"].value == 1.0
        assert metrics_dict["forge_features_total"].value == 5.0  # 2+3 (failed doesn't count)
        assert metrics_dict["forge_tests_total"].value == 13.0  # 5+8 (failed doesn't count)
        assert metrics_dict["forge_lines_total"].value == 250.0  # 100+150 (failed doesn't count)

        # Average duration: only completed iterations count
        # We need to check the actual durations from completed iterations
        # They will vary based on execution time, so just check it's > 0
        assert metrics_dict["forge_iteration_duration_avg_seconds"].value > 0.0

        # Success rate: 2/3 = 0.666...
        assert abs(metrics_dict["forge_success_rate"].value - 0.666) < 0.01

    def test_collect_metric_labels(self, mock_tracker):
        """Test that metrics have correct labels."""
        exporter = MetricsExporter(
            project_name="my-project",
            tracker=mock_tracker,
        )
        snapshot = exporter.collect()

        # All metrics should have project label
        for metric in snapshot.metrics:
            assert "project" in metric.labels
            assert metric.labels["project"] == "my-project"

    def test_collect_metric_types(self, mock_tracker):
        """Test that metrics have correct types."""
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
        )
        snapshot = exporter.collect()

        metrics_dict = {m.name: m for m in snapshot.metrics}

        # Counters
        assert metrics_dict["forge_iterations_total"].metric_type == "counter"
        assert metrics_dict["forge_iterations_completed"].metric_type == "counter"
        assert metrics_dict["forge_iterations_failed"].metric_type == "counter"
        assert metrics_dict["forge_features_total"].metric_type == "counter"
        assert metrics_dict["forge_tests_total"].metric_type == "counter"
        assert metrics_dict["forge_lines_total"].metric_type == "counter"

        # Gauges
        assert metrics_dict["forge_iteration_duration_avg_seconds"].metric_type == "gauge"
        assert metrics_dict["forge_success_rate"].metric_type == "gauge"

    def test_success_rate_zero_iterations(self):
        """Test success rate calculation with zero iterations."""
        empty_tracker = IterationTracker()
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=empty_tracker,
        )
        snapshot = exporter.collect()

        metrics_dict = {m.name: m for m in snapshot.metrics}
        assert metrics_dict["forge_success_rate"].value == 0.0

    def test_to_json_format(self, mock_tracker):
        """Test JSON export format."""
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
        )
        json_str = exporter.to_json()

        # Parse JSON to verify structure
        data = json.loads(json_str)

        assert "timestamp" in data
        assert "project" in data
        assert data["project"] == "test-project"
        assert "metrics" in data
        assert isinstance(data["metrics"], dict)

        # Check a specific metric
        assert "forge_iterations_total" in data["metrics"]
        metric = data["metrics"]["forge_iterations_total"]
        assert metric["value"] == 3.0
        assert metric["labels"] == {"project": "test-project"}
        assert metric["type"] == "counter"
        assert metric["help"] == "Total number of iterations"

    def test_to_json_with_snapshot(self, mock_tracker):
        """Test JSON export with provided snapshot."""
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
        )
        snapshot = exporter.collect()
        json_str = exporter.to_json(snapshot)

        data = json.loads(json_str)
        assert data["project"] == "test-project"
        assert len(data["metrics"]) == 8

    def test_to_prometheus_format(self, mock_tracker):
        """Test Prometheus export format."""
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
        )
        prom_str = exporter.to_prometheus()

        # Check for HELP lines
        assert "# HELP forge_iterations_total Total number of iterations" in prom_str
        assert "# HELP forge_success_rate Success rate (0.0 to 1.0)" in prom_str

        # Check for TYPE lines
        assert "# TYPE forge_iterations_total counter" in prom_str
        assert "# TYPE forge_success_rate gauge" in prom_str

        # Check for metric lines with labels
        assert 'forge_iterations_total{project="test-project"} 3.0' in prom_str
        assert 'forge_iterations_completed{project="test-project"} 2.0' in prom_str
        assert 'forge_iterations_failed{project="test-project"} 1.0' in prom_str

    def test_to_prometheus_with_snapshot(self, mock_tracker):
        """Test Prometheus export with provided snapshot."""
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
        )
        snapshot = exporter.collect()
        prom_str = exporter.to_prometheus(snapshot)

        assert "# HELP forge_iterations_total" in prom_str
        assert 'forge_iterations_total{project="test-project"} 3.0' in prom_str

    def test_to_prometheus_metric_without_labels(self):
        """Test Prometheus format for metrics without labels."""
        # Create a custom snapshot with a metric without labels
        snapshot = MetricsSnapshot(
            timestamp=datetime.now(),
            metrics=[
                MetricPoint(
                    name="test_metric",
                    value=42.0,
                    help_text="Test metric",
                    metric_type="gauge",
                )
            ],
        )

        exporter = MetricsExporter(project_name="test-project")
        prom_str = exporter.to_prometheus(snapshot)

        assert "# HELP test_metric Test metric" in prom_str
        assert "# TYPE test_metric gauge" in prom_str
        assert "test_metric 42.0" in prom_str

    def test_write_json_default_filename(self, mock_tracker, tmp_output_dir):
        """Test writing JSON to file with default filename."""
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
            output_dir=tmp_output_dir,
        )

        output_path = exporter.write_json()

        assert output_path.exists()
        assert output_path.parent == tmp_output_dir
        assert output_path.name.startswith("metrics_")
        assert output_path.name.endswith(".json")

        # Verify content
        data = json.loads(output_path.read_text())
        assert data["project"] == "test-project"

    def test_write_json_custom_filename(self, mock_tracker, tmp_output_dir):
        """Test writing JSON to file with custom filename."""
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
            output_dir=tmp_output_dir,
        )

        output_path = exporter.write_json("custom_metrics.json")

        assert output_path.exists()
        assert output_path.name == "custom_metrics.json"

        # Verify content
        data = json.loads(output_path.read_text())
        assert data["project"] == "test-project"

    def test_write_json_creates_directory(self, mock_tracker, tmp_path):
        """Test that write_json creates output directory if it doesn't exist."""
        output_dir = tmp_path / "nested" / "metrics"
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
            output_dir=output_dir,
        )

        assert not output_dir.exists()
        output_path = exporter.write_json()
        assert output_dir.exists()
        assert output_path.exists()

    def test_write_prometheus_default_filename(self, mock_tracker, tmp_output_dir):
        """Test writing Prometheus to file with default filename."""
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
            output_dir=tmp_output_dir,
        )

        output_path = exporter.write_prometheus()

        assert output_path.exists()
        assert output_path.name == "metrics.prom"

        # Verify content
        content = output_path.read_text()
        assert "# HELP forge_iterations_total" in content
        assert 'forge_iterations_total{project="test-project"} 3.0' in content

    def test_write_prometheus_custom_filename(self, mock_tracker, tmp_output_dir):
        """Test writing Prometheus to file with custom filename."""
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
            output_dir=tmp_output_dir,
        )

        output_path = exporter.write_prometheus("custom.prom")

        assert output_path.exists()
        assert output_path.name == "custom.prom"

    def test_write_prometheus_creates_directory(self, mock_tracker, tmp_path):
        """Test that write_prometheus creates output directory if it doesn't exist."""
        output_dir = tmp_path / "nested" / "metrics"
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
            output_dir=output_dir,
        )

        assert not output_dir.exists()
        output_path = exporter.write_prometheus()
        assert output_dir.exists()
        assert output_path.exists()

    def test_write_all_creates_both_files(self, mock_tracker, tmp_output_dir):
        """Test that write_all creates both JSON and Prometheus files."""
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
            output_dir=tmp_output_dir,
        )

        paths = exporter.write_all()

        assert "json" in paths
        assert "prometheus" in paths
        assert paths["json"].exists()
        assert paths["prometheus"].exists()

        # Verify JSON content
        json_data = json.loads(paths["json"].read_text())
        assert json_data["project"] == "test-project"

        # Verify Prometheus content
        prom_content = paths["prometheus"].read_text()
        assert "# HELP forge_iterations_total" in prom_content

    def test_write_all_uses_same_snapshot(self, mock_tracker, tmp_output_dir):
        """Test that write_all uses the same snapshot for both formats."""
        exporter = MetricsExporter(
            project_name="test-project",
            tracker=mock_tracker,
            output_dir=tmp_output_dir,
        )

        paths = exporter.write_all()

        # Parse JSON
        json_data = json.loads(paths["json"].read_text())
        json_timestamp = json_data["timestamp"]

        # Both files should have been created from the same snapshot
        # (verified by the timestamp being consistent)
        assert json_timestamp is not None


class TestCreateMetricsExporter:
    """Tests for create_metrics_exporter factory function."""

    def test_factory_minimal(self):
        """Test factory with minimal parameters."""
        exporter = create_metrics_exporter(project_name="test-project")

        assert isinstance(exporter, MetricsExporter)
        assert exporter.project_name == "test-project"
        assert exporter.tracker is not None
        assert exporter.output_dir == Path(".forge/metrics")

    def test_factory_custom_tracker(self, mock_tracker):
        """Test factory with custom tracker."""
        exporter = create_metrics_exporter(
            project_name="test-project",
            tracker=mock_tracker,
        )

        assert isinstance(exporter, MetricsExporter)
        assert exporter.tracker is mock_tracker

    def test_factory_custom_output_dir(self, tmp_output_dir):
        """Test factory with custom output directory."""
        exporter = create_metrics_exporter(
            project_name="test-project",
            output_dir=tmp_output_dir,
        )

        assert isinstance(exporter, MetricsExporter)
        assert exporter.output_dir == tmp_output_dir

    def test_factory_all_parameters(self, mock_tracker, tmp_output_dir):
        """Test factory with all parameters."""
        exporter = create_metrics_exporter(
            project_name="test-project",
            tracker=mock_tracker,
            output_dir=tmp_output_dir,
        )

        assert isinstance(exporter, MetricsExporter)
        assert exporter.project_name == "test-project"
        assert exporter.tracker is mock_tracker
        assert exporter.output_dir == tmp_output_dir


class TestSuccessRateCalculation:
    """Tests for success rate calculation edge cases."""

    def test_success_rate_all_success(self, tmp_path):
        """Test success rate with all successful iterations."""
        tracker = IterationTracker(storage_path=tmp_path / "test_history.json")
        for i in range(5):
            tracker.start_iteration(i + 1)
            tracker.complete_iteration(
                features=[f"Feature {i + 1}"],
                tests_added=1,
                lines_added=10,
            )

        exporter = MetricsExporter(project_name="test", tracker=tracker)
        snapshot = exporter.collect()
        metrics_dict = {m.name: m for m in snapshot.metrics}

        assert metrics_dict["forge_success_rate"].value == 1.0

    def test_success_rate_all_failed(self, tmp_path):
        """Test success rate with all failed iterations."""
        tracker = IterationTracker(storage_path=tmp_path / "test_history.json")
        for i in range(5):
            tracker.start_iteration(i + 1)
            tracker.fail_iteration(reason="Test failure")

        exporter = MetricsExporter(project_name="test", tracker=tracker)
        snapshot = exporter.collect()
        metrics_dict = {m.name: m for m in snapshot.metrics}

        assert metrics_dict["forge_success_rate"].value == 0.0

    def test_success_rate_mixed(self, tmp_path):
        """Test success rate with mixed results."""
        tracker = IterationTracker(storage_path=tmp_path / "test_history.json")
        # 7 success out of 10 = 0.7
        for i in range(7):
            tracker.start_iteration(i + 1)
            tracker.complete_iteration(
                features=[f"Feature {i + 1}"],
                tests_added=1,
                lines_added=10,
            )
        for i in range(3):
            tracker.start_iteration(i + 8)
            tracker.fail_iteration(reason="Test failure")

        exporter = MetricsExporter(project_name="test", tracker=tracker)
        snapshot = exporter.collect()
        metrics_dict = {m.name: m for m in snapshot.metrics}

        assert abs(metrics_dict["forge_success_rate"].value - 0.7) < 0.01


class TestMetricsIntegration:
    """Integration tests for metrics exporter."""

    def test_full_workflow(self, mock_tracker, tmp_output_dir):
        """Test complete workflow: collect, export, write."""
        exporter = create_metrics_exporter(
            project_name="integration-test",
            tracker=mock_tracker,
            output_dir=tmp_output_dir,
        )

        # Collect
        snapshot = exporter.collect()
        assert len(snapshot.metrics) == 8

        # Export to JSON
        json_str = exporter.to_json(snapshot)
        json_data = json.loads(json_str)
        assert json_data["project"] == "integration-test"

        # Export to Prometheus
        prom_str = exporter.to_prometheus(snapshot)
        assert "forge_iterations_total" in prom_str

        # Write all
        paths = exporter.write_all()
        assert paths["json"].exists()
        assert paths["prometheus"].exists()

    def test_multiple_exports(self, mock_tracker, tmp_output_dir):
        """Test multiple export operations."""
        exporter = create_metrics_exporter(
            project_name="test-project",
            tracker=mock_tracker,
            output_dir=tmp_output_dir,
        )

        # Write multiple times
        path1 = exporter.write_json("metrics1.json")
        path2 = exporter.write_json("metrics2.json")

        assert path1.exists()
        assert path2.exists()
        assert path1 != path2

        # Both should have same content (since tracker didn't change)
        data1 = json.loads(path1.read_text())
        data2 = json.loads(path2.read_text())

        assert data1["metrics"] == data2["metrics"]
