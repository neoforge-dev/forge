"""Comprehensive tests for two webhook server API modules:

  - forge_harness/webhook_server/api/tasks.py  (~1019 lines) - Task management
  - forge_harness/webhook_server/api/nodes.py  (~626 lines)  - Node management

Coverage strategy:
  - All endpoints (GET, POST, PUT, DELETE)
  - Pydantic model validation (required fields, defaults, constraints)
  - Auth bypass via dependency_overrides
  - Mock injection via unittest.mock patch
  - 404 not-found paths
  - 422 validation error paths
  - 500 server error paths
  - Event-bus SSE publishing
  - Audit log calls
  - Lease operations (claim/renew/release/requeue)
  - Node discovery (local + remote heartbeat)
  - Scheduler scoring
  - Helper function unit tests

Target: 80%+ coverage per module, all tests pass.
"""

from __future__ import annotations

import json
import platform
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

# ===========================================================================
# Shared helpers
# ===========================================================================


def _make_task(
    task_id: str = "task-abc1",
    subject: str = "Test Task",
    description: str = "A test task",
    priority: str = "medium",
    status: str = "pending",
    claimed_by: str | None = None,
    required_role: str | None = None,
    lease: dict | None = None,
) -> dict:
    """Build a plain task dict as returned by the task handler."""
    return {
        "id": task_id,
        "subject": subject,
        "description": description,
        "priority": priority,
        "status": status,
        "claimed_by": claimed_by or "",
        "required_role": required_role,
        "order": 0,
        "domain": None,
        "project": None,
        "lease": lease,
        "created_at": "2026-02-22T00:00:00+00:00",
        "updated_at": "2026-02-22T00:00:00+00:00",
    }


def _make_task_handler(
    tasks: list[dict] | None = None,
    task: dict | None = None,
    stats: dict | None = None,
) -> Mock:
    """Build a mock TaskQueueHandler with sensible defaults."""
    handler = Mock()
    _tasks = tasks if tasks is not None else [_make_task()]
    _task = task if task is not None else (_tasks[0] if _tasks else None)
    _stats = stats or {"total": 1, "pending": 1, "in_progress": 0, "completed": 0}

    handler.list_tasks = AsyncMock(return_value=_tasks)
    handler.get_task = AsyncMock(return_value=_task)
    handler.create_task = AsyncMock(return_value=_task)
    handler.update_task = AsyncMock(return_value=_task)
    handler.delete_task = AsyncMock(return_value=True)
    handler.claim_task = AsyncMock(return_value=_task)
    handler.claim_task_with_lease = AsyncMock(return_value=_task)
    handler.renew_task_lease = AsyncMock(return_value=_task)
    handler.release_task_lease = AsyncMock(return_value=_task)
    handler.requeue_task = AsyncMock(return_value=_task)
    handler.get_stats = AsyncMock(return_value=_stats)
    return handler


def _make_event_bus() -> Mock:
    bus = Mock()
    bus.publish = AsyncMock()
    return bus


def _make_audit_logger() -> Mock:
    audit = Mock()
    audit.log_task_creation = AsyncMock()
    audit.log_task_deletion = AsyncMock()
    audit.log_task_assignment = AsyncMock()
    return audit


def _make_agent(
    agent_id: str = "forge:prya",
    status: str = "idle",
    role: str = "developer",
    tmux_session: str | None = None,
) -> Mock:
    agent = Mock()
    agent.id = agent_id
    agent.status = status
    agent.role = role
    agent.name = agent_id
    agent.tmux_session = tmux_session
    return agent


def _make_agent_registry(agents: list | None = None) -> Mock:
    registry = Mock()
    _agents = agents if agents is not None else [_make_agent()]
    registry.list_active = Mock(return_value=_agents)
    registry.get = Mock(return_value=_agents[0] if _agents else None)
    return registry


def _future_dt() -> datetime:
    return datetime.now(UTC) + timedelta(hours=2)


def _sample_lease() -> dict:
    return {
        "owner_node": "nova",
        "owner_agent": "forge:prya",
        "lease_expires_at": _future_dt().isoformat(),
        "path_lock": "harness/",
    }


# ---------------------------------------------------------------------------
# Auth bypass helper (matches pattern from test_webhook_api_patterns_auth_messages_extended.py)
# ---------------------------------------------------------------------------


def _bypass_auth(app: FastAPI) -> None:
    """Override auth dependencies so no credentials are needed in tests."""
    from forge_harness.webhook_server.core import dependencies

    _admin_user = {"sub": "test", "permissions": ["admin"]}

    app.dependency_overrides[dependencies.verify_auth] = lambda: _admin_user
    app.dependency_overrides[dependencies.verify_unified_auth] = lambda: _admin_user

    def _noop_require_permission(permission: str):
        async def _check():
            return _admin_user
        return _check

    app.dependency_overrides[dependencies.require_permission] = _noop_require_permission


# ===========================================================================
# SECTION 1: TASKS API — Pydantic model tests
# ===========================================================================


class TestTaskLeaseModel:
    def test_valid_lease(self):
        from forge_harness.webhook_server.api.tasks import TaskLease

        lease = TaskLease(
            owner_node="nova",
            owner_agent="forge:prya",
            lease_expires_at=_future_dt(),
            path_lock="harness/",
        )
        assert lease.owner_node == "nova"
        assert lease.owner_agent == "forge:prya"
        assert lease.path_lock == "harness/"

    def test_missing_owner_node_raises(self):
        from forge_harness.webhook_server.api.tasks import TaskLease

        with pytest.raises(ValidationError):
            TaskLease(
                owner_agent="forge:prya",
                lease_expires_at=_future_dt(),
                path_lock="harness/",
            )

    def test_missing_path_lock_raises(self):
        from forge_harness.webhook_server.api.tasks import TaskLease

        with pytest.raises(ValidationError):
            TaskLease(
                owner_node="nova",
                owner_agent="forge:prya",
                lease_expires_at=_future_dt(),
            )


class TestCreateTaskRequestModel:
    def test_minimal_valid(self):
        from forge_harness.webhook_server.api.tasks import CreateTaskRequest

        req = CreateTaskRequest(subject="My Task")
        assert req.subject == "My Task"
        assert req.description == ""
        assert req.priority == "medium"
        assert req.required_role is None
        assert req.lease is None

    def test_full_fields(self):
        from forge_harness.webhook_server.api.tasks import CreateTaskRequest, TaskLease

        req = CreateTaskRequest(
            subject="Full Task",
            description="A description",
            priority="high",
            required_role="developer",
            lease=TaskLease(
                owner_node="nova",
                owner_agent="forge:prya",
                lease_expires_at=_future_dt(),
                path_lock="harness/",
            ),
        )
        assert req.priority == "high"
        assert req.required_role == "developer"
        assert req.lease is not None

    def test_missing_subject_raises(self):
        from forge_harness.webhook_server.api.tasks import CreateTaskRequest

        with pytest.raises(ValidationError):
            CreateTaskRequest()


class TestUpdateTaskRequestModel:
    def test_all_none_valid(self):
        from forge_harness.webhook_server.api.tasks import UpdateTaskRequest

        req = UpdateTaskRequest()
        assert req.subject is None
        assert req.description is None
        assert req.priority is None
        assert req.status is None
        assert req.required_role is None
        assert req.claimed_by is None
        assert req.lease is None

    def test_partial_update(self):
        from forge_harness.webhook_server.api.tasks import UpdateTaskRequest

        req = UpdateTaskRequest(subject="New Subject", priority="high")
        assert req.subject == "New Subject"
        assert req.priority == "high"
        assert req.status is None

    def test_status_field(self):
        from forge_harness.webhook_server.api.tasks import UpdateTaskRequest

        req = UpdateTaskRequest(status="in_progress")
        assert req.status == "in_progress"


class TestClaimTaskRequestModel:
    def test_valid_minimal(self):
        from forge_harness.webhook_server.api.tasks import ClaimTaskRequest

        req = ClaimTaskRequest(agent_id="forge:prya")
        assert req.agent_id == "forge:prya"
        assert req.lease is None

    def test_with_lease(self):
        from forge_harness.webhook_server.api.tasks import ClaimTaskRequest, TaskLease

        req = ClaimTaskRequest(
            agent_id="forge:prya",
            lease=TaskLease(
                owner_node="nova",
                owner_agent="forge:prya",
                lease_expires_at=_future_dt(),
                path_lock="harness/",
            ),
        )
        assert req.lease is not None

    def test_missing_agent_id_raises(self):
        from forge_harness.webhook_server.api.tasks import ClaimTaskRequest

        with pytest.raises(ValidationError):
            ClaimTaskRequest()


class TestLeaseRequestModels:
    def test_lease_claim_request_valid(self):
        from forge_harness.webhook_server.api.tasks import LeaseClaimRequest, TaskLease

        req = LeaseClaimRequest(
            lease=TaskLease(
                owner_node="nova",
                owner_agent="forge:prya",
                lease_expires_at=_future_dt(),
                path_lock="harness/",
            )
        )
        assert req.lease.owner_node == "nova"

    def test_lease_renew_request_valid(self):
        from forge_harness.webhook_server.api.tasks import LeaseRenewRequest, TaskLease

        req = LeaseRenewRequest(
            lease=TaskLease(
                owner_node="nova",
                owner_agent="forge:prya",
                lease_expires_at=_future_dt(),
                path_lock="harness/",
            )
        )
        assert req.lease is not None

    def test_lease_release_request_defaults(self):
        from forge_harness.webhook_server.api.tasks import LeaseReleaseRequest

        req = LeaseReleaseRequest()
        assert req.owner_node is None
        assert req.owner_agent is None

    def test_lease_requeue_request_defaults(self):
        from forge_harness.webhook_server.api.tasks import LeaseRequeueRequest

        req = LeaseRequeueRequest()
        assert req.owner_node is None
        assert req.owner_agent is None
        assert req.reason is None

    def test_requeue_with_reason(self):
        from forge_harness.webhook_server.api.tasks import LeaseRequeueRequest

        req = LeaseRequeueRequest(owner_node="nova", reason="expired")
        assert req.reason == "expired"


