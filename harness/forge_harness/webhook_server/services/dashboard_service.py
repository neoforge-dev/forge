"""Unified Dashboard Service

Provides a single source of truth for dashboard data, consolidating metrics logic
from the TUI dashboard and fleet metrics into a unified service.

Features:
- Agent metrics aggregation (active/idle/error counts)
- Task throughput tracking (completed/failed/in-progress)
- Historical metrics storage with configurable retention
- Unified representation for both TUI and API consumers

Usage:
    from forge_harness.webhook_server.services.dashboard_service import (
        get_dashboard_service,
        DashboardSummary,
        AgentMetricsSummary,
        TaskThroughputMetrics,
    )

    # Get the dashboard service singleton
    service = get_dashboard_service()

    # Get comprehensive dashboard summary
    summary = await service.get_dashboard_summary()
    print(f"Active agents: {summary.active_agents_count}")
    print(f"Tasks completed today: {summary.task_throughput.completed_today}")

    # Get agent metrics
    agents = await service.get_agent_metrics()
    for agent in agents:
        print(f"{agent.id}: {agent.status} ({agent.progress}%)")

    # Record task events
    await service.record_task_start("agent-1", "task-123", task_type="feature")
    await service.record_task_end("task-123", outcome="success")
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Data Models - Unified Representations
# =============================================================================

@dataclass
class AgentMetricsSummary:
    """Unified agent metrics for dashboard display.

    This dataclass provides a consistent representation of agent state
    used by both the TUI dashboard and Command Center API.
    """

    id: str
    role: str
    project: str | None
    task: str | None
    status: str  # "active", "idle", "paused", "error", "completed"
    progress: int  # 0-100
    last_activity: datetime | None
    is_stale: bool = False
    domain: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    messages_count: int = 0
    focus_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "id": self.id,
            "role": self.role,
            "project": self.project,
            "task": self.task,
            "status": self.status,
            "progress": self.progress,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "is_stale": self.is_stale,
            "domain": self.domain,
            "token_usage": self.token_usage,
            "messages_count": self.messages_count,
            "focus_tags": self.focus_tags,
        }


@dataclass
class TaskThroughputMetrics:
    """Unified task throughput metrics for dashboard display."""

    total_today: int = 0
    completed_today: int = 0
    failed_today: int = 0
    in_progress_today: int = 0
    avg_duration_seconds: float = 0.0
    success_rate: float = 0.0
    by_type: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_today": self.total_today,
            "completed_today": self.completed_today,
            "failed_today": self.failed_today,
            "in_progress_today": self.in_progress_today,
            "avg_duration_seconds": self.avg_duration_seconds,
            "success_rate": self.success_rate,
            "by_type": self.by_type,
        }


@dataclass
class DashboardSummary:
    """Unified dashboard summary combining all metrics.

    This is the primary data structure returned by the dashboard service,
    providing a complete snapshot of the current system state.
    """

    # Agent metrics
    total_agents: int = 0
    active_agents_count: int = 0
    idle_agents_count: int = 0
    error_agents_count: int = 0
    paused_agents_count: int = 0

    # Agent details
    agents: list[AgentMetricsSummary] = field(default_factory=list)

    # Task throughput
    task_throughput: TaskThroughputMetrics = field(default_factory=TaskThroughputMetrics)

    # System health
    system_status: str = "healthy"  # "healthy", "degraded", "critical"
    last_updated: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "total_agents": self.total_agents,
            "active_agents_count": self.active_agents_count,
            "idle_agents_count": self.idle_agents_count,
            "error_agents_count": self.error_agents_count,
            "paused_agents_count": self.paused_agents_count,
            "agents": [a.to_dict() for a in self.agents],
            "task_throughput": self.task_throughput.to_dict(),
            "system_status": self.system_status,
            "last_updated": self.last_updated.isoformat(),
        }


@dataclass
class ThroughputRecord:
    """Task throughput record for historical tracking."""

    task_id: str
    agent_id: str
    task_type: str | None
    started_at: datetime
    ended_at: datetime | None
    outcome: str | None  # "success", "failure", "cancelled", None for in-progress
    duration_seconds: float | None


# =============================================================================
# Dashboard Service
# =============================================================================

class DashboardService:
    """Unified service for dashboard metrics and agent tracking.

    This service consolidates metrics from multiple sources:
    - AgentRegistry (in-memory agent tracking)
    - Task throughput database (SQLite)
    - Metrics snapshots (JSON file storage)

    Provides a single source of truth for dashboard data.
    """

    def __init__(
        self,
        metrics_db_path: Path | None = None,
        snapshots_path: Path | None = None,
        registry_ttl_seconds: int = 300,
    ):
        """Initialize the dashboard service.

        Args:
            metrics_db_path: Path to SQLite database for task throughput (default: .forge_dashboard.db)
            snapshots_path: Path to JSON file for metrics snapshots (default: .forge_dashboard_snapshots.json)
            registry_ttl_seconds: Seconds before agent registry entries expire (default: 300)
        """
        # Paths
        self.forge_root = Path.cwd()
        self.metrics_db_path = metrics_db_path or self.forge_root / ".forge_dashboard.db"
        self.snapshots_path = snapshots_path or self.forge_root / ".forge_dashboard_snapshots.json"

        # Registry TTL
        self.registry_ttl_seconds = registry_ttl_seconds

        # Agent registry (in-memory)
        self._agents: dict[str, dict[str, Any]] = {}
        self._agents_lock: sqlite3.Lock | None = None  # Use SQLite lock for thread safety

        # Metrics collection state
        self._last_collect = 0.0
        self._rate_limit = 5.0  # seconds between collections
        self._cached_snapshot: DashboardSummary | None = None

        # Initialize
        self._initialize_metrics_db()
        self._load_snapshot()

    def _initialize_metrics_db(self) -> None:
        """Initialize the SQLite database for task throughput."""
        self.metrics_db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Task events table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_task_events (
                    task_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    task_type TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    outcome TEXT,
                    duration_seconds REAL,
                    metadata TEXT
                )
            """)

            # Agent registry table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_agent_registry (
                    agent_id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    project TEXT,
                    task TEXT,
                    status TEXT DEFAULT 'active',
                    progress INTEGER DEFAULT 0,
                    last_activity TEXT NOT NULL,
                    domain TEXT,
                    token_usage TEXT,
                    messages_count INTEGER DEFAULT 0,
                    focus_tags TEXT,
                    registered_at TEXT NOT NULL,
                    UNIQUE(agent_id)
                )
            """)

            # Create indexes
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_agent_activity
                ON dashboard_agent_registry(last_activity)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_task_started
                ON dashboard_task_events(started_at)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_task_outcome
                ON dashboard_task_events(outcome)
            """)

            conn.commit()
            logger.debug("Dashboard service database initialized")

    @contextmanager
    def _get_connection(self):
        """Get database connection context manager."""
        conn = sqlite3.connect(str(self.metrics_db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _load_snapshot(self) -> None:
        """Load cached metrics snapshot from file."""
        if self.snapshots_path.exists():
            try:
                data = json.loads(self.snapshots_path.read_text())
                # Parse cached snapshot if recent (within rate limit)
                cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
                if (datetime.now(UTC) - cached_at).total_seconds() < self._rate_limit:
                    self._cached_snapshot = DashboardSummary(**data.get("summary", {}))
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.debug(f"Failed to load snapshot: {e}")

    def _save_snapshot(self, summary: DashboardSummary) -> None:
        """Save metrics snapshot to file."""
        self.snapshots_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "cached_at": datetime.now(UTC).isoformat(),
            "summary": {
                "total_agents": summary.total_agents,
                "active_agents_count": summary.active_agents_count,
                "idle_agents_count": summary.idle_agents_count,
                "error_agents_count": summary.error_agents_count,
                "paused_agents_count": summary.paused_agents_count,
                "system_status": summary.system_status,
                "last_updated": summary.last_updated.isoformat(),
                "task_throughput": summary.task_throughput.to_dict(),
                "agents": [a.to_dict() for a in summary.agents],
            },
        }
        self.snapshots_path.write_text(json.dumps(data, indent=2))

    # =============================================================================
    # Agent Registry Methods
    # =============================================================================

    def register_agent(
        self,
        agent_id: str,
        role: str,
        project: str | None = None,
        task: str | None = None,
        domain: str | None = None,
        focus_tags: list[str] | None = None,
    ) -> AgentMetricsSummary:
        """Register or update an agent in the dashboard registry.

        Args:
            agent_id: Unique agent identifier
            role: Agent role (e.g., "feature-dev", "backend-engineer")
            project: Project being worked on
            task: Current task description
            domain: Domain for hierarchy
            focus_tags: List of skill tags

        Returns:
            AgentMetricsSummary for the registered agent
        """
        now = datetime.now(UTC)

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Upsert agent
            cursor.execute("""
                INSERT INTO dashboard_agent_registry
                (agent_id, role, project, task, status, progress, last_activity,
                 domain, focus_tags, registered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    last_activity = excluded.last_activity,
                    status = COALESCE(excluded.status, dashboard_agent_registry.status),
                    task = COALESCE(excluded.task, dashboard_agent_registry.task),
                    progress = COALESCE(excluded.progress, dashboard_agent_registry.progress)
            """, (
                agent_id, role, project, task, "active", 0, now.isoformat(),
                domain, json.dumps(focus_tags or []), now.isoformat(),
            ))

        logger.debug(f"Agent registered: {agent_id} ({role})")
        return AgentMetricsSummary(
            id=agent_id,
            role=role,
            project=project,
            task=task,
            status="active",
            progress=0,
            last_activity=now,
            is_stale=False,
            domain=domain,
            focus_tags=focus_tags or [],
        )

    def update_agent(
        self,
        agent_id: str,
        status: str | None = None,
        progress: int | None = None,
        task: str | None = None,
        token_usage: dict[str, int] | None = None,
        messages_count: int | None = None,
    ) -> AgentMetricsSummary | None:
        """Update agent status in the registry.

        Args:
            agent_id: Agent identifier
            status: New status
            progress: Progress percentage (0-100)
            task: Current task description
            token_usage: Token usage statistics
            messages_count: Number of messages

        Returns:
            Updated AgentMetricsSummary or None if agent not found
        """
        updates = []
        params = []

        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if progress is not None:
            updates.append("progress = ?")
            params.append(progress)
        if task is not None:
            updates.append("task = ?")
            params.append(task)
        if token_usage is not None:
            updates.append("token_usage = ?")
            params.append(json.dumps(token_usage))
        if messages_count is not None:
            updates.append("messages_count = ?")
            params.append(messages_count)

        if not updates:
            return None

        updates.append("last_activity = ?")
        params.append(datetime.now(UTC).isoformat())
        params.append(agent_id)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE dashboard_agent_registry
                SET {', '.join(updates)}
                WHERE agent_id = ?
            """, params)

        return self.get_agent(agent_id)

    def get_agent(self, agent_id: str) -> AgentMetricsSummary | None:
        """Get a single agent's metrics.

        Args:
            agent_id: Agent identifier

        Returns:
            AgentMetricsSummary or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM dashboard_agent_registry
                WHERE agent_id = ?
            """, (agent_id,))

            row = cursor.fetchone()
            if not row:
                return None

            return AgentMetricsSummary(
                id=row["agent_id"],
                role=row["role"],
                project=row["project"],
                task=row["task"],
                status=row["status"],
                progress=row["progress"],
                last_activity=datetime.fromisoformat(row["last_activity"]),
                is_stale=self._is_agent_stale(row["last_activity"]),
                domain=row["domain"],
                token_usage=json.loads(row["token_usage"] or "{}"),
                messages_count=row["messages_count"],
                focus_tags=json.loads(row["focus_tags"] or "[]"),
            )

    def list_agents(
        self,
        include_stale: bool = False,
        status_filter: str | None = None,
    ) -> list[AgentMetricsSummary]:
        """List all registered agents.

        Args:
            include_stale: Include agents with expired TTL
            status_filter: Filter by status (e.g., "active", "idle")

        Returns:
            List of AgentMetricsSummary
        """
        cutoff = datetime.now(UTC).timestamp() - self.registry_ttl_seconds
        cutoff_str = datetime.fromtimestamp(cutoff, UTC).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM dashboard_agent_registry WHERE last_activity >= ?"
            params = [cutoff_str]

            if status_filter:
                query += " AND status = ?"
                params.append(status_filter)

            query += " ORDER BY last_activity DESC"

            cursor.execute(query, params)

            agents = []
            for row in cursor.fetchall():
                agent = AgentMetricsSummary(
                    id=row["agent_id"],
                    role=row["role"],
                    project=row["project"],
                    task=row["task"],
                    status=row["status"],
                    progress=row["progress"],
                    last_activity=datetime.fromisoformat(row["last_activity"]),
                    is_stale=self._is_agent_stale(row["last_activity"]),
                    domain=row["domain"],
                    token_usage=json.loads(row["token_usage"] or "{}"),
                    messages_count=row["messages_count"],
                    focus_tags=json.loads(row["focus_tags"] or "[]"),
                )
                if include_stale or not agent.is_stale:
                    agents.append(agent)

        return agents

    def _is_agent_stale(self, last_activity: str) -> bool:
        """Check if agent is stale (no activity within TTL)."""
        try:
            last = datetime.fromisoformat(last_activity)
            return (datetime.now(UTC) - last).total_seconds() > self.registry_ttl_seconds
        except (ValueError, TypeError):
            return True

    def remove_agent(self, agent_id: str) -> bool:
        """Remove an agent from the registry.

        Args:
            agent_id: Agent identifier

        Returns:
            True if agent was removed, False if not found
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM dashboard_agent_registry WHERE agent_id = ?
            """, (agent_id,))
            return cursor.rowcount > 0

    # =============================================================================
    # Task Throughput Methods
    # =============================================================================

    def record_task_start(
        self,
        agent_id: str,
        task_id: str,
        task_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Record the start of a task.

        Args:
            agent_id: Agent identifier
            task_id: Unique task identifier
            task_type: Type of task (e.g., "feature", "bugfix")
            metadata: Optional metadata

        Returns:
            True if successful, False if task already exists
        """
        try:
            started_at = datetime.now(UTC).isoformat()

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO dashboard_task_events
                    (task_id, agent_id, task_type, started_at, metadata)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    task_id, agent_id, task_type, started_at,
                    json.dumps(metadata) if metadata else None,
                ))

            logger.debug(f"Task start recorded: {task_id} by {agent_id}")
            return True

        except sqlite3.IntegrityError:
            logger.warning(f"Task {task_id} already exists")
            return False
        except Exception as e:
            logger.error(f"Failed to record task start: {e}")
            return False

    def record_task_end(
        self,
        task_id: str,
        outcome: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Record the end of a task.

        Args:
            task_id: Task identifier
            outcome: Task outcome ("success", "failure", "cancelled")
            metadata: Optional metadata to merge with existing

        Returns:
            True if successful, False if task not found
        """
        try:
            ended_at = datetime.now(UTC)

            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Get existing task
                cursor.execute("""
                    SELECT started_at, metadata FROM dashboard_task_events
                    WHERE task_id = ?
                """, (task_id,))

                row = cursor.fetchone()
                if not row:
                    logger.warning(f"Task {task_id} not found")
                    return False

                started_at = datetime.fromisoformat(row["started_at"])
                duration = (ended_at - started_at).total_seconds()

                # Merge metadata
                existing_metadata = row["metadata"]
                if metadata:
                    if existing_metadata:
                        merged = json.loads(existing_metadata)
                        merged.update(metadata)
                        metadata_json = json.dumps(merged)
                    else:
                        metadata_json = json.dumps(metadata)
                else:
                    metadata_json = existing_metadata

                # Update task
                cursor.execute("""
                    UPDATE dashboard_task_events
                    SET ended_at = ?, outcome = ?, duration_seconds = ?, metadata = ?
                    WHERE task_id = ?
                """, (ended_at.isoformat(), outcome, duration, metadata_json, task_id))

            logger.debug(f"Task end recorded: {task_id} ({outcome}, {duration:.1f}s)")
            return True

        except Exception as e:
            logger.error(f"Failed to record task end: {e}")
            return False

    def get_task_throughput(
        self,
        hours: int = 24,
    ) -> TaskThroughputMetrics:
        """Get task throughput metrics for the specified time period.

        Args:
            hours: Number of hours to look back (default: 24)

        Returns:
            TaskThroughputMetrics with aggregated data
        """
        period_start = datetime.now(UTC) - timedelta(hours=hours)

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Overall stats
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN outcome = 'failure' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN outcome IS NULL THEN 1 ELSE 0 END) as in_progress,
                    AVG(CASE WHEN duration_seconds IS NOT NULL THEN duration_seconds END) as avg_duration
                FROM dashboard_task_events
                WHERE started_at >= ?
            """, (period_start.isoformat(),))

            row = cursor.fetchone()
            total = row["total"] or 0
            completed = row["completed"] or 0
            failed = row["failed"] or 0
            in_progress = row["in_progress"] or 0
            avg_duration = row["avg_duration"] or 0.0

            # Calculate success rate
            success_rate = completed / (completed + failed) if (completed + failed) > 0 else 0.0

            # Task type breakdown
            cursor.execute("""
                SELECT task_type, COUNT(*) as count
                FROM dashboard_task_events
                WHERE started_at >= ? AND task_type IS NOT NULL
                GROUP BY task_type
            """, (period_start.isoformat(),))

            by_type = {row["task_type"]: row["count"] for row in cursor.fetchall()}

            return TaskThroughputMetrics(
                total_today=total,
                completed_today=completed,
                failed_today=failed,
                in_progress_today=in_progress,
                avg_duration_seconds=avg_duration,
                success_rate=success_rate,
                by_type=by_type,
            )

    # =============================================================================
    # Per-Agent and Per-Task-Type Metrics (migrated from FleetMetrics)
    # =============================================================================

    def get_agent_task_metrics(self, agent_id: str, days: int = 7) -> dict[str, Any]:
        """Get task metrics for a specific agent.

        Args:
            agent_id: Agent identifier
            days: Number of days to look back (default: 7)

        Returns:
            Dict with agent task metrics including utilization
        """
        period_start = datetime.now(UTC) - timedelta(days=days)
        total_time_seconds = days * 24 * 3600

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as completed,
                        SUM(CASE WHEN outcome = 'failure' THEN 1 ELSE 0 END) as failed,
                        AVG(CASE WHEN duration_seconds IS NOT NULL THEN duration_seconds END) as avg_duration,
                        SUM(CASE WHEN duration_seconds IS NOT NULL THEN duration_seconds ELSE 0 END) as active_time,
                        MIN(started_at) as first_task,
                        MAX(started_at) as last_task
                    FROM dashboard_task_events
                    WHERE agent_id = ? AND started_at >= ?
                """, (agent_id, period_start.isoformat()))

                row = cursor.fetchone()
                total = row["total"] or 0
                completed = row["completed"] or 0
                failed = row["failed"] or 0
                avg_duration = row["avg_duration"] or 0.0
                active_time = row["active_time"] or 0.0
                success_rate = completed / (completed + failed) if (completed + failed) > 0 else 0.0
                utilization = active_time / total_time_seconds if total_time_seconds > 0 else 0.0

                return {
                    "agent_id": agent_id,
                    "total_tasks": total,
                    "completed_tasks": completed,
                    "failed_tasks": failed,
                    "success_rate": success_rate,
                    "avg_duration_seconds": avg_duration,
                    "utilization": utilization,
                    "active_time_seconds": active_time,
                    "total_time_seconds": total_time_seconds,
                    "first_task": row["first_task"],
                    "last_task": row["last_task"],
                }
        except Exception as e:
            logger.error(f"Failed to get agent task metrics: {e}")
            return {
                "agent_id": agent_id,
                "total_tasks": 0,
                "completed_tasks": 0,
                "failed_tasks": 0,
                "success_rate": 0.0,
                "avg_duration_seconds": 0.0,
                "utilization": 0.0,
                "active_time_seconds": 0.0,
                "total_time_seconds": total_time_seconds,
            }

    def get_task_type_metrics(self, task_type: str, days: int = 7) -> dict[str, Any]:
        """Get metrics for a specific task type.

        Args:
            task_type: Task type identifier
            days: Number of days to look back (default: 7)

        Returns:
            Dict with task type metrics
        """
        period_start = datetime.now(UTC) - timedelta(days=days)

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as completed,
                        SUM(CASE WHEN outcome = 'failure' THEN 1 ELSE 0 END) as failed,
                        AVG(CASE WHEN duration_seconds IS NOT NULL THEN duration_seconds END) as avg_duration
                    FROM dashboard_task_events
                    WHERE task_type = ? AND started_at >= ?
                """, (task_type, period_start.isoformat()))

                row = cursor.fetchone()
                total = row["total"] or 0
                completed = row["completed"] or 0
                failed = row["failed"] or 0
                avg_duration = row["avg_duration"] or 0.0
                success_rate = completed / (completed + failed) if (completed + failed) > 0 else 0.0

                return {
                    "task_type": task_type,
                    "total_tasks": total,
                    "completed_tasks": completed,
                    "failed_tasks": failed,
                    "success_rate": success_rate,
                    "avg_duration_seconds": avg_duration,
                }
        except Exception as e:
            logger.error(f"Failed to get task type metrics: {e}")
            return {
                "task_type": task_type,
                "total_tasks": 0,
                "completed_tasks": 0,
                "failed_tasks": 0,
                "success_rate": 0.0,
                "avg_duration_seconds": 0.0,
            }

    # =============================================================================
    # Dashboard Summary (Primary Interface)
    # =============================================================================

    async def get_dashboard_summary(self, force_refresh: bool = False) -> DashboardSummary:
        """Get comprehensive dashboard summary.

        This is the primary method for fetching dashboard data,
        providing a unified view of all metrics.

        Args:
            force_refresh: Skip cache and fetch fresh data

        Returns:
            DashboardSummary with all metrics
        """
        now = time.time()

        # Return cached snapshot if valid
        if not force_refresh and self._cached_snapshot:
            elapsed = now - self._last_collect
            if elapsed < self._rate_limit:
                return self._cached_snapshot

        self._last_collect = now

        # Collect agent metrics
        agents = self.list_agents(include_stale=False)

        # Count by status
        active_count = sum(1 for a in agents if a.status == "active")
        idle_count = sum(1 for a in agents if a.status == "idle")
        error_count = sum(1 for a in agents if a.status == "error")
        paused_count = sum(1 for a in agents if a.status == "paused")

        # Determine system status
        system_status = "healthy"
        if error_count > 0:
            system_status = "degraded"
        if error_count > len(agents) / 2 and len(agents) > 0:
            system_status = "critical"

        # Collect task throughput
        throughput = self.get_task_throughput(hours=24)

        # Build summary
        summary = DashboardSummary(
            total_agents=len(agents),
            active_agents_count=active_count,
            idle_agents_count=idle_count,
            error_agents_count=error_count,
            paused_agents_count=paused_count,
            agents=agents,
            task_throughput=throughput,
            system_status=system_status,
            last_updated=datetime.now(UTC),
        )

        # Cache and save
        self._cached_snapshot = summary
        self._save_snapshot(summary)

        return summary

    def get_agent_metrics(self) -> list[AgentMetricsSummary]:
        """Get all agent metrics (convenience method)."""
        return self.list_agents(include_stale=False)


