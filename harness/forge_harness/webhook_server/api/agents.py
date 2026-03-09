"""Agent Registry API Endpoints

REST API endpoints for agent registry management.
Extracted from webhook_server_main.py for better modularity.
"""

import os
import platform
import shutil
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.requests import Request as StarletteRequest

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.core.dependencies import verify_auth
from forge_harness.webhook_server.models.sse_events import SSEEventType
from forge_harness.webhook_server.services.audit import get_audit_logger
from forge_harness.webhook_server.services.event_emitter import get_event_emitter

logger = get_logger(__name__)

router = APIRouter()

# Root of the FORGE repo — can be overridden via FORGE_ROOT env var.
FORGE_ROOT: Path = Path(os.environ.get("FORGE_ROOT", str(Path(__file__).resolve().parents[4])))


# =============================================================================
# Request Models
# =============================================================================


class AgentRegisterRequest(BaseModel):
    """Request body for agent registration."""

    role: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Agent role (e.g., 'backend-engineer', 'frontend-builder')",
    )
    project: str = Field(..., min_length=1, max_length=200, description="Project name")
    task: str = Field(..., min_length=1, max_length=1000, description="Task description")
    name: str | None = Field(None, max_length=200, description="Agent display name")
    domain: str | None = Field(
        None,
        pattern=r"^[a-z0-9\-]+$",
        max_length=100,
        description="Domain for hierarchy (lowercase, alphanumeric, hyphens)",
    )
    parent_id: str | None = Field(
        None, max_length=100, description="Parent agent ID for hierarchy tracking"
    )
    tmux_session: str | None = Field(
        None,
        pattern=r"^[a-zA-Z0-9\-_]+:[a-zA-Z0-9\-_]+$",
        description="tmux session:window location",
    )
    skills: list[str] | None = Field(None, description="Attached skills")

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        """Validate role is not empty or whitespace."""
        if not v or not v.strip():
            raise ValueError("role cannot be empty or whitespace")
        return v.strip()

    @field_validator("project")
    @classmethod
    def validate_project(cls, v: str) -> str:
        """Validate project is not empty or whitespace."""
        if not v or not v.strip():
            raise ValueError("project cannot be empty or whitespace")
        return v.strip()

    @field_validator("task")
    @classmethod
    def validate_task(cls, v: str) -> str:
        """Validate task is not empty or whitespace."""
        if not v or not v.strip():
            raise ValueError("task cannot be empty or whitespace")
        return v.strip()


class AgentProgressRequest(BaseModel):
    """Request body for agent progress update."""

    progress: int = Field(..., ge=0, le=100, description="Progress percentage")
    current_task: str | None = Field(None, description="Current task being worked on")
    files_modified: list[str] | None = Field(None, description="Files modified in this update")


class AgentMessageRequest(BaseModel):
    """Request body for sending message to agent."""

    message: str = Field(..., min_length=1, max_length=10000, description="Message to send")


class AgentPauseRequest(BaseModel):
    """Request body for pausing an agent."""

    reason: str | None = None
    duration_minutes: int = Field(30, ge=5, le=120)


class NodeDispatchRequest(BaseModel):
    """Request body for dispatching work to a node."""

    agent_type: str = Field(..., min_length=1, max_length=100)
    task: str = Field(..., min_length=1, max_length=2000)
    priority: str = Field("normal", pattern=r"^(normal|high|urgent)$")
    project: str | None = Field(None, max_length=200)
    domain: str | None = Field(None, max_length=100)
    timeout_minutes: int | None = Field(None, ge=1, le=480)


# =============================================================================
# Helper Functions
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


def _to_iso(v: Any) -> str | None:
    """Convert datetime or value to ISO string; return None for missing."""
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v) if v else None


def normalize_agent_dict(agent_dict: dict) -> dict:
    """Normalize agent payloads to API_CONTRACTS.md GET /api/agents format.

    Field names: session_id, agent_role, agent_name (never name/role).
    Null handling: optional fields use None; required strings use "".
    All contract fields are always present for consistent responses.

    IMPORTANT: session_id is NEVER empty - falls back to agent role or UUID.
    """
    raw = dict(agent_dict)
    # session_id must NEVER be empty - fallback chain ensures valid ID
    session_id = (
        raw.get("session_id")
        or raw.get("id")
        or raw.get("tmux_session")  # Fall back to tmux session if available
        or f"agent-{raw.get('role', 'unknown')}"  # Final fallback
    )
    if not session_id or session_id.strip() == "":
        session_id = f"agent-{raw.get('role', 'unknown')}"
    agent_role = raw.get("agent_role") or raw.get("role") or ""
    agent_name = raw.get("agent_name") or raw.get("name")
    if agent_name is None or agent_name == "":
        agent_name = agent_role or "agent"
    domain = raw.get("domain") if raw.get("domain") not in (None, "") else None
    project = raw.get("project") or ""
    current_task = raw.get("current_task") or raw.get("task") or ""
    focus_tags = raw.get("focus_tags") or raw.get("skills") or []
    if not isinstance(focus_tags, list):
        focus_tags = []
    status = (raw.get("status") or "active").lower()
    progress = raw.get("progress_pct") if "progress_pct" in raw else raw.get("progress")
    progress_pct = float(progress) if progress is not None else 0.0
    tasks_completed = int(raw.get("tasks_completed", 0) or 0)
    tasks_remaining = int(raw.get("tasks_remaining", 0) or 0)
    registered_at = raw.get("registered_at") or raw.get("started_at")
    started_at = _to_iso(registered_at) if registered_at else None
    last_activity = _to_iso(raw.get("last_activity"))
    token_usage_raw = raw.get("token_usage")
    if isinstance(token_usage_raw, dict):
        token_usage = sum(v for v in token_usage_raw.values() if isinstance(v, (int, float)))
    else:
        token_usage = int(token_usage_raw) if token_usage_raw is not None else 0
    api_calls = int(raw.get("api_calls") or raw.get("messages_count", 0) or 0)

    result: dict[str, Any] = {
        "session_id": session_id,
        "agent_role": agent_role,
        "agent_name": agent_name,
        "domain": domain,
        "project": project,
        "current_task": current_task,
        "focus_tags": focus_tags,
        "status": status,
        "progress_pct": progress_pct,
        "tasks_completed": tasks_completed,
        "tasks_remaining": tasks_remaining,
        "started_at": started_at,
        "last_activity": last_activity,
        "token_usage": token_usage,
        "api_calls": api_calls,
        "pending_approval_id": None,
        "parent_id": raw.get("parent_id"),
        "children": raw.get("children", []),
    }
    if "source" in raw:
        result["source"] = raw["source"]
    return result


