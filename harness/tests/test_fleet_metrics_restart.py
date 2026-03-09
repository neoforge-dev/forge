"""Combined tests for fleet metrics and restart modules.

Covers:
- forge_harness.fleet.metrics (FleetMetrics, MetricsCollector, dataclasses, factories)
- forge_harness.fleet.restart (AgentRestarter, dataclasses, factories)

All tests are self-contained with no dependency on other fleet test files.
Both modules are targeted for 80%+ line coverage from this file alone.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Suppress the module-level DeprecationWarning emitted by fleet.metrics
# ---------------------------------------------------------------------------
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

try:
    from forge_harness.fleet.restart import (
        AgentRestarter,
        HealthStatus,
        RestartEvent,
        TaskState,
        create_agent_restarter,
    )
    _RESTART_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _RESTART_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _METRICS_AVAILABLE,
    reason="forge_harness.fleet.metrics module not available — needs rewrite for current fleet structure",
)


# ===========================================================================
# Shared fixtures
# ===========================================================================


@pytest.fixture
def tmp_forge_root(tmp_path: Path) -> Path:
    """Temporary directory with .forge/fleet subdirectory."""
    (tmp_path / ".forge/fleet").mkdir()
    return tmp_path


@pytest.fixture
def restarter(tmp_forge_root: Path) -> AgentRestarter:
    return AgentRestarter(forge_root=tmp_forge_root, stale_threshold_minutes=30)


@pytest.fixture
def fleet_metrics(tmp_path: Path) -> FleetMetrics:
    db_path = tmp_path / "test_metrics.db"
    return FleetMetrics(db_path=db_path)


def _make_session(name: str, seconds_ago: int = 0) -> dict:
    ts = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    return {"name": name, "created": ts, "last_activity": ts}


# ===========================================================================
# SECTION 1 — fleet.metrics dataclasses
# ===========================================================================


class TestAgentMetricsSnapshot:
    """AgentMetricsSnapshot dataclass — field defaults and values."""

    def test_required_fields_stored(self) -> None:
        m = AgentMetricsSnapshot(agent_id="agent-1", status="active")
        assert m.agent_id == "agent-1"
        assert m.status == "active"

    def test_default_context_percentage(self) -> None:
        m = AgentMetricsSnapshot(agent_id="a", status="idle")
        assert m.context_percentage == 100

    def test_default_numeric_fields(self) -> None:
        m = AgentMetricsSnapshot(agent_id="a", status="idle")
        assert m.tasks_completed == 0
        assert m.errors == 0
        assert m.uptime == 0.0

    def test_default_last_activity_is_none(self) -> None:
        m = AgentMetricsSnapshot(agent_id="a", status="idle")
        assert m.last_activity is None

    def test_all_fields_set(self) -> None:
        ts = datetime.now(UTC)
        m = AgentMetricsSnapshot(
            agent_id="x",
            status="error",
            context_percentage=50,
            tasks_completed=7,
            errors=2,
            last_activity=ts,
            uptime=1800.0,
        )
        assert m.context_percentage == 50
        assert m.tasks_completed == 7
        assert m.errors == 2
        assert m.last_activity == ts
        assert m.uptime == 1800.0


class TestFleetMetricsSnapshot:
    """FleetMetricsSnapshot dataclass — field defaults and timestamp."""

    def test_default_all_zeros(self) -> None:
        m = FleetMetricsSnapshot()
        assert m.total_agents == 0
        assert m.active_agents == 0
        assert m.idle_agents == 0
        assert m.error_agents == 0
        assert m.avg_context_usage == 0.0
        assert m.total_tasks_completed == 0
        assert m.uptime_seconds == 0.0

    def test_timestamp_is_recent(self) -> None:
        before = datetime.now()
        m = FleetMetricsSnapshot()
        after = datetime.now()
        assert before <= m.timestamp <= after

    def test_explicit_values(self) -> None:
        ts = datetime(2026, 2, 1, 12, 0, 0)
        m = FleetMetricsSnapshot(
            total_agents=5,
            active_agents=3,
            idle_agents=1,
            error_agents=1,
            avg_context_usage=60.0,
            total_tasks_completed=42,
            uptime_seconds=3600.0,
            timestamp=ts,
        )
        assert m.total_agents == 5
        assert m.active_agents == 3
        assert m.error_agents == 1
        assert m.avg_context_usage == 60.0
        assert m.total_tasks_completed == 42
        assert m.uptime_seconds == 3600.0
        assert m.timestamp == ts


# ===========================================================================
# SECTION 2 — MetricsCollector
# ===========================================================================


class TestMetricsCollector:
    """MetricsCollector — init, collect, history, persistence, prometheus."""

    @pytest.fixture
    def storage(self, tmp_path: Path) -> Path:
        return tmp_path / "metrics.json"

    @pytest.fixture
    def collector(self, storage: Path) -> MetricsCollector:
        return MetricsCollector(storage_path=storage)

    # --- init ---

    def test_default_storage_path(self) -> None:
        c = MetricsCollector()
        assert c.storage_path == Path(".forge/learning/fleet_metrics.json")

    def test_custom_storage_path(self, storage: Path) -> None:
        c = MetricsCollector(storage_path=storage)
        assert c.storage_path == storage

    def test_initial_history_empty(self, collector: MetricsCollector) -> None:
        assert collector._history == []

    def test_rate_limit_default(self, collector: MetricsCollector) -> None:
        assert collector._rate_limit == 5.0

    # --- persistence ---

    def test_save_and_reload_history(self, storage: Path) -> None:
        c = MetricsCollector(storage_path=storage)
        c._history.append(FleetMetricsSnapshot(total_agents=4, active_agents=2))
        c._save_history()

        c2 = MetricsCollector(storage_path=storage)
        assert len(c2._history) == 1
        assert c2._history[0].total_agents == 4
        assert c2._history[0].active_agents == 2

    def test_history_capped_at_100_on_save(self, storage: Path) -> None:
        c = MetricsCollector(storage_path=storage)
        for i in range(150):
            c._history.append(FleetMetricsSnapshot(total_agents=i))
        c._save_history()

        c2 = MetricsCollector(storage_path=storage)
        assert len(c2._history) == 100
        # Should retain the last 100 (indices 50..149)
        assert c2._history[0].total_agents == 50

    def test_load_invalid_json_yields_empty_history(self, storage: Path) -> None:
        storage.parent.mkdir(parents=True, exist_ok=True)
        storage.write_text("{ not valid json }")
        c = MetricsCollector(storage_path=storage)
        assert c._history == []

    def test_load_missing_file_yields_empty_history(self, storage: Path) -> None:
        assert not storage.exists()
        c = MetricsCollector(storage_path=storage)
        assert c._history == []

    def test_load_partial_history_items(self, storage: Path) -> None:
        """Items with missing keys load with defaults."""
        data = {
            "history": [
                {
                    "total_agents": 3,
                    "active_agents": 1,
                    "idle_agents": 1,
                    "error_agents": 0,
                    "avg_context_usage": 25.0,
                    "total_tasks_completed": 5,
                    "uptime_seconds": 100.0,
                },
                {},  # all defaults
            ]
        }
        storage.write_text(json.dumps(data))
        c = MetricsCollector(storage_path=storage)
        assert len(c._history) == 2
        assert c._history[0].total_agents == 3
        assert c._history[1].total_agents == 0

    def test_save_creates_parent_directories(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c" / "metrics.json"
        c = MetricsCollector(storage_path=deep)
        c._history.append(FleetMetricsSnapshot(total_agents=9))
        c._save_history()
        assert deep.exists()
        saved = json.loads(deep.read_text())
        assert saved["history"][0]["total_agents"] == 9

    # --- collect ---

    def test_collect_rate_limit_returns_cached(self, collector: MetricsCollector) -> None:
        sentinel = FleetMetricsSnapshot(total_agents=77)
        collector._history.append(sentinel)
        collector._last_collect = time.time()  # just collected
        result = collector.collect()
        assert result.total_agents == 77

    def test_collect_rate_limit_no_history_returns_default(
        self, collector: MetricsCollector
    ) -> None:
        collector._last_collect = time.time()
        result = collector.collect()
        assert isinstance(result, FleetMetricsSnapshot)
        assert result.total_agents == 0

    def test_collect_after_rate_limit_calls_dashboard(self, storage: Path) -> None:
        c = MetricsCollector(storage_path=storage)
        c._last_collect = 0.0

        mock_agent_active = MagicMock(status="active", context_estimate=30)
        mock_agent_idle = MagicMock(status="idle", context_estimate=70)
        mock_status = MagicMock(agents=[mock_agent_active, mock_agent_idle])

        with patch(
            "forge_harness.fleet.dashboard.get_fleet_status", return_value=mock_status
        ):
            result = c.collect()

        assert result.total_agents == 2
        assert result.active_agents == 1
        assert result.idle_agents == 1
        assert result.error_agents == 0
        assert result.avg_context_usage == 50.0

    def test_collect_handles_dashboard_exception(self, storage: Path) -> None:
        c = MetricsCollector(storage_path=storage)
        c._last_collect = 0.0
        with patch(
            "forge_harness.fleet.dashboard.get_fleet_status",
            side_effect=RuntimeError("unavailable"),
        ):
            result = c.collect()
        assert isinstance(result, FleetMetricsSnapshot)

    def test_collect_handles_import_error(self, storage: Path) -> None:
        c = MetricsCollector(storage_path=storage)
        c._last_collect = 0.0
        with patch(
            "forge_harness.fleet.dashboard.get_fleet_status",
            side_effect=ImportError("missing"),
        ):
            result = c.collect()
        assert isinstance(result, FleetMetricsSnapshot)

    def test_collect_appends_to_history(self, storage: Path) -> None:
        c = MetricsCollector(storage_path=storage)
        c._last_collect = 0.0
        with patch(
            "forge_harness.fleet.dashboard.get_fleet_status",
            side_effect=RuntimeError("skip"),
        ):
            c.collect()
        assert len(c._history) == 1

    def test_second_collect_within_rate_limit_does_not_duplicate(
        self, storage: Path
    ) -> None:
        c = MetricsCollector(storage_path=storage)
        c._last_collect = 0.0
        with patch(
            "forge_harness.fleet.dashboard.get_fleet_status",
            side_effect=RuntimeError("skip"),
        ):
            c.collect()
        # Second call — rate limited
        c.collect()
        assert len(c._history) == 1

    # --- get_history ---

    def test_get_history_returns_only_recent(self, collector: MetricsCollector) -> None:
        old = FleetMetricsSnapshot(total_agents=1)
        old.timestamp = datetime.fromtimestamp(time.time() - 7200)
        recent = FleetMetricsSnapshot(total_agents=2)
        collector._history = [old, recent]

        result = collector.get_history(minutes=60)
        assert len(result) == 1
        assert result[0].total_agents == 2

    def test_get_history_empty_when_all_old(self, collector: MetricsCollector) -> None:
        old = FleetMetricsSnapshot(total_agents=5)
        old.timestamp = datetime.fromtimestamp(time.time() - 10000)
        collector._history = [old]
        result = collector.get_history(minutes=1)
        assert result == []

    def test_get_history_all_recent_returned(self, collector: MetricsCollector) -> None:
        for i in range(5):
            collector._history.append(FleetMetricsSnapshot(total_agents=i))
        result = collector.get_history(minutes=60)
        assert len(result) == 5

    # --- export_prometheus ---

    def test_prometheus_empty_history(self, collector: MetricsCollector) -> None:
        out = collector.export_prometheus()
        assert "forge_fleet_total_agents 0" in out
        assert "forge_fleet_active_agents 0" in out
        assert "forge_fleet_idle_agents 0" in out
        assert "forge_fleet_error_agents 0" in out
        assert "forge_fleet_avg_context_usage 0.00" in out

    def test_prometheus_uses_last_history_entry(self, collector: MetricsCollector) -> None:
        collector._history.append(FleetMetricsSnapshot(total_agents=1))
        collector._history.append(FleetMetricsSnapshot(total_agents=2))
        collector._history.append(FleetMetricsSnapshot(total_agents=3))
        out = collector.export_prometheus()
        assert "forge_fleet_total_agents 3" in out

    def test_prometheus_with_data(self, collector: MetricsCollector) -> None:
        m = FleetMetricsSnapshot(
            total_agents=10,
            active_agents=6,
            idle_agents=3,
            error_agents=1,
            avg_context_usage=45.5,
        )
        collector._history.append(m)
        out = collector.export_prometheus()
        assert "forge_fleet_total_agents 10" in out
        assert "forge_fleet_active_agents 6" in out
        assert "forge_fleet_idle_agents 3" in out
        assert "forge_fleet_error_agents 1" in out
        assert "forge_fleet_avg_context_usage 45.50" in out


# ===========================================================================
# SECTION 3 — create_metrics_collector factory
# ===========================================================================


class TestCreateMetricsCollector:
    def test_returns_instance(self) -> None:
        c = create_metrics_collector()
        assert isinstance(c, MetricsCollector)

    def test_default_path(self) -> None:
        c = create_metrics_collector()
        assert c.storage_path == Path(".forge/learning/fleet_metrics.json")

    def test_custom_path(self, tmp_path: Path) -> None:
        p = tmp_path / "custom.json"
        c = create_metrics_collector(storage_path=p)
        assert c.storage_path == p


# ===========================================================================
# SECTION 4 — record_dispatch_metrics helper
# ===========================================================================


class TestRecordDispatchMetrics:
    def test_does_not_raise_on_success(self) -> None:
        record_dispatch_metrics("agent-x", success=True, delivery_time_ms=5.0)

    def test_does_not_raise_on_failure(self) -> None:
        record_dispatch_metrics("agent-x", success=False, delivery_time_ms=999.9, retries=3)

    def test_default_retries_zero(self) -> None:
        record_dispatch_metrics("agent-x", success=True, delivery_time_ms=1.0)

    def test_logs_via_debug(self) -> None:
        with patch("forge_harness.fleet.metrics.logger") as mock_logger:
            record_dispatch_metrics("agent-z", success=False, delivery_time_ms=123.0, retries=2)
        mock_logger.debug.assert_called_once()
        msg = mock_logger.debug.call_args[0][0]
        assert "agent-z" in msg
        assert "False" in msg


# ===========================================================================
# SECTION 5 — MetricsReport dataclass
# ===========================================================================


class TestMetricsReport:
    @pytest.fixture
    def report(self) -> MetricsReport:
        now = datetime.now(UTC)
        return MetricsReport(
            total_tasks=10,
            completed_tasks=7,
            failed_tasks=2,
            in_progress_tasks=1,
            success_rate=0.777,
            avg_duration_seconds=30.0,
            total_agents=3,
            period_start=now - timedelta(days=7),
            period_end=now,
            task_types={"feature": 5, "bugfix": 3, "refactor": 2},
        )

    def test_to_dict_has_all_keys(self, report: MetricsReport) -> None:
        d = report.to_dict()
        assert set(d.keys()) == {
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

    def test_to_dict_numeric_values(self, report: MetricsReport) -> None:
        d = report.to_dict()
        assert d["total_tasks"] == 10
        assert d["completed_tasks"] == 7
        assert d["failed_tasks"] == 2
        assert d["in_progress_tasks"] == 1
        assert d["success_rate"] == 0.777
        assert d["total_agents"] == 3

    def test_to_dict_timestamps_are_iso_strings(self, report: MetricsReport) -> None:
        d = report.to_dict()
        datetime.fromisoformat(d["period_start"])
        datetime.fromisoformat(d["period_end"])

    def test_to_dict_task_types_preserved(self, report: MetricsReport) -> None:
        d = report.to_dict()
        assert d["task_types"] == {"feature": 5, "bugfix": 3, "refactor": 2}

    def test_empty_task_types_default(self) -> None:
        now = datetime.now(UTC)
        r = MetricsReport(
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
        assert r.to_dict()["task_types"] == {}


# ===========================================================================
# SECTION 6 — AgentMetrics dataclass
# ===========================================================================


class TestAgentMetrics:
    @pytest.fixture
    def am(self) -> AgentMetrics:
        now = datetime.now(UTC)
        return AgentMetrics(
            agent_id="agent-A",
            total_tasks=15,
            completed_tasks=12,
            failed_tasks=3,
            success_rate=0.8,
            avg_duration_seconds=60.0,
            utilization=0.33,
            active_time_seconds=2400.0,
            total_time_seconds=7200.0,
            first_task=now - timedelta(hours=2),
            last_task=now,
        )

    def test_to_dict_keys(self, am: AgentMetrics) -> None:
        d = am.to_dict()
        assert set(d.keys()) == {
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

    def test_to_dict_values(self, am: AgentMetrics) -> None:
        d = am.to_dict()
        assert d["agent_id"] == "agent-A"
        assert d["total_tasks"] == 15
        assert d["success_rate"] == 0.8
        assert d["utilization"] == 0.33

    def test_to_dict_timestamps_parseable(self, am: AgentMetrics) -> None:
        d = am.to_dict()
        datetime.fromisoformat(d["first_task"])
        datetime.fromisoformat(d["last_task"])

    def test_to_dict_none_timestamps(self) -> None:
        am = AgentMetrics(
            agent_id="ghost",
            total_tasks=0,
            completed_tasks=0,
            failed_tasks=0,
            success_rate=0.0,
            avg_duration_seconds=0.0,
            utilization=0.0,
            active_time_seconds=0.0,
            total_time_seconds=86400.0,
        )
        d = am.to_dict()
        assert d["first_task"] is None
        assert d["last_task"] is None


# ===========================================================================
# SECTION 7 — TaskTypeMetrics dataclass
# ===========================================================================


class TestTaskTypeMetrics:
    def test_to_dict_keys(self) -> None:
        ttm = TaskTypeMetrics(
            task_type="feature",
            total_tasks=10,
            completed_tasks=8,
            failed_tasks=2,
            success_rate=0.8,
            avg_duration_seconds=45.0,
        )
        d = ttm.to_dict()
        assert set(d.keys()) == {
            "task_type",
            "total_tasks",
            "completed_tasks",
            "failed_tasks",
            "success_rate",
            "avg_duration_seconds",
        }

    def test_to_dict_values(self) -> None:
        ttm = TaskTypeMetrics(
            task_type="bugfix",
            total_tasks=5,
            completed_tasks=4,
            failed_tasks=1,
            success_rate=0.8,
            avg_duration_seconds=20.0,
        )
        d = ttm.to_dict()
        assert d["task_type"] == "bugfix"
        assert d["total_tasks"] == 5
        assert d["success_rate"] == 0.8


# ===========================================================================
# SECTION 8 — FleetMetrics (SQLite-backed)
# ===========================================================================


class TestFleetMetricsInit:
    def test_creates_db_file(self, tmp_path: Path) -> None:
        db = tmp_path / "fm.db"
        assert not db.exists()
        FleetMetrics(db_path=db)
        assert db.exists()

    def test_default_db_path(self, tmp_path: Path) -> None:
        with patch("forge_harness.fleet.metrics.Path.cwd", return_value=tmp_path):
            fm = FleetMetrics()
        assert fm.db_path == tmp_path / ".forge_dashboard.db"

    def test_table_name_constant(self) -> None:
        assert FleetMetrics.TABLE_NAME == "dashboard_task_events"

    def test_schema_has_expected_table(self, fleet_metrics: FleetMetrics) -> None:
        with sqlite3.connect(str(fleet_metrics.db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='dashboard_task_events'"
            )
            assert cursor.fetchone() is not None

    def test_schema_has_expected_indexes(self, fleet_metrics: FleetMetrics) -> None:
        with sqlite3.connect(str(fleet_metrics.db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='dashboard_task_events'"
            )
            index_names = {row[0] for row in cursor.fetchall()}
        assert "idx_agent_started" in index_names
        assert "idx_task_type" in index_names
        assert "idx_started_at" in index_names
        assert "idx_outcome" in index_names


class TestFleetMetricsRecordTaskStart:
    def test_returns_true_on_success(self, fleet_metrics: FleetMetrics) -> None:
        assert fleet_metrics.record_task_start("agent-1", "task-001") is True

    def test_returns_false_on_duplicate_task_id(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("agent-1", "dup-task")
        assert fleet_metrics.record_task_start("agent-1", "dup-task") is False

    def test_with_task_type(self, fleet_metrics: FleetMetrics) -> None:
        assert fleet_metrics.record_task_start("a", "t", task_type="feature") is True

    def test_with_metadata_dict(self, fleet_metrics: FleetMetrics) -> None:
        assert fleet_metrics.record_task_start("a", "t2", metadata={"key": "value"}) is True

    def test_with_all_params(self, fleet_metrics: FleetMetrics) -> None:
        assert (
            fleet_metrics.record_task_start(
                "agent-2", "task-full", task_type="bugfix", metadata={"priority": "high"}
            )
            is True
        )

    def test_returns_false_on_db_error(self, fleet_metrics: FleetMetrics) -> None:
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("db error")
            assert fleet_metrics.record_task_start("a", "t") is False

    def test_multiple_agents_same_type(self, fleet_metrics: FleetMetrics) -> None:
        for i in range(4):
            fleet_metrics.record_task_start(f"agent-{i}", f"task-{i}", task_type="feature")
        assert fleet_metrics.list_active_agents() == [
            "agent-0",
            "agent-1",
            "agent-2",
            "agent-3",
        ]


class TestFleetMetricsRecordTaskEnd:
    def test_returns_true_on_success(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a", "t")
        assert fleet_metrics.record_task_end("t", outcome="success") is True

    def test_returns_false_for_missing_task(self, fleet_metrics: FleetMetrics) -> None:
        assert fleet_metrics.record_task_end("ghost-task", outcome="success") is False

    def test_failure_outcome(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a", "f")
        assert fleet_metrics.record_task_end("f", outcome="failure") is True

    def test_cancelled_outcome(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a", "c")
        assert fleet_metrics.record_task_end("c", outcome="cancelled") is True

    def test_end_metadata_provided(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a", "m")
        assert fleet_metrics.record_task_end("m", outcome="success", metadata={"done": True}) is True

    def test_merges_start_and_end_metadata(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a", "merge-t", metadata={"start": 1})
        assert (
            fleet_metrics.record_task_end("merge-t", outcome="success", metadata={"end": 2}) is True
        )

    def test_end_only_metadata_no_start_metadata(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a", "no-start-meta")
        assert (
            fleet_metrics.record_task_end("no-start-meta", outcome="success", metadata={"end": 1})
            is True
        )

    def test_duration_is_non_negative(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a", "dur-t")
        fleet_metrics.record_task_end("dur-t", outcome="success")
        with sqlite3.connect(str(fleet_metrics.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT duration_seconds FROM dashboard_task_events WHERE task_id = ?",
                ("dur-t",),
            ).fetchone()
        assert row["duration_seconds"] >= 0.0

    def test_returns_false_on_db_error(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a", "err-t")
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("db error")
            assert fleet_metrics.record_task_end("err-t", outcome="success") is False


class TestFleetMetricsGetMetrics:
    def _seed(
        self,
        fm: FleetMetrics,
        n_success: int = 2,
        n_failure: int = 1,
        n_progress: int = 1,
    ) -> None:
        for i in range(n_success):
            fm.record_task_start(f"agent-{i % 2}", f"ts-{i}", task_type="feature")
            fm.record_task_end(f"ts-{i}", outcome="success")
        for i in range(n_failure):
            fm.record_task_start(f"agent-{i % 2}", f"tf-{i}", task_type="bugfix")
            fm.record_task_end(f"tf-{i}", outcome="failure")
        for i in range(n_progress):
            fm.record_task_start("agent-0", f"tp-{i}", task_type="refactor")

    def test_returns_metrics_report(self, fleet_metrics: FleetMetrics) -> None:
        self._seed(fleet_metrics)
        assert isinstance(fleet_metrics.get_metrics(days=7), MetricsReport)

    def test_total_tasks(self, fleet_metrics: FleetMetrics) -> None:
        self._seed(fleet_metrics, n_success=2, n_failure=1, n_progress=1)
        report = fleet_metrics.get_metrics(days=7)
        assert report.total_tasks == 4

    def test_completed_tasks(self, fleet_metrics: FleetMetrics) -> None:
        self._seed(fleet_metrics, n_success=3, n_failure=0, n_progress=0)
        assert fleet_metrics.get_metrics(days=7).completed_tasks == 3

    def test_failed_tasks(self, fleet_metrics: FleetMetrics) -> None:
        self._seed(fleet_metrics, n_success=0, n_failure=2, n_progress=0)
        assert fleet_metrics.get_metrics(days=7).failed_tasks == 2

    def test_in_progress_tasks(self, fleet_metrics: FleetMetrics) -> None:
        self._seed(fleet_metrics, n_success=0, n_failure=0, n_progress=3)
        assert fleet_metrics.get_metrics(days=7).in_progress_tasks == 3

    def test_success_rate(self, fleet_metrics: FleetMetrics) -> None:
        self._seed(fleet_metrics, n_success=3, n_failure=1, n_progress=0)
        assert fleet_metrics.get_metrics(days=7).success_rate == pytest.approx(0.75)

    def test_success_rate_zero_when_no_outcomes(self, fleet_metrics: FleetMetrics) -> None:
        self._seed(fleet_metrics, n_success=0, n_failure=0, n_progress=2)
        assert fleet_metrics.get_metrics(days=7).success_rate == 0.0

    def test_task_types_breakdown(self, fleet_metrics: FleetMetrics) -> None:
        self._seed(fleet_metrics, n_success=1, n_failure=1, n_progress=1)
        types = fleet_metrics.get_metrics(days=7).task_types
        assert "feature" in types
        assert "bugfix" in types
        assert "refactor" in types

    def test_period_boundaries(self, fleet_metrics: FleetMetrics) -> None:
        report = fleet_metrics.get_metrics(days=7)
        assert report.period_end > report.period_start

    def test_empty_database(self, fleet_metrics: FleetMetrics) -> None:
        report = fleet_metrics.get_metrics(days=7)
        assert report.total_tasks == 0
        assert report.success_rate == 0.0

    def test_returns_empty_on_db_error(self, fleet_metrics: FleetMetrics) -> None:
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("db error")
            report = fleet_metrics.get_metrics()
        assert report.total_tasks == 0


class TestFleetMetricsGetAgentMetrics:
    def test_returns_agent_metrics_instance(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a1", "t")
        fleet_metrics.record_task_end("t", outcome="success")
        assert isinstance(fleet_metrics.get_agent_metrics("a1"), AgentMetrics)

    def test_total_tasks_for_agent(self, fleet_metrics: FleetMetrics) -> None:
        for i in range(4):
            fleet_metrics.record_task_start("a1", f"t{i}")
            fleet_metrics.record_task_end(f"t{i}", outcome="success")
        assert fleet_metrics.get_agent_metrics("a1").total_tasks == 4

    def test_completed_and_failed_counts(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a1", "ok")
        fleet_metrics.record_task_end("ok", outcome="success")
        fleet_metrics.record_task_start("a1", "bad")
        fleet_metrics.record_task_end("bad", outcome="failure")
        r = fleet_metrics.get_agent_metrics("a1")
        assert r.completed_tasks == 1
        assert r.failed_tasks == 1

    def test_success_rate(self, fleet_metrics: FleetMetrics) -> None:
        for i in range(3):
            fleet_metrics.record_task_start("a1", f"ok{i}")
            fleet_metrics.record_task_end(f"ok{i}", outcome="success")
        fleet_metrics.record_task_start("a1", "fail")
        fleet_metrics.record_task_end("fail", outcome="failure")
        assert fleet_metrics.get_agent_metrics("a1").success_rate == pytest.approx(0.75)

    def test_zero_success_rate_for_unknown_agent(self, fleet_metrics: FleetMetrics) -> None:
        assert fleet_metrics.get_agent_metrics("ghost").success_rate == 0.0

    def test_utilization_within_bounds(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a1", "t")
        fleet_metrics.record_task_end("t", outcome="success")
        r = fleet_metrics.get_agent_metrics("a1")
        assert 0.0 <= r.utilization <= 1.0

    def test_first_and_last_task_set(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a1", "first")
        fleet_metrics.record_task_end("first", outcome="success")
        fleet_metrics.record_task_start("a1", "last")
        fleet_metrics.record_task_end("last", outcome="success")
        r = fleet_metrics.get_agent_metrics("a1")
        assert r.first_task is not None
        assert r.last_task is not None

    def test_none_timestamps_for_no_tasks(self, fleet_metrics: FleetMetrics) -> None:
        r = fleet_metrics.get_agent_metrics("noone")
        assert r.first_task is None
        assert r.last_task is None

    def test_returns_empty_on_db_error(self, fleet_metrics: FleetMetrics) -> None:
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("err")
            r = fleet_metrics.get_agent_metrics("a1")
        assert r.total_tasks == 0

    def test_isolates_per_agent(self, fleet_metrics: FleetMetrics) -> None:
        for i in range(2):
            fleet_metrics.record_task_start("agent-A", f"A-{i}")
            fleet_metrics.record_task_end(f"A-{i}", outcome="success")
        for i in range(5):
            fleet_metrics.record_task_start("agent-B", f"B-{i}")
            fleet_metrics.record_task_end(f"B-{i}", outcome="success")
        assert fleet_metrics.get_agent_metrics("agent-A").total_tasks == 2
        assert fleet_metrics.get_agent_metrics("agent-B").total_tasks == 5


class TestFleetMetricsGetTaskTypeMetrics:
    def _seed_type(
        self, fm: FleetMetrics, ttype: str, n_success: int = 2, n_failure: int = 1
    ) -> None:
        for i in range(n_success):
            fm.record_task_start("a", f"{ttype}-s{i}", task_type=ttype)
            fm.record_task_end(f"{ttype}-s{i}", outcome="success")
        for i in range(n_failure):
            fm.record_task_start("a", f"{ttype}-f{i}", task_type=ttype)
            fm.record_task_end(f"{ttype}-f{i}", outcome="failure")

    def test_returns_task_type_metrics(self, fleet_metrics: FleetMetrics) -> None:
        self._seed_type(fleet_metrics, "feature")
        assert isinstance(fleet_metrics.get_task_type_metrics("feature"), TaskTypeMetrics)

    def test_total_tasks(self, fleet_metrics: FleetMetrics) -> None:
        self._seed_type(fleet_metrics, "bugfix", n_success=2, n_failure=1)
        assert fleet_metrics.get_task_type_metrics("bugfix").total_tasks == 3

    def test_success_rate(self, fleet_metrics: FleetMetrics) -> None:
        self._seed_type(fleet_metrics, "refactor", n_success=3, n_failure=1)
        assert fleet_metrics.get_task_type_metrics("refactor").success_rate == pytest.approx(0.75)

    def test_unknown_type_returns_zeros(self, fleet_metrics: FleetMetrics) -> None:
        r = fleet_metrics.get_task_type_metrics("ghost")
        assert r.total_tasks == 0
        assert r.success_rate == 0.0

    def test_task_type_preserved(self, fleet_metrics: FleetMetrics) -> None:
        self._seed_type(fleet_metrics, "special-type", n_success=1, n_failure=0)
        assert fleet_metrics.get_task_type_metrics("special-type").task_type == "special-type"

    def test_returns_empty_on_db_error(self, fleet_metrics: FleetMetrics) -> None:
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("err")
            r = fleet_metrics.get_task_type_metrics("any")
        assert r.total_tasks == 0


class TestFleetMetricsListActiveAgents:
    def test_empty_when_no_tasks(self, fleet_metrics: FleetMetrics) -> None:
        assert fleet_metrics.list_active_agents() == []

    def test_returns_distinct_agents(self, fleet_metrics: FleetMetrics) -> None:
        for i in range(3):
            fleet_metrics.record_task_start("agent-X", f"Xt{i}")
        for i in range(2):
            fleet_metrics.record_task_start("agent-Y", f"Yt{i}")
        agents = fleet_metrics.list_active_agents()
        assert set(agents) == {"agent-X", "agent-Y"}

    def test_sorted_alphabetically(self, fleet_metrics: FleetMetrics) -> None:
        for name in ("charlie", "alice", "bob"):
            fleet_metrics.record_task_start(name, f"t-{name}")
        result = fleet_metrics.list_active_agents()
        assert result == sorted(result)

    def test_days_filters_old_records(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aged.db"
        fm = FleetMetrics(db_path=db_path)
        old_ts = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO dashboard_task_events (task_id, agent_id, started_at) VALUES (?,?,?)",
                ("old", "old-agent", old_ts),
            )
        assert "old-agent" not in fm.list_active_agents(days=7)

    def test_returns_empty_on_db_error(self, fleet_metrics: FleetMetrics) -> None:
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("err")
            assert fleet_metrics.list_active_agents() == []


class TestFleetMetricsListTaskTypes:
    def test_empty_when_no_tasks(self, fleet_metrics: FleetMetrics) -> None:
        assert fleet_metrics.list_task_types() == []

    def test_excludes_null_types(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a", "no-type")
        assert fleet_metrics.list_task_types() == []

    def test_lists_distinct_types(self, fleet_metrics: FleetMetrics) -> None:
        for t in ("feature", "bugfix", "feature"):
            fleet_metrics.record_task_start("a", f"t-{t}-{id(t)}", task_type=t)
        assert set(fleet_metrics.list_task_types()) == {"feature", "bugfix"}

    def test_sorted_alphabetically(self, fleet_metrics: FleetMetrics) -> None:
        for t in ("zebra", "apple", "mango"):
            fleet_metrics.record_task_start("a", f"t-{t}", task_type=t)
        result = fleet_metrics.list_task_types()
        assert result == sorted(result)

    def test_returns_empty_on_db_error(self, fleet_metrics: FleetMetrics) -> None:
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("err")
            assert fleet_metrics.list_task_types() == []


class TestFleetMetricsCleanupOldMetrics:
    def test_returns_zero_when_nothing_old(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a", "recent")
        assert fleet_metrics.cleanup_old_metrics(days=90) == 0

    def test_deletes_old_records(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cleanup.db"
        fm = FleetMetrics(db_path=db_path)
        old_ts = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO dashboard_task_events (task_id, agent_id, started_at) VALUES (?,?,?)",
                ("old-task", "a", old_ts),
            )
        assert fm.cleanup_old_metrics(days=90) == 1

    def test_keeps_recent_records(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a", "keep")
        fleet_metrics.cleanup_old_metrics(days=90)
        assert "a" in fleet_metrics.list_active_agents()

    def test_returns_zero_on_db_error(self, fleet_metrics: FleetMetrics) -> None:
        with patch.object(fleet_metrics, "_get_connection") as mock_ctx:
            mock_ctx.return_value.__enter__.side_effect = Exception("err")
            assert fleet_metrics.cleanup_old_metrics() == 0

    def test_window_boundary(self, tmp_path: Path) -> None:
        db_path = tmp_path / "boundary.db"
        fm = FleetMetrics(db_path=db_path)
        now = datetime.now(UTC)
        old_ts = (now - timedelta(days=200)).isoformat()
        recent_ts = (now - timedelta(days=1)).isoformat()
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO dashboard_task_events (task_id, agent_id, started_at) VALUES (?,?,?)",
                ("old", "a-old", old_ts),
            )
            conn.execute(
                "INSERT INTO dashboard_task_events (task_id, agent_id, started_at) VALUES (?,?,?)",
                ("recent", "a-recent", recent_ts),
            )
        deleted = fm.cleanup_old_metrics(days=90)
        assert deleted == 1
        remaining = fm.list_active_agents()
        assert "a-recent" in remaining
        assert "a-old" not in remaining


class TestFleetMetricsConnectionManager:
    def test_commits_on_success(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("cm-agent", "cm-task")
        assert "cm-agent" in fleet_metrics.list_active_agents()

    def test_rollback_on_exception(self, fleet_metrics: FleetMetrics) -> None:
        with pytest.raises(RuntimeError):
            with fleet_metrics._get_connection() as conn:
                conn.execute(
                    "INSERT INTO dashboard_task_events (task_id, agent_id, started_at) VALUES (?,?,?)",
                    ("rb-task", "rb-agent", datetime.now(UTC).isoformat()),
                )
                raise RuntimeError("force rollback")
        assert "rb-agent" not in fleet_metrics.list_active_agents()


class TestCreateFleetMetrics:
    def test_returns_fleet_metrics_instance(self, tmp_path: Path) -> None:
        fm = create_fleet_metrics(db_path=tmp_path / "fm.db")
        assert isinstance(fm, FleetMetrics)

    def test_default_path(self, tmp_path: Path) -> None:
        with patch("forge_harness.fleet.metrics.Path.cwd", return_value=tmp_path):
            fm = create_fleet_metrics()
        assert fm.db_path == tmp_path / ".forge_dashboard.db"

    def test_custom_path(self, tmp_path: Path) -> None:
        p = tmp_path / "custom.db"
        fm = create_fleet_metrics(db_path=p)
        assert fm.db_path == p


# ===========================================================================
# SECTION 9 — FleetMetrics integration (full lifecycle)
# ===========================================================================


class TestFleetMetricsIntegration:
    def test_full_task_lifecycle(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a", "full-task", task_type="feature", metadata={"v": 1})
        fleet_metrics.record_task_end("full-task", outcome="success", metadata={"result": "ok"})

        report = fleet_metrics.get_metrics(days=7)
        assert report.total_tasks == 1
        assert report.completed_tasks == 1
        assert report.success_rate == 1.0

        agent_r = fleet_metrics.get_agent_metrics("a")
        assert agent_r.total_tasks == 1

        type_r = fleet_metrics.get_task_type_metrics("feature")
        assert type_r.total_tasks == 1

    def test_mixed_outcomes_report(self, fleet_metrics: FleetMetrics) -> None:
        for i, outcome in enumerate(["success", "success", "failure"]):
            fleet_metrics.record_task_start("a", f"m{i}", task_type="work")
            fleet_metrics.record_task_end(f"m{i}", outcome=outcome)

        report = fleet_metrics.get_metrics(days=7)
        assert report.total_tasks == 3
        assert report.success_rate == pytest.approx(2 / 3)

    def test_report_to_dict_roundtrip(self, fleet_metrics: FleetMetrics) -> None:
        fleet_metrics.record_task_start("a", "rt", task_type="feature")
        fleet_metrics.record_task_end("rt", outcome="success")
        d = fleet_metrics.get_metrics(days=7).to_dict()
        assert d["total_tasks"] == 1
        assert d["success_rate"] == 1.0
        assert "feature" in d["task_types"]


# ===========================================================================
# SECTION 10 — fleet.restart dataclasses
# ===========================================================================


class TestHealthStatus:
    def test_required_fields(self) -> None:
        now = datetime.now(UTC)
        h = HealthStatus(agent_id="forge:tech", status="healthy", last_activity=now)
        assert h.agent_id == "forge:tech"
        assert h.status == "healthy"
        assert h.last_activity == now

    def test_optional_fields_default_to_none(self) -> None:
        h = HealthStatus(agent_id="a", status="stale", last_activity=None)
        assert h.current_task is None
        assert h.context_percent is None
        assert h.error_message is None

    def test_all_fields_set(self) -> None:
        now = datetime.now(UTC)
        h = HealthStatus(
            agent_id="forge:a1",
            status="crashed",
            last_activity=now,
            current_task="doing work",
            context_percent=0.5,
            error_message="OOM",
        )
        assert h.current_task == "doing work"
        assert h.context_percent == 0.5
        assert h.error_message == "OOM"

    def test_status_values(self) -> None:
        for s in ("healthy", "stale", "crashed"):
            h = HealthStatus(agent_id="x", status=s, last_activity=None)
            assert h.status == s


class TestTaskState:
    def test_required_field_only(self) -> None:
        ts = TaskState(task_description="Fix the bug")
        assert ts.task_description == "Fix the bug"
        assert ts.progress is None
        assert ts.context_remaining is None
        assert ts.file_being_edited is None

    def test_all_fields(self) -> None:
        ts = TaskState(
            task_description="Add auth",
            progress="50%",
            context_remaining=30,
            file_being_edited="auth.py",
        )
        assert ts.progress == "50%"
        assert ts.context_remaining == 30
        assert ts.file_being_edited == "auth.py"


class TestRestartEvent:
    def test_required_fields(self) -> None:
        e = RestartEvent(
            agent_id="forge:tech-new",
            reason="stale_agent",
            recovered_task="Task: do something",
            new_session_id="forge:tech-new",
        )
        assert e.agent_id == "forge:tech-new"
        assert e.reason == "stale_agent"
        assert e.old_session_id is None

    def test_auto_timestamp(self) -> None:
        e = RestartEvent(
            agent_id="a", reason="r", recovered_task=None, new_session_id="a-new"
        )
        parsed = datetime.fromisoformat(e.timestamp)
        assert parsed is not None

    def test_old_session_id_optional(self) -> None:
        e = RestartEvent(
            agent_id="a",
            reason="r",
            recovered_task=None,
            new_session_id="a-new",
            old_session_id="a-old",
        )
        assert e.old_session_id == "a-old"


# ===========================================================================
# SECTION 11 — AgentRestarter init
# ===========================================================================


class TestAgentRestarterInit:
    def test_explicit_forge_root(self, tmp_forge_root: Path) -> None:
        r = AgentRestarter(forge_root=tmp_forge_root)
        assert r.forge_root == tmp_forge_root

    def test_fleet_dir_created(self, tmp_path: Path) -> None:
        r = AgentRestarter(forge_root=tmp_path)
        assert (tmp_path / ".forge/fleet").exists()

    def test_custom_stale_threshold(self, tmp_forge_root: Path) -> None:
        r = AgentRestarter(forge_root=tmp_forge_root, stale_threshold_minutes=60)
        assert r.stale_threshold == timedelta(minutes=60)

    def test_default_stale_threshold(self, tmp_forge_root: Path) -> None:
        r = AgentRestarter(forge_root=tmp_forge_root)
        assert r.stale_threshold == timedelta(minutes=30)

    def test_restart_log_path(self, tmp_forge_root: Path) -> None:
        r = AgentRestarter(forge_root=tmp_forge_root)
        assert r.restart_log == tmp_forge_root / ".forge/fleet" / "restart.log"

    def test_none_forge_root_falls_back(self, tmp_path: Path) -> None:
        with patch("forge_harness.fleet.restart.Path.cwd", return_value=tmp_path):
            r = AgentRestarter(forge_root=None)
        assert isinstance(r.forge_root, Path)

    def test_none_forge_root_discovers_forge_fleet(self, tmp_path: Path) -> None:
        (tmp_path / ".forge/fleet").mkdir()
        with patch("forge_harness.fleet.restart.Path.cwd", return_value=tmp_path):
            r = AgentRestarter(forge_root=None)
        assert r.forge_root == tmp_path


# ===========================================================================
# SECTION 12 — _get_session_list
# ===========================================================================


class TestGetSessionList:
    def test_parses_valid_sessions(self, restarter: AgentRestarter) -> None:
        fake = "forge-tech:1700000000:1700000100\nforge-design:1700000200:1700000300\n"
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=fake)):
            sessions = restarter._get_session_list()
        assert len(sessions) == 2
        assert sessions[0]["name"] == "forge-tech"
        assert isinstance(sessions[0]["last_activity"], datetime)

    def test_returns_empty_on_nonzero_returncode(self, restarter: AgentRestarter) -> None:
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            assert restarter._get_session_list() == []

    def test_returns_empty_on_timeout(self, restarter: AgentRestarter) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 10)):
            assert restarter._get_session_list() == []

    def test_returns_empty_when_tmux_not_found(self, restarter: AgentRestarter) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert restarter._get_session_list() == []

    def test_returns_empty_on_generic_exception(self, restarter: AgentRestarter) -> None:
        with patch("subprocess.run", side_effect=RuntimeError("oops")):
            assert restarter._get_session_list() == []

    def test_skips_malformed_lines(self, restarter: AgentRestarter) -> None:
        fake = "bad-line\nforge-tech:1700000000:1700000100\n"
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=fake)):
            sessions = restarter._get_session_list()
        assert len(sessions) == 1
        assert sessions[0]["name"] == "forge-tech"

    def test_skips_blank_lines(self, restarter: AgentRestarter) -> None:
        fake = "forge-tech:1700000000:1700000100\n\n\n"
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=fake)):
            assert len(restarter._get_session_list()) == 1

    def test_skips_whitespace_only_lines(self, restarter: AgentRestarter) -> None:
        """Whitespace-only lines exercise the `continue` branch (line 132)."""
        # Mix of blank, whitespace-only, and valid lines
        fake = "\nforge-alpha:1700000000:1700000100\n   \nforge-beta:1700000200:1700000300\n"
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=fake)):
            sessions = restarter._get_session_list()
        assert len(sessions) == 2


# ===========================================================================
# SECTION 13 — _capture_session_output
# ===========================================================================


class TestCaptureSessionOutput:
    def test_returns_stdout_on_success(self, restarter: AgentRestarter) -> None:
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="session output\n"),
        ):
            assert restarter._capture_session_output("forge:tech") == "session output\n"

    def test_returns_empty_on_nonzero(self, restarter: AgentRestarter) -> None:
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            assert restarter._capture_session_output("forge:tech") == ""

    def test_returns_empty_on_timeout(self, restarter: AgentRestarter) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 10)):
            assert restarter._capture_session_output("forge:tech") == ""

    def test_returns_empty_on_exception(self, restarter: AgentRestarter) -> None:
        with patch("subprocess.run", side_effect=RuntimeError("err")):
            assert restarter._capture_session_output("forge:tech") == ""

    def test_custom_lines_count_in_command(self, restarter: AgentRestarter) -> None:
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="x"),
        ) as mock_run:
            restarter._capture_session_output("forge:tech", lines=500)
        call_args = mock_run.call_args[0][0]
        assert "-500" in call_args


# ===========================================================================
# SECTION 14 — _check_session_exit_code
# ===========================================================================


class TestCheckSessionExitCode:
    def test_returns_one_when_session_not_found(self, restarter: AgentRestarter) -> None:
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            assert restarter._check_session_exit_code("forge:gone") == 1

    def test_returns_zero_for_healthy_session(self, restarter: AgentRestarter) -> None:
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="id")):
            with patch.object(restarter, "_capture_session_output", return_value="Normal output"):
                assert restarter._check_session_exit_code("forge:tech") == 0

    def test_returns_one_when_error_in_output(self, restarter: AgentRestarter) -> None:
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="id")):
            with patch.object(
                restarter, "_capture_session_output", return_value="Error: something failed"
            ):
                assert restarter._check_session_exit_code("forge:tech") == 1

    @pytest.mark.parametrize(
        "error_text",
        [
            "Error: connection refused",
            "Exception: NullPointerException",
            "Traceback (most recent call last):",
            "Failed: step 2",
            "panic: runtime error",
            "exit code 127",
        ],
    )
    def test_detects_various_error_patterns(
        self, restarter: AgentRestarter, error_text: str
    ) -> None:
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="id")):
            with patch.object(restarter, "_capture_session_output", return_value=error_text):
                assert restarter._check_session_exit_code("forge:tech") == 1

    def test_returns_none_on_unexpected_exception(self, restarter: AgentRestarter) -> None:
        with patch("subprocess.run", side_effect=RuntimeError("oops")):
            assert restarter._check_session_exit_code("forge:tech") is None


# ===========================================================================
# SECTION 15 — _extract_current_task
# ===========================================================================


class TestExtractCurrentTask:
    def test_empty_string_returns_none(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_current_task("") is None

    def test_task_prefix(self, restarter: AgentRestarter) -> None:
        result = restarter._extract_current_task("Task: Implement OAuth2 module\n")
        assert result == "Implement OAuth2 module"

    def test_working_on_prefix(self, restarter: AgentRestarter) -> None:
        result = restarter._extract_current_task("Working on: database migrations\n")
        assert result == "database migrations"

    def test_implementing_prefix(self, restarter: AgentRestarter) -> None:
        result = restarter._extract_current_task("Implementing: the caching layer\n")
        assert result == "the caching layer"

    def test_feature_prefix(self, restarter: AgentRestarter) -> None:
        result = restarter._extract_current_task("Feature: user profile management\n")
        assert result == "user profile management"

    def test_markdown_header(self, restarter: AgentRestarter) -> None:
        result = restarter._extract_current_task("# Implement search functionality\n")
        assert result == "Implement search functionality"

    def test_fallback_to_last_line(self, restarter: AgentRestarter) -> None:
        result = restarter._extract_current_task("first line noise\nlast meaningful line here\n")
        assert result == "last meaningful line here"

    def test_ignores_short_lines(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_current_task("short\n") is None

    def test_ignores_prompt_lines(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_current_task("> type here\n") is None

    def test_truncates_at_200_chars(self, restarter: AgentRestarter) -> None:
        long = "A" * 300
        result = restarter._extract_current_task(f"Task: {long}\n")
        assert result is not None
        assert len(result) <= 200

    def test_case_insensitive(self, restarter: AgentRestarter) -> None:
        result = restarter._extract_current_task("TASK: uppercase description here\n")
        assert result is not None


# ===========================================================================
# SECTION 16 — _extract_context_usage
# ===========================================================================


class TestExtractContextUsage:
    def test_empty_returns_none(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_context_usage("") is None

    def test_context_colon_percent(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_context_usage("context: 75%") == pytest.approx(0.75)

    def test_percent_then_context(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_context_usage("50% context used") == pytest.approx(0.50)

    def test_usage_colon_percent(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_context_usage("usage: 90%") == pytest.approx(0.90)

    def test_returns_none_when_no_match(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_context_usage("No context info here") is None

    def test_case_insensitive(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_context_usage("CONTEXT: 60%") == pytest.approx(0.60)

    def test_divides_by_100(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_context_usage("context: 100%") == pytest.approx(1.0)

    def test_handles_value_error_in_conversion(self, restarter: AgentRestarter) -> None:
        """When float() raises ValueError the pattern is skipped and None returned."""
        import re as re_mod

        fake_match = MagicMock()
        fake_match.group.return_value = "not_a_number"

        with patch.object(re_mod, "search", return_value=fake_match):
            result = restarter._extract_context_usage("context: abc%")
        # All patterns hit ValueError → returns None
        assert result is None


# ===========================================================================
# SECTION 17 — _extract_error_message
# ===========================================================================


class TestExtractErrorMessage:
    def test_empty_returns_none(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_error_message("") is None

    def test_error_prefix(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_error_message("Error: connection timed out") == "connection timed out"

    def test_exception_prefix(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_error_message("Exception: NullPointerException") == "NullPointerException"

    def test_failed_prefix(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_error_message("Failed: build step 3") == "build step 3"

    def test_traceback_extracted(self, restarter: AgentRestarter) -> None:
        output = "Traceback (most recent call last):\n  File 'x.py', line 5\nValueError\n\n"
        result = restarter._extract_error_message(output)
        assert result is not None

    def test_returns_none_when_no_error(self, restarter: AgentRestarter) -> None:
        assert restarter._extract_error_message("Everything is fine here") is None

    def test_truncates_at_500_chars(self, restarter: AgentRestarter) -> None:
        long = "X" * 600
        result = restarter._extract_error_message(f"Error: {long}")
        assert result is not None
        assert len(result) <= 500


# ===========================================================================
# SECTION 18 — detect_stale_agents
# ===========================================================================


class TestDetectStaleAgents:
    def test_empty_when_no_sessions(self, restarter: AgentRestarter) -> None:
        with patch.object(restarter, "_get_session_list", return_value=[]):
            assert restarter.detect_stale_agents() == []

    def test_returns_stale_agent_over_threshold(self, restarter: AgentRestarter) -> None:
        session = _make_session("forge:tech", seconds_ago=2000)
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(
                restarter, "_capture_session_output", return_value="Task: fix the auth bug here"
            ):
                stale = restarter.detect_stale_agents(threshold_minutes=30)
        assert len(stale) == 1
        assert stale[0].agent_id == "forge:tech"
        assert stale[0].status == "stale"

    def test_ignores_active_agent(self, restarter: AgentRestarter) -> None:
        session = _make_session("forge:active", seconds_ago=60)
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            assert restarter.detect_stale_agents(threshold_minutes=30) == []

    def test_uses_instance_threshold_when_none(self, restarter: AgentRestarter) -> None:
        restarter.stale_threshold = timedelta(minutes=5)
        session = _make_session("forge:tech", seconds_ago=400)
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(
                restarter, "_capture_session_output", return_value="Task: some long task here"
            ):
                stale = restarter.detect_stale_agents()
        assert len(stale) == 1

    def test_extracts_current_task(self, restarter: AgentRestarter) -> None:
        session = _make_session("forge:tech", seconds_ago=2000)
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(
                restarter,
                "_capture_session_output",
                return_value="Task: implement search feature\n",
            ):
                stale = restarter.detect_stale_agents(threshold_minutes=30)
        assert stale[0].current_task == "implement search feature"

    def test_extracts_context_percent(self, restarter: AgentRestarter) -> None:
        session = _make_session("forge:tech", seconds_ago=2000)
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(
                restarter,
                "_capture_session_output",
                return_value="Task: build API endpoints\ncontext: 80%",
            ):
                stale = restarter.detect_stale_agents(threshold_minutes=30)
        assert stale[0].context_percent == pytest.approx(0.80)

    def test_mixed_sessions(self, restarter: AgentRestarter) -> None:
        sessions = [
            _make_session("forge:stale", seconds_ago=3600),
            _make_session("forge:active", seconds_ago=30),
        ]

        def fake_capture(sid: str, lines: int = 50) -> str:
            return "Task: some stale long task description" if "stale" in sid else ""

        with patch.object(restarter, "_get_session_list", return_value=sessions):
            with patch.object(restarter, "_capture_session_output", side_effect=fake_capture):
                stale = restarter.detect_stale_agents(threshold_minutes=30)

        assert len(stale) == 1
        assert stale[0].agent_id == "forge:stale"


# ===========================================================================
# SECTION 19 — detect_crashed_agents
# ===========================================================================


class TestDetectCrashedAgents:
    def test_empty_when_no_sessions(self, restarter: AgentRestarter) -> None:
        with patch.object(restarter, "_get_session_list", return_value=[]):
            assert restarter.detect_crashed_agents() == []

    def test_detects_crashed_agent(self, restarter: AgentRestarter) -> None:
        session = _make_session("forge:broken")
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_check_session_exit_code", return_value=1):
                with patch.object(
                    restarter, "_capture_session_output", return_value="Error: crash"
                ):
                    crashed = restarter.detect_crashed_agents()
        assert len(crashed) == 1
        assert crashed[0].status == "crashed"

    def test_ignores_healthy_agent(self, restarter: AgentRestarter) -> None:
        session = _make_session("forge:healthy")
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_check_session_exit_code", return_value=0):
                assert restarter.detect_crashed_agents() == []

    def test_ignores_none_exit_code(self, restarter: AgentRestarter) -> None:
        session = _make_session("forge:unknown")
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_check_session_exit_code", return_value=None):
                assert restarter.detect_crashed_agents() == []

    def test_error_message_populated(self, restarter: AgentRestarter) -> None:
        session = _make_session("forge:broken")
        with patch.object(restarter, "_get_session_list", return_value=[session]):
            with patch.object(restarter, "_check_session_exit_code", return_value=1):
                with patch.object(
                    restarter, "_capture_session_output", return_value="Error: out of memory"
                ):
                    crashed = restarter.detect_crashed_agents()
        assert crashed[0].error_message == "out of memory"


# ===========================================================================
# SECTION 20 — recover_task_state
# ===========================================================================


class TestRecoverTaskState:
    def test_returns_none_when_no_output(self, restarter: AgentRestarter) -> None:
        with patch.object(restarter, "_capture_session_output", return_value=""):
            assert restarter.recover_task_state("forge:tech") is None

    def test_returns_none_when_no_task_found(self, restarter: AgentRestarter) -> None:
        with patch.object(restarter, "_capture_session_output", return_value="short\n"):
            assert restarter.recover_task_state("forge:tech") is None

    def test_returns_task_state_with_description(self, restarter: AgentRestarter) -> None:
        with patch.object(
            restarter,
            "_capture_session_output",
            return_value="Task: Implement pagination for the API\n",
        ):
            state = restarter.recover_task_state("forge:tech")
        assert state is not None
        assert "pagination" in state.task_description.lower()

    def test_extracts_progress(self, restarter: AgentRestarter) -> None:
        with patch.object(
            restarter,
            "_capture_session_output",
            return_value="Task: Implement auth module\nProgress: step 2 of 5\n",
        ):
            state = restarter.recover_task_state("forge:tech")
        assert state is not None
        assert state.progress == "step 2 of 5"

    def test_extracts_file_being_edited(self, restarter: AgentRestarter) -> None:
        with patch.object(
            restarter,
            "_capture_session_output",
            return_value="Task: Fix authentication module\nediting: auth_handler.py\n",
        ):
            state = restarter.recover_task_state("forge:tech")
        assert state is not None
        assert state.file_being_edited == "auth_handler.py"

    def test_extracts_context_remaining(self, restarter: AgentRestarter) -> None:
        with patch.object(
            restarter,
            "_capture_session_output",
            return_value="Task: Build user profile page\ncontext: 60%\n",
        ):
            state = restarter.recover_task_state("forge:tech")
        assert state is not None
        assert state.context_remaining == 40  # 100 - 60

    def test_context_remaining_none_when_no_usage(self, restarter: AgentRestarter) -> None:
        with patch.object(
            restarter,
            "_capture_session_output",
            return_value="Task: Create database migration scripts\n",
        ):
            state = restarter.recover_task_state("forge:tech")
        assert state is not None
        assert state.context_remaining is None


# ===========================================================================
# SECTION 21 — restart_agent
# ===========================================================================


class TestRestartAgent:
    def _ok_run(self) -> MagicMock:
        return MagicMock(returncode=0)

    def test_returns_restart_event(self, restarter: AgentRestarter) -> None:
        with patch("subprocess.run", return_value=self._ok_run()):
            event = restarter.restart_agent("forge:tech", "Task: implement auth", reason="manual_restart")
        assert isinstance(event, RestartEvent)
        assert event.old_session_id == "forge:tech"
        assert event.reason == "manual_restart"
        assert "forge:tech" in event.new_session_id

    def test_writes_context_file(self, restarter: AgentRestarter, tmp_forge_root: Path) -> None:
        with patch("subprocess.run", return_value=self._ok_run()):
            restarter.restart_agent("forge:tech", "Task: build the feature", reason="stale_agent")
        files = list((tmp_forge_root / ".forge/restarts").glob("*.md"))
        assert len(files) == 1

    def test_context_file_content(self, restarter: AgentRestarter, tmp_forge_root: Path) -> None:
        with patch("subprocess.run", return_value=self._ok_run()):
            restarter.restart_agent("forge:tech", "Task: refactor the database layer")
        ctx_file = next((tmp_forge_root / ".forge/restarts").glob("*.md"))
        content = ctx_file.read_text()
        assert "forge:tech" in content
        assert "refactor" in content

    def test_raises_on_subprocess_error(self, restarter: AgentRestarter) -> None:
        with patch(
            "subprocess.run", side_effect=subprocess.CalledProcessError(1, "tmux")
        ):
            with pytest.raises(subprocess.CalledProcessError):
                restarter.restart_agent("forge:tech", "Task: do something important")

    def test_raises_on_timeout(self, restarter: AgentRestarter) -> None:
        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 10)
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                restarter.restart_agent("forge:tech", "Task: do something important")

    def test_logs_restart_event(self, restarter: AgentRestarter, tmp_forge_root: Path) -> None:
        with patch("subprocess.run", return_value=self._ok_run()):
            restarter.restart_agent("forge:tech", "Task: complete the sprint", reason="manual_restart")
        log_file = tmp_forge_root / ".forge/fleet" / "restart.log"
        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["old_session"] == "forge:tech"
        assert entry["reason"] == "manual_restart"

    def test_recovered_task_truncated_to_200(self, restarter: AgentRestarter) -> None:
        with patch("subprocess.run", return_value=self._ok_run()):
            event = restarter.restart_agent("forge:tech", "Task: " + "A" * 500)
        assert event.recovered_task is not None
        assert len(event.recovered_task) <= 200

    def test_default_reason_is_manual_restart(self, restarter: AgentRestarter) -> None:
        with patch("subprocess.run", return_value=self._ok_run()):
            event = restarter.restart_agent("forge:tech", "Task: implement feature")
        assert event.reason == "manual_restart"

    def test_two_send_keys_calls(self, restarter: AgentRestarter) -> None:
        calls: list = []

        def capture_run(cmd: list, *a: object, **kw: object) -> MagicMock:
            calls.append(cmd)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=capture_run):
            restarter.restart_agent("forge:tech", "Task: do something useful")

        send_keys_calls = [c for c in calls if "send-keys" in c]
        assert len(send_keys_calls) == 2


# ===========================================================================
# SECTION 22 — _log_restart_event
# ===========================================================================


class TestLogRestartEvent:
    def test_appends_json_line(self, restarter: AgentRestarter, tmp_forge_root: Path) -> None:
        event = RestartEvent(
            agent_id="forge:tech-new",
            reason="stale_agent",
            recovered_task="Task: work",
            new_session_id="forge:tech-new",
            old_session_id="forge:tech",
        )
        restarter._log_restart_event(event)
        log = tmp_forge_root / ".forge/fleet" / "restart.log"
        assert log.exists()
        data = json.loads(log.read_text().strip())
        assert data["old_session"] == "forge:tech"
        assert data["reason"] == "stale_agent"

    def test_handles_write_error_gracefully(self, restarter: AgentRestarter) -> None:
        event = RestartEvent(
            agent_id="a", reason="r", recovered_task=None, new_session_id="a-new"
        )
        with patch("builtins.open", side_effect=OSError("disk full")):
            restarter._log_restart_event(event)  # should not raise

    def test_multiple_events_appended(
        self, restarter: AgentRestarter, tmp_forge_root: Path
    ) -> None:
        for i in range(3):
            event = RestartEvent(
                agent_id=f"forge:a{i}",
                reason="stale_agent",
                recovered_task=None,
                new_session_id=f"forge:a{i}-new",
                old_session_id=f"forge:a{i}",
            )
            restarter._log_restart_event(event)

        log = tmp_forge_root / ".forge/fleet" / "restart.log"
        lines = [ln for ln in log.read_text().strip().split("\n") if ln.strip()]
        assert len(lines) == 3


# ===========================================================================
# SECTION 23 — auto_restart_stale
# ===========================================================================


class TestAutoRestartStale:
    def test_returns_empty_when_no_stale(self, restarter: AgentRestarter) -> None:
        with patch.object(restarter, "detect_stale_agents", return_value=[]):
            assert restarter.auto_restart_stale() == []

    def test_dry_run_returns_empty(self, restarter: AgentRestarter) -> None:
        health = HealthStatus(
            agent_id="forge:stale",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
        )
        with patch.object(restarter, "detect_stale_agents", return_value=[health]):
            assert restarter.auto_restart_stale(dry_run=True) == []

    def test_restarts_stale_agents(self, restarter: AgentRestarter) -> None:
        health = HealthStatus(
            agent_id="forge:stale",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
        )
        task_state = TaskState(task_description="Implement auth module", progress="step 2")
        fake_event = RestartEvent(
            agent_id="forge:stale-new",
            reason="stale_agent",
            recovered_task="Task: Implement auth module",
            new_session_id="forge:stale-new",
            old_session_id="forge:stale",
        )
        with patch.object(restarter, "detect_stale_agents", return_value=[health]):
            with patch.object(restarter, "recover_task_state", return_value=task_state):
                with patch.object(restarter, "restart_agent", return_value=fake_event):
                    events = restarter.auto_restart_stale()
        assert len(events) == 1
        assert events[0].old_session_id == "forge:stale"

    def test_uses_current_task_when_recovery_fails(self, restarter: AgentRestarter) -> None:
        health = HealthStatus(
            agent_id="forge:stale",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
            current_task="some known task description",
        )
        fake_event = RestartEvent(
            agent_id="forge:stale-new",
            reason="stale_agent",
            recovered_task=None,
            new_session_id="forge:stale-new",
            old_session_id="forge:stale",
        )
        with patch.object(restarter, "detect_stale_agents", return_value=[health]):
            with patch.object(restarter, "recover_task_state", return_value=None):
                with patch.object(restarter, "restart_agent", return_value=fake_event) as mock_r:
                    restarter.auto_restart_stale()
        call_context = mock_r.call_args[0][1]
        assert "some known task description" in call_context

    def test_continues_after_restart_failure(self, restarter: AgentRestarter) -> None:
        h1 = HealthStatus(
            agent_id="forge:stale1",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
        )
        h2 = HealthStatus(
            agent_id="forge:stale2",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
        )
        good_event = RestartEvent(
            agent_id="forge:stale2-new",
            reason="stale_agent",
            recovered_task=None,
            new_session_id="forge:stale2-new",
            old_session_id="forge:stale2",
        )

        def restart_side(sid: str, ctx: str, reason: str = "stale_agent") -> RestartEvent:
            if sid == "forge:stale1":
                raise RuntimeError("tmux failed")
            return good_event

        with patch.object(restarter, "detect_stale_agents", return_value=[h1, h2]):
            with patch.object(restarter, "recover_task_state", return_value=None):
                with patch.object(restarter, "restart_agent", side_effect=restart_side):
                    events = restarter.auto_restart_stale()

        assert len(events) == 1
        assert events[0].old_session_id == "forge:stale2"

    def test_includes_file_and_context_in_recovery(self, restarter: AgentRestarter) -> None:
        health = HealthStatus(
            agent_id="forge:stale",
            status="stale",
            last_activity=datetime.now(UTC) - timedelta(hours=1),
        )
        task_state = TaskState(
            task_description="Fix the login bug",
            progress="Step 1",
            file_being_edited="auth.py",
            context_remaining=40,
        )
        fake_event = RestartEvent(
            agent_id="forge:stale-new",
            reason="stale_agent",
            recovered_task="Task: Fix the login bug",
            new_session_id="forge:stale-new",
        )
        with patch.object(restarter, "detect_stale_agents", return_value=[health]):
            with patch.object(restarter, "recover_task_state", return_value=task_state):
                with patch.object(restarter, "restart_agent", return_value=fake_event) as mock_r:
                    restarter.auto_restart_stale()

        ctx = mock_r.call_args[0][1]
        assert "auth.py" in ctx
        assert "40" in ctx


# ===========================================================================
# SECTION 24 — get_restart_history
# ===========================================================================


class TestGetRestartHistory:
    def test_returns_empty_when_no_log(
        self, restarter: AgentRestarter, tmp_forge_root: Path
    ) -> None:
        assert not restarter.restart_log.exists()
        assert restarter.get_restart_history() == []

    def test_returns_parsed_events(
        self, restarter: AgentRestarter, tmp_forge_root: Path
    ) -> None:
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00",
                "old_session": "forge:a",
                "new_session": "forge:a-new",
                "reason": "stale_agent",
                "recovered_task": None,
            },
            {
                "timestamp": "2026-01-01T00:01:00",
                "old_session": "forge:b",
                "new_session": "forge:b-new",
                "reason": "manual",
                "recovered_task": "Task: something",
            },
        ]
        restarter.restart_log.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n"
        )
        result = restarter.get_restart_history()
        assert len(result) == 2
        assert result[0]["old_session"] == "forge:a"

    def test_limit_returns_most_recent(
        self, restarter: AgentRestarter, tmp_forge_root: Path
    ) -> None:
        entries = [
            {
                "timestamp": f"2026-01-01T00:0{i}:00",
                "old_session": f"forge:a{i}",
                "new_session": f"forge:a{i}-new",
                "reason": "stale",
                "recovered_task": None,
            }
            for i in range(5)
        ]
        restarter.restart_log.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n"
        )
        result = restarter.get_restart_history(limit=3)
        assert len(result) == 3
        assert result[0]["old_session"] == "forge:a2"

    def test_handles_read_error(
        self, restarter: AgentRestarter, tmp_forge_root: Path
    ) -> None:
        restarter.restart_log.write_text("")
        with patch("builtins.open", side_effect=OSError("permission denied")):
            assert restarter.get_restart_history() == []

    def test_skips_blank_lines(
        self, restarter: AgentRestarter, tmp_forge_root: Path
    ) -> None:
        entry = {
            "timestamp": "t",
            "old_session": "o",
            "new_session": "n",
            "reason": "r",
            "recovered_task": None,
        }
        restarter.restart_log.write_text("\n\n" + json.dumps(entry) + "\n\n")
        assert len(restarter.get_restart_history()) == 1

    def test_default_limit_is_ten(
        self, restarter: AgentRestarter, tmp_forge_root: Path
    ) -> None:
        entries = [
            {
                "timestamp": "t",
                "old_session": f"o{i}",
                "new_session": f"n{i}",
                "reason": "r",
                "recovered_task": None,
            }
            for i in range(20)
        ]
        restarter.restart_log.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n"
        )
        assert len(restarter.get_restart_history()) == 10


# ===========================================================================
# SECTION 25 — create_agent_restarter factory
# ===========================================================================


class TestCreateAgentRestarter:
    def test_returns_instance(self, tmp_forge_root: Path) -> None:
        assert isinstance(create_agent_restarter(forge_root=tmp_forge_root), AgentRestarter)

    def test_custom_threshold(self, tmp_forge_root: Path) -> None:
        r = create_agent_restarter(forge_root=tmp_forge_root, stale_threshold_minutes=60)
        assert r.stale_threshold == timedelta(minutes=60)

    def test_default_threshold_thirty(self, tmp_forge_root: Path) -> None:
        r = create_agent_restarter(forge_root=tmp_forge_root)
        assert r.stale_threshold == timedelta(minutes=30)

    def test_forge_root_stored(self, tmp_forge_root: Path) -> None:
        r = create_agent_restarter(forge_root=tmp_forge_root)
        assert r.forge_root == tmp_forge_root

    def test_none_forge_root_accepted(self, tmp_path: Path) -> None:
        with patch("forge_harness.fleet.restart.Path.cwd", return_value=tmp_path):
            r = create_agent_restarter(forge_root=None)
        assert isinstance(r, AgentRestarter)


# ===========================================================================
# SECTION 26 — Cross-module integration scenarios
# ===========================================================================


class TestCrossModuleIntegration:
    """Integration scenarios that exercise both metrics and restart together."""

    def test_metrics_recorded_and_restart_triggered(
        self, fleet_metrics: FleetMetrics, restarter: AgentRestarter
    ) -> None:
        """Record metrics for an agent; verify we can still restart another agent."""
        fleet_metrics.record_task_start("agent-1", "task-A", task_type="feature")
        fleet_metrics.record_task_end("task-A", outcome="success")
        report = fleet_metrics.get_metrics(days=7)
        assert report.total_tasks == 1

        # Independently verify restarter works
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            event = restarter.restart_agent("forge:agent-1", "Task: continue from task-A")
        assert isinstance(event, RestartEvent)

    def test_health_status_and_metrics_report_coexist(
        self, fleet_metrics: FleetMetrics
    ) -> None:
        """HealthStatus dataclass and MetricsReport.to_dict are independently usable."""
        now = datetime.now(UTC)
        health = HealthStatus(
            agent_id="forge:worker",
            status="healthy",
            last_activity=now,
            context_percent=0.45,
        )
        fleet_metrics.record_task_start("forge:worker", "task-x", task_type="feature")
        fleet_metrics.record_task_end("task-x", outcome="success")
        report = fleet_metrics.get_metrics(days=7)

        assert health.agent_id == "forge:worker"
        assert health.context_percent == pytest.approx(0.45)
        assert report.total_tasks == 1
        assert report.success_rate == 1.0

    def test_task_state_and_agent_metrics_to_dict(
        self, fleet_metrics: FleetMetrics
    ) -> None:
        """TaskState (restart) and AgentMetrics.to_dict (metrics) can be serialized."""
        fleet_metrics.record_task_start("worker", "t1", task_type="bugfix")
        fleet_metrics.record_task_end("t1", outcome="failure")
        agent_m = fleet_metrics.get_agent_metrics("worker")
        d = agent_m.to_dict()

        task_state = TaskState(
            task_description="Fix the failing test",
            progress="50%",
            context_remaining=30,
            file_being_edited="tests/test_fleet.py",
        )

        assert d["failed_tasks"] == 1
        assert task_state.file_being_edited == "tests/test_fleet.py"

    def test_metrics_cleanup_does_not_affect_restarter_log(
        self, fleet_metrics: FleetMetrics, restarter: AgentRestarter, tmp_forge_root: Path
    ) -> None:
        """Cleaning old metrics does not corrupt restart logs."""
        fleet_metrics.record_task_start("a", "old-task")
        fleet_metrics.cleanup_old_metrics(days=0)  # delete everything

        # Restart log should still be appendable
        event = RestartEvent(
            agent_id="a-new",
            reason="test",
            recovered_task=None,
            new_session_id="a-new",
            old_session_id="a",
        )
        restarter._log_restart_event(event)
        assert restarter.restart_log.exists()
        data = json.loads(restarter.restart_log.read_text().strip())
        assert data["old_session"] == "a"
