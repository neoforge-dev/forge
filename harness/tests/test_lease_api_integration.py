"""Integration tests for TaskLease lifecycle API endpoints (CP-2002).

Tests cover the four lease endpoints that live in
forge_harness/webhook_server/api/tasks.py:

    POST /api/tasks/{task_id}/lease/claim
    POST /api/tasks/{task_id}/lease/renew
    POST /api/tasks/{task_id}/lease/release
    POST /api/tasks/{task_id}/lease/requeue

Strategy
--------
- Use FastAPI TestClient backed by a real app (create_app).
- Bypass auth by setting FORGE_JWT_REQUIRE_AUTH=false on the app state so that
  require_permission passes without a token.
- Rate limiting is disabled by setting FORGE_RATE_LIMIT_ENABLED=false and
  passing RateLimitConfig(enabled=False) to create_app.  The middleware's
  per-path limiters are also disabled via module-level env var so the fixed
  burst of 10 tokens does not block our test suite.
- Mock the task_handler methods at the API-layer injection points using
  patch("forge_harness.webhook_server.api.tasks.get_task_handler") so we
  control what the handler returns and raises.
- Separately mock the event bus and audit logger so those side-effects do not
  require a real SSE/file infrastructure.

Coverage targets
----------------
- All four lease endpoints happy paths.
- 404 when handler returns None (task not found).
- 409/400 TaskLeaseError propagation for each error code variant.
- 422 Pydantic validation for missing required fields.
- Event bus publish called with the right event type.
- Audit logger called with the right arguments.
- Full lifecycle integration: claim -> renew -> release -> requeue.
- Concurrent claim conflict (LEASE_ALREADY_OWNED).
- Expired lease auto-detection (LEASE_EXPIRED on renew).
- PATH_LOCK_CONFLICT on claim.
- PATH_LOCK_IMMUTABLE on renew.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Disable rate limiting at the environment level before any module-level
# imports trigger the app to read the env vars.  The WebhookRateLimitMiddleware
# creates per-path limiters with `enabled=True` hardcoded, so we override the
# global env var and additionally patch the middleware class at app-build time.
os.environ.setdefault("FORGE_RATE_LIMIT_ENABLED", "false")

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


@pytest.fixture
def app(monkeypatch):
    """Create a FastAPI test app with auth and rate-limiting disabled.

    Uses function scope so each test gets a fresh RateLimiter bucket, avoiding
    the 10-request burst cap on /api/* paths enforced by the middleware.

    Rate limiting is disabled by:
    1. Passing RateLimitConfig(enabled=False) to create_app (global limiter).
    2. Patching RateLimitConfig so the per-path limiters created by
       WebhookRateLimitMiddleware also have enabled=False.
    """
    from forge_harness.webhook_server import create_app
    from forge_harness.webhook_server.infrastructure import rate_limiter as rl_module

    # Make every RateLimitConfig instantiation return an always-disabled config.
    _orig_init = rl_module.RateLimitConfig.__init__

    def _disabled_init(self, requests_per_minute=60, burst_size=10, enabled=True):
        _orig_init(
            self, requests_per_minute=requests_per_minute, burst_size=burst_size, enabled=False
        )

    monkeypatch.setattr(rl_module.RateLimitConfig, "__init__", _disabled_init)

    _app = create_app(rate_limit_config=rl_module.RateLimitConfig(enabled=False))

    # Disable auth so require_permission passes without a Bearer token.
    # verify_unified_auth checks app.state.auth_config.require_auth.
    from dataclasses import dataclass

    @dataclass
    class _NoAuth:
        require_auth: bool = False
        allow_localhost: bool = False

    _app.state.auth_config = _NoAuth()
    return _app


@pytest.fixture
def client(app):
    """TestClient wrapping the app."""
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Shared test data helpers
# ---------------------------------------------------------------------------


def _future_ts(seconds: int = 3600) -> str:
    """Return a future UTC ISO-8601 timestamp string."""
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _past_ts(seconds: int = 3600) -> str:
    """Return a past UTC ISO-8601 timestamp string."""
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def _valid_lease(
    owner_node: str = "nova",
    owner_agent: str = "forge:claude",
    path_lock: str = "src/api/routes.py",
    expires_in: int = 3600,
) -> dict[str, Any]:
    """Build a valid lease payload dict."""
    return {
        "owner_node": owner_node,
        "owner_agent": owner_agent,
        "lease_expires_at": _future_ts(expires_in),
        "path_lock": path_lock,
    }


def _task_dict(
    task_id: str = "t1",
    status: str = "assigned",
    lease: dict | None = None,
    claimed_by: str = "forge:claude",
) -> dict[str, Any]:
    """Return a minimal task dict as the handler would return."""
    return {
        "id": task_id,
        "subject": "Test task",
        "description": "desc",
        "priority": "medium",
        "status": status,
        "claimed_by": claimed_by,
        "lease": lease,
        "order": 0,
        "domain": None,
        "project": None,
        "created_at": "2026-02-22T12:00:00+00:00",
        "updated_at": "2026-02-22T12:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Context managers for patching the three injection points
# ---------------------------------------------------------------------------

_HANDLER_PATH = "forge_harness.webhook_server.api.tasks.get_task_handler"
_EVENT_BUS_PATH = "forge_harness.webhook_server.api.tasks.get_event_bus"
_AUDIT_PATH = "forge_harness.webhook_server.api.tasks.get_audit_logger"


def _mock_handler(return_value=None, side_effect=None):
    """Return an AsyncMock-backed coroutine for get_task_handler."""
    handler = MagicMock()
    handler.claim_task_with_lease = AsyncMock(return_value=return_value, side_effect=side_effect)
    handler.renew_task_lease = AsyncMock(return_value=return_value, side_effect=side_effect)
    handler.release_task_lease = AsyncMock(return_value=return_value, side_effect=side_effect)
    handler.requeue_task = AsyncMock(return_value=return_value, side_effect=side_effect)
    return handler


def _mock_event_bus():
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


def _mock_audit():
    audit = MagicMock()
    audit.log_task_assignment = AsyncMock()
    return audit


# ===========================================================================
# POST /api/tasks/{task_id}/lease/claim
# ===========================================================================


class TestLeaseClaimEndpoint:
    """Tests for the POST /api/tasks/{task_id}/lease/claim endpoint."""

    def _url(self, task_id: str = "t1") -> str:
        return f"/api/tasks/{task_id}/lease/claim"

    # -----------------------------------------------------------------------
    # Happy path
    # -----------------------------------------------------------------------

    def test_claim_returns_200_with_task_data(self, client):
        lease = _valid_lease()
        task = _task_dict(lease=lease)
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url(), json={"lease": lease})

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["id"] == "t1"

    def test_claim_passes_lease_to_handler(self, client):
        lease = _valid_lease(owner_node="prya", owner_agent="forge:codex")
        task = _task_dict(lease=lease, claimed_by="forge:codex")
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url(), json={"lease": lease})

        assert response.status_code == 200
        # Verify handler was called with lease dict
        handler.claim_task_with_lease.assert_called_once()
        call_args = handler.claim_task_with_lease.call_args
        passed_lease = call_args[0][1]
        assert passed_lease["owner_node"] == "prya"
        assert passed_lease["owner_agent"] == "forge:codex"

    def test_claim_publishes_task_lease_claimed_event(self, client):
        lease = _valid_lease()
        task = _task_dict(lease=lease)
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post(self._url(), json={"lease": lease})

        bus.publish.assert_called_once()
        event_name = bus.publish.call_args[0][0]
        assert event_name == "task.lease.claimed"

    def test_claim_calls_audit_logger(self, client):
        lease = _valid_lease(owner_agent="forge:claude")
        task = _task_dict(lease=lease)
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post(self._url("t1"), json={"lease": lease})

        audit.log_task_assignment.assert_called_once()
        call_kwargs = audit.log_task_assignment.call_args.kwargs
        assert call_kwargs["task_id"] == "t1"
        assert call_kwargs["agent_id"] == "forge:claude"

    # -----------------------------------------------------------------------
    # 404 — task not found
    # -----------------------------------------------------------------------

    def test_claim_404_when_handler_returns_none(self, client):
        handler = _mock_handler(return_value=None)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url("missing"), json={"lease": _valid_lease()})

        assert response.status_code == 404
        detail = response.json()
        # Error is nested in FastAPI 422 detail or HTTPException detail
        assert "TASK_NOT_FOUND" in str(detail)

    # -----------------------------------------------------------------------
    # 409 — lease conflicts
    # -----------------------------------------------------------------------

    def test_claim_409_when_lease_already_owned(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError("LEASE_ALREADY_OWNED", "Task is already leased", 409)
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url(), json={"lease": _valid_lease()})

        assert response.status_code == 409
        assert "LEASE_ALREADY_OWNED" in str(response.json())

    def test_claim_409_when_path_lock_conflict(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "PATH_LOCK_CONFLICT",
                "path_lock 'src/api/routes.py' is already held by task t2",
                409,
            )
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url(), json={"lease": _valid_lease()})

        assert response.status_code == 409
        body = response.json()
        assert "PATH_LOCK_CONFLICT" in str(body)

    # -----------------------------------------------------------------------
    # 400 — invalid lease
    # -----------------------------------------------------------------------

    def test_claim_400_when_lease_invalid(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "INVALID_LEASE",
                "lease_expires_at must be a future UTC timestamp",
                400,
            )
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url(), json={"lease": _valid_lease()})

        assert response.status_code == 400
        assert "INVALID_LEASE" in str(response.json())

    # -----------------------------------------------------------------------
    # 422 — validation error (missing required body fields)
    # -----------------------------------------------------------------------

    def test_claim_422_when_body_missing_lease(self, client):
        """LeaseClaimRequest requires a 'lease' field; omitting it gives 422."""
        response = client.post(self._url(), json={})
        assert response.status_code == 422

    def test_claim_422_when_lease_missing_owner_node(self, client):
        """owner_node is required in TaskLease; omitting it gives 422."""
        incomplete_lease = {
            "owner_agent": "forge:claude",
            "lease_expires_at": _future_ts(),
            "path_lock": "src/api/routes.py",
        }
        response = client.post(self._url(), json={"lease": incomplete_lease})
        assert response.status_code == 422

    def test_claim_422_when_lease_expires_at_invalid_format(self, client):
        """lease_expires_at must be ISO-8601 datetime; garbage string gives 422."""
        bad_lease = {
            "owner_node": "nova",
            "owner_agent": "forge:claude",
            "lease_expires_at": "not-a-timestamp",
            "path_lock": "src/api/routes.py",
        }
        response = client.post(self._url(), json={"lease": bad_lease})
        assert response.status_code == 422


# ===========================================================================
# POST /api/tasks/{task_id}/lease/renew
# ===========================================================================


class TestLeaseRenewEndpoint:
    """Tests for the POST /api/tasks/{task_id}/lease/renew endpoint."""

    def _url(self, task_id: str = "t1") -> str:
        return f"/api/tasks/{task_id}/lease/renew"

    # -----------------------------------------------------------------------
    # Happy path
    # -----------------------------------------------------------------------

    def test_renew_returns_200_with_updated_task(self, client):
        new_lease = _valid_lease(expires_in=7200)
        task = _task_dict(lease=new_lease)
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url(), json={"lease": new_lease})

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["id"] == "t1"

    def test_renew_passes_lease_dict_to_handler(self, client):
        new_lease = _valid_lease(expires_in=7200)
        task = _task_dict(lease=new_lease)
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post(self._url("t1"), json={"lease": new_lease})

        handler.renew_task_lease.assert_called_once()
        args = handler.renew_task_lease.call_args[0]
        assert args[0] == "t1"

    def test_renew_publishes_task_lease_renewed_event(self, client):
        new_lease = _valid_lease(expires_in=7200)
        task = _task_dict(lease=new_lease)
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post(self._url(), json={"lease": new_lease})

        bus.publish.assert_called_once()
        event_name = bus.publish.call_args[0][0]
        assert event_name == "task.lease.renewed"

    # -----------------------------------------------------------------------
    # 404 — task not found
    # -----------------------------------------------------------------------

    def test_renew_404_when_task_not_found(self, client):
        handler = _mock_handler(return_value=None)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url("ghost"), json={"lease": _valid_lease()})

        assert response.status_code == 404
        assert "TASK_NOT_FOUND" in str(response.json())

    # -----------------------------------------------------------------------
    # 409 — lease expired on renew
    # -----------------------------------------------------------------------

    def test_renew_409_when_existing_lease_expired(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "LEASE_EXPIRED",
                "Existing lease has expired; claim the task again",
                409,
            )
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url(), json={"lease": _valid_lease()})

        assert response.status_code == 409
        assert "LEASE_EXPIRED" in str(response.json())

    def test_renew_409_when_lease_not_found(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError("LEASE_NOT_FOUND", "Task has no lease to renew", 409)
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url(), json={"lease": _valid_lease()})

        assert response.status_code == 409
        assert "LEASE_NOT_FOUND" in str(response.json())

    def test_renew_409_when_owner_mismatch(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "LEASE_OWNER_MISMATCH", "Only the current lease owner can renew this lease", 409
            )
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(
                self._url(),
                json={"lease": _valid_lease(owner_agent="forge:imposter")},
            )

        assert response.status_code == 409
        assert "LEASE_OWNER_MISMATCH" in str(response.json())

    # -----------------------------------------------------------------------
    # 400 — path lock immutable
    # -----------------------------------------------------------------------

    def test_renew_400_when_path_lock_changed(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "PATH_LOCK_IMMUTABLE",
                "path_lock cannot change during lease renewal",
                400,
            )
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(
                self._url(),
                json={"lease": _valid_lease(path_lock="src/different/file.py")},
            )

        assert response.status_code == 400
        assert "PATH_LOCK_IMMUTABLE" in str(response.json())

    # -----------------------------------------------------------------------
    # 422 — validation
    # -----------------------------------------------------------------------

    def test_renew_422_when_body_missing_lease(self, client):
        response = client.post(self._url(), json={})
        assert response.status_code == 422

    def test_renew_422_when_lease_missing_path_lock(self, client):
        incomplete_lease = {
            "owner_node": "nova",
            "owner_agent": "forge:claude",
            "lease_expires_at": _future_ts(),
            # path_lock missing
        }
        response = client.post(self._url(), json={"lease": incomplete_lease})
        assert response.status_code == 422


# ===========================================================================
# POST /api/tasks/{task_id}/lease/release
# ===========================================================================


class TestLeaseReleaseEndpoint:
    """Tests for the POST /api/tasks/{task_id}/lease/release endpoint."""

    def _url(self, task_id: str = "t1") -> str:
        return f"/api/tasks/{task_id}/lease/release"

    # -----------------------------------------------------------------------
    # Happy path
    # -----------------------------------------------------------------------

    def test_release_returns_200(self, client):
        task = _task_dict(status="pending", lease=None, claimed_by="")
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(
                self._url(), json={"owner_node": "nova", "owner_agent": "forge:claude"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_release_with_no_body_uses_defaults(self, client):
        """LeaseReleaseRequest has all optional fields; empty body is valid."""
        task = _task_dict(status="pending", lease=None)
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url(), json={})

        assert response.status_code == 200

    def test_release_publishes_task_lease_released_event(self, client):
        task = _task_dict(status="pending", lease=None)
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post(self._url(), json={})

        bus.publish.assert_called_once()
        event_name = bus.publish.call_args[0][0]
        assert event_name == "task.lease.released"

    def test_release_passes_owner_verification_fields(self, client):
        task = _task_dict(status="pending", lease=None)
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post(
                self._url("t1"),
                json={"owner_node": "nova", "owner_agent": "forge:claude"},
            )

        handler.release_task_lease.assert_called_once()
        call_kwargs = handler.release_task_lease.call_args.kwargs
        assert call_kwargs["owner_node"] == "nova"
        assert call_kwargs["owner_agent"] == "forge:claude"

    # -----------------------------------------------------------------------
    # 404 — task not found
    # -----------------------------------------------------------------------

    def test_release_404_when_task_not_found(self, client):
        handler = _mock_handler(return_value=None)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url("ghost"), json={})

        assert response.status_code == 404
        assert "TASK_NOT_FOUND" in str(response.json())

    # -----------------------------------------------------------------------
    # 409 — owner mismatch
    # -----------------------------------------------------------------------

    def test_release_409_when_owner_node_mismatch(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "LEASE_OWNER_MISMATCH",
                "owner_node does not match the active lease owner",
                409,
            )
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(
                self._url(), json={"owner_node": "wrong-node", "owner_agent": "forge:claude"}
            )

        assert response.status_code == 409
        assert "LEASE_OWNER_MISMATCH" in str(response.json())

    def test_release_409_when_owner_agent_mismatch(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "LEASE_OWNER_MISMATCH",
                "owner_agent does not match the active lease owner",
                409,
            )
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(
                self._url(), json={"owner_node": "nova", "owner_agent": "forge:imposter"}
            )

        assert response.status_code == 409
        assert "LEASE_OWNER_MISMATCH" in str(response.json())

    def test_release_409_when_no_lease_on_task(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError("LEASE_NOT_FOUND", "Task has no lease to release", 409)
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url(), json={})

        assert response.status_code == 409
        assert "LEASE_NOT_FOUND" in str(response.json())


# ===========================================================================
# POST /api/tasks/{task_id}/lease/requeue
# ===========================================================================


class TestLeaseRequeueEndpoint:
    """Tests for the POST /api/tasks/{task_id}/lease/requeue endpoint."""

    def _url(self, task_id: str = "t1") -> str:
        return f"/api/tasks/{task_id}/lease/requeue"

    # -----------------------------------------------------------------------
    # Happy path
    # -----------------------------------------------------------------------

    def test_requeue_returns_200_with_cleared_ownership(self, client):
        task = _task_dict(status="pending", lease=None, claimed_by="")
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url(), json={})

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # Ownership should be cleared in the returned task
        task_data = data["data"]
        assert task_data["status"] == "pending"

    def test_requeue_with_no_body_uses_defaults(self, client):
        """LeaseRequeueRequest has all optional fields; empty body is valid."""
        task = _task_dict(status="pending", lease=None, claimed_by="")
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url())

        assert response.status_code == 200

    def test_requeue_publishes_task_requeued_event(self, client):
        task = _task_dict(status="pending", lease=None, claimed_by="")
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post(self._url("t1"), json={"reason": "lease expired"})

        bus.publish.assert_called_once()
        event_name = bus.publish.call_args[0][0]
        assert event_name == "task.requeued"

    def test_requeue_event_payload_includes_task_id_and_reason(self, client):
        task = _task_dict(status="pending", lease=None, claimed_by="")
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post(self._url("t1"), json={"reason": "timed out"})

        event_payload = bus.publish.call_args[0][1]
        assert event_payload["task_id"] == "t1"
        assert event_payload["reason"] == "timed out"

    def test_requeue_passes_owner_fields_to_handler(self, client):
        task = _task_dict(status="pending", lease=None, claimed_by="")
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post(
                self._url("t1"),
                json={"owner_node": "nova", "owner_agent": "forge:claude", "reason": "done"},
            )

        handler.requeue_task.assert_called_once()
        call_kwargs = handler.requeue_task.call_args.kwargs
        assert call_kwargs["owner_node"] == "nova"
        assert call_kwargs["owner_agent"] == "forge:claude"

    # -----------------------------------------------------------------------
    # 404 — task not found
    # -----------------------------------------------------------------------

    def test_requeue_404_when_task_not_found(self, client):
        handler = _mock_handler(return_value=None)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(self._url("ghost"), json={})

        assert response.status_code == 404
        assert "TASK_NOT_FOUND" in str(response.json())

    # -----------------------------------------------------------------------
    # 409 — owner mismatch
    # -----------------------------------------------------------------------

    def test_requeue_409_when_owner_mismatch(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "LEASE_OWNER_MISMATCH",
                "owner_node does not match the active lease owner",
                409,
            )
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(
                self._url(), json={"owner_node": "wrong-node", "owner_agent": "forge:claude"}
            )

        assert response.status_code == 409
        assert "LEASE_OWNER_MISMATCH" in str(response.json())


# ===========================================================================
# Full lifecycle integration test
# ===========================================================================


class TestLeaseFullLifecycle:
    """End-to-end lifecycle: claim -> renew -> release -> requeue."""

    def test_full_lifecycle_happy_path(self, client):
        """Walk through all four endpoints in sequence with matching mocks."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        lease = _valid_lease()
        task_claimed = _task_dict(status="assigned", lease=lease)
        task_renewed = _task_dict(
            status="assigned", lease={**lease, "lease_expires_at": _future_ts(7200)}
        )
        task_released = _task_dict(status="pending", lease=None, claimed_by="")
        task_requeued = _task_dict(status="pending", lease=None, claimed_by="")

        bus = _mock_event_bus()
        audit = _mock_audit()

        # --- CLAIM ---
        claim_handler = _mock_handler(return_value=task_claimed)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=claim_handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r1 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease})
        assert r1.status_code == 200, f"claim failed: {r1.json()}"

        # --- RENEW ---
        renew_handler = _mock_handler(return_value=task_renewed)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=renew_handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r2 = client.post(
                "/api/tasks/t1/lease/renew",
                json={"lease": {**lease, "lease_expires_at": _future_ts(7200)}},
            )
        assert r2.status_code == 200, f"renew failed: {r2.json()}"

        # --- RELEASE ---
        release_handler = _mock_handler(return_value=task_released)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=release_handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r3 = client.post(
                "/api/tasks/t1/lease/release",
                json={"owner_node": "nova", "owner_agent": "forge:claude"},
            )
        assert r3.status_code == 200, f"release failed: {r3.json()}"

        # --- REQUEUE ---
        requeue_handler = _mock_handler(return_value=task_requeued)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=requeue_handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r4 = client.post(
                "/api/tasks/t1/lease/requeue",
                json={"owner_node": "nova", "owner_agent": "forge:claude", "reason": "done"},
            )
        assert r4.status_code == 200, f"requeue failed: {r4.json()}"

        # Verify returned task after requeue has cleared ownership
        final_task = r4.json()["data"]
        assert final_task["status"] == "pending"
        assert final_task["lease"] is None

    def test_claim_then_expired_then_requeue_flow(self, client):
        """Simulate lease expiry: claim succeeds, renew on expired lease returns 409."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        lease = _valid_lease()
        task_claimed = _task_dict(status="assigned", lease=lease)
        bus = _mock_event_bus()
        audit = _mock_audit()

        # CLAIM succeeds
        claim_handler = _mock_handler(return_value=task_claimed)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=claim_handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r1 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease})
        assert r1.status_code == 200

        # RENEW fails because lease has "expired"
        expired_renew_handler = _mock_handler(
            side_effect=TaskLeaseError(
                "LEASE_EXPIRED", "Existing lease has expired; claim the task again", 409
            )
        )
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=expired_renew_handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r2 = client.post("/api/tasks/t1/lease/renew", json={"lease": lease})
        assert r2.status_code == 409
        assert "LEASE_EXPIRED" in str(r2.json())

        # REQUEUE to clear the stale ownership
        task_requeued = _task_dict(status="pending", lease=None, claimed_by="")
        requeue_handler = _mock_handler(return_value=task_requeued)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=requeue_handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r3 = client.post("/api/tasks/t1/lease/requeue", json={"reason": "expired"})
        assert r3.status_code == 200


# ===========================================================================
# Concurrent claim conflict
# ===========================================================================


class TestConcurrentClaimConflict:
    """Validate that the 409 LEASE_ALREADY_OWNED contract is correctly surfaced."""

    def test_second_agent_gets_409_when_task_already_claimed(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        lease_agent1 = _valid_lease(owner_agent="forge:agent1")
        lease_agent2 = _valid_lease(owner_agent="forge:agent2")
        bus = _mock_event_bus()
        audit = _mock_audit()

        # Agent 1 claims successfully
        task = _task_dict(lease=lease_agent1, claimed_by="forge:agent1")
        handler_ok = _mock_handler(return_value=task)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_ok)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r1 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease_agent1})
        assert r1.status_code == 200

        # Agent 2 tries to claim the same task — gets 409
        handler_conflict = _mock_handler(
            side_effect=TaskLeaseError(
                "LEASE_ALREADY_OWNED", "Task is already leased by another owner", 409
            )
        )
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_conflict)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r2 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease_agent2})

        assert r2.status_code == 409
        body = r2.json()
        assert "LEASE_ALREADY_OWNED" in str(body)

    def test_same_owner_can_reclaim_without_conflict(self, client):
        """The same owner reclaiming its own active lease must succeed (200)."""
        lease = _valid_lease()
        task = _task_dict(lease=lease)
        bus = _mock_event_bus()
        audit = _mock_audit()

        handler = _mock_handler(return_value=task)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r1 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease})
        assert r1.status_code == 200

        # Same owner reclaims
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r2 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease})
        assert r2.status_code == 200


# ===========================================================================
# Expired lease auto-detection
# ===========================================================================


class TestExpiredLeaseAutoDetection:
    """Verify that the API surfaces LEASE_EXPIRED correctly."""

    def test_renew_detects_expired_lease_from_handler(self, client):
        """Handler detects expiry and raises LEASE_EXPIRED -> API returns 409."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "LEASE_EXPIRED", "Existing lease has expired; claim the task again", 409
            )
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(
                "/api/tasks/t1/lease/renew",
                json={"lease": _valid_lease()},
            )

        assert response.status_code == 409
        body = response.json()
        assert "LEASE_EXPIRED" in str(body)

    def test_claim_with_past_expires_at_fails_pydantic_or_handler(self, client):
        """A past lease_expires_at gets accepted by Pydantic (it's a valid datetime),
        but the handler must raise INVALID_LEASE with status 400."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        past_lease = {
            "owner_node": "nova",
            "owner_agent": "forge:claude",
            "lease_expires_at": _past_ts(),
            "path_lock": "src/api/routes.py",
        }
        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "INVALID_LEASE", "lease_expires_at must be a future UTC timestamp", 400
            )
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post("/api/tasks/t1/lease/claim", json={"lease": past_lease})

        assert response.status_code == 400
        assert "INVALID_LEASE" in str(response.json())


# ===========================================================================
# Error response schema validation
# ===========================================================================


class TestErrorResponseSchema:
    """Verify the canonical error schema is returned on all error paths."""

    def test_404_error_response_structure(self, client):
        handler = _mock_handler(return_value=None)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post("/api/tasks/ghost/lease/claim", json={"lease": _valid_lease()})

        assert response.status_code == 404
        # FastAPI HTTPException wraps the error in 'detail'
        body = response.json()
        # The canonical error body should contain 'code' and 'message' somewhere in the body
        body_str = str(body)
        assert "TASK_NOT_FOUND" in body_str
        assert "Task" in body_str or "not found" in body_str.lower()

    def test_409_error_response_structure(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError("LEASE_ALREADY_OWNED", "Already owned", 409)
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post("/api/tasks/t1/lease/claim", json={"lease": _valid_lease()})

        assert response.status_code == 409
        body = response.json()
        # forge_json_error wraps in {"error": {...}} or {"code": ...} shape
        body_str = str(body)
        assert "LEASE_ALREADY_OWNED" in body_str

    def test_400_error_response_contains_code(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(side_effect=TaskLeaseError("INVALID_LEASE", "Bad lease", 400))
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post("/api/tasks/t1/lease/claim", json={"lease": _valid_lease()})

        assert response.status_code == 400
        assert "INVALID_LEASE" in str(response.json())

    def test_success_response_has_success_true_and_data(self, client):
        lease = _valid_lease()
        task = _task_dict(lease=lease)
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post("/api/tasks/t1/lease/claim", json={"lease": lease})

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "data" in body
        assert body["data"]["id"] == "t1"


# ===========================================================================
# Audit logging detailed checks
# ===========================================================================


class TestAuditLogging:
    """Verify audit logging is invoked correctly on the claim endpoint."""

    def test_audit_receives_correct_task_id(self, client):
        lease = _valid_lease(owner_agent="forge:agent99")
        task = _task_dict(task_id="audit-task", lease=lease, claimed_by="forge:agent99")
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post("/api/tasks/audit-task/lease/claim", json={"lease": lease})

        call_kwargs = audit.log_task_assignment.call_args.kwargs
        assert call_kwargs["task_id"] == "audit-task"

    def test_audit_receives_correct_agent_id(self, client):
        lease = _valid_lease(owner_agent="forge:special-agent")
        task = _task_dict(lease=lease, claimed_by="forge:special-agent")
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post("/api/tasks/t1/lease/claim", json={"lease": lease})

        call_kwargs = audit.log_task_assignment.call_args.kwargs
        assert call_kwargs["agent_id"] == "forge:special-agent"

    def test_audit_receives_owner_node_in_context(self, client):
        lease = _valid_lease(owner_node="prya", owner_agent="forge:glm")
        task = _task_dict(lease=lease, claimed_by="forge:glm")
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post("/api/tasks/t1/lease/claim", json={"lease": lease})

        call_kwargs = audit.log_task_assignment.call_args.kwargs
        context = call_kwargs.get("context", {})
        assert context.get("owner_node") == "prya"

    def test_audit_not_called_when_handler_raises(self, client):
        """If the handler raises, audit logging must NOT be called."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(side_effect=TaskLeaseError("LEASE_ALREADY_OWNED", "Conflict", 409))
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post("/api/tasks/t1/lease/claim", json={"lease": _valid_lease()})

        audit.log_task_assignment.assert_not_called()

    def test_audit_not_called_when_task_not_found(self, client):
        """If handler returns None (task not found), audit must NOT be called."""
        handler = _mock_handler(return_value=None)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post("/api/tasks/ghost/lease/claim", json={"lease": _valid_lease()})

        audit.log_task_assignment.assert_not_called()


# ===========================================================================
# Event bus publishing checks
# ===========================================================================


class TestEventBusPublishing:
    """Verify event publishing happens with correct event names and payloads."""

    def test_claim_event_carries_task_payload(self, client):
        lease = _valid_lease()
        task = _task_dict(lease=lease)
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post("/api/tasks/t1/lease/claim", json={"lease": lease})

        call_args = bus.publish.call_args[0]
        event_data = call_args[1]
        assert event_data["id"] == "t1"

    def test_renew_event_carries_task_payload(self, client):
        lease = _valid_lease(expires_in=7200)
        task = _task_dict(lease=lease)
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post("/api/tasks/t1/lease/renew", json={"lease": lease})

        event_data = bus.publish.call_args[0][1]
        assert event_data["id"] == "t1"

    def test_release_event_carries_task_payload(self, client):
        task = _task_dict(status="pending", lease=None, claimed_by="")
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post("/api/tasks/t1/lease/release", json={})

        event_data = bus.publish.call_args[0][1]
        assert event_data["id"] == "t1"

    def test_requeue_event_carries_task_and_reason(self, client):
        task = _task_dict(status="pending", lease=None, claimed_by="")
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post("/api/tasks/t1/lease/requeue", json={"reason": "supervisor forced requeue"})

        call_args = bus.publish.call_args[0]
        assert call_args[0] == "task.requeued"
        payload = call_args[1]
        assert payload["task_id"] == "t1"
        assert payload["reason"] == "supervisor forced requeue"
        assert payload["task"]["id"] == "t1"

    def test_event_not_published_when_handler_raises(self, client):
        """On TaskLeaseError, the event bus must NOT be called."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(side_effect=TaskLeaseError("PATH_LOCK_CONFLICT", "conflict", 409))
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post("/api/tasks/t1/lease/claim", json={"lease": _valid_lease()})

        bus.publish.assert_not_called()

    def test_event_not_published_when_task_not_found(self, client):
        """On 404 (handler returns None), the event bus must NOT be called."""
        handler = _mock_handler(return_value=None)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post("/api/tasks/ghost/lease/claim", json={"lease": _valid_lease()})

        bus.publish.assert_not_called()


# ===========================================================================
# PATH_LOCK_CONFLICT on claim
# ===========================================================================


class TestPathLockConflictOnClaim:
    """Dedicated tests for the PATH_LOCK_CONFLICT error surface."""

    def test_path_lock_conflict_returns_409(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "PATH_LOCK_CONFLICT",
                "path_lock 'src/api/routes.py' is already held by task t9",
                409,
            )
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(
                "/api/tasks/t1/lease/claim",
                json={"lease": _valid_lease(path_lock="src/api/routes.py")},
            )

        assert response.status_code == 409
        body_str = str(response.json())
        assert "PATH_LOCK_CONFLICT" in body_str

    def test_path_lock_conflict_mentions_conflicting_task(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "PATH_LOCK_CONFLICT",
                "path_lock 'src/api/routes.py' is already held by task t99",
                409,
            )
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(
                "/api/tasks/t1/lease/claim",
                json={"lease": _valid_lease(path_lock="src/api/routes.py")},
            )

        assert "t99" in str(response.json())
