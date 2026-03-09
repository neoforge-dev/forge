"""
FORGE Harness Parallel Dispatch
================================

Dispatch multiple tasks to agents in parallel with concurrency control.

Usage:
    from forge_harness.iteration.parallel_dispatch import (
        ParallelDispatcher,
        DispatchBatch,
        TaskDispatchRequest,
        dispatch_features,
    )

    # Create batch
    batch = DispatchBatch(
        batch_id="iteration-5",
        requests=[
            TaskDispatchRequest(
                task_id="HRN-001",
                agent_type=AgentType.CODEX,
                prompt="Implement feature X",
            ),
            TaskDispatchRequest(
                task_id="HRN-002",
                agent_type=AgentType.OPENCODE,
                prompt="Add API endpoint Y",
            ),
        ],
    )

    # Dispatch in parallel
    dispatcher = ParallelDispatcher(max_concurrent=3)
    result = await dispatcher.dispatch_batch(batch)
    print(f"Completed: {result.success_count}/{len(batch.requests)}")

    # Convenience function
    result = await dispatch_features(
        ["HRN-001", "HRN-002", "HRN-003"],
        max_concurrent=5,
    )
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .dispatcher import (
    AgentType,
    DispatchError,
    DispatchResult,
    DispatchStatus,
    _build_prompt,
    _create_session,
    _load_task,
    _send_to_session,
    select_agent,
)
from .monitor import ProgressMonitor, SessionStatus

logger = logging.getLogger(__name__)


# =============================================================================
# Custom Exceptions
# =============================================================================


class BatchDispatchError(Exception):
    """Raised when batch dispatch fails critically."""

    pass


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class TaskDispatchRequest:
    """Request to dispatch a single task to an agent.

    Attributes:
        task_id: Unique task identifier
        agent_type: Type of agent to use (from AgentType enum)
        prompt: Task prompt/instructions
        working_dir: Working directory for the agent
        timeout: Task-specific timeout in seconds
        priority: Task priority (1=highest, 5=lowest)
        metadata: Additional task metadata
    """

    task_id: str
    agent_type: AgentType
    prompt: str
    working_dir: str | None = None
    timeout: float = 300.0  # 5 minutes default
    priority: int = 3
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "task_id": self.task_id,
            "agent_type": self.agent_type.value,
            "prompt": self.prompt,
            "working_dir": self.working_dir,
            "timeout": self.timeout,
            "priority": self.priority,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskDispatchRequest:
        """Create from dictionary."""
        return cls(
            task_id=data["task_id"],
            agent_type=AgentType(data["agent_type"]),
            prompt=data["prompt"],
            working_dir=data.get("working_dir"),
            timeout=data.get("timeout", 300.0),
            priority=data.get("priority", 3),
            metadata=data.get("metadata", {}),
        )


@dataclass
class DispatchBatch:
    """A batch of related task dispatch requests.

    Attributes:
        batch_id: Unique batch identifier
        requests: List of TaskDispatchRequest
        created_at: When batch was created
        max_concurrent: Maximum concurrent agents (default 5)
        batch_timeout: Overall batch timeout
        fail_fast: Stop on first failure (default False)
    """

    batch_id: str
    requests: list[TaskDispatchRequest]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    max_concurrent: int = 5
    batch_timeout: float = 900.0  # 15 minutes
    fail_fast: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "batch_id": self.batch_id,
            "requests": [req.to_dict() for req in self.requests],
            "created_at": self.created_at.isoformat(),
            "max_concurrent": self.max_concurrent,
            "batch_timeout": self.batch_timeout,
            "fail_fast": self.fail_fast,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DispatchBatch:
        """Create from dictionary."""
        return cls(
            batch_id=data["batch_id"],
            requests=[TaskDispatchRequest.from_dict(req) for req in data["requests"]],
            created_at=datetime.fromisoformat(data["created_at"]),
            max_concurrent=data.get("max_concurrent", 5),
            batch_timeout=data.get("batch_timeout", 900.0),
            fail_fast=data.get("fail_fast", False),
        )

    @property
    def task_ids(self) -> list[str]:
        """Return all task IDs in batch."""
        return [req.task_id for req in self.requests]


@dataclass
class BatchResult:
    """Result of batch dispatch operation.

    Attributes:
        batch_id: Batch identifier
        results: Dict mapping task_id to DispatchResult
        started_at: When batch started
        completed_at: When batch completed
        total_duration: Total execution time
        success_count: Number of successful tasks
        failure_count: Number of failed tasks
        status: Overall batch status
    """

    batch_id: str
    results: dict[str, DispatchResult]
    started_at: datetime
    completed_at: datetime | None = None
    total_duration: float = 0.0
    success_count: int = 0
    failure_count: int = 0
    status: str = "pending"  # pending, running, completed, partial, failed

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "batch_id": self.batch_id,
            "results": {task_id: result.to_dict() for task_id, result in self.results.items()},
            "started_at": self.started_at.isoformat(),
            "completed_at": (self.completed_at.isoformat() if self.completed_at else None),
            "total_duration": self.total_duration,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "status": self.status,
        }

    @property
    def is_complete(self) -> bool:
        """Check if batch is complete."""
        return self.status in ["completed", "partial", "failed"]

    @property
    def success_rate(self) -> float:
        """Calculate success rate as a fraction (0.0-1.0)."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.success_count / total


