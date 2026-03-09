"""
Tests for XNode Realtime Bridge API Endpoint
=============================================

Integration tests for the consolidated XNode realtime delivery endpoint.

Tests cover:
1. Endpoint registration in FastAPI app
2. Request model validation
3. Authentication (Bearer token via unified auth)
4. Envelope validation (schema_version, required fields)
5. Channel routing by event type
6. 202 Accepted response format

Run with: pytest tests/test_xnode_api.py -v
"""

import pytest

from forge_harness.webhook_server.infrastructure.auth import AuthConfig


@pytest.fixture
def auth_app(tmp_path, monkeypatch):
    """Create FastAPI TestClient with auth enabled (Bearer token)."""
    from fastapi.testclient import TestClient

    from forge_harness.webhook_server import create_app

    config_dir = tmp_path / ".forge/config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "test-xnode-token")
    monkeypatch.setenv("FORGE_WEBHOOK_REQUIRE_AUTH", "true")
    monkeypatch.setenv("FORGE_WEBHOOK_ALLOW_LOCALHOST", "false")

    auth_config = AuthConfig(
        bearer_token="test-xnode-token",
        require_auth=True,
        allow_localhost=False,
    )
    app = create_app(auth_config=auth_config)
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_xnode_idempotency_cache():
    """Isolate bridge idempotency cache between tests."""
    from forge_harness.webhook_server.api import xnode as xnode_api

    xnode_api._reset_seen_keys_for_tests()
    yield
    xnode_api._reset_seen_keys_for_tests()


@pytest.fixture
def noauth_app(tmp_path, monkeypatch):
    """Create FastAPI TestClient with auth disabled."""
    from fastapi.testclient import TestClient

    from forge_harness.webhook_server import create_app

    config_dir = tmp_path / ".forge/config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("FORGE_WEBHOOK_TOKEN", raising=False)
    monkeypatch.setenv("FORGE_WEBHOOK_REQUIRE_AUTH", "false")
    monkeypatch.setenv("FORGE_WEBHOOK_ALLOW_LOCALHOST", "true")

    auth_config = AuthConfig(
        bearer_token=None,
        require_auth=False,
        allow_localhost=True,
    )
    app = create_app(auth_config=auth_config)
    return TestClient(app)


def _valid_envelope(**overrides):
    """Build a minimal valid envelope, merging any overrides."""
    base = {
        "schema_version": "1.0",
        "message_id": "msg_test_123",
        "type": "lead.send",
        "source": {"node": "prya"},
        "target": {"node": "sati"},
    }
    base.update(overrides)
    return base


class TestXNodeEndpointRegistration:
    """Tests for XNode endpoint registration."""

    def test_xnode_events_endpoint_registered(self, noauth_app):
        """POST /api/xnode/events should be registered."""
        routes = [route.path for route in noauth_app.app.routes]
        assert "/api/xnode/events" in routes


class TestXNodeRequestModels:
    """Tests for Pydantic request models."""

    def test_xnode_event_request_model_valid(self):
        """XNodeEventRequest model should validate event_type and envelope."""
        from forge_harness.webhook_server.api.xnode import XNodeEventRequest

        request = XNodeEventRequest(
            event_type="lead.send",
            envelope={
                "message_id": "msg_test_123",
                "correlation_id": "task_TEST",
                "source": {"node": "prya", "lead_window": "forge:prya"},
                "target": {"node": "sati", "lead_window": "forge:sati"},
                "type": "lead.send",
            },
        )

        assert request.event_type == "lead.send"
        assert request.envelope["message_id"] == "msg_test_123"


class TestXNodeAuthentication:
    """Tests for Bearer token authentication."""

    def test_valid_token_accepts_request(self, auth_app):
        """Valid Bearer token should allow request."""
        response = auth_app.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": _valid_envelope(),
            },
            headers={"Authorization": "Bearer test-xnode-token"},
        )

        assert response.status_code == 202

    def test_missing_token_rejects_request(self, auth_app):
        """Missing Authorization header should return 401."""
        response = auth_app.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": _valid_envelope(),
            },
        )

        assert response.status_code == 401
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "unauthorized"

    def test_invalid_token_rejects_request(self, auth_app):
        """Invalid Bearer token should return 401."""
        response = auth_app.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": _valid_envelope(),
            },
            headers={"Authorization": "Bearer wrong-token"},
        )

        assert response.status_code == 401
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "unauthorized"


class TestXNodeEnvelopeValidation:
    """Tests for envelope field validation."""

    def test_missing_required_fields_return_400(self, noauth_app):
        """Missing required envelope fields should return 400."""
        response = noauth_app.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": {
                    "priority": "high",
                },
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "invalid_envelope"

    def test_partial_missing_fields_returns_400(self, noauth_app):
        """Partial missing required fields should return 400."""
        response = noauth_app.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": {
                    "message_id": "msg_test_123",
                    "correlation_id": "task_TEST",
                },
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "invalid_envelope"

    def test_valid_envelope_passes_validation(self, noauth_app):
        """Complete valid envelope should pass validation."""
        response = noauth_app.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": _valid_envelope(priority="high"),
            },
        )

        assert response.status_code == 202


class TestXNodeChannelRouting:
    """Tests for channel routing by event type."""

    def test_lead_send_routes_to_inbox(self, noauth_app):
        """lead.send events should route to lead.{target}.inbox channel."""
        response = noauth_app.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": _valid_envelope(),
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["data"]["channel"] == "lead.sati.inbox"

    def test_lead_handoff_routes_to_handoff(self, noauth_app):
        """lead.handoff events should route to lead.{target}.handoff channel."""
        response = noauth_app.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.handoff",
                "envelope": _valid_envelope(type="lead.handoff"),
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["data"]["channel"] == "lead.sati.handoff"

    def test_xnode_exception_routes_to_exceptions(self, noauth_app):
        """xnode.relay.exception events should route to xnode.exceptions channel."""
        response = noauth_app.post(
            "/api/xnode/events",
            json={
                "event_type": "xnode.relay.exception",
                "envelope": _valid_envelope(type="xnode.relay.exception"),
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["data"]["channel"] == "xnode.exceptions"


class TestXNodeResponseFormat:
    """Tests for 202 Accepted response format."""

    def test_response_includes_delivery_id(self, noauth_app):
        """Response should include unique delivery_id."""
        response1 = noauth_app.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": _valid_envelope(message_id="msg_test_123"),
            },
        )

        response2 = noauth_app.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": _valid_envelope(message_id="msg_test_456"),
            },
        )

        assert response1.status_code == 202
        assert response2.status_code == 202

        data1 = response1.json()
        data2 = response2.json()

        assert "delivery_id" in data1["data"]
        assert "delivery_id" in data2["data"]
        assert data1["data"]["delivery_id"] != data2["data"]["delivery_id"]

    def test_response_includes_accepted_flag(self, noauth_app):
        """Response should include accepted=True flag."""
        response = noauth_app.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": _valid_envelope(),
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["data"]["accepted"] is True


class TestXNodeEventTypes:
    """Tests for supported event types."""

    def test_all_supported_event_types(self, noauth_app):
        """All supported event types should be accepted."""
        event_types = [
            "lead.send",
            "lead.ack",
            "lead.handoff",
            "lead.broadcast",
            "xnode.relay.exception",
        ]

        for event_type in event_types:
            response = noauth_app.post(
                "/api/xnode/events",
                json={
                    "event_type": event_type,
                    "envelope": _valid_envelope(type=event_type),
                },
            )

            assert response.status_code == 202, f"Failed for event_type: {event_type}"
