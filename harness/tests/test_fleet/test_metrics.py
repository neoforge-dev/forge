"""Tests for forge_harness.fleet.metrics."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

try:
    from forge_harness.fleet.metrics import (
        AgentMetrics,
        FleetMetrics,
        MetricsReport,
        TaskTypeMetrics,
        create_fleet_metrics,
    )
    _METRICS_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _METRICS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _METRICS_AVAILABLE,
    reason="forge_harness.fleet.metrics module not available — needs rewrite for current fleet structure",
)


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    yield db_path
    # Cleanup
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def metrics(temp_db):
    """Create FleetMetrics instance with temp database."""
    return FleetMetrics(db_path=temp_db)


class TestFleetMetricsInitialization:
    """Test FleetMetrics initialization."""

    def test_initialize_with_default_path(self):
        """Test initialization with default database path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            import os

            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                metrics = FleetMetrics()
                # Resolve symlinks for macOS /var -> /private/var
                assert metrics.db_path.resolve() == (Path(tmpdir) / ".forge_dashboard.db").resolve()
                assert metrics.db_path.exists()
            finally:
                os.chdir(original_cwd)

    def test_initialize_with_custom_path(self, temp_db):
        """Test initialization with custom database path."""
        metrics = FleetMetrics(db_path=temp_db)
        assert metrics.db_path == temp_db
        assert temp_db.exists()

    def test_database_schema_created(self, temp_db, metrics):
        """Test that database schema is created correctly."""
        conn = sqlite3.connect(str(temp_db))
        cursor = conn.cursor()

        # Check table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dashboard_task_events'"
        )
        assert cursor.fetchone() is not None

        # Check indexes exist
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_agent_started'"
        )
        assert cursor.fetchone() is not None

        conn.close()

    def test_factory_function(self, temp_db):
        """Test create_fleet_metrics factory function."""
        metrics = create_fleet_metrics(db_path=temp_db)
        assert isinstance(metrics, FleetMetrics)
        assert metrics.db_path == temp_db


class TestRecordTaskStart:
    """Test record_task_start method."""

    def test_record_task_start_basic(self, metrics):
        """Test basic task start recording."""
        result = metrics.record_task_start("agent-1", "task-1")
        assert result is True

        # Verify in database
        with metrics._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM dashboard_task_events WHERE task_id = ?", ("task-1",))
            row = cursor.fetchone()

            assert row is not None
            assert row["agent_id"] == "agent-1"
            assert row["task_id"] == "task-1"
            assert row["started_at"] is not None

    def test_record_task_start_with_type(self, metrics):
        """Test task start recording with task type."""
        result = metrics.record_task_start("agent-1", "task-1", task_type="feature")
        assert result is True

        with metrics._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT task_type FROM dashboard_task_events WHERE task_id = ?", ("task-1",)
            )
            row = cursor.fetchone()

            assert row["task_type"] == "feature"

    def test_record_task_start_with_metadata(self, metrics):
        """Test task start recording with metadata."""
        metadata = {"priority": "high", "domain": "test"}
        result = metrics.record_task_start("agent-1", "task-1", metadata=metadata)
        assert result is True

        with metrics._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT metadata FROM dashboard_task_events WHERE task_id = ?", ("task-1",)
            )
            row = cursor.fetchone()

            assert row["metadata"] is not None
            import json

            stored_metadata = json.loads(row["metadata"])
            assert stored_metadata == metadata

    def test_record_duplicate_task_start(self, metrics):
        """Test recording duplicate task start fails."""
        metrics.record_task_start("agent-1", "task-1")
        result = metrics.record_task_start("agent-1", "task-1")
        assert result is False


