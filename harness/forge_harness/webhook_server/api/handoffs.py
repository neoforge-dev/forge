"""
Handoffs API Router

Handles agent handoff management for session continuity.
Handoffs allow transferring work from one agent to another with
full context preservation.

Endpoints:
- GET /api/handoffs                    - List all handoffs (optional status filter)
- POST /api/handoffs                    - Create a new handoff
- POST /api/handoffs/{handoff_id}/accept - Accept a handoff
- POST /api/handoffs/{handoff_id}/reject - Reject a handoff
- POST /api/handoffs/{handoff_id}/complete - Complete a handoff
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from forge_harness.webhook_server.api.health import get_state_store  # noqa: F401

# For test compatibility - expose get_state_store from health module
# For test compatibility - import from health module
from forge_harness.webhook_server.core.dependencies import verify_auth
from forge_harness.webhook_server.services.audit import get_audit_logger

router = APIRouter()


# =============================================================================
# Request Models
# =============================================================================


class HandoffRequest(BaseModel):
    """Request body for creating a handoff."""

    from_agent: str
    to_agent: str
    task_description: str
    files: list[str] = []
    priority: str = "medium"


class HandoffAcceptRequest(BaseModel):
    """Request body for accepting a handoff."""

    accepting_agent: str | None = None


class HandoffRejectRequest(BaseModel):
    """Request body for rejecting a handoff."""

    reason: str


class HandoffCompleteRequest(BaseModel):
    """Request body for completing a handoff."""

    notes: str | None = None


# =============================================================================
# Helper Functions
# =============================================================================


def api_response(
    data: Any = None, error_code: str | None = None, error_message: str | None = None
) -> dict[str, Any]:
    """Create a standardized API response."""
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


async def get_handoff_handler():
    """Get the handoff handler singleton."""
    from forge_harness.webhook_server.handlers.handoff_handler import get_handoff_handler

    return get_handoff_handler()


async def get_event_bus():
    """Get the event bus singleton."""
    from forge_harness.webhook_server.services.event_bus import get_event_bus

    return get_event_bus()


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


def _get_handoff_key(handoff_id: str) -> str:
    """Generate Redis key for a handoff."""
    return f"forge:handoffs:{handoff_id}"


import json


def _serialize_handoff(handoff: dict[str, Any]) -> dict[str, str]:
    """Serialize handoff dict for Redis storage (convert values to strings)."""
    result: dict[str, str] = {}
    for key, value in handoff.items():
        if value is None:
            result[key] = ""
        elif isinstance(value, list):
            result[key] = json.dumps(value)
        else:
            result[key] = str(value)
    return result


def _deserialize_handoff(data: dict[bytes, bytes]) -> dict[str, Any]:
    """Deserialize handoff data from Redis (convert bytes to strings)."""
    if not data:
        return {}
    result: dict[str, Any] = {}
    for key, value in data.items():
        key_str = key.decode() if isinstance(key, bytes) else str(key)
        value_str = value.decode() if isinstance(value, bytes) else str(value)
        # Parse JSON for list fields
        if key_str in ("files",) and value_str:
            try:
                result[key_str] = json.loads(value_str)
            except (json.JSONDecodeError, ValueError):
                result[key_str] = []
        else:
            result[key_str] = value_str
    return result


# =============================================================================
# API Endpoints
# =============================================================================


@router.get("/api/handoffs")
async def list_handoffs(
    status: str | None = Query(None, description="Filter by status"),
    from_agent: str | None = Query(None, description="Filter by from_agent"),
    to_agent: str | None = Query(None, description="Filter by to_agent"),
    limit: int = Query(50, ge=1, le=200, description="Maximum results"),
    _=Depends(verify_auth),
):
    """List all handoffs with optional status filter."""
    handler = await get_handoff_handler()
    handoffs = await handler.list_handoffs(
        status=status,
        from_agent=from_agent,
        to_agent=to_agent,
        limit=limit,
    )
    return JSONResponse(
        content=api_response(
            {
                "handoffs": handoffs,
                "count": len(handoffs),
            }
        )
    )


@router.post("/api/handoffs")
async def create_handoff(
    body: HandoffRequest,
    request: Request,
    _=Depends(verify_auth),
):
    """Create a new handoff."""
    handler = await get_handoff_handler()
    try:
        handoff = await handler.create_handoff(
            from_agent=body.from_agent,
            to_agent=body.to_agent,
            task_description=body.task_description,
            files=body.files,
            priority=body.priority,
        )

        # Emit SSE event
        event_bus = await get_event_bus()
        if event_bus:
            await event_bus.publish("handoff.created", handoff)

        # Audit log
        handoff_id = (
            handoff.get("id", "unknown")
            if isinstance(handoff, dict)
            else getattr(handoff, "id", "unknown")
        )
        ip_address, user_agent = _extract_request_context(request)
        audit_logger = get_audit_logger()
        await audit_logger.log_handoff_operation(
            handoff_id=str(handoff_id),
            actor={"id": "api", "type": "system"},
            context={
                "from_agent": body.from_agent,
                "to_agent": body.to_agent,
                "priority": body.priority,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return JSONResponse(content=api_response(handoff))
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content=api_response(error_code="CREATE_FAILED", error_message=str(e)),
        )


@router.post("/api/handoffs/{handoff_id}/accept")
async def accept_handoff(
    handoff_id: str,
    body: HandoffAcceptRequest | None = None,
    _=Depends(verify_auth),
):
    """Accept a handoff."""
    handler = await get_handoff_handler()
    accepting_agent = body.accepting_agent if body else None

    try:
        handoff = await handler.accept_handoff(handoff_id, accepting_agent)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")

    # Emit SSE event
    event_bus = await get_event_bus()
    if event_bus:
        await event_bus.publish("handoff.accepted", handoff)

    return JSONResponse(content=api_response(handoff))


@router.post("/api/handoffs/{handoff_id}/reject")
async def reject_handoff(
    handoff_id: str,
    body: HandoffRejectRequest,
    _=Depends(verify_auth),
):
    """Reject a handoff."""
    handler = await get_handoff_handler()
    try:
        handoff = await handler.reject_handoff(handoff_id, body.reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")

    # Emit SSE event
    event_bus = await get_event_bus()
    if event_bus:
        await event_bus.publish("handoff.rejected", handoff)

    return JSONResponse(content=api_response(handoff))


@router.post("/api/handoffs/{handoff_id}/complete")
async def complete_handoff(
    handoff_id: str,
    body: HandoffCompleteRequest | None = None,
    _=Depends(verify_auth),
):
    """Complete a handoff."""
    handler = await get_handoff_handler()
    notes = body.notes if body else None

    try:
        handoff = await handler.complete_handoff(handoff_id, notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")

    # Emit SSE event
    event_bus = await get_event_bus()
    if event_bus:
        await event_bus.publish("handoff.completed", handoff)

    return JSONResponse(content=api_response(handoff))
