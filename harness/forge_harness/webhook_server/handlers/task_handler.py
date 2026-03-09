"""Task Handler

Business logic for task queue operations.
Delegates to TaskQueue (file-based) or Redis depending on availability.

IMPORTANT: This handler uses the canonical DF lifecycle from
TaskLifecycleState. All status transitions are validated via validate_transition().
"""

import json
from datetime import UTC, datetime
from typing import Any

from forge_harness.logging_config import get_logger
from forge_harness.models.task_id import generate_task_id
from forge_harness.models.task_lifecycle import (
    TaskLifecycleState,
    TransitionError,
    validate_transition,
)
from forge_harness.task_queue import Task, TaskPriority, TaskQueue, TaskStatus, create_task_queue

logger = get_logger(__name__)

# Map handler priority strings to TaskPriority enums
_PRIORITY_MAP = {
    "critical": TaskPriority.CRITICAL,
    "high": TaskPriority.HIGH,
    "medium": TaskPriority.MEDIUM,
    "low": TaskPriority.LOW,
}

# Map handler status strings to TaskLifecycleState (canonical DF states)
# This includes all states from the canonical lifecycle
_STATUS_MAP: dict[str, TaskLifecycleState] = {
    # Submission / scheduling
    "pending": TaskLifecycleState.PENDING,
    "queued": TaskLifecycleState.QUEUED,
    # Agent execution
    "assigned": TaskLifecycleState.ASSIGNED,
    "in_progress": TaskLifecycleState.IN_PROGRESS,
    # Evaluation pipeline
    "candidate_done": TaskLifecycleState.CANDIDATE_DONE,
    "evaluator_running": TaskLifecycleState.EVALUATOR_RUNNING,
    "evaluator_passed": TaskLifecycleState.EVALUATOR_PASSED,
    "evaluator_failed": TaskLifecycleState.EVALUATOR_FAILED,
    # Evaluator escalation
    "blocked": TaskLifecycleState.BLOCKED,
    # Terminal / recovery
    "completed": TaskLifecycleState.COMPLETED,
    "failed": TaskLifecycleState.FAILED,
    "cancelled": TaskLifecycleState.CANCELLED,
    "expired": TaskLifecycleState.EXPIRED,
}


def _validate_status_transition(current_status: TaskLifecycleState | str, new_status: TaskLifecycleState | str) -> None:
    """Validate a status transition against the canonical DF lifecycle.

    Args:
        current_status: Current task status
        new_status: Requested new status

    Raises:
        TransitionError: If the transition is not valid per the lifecycle contract
    """
    try:
        validate_transition(current_status, new_status)
    except TransitionError as e:
        logger.warning(f"Invalid status transition: {e}")
        raise


def _task_to_dict(task: Task) -> dict[str, Any]:
    """Convert a TaskQueue Task to the handler's API dict format."""
    return {
        "id": task.id,
        "subject": task.title,
        "description": task.description,
        "priority": task.priority.value,
        "status": task.status.value,
        "required_role": task.required_role,
        "claimed_by": task.assigned_agent or "",
        "order": task.order,
        "domain": task.domain,
        "project": task.project,
        "depends_on": task.depends_on or [],
        "lease": task.lease,
        "created_at": task.created_at,
        "updated_at": task.created_at,  # TaskQueue doesn't track updated_at separately
    }


def _dict_to_task(data: dict[str, Any], existing: Task | None = None, skip_validation: bool = False) -> Task:
    """Convert handler API dict to a TaskQueue Task.

    Args:
        data: Dictionary with task fields
        existing: Existing task to update (optional)
        skip_validation: If True, skip transition validation (for internal/system operations)
    """
    priority = _PRIORITY_MAP.get(data.get("priority", "medium"), TaskPriority.MEDIUM)
    status = _STATUS_MAP.get(data.get("status", "pending"), TaskStatus.PENDING)

    if existing:
        # Update existing task with provided fields
        if "subject" in data:
            existing.title = data["subject"]
        if "description" in data:
            existing.description = data["description"]
        if "priority" in data:
            existing.priority = priority
        if "status" in data:
            # Validate transition against canonical DF lifecycle (unless skipped)
            if not skip_validation:
                _validate_status_transition(existing.status, status)
            existing.status = status
        if "required_role" in data:
            existing.required_role = data["required_role"] or None
        if "claimed_by" in data:
            existing.assigned_agent = data["claimed_by"] or None
            if existing.assigned_agent:
                existing.assigned_at = datetime.now(UTC).isoformat()
        if "order" in data:
            existing.order = data["order"]
        if "domain" in data:
            existing.domain = data["domain"]
        if "project" in data:
            existing.project = data["project"]
        if "depends_on" in data:
            existing.depends_on = data["depends_on"] or []
        if "lease" in data:
            existing.lease = data["lease"]
        return existing

    return Task(
        id=data.get("id") or generate_task_id(data.get("domain")),
        title=data.get("subject", ""),
        description=data.get("description", ""),
        priority=priority,
        status=status,
        required_role=data.get("required_role") or None,
        assigned_agent=data.get("claimed_by") or None,
        order=data.get("order", 0),
        domain=data.get("domain"),
        project=data.get("project"),
        depends_on=data.get("depends_on") or [],
        lease=data.get("lease"),
        created_at=data.get("created_at", datetime.now(UTC).isoformat()),
    )