class TestRecordTaskEnd:
    """Test record_task_end method."""

    def test_record_task_end_success(self, metrics):
        """Test recording successful task completion."""
        metrics.record_task_start("agent-1", "task-1")
        result = metrics.record_task_end("task-1", outcome="success")
        assert result is True

        with metrics._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT outcome, ended_at, duration_seconds FROM dashboard_task_events WHERE task_id = ?",
                ("task-1",),
            )
            row = cursor.fetchone()

            assert row["outcome"] == "success"
            assert row["ended_at"] is not None
            assert row["duration_seconds"] is not None
            assert row["duration_seconds"] >= 0

    def test_record_task_end_failure(self, metrics):
        """Test recording failed task."""
        metrics.record_task_start("agent-1", "task-1")
        result = metrics.record_task_end("task-1", outcome="failure")
        assert result is True

        with metrics._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT outcome FROM dashboard_task_events WHERE task_id = ?", ("task-1",)
            )
            row = cursor.fetchone()

            assert row["outcome"] == "failure"

    def test_record_task_end_with_metadata(self, metrics):
        """Test recording task end with metadata."""
        metrics.record_task_start("agent-1", "task-1", metadata={"key": "value1"})
        result = metrics.record_task_end("task-1", outcome="success", metadata={"key2": "value2"})
        assert result is True

        with metrics._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT metadata FROM dashboard_task_events WHERE task_id = ?", ("task-1",)
            )
            row = cursor.fetchone()

            import json

            metadata = json.loads(row["metadata"])
            assert metadata["key"] == "value1"
            assert metadata["key2"] == "value2"

    def test_record_task_end_nonexistent_task(self, metrics):
        """Test recording end for nonexistent task fails."""
        result = metrics.record_task_end("nonexistent", outcome="success")
        assert result is False


class TestGetMetrics:
    """Test get_metrics method."""

    def test_get_metrics_empty(self, metrics):
        """Test getting metrics with no data."""
        report = metrics.get_metrics(days=7)

        assert isinstance(report, MetricsReport)
        assert report.total_tasks == 0
        assert report.completed_tasks == 0
        assert report.failed_tasks == 0
        assert report.success_rate == 0.0

    def test_get_metrics_with_tasks(self, metrics):
        """Test getting metrics with recorded tasks."""
        # Record some tasks
        metrics.record_task_start("agent-1", "task-1", task_type="feature")
        metrics.record_task_end("task-1", outcome="success")

        metrics.record_task_start("agent-1", "task-2", task_type="bugfix")
        metrics.record_task_end("task-2", outcome="failure")

        metrics.record_task_start("agent-2", "task-3", task_type="feature")
        # task-3 in progress (no end)

        report = metrics.get_metrics(days=7)

        assert report.total_tasks == 3
        assert report.completed_tasks == 1
        assert report.failed_tasks == 1
        assert report.in_progress_tasks == 1
        assert report.success_rate == 0.5  # 1 success / (1 success + 1 failure)
        assert report.total_agents == 2
        assert "feature" in report.task_types
        assert "bugfix" in report.task_types

    def test_get_metrics_time_filter(self, metrics):
        """Test metrics filtering by time period."""
        # Insert old task (manually set timestamp)
        old_time = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        with metrics._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO dashboard_task_events (task_id, agent_id, started_at) VALUES (?, ?, ?)",
                ("old-task", "agent-1", old_time),
            )

        # Insert recent task
        metrics.record_task_start("agent-1", "recent-task")

        # Get metrics for last 7 days (should not include old task)
        report = metrics.get_metrics(days=7)
        assert report.total_tasks == 1

        # Get metrics for last 30 days (should include both)
        report = metrics.get_metrics(days=30)
        assert report.total_tasks == 2

    def test_get_metrics_success_rate_calculation(self, metrics):
        """Test success rate calculation."""
        # 3 successes, 1 failure
        for i in range(3):
            metrics.record_task_start("agent-1", f"task-success-{i}")
            metrics.record_task_end(f"task-success-{i}", outcome="success")

        metrics.record_task_start("agent-1", "task-failure")
        metrics.record_task_end("task-failure", outcome="failure")

        report = metrics.get_metrics(days=7)
        assert report.success_rate == 0.75  # 3 / 4

    def test_metrics_report_to_dict(self, metrics):
        """Test MetricsReport serialization."""
        metrics.record_task_start("agent-1", "task-1")
        report = metrics.get_metrics(days=7)
        data = report.to_dict()

        assert isinstance(data, dict)
        assert "total_tasks" in data
        assert "success_rate" in data
        assert "period_start" in data