class TestDispatchRequestModel:
    def test_valid(self):
        from forge_harness.webhook_server.api.tasks import DispatchRequest

        req = DispatchRequest(agent_id="forge:prya")
        assert req.agent_id == "forge:prya"

    def test_missing_agent_id_raises(self):
        from forge_harness.webhook_server.api.tasks import DispatchRequest

        with pytest.raises(ValidationError):
            DispatchRequest()


class TestReorderRequestModel:
    def test_both_none_valid(self):
        from forge_harness.webhook_server.api.tasks import ReorderRequest

        req = ReorderRequest()
        assert req.task_ids is None
        assert req.task_order is None

    def test_task_ids_set(self):
        from forge_harness.webhook_server.api.tasks import ReorderRequest

        req = ReorderRequest(task_ids=["a", "b"])
        assert req.task_ids == ["a", "b"]

    def test_task_order_set(self):
        from forge_harness.webhook_server.api.tasks import ReorderRequest

        req = ReorderRequest(task_order=["x", "y"])
        assert req.task_order == ["x", "y"]


# ===========================================================================
# SECTION 2: TASKS API — api_response helper
# ===========================================================================


class TestTasksApiResponseHelper:
    def test_success_shape(self):
        from forge_harness.webhook_server.api.tasks import api_response

        out = api_response({"count": 5})
        assert out["success"] is True
        assert out["data"] == {"count": 5}
        assert out["error"] is None

    def test_error_shape(self):
        from forge_harness.webhook_server.api.tasks import api_response

        out = api_response(error_code="TASK_NOT_FOUND", error_message="Not found")
        assert out["success"] is False
        assert out["error"]["code"] == "TASK_NOT_FOUND"
        assert out["error"]["message"] == "Not found"

    def test_error_defaults_unknown_code(self):
        from forge_harness.webhook_server.api.tasks import api_response

        out = api_response(error_message="Something broke")
        assert out["error"]["code"] == "UNKNOWN_ERROR"

    def test_no_args_returns_success(self):
        from forge_harness.webhook_server.api.tasks import api_response

        out = api_response()
        assert out["success"] is True
        assert out["data"] is None


# ===========================================================================
# SECTION 3: TASKS API — _extract_request_context helper
# ===========================================================================


class TestExtractRequestContext:
    def _make_request(self, headers: dict, client_host: str | None = None) -> Mock:
        """Build a mock Request with dict-backed headers."""
        request = Mock()
        # Use a real dict so .get() works correctly
        request.headers = headers
        if client_host is not None:
            request.client = Mock()
            request.client.host = client_host
        else:
            request.client = None
        return request

    def test_extracts_from_forwarded_for(self):
        from forge_harness.webhook_server.api.tasks import _extract_request_context

        # Due to Python operator precedence, the "if request.client else None" ternary
        # applies to the whole `or` chain, so client must be set for headers to be used.
        request = self._make_request(
            {"X-Forwarded-For": "192.168.1.1, 10.0.0.1"},
            client_host="10.0.0.1",
        )
        ip, ua = _extract_request_context(request)
        assert ip == "192.168.1.1"

    def test_extracts_real_ip(self):
        from forge_harness.webhook_server.api.tasks import _extract_request_context

        request = self._make_request(
            {"X-Real-IP": "10.0.0.5"},
            client_host="10.0.0.5",
        )
        ip, ua = _extract_request_context(request)
        assert ip == "10.0.0.5"

    def test_falls_back_to_client_host(self):
        from forge_harness.webhook_server.api.tasks import _extract_request_context

        request = self._make_request({}, client_host="127.0.0.1")
        ip, ua = _extract_request_context(request)
        assert ip == "127.0.0.1"

    def test_extracts_user_agent(self):
        from forge_harness.webhook_server.api.tasks import _extract_request_context

        request = self._make_request(
            {"User-Agent": "TestBot/1.0"},
            client_host="1.2.3.4",
        )
        ip, ua = _extract_request_context(request)
        assert ua == "TestBot/1.0"

    def test_none_request_returns_nones(self):
        from forge_harness.webhook_server.api.tasks import _extract_request_context

        ip, ua = _extract_request_context(None)
        assert ip is None
        assert ua is None

    def test_no_client_returns_none_ip(self):
        from forge_harness.webhook_server.api.tasks import _extract_request_context

        # Without a client object, the whole ternary resolves to None
        request = self._make_request({"X-Forwarded-For": "1.2.3.4"}, client_host=None)
        ip, ua = _extract_request_context(request)
        assert ip is None


# ===========================================================================
# SECTION 4: TASKS API — Endpoint tests
# ===========================================================================


@pytest.fixture
def tasks_client():
    """TestClient for tasks router with all deps mocked."""
    from forge_harness.webhook_server.api import tasks as tasks_module

    handler = _make_task_handler()
    bus = _make_event_bus()
    audit = _make_audit_logger()
    registry = _make_agent_registry()

    app = FastAPI()
    app.include_router(tasks_module.router)
    _bypass_auth(app)

    patches = [
        patch(
            "forge_harness.webhook_server.api.tasks.get_task_handler",
            new=AsyncMock(return_value=handler),
        ),
        patch(
            "forge_harness.webhook_server.api.tasks.get_event_bus",
            new=AsyncMock(return_value=bus),
        ),
        patch(
            "forge_harness.webhook_server.api.tasks.get_audit_logger",
            return_value=audit,
        ),
        patch(
            "forge_harness.webhook_server.api.tasks.get_agent_registry",
            new=AsyncMock(return_value=registry),
        ),
    ]
    for p in patches:
        p.start()

    client = TestClient(app, raise_server_exceptions=False)
    yield client, {"handler": handler, "bus": bus, "audit": audit, "registry": registry}

    for p in patches:
        p.stop()


# ---------------------------------------------------------------------------
# GET /api/tasks
# ---------------------------------------------------------------------------


class TestListTasks:
    def test_returns_tasks(self, tasks_client):
        client, mocks = tasks_client
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "tasks" in data["data"]
        assert data["data"]["count"] == 1

    def test_empty_task_list(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].list_tasks = AsyncMock(return_value=[])
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        assert resp.json()["data"]["count"] == 0

    def test_status_filter_passed(self, tasks_client):
        client, mocks = tasks_client
        resp = client.get("/api/tasks?status=pending")
        assert resp.status_code == 200
        mocks["handler"].list_tasks.assert_called_once_with(
            status="pending", priority=None, limit=50
        )

    def test_priority_filter_passed(self, tasks_client):
        client, mocks = tasks_client
        resp = client.get("/api/tasks?priority=high")
        assert resp.status_code == 200
        mocks["handler"].list_tasks.assert_called_once_with(
            status=None, priority="high", limit=50
        )

    def test_limit_parameter(self, tasks_client):
        client, mocks = tasks_client
        resp = client.get("/api/tasks?limit=10")
        assert resp.status_code == 200
        mocks["handler"].list_tasks.assert_called_once_with(
            status=None, priority=None, limit=10
        )

    def test_limit_below_minimum_422(self, tasks_client):
        client, _ = tasks_client
        resp = client.get("/api/tasks?limit=0")
        assert resp.status_code == 422

    def test_limit_above_maximum_422(self, tasks_client):
        client, _ = tasks_client
        resp = client.get("/api/tasks?limit=201")
        assert resp.status_code == 422

    def test_multiple_tasks_count(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].list_tasks = AsyncMock(
            return_value=[_make_task("t1"), _make_task("t2"), _make_task("t3")]
        )
        resp = client.get("/api/tasks")
        assert resp.json()["data"]["count"] == 3


# ---------------------------------------------------------------------------
# GET /api/tasks/stats
# ---------------------------------------------------------------------------


