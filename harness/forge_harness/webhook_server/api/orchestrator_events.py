"""Orchestrator Events API Endpoints

Provides heartbeat and dispatch event reporting for dashboard visibility.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge_harness.webhook_server.services.event_bus import EventBus

from fastapi import APIRouter, Depends
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


# Event bus for publishing events - will be injected from app
_event_bus: "EventBus | None" = None


def get_event_bus() -> "EventBus":
    """Get or initialize event bus."""
    global _event_bus
    if _event_bus is None:
        from forge_harness.webhook_server.services.event_bus import get_event_bus as _get

        _event_bus = _get()
    return _event_bus


class OrchestratorEventRequest(BaseModel):
    """Request body for orchestrator events (heartbeat/dispatch)."""

    type: str = Field(..., description="heartbeat | dispatch")
    idle: int | None = Field(None, description="Idle agent count (heartbeat)")
    busy: int | None = Field(None, description="Busy agent count (heartbeat)")
    error: int | None = Field(None, description="Error agent count (heartbeat)")
    unknown: int | None = Field(None, description="Unknown status count (heartbeat)")
    not_found: int | None = Field(None, description="Not found count (heartbeat)")
    idle_names: str | None = Field(None, description="Idle agent names (heartbeat)")
    agent_id: str | None = Field(None, description="Target agent (dispatch)")
    task: str | None = Field(None, description="Task filename or ID (dispatch)")
    timestamp: str | None = Field(None, description="ISO timestamp (default: now)")


@router.post("/api/orchestrator/events")
async def report_orchestrator_event(
    body: OrchestratorEventRequest,
    _: None = Depends(verify_auth),
):
    """Report orchestrator heartbeat or dispatch. Publishes to SSE for Command Center.

    Called by orchestrator-heartbeat.sh for dashboard visibility.
    Localhost allowed without auth (for local scripts).
    """
    ts = body.timestamp or datetime.now(UTC).isoformat()
    event_type = f"orchestrator.{body.type}"
    data: dict[str, Any] = {
        "type": body.type,
        "timestamp": ts,
    }
    if body.type == "heartbeat":
        data.update(
            {
                "idle": body.idle or 0,
                "busy": body.busy or 0,
                "error": body.error or 0,
                "unknown": body.unknown or 0,
                "not_found": body.not_found or 0,
                "idle_names": body.idle_names or "",
            }
        )
    elif body.type == "dispatch":
        data.update(
            {
                "agent_id": body.agent_id or "",
                "task": body.task or "",
            }
        )
    event_bus = get_event_bus()
    await event_bus.publish(event_type, data, source="orchestrator-heartbeat")
    return JSONResponse(
        content=api_response(
            {
                "status": "published",
                "event_type": event_type,
            }
        )
    )
