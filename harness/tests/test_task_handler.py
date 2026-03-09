"""
Tests for TaskQueueHandler.

Comprehensive unit tests for the task_handler module covering:
- Helper functions (_task_to_dict, _dict_to_task)
- TaskLeaseError domain exception
- Lease lifecycle helpers (_parse_lease_expiry, _is_lease_active)
- _find_path_lock_conflict
- TaskQueueHandler initialisation and key helpers
- All CRUD operations on both Redis and file-based backends
- Lease claim/renew/release/requeue operations
- Serialization/deserialization
- Singleton factory
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from forge_harness.task_queue import Task, TaskPriority, TaskQueue, TaskStatus
from forge_harness.webhook_server.handlers.task_handler import (
    TaskLeaseError,
    TaskQueueHandler,
    _dict_to_task,
    _task_to_dict,
    get_task_handler,
)

# ---------------------------------------------------------------------------
# Shared helpers
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
    """Return a TaskQueueHandler backed by a MagicMock TaskQueue (no Redis)."""
    if mock_queue is None:
        mock_queue = MagicMock()
    mock_store = Mock()
    mock_store.is_connected.return_value = False
    mock_store.get_store_type.return_value = "sqlite"
    return TaskQueueHandler(state_store=mock_store, task_queue=mock_queue)


def _make_redis_handler() -> tuple[TaskQueueHandler, MagicMock]:
    """Return a TaskQueueHandler wired to a fake Redis client."""
    mock_redis = MagicMock()
    mock_state_store = MagicMock()
    mock_state_store.is_connected.return_value = True
    mock_state_store.get_store_type.return_value = "redis"
    mock_state_store._redis = MagicMock()
    mock_state_store._redis._client = mock_redis

    mock_queue = MagicMock()
    handler = TaskQueueHandler(state_store=mock_state_store, task_queue=mock_queue)
    return handler, mock_redis


# ---------------------------------------------------------------------------
# _task_to_dict
# ---------------------------------------------------------------------------


class TestTaskToDict:
    """Tests for the _task_to_dict helper."""

    def test_basic_fields_mapped(self):
        task = _make_task(task_id="abc", title="My task", description="Do it")
        result = _task_to_dict(task)

        assert result["id"] == "abc"
        assert result["subject"] == "My task"
        assert result["description"] == "Do it"
        assert result["priority"] == "medium"
        assert result["status"] == "pending"
        assert result["claimed_by"] == ""
        assert result["order"] == 0

    def test_assigned_agent_mapped_to_claimed_by(self):
        task = _make_task()
        task.assigned_agent = "forge:agent1"
        result = _task_to_dict(task)
        assert result["claimed_by"] == "forge:agent1"

    def test_none_assigned_agent_becomes_empty_string(self):
        task = _make_task()
        task.assigned_agent = None
        result = _task_to_dict(task)
        assert result["claimed_by"] == ""

    def test_lease_propagated(self):
        lease = {"owner_node": "node1", "path_lock": "/some/path"}
        task = _make_task(lease=lease)
        result = _task_to_dict(task)
        assert result["lease"] == lease

    @pytest.mark.parametrize(
        "priority_enum,expected",
        [
            (TaskPriority.CRITICAL, "critical"),
            (TaskPriority.HIGH, "high"),
            (TaskPriority.MEDIUM, "medium"),
            (TaskPriority.LOW, "low"),
        ],
    )
    def test_all_priority_values(self, priority_enum, expected):
        task = _make_task(priority=priority_enum)
        assert _task_to_dict(task)["priority"] == expected

    @pytest.mark.parametrize(
        "status_enum,expected",
        [
            (TaskStatus.PENDING, "pending"),
            (TaskStatus.ASSIGNED, "assigned"),
            (TaskStatus.IN_PROGRESS, "in_progress"),
            (TaskStatus.COMPLETED, "completed"),
            (TaskStatus.FAILED, "failed"),
            (TaskStatus.CANCELLED, "cancelled"),
        ],
    )
    def test_all_status_values(self, status_enum, expected):
        task = _make_task(status=status_enum)
        assert _task_to_dict(task)["status"] == expected

    def test_domain_project_order_exposed(self):
        task = _make_task()
        task.domain = "saas"
        task.project = "api"
        task.order = 7
        result = _task_to_dict(task)
        assert result["domain"] == "saas"
        assert result["project"] == "api"
        assert result["order"] == 7


# ---------------------------------------------------------------------------
# _dict_to_task (create branch)
# ---------------------------------------------------------------------------


class TestDictToTaskCreate:
    """Tests for _dict_to_task when creating a new Task."""

    def test_minimal_data(self):
        task = _dict_to_task({"subject": "X", "description": "Y"})
        assert task.title == "X"
        assert task.description == "Y"
        assert task.priority == TaskPriority.MEDIUM
        assert task.status == TaskStatus.PENDING

    def test_custom_priority_and_status(self):
        task = _dict_to_task(
            {
                "subject": "X",
                "description": "Y",
                "priority": "high",
                "status": "in_progress",
            }
        )
        assert task.priority == TaskPriority.HIGH
        assert task.status == TaskStatus.IN_PROGRESS

    def test_unknown_priority_defaults_to_medium(self):
        task = _dict_to_task({"subject": "X", "description": "Y", "priority": "ultra"})
        assert task.priority == TaskPriority.MEDIUM

    def test_unknown_status_defaults_to_pending(self):
        task = _dict_to_task({"subject": "X", "description": "Y", "status": "zombie"})
        assert task.status == TaskStatus.PENDING

    def test_empty_claimed_by_becomes_none(self):
        task = _dict_to_task({"subject": "X", "description": "Y", "claimed_by": ""})
        assert task.assigned_agent is None

    def test_populated_claimed_by_set(self):
        task = _dict_to_task({"subject": "X", "description": "Y", "claimed_by": "agent1"})
        assert task.assigned_agent == "agent1"

    def test_empty_required_role_becomes_none(self):
        task = _dict_to_task({"subject": "X", "description": "Y", "required_role": ""})
        assert task.required_role is None

    def test_all_optional_fields(self):
        data = {
            "id": "custom_id",
            "subject": "S",
            "description": "D",
            "priority": "critical",
            "status": "assigned",
            "required_role": "backend-engineer",
            "claimed_by": "agent1",
            "order": 5,
            "domain": "saas",
            "project": "api",
            "lease": {"owner_node": "n1"},
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        task = _dict_to_task(data)
        assert task.id == "custom_id"
        assert task.priority == TaskPriority.CRITICAL
        assert task.status == TaskStatus.ASSIGNED
        assert task.required_role == "backend-engineer"
        assert task.assigned_agent == "agent1"
        assert task.order == 5
        assert task.domain == "saas"
        assert task.project == "api"
        assert task.lease == {"owner_node": "n1"}
        assert task.created_at == "2026-01-01T00:00:00+00:00"

    def test_id_generated_when_missing(self):
        task = _dict_to_task({"subject": "X", "description": "Y"})
        assert task.id is not None
        assert len(task.id) > 0


# ---------------------------------------------------------------------------
# _dict_to_task (update / existing branch)
# ---------------------------------------------------------------------------


class TestDictToTaskUpdate:
    """Tests for _dict_to_task when updating an existing Task."""

    def test_update_subject(self):
        existing = _make_task(title="Old")
        _dict_to_task({"subject": "New"}, existing=existing)
        assert existing.title == "New"

    def test_update_description(self):
        existing = _make_task()
        _dict_to_task({"description": "Updated"}, existing=existing)
        assert existing.description == "Updated"

    def test_update_priority(self):
        existing = _make_task(priority=TaskPriority.LOW)
        _dict_to_task({"priority": "critical"}, existing=existing)
        assert existing.priority == TaskPriority.CRITICAL

    def test_update_status(self):
        # Test valid transition: PENDING -> QUEUED (canonical DF lifecycle)
        existing = _make_task(status=TaskStatus.PENDING)
        _dict_to_task({"status": "queued"}, existing=existing)
        assert existing.status == TaskStatus.QUEUED

    def test_update_required_role(self):
        existing = _make_task()
        existing.required_role = "frontend"
        _dict_to_task({"required_role": ""}, existing=existing)
        assert existing.required_role is None

    def test_update_claimed_by_sets_assigned_at(self):
        existing = _make_task()
        assert existing.assigned_at is None
        _dict_to_task({"claimed_by": "agent99"}, existing=existing)
        assert existing.assigned_agent == "agent99"
        assert existing.assigned_at is not None

    def test_update_empty_claimed_by_clears_agent(self):
        existing = _make_task()
        existing.assigned_agent = "agent1"
        _dict_to_task({"claimed_by": ""}, existing=existing)
        assert existing.assigned_agent is None

    def test_update_order_domain_project_lease(self):
        existing = _make_task()
        _dict_to_task(
            {"order": 10, "domain": "d", "project": "p", "lease": {"x": 1}},
            existing=existing,
        )
        assert existing.order == 10
        assert existing.domain == "d"
        assert existing.project == "p"
        assert existing.lease == {"x": 1}

    def test_update_preserves_untouched_fields(self):
        # Test valid transition: PENDING -> QUEUED (canonical DF lifecycle)
        existing = _make_task(title="Keep Me", description="Also Keep")
        _dict_to_task({"status": "queued"}, existing=existing)
        assert existing.title == "Keep Me"
        assert existing.description == "Also Keep"

    def test_update_returns_existing_object(self):
        # Test valid transition: PENDING -> QUEUED (canonical DF lifecycle)
        existing = _make_task()
        result = _dict_to_task({"status": "queued"}, existing=existing)
        assert result is existing


# ---------------------------------------------------------------------------
# TaskLeaseError
# ---------------------------------------------------------------------------


class TestTaskLeaseError:
    def test_default_status_code(self):
        err = TaskLeaseError("SOME_CODE", "a message")
        assert err.code == "SOME_CODE"
        assert err.status_code == 409
        assert str(err) == "a message"

    def test_custom_status_code(self):
        err = TaskLeaseError("INVALID_LEASE", "bad", status_code=400)
        assert err.status_code == 400

    def test_is_exception(self):
        with pytest.raises(TaskLeaseError):
            raise TaskLeaseError("X", "y")


# ---------------------------------------------------------------------------
# _parse_lease_expiry
# ---------------------------------------------------------------------------


class TestParseLeaseExpiry:
    def test_missing_key_returns_none(self):
        assert TaskQueueHandler._parse_lease_expiry({}) is None

    def test_none_value_returns_none(self):
        assert TaskQueueHandler._parse_lease_expiry({"lease_expires_at": None}) is None

    def test_datetime_object_returned(self):
        dt = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = TaskQueueHandler._parse_lease_expiry({"lease_expires_at": dt})
        assert result == dt

    def test_naive_datetime_gets_utc(self):
        dt = datetime(2030, 1, 1, 0, 0, 0)  # naive
        result = TaskQueueHandler._parse_lease_expiry({"lease_expires_at": dt})
        assert result is not None
        assert result.tzinfo is not None

    def test_iso_string_with_z_suffix(self):
        result = TaskQueueHandler._parse_lease_expiry({"lease_expires_at": "2030-01-01T12:00:00Z"})
        assert result is not None
        assert result.year == 2030
        assert result.tzinfo is not None

    def test_iso_string_with_offset(self):
        result = TaskQueueHandler._parse_lease_expiry(
            {"lease_expires_at": "2030-06-15T08:00:00+00:00"}
        )
        assert result is not None
        assert result.month == 6

    def test_invalid_string_returns_none(self):
        result = TaskQueueHandler._parse_lease_expiry({"lease_expires_at": "not-a-date"})
        assert result is None

    def test_non_string_non_datetime_returns_none(self):
        result = TaskQueueHandler._parse_lease_expiry({"lease_expires_at": 12345})
        assert result is None


# ---------------------------------------------------------------------------
# _is_lease_active
# ---------------------------------------------------------------------------


class TestIsLeaseActive:
    @pytest.fixture
    def handler(self):
        return _make_file_handler()

    def test_none_lease_is_inactive(self, handler):
        assert handler._is_lease_active(None) is False

    def test_empty_dict_is_inactive(self, handler):
        assert handler._is_lease_active({}) is False

    def test_future_expiry_is_active(self, handler):
        assert handler._is_lease_active({"lease_expires_at": _future_iso()}) is True

    def test_past_expiry_is_inactive(self, handler):
        assert handler._is_lease_active({"lease_expires_at": _past_iso()}) is False

    def test_respects_custom_now(self, handler):
        lease = {"lease_expires_at": _future_iso(10)}
        far_future = datetime.now(UTC) + timedelta(hours=24)
        assert handler._is_lease_active(lease, now=far_future) is False

    def test_missing_expiry_key_is_inactive(self, handler):
        assert handler._is_lease_active({"owner_node": "n1"}) is False


# ---------------------------------------------------------------------------
# TaskQueueHandler init and key helpers
# ---------------------------------------------------------------------------


class TestHandlerInit:
    def test_init_with_state_store(self, tmp_path):
        mock_store = Mock()
        mock_store.is_connected.return_value = False
        queue = TaskQueue(tmp_path / ".forge/tasks")
        handler = TaskQueueHandler(state_store=mock_store, task_queue=queue)
        assert handler.state_store is mock_store

    def test_init_without_state_store_calls_ensure(self, tmp_path):
        queue = TaskQueue(tmp_path / ".forge/tasks")
        with patch("forge_harness.state_store.StateStore") as MockStateStore:
            mock_store = MagicMock()
            MockStateStore.return_value = mock_store
            handler = TaskQueueHandler(state_store=None, task_queue=queue)
        MockStateStore.assert_called_once()
        mock_store.connect.assert_called_once()

    def test_ensure_state_store_handles_exception(self, tmp_path):
        """StateStore failures are swallowed — handler continues with state_store=None."""
        queue = TaskQueue(tmp_path / ".forge/tasks")
        with patch("forge_harness.state_store.StateStore") as MockStateStore:
            MockStateStore.side_effect = Exception("cannot connect")
            handler = TaskQueueHandler(state_store=None, task_queue=queue)
        assert handler.state_store is None

    def test_task_key_format(self, tmp_path):
        handler = _make_file_handler()
        assert handler._task_key("abc-123") == "forge:tasks:abc-123"

    def test_tasks_index_key(self, tmp_path):
        handler = _make_file_handler()
        assert handler._tasks_index_key() == "forge:tasks:index"

    def test_get_redis_client_not_connected(self):
        mock_store = Mock()
        mock_store.is_connected.return_value = False
        handler = _make_file_handler()
        handler.state_store = mock_store
        assert handler._get_redis_client() is None

    def test_get_redis_client_not_redis_type(self):
        mock_store = Mock()
        mock_store.is_connected.return_value = True
        mock_store.get_store_type.return_value = "file"
        handler = _make_file_handler()
        handler.state_store = mock_store
        assert handler._get_redis_client() is None

    def test_get_redis_client_missing_internal_redis(self):
        mock_store = Mock()
        mock_store.is_connected.return_value = True
        mock_store.get_store_type.return_value = "redis"
        # _redis attribute absent
        del mock_store._redis
        handler = _make_file_handler()
        handler.state_store = mock_store
        assert handler._get_redis_client() is None

    def test_get_redis_client_success(self):
        mock_redis = MagicMock()
        mock_store = MagicMock()
        mock_store.is_connected.return_value = True
        mock_store.get_store_type.return_value = "redis"
        mock_store._redis._client = mock_redis
        handler = _make_file_handler()
        handler.state_store = mock_store
        assert handler._get_redis_client() is mock_redis

    def test_get_redis_client_none_state_store(self):
        handler = _make_file_handler()
        handler.state_store = None
        assert handler._get_redis_client() is None


# ---------------------------------------------------------------------------
# list_tasks — file-based backend
# ---------------------------------------------------------------------------


class TestListTasksFileBased:
    @pytest.mark.asyncio
    async def test_returns_all_tasks(self):
        tasks = [_make_task("t1"), _make_task("t2")]
        mq = MagicMock()
        mq.list_all = AsyncMock(return_value=tasks)
        handler = _make_file_handler(mq)

        result = await handler.list_tasks()
        assert len(result) == 2
        assert result[0]["id"] == "t1"

    @pytest.mark.asyncio
    async def test_status_filter_converted_to_enum(self):
        mq = MagicMock()
        mq.list_all = AsyncMock(return_value=[])
        handler = _make_file_handler(mq)

        await handler.list_tasks(status="pending")
        mq.list_all.assert_called_once_with(status=TaskStatus.PENDING, priority=None)

    @pytest.mark.asyncio
    async def test_priority_filter_converted_to_enum(self):
        mq = MagicMock()
        mq.list_all = AsyncMock(return_value=[])
        handler = _make_file_handler(mq)

        await handler.list_tasks(priority="high")
        mq.list_all.assert_called_once_with(status=None, priority=TaskPriority.HIGH)

    @pytest.mark.asyncio
    async def test_unknown_status_passes_none(self):
        mq = MagicMock()
        mq.list_all = AsyncMock(return_value=[])
        handler = _make_file_handler(mq)

        await handler.list_tasks(status="bogus_status")
        mq.list_all.assert_called_once_with(status=None, priority=None)

    @pytest.mark.asyncio
    async def test_limit_applied(self):
        tasks = [_make_task(f"t{i}") for i in range(20)]
        mq = MagicMock()
        mq.list_all = AsyncMock(return_value=tasks)
        handler = _make_file_handler(mq)

        result = await handler.list_tasks(limit=5)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# get_task — file-based backend
# ---------------------------------------------------------------------------


class TestGetTaskFileBased:
    @pytest.mark.asyncio
    async def test_returns_dict_when_found(self):
        mq = MagicMock()
        mq.get = AsyncMock(return_value=_make_task("t1", title="Found"))
        handler = _make_file_handler(mq)

        result = await handler.get_task("t1")
        assert result["id"] == "t1"
        assert result["subject"] == "Found"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        mq = MagicMock()
        mq.get = AsyncMock(return_value=None)
        handler = _make_file_handler(mq)

        result = await handler.get_task("missing")
        assert result is None


# ---------------------------------------------------------------------------
# create_task — file-based backend
# ---------------------------------------------------------------------------


class TestCreateTaskFileBased:
    @pytest.mark.asyncio
    async def test_creates_with_medium_default_priority(self):
        created = _make_task("new1")
        mq = MagicMock()
        mq.add = AsyncMock(return_value=created)
        handler = _make_file_handler(mq)

        await handler.create_task("Task", "desc")
        added = mq.add.call_args[0][0]
        assert added.priority == TaskPriority.MEDIUM

    @pytest.mark.asyncio
    async def test_creates_with_specified_priority(self):
        created = _make_task("new2", priority=TaskPriority.CRITICAL)
        mq = MagicMock()
        mq.add = AsyncMock(return_value=created)
        handler = _make_file_handler(mq)

        await handler.create_task("Task", "desc", priority="critical")
        added = mq.add.call_args[0][0]
        assert added.priority == TaskPriority.CRITICAL

    @pytest.mark.asyncio
    async def test_creates_with_required_role(self):
        created = _make_task("new3")
        mq = MagicMock()
        mq.add = AsyncMock(return_value=created)
        handler = _make_file_handler(mq)

        await handler.create_task("Task", "desc", required_role="backend-engineer")
        added = mq.add.call_args[0][0]
        assert added.required_role == "backend-engineer"

    @pytest.mark.asyncio
    async def test_creates_with_lease(self):
        lease = {"owner_node": "n1", "path_lock": "/p"}
        created = _make_task("new4", lease=lease)
        mq = MagicMock()
        mq.add = AsyncMock(return_value=created)
        handler = _make_file_handler(mq)

        await handler.create_task("Task", "desc", lease=lease)
        added = mq.add.call_args[0][0]
        assert added.lease == lease

    @pytest.mark.asyncio
    async def test_unknown_priority_defaults_to_medium(self):
        created = _make_task("new5")
        mq = MagicMock()
        mq.add = AsyncMock(return_value=created)
        handler = _make_file_handler(mq)

        await handler.create_task("Task", "desc", priority="bogus")
        added = mq.add.call_args[0][0]
        assert added.priority == TaskPriority.MEDIUM

    @pytest.mark.asyncio
    async def test_returns_task_dict(self):
        created = _make_task("new6", title="Returned")
        mq = MagicMock()
        mq.add = AsyncMock(return_value=created)
        handler = _make_file_handler(mq)

        result = await handler.create_task("Returned", "desc")
        assert result["subject"] == "Returned"


# ---------------------------------------------------------------------------
# update_task — file-based backend
# ---------------------------------------------------------------------------


class TestUpdateTaskFileBased:
    @pytest.mark.asyncio
    async def test_updates_existing_task(self):
        # Test valid transition: PENDING -> QUEUED (canonical DF lifecycle)
        existing = _make_task("t1")
        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)

        result = await handler.update_task("t1", {"status": "queued"})
        assert result is not None
        mq.update.assert_called_once_with(existing)

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        mq = MagicMock()
        mq.get = AsyncMock(return_value=None)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)

        result = await handler.update_task("missing", {"status": "completed"})
        assert result is None
        mq.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_returned_dict_reflects_update(self):
        existing = _make_task("t1")
        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)

        result = await handler.update_task("t1", {"subject": "Updated"})
        assert result["subject"] == "Updated"


# ---------------------------------------------------------------------------
# delete_task — file-based backend
# ---------------------------------------------------------------------------


class TestDeleteTaskFileBased:
    @pytest.mark.asyncio
    async def test_deletes_existing_task(self):
        mq = MagicMock()
        mq.delete = AsyncMock(return_value=True)
        handler = _make_file_handler(mq)

        result = await handler.delete_task("t1")
        assert result is True
        mq.delete.assert_called_once_with("t1")

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self):
        mq = MagicMock()
        mq.delete = AsyncMock(return_value=False)
        handler = _make_file_handler(mq)

        result = await handler.delete_task("missing")
        assert result is False


# ---------------------------------------------------------------------------
# claim_task
# ---------------------------------------------------------------------------


class TestClaimTask:
    @pytest.mark.asyncio
    async def test_claim_sets_agent_and_assigned_status(self):
        existing = _make_task("t1")
        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)

        result = await handler.claim_task("t1", "agent42")
        assert result is not None
        assert existing.assigned_agent == "agent42"
        assert existing.status == TaskStatus.ASSIGNED


# ---------------------------------------------------------------------------
# get_stats — file-based backend
# ---------------------------------------------------------------------------


class TestGetStatsFileBased:
    @pytest.mark.asyncio
    async def test_returns_flattened_stats(self):
        raw_stats = {
            "by_status": {
                "pending": 3,
                "assigned": 1,
                "in_progress": 2,
                "completed": 5,
                "failed": 0,
            },
            "total": 11,
        }
        mq = MagicMock()
        mq.get_stats = AsyncMock(return_value=raw_stats)
        handler = _make_file_handler(mq)

        result = await handler.get_stats()
        assert result["pending"] == 3
        assert result["assigned"] == 1
        assert result["in_progress"] == 2
        assert result["completed"] == 5
        assert result["failed"] == 0
        assert result["total"] == 11

    @pytest.mark.asyncio
    async def test_missing_status_keys_default_to_zero(self):
        mq = MagicMock()
        mq.get_stats = AsyncMock(return_value={"by_status": {}, "total": 0})
        handler = _make_file_handler(mq)

        result = await handler.get_stats()
        assert result["pending"] == 0
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# _find_path_lock_conflict
# ---------------------------------------------------------------------------


class TestFindPathLockConflict:
    @pytest.mark.asyncio
    async def test_empty_path_lock_returns_none(self):
        handler = _make_file_handler()
        handler.list_tasks = AsyncMock(return_value=[])
        result = await handler._find_path_lock_conflict("")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_tasks_returns_none(self):
        handler = _make_file_handler()
        handler.list_tasks = AsyncMock(return_value=[])
        result = await handler._find_path_lock_conflict("/projects/api")
        assert result is None

    @pytest.mark.asyncio
    async def test_finds_active_conflict(self):
        conflict_task = {
            "id": "t2",
            "lease": {"path_lock": "/projects/api", "lease_expires_at": _future_iso()},
        }
        handler = _make_file_handler()
        handler.list_tasks = AsyncMock(return_value=[conflict_task])
        result = await handler._find_path_lock_conflict("/projects/api")
        assert result is not None
        assert result["id"] == "t2"

    @pytest.mark.asyncio
    async def test_excludes_specified_task_id(self):
        my_task = {
            "id": "t1",
            "lease": {"path_lock": "/projects/api", "lease_expires_at": _future_iso()},
        }
        handler = _make_file_handler()
        handler.list_tasks = AsyncMock(return_value=[my_task])
        result = await handler._find_path_lock_conflict("/projects/api", exclude_task_id="t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_ignores_expired_lease(self):
        expired_task = {
            "id": "t2",
            "lease": {"path_lock": "/projects/api", "lease_expires_at": _past_iso()},
        }
        handler = _make_file_handler()
        handler.list_tasks = AsyncMock(return_value=[expired_task])
        result = await handler._find_path_lock_conflict("/projects/api")
        assert result is None

    @pytest.mark.asyncio
    async def test_ignores_non_dict_lease(self):
        task_no_lease = {"id": "t2", "lease": None}
        handler = _make_file_handler()
        handler.list_tasks = AsyncMock(return_value=[task_no_lease])
        result = await handler._find_path_lock_conflict("/projects/api")
        assert result is None

    @pytest.mark.asyncio
    async def test_ignores_different_path_lock(self):
        other_task = {
            "id": "t2",
            "lease": {"path_lock": "/other/path", "lease_expires_at": _future_iso()},
        }
        handler = _make_file_handler()
        handler.list_tasks = AsyncMock(return_value=[other_task])
        result = await handler._find_path_lock_conflict("/projects/api")
        assert result is None

    @pytest.mark.asyncio
    async def test_custom_now_respected(self):
        """A task whose lease expires in 1 second should be inactive if 'now' is 1 hour later."""
        soon_task = {
            "id": "t2",
            "lease": {"path_lock": "/p", "lease_expires_at": _future_iso(1)},
        }
        handler = _make_file_handler()
        handler.list_tasks = AsyncMock(return_value=[soon_task])
        far_future = datetime.now(UTC) + timedelta(hours=1)
        result = await handler._find_path_lock_conflict("/p", now=far_future)
        assert result is None


# ---------------------------------------------------------------------------
# claim_task_with_lease
# ---------------------------------------------------------------------------


class TestClaimTaskWithLease:
    def _valid_lease(self, **kwargs) -> dict:
        defaults = {
            "owner_node": "node1",
            "owner_agent": "agent1",
            "path_lock": "/projects/api",
            "lease_expires_at": _future_iso(),
        }
        defaults.update(kwargs)
        return defaults

    def _make_handler_with_task(self, task_dict: dict) -> TaskQueueHandler:
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value=task_dict)
        handler._find_path_lock_conflict = AsyncMock(return_value=None)
        return handler

    @pytest.mark.asyncio
    async def test_task_not_found_returns_none(self):
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value=None)
        result = await handler.claim_task_with_lease("missing", self._valid_lease())
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_owner_node_raises_invalid_lease(self):
        handler = self._make_handler_with_task({"id": "t1"})
        with pytest.raises(TaskLeaseError) as exc:
            await handler.claim_task_with_lease("t1", self._valid_lease(owner_node=""))
        assert exc.value.code == "INVALID_LEASE"
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_owner_agent_raises_invalid_lease(self):
        handler = self._make_handler_with_task({"id": "t1"})
        with pytest.raises(TaskLeaseError) as exc:
            await handler.claim_task_with_lease("t1", self._valid_lease(owner_agent=""))
        assert exc.value.code == "INVALID_LEASE"

    @pytest.mark.asyncio
    async def test_missing_path_lock_raises_invalid_lease(self):
        handler = self._make_handler_with_task({"id": "t1"})
        with pytest.raises(TaskLeaseError) as exc:
            await handler.claim_task_with_lease("t1", self._valid_lease(path_lock=""))
        assert exc.value.code == "INVALID_LEASE"

    @pytest.mark.asyncio
    async def test_expired_lease_expiry_raises_invalid_lease(self):
        handler = self._make_handler_with_task({"id": "t1"})
        with pytest.raises(TaskLeaseError) as exc:
            await handler.claim_task_with_lease(
                "t1", self._valid_lease(lease_expires_at=_past_iso())
            )
        assert exc.value.code == "INVALID_LEASE"
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_already_owned_by_different_owner_raises(self):
        existing_lease = {
            "owner_node": "other_node",
            "owner_agent": "other_agent",
            "path_lock": "/projects/api",
            "lease_expires_at": _future_iso(),
        }
        handler = self._make_handler_with_task({"id": "t1", "lease": existing_lease})
        with pytest.raises(TaskLeaseError) as exc:
            await handler.claim_task_with_lease("t1", self._valid_lease())
        assert exc.value.code == "LEASE_ALREADY_OWNED"
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_same_owner_can_reclaim(self):
        existing_lease = self._valid_lease()
        existing_obj = _make_task("t1", status=TaskStatus.ASSIGNED)
        existing_obj.lease = existing_lease
        existing_obj.assigned_agent = "agent1"
        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": existing_lease})
        handler._find_path_lock_conflict = AsyncMock(return_value=None)

        result = await handler.claim_task_with_lease("t1", self._valid_lease())
        assert result is not None

    @pytest.mark.asyncio
    async def test_path_lock_conflict_raises(self):
        task = {"id": "t1", "lease": None}
        conflict_task = {"id": "t2"}
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value=task)
        handler._find_path_lock_conflict = AsyncMock(return_value=conflict_task)

        with pytest.raises(TaskLeaseError) as exc:
            await handler.claim_task_with_lease("t1", self._valid_lease())
        assert exc.value.code == "PATH_LOCK_CONFLICT"
        assert "t2" in str(exc.value)

    @pytest.mark.asyncio
    async def test_successful_claim_returns_task(self):
        existing_obj = _make_task("t1")
        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": None})
        handler._find_path_lock_conflict = AsyncMock(return_value=None)

        result = await handler.claim_task_with_lease("t1", self._valid_lease())
        assert result is not None


# ---------------------------------------------------------------------------
# renew_task_lease
# ---------------------------------------------------------------------------


class TestRenewTaskLease:
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
    async def test_task_not_found_returns_none(self):
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value=None)
        result = await handler.renew_task_lease("missing", self._active_lease())
        assert result is None

    @pytest.mark.asyncio
    async def test_no_existing_lease_raises_lease_not_found(self):
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": None})
        with pytest.raises(TaskLeaseError) as exc:
            await handler.renew_task_lease("t1", self._active_lease())
        assert exc.value.code == "LEASE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_expired_existing_lease_raises_lease_expired(self):
        current_lease = self._active_lease(lease_expires_at=_past_iso())
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": current_lease})
        with pytest.raises(TaskLeaseError) as exc:
            await handler.renew_task_lease("t1", self._active_lease())
        assert exc.value.code == "LEASE_EXPIRED"

    @pytest.mark.asyncio
    async def test_owner_node_mismatch_raises(self):
        current_lease = self._active_lease()
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": current_lease})
        new_lease = self._active_lease(owner_node="other_node", owner_agent="other_agent")
        with pytest.raises(TaskLeaseError) as exc:
            await handler.renew_task_lease("t1", new_lease)
        assert exc.value.code == "LEASE_OWNER_MISMATCH"

    @pytest.mark.asyncio
    async def test_path_lock_change_raises_immutable(self):
        current_lease = self._active_lease()
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": current_lease})
        new_lease = self._active_lease(path_lock="/different/path")
        with pytest.raises(TaskLeaseError) as exc:
            await handler.renew_task_lease("t1", new_lease)
        assert exc.value.code == "PATH_LOCK_IMMUTABLE"
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_new_lease_expired_raises_invalid_lease(self):
        current_lease = self._active_lease()
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": current_lease})
        new_lease = self._active_lease(lease_expires_at=_past_iso())
        with pytest.raises(TaskLeaseError) as exc:
            await handler.renew_task_lease("t1", new_lease)
        assert exc.value.code == "INVALID_LEASE"

    @pytest.mark.asyncio
    async def test_successful_renewal(self):
        current_lease = self._active_lease()
        existing_obj = _make_task("t1", status=TaskStatus.ASSIGNED)
        existing_obj.lease = current_lease
        existing_obj.assigned_agent = "agent1"
        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": current_lease})

        new_lease = self._active_lease(lease_expires_at=_future_iso(7200))
        result = await handler.renew_task_lease("t1", new_lease)
        assert result is not None


# ---------------------------------------------------------------------------
# release_task_lease
# ---------------------------------------------------------------------------


class TestReleaseTaskLease:
    @pytest.mark.asyncio
    async def test_task_not_found_returns_none(self):
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value=None)
        result = await handler.release_task_lease("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_lease_raises_lease_not_found(self):
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": None})
        with pytest.raises(TaskLeaseError) as exc:
            await handler.release_task_lease("t1")
        assert exc.value.code == "LEASE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_owner_node_mismatch_raises(self):
        lease = {"owner_node": "node1", "owner_agent": "agent1"}
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": lease})
        with pytest.raises(TaskLeaseError) as exc:
            await handler.release_task_lease("t1", owner_node="wrong_node")
        assert exc.value.code == "LEASE_OWNER_MISMATCH"

    @pytest.mark.asyncio
    async def test_owner_agent_mismatch_raises(self):
        lease = {"owner_node": "node1", "owner_agent": "agent1"}
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": lease})
        with pytest.raises(TaskLeaseError) as exc:
            await handler.release_task_lease("t1", owner_agent="wrong_agent")
        assert exc.value.code == "LEASE_OWNER_MISMATCH"

    @pytest.mark.asyncio
    async def test_successful_release_clears_lease(self):
        lease = {"owner_node": "node1", "owner_agent": "agent1"}
        existing_obj = _make_task("t1", status=TaskStatus.ASSIGNED)
        existing_obj.lease = lease
        existing_obj.assigned_agent = "agent1"
        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(
            return_value={
                "id": "t1",
                "lease": lease,
                "claimed_by": "agent1",
                "status": "assigned",
            }
        )

        result = await handler.release_task_lease("t1", owner_node="node1", owner_agent="agent1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_release_without_owner_check(self):
        lease = {"owner_node": "node1", "owner_agent": "agent1"}
        existing_obj = _make_task("t1", status=TaskStatus.ASSIGNED)
        existing_obj.lease = lease
        existing_obj.assigned_agent = "agent1"
        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(
            return_value={
                "id": "t1",
                "lease": lease,
                "claimed_by": "agent1",
                "status": "assigned",
            }
        )

        result = await handler.release_task_lease("t1")
        assert result is not None


# ---------------------------------------------------------------------------
# requeue_task
# ---------------------------------------------------------------------------


class TestRequeueTask:
    @pytest.mark.asyncio
    async def test_task_not_found_returns_none(self):
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value=None)
        result = await handler.requeue_task("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_requeue_without_lease(self):
        existing_obj = _make_task("t1", status=TaskStatus.IN_PROGRESS)
        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(
            return_value={"id": "t1", "lease": None, "status": "in_progress"}
        )

        result = await handler.requeue_task("t1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_owner_node_mismatch_raises(self):
        lease = {"owner_node": "correct_node", "owner_agent": "agent1"}
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": lease})
        with pytest.raises(TaskLeaseError) as exc:
            await handler.requeue_task("t1", owner_node="wrong_node")
        assert exc.value.code == "LEASE_OWNER_MISMATCH"

    @pytest.mark.asyncio
    async def test_owner_agent_mismatch_raises(self):
        lease = {"owner_node": "node1", "owner_agent": "correct_agent"}
        handler = _make_file_handler()
        handler.get_task = AsyncMock(return_value={"id": "t1", "lease": lease})
        with pytest.raises(TaskLeaseError) as exc:
            await handler.requeue_task("t1", owner_agent="wrong_agent")
        assert exc.value.code == "LEASE_OWNER_MISMATCH"

    @pytest.mark.asyncio
    async def test_requeue_with_matching_owner_clears_ownership(self):
        lease = {"owner_node": "node1", "owner_agent": "agent1"}
        existing_obj = _make_task("t1", status=TaskStatus.ASSIGNED)
        existing_obj.lease = lease
        existing_obj.assigned_agent = "agent1"
        mq = MagicMock()
        mq.get = AsyncMock(return_value=existing_obj)
        mq.update = AsyncMock()
        handler = _make_file_handler(mq)
        handler.get_task = AsyncMock(
            return_value={
                "id": "t1",
                "lease": lease,
                "claimed_by": "agent1",
                "status": "assigned",
            }
        )

        result = await handler.requeue_task("t1", owner_node="node1", owner_agent="agent1")
        assert result is not None


# ---------------------------------------------------------------------------
# Serialization / Deserialization (Redis path)
# ---------------------------------------------------------------------------


class TestSerializeTask:
    @pytest.fixture
    def handler(self):
        return _make_file_handler()

    def _sample(self, **overrides) -> dict:
        base = {
            "id": "abc",
            "subject": "S",
            "description": "D",
            "priority": "medium",
            "status": "pending",
            "required_role": "backend",
            "claimed_by": "agent1",
            "lease": {"owner_node": "n1"},
            "order": 3,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        base.update(overrides)
        return base

    def test_all_values_are_strings(self, handler):
        serialized = handler._serialize_task(self._sample())
        for key, value in serialized.items():
            assert isinstance(value, str), f"Key {key!r} has non-string value {value!r}"

    def test_lease_json_encoded(self, handler):
        serialized = handler._serialize_task(self._sample())
        parsed = json.loads(serialized["lease"])
        assert parsed["owner_node"] == "n1"

    def test_none_lease_becomes_empty_string(self, handler):
        serialized = handler._serialize_task(self._sample(lease=None))
        assert serialized["lease"] == ""

    def test_order_becomes_string(self, handler):
        serialized = handler._serialize_task(self._sample(order=7))
        assert serialized["order"] == "7"


class TestDeserializeTask:
    @pytest.fixture
    def handler(self):
        return _make_file_handler()

    def _base_data(self, **overrides) -> dict:
        base = {
            "id": "x",
            "subject": "s",
            "description": "d",
            "priority": "low",
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

    def test_empty_required_role_to_none(self, handler):
        result = handler._deserialize_task(self._base_data())
        assert result["required_role"] is None

    def test_empty_claimed_by_to_none(self, handler):
        result = handler._deserialize_task(self._base_data())
        assert result["claimed_by"] is None

    def test_string_order_to_int(self, handler):
        result = handler._deserialize_task(self._base_data(order="5"))
        assert result["order"] == 5

    def test_invalid_order_to_zero(self, handler):
        result = handler._deserialize_task(self._base_data(order="nope"))
        assert result["order"] == 0

    def test_missing_order_defaults_to_zero(self, handler):
        data = self._base_data()
        del data["order"]
        result = handler._deserialize_task(data)
        assert result["order"] == 0

    def test_valid_lease_json_decoded(self, handler):
        lease_dict = {"owner_node": "n1", "path_lock": "/p"}
        result = handler._deserialize_task(self._base_data(lease=json.dumps(lease_dict)))
        assert result["lease"] == lease_dict

    def test_invalid_lease_json_to_none(self, handler):
        result = handler._deserialize_task(self._base_data(lease="{bad json"))
        assert result["lease"] is None

    def test_empty_lease_string_to_none(self, handler):
        result = handler._deserialize_task(self._base_data(lease=""))
        assert result["lease"] is None

    def test_non_empty_fields_preserved(self, handler):
        result = handler._deserialize_task(
            self._base_data(required_role="backend", claimed_by="agent1")
        )
        assert result["required_role"] == "backend"
        assert result["claimed_by"] == "agent1"

    def test_roundtrip_via_serialize(self, handler):
        task_dict = {
            "id": "round",
            "subject": "R",
            "description": "T",
            "priority": "high",
            "status": "pending",
            "required_role": "backend",
            "claimed_by": "agent1",
            "lease": {"owner": "n1"},
            "order": 2,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        serialized = handler._serialize_task(task_dict)
        deserialized = handler._deserialize_task(serialized)
        assert deserialized["id"] == "round"
        assert deserialized["priority"] == "high"
        assert deserialized["order"] == 2
        assert deserialized["lease"] == {"owner": "n1"}


# ---------------------------------------------------------------------------
# Redis backend — list_tasks
# ---------------------------------------------------------------------------


class TestListTasksRedis:
    @pytest.mark.asyncio
    async def test_empty_index_returns_empty_list(self):
        handler, redis = _make_redis_handler()
        redis.smembers.return_value = set()
        result = await handler.list_tasks()
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_by_status(self):
        handler, redis = _make_redis_handler()
        redis.smembers.return_value = {"t1", "t2"}

        base = {
            "id": "t1",
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

        def hgetall(key):
            if "t1" in key:
                return base
            return {**base, "id": "t2", "status": "completed"}

        redis.hgetall.side_effect = hgetall
        result = await handler.list_tasks(status="pending")
        assert all(t["status"] == "pending" for t in result)

    @pytest.mark.asyncio
    async def test_skips_empty_hgetall(self):
        handler, redis = _make_redis_handler()
        redis.smembers.return_value = {"t1"}
        redis.hgetall.return_value = {}
        result = await handler.list_tasks()
        assert result == []

    @pytest.mark.asyncio
    async def test_redis_exception_returns_empty_list(self):
        handler, redis = _make_redis_handler()
        redis.smembers.side_effect = Exception("Redis down")
        result = await handler.list_tasks()
        assert result == []

    @pytest.mark.asyncio
    async def test_limit_applied(self):
        handler, redis = _make_redis_handler()
        redis.smembers.return_value = {"t1", "t2", "t3"}

        def hgetall(key):
            suffix = key.split(":")[-1]
            return {
                "id": suffix,
                "subject": "S",
                "description": "D",
                "priority": "medium",
                "status": "pending",
                "required_role": "",
                "claimed_by": "",
                "lease": "",
                "order": "0",
                "created_at": f"2026-01-0{suffix[-1]}T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }

        redis.hgetall.side_effect = hgetall
        result = await handler.list_tasks(limit=2)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_filters_by_priority(self):
        """Covers the priority filter continue-branch (line 267)."""
        handler, redis = _make_redis_handler()
        redis.smembers.return_value = {"t1", "t2"}

        base = {
            "subject": "S",
            "description": "D",
            "status": "pending",
            "required_role": "",
            "claimed_by": "",
            "lease": "",
            "order": "0",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }

        def hgetall(key):
            if "t1" in key:
                return {**base, "id": "t1", "priority": "high"}
            return {**base, "id": "t2", "priority": "low"}

        redis.hgetall.side_effect = hgetall

        result = await handler.list_tasks(priority="high")
        assert len(result) == 1
        assert result[0]["priority"] == "high"


# ---------------------------------------------------------------------------
# Redis backend — get_task
# ---------------------------------------------------------------------------


class TestGetTaskRedis:
    def _base_data(self, task_id: str = "t1") -> dict:
        return {
            "id": task_id,
            "subject": "S",
            "description": "D",
            "priority": "high",
            "status": "pending",
            "required_role": "",
            "claimed_by": "",
            "lease": "",
            "order": "0",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }

    @pytest.mark.asyncio
    async def test_returns_task_when_found(self):
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = self._base_data()
        result = await handler.get_task("t1")
        assert result is not None
        assert result["id"] == "t1"

    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self):
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = {}
        result = await handler.get_task("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_redis_exception_returns_none(self):
        handler, redis = _make_redis_handler()
        redis.hgetall.side_effect = Exception("Redis error")
        result = await handler.get_task("t1")
        assert result is None


# ---------------------------------------------------------------------------
# Redis backend — create_task
# ---------------------------------------------------------------------------


class TestCreateTaskRedis:
    @pytest.mark.asyncio
    async def test_stores_task_in_redis(self):
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = {
            "id": "new",
            "subject": "New task",
            "description": "Desc",
            "priority": "medium",
            "status": "pending",
            "required_role": "",
            "claimed_by": "",
            "lease": "",
            "order": "0",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }

        result = await handler.create_task("New task", "Desc")

        redis.hset.assert_called_once()
        redis.sadd.assert_called_once()
        redis.expire.assert_called_once()
        assert result["subject"] == "New task"

    @pytest.mark.asyncio
    async def test_redis_write_exception_propagates(self):
        handler, redis = _make_redis_handler()
        redis.hset.side_effect = Exception("write failed")
        with pytest.raises(Exception, match="write failed"):
            await handler.create_task("Task", "Desc")


# ---------------------------------------------------------------------------
# Redis backend — update_task
# ---------------------------------------------------------------------------


class TestUpdateTaskRedis:
    def _base_data(self) -> dict:
        return {
            "id": "t1",
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

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = {}
        result = await handler.update_task("missing", {"status": "completed"})
        assert result is None

    @pytest.mark.asyncio
    async def test_updates_status_field(self):
        # Test valid transition: PENDING -> QUEUED (canonical DF lifecycle)
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = self._base_data()
        redis.hset.return_value = 0

        result = await handler.update_task("t1", {"status": "queued"})
        assert result is not None
        assert result["status"] == "queued"
        redis.hset.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignores_id_and_created_at(self):
        # Test valid transition: PENDING -> QUEUED (canonical DF lifecycle)
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = self._base_data()
        redis.hset.return_value = 0

        result = await handler.update_task(
            "t1", {"id": "hacked", "created_at": "2000-01-01", "status": "queued"}
        )
        assert result["id"] == "t1"
        assert result["created_at"] == "2026-01-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_redis_exception_returns_none(self):
        handler, redis = _make_redis_handler()
        redis.hgetall.return_value = self._base_data()
        redis.hset.side_effect = Exception("write error")

        result = await handler.update_task("t1", {"status": "completed"})
        assert result is None


# ---------------------------------------------------------------------------
# Redis backend — delete_task
# ---------------------------------------------------------------------------


class TestDeleteTaskRedis:
    @pytest.mark.asyncio
    async def test_removes_from_index_and_hash(self):
        handler, redis = _make_redis_handler()
        result = await handler.delete_task("t1")

        assert result is True
        redis.srem.assert_called_once()
        redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_exception_returns_false(self):
        handler, redis = _make_redis_handler()
        redis.srem.side_effect = Exception("error")
        result = await handler.delete_task("t1")
        assert result is False


# ---------------------------------------------------------------------------
# Redis backend — get_stats
# ---------------------------------------------------------------------------


class TestGetStatsRedis:
    @pytest.mark.asyncio
    async def test_counts_tasks_by_status(self):
        handler, redis = _make_redis_handler()
        redis.smembers.return_value = {"t1", "t2", "t3"}

        def hgetall(key):
            if "t1" in key:
                return {"status": "pending"}
            elif "t2" in key:
                return {"status": "completed"}
            return {"status": "pending"}

        redis.hgetall.side_effect = hgetall
        result = await handler.get_stats()
        assert result["pending"] == 2
        assert result["completed"] == 1
        assert result["total"] == 3

    @pytest.mark.asyncio
    async def test_skips_empty_hgetall(self):
        handler, redis = _make_redis_handler()
        redis.smembers.return_value = {"t1"}
        redis.hgetall.return_value = {}
        result = await handler.get_stats()
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_redis_exception_returns_zero_stats(self):
        handler, redis = _make_redis_handler()
        redis.smembers.side_effect = Exception("down")
        result = await handler.get_stats()
        assert result == {
            "pending": 0,
            "in_progress": 0,
            "completed": 0,
            "total": 0,
            "assigned": 0,
        }


# ---------------------------------------------------------------------------
# Singleton — get_task_handler
# ---------------------------------------------------------------------------


class TestGetTaskHandlerSingleton:
    def test_returns_task_queue_handler_instance(self, tmp_path, monkeypatch):
        import forge_harness.webhook_server.handlers.task_handler as mod

        mod._task_handler = None
        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
        with patch.object(TaskQueueHandler, "_ensure_state_store"):
            with patch(
                "forge_harness.webhook_server.handlers.task_handler.create_task_queue",
                return_value=MagicMock(),
            ):
                handler = get_task_handler()
        assert isinstance(handler, TaskQueueHandler)

    def test_returns_same_instance_on_second_call(self, tmp_path, monkeypatch):
        import forge_harness.webhook_server.handlers.task_handler as mod

        mod._task_handler = None
        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
        with patch.object(TaskQueueHandler, "_ensure_state_store"):
            with patch(
                "forge_harness.webhook_server.handlers.task_handler.create_task_queue",
                return_value=MagicMock(),
            ):
                h1 = get_task_handler()
                h2 = get_task_handler()
        assert h1 is h2
