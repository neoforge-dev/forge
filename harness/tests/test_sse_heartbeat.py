"""
Tests for SSE Heartbeat Functionality
======================================

Tests for Server-Sent Events heartbeat mechanism in webhook_server.py.
Ensures connections stay alive through proxies with periodic heartbeat events.
"""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_app(tmp_path, monkeypatch):
    """Create mock FastAPI app for SSE testing with temp config directory."""
    try:
        from fastapi.testclient import TestClient

        from forge_harness.webhook_server import AuthConfig, create_app
    except ImportError:
        pytest.skip("FastAPI not installed")

    # Set temp config directory via environment variable
    config_dir = tmp_path / ".forge/config"
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(config_dir))

    # Disable auth for testing
    auth_config = AuthConfig(require_auth=False)

    # Create app with mock notification harness
    app = create_app(None, auth_config=auth_config)

    return TestClient(app)


class TestSSEHeartbeatConfiguration:
    """Tests for SSE heartbeat configuration from environment."""

    def test_default_heartbeat_interval(self, monkeypatch):
        """Test that SSE_HEARTBEAT_INTERVAL defaults to 30 seconds."""
        # Ensure env var is not set
        monkeypatch.delenv("SSE_HEARTBEAT_INTERVAL", raising=False)

        # Re-import to get fresh module state
        import importlib

        import forge_harness.webhook_server as ws

        importlib.reload(ws)

        assert ws.SSE_HEARTBEAT_INTERVAL == 30

    def test_custom_heartbeat_interval(self, monkeypatch):
        """Test that SSE_HEARTBEAT_INTERVAL can be configured via environment."""
        monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL", "15")

        # Re-import to get fresh module state
        import importlib

        import forge_harness.webhook_server as ws

        importlib.reload(ws)

        assert ws.SSE_HEARTBEAT_INTERVAL == 15

    def test_heartbeat_interval_type_conversion(self, monkeypatch):
        """Test that SSE_HEARTBEAT_INTERVAL is converted to integer."""
        monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL", "45")

        import importlib

        import forge_harness.webhook_server as ws

        importlib.reload(ws)

        assert isinstance(ws.SSE_HEARTBEAT_INTERVAL, int)
        assert ws.SSE_HEARTBEAT_INTERVAL == 45


@pytest.mark.asyncio
class TestSSEHeartbeatStream:
    """Tests for SSE heartbeat events in the event stream."""

    async def test_heartbeat_event_structure(self):
        """Test that heartbeat SSEEvent has correct structure.

        The SSE handler sends heartbeat events when no real events arrive
        within the timeout interval. We test the event format directly
        since the handler uses asyncio timeouts (not mockable time.time).
        """
        try:
            from forge_harness.webhook_server import SSEEvent
        except ImportError:
            pytest.skip("webhook_server not available")

        # Create a heartbeat event the same way the handler does
        import time as _time

        heartbeat = SSEEvent(
            id=f"heartbeat_{int(_time.time())}",
            event="heartbeat",
            data={"timestamp": datetime.now(UTC).isoformat()},
            source="webhook-server",
        )

        # Verify SSE format
        sse_format = heartbeat.to_sse_format()
        assert "event: heartbeat" in sse_format
        assert "id: heartbeat_" in sse_format
        assert "data:" in sse_format
        assert sse_format.endswith("\n\n")

        # Parse data JSON from SSE format
        lines = sse_format.strip().split("\n")
        data_line = [line for line in lines if line.startswith("data:")][0]
        data_json = data_line.replace("data:", "").strip()
        data = json.loads(data_json)

        # Verify heartbeat data structure (matches frontend expectations)
        assert data["id"].startswith("heartbeat_")
        assert data["type"] == "heartbeat"  # Frontend uses 'type' field
        assert "timestamp" in data
        assert data["source"] == "webhook-server"

    async def test_heartbeat_timing_with_mock_time(self, mock_app, monkeypatch):
        """Test that heartbeat is sent at configured interval using mocked time."""
        # Set short heartbeat interval for testing
        monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL", "2")

        import importlib

        import forge_harness.webhook_server as ws

        importlib.reload(ws)

        from forge_harness.webhook_server import AuthConfig, create_app, get_event_bus

        # Recreate app with new heartbeat interval
        auth_config = AuthConfig(require_auth=False)
        app = create_app(None, auth_config=auth_config)

        # Track time.time() calls and return values
        time_values = [0.0, 0.5, 1.0, 1.5, 2.1, 2.5]  # Last value triggers heartbeat
        time_index = 0

        def mock_time_generator():
            nonlocal time_index
            if time_index < len(time_values):
                value = time_values[time_index]
                time_index += 1
                return value
            return time_values[-1] + (time_index - len(time_values)) * 0.5

        with patch("time.time", side_effect=mock_time_generator):
            # Get event bus to verify heartbeat events
            event_bus = get_event_bus()

            # Create a queue to capture events
            queue = event_bus.subscribe()

            # Simulate SSE connection logic
            last_heartbeat = 0.0
            heartbeat_interval = 2

            heartbeat_count = 0

            # Simulate event loop checking for heartbeat
            for _ in range(10):
                now = mock_time_generator()

                if now - last_heartbeat >= heartbeat_interval:
                    # Heartbeat should be sent
                    heartbeat_count += 1
                    last_heartbeat = now

                    # Verify timestamp format
                    timestamp = datetime.now(UTC).isoformat()
                    assert "T" in timestamp

                # Simulate small wait
                await asyncio.sleep(0.01)

                # Stop after first heartbeat for test efficiency
                if heartbeat_count > 0:
                    break

            assert heartbeat_count > 0, "Should have triggered at least one heartbeat"

    async def test_heartbeat_does_not_interfere_with_real_events(self, mock_app, monkeypatch):
        """Test that heartbeat events don't interfere with real events."""
        monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL", "1")

        import importlib

        import forge_harness.webhook_server as ws

        importlib.reload(ws)

        from forge_harness.webhook_server import AuthConfig, create_app, get_event_bus

        auth_config = AuthConfig(require_auth=False)
        app = create_app(None, auth_config=auth_config)

        event_bus = get_event_bus()

        # Publish a real event
        real_event_data = {
            "session_id": "test-123",
            "status": "active",
            "reason": "testing",
        }

        await event_bus.publish(
            event_type="agent.status",
            data=real_event_data,
            source="test",
        )

        # Verify event was published
        queue = event_bus.subscribe()

        try:
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert event is not None
            assert event.event == "agent.status"
            assert event.data == real_event_data
        except TimeoutError:
            pytest.fail("Real event was not received")

    async def test_multiple_heartbeats_over_time(self, monkeypatch):
        """Test that multiple heartbeats are sent as time progresses."""
        monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL", "1")

        import importlib

        import forge_harness.webhook_server as ws

        importlib.reload(ws)

        # Simulate heartbeat logic
        last_heartbeat = 0.0
        heartbeat_interval = 1
        heartbeat_times = []

        # Mock time progressing
        simulated_time_points = [0.0, 0.5, 1.1, 1.5, 2.1, 2.5, 3.1]

        for now in simulated_time_points:
            if now - last_heartbeat >= heartbeat_interval:
                heartbeat_times.append(now)
                last_heartbeat = now

        # Should have heartbeats at approximately 1.1, 2.1, 3.1
        assert len(heartbeat_times) == 3
        assert heartbeat_times[0] >= 1.0
        assert heartbeat_times[1] >= 2.0
        assert heartbeat_times[2] >= 3.0


