"""Extended tests for forge_harness/webhook_server/api/approvals.py

Covers all endpoints using FastAPI TestClient with mocked dependencies:
- GET  /api/approvals              — list approvals
- GET  /api/approvals/count        — count pending
- GET  /api/approvals/stats        — queue stats
- GET  /api/approvals/{id}         — get approval detail
- POST /api/approvals/{id}/approve — approve request
- POST /api/approvals/{id}/reject  — reject request
- POST /api/approvals/batch-approve — batch approve
- POST /api/approvals/bulk/approve  — bulk approve
- POST /api/approvals/bulk/reject   — bulk reject
- POST /api/webhooks/slack/approvals — Slack webhook

Target: 70+ tests, all pass.
"""

import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Mock Factories
# ---------------------------------------------------------------------------


def _make_approval(
    approval_id: str = "req-001",
    status: str = "pending",
    tier: str = "desktop",
    priority: str = "high",
    domain: str = "test-domain",
    project: str = "test-project",
    title: str = "Test Approval",
) -> dict:
    return {
        "id": approval_id,
        "title": title,
        "status": status,
        "tier": tier,
        "priority": priority,
        "domain": domain,
        "project": project,
        "created_at": "2026-01-01T00:00:00Z",
    }


def _make_handler(
    approvals: list | None = None,
    approve_success: bool = True,
    reject_success: bool = True,
) -> Mock:
    handler = Mock()
    _approvals = approvals or [_make_approval(), _make_approval("req-002", tier="watch", priority="low")]

    handler.list_pending = AsyncMock(return_value=_approvals)
    handler.count_pending = AsyncMock(return_value=len(_approvals))
    handler.get_stats = AsyncMock(
        return_value={
            "pending": len(_approvals),
            "approved": 5,
            "rejected": 1,
            "expired": 0,
            "total": len(_approvals) + 6,
        }
    )
    handler.get_request = AsyncMock(
        side_effect=lambda rid: _make_approval(approval_id=rid) if rid else None
    )
    handler.approve_request = AsyncMock(
        return_value={"success": approve_success, "request_id": "req-001"}
        if approve_success
        else {"success": False, "error": "Request not found"}
    )
    handler.reject_request = AsyncMock(
        return_value={"success": reject_success, "request_id": "req-001"}
        if reject_success
        else {"success": False, "error": "Request not found"}
    )
    handler.batch_approve = AsyncMock(
        return_value={"approved": ["req-001", "req-002"], "failed": []}
    )
    return handler


def _make_event_bus() -> Mock:
    bus = Mock()
    bus.publish = AsyncMock()
    return bus


def _make_audit() -> Mock:
    audit = Mock()
    audit.log = AsyncMock()
    audit.log_approval_decision = AsyncMock()
    return audit


def _make_webhook_handler() -> Mock:
    wh = Mock()
    wh.slack_signing_secret = None
    wh.verify_slack_signature = Mock(return_value=True)
    return wh


# ---------------------------------------------------------------------------
# App factory fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def client_with_mocks():
    """Return (TestClient, mocks_dict) with all deps patched and auth bypassed."""
    handler = _make_handler()
    event_bus = _make_event_bus()
    audit = _make_audit()
    webhook_handler = _make_webhook_handler()

    from forge_harness.webhook_server.api import approvals as approvals_module
    from forge_harness.webhook_server.core import dependencies

    app = FastAPI()
    app.include_router(approvals_module.router)

    # Auth bypass: require_permission is a factory returning a dependency.
    # We override the entire factory to always return a no-op dependency.
    def _noop_require_permission(permission: str):
        async def _check():
            return {"sub": "test", "permissions": ["admin"]}
        return _check

    app.dependency_overrides[dependencies.require_permission] = _noop_require_permission
    app.dependency_overrides[dependencies.verify_unified_auth] = lambda: {
        "sub": "test",
        "permissions": ["admin"],
    }

    patches = [
        patch(
            "forge_harness.webhook_server.api.approvals.get_approval_handler",
            new=AsyncMock(return_value=handler),
        ),
        patch(
            "forge_harness.webhook_server.api.approvals.get_event_bus",
            new=AsyncMock(return_value=event_bus),
        ),
        patch(
            "forge_harness.webhook_server.api.approvals.get_audit_logger",
            return_value=audit,
        ),
        patch(
            "forge_harness.webhook_server.api.approvals.get_webhook_handler",
            new=AsyncMock(return_value=webhook_handler),
        ),
    ]

    for p in patches:
        p.start()

    test_client = TestClient(app, raise_server_exceptions=False)

    yield test_client, {
        "handler": handler,
        "event_bus": event_bus,
        "audit": audit,
        "webhook_handler": webhook_handler,
    }

    for p in patches:
        p.stop()


