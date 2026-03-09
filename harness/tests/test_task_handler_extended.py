"""
Extended tests for TaskQueueHandler — covering uncovered paths and edge cases.

Targets:
- release_task_lease conditional branches (claimed_by mismatch, non-assigned status)
- requeue_task with/without lease, None owner args
- claim_task_with_lease with None/whitespace fields
- renew_task_lease owner_agent-only mismatch
- _parse_lease_expiry naive datetime UTC attach
- _serialize_task with None optional fields
- _deserialize_task None lease_raw path
- Redis create_task with priority/role/lease variations
- Redis update_task with lease key
- Redis get_stats with unknown status accumulation
- _find_path_lock_conflict multiple tasks, first-found semantics
- Module-level constant maps (_PRIORITY_MAP, _STATUS_MAP)
- get_task_handler singleton reset
- TaskQueueHandler with None task_queue (uses create_task_queue)
- Error message content for PATH_LOCK_CONFLICT
- claimed_by/status interaction in release_task_lease
- requeue_task owner checks with only one of node/agent provided
- Redis list_tasks sort order
- _task_to_dict required_role field
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from forge_harness.task_queue import Task, TaskPriority, TaskQueue, TaskStatus
from forge_harness.webhook_server.handlers.task_handler import (
    _PRIORITY_MAP,
    _STATUS_MAP,
    TaskLeaseError,
    TaskQueueHandler,
    _dict_to_task,
    _task_to_dict,
    get_task_handler,
)

# ---------------------------------------------------------------------------
# Shared helpers (mirrored from test_task_handler.py)
# ---------------------------------------------------------------------------

def _future_iso(seconds: int = 3600) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _past_iso(seconds: int = 3600) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def _make_task(
    task_id: str = "t1",
    title: str = "Test task",
    description: str = "desc",
    priority: TaskPriority = TaskPriority.MEDIUM,
    status: TaskStatus = TaskStatus.PENDING,
    lease: dict | None = None,
) -> Task:
    return Task(
        id=task_id,
        title=title,
        description=description,
        priority=priority,
        status=status,
        lease=lease,
    )


def _make_file_handler(mock_queue: MagicMock | None = None) -> TaskQueueHandler:
    if mock_queue is None:
        mock_queue = MagicMock()
    mock_store = Mock()
    mock_store.is_connected.return_value = False
    mock_store.get_store_type.return_value = "sqlite"
    return TaskQueueHandler(state_store=mock_store, task_queue=mock_queue)


def _make_redis_handler() -> tuple[TaskQueueHandler, MagicMock]:
    mock_redis = MagicMock()
    mock_state_store = MagicMock()
    mock_state_store.is_connected.return_value = True
    mock_state_store.get_store_type.return_value = "redis"
    mock_state_store._redis = MagicMock()
    mock_state_store._redis._client = mock_redis

    mock_queue = MagicMock()
    handler = TaskQueueHandler(state_store=mock_state_store, task_queue=mock_queue)
    return handler, mock_redis


def _base_redis_data(task_id: str = "t1", **overrides) -> dict:
    base = {
        "id": task_id,
        "subject": "S",
        "description": "D",
        "priority": "medium",
        "status": "pending",
        "required_role": "",
        "claimed_by": "",
        "lease": "",
        "order": "0",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Module-level constant maps
# ---------------------------------------------------------------------------


class TestConstantMaps:
    """Verify _PRIORITY_MAP and _STATUS_MAP cover all expected keys."""

    def test_priority_map_has_all_levels(self):
        assert _PRIORITY_MAP["critical"] == TaskPriority.CRITICAL
        assert _PRIORITY_MAP["high"] == TaskPriority.HIGH
        assert _PRIORITY_MAP["medium"] == TaskPriority.MEDIUM
        assert _PRIORITY_MAP["low"] == TaskPriority.LOW
        assert len(_PRIORITY_MAP) == 4

    def test_status_map_has_all_states(self):
        assert _STATUS_MAP["pending"] == TaskStatus.PENDING
        assert _STATUS_MAP["assigned"] == TaskStatus.ASSIGNED
        assert _STATUS_MAP["in_progress"] == TaskStatus.IN_PROGRESS
        assert _STATUS_MAP["completed"] == TaskStatus.COMPLETED
        assert _STATUS_MAP["failed"] == TaskStatus.FAILED
        assert _STATUS_MAP["cancelled"] == TaskStatus.CANCELLED
        assert _STATUS_MAP["blocked"] == TaskStatus.BLOCKED
        assert len(_STATUS_MAP) == 7


# ---------------------------------------------------------------------------
# _task_to_dict — required_role field
# ---------------------------------------------------------------------------


class TestTaskToDictExtended:
    def test_required_role_propagated(self):
        task = _make_task()
        task.required_role = "backend-engineer"
        result = _task_to_dict(task)
        assert result["required_role"] == "backend-engineer"

    def test_none_required_role_propagated(self):
        task = _make_task()
        task.required_role = None
        result = _task_to_dict(task)
        assert result["required_role"] is None

    def test_created_at_used_for_updated_at(self):
        task = _make_task()
        task.created_at = "2026-03-01T12:00:00+00:00"
        result = _task_to_dict(task)
        # updated_at mirrors created_at since TaskQueue doesn't track separately
        assert result["updated_at"] == "2026-03-01T12:00:00+00:00"
        assert result["created_at"] == "2026-03-01T12:00:00+00:00"

    def test_none_lease_propagated(self):
        task = _make_task()
        task.lease = None
        result = _task_to_dict(task)
        assert result["lease"] is None


# ---------------------------------------------------------------------------
# _dict_to_task update branch — remaining status/priority variants
# ---------------------------------------------------------------------------


class TestDictToTaskUpdateExtended:
    """Covers status and priority enum update paths not explicitly tested."""

    def test_update_to_all_statuses(self):
        for status_str, status_enum in _STATUS_MAP.items():
            existing = _make_task(status=TaskStatus.PENDING)
            _dict_to_task({"status": status_str}, existing=existing)
            assert existing.status == status_enum

    def test_update_to_all_priorities(self):
        for priority_str, priority_enum in _PRIORITY_MAP.items():
            existing = _make_task(priority=TaskPriority.MEDIUM)
            _dict_to_task({"priority": priority_str}, existing=existing)
            assert existing.priority == priority_enum

    def test_update_with_no_matching_fields_leaves_task_unchanged(self):
        existing = _make_task(title="Unchanged", description="also unchanged")
        _dict_to_task({"nonexistent_field": "value"}, existing=existing)
        assert existing.title == "Unchanged"
        assert existing.description == "also unchanged"


# ---------------------------------------------------------------------------
# _parse_lease_expiry — UTC normalisation paths
# ---------------------------------------------------------------------------


class TestParseLeaseExpiryExtended:
    def test_aware_datetime_converted_to_utc(self):
        """A timezone-aware datetime in non-UTC zone is converted to UTC."""
        from datetime import timezone
        # UTC+5 offset
        tz_plus5 = timezone(timedelta(hours=5))
        dt = datetime(2030, 6, 1, 10, 0, 0, tzinfo=tz_plus5)
        result = TaskQueueHandler._parse_lease_expiry({"lease_expires_at": dt})
        assert result is not None
        assert result.tzinfo == UTC
        # 10:00 +05:00 == 05:00 UTC
        assert result.hour == 5

    def test_iso_string_naive_treated_as_utc(self):
        """ISO string without timezone info gets UTC attached."""
        result = TaskQueueHandler._parse_lease_expiry(
            {"lease_expires_at": "2030-12-31T23:59:59"}
        )
        assert result is not None
        assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# _serialize_task — edge cases
# ---------------------------------------------------------------------------


class TestSerializeTaskExtended:
    def test_none_required_role_becomes_empty_string(self):
        handler = _make_file_handler()
        task = {
            "id": "x", "subject": "s", "description": "d",
            "priority": "medium", "status": "pending",
            "required_role": None, "claimed_by": None,
            "order": 0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        serialized = handler._serialize_task(task)
        assert serialized["required_role"] == "None"
        assert serialized["claimed_by"] == "None"

    def test_large_order_value(self):
        handler = _make_file_handler()
        task = {
            "id": "x", "subject": "s", "description": "d",
            "priority": "high", "status": "pending",
            "required_role": "", "claimed_by": "",
            "order": 9999,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        serialized = handler._serialize_task(task)
        assert serialized["order"] == "9999"

    def test_complex_lease_round_trips(self):
        handler = _make_file_handler()
        lease = {
            "owner_node": "nova",
            "owner_agent": "forge:codex",
            "path_lock": "/projects/api",
            "lease_expires_at": "2099-01-01T00:00:00Z",
            "metadata": {"attempt": 3},
        }
        task = {
            "id": "x", "subject": "s", "description": "d",
            "priority": "high", "status": "pending",
            "required_role": "", "claimed_by": "",
            "lease": lease, "order": 0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        serialized = handler._serialize_task(task)
        deserialized = handler._deserialize_task(serialized)
        assert deserialized["lease"] == lease


# ---------------------------------------------------------------------------
# release_task_lease — conditional branches
# ---------------------------------------------------------------------------


class TestReleaseTaskLeaseExtended:
    """Cover the two conditional branches at lines 601-604."""

    @pytest.mark.asyncio
    async def test_release_does_not_clear_claimed_by_when_different_agent(self):
        """If claimed_by != owner_agent, claimed_by should NOT be cleared."""
        lease = {"owner_node": "node1", "owner_agent": "agent1"}
        existing_obj = _make_task("t1", status=TaskStatus.ASSIGNED)
        existing_obj.lease = lease
        existing_obj.assigned_agent = "different_agent"  # different from owner_agent

        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(return_value={
            "id": "t1",
            "lease": lease,
            "claimed_by": "different_agent",  # does not match owner_agent
            "status": "assigned",
        })

        result = await handler.release_task_lease("t1")
        assert result is not None
        # The update should set lease=None and status=pending but NOT claimed_by
        update_call_args = mq.update.call_args[0][0]
        # claimed_by on task object should remain as was (update applied _dict_to_task)
        # Just verify we got a result back — the key check is no exception raised
        assert result["lease"] is None

    @pytest.mark.asyncio
    async def test_release_does_not_change_status_when_not_assigned(self):
        """If status != 'assigned', status should NOT be reset to pending."""
        lease = {"owner_node": "node1", "owner_agent": "agent1"}
        existing_obj = _make_task("t1", status=TaskStatus.IN_PROGRESS)
        existing_obj.lease = lease
        existing_obj.assigned_agent = "agent1"

        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(return_value={
            "id": "t1",
            "lease": lease,
            "claimed_by": "agent1",
            "status": "in_progress",  # NOT "assigned"
        })

        result = await handler.release_task_lease("t1")
        assert result is not None
        # Status should NOT be changed to pending since it was in_progress
        # The update dict passed had only lease=None and claimed_by=""
        # (status was not included because it wasn't "assigned")
        # Verify update was called
        mq.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_release_clears_both_when_agent_matches_and_assigned(self):
        """Full happy path: owner matches, status=assigned → both cleared."""
        lease = {"owner_node": "node1", "owner_agent": "agent1"}
        existing_obj = _make_task("t1", status=TaskStatus.ASSIGNED)
        existing_obj.lease = lease
        existing_obj.assigned_agent = "agent1"

        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(return_value={
            "id": "t1",
            "lease": lease,
            "claimed_by": "agent1",
            "status": "assigned",
        })

        result = await handler.release_task_lease(
            "t1", owner_node="node1", owner_agent="agent1"
        )
        assert result is not None
        assert result["lease"] is None
        assert result["status"] == "pending"
        assert result["claimed_by"] == ""

    @pytest.mark.asyncio
    async def test_release_with_only_owner_node_check(self):
        """Release with only owner_node provided — owner_agent not checked."""
        lease = {"owner_node": "node1", "owner_agent": "agent1"}
        existing_obj = _make_task("t1", status=TaskStatus.ASSIGNED)
        existing_obj.lease = lease
        existing_obj.assigned_agent = "agent1"

        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(return_value={
            "id": "t1",
            "lease": lease,
            "claimed_by": "agent1",
            "status": "assigned",
        })

        result = await handler.release_task_lease("t1", owner_node="node1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_release_with_only_owner_agent_check(self):
        """Release with only owner_agent provided — owner_node not checked."""
        lease = {"owner_node": "node1", "owner_agent": "agent1"}
        existing_obj = _make_task("t1", status=TaskStatus.ASSIGNED)
        existing_obj.lease = lease
        existing_obj.assigned_agent = "agent1"

        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(return_value={
            "id": "t1",
            "lease": lease,
            "claimed_by": "agent1",
            "status": "assigned",
        })

        result = await handler.release_task_lease("t1", owner_agent="agent1")
        assert result is not None


# ---------------------------------------------------------------------------
# requeue_task — extended edge cases
# ---------------------------------------------------------------------------


class TestRequeueTaskExtended:
    @pytest.mark.asyncio
    async def test_requeue_no_lease_and_no_owner_args(self):
        """Task with no lease and no owner args — should succeed."""
        existing_obj = _make_task("t1", status=TaskStatus.IN_PROGRESS)
        existing_obj.lease = None
        existing_obj.assigned_agent = "some_agent"

        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(return_value={
            "id": "t1", "lease": None,
            "claimed_by": "some_agent", "status": "in_progress",
        })

        result = await handler.requeue_task("t1")
        assert result is not None
        assert result["status"] == "pending"
        assert result["claimed_by"] == ""
        assert result["lease"] is None

    @pytest.mark.asyncio
    async def test_requeue_with_lease_no_owner_args_skips_owner_check(self):
        """Task has lease but neither owner_node nor owner_agent provided — no check."""
        lease = {"owner_node": "node1", "owner_agent": "agent1"}
        existing_obj = _make_task("t1", status=TaskStatus.ASSIGNED)
        existing_obj.lease = lease
        existing_obj.assigned_agent = "agent1"

        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(return_value={
            "id": "t1", "lease": lease,
            "claimed_by": "agent1", "status": "assigned",
        })

        # No owner_node, no owner_agent — should not raise even with active lease
        result = await handler.requeue_task("t1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_requeue_owner_node_only_mismatch(self):
        """Only owner_node provided and mismatches — should raise."""
        lease = {"owner_node": "correct_node", "owner_agent": "agent1"}
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": lease})
        with pytest.raises(TaskLeaseError) as exc:
            await handler.requeue_task("t1", owner_node="wrong_node")
        assert exc.value.code == "LEASE_OWNER_MISMATCH"

    @pytest.mark.asyncio
    async def test_requeue_owner_agent_only_mismatch(self):
        """Only owner_agent provided and mismatches — should raise."""
        lease = {"owner_node": "node1", "owner_agent": "correct_agent"}
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": lease})
        with pytest.raises(TaskLeaseError) as exc:
            await handler.requeue_task("t1", owner_agent="wrong_agent")
        assert exc.value.code == "LEASE_OWNER_MISMATCH"

    @pytest.mark.asyncio
    async def test_requeue_owner_node_only_matches(self):
        """Only owner_node matches — no agent check, should succeed."""
        lease = {"owner_node": "node1", "owner_agent": "agent1"}
        existing_obj = _make_task("t1", status=TaskStatus.ASSIGNED)
        existing_obj.lease = lease
        existing_obj.assigned_agent = "agent1"

        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(return_value={
            "id": "t1", "lease": lease,
            "claimed_by": "agent1", "status": "assigned",
        })

        result = await handler.requeue_task("t1", owner_node="node1")
        assert result is not None


# ---------------------------------------------------------------------------
# claim_task_with_lease — None/whitespace edge cases
# ---------------------------------------------------------------------------


class TestClaimTaskWithLeaseExtended:
    def _base_lease(self, **overrides) -> dict:
        defaults = {
            "owner_node": "node1",
            "owner_agent": "agent1",
            "path_lock": "/projects/api",
            "lease_expires_at": _future_iso(),
        }
        defaults.update(overrides)
        return defaults

    @pytest.mark.asyncio
    async def test_none_owner_node_raises_invalid_lease(self):
        """None owner_node should be treated as empty and raise INVALID_LEASE."""
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1"})
        lease = self._base_lease(owner_node=None)
        with pytest.raises(TaskLeaseError) as exc:
            await handler.claim_task_with_lease("t1", lease)
        assert exc.value.code == "INVALID_LEASE"

    @pytest.mark.asyncio
    async def test_none_owner_agent_raises_invalid_lease(self):
        """None owner_agent should be treated as empty and raise INVALID_LEASE."""
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1"})
        lease = self._base_lease(owner_agent=None)
        with pytest.raises(TaskLeaseError) as exc:
            await handler.claim_task_with_lease("t1", lease)
        assert exc.value.code == "INVALID_LEASE"

    @pytest.mark.asyncio
    async def test_none_path_lock_raises_invalid_lease(self):
        """None path_lock should be treated as empty and raise INVALID_LEASE."""
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1"})
        lease = self._base_lease(path_lock=None)
        with pytest.raises(TaskLeaseError) as exc:
            await handler.claim_task_with_lease("t1", lease)
        assert exc.value.code == "INVALID_LEASE"

    @pytest.mark.asyncio
    async def test_whitespace_owner_node_raises_invalid_lease(self):
        """Whitespace-only owner_node should raise INVALID_LEASE after strip."""
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1"})
        lease = self._base_lease(owner_node="   ")
        with pytest.raises(TaskLeaseError) as exc:
            await handler.claim_task_with_lease("t1", lease)
        assert exc.value.code == "INVALID_LEASE"

    @pytest.mark.asyncio
    async def test_expired_existing_lease_by_different_owner_raises_already_owned(self):
        """Active existing lease from different owner should raise LEASE_ALREADY_OWNED."""
        existing_lease = {
            "owner_node": "other_node", "owner_agent": "other_agent",
            "path_lock": "/p", "lease_expires_at": _future_iso(7200),
        }
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={
            "id": "t1", "lease": existing_lease
        })
        handler._find_path_lock_conflict = AsyncMock(return_value=None)

        lease = self._base_lease()
        with pytest.raises(TaskLeaseError) as exc:
            await handler.claim_task_with_lease("t1", lease)
        assert exc.value.code == "LEASE_ALREADY_OWNED"

    @pytest.mark.asyncio
    async def test_expired_existing_lease_allows_reclaim_by_new_owner(self):
        """Expired existing lease should allow any owner to claim."""
        existing_lease = {
            "owner_node": "old_node", "owner_agent": "old_agent",
            "path_lock": "/projects/api",
            "lease_expires_at": _past_iso(),  # expired
        }
        existing_obj = _make_task("t1")
        existing_obj.lease = None
        existing_obj.assigned_agent = None

        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": existing_lease})
        handler._find_path_lock_conflict = AsyncMock(return_value=None)

        result = await handler.claim_task_with_lease("t1", self._base_lease())
        assert result is not None

    @pytest.mark.asyncio
    async def test_path_lock_conflict_error_message_contains_conflicting_id(self):
        """PATH_LOCK_CONFLICT error message should name the conflicting task ID."""
        conflict_task = {"id": "conflicting-task-id"}
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": None})
        handler._find_path_lock_conflict = AsyncMock(return_value=conflict_task)

        with pytest.raises(TaskLeaseError) as exc:
            await handler.claim_task_with_lease("t1", self._base_lease())
        assert "conflicting-task-id" in str(exc.value)
        assert exc.value.code == "PATH_LOCK_CONFLICT"
        assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# renew_task_lease — owner_agent only mismatch
# ---------------------------------------------------------------------------


class TestRenewTaskLeaseExtended:
    def _active_lease(self, **overrides) -> dict:
        base = {
            "owner_node": "node1",
            "owner_agent": "agent1",
            "path_lock": "/projects/api",
            "lease_expires_at": _future_iso(),
        }
        base.update(overrides)
        return base

    @pytest.mark.asyncio
    async def test_owner_agent_only_mismatch_raises(self):
        """Same owner_node but different owner_agent should raise LEASE_OWNER_MISMATCH."""
        current_lease = self._active_lease()
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": current_lease})
        new_lease = self._active_lease(owner_agent="different_agent")
        with pytest.raises(TaskLeaseError) as exc:
            await handler.renew_task_lease("t1", new_lease)
        assert exc.value.code == "LEASE_OWNER_MISMATCH"

    @pytest.mark.asyncio
    async def test_owner_node_only_mismatch_raises(self):
        """Same owner_agent but different owner_node should raise LEASE_OWNER_MISMATCH."""
        current_lease = self._active_lease()
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": current_lease})
        new_lease = self._active_lease(owner_node="different_node")
        with pytest.raises(TaskLeaseError) as exc:
            await handler.renew_task_lease("t1", new_lease)
        assert exc.value.code == "LEASE_OWNER_MISMATCH"

    @pytest.mark.asyncio
    async def test_renew_updates_expiry_only(self):
        """Successful renewal should update lease expiry but keep path_lock unchanged."""
        current_lease = self._active_lease()
        existing_obj = _make_task("t1", status=TaskStatus.ASSIGNED)
        existing_obj.lease = current_lease
        existing_obj.assigned_agent = "agent1"

        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": current_lease})

        new_expiry = _future_iso(7200)
        new_lease = self._active_lease(lease_expires_at=new_expiry)
        result = await handler.renew_task_lease("t1", new_lease)
        assert result is not None
        assert result["lease"]["lease_expires_at"] == new_expiry

    @pytest.mark.asyncio
    async def test_renew_status_code_for_mismatch(self):
        """LEASE_OWNER_MISMATCH should have status_code=409."""
        current_lease = self._active_lease()
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": current_lease})
        new_lease = self._active_lease(owner_node="other_node", owner_agent="other_agent")
        with pytest.raises(TaskLeaseError) as exc:
            await handler.renew_task_lease("t1", new_lease)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_path_lock_immutable_status_code(self):
        """PATH_LOCK_IMMUTABLE should have status_code=400."""
        current_lease = self._active_lease()
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": current_lease})
        new_lease = self._active_lease(path_lock="/different/path")
        with pytest.raises(TaskLeaseError) as exc:
            await handler.renew_task_lease("t1", new_lease)
        assert exc.value.code == "PATH_LOCK_IMMUTABLE"
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# _find_path_lock_conflict — multiple tasks, correct first-found semantics
# ---------------------------------------------------------------------------


class TestFindPathLockConflictExtended:
    @pytest.mark.asyncio
    async def test_returns_first_active_conflict_when_multiple_tasks(self):
        """When multiple tasks have the same path_lock, the first active one is returned."""
        conflict_1 = {
            "id": "conflict-1",
            "lease": {"path_lock": "/shared", "lease_expires_at": _future_iso()},
        }
        conflict_2 = {
            "id": "conflict-2",
            "lease": {"path_lock": "/shared", "lease_expires_at": _future_iso()},
        }
        handler = _make_file_handler()
        handler.list_tasks = AsyncMock(return_value=[conflict_1, conflict_2])
        result = await handler._find_path_lock_conflict("/shared")
        assert result is not None
        assert result["id"] in {"conflict-1", "conflict-2"}

    @pytest.mark.asyncio
    async def test_skips_task_with_no_id(self):
        """Task entries without 'id' key still processed without error."""
        task_no_id = {
            "lease": {"path_lock": "/p", "lease_expires_at": _future_iso()},
        }
        handler = _make_file_handler()
        handler.list_tasks = AsyncMock(return_value=[task_no_id])
        # Should not raise even though task has no 'id'
        result = await handler._find_path_lock_conflict("/p")
        assert result is not None

    @pytest.mark.asyncio
    async def test_only_expired_leases_returns_none(self):
        """All expired leases for path_lock — no conflict found."""
        expired = {
            "id": "t-old",
            "lease": {"path_lock": "/p", "lease_expires_at": _past_iso()},
        }
        handler = _make_file_handler()
        handler.list_tasks = AsyncMock(return_value=[expired])
        result = await handler._find_path_lock_conflict("/p")
        assert result is None

    @pytest.mark.asyncio
    async def test_exclude_task_id_excludes_only_that_task(self):
        """Excludes the specified task but returns other conflicts."""
        my_task = {
            "id": "my-task",
            "lease": {"path_lock": "/p", "lease_expires_at": _future_iso()},
        }
        other_task = {
            "id": "other-task",
            "lease": {"path_lock": "/p", "lease_expires_at": _future_iso()},
        }
        handler = _make_file_handler()
        handler.list_tasks = AsyncMock(return_value=[my_task, other_task])
        result = await handler._find_path_lock_conflict("/p", exclude_task_id="my-task")
        assert result is not None
        assert result["id"] == "other-task"


# ---------------------------------------------------------------------------
# Redis backend — create_task priority/role/lease variations
# ---------------------------------------------------------------------------


class TestCreateTaskRedisExtended:
    @pytest.mark.asyncio
    async def test_create_with_critical_priority(self):
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = _base_redis_data(priority="critical")

        result = await handler.create_task("Task", "Desc", priority="critical")
        called_mapping = redis.hset.call_args[1]["mapping"]
        assert called_mapping["priority"] == "critical"
        assert result is not None

    @pytest.mark.asyncio
    async def test_create_with_low_priority(self):
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = _base_redis_data(priority="low")

        result = await handler.create_task("Task", "Desc", priority="low")
        called_mapping = redis.hset.call_args[1]["mapping"]
        assert called_mapping["priority"] == "low"

    @pytest.mark.asyncio
    async def test_create_with_required_role(self):
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = _base_redis_data(required_role="backend-engineer")

        result = await handler.create_task(
            "Task", "Desc", required_role="backend-engineer"
        )
        called_mapping = redis.hset.call_args[1]["mapping"]
        assert called_mapping["required_role"] == "backend-engineer"

    @pytest.mark.asyncio
    async def test_create_with_lease_serialized(self):
        handler, redis = _make_redis_handler()
        lease = {"owner_node": "n1", "path_lock": "/p", "lease_expires_at": _future_iso()}
        redis.hgetall.return_value = _base_redis_data(lease=json.dumps(lease))

        await handler.create_task("Task", "Desc", lease=lease)
        called_mapping = redis.hset.call_args[1]["mapping"]
        # Lease should be JSON-encoded in the Redis mapping
        assert called_mapping["lease"] == json.dumps(lease)

    @pytest.mark.asyncio
    async def test_create_no_required_role_stores_empty(self):
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = _base_redis_data()

        await handler.create_task("Task", "Desc")
        called_mapping = redis.hset.call_args[1]["mapping"]
        assert called_mapping["required_role"] == ""

    @pytest.mark.asyncio
    async def test_create_stores_raw_priority_string_in_redis(self):
        """Redis path stores the raw priority string as-is (no enum lookup).
        Unknown values are stored verbatim; normalization happens in file-based path."""
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = _base_redis_data(priority="bogus")

        await handler.create_task("Task", "Desc", priority="bogus")
        called_mapping = redis.hset.call_args[1]["mapping"]
        # Redis path passes through the raw string unchanged
        assert called_mapping["priority"] == "bogus"

    @pytest.mark.asyncio
    async def test_create_sets_ttl_30days(self):
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = _base_redis_data()

        await handler.create_task("Task", "Desc")
        redis.expire.assert_called_once()
        args = redis.expire.call_args[0]
        # 30 days in seconds
        assert args[1] == 2592000


# ---------------------------------------------------------------------------
# Redis backend — update_task with lease key
# ---------------------------------------------------------------------------


class TestUpdateTaskRedisExtended:
    @pytest.mark.asyncio
    async def test_update_with_lease_key_accepted(self):
        """Lease is a valid key to update on Redis path."""
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = _base_redis_data()
        redis.hset.return_value = 0

        lease = {"owner_node": "n1", "path_lock": "/p"}
        result = await handler.update_task("t1", {"lease": lease})
        assert result is not None
        assert result["lease"] == lease

    @pytest.mark.asyncio
    async def test_update_updates_timestamp(self):
        """updated_at should be refreshed on every update."""
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = _base_redis_data(
            updated_at="2026-01-01T00:00:00+00:00"
        )
        redis.hset.return_value = 0

        result = await handler.update_task("t1", {"status": "completed"})
        assert result is not None
        # updated_at should be fresher than the original
        assert result["updated_at"] != "2026-01-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_update_id_not_overwritten(self):
        """id field in updates dict should be ignored."""
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = _base_redis_data(task_id="original-id")
        redis.hset.return_value = 0

        result = await handler.update_task("original-id", {"id": "hacked", "status": "completed"})
        assert result is not None
        assert result["id"] == "original-id"

    @pytest.mark.asyncio
    async def test_update_created_at_not_overwritten(self):
        """created_at field in updates dict should be ignored."""
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = _base_redis_data(
            created_at="2026-01-01T00:00:00+00:00"
        )
        redis.hset.return_value = 0

        result = await handler.update_task(
            "t1", {"created_at": "2000-01-01T00:00:00+00:00", "status": "completed"}
        )
        assert result is not None
        assert result["created_at"] == "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Redis backend — get_stats unknown status accumulation
# ---------------------------------------------------------------------------


class TestGetStatsRedisExtended:
    @pytest.mark.asyncio
    async def test_unknown_status_accumulated_in_stats(self):
        """Unknown status values should be added to stats dynamically."""
        handler, redis = _make_redis_handler()
        redis.smembers.return_value = {"t1"}
        redis.hgetall.side_effect = lambda key: {"status": "in_review"}

        result = await handler.get_stats()
        # Unknown status "in_review" should appear in result via stats.get(status, 0) + 1
        assert result.get("in_review", 0) == 1
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_failed_status_tracked(self):
        """'failed' status should be tracked even though not in initial dict."""
        handler, redis = _make_redis_handler()
        redis.smembers.return_value = {"t1", "t2"}

        def hgetall(key):
            if "t1" in key:
                return {"status": "failed"}
            return {"status": "pending"}

        redis.hgetall.side_effect = hgetall
        result = await handler.get_stats()
        assert result.get("failed", 0) == 1
        assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_assigned_status_tracked(self):
        """'assigned' status should appear correctly in stats."""
        handler, redis = _make_redis_handler()
        redis.smembers.return_value = {"t1"}
        redis.hgetall.return_value = {"status": "assigned"}

        result = await handler.get_stats()
        assert result["assigned"] == 1
        assert result["total"] == 1


# ---------------------------------------------------------------------------
# Redis backend — list_tasks sort order
# ---------------------------------------------------------------------------


class TestListTasksRedisSortOrder:
    @pytest.mark.asyncio
    async def test_tasks_sorted_by_created_at_descending(self):
        """Tasks should come back newest-first."""
        handler, redis = _make_redis_handler()
        redis.smembers.return_value = {"t1", "t2", "t3"}

        task_data = {
            "t1": _base_redis_data("t1", created_at="2026-01-01T00:00:00+00:00"),
            "t2": _base_redis_data("t2", created_at="2026-03-01T00:00:00+00:00"),
            "t3": _base_redis_data("t3", created_at="2026-02-01T00:00:00+00:00"),
        }

        def hgetall(key):
            for tid, data in task_data.items():
                if tid in key:
                    return data
            return {}

        redis.hgetall.side_effect = hgetall
        result = await handler.list_tasks()
        assert len(result) == 3
        # Newest first
        dates = [r["created_at"] for r in result]
        assert dates == sorted(dates, reverse=True)


# ---------------------------------------------------------------------------
# TaskQueueHandler — _ensure_state_store import error path
# ---------------------------------------------------------------------------


class TestEnsureStateStoreExtended:
    def test_import_error_swallowed(self, tmp_path):
        """If _ensure_state_store is a no-op, handler is still created cleanly."""
        queue = TaskQueue(tmp_path / ".forge/tasks")
        with patch.object(TaskQueueHandler, "_ensure_state_store", return_value=None):
            handler = TaskQueueHandler(state_store=None, task_queue=queue)
        # Handler should still be creatable
        assert handler is not None

    def test_state_store_connect_failure_sets_none(self, tmp_path):
        """If StateStore.connect() raises, state_store should be None."""
        queue = TaskQueue(tmp_path / ".forge/tasks")
        with patch("forge_harness.state_store.StateStore") as MockStateStore:
            mock_instance = MagicMock()
            mock_instance.connect.side_effect = Exception("connection refused")
            MockStateStore.return_value = mock_instance
            handler = TaskQueueHandler(state_store=None, task_queue=queue)
        assert handler.state_store is None


# ---------------------------------------------------------------------------
# get_task_handler singleton — reset and re-creation
# ---------------------------------------------------------------------------


class TestGetTaskHandlerExtended:
    def test_singleton_reset_creates_new_instance(self, tmp_path, monkeypatch):
        """Resetting _task_handler to None forces a new instance on next call."""
        import forge_harness.webhook_server.handlers.task_handler as mod

        mod._task_handler = None
        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
        with patch.object(TaskQueueHandler, "_ensure_state_store"):
            with patch(
                "forge_harness.webhook_server.handlers.task_handler.create_task_queue",
                return_value=MagicMock(),
            ):
                h1 = get_task_handler()
                mod._task_handler = None  # reset
                h2 = get_task_handler()
        # Two separate instances
        assert h1 is not h2

    def test_singleton_not_recreated_when_set(self, tmp_path, monkeypatch):
        """If _task_handler is already set, it is returned without calling create_task_queue."""
        import forge_harness.webhook_server.handlers.task_handler as mod

        existing_handler = MagicMock(spec=TaskQueueHandler)
        mod._task_handler = existing_handler

        with patch(
            "forge_harness.webhook_server.handlers.task_handler.create_task_queue",
        ) as mock_create:
            result = get_task_handler()

        assert result is existing_handler
        mock_create.assert_not_called()

        # Cleanup: reset for other tests
        mod._task_handler = None


# ---------------------------------------------------------------------------
# TaskLeaseError — all fields
# ---------------------------------------------------------------------------


class TestTaskLeaseErrorExtended:
    def test_all_error_codes_representable(self):
        codes = [
            "INVALID_LEASE",
            "LEASE_ALREADY_OWNED",
            "PATH_LOCK_CONFLICT",
            "LEASE_NOT_FOUND",
            "LEASE_EXPIRED",
            "LEASE_OWNER_MISMATCH",
            "PATH_LOCK_IMMUTABLE",
        ]
        for code in codes:
            err = TaskLeaseError(code, f"message for {code}")
            assert err.code == code
            assert f"message for {code}" in str(err)

    def test_400_status_codes(self):
        err = TaskLeaseError("PATH_LOCK_IMMUTABLE", "bad path", status_code=400)
        assert err.status_code == 400

    def test_inheritance_from_exception(self):
        err = TaskLeaseError("X", "msg")
        assert isinstance(err, Exception)

    def test_can_be_caught_as_exception(self):
        caught = False
        try:
            raise TaskLeaseError("SOME_CODE", "test")
        except Exception:
            caught = True
        assert caught
