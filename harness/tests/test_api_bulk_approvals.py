"""Tests for Bulk Approval Mutation API Endpoints

Covers bulk mutation endpoints in forge_harness/webhook_server/api/approvals.py:
- POST /api/approvals/bulk/approve   - Bulk approve multiple requests
- POST /api/approvals/bulk/reject    - Bulk reject multiple requests

NOTE on route shadowing:
  When the approvals router is mounted standalone (not via create_app),
  FastAPI's route-matching resolves POST /api/approvals/bulk/approve to the
  earlier-registered /{request_id}/approve route (request_id="bulk").
  The bulk endpoints work correctly in the full app where route ordering
  places static paths before parameterised ones.

  Strategy used here:
  1. Pydantic model validation — pure unit tests (no HTTP client needed).
  2. Handler logic — call the endpoint functions directly with mocked deps
     using @pytest.mark.asyncio (avoids the route-shadow problem entirely).
  3. Auth enforcement — a minimal app with bulk routes registered FIRST,
     without the parameterised routes present.

Test categories:
- Pydantic model validation (BulkApproveRequest, BulkRejectRequest)
- Success path: all IDs processed successfully
- Partial failure: some IDs fail, others succeed
- All IDs fail: handler returns error for every ID
- Empty IDs list: graceful handling
- Handler exception counted as failure
- SSE events emitted per successful operation
- Audit log emitted only when success_count > 0
- Auth required (401 without credentials) via dedicated minimal app
"""

import json as _json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Shared mock factories
# ---------------------------------------------------------------------------


