"""Tests for XNode Bridge API endpoints (modular router).

Tests for:
- POST /api/xnode/events - Receive xnode realtime events
- Returns 202 with delivery_id
- Envelope validation returns 400 on missing fields
- Channel routing for lead.send, lead.ack, lead.handoff, lead.broadcast, xnode.relay.exception
- Idempotency deduplication
- Auth required (401 without token)
"""

import pytest
from fastapi.testclient import TestClient

from forge_harness.webhook_server.infrastructure.auth import AuthConfig


def _make_client(auth: bool = False) -> TestClient:
    """Create a TestClient with auth disabled (default) or enabled.

    Using an explicit AuthConfig ensures tests are independent of
    environment variables like FORGE_WEBHOOK_TOKEN.
    """
    from forge_harness.webhook_server_main import create_app

    if auth:
        auth_config = AuthConfig(
            bearer_token="test-token",
            require_auth=True,
            allow_localhost=False,
        )
    else:
        auth_config = AuthConfig(
            bearer_token=None,
            require_auth=False,
            allow_localhost=True,
        )
    return TestClient(create_app(auth_config=auth_config))


@pytest.fixture(autouse=True)
def _reset_xnode_idempotency_cache():
    """Isolate bridge idempotency cache between tests."""
    from forge_harness.webhook_server.api import xnode as xnode_api

    xnode_api._reset_seen_keys_for_tests()
    yield
    xnode_api._reset_seen_keys_for_tests()


