"""Task lease schema and API forwarding tests (CP-2001)."""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from forge_harness.shared_contracts import LeaseContract, TaskContract
from forge_harness.webhook_server.api import tasks as tasks_api
from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError


def _lease_payload() -> dict[str, str]:
    return {
        "owner_node": "nova",
        "owner_agent": "forge:codex",
        "lease_expires_at": "2026-02-21T12:00:00Z",
        "path_lock": "forge-terminal#FT-123",
    }


def test_create_task_request_parses_lease_schema():
    """CreateTaskRequest should parse and JSON-dump lease metadata."""
    body = tasks_api.CreateTaskRequest(
        subject="Lease task",
        description="Task with lease",
        priority="high",
        lease=_lease_payload(),
    )

    assert body.lease is not None
    assert body.lease.owner_node == "nova"
    dumped = body.lease.model_dump(mode="json")
    assert dumped["owner_agent"] == "forge:codex"
    assert dumped["path_lock"] == "forge-terminal#FT-123"
    datetime.fromisoformat(dumped["lease_expires_at"].replace("Z", "+00:00"))


@pytest.mark.asyncio
async def test_create_task_endpoint_forwards_lease_to_handler(monkeypatch):
    """API create endpoint should pass lease payload to task handler."""
    lease = _lease_payload()
    created_task = {
        "id": "task-1",
        "subject": "Lease task",
        "description": "Task with lease",
        "priority": "high",
        "status": "pending",
        "required_role": None,
        "claimed_by": None,
        "lease": lease,
        "order": 0,
        "created_at": "2026-02-20T00:00:00Z",
        "updated_at": "2026-02-20T00:00:00Z",
    }

    mock_handler = Mock()
    mock_handler.create_task = AsyncMock(return_value=created_task)
    mock_event_bus = Mock()
    mock_event_bus.publish = AsyncMock()
    mock_audit_logger = Mock()
    mock_audit_logger.log_task_creation = AsyncMock()

    monkeypatch.setattr(tasks_api, "get_task_handler", AsyncMock(return_value=mock_handler))
    monkeypatch.setattr(tasks_api, "get_event_bus", AsyncMock(return_value=mock_event_bus))
    monkeypatch.setattr(tasks_api, "get_audit_logger", lambda: mock_audit_logger)

    body = tasks_api.CreateTaskRequest(
        subject="Lease task",
        description="Task with lease",
        priority="high",
        lease=lease,
    )

    response = await tasks_api.create_task(body=body, request=None, _=None)
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["data"]["lease"]["owner_node"] == "nova"

    mock_handler.create_task.assert_awaited_once()
    create_kwargs = mock_handler.create_task.await_args.kwargs
    assert create_kwargs["lease"]["owner_agent"] == "forge:codex"
    assert create_kwargs["lease"]["path_lock"] == "forge-terminal#FT-123"


@pytest.mark.asyncio
async def test_update_task_endpoint_forwards_lease_to_handler(monkeypatch):
    """API update endpoint should include lease payload in updates."""
    lease = _lease_payload()
    updated_task = {
        "id": "task-2",
        "subject": "Task",
        "description": "Desc",
        "priority": "medium",
        "status": "pending",
        "required_role": None,
        "claimed_by": None,
        "lease": lease,
        "order": 0,
        "created_at": "2026-02-20T00:00:00Z",
        "updated_at": "2026-02-20T01:00:00Z",
    }

    mock_handler = Mock()
    mock_handler.update_task = AsyncMock(return_value=updated_task)
    mock_event_bus = Mock()
    mock_event_bus.publish = AsyncMock()

    monkeypatch.setattr(tasks_api, "get_task_handler", AsyncMock(return_value=mock_handler))
    monkeypatch.setattr(tasks_api, "get_event_bus", AsyncMock(return_value=mock_event_bus))

    body = tasks_api.UpdateTaskRequest(lease=lease)
    response = await tasks_api.update_task(task_id="task-2", body=body, _=None)
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["data"]["lease"]["owner_node"] == "nova"

    mock_handler.update_task.assert_awaited_once()
    update_args = mock_handler.update_task.await_args.args
    assert update_args[0] == "task-2"
    assert update_args[1]["lease"]["owner_agent"] == "forge:codex"


