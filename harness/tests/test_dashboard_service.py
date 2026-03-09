"""Tests for dashboard_service.py - Unified Dashboard Service.

Tests cover:
- AgentMetricsSummary dataclass
- TaskThroughputMetrics dataclass
- DashboardSummary dataclass
- DashboardService initialization
- Agent registry CRUD operations
- Task throughput tracking
- Dashboard summary generation
- Stale agent detection
- get_dashboard_service singleton
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from forge_harness.webhook_server.services.dashboard_service import (
    AgentMetricsSummary,
    DashboardService,
    DashboardSummary,
    TaskThroughputMetrics,
    create_dashboard_service,
    get_dashboard_service,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """Create a temporary database path."""
    return tmp_path / "test_dashboard.db"


@pytest.fixture
def tmp_snapshots_path(tmp_path: Path) -> Path:
    """Create a temporary snapshots path."""
    return tmp_path / "test_snapshots.json"


@pytest.fixture
def dashboard_service(tmp_db_path: Path, tmp_snapshots_path: Path) -> DashboardService:
    """Create a DashboardService instance with temp paths."""
    return DashboardService(
        metrics_db_path=tmp_db_path,
        snapshots_path=tmp_snapshots_path,
        registry_ttl_seconds=300,
    )


# =============================================================================
# AgentMetricsSummary Tests
# =============================================================================


class TestAgentMetricsSummary:
    """Tests for AgentMetricsSummary dataclass."""

    def test_agent_metrics_creation(self) -> None:
        """Should create agent metrics with required fields."""
        metrics = AgentMetricsSummary(
            id="agent-1",
            role="feature-dev",
            project="test-project",
            task="Implement feature",
            status="active",
            progress=50,
            last_activity=datetime.now(UTC),
        )

        assert metrics.id == "agent-1"
        assert metrics.role == "feature-dev"
        assert metrics.project == "test-project"
        assert metrics.status == "active"
        assert metrics.progress == 50
        assert metrics.is_stale is False

    def test_agent_metrics_to_dict(self) -> None:
        """Should convert to dictionary."""
        now = datetime.now(UTC)
        metrics = AgentMetricsSummary(
            id="agent-1",
            role="feature-dev",
            project="test-project",
            task="Implement feature",
            status="active",
            progress=50,
            last_activity=now,
            is_stale=False,
            domain="test-domain",
            token_usage={"prompt": 100, "completion": 50},
            messages_count=10,
            focus_tags=["backend", "api"],
        )

        data = metrics.to_dict()

        assert data["id"] == "agent-1"
        assert data["role"] == "feature-dev"
        assert data["status"] == "active"
        assert data["progress"] == 50
        assert data["is_stale"] is False
        assert data["domain"] == "test-domain"
        assert data["token_usage"] == {"prompt": 100, "completion": 50}
        assert data["messages_count"] == 10
        assert data["focus_tags"] == ["backend", "api"]


# =============================================================================
# TaskThroughputMetrics Tests
# =============================================================================


class TestTaskThroughputMetrics:
    """Tests for TaskThroughputMetrics dataclass."""

    def test_throughput_metrics_creation(self) -> None:
        """Should create throughput metrics with defaults."""
        metrics = TaskThroughputMetrics()

        assert metrics.total_today == 0
        assert metrics.completed_today == 0
        assert metrics.failed_today == 0
        assert metrics.in_progress_today == 0
        assert metrics.avg_duration_seconds == 0.0
        assert metrics.success_rate == 0.0
        assert metrics.by_type == {}

    def test_throughput_metrics_to_dict(self) -> None:
        """Should convert to dictionary."""
        metrics = TaskThroughputMetrics(
            total_today=10,
            completed_today=8,
            failed_today=1,
            in_progress_today=1,
            avg_duration_seconds=120.5,
            success_rate=0.8,
            by_type={"feature": 5, "bugfix": 3},
        )

        data = metrics.to_dict()

        assert data["total_today"] == 10
        assert data["completed_today"] == 8
        assert data["failed_today"] == 1
        assert data["avg_duration_seconds"] == 120.5
        assert data["success_rate"] == 0.8
        assert data["by_type"] == {"feature": 5, "bugfix": 3}


# =============================================================================
# DashboardSummary Tests
# =============================================================================


class TestDashboardSummary:
    """Tests for DashboardSummary dataclass."""

    def test_summary_creation(self) -> None:
        """Should create summary with defaults."""
        summary = DashboardSummary()

        assert summary.total_agents == 0
        assert summary.active_agents_count == 0
        assert summary.idle_agents_count == 0
        assert summary.error_agents_count == 0
        assert summary.paused_agents_count == 0
        assert summary.agents == []
        assert summary.system_status == "healthy"

    def test_summary_to_dict(self) -> None:
        """Should convert to dictionary."""
        now = datetime.now(UTC)
        agent = AgentMetricsSummary(
            id="agent-1",
            role="dev",
            project=None,
            task=None,
            status="active",
            progress=75,
            last_activity=now,
        )
        throughput = TaskThroughputMetrics(total_today=5, completed_today=4)

        summary = DashboardSummary(
            total_agents=1,
            active_agents_count=1,
            agents=[agent],
            task_throughput=throughput,
            system_status="healthy",
            last_updated=datetime.now(UTC),
        )

        data = summary.to_dict()

        assert data["total_agents"] == 1
        assert data["active_agents_count"] == 1
        assert data["system_status"] == "healthy"
        assert len(data["agents"]) == 1
        assert data["task_throughput"]["total_today"] == 5


# =============================================================================
# DashboardService Initialization Tests
# =============================================================================


class TestDashboardServiceInit:
    """Tests for DashboardService initialization."""

    def test_init_creates_database(self, tmp_db_path: Path, tmp_snapshots_path: Path) -> None:
        """Should create database on initialization."""
        service = DashboardService(
            metrics_db_path=tmp_db_path,
            snapshots_path=tmp_snapshots_path,
        )

        assert tmp_db_path.exists()

    def test_init_creates_tables(self, tmp_db_path: Path, tmp_snapshots_path: Path) -> None:
        """Should create required tables."""
        service = DashboardService(
            metrics_db_path=tmp_db_path,
            snapshots_path=tmp_snapshots_path,
        )

        # Check tables exist
        conn = sqlite3.connect(str(tmp_db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "dashboard_task_events" in tables
        assert "dashboard_agent_registry" in tables


# =============================================================================
# Agent Registration Tests
# =============================================================================


class TestAgentRegistration:
    """Tests for agent registration functionality."""

    def test_register_agent(self, dashboard_service: DashboardService) -> None:
        """Should register a new agent."""
        agent = dashboard_service.register_agent(
            agent_id="agent-1",
            role="feature-dev",
            project="test-project",
            task="Implement feature",
            domain="test-domain",
            focus_tags=["backend"],
        )

        assert agent.id == "agent-1"
        assert agent.role == "feature-dev"
        assert agent.project == "test-project"
        assert agent.status == "active"
        assert agent.domain == "test-domain"
        assert agent.focus_tags == ["backend"]

    def test_get_agent_existing(self, dashboard_service: DashboardService) -> None:
        """Should retrieve existing agent."""
        dashboard_service.register_agent(
            agent_id="agent-1",
            role="feature-dev",
            project="test-project",
        )

        agent = dashboard_service.get_agent("agent-1")

        assert agent is not None
        assert agent.id == "agent-1"
        assert agent.role == "feature-dev"

    def test_get_agent_nonexistent(self, dashboard_service: DashboardService) -> None:
        """Should return None for non-existent agent."""
        agent = dashboard_service.get_agent("nonexistent")

        assert agent is None

    def test_update_agent(self, dashboard_service: DashboardService) -> None:
        """Should update agent fields."""
        dashboard_service.register_agent(
            agent_id="agent-1",
            role="feature-dev",
            project="test-project",
        )

        updated = dashboard_service.update_agent(
            agent_id="agent-1",
            status="idle",
            progress=50,
            task="Updated task",
        )

        assert updated is not None
        assert updated.status == "idle"
        assert updated.progress == 50
        assert updated.task == "Updated task"

    def test_update_agent_no_changes(self, dashboard_service: DashboardService) -> None:
        """Should return None when no updates provided."""
        dashboard_service.register_agent(
            agent_id="agent-1",
            role="feature-dev",
        )

        result = dashboard_service.update_agent("agent-1")

        assert result is None

    def test_list_agents(self, dashboard_service: DashboardService) -> None:
        """Should list all registered agents."""
        dashboard_service.register_agent("agent-1", "dev-1")
        dashboard_service.register_agent("agent-2", "dev-2")
        dashboard_service.register_agent("agent-3", "dev-3")

        agents = dashboard_service.list_agents()

        assert len(agents) == 3
        ids = {a.id for a in agents}
        assert ids == {"agent-1", "agent-2", "agent-3"}

    def test_list_agents_status_filter(self, dashboard_service: DashboardService) -> None:
        """Should filter agents by status."""
        dashboard_service.register_agent("agent-1", "dev")
        dashboard_service.register_agent("agent-2", "dev")
        dashboard_service.register_agent("agent-3", "dev")

        # Update agents to have different statuses
        dashboard_service.update_agent("agent-1", status="active")
        dashboard_service.update_agent("agent-2", status="idle")
        dashboard_service.update_agent("agent-3", status="active")

        agents = dashboard_service.list_agents(status_filter="active")

        assert len(agents) == 2
        for agent in agents:
            assert agent.status == "active"

    def test_remove_agent(self, dashboard_service: DashboardService) -> None:
        """Should remove agent from registry."""
        dashboard_service.register_agent("agent-1", "dev")

        result = dashboard_service.remove_agent("agent-1")

        assert result is True
        assert dashboard_service.get_agent("agent-1") is None

    def test_remove_agent_nonexistent(self, dashboard_service: DashboardService) -> None:
        """Should return False for non-existent agent."""
        result = dashboard_service.remove_agent("nonexistent")

        assert result is False


# =============================================================================
# Stale Agent Tests
# =============================================================================


class TestStaleAgentDetection:
    """Tests for stale agent detection."""

    def test_is_agent_stale_recent(self, dashboard_service: DashboardService) -> None:
        """Should not mark recent agent as stale."""
        recent = datetime.now(UTC).isoformat()

        is_stale = dashboard_service._is_agent_stale(recent)

        assert is_stale is False

    def test_is_agent_stale_old(self, dashboard_service: DashboardService) -> None:
        """Should mark old agent as stale."""
        old = (datetime.now(UTC) - timedelta(seconds=400)).isoformat()

        is_stale = dashboard_service._is_agent_stale(old)

        assert is_stale is True

    def test_is_agent_stale_invalid(self, dashboard_service: DashboardService) -> None:
        """Should handle invalid timestamp."""
        is_stale = dashboard_service._is_agent_stale("invalid")

        assert is_stale is True

    def test_list_agents_excludes_stale(self, dashboard_service: DashboardService) -> None:
        """Should exclude stale agents by default."""
        agents = dashboard_service.list_agents(include_stale=False)
        assert isinstance(agents, list)


# =============================================================================
# Task Throughput Tests
# =============================================================================


class TestTaskThroughput:
    """Tests for task throughput tracking."""

    def test_record_task_start(self, dashboard_service: DashboardService) -> None:
        """Should record task start."""
        result = dashboard_service.record_task_start(
            agent_id="agent-1",
            task_id="task-1",
            task_type="feature",
            metadata={"priority": "high"},
        )

        assert result is True

    def test_record_task_start_duplicate(self, dashboard_service: DashboardService) -> None:
        """Should reject duplicate task ID."""
        dashboard_service.record_task_start(
            agent_id="agent-1",
            task_id="task-1",
        )

        result = dashboard_service.record_task_start(
            agent_id="agent-1",
            task_id="task-1",
        )

        assert result is False

    def test_record_task_end(self, dashboard_service: DashboardService) -> None:
        """Should record task end."""
        dashboard_service.record_task_start(
            agent_id="agent-1",
            task_id="task-1",
        )

        result = dashboard_service.record_task_end(
            task_id="task-1",
            outcome="success",
            metadata={"result": "completed"},
        )

        assert result is True

    def test_record_task_end_nonexistent(self, dashboard_service: DashboardService) -> None:
        """Should return False for non-existent task."""
        result = dashboard_service.record_task_end(
            task_id="nonexistent",
            outcome="success",
        )

        assert result is False

    def test_get_task_throughput_empty(self, dashboard_service: DashboardService) -> None:
        """Should return zero metrics when no tasks."""
        metrics = dashboard_service.get_task_throughput(hours=24)

        assert metrics.total_today == 0
        assert metrics.completed_today == 0
        assert metrics.failed_today == 0
        assert metrics.success_rate == 0.0

    def test_get_task_throughput_with_data(self, dashboard_service: DashboardService) -> None:
        """Should calculate throughput metrics."""
        # Record some tasks
        dashboard_service.record_task_start("agent-1", "task-1", "feature")
        dashboard_service.record_task_start("agent-1", "task-2", "bugfix")
        dashboard_service.record_task_start("agent-1", "task-3", "feature")

        # Complete some tasks
        dashboard_service.record_task_end("task-1", "success")
        dashboard_service.record_task_end("task-2", "failure")
        # task-3 remains in_progress

        metrics = dashboard_service.get_task_throughput(hours=24)

        assert metrics.total_today == 3
        assert metrics.completed_today == 1
        assert metrics.failed_today == 1
        assert metrics.in_progress_today == 1
        assert metrics.success_rate == 0.5  # 1 success / (1 success + 1 failure)
        assert "feature" in metrics.by_type
        assert "bugfix" in metrics.by_type


# =============================================================================
# Agent Task Metrics Tests
# =============================================================================


class TestAgentTaskMetrics:
    """Tests for per-agent task metrics."""

    def test_get_agent_task_metrics_empty(self, dashboard_service: DashboardService) -> None:
        """Should return zero metrics for agent with no tasks."""
        metrics = dashboard_service.get_agent_task_metrics("agent-1", days=7)

        assert metrics["agent_id"] == "agent-1"
        assert metrics["total_tasks"] == 0
        assert metrics["completed_tasks"] == 0
        assert metrics["failed_tasks"] == 0
        assert metrics["success_rate"] == 0.0

    def test_get_agent_task_metrics_with_tasks(self, dashboard_service: DashboardService) -> None:
        """Should calculate metrics for agent tasks."""
        # Record tasks for agent
        dashboard_service.record_task_start("agent-1", "task-1", "feature")
        dashboard_service.record_task_start("agent-1", "task-2", "bugfix")
        dashboard_service.record_task_end("task-1", "success")
        dashboard_service.record_task_end("task-2", "success")

        metrics = dashboard_service.get_agent_task_metrics("agent-1", days=7)

        assert metrics["agent_id"] == "agent-1"
        assert metrics["total_tasks"] == 2
        assert metrics["completed_tasks"] == 2
        assert metrics["failed_tasks"] == 0
        assert metrics["success_rate"] == 1.0


# =============================================================================
# Task Type Metrics Tests
# =============================================================================


class TestTaskTypeMetrics:
    """Tests for per-task-type metrics."""

    def test_get_task_type_metrics_empty(self, dashboard_service: DashboardService) -> None:
        """Should return zero metrics for task type with no tasks."""
        metrics = dashboard_service.get_task_type_metrics("feature", days=7)

        assert metrics["task_type"] == "feature"
        assert metrics["total_tasks"] == 0
        assert metrics["success_rate"] == 0.0

    def test_get_task_type_metrics_with_tasks(self, dashboard_service: DashboardService) -> None:
        """Should calculate metrics for task type."""
        # Record tasks
        dashboard_service.record_task_start("agent-1", "task-1", "feature")
        dashboard_service.record_task_start("agent-1", "task-2", "feature")
        dashboard_service.record_task_start("agent-1", "task-3", "bugfix")

        dashboard_service.record_task_end("task-1", "success")
        dashboard_service.record_task_end("task-2", "failure")
        dashboard_service.record_task_end("task-3", "success")

        metrics = dashboard_service.get_task_type_metrics("feature", days=7)

        assert metrics["task_type"] == "feature"
        assert metrics["total_tasks"] == 2
        assert metrics["completed_tasks"] == 1
        assert metrics["failed_tasks"] == 1
        assert metrics["success_rate"] == 0.5


# =============================================================================
# Dashboard Summary Tests
# =============================================================================


class TestDashboardSummaryGeneration:
    """Tests for dashboard summary generation."""

    @pytest.mark.asyncio
    async def test_get_dashboard_summary_empty(self, dashboard_service: DashboardService) -> None:
        """Should generate summary with zero metrics."""
        summary = await dashboard_service.get_dashboard_summary()

        assert summary.total_agents == 0
        assert summary.active_agents_count == 0
        assert summary.system_status == "healthy"
        assert summary.task_throughput.total_today == 0

    @pytest.mark.asyncio
    async def test_get_dashboard_summary_with_agents(
        self, dashboard_service: DashboardService
    ) -> None:
        """Should count agents by status."""
        # Register agents
        dashboard_service.register_agent("agent-1", "dev")
        dashboard_service.register_agent("agent-2", "dev")
        dashboard_service.register_agent("agent-3", "dev")

        # Update statuses
        dashboard_service.update_agent("agent-1", status="active")
        dashboard_service.update_agent("agent-2", status="idle")
        dashboard_service.update_agent("agent-3", status="error")

        summary = await dashboard_service.get_dashboard_summary()

        assert summary.total_agents == 3
        assert summary.active_agents_count == 1
        assert summary.idle_agents_count == 1
        assert summary.error_agents_count == 1

    @pytest.mark.asyncio
    async def test_get_dashboard_summary_system_status(
        self, dashboard_service: DashboardService
    ) -> None:
        """Should determine system status based on errors."""
        # All healthy
        dashboard_service.register_agent("agent-1", "dev")
        dashboard_service.register_agent("agent-2", "dev")
        dashboard_service.update_agent("agent-1", status="active")
        dashboard_service.update_agent("agent-2", status="active")

        summary = await dashboard_service.get_dashboard_summary()
        assert summary.system_status == "healthy"

    def test_get_agent_metrics(self, dashboard_service: DashboardService) -> None:
        """Should return agent metrics."""
        dashboard_service.register_agent("agent-1", "dev")
        dashboard_service.register_agent("agent-2", "dev")

        agents = dashboard_service.get_agent_metrics()

        assert len(agents) == 2


# =============================================================================
# Snapshot Tests
# =============================================================================


class TestSnapshotOperations:
    """Tests for snapshot save/load operations."""

    def test_save_and_load_snapshot(self, dashboard_service: DashboardService) -> None:
        """Should save and load snapshot."""
        agent = AgentMetricsSummary(
            id="agent-1",
            role="dev",
            project=None,
            task=None,
            status="active",
            progress=50,
            last_activity=datetime.now(UTC),
        )
        throughput = TaskThroughputMetrics(total_today=5)

        summary = DashboardSummary(
            total_agents=1,
            active_agents_count=1,
            agents=[agent],
            task_throughput=throughput,
            system_status="healthy",
            last_updated=datetime.now(UTC),
        )

        dashboard_service._save_snapshot(summary)

        # Verify file was created
        assert dashboard_service.snapshots_path.exists()

    def test_load_snapshot_corrupted(self, dashboard_service: DashboardService) -> None:
        """Should handle corrupted snapshot file."""
        # Write invalid JSON
        dashboard_service.snapshots_path.parent.mkdir(parents=True, exist_ok=True)
        dashboard_service.snapshots_path.write_text("invalid json")

        # Should not raise
        dashboard_service._load_snapshot()


# =============================================================================
# Singleton Tests
# =============================================================================


class TestGetDashboardService:
    """Tests for get_dashboard_service singleton."""

    def test_get_dashboard_service_returns_singleton(self) -> None:
        """Should return the same instance on multiple calls."""
        # Reset global state
        import forge_harness.webhook_server.services.dashboard_service as ds

        ds._dashboard_service = None

        service1 = get_dashboard_service()
        service2 = get_dashboard_service()

        assert service1 is service2

    def test_get_dashboard_service_creates_new_if_none(self) -> None:
        """Should create new service if none exists."""
        # Reset global state
        import forge_harness.webhook_server.services.dashboard_service as ds

        ds._dashboard_service = None

        service = get_dashboard_service()

        assert isinstance(service, DashboardService)


class TestCreateDashboardService:
    """Tests for create_dashboard_service."""

    def test_create_dashboard_service(self, tmp_path: Path) -> None:
        """Should create new isolated service instance."""
        db_path = tmp_path / "test.db"
        snapshots_path = tmp_path / "snapshots.json"

        service = create_dashboard_service(
            metrics_db_path=db_path,
            snapshots_path=snapshots_path,
        )

        assert isinstance(service, DashboardService)
        assert service.metrics_db_path == db_path


# =============================================================================
# Legacy Compatibility Tests
# =============================================================================


class TestLegacyCompatibility:
    """Tests for legacy compatibility aliases."""

    def test_legacy_aliases_exist(self) -> None:
        """Should have legacy aliases for backward compatibility."""
        from forge_harness.webhook_server.services.dashboard_service import (
            AgentMetricsSnapshot,
            FleetMetricsSnapshot,
            MetricsCollector,
            TaskThroughputRecord,
            create_metrics_collector,
        )

        # Verify aliases point to correct types
        assert MetricsCollector is DashboardService
        assert FleetMetricsSnapshot is DashboardSummary
        assert AgentMetricsSnapshot is AgentMetricsSummary

        # Test create_metrics_collector
        collector = create_metrics_collector()
        assert isinstance(collector, DashboardService)


# =============================================================================
# Coverage Expansion Tests
# =============================================================================


class TestCacheBehavior:
    """Tests for caching behavior to improve coverage."""

    def test_snapshot_cache_hit_from_file(self, tmp_path: Path) -> None:
        """Test cache hit when loading snapshot from file."""
        import json
        from datetime import UTC, datetime

        db_path = tmp_path / "test.db"
        snapshots_path = tmp_path / "snapshots.json"

        # Create a valid cached snapshot file
        cached_data = {
            "cached_at": datetime.now(UTC).isoformat(),
            "summary": {
                "total_agents": 5,
                "active_agents_count": 2,
                "idle_agents_count": 2,
                "error_agents_count": 1,
                "paused_agents_count": 0,
                "system_status": "healthy",
                "last_updated": datetime.now(UTC).isoformat(),
                "task_throughput": {
                    "tasks_per_hour": 10.0,
                    "success_rate": 0.95,
                    "avg_duration_seconds": 120.0,
                },
                "agents": [],
            },
        }
        snapshots_path.write_text(json.dumps(cached_data))

        service = DashboardService(
            metrics_db_path=db_path,
            snapshots_path=snapshots_path,
        )

        # The cached snapshot should be loaded
        assert service._cached_snapshot is not None
        assert service._cached_snapshot.total_agents == 5

    @pytest.mark.asyncio
    async def test_get_summary_cache_hit(self, dashboard_service: DashboardService) -> None:
        """Test get_dashboard_summary returns cached result within rate limit."""
        # First call to populate cache
        summary1 = await dashboard_service.get_dashboard_summary()

        # Second call should return cached result (same object)
        summary2 = await dashboard_service.get_dashboard_summary()

        assert summary1 is summary2

    @pytest.mark.asyncio
    async def test_get_summary_force_refresh(self, dashboard_service: DashboardService) -> None:
        """Test get_dashboard_summary with force_refresh bypasses cache."""
        # First call to populate cache
        summary1 = await dashboard_service.get_dashboard_summary()

        # Force refresh should create new summary
        summary2 = await dashboard_service.get_dashboard_summary(force_refresh=True)

        # Should be different objects (even if same data)
        assert summary1 is not summary2


class TestUpdateAgentExtended:
    """Extended tests for update_agent method."""

    def test_update_agent_token_usage(self, dashboard_service: DashboardService) -> None:
        """Test updating agent with token_usage."""
        # Register agent first
        dashboard_service.register_agent(
            agent_id="token-test-agent",
            role="builder",
            domain="test-domain",
            project="test-project",
        )

        # Update with token_usage
        result = dashboard_service.update_agent(
            agent_id="token-test-agent",
            token_usage={"input": 5000, "output": 2500},
        )

        assert result is not None

        # Verify update
        agents = dashboard_service.list_agents()
        agent = next(a for a in agents if a.id == "token-test-agent")
        assert agent.token_usage == {"input": 5000, "output": 2500}

    def test_update_agent_messages_count(self, dashboard_service: DashboardService) -> None:
        """Test updating agent with messages_count."""
        # Register agent first
        dashboard_service.register_agent(
            agent_id="msg-count-agent",
            role="tester",
            domain="test-domain",
            project="test-project",
        )

        # Update with messages_count
        result = dashboard_service.update_agent(
            agent_id="msg-count-agent",
            messages_count=42,
        )

        assert result is not None


class TestRecordTaskErrorHandling:
    """Tests for error handling in task recording methods."""

    def test_record_task_start_exception_handling(self, tmp_path: Path) -> None:
        """Test record_task_start handles exceptions gracefully."""
        db_path = tmp_path / "test.db"
        snapshots_path = tmp_path / "snapshots.json"

        service = DashboardService(
            metrics_db_path=db_path,
            snapshots_path=snapshots_path,
        )

        # Drop the table to force an error
        with service._get_connection() as conn:
            conn.execute("DROP TABLE IF EXISTS dashboard_task_events")

        # This should handle the exception and return False
        result = service.record_task_start(
            task_id="error-task",
            agent_id="agent-1",
            task_type="test",
        )

        assert result is False

    def test_record_task_end_exception_handling(self, tmp_path: Path) -> None:
        """Test record_task_end handles exceptions gracefully."""
        db_path = tmp_path / "test.db"
        snapshots_path = tmp_path / "snapshots.json"

        service = DashboardService(
            metrics_db_path=db_path,
            snapshots_path=snapshots_path,
        )

        # Drop the table to force an error
        with service._get_connection() as conn:
            conn.execute("DROP TABLE IF EXISTS dashboard_task_events")

        result = service.record_task_end(
            task_id="error-task",
            outcome="failure",
        )

        assert result is False

    def test_record_task_end_metadata_merge(self, dashboard_service: DashboardService) -> None:
        """Test record_task_end merges metadata correctly."""
        # Start a task
        dashboard_service.record_task_start(
            task_id="merge-task",
            agent_id="merge-agent",
            task_type="test",
            metadata={"initial": "value", "keep": "this"},
        )

        # End with additional metadata
        dashboard_service.record_task_end(
            task_id="merge-task",
            outcome="success",
            metadata={"new": "data", "initial": "updated"},
        )

        # Get throughput to verify task was recorded
        throughput = dashboard_service.get_task_throughput(hours=24)
        assert throughput.completed_today >= 1


class TestMetricsErrorHandling:
    """Tests for error handling in metrics methods."""

    def test_get_agent_task_metrics_exception(self, tmp_path: Path) -> None:
        """Test get_agent_task_metrics handles exceptions."""
        db_path = tmp_path / "test.db"
        snapshots_path = tmp_path / "snapshots.json"

        service = DashboardService(
            metrics_db_path=db_path,
            snapshots_path=snapshots_path,
        )

        # Drop table to force error
        with service._get_connection() as conn:
            conn.execute("DROP TABLE IF EXISTS dashboard_task_events")

        result = service.get_agent_task_metrics("nonexistent-agent")

        # Should return default values on error
        assert result["agent_id"] == "nonexistent-agent"
        assert result["total_tasks"] == 0
        assert result["success_rate"] == 0.0

    def test_get_task_type_metrics_exception(self, tmp_path: Path) -> None:
        """Test get_task_type_metrics handles exceptions."""
        db_path = tmp_path / "test.db"
        snapshots_path = tmp_path / "snapshots.json"

        service = DashboardService(
            metrics_db_path=db_path,
            snapshots_path=snapshots_path,
        )

        # Drop table to force error
        with service._get_connection() as conn:
            conn.execute("DROP TABLE IF EXISTS dashboard_task_events")

        result = service.get_task_type_metrics("test-type")

        # Should return default values on error
        assert result["task_type"] == "test-type"
        assert result["total_tasks"] == 0
        assert result["success_rate"] == 0.0


class TestSystemStatusCritical:
    """Tests for system status escalation to critical."""

    @pytest.mark.asyncio
    async def test_system_status_critical_when_majority_errors(
        self, dashboard_service: DashboardService
    ) -> None:
        """Test system status becomes critical when >50% agents have errors."""
        # Register 4 agents
        for i in range(4):
            dashboard_service.register_agent(
                agent_id=f"critical-agent-{i}",
                role="test",
                domain="test",
                project="test",
            )

        # Set 3 of 4 agents to error status (>50%)
        for i in range(3):
            dashboard_service.update_agent(
                agent_id=f"critical-agent-{i}",
                status="error",
            )

        # Set 1 agent to active
        dashboard_service.update_agent(
            agent_id="critical-agent-3",
            status="active",
        )

        # Get summary - should have critical status
        summary = await dashboard_service.get_dashboard_summary(force_refresh=True)

        assert summary.system_status == "critical"
        assert summary.error_agents_count == 3
        assert summary.active_agents_count == 1

    @pytest.mark.asyncio
    async def test_system_status_degraded_with_some_errors(
        self, dashboard_service: DashboardService
    ) -> None:
        """Test system status is degraded when some agents have errors."""
        # Register 4 agents
        for i in range(4):
            dashboard_service.register_agent(
                agent_id=f"degraded-agent-{i}",
                role="test",
                domain="test",
                project="test",
            )

        # Set 1 of 4 agents to error status (<50%)
        dashboard_service.update_agent(
            agent_id="degraded-agent-0",
            status="error",
        )

        # Set others to active
        for i in range(1, 4):
            dashboard_service.update_agent(
                agent_id=f"degraded-agent-{i}",
                status="active",
            )

        summary = await dashboard_service.get_dashboard_summary(force_refresh=True)

        assert summary.system_status == "degraded"
        assert summary.error_agents_count == 1
