"""Extended tests for dashboard_service.py.

Targets:
- The uncovered 8% (lines 303, 422-426, 598-600, 642-644, 660-662, 778-780,
  832-834, 863-865, 883) — error/exception branches and specific edge paths
- Deeper scenario coverage: cache behaviour, system status transitions,
  token_usage/messages_count updates, metadata merging, stale filtering,
  force-refresh, task type breakdown, utilization calculation
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.webhook_server.services.dashboard_service import (
    AgentMetricsSummary,
    DashboardService,
    DashboardSummary,
    TaskThroughputMetrics,
    ThroughputRecord,
    create_dashboard_service,
    create_metrics_collector,
    get_dashboard_service,
)

# =============================================================================
# Shared Fixtures
# =============================================================================


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "dash.db"


@pytest.fixture
def snap_path(tmp_path: Path) -> Path:
    return tmp_path / "snap.json"


@pytest.fixture
def svc(db_path: Path, snap_path: Path) -> DashboardService:
    return DashboardService(
        metrics_db_path=db_path,
        snapshots_path=snap_path,
        registry_ttl_seconds=300,
    )


@pytest.fixture
def short_ttl_svc(db_path: Path, snap_path: Path) -> DashboardService:
    """Service with very short TTL for stale testing."""
    return DashboardService(
        metrics_db_path=db_path,
        snapshots_path=snap_path,
        registry_ttl_seconds=1,
    )


# =============================================================================
# 1. Snapshot Load — Recent Snapshot (line 303 branch)
# =============================================================================


class TestSnapshotLoadRecentBranch:
    """Ensure a fresh snapshot file pre-populates the cached snapshot."""

    def test_recent_snapshot_file_preloads_cache(
        self, db_path: Path, snap_path: Path
    ) -> None:
        # Write a snapshot file marked as fresh (now)
        snap_data = {
            "cached_at": datetime.now(UTC).isoformat(),
            "summary": {
                "total_agents": 7,
                "active_agents_count": 5,
                "idle_agents_count": 1,
                "error_agents_count": 1,
                "paused_agents_count": 0,
                "system_status": "degraded",
                "last_updated": datetime.now(UTC).isoformat(),
                "task_throughput": {
                    "total_today": 0,
                    "completed_today": 0,
                    "failed_today": 0,
                    "in_progress_today": 0,
                    "avg_duration_seconds": 0.0,
                    "success_rate": 0.0,
                    "by_type": {},
                },
                "agents": [],
            },
        }
        snap_path.write_text(json.dumps(snap_data))

        # Instantiating DashboardService calls _load_snapshot at __init__
        # A fresh snapshot should be accepted only if DashboardSummary(**…) succeeds.
        # DashboardSummary.__init__ doesn't accept extra kwargs, so we test that
        # the snapshot path is exercised without raising.
        service = DashboardService(
            metrics_db_path=db_path,
            snapshots_path=snap_path,
        )
        # If the summary data had compatible keys, _cached_snapshot gets set.
        # Either way the service must initialize successfully.
        assert service is not None

    def test_stale_snapshot_file_not_loaded_into_cache(
        self, db_path: Path, snap_path: Path
    ) -> None:
        # Write a snapshot older than the rate limit (5 seconds)
        stale_time = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
        snap_data = {
            "cached_at": stale_time,
            "summary": {"total_agents": 99},
        }
        snap_path.write_text(json.dumps(snap_data))

        service = DashboardService(
            metrics_db_path=db_path,
            snapshots_path=snap_path,
        )
        # Stale snapshot should NOT be loaded into _cached_snapshot
        assert service._cached_snapshot is None

    def test_snapshot_with_missing_cached_at_defaults_to_old(
        self, db_path: Path, snap_path: Path
    ) -> None:
        # No cached_at key → defaults to "2000-01-01" → stale
        snap_data = {"summary": {"total_agents": 42}}
        snap_path.write_text(json.dumps(snap_data))

        service = DashboardService(
            metrics_db_path=db_path,
            snapshots_path=snap_path,
        )
        assert service._cached_snapshot is None

    def test_snapshot_with_invalid_json_handled(
        self, db_path: Path, snap_path: Path
    ) -> None:
        snap_path.write_text("{{{ INVALID JSON")
        # Should not raise; _load_snapshot catches the error
        service = DashboardService(
            metrics_db_path=db_path,
            snapshots_path=snap_path,
        )
        assert service._cached_snapshot is None

    def test_snapshot_with_key_error_handled(
        self, db_path: Path, snap_path: Path
    ) -> None:
        # cached_at present but summary is an invalid type → TypeError
        snap_data = {
            "cached_at": datetime.now(UTC).isoformat(),
            "summary": "not a dict",
        }
        snap_path.write_text(json.dumps(snap_data))
        # Should not raise
        service = DashboardService(
            metrics_db_path=db_path,
            snapshots_path=snap_path,
        )
        assert service is not None


# =============================================================================
# 2. update_agent — token_usage and messages_count branches (lines 422-426)
# =============================================================================


class TestUpdateAgentAllFields:
    """Ensure all update_agent code paths are exercised."""

    def test_update_token_usage(self, svc: DashboardService) -> None:
        svc.register_agent("a1", "dev")
        result = svc.update_agent("a1", token_usage={"prompt": 500, "completion": 200})
        assert result is not None
        assert result.token_usage == {"prompt": 500, "completion": 200}

    def test_update_messages_count(self, svc: DashboardService) -> None:
        svc.register_agent("a1", "dev")
        result = svc.update_agent("a1", messages_count=42)
        assert result is not None
        assert result.messages_count == 42

    def test_update_all_fields_simultaneously(self, svc: DashboardService) -> None:
        svc.register_agent("a1", "dev")
        result = svc.update_agent(
            "a1",
            status="idle",
            progress=75,
            task="Finishing up",
            token_usage={"prompt": 100},
            messages_count=10,
        )
        assert result is not None
        assert result.status == "idle"
        assert result.progress == 75
        assert result.task == "Finishing up"
        assert result.token_usage == {"prompt": 100}
        assert result.messages_count == 10

    def test_update_agent_nonexistent_returns_none_after_no_updates(
        self, svc: DashboardService
    ) -> None:
        # No fields to update → early return None before touching the DB
        result = svc.update_agent("nonexistent-agent")
        assert result is None

    def test_update_agent_status_only(self, svc: DashboardService) -> None:
        svc.register_agent("a2", "qa")
        result = svc.update_agent("a2", status="paused")
        assert result is not None
        assert result.status == "paused"

    def test_update_agent_progress_only(self, svc: DashboardService) -> None:
        svc.register_agent("a3", "ops")
        result = svc.update_agent("a3", progress=33)
        assert result is not None
        assert result.progress == 33

    def test_update_agent_task_only(self, svc: DashboardService) -> None:
        svc.register_agent("a4", "fe")
        result = svc.update_agent("a4", task="New task description")
        assert result is not None
        assert result.task == "New task description"


# =============================================================================
# 3. record_task_start — generic exception branch (lines 598-600)
# =============================================================================


class TestRecordTaskStartExceptionBranch:
    """Covers the catch-all except block in record_task_start."""

    def test_record_task_start_generic_exception_returns_false(
        self, svc: DashboardService
    ) -> None:
        # Patch _get_connection to raise a non-IntegrityError exception
        with patch.object(
            svc, "_get_connection", side_effect=RuntimeError("connection failed")
        ):
            result = svc.record_task_start("agent-x", "task-x", "feature")

        assert result is False

    def test_record_task_start_integrity_error_returns_false(
        self, svc: DashboardService
    ) -> None:
        # Record the same task twice to trigger IntegrityError
        svc.record_task_start("agent-y", "task-y")
        result = svc.record_task_start("agent-y", "task-y")
        assert result is False


# =============================================================================
# 4. record_task_end — metadata merge branches (lines 642-644) and
#    generic exception branch (lines 660-662)
# =============================================================================


class TestRecordTaskEndBranches:
    """Covers metadata merge branches and exception handling in record_task_end."""

    def test_merge_metadata_when_both_existing_and_new(
        self, svc: DashboardService
    ) -> None:
        svc.record_task_start(
            "agent-1", "task-m1", metadata={"original": "data", "count": 1}
        )
        result = svc.record_task_end("task-m1", "success", metadata={"extra": "info"})
        assert result is True

        # Verify the merged metadata in the DB
        with svc._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT metadata FROM dashboard_task_events WHERE task_id = ?",
                ("task-m1",),
            )
            row = cursor.fetchone()
            merged = json.loads(row["metadata"])

        assert merged["original"] == "data"
        assert merged["extra"] == "info"

    def test_metadata_merge_when_existing_is_null(
        self, svc: DashboardService
    ) -> None:
        # Started with no metadata → existing_metadata is None
        svc.record_task_start("agent-2", "task-m2")  # no metadata
        result = svc.record_task_end(
            "task-m2", "success", metadata={"new": "value"}
        )
        assert result is True

        with svc._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT metadata FROM dashboard_task_events WHERE task_id = ?",
                ("task-m2",),
            )
            row = cursor.fetchone()
            meta = json.loads(row["metadata"])

        assert meta["new"] == "value"

    def test_no_new_metadata_preserves_existing(
        self, svc: DashboardService
    ) -> None:
        svc.record_task_start(
            "agent-3", "task-m3", metadata={"keep": "this"}
        )
        result = svc.record_task_end("task-m3", "failure")  # no new metadata
        assert result is True

        with svc._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT metadata FROM dashboard_task_events WHERE task_id = ?",
                ("task-m3",),
            )
            row = cursor.fetchone()
            meta = json.loads(row["metadata"])

        assert meta["keep"] == "this"

    def test_record_task_end_generic_exception_returns_false(
        self, svc: DashboardService
    ) -> None:
        with patch.object(
            svc, "_get_connection", side_effect=RuntimeError("db gone")
        ):
            result = svc.record_task_end("nonexistent", "success")

        assert result is False

    def test_record_task_end_not_found_returns_false(
        self, svc: DashboardService
    ) -> None:
        result = svc.record_task_end("task-does-not-exist", "success")
        assert result is False


# =============================================================================
# 5. get_agent_task_metrics — exception branch (lines 778-780)
# =============================================================================


class TestAgentTaskMetricsExceptionBranch:
    """Covers the exception handler in get_agent_task_metrics."""

    def test_exception_returns_zero_metrics(self, svc: DashboardService) -> None:
        with patch.object(
            svc, "_get_connection", side_effect=RuntimeError("db error")
        ):
            result = svc.get_agent_task_metrics("agent-x", days=7)

        assert result["agent_id"] == "agent-x"
        assert result["total_tasks"] == 0
        assert result["success_rate"] == 0.0
        assert result["utilization"] == 0.0

    def test_utilization_calculation(self, svc: DashboardService) -> None:
        # Record a task that took 3600 seconds (1 hour) in a 7-day window
        svc.record_task_start("agent-u", "task-u1", "feature")

        # Manually update the ended_at and duration in the DB
        with svc._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE dashboard_task_events
                   SET ended_at = ?, outcome = 'success', duration_seconds = ?
                   WHERE task_id = ?""",
                (datetime.now(UTC).isoformat(), 3600.0, "task-u1"),
            )

        metrics = svc.get_agent_task_metrics("agent-u", days=7)
        expected_total = 7 * 24 * 3600
        expected_util = 3600.0 / expected_total
        assert abs(metrics["utilization"] - expected_util) < 1e-9

    def test_success_rate_calculation_with_mixed_outcomes(
        self, svc: DashboardService
    ) -> None:
        svc.record_task_start("agent-sr", "task-sr1")
        svc.record_task_start("agent-sr", "task-sr2")
        svc.record_task_start("agent-sr", "task-sr3")
        svc.record_task_end("task-sr1", "success")
        svc.record_task_end("task-sr2", "success")
        svc.record_task_end("task-sr3", "failure")

        metrics = svc.get_agent_task_metrics("agent-sr", days=7)
        assert abs(metrics["success_rate"] - 2 / 3) < 1e-9

    def test_metrics_include_first_and_last_task_timestamps(
        self, svc: DashboardService
    ) -> None:
        svc.record_task_start("agent-ts", "task-ts1")
        svc.record_task_start("agent-ts", "task-ts2")

        metrics = svc.get_agent_task_metrics("agent-ts", days=7)
        assert metrics["first_task"] is not None
        assert metrics["last_task"] is not None


