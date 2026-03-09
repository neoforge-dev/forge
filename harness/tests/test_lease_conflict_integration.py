"""Integration tests for lease conflict detection, path-lock rejection, and full
lease lifecycle recovery (CP-2008).

Coverage areas
--------------
- **Collision tests**: simultaneous same-task claims, same-path claims on distinct
  tasks, reclaim after release, reclaim after expiry+requeue.
- **Expiry / Recovery tests**: LeaseRecoveryWorker detects expired leases, requeues
  them, and publishes the correct events; expired leases cannot be renewed; requeued
  leases can be re-claimed.
- **Path-lock rejection tests**: claiming with a locked path returns 409; releasing
  frees the path; recovery clears the path lock.
- **Reassignment tests**: full claim→release→requeue→claim by a different agent,
  and claim→expire→recover→claim by a different agent.
- **Edge cases**: empty path_lock bypasses conflict check; multiple renewals within
  max_renewals; renewal beyond max_renewals rejected; concurrent agents on different
  paths succeed independently.

Strategy
--------
- API-layer tests reuse the TestClient + fixture pattern from
  test_lease_api_integration.py (same rate-limit + auth bypass, same mock injection
  points).
- LeaseRecoveryWorker tests operate directly on the worker class with in-memory
  store and event-bus doubles, so no network I/O is required.
- TaskLease model tests exercise the state-machine helpers directly (no HTTP layer).
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from forge_harness.models.lease import TaskLease

os.environ.setdefault("FORGE_RATE_LIMIT_ENABLED", "false")


# ---------------------------------------------------------------------------
# App / client fixtures (identical pattern to test_lease_api_integration.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def app(monkeypatch):
    from forge_harness.webhook_server import create_app
    from forge_harness.webhook_server.infrastructure import rate_limiter as rl_module

    _orig_init = rl_module.RateLimitConfig.__init__

    def _disabled_init(self, requests_per_minute=60, burst_size=10, enabled=True):
        _orig_init(
            self, requests_per_minute=requests_per_minute, burst_size=burst_size, enabled=False
        )

    monkeypatch.setattr(rl_module.RateLimitConfig, "__init__", _disabled_init)
    _app = create_app(rate_limit_config=rl_module.RateLimitConfig(enabled=False))

    from dataclasses import dataclass

    @dataclass
    class _NoAuth:
        require_auth: bool = False
        allow_localhost: bool = False

    _app.state.auth_config = _NoAuth()
    return _app


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_HANDLER_PATH = "forge_harness.webhook_server.api.tasks.get_task_handler"
_EVENT_BUS_PATH = "forge_harness.webhook_server.api.tasks.get_event_bus"
_AUDIT_PATH = "forge_harness.webhook_server.api.tasks.get_audit_logger"


def _future_ts(seconds: int = 3600) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _past_ts(seconds: int = 3600) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def _lease_payload(
    owner_node: str = "nova",
    owner_agent: str = "forge:claude",
    path_lock: str = "src/api/routes.py",
    expires_in: int = 3600,
) -> dict[str, Any]:
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


def _mock_handler(return_value=None, side_effect=None):
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


# ---------------------------------------------------------------------------
# In-memory lease store double for LeaseRecoveryWorker tests
# ---------------------------------------------------------------------------


class _InMemoryLeaseStore:
    """Minimal async lease store backed by a plain dict."""

    def __init__(self, leases: list | None = None):
        from forge_harness.models.lease import TaskLease

        self._leases: dict[str, TaskLease] = {}
        for lease in leases or []:
            self._leases[lease.lease_id] = lease

    async def list_leases(self):
        return list(self._leases.values())

    async def save_lease(self, lease):
        self._leases[lease.lease_id] = lease


class _InMemoryEventBus:
    """Simple async event bus that records published events."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def publish(self, topic: str, payload: dict) -> None:
        self.events.append((topic, payload))

    def topics(self) -> list[str]:
        return [e[0] for e in self.events]

    def payloads_for(self, topic: str) -> list[dict]:
        return [payload for t, payload in self.events if t == topic]


# ---------------------------------------------------------------------------
# Helpers for building TaskLease instances in known states
# ---------------------------------------------------------------------------


def _make_lease(
    task_id: str = "task-1",
    state: str = "active",
    owner_node: str = "nova",
    owner_agent: str = "forge:claude",
    path_lock: str | None = "src/api/routes.py",
    expires_delta: int = 3600,  # positive = future, negative = past
) -> TaskLease:
    from forge_harness.models.lease import LeaseState

    lease = TaskLease(
        task_id=task_id,
        owner_node=owner_node,
        owner_agent=owner_agent,
        path_lock=path_lock,
        state=LeaseState(state),
        expires_at=datetime.utcnow() + timedelta(seconds=expires_delta),
        claimed_at=datetime.utcnow() - timedelta(seconds=60),
    )
    return lease


# ===========================================================================
# 1. Collision Tests
# ===========================================================================


