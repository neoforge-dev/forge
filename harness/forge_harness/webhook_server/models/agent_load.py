"""Agent Load Models — DF-2005
==============================

Pydantic models and enumerations for the agent load-balancing subsystem.

These models represent the runtime capability profile of a registered agent,
the output of the balancing algorithm, and the available balancing strategies.

Classes
-------
AgentCapability
    Snapshot of a single agent's lane support, concurrency headroom, context
    budget, and heartbeat freshness.
BalancingRecommendation
    A scored agent candidate produced by :class:`~forge_harness.webhook_server
    .services.load_balancer.LoadBalancer`.
BalancingStrategy
    Enum of available load-distribution algorithms.

Reference
---------
forge_harness/webhook_server/models/work_cell.py  (WorkCellLane)
forge_harness/webhook_server/services/load_balancer.py  (LoadBalancer)
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-2005)
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from forge_harness.webhook_server.models.work_cell import WorkCellLane

# ---------------------------------------------------------------------------
# BalancingStrategy enum
# ---------------------------------------------------------------------------


class BalancingStrategy(str, Enum):
    """Algorithm used to rank and select agents for an incoming task.

    Attributes:
        round_robin:    Distribute tasks evenly across eligible agents
                        without regard to instantaneous load.  Maintains
                        a per-lane counter so each successive call selects
                        the next agent in rotation.
        least_loaded:   Prefer the agent with the lowest ratio of active
                        tasks to its declared concurrency ceiling
                        (``current_active / max_concurrent``).  Agents
                        with more headroom score higher.
        affinity_first: Strongly prefer agents that explicitly list the
                        requested lane in their ``supported_lanes``.
                        Among equally-affine agents the least-loaded tie-
                        breaking rule applies.
    """

    round_robin = "round_robin"
    least_loaded = "least_loaded"
    affinity_first = "affinity_first"


# ---------------------------------------------------------------------------
# AgentCapability model
# ---------------------------------------------------------------------------


class AgentCapability(BaseModel):
    """Runtime capability snapshot for a single registered agent.

    This model is maintained by the
    :class:`~forge_harness.webhook_server.services.load_balancer.LoadBalancer`
    and updated each time the agent reports its load via
    :meth:`~forge_harness.webhook_server.services.load_balancer.LoadBalancer
    .update_agent_load`.

    Attributes:
        agent_id
            Stable, unique identifier for the agent (e.g. ``"forge:nova"``).
        supported_lanes
            Work-cell lanes this agent is equipped to handle.  Only agents
            whose ``supported_lanes`` include the requested lane are
            considered eligible candidates by the load balancer.
        max_concurrent
            Maximum number of tasks the agent may execute simultaneously.
            Must be at least 1.
        current_active
            Number of tasks the agent is currently executing.  Must be
            non-negative and must not exceed ``max_concurrent`` for a
            healthy agent state.
        context_budget_remaining_pct
            Percentage of the agent's context-window (token) budget that
            is still available, expressed as a value in ``[0.0, 100.0]``.
            A lower value indicates the agent is closer to exhaustion and
            should receive fewer new tasks.
        last_heartbeat_age_seconds
            Seconds elapsed since the agent's most recent heartbeat.
            Values below 60 indicate a live, responsive agent.  Higher
            values cause the freshness component of the composite score to
            decay linearly until the agent is treated as unresponsive.
        node
            Name of the compute node this agent runs on (e.g. ``"prya"``,
            ``"sati"``).  An empty string means no node association:  the
            agent is considered eligible regardless of node budget limits.
            When set, the load balancer consults
            :attr:`~forge_harness.webhook_server.services.load_balancer
            .LoadBalancer._node_budgets` to gate eligibility and apply a
            utilisation penalty.
    """

    agent_id: str = Field(..., description="Unique agent identifier.")
    supported_lanes: list[WorkCellLane] = Field(
        default_factory=list,
        description="Work-cell lanes this agent can handle.",
    )
    max_concurrent: int = Field(
        default=3,
        ge=1,
        description="Maximum simultaneous tasks for this agent.",
    )
    current_active: int = Field(
        default=0,
        ge=0,
        description="Number of tasks currently executing on this agent.",
    )
    context_budget_remaining_pct: float = Field(
        default=100.0,
        ge=0.0,
        le=100.0,
        description="Remaining context-window budget as a percentage (0–100).",
    )
    last_heartbeat_age_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Seconds since the agent's last heartbeat.",
    )
    node: str = Field(
        default="",
        description=(
            "Compute node this agent runs on (e.g. 'prya', 'sati'). "
            "Empty string means no node association — agent is always eligible "
            "regardless of node budgets."
        ),
    )


# ---------------------------------------------------------------------------
# BalancingRecommendation model
# ---------------------------------------------------------------------------


class BalancingRecommendation(BaseModel):
    """A single agent recommendation produced by :class:`LoadBalancer`.

    Recommendations are sorted by :attr:`score` descending (highest first)
    so the caller can simply take the first element for the best candidate.

    Attributes:
        agent_id
            Identifier of the recommended agent.
        lane
            The work-cell lane the recommendation was computed for.
        score
            Composite recommendation score in ``[0.0, 1.0]``.  Higher is
            better.
        reasons
            Human-readable breakdown of how the score was composed.  Each
            element describes one scoring component (e.g.
            ``"capacity_score=0.667"``).
    """

    agent_id: str = Field(..., description="Recommended agent identifier.")
    lane: WorkCellLane = Field(..., description="Target work-cell lane.")
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Composite recommendation score (higher is better).",
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="Human-readable score component breakdown.",
    )
