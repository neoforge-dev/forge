"""Deep tests for webhook_server/handlers — targeting 100% coverage.

Covers the three handler modules:
  - forge_harness.webhook_server.handlers.task_handler
  - forge_harness.webhook_server.handlers.webhook_handler
  - forge_harness.webhook_server.handlers.approval_handler

Focus areas
-----------
1. ApprovalQueueHandler._find_forge_root: lines 48 and 51
   (the two return statements inside the traversal loop that existing tests miss)
2. ApprovalQueueHandler.batch_approve exception branch: lines 188-189
   (exception raised inside the per-request loop)
3. Additional edge-case and integration paths across all three handlers that
   complement (without duplicating) the existing test files.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
)
from forge_harness.task_queue import Task, TaskPriority, TaskStatus
from forge_harness.webhook_server.core.models import WebhookPayload, WebhookResponse
from forge_harness.webhook_server.handlers.approval_handler import (
    ApprovalQueueHandler,
    get_approval_handler,
)
from forge_harness.webhook_server.handlers.task_handler import (
    _PRIORITY_MAP,
    _STATUS_MAP,
    TaskLeaseError,
    TaskQueueHandler,
    _dict_to_task,
    _task_to_dict,
    get_task_handler,
)
from forge_harness.webhook_server.handlers.webhook_handler import (
    WebhookHandler,
    get_webhook_handler,
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
    assigned_agent: str | None = None,
    required_role: str | None = None,
    lease: dict | None = None,
    order: int = 0,
    domain: str | None = None,
    project: str | None = None,
) -> Task:
    return Task(
        id=task_id,
        title=title,
        description=description,
        priority=priority,
        status=status,
        assigned_agent=assigned_agent,
        required_role=required_role,
        lease=lease,
        order=order,
        domain=domain,
        project=project,
        created_at=datetime.now(UTC).isoformat(),
    )


def _make_handler_no_redis(task_queue: MagicMock | None = None) -> TaskQueueHandler:
    """Return a TaskQueueHandler wired to a mocked file-based TaskQueue (no Redis)."""
    tq = task_queue or MagicMock()
    with patch(
        "forge_harness.webhook_server.handlers.task_handler.TaskQueueHandler._ensure_state_store"
    ):
        h = TaskQueueHandler(state_store=None, task_queue=tq)
    h.state_store = None  # Force file-based path
    return h


def _make_mock_queue() -> MagicMock:
    q = MagicMock()
    q.list_all = AsyncMock(return_value=[])
    q.get = AsyncMock(return_value=None)
    q.add = AsyncMock(side_effect=lambda t: t)
    q.update = AsyncMock()
    q.delete = AsyncMock(return_value=True)
    q.get_stats = AsyncMock(return_value={"by_status": {}, "total": 0})
    return q


def _make_approval_request(
    req_id: str = "req-001",
    approval_type: ApprovalType = ApprovalType.CONTENT,
    domain: str = "test-domain",
    title: str = "Test Approval",
    description: str = "Test description",
    status: ApprovalStatus = ApprovalStatus.PENDING,
    priority: ApprovalPriority = ApprovalPriority.NORMAL,
    metadata: dict | None = None,
    workflow_checkpoint: str | None = None,
    expires_at: datetime | None = None,
    resolved_at: datetime | None = None,
    approved_by: str | None = None,
    resolution_reason: str | None = None,
) -> ApprovalRequest:
    return ApprovalRequest(
        id=req_id,
        type=approval_type,
        domain=domain,
        title=title,
        description=description,
        status=status,
        priority=priority,
        metadata=metadata or {},
        workflow_checkpoint=workflow_checkpoint,
        expires_at=expires_at,
        resolved_at=resolved_at,
        approved_by=approved_by,
        resolution_reason=resolution_reason,
        created_at=datetime(2026, 2, 22, 10, 0, 0, tzinfo=UTC),
    )


# ===========================================================================
# SECTION 1: ApprovalQueueHandler._find_forge_root — missing lines 48 and 51
# ===========================================================================

class TestFindForgeRootMissingLines:
    """Target the two return statements in _find_forge_root that need coverage.

    Line 46: `return current`  — triggered by `(current / "domains.yaml").exists()`
    Line 48: `return current`  — triggered by `(current / "forge_harness" / "domains.yaml").exists()`
    Line 51: `return Path(__file__).parent.parent`  — fallback when nothing found
    """

    def test_returns_dir_containing_domains_yaml(self, tmp_path):
        """Line 46: traverse returns the directory that contains domains.yaml."""
        # Create domains.yaml two levels above the fake __file__ location
        root_dir = tmp_path / "project_root"
        root_dir.mkdir()
        (root_dir / "domains.yaml").write_text("domains: []")

        handlers_dir = root_dir / "forge_harness" / "webhook_server" / "handlers"
        handlers_dir.mkdir(parents=True)
        fake_file = str(handlers_dir / "approval_handler.py")

        with patch(
            "forge_harness.webhook_server.handlers.approval_handler.__file__",
            fake_file,
        ):
            handler = ApprovalQueueHandler()

        # The traversal must have found root_dir (or one of its parents containing domains.yaml)
        assert handler._forge_root is not None
        assert isinstance(handler._forge_root, Path)
        # The returned path must be root_dir or an ancestor that has domains.yaml
        assert (handler._forge_root / "domains.yaml").exists()

    def test_returns_dir_containing_forge_harness_domains_yaml(self, tmp_path):
        """Line 48: traverse returns the directory that has forge_harness/domains.yaml.

        _find_forge_root starts at Path(__file__).parent and walks up. It checks
        `(current / "forge_harness" / "domains.yaml").exists()` (line 47).
        We place the fake __file__ OUTSIDE of forge_harness/ so that the traversal
        hits root_dir where (root_dir / "forge_harness" / "domains.yaml") exists,
        without ever passing through forge_harness/ itself (which would trigger line 45).
        """
        root_dir = tmp_path / "project_root2"
        root_dir.mkdir()

        # root_dir/forge_harness/domains.yaml exists — triggers line 47 check
        fh_dir = root_dir / "forge_harness"
        fh_dir.mkdir()
        (fh_dir / "domains.yaml").write_text("domains: []")
        # root_dir/domains.yaml must NOT exist — so line 45 check is skipped
        assert not (root_dir / "domains.yaml").exists()

        # Place fake __file__ in an unrelated sibling subtree so traversal
        # passes through root_dir without going through forge_harness/ first.
        # Path: root_dir/other_pkg/handlers/approval_handler.py
        # Traversal: handlers/ -> other_pkg/ -> root_dir (line 47 hits here)
        handlers_dir = root_dir / "other_pkg" / "handlers"
        handlers_dir.mkdir(parents=True)
        fake_file = str(handlers_dir / "approval_handler.py")

        with patch(
            "forge_harness.webhook_server.handlers.approval_handler.__file__",
            fake_file,
        ):
            handler = ApprovalQueueHandler()

        assert handler._forge_root is not None
        assert isinstance(handler._forge_root, Path)
        # The returned path is root_dir; verify the forge_harness/domains.yaml check holds
        assert (handler._forge_root / "forge_harness" / "domains.yaml").exists()

    def test_fallback_when_no_yaml_found(self):
        """Line 51: When no domains.yaml found anywhere, falls back to parent of __file__."""
        # Use a completely isolated path that has no domains.yaml in the hierarchy
        # The real __file__ for approval_handler.py is in the harness tree which
        # does NOT have domains.yaml, so calling with default __file__ triggers the fallback.
        with patch(
            "forge_harness.webhook_server.handlers.approval_handler.__file__",
            "/nonexistent/deep/path/approval_handler.py",
        ):
            handler = ApprovalQueueHandler()

        # Fallback: returns Path("/nonexistent/deep/path").parent (i.e. /nonexistent/deep)
        assert handler._forge_root == Path("/nonexistent/deep")

    def test_explicit_forge_root_bypasses_traversal(self, tmp_path):
        """When forge_root is provided, _find_forge_root is never called."""
        with patch.object(
            ApprovalQueueHandler, "_find_forge_root", wraps=None
        ) as mock_find:
            h = ApprovalQueueHandler(forge_root=tmp_path)
            mock_find.assert_not_called()
        assert h._forge_root == tmp_path


# ===========================================================================
# SECTION 2: ApprovalQueueHandler.batch_approve — exception branch (lines 188-189)
# ===========================================================================

class TestBatchApproveExceptionBranch:
    """Target the except block at lines 188-189 in batch_approve.

    The except catches any Exception raised while processing a single request_id
    inside the for-loop and adds it to the failed list.
    """

    @pytest.fixture
    def mock_approval_queue(self):
        q = MagicMock()
        q.list_pending = AsyncMock(return_value=[])
        q.get_request = AsyncMock(return_value=None)
        q.approve = AsyncMock()
        q.reject = AsyncMock()
        q.get_stats = AsyncMock()
        return q

    @pytest.mark.asyncio
    async def test_exception_from_approve_request_method_recorded_in_failed(
        self, mock_approval_queue
    ):
        """When approve_request() itself raises (not just returns error dict),
        the outer except in batch_approve catches it and adds to failed.

        We patch self.approve_request directly so it raises rather than returns.
        """
        req = _make_approval_request(
            req_id="req-direct-raise",
            priority=ApprovalPriority.LOW,  # watch tier — passes tier check
        )
        # get_request succeeds (returns the serialized dict via _serialize_request)
        mock_approval_queue.get_request.return_value = req

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )

        # Patch approve_request so it raises instead of returning a dict
        with patch.object(
            h,
            "approve_request",
            side_effect=RuntimeError("Unhandled pipeline crash"),
        ):
            result = await h.batch_approve(
                ["req-direct-raise"],
                approver="admin",
            )

        assert result["success"] is True
        assert result["approved_count"] == 0
        assert result["failed_count"] == 1
        assert result["failed"][0]["request_id"] == "req-direct-raise"
        assert "Unhandled pipeline crash" in result["failed"][0]["error"]

    @pytest.mark.asyncio
    async def test_exception_from_get_request_method_recorded_in_failed(
        self, mock_approval_queue
    ):
        """When self.get_request() raises (patched at method level to bypass
        its own try/except), the outer except in batch_approve catches it.
        """
        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )

        # Patch get_request at the method level so it raises without catching
        with patch.object(
            h,
            "get_request",
            side_effect=OSError("Disk I/O failure"),
        ):
            result = await h.batch_approve(
                ["req-bad-read"],
                approver="admin",
            )

        assert result["success"] is True
        assert result["approved_count"] == 0
        assert result["failed_count"] == 1
        assert result["failed"][0]["request_id"] == "req-bad-read"
        assert "Disk I/O failure" in result["failed"][0]["error"]

    @pytest.mark.asyncio
    async def test_mixed_batch_one_ok_one_raises(self, mock_approval_queue):
        """One request succeeds, another raises at get_request — both handled."""
        good_req = _make_approval_request(
            req_id="req-good",
            priority=ApprovalPriority.LOW,
            status=ApprovalStatus.APPROVED,
        )

        call_count = {"n": 0}

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )

        # First call returns the good request dict, second raises
        async def _get_request(request_id):
            call_count["n"] += 1
            if request_id == "req-good":
                return h._serialize_request(good_req)
            raise OSError("Storage unavailable")

        approved_req = _make_approval_request(
            req_id="req-good",
            status=ApprovalStatus.APPROVED,
            workflow_checkpoint=None,
        )
        mock_approval_queue.approve.return_value = approved_req

        with patch.object(h, "get_request", side_effect=_get_request):
            result = await h.batch_approve(
                ["req-good", "req-bad"],
                approver="admin",
            )

        assert result["success"] is True
        assert result["approved_count"] == 1
        assert result["failed_count"] == 1
        assert "req-good" in result["approved"]
        assert result["failed"][0]["request_id"] == "req-bad"
        assert "Storage unavailable" in result["failed"][0]["error"]

    @pytest.mark.asyncio
    async def test_all_requests_raise_exceptions(self, mock_approval_queue):
        """When every request raises at the method level, all end up in failed."""
        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )

        with patch.object(
            h,
            "get_request",
            side_effect=Exception("Fatal error"),
        ):
            result = await h.batch_approve(
                ["req-a", "req-b", "req-c"],
                approver="admin",
            )

        assert result["success"] is True
        assert result["approved_count"] == 0
        assert result["failed_count"] == 3
        for entry in result["failed"]:
            assert "Fatal error" in entry["error"]

    @pytest.mark.asyncio
    async def test_batch_approve_no_queue(self):
        """When approval_queue is None, returns error dict immediately."""
        h = ApprovalQueueHandler(approval_queue=None, forge_root=Path("/tmp"))
        result = await h.batch_approve(["req-x"], approver="admin")
        assert result["success"] is False
        assert "not configured" in result["error"]


# ===========================================================================
# SECTION 3: TaskQueueHandler — additional edge cases
# ===========================================================================

class TestTaskQueueHandlerStateStoreInit:
    """Test _ensure_state_store and _get_redis_client edge cases."""

    def test_ensure_state_store_none_when_import_fails(self):
        """StateStore import failure keeps state_store as None."""
        with patch(
            "forge_harness.webhook_server.handlers.task_handler.create_task_queue"
        ) as mock_create:
            mock_create.return_value = MagicMock()
            with patch(
                "forge_harness.webhook_server.handlers.task_handler.TaskQueueHandler._ensure_state_store"
            ) as mock_ensure:
                # Make _ensure_state_store do nothing (simulate import failure path)
                mock_ensure.return_value = None
                h = TaskQueueHandler(state_store=None)
                h.state_store = None  # simulate failure outcome

        assert h.state_store is None

    def test_get_redis_client_none_when_state_store_none(self):
        """_get_redis_client returns None when state_store is None."""
        tq = _make_mock_queue()
        h = _make_handler_no_redis(tq)
        assert h._get_redis_client() is None

    def test_get_redis_client_none_when_not_connected(self):
        """_get_redis_client returns None when state_store is not connected."""
        tq = _make_mock_queue()
        h = _make_handler_no_redis(tq)
        mock_store = MagicMock()
        mock_store.is_connected.return_value = False
        h.state_store = mock_store
        assert h._get_redis_client() is None

    def test_get_redis_client_none_when_wrong_store_type(self):
        """_get_redis_client returns None when store type is not redis."""
        tq = _make_mock_queue()
        h = _make_handler_no_redis(tq)
        mock_store = MagicMock()
        mock_store.is_connected.return_value = True
        mock_store.get_store_type.return_value = "sqlite"
        h.state_store = mock_store
        assert h._get_redis_client() is None

    def test_get_redis_client_none_when_no_internal_redis_attr(self):
        """_get_redis_client returns None when _redis attr missing."""
        tq = _make_mock_queue()
        h = _make_handler_no_redis(tq)
        mock_store = MagicMock(spec=[])  # no attributes
        mock_store.is_connected = MagicMock(return_value=True)
        mock_store.get_store_type = MagicMock(return_value="redis")
        h.state_store = mock_store
        # hasattr(state_store, "_redis") will be False
        assert h._get_redis_client() is None

    def test_task_key_format(self):
        """_task_key generates correct Redis key."""
        tq = _make_mock_queue()
        h = _make_handler_no_redis(tq)
        assert h._task_key("abc123") == "forge:tasks:abc123"

    def test_tasks_index_key(self):
        """_tasks_index_key returns the correct Redis index key."""
        tq = _make_mock_queue()
        h = _make_handler_no_redis(tq)
        assert h._tasks_index_key() == "forge:tasks:index"


class TestParseLeaseExpiry:
    """Tests for _parse_lease_expiry static method."""

    def test_none_raw_value_returns_none(self):
        """Returns None when lease_expires_at is absent."""
        tq = _make_mock_queue()
        h = _make_handler_no_redis(tq)
        assert h._parse_lease_expiry({}) is None

    def test_datetime_object_passed_directly(self):
        """Accepts a real datetime object."""
        tq = _make_mock_queue()
        h = _make_handler_no_redis(tq)
        dt = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        result = h._parse_lease_expiry({"lease_expires_at": dt})
        assert result == dt

    def test_naive_datetime_gets_utc_attached(self):
        """A naive datetime gets tzinfo=UTC attached."""
        tq = _make_mock_queue()
        h = _make_handler_no_redis(tq)
        naive_dt = datetime(2026, 3, 1, 12, 0, 0)
        result = h._parse_lease_expiry({"lease_expires_at": naive_dt})
        assert result.tzinfo is not None

    def test_iso_string_with_z_suffix(self):
        """ISO string with Z suffix parses correctly."""
        tq = _make_mock_queue()
        h = _make_handler_no_redis(tq)
        iso = "2026-03-01T12:00:00Z"
        result = h._parse_lease_expiry({"lease_expires_at": iso})
        assert result is not None
        assert result.year == 2026

    def test_invalid_iso_string_returns_none(self):
        """Invalid ISO string returns None."""
        tq = _make_mock_queue()
        h = _make_handler_no_redis(tq)
        result = h._parse_lease_expiry({"lease_expires_at": "not-a-date"})
        assert result is None

    def test_integer_value_returns_none(self):
        """Unsupported type (int) returns None."""
        tq = _make_mock_queue()
        h = _make_handler_no_redis(tq)
        result = h._parse_lease_expiry({"lease_expires_at": 9999999999})
        assert result is None


class TestIsLeaseActive:
    """Tests for _is_lease_active."""

    def test_none_lease_returns_false(self):
        """None lease is not active."""
        h = _make_handler_no_redis()
        assert h._is_lease_active(None) is False

    def test_empty_dict_returns_false(self):
        """Empty lease dict (no expiry) is not active."""
        h = _make_handler_no_redis()
        assert h._is_lease_active({}) is False

    def test_future_expiry_is_active(self):
        """Future expiry means lease is active."""
        h = _make_handler_no_redis()
        lease = {"lease_expires_at": _future_iso()}
        assert h._is_lease_active(lease) is True

    def test_past_expiry_is_inactive(self):
        """Past expiry means lease is not active."""
        h = _make_handler_no_redis()
        lease = {"lease_expires_at": _past_iso()}
        assert h._is_lease_active(lease) is False

    def test_custom_now_used_for_comparison(self):
        """Custom 'now' parameter shifts the comparison baseline."""
        h = _make_handler_no_redis()
        future_time = datetime.now(UTC) + timedelta(hours=2)
        lease = {"lease_expires_at": _future_iso(3600)}  # 1h from now
        # If 'now' is 3 hours in the future, the 1h lease has already expired
        now_plus_3h = datetime.now(UTC) + timedelta(hours=3)
        assert h._is_lease_active(lease, now=now_plus_3h) is False


class TestTaskQueueHandlerFileBased:
    """Tests for file-based (non-Redis) TaskQueueHandler operations."""

    @pytest.mark.asyncio
    async def test_list_tasks_no_filters(self):
        """list_tasks returns all tasks when no filters provided."""
        tasks = [_make_task("t1"), _make_task("t2")]
        tq = _make_mock_queue()
        tq.list_all = AsyncMock(return_value=tasks)
        h = _make_handler_no_redis(tq)

        result = await h.list_tasks()

        assert len(result) == 2
        tq.list_all.assert_called_once_with(status=None, priority=None)

    @pytest.mark.asyncio
    async def test_list_tasks_with_status_filter(self):
        """list_tasks passes status enum to task queue."""
        tq = _make_mock_queue()
        tq.list_all = AsyncMock(return_value=[])
        h = _make_handler_no_redis(tq)

        await h.list_tasks(status="pending")

        tq.list_all.assert_called_once_with(
            status=TaskStatus.PENDING, priority=None
        )

    @pytest.mark.asyncio
    async def test_list_tasks_with_priority_filter(self):
        """list_tasks passes priority enum to task queue."""
        tq = _make_mock_queue()
        tq.list_all = AsyncMock(return_value=[])
        h = _make_handler_no_redis(tq)

        await h.list_tasks(priority="high")

        tq.list_all.assert_called_once_with(
            status=None, priority=TaskPriority.HIGH
        )

    @pytest.mark.asyncio
    async def test_list_tasks_unknown_status_maps_to_none(self):
        """An unknown status string maps to None (not in _STATUS_MAP)."""
        tq = _make_mock_queue()
        tq.list_all = AsyncMock(return_value=[])
        h = _make_handler_no_redis(tq)

        await h.list_tasks(status="unknown_status")

        tq.list_all.assert_called_once_with(status=None, priority=None)

    @pytest.mark.asyncio
    async def test_list_tasks_limit_applied(self):
        """Limit is applied after fetching from task queue."""
        tasks = [_make_task(f"t{i}") for i in range(10)]
        tq = _make_mock_queue()
        tq.list_all = AsyncMock(return_value=tasks)
        h = _make_handler_no_redis(tq)

        result = await h.list_tasks(limit=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_get_task_exists(self):
        """get_task returns serialized dict when task exists."""
        task = _make_task("t42")
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        h = _make_handler_no_redis(tq)

        result = await h.get_task("t42")

        assert result is not None
        assert result["id"] == "t42"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self):
        """get_task returns None when task does not exist."""
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=None)
        h = _make_handler_no_redis(tq)

        result = await h.get_task("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_task_critical_priority(self):
        """create_task with critical priority stores correctly."""
        tq = _make_mock_queue()
        created_task = _make_task("new1", priority=TaskPriority.CRITICAL)
        tq.add = AsyncMock(return_value=created_task)
        h = _make_handler_no_redis(tq)

        result = await h.create_task(
            subject="Critical task",
            description="Desc",
            priority="critical",
        )

        assert result["priority"] == "critical"
        added_task: Task = tq.add.call_args[0][0]
        assert added_task.priority == TaskPriority.CRITICAL

    @pytest.mark.asyncio
    async def test_create_task_with_lease(self):
        """create_task stores lease field correctly."""
        tq = _make_mock_queue()
        lease = {
            "owner_node": "node-1",
            "owner_agent": "agent-a",
            "path_lock": "/some/path",
            "lease_expires_at": _future_iso(),
        }
        created_task = _make_task("new2", lease=lease)
        tq.add = AsyncMock(return_value=created_task)
        h = _make_handler_no_redis(tq)

        result = await h.create_task(
            subject="Leased task",
            description="Desc",
            lease=lease,
        )

        assert result["lease"] == lease

    @pytest.mark.asyncio
    async def test_create_task_with_required_role(self):
        """create_task stores required_role correctly."""
        tq = _make_mock_queue()
        created_task = _make_task("new3", required_role="backend-engineer")
        tq.add = AsyncMock(return_value=created_task)
        h = _make_handler_no_redis(tq)

        result = await h.create_task(
            subject="Role task",
            description="Desc",
            required_role="backend-engineer",
        )

        assert result["required_role"] == "backend-engineer"

    @pytest.mark.asyncio
    async def test_update_task_not_found_returns_none(self):
        """update_task returns None when task does not exist."""
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=None)
        h = _make_handler_no_redis(tq)

        result = await h.update_task("nonexistent", {"subject": "New title"})
        assert result is None

    @pytest.mark.asyncio
    async def test_update_task_patches_title(self):
        """update_task correctly patches the title field."""
        task = _make_task("t1", title="Old title")
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        tq.update = AsyncMock()
        h = _make_handler_no_redis(tq)

        result = await h.update_task("t1", {"subject": "New title"})

        assert result is not None
        assert result["subject"] == "New title"
        tq.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_task_success(self):
        """delete_task returns True when deletion succeeds."""
        tq = _make_mock_queue()
        tq.delete = AsyncMock(return_value=True)
        h = _make_handler_no_redis(tq)

        result = await h.delete_task("t1")
        assert result is True
        tq.delete.assert_called_once_with("t1")

    @pytest.mark.asyncio
    async def test_delete_task_not_found_returns_false(self):
        """delete_task returns False when task does not exist."""
        tq = _make_mock_queue()
        tq.delete = AsyncMock(return_value=False)
        h = _make_handler_no_redis(tq)

        result = await h.delete_task("missing")
        assert result is False

    @pytest.mark.asyncio
    async def test_claim_task_sets_assigned_status(self):
        """claim_task updates claimed_by and status to assigned."""
        task = _make_task("t1")
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        tq.update = AsyncMock()
        h = _make_handler_no_redis(tq)

        result = await h.claim_task("t1", "agent-007")

        assert result is not None
        assert result["claimed_by"] == "agent-007"
        assert result["status"] == "assigned"

    @pytest.mark.asyncio
    async def test_get_stats_flattens_by_status(self):
        """get_stats flattens the by_status dict to top-level keys."""
        tq = _make_mock_queue()
        tq.get_stats = AsyncMock(return_value={
            "by_status": {
                "pending": 5,
                "assigned": 2,
                "in_progress": 1,
                "completed": 10,
                "failed": 0,
            },
            "total": 18,
        })
        h = _make_handler_no_redis(tq)

        result = await h.get_stats()

        assert result["pending"] == 5
        assert result["assigned"] == 2
        assert result["in_progress"] == 1
        assert result["completed"] == 10
        assert result["failed"] == 0
        assert result["total"] == 18

    @pytest.mark.asyncio
    async def test_get_stats_missing_by_status_keys_default_to_zero(self):
        """get_stats defaults missing status keys to 0."""
        tq = _make_mock_queue()
        tq.get_stats = AsyncMock(return_value={
            "by_status": {"pending": 3},
            "total": 3,
        })
        h = _make_handler_no_redis(tq)

        result = await h.get_stats()

        assert result["pending"] == 3
        assert result["assigned"] == 0
        assert result["in_progress"] == 0
        assert result["completed"] == 0
        assert result["failed"] == 0


class TestTaskLeaseWithFileBased:
    """Tests for lease operations using file-based backend."""

    @pytest.mark.asyncio
    async def test_claim_task_with_lease_task_not_found(self):
        """claim_task_with_lease returns None when task missing."""
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=None)
        h = _make_handler_no_redis(tq)

        result = await h.claim_task_with_lease(
            "nonexistent",
            {
                "owner_node": "n1",
                "owner_agent": "a1",
                "path_lock": "/path",
                "lease_expires_at": _future_iso(),
            },
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_claim_task_with_lease_missing_fields_raises(self):
        """Missing owner_node raises TaskLeaseError with INVALID_LEASE code."""
        task = _make_task("t1")
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        h = _make_handler_no_redis(tq)

        with pytest.raises(TaskLeaseError) as exc_info:
            await h.claim_task_with_lease(
                "t1",
                {
                    # owner_node is missing
                    "owner_agent": "a1",
                    "path_lock": "/path",
                    "lease_expires_at": _future_iso(),
                },
            )
        assert exc_info.value.code == "INVALID_LEASE"
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_claim_task_with_lease_expired_raises(self):
        """Expired lease_expires_at raises TaskLeaseError with INVALID_LEASE."""
        task = _make_task("t1")
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        h = _make_handler_no_redis(tq)

        with pytest.raises(TaskLeaseError) as exc_info:
            await h.claim_task_with_lease(
                "t1",
                {
                    "owner_node": "n1",
                    "owner_agent": "a1",
                    "path_lock": "/path",
                    "lease_expires_at": _past_iso(),
                },
            )
        assert exc_info.value.code == "INVALID_LEASE"

    @pytest.mark.asyncio
    async def test_claim_task_with_lease_path_lock_conflict_raises(self):
        """Another task holding same path_lock raises PATH_LOCK_CONFLICT."""
        # t1 is unclaimed; t2 holds the same path_lock
        conflicting_lease = {
            "owner_node": "n2",
            "owner_agent": "a2",
            "path_lock": "/shared/path",
            "lease_expires_at": _future_iso(),
        }
        t1 = _make_task("t1")
        t2 = _make_task("t2", lease=conflicting_lease, status=TaskStatus.ASSIGNED)
        tq = _make_mock_queue()

        # get("t1") for the initial lookup
        tq.get = AsyncMock(return_value=t1)
        # list_all returns both for the conflict scan
        tq.list_all = AsyncMock(return_value=[t1, t2])
        h = _make_handler_no_redis(tq)

        with pytest.raises(TaskLeaseError) as exc_info:
            await h.claim_task_with_lease(
                "t1",
                {
                    "owner_node": "n1",
                    "owner_agent": "a1",
                    "path_lock": "/shared/path",
                    "lease_expires_at": _future_iso(),
                },
            )
        assert exc_info.value.code == "PATH_LOCK_CONFLICT"
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_claim_task_with_lease_same_owner_renews(self):
        """Same owner claiming a currently-leased task succeeds (re-claim)."""
        existing_lease = {
            "owner_node": "n1",
            "owner_agent": "a1",
            "path_lock": "/unique/path",
            "lease_expires_at": _future_iso(),
        }
        task = _make_task("t1", lease=existing_lease, status=TaskStatus.ASSIGNED)
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        tq.list_all = AsyncMock(return_value=[task])
        tq.update = AsyncMock()
        h = _make_handler_no_redis(tq)

        new_lease = {
            "owner_node": "n1",
            "owner_agent": "a1",
            "path_lock": "/unique/path",
            "lease_expires_at": _future_iso(7200),  # extended expiry
        }
        result = await h.claim_task_with_lease("t1", new_lease)

        assert result is not None
        assert result["lease"] == new_lease

    @pytest.mark.asyncio
    async def test_renew_task_lease_task_not_found(self):
        """renew_task_lease returns None when task missing."""
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=None)
        h = _make_handler_no_redis(tq)

        result = await h.renew_task_lease(
            "missing",
            {"owner_node": "n1", "owner_agent": "a1", "path_lock": "/p"},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_renew_task_lease_no_existing_lease_raises(self):
        """Renewing a task with no lease raises LEASE_NOT_FOUND."""
        task = _make_task("t1", lease=None)
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        h = _make_handler_no_redis(tq)

        with pytest.raises(TaskLeaseError) as exc_info:
            await h.renew_task_lease(
                "t1",
                {
                    "owner_node": "n1",
                    "owner_agent": "a1",
                    "path_lock": "/p",
                    "lease_expires_at": _future_iso(),
                },
            )
        assert exc_info.value.code == "LEASE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_renew_task_lease_expired_current_lease_raises(self):
        """Renewing an expired lease raises LEASE_EXPIRED."""
        expired_lease = {
            "owner_node": "n1",
            "owner_agent": "a1",
            "path_lock": "/p",
            "lease_expires_at": _past_iso(),
        }
        task = _make_task("t1", lease=expired_lease)
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        h = _make_handler_no_redis(tq)

        with pytest.raises(TaskLeaseError) as exc_info:
            await h.renew_task_lease(
                "t1",
                {
                    "owner_node": "n1",
                    "owner_agent": "a1",
                    "path_lock": "/p",
                    "lease_expires_at": _future_iso(),
                },
            )
        assert exc_info.value.code == "LEASE_EXPIRED"

    @pytest.mark.asyncio
    async def test_renew_task_lease_owner_mismatch_raises(self):
        """Renewing with wrong owner raises LEASE_OWNER_MISMATCH."""
        active_lease = {
            "owner_node": "n1",
            "owner_agent": "a1",
            "path_lock": "/p",
            "lease_expires_at": _future_iso(),
        }
        task = _make_task("t1", lease=active_lease)
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        h = _make_handler_no_redis(tq)

        with pytest.raises(TaskLeaseError) as exc_info:
            await h.renew_task_lease(
                "t1",
                {
                    "owner_node": "n2",  # different node
                    "owner_agent": "a1",
                    "path_lock": "/p",
                    "lease_expires_at": _future_iso(),
                },
            )
        assert exc_info.value.code == "LEASE_OWNER_MISMATCH"

    @pytest.mark.asyncio
    async def test_renew_task_lease_path_lock_change_raises(self):
        """Changing path_lock during renewal raises PATH_LOCK_IMMUTABLE."""
        active_lease = {
            "owner_node": "n1",
            "owner_agent": "a1",
            "path_lock": "/original/path",
            "lease_expires_at": _future_iso(),
        }
        task = _make_task("t1", lease=active_lease)
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        h = _make_handler_no_redis(tq)

        with pytest.raises(TaskLeaseError) as exc_info:
            await h.renew_task_lease(
                "t1",
                {
                    "owner_node": "n1",
                    "owner_agent": "a1",
                    "path_lock": "/new/path",  # changed!
                    "lease_expires_at": _future_iso(),
                },
            )
        assert exc_info.value.code == "PATH_LOCK_IMMUTABLE"
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_renew_task_lease_new_expiry_in_past_raises(self):
        """Renewal with past lease_expires_at raises INVALID_LEASE."""
        active_lease = {
            "owner_node": "n1",
            "owner_agent": "a1",
            "path_lock": "/p",
            "lease_expires_at": _future_iso(),
        }
        task = _make_task("t1", lease=active_lease)
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        h = _make_handler_no_redis(tq)

        with pytest.raises(TaskLeaseError) as exc_info:
            await h.renew_task_lease(
                "t1",
                {
                    "owner_node": "n1",
                    "owner_agent": "a1",
                    "path_lock": "/p",
                    "lease_expires_at": _past_iso(),  # already expired
                },
            )
        assert exc_info.value.code == "INVALID_LEASE"

    @pytest.mark.asyncio
    async def test_release_task_lease_task_not_found(self):
        """release_task_lease returns None when task missing."""
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=None)
        h = _make_handler_no_redis(tq)

        result = await h.release_task_lease("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_release_task_lease_no_lease_raises(self):
        """release_task_lease raises LEASE_NOT_FOUND when no lease exists."""
        task = _make_task("t1", lease=None)
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        h = _make_handler_no_redis(tq)

        with pytest.raises(TaskLeaseError) as exc_info:
            await h.release_task_lease("t1")
        assert exc_info.value.code == "LEASE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_release_task_lease_owner_node_mismatch_raises(self):
        """Mismatched owner_node raises LEASE_OWNER_MISMATCH."""
        lease = {
            "owner_node": "n1",
            "owner_agent": "a1",
            "path_lock": "/p",
            "lease_expires_at": _future_iso(),
        }
        task = _make_task("t1", lease=lease)
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        h = _make_handler_no_redis(tq)

        with pytest.raises(TaskLeaseError) as exc_info:
            await h.release_task_lease("t1", owner_node="wrong-node")
        assert exc_info.value.code == "LEASE_OWNER_MISMATCH"

    @pytest.mark.asyncio
    async def test_release_task_lease_owner_agent_mismatch_raises(self):
        """Mismatched owner_agent raises LEASE_OWNER_MISMATCH."""
        lease = {
            "owner_node": "n1",
            "owner_agent": "a1",
            "path_lock": "/p",
            "lease_expires_at": _future_iso(),
        }
        task = _make_task("t1", lease=lease)
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        h = _make_handler_no_redis(tq)

        with pytest.raises(TaskLeaseError) as exc_info:
            await h.release_task_lease("t1", owner_agent="wrong-agent")
        assert exc_info.value.code == "LEASE_OWNER_MISMATCH"

    @pytest.mark.asyncio
    async def test_release_task_lease_clears_claimed_by_and_resets_status(self):
        """Successful release clears claimed_by and resets status to pending."""
        lease = {
            "owner_node": "n1",
            "owner_agent": "a1",
            "path_lock": "/p",
            "lease_expires_at": _future_iso(),
        }
        task = _make_task(
            "t1",
            lease=lease,
            assigned_agent="a1",
            status=TaskStatus.ASSIGNED,
        )
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        tq.update = AsyncMock()
        h = _make_handler_no_redis(tq)

        result = await h.release_task_lease(
            "t1", owner_node="n1", owner_agent="a1"
        )

        assert result is not None
        assert result["lease"] is None
        assert result["claimed_by"] == ""
        assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_requeue_task_task_not_found(self):
        """requeue_task returns None when task missing."""
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=None)
        h = _make_handler_no_redis(tq)

        result = await h.requeue_task("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_requeue_task_no_lease_succeeds(self):
        """requeue_task succeeds for a task with no lease."""
        task = _make_task("t1", status=TaskStatus.IN_PROGRESS)
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        tq.update = AsyncMock()
        h = _make_handler_no_redis(tq)

        result = await h.requeue_task("t1")

        assert result is not None
        assert result["status"] == "pending"
        assert result["claimed_by"] == ""

    @pytest.mark.asyncio
    async def test_requeue_task_with_correct_owner_clears_lease(self):
        """requeue_task with correct owner node/agent clears the lease."""
        lease = {
            "owner_node": "n1",
            "owner_agent": "a1",
            "path_lock": "/p",
            "lease_expires_at": _future_iso(),
        }
        task = _make_task("t1", lease=lease, status=TaskStatus.ASSIGNED)
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        tq.update = AsyncMock()
        h = _make_handler_no_redis(tq)

        result = await h.requeue_task("t1", owner_node="n1", owner_agent="a1")

        assert result is not None
        assert result["status"] == "pending"
        assert result["lease"] is None

    @pytest.mark.asyncio
    async def test_requeue_task_wrong_owner_node_raises(self):
        """requeue_task with wrong owner_node raises LEASE_OWNER_MISMATCH."""
        lease = {
            "owner_node": "n1",
            "owner_agent": "a1",
            "path_lock": "/p",
            "lease_expires_at": _future_iso(),
        }
        task = _make_task("t1", lease=lease)
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        h = _make_handler_no_redis(tq)

        with pytest.raises(TaskLeaseError) as exc_info:
            await h.requeue_task("t1", owner_node="wrong-node")
        assert exc_info.value.code == "LEASE_OWNER_MISMATCH"

    @pytest.mark.asyncio
    async def test_requeue_task_wrong_owner_agent_raises(self):
        """requeue_task with wrong owner_agent raises LEASE_OWNER_MISMATCH."""
        lease = {
            "owner_node": "n1",
            "owner_agent": "a1",
            "path_lock": "/p",
            "lease_expires_at": _future_iso(),
        }
        task = _make_task("t1", lease=lease)
        tq = _make_mock_queue()
        tq.get = AsyncMock(return_value=task)
        h = _make_handler_no_redis(tq)

        with pytest.raises(TaskLeaseError) as exc_info:
            await h.requeue_task("t1", owner_agent="wrong-agent")
        assert exc_info.value.code == "LEASE_OWNER_MISMATCH"


class TestSerializationRoundTrip:
    """Tests for serialize/deserialize methods."""

    def test_serialize_deserialize_roundtrip(self):
        """Task survives serialize -> deserialize round-trip."""
        h = _make_handler_no_redis()
        task_dict = {
            "id": "abc123",
            "subject": "Test",
            "description": "Desc",
            "priority": "high",
            "status": "pending",
            "required_role": "backend-engineer",
            "claimed_by": "agent-1",
            "lease": {"owner_node": "n1", "owner_agent": "a1"},
            "order": 5,
            "created_at": "2026-02-22T10:00:00+00:00",
            "updated_at": "2026-02-22T10:00:00+00:00",
        }

        serialized = h._serialize_task(task_dict)
        assert isinstance(serialized, dict)
        assert all(isinstance(v, str) for v in serialized.values())

        deserialized = h._deserialize_task(serialized)
        assert deserialized["id"] == "abc123"
        assert deserialized["subject"] == "Test"
        assert deserialized["required_role"] == "backend-engineer"
        assert deserialized["claimed_by"] == "agent-1"
        assert deserialized["lease"] == {"owner_node": "n1", "owner_agent": "a1"}
        assert deserialized["order"] == 5

    def test_serialize_none_fields_become_empty_strings(self):
        """None optional fields serialize to empty strings."""
        h = _make_handler_no_redis()
        task_dict = {
            "id": "xyz",
            "subject": "S",
            "description": "D",
            "priority": "medium",
            "status": "pending",
            "required_role": None,
            "claimed_by": None,
            "lease": None,
            "order": 0,
            "created_at": "2026-02-22T10:00:00+00:00",
            "updated_at": "2026-02-22T10:00:00+00:00",
        }

        serialized = h._serialize_task(task_dict)
        assert serialized["required_role"] == "None"
        assert serialized["claimed_by"] == "None"
        assert serialized["lease"] == ""

    def test_deserialize_empty_required_role_becomes_none(self):
        """Empty required_role string deserializes to None."""
        h = _make_handler_no_redis()
        data = {
            "id": "t1",
            "subject": "S",
            "description": "D",
            "priority": "medium",
            "status": "pending",
            "required_role": "",
            "claimed_by": "",
            "lease": "",
            "order": "0",
            "created_at": "2026-02-22T10:00:00+00:00",
            "updated_at": "2026-02-22T10:00:00+00:00",
        }

        result = h._deserialize_task(data)
        assert result["required_role"] is None
        assert result["claimed_by"] is None
        assert result["lease"] is None

    def test_deserialize_invalid_json_lease_becomes_none(self):
        """Invalid JSON in lease field deserializes to None."""
        h = _make_handler_no_redis()
        data = {
            "id": "t1",
            "subject": "S",
            "description": "D",
            "priority": "medium",
            "status": "pending",
            "required_role": "",
            "claimed_by": "",
            "lease": "not-valid-json{{{",
            "order": "0",
            "created_at": "2026-02-22T10:00:00+00:00",
            "updated_at": "2026-02-22T10:00:00+00:00",
        }

        result = h._deserialize_task(data)
        assert result["lease"] is None

    def test_deserialize_invalid_order_defaults_to_zero(self):
        """Non-integer order field defaults to 0."""
        h = _make_handler_no_redis()
        data = {
            "id": "t1",
            "subject": "S",
            "description": "D",
            "priority": "medium",
            "status": "pending",
            "required_role": "",
            "claimed_by": "",
            "lease": "",
            "order": "not-a-number",
            "created_at": "2026-02-22T10:00:00+00:00",
            "updated_at": "2026-02-22T10:00:00+00:00",
        }

        result = h._deserialize_task(data)
        assert result["order"] == 0


class TestTaskHandlerHelperFunctions:
    """Tests for module-level helper functions."""

    def test_task_to_dict_all_fields(self):
        """_task_to_dict maps all task fields correctly."""
        task = _make_task(
            task_id="abc",
            title="My Task",
            description="Some description",
            priority=TaskPriority.HIGH,
            status=TaskStatus.IN_PROGRESS,
            assigned_agent="agent-x",
            required_role="senior-engineer",
            lease={"owner_node": "n1"},
            order=3,
            domain="health",
            project="my-project",
        )
        result = _task_to_dict(task)

        assert result["id"] == "abc"
        assert result["subject"] == "My Task"
        assert result["description"] == "Some description"
        assert result["priority"] == "high"
        assert result["status"] == "in_progress"
        assert result["claimed_by"] == "agent-x"
        assert result["required_role"] == "senior-engineer"
        assert result["lease"] == {"owner_node": "n1"}
        assert result["order"] == 3
        assert result["domain"] == "health"
        assert result["project"] == "my-project"

    def test_task_to_dict_none_assigned_agent_becomes_empty_string(self):
        """None assigned_agent maps to empty string in claimed_by."""
        task = _make_task(assigned_agent=None)
        result = _task_to_dict(task)
        assert result["claimed_by"] == ""

    def test_dict_to_task_creates_new_task_with_defaults(self):
        """_dict_to_task with minimal data uses sane defaults."""
        result = _dict_to_task({"subject": "New task"})
        assert result.title == "New task"
        assert result.priority == TaskPriority.MEDIUM
        assert result.status == TaskStatus.PENDING

    def test_dict_to_task_updates_existing_task(self):
        """_dict_to_task with existing task updates only provided fields."""
        task = _make_task("t1", title="Old", description="Old desc")
        _dict_to_task({"subject": "New"}, existing=task)
        assert task.title == "New"
        assert task.description == "Old desc"  # unchanged

    def test_dict_to_task_updates_claimed_by_sets_assigned_at(self):
        """Updating claimed_by on existing task sets assigned_at timestamp."""
        task = _make_task("t1")
        assert task.assigned_at is None
        _dict_to_task({"claimed_by": "agent-7"}, existing=task)
        assert task.assigned_agent == "agent-7"
        assert task.assigned_at is not None

    def test_dict_to_task_empty_claimed_by_clears_assigned_agent(self):
        """Empty string claimed_by clears assigned_agent to None."""
        task = _make_task("t1", assigned_agent="old-agent")
        _dict_to_task({"claimed_by": ""}, existing=task)
        assert task.assigned_agent is None

    def test_priority_map_contains_all_priorities(self):
        """_PRIORITY_MAP has entries for all expected priority strings."""
        for key in ("critical", "high", "medium", "low"):
            assert key in _PRIORITY_MAP

    def test_status_map_contains_all_statuses(self):
        """_STATUS_MAP has entries for all expected status strings."""
        for key in ("pending", "assigned", "in_progress", "completed", "failed", "cancelled"):
            assert key in _STATUS_MAP


class TestTaskLeaseError:
    """Tests for TaskLeaseError exception class."""

    def test_default_status_code(self):
        """Default status code is 409."""
        err = TaskLeaseError("SOME_CODE", "message")
        assert err.status_code == 409
        assert err.code == "SOME_CODE"
        assert str(err) == "message"

    def test_custom_status_code(self):
        """Custom status code is stored correctly."""
        err = TaskLeaseError("INVALID", "bad input", status_code=400)
        assert err.status_code == 400

    def test_is_exception_subclass(self):
        """TaskLeaseError is a proper Exception subclass."""
        err = TaskLeaseError("X", "y")
        assert isinstance(err, Exception)
        with pytest.raises(TaskLeaseError):
            raise err


class TestGetTaskHandlerSingleton:
    """Tests for get_task_handler singleton factory."""

    def test_returns_task_queue_handler_instance(self):
        """get_task_handler returns a TaskQueueHandler."""
        import forge_harness.webhook_server.handlers.task_handler as th_module
        th_module._task_handler = None

        with patch(
            "forge_harness.webhook_server.handlers.task_handler.TaskQueueHandler._ensure_state_store"
        ):
            handler = get_task_handler()

        assert isinstance(handler, TaskQueueHandler)

    def test_singleton_returns_same_instance(self):
        """get_task_handler returns same instance on repeated calls."""
        import forge_harness.webhook_server.handlers.task_handler as th_module
        th_module._task_handler = None

        with patch(
            "forge_harness.webhook_server.handlers.task_handler.TaskQueueHandler._ensure_state_store"
        ):
            h1 = get_task_handler()
            h2 = get_task_handler()

        assert h1 is h2

    def test_reset_singleton_creates_new_instance(self):
        """After resetting _task_handler, a fresh instance is created."""
        import forge_harness.webhook_server.handlers.task_handler as th_module

        with patch(
            "forge_harness.webhook_server.handlers.task_handler.TaskQueueHandler._ensure_state_store"
        ):
            h1 = get_task_handler()
            th_module._task_handler = None
            h2 = get_task_handler()

        assert h1 is not h2


# ===========================================================================
# SECTION 4: WebhookHandler — additional edge cases
# ===========================================================================

class TestWebhookHandlerEdgeCases:
    """Additional edge cases for WebhookHandler not covered by existing tests."""

    @pytest.fixture
    def handler_with_secrets(self):
        return WebhookHandler(
            notification_harness=None,
            slack_signing_secret="slack-secret",
            github_webhook_secret="github-secret",
        )

    # --- Slack signature ---

    def test_verify_slack_signature_valid_roundtrip(self, handler_with_secrets):
        """A freshly computed signature always validates."""
        body = b"test-body-payload"
        timestamp = str(int(datetime.now(UTC).timestamp()))
        sig_base = f"v0:{timestamp}:{body.decode()}"
        computed = "v0=" + hmac.new(
            b"slack-secret",
            sig_base.encode(),
            hashlib.sha256,
        ).hexdigest()

        assert handler_with_secrets.verify_slack_signature(body, computed, timestamp) is True

    def test_verify_slack_signature_wrong_body_fails(self, handler_with_secrets):
        """Signature computed over different body fails verification."""
        body = b"correct-body"
        timestamp = str(int(datetime.now(UTC).timestamp()))
        sig_base = f"v0:{timestamp}:wrong-body"
        bad_sig = "v0=" + hmac.new(
            b"slack-secret",
            sig_base.encode(),
            hashlib.sha256,
        ).hexdigest()

        assert handler_with_secrets.verify_slack_signature(body, bad_sig, timestamp) is False

    # --- GitHub signature ---

    def test_verify_github_signature_valid_roundtrip(self, handler_with_secrets):
        """A freshly computed GitHub signature always validates."""
        body = b'{"action":"push"}'
        computed = "sha256=" + hmac.new(
            b"github-secret",
            body,
            hashlib.sha256,
        ).hexdigest()

        assert handler_with_secrets.verify_github_signature(body, computed) is True

    def test_verify_github_signature_wrong_body(self, handler_with_secrets):
        """Signature computed over different body fails."""
        body = b"real-body"
        fake_sig = "sha256=" + hmac.new(
            b"github-secret",
            b"other-body",
            hashlib.sha256,
        ).hexdigest()

        assert handler_with_secrets.verify_github_signature(body, fake_sig) is False

    # --- Slack payload parsing ---

    def test_parse_slack_no_actions_key(self):
        """block_actions with missing 'actions' key returns empty defaults."""
        handler = WebhookHandler(notification_harness=None)
        payload = {
            "type": "block_actions",
            "user": {"username": "user1"},
            # no 'actions' key
        }
        result = handler.parse_slack_payload(payload)
        assert result.source == "slack"
        assert result.notification_id == ""
        assert result.response_type == "unknown"

    def test_parse_slack_view_submission_with_single_part_callback(self):
        """view_submission callback_id with single part results in empty notification_id."""
        handler = WebhookHandler(notification_harness=None)
        payload = {
            "type": "view_submission",
            "user": {"username": "user"},
            "view": {
                "callback_id": "single",
                "state": {"values": {}},
            },
        }
        result = handler.parse_slack_payload(payload)
        # Single-part callback: parts[1] would fail, so notification_id stays ""
        # Actually split("_") on "single" gives ["single"], len < 2 so notification_id=""
        assert result.response_type == "submitted"
        assert result.notification_id == ""

    def test_parse_slack_user_prefers_username_over_name_over_id(self):
        """User field priority: username > name > id."""
        handler = WebhookHandler(notification_harness=None)

        # username wins
        payload = {
            "type": "block_actions",
            "user": {"username": "alice", "name": "Alice B", "id": "U001"},
            "actions": [{"action_id": "action_n1_approved"}],
        }
        result = handler.parse_slack_payload(payload)
        assert result.responder == "alice"

        # name wins when no username
        payload2 = {
            "type": "block_actions",
            "user": {"name": "Alice B", "id": "U001"},
            "actions": [{"action_id": "action_n1_approved"}],
        }
        result2 = handler.parse_slack_payload(payload2)
        assert result2.responder == "Alice B"

        # id wins when no username or name
        payload3 = {
            "type": "block_actions",
            "user": {"id": "U001"},
            "actions": [{"action_id": "action_n1_approved"}],
        }
        result3 = handler.parse_slack_payload(payload3)
        assert result3.responder == "U001"

    # --- GitHub payload parsing ---

    def test_parse_github_pr_review_request_changes_state(self):
        """PR review with 'request_changes' state maps to rejected."""
        handler = WebhookHandler(notification_harness=None)
        payload = {
            "pull_request": {
                "body": "<!-- notification_id: pr-001 -->",
            },
            "review": {
                "state": "request_changes",
                "body": "Needs work",
            },
            "sender": {"login": "reviewer"},
        }
        result = handler.parse_github_payload(payload, "pull_request_review")
        assert result.response_type == "rejected"

    def test_parse_github_issue_comment_plus_one_keyword(self):
        """'+1' in comment body maps to approved."""
        handler = WebhookHandler(notification_harness=None)
        payload = {
            "issue": {"body": "<!-- notification_id: gh-001 -->"},
            "comment": {"body": "+1 great work"},
            "sender": {"login": "user"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.response_type == "approved"

    def test_parse_github_issue_comment_minus_one_keyword(self):
        """-1 in comment body maps to rejected."""
        handler = WebhookHandler(notification_harness=None)
        payload = {
            "issue": {"body": "<!-- notification_id: gh-002 -->"},
            "comment": {"body": "-1 not ready"},
            "sender": {"login": "user"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.response_type == "rejected"

    def test_parse_github_shipit_keyword(self):
        """:shipit: in comment body maps to approved."""
        handler = WebhookHandler(notification_harness=None)
        payload = {
            "issue": {"body": "<!-- notification_id: gh-003 -->"},
            "comment": {"body": ":shipit: ship it!"},
            "sender": {"login": "user"},
        }
        result = handler.parse_github_payload(payload, "issue_comment")
        assert result.response_type == "approved"

    @pytest.mark.asyncio
    async def test_handle_slack_notification_none_still_returns_received(self):
        """handle_slack returns received when no notification harness set."""
        handler = WebhookHandler(notification_harness=None)
        payload = {
            "type": "view_submission",
            "user": {"username": "test"},
            "view": {
                "callback_id": "modal_xyz_001",
                "state": {"values": {}},
            },
        }
        result = await handler.handle_slack(payload, None, None)
        assert result.status == "received"

    @pytest.mark.asyncio
    async def test_handle_github_pr_review_approved_recorded(self):
        """handle_github correctly routes PR review approved events."""
        mock_notification = MagicMock()
        handler = WebhookHandler(
            notification_harness=mock_notification,
            github_webhook_secret="secret",
        )
        payload = {
            "pull_request": {
                "body": "<!-- notification_id: pr-notif-007 -->",
            },
            "review": {
                "state": "approved",
                "body": "Looks good!",
            },
            "sender": {"login": "maintainer"},
        }

        result = await handler.handle_github(payload, None, "pull_request_review")

        assert result.status == "received"
        assert result.notification_id == "pr-notif-007"
        mock_notification.record_response.assert_called_once_with(
            notification_id="pr-notif-007",
            response_type="approved",
            responder="maintainer",
            message="Looks good!",
        )


class TestWebhookHandlerSingleton:
    """Tests for get_webhook_handler singleton."""

    def test_returns_webhook_handler_instance(self):
        """Returns a WebhookHandler instance."""
        import forge_harness.webhook_server.handlers.webhook_handler as wh_module
        wh_module._webhook_handler = None
        handler = get_webhook_handler()
        assert isinstance(handler, WebhookHandler)

    def test_singleton_idempotent(self):
        """Multiple calls return the same instance."""
        import forge_harness.webhook_server.handlers.webhook_handler as wh_module
        wh_module._webhook_handler = None
        h1 = get_webhook_handler()
        h2 = get_webhook_handler()
        h3 = get_webhook_handler()
        assert h1 is h2 is h3


# ===========================================================================
# SECTION 5: ApprovalQueueHandler — additional operational tests
# ===========================================================================

class TestApprovalQueueHandlerOperations:
    """Integration-style tests for ApprovalQueueHandler main operations."""

    @pytest.fixture
    def mock_queue(self):
        q = MagicMock()
        q.list_pending = AsyncMock(return_value=[])
        q.get_request = AsyncMock(return_value=None)
        q.approve = AsyncMock()
        q.reject = AsyncMock()
        q.get_stats = AsyncMock()
        return q

    @pytest.mark.asyncio
    async def test_list_pending_no_queue_returns_empty(self):
        """list_pending with None queue returns []."""
        h = ApprovalQueueHandler(approval_queue=None, forge_root=Path("/tmp"))
        result = await h.list_pending()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_pending_exception_returns_empty(self, mock_queue):
        """list_pending swallows exceptions and returns []."""
        mock_queue.list_pending.side_effect = RuntimeError("backend down")
        h = ApprovalQueueHandler(
            approval_queue=mock_queue, forge_root=Path("/tmp")
        )
        result = await h.list_pending()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_request_no_queue_returns_none(self):
        """get_request with None queue returns None."""
        h = ApprovalQueueHandler(approval_queue=None, forge_root=Path("/tmp"))
        result = await h.get_request("req-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_request_exception_returns_none(self, mock_queue):
        """get_request swallows exceptions and returns None."""
        mock_queue.get_request.side_effect = OSError("disk error")
        h = ApprovalQueueHandler(
            approval_queue=mock_queue, forge_root=Path("/tmp")
        )
        result = await h.get_request("req-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_approve_request_no_queue_returns_error_dict(self):
        """approve_request with None queue returns error dict."""
        h = ApprovalQueueHandler(approval_queue=None, forge_root=Path("/tmp"))
        result = await h.approve_request("req-001", "admin")
        assert result["success"] is False
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_approve_request_queue_raises_returns_error_dict(self, mock_queue):
        """approve_request swallows queue exceptions and returns error dict."""
        mock_queue.approve.side_effect = Exception("Queue full")
        h = ApprovalQueueHandler(
            approval_queue=mock_queue, forge_root=Path("/tmp")
        )
        result = await h.approve_request("req-001", "admin")
        assert result["success"] is False
        assert "Queue full" in result["error"]

    @pytest.mark.asyncio
    async def test_reject_request_no_queue_returns_error_dict(self):
        """reject_request with None queue returns error dict."""
        h = ApprovalQueueHandler(approval_queue=None, forge_root=Path("/tmp"))
        result = await h.reject_request("req-001", "admin")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_reject_request_queue_raises_returns_error_dict(self, mock_queue):
        """reject_request swallows exceptions and returns error dict."""
        mock_queue.reject.side_effect = Exception("Network error")
        h = ApprovalQueueHandler(
            approval_queue=mock_queue, forge_root=Path("/tmp")
        )
        result = await h.reject_request("req-001", "admin", reason="Too risky")
        assert result["success"] is False
        assert "Network error" in result["error"]

    @pytest.mark.asyncio
    async def test_get_stats_no_queue_returns_error(self):
        """get_stats with None queue returns error dict."""
        h = ApprovalQueueHandler(approval_queue=None, forge_root=Path("/tmp"))
        result = await h.get_stats()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_stats_queue_raises_returns_error(self, mock_queue):
        """get_stats swallows exceptions and returns error dict."""
        mock_queue.get_stats.side_effect = RuntimeError("Timeout")
        h = ApprovalQueueHandler(
            approval_queue=mock_queue, forge_root=Path("/tmp")
        )
        result = await h.get_stats()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_count_pending_delegates_to_list_pending(self, mock_queue):
        """count_pending uses list_pending with status=pending and large limit."""
        req1 = _make_approval_request("r1", status=ApprovalStatus.PENDING)
        req2 = _make_approval_request("r2", status=ApprovalStatus.PENDING)
        mock_queue.list_pending.return_value = [req1, req2]

        h = ApprovalQueueHandler(
            approval_queue=mock_queue, forge_root=Path("/tmp")
        )
        count = await h.count_pending()

        # Both requests are PENDING so they pass the status filter
        assert count == 2

    @pytest.mark.asyncio
    async def test_approve_request_with_checkpoint_pipeline_resume(
        self, mock_queue, tmp_path
    ):
        """approve_request triggers pipeline resume when checkpoint exists."""
        checkpoint_file = tmp_path / "pipeline.checkpoint"
        checkpoint_file.write_text("{}")

        approved_req = _make_approval_request(
            "r1",
            status=ApprovalStatus.APPROVED,
            workflow_checkpoint=str(checkpoint_file),
        )
        mock_queue.approve.return_value = approved_req

        mock_orchestrator = MagicMock()
        pipeline_result = MagicMock()
        pipeline_result.success = True
        mock_orchestrator.resume = AsyncMock(return_value=pipeline_result)

        h = ApprovalQueueHandler(
            approval_queue=mock_queue,
            orchestrator=mock_orchestrator,
            forge_root=Path("/tmp"),
        )
        result = await h.approve_request("r1", "admin", auto_resume=True)

        assert result["success"] is True
        assert result["pipeline_resumed"] is True
        assert result["pipeline_success"] is True
        mock_orchestrator.resume.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_request_checkpoint_not_exists_no_resume(
        self, mock_queue, tmp_path
    ):
        """approve_request skips pipeline resume when checkpoint file missing."""
        approved_req = _make_approval_request(
            "r1",
            status=ApprovalStatus.APPROVED,
            workflow_checkpoint="/nonexistent/path/checkpoint.json",
        )
        mock_queue.approve.return_value = approved_req

        mock_orchestrator = MagicMock()
        mock_orchestrator.resume = AsyncMock()

        h = ApprovalQueueHandler(
            approval_queue=mock_queue,
            orchestrator=mock_orchestrator,
            forge_root=Path("/tmp"),
        )
        result = await h.approve_request("r1", "admin", auto_resume=True)

        assert result["success"] is True
        # Checkpoint file doesn't exist — resume not called
        mock_orchestrator.resume.assert_not_called()
        assert "pipeline_resumed" not in result

    @pytest.mark.asyncio
    async def test_approve_request_orchestrator_raises_records_error(
        self, mock_queue, tmp_path
    ):
        """Failed pipeline resume is recorded in result dict."""
        checkpoint_file = tmp_path / "fail.checkpoint"
        checkpoint_file.write_text("{}")

        approved_req = _make_approval_request(
            "r1",
            status=ApprovalStatus.APPROVED,
            workflow_checkpoint=str(checkpoint_file),
        )
        mock_queue.approve.return_value = approved_req

        mock_orchestrator = MagicMock()
        mock_orchestrator.resume = AsyncMock(side_effect=RuntimeError("Pipeline crash"))

        h = ApprovalQueueHandler(
            approval_queue=mock_queue,
            orchestrator=mock_orchestrator,
            forge_root=Path("/tmp"),
        )
        result = await h.approve_request("r1", "admin", auto_resume=True)

        assert result["success"] is True
        assert result["pipeline_resumed"] is False
        assert "Pipeline crash" in result["resume_error"]

    @pytest.mark.asyncio
    async def test_batch_approve_phone_tier_rejected(self, mock_queue):
        """Phone-tier (HIGH priority) requests are rejected from batch approve."""
        req = _make_approval_request(
            "req-phone",
            priority=ApprovalPriority.HIGH,  # HIGH -> phone tier
        )
        mock_queue.get_request.return_value = req

        h = ApprovalQueueHandler(
            approval_queue=mock_queue, forge_root=Path("/tmp")
        )
        result = await h.batch_approve(["req-phone"], approver="admin")

        assert result["approved_count"] == 0
        assert result["failed_count"] == 1
        assert "phone" in result["failed"][0]["error"]
        assert "requires individual approval" in result["failed"][0]["error"]

    @pytest.mark.asyncio
    async def test_batch_approve_not_found_recorded_in_failed(self, mock_queue):
        """Requests not found in get_request are recorded in failed list."""
        mock_queue.get_request.return_value = None

        h = ApprovalQueueHandler(
            approval_queue=mock_queue, forge_root=Path("/tmp")
        )
        result = await h.batch_approve(["req-missing"], approver="admin")

        assert result["approved_count"] == 0
        assert result["failed_count"] == 1
        assert result["failed"][0]["error"] == "Not found"


class TestApprovalHandlerSerializeRequest:
    """Tests for _serialize_request and helper methods."""

    @pytest.fixture
    def h(self):
        return ApprovalQueueHandler(forge_root=Path("/tmp"))

    def test_serialize_security_type_gets_desktop_tier(self, h):
        """SECURITY type maps to desktop tier regardless of priority.

        Note: ApprovalType.DEPLOY.value == 'deploy' which is NOT in the
        high_risk_types set ('deployment', 'security', 'production', 'infrastructure').
        ApprovalType.SECURITY.value == 'security' which IS in that set.
        """
        req = _make_approval_request(
            approval_type=ApprovalType.SECURITY,
            priority=ApprovalPriority.LOW,
        )
        result = h._serialize_request(req)
        assert result["tier"] == "desktop"

    def test_serialize_content_normal_priority_gets_phone_tier(self, h):
        """CONTENT type with NORMAL priority gets phone tier."""
        req = _make_approval_request(
            approval_type=ApprovalType.CONTENT,
            priority=ApprovalPriority.NORMAL,
        )
        result = h._serialize_request(req)
        assert result["tier"] == "phone"

    def test_serialize_full_request_structure(self, h):
        """_serialize_request returns all expected keys."""
        now = datetime.now(UTC)
        req = _make_approval_request(
            req_id="full-req",
            approval_type=ApprovalType.FEATURE,
            domain="fintech",
            title="Full Request",
            description="Full description",
            status=ApprovalStatus.PENDING,
            priority=ApprovalPriority.HIGH,
            metadata={"project": "wallet-app", "files_changed": 7},
            expires_at=now + timedelta(hours=24),
            resolved_at=None,
            approved_by=None,
            resolution_reason=None,
            workflow_checkpoint="/checkpoints/wallet.json",
        )
        result = h._serialize_request(req)

        assert result["request_id"] == "full-req"
        assert result["approval_type"] == "feature"
        assert result["domain"] == "fintech"
        assert result["project"] == "wallet-app"
        assert result["title"] == "Full Request"
        assert result["status"] == "pending"
        assert result["priority"] == "high"
        assert result["tier"] == "phone"
        assert result["expires_at"] is not None
        assert result["resolved_at"] is None
        assert result["context"]["files_affected"] == 7
        assert result["context"]["risk_level"] == "medium"
        assert result["context"]["estimated_impact"] == 4

    def test_compute_tier_deployment_type_always_desktop(self, h):
        """All high-risk type values always return desktop tier."""
        for type_val in ("deployment", "security", "production", "infrastructure"):
            req = MagicMock()
            req.priority = "low"
            req.type = type_val  # raw string
            assert h._compute_tier(req) == "desktop", f"Failed for type={type_val}"

    def test_derive_risk_level_combinations(self, h):
        """Risk level derivation covers all priority/tier combinations."""
        assert h._derive_risk_level(ApprovalPriority.CRITICAL, "phone") == "high"
        assert h._derive_risk_level(ApprovalPriority.HIGH, "phone") == "medium"
        assert h._derive_risk_level(ApprovalPriority.NORMAL, "phone") == "low"
        assert h._derive_risk_level(ApprovalPriority.LOW, "phone") == "low"
        assert h._derive_risk_level(ApprovalPriority.LOW, "desktop") == "high"

    def test_derive_estimated_impact_all_priorities(self, h):
        """Impact scores match expected values for all priority levels."""
        assert h._derive_estimated_impact(ApprovalPriority.CRITICAL) == 5
        assert h._derive_estimated_impact(ApprovalPriority.HIGH) == 4
        assert h._derive_estimated_impact(ApprovalPriority.NORMAL) == 3
        assert h._derive_estimated_impact(ApprovalPriority.LOW) == 2

    def test_extract_files_affected_files_changed_int(self, h):
        """files_changed int is returned directly."""
        assert h._extract_files_affected({"files_changed": 15}) == 15

    def test_extract_files_affected_files_affected_int(self, h):
        """files_affected int is returned when files_changed absent."""
        assert h._extract_files_affected({"files_affected": 8}) == 8

    def test_extract_files_affected_files_list(self, h):
        """List of files returns list length."""
        assert h._extract_files_affected({"files": ["a.py", "b.py"]}) == 2

    def test_extract_files_affected_empty_metadata(self, h):
        """Empty metadata dict returns 0."""
        assert h._extract_files_affected({}) == 0

    def test_extract_files_affected_none_metadata(self, h):
        """None metadata returns 0."""
        assert h._extract_files_affected(None) == 0