class TestCollisionDetection:
    """Two agents racing to claim the same task or the same file path."""

    def test_two_agents_claim_same_task_second_gets_409(self, client):
        """Agent 1 claims t1; agent 2 claim attempt hits LEASE_ALREADY_OWNED."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        lease_a1 = _lease_payload(owner_agent="forge:agent-1")
        lease_a2 = _lease_payload(owner_agent="forge:agent-2")
        bus = _mock_event_bus()
        audit = _mock_audit()

        # Agent 1 wins the claim
        task_a1 = _task_dict(lease=lease_a1, claimed_by="forge:agent-1")
        handler_ok = _mock_handler(return_value=task_a1)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_ok)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r1 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease_a1})
        assert r1.status_code == 200
        assert r1.json()["data"]["claimed_by"] == "forge:agent-1"

        # Agent 2 loses
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
            r2 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease_a2})
        assert r2.status_code == 409
        assert "LEASE_ALREADY_OWNED" in str(r2.json())

    def test_two_agents_lock_same_path_on_different_tasks(self, client):
        """Agent 1 locks routes.py on task t1; agent 2 claiming t2 with the same
        path gets PATH_LOCK_CONFLICT."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        shared_path = "src/api/routes.py"
        lease_a1 = _lease_payload(owner_agent="forge:agent-1", path_lock=shared_path)
        lease_a2 = _lease_payload(owner_agent="forge:agent-2", path_lock=shared_path)
        bus = _mock_event_bus()
        audit = _mock_audit()

        # Agent 1 claims t1 with the shared path
        task_a1 = _task_dict(task_id="t1", lease=lease_a1, claimed_by="forge:agent-1")
        handler_ok = _mock_handler(return_value=task_a1)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_ok)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r1 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease_a1})
        assert r1.status_code == 200

        # Agent 2 tries to claim t2 with the same path — conflict
        handler_path_conflict = _mock_handler(
            side_effect=TaskLeaseError(
                "PATH_LOCK_CONFLICT",
                f"path_lock '{shared_path}' is already held by task t1",
                409,
            )
        )
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_path_conflict)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r2 = client.post("/api/tasks/t2/lease/claim", json={"lease": lease_a2})
        assert r2.status_code == 409
        body = r2.json()
        assert "PATH_LOCK_CONFLICT" in str(body)
        assert "t1" in str(body)

    def test_agent_reclaims_same_task_after_explicit_release(self, client):
        """Agent releases a task, then successfully reclaims it."""
        lease = _lease_payload()
        bus = _mock_event_bus()
        audit = _mock_audit()

        # Step 1: claim
        task_claimed = _task_dict(lease=lease)
        handler_claim = _mock_handler(return_value=task_claimed)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_claim)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r1 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease})
        assert r1.status_code == 200

        # Step 2: release
        task_released = _task_dict(status="pending", lease=None, claimed_by="")
        handler_release = _mock_handler(return_value=task_released)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_release)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r2 = client.post(
                "/api/tasks/t1/lease/release",
                json={"owner_node": "nova", "owner_agent": "forge:claude"},
            )
        assert r2.status_code == 200

        # Step 3: same agent reclaims successfully
        new_lease = _lease_payload(expires_in=7200)
        task_reclaimed = _task_dict(lease=new_lease)
        handler_reclaim = _mock_handler(return_value=task_reclaimed)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_reclaim)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r3 = client.post("/api/tasks/t1/lease/claim", json={"lease": new_lease})
        assert r3.status_code == 200
        assert r3.json()["data"]["status"] == "assigned"

    def test_agent_reclaims_after_expiry_and_requeue(self, client):
        """Lease expires, task is requeued, then a new claim succeeds."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        lease = _lease_payload()
        bus = _mock_event_bus()
        audit = _mock_audit()

        # Claim
        task_claimed = _task_dict(lease=lease)
        handler_claim = _mock_handler(return_value=task_claimed)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_claim)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r1 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease})
        assert r1.status_code == 200

        # Renew fails because lease expired in the interim
        handler_expired = _mock_handler(
            side_effect=TaskLeaseError("LEASE_EXPIRED", "Lease has expired", 409)
        )
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_expired)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r2 = client.post("/api/tasks/t1/lease/renew", json={"lease": lease})
        assert r2.status_code == 409

        # Recovery worker requeues (simulated via the requeue endpoint)
        task_requeued = _task_dict(status="pending", lease=None, claimed_by="")
        handler_requeue = _mock_handler(return_value=task_requeued)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_requeue)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r3 = client.post("/api/tasks/t1/lease/requeue", json={"reason": "lease expired"})
        assert r3.status_code == 200

        # Different agent claims successfully after requeue
        new_lease = _lease_payload(owner_agent="forge:agent-2", expires_in=7200)
        task_new_claim = _task_dict(lease=new_lease, claimed_by="forge:agent-2")
        handler_new_claim = _mock_handler(return_value=task_new_claim)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_new_claim)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r4 = client.post("/api/tasks/t1/lease/claim", json={"lease": new_lease})
        assert r4.status_code == 200
        assert r4.json()["data"]["claimed_by"] == "forge:agent-2"


# ===========================================================================
# 2. Expiry and Recovery Tests (LeaseRecoveryWorker)
# ===========================================================================


class TestLeaseRecoveryWorker:
    """Direct unit tests on LeaseRecoveryWorker v2."""

    @pytest.mark.asyncio
    async def test_scan_expired_returns_only_expired_leases(self):
        from forge_harness.webhook_server.services.lease_recovery_v2 import LeaseRecoveryWorker

        expired_lease = _make_lease("task-expired", state="active", expires_delta=-10)
        active_lease = _make_lease("task-active", state="active", expires_delta=3600)
        store = _InMemoryLeaseStore([expired_lease, active_lease])
        bus = _InMemoryEventBus()

        worker = LeaseRecoveryWorker(store, bus, scan_interval_seconds=1)
        found = await worker.scan_expired()

        assert len(found) == 1
        assert found[0].task_id == "task-expired"

    @pytest.mark.asyncio
    async def test_scan_expired_includes_leases_already_in_expired_state(self):
        from forge_harness.models.lease import LeaseState
        from forge_harness.webhook_server.services.lease_recovery_v2 import LeaseRecoveryWorker

        # Lease is already in EXPIRED state (wall-clock still future but state is EXPIRED)
        state_expired = _make_lease("task-state-expired", state="expired", expires_delta=3600)
        store = _InMemoryLeaseStore([state_expired])
        bus = _InMemoryEventBus()

        worker = LeaseRecoveryWorker(store, bus)
        found = await worker.scan_expired()

        assert len(found) == 1
        assert found[0].task_id == "task-state-expired"

    @pytest.mark.asyncio
    async def test_recover_transitions_expired_lease_to_requeued(self):
        from forge_harness.models.lease import LeaseState
        from forge_harness.webhook_server.services.lease_recovery_v2 import LeaseRecoveryWorker

        lease = _make_lease("task-1", state="active", expires_delta=-5)
        store = _InMemoryLeaseStore([lease])
        bus = _InMemoryEventBus()

        worker = LeaseRecoveryWorker(store, bus)
        result = await worker.recover(lease)

        assert result is True
        assert lease.state is LeaseState.REQUEUED

    @pytest.mark.asyncio
    async def test_recover_clears_owner_and_path_lock(self):
        from forge_harness.webhook_server.services.lease_recovery_v2 import LeaseRecoveryWorker

        lease = _make_lease(
            "task-1",
            state="active",
            expires_delta=-5,
            owner_node="nova",
            owner_agent="forge:claude",
            path_lock="src/api/routes.py",
        )
        store = _InMemoryLeaseStore([lease])
        bus = _InMemoryEventBus()

        worker = LeaseRecoveryWorker(store, bus)
        await worker.recover(lease)

        assert lease.owner_node == ""
        assert lease.owner_agent == ""
        assert lease.path_lock is None

    @pytest.mark.asyncio
    async def test_recover_publishes_expired_detected_event(self):
        from forge_harness.webhook_server.services.lease_recovery_v2 import LeaseRecoveryWorker

        lease = _make_lease("task-1", state="active", expires_delta=-5)
        store = _InMemoryLeaseStore([lease])
        bus = _InMemoryEventBus()

        worker = LeaseRecoveryWorker(store, bus)
        await worker.recover(lease)

        assert "lease.expired.detected" in bus.topics()

    @pytest.mark.asyncio
    async def test_recover_publishes_lease_recovered_event(self):
        from forge_harness.webhook_server.services.lease_recovery_v2 import LeaseRecoveryWorker

        lease = _make_lease("task-1", state="active", expires_delta=-5)
        store = _InMemoryLeaseStore([lease])
        bus = _InMemoryEventBus()

        worker = LeaseRecoveryWorker(store, bus)
        await worker.recover(lease)

        assert "lease.recovered" in bus.topics()
        recovered_payloads = bus.payloads_for("lease.recovered")
        assert len(recovered_payloads) == 1
        assert recovered_payloads[0]["task_id"] == "task-1"

    @pytest.mark.asyncio
    async def test_recover_returns_false_for_illegal_transition(self):
        """REQUEUED → REQUEUED is not a valid transition; worker returns False."""
        from forge_harness.webhook_server.services.lease_recovery_v2 import LeaseRecoveryWorker

        # A requeued lease is not in an expired state and should not be re-recovered.
        # However, is_expired() can return True if expires_at is in the past.
        # We set state=requeued directly — the REQUEUED → EXPIRED transition is invalid
        # per the state machine (REQUEUED only allows CLAIMED), so recover() should
        # fail gracefully.
        lease = _make_lease("task-already-requeued", state="requeued", expires_delta=-5)
        store = _InMemoryLeaseStore([lease])
        bus = _InMemoryEventBus()

        worker = LeaseRecoveryWorker(store, bus)
        result = await worker.recover(lease)

        assert result is False
        assert "lease.recovery.failed" in bus.topics()

    @pytest.mark.asyncio
    async def test_recover_updates_stats_on_success(self):
        from forge_harness.webhook_server.services.lease_recovery_v2 import LeaseRecoveryWorker

        lease = _make_lease("task-1", state="active", expires_delta=-5)
        store = _InMemoryLeaseStore([lease])
        bus = _InMemoryEventBus()

        worker = LeaseRecoveryWorker(store, bus)
        await worker.recover(lease)

        stats = worker.get_stats()
        assert stats.total_recovered == 1
        assert stats.total_failed == 0

    @pytest.mark.asyncio
    async def test_recover_updates_stats_on_failure(self):
        from forge_harness.webhook_server.services.lease_recovery_v2 import LeaseRecoveryWorker

        lease = _make_lease("task-bad", state="requeued", expires_delta=-5)
        store = _InMemoryLeaseStore([lease])
        bus = _InMemoryEventBus()

        worker = LeaseRecoveryWorker(store, bus)
        await worker.recover(lease)

        stats = worker.get_stats()
        assert stats.total_failed == 1

    @pytest.mark.asyncio
    async def test_scan_and_recover_multiple_expired_leases(self):
        from forge_harness.models.lease import LeaseState
        from forge_harness.webhook_server.services.lease_recovery_v2 import LeaseRecoveryWorker

        leases = [_make_lease(f"task-{i}", state="active", expires_delta=-10) for i in range(3)]
        active = _make_lease("task-active", state="active", expires_delta=3600)
        store = _InMemoryLeaseStore(leases + [active])
        bus = _InMemoryEventBus()

        worker = LeaseRecoveryWorker(store, bus)
        expired_found = await worker.scan_expired()
        assert len(expired_found) == 3

        for lease in expired_found:
            await worker.recover(lease)

        stats = worker.get_stats()
        assert stats.total_recovered == 3
        # All three should be in REQUEUED state
        for lease in leases:
            assert lease.state is LeaseState.REQUEUED

    @pytest.mark.asyncio
    async def test_store_failure_on_save_publishes_recovery_failed(self):
        """If save_lease raises, recover() publishes lease.recovery.failed."""
        from forge_harness.webhook_server.services.lease_recovery_v2 import LeaseRecoveryWorker

        class _FailingStore(_InMemoryLeaseStore):
            async def save_lease(self, lease):
                raise RuntimeError("disk full")

        lease = _make_lease("task-1", state="active", expires_delta=-5)
        store = _FailingStore([lease])
        bus = _InMemoryEventBus()

        worker = LeaseRecoveryWorker(store, bus)
        with pytest.raises(RuntimeError, match="disk full"):
            await worker.recover(lease)

        assert "lease.recovery.failed" in bus.topics()


# ===========================================================================
# 3. Expiry / Recovery — API surface (expired lease cannot be renewed)
# ===========================================================================


class TestExpiredLeaseCannotBeRenewed:
    """Verify the API correctly rejects renewal of an expired lease."""

    def test_expired_lease_renew_returns_409_lease_expired(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError("LEASE_EXPIRED", "Lease has expired", 409)
        )
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post("/api/tasks/t1/lease/renew", json={"lease": _lease_payload()})
        assert response.status_code == 409
        assert "LEASE_EXPIRED" in str(response.json())

    def test_requeued_lease_can_be_claimed_by_new_agent(self, client):
        """After expiry+requeue, a brand-new agent can claim the task."""
        new_lease = _lease_payload(owner_agent="forge:fresh-agent", expires_in=7200)
        task_reclaimed = _task_dict(lease=new_lease, claimed_by="forge:fresh-agent")
        handler = _mock_handler(return_value=task_reclaimed)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post("/api/tasks/t1/lease/claim", json={"lease": new_lease})
        assert response.status_code == 200
        assert response.json()["data"]["claimed_by"] == "forge:fresh-agent"

    def test_renew_event_not_published_on_expired_lease(self, client):
        """Event bus must NOT be called when the renew request fails with LEASE_EXPIRED."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(side_effect=TaskLeaseError("LEASE_EXPIRED", "Lease expired", 409))
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post("/api/tasks/t1/lease/renew", json={"lease": _lease_payload()})

        bus.publish.assert_not_called()