def _normalize_node_id(raw: str) -> str:
    """Normalize host/node names into stable API IDs."""
    return raw.strip().lower().replace("_", "-").replace(" ", "-")


def _detect_ram_gb() -> int:
    """Best-effort RAM detection without optional dependencies."""
    try:
        if hasattr(os, "sysconf"):
            page_size = os.sysconf("SC_PAGE_SIZE")
            total_pages = os.sysconf("SC_PHYS_PAGES")
            if (
                isinstance(page_size, int)
                and isinstance(total_pages, int)
                and page_size > 0
                and total_pages > 0
            ):
                return int((page_size * total_pages) / (1024**3))
    except Exception:
        pass
    return 0


def _get_cpu_load_pct() -> int:
    """Convert 1m load average to approximate CPU utilization percentage."""
    try:
        load_1m = os.getloadavg()[0]
        cpu_count = max(os.cpu_count() or 1, 1)
        return int(min(100.0, max(0.0, (load_1m / cpu_count) * 100.0)))
    except Exception:
        return 0


def _get_disk_usage_pct() -> int:
    """Get disk usage percentage for the current filesystem."""
    try:
        usage = shutil.disk_usage(os.getcwd())
        if usage.total <= 0:
            return 0
        return int((usage.used / usage.total) * 100)
    except Exception:
        return 0


def _default_capabilities() -> dict[str, bool]:
    """Detect local node runtime capabilities."""
    return {
        "claude": shutil.which("claude") is not None,
        "codex": shutil.which("codex") is not None,
        "gemini": shutil.which("gemini") is not None,
        "kimi": shutil.which("kimi") is not None,
        "opencode": shutil.which("opencode") is not None,
        "ollama": shutil.which("ollama") is not None,
        "docker": shutil.which("docker") is not None,
        "ios_simulator": shutil.which("xcrun") is not None,
        "gpu": shutil.which("nvidia-smi") is not None,
    }


