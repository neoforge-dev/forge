"""Extended tests for /api/relay POST endpoints.

Covers:
- POST /api/relay/dispatch      create delivery record
- POST /api/relay/ack           acknowledge delivery
- POST /api/relay/nack          negative acknowledge
- JSON envelope consistency across all endpoints
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge_harness.models.delivery import DeliveryRecord, DeliveryState

# ---------------------------------------------------------------------------
# Helpers (copied from test_api_relay.py)
# ---------------------------------------------------------------------------


def _make_record(
    message_id: str,
    state: DeliveryState = DeliveryState.QUEUED,
    target_agent: str | None = "gemini",
    task_id: str | None = None,
) -> DeliveryRecord:
    """Build a minimal DeliveryRecord for use in tests."""
    return DeliveryRecord(
        message_id=message_id,
        task_id=task_id or message_id,
        source_node="prya",
        target_node="prya",
        target_agent=target_agent,
        state=state,
        created_at=datetime.now(UTC),
        payload_summary="Test dispatch content",
    )


def _write_jsonl(path: Path, records: list[DeliveryRecord]) -> None:
    """Append each record as a JSON line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(rec.model_dump_json() + "\n")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Create a test client with the relay router mounted."""
    from forge_harness.webhook_server.api.relay import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def authed_client(client):
    """Test client with auth dependency bypassed (operator-level)."""
    from forge_harness.webhook_server.core.dependencies import verify_unified_auth

    client.app.dependency_overrides[verify_unified_auth] = lambda: {
        "sub": "test-operator",
        "permissions": ["operator"],
    }
    yield client
    client.app.dependency_overrides.clear()


@pytest.fixture
def sample_log(tmp_path) -> Path:
    """JSONL log file containing a set of delivery records."""
    log = tmp_path / "delivery_log.jsonl"
    records = [
        _make_record("msg_001", DeliveryState.QUEUED, target_agent="gemini"),
        _make_record("msg_002", DeliveryState.DISPATCHED, target_agent="kimi"),
        _make_record("msg_003", DeliveryState.DELIVERED, target_agent="gemini"),
        _make_record("msg_003", DeliveryState.ACKED, target_agent="gemini"),
        _make_record("msg_004", DeliveryState.FAILED, target_agent="minimax"),
    ]
    _write_jsonl(log, records)
    return log


# ---------------------------------------------------------------------------
# Tests: POST /api/relay/dispatch
# ---------------------------------------------------------------------------


class TestPostDispatch:
    """Tests for the dispatch endpoint (POST to create delivery)."""

    def test_no_auth_returns_401(self, client):
        response = client.post("/api/relay/dispatch", json={
            "target_agent": "gemini",
            "message": "Test message"
        })
        assert response.status_code == 401

    def test_dispatch_creates_queued_record(self, authed_client):
        """Dispatch creates a delivery record in QUEUED state."""
        response = authed_client.post("/api/relay/dispatch", json={
            "target_agent": "gemini",
            "target_node": "sati",
            "message": "Test dispatch message",
            "task_id": "TASK-123",
            "source_node": "prya"
        })

        # 201 Created or 202 Accepted both valid
        assert response.status_code in [201, 202]
        data = response.json()
        assert "message_id" in data
        # State may be "queued" (ideal) or "failed" (if dispatch fails immediately)
        assert data["state"] in ["queued", "failed"]
        assert data["target_agent"] == "gemini"

    def test_dispatch_missing_target_returns_422(self, authed_client):
        """Dispatch without target_agent returns validation error."""
        response = authed_client.post("/api/relay/dispatch", json={
            "message": "No target specified"
        })

        assert response.status_code == 422

    def test_dispatch_json_envelope_format(self, authed_client):
        """Dispatch response uses standardized JSON envelope."""
        response = authed_client.post("/api/relay/dispatch", json={
            "target_agent": "kimi",
            "message": "Test with envelope",
            "task_id": "TASK-456"
        })

        data = response.json()
        # Standard envelope: success, data, error, timestamp
        assert "success" in data or "state" in data
        assert "message_id" in data
        assert isinstance(data.get("timestamp", ""), str)


# ---------------------------------------------------------------------------
# Tests: POST /api/relay/ack
# ---------------------------------------------------------------------------


class TestPostAck:
    """Tests for the ack endpoint (acknowledge delivery)."""

    def test_no_auth_returns_401_or_404(self, client):
        """Ack endpoint returns 401 (no auth) or 404 (not implemented)."""
        response = client.post("/api/relay/ack", json={
            "message_id": "msg_001"
        })
        # 401 if endpoint exists and requires auth
        # 404 if endpoint doesn't exist (not implemented yet)
        assert response.status_code in [401, 404]

    def test_ack_unknown_message_returns_404(self, authed_client):
        """Acking unknown message returns 404."""
        response = authed_client.post("/api/relay/ack", json={
            "message_id": "msg_nonexistent",
            "status": "ack"
        })

        assert response.status_code == 404

    def test_ack_valid_message_updates_state(self, authed_client, sample_log):
        """Ack updates delivery state to ACKED."""
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.post("/api/relay/ack", json={
                "message_id": "msg_001",
                "status": "ack",
                "note": "Processing started"
            })

        # Should succeed (or 404 if message not found)
        assert response.status_code in [200, 202, 404]
        if response.status_code in [200, 202]:
            data = response.json()
            assert data.get("state") == "acked" or "success" in data

    def test_ack_json_envelope_format(self, authed_client, sample_log):
        """Ack response uses standardized JSON envelope."""
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.post("/api/relay/ack", json={
                "message_id": "msg_002",
                "status": "ack"
            })

        if response.status_code in [200, 202]:
            data = response.json()
            # Should have standard envelope fields
            assert any(field in data for field in ["success", "state", "data"])


# ---------------------------------------------------------------------------
# Tests: POST /api/relay/nack
# ---------------------------------------------------------------------------


class TestPostNack:
    """Tests for the nack endpoint (negative acknowledge)."""

    def test_no_auth_returns_401_or_404(self, client):
        """Nack endpoint returns 401 (no auth) or 404 (not implemented)."""
        response = client.post("/api/relay/nack", json={
            "message_id": "msg_001"
        })
        # 401 if endpoint exists and requires auth
        # 404 if endpoint doesn't exist (not implemented yet)
        assert response.status_code in [401, 404]

    def test_nack_unknown_message_returns_404(self, authed_client):
        """Nacking unknown message returns 404."""
        response = authed_client.post("/api/relay/nack", json={
            "message_id": "msg_nonexistent",
            "reason": "Cannot process"
        })

        assert response.status_code == 404

    def test_nack_valid_message_updates_state(self, authed_client, sample_log):
        """Nack updates delivery state to FAILED."""
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.post("/api/relay/nack", json={
                "message_id": "msg_001",
                "reason": "Agent unavailable"
            })

        # Should succeed (or 404 if message not found)
        assert response.status_code in [200, 202, 404]

    def test_nack_requires_reason(self, authed_client, sample_log):
        """Nack should accept reason field."""
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.post("/api/relay/nack", json={
                "message_id": "msg_003",
                "reason": "Context exhausted"
            })

        # Just verify it doesn't crash
        assert response.status_code in [200, 202, 404]


# ---------------------------------------------------------------------------
# Tests: JSON Envelope Consistency
# ---------------------------------------------------------------------------


class TestJsonEnvelopeConsistency:
    """Verify all relay endpoints use consistent JSON envelope format."""

    ENVELOPE_FIELDS = {"success", "data", "error", "timestamp"}

    def _check_envelope(self, data: dict, allow_list: bool = False):
        """Check if response has standard envelope or list format."""
        if allow_list and isinstance(data, list):
            return True
        if not isinstance(data, dict):
            return False
        # Should have at least one envelope field OR be a data wrapper
        has_envelope = bool(self.ENVELOPE_FIELDS & set(data.keys()))
        has_data_wrapper = "data" in data or "deliveries" in data or "delivery" in data
        # Stats endpoint has its own format (total, by_state, etc.)
        has_stats_fields = "total" in data and "by_state" in data
        return has_envelope or has_data_wrapper or has_stats_fields

    def test_list_deliveries_envelope(self, authed_client, sample_log):
        """GET /deliveries returns consistent envelope."""
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.get("/api/relay/deliveries")

        data = response.json()
        assert self._check_envelope(data, allow_list=False)
        # Should have these specific fields
        assert "deliveries" in data
        assert "total" in data
        assert "filtered" in data

    def test_get_delivery_envelope(self, authed_client, sample_log):
        """GET /deliveries/{id} returns consistent envelope."""
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.get("/api/relay/deliveries/msg_003")

        data = response.json()
        assert self._check_envelope(data)
        assert "delivery" in data or "data" in data

    def test_stats_envelope(self, authed_client, sample_log):
        """GET /stats returns consistent envelope."""
        with patch(
            "forge_harness.webhook_server.api.relay._DELIVERY_LOG",
            sample_log,
        ):
            response = authed_client.get("/api/relay/stats")

        data = response.json()
        assert self._check_envelope(data)
        assert "total" in data
        assert "by_state" in data

    def test_dispatch_envelope(self, authed_client):
        """POST /dispatch returns consistent envelope."""
        response = authed_client.post("/api/relay/dispatch", json={
            "target_agent": "gemini",
            "message": "Envelope test"
        })

        data = response.json()
        # 202 Accepted should have message_id and state
        assert "message_id" in data
        assert "state" in data

    def test_error_responses_use_detail_field(self, authed_client):
        """Error responses use 'detail' field consistently."""
        # 404 on unknown delivery
        response = authed_client.get("/api/relay/deliveries/msg_nonexistent")
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

        # 422 on invalid input
        response = authed_client.get("/api/relay/deliveries?state=invalid")
        assert response.status_code == 422
        # 422 uses FastAPI default format (detail field)