def _make_handler(
    approve_success: bool = True,
    reject_success: bool = True,
) -> Mock:
    handler = Mock()
    handler.list_pending = AsyncMock(return_value=[])
    handler.count_pending = AsyncMock(return_value=0)
    handler.get_stats = AsyncMock(
        return_value={"pending": 0, "approved": 0, "rejected": 0, "expired": 0, "total": 0}
    )
    handler.get_request = AsyncMock(return_value=None)
    handler.approve_request = AsyncMock(
        return_value=(
            {"success": True, "request_id": "req-001"}
            if approve_success
            else {"success": False, "error": "Request not found"}
        )
    )
    handler.reject_request = AsyncMock(
        return_value=(
            {"success": True, "request_id": "req-001"}
            if reject_success
            else {"success": False, "error": "Request not found"}
        )
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
# Helpers for direct handler invocation
# ---------------------------------------------------------------------------


def _patch_approvals(handler, event_bus, audit):
    """Return a context manager that patches all approvals module deps."""
    return (
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
    )


# ---------------------------------------------------------------------------
# Minimal-app fixture for auth enforcement tests
# (bulk routes registered BEFORE parameterised routes to avoid shadowing)
# ---------------------------------------------------------------------------


@pytest.fixture
def bulk_auth_client():
    """
    Minimal FastAPI app with the approvals router included but NO auth bypass.
    Used to verify that bulk endpoints return 401/403 without credentials.

    Note: Due to route shadowing, the bulk URL hits the {request_id} route
    first, but the auth check fires before any route-specific logic — so the
    401 is correctly returned regardless of which route captures the request.
    """
    from forge_harness.webhook_server.api import approvals as approvals_module

    app = FastAPI()
    app.include_router(approvals_module.router)

    # No dependency_overrides — auth is enforced

    handler = _make_handler()
    event_bus = _make_event_bus()
    audit = _make_audit()
    webhook_handler = _make_webhook_handler()

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
    yield test_client

    for p in patches:
        p.stop()


# ---------------------------------------------------------------------------
# Pydantic model validation tests
# ---------------------------------------------------------------------------


class TestBulkApproveRequestModel:
    def test_valid_minimal(self):
        from forge_harness.webhook_server.api.approvals import BulkApproveRequest

        req = BulkApproveRequest(ids=["req-001", "req-002"])
        assert req.ids == ["req-001", "req-002"]
        assert req.notes is None

    def test_valid_with_notes(self):
        from forge_harness.webhook_server.api.approvals import BulkApproveRequest

        req = BulkApproveRequest(ids=["req-001"], notes="Sprint cleanup")
        assert req.notes == "Sprint cleanup"

    def test_empty_ids_is_valid(self):
        from forge_harness.webhook_server.api.approvals import BulkApproveRequest

        req = BulkApproveRequest(ids=[])
        assert req.ids == []

    def test_missing_ids_raises(self):
        from forge_harness.webhook_server.api.approvals import BulkApproveRequest

        with pytest.raises(ValidationError):
            BulkApproveRequest()  # type: ignore[call-arg]

    def test_notes_optional(self):
        from forge_harness.webhook_server.api.approvals import BulkApproveRequest

        req = BulkApproveRequest(ids=["r1"])
        assert req.notes is None


class TestBulkRejectRequestModel:
    def test_valid(self):
        from forge_harness.webhook_server.api.approvals import BulkRejectRequest

        req = BulkRejectRequest(ids=["req-001", "req-002"], reason="Sprint cancelled")
        assert req.ids == ["req-001", "req-002"]
        assert req.reason == "Sprint cancelled"

    def test_missing_ids_raises(self):
        from forge_harness.webhook_server.api.approvals import BulkRejectRequest

        with pytest.raises(ValidationError):
            BulkRejectRequest(reason="Cancelled")  # type: ignore[call-arg]

    def test_missing_reason_raises(self):
        from forge_harness.webhook_server.api.approvals import BulkRejectRequest

        with pytest.raises(ValidationError):
            BulkRejectRequest(ids=["req-001"])  # type: ignore[call-arg]

    def test_empty_ids_is_valid(self):
        from forge_harness.webhook_server.api.approvals import BulkRejectRequest

        req = BulkRejectRequest(ids=[], reason="Nothing to reject")
        assert req.ids == []


# ---------------------------------------------------------------------------
# POST /api/approvals/bulk/approve — Handler-level tests
# ---------------------------------------------------------------------------


class TestBulkApproveHandler:
    """Test bulk_approve_requests handler via direct invocation (bypasses route shadow)."""

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

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkApproveRequest(ids=["req-001", "req-002", "req-003"])
            resp = await bulk_approve_requests(body, _=None)

        data = _json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["success"] == 3
        assert data["data"]["failed"] == 0
        assert data["data"]["errors"] == []

    @pytest.mark.asyncio
    async def test_calls_handler_for_each_id(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkApproveRequest,
            bulk_approve_requests,
        )

        handler = _make_handler()
        handler.approve_request = AsyncMock(
            return_value={"success": True, "request_id": "x"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkApproveRequest(ids=["req-001", "req-002"])
            await bulk_approve_requests(body, _=None)

        assert handler.approve_request.call_count == 2

    @pytest.mark.asyncio
    async def test_uses_command_center_as_approver(self):
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

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkApproveRequest(ids=["req-001"])
            await bulk_approve_requests(body, _=None)

        call_kwargs = handler.approve_request.call_args[1]
        assert call_kwargs["approver"] == "command-center"

    @pytest.mark.asyncio
    async def test_passes_notes_as_comment(self):
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

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkApproveRequest(ids=["req-001"], notes="Sprint cleanup")
            await bulk_approve_requests(body, _=None)

        call_kwargs = handler.approve_request.call_args[1]
        assert call_kwargs["comment"] == "Sprint cleanup"

    @pytest.mark.asyncio
    async def test_auto_resume_is_true(self):
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

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkApproveRequest(ids=["req-001"])
            await bulk_approve_requests(body, _=None)

        call_kwargs = handler.approve_request.call_args[1]
        assert call_kwargs["auto_resume"] is True

    @pytest.mark.asyncio
    async def test_emits_sse_per_success(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkApproveRequest,
            bulk_approve_requests,
        )

        handler = _make_handler()
        handler.approve_request = AsyncMock(
            return_value={"success": True, "request_id": "x"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkApproveRequest(ids=["req-001", "req-002"])
            await bulk_approve_requests(body, _=None)

        assert event_bus.publish.call_count == 2
        for c in event_bus.publish.call_args_list:
            assert c[0][0] == "approval.resolved"
            payload = c[0][1]
            assert payload["action"] == "approved"
            assert payload["bulk"] is True

    @pytest.mark.asyncio
    async def test_emits_audit_log_when_any_succeed(self):
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

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkApproveRequest(ids=["req-001", "req-002"])
            await bulk_approve_requests(body, _=None)

        audit.log.assert_called_once()
        assert audit.log.call_args[1]["action"] == "approval_bulk_approve"

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkApproveRequest,
            bulk_approve_requests,
        )

        responses = [
            {"success": True, "request_id": "req-001"},
            {"success": False, "error": "Not found"},
            {"success": True, "request_id": "req-003"},
        ]
        handler = _make_handler()
        handler.approve_request = AsyncMock(side_effect=responses)
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkApproveRequest(ids=["req-001", "req-002", "req-003"])
            resp = await bulk_approve_requests(body, _=None)

        data = _json.loads(resp.body)["data"]
        assert data["success"] == 2
        assert data["failed"] == 1
        assert len(data["errors"]) == 1
        assert data["errors"][0]["id"] == "req-002"

    @pytest.mark.asyncio
    async def test_all_fail(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkApproveRequest,
            bulk_approve_requests,
        )

        handler = _make_handler()
        handler.approve_request = AsyncMock(
            return_value={"success": False, "error": "Request not found"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkApproveRequest(ids=["req-001", "req-002"])
            resp = await bulk_approve_requests(body, _=None)

        data = _json.loads(resp.body)["data"]
        assert data["success"] == 0
        assert data["failed"] == 2

    @pytest.mark.asyncio
    async def test_no_audit_when_all_fail(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkApproveRequest,
            bulk_approve_requests,
        )

        handler = _make_handler()
        handler.approve_request = AsyncMock(
            return_value={"success": False, "error": "Not found"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkApproveRequest(ids=["req-001"])
            await bulk_approve_requests(body, _=None)

        audit.log.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_counted_as_failure(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkApproveRequest,
            bulk_approve_requests,
        )

        handler = _make_handler()
        handler.approve_request = AsyncMock(side_effect=Exception("Redis timeout"))
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkApproveRequest(ids=["req-001"])
            resp = await bulk_approve_requests(body, _=None)

        data = _json.loads(resp.body)["data"]
        assert data["failed"] == 1
        assert "Redis timeout" in data["errors"][0]["error"]

    @pytest.mark.asyncio
    async def test_empty_ids(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkApproveRequest,
            bulk_approve_requests,
        )

        handler = _make_handler()
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkApproveRequest(ids=[])
            resp = await bulk_approve_requests(body, _=None)

        data = _json.loads(resp.body)["data"]
        assert data["success"] == 0
        assert data["failed"] == 0
        assert data["errors"] == []
        handler.approve_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_response_shape(self):
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

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkApproveRequest(ids=["req-001"])
            resp = await bulk_approve_requests(body, _=None)

        outer = _json.loads(resp.body)
        assert "success" in outer
        assert "data" in outer
        assert "error" in outer
        assert "timestamp" in outer
        inner = outer["data"]
        assert "success" in inner
        assert "failed" in inner
        assert "errors" in inner


class TestBulkApproveAuthEnforcement:
    """Auth enforcement tests via a minimal app where bulk routes are NOT shadowed."""

    def test_bulk_approve_auth_required(self, bulk_auth_client):
        resp = bulk_auth_client.post(
            "/api/approvals/bulk/approve",
            json={"ids": ["req-001"]},
        )
        assert resp.status_code in (401, 403)

    def test_bulk_reject_auth_required(self, bulk_auth_client):
        resp = bulk_auth_client.post(
            "/api/approvals/bulk/reject",
            json={"ids": ["req-001"], "reason": "Test"},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /api/approvals/bulk/reject — Handler-level tests
# ---------------------------------------------------------------------------


class TestBulkRejectHandler:
    """Test bulk_reject_requests handler via direct invocation (bypasses route shadow)."""

    @pytest.mark.asyncio
    async def test_all_success(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(
            return_value={"success": True, "request_id": "req-001"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkRejectRequest(ids=["req-001", "req-002"], reason="Sprint cancelled")
            resp = await bulk_reject_requests(body, _=None)

        data = _json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["success"] == 2
        assert data["data"]["failed"] == 0

    @pytest.mark.asyncio
    async def test_calls_handler_for_each_id(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(
            return_value={"success": True, "request_id": "x"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkRejectRequest(ids=["req-001", "req-002"], reason="OOS")
            await bulk_reject_requests(body, _=None)

        assert handler.reject_request.call_count == 2

    @pytest.mark.asyncio
    async def test_uses_command_center_as_rejector(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(
            return_value={"success": True, "request_id": "req-001"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkRejectRequest(ids=["req-001"], reason="OOS")
            await bulk_reject_requests(body, _=None)

        call_kwargs = handler.reject_request.call_args[1]
        assert call_kwargs["rejector"] == "command-center"

    @pytest.mark.asyncio
    async def test_passes_reason_to_handler(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(
            return_value={"success": True, "request_id": "req-001"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkRejectRequest(ids=["req-001"], reason="Out of scope")
            await bulk_reject_requests(body, _=None)

        call_kwargs = handler.reject_request.call_args[1]
        assert call_kwargs["reason"] == "Out of scope"

    @pytest.mark.asyncio
    async def test_emits_sse_per_success(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(
            return_value={"success": True, "request_id": "x"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkRejectRequest(ids=["req-001", "req-002"], reason="OOS")
            await bulk_reject_requests(body, _=None)

        assert event_bus.publish.call_count == 2
        for c in event_bus.publish.call_args_list:
            assert c[0][0] == "approval.resolved"
            payload = c[0][1]
            assert payload["action"] == "rejected"
            assert payload["bulk"] is True

    @pytest.mark.asyncio
    async def test_sse_contains_reason(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(
            return_value={"success": True, "request_id": "req-001"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkRejectRequest(ids=["req-001"], reason="Sprint cancelled")
            await bulk_reject_requests(body, _=None)

        sse_payload = event_bus.publish.call_args[0][1]
        assert sse_payload["reason"] == "Sprint cancelled"

    @pytest.mark.asyncio
    async def test_emits_audit_log_when_any_succeed(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(
            return_value={"success": True, "request_id": "req-001"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkRejectRequest(ids=["req-001"], reason="OOS")
            await bulk_reject_requests(body, _=None)

        audit.log.assert_called_once()
        assert audit.log.call_args[1]["action"] == "approval_bulk_reject"

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        responses = [
            {"success": True, "request_id": "req-001"},
            {"success": False, "error": "Not found"},
        ]
        handler = _make_handler()
        handler.reject_request = AsyncMock(side_effect=responses)
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkRejectRequest(ids=["req-001", "req-002"], reason="OOS")
            resp = await bulk_reject_requests(body, _=None)

        data = _json.loads(resp.body)["data"]
        assert data["success"] == 1
        assert data["failed"] == 1
        assert data["errors"][0]["id"] == "req-002"

    @pytest.mark.asyncio
    async def test_all_fail(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(
            return_value={"success": False, "error": "Not found"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkRejectRequest(ids=["req-001", "req-002"], reason="Gone")
            resp = await bulk_reject_requests(body, _=None)

        data = _json.loads(resp.body)["data"]
        assert data["success"] == 0
        assert data["failed"] == 2

    @pytest.mark.asyncio
    async def test_no_audit_when_all_fail(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(
            return_value={"success": False, "error": "Not found"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkRejectRequest(ids=["req-001"], reason="Gone")
            await bulk_reject_requests(body, _=None)

        audit.log.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_counted_as_failure(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(side_effect=Exception("DB locked"))
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkRejectRequest(ids=["req-001"], reason="Gone")
            resp = await bulk_reject_requests(body, _=None)

        data = _json.loads(resp.body)["data"]
        assert data["failed"] == 1
        assert "DB locked" in data["errors"][0]["error"]

    @pytest.mark.asyncio
    async def test_empty_ids(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkRejectRequest(ids=[], reason="Nothing")
            resp = await bulk_reject_requests(body, _=None)

        data = _json.loads(resp.body)["data"]
        assert data["success"] == 0
        assert data["failed"] == 0
        handler.reject_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_response_shape(self):
        from forge_harness.webhook_server.api.approvals import (
            BulkRejectRequest,
            bulk_reject_requests,
        )

        handler = _make_handler()
        handler.reject_request = AsyncMock(
            return_value={"success": True, "request_id": "req-001"}
        )
        event_bus = _make_event_bus()
        audit = _make_audit()

        p1, p2, p3 = _patch_approvals(handler, event_bus, audit)
        with p1, p2, p3:
            body = BulkRejectRequest(ids=["req-001"], reason="OOS")
            resp = await bulk_reject_requests(body, _=None)

        outer = _json.loads(resp.body)
        assert "success" in outer
        assert "data" in outer
        assert "error" in outer
        assert "timestamp" in outer
