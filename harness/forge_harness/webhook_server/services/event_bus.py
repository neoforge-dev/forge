"""
Event Bus Service (SSE)

Singleton event bus for publishing Server-Sent Events.
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class SSEEvent:
    """Server-Sent Event structure.

    Frontend expects data to be a JSON object with:
    - id: event ID
    - type: event type (maps to 'event' field in SSE protocol)
    - timestamp: ISO 8601 timestamp
    - source: source identifier (e.g., 'webhook-server')
    - data: actual event payload
    """

    id: str
    event: str  # Event type (e.g., 'agent.progress')
    data: dict[str, Any]  # Event payload
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    source: str = "webhook-server"  # Source identifier for frontend

    def to_sse_format(self) -> str:
        """Format event for SSE transmission.

        SSE format:
            id: <event_id>
            event: <event_type>
            data: <json_payload>
            <blank line>

        The data field contains a JSON object with id, type, timestamp, source, and data fields
        to match frontend expectations.
        """
        # Frontend expects data to be structured with these fields
        payload = {
            "id": self.id,
            "type": self.event,  # Frontend uses 'type' instead of 'event'
            "timestamp": self.timestamp,
            "source": self.source,
            "data": self.data,  # Actual event data
        }

        lines = [
            f"id: {self.id}",
            f"event: {self.event}",
            f"data: {json.dumps(payload)}",
            "",  # Empty line to terminate event
        ]
        return "\n".join(lines) + "\n"


class EventBus:
    """Singleton event bus for publishing SSE events.

    Supports multiple concurrent SSE connections via asyncio.Queue.
    Events are published to all connected subscribers.

    Event types:
        - agent.registered: New agent registered
        - agent.progress: Agent progress updated
        - agent.completed: Agent completed task
        - approval.created: New approval request created
        - approval.resolved: Approval request resolved
    """

    _instance: "EventBus | None" = None
    _lock: Lock = Lock()

    def __new__(cls) -> "EventBus":
        """Singleton pattern implementation."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        """Initialize event bus (only once due to singleton)."""
        if getattr(self, "_initialized", False):
            return
        self._subscribers: list[asyncio.Queue[SSEEvent | None]] = []
        self._subscriber_lock = Lock()
        self._event_counter = 0
        self._initialized = True
        logger.info("EventBus initialized")

    def subscribe(self) -> asyncio.Queue[SSEEvent | None]:
        """Subscribe to events, returning a queue for receiving events.

        Returns:
            asyncio.Queue that will receive SSEEvent objects.
            None is sent when connection should close.
        """
        queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue(maxsize=100)
        with self._subscriber_lock:
            self._subscribers.append(queue)
        logger.debug(f"New SSE subscriber, total: {len(self._subscribers)}")
        return queue

    def unsubscribe(self, queue: asyncio.Queue[SSEEvent | None]) -> None:
        """Unsubscribe from events.

        Args:
            queue: The queue returned from subscribe()
        """
        with self._subscriber_lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)
        logger.debug(f"SSE subscriber removed, total: {len(self._subscribers)}")

    async def publish(
        self, event_type: str, data: dict[str, Any], source: str = "webhook-server"
    ) -> None:
        """Publish an event to all subscribers.

        Args:
            event_type: Event type (e.g., "agent.registered")
            data: Event data payload
            source: Source identifier (default: "webhook-server")
        """
        self._event_counter += 1
        event = SSEEvent(
            id=f"{self._event_counter}",
            event=event_type,
            data=data,
            source=source,
        )

        with self._subscriber_lock:
            subscribers = list(self._subscribers)

        if subscribers:
            logger.info(f"Publishing event {event_type} to {len(subscribers)} subscribers")
        else:
            logger.debug(f"Publishing event {event_type} but no subscribers connected")

        for queue in subscribers:
            try:
                # Non-blocking put, drop event if queue is full
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(f"SSE queue full, dropping event {event_type}")

        logger.debug(f"Published event {event_type} to {len(subscribers)} subscribers")

    async def close_all(self) -> None:
        """Close all subscriber connections."""
        with self._subscriber_lock:
            subscribers = list(self._subscribers)

        for queue in subscribers:
            try:
                queue.put_nowait(None)  # Signal to close
            except asyncio.QueueFull:
                pass

        logger.info("Closed all SSE connections")


# Global event bus
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get or create global event bus."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
