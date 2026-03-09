"""Tests for webhook_server_main.py endpoints - TaskQueueHandler, health, and agent registration.

Tests cover:
- TaskQueueHandler CRUD operations (create, list, get, update, delete)
- Health endpoint
- Agent registration endpoint
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from httpx import ASGITransport, AsyncClient


class TestTaskQueueHandler:
    """Test class for TaskQueueHandler CRUD operations."""

    @pytest.fixture
    def mock_state_store(self):
        """Create a mock state store with Redis client."""
        store = Mock()
        store.is_connected = Mock(return_value=True)
        store.get_store_type = Mock(return_value="redis")
        store._redis = Mock()
        store._redis._client = Mock()
        return store

    @pytest.fixture
    def task_handler(self, mock_state_store):
        """Create a TaskQueueHandler instance with mocked state store."""
        from forge_harness.webhook_server.handlers.task_handler import TaskQueueHandler

        return TaskQueueHandler(state_store=mock_state_store)

    @pytest.fixture
    def sample_task(self):
        """Create a sample task."""
        return {
            "id": "task-001",
            "subject": "Test task",
            "description": "A test task description",
            "priority": "high",
            "status": "pending",
            "required_role": "backend-engineer",
            "claimed_by": "",
            "order": 0,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }

    @pytest.mark.asyncio
    async def test_list_tasks_empty(self, task_handler):
        """Test listing tasks when empty."""
        task_handler.state_store._redis._client.smembers = Mock(return_value=set())

        result = await task_handler.list_tasks()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_tasks_with_filter(self, task_handler, sample_task):
        """Test listing tasks with status filter."""
        task_handler.state_store._redis._client.smembers = Mock(return_value={b"task-001"})
        task_handler.state_store._redis._client.hgetall = Mock(
            return_value={
                "id": "task-001",
                "subject": "Test task",
                "description": "A test task description",
                "priority": "high",
                "status": "pending",
                "required_role": "backend-engineer",
                "claimed_by": "",
                "order": "0",
                "created_at": sample_task["created_at"],
                "updated_at": sample_task["updated_at"],
            }
        )

        result = await task_handler.list_tasks(status="pending")
        assert len(result) == 1
        assert result[0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_list_tasks_with_priority_filter(self, task_handler, sample_task):
        """Test listing tasks with priority filter."""
        task_handler.state_store._redis._client.smembers = Mock(return_value={b"task-001"})
        task_handler.state_store._redis._client.hgetall = Mock(
            return_value={
                "id": "task-001",
                "subject": "Test task",
                "description": "A test task description",
                "priority": "high",
                "status": "pending",
                "required_role": "",
                "claimed_by": "",
                "order": "0",
                "created_at": sample_task["created_at"],
                "updated_at": sample_task["updated_at"],
            }
        )

        result = await task_handler.list_tasks(priority="high")
        assert len(result) == 1
        assert result[0]["priority"] == "high"

    @pytest.mark.asyncio
    async def test_get_task_found(self, task_handler, sample_task):
        """Test getting an existing task."""
        task_handler.state_store._redis._client.hgetall = Mock(
            return_value={
                "id": "task-001",
                "subject": "Test task",
                "description": "A test task description",
                "priority": "high",
                "status": "pending",
                "required_role": "",
                "claimed_by": "",
                "order": "0",
                "created_at": sample_task["created_at"],
                "updated_at": sample_task["updated_at"],
            }
        )

        result = await task_handler.get_task("task-001")
        assert result is not None
        assert result["id"] == "task-001"
        assert result["subject"] == "Test task"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, task_handler):
        """Test getting a non-existent task."""
        task_handler.state_store._redis._client.hgetall = Mock(return_value={})

        result = await task_handler.get_task("non-existent")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_task(self, task_handler):
        """Test creating a new task."""
        task_handler.state_store._redis._client.hset = Mock()
        task_handler.state_store._redis._client.sadd = Mock()
        task_handler.state_store._redis._client.expire = Mock()
        task_handler.state_store._redis._client.hgetall = Mock(
            return_value={
                "id": "mock-id",
                "subject": "New task",
                "description": "Task description",
                "priority": "medium",
                "status": "pending",
                "required_role": "",
                "claimed_by": "",
                "order": "0",
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

        result = await task_handler.create_task(
            subject="New task",
            description="Task description",
            priority="medium",
        )

        assert result is not None
        assert result["subject"] == "New task"
        assert result["priority"] == "medium"

    @pytest.mark.asyncio
    async def test_create_task_with_required_role(self, task_handler):
        """Test creating a task with required role."""
        task_handler.state_store._redis._client.hset = Mock()
        task_handler.state_store._redis._client.sadd = Mock()
        task_handler.state_store._redis._client.expire = Mock()
        task_handler.state_store._redis._client.hgetall = Mock(
            return_value={
                "id": "mock-id",
                "subject": "Backend task",
                "description": "Backend work",
                "priority": "high",
                "status": "pending",
                "required_role": "backend-engineer",
                "claimed_by": "",
                "order": "0",
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

        result = await task_handler.create_task(
            subject="Backend task",
            description="Backend work",
            priority="high",
            required_role="backend-engineer",
        )

        assert result is not None
        assert result["required_role"] == "backend-engineer"

    @pytest.mark.asyncio
    async def test_update_task_found(self, task_handler, sample_task):
        """Test updating an existing task."""
        task_handler.state_store._redis._client.hgetall = Mock(
            side_effect=[
                {
                    "id": "task-001",
                    "subject": "Original subject",
                    "description": "Original description",
                    "priority": "medium",
                    "status": "pending",
                    "required_role": "",
                    "claimed_by": "",
                    "order": "0",
                    "created_at": sample_task["created_at"],
                    "updated_at": sample_task["created_at"],
                },
                {},  # Second call for update
            ]
        )
        task_handler.state_store._redis._client.hset = Mock()

        result = await task_handler.update_task("task-001", {"subject": "Updated subject"})
        assert result is not None
        assert result["subject"] == "Updated subject"

    @pytest.mark.asyncio
    async def test_update_task_not_found(self, task_handler):
        """Test updating a non-existent task."""
        task_handler.state_store._redis._client.hgetall = Mock(return_value={})

        result = await task_handler.update_task("non-existent", {"subject": "Updated"})
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_task_success(self, task_handler):
        """Test deleting an existing task."""
        task_handler.state_store._redis._client.srem = Mock()
        task_handler.state_store._redis._client.delete = Mock()

        result = await task_handler.delete_task("task-001")
        assert result is True
        task_handler.state_store._redis._client.srem.assert_called_once()
        task_handler.state_store._redis._client.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_task_not_found(self, task_handler):
        """Test deleting a non-existent task (Redis behavior returns True)."""
        task_handler.state_store._redis._client.srem = Mock()
        task_handler.state_store._redis._client.delete = Mock()

        result = await task_handler.delete_task("non-existent")
        # Redis operations succeed even if key doesn't exist
        assert result is True

    @pytest.mark.asyncio
    async def test_claim_task(self, task_handler, sample_task):
        """Test claiming a task for an agent."""
        task_handler.state_store._redis._client.hgetall = Mock(
            side_effect=[
                {
                    "id": "task-001",
                    "subject": "Test task",
                    "description": "Description",
                    "priority": "high",
                    "status": "pending",
                    "required_role": "",
                    "claimed_by": "",
                    "order": "0",
                    "created_at": sample_task["created_at"],
                    "updated_at": sample_task["created_at"],
                },
                {},  # Second call for update
            ]
        )
        task_handler.state_store._redis._client.hset = Mock()

        result = await task_handler.claim_task("task-001", "agent-001")
        assert result is not None
        assert result["claimed_by"] == "agent-001"
        assert result["status"] == "assigned"

    @pytest.mark.asyncio
    async def test_get_stats(self, task_handler):
        """Test getting task statistics."""
        task_handler.state_store._redis._client.smembers = Mock(
            return_value={
                b"task-001",
                b"task-002",
                b"task-003",
            }
        )
        task_handler.state_store._redis._client.hgetall = Mock(
            side_effect=[
                {
                    "id": "task-001",
                    "subject": "Task 1",
                    "description": "Desc",
                    "priority": "high",
                    "status": "pending",
                    "required_role": "",
                    "claimed_by": "",
                    "order": "0",
                    "created_at": datetime.now(UTC).isoformat(),
                    "updated_at": datetime.now(UTC).isoformat(),
                },
                {
                    "id": "task-002",
                    "subject": "Task 2",
                    "description": "Desc",
                    "priority": "medium",
                    "status": "in_progress",
                    "required_role": "",
                    "claimed_by": "",
                    "order": "0",
                    "created_at": datetime.now(UTC).isoformat(),
                    "updated_at": datetime.now(UTC).isoformat(),
                },
                {
                    "id": "task-003",
                    "subject": "Task 3",
                    "description": "Desc",
                    "priority": "low",
                    "status": "completed",
                    "required_role": "",
                    "claimed_by": "",
                    "order": "0",
                    "created_at": datetime.now(UTC).isoformat(),
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            ]
        )

        result = await task_handler.get_stats()
        assert result["total"] == 3
        assert result["pending"] == 1
        assert result["in_progress"] == 1
        assert result["completed"] == 1


class TestTaskQueueHandlerJSONFallback:
    """Test TaskQueueHandler JSON fallback when Redis is unavailable."""

    @pytest.fixture
    def task_handler_no_redis(self, tmp_path):
        """Create a TaskQueueHandler with no Redis connection."""
        from forge_harness.task_queue import TaskQueue
        from forge_harness.webhook_server.handlers.task_handler import TaskQueueHandler

        # Create a TaskQueue with the tmp_path
        task_queue = TaskQueue(queue_dir=tmp_path / ".forge/tasks")

        handler = TaskQueueHandler(state_store=None, task_queue=task_queue)
        # Mock _get_redis_client to return None so it uses TaskQueue
        handler._get_redis_client = Mock(return_value=None)
        return handler

    @pytest.mark.asyncio
    async def test_list_tasks_json_fallback(self, task_handler_no_redis):
        """Test listing tasks using TaskQueue file-based storage."""
        from forge_harness.task_queue import Task, TaskPriority, TaskStatus

        # Add a test task directly via TaskQueue
        task = Task(
            id="task-001",
            title="Task 1",
            description="Desc",
            priority=TaskPriority.HIGH,
            status=TaskStatus.PENDING,
        )
        await task_handler_no_redis._task_queue.add(task)

        result = await task_handler_no_redis.list_tasks()
        assert len(result) == 1
        assert result[0]["id"] == "task-001"

    @pytest.mark.asyncio
    async def test_create_task_json_fallback(self, task_handler_no_redis, tmp_path):
        """Test creating task using TaskQueue file-based storage."""
        result = await task_handler_no_redis.create_task(
            subject="New task",
            description="Description",
            priority="medium",
        )

        assert result is not None
        assert result["subject"] == "New task"
        # Current implementation uses 8-char truncated UUID
        assert len(result["id"]) == 8

    @pytest.mark.asyncio
    async def test_delete_task_json_fallback(self, task_handler_no_redis, tmp_path):
        """Test deleting task using TaskQueue file-based storage."""
        from forge_harness.task_queue import Task, TaskPriority, TaskStatus

        # Add a test task first
        task = Task(
            id="task-001",
            title="Task 1",
            description="Desc",
            priority=TaskPriority.HIGH,
            status=TaskStatus.PENDING,
        )
        await task_handler_no_redis._task_queue.add(task)

        result = await task_handler_no_redis.delete_task("task-001")
        assert result is True
        # Verify task was deleted
        assert await task_handler_no_redis._task_queue.get("task-001") is None


class TestHealthEndpoint:
    """Test class for health endpoint."""

    @pytest.mark.asyncio
    async def test_health_check_returns_fields(self):
        """Test basic health check returns expected fields."""
        from forge_harness.webhook_server.api.health import health_check

        result = await health_check()

        assert "status" in result
        assert result["status"] in ["ok", "degraded"]
        assert "service" in result
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_api_version(self):
        """Test API version endpoint."""
        from forge_harness.webhook_server.api.version import api_version

        result = await api_version()

        assert "version" in result
        assert "name" in result
        assert "api_prefix" in result
        assert result["api_prefix"] == "/api"


class TestAgentRegistrationEndpoint:
    """Test class for agent registration endpoint."""

    @pytest.fixture
    def mock_agent_registry(self):
        """Create a mock agent registry."""
        registry = Mock()

        agent = Mock()
        agent.id = "agent-001"
        agent.role = "backend-engineer"
        agent.name = "Backend Agent"
        agent.domain = "test-domain"
        agent.project = "test-project"
        agent.task = "Test task"
        agent.parent_id = None
        agent.children = []
        agent.tmux_session = None
        agent.skills = ["python", "fastapi"]
        agent.status = "active"
        agent.progress = 0
        agent.last_activity = datetime.now(UTC)

        agent.to_dict = Mock(
            return_value={
                "id": "agent-001",
                "role": "backend-engineer",
                "name": "Backend Agent",
                "domain": "test-domain",
                "project": "test-project",
                "task": "Test task",
                "parent_id": None,
                "children": [],
                "tmux_session": None,
                "skills": ["python", "fastapi"],
                "status": "active",
                "progress": 0,
            }
        )

        registry.register = Mock(return_value=agent)
        registry.get = Mock(return_value=agent)
        registry.list_active = Mock(return_value=[agent])

        return registry

    @pytest.fixture
    def mock_state_store(self):
        """Create a mock state store."""
        store = Mock()
        store.is_connected = Mock(return_value=True)
        store.register_agent = Mock()
        return store

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = AsyncMock()
        bus.publish = AsyncMock()
        return bus

    @pytest.mark.asyncio
    async def test_register_agent_success(
        self, mock_agent_registry, mock_state_store, mock_event_bus
    ):
        """Test successful agent registration."""
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest, register_agent

        request = AgentRegisterRequest(
            role="backend-engineer",
            project="test-project",
            task="Implement feature",
            name="Backend Agent",
            domain="test-domain",
            skills=["python", "fastapi"],
        )

        mock_request = Mock()
        mock_request.headers = {}

        with (
            patch("forge_harness.webhook_server.api.agents.get_agent_registry") as mock_get_reg,
            patch("forge_harness.webhook_server.api.agents.get_state_store") as mock_get_store,
            patch("forge_harness.webhook_server.api.agents.get_event_bus") as mock_get_bus,
            patch("forge_harness.webhook_server.api.agents.get_audit_logger") as mock_audit,
        ):
            mock_get_reg.return_value = mock_agent_registry
            mock_get_store.return_value = mock_state_store
            mock_get_bus.return_value = mock_event_bus
            mock_audit.return_value = Mock()
            mock_audit.return_value.log_agent_registration = AsyncMock()

            result = await register_agent(request, mock_request)

            assert result.status_code == 200
            mock_agent_registry.register.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_agent_with_hierarchy(
        self, mock_agent_registry, mock_state_store, mock_event_bus
    ):
        """Test agent registration with parent_id for hierarchy."""
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest, register_agent

        request = AgentRegisterRequest(
            role="frontend-builder",
            project="test-project",
            task="Build UI",
            name="Frontend Agent",
            domain="test-domain",
            parent_id="agent-parent-001",
        )

        mock_request = Mock()
        mock_request.headers = {}

        with (
            patch("forge_harness.webhook_server.api.agents.get_agent_registry") as mock_get_reg,
            patch("forge_harness.webhook_server.api.agents.get_state_store") as mock_get_store,
            patch("forge_harness.webhook_server.api.agents.get_event_bus") as mock_get_bus,
            patch("forge_harness.webhook_server.api.agents.get_audit_logger") as mock_audit,
        ):
            mock_get_reg.return_value = mock_agent_registry
            mock_get_store.return_value = mock_state_store
            mock_get_bus.return_value = mock_event_bus
            mock_audit.return_value = Mock()
            mock_audit.return_value.log_agent_registration = AsyncMock()

            result = await register_agent(request, mock_request)

            assert result.status_code == 200
            call_kwargs = mock_agent_registry.register.call_args[1]
            assert call_kwargs["parent_id"] == "agent-parent-001"

    def test_register_agent_validates_role(self):
        """Test agent registration validates non-empty role."""
        from pydantic import ValidationError

        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        with pytest.raises(ValidationError):
            AgentRegisterRequest(
                role="",  # Empty role should fail
                project="test-project",
                task="Test task",
            )

    def test_register_agent_validates_project(self):
        """Test agent registration validates non-empty project."""
        from pydantic import ValidationError

        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        with pytest.raises(ValidationError):
            AgentRegisterRequest(
                role="backend-engineer",
                project="",  # Empty project should fail
                task="Test task",
            )

    def test_register_agent_validates_task(self):
        """Test agent registration validates non-empty task."""
        from pydantic import ValidationError

        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        with pytest.raises(ValidationError):
            AgentRegisterRequest(
                role="backend-engineer",
                project="test-project",
                task="",  # Empty task should fail
            )


class TestAgentModels:
    """Test Pydantic models for agent API."""

    def test_agent_register_request(self):
        """Test AgentRegisterRequest model."""
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        request = AgentRegisterRequest(
            role="backend-engineer",
            project="test-project",
            task="Implement API",
            name="Backend Agent",
            domain="test-domain",
            parent_id=None,
            tmux_session=None,
            skills=["python", "fastapi"],
        )

        assert request.role == "backend-engineer"
        assert request.project == "test-project"
        assert request.task == "Implement API"
        assert request.name == "Backend Agent"
        assert request.skills == ["python", "fastapi"]

    def test_agent_register_request_defaults(self):
        """Test AgentRegisterRequest default values."""
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        request = AgentRegisterRequest(
            role="backend-engineer",
            project="test-project",
            task="Test task",
        )

        assert request.name is None
        assert request.domain is None
        assert request.parent_id is None
        assert request.tmux_session is None
        assert request.skills is None

    def test_agent_progress_request(self):
        """Test AgentProgressRequest model."""
        from forge_harness.webhook_server.api.agents import AgentProgressRequest

        request = AgentProgressRequest(
            progress=50,
            current_task="Writing tests",
            files_modified=["test_file.py"],
        )

        assert request.progress == 50
        assert request.current_task == "Writing tests"
        assert "test_file.py" in request.files_modified

    def test_agent_progress_request_validation(self):
        """Test AgentProgressRequest progress validation."""
        from pydantic import ValidationError

        from forge_harness.webhook_server.api.agents import AgentProgressRequest

        # Valid progress
        request = AgentProgressRequest(progress=0)
        assert request.progress == 0

        request = AgentProgressRequest(progress=100)
        assert request.progress == 100

        # Invalid progress
        with pytest.raises(ValidationError):
            AgentProgressRequest(progress=-1)

        with pytest.raises(ValidationError):
            AgentProgressRequest(progress=101)


class TestApiResponseHelper:
    """Test API response helper function."""

    def test_api_response_success(self):
        """Test successful API response."""
        from forge_harness.webhook_server.api.agents import api_response

        result = api_response(data={"key": "value"})

        assert result["success"] is True
        assert result["data"] == {"key": "value"}
        assert result["error"] is None
        assert "timestamp" in result

    def test_api_response_error(self):
        """Test error API response."""
        from forge_harness.webhook_server.api.agents import api_response

        result = api_response(error_code="VALIDATION_ERROR", error_message="Invalid input")

        assert result["success"] is False
        assert result["data"] is None
        assert result["error"]["code"] == "VALIDATION_ERROR"
        assert result["error"]["message"] == "Invalid input"


class TestNormalizeAgentDict:
    """Test agent dict normalization function."""

    def test_normalize_agent_dict_basic(self):
        """Test basic agent dict normalization."""
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        agent_dict = {
            "id": "agent-001",
            "role": "backend-engineer",
            "name": "Backend Agent",
            "domain": "test-domain",
            "project": "test-project",
            "task": "Implement feature",
            "parent_id": None,
            "children": [],
            "tmux_session": None,
            "skills": ["python"],
            "status": "active",
            "progress": 50,
        }

        result = normalize_agent_dict(agent_dict)

        assert result["session_id"] == "agent-001"
        assert result["agent_role"] == "backend-engineer"
        assert result["agent_name"] == "Backend Agent"
        assert result["domain"] == "test-domain"
        assert result["project"] == "test-project"
        assert result["status"] == "active"
        assert result["progress_pct"] == 50.0

    def test_normalize_agent_dict_fallback_id(self):
        """Test agent dict normalization with fallback ID."""
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        agent_dict = {
            "role": "backend-engineer",
            "name": "Backend Agent",
        }

        result = normalize_agent_dict(agent_dict)

        assert result["session_id"] == "agent-backend-engineer"
        assert result["agent_role"] == "backend-engineer"

    def test_normalize_agent_dict_handles_missing_fields(self):
        """Test agent dict normalization handles missing optional fields."""
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        agent_dict = {
            "id": "agent-001",
            "role": "test-agent",
        }

        result = normalize_agent_dict(agent_dict)

        assert result["session_id"] == "agent-001"
        assert result["agent_role"] == "test-agent"
        assert result["agent_name"] == "test-agent"  # Falls back to role
        assert result["domain"] is None
        assert result["project"] == ""
