"""Shared State Adapter for FORGE Harness.

Provides a unified interface for reading/writing tasks, agents, and nodes state.
Backed by:
- .forge/tasks/ JSON files for tasks
- .forge/heartbeat/nodes/ JSON files for nodes
- In-memory AgentRegistry for agents (with optional persistence)
"""

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Path Resolution
# =============================================================================

def _get_forge_root() -> Path:
    """Get the FORGE project root directory.

    Resolves to 3 levels up from this file (forge_harness/ -> harness/ -> FORGE/).
    Can be overridden by FORGE_ROOT environment variable.
    """
    if forge_root := os.environ.get("FORGE_ROOT"):
        return Path(forge_root)
    # From harness/forge_harness/state_adapter.py, go up 3 levels to FORGE/
    return Path(__file__).resolve().parents[3]


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class TaskState:
    """Unified task state representation.

    Fields are normalized from both TaskQueue and task handler formats.
    """
    id: str
    subject: str
    description: str = ""
    priority: str = "medium"  # critical, high, medium, low
    status: str = "pending"  # pending, assigned, in_progress, completed, failed, cancelled, blocked
    domain: str | None = None
    project: str | None = None
    required_role: str | None = None
    required_skills: list[str] = field(default_factory=list)
    assigned_agent: str | None = None
    assigned_at: str | None = None
    order: int = 0
    lease: dict | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None
    result: dict | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskState":
        """Create from dictionary, handling field name variations."""
        # Handle title vs subject naming
        subject = data.get("subject") or data.get("title", "")

        # Handle assigned_agent vs claimed_by
        assigned_agent = data.get("assigned_agent") or data.get("claimed_by")

        return cls(
            id=data.get("id", ""),
            subject=subject,
            description=data.get("description", ""),
            priority=data.get("priority", "medium"),
            status=data.get("status", "pending"),
            domain=data.get("domain"),
            project=data.get("project"),
            required_role=data.get("required_role"),
            required_skills=data.get("required_skills", []),
            assigned_agent=assigned_agent,
            assigned_at=data.get("assigned_at") or data.get("assigned_at"),
            order=data.get("order", 0),
            lease=data.get("lease"),
            created_at=data.get("created_at", datetime.now(UTC).isoformat()),
            completed_at=data.get("completed_at"),
            result=data.get("result"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "priority": self.priority,
            "status": self.status,
            "domain": self.domain,
            "project": self.project,
            "required_role": self.required_role,
            "required_skills": self.required_skills,
            "assigned_agent": self.assigned_agent,
            "assigned_at": self.assigned_at,
            "order": self.order,
            "lease": self.lease,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "result": self.result,
        }


@dataclass
class AgentState:
    """Unified agent state representation.

    Fields are normalized from AgentSession and API response formats.
    """
    id: str
    role: str
    name: str | None = None
    domain: str | None = None
    project: str = ""
    task: str = ""
    status: str = "active"  # active, idle, waiting, completed, failed, paused
    progress: int = 0
    current_task: str | None = None
    files_modified: list[str] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    tmux_session: str | None = None
    skills: list[str] = field(default_factory=list)
    registered_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_activity: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentState":
        """Create from dictionary, handling field name variations."""
        # Handle name variations
        role = data.get("role") or data.get("agent_role", "")
        name = data.get("name") or data.get("agent_name")
        project = data.get("project") or ""
        task = data.get("task") or data.get("current_task", "")

        return cls(
            id=data.get("id") or data.get("session_id", ""),
            role=role,
            name=name,
            domain=data.get("domain"),
            project=project,
            task=task,
            status=data.get("status", "active"),
            progress=int(data.get("progress_pct", data.get("progress", 0))),
            current_task=data.get("current_task"),
            files_modified=data.get("files_modified", []),
            token_usage=data.get("token_usage", {}),
            parent_id=data.get("parent_id"),
            children=data.get("children", []),
            tmux_session=data.get("tmux_session"),
            skills=data.get("skills") or data.get("focus_tags", []),
            registered_at=data.get("registered_at") or data.get("started_at", datetime.now(UTC).isoformat()),
            last_activity=data.get("last_activity", datetime.now(UTC).isoformat()),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "role": self.role,
            "name": self.name,
            "domain": self.domain,
            "project": self.project,
            "task": self.task,
            "status": self.status,
            "progress": self.progress,
            "current_task": self.current_task,
            "files_modified": self.files_modified,
            "token_usage": self.token_usage,
            "parent_id": self.parent_id,
            "children": self.children,
            "tmux_session": self.tmux_session,
            "skills": self.skills,
            "registered_at": self.registered_at,
            "last_activity": self.last_activity,
        }


@dataclass
class NodeState:
    """Unified node state representation.

    Fields are normalized from heartbeat files and API responses.
    """
    node_id: str
    name: str
    status: str = "offline"  # online, offline, degraded, stale
    agent_count: int = 0
    cpu_load: float = 0.0
    ram_usage: float = 0.0
    disk_usage: float = 0.0
    load_average: float = 0.0
    last_heartbeat: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    latency_ms: int = 0
    capabilities: dict[str, bool] = field(default_factory=dict)
    specs: dict[str, Any] = field(default_factory=dict)
    current_task: str = ""
    alerts: list[dict[str, Any]] = field(default_factory=list)
    # Fleet-awareness metadata
    is_fresh: bool = True
    age_seconds: float = 0.0
    received_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NodeState":
        """Create from dictionary, handling field name variations."""
        return cls(
            node_id=data.get("node_id", ""),
            name=data.get("name", data.get("node_id", "")),
            status=data.get("status", "offline"),
            agent_count=data.get("agent_count", 0),
            cpu_load=float(data.get("cpu_load", data.get("cpu_percent", 0))),
            ram_usage=float(data.get("ram_usage", data.get("memory_percent", 0))),
            disk_usage=float(data.get("disk_usage", 0)),
            load_average=float(data.get("load_average", data.get("cpu_load", 0))),
            last_heartbeat=data.get("last_heartbeat") or data.get("timestamp", datetime.now(UTC).isoformat()),
            latency_ms=int(data.get("latency_ms", 0)),
            capabilities=data.get("capabilities", {}),
            specs=data.get("specs", {}),
            current_task=data.get("current_task", ""),
            alerts=data.get("alerts", []),
            is_fresh=data.get("is_fresh", True),
            age_seconds=float(data.get("age_seconds", 0)),
            received_at=data.get("received_at", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "node_id": self.node_id,
            "name": self.name,
            "status": self.status,
            "agent_count": self.agent_count,
            "cpu_load": self.cpu_load,
            "ram_usage": self.ram_usage,
            "disk_usage": self.disk_usage,
            "load_average": self.load_average,
            "last_heartbeat": self.last_heartbeat,
            "latency_ms": self.latency_ms,
            "capabilities": self.capabilities,
            "specs": self.specs,
            "current_task": self.current_task,
            "alerts": self.alerts,
            "is_fresh": self.is_fresh,
            "age_seconds": self.age_seconds,
            "received_at": self.received_at,
        }


# =============================================================================
# State Adapter
# =============================================================================

class StateAdapter:
    """Unified state adapter for tasks, agents, and nodes.

    Provides thread-safe access to all FORGE harness state with a consistent API.
    Handles serialization/deserialization and path resolution automatically.

    Example:
        adapter = StateAdapter()
        tasks = await adapter.get_tasks()
        agents = await adapter.get_agents()
        nodes = await adapter.get_nodes()

        await adapter.update_task(task_id, {"status": "completed"})
        await adapter.update_agent(agent_id, {"progress": 100})
    """

    def __init__(self, forge_root: Path | None = None):
        """Initialize the state adapter.

        Args:
            forge_root: FORGE project root. If None, auto-detected.
        """
        self._forge_root = Path(forge_root) if forge_root else _get_forge_root()
        self._tasks_dir = self._forge_root / ".forge/tasks"
        self._nodes_dir = self._forge_root / ".forge" / "heartbeat" / "nodes"
        self._agents_dir = self._forge_root / ".forge" / "agents"

        # Ensure directories exist
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        self._nodes_dir.mkdir(parents=True, exist_ok=True)
        self._agents_dir.mkdir(parents=True, exist_ok=True)

        # Thread lock for write operations
        self._lock = Lock()

        logger.info(
            f"StateAdapter initialized: tasks={self._tasks_dir}, "
            f"nodes={self._nodes_dir}, agents={self._agents_dir}"
        )

    # =========================================================================
    # Tasks
    # =========================================================================

    def _task_path(self, task_id: str) -> Path:
        """Get the file path for a task."""
        return self._tasks_dir / f"{task_id}.json"

    async def get_tasks(
        self,
        status: str | None = None,
        priority: str | None = None,
        domain: str | None = None,
        limit: int | None = None,
    ) -> list[TaskState]:
        """Get all tasks with optional filtering.

        Args:
            status: Filter by status (e.g., "pending", "in_progress")
            priority: Filter by priority (e.g., "high", "critical")
            domain: Filter by domain
            limit: Maximum number of tasks to return

        Returns:
            List of TaskState objects
        """
        tasks = []

        for path in self._tasks_dir.glob("*.json"):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                task = TaskState.from_dict(data)

                # Apply filters
                if status and task.status != status:
                    continue
                if priority and task.priority != priority:
                    continue
                if domain and task.domain != domain:
                    continue

                tasks.append(task)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to load task from {path}: {e}")

        # Sort: by order, then priority (critical first), then created_at
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        tasks.sort(
            key=lambda t: (
                t.order,
                priority_order.get(t.priority, 99),
                t.created_at,
            )
        )

        if limit:
            tasks = tasks[:limit]

        return tasks

    async def get_task(self, task_id: str) -> TaskState | None:
        """Get a specific task by ID.

        Args:
            task_id: Task identifier

        Returns:
            TaskState if found, None otherwise
        """
        path = self._task_path(task_id)
        if not path.exists():
            return None

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return TaskState.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to load task {task_id}: {e}")
            return None

    async def update_task(self, task_id: str, data: dict[str, Any]) -> TaskState | None:
        """Update a task with the provided data.

        Args:
            task_id: Task identifier
            data: Fields to update (partial update)

        Returns:
            Updated TaskState if found, None otherwise
        """
        with self._lock:
            # Load existing task
            path = self._task_path(task_id)
            if not path.exists():
                return None

            try:
                with open(path, encoding="utf-8") as f:
                    existing_data = json.load(f)

                # Update with new data
                existing_data.update(data)

                # Write back
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(existing_data, f, indent=2)

                logger.debug(f"Updated task {task_id}: {list(data.keys())}")
                return TaskState.from_dict(existing_data)
            except (OSError, json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to update task {task_id}: {e}")
                return None

    async def create_task(self, data: dict[str, Any]) -> TaskState:
        """Create a new task.

        Args:
            data: Task data (must include 'subject')

        Returns:
            Created TaskState
        """
        import uuid

        task_id = data.get("id") or str(uuid.uuid4())[:8]

        # Prepare full task data
        task_data = {
            "id": task_id,
            "title": data.get("subject", data.get("title", "")),
            "description": data.get("description", ""),
            "priority": data.get("priority", "medium"),
            "status": data.get("status", "pending"),
            "domain": data.get("domain"),
            "project": data.get("project"),
            "required_role": data.get("required_role"),
            "required_skills": data.get("required_skills", []),
            "assigned_agent": data.get("assigned_agent"),
            "order": data.get("order", 0),
            "lease": data.get("lease"),
            "created_at": data.get("created_at", datetime.now(UTC).isoformat()),
            "completed_at": data.get("completed_at"),
            "result": data.get("result"),
        }

        with self._lock:
            path = self._task_path(task_id)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(task_data, f, indent=2)

            logger.info(f"Created task {task_id}: {task_data.get('title', '')}")
            return TaskState.from_dict(task_data)

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task.

        Args:
            task_id: Task identifier

        Returns:
            True if deleted, False if not found
        """
        with self._lock:
            path = self._task_path(task_id)
            if path.exists():
                path.unlink()
                logger.info(f"Deleted task {task_id}")
                return True
            return False

    # =========================================================================
    # Agents
    # =========================================================================

    def _agent_path(self, agent_id: str) -> Path:
        """Get the file path for an agent."""
        return self._agents_dir / f"{agent_id}.json"

    async def get_agents(
        self,
        status: str | None = None,
        domain: str | None = None,
        role: str | None = None,
    ) -> list[AgentState]:
        """Get all agents with optional filtering.

        Args:
            status: Filter by status (e.g., "active", "idle")
            domain: Filter by domain
            role: Filter by role

        Returns:
            List of AgentState objects from persisted files.
            Note: This only returns persisted agents, not in-memory registry agents.
        """
        agents = []

        for path in self._agents_dir.glob("*.json"):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                agent = AgentState.from_dict(data)

                # Apply filters
                if status and agent.status != status:
                    continue
                if domain and agent.domain != domain:
                    continue
                if role and agent.role != role:
                    continue

                agents.append(agent)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to load agent from {path}: {e}")

        # Sort by last_activity (most recent first)
        agents.sort(key=lambda a: a.last_activity, reverse=True)
        return agents

    async def get_agent(self, agent_id: str) -> AgentState | None:
        """Get a specific agent by ID.

        Args:
            agent_id: Agent identifier

        Returns:
            AgentState if found, None otherwise
        """
        path = self._agent_path(agent_id)
        if not path.exists():
            return None

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return AgentState.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to load agent {agent_id}: {e}")
            return None

    async def update_agent(self, agent_id: str, data: dict[str, Any]) -> AgentState | None:
        """Update an agent with the provided data.

        Args:
            agent_id: Agent identifier
            data: Fields to update (partial update)

        Returns:
            Updated AgentState if found, None otherwise
        """
        with self._lock:
            # Load existing agent
            path = self._agent_path(agent_id)

            # If agent doesn't exist, create it with the provided data
            if not path.exists():
                if "id" not in data:
                    data = {**data, "id": agent_id}
                full_data = {
                    "id": agent_id,
                    "role": data.get("role", "developer"),
                    "name": data.get("name"),
                    "domain": data.get("domain"),
                    "project": data.get("project", ""),
                    "task": data.get("task", ""),
                    "status": data.get("status", "active"),
                    "progress": data.get("progress", 0),
                    "current_task": data.get("current_task"),
                    "files_modified": data.get("files_modified", []),
                    "token_usage": data.get("token_usage", {}),
                    "parent_id": data.get("parent_id"),
                    "children": data.get("children", []),
                    "tmux_session": data.get("tmux_session"),
                    "skills": data.get("skills", []),
                    "registered_at": data.get("registered_at", datetime.now(UTC).isoformat()),
                    "last_activity": data.get("last_activity", datetime.now(UTC).isoformat()),
                }
            else:
                try:
                    with open(path, encoding="utf-8") as f:
                        existing_data = json.load(f)
                    # Update with new data
                    existing_data.update(data)
                    full_data = existing_data
                except (OSError, json.JSONDecodeError) as e:
                    logger.error(f"Failed to load agent {agent_id}: {e}")
                    return None

            # Write back
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(full_data, f, indent=2, default=str)

                logger.debug(f"Updated agent {agent_id}: {list(data.keys())}")
                return AgentState.from_dict(full_data)
            except OSError as e:
                logger.error(f"Failed to write agent {agent_id}: {e}")
                return None

    async def persist_agent(self, agent_data: dict[str, Any]) -> AgentState:
        """Persist an agent from the in-memory registry to disk.

        Args:
            agent_data: Agent data dictionary (from AgentSession.to_dict())

        Returns:
            Persisted AgentState
        """
        agent_id = agent_data.get("id") or agent_data.get("session_id", "")
        if not agent_id:
            raise ValueError("Agent data must contain 'id' or 'session_id'")

        return await self.update_agent(agent_id, agent_data) or await self.update_agent(
            agent_id,
            {
                "id": agent_id,
                **agent_data,
            },
        )

    # =========================================================================
    # Nodes
    # =========================================================================

    def _node_path(self, node_id: str) -> Path:
        """Get the file path for a node heartbeat."""
        return self._nodes_dir / f"{node_id}.json"

    async def get_nodes(
        self,
        status: str | None = None,
        fresh_only: bool = False,
    ) -> list[NodeState]:
        """Get all nodes with optional filtering.

        Args:
            status: Filter by status (e.g., "online", "offline")
            fresh_only: Only return nodes with recent heartbeats (< 120s old)

        Returns:
            List of NodeState objects
        """
        nodes = []

        for path in self._nodes_dir.glob("*.json"):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                node = NodeState.from_dict(data)

                # Apply filters
                if status and node.status != status:
                    continue
                if fresh_only and not node.is_fresh:
                    continue

                nodes.append(node)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to load node from {path}: {e}")

        # Sort by name
        nodes.sort(key=lambda n: n.node_id)
        return nodes

    async def get_node(self, node_id: str) -> NodeState | None:
        """Get a specific node by ID.

        Args:
            node_id: Node identifier

        Returns:
            NodeState if found, None otherwise
        """
        path = self._node_path(node_id)
        if not path.exists():
            return None

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return NodeState.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to load node {node_id}: {e}")
            return None

    async def update_node(self, node_id: str, data: dict[str, Any]) -> NodeState | None:
        """Update a node with the provided data.

        Args:
            node_id: Node identifier
            data: Fields to update (partial update)

        Returns:
            Updated NodeState if found, None otherwise
        """
        with self._lock:
            # Load existing node
            path = self._node_path(node_id)
            if not path.exists():
                return None

            try:
                with open(path, encoding="utf-8") as f:
                    existing_data = json.load(f)

                # Update with new data
                existing_data.update(data)

                # Write back
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(existing_data, f, indent=2, default=str)

                logger.debug(f"Updated node {node_id}: {list(data.keys())}")
                return NodeState.from_dict(existing_data)
            except (OSError, json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to update node {node_id}: {e}")
                return None

    # =========================================================================
    # Sync from Registry (Bridge to in-memory state)
    # =========================================================================

    async def sync_from_registry(
        self, agent_registry=None, stale_threshold_seconds: int = 120
    ) -> dict[str, Any]:
        """Sync agent state from in-memory AgentRegistry to disk.

        Args:
            agent_registry: AgentRegistry instance (if None, tries to import)
            stale_threshold_seconds: Age threshold for considering a node fresh

        Returns:
            Sync statistics dict
        """
        if agent_registry is None:
            try:
                from forge_harness.webhook_server.services.agent_registry import (
                    get_agent_registry,
                )
                agent_registry = get_agent_registry()
            except ImportError:
                logger.warning("Could not import agent_registry for sync")
                return {"synced": 0, "error": "agent_registry_not_available"}

        synced = 0
        for agent in agent_registry.list_active():
            await self.persist_agent(agent.to_dict())
            synced += 1

        logger.info(f"Synced {synced} agents from registry to disk")
        return {"synced": synced}


# =============================================================================
# Singleton accessor
# =============================================================================

_state_adapter_instance: StateAdapter | None = None
_state_adapter_lock = Lock()


def get_state_adapter(forge_root: Path | None = None) -> StateAdapter:
    """Get the singleton StateAdapter instance.

    Args:
        forge_root: FORGE project root (only used on first call)

    Returns:
        StateAdapter singleton instance
    """
    global _state_adapter_instance

    if _state_adapter_instance is None:
        with _state_adapter_lock:
            if _state_adapter_instance is None:
                _state_adapter_instance = StateAdapter(forge_root)

    return _state_adapter_instance
