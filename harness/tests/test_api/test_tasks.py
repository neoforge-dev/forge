"""Tests for /api/tasks endpoints.

Tests cover:
- GET /api/tasks - List tasks with filters
- POST /api/tasks - Create new task
- GET /api/tasks/{task_id} - Get task details
- PUT /api/tasks/{task_id} - Update task
- DELETE /api/tasks/{task_id} - Delete task
- POST /api/tasks/{task_id}/claim - Claim task
- POST /api/tasks/{task_id}/dispatch - Dispatch task
- GET /api/tasks/agents/available - List available agents
- POST /api/tasks/reorder - Reorder tasks
- GET /api/tasks/recommended - Get recommended tasks
- GET /api/tasks/stats - Get task statistics
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestTasksAPI:
    """Test class for /api/tasks endpoints."""

    @staticmethod
    def _response_body_text(response) -> str:
        """Decode JSONResponse body into text."""
        return response.body.decode()

    @staticmethod
    def _mock_request() -> Mock:
        """Create a minimal request-like object for endpoint unit tests."""
        request = Mock()
        request.headers = {}
        request.client = Mock(host="testclient")
        return request

    @pytest.fixture
    def mock_task_handler(self):
        """Create a mock task handler."""
        handler = Mock()

        # Sample tasks
        task1 = {
            "id": "task-001",
            "subject": "Implement user authentication",
            "description": "Add JWT-based authentication",
            "priority": "high",
            "status": "pending",
            "claimed_by": None,
            "created_at": datetime.now(UTC).isoformat(),
        }

        task2 = {
            "id": "task-002",
            "subject": "Create API endpoints",
            "description": "Build REST API",
            "priority": "medium",
            "status": "in_progress",
            "claimed_by": "agent-001",
            "created_at": datetime.now(UTC).isoformat(),
        }

        handler.list_tasks = AsyncMock(return_value=[task1, task2])
        handler.get_task = AsyncMock(
            side_effect=lambda x: {
                "task-001": task1,
                "task-002": task2,
            }.get(x)
        )
        handler.create_task = AsyncMock(return_value=task1)
        handler.update_task = AsyncMock(return_value=task1)
        handler.delete_task = AsyncMock(return_value=True)
        handler.claim_task = AsyncMock(return_value=task1)
        handler.requeue_task = AsyncMock(return_value=task1)
        handler.get_stats = AsyncMock(
            return_value={
                "total": 10,
                "pending": 5,
                "in_progress": 3,
                "completed": 2,
                "high_priority": 3,
                "avg_wait_time_hours": 2.5,
            }
        )

        return handler

    @pytest.fixture
    def mock_agent_registry(self):
        """Create a mock agent registry."""
        registry = Mock()

        agent = Mock()
        agent.id = "agent-001"
        agent.role = "backend-engineer"
        agent.name = "Backend Agent"
        agent.status = "idle"
        agent.tmux_session = "forge:backend"

        registry.list_active = Mock(return_value=[agent])
        registry.get = Mock(return_value=agent)

        return registry

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = AsyncMock()
        bus.publish = AsyncMock()
        return bus

    async def test_list_tasks(self, mock_task_handler):
        """Test listing tasks with optional filters."""
        from forge_harness.webhook_server.api.tasks import list_tasks

        with patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler:
            mock_handler.return_value = mock_task_handler

            result = await list_tasks(status="pending", priority="high", limit=50)

            assert result.status_code == 200
            response_text = self._response_body_text(result)
            assert "success" in response_text
            assert "tasks" in response_text
            assert "count" in response_text

    async def test_list_tasks_empty(self, mock_task_handler):
        """Test listing tasks when empty."""
        mock_task_handler.list_tasks = AsyncMock(return_value=[])

        from forge_harness.webhook_server.api.tasks import list_tasks

        with patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler:
            mock_handler.return_value = mock_task_handler

            result = await list_tasks()

            assert result.status_code == 200
            response_text = self._response_body_text(result)
            assert "success" in response_text
            assert "tasks" in response_text

    async def test_create_task(self, mock_task_handler, mock_event_bus):
        """Test creating a new task."""
        from forge_harness.webhook_server.api.tasks import CreateTaskRequest, create_task

        request = CreateTaskRequest(
            subject="New Feature",
            description="Implement a new feature",
            priority="high",
            required_role="backend-engineer",
        )

        with (
            patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler,
            patch("forge_harness.webhook_server.api.tasks.get_event_bus") as mock_bus,
        ):
            mock_handler.return_value = mock_task_handler
            mock_bus.return_value = mock_event_bus

            result = await create_task(request, self._mock_request())

            assert result.status_code == 200
            assert "success" in self._response_body_text(result)
            mock_task_handler.create_task.assert_called_once()

    async def test_get_task_found(self, mock_task_handler):
        """Test getting an existing task."""
        from forge_harness.webhook_server.api.tasks import get_task

        with patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler:
            mock_handler.return_value = mock_task_handler

            result = await get_task("task-001")

            assert result.status_code == 200
            assert "success" in self._response_body_text(result)
            mock_task_handler.get_task.assert_called_with("task-001")

    async def test_get_task_not_found(self, mock_task_handler):
        """Test getting a non-existent task."""
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.tasks import get_task

        mock_task_handler.get_task = AsyncMock(return_value=None)

        with patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler:
            mock_handler.return_value = mock_task_handler

            with pytest.raises(HTTPException) as exc_info:
                await get_task("non-existent")
            assert exc_info.value.status_code == 404

    async def test_update_task(self, mock_task_handler, mock_event_bus):
        """Test updating a task."""
        from forge_harness.webhook_server.api.tasks import UpdateTaskRequest, update_task

        request = UpdateTaskRequest(subject="Updated Subject", priority="low")

        with (
            patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler,
            patch("forge_harness.webhook_server.api.tasks.get_event_bus") as mock_bus,
        ):
            mock_handler.return_value = mock_task_handler
            mock_bus.return_value = mock_event_bus

            result = await update_task("task-001", request)

            assert result.status_code == 200
            assert "success" in self._response_body_text(result)

    async def test_delete_task(self, mock_task_handler, mock_event_bus):
        """Test deleting a task."""
        from forge_harness.webhook_server.api.tasks import delete_task

        with (
            patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler,
            patch("forge_harness.webhook_server.api.tasks.get_event_bus") as mock_bus,
        ):
            mock_handler.return_value = mock_task_handler
            mock_bus.return_value = mock_event_bus

            result = await delete_task("task-001", self._mock_request())

            assert result.status_code == 200
            mock_task_handler.delete_task.assert_called_with("task-001")

    async def test_delete_task_not_found(self, mock_task_handler):
        """Test deleting a non-existent task."""
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.tasks import delete_task

        mock_task_handler.delete_task = AsyncMock(return_value=False)

        with patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler:
            mock_handler.return_value = mock_task_handler

            with pytest.raises(HTTPException) as exc_info:
                await delete_task("non-existent", self._mock_request())
            assert exc_info.value.status_code == 404

    async def test_claim_task(self, mock_task_handler, mock_event_bus):
        """Test claiming a task for an agent."""
        from forge_harness.webhook_server.api.tasks import ClaimTaskRequest, claim_task

        request = ClaimTaskRequest(agent_id="agent-001")

        with (
            patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler,
            patch("forge_harness.webhook_server.api.tasks.get_event_bus") as mock_bus,
        ):
            mock_handler.return_value = mock_task_handler
            mock_bus.return_value = mock_event_bus

            result = await claim_task("task-001", request, self._mock_request())

            assert result.status_code == 200
            assert "success" in self._response_body_text(result)

    async def test_dispatch_task(self, mock_task_handler, mock_agent_registry, mock_event_bus):
        """Test dispatching a task to an agent."""
        from forge_harness.webhook_server.api.tasks import DispatchRequest, dispatch_task

        request = DispatchRequest(agent_id="agent-001")

        # Mock DispatchResult for successful dispatch
        mock_dispatch_result = Mock()
        mock_dispatch_result.success = True
        mock_dispatch_result.error = None

        with (
            patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler,
            patch("forge_harness.webhook_server.api.tasks.get_agent_registry") as mock_reg,
            patch("forge_harness.webhook_server.api.tasks.get_event_bus") as mock_bus,
            patch("forge_harness.fleet.dispatch_client.DispatchClient") as mock_dispatch_client,
        ):
            mock_handler.return_value = mock_task_handler
            mock_reg.return_value = mock_agent_registry
            mock_bus.return_value = mock_event_bus

            # Mock the DispatchClient.send to return success
            mock_client_instance = mock_dispatch_client.return_value
            mock_client_instance.send = AsyncMock(return_value=mock_dispatch_result)

            result = await dispatch_task("task-001", request, self._mock_request())

            assert result.status_code == 200

    async def test_dispatch_task_agent_not_found(
        self, mock_task_handler, mock_agent_registry, mock_event_bus
    ):
        """Test dispatching task to non-existent agent."""
        from forge_harness.webhook_server.api.tasks import DispatchRequest, dispatch_task

        request = DispatchRequest(agent_id="non-existent")

        with (
            patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler,
            patch("forge_harness.webhook_server.api.tasks.get_agent_registry") as mock_reg,
            patch("forge_harness.webhook_server.api.tasks.get_event_bus") as mock_bus,
        ):
            mock_handler.return_value = mock_task_handler
            mock_reg.return_value = mock_agent_registry
            mock_agent_registry.get.return_value = None
            mock_bus.return_value = mock_event_bus

            result = await dispatch_task("task-001", request, self._mock_request())

            # Should return 404 error in response
            assert "AGENT_NOT_FOUND" in self._response_body_text(result)

    async def test_get_available_agents(self, mock_task_handler, mock_agent_registry):
        """Test getting available agents for task assignment."""
        from forge_harness.webhook_server.api.tasks import get_available_agents

        with (
            patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler,
            patch("forge_harness.webhook_server.api.tasks.get_agent_registry") as mock_reg,
        ):
            mock_handler.return_value = mock_task_handler
            mock_reg.return_value = mock_agent_registry

            result = await get_available_agents()

            assert result.status_code == 200
            response_text = self._response_body_text(result)
            assert "success" in response_text
            assert "agents" in response_text

    async def test_reorder_tasks(self, mock_task_handler, mock_event_bus):
        """Test reordering tasks."""
        from forge_harness.webhook_server.api.tasks import ReorderRequest, reorder_tasks

        request = ReorderRequest(task_ids=["task-001", "task-002"])

        with (
            patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler,
            patch("forge_harness.webhook_server.api.tasks.get_event_bus") as mock_bus,
        ):
            mock_handler.return_value = mock_task_handler
            mock_bus.return_value = mock_event_bus

            result = await reorder_tasks(request)

            assert result.status_code == 200
            assert "success" in self._response_body_text(result)

    async def test_reorder_tasks_empty(self, mock_task_handler):
        """Test reordering with empty task list."""
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.tasks import ReorderRequest, reorder_tasks

        request = ReorderRequest(task_ids=[])

        with patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler:
            mock_handler.return_value = mock_task_handler

            with pytest.raises(HTTPException) as exc_info:
                await reorder_tasks(request)
            assert "cannot be empty" in str(exc_info.value.detail)

    async def test_get_task_stats(self, mock_task_handler):
        """Test getting task statistics."""
        from forge_harness.webhook_server.api.tasks import get_task_stats

        with patch("forge_harness.webhook_server.api.tasks.get_task_handler") as mock_handler:
            mock_handler.return_value = mock_task_handler

            result = await get_task_stats()

            assert result.status_code == 200
            response_text = self._response_body_text(result)
            assert "success" in response_text
            assert "total" in response_text
            assert "pending" in response_text


class TestTasksAPIModels:
    """Test Pydantic models for tasks API."""

    def test_create_task_request(self):
        """Test CreateTaskRequest model."""
        from forge_harness.webhook_server.api.tasks import CreateTaskRequest

        request = CreateTaskRequest(
            subject="Implement Feature",
            description="Build a new feature",
            priority="high",
            required_role="backend",
        )

        assert request.subject == "Implement Feature"
        assert request.description == "Build a new feature"
        assert request.priority == "high"
        assert request.required_role == "backend"

    def test_create_task_request_defaults(self):
        """Test CreateTaskRequest default values."""
        from forge_harness.webhook_server.api.tasks import CreateTaskRequest

        request = CreateTaskRequest(subject="Test", description="Test description")

        assert request.priority == "medium"
        assert request.required_role is None

    def test_update_task_request(self):
        """Test UpdateTaskRequest model."""
        from forge_harness.webhook_server.api.tasks import UpdateTaskRequest

        request = UpdateTaskRequest(subject="Updated Subject", priority="low", status="completed")

        assert request.subject == "Updated Subject"
        assert request.priority == "low"
        assert request.status == "completed"

    def test_claim_task_request(self):
        """Test ClaimTaskRequest model."""
        from forge_harness.webhook_server.api.tasks import ClaimTaskRequest

        request = ClaimTaskRequest(agent_id="agent-001")

        assert request.agent_id == "agent-001"

    def test_dispatch_task_request(self):
        """Test DispatchRequest model."""
        from forge_harness.webhook_server.api.tasks import DispatchRequest

        request = DispatchRequest(agent_id="agent-002")

        assert request.agent_id == "agent-002"

    def test_reorder_task_request(self):
        """Test ReorderRequest model."""
        from forge_harness.webhook_server.api.tasks import ReorderRequest

        request = ReorderRequest(task_ids=["task-1", "task-2", "task-3"])

        assert len(request.task_ids) == 3
        assert request.task_ids[0] == "task-1"


class TestTasksAPIResponse:
    """Test API response formatting."""

    def test_api_response_success(self):
        """Test successful API response format."""
        from forge_harness.webhook_server.api.tasks import api_response

        result = api_response(data={"task_id": "123"})

        assert result["success"] is True
        assert result["data"]["task_id"] == "123"
        assert result["error"] is None
        assert "timestamp" in result

    def test_api_response_error(self):
        """Test error API response format."""
        from forge_harness.webhook_server.api.tasks import api_response

        result = api_response(error_code="VALIDATION_ERROR", error_message="Invalid input")

        assert result["success"] is False
        assert result["data"] is None
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert result["error"]["message"] == "Invalid input"