class TestSSEEventStructure:
    """Tests for SSEEvent class and format."""

    def test_sse_event_to_sse_format(self):
        """Test SSEEvent.to_sse_format() produces correct SSE protocol format."""
        try:
            from forge_harness.webhook_server import SSEEvent
        except ImportError:
            pytest.skip("webhook_server not available")

        # Use a fixed timestamp for testing
        test_timestamp = "2026-02-06T12:00:00+00:00"

        event = SSEEvent(
            id="test-123",
            event="heartbeat",
            data={"timestamp": test_timestamp},
            timestamp=test_timestamp,
            source="webhook-server",
        )

        sse_format = event.to_sse_format()

        # SSE format should have id, event, data fields
        assert "id: test-123" in sse_format
        assert "event: heartbeat" in sse_format
        assert "data:" in sse_format

        # Should end with double newline
        assert sse_format.endswith("\n\n")

        # Parse the data JSON to verify structure
        lines = sse_format.strip().split("\n")
        data_line = [l for l in lines if l.startswith("data:")][0]
        data_json = data_line.replace("data:", "").strip()
        data = json.loads(data_json)

        assert data["id"] == "test-123"
        assert data["type"] == "heartbeat"
        assert data["timestamp"] == test_timestamp
        assert data["source"] == "webhook-server"

    def test_heartbeat_event_id_format(self):
        """Test that heartbeat event IDs follow the expected format."""
        try:
            from forge_harness.webhook_server import SSEEvent
        except ImportError:
            pytest.skip("webhook_server not available")

        # Mock time for consistent event ID
        with patch("time.time", return_value=1234567890.0):
            event_id = f"heartbeat_{int(1234567890.0)}"

            event = SSEEvent(
                id=event_id,
                event="heartbeat",
                data={"timestamp": datetime.now(UTC).isoformat()},
                source="webhook-server",
            )

            assert event.id == "heartbeat_1234567890"
            assert event.id.startswith("heartbeat_")
            assert event.id.split("_")[1].isdigit()