# =============================================================================
# Parallel Dispatcher
# =============================================================================


class ParallelDispatcher:
    """Dispatch multiple tasks to agents in parallel.

    Features:
    - Concurrent dispatch with configurable limit
    - Timeout handling per-task and per-batch
    - Partial failure handling (continue on error)
    - Progress monitoring via ProgressMonitor
    - Resource limits to prevent OOM

    Usage:
        dispatcher = ParallelDispatcher(max_concurrent=3)

        batch = DispatchBatch(
            batch_id="iteration-5",
            requests=[
                TaskDispatchRequest(task_id="HRN-001", agent_type=AgentType.CODEX, prompt="..."),
                TaskDispatchRequest(task_id="HRN-002", agent_type=AgentType.OPENCODE, prompt="..."),
            ]
        )

        result = await dispatcher.dispatch_batch(batch)
        print(f"Completed: {result.success_count}/{len(batch.requests)}")
    """

    def __init__(
        self,
        max_concurrent: int = 5,
        monitor: ProgressMonitor | None = None,
        default_timeout: float = 300.0,
    ):
        """Initialize parallel dispatcher.

        Args:
            max_concurrent: Maximum concurrent dispatches (default 5)
            monitor: ProgressMonitor instance (creates new if None)
            default_timeout: Default task timeout in seconds
        """
        self.max_concurrent = max_concurrent
        self.monitor = monitor or ProgressMonitor()
        self.default_timeout = default_timeout
        self._active_sessions: dict[str, DispatchResult] = {}
        self._semaphore: asyncio.Semaphore | None = None

    async def dispatch_batch(
        self,
        batch: DispatchBatch,
    ) -> BatchResult:
        """Dispatch a batch of tasks in parallel.

        Args:
            batch: DispatchBatch with requests to dispatch

        Returns:
            BatchResult with aggregated results
        """
        started_at = datetime.now(UTC)
        results: dict[str, DispatchResult] = {}

        logger.info(
            f"Dispatching batch {batch.batch_id} with {len(batch.requests)} tasks "
            f"(max_concurrent={batch.max_concurrent})"
        )

        # Create semaphore for concurrency limit
        # Use batch-specific max_concurrent or instance default
        max_concurrent = batch.max_concurrent or self.max_concurrent
        semaphore = asyncio.Semaphore(max_concurrent)

        # Create async tasks for each request
        tasks = [self._dispatch_single(request, semaphore) for request in batch.requests]

        # Use gather with return_exceptions to continue on error
        dispatch_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        success_count = 0
        failure_count = 0

        for i, result in enumerate(dispatch_results):
            request = batch.requests[i]
            task_id = request.task_id

            if isinstance(result, Exception):
                # Task raised an exception
                logger.error(f"Task {task_id} failed with exception: {result}")
                failure_count += 1

                # Create failed DispatchResult
                results[task_id] = DispatchResult(
                    task_id=task_id,
                    session_id=f"{request.agent_type.value}-{task_id}",
                    agent_type=request.agent_type,
                    started_at=started_at,
                    status=DispatchStatus.FAILED,
                    prompt=request.prompt,
                    working_directory=request.working_dir,
                )

                # Fail fast if requested
                if batch.fail_fast:
                    logger.warning(f"Fail-fast enabled, stopping batch {batch.batch_id}")
                    break
            else:
                # Successful dispatch
                results[task_id] = result
                success_count += 1
                logger.info(f"Task {task_id} dispatched successfully")

        # Calculate overall status
        completed_at = datetime.now(UTC)
        total_duration = (completed_at - started_at).total_seconds()

        if failure_count == 0:
            status = "completed"
        elif success_count == 0:
            status = "failed"
        else:
            status = "partial"

        batch_result = BatchResult(
            batch_id=batch.batch_id,
            results=results,
            started_at=started_at,
            completed_at=completed_at,
            total_duration=total_duration,
            success_count=success_count,
            failure_count=failure_count,
            status=status,
        )

        logger.info(
            f"Batch {batch.batch_id} dispatch complete: "
            f"{success_count} success, {failure_count} failed, "
            f"duration={total_duration:.1f}s"
        )

        return batch_result

    async def _dispatch_single(
        self,
        request: TaskDispatchRequest,
        semaphore: asyncio.Semaphore,
    ) -> DispatchResult:
        """Dispatch a single task with semaphore limit.

        Args:
            request: Task dispatch request
            semaphore: Concurrency limiter

        Returns:
            DispatchResult for this task

        Raises:
            DispatchError: If dispatch fails
        """
        async with semaphore:
            logger.debug(f"Dispatching task {request.task_id} to {request.agent_type}")

            # Build session name
            session_id = f"{request.agent_type.value}-{request.task_id}"

            # Create tmux session (run in executor to avoid blocking)
            loop = asyncio.get_event_loop()
            try:
                created = await loop.run_in_executor(
                    None,
                    _create_session,
                    session_id,
                    request.working_dir,
                    30,  # timeout
                )

                if not created:
                    raise DispatchError(f"Failed to create tmux session: {session_id}")

                # Send prompt to session
                sent = await loop.run_in_executor(
                    None,
                    _send_to_session,
                    session_id,
                    request.prompt,
                    request.agent_type,
                    30,  # timeout
                )

                if not sent:
                    raise DispatchError(f"Failed to send prompt to session: {session_id}")

                # Create result
                result = DispatchResult(
                    task_id=request.task_id,
                    session_id=session_id,
                    agent_type=request.agent_type,
                    started_at=datetime.now(UTC),
                    status=DispatchStatus.RUNNING,
                    prompt=request.prompt,
                    working_directory=request.working_dir,
                )

                # Track active session
                self._active_sessions[session_id] = result

                return result

            except Exception as e:
                logger.error(f"Failed to dispatch {request.task_id}: {e}")
                raise DispatchError(f"Dispatch failed for {request.task_id}: {e}")

    async def wait_for_batch(
        self,
        batch_result: BatchResult,
        timeout: float | None = None,
    ) -> BatchResult:
        """Wait for all dispatched tasks to complete.

        Args:
            batch_result: Result from dispatch_batch
            timeout: Override batch timeout

        Returns:
            Updated BatchResult with completion status
        """
        session_ids = [result.session_id for result in batch_result.results.values()]

        logger.info(f"Waiting for {len(session_ids)} sessions to complete (timeout={timeout}s)")

        # Use monitor to wait for completion
        monitor_results = await self.monitor.wait_for_completion(
            session_ids=session_ids,
            timeout=timeout or 900.0,
            stall_threshold=300.0,
        )

        # Update batch result with completion info
        completed_count = 0
        for session_id, monitor_result in monitor_results.items():
            if monitor_result.status == SessionStatus.COMPLETED:
                completed_count += 1

        batch_result.completed_at = datetime.now(UTC)
        batch_result.total_duration = (
            batch_result.completed_at - batch_result.started_at
        ).total_seconds()

        logger.info(
            f"Batch {batch_result.batch_id} completion: "
            f"{completed_count}/{len(session_ids)} completed"
        )

        return batch_result

    def cancel_batch(self, batch_id: str) -> int:
        """Cancel all running tasks in a batch.

        Args:
            batch_id: Batch to cancel

        Returns:
            Number of tasks cancelled
        """
        logger.info(f"Cancelling batch {batch_id}")

        cancelled = 0
        sessions_to_remove = []

        for session_id, result in self._active_sessions.items():
            # Cancel all sessions (we don't track batch_id in active_sessions)
            # In a real implementation, we'd track batch_id per session
            try:
                subprocess.run(
                    ["tmux", "kill-session", "-t", session_id],
                    capture_output=True,
                    timeout=10,
                )
                cancelled += 1
                sessions_to_remove.append(session_id)
                logger.info(f"Cancelled session {session_id}")
            except Exception as e:
                logger.error(f"Failed to cancel session {session_id}: {e}")

        # Clean up tracked sessions
        for session_id in sessions_to_remove:
            del self._active_sessions[session_id]

        logger.info(f"Cancelled {cancelled} sessions for batch {batch_id}")
        return cancelled


