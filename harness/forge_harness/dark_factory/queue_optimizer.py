"""Queue Optimizer — DF-5002
============================

Optimizes queue throughput using lane-level WIP (Work-In-Progress) limits
and retry budgets.  The goal is to prevent queue congestion, reduce retry
waste, and maintain healthy throughput across all work-cell lanes.

WIP Limits
----------
Each lane has a maximum number of tasks that can be in-progress
simultaneously.  When the limit is reached, new tasks for that lane are
queued rather than dispatched, preventing agent overload.

Retry Budgets
-------------
Each task has a maximum number of retry attempts.  When the budget is
exhausted, the task is escalated to human review rather than retried
indefinitely.  This prevents retry storms and wasted compute.

Design principles
-----------------
* **Per-lane**: WIP limits and retry budgets are lane-specific.
* **Config-driven**: all limits are adjustable at runtime.
* **Observable**: exposes queue depth, retry counts, and budget usage.
* **Singleton**: :func:`get_queue_optimizer` / :func:`reset_queue_optimizer`.
* **Thread-safe**: ``RLock`` guarded.

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-5002)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_WIP_LIMITS: dict[str, int] = {
    "docs": 5,
    "test_writing": 5,
    "api_simple": 3,
    "api_stateful": 2,
    "research": 3,
    "refactor": 2,
    "security_change": 1,
    "deployment": 1,
}

DEFAULT_RETRY_BUDGETS: dict[str, int] = {
    "docs": 3,
    "test_writing": 3,
    "api_simple": 2,
    "api_stateful": 2,
    "research": 2,
    "refactor": 1,
    "security_change": 1,
    "deployment": 0,  # No retries for deployments
}

DEFAULT_GLOBAL_WIP = 15  # Maximum total in-progress tasks across all lanes


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class LaneQueueState:
    """Mutable state of a single lane's queue.

    Attributes:
        lane:            Lane name.
        wip_limit:       Max concurrent in-progress tasks.
        retry_budget:    Max retries per task.
        active_count:    Currently in-progress tasks.
        queued_count:    Tasks waiting to be dispatched.
        retry_counts:    task_id → current retry count.
        exhausted_tasks: Tasks that have exceeded their retry budget.
    """

    lane: str
    wip_limit: int
    retry_budget: int
    active_count: int = 0
    queued_count: int = 0
    retry_counts: dict[str, int] = field(default_factory=dict)
    exhausted_tasks: list[str] = field(default_factory=list)

    @property
    def available_slots(self) -> int:
        """Number of tasks that can start immediately."""
        return max(0, self.wip_limit - self.active_count)

    @property
    def utilization(self) -> float:
        """WIP utilization (0.0 to 1.0+)."""
        if self.wip_limit == 0:
            return 1.0
        return self.active_count / self.wip_limit

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "wip_limit": self.wip_limit,
            "retry_budget": self.retry_budget,
            "active_count": self.active_count,
            "queued_count": self.queued_count,
            "available_slots": self.available_slots,
            "utilization": round(self.utilization, 2),
            "total_retries": sum(self.retry_counts.values()),
            "exhausted_tasks": len(self.exhausted_tasks),
        }


@dataclass(frozen=True)
class DispatchDecision:
    """Result of checking whether a task can be dispatched.

    Attributes:
        task_id:   The task identifier.
        lane:      The target lane.
        allowed:   Whether dispatch is allowed.
        reason:    Explanation if not allowed.
        queue_position: Position in queue if not allowed (0 = immediate).
    """

    task_id: str
    lane: str
    allowed: bool
    reason: str = ""
    queue_position: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "lane": self.lane,
            "allowed": self.allowed,
            "reason": self.reason,
            "queue_position": self.queue_position,
        }


@dataclass(frozen=True)
class RetryDecision:
    """Result of checking whether a task can retry.

    Attributes:
        task_id:      The task identifier.
        lane:         The lane.
        allowed:      Whether retry is allowed.
        attempt:      Current attempt number.
        max_attempts: Maximum allowed attempts.
        reason:       Explanation.
    """

    task_id: str
    lane: str
    allowed: bool
    attempt: int
    max_attempts: int
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "lane": self.lane,
            "allowed": self.allowed,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class QueueSummary:
    """Summary of queue state across all lanes.

    Attributes:
        total_active:      Total in-progress tasks.
        total_queued:      Total waiting tasks.
        total_exhausted:   Total tasks with exhausted retry budgets.
        global_wip_limit:  Maximum total in-progress.
        global_utilization: Overall WIP utilization.
        lane_states:       Per-lane state summaries.
    """

    total_active: int
    total_queued: int
    total_exhausted: int
    global_wip_limit: int
    global_utilization: float
    lane_states: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_active": self.total_active,
            "total_queued": self.total_queued,
            "total_exhausted": self.total_exhausted,
            "global_wip_limit": self.global_wip_limit,
            "global_utilization": round(self.global_utilization, 2),
            "lane_count": len(self.lane_states),
            "lane_states": list(self.lane_states),
        }


# ---------------------------------------------------------------------------
# Queue Optimizer
# ---------------------------------------------------------------------------


class QueueOptimizer:
    """Lane-level WIP limits and retry budgets for queue throughput.

    Args:
        wip_limits: Per-lane WIP limits (defaults to DEFAULT_WIP_LIMITS).
        retry_budgets: Per-lane retry budgets (defaults to DEFAULT_RETRY_BUDGETS).
        global_wip: Maximum total in-progress tasks.
    """

    def __init__(
        self,
        wip_limits: dict[str, int] | None = None,
        retry_budgets: dict[str, int] | None = None,
        global_wip: int = DEFAULT_GLOBAL_WIP,
    ) -> None:
        self._lock = RLock()
        self._wip_limits = dict(wip_limits or DEFAULT_WIP_LIMITS)
        self._retry_budgets = dict(retry_budgets or DEFAULT_RETRY_BUDGETS)
        self._global_wip = global_wip
        self._lanes: dict[str, LaneQueueState] = {}
        self._total_active = 0
        logger.info("QueueOptimizer initialised (global_wip=%d)", global_wip)

    def _get_lane(self, lane: str) -> LaneQueueState:
        """Get or create lane state."""
        if lane not in self._lanes:
            self._lanes[lane] = LaneQueueState(
                lane=lane,
                wip_limit=self._wip_limits.get(lane, 3),
                retry_budget=self._retry_budgets.get(lane, 2),
            )
        return self._lanes[lane]

    def can_dispatch(self, task_id: str, lane: str) -> DispatchDecision:
        """Check if a task can be dispatched to a lane.

        Args:
            task_id: Task identifier.
            lane: Target lane.

        Returns:
            A :class:`DispatchDecision`.
        """
        with self._lock:
            state = self._get_lane(lane)

            # Check global WIP
            if self._total_active >= self._global_wip:
                return DispatchDecision(
                    task_id=task_id,
                    lane=lane,
                    allowed=False,
                    reason=f"Global WIP limit reached ({self._total_active}/{self._global_wip})",
                    queue_position=state.queued_count + 1,
                )

            # Check lane WIP
            if state.active_count >= state.wip_limit:
                return DispatchDecision(
                    task_id=task_id,
                    lane=lane,
                    allowed=False,
                    reason=f"Lane {lane} WIP limit reached ({state.active_count}/{state.wip_limit})",
                    queue_position=state.queued_count + 1,
                )

            return DispatchDecision(task_id=task_id, lane=lane, allowed=True)

    def task_started(self, task_id: str, lane: str) -> None:
        """Record that a task has started (moved to in-progress).

        Args:
            task_id: Task identifier.
            lane: Lane the task is running in.
        """
        with self._lock:
            state = self._get_lane(lane)
            state.active_count += 1
            self._total_active += 1
            if task_id not in state.retry_counts:
                state.retry_counts[task_id] = 0

    def task_completed(self, task_id: str, lane: str) -> None:
        """Record that a task has completed (success or failure).

        Args:
            task_id: Task identifier.
            lane: Lane the task was running in.
        """
        with self._lock:
            state = self._get_lane(lane)
            state.active_count = max(0, state.active_count - 1)
            self._total_active = max(0, self._total_active - 1)
            state.retry_counts.pop(task_id, None)

    def task_queued(self, task_id: str, lane: str) -> None:
        """Record that a task has been queued (waiting for WIP slot)."""
        with self._lock:
            state = self._get_lane(lane)
            state.queued_count += 1

    def task_dequeued(self, task_id: str, lane: str) -> None:
        """Record that a queued task has been picked up."""
        with self._lock:
            state = self._get_lane(lane)
            state.queued_count = max(0, state.queued_count - 1)

    def can_retry(self, task_id: str, lane: str) -> RetryDecision:
        """Check if a task can be retried within its lane's budget.

        Args:
            task_id: Task identifier.
            lane: Lane.

        Returns:
            A :class:`RetryDecision`.
        """
        with self._lock:
            state = self._get_lane(lane)
            current = state.retry_counts.get(task_id, 0)
            max_retries = state.retry_budget

            if current >= max_retries:
                if task_id not in state.exhausted_tasks:
                    state.exhausted_tasks.append(task_id)
                return RetryDecision(
                    task_id=task_id,
                    lane=lane,
                    allowed=False,
                    attempt=current + 1,
                    max_attempts=max_retries,
                    reason=f"Retry budget exhausted ({current}/{max_retries}); escalate to human review",
                )

            return RetryDecision(
                task_id=task_id,
                lane=lane,
                allowed=True,
                attempt=current + 1,
                max_attempts=max_retries,
            )

    def record_retry(self, task_id: str, lane: str) -> int:
        """Increment the retry count for a task.

        Returns the new retry count.
        """
        with self._lock:
            state = self._get_lane(lane)
            state.retry_counts[task_id] = state.retry_counts.get(task_id, 0) + 1
            return state.retry_counts[task_id]

    def update_wip_limit(self, lane: str, limit: int) -> None:
        """Update WIP limit for a lane."""
        with self._lock:
            self._wip_limits[lane] = limit
            state = self._get_lane(lane)
            state.wip_limit = limit
            logger.info("WIP limit updated: %s = %d", lane, limit)

    def update_retry_budget(self, lane: str, budget: int) -> None:
        """Update retry budget for a lane."""
        with self._lock:
            self._retry_budgets[lane] = budget
            state = self._get_lane(lane)
            state.retry_budget = budget
            logger.info("Retry budget updated: %s = %d", lane, budget)

    def update_global_wip(self, limit: int) -> None:
        """Update global WIP limit."""
        with self._lock:
            self._global_wip = limit
            logger.info("Global WIP limit updated: %d", limit)

    def get_lane_state(self, lane: str) -> LaneQueueState:
        """Get state for a specific lane."""
        with self._lock:
            return self._get_lane(lane)

    def get_summary(self) -> QueueSummary:
        """Get summary of queue state across all lanes."""
        with self._lock:
            total_queued = sum(s.queued_count for s in self._lanes.values())
            total_exhausted = sum(len(s.exhausted_tasks) for s in self._lanes.values())
            util = self._total_active / self._global_wip if self._global_wip > 0 else 0.0

            return QueueSummary(
                total_active=self._total_active,
                total_queued=total_queued,
                total_exhausted=total_exhausted,
                global_wip_limit=self._global_wip,
                global_utilization=util,
                lane_states=tuple(s.to_dict() for s in self._lanes.values()),
            )

    def reset(self) -> None:
        """Reset all state. For testing."""
        with self._lock:
            self._wip_limits = dict(DEFAULT_WIP_LIMITS)
            self._retry_budgets = dict(DEFAULT_RETRY_BUDGETS)
            self._global_wip = DEFAULT_GLOBAL_WIP
            self._lanes.clear()
            self._total_active = 0
            logger.info("QueueOptimizer reset")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: QueueOptimizer | None = None
_instance_lock = RLock()


def get_queue_optimizer() -> QueueOptimizer:
    """Get the shared QueueOptimizer singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = QueueOptimizer()
        return _instance


def reset_queue_optimizer() -> None:
    """Reset the singleton (for tests)."""
    global _instance
    with _instance_lock:
        if _instance is not None:
            _instance.reset()
        _instance = None