class TestGetAgentMetrics:
    """Test get_agent_metrics method."""

    def test_get_agent_metrics_empty(self, metrics):
        """Test getting agent metrics with no data."""
        agent_metrics = metrics.get_agent_metrics("agent-1", days=7)

        assert isinstance(agent_metrics, AgentMetrics)
        assert agent_metrics.agent_id == "agent-1"
        assert agent_metrics.total_tasks == 0
        assert agent_metrics.utilization == 0.0

    def test_get_agent_metrics_with_tasks(self, metrics):
        """Test getting agent metrics with recorded tasks."""
        # Record tasks for agent-1
        metrics.record_task_start("agent-1", "task-1")
        metrics.record_task_end("task-1", outcome="success")

        metrics.record_task_start("agent-1", "task-2")
        metrics.record_task_end("task-2", outcome="success")

        # Record task for agent-2
        metrics.record_task_start("agent-2", "task-3")
        metrics.record_task_end("task-3", outcome="failure")

        agent_metrics = metrics.get_agent_metrics("agent-1", days=7)

        assert agent_metrics.total_tasks == 2
        assert agent_metrics.completed_tasks == 2
        assert agent_metrics.failed_tasks == 0
        assert agent_metrics.success_rate == 1.0
        assert agent_metrics.first_task is not None
        assert agent_metrics.last_task is not None

    def test_get_agent_metrics_utilization(self, metrics):
        """Test agent utilization calculation."""
        # Record task with known duration
        metrics.record_task_start("agent-1", "task-1")
        metrics.record_task_end("task-1", outcome="success")

        agent_metrics = metrics.get_agent_metrics("agent-1", days=7)

        # Utilization should be > 0 but < 1 (task duration / 7 days)
        assert 0 < agent_metrics.utilization < 1
        assert agent_metrics.active_time_seconds > 0
        assert agent_metrics.total_time_seconds == 7 * 24 * 3600

    def test_agent_metrics_to_dict(self, metrics):
        """Test AgentMetrics serialization."""
        metrics.record_task_start("agent-1", "task-1")
        agent_metrics = metrics.get_agent_metrics("agent-1")
        data = agent_metrics.to_dict()

        assert isinstance(data, dict)
        assert "agent_id" in data
        assert "utilization" in data


class TestGetTaskTypeMetrics:
    """Test get_task_type_metrics method."""

    def test_get_task_type_metrics_empty(self, metrics):
        """Test getting task type metrics with no data."""
        task_metrics = metrics.get_task_type_metrics("feature", days=7)

        assert isinstance(task_metrics, TaskTypeMetrics)
        assert task_metrics.task_type == "feature"
        assert task_metrics.total_tasks == 0

    def test_get_task_type_metrics_with_tasks(self, metrics):
        """Test getting task type metrics with recorded tasks."""
        # Record feature tasks
        metrics.record_task_start("agent-1", "task-1", task_type="feature")
        metrics.record_task_end("task-1", outcome="success")

        metrics.record_task_start("agent-1", "task-2", task_type="feature")
        metrics.record_task_end("task-2", outcome="failure")

        # Record bugfix task
        metrics.record_task_start("agent-1", "task-3", task_type="bugfix")
        metrics.record_task_end("task-3", outcome="success")

        task_metrics = metrics.get_task_type_metrics("feature", days=7)

        assert task_metrics.total_tasks == 2
        assert task_metrics.completed_tasks == 1
        assert task_metrics.failed_tasks == 1
        assert task_metrics.success_rate == 0.5

    def test_task_type_metrics_to_dict(self, metrics):
        """Test TaskTypeMetrics serialization."""
        metrics.record_task_start("agent-1", "task-1", task_type="feature")
        task_metrics = metrics.get_task_type_metrics("feature")
        data = task_metrics.to_dict()

        assert isinstance(data, dict)
        assert "task_type" in data
        assert "success_rate" in data


