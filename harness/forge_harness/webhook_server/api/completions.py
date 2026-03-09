"""Completions API Endpoints

Provides completion reporting endpoint for agents.
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


class CompletionRequest(BaseModel):
    """Request body for completion reporting."""

    message: str = Field(..., min_length=1, max_length=2000)
    agent_id: str | None = Field(None, description="Optional agent/session identifier")


@router.post("/api/completions")
async def report_completion(
    body: CompletionRequest,
    _: None = Depends(verify_auth),
):
    """Report task completion. Publishes agent.completed to SSE for Command Center.

    Called by notify-completion.sh when agents finish work.
    Localhost allowed without auth (for local scripts).
    """
    event_bus = get_event_bus()
    await event_bus.publish(
        "agent.completed",
        {
            "message": body.message,
            "agent_id": body.agent_id,
            "timestamp": datetime.now(UTC).isoformat(),
        },
        source=body.agent_id or "notify-completion",
    )
    return JSONResponse(
        content=api_response(
            {
                "status": "published",
                "event_type": "agent.completed",
            }
        )
    )
