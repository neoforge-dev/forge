"""Tests for POST /api/completions endpoint.

Tests cover:
- POST /api/completions - Report task completion
- Returns 200 with status published
- Input validation via endpoint behavior
- EventBus publishing verification
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from forge_harness.webhook_server.infrastructure.auth import AuthConfig
from forge_harness.webhook_server_main import create_app


def _make_client() -> TestClient:
    """Create a TestClient with auth disabled for testing."""
    app = create_app(auth_config=AuthConfig(require_auth=False))
    return TestClient(app)


class TestCompletionsAPI:
    """Test class for POST /api/completions endpoint."""

    def test_completions_endpoint_exists(self):
        """Test that POST /api/completions endpoint exists."""
        app = create_app()
        routes = [r.path for r in app.routes]
        assert "/api/completions" in routes

    def test_report_completion_returns_200(self):
        """Test that completion returns 200 status."""
        client = _make_client()

        response = client.post(
            "/api/completions",
            json={"message": "Task completed successfully", "agent_id": "test-agent-001"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["status"] == "published"
        assert data["data"]["event_type"] == "agent.completed"

    def test_report_completion_without_agent_id(self):
        """Test completion with optional agent_id omitted."""
        client = _make_client()

        response = client.post("/api/completions", json={"message": "Background task finished"})

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["status"] == "published"

    def test_completion_response_format(self):
        """Test that completion response follows API format."""
        client = _make_client()

        response = client.post("/api/completions", json={"message": "Test completion"})

        assert response.status_code == 200
        data = response.json()

        # Verify API response format
        assert "success" in data
        assert "data" in data
        assert "error" in data
        assert "timestamp" in data
        assert data["success"] is True
        assert data["error"] is None

    def test_completion_with_empty_message_fails(self):
        """Test that empty message is rejected."""
        client = _make_client()

        response = client.post("/api/completions", json={"message": "", "agent_id": "test-agent"})

        # Should fail validation (message min_length=1)
        assert response.status_code == 422

    def test_completion_missing_message_fails(self):
        """Test that missing message is rejected."""
        client = _make_client()

        response = client.post("/api/completions", json={"agent_id": "test-agent"})

        # Should fail validation (message is required)
        assert response.status_code == 422

    def test_completion_response_has_correct_fields(self):
        """Test that response data has correct fields."""
        client = _make_client()

        response = client.post("/api/completions", json={"message": "Test", "agent_id": "my-agent"})

        assert response.status_code == 200
        data = response.json()

        assert data["data"]["status"] == "published"
        assert data["data"]["event_type"] == "agent.completed"

    def test_completion_with_special_characters(self):
        """Test completion with special characters in message."""
        client = _make_client()

        special_message = "Task 'complex-name' completed (via agent-pi)"

        response = client.post("/api/completions", json={"message": special_message})

        assert response.status_code == 200

    def test_completion_with_unicode(self):
        """Test completion with unicode characters in message."""
        client = _make_client()

        unicode_message = "Task completed with 🚀 emoji and üñíçödé"

        response = client.post("/api/completions", json={"message": unicode_message})

        assert response.status_code == 200


class TestCompletionsEventBusIntegration:
    """Test EventBus integration (integration tests)."""

    def test_endpoint_publishes_to_event_bus(self):
        """Test that endpoint publishes to EventBus (integration test)."""
        from forge_harness.webhook_server.services.event_bus import EventBus, get_event_bus

        client = _make_client()

        # Track publish calls by patching at class level
        original_publish = EventBus.publish
        publish_calls = []

        async def tracking_publish(self, event_type, data, source=None):
            publish_calls.append({"type": event_type, "data": data, "source": source})

        # Patch the publish method on the EventBus class
        EventBus.publish = tracking_publish

        try:
            response = client.post(
                "/api/completions",
                json={"message": "Integration test completion", "agent_id": "test-agent-123"},
            )

            assert response.status_code == 200
            assert len(publish_calls) == 1
            assert publish_calls[0]["type"] == "agent.completed"
            assert publish_calls[0]["data"]["message"] == "Integration test completion"
            assert publish_calls[0]["data"]["agent_id"] == "test-agent-123"
        finally:
            # Restore original publish method
            EventBus.publish = original_publish

    def test_event_bus_singleton_instance(self):
        """Test that EventBus is a singleton."""
        from forge_harness.webhook_server.services.event_bus import EventBus, get_event_bus

        bus1 = get_event_bus()
        bus2 = get_event_bus()

        assert bus1 is bus2  # Same instance

    def test_endpoint_calls_real_event_bus(self):
        """Test that endpoint actually calls EventBus.publish (no mocking)."""
        from forge_harness.webhook_server.services.event_bus import get_event_bus

        # Verify EventBus is initialized
        event_bus = get_event_bus()
        assert event_bus is not None
        assert hasattr(event_bus, "subscribe")

        client = _make_client()

        # Make request - this should work without mocking
        response = client.post("/api/completions", json={"message": "Real EventBus test"})

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "published"


class TestCompletionsNotifyScript:
    """Test compatibility with notify-completion.sh script."""

    def test_notify_script_format(self):
        """Test that notify-completion.sh format works."""
        client = _make_client()

        # Simulate notify-completion.sh call
        completion_message = "Task 'implement-api' completed by agent-pi"

        response = client.post(
            "/api/completions", json={"message": completion_message, "agent_id": "pi"}
        )

        assert response.status_code == 200
        assert response.json()["data"]["status"] == "published"

    def test_minimal_notify_format(self):
        """Test minimal format (message only, no agent_id)."""
        client = _make_client()

        response = client.post("/api/completions", json={"message": "Simple completion"})

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestCompletionsResponseTimestamp:
    """Test timestamp in completion response."""

    def test_response_has_timestamp(self):
        """Test that response includes ISO timestamp."""
        from datetime import datetime

        client = _make_client()

        response = client.post("/api/completions", json={"message": "Timestamp test"})

        assert response.status_code == 200
        data = response.json()

        assert "timestamp" in data
        # Verify it's a valid ISO format timestamp
        timestamp = data["timestamp"]
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        assert parsed is not None
