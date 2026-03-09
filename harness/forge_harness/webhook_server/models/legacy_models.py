"""Legacy Pydantic Models — extracted from webhook_server_main.py.

These models were previously defined inline inside create_app(). They are
used by the legacy route handlers (routes prefixed with /api/legacy/ and
a few non-legacy paths like /api/agents/fleet/*).

These are NOT the canonical models — the canonical (modern) routers in
webhook_server/api/*.py define their own request/response schemas. These
exist purely for backward compatibility of the legacy inline routes.
"""

from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Fleet control
# ---------------------------------------------------------------------------


class FleetPauseRequest(BaseModel):
    """Request body for pausing the fleet or specific agents."""

    reason: str | None = None
    duration_minutes: int = Field(30, ge=5, le=120)
    agents: list[str] | None = None


class FleetResumeRequest(BaseModel):
    """Request body for resuming the fleet or specific agents."""

    agents: list[str] | None = None


class AgentPauseRequest(BaseModel):
    """Request body for pausing a single agent."""

    reason: str | None = None
    duration_minutes: int = Field(30, ge=5, le=120)


class FleetActionAgentEntry(BaseModel):
    """Agent entry in FleetActionResponse."""

    id: str
    name: str | None = None
    previous_status: str | None = None
    new_status: str | None = None


class FleetActionResponse(BaseModel):
    """Response model for fleet-wide control actions (FC-002 / FC-005)."""

    success: bool
    action: str  # "pause_all" | "resume_all" | "broadcast"
    affected_agents: int
    agents: list[FleetActionAgentEntry]
    timestamp: str


class FleetBroadcastRequest(BaseModel):
    """Fleet-wide broadcast request with priority."""

    message: str
    priority: str = "normal"


# ---------------------------------------------------------------------------
# Standardised API response wrapper
# ---------------------------------------------------------------------------


class APIResponse(BaseModel, Generic[T]):  # noqa: UP046
    """Standardized API response format for Command Center.

    All API endpoints should use this format for consistency:
    {
        "success": true/false,
        "data": <actual response data>,
        "error": null or {"code": "ERROR_CODE", "message": "description"},
        "timestamp": "2024-01-18T12:00:00Z"
    }
    """

    success: bool = True
    data: T | None = None
    error: dict | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


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


def error_code_from_status(status_code: int) -> str:
    """Map HTTP status codes to standard error codes."""
    if status_code == 400:
        return "bad_request"
    if status_code == 401:
        return "unauthorized"
    if status_code == 403:
        return "forbidden"
    if status_code == 404:
        return "not_found"
    if status_code == 409:
        return "conflict"
    if status_code == 422:
        return "validation_error"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "internal_error"
    return "error"


# ---------------------------------------------------------------------------
# Agent request / response models
# ---------------------------------------------------------------------------


class AgentMessageRequest(BaseModel):
    """Backward-compatible request body for sending message to agent.

    Supports both ``content`` and ``message`` fields so existing callers
    do not need to change their payloads.
    """

    type: str = "instruction"
    content: str | None = None
    message: str | None = None  # Backward compatibility alias

    def get_content(self) -> str:
        """Return message content from either field."""
        return self.content or self.message or ""


class AgentProgressRequest(BaseModel):
    """Request body for agent progress update."""

    progress: int = Field(ge=0, le=100)
    current_task: str | None = None
    files_modified: list[str] | None = None
    token_usage: dict[str, int] | None = None


class AgentCompleteRequest(BaseModel):
    """Request body for agent completion."""

    summary: str | None = None


class AgentActionResponse(BaseModel):
    """Response model for single-agent control actions (FC-001)."""

    success: bool
    agent_id: str
    action: str  # "pause" | "resume" | "kill" | "message" | "handoff"
    previous_status: str | None = None
    new_status: str | None = None
    message: str | None = None
    timestamp: str


class KillAgentRequest(BaseModel):
    """Request body for killing an agent."""

    reason: str | None = None


class BroadcastRequest(BaseModel):
    """Request body for broadcast message."""

    type: str = "instruction"
    content: str


class ActivityEvent(BaseModel):
    """An agent activity event reported by Command Center clients.

    This is normalized into the SSEEvent shape used by the frontend:
    {
        "id": str,
        "type": str,
        "timestamp": str,
        "source": str,
        "data": {...}
    }
    """

    agent_id: str
    event_type: str  # e.g. "agent.progress", "agent.status", "error"
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class HandoffRequest(BaseModel):
    """Request body for triggering agent handoff."""

    target_agent_id: str | None = None  # Optional: specific agent to hand off to
    reason: str | None = None
    include_context: bool = True


# ---------------------------------------------------------------------------
# PRIME models
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class ConfigUpdateRequest(BaseModel):
    """Request body for updating configuration."""

    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


# ---------------------------------------------------------------------------
# SSE / completions / orchestrator models
# ---------------------------------------------------------------------------


class CompletionRequest(BaseModel):
    """Request body for task completion notification."""

    message: str = Field(..., min_length=1, max_length=2000)
    agent_id: str | None = Field(None, description="Optional agent/session identifier")


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
