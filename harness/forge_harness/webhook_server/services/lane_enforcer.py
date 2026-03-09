"""Lane Enforcer Service — DF-2003
===================================

Enforces the work-cell lane taxonomy at runtime.  Every queued task must be
assigned to exactly one WorkCellLane before it can be executed; the enforcer
records that assignment, tracks WIP per lane, and exposes per-lane statistics
for the scheduler and dashboard.

Thread Safety
-------------
All mutable state is guarded by a single :class:`threading.RLock`.  The
RLock (re-entrant) is chosen deliberately: :meth:`assign_lane` calls
:meth:`check_wip` internally, so the same thread may acquire the lock twice
in the same call stack.

Singleton Pattern
-----------------
Use :func:`get_lane_enforcer` to obtain the shared instance.  In tests call
:func:`reset_lane_enforcer` before each test to start from a clean slate.

Usage
-----
::

    from forge_harness.webhook_server.services.lane_enforcer import (
        get_lane_enforcer,
    )

    enforcer = get_lane_enforcer()
    lane = enforcer.assign_lane("task-001", TaskType.bug_fix, RiskTier.low)
    # WorkCellLane.api_simple

    has_capacity = enforcer.check_wip(lane)
    # True (assuming WIP < max_wip)

    stats = enforcer.get_lane_stats()
    # {"api_simple": {"active": 1, "queued": 0, "completed": 0, "max_wip": 10}, ...}

Reference
---------
forge_harness/webhook_server/models/work_cell.py  (WorkCellLane, LaneResolver)
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-2003)
"""

from __future__ import annotations