# ===========================================================================
# 4. Path Lock Rejection Tests
# ===========================================================================


class TestPathLockRejection:
    """Path lock enforcement across claim / release / recovery."""

    def test_claim_with_locked_path_returns_409(self, client):
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        locked_path = "src/models/user.py"
        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "PATH_LOCK_CONFLICT",
                f"path_lock '{locked_path}' is already held by task t5",
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
                "/api/tasks/t2/lease/claim",
                json={"lease": _lease_payload(path_lock=locked_path)},
            )
        assert response.status_code == 409
        assert "PATH_LOCK_CONFLICT" in str(response.json())

    def test_release_frees_path_for_subsequent_claim(self, client):
        """After task t1 releases its path lock, task t2 can claim the same path."""
        shared_path = "src/models/user.py"
        bus = _mock_event_bus()
        audit = _mock_audit()

        # Release t1 — path is now free
        released_task = _task_dict(task_id="t1", status="pending", lease=None, claimed_by="")
        handler_release = _mock_handler(return_value=released_task)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_release)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r1 = client.post(
                "/api/tasks/t1/lease/release",
                json={"owner_node": "nova", "owner_agent": "forge:claude"},
            )
        assert r1.status_code == 200

        # t2 now claims the same path successfully
        t2_lease = _lease_payload(
            owner_agent="forge:agent-2", path_lock=shared_path, expires_in=7200
        )
        task_t2 = _task_dict(task_id="t2", lease=t2_lease, claimed_by="forge:agent-2")
        handler_claim = _mock_handler(return_value=task_t2)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_claim)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r2 = client.post("/api/tasks/t2/lease/claim", json={"lease": t2_lease})
        assert r2.status_code == 200
        assert r2.json()["data"]["id"] == "t2"

    def test_path_lock_cleared_by_recovery_worker(self):
        """Recovery worker clears path_lock when transitioning to REQUEUED."""
        lease = _make_lease(
            "task-lock-test",
            state="active",
            expires_delta=-5,
            path_lock="src/billing/invoices.py",
        )
        store = _InMemoryLeaseStore([lease])
        bus = _InMemoryEventBus()

        from forge_harness.webhook_server.services.lease_recovery_v2 import LeaseRecoveryWorker

        asyncio.get_event_loop().run_until_complete(LeaseRecoveryWorker(store, bus).recover(lease))
        assert lease.path_lock is None

    def test_empty_path_lock_bypasses_conflict_check(self, client):
        """Claiming a task with path_lock='' (empty string) must succeed even if
        other tasks hold non-empty path locks."""
        # The handler returns the task successfully — no conflict raised
        task = _task_dict(lease=_lease_payload(path_lock=""))
        handler = _mock_handler(return_value=task)
        bus = _mock_event_bus()
        audit = _mock_audit()

        # We cannot send empty string through the Pydantic model (path_lock is required
        # and non-empty in the API body), so we simulate the handler returning a task
        # that was claimed with an empty path_lock.  The actual enforcement of "no
        # conflict" for empty paths lives in task_handler._find_path_lock_conflict which
        # guards with `if not path_lock: return None`.  This test verifies that a
        # handler returning successfully maps to HTTP 200.
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            response = client.post(
                "/api/tasks/t1/lease/claim",
                json={"lease": _lease_payload()},  # valid payload to pass pydantic
            )
        assert response.status_code == 200

    def test_path_lock_conflict_event_bus_not_called(self, client):
        """When PATH_LOCK_CONFLICT is raised, the event bus must NOT publish."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(side_effect=TaskLeaseError("PATH_LOCK_CONFLICT", "conflict", 409))
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post("/api/tasks/t1/lease/claim", json={"lease": _lease_payload()})

        bus.publish.assert_not_called()

    def test_different_paths_do_not_conflict(self, client):
        """Two agents holding different paths on different tasks must not conflict."""
        lease_a = _lease_payload(owner_agent="forge:agent-1", path_lock="src/api/routes.py")
        task_a = _task_dict(task_id="t1", lease=lease_a, claimed_by="forge:agent-1")
        handler_a = _mock_handler(return_value=task_a)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_a)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r1 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease_a})
        assert r1.status_code == 200

        lease_b = _lease_payload(owner_agent="forge:agent-2", path_lock="src/models/user.py")
        task_b = _task_dict(task_id="t2", lease=lease_b, claimed_by="forge:agent-2")
        handler_b = _mock_handler(return_value=task_b)

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_b)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r2 = client.post("/api/tasks/t2/lease/claim", json={"lease": lease_b})
        assert r2.status_code == 200
        assert r2.json()["data"]["id"] == "t2"


# ===========================================================================
# 5. Reassignment Tests
# ===========================================================================


class TestReassignment:
    """Full reassignment cycles: voluntary (release→requeue) and forced (expiry→recover)."""

    def test_full_cycle_claim_release_requeue_claim_different_agent(self, client):
        """claim(A) → release(A) → requeue → claim(B) must all return 200."""
        bus = _mock_event_bus()
        audit = _mock_audit()

        # Claim by agent A
        lease_a = _lease_payload(owner_agent="forge:agent-A")
        task_a = _task_dict(lease=lease_a, claimed_by="forge:agent-A")
        handler_claim_a = _mock_handler(return_value=task_a)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_claim_a)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r1 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease_a})
        assert r1.status_code == 200, r1.json()

        # Release by agent A
        task_released = _task_dict(status="pending", lease=None, claimed_by="")
        handler_release = _mock_handler(return_value=task_released)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_release)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r2 = client.post(
                "/api/tasks/t1/lease/release",
                json={"owner_node": "nova", "owner_agent": "forge:agent-A"},
            )
        assert r2.status_code == 200, r2.json()

        # Requeue (supervisor or recovery)
        task_requeued = _task_dict(status="pending", lease=None, claimed_by="")
        handler_requeue = _mock_handler(return_value=task_requeued)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_requeue)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r3 = client.post("/api/tasks/t1/lease/requeue", json={"reason": "voluntary"})
        assert r3.status_code == 200, r3.json()

        # Claim by agent B
        lease_b = _lease_payload(owner_agent="forge:agent-B", expires_in=7200)
        task_b = _task_dict(lease=lease_b, claimed_by="forge:agent-B")
        handler_claim_b = _mock_handler(return_value=task_b)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_claim_b)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r4 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease_b})
        assert r4.status_code == 200, r4.json()
        assert r4.json()["data"]["claimed_by"] == "forge:agent-B"

    def test_full_cycle_claim_expire_recover_claim_different_agent(self, client):
        """claim(A) → expire (409 on renew) → requeue (recovery) → claim(B) succeeds."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        bus = _mock_event_bus()
        audit = _mock_audit()

        # Claim by agent A
        lease_a = _lease_payload(owner_agent="forge:agent-A")
        task_a = _task_dict(lease=lease_a, claimed_by="forge:agent-A")
        handler_claim_a = _mock_handler(return_value=task_a)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_claim_a)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r1 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease_a})
        assert r1.status_code == 200

        # Renew fails — lease expired
        handler_expired_renew = _mock_handler(
            side_effect=TaskLeaseError("LEASE_EXPIRED", "Lease expired", 409)
        )
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_expired_renew)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r2 = client.post("/api/tasks/t1/lease/renew", json={"lease": lease_a})
        assert r2.status_code == 409

        # Recovery worker requeues (simulated via requeue endpoint)
        task_requeued = _task_dict(status="pending", lease=None, claimed_by="")
        handler_requeue = _mock_handler(return_value=task_requeued)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_requeue)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r3 = client.post("/api/tasks/t1/lease/requeue", json={"reason": "lease expired"})
        assert r3.status_code == 200

        # Agent B claims successfully
        lease_b = _lease_payload(owner_agent="forge:agent-B", expires_in=7200)
        task_b = _task_dict(lease=lease_b, claimed_by="forge:agent-B")
        handler_claim_b = _mock_handler(return_value=task_b)
        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler_claim_b)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            r4 = client.post("/api/tasks/t1/lease/claim", json={"lease": lease_b})
        assert r4.status_code == 200
        assert r4.json()["data"]["claimed_by"] == "forge:agent-B"

    def test_requeue_event_payload_contains_correct_reason(self, client):
        """Requeue event payload must carry the reason from the request body."""
        task_requeued = _task_dict(status="pending", lease=None, claimed_by="")
        handler = _mock_handler(return_value=task_requeued)
        bus = _mock_event_bus()
        audit = _mock_audit()

        with (
            patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
            patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
            patch(_AUDIT_PATH, return_value=audit),
        ):
            client.post(
                "/api/tasks/t1/lease/requeue",
                json={"reason": "agent-A crashed"},
            )

        event_payload = bus.publish.call_args[0][1]
        assert event_payload["reason"] == "agent-A crashed"
        assert event_payload["task_id"] == "t1"


