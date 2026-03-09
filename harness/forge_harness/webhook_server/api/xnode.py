"""XNode Realtime Bridge API Endpoint

REST API for receiving cross-node messages from CLI realtime delivery.
Consolidated from inline webhook_server_main.py implementation.

Endpoints:
    POST /api/xnode/events - Receive xnode realtime events

Spec: docs/runbooks/CANONICAL_WORKFLOW.md
Schema: see XNodeEvent/XNodePayload Pydantic models in this file
"""

import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.core.dependencies import verify_auth
from forge_harness.webhook_server.services.event_bus import get_event_bus

logger = get_logger(__name__)

router = APIRouter()

# Idempotency deduplication (in-memory, bounded TTL/LRU)
_SEEN_KEYS_TTL_SECONDS = 60 * 60
_SEEN_KEYS_MAX_ENTRIES = 10_000
_seen_keys: OrderedDict[str, float] = OrderedDict()
_seen_keys_lock = Lock()


# =============================================================================
# Request Model
# =============================================================================


class XNodeEventRequest(BaseModel):
    """Request body for xnode realtime events."""

    event_type: Literal[
        "lead.send",
        "lead.ack",
        "lead.handoff",
        "lead.broadcast",
        "xnode.relay.exception",
    ] = Field(
        ...,
        description="Event type: lead.send, lead.ack, lead.handoff, lead.broadcast, xnode.relay.exception",
    )
    envelope: dict[str, Any] = Field(
        ..., description="Full message envelope per XNODE_MESSAGE_SCHEMA_v1"
    )


# =============================================================================
# Helpers
# =============================================================================


def _api_response(
    data: Any = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    """Create standardized API response matching the server envelope format."""
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


def _route_channel(event_type: str, envelope: dict[str, Any]) -> str:
    """Route xnode event to logical SSE channel."""
    if event_type == "lead.send":
        target_node = envelope.get("target", {}).get("node", "unknown")
        return f"lead.{target_node}.inbox"
    elif event_type == "lead.ack":
        target_node = envelope.get("target_node", "unknown")
        return f"lead.{target_node}.ack"
    elif event_type == "lead.handoff":
        target_node = envelope.get("target", {}).get("node", "unknown")
        return f"lead.{target_node}.handoff"
    elif event_type == "xnode.relay.exception":
        return "xnode.exceptions"
    elif event_type == "lead.broadcast":
        return "lead.broadcast"
    return f"xnode.{event_type}"


def _prune_seen_keys(now_monotonic: float) -> None:
    """Evict expired idempotency keys and trim to max size."""
    while _seen_keys:
        first_key = next(iter(_seen_keys))
        expires_at = _seen_keys[first_key]
        if expires_at > now_monotonic:
            break
        _seen_keys.popitem(last=False)
    while len(_seen_keys) > _SEEN_KEYS_MAX_ENTRIES:
        _seen_keys.popitem(last=False)


def _is_duplicate_idempotency_key(
    idem_key: str,
    *,
    now_monotonic: float | None = None,
) -> bool:
    """Return True when the key is still within the dedupe window."""
    if not idem_key:
        return False
    now_value = now_monotonic if now_monotonic is not None else time.monotonic()
    with _seen_keys_lock:
        _prune_seen_keys(now_value)
        expires_at = _seen_keys.get(idem_key)
        if expires_at is not None and expires_at > now_value:
            _seen_keys.move_to_end(idem_key, last=True)
            return True
        _seen_keys[idem_key] = now_value + _SEEN_KEYS_TTL_SECONDS
        _seen_keys.move_to_end(idem_key, last=True)
        _prune_seen_keys(now_value)
        return False


def _reset_seen_keys_for_tests() -> None:
    """Test helper to clear dedupe state."""
    with _seen_keys_lock:
        _seen_keys.clear()


# =============================================================================
# Endpoint
# =============================================================================


@router.post("/api/xnode/events")
async def receive_xnode_event(
    body: XNodeEventRequest,
    request: Request,
    _: None = Depends(verify_auth),
):
    """Bridge endpoint for xnode realtime events.

    Validates envelope, checks idempotency, and publishes to the SSE event
    bus for Command Center dashboards. This endpoint does not persist payloads;
    callers must already have written their durable local/outbox record.
    """
    envelope = body.envelope

    # Validate required envelope fields
    required_fields = ["schema_version", "message_id", "type"]
    missing = [f for f in required_fields if f not in envelope]
    if missing:
        return JSONResponse(
            status_code=400,
            content=_api_response(
                error_code="invalid_envelope",
                error_message=f"Missing required envelope fields: {missing}",
            ),
        )

    if envelope.get("schema_version") != "1.0":
        return JSONResponse(
            status_code=400,
            content=_api_response(
                error_code="unsupported_schema",
                error_message=f"Unsupported schema_version: {envelope.get('schema_version')}",
            ),
        )

    # Idempotency check
    idem_key = envelope.get("idempotency_key", "")
    if idem_key and _is_duplicate_idempotency_key(idem_key):
        return JSONResponse(
            status_code=200,
            content=_api_response(
                {
                    "accepted": True,
                    "delivery_id": f"rt_{envelope['message_id']}_dup",
                    "channel": _route_channel(body.event_type, envelope),
                    "deduplicated": True,
                }
            ),
        )

    # Route to logical channel
    channel = _route_channel(body.event_type, envelope)

    # Publish to SSE event bus
    delivery_id = f"rt_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    event_bus = get_event_bus()
    await event_bus.publish(
        channel,
        {
            "delivery_id": delivery_id,
            "event_type": body.event_type,
            "envelope": envelope,
        },
        source="xnode-bridge",
    )

    logger.info(f"XNode event published: {body.event_type} -> {channel} ({delivery_id})")

    return JSONResponse(
        status_code=202,
        content=_api_response(
            {
                "accepted": True,
                "delivery_id": delivery_id,
                "channel": channel,
            }
        ),
    )
