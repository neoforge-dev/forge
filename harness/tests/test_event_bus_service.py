"""Tests for EventBus service (SSE).

Targets 100% statement coverage of:
    forge_harness/webhook_server/services/event_bus.py
"""

import asyncio
import json
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers – reset the singleton between tests so each test starts clean
# ---------------------------------------------------------------------------


def _reset_event_bus_singleton():
    """Reset EventBus singleton state so each test is isolated."""
    import forge_harness.webhook_server.services.event_bus as eb_module

    # Reset module-level global
    eb_module._event_bus = None

    # Reset class-level singleton
    from forge_harness.webhook_server.services.event_bus import EventBus

    EventBus._instance = None


@pytest.fixture(autouse=True)
def reset_singleton():
    """Auto-reset the EventBus singleton before and after every test."""
    _reset_event_bus_singleton()
    yield
    _reset_event_bus_singleton()


# ---------------------------------------------------------------------------
# Imports (done after path is configured via conftest.py)
# ---------------------------------------------------------------------------

from forge_harness.webhook_server.services.event_bus import (  # noqa: E402
    EventBus,
    SSEEvent,
    get_event_bus,
)

# ===========================================================================
# SSEEvent tests
# ===========================================================================


class TestSSEEvent:
    """Tests for the SSEEvent dataclass and its to_sse_format method."""

    def test_create_with_required_fields(self):
        """SSEEvent can be constructed with id, event, and data."""
        evt = SSEEvent(id="1", event="agent.registered", data={"agent": "x"})
        assert evt.id == "1"
        assert evt.event == "agent.registered"
        assert evt.data == {"agent": "x"}
        assert evt.source == "webhook-server"  # default
        # timestamp should be a non-empty ISO string
        assert isinstance(evt.timestamp, str)
        assert len(evt.timestamp) > 0

    def test_create_with_custom_source(self):
        """SSEEvent accepts a custom source value."""
        evt = SSEEvent(id="2", event="approval.created", data={}, source="my-service")
        assert evt.source == "my-service"

    def test_to_sse_format_structure(self):
        """to_sse_format returns correctly structured SSE text."""
        evt = SSEEvent(
            id="42",
            event="agent.progress",
            data={"progress": 75},
            timestamp="2026-01-01T00:00:00+00:00",
            source="webhook-server",
        )
        raw = evt.to_sse_format()

        # Must end with newline
        assert raw.endswith("\n")

        # Split without stripping so the blank terminator line is visible
        lines = raw.split("\n")
        assert lines[0] == "id: 42"
        assert lines[1] == "event: agent.progress"
        assert lines[2].startswith("data: ")
        # The blank line terminator sits at index 3; final \n produces "" at index 4
        assert lines[3] == ""

    def test_to_sse_format_data_json(self):
        """The data line contains valid JSON with all expected fields."""
        evt = SSEEvent(
            id="7",
            event="agent.completed",
            data={"result": "ok"},
            timestamp="2026-01-02T00:00:00+00:00",
            source="test-source",
        )
        raw = evt.to_sse_format()
        data_line = [line for line in raw.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: ") :])

        assert payload["id"] == "7"
        assert payload["type"] == "agent.completed"  # frontend uses 'type' not 'event'
        assert payload["timestamp"] == "2026-01-02T00:00:00+00:00"
        assert payload["source"] == "test-source"
        assert payload["data"] == {"result": "ok"}

    def test_timestamp_auto_generated(self):
        """Event created without explicit timestamp has a valid ISO timestamp."""
        evt = SSEEvent(id="1", event="x", data={})
        # Just verify it looks like an ISO timestamp (contains T or Z)
        assert any(c in evt.timestamp for c in ("T", "Z", "+"))

    def test_to_sse_format_empty_data(self):
        """to_sse_format handles empty data dict."""
        evt = SSEEvent(id="0", event="ping", data={})
        raw = evt.to_sse_format()
        data_line = [line for line in raw.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: ") :])
        assert payload["data"] == {}

    def test_to_sse_format_nested_data(self):
        """to_sse_format serialises nested data structures correctly."""
        nested = {"a": {"b": [1, 2, 3]}, "c": True}
        evt = SSEEvent(id="9", event="test", data=nested)
        raw = evt.to_sse_format()
        data_line = [line for line in raw.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: ") :])
        assert payload["data"] == nested

    def test_to_sse_format_trailing_newline(self):
        """to_sse_format output ends with a newline as required by SSE spec."""
        evt = SSEEvent(id="3", event="approval.resolved", data={"approved": True})
        raw = evt.to_sse_format()
        assert raw[-1] == "\n"


# ===========================================================================
# EventBus – singleton and initialisation
# ===========================================================================


class TestEventBusSingleton:
    """Tests for the EventBus singleton pattern."""

    def test_singleton_returns_same_instance(self):
        """Multiple EventBus() calls return the identical object."""
        bus1 = EventBus()
        bus2 = EventBus()
        assert bus1 is bus2

    def test_init_only_runs_once(self):
        """__init__ guard prevents re-initialisation of the same instance."""
        bus1 = EventBus()
        # Mutate state
        bus1._event_counter = 99
        bus2 = EventBus()
        # Counter must NOT have been reset by a second __init__
        assert bus2._event_counter == 99

    def test_initialized_flag_set(self):
        """_initialized is True after the first construction."""
        bus = EventBus()
        assert bus._initialized is True

    def test_initial_state(self):
        """Fresh EventBus starts with empty subscribers and counter = 0."""
        bus = EventBus()
        assert bus._subscribers == []
        assert bus._event_counter == 0


# ===========================================================================
# EventBus – subscribe / unsubscribe
# ===========================================================================


class TestEventBusSubscribeUnsubscribe:
    """Tests for subscribe() and unsubscribe()."""

    def test_subscribe_returns_queue(self):
        """subscribe() returns an asyncio.Queue."""
        bus = EventBus()
        q = bus.subscribe()
        assert isinstance(q, asyncio.Queue)

    def test_subscribe_adds_to_subscribers(self):
        """subscribe() registers the queue in the internal list."""
        bus = EventBus()
        q = bus.subscribe()
        assert q in bus._subscribers

    def test_multiple_subscribes(self):
        """Each subscribe() call adds a distinct queue."""
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        assert len(bus._subscribers) == 2
        assert q1 is not q2

    def test_unsubscribe_removes_queue(self):
        """unsubscribe() removes the given queue from the list."""
        bus = EventBus()
        q = bus.subscribe()
        assert q in bus._subscribers
        bus.unsubscribe(q)
        assert q not in bus._subscribers

    def test_unsubscribe_unknown_queue_is_noop(self):
        """unsubscribe() with an unregistered queue does not raise."""
        bus = EventBus()
        phantom: asyncio.Queue = asyncio.Queue()
        # Should silently succeed
        bus.unsubscribe(phantom)

    def test_unsubscribe_does_not_affect_other_queues(self):
        """Removing one queue leaves others intact."""
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.unsubscribe(q1)
        assert q1 not in bus._subscribers
        assert q2 in bus._subscribers

    def test_subscribe_queue_maxsize(self):
        """subscribe() creates a queue with maxsize=100."""
        bus = EventBus()
        q = bus.subscribe()
        assert q.maxsize == 100


# ===========================================================================
# EventBus – publish
# ===========================================================================


class TestEventBusPublish:
    """Tests for the async publish() method."""

    @pytest.mark.asyncio
    async def test_publish_increments_counter(self):
        """publish() increments the internal event counter."""
        bus = EventBus()
        assert bus._event_counter == 0
        await bus.publish("agent.registered", {"agent": "a1"})
        assert bus._event_counter == 1
        await bus.publish("agent.progress", {"progress": 50})
        assert bus._event_counter == 2

    @pytest.mark.asyncio
    async def test_publish_delivers_to_subscriber(self):
        """publish() puts an SSEEvent onto each subscriber queue."""
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("agent.completed", {"result": "done"})
        assert not q.empty()
        event = q.get_nowait()
        assert isinstance(event, SSEEvent)
        assert event.event == "agent.completed"
        assert event.data == {"result": "done"}

    @pytest.mark.asyncio
    async def test_publish_delivers_to_multiple_subscribers(self):
        """publish() delivers to all registered subscribers."""
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        await bus.publish("approval.created", {"id": "req-1"})
        assert not q1.empty()
        assert not q2.empty()

    @pytest.mark.asyncio
    async def test_publish_event_id_matches_counter(self):
        """The SSEEvent id matches the string of the current counter value."""
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("ping", {})
        event = q.get_nowait()
        assert event.id == "1"

    @pytest.mark.asyncio
    async def test_publish_custom_source(self):
        """publish() forwards a custom source to the SSEEvent."""
        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("test.event", {}, source="custom-source")
        event = q.get_nowait()
        assert event.source == "custom-source"

    @pytest.mark.asyncio
    async def test_publish_no_subscribers_no_error(self):
        """publish() with no subscribers completes without error."""
        bus = EventBus()
        # Should not raise
        await bus.publish("orphan.event", {"key": "value"})
        assert bus._event_counter == 1

    @pytest.mark.asyncio
    async def test_publish_drops_event_when_queue_full(self):
        """publish() drops the event (no raise) when a queue is at capacity."""
        bus = EventBus()
        q = bus.subscribe()
        # Fill the queue to capacity (maxsize=100)
        for i in range(100):
            q.put_nowait(SSEEvent(id=str(i), event="fill", data={}))
        # Queue is now full; publish should swallow QueueFull silently
        await bus.publish("overflow.event", {"overflow": True})
        # Counter still incremented
        assert bus._event_counter == 1
        # Queue size unchanged at 100
        assert q.qsize() == 100

    @pytest.mark.asyncio
    async def test_publish_logs_info_with_subscribers(self):
        """publish() calls logger.info when there are subscribers."""
        bus = EventBus()
        bus.subscribe()
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_logger:
            await bus.publish("agent.progress", {"p": 10})
        mock_logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_publish_logs_debug_without_subscribers(self):
        """publish() calls logger.debug when no subscribers are connected."""
        bus = EventBus()
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_logger:
            await bus.publish("no.sub", {})
        mock_logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_publish_full_queue_logs_warning(self):
        """publish() logs a warning when a subscriber queue is full."""
        bus = EventBus()
        q = bus.subscribe()
        for i in range(100):
            q.put_nowait(SSEEvent(id=str(i), event="fill", data={}))
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_logger:
            await bus.publish("overflow", {})
        mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_publish_sequential_ids(self):
        """Sequential publishes produce incrementing event IDs."""
        bus = EventBus()
        q = bus.subscribe()
        for expected_id in range(1, 6):
            await bus.publish("seq", {})
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        assert [e.id for e in events] == ["1", "2", "3", "4", "5"]


# ===========================================================================
# EventBus – close_all
# ===========================================================================


class TestEventBusCloseAll:
    """Tests for the async close_all() method."""

    @pytest.mark.asyncio
    async def test_close_all_sends_none_to_subscribers(self):
        """close_all() puts None onto each subscriber queue as a close signal."""
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        await bus.close_all()
        assert q1.get_nowait() is None
        assert q2.get_nowait() is None

    @pytest.mark.asyncio
    async def test_close_all_no_subscribers_no_error(self):
        """close_all() with no subscribers completes without error."""
        bus = EventBus()
        # Should not raise
        await bus.close_all()

    @pytest.mark.asyncio
    async def test_close_all_full_queue_swallowed(self):
        """close_all() does not raise when a subscriber queue is full."""
        bus = EventBus()
        q = bus.subscribe()
        # Fill queue completely
        for i in range(100):
            q.put_nowait(SSEEvent(id=str(i), event="fill", data={}))
        # Should not raise; QueueFull is silently caught
        await bus.close_all()
        # Queue still at capacity with original items (None was dropped)
        assert q.qsize() == 100

    @pytest.mark.asyncio
    async def test_close_all_logs_info(self):
        """close_all() logs an info message after closing connections."""
        bus = EventBus()
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_logger:
            await bus.close_all()
        mock_logger.info.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_all_does_not_remove_subscribers(self):
        """close_all() signals close but does not remove queues from the list."""
        bus = EventBus()
        bus.subscribe()
        bus.subscribe()
        await bus.close_all()
        # Subscribers list is NOT cleared by close_all
        assert len(bus._subscribers) == 2


# ===========================================================================
# get_event_bus factory
# ===========================================================================


class TestGetEventBus:
    """Tests for the module-level get_event_bus() factory function."""

    def test_returns_event_bus_instance(self):
        """get_event_bus() returns an EventBus object."""
        bus = get_event_bus()
        assert isinstance(bus, EventBus)

    def test_returns_same_instance_on_repeated_calls(self):
        """get_event_bus() always returns the same global instance."""
        bus1 = get_event_bus()
        bus2 = get_event_bus()
        assert bus1 is bus2

    def test_creates_instance_when_global_is_none(self):
        """get_event_bus() creates a new instance when global is None."""
        import forge_harness.webhook_server.services.event_bus as eb_module

        eb_module._event_bus = None
        bus = get_event_bus()
        assert bus is not None
        assert isinstance(bus, EventBus)

    def test_reuses_existing_global_instance(self):
        """get_event_bus() reuses an already-created global instance."""
        import forge_harness.webhook_server.services.event_bus as eb_module

        first = get_event_bus()
        first._event_counter = 55  # sentinel value
        second = get_event_bus()
        assert second._event_counter == 55

    def test_get_event_bus_and_direct_construct_share_state(self):
        """get_event_bus() and EventBus() refer to the same underlying object."""
        direct = EventBus()
        via_factory = get_event_bus()
        # Both must be the same Python object (singleton + global)
        assert direct is via_factory


# ===========================================================================
# EventBus – logger calls on lifecycle operations
# ===========================================================================


class TestEventBusLogging:
    """Tests verifying logger is called during EventBus lifecycle operations."""

    def test_init_logs_info(self):
        """EventBus.__init__ logs 'EventBus initialized' on first creation."""
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_logger:
            EventBus()
        mock_logger.info.assert_called_once()

    def test_subscribe_logs_debug(self):
        """subscribe() emits a debug log with subscriber count."""
        bus = EventBus()
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_logger:
            bus.subscribe()
        mock_logger.debug.assert_called()

    def test_unsubscribe_known_queue_logs_debug(self):
        """unsubscribe() of a registered queue emits a debug log."""
        bus = EventBus()
        q = bus.subscribe()
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_logger:
            bus.unsubscribe(q)
        mock_logger.debug.assert_called()

    def test_unsubscribe_unknown_queue_logs_debug(self):
        """unsubscribe() of an unknown queue still emits a debug log."""
        bus = EventBus()
        with patch("forge_harness.webhook_server.services.event_bus.logger") as mock_logger:
            bus.unsubscribe(asyncio.Queue())
        mock_logger.debug.assert_called()
