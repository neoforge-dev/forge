"""Tests for fleet metrics collection.

Covers:
- AgentMetricsSnapshot dataclass
- FleetMetricsSnapshot dataclass
- MetricsCollector (collect, history, prometheus export, persistence)
- MetricsReport dataclass + to_dict
- AgentMetrics dataclass + to_dict
- TaskTypeMetrics dataclass + to_dict
- FleetMetrics (SQLite-backed) – all CRUD methods
- create_metrics_collector factory
- create_fleet_metrics factory
- record_dispatch_metrics helper
"""

import json
import tempfile
import time
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Suppress the module-level DeprecationWarning so it does not pollute test
# output and so the fixture-level import works cleanly.
try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from forge_harness.fleet.metrics import (
            AgentMetrics,
            AgentMetricsSnapshot,
            FleetMetrics,
            FleetMetricsSnapshot,
            MetricsCollector,
            MetricsReport,
            TaskTypeMetrics,
            create_fleet_metrics,
            create_metrics_collector,
            record_dispatch_metrics,
        )
    _METRICS_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _METRICS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _METRICS_AVAILABLE,
    reason="forge_harness.fleet.metrics module not available — needs rewrite for current fleet structure",
)


# ---------------------------------------------------------------------------
# AgentMetricsSnapshot
# ---------------------------------------------------------------------------


class TestAgentMetricsSnapshot:
    def test_agent_metrics_creation(self):
        metrics = AgentMetricsSnapshot(
            agent_id="test-agent",
            status="active",
            context_percentage=75,
            tasks_completed=5,
        )
        assert metrics.agent_id == "test-agent"
        assert metrics.status == "active"
        assert metrics.context_percentage == 75
        assert metrics.tasks_completed == 5
        assert metrics.errors == 0
        assert metrics.uptime == 0.0

    def test_agent_metrics_with_defaults(self):
        metrics = AgentMetricsSnapshot(agent_id="test", status="idle")
        assert metrics.context_percentage == 100
        assert metrics.tasks_completed == 0
        assert metrics.errors == 0
        assert metrics.last_activity is None

    def test_agent_metrics_with_all_fields(self):
        ts = datetime.now(UTC)
        metrics = AgentMetricsSnapshot(
            agent_id="agent-x",
            status="error",
            context_percentage=90,
            tasks_completed=10,
            errors=3,
            last_activity=ts,
            uptime=3600.0,
        )
        assert metrics.agent_id == "agent-x"
        assert metrics.status == "error"
        assert metrics.errors == 3
        assert metrics.last_activity == ts
        assert metrics.uptime == 3600.0


# ---------------------------------------------------------------------------
# FleetMetricsSnapshot
# ---------------------------------------------------------------------------


class TestFleetMetricsSnapshot:
    def test_fleet_metrics_creation(self):
        metrics = FleetMetricsSnapshot(
            total_agents=5,
            active_agents=3,
            idle_agents=2,
        )
        assert metrics.total_agents == 5
        assert metrics.active_agents == 3
        assert metrics.idle_agents == 2
        assert metrics.error_agents == 0

    def test_fleet_metrics_with_defaults(self):
        metrics = FleetMetricsSnapshot()
        assert metrics.total_agents == 0
        assert metrics.active_agents == 0
        assert metrics.avg_context_usage == 0.0
        assert isinstance(metrics.timestamp, datetime)

    def test_fleet_metrics_timestamp_is_recent(self):
        before = datetime.now()
        metrics = FleetMetricsSnapshot()
        after = datetime.now()
        assert before <= metrics.timestamp <= after

    def test_fleet_metrics_all_fields(self):
        ts = datetime(2026, 1, 1, 12, 0, 0)
        metrics = FleetMetricsSnapshot(
            total_agents=10,
            active_agents=4,
            idle_agents=3,
            error_agents=2,
            avg_context_usage=55.5,
            total_tasks_completed=100,
            uptime_seconds=7200.0,
            timestamp=ts,
        )
        assert metrics.error_agents == 2
        assert metrics.avg_context_usage == 55.5
        assert metrics.total_tasks_completed == 100
        assert metrics.uptime_seconds == 7200.0
        assert metrics.timestamp == ts


# ---------------------------------------------------------------------------
# MetricsCollector
# ---------------------------------------------------------------------------


