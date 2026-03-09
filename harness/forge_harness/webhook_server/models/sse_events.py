"""SSE Event definitions for Phase 4 real-time wiring.

Defines the typed event contract used to publish events from feature_tracker,
evaluator, and session_manager through the EventEmitter to SSE subscribers.

Classes
-------
SSEEventType
    Enum of all recognized event types in the system.
SSEEvent
    Pydantic model for a structured event payload.

Functions
---------
serialize_sse(event: SSEEvent) -> str
    Formats an SSEEvent as a valid SSE wire-format string.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Event type taxonomy
# ---------------------------------------------------------------------------


class SSEEventType(str, Enum):
    """All recognized SSE event types.

    String enum so values can be compared directly to strings without
    extra unwrapping (e.g. ``SSEEventType.feature_updated == "feature.updated"``).
    """

    # Feature events
    feature_updated = "feature.updated"
    feature_assigned = "feature.assigned"

    # Evaluator events
    evaluator_completed = "evaluator.completed"
    evaluator_failed = "evaluator.failed"

    # Session events
    session_created = "session.created"
    session_ended = "session.ended"

    # Task events
    task_created = "task.created"
    task_assigned = "task.assigned"
    task_completed = "task.completed"
    task_status_changed = "task.status_changed"

    # Dispatch events (CP-2006)
    dispatch = "dispatch"

    # Agent events
    agent_spawned = "agent.spawned"
    agent_rotated = "agent.rotated"
    agent_exhausted = "agent.exhausted"
    agent_heartbeat = "agent.heartbeat"
    agent_idle = "agent.idle"
    agent_busy = "agent.busy"

    # Fleet events
    fleet_heartbeat = "fleet.heartbeat"
    node_joined = "node.joined"
    heartbeat_stale = "heartbeat.stale"

    # SLO events
    task_slo_warning = "task.slo.warning"
    task_slo_breached = "task.slo.breached"

    # Heartbeat loop events
    heartbeat_loop_eval = "heartbeat_loop.eval"


# ---------------------------------------------------------------------------
# SSEEvent model
# ---------------------------------------------------------------------------


class SSEEvent(BaseModel):
    """Structured payload for a single Server-Sent Event.

    Attributes:
        event_type:     One of the :class:`SSEEventType` values.
        data:           Arbitrary JSON-serialisable event payload.
        timestamp:      UTC ISO-8601 timestamp; auto-set if not provided.
        source_service: Identifier of the service that produced the event.
    """

    event_type: SSEEventType = Field(..., description="Event type enum value.")
    data: dict = Field(default_factory=dict, description="Arbitrary event payload.")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="UTC ISO-8601 timestamp.",
    )
    source_service: str = Field(
        default="webhook-server",
        description="Originating service identifier.",
    )

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Wire-format serializer
# ---------------------------------------------------------------------------


def serialize_sse(event: SSEEvent) -> str:
    """Format an :class:`SSEEvent` as the SSE wire format.

    Produces::

        event: <event_type_value>
        data: <json_payload>
        \\n

    The ``data`` field is a JSON object that includes all :class:`SSEEvent`
    fields so consumers can reconstruct the full event from the data line
    alone without parsing the ``event:`` directive.

    Args:
        event: The event to serialise.

    Returns:
        A string ready for streaming over an HTTP SSE connection.
    """
    payload = {
        "event_type": event.event_type.value,
        "data": event.data,
        "timestamp": event.timestamp,
        "source_service": event.source_service,
    }
    lines = [
        f"event: {event.event_type.value}",
        f"data: {json.dumps(payload)}",
        "",  # blank line terminates the event
        "",  # extra blank for the trailing \n\n
    ]
    return "\n".join(lines)