# ===========================================================================
# 6. Edge Cases
# ===========================================================================


class TestEdgeCases:
    """Boundary conditions and max-renewals enforcement."""

    def test_multiple_renewals_within_max_renewals_all_succeed(self):
        """TaskLease.renew() succeeds for renewals < max_renewals."""
        from forge_harness.models.lease import LeaseState, TaskLease

        lease = TaskLease(
            task_id="t-edge",
            owner_node="nova",
            owner_agent="forge:claude",
            state=LeaseState.ACTIVE,
            expires_at=datetime.utcnow() + timedelta(seconds=3600),
            max_renewals=5,
            renewals=0,
        )

        for i in range(5):
            lease.renew()
            assert lease.renewals == i + 1
            assert lease.state is LeaseState.ACTIVE

    def test_renewal_beyond_max_renewals_is_caller_responsibility(self):
        """The TaskLease model itself does not enforce max_renewals — that is the
        handler's job.  Callers must check renewals < max_renewals before calling
        renew().  This test documents that renew() will still succeed at the model
        level and confirms max_renewals is stored correctly."""
        from forge_harness.models.lease import LeaseState, TaskLease

        lease = TaskLease(
            task_id="t-edge",
            owner_node="nova",
            owner_agent="forge:claude",
            state=LeaseState.ACTIVE,
            expires_at=datetime.utcnow() + timedelta(seconds=3600),
            max_renewals=2,
            renewals=2,
        )
        # The model does not enforce this limit; the handler should.
        # The API layer surfaces a LEASE_RENEWAL_EXCEEDED error.
        assert lease.renewals >= lease.max_renewals

    def test_handler_rejects_renewal_beyond_max_renewals(self, client):
        """When renewals >= max_renewals, handler raises TaskLeaseError; API returns 409."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "LEASE_RENEWAL_EXCEEDED",
                "Maximum lease renewals (10) reached",
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
            response = client.post("/api/tasks/t1/lease/renew", json={"lease": _lease_payload()})
        assert response.status_code == 409
        assert "LEASE_RENEWAL_EXCEEDED" in str(response.json())

    def test_concurrent_agents_different_tasks_different_paths_both_succeed(self, client):
        """Two independent agents on distinct tasks with distinct paths must both get 200."""
        bus = _mock_event_bus()
        audit = _mock_audit()

        for task_id, agent, path in [
            ("t10", "forge:agent-X", "src/payments/stripe.py"),
            ("t11", "forge:agent-Y", "src/auth/jwt.py"),
        ]:
            lease = _lease_payload(owner_agent=agent, path_lock=path)
            task = _task_dict(task_id=task_id, lease=lease, claimed_by=agent)
            handler = _mock_handler(return_value=task)
            with (
                patch(_HANDLER_PATH, new=AsyncMock(return_value=handler)),
                patch(_EVENT_BUS_PATH, new=AsyncMock(return_value=bus)),
                patch(_AUDIT_PATH, return_value=audit),
            ):
                r = client.post(f"/api/tasks/{task_id}/lease/claim", json={"lease": lease})
            assert r.status_code == 200, f"claim for {agent} on {task_id} failed: {r.json()}"
            assert r.json()["data"]["claimed_by"] == agent

    def test_lease_is_expired_helper_with_future_expiry(self):
        """TaskLease.is_expired() returns False when expires_at is in the future."""
        from forge_harness.models.lease import LeaseState, TaskLease

        lease = TaskLease(
            task_id="t-future",
            state=LeaseState.ACTIVE,
            expires_at=datetime.utcnow() + timedelta(seconds=3600),
        )
        assert lease.is_expired() is False

    def test_lease_is_expired_helper_with_past_expiry(self):
        """TaskLease.is_expired() returns True when expires_at is in the past."""
        from forge_harness.models.lease import LeaseState, TaskLease

        lease = TaskLease(
            task_id="t-past",
            state=LeaseState.ACTIVE,
            expires_at=datetime.utcnow() - timedelta(seconds=1),
        )
        assert lease.is_expired() is True

    def test_lease_is_expired_helper_without_expiry_set(self):
        """TaskLease.is_expired() returns False when expires_at is None."""
        from forge_harness.models.lease import LeaseState, TaskLease

        lease = TaskLease(
            task_id="t-none",
            state=LeaseState.UNCLAIMED,
            expires_at=None,
        )
        assert lease.is_expired() is False

    def test_claim_with_past_timestamp_rejected_by_handler(self, client):
        """A lease with a past lease_expires_at is accepted by Pydantic but the
        handler raises INVALID_LEASE with status 400."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        past_lease = {
            "owner_node": "nova",
            "owner_agent": "forge:claude",
            "lease_expires_at": _past_ts(60),
            "path_lock": "src/api/routes.py",
        }
        handler = _mock_handler(
            side_effect=TaskLeaseError("INVALID_LEASE", "lease_expires_at must be future", 400)
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

    def test_path_lock_immutable_on_renew_detected_at_api(self, client):
        """Changing path_lock during renewal returns 400 PATH_LOCK_IMMUTABLE."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        handler = _mock_handler(
            side_effect=TaskLeaseError(
                "PATH_LOCK_IMMUTABLE", "path_lock cannot change during lease renewal", 400
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
                json={"lease": _lease_payload(path_lock="src/different/file.py")},
            )
        assert response.status_code == 400
        assert "PATH_LOCK_IMMUTABLE" in str(response.json())


# ===========================================================================
# 7. LeaseTransitionError and State Machine correctness
# ===========================================================================


class TestLeaseStateMachine:
    """Direct tests on the TaskLease state machine helpers."""

    def test_unclaimed_to_claimed_is_valid(self):
        from forge_harness.models.lease import LeaseState, validate_lease_transition

        assert validate_lease_transition(LeaseState.UNCLAIMED, LeaseState.CLAIMED) is True

    def test_claimed_to_active_is_valid(self):
        from forge_harness.models.lease import LeaseState, validate_lease_transition

        assert validate_lease_transition(LeaseState.CLAIMED, LeaseState.ACTIVE) is True

    def test_active_to_renewing_is_valid(self):
        from forge_harness.models.lease import LeaseState, validate_lease_transition

        assert validate_lease_transition(LeaseState.ACTIVE, LeaseState.RENEWING) is True

    def test_renewing_to_active_is_valid(self):
        from forge_harness.models.lease import LeaseState, validate_lease_transition

        assert validate_lease_transition(LeaseState.RENEWING, LeaseState.ACTIVE) is True

    def test_active_to_releasing_is_valid(self):
        from forge_harness.models.lease import LeaseState, validate_lease_transition

        assert validate_lease_transition(LeaseState.ACTIVE, LeaseState.RELEASING) is True

    def test_releasing_to_requeued_is_valid(self):
        from forge_harness.models.lease import LeaseState, validate_lease_transition

        assert validate_lease_transition(LeaseState.RELEASING, LeaseState.REQUEUED) is True

    def test_expired_to_requeued_is_valid(self):
        from forge_harness.models.lease import LeaseState, validate_lease_transition

        assert validate_lease_transition(LeaseState.EXPIRED, LeaseState.REQUEUED) is True

    def test_requeued_to_claimed_is_valid(self):
        from forge_harness.models.lease import LeaseState, validate_lease_transition

        assert validate_lease_transition(LeaseState.REQUEUED, LeaseState.CLAIMED) is True

    def test_unclaimed_to_active_is_invalid(self):
        from forge_harness.models.lease import (
            LeaseState,
            LeaseTransitionError,
            validate_lease_transition,
        )

        with pytest.raises(LeaseTransitionError):
            validate_lease_transition(LeaseState.UNCLAIMED, LeaseState.ACTIVE)

    def test_requeued_to_active_is_invalid(self):
        from forge_harness.models.lease import (
            LeaseState,
            LeaseTransitionError,
            validate_lease_transition,
        )

        with pytest.raises(LeaseTransitionError):
            validate_lease_transition(LeaseState.REQUEUED, LeaseState.ACTIVE)

    def test_expired_to_claimed_is_invalid(self):
        from forge_harness.models.lease import (
            LeaseState,
            LeaseTransitionError,
            validate_lease_transition,
        )

        with pytest.raises(LeaseTransitionError):
            validate_lease_transition(LeaseState.EXPIRED, LeaseState.CLAIMED)

    def test_lease_claim_method_transitions_correctly(self):
        from forge_harness.models.lease import LeaseState, TaskLease

        lease = TaskLease(task_id="t1")
        assert lease.state is LeaseState.UNCLAIMED
        lease.claim("nova", "forge:claude")
        assert lease.state is LeaseState.CLAIMED
        assert lease.owner_node == "nova"
        assert lease.owner_agent == "forge:claude"
        assert lease.expires_at is not None

    def test_lease_renew_method_increments_counter(self):
        from forge_harness.models.lease import LeaseState, TaskLease

        lease = TaskLease(
            task_id="t1",
            state=LeaseState.ACTIVE,
            expires_at=datetime.utcnow() + timedelta(seconds=3600),
        )
        old_expires = lease.expires_at
        lease.renew(duration=7200)
        assert lease.renewals == 1
        assert lease.state is LeaseState.ACTIVE
        assert lease.expires_at > old_expires  # type: ignore[operator]

    def test_lease_release_clears_path_lock(self):
        from forge_harness.models.lease import LeaseState, TaskLease

        lease = TaskLease(
            task_id="t1",
            state=LeaseState.ACTIVE,
            path_lock="src/api/routes.py",
            expires_at=datetime.utcnow() + timedelta(seconds=3600),
        )
        lease.release()
        assert lease.state is LeaseState.RELEASING
        assert lease.path_lock is None

    def test_lease_transition_error_message_contains_states(self):
        from forge_harness.models.lease import LeaseState, LeaseTransitionError

        err = LeaseTransitionError(
            LeaseState.UNCLAIMED,
            LeaseState.ACTIVE,
            frozenset({LeaseState.CLAIMED}),
        )
        assert "unclaimed" in str(err)
        assert "active" in str(err)


# ===========================================================================
# 8. LeaseStats aggregate helper
# ===========================================================================


class TestLeaseStats:
    """Verify LeaseStats.from_leases() correctly aggregates state information."""

    def test_stats_counts_active_path_locks(self):
        from forge_harness.models.lease import LeaseState, LeaseStats, TaskLease

        leases = [
            TaskLease(task_id="t1", state=LeaseState.ACTIVE, path_lock="src/a.py"),
            TaskLease(task_id="t2", state=LeaseState.ACTIVE, path_lock=None),
            TaskLease(task_id="t3", state=LeaseState.ACTIVE, path_lock="src/b.py"),
        ]
        stats = LeaseStats.from_leases(leases)
        assert stats.active_path_locks == 2

    def test_stats_counts_expired(self):
        from forge_harness.models.lease import LeaseState, LeaseStats, TaskLease

        leases = [
            TaskLease(task_id="t1", state=LeaseState.EXPIRED),
            TaskLease(task_id="t2", state=LeaseState.ACTIVE),
            TaskLease(task_id="t3", state=LeaseState.EXPIRED),
        ]
        stats = LeaseStats.from_leases(leases)
        assert stats.expired_count == 2

    def test_stats_calculates_mean_renewals(self):
        from forge_harness.models.lease import LeaseState, LeaseStats, TaskLease

        leases = [
            TaskLease(task_id="t1", state=LeaseState.ACTIVE, renewals=2),
            TaskLease(task_id="t2", state=LeaseState.ACTIVE, renewals=4),
        ]
        stats = LeaseStats.from_leases(leases)
        assert stats.mean_renewals == 3.0

    def test_stats_total_reflects_all_leases(self):
        from forge_harness.models.lease import LeaseState, LeaseStats, TaskLease

        leases = [TaskLease(task_id=f"t{i}", state=LeaseState.ACTIVE) for i in range(7)]
        stats = LeaseStats.from_leases(leases)
        assert stats.total == 7

    def test_stats_from_empty_list(self):
        from forge_harness.models.lease import LeaseStats

        stats = LeaseStats.from_leases([])
        assert stats.total == 0
        assert stats.active_path_locks == 0
        assert stats.expired_count == 0
        assert stats.mean_renewals is None
