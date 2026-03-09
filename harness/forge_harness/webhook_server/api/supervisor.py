"""Supervisor API Endpoints

REST API endpoints for supervisor status monitoring.
Extracted from webhook_server_main.py for better modularity.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.core.dependencies import verify_auth
from forge_harness.webhook_server.services.agent_registry import (
    AgentRegistry,
    get_agent_registry,
)

logger = get_logger(__name__)

router = APIRouter()


def get_agent_registry_dep() -> AgentRegistry:
    """Dependency to get agent registry instance."""
    return get_agent_registry()


def api_response(data: Any = None, error_code: str | None = None, error_message: str | None = None) -> dict[str, Any]:
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


@router.get("/api/supervisor/status")
async def get_supervisor_status(
    _: None = Depends(verify_auth),
    agent_registry: AgentRegistry = Depends(get_agent_registry_dep),
):
    """Get agent supervisor health status.

    Returns information about the agent supervisor's health,
    monitored agents, and restart counts.

    Returns:
        APIResponse with:
        - running: bool (supervisor is active)
        - monitored_agents: Number of agents being monitored
        - restarts_today: Number of restarts today
        - health: Overall health status
    """
    try:
        # Get active agents from registry
        active_agents = agent_registry.list_active()
        monitored_count = len(active_agents)

        # Check if supervisor is running by looking at recent agent activity
        now = datetime.now(UTC)
        recent_activity = False

        for agent in active_agents:
            if agent.last_activity:
                age = (now - agent.last_activity).total_seconds()
                if age < 60:  # Activity within last minute
                    recent_activity = True
                    break

        # Count restarts today (look at agent creation times)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        restarts_today = 0

        for agent in active_agents:
            if agent.registered_at >= today_start:
                restarts_today += 1

        # Determine health status
        if monitored_count == 0:
            health = "idle"
        elif recent_activity:
            health = "healthy"
        else:
            health = "stale"

        return JSONResponse(
            content=api_response(
                {
                    "running": monitored_count > 0,
                    "monitored_agents": monitored_count,
                    "restarts_today": restarts_today,
                    "health": health,
                    "agents": [
                        {
                            "id": agent.id,
                            "role": agent.role,
                            "project": agent.project,
                            "status": agent.status,
                            "last_activity": agent.last_activity.isoformat()
                            if agent.last_activity
                            else None,
                        }
                        for agent in active_agents
                    ],
                }
            )
        )

    except Exception as e:
        logger.error(f"Error reading supervisor status: {e}")
        return JSONResponse(
            content=api_response(
                error_code="READ_ERROR",
                error_message=f"Failed to read supervisor status: {str(e)}",
            )
        )