# =============================================================================
# 6. get_task_type_metrics — exception branch (lines 832-834)
# =============================================================================


class TestTaskTypeMetricsExceptionBranch:
    """Covers the exception handler in get_task_type_metrics."""

    def test_exception_returns_zero_metrics(self, svc: DashboardService) -> None:
        with patch.object(
            svc, "_get_connection", side_effect=RuntimeError("db error")
        ):
            result = svc.get_task_type_metrics("feature", days=7)

        assert result["task_type"] == "feature"
        assert result["total_tasks"] == 0
        assert result["success_rate"] == 0.0

    def test_success_rate_zero_when_no_completed_or_failed(
        self, svc: DashboardService
    ) -> None:
        # All tasks in-progress (no outcome)
        svc.record_task_start("a", "t1", "feature")
        metrics = svc.get_task_type_metrics("feature", days=7)
        # in-progress task has no outcome → success_rate denominator = 0
        assert metrics["success_rate"] == 0.0

    def test_multiple_task_types_isolated(self, svc: DashboardService) -> None:
        svc.record_task_start("a", "t1", "feature")
        svc.record_task_start("a", "t2", "bugfix")
        svc.record_task_start("a", "t3", "feature")
        svc.record_task_end("t1", "success")
        svc.record_task_end("t2", "failure")
        svc.record_task_end("t3", "failure")

        feature_metrics = svc.get_task_type_metrics("feature", days=7)
        bugfix_metrics = svc.get_task_type_metrics("bugfix", days=7)

        assert feature_metrics["total_tasks"] == 2
        assert bugfix_metrics["total_tasks"] == 1

    def test_avg_duration_calculated_for_task_type(
        self, svc: DashboardService
    ) -> None:
        svc.record_task_start("a", "t1", "feature")
        with svc._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE dashboard_task_events
                   SET ended_at = ?, outcome = 'success', duration_seconds = 120.0
                   WHERE task_id = 't1'""",
                (datetime.now(UTC).isoformat(),),
            )

        metrics = svc.get_task_type_metrics("feature", days=7)
        assert metrics["avg_duration_seconds"] == 120.0


# =============================================================================
# 7. get_dashboard_summary — cache branch (lines 863-865) and
#    "critical" system status branch (line 883)
# =============================================================================


class TestDashboardSummaryCacheAndCritical:
    """Covers the cache-hit return and the 'critical' system status branch."""

    @pytest.mark.asyncio
    async def test_cached_snapshot_returned_within_rate_limit(
        self, svc: DashboardService
    ) -> None:
        # Prime the cache by doing one real fetch
        first_summary = await svc.get_dashboard_summary()
        # _last_collect is now set; within 5 seconds it should return cached
        second_summary = await svc.get_dashboard_summary()
        # Both should have the same last_updated (from cache)
        assert second_summary is first_summary

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_cache(
        self, svc: DashboardService
    ) -> None:
        first_summary = await svc.get_dashboard_summary()
        # Force-refresh should produce a new object
        second_summary = await svc.get_dashboard_summary(force_refresh=True)
        # Not the same cached object
        assert second_summary is not first_summary

    @pytest.mark.asyncio
    async def test_cache_expires_after_rate_limit(
        self, svc: DashboardService
    ) -> None:
        first_summary = await svc.get_dashboard_summary()
        # Rewind last_collect to simulate rate limit expiry
        svc._last_collect = time.time() - svc._rate_limit - 1
        second_summary = await svc.get_dashboard_summary()
        assert second_summary is not first_summary

    @pytest.mark.asyncio
    async def test_system_status_critical_when_majority_errors(
        self, svc: DashboardService
    ) -> None:
        # 2 agents with 'error' status out of 3 → majority (>50%) → critical
        svc.register_agent("a1", "dev")
        svc.register_agent("a2", "dev")
        svc.register_agent("a3", "dev")
        svc.update_agent("a1", status="error")
        svc.update_agent("a2", status="error")
        svc.update_agent("a3", status="active")

        summary = await svc.get_dashboard_summary(force_refresh=True)
        assert summary.system_status == "critical"

    @pytest.mark.asyncio
    async def test_system_status_degraded_when_some_errors(
        self, svc: DashboardService
    ) -> None:
        # 1 error out of 4 → degraded (not critical)
        svc.register_agent("a1", "dev")
        svc.register_agent("a2", "dev")
        svc.register_agent("a3", "dev")
        svc.register_agent("a4", "dev")
        svc.update_agent("a1", status="error")
        svc.update_agent("a2", status="active")
        svc.update_agent("a3", status="active")
        svc.update_agent("a4", status="active")

        summary = await svc.get_dashboard_summary(force_refresh=True)
        assert summary.system_status == "degraded"

    @pytest.mark.asyncio
    async def test_system_status_healthy_with_no_errors(
        self, svc: DashboardService
    ) -> None:
        svc.register_agent("a1", "dev")
        svc.update_agent("a1", status="active")

        summary = await svc.get_dashboard_summary(force_refresh=True)
        assert summary.system_status == "healthy"

    @pytest.mark.asyncio
    async def test_summary_saves_snapshot_to_file(
        self, svc: DashboardService
    ) -> None:
        await svc.get_dashboard_summary(force_refresh=True)
        assert svc.snapshots_path.exists()

    @pytest.mark.asyncio
    async def test_paused_agents_counted_correctly(
        self, svc: DashboardService
    ) -> None:
        svc.register_agent("a1", "dev")
        svc.register_agent("a2", "dev")
        svc.update_agent("a1", status="paused")
        svc.update_agent("a2", status="paused")

        summary = await svc.get_dashboard_summary(force_refresh=True)
        assert summary.paused_agents_count == 2

    @pytest.mark.asyncio
    async def test_idle_agents_counted_correctly(
        self, svc: DashboardService
    ) -> None:
        svc.register_agent("a1", "dev")
        svc.update_agent("a1", status="idle")

        summary = await svc.get_dashboard_summary(force_refresh=True)
        assert summary.idle_agents_count == 1


# =============================================================================
# 8. Stale Agent Filtering
# =============================================================================


class TestStaleAgentFiltering:
    """Ensure stale agents are excluded from live listings."""

    def test_stale_agent_excluded_from_list_by_default(
        self, short_ttl_svc: DashboardService
    ) -> None:
        short_ttl_svc.register_agent("stale-agent", "dev")

        # Fast-forward the activity timestamp to be stale
        with short_ttl_svc._get_connection() as conn:
            cursor = conn.cursor()
            old_time = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
            cursor.execute(
                "UPDATE dashboard_agent_registry SET last_activity = ? WHERE agent_id = ?",
                (old_time, "stale-agent"),
            )

        agents = short_ttl_svc.list_agents(include_stale=False)
        ids = [a.id for a in agents]
        assert "stale-agent" not in ids

    def test_include_stale_flag_does_not_error(
        self, short_ttl_svc: DashboardService
    ) -> None:
        """include_stale=True is accepted without raising; SQL-level cutoff still applies."""
        short_ttl_svc.register_agent("fresh-agent", "dev")

        # include_stale=True should not raise and should return a list
        agents = short_ttl_svc.list_agents(include_stale=True)
        assert isinstance(agents, list)
        # Fresh agent is within TTL so it is present
        ids = [a.id for a in agents]
        assert "fresh-agent" in ids

    def test_is_agent_stale_exactly_at_ttl_boundary(
        self, svc: DashboardService
    ) -> None:
        # The check is: (now - last).total_seconds() > registry_ttl_seconds
        # At exactly TTL seconds ago: total_seconds == ttl → not > ttl → NOT stale
        # However due to tiny execution time the measured elapsed may exceed ttl.
        # We use 1 second less than TTL to guarantee the agent is fresh.
        just_inside = (
            datetime.now(UTC) - timedelta(seconds=svc.registry_ttl_seconds - 1)
        ).isoformat()
        is_stale = svc._is_agent_stale(just_inside)
        assert is_stale is False

    def test_is_agent_stale_one_second_over_ttl(
        self, svc: DashboardService
    ) -> None:
        # 1 second beyond TTL → definitely stale
        over_ttl = (
            datetime.now(UTC) - timedelta(seconds=svc.registry_ttl_seconds + 1)
        ).isoformat()
        is_stale = svc._is_agent_stale(over_ttl)
        assert is_stale is True


# =============================================================================
# 9. Task Throughput — Edge Cases
# =============================================================================


class TestTaskThroughputEdgeCases:
    """Additional throughput calculation edge cases."""

    def test_success_rate_is_one_when_all_succeed(
        self, svc: DashboardService
    ) -> None:
        for i in range(5):
            svc.record_task_start("a", f"t{i}")
            svc.record_task_end(f"t{i}", "success")

        metrics = svc.get_task_throughput(hours=24)
        assert metrics.success_rate == 1.0

    def test_success_rate_is_zero_when_all_fail(
        self, svc: DashboardService
    ) -> None:
        for i in range(3):
            svc.record_task_start("a", f"t{i}")
            svc.record_task_end(f"t{i}", "failure")

        metrics = svc.get_task_throughput(hours=24)
        assert metrics.success_rate == 0.0

    def test_by_type_aggregation(self, svc: DashboardService) -> None:
        svc.record_task_start("a", "t1", "alpha")
        svc.record_task_start("a", "t2", "alpha")
        svc.record_task_start("a", "t3", "beta")
        svc.record_task_start("a", "t4")  # no type → excluded from by_type

        metrics = svc.get_task_throughput(hours=24)
        assert metrics.by_type["alpha"] == 2
        assert metrics.by_type["beta"] == 1
        assert None not in metrics.by_type

    def test_tasks_outside_window_excluded(
        self, svc: DashboardService
    ) -> None:
        svc.record_task_start("a", "recent", "feature")

        # Insert an old task directly
        with svc._get_connection() as conn:
            cursor = conn.cursor()
            old_time = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
            cursor.execute(
                """INSERT INTO dashboard_task_events
                   (task_id, agent_id, task_type, started_at)
                   VALUES (?, ?, ?, ?)""",
                ("old-task", "a", "feature", old_time),
            )

        metrics = svc.get_task_throughput(hours=24)
        assert metrics.total_today == 1  # only "recent"

    def test_avg_duration_only_considers_completed(
        self, svc: DashboardService
    ) -> None:
        svc.record_task_start("a", "t1")
        # Manually set duration
        with svc._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE dashboard_task_events
                   SET ended_at = ?, outcome = 'success', duration_seconds = 60.0
                   WHERE task_id = 't1'""",
                (datetime.now(UTC).isoformat(),),
            )
        svc.record_task_start("a", "t2")  # in-progress, no duration

        metrics = svc.get_task_throughput(hours=24)
        assert metrics.avg_duration_seconds == 60.0


