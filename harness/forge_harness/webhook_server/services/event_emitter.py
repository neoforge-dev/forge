"""Event Emitter Service

Thread-safe singleton that publishes typed SSE events to the event bus and
maintains a bounded in-memory ring of recent events for polling consumers.

Usage::

    from forge_harness.webhook_server.services.event_emitter import (
        get_event_emitter,
        reset_event_emitter,
    )

    emitter = get_event_emitter()

    # Fire-and-forget from synchronous code
    emitter.emit(SSEEventType.feature_updated, {"feature_id": "f-1"}, source="feature_tracker")

    # Poll recent events
    recent = emitter.get_recent_events(limit=10)
    filtered = emitter.get_recent_events(limit=5, event_type="feature.updated")

    # Register a synchronous callback
    emitter.subscribe(lambda event: print(event.event_type))
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import Callable

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.models.sse_events import SSEEvent, SSEEventType

logger = get_logger(__name__)

# Maximum number of events kept in the in-memory ring buffer.
_DEQUE_MAXLEN = 100

# ---------------------------------------------------------------------------
# Module-level singleton state
# ---------------------------------------------------------------------------

_emitter_instance: EventEmitter | None = None
_emitter_lock = threading.Lock()


# ---------------------------------------------------------------------------
# EventEmitter
# ---------------------------------------------------------------------------


class EventEmitter:
    """Publishes typed SSE events to the event bus and a bounded ring buffer.

    All public methods are thread-safe via an internal :class:`threading.RLock`.

    Attributes:
        _recent:     Bounded deque of the last ``_DEQUE_MAXLEN`` events.
        _callbacks:  Registered synchronous subscriber callbacks.
    """

    def __init__(self, maxlen: int = _DEQUE_MAXLEN) -> None:
        self._lock = threading.RLock()
        self._recent: deque[SSEEvent] = deque(maxlen=maxlen)
        self._callbacks: list[Callable[[SSEEvent], None]] = []

    # ------------------------------------------------------------------
    # Core publish path
    # ------------------------------------------------------------------

    def emit(
        self,
        event_type: SSEEventType,
        data: dict,
        source: str = "webhook-server",
    ) -> SSEEvent:
        """Publish an event.

        Builds an :class:`SSEEvent`, stores it in the ring buffer, notifies
        synchronous callbacks, and schedules an async publish on the event bus
        (if an event loop is running in the current thread).

        Args:
            event_type: The :class:`SSEEventType` enum value.
            data:       Arbitrary JSON-serialisable payload dict.
            source:     Identifier of the originating service.

        Returns:
            The constructed :class:`SSEEvent` that was emitted.
        """
        event = SSEEvent(event_type=event_type, data=data, source_service=source)

        with self._lock:
            self._recent.append(event)
            callbacks = list(self._callbacks)

        # Notify synchronous subscribers
        for cb in callbacks:
            try:
                cb(event)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"EventEmitter callback raised: {exc}")

        # Best-effort async publish to the event bus
        self._publish_to_bus(event)

        logger.debug(f"Emitted {event_type.value} from {source}")
        return event

    def _publish_to_bus(self, event: SSEEvent) -> None:
        """Schedule an async publish on the running event loop, if any.

        Safe to call from synchronous code inside or outside an async context.
        Falls back to a fire-and-forget thread when no loop is running.

        Architecture note — single broadcast path:
        EventEmitter is the *typed* entry-point for service-layer code.  It
        stores events in the ring buffer, notifies sync callbacks, then
        delegates to EventBus (the low-level fanout layer) exactly once.
        Direct callers of EventBus.publish() (e.g. webhook_server_main.py
        inline endpoints) bypass EventEmitter intentionally — they already
        hold the final data dict and do not need the ring buffer or typed
        enum validation.  Do NOT call both emitter.emit() and
        event_bus.publish() for the same logical event: each call produces one
        SSE message to every connected client.
        """
        try:
            from forge_harness.webhook_server.services.event_bus import get_event_bus

            bus = get_event_bus()

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    bus.publish(
                        event_type=event.event_type.value,
                        data=event.data,
                        source=event.source_service,
                    )
                )
            except RuntimeError:
                # No running event loop — run in a new thread
                def _run() -> None:
                    asyncio.run(
                        bus.publish(
                            event_type=event.event_type.value,
                            data=event.data,
                            source=event.source_service,
                        )
                    )

                t = threading.Thread(target=_run, daemon=True)
                t.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"EventEmitter could not publish to bus: {exc}")

    # ------------------------------------------------------------------
    # Recent events ring
    # ------------------------------------------------------------------

    def get_recent_events(
        self,
        limit: int = 20,
        event_type: str | None = None,
    ) -> list[SSEEvent]:
        """Return recent events from the ring buffer, newest last.

        Args:
            limit:      Maximum number of events to return.
            event_type: Optional dot-notation event type string to filter by
                        (e.g. ``"feature.updated"``).  ``None`` returns all types.

        Returns:
            A list of :class:`SSEEvent` objects, up to *limit* entries.
        """
        with self._lock:
            events = list(self._recent)

        if event_type is not None:
            events = [e for e in events if e.event_type.value == event_type]

        return events[-limit:] if limit > 0 else []

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def subscribe(self, callback: Callable[[SSEEvent], None]) -> None:
        """Register a synchronous callback invoked on every emitted event.

        The callback receives the :class:`SSEEvent` instance.  Exceptions
        raised inside callbacks are caught and logged but do not propagate.

        Args:
            callback: A callable that accepts a single :class:`SSEEvent`.
        """
        with self._lock:
            self._callbacks.append(callback)

    def unsubscribe(self, callback: Callable[[SSEEvent], None]) -> None:
        """Remove a previously registered callback.

        No-op if the callback is not currently registered.

        Args:
            callback: The callable to remove.
        """
        with self._lock:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def event_count(self) -> int:
        """Return the number of events currently in the ring buffer."""
        with self._lock:
            return len(self._recent)

    def subscriber_count(self) -> int:
        """Return the number of registered synchronous callbacks."""
        with self._lock:
            return len(self._callbacks)


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------


def get_event_emitter(maxlen: int = _DEQUE_MAXLEN) -> EventEmitter:
    """Return the module-level :class:`EventEmitter` singleton.

    Args:
        maxlen: Ring buffer size — used only on the *first* call.

    Returns:
        The singleton :class:`EventEmitter` instance.
    """
    global _emitter_instance

    with _emitter_lock:
        if _emitter_instance is None:
            _emitter_instance = EventEmitter(maxlen=maxlen)
            logger.info("EventEmitter singleton created")
        return _emitter_instance


def reset_event_emitter() -> None:
    """Destroy the singleton instance.

    Primarily useful in tests to ensure isolation between test cases.
    """
    global _emitter_instance
    with _emitter_lock:
        _emitter_instance = None