from collections import defaultdict
from threading import RLock
from typing import Any

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.work_cell import (
    DEFAULT_LANE_CONFIGS,
    LaneResolver,
    WorkCellLane,
    get_lane_config,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# LaneEnforcer
# ---------------------------------------------------------------------------


class LaneEnforcer:
    """Runtime enforcer for the work-cell lane taxonomy.

    Responsibilities:
    - Assign each task to exactly one WorkCellLane via LaneResolver.
    - Record lane assignments indexed by task_id.
    - Track per-lane counters: active, queued, completed.
    - Enforce WIP limits: reject (return False from check_wip) when the lane
      is at capacity so callers can queue rather than execute immediately.

    Attributes:
        _resolver:     Stateless LaneResolver for deterministic mapping.
        _lock:         Re-entrant lock guarding all mutable state.
        _assignments:  Mapping of task_id → WorkCellLane.
        _active:       Per-lane count of tasks currently executing.
        _queued:       Per-lane count of tasks waiting for WIP capacity.
        _completed:    Per-lane count of tasks that have finished.
    """

    def __init__(self) -> None:
        self._resolver: LaneResolver = LaneResolver()
        self._lock: RLock = RLock()

        # task_id → assigned WorkCellLane
        self._assignments: dict[str, WorkCellLane] = {}

        # Per-lane counters — defaultdict so lanes without activity read as 0.
        self._active: dict[WorkCellLane, int] = defaultdict(int)
        self._queued: dict[WorkCellLane, int] = defaultdict(int)
        self._completed: dict[WorkCellLane, int] = defaultdict(int)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assign_lane(
        self,
        task_id: str,
        task_type: TaskType,
        risk_tier: RiskTier,
    ) -> WorkCellLane:
        """Resolve and record the lane assignment for a task.

        If the task has already been assigned a lane the existing assignment
        is returned without incrementing counters, making this call idempotent
        for repeated calls with the same task_id.

        When the resolved lane has available WIP capacity (active < max_wip)
        the task is counted as *active*.  Otherwise it is counted as *queued*
        until capacity frees up.

        Args:
            task_id:   Unique identifier of the task being assigned.
            task_type: Canonical type of work (from TaskType enum).
            risk_tier: Assessed risk level of the task (from RiskTier enum).

        Returns:
            The WorkCellLane assigned to this task.
        """
        with self._lock:
            # Idempotent: return existing assignment unchanged.
            if task_id in self._assignments:
                existing = self._assignments[task_id]
                logger.debug(
                    "LaneEnforcer.assign_lane: task %s already assigned to %s",
                    task_id,
                    existing.value,
                )
                return existing

            lane = self._resolver.resolve(task_type, risk_tier)
            self._assignments[task_id] = lane

            # Determine whether the task can start immediately or must queue.
            if self.check_wip(lane):
                self._active[lane] += 1
                status = "active"
            else:
                self._queued[lane] += 1
                status = "queued"

            logger.info(
                "LaneEnforcer.assign_lane: task=%s type=%s risk=%s → lane=%s status=%s",
                task_id,
                task_type.value,
                risk_tier.value,
                lane.value,
                status,
            )
            return lane

    def check_wip(self, lane: WorkCellLane) -> bool:
        """Return True if the lane has available WIP capacity.

        Capacity is available when the number of *active* tasks in the lane
        is strictly less than the lane's configured max_wip limit.

        Args:
            lane: The work-cell lane to check.

        Returns:
            True when active count < max_wip; False when the lane is full.
        """
        with self._lock:
            config = get_lane_config(lane)
            current_active = self._active[lane]
            has_capacity = current_active < config.max_wip
            logger.debug(
                "LaneEnforcer.check_wip: lane=%s active=%d max_wip=%d capacity=%s",
                lane.value,
                current_active,
                config.max_wip,
                has_capacity,
            )
            return has_capacity

    def complete_task(self, task_id: str) -> WorkCellLane | None:
        """Mark a task as completed and free its WIP slot.

        Moves the task from the *active* bucket to *completed*.  If there are
        queued tasks in the same lane, one queued task is promoted to active
        to fill the freed slot.

        Args:
            task_id: Unique identifier of the task that finished.

        Returns:
            The WorkCellLane the task was assigned to, or None when the
            task_id is unknown (e.g. already completed or never assigned).
        """
        with self._lock:
            lane = self._assignments.get(task_id)
            if lane is None:
                logger.warning(
                    "LaneEnforcer.complete_task: unknown task_id=%s", task_id
                )
                return None

            # Decrement active count (clamp at 0 for safety).
            if self._active[lane] > 0:
                self._active[lane] -= 1
            self._completed[lane] += 1

            # Promote one queued task to active if capacity is now available.
            if self._queued[lane] > 0 and self.check_wip(lane):
                self._queued[lane] -= 1
                self._active[lane] += 1
                logger.debug(
                    "LaneEnforcer.complete_task: promoted queued task in lane=%s",
                    lane.value,
                )

            logger.info(
                "LaneEnforcer.complete_task: task=%s lane=%s completed",
                task_id,
                lane.value,
            )
            return lane

    def get_lane_stats(self) -> dict[str, dict[str, Any]]:
        """Return a snapshot of per-lane active/queued/completed counts.

        The returned dict is keyed by lane value (str) for easy JSON
        serialisation.  Each entry includes the lane's max_wip limit from the
        default config for convenient dashboard rendering.

        Returns:
            Dict mapping lane value → stats dict with keys:
            ``active``, ``queued``, ``completed``, ``max_wip``.

        Example::

            {
                "api_simple":   {"active": 3, "queued": 0, "completed": 12, "max_wip": 10},
                "security_change": {"active": 1, "queued": 1, "completed": 4, "max_wip": 2},
                ...
            }
        """
        with self._lock:
            stats: dict[str, dict[str, Any]] = {}
            for lane in WorkCellLane:
                config = DEFAULT_LANE_CONFIGS[lane]
                stats[lane.value] = {
                    "active": self._active[lane],
                    "queued": self._queued[lane],
                    "completed": self._completed[lane],
                    "max_wip": config.max_wip,
                }
            return stats

    def get_assignment(self, task_id: str) -> WorkCellLane | None:
        """Return the lane a task was assigned to, or None if unknown.

        Args:
            task_id: The task identifier to look up.

        Returns:
            The assigned WorkCellLane or None.
        """
        with self._lock:
            return self._assignments.get(task_id)

    def reset(self) -> None:
        """Clear all state — intended for use in tests only.

        After calling reset() the enforcer behaves as if freshly constructed.
        """
        with self._lock:
            self._assignments.clear()
            self._active.clear()
            self._queued.clear()
            self._completed.clear()


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_enforcer_instance: LaneEnforcer | None = None
_enforcer_lock: RLock = RLock()


def get_lane_enforcer() -> LaneEnforcer:
    """Return the global :class:`LaneEnforcer` singleton.

    The instance is created lazily on the first call and reused on all
    subsequent calls.  Thread-safe.

    To replace the singleton in tests call :func:`reset_lane_enforcer` first.

    Returns:
        The singleton :class:`LaneEnforcer`.
    """
    global _enforcer_instance
    with _enforcer_lock:
        if _enforcer_instance is None:
            _enforcer_instance = LaneEnforcer()
            logger.info("LaneEnforcer singleton created")
        return _enforcer_instance


def reset_lane_enforcer() -> None:
    """Destroy the global singleton — intended for use in tests only.

    After calling this function the next call to :func:`get_lane_enforcer`
    will create a fresh instance.
    """
    global _enforcer_instance
    with _enforcer_lock:
        _enforcer_instance = None
        logger.debug("LaneEnforcer singleton reset")
