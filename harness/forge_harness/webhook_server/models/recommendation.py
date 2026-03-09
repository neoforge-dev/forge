"""Recommendation Models — DF-3001
==================================

Pydantic models for the recommendation engine that maps tasks to the best
available agent based on runtime capabilities and historical performance.

Classes
-------
RecommendationStrategy
    Enum of available recommendation strategies.
AgentScore
    A single scored agent candidate with factor breakdown and historical stats.
TaskRecommendation
    Full recommendation result for a task including the ranked agent list,
    confidence score, and human-readable reasoning.

Reference
---------
forge_harness/webhook_server/models/agent_load.py  (AgentCapability)
forge_harness/webhook_server/models/work_cell.py   (WorkCellLane)
forge_harness/webhook_server/services/recommendation_engine.py
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-3001)
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# RecommendationStrategy enum
# ---------------------------------------------------------------------------


class RecommendationStrategy(str, Enum):
    """Algorithm used to score and rank agent candidates for a task.

    Attributes:
        capability_match:       Weight capability match (lane compatibility)
                                above historical performance.  Fast path for
                                tasks where agent specialisation matters most.
        historical_performance: Weight historical pass-rate and lead-time data
                                above current load.  Best when agents have
                                enough history to differentiate.
        balanced:               Equal consideration of capability, historical
                                performance, current load, and context budget.
                                Default strategy; recommended for general use.
    """

    capability_match = "capability_match"
    historical_performance = "historical_performance"
    balanced = "balanced"


# ---------------------------------------------------------------------------
# AgentScore model
# ---------------------------------------------------------------------------


class AgentScore(BaseModel):
    """A single scored agent candidate for a task recommendation.

    Attributes:
        agent_id
            Stable, unique identifier of the agent (e.g. ``"forge:nova"``).
        score
            Composite recommendation score in ``[0.0, 1.0]``.  Higher values
            indicate better suitability for the requested task.
        factors
            Breakdown of the composite score into named sub-components.
            Keys are factor names (e.g. ``"capability_match"``,
            ``"historical_pass_rate"``, ``"current_load"``,
            ``"context_budget"``); values are floats in ``[0.0, 1.0]``.
        historical_pass_rate
            Fraction of tasks the agent completed successfully, based on
            recorded outcomes.  ``None`` when no history is available.
        avg_lead_time_seconds
            Average wall-clock time from task start to completion, in seconds.
            ``None`` when no history is available.
    """

    model_config = ConfigDict(frozen=True)

    agent_id: str = Field(..., description="Unique agent identifier.")
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Composite recommendation score (higher is better).",
    )
    factors: dict[str, float] = Field(
        default_factory=dict,
        description="Score factor breakdown: factor_name → value in [0, 1].",
    )
    historical_pass_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Historical task pass rate (0–1).  None when no history.",
    )
    avg_lead_time_seconds: float | None = Field(
        default=None,
        ge=0.0,
        description="Average lead time in seconds.  None when no history.",
    )


# ---------------------------------------------------------------------------
# TaskRecommendation model
# ---------------------------------------------------------------------------


class TaskRecommendation(BaseModel):
    """Full recommendation result for a task.

    The recommendation engine resolves the target lane, filters eligible
    agents, scores each candidate, and returns this model with the sorted
    ranked list.

    Attributes:
        task_id
            Identifier of the task this recommendation was computed for.
        recommended_agents
            Ranked list of :class:`AgentScore` sorted by ``score`` descending.
            The first element is the best candidate.  Empty when no eligible
            agents are registered.
        confidence
            Confidence in the top recommendation, expressed as the score gap
            between the first and second ranked agents (``top_score -
            second_score``).  In ``[0.0, 1.0]``.  When only one agent is
            eligible this equals the top score directly.  Zero when the list
            is empty.
        reasoning
            Human-readable explanation strings describing the recommendation
            outcome, including lane resolution, agent filtering, and the top
            agent's factor breakdown.
        created_at
            UTC timestamp when the recommendation was computed.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(..., description="Task identifier this recommendation belongs to.")
    recommended_agents: list[AgentScore] = Field(
        default_factory=list,
        description="Ranked agent candidates, best first.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Score spread between top and second-best agent (0–1).",
    )
    reasoning: list[str] = Field(
        default_factory=list,
        description="Human-readable explanation of the recommendation.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the recommendation was computed.",
    )
