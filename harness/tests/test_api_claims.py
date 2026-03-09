"""Comprehensive tests for forge_harness/webhook_server/api/claims.py

Tests cover all 4 endpoints:
- GET  /api/claims                    - List claims with optional status filter
- GET  /api/claims/stats              - Claim counts by status
- GET  /api/claims/{claim_id}         - Get a single claim by ID
- POST /api/claims/{claim_id}/verify  - Approve or reject a claim

All external dependencies (ClaimsService, EventBus) are mocked.
Auth is bypassed via dependency_overrides.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers: mock claim and service objects
# ---------------------------------------------------------------------------


def _make_claim(
    claim_id: str = "claim-001",
    task_id: str = "IS-219",
    agent_id: str = "forge:nova",
    status: str = "submitted",
    verified_at: datetime | None = None,
    rejection_reason: str | None = None,
) -> Mock:
    """Return a mock CompletionClaim-like object."""
    from forge_harness.webhook_server.models.claim import ClaimStatus

    claim = Mock()
    claim.claim_id = claim_id
    claim.task_id = task_id
    claim.agent_id = agent_id
    claim.status = ClaimStatus(status)
    claim.submitted_at = datetime.now(UTC)
    claim.verified_at = verified_at
    claim.rejection_reason = rejection_reason

    claim.model_dump.return_value = {
        "claim_id": claim_id,
        "task_id": task_id,
        "agent_id": agent_id,
        "status": status,
        "submitted_at": datetime.now(UTC).isoformat(),
        "verified_at": verified_at.isoformat() if verified_at else None,
        "rejection_reason": rejection_reason,
        "evidence": {},
    }
    return claim


def _make_claims_service(
    claims: list | None = None,
    stats: dict | None = None,
    single_claim: Mock | None = None,
) -> Mock:
    """Return a mock ClaimsService."""
    service = Mock()
    _claims = claims if claims is not None else [_make_claim()]
    _stats = stats or {
        "submitted": 1,
        "verifying": 0,
        "approved": 0,
        "rejected": 0,
    }
    _single = single_claim if single_claim is not None else (_claims[0] if _claims else None)

    service.list_claims.return_value = _claims
    service.get_stats.return_value = _stats
    service.get_claim.return_value = _single
    service.verify.return_value = _make_claim(status="approved")
    service._persist_claim = Mock()
    return service


def _make_event_bus() -> Mock:
    bus = Mock()
    bus.publish = AsyncMock()
    return bus


# ---------------------------------------------------------------------------
# App factory with auth bypassed
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    """Build a minimal FastAPI app with the claims router and auth bypassed."""
    from forge_harness.webhook_server.api import claims as claims_module
    from forge_harness.webhook_server.core import dependencies

    app = FastAPI()
    app.include_router(claims_module.router)

    # Bypass auth
    _admin_user = {"sub": "test", "permissions": ["admin"]}
    app.dependency_overrides[dependencies.verify_auth] = lambda: _admin_user
    app.dependency_overrides[dependencies.verify_unified_auth] = lambda: _admin_user

    def _noop_require_permission(permission: str):
        async def _check():
            return _admin_user
        return _check

    app.dependency_overrides[dependencies.require_permission] = _noop_require_permission

    return app


class _PatchedClient:
    """Context manager that keeps patches alive while the client is used."""

    def __init__(self, service: Mock, event_bus: Mock | None = None):
        self._service = service
        self._bus = event_bus or _make_event_bus()

    def __enter__(self):
        self._p1 = patch(
            "forge_harness.webhook_server.api.claims.get_claims_service",
            new=AsyncMock(return_value=self._service),
        )
        self._p2 = patch(
            "forge_harness.webhook_server.api.claims.get_event_bus",
            new=AsyncMock(return_value=self._bus),
        )
        self._p1.start()
        self._p2.start()
        app = _build_app()
        self._tc = TestClient(app)
        return self._tc, self._service, self._bus

    def __exit__(self, *args):
        self._p1.stop()
        self._p2.stop()


# ---------------------------------------------------------------------------
# GET /api/claims
# ---------------------------------------------------------------------------


class TestListClaims:
    """Tests for GET /api/claims."""

    def test_returns_200_with_claims_list(self):
        service = _make_claims_service()
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "claims" in data
        assert "count" in data

    def test_empty_claims_returns_empty_list(self):
        service = _make_claims_service(claims=[])
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["claims"] == []
        assert data["count"] == 0

    def test_returns_multiple_claims(self):
        claims = [
            _make_claim(claim_id="c-001", status="submitted"),
            _make_claim(claim_id="c-002", status="approved"),
        ]
        service = _make_claims_service(claims=claims)
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims")

        data = resp.json()["data"]
        assert data["count"] == 2
        assert len(data["claims"]) == 2

    def test_filter_by_status_submitted(self):
        claims = [_make_claim(status="submitted")]
        service = _make_claims_service(claims=claims)
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims?status=submitted")

        assert resp.status_code == 200
        # Verify service was called with the correct filter
        from forge_harness.webhook_server.models.claim import ClaimStatus

        svc.list_claims.assert_called_once_with(status=ClaimStatus.SUBMITTED)

    def test_filter_by_status_approved(self):
        claims = [_make_claim(status="approved")]
        service = _make_claims_service(claims=claims)
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims?status=approved")

        assert resp.status_code == 200

    def test_invalid_status_returns_400(self):
        service = _make_claims_service()
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims?status=nonexistent_status")

        assert resp.status_code == 400

    def test_limit_param_applies(self):
        claims = [_make_claim(claim_id=f"c-{i:03d}") for i in range(10)]
        service = _make_claims_service(claims=claims)
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims?limit=3")

        data = resp.json()["data"]
        assert data["count"] == 3
        assert len(data["claims"]) == 3

    def test_claim_dict_contains_submitted_at_as_string(self):
        service = _make_claims_service()
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims")

        claims = resp.json()["data"]["claims"]
        assert len(claims) >= 1
        # submitted_at should be a string (ISO format)
        assert isinstance(claims[0]["submitted_at"], str)

    def test_claim_status_serialized_as_string(self):
        service = _make_claims_service()
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims")

        claims = resp.json()["data"]["claims"]
        assert isinstance(claims[0]["status"], str)

    def test_no_filter_calls_list_with_none(self):
        service = _make_claims_service()
        with _PatchedClient(service) as (client, svc, _bus):
            client.get("/api/claims")

        svc.list_claims.assert_called_once_with(status=None)


# ---------------------------------------------------------------------------
# GET /api/claims/stats
# ---------------------------------------------------------------------------


class TestGetClaimStats:
    """Tests for GET /api/claims/stats."""

    def test_returns_200_with_stats(self):
        stats = {"submitted": 5, "verifying": 2, "approved": 10, "rejected": 1}
        service = _make_claims_service(stats=stats)
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims/stats")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["submitted"] == 5
        assert data["approved"] == 10

    def test_delegates_to_service_get_stats(self):
        service = _make_claims_service()
        with _PatchedClient(service) as (client, svc, _bus):
            client.get("/api/claims/stats")

        svc.get_stats.assert_called_once()

    def test_stats_returns_all_statuses(self):
        stats = {
            "submitted": 0,
            "verifying": 0,
            "approved": 0,
            "rejected": 0,
        }
        service = _make_claims_service(stats=stats)
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims/stats")

        data = resp.json()["data"]
        for key in ("submitted", "verifying", "approved", "rejected"):
            assert key in data


# ---------------------------------------------------------------------------
# GET /api/claims/{claim_id}
# ---------------------------------------------------------------------------


class TestGetClaim:
    """Tests for GET /api/claims/{claim_id}."""

    def test_returns_claim_when_found(self):
        claim = _make_claim(claim_id="claim-42", task_id="IS-300")
        service = _make_claims_service(single_claim=claim)
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims/claim-42")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["claim_id"] == "claim-42"

    def test_returns_404_when_not_found(self):
        service = _make_claims_service(single_claim=None)
        service.get_claim.return_value = None
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims/nonexistent-claim")

        assert resp.status_code == 404

    def test_calls_service_with_correct_id(self):
        claim = _make_claim(claim_id="claim-99")
        service = _make_claims_service(single_claim=claim)
        with _PatchedClient(service) as (client, svc, _bus):
            client.get("/api/claims/claim-99")

        svc.get_claim.assert_called_once_with("claim-99")

    def test_verified_at_serialized_when_present(self):
        verified_at = datetime.now(UTC)
        claim = _make_claim(claim_id="claim-v", status="approved", verified_at=verified_at)
        # Make sure model_dump returns verified_at
        claim.model_dump.return_value = {
            "claim_id": "claim-v",
            "task_id": "IS-1",
            "agent_id": "forge:nova",
            "status": "approved",
            "submitted_at": datetime.now(UTC).isoformat(),
            "verified_at": verified_at.isoformat(),
            "rejection_reason": None,
            "evidence": {},
        }
        service = _make_claims_service(single_claim=claim)
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims/claim-v")

        data = resp.json()["data"]
        assert data["verified_at"] is not None
        assert isinstance(data["verified_at"], str)

    def test_status_serialized_as_string(self):
        claim = _make_claim(claim_id="c-str", status="submitted")
        service = _make_claims_service(single_claim=claim)
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.get("/api/claims/c-str")

        data = resp.json()["data"]
        assert isinstance(data["status"], str)


# ---------------------------------------------------------------------------
# POST /api/claims/{claim_id}/verify
# ---------------------------------------------------------------------------


class TestVerifyClaim:
    """Tests for POST /api/claims/{claim_id}/verify."""

    def test_approve_claim_returns_200(self):
        original_claim = _make_claim(claim_id="c-001", status="submitted")
        approved_claim = _make_claim(claim_id="c-001", status="approved")
        service = _make_claims_service(single_claim=original_claim)
        service.verify.return_value = approved_claim
        bus = _make_event_bus()
        with _PatchedClient(service, event_bus=bus) as (client, svc, _bus):
            resp = client.post(
                "/api/claims/c-001/verify",
                json={"action": "approve", "approver": "alice"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["action"] == "approve"
        assert "claim" in data

    def test_reject_claim_returns_200(self):
        original_claim = _make_claim(claim_id="c-002", status="submitted")
        # The router transitions submitted -> verifying first, then verifying -> rejected.
        # model_copy call 1 (on original_claim) must return a "verifying" intermediate.
        # model_copy call 2 (on verifying_claim) must return the final "rejected" claim.
        verifying_claim = _make_claim(claim_id="c-002", status="verifying")
        rejected_claim = _make_claim(
            claim_id="c-002", status="rejected", rejection_reason="evidence incomplete"
        )
        service = _make_claims_service(single_claim=original_claim)
        original_claim.model_copy.return_value = verifying_claim
        verifying_claim.model_copy.return_value = rejected_claim
        bus = _make_event_bus()
        with _PatchedClient(service, event_bus=bus) as (client, svc, _bus):
            resp = client.post(
                "/api/claims/c-002/verify",
                json={
                    "action": "reject",
                    "approver": "alice",
                    "reason": "evidence incomplete",
                },
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["action"] == "reject"

    def test_reject_without_reason_returns_400(self):
        claim = _make_claim(claim_id="c-003", status="submitted")
        service = _make_claims_service(single_claim=claim)
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.post(
                "/api/claims/c-003/verify",
                json={"action": "reject", "approver": "alice"},
            )

        assert resp.status_code == 400
        assert "reason" in resp.json()["detail"].lower()

    def test_invalid_action_returns_400(self):
        claim = _make_claim(claim_id="c-004", status="submitted")
        service = _make_claims_service(single_claim=claim)
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.post(
                "/api/claims/c-004/verify",
                json={"action": "obliterate", "approver": "alice"},
            )

        assert resp.status_code == 400

    def test_claim_not_found_returns_404(self):
        service = _make_claims_service()
        service.get_claim.return_value = None
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.post(
                "/api/claims/nonexistent/verify",
                json={"action": "approve", "approver": "alice"},
            )

        assert resp.status_code == 404

    def test_already_approved_returns_400(self):
        claim = _make_claim(claim_id="c-done", status="approved")
        service = _make_claims_service(single_claim=claim)
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.post(
                "/api/claims/c-done/verify",
                json={"action": "approve", "approver": "alice"},
            )

        assert resp.status_code == 400
        assert "already" in resp.json()["detail"].lower()

    def test_already_rejected_returns_400(self):
        claim = _make_claim(claim_id="c-rej", status="rejected")
        service = _make_claims_service(single_claim=claim)
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.post(
                "/api/claims/c-rej/verify",
                json={
                    "action": "reject",
                    "approver": "alice",
                    "reason": "already done",
                },
            )

        assert resp.status_code == 400

    def test_event_bus_publish_called_on_approve(self):
        original_claim = _make_claim(claim_id="c-evt", status="submitted")
        approved_claim = _make_claim(claim_id="c-evt", status="approved")
        service = _make_claims_service(single_claim=original_claim)
        service.verify.return_value = approved_claim
        bus = _make_event_bus()
        with _PatchedClient(service, event_bus=bus) as (client, svc, _bus):
            client.post(
                "/api/claims/c-evt/verify",
                json={"action": "approve", "approver": "bob"},
            )

        _bus.publish.assert_called_once()
        call_args = _bus.publish.call_args
        assert call_args.args[0] == "claim.verified"
        payload = call_args.args[1]
        assert payload["claim_id"] == "c-evt"
        assert payload["action"] == "approve"
        assert payload["approver"] == "bob"

    def test_service_exception_returns_500(self):
        claim = _make_claim(claim_id="c-err", status="submitted")
        service = _make_claims_service(single_claim=claim)
        service.verify.side_effect = RuntimeError("unexpected storage failure")
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.post(
                "/api/claims/c-err/verify",
                json={"action": "approve", "approver": "alice"},
            )

        assert resp.status_code == 500

    def test_missing_approver_returns_422(self):
        service = _make_claims_service()
        with _PatchedClient(service) as (client, svc, _bus):
            resp = client.post(
                "/api/claims/c-001/verify",
                json={"action": "approve"},
            )

        assert resp.status_code == 422

    def test_action_is_case_insensitive(self):
        claim = _make_claim(claim_id="c-case", status="submitted")
        approved_claim = _make_claim(claim_id="c-case", status="approved")
        service = _make_claims_service(single_claim=claim)
        service.verify.return_value = approved_claim
        bus = _make_event_bus()
        with _PatchedClient(service, event_bus=bus) as (client, svc, _bus):
            resp = client.post(
                "/api/claims/c-case/verify",
                json={"action": "APPROVE", "approver": "alice"},
            )

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------


class TestVerifyClaimRequest:
    """Validate Pydantic model VerifyClaimRequest."""

    def test_valid_approve(self):
        from forge_harness.webhook_server.api.claims import VerifyClaimRequest

        req = VerifyClaimRequest(action="approve", approver="alice")
        assert req.action == "approve"
        assert req.approver == "alice"
        assert req.reason is None

    def test_valid_reject_with_reason(self):
        from forge_harness.webhook_server.api.claims import VerifyClaimRequest

        req = VerifyClaimRequest(
            action="reject", approver="bob", reason="Not enough evidence"
        )
        assert req.reason == "Not enough evidence"

    def test_empty_approver_raises(self):
        from forge_harness.webhook_server.api.claims import VerifyClaimRequest

        with pytest.raises(ValidationError):
            VerifyClaimRequest(action="approve", approver="")

    def test_approver_too_long_raises(self):
        from forge_harness.webhook_server.api.claims import VerifyClaimRequest

        with pytest.raises(ValidationError):
            VerifyClaimRequest(action="approve", approver="x" * 201)

    def test_reason_optional(self):
        from forge_harness.webhook_server.api.claims import VerifyClaimRequest

        req = VerifyClaimRequest(action="approve", approver="alice")
        assert req.reason is None


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestApiResponseHelper:
    """Test the api_response helper in claims module."""

    def test_success_response_structure(self):
        from forge_harness.webhook_server.api.claims import api_response

        out = api_response(data={"key": "value"})
        assert out["success"] is True
        assert out["data"] == {"key": "value"}
        assert out["error"] is None
        assert "timestamp" in out

    def test_error_response_structure(self):
        from forge_harness.webhook_server.api.claims import api_response

        out = api_response(error_code="NOT_FOUND", error_message="Missing resource")
        assert out["success"] is False
        assert out["data"] is None
        assert out["error"]["code"] == "NOT_FOUND"
        assert out["error"]["message"] == "Missing resource"

    def test_error_defaults_unknown_code(self):
        from forge_harness.webhook_server.api.claims import api_response

        out = api_response(error_message="oops")
        assert out["error"]["code"] == "UNKNOWN_ERROR"

    def test_timestamp_is_string(self):
        from forge_harness.webhook_server.api.claims import api_response

        out = api_response(data=None)
        assert isinstance(out["timestamp"], str)
