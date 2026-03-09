"""Dashboard API Endpoints

REST API endpoints for dashboard metrics and agent tracking.
Uses the unified DashboardService for all dashboard data.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.core.dependencies import verify_auth
from forge_harness.webhook_server.services.dashboard_service import (
    get_dashboard_service,
)

logger = get_logger(__name__)

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================


class AgentMetricsResponse(BaseModel):
    """Response model for agent metrics."""

    id: str
    role: str
    project: str | None
    task: str | None
    status: str
    progress: int
    last_activity: str | None
    is_stale: bool
    domain: str | None
    token_usage: dict[str, int]
    messages_count: int
    focus_tags: list[str]


class TaskThroughputResponse(BaseModel):
    """Response model for task throughput metrics."""

    total_today: int
    completed_today: int
    failed_today: int
    in_progress_today: int
    avg_duration_seconds: float
    success_rate: float
    by_type: dict[str, int]


class DashboardSummaryResponse(BaseModel):
    """Response model for dashboard summary."""

    total_agents: int
    active_agents_count: int
    idle_agents_count: int
    error_agents_count: int
    paused_agents_count: int
    agents: list[AgentMetricsResponse]
    task_throughput: TaskThroughputResponse
    system_status: str
    last_updated: str


class TaskRecordRequest(BaseModel):
    """Request model for recording task events."""

    task_id: str = Field(..., min_length=1, max_length=200)
    task_type: str | None = Field(None, max_length=100)
    metadata: dict[str, Any] | None = None


class TaskStartRequest(BaseModel):
    """Request model for recording task start."""

    agent_id: str = Field(..., min_length=1, max_length=100)
    task_id: str = Field(..., min_length=1, max_length=200)
    task_type: str | None = Field(None, max_length=100)
    metadata: dict[str, Any] | None = None


class TaskEndRequest(BaseModel):
    """Request model for recording task end."""

    outcome: str = Field(..., pattern=r"^(success|failure|cancelled)$")


class AgentUpdateRequest(BaseModel):
    """Request model for updating agent status."""

    status: str | None = Field(None, pattern=r"^(active|idle|paused|error|completed)$")
    progress: int | None = Field(None, ge=0, le=100)
    task: str | None = Field(None, max_length=1000)


class DashboardAgentRegisterRequest(BaseModel):
    """Request model for registering an agent in the dashboard service.

    Note: This differs from the agent registry AgentRegisterRequest
    (webhook_server/api/agents.py). This model serves the dashboard-specific
    endpoint and includes agent_id and focus_tags fields.
    """

    agent_id: str = Field(..., min_length=1, max_length=100)
    role: str = Field(..., min_length=1, max_length=100)
    project: str | None = Field(None, max_length=200)
    task: str | None = Field(None, max_length=1000)
    domain: str | None = Field(None, max_length=100)
    focus_tags: list[str] | None = Field(None, description="List of skill tags")


# =============================================================================
# API Response Helper
# =============================================================================


def api_response(
    data: Any = None, error_code: str | None = None, error_message: str | None = None
) -> dict[str, Any]:
    """Create a standardized API response."""
    from datetime import UTC, datetime

    if error_code or error_message:
        return {
            "success": False,
            "data": None,
            "error": {
                "code": error_code or "UNKNOWN_ERROR",
                "message": error_message or "An error occurred",
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }
    return {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# =============================================================================
# API Endpoints
# =============================================================================


@router.get("/api/dashboard", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    force_refresh: bool = Query(False, description="Skip cache and fetch fresh data"),
    _=Depends(verify_auth),
) -> DashboardSummaryResponse:
    """Get comprehensive dashboard summary.

    Returns a unified view of all dashboard metrics including:
    - Agent status counts (active, idle, error, paused)
    - Individual agent details
    - Task throughput metrics
    - System health status
    """
    service = get_dashboard_service()
    summary = await service.get_dashboard_summary(force_refresh=force_refresh)

    return DashboardSummaryResponse(
        total_agents=summary.total_agents,
        active_agents_count=summary.active_agents_count,
        idle_agents_count=summary.idle_agents_count,
        error_agents_count=summary.error_agents_count,
        paused_agents_count=summary.paused_agents_count,
        agents=[
            AgentMetricsResponse(
                id=a.id,
                role=a.role,
                project=a.project,
                task=a.task,
                status=a.status,
                progress=a.progress,
                last_activity=a.last_activity.isoformat() if a.last_activity else None,
                is_stale=a.is_stale,
                domain=a.domain,
                token_usage=a.token_usage,
                messages_count=a.messages_count,
                focus_tags=a.focus_tags,
            )
            for a in summary.agents
        ],
        task_throughput=TaskThroughputResponse(
            total_today=summary.task_throughput.total_today,
            completed_today=summary.task_throughput.completed_today,
            failed_today=summary.task_throughput.failed_today,
            in_progress_today=summary.task_throughput.in_progress_today,
            avg_duration_seconds=summary.task_throughput.avg_duration_seconds,
            success_rate=summary.task_throughput.success_rate,
            by_type=summary.task_throughput.by_type,
        ),
        system_status=summary.system_status,
        last_updated=summary.last_updated.isoformat(),
    )


@router.get("/api/dashboard/agents", response_model=list[AgentMetricsResponse])
async def list_agents(
    include_stale: bool = Query(False, description="Include agents with expired TTL"),
    status_filter: str | None = Query(None, description="Filter by status"),
    _=Depends(verify_auth),
) -> list[AgentMetricsResponse]:
    """List all registered agents with their metrics."""
    service = get_dashboard_service()
    agents = service.list_agents(include_stale=include_stale, status_filter=status_filter)

    return [
        AgentMetricsResponse(
            id=a.id,
            role=a.role,
            project=a.project,
            task=a.task,
            status=a.status,
            progress=a.progress,
            last_activity=a.last_activity.isoformat() if a.last_activity else None,
            is_stale=a.is_stale,
            domain=a.domain,
            token_usage=a.token_usage,
            messages_count=a.messages_count,
            focus_tags=a.focus_tags,
        )
        for a in agents
    ]


@router.get("/api/dashboard/agents/{agent_id}", response_model=AgentMetricsResponse)
async def get_agent(
    agent_id: str,
    _=Depends(verify_auth),
) -> AgentMetricsResponse:
    """Get metrics for a specific agent."""
    service = get_dashboard_service()
    agent = service.get_agent(agent_id)

    if not agent:
        return JSONResponse(
            status_code=404,
            content=api_response(
                error_code="NOT_FOUND", error_message=f"Agent {agent_id} not found"
            ),
        )

    return AgentMetricsResponse(
        id=agent.id,
        role=agent.role,
        project=agent.project,
        task=agent.task,
        status=agent.status,
        progress=agent.progress,
        last_activity=agent.last_activity.isoformat() if agent.last_activity else None,
        is_stale=agent.is_stale,
        domain=agent.domain,
        token_usage=agent.token_usage,
        messages_count=agent.messages_count,
        focus_tags=agent.focus_tags,
    )


@router.post("/api/dashboard/agents/register")
async def register_agent(
    body: DashboardAgentRegisterRequest,
    _=Depends(verify_auth),
) -> dict[str, Any]:
    """Register or update an agent in the dashboard registry."""
    service = get_dashboard_service()
    agent = service.register_agent(
        agent_id=body.agent_id,
        role=body.role,
        project=body.project,
        task=body.task,
        domain=body.domain,
        focus_tags=body.focus_tags,
    )

    return JSONResponse(
        content=api_response(agent.to_dict()),
    )


@router.patch("/api/dashboard/agents/{agent_id}")
async def update_agent(
    agent_id: str,
    body: AgentUpdateRequest,
    _=Depends(verify_auth),
) -> dict[str, Any]:
    """Update agent status in the dashboard registry."""
    service = get_dashboard_service()
    agent = service.update_agent(
        agent_id=agent_id,
        status=body.status,
        progress=body.progress,
        task=body.task,
    )

    if not agent:
        return JSONResponse(
            status_code=404,
            content=api_response(
                error_code="NOT_FOUND", error_message=f"Agent {agent_id} not found"
            ),
        )

    return JSONResponse(
        content=api_response(agent.to_dict()),
    )


@router.delete("/api/dashboard/agents/{agent_id}")
async def remove_agent(
    agent_id: str,
    _=Depends(verify_auth),
) -> dict[str, Any]:
    """Remove an agent from the dashboard registry."""
    service = get_dashboard_service()
    removed = service.remove_agent(agent_id)

    if not removed:
        return JSONResponse(
            status_code=404,
            content=api_response(
                error_code="NOT_FOUND", error_message=f"Agent {agent_id} not found"
            ),
        )

    return JSONResponse(
        content=api_response({"removed": True, "agent_id": agent_id}),
    )


@router.get("/api/dashboard/throughput", response_model=TaskThroughputResponse)
async def get_task_throughput(
    hours: int = Query(24, ge=1, le=720, description="Hours to look back"),
    _=Depends(verify_auth),
) -> TaskThroughputResponse:
    """Get task throughput metrics for the specified time period."""
    service = get_dashboard_service()
    throughput = service.get_task_throughput(hours=hours)

    return TaskThroughputResponse(
        total_today=throughput.total_today,
        completed_today=throughput.completed_today,
        failed_today=throughput.failed_today,
        in_progress_today=throughput.in_progress_today,
        avg_duration_seconds=throughput.avg_duration_seconds,
        success_rate=throughput.success_rate,
        by_type=throughput.by_type,
    )


@router.post("/api/dashboard/tasks/start")
async def record_task_start(
    body: TaskStartRequest,
    _=Depends(verify_auth),
) -> dict[str, Any]:
    """Record the start of a task."""
    service = get_dashboard_service()
    success = service.record_task_start(
        agent_id=body.agent_id,
        task_id=body.task_id,
        task_type=body.task_type,
        metadata=body.metadata,
    )

    if not success:
        return JSONResponse(
            status_code=409,
            content=api_response(
                error_code="ALREADY_EXISTS", error_message=f"Task {body.task_id} already exists"
            ),
        )

    return JSONResponse(
        content=api_response(
            {
                "task_id": body.task_id,
                "agent_id": body.agent_id,
                "started": True,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        ),
    )


@router.post("/api/dashboard/tasks/{task_id}/end")
async def record_task_end(
    task_id: str,
    body: TaskEndRequest,
    _=Depends(verify_auth),
) -> dict[str, Any]:
    """Record the end of a task."""
    service = get_dashboard_service()
    success = service.record_task_end(task_id=task_id, outcome=body.outcome)

    if not success:
        return JSONResponse(
            status_code=404,
            content=api_response(error_code="NOT_FOUND", error_message=f"Task {task_id} not found"),
        )

    return JSONResponse(
        content=api_response(
            {
                "task_id": task_id,
                "outcome": body.outcome,
                "ended": True,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        ),
    )


@router.get("/api/dashboard/agents/{agent_id}/tasks")
async def get_agent_task_metrics(
    agent_id: str,
    days: int = 7,
    _=Depends(verify_auth),
) -> dict[str, Any]:
    """Get task metrics for a specific agent."""
    service = get_dashboard_service()
    metrics = service.get_agent_task_metrics(agent_id=agent_id, days=days)
    return JSONResponse(content=api_response(metrics))


@router.get("/api/dashboard/task-types/{task_type}")
async def get_task_type_metrics(
    task_type: str,
    days: int = 7,
    _=Depends(verify_auth),
) -> dict[str, Any]:
    """Get metrics for a specific task type."""
    service = get_dashboard_service()
    metrics = service.get_task_type_metrics(task_type=task_type, days=days)
    return JSONResponse(content=api_response(metrics))
