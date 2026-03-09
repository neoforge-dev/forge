"""Unit tests for forge_harness.webhook_server.services.event_bus.

Covers:
- SSEEvent dataclass construction and to_sse_format() serialisation
- EventBus singleton behaviour
- subscribe / unsubscribe queue management
- publish: event counter, event fields, delivery to all queues, QueueFull handling
- close_all: None sentinel delivery, QueueFull handling
- get_event_bus() factory

All external I/O is mocked — no network, DB, or file calls.
"""

from __future__ import annotations

import asyncio
import json
from threading import Lock
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.services.event_bus import (
    EventBus,
    SSEEvent,
    get_event_bus,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_event_bus() -> EventBus:
    """Return a brand-new EventBus without relying on the singleton."""
    # Reset the singleton so each test can get a clean instance when needed.
    with EventBus._lock:
        EventBus._instance = None
    bus = EventBus()
    return bus


# ---------------------------------------------------------------------------
# SSEEvent tests
# ---------------------------------------------------------------------------


class TestSSEEvent:
    def test_default_timestamp_and_source(self):
        evt = SSEEvent(id="1", event="agent.progress", data={"pct": 50})
        assert evt.timestamp  # not empty
        assert evt.source == "webhook-server"

    def test_custom_source(self):
        evt = SSEEvent(id="2", event="agent.completed", data={}, source="my-svc")
        assert evt.source == "my-svc"

    def test_to_sse_format_structure(self):
        evt = SSEEvent(id="42", event="agent.registered", data={"agent_id": "a1"})
        formatted = evt.to_sse_format()

        lines = formatted.split("\n")
        assert lines[0] == "id: 42"
        assert lines[1] == "event: agent.registered"
        assert lines[2].startswith("data: ")
        # blank line terminates event
        assert lines[3] == ""
        # trailing newline means the string ends with \n
        assert formatted.endswith("\n")

    def test_to_sse_format_data_payload(self):
        evt = SSEEvent(
            id="7",
            event="approval.created",
            data={"key": "val"},
            source="svc-x",
        )
        formatted = evt.to_sse_format()
        data_line = [l for l in formatted.split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])

        assert payload["id"] == "7"
        assert payload["type"] == "approval.created"
        assert payload["source"] == "svc-x"
        assert payload["data"] == {"key": "val"}
        assert "timestamp" in payload

    def test_to_sse_format_empty_data(self):
        evt = SSEEvent(id="0", event="ping", data={})
        formatted = evt.to_sse_format()
        assert "data: " in formatted

    def test_timestamp_is_iso8601(self):
        import re
        evt = SSEEvent(id="3", event="test", data={})
        # ISO 8601 basic pattern
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", evt.timestamp)


# ---------------------------------------------------------------------------
# EventBus singleton tests
# ---------------------------------------------------------------------------


class TestEventBusSingleton:
    def setup_method(self):
        # Reset singleton before each test
        with EventBus._lock:
            EventBus._instance = None

    def test_singleton_same_instance(self):
        bus1 = EventBus()
        bus2 = EventBus()
        assert bus1 is bus2

    def test_initialization_once(self):
        bus = EventBus()
        counter_before = bus._event_counter
        # Creating another instance must not reset state
        bus._event_counter = 99
        bus2 = EventBus()
        assert bus2._event_counter == 99

    def test_initialized_flag(self):
        bus = EventBus()
        assert bus._initialized is True


# ---------------------------------------------------------------------------
# Subscribe / Unsubscribe
# ---------------------------------------------------------------------------