def _build_local_node(agent_registry: Any) -> dict[str, Any]:
    """Build NodeStatus payload from local host and active registry agents."""
    host = socket.gethostname() or "local-node"
    node_id = _normalize_node_id(os.environ.get("FORGE_NODE_ID", host))
    node_name = os.environ.get("FORGE_NODE_NAME", host)

    cpu_load = _get_cpu_load_pct()
    disk_usage = _get_disk_usage_pct()

    try:
        load_average = round(float(os.getloadavg()[0]), 2)
    except Exception:
        load_average = 0.0

    agents_payload: list[dict[str, Any]] = []
    for agent in agent_registry.list_active():
        normalized = normalize_agent_dict(agent.to_dict())
        agents_payload.append(
            {
                "id": normalized.get("session_id") or normalized.get("id") or "unknown",
                "name": normalized.get("agent_name") or normalized.get("name") or "agent",
                "role": normalized.get("agent_role") or normalized.get("role") or "agent",
                "status": normalized.get("status") or "unknown",
                "project": normalized.get("project") or "",
                "task": normalized.get("current_task") or normalized.get("task") or "",
                "progress": int(normalized.get("progress_pct") or normalized.get("progress") or 0),
                "context_percentage": 0,
            }
        )

    status = "online"
    alerts: list[dict[str, str]] = []
    if cpu_load >= 85 or disk_usage >= 90:
        status = "degraded"
        alerts.append(
            {
                "level": "warning",
                "message": "Node under sustained resource pressure",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

    return {
        "node_id": node_id,
        "name": node_name,
        "status": status,
        "agent_count": len(agents_payload),
        "cpu_load": cpu_load,
        "ram_usage": 0,
        "last_heartbeat": datetime.now(UTC).isoformat(),
        "latency_ms": 1,
        "agents": agents_payload,
        "load_average": load_average,
        "disk_usage": disk_usage,
        "alerts": alerts,
        "capabilities": _default_capabilities(),
        "specs": {
            "cpu": platform.processor() or platform.machine() or "unknown",
            "ram_gb": _detect_ram_gb(),
            "os": platform.system(),
            "cores": os.cpu_count() or 1,
        },
    }


def _select_dispatch_agent(
    agents: list[dict[str, Any]],
    requested_agent_type: str,
) -> dict[str, Any] | None:
    """Pick the best available local agent for a dispatch request."""
    if not agents:
        return None

    requested = requested_agent_type.strip().lower()
    filtered = [
        agent
        for agent in agents
        if requested in (agent.get("role") or "").lower()
        or requested in (agent.get("name") or "").lower()
    ]
    candidates = filtered if filtered else agents

    for target_status in ("idle", "waiting", "active"):
        for candidate in candidates:
            if (candidate.get("status") or "").lower() == target_status:
                return candidate
    return candidates[0]


async def get_agent_registry():
    """Get the agent registry."""
    from forge_harness.webhook_server_main import get_agent_registry as _get_agent_registry

    return _get_agent_registry()


async def get_state_store():
    """Get the state store."""
    from forge_harness.state_store import StateStore

    # Try to get existing instance or create new one
    state_store = StateStore()
    state_store.connect()
    return state_store


async def get_event_bus():
    """Get the event bus singleton."""
    from forge_harness.webhook_server.services.event_bus import get_event_bus as _get_event_bus

    return _get_event_bus()


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


@router.post("/api/agents/register")
async def register_agent(
    body: AgentRegisterRequest,
    request: Request,
    _=Depends(verify_auth),
):
    """Register a new agent session with hierarchy support.

    Registers agent in both in-memory registry and persistent StateStore
    for consistency between Dashboard TUI and Command Center.
    """
    agent_registry = await get_agent_registry()
    agent = agent_registry.register(
        role=body.role,
        project=body.project,
        task=body.task,
        name=body.name,
        domain=body.domain,
        parent_id=body.parent_id,
        tmux_session=body.tmux_session,
        skills=body.skills,
    )

    # Also register in StateStore for persistence
    try:
        state_store = await get_state_store()
        if state_store.is_connected():
            from forge_harness.state_store import AgentRole, AgentStatus, AgentType
            from forge_harness.state_store import AgentSession as StateStoreSession

            try:
                agent_role = AgentRole(body.role.lower())
            except ValueError:
                agent_role = AgentRole.BUILDER

            store_session = StateStoreSession(
                session_id=agent.id,
                agent_type=AgentType.CLAUDE_CODE,
                agent_role=agent_role,
                domain=body.domain or "unknown",
                project=body.project,
                status=AgentStatus.ACTIVE,
                current_task=body.task,
                capabilities=body.skills or [],
            )
            state_store.register_agent(store_session)
            logger.debug(f"Agent {agent.id} also registered in StateStore")
    except Exception as e:
        logger.warning(f"Failed to register agent in StateStore: {e}")

    # Emit SSE event via EventBus (for streaming clients)
    event_bus = await get_event_bus()
    await event_bus.publish("agent.registered", agent.to_dict())

    # Also emit via EventEmitter (for ring buffer / polling API)
    try:
        emitter = get_event_emitter()
        # Emit task_created with task info from registration
        task_payload = {
            "task_id": agent.id,
            "task": body.task,
            "agent_id": agent.id,
            "role": body.role,
            "project": body.project,
        }
        emitter.emit(SSEEventType.task_created, task_payload, source="agents-api")
        # Emit agent_busy to indicate agent is now working
        emitter.emit(SSEEventType.agent_busy, agent.to_dict(), source="agents-api")
    except Exception as e:
        logger.warning(f"Failed to emit agent.registered events to EventEmitter: {e}")

    # Audit log
    ip_address, user_agent = _extract_request_context(request)
    audit_logger = get_audit_logger()
    await audit_logger.log_agent_registration(
        agent_id=agent.id,
        actor={"id": "api", "type": "system"},
        context={
            "role": body.role,
            "project": body.project,
            "domain": body.domain,
            "tmux_session": body.tmux_session,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return JSONResponse(content=api_response(normalize_agent_dict(agent.to_dict())))


@router.get("/api/agents")
async def list_agents(
    _=Depends(verify_auth),
):
    """List all active agents.

    Returns agents from registry, state store, and tmux session tracker,
    deduplicated by session_id. Matches /api/portfolio agent counting.
    """
    agent_registry = await get_agent_registry()
    seen_ids: set[str] = set()
    agents_out: list[dict] = []

    # 1. Registry agents
    for a in agent_registry.list_active():
        d = normalize_agent_dict(a.to_dict())
        sid = d.get("session_id", "")
        if sid:
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
        agents_out.append(d)

    # 2. State store agents (matches portfolio_service counting)
    try:
        state_store = await get_state_store()
        if state_store.is_connected():
            for sa in state_store.get_active_agents():
                if sa.session_id in seen_ids:
                    continue
                seen_ids.add(sa.session_id)
                store_dict = normalize_agent_dict(
                    {
                        "session_id": sa.session_id,
                        "role": getattr(sa, "role", "agent"),
                        "name": getattr(sa, "name", sa.session_id),
                        "domain": getattr(sa, "domain", ""),
                        "project": getattr(sa, "project", ""),
                        "task": getattr(sa, "current_task", ""),
                        "status": getattr(sa, "status", "active"),
                        "source": "state_store",
                    }
                )
                agents_out.append(store_dict)
    except Exception as e:
        logger.debug(f"State store agent list: {e}")

    # 3. Tmux session tracker agents
    try:
        from forge_harness.session_tracker import get_session_tracker

        status_map = {
            "active": "active",
            "idle": "idle",
            "error": "error",
            "completed": "idle",
            "unknown": "idle",
        }
        tracker = get_session_tracker()
        for s in tracker.get_all_sessions():
            if s.session_name in seen_ids:
                continue
            seen_ids.add(s.session_name)
            tmux_dict = {
                "session_id": s.session_name,
                "role": s.agent_type or s.window_name,
                "name": s.window_name,
                "domain": s.domain,
                "project": s.project or "",
                "task": s.current_task or "",
                "parent_id": None,
                "children": [],
                "tmux_session": s.session_name,
                "skills": [],
                "status": status_map.get(s.status, "idle"),
                "progress": 0,
                "current_task": s.current_task,
                "files_modified": [],
                "token_usage": {},
                "messages_count": 0,
                "registered_at": s.started_at or datetime.now(UTC).isoformat(),
                "last_activity": s.last_activity or datetime.now(UTC).isoformat(),
                "is_stale": False,
                "source": "tmux",
            }
            agents_out.append(normalize_agent_dict(tmux_dict))
    except Exception as e:
        logger.debug(f"Session tracker list: {e}")

    # 4. Enrich with fleet sidecar data (.forge/heartbeat/agents/*.json)
    #    This provides real context_pct, task_id, and status from the FSM.
    try:
        from .fleet_summary import _read_fleet_agents

        fleet_agents = _read_fleet_agents()
        fleet_lookup: dict[str, dict] = {}
        for fa in fleet_agents:
            agent_name = fa.get("agent", "")
            if agent_name:
                fleet_lookup[agent_name] = fa

        # Enrich existing agents with sidecar data
        enriched_ids: set[str] = set()
        for agent_dict in agents_out:
            agent_name = agent_dict.get("agent_name", "")
            # Try exact match first, then suffix match (pool-t1-minimax → minimax)
            fa_match = fleet_lookup.get(agent_name)
            if not fa_match:
                for sidecar_name, fa_candidate in fleet_lookup.items():
                    if agent_name.endswith(sidecar_name) or sidecar_name.endswith(agent_name):
                        fa_match = fa_candidate
                        agent_name = sidecar_name  # use sidecar name for enriched_ids
                        break
            if fa_match:
                agent_dict["current_task"] = fa_match.get("task_id") or agent_dict.get("current_task", "")
                agent_dict["progress_pct"] = float(fa_match.get("context_pct", 0))
                agent_dict["is_stale"] = fa_match.get("is_stale", False)
                sidecar_status = fa_match.get("status", "")
                if sidecar_status == "working":
                    agent_dict["status"] = "active"
                elif sidecar_status in ("idle", "error"):
                    agent_dict["status"] = sidecar_status
                # Fix agent_role from "unknown" to real name
                if agent_dict.get("agent_role") in ("unknown", ""):
                    agent_dict["agent_role"] = fa_match.get("agent", agent_name)
                agent_dict["source"] = agent_dict.get("source", "") + "+sidecar"
                enriched_ids.add(agent_name)

        # Add fleet agents not already present
        for agent_name, fa in fleet_lookup.items():
            if agent_name not in enriched_ids and agent_name not in seen_ids:
                sidecar_status = fa.get("status", "idle")
                if sidecar_status == "working":
                    sidecar_status = "active"
                sidecar_dict = normalize_agent_dict({
                    "session_id": f"fleet:{agent_name}",
                    "role": agent_name,
                    "name": agent_name,
                    "task": fa.get("task_id", ""),
                    "status": sidecar_status,
                    "progress": fa.get("context_pct", 0),
                    "is_stale": fa.get("is_stale", False),
                    "source": "sidecar",
                })
                agents_out.append(sidecar_dict)
                seen_ids.add(f"fleet:{agent_name}")
    except Exception as e:
        logger.debug(f"Fleet sidecar enrichment: {e}")

    return JSONResponse(content=api_response({"agents": agents_out, "total": len(agents_out)}))


@router.get("/api/agents/fleet/status")
async def get_fleet_status(
    _=Depends(verify_auth),
):
    """Get fleet-wide status."""
    agent_registry = await get_agent_registry()

    # Collect all agents from both sources
    all_agents = []
    seen_ids = set()

    # 1. Registry agents
    for agent in agent_registry.list_active():
        sid = agent.session_id
        if sid and sid not in seen_ids:
            seen_ids.add(sid)
            all_agents.append({"status": agent.status})

    # 2. Tmux session tracker agents
    try:
        from forge_harness.session_tracker import get_session_tracker

        status_map = {
            "active": "active",
            "idle": "idle",
            "error": "error",
            "completed": "idle",
            "unknown": "idle",
        }
        tracker = get_session_tracker()
        for s in tracker.get_all_sessions():
            if s.session_name in seen_ids:
                continue
            seen_ids.add(s.session_name)
            mapped_status = status_map.get(s.status, "idle")
            all_agents.append({"status": mapped_status})
    except Exception as e:
        logger.debug(f"Session tracker fleet status: {e}")

    # Count by status
    active_count = sum(1 for a in all_agents if a["status"] == "active")
    idle_count = sum(1 for a in all_agents if a["status"] == "idle")
    failed_count = sum(1 for a in all_agents if a["status"] in ("error", "failed"))
    completed_count = sum(1 for a in all_agents if a["status"] == "completed")
    waiting_count = sum(1 for a in all_agents if a["status"] == "waiting")

    return JSONResponse(
        content=api_response(
            {
                "total_agents": len(all_agents),
                "active": active_count,
                "idle": idle_count,
                "failed": failed_count,
                "completed": completed_count,
                "waiting": waiting_count,
                "emergency_mode": False,
            }
        )
    )


@router.post("/api/agents/fleet/pause")
async def fleet_pause(
    body: dict = Body(default={}),
    _=Depends(verify_auth),
):
    """Pause all fleet agents (sets status to 'paused')."""
    agent_registry = await get_agent_registry()
    paused: list[str] = []
    reason = body.get("reason", "Manual pause")
    now = datetime.now(UTC)

    for agent in agent_registry.list_active():
        current_status = (getattr(agent, "status", None) or "").lower()
        if current_status not in ("paused", "offline"):
            # Preserve previous status for later restoration
            if hasattr(agent, "metadata") and isinstance(agent.metadata, dict):
                agent.metadata["previous_status"] = current_status or "idle"
                agent.metadata["pause_reason"] = reason
            if hasattr(agent, "status"):
                agent.status = "paused"
            if hasattr(agent, "last_activity"):
                agent.last_activity = now
            paused.append(agent.id)

    return JSONResponse(
        content={
            "success": True,
            "paused_count": len(paused),
            "paused_agents": paused,
            "reason": reason,
            "timestamp": now.isoformat(),
        }
    )


@router.post("/api/agents/fleet/resume")
async def fleet_resume(
    body: dict = Body(default={}),
    _=Depends(verify_auth),
):
    """Resume all paused fleet agents (restores previous status)."""
    agent_registry = await get_agent_registry()
    resumed: list[str] = []
    now = datetime.now(UTC)

    for agent in agent_registry.list_active():
        if (getattr(agent, "status", None) or "").lower() == "paused":
            # Restore previous status if recorded, otherwise fall back to "idle"
            previous_status = "idle"
            if hasattr(agent, "metadata") and isinstance(agent.metadata, dict):
                previous_status = agent.metadata.pop("previous_status", "idle")
                agent.metadata.pop("pause_reason", None)
            if hasattr(agent, "status"):
                agent.status = previous_status
            if hasattr(agent, "last_activity"):
                agent.last_activity = now
            resumed.append(agent.id)

    return JSONResponse(
        content={
            "success": True,
            "resumed_count": len(resumed),
            "resumed_agents": resumed,
            "timestamp": now.isoformat(),
        }
    )


@router.post("/api/agents/broadcast")
async def broadcast_message(
    message: str,
    request: Request,
    _=Depends(verify_auth),
):
    """Broadcast message to all agents."""
    agent_registry = await get_agent_registry()
    agents = agent_registry.list_active()

    count = 0
    for agent in agents:
        if hasattr(agent, "messages"):
            from datetime import UTC, datetime

            agent.messages.append(
                {
                    "type": "broadcast",
                    "content": message,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
            count += 1

    # Audit log fleet control
    ip_address, user_agent = _extract_request_context(request)
    audit_logger = get_audit_logger()
    await audit_logger.log_fleet_control(
        action_type="broadcast",
        actor={"id": "api", "type": "system"},
        context={"message": message[:100], "delivered_count": count},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return JSONResponse(content=api_response({"delivered": count}))


@router.get("/api/agents/{agent_id}")
async def get_agent(
    agent_id: str,
    _=Depends(verify_auth),
):
    """Get agent details.

    Supports lookup by:
    - Registry ID (UUID)
    - tmux_session format (e.g., "forge:opencode")
    - agent name (case-insensitive)
    - StateStore session_id (persistent storage)
    """
    agent_registry = await get_agent_registry()

    # 1. Try registry lookup first
    agent = agent_registry.get(agent_id)
    if agent:
        return JSONResponse(content=api_response(normalize_agent_dict(agent.to_dict())))

    # 1.1 Try registry lookup by role (if not already matched by name/ID)
    agent_id_lower = agent_id.lower()
    for ag in agent_registry.list_active():
        if (
            (ag.role and ag.role.lower() == agent_id_lower)
            or (ag.tmux_session and ag.tmux_session.lower() == agent_id_lower)
            or (ag.id.lower() == agent_id_lower)
        ):
            return JSONResponse(content=api_response(normalize_agent_dict(ag.to_dict())))

    # 2. Try StateStore lookup (persistent storage)
    try:
        state_store = await get_state_store()
        if state_store.is_connected():
            sa = state_store.get_agent(agent_id)
            if sa is not None:
                # Convert StateStore agent to API format
                agent_dict = {
                    "session_id": sa.session_id,
                    "role": sa.agent_role.value
                    if hasattr(sa.agent_role, "value")
                    else str(sa.agent_role),
                    "name": sa.agent_role.value
                    if hasattr(sa.agent_role, "value")
                    else str(sa.agent_role),
                    "domain": sa.domain,
                    "project": sa.project,
                    "task": sa.current_task or "",
                    "parent_id": None,
                    "children": [],
                    "tmux_session": sa.session_id if ":" in sa.session_id else None,
                    "skills": sa.capabilities if hasattr(sa, "capabilities") else [],
                    "status": sa.status.value if hasattr(sa.status, "value") else str(sa.status),
                    "progress": 0,
                    "current_task": sa.current_task,
                    "files_modified": [],
                    "token_usage": {},
                    "messages_count": 0,
                    "registered_at": sa.created_at.isoformat()
                    if hasattr(sa, "created_at") and hasattr(sa.created_at, "isoformat")
                    else datetime.now(UTC).isoformat(),
                    "last_activity": sa.last_heartbeat.isoformat()
                    if hasattr(sa, "last_heartbeat") and hasattr(sa.last_heartbeat, "isoformat")
                    else datetime.now(UTC).isoformat(),
                    "is_stale": False,
                    "source": "state_store",
                }
                return JSONResponse(content=api_response(normalize_agent_dict(agent_dict)))
    except Exception as e:
        logger.debug(f"StateStore lookup for {agent_id}: {e}")

    # 3. Look up agent via SessionTracker (tmux windows)
    try:
        from forge_harness.session_tracker import get_session_tracker

        tracker = get_session_tracker()
        status_map = {
            "active": "active",
            "idle": "idle",
            "error": "error",
            "completed": "idle",
            "unknown": "idle",
        }

        def _session_to_dict(s) -> dict:
            return {
                "session_id": s.session_name,
                "role": s.agent_type or s.window_name,
                "name": s.window_name,
                "domain": s.domain,
                "project": s.project or "",
                "task": s.current_task or "",
                "parent_id": None,
                "children": [],
                "tmux_session": s.session_name,
                "skills": [],
                "status": status_map.get(s.status, "idle"),
                "progress": 0,
                "current_task": s.current_task,
                "files_modified": [],
                "token_usage": {},
                "messages_count": 0,
                "registered_at": s.started_at or datetime.now(UTC).isoformat(),
                "last_activity": s.last_activity or datetime.now(UTC).isoformat(),
                "is_stale": False,
                "source": "tmux",
            }

        # Fast path: "forge:window" format → look up window directly
        if ":" in agent_id:
            _prefix, window_name = agent_id.rsplit(":", 1)
            if _prefix and window_name:
                s = tracker.get_session(window_name)
                # Accept if session found and matches (by session_name or window_name)
                if s is not None and (s.session_name == agent_id or s.window_name == window_name):
                    return JSONResponse(
                        content=api_response(normalize_agent_dict(_session_to_dict(s)))
                    )

        # Fallback: iterate all sessions (for bare window names like "orch-m3")
        for s in tracker.get_all_sessions():
            if s.session_name == agent_id or s.window_name == agent_id:
                return JSONResponse(content=api_response(normalize_agent_dict(_session_to_dict(s))))
    except Exception as e:
        logger.warning(f"Session tracker lookup for {agent_id}: {e}")

    raise HTTPException(status_code=404, detail="Agent not found")


@router.post("/api/agents/{agent_id}/progress")
async def update_agent_progress(
    agent_id: str,
    body: AgentProgressRequest,
    _=Depends(verify_auth),
):
    """Update agent progress."""
    agent_registry = await get_agent_registry()
    agent = agent_registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent.progress = body.progress
    if body.current_task:
        agent.current_task = body.current_task
    if body.files_modified is not None:
        agent.files_modified = body.files_modified
    agent.last_activity = datetime.now(UTC)

    return JSONResponse(content=api_response(normalize_agent_dict(agent.to_dict())))


@router.post("/api/agents/{agent_id}/heartbeat")
async def agent_heartbeat(
    agent_id: str,
    _=Depends(verify_auth),
):
    """Update agent last_activity timestamp (heartbeat)."""
    agent_registry = await get_agent_registry()
    agent = agent_registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    from datetime import UTC, datetime

    agent.last_activity = datetime.now(UTC)

    # Emit SSE event via EventEmitter (for ring buffer / polling API)
    try:
        emitter = get_event_emitter()
        emitter.emit(
            SSEEventType.agent_heartbeat,
            {"agent_id": agent_id, "last_activity": agent.last_activity.isoformat()},
            source="agents-api",
        )
    except Exception as e:
        logger.warning(f"Failed to emit agent.heartbeat to EventEmitter: {e}")

    return JSONResponse(content=api_response({"status": "ok"}))


@router.post("/api/agents/{agent_id}/complete")
async def complete_agent(
    agent_id: str,
    request: Request,
    _=Depends(verify_auth),
):
    """Mark agent as completed."""
    agent_registry = await get_agent_registry()
    success = agent_registry.complete(agent_id)
    if not success:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Emit SSE event via EventBus (for streaming clients)
    event_bus = await get_event_bus()
    await event_bus.publish("agent.completed", {"agent_id": agent_id})

    # Also emit via EventEmitter (for ring buffer / polling API)
    try:
        emitter = get_event_emitter()
        # Emit task_completed when agent finishes
        task_payload = {
            "task_id": agent_id,
            "agent_id": agent_id,
            "status": "completed",
        }
        emitter.emit(SSEEventType.task_completed, task_payload, source="agents-api")
        # Emit agent_idle to indicate agent is now available
        emitter.emit(SSEEventType.agent_idle, {"agent_id": agent_id}, source="agents-api")
    except Exception as e:
        logger.warning(f"Failed to emit agent.completed events to EventEmitter: {e}")

    # Audit log (deregistration)
    ip_address, user_agent = _extract_request_context(request)
    audit_logger = get_audit_logger()
    await audit_logger.log_agent_deregistration(
        agent_id=agent_id,
        actor={"id": "api", "type": "system"},
        context={"reason": "completed"},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return JSONResponse(content=api_response({"status": "completed"}))


@router.post("/api/agents/{agent_id}/pause")
async def pause_agent(
    agent_id: str,
    body: AgentPauseRequest | None = None,
    request: StarletteRequest = None,  # type: ignore[assignment]  # FastAPI injects Request automatically
    _=Depends(verify_auth),
):
    # FastAPI will inject Request automatically, but we need to handle None for backward compatibility
    if request is None:
        # This shouldn't happen in normal operation, but handle gracefully
        request = None  # Will be handled by _extract_request_context
    """Pause an agent."""
    agent_registry = await get_agent_registry()
    result = agent_registry.pause(agent_id)
    agent, previous_status = result if isinstance(result, tuple) else (result, None)
    if agent is None:
        # Fall back to session_tracker for tmux-tracked agents
        try:
            from forge_harness.session_tracker import get_session_tracker

            tracker = get_session_tracker()
            # session_name format is "forge:window" — extract window part
            window = agent_id.split(":", 1)[-1] if ":" in agent_id else agent_id
            session = tracker.get_session(window)
            if session is not None:
                previous_status = session.status
                tracker.save_session_metadata(window, {"status": "paused"})
                agent = session  # non-None sentinel so we don't 404
            else:
                raise HTTPException(status_code=404, detail="Agent not found")
        except HTTPException:
            raise
        except Exception as tracker_err:
            logger.debug(f"Session tracker pause fallback failed: {tracker_err}")
            raise HTTPException(status_code=404, detail="Agent not found")

    # Audit log fleet control
    ip_address, user_agent = _extract_request_context(request)
    audit_logger = get_audit_logger()
    await audit_logger.log_fleet_control(
        action_type="pause",
        actor={"id": "api", "type": "system"},
        target_id=agent_id,
        context={"reason": body.reason if body else None},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return JSONResponse(
        content=api_response(
            {
                "success": True,
                "agent_id": agent_id,
                "action": "pause",
                "previous_status": previous_status or "active",
                "new_status": "paused",
                "message": f"Agent {agent_id} paused",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
    )


@router.post("/api/agents/{agent_id}/resume")
async def resume_agent(
    agent_id: str,
    request: Request,
    _=Depends(verify_auth),
):
    """Resume a paused agent."""
    agent_registry = await get_agent_registry()
    result = agent_registry.resume(agent_id)
    agent, previous_status = result if isinstance(result, tuple) else (result, None)
    if agent is None:
        # Fall back to session_tracker for tmux-tracked agents
        try:
            from forge_harness.session_tracker import get_session_tracker

            tracker = get_session_tracker()
            # session_name format is "forge:window" — extract window part
            window = agent_id.split(":", 1)[-1] if ":" in agent_id else agent_id
            session = tracker.get_session(window)
            if session is not None:
                previous_status = session.status
                tracker.save_session_metadata(window, {"status": "active"})
                agent = session  # non-None sentinel so we don't 404
            else:
                raise HTTPException(status_code=404, detail="Agent not found")
        except HTTPException:
            raise
        except Exception as tracker_err:
            logger.debug(f"Session tracker resume fallback failed: {tracker_err}")
            raise HTTPException(status_code=404, detail="Agent not found")

    # Audit log fleet control
    ip_address, user_agent = _extract_request_context(request)
    audit_logger = get_audit_logger()
    await audit_logger.log_fleet_control(
        action_type="resume",
        actor={"id": "api", "type": "system"},
        target_id=agent_id,
        context={},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return JSONResponse(
        content=api_response(
            {
                "success": True,
                "agent_id": agent_id,
                "action": "resume",
                "previous_status": previous_status or "paused",
                "new_status": "active",
                "message": f"Agent {agent_id} resumed",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
    )


@router.post("/api/agents/{agent_id}/kill")
async def kill_agent(
    agent_id: str,
    request: Request,
    _=Depends(verify_auth),
):
    """Terminate an agent."""
    agent_registry = await get_agent_registry()
    success = agent_registry.kill(agent_id)
    if not success:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Audit log (deregistration)
    ip_address, user_agent = _extract_request_context(request)
    audit_logger = get_audit_logger()
    await audit_logger.log_agent_deregistration(
        agent_id=agent_id,
        actor={"id": "api", "type": "system"},
        context={"reason": "killed"},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return JSONResponse(content=api_response({"status": "killed"}))


@router.post("/api/agents/{agent_id}/handoff")
async def create_agent_handoff(
    agent_id: str,
    body: dict = Body(default={}),
    request: Request = None,  # type: ignore[assignment]
    _=Depends(verify_auth),
):
    """Create a handoff document for an agent session.

    Saves a markdown handoff file to .forge/sessions/ and records the
    handoff initiation in the agent's metadata.
    """
    agent_registry = await get_agent_registry()
    agent = agent_registry.get(agent_id)
    if agent is None:
        # Fall back to session_tracker for tmux-tracked agents (same pattern as pause/resume)
        try:
            from forge_harness.session_tracker import get_session_tracker

            tracker = get_session_tracker()
            window = agent_id.split(":", 1)[-1] if ":" in agent_id else agent_id
            session = tracker.get_session(window)
            if session is not None:
                agent = session  # non-None sentinel so we proceed
            else:
                raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
        except HTTPException:
            raise
        except Exception as tracker_err:
            logger.debug(f"Session tracker handoff fallback failed: {tracker_err}")
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    # Save handoff to .forge/sessions/
    handoff_dir = FORGE_ROOT / ".forge/sessions"
    handoff_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    timestamp = now.strftime("%Y-%m-%d_%H-%M")
    filename = f"handoff_{agent_id}_{timestamp}.md"

    content = body.get("content", "")
    summary = body.get("summary", "")
    next_steps = body.get("next_steps", [])

    # Build markdown
    md = f"# Handoff - {agent_id}\n\n"
    md += f"**Timestamp:** {now.isoformat()}\n"
    md += f"**Agent:** {agent_id}\n\n"
    if summary:
        md += f"## Summary\n{summary}\n\n"
    if content:
        md += f"## Content\n{content}\n\n"
    if next_steps:
        md += "## Next Steps\n"
        for step in next_steps:
            md += f"- {step}\n"

    (handoff_dir / filename).write_text(md)

    # Record handoff initiation in agent metadata
    if hasattr(agent, "last_activity"):
        agent.last_activity = now
    if hasattr(agent, "metadata") and isinstance(agent.metadata, dict):
        agent.metadata["handoff_initiated_at"] = now.isoformat()

    return JSONResponse(
        content={
            "success": True,
            "handoff_id": filename,
            "agent_id": agent_id,
            "path": str(handoff_dir / filename),
            "timestamp": now.isoformat(),
        }
    )


@router.post("/api/agents/{agent_id}/message")
async def send_agent_message(
    agent_id: str,
    body: AgentMessageRequest,
    request: Request,
    _=Depends(verify_auth),
):
    """Send a message to an agent via tmux.

    Uses DispatchClient to reliably send messages to agents with verification.
    Supports agents identified by:
    - Registry ID (UUID)
    - tmux_session format (e.g., "forge:opencode")
    - agent name (case-insensitive)
    """
    agent_registry = await get_agent_registry()

    # Look up agent (supports multiple ID formats)
    agent = agent_registry.get(agent_id)
    if not agent:
        # Try looking up by role/tmux_session
        agent_id_lower = agent_id.lower()
        for ag in agent_registry.list_active():
            if (
                (ag.role and ag.role.lower() == agent_id_lower)
                or (ag.tmux_session and ag.tmux_session.lower() == agent_id_lower)
                or (ag.id.lower() == agent_id_lower)
            ):
                agent = ag
                break

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Get tmux target
    tmux_target = getattr(agent, "tmux_session", None)
    if not tmux_target:
        return JSONResponse(
            status_code=400,
            content=api_response(
                error_code="NO_TMUX_SESSION",
                error_message=f"Agent {agent_id} has no tmux session configured",
            ),
        )

    # Send message using DispatchClient
    dispatch_success = False
    dispatch_error = None
    delivery_time_ms = 0.0

    try:
        from forge_harness.fleet.dispatch_client import DispatchClient

        client = DispatchClient()
        dispatch_result = await client.send(
            target=tmux_target,
            message=body.message,
            verify=True,
            timeout=10.0,
            skip_readiness=False,
            check_git_lock=False,
        )

        dispatch_success = dispatch_result.success
        delivery_time_ms = dispatch_result.delivery_time_ms

        if not dispatch_success:
            dispatch_error = dispatch_result.error
            logger.warning(f"Failed to send message to {tmux_target}: {dispatch_error}")
        else:
            logger.info(f"Successfully sent message to {tmux_target} in {delivery_time_ms:.0f}ms")

    except Exception as e:
        dispatch_error = str(e)
        logger.error(f"Failed to send message via DispatchClient: {e}", exc_info=True)

    # Update agent last_activity
    if dispatch_success and hasattr(agent, "last_activity"):
        from datetime import UTC, datetime

        agent.last_activity = datetime.now(UTC)

    # Emit SSE event
    event_bus = await get_event_bus()
    await event_bus.publish(
        "agent.message_sent",
        {
            "agent_id": agent.id,
            "tmux_target": tmux_target,
            "message_preview": body.message[:100],
            "success": dispatch_success,
            "delivery_time_ms": delivery_time_ms,
        },
    )

    # Audit log
    ip_address, user_agent = _extract_request_context(request)
    audit_logger = get_audit_logger()
    await audit_logger.log(
        action="send_message",
        actor={"id": "api", "type": "system"},
        target={"id": agent.id, "type": "agent"},
        context={
            "tmux_target": tmux_target,
            "message_length": len(body.message),
            "success": dispatch_success,
            "error": dispatch_error,
        },
        source="webhook_api",
        ip_address=ip_address,
        user_agent=user_agent,
    )

    if not dispatch_success:
        return JSONResponse(
            status_code=500,
            content=api_response(
                error_code="DISPATCH_FAILED",
                error_message=dispatch_error or "Unknown dispatch error",
            ),
        )

    return JSONResponse(
        content=api_response(
            {
                "status": "sent",
                "agent_id": agent.id,
                "tmux_target": tmux_target,
                "delivery_time_ms": delivery_time_ms,
            }
        )
    )


@router.get("/api/agents/{agent_id}/context/export")
async def export_agent_context(
    agent_id: str,
    include_tmux: bool = Query(True, description="Include tmux pane capture"),
    include_files: bool = Query(True, description="Include active files list"),
    include_memory: bool = Query(True, description="Include memory/state dump"),
    _=Depends(verify_auth),
):
    """Export complete agent context snapshot for debugging.

    Returns a downloadable JSON snapshot containing:
    - Agent metadata and status
    - Tmux pane capture (if available)
    - Active/modified files
    - Recent commands executed
    - Memory/state dump
    - Timestamp

    Useful for debugging agent state and session handoffs.
    """
    import subprocess
    from datetime import UTC, datetime

    agent_registry = await get_agent_registry()
    agent = agent_registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Build context snapshot
    snapshot = {
        "exported_at": datetime.now(UTC).isoformat() + "Z",
        "agent_id": agent_id,
        "agent": normalize_agent_dict(agent.to_dict()),
        "context": {},
    }

    # 1. Tmux capture (if requested and available)
    if include_tmux and agent.tmux_session:
        tmux_output = None
        try:
            # Format: session:window
            session, window = agent.tmux_session.split(":")
            # Capture pane content
            result = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", f"{session}:{window}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                tmux_output = result.stdout
        except Exception as e:
            tmux_output = f"Failed to capture tmux: {str(e)}"

        snapshot["context"]["tmux"] = {
            "session": agent.tmux_session,
            "captured_at": datetime.now(UTC).isoformat() + "Z",
            "content": tmux_output,
        }

    # 2. Active files (from agent registry)
    if include_files:
        files_modified = getattr(agent, "files_modified", []) or []
        snapshot["context"]["files"] = {
            "active_files": files_modified,
            "count": len(files_modified),
        }

    # 3. Recent commands (from agent messages/logs if available)
    recent_commands = []
    if hasattr(agent, "messages"):
        for msg in agent.messages[-50:]:  # Last 50 messages
            if isinstance(msg, dict) and msg.get("type") == "command":
                recent_commands.append(
                    {
                        "timestamp": msg.get("timestamp"),
                        "command": msg.get("content"),
                    }
                )

    snapshot["context"]["recent_commands"] = {
        "commands": recent_commands[-20:],  # Last 20 commands
        "count": len(recent_commands),
    }

    # 4. Memory/state dump
    if include_memory:
        memory_state = {
            "status": agent.status,
            "progress": getattr(agent, "progress", 0),
            "current_task": getattr(agent, "current_task", None),
            "last_activity": getattr(agent, "last_activity", None).isoformat()
            if hasattr(agent.last_activity, "isoformat")
            else str(agent.last_activity),
            "messages_count": len(getattr(agent, "messages", [])),
            "files_modified_count": len(getattr(agent, "files_modified", []) or []),
        }
        snapshot["context"]["memory"] = memory_state

    # 5. Activity log (last 20 events)
    activity_log = []
    if hasattr(agent, "activity_log"):
        for entry in agent.activity_log[-20:]:
            if isinstance(entry, dict):
                activity_log.append(entry)

    snapshot["context"]["activity_log"] = {
        "entries": activity_log,
        "count": len(activity_log),
    }

    return JSONResponse(content=api_response(snapshot))


@router.get("/api/agents/{agent_id}/logs")
async def get_agent_logs(
    agent_id: str,
    limit: Annotated[
        int, Query(ge=1, le=1000, description="Maximum number of log lines to return")
    ] = 100,
    _=Depends(verify_auth),
):
    """Return recent agent logs.

    Implementation notes:
    - If the agent is attached to a tmux session, attempts to capture the
      last N lines from the pane as log lines.
    - Otherwise synthesizes logs from recent in-memory activity events.

    Returns:
        {
            "logs": [
                {
                    "timestamp": str,
                    "level": "debug" | "info" | "warning" | "error",
                    "message": str,
                    "source": str,
                    "metadata": {...},
                },
                ...
            ],
            "count": int
        }
    """
    agent_registry = await get_agent_registry()
    agent = agent_registry.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    logs: list[dict[str, Any]] = []
    now = datetime.now(UTC).isoformat()

    # First, try to read from tmux if a session/window is known
    tmux_target = getattr(agent, "tmux_session", None)
    if tmux_target:
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-pt", tmux_target, "-S", "-200"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
                for line in lines[-limit:]:
                    logs.append(
                        {
                            "timestamp": now,
                            "level": "info",
                            "message": line,
                            "source": tmux_target,
                            "metadata": {},
                        }
                    )
        except Exception as exc:
            logger.debug(f"Failed to capture tmux logs for {agent_id}: {exc}")

    # Fallback: synthesize logs from recent activity events
    if not logs:
        # Retrieve activity events from the event bus if available
        activity_events: list[dict[str, Any]] = []
        try:
            event_bus = await get_event_bus()
            if hasattr(event_bus, "get_recent_events"):
                activity_events = await event_bus.get_recent_events() or []
        except Exception as exc:
            logger.debug(f"Failed to retrieve activity events for {agent_id}: {exc}")

        for event in activity_events:
            data = event.get("data", {}) or {}
            if data.get("session_id") != agent_id:
                continue
            logs.append(
                {
                    "timestamp": event.get("timestamp", now),
                    "level": "info" if data.get("event_type") != "error" else "error",
                    "message": data.get("content", ""),
                    "source": event.get("source", agent_id),
                    "metadata": data.get("metadata", {}),
                }
            )

        logs = logs[-limit:]

    return JSONResponse(
        content=api_response(
            {
                "logs": logs,
                "count": len(logs),
            }
        )
    )