# =============================================================================
# Helper Functions
# =============================================================================


def create_batch_from_features(
    feature_ids: list[str],
    features_path: str | Path = "features.json",
    batch_id: str | None = None,
) -> DispatchBatch:
    """Create a DispatchBatch from feature IDs in features.json.

    Args:
        feature_ids: List of feature IDs to dispatch
        features_path: Path to features.json
        batch_id: Optional batch ID (auto-generated if None)

    Returns:
        DispatchBatch ready for dispatch

    Raises:
        TaskNotFoundError: If any feature ID not found
        FileNotFoundError: If features.json doesn't exist
    """
    if isinstance(features_path, str):
        features_path = Path(features_path)

    logger.info(f"Creating batch from {len(feature_ids)} features")

    requests = []

    for feature_id in feature_ids:
        # Load task from features.json
        task = _load_task(feature_id, features_path)

        # Select agent type
        agent_type = select_agent(task)

        # Build prompt
        prompt = _build_prompt(task)

        # Create request
        request = TaskDispatchRequest(
            task_id=feature_id,
            agent_type=agent_type,
            prompt=prompt,
            timeout=task.get("timeout", 300.0),
            priority=task.get("priority", 3),
            metadata={"title": task.get("title", "")},
        )
        requests.append(request)

    # Generate batch ID if not provided
    if not batch_id:
        batch_id = f"batch-{uuid.uuid4().hex[:8]}"

    batch = DispatchBatch(
        batch_id=batch_id,
        requests=requests,
    )

    logger.info(f"Created batch {batch_id} with {len(requests)} requests")

    return batch


async def dispatch_features(
    feature_ids: list[str],
    features_path: str | Path = "features.json",
    max_concurrent: int = 5,
    timeout: float = 900.0,
) -> BatchResult:
    """Convenience function to dispatch multiple features.

    Args:
        feature_ids: Features to dispatch
        features_path: Path to features.json
        max_concurrent: Max concurrent agents
        timeout: Total timeout

    Returns:
        BatchResult with all task results
    """
    # Create batch
    batch = create_batch_from_features(feature_ids, features_path)
    batch.max_concurrent = max_concurrent
    batch.batch_timeout = timeout

    # Create dispatcher and execute
    dispatcher = ParallelDispatcher(max_concurrent=max_concurrent)
    result = await dispatcher.dispatch_batch(batch)

    return result