@pytest.mark.asyncio
async def test_claim_task_lease_endpoint_forwards_to_handler(monkeypatch):
    """Dedicated lease claim endpoint should call claim_task_with_lease."""
    lease = _lease_payload()
    claimed_task = {
        "id": "task-3",
        "subject": "Task",
        "description": "Desc",
        "priority": "medium",
        "status": "assigned",
        "claimed_by": "forge:codex",
        "lease": lease,
        "order": 0,
        "created_at": "2026-02-20T00:00:00Z",
        "updated_at": "2026-02-20T01:00:00Z",
    }

    mock_handler = Mock()
    mock_handler.claim_task_with_lease = AsyncMock(return_value=claimed_task)
    mock_event_bus = Mock()
    mock_event_bus.publish = AsyncMock()
    mock_audit_logger = Mock()
    mock_audit_logger.log_task_assignment = AsyncMock()

    monkeypatch.setattr(tasks_api, "get_task_handler", AsyncMock(return_value=mock_handler))
    monkeypatch.setattr(tasks_api, "get_event_bus", AsyncMock(return_value=mock_event_bus))
    monkeypatch.setattr(tasks_api, "get_audit_logger", lambda: mock_audit_logger)

    body = tasks_api.LeaseClaimRequest(lease=lease)
    response = await tasks_api.claim_task_lease(task_id="task-3", body=body, request=None, _=None)
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["success"] is True
    mock_handler.claim_task_with_lease.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_task_lease_endpoint_maps_conflict(monkeypatch):
    """Lease conflict from handler should be returned as structured 409."""
    mock_handler = Mock()
    mock_handler.claim_task_with_lease = AsyncMock(
        side_effect=TaskLeaseError(
            "PATH_LOCK_CONFLICT",
            "path lock already held",
            status_code=409,
        )
    )

    monkeypatch.setattr(tasks_api, "get_task_handler", AsyncMock(return_value=mock_handler))

    body = tasks_api.LeaseClaimRequest(lease=_lease_payload())
    response = await tasks_api.claim_task_lease(task_id="task-4", body=body, request=None, _=None)
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 409
    assert payload["success"] is False
    assert payload["error"]["code"] == "PATH_LOCK_CONFLICT"


@pytest.mark.asyncio
async def test_renew_release_requeue_endpoints_forward_to_handler(monkeypatch):
    """Renew/release/requeue endpoints should delegate to handler methods."""
    lease = _lease_payload()
    task_payload = {
        "id": "task-5",
        "subject": "Task",
        "description": "Desc",
        "priority": "medium",
        "status": "pending",
        "claimed_by": None,
        "lease": None,
        "order": 0,
        "created_at": "2026-02-20T00:00:00Z",
        "updated_at": "2026-02-20T01:00:00Z",
    }

    mock_handler = Mock()
    mock_handler.renew_task_lease = AsyncMock(return_value={**task_payload, "lease": lease})
    mock_handler.release_task_lease = AsyncMock(return_value=task_payload)
    mock_handler.requeue_task = AsyncMock(return_value=task_payload)
    mock_event_bus = Mock()
    mock_event_bus.publish = AsyncMock()

    monkeypatch.setattr(tasks_api, "get_task_handler", AsyncMock(return_value=mock_handler))
    monkeypatch.setattr(tasks_api, "get_event_bus", AsyncMock(return_value=mock_event_bus))

    renew_response = await tasks_api.renew_task_lease(
        task_id="task-5",
        body=tasks_api.LeaseRenewRequest(lease=lease),
        _=None,
    )
    assert renew_response.status_code == 200
    mock_handler.renew_task_lease.assert_awaited_once()

    release_response = await tasks_api.release_task_lease(
        task_id="task-5",
        body=tasks_api.LeaseReleaseRequest(owner_node="nova", owner_agent="forge:codex"),
        _=None,
    )
    assert release_response.status_code == 200
    mock_handler.release_task_lease.assert_awaited_once()

    requeue_response = await tasks_api.requeue_task(
        task_id="task-5",
        body=tasks_api.LeaseRequeueRequest(reason="stale"),
        _=None,
    )
    assert requeue_response.status_code == 200
    mock_handler.requeue_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_task_rolls_back_on_delivery_failure(monkeypatch):
    """Dispatch endpoint should rollback the claim when tmux delivery fails."""
    claimed_task = {
        "id": "task-6",
        "subject": "Ship rollback logic",
        "description": "Ensure dispatch is transactional",
        "priority": "high",
        "status": "assigned",
        "claimed_by": "agent-1",
        "lease": None,
        "order": 0,
        "created_at": "2026-02-20T00:00:00Z",
        "updated_at": "2026-02-20T01:00:00Z",
    }
    requeued_task = {**claimed_task, "status": "pending", "claimed_by": ""}

    mock_handler = Mock()
    mock_handler.claim_task = AsyncMock(return_value=claimed_task)
    mock_handler.requeue_task = AsyncMock(return_value=requeued_task)
    mock_agent = Mock(
        id="agent-1", role="backend", name="Backend", status="idle", tmux_session="forge:backend"
    )
    mock_registry = Mock()
    mock_registry.get = Mock(return_value=mock_agent)
    mock_event_bus = Mock()
    mock_event_bus.publish = AsyncMock()
    mock_audit_logger = Mock()
    mock_audit_logger.log_task_assignment = AsyncMock()

    monkeypatch.setattr(tasks_api, "get_task_handler", AsyncMock(return_value=mock_handler))
    monkeypatch.setattr(tasks_api, "get_agent_registry", AsyncMock(return_value=mock_registry))
    monkeypatch.setattr(tasks_api, "get_event_bus", AsyncMock(return_value=mock_event_bus))
    monkeypatch.setattr(tasks_api, "get_audit_logger", lambda: mock_audit_logger)

    request = Mock()
    request.headers = {}
    request.client = Mock(host="testclient")

    with patch("forge_harness.fleet.dispatch_client.DispatchClient") as mock_dispatch_client:
        mock_client = mock_dispatch_client.return_value
        mock_client.send = AsyncMock(return_value=Mock(success=False, error="agent not ready"))

        response = await tasks_api.dispatch_task(
            task_id="task-6",
            body=tasks_api.DispatchRequest(agent_id="agent-1"),
            request=request,
            _=None,
        )

    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 503
    assert payload["success"] is False
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    mock_handler.requeue_task.assert_awaited_once_with("task-6")
    mock_event_bus.publish.assert_awaited()
    assert mock_event_bus.publish.await_args_list[0].args[0] == "task.dispatch_failed"