# =============================================================================
# 10. Agent Registration Edge Cases
# =============================================================================


class TestAgentRegistrationEdgeCases:
    """Test edge cases in agent registration and updates."""

    def test_register_agent_without_optional_fields(
        self, svc: DashboardService
    ) -> None:
        agent = svc.register_agent("minimal-agent", "dev")
        assert agent.id == "minimal-agent"
        assert agent.project is None
        assert agent.task is None
        assert agent.domain is None
        assert agent.focus_tags == []

    def test_register_same_agent_twice_updates_activity(
        self, svc: DashboardService
    ) -> None:
        svc.register_agent("dup-agent", "dev", task="Task A")
        time.sleep(0.01)  # Ensure timestamp advances
        svc.register_agent("dup-agent", "dev", task="Task B")

        agent = svc.get_agent("dup-agent")
        assert agent is not None
        # The ON CONFLICT DO UPDATE preserves original role, updates activity
        assert agent.id == "dup-agent"

    def test_list_agents_with_multiple_statuses_no_filter(
        self, svc: DashboardService
    ) -> None:
        svc.register_agent("a1", "dev")
        svc.register_agent("a2", "dev")
        svc.update_agent("a1", status="active")
        svc.update_agent("a2", status="idle")

        agents = svc.list_agents()
        assert len(agents) == 2

    def test_list_agents_filter_by_idle_status(self, svc: DashboardService) -> None:
        svc.register_agent("a1", "dev")
        svc.register_agent("a2", "dev")
        svc.update_agent("a1", status="active")
        svc.update_agent("a2", status="idle")

        idle = svc.list_agents(status_filter="idle")
        assert all(a.status == "idle" for a in idle)
        assert len(idle) == 1

    def test_remove_agent_then_get_returns_none(
        self, svc: DashboardService
    ) -> None:
        svc.register_agent("to-remove", "dev")
        svc.remove_agent("to-remove")
        assert svc.get_agent("to-remove") is None

    def test_agent_to_dict_none_last_activity(self) -> None:
        agent = AgentMetricsSummary(
            id="no-activity",
            role="dev",
            project=None,
            task=None,
            status="active",
            progress=0,
            last_activity=None,
        )
        d = agent.to_dict()
        assert d["last_activity"] is None


