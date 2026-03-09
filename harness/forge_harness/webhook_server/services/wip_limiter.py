"""WIP Limiter Service — DF-5002
===============================

Lane-level WIP (Work In Progress) limits and retry budgets for Dark Factory
queue optimization. This service tracks queue latency, retry waste, and enforces
per-lane limits to ensure throughput optimization.

The WIP Limiter complements the LaneEnforcer by adding:
- Per-lane retry budgets (max retries before human intervention required)
- Queue latency tracking and metrics
- Retry waste measurement
- Priority-based queue dispatch with throttling

Thread Safety
-------------
All mutable state is guarded by a single :class:`threading.RLock`.  The
service is designed to be thread-safe for concurrent access from multiple
workers processing the queue.

Singleton Pattern
----------------
Use :func:`get_wip_limiter` to obtain the shared instance.  In tests call
:func:`reset_wip_limiter` before each test to start from a clean slate.

Usage
-----

    from forge_harness.webhook_server.services.wip_limiter import (
        get_wip_limiter,
        WorkCellLane,
    )

    limiter = get_wip_limiter()

    # Check if task can be dispatched (respects WIP limits + retry budgets)
    can_dispatch, reason = limiter.can_dispatch("task-001", WorkCellLane.docs)
    # can_dispatch = True/False, reason = explanation

    # Record task start
    limiter.record_dispatch("task-001", WorkCellLane.docs)

    # Record task completion (success)
    limiter.record_completion("task-001", WorkCellLane.docs, success=True)

    # Record task failure (may be retried)
    limiter.record_completion("task-001", WorkCellLane.docs, success=False)

    # Get queue metrics
    metrics = limiter.get_queue_metrics()
    # {
    #     "queue_latency_avg_ms": 123.4,
    #     "retry_waste_rate": 0.15,
    #     "lanes": {...}
    # }

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md (DF-5002)
forge_harness/webhook_server/services/lane_enforcer.py (LaneEnforcer)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock
from typing import Any

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.work_cell import (
    WorkCellLane,
    get_lane_config,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class RetryBudget:
    """Retry budget configuration for a lane.

    Attributes:
        max_retries: Maximum number of retries allowed before requiring
                     human intervention.
        retry_window_seconds: Time window for counting retries (resets after).
        backoff_multiplier: Multiplier for exponential backoff between retries.
    """

    max_retries: int = 3
    retry_window_seconds: int = 3600  # 1 hour
    backoff_multiplier: float = 2.0


@dataclass
class TaskMetrics:
    """Metrics for a single task.

    Attributes:
        task_id: Unique task identifier.
        lane: The work-cell lane the task is in.
        dispatch_time: When the task was dispatched (Unix timestamp).
        completion_time: When the task completed (Unix timestamp).
        retry_count: Number of times this task has been retried.
        success: Whether the task completed successfully.
    """

    task_id: str
    lane: WorkCellLane
    dispatch_time: float
    completion_time: float | None = None
    retry_count: int = 0
    success: bool | None = None  # None = still in progress


@dataclass
class LaneMetrics:
    """Aggregated metrics for a single lane.

    Attributes:
        lane: The work-cell lane.
        total_dispatched: Total tasks dispatched to this lane.
        total_completed: Total tasks completed in this lane.
        total_succeeded: Total tasks that succeeded.
        total_failed: Total tasks that failed permanently.
        total_retried: Total tasks that were retried at least once.
        avg_latency_ms: Average queue latency in milliseconds.
        max_latency_ms: Maximum observed latency in milliseconds.
    """

    lane: WorkCellLane
    total_dispatched: int = 0
    total_completed: int = 0
    total_succeeded: int = 0
    total_failed: int = 0
    total_retried: int = 0
    avg_latency_ms: float = 0.0
    max_latency_ms: float = 0.0

    @property
    def retry_rate(self) -> float:
        """Calculate retry rate (tasks retried / total completed)."""
        if self.total_completed == 0:
            return 0.0
        return self.total_retried / self.total_completed

    @property
    def success_rate(self) -> float:
        """Calculate success rate (succeeded / completed)."""
        if self.total_completed == 0:
            return 0.0
        return self.total_succeeded / self.total_completed


# ---------------------------------------------------------------------------
# WIP Limiter
# ---------------------------------------------------------------------------


class WIPLimiter:
    """Lane-level WIP limits and retry budgets for queue optimization.

    Responsibilities:
    - Enforce per-lane retry budgets (max retries before human intervention).
    - Track queue latency metrics per lane.
    - Calculate retry waste rates.
    - Provide can_dispatch() checks that respect both WIP limits and retry budgets.
    - Support priority-based dispatch with throttling.

    The limiter maintains in-memory state for metrics. In production, consider
    periodically persisting metrics to disk for durability.
    """

    def __init__(self) -> None:
        self._lock = RLock()

        # Default retry budgets per lane type
        self._retry_budgets: dict[WorkCellLane, RetryBudget] = {
            lane: RetryBudget() for lane in WorkCellLane
        }

        # Override defaults for specific lanes
        self._retry_budgets[WorkCellLane.api_simple] = RetryBudget(
            max_retries=5,
            retry_window_seconds=1800,  # 30 minutes
            backoff_multiplier=1.5,
        )
        self._retry_budgets[WorkCellLane.security_change] = RetryBudget(
            max_retries=1,
            retry_window_seconds=7200,  # 2 hours
            backoff_multiplier=2.0,
        )
        self._retry_budgets[WorkCellLane.deployment] = RetryBudget(
            max_retries=1,
            retry_window_seconds=7200,
            backoff_multiplier=2.0,
        )

        # Per-lane retry counts (task_id -> retry_count)
        self._retry_counts: dict[str, dict[str, int]] = {}  # lane -> {task_id -> count}

        # Active tasks (task_id -> TaskMetrics)
        self._active_tasks: dict[str, TaskMetrics] = {}

        # Completed tasks for metrics calculation (keep last 1000 per lane)
        self._completed_tasks: dict[WorkCellLane, list[TaskMetrics]] = {
            lane: [] for lane in WorkCellLane
        }

        # Per-lane latency samples for moving average (last 100 per lane)
        self._latency_samples: dict[WorkCellLane, list[float]] = {lane: [] for lane in WorkCellLane}

        # Aggregated lane metrics
        self._lane_metrics: dict[WorkCellLane, LaneMetrics] = {
            lane: LaneMetrics(lane=lane) for lane in WorkCellLane
        }

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_retry_budget(self, lane: WorkCellLane, budget: RetryBudget) -> None:
        """Set custom retry budget for a lane.

        Args:
            lane: The work-cell lane to configure.
            budget: The retry budget to apply.
        """
        with self._lock:
            self._retry_budgets[lane] = budget
            logger.info(
                "WIPLimiter: Set retry budget for lane=%s max_retries=%d",
                lane.value,
                budget.max_retries,
            )

    def get_retry_budget(self, lane: WorkCellLane) -> RetryBudget:
        """Get retry budget for a lane.

        Args:
            lane: The work-cell lane.

        Returns:
            The RetryBudget for the lane.
        """
        with self._lock:
            return self._retry_budgets[lane]

    # ------------------------------------------------------------------
    # Dispatch Control
    # ------------------------------------------------------------------

    def can_dispatch(self, task_id: str, lane: WorkCellLane, priority: int = 1) -> tuple[bool, str]:
        """Check if a task can be dispatched to a lane.

        Considers:
        - Current WIP in the lane (via lane config)
        - Retry budget (has task exceeded max retries?)
        - Lane-specific throttling rules

        Args:
            task_id: Unique identifier for the task.
            lane: The work-cell lane to dispatch to.
            priority: Task priority (higher = more urgent, affects throttling).

        Returns:
            Tuple of (can_dispatch: bool, reason: str).
            Reason explains why dispatch is allowed or denied.
        """
        with self._lock:
            budget = self._retry_budgets[lane]
            current_retry_count = self._get_retry_count(lane, task_id)

            # Check retry budget
            if current_retry_count >= budget.max_retries:
                return (
                    False,
                    f"Task {task_id} exceeded max retries ({budget.max_retries}) for lane {lane.value}. Human intervention required.",
                )

            # Check WIP limit from lane config
            config = get_lane_config(lane)
            current_wip = len([t for t in self._active_tasks.values() if t.lane == lane])

            if current_wip >= config.max_wip:
                # Calculate estimated wait time
                avg_latency = self._lane_metrics[lane].avg_latency_ms
                wait_time_ms = (avg_latency * config.max_wip) if avg_latency > 0 else 1000
                return (
                    False,
                    f"Lane {lane.value} at capacity (WIP: {current_wip}/{config.max_wip}). Estimated wait: {wait_time_ms:.0f}ms",
                )

            # Priority-based throttling: lower priority tasks get throttled more
            if priority < 3:  # Not urgent
                # Check if lane has high load (more than 70% of WIP)
                if current_wip >= int(config.max_wip * 0.7):
                    throttle_factor = (3 - priority) / 3  # 0.33 for priority 2, 0.67 for priority 1
                    import random

                    if (
                        random.random() < throttle_factor * 0.2
                    ):  # 20% chance of throttle at high load
                        return (
                            False,
                            f"Task {task_id} throttled due to lane load (priority={priority})",
                        )

            return (
                True,
                f"Task {task_id} can be dispatched to lane {lane.value} (retry_count={current_retry_count}, wip={current_wip}/{config.max_wip})",
            )

    def record_dispatch(self, task_id: str, lane: WorkCellLane) -> None:
        """Record that a task has been dispatched.

        Args:
            task_id: Unique identifier for the task.
            lane: The work-cell lane the task was dispatched to.
        """
        with self._lock:
            metrics = TaskMetrics(
                task_id=task_id,
                lane=lane,
                dispatch_time=time.time(),
            )
            self._active_tasks[task_id] = metrics

            # Update lane metrics
            self._lane_metrics[lane].total_dispatched += 1

            logger.debug("WIPLimiter: Dispatched task %s to lane %s", task_id, lane.value)

    def record_completion(self, task_id: str, lane: WorkCellLane, success: bool) -> None:
        """Record that a task has completed.

        Args:
            task_id: Unique identifier for the task.
            lane: The work-cell lane the task was in.
            success: Whether the task completed successfully.
        """
        with self._lock:
            task_metrics = self._active_tasks.pop(task_id, None)
            if task_metrics is None:
                logger.warning(
                    "WIPLimiter: Attempted to complete unknown task %s in lane %s",
                    task_id,
                    lane.value,
                )
                return

            completion_time = time.time()
            latency_ms = (completion_time - task_metrics.dispatch_time) * 1000

            # Update task metrics
            task_metrics.completion_time = completion_time
            task_metrics.success = success
            task_metrics.retry_count = self._get_retry_count(lane, task_id)

            # Store in completed tasks (limit to last 1000)
            completed_list = self._completed_tasks[lane]
            completed_list.append(task_metrics)
            if len(completed_list) > 1000:
                completed_list.pop(0)

            # Update latency samples (limit to last 100)
            latency_list = self._latency_samples[lane]
            latency_list.append(latency_ms)
            if len(latency_list) > 100:
                latency_list.pop(0)

            # Update lane metrics
            lane_metrics = self._lane_metrics[lane]
            lane_metrics.total_completed += 1

            if success:
                lane_metrics.total_succeeded += 1
            else:
                lane_metrics.total_failed += 1

            if task_metrics.retry_count > 0:
                lane_metrics.total_retried += 1

            # Update latency stats
            if latency_list:
                lane_metrics.avg_latency_ms = sum(latency_list) / len(latency_list)
                lane_metrics.max_latency_ms = max(latency_list)

            logger.info(
                "WIPLimiter: Task %s completed in lane %s (success=%s, latency=%.2fms, retries=%d)",
                task_id,
                lane.value,
                success,
                latency_ms,
                task_metrics.retry_count,
            )

    def record_retry(self, task_id: str, lane: WorkCellLane) -> int:
        """Record that a task is being retried.

        Args:
            task_id: Unique identifier for the task.
            lane: The work-cell lane the task is in.

        Returns:
            The new retry count after this retry.
        """
        with self._lock:
            if lane not in self._retry_counts:
                self._retry_counts[lane] = {}

            retry_count = self._retry_counts[lane].get(task_id, 0) + 1
            self._retry_counts[lane][task_id] = retry_count

            # Update active task metrics
            if task_id in self._active_tasks:
                self._active_tasks[task_id].retry_count = retry_count

            logger.info(
                "WIPLimiter: Task %s retry %d in lane %s",
                task_id,
                retry_count,
                lane.value,
            )
            return retry_count

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _get_retry_count(self, lane: WorkCellLane, task_id: str) -> int:
        """Get retry count for a task in a lane."""
        return self._retry_counts.get(lane, {}).get(task_id, 0)

    def get_lane_metrics(self, lane: WorkCellLane) -> LaneMetrics:
        """Get metrics for a specific lane.

        Args:
            lane: The work-cell lane.

        Returns:
            LaneMetrics for the lane.
        """
        with self._lock:
            return self._lane_metrics[lane]

    def get_all_lane_metrics(self) -> dict[str, LaneMetrics]:
        """Get metrics for all lanes.

        Returns:
            Dict mapping lane value to LaneMetrics.
        """
        with self._lock:
            return {lane.value: self._lane_metrics[lane] for lane in WorkCellLane}

    def get_queue_metrics(self) -> dict[str, Any]:
        """Get overall queue metrics for monitoring.

        Returns:
            Dict with aggregated metrics including:
            - queue_latency_avg_ms: Average latency across all lanes
            - retry_waste_rate: Overall retry waste rate
            - lanes: Per-lane metrics
        """
        with self._lock:
            all_latencies = []
            total_completed = 0
            total_retried = 0

            for lane in WorkCellLane:
                latencies = self._latency_samples[lane]
                all_latencies.extend(latencies)
                metrics = self._lane_metrics[lane]
                total_completed += metrics.total_completed
                total_retried += metrics.total_retried

            avg_latency = sum(all_latencies) / len(all_latencies) if all_latencies else 0.0
            retry_waste_rate = total_retried / total_completed if total_completed > 0 else 0.0

            return {
                "queue_latency_avg_ms": round(avg_latency, 2),
                "queue_latency_p95_ms": round(
                    sorted(all_latencies)[int(len(all_latencies) * 0.95)] if all_latencies else 0, 2
                ),
                "retry_waste_rate": round(retry_waste_rate, 4),
                "total_dispatched": sum(m.total_dispatched for m in self._lane_metrics.values()),
                "total_completed": total_completed,
                "total_succeeded": sum(m.total_succeeded for m in self._lane_metrics.values()),
                "total_failed": sum(m.total_failed for m in self._lane_metrics.values()),
                "lanes": {
                    lane.value: {
                        "total_dispatched": metrics.total_dispatched,
                        "total_completed": metrics.total_completed,
                        "total_succeeded": metrics.total_succeeded,
                        "total_failed": metrics.total_failed,
                        "total_retried": metrics.total_retried,
                        "retry_rate": round(metrics.retry_rate, 4),
                        "success_rate": round(metrics.success_rate, 4),
                        "avg_latency_ms": round(metrics.avg_latency_ms, 2),
                        "max_latency_ms": round(metrics.max_latency_ms, 2),
                        "max_wip": get_lane_config(lane).max_wip,
                        "retry_budget": {
                            "max_retries": self._retry_budgets[lane].max_retries,
                            "retry_window_seconds": self._retry_budgets[lane].retry_window_seconds,
                        },
                    }
                    for lane, metrics in self._lane_metrics.items()
                },
            }

    def get_active_tasks(self, lane: WorkCellLane | None = None) -> list[TaskMetrics]:
        """Get currently active tasks.

        Args:
            lane: Optional lane filter. If None, returns all active tasks.

        Returns:
            List of active TaskMetrics.
        """
        with self._lock:
            if lane is None:
                return list(self._active_tasks.values())
            return [t for t in self._active_tasks.values() if t.lane == lane]

    def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        """Get status for a specific task.

        Args:
            task_id: The task identifier.

        Returns:
            Dict with task status or None if not found.
        """
        with self._lock:
            # Check active tasks
            if task_id in self._active_tasks:
                task = self._active_tasks[task_id]
                return {
                    "task_id": task.task_id,
                    "lane": task.lane.value,
                    "status": "active",
                    "dispatch_time": task.dispatch_time,
                    "retry_count": task.retry_count,
                    "latency_so_far_ms": (time.time() - task.dispatch_time) * 1000,
                }

            # Check completed tasks
            for lane in WorkCellLane:
                for task in self._completed_tasks[lane]:
                    if task.task_id == task_id:
                        latency = (
                            (task.completion_time - task.dispatch_time) * 1000
                            if task.completion_time
                            else 0
                        )
                        return {
                            "task_id": task.task_id,
                            "lane": task.lane.value,
                            "status": "completed",
                            "success": task.success,
                            "dispatch_time": task.dispatch_time,
                            "completion_time": task.completion_time,
                            "latency_ms": latency,
                            "retry_count": task.retry_count,
                        }

            return None

    # ------------------------------------------------------------------
    # Reset (for testing)
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all state — intended for use in tests only."""
        with self._lock:
            self._retry_counts.clear()
            self._active_tasks.clear()
            for lane in WorkCellLane:
                self._completed_tasks[lane].clear()
                self._latency_samples[lane].clear()
                self._lane_metrics[lane] = LaneMetrics(lane=lane)
            logger.debug("WIPLimiter: Reset complete")


# ---------------------------------------------------------------------------
# Singleton Factory
# ---------------------------------------------------------------------------

_limiter_instance: WIPLimiter | None = None
_limiter_lock: RLock = RLock()


def get_wip_limiter() -> WIPLimiter:
    """Return the global :class:`WIPLimiter` singleton.

    The instance is created lazily on the first call and reused on all
    subsequent calls. Thread-safe.

    To replace the singleton in tests call :func:`reset_wip_limiter` first.

    Returns:
        The singleton :class:`WIPLimiter`.
    """
    global _limiter_instance
    with _limiter_lock:
        if _limiter_instance is None:
            _limiter_instance = WIPLimiter()
            logger.info("WIPLimiter singleton created")
        return _limiter_instance


def reset_wip_limiter() -> None:
    """Destroy the global singleton — intended for use in tests only.

    After calling this function the next call to :func:`get_wip_limiter`
    will create a fresh instance.
    """
    global _limiter_instance
    with _limiter_lock:
        _limiter_instance = None
        logger.debug("WIPLimiter singleton reset")
