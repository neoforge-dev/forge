"""
Persistent State Store for Multi-Agent Coordination
=================================================

Redis-based state management for cross-process agent coordination.

Schema:
--------
- agent:session:{session_id} (hash) - Agent session data
- assignment:{assignment_id} (hash) - Work assignment data
- worktree:{work_tree_path} (hash) - Work tree tracking
- work:locked:{task_id} (set) - Work lock registry (TTL: 30min)
- heartbeats (sorted set) - Agent heartbeats (score = timestamp)

Channels (Pub/Sub):
-------------------
- agent:status:{session_id} - Status change events
- agent:assignment - Assignment events
- agent:worklock - Work lock events
- agent:handoff - Handoff events

Fallback: SQLite for local development (when Redis unavailable)
"""

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

try:
    import redis
except ImportError:
    # For type checking
    if TYPE_CHECKING:
        import redis

# Define a Protocol for Redis PubSub for better typing support
if TYPE_CHECKING:

    class PubSubProtocol(Protocol):
        """Protocol for Redis PubSub interface."""

        def subscribe(self, *channels: str) -> None: ...
        def psubscribe(self, *patterns: str) -> None: ...
        def unsubscribe(self, *channels: str) -> None: ...
        def punsubscribe(self, *patterns: str) -> None: ...
        def get_message(self, timeout: float = 0.0) -> dict[str, Any] | None: ...
        def listen(self) -> Any: ...
        def close(self) -> None: ...

    # Type alias for PubSub return type
    RedisPubSub = PubSubProtocol
else:
    # Use Any in runtime to avoid importing redis client
    RedisPubSub = Any

from .logging_config import get_logger

logger = get_logger(__name__)


class AgentType(str, Enum):
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    GEMINI = "gemini"
    OPENCODE = "opencode"
    OTHER = "other"


class AgentRole(str, Enum):
    CTO = "cto"
    PM = "pm"
    BUILDER = "builder"
    QA = "qa"
    CONTENT = "content"


class AgentStatus(str, Enum):
    ACTIVE = "active"
    WAITING_HUMAN = "waiting_human"
    IDLE = "idle"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class AssignmentStatus(str, Enum):
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkTreeStatus(str, Enum):
    ACTIVE = "active"
    MERGING = "merging"
    CLEANUP = "cleanup"
    ABANDONED = "abandoned"


class ConflictRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class AgentSession:
    """Agent session data for persistent storage."""

    session_id: str
    agent_type: AgentType
    agent_role: AgentRole
    domain: str
    project: str
    status: AgentStatus = AgentStatus.ACTIVE
    current_task: str | None = None
    assignment_id: str | None = None
    work_tree_path: str | None = None
    registered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Optional metadata
    capabilities: list[str] = field(default_factory=list)
    preferences: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "session_id": str(self.session_id),
            "agent_type": str(self.agent_type.value),
            "agent_role": str(self.agent_role.value),
            "domain": str(self.domain),
            "project": str(self.project),
            "status": str(self.status.value),
            "current_task": str(self.current_task or ""),
            "assignment_id": str(self.assignment_id or ""),
            "work_tree_path": str(self.work_tree_path or ""),
            "registered_at": self.registered_at.isoformat(),
            "last_heartbeat": self.last_heartbeat.isoformat(),
            "capabilities": json.dumps(self.capabilities),
            "preferences": json.dumps(self.preferences),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentSession":
        """Create from dictionary."""
        import json

        # Check for required field
        if "session_id" not in data:
            raise KeyError("session_id")

        try:
            # Deserialize JSON fields
            capabilities = data.get("capabilities", "[]")
            preferences = data.get("preferences", "{}")

            if isinstance(capabilities, str):
                capabilities = json.loads(capabilities) if capabilities else []
            if isinstance(preferences, str):
                preferences = json.loads(preferences) if preferences else {}

            # Parse datetime fields
            registered_at = data.get("registered_at")
            last_heartbeat = data.get("last_heartbeat")

            return cls(
                session_id=data.get("session_id", ""),
                agent_type=AgentType(data.get("agent_type", "other")),
                agent_role=AgentRole(data.get("agent_role", "builder")),
                domain=data.get("domain", ""),
                project=data.get("project", ""),
                status=AgentStatus(data.get("status", "idle")),
                current_task=data.get("current_task") or None,
                assignment_id=data.get("assignment_id") or None,
                work_tree_path=data.get("work_tree_path") or None,
                registered_at=datetime.fromisoformat(registered_at)
                if registered_at
                else datetime.now(UTC),
                last_heartbeat=datetime.fromisoformat(last_heartbeat)
                if last_heartbeat
                else datetime.now(UTC),
                capabilities=capabilities,
                preferences=preferences,
            )
        except Exception as e:
            logger.error(f"Failed to create AgentSession from dict: {e}")
            raise


@dataclass
class WorkAssignment:
    """Work assignment data."""

    assignment_id: str
    session_id: str
    task_type: str
    task_description: str
    priority: int
    domain: str
    project: str
    files_affected: list[str]
    dependencies: list[str]
    status: AssignmentStatus = AssignmentStatus.ASSIGNED
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    # Conflict tracking
    blocked_by: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    conflict_risk: ConflictRisk = ConflictRisk.LOW

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "assignment_id": self.assignment_id,
            "session_id": self.session_id,
            "task_type": self.task_type,
            "task_description": self.task_description,
            "priority": self.priority,
            "domain": self.domain,
            "project": self.project,
            "files_affected": self.files_affected,
            "dependencies": self.dependencies,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "blocked_by": self.blocked_by,
            "blocking": self.blocking,
            "conflict_risk": self.conflict_risk.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkAssignment":
        """Create from dictionary."""
        return cls(
            assignment_id=data["assignment_id"],
            session_id=data["session_id"],
            task_type=data["task_type"],
            task_description=data["task_description"],
            priority=data["priority"],
            domain=data["domain"],
            project=data["project"],
            files_affected=data["files_affected"],
            dependencies=data["dependencies"],
            status=AssignmentStatus(data["status"]),
            started_at=datetime.fromisoformat(data["started_at"]),
            completed_at=datetime.fromisoformat(data["completed_at"])
            if data.get("completed_at")
            else None,
            blocked_by=data.get("blocked_by", []),
            blocking=data.get("blocking", []),
            conflict_risk=ConflictRisk(data.get("conflict_risk", "low")),
        )


