"""Task Queue System for FORGE Agents.

Priority-based task queue that agents pull from automatically.
Supports file-based and Redis backends.

IMPORTANT: This module uses the canonical DF lifecycle from
webhook_server.models.task_lifecycle.TaskLifecycleState.
All status operations should validate transitions via validate_transition().
"""

import asyncio
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

import aiofiles
import aiofiles.os

# Import canonical DF lifecycle states (from shared models to avoid circular imports)
from forge_harness.models.task_lifecycle import (
    TaskLifecycleState,
)


class TaskPriority(str, Enum):
    """Task priority levels."""

    CRITICAL = "critical"  # Production issues, security
    HIGH = "high"  # Blocking features, bugs
    MEDIUM = "medium"  # Regular features
    LOW = "low"  # Nice-to-haves, refactoring


# Alias canonical lifecycle state as TaskStatus for backward compatibility
# All code should use TaskLifecycleState directly when possible
TaskStatus = TaskLifecycleState


@dataclass
class Task:
    """A task in the queue."""

    id: str
    title: str
    description: str
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING

    # Targeting
    domain: str | None = None
    project: str | None = None
    required_role: str | None = None  # e.g., "backend-engineer"
    required_skills: list[str] = field(default_factory=list)

    # Assignment
    assigned_agent: str | None = None
    assigned_at: str | None = None

    # Context
    context: dict = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)  # Task IDs

    # Ordering
    order: int = 0  # For custom ordering in UI

    # Lease (ownership metadata for multi-node task coordination)
    lease: dict | None = None

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    created_by: str | None = None
    completed_at: str | None = None
    result: dict | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """Create from dictionary.

        Handles legacy field name migrations:
        - assigned_to -> assigned_agent
        - created -> created_at
        """
        data = data.copy()

        # Handle enum conversion
        if "priority" in data and isinstance(data["priority"], str):
            data["priority"] = TaskPriority(data["priority"])
        if "status" in data and isinstance(data["status"], str):
            data["status"] = TaskStatus(data["status"])

        # Legacy field name migrations
        if "assigned_to" in data:
            data["assigned_agent"] = data.pop("assigned_to")
        if "created" in data and "created_at" not in data:
            data["created_at"] = data.pop("created")

        # Ensure required fields have defaults
        if "title" not in data:
            data["title"] = data.get("id", "unknown")
        if "description" not in data:
            data["description"] = ""

        # Remove unknown fields that aren't in the Task dataclass
        known_fields = {
            "id",
            "title",
            "description",
            "priority",
            "status",
            "domain",
            "project",
            "required_role",
            "required_skills",
            "assigned_agent",
            "assigned_at",
            "context",
            "files",
            "depends_on",
            "order",
            "lease",
            "created_at",
            "created_by",
            "completed_at",
            "result",
        }
        unknown_fields = set(data.keys()) - known_fields
        for key in unknown_fields:
            data.pop(key)

        return cls(**data)