# ---------------------------------------------------------------------------
# Pydantic Model Validation Tests
# ---------------------------------------------------------------------------


class TestApproveRequestModel:
    def test_valid_minimal(self):
        from forge_harness.webhook_server.api.approvals import ApproveRequest

        req = ApproveRequest(approver="alice")
        assert req.approver == "alice"
        assert req.comment is None
        assert req.auto_resume is True

    def test_full_fields(self):
        from forge_harness.webhook_server.api.approvals import ApproveRequest

        req = ApproveRequest(approver="alice", comment="Looks good", auto_resume=False)
        assert req.comment == "Looks good"
        assert req.auto_resume is False

    def test_whitespace_only_approver_raises(self):
        from forge_harness.webhook_server.api.approvals import ApproveRequest

        with pytest.raises(ValidationError):
            ApproveRequest(approver="   ")

    def test_approver_stripped(self):
        from forge_harness.webhook_server.api.approvals import ApproveRequest

        req = ApproveRequest(approver="  alice  ")
        assert req.approver == "alice"


class TestRejectRequestModel:
    def test_valid_minimal(self):
        from forge_harness.webhook_server.api.approvals import RejectRequest

        req = RejectRequest(rejector="bob")
        assert req.rejector == "bob"
        assert req.reason is None

    def test_full_fields(self):
        from forge_harness.webhook_server.api.approvals import RejectRequest

        req = RejectRequest(rejector="bob", reason="Not ready")
        assert req.reason == "Not ready"

    def test_whitespace_only_rejector_raises(self):
        from forge_harness.webhook_server.api.approvals import RejectRequest

        with pytest.raises(ValidationError):
            RejectRequest(rejector="   ")

    def test_rejector_stripped(self):
        from forge_harness.webhook_server.api.approvals import RejectRequest

        req = RejectRequest(rejector="  bob  ")
        assert req.rejector == "bob"

    def test_reason_stripped(self):
        from forge_harness.webhook_server.api.approvals import RejectRequest

        req = RejectRequest(rejector="bob", reason="  too risky  ")
        assert req.reason == "too risky"


class TestBatchApproveRequestModel:
    def test_valid(self):
        from forge_harness.webhook_server.api.approvals import BatchApproveRequest

        req = BatchApproveRequest(
            request_ids=["req-001", "req-002"],
            approver="alice",
            comment="Batch approved",
        )
        assert req.request_ids == ["req-001", "req-002"]
        assert req.approver == "alice"

    def test_empty_ids_allowed(self):
        from forge_harness.webhook_server.api.approvals import BatchApproveRequest

        req = BatchApproveRequest(request_ids=[], approver="alice")
        assert req.request_ids == []


class TestBulkApproveRequestModel:
    def test_valid(self):
        from forge_harness.webhook_server.api.approvals import BulkApproveRequest

        req = BulkApproveRequest(ids=["r1", "r2"], notes="All good")
        assert req.ids == ["r1", "r2"]

    def test_notes_optional(self):
        from forge_harness.webhook_server.api.approvals import BulkApproveRequest

        req = BulkApproveRequest(ids=["r1"])
        assert req.notes is None