class TaskLeaseError(Exception):
    """Domain-level lease lifecycle error with API-friendly metadata."""

    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class TaskQueueHandler:
    """Handles task queue operations for the webhook server.

    Uses TaskQueue (file-based, .forge/tasks/) as primary storage.
    Falls back to Redis when available for better performance.
    """

    def __init__(self, state_store: Any = None, task_queue: TaskQueue | None = None):
        """Initialize task queue handler.

        Args:
            state_store: StateStore instance for Redis access
            task_queue: Optional TaskQueue instance (for testing)
        """
        self.state_store = state_store
        self._ensure_state_store()
        self._task_queue = task_queue or create_task_queue()

    def _ensure_state_store(self):
        """Ensure StateStore is available."""
        if self.state_store is None:
            try:
                from forge_harness.state_store import StateStore

                self.state_store = StateStore()
                self.state_store.connect()
            except Exception as e:
                logger.warning(f"StateStore unavailable: {e}")
                self.state_store = None

    def _get_redis_client(self):
        """Get Redis client from StateStore."""
        if not self.state_store or not self.state_store.is_connected():
            return None
        if self.state_store.get_store_type() != "redis":
            return None
        # Access internal Redis client
        if hasattr(self.state_store, "_redis") and hasattr(self.state_store._redis, "_client"):
            return self.state_store._redis._client
        return None

    def _task_key(self, task_id: str) -> str:
        """Generate Redis key for a task."""
        return f"forge:tasks:{task_id}"

    def _tasks_index_key(self) -> str:
        """Generate Redis key for tasks index."""
        return "forge:tasks:index"

    @staticmethod
    def _parse_lease_expiry(lease: dict[str, Any]) -> datetime | None:
        """Parse lease expiration timestamp to UTC datetime."""
        raw_value = lease.get("lease_expires_at")
        if raw_value is None:
            return None

        if isinstance(raw_value, datetime):
            parsed = raw_value
        elif isinstance(raw_value, str):
            try:
                parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _is_lease_active(self, lease: dict[str, Any] | None, *, now: datetime | None = None) -> bool:
        """Check whether a lease is currently active."""
        if not lease:
            return False

        expires_at = self._parse_lease_expiry(lease)
        if expires_at is None:
            return False

        return expires_at > (now or datetime.now(UTC))

    async def _find_path_lock_conflict(
        self,
        path_lock: str,
        *,
        exclude_task_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Find another active task lease holding the same path lock."""
        if not path_lock:
            return None

        tasks = await self.list_tasks(limit=10000)
        for task in tasks:
            task_id = task.get("id")
            if exclude_task_id and task_id == exclude_task_id:
                continue

            lease = task.get("lease")
            if not isinstance(lease, dict):
                continue

            if lease.get("path_lock") != path_lock:
                continue

            if self._is_lease_active(lease, now=now):
                return task

        return None

    async def list_tasks(
        self,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List tasks with optional filtering.

        Args:
            status: Filter by status (pending, in_progress, completed, assigned, etc.)
            priority: Filter by priority (critical, high, medium, low)
            limit: Maximum results

        Returns:
            List of tasks
        """
        client = self._get_redis_client()
        if not client:
            # Use TaskQueue file-based storage
            status_enum = _STATUS_MAP.get(status) if status else None
            priority_enum = _PRIORITY_MAP.get(priority) if priority else None
            tasks = await self._task_queue.list_all(
                status=status_enum,
                priority=priority_enum,
            )
            result = [_task_to_dict(t) for t in tasks]
            return result[:limit]

        try:
            # Get all task IDs from index
            task_ids = client.smembers(self._tasks_index_key())
            tasks = []

            for task_id in task_ids:
                task_data = client.hgetall(self._task_key(task_id))
                if not task_data:
                    continue

                task = self._deserialize_task(task_data)

                # Apply filters
                if status and task["status"] != status:
                    continue
                if priority and task["priority"] != priority:
                    continue

                tasks.append(task)

            # Sort by created_at descending
            tasks.sort(key=lambda t: t["created_at"], reverse=True)

            # Apply limit
            return tasks[:limit]
        except Exception as e:
            logger.error(f"Failed to list tasks from Redis: {e}")
            return []

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get a specific task.

        Args:
            task_id: Task ID

        Returns:
            Task data or None
        """
        client = self._get_redis_client()
        if not client:
            task = await self._task_queue.get(task_id)
            return _task_to_dict(task) if task else None

        try:
            task_data = client.hgetall(self._task_key(task_id))
            if not task_data:
                return None
            return self._deserialize_task(task_data)
        except Exception as e:
            logger.error(f"Failed to get task {task_id}: {e}")
            return None

    async def create_task(
        self,
        subject: str,
        description: str,
        priority: str = "medium",
        required_role: str | None = None,
        lease: dict[str, Any] | None = None,
        domain: str | None = None,
        project: str | None = None,
        depends_on: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new task.

        Args:
            subject: Task subject
            description: Task description
            priority: Priority (critical, high, medium, low)
            required_role: Required agent role
            domain: Domain for JIRA-style ID prefix (e.g., "interview-simulator" → IS)
            project: Project name
            depends_on: List of task IDs this task depends on

        Returns:
            Created task
        """
        task_id = generate_task_id(domain)
        now = datetime.now(UTC).isoformat()

        client = self._get_redis_client()
        if not client:
            task = Task(
                id=task_id,
                title=subject,
                description=description,
                priority=_PRIORITY_MAP.get(priority, TaskPriority.MEDIUM),
                status=TaskStatus.PENDING,
                required_role=required_role or None,
                domain=domain,
                project=project,
                depends_on=depends_on or [],
                lease=lease,
                created_at=now,
            )
            task = await self._task_queue.add(task)
            return _task_to_dict(task)

        task = {
            "id": task_id,
            "subject": subject,
            "description": description,
            "priority": priority,
            "status": "pending",
            "required_role": required_role or "",
            "claimed_by": "",
            "depends_on": depends_on or [],
            "lease": lease,
            "order": 0,
            "created_at": now,
            "updated_at": now,
        }

        try:
            # Store task
            client.hset(self._task_key(task_id), mapping=self._serialize_task(task))
            # Add to index
            client.sadd(self._tasks_index_key(), task_id)
            # Set TTL (30 days)
            client.expire(self._task_key(task_id), 2592000)

            # Return deserialized task (converts empty strings to None)
            task_data = client.hgetall(self._task_key(task_id))
            return self._deserialize_task(task_data)
        except Exception as e:
            logger.error(f"Failed to create task: {e}")
            raise

    async def update_task(
        self,
        task_id: str,
        updates: dict[str, Any],
        skip_validation: bool = False,
    ) -> dict[str, Any] | None:
        """Update a task.

        Args:
            task_id: Task ID
            updates: Fields to update
            skip_validation: If True, skip transition validation (for internal/system operations)

        Returns:
            Updated task or None
        """
        client = self._get_redis_client()
        if not client:
            existing = await self._task_queue.get(task_id)
            if not existing:
                return None
            _dict_to_task(updates, existing=existing, skip_validation=skip_validation)
            await self._task_queue.update(existing)
            return _task_to_dict(existing)

        try:
            # Get existing task
            existing = await self.get_task(task_id)
            if not existing:
                return None

            # Apply updates (with transition validation unless skipped)
            if "status" in updates and not skip_validation:
                # Validate transition against canonical DF lifecycle
                current_status = existing.get("status", "pending")
                new_status = updates["status"]
                _validate_status_transition(current_status, new_status)

            for key, value in updates.items():
                if key not in ["id", "created_at"] and (key in existing or key in ("lease", "depends_on")):
                    existing[key] = value

            # Update timestamp
            existing["updated_at"] = datetime.now(UTC).isoformat()

            # Save
            client.hset(self._task_key(task_id), mapping=self._serialize_task(existing))

            return existing
        except Exception as e:
            logger.error(f"Failed to update task {task_id}: {e}")
            return None

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task.

        Args:
            task_id: Task ID

        Returns:
            True if deleted
        """
        client = self._get_redis_client()
        if not client:
            return await self._task_queue.delete(task_id)

        try:
            # Remove from index
            client.srem(self._tasks_index_key(), task_id)
            # Delete task data
            client.delete(self._task_key(task_id))
            return True
        except Exception as e:
            logger.error(f"Failed to delete task {task_id}: {e}")
            return False

    async def claim_task(self, task_id: str, agent_id: str) -> dict[str, Any] | None:
        """Claim a task for an agent.

        Args:
            task_id: Task ID
            agent_id: Agent ID

        Returns:
            Updated task or None
        """
        # Note: Internal operation - bypasses transition validation
        # In canonical lifecycle, this should go: PENDING -> QUEUED -> ASSIGNED
        # but for backward compatibility with existing system behavior, we allow
        # direct transition with skip_validation
        return await self.update_task(
            task_id,
            {
                "claimed_by": agent_id,
                "status": "assigned",
            },
            skip_validation=True,
        )

    async def claim_task_with_lease(
        self,
        task_id: str,
        lease: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Claim task with explicit lease metadata and conflict checks."""
        task = await self.get_task(task_id)
        if not task:
            return None

        owner_node = str(lease.get("owner_node") or "").strip()
        owner_agent = str(lease.get("owner_agent") or "").strip()
        path_lock = str(lease.get("path_lock") or "").strip()
        now = datetime.now(UTC)

        if not owner_node or not owner_agent or not path_lock:
            raise TaskLeaseError(
                "INVALID_LEASE",
                "Lease must include owner_node, owner_agent, and path_lock",
                status_code=400,
            )

        if not self._is_lease_active(lease, now=now):
            raise TaskLeaseError(
                "INVALID_LEASE",
                "lease_expires_at must be a future UTC timestamp",
                status_code=400,
            )

        current_lease = task.get("lease")
        if isinstance(current_lease, dict) and self._is_lease_active(current_lease, now=now):
            same_owner = (
                current_lease.get("owner_node") == owner_node
                and current_lease.get("owner_agent") == owner_agent
            )
            if not same_owner:
                raise TaskLeaseError(
                    "LEASE_ALREADY_OWNED",
                    "Task is already leased by another owner",
                    status_code=409,
                )

        conflict_task = await self._find_path_lock_conflict(
            path_lock, exclude_task_id=task_id, now=now
        )
        if conflict_task:
            raise TaskLeaseError(
                "PATH_LOCK_CONFLICT",
                f"path_lock '{path_lock}' is already held by task {conflict_task.get('id')}",
                status_code=409,
            )

        return await self.update_task(
            task_id,
            {
                "claimed_by": owner_agent,
                "status": "assigned",
                "lease": lease,
            },
            skip_validation=True,
        )

    async def renew_task_lease(
        self,
        task_id: str,
        lease: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Renew an active lease owned by the same node/agent."""
        task = await self.get_task(task_id)
        if not task:
            return None

        current_lease = task.get("lease")
        if not isinstance(current_lease, dict):
            raise TaskLeaseError("LEASE_NOT_FOUND", "Task has no lease to renew", status_code=409)

        now = datetime.now(UTC)
        if not self._is_lease_active(current_lease, now=now):
            raise TaskLeaseError(
                "LEASE_EXPIRED",
                "Existing lease has expired; claim the task again",
                status_code=409,
            )

        owner_node = str(lease.get("owner_node") or "").strip()
        owner_agent = str(lease.get("owner_agent") or "").strip()
        if (
            current_lease.get("owner_node") != owner_node
            or current_lease.get("owner_agent") != owner_agent
        ):
            raise TaskLeaseError(
                "LEASE_OWNER_MISMATCH",
                "Only the current lease owner can renew this lease",
                status_code=409,
            )

        if current_lease.get("path_lock") != lease.get("path_lock"):
            raise TaskLeaseError(
                "PATH_LOCK_IMMUTABLE",
                "path_lock cannot change during lease renewal",
                status_code=400,
            )

        if not self._is_lease_active(lease, now=now):
            raise TaskLeaseError(
                "INVALID_LEASE",
                "lease_expires_at must be a future UTC timestamp",
                status_code=400,
            )

        return await self.update_task(
            task_id,
            {
                "lease": lease,
                "claimed_by": owner_agent,
                "status": "assigned",
            },
            skip_validation=True,
        )

    async def release_task_lease(
        self,
        task_id: str,
        *,
        owner_node: str | None = None,
        owner_agent: str | None = None,
    ) -> dict[str, Any] | None:
        """Release task lease with optional owner verification."""
        task = await self.get_task(task_id)
        if not task:
            return None

        current_lease = task.get("lease")
        if not isinstance(current_lease, dict):
            raise TaskLeaseError("LEASE_NOT_FOUND", "Task has no lease to release", status_code=409)

        if owner_node and current_lease.get("owner_node") != owner_node:
            raise TaskLeaseError(
                "LEASE_OWNER_MISMATCH",
                "owner_node does not match the active lease owner",
                status_code=409,
            )
        if owner_agent and current_lease.get("owner_agent") != owner_agent:
            raise TaskLeaseError(
                "LEASE_OWNER_MISMATCH",
                "owner_agent does not match the active lease owner",
                status_code=409,
            )

        updates: dict[str, Any] = {"lease": None}
        if task.get("claimed_by") == current_lease.get("owner_agent"):
            updates["claimed_by"] = ""
        if task.get("status") == "assigned":
            updates["status"] = "pending"

        # Internal operation - bypasses transition validation
        return await self.update_task(task_id, updates, skip_validation=True)

    async def requeue_task(
        self,
        task_id: str,
        *,
        owner_node: str | None = None,
        owner_agent: str | None = None,
    ) -> dict[str, Any] | None:
        """Force task back to pending and clear ownership metadata."""
        task = await self.get_task(task_id)
        if not task:
            return None

        current_lease = task.get("lease")
        if isinstance(current_lease, dict):
            if owner_node and current_lease.get("owner_node") != owner_node:
                raise TaskLeaseError(
                    "LEASE_OWNER_MISMATCH",
                    "owner_node does not match the active lease owner",
                    status_code=409,
                )
            if owner_agent and current_lease.get("owner_agent") != owner_agent:
                raise TaskLeaseError(
                    "LEASE_OWNER_MISMATCH",
                    "owner_agent does not match the active lease owner",
                    status_code=409,
                )

        # Internal operation - bypasses transition validation
        return await self.update_task(
            task_id,
            {
                "status": "pending",
                "claimed_by": "",
                "lease": None,
            },
            skip_validation=True,
        )

    async def get_stats(self) -> dict[str, int]:
        """Get task queue statistics.

        Returns:
            Statistics dict
        """
        client = self._get_redis_client()
        if not client:
            raw_stats = await self._task_queue.get_stats()
            # Flatten to match API contract
            by_status = raw_stats.get("by_status", {})
            return {
                "pending": by_status.get("pending", 0),
                "assigned": by_status.get("assigned", 0),
                "in_progress": by_status.get("in_progress", 0),
                "completed": by_status.get("completed", 0),
                "failed": by_status.get("failed", 0),
                "total": raw_stats.get("total", 0),
            }

        try:
            task_ids = client.smembers(self._tasks_index_key())
            stats = {"pending": 0, "in_progress": 0, "completed": 0, "total": 0, "assigned": 0}

            for task_id in task_ids:
                task_data = client.hgetall(self._task_key(task_id))
                if not task_data:
                    continue

                status = task_data.get("status", "pending")
                stats[status] = stats.get(status, 0) + 1
                stats["total"] += 1

            return stats
        except Exception as e:
            logger.error(f"Failed to get task stats: {e}")
            return {"pending": 0, "in_progress": 0, "completed": 0, "total": 0, "assigned": 0}

    async def list_ready_tasks(
        self,
        priority: str | None = None,
        limit: int = 50,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """List tasks that are ready for execution (all dependencies met).

        A task is "ready" when:
        - status == "pending"
        - Not claimed/leased
        - All depends_on task IDs reference completed tasks

        Args:
            priority: Filter by priority
            limit: Maximum results
            agent: Filter by required_role matching this agent

        Returns:
            Dict with "ready" (list of ready tasks) and "blocked_count" (int)
        """
        # Fetch all pending tasks
        pending = await self.list_tasks(status="pending", limit=1000)

        # Build a status lookup for dependency checking
        all_tasks = await self.list_tasks(limit=10000)
        status_map: dict[str, str] = {t["id"]: t["status"] for t in all_tasks}

        # Detect cycles: find tasks in dependency loops
        # Build adjacency list
        dep_graph: dict[str, list[str]] = {}
        for t in pending:
            deps = t.get("depends_on") or []
            if deps:
                dep_graph[t["id"]] = deps

        cycle_members: set[str] = set()
        for task_id in dep_graph:
            visited: set[str] = set()
            current = task_id
            while current in dep_graph:
                if current in visited:
                    cycle_members.update(visited)
                    break
                visited.add(current)
                # Follow chain (simplified: only follow first dep for cycle detection)
                next_deps = dep_graph.get(current, [])
                if not next_deps:
                    break
                current = next_deps[0]

        ready = []
        blocked_count = 0

        for task in pending:
            # Skip claimed/leased tasks
            if task.get("claimed_by"):
                continue
            lease = task.get("lease")
            if isinstance(lease, dict) and lease.get("owner_agent"):
                continue

            # Apply priority filter
            if priority and task.get("priority") != priority:
                continue

            # Apply agent/role filter
            if agent and task.get("required_role") and task.get("required_role") != agent:
                continue

            deps = task.get("depends_on") or []
            task_id = task["id"]

            if task_id in cycle_members:
                blocked_count += 1
                continue

            if deps:
                all_met = True
                for dep_id in deps:
                    dep_status = status_map.get(dep_id)
                    if dep_status != "completed":
                        all_met = False
                        break
                if not all_met:
                    blocked_count += 1
                    continue

            ready.append(task)

            if len(ready) >= limit:
                break

        return {"ready": ready, "blocked_count": blocked_count}

    def _serialize_task(self, task: dict[str, Any]) -> dict[str, str]:
        """Serialize task for Redis storage (all strings)."""
        return {
            "id": str(task["id"]),
            "subject": str(task["subject"]),
            "description": str(task["description"]),
            "priority": str(task["priority"]),
            "status": str(task["status"]),
            "required_role": str(task.get("required_role", "")),
            "claimed_by": str(task.get("claimed_by", "")),
            "depends_on": json.dumps(task.get("depends_on") or []),
            "lease": json.dumps(task.get("lease")) if task.get("lease") is not None else "",
            "order": str(task.get("order", 0)),
            "created_at": str(task["created_at"]),
            "updated_at": str(task["updated_at"]),
        }

    def _deserialize_task(self, data: dict[str, str]) -> dict[str, Any]:
        """Deserialize task from Redis storage."""
        # Convert empty strings to None for optional fields
        required_role = data.get("required_role")
        required_role = None if not required_role or required_role == "" else required_role

        claimed_by = data.get("claimed_by")
        claimed_by = None if not claimed_by or claimed_by == "" else claimed_by

        lease_raw = data.get("lease")
        lease: dict[str, Any] | None = None
        if lease_raw and lease_raw != "":
            try:
                lease = json.loads(lease_raw)
            except (TypeError, ValueError):
                lease = None

        # Parse depends_on field
        depends_on_raw = data.get("depends_on", "[]")
        depends_on: list[str] = []
        if depends_on_raw and depends_on_raw != "":
            try:
                depends_on = json.loads(depends_on_raw)
            except (TypeError, ValueError):
                depends_on = []

        # Parse order field (default to 0 if not present)
        order = data.get("order", "0")
        try:
            order = int(order)
        except (ValueError, TypeError):
            order = 0

        return {
            "id": data["id"],
            "subject": data["subject"],
            "description": data["description"],
            "priority": data["priority"],
            "status": data["status"],
            "required_role": required_role,
            "claimed_by": claimed_by,
            "depends_on": depends_on,
            "lease": lease,
            "order": order,
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
        }


# Singleton instance
_task_handler: TaskQueueHandler | None = None


def get_task_handler() -> TaskQueueHandler:
    """Get or create the task handler singleton.

    Returns:
        TaskQueueHandler: The task handler instance
    """
    global _task_handler
    if _task_handler is None:
        _task_handler = TaskQueueHandler()
    return _task_handler
