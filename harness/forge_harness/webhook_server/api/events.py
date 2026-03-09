"""SSE Events API Endpoints

Provides a polling fallback for recent events.

Endpoints:
    GET /api/events/recent   — JSON polling fallback (last N events)

NOTE: The canonical SSE streaming endpoint is GET /api/events (defined inline
in webhook_server_main.py).  A second streaming endpoint at /api/events/stream
was intentionally removed to prevent duplicate EventBus subscriptions: each
client connection creates a queue in the shared EventBus singleton, so having
two mounted SSE endpoints meant that any client that accidentally opened both
(or any tooling that auto-probed both) would receive every event twice.

If you need typed-model access to the stream, use GET /api/events with the
same auth mechanism (session token or Bearer header).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.sse_events import SSEEvent, SSEEventType  # noqa: F401

logger = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event_to_dict(event: SSEEvent) -> dict[str, Any]:
    """Serialise an :class:`SSEEvent` to a plain dict for JSON responses."""
    return {
        "event_type": event.event_type.value,
        "data": event.data,
        "timestamp": event.timestamp,
        "source_service": event.source_service,
    }


def _get_emitter():
    """Lazy import of EventEmitter singleton to avoid circular imports."""
    from forge_harness.webhook_server.services.event_emitter import get_event_emitter

    return get_event_emitter()


# ---------------------------------------------------------------------------
# Polling fallback endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/api/events/recent",
    summary="Return recent events as JSON (polling fallback)",
    tags=["events"],
)
async def get_recent_events(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of events to return (1-100).",
    ),
    type: str | None = Query(
        None,
        description="Filter events by type (e.g. feature.updated). Omit for all types.",
        alias="type",
    ),
):
    """Return the last N events as JSON for polling-based consumers.

    Events are returned newest-last (ascending timestamp order) so clients
    can append them directly to an existing list.

    Query params:
        limit: Maximum number of events (1-100, default 20).
        type:  Optional event type filter.

    Returns:
        JSON object with ``events`` list and ``count`` integer.
    """
    emitter = _get_emitter()
    events = emitter.get_recent_events(limit=limit, event_type=type)

    return JSONResponse(
        content={
            "success": True,
            "data": {
                "events": [_event_to_dict(e) for e in events],
                "count": len(events),
                "filter": type,
            },
            "error": None,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )
