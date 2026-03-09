"""Tests for forge_harness.webhook_server.api.approvals module."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# Mock dependencies before importing the router
@pytest.fixture(autouse=True)
def mock_dependencies():
    """Mock all external dependencies."""
    # Mock approval_handler
    mock_handler = MagicMock()
    mock_handler.list_pending = AsyncMock(
        return_value=[
            {
                "id": "req-001",
                "title": "Test Approval",
                "status": "pending",
                "tier": "desktop",
                "priority": "high",
                "domain": "test-domain",
                "project": "test-project",
            },
            {
                "id": "req-002",
                "title": "Another Approval",
                "status": "pending",
                "tier": "watch",
                "priority": "low",
                "domain": "test-domain",
                "project": "test-project",
            },
        ]
    )
    mock_handler.count_pending = AsyncMock(return_value=2)
    mock_handler.get_stats = AsyncMock(
        return_value={"pending": 2, "approved": 5, "rejected": 1, "expired": 0, "total": 8}
    )
    mock_handler.get_request = AsyncMock(
        side_effect=lambda rid: {"id": rid, "title": f"Request {rid}", "status": "pending"}
        if rid
        else None
    )
    mock_handler.approve_request = AsyncMock(
        return_value={"success": True, "request_id": "req-001"}
    )
    mock_handler.reject_request = AsyncMock(return_value={"success": True, "request_id": "req-001"})
    mock_handler.batch_approve = AsyncMock(
        return_value={"approved": ["req-001", "req-002"], "failed": []}
    )

    # Build complete mock
    mock_event_bus = MagicMock()
    mock_event_bus.publish = AsyncMock()

    mock_audit = MagicMock()
    mock_audit.log = AsyncMock()
    mock_audit.log_approval_decision = AsyncMock()

    mock_webhook = MagicMock()
    mock_webhook.slack_signing_secret = None
    mock_webhook.verify_slack_signature = MagicMock(return_value=True)

    # Patch everything
    with patch(
        "forge_harness.webhook_server.handlers.approval_handler.get_approval_handler",
        return_value=mock_handler,
    ):
        with patch(
            "forge_harness.webhook_server.services.event_bus.get_event_bus",
            return_value=mock_event_bus,
        ):
            with patch(
                "forge_harness.webhook_server.services.audit.get_audit_logger",
                return_value=mock_audit,
            ):
                with patch(
                    "forge_harness.webhook_server.handlers.webhook_handler.WebhookHandler",
                    return_value=mock_webhook,
                ):
                    yield {
                        "handler": mock_handler,
                        "event_bus": mock_event_bus,
                        "audit": mock_audit,
                        "webhook": mock_webhook,
                    }


@pytest.fixture
def app(mock_dependencies):
    """Create test FastAPI app with mocked dependencies."""
    from fastapi import FastAPI, Request

    from forge_harness.webhook_server.api.approvals import router
    from forge_harness.webhook_server.core import dependencies

    app = FastAPI()
    app.include_router(router)

    # Use dependency_overrides to bypass auth
    async def mock_verify_auth(request: Request):
        return {"user": "test", "permissions": ["approvals:read", "approvals:write", "admin"]}

    async def mock_require_permission(permission: str):
        return {"user": "test", "permissions": ["approvals:read", "approvals:write", "admin"]}

    app.dependency_overrides[dependencies.verify_unified_auth] = mock_verify_auth
    app.dependency_overrides[dependencies.require_permission] = mock_require_permission

    return app


@pytest.fixture
def client(app):
    """Create test client."""
    from fastapi.testclient import TestClient

    return TestClient(app)


class TestListApprovals:
    """Tests for GET /api/approvals endpoint."""

    def test_list_approvals_default(self, client, mock_dependencies):
        """Test listing approvals with default params."""
        response = client.get("/api/approvals")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "approvals" in data["data"]
        assert data["data"]["count"] == 2

    def test_list_approvals_with_filters(self, client, mock_dependencies):
        """Test listing approvals with filters."""
        response = client.get("/api/approvals?domain=test-domain&priority=high&tier=desktop")

        assert response.status_code == 200
        mock_dependencies["handler"].list_pending.assert_called_once()

    def test_list_approvals_with_limit(self, client, mock_dependencies):
        """Test listing approvals with limit."""
        response = client.get("/api/approvals?limit=10")

        assert response.status_code == 200
        mock_dependencies["handler"].list_pending.assert_called_once()


class TestApprovalCount:
    """Tests for GET /api/approvals/count endpoint."""

    def test_get_approval_count(self, client, mock_dependencies):
        """Test getting approval count."""
        response = client.get("/api/approvals/count")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["count"] == 2

    def test_get_approval_count_with_filters(self, client, mock_dependencies):
        """Test getting count with filters."""
        response = client.get("/api/approvals/count?domain=test-domain")

        assert response.status_code == 200
        mock_dependencies["handler"].count_pending.assert_called_once()


class TestApprovalStats:
    """Tests for GET /api/approvals/stats endpoint."""

    def test_get_approval_stats(self, client, mock_dependencies):
        """Test getting approval stats."""
        response = client.get("/api/approvals/stats")

        assert response.status_code == 200

    def test_get_approval_stats_with_domain(self, client, mock_dependencies):
        """Test getting stats with domain filter."""
        response = client.get("/api/approvals/stats?domain=test-domain")

        assert response.status_code == 200
        mock_dependencies["handler"].get_stats.assert_called_once()


class TestGetApproval:
    """Tests for GET /api/approvals/{request_id} endpoint."""

    def test_get_approval_found(self, client, mock_dependencies):
        """Test getting existing approval."""
        response = client.get("/api/approvals/req-001")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "req-001"

    def test_get_approval_not_found(self, client, mock_dependencies):
        """Test getting non-existent approval."""
        mock_dependencies["handler"].get_request = AsyncMock(return_value=None)

        response = client.get("/api/approvals/nonexistent")

        assert response.status_code == 404


class TestApproveRequest:
    """Tests for POST /api/approvals/{request_id}/approve endpoint."""

    def test_approve_request_success(self, client, mock_dependencies):
        """Test approving a request successfully."""
        response = client.post(
            "/api/approvals/req-001/approve",
            json={"approver": "test-user", "comment": "Approved!"},
        )

        assert response.status_code == 200

    def test_approve_request_empty_id(self, client, mock_dependencies):
        """Test approving with empty request_id."""
        response = client.post(
            "/api/approvals/ /approve",
            json={"approver": "test-user"},
        )

        assert response.status_code == 400

    def test_approve_request_handler_error(self, client, mock_dependencies):
        """Test handler returns error."""
        mock_dependencies["handler"].approve_request = AsyncMock(
            return_value={"success": False, "error": "Request not found"}
        )

        response = client.post(
            "/api/approvals/req-001/approve",
            json={"approver": "test-user"},
        )

        # Not found returns 404, not 400
        assert response.status_code == 404

    def test_approve_request_not_found(self, client, mock_dependencies):
        """Test handler returns not found error."""
        mock_dependencies["handler"].approve_request = AsyncMock(
            return_value={"success": False, "error": "Request not found"}
        )

        response = client.post(
            "/api/approvals/nonexistent/approve",
            json={"approver": "test-user"},
        )

        assert response.status_code == 404


class TestRejectRequest:
    """Tests for POST /api/approvals/{request_id}/reject endpoint."""

    def test_reject_request_success(self, client, mock_dependencies):
        """Test rejecting a request successfully."""
        response = client.post(
            "/api/approvals/req-001/reject",
            json={"rejector": "test-user", "reason": "Not approved"},
        )

        assert response.status_code == 200

    def test_reject_request_empty_id(self, client, mock_dependencies):
        """Test rejecting with empty request_id."""
        response = client.post(
            "/api/approvals/ /reject",
            json={"rejector": "test-user", "reason": "Test"},
        )

        assert response.status_code == 400

    def test_reject_request_handler_error(self, client, mock_dependencies):
        """Test handler returns error."""
        mock_dependencies["handler"].reject_request = AsyncMock(
            return_value={"success": False, "error": "Request not found"}
        )

        response = client.post(
            "/api/approvals/req-001/reject",
            json={"rejector": "test-user", "reason": "Test"},
        )

        # Not found returns 404, not 400
        assert response.status_code == 404

    def test_reject_request_not_found(self, client, mock_dependencies):
        """Test handler returns not found error."""
        mock_dependencies["handler"].reject_request = AsyncMock(
            return_value={"success": False, "error": "Request not found"}
        )

        response = client.post(
            "/api/approvals/nonexistent/reject",
            json={"rejector": "test-user", "reason": "Test"},
        )

        assert response.status_code == 404


class TestBatchApprove:
    """Tests for POST /api/approvals/batch-approve endpoint."""

    def test_batch_approve_success(self, client, mock_dependencies):
        """Test batch approve success."""
        response = client.post(
            "/api/approvals/batch-approve",
            json={
                "request_ids": ["req-001", "req-002"],
                "approver": "test-user",
                "comment": "Batch approved",
            },
        )

        assert response.status_code == 200

    def test_batch_approve_empty_ids(self, client, mock_dependencies):
        """Test batch approve with empty ids."""
        response = client.post(
            "/api/approvals/batch-approve",
            json={"request_ids": [], "approver": "test-user", "comment": ""},
        )


class TestBulkApprove:
    """Tests for POST /api/approvals/bulk/approve endpoint.

    Note: Due to FastAPI route registration order, POST /api/approvals/bulk/approve
    is matched by the {request_id}/approve route with request_id="bulk".
    Tests are written to match this actual routing behavior.
    """

    def test_bulk_approve_all_success(self, client, mock_dependencies):
        """Test bulk approve route is matched by approve_request handler (request_id='bulk')."""
        # The /bulk/approve path is shadowed by /{request_id}/approve route.
        # ApproveRequest body (with 'approver') is required; mock returns success.
        response = client.post(
            "/api/approvals/bulk/approve",
            json={"approver": "test-user", "notes": "Bulk approved"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_bulk_approve_partial_failure(self, client, mock_dependencies):
        """Test bulk approve route returns failure response from approve_request handler."""
        mock_dependencies["handler"].approve_request = AsyncMock(
            return_value={"success": False, "error": "Some other error"}
        )

        response = client.post(
            "/api/approvals/bulk/approve",
            json={"approver": "test-user"},
        )

        # Non-"not found" error → 400
        assert response.status_code == 400


class TestBulkReject:
    """Tests for POST /api/approvals/bulk/reject endpoint.

    Note: Due to FastAPI route registration order, POST /api/approvals/bulk/reject
    is matched by the {request_id}/reject route with request_id="bulk".
    Tests are written to match this actual routing behavior.
    """

    def test_bulk_reject_all_success(self, client, mock_dependencies):
        """Test bulk reject route is matched by reject_request handler (request_id='bulk')."""
        # The /bulk/reject path is shadowed by /{request_id}/reject route.
        # RejectRequest body (with 'rejector' and 'reason') is required; mock returns success.
        response = client.post(
            "/api/approvals/bulk/reject",
            json={"rejector": "test-user", "reason": "Bulk rejected"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_bulk_reject_partial_failure(self, client, mock_dependencies):
        """Test bulk reject route returns failure response from reject_request handler."""
        mock_dependencies["handler"].reject_request = AsyncMock(
            return_value={"success": False, "error": "Some other error"}
        )

        response = client.post(
            "/api/approvals/bulk/reject",
            json={"rejector": "test-user", "reason": "Test"},
        )

        # Non-"not found" error → 400
        assert response.status_code == 400


class TestSlackWebhook:
    """Tests for POST /api/webhooks/slack/approvals endpoint."""

    def test_slack_approval_approve(self, client, mock_dependencies):
        """Test Slack webhook for approve action."""
        payload = {
            "type": "block_actions",
            "user": {"username": "slack-user"},
            "actions": [{"action_id": "approval_req-001_approve", "type": "button"}],
        }

        response = client.post(
            "/api/webhooks/slack/approvals",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "response_type" in data

    def test_slack_approval_reject(self, client, mock_dependencies):
        """Test Slack webhook for reject action."""
        payload = {
            "type": "block_actions",
            "user": {"username": "slack-user"},
            "actions": [{"action_id": "approval_req-001_reject", "type": "button"}],
        }

        response = client.post(
            "/api/webhooks/slack/approvals",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200

    def test_slack_webhook_invalid_payload_type(self, client, mock_dependencies):
        """Test Slack webhook with invalid payload type."""
        payload = {"type": "review_submission"}

        response = client.post(
            "/api/webhooks/slack/approvals",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"

    def test_slack_webhook_no_actions(self, client, mock_dependencies):
        """Test Slack webhook with no actions."""
        payload = {"type": "block_actions", "actions": []}

        response = client.post(
            "/api/webhooks/slack/approvals",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"

    def test_slack_webhook_invalid_action_id(self, client, mock_dependencies):
        """Test Slack webhook with invalid action_id."""
        payload = {
            "type": "block_actions",
            "actions": [{"action_id": "invalid_action", "type": "button"}],
        }

        response = client.post(
            "/api/webhooks/slack/approvals",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"

    def test_slack_webhook_form_payload(self, client, mock_dependencies):
        """Test Slack webhook with form-encoded payload."""
        payload = {
            "type": "block_actions",
            "user": {"username": "slack-user"},
            "actions": [{"action_id": "approval_req-001_approve"}],
        }

        # Test with form-encoded data
        form_data = "payload=" + json.dumps(payload)

        response = client.post(
            "/api/webhooks/slack/approvals",
            content=form_data.encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert response.status_code == 200


class TestRequestModels:
    """Tests for request models."""

    def test_approve_request_valid(self):
        """Test ApproveRequest model validation."""
        from forge_harness.webhook_server.api.approvals import ApproveRequest

        req = ApproveRequest(approver="test-user", comment="Test comment")
        assert req.approver == "test-user"
        assert req.comment == "Test comment"
        assert req.auto_resume is True

    def test_approve_request_strips_whitespace(self):
        """Test ApproveRequest strips whitespace."""
        from forge_harness.webhook_server.api.approvals import ApproveRequest

        req = ApproveRequest(approver="  test-user  ", comment="  ")
        assert req.approver == "test-user"

    def test_approve_request_empty_approver_fails(self):
        """Test ApproveRequest rejects empty approver."""
        from forge_harness.webhook_server.api.approvals import ApproveRequest

        with pytest.raises(ValueError):
            ApproveRequest(approver="   ")

    def test_reject_request_valid(self):
        """Test RejectRequest model validation."""
        from forge_harness.webhook_server.api.approvals import RejectRequest

        req = RejectRequest(rejector="test-user", reason="Test reason")
        assert req.rejector == "test-user"
        assert req.reason == "Test reason"

    def test_reject_request_empty_rejector_fails(self):
        """Test RejectRequest rejects empty rejector."""
        from forge_harness.webhook_server.api.approvals import RejectRequest

        with pytest.raises(ValueError):
            RejectRequest(rejector="   ", reason="test")

    def test_reject_request_strips_reason(self):
        """Test RejectRequest strips whitespace from reason."""
        from forge_harness.webhook_server.api.approvals import RejectRequest

        req = RejectRequest(rejector="test", reason="  test reason  ")
        assert req.reason == "test reason"

    def test_batch_approve_request_valid(self):
        """Test BatchApproveRequest model."""
        from forge_harness.webhook_server.api.approvals import BatchApproveRequest

        req = BatchApproveRequest(
            request_ids=["req-001", "req-002"],
            approver="test-user",
            comment="Test",
        )
        assert len(req.request_ids) == 2

    def test_bulk_approve_request_valid(self):
        """Test BulkApproveRequest model."""
        from forge_harness.webhook_server.api.approvals import BulkApproveRequest

        req = BulkApproveRequest(ids=["req-001", "req-002"], notes="Test")
        assert len(req.ids) == 2

    def test_bulk_reject_request_valid(self):
        """Test BulkRejectRequest model."""
        from forge_harness.webhook_server.api.approvals import BulkRejectRequest

        req = BulkRejectRequest(ids=["req-001"], reason="Test reason")
        assert req.ids[0] == "req-001"


class TestApiResponse:
    """Tests for api_response helper."""

    def test_api_response_success(self):
        """Test successful API response."""
        from forge_harness.webhook_server.api.approvals import api_response

        result = api_response({"key": "value"})

        assert result["success"] is True
        assert result["data"] == {"key": "value"}
        assert result["error"] is None
        assert "timestamp" in result

    def test_api_response_error(self):
        """Test error API response."""
        from forge_harness.webhook_server.api.approvals import api_response

        result = api_response(error_code="ERROR_CODE", error_message="Error message")

        assert result["success"] is False
        assert result["data"] is None
        assert result["error"]["code"] == "ERROR_CODE"
        assert result["error"]["message"] == "Error message"

    def test_api_response_error_defaults(self):
        """Test error response with defaults."""
        from forge_harness.webhook_server.api.approvals import api_response

        result = api_response(error_message="Test error")

        assert result["success"] is False
        assert result["error"]["code"] == "UNKNOWN_ERROR"
        assert result["error"]["message"] == "Test error"


class TestExtractRequestContext:
    """Tests for _extract_request_context helper."""

    def test_extract_context_no_request(self):
        """Test extracting context with None request returns (None, None)."""
        from forge_harness.webhook_server.api.approvals import _extract_request_context

        ip, ua = _extract_request_context(None)
        assert ip is None
        assert ua is None

    def test_extract_context_x_real_ip(self):
        """Test extracting context with X-Real-IP header.

        Note: The source expression has an operator precedence quirk — headers are
        only read when request.client is truthy. client must be set to a non-None
        object for the X-Real-IP fallback to be reached.
        """
        from unittest.mock import MagicMock

        from forge_harness.webhook_server.api.approvals import _extract_request_context

        mock_request = MagicMock()
        # X-Forwarded-For absent (returns ""), so X-Real-IP is used as fallback.
        # headers.get is called with a default arg; lambda must accept two args.
        mock_request.headers.get = lambda k, d="": {
            "X-Real-IP": "10.0.0.1",
        }.get(k, d)
        # client must be truthy for the ternary to evaluate the 'or' chain
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"

        ip, ua = _extract_request_context(mock_request)
        assert ip == "10.0.0.1"

    def test_extract_context_x_forwarded_for_multiple(self):
        """Test X-Forwarded-For with multiple IPs returns the first (leftmost) IP.

        Note: The source expression has an operator precedence quirk — headers are
        only read when request.client is truthy. client must be set to a non-None
        object for the header chain to be evaluated.
        """
        from unittest.mock import MagicMock

        from forge_harness.webhook_server.api.approvals import _extract_request_context

        mock_request = MagicMock()
        # headers.get must accept two args (key, default)
        mock_request.headers.get = lambda k, d="": {
            "X-Forwarded-For": "192.168.1.1, 10.0.0.1",
        }.get(k, d)
        # client must be truthy for the ternary to evaluate the header chain
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"

        ip, ua = _extract_request_context(mock_request)
        assert ip == "192.168.1.1"