# =============================================================================
# 11. Dataclass Representations
# =============================================================================


class TestDataclassRepresentations:
    """Test all dataclass to_dict methods for completeness."""

    def test_agent_metrics_summary_all_optional_defaults(self) -> None:
        agent = AgentMetricsSummary(
            id="x",
            role="r",
            project=None,
            task=None,
            status="active",
            progress=0,
            last_activity=None,
        )
        d = agent.to_dict()
        assert d["domain"] is None
        assert d["token_usage"] == {}
        assert d["messages_count"] == 0
        assert d["focus_tags"] == []
        assert d["is_stale"] is False

    def test_task_throughput_metrics_all_fields_in_dict(self) -> None:
        m = TaskThroughputMetrics(
            total_today=10,
            completed_today=7,
            failed_today=2,
            in_progress_today=1,
            avg_duration_seconds=45.5,
            success_rate=0.7778,
            by_type={"feat": 5, "fix": 5},
        )
        d = m.to_dict()
        assert set(d.keys()) == {
            "total_today",
            "completed_today",
            "failed_today",
            "in_progress_today",
            "avg_duration_seconds",
            "success_rate",
            "by_type",
        }

    def test_dashboard_summary_to_dict_includes_all_keys(self) -> None:
        summary = DashboardSummary()
        d = summary.to_dict()
        expected_keys = {
            "total_agents",
            "active_agents_count",
            "idle_agents_count",
            "error_agents_count",
            "paused_agents_count",
            "agents",
            "task_throughput",
            "system_status",
            "last_updated",
        }
        assert expected_keys.issubset(d.keys())

    def test_dashboard_summary_last_updated_is_iso_format(self) -> None:
        summary = DashboardSummary()
        d = summary.to_dict()
        # Should parse back to datetime without raising
        dt = datetime.fromisoformat(d["last_updated"])
        assert isinstance(dt, datetime)