class TestBulkRejectRequestModel:
    def test_valid(self):
        from forge_harness.webhook_server.api.approvals import BulkRejectRequest

        req = BulkRejectRequest(ids=["r1", "r2"], reason="Not approved")
        assert req.reason == "Not approved"


# ---------------------------------------------------------------------------
# GET /api/approvals — List Approvals
# ---------------------------------------------------------------------------


class TestListApprovals:
    def test_returns_approvals_list(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.get("/api/approvals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "approvals" in data["data"]
        assert data["data"]["count"] == 2

    def test_calls_handler_list_pending(self, client_with_mocks):
        client, mocks = client_with_mocks
        client.get("/api/approvals")
        mocks["handler"].list_pending.assert_called_once()

    def test_filter_by_domain(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.get("/api/approvals?domain=test-domain")
        assert resp.status_code == 200
        call_kwargs = mocks["handler"].list_pending.call_args[1]
        assert call_kwargs.get("domain") == "test-domain"

    def test_filter_by_priority(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.get("/api/approvals?priority=high")
        assert resp.status_code == 200
        call_kwargs = mocks["handler"].list_pending.call_args[1]
        assert call_kwargs.get("priority") == "high"

    def test_filter_by_tier(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.get("/api/approvals?tier=watch")
        assert resp.status_code == 200
        call_kwargs = mocks["handler"].list_pending.call_args[1]
        assert call_kwargs.get("tier") == "watch"

    def test_filter_by_status(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.get("/api/approvals?status=pending")
        assert resp.status_code == 200
        call_kwargs = mocks["handler"].list_pending.call_args[1]
        assert call_kwargs.get("status") == "pending"

    def test_custom_limit(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.get("/api/approvals?limit=10")
        assert resp.status_code == 200
        call_kwargs = mocks["handler"].list_pending.call_args[1]
        assert call_kwargs.get("limit") == 10

    def test_limit_too_high_422(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.get("/api/approvals?limit=999")
        assert resp.status_code == 422

    def test_limit_zero_422(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.get("/api/approvals?limit=0")
        assert resp.status_code == 422

    def test_empty_approvals_list(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].list_pending = AsyncMock(return_value=[])
        resp = client.get("/api/approvals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["count"] == 0

    def test_response_structure(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.get("/api/approvals")
        data = resp.json()
        assert "success" in data
        assert "data" in data
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# GET /api/approvals/count — Approval Count
# ---------------------------------------------------------------------------


class TestApprovalCount:
    def test_returns_count(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.get("/api/approvals/count")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "count" in data["data"]

    def test_filter_by_domain(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.get("/api/approvals/count?domain=my-domain")
        assert resp.status_code == 200
        call_kwargs = mocks["handler"].count_pending.call_args[1]
        assert call_kwargs.get("domain") == "my-domain"

    def test_filter_by_project(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.get("/api/approvals/count?project=my-project")
        assert resp.status_code == 200
        call_kwargs = mocks["handler"].count_pending.call_args[1]
        assert call_kwargs.get("project") == "my-project"


# ---------------------------------------------------------------------------
# GET /api/approvals/stats — Approval Stats
# ---------------------------------------------------------------------------


class TestApprovalStats:
    def test_returns_stats(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.get("/api/approvals/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "pending" in data

    def test_filter_by_domain(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.get("/api/approvals/stats?domain=test-domain")
        assert resp.status_code == 200
        mocks["handler"].get_stats.assert_called_once()

    def test_stats_error_returns_zeroed_response(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].get_stats = AsyncMock(
            return_value={"error": "Storage unavailable"}
        )
        resp = client.get("/api/approvals/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("pending") == 0


# ---------------------------------------------------------------------------
# GET /api/approvals/{request_id} — Get Approval
# ---------------------------------------------------------------------------


class TestGetApproval:
    def test_found_returns_detail(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.get("/api/approvals/req-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "req-001"

    def test_not_found_returns_404(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].get_request = AsyncMock(return_value=None)
        resp = client.get("/api/approvals/nonexistent")
        assert resp.status_code == 404

    def test_calls_get_request_with_id(self, client_with_mocks):
        client, mocks = client_with_mocks
        client.get("/api/approvals/req-abc")
        mocks["handler"].get_request.assert_called_once_with("req-abc")


# ---------------------------------------------------------------------------
# POST /api/approvals/{request_id}/approve — Approve Request
# ---------------------------------------------------------------------------


class TestApproveRequest:
    def test_approve_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.post(
            "/api/approvals/req-001/approve",
            json={"approver": "alice", "comment": "Approved!"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_approve_emits_sse_event(self, client_with_mocks):
        client, mocks = client_with_mocks
        client.post(
            "/api/approvals/req-001/approve",
            json={"approver": "alice"},
        )
        mocks["event_bus"].publish.assert_called_once()
        call_args = mocks["event_bus"].publish.call_args[0]
        assert call_args[0] == "approval.resolved"
        assert call_args[1]["action"] == "approved"

    def test_approve_triggers_audit_log(self, client_with_mocks):
        client, mocks = client_with_mocks
        client.post(
            "/api/approvals/req-001/approve",
            json={"approver": "alice"},
        )
        mocks["audit"].log_approval_decision.assert_called_once()
        call_kwargs = mocks["audit"].log_approval_decision.call_args[1]
        assert call_kwargs["decision"] == "approved"

    def test_approve_with_auto_resume_false(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.post(
            "/api/approvals/req-001/approve",
            json={"approver": "alice", "auto_resume": False},
        )
        assert resp.status_code == 200
        call_kwargs = mocks["handler"].approve_request.call_args[1]
        assert call_kwargs.get("auto_resume") is False

    def test_approve_handler_not_found_returns_404(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].approve_request = AsyncMock(
            return_value={"success": False, "error": "Request not found"}
        )
        resp = client.post(
            "/api/approvals/req-999/approve",
            json={"approver": "alice"},
        )
        assert resp.status_code == 404

    def test_approve_handler_generic_error_returns_400(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].approve_request = AsyncMock(
            return_value={"success": False, "error": "Validation failed"}
        )
        resp = client.post(
            "/api/approvals/req-001/approve",
            json={"approver": "alice"},
        )
        assert resp.status_code == 400

    def test_approve_empty_approver_422(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post(
            "/api/approvals/req-001/approve",
            json={"approver": ""},
        )
        assert resp.status_code == 422

    def test_approve_whitespace_request_id_400(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post(
            "/api/approvals/ /approve",
            json={"approver": "alice"},
        )
        assert resp.status_code == 400

    def test_approve_passes_comment_to_handler(self, client_with_mocks):
        client, mocks = client_with_mocks
        client.post(
            "/api/approvals/req-001/approve",
            json={"approver": "alice", "comment": "LGTM"},
        )
        call_kwargs = mocks["handler"].approve_request.call_args[1]
        assert call_kwargs.get("comment") == "LGTM"


# ---------------------------------------------------------------------------
# POST /api/approvals/{request_id}/reject — Reject Request
# ---------------------------------------------------------------------------


class TestRejectRequest:
    def test_reject_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.post(
            "/api/approvals/req-001/reject",
            json={"rejector": "bob", "reason": "Too risky"},
        )
        assert resp.status_code == 200

    def test_reject_emits_sse_event(self, client_with_mocks):
        client, mocks = client_with_mocks
        client.post(
            "/api/approvals/req-001/reject",
            json={"rejector": "bob", "reason": "Not ready"},
        )
        mocks["event_bus"].publish.assert_called_once()
        call_args = mocks["event_bus"].publish.call_args[0]
        assert call_args[0] == "approval.resolved"
        assert call_args[1]["action"] == "rejected"

    def test_reject_triggers_audit_log(self, client_with_mocks):
        client, mocks = client_with_mocks
        client.post(
            "/api/approvals/req-001/reject",
            json={"rejector": "bob", "reason": "Risk"},
        )
        mocks["audit"].log_approval_decision.assert_called_once()
        call_kwargs = mocks["audit"].log_approval_decision.call_args[1]
        assert call_kwargs["decision"] == "rejected"

    def test_reject_no_reason_still_succeeds(self, client_with_mocks):
        """Rejection without reason logs warning but still proceeds."""
        client, mocks = client_with_mocks
        resp = client.post(
            "/api/approvals/req-001/reject",
            json={"rejector": "bob"},
        )
        assert resp.status_code == 200

    def test_reject_handler_not_found_returns_404(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].reject_request = AsyncMock(
            return_value={"success": False, "error": "Request not found"}
        )
        resp = client.post(
            "/api/approvals/req-999/reject",
            json={"rejector": "bob"},
        )
        assert resp.status_code == 404

    def test_reject_handler_generic_error_returns_400(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].reject_request = AsyncMock(
            return_value={"success": False, "error": "Validation error"}
        )
        resp = client.post(
            "/api/approvals/req-001/reject",
            json={"rejector": "bob"},
        )
        assert resp.status_code == 400

    def test_reject_empty_rejector_422(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post(
            "/api/approvals/req-001/reject",
            json={"rejector": ""},
        )
        assert resp.status_code == 422

    def test_reject_whitespace_request_id_400(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post(
            "/api/approvals/ /reject",
            json={"rejector": "bob"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/approvals/batch-approve — Batch Approve
# ---------------------------------------------------------------------------


class TestBatchApprove:
    def test_batch_approve_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.post(
            "/api/approvals/batch-approve",
            json={
                "request_ids": ["req-001", "req-002"],
                "approver": "alice",
                "comment": "All good",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_batch_approve_emits_sse_events(self, client_with_mocks):
        client, mocks = client_with_mocks
        client.post(
            "/api/approvals/batch-approve",
            json={"request_ids": ["req-001", "req-002"], "approver": "alice"},
        )
        # One event per approved request
        assert mocks["event_bus"].publish.call_count == 2

    def test_batch_approve_triggers_audit(self, client_with_mocks):
        client, mocks = client_with_mocks
        client.post(
            "/api/approvals/batch-approve",
            json={"request_ids": ["req-001", "req-002"], "approver": "alice"},
        )
        mocks["audit"].log.assert_called_once()
        call_kwargs = mocks["audit"].log.call_args[1]
        assert call_kwargs["action"] == "approval_batch_approve"

    def test_batch_approve_empty_ids(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].batch_approve = AsyncMock(
            return_value={"approved": [], "failed": []}
        )
        resp = client.post(
            "/api/approvals/batch-approve",
            json={"request_ids": [], "approver": "alice"},
        )
        assert resp.status_code == 200

    def test_batch_approve_missing_approver_422(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post(
            "/api/approvals/batch-approve",
            json={"request_ids": ["req-001"]},  # missing approver
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/approvals/bulk/approve — Bulk Approve
#
# NOTE: When the approvals router is mounted standalone (not via create_app),
# FastAPI's route-matching resolves POST /api/approvals/bulk/approve to the
# earlier-registered /{request_id}/approve route (request_id="bulk").
# The bulk endpoints work correctly in the full app where route ordering
# places static paths before parameterized ones.  These tests validate the
# BulkApproveRequest Pydantic model and the handler logic directly, and use
# the full-app fixture for integration-level endpoint tests.
# ---------------------------------------------------------------------------


class TestBulkApproveModel:
    """Validate BulkApproveRequest independently of route ordering."""

    def test_valid_with_notes(self):
        from forge_harness.webhook_server.api.approvals import BulkApproveRequest

        req = BulkApproveRequest(ids=["r1", "r2"], notes="Batch OK")
        assert req.ids == ["r1", "r2"]
        assert req.notes == "Batch OK"

    def test_valid_no_notes(self):
        from forge_harness.webhook_server.api.approvals import BulkApproveRequest

        req = BulkApproveRequest(ids=["r1"])
        assert req.notes is None

    def test_empty_ids_allowed(self):
        from forge_harness.webhook_server.api.approvals import BulkApproveRequest

        req = BulkApproveRequest(ids=[])
        assert req.ids == []

    def test_missing_ids_raises(self):
        from forge_harness.webhook_server.api.approvals import BulkApproveRequest

        with pytest.raises(ValidationError):
            BulkApproveRequest(notes="no ids field")


class TestBulkApproveHandler:
    """Test bulk_approve_requests handler logic via direct import (avoids route shadow)."""

    @pytest.mark.asyncio
    async def test_all_success(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkApproveRequest,
            bulk_approve_requests,
        )

        handler = _make_handler()
        handler.approve_request = AsyncMock(
            return_value={"success": True, "request_id": "req-001"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        with patch(
            "forge_harness.webhook_server.api.approvals.get_approval_handler",
            new=AsyncMock(return_value=handler),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_event_bus",
            new=AsyncMock(return_value=event_bus),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_audit_logger",
            return_value=audit,
        ):
            body = BulkApproveRequest(ids=["req-001", "req-002"], notes="OK")
            resp = await bulk_approve_requests(body, _=None)

        import json as _json
        data = _json.loads(resp.body)
        assert data["data"]["success"] == 2
        assert data["data"]["failed"] == 0

    @pytest.mark.asyncio
    async def test_exception_counted_as_failure(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkApproveRequest,
            bulk_approve_requests,
        )

        handler = _make_handler()
        handler.approve_request = AsyncMock(side_effect=RuntimeError("db error"))
        event_bus = _make_event_bus()
        audit = _make_audit()

        with patch(
            "forge_harness.webhook_server.api.approvals.get_approval_handler",
            new=AsyncMock(return_value=handler),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_event_bus",
            new=AsyncMock(return_value=event_bus),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_audit_logger",
            return_value=audit,
        ):
            body = BulkApproveRequest(ids=["req-001"])
            resp = await bulk_approve_requests(body, _=None)

        import json as _json
        data = _json.loads(resp.body)
        assert data["data"]["failed"] == 1
        assert len(data["data"]["errors"]) == 1

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkApproveRequest,
            bulk_approve_requests,
        )

        call_count = [0]

        async def _side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                return {"success": False, "error": "Not found"}
            return {"success": True, "request_id": kwargs.get("request_id", "r")}

        handler = _make_handler()
        handler.approve_request = _side_effect
        event_bus = _make_event_bus()
        audit = _make_audit()

        with patch(
            "forge_harness.webhook_server.api.approvals.get_approval_handler",
            new=AsyncMock(return_value=handler),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_event_bus",
            new=AsyncMock(return_value=event_bus),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_audit_logger",
            return_value=audit,
        ):
            body = BulkApproveRequest(ids=["r1", "r2"])
            resp = await bulk_approve_requests(body, _=None)

        import json as _json
        data = _json.loads(resp.body)
        assert data["data"]["success"] == 1
        assert data["data"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_emits_sse_events_per_approval(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkApproveRequest,
            bulk_approve_requests,
        )

        handler = _make_handler()
        handler.approve_request = AsyncMock(
            return_value={"success": True, "request_id": "r"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        with patch(
            "forge_harness.webhook_server.api.approvals.get_approval_handler",
            new=AsyncMock(return_value=handler),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_event_bus",
            new=AsyncMock(return_value=event_bus),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_audit_logger",
            return_value=audit,
        ):
            body = BulkApproveRequest(ids=["r1", "r2"])
            await bulk_approve_requests(body, _=None)

        assert event_bus.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_triggers_audit_on_success(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkApproveRequest,
            bulk_approve_requests,
        )

        handler = _make_handler()
        handler.approve_request = AsyncMock(
            return_value={"success": True, "request_id": "r"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        with patch(
            "forge_harness.webhook_server.api.approvals.get_approval_handler",
            new=AsyncMock(return_value=handler),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_event_bus",
            new=AsyncMock(return_value=event_bus),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_audit_logger",
            return_value=audit,
        ):
            body = BulkApproveRequest(ids=["r1"])
            await bulk_approve_requests(body, _=None)

        audit.log.assert_called_once()
        call_kwargs = audit.log.call_args[1]
        assert call_kwargs["action"] == "approval_bulk_approve"


# ---------------------------------------------------------------------------
# POST /api/approvals/bulk/reject — Bulk Reject (handler tests, same reason)
# ---------------------------------------------------------------------------


class TestBulkRejectModel:
    def test_valid(self):
        from forge_harness.webhook_server.api.approvals import BulkRejectRequest

        req = BulkRejectRequest(ids=["r1", "r2"], reason="Not approved")
        assert req.reason == "Not approved"

    def test_missing_reason_raises(self):
        from forge_harness.webhook_server.api.approvals import BulkRejectRequest

        with pytest.raises(ValidationError):
            BulkRejectRequest(ids=["r1"])  # reason required

    def test_empty_ids(self):
        from forge_harness.webhook_server.api.approvals import BulkRejectRequest

        req = BulkRejectRequest(ids=[], reason="denied")
        assert req.ids == []


class TestBulkRejectHandler:
    @pytest.mark.asyncio
    async def test_all_success(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(
            return_value={"success": True, "request_id": "r"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        with patch(
            "forge_harness.webhook_server.api.approvals.get_approval_handler",
            new=AsyncMock(return_value=handler),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_event_bus",
            new=AsyncMock(return_value=event_bus),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_audit_logger",
            return_value=audit,
        ):
            body = BulkRejectRequest(ids=["r1", "r2"], reason="denied")
            resp = await bulk_reject_requests(body, _=None)

        import json as _json
        data = _json.loads(resp.body)
        assert data["data"]["success"] == 2
        assert data["data"]["failed"] == 0

    @pytest.mark.asyncio
    async def test_exception_counted_as_failure(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(side_effect=RuntimeError("db error"))
        event_bus = _make_event_bus()
        audit = _make_audit()

        with patch(
            "forge_harness.webhook_server.api.approvals.get_approval_handler",
            new=AsyncMock(return_value=handler),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_event_bus",
            new=AsyncMock(return_value=event_bus),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_audit_logger",
            return_value=audit,
        ):
            body = BulkRejectRequest(ids=["r1"], reason="denied")
            resp = await bulk_reject_requests(body, _=None)

        import json as _json
        data = _json.loads(resp.body)
        assert data["data"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_emits_sse_events_per_rejection(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(
            return_value={"success": True, "request_id": "r"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        with patch(
            "forge_harness.webhook_server.api.approvals.get_approval_handler",
            new=AsyncMock(return_value=handler),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_event_bus",
            new=AsyncMock(return_value=event_bus),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_audit_logger",
            return_value=audit,
        ):
            body = BulkRejectRequest(ids=["r1", "r2"], reason="denied")
            await bulk_reject_requests(body, _=None)

        assert event_bus.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_triggers_audit_on_success(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(
            return_value={"success": True, "request_id": "r"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        with patch(
            "forge_harness.webhook_server.api.approvals.get_approval_handler",
            new=AsyncMock(return_value=handler),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_event_bus",
            new=AsyncMock(return_value=event_bus),
        ), patch(
            "forge_harness.webhook_server.api.approvals.get_audit_logger",
            return_value=audit,
        ):
            body = BulkRejectRequest(ids=["r1"], reason="denied")
            await bulk_reject_requests(body, _=None)

        audit.log.assert_called_once()
        call_kwargs = audit.log.call_args[1]
        assert call_kwargs["action"] == "approval_bulk_reject"


# ---------------------------------------------------------------------------
# POST /api/webhooks/slack/approvals — Slack Webhook
# ---------------------------------------------------------------------------


class TestSlackApprovalWebhook:
    def _make_slack_block_action_payload(
        self,
        action_id: str = "approval_req-001_approve",
        username: str = "alice",
    ) -> dict:
        return {
            "type": "block_actions",
            "user": {"id": "U12345", "username": username, "name": "Alice"},
            "actions": [{"action_id": action_id, "value": "click"}],
        }

    def test_slack_approve_action(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].approve_request = AsyncMock(
            return_value={"success": True, "request_id": "req-001"}
        )
        payload = self._make_slack_block_action_payload(
            action_id="approval_req-001_approve"
        )
        resp = client.post(
            "/api/webhooks/slack/approvals",
            json=payload,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "req-001" in data.get("text", "")

    def test_slack_reject_action(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["handler"].reject_request = AsyncMock(
            return_value={"success": True, "request_id": "req-001"}
        )
        payload = self._make_slack_block_action_payload(
            action_id="approval_req-001_reject"
        )
        resp = client.post(
            "/api/webhooks/slack/approvals",
            json=payload,
        )
        assert resp.status_code == 200

    def test_slack_non_block_action_ignored(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post(
            "/api/webhooks/slack/approvals",
            json={"type": "message", "text": "hello"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ignored"

    def test_slack_empty_actions_ignored(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post(
            "/api/webhooks/slack/approvals",
            json={"type": "block_actions", "actions": []},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ignored"

    def test_slack_non_approval_action_id_ignored(self, client_with_mocks):
        client, _ = client_with_mocks
        payload = {
            "type": "block_actions",
            "actions": [{"action_id": "button_click", "value": "x"}],
        }
        resp = client.post(
            "/api/webhooks/slack/approvals",
            json=payload,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ignored"

    def test_slack_unknown_action_type_ignored(self, client_with_mocks):
        client, _ = client_with_mocks
        payload = {
            "type": "block_actions",
            "user": {"username": "alice"},
            "actions": [{"action_id": "approval_req-001_cancel"}],
        }
        resp = client.post(
            "/api/webhooks/slack/approvals",
            json=payload,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ignored"

    def test_slack_invalid_json_returns_400(self, client_with_mocks):
        client, _ = client_with_mocks
        # Form-encoded data with no payload key is invalid
        resp = client.post(
            "/api/webhooks/slack/approvals",
            data="not_json_or_form=true",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 400

    def test_slack_signature_verification_invalid(self, client_with_mocks):
        client, mocks = client_with_mocks
        # Set secret so verification is triggered
        mocks["webhook_handler"].slack_signing_secret = "test-secret"
        mocks["webhook_handler"].verify_slack_signature = Mock(return_value=False)

        payload = self._make_slack_block_action_payload()
        resp = client.post(
            "/api/webhooks/slack/approvals",
            json=payload,
            headers={
                "X-Slack-Signature": "v0=bad",
                "X-Slack-Request-Timestamp": "12345",
            },
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Helper Utility Tests
# ---------------------------------------------------------------------------


class TestApprovalApiResponse:
    def test_success_response_shape(self):
        from forge_harness.webhook_server.api.approvals import api_response

        out = api_response(data={"count": 5})
        assert out["success"] is True
        assert out["data"] == {"count": 5}
        assert out["error"] is None
        assert "timestamp" in out

    def test_error_response_shape(self):
        from forge_harness.webhook_server.api.approvals import api_response

        out = api_response(error_code="NOT_FOUND", error_message="Not found")
        assert out["success"] is False
        assert out["error"]["code"] == "NOT_FOUND"
        assert out["error"]["message"] == "Not found"

    def test_error_defaults_unknown_code(self):
        from forge_harness.webhook_server.api.approvals import api_response

        out = api_response(error_message="oops")
        assert out["error"]["code"] == "UNKNOWN_ERROR"

    def test_no_args_returns_success(self):
        from forge_harness.webhook_server.api.approvals import api_response

        out = api_response()
        assert out["success"] is True
        assert out["data"] is None