class TestXNodeEventsAPI:
    """Test class for POST /api/xnode/events endpoint."""

    def test_xnode_events_endpoint_exists(self):
        """Test that POST /api/xnode/events endpoint exists."""
        from forge_harness.webhook_server_main import create_app

        app = create_app()
        routes = [r.path for r in app.routes]
        assert "/api/xnode/events" in routes

    def test_xnode_events_returns_202_with_delivery_id(self):
        """Test that xnode events returns 202 with delivery_id."""
        client = _make_client()

        response = client.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": {
                    "schema_version": "1.0",
                    "message_id": "msg_test_123",
                    "correlation_id": "task_TEST",
                    "idempotency_key": "lead-send-test-123",
                    "sent_at": "2026-02-13T20:47:18Z",
                    "expires_at": "2026-02-14T20:47:18Z",
                    "source": {
                        "node": "prya",
                        "lead_window": "forge:prya",
                        "agent": "orchestrator",
                    },
                    "target": {
                        "node": "sati",
                        "lead_window": "forge:sati",
                        "agent": "orchestrator",
                    },
                    "priority": "high",
                    "type": "lead.send",
                    "requires_ack": True,
                    "payload": {},
                },
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["success"] is True
        assert data["data"]["accepted"] is True
        assert "delivery_id" in data["data"]
        assert data["data"]["channel"] == "lead.sati.inbox"

    def test_xnode_events_returns_400_on_missing_envelope_fields(self):
        """Test that envelope validation returns 400 on missing fields."""
        client = _make_client()

        response = client.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": {
                    "message_id": "msg_test_123",
                },
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "invalid_envelope"

    def test_xnode_events_returns_400_on_unsupported_schema(self):
        """Test that unsupported schema_version returns 400."""
        client = _make_client()

        response = client.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": {
                    "schema_version": "2.0",
                    "message_id": "msg_test_123",
                    "type": "lead.send",
                },
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "unsupported_schema"

    def test_xnode_events_routes_exception_to_xnode_exceptions_channel(self):
        """Test that xnode.relay.exception events route to xnode.exceptions channel."""
        client = _make_client()

        response = client.post(
            "/api/xnode/events",
            json={
                "event_type": "xnode.relay.exception",
                "envelope": {
                    "schema_version": "1.0",
                    "message_id": "msg_exception_123",
                    "type": "xnode.relay.exception",
                    "idempotency_key": "exception-test-123",
                    "source": {"node": "prya"},
                    "target": {"node": "sati"},
                    "payload": {"error": "test error"},
                },
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["data"]["channel"] == "xnode.exceptions"

    def test_xnode_events_routes_broadcast_to_lead_broadcast_channel(self):
        """Test that lead.broadcast events route to lead.broadcast channel."""
        client = _make_client()

        response = client.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.broadcast",
                "envelope": {
                    "schema_version": "1.0",
                    "message_id": "msg_broadcast_123",
                    "type": "lead.broadcast",
                    "idempotency_key": "broadcast-test-123",
                    "source": {"node": "orchestrator"},
                    "target": {"node": "all"},
                    "payload": {"message": "test broadcast"},
                },
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["data"]["channel"] == "lead.broadcast"

    def test_xnode_events_routes_ack_to_lead_ack_channel(self):
        """Test that lead.ack events route to lead.<node>.ack channel."""
        client = _make_client()

        response = client.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.ack",
                "envelope": {
                    "schema_version": "1.0",
                    "message_id": "msg_ack_123",
                    "type": "lead.ack",
                    "idempotency_key": "ack-test-123",
                    "source": {"node": "sati"},
                    "target_node": "prya",
                    "status": "ack",
                    "acknowledged_by_node": "sati",
                },
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["data"]["channel"] == "lead.prya.ack"

    def test_xnode_events_routes_handoff_to_lead_handoff_channel(self):
        """Test that lead.handoff events route to lead.<node>.handoff channel."""
        client = _make_client()

        response = client.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.handoff",
                "envelope": {
                    "schema_version": "1.0",
                    "message_id": "msg_handoff_123",
                    "type": "lead.handoff",
                    "idempotency_key": "handoff-test-123",
                    "source": {"node": "prya"},
                    "target": {"node": "sati"},
                    "handoff_id": "ho_20260220_prya_to_sati",
                    "from_task_id": "IS-100",
                    "to_task_id": "IS-101",
                },
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["data"]["channel"] == "lead.sati.handoff"

    def test_xnode_events_idempotency_key_prevents_duplicate(self):
        """Test that duplicate idempotency_key returns success but marks as deduplicated."""
        client = _make_client()

        idempotency_key = "idempotency-test-456"

        response1 = client.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": {
                    "schema_version": "1.0",
                    "message_id": "msg_dedup_123",
                    "type": "lead.send",
                    "idempotency_key": idempotency_key,
                    "source": {"node": "prya"},
                    "target": {"node": "sati"},
                },
            },
        )
        assert response1.status_code == 202
        data1 = response1.json()
        assert data1["data"]["accepted"] is True
        assert "deduplicated" not in data1["data"] or data1["data"].get("deduplicated") is None

        response2 = client.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": {
                    "schema_version": "1.0",
                    "message_id": "msg_dedup_456",
                    "type": "lead.send",
                    "idempotency_key": idempotency_key,
                    "source": {"node": "prya"},
                    "target": {"node": "sati"},
                },
            },
        )
        assert response2.status_code == 200
        data2 = response2.json()
        assert data2["data"]["accepted"] is True
        assert data2["data"].get("deduplicated") is True

    def test_idempotency_window_expires_keys(self, monkeypatch):
        """Idempotency dedupe should expire keys after TTL window."""
        from forge_harness.webhook_server.api import xnode as xnode_api

        monkeypatch.setattr(xnode_api, "_SEEN_KEYS_TTL_SECONDS", 5)
        assert xnode_api._is_duplicate_idempotency_key("expiring-key", now_monotonic=100.0) is False
        assert xnode_api._is_duplicate_idempotency_key("expiring-key", now_monotonic=102.0) is True
        assert xnode_api._is_duplicate_idempotency_key("expiring-key", now_monotonic=106.0) is False

    def test_idempotency_window_is_bounded(self, monkeypatch):
        """Idempotency cache should enforce max-entry bound."""
        from forge_harness.webhook_server.api import xnode as xnode_api

        monkeypatch.setattr(xnode_api, "_SEEN_KEYS_MAX_ENTRIES", 2)
        monkeypatch.setattr(xnode_api, "_SEEN_KEYS_TTL_SECONDS", 300)

        xnode_api._is_duplicate_idempotency_key("k1", now_monotonic=100.0)
        xnode_api._is_duplicate_idempotency_key("k2", now_monotonic=101.0)
        xnode_api._is_duplicate_idempotency_key("k3", now_monotonic=102.0)

        with xnode_api._seen_keys_lock:
            assert len(xnode_api._seen_keys) == 2
            assert "k1" not in xnode_api._seen_keys
            assert "k2" in xnode_api._seen_keys
            assert "k3" in xnode_api._seen_keys

    def test_xnode_events_requires_auth(self):
        """Test that xnode events endpoint requires authentication (without localhost bypass)."""
        client = _make_client(auth=True)

        response = client.post(
            "/api/xnode/events",
            json={
                "event_type": "lead.send",
                "envelope": {
                    "schema_version": "1.0",
                    "message_id": "msg_auth_test",
                    "type": "lead.send",
                },
            },
        )

        assert response.status_code == 401
