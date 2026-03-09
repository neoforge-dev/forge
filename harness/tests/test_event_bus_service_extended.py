"""Extended tests for EventBus service (SSE).

Covers edge cases, concurrent behaviour, SSEEvent formatting details,
and the get_event_bus() factory not exercised by the base test file.
"""

import asyncio
import json
from datetime import UTC, datetime

import pytest

from forge_harness.webhook_server.services.event_bus import (
    EventBus,
    SSEEvent,
    get_event_bus,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def fresh_bus():
    """Isolated EventBus instance (singleton reset before and after)."""
    EventBus._instance = None
    bus = EventBus()
    yield bus
    EventBus._instance = None


# ---------------------------------------------------------------------------
# SSEEvent – formatting and field coverage
# ---------------------------------------------------------------------------


class TestSSEEventExtended:
    """Additional SSEEvent formatting and edge-case tests."""

    def test_default_source_is_webhook_server(self):
        event = SSEEvent(id="1", event="ping", data={})
        assert event.source == "webhook-server"

    def test_timestamp_is_utc_iso(self):
        before = datetime.now(UTC)
        event = SSEEvent(id="2", event="ping", data={})
        after = datetime.now(UTC)

        ts = datetime.fromisoformat(event.timestamp)
        assert ts.tzinfo is not None
        assert before <= ts <= after

    def test_to_sse_format_ends_with_double_newline(self):
        event = SSEEvent(id="x", event="test", data={})
        result = event.to_sse_format()
        assert result.endswith("\n\n"), repr(result)

    def test_to_sse_format_id_line_first(self):
        event = SSEEvent(id="42", event="agent.progress", data={"pct": 75})
        lines = event.to_sse_format().split("\n")
        assert lines[0] == "id: 42"

    def test_to_sse_format_event_line_second(self):
        event = SSEEvent(id="1", event="my.event", data={})
        lines = event.to_sse_format().split("\n")
        assert lines[1] == "event: my.event"

    def test_to_sse_format_data_is_valid_json(self):
        event = SSEEvent(id="3", event="test", data={"nested": {"a": 1}})
        result = event.to_sse_format()
        data_line = [l for l in result.split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert isinstance(payload, dict)

    def test_to_sse_format_data_contains_type_not_event_key(self):
        """Frontend expects 'type' in the data JSON, not 'event'."""
        event = SSEEvent(id="5", event="agent.completed", data={})
        result = event.to_sse_format()
        data_line = [l for l in result.split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert "type" in payload
        assert payload["type"] == "agent.completed"
        assert "event" not in payload

    def test_to_sse_format_data_contains_source(self):
        event = SSEEvent(id="6", event="x", data={}, source="my-src")
        result = event.to_sse_format()
        data_line = [l for l in result.split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["source"] == "my-src"

    def test_to_sse_format_data_payload_preserved(self):
        inner = {"agents": [1, 2, 3], "flag": True}
        event = SSEEvent(id="7", event="e", data=inner)
        result = event.to_sse_format()
        data_line = [l for l in result.split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["data"] == inner

    def test_to_sse_format_empty_data(self):
        event = SSEEvent(id="8", event="heartbeat", data={})
        result = event.to_sse_format()
        data_line = [l for l in result.split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["data"] == {}

    def test_event_with_unicode_content(self):
        event = SSEEvent(id="9", event="msg", data={"text": "Héllo wörld 🌍"})
        result = event.to_sse_format()
        assert "Héllo" in result or "\\u" in result  # valid JSON encoding either way

    def test_custom_timestamp_preserved(self):
        ts = "2026-01-01T00:00:00+00:00"
        event = SSEEvent(id="10", event="e", data={}, timestamp=ts)
        assert event.timestamp == ts
        data_line = [l for l in event.to_sse_format().split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["timestamp"] == ts


# ---------------------------------------------------------------------------
# EventBus – subscribe / unsubscribe / singleton
# ---------------------------------------------------------------------------


class TestEventBusSubscriptions:
    def test_subscribe_returns_async_queue(self, fresh_bus):
        q = fresh_bus.subscribe()
        assert isinstance(q, asyncio.Queue)

    def test_subscribe_queue_max_size_100(self, fresh_bus):
        q = fresh_bus.subscribe()
        assert q.maxsize == 100

    def test_unsubscribe_one_of_many(self, fresh_bus):
        q1 = fresh_bus.subscribe()
        q2 = fresh_bus.subscribe()
        q3 = fresh_bus.subscribe()
        fresh_bus.unsubscribe(q2)
        assert len(fresh_bus._subscribers) == 2
        assert q1 in fresh_bus._subscribers
        assert q3 in fresh_bus._subscribers
        assert q2 not in fresh_bus._subscribers

    def test_unsubscribe_same_queue_twice_is_safe(self, fresh_bus):
        q = fresh_bus.subscribe()
        fresh_bus.unsubscribe(q)
        # Second unsubscribe should not raise
        fresh_bus.unsubscribe(q)
        assert len(fresh_bus._subscribers) == 0

    def test_singleton_reinit_does_not_reset_state(self, fresh_bus):
        """Calling EventBus() again must not reset _subscribers."""
        fresh_bus.subscribe()
        fresh_bus.subscribe()
        bus2 = EventBus()  # same instance
        assert bus2 is fresh_bus
        assert len(bus2._subscribers) == 2


# ---------------------------------------------------------------------------
# EventBus – publish behaviour
# ---------------------------------------------------------------------------


class TestEventBusPublish:
    @pytest.mark.asyncio
    async def test_event_id_starts_at_one(self, fresh_bus):
        q = fresh_bus.subscribe()
        await fresh_bus.publish("e", {})
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert event.id == "1"

    @pytest.mark.asyncio
    async def test_event_ids_are_sequential_strings(self, fresh_bus):
        q = fresh_bus.subscribe()
        for _ in range(5):
            await fresh_bus.publish("e", {})
        ids = []
        for _ in range(5):
            e = await asyncio.wait_for(q.get(), timeout=1.0)
            ids.append(e.id)
        assert ids == ["1", "2", "3", "4", "5"]

    @pytest.mark.asyncio
    async def test_publish_default_source(self, fresh_bus):
        q = fresh_bus.subscribe()
        await fresh_bus.publish("e", {})
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert event.source == "webhook-server"

    @pytest.mark.asyncio
    async def test_publish_carries_correct_event_type(self, fresh_bus):
        q = fresh_bus.subscribe()
        await fresh_bus.publish("agent.registered", {"id": "x"})
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert event.event == "agent.registered"

    @pytest.mark.asyncio
    async def test_publish_carries_correct_data(self, fresh_bus):
        q = fresh_bus.subscribe()
        payload = {"task": "build", "status": "running"}
        await fresh_bus.publish("task.update", payload)
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert event.data == payload

    @pytest.mark.asyncio
    async def test_publish_does_not_raise_with_zero_subscribers(self, fresh_bus):
        # Must not raise
        await fresh_bus.publish("e", {"k": "v"})
        assert fresh_bus._event_counter == 1

    @pytest.mark.asyncio
    async def test_publish_multiple_events_all_received(self, fresh_bus):
        q = fresh_bus.subscribe()
        types = ["a.1", "b.2", "c.3"]
        for t in types:
            await fresh_bus.publish(t, {})
        received = []
        for _ in types:
            e = await asyncio.wait_for(q.get(), timeout=1.0)
            received.append(e.event)
        assert received == types

    @pytest.mark.asyncio
    async def test_full_queue_does_not_affect_other_subscribers(self, fresh_bus):
        """A full queue must not prevent delivery to other subscribers."""
        small_q: asyncio.Queue = asyncio.Queue(maxsize=1)
        normal_q = fresh_bus.subscribe()
        with fresh_bus._subscriber_lock:
            fresh_bus._subscribers.insert(0, small_q)

        await fresh_bus.publish("e1", {})
        await fresh_bus.publish("e2", {})  # small_q drops this

        # normal_q received both
        assert normal_q.qsize() == 2

    @pytest.mark.asyncio
    async def test_publish_after_unsubscribe_not_received(self, fresh_bus):
        q = fresh_bus.subscribe()
        fresh_bus.unsubscribe(q)
        await fresh_bus.publish("ghost", {"x": 1})
        assert q.qsize() == 0

    @pytest.mark.asyncio
    async def test_publish_complex_nested_data(self, fresh_bus):
        q = fresh_bus.subscribe()
        data = {"levels": [{"deep": {"value": 99}}], "meta": None}
        await fresh_bus.publish("deep.event", data)
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert event.data == data


# ---------------------------------------------------------------------------
# EventBus – close_all
# ---------------------------------------------------------------------------


class TestEventBusCloseAll:
    @pytest.mark.asyncio
    async def test_close_all_sends_none_to_every_subscriber(self, fresh_bus):
        queues = [fresh_bus.subscribe() for _ in range(4)]
        await fresh_bus.close_all()
        for q in queues:
            sentinel = await asyncio.wait_for(q.get(), timeout=1.0)
            assert sentinel is None

    @pytest.mark.asyncio
    async def test_close_all_with_no_subscribers_is_safe(self, fresh_bus):
        # No subscribers – must not raise
        await fresh_bus.close_all()

    @pytest.mark.asyncio
    async def test_close_all_with_full_queue_does_not_raise(self, fresh_bus):
        """If a subscriber queue is already full, close_all must swallow QueueFull."""
        full_q: asyncio.Queue = asyncio.Queue(maxsize=1)
        # Pre-fill the queue
        full_q.put_nowait("occupying-item")
        with fresh_bus._subscriber_lock:
            fresh_bus._subscribers.append(full_q)
        # Must not raise
        await fresh_bus.close_all()

    @pytest.mark.asyncio
    async def test_event_counter_not_reset_by_close_all(self, fresh_bus):
        await fresh_bus.publish("e", {})
        await fresh_bus.close_all()
        assert fresh_bus._event_counter == 1


# ---------------------------------------------------------------------------
# get_event_bus factory
# ---------------------------------------------------------------------------


class TestGetEventBusExtended:
    def test_get_event_bus_reuses_existing_instance(self):
        import forge_harness.webhook_server.services.event_bus as eb_module

        eb_module._event_bus = None
        EventBus._instance = None
        b1 = get_event_bus()
        b2 = get_event_bus()
        assert b1 is b2
        # Cleanup
        eb_module._event_bus = None
        EventBus._instance = None

    def test_get_event_bus_creates_event_bus_type(self):
        import forge_harness.webhook_server.services.event_bus as eb_module

        eb_module._event_bus = None
        EventBus._instance = None
        bus = get_event_bus()
        assert isinstance(bus, EventBus)
        # Cleanup
        eb_module._event_bus = None
        EventBus._instance = None

    def test_get_event_bus_if_already_set_returns_existing(self):
        import forge_harness.webhook_server.services.event_bus as eb_module

        EventBus._instance = None
        existing = EventBus()
        eb_module._event_bus = existing
        result = get_event_bus()
        assert result is existing
        # Cleanup
        eb_module._event_bus = None
        EventBus._instance = None