class TestEventBusSubscription:
    def setup_method(self):
        with EventBus._lock:
            EventBus._instance = None

    def test_subscribe_returns_queue(self):
        bus = EventBus()
        q = bus.subscribe()
        assert isinstance(q, asyncio.Queue)

    def test_subscribe_adds_to_subscribers(self):
        bus = EventBus()
        assert len(bus._subscribers) == 0
        bus.subscribe()
        assert len(bus._subscribers) == 1

    def test_multiple_subscribers(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        assert len(bus._subscribers) == 2
        assert q1 is not q2

    def test_unsubscribe_removes_queue(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        assert q not in bus._subscribers

    def test_unsubscribe_nonexistent_is_noop(self):
        bus = EventBus()
        orphan_q: asyncio.Queue = asyncio.Queue()
        # Must not raise
        bus.unsubscribe(orphan_q)
        assert len(bus._subscribers) == 0

    def test_unsubscribe_only_removes_target(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.unsubscribe(q1)
        assert q1 not in bus._subscribers
        assert q2 in bus._subscribers


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


class TestEventBusPublish:
    def setup_method(self):
        with EventBus._lock:
            EventBus._instance = None

    @pytest.mark.asyncio
    async def test_publish_increments_counter(self):
        bus = EventBus()
        await bus.publish("agent.progress", {"pct": 10})
        assert bus._event_counter == 1
        await bus.publish("agent.progress", {"pct": 20})
        assert bus._event_counter == 2

    @pytest.mark.asyncio
    async def test_publish_delivers_to_subscriber(self):
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("agent.registered", {"id": "a1"})
        assert not q.empty()
        event = q.get_nowait()
        assert isinstance(event, SSEEvent)
        assert event.event == "agent.registered"
        assert event.data == {"id": "a1"}

    @pytest.mark.asyncio
    async def test_publish_delivers_to_all_subscribers(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        await bus.publish("approval.resolved", {"rid": "r1"})
        e1 = q1.get_nowait()
        e2 = q2.get_nowait()
        assert e1.event == "approval.resolved"
        assert e2.event == "approval.resolved"

    @pytest.mark.asyncio
    async def test_publish_event_id_matches_counter(self):
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("ping", {})
        event = q.get_nowait()
        assert event.id == "1"

    @pytest.mark.asyncio
    async def test_publish_custom_source(self):
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("agent.completed", {}, source="custom-svc")
        event = q.get_nowait()
        assert event.source == "custom-svc"

    @pytest.mark.asyncio
    async def test_publish_no_subscribers_no_error(self):
        bus = EventBus()
        # Should complete without raising
        await bus.publish("agent.progress", {"pct": 5})
        assert bus._event_counter == 1

    @pytest.mark.asyncio
    async def test_publish_drops_when_queue_full(self):
        bus = EventBus()
        # Create a full queue (maxsize=1)
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        q.put_nowait(SSEEvent(id="0", event="pre", data={}))  # fill it
        with bus._subscriber_lock:
            bus._subscribers.append(q)

        # Publish should not raise even though queue is full
        await bus.publish("agent.progress", {"pct": 99})
        # The pre-loaded event is still there; the new one was dropped
        assert q.qsize() == 1
        pre_event = q.get_nowait()
        assert pre_event.event == "pre"

    @pytest.mark.asyncio
    async def test_publish_multiple_events_sequential_ids(self):
        bus = EventBus()
        q = bus.subscribe()
        for i in range(5):
            await bus.publish("tick", {"i": i})
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        ids = [e.id for e in events]
        assert ids == ["1", "2", "3", "4", "5"]


# ---------------------------------------------------------------------------
# close_all
# ---------------------------------------------------------------------------


class TestEventBusCloseAll:
    def setup_method(self):
        with EventBus._lock:
            EventBus._instance = None

    @pytest.mark.asyncio
    async def test_close_all_sends_none_sentinel(self):
        bus = EventBus()
        q = bus.subscribe()
        await bus.close_all()
        sentinel = q.get_nowait()
        assert sentinel is None

    @pytest.mark.asyncio
    async def test_close_all_multiple_subscribers(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        await bus.close_all()
        assert q1.get_nowait() is None
        assert q2.get_nowait() is None

    @pytest.mark.asyncio
    async def test_close_all_no_subscribers_no_error(self):
        bus = EventBus()
        await bus.close_all()  # must not raise

    @pytest.mark.asyncio
    async def test_close_all_full_queue_silently_skips(self):
        bus = EventBus()
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        q.put_nowait(SSEEvent(id="x", event="pre", data={}))  # fill queue
        with bus._subscriber_lock:
            bus._subscribers.append(q)

        # Should not raise even if queue is full
        await bus.close_all()
        # Queue still contains the original event (None was dropped silently)
        assert q.qsize() == 1


# ---------------------------------------------------------------------------
# get_event_bus factory
# ---------------------------------------------------------------------------


class TestGetEventBus:
    def setup_method(self):
        import forge_harness.webhook_server.services.event_bus as _mod
        _mod._event_bus = None
        with EventBus._lock:
            EventBus._instance = None

    def test_get_event_bus_returns_event_bus(self):
        bus = get_event_bus()
        assert isinstance(bus, EventBus)

    def test_get_event_bus_same_instance_on_repeated_calls(self):
        b1 = get_event_bus()
        b2 = get_event_bus()
        assert b1 is b2

    def test_get_event_bus_creates_instance_if_none(self):
        import forge_harness.webhook_server.services.event_bus as _mod
        _mod._event_bus = None
        bus = get_event_bus()
        assert bus is not None


# ---------------------------------------------------------------------------
# Thread-safety smoke test
# ---------------------------------------------------------------------------


class TestEventBusThreadSafety:
    def setup_method(self):
        with EventBus._lock:
            EventBus._instance = None

    def test_concurrent_subscribe_unsubscribe(self):
        """Subscribe and unsubscribe from multiple threads without deadlock."""
        import threading

        bus = EventBus()
        errors: list[Exception] = []

        def worker():
            try:
                q = bus.subscribe()
                bus.unsubscribe(q)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert errors == []
        assert len(bus._subscribers) == 0