@pytest.mark.asyncio
async def test_dispatch_task_rolls_back_when_agent_has_no_tmux_session(monkeypatch):
    """Dispatch endpoint should rollback when target agent has no tmux session."""
    claimed_task = {
        "id": "task-7",
        "subject": "No tmux target",
        "description": "Rollback on routing failures",
        "priority": "medium",
        "status": "assigned",
        "claimed_by": "agent-2",
        "lease": None,
        "order": 0,
        "created_at": "2026-02-20T00:00:00Z",
        "updated_at": "2026-02-20T01:00:00Z",
    }
    requeued_task = {**claimed_task, "status": "pending", "claimed_by": ""}

    mock_handler = Mock()
    mock_handler.claim_task = AsyncMock(return_value=claimed_task)
    mock_handler.requeue_task = AsyncMock(return_value=requeued_task)
    mock_agent = Mock(
        id="agent-2", role="backend", name="Backend", status="idle", tmux_session=None
    )
    mock_registry = Mock()
    mock_registry.get = Mock(return_value=mock_agent)
    mock_event_bus = Mock()
    mock_event_bus.publish = AsyncMock()
    mock_audit_logger = Mock()
    mock_audit_logger.log_task_assignment = AsyncMock()

    monkeypatch.setattr(tasks_api, "get_task_handler", AsyncMock(return_value=mock_handler))
    monkeypatch.setattr(tasks_api, "get_agent_registry", AsyncMock(return_value=mock_registry))
    monkeypatch.setattr(tasks_api, "get_event_bus", AsyncMock(return_value=mock_event_bus))
    monkeypatch.setattr(tasks_api, "get_audit_logger", lambda: mock_audit_logger)

    request = Mock()
    request.headers = {}
    request.client = Mock(host="testclient")

    response = await tasks_api.dispatch_task(
        task_id="task-7",
        body=tasks_api.DispatchRequest(agent_id="agent-2"),
        request=request,
        _=None,
    )

    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 503
    assert payload["success"] is False
    mock_handler.requeue_task.assert_awaited_once_with("task-7")


@pytest.mark.asyncio
async def test_dispatch_task_success_does_not_rollback(monkeypatch):
    """Successful dispatch should keep assignment and return 200."""
    claimed_task = {
        "id": "task-8",
        "subject": "Happy path",
        "description": "No rollback on success",
        "priority": "low",
        "status": "assigned",
        "claimed_by": "agent-3",
        "lease": None,
        "order": 0,
        "created_at": "2026-02-20T00:00:00Z",
        "updated_at": "2026-02-20T01:00:00Z",
    }

    mock_handler = Mock()
    mock_handler.claim_task = AsyncMock(return_value=claimed_task)
    mock_handler.requeue_task = AsyncMock()
    mock_agent = Mock(
        id="agent-3", role="backend", name="Backend", status="idle", tmux_session="forge:backend"
    )
    mock_registry = Mock()
    mock_registry.get = Mock(return_value=mock_agent)
    mock_event_bus = Mock()
    mock_event_bus.publish = AsyncMock()
    mock_audit_logger = Mock()
    mock_audit_logger.log_task_assignment = AsyncMock()

    monkeypatch.setattr(tasks_api, "get_task_handler", AsyncMock(return_value=mock_handler))
    monkeypatch.setattr(tasks_api, "get_agent_registry", AsyncMock(return_value=mock_registry))
    monkeypatch.setattr(tasks_api, "get_event_bus", AsyncMock(return_value=mock_event_bus))
    monkeypatch.setattr(tasks_api, "get_audit_logger", lambda: mock_audit_logger)

    request = Mock()
    request.headers = {}
    request.client = Mock(host="testclient")

    with patch("forge_harness.fleet.dispatch_client.DispatchClient") as mock_dispatch_client:
        mock_client = mock_dispatch_client.return_value
        mock_client.send = AsyncMock(return_value=Mock(success=True, error=None))

        response = await tasks_api.dispatch_task(
            task_id="task-8",
            body=tasks_api.DispatchRequest(agent_id="agent-3"),
            request=request,
            _=None,
        )

    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 200
    assert payload["success"] is True
    mock_handler.requeue_task.assert_not_awaited()