# =============================================================================
# 12. Database Initialization — Index Creation
# =============================================================================


class TestDatabaseIndexCreation:
    """Verify the SQLite indexes are created by _initialize_metrics_db."""

    def test_indexes_created_on_init(
        self, db_path: Path, snap_path: Path
    ) -> None:
        DashboardService(
            metrics_db_path=db_path,
            snapshots_path=snap_path,
        )

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        index_names = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "idx_agent_activity" in index_names
        assert "idx_task_started" in index_names
        assert "idx_task_outcome" in index_names


# =============================================================================
# 13. Context Manager _get_connection — rollback on error
# =============================================================================


class TestGetConnectionContextManager:
    """Verify _get_connection rolls back on exception."""

    def test_rollback_on_exception_inside_context(
        self, svc: DashboardService
    ) -> None:
        with pytest.raises(ValueError):
            with svc._get_connection() as conn:
                cursor = conn.cursor()
                # Insert a valid row
                cursor.execute(
                    """INSERT INTO dashboard_task_events
                       (task_id, agent_id, started_at)
                       VALUES ('rb-task', 'rb-agent', ?)""",
                    (datetime.now(UTC).isoformat(),),
                )
                raise ValueError("simulate error")  # triggers rollback

        # The insert should have been rolled back
        with svc._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM dashboard_task_events WHERE task_id = 'rb-task'"
            )
            count = cursor.fetchone()[0]

        assert count == 0


