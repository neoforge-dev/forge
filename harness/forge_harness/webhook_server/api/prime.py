"""PRIME Command Endpoints (Multi-Agent Clearance)

Provides registration, completion, and assignment listing for the /prime command.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge_harness.clearance_engine import ClearanceEngine
    from forge_harness.state_store import StateStore
    from forge_harness.webhook_server.services.event_bus import EventBus
    from forge_harness.worktree_manager import WorktreeManager

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.core.dependencies import verify_auth

router = APIRouter()
logger = get_logger(__name__)


def api_response(
    data: Any = None, error_code: str | None = None, error_message: str | None = None
) -> dict:
    """Create standardized API response.

    Args:
        data: Response data (for success)
        error_code: Error code (for failures)
        error_message: Error description (for failures)

    Returns:
        Standardized response dict
    """
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

# Initialize clearance engine and work tree coordinator
_state_store: "StateStore | None" = None
_clearance_engine: "ClearanceEngine | None" = None
_work_tree_coordinator: "WorktreeManager | None" = None


def get_state_store() -> "StateStore":
    """Get or initialize state store."""
    global _state_store
    if _state_store is None:
        from forge_harness.state_store import StateStore

        _state_store = StateStore()
        _state_store.connect()
    return _state_store


def get_clearance_engine() -> "ClearanceEngine":
    """Get or initialize clearance engine."""
    global _clearance_engine
    if _clearance_engine is None:
        from forge_harness.clearance_engine import ClearanceEngine

        forge_root = Path.cwd()
        _clearance_engine = ClearanceEngine(
            state_store=get_state_store(),
            forge_root=forge_root,
            github_repo=None,  # Auto-detect
        )
    return _clearance_engine


def get_work_tree_coordinator() -> "WorktreeManager":
    """Get or initialize worktree manager."""
    global _work_tree_coordinator
    if _work_tree_coordinator is None:
        from forge_harness.worktree_manager import WorktreeManager

        forge_root = Path.cwd()
        _work_tree_coordinator = WorktreeManager(repo_root=forge_root)
    return _work_tree_coordinator


class PrimeRegisterRequest(BaseModel):
    """Request body for /prime registration."""

    session_id: str
    agent_role: str
    domain: str
    project: str
    preferences: dict[str, Any] = Field(default_factory=dict)
    focus_tags: list[str] = Field(default_factory=list)


class PrimeCompleteRequest(BaseModel):
    """Request body for completing /prime assignment."""

    session_id: str
    assignment_id: str
    summary: str


# Event bus for publishing events - will be injected from app
_event_bus: "EventBus | None" = None


def get_event_bus() -> "EventBus":
    """Get or initialize event bus."""
    global _event_bus
    if _event_bus is None:
        from forge_harness.webhook_server.services.event_bus import get_event_bus as _get

        _event_bus = _get()
    return _event_bus


@router.post("/api/prime/register")
async def prime_register(
    body: PrimeRegisterRequest,
    _: None = Depends(verify_auth),
):
    """Register agent and request work clearance via /prime command.

    Returns:
        Clearance response with assignment or rejection reason.
    """
    from forge_harness.state_store import AgentRole, AgentSession, AgentStatus, AgentType

    # Parse agent role
    try:
        role = AgentRole(body.agent_role.lower())
    except ValueError:
        return JSONResponse(
            content=api_response(
                {
                    "success": False,
                    "message": f"Invalid agent role: {body.agent_role}. "
                    f"Valid roles: {', '.join([r.value for r in AgentRole])}",
                }
            ),
            status_code=400,
        )

    # Detect agent capabilities based on role
    from forge_harness.capability_discovery import get_agent_info
    role_map = {
        AgentRole.CTO: "architect-advisor",
        AgentRole.PM: "architect-advisor",
        AgentRole.BUILDER: "backend-engineer",
        AgentRole.QA: "qa-test-guardian",
        AgentRole.CONTENT: "content-agent",
    }
    cap_role = role_map.get(role, "backend-engineer")
    agent_info = get_agent_info(cap_role)
    capabilities = agent_info.skills if agent_info else []

    # Register agent session in state store
    state_store = get_state_store()
    session = AgentSession(
        session_id=body.session_id,
        agent_type=AgentType.CLAUDE_CODE,  # Default, could be parameterized
        agent_role=role,
        domain=body.domain,
        project=body.project,
        status=AgentStatus.ACTIVE,
        capabilities=capabilities,
        preferences=body.preferences,
    )

    if not state_store.register_agent(session):
        return JSONResponse(
            content=api_response(
                {
                    "success": False,
                    "message": "Failed to register agent session",
                }
            ),
            status_code=500,
        )

    # Request work clearance
    clearance_engine = get_clearance_engine()
    response = clearance_engine.request_clearance(
        session_id=body.session_id,
        agent_role=role,
        domain=body.domain,
        project=body.project,
        preferences=body.preferences,
        focus_tags=body.focus_tags,
    )

    # Publish agent registered event
    event_bus = get_event_bus()
    await event_bus.publish(
        "agent.registered",
        {
            "session_id": body.session_id,
            "role": body.agent_role,
            "domain": body.domain,
            "project": body.project,
            "assignment_id": response.assignment_id,
        },
    )

    # Prepare work tree if needed
    if response.success and response.work_tree_path:
        try:
            wt_manager = get_work_tree_coordinator()
            branch = f"prime/{body.session_id}"
            wt_path = wt_manager.create_worktree(branch=branch)
            response.work_tree_path = str(wt_path)
        except Exception as e:
            logger.warning(f"Failed to prepare work tree: {e}")
            # Fall back to main tree
            response.work_tree_path = None

    return JSONResponse(content=api_response(response.__dict__))


@router.post("/api/prime/complete")
async def prime_complete(
    body: PrimeCompleteRequest,
    _: None = Depends(verify_auth),
):
    """Mark assignment as complete via /prime command.

    Returns:
        Success/failure response.
    """
    clearance_engine = get_clearance_engine()
    success = clearance_engine.complete_assignment(
        session_id=body.session_id,
        assignment_id=body.assignment_id,
        summary=body.summary,
    )

    if not success:
        return JSONResponse(
            content=api_response(
                {
                    "success": False,
                    "message": "Failed to complete assignment",
                }
            ),
            status_code=400,
        )

    # Publish completion event
    event_bus = get_event_bus()
    await event_bus.publish(
        "agent.completed",
        {
            "session_id": body.session_id,
            "assignment_id": body.assignment_id,
            "summary": body.summary,
        },
    )

    return JSONResponse(
        content=api_response(
            {
                "success": True,
                "message": "Assignment completed successfully",
            }
        )
    )


@router.get("/api/prime/assignments")
async def prime_list_assignments(
    domain: str | None = Query(None),
    project: str | None = Query(None),
    status: str | None = Query(None),
    _: None = Depends(verify_auth),
):
    """List all work assignments.

    Returns:
        List of assignments filtered by domain/project/status.
    """
    from forge_harness.state_store import AssignmentStatus

    status_filter = AssignmentStatus(status) if status else None
    clearance_engine = get_clearance_engine()
    assignments = clearance_engine.get_assignments(
        domain=domain,
        project=project,
        status=status_filter,
    )

    return JSONResponse(
        content=api_response(
            {
                "assignments": [a.to_dict() for a in assignments],
                "count": len(assignments),
            }
        )
    )