# =============================================================================
# Global Service Singleton
# =============================================================================

_dashboard_service: DashboardService | None = None


def get_dashboard_service() -> DashboardService:
    """Get or create the global dashboard service singleton."""
    global _dashboard_service
    if _dashboard_service is None:
        _dashboard_service = DashboardService()
    return _dashboard_service


def create_dashboard_service(
    metrics_db_path: Path | None = None,
    snapshots_path: Path | None = None,
) -> DashboardService:
    """Create a new dashboard service instance.

    Use this function to create isolated service instances.
    For the global singleton, use get_dashboard_service() instead.
    """
    return DashboardService(
        metrics_db_path=metrics_db_path,
        snapshots_path=snapshots_path,
    )


# =============================================================================
# Legacy Compatibility
# =============================================================================

# Aliases for backward compatibility with existing code
MetricsCollector = DashboardService
FleetMetricsSnapshot = DashboardSummary
AgentMetricsSnapshot = AgentMetricsSummary
TaskThroughputRecord = ThroughputRecord


def create_metrics_collector(storage_path: Path | None = None) -> DashboardService:
    """Create a metrics collector (legacy compatibility).

    Args:
        storage_path: Ignored (DashboardService uses its own storage)

    Returns:
        DashboardService instance
    """
    return DashboardService()
