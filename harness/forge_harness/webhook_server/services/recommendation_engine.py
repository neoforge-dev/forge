"""Recommendation Engine Service — DF-3001
==========================================

Replaces static recommendation profiles with a live agent registry lookup
combined with historical task-outcome data to produce ranked agent
recommendations for every incoming task.

Architecture
------------
The engine is a singleton that wraps the existing
:class:`~forge_harness.webhook_server.services.load_balancer.LoadBalancer`
registry.  It **does not duplicate** agent registration — it delegates to
the LoadBalancer for the live capability snapshot and overlays its own
historical statistics on top.

Scoring model
-------------
Each eligible agent receives a composite score from four components:

+----------------------+--------+--------------------------------------------+
| Component            | Weight | Source                                     |
+======================+========+============================================+
| capability_match     | 30 %   | 1.0 when agent supports the resolved lane  |
+----------------------+--------+--------------------------------------------+
| historical_pass_rate | 25 %   | recorded pass_rate or 0.5 (neutral prior)  |
+----------------------+--------+--------------------------------------------+
| current_load         | 25 %   | 1 - current_active / max_concurrent        |
+----------------------+--------+--------------------------------------------+
| context_budget       | 20 %   | context_budget_remaining_pct / 100         |
+----------------------+--------+--------------------------------------------+

Weights are adjusted per :class:`RecommendationStrategy`:

- ``capability_match`` — capability_match weight doubled (60 %), rest halved.
- ``historical_performance`` — historical_pass_rate weight doubled (50 %),
  current_load and context_budget reduced.
- ``balanced`` — default weights above.

Confidence
----------
``confidence = top_score - second_score`` (score spread).  When only one
eligible agent exists, ``confidence = top_score``.  When no agents are
eligible, ``confidence = 0.0``.

Thread safety
-------------
All mutable state (historical stats, recommendation log) is guarded by a
single :class:`threading.RLock`.

Persistence
-----------
Recommendations and outcomes are appended to a JSONL file at
``.forge/recommendations/history.jsonl`` (relative to the current working
directory) so they survive process restarts.

Singleton pattern
-----------------
Use :func:`get_recommendation_engine` to obtain the shared instance.  In
tests call :func:`reset_recommendation_engine` before each test to start from
a clean slate.

Usage
-----
::

    from forge_harness.webhook_server.services.recommendation_engine import (
        get_recommendation_engine,
    )
    from forge_harness.webhook_server.models.lane_policy import TaskType, RiskTier

    engine = get_recommendation_engine()
    rec = engine.recommend("task-001", TaskType.bug_fix, RiskTier.low)
    # rec.recommended_agents[0].agent_id → best available agent

    engine.record_outcome("task-001", "forge:nova", passed=True, lead_time_seconds=42.5)

Reference
---------
forge_harness/webhook_server/services/load_balancer.py  (LoadBalancer)
forge_harness/webhook_server/services/lane_enforcer.py  (LaneEnforcer)
forge_harness/webhook_server/models/recommendation.py   (models)
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-3001)
"""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.agent_load import AgentCapability
from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
from forge_harness.webhook_server.models.recommendation import (
    AgentScore,
    RecommendationStrategy,
    TaskRecommendation,
)
from forge_harness.webhook_server.models.work_cell import LaneResolver, WorkCellLane
from forge_harness.webhook_server.services.load_balancer import (
    LoadBalancer,
    get_load_balancer,
)
from forge_harness.webhook_server.services.path_lock_registry import (
    PathLockRegistry,
    get_path_lock_registry,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants — scoring weights (balanced strategy defaults)
# ---------------------------------------------------------------------------

#: Factor weights for the ``balanced`` strategy.  Must sum to 1.0.
_W_CAPABILITY_MATCH: float = 0.30
_W_HISTORICAL_PASS_RATE: float = 0.25
_W_CURRENT_LOAD: float = 0.25
_W_CONTEXT_BUDGET: float = 0.20

#: Neutral historical prior used when an agent has no recorded outcomes yet.
_DEFAULT_HISTORICAL_PASS_RATE: float = 0.5

#: Maximum entries kept in the in-memory recommendation log.
_MAX_LOG_ENTRIES: int = 500

#: JSONL persistence path (relative to cwd at engine creation time).
_HISTORY_JSONL_PATH: str = ".forge/recommendations/history.jsonl"


# ---------------------------------------------------------------------------
# Strategy weight sets
# ---------------------------------------------------------------------------

#: ``(capability, historical, load, budget)`` weight tuples per strategy.
_STRATEGY_WEIGHTS: dict[RecommendationStrategy, tuple[float, float, float, float]] = {
    RecommendationStrategy.balanced: (
        _W_CAPABILITY_MATCH,
        _W_HISTORICAL_PASS_RATE,
        _W_CURRENT_LOAD,
        _W_CONTEXT_BUDGET,
    ),
    RecommendationStrategy.capability_match: (0.60, 0.125, 0.125, 0.15),
    RecommendationStrategy.historical_performance: (0.15, 0.50, 0.20, 0.15),
}


# ---------------------------------------------------------------------------
# Internal history record
# ---------------------------------------------------------------------------


class _AgentHistory:
    """Mutable running statistics for a single agent.

    Attributes:
        total_tasks:    Total recorded outcomes.
        passed_tasks:   Outcomes where ``passed=True``.
        total_lead_time: Cumulative lead time in seconds.
    """

    __slots__ = ("total_tasks", "passed_tasks", "total_lead_time")

    def __init__(self) -> None:
        self.total_tasks: int = 0
        self.passed_tasks: int = 0
        self.total_lead_time: float = 0.0

    @property
    def pass_rate(self) -> float:
        """Return pass rate in ``[0.0, 1.0]`` or the neutral prior."""
        if self.total_tasks == 0:
            return _DEFAULT_HISTORICAL_PASS_RATE
        return self.passed_tasks / self.total_tasks

    @property
    def avg_lead_time_seconds(self) -> float | None:
        """Return average lead time in seconds, or ``None`` when no data."""
        if self.total_tasks == 0:
            return None
        return self.total_lead_time / self.total_tasks

    def record(self, passed: bool, lead_time_seconds: float) -> None:
        """Append a new outcome."""
        self.total_tasks += 1
        if passed:
            self.passed_tasks += 1
        self.total_lead_time += lead_time_seconds


# ---------------------------------------------------------------------------
# RecommendationEngine
# ---------------------------------------------------------------------------


class RecommendationEngine:
    """Scores agents for tasks using live capabilities and historical outcomes.

    Parameters
    ----------
    load_balancer
        Optional :class:`LoadBalancer` to use.  Defaults to the global
        singleton from :func:`get_load_balancer`.  Pass an explicit instance
        in tests to isolate state.
    history_path
        Path to the JSONL persistence file.  Defaults to
        ``.forge/recommendations/history.jsonl``.

    Attributes
    ----------
    _lock
        Re-entrant lock guarding all mutable state.
    _lb
        Reference to the load balancer providing the live agent registry.
    _resolver
        Stateless lane resolver for (TaskType, RiskTier) → WorkCellLane.
    _history
        Per-agent running statistics.  Populated by :meth:`record_outcome`
        and consulted during scoring.
    _log
        Bounded in-memory log of recent :class:`TaskRecommendation` objects.
    _history_path
        Path to the JSONL persistence file.
    """

    def __init__(
        self,
        load_balancer: LoadBalancer | None = None,
        history_path: str | Path = _HISTORY_JSONL_PATH,
        path_lock_registry: PathLockRegistry | None = None,
    ) -> None:
        self._lock: RLock = RLock()
        self._lb: LoadBalancer = load_balancer if load_balancer is not None else get_load_balancer()
        self._resolver: LaneResolver = LaneResolver()
        self._history: dict[str, _AgentHistory] = {}
        self._log: deque[TaskRecommendation] = deque(maxlen=_MAX_LOG_ENTRIES)
        self._history_path: Path = Path(history_path)
        self._plr: PathLockRegistry = (
            path_lock_registry if path_lock_registry is not None
            else get_path_lock_registry()
        )

        self._load_history()
        logger.info("RecommendationEngine initialised (history_path=%s)", self._history_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def recommend(
        self,
        task_id: str,
        task_type: TaskType,
        risk_tier: RiskTier,
        preferred_lane: WorkCellLane | None = None,
        strategy: RecommendationStrategy = RecommendationStrategy.balanced,
        task_paths: list[str] | None = None,
    ) -> TaskRecommendation:
        """Return a ranked list of agent recommendations for a task.

        Steps:

        1. Resolve the target :class:`WorkCellLane` from ``(task_type, risk_tier)``
           unless ``preferred_lane`` is supplied.
        2. Query the :class:`LoadBalancer` for all registered agents.
        3. Filter to agents whose ``supported_lanes`` includes the target lane.
        3b. When *task_paths* is provided, also filter out any agent that holds
            a conflicting path lock on one or more of those paths.
        4. Score each eligible agent using the composite formula.
        5. Sort by score descending and compute confidence.
        6. Persist the recommendation and return it.

        Args:
            task_id:        Unique task identifier.
            task_type:      Canonical task type (used for lane resolution).
            risk_tier:      Risk tier (used for lane resolution).
            preferred_lane: Override the resolved lane.  When supplied, lane
                            resolution is skipped and this value is used as the
                            target lane for capability filtering.
            strategy:       Scoring strategy.  Defaults to ``balanced``.
            task_paths:     Optional list of file-system or logical resource
                            paths the task intends to work with.  When
                            provided, agents that hold conflicting path locks
                            on any of these paths are excluded from the
                            candidate list before scoring.

        Returns:
            A :class:`TaskRecommendation` with the ranked agent list.
        """
        reasoning: list[str] = []

        # Step 1 — lane resolution
        if preferred_lane is not None:
            target_lane = preferred_lane
            reasoning.append(f"Lane overridden by caller: {target_lane.value}")
        else:
            target_lane = self._resolver.resolve(task_type, risk_tier)
            reasoning.append(
                f"Lane resolved from task_type={task_type.value} risk_tier={risk_tier.value}"
                f" → {target_lane.value}"
            )

        # Step 2 — get all registered agents from LoadBalancer
        all_agents = self._lb.get_agent_stats()

        if not all_agents:
            reasoning.append("No agents registered in LoadBalancer.")
            rec = TaskRecommendation(
                task_id=task_id,
                recommended_agents=[],
                confidence=0.0,
                reasoning=reasoning,
            )
            self._append_log(rec)
            return rec

        # Step 3 — filter by lane compatibility
        eligible = {
            agent_id: cap
            for agent_id, cap in all_agents.items()
            if target_lane in cap.supported_lanes
        }

        reasoning.append(
            f"Eligible agents for lane={target_lane.value}: {len(eligible)}/{len(all_agents)}"
        )

        if not eligible:
            reasoning.append(
                f"No agents support lane={target_lane.value}; returning empty recommendation."
            )
            rec = TaskRecommendation(
                task_id=task_id,
                recommended_agents=[],
                confidence=0.0,
                reasoning=reasoning,
            )
            self._append_log(rec)
            return rec

        # Step 3b — filter out agents with conflicting path locks
        if task_paths:
            eligible = self._filter_by_path_locks(eligible, task_paths, reasoning)

            if not eligible:
                reasoning.append(
                    "All lane-eligible agents hold conflicting path locks; "
                    "returning empty recommendation."
                )
                rec = TaskRecommendation(
                    task_id=task_id,
                    recommended_agents=[],
                    confidence=0.0,
                    reasoning=reasoning,
                )
                self._append_log(rec)
                return rec

        # Step 4 — score each eligible agent
        weights = _STRATEGY_WEIGHTS[strategy]
        scored: list[AgentScore] = []

        with self._lock:
            history_snapshot = {aid: hist for aid, hist in self._history.items()}

        for agent_id, cap in eligible.items():
            hist = history_snapshot.get(agent_id)
            pass_rate = hist.pass_rate if hist else _DEFAULT_HISTORICAL_PASS_RATE
            avg_lt = hist.avg_lead_time_seconds if hist else None

            # Individual factor scores
            f_capability = 1.0  # agent already passed the lane filter
            f_historical = pass_rate
            f_load = max(1.0 - cap.current_active / cap.max_concurrent, 0.0)
            f_budget = cap.context_budget_remaining_pct / 100.0

            w_cap, w_hist, w_load, w_budget = weights
            composite = round(
                w_cap * f_capability
                + w_hist * f_historical
                + w_load * f_load
                + w_budget * f_budget,
                6,
            )
            composite = min(max(composite, 0.0), 1.0)

            scored.append(
                AgentScore(
                    agent_id=agent_id,
                    score=composite,
                    factors={
                        "capability_match": round(f_capability, 4),
                        "historical_pass_rate": round(f_historical, 4),
                        "current_load": round(f_load, 4),
                        "context_budget": round(f_budget, 4),
                    },
                    historical_pass_rate=pass_rate if hist else None,
                    avg_lead_time_seconds=avg_lt,
                )
            )

        # Step 5 — sort descending; tie-break deterministically by agent_id
        scored.sort(key=lambda s: (-s.score, s.agent_id))

        # Confidence = score spread between top and second
        if len(scored) >= 2:
            confidence = round(scored[0].score - scored[1].score, 6)
        elif len(scored) == 1:
            confidence = round(scored[0].score, 6)
        else:
            confidence = 0.0

        confidence = min(max(confidence, 0.0), 1.0)

        top = scored[0]
        reasoning.append(
            f"Top agent: {top.agent_id} score={top.score:.4f} "
            f"(capability={top.factors.get('capability_match', 0):.3f}, "
            f"historical={top.factors.get('historical_pass_rate', 0):.3f}, "
            f"load={top.factors.get('current_load', 0):.3f}, "
            f"budget={top.factors.get('context_budget', 0):.3f})"
        )
        reasoning.append(f"Confidence (score spread): {confidence:.4f}")

        rec = TaskRecommendation(
            task_id=task_id,
            recommended_agents=scored,
            confidence=confidence,
            reasoning=reasoning,
        )

        self._append_log(rec)

        logger.info(
            "RecommendationEngine.recommend: task=%s lane=%s top_agent=%s score=%.4f confidence=%.4f",
            task_id,
            target_lane.value,
            top.agent_id,
            top.score,
            confidence,
        )
        return rec

    def record_outcome(
        self,
        task_id: str,
        agent_id: str,
        passed: bool,
        lead_time_seconds: float,
    ) -> None:
        """Update the historical statistics for an agent after a task completes.

        Args:
            task_id:             Identifier of the completed task (used for logging).
            agent_id:            Agent that executed the task.
            passed:              ``True`` when the task completed successfully.
            lead_time_seconds:   Wall-clock duration from task start to finish.

        Raises:
            ValueError: If ``lead_time_seconds`` is negative.
        """
        if lead_time_seconds < 0:
            raise ValueError(
                f"lead_time_seconds must be >= 0, got {lead_time_seconds}"
            )

        with self._lock:
            if agent_id not in self._history:
                self._history[agent_id] = _AgentHistory()
            self._history[agent_id].record(passed, lead_time_seconds)

        self._persist_outcome(task_id, agent_id, passed, lead_time_seconds)

        logger.debug(
            "RecommendationEngine.record_outcome: task=%s agent=%s passed=%s lead_time=%.2fs",
            task_id,
            agent_id,
            passed,
            lead_time_seconds,
        )

    def get_agent_history(self, agent_id: str) -> dict[str, Any]:
        """Return historical statistics for a single agent.

        Args:
            agent_id: The agent to query.

        Returns:
            Dict with keys:
            - ``pass_rate`` (float): historical pass rate in [0, 1]
            - ``avg_lead_time`` (float | None): average lead time in seconds
            - ``total_tasks`` (int): total outcomes recorded
        """
        with self._lock:
            hist = self._history.get(agent_id)
            if hist is None:
                return {
                    "pass_rate": _DEFAULT_HISTORICAL_PASS_RATE,
                    "avg_lead_time": None,
                    "total_tasks": 0,
                }
            return {
                "pass_rate": hist.pass_rate,
                "avg_lead_time": hist.avg_lead_time_seconds,
                "total_tasks": hist.total_tasks,
            }

    def get_recommendation_log(self, limit: int = 20) -> list[TaskRecommendation]:
        """Return the most recent task recommendations.

        Args:
            limit: Maximum number of entries to return.  Defaults to 20.

        Returns:
            List of :class:`TaskRecommendation` in reverse-chronological order
            (most recent first), capped at *limit*.
        """
        with self._lock:
            entries = list(self._log)
        # Deque is in append order (oldest first); reverse for recency.
        entries.reverse()
        return entries[:limit]

    # ------------------------------------------------------------------
    # Path lock filtering
    # ------------------------------------------------------------------

    def _filter_by_path_locks(
        self,
        eligible: dict[str, AgentCapability],
        task_paths: list[str],
        reasoning: list[str],
    ) -> dict[str, AgentCapability]:
        """Remove agents from *eligible* that hold conflicting path locks.

        For each path in *task_paths*, the :class:`PathLockRegistry` is
        consulted.  Any agent that holds a lock that would conflict with a new
        exclusive acquisition on that path is excluded.  A reasoning entry is
        added for each excluded agent.

        Args:
            eligible:   Dict of ``agent_id`` → :class:`AgentCapability` that
                        passed the lane filter.
            task_paths: Paths the task intends to work with.
            reasoning:  Mutable list of reasoning strings to append to.

        Returns:
            Filtered ``eligible`` dict (may be empty).
        """
        locked_out: dict[str, list[str]] = {}  # agent_id → [reason, ...]

        for path in task_paths:
            conflicts = self._plr.check_conflicts(path)
            for conflict in conflicts:
                conflicting_agent = conflict.agent_id
                if conflicting_agent in eligible:
                    if conflicting_agent not in locked_out:
                        locked_out[conflicting_agent] = []
                    locked_out[conflicting_agent].append(
                        f"holds conflicting lock on {path} ({conflict.conflict_reason})"
                    )

        if locked_out:
            for agent_id, reasons in locked_out.items():
                detail = "; ".join(reasons)
                reasoning.append(
                    f"{agent_id} excluded: {detail}"
                )
                logger.debug(
                    "RecommendationEngine._filter_by_path_locks: "
                    "excluded agent=%s reasons=%s",
                    agent_id,
                    reasons,
                )

            eligible = {
                agent_id: cap
                for agent_id, cap in eligible.items()
                if agent_id not in locked_out
            }
            reasoning.append(
                f"After path-lock filter: {len(eligible)} eligible agent(s) remain."
            )
        else:
            reasoning.append(
                f"Path-lock filter: no conflicts found for {len(task_paths)} path(s)."
            )

        return eligible

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_history(self) -> None:
        """Reload per-agent history from the JSONL persistence file.

        Each line is expected to be a JSON object with keys:
        ``agent_id``, ``passed``, ``lead_time_seconds``.

        Missing or corrupted lines are silently skipped.
        """
        if not self._history_path.exists():
            logger.debug(
                "RecommendationEngine._load_history: no history file at %s",
                self._history_path,
            )
            return

        loaded = 0
        with self._history_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    # Only outcome records have agent_id + passed fields.
                    if "agent_id" not in record or "passed" not in record:
                        continue
                    agent_id = record["agent_id"]
                    passed = bool(record["passed"])
                    lead_time = float(record.get("lead_time_seconds", 0.0))
                    if agent_id not in self._history:
                        self._history[agent_id] = _AgentHistory()
                    self._history[agent_id].record(passed, lead_time)
                    loaded += 1
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    logger.debug(
                        "RecommendationEngine._load_history: skipping malformed line: %r",
                        line,
                    )

        logger.info(
            "RecommendationEngine._load_history: loaded %d outcome records", loaded
        )

    def _persist_outcome(
        self,
        task_id: str,
        agent_id: str,
        passed: bool,
        lead_time_seconds: float,
    ) -> None:
        """Append a single outcome record to the JSONL file.

        Creates parent directories if they do not exist.  Write errors are
        logged but never re-raised so that the main recommendation flow is
        not interrupted.

        Args:
            task_id:            Task identifier.
            agent_id:           Agent identifier.
            passed:             Whether the task passed.
            lead_time_seconds:  Lead time in seconds.
        """
        record = {
            "type": "outcome",
            "task_id": task_id,
            "agent_id": agent_id,
            "passed": passed,
            "lead_time_seconds": lead_time_seconds,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            with self._history_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.warning(
                "RecommendationEngine._persist_outcome: write failed: %s", exc
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_log(self, rec: TaskRecommendation) -> None:
        """Append *rec* to the in-memory log under the lock."""
        with self._lock:
            self._log.append(rec)


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_engine: RecommendationEngine | None = None
_engine_lock: RLock = RLock()


def get_recommendation_engine() -> RecommendationEngine:
    """Return the global :class:`RecommendationEngine` singleton.

    The instance is created lazily on the first call and reused on all
    subsequent calls.  Thread-safe.

    To replace the singleton (e.g. in tests) call
    :func:`reset_recommendation_engine` first.

    Returns:
        The singleton :class:`RecommendationEngine`.
    """
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = RecommendationEngine()
            logger.info("RecommendationEngine singleton created")
        return _engine


def reset_recommendation_engine() -> None:
    """Destroy the global singleton — intended for use in tests only.

    After calling this function the next call to
    :func:`get_recommendation_engine` will create a fresh instance.
    """
    global _engine
    with _engine_lock:
        _engine = None
        logger.debug("RecommendationEngine singleton reset")