@dataclass
class WorkTree:
    """Git work tree tracking."""

    work_tree_path: str
    assigned_session_id: str
    files_modified: list[str]
    branch_name: str
    base_commit: str
    status: WorkTreeStatus = WorkTreeStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "work_tree_path": self.work_tree_path,
            "assigned_session_id": self.assigned_session_id,
            "files_modified": self.files_modified,
            "branch_name": self.branch_name,
            "base_commit": self.base_commit,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkTree":
        """Create from dictionary."""
        return cls(
            work_tree_path=data["work_tree_path"],
            assigned_session_id=data["assigned_session_id"],
            files_modified=data["files_modified"],
            branch_name=data["branch_name"],
            base_commit=data["base_commit"],
            status=WorkTreeStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


# =============================================================================
# Redis Client
# =============================================================================


class RedisStateStore:
    """Redis-based persistent state store for multi-agent coordination."""

    def __init__(self, redis_url: str | None = None):
        """Initialize Redis client.

        Args:
            redis_url: Redis connection URL (default: env var REDIS_URL or localhost)
        """
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._client: redis.Redis | None = None
        self._connected = False

    def connect(self) -> bool:
        """Connect to Redis.

        Returns:
            True if successful, False otherwise.
        """
        try:
            self._client = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_timeout=1,
                socket_connect_timeout=1,
            )
            assert self._client is not None, "Redis client should be set"
            self._client.ping()
            self._connected = True
            logger.info(f"Connected to Redis at {self.redis_url}")
            return True
        except Exception as e:
            logger.warning(f"Failed to connect to Redis: {e}")
            self._connected = False
            self._client = None
            return False

    def disconnect(self):
        """Disconnect from Redis."""
        if self._client:
            self._client.close()
            self._connected = False

    def is_connected(self) -> bool:
        """Check if connected to Redis."""
        return self._connected and self._client is not None

    # =========================================================================
    # Agent Operations
    # =========================================================================

    def register_agent(self, session: AgentSession) -> bool:
        """Register an agent session.

        Args:
            session: Agent session data.

        Returns:
            True if successful.
        """
        if not self.is_connected():
            return False

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        key = f"agent:session:{session.session_id}"
        try:
            self._client.hset(key, mapping=session.to_dict())
            self._client.expire(key, 86400)  # 24 hours TTL
            self.update_heartbeat(session.session_id)
            return True
        except Exception as e:
            logger.error(f"Failed to register agent {session.session_id}: {e}")
            return False

    def get_agent(self, session_id: str) -> AgentSession | None:
        """Get agent session by ID.

        Args:
            session_id: Agent session ID.

        Returns:
            Agent session or None if not found.
        """
        if not self.is_connected():
            return None

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        key = f"agent:session:{session_id}"
        try:
            data = self._client.hgetall(key)
            if not data:
                return None
            return AgentSession.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to get agent {session_id}: {e}")
            return None

    def get_active_agents(
        self, domain: str | None = None, project: str | None = None
    ) -> list[AgentSession]:
        """Get all active agents.

        Args:
            domain: Filter by domain (optional).
            project: Filter by project (optional).

        Returns:
            List of active agent sessions.
        """
        if not self.is_connected():
            return []

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        try:
            pattern = "agent:session:*"
            keys = self._client.keys(pattern)

            agents = []
            for key in keys:
                data = self._client.hgetall(key)
                if not data:
                    continue

                try:
                    agent = AgentSession.from_dict(data)
                except (KeyError, Exception):
                    # Skip malformed entries
                    continue

                # Filter by domain/project
                if domain and agent.domain != domain:
                    continue
                if project and agent.project != project:
                    continue

                # Check if still active (heartbeat < 5 min)
                now = datetime.now(UTC)
                heartbeat = agent.last_heartbeat  # Already a datetime, no conversion needed
                if (now - heartbeat).total_seconds() > 300:
                    continue

                agents.append(agent)

            return agents
        except Exception as e:
            logger.error(f"Failed to get active agents: {e}")
            return []

    def update_agent_status(
        self,
        session_id: str,
        status: AgentStatus,
        current_task: str | None = None,
    ) -> bool:
        """Update agent status.

        Args:
            session_id: Agent session ID.
            status: New status.
            current_task: New current task (optional).

        Returns:
            True if successful.
        """
        if not self.is_connected():
            return False

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        key = f"agent:session:{session_id}"
        try:
            updates = {"status": status.value}
            if current_task:
                updates["current_task"] = current_task
            self._client.hset(key, mapping=updates)
            self.update_heartbeat(session_id)
            return True
        except Exception as e:
            logger.error(f"Failed to update agent status for {session_id}: {e}")
            return False

    def assign_work(
        self, session_id: str, assignment_id: str | None, work_tree_path: str | None = None
    ) -> bool:
        """Assign work to agent.

        Args:
            session_id: Agent session ID.
            assignment_id: Work assignment ID or None to clear assignment.
            work_tree_path: Work tree path (optional).

        Returns:
            True if successful.
        """
        if not self.is_connected():
            return False

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        key = f"agent:session:{session_id}"
        try:
            updates = {
                "assignment_id": assignment_id if assignment_id is not None else "",
            }
            if work_tree_path:
                updates["work_tree_path"] = work_tree_path
            self._client.hset(key, mapping=updates)
            return True
        except Exception as e:
            logger.error(f"Failed to assign work to agent {session_id}: {e}")
            return False

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        key = f"agent:session:{session_id}"
        try:
            updates = {
                "assignment_id": assignment_id,
            }
            if work_tree_path:
                updates["work_tree_path"] = work_tree_path
            self._client.hset(key, mapping=updates)
            return True
        except Exception as e:
            logger.error(f"Failed to assign work to agent {session_id}: {e}")
            return False

    # =========================================================================
    # Heartbeat System
    # =========================================================================

    def update_heartbeat(self, session_id: str) -> bool:
        """Update agent heartbeat.

        Args:
            session_id: Agent session ID.

        Returns:
            True if successful.
        """
        if not self.is_connected():
            return False

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        try:
            now = time.time()
            self._client.zadd("heartbeats", {session_id: now})
            return True
        except Exception as e:
            logger.error(f"Failed to update heartbeat for {session_id}: {e}")
            return False

    def get_heartbeat_time(self, session_id: str) -> float | None:
        """Get last heartbeat time for agent.

        Args:
            session_id: Agent session ID.

        Returns:
            Unix timestamp or None if not found.
        """
        if not self.is_connected():
            return None

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        try:
            score = self._client.zscore("heartbeats", session_id)
            return float(score) if score else None
        except Exception as e:
            logger.error(f"Failed to get heartbeat for {session_id}: {e}")
            return None

    def cleanup_dead_heartbeats(self, timeout_seconds: int = 300) -> list[str]:
        """Remove dead agents from heartbeat tracker.

        Args:
            timeout_seconds: Seconds of inactivity before considered dead.

        Returns:
            List of dead session IDs.
        """
        if not self.is_connected():
            return []

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        try:
            cutoff = time.time() - timeout_seconds
            dead_agents = self._client.zrangebyscore("heartbeats", 0, cutoff)
            if dead_agents:
                self._client.zrem("heartbeats", *dead_agents)
                logger.info(f"Cleaned up {len(dead_agents)} dead agents from heartbeats")
            return dead_agents
        except Exception as e:
            logger.error(f"Failed to cleanup dead heartbeats: {e}")
            return []

    # =========================================================================
    # Work Lock Registry
    # =========================================================================

    def acquire_work_lock(self, task_id: str, session_id: str, ttl_seconds: int = 1800) -> bool:
        """Acquire lock on a task.

        Args:
            task_id: Task identifier (e.g., "github-issue-42").
            session_id: Agent session ID acquiring the lock.
            ttl_seconds: Time to live in seconds (default: 30 min).

        Returns:
            True if lock acquired, False if already locked.
        """
        if not self.is_connected():
            return False

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        key = f"work:locked:{task_id}"
        try:
            # Try to set if not exists (NX)
            result = self._client.set(key, session_id, nx=True, ex=ttl_seconds)
            acquired = result is not None
            if acquired:
                logger.debug(f"Work lock acquired for {task_id} by {session_id}")
            else:
                logger.debug(f"Work lock already held for {task_id}")
            return acquired
        except Exception as e:
            logger.error(f"Failed to acquire work lock for {task_id}: {e}")
            return False

    def release_work_lock(self, task_id: str, session_id: str) -> bool:
        """Release lock on a task.

        Args:
            task_id: Task identifier.
            session_id: Agent session ID releasing the lock.

        Returns:
            True if lock released, False if lock not held by this session.
        """
        if not self.is_connected():
            return False

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        key = f"work:locked:{task_id}"
        try:
            # Check if lock is held by this session
            holder = self._client.get(key)
            if holder == session_id:
                self._client.delete(key)
                logger.debug(f"Work lock released for {task_id} by {session_id}")
                return True
            else:
                logger.warning(f"Work lock for {task_id} not held by {session_id}")
                return False
        except Exception as e:
            logger.error(f"Failed to release work lock for {task_id}: {e}")
            return False

    def is_work_locked(self, task_id: str) -> bool:
        """Check if task is locked.

        Args:
            task_id: Task identifier.

        Returns:
            True if locked.
        """
        if not self.is_connected():
            return False

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        key = f"work:locked:{task_id}"
        try:
            return self._client.exists(key) > 0
        except Exception as e:
            logger.error(f"Failed to check work lock for {task_id}: {e}")
            return False

    def get_work_lock_holder(self, task_id: str) -> str | None:
        """Get the session ID holding the work lock.

        Args:
            task_id: Task identifier.

        Returns:
            Session ID or None if not locked.
        """
        if not self.is_connected():
            return None

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        key = f"work:locked:{task_id}"
        try:
            return self._client.get(key)
        except Exception as e:
            logger.error(f"Failed to get work lock holder for {task_id}: {e}")
            return None

    # =========================================================================
    # Assignment Operations
    # =========================================================================

    def create_assignment(self, assignment: WorkAssignment) -> bool:
        """Create a work assignment.

        Args:
            assignment: Work assignment data.

        Returns:
            True if successful.
        """
        if not self.is_connected():
            return False

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        key = f"assignment:{assignment.assignment_id}"
        try:
            self._client.hset(key, mapping=assignment.to_dict())
            self._client.expire(key, 86400 * 7)  # 7 days TTL
            return True
        except Exception as e:
            logger.error(f"Failed to create assignment {assignment.assignment_id}: {e}")
            return False

    def get_assignment(self, assignment_id: str) -> WorkAssignment | None:
        """Get assignment by ID.

        Args:
            assignment_id: Assignment ID.

        Returns:
            Work assignment or None if not found.
        """
        if not self.is_connected():
            return None

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        key = f"assignment:{assignment_id}"
        try:
            data = self._client.hgetall(key)
            if not data:
                return None
            return WorkAssignment.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to get assignment {assignment_id}: {e}")
            return None

    def update_assignment_status(
        self,
        assignment_id: str,
        status: AssignmentStatus,
        completed_at: datetime | None = None,
    ) -> bool:
        """Update assignment status.

        Args:
            assignment_id: Assignment ID.
            status: New status.
            completed_at: Completion timestamp (optional).

        Returns:
            True if successful.
        """
        if not self.is_connected():
            return False

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        key = f"assignment:{assignment_id}"
        try:
            updates = {"status": status.value}
            if completed_at:
                updates["completed_at"] = completed_at.isoformat()
            self._client.hset(key, mapping=updates)
            return True
        except Exception as e:
            logger.error(f"Failed to update assignment status for {assignment_id}: {e}")
            return False

    def list_assignments(
        self,
        domain: str | None = None,
        project: str | None = None,
        status: AssignmentStatus | None = None,
    ) -> list[WorkAssignment]:
        """List all work assignments with optional filters.

        Args:
            domain: Filter by domain (optional).
            project: Filter by project (optional).
            status: Filter by assignment status (optional).

        Returns:
            List of matching work assignments.
        """
        if not self.is_connected():
            return []

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        try:
            pattern = "assignment:*"
            keys = self._client.keys(pattern)

            assignments = []
            for key in keys:
                data = self._client.hgetall(key)
                if not data:
                    continue

                assignment = WorkAssignment.from_dict(data)

                # Apply filters
                if domain and assignment.domain != domain:
                    continue
                if project and assignment.project != project:
                    continue
                if status and assignment.status != status:
                    continue

                assignments.append(assignment)

            # Sort by priority (higher = more important) then by started_at
            assignments.sort(key=lambda a: (-a.priority, a.started_at))

            logger.debug(
                f"Listed {len(assignments)} assignments (domain={domain}, project={project}, status={status})"
            )
            return assignments
        except Exception as e:
            logger.error(f"Failed to list assignments: {e}")
            return []

    # =========================================================================
    # Work Tree Operations
    # =========================================================================

    def register_work_tree(self, work_tree: WorkTree) -> bool:
        """Register a work tree.

        Args:
            work_tree: Work tree data.

        Returns:
            True if successful.
        """
        if not self.is_connected():
            return False

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        key = f"worktree:{work_tree.work_tree_path}"
        try:
            self._client.hset(key, mapping=work_tree.to_dict())
            self._client.expire(key, 86400)  # 24 hours TTL
            return True
        except Exception as e:
            logger.error(f"Failed to register work tree {work_tree.work_tree_path}: {e}")
            return False

    def get_work_tree(self, work_tree_path: str) -> WorkTree | None:
        """Get work tree by path.

        Args:
            work_tree_path: Work tree path.

        Returns:
            Work tree or None if not found.
        """
        if not self.is_connected():
            return None

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        key = f"worktree:{work_tree_path}"
        try:
            data = self._client.hgetall(key)
            if not data:
                return None
            return WorkTree.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to get work tree {work_tree_path}: {e}")
            return None

    def get_work_trees_for_session(self, session_id: str) -> list[WorkTree]:
        """Get all work trees for a session.

        Args:
            session_id: Agent session ID.

        Returns:
            List of work trees.
        """
        if not self.is_connected():
            return []

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        try:
            pattern = "worktree:*"
            keys = self._client.keys(pattern)

            work_trees = []
            for key in keys:
                data = self._client.hgetall(key)
                if not data:
                    continue

                work_tree = WorkTree.from_dict(data)
                if work_tree.assigned_session_id == session_id:
                    work_trees.append(work_tree)

            return work_trees
        except Exception as e:
            logger.error(f"Failed to get work trees for session {session_id}: {e}")
            return []

    # =========================================================================
    # Pub/Sub (Status Broadcasting)
    # =========================================================================

    def publish_status_change(self, session_id: str, status_data: dict[str, Any]) -> bool:
        """Publish agent status change event.

        Args:
            session_id: Agent session ID.
            status_data: Status change payload.

        Returns:
            True if successful.
        """
        if not self.is_connected():
            return False

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        channel = f"agent:status:{session_id}"
        try:
            self._client.publish(channel, json.dumps(status_data))
            return True
        except Exception as e:
            logger.error(f"Failed to publish status change for {session_id}: {e}")
            return False

    def subscribe_to_agent(self, session_id: str) -> "RedisPubSub | None":
        """Subscribe to agent status changes.

        Args:
            session_id: Agent session ID.

        Returns:
            PubSub object or None if not connected.
        """
        if not self.is_connected():
            return None

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        channel = f"agent:status:{session_id}"
        try:
            pubsub = self._client.pubsub()
            pubsub.subscribe(channel)
            if TYPE_CHECKING:
                return cast(RedisPubSub, pubsub)
            return pubsub
        except Exception as e:
            logger.error(f"Failed to subscribe to agent {session_id}: {e}")
            return None

    def subscribe_to_all_agents(self) -> "RedisPubSub | None":
        """Subscribe to all agent status change events.

        Returns:
            Redis PubSub object or None if not connected or not using Redis.
        """
        if self.get_store_type() == "redis":
            # Get pubsub object and cast it to our RedisPubSub type for type checking
            if hasattr(self, "_redis"):
                pubsub = self._redis.subscribe_to_all_agents()
                if TYPE_CHECKING:
                    return cast(RedisPubSub, pubsub)
                return pubsub

        # SQLite doesn't support pub/sub directly or not connected to Redis
        logger.info(f"{self.get_store_type()}: Pub/Sub not supported")
        return None

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        try:
            pubsub = self._client.pubsub()
            pubsub.psubscribe("agent:status:*")
            return pubsub
        except Exception as e:
            logger.error(f"Failed to subscribe to all agents: {e}")
            return None


