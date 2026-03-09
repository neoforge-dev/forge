"""Comprehensive tests for the Command Center SSE Event Engine.

Target module: forge_harness/webhook_server/services/event_bus.py

The EventBus is the Command Center's SSE event engine — it manages real-time
Server-Sent Event delivery to connected dashboard clients.

Coverage goals (70%+ per task spec, these tests target 100%):
  - SSEEvent dataclass construction, field defaults, and to_sse_format()
  - EventBus singleton lifecycle (__new__, __init__, _initialized guard)
  - subscribe(): queue creation, maxsize, multi-subscriber tracking
  - unsubscribe(): removal, unknown-queue noop, selective removal
  - publish(): counter increments, event delivery to all queues, QueueFull drop,
               custom source, no-subscriber path, sequential IDs
  - close_all(): None signal delivery, full-queue noop, empty-subscriber noop
  - get_event_bus(): module-level factory, singleton reuse, None-global path
  - Logger calls across all lifecycle points
  - Concurrent subscribe/publish/unsubscribe correctness
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Singleton isolation helpers
# ---------------------------------------------------------------------------


def _reset():
    """Reset both the class-level singleton and the module-level global."""
    import forge_harness.webhook_server.services.event_bus as mod

    mod._event_bus = None
    from forge_harness.webhook_server.services.event_bus import EventBus

    EventBus._instance = None


@pytest.fixture(autouse=True)
def isolated_event_bus():
    """Guarantee a clean EventBus singleton for every test."""
    _reset()
    yield
    _reset()


# ---------------------------------------------------------------------------
# Lazy imports after singleton reset
# ---------------------------------------------------------------------------

from forge_harness.webhook_server.services.event_bus import (  # noqa: E402
    EventBus,
    SSEEvent,
    get_event_bus,
)

# ===========================================================================
# SSEEvent — construction and field semantics
# ===========================================================================


class TestSSEEventConstruction:
    """Validate SSEEvent dataclass field defaults and custom values."""

    def test_required_fields_stored(self):
        evt = SSEEvent(id="1", event="agent.registered", data={"agent": "a1"})
        assert evt.id == "1"
        assert evt.event == "agent.registered"
        assert evt.data == {"agent": "a1"}

    def test_default_source_is_webhook_server(self):
        evt = SSEEvent(id="2", event="ping", data={})
        assert evt.source == "webhook-server"

    def test_custom_source_stored(self):
        evt = SSEEvent(id="3", event="test", data={}, source="my-source")
        assert evt.source == "my-source"

    def test_timestamp_auto_generated(self):
        before = datetime.now(UTC)
        evt = SSEEvent(id="4", event="x", data={})
        after = datetime.now(UTC)
        ts = datetime.fromisoformat(evt.timestamp)
        assert ts.tzinfo is not None
        assert before <= ts <= after

    def test_explicit_timestamp_preserved(self):
        ts = "2026-03-01T12:00:00+00:00"
        evt = SSEEvent(id="5", event="y", data={}, timestamp=ts)
        assert evt.timestamp == ts

    def test_data_field_stores_nested_structures(self):
        payload = {"a": [1, 2, 3], "b": {"c": True}}
        evt = SSEEvent(id="6", event="nested", data=payload)
        assert evt.data == payload

    def test_empty_data_dict_accepted(self):
        evt = SSEEvent(id="7", event="empty", data={})
        assert evt.data == {}


# ===========================================================================
# SSEEvent — to_sse_format()
# ===========================================================================


class TestSSEEventFormat:
    """Validate the SSE wire format produced by to_sse_format()."""

    def _make_event(
        self,
        event_id: str = "42",
        event_type: str = "agent.progress",
        data: dict | None = None,
        timestamp: str = "2026-01-01T00:00:00+00:00",
        source: str = "webhook-server",
    ) -> SSEEvent:
        return SSEEvent(
            id=event_id,
            event=event_type,
            data=data or {},
            timestamp=timestamp,
            source=source,
        )

    def test_output_ends_with_newline(self):
        raw = self._make_event().to_sse_format()
        assert raw.endswith("\n")

    def test_output_ends_with_double_newline(self):
        # SSE spec requires blank line (double-newline) after each event
        raw = self._make_event().to_sse_format()
        assert raw.endswith("\n\n")

    def test_id_line_first(self):
        raw = self._make_event(event_id="99").to_sse_format()
        assert raw.split("\n")[0] == "id: 99"

    def test_event_line_second(self):
        raw = self._make_event(event_type="approval.created").to_sse_format()
        assert raw.split("\n")[1] == "event: approval.created"

    def test_data_line_third(self):
        raw = self._make_event().to_sse_format()
        assert raw.split("\n")[2].startswith("data: ")

    def test_blank_line_terminates_event(self):
        lines = self._make_event().to_sse_format().split("\n")
        assert lines[3] == ""

    def test_data_json_has_id_field(self):
        raw = self._make_event(event_id="77").to_sse_format()
        payload = self._extract_payload(raw)
        assert payload["id"] == "77"

    def test_data_json_uses_type_not_event(self):
        """Frontend reads 'type', not 'event', from the JSON payload."""
        raw = self._make_event(event_type="agent.completed").to_sse_format()
        payload = self._extract_payload(raw)
        assert "type" in payload
        assert payload["type"] == "agent.completed"
        assert "event" not in payload

    def test_data_json_has_timestamp(self):
        ts = "2026-06-01T09:00:00+00:00"
        raw = self._make_event(timestamp=ts).to_sse_format()
        payload = self._extract_payload(raw)
        assert payload["timestamp"] == ts

    def test_data_json_has_source(self):
        raw = self._make_event(source="custom-svc").to_sse_format()
        payload = self._extract_payload(raw)
        assert payload["source"] == "custom-svc"

    def test_data_json_data_field_matches_original(self):
        inner = {"progress": 75, "project": "voice-coach"}
        raw = self._make_event(data=inner).to_sse_format()
        payload = self._extract_payload(raw)
        assert payload["data"] == inner

    def test_nested_data_json_serialised_correctly(self):
        inner = {"nested": {"list": [1, 2, 3], "flag": True}}
        raw = self._make_event(data=inner).to_sse_format()
        payload = self._extract_payload(raw)
        assert payload["data"] == inner

    def test_all_required_payload_keys_present(self):
        raw = self._make_event().to_sse_format()
        payload = self._extract_payload(raw)
        for key in ("id", "type", "timestamp", "source", "data"):
            assert key in payload, f"Missing key: {key}"

    def test_full_format_structure_known_values(self):
        evt = SSEEvent(
            id="10",
            event="approval.resolved",
            data={"approved": True},
            timestamp="2026-02-14T00:00:00+00:00",
            source="test",
        )
        raw = evt.to_sse_format()
        assert raw.startswith("id: 10\n")
        assert "event: approval.resolved\n" in raw

    def _extract_payload(self, raw: str) -> dict:
        data_line = next(line for line in raw.split("\n") if line.startswith("data: "))
        return json.loads(data_line[len("data: "):])


# ===========================================================================
# EventBus — singleton and initialisation
# ===========================================================================


class TestEventBusSingleton:
    def test_multiple_constructions_return_same_object(self):
        b1 = EventBus()
        b2 = EventBus()
        assert b1 is b2

    def test_second_init_does_not_reset_state(self):
        bus = EventBus()
        bus._event_counter = 42
        _ = EventBus()
        assert bus._event_counter == 42

    def test_initialized_flag_set_after_first_creation(self):
        bus = EventBus()
        assert bus._initialized is True

    def test_starts_with_empty_subscribers_list(self):
        bus = EventBus()
        assert bus._subscribers == []

    def test_starts_with_zero_event_counter(self):
        bus = EventBus()
        assert bus._event_counter == 0

    def test_class_lock_exists(self):
        import threading
        # threading.Lock() returns a _thread.lock instance; verify it has acquire/release
        lock = EventBus._lock
        assert hasattr(lock, "acquire") and hasattr(lock, "release")

    def test_init_logs_info_once(self):
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_log:
            EventBus()
        mock_log.info.assert_called_once()

    def test_second_construction_does_not_re_log_init(self):
        EventBus()
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_log:
            EventBus()  # second call, _initialized guard fires
        mock_log.info.assert_not_called()


# ===========================================================================
# EventBus — subscribe
# ===========================================================================


class TestEventBusSubscribe:
    def test_returns_asyncio_queue(self):
        bus = EventBus()
        q = bus.subscribe()
        assert isinstance(q, asyncio.Queue)

    def test_returned_queue_maxsize_is_100(self):
        bus = EventBus()
        q = bus.subscribe()
        assert q.maxsize == 100

    def test_queue_added_to_subscribers_list(self):
        bus = EventBus()
        q = bus.subscribe()
        assert q in bus._subscribers

    def test_multiple_subscribes_each_unique_queue(self):
        bus = EventBus()
        queues = [bus.subscribe() for _ in range(5)]
        assert len(set(id(q) for q in queues)) == 5

    def test_subscriber_count_increments(self):
        bus = EventBus()
        for i in range(1, 4):
            bus.subscribe()
            assert len(bus._subscribers) == i

    def test_subscribe_logs_debug(self):
        bus = EventBus()
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_log:
            bus.subscribe()
        mock_log.debug.assert_called()


# ===========================================================================
# EventBus — unsubscribe
# ===========================================================================


class TestEventBusUnsubscribe:
    def test_removes_known_queue(self):
        bus = EventBus()
        q = bus.subscribe()
        assert q in bus._subscribers
        bus.unsubscribe(q)
        assert q not in bus._subscribers

    def test_unknown_queue_does_not_raise(self):
        bus = EventBus()
        phantom: asyncio.Queue = asyncio.Queue()
        bus.unsubscribe(phantom)  # Should not raise

    def test_unsubscribe_does_not_remove_other_queues(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.unsubscribe(q1)
        assert q2 in bus._subscribers

    def test_subscriber_count_decrements(self):
        bus = EventBus()
        q = bus.subscribe()
        assert len(bus._subscribers) == 1
        bus.unsubscribe(q)
        assert len(bus._subscribers) == 0

    def test_unsubscribe_known_queue_logs_debug(self):
        bus = EventBus()
        q = bus.subscribe()
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_log:
            bus.unsubscribe(q)
        mock_log.debug.assert_called()

    def test_unsubscribe_unknown_queue_logs_debug(self):
        bus = EventBus()
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_log:
            bus.unsubscribe(asyncio.Queue())
        mock_log.debug.assert_called()


# ===========================================================================
# EventBus — publish (async)
# ===========================================================================


class TestEventBusPublish:
    @pytest.mark.asyncio
    async def test_increments_event_counter(self):
        bus = EventBus()
        assert bus._event_counter == 0
        await bus.publish("ping", {})
        assert bus._event_counter == 1
        await bus.publish("pong", {})
        assert bus._event_counter == 2

    @pytest.mark.asyncio
    async def test_delivers_event_to_subscriber_queue(self):
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("agent.registered", {"id": "x"})
        assert not q.empty()
        evt = q.get_nowait()
        assert isinstance(evt, SSEEvent)
        assert evt.event == "agent.registered"
        assert evt.data == {"id": "x"}

    @pytest.mark.asyncio
    async def test_delivers_to_all_subscribers(self):
        bus = EventBus()
        queues = [bus.subscribe() for _ in range(3)]
        await bus.publish("broadcast", {"msg": "hello"})
        for q in queues:
            assert not q.empty()
            evt = q.get_nowait()
            assert evt.event == "broadcast"

    @pytest.mark.asyncio
    async def test_event_id_matches_counter_string(self):
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("test", {})
        evt = q.get_nowait()
        assert evt.id == "1"

    @pytest.mark.asyncio
    async def test_sequential_event_ids(self):
        bus = EventBus()
        q = bus.subscribe()
        for _ in range(5):
            await bus.publish("seq", {})
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        assert [e.id for e in events] == ["1", "2", "3", "4", "5"]

    @pytest.mark.asyncio
    async def test_custom_source_forwarded(self):
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("test", {}, source="my-svc")
        evt = q.get_nowait()
        assert evt.source == "my-svc"

    @pytest.mark.asyncio
    async def test_default_source_is_webhook_server(self):
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("test", {})
        evt = q.get_nowait()
        assert evt.source == "webhook-server"

    @pytest.mark.asyncio
    async def test_no_subscribers_completes_without_error(self):
        bus = EventBus()
        await bus.publish("orphan", {"key": "val"})
        assert bus._event_counter == 1

    @pytest.mark.asyncio
    async def test_full_queue_drops_event_no_raise(self):
        bus = EventBus()
        q = bus.subscribe()
        # Fill queue to capacity
        for i in range(100):
            q.put_nowait(SSEEvent(id=str(i), event="fill", data={}))
        # Should not raise; event is silently dropped
        await bus.publish("overflow", {"overflow": True})
        assert bus._event_counter == 1
        assert q.qsize() == 100  # unchanged

    @pytest.mark.asyncio
    async def test_full_queue_logs_warning(self):
        bus = EventBus()
        q = bus.subscribe()
        for i in range(100):
            q.put_nowait(SSEEvent(id=str(i), event="fill", data={}))
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_log:
            await bus.publish("warn-me", {})
        mock_log.warning.assert_called()

    @pytest.mark.asyncio
    async def test_publishes_info_log_with_subscribers(self):
        bus = EventBus()
        bus.subscribe()
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_log:
            await bus.publish("agent.progress", {"p": 10})
        mock_log.info.assert_called()

    @pytest.mark.asyncio
    async def test_publishes_debug_log_without_subscribers(self):
        bus = EventBus()
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_log:
            await bus.publish("no-sub", {})
        mock_log.debug.assert_called()

    @pytest.mark.asyncio
    async def test_event_data_preserved_exactly(self):
        bus = EventBus()
        q = bus.subscribe()
        inner = {"nested": {"list": [1, 2, 3]}, "flag": True}
        await bus.publish("complex", inner)
        evt = q.get_nowait()
        assert evt.data == inner

    @pytest.mark.asyncio
    async def test_multiple_event_types_delivered_correctly(self):
        bus = EventBus()
        q = bus.subscribe()
        events_to_send = [
            ("agent.registered", {"agent": "a1"}),
            ("agent.progress", {"progress": 50}),
            ("approval.created", {"id": "req-1"}),
            ("agent.completed", {"result": "ok"}),
        ]
        for evt_type, data in events_to_send:
            await bus.publish(evt_type, data)

        received = []
        while not q.empty():
            received.append(q.get_nowait())

        assert len(received) == 4
        for i, (evt_type, data) in enumerate(events_to_send):
            assert received[i].event == evt_type
            assert received[i].data == data

    @pytest.mark.asyncio
    async def test_unsubscribed_queue_does_not_receive_events(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.unsubscribe(q1)
        await bus.publish("targeted", {"msg": "for q2 only"})
        assert q1.empty()
        assert not q2.empty()


# ===========================================================================
# EventBus — close_all (async)
# ===========================================================================


class TestEventBusCloseAll:
    @pytest.mark.asyncio
    async def test_sends_none_to_all_subscribers(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        await bus.close_all()
        assert q1.get_nowait() is None
        assert q2.get_nowait() is None

    @pytest.mark.asyncio
    async def test_no_subscribers_no_error(self):
        bus = EventBus()
        await bus.close_all()  # Should not raise

    @pytest.mark.asyncio
    async def test_full_queue_does_not_raise(self):
        bus = EventBus()
        q = bus.subscribe()
        for i in range(100):
            q.put_nowait(SSEEvent(id=str(i), event="fill", data={}))
        # close_all should silently handle QueueFull
        await bus.close_all()
        assert q.qsize() == 100  # original items unchanged (None was dropped)

    @pytest.mark.asyncio
    async def test_subscribers_list_not_cleared_by_close_all(self):
        bus = EventBus()
        bus.subscribe()
        bus.subscribe()
        await bus.close_all()
        # close_all signals but does NOT remove queues
        assert len(bus._subscribers) == 2

    @pytest.mark.asyncio
    async def test_close_all_logs_info(self):
        bus = EventBus()
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_log:
            await bus.close_all()
        mock_log.info.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_all_after_publish_includes_none_signal(self):
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("pre-close", {"data": 1})
        await bus.close_all()

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        assert len(events) == 2
        assert events[0].event == "pre-close"
        assert events[1] is None

    @pytest.mark.asyncio
    async def test_close_all_multiple_times_no_error(self):
        bus = EventBus()
        bus.subscribe()
        await bus.close_all()
        await bus.close_all()  # Should not raise


# ===========================================================================
# get_event_bus — module-level factory
# ===========================================================================


class TestGetEventBus:
    def test_returns_event_bus_instance(self):
        bus = get_event_bus()
        assert isinstance(bus, EventBus)

    def test_returns_same_instance_on_repeated_calls(self):
        b1 = get_event_bus()
        b2 = get_event_bus()
        assert b1 is b2

    def test_creates_instance_when_global_none(self):
        import forge_harness.webhook_server.services.event_bus as mod

        mod._event_bus = None
        bus = get_event_bus()
        assert bus is not None

    def test_reuses_existing_global(self):
        first = get_event_bus()
        first._event_counter = 77
        second = get_event_bus()
        assert second._event_counter == 77

    def test_factory_and_direct_constructor_share_singleton(self):
        direct = EventBus()
        via_factory = get_event_bus()
        assert direct is via_factory

    def test_global_variable_set_after_first_call(self):
        import forge_harness.webhook_server.services.event_bus as mod

        assert mod._event_bus is None
        get_event_bus()
        assert mod._event_bus is not None


# ===========================================================================
# Concurrent subscribe / publish / unsubscribe correctness
# ===========================================================================


class TestEventBusConcurrency:
    """Thread-safety smoke tests for subscribe/unsubscribe under concurrent load."""

    def test_concurrent_subscribe_no_data_race(self):
        """Many threads subscribing simultaneously produce distinct queues."""
        bus = EventBus()
        queues: list[asyncio.Queue] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker():
            try:
                q = bus.subscribe()
                with lock:
                    queues.append(q)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(queues) == 20
        assert len(set(id(q) for q in queues)) == 20

    def test_concurrent_subscribe_unsubscribe_no_exception(self):
        bus = EventBus()
        errors: list[Exception] = []

        def sub_then_unsub():
            try:
                q = bus.subscribe()
                bus.unsubscribe(q)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=sub_then_unsub) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors

    @pytest.mark.asyncio
    async def test_publish_while_unsubscribing_no_exception(self):
        """Concurrent publish and unsubscribe should not raise."""
        bus = EventBus()
        queues = [bus.subscribe() for _ in range(10)]
        errors: list[Exception] = []

        async def publisher():
            for _ in range(20):
                try:
                    await bus.publish("concurrent", {})
                except Exception as exc:
                    errors.append(exc)

        def unsubscriber():
            for q in queues[:5]:
                try:
                    bus.unsubscribe(q)
                except Exception as exc:
                    errors.append(exc)

        t = threading.Thread(target=unsubscriber)
        t.start()
        await publisher()
        t.join()

        assert not errors


# ===========================================================================
# SSEEvent — edge cases
# ===========================================================================


class TestSSEEventEdgeCases:
    def test_special_characters_in_event_type(self):
        evt = SSEEvent(id="x", event="my.custom-event_type", data={})
        raw = evt.to_sse_format()
        assert "event: my.custom-event_type" in raw

    def test_large_data_payload_serialised(self):
        large_data = {f"key_{i}": f"value_{i}" for i in range(100)}
        evt = SSEEvent(id="big", event="bulk", data=large_data)
        raw = evt.to_sse_format()
        payload = json.loads(raw.split("\n")[2][len("data: "):])
        assert len(payload["data"]) == 100

    def test_boolean_values_in_data(self):
        evt = SSEEvent(id="b", event="bool-test", data={"flag": True, "off": False})
        payload = json.loads(evt.to_sse_format().split("\n")[2][len("data: "):])
        assert payload["data"]["flag"] is True
        assert payload["data"]["off"] is False

    def test_null_value_in_data(self):
        evt = SSEEvent(id="n", event="null-test", data={"key": None})
        payload = json.loads(evt.to_sse_format().split("\n")[2][len("data: "):])
        assert payload["data"]["key"] is None

    def test_numeric_data_values(self):
        evt = SSEEvent(id="num", event="numbers", data={"int": 42, "float": 3.14})
        payload = json.loads(evt.to_sse_format().split("\n")[2][len("data: "):])
        assert payload["data"]["int"] == 42
        assert abs(payload["data"]["float"] - 3.14) < 1e-9


# ===========================================================================
# EventBus — known event type constants documentation test
# ===========================================================================


class TestEventBusKnownEventTypes:
    """Smoke tests ensuring the event types documented in the class docstring
    are publishable end-to-end without errors."""

    KNOWN_EVENT_TYPES = [
        "agent.registered",
        "agent.progress",
        "agent.completed",
        "approval.created",
        "approval.resolved",
    ]

    @pytest.mark.asyncio
    async def test_all_known_event_types_publishable(self):
        bus = EventBus()
        q = bus.subscribe()
        for evt_type in self.KNOWN_EVENT_TYPES:
            await bus.publish(evt_type, {"type": evt_type})

        received = []
        while not q.empty():
            received.append(q.get_nowait())

        received_types = {e.event for e in received}
        assert received_types == set(self.KNOWN_EVENT_TYPES)

    @pytest.mark.asyncio
    async def test_counter_reflects_total_publishes(self):
        bus = EventBus()
        for evt_type in self.KNOWN_EVENT_TYPES:
            await bus.publish(evt_type, {})
        assert bus._event_counter == len(self.KNOWN_EVENT_TYPES)