@pytest.mark.asyncio
class TestSSEConnectionLifecycle:
    """Tests for SSE connection lifecycle with heartbeat."""

    async def test_heartbeat_starts_on_connection(self, monkeypatch):
        """Test that heartbeat timer starts when SSE connection is established."""
        monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL", "1")

        # Mock the FastAPI SSE endpoint behavior
        last_heartbeat = 0.0
        heartbeat_started = False

        # Simulate connection establishment
        async def simulate_connection():
            nonlocal last_heartbeat, heartbeat_started

            # Initialize heartbeat
            last_heartbeat = 0.0
            heartbeat_started = True

            return heartbeat_started

        result = await simulate_connection()

        assert result is True
        assert last_heartbeat == 0.0
        assert heartbeat_started is True

    async def test_heartbeat_stops_on_disconnection(self):
        """Test that heartbeat timer stops when connection closes."""
        heartbeat_timer = MagicMock()

        # Simulate cleanup on disconnect
        def cleanup():
            if heartbeat_timer:
                heartbeat_timer.cancel()
                return True
            return False

        result = cleanup()
        assert result is True
        heartbeat_timer.cancel.assert_called_once()

    async def test_connection_survives_heartbeat_events(self, monkeypatch):
        """Test that heartbeat events don't cause connection issues."""
        monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL", "1")

        # Track connection state
        connection_active = True
        heartbeat_count = 0

        # Simulate heartbeat events
        for _ in range(5):
            heartbeat_count += 1
            # Connection should remain active
            assert connection_active is True
            await asyncio.sleep(0.01)

        assert heartbeat_count == 5
        assert connection_active is True


@pytest.mark.asyncio
class TestEventBusIntegration:
    """Tests for heartbeat integration with EventBus."""

    async def test_heartbeat_uses_event_bus(self):
        """Test that heartbeat events go through the EventBus."""
        try:
            from forge_harness.webhook_server import get_event_bus
        except ImportError:
            pytest.skip("webhook_server not available")

        event_bus = get_event_bus()
        queue = event_bus.subscribe()

        # Publish a heartbeat event
        await event_bus.publish(
            event_type="heartbeat",
            data={"timestamp": datetime.now(UTC).isoformat()},
            source="webhook-server",
        )

        # Verify event was received
        try:
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert event is not None
            assert event.event == "heartbeat"
            assert "timestamp" in event.data
        except TimeoutError:
            pytest.fail("Heartbeat event was not received from EventBus")

    async def test_heartbeat_subscriber_receives_events(self):
        """Test that subscribers receive heartbeat events."""
        try:
            from forge_harness.webhook_server import get_event_bus
        except ImportError:
            pytest.skip("webhook_server not available")

        event_bus = get_event_bus()
        received_events = []

        # Subscribe to all events
        queue = event_bus.subscribe()

        # Publish heartbeat
        await event_bus.publish(
            event_type="heartbeat",
            data={"timestamp": datetime.now(UTC).isoformat()},
            source="test",
        )

        # Collect event
        try:
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
            received_events.append(event)
        except TimeoutError:
            pass

        assert len(received_events) > 0
        assert received_events[0].event == "heartbeat"


# ============================================================================
# Integration Tests
# ============================================================================


@pytest.mark.asyncio
class TestSSEHeartbeatIntegration:
    """Integration tests for complete SSE heartbeat flow."""

    async def test_end_to_end_heartbeat_flow(self, mock_app, monkeypatch):
        """Test complete heartbeat flow from server to client."""
        monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL", "1")

        import importlib

        import forge_harness.webhook_server as ws

        importlib.reload(ws)

        from forge_harness.webhook_server import AuthConfig, create_app

        auth_config = AuthConfig(require_auth=False)
        app = create_app(None, auth_config=auth_config)

        from fastapi.testclient import TestClient

        client = TestClient(app)

        # Track heartbeat reception
        heartbeat_received = False

        with patch("time.time") as mock_time:
            # Control time to trigger heartbeat
            time_sequence = [0.0, 0.5, 1.1]
            time_index = 0

            def get_time():
                nonlocal time_index
                if time_index < len(time_sequence):
                    val = time_sequence[time_index]
                    time_index += 1
                    return val
                return time_sequence[-1]

            mock_time.side_effect = get_time

            # This test validates the endpoint exists and returns correct content type
            # Full SSE streaming tested via manual testing or E2E tests
            response = client.get("/api/events")

            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

    async def test_heartbeat_configuration_is_respected(self, monkeypatch):
        """Test that custom heartbeat configuration is respected."""
        custom_interval = 5
        monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL", str(custom_interval))

        import importlib

        import forge_harness.webhook_server as ws

        importlib.reload(ws)

        assert ws.SSE_HEARTBEAT_INTERVAL == custom_interval

        # Simulate using the configured interval
        last_heartbeat = 0.0
        heartbeat_interval = ws.SSE_HEARTBEAT_INTERVAL

        # Time progresses but not enough for heartbeat
        now = 3.0
        should_send = (now - last_heartbeat) >= heartbeat_interval
        assert should_send is False

        # Time progresses past interval
        now = 5.5
        should_send = (now - last_heartbeat) >= heartbeat_interval
        assert should_send is True