# =============================================================================
# SQLite Fallback
# =============================================================================


class SQLiteStateStore:
    """SQLite fallback for local development (when Redis unavailable)."""

    def __init__(self, db_path: str | None = None):
        """Initialize SQLite database.

        Args:
            db_path: Database file path (default: .forge/state.db)
        """
        if db_path is None:
            forge_root = Path.cwd()
            db_path = str(forge_root / ".forge/state.db")

        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> bool:
        """Connect to SQLite database."""
        try:
            self._conn = sqlite3.connect(self.db_path)
            self._create_tables()
            logger.info(f"Connected to SQLite at {self.db_path}")
            return True
        except Exception as e:
            logger.warning(f"Failed to connect to SQLite: {e}")
            return False

    def disconnect(self):
        """Disconnect from SQLite."""
        if self._conn:
            self._conn.close()

    def _create_tables(self):
        """Create tables if not exist."""
        if not self._conn:
            return

        cursor = self._conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                session_id TEXT PRIMARY KEY,
                agent_type TEXT NOT NULL,
                agent_role TEXT NOT NULL,
                domain TEXT NOT NULL,
                project TEXT NOT NULL,
                status TEXT NOT NULL,
                current_task TEXT,
                assignment_id TEXT,
                work_tree_path TEXT,
                registered_at TEXT NOT NULL,
                last_heartbeat TEXT NOT NULL,
                capabilities TEXT,
                preferences TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS assignments (
                assignment_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                task_description TEXT NOT NULL,
                priority INTEGER NOT NULL,
                domain TEXT NOT NULL,
                project TEXT NOT NULL,
                files_affected TEXT NOT NULL,
                dependencies TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                blocked_by TEXT,
                blocking TEXT,
                conflict_risk TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_trees (
                work_tree_path TEXT PRIMARY KEY,
                assigned_session_id TEXT NOT NULL,
                files_modified TEXT NOT NULL,
                branch_name TEXT NOT NULL,
                base_commit TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS work_locks (
                task_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                locked_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)

        self._conn.commit()


# =============================================================================
# State Store Manager (Auto-fallback)
# =============================================================================


class StateStore:
    """State store manager with automatic Redis/SQLite fallback."""

    def __init__(self, redis_url: str | None = None, sqlite_path: str | None = None):
        """Initialize state store.

        Args:
            redis_url: Redis connection URL (optional).
            sqlite_path: SQLite database path (optional).
        """
        self._redis = RedisStateStore(redis_url)
        self._sqlite = SQLiteStateStore(sqlite_path)
        self._store_type: str = "none"

    def connect(self) -> str:
        """Connect to state store (Redis or SQLite fallback).

        Returns:
            Store type: "redis" or "sqlite".
        """
        # Try Redis first
        if self._redis.connect():
            self._store_type = "redis"
            return "redis"

        # Fallback to SQLite
        if self._sqlite.connect():
            self._store_type = "sqlite"
            logger.info("Using SQLite state store (Redis unavailable)")
            return "sqlite"

        self._store_type = "none"
        logger.error("Failed to connect to any state store")
        return "none"

    def disconnect(self):
        """Disconnect from state store."""
        self._redis.disconnect()
        self._sqlite.disconnect()

    def is_connected(self) -> bool:
        """Check if connected to any state store."""
        return self._store_type != "none"

    def get_store_type(self) -> str:
        """Get current store type."""
        return self._store_type

    # Delegate to active store
    def register_agent(self, session: AgentSession) -> bool:
        if self._store_type == "redis":
            return self._redis.register_agent(session)
        elif self._store_type == "sqlite":
            return self._sqlite_register_agent(session)
        return False

    def _sqlite_register_agent(self, session: AgentSession) -> bool:
        """Register agent in SQLite."""
        if not self._sqlite._conn:
            return False

        try:
            cursor = self._sqlite._conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO agents (
                    session_id, agent_type, agent_role, domain, project,
                    status, current_task, assignment_id, work_tree_path,
                    registered_at, last_heartbeat, capabilities, preferences
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    session.session_id,
                    session.agent_type.value,
                    session.agent_role.value,
                    session.domain,
                    session.project,
                    session.status.value,
                    session.current_task,
                    session.assignment_id,
                    session.work_tree_path,
                    session.registered_at.isoformat(),
                    session.last_heartbeat.isoformat(),
                    json.dumps(session.capabilities),
                    json.dumps(session.preferences),
                ),
            )
            self._sqlite._conn.commit()
            logger.debug(f"SQLite: Registered agent {session.session_id}")
            return True
        except Exception as e:
            logger.error(f"SQLite: Failed to register agent {session.session_id}: {e}")
            return False

    def get_agent(self, session_id: str) -> AgentSession | None:
        if self._store_type == "redis":
            return self._redis.get_agent(session_id)
        elif self._store_type == "sqlite":
            return self._sqlite_get_agent(session_id)
        return None

    def _sqlite_get_agent(self, session_id: str) -> AgentSession | None:
        """Get agent from SQLite."""
        if not self._sqlite._conn:
            return None

        try:
            cursor = self._sqlite._conn.cursor()
            cursor.execute("SELECT * FROM agents WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return AgentSession(
                session_id=row[0],
                agent_type=AgentType(row[1]),
                agent_role=AgentRole(row[2]),
                domain=row[3],
                project=row[4],
                status=AgentStatus(row[5]),
                current_task=row[6],
                assignment_id=row[7],
                work_tree_path=row[8],
                registered_at=datetime.fromisoformat(row[9]),
                last_heartbeat=datetime.fromisoformat(row[10]),
                capabilities=json.loads(row[11]) if row[11] else [],
                preferences=json.loads(row[12]) if row[12] else {},
            )
        except Exception as e:
            logger.error(f"SQLite: Failed to get agent {session_id}: {e}")
            return None

    def get_active_agents(
        self, domain: str | None = None, project: str | None = None
    ) -> list[AgentSession]:
        if self._store_type == "redis":
            return self._redis.get_active_agents(domain, project)
        elif self._store_type == "sqlite":
            return self._sqlite_get_active_agents(domain, project)
        return []

    def _sqlite_get_active_agents(
        self, domain: str | None = None, project: str | None = None
    ) -> list[AgentSession]:
        """Get active agents from SQLite."""
        if not self._sqlite._conn:
            return []

        try:
            cursor = self._sqlite._conn.cursor()

            # Build query with filters
            query = "SELECT * FROM agents WHERE 1=1"
            params: list[Any] = []

            if domain:
                query += " AND domain = ?"
                params.append(domain)
            if project:
                query += " AND project = ?"
                params.append(project)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            agents = []
            now = datetime.now(UTC)

            for row in rows:
                # Check heartbeat (< 5 minutes)
                last_heartbeat = datetime.fromisoformat(row[10])
                if (now - last_heartbeat).total_seconds() > 300:
                    continue

                agent = AgentSession(
                    session_id=row[0],
                    agent_type=AgentType(row[1]),
                    agent_role=AgentRole(row[2]),
                    domain=row[3],
                    project=row[4],
                    status=AgentStatus(row[5]),
                    current_task=row[6],
                    assignment_id=row[7],
                    work_tree_path=row[8],
                    registered_at=datetime.fromisoformat(row[9]),
                    last_heartbeat=last_heartbeat,
                    capabilities=json.loads(row[11]) if row[11] else [],
                    preferences=json.loads(row[12]) if row[12] else {},
                )
                agents.append(agent)

            logger.debug(f"SQLite: Found {len(agents)} active agents")
            return agents
        except Exception as e:
            logger.error(f"SQLite: Failed to get active agents: {e}")
            return []

    def update_agent_status(
        self, session_id: str, status: AgentStatus, current_task: str | None = None
    ) -> bool:
        if self._store_type == "redis":
            return self._redis.update_agent_status(session_id, status, current_task)
        elif self._store_type == "sqlite":
            return self._sqlite_update_agent_status(session_id, status, current_task)
        return False

    def _sqlite_update_agent_status(
        self, session_id: str, status: AgentStatus, current_task: str | None = None
    ) -> bool:
        """Update agent status in SQLite."""
        if not self._sqlite._conn:
            return False

        try:
            cursor = self._sqlite._conn.cursor()
            now = datetime.now(UTC).isoformat()

            if current_task is not None:
                cursor.execute(
                    """
                    UPDATE agents
                    SET status = ?, current_task = ?, last_heartbeat = ?
                    WHERE session_id = ?
                """,
                    (status.value, current_task, now, session_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE agents
                    SET status = ?, last_heartbeat = ?
                    WHERE session_id = ?
                """,
                    (status.value, now, session_id),
                )

            self._sqlite._conn.commit()
            logger.debug(f"SQLite: Updated agent {session_id} status to {status.value}")
            return True
        except Exception as e:
            logger.error(f"SQLite: Failed to update agent status for {session_id}: {e}")
            return False

    def assign_work(
        self, session_id: str, assignment_id: str | None, work_tree_path: str | None = None
    ) -> bool:
        if self._store_type == "redis":
            return self._redis.assign_work(session_id, assignment_id, work_tree_path)
        elif self._store_type == "sqlite":
            return self._sqlite_assign_work(session_id, assignment_id, work_tree_path)
        return False

    def _sqlite_assign_work(
        self, session_id: str, assignment_id: str | None, work_tree_path: str | None = None
    ) -> bool:
        """Assign work to agent in SQLite."""
        if not self._sqlite._conn:
            return False

        try:
            cursor = self._sqlite._conn.cursor()

            if work_tree_path is not None:
                cursor.execute(
                    """
                    UPDATE agents
                    SET assignment_id = ?, work_tree_path = ?
                    WHERE session_id = ?
                """,
                    (assignment_id or "", work_tree_path, session_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE agents
                    SET assignment_id = ?
                    WHERE session_id = ?
                """,
                    (assignment_id or "", session_id),
                )

            self._sqlite._conn.commit()
            logger.debug(f"SQLite: Assigned work {assignment_id} to agent {session_id}")
            return True
        except Exception as e:
            logger.error(f"SQLite: Failed to assign work to agent {session_id}: {e}")
            return False

    def update_heartbeat(self, session_id: str) -> bool:
        if self._store_type == "redis":
            return self._redis.update_heartbeat(session_id)
        elif self._store_type == "sqlite":
            return self._sqlite_update_heartbeat(session_id)
        return False

    def _sqlite_update_heartbeat(self, session_id: str) -> bool:
        """Update heartbeat in SQLite."""
        if not self._sqlite._conn:
            return False

        try:
            cursor = self._sqlite._conn.cursor()
            now = datetime.now(UTC).isoformat()

            cursor.execute(
                """
                UPDATE agents
                SET last_heartbeat = ?
                WHERE session_id = ?
            """,
                (now, session_id),
            )

            self._sqlite._conn.commit()
            logger.debug(f"SQLite: Updated heartbeat for agent {session_id}")
            return True
        except Exception as e:
            logger.error(f"SQLite: Failed to update heartbeat for {session_id}: {e}")
            return False

    def acquire_work_lock(self, task_id: str, session_id: str, ttl_seconds: int = 1800) -> bool:
        if self._store_type == "redis":
            return self._redis.acquire_work_lock(task_id, session_id, ttl_seconds)
        elif self._store_type == "sqlite":
            return self._sqlite_acquire_work_lock(task_id, session_id, ttl_seconds)
        return False

    def _sqlite_acquire_work_lock(
        self, task_id: str, session_id: str, ttl_seconds: int = 1800
    ) -> bool:
        """Acquire work lock in SQLite."""
        if not self._sqlite._conn:
            return False

        try:
            cursor = self._sqlite._conn.cursor()
            now = datetime.now(UTC)
            expires_at = now + timedelta(seconds=ttl_seconds)

            # Check if lock exists and is not expired
            cursor.execute(
                """
                SELECT session_id, expires_at FROM work_locks WHERE task_id = ?
            """,
                (task_id,),
            )
            row = cursor.fetchone()

            if row:
                # Lock exists, check if expired
                existing_expires = datetime.fromisoformat(row[1])
                if existing_expires > now:
                    # Lock still valid
                    logger.debug(f"SQLite: Work lock already held for {task_id}")
                    return False
                # Lock expired, delete it
                cursor.execute("DELETE FROM work_locks WHERE task_id = ?", (task_id,))

            # Acquire lock
            cursor.execute(
                """
                INSERT INTO work_locks (task_id, session_id, locked_at, expires_at)
                VALUES (?, ?, ?, ?)
            """,
                (task_id, session_id, now.isoformat(), expires_at.isoformat()),
            )

            self._sqlite._conn.commit()
            logger.debug(f"SQLite: Work lock acquired for {task_id} by {session_id}")
            return True
        except Exception as e:
            logger.error(f"SQLite: Failed to acquire work lock for {task_id}: {e}")
            return False

    def release_work_lock(self, task_id: str, session_id: str) -> bool:
        if self._store_type == "redis":
            return self._redis.release_work_lock(task_id, session_id)
        elif self._store_type == "sqlite":
            return self._sqlite_release_work_lock(task_id, session_id)
        return False

    def _sqlite_release_work_lock(self, task_id: str, session_id: str) -> bool:
        """Release work lock in SQLite."""
        if not self._sqlite._conn:
            return False

        try:
            cursor = self._sqlite._conn.cursor()

            # Check if lock is held by this session
            cursor.execute(
                """
                SELECT session_id FROM work_locks WHERE task_id = ?
            """,
                (task_id,),
            )
            row = cursor.fetchone()

            if not row:
                logger.warning(f"SQLite: No lock found for {task_id}")
                return False

            if row[0] != session_id:
                logger.warning(f"SQLite: Work lock for {task_id} not held by {session_id}")
                return False

            # Release lock
            cursor.execute("DELETE FROM work_locks WHERE task_id = ?", (task_id,))
            self._sqlite._conn.commit()
            logger.debug(f"SQLite: Work lock released for {task_id} by {session_id}")
            return True
        except Exception as e:
            logger.error(f"SQLite: Failed to release work lock for {task_id}: {e}")
            return False

    def is_work_locked(self, task_id: str) -> bool:
        if self._store_type == "redis":
            return self._redis.is_work_locked(task_id)
        elif self._store_type == "sqlite":
            return self._sqlite_is_work_locked(task_id)
        return False

    def _sqlite_is_work_locked(self, task_id: str) -> bool:
        """Check if work is locked in SQLite."""
        if not self._sqlite._conn:
            return False

        try:
            cursor = self._sqlite._conn.cursor()
            now = datetime.now(UTC)

            cursor.execute(
                """
                SELECT expires_at FROM work_locks WHERE task_id = ?
            """,
                (task_id,),
            )
            row = cursor.fetchone()

            if not row:
                return False

            # Check if lock is expired
            expires_at = datetime.fromisoformat(row[0])
            if expires_at <= now:
                # Lock expired, clean it up
                cursor.execute("DELETE FROM work_locks WHERE task_id = ?", (task_id,))
                self._sqlite._conn.commit()
                return False

            return True
        except Exception as e:
            logger.error(f"SQLite: Failed to check work lock for {task_id}: {e}")
            return False

    def get_work_lock_holder(self, task_id: str) -> str | None:
        """Get the session ID holding the work lock."""
        if self._store_type == "redis":
            return self._redis.get_work_lock_holder(task_id)
        elif self._store_type == "sqlite":
            return self._sqlite_get_work_lock_holder(task_id)
        return None

    def _sqlite_get_work_lock_holder(self, task_id: str) -> str | None:
        """Get work lock holder from SQLite."""
        if not self._sqlite._conn:
            return None

        try:
            cursor = self._sqlite._conn.cursor()
            now = datetime.now(UTC)

            cursor.execute(
                """
                SELECT session_id, expires_at FROM work_locks WHERE task_id = ?
            """,
                (task_id,),
            )
            row = cursor.fetchone()

            if not row:
                return None

            # Check if lock is expired
            expires_at = datetime.fromisoformat(row[1])
            if expires_at <= now:
                # Lock expired, clean it up
                cursor.execute("DELETE FROM work_locks WHERE task_id = ?", (task_id,))
                self._sqlite._conn.commit()
                return None

            return row[0]
        except Exception as e:
            logger.error(f"SQLite: Failed to get work lock holder for {task_id}: {e}")
            return None

    def create_assignment(self, assignment: WorkAssignment) -> bool:
        if self._store_type == "redis":
            return self._redis.create_assignment(assignment)
        elif self._store_type == "sqlite":
            return self._sqlite_create_assignment(assignment)
        return False

    def _sqlite_create_assignment(self, assignment: WorkAssignment) -> bool:
        """Create assignment in SQLite."""
        if not self._sqlite._conn:
            return False

        try:
            cursor = self._sqlite._conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO assignments (
                    assignment_id, session_id, task_type, task_description,
                    priority, domain, project, files_affected, dependencies,
                    status, started_at, completed_at, blocked_by, blocking,
                    conflict_risk
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    assignment.assignment_id,
                    assignment.session_id,
                    assignment.task_type,
                    assignment.task_description,
                    assignment.priority,
                    assignment.domain,
                    assignment.project,
                    json.dumps(assignment.files_affected),
                    json.dumps(assignment.dependencies),
                    assignment.status.value,
                    assignment.started_at.isoformat(),
                    assignment.completed_at.isoformat() if assignment.completed_at else None,
                    json.dumps(assignment.blocked_by),
                    json.dumps(assignment.blocking),
                    assignment.conflict_risk.value,
                ),
            )
            self._sqlite._conn.commit()
            logger.debug(f"SQLite: Created assignment {assignment.assignment_id}")
            return True
        except Exception as e:
            logger.error(f"SQLite: Failed to create assignment {assignment.assignment_id}: {e}")
            return False

    def get_assignment(self, assignment_id: str) -> WorkAssignment | None:
        if self._store_type == "redis":
            return self._redis.get_assignment(assignment_id)
        elif self._store_type == "sqlite":
            return self._sqlite_get_assignment(assignment_id)
        return None

    def _sqlite_get_assignment(self, assignment_id: str) -> WorkAssignment | None:
        """Get assignment from SQLite."""
        if not self._sqlite._conn:
            return None

        try:
            cursor = self._sqlite._conn.cursor()
            cursor.execute("SELECT * FROM assignments WHERE assignment_id = ?", (assignment_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return WorkAssignment(
                assignment_id=row[0],
                session_id=row[1],
                task_type=row[2],
                task_description=row[3],
                priority=row[4],
                domain=row[5],
                project=row[6],
                files_affected=json.loads(row[7]) if row[7] else [],
                dependencies=json.loads(row[8]) if row[8] else [],
                status=AssignmentStatus(row[9]),
                started_at=datetime.fromisoformat(row[10]),
                completed_at=datetime.fromisoformat(row[11]) if row[11] else None,
                blocked_by=json.loads(row[12]) if row[12] else [],
                blocking=json.loads(row[13]) if row[13] else [],
                conflict_risk=ConflictRisk(row[14]),
            )
        except Exception as e:
            logger.error(f"SQLite: Failed to get assignment {assignment_id}: {e}")
            return None

    def update_assignment_status(
        self,
        assignment_id: str,
        status: AssignmentStatus,
        completed_at: datetime | None = None,
    ) -> bool:
        """Update assignment status."""
        if self._store_type == "redis":
            return self._redis.update_assignment_status(assignment_id, status, completed_at)
        elif self._store_type == "sqlite":
            return self._sqlite_update_assignment_status(assignment_id, status, completed_at)
        return False

    def _sqlite_update_assignment_status(
        self,
        assignment_id: str,
        status: AssignmentStatus,
        completed_at: datetime | None = None,
    ) -> bool:
        """Update assignment status in SQLite."""
        if not self._sqlite._conn:
            return False

        try:
            cursor = self._sqlite._conn.cursor()

            if completed_at is not None:
                cursor.execute(
                    """
                    UPDATE assignments
                    SET status = ?, completed_at = ?
                    WHERE assignment_id = ?
                """,
                    (status.value, completed_at.isoformat(), assignment_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE assignments
                    SET status = ?
                    WHERE assignment_id = ?
                """,
                    (status.value, assignment_id),
                )

            self._sqlite._conn.commit()
            logger.debug(f"SQLite: Updated assignment {assignment_id} status to {status.value}")
            return True
        except Exception as e:
            logger.error(f"SQLite: Failed to update assignment status for {assignment_id}: {e}")
            return False

    def list_assignments(
        self,
        domain: str | None = None,
        project: str | None = None,
        status: AssignmentStatus | None = None,
    ) -> list[WorkAssignment]:
        """List all work assignments with optional filters.

        Args:
            domain: Filter by domain (optional).
            project: Filter by project (optional).
            status: Filter by assignment status (optional).

        Returns:
            List of matching work assignments.
        """
        if self._store_type == "redis":
            return self._redis.list_assignments(domain, project, status)
        elif self._store_type == "sqlite":
            # SQLite implementation
            if not self._sqlite._conn:
                return []

            try:
                import json

                cursor = self._sqlite._conn.cursor()

                # Build query with optional filters
                query = "SELECT * FROM assignments WHERE 1=1"
                params: list[Any] = []

                if domain:
                    query += " AND domain = ?"
                    params.append(domain)
                if project:
                    query += " AND project = ?"
                    params.append(project)
                if status:
                    query += " AND status = ?"
                    params.append(status.value)

                query += " ORDER BY priority DESC, started_at ASC"

                cursor.execute(query, params)
                rows = cursor.fetchall()

                assignments = []
                for row in rows:
                    # Map row to WorkAssignment (matches table schema order)
                    assignment = WorkAssignment(
                        assignment_id=row[0],
                        session_id=row[1],
                        task_type=row[2],
                        task_description=row[3],
                        priority=row[4],
                        domain=row[5],
                        project=row[6],
                        files_affected=json.loads(row[7]) if isinstance(row[7], str) else [],
                        dependencies=json.loads(row[8]) if isinstance(row[8], str) else [],
                        status=AssignmentStatus(row[9]),
                        started_at=datetime.fromisoformat(row[10]),
                        completed_at=datetime.fromisoformat(row[11]) if row[11] else None,
                        blocked_by=json.loads(row[12])
                        if row[12] and isinstance(row[12], str)
                        else [],
                        blocking=json.loads(row[13])
                        if row[13] and isinstance(row[13], str)
                        else [],
                        conflict_risk=ConflictRisk(row[14]) if row[14] else ConflictRisk.LOW,
                    )
                    assignments.append(assignment)

                logger.debug(f"SQLite: Listed {len(assignments)} assignments")
                return assignments
            except Exception as e:
                logger.error(f"Failed to list assignments from SQLite: {e}")
                return []
        return []

    def register_work_tree(self, work_tree: WorkTree) -> bool:
        if self._store_type == "redis":
            return self._redis.register_work_tree(work_tree)
        elif self._store_type == "sqlite":
            return self._sqlite_register_work_tree(work_tree)
        return False

    def _sqlite_register_work_tree(self, work_tree: WorkTree) -> bool:
        """Register work tree in SQLite."""
        if not self._sqlite._conn:
            return False

        try:
            cursor = self._sqlite._conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO work_trees (
                    work_tree_path, assigned_session_id, files_modified,
                    branch_name, base_commit, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    work_tree.work_tree_path,
                    work_tree.assigned_session_id,
                    json.dumps(work_tree.files_modified),
                    work_tree.branch_name,
                    work_tree.base_commit,
                    work_tree.status.value,
                    work_tree.created_at.isoformat(),
                ),
            )
            self._sqlite._conn.commit()
            logger.debug(f"SQLite: Registered work tree {work_tree.work_tree_path}")
            return True
        except Exception as e:
            logger.error(f"SQLite: Failed to register work tree {work_tree.work_tree_path}: {e}")
            return False

    def get_work_tree(self, work_tree_path: str) -> WorkTree | None:
        """Get work tree by path."""
        if self._store_type == "redis":
            return self._redis.get_work_tree(work_tree_path)
        elif self._store_type == "sqlite":
            return self._sqlite_get_work_tree(work_tree_path)
        return None

    def _sqlite_get_work_tree(self, work_tree_path: str) -> WorkTree | None:
        """Get work tree from SQLite."""
        if not self._sqlite._conn:
            return None

        try:
            cursor = self._sqlite._conn.cursor()
            cursor.execute("SELECT * FROM work_trees WHERE work_tree_path = ?", (work_tree_path,))
            row = cursor.fetchone()

            if not row:
                return None

            return WorkTree(
                work_tree_path=row[0],
                assigned_session_id=row[1],
                files_modified=json.loads(row[2]) if row[2] else [],
                branch_name=row[3],
                base_commit=row[4],
                status=WorkTreeStatus(row[5]),
                created_at=datetime.fromisoformat(row[6]),
            )
        except Exception as e:
            logger.error(f"SQLite: Failed to get work tree {work_tree_path}: {e}")
            return None

    def list_work_trees(self) -> list[WorkTree]:
        """List all work trees.

        Returns:
            List of all registered work trees.
        """
        if self._store_type == "redis":
            return self._redis.get_work_trees_for_session("")  # Empty string to get all
        elif self._store_type == "sqlite":
            # Basic SQLite implementation (stub)
            logger.info("SQLite: Listing all work trees")
            return []
        return []

    def get_work_trees_for_session(self, session_id: str | None) -> list[WorkTree]:
        """Get work trees for session.

        Args:
            session_id: Agent session ID or empty string for all trees.

        Returns:
            List of work trees.
        """
        if self._store_type == "redis":
            return self._redis.get_work_trees_for_session(session_id or "")
        elif self._store_type == "sqlite":
            # SQLite implementation
            logger.info(f"SQLite: Getting work trees for session {session_id}")

            if not self._sqlite._conn:
                return []

            try:
                cursor = self._sqlite._conn.cursor()

                # If session_id is empty string, return all work trees
                if session_id == "":
                    cursor.execute("SELECT * FROM work_trees")
                else:
                    cursor.execute(
                        "SELECT * FROM work_trees WHERE assigned_session_id = ?", (session_id,)
                    )

                rows = cursor.fetchall()
                work_trees = []

                # Convert rows to WorkTree objects
                for row in rows:
                    import json

                    work_tree = WorkTree(
                        work_tree_path=row[0],
                        assigned_session_id=row[1],
                        files_modified=json.loads(row[2]) if isinstance(row[2], str) else [],
                        branch_name=row[3],
                        base_commit=row[4],
                        status=WorkTreeStatus(row[5]),
                        created_at=datetime.fromisoformat(row[6]),
                    )
                    work_trees.append(work_tree)

                return work_trees
            except Exception as e:
                logger.error(f"Failed to get work trees from SQLite: {e}")
                return []
        return []

    def publish_status_change(self, session_id: str, status_data: dict[str, Any]) -> bool:
        if self._store_type == "redis":
            return self._redis.publish_status_change(session_id, status_data)
        elif self._store_type == "sqlite":
            # Basic SQLite implementation (stub)
            logger.info(f"SQLite: Publishing status change for {session_id}: {status_data}")
            return True
        return False

    def subscribe_to_all_agents(self) -> "RedisPubSub | None":
        """Subscribe to all agent status changes.

        Returns:
            PubSub object or None if not connected.
        """
        if not self.is_connected():
            return None

        assert self._client is not None, (
            "Redis client should be non-null after is_connected() check"
        )

        try:
            pubsub = self._client.pubsub()
            pubsub.psubscribe("agent:status:*")
            if TYPE_CHECKING:
                return cast(RedisPubSub, pubsub)
            return pubsub
        except Exception as e:
            logger.error(f"Failed to subscribe to all agents: {e}")
            return None


# =============================================================================
# Global Singleton
# =============================================================================

_state_store: StateStore | None = None


def get_state_store() -> StateStore:
    """Get or create the global StateStore singleton.

    Returns:
        Connected StateStore instance.
    """
    global _state_store
    if _state_store is None:
        _state_store = StateStore()
        _state_store.connect()
    return _state_store