class TestLeaseContractHelpers:
    """Tests for LeaseContract helper methods (CP-2001)."""

    def test_is_lease_active_returns_false_when_expired(self):
        """Lease should be inactive when expiry time has passed."""
        lease = LeaseContract(
            owner_node="nova",
            owner_agent="forge:codex",
            lease_expires_at="2020-01-01T00:00:00+00:00",
            path_lock="path/to/file",
        )
        assert lease.is_lease_active() is False

    def test_is_lease_active_returns_true_when_valid(self):
        """Lease should be active when expiry time is in the future."""
        future_expiry = (datetime.now() + timedelta(hours=1)).isoformat()
        lease = LeaseContract(
            owner_node="nova",
            owner_agent="forge:codex",
            lease_expires_at=future_expiry,
            path_lock="path/to/file",
        )
        assert lease.is_lease_active() is True

    def test_is_lease_active_returns_false_when_empty(self):
        """Lease should be inactive when expiry is empty."""
        lease = LeaseContract()
        assert lease.is_lease_active() is False

    def test_claim_sets_lease_fields(self):
        """Claim should set node, agent, and expiry."""
        lease = LeaseContract()
        lease.claim("nova", "forge:codex", ttl_seconds=300)

        assert lease.owner_node == "nova"
        assert lease.owner_agent == "forge:codex"
        assert lease.lease_expires_at != ""

        expiry = datetime.fromisoformat(lease.lease_expires_at.replace("Z", "+00:00"))
        expected = datetime.now(expiry.tzinfo) + timedelta(seconds=300)
        assert abs((expiry - expected).total_seconds()) < 1

    def test_release_clears_lease_fields(self):
        """Release should clear all lease fields."""
        lease = LeaseContract(
            owner_node="nova",
            owner_agent="forge:codex",
            lease_expires_at="2030-01-01T00:00:00+00:00",
            path_lock="path/to/file",
        )
        lease.release()

        assert lease.owner_node == ""
        assert lease.owner_agent == ""
        assert lease.lease_expires_at == ""
        assert lease.path_lock == ""


class TestTaskContractLeaseHelpers:
    """Tests for TaskContract lease helper methods (CP-2001)."""

    def test_is_lease_active_returns_false_when_no_lease(self):
        """Task with no lease should return False."""
        task = TaskContract(id="task-1", subject="Test task")
        assert task.is_lease_active() is False

    def test_is_lease_active_returns_lease_status(self):
        """Task with lease should delegate to lease.is_lease_active()."""
        future_expiry = (datetime.now() + timedelta(hours=1)).isoformat()
        task = TaskContract(
            id="task-2",
            subject="Test task",
            lease=LeaseContract(
                owner_node="nova",
                owner_agent="forge:codex",
                lease_expires_at=future_expiry,
                path_lock="path",
            ),
        )
        assert task.is_lease_active() is True

    def test_claim_creates_lease_if_none(self):
        """Claim should create lease if none exists."""
        task = TaskContract(id="task-3", subject="Test task")
        task.claim("nova", "forge:codex", ttl_seconds=300, path_lock="test/path")

        assert task.lease is not None
        assert task.lease.owner_node == "nova"
        assert task.lease.owner_agent == "forge:codex"
        assert task.lease.path_lock == "test/path"

    def test_claim_updates_existing_lease(self):
        """Claim should update existing lease."""
        task = TaskContract(
            id="task-4",
            subject="Test task",
            lease=LeaseContract(owner_node="old", owner_agent="old"),
        )
        task.claim("nova", "forge:codex", ttl_seconds=600)

        assert task.lease.owner_node == "nova"
        assert task.lease.owner_agent == "forge:codex"

    def test_release_clears_lease(self):
        """Release should clear lease fields."""
        future_expiry = (datetime.now() + timedelta(hours=1)).isoformat()
        task = TaskContract(
            id="task-5",
            subject="Test task",
            lease=LeaseContract(
                owner_node="nova",
                owner_agent="forge:codex",
                lease_expires_at=future_expiry,
                path_lock="path",
            ),
        )
        task.release()

        assert task.lease is not None
        assert task.lease.owner_node == ""
        assert task.lease.owner_agent == ""