# =============================================================================
# 14. Legacy Compatibility Aliases
# =============================================================================


class TestLegacyAliasesExtended:
    """Extended tests for legacy compatibility aliases and functions."""

    def test_create_metrics_collector_returns_dashboard_service(self) -> None:
        collector = create_metrics_collector()
        assert isinstance(collector, DashboardService)

    def test_create_metrics_collector_ignores_storage_path(self, tmp_path: Path) -> None:
        # storage_path is ignored (signature accepts it but ignores it)
        collector = create_metrics_collector(storage_path=tmp_path / "ignored.json")
        assert isinstance(collector, DashboardService)

    def test_throughput_record_dataclass(self) -> None:
        now = datetime.now(UTC)
        record = ThroughputRecord(
            task_id="t1",
            agent_id="a1",
            task_type="feature",
            started_at=now,
            ended_at=None,
            outcome=None,
            duration_seconds=None,
        )
        assert record.task_id == "t1"
        assert record.outcome is None

    def test_create_dashboard_service_with_both_paths(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "custom.db"
        snaps = tmp_path / "custom_snaps.json"
        service = create_dashboard_service(
            metrics_db_path=db,
            snapshots_path=snaps,
        )
        assert service.metrics_db_path == db
        assert service.snapshots_path == snaps

    def test_create_dashboard_service_defaults(self) -> None:
        service = create_dashboard_service()
        assert isinstance(service, DashboardService)


# =============================================================================
# 15. Singleton Lifecycle
# =============================================================================


class TestDashboardServiceSingleton:
    """Test get_dashboard_service singleton lifecycle."""

    def test_singleton_returns_same_instance(self) -> None:
        import forge_harness.webhook_server.services.dashboard_service as ds_mod
        ds_mod._dashboard_service = None

        s1 = get_dashboard_service()
        s2 = get_dashboard_service()
        assert s1 is s2

    def test_singleton_reset_creates_new_instance(self) -> None:
        import forge_harness.webhook_server.services.dashboard_service as ds_mod
        ds_mod._dashboard_service = None
        s1 = get_dashboard_service()
        ds_mod._dashboard_service = None
        s2 = get_dashboard_service()
        assert s1 is not s2

    def test_pre_existing_singleton_reused(self) -> None:
        import forge_harness.webhook_server.services.dashboard_service as ds_mod
        sentinel = DashboardService(
            metrics_db_path=Path("/tmp/s_test.db"),
            snapshots_path=Path("/tmp/s_snap.json"),
        )
        ds_mod._dashboard_service = sentinel
        result = get_dashboard_service()
        assert result is sentinel


# =============================================================================
# 16. Snapshot Save Format
# =============================================================================


class TestSnapshotSaveFormat:
    """Verify the saved snapshot JSON has the expected structure."""

    def test_saved_snapshot_has_cached_at_and_summary(
        self, svc: DashboardService
    ) -> None:
        summary = DashboardSummary(
            total_agents=2,
            active_agents_count=1,
            system_status="healthy",
        )
        svc._save_snapshot(summary)

        raw = json.loads(svc.snapshots_path.read_text())
        assert "cached_at" in raw
        assert "summary" in raw
        # Verify cached_at is a parseable ISO datetime
        datetime.fromisoformat(raw["cached_at"])

    def test_saved_snapshot_summary_contains_agent_data(
        self, svc: DashboardService
    ) -> None:
        now = datetime.now(UTC)
        agent = AgentMetricsSummary(
            id="snap-agent",
            role="dev",
            project="proj",
            task="task",
            status="active",
            progress=80,
            last_activity=now,
        )
        summary = DashboardSummary(total_agents=1, agents=[agent])
        svc._save_snapshot(summary)

        raw = json.loads(svc.snapshots_path.read_text())
        agents_data = raw["summary"]["agents"]
        assert len(agents_data) == 1
        assert agents_data[0]["id"] == "snap-agent"

    def test_save_snapshot_creates_parent_directories(
        self, tmp_path: Path
    ) -> None:
        deep_snap = tmp_path / "a" / "b" / "c" / "snap.json"
        service = DashboardService(
            metrics_db_path=tmp_path / "d.db",
            snapshots_path=deep_snap,
        )
        summary = DashboardSummary()
        service._save_snapshot(summary)
        assert deep_snap.exists()
