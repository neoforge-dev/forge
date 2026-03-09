"""Legacy SSE and Event endpoints — extracted from webhook_server_main.py.

Handles /api/events, /api/events/debug, /api/events/test,
/api/completions, /api/orchestrator/events, /api/sse/health routes.
"""

import asyncio
import os
import secrets
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.infrastructure.auth import (
    AuthConfig,
    AuthResult,
    verify_bearer_token,
)
from forge_harness.webhook_server.models.legacy_models import (
    CompletionRequest,
    OrchestratorEventRequest,
    api_response,
)
from forge_harness.webhook_server.services.event_bus import SSEEvent, get_event_bus

logger = get_logger(__name__)

router = APIRouter(tags=["legacy-sse"])

security = HTTPBearer(auto_error=False)

# SSE Heartbeat configuration
SSE_HEARTBEAT_INTERVAL = int(os.environ.get("SSE_HEARTBEAT_INTERVAL", "30"))

# Connection tracking for debugging
_sse_connections: dict[str, dict[str, Any]] = {}
_sse_connection_counter = 0


def _is_localhost_request(client_host: str | None, host_header: str) -> bool:
    """Check if the request is from localhost."""
    if not client_host:
        return False
    localhost_patterns = ["localhost", "127.0.0.1", "::1", "0.0.0.0"]
    return any(pattern in client_host.lower() for pattern in localhost_patterns) or any(
        pattern in host_header.lower() for pattern in localhost_patterns
    )


async def _event_generator(
    queue: asyncio.Queue[SSEEvent | None],
    connection_id: str,
    event_bus: Any,
):
    """Generate SSE events from queue."""
    try:
        logger.info(f"[SSE:{connection_id}] Connection established")

        # Send initial retry directive
        yield "retry: 3000\n\n"

        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=float(SSE_HEARTBEAT_INTERVAL)
                )
                if event is None:
                    logger.info(f"[SSE:{connection_id}] Received close signal")
                    break

                formatted = event.to_sse_format()
                logger.debug(f"[SSE:{connection_id}] Sending event: {event.event}")
                yield formatted

            except TimeoutError:
                logger.debug(f"[SSE:{connection_id}] Sending heartbeat")
                heartbeat = SSEEvent(
                    id=f"heartbeat_{int(time.time())}",
                    event="heartbeat",
                    data={"timestamp": datetime.now(UTC).isoformat()},
                    source="webhook-server",
                )
                yield heartbeat.to_sse_format()

    except asyncio.CancelledError:
        logger.info(f"[SSE:{connection_id}] Connection cancelled")
    except Exception as e:
        logger.error(f"[SSE:{connection_id}] Error in event generator: {e}")
    finally:
        event_bus.unsubscribe(queue)
        if connection_id in _sse_connections:
            del _sse_connections[connection_id]
        logger.info(
            f"[SSE:{connection_id}] Connection closed, active connections: {len(_sse_connections)}"
        )