class TestMetricsCollector:
    @pytest.fixture
    def temp_storage(self, tmp_path):
        return tmp_path / "metrics.json"

    @pytest.fixture
    def collector(self, temp_storage):
        return MetricsCollector(storage_path=temp_storage)

    def test_collector_initialization(self, collector, temp_storage):
        assert collector.storage_path == temp_storage
        assert collector._rate_limit == 5.0
        assert collector._history == []

    def test_save_and_load_history(self, temp_storage):
        collector = MetricsCollector(storage_path=temp_storage)
        metrics = FleetMetricsSnapshot(
            total_agents=3,
            active_agents=2,
            idle_agents=1,
        )
        collector._history.append(metrics)
        collector._save_history()

        new_collector = MetricsCollector(storage_path=temp_storage)
        assert len(new_collector._history) == 1
        assert new_collector._history[0].total_agents == 3
        assert new_collector._history[0].active_agents == 2

    def test_collect_rate_limiting(self, collector):
        # First collect sets the timestamp.
        metrics1 = collector.collect()
        # Immediate second collect is within rate limit — returns same item.
        metrics2 = collector.collect()
        assert metrics1.timestamp == metrics2.timestamp

    def test_collect_bypasses_rate_limit_after_timeout(self, temp_storage):
        """After the rate-limit window, collect() fetches fresh data."""
        collector = MetricsCollector(storage_path=temp_storage)
        # Force the last-collect time far in the past so rate limit is bypassed.
        collector._last_collect = 0.0
        with patch("forge_harness.fleet.dashboard.get_fleet_status") as mock_status:
            mock_status.side_effect = RuntimeError("fleet unavailable")
            metrics = collector.collect()
        assert isinstance(metrics, FleetMetricsSnapshot)

    def test_get_history_filtering(self, collector):
        old_metrics = FleetMetricsSnapshot(total_agents=1)
        old_metrics.timestamp = datetime.fromtimestamp(time.time() - 7200)

        recent_metrics = FleetMetricsSnapshot(total_agents=2)

        collector._history = [old_metrics, recent_metrics]

        recent = collector.get_history(minutes=60)
        assert len(recent) == 1
        assert recent[0].total_agents == 2

    def test_get_history_all_recent(self, collector):
        for i in range(5):
            collector._history.append(FleetMetricsSnapshot(total_agents=i))
        result = collector.get_history(minutes=60)
        assert len(result) == 5

    def test_get_history_all_old(self, collector):
        old = FleetMetricsSnapshot(total_agents=99)
        old.timestamp = datetime.fromtimestamp(time.time() - 10000)
        collector._history = [old]
        result = collector.get_history(minutes=1)
        assert result == []

    def test_export_prometheus_empty(self, collector):
        output = collector.export_prometheus()
        assert "forge_fleet_total_agents 0" in output
        assert "forge_fleet_active_agents 0" in output
        assert "forge_fleet_idle_agents 0" in output

    def test_export_prometheus_with_data(self, collector):
        metrics = FleetMetricsSnapshot(
            total_agents=5,
            active_agents=3,
            idle_agents=2,
            error_agents=1,
            avg_context_usage=75.5,
        )
        collector._history.append(metrics)

        output = collector.export_prometheus()
        assert "forge_fleet_total_agents 5" in output
        assert "forge_fleet_active_agents 3" in output
        assert "forge_fleet_idle_agents 2" in output
        assert "forge_fleet_error_agents 1" in output
        assert "forge_fleet_avg_context_usage 75.50" in output

    def test_export_prometheus_uses_last_entry(self, collector):
        collector._history.append(FleetMetricsSnapshot(total_agents=1))
        collector._history.append(FleetMetricsSnapshot(total_agents=2))
        collector._history.append(FleetMetricsSnapshot(total_agents=3))
        output = collector.export_prometheus()
        assert "forge_fleet_total_agents 3" in output

    def test_history_size_limit(self, temp_storage):
        collector = MetricsCollector(storage_path=temp_storage)
        for i in range(150):
            collector._history.append(FleetMetricsSnapshot(total_agents=i))
        collector._save_history()

        new_collector = MetricsCollector(storage_path=temp_storage)
        assert len(new_collector._history) == 100
        assert new_collector._history[0].total_agents == 50

    def test_collect_handles_missing_dashboard(self, temp_storage):
        """collect() should return a FleetMetricsSnapshot even when
        get_fleet_status raises an exception."""
        collector = MetricsCollector(storage_path=temp_storage)
        collector._last_collect = 0.0  # bypass rate limit
        with patch(
            "forge_harness.fleet.dashboard.get_fleet_status",
            side_effect=ImportError("no dashboard"),
        ):
            metrics = collector.collect()
        assert isinstance(metrics, FleetMetricsSnapshot)

    def test_load_history_handles_invalid_json(self, temp_storage):
        temp_storage.parent.mkdir(parents=True, exist_ok=True)
        temp_storage.write_text("invalid json {")
        collector = MetricsCollector(storage_path=temp_storage)
        assert collector._history == []

    def test_load_history_handles_missing_file(self, temp_storage):
        assert not temp_storage.exists()
        collector = MetricsCollector(storage_path=temp_storage)
        assert collector._history == []

    def test_load_history_handles_partial_data(self, temp_storage):
        """Partial/malformed history items should be skipped gracefully."""
        data = {
            "history": [
                {
                    "total_agents": 5,
                    "active_agents": 2,
                    "idle_agents": 1,
                    "error_agents": 0,
                    "avg_context_usage": 50.0,
                    "total_tasks_completed": 10,
                    "uptime_seconds": 100.0,
                },
                # Missing all optional keys — should still load with defaults
                {},
            ]
        }
        temp_storage.write_text(json.dumps(data))
        collector = MetricsCollector(storage_path=temp_storage)
        assert len(collector._history) == 2
        assert collector._history[0].total_agents == 5

    def test_collect_with_live_fleet_data(self, temp_storage):
        """collect() correctly maps fleet status fields when dashboard works."""
        collector = MetricsCollector(storage_path=temp_storage)
        collector._last_collect = 0.0

        # Build a minimal mock status object
        mock_agent_active = MagicMock()
        mock_agent_active.status = "active"
        mock_agent_active.context_estimate = 40

        mock_agent_idle = MagicMock()
        mock_agent_idle.status = "idle"
        mock_agent_idle.context_estimate = 60

        mock_status = MagicMock()
        mock_status.agents = [mock_agent_active, mock_agent_idle]

        with patch(
            "forge_harness.fleet.dashboard.get_fleet_status",
            return_value=mock_status,
        ):
            metrics = collector.collect()

        assert metrics.total_agents == 2
        assert metrics.active_agents == 1
        assert metrics.idle_agents == 1
        assert metrics.error_agents == 0
        assert metrics.avg_context_usage == 50.0

    def test_save_history_creates_parent_directories(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "c" / "metrics.json"
        collector = MetricsCollector(storage_path=deep_path)
        collector._history.append(FleetMetricsSnapshot(total_agents=7))
        collector._save_history()
        assert deep_path.exists()
        saved = json.loads(deep_path.read_text())
        assert saved["history"][0]["total_agents"] == 7

    def test_collect_rate_limit_returns_last_history(self, collector):
        """Within the rate-limit window, the cached entry from history is returned."""
        sentinel = FleetMetricsSnapshot(total_agents=42)
        collector._history.append(sentinel)
        collector._last_collect = time.time()  # just collected
        result = collector.collect()
        assert result.total_agents == 42

    def test_collect_rate_limit_empty_history_returns_default(self, collector):
        """Rate-limited collect with no history returns a default snapshot."""
        collector._last_collect = time.time()
        result = collector.collect()
        assert isinstance(result, FleetMetricsSnapshot)
        assert result.total_agents == 0


# ---------------------------------------------------------------------------
# create_metrics_collector
# ---------------------------------------------------------------------------


class TestCreateMetricsCollector:
    def test_create_with_default_path(self):
        collector = create_metrics_collector()
        assert collector.storage_path == Path(".forge/learning/fleet_metrics.json")

    def test_create_with_custom_path(self):
        custom_path = Path("/tmp/custom_metrics.json")
        collector = create_metrics_collector(storage_path=custom_path)
        assert collector.storage_path == custom_path

    def test_returns_metrics_collector_instance(self):
        collector = create_metrics_collector()
        assert isinstance(collector, MetricsCollector)


# ---------------------------------------------------------------------------
# record_dispatch_metrics
# ---------------------------------------------------------------------------


class TestRecordDispatchMetrics:
    def test_records_successful_dispatch(self):
        """record_dispatch_metrics must not raise."""
        record_dispatch_metrics(
            agent="test-agent",
            success=True,
            delivery_time_ms=15.3,
            retries=0,
        )

    def test_records_failed_dispatch(self):
        record_dispatch_metrics(
            agent="broken-agent",
            success=False,
            delivery_time_ms=250.0,
            retries=3,
        )

    def test_calls_logger_debug(self):
        with patch("forge_harness.fleet.metrics.logger") as mock_logger:
            record_dispatch_metrics(
                agent="agent-1",
                success=True,
                delivery_time_ms=10.0,
                retries=1,
            )
        mock_logger.debug.assert_called_once()
        call_args = mock_logger.debug.call_args[0][0]
        assert "agent-1" in call_args
        assert "True" in call_args

    def test_default_retries(self):
        """retries defaults to 0 without raising."""
        record_dispatch_metrics(agent="a", success=True, delivery_time_ms=1.0)


# ---------------------------------------------------------------------------
# MetricsReport
# ---------------------------------------------------------------------------


class TestMetricsReport:
    @pytest.fixture
    def sample_report(self):
        now = datetime.now(UTC)
        return MetricsReport(
            total_tasks=100,
            completed_tasks=80,
            failed_tasks=15,
            in_progress_tasks=5,
            success_rate=0.842,
            avg_duration_seconds=45.0,
            total_agents=10,
            period_start=now - timedelta(days=7),
            period_end=now,
            task_types={"feature": 50, "bugfix": 30, "refactor": 20},
        )

    def test_to_dict_keys(self, sample_report):
        d = sample_report.to_dict()
        expected_keys = {
            "total_tasks",
            "completed_tasks",
            "failed_tasks",
            "in_progress_tasks",
            "success_rate",
            "avg_duration_seconds",
            "total_agents",
            "period_start",
            "period_end",
            "task_types",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_values(self, sample_report):
        d = sample_report.to_dict()
        assert d["total_tasks"] == 100
        assert d["completed_tasks"] == 80
        assert d["failed_tasks"] == 15
        assert d["in_progress_tasks"] == 5
        assert d["success_rate"] == 0.842
        assert d["avg_duration_seconds"] == 45.0
        assert d["total_agents"] == 10
        assert d["task_types"] == {"feature": 50, "bugfix": 30, "refactor": 20}

    def test_to_dict_timestamps_are_iso_strings(self, sample_report):
        d = sample_report.to_dict()
        # Should be parseable ISO strings
        datetime.fromisoformat(d["period_start"])
        datetime.fromisoformat(d["period_end"])

    def test_empty_task_types(self):
        now = datetime.now(UTC)
        report = MetricsReport(
            total_tasks=0,
            completed_tasks=0,
            failed_tasks=0,
            in_progress_tasks=0,
            success_rate=0.0,
            avg_duration_seconds=0.0,
            total_agents=0,
            period_start=now,
            period_end=now,
        )
        d = report.to_dict()
        assert d["task_types"] == {}


# ---------------------------------------------------------------------------
# AgentMetrics
# ---------------------------------------------------------------------------


class TestAgentMetrics:
    @pytest.fixture
    def sample_agent_metrics(self):
        now = datetime.now(UTC)
        return AgentMetrics(
            agent_id="agent-1",
            total_tasks=20,
            completed_tasks=18,
            failed_tasks=2,
            success_rate=0.9,
            avg_duration_seconds=30.0,
            utilization=0.25,
            active_time_seconds=1800.0,
            total_time_seconds=7200.0,
            first_task=now - timedelta(hours=2),
            last_task=now,
        )

    def test_to_dict_keys(self, sample_agent_metrics):
        d = sample_agent_metrics.to_dict()
        expected_keys = {
            "agent_id",
            "total_tasks",
            "completed_tasks",
            "failed_tasks",
            "success_rate",
            "avg_duration_seconds",
            "utilization",
            "active_time_seconds",
            "total_time_seconds",
            "first_task",
            "last_task",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_values(self, sample_agent_metrics):
        d = sample_agent_metrics.to_dict()
        assert d["agent_id"] == "agent-1"
        assert d["total_tasks"] == 20
        assert d["success_rate"] == 0.9
        assert d["utilization"] == 0.25

    def test_to_dict_timestamps_are_iso_strings(self, sample_agent_metrics):
        d = sample_agent_metrics.to_dict()
        datetime.fromisoformat(d["first_task"])
        datetime.fromisoformat(d["last_task"])

    def test_to_dict_none_timestamps(self):
        metrics = AgentMetrics(
            agent_id="agent-2",
            total_tasks=0,
            completed_tasks=0,
            failed_tasks=0,
            success_rate=0.0,
            avg_duration_seconds=0.0,
            utilization=0.0,
            active_time_seconds=0.0,
            total_time_seconds=86400.0,
            first_task=None,
            last_task=None,
        )
        d = metrics.to_dict()
        assert d["first_task"] is None
        assert d["last_task"] is None


# ---------------------------------------------------------------------------
# TaskTypeMetrics
# ---------------------------------------------------------------------------


class TestTaskTypeMetrics:
    @pytest.fixture
    def sample_task_type_metrics(self):
        return TaskTypeMetrics(
            task_type="feature",
            total_tasks=50,
            completed_tasks=45,
            failed_tasks=5,
            success_rate=0.9,
            avg_duration_seconds=120.0,
        )

    def test_to_dict_keys(self, sample_task_type_metrics):
        d = sample_task_type_metrics.to_dict()
        expected_keys = {
            "task_type",
            "total_tasks",
            "completed_tasks",
            "failed_tasks",
            "success_rate",
            "avg_duration_seconds",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_values(self, sample_task_type_metrics):
        d = sample_task_type_metrics.to_dict()
        assert d["task_type"] == "feature"
        assert d["total_tasks"] == 50
        assert d["completed_tasks"] == 45
        assert d["failed_tasks"] == 5
        assert d["success_rate"] == 0.9
        assert d["avg_duration_seconds"] == 120.0


# ---------------------------------------------------------------------------
# FleetMetrics (SQLite-backed)
# ---------------------------------------------------------------------------


@pytest.fixture
def fleet_metrics(tmp_path):
    """FleetMetrics backed by an isolated in-memory-like SQLite file."""
    db_path = tmp_path / "test_metrics.db"
    return FleetMetrics(db_path=db_path)


class TestFleetMetricsInit:
    def test_creates_database_file(self, tmp_path):
        db_path = tmp_path / "test.db"
        assert not db_path.exists()
        FleetMetrics(db_path=db_path)
        assert db_path.exists()

    def test_default_db_path(self, tmp_path):
        """When no db_path given, uses .forge_dashboard.db in cwd."""
        with patch("forge_harness.fleet.metrics.Path.cwd", return_value=tmp_path):
            fm = FleetMetrics()
        assert fm.db_path == tmp_path / ".forge_dashboard.db"

    def test_table_name_constant(self):
        assert FleetMetrics.TABLE_NAME == "dashboard_task_events"

    def test_schema_created(self, fleet_metrics):
        """Confirm the expected table and indexes exist."""
        import sqlite3

        with sqlite3.connect(str(fleet_metrics.db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='dashboard_task_events'"
            )
            assert cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# FleetMetrics.record_task_start
# ---------------------------------------------------------------------------


class TestFleetMetricsRecordTaskStart:
    def test_basic_success(self, fleet_metrics):
        result = fleet_metrics.record_task_start("agent-1", "task-001")
        assert result is True

    def test_returns_false_on_duplicate(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "task-dup")
        result = fleet_metrics.record_task_start("agent-1", "task-dup")
        assert result is False

    def test_with_task_type(self, fleet_metrics):
        result = fleet_metrics.record_task_start(
            "agent-1", "task-typed", task_type="feature"
        )
        assert result is True

    def test_with_metadata(self, fleet_metrics):
        meta = {"priority": "high", "project": "voice-coach"}
        result = fleet_metrics.record_task_start(
            "agent-1", "task-meta", metadata=meta
        )
        assert result is True

    def test_with_all_parameters(self, fleet_metrics):
        result = fleet_metrics.record_task_start(
            agent_id="agent-2",
            task_id="task-full",
            task_type="bugfix",
            metadata={"key": "value"},
        )
        assert result is True

    def test_returns_false_on_db_error(self, fleet_metrics):
        """Returns False when the database operation fails."""
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("db error")
            result = fleet_metrics.record_task_start("a", "t")
        assert result is False

    def test_multiple_agents_same_task_type(self, fleet_metrics):
        for i in range(5):
            fleet_metrics.record_task_start(
                f"agent-{i}", f"task-{i}", task_type="feature"
            )
        agents = fleet_metrics.list_active_agents()
        assert len(agents) == 5


# ---------------------------------------------------------------------------
# FleetMetrics.record_task_end
# ---------------------------------------------------------------------------


class TestFleetMetricsRecordTaskEnd:
    def test_basic_success(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "task-001")
        result = fleet_metrics.record_task_end("task-001", outcome="success")
        assert result is True

    def test_returns_false_for_missing_task(self, fleet_metrics):
        result = fleet_metrics.record_task_end("nonexistent-task", outcome="success")
        assert result is False

    def test_outcome_failure(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "task-fail")
        result = fleet_metrics.record_task_end("task-fail", outcome="failure")
        assert result is True

    def test_outcome_cancelled(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "task-cancel")
        result = fleet_metrics.record_task_end("task-cancel", outcome="cancelled")
        assert result is True

    def test_with_end_metadata(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "task-meta-end")
        result = fleet_metrics.record_task_end(
            "task-meta-end",
            outcome="success",
            metadata={"result": "all tests pass"},
        )
        assert result is True

    def test_merges_metadata(self, fleet_metrics):
        """End metadata should merge with start metadata."""
        fleet_metrics.record_task_start(
            "agent-1", "task-merge", metadata={"start_key": "start_val"}
        )
        result = fleet_metrics.record_task_end(
            "task-merge",
            outcome="success",
            metadata={"end_key": "end_val"},
        )
        assert result is True

    def test_end_without_start_metadata(self, fleet_metrics):
        """End metadata when no start metadata was recorded."""
        fleet_metrics.record_task_start("agent-1", "task-no-start-meta")
        result = fleet_metrics.record_task_end(
            "task-no-start-meta",
            outcome="success",
            metadata={"only_end": True},
        )
        assert result is True

    def test_returns_false_on_db_error(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "task-err-end")
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("db error")
            result = fleet_metrics.record_task_end("task-err-end", outcome="success")
        assert result is False

    def test_duration_is_positive(self, fleet_metrics):
        """Duration recorded for a completed task should be >= 0."""
        import sqlite3

        fleet_metrics.record_task_start("agent-1", "task-duration")
        fleet_metrics.record_task_end("task-duration", outcome="success")

        with sqlite3.connect(str(fleet_metrics.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT duration_seconds FROM dashboard_task_events WHERE task_id = ?",
                ("task-duration",),
            )
            row = cursor.fetchone()
        assert row["duration_seconds"] >= 0.0


# ---------------------------------------------------------------------------
# FleetMetrics.get_metrics
# ---------------------------------------------------------------------------


class TestFleetMetricsGetMetrics:
    def _seed(self, fleet_metrics, n_success=3, n_failure=1, n_progress=1):
        """Insert a mix of task records into the DB."""
        for i in range(n_success):
            fleet_metrics.record_task_start(
                f"agent-{i % 3}", f"task-s-{i}", task_type="feature"
            )
            fleet_metrics.record_task_end(f"task-s-{i}", outcome="success")

        for i in range(n_failure):
            fleet_metrics.record_task_start(
                f"agent-{i % 3}", f"task-f-{i}", task_type="bugfix"
            )
            fleet_metrics.record_task_end(f"task-f-{i}", outcome="failure")

        for i in range(n_progress):
            fleet_metrics.record_task_start(
                "agent-0", f"task-p-{i}", task_type="refactor"
            )
            # No end recorded — in-progress

    def test_returns_metrics_report(self, fleet_metrics):
        self._seed(fleet_metrics)
        report = fleet_metrics.get_metrics(days=7)
        assert isinstance(report, MetricsReport)

    def test_total_tasks_count(self, fleet_metrics):
        self._seed(fleet_metrics, n_success=3, n_failure=1, n_progress=1)
        report = fleet_metrics.get_metrics(days=7)
        assert report.total_tasks == 5

    def test_completed_tasks_count(self, fleet_metrics):
        self._seed(fleet_metrics, n_success=4, n_failure=0, n_progress=0)
        report = fleet_metrics.get_metrics(days=7)
        assert report.completed_tasks == 4

    def test_failed_tasks_count(self, fleet_metrics):
        self._seed(fleet_metrics, n_success=0, n_failure=3, n_progress=0)
        report = fleet_metrics.get_metrics(days=7)
        assert report.failed_tasks == 3

    def test_in_progress_tasks_count(self, fleet_metrics):
        self._seed(fleet_metrics, n_success=0, n_failure=0, n_progress=2)
        report = fleet_metrics.get_metrics(days=7)
        assert report.in_progress_tasks == 2

    def test_success_rate_calculation(self, fleet_metrics):
        self._seed(fleet_metrics, n_success=3, n_failure=1, n_progress=0)
        report = fleet_metrics.get_metrics(days=7)
        assert report.success_rate == pytest.approx(0.75)

    def test_success_rate_zero_when_no_completed_or_failed(self, fleet_metrics):
        self._seed(fleet_metrics, n_success=0, n_failure=0, n_progress=2)
        report = fleet_metrics.get_metrics(days=7)
        assert report.success_rate == 0.0

    def test_task_types_breakdown(self, fleet_metrics):
        self._seed(fleet_metrics, n_success=2, n_failure=1, n_progress=1)
        report = fleet_metrics.get_metrics(days=7)
        # feature (from success) + bugfix (from failure) + refactor (from progress)
        assert "feature" in report.task_types
        assert "bugfix" in report.task_types
        assert "refactor" in report.task_types

    def test_total_agents_count(self, fleet_metrics):
        self._seed(fleet_metrics, n_success=3, n_failure=0, n_progress=0)
        report = fleet_metrics.get_metrics(days=7)
        assert report.total_agents >= 1

    def test_empty_database(self, fleet_metrics):
        report = fleet_metrics.get_metrics(days=7)
        assert report.total_tasks == 0
        assert report.success_rate == 0.0

    def test_period_boundaries(self, fleet_metrics):
        report = fleet_metrics.get_metrics(days=7)
        assert report.period_end > report.period_start

    def test_returns_empty_report_on_db_error(self, fleet_metrics):
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("db error")
            report = fleet_metrics.get_metrics(days=7)
        assert report.total_tasks == 0

    def test_custom_days_parameter(self, fleet_metrics):
        self._seed(fleet_metrics, n_success=2)
        report_7 = fleet_metrics.get_metrics(days=7)
        report_30 = fleet_metrics.get_metrics(days=30)
        # Both should include the recent tasks
        assert report_7.total_tasks == report_30.total_tasks


# ---------------------------------------------------------------------------
# FleetMetrics.get_agent_metrics
# ---------------------------------------------------------------------------


class TestFleetMetricsGetAgentMetrics:
    def test_returns_agent_metrics(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "task-a")
        fleet_metrics.record_task_end("task-a", outcome="success")
        result = fleet_metrics.get_agent_metrics("agent-1")
        assert isinstance(result, AgentMetrics)

    def test_total_tasks_for_agent(self, fleet_metrics):
        for i in range(5):
            fleet_metrics.record_task_start("agent-1", f"task-a{i}")
            fleet_metrics.record_task_end(f"task-a{i}", outcome="success")
        result = fleet_metrics.get_agent_metrics("agent-1")
        assert result.total_tasks == 5

    def test_completed_and_failed_counts(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "task-ok")
        fleet_metrics.record_task_end("task-ok", outcome="success")
        fleet_metrics.record_task_start("agent-1", "task-bad")
        fleet_metrics.record_task_end("task-bad", outcome="failure")
        result = fleet_metrics.get_agent_metrics("agent-1")
        assert result.completed_tasks == 1
        assert result.failed_tasks == 1

    def test_success_rate(self, fleet_metrics):
        for i in range(3):
            fleet_metrics.record_task_start("agent-1", f"task-ok-{i}")
            fleet_metrics.record_task_end(f"task-ok-{i}", outcome="success")
        fleet_metrics.record_task_start("agent-1", "task-fail")
        fleet_metrics.record_task_end("task-fail", outcome="failure")
        result = fleet_metrics.get_agent_metrics("agent-1")
        assert result.success_rate == pytest.approx(0.75)

    def test_success_rate_zero_for_empty(self, fleet_metrics):
        result = fleet_metrics.get_agent_metrics("unknown-agent")
        assert result.success_rate == 0.0

    def test_utilization_computed(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "task-u")
        fleet_metrics.record_task_end("task-u", outcome="success")
        result = fleet_metrics.get_agent_metrics("agent-1")
        assert 0.0 <= result.utilization <= 1.0

    def test_first_and_last_task_timestamps(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "task-first")
        fleet_metrics.record_task_end("task-first", outcome="success")
        fleet_metrics.record_task_start("agent-1", "task-last")
        fleet_metrics.record_task_end("task-last", outcome="success")
        result = fleet_metrics.get_agent_metrics("agent-1")
        assert result.first_task is not None
        assert result.last_task is not None

    def test_none_timestamps_for_no_tasks(self, fleet_metrics):
        result = fleet_metrics.get_agent_metrics("ghost-agent")
        assert result.first_task is None
        assert result.last_task is None

    def test_returns_empty_metrics_on_db_error(self, fleet_metrics):
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("db error")
            result = fleet_metrics.get_agent_metrics("agent-1")
        assert result.total_tasks == 0

    def test_isolates_agent_data(self, fleet_metrics):
        for i in range(3):
            fleet_metrics.record_task_start("agent-A", f"task-A{i}")
            fleet_metrics.record_task_end(f"task-A{i}", outcome="success")
        for i in range(5):
            fleet_metrics.record_task_start("agent-B", f"task-B{i}")
            fleet_metrics.record_task_end(f"task-B{i}", outcome="success")
        result_a = fleet_metrics.get_agent_metrics("agent-A")
        result_b = fleet_metrics.get_agent_metrics("agent-B")
        assert result_a.total_tasks == 3
        assert result_b.total_tasks == 5


# ---------------------------------------------------------------------------
# FleetMetrics.get_task_type_metrics
# ---------------------------------------------------------------------------


class TestFleetMetricsGetTaskTypeMetrics:
    def _seed_type(self, fleet_metrics, task_type, n_success=2, n_failure=1):
        for i in range(n_success):
            fleet_metrics.record_task_start("agent-1", f"task-{task_type}-s{i}", task_type=task_type)
            fleet_metrics.record_task_end(f"task-{task_type}-s{i}", outcome="success")
        for i in range(n_failure):
            fleet_metrics.record_task_start("agent-1", f"task-{task_type}-f{i}", task_type=task_type)
            fleet_metrics.record_task_end(f"task-{task_type}-f{i}", outcome="failure")

    def test_returns_task_type_metrics(self, fleet_metrics):
        self._seed_type(fleet_metrics, "feature")
        result = fleet_metrics.get_task_type_metrics("feature")
        assert isinstance(result, TaskTypeMetrics)

    def test_total_tasks(self, fleet_metrics):
        self._seed_type(fleet_metrics, "bugfix", n_success=2, n_failure=1)
        result = fleet_metrics.get_task_type_metrics("bugfix")
        assert result.total_tasks == 3

    def test_success_rate(self, fleet_metrics):
        self._seed_type(fleet_metrics, "refactor", n_success=3, n_failure=1)
        result = fleet_metrics.get_task_type_metrics("refactor")
        assert result.success_rate == pytest.approx(0.75)

    def test_unknown_task_type_returns_zeros(self, fleet_metrics):
        result = fleet_metrics.get_task_type_metrics("nonexistent")
        assert result.total_tasks == 0
        assert result.success_rate == 0.0

    def test_returns_empty_on_db_error(self, fleet_metrics):
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("db error")
            result = fleet_metrics.get_task_type_metrics("feature")
        assert result.total_tasks == 0

    def test_task_type_field_preserved(self, fleet_metrics):
        self._seed_type(fleet_metrics, "my-special-type", n_success=1, n_failure=0)
        result = fleet_metrics.get_task_type_metrics("my-special-type")
        assert result.task_type == "my-special-type"

    def test_zero_success_rate_when_no_completed_failed(self, fleet_metrics):
        # In-progress only (no outcome)
        fleet_metrics.record_task_start("agent-1", "task-prog", task_type="mytype")
        result = fleet_metrics.get_task_type_metrics("mytype")
        assert result.success_rate == 0.0


# ---------------------------------------------------------------------------
# FleetMetrics.list_active_agents
# ---------------------------------------------------------------------------


class TestFleetMetricsListActiveAgents:
    def test_returns_list(self, fleet_metrics):
        result = fleet_metrics.list_active_agents()
        assert isinstance(result, list)

    def test_empty_when_no_tasks(self, fleet_metrics):
        result = fleet_metrics.list_active_agents()
        assert result == []

    def test_lists_distinct_agents(self, fleet_metrics):
        for i in range(5):
            fleet_metrics.record_task_start("agent-A", f"task-A{i}")
        for i in range(3):
            fleet_metrics.record_task_start("agent-B", f"task-B{i}")
        result = fleet_metrics.list_active_agents()
        assert set(result) == {"agent-A", "agent-B"}

    def test_sorted_alphabetically(self, fleet_metrics):
        fleet_metrics.record_task_start("charlie", "t1")
        fleet_metrics.record_task_start("alice", "t2")
        fleet_metrics.record_task_start("bob", "t3")
        result = fleet_metrics.list_active_agents()
        assert result == sorted(result)

    def test_returns_empty_on_db_error(self, fleet_metrics):
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("db error")
            result = fleet_metrics.list_active_agents()
        assert result == []

    def test_days_parameter_filters_old_records(self, fleet_metrics, tmp_path):
        """Tasks older than the window should not appear in the list."""
        import sqlite3
        from datetime import UTC, datetime, timedelta

        db_path = tmp_path / "aged_metrics.db"
        fm = FleetMetrics(db_path=db_path)
        # Insert a task with a very old started_at
        old_ts = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO dashboard_task_events (task_id, agent_id, started_at) VALUES (?, ?, ?)",
                ("old-task", "old-agent", old_ts),
            )
        result = fm.list_active_agents(days=7)
        assert "old-agent" not in result


# ---------------------------------------------------------------------------
# FleetMetrics.list_task_types
# ---------------------------------------------------------------------------


class TestFleetMetricsListTaskTypes:
    def test_returns_list(self, fleet_metrics):
        result = fleet_metrics.list_task_types()
        assert isinstance(result, list)

    def test_empty_when_no_tasks(self, fleet_metrics):
        result = fleet_metrics.list_task_types()
        assert result == []

    def test_lists_distinct_types(self, fleet_metrics):
        for t in ("feature", "bugfix", "feature", "refactor"):
            fleet_metrics.record_task_start("agent-1", f"task-{t}-{id(t)}", task_type=t)
        result = fleet_metrics.list_task_types()
        assert set(result) == {"feature", "bugfix", "refactor"}

    def test_excludes_null_task_types(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "task-no-type")  # no task_type
        result = fleet_metrics.list_task_types()
        assert result == []

    def test_sorted_alphabetically(self, fleet_metrics):
        for t in ("zebra", "apple", "mango"):
            fleet_metrics.record_task_start("agent-1", f"task-{t}", task_type=t)
        result = fleet_metrics.list_task_types()
        assert result == sorted(result)

    def test_returns_empty_on_db_error(self, fleet_metrics):
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("db error")
            result = fleet_metrics.list_task_types()
        assert result == []


# ---------------------------------------------------------------------------
# FleetMetrics.cleanup_old_metrics
# ---------------------------------------------------------------------------


class TestFleetMetricsCleanupOldMetrics:
    def test_returns_zero_when_nothing_to_delete(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "recent-task")
        deleted = fleet_metrics.cleanup_old_metrics(days=90)
        assert deleted == 0

    def test_deletes_old_records(self, fleet_metrics, tmp_path):
        import sqlite3
        from datetime import UTC, datetime, timedelta

        db_path = tmp_path / "cleanup_test.db"
        fm = FleetMetrics(db_path=db_path)

        # Insert an artificially old record
        old_ts = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO dashboard_task_events (task_id, agent_id, started_at) VALUES (?, ?, ?)",
                ("old-task", "agent-1", old_ts),
            )

        # Keep recent data, delete anything > 90 days
        deleted = fm.cleanup_old_metrics(days=90)
        assert deleted == 1

    def test_keeps_recent_records(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "keep-me")
        fleet_metrics.cleanup_old_metrics(days=90)
        # Recent record should still be there
        agents = fleet_metrics.list_active_agents()
        assert "agent-1" in agents

    def test_returns_zero_on_db_error(self, fleet_metrics):
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("db error")
            result = fleet_metrics.cleanup_old_metrics(days=90)
        assert result == 0

    def test_deletes_only_records_outside_window(self, fleet_metrics, tmp_path):
        import sqlite3
        from datetime import UTC, datetime, timedelta

        db_path = tmp_path / "window_test.db"
        fm = FleetMetrics(db_path=db_path)

        now = datetime.now(UTC)
        old_ts = (now - timedelta(days=200)).isoformat()
        # Use a record from 1 day ago so it's within the default 7-day window
        recent_ts = (now - timedelta(days=1)).isoformat()

        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO dashboard_task_events (task_id, agent_id, started_at) VALUES (?, ?, ?)",
                ("old-one", "agent-old", old_ts),
            )
            conn.execute(
                "INSERT INTO dashboard_task_events (task_id, agent_id, started_at) VALUES (?, ?, ?)",
                ("recent-one", "agent-recent", recent_ts),
            )

        deleted = fm.cleanup_old_metrics(days=90)
        assert deleted == 1
        remaining = fm.list_active_agents()
        assert "agent-recent" in remaining
        assert "agent-old" not in remaining


# ---------------------------------------------------------------------------
# create_fleet_metrics factory
# ---------------------------------------------------------------------------


class TestCreateFleetMetrics:
    def test_returns_fleet_metrics_instance(self, tmp_path):
        fm = create_fleet_metrics(db_path=tmp_path / "test.db")
        assert isinstance(fm, FleetMetrics)

    def test_default_path(self, tmp_path):
        with patch("forge_harness.fleet.metrics.Path.cwd", return_value=tmp_path):
            fm = create_fleet_metrics()
        assert fm.db_path == tmp_path / ".forge_dashboard.db"

    def test_custom_path(self, tmp_path):
        custom = tmp_path / "custom.db"
        fm = create_fleet_metrics(db_path=custom)
        assert fm.db_path == custom


# ---------------------------------------------------------------------------
# FleetMetrics._get_connection (context manager behavior)
# ---------------------------------------------------------------------------


class TestFleetMetricsConnectionManager:
    def test_commits_on_success(self, fleet_metrics):
        """Verify the context manager commits successfully."""
        result = fleet_metrics.record_task_start("agent-cm", "task-cm")
        assert result is True
        # Confirm it's readable after commit
        agents = fleet_metrics.list_active_agents()
        assert "agent-cm" in agents

    def test_rollback_on_exception(self, fleet_metrics):
        """Verify the context manager rolls back on exception."""
        import sqlite3

        with pytest.raises(Exception):
            with fleet_metrics._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO dashboard_task_events (task_id, agent_id, started_at) VALUES (?, ?, ?)",
                    ("rollback-task", "agent-rb", datetime.now(UTC).isoformat()),
                )
                raise RuntimeError("force rollback")

        # The rolled-back task should not be present
        agents = fleet_metrics.list_active_agents()
        assert "agent-rb" not in agents


# ---------------------------------------------------------------------------
# Integration: full task lifecycle
# ---------------------------------------------------------------------------


class TestFleetMetricsIntegration:
    def test_full_task_lifecycle(self, fleet_metrics):
        fleet_metrics.record_task_start(
            "agent-1", "lifecycle-task", task_type="feature", metadata={"version": 1}
        )
        fleet_metrics.record_task_end(
            "lifecycle-task", outcome="success", metadata={"result": "ok"}
        )

        report = fleet_metrics.get_metrics(days=7)
        assert report.total_tasks == 1
        assert report.completed_tasks == 1
        assert report.success_rate == 1.0

        agent_report = fleet_metrics.get_agent_metrics("agent-1")
        assert agent_report.total_tasks == 1
        assert agent_report.completed_tasks == 1

        type_report = fleet_metrics.get_task_type_metrics("feature")
        assert type_report.total_tasks == 1

    def test_mixed_outcomes(self, fleet_metrics):
        for i, outcome in enumerate(("success", "success", "failure", "cancelled")):
            fleet_metrics.record_task_start("agent-1", f"mixed-{i}", task_type="work")
            if outcome != "cancelled":
                fleet_metrics.record_task_end(f"mixed-{i}", outcome=outcome)
            # "cancelled" left in-progress for this test

        report = fleet_metrics.get_metrics(days=7)
        assert report.total_tasks == 4

    def test_report_to_dict_roundtrip(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "rt-task", task_type="feature")
        fleet_metrics.record_task_end("rt-task", outcome="success")

        report = fleet_metrics.get_metrics(days=7)
        d = report.to_dict()
        assert d["total_tasks"] == 1
        assert d["success_rate"] == 1.0
        assert "feature" in d["task_types"]

    def test_agent_metrics_to_dict_roundtrip(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "am-task")
        fleet_metrics.record_task_end("am-task", outcome="success")

        agent_metrics = fleet_metrics.get_agent_metrics("agent-1")
        d = agent_metrics.to_dict()
        assert d["agent_id"] == "agent-1"
        assert d["total_tasks"] == 1

    def test_task_type_metrics_to_dict_roundtrip(self, fleet_metrics):
        fleet_metrics.record_task_start("agent-1", "ttm-task", task_type="bugfix")
        fleet_metrics.record_task_end("ttm-task", outcome="failure")

        ttm = fleet_metrics.get_task_type_metrics("bugfix")
        d = ttm.to_dict()
        assert d["task_type"] == "bugfix"
        assert d["failed_tasks"] == 1
