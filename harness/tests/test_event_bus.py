"""
Tests for forge_harness.webhook_server.services.event_bus

Tests all classes and methods:
- SSEEvent: to_sse_format
- EventBus: subscribe, unsubscribe, publish, get_subscriber_count, event history
"""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from forge_harness.webhook_server.services.event_bus import (
    EventBus,
    SSEEvent,
    get_event_bus,
)


class TestSSEEvent:
    """Tests for SSEEvent class."""

    def test_sseevent_creation_defaults(self):
        """Test SSEEvent creation with default values."""
        event = SSEEvent(
            id="123",
            event="test.event",
            data={"key": "value"},
        )

        assert event.id == "123"
        assert event.event == "test.event"
        assert event.data == {"key": "value"}
        assert event.source == "webhook-server"
        assert isinstance(event.timestamp, str)
        # Verify timestamp is ISO 8601 format
        datetime.fromisoformat(event.timestamp)

    def test_sseevent_creation_custom_source(self):
        """Test SSEEvent creation with custom source."""
        event = SSEEvent(
            id="456",
            event="custom.event",
            data={"foo": "bar"},
            source="custom-service",
        )

        assert event.source == "custom-service"

    def test_sseevent_creation_custom_timestamp(self):
        """Test SSEEvent creation with custom timestamp."""
        custom_time = "2026-02-16T10:30:00Z"
        event = SSEEvent(
            id="789",
            event="time.event",
            data={},
            timestamp=custom_time,
        )

        assert event.timestamp == custom_time

    def test_to_sse_format_structure(self):
        """Test SSE format structure."""
        event = SSEEvent(
            id="test-001",
            event="agent.registered",
            data={"agent_id": "kimi", "status": "active"},
            timestamp="2026-02-16T12:00:00Z",
            source="test-server",
        )

        sse_output = event.to_sse_format()

        # SSE format should have id, event, data lines followed by blank line
        lines = sse_output.split("\n")
        assert lines[0] == "id: test-001"
        assert lines[1] == "event: agent.registered"
        assert lines[2].startswith("data: ")
        assert lines[3] == ""  # Blank line terminator
        assert lines[4] == ""  # Final newline

    def test_to_sse_format_data_payload(self):
        """Test SSE format data payload structure."""
        event = SSEEvent(
            id="test-002",
            event="approval.created",
            data={"request_id": "abc123", "priority": "high"},
            timestamp="2026-02-16T12:00:00Z",
            source="webhook-server",
        )

        sse_output = event.to_sse_format()

        # Extract data line
        data_line = [line for line in sse_output.split("\n") if line.startswith("data: ")][0]
        data_json = data_line[6:]  # Remove "data: " prefix
        payload = json.loads(data_json)

        # Verify payload structure
        assert payload["id"] == "test-002"
        assert payload["type"] == "approval.created"  # Frontend uses 'type'
        assert payload["timestamp"] == "2026-02-16T12:00:00Z"
        assert payload["source"] == "webhook-server"
        assert payload["data"] == {"request_id": "abc123", "priority": "high"}

    def test_to_sse_format_complex_data(self):
        """Test SSE format with complex nested data."""
        complex_data = {
            "nested": {"key": "value"},
            "list": [1, 2, 3],
            "bool": True,
            "null": None,
        }

        event = SSEEvent(
            id="test-003",
            event="test.complex",
            data=complex_data,
        )

        sse_output = event.to_sse_format()
        data_line = [line for line in sse_output.split("\n") if line.startswith("data: ")][0]
        data_json = data_line[6:]
        payload = json.loads(data_json)

        assert payload["data"] == complex_data

    def test_to_sse_format_empty_data(self):
        """Test SSE format with empty data."""
        event = SSEEvent(
            id="test-004",
            event="test.empty",
            data={},
        )

        sse_output = event.to_sse_format()
        data_line = [line for line in sse_output.split("\n") if line.startswith("data: ")][0]
        data_json = data_line[6:]
        payload = json.loads(data_json)

        assert payload["data"] == {}


