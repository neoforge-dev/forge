"""Legacy Agent Registry endpoints — extracted from webhook_server_main.py.

Handles /api/legacy/agents/* routes plus fleet control routes that were
defined inline in create_app().
"""

import json
import subprocess
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.api.agents import (
    AgentRegisterRequest,
    normalize_agent_dict,
)
from forge_harness.webhook_server.models.legacy_models import (
    ActivityEvent,
    AgentActionResponse,
    AgentCompleteRequest,
    AgentMessageRequest,
    AgentPauseRequest,
    AgentProgressRequest,
    BroadcastRequest,
    FleetActionAgentEntry,
    FleetActionResponse,
    FleetBroadcastRequest,
    FleetPauseRequest,
    FleetResumeRequest,
    HandoffRequest,
    KillAgentRequest,
    api_response,
)
from forge_harness.webhook_server.services.agent_registry import get_agent_registry
from forge_harness.webhook_server.services.event_bus import get_event_bus

logger = get_logger(__name__)

router = APIRouter(tags=["legacy-agents"])

# Module-level state for fleet control (shared across requests via app.state)
# These will be set during app startup via init_legacy_agents_state()
_fleet_emergency_mode: bool = False
_fleet_last_broadcast: dict[str, Any] | None = None
_activity_events: list[dict[str, Any]] = []
_activity_max_events = 100


def _get_agent_registry(request: Request):
    """Get agent registry from app.state or global singleton."""
    return getattr(request.app.state, "agent_registry", None) or get_agent_registry()


def _get_event_bus(request: Request):
    """Get event bus from app.state or global singleton."""
    return getattr(request.app.state, "event_bus", None) or get_event_bus()


# =========================================================================
# Agent Registration
# =========================================================================