@router.get("/api/events")
async def sse_events(
    request: Request,
    token: str | None = Query(
        None, description="Auth token (for EventSource which can't set headers)"
    ),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """Server-Sent Events endpoint for real-time updates."""
    global _sse_connection_counter

    _event_bus = get_event_bus()
    _auth_config = getattr(request.app.state, "auth_config", None) or AuthConfig.from_env()

    # Accept token from query param OR header
    auth_token = token or (credentials.credentials if credentials else None)
    client_host = request.client.host if request.client else None

    # Phase 1.3: Prefer session token
    from forge_harness.webhook_server.infrastructure.sse_session import (
        get_sse_session_store,
    )

    session_store = get_sse_session_store()
    host_header = request.headers.get("host", "")

    if auth_token and session_store.validate(auth_token):
        result = AuthResult.SUCCESS
    elif (
        auth_token
        and token
        and _auth_config.bearer_token
        and (
            _auth_config.sse_require_session_token
            or (
                not _auth_config.allow_localhost
                and not _is_localhost_request(client_host, host_header)
            )
        )
        and secrets.compare_digest(auth_token, _auth_config.bearer_token)
    ):
        raise HTTPException(
            status_code=401,
            detail="Use session token for SSE. Post to /api/auth/sse-session with Bearer header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    else:
        authorization = f"Bearer {auth_token}" if auth_token else None
        if _auth_config.allow_localhost and _is_localhost_request(client_host, host_header):
            result = AuthResult.SUCCESS
        else:
            result = verify_bearer_token(authorization, _auth_config, client_host)

    if result == AuthResult.MISSING_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Missing authentication token. Use ?token=xxx or Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    elif result == AuthResult.INVALID_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token",
        )

    # Create connection tracking
    _sse_connection_counter += 1
    connection_id = f"conn_{_sse_connection_counter}"

    _sse_connections[connection_id] = {
        "connected_at": datetime.now(UTC).isoformat(),
        "client_host": client_host,
    }

    logger.info(
        f"[SSE:{connection_id}] New connection from {client_host}, total connections: {len(_sse_connections)}"
    )

    queue = _event_bus.subscribe()

    _sse_response_headers: dict[str, str] = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    _request_origin = request.headers.get("origin")
    if _request_origin:
        _sse_response_headers["Access-Control-Allow-Origin"] = _request_origin
        _sse_response_headers["Access-Control-Allow-Credentials"] = "true"

    return StreamingResponse(
        _event_generator(queue, connection_id, _event_bus),
        media_type="text/event-stream",
        headers=_sse_response_headers,
    )


@router.get("/api/events/stream")
async def sse_events_stream_alias(
    request: Request,
    token: str | None = Query(
        None, description="Auth token (for EventSource which can't set headers)"
    ),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """Alias for /api/events — backwards compat for remote xnode listeners."""
    return await sse_events(request, token, credentials)


@router.get("/api/events/debug")
async def sse_debug_info(request: Request):
    """Get debug information about SSE connections."""
    _event_bus = get_event_bus()
    return JSONResponse(
        content=api_response(
            {
                "active_connections": len(_sse_connections),
                "connections": _sse_connections,
                "total_events_published": _event_bus._event_counter,
            }
        )
    )


@router.post("/api/events/test")
async def publish_test_event(request: Request):
    """Publish a test event to all SSE subscribers (for debugging)."""
    _event_bus = get_event_bus()
    await _event_bus.publish(
        "system.notification",
        {
            "level": "info",
            "message": "Test event from webhook server",
            "title": "SSE Test",
        },
    )
    return JSONResponse(
        content=api_response(
            {
                "status": "published",
                "active_connections": len(_sse_connections),
            }
        )
    )


@router.post("/api/completions")
async def report_completion(body: CompletionRequest, request: Request):
    """Report task completion. Publishes agent.completed to SSE for Command Center."""
    _event_bus = get_event_bus()
    await _event_bus.publish(
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


@router.post("/api/orchestrator/events")
async def report_orchestrator_event(body: OrchestratorEventRequest, request: Request):
    """Report orchestrator heartbeat or dispatch. Publishes to SSE for Command Center."""
    _event_bus = get_event_bus()
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
    await _event_bus.publish(event_type, data, source="orchestrator-heartbeat")
    return JSONResponse(
        content=api_response(
            {
                "status": "published",
                "event_type": event_type,
            }
        )
    )


@router.get("/api/sse/health")
async def sse_health_check():
    """Health check endpoint for SSE connections."""
    try:
        event_bus = get_event_bus()
        last_event_time = None

        if hasattr(event_bus, "_last_event_time") and event_bus._last_event_time:
            last_event_time = event_bus._last_event_time.isoformat()

        return JSONResponse(
            content=api_response(
                {
                    "status": "healthy",
                    "event_bus": "running",
                    "active_connections": len(_sse_connections),
                    "subscribers_count": len(event_bus._subscribers)
                    if hasattr(event_bus, "_subscribers")
                    else 0,
                    "last_event_time": last_event_time,
                    "heartbeat_interval": SSE_HEARTBEAT_INTERVAL,
                }
            )
        )
    except Exception as e:
        return JSONResponse(
            content=api_response(
                error_code="SSE_HEALTH_ERROR",
                error_message=f"SSE health check failed: {str(e)}",
            ),
            status_code=503,
        )