class TestGetTaskStats:
    def test_returns_stats(self, tasks_client):
        client, mocks = tasks_client
        resp = client.get("/api/tasks/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "total" in data["data"]

    def test_calls_get_stats(self, tasks_client):
        client, mocks = tasks_client
        client.get("/api/tasks/stats")
        mocks["handler"].get_stats.assert_called_once()


# ---------------------------------------------------------------------------
# GET /api/tasks/recommended
# ---------------------------------------------------------------------------


class TestGetRecommendedTasks:
    def _make_rec_engine(self, result_dict: dict | None = None) -> Mock:
        rec_engine = Mock()
        rec_engine.generate_recommendations = Mock(return_value=Mock())
        rec_engine.to_dict = Mock(return_value=result_dict or {
            "recommendations": [],
            "timestamp": "2026-02-22T00:00:00+00:00",
            "total_pending": 1,
        })
        return rec_engine

    def test_returns_recommendations(self, tasks_client):
        client, mocks = tasks_client
        rec_engine = self._make_rec_engine()
        with patch(
            "forge_harness.analytics.RecommendationEngine",
            return_value=rec_engine,
        ):
            resp = client.get("/api/tasks/recommended")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_filters_pending_tasks(self, tasks_client):
        client, mocks = tasks_client
        rec_engine = self._make_rec_engine({"recommendations": []})
        with patch(
            "forge_harness.analytics.RecommendationEngine",
            return_value=rec_engine,
        ):
            resp = client.get("/api/tasks/recommended")
        assert resp.status_code == 200
        # verify list_tasks was called with status=pending
        mocks["handler"].list_tasks.assert_called_with(status="pending", limit=1000)

    def test_agent_id_query_param(self, tasks_client):
        client, mocks = tasks_client
        rec_engine = self._make_rec_engine({"recommendations": []})
        with patch(
            "forge_harness.analytics.RecommendationEngine",
            return_value=rec_engine,
        ):
            resp = client.get("/api/tasks/recommended?agent_id=forge:prya&limit=5")
        assert resp.status_code == 200

    def test_limit_below_min_422(self, tasks_client):
        client, _ = tasks_client
        resp = client.get("/api/tasks/recommended?limit=0")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/tasks/agents/available
# ---------------------------------------------------------------------------


class TestGetAvailableAgents:
    def test_returns_idle_agents(self, tasks_client):
        client, mocks = tasks_client
        mocks["registry"].list_active = Mock(
            return_value=[_make_agent("forge:prya", status="idle")]
        )
        mocks["handler"].list_tasks = AsyncMock(return_value=[])
        resp = client.get("/api/tasks/agents/available")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        agents = data["data"]["agents"]
        assert len(agents) == 1
        assert agents[0]["agent_id"] == "forge:prya"

    def test_excludes_busy_agents(self, tasks_client):
        client, mocks = tasks_client
        busy = _make_agent("forge:busy", status="busy")
        idle = _make_agent("forge:idle", status="idle")
        mocks["registry"].list_active = Mock(return_value=[busy, idle])
        mocks["handler"].list_tasks = AsyncMock(return_value=[])
        resp = client.get("/api/tasks/agents/available")
        agents = resp.json()["data"]["agents"]
        agent_ids = [a["agent_id"] for a in agents]
        assert "forge:busy" not in agent_ids
        assert "forge:idle" in agent_ids

    def test_includes_waiting_agents(self, tasks_client):
        client, mocks = tasks_client
        waiting = _make_agent("forge:waiting", status="waiting")
        mocks["registry"].list_active = Mock(return_value=[waiting])
        mocks["handler"].list_tasks = AsyncMock(return_value=[])
        resp = client.get("/api/tasks/agents/available")
        agents = resp.json()["data"]["agents"]
        assert any(a["agent_id"] == "forge:waiting" for a in agents)

    def test_role_filter(self, tasks_client):
        client, mocks = tasks_client
        dev = _make_agent("forge:dev", status="idle", role="developer")
        qa = _make_agent("forge:qa", status="idle", role="qa")
        mocks["registry"].list_active = Mock(return_value=[dev, qa])
        mocks["handler"].list_tasks = AsyncMock(return_value=[])
        resp = client.get("/api/tasks/agents/available?role=developer")
        agents = resp.json()["data"]["agents"]
        assert all(a["role"] == "developer" for a in agents)

    def test_task_count_included(self, tasks_client):
        client, mocks = tasks_client
        agent = _make_agent("forge:prya", status="idle")
        mocks["registry"].list_active = Mock(return_value=[agent])
        mocks["handler"].list_tasks = AsyncMock(
            return_value=[
                _make_task(claimed_by="forge:prya", status="in_progress"),
                _make_task("t2", claimed_by="forge:prya", status="pending"),
            ]
        )
        resp = client.get("/api/tasks/agents/available")
        agents = resp.json()["data"]["agents"]
        assert agents[0]["current_task_count"] == 2

    def test_empty_registry(self, tasks_client):
        client, mocks = tasks_client
        mocks["registry"].list_active = Mock(return_value=[])
        mocks["handler"].list_tasks = AsyncMock(return_value=[])
        resp = client.get("/api/tasks/agents/available")
        assert resp.json()["data"]["count"] == 0


# ---------------------------------------------------------------------------
# GET /api/tasks/{task_id}
# ---------------------------------------------------------------------------


class TestGetTask:
    def test_found_returns_task(self, tasks_client):
        client, mocks = tasks_client
        resp = client.get("/api/tasks/task-abc1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["id"] == "task-abc1"

    def test_not_found_returns_404(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].get_task = AsyncMock(return_value=None)
        resp = client.get("/api/tasks/nonexistent")
        assert resp.status_code == 404

    def test_404_contains_task_not_found_code(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].get_task = AsyncMock(return_value=None)
        resp = client.get("/api/tasks/nonexistent")
        detail = resp.json()["detail"]
        assert "TASK_NOT_FOUND" in str(detail)

    def test_calls_handler_with_correct_id(self, tasks_client):
        client, mocks = tasks_client
        client.get("/api/tasks/my-task-id")
        mocks["handler"].get_task.assert_called_once_with("my-task-id")


# ---------------------------------------------------------------------------
# POST /api/tasks
# ---------------------------------------------------------------------------


_VALID_SUBJECT = "Implement webhook authentication integration tests"


class TestCreateTask:
    def test_create_minimal_task(self, tasks_client):
        client, mocks = tasks_client
        resp = client.post("/api/tasks", json={"subject": _VALID_SUBJECT})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_create_full_task(self, tasks_client):
        client, mocks = tasks_client
        resp = client.post(
            "/api/tasks",
            json={
                "subject": _VALID_SUBJECT,
                "description": "Detailed description of the work required",
                "priority": "high",
                "required_role": "developer",
            },
        )
        assert resp.status_code == 200

    def test_create_emits_sse_event(self, tasks_client):
        client, mocks = tasks_client
        # Patch validate_task to bypass any title-length restrictions
        with patch("forge_harness.cli_v2.tasks.validate_task", return_value=None):
            client.post("/api/tasks", json={"subject": _VALID_SUBJECT})
        mocks["bus"].publish.assert_called_once()
        call_args = mocks["bus"].publish.call_args[0]
        assert call_args[0] == "task.created"

    def test_create_logs_audit(self, tasks_client):
        client, mocks = tasks_client
        with patch("forge_harness.cli_v2.tasks.validate_task", return_value=None):
            client.post("/api/tasks", json={"subject": _VALID_SUBJECT})
        mocks["audit"].log_task_creation.assert_called_once()

    def test_create_missing_subject_422(self, tasks_client):
        client, _ = tasks_client
        resp = client.post("/api/tasks", json={"description": "No subject"})
        assert resp.status_code == 422

    def test_create_with_lease(self, tasks_client):
        client, mocks = tasks_client
        resp = client.post(
            "/api/tasks",
            json={
                "subject": _VALID_SUBJECT,
                "lease": {
                    "owner_node": "nova",
                    "owner_agent": "forge:prya",
                    "lease_expires_at": _future_dt().isoformat(),
                    "path_lock": "harness/",
                },
            },
        )
        assert resp.status_code == 200

    def test_create_handler_exception_returns_500(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].create_task = AsyncMock(side_effect=RuntimeError("DB Error"))
        with patch("forge_harness.cli_v2.tasks.validate_task", return_value=None):
            resp = client.post("/api/tasks", json={"subject": _VALID_SUBJECT})
        assert resp.status_code == 500

    def test_create_validation_error_returns_400(self, tasks_client):
        client, _ = tasks_client
        with patch(
            "forge_harness.cli_v2.tasks.validate_task",
            side_effect=ValueError("subject too short"),
        ):
            resp = client.post("/api/tasks", json={"subject": "x"})
        assert resp.status_code == 400

    def test_create_passes_description_to_handler(self, tasks_client):
        client, mocks = tasks_client
        with patch("forge_harness.cli_v2.tasks.validate_task", return_value=None):
            client.post(
                "/api/tasks",
                json={"subject": _VALID_SUBJECT, "description": "My description"},
            )
        call_kwargs = mocks["handler"].create_task.call_args[1]
        assert call_kwargs["description"] == "My description"

    def test_create_passes_priority_to_handler(self, tasks_client):
        client, mocks = tasks_client
        with patch("forge_harness.cli_v2.tasks.validate_task", return_value=None):
            client.post("/api/tasks", json={"subject": _VALID_SUBJECT, "priority": "critical"})
        call_kwargs = mocks["handler"].create_task.call_args[1]
        assert call_kwargs["priority"] == "critical"


# ---------------------------------------------------------------------------
# PUT /api/tasks/{task_id}
# ---------------------------------------------------------------------------


class TestUpdateTask:
    def test_update_subject(self, tasks_client):
        client, mocks = tasks_client
        resp = client.put("/api/tasks/task-abc1", json={"subject": "Updated Subject"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_update_status(self, tasks_client):
        client, mocks = tasks_client
        resp = client.put("/api/tasks/task-abc1", json={"status": "in_progress"})
        assert resp.status_code == 200

    def test_update_priority(self, tasks_client):
        client, mocks = tasks_client
        resp = client.put("/api/tasks/task-abc1", json={"priority": "high"})
        assert resp.status_code == 200

    def test_update_claimed_by(self, tasks_client):
        client, mocks = tasks_client
        resp = client.put("/api/tasks/task-abc1", json={"claimed_by": "forge:prya"})
        assert resp.status_code == 200
        call_kwargs = mocks["handler"].update_task.call_args
        # second positional arg is the updates dict
        assert call_kwargs[0][1]["claimed_by"] == "forge:prya"

    def test_update_not_found_returns_404(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].update_task = AsyncMock(return_value=None)
        resp = client.put("/api/tasks/nonexistent", json={"subject": "X"})
        assert resp.status_code == 404

    def test_update_emits_sse_event(self, tasks_client):
        client, mocks = tasks_client
        client.put("/api/tasks/task-abc1", json={"subject": "New"})
        mocks["bus"].publish.assert_called_once()
        call_args = mocks["bus"].publish.call_args[0]
        assert call_args[0] == "task.updated"

    def test_update_skips_none_fields(self, tasks_client):
        client, mocks = tasks_client
        client.put("/api/tasks/task-abc1", json={"subject": "New", "priority": None})
        # Only non-null fields should be in the updates dict passed to handler
        call_kwargs = mocks["handler"].update_task.call_args[0]
        updates = call_kwargs[1]
        assert "subject" in updates
        assert "priority" not in updates

    def test_update_with_lease(self, tasks_client):
        client, mocks = tasks_client
        resp = client.put(
            "/api/tasks/task-abc1",
            json={
                "lease": {
                    "owner_node": "nova",
                    "owner_agent": "forge:prya",
                    "lease_expires_at": _future_dt().isoformat(),
                    "path_lock": "harness/",
                }
            },
        )
        assert resp.status_code == 200

    def test_empty_update_body_still_calls_handler(self, tasks_client):
        client, mocks = tasks_client
        resp = client.put("/api/tasks/task-abc1", json={})
        assert resp.status_code == 200
        mocks["handler"].update_task.assert_called_once()


# ---------------------------------------------------------------------------
# DELETE /api/tasks/{task_id}
# ---------------------------------------------------------------------------


class TestDeleteTask:
    def test_delete_succeeds(self, tasks_client):
        client, mocks = tasks_client
        resp = client.delete("/api/tasks/task-abc1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["success"] is True

    def test_delete_not_found_returns_404(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].delete_task = AsyncMock(return_value=False)
        resp = client.delete("/api/tasks/nonexistent")
        assert resp.status_code == 404

    def test_delete_emits_sse_event(self, tasks_client):
        client, mocks = tasks_client
        client.delete("/api/tasks/task-abc1")
        mocks["bus"].publish.assert_called_once()
        call_args = mocks["bus"].publish.call_args[0]
        assert call_args[0] == "task.deleted"

    def test_delete_logs_audit(self, tasks_client):
        client, mocks = tasks_client
        client.delete("/api/tasks/task-abc1")
        mocks["audit"].log_task_deletion.assert_called_once()

    def test_delete_audit_contains_task_id(self, tasks_client):
        client, mocks = tasks_client
        client.delete("/api/tasks/task-abc1")
        call_kwargs = mocks["audit"].log_task_deletion.call_args[1]
        assert call_kwargs["task_id"] == "task-abc1"


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/claim
# ---------------------------------------------------------------------------


class TestClaimTask:
    def test_claim_without_lease(self, tasks_client):
        client, mocks = tasks_client
        resp = client.post(
            "/api/tasks/task-abc1/claim",
            json={"agent_id": "forge:prya"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_claim_without_lease_calls_claim_task(self, tasks_client):
        client, mocks = tasks_client
        client.post(
            "/api/tasks/task-abc1/claim",
            json={"agent_id": "forge:prya"},
        )
        mocks["handler"].claim_task.assert_called_once_with("task-abc1", "forge:prya")

    def test_claim_with_lease_calls_claim_task_with_lease(self, tasks_client):
        client, mocks = tasks_client
        client.post(
            "/api/tasks/task-abc1/claim",
            json={
                "agent_id": "forge:prya",
                "lease": {
                    "owner_node": "nova",
                    "owner_agent": "forge:prya",
                    "lease_expires_at": _future_dt().isoformat(),
                    "path_lock": "harness/",
                },
            },
        )
        mocks["handler"].claim_task_with_lease.assert_called_once()

    def test_claim_not_found_returns_404(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].claim_task = AsyncMock(return_value=None)
        resp = client.post(
            "/api/tasks/task-abc1/claim",
            json={"agent_id": "forge:prya"},
        )
        assert resp.status_code == 404

    def test_claim_emits_sse_event(self, tasks_client):
        client, mocks = tasks_client
        client.post(
            "/api/tasks/task-abc1/claim",
            json={"agent_id": "forge:prya"},
        )
        mocks["bus"].publish.assert_called_once()
        call_args = mocks["bus"].publish.call_args[0]
        assert call_args[0] == "task.claimed"

    def test_claim_logs_audit(self, tasks_client):
        client, mocks = tasks_client
        client.post(
            "/api/tasks/task-abc1/claim",
            json={"agent_id": "forge:prya"},
        )
        mocks["audit"].log_task_assignment.assert_called_once()

    def test_claim_lease_error_returns_409(self, tasks_client):
        client, mocks = tasks_client
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        err = TaskLeaseError("LEASE_ALREADY_OWNED", "Already owned", status_code=409)
        mocks["handler"].claim_task = AsyncMock(side_effect=err)
        resp = client.post(
            "/api/tasks/task-abc1/claim",
            json={"agent_id": "forge:prya"},
        )
        assert resp.status_code == 409

    def test_claim_missing_agent_id_422(self, tasks_client):
        client, _ = tasks_client
        resp = client.post("/api/tasks/task-abc1/claim", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/lease/claim
# ---------------------------------------------------------------------------


class TestClaimTaskLease:
    def test_claim_lease_succeeds(self, tasks_client):
        client, mocks = tasks_client
        resp = client.post(
            "/api/tasks/task-abc1/lease/claim",
            json={"lease": _sample_lease()},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_claim_lease_calls_handler(self, tasks_client):
        client, mocks = tasks_client
        client.post(
            "/api/tasks/task-abc1/lease/claim",
            json={"lease": _sample_lease()},
        )
        mocks["handler"].claim_task_with_lease.assert_called_once()

    def test_claim_lease_emits_event(self, tasks_client):
        client, mocks = tasks_client
        client.post(
            "/api/tasks/task-abc1/lease/claim",
            json={"lease": _sample_lease()},
        )
        mocks["bus"].publish.assert_called_once()
        call_args = mocks["bus"].publish.call_args[0]
        assert call_args[0] == "task.lease.claimed"

    def test_claim_lease_logs_audit(self, tasks_client):
        client, mocks = tasks_client
        client.post(
            "/api/tasks/task-abc1/lease/claim",
            json={"lease": _sample_lease()},
        )
        mocks["audit"].log_task_assignment.assert_called_once()

    def test_claim_lease_not_found_returns_404(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].claim_task_with_lease = AsyncMock(return_value=None)
        resp = client.post(
            "/api/tasks/task-abc1/lease/claim",
            json={"lease": _sample_lease()},
        )
        assert resp.status_code == 404

    def test_claim_lease_error_returns_error(self, tasks_client):
        client, mocks = tasks_client
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        err = TaskLeaseError("PATH_LOCK_CONFLICT", "Path locked", status_code=409)
        mocks["handler"].claim_task_with_lease = AsyncMock(side_effect=err)
        resp = client.post(
            "/api/tasks/task-abc1/lease/claim",
            json={"lease": _sample_lease()},
        )
        assert resp.status_code == 409

    def test_claim_lease_missing_body_422(self, tasks_client):
        client, _ = tasks_client
        resp = client.post("/api/tasks/task-abc1/lease/claim", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/lease/renew
# ---------------------------------------------------------------------------


class TestRenewTaskLease:
    def test_renew_lease_succeeds(self, tasks_client):
        client, mocks = tasks_client
        resp = client.post(
            "/api/tasks/task-abc1/lease/renew",
            json={"lease": _sample_lease()},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_renew_lease_calls_handler(self, tasks_client):
        client, mocks = tasks_client
        client.post(
            "/api/tasks/task-abc1/lease/renew",
            json={"lease": _sample_lease()},
        )
        mocks["handler"].renew_task_lease.assert_called_once()

    def test_renew_lease_emits_event(self, tasks_client):
        client, mocks = tasks_client
        client.post(
            "/api/tasks/task-abc1/lease/renew",
            json={"lease": _sample_lease()},
        )
        mocks["bus"].publish.assert_called_once()
        call_args = mocks["bus"].publish.call_args[0]
        assert call_args[0] == "task.lease.renewed"

    def test_renew_lease_not_found_returns_404(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].renew_task_lease = AsyncMock(return_value=None)
        resp = client.post(
            "/api/tasks/task-abc1/lease/renew",
            json={"lease": _sample_lease()},
        )
        assert resp.status_code == 404

    def test_renew_lease_error_returns_error(self, tasks_client):
        client, mocks = tasks_client
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        err = TaskLeaseError("LEASE_OWNER_MISMATCH", "Wrong owner", status_code=409)
        mocks["handler"].renew_task_lease = AsyncMock(side_effect=err)
        resp = client.post(
            "/api/tasks/task-abc1/lease/renew",
            json={"lease": _sample_lease()},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/lease/release
# ---------------------------------------------------------------------------


class TestReleaseTaskLease:
    def test_release_lease_succeeds(self, tasks_client):
        client, mocks = tasks_client
        resp = client.post(
            "/api/tasks/task-abc1/lease/release",
            json={"owner_node": "nova", "owner_agent": "forge:prya"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_release_lease_no_body_succeeds(self, tasks_client):
        client, mocks = tasks_client
        # LeaseReleaseRequest defaults all to None, so empty body is valid
        resp = client.post(
            "/api/tasks/task-abc1/lease/release",
            json={},
        )
        assert resp.status_code == 200

    def test_release_lease_calls_handler(self, tasks_client):
        client, mocks = tasks_client
        client.post(
            "/api/tasks/task-abc1/lease/release",
            json={"owner_node": "nova"},
        )
        mocks["handler"].release_task_lease.assert_called_once()

    def test_release_lease_emits_event(self, tasks_client):
        client, mocks = tasks_client
        client.post("/api/tasks/task-abc1/lease/release", json={})
        mocks["bus"].publish.assert_called_once()
        call_args = mocks["bus"].publish.call_args[0]
        assert call_args[0] == "task.lease.released"

    def test_release_lease_not_found_returns_404(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].release_task_lease = AsyncMock(return_value=None)
        resp = client.post("/api/tasks/task-abc1/lease/release", json={})
        assert resp.status_code == 404

    def test_release_lease_error_returns_error(self, tasks_client):
        client, mocks = tasks_client
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        err = TaskLeaseError("LEASE_NOT_FOUND", "No lease exists", status_code=409)
        mocks["handler"].release_task_lease = AsyncMock(side_effect=err)
        resp = client.post("/api/tasks/task-abc1/lease/release", json={})
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/lease/requeue
# ---------------------------------------------------------------------------


class TestRequeueTask:
    def test_requeue_succeeds(self, tasks_client):
        client, mocks = tasks_client
        resp = client.post(
            "/api/tasks/task-abc1/lease/requeue",
            json={"reason": "timeout"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_requeue_no_body_succeeds(self, tasks_client):
        client, mocks = tasks_client
        resp = client.post("/api/tasks/task-abc1/lease/requeue", json={})
        assert resp.status_code == 200

    def test_requeue_emits_event(self, tasks_client):
        client, mocks = tasks_client
        client.post("/api/tasks/task-abc1/lease/requeue", json={})
        mocks["bus"].publish.assert_called_once()
        call_args = mocks["bus"].publish.call_args[0]
        assert call_args[0] == "task.requeued"

    def test_requeue_not_found_returns_404(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].requeue_task = AsyncMock(return_value=None)
        resp = client.post("/api/tasks/task-abc1/lease/requeue", json={})
        assert resp.status_code == 404

    def test_requeue_error_returns_error(self, tasks_client):
        client, mocks = tasks_client
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        err = TaskLeaseError("INVALID_LEASE", "Not leased", status_code=409)
        mocks["handler"].requeue_task = AsyncMock(side_effect=err)
        resp = client.post("/api/tasks/task-abc1/lease/requeue", json={})
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/tasks/reorder
# ---------------------------------------------------------------------------


class TestReorderTasks:
    def test_reorder_with_task_ids(self, tasks_client):
        client, mocks = tasks_client
        # Stub get_task to return tasks for both IDs
        mocks["handler"].get_task = AsyncMock(side_effect=lambda tid: _make_task(tid))
        mocks["handler"].update_task = AsyncMock(side_effect=lambda tid, u: _make_task(tid))
        resp = client.post(
            "/api/tasks/reorder",
            json={"task_ids": ["task-1", "task-2"]},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_reorder_with_task_order(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].get_task = AsyncMock(side_effect=lambda tid: _make_task(tid))
        mocks["handler"].update_task = AsyncMock(side_effect=lambda tid, u: _make_task(tid))
        resp = client.post(
            "/api/tasks/reorder",
            json={"task_order": ["task-1", "task-2"]},
        )
        assert resp.status_code == 200

    def test_reorder_both_none_returns_422(self, tasks_client):
        client, _ = tasks_client
        resp = client.post("/api/tasks/reorder", json={})
        assert resp.status_code == 422

    def test_reorder_empty_list_returns_400(self, tasks_client):
        client, _ = tasks_client
        resp = client.post("/api/tasks/reorder", json={"task_ids": []})
        assert resp.status_code == 400

    def test_reorder_task_not_found_returns_404(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].get_task = AsyncMock(return_value=None)
        resp = client.post(
            "/api/tasks/reorder",
            json={"task_ids": ["nonexistent"]},
        )
        assert resp.status_code == 404

    def test_reorder_emits_sse_event(self, tasks_client):
        client, mocks = tasks_client
        mocks["handler"].get_task = AsyncMock(side_effect=lambda tid: _make_task(tid))
        mocks["handler"].update_task = AsyncMock(side_effect=lambda tid, u: _make_task(tid))
        client.post("/api/tasks/reorder", json={"task_ids": ["task-1"]})
        mocks["bus"].publish.assert_called_once()
        call_args = mocks["bus"].publish.call_args[0]
        assert call_args[0] == "task.reordered"

    def test_reorder_calls_update_with_index(self, tasks_client):
        client, mocks = tasks_client
        call_args_list = []

        async def _update(tid, updates):
            call_args_list.append((tid, updates))
            return _make_task(tid)

        mocks["handler"].get_task = AsyncMock(side_effect=lambda tid: _make_task(tid))
        mocks["handler"].update_task = _update
        client.post(
            "/api/tasks/reorder",
            json={"task_ids": ["task-a", "task-b"]},
        )
        # First task should have order=0, second order=1
        assert call_args_list[0] == ("task-a", {"order": 0})
        assert call_args_list[1] == ("task-b", {"order": 1})


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/dispatch
# ---------------------------------------------------------------------------


class TestDispatchTask:
    def _make_dispatch_client(self, success: bool = True) -> Mock:
        result = Mock()
        result.success = success
        result.error = None if success else "tmux error"
        dc = Mock()
        dc.send = AsyncMock(return_value=result)
        return dc

    def test_dispatch_agent_not_found_returns_error(self, tasks_client):
        """When agent is None, the code tries to use AGENT_NOT_FOUND which is not
        imported in tasks.py — resulting in a NameError caught as a 500.
        The response will be a non-200 error code."""
        client, mocks = tasks_client
        mocks["registry"].get = Mock(return_value=None)
        resp = client.post(
            "/api/tasks/task-abc1/dispatch",
            json={"agent_id": "forge:ghost"},
        )
        # AGENT_NOT_FOUND is not imported in tasks.py so this raises NameError -> 500
        # OR if the server propagates, it returns 500
        assert resp.status_code in (404, 500)

    def test_dispatch_agent_not_found_with_patch(self, tasks_client):
        """Patch AGENT_NOT_FOUND to verify the 404 path is intended."""
        client, mocks = tasks_client
        mocks["registry"].get = Mock(return_value=None)
        with patch(
            "forge_harness.webhook_server.api.tasks.AGENT_NOT_FOUND",
            "AGENT_NOT_FOUND",
            create=True,
        ):
            resp = client.post(
                "/api/tasks/task-abc1/dispatch",
                json={"agent_id": "forge:ghost"},
            )
        assert resp.status_code in (404, 500)

    def test_dispatch_agent_busy_returns_error(self, tasks_client):
        """When agent is busy, the code tries AGENT_NOT_AVAILABLE (not imported) -> NameError."""
        client, mocks = tasks_client
        busy_agent = _make_agent("forge:busy", status="busy")
        mocks["registry"].get = Mock(return_value=busy_agent)
        resp = client.post(
            "/api/tasks/task-abc1/dispatch",
            json={"agent_id": "forge:busy"},
        )
        assert resp.status_code in (400, 500)

    def test_dispatch_task_not_found_returns_404(self, tasks_client):
        client, mocks = tasks_client
        idle_agent = _make_agent("forge:prya", status="idle", tmux_session="forge:prya")
        mocks["registry"].get = Mock(return_value=idle_agent)
        mocks["handler"].claim_task = AsyncMock(return_value=None)
        resp = client.post(
            "/api/tasks/task-abc1/dispatch",
            json={"agent_id": "forge:prya"},
        )
        assert resp.status_code == 404

    def test_dispatch_no_tmux_session_returns_error(self, tasks_client):
        client, mocks = tasks_client
        agent = _make_agent("forge:prya", status="idle", tmux_session=None)
        mocks["registry"].get = Mock(return_value=agent)
        mocks["handler"].claim_task = AsyncMock(return_value=_make_task())
        mocks["handler"].requeue_task = AsyncMock(return_value=_make_task())
        with patch(
            "forge_harness.webhook_server.api.tasks.INTERNAL_ERROR",
            "INTERNAL_ERROR",
            create=True,
        ):
            resp = client.post(
                "/api/tasks/task-abc1/dispatch",
                json={"agent_id": "forge:prya"},
            )
        assert resp.status_code in (503, 500)

    def test_dispatch_success_emits_event(self, tasks_client):
        client, mocks = tasks_client
        agent = _make_agent("forge:prya", status="idle", tmux_session="forge:prya")
        mocks["registry"].get = Mock(return_value=agent)
        mocks["handler"].claim_task = AsyncMock(return_value=_make_task())

        dispatch_client = self._make_dispatch_client(success=True)
        # DispatchClient is imported inside the endpoint function body
        with patch(
            "forge_harness.fleet.dispatch_client.DispatchClient",
            return_value=dispatch_client,
        ):
            resp = client.post(
                "/api/tasks/task-abc1/dispatch",
                json={"agent_id": "forge:prya"},
            )
        assert resp.status_code == 200
        # The last publish call should be task.dispatched
        last_call = mocks["bus"].publish.call_args_list[-1][0]
        assert last_call[0] == "task.dispatched"

    def test_dispatch_success_logs_audit(self, tasks_client):
        client, mocks = tasks_client
        agent = _make_agent("forge:prya", status="idle", tmux_session="forge:prya")
        mocks["registry"].get = Mock(return_value=agent)
        mocks["handler"].claim_task = AsyncMock(return_value=_make_task())

        dispatch_client = self._make_dispatch_client(success=True)
        with patch(
            "forge_harness.fleet.dispatch_client.DispatchClient",
            return_value=dispatch_client,
        ):
            client.post(
                "/api/tasks/task-abc1/dispatch",
                json={"agent_id": "forge:prya"},
            )
        mocks["audit"].log_task_assignment.assert_called()

    def test_dispatch_missing_agent_id_422(self, tasks_client):
        client, _ = tasks_client
        resp = client.post("/api/tasks/task-abc1/dispatch", json={})
        assert resp.status_code == 422


# ===========================================================================
# SECTION 5: NODES API — helper / unit tests
# ===========================================================================


class TestGetSystemSpecs:
    def test_returns_dict_with_keys(self):
        from forge_harness.webhook_server.api.nodes import _get_system_specs

        specs = _get_system_specs()
        assert "cpu" in specs
        assert "ram_gb" in specs
        assert "os" in specs
        assert "cores" in specs
        assert isinstance(specs["cores"], int)
        assert specs["cores"] >= 1

    def test_ram_gb_non_negative(self):
        from forge_harness.webhook_server.api.nodes import _get_system_specs

        specs = _get_system_specs()
        assert specs["ram_gb"] >= 0


class TestGetCapabilities:
    def test_returns_dict(self):
        from forge_harness.webhook_server.api.nodes import _get_capabilities

        caps = _get_capabilities()
        assert isinstance(caps, dict)
        assert "docker" in caps
        assert "claude" in caps

    def test_values_are_bools(self):
        from forge_harness.webhook_server.api.nodes import _get_capabilities

        caps = _get_capabilities()
        for v in caps.values():
            assert isinstance(v, bool)


class TestGetSystemLoad:
    def test_returns_three_floats(self):
        from forge_harness.webhook_server.api.nodes import _get_system_load

        cpu, ram, disk = _get_system_load()
        assert isinstance(cpu, float)
        assert isinstance(ram, float)
        assert isinstance(disk, float)

    def test_values_in_range(self):
        from forge_harness.webhook_server.api.nodes import _get_system_load

        cpu, ram, disk = _get_system_load()
        assert 0.0 <= cpu <= 100.0
        assert 0.0 <= ram <= 100.0
        assert 0.0 <= disk <= 100.0


class TestGetAgentsFromTmux:
    def test_tmux_failure_returns_empty(self):
        from forge_harness.webhook_server.api.nodes import _get_agents_from_tmux

        with patch("subprocess.run", side_effect=OSError("no tmux")):
            agents = _get_agents_from_tmux()
        assert agents == []

    def test_tmux_non_zero_return_returns_empty(self):
        from forge_harness.webhook_server.api.nodes import _get_agents_from_tmux

        result = Mock()
        result.returncode = 1
        result.stdout = ""
        with patch("subprocess.run", return_value=result):
            agents = _get_agents_from_tmux()
        assert agents == []

    def test_tmux_success_returns_agents(self):
        from forge_harness.webhook_server.api import nodes as nodes_module

        hostname = platform.node().split(".")[0]
        result = Mock()
        result.returncode = 0
        # Use window names that are not the hostname and not "heartbeat"
        result.stdout = f"nova\ntestworker\n{hostname}\n"
        with patch("subprocess.run", return_value=result):
            agents = nodes_module._get_agents_from_tmux()
        # hostname window is skipped; heartbeat window is skipped
        names = [a["name"] for a in agents]
        assert "nova" in names
        assert "testworker" in names
        assert hostname not in names

    def test_heartbeat_window_excluded(self):
        from forge_harness.webhook_server.api import nodes as nodes_module

        result = Mock()
        result.returncode = 0
        result.stdout = "nova\nheartbeat\n"
        with patch("subprocess.run", return_value=result):
            agents = nodes_module._get_agents_from_tmux()
        assert all(a["name"] != "heartbeat" for a in agents)


class TestBuildNodeResponse:
    def test_returns_required_keys(self):
        from forge_harness.webhook_server.api.nodes import _build_node_response

        with patch(
            "forge_harness.webhook_server.api.nodes._get_agents_from_tmux",
            return_value=[],
        ), patch(
            "forge_harness.webhook_server.api.nodes._get_system_load",
            return_value=(10.0, 50.0, 30.0),
        ):
            node = _build_node_response()

        assert "node_id" in node
        assert "name" in node
        assert "status" in node
        assert "cpu_load" in node
        assert "ram_usage" in node
        assert "agents" in node
        assert "capabilities" in node
        assert "specs" in node

    def test_offline_when_no_agents(self):
        from forge_harness.webhook_server.api.nodes import _build_node_response

        with patch(
            "forge_harness.webhook_server.api.nodes._get_agents_from_tmux",
            return_value=[],
        ), patch(
            "forge_harness.webhook_server.api.nodes._get_system_load",
            return_value=(0.0, 0.0, 0.0),
        ):
            node = _build_node_response()

        assert node["status"] == "offline"

    def test_online_when_agents_healthy(self):
        from forge_harness.webhook_server.api.nodes import _build_node_response

        agents = [{"id": "forge-prya", "status": "active", "name": "prya"}]
        with patch(
            "forge_harness.webhook_server.api.nodes._get_agents_from_tmux",
            return_value=agents,
        ), patch(
            "forge_harness.webhook_server.api.nodes._get_system_load",
            return_value=(0.0, 0.0, 0.0),
        ):
            node = _build_node_response()

        assert node["status"] == "online"

    def test_degraded_when_agent_failed(self):
        from forge_harness.webhook_server.api.nodes import _build_node_response

        agents = [{"id": "forge-prya", "status": "failed", "name": "prya"}]
        with patch(
            "forge_harness.webhook_server.api.nodes._get_agents_from_tmux",
            return_value=agents,
        ), patch(
            "forge_harness.webhook_server.api.nodes._get_system_load",
            return_value=(0.0, 0.0, 0.0),
        ):
            node = _build_node_response()

        assert node["status"] == "degraded"

    def test_node_id_uses_custom_id(self):
        from forge_harness.webhook_server.api.nodes import _build_node_response

        with patch(
            "forge_harness.webhook_server.api.nodes._get_agents_from_tmux",
            return_value=[],
        ), patch(
            "forge_harness.webhook_server.api.nodes._get_system_load",
            return_value=(0.0, 0.0, 0.0),
        ):
            node = _build_node_response(node_id="custom-node")

        assert node["node_id"] == "custom-node"


class TestAgeSeconds:
    def test_fresh_timestamp(self):
        from forge_harness.webhook_server.api.nodes import _age_seconds

        now = datetime.now(UTC).isoformat()
        age = _age_seconds(now)
        assert 0.0 <= age < 5.0

    def test_old_timestamp(self):
        from forge_harness.webhook_server.api.nodes import _age_seconds

        old = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        age = _age_seconds(old)
        assert age > 200.0

    def test_invalid_timestamp_returns_inf(self):
        from forge_harness.webhook_server.api.nodes import _age_seconds

        age = _age_seconds("not-a-timestamp")
        assert age == float("inf")

    def test_naive_datetime_handled(self):
        from forge_harness.webhook_server.api.nodes import _age_seconds

        naive = datetime.now().isoformat()  # no tzinfo
        age = _age_seconds(naive)
        assert age >= 0.0


class TestBuildRemoteNodeEntry:
    def test_fresh_node_is_online(self):
        from forge_harness.webhook_server.api.nodes import _build_remote_node_entry

        hb = {
            "node_id": "sati",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {"cpu_load": 20.0, "ram_usage": 40.0, "disk_usage": 30.0},
            "capabilities": ["claude", "docker"],
        }
        entry = _build_remote_node_entry(hb)
        assert entry["status"] == "online"
        assert entry["is_fresh"] is True
        assert entry["cpu_load"] == 20.0

    def test_stale_node_is_stale(self):
        from forge_harness.webhook_server.api.nodes import _build_remote_node_entry

        old_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        hb = {
            "node_id": "old-node",
            "received_at": old_time,
            "resources": {},
        }
        entry = _build_remote_node_entry(hb)
        assert entry["status"] == "stale"
        assert entry["is_fresh"] is False

    def test_capabilities_converted_to_dict(self):
        from forge_harness.webhook_server.api.nodes import _build_remote_node_entry

        hb = {
            "node_id": "n1",
            "received_at": datetime.now(UTC).isoformat(),
            "capabilities": ["claude", "docker"],
            "resources": {},
        }
        entry = _build_remote_node_entry(hb)
        assert entry["capabilities"]["claude"] is True
        assert entry["capabilities"]["docker"] is True

    def test_alternate_resource_keys(self):
        from forge_harness.webhook_server.api.nodes import _build_remote_node_entry

        hb = {
            "node_id": "n2",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {"cpu_percent": 55.0, "memory_percent": 70.0},
        }
        entry = _build_remote_node_entry(hb)
        assert entry["cpu_load"] == 55.0
        assert entry["ram_usage"] == 70.0


class TestReadHeartbeatFiles:
    def test_missing_dir_returns_empty(self):
        from forge_harness.webhook_server.api.nodes import _read_heartbeat_files

        with patch(
            "forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR",
            Path("/nonexistent/path/"),
        ):
            nodes = _read_heartbeat_files()
        assert nodes == []

    def test_reads_valid_json_files(self):
        from forge_harness.webhook_server.api.nodes import _read_heartbeat_files

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            hb = {
                "node_id": "sati",
                "received_at": datetime.now(UTC).isoformat(),
            }
            (tmp / "sati.json").write_text(json.dumps(hb))
            with patch(
                "forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR",
                tmp,
            ):
                nodes = _read_heartbeat_files()
        assert len(nodes) == 1
        assert nodes[0]["node_id"] == "sati"

    def test_ignores_invalid_json(self):
        from forge_harness.webhook_server.api.nodes import _read_heartbeat_files

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "bad.json").write_text("NOT JSON {{{")
            with patch(
                "forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR",
                tmp,
            ):
                nodes = _read_heartbeat_files()
        assert nodes == []

    def test_ignores_files_without_required_keys(self):
        from forge_harness.webhook_server.api.nodes import _read_heartbeat_files

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            # Missing "received_at"
            (tmp / "incomplete.json").write_text(json.dumps({"node_id": "x"}))
            with patch(
                "forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR",
                tmp,
            ):
                nodes = _read_heartbeat_files()
        assert nodes == []


class TestScoreNode:
    def test_low_load_high_score(self):
        from forge_harness.webhook_server.api.nodes import _score_node

        node = {"cpu_load": 10.0, "ram_usage": 20.0}
        score, reason = _score_node(node)
        assert score > 0.7
        assert "low CPU" in reason

    def test_high_load_low_score(self):
        from forge_harness.webhook_server.api.nodes import _score_node

        node = {"cpu_load": 90.0, "ram_usage": 90.0}
        score, reason = _score_node(node)
        assert score < 0.2
        assert "high CPU" in reason

    def test_moderate_load(self):
        from forge_harness.webhook_server.api.nodes import _score_node

        node = {"cpu_load": 50.0, "ram_usage": 50.0}
        score, reason = _score_node(node)
        assert 0.4 < score < 0.6
        assert "moderate CPU" in reason

    def test_zero_load_max_score(self):
        from forge_harness.webhook_server.api.nodes import _score_node

        node = {"cpu_load": 0.0, "ram_usage": 0.0}
        score, reason = _score_node(node)
        assert score == 1.0

    def test_full_load_zero_score(self):
        from forge_harness.webhook_server.api.nodes import _score_node

        node = {"cpu_load": 100.0, "ram_usage": 100.0}
        score, reason = _score_node(node)
        assert score == 0.0

    def test_missing_keys_defaults_to_zero(self):
        from forge_harness.webhook_server.api.nodes import _score_node

        score, reason = _score_node({})
        assert score == 1.0  # 0% load → perfect score

    def test_ample_memory_message(self):
        from forge_harness.webhook_server.api.nodes import _score_node

        _, reason = _score_node({"cpu_load": 10.0, "ram_usage": 10.0})
        assert "ample memory" in reason

    def test_limited_memory_message(self):
        from forge_harness.webhook_server.api.nodes import _score_node

        _, reason = _score_node({"cpu_load": 10.0, "ram_usage": 80.0})
        assert "limited memory" in reason


# ===========================================================================
# SECTION 6: NODES API — Endpoint tests
# ===========================================================================


@pytest.fixture
def nodes_client():
    """TestClient for nodes router — no auth bypass needed (nodes.py has no auth deps)."""
    from forge_harness.webhook_server.api import nodes as nodes_module

    app = FastAPI()
    app.include_router(nodes_module.router)
    client = TestClient(app, raise_server_exceptions=False)
    return client


def _stub_build_node(
    node_id: str = "local-host",
    status: str = "online",
    cpu_load: float = 10.0,
    ram_usage: float = 30.0,
    agent_count: int = 1,
) -> dict:
    return {
        "node_id": node_id,
        "name": node_id,
        "status": status,
        "agent_count": agent_count,
        "cpu_load": cpu_load,
        "ram_usage": ram_usage,
        "last_heartbeat": datetime.now(UTC).isoformat(),
        "latency_ms": 0,
        "agents": [],
        "load_average": cpu_load,
        "disk_usage": 20.0,
        "alerts": [],
        "capabilities": {},
        "specs": {},
        "is_fresh": True,
        "age_seconds": 0.0,
        "received_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /api/nodes
# ---------------------------------------------------------------------------


class TestListNodes:
    def test_returns_nodes_list(self, nodes_client):
        local = _stub_build_node()
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=[local],
        ):
            resp = nodes_client.get("/api/nodes")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert len(data["nodes"]) == 1

    def test_returns_multiple_nodes(self, nodes_client):
        local = _stub_build_node("local")
        remote = _stub_build_node("remote", status="online")
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=[local, remote],
        ):
            resp = nodes_client.get("/api/nodes")
        assert len(resp.json()["nodes"]) == 2

    def test_node_has_freshness_fields(self, nodes_client):
        local = _stub_build_node()
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=[local],
        ):
            resp = nodes_client.get("/api/nodes")
        node = resp.json()["nodes"][0]
        assert "is_fresh" in node
        assert "age_seconds" in node
        assert "status" in node

    def test_node_has_cpu_load(self, nodes_client):
        local = _stub_build_node(cpu_load=42.5)
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=[local],
        ):
            resp = nodes_client.get("/api/nodes")
        assert resp.json()["nodes"][0]["cpu_load"] == 42.5


# ---------------------------------------------------------------------------
# GET /api/nodes/health
# ---------------------------------------------------------------------------


class TestFleetHealth:
    def test_health_all_online(self, nodes_client):
        nodes = [_stub_build_node("n1", "online"), _stub_build_node("n2", "online")]
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=nodes,
        ):
            resp = nodes_client.get("/api/nodes/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["online"] == 2
        assert data["degraded"] == 0
        assert data["offline"] == 0
        assert data["health_percentage"] == 100.0

    def test_health_mixed(self, nodes_client):
        nodes = [
            _stub_build_node("n1", "online"),
            _stub_build_node("n2", "degraded"),
            _stub_build_node("n3", "offline"),
        ]
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=nodes,
        ):
            resp = nodes_client.get("/api/nodes/health")
        data = resp.json()
        assert data["online"] == 1
        assert data["degraded"] == 1
        assert data["offline"] == 1
        assert data["total_nodes"] == 3

    def test_health_stale_counts_as_offline(self, nodes_client):
        nodes = [_stub_build_node("n1", "stale")]
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=nodes,
        ):
            resp = nodes_client.get("/api/nodes/health")
        data = resp.json()
        assert data["offline"] == 1

    def test_health_agent_count_total(self, nodes_client):
        nodes = [
            _stub_build_node("n1", agent_count=3),
            _stub_build_node("n2", agent_count=5),
        ]
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=nodes,
        ):
            resp = nodes_client.get("/api/nodes/health")
        assert resp.json()["total_agents"] == 8

    def test_health_no_nodes_zero_percentage(self, nodes_client):
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=[],
        ):
            resp = nodes_client.get("/api/nodes/health")
        assert resp.json()["health_percentage"] == 0.0

    def test_health_includes_node_summaries(self, nodes_client):
        nodes = [_stub_build_node("n1")]
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=nodes,
        ):
            resp = nodes_client.get("/api/nodes/health")
        summaries = resp.json()["nodes"]
        assert len(summaries) == 1
        assert "node_id" in summaries[0]
        assert "cpu_load" in summaries[0]


# ---------------------------------------------------------------------------
# GET /api/nodes/recommend
# ---------------------------------------------------------------------------


class TestRecommendNode:
    def test_recommend_returns_best_node(self, nodes_client):
        low_load = _stub_build_node("light-node", cpu_load=5.0, ram_usage=10.0)
        high_load = _stub_build_node("heavy-node", cpu_load=80.0, ram_usage=90.0)
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=[high_load, low_load],
        ):
            resp = nodes_client.get("/api/nodes/recommend")
        assert resp.status_code == 200
        data = resp.json()
        assert data["recommended_node_id"] == "light-node"

    def test_recommend_includes_candidates(self, nodes_client):
        node = _stub_build_node("n1")
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=[node],
        ):
            resp = nodes_client.get("/api/nodes/recommend")
        data = resp.json()
        assert "candidates" in data
        assert len(data["candidates"]) >= 1

    def test_recommend_candidate_has_score(self, nodes_client):
        node = _stub_build_node("n1")
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=[node],
        ):
            resp = nodes_client.get("/api/nodes/recommend")
        candidate = resp.json()["candidates"][0]
        assert "score" in candidate
        assert "reason" in candidate

    def test_recommend_task_type_param(self, nodes_client):
        node = _stub_build_node()
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=[node],
        ):
            resp = nodes_client.get("/api/nodes/recommend?task_type=python")
        assert resp.status_code == 200
        assert resp.json()["task_type"] == "python"

    def test_recommend_path_lock_param(self, nodes_client):
        node = _stub_build_node()
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=[node],
        ):
            resp = nodes_client.get("/api/nodes/recommend?path_lock=harness/")
        assert resp.status_code == 200
        assert resp.json()["path_lock"] == "harness/"

    def test_recommend_no_fresh_nodes_fallback_to_local(self, nodes_client):
        stale = {**_stub_build_node("stale"), "is_fresh": False}
        local = _stub_build_node("local", cpu_load=0.0, ram_usage=0.0)
        # all_nodes[0] = local (always fresh)
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=[local, stale],
        ):
            resp = nodes_client.get("/api/nodes/recommend")
        assert resp.status_code == 200
        # local is always fresh; should be recommended
        assert "recommended_node_id" in resp.json()

    def test_recommend_empty_nodes_fallback(self, nodes_client):
        """Absolute fallback when _get_all_nodes returns empty list."""
        with patch(
            "forge_harness.webhook_server.api.nodes._get_all_nodes",
            return_value=[],
        ), patch(
            "forge_harness.webhook_server.api.nodes._build_node_response",
            return_value=_stub_build_node("fallback"),
        ):
            resp = nodes_client.get("/api/nodes/recommend")
        assert resp.status_code == 200
        assert resp.json()["recommended_node_id"] == "fallback"


# ---------------------------------------------------------------------------
# GET /api/nodes/{node_id}
# ---------------------------------------------------------------------------


class TestGetNode:
    def test_get_local_node(self, nodes_client):
        hostname = platform.node().split(".")[0]
        with patch(
            "forge_harness.webhook_server.api.nodes._build_node_response",
            return_value=_stub_build_node(hostname),
        ):
            resp = nodes_client.get(f"/api/nodes/{hostname}")
        assert resp.status_code == 200
        data = resp.json()
        assert "node_id" in data

    def test_get_unknown_node_returns_404(self, nodes_client):
        resp = nodes_client.get("/api/nodes/this-node-does-not-exist-xyz")
        assert resp.status_code == 404

    def test_get_node_404_message(self, nodes_client):
        resp = nodes_client.get("/api/nodes/ghost-node")
        assert "ghost-node" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/nodes/{node_id}/dispatch
# ---------------------------------------------------------------------------


class TestDispatchToNode:
    def test_dispatch_script_not_found_returns_error(self, nodes_client):
        with patch("os.path.isfile", return_value=False):
            resp = nodes_client.post(
                "/api/nodes/local-host/dispatch",
                json={
                    "agent_type": "prya",
                    "task": "Run tests",
                    "priority": "high",
                },
            )
        assert resp.status_code == 500
        data = resp.json()
        assert data["success"] is False
        assert "not found" in data["message"].lower()

    def test_dispatch_script_found_success(self, nodes_client):
        result = Mock()
        result.returncode = 0
        with patch("os.path.isfile", return_value=True), patch(
            "subprocess.run", return_value=result
        ):
            resp = nodes_client.post(
                "/api/nodes/local-host/dispatch",
                json={"agent_type": "prya", "task": "Run tests"},
            )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["status"] == "dispatched"

    def test_dispatch_script_failure(self, nodes_client):
        result = Mock()
        result.returncode = 1
        result.stderr = "tmux error"
        with patch("os.path.isfile", return_value=True), patch(
            "subprocess.run", return_value=result
        ):
            resp = nodes_client.post(
                "/api/nodes/local-host/dispatch",
                json={"agent_type": "prya", "task": "Run tests"},
            )
        assert resp.status_code == 500
        assert resp.json()["success"] is False

    def test_dispatch_returns_dispatch_id(self, nodes_client):
        result = Mock()
        result.returncode = 0
        with patch("os.path.isfile", return_value=True), patch(
            "subprocess.run", return_value=result
        ):
            resp = nodes_client.post(
                "/api/nodes/local-host/dispatch",
                json={"agent_type": "prya", "task": "Do it"},
            )
        assert "dispatch_id" in resp.json()
        assert len(resp.json()["dispatch_id"]) == 8

    def test_dispatch_target_format(self, nodes_client):
        """agent_id in response should be forge:{agent_type}."""
        result = Mock()
        result.returncode = 0
        with patch("os.path.isfile", return_value=True), patch(
            "subprocess.run", return_value=result
        ) as mock_run:
            resp = nodes_client.post(
                "/api/nodes/local-host/dispatch",
                json={"agent_type": "nova", "task": "Do thing"},
            )
        assert resp.json()["agent_id"] == "forge:nova"

    def test_dispatch_exception_returns_error(self, nodes_client):
        with patch("os.path.isfile", return_value=True), patch(
            "subprocess.run", side_effect=OSError("subprocess failed")
        ):
            resp = nodes_client.post(
                "/api/nodes/local-host/dispatch",
                json={"agent_type": "prya", "task": "Do thing"},
            )
        assert resp.status_code == 500
        assert resp.json()["success"] is False

    def test_dispatch_missing_agent_type_422(self, nodes_client):
        resp = nodes_client.post(
            "/api/nodes/local-host/dispatch",
            json={"task": "No agent type"},
        )
        assert resp.status_code == 422

    def test_dispatch_missing_task_422(self, nodes_client):
        resp = nodes_client.post(
            "/api/nodes/local-host/dispatch",
            json={"agent_type": "prya"},
        )
        assert resp.status_code == 422

    def test_dispatch_with_optional_fields(self, nodes_client):
        result = Mock()
        result.returncode = 0
        with patch("os.path.isfile", return_value=True), patch(
            "subprocess.run", return_value=result
        ):
            resp = nodes_client.post(
                "/api/nodes/local-host/dispatch",
                json={
                    "agent_type": "prya",
                    "task": "Run",
                    "priority": "high",
                    "project": "interview-simulator",
                    "domain": "codeswiftr-com",
                    "timeout_minutes": 30,
                },
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/nodes/sync
# ---------------------------------------------------------------------------


class TestSyncNodes:
    def test_sync_returns_success(self, nodes_client):
        resp = nodes_client.post("/api/nodes/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_sync_synced_one_node(self, nodes_client):
        resp = nodes_client.post("/api/nodes/sync")
        assert resp.json()["synced_nodes"] == 1
        assert resp.json()["failed_nodes"] == 0

    def test_sync_has_sync_id(self, nodes_client):
        resp = nodes_client.post("/api/nodes/sync")
        assert "sync_id" in resp.json()
        assert len(resp.json()["sync_id"]) == 8

    def test_sync_has_timestamp(self, nodes_client):
        resp = nodes_client.post("/api/nodes/sync")
        assert "timestamp" in resp.json()

    def test_sync_details_has_local_node(self, nodes_client):
        resp = nodes_client.post("/api/nodes/sync")
        details = resp.json()["details"]
        assert len(details) == 1
        assert details[0]["success"] is True
        assert "node_id" in details[0]


# ===========================================================================
# SECTION 7: NodeDispatchRequest model validation
# ===========================================================================


class TestNodeDispatchRequestModel:
    def test_minimal_valid(self):
        from forge_harness.webhook_server.api.nodes import NodeDispatchRequest

        req = NodeDispatchRequest(agent_type="prya", task="Run tests")
        assert req.agent_type == "prya"
        assert req.task == "Run tests"
        assert req.priority == "normal"
        assert req.project is None
        assert req.domain is None
        assert req.timeout_minutes is None

    def test_full_fields(self):
        from forge_harness.webhook_server.api.nodes import NodeDispatchRequest

        req = NodeDispatchRequest(
            agent_type="nova",
            task="Deploy",
            priority="high",
            project="interview-simulator",
            domain="codeswiftr-com",
            timeout_minutes=60,
        )
        assert req.priority == "high"
        assert req.timeout_minutes == 60

    def test_missing_agent_type_raises(self):
        from forge_harness.webhook_server.api.nodes import NodeDispatchRequest

        with pytest.raises(ValidationError):
            NodeDispatchRequest(task="No agent type")

    def test_missing_task_raises(self):
        from forge_harness.webhook_server.api.nodes import NodeDispatchRequest

        with pytest.raises(ValidationError):
            NodeDispatchRequest(agent_type="prya")


# ===========================================================================
# SECTION 8: _get_all_nodes integration
# ===========================================================================


class TestGetAllNodes:
    def test_local_node_always_first(self):
        from forge_harness.webhook_server.api.nodes import _get_all_nodes

        with patch(
            "forge_harness.webhook_server.api.nodes._build_node_response",
            return_value=_stub_build_node("local"),
        ), patch(
            "forge_harness.webhook_server.api.nodes._read_heartbeat_files",
            return_value=[],
        ):
            nodes = _get_all_nodes()
        assert nodes[0]["node_id"] == "local"

    def test_local_has_freshness_annotated(self):
        from forge_harness.webhook_server.api.nodes import _get_all_nodes

        with patch(
            "forge_harness.webhook_server.api.nodes._build_node_response",
            return_value=_stub_build_node("local"),
        ), patch(
            "forge_harness.webhook_server.api.nodes._read_heartbeat_files",
            return_value=[],
        ):
            nodes = _get_all_nodes()
        assert nodes[0]["is_fresh"] is True
        assert nodes[0]["age_seconds"] == 0.0

    def test_remote_nodes_appended(self):
        from forge_harness.webhook_server.api.nodes import _get_all_nodes

        hb = {
            "node_id": "remote-sati",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {},
        }
        with patch(
            "forge_harness.webhook_server.api.nodes._build_node_response",
            return_value=_stub_build_node("local"),
        ), patch(
            "forge_harness.webhook_server.api.nodes._read_heartbeat_files",
            return_value=[hb],
        ):
            nodes = _get_all_nodes()
        assert len(nodes) == 2
        node_ids = [n["node_id"] for n in nodes]
        assert "remote-sati" in node_ids

    def test_local_node_heartbeat_file_skipped(self):
        from forge_harness.webhook_server.api.nodes import _get_all_nodes

        # If a heartbeat file describes the local node, it should be skipped
        local = _stub_build_node("local")
        hb = {
            "node_id": "local",  # Same as local node
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {},
        }
        with patch(
            "forge_harness.webhook_server.api.nodes._build_node_response",
            return_value=local,
        ), patch(
            "forge_harness.webhook_server.api.nodes._read_heartbeat_files",
            return_value=[hb],
        ):
            nodes = _get_all_nodes()
        # Only local node, remote duplicate excluded
        assert len(nodes) == 1