@router.post("/api/legacy/agents/register")
async def register_agent(
    body: AgentRegisterRequest,
    request: Request,
):
    """Register a new agent session with hierarchy support."""
    _agent_registry = _get_agent_registry(request)
    _event_bus = _get_event_bus(request)

    agent = _agent_registry.register(
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
        get_store = getattr(request.app.state, "get_state_store", None)
        if get_store:
            state_store = get_store()
        else:
            from forge_harness.state_store import StateStore

            state_store = StateStore()
            state_store.connect()

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

    await _event_bus.publish("agent.registered", agent.to_dict())
    return JSONResponse(content=api_response(normalize_agent_dict(agent.to_dict())))


# =========================================================================
# Agent List / Detail
# =========================================================================


@router.get("/api/legacy/agents")
async def list_agents(request: Request):
    """List all active agents."""
    _agent_registry = _get_agent_registry(request)

    registry_agent_dicts = []
    seen_ids: set[str] = set()
    source_counts = {"registry": 0, "state_store": 0, "tmux": 0}
    errors = []

    # 1. Get agents from in-memory registry
    try:
        registry_agents = _agent_registry.list_active()
        for agent in registry_agents:
            try:
                agent_dict = normalize_agent_dict(agent.to_dict())
                agent_dict["source"] = "registry"
                registry_agent_dicts.append(agent_dict)
                seen_ids.add(agent_dict["session_id"])
                source_counts["registry"] += 1
            except Exception as e:
                logger.error(f"Failed to convert registry agent: {e}")
                errors.append({"source": "registry", "error": str(e)})
    except Exception as e:
        logger.error(f"Failed to query agent registry: {e}", exc_info=True)
        errors.append({"source": "registry", "error": str(e)})

    # 2. Get agents from StateStore
    try:
        get_store = getattr(request.app.state, "get_state_store", None)
        if get_store:
            state_store = get_store()
        else:
            from forge_harness.state_store import StateStore

            state_store = StateStore()
            state_store.connect()

        if state_store.is_connected():
            store_agents = state_store.get_active_agents()
            for sa in store_agents:
                try:
                    if sa.session_id in seen_ids:
                        continue
                    seen_ids.add(sa.session_id)
                    agent_dict = normalize_agent_dict(
                        {
                            "session_id": sa.session_id,
                            "role": sa.agent_role.value
                            if hasattr(sa.agent_role, "value")
                            else str(sa.agent_role),
                            "name": None,
                            "domain": sa.domain,
                            "project": sa.project,
                            "task": sa.current_task or "",
                            "parent_id": None,
                            "children": [],
                            "tmux_session": None,
                            "skills": sa.capabilities if hasattr(sa, "capabilities") else [],
                            "status": sa.status.value
                            if hasattr(sa.status, "value")
                            else str(sa.status),
                            "progress": 0,
                            "current_task": sa.current_task,
                            "files_modified": [],
                            "token_usage": {},
                            "messages_count": 0,
                            "registered_at": sa.registered_at.isoformat()
                            if hasattr(sa.registered_at, "isoformat")
                            else str(sa.registered_at),
                            "last_activity": sa.last_heartbeat.isoformat()
                            if hasattr(sa.last_heartbeat, "isoformat")
                            else str(sa.last_heartbeat),
                            "is_stale": False,
                            "source": "state_store",
                        }
                    )
                    registry_agent_dicts.append(agent_dict)
                    source_counts["state_store"] += 1
                except Exception as e:
                    errors.append({"source": "state_store", "error": str(e)})
    except Exception as e:
        logger.error(f"Failed to query StateStore: {e}", exc_info=True)
        errors.append({"source": "state_store", "error": str(e)})

    # 3. Include tmux sessions as agents
    try:
        from forge_harness.session_tracker import get_session_tracker

        tracker = get_session_tracker()
        sessions = tracker.get_all_sessions()

        for s in sessions:
            try:
                if s.session_name in seen_ids or s.window_name in seen_ids:
                    continue
                seen_ids.add(s.session_name)

                status_map = {
                    "active": "active",
                    "idle": "idle",
                    "error": "error",
                    "completed": "idle",
                    "unknown": "idle",
                }

                agent_dict = normalize_agent_dict(
                    {
                        "session_id": s.session_name,
                        "role": s.agent_type or s.window_name,
                        "name": s.window_name,
                        "domain": s.domain,
                        "project": s.project,
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
                )
                registry_agent_dicts.append(agent_dict)
                source_counts["tmux"] += 1
            except Exception as e:
                errors.append({"source": "tmux", "error": str(e)})
    except Exception as e:
        errors.append({"source": "tmux", "error": str(e)})

    total = len(registry_agent_dicts)
    response_data: dict[str, Any] = {
        "agents": registry_agent_dicts,
        "total": total,
        "sources": source_counts,
    }
    if errors:
        response_data["errors"] = errors

    return JSONResponse(content=api_response(response_data))


@router.get("/api/legacy/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    """Get agent details."""
    _agent_registry = _get_agent_registry(request)
    agent_dict = None

    # 1. Registry
    agent = _agent_registry.get(agent_id)
    if agent is not None:
        agent_dict = agent.to_dict()

    # 2. StateStore
    if agent_dict is None:
        try:
            get_store = getattr(request.app.state, "get_state_store", None)
            if get_store:
                state_store = get_store()
            else:
                from forge_harness.state_store import StateStore

                state_store = StateStore()
                state_store.connect()

            if state_store.is_connected():
                sa = state_store.get_agent(agent_id)
                if sa is not None:
                    agent_dict = {
                        "session_id": sa.session_id,
                        "role": sa.agent_role.value
                        if hasattr(sa.agent_role, "value")
                        else str(sa.agent_role),
                        "name": None,
                        "domain": sa.domain,
                        "project": sa.project or "",
                        "task": sa.current_task or "",
                        "parent_id": None,
                        "children": [],
                        "tmux_session": None,
                        "skills": getattr(sa, "capabilities", []) or [],
                        "status": sa.status.value
                        if hasattr(sa.status, "value")
                        else str(sa.status),
                        "progress": 0,
                        "current_task": sa.current_task,
                        "files_modified": [],
                        "token_usage": {},
                        "messages_count": 0,
                        "registered_at": sa.registered_at.isoformat()
                        if hasattr(sa.registered_at, "isoformat")
                        else datetime.now(UTC).isoformat(),
                        "last_activity": sa.last_heartbeat.isoformat()
                        if hasattr(sa.last_heartbeat, "isoformat")
                        else datetime.now(UTC).isoformat(),
                        "is_stale": False,
                        "source": "state_store",
                    }
        except Exception as e:
            logger.debug(f"StateStore lookup for {agent_id}: {e}")

    # 3. Session tracker (tmux)
    if agent_dict is None:
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

            def session_to_agent_dict(s) -> dict:
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

            if ":" in agent_id:
                prefix, window_name = agent_id.rsplit(":", 1)
                if prefix and window_name:
                    s = tracker.get_session(window_name)
                    if s is not None and s.session_name == agent_id:
                        agent_dict = session_to_agent_dict(s)

            if agent_dict is None:
                sessions = tracker.get_all_sessions()
                for s in sessions:
                    if s.session_name == agent_id or s.window_name == agent_id:
                        agent_dict = session_to_agent_dict(s)
                        break
        except Exception as e:
            logger.debug(f"Session tracker lookup for {agent_id}: {e}")

    if agent_dict is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return JSONResponse(content=api_response(normalize_agent_dict(agent_dict)))


# =========================================================================
# Agent Progress / Heartbeat / Complete
# =========================================================================


@router.post("/api/legacy/agents/{agent_id}/progress")
async def update_agent_progress(agent_id: str, body: AgentProgressRequest, request: Request):
    """Update agent progress."""
    _agent_registry = _get_agent_registry(request)
    _event_bus = _get_event_bus(request)

    agent = _agent_registry.update_progress(
        agent_id=agent_id,
        progress=body.progress,
        current_task=body.current_task,
        files_modified=body.files_modified,
        token_usage=body.token_usage,
    )
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    await _event_bus.publish("agent.progress", agent.to_dict())
    return JSONResponse(content=api_response(normalize_agent_dict(agent.to_dict())))


@router.post("/api/legacy/agents/{agent_id}/heartbeat")
async def agent_heartbeat(agent_id: str, request: Request):
    """Record agent heartbeat to indicate it's still alive."""
    _agent_registry = _get_agent_registry(request)
    _event_bus = _get_event_bus(request)

    agent = _agent_registry.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent.last_activity = datetime.now(UTC)
    await _event_bus.publish(
        "agent.heartbeat", {"id": agent_id, "timestamp": agent.last_activity.isoformat()}
    )
    return JSONResponse(content=api_response({"status": "ok", "agent_id": agent_id}))


@router.post("/api/legacy/agents/{agent_id}/complete")
async def complete_agent(agent_id: str, body: AgentCompleteRequest, request: Request):
    """Mark agent as completed."""
    _agent_registry = _get_agent_registry(request)
    _event_bus = _get_event_bus(request)

    agent = _agent_registry.complete(agent_id=agent_id, summary=body.summary)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    await _event_bus.publish("agent.completed", agent.to_dict())

    from forge_harness.webhook_server.services.audit import get_audit_logger

    audit_logger = get_audit_logger()
    await audit_logger.log(
        action="complete",
        actor={"id": "api", "type": "system"},
        target={"id": agent_id, "type": "agent"},
        context={"agent_id": agent_id},
        source="webhook_api",
    )
    return JSONResponse(content=api_response(normalize_agent_dict(agent.to_dict())))


# =========================================================================
# Agent Pause / Resume / Kill / Message
# =========================================================================


@router.post("/api/legacy/agents/{agent_id}/pause")
async def pause_agent(
    agent_id: str,
    request: Request,
    body: AgentPauseRequest | None = None,
):
    """Pause a single agent (sets status to 'paused')."""
    _agent_registry = _get_agent_registry(request)
    _event_bus = _get_event_bus(request)

    agent, previous_status = _agent_registry.pause(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    tmux_target = getattr(agent, "tmux_session", None)
    tmux_result = None
    if tmux_target:
        try:
            result = subprocess.run(
                ["tmux", "send-keys", "-t", tmux_target, "C-c"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            tmux_result = "tmux_paused" if result.returncode == 0 else "tmux_failed"
        except Exception as e:
            logger.debug(f"tmux send-keys C-c failed for {agent.id}: {e}")
            tmux_result = f"tmux_error: {e}"

    now = datetime.now(UTC).isoformat()
    await _event_bus.publish(
        "agent.paused",
        {"agent_id": agent.id, "previous_status": previous_status, "timestamp": now},
    )

    from forge_harness.webhook_server.services.audit import get_audit_logger

    audit_logger = get_audit_logger()
    await audit_logger.log(
        action="pause",
        actor={"id": "api", "type": "system"},
        target={"id": agent_id, "type": "agent"},
        context={"agent_id": agent_id, "reason": body.reason if body else None},
        source="webhook_api",
    )

    resp = AgentActionResponse(
        success=True,
        agent_id=agent.id,
        action="pause",
        previous_status=previous_status,
        new_status=agent.status,
        message=f"Agent paused{' (tmux interrupted)' if tmux_result == 'tmux_paused' else ''}",
        timestamp=now,
    )
    return JSONResponse(content=api_response(resp.model_dump()))


@router.post("/api/legacy/agents/{agent_id}/resume")
async def resume_agent(agent_id: str, request: Request):
    """Resume a single agent (sets status to 'active')."""
    _agent_registry = _get_agent_registry(request)
    _event_bus = _get_event_bus(request)

    agent, previous_status = _agent_registry.resume(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    tmux_target = getattr(agent, "tmux_session", None)
    tmux_result = None
    if tmux_target:
        try:
            result = subprocess.run(
                ["tmux", "send-keys", "-t", tmux_target, "Enter"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            tmux_result = "tmux_resumed" if result.returncode == 0 else "tmux_failed"
        except Exception as e:
            logger.debug(f"tmux send-keys Enter failed for {agent.id}: {e}")
            tmux_result = f"tmux_error: {e}"

    now = datetime.now(UTC).isoformat()
    await _event_bus.publish(
        "agent.resumed",
        {"agent_id": agent.id, "new_status": agent.status, "timestamp": now},
    )

    from forge_harness.webhook_server.services.audit import get_audit_logger

    audit_logger = get_audit_logger()
    await audit_logger.log(
        action="resume",
        actor={"id": "api", "type": "system"},
        target={"id": agent_id, "type": "agent"},
        context={"agent_id": agent_id},
        source="webhook_api",
    )

    resp = AgentActionResponse(
        success=True,
        agent_id=agent.id,
        action="resume",
        previous_status=previous_status,
        new_status=agent.status,
        message=f"Agent resumed{' (tmux nudged)' if tmux_result == 'tmux_resumed' else ''}",
        timestamp=now,
    )
    return JSONResponse(content=api_response(resp.model_dump()))


@router.post("/api/legacy/agents/{agent_id}/kill")
async def kill_agent(
    agent_id: str,
    request: Request,
    body: KillAgentRequest | None = None,
):
    """Kill a single agent (sets status to 'failed')."""
    _agent_registry = _get_agent_registry(request)
    _event_bus = _get_event_bus(request)

    reason = body.reason if body else None
    agent, previous_status = _agent_registry.kill(
        agent_id, reason=reason or "Killed from Command Center"
    )
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    if getattr(agent, "tmux_session", None):
        try:
            subprocess.run(
                ["tmux", "kill-window", "-t", agent.tmux_session],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except Exception as e:
            logger.debug(f"tmux kill-window failed for {agent.id}: {e}")

    now = datetime.now(UTC).isoformat()
    await _event_bus.publish(
        "agent.status",
        {
            "session_id": agent.id,
            "status": agent.status,
            "previous_status": previous_status,
            "reason": reason or "Killed from Command Center",
            "timestamp": now,
        },
    )

    from forge_harness.webhook_server.services.audit import get_audit_logger

    audit_logger = get_audit_logger()
    await audit_logger.log(
        action="kill",
        actor={"id": "api", "type": "system"},
        target={"id": agent_id, "type": "agent"},
        context={"agent_id": agent_id, "reason": reason},
        source="webhook_api",
    )

    resp = AgentActionResponse(
        success=True,
        agent_id=agent.id,
        action="kill",
        previous_status=previous_status,
        new_status=agent.status,
        message=reason or "Agent killed",
        timestamp=now,
    )
    return JSONResponse(content=api_response(resp.model_dump()))


@router.post("/api/legacy/agents/{agent_id}/message")
async def send_agent_message(
    agent_id: str,
    body: AgentMessageRequest,
    request: Request,
):
    """Send message to an agent (backward-compatible endpoint)."""
    _agent_registry = _get_agent_registry(request)
    _event_bus = _get_event_bus(request)

    content = body.get_content()
    if not content:
        raise HTTPException(status_code=400, detail="Message content is required")

    message = {"type": body.type, "content": content}
    now = datetime.now(UTC).isoformat()

    agent, message_id = _agent_registry.send_message(agent_id=agent_id, message=message)

    if agent is None:
        # Try tmux agent
        try:
            from forge_harness.session_tracker import get_session_tracker

            tracker = get_session_tracker()

            if ":" in agent_id:
                session_prefix, window_name = agent_id.rsplit(":", 1)
                target = f"{session_prefix}:{window_name}"
            else:
                window_name = agent_id
                target = f"forge:{window_name}"

            session = tracker.get_session(window_name)
            if session is None:
                raise HTTPException(
                    status_code=404, detail="Agent not found in registry or tmux sessions"
                )

            from forge_harness.fleet.verification import send_to_tmux

            send_ok = await send_to_tmux(target, content)
            if not send_ok:
                raise HTTPException(status_code=500, detail="Failed to deliver message to tmux")

            await _event_bus.publish(
                "message.sent",
                {
                    "message_id": f"tmux-{agent_id}-{int(datetime.now().timestamp())}",
                    "recipient_id": agent_id,
                    "sender_id": "system",
                    "type": body.type,
                    "delivery_method": "tmux",
                },
            )

            data = {
                "success": True,
                "agent_id": agent_id,
                "action": "message",
                "previous_status": session.status,
                "new_status": session.status,
                "message": content,
                "timestamp": now,
                "delivered": True,
                "delivery_method": "tmux",
                "tmux_target": target,
            }
            return JSONResponse(content=api_response(data))

        except subprocess.CalledProcessError as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to deliver message to tmux session: {e}"
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=504, detail="Timeout sending message to tmux session"
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=404, detail="Agent not found")

    # Registry agent - also send to tmux if available
    tmux_target: str | None = getattr(agent, "tmux_session", None)
    if tmux_target:
        try:
            from forge_harness.fleet.verification import send_to_tmux

            send_ok = await send_to_tmux(tmux_target, content)
            if not send_ok:
                logger.warning(f"Registry message sent but tmux delivery failed for {agent_id}")
        except Exception as e:
            logger.warning(f"Registry message sent but tmux delivery failed for {agent_id}: {e}")

    await _event_bus.publish(
        "message.sent",
        {
            "message_id": message_id,
            "recipient_id": agent_id,
            "sender_id": "system",
            "type": body.type,
        },
    )

    data = {
        "success": True,
        "agent_id": agent_id,
        "action": "message",
        "previous_status": agent.status,
        "new_status": agent.status,
        "message": content,
        "timestamp": now,
        "delivered": True,
        "message_id": message_id,
        "delivery_method": "tmux" if tmux_target else "registry",
    }
    return JSONResponse(content=api_response(data))


@router.post("/api/legacy/agents/broadcast")
async def broadcast_to_agents(body: BroadcastRequest, request: Request):
    """Broadcast message to all active agents (legacy endpoint)."""
    _agent_registry = _get_agent_registry(request)
    message = {"type": body.type, "content": body.content}
    count = _agent_registry.broadcast(message)
    return JSONResponse(content=api_response({"delivered_count": count}))


# =========================================================================
# Fleet Control (pause/resume all, broadcast, status)
# =========================================================================


@router.post("/api/agents/fleet/pause")
async def pause_all_agents(body: FleetPauseRequest, request: Request):
    """Pause all controllable agents (sets status to 'waiting')."""
    global _fleet_emergency_mode
    _agent_registry = _get_agent_registry(request)
    _event_bus = _get_event_bus(request)

    agents = _agent_registry.list_active()
    changed: list[FleetActionAgentEntry] = []
    now = datetime.now(UTC).isoformat()

    for agent in agents:
        if agent.status in ("active", "idle"):
            updated, previous_status = _agent_registry.pause(agent.id)
            if updated:
                changed.append(
                    FleetActionAgentEntry(
                        id=updated.id,
                        name=updated.name,
                        previous_status=previous_status,
                        new_status=updated.status,
                    )
                )

    _fleet_emergency_mode = bool(changed)

    if changed:
        await _event_bus.publish(
            "fleet.pause_all",
            {
                "timestamp": now,
                "affected_agents": len(changed),
                "agents": [c.model_dump() for c in changed],
            },
        )

    resp = FleetActionResponse(
        success=True,
        action="pause_all",
        affected_agents=len(changed),
        agents=changed,
        timestamp=now,
    )
    return JSONResponse(content=api_response(resp.model_dump()))


@router.post("/api/agents/fleet/resume")
async def resume_all_agents(body: FleetResumeRequest, request: Request):
    """Resume all paused agents (sets status to 'active')."""
    global _fleet_emergency_mode
    _agent_registry = _get_agent_registry(request)
    _event_bus = _get_event_bus(request)

    agents = _agent_registry.list_active()
    changed: list[FleetActionAgentEntry] = []
    now = datetime.now(UTC).isoformat()

    for agent in agents:
        if agent.status == "waiting":
            updated, previous_status = _agent_registry.resume(agent.id)
            if updated:
                changed.append(
                    FleetActionAgentEntry(
                        id=updated.id,
                        name=updated.name,
                        previous_status=previous_status,
                        new_status=updated.status,
                    )
                )

    if changed:
        _fleet_emergency_mode = False
        await _event_bus.publish(
            "fleet.resume_all",
            {
                "timestamp": now,
                "affected_agents": len(changed),
                "agents": [c.model_dump() for c in changed],
            },
        )

    resp = FleetActionResponse(
        success=True,
        action="resume_all",
        affected_agents=len(changed),
        agents=changed,
        timestamp=now,
    )
    return JSONResponse(content=api_response(resp.model_dump()))


@router.post("/api/agents/fleet/broadcast")
async def fleet_broadcast(body: FleetBroadcastRequest, request: Request):
    """Broadcast an urgent message to all agents."""
    global _fleet_last_broadcast
    _agent_registry = _get_agent_registry(request)
    _event_bus = _get_event_bus(request)

    now = datetime.now(UTC).isoformat()
    payload = {
        "type": "broadcast",
        "content": body.message,
        "priority": body.priority,
    }
    count = _agent_registry.broadcast(payload)

    _fleet_last_broadcast = {
        "message": body.message,
        "priority": body.priority,
        "timestamp": now,
    }

    await _event_bus.publish(
        "fleet.broadcast",
        {
            "timestamp": now,
            "affected_agents": count,
            "message": body.message,
            "priority": body.priority,
        },
    )

    resp = FleetActionResponse(
        success=True,
        action="broadcast",
        affected_agents=count,
        agents=[],
        timestamp=now,
    )
    return JSONResponse(content=api_response(resp.model_dump()))


@router.get("/api/legacy/agents/fleet/status")
async def fleet_status(request: Request):
    """Return heartbeat-style fleet status for Command Center."""
    _agent_registry = _get_agent_registry(request)

    status_counts: dict[str, int] = {
        "active": 0,
        "waiting": 0,
        "idle": 0,
        "completed": 0,
        "failed": 0,
    }
    seen_ids: set[str] = set()

    for agent in _agent_registry.list_active():
        sid = agent.id
        if sid and sid not in seen_ids:
            seen_ids.add(sid)
            if agent.status in status_counts:
                status_counts[agent.status] += 1

    try:
        from forge_harness.session_tracker import get_session_tracker

        _tmux_status_map = {
            "active": "active",
            "idle": "idle",
            "error": "failed",
            "completed": "completed",
            "unknown": "idle",
        }
        tracker = get_session_tracker()
        for s in tracker.get_all_sessions():
            if s.session_name in seen_ids:
                continue
            seen_ids.add(s.session_name)
            mapped = _tmux_status_map.get(s.status, "idle")
            if mapped in status_counts:
                status_counts[mapped] += 1
    except Exception:
        pass

    total = sum(status_counts.values())
    data: dict[str, Any] = {
        "total_agents": total,
        "active": status_counts["active"],
        "waiting": status_counts["waiting"],
        "idle": status_counts["idle"],
        "completed": status_counts["completed"],
        "failed": status_counts["failed"],
        "emergency_mode": _fleet_emergency_mode,
    }

    if _fleet_last_broadcast is not None:
        data["last_broadcast"] = _fleet_last_broadcast

    return JSONResponse(content=api_response(data))


@router.get("/api/fleet/status")
async def fleet_status_short(request: Request):
    """Alias for /api/agents/fleet/status for compatibility."""
    return await fleet_status(request)


# =========================================================================
# Activity stream
# =========================================================================


@router.post("/api/agents/{agent_id}/activity")
async def post_agent_activity(agent_id: str, body: ActivityEvent, request: Request):
    """Record an activity event for an agent."""
    now = datetime.now(UTC).isoformat()
    event_id = str(uuid.uuid4())
    event = {
        "id": event_id,
        "type": body.event_type,
        "timestamp": now,
        "source": agent_id,
        "data": {
            "session_id": agent_id,
            "event_type": body.event_type,
            "content": body.content,
            "metadata": body.metadata,
        },
    }
    _activity_events.append(event)

    while len(_activity_events) > _activity_max_events:
        _activity_events.pop(0)

    return JSONResponse(content=api_response({"recorded": True}))


@router.get("/api/agents/activity/stream")
async def stream_agent_activity(request: Request):
    """Stream agent activity events via SSE."""
    import asyncio

    from sse_starlette.sse import EventSourceResponse

    async def event_generator():
        last_index = len(_activity_events)

        while True:
            current_len = len(_activity_events)
            if current_len > last_index:
                for event in _activity_events[last_index:current_len]:
                    yield {
                        "event": event.get("type", "activity"),
                        "data": json.dumps(event),
                    }
                last_index = current_len

            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@router.get("/api/agents/activity/recent")
async def get_recent_activity(
    limit: int = 20,
    agent_id: str | None = None,
    request: Request = None,
):
    """Get recent activity events in SSEEvent format."""
    events = _activity_events

    if agent_id:
        events = [e for e in events if e.get("source") == agent_id]

    recent_events = events[-limit:]

    return JSONResponse(
        content=api_response(
            {
                "events": recent_events,
                "total": len(events),
            }
        )
    )


# =========================================================================
# Agent Context Export & Handoff
# =========================================================================


@router.get("/api/legacy/agents/{agent_id}/context/export")
async def export_agent_context(agent_id: str, request: Request):
    """Export agent context for handoff to another session."""
    _agent_registry = _get_agent_registry(request)

    agent = _agent_registry.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    terminal_output = ""
    context_percentage = None
    tmux_target = getattr(agent, "tmux_session", None)

    if tmux_target:
        try:
            import re

            result = subprocess.run(
                ["tmux", "capture-pane", "-pt", tmux_target, "-S", "-500"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                terminal_output = result.stdout
                context_match = re.search(
                    r"(\d+)%\s*(?:context|used)", terminal_output, re.IGNORECASE
                )
                if context_match:
                    context_percentage = int(context_match.group(1))
        except Exception as e:
            logger.warning(f"Failed to capture tmux context for {agent_id}: {e}")

    now = datetime.now(UTC).isoformat()

    export_data = {
        "agent_id": agent.id,
        "terminal_output": terminal_output,
        "current_task": getattr(agent, "current_task", None) or getattr(agent, "task", None),
        "session_info": {
            "started_at": getattr(agent, "registered_at", now),
            "domain": getattr(agent, "domain", None),
            "project": getattr(agent, "project", None),
            "status": getattr(agent, "status", "unknown"),
            "tmux_session": tmux_target,
        },
        "context_percentage": context_percentage,
        "exported_at": now,
    }

    return JSONResponse(content=api_response(export_data))


@router.post("/api/agents/{agent_id}/handoff")
async def trigger_agent_handoff(
    agent_id: str,
    request: Request,
    body: HandoffRequest | None = None,
):
    """Trigger handoff for an agent."""
    _agent_registry = _get_agent_registry(request)
    _event_bus = _get_event_bus(request)

    agent = _agent_registry.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    body = body or HandoffRequest()
    tmux_target = getattr(agent, "tmux_session", None)
    handoff_triggered = False
    context_exported = None

    if tmux_target:
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", tmux_target, "/handoff"],
                capture_output=True,
                timeout=5,
                check=True,
            )
            subprocess.run(
                ["tmux", "send-keys", "-t", tmux_target, "Enter"],
                capture_output=True,
                timeout=5,
                check=True,
            )
            handoff_triggered = True
            logger.info(f"Triggered handoff for agent {agent_id}")
        except Exception as e:
            logger.warning(f"Failed to trigger handoff for {agent_id}: {e}")

    if body.include_context:
        try:
            terminal_output = ""
            if tmux_target:
                result = subprocess.run(
                    ["tmux", "capture-pane", "-pt", tmux_target, "-S", "-200"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    terminal_output = result.stdout

            context_exported = {
                "agent_id": agent.id,
                "task": getattr(agent, "current_task", None) or getattr(agent, "task", None),
                "domain": getattr(agent, "domain", None),
                "project": getattr(agent, "project", None),
                "terminal_preview": terminal_output[-2000:] if terminal_output else None,
                "reason": body.reason,
            }
        except Exception as e:
            logger.warning(f"Failed to export context for handoff: {e}")

    if body.target_agent_id:
        target_agent = _agent_registry.get(body.target_agent_id)
        if target_agent:
            target_tmux = getattr(target_agent, "tmux_session", None)
            if target_tmux:
                try:
                    handoff_msg = (
                        f"Handoff from {agent_id}: {body.reason or 'Handoff requested'}"
                    )
                    subprocess.run(
                        ["tmux", "send-keys", "-t", target_tmux, handoff_msg, "Enter"],
                        capture_output=True,
                        timeout=5,
                        check=False,
                    )
                except Exception as e:
                    logger.warning(f"Failed to notify target agent: {e}")

    now = datetime.now(UTC).isoformat()
    agent.status = "handoff"

    await _event_bus.publish(
        "agent.handoff",
        {
            "session_id": agent.id,
            "target_id": body.target_agent_id,
            "reason": body.reason,
            "timestamp": now,
        },
    )

    resp = AgentActionResponse(
        success=handoff_triggered,
        agent_id=agent.id,
        action="handoff",
        previous_status=agent.status,
        new_status="handoff",
        message="Handoff triggered"
        if handoff_triggered
        else "Handoff requested (no tmux session)",
        timestamp=now,
    )

    response_data = resp.model_dump()
    if context_exported:
        response_data["context"] = context_exported

    return JSONResponse(content=api_response(response_data))