class TestEventBus:
    """Tests for EventBus class."""

    @pytest.fixture
    def event_bus(self):
        """Create a fresh EventBus instance for each test."""
        # Clear singleton
        EventBus._instance = None
        bus = EventBus()
        yield bus
        # Cleanup
        EventBus._instance = None

    def test_event_bus_singleton(self):
        """Test EventBus singleton pattern."""
        EventBus._instance = None

        bus1 = EventBus()
        bus2 = EventBus()

        assert bus1 is bus2

        # Cleanup
        EventBus._instance = None

    def test_event_bus_initialization(self, event_bus):
        """Test EventBus initialization."""
        assert event_bus._initialized is True
        assert isinstance(event_bus._subscribers, list)
        assert len(event_bus._subscribers) == 0
        assert event_bus._event_counter == 0

    def test_event_bus_double_init_safe(self):
        """Test that double initialization doesn't reset state."""
        EventBus._instance = None
        bus = EventBus()
        bus._event_counter = 42

        # Call __init__ again
        bus.__init__()

        # Counter should not be reset
        assert bus._event_counter == 42

        # Cleanup
        EventBus._instance = None

    def test_subscribe_returns_queue(self, event_bus):
        """Test subscribe returns asyncio.Queue."""
        queue = event_bus.subscribe()

        assert isinstance(queue, asyncio.Queue)
        assert queue.maxsize == 100

    def test_subscribe_adds_subscriber(self, event_bus):
        """Test subscribe adds subscriber to list."""
        queue = event_bus.subscribe()

        assert len(event_bus._subscribers) == 1
        assert queue in event_bus._subscribers

    def test_subscribe_multiple_subscribers(self, event_bus):
        """Test multiple subscribers."""
        queue1 = event_bus.subscribe()
        queue2 = event_bus.subscribe()
        queue3 = event_bus.subscribe()

        assert len(event_bus._subscribers) == 3
        assert queue1 in event_bus._subscribers
        assert queue2 in event_bus._subscribers
        assert queue3 in event_bus._subscribers

    def test_unsubscribe_removes_subscriber(self, event_bus):
        """Test unsubscribe removes subscriber."""
        queue = event_bus.subscribe()
        assert len(event_bus._subscribers) == 1

        event_bus.unsubscribe(queue)

        assert len(event_bus._subscribers) == 0
        assert queue not in event_bus._subscribers

    def test_unsubscribe_nonexistent_queue(self, event_bus):
        """Test unsubscribe with queue not in list (should not error)."""
        queue = asyncio.Queue()

        # Should not raise error
        event_bus.unsubscribe(queue)

        assert len(event_bus._subscribers) == 0

    def test_unsubscribe_partial_removal(self, event_bus):
        """Test unsubscribe removes only specified queue."""
        queue1 = event_bus.subscribe()
        queue2 = event_bus.subscribe()
        queue3 = event_bus.subscribe()

        event_bus.unsubscribe(queue2)

        assert len(event_bus._subscribers) == 2
        assert queue1 in event_bus._subscribers
        assert queue2 not in event_bus._subscribers
        assert queue3 in event_bus._subscribers

    @pytest.mark.asyncio
    async def test_publish_increments_counter(self, event_bus):
        """Test publish increments event counter."""
        assert event_bus._event_counter == 0

        await event_bus.publish("test.event", {"key": "value"})
        assert event_bus._event_counter == 1

        await event_bus.publish("test.event2", {"key": "value2"})
        assert event_bus._event_counter == 2

    @pytest.mark.asyncio
    async def test_publish_no_subscribers(self, event_bus):
        """Test publish with no subscribers (should not error)."""
        # Should not raise error
        await event_bus.publish("test.event", {"key": "value"})

        assert event_bus._event_counter == 1

    @pytest.mark.asyncio
    async def test_publish_to_single_subscriber(self, event_bus):
        """Test publish sends event to single subscriber."""
        queue = event_bus.subscribe()

        await event_bus.publish("test.event", {"foo": "bar"})

        # Get event from queue
        event = await asyncio.wait_for(queue.get(), timeout=1.0)

        assert isinstance(event, SSEEvent)
        assert event.id == "1"
        assert event.event == "test.event"
        assert event.data == {"foo": "bar"}
        assert event.source == "webhook-server"

    @pytest.mark.asyncio
    async def test_publish_to_multiple_subscribers(self, event_bus):
        """Test publish sends event to all subscribers."""
        queue1 = event_bus.subscribe()
        queue2 = event_bus.subscribe()
        queue3 = event_bus.subscribe()

        await event_bus.publish("test.event", {"key": "value"})

        # All queues should receive event
        event1 = await asyncio.wait_for(queue1.get(), timeout=1.0)
        event2 = await asyncio.wait_for(queue2.get(), timeout=1.0)
        event3 = await asyncio.wait_for(queue3.get(), timeout=1.0)

        assert event1.event == "test.event"
        assert event2.event == "test.event"
        assert event3.event == "test.event"

    @pytest.mark.asyncio
    async def test_publish_custom_source(self, event_bus):
        """Test publish with custom source."""
        queue = event_bus.subscribe()

        await event_bus.publish("test.event", {"key": "value"}, source="custom-source")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.source == "custom-source"

    @pytest.mark.asyncio
    async def test_publish_queue_full_drops_event(self, event_bus):
        """Test publish drops event if queue is full."""
        queue = event_bus.subscribe()

        # Fill queue to max (100 items)
        for i in range(100):
            await queue.put(SSEEvent(id=str(i), event="filler", data={}))

        # Publish should not block or error
        await event_bus.publish("test.event", {"key": "value"})

        # Queue should still have 100 items (event was dropped)
        assert queue.qsize() == 100

    @pytest.mark.asyncio
    async def test_publish_sequential_events(self, event_bus):
        """Test publish maintains event order and IDs."""
        queue = event_bus.subscribe()

        await event_bus.publish("event1", {"seq": 1})
        await event_bus.publish("event2", {"seq": 2})
        await event_bus.publish("event3", {"seq": 3})

        event1 = await asyncio.wait_for(queue.get(), timeout=1.0)
        event2 = await asyncio.wait_for(queue.get(), timeout=1.0)
        event3 = await asyncio.wait_for(queue.get(), timeout=1.0)

        assert event1.id == "1"
        assert event2.id == "2"
        assert event3.id == "3"
        assert event1.data["seq"] == 1
        assert event2.data["seq"] == 2
        assert event3.data["seq"] == 3

    @pytest.mark.asyncio
    async def test_close_all_sends_none(self, event_bus):
        """Test close_all sends None to all subscribers."""
        queue1 = event_bus.subscribe()
        queue2 = event_bus.subscribe()

        await event_bus.close_all()

        # Both queues should receive None
        msg1 = await asyncio.wait_for(queue1.get(), timeout=1.0)
        msg2 = await asyncio.wait_for(queue2.get(), timeout=1.0)

        assert msg1 is None
        assert msg2 is None

    @pytest.mark.asyncio
    async def test_close_all_no_subscribers(self, event_bus):
        """Test close_all with no subscribers (should not error)."""
        # Should not raise error
        await event_bus.close_all()

    @pytest.mark.asyncio
    async def test_close_all_full_queue(self, event_bus):
        """Test close_all with full queue (should not block)."""
        queue = event_bus.subscribe()

        # Fill queue
        for i in range(100):
            await queue.put(SSEEvent(id=str(i), event="filler", data={}))

        # Should not block or error
        await event_bus.close_all()

    @pytest.mark.asyncio
    async def test_event_types_agent_registered(self, event_bus):
        """Test agent.registered event type."""
        queue = event_bus.subscribe()

        await event_bus.publish(
            "agent.registered",
            {"agent_id": "kimi", "status": "active"},
        )

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.event == "agent.registered"
        assert event.data["agent_id"] == "kimi"

    @pytest.mark.asyncio
    async def test_event_types_agent_progress(self, event_bus):
        """Test agent.progress event type."""
        queue = event_bus.subscribe()

        await event_bus.publish(
            "agent.progress",
            {"agent_id": "kimi", "progress": 50},
        )

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.event == "agent.progress"
        assert event.data["progress"] == 50

    @pytest.mark.asyncio
    async def test_event_types_agent_completed(self, event_bus):
        """Test agent.completed event type."""
        queue = event_bus.subscribe()

        await event_bus.publish(
            "agent.completed",
            {"agent_id": "kimi", "result": "success"},
        )

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.event == "agent.completed"
        assert event.data["result"] == "success"

    @pytest.mark.asyncio
    async def test_event_types_approval_created(self, event_bus):
        """Test approval.created event type."""
        queue = event_bus.subscribe()

        await event_bus.publish(
            "approval.created",
            {"request_id": "abc123", "priority": "high"},
        )

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.event == "approval.created"
        assert event.data["request_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_event_types_approval_resolved(self, event_bus):
        """Test approval.resolved event type."""
        queue = event_bus.subscribe()

        await event_bus.publish(
            "approval.resolved",
            {"request_id": "abc123", "status": "approved"},
        )

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.event == "approval.resolved"
        assert event.data["status"] == "approved"


class TestGetEventBus:
    """Tests for get_event_bus() factory function."""

    def test_get_event_bus_creates_instance(self):
        """Test get_event_bus creates EventBus instance."""
        from forge_harness.webhook_server.services import event_bus as event_bus_module

        # Clear global
        event_bus_module._event_bus = None

        bus = get_event_bus()

        assert isinstance(bus, EventBus)
        assert bus is event_bus_module._event_bus

        # Cleanup
        event_bus_module._event_bus = None
        EventBus._instance = None

    def test_get_event_bus_returns_existing(self):
        """Test get_event_bus returns existing instance."""
        from forge_harness.webhook_server.services import event_bus as event_bus_module

        # Clear global
        event_bus_module._event_bus = None

        bus1 = get_event_bus()
        bus2 = get_event_bus()

        assert bus1 is bus2

        # Cleanup
        event_bus_module._event_bus = None
        EventBus._instance = None


class TestEventBusThreadSafety:
    """Tests for EventBus thread safety."""

    @pytest.fixture
    def event_bus(self):
        """Create a fresh EventBus instance for each test."""
        EventBus._instance = None
        bus = EventBus()
        yield bus
        EventBus._instance = None

    @pytest.mark.asyncio
    async def test_concurrent_subscribes(self, event_bus):
        """Test concurrent subscribe calls are thread-safe."""
        async def subscribe_task():
            return event_bus.subscribe()

        # Create 10 concurrent subscriptions
        tasks = [subscribe_task() for _ in range(10)]
        queues = await asyncio.gather(*tasks)

        assert len(event_bus._subscribers) == 10
        assert len(set(id(q) for q in queues)) == 10  # All unique

    @pytest.mark.asyncio
    async def test_concurrent_publishes(self, event_bus):
        """Test concurrent publish calls work correctly."""
        queue = event_bus.subscribe()

        async def publish_task(i):
            await event_bus.publish(f"event{i}", {"index": i})

        # Publish 10 events concurrently
        tasks = [publish_task(i) for i in range(10)]
        await asyncio.gather(*tasks)

        # All events should be received
        events = []
        for _ in range(10):
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
            events.append(event)

        assert len(events) == 10
        assert event_bus._event_counter == 10

    @pytest.mark.asyncio
    async def test_concurrent_subscribe_unsubscribe(self, event_bus):
        """Test concurrent subscribe/unsubscribe operations."""
        queues = [event_bus.subscribe() for _ in range(5)]

        async def unsub_task(q):
            await asyncio.sleep(0.01)  # Small delay
            event_bus.unsubscribe(q)

        # Unsubscribe concurrently
        tasks = [unsub_task(q) for q in queues]
        await asyncio.gather(*tasks)

        assert len(event_bus._subscribers) == 0


class TestEventBusIntegration:
    """Integration tests for EventBus with realistic scenarios."""

    @pytest.fixture
    def event_bus(self):
        """Create a fresh EventBus instance for each test."""
        EventBus._instance = None
        bus = EventBus()
        yield bus
        EventBus._instance = None

    @pytest.mark.asyncio
    async def test_agent_workflow(self, event_bus):
        """Test complete agent workflow with events."""
        queue = event_bus.subscribe()

        # Agent registers
        await event_bus.publish("agent.registered", {"agent_id": "kimi", "status": "active"})

        # Agent reports progress
        await event_bus.publish("agent.progress", {"agent_id": "kimi", "progress": 25})
        await event_bus.publish("agent.progress", {"agent_id": "kimi", "progress": 50})
        await event_bus.publish("agent.progress", {"agent_id": "kimi", "progress": 75})

        # Agent completes
        await event_bus.publish("agent.completed", {"agent_id": "kimi", "result": "success"})

        # Verify events received in order
        event1 = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event1.event == "agent.registered"

        event2 = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event2.event == "agent.progress"
        assert event2.data["progress"] == 25

        event3 = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event3.data["progress"] == 50

        event4 = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event4.data["progress"] == 75

        event5 = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event5.event == "agent.completed"

    @pytest.mark.asyncio
    async def test_approval_workflow(self, event_bus):
        """Test complete approval workflow with events."""
        queue = event_bus.subscribe()

        # Approval created
        await event_bus.publish(
            "approval.created",
            {"request_id": "req-001", "type": "deployment", "priority": "high"},
        )

        # Approval resolved
        await event_bus.publish(
            "approval.resolved",
            {"request_id": "req-001", "status": "approved", "approver": "user@example.com"},
        )

        # Verify events
        event1 = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event1.event == "approval.created"
        assert event1.data["request_id"] == "req-001"

        event2 = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event2.event == "approval.resolved"
        assert event2.data["status"] == "approved"

    @pytest.mark.asyncio
    async def test_multiple_clients_subscribe_mid_stream(self, event_bus):
        """Test client subscribing mid-stream doesn't receive old events."""
        queue1 = event_bus.subscribe()

        # Publish event to first subscriber
        await event_bus.publish("event1", {"num": 1})

        # Second subscriber joins
        queue2 = event_bus.subscribe()

        # Publish event to both
        await event_bus.publish("event2", {"num": 2})

        # First subscriber should get both events
        event1a = await asyncio.wait_for(queue1.get(), timeout=1.0)
        event1b = await asyncio.wait_for(queue1.get(), timeout=1.0)
        assert event1a.data["num"] == 1
        assert event1b.data["num"] == 2

        # Second subscriber should only get event2
        event2a = await asyncio.wait_for(queue2.get(), timeout=1.0)
        assert event2a.data["num"] == 2

        # queue2 should be empty
        assert queue2.empty()

    @pytest.mark.asyncio
    async def test_sse_format_integration(self, event_bus):
        """Test SSE format output matches expected protocol."""
        queue = event_bus.subscribe()

        await event_bus.publish(
            "agent.registered",
            {"agent_id": "kimi", "domain": "voice-coach"},
        )

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        sse_output = event.to_sse_format()

        # Parse SSE output
        lines = sse_output.strip().split("\n")
        assert lines[0].startswith("id: ")
        assert lines[1].startswith("event: ")
        assert lines[2].startswith("data: ")

        # Parse data JSON
        data_json = lines[2][6:]  # Remove "data: " prefix
        payload = json.loads(data_json)

        assert payload["type"] == "agent.registered"
        assert payload["data"]["agent_id"] == "kimi"
        assert "timestamp" in payload
        assert "source" in payload