class TaskQueue:
    """File-based task queue with priority ordering."""

    def __init__(self, queue_dir: Path):
        self.queue_dir = Path(queue_dir)
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _task_path(self, task_id: str) -> Path:
        """Get path for a task file."""
        return self.queue_dir / f"{task_id}.json"

    async def add(self, task: Task) -> Task:
        """Add a task to the queue."""
        if not task.id:
            from forge_harness.models.task_id import generate_task_id

            task.id = generate_task_id(getattr(task, "domain", None))

        async with self._lock:
            path = self._task_path(task.id)
            async with aiofiles.open(path, "w") as f:
                await f.write(json.dumps(task.to_dict(), indent=2))

        return task

    async def get(self, task_id: str) -> Task | None:
        """Get a task by ID."""
        path = self._task_path(task_id)
        if not path.exists():
            return None

        async with aiofiles.open(path) as f:
            data = json.loads(await f.read())
        return Task.from_dict(data)

    async def update(self, task: Task) -> Task:
        """Update a task."""
        async with self._lock:
            path = self._task_path(task.id)
            async with aiofiles.open(path, "w") as f:
                await f.write(json.dumps(task.to_dict(), indent=2))
        return task

    async def delete(self, task_id: str) -> bool:
        """Delete a task."""
        path = self._task_path(task_id)
        if path.exists():
            await aiofiles.os.remove(path)
            return True
        return False

    async def list_all(
        self,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        domain: str | None = None,
        role: str | None = None,
    ) -> list[Task]:
        """List tasks with optional filtering."""
        tasks = []

        for path in self.queue_dir.glob("*.json"):
            async with aiofiles.open(path) as f:
                data = json.loads(await f.read())
            task = Task.from_dict(data)

            # Apply filters
            if status and task.status != status:
                continue
            if priority and task.priority != priority:
                continue
            if domain and task.domain != domain:
                continue
            if role and task.required_role != role:
                continue

            tasks.append(task)

        # Sort by order first, then by priority (critical first), then by created_at
        priority_order = {
            TaskPriority.CRITICAL: 0,
            TaskPriority.HIGH: 1,
            TaskPriority.MEDIUM: 2,
            TaskPriority.LOW: 3,
        }
        tasks.sort(key=lambda t: (t.order, priority_order.get(t.priority, 99), t.created_at))

        return tasks

    async def claim_next(
        self,
        agent_id: str,
        agent_role: str,
        agent_skills: list[str] = None,
        domain: str | None = None,
    ) -> Task | None:
        """Claim the next available task for an agent.

        Returns the highest priority task that:
        - Is in PENDING status
        - Matches agent's role (if required_role is set)
        - Matches agent's domain (if domain is set on task)
        - Has all required_skills in agent's skills
        - Has all dependencies completed
        """
        agent_skills = agent_skills or []

        async with self._lock:
            tasks = await self.list_all(status=TaskStatus.PENDING)

            for task in tasks:
                # Check role match
                if task.required_role and task.required_role != agent_role:
                    continue

                # Check domain match
                if task.domain and domain and task.domain != domain:
                    continue

                # Check skills match
                if task.required_skills:
                    if not all(s in agent_skills for s in task.required_skills):
                        continue

                # Check dependencies
                if task.depends_on:
                    deps_met = True
                    for dep_id in task.depends_on:
                        dep_task = await self.get(dep_id)
                        if dep_task and dep_task.status != TaskStatus.COMPLETED:
                            deps_met = False
                            break
                    if not deps_met:
                        continue

                # Claim this task
                task.status = TaskStatus.ASSIGNED
                task.assigned_agent = agent_id
                task.assigned_at = datetime.now(UTC).isoformat()
                await self.update(task)
                return task

        return None

    async def release(self, task_id: str) -> Task | None:
        """Release a task back to pending (e.g., agent crashed)."""
        task = await self.get(task_id)
        if task:
            task.status = TaskStatus.PENDING
            task.assigned_agent = None
            task.assigned_at = None
            await self.update(task)
        return task

    async def complete(
        self,
        task_id: str,
        result: dict | None = None,
        success: bool = True,
    ) -> Task | None:
        """Mark a task as completed or failed."""
        task = await self.get(task_id)
        if task:
            task.status = TaskStatus.COMPLETED if success else TaskStatus.FAILED
            task.completed_at = datetime.now(UTC).isoformat()
            task.result = result
            await self.update(task)
        return task

    async def get_stats(self) -> dict:
        """Get queue statistics."""
        tasks = await self.list_all()

        stats = {
            "total": len(tasks),
            "by_status": {},
            "by_priority": {},
            "by_domain": {},
        }

        for task in tasks:
            # By status
            status = task.status.value
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            # By priority
            priority = task.priority.value
            stats["by_priority"][priority] = stats["by_priority"].get(priority, 0) + 1

            # By domain
            domain = task.domain or "unassigned"
            stats["by_domain"][domain] = stats["by_domain"].get(domain, 0) + 1

        return stats


def create_task_queue(forge_root: Path | None = None) -> TaskQueue:
    """Create a task queue instance."""
    if forge_root is None:
        forge_root = Path(os.environ.get("FORGE_ROOT", "."))

    queue_dir = forge_root / ".forge/tasks"
    return TaskQueue(queue_dir)
