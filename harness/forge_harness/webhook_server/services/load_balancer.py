"""Load Balancer Service — DF-2005
==================================

Distributes incoming tasks across eligible agents based on lane capabilities,
remaining context budget, instantaneous load, and node-level resource budgets.

Scoring model
-------------

Each candidate agent receives a composite score built from five components:

+-------------------+--------+---------------------------------------------------+
| Component         | Weight | Source                                            |
+===================+========+===================================================+
| capacity_score    | 35 %   | 1 - current_active / max_concurrent               |
+-------------------+--------+---------------------------------------------------+
| budget_score      | 25 %   | context_budget_remaining_pct / 100                |
+-------------------+--------+---------------------------------------------------+
| lane_affinity     | 20 %   | 1.0 if lane in supported_lanes, else 0.0          |
+-------------------+--------+---------------------------------------------------+
| freshness         | 10 %   | 1.0 if heartbeat age < 60 s, linear decay beyond  |
+-------------------+--------+---------------------------------------------------+
| node_score        | 10 %   | 1.0 at <=75% node utilisation, linear penalty     |
|                   |        | to 0.0 at 100%; agent ineligible when node full   |
+-------------------+--------+---------------------------------------------------+

Only agents whose ``supported_lanes`` contains the requested lane are included
in the candidate set (lane_affinity acts as a hard filter for eligibility).
Additionally, agents whose node has reached ``max_agents`` are excluded
entirely.  Agents on nodes above 75% utilisation receive a proportional penalty
on the ``node_score`` component.

Agents registered without a node (``node=""``) bypass all node-budget checks
and always remain eligible (backward-compatible behaviour).

Node budget registry
--------------------
:data:`DEFAULT_NODE_BUDGETS` lists the five known compute nodes with their
RAM and agent-count limits (sourced from CLAUDE.md).  Callers can update the
live agent count via :meth:`LoadBalancer.update_node_state` and retrieve
utilisation statistics via :meth:`LoadBalancer.get_node_stats`.

Round-robin strategy
--------------------
A per-lane counter tracks the last-selected position in the registered-agent
list.  Successive calls with the same lane cycle through agents regardless of
their load scores, providing even task distribution when all agents are
similarly capable.

Thread safety
-------------
All mutable state (agent registry, node budgets, round-robin counters) is
guarded by a single :class:`threading.RLock`.

Singleton pattern
-----------------
Use :func:`get_load_balancer` to obtain the shared instance.  In tests call
:func:`reset_load_balancer` before each test to start from a clean slate.

Usage
-----
::

    from forge_harness.webhook_server.services.load_balancer import (
        get_load_balancer,
    )
    from forge_harness.webhook_server.models.work_cell import WorkCellLane
    from forge_harness.webhook_server.models.agent_load import BalancingStrategy

    lb = get_load_balancer()
    lb.register_agent("forge:nova", [WorkCellLane.api_simple, WorkCellLane.docs],
                      node="nova")
    lb.update_agent_load("forge:nova", current_active=1, context_budget_pct=88.5)

    recs = lb.recommend(WorkCellLane.api_simple)
    best = recs[0]  # BalancingRecommendation with highest score

Reference
---------
forge_harness/webhook_server/models/agent_load.py  (models)
forge_harness/webhook_server/models/work_cell.py   (WorkCellLane)
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-2005)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.agent_load import (
    AgentCapability,
    BalancingRecommendation,
    BalancingStrategy,
)
from forge_harness.webhook_server.models.work_cell import WorkCellLane

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Heartbeat age below which freshness_score is 1.0 (fully fresh).
_FRESHNESS_FULL_SECONDS: float = 60.0

#: Heartbeat age at or beyond which freshness_score is 0.0 (stale).
_FRESHNESS_ZERO_SECONDS: float = 300.0

#: Scoring weights — must sum to 1.0.
_W_CAPACITY: float = 0.35
_W_BUDGET: float = 0.25
_W_AFFINITY: float = 0.20
_W_FRESHNESS: float = 0.10
_W_NODE: float = 0.10

#: Node utilisation above which a score penalty begins to apply.
_NODE_PENALTY_THRESHOLD: float = 0.75


# ---------------------------------------------------------------------------
# NodeBudget dataclass
# ---------------------------------------------------------------------------


@dataclass
class NodeBudget:
    """Resource budget for a compute node.

    Attributes:
        name:                Name of the node (e.g. ``"prya"``).
        max_agents:          Maximum number of agents allowed to run
                             simultaneously on this node.
        ram_gb:              Total RAM available on the node in gigabytes.
        current_agent_count: Live count of agents currently registered on
                             this node.  Updated via
                             :meth:`LoadBalancer.update_node_state` or
                             automatically when agents are registered /
                             deregistered with a matching ``node`` value.
    """

    name: str
    max_agents: int
    ram_gb: int
    current_agent_count: int = field(default=0)


#: Default node budgets sourced from CLAUDE.md infrastructure cheat-sheet.
DEFAULT_NODE_BUDGETS: dict[str, NodeBudget] = {
    "prya": NodeBudget(name="prya", max_agents=2, ram_gb=16),
    "sati": NodeBudget(name="sati", max_agents=6, ram_gb=64),
    "nova": NodeBudget(name="nova", max_agents=4, ram_gb=48),
    "vega": NodeBudget(name="vega", max_agents=2, ram_gb=16),
    "gaea": NodeBudget(name="gaea", max_agents=3, ram_gb=16),
}


# ---------------------------------------------------------------------------
# LoadBalancer class
# ---------------------------------------------------------------------------


class LoadBalancer:
    """Distributes tasks across registered agents based on lane and load.

    Parameters
    ----------
    None.  The balancer starts with an empty agent registry; call
    :meth:`register_agent` to add agents before calling :meth:`recommend`.

    Attributes
    ----------
    _lock
        Re-entrant lock guarding all mutable state.
    _agents
        Mapping of ``agent_id`` → :class:`AgentCapability`.
    _heartbeat_times
        Mapping of ``agent_id`` → last-update wall-clock time (from
        :func:`time.monotonic`).  Used to compute ``last_heartbeat_age_seconds``
        at score time rather than storing absolute timestamps.
    _rr_counters
        Per-lane round-robin position counter.  Maps
        ``WorkCellLane`` → ``int`` index into the sorted eligible-agent list.
    _node_budgets
        Mapping of node name → :class:`NodeBudget`.  Initialised from
        :data:`DEFAULT_NODE_BUDGETS`; live counts are updated automatically
        when agents are registered with a non-empty ``node`` value and can
        also be set explicitly via :meth:`update_node_state`.
    """

    def __init__(self) -> None:
        self._lock: RLock = RLock()
        self._agents: dict[str, AgentCapability] = {}
        self._heartbeat_times: dict[str, float] = {}
        self._rr_counters: dict[WorkCellLane, int] = {}
        # Deep-copy the defaults so each instance has its own mutable copy.
        self._node_budgets: dict[str, NodeBudget] = {
            name: NodeBudget(
                name=nb.name,
                max_agents=nb.max_agents,
                ram_gb=nb.ram_gb,
                current_agent_count=nb.current_agent_count,
            )
            for name, nb in DEFAULT_NODE_BUDGETS.items()
        }

        logger.info("LoadBalancer initialised")

    # ------------------------------------------------------------------
    # Public API — agent registration and load reporting
    # ------------------------------------------------------------------

    def register_agent(
        self,
        agent_id: str,
        supported_lanes: list[WorkCellLane],
        max_concurrent: int = 3,
        node: str = "",
    ) -> AgentCapability:
        """Register an agent with its lane capabilities and concurrency limit.

        Calling this method a second time with the same *agent_id* replaces
        the existing registration, preserving the current load counters stored
        via :meth:`update_agent_load` only if the new ``max_concurrent`` value
        is identical to the old one.  In all other respects the registration
        is overwritten.

        When a non-empty *node* is provided and the node is known in the budget
        registry, the node's ``current_agent_count`` is incremented.  If the
        agent was previously registered on a different node its old node count
        is decremented before the new node count is incremented.

        Args:
            agent_id:        Unique agent identifier.
            supported_lanes: List of :class:`WorkCellLane` values this agent
                             can handle.  An empty list means the agent will
                             never be recommended (no lane affinity).
            max_concurrent:  Maximum simultaneous tasks.  Must be >= 1.
            node:            Name of the compute node this agent runs on
                             (e.g. ``"prya"``).  Empty string disables all
                             node-budget filtering for this agent.

        Returns:
            The :class:`AgentCapability` record stored for the agent.

        Raises:
            ValueError: If *max_concurrent* is less than 1.
        """
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be >= 1, got {max_concurrent}")

        with self._lock:
            existing = self._agents.get(agent_id)
            now = time.monotonic()

            # Adjust node counts: decrement old node, increment new node.
            old_node = existing.node if existing else ""
            if old_node and old_node != node:
                # Agent is moving from one node to another — release old slot.
                old_budget = self._node_budgets.get(old_node)
                if old_budget is not None and old_budget.current_agent_count > 0:
                    old_budget.current_agent_count -= 1

            if node and node != old_node:
                # Agent is newly associated with a node — claim a slot.
                new_budget = self._node_budgets.get(node)
                if new_budget is not None:
                    new_budget.current_agent_count += 1

            capability = AgentCapability(
                agent_id=agent_id,
                supported_lanes=list(supported_lanes),
                max_concurrent=max_concurrent,
                # Preserve live counters when re-registering
                current_active=existing.current_active if existing else 0,
                context_budget_remaining_pct=(
                    existing.context_budget_remaining_pct if existing else 100.0
                ),
                last_heartbeat_age_seconds=0.0,
                node=node,
            )
            self._agents[agent_id] = capability
            # Record heartbeat timestamp so freshness can be computed later
            self._heartbeat_times[agent_id] = now

            logger.info(
                "LoadBalancer.register_agent: agent=%s lanes=%s max_concurrent=%d node=%s",
                agent_id,
                [lane.value for lane in supported_lanes],
                max_concurrent,
                node or "(none)",
            )
            return capability

    def update_agent_load(
        self,
        agent_id: str,
        current_active: int,
        context_budget_pct: float,
    ) -> None:
        """Update the live load metrics for a registered agent.

        Also records the current monotonic time as the agent's heartbeat
        timestamp, resetting the freshness clock.

        Args:
            agent_id:           Agent to update.  Must already be registered
                                via :meth:`register_agent`.
            current_active:     Current number of active tasks (>= 0).
            context_budget_pct: Remaining context-window budget as a
                                percentage in ``[0.0, 100.0]``.

        Raises:
            KeyError:   If *agent_id* has not been registered.
            ValueError: If *current_active* < 0 or *context_budget_pct* is
                        outside ``[0.0, 100.0]``.
        """
        if current_active < 0:
            raise ValueError(f"current_active must be >= 0, got {current_active}")
        if not (0.0 <= context_budget_pct <= 100.0):
            raise ValueError(
                f"context_budget_pct must be in [0, 100], got {context_budget_pct}"
            )

        with self._lock:
            if agent_id not in self._agents:
                raise KeyError(f"Agent '{agent_id}' is not registered")

            existing = self._agents[agent_id]
            self._agents[agent_id] = existing.model_copy(
                update={
                    "current_active": current_active,
                    "context_budget_remaining_pct": context_budget_pct,
                    "last_heartbeat_age_seconds": 0.0,
                }
            )
            self._heartbeat_times[agent_id] = time.monotonic()

            logger.debug(
                "LoadBalancer.update_agent_load: agent=%s active=%d budget=%.1f%%",
                agent_id,
                current_active,
                context_budget_pct,
            )

    # ------------------------------------------------------------------
    # Public API — recommendation
    # ------------------------------------------------------------------

    def recommend(
        self,
        lane: WorkCellLane,
        strategy: BalancingStrategy = BalancingStrategy.least_loaded,
    ) -> list[BalancingRecommendation]:
        """Return agents ranked by suitability for the requested lane.

        Only agents whose ``supported_lanes`` includes *lane* are eligible.
        When no eligible agents exist an empty list is returned.

        Args:
            lane:     The work-cell lane a task needs to execute in.
            strategy: Algorithm used to rank candidates.  Defaults to
                      :attr:`~BalancingStrategy.least_loaded`.

        Returns:
            List of :class:`BalancingRecommendation` sorted by ``score``
            descending (best candidate first).  Empty when no eligible agents
            are registered or all eligible agents are fully saturated.
        """
        with self._lock:
            # Collect agents that support the requested lane AND are not
            # blocked by a node that has already reached its max_agents limit.
            eligible = [
                cap
                for cap in self._agents.values()
                if lane in cap.supported_lanes
                and not self._node_is_full(cap.node)
            ]

        if not eligible:
            logger.debug(
                "LoadBalancer.recommend: no eligible agents for lane=%s", lane.value
            )
            return []

        if strategy == BalancingStrategy.round_robin:
            return self._recommend_round_robin(eligible, lane)

        recommendations = self._score_agents(eligible, lane, strategy)

        logger.info(
            "LoadBalancer.recommend: lane=%s strategy=%s candidates=%d top=%s score=%.4f",
            lane.value,
            strategy.value,
            len(recommendations),
            recommendations[0].agent_id if recommendations else "none",
            recommendations[0].score if recommendations else 0.0,
        )
        return recommendations

    def get_agent_stats(self) -> dict[str, AgentCapability]:
        """Return a snapshot of all registered agents' capability records.

        The returned dict is keyed by ``agent_id``.  The
        ``last_heartbeat_age_seconds`` field on each record reflects the
        computed age at the moment this method is called.

        Returns:
            Dict mapping ``agent_id`` → :class:`AgentCapability`.
        """
        with self._lock:
            now = time.monotonic()
            snapshot: dict[str, AgentCapability] = {}
            for agent_id, cap in self._agents.items():
                hb_time = self._heartbeat_times.get(agent_id, now)
                age = max(now - hb_time, 0.0)
                snapshot[agent_id] = cap.model_copy(
                    update={"last_heartbeat_age_seconds": round(age, 3)}
                )
            return snapshot

    def update_node_state(self, node_name: str, current_agent_count: int) -> None:
        """Update the current agent count for a node.

        This method provides an explicit override of the live agent count for a
        node, independent of the automatic accounting performed by
        :meth:`register_agent`.  Useful when external orchestration tracks node
        load and wants to push authoritative counts into the balancer.

        If *node_name* is not found in the budget registry the call is a
        no-op (unknown nodes are silently ignored to avoid raising in hot-path
        callers).

        Args:
            node_name:           Name of the node to update (e.g. ``"prya"``).
            current_agent_count: Authoritative live agent count for this node.
                                 Must be >= 0.

        Raises:
            ValueError: If *current_agent_count* is negative.
        """
        if current_agent_count < 0:
            raise ValueError(
                f"current_agent_count must be >= 0, got {current_agent_count}"
            )
        with self._lock:
            budget = self._node_budgets.get(node_name)
            if budget is None:
                logger.debug(
                    "LoadBalancer.update_node_state: unknown node=%s — ignored",
                    node_name,
                )
                return
            budget.current_agent_count = current_agent_count
            logger.debug(
                "LoadBalancer.update_node_state: node=%s agent_count=%d",
                node_name,
                current_agent_count,
            )

    def get_node_stats(self) -> dict[str, dict]:
        """Return current node utilisation statistics.

        Returns a dict keyed by node name.  Each value is a plain dict with
        the keys:

        ``name``
            Node name.
        ``max_agents``
            Maximum allowed agents.
        ``ram_gb``
            RAM in GB.
        ``current_agent_count``
            Live agent count.
        ``utilization``
            ``current_agent_count / max_agents`` expressed as a float in
            ``[0.0, ∞)``.  Values >= 1.0 indicate the node is at or over
            capacity.

        Returns:
            Dict mapping node name → stats dict.
        """
        with self._lock:
            return {
                name: {
                    "name": nb.name,
                    "max_agents": nb.max_agents,
                    "ram_gb": nb.ram_gb,
                    "current_agent_count": nb.current_agent_count,
                    "utilization": nb.current_agent_count / nb.max_agents,
                }
                for name, nb in self._node_budgets.items()
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _node_is_full(self, node: str) -> bool:
        """Return ``True`` if *node* has reached its agent capacity.

        Agents without a node (``node=""``) are never considered blocked.
        Unknown nodes are also treated as not full (permissive default).

        Args:
            node: Node name from the agent's capability record.

        Returns:
            ``True`` when the node's ``current_agent_count`` equals or exceeds
            ``max_agents``; ``False`` otherwise.
        """
        if not node:
            return False
        budget = self._node_budgets.get(node)
        if budget is None:
            return False
        return budget.current_agent_count >= budget.max_agents

    def _compute_node_score(self, node: str) -> float:
        """Return ``[0.0, 1.0]`` node utilisation score.

        * ``node=""`` (no association) → ``1.0`` — no penalty applied.
        * Unknown node → ``1.0`` — permissive default.
        * ``utilisation <= _NODE_PENALTY_THRESHOLD`` → ``1.0``.
        * ``utilisation > _NODE_PENALTY_THRESHOLD`` → linear decay from
          ``1.0`` at the threshold to ``0.0`` at 100% utilisation.

        Args:
            node: Node name from the agent's capability record.

        Returns:
            Node score in ``[0.0, 1.0]``.
        """
        if not node:
            return 1.0
        budget = self._node_budgets.get(node)
        if budget is None:
            return 1.0
        utilization = budget.current_agent_count / budget.max_agents
        if utilization <= _NODE_PENALTY_THRESHOLD:
            return 1.0
        # Linear decay: 1.0 at threshold → 0.0 at 100%
        penalty_range = 1.0 - _NODE_PENALTY_THRESHOLD
        return max(1.0 - (utilization - _NODE_PENALTY_THRESHOLD) / penalty_range, 0.0)

    def _compute_freshness_score(self, agent_id: str, now: float) -> float:
        """Return ``[0.0, 1.0]`` freshness score based on heartbeat age.

        Score is 1.0 when age < :const:`_FRESHNESS_FULL_SECONDS`.
        Score decays linearly to 0.0 at :const:`_FRESHNESS_ZERO_SECONDS`.

        Args:
            agent_id: Agent whose heartbeat timestamp is looked up.
            now:      Current monotonic time for age calculation.

        Returns:
            Freshness score in ``[0.0, 1.0]``.
        """
        hb_time = self._heartbeat_times.get(agent_id)
        if hb_time is None:
            return 0.0
        age = max(now - hb_time, 0.0)
        if age <= _FRESHNESS_FULL_SECONDS:
            return 1.0
        if age >= _FRESHNESS_ZERO_SECONDS:
            return 0.0
        decay_range = _FRESHNESS_ZERO_SECONDS - _FRESHNESS_FULL_SECONDS
        return 1.0 - (age - _FRESHNESS_FULL_SECONDS) / decay_range

    @staticmethod
    def _compute_capacity_score(cap: AgentCapability) -> float:
        """Return ``[0.0, 1.0]`` capacity score.

        Score is the fraction of concurrency headroom remaining:
        ``1 - current_active / max_concurrent``.

        Args:
            cap: Agent capability record.

        Returns:
            Capacity score in ``[0.0, 1.0]``.
        """
        return max(1.0 - cap.current_active / cap.max_concurrent, 0.0)

    @staticmethod
    def _compute_budget_score(cap: AgentCapability) -> float:
        """Return ``[0.0, 1.0]`` context-budget score.

        Args:
            cap: Agent capability record.

        Returns:
            ``context_budget_remaining_pct / 100``.
        """
        return cap.context_budget_remaining_pct / 100.0

    @staticmethod
    def _compute_lane_affinity(cap: AgentCapability, lane: WorkCellLane) -> float:
        """Return ``1.0`` if *lane* is in ``cap.supported_lanes``, else ``0.0``.

        Because :meth:`recommend` pre-filters to eligible agents, this score
        is always 1.0 for the candidates that reach scoring.  The weight is
        still included so that :class:`affinity_first` strategy can amplify it.

        Args:
            cap:  Agent capability record.
            lane: The target lane.

        Returns:
            ``1.0`` or ``0.0``.
        """
        return 1.0 if lane in cap.supported_lanes else 0.0

    def _build_recommendation(
        self,
        cap: AgentCapability,
        lane: WorkCellLane,
        now: float,
    ) -> BalancingRecommendation:
        """Compute the composite score for *cap* and return a recommendation.

        Args:
            cap:  Agent capability snapshot.
            lane: Target lane (used for affinity sub-score).
            now:  Current monotonic time for freshness calculation.

        Returns:
            :class:`BalancingRecommendation` with score and reasons.
        """
        capacity = self._compute_capacity_score(cap)
        budget = self._compute_budget_score(cap)
        affinity = self._compute_lane_affinity(cap, lane)
        freshness = self._compute_freshness_score(cap.agent_id, now)
        node_score = self._compute_node_score(cap.node)

        composite = round(
            _W_CAPACITY * capacity
            + _W_BUDGET * budget
            + _W_AFFINITY * affinity
            + _W_FRESHNESS * freshness
            + _W_NODE * node_score,
            4,
        )

        reasons = [
            f"capacity_score={capacity:.3f}({_W_CAPACITY * 100:.0f}%)",
            f"budget_score={budget:.3f}({_W_BUDGET * 100:.0f}%)",
            f"lane_affinity={affinity:.1f}({_W_AFFINITY * 100:.0f}%)",
            f"freshness={freshness:.3f}({_W_FRESHNESS * 100:.0f}%)",
            f"node_score={node_score:.3f}({_W_NODE * 100:.0f}%)",
        ]

        return BalancingRecommendation(
            agent_id=cap.agent_id,
            lane=lane,
            score=composite,
            reasons=reasons,
        )

    def _score_agents(
        self,
        eligible: list[AgentCapability],
        lane: WorkCellLane,
        strategy: BalancingStrategy,
    ) -> list[BalancingRecommendation]:
        """Score and sort eligible agents using the composite formula.

        For :attr:`~BalancingStrategy.affinity_first` the lane-affinity weight
        is amplified by boosting agents that explicitly list the lane —
        in practice all eligible agents have full affinity, so the
        differentiation comes from the remaining components.

        For :attr:`~BalancingStrategy.least_loaded` the standard weights apply.

        Args:
            eligible: Pre-filtered list of eligible :class:`AgentCapability`.
            lane:     Target lane.
            strategy: Scoring strategy (not round_robin).

        Returns:
            Sorted :class:`BalancingRecommendation` list (best first).
        """
        now = time.monotonic()
        recommendations: list[BalancingRecommendation] = []

        for cap in eligible:
            rec = self._build_recommendation(cap, lane, now)
            recommendations.append(rec)

        # Sort by score descending; tie-break by agent_id for determinism
        recommendations.sort(key=lambda r: (-r.score, r.agent_id))
        return recommendations

    def _recommend_round_robin(
        self,
        eligible: list[AgentCapability],
        lane: WorkCellLane,
    ) -> list[BalancingRecommendation]:
        """Return agents in round-robin order for even task distribution.

        The eligible list is sorted by agent_id for a deterministic rotation
        sequence across calls.  The per-lane counter advances by 1 on every
        call regardless of scores.

        Args:
            eligible: Pre-filtered list of eligible :class:`AgentCapability`.
            lane:     Target lane (used for counter keying and rec building).

        Returns:
            List of :class:`BalancingRecommendation` starting at the current
            rotation position and wrapping around.  Scores still reflect the
            composite formula so the caller can see relative suitability even
            under round-robin ordering.
        """
        # Stable sort so round-robin cycles are predictable
        sorted_eligible = sorted(eligible, key=lambda c: c.agent_id)
        n = len(sorted_eligible)
        now = time.monotonic()

        with self._lock:
            pos = self._rr_counters.get(lane, 0) % n
            self._rr_counters[lane] = (pos + 1) % n

        # Build recommendations starting from the selected position
        recommendations: list[BalancingRecommendation] = []
        for i in range(n):
            cap = sorted_eligible[(pos + i) % n]
            rec = self._build_recommendation(cap, lane, now)
            recommendations.append(rec)

        logger.debug(
            "LoadBalancer.round_robin: lane=%s selected=%s pos=%d",
            lane.value,
            recommendations[0].agent_id,
            pos,
        )
        return recommendations


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_lb_instance: LoadBalancer | None = None
_lb_lock: RLock = RLock()


def get_load_balancer() -> LoadBalancer:
    """Return the global :class:`LoadBalancer` singleton.

    The instance is created lazily on the first call and reused on all
    subsequent calls.  Thread-safe.

    To replace the singleton (e.g. in tests) call :func:`reset_load_balancer`
    first.

    Returns:
        The singleton :class:`LoadBalancer`.
    """
    global _lb_instance
    with _lb_lock:
        if _lb_instance is None:
            _lb_instance = LoadBalancer()
            logger.info("LoadBalancer singleton created")
        return _lb_instance


def reset_load_balancer() -> None:
    """Destroy the global singleton — intended for use in tests only.

    After calling this function the next call to :func:`get_load_balancer`
    will create a fresh instance.
    """
    global _lb_instance
    with _lb_lock:
        _lb_instance = None
        logger.debug("LoadBalancer singleton reset")
