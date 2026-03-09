"""OpenClaw gateway endpoints and routing utilities.

Minimal scaffold for:
- Intent parsing from chat messages
- Tier-based priority routing
- verified dispatch-client integration
- SSE subscription for push notifications

Register these routes inside webhook_server.py using `register_openclaw_routes(app)`.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from forge_harness.domain_registry import DomainRegistry
from forge_harness.task_queue import Task, TaskPriority, TaskQueue, TaskStatus
from forge_harness.webhook_server.infrastructure.auth import (
    AuthConfig,
    AuthResult,
    verify_bearer_token,
)
from forge_harness.webhook_server.services.event_bus import SSEEvent, get_event_bus

from .tmux_dispatcher import capture_tail, list_sessions, resolve_target

DEFAULT_QUEUE_DIR = Path(__file__).resolve().parents[2] / ".openclaw_queue"


@dataclass(frozen=True)
class Intent:
    kind: str
    agent: str | None
    text: str
    raw: str
    priority_override: TaskPriority | None
    domain: str | None
    tier: int | None


class ChatMessage(BaseModel):
    source: str = Field(..., description="telegram|whatsapp|slack")
    user_id: str = Field(..., description="User ID in the chat system")
    text: str = Field(..., description="Raw message text")
    timestamp: str | None = Field(default=None, description="ISO timestamp from chat provider")
    metadata: dict[str, Any] = Field(default_factory=dict)


class DispatchResponse(BaseModel):
    status: str
    task_id: str | None = None
    dispatched: bool = False
    agent: str | None = None
    priority: str | None = None
    tier: int | None = None
    detail: str | None = None


class StatusResponse(BaseModel):
    sessions: list[dict[str, Any]]
    queue_depth: int


def _ensure_registry_loaded() -> None:
    try:
        DomainRegistry.load()
    except FileNotFoundError:
        # Best-effort: allow gateway to operate without tier inference
        return


def _infer_domain_and_tier(text: str) -> tuple[str | None, int | None]:
    _ensure_registry_loaded()
    lowered = text.lower()
    try:
        domains = DomainRegistry.active()
    except Exception:
        return None, None
    for domain in domains:
        if domain.key.lower() in lowered:
            return domain.key, domain.content.tier if domain.content else None
        for product in domain.products:
            if product.lower() in lowered:
                return domain.key, domain.content.tier if domain.content else None
    return None, None


_PRIORITY_OVERRIDE = {
    "p0": TaskPriority.CRITICAL,
    "p1": TaskPriority.HIGH,
    "p2": TaskPriority.MEDIUM,
    "p3": TaskPriority.LOW,
    "urgent": TaskPriority.CRITICAL,
    "asap": TaskPriority.HIGH,
}


def _priority_from_tier(tier: int | None) -> TaskPriority:
    if tier == 1:
        return TaskPriority.HIGH
    if tier == 2:
        return TaskPriority.MEDIUM
    if tier == 3:
        return TaskPriority.LOW
    return TaskPriority.MEDIUM


def parse_intent(message: str) -> Intent:
    raw = message.strip()
    lowered = raw.lower()

    priority_override = None
    for token, priority in _PRIORITY_OVERRIDE.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            priority_override = priority
            break

    agent_match = re.match(r"^@agent\s+(?P<agent>\S+)", lowered)
    fleet_match = re.match(r"^@fleet\b", lowered)
    status_match = re.match(r"^(status|/status)$", lowered)
    approve_match = re.match(r"^(approve|reject|deny)\s+(?P<id>\S+)", lowered)
    task_match = re.match(r"^(@task|/task)\b", lowered)

    domain, tier = _infer_domain_and_tier(lowered)

    if approve_match:
        return Intent(
            kind="approval",
            agent=None,
            text=raw,
            raw=raw,
            priority_override=priority_override,
            domain=domain,
            tier=tier,
        )
    if status_match or (fleet_match and "status" in lowered):
        return Intent(
            kind="status",
            agent=None,
            text=raw,
            raw=raw,
            priority_override=priority_override,
            domain=domain,
            tier=tier,
        )
    if agent_match:
        return Intent(
            kind="direct",
            agent=agent_match.group("agent"),
            text=raw,
            raw=raw,
            priority_override=priority_override,
            domain=domain,
            tier=tier,
        )
    if task_match or fleet_match:
        return Intent(
            kind="task",
            agent=None,
            text=raw,
            raw=raw,
            priority_override=priority_override,
            domain=domain,
            tier=tier,
        )

    return Intent(
        kind="task",
        agent=None,
        text=raw,
        raw=raw,
        priority_override=priority_override,
        domain=domain,
        tier=tier,
    )


def _queue_dir() -> Path:
    return DEFAULT_QUEUE_DIR


def _publish_event(kind: str, payload: dict[str, Any]) -> None:
    try:
        bus = get_event_bus()
        bus.publish(kind, payload)
    except Exception:
        # Best-effort: do not block webhook response on SSE failures
        return


def _make_task(intent: Intent, message: ChatMessage) -> Task:
    priority = intent.priority_override or _priority_from_tier(intent.tier)
    return Task(
        id="",
        title=intent.text[:80],
        description=intent.text,
        priority=priority,
        status=TaskStatus.PENDING,
        domain=intent.domain,
        project=None,
        required_role=None,
        created_by=message.user_id,
        context={"source": message.source, "raw": intent.raw, "metadata": message.metadata},
    )


def _dispatch_to_agent(agent: str, task_text: str) -> bool:
    """Dispatch through the same verified path used by `forge dispatch send`."""
    target = resolve_target(agent)
    if not target:
        return False
    try:
        from forge_harness.fleet.dispatch_client import send_sync as dispatch_send_sync

        result = dispatch_send_sync(
            target=target,
            message=task_text,
            cli_type="auto",
            verify=True,
            timeout=20.0,
            max_retries=3,
            check_git_lock=True,
            skip_readiness=False,
        )
    except Exception:
        return False
    return bool(getattr(result, "success", False))


def _dispatch_to_tmux(agent: str, task_text: str) -> bool:
    """Deprecated alias kept for compatibility with older tests/callers."""
    return _dispatch_to_agent(agent, task_text)


def _auth_result_matches(result: AuthResult | str, expected: AuthResult) -> bool:
    """Support enum and historical string result tokens in tests/callers."""
    if result == expected:
        return True
    if not isinstance(result, str):
        return False
    normalized = result.strip().lower()
    aliases: dict[AuthResult, set[str]] = {
        AuthResult.SUCCESS: {"success", "authorized", "ok"},
        AuthResult.MISSING_TOKEN: {"missing_token", "missing", "no_token"},
        AuthResult.INVALID_TOKEN: {"invalid_token", "invalid", "forbidden"},
    }
    return normalized in aliases.get(expected, set())


def _verify_auth(request: Request, auth_config: AuthConfig) -> None:
    auth_header = request.headers.get("Authorization")
    client_host = request.client.host if request.client else None
    if auth_config.allow_localhost and client_host in {"127.0.0.1", "::1", "localhost"}:
        return
    result = verify_bearer_token(auth_header, auth_config, client_host)
    if _auth_result_matches(result, AuthResult.SUCCESS):
        return
    if _auth_result_matches(result, AuthResult.MISSING_TOKEN):
        raise HTTPException(status_code=401, detail="Missing authentication token")
    raise HTTPException(status_code=403, detail="Invalid authentication token")


def register_openclaw_routes(app: FastAPI, auth_config: AuthConfig | None = None) -> None:
    _auth_config = auth_config or AuthConfig.from_env()
    queue = TaskQueue(_queue_dir())

    @app.post("/api/openclaw/chat", response_model=DispatchResponse)
    async def openclaw_chat(message: ChatMessage, request: Request) -> DispatchResponse:
        _verify_auth(request, _auth_config)
        intent = parse_intent(message.text)

        if intent.kind == "status":
            _publish_event("openclaw.status.requested", {"user": message.user_id})
            return DispatchResponse(status="status", detail="status requested")

        if intent.kind == "approval":
            _publish_event(
                "openclaw.approval.received",
                {"user": message.user_id, "text": message.text},
            )
            return DispatchResponse(status="approval", detail="approval captured")

        task = _make_task(intent, message)
        task = await queue.add(task)

        dispatched = False
        agent = intent.agent
        if agent:
            dispatched = _dispatch_to_agent(agent, task.description)
            if dispatched:
                task.status = TaskStatus.ASSIGNED
                task.assigned_agent = agent
                task.assigned_at = datetime.now(UTC).isoformat()
                await queue.update(task)

        _publish_event(
            "openclaw.task.created",
            {
                "task_id": task.id,
                "priority": task.priority.value,
                "tier": intent.tier,
                "domain": intent.domain,
                "dispatched": dispatched,
                "agent": agent,
            },
        )

        return DispatchResponse(
            status="queued",
            task_id=task.id,
            dispatched=dispatched,
            agent=agent,
            priority=task.priority.value,
            tier=intent.tier,
        )

    @app.get("/api/openclaw/status", response_model=StatusResponse)
    async def openclaw_status(request: Request) -> StatusResponse:
        _verify_auth(request, _auth_config)
        sessions = [
            {
                "name": s.name,
                "created_at": s.created_at,
                "windows": s.windows,
                "attached": s.attached,
                "tail": capture_tail(s.name, lines=10),
            }
            for s in list_sessions()
        ]
        pending = await queue.list_all(status=TaskStatus.PENDING)
        return StatusResponse(sessions=sessions, queue_depth=len(pending))

    @app.get("/api/openclaw/events")
    async def openclaw_events(request: Request) -> StreamingResponse:
        _verify_auth(request, _auth_config)
        event_bus = get_event_bus()
        queue_events = event_bus.subscribe()

        async def event_stream() -> AsyncIterator[str]:
            try:
                while True:
                    event = await queue_events.get()
                    if event is None:
                        break
                    if isinstance(event, SSEEvent):
                        yield event.to_sse()
                    else:
                        yield SSEEvent(event="message", data=event).to_sse()
            finally:
                event_bus.unsubscribe(queue_events)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @app.post("/api/openclaw/dispatch", response_model=DispatchResponse)
    async def openclaw_dispatch(payload: dict[str, Any], request: Request) -> DispatchResponse:
        _verify_auth(request, _auth_config)
        agent = payload.get("agent")
        task_text = payload.get("text")
        if not agent or not task_text:
            raise HTTPException(status_code=400, detail="agent and text are required")
        dispatched = _dispatch_to_agent(agent, task_text)
        if dispatched:
            _publish_event(
                "openclaw.task.dispatched",
                {"agent": agent, "text": task_text},
            )
            return DispatchResponse(status="dispatched", dispatched=True, agent=agent)
        return DispatchResponse(status="failed", dispatched=False, agent=agent)

    @app.post("/api/openclaw/notify")
    async def openclaw_notify(payload: dict[str, Any], request: Request) -> JSONResponse:
        _verify_auth(request, _auth_config)
        event_type = payload.get("event", "openclaw.notification")
        data = payload.get("data", payload)
        _publish_event(event_type, data)
        return JSONResponse({"status": "ok"})
