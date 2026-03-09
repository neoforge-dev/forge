"""Scheduler Policy — CP-2004
==============================

Recommends task-to-node assignments based on node load (from
:class:`~forge_harness.webhook_server.services.heartbeat_store.HeartbeatStore`),
active lease state (path locks), and task-type affinity.

Scoring model
-------------

Each candidate node receives a composite score in **[0.0, 1.0]** built from
four weighted components:

+----------------+--------+------------------------------------------------------+
| Component      | Weight | Source                                               |
+================+========+======================================================+
| availability   | 40 %   | ``HeartbeatRecord.availability_score()``             |
+----------------+--------+------------------------------------------------------+
| capacity       | 30 %   | Available agent slots (max 8 per node, capped 0-1)   |
+----------------+--------+------------------------------------------------------+
| affinity       | 20 %   | Known task-type capability match on the node         |
+----------------+--------+------------------------------------------------------+
| path_safety    | 10 %   | 1.0 when no active leases hold ``path_lock``;        |
|                |        | 0.0 when any active lease conflicts with the request |
+----------------+--------+------------------------------------------------------+

Usage
-----

Inject the singleton::

    from forge_harness.webhook_server.services.scheduler_policy import (
        get_scheduler_policy,
    )

    policy = get_scheduler_policy(heartbeat_store=store)
    recs = policy.recommend(task_type="python", path_lock="src/api/routes.py")
    best = recs[0]  # SchedulerRecommendation with highest score

To replace the singleton in tests call :func:`reset_scheduler_policy` first.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field

from forge_harness.logging_config import get_logger
from forge_harness.models.lease import LeaseState, TaskLease
from forge_harness.webhook_server.services.heartbeat_store import HeartbeatStore

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum number of agent slots assumed per node when computing capacity score.
_MAX_AGENTS_PER_NODE: int = 8

#: Task types that map to known node-level capability keys (from nodes.py).
#: An affinity score of 1.0 is awarded when the node reports that capability.
#: A score of 0.5 is the baseline for unknown / unverifiable affinity.
_TASK_TYPE_CAPABILITY_MAP: dict[str, str] = {
    "python": "python",
    "ios": "ios_simulator",
    "docker": "docker",
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "kimi": "kimi",
    "opencode": "opencode",
    "ollama": "ollama",
}

#: Scoring weights — must sum to 1.0.
_W_AVAILABILITY: float = 0.40
_W_CAPACITY: float = 0.30
_W_AFFINITY: float = 0.20
_W_PATH_SAFETY: float = 0.10


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------


class SchedulerRecommendation(BaseModel):
    """A single node recommendation produced by :class:`SchedulerPolicy`.

    Fields
    ------
    node_id
        Unique node identifier (matches ``HeartbeatRecord.node_id``).
    score
        Composite recommendation score in ``[0.0, 1.0]``.  Higher is better.
    reason
        Human-readable explanation of the score breakdown.
    available_agents
        Estimated number of free agent slots on this node.
    path_conflicts
        List of ``task_id`` values whose active leases hold the requested
        ``path_lock``.  Empty when there is no path-lock request or no
        conflicts exist.
    """

    node_id: str = Field(..., description="Unique node identifier.")
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Composite recommendation score (higher is better).",
    )
    reason: str = Field(
        default="",
        description="Human-readable explanation of the score breakdown.",
    )
    available_agents: int = Field(
        default=0,
        ge=0,
        description="Estimated number of free agent slots on this node.",
    )
    path_conflicts: list[str] = Field(
        default_factory=list,
        description="task_id values whose leases conflict with the requested path_lock.",
    )


# ---------------------------------------------------------------------------
# Policy class
# ---------------------------------------------------------------------------


class SchedulerPolicy:
    """Stateless recommendation engine for task-to-node placement.

    Parameters
    ----------
    heartbeat_store:
        Live :class:`HeartbeatStore` instance; provides node telemetry and
        the scheduler view.
    lease_store:
        Optional list of :class:`~forge_harness.models.lease.TaskLease`
        instances **or** any object with a synchronous ``list_leases()`` method
        that returns ``list[TaskLease]``.  When ``None`` path-safety scoring
        degrades to a neutral 1.0 (no conflict data available).
    """

    def __init__(
        self,
        heartbeat_store: HeartbeatStore,
        lease_store: Any = None,
    ) -> None:
        self._hb_store = heartbeat_store
        self._lease_store = lease_store

        # Stats accumulators — guarded by _stats_lock
        self._stats_lock: Lock = Lock()
        self._total_recommendations: int = 0
        self._cache_hits: int = 0
        self._latency_samples: list[float] = []

        logger.info(
            "SchedulerPolicy initialised",
            extra={"has_lease_store": lease_store is not None},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_active_leases(self) -> list[TaskLease]:
        """Return all active leases from the lease store.

        Supports two lease-store shapes:
        - An iterable of :class:`TaskLease` objects directly.
        - An object with a synchronous ``list_leases()`` callable.

        Returns an empty list when the lease store is ``None`` or an
        error occurs during retrieval.
        """
        if self._lease_store is None:
            return []

        try:
            if callable(getattr(self._lease_store, "list_leases", None)):
                leases = self._lease_store.list_leases()
            else:
                leases = list(self._lease_store)
        except Exception as exc:
            logger.warning(
                "SchedulerPolicy: failed to read lease store: %s",
                exc,
                exc_info=True,
            )
            return []

        # Filter to leases that actually hold a path lock and are active
        active_states = {LeaseState.CLAIMED, LeaseState.ACTIVE, LeaseState.RENEWING}
        return [
            lease
            for lease in leases
            if (
                isinstance(lease, TaskLease)
                and lease.path_lock
                and lease.state in active_states
            )
        ]

    def _path_safety_score(
        self,
        node_id: str,
        path_lock: str | None,
        active_leases: list[TaskLease],
    ) -> tuple[float, list[str]]:
        """Return ``(score, conflicting_task_ids)`` for a node / path combination.

        Score is ``1.0`` when there are no conflicts (or no path requested).
        Score is ``0.0`` when one or more active leases on *node_id* hold the
        same *path_lock*.

        Args:
            node_id:       Node being evaluated.
            path_lock:     Requested path lock (``None`` means no lock needed).
            active_leases: Pre-fetched list of active leases from the store.

        Returns:
            Tuple of ``(score, conflicting_task_ids)``.
        """
        if not path_lock:
            return 1.0, []

        conflicts = [
            lease.task_id
            for lease in active_leases
            if lease.owner_node == node_id and lease.path_lock == path_lock
        ]
        score = 0.0 if conflicts else 1.0
        return score, conflicts

    @staticmethod
    def _affinity_score(task_type: str, node_entry: dict[str, Any]) -> float:
        """Return ``[0.0, 1.0]`` affinity score for a task type on a node.

        Uses the ``capabilities`` dict if present in *node_entry* (populated
        by ``nodes.py`` capability detection).  Falls back to a neutral ``0.5``
        when capability data is unavailable, and ``1.0`` for the ``"general"``
        task type (any node can handle it).

        Args:
            task_type:  Task type string (e.g. ``"python"``, ``"ios"``).
            node_entry: Ranked-node dict from the scheduler view.

        Returns:
            Affinity score in ``[0.0, 1.0]``.
        """
        if task_type == "general" or not task_type:
            return 1.0

        capabilities: dict[str, bool] = node_entry.get("capabilities", {})
        if not capabilities:
            # No capability data — neutral score
            return 0.5

        capability_key = _TASK_TYPE_CAPABILITY_MAP.get(task_type, task_type)
        if capabilities.get(capability_key, False):
            return 1.0
        # Capability explicitly absent
        return 0.0

    @staticmethod
    def _capacity_score(available_agents: int) -> float:
        """Return ``[0.0, 1.0]`` capacity score from available agent slots.

        Args:
            available_agents: Number of free agent slots (already clamped >= 0).

        Returns:
            Score in ``[0.0, 1.0]``.
        """
        return min(available_agents / _MAX_AGENTS_PER_NODE, 1.0)

    def _score_node(
        self,
        node_entry: dict[str, Any],
        task_type: str,
        path_lock: str | None,
        active_leases: list[TaskLease],
    ) -> tuple[float, str, int, list[str]]:
        """Compute the composite score for a single node.

        Args:
            node_entry:    Ranked-node dict from the scheduler view.
            task_type:     Task type hint.
            path_lock:     Requested exclusive path lock (or ``None``).
            active_leases: Pre-fetched active leases for path-safety scoring.

        Returns:
            Tuple of ``(composite_score, reason_string, available_agents, path_conflicts)``.
        """
        node_id: str = node_entry["node_id"]
        avail_score: float = node_entry.get("availability_score", 0.0)
        available_agents: int = max(
            _MAX_AGENTS_PER_NODE - node_entry.get("active_agents", 0), 0
        )

        cap_score = self._capacity_score(available_agents)
        aff_score = self._affinity_score(task_type, node_entry)
        safety_score, conflicts = self._path_safety_score(node_id, path_lock, active_leases)

        composite = round(
            _W_AVAILABILITY * avail_score
            + _W_CAPACITY * cap_score
            + _W_AFFINITY * aff_score
            + _W_PATH_SAFETY * safety_score,
            4,
        )

        reason = (
            f"availability={avail_score:.3f}({_W_AVAILABILITY*100:.0f}%) "
            f"capacity={cap_score:.3f}({_W_CAPACITY*100:.0f}%) "
            f"affinity={aff_score:.3f}({_W_AFFINITY*100:.0f}%) "
            f"path_safety={safety_score:.3f}({_W_PATH_SAFETY*100:.0f}%)"
        )
        if conflicts:
            reason += f" [PATH CONFLICT: {', '.join(conflicts)}]"

        return composite, reason, available_agents, conflicts

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def recommend(
        self,
        task_type: str = "general",
        path_lock: str | None = None,
        exclude_nodes: list[str] | None = None,
    ) -> list[SchedulerRecommendation]:
        """Return nodes ranked by suitability for the requested task.

        Only **fresh** (healthy) nodes are considered.  Stale nodes are
        excluded because the scheduler view's ``ranked_nodes`` list already
        filters them.

        Args:
            task_type:     Task type hint used for affinity scoring.
                           ``"general"`` (default) grants every node a full
                           affinity score.
            path_lock:     File path the task needs exclusive access to.
                           When provided nodes that already have an active
                           lease on this path receive a penalty.
            exclude_nodes: List of node IDs to unconditionally omit from
                           the candidate set.

        Returns:
            List of :class:`SchedulerRecommendation` sorted by
            ``score`` descending (best first).  Empty when no fresh nodes
            are available.
        """
        t0 = time.monotonic()
        excluded: set[str] = set(exclude_nodes or [])

        scheduler_view = self._hb_store.get_scheduler_view()
        ranked_nodes: list[dict[str, Any]] = scheduler_view.get("ranked_nodes", [])

        # Filter excluded nodes
        candidates = [n for n in ranked_nodes if n["node_id"] not in excluded]

        if not candidates:
            logger.debug(
                "SchedulerPolicy.recommend: no candidates after filtering",
                extra={
                    "task_type": task_type,
                    "path_lock": path_lock,
                    "excluded": list(excluded),
                },
            )
            self._record_stats(t0)
            return []

        active_leases = self._collect_active_leases()

        recommendations: list[SchedulerRecommendation] = []
        for node_entry in candidates:
            composite, reason, available_agents, conflicts = self._score_node(
                node_entry, task_type, path_lock, active_leases
            )
            recommendations.append(
                SchedulerRecommendation(
                    node_id=node_entry["node_id"],
                    score=composite,
                    reason=reason,
                    available_agents=available_agents,
                    path_conflicts=conflicts,
                )
            )

        # Sort by score descending; tie-break by node_id for determinism
        recommendations.sort(key=lambda r: (-r.score, r.node_id))

        elapsed_ms = (time.monotonic() - t0) * 1000.0
        self._record_stats(t0)

        logger.info(
            "SchedulerPolicy.recommend: %d candidates → top=%s score=%.4f (%.1fms)",
            len(recommendations),
            recommendations[0].node_id if recommendations else "none",
            recommendations[0].score if recommendations else 0.0,
            elapsed_ms,
            extra={
                "task_type": task_type,
                "path_lock": path_lock,
                "candidate_count": len(recommendations),
            },
        )
        return recommendations

    def get_stats(self) -> dict[str, Any]:
        """Return a snapshot of scheduler performance metrics.

        Returns
        -------
        dict with keys:

        ``total_recommendations``
            Total number of :meth:`recommend` calls completed.
        ``cache_hits``
            Placeholder counter for future cache layer integration.
        ``avg_latency_ms``
            Rolling average recommendation latency in milliseconds, or
            ``None`` when no calls have completed yet.
        """
        with self._stats_lock:
            avg = (
                round(sum(self._latency_samples) / len(self._latency_samples), 3)
                if self._latency_samples
                else None
            )
            return {
                "total_recommendations": self._total_recommendations,
                "cache_hits": self._cache_hits,
                "avg_latency_ms": avg,
            }

    # ------------------------------------------------------------------
    # Internal stat tracking
    # ------------------------------------------------------------------

    def _record_stats(self, t0: float) -> None:
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        with self._stats_lock:
            self._total_recommendations += 1
            self._latency_samples.append(elapsed_ms)


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_policy_instance: SchedulerPolicy | None = None
_policy_lock: Lock = Lock()


def get_scheduler_policy(
    heartbeat_store: HeartbeatStore | None = None,
    lease_store: Any = None,
) -> SchedulerPolicy:
    """Return the global :class:`SchedulerPolicy` singleton.

    On the first call both *heartbeat_store* and *lease_store* are honoured;
    subsequent calls return the cached instance and ignore the arguments.

    When *heartbeat_store* is ``None`` on the first call the function imports
    and uses :func:`~forge_harness.webhook_server.services.heartbeat_store.get_heartbeat_store`
    to obtain the default store.

    To replace the singleton (e.g. in tests) call :func:`reset_scheduler_policy`
    first.

    Args:
        heartbeat_store: Override the heartbeat store.  Only honoured on the
                         first call.
        lease_store:     Optional lease store.  Only honoured on the first call.

    Returns:
        The singleton :class:`SchedulerPolicy`.
    """
    global _policy_instance
    with _policy_lock:
        if _policy_instance is None:
            if heartbeat_store is None:
                from forge_harness.webhook_server.services.heartbeat_store import (
                    get_heartbeat_store,
                )

                heartbeat_store = get_heartbeat_store()
            _policy_instance = SchedulerPolicy(
                heartbeat_store=heartbeat_store,
                lease_store=lease_store,
            )
        return _policy_instance


def reset_scheduler_policy() -> None:
    """Reset the global singleton — intended for use in tests only."""
    global _policy_instance
    with _policy_lock:
        _policy_instance = None


# ---------------------------------------------------------------------------
# Weekly performance report trigger (DF-5003)
# ---------------------------------------------------------------------------


async def run_weekly_performance_report_trigger() -> None:
    """Run the PerformanceReporter on a Sunday-midnight-aligned weekly schedule.

    This coroutine is the canonical DF-5003 scheduler hook.  It delegates to
    :meth:`PerformanceReporter.schedule_weekly_aligned` which computes the
    delay until the next Sunday 00:00:00 UTC before each run.

    Intended to be started as a long-running asyncio background task during
    server startup::

        asyncio.create_task(run_weekly_performance_report_trigger())

    Raises:
        RuntimeError: If no PerformanceReporter singleton has been initialised
            before this function is called.
    """
    from forge_harness.webhook_server.services.performance_reporter import (
        get_performance_reporter,
    )

    reporter = get_performance_reporter()
    logger.info("SchedulerPolicy: starting weekly performance report trigger (DF-5003)")
    await reporter.schedule_weekly_aligned()
