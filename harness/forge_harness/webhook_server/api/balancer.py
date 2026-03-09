"""Load Balancer API Endpoints — DF-2005

REST endpoints that expose the agent load-balancing service to the
Command Center frontend and forge CLI.

Endpoints:
    GET  /api/balancer/agents              — List registered agents with load stats
    POST /api/balancer/agents              — Register a new agent
    GET  /api/balancer/agents/{agent_id}   — Get stats for a specific agent
    POST /api/balancer/recommend           — Get recommendation for a task
    PUT  /api/balancer/agents/{agent_id}/load — Update agent load metrics
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forge_harness.error_schema import (
    NOT_FOUND,
    VALIDATION_ERROR,
    ForgeError,
    api_success_body,
)
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class RegisterAgentRequest(BaseModel):
    """Request body for registering a new agent with the load balancer."""

    agent_id: str = Field(..., min_length=1, description="Unique agent identifier.")
    capabilities: list[str] = Field(
        ...,
        description="List of WorkCellLane values this agent supports (e.g. ['api_simple', 'docs']).",
    )
    max_concurrent: int = Field(
        default=3,
        ge=1,
        description="Maximum simultaneous tasks for this agent.",
    )


class RecommendRequest(BaseModel):
    """Request body for getting an agent recommendation for a task."""

    task_type: str = Field(..., description="Canonical task type (e.g. 'bug-fix').")
    risk_tier: str = Field(..., description="Risk classification: low, medium, high, critical.")
    strategy: str = Field(
        default="balanced",
        description="Scoring strategy: balanced, capability_match, historical_performance.",
    )
    task_paths: list[str] | None = Field(
        default=None,
        description="Optional list of paths for path-lock filtering.",
    )


class UpdateLoadRequest(BaseModel):
    """Request body for updating an agent's live load metrics."""

    current_active: int = Field(..., ge=0, description="Current number of active tasks.")
    context_budget_pct: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Remaining context-window budget as a percentage (0-100).",
    )


# ---------------------------------------------------------------------------
# Dependency helper
# ---------------------------------------------------------------------------


def _get_balancer():
    """Lazy import of LoadBalancer singleton to avoid circular imports."""
    from forge_harness.webhook_server.services.load_balancer import get_load_balancer

    return get_load_balancer()


def _api_response(data: Any) -> dict[str, Any]:
    """Build a canonical success response body."""
    return api_success_body(data)


def _capability_to_dict(cap: Any) -> dict[str, Any]:
    """Serialise an AgentCapability to a plain dict for API responses."""
    return cap.model_dump(mode="json")


def _parse_lanes(lane_strings: list[str]) -> list[Any]:
    """Parse a list of lane strings into WorkCellLane enum values.

    Args:
        lane_strings: List of lane identifier strings.

    Returns:
        List of WorkCellLane enum values.

    Raises:
        HTTPException: 422 when any lane string is not recognised.
    """
    from forge_harness.webhook_server.models.work_cell import WorkCellLane

    lanes = []
    valid = [lane.value for lane in WorkCellLane]
    for lane_str in lane_strings:
        try:
            lanes.append(WorkCellLane(lane_str))
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=ForgeError(
                    code=VALIDATION_ERROR,
                    message=f"Invalid lane {lane_str!r}. Valid lanes: {valid}",
                ).to_dict(),
            )
    return lanes


def _parse_lane(lane_str: str) -> Any:
    """Parse a single lane string into a WorkCellLane enum value."""
    return _parse_lanes([lane_str])[0]


# ---------------------------------------------------------------------------
# Agent collection routes
# ---------------------------------------------------------------------------


@router.get("/api/balancer/agents")
async def list_agents():
    """List all registered agents with their current load statistics.

    The ``last_heartbeat_age_seconds`` field reflects the computed age at
    the time of this call.

    Returns:
        Dict with ``agents`` list and ``count``.
    """
    balancer = _get_balancer()
    stats = balancer.get_agent_stats()
    return JSONResponse(
        content=_api_response(
            {
                "agents": [_capability_to_dict(cap) for cap in stats.values()],
                "count": len(stats),
            }
        )
    )


@router.post("/api/balancer/agents")
async def register_agent(body: RegisterAgentRequest):
    """Register a new agent with the load balancer.

    If the agent is already registered, its registration is updated (the
    existing load counters are preserved when max_concurrent is unchanged).

    Args:
        body: agent_id, capabilities (lane list), and max_concurrent.

    Returns:
        The AgentCapability dict for the registered agent.
    """
    lanes = _parse_lanes(body.capabilities)
    balancer = _get_balancer()

    try:
        capability = balancer.register_agent(
            agent_id=body.agent_id,
            supported_lanes=lanes,
            max_concurrent=body.max_concurrent,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=str(exc),
            ).to_dict(),
        )

    return JSONResponse(
        status_code=201,
        content=_api_response(_capability_to_dict(capability)),
    )


