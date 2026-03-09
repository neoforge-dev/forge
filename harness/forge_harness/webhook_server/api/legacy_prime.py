"""Legacy PRIME Command endpoints — extracted from webhook_server_main.py.

Handles /api/prime/register, /api/prime/complete, /api/prime/assignments routes.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.legacy_models import (
    PrimeCompleteRequest,
    PrimeRegisterRequest,
    api_response,
)
from forge_harness.webhook_server.services.event_bus import get_event_bus

logger = get_logger(__name__)

router = APIRouter(tags=["legacy-prime"])

# Lazy-initialised singletons (same pattern as the original monolith)
_state_store: Any = None
_clearance_engine: Any = None
_work_tree_coordinator: Any = None


def _get_state_store() -> Any:
    """Get or initialize state store."""
    global _state_store
    if _state_store is None:
        from forge_harness.state_store import StateStore

        _state_store = StateStore()
        _state_store.connect()
    return _state_store


def _get_clearance_engine() -> Any:
    """Get or initialize clearance engine."""
    global _clearance_engine
    if _clearance_engine is None:
        from forge_harness.clearance_engine import ClearanceEngine

        forge_root = Path.cwd()
        _clearance_engine = ClearanceEngine(
            state_store=_get_state_store(),
            forge_root=forge_root,
            github_repo=None,
        )
    return _clearance_engine


def _get_work_tree_coordinator() -> Any:
    """Get or initialize work tree coordinator."""
    global _work_tree_coordinator
    if _work_tree_coordinator is None:
        from forge_harness.work_tree_manager import WorkTreeCoordinator

        forge_root = Path.cwd()
        _work_tree_coordinator = WorkTreeCoordinator(
            state_store=_get_state_store(),
            forge_root=forge_root,
        )
    return _work_tree_coordinator


@router.post("/api/prime/register")
async def prime_register(body: PrimeRegisterRequest, request: Request):
    """Register agent and request work clearance via /prime command."""
    from forge_harness.state_store import AgentRole, AgentSession, AgentStatus, AgentType

    _event_bus = get_event_bus()

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

    state_store = _get_state_store()

    session = AgentSession(
        session_id=body.session_id,
        agent_type=AgentType.CLAUDE_CODE,
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

    clearance_engine = _get_clearance_engine()
    response = clearance_engine.request_clearance(
        session_id=body.session_id,
        agent_role=role,
        domain=body.domain,
        project=body.project,
        preferences=body.preferences,
        focus_tags=body.focus_tags,
    )

    await _event_bus.publish(
        "agent.registered",
        {
            "session_id": body.session_id,
            "role": body.agent_role,
            "domain": body.domain,
            "project": body.project,
            "assignment_id": response.assignment_id,
        },
    )

    if response.success and response.work_tree_path:
        work_tree_coordinator = _get_work_tree_coordinator()

        success, work_path, message = work_tree_coordinator.prepare_work_environment(
            session_id=body.session_id,
            files=response.files,
            needs_parallel_work=True,
        )

        if not success:
            logger.warning(f"Failed to prepare work tree: {message}")
            response.work_tree_path = None

    return JSONResponse(content=api_response(response.__dict__))


@router.post("/api/prime/complete")
async def prime_complete(body: PrimeCompleteRequest, request: Request):
    """Mark assignment as complete via /prime command."""
    _event_bus = get_event_bus()
    clearance_engine = _get_clearance_engine()
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

    await _event_bus.publish(
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
    request: Request = None,
):
    """List all work assignments."""
    from forge_harness.state_store import AssignmentStatus

    status_filter = AssignmentStatus(status) if status else None
    clearance_engine = _get_clearance_engine()
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
