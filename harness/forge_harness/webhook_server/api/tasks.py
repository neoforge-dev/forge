"""Task Queue API Endpoints

REST API endpoints for task queue management.
Extracted from webhook_server_main.py for better modularity.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forge_harness.error_schema import (
    AGENT_NOT_AVAILABLE,
    AGENT_NOT_FOUND,
    INTERNAL_ERROR,
    TASK_CREATION_FAILED,
    TASK_NOT_FOUND,
    TASK_VALIDATION_ERROR,
    VALIDATION_ERROR,
    ForgeError,
    api_error_body,
    api_success_body,
)
from forge_harness.logging_config import get_logger
from forge_harness.models.task_lifecycle import TransitionError
from forge_harness.webhook_server.core.dependencies import require_permission
from forge_harness.webhook_server.core.errors import forge_json_error
from forge_harness.webhook_server.models.sse_events import SSEEventType
from forge_harness.webhook_server.services.audit import get_audit_logger
from forge_harness.webhook_server.services.event_emitter import get_event_emitter

# Import types for type hints only (avoid circular imports)
if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

router = APIRouter()


# =============================================================================
# Request Models
# =============================================================================


class TaskLease(BaseModel):
    """Node/agent lease metadata for deterministic task ownership.

    CP-2001 schema fields:
    - owner_node: node currently responsible for execution
    - owner_agent: concrete agent session handling the task
    - lease_expires_at: lease expiry timestamp (UTC ISO-8601)
    - path_lock: repo or submodule path lock key for collision prevention
    """

    owner_node: str
    owner_agent: str
    lease_expires_at: datetime
    path_lock: str


class CreateTaskRequest(BaseModel):
    """Request body for creating a task."""

    subject: str
    description: str = ""
    priority: str = "medium"
    required_role: str | None = None
    domain: str | None = None
    project: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    lease: TaskLease | None = None


class UpdateTaskRequest(BaseModel):
    """Request body for updating a task."""

    subject: str | None = None
    description: str | None = None
    priority: str | None = None
    status: str | None = None
    required_role: str | None = None
    claimed_by: str | None = None
    depends_on: list[str] | None = None
    lease: TaskLease | None = None


class ClaimTaskRequest(BaseModel):
    """Request body for claiming a task."""

    agent_id: str
    lease: TaskLease | None = None


class LeaseClaimRequest(BaseModel):
    """Request body for claiming a lease explicitly."""

    lease: TaskLease


class LeaseRenewRequest(BaseModel):
    """Request body for renewing an existing task lease."""

    lease: TaskLease


class LeaseReleaseRequest(BaseModel):
    """Request body for releasing an existing task lease."""

    owner_node: str | None = None
    owner_agent: str | None = None


class LeaseRequeueRequest(BaseModel):
    """Request body for forcing a leased task back to pending."""

    owner_node: str | None = None
    owner_agent: str | None = None
    reason: str | None = None


class DispatchRequest(BaseModel):
    """Request body for dispatching a task to an agent."""

    agent_id: str


class ReorderRequest(BaseModel):
    """Request body for reordering tasks."""

    task_ids: list[str] | None = None
    task_order: list[str] | None = None


# =============================================================================
# Helper Functions
# =============================================================================


def api_response(
    data: Any = None, error_code: str | None = None, error_message: str | None = None
) -> dict[str, Any]:
    """Create a standardized API response using the canonical error schema."""
    if error_code or error_message:
        return api_error_body(
            code=error_code or "UNKNOWN_ERROR",
            message=error_message or "An error occurred",
        )
    return api_success_body(data)


def _lease_error_response(code: str, message: str, status_code: int = 409) -> JSONResponse:
    """Build standardized lease error response using the canonical error schema."""
    return forge_json_error(status_code, code, message)


async def get_task_handler():
    """Get the task handler singleton."""
    from forge_harness.webhook_server.handlers.task_handler import get_task_handler

    return get_task_handler()


async def get_event_bus():
    """Get the event bus singleton."""
    from forge_harness.webhook_server.services.event_bus import get_event_bus as _get_event_bus

    return _get_event_bus()


async def get_agent_registry():
    """Get the agent registry."""
    from forge_harness.webhook_server.services.agent_registry import (
        get_agent_registry as _get_agent_registry,
    )

    return _get_agent_registry()


def _extract_request_context(request: Request | None = None) -> tuple[str | None, str | None]:
    """Extract IP address and user agent from FastAPI Request."""
    if request is None:
        return None, None
    # Get client IP (check common headers first)
    ip_address = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.headers.get("X-Real-IP", "").strip()
        or request.client.host
        if request.client
        else None
    )
    user_agent = request.headers.get("User-Agent")
    return ip_address, user_agent


# =============================================================================
# API Endpoints
# =============================================================================


@router.get("/api/tasks")
async def list_tasks(
    status: str | None = Query(None, description="Filter by status"),
    priority: str | None = Query(None, description="Filter by priority"),
    limit: int = Query(50, ge=1, le=200, description="Maximum results"),
    _=Depends(require_permission("tasks:read")),
):
    """List all tasks with optional filters."""
    handler = await get_task_handler()
    tasks = await handler.list_tasks(
        status=status,
        priority=priority,
        limit=limit,
    )
    return JSONResponse(
        content=api_response(
            {
                "tasks": tasks,
                "count": len(tasks),
            }
        )
    )


@router.post("/api/tasks")
async def create_task(
    body: CreateTaskRequest,
    request: Request,
    _=Depends(require_permission("tasks:write")),
):
    """Create a new task."""
    try:
        from forge_harness.cli_v2.tasks import validate_task

        validate_task(body.subject, body.description)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=ForgeError(code=TASK_VALIDATION_ERROR, message=str(e)).to_dict(),
        )
    except Exception as e:
        # If import fails or other error, log it but don't crash
        logger.warning(f"Task validation import failed: {e}")

    handler = await get_task_handler()
    try:
        task = await handler.create_task(
            subject=body.subject,
            description=body.description,
            priority=body.priority,
            required_role=body.required_role,
            lease=body.lease.model_dump(mode="json") if body.lease else None,
            domain=body.domain,
            project=body.project,
            depends_on=body.depends_on,
        )
        # Emit SSE event via EventBus (for streaming clients)
        event_bus = await get_event_bus()
        await event_bus.publish("task.created", task)

        # Also emit via EventEmitter (for ring buffer / polling API)
        try:
            emitter = get_event_emitter()
            emitter.emit(
                SSEEventType.task_created,
                task,
                source="tasks-api",
            )
        except Exception as e:
            logger.warning(f"Failed to emit task.created to EventEmitter: {e}")

        # Audit log
        ip_address, user_agent = _extract_request_context(request)
        audit_logger = get_audit_logger()
        await audit_logger.log_task_creation(
            task_id=task.get("id", "unknown"),
            actor={"id": "api", "type": "system"},
            context={
                "subject": body.subject,
                "priority": body.priority,
                "required_role": body.required_role,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return JSONResponse(content=api_response(task))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ForgeError(code=TASK_CREATION_FAILED, message=str(e)).to_dict(),
        )


@router.get("/api/tasks/stats")
async def get_task_stats(
    _=Depends(require_permission("tasks:read")),
):
    """Get task queue statistics."""
    handler = await get_task_handler()
    stats = await handler.get_stats()
    return JSONResponse(content=api_response(stats))


# =============================================================================
# Next Task Recommendation Engine (must be before /api/tasks/{task_id} to avoid shadowing)
# =============================================================================


@router.get("/api/tasks/recommended")
async def get_recommended_tasks(
    agent_id: str | None = Query(None, description="Filter recommendations for specific agent"),
    limit: int = Query(10, ge=1, le=100, description="Max recommendations to return"),
    include_blocked: bool = Query(False, description="Include blocked tasks"),
    min_priority: int = Query(1, ge=1, le=10, description="Minimum priority threshold"),
    task_type: str | None = Query(None, description="Filter by task type"),
    domain: str | None = Query(None, description="Filter by domain"),
    _=Depends(require_permission("tasks:read")),
):
    """
    Get intelligent task recommendations for agents.

    Uses multi-factor scoring to recommend optimal task-agent pairings
    based on priority, capability match, and historical performance.

    This is the Next Task Engine - the "brain" of the orchestrator.
    """
    from forge_harness.analytics import RecommendationEngine

    handler = await get_task_handler()

    # Get all pending tasks
    tasks = await handler.list_tasks(status="pending", limit=1000)

    # Create recommendation engine
    engine = RecommendationEngine()

    # Get default agent profiles (would come from agent registry in production)
    agents = _get_default_agent_profiles()

    # Generate recommendations
    response = engine.generate_recommendations(
        tasks=tasks,
        agents=agents,
        limit=limit,
        agent_id_filter=agent_id,
        include_blocked=include_blocked,
        min_priority=min_priority,
        task_type_filter=task_type,
        domain_filter=domain,
    )

    # Convert to dictionary
    result = engine.to_dict(response)

    return JSONResponse(content=api_response(result))


def _get_default_agent_profiles() -> list:
    """
    Get default agent profiles for recommendations.

    In production, these would be loaded from the agent registry
    with real-time status and performance data.
    """
    from datetime import datetime, timedelta

    from forge_harness.models import AgentProfile, ComplexityLevel, Skill, TaskType

    return [
        AgentProfile(
            agent_id="forge:opencode",
            name="OpenCode",
            model="opencode-latest",
            capabilities={
                TaskType.API_SIMPLE: 0.95,
                TaskType.API_STATEFUL: 0.90,
                TaskType.API_BATCH: 0.85,
                TaskType.API_EXTERNAL: 0.80,
                TaskType.TEST_UNIT: 0.75,
                TaskType.TEST_INTEGRATION: 0.70,
                TaskType.REFACTOR_CLEANUP: 0.70,
            },
            skills={
                Skill.PYTHON: 0.95,
                Skill.FASTAPI: 0.90,
                Skill.SQLALCHEMY: 0.85,
                Skill.PYTEST: 0.80,
                Skill.AUTHENTICATION: 0.85,
            },
            max_complexity=ComplexityLevel.EXPERT,
            preferred_domains=["codeswiftr-com", "brandfocus-ai"],
            avoids=[TaskType.FRONTEND_COMPONENT, TaskType.RESEARCH_MARKET],
            status="idle",
            last_activity=datetime.now(),
            total_tasks_completed=150,
            success_rate=0.92,
            avg_task_duration=timedelta(hours=2),
            avg_quality_score=0.88,
        ),
        AgentProfile(
            agent_id="forge:codex",
            name="Codex",
            model="codex-latest",
            capabilities={
                TaskType.FRONTEND_COMPONENT: 0.90,
                TaskType.FRONTEND_PAGE: 0.85,
                TaskType.FRONTEND_STYLES: 0.80,
                TaskType.API_SIMPLE: 0.75,
                TaskType.TEST_UNIT: 0.70,
            },
            skills={
                Skill.TYPESCRIPT: 0.90,
                Skill.REACT: 0.85,
                Skill.JAVASCRIPT: 0.85,
            },
            max_complexity=ComplexityLevel.COMPLEX,
            preferred_domains=["brandfocus-ai", "thebrightharbor-com"],
            avoids=[TaskType.INFRA_DEPLOY, TaskType.RESEARCH_TECHNICAL],
            status="idle",
            last_activity=datetime.now(),
            total_tasks_completed=120,
            success_rate=0.88,
            avg_task_duration=timedelta(hours=1.5),
            avg_quality_score=0.85,
        ),
        AgentProfile(
            agent_id="forge:gemini",
            name="Gemini",
            model="gemini-2.0-flash",
            capabilities={
                TaskType.RESEARCH_TECHNICAL: 0.95,
                TaskType.RESEARCH_MARKET: 0.90,
                TaskType.DOCS_ARCHITECTURE: 0.85,
                TaskType.DOCS_API: 0.80,
                TaskType.REFACTOR_CLEANUP: 0.75,
            },
            skills={
                Skill.RESEARCH: 0.95,
                Skill.DOCUMENTATION: 0.90,
                Skill.ANALYSIS: 0.85,
                Skill.PYTHON: 0.70,
            },
            max_complexity=ComplexityLevel.MODERATE,
            preferred_domains=[],
            avoids=[TaskType.INFRA_CI_CD],
            status="idle",
            last_activity=datetime.now(),
            total_tasks_completed=80,
            success_rate=0.85,
            avg_task_duration=timedelta(hours=3),
            avg_quality_score=0.82,
        ),
    ]


@router.get("/api/tasks/agents/available")
async def get_available_agents(
    role: str | None = Query(None, description="Filter by role"),
    _=Depends(require_permission("agents:read")),
):
    """List agents available to accept tasks.

    Returns agents with status 'idle' or 'waiting'.
    Excludes agents with status 'busy' or 'paused'.
    """
    handler = await get_task_handler()
    agent_registry = await get_agent_registry()

    agents = agent_registry.list_active()

    # Get all tasks to count per agent
    all_tasks = await handler.list_tasks(limit=1000)
    task_counts = {}
    for task in all_tasks:
        claimed_by = task.get("claimed_by")
        if claimed_by and task.get("status") in ("pending", "in_progress"):
            task_counts[claimed_by] = task_counts.get(claimed_by, 0) + 1

    # Filter to agents that are available
    available = []
    for agent in agents:
        # Include agents with status 'idle' or 'waiting'
        # Exclude 'busy', 'paused', 'completed', 'failed'
        if agent.status not in ("idle", "waiting"):
            continue

        # Apply role filter if provided
        if role and agent.role != role:
            continue

        available.append(
            {
                "agent_id": agent.id,
                "name": agent.name or agent.role,
                "role": agent.role,
                "current_task_count": task_counts.get(agent.id, 0),
            }
        )

    return JSONResponse(content=api_response({"agents": available, "count": len(available)}))


@router.get("/api/tasks/ready")
async def list_ready_tasks(
    priority: str | None = Query(None, description="Filter by priority"),
    limit: int = Query(50, ge=1, le=200, description="Maximum results"),
    agent: str | None = Query(None, description="Filter by required role matching agent"),
    _=Depends(require_permission("tasks:read")),
):
    """List tasks that are ready for execution (all dependencies met).

    Returns pending tasks where:
    - status == "pending"
    - Not claimed/leased
    - All depends_on task IDs are in "completed" status
    """
    handler = await get_task_handler()
    result = await handler.list_ready_tasks(
        priority=priority,
        limit=limit,
        agent=agent,
    )
    return JSONResponse(
        content=api_response(
            {
                "tasks": result["ready"],
                "count": len(result["ready"]),
                "blocked_count": result["blocked_count"],
            }
        )
    )


# =============================================================================
# Task ID Routes (must be after specific routes to avoid shadowing)
# =============================================================================


@router.get("/api/tasks/{task_id}")
async def get_task(
    task_id: str,
    _=Depends(require_permission("tasks:read")),
):
    """Get a specific task."""
    handler = await get_task_handler()
    task = await handler.get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=TASK_NOT_FOUND,
                message=f"Task '{task_id}' not found",
            ).to_dict(),
        )
    return JSONResponse(content=api_response(task))


@router.put("/api/tasks/{task_id}")
async def update_task(
    task_id: str,
    body: UpdateTaskRequest,
    _=Depends(require_permission("tasks:write")),
):
    """Update a task."""
    handler = await get_task_handler()
    # Build updates dict from body (only include non-None values)
    updates = {}
    if body.subject is not None:
        updates["subject"] = body.subject
    if body.description is not None:
        updates["description"] = body.description
    if body.priority is not None:
        updates["priority"] = body.priority
    if body.status is not None:
        updates["status"] = body.status
    if body.required_role is not None:
        updates["required_role"] = body.required_role
    if body.claimed_by is not None:
        updates["claimed_by"] = body.claimed_by
    if body.depends_on is not None:
        updates["depends_on"] = body.depends_on
    if body.lease is not None:
        updates["lease"] = body.lease.model_dump(mode="json")

    try:
        task = await handler.update_task(task_id, updates)
    except TransitionError as exc:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=TASK_VALIDATION_ERROR,
                message=str(exc),
            ).to_dict(),
        )
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=TASK_NOT_FOUND,
                message=f"Task '{task_id}' not found",
            ).to_dict(),
        )

    # Emit SSE event via EventBus (for streaming clients)
    event_bus = await get_event_bus()
    await event_bus.publish("task.updated", task)

    # Also emit via EventEmitter (for ring buffer / polling API)
    try:
        emitter = get_event_emitter()
        # Determine event type based on status
        if task.get("status") == "completed":
            event_type = SSEEventType.task_completed
        elif task.get("status") == "in_progress" or task.get("claimed_by"):
            event_type = SSEEventType.task_assigned
        else:
            event_type = SSEEventType.task_status_changed
        emitter.emit(event_type, task, source="tasks-api")
    except Exception as e:
        logger.warning(f"Failed to emit task.updated to EventEmitter: {e}")

    return JSONResponse(content=api_response(task))


@router.delete("/api/tasks/{task_id}")
async def delete_task(
    task_id: str,
    request: Request,
    _=Depends(require_permission("tasks:write")),
):
    """Delete a task."""
    handler = await get_task_handler()
    success = await handler.delete_task(task_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=TASK_NOT_FOUND,
                message=f"Task '{task_id}' not found",
            ).to_dict(),
        )

    # Emit SSE event
    event_bus = await get_event_bus()
    await event_bus.publish("task.deleted", {"task_id": task_id})

    # Audit log
    ip_address, user_agent = _extract_request_context(request)
    audit_logger = get_audit_logger()
    await audit_logger.log_task_deletion(
        task_id=task_id,
        actor={"id": "api", "type": "system"},
        context={},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return JSONResponse(content=api_response({"success": True}))


@router.post("/api/tasks/{task_id}/claim")
async def claim_task(
    task_id: str,
    body: ClaimTaskRequest,
    request: Request,
    _=Depends(require_permission("tasks:write")),
):
    """Claim a task for an agent."""
    from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

    handler = await get_task_handler()
    try:
        if body.lease is not None:
            task = await handler.claim_task_with_lease(
                task_id,
                body.lease.model_dump(mode="json"),
            )
        else:
            task = await handler.claim_task(task_id, body.agent_id)
    except TaskLeaseError as e:
        return _lease_error_response(e.code, str(e), e.status_code)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=TASK_NOT_FOUND,
                message=f"Task '{task_id}' not found",
            ).to_dict(),
        )

    # Emit SSE event via EventBus (for streaming clients)
    event_bus = await get_event_bus()
    await event_bus.publish("task.claimed", task)

    # Also emit via EventEmitter (for ring buffer / polling API)
    try:
        emitter = get_event_emitter()
        emitter.emit(SSEEventType.task_assigned, task, source="tasks-api")
    except Exception as e:
        logger.warning(f"Failed to emit task.claimed to EventEmitter: {e}")

    # Audit log
    ip_address, user_agent = _extract_request_context(request)
    audit_logger = get_audit_logger()
    await audit_logger.log_task_assignment(
        task_id=task_id,
        agent_id=body.lease.owner_agent if body.lease else body.agent_id,
        actor={"id": "api", "type": "system"},
        context={},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return JSONResponse(content=api_response(task))


@router.post("/api/tasks/{task_id}/lease/claim")
async def claim_task_lease(
    task_id: str,
    body: LeaseClaimRequest,
    request: Request,
    _=Depends(require_permission("tasks:write")),
):
    """Claim task ownership with explicit lease metadata."""
    from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

    handler = await get_task_handler()
    try:
        task = await handler.claim_task_with_lease(
            task_id,
            body.lease.model_dump(mode="json"),
        )
    except TaskLeaseError as e:
        return _lease_error_response(e.code, str(e), e.status_code)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=TASK_NOT_FOUND,
                message=f"Task '{task_id}' not found",
            ).to_dict(),
        )

    event_bus = await get_event_bus()
    await event_bus.publish("task.lease.claimed", task)

    ip_address, user_agent = _extract_request_context(request)
    audit_logger = get_audit_logger()
    await audit_logger.log_task_assignment(
        task_id=task_id,
        agent_id=body.lease.owner_agent,
        actor={"id": "api", "type": "system"},
        context={"owner_node": body.lease.owner_node, "path_lock": body.lease.path_lock},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return JSONResponse(content=api_response(task))


@router.post("/api/tasks/{task_id}/lease/renew")
async def renew_task_lease(
    task_id: str,
    body: LeaseRenewRequest,
    _=Depends(require_permission("tasks:write")),
):
    """Renew lease expiry for an already-owned task lease."""
    from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

    handler = await get_task_handler()
    try:
        task = await handler.renew_task_lease(
            task_id,
            body.lease.model_dump(mode="json"),
        )
    except TaskLeaseError as e:
        return _lease_error_response(e.code, str(e), e.status_code)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=TASK_NOT_FOUND,
                message=f"Task '{task_id}' not found",
            ).to_dict(),
        )

    event_bus = await get_event_bus()
    await event_bus.publish("task.lease.renewed", task)
    return JSONResponse(content=api_response(task))


@router.post("/api/tasks/{task_id}/lease/release")
async def release_task_lease(
    task_id: str,
    body: LeaseReleaseRequest | None = None,
    _=Depends(require_permission("tasks:write")),
):
    """Release active lease ownership from a task."""
    from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

    handler = await get_task_handler()
    payload = body or LeaseReleaseRequest()

    try:
        task = await handler.release_task_lease(
            task_id,
            owner_node=payload.owner_node,
            owner_agent=payload.owner_agent,
        )
    except TaskLeaseError as e:
        return _lease_error_response(e.code, str(e), e.status_code)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=TASK_NOT_FOUND,
                message=f"Task '{task_id}' not found",
            ).to_dict(),
        )

    event_bus = await get_event_bus()
    await event_bus.publish("task.lease.released", task)
    return JSONResponse(content=api_response(task))


@router.post("/api/tasks/{task_id}/lease/requeue")
async def requeue_task(
    task_id: str,
    body: LeaseRequeueRequest | None = None,
    _=Depends(require_permission("tasks:write")),
):
    """Clear ownership metadata and return task to pending queue."""
    from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

    handler = await get_task_handler()
    payload = body or LeaseRequeueRequest()

    try:
        task = await handler.requeue_task(
            task_id,
            owner_node=payload.owner_node,
            owner_agent=payload.owner_agent,
        )
    except TaskLeaseError as e:
        return _lease_error_response(e.code, str(e), e.status_code)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=TASK_NOT_FOUND,
                message=f"Task '{task_id}' not found",
            ).to_dict(),
        )

    event_bus = await get_event_bus()
    await event_bus.publish(
        "task.requeued",
        {"task_id": task_id, "task": task, "reason": payload.reason},
    )
    return JSONResponse(content=api_response(task))


@router.post("/api/tasks/{task_id}/dispatch")
async def dispatch_task(
    task_id: str,
    body: DispatchRequest,
    request: Request,
    _=Depends(require_permission("tasks:write")),
):
    """Dispatch task to a specific agent.

    This is similar to claim_task but initiated by the system rather than the agent.
    Updates task status to 'assigned' and sets claimed_by to the agent_id.

    Also sends a message to the target agent via tmux using DispatchClient.
    """
    handler = await get_task_handler()
    agent_registry = await get_agent_registry()

    # Validate agent exists and is available
    agent = agent_registry.get(body.agent_id)
    if not agent:
        return JSONResponse(
            status_code=404,
            content=api_response(
                error_code=AGENT_NOT_FOUND,
                error_message=f"Agent {body.agent_id} not found",
            ),
        )

    # Validate agent is available (status idle or waiting)
    if agent.status not in ("idle", "waiting"):
        return JSONResponse(
            status_code=400,
            content=api_response(
                error_code=AGENT_NOT_AVAILABLE,
                error_message=f"Agent {body.agent_id} is not available (status: {agent.status})",
            ),
        )

    # 1. Claim the task for the agent
    task = await handler.claim_task(task_id, body.agent_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=TASK_NOT_FOUND,
                message=f"Task '{task_id}' not found",
            ).to_dict(),
        )

    # 2. Send task details to agent via tmux using DispatchClient
    tmux_target = getattr(agent, "tmux_session", None)
    dispatch_success = False
    dispatch_error = None

    if tmux_target:
        try:
            from forge_harness.fleet.dispatch_client import DispatchClient

            # Build task notification message
            task_message = (
                f"Task dispatched: {task.get('subject', 'N/A')}\n"
                f"Description: {task.get('description', 'No description')}\n"
                f"Priority: {task.get('priority', 'medium')}"
            )

            # Use DispatchClient to send message with verification
            client = DispatchClient()
            dispatch_result = await client.send(
                target=tmux_target,
                message=task_message,
                verify=True,
                timeout=10.0,
                skip_readiness=False,
                check_git_lock=False,  # Don't wait for git lock on task dispatch
            )

            dispatch_success = dispatch_result.success
            if not dispatch_success:
                dispatch_error = (
                    dispatch_result.error or "DispatchClient returned unsuccessful result"
                )
                logger.warning(f"Failed to dispatch task to {tmux_target}: {dispatch_error}")
            else:
                logger.info(f"Successfully dispatched task {task_id} to {tmux_target}")

        except Exception as e:
            dispatch_error = str(e)
            logger.error(f"Failed to dispatch task via DispatchClient: {e}", exc_info=True)
    else:
        dispatch_error = f"Agent {body.agent_id} has no tmux_session configured"
        logger.warning(dispatch_error)

    if not dispatch_success:
        # Transactional rollback: if task delivery fails, return task to pending queue.
        rollback_success = False
        rollback_error = None
        rolled_back_task = None
        try:
            rolled_back_task = await handler.requeue_task(task_id)
            rollback_success = rolled_back_task is not None
            if not rollback_success:
                rollback_error = "Task disappeared before rollback could complete"
        except Exception as e:
            rollback_error = str(e)
            logger.error(f"Failed to rollback dispatch for task {task_id}: {e}", exc_info=True)

        event_bus = await get_event_bus()
        await event_bus.publish(
            "task.dispatch_failed",
            {
                "task_id": task_id,
                "agent_id": body.agent_id,
                "dispatch_error": dispatch_error,
                "rollback_success": rollback_success,
                "rollback_error": rollback_error,
                "task": rolled_back_task or task,
            },
        )

        ip_address, user_agent = _extract_request_context(request)
        audit_logger = get_audit_logger()
        await audit_logger.log_task_assignment(
            task_id=task_id,
            agent_id=body.agent_id,
            actor={"id": "api", "type": "system"},
            context={
                "dispatch_success": False,
                "dispatch_error": dispatch_error,
                "tmux_target": tmux_target,
                "rollback_success": rollback_success,
                "rollback_error": rollback_error,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )

        error_message = (
            f"Failed to dispatch task {task_id} to agent {body.agent_id}: "
            f"{dispatch_error or 'unknown dispatch error'}"
        )
        if rollback_success:
            error_message = f"{error_message}. Task claim was rolled back."
        else:
            error_message = (
                f"{error_message}. Rollback failed: {rollback_error or 'unknown rollback error'}"
            )

        return JSONResponse(
            status_code=503 if rollback_success else 500,
            content=api_response(
                error_code=INTERNAL_ERROR,
                error_message=error_message,
            ),
        )

    # 3. Emit SSE event
    event_bus = await get_event_bus()
    await event_bus.publish(
        "task.dispatched",
        {
            "task_id": task_id,
            "agent_id": body.agent_id,
            "task": task,
            "dispatch_success": dispatch_success,
            "dispatch_error": dispatch_error,
        },
    )

    # 4. Audit log
    ip_address, user_agent = _extract_request_context(request)
    audit_logger = get_audit_logger()
    await audit_logger.log_task_assignment(
        task_id=task_id,
        agent_id=body.agent_id,
        actor={"id": "api", "type": "system"},
        context={
            "dispatch_success": dispatch_success,
            "dispatch_error": dispatch_error,
            "tmux_target": tmux_target,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return JSONResponse(content=api_response(task))


@router.get("/api/tasks/{task_id}/history")
async def get_task_history(
    task_id: str,
    _=Depends(require_permission("tasks:read")),
):
    """Get status history for a task.

    Reconstructs a status timeline from the task's current state and timestamps.
    Returns entries in reverse-chronological order (most recent first).

    Each entry contains:
    - timestamp: ISO-8601 UTC string
    - status: the task status value at that point
    - changed_by: agent ID if applicable
    - notes: human-readable description of the transition
    """
    handler = await get_task_handler()
    task = await handler.get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=TASK_NOT_FOUND,
                message=f"Task '{task_id}' not found",
            ).to_dict(),
        )

    history: list[dict[str, str | None]] = []

    # Every task starts as pending at created_at
    history.append(
        {
            "timestamp": task.get("created_at", ""),
            "status": "pending",
            "changed_by": None,
            "notes": "Task created",
        }
    )

    current_status = task.get("status", "pending")
    updated_at = task.get("updated_at", "")
    created_at = task.get("created_at", "")

    # Only add a second entry when the status has actually changed and
    # updated_at differs from created_at (avoids a duplicate "pending" row).
    if current_status != "pending" and updated_at and updated_at != created_at:
        status_labels: dict[str, str] = {
            "assigned": "Assigned",
            "in_progress": "In Progress",
            "completed": "Completed",
            "failed": "Failed",
            "cancelled": "Cancelled",
            "blocked": "Blocked",
        }
        label = status_labels.get(current_status, current_status.replace("_", " ").title())
        history.append(
            {
                "timestamp": updated_at,
                "status": current_status,
                "changed_by": task.get("claimed_by") or None,
                "notes": f"Task moved to {label}",
            }
        )

    # Return most-recent first
    history.sort(key=lambda e: e.get("timestamp") or "", reverse=True)

    return JSONResponse(content=api_response({"history": history, "count": len(history)}))


@router.post("/api/tasks/reorder")
async def reorder_tasks(
    body: ReorderRequest,
    _=Depends(require_permission("tasks:write")),
):
    """Update task order.

    Accepts a list of task IDs in the desired order.
    Updates each task's order field based on array index.
    Validates all task IDs exist before updating.
    """
    handler = await get_task_handler()

    if body.task_ids is None and body.task_order is None:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message="task_ids or task_order must be provided",
            ).to_dict(),
        )

    task_ids = body.task_ids or body.task_order

    if not task_ids:
        raise HTTPException(
            status_code=400,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message="task_ids or task_order cannot be empty",
            ).to_dict(),
        )

    # Validation pass: check all task IDs exist before making changes
    not_found_ids = []
    for task_id in task_ids:
        task = await handler.get_task(task_id)
        if not task:
            not_found_ids.append(task_id)

    if not_found_ids:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=TASK_NOT_FOUND,
                message=f"Tasks not found: {', '.join(not_found_ids)}",
                details={"not_found_ids": not_found_ids},
            ).to_dict(),
        )

    # Update pass: persist the new order
    updated_tasks = []
    for idx, task_id in enumerate(task_ids):
        updated_task = await handler.update_task(task_id, {"order": idx})
        if updated_task:
            updated_tasks.append(updated_task)

    # Emit SSE event
    event_bus = await get_event_bus()
    await event_bus.publish("task.reordered", {"task_ids": task_ids, "count": len(updated_tasks)})

    return JSONResponse(
        content=api_response({"updated": updated_tasks, "count": len(updated_tasks)})
    )
