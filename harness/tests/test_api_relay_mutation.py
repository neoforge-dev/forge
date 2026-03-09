"""Tests for relay POST mutation endpoints.

Covers the 3 POST endpoints in relay.py:
- POST /api/relay/dispatch - Dispatch a message to an agent via relay
- POST /api/relay/{message_id}/ack - Acknowledge a delivered message
- POST /api/relay/{message_id}/nack - Reject/nack a delivered message

Test patterns:
- Happy path (valid payload, auth token)
- Missing auth (401)
- Invalid payload (422)
- Non-existent target (404 for ack/nack)
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge_harness.models.delivery import DeliveryRecord, DeliveryState

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _make_delivery_record(
    message_id: str = "msg_20260226_120000_abc123",
    task_id: str | None = None,
    state: DeliveryState = DeliveryState.QUEUED,
    target_agent: str | None = "gemini",
) -> DeliveryRecord:
    """Create a DeliveryRecord for testing."""
    return DeliveryRecord(
        message_id=message_id,
        task_id=task_id or "task-001",
        source_node="nova",
        target_node="nova",
        source_agent="orchestrator",
        target_agent=target_agent,
        state=state,
        created_at=datetime.now(UTC),
        payload_summary="Test dispatch message",
    )


def _make_relay_worker(
    deliver_result: bool = True,
    existing_records: list[DeliveryRecord] | None = None,
) -> Mock:
    """Create a mock RelayWorker with configurable behavior."""
    worker = Mock()
    worker.poll_interval = 5.0
    worker.max_retries = 3
    worker.deliver = AsyncMock(return_value=deliver_result)
    worker.ack = AsyncMock()
    worker.nack = AsyncMock()
    worker._append_log = Mock()

    # For _read_delivery_log simulations
    worker._read_log = Mock(return_value=existing_records or [])

    return worker


def _bypass_auth(app: FastAPI) -> None:
    """Override auth dependencies so no credentials are needed in tests."""
    from forge_harness.webhook_server.core import dependencies

    _admin_user = {"sub": "test", "permissions": ["admin", "operator"]}

    app.dependency_overrides[dependencies.verify_auth] = lambda: _admin_user
    app.dependency_overrides[dependencies.verify_unified_auth] = lambda: _admin_user

    def _noop_require_permission(permission: str):
        async def _check():
            return _admin_user

        return _check

    app.dependency_overrides[dependencies.require_permission] = _noop_require_permission


@pytest.fixture
def relay_client():
    """TestClient for relay router with all deps mocked."""
    from forge_harness.webhook_server.api import relay as relay_module

    app = FastAPI()
    app.include_router(relay_module.router)
    _bypass_auth(app)

    client = TestClient(app, raise_server_exceptions=False)
    yield client


# -----------------------------------------------------------------------------
# POST /api/relay/dispatch tests
# -----------------------------------------------------------------------------


class TestDispatchEndpoint:
    """POST /api/relay/dispatch - Dispatch a message to an agent via relay."""

    def test_dispatch_success_delivered(self, relay_client):
        """Happy path: dispatch with valid payload returns 201 and delivered state."""
        client = relay_client
        worker = _make_relay_worker(deliver_result=True)

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            resp = client.post(
                "/api/relay/dispatch",
                json={
                    "target_agent": "gemini",
                    "message": "Test message",
                    "task_id": "TASK-001",
                    "source_agent": "orchestrator",
                    "priority": "high",
                },
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["state"] == "delivered"
        assert data["target_agent"] == "gemini"
        assert "message_id" in data

    def test_dispatch_success_failed_delivery(self, relay_client):
        """Dispatch where delivery fails returns 201 with failed state."""
        client = relay_client
        worker = _make_relay_worker(deliver_result=False)

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            resp = client.post(
                "/api/relay/dispatch",
                json={
                    "target_agent": "gemini",
                    "message": "Test message",
                },
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["state"] == "failed"
        assert data["target_agent"] == "gemini"

    def test_dispatch_minimal_payload(self, relay_client):
        """Dispatch with only required fields succeeds."""
        client = relay_client
        worker = _make_relay_worker(deliver_result=True)

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            resp = client.post(
                "/api/relay/dispatch",
                json={
                    "target_agent": "claude",
                    "message": "Simple message",
                },
            )

        assert resp.status_code == 201
        assert resp.json()["target_agent"] == "claude"

    def test_dispatch_missing_target_agent_422(self, relay_client):
        """Missing required target_agent field returns 422."""
        client = relay_client

        resp = client.post(
            "/api/relay/dispatch",
            json={"message": "No target agent"},
        )

        assert resp.status_code == 422

    def test_dispatch_missing_message_422(self, relay_client):
        """Missing required message field returns 422."""
        client = relay_client

        resp = client.post(
            "/api/relay/dispatch",
            json={"target_agent": "gemini"},
        )

        assert resp.status_code == 422

    def test_dispatch_invalid_priority_422(self, relay_client):
        """Invalid priority value returns 422."""
        client = relay_client

        resp = client.post(
            "/api/relay/dispatch",
            json={
                "target_agent": "gemini",
                "message": "Test",
                "priority": "invalid_priority_value",
            },
        )

        assert resp.status_code == 422

    def test_dispatch_calls_worker_deliver(self, relay_client):
        """Verify worker.deliver is called with the created record."""
        client = relay_client
        worker = _make_relay_worker(deliver_result=True)

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            client.post(
                "/api/relay/dispatch",
                json={
                    "target_agent": "kimi",
                    "message": "Test dispatch",
                    "task_id": "TASK-123",
                },
            )

        worker.deliver.assert_called_once()
        call_args = worker.deliver.call_args[0][0]
        assert isinstance(call_args, DeliveryRecord)
        assert call_args.target_agent == "kimi"
        assert call_args.task_id == "TASK-123"

    def test_dispatch_appends_log(self, relay_client):
        """Verify record is logged via worker._append_log."""
        client = relay_client
        worker = _make_relay_worker(deliver_result=True)

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            client.post(
                "/api/relay/dispatch",
                json={
                    "target_agent": "gemini",
                    "message": "Test message",
                },
            )

        worker._append_log.assert_called_once()
        record = worker._append_log.call_args[0][0]
        assert isinstance(record, DeliveryRecord)
        assert record.state == DeliveryState.QUEUED

    def test_dispatch_payload_truncation(self, relay_client):
        """Long messages should have payload_summary truncated to 120 chars."""
        client = relay_client
        worker = _make_relay_worker(deliver_result=True)
        long_message = "x" * 200

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            client.post(
                "/api/relay/dispatch",
                json={
                    "target_agent": "gemini",
                    "message": long_message,
                },
            )

        record = worker._append_log.call_args[0][0]
        assert len(record.payload_summary) <= 120


# -----------------------------------------------------------------------------
# POST /api/relay/{message_id}/ack tests
# -----------------------------------------------------------------------------


class TestAckEndpoint:
    """POST /api/relay/{message_id}/ack - Acknowledge a delivered message."""

    def test_ack_success(self, relay_client):
        """Happy path: ack a delivered message returns 200."""
        client = relay_client
        existing_record = _make_delivery_record(
            message_id="msg_001",
            state=DeliveryState.DELIVERED,
        )
        worker = _make_relay_worker(existing_records=[existing_record])

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            resp = client.post("/api/relay/msg_001/ack")

        assert resp.status_code == 200
        data = resp.json()
        assert data["message_id"] == "msg_001"
        assert data["state"] == "acked"

    def test_ack_nonexistent_message_404(self, relay_client):
        """Ack for non-existent message returns 404."""
        client = relay_client
        worker = _make_relay_worker(existing_records=[])

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            resp = client.post("/api/relay/nonexistent_msg/ack")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_ack_wrong_state_409(self, relay_client):
        """Ack for message not in delivered state returns 409."""
        client = relay_client
        existing_record = _make_delivery_record(
            message_id="msg_002",
            state=DeliveryState.QUEUED,  # Not delivered yet
        )
        worker = _make_relay_worker(existing_records=[existing_record])

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            resp = client.post("/api/relay/msg_002/ack")

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "delivered" in detail.lower()

    def test_ack_already_acked_409(self, relay_client):
        """Ack for already-acked message returns 409."""
        client = relay_client
        existing_record = _make_delivery_record(
            message_id="msg_003",
            state=DeliveryState.ACKED,
        )
        worker = _make_relay_worker(existing_records=[existing_record])

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            resp = client.post("/api/relay/msg_003/ack")

        assert resp.status_code == 409

    def test_ack_calls_worker_ack(self, relay_client):
        """Verify worker.ack is called with correct message_id."""
        client = relay_client
        existing_record = _make_delivery_record(
            message_id="msg_004",
            state=DeliveryState.DELIVERED,
        )
        worker = _make_relay_worker(existing_records=[existing_record])

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            client.post("/api/relay/msg_004/ack")

        worker.ack.assert_called_once_with("msg_004")

    def test_ack_response_format(self, relay_client):
        """Verify ack response format matches expected schema."""
        client = relay_client
        existing_record = _make_delivery_record(
            message_id="msg_005",
            state=DeliveryState.DELIVERED,
        )
        worker = _make_relay_worker(existing_records=[existing_record])

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            resp = client.post("/api/relay/msg_005/ack")

        data = resp.json()
        assert "message_id" in data
        assert "state" in data
        assert data["state"] == "acked"


# -----------------------------------------------------------------------------
# POST /api/relay/{message_id}/nack tests
# -----------------------------------------------------------------------------


class TestNackEndpoint:
    """POST /api/relay/{message_id}/nack - Reject a delivered message."""

    def test_nack_success(self, relay_client):
        """Happy path: nack a delivered message returns 200."""
        client = relay_client
        existing_record = _make_delivery_record(
            message_id="msg_006",
            state=DeliveryState.DELIVERED,
        )
        worker = _make_relay_worker(existing_records=[existing_record])

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            resp = client.post(
                "/api/relay/msg_006/nack",
                json={"reason": "Agent rejected message"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["message_id"] == "msg_006"
        assert data["state"] == "nacked"
        assert data["reason"] == "Agent rejected message"

    def test_nack_with_default_reason(self, relay_client):
        """Nack without providing reason uses default."""
        client = relay_client
        existing_record = _make_delivery_record(
            message_id="msg_007",
            state=DeliveryState.DELIVERED,
        )
        worker = _make_relay_worker(existing_records=[existing_record])

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            resp = client.post("/api/relay/msg_007/nack", json={})

        assert resp.status_code == 200
        assert "Agent rejected delivery" in resp.json()["reason"]

    def test_nack_nonexistent_message_404(self, relay_client):
        """Nack for non-existent message returns 404."""
        client = relay_client
        worker = _make_relay_worker(existing_records=[])

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            resp = client.post(
                "/api/relay/nonexistent_msg/nack",
                json={"reason": "Test reason"},
            )

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_nack_wrong_state_409(self, relay_client):
        """Nack for message not in delivered state returns 409."""
        client = relay_client
        existing_record = _make_delivery_record(
            message_id="msg_008",
            state=DeliveryState.NACKED,  # Already nacked
        )
        worker = _make_relay_worker(existing_records=[existing_record])

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            resp = client.post(
                "/api/relay/msg_008/nack",
                json={"reason": "Should fail"},
            )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "delivered" in detail.lower()

    def test_nack_calls_worker_nack(self, relay_client):
        """Verify worker.nack is called with correct message_id and reason."""
        client = relay_client
        existing_record = _make_delivery_record(
            message_id="msg_009",
            state=DeliveryState.DELIVERED,
        )
        worker = _make_relay_worker(existing_records=[existing_record])

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            client.post(
                "/api/relay/msg_009/nack",
                json={"reason": "Custom rejection reason"},
            )

        worker.nack.assert_called_once_with("msg_009", "Custom rejection reason")

    def test_nack_response_format(self, relay_client):
        """Verify nack response format matches expected schema."""
        client = relay_client
        existing_record = _make_delivery_record(
            message_id="msg_010",
            state=DeliveryState.DELIVERED,
        )
        worker = _make_relay_worker(existing_records=[existing_record])

        with patch(
            "forge_harness.webhook_server.api.relay.get_relay_worker",
            return_value=worker,
        ):
            resp = client.post(
                "/api/relay/msg_010/nack",
                json={"reason": "Test rejection"},
            )

        data = resp.json()
        assert "message_id" in data
        assert "state" in data
        assert "reason" in data
        assert data["state"] == "nacked"


# -----------------------------------------------------------------------------
# Request model validation tests
# -----------------------------------------------------------------------------


class TestRelayDispatchRequestModel:
    """Tests for RelayDispatchRequest Pydantic model."""

    def test_minimal_valid(self):
        """Minimal valid dispatch request."""
        from forge_harness.webhook_server.api.relay import RelayDispatchRequest

        req = RelayDispatchRequest(target_agent="gemini", message="Hello")
        assert req.target_agent == "gemini"
        assert req.message == "Hello"
        assert req.task_id is None
        assert req.source_agent is None
        assert req.priority == "normal"

    def test_full_fields(self):
        """Dispatch request with all fields."""
        from forge_harness.webhook_server.api.relay import RelayDispatchRequest

        req = RelayDispatchRequest(
            target_agent="claude",
            message="Full test",
            task_id="TASK-001",
            source_agent="orchestrator",
            priority="high",
        )
        assert req.task_id == "TASK-001"
        assert req.source_agent == "orchestrator"
        assert req.priority == "high"

    def test_missing_target_agent_raises(self):
        """Missing target_agent raises validation error."""
        from pydantic import ValidationError

        from forge_harness.webhook_server.api.relay import RelayDispatchRequest

        with pytest.raises(ValidationError):
            RelayDispatchRequest(message="No target")

    def test_missing_message_raises(self):
        """Missing message raises validation error."""
        from pydantic import ValidationError

        from forge_harness.webhook_server.api.relay import RelayDispatchRequest

        with pytest.raises(ValidationError):
            RelayDispatchRequest(target_agent="gemini")


class TestRelayNackRequestModel:
    """Tests for RelayNackRequest Pydantic model."""

    def test_default_reason(self):
        """Default reason when not provided."""
        from forge_harness.webhook_server.api.relay import RelayNackRequest

        req = RelayNackRequest()
        assert req.reason == "Agent rejected delivery"

    def test_custom_reason(self):
        """Custom reason provided."""
        from forge_harness.webhook_server.api.relay import RelayNackRequest

        req = RelayNackRequest(reason="Agent busy")
        assert req.reason == "Agent busy"


class TestRelayAckRequestModel:
    """Tests for RelayAckRequest Pydantic model."""

    def test_empty_body_valid(self):
        """Ack request accepts empty body."""
        from forge_harness.webhook_server.api.relay import RelayAckRequest

        req = RelayAckRequest()
        # Model has no required fields (pass statement)
        assert req is not None


# -----------------------------------------------------------------------------
# Auth tests
# -----------------------------------------------------------------------------


class TestAuthRequired:
    """Tests for authentication/authorization requirements."""

    def test_dispatch_without_auth_returns_401(self):
        """Dispatch without auth token returns 401."""
        from forge_harness.webhook_server.api import relay as relay_module

        app = FastAPI()
        app.include_router(relay_module.router)
        # Do NOT bypass auth

        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/api/relay/dispatch",
            json={"target_agent": "gemini", "message": "Test"},
        )

        assert resp.status_code == 401

    def test_ack_without_auth_returns_401(self):
        """Ack without auth token returns 401."""
        from forge_harness.webhook_server.api import relay as relay_module

        app = FastAPI()
        app.include_router(relay_module.router)

        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/api/relay/msg_001/ack")

        assert resp.status_code == 401

    def test_nack_without_auth_returns_401(self):
        """Nack without auth token returns 401."""
        from forge_harness.webhook_server.api import relay as relay_module

        app = FastAPI()
        app.include_router(relay_module.router)

        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/api/relay/msg_001/nack", json={"reason": "Test"})

        assert resp.status_code == 401

    def test_dispatch_with_insufficient_permissions_403(self):
        """Dispatch with auth but missing 'operator' permission returns 403."""
        from forge_harness.webhook_server.api import relay as relay_module
        from forge_harness.webhook_server.core import dependencies

        app = FastAPI()
        app.include_router(relay_module.router)

        # Override with user lacking operator permission
        _user_no_operator = {"sub": "test", "permissions": ["read"]}
        app.dependency_overrides[dependencies.verify_auth] = lambda: _user_no_operator
        app.dependency_overrides[
            dependencies.verify_unified_auth
        ] = lambda: _user_no_operator

        def _require_permission_denied(permission: str):
            async def _check():
                from fastapi import HTTPException

                raise HTTPException(status_code=403, detail="Permission denied")

            return _check

        app.dependency_overrides[
            dependencies.require_permission
        ] = _require_permission_denied

        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/api/relay/dispatch",
            json={"target_agent": "gemini", "message": "Test"},
        )

        assert resp.status_code == 403