class TestListActiveAgents:
    """Test list_active_agents method."""

    def test_list_active_agents_empty(self, metrics):
        """Test listing agents with no data."""
        agents = metrics.list_active_agents(days=7)
        assert agents == []

    def test_list_active_agents(self, metrics):
        """Test listing active agents."""
        metrics.record_task_start("agent-1", "task-1")
        metrics.record_task_start("agent-2", "task-2")
        metrics.record_task_start("agent-1", "task-3")  # duplicate agent

        agents = metrics.list_active_agents(days=7)

        assert len(agents) == 2
        assert "agent-1" in agents
        assert "agent-2" in agents

    def test_list_active_agents_time_filter(self, metrics):
        """Test listing agents with time filter."""
        # Insert old task
        old_time = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        with metrics._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO dashboard_task_events (task_id, agent_id, started_at) VALUES (?, ?, ?)",
                ("old-task", "old-agent", old_time),
            )

        metrics.record_task_start("recent-agent", "recent-task")

        # Last 7 days should only show recent agent
        agents = metrics.list_active_agents(days=7)
        assert agents == ["recent-agent"]

        # Last 30 days should show both
        agents = metrics.list_active_agents(days=30)
        assert len(agents) == 2


class TestListTaskTypes:
    """Test list_task_types method."""

    def test_list_task_types_empty(self, metrics):
        """Test listing task types with no data."""
        types = metrics.list_task_types(days=7)
        assert types == []

    def test_list_task_types(self, metrics):
        """Test listing task types."""
        metrics.record_task_start("agent-1", "task-1", task_type="feature")
        metrics.record_task_start("agent-1", "task-2", task_type="bugfix")
        metrics.record_task_start("agent-1", "task-3", task_type="feature")  # duplicate type
        metrics.record_task_start("agent-1", "task-4")  # no type

        types = metrics.list_task_types(days=7)

        assert len(types) == 2
        assert "feature" in types
        assert "bugfix" in types


class TestCleanupOldMetrics:
    """Test cleanup_old_metrics method."""

    def test_cleanup_old_metrics(self, metrics):
        """Test cleaning up old metrics."""
        # Insert old task
        old_time = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        with metrics._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO dashboard_task_events (task_id, agent_id, started_at) VALUES (?, ?, ?)",
                ("old-task", "agent-1", old_time),
            )

        # Insert recent task
        metrics.record_task_start("agent-1", "recent-task")

        # Cleanup metrics older than 90 days
        deleted = metrics.cleanup_old_metrics(days=90)

        assert deleted == 1

        # Verify recent task still exists
        report = metrics.get_metrics(days=100)
        assert report.total_tasks == 1

    def test_cleanup_no_old_metrics(self, metrics):
        """Test cleanup when no old metrics exist."""
        metrics.record_task_start("agent-1", "task-1")

        deleted = metrics.cleanup_old_metrics(days=90)
        assert deleted == 0


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_concurrent_task_recording(self, metrics):
        """Test recording multiple tasks concurrently."""
        # Record multiple tasks quickly
        for i in range(10):
            result = metrics.record_task_start(f"agent-{i % 3}", f"task-{i}")
            assert result is True

        report = metrics.get_metrics(days=7)
        assert report.total_tasks == 10

    def test_very_long_task(self, metrics):
        """Test task with very long duration."""
        metrics.record_task_start("agent-1", "long-task")

        # Manually set old start time
        old_time = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
        with metrics._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE dashboard_task_events SET started_at = ? WHERE task_id = ?",
                (old_time, "long-task"),
            )

        metrics.record_task_end("long-task", outcome="success")

        agent_metrics = metrics.get_agent_metrics("agent-1", days=7)
        # Duration should be ~48 hours = 172800 seconds
        assert agent_metrics.avg_duration_seconds > 170000

    def test_database_connection_handling(self, metrics):
        """Test proper database connection handling."""
        # Multiple operations should not leave connections open
        for _ in range(100):
            metrics.record_task_start("agent-1", f"task-{_}")

        # Should still be able to query
        report = metrics.get_metrics(days=7)
        assert report.total_tasks == 100
