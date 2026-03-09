"""Integration tests for FORGE task lease system (CP-2008).

Tests the full lease lifecycle through TaskQueueHandler with a real
file-backed TaskQueue in a temporary directory.  No mocks for the handler
or queue layer — only the network/Redis layer is bypassed by not passing a
state_store, which forces the handler to fall through to file storage.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from forge_harness.task_queue import TaskQueue
from forge_harness.webhook_server.handlers.task_handler import (
    TaskLeaseError,
    TaskQueueHandler,
)
from forge_harness.webhook_server.services.lease_recovery import (
    StaleLeaseRecoveryService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _future_lease(
    *,
    owner_node: str = "nova",
    owner_agent: str = "forge:codex",
    path_lock: str = "harness/forge_harness",
    minutes: int = 30,
) -> dict:
    """Return a valid lease dict that expires *minutes* from now."""
    return {
        "owner_node": owner_node,
        "owner_agent": owner_agent,
        "path_lock": path_lock,
        "lease_expires_at": (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat(),
    }


def _expired_lease(
    *,
    owner_node: str = "nova",
    owner_agent: str = "forge:codex",
    path_lock: str = "harness/forge_harness",
    minutes: int = 5,
) -> dict:
    """Return a lease dict whose expiry is *minutes* in the past."""
    return {
        "owner_node": owner_node,
        "owner_agent": owner_agent,
        "path_lock": path_lock,
        "lease_expires_at": (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(),
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def task_queue(tmp_path: Path) -> TaskQueue:
    """Isolated file-based TaskQueue backed by tmp_path."""
    return TaskQueue(tmp_path / ".forge/tasks")


@pytest.fixture
def handler(task_queue: TaskQueue) -> TaskQueueHandler:
    """TaskQueueHandler wired to the isolated queue; no Redis."""
    return TaskQueueHandler(state_store=None, task_queue=task_queue)


# ---------------------------------------------------------------------------
# 1. Claim lease success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_claim_lease_success(handler: TaskQueueHandler) -> None:
    """Create a task, claim it with a valid lease, verify task.lease is populated."""
    task = await handler.create_task(
        subject="Implement auth module",
        description="JWT-based authentication",
        priority="high",
    )
    task_id = task["id"]

    lease = _future_lease(path_lock="harness/auth")
    claimed = await handler.claim_task_with_lease(task_id, lease)

    assert claimed is not None, "claim_task_with_lease should return the updated task"
    assert claimed["status"] == "assigned"
    assert claimed["claimed_by"] == "forge:codex"

    stored_lease = claimed["lease"]
    assert isinstance(stored_lease, dict), "lease should be persisted as a dict"
    assert stored_lease["owner_node"] == "nova"
    assert stored_lease["owner_agent"] == "forge:codex"
    assert stored_lease["path_lock"] == "harness/auth"


# ---------------------------------------------------------------------------
# 2. Duplicate claim returns conflict
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_claim_returns_conflict(handler: TaskQueueHandler) -> None:
    """Second agent trying to claim an already-leased task should raise LEASE_ALREADY_OWNED."""
    task = await handler.create_task(
        subject="Database migrations",
        description="Run schema migration",
        priority="medium",
    )
    task_id = task["id"]

    first_lease = _future_lease(
        owner_node="nova",
        owner_agent="forge:codex",
        path_lock="migrations/v2",
    )
    await handler.claim_task_with_lease(task_id, first_lease)

    second_lease = _future_lease(
        owner_node="prya",
        owner_agent="forge:gemini",
        path_lock="migrations/v2",
    )
    with pytest.raises(TaskLeaseError) as exc_info:
        await handler.claim_task_with_lease(task_id, second_lease)

    err = exc_info.value
    assert err.code == "LEASE_ALREADY_OWNED", (
        f"Expected LEASE_ALREADY_OWNED but got {err.code!r}"
    )
    assert err.status_code == 409


# ---------------------------------------------------------------------------
# 3. path_lock conflict prevents concurrent claim on a *different* task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_path_lock_prevents_concurrent_claim(handler: TaskQueueHandler) -> None:
    """Two tasks sharing the same path_lock cannot be simultaneously leased."""
    task_a = await handler.create_task(
        subject="Feature A",
        description="First feature touching the same code path",
        priority="high",
    )
    task_b = await handler.create_task(
        subject="Feature B",
        description="Second feature touching the same code path",
        priority="high",
    )

    shared_lock = "forge-terminal/ios"

    lease_a = _future_lease(
        owner_node="nova",
        owner_agent="forge:codex",
        path_lock=shared_lock,
    )
    # Claim task A — should succeed
    claimed_a = await handler.claim_task_with_lease(task_a["id"], lease_a)
    assert claimed_a is not None
    assert claimed_a["lease"]["path_lock"] == shared_lock

    # Attempt to claim task B with the same path_lock — should conflict
    lease_b = _future_lease(
        owner_node="prya",
        owner_agent="forge:claude",
        path_lock=shared_lock,
    )
    with pytest.raises(TaskLeaseError) as exc_info:
        await handler.claim_task_with_lease(task_b["id"], lease_b)

    err = exc_info.value
    assert err.code == "PATH_LOCK_CONFLICT", (
        f"Expected PATH_LOCK_CONFLICT but got {err.code!r}"
    )
    assert err.status_code == 409


# ---------------------------------------------------------------------------
# 4. Renew extends expiry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_renew_extends_expiry(handler: TaskQueueHandler) -> None:
    """Renewing a lease with a later expiry should update lease_expires_at."""
    task = await handler.create_task(
        subject="Long-running refactor",
        description="Refactor core module",
        priority="medium",
    )
    task_id = task["id"]

    initial_lease = _future_lease(
        owner_node="nova",
        owner_agent="forge:codex",
        path_lock="harness/core",
        minutes=10,
    )
    claimed = await handler.claim_task_with_lease(task_id, initial_lease)
    original_expiry = claimed["lease"]["lease_expires_at"]

    # Renew with a 60-minute extension
    renewed_lease = _future_lease(
        owner_node="nova",
        owner_agent="forge:codex",
        path_lock="harness/core",
        minutes=60,
    )
    renewed = await handler.renew_task_lease(task_id, renewed_lease)

    assert renewed is not None, "renew_task_lease should return the updated task"
    new_expiry = renewed["lease"]["lease_expires_at"]

    # The new expiry should differ from the original
    assert new_expiry != original_expiry, (
        "Renewed lease should have a different expiry timestamp"
    )

    # Parse and compare to confirm the new expiry is later
    original_dt = datetime.fromisoformat(original_expiry.replace("Z", "+00:00"))
    new_dt = datetime.fromisoformat(new_expiry.replace("Z", "+00:00"))
    assert new_dt > original_dt, "Renewed expiry must be later than the original expiry"


# ---------------------------------------------------------------------------
# 5. Release clears lease
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_release_clears_lease(handler: TaskQueueHandler) -> None:
    """Releasing a lease should set task.lease to None and reset status."""
    task = await handler.create_task(
        subject="Content pipeline",
        description="Generate blog content",
        priority="low",
    )
    task_id = task["id"]

    lease = _future_lease(
        owner_node="nova",
        owner_agent="forge:codex",
        path_lock="content/blog",
    )
    await handler.claim_task_with_lease(task_id, lease)

    released = await handler.release_task_lease(
        task_id,
        owner_node="nova",
        owner_agent="forge:codex",
    )

    assert released is not None, "release_task_lease should return the updated task"
    assert released["lease"] is None, "lease must be cleared after release"
    assert released["status"] == "pending", (
        "Task should return to pending status after lease release"
    )
    # claimed_by should be cleared (empty string mapped from file storage)
    assert not released["claimed_by"], (
        "claimed_by should be empty after lease release"
    )


# ---------------------------------------------------------------------------
# 6. Requeue returns task to pending
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_requeue_returns_to_pending(handler: TaskQueueHandler) -> None:
    """Requeueing a leased task must reset status to pending and clear lease."""
    task = await handler.create_task(
        subject="Deploy service",
        description="Deploy to Railway",
        priority="critical",
    )
    task_id = task["id"]

    lease = _future_lease(
        owner_node="prya",
        owner_agent="forge:claude",
        path_lock="deploy/railway",
    )
    claimed = await handler.claim_task_with_lease(task_id, lease)
    assert claimed["status"] == "assigned"

    requeued = await handler.requeue_task(task_id)

    assert requeued is not None, "requeue_task should return the updated task"
    assert requeued["status"] == "pending", (
        "Task status must be pending after requeue"
    )
    assert requeued["lease"] is None, "lease must be None after requeue"
    assert not requeued["claimed_by"], "claimed_by must be empty after requeue"


# ---------------------------------------------------------------------------
# 7. Expired lease auto-recovery via StaleLeaseRecoveryService
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_lease_auto_recovery(handler: TaskQueueHandler) -> None:
    """StaleLeaseRecoveryService should requeue tasks with expired leases."""
    # Create a task with an already-expired lease by going through update_task
    # directly (bypass claim validation which rejects expired leases)
    task = await handler.create_task(
        subject="Stale task",
        description="Task whose lease has already expired",
        priority="medium",
    )
    task_id = task["id"]

    expired = _expired_lease(
        owner_node="nova",
        owner_agent="forge:codex",
        path_lock="harness/stale",
        minutes=10,
    )
    # Inject the expired lease + assigned status directly
    await handler.update_task(
        task_id,
        {
            "lease": expired,
            "status": "assigned",
            "claimed_by": "forge:codex",
        },
    )

    # Confirm the task is currently assigned with an expired lease
    pre_recovery = await handler.get_task(task_id)
    assert pre_recovery is not None
    assert pre_recovery["status"] == "assigned"
    assert pre_recovery["lease"] is not None

    # Run the recovery service
    event_bus = AsyncMock()
    service = StaleLeaseRecoveryService(
        task_handler=handler,
        event_bus=event_bus,
    )
    recovered = await service.scan_and_recover()

    assert recovered == 1, (
        f"Expected 1 recovered task, got {recovered}"
    )

    # Verify the task was requeued
    post_recovery = await handler.get_task(task_id)
    assert post_recovery is not None
    assert post_recovery["status"] == "pending", (
        "Task should be pending after auto-recovery"
    )
    assert post_recovery["lease"] is None, (
        "lease should be cleared after auto-recovery"
    )

    # Verify events were published
    assert event_bus.publish.await_count == 2, (
        "Expected task.lease.expired and task.requeued events"
    )
    first_event_name = event_bus.publish.await_args_list[0].args[0]
    second_event_name = event_bus.publish.await_args_list[1].args[0]
    assert first_event_name == "task.lease.expired"
    assert second_event_name == "task.requeued"

    first_payload = event_bus.publish.await_args_list[0].args[1]
    assert first_payload["task_id"] == task_id
    assert first_payload["owner_node"] == "nova"
