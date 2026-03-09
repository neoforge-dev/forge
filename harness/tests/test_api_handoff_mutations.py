"""Tests for Handoff Mutation API Endpoints

Covers all mutation endpoints in forge_harness/webhook_server/api/handoffs.py:
- POST /api/handoffs                        - Create a new handoff
- POST /api/handoffs/{id}/accept            - Accept a handoff
- POST /api/handoffs/{id}/reject            - Reject a handoff
- POST /api/handoffs/{id}/complete          - Complete a handoff

Test categories per endpoint:
- Success path (happy path)
- Auth required (401 without token)
- 404 when handoff not found
- 400 when handler raises ValueError
- SSE event emission verified
- Audit log emission verified
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Shared mock factories
# ---------------------------------------------------------------------------


def _make_handoff(
    handoff_id: str = "hof-001",
    status: str = "pending",
    from_agent: str = "forge:kimi",
    to_agent: str = "forge:opencode",
    priority: str = "medium",
) -> dict:
    return {
        "id": handoff_id,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "task_description": "Continue backend implementation",
        "files": ["src/api.py", "tests/test_api.py"],
        "priority": priority,
        "status": status,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T01:00:00Z",
    }


def _make_handler(
    create_result: dict | None = None,
    accept_result: dict | None = None,
    reject_result: dict | None = None,
    complete_result: dict | None = None,
) -> Mock:
    handler = Mock()
    handler.create_handoff = AsyncMock(return_value=create_result or _make_handoff())
    handler.list_handoffs = AsyncMock(return_value=[_make_handoff()])
    handler.accept_handoff = AsyncMock(
        return_value=accept_result or _make_handoff(status="accepted")
    )
    handler.reject_handoff = AsyncMock(
        return_value=reject_result or _make_handoff(status="rejected")
    )
    handler.complete_handoff = AsyncMock(
        return_value=complete_result or _make_handoff(status="completed")
    )
    return handler


def _make_event_bus() -> Mock:
    bus = Mock()
    bus.publish = AsyncMock()
    return bus


def _make_audit() -> Mock:
    audit = Mock()
    audit.log = AsyncMock()
    audit.log_handoff_operation = AsyncMock()
    return audit


# ---------------------------------------------------------------------------
# App factory fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def client_with_mocks():
    """Return (TestClient, mocks_dict) with all deps patched and auth bypassed."""
    handler = _make_handler()
    event_bus = _make_event_bus()
    audit = _make_audit()

    from forge_harness.webhook_server.api import handoffs as handoffs_module
    from forge_harness.webhook_server.core import dependencies

    app = FastAPI()
    app.include_router(handoffs_module.router)

    # Bypass auth
    def _noop_auth(request=None):
        return {"sub": "test", "permissions": ["read", "write", "admin"]}

    app.dependency_overrides[dependencies.verify_auth] = _noop_auth

    patches = [
        patch(
            "forge_harness.webhook_server.api.handoffs.get_handoff_handler",
            new=AsyncMock(return_value=handler),
        ),
        patch(
            "forge_harness.webhook_server.api.handoffs.get_event_bus",
            new=AsyncMock(return_value=event_bus),
        ),
        patch(
            "forge_harness.webhook_server.api.handoffs.get_audit_logger",
            return_value=audit,
        ),
    ]

    for p in patches:
        p.start()

    test_client = TestClient(app, raise_server_exceptions=False)

    yield test_client, {"handler": handler, "event_bus": event_bus, "audit": audit}

    for p in patches:
        p.stop()


@pytest.fixture
def auth_required_client():
    """Return TestClient with NO auth bypass — used to test 401 enforcement."""
    from forge_harness.webhook_server.api import handoffs as handoffs_module

    app = FastAPI()
    app.include_router(handoffs_module.router)

    handler = _make_handler()
    event_bus = _make_event_bus()
    audit = _make_audit()

    patches = [
        patch(
            "forge_harness.webhook_server.api.handoffs.get_handoff_handler",
            new=AsyncMock(return_value=handler),
        ),
        patch(
            "forge_harness.webhook_server.api.handoffs.get_event_bus",
            new=AsyncMock(return_value=event_bus),
        ),
        patch(
            "forge_harness.webhook_server.api.handoffs.get_audit_logger",
            return_value=audit,
        ),
    ]
    for p in patches:
        p.start()

    test_client = TestClient(app, raise_server_exceptions=False)

    yield test_client

    for p in patches:
        p.stop()


# ---------------------------------------------------------------------------
# Pydantic model validation tests
# ---------------------------------------------------------------------------


class TestHandoffRequestModel:
    def test_valid_minimal(self):
        from forge_harness.webhook_server.api.handoffs import HandoffRequest

        req = HandoffRequest(
            from_agent="kimi",
            to_agent="opencode",
            task_description="Fix auth bug",
        )
        assert req.from_agent == "kimi"
        assert req.to_agent == "opencode"
        assert req.task_description == "Fix auth bug"
        assert req.files == []
        assert req.priority == "medium"

    def test_valid_with_all_fields(self):
        from forge_harness.webhook_server.api.handoffs import HandoffRequest

        req = HandoffRequest(
            from_agent="kimi",
            to_agent="opencode",
            task_description="Fix auth bug",
            files=["a.py", "b.py"],
            priority="high",
        )
        assert req.files == ["a.py", "b.py"]
        assert req.priority == "high"

    def test_missing_required_fields_raises(self):
        from forge_harness.webhook_server.api.handoffs import HandoffRequest

        with pytest.raises(ValidationError):
            HandoffRequest(from_agent="kimi", to_agent="opencode")  # type: ignore[call-arg]

    def test_missing_from_agent_raises(self):
        from forge_harness.webhook_server.api.handoffs import HandoffRequest

        with pytest.raises(ValidationError):
            HandoffRequest(to_agent="opencode", task_description="task")  # type: ignore[call-arg]


class TestHandoffAcceptRequestModel:
    def test_default_accepting_agent_is_none(self):
        from forge_harness.webhook_server.api.handoffs import HandoffAcceptRequest

        req = HandoffAcceptRequest()
        assert req.accepting_agent is None

    def test_with_accepting_agent(self):
        from forge_harness.webhook_server.api.handoffs import HandoffAcceptRequest

        req = HandoffAcceptRequest(accepting_agent="forge:gemini")
        assert req.accepting_agent == "forge:gemini"


class TestHandoffRejectRequestModel:
    def test_requires_reason(self):
        from forge_harness.webhook_server.api.handoffs import HandoffRejectRequest

        req = HandoffRejectRequest(reason="Not ready yet")
        assert req.reason == "Not ready yet"

    def test_missing_reason_raises(self):
        from forge_harness.webhook_server.api.handoffs import HandoffRejectRequest

        with pytest.raises(ValidationError):
            HandoffRejectRequest()  # type: ignore[call-arg]


class TestHandoffCompleteRequestModel:
    def test_default_notes_is_none(self):
        from forge_harness.webhook_server.api.handoffs import HandoffCompleteRequest

        req = HandoffCompleteRequest()
        assert req.notes is None

    def test_with_notes(self):
        from forge_harness.webhook_server.api.handoffs import HandoffCompleteRequest

        req = HandoffCompleteRequest(notes="Merged successfully")
        assert req.notes == "Merged successfully"


# ---------------------------------------------------------------------------
# POST /api/handoffs — Create handoff
# ---------------------------------------------------------------------------


class TestCreateHandoff:
    def test_create_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        payload = {
            "from_agent": "forge:kimi",
            "to_agent": "forge:opencode",
            "task_description": "Continue auth module",
            "files": ["api/auth.py"],
            "priority": "high",
        }
        resp = client.post("/api/handoffs", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["id"] == "hof-001"

    def test_create_calls_handler_with_correct_args(self, client_with_mocks):
        client, mocks = client_with_mocks
        payload = {
            "from_agent": "forge:kimi",
            "to_agent": "forge:opencode",
            "task_description": "Write tests",
            "files": ["tests/test_auth.py"],
            "priority": "low",
        }
        client.post("/api/handoffs", json=payload)
        mocks["handler"].create_handoff.assert_called_once_with(
            from_agent="forge:kimi",
            to_agent="forge:opencode",
            task_description="Write tests",
            files=["tests/test_auth.py"],
            priority="low",
        )

    def test_create_emits_sse_event(self, client_with_mocks):
        client, mocks = client_with_mocks
        payload = {
            "from_agent": "forge:kimi",
            "to_agent": "forge:opencode",
            "task_description": "Task",
        }
        client.post("/api/handoffs", json=payload)
        mocks["event_bus"].publish.assert_called_once()
        call_args = mocks["event_bus"].publish.call_args
        assert call_args[0][0] == "handoff.created"

    def test_create_emits_audit_log(self, client_with_mocks):
        client, mocks = client_with_mocks
        payload = {
            "from_agent": "forge:kimi",
            "to_agent": "forge:opencode",
            "task_description": "Task",
        }
        client.post("/api/handoffs", json=payload)
        mocks["audit"].log_handoff_operation.assert_called_once()

    def test_create_returns_400_on_handler_exception(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].create_handoff = AsyncMock(
            side_effect=Exception("Redis unavailable")
        )
        payload = {
            "from_agent": "forge:kimi",
            "to_agent": "forge:opencode",
            "task_description": "Task",
        }
        resp = client.post("/api/handoffs", json=payload)
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert data["error"]["code"] == "CREATE_FAILED"

    def test_create_missing_required_fields_returns_422(self, client_with_mocks):
        client, _ = client_with_mocks
        # Missing task_description
        resp = client.post(
            "/api/handoffs",
            json={"from_agent": "kimi", "to_agent": "opencode"},
        )
        assert resp.status_code == 422

    def test_create_uses_default_priority_medium(self, client_with_mocks):
        client, mocks = client_with_mocks
        payload = {
            "from_agent": "forge:kimi",
            "to_agent": "forge:opencode",
            "task_description": "Task",
        }
        client.post("/api/handoffs", json=payload)
        call_kwargs = mocks["handler"].create_handoff.call_args[1]
        assert call_kwargs["priority"] == "medium"

    def test_create_uses_default_empty_files(self, client_with_mocks):
        client, mocks = client_with_mocks
        payload = {
            "from_agent": "forge:kimi",
            "to_agent": "forge:opencode",
            "task_description": "Task",
        }
        client.post("/api/handoffs", json=payload)
        call_kwargs = mocks["handler"].create_handoff.call_args[1]
        assert call_kwargs["files"] == []

    def test_create_auth_required(self, auth_required_client):
        resp = auth_required_client.post(
            "/api/handoffs",
            json={
                "from_agent": "forge:kimi",
                "to_agent": "forge:opencode",
                "task_description": "Task",
            },
        )
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# POST /api/handoffs/{id}/accept — Accept handoff
# ---------------------------------------------------------------------------


class TestAcceptHandoff:
    def test_accept_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.post("/api/handoffs/hof-001/accept", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["status"] == "accepted"

    def test_accept_with_accepting_agent(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.post(
            "/api/handoffs/hof-001/accept",
            json={"accepting_agent": "forge:gemini"},
        )
        assert resp.status_code == 200
        mocks["handler"].accept_handoff.assert_called_once_with(
            "hof-001", "forge:gemini"
        )

    def test_accept_without_body_uses_none_agent(self, client_with_mocks):
        client, mocks = client_with_mocks
        # TestClient sends no body at all
        resp = client.post("/api/handoffs/hof-001/accept")
        assert resp.status_code == 200
        mocks["handler"].accept_handoff.assert_called_once_with("hof-001", None)

    def test_accept_emits_sse_event(self, client_with_mocks):
        client, mocks = client_with_mocks
        client.post("/api/handoffs/hof-001/accept", json={})
        mocks["event_bus"].publish.assert_called_once()
        assert mocks["event_bus"].publish.call_args[0][0] == "handoff.accepted"

    def test_accept_returns_404_when_handler_returns_none(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].accept_handoff = AsyncMock(return_value=None)
        resp = client.post("/api/handoffs/nonexistent/accept", json={})
        assert resp.status_code == 404

    def test_accept_returns_400_on_value_error(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].accept_handoff = AsyncMock(
            side_effect=ValueError("Handoff already accepted")
        )
        resp = client.post("/api/handoffs/hof-001/accept", json={})
        assert resp.status_code == 400
        assert "already accepted" in resp.json()["detail"]

    def test_accept_auth_required(self, auth_required_client):
        resp = auth_required_client.post("/api/handoffs/hof-001/accept", json={})
        assert resp.status_code in (401, 403, 422)

    def test_accept_no_sse_when_event_bus_is_none(self, client_with_mocks):
        client, mocks = client_with_mocks
        # Patch get_event_bus to return None
        with patch(
            "forge_harness.webhook_server.api.handoffs.get_event_bus",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post("/api/handoffs/hof-001/accept", json={})
        assert resp.status_code == 200
        # Event bus publish should NOT have been called since bus is None
        mocks["event_bus"].publish.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/handoffs/{id}/reject — Reject handoff
# ---------------------------------------------------------------------------


class TestRejectHandoff:
    def test_reject_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.post(
            "/api/handoffs/hof-001/reject", json={"reason": "Not ready"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["status"] == "rejected"

    def test_reject_calls_handler_with_reason(self, client_with_mocks):
        client, mocks = client_with_mocks
        client.post(
            "/api/handoffs/hof-001/reject",
            json={"reason": "Blocked by dependency"},
        )
        mocks["handler"].reject_handoff.assert_called_once_with(
            "hof-001", "Blocked by dependency"
        )

    def test_reject_emits_sse_event(self, client_with_mocks):
        client, mocks = client_with_mocks
        client.post("/api/handoffs/hof-001/reject", json={"reason": "Too risky"})
        mocks["event_bus"].publish.assert_called_once()
        assert mocks["event_bus"].publish.call_args[0][0] == "handoff.rejected"

    def test_reject_missing_reason_returns_422(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post("/api/handoffs/hof-001/reject", json={})
        assert resp.status_code == 422

    def test_reject_returns_404_when_handler_returns_none(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].reject_handoff = AsyncMock(return_value=None)
        resp = client.post(
            "/api/handoffs/nonexistent/reject", json={"reason": "Gone"}
        )
        assert resp.status_code == 404

    def test_reject_returns_400_on_value_error(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].reject_handoff = AsyncMock(
            side_effect=ValueError("Handoff already completed")
        )
        resp = client.post(
            "/api/handoffs/hof-001/reject", json={"reason": "Too late"}
        )
        assert resp.status_code == 400
        assert "already completed" in resp.json()["detail"]

    def test_reject_auth_required(self, auth_required_client):
        resp = auth_required_client.post(
            "/api/handoffs/hof-001/reject", json={"reason": "Test"}
        )
        assert resp.status_code in (401, 403, 422)

    def test_reject_no_sse_when_event_bus_is_none(self, client_with_mocks):
        client, mocks = client_with_mocks
        with patch(
            "forge_harness.webhook_server.api.handoffs.get_event_bus",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post(
                "/api/handoffs/hof-001/reject", json={"reason": "No bus"}
            )
        assert resp.status_code == 200
        mocks["event_bus"].publish.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/handoffs/{id}/complete — Complete handoff
# ---------------------------------------------------------------------------


class TestCompleteHandoff:
    def test_complete_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.post("/api/handoffs/hof-001/complete", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["status"] == "completed"

    def test_complete_with_notes(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.post(
            "/api/handoffs/hof-001/complete",
            json={"notes": "All tests passing"},
        )
        assert resp.status_code == 200
        mocks["handler"].complete_handoff.assert_called_once_with(
            "hof-001", "All tests passing"
        )

    def test_complete_without_body_uses_none_notes(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.post("/api/handoffs/hof-001/complete")
        assert resp.status_code == 200
        mocks["handler"].complete_handoff.assert_called_once_with("hof-001", None)

    def test_complete_emits_sse_event(self, client_with_mocks):
        client, mocks = client_with_mocks
        client.post("/api/handoffs/hof-001/complete", json={})
        mocks["event_bus"].publish.assert_called_once()
        assert mocks["event_bus"].publish.call_args[0][0] == "handoff.completed"

    def test_complete_returns_404_when_handler_returns_none(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].complete_handoff = AsyncMock(return_value=None)
        resp = client.post("/api/handoffs/nonexistent/complete", json={})
        assert resp.status_code == 404

    def test_complete_returns_400_on_value_error(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].complete_handoff = AsyncMock(
            side_effect=ValueError("Handoff not in accepted state")
        )
        resp = client.post("/api/handoffs/hof-001/complete", json={})
        assert resp.status_code == 400
        assert "not in accepted state" in resp.json()["detail"]

    def test_complete_auth_required(self, auth_required_client):
        resp = auth_required_client.post("/api/handoffs/hof-001/complete", json={})
        assert resp.status_code in (401, 403, 422)

    def test_complete_no_sse_when_event_bus_is_none(self, client_with_mocks):
        client, mocks = client_with_mocks
        with patch(
            "forge_harness.webhook_server.api.handoffs.get_event_bus",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post("/api/handoffs/hof-001/complete", json={})
        assert resp.status_code == 200
        mocks["event_bus"].publish.assert_not_called()


# ---------------------------------------------------------------------------
# Response shape and api_response format tests
# ---------------------------------------------------------------------------


class TestApiResponseFormat:
    def test_success_response_shape(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post("/api/handoffs/hof-001/complete", json={})
        data = resp.json()
        assert "success" in data
        assert "data" in data
        assert "error" in data
        assert "timestamp" in data

    def test_error_response_shape(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].create_handoff = AsyncMock(side_effect=Exception("boom"))
        resp = client.post(
            "/api/handoffs",
            json={
                "from_agent": "kimi",
                "to_agent": "opencode",
                "task_description": "task",
            },
        )
        data = resp.json()
        assert data["success"] is False
        assert data["data"] is None
        assert data["error"]["code"] == "CREATE_FAILED"
        assert "timestamp" in data

    def test_complete_response_contains_handoff_data(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post("/api/handoffs/hof-001/complete", json={})
        data = resp.json()
        assert data["data"]["id"] == "hof-001"
        assert data["data"]["status"] == "completed"