# ---------------------------------------------------------------------------
# Recommendation route
# ---------------------------------------------------------------------------


@router.post("/api/balancer/recommend")
async def recommend_agent(body: RecommendRequest):
    """Get agent recommendation(s) for an incoming task.

    Uses the full RecommendationEngine with path-lock awareness,
    historical pass rates, and context budget scoring.

    Args:
        body: task_type, risk_tier, optional strategy, and optional task_paths.

    Returns:
        Dict with ``recommendations``, ``confidence``, ``reasoning``, and ``lane``.
    """
    import uuid

    from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType
    from forge_harness.webhook_server.models.recommendation import RecommendationStrategy

    try:
        task_type = TaskType(body.task_type)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=f"Invalid task_type {body.task_type!r}. Valid values: {[t.value for t in TaskType]}",
            ).to_dict(),
        )

    try:
        risk_tier = RiskTier(body.risk_tier)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=f"Invalid risk_tier {body.risk_tier!r}. Valid values: {[r.value for r in RiskTier]}",
            ).to_dict(),
        )

    try:
        strategy = RecommendationStrategy(body.strategy)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=f"Invalid strategy {body.strategy!r}. Valid values: {[s.value for s in RecommendationStrategy]}",
            ).to_dict(),
        )

    # Use the full RecommendationEngine (same as CLI)
    from forge_harness.webhook_server.services.recommendation_engine import (
        get_recommendation_engine,
    )

    engine = get_recommendation_engine()

    # Generate synthetic task_id for hypothetical recommendations
    task_id = f"api-{uuid.uuid4().hex[:8]}"

    recommendation = engine.recommend(
        task_id=task_id,
        task_type=task_type,
        risk_tier=risk_tier,
        strategy=strategy,
        task_paths=body.task_paths,
    )

    # Resolve lane for response
    from forge_harness.webhook_server.models.work_cell import LaneResolver

    resolver = LaneResolver()
    lane = resolver.resolve(task_type, risk_tier)

    return JSONResponse(
        content=_api_response(
            {
                "recommendations": [
                    {
                        "agent_id": a.agent_id,
                        "score": a.score,
                        "factors": a.factors,
                        "historical_pass_rate": a.historical_pass_rate,
                        "avg_lead_time_seconds": a.avg_lead_time_seconds,
                    }
                    for a in recommendation.recommended_agents
                ],
                "count": len(recommendation.recommended_agents),
                "confidence": recommendation.confidence,
                "reasoning": recommendation.reasoning,
                "lane": lane.value,
                "strategy": strategy.value,
                "task_id": task_id,
            }
        )
    )


# ---------------------------------------------------------------------------
# Agent-ID routes
# ---------------------------------------------------------------------------


@router.get("/api/balancer/agents/{agent_id}")
async def get_agent_stats(agent_id: str):
    """Get load statistics for a specific registered agent.

    Args:
        agent_id: The agent identifier.

    Returns:
        AgentCapability dict with current load stats, or 404 if not found.
    """
    balancer = _get_balancer()
    stats = balancer.get_agent_stats()
    cap = stats.get(agent_id)
    if cap is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=NOT_FOUND,
                message=f"Agent '{agent_id}' is not registered with the load balancer",
            ).to_dict(),
        )
    return JSONResponse(content=_api_response(_capability_to_dict(cap)))


@router.put("/api/balancer/agents/{agent_id}/load")
async def update_agent_load(agent_id: str, body: UpdateLoadRequest):
    """Update the live load metrics for a registered agent.

    Also resets the agent's heartbeat clock (freshness score becomes 1.0).

    Args:
        agent_id: The agent to update.  Must already be registered.
        body:     current_active and context_budget_pct.

    Returns:
        Updated AgentCapability dict, or 404/422 on error.
    """
    balancer = _get_balancer()
    try:
        balancer.update_agent_load(
            agent_id=agent_id,
            current_active=body.current_active,
            context_budget_pct=body.context_budget_pct,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=NOT_FOUND,
                message=f"Agent '{agent_id}' is not registered with the load balancer",
            ).to_dict(),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=str(exc),
            ).to_dict(),
        )

    # Return updated stats
    stats = balancer.get_agent_stats()
    cap = stats[agent_id]
    return JSONResponse(content=_api_response(_capability_to_dict(cap)))
