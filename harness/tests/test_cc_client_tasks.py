"""
CommandCenterClient Tasks Tests

Tests for task-related methods in CommandCenterClient.
Uses AsyncMock for _request to avoid HTTP calls.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.command_center_client import (
    APIResponse,
    CommandCenterClient,
    Task,
    TaskPriority,
    TaskStatus,
)


@pytest.fixture
def mock_client():
    """Create a client with mocked _request method."""
    client = CommandCenterClient(base_url="http://test.local")
    client._request = AsyncMock()
    return client


@pytest.fixture
def sample_task_dict():
    """Sample task data from API."""
    return {
        "id": "task-123",
        "subject": "Test Task",
        "description": "Test description",
        "status": "pending",
        "priority": "medium",
        "required_role": None,
        "claimed_by": None,
        "created_at": "2026-02-10T12:00:00Z",
        "updated_at": "2026-02-10T12:00:00Z",
        "order": 0,
        "domain": "codeswiftr-com",
        "project": "interview-simulator",
        "task_type": "api_simple",
        "complexity": "moderate",
        "blocked_by": None,
        "estimated_hours": 2.0,
    }


# =============================================================================
# list_tasks
# =============================================================================


class TestListTasks:
    """Tests for list_tasks method."""

    @pytest.mark.asyncio
    async def test_list_tasks_default(self, mock_client, sample_task_dict):
        """Test list_tasks with default parameters."""
        mock_client._request.return_value = APIResponse(
            success=True,
            data={"tasks": [sample_task_dict], "count": 1},
        )

        result = await mock_client.list_tasks()

        mock_client._request.assert_called_once_with("GET", "/api/tasks", params={"limit": 50})
        assert len(result) == 1
        assert result[0]["id"] == "task-123"

    @pytest.mark.asyncio
    async def test_list_tasks_with_filters(self, mock_client, sample_task_dict):
        """Test list_tasks with status and priority filters."""
        mock_client._request.return_value = APIResponse(
            success=True,
            data={"tasks": [sample_task_dict], "count": 1},
        )

        result = await mock_client.list_tasks(status="pending", priority="high", limit=10)

        mock_client._request.assert_called_once_with(
            "GET", "/api/tasks", params={"status": "pending", "priority": "high", "limit": 10}
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_list_tasks_empty(self, mock_client):
        """Test list_tasks returns empty list on failure."""
        mock_client._request.return_value = APIResponse(success=False, error="API unavailable")

        result = await mock_client.list_tasks()

        assert result == []

    @pytest.mark.asyncio
    async def test_list_tasks_array_response(self, mock_client, sample_task_dict):
        """Test list_tasks handles array response (not wrapped dict)."""
        mock_client._request.return_value = APIResponse(
            success=True,
            data=[sample_task_dict],
        )

        result = await mock_client.list_tasks()

        assert len(result) == 1
        assert result[0]["id"] == "task-123"


# =============================================================================
# create_task
# =============================================================================


class TestCreateTask:
    """Tests for create_task method."""

    @pytest.mark.asyncio
    async def test_create_task_success(self, mock_client, sample_task_dict):
        """Test create_task with required fields."""
        mock_client._request.return_value = APIResponse(success=True, data=sample_task_dict)

        result = await mock_client.create_task(subject="New Task", description="Task description")

        mock_client._request.assert_called_once_with(
            "POST",
            "/api/tasks",
            data={
                "subject": "New Task",
                "description": "Task description",
                "priority": "medium",
            },
        )
        assert result is not None
        assert result["id"] == "task-123"

    @pytest.mark.asyncio
    async def test_create_task_with_all_fields(self, mock_client, sample_task_dict):
        """Test create_task with all optional fields."""
        mock_client._request.return_value = APIResponse(success=True, data=sample_task_dict)

        result = await mock_client.create_task(
            subject="Complex Task",
            description="Full description",
            priority="high",
            required_role="tech-agent",
        )

        call_data = mock_client._request.call_args.kwargs["data"]
        assert call_data["subject"] == "Complex Task"
        assert call_data["priority"] == "high"
        assert call_data["required_role"] == "tech-agent"
        assert result is not None

    @pytest.mark.asyncio
    async def test_create_task_failure(self, mock_client):
        """Test create_task returns None on failure."""
        mock_client._request.return_value = APIResponse(success=False, error="Invalid input")

        result = await mock_client.create_task(subject="Bad Task")

        assert result is None


# =============================================================================
# get_task
# =============================================================================


class TestGetTask:
    """Tests for get_task method."""

    @pytest.mark.asyncio
    async def test_get_task_success(self, mock_client, sample_task_dict):
        """Test get_task returns task data."""
        mock_client._request.return_value = APIResponse(success=True, data=sample_task_dict)

        result = await mock_client.get_task("task-123")

        mock_client._request.assert_called_once_with("GET", "/api/tasks/task-123")
        assert result is not None
        assert result["id"] == "task-123"
        assert result["subject"] == "Test Task"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, mock_client):
        """Test get_task returns None for 404."""
        mock_client._request.return_value = APIResponse(success=False, error="Task not found")

        result = await mock_client.get_task("nonexistent")

        assert result is None


# =============================================================================
# claim_task
# =============================================================================


class TestClaimTask:
    """Tests for claim_task method."""

    @pytest.mark.asyncio
    async def test_claim_task_success(self, mock_client, sample_task_dict):
        """Test claim_task claims task for agent."""
        claimed_task = sample_task_dict.copy()
        claimed_task["claimed_by"] = "agent-001"
        claimed_task["status"] = "assigned"

        mock_client._request.return_value = APIResponse(success=True, data=claimed_task)

        result = await mock_client.claim_task("task-123", "agent-001")

        mock_client._request.assert_called_once_with(
            "POST", "/api/tasks/task-123/claim", data={"agent_id": "agent-001"}
        )
        assert result is not None
        assert result["claimed_by"] == "agent-001"

    @pytest.mark.asyncio
    async def test_claim_task_failure(self, mock_client):
        """Test claim_task returns None on failure."""
        mock_client._request.return_value = APIResponse(success=False, error="Task not found")

        result = await mock_client.claim_task("task-123", "agent-001")

        assert result is None


# =============================================================================
# dispatch_task
# =============================================================================


class TestDispatchTask:
    """Tests for dispatch_task method."""

    @pytest.mark.asyncio
    async def test_dispatch_task_success(self, mock_client, sample_task_dict):
        """Test dispatch_task dispatches task to agent."""
        dispatched_task = sample_task_dict.copy()
        dispatched_task["claimed_by"] = "agent-001"
        dispatched_task["status"] = "assigned"

        mock_client._request.return_value = APIResponse(success=True, data=dispatched_task)

        result = await mock_client.dispatch_task("task-123", "agent-001")

        mock_client._request.assert_called_once_with(
            "POST", "/api/tasks/task-123/dispatch", data={"agent_id": "agent-001"}
        )
        assert result is not None
        assert result["claimed_by"] == "agent-001"

    @pytest.mark.asyncio
    async def test_dispatch_task_failure(self, mock_client):
        """Test dispatch_task returns None on failure."""
        mock_client._request.return_value = APIResponse(success=False, error="Agent not available")

        result = await mock_client.dispatch_task("task-123", "agent-001")

        assert result is None


# =============================================================================
# get_recommended_tasks
# =============================================================================


class TestGetRecommendedTasks:
    """Tests for get_recommended_tasks method."""

    @pytest.mark.asyncio
    async def test_get_recommended_tasks_default(self, mock_client, sample_task_dict):
        """Test get_recommended_tasks with defaults."""
        mock_client._request.return_value = APIResponse(
            success=True,
            data={"tasks": [sample_task_dict], "count": 1},
        )

        result = await mock_client.get_recommended_tasks()

        mock_client._request.assert_called_once_with(
            "GET", "/api/tasks/recommended", params={"limit": 10}
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_recommended_tasks_with_filters(self, mock_client, sample_task_dict):
        """Test get_recommended_tasks with filters."""
        mock_client._request.return_value = APIResponse(
            success=True,
            data={"tasks": [sample_task_dict], "count": 1},
        )

        result = await mock_client.get_recommended_tasks(
            limit=5,
            agent_id="agent-001",
            include_blocked=True,
            min_priority="high",
            task_type="api_simple",
            domain="codeswiftr-com",
        )

        params = mock_client._request.call_args.kwargs["params"]
        assert params["limit"] == 5
        assert params["agent_id"] == "agent-001"
        assert params["include_blocked"] == "true"
        assert params["min_priority"] == "high"
        assert params["task_type"] == "api_simple"
        assert params["domain"] == "codeswiftr-com"
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_recommended_tasks_array_response(self, mock_client, sample_task_dict):
        """Test get_recommended_tasks handles array response."""
        mock_client._request.return_value = APIResponse(
            success=True,
            data=[sample_task_dict],
        )

        result = await mock_client.get_recommended_tasks()

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_recommended_tasks_empty(self, mock_client):
        """Test get_recommended_tasks returns empty list on failure."""
        mock_client._request.return_value = APIResponse(success=False, error="Service unavailable")

        result = await mock_client.get_recommended_tasks()

        assert result == []


# =============================================================================
# update_task
# =============================================================================


class TestUpdateTask:
    """Tests for update_task method."""

    @pytest.mark.asyncio
    async def test_update_task_partial(self, mock_client, sample_task_dict):
        """Test update_task with partial updates."""
        updated_task = sample_task_dict.copy()
        updated_task["priority"] = "high"

        mock_client._request.return_value = APIResponse(success=True, data=updated_task)

        result = await mock_client.update_task("task-123", priority="high")

        mock_client._request.assert_called_once_with(
            "PUT", "/api/tasks/task-123", data={"priority": "high"}
        )
        assert result is not None
        assert result["priority"] == "high"

    @pytest.mark.asyncio
    async def test_update_task_multiple_fields(self, mock_client, sample_task_dict):
        """Test update_task with multiple fields."""
        updated_task = sample_task_dict.copy()
        updated_task["status"] = "in_progress"
        updated_task["priority"] = "high"

        mock_client._request.return_value = APIResponse(success=True, data=updated_task)

        result = await mock_client.update_task("task-123", status="in_progress", priority="high")

        call_data = mock_client._request.call_args.kwargs["data"]
        assert call_data["status"] == "in_progress"
        assert call_data["priority"] == "high"
        assert result is not None

    @pytest.mark.asyncio
    async def test_update_task_no_changes(self, mock_client):
        """Test update_task with no changes returns None."""
        mock_client._request.return_value = APIResponse(success=False, error="Task not found")

        result = await mock_client.update_task("task-123")

        assert result is None


# =============================================================================
# delete_task
# =============================================================================


class TestDeleteTask:
    """Tests for delete_task method."""

    @pytest.mark.asyncio
    async def test_delete_task_success(self, mock_client):
        """Test delete_task returns True on success."""
        mock_client._request.return_value = APIResponse(success=True)

        result = await mock_client.delete_task("task-123")

        mock_client._request.assert_called_once_with("DELETE", "/api/tasks/task-123")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_task_failure(self, mock_client):
        """Test delete_task returns False on failure."""
        mock_client._request.return_value = APIResponse(success=False, error="Task not found")

        result = await mock_client.delete_task("task-123")

        assert result is False


# =============================================================================
# get_task_stats
# =============================================================================


class TestGetTaskStats:
    """Tests for get_task_stats method."""

    @pytest.mark.asyncio
    async def test_get_task_stats_success(self, mock_client):
        """Test get_task_stats returns statistics."""
        mock_client._request.return_value = APIResponse(
            success=True,
            data={
                "total": 100,
                "pending": 20,
                "in_progress": 5,
                "completed": 70,
                "failed": 5,
            },
        )

        result = await mock_client.get_task_stats()

        mock_client._request.assert_called_once_with("GET", "/api/tasks/stats")
        assert result["total"] == 100
        assert result["pending"] == 20

    @pytest.mark.asyncio
    async def test_get_task_stats_failure(self, mock_client):
        """Test get_task_stats returns empty dict on failure."""
        mock_client._request.return_value = APIResponse(success=False, error="Service unavailable")

        result = await mock_client.get_task_stats()

        assert result == {}


# =============================================================================
# reorder_tasks
# =============================================================================


class TestReorderTasks:
    """Tests for reorder_tasks method."""

    @pytest.mark.asyncio
    async def test_reorder_tasks_success(self, mock_client, sample_task_dict):
        """Test reorder_tasks reorders tasks."""
        task1 = sample_task_dict.copy()
        task1["id"] = "task-1"
        task2 = sample_task_dict.copy()
        task2["id"] = "task-2"

        mock_client._request.return_value = APIResponse(
            success=True,
            data={"updated": [task1, task2], "count": 2},
        )

        result = await mock_client.reorder_tasks(["task-1", "task-2"])

        mock_client._request.assert_called_once_with(
            "POST", "/api/tasks/reorder", data={"task_ids": ["task-1", "task-2"]}
        )
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_reorder_tasks_failure(self, mock_client):
        """Test reorder_tasks returns empty list on failure."""
        mock_client._request.return_value = APIResponse(success=False, error="Invalid task IDs")

        result = await mock_client.reorder_tasks(["task-1", "task-2"])

        assert result == []


# =============================================================================
# Sync Wrappers
# =============================================================================


class TestSyncWrappers:
    """Tests for sync wrapper methods."""

    def test_sync_list_tasks(self, mock_client, sample_task_dict):
        """Test sync_list_tasks calls async method."""

        async def mock_list_tasks(*args, **kwargs):
            return [sample_task_dict]

        mock_client.list_tasks = mock_list_tasks

        result = mock_client.sync_list_tasks()

        assert len(result) == 1
        assert result[0]["id"] == "task-123"

    def test_sync_create_task(self, mock_client, sample_task_dict):
        async def mock_create_task(*args, **kwargs):
            return sample_task_dict

        mock_client.create_task = mock_create_task

        result = mock_client.sync_create_task("New Task", "Description")

        assert result is not None
        assert result["id"] == "task-123"

    def test_sync_get_task(self, mock_client, sample_task_dict):
        async def mock_get_task(*args, **kwargs):
            return sample_task_dict

        mock_client.get_task = mock_get_task

        result = mock_client.sync_get_task("task-123")

        assert result is not None
        assert result["id"] == "task-123"

    def test_sync_claim_task(self, mock_client, sample_task_dict):
        claimed_task = sample_task_dict.copy()
        claimed_task["claimed_by"] = "agent-001"

        async def mock_claim_task(*args, **kwargs):
            return claimed_task

        mock_client.claim_task = mock_claim_task

        result = mock_client.sync_claim_task("task-123", "agent-001")

        assert result is not None
        assert result["claimed_by"] == "agent-001"

    def test_sync_dispatch_task(self, mock_client, sample_task_dict):
        dispatched_task = sample_task_dict.copy()
        dispatched_task["claimed_by"] = "agent-001"

        async def mock_dispatch_task(*args, **kwargs):
            return dispatched_task

        mock_client.dispatch_task = mock_dispatch_task

        result = mock_client.sync_dispatch_task("task-123", "agent-001")

        assert result is not None
        assert result["claimed_by"] == "agent-001"

    def test_sync_get_recommended_tasks(self, mock_client, sample_task_dict):
        async def mock_get_recommended_tasks(*args, **kwargs):
            return [sample_task_dict]

        mock_client.get_recommended_tasks = mock_get_recommended_tasks

        result = mock_client.sync_get_recommended_tasks()

        assert len(result) == 1

    def test_sync_update_task(self, mock_client, sample_task_dict):
        updated_task = sample_task_dict.copy()
        updated_task["priority"] = "high"

        async def mock_update_task(*args, **kwargs):
            return updated_task

        mock_client.update_task = mock_update_task

        result = mock_client.sync_update_task("task-123", priority="high")

        assert result is not None
        assert result["priority"] == "high"

    def test_sync_delete_task(self, mock_client):
        async def mock_delete_task(*args, **kwargs):
            return True

        mock_client.delete_task = mock_delete_task

        result = mock_client.sync_delete_task("task-123")

        assert result is True

    def test_sync_get_task_stats(self, mock_client):
        async def mock_get_task_stats(*args, **kwargs):
            return {"total": 100}

        mock_client.get_task_stats = mock_get_task_stats

        result = mock_client.sync_get_task_stats()

        assert result["total"] == 100

    def test_sync_reorder_tasks(self, mock_client, sample_task_dict):
        async def mock_reorder_tasks(*args, **kwargs):
            return [sample_task_dict]

        mock_client.reorder_tasks = mock_reorder_tasks

        result = mock_client.sync_reorder_tasks(["task-1"])

        assert len(result) == 1


# =============================================================================
# Task Data Model
# =============================================================================


class TestTaskDataclass:
    """Tests for Task dataclass and from_dict method."""

    def test_task_from_dict_full(self, sample_task_dict):
        """Test Task.from_dict with all fields."""
        task = Task.from_dict(sample_task_dict)

        assert task.id == "task-123"
        assert task.subject == "Test Task"
        assert task.description == "Test description"
        assert task.status == TaskStatus.PENDING
        assert task.priority == TaskPriority.MEDIUM
        assert task.domain == "codeswiftr-com"
        assert task.project == "interview-simulator"
        assert task.task_type == "api_simple"
        assert task.complexity == "moderate"
        assert task.estimated_hours == 2.0

    def test_task_from_dict_minimal(self):
        """Test Task.from_dict with minimal fields."""
        minimal_dict = {
            "id": "task-456",
            "subject": "Minimal Task",
            "description": "Minimal desc",
            "status": "pending",
            "priority": "low",
        }

        task = Task.from_dict(minimal_dict)

        assert task.id == "task-456"
        assert task.subject == "Minimal Task"
        assert task.status == TaskStatus.PENDING
        assert task.priority == TaskPriority.LOW
        assert task.required_role is None
        assert task.claimed_by is None
        assert task.order == 0

    def test_task_status_enum(self):
        """Test TaskStatus enum values."""
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.IN_PROGRESS == "in_progress"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.CANCELLED == "cancelled"
        assert TaskStatus.ASSIGNED == "assigned"
        assert TaskStatus.BLOCKED == "blocked"

    def test_task_priority_enum(self):
        """Test TaskPriority enum values."""
        assert TaskPriority.CRITICAL == "critical"
        assert TaskPriority.HIGH == "high"
        assert TaskPriority.MEDIUM == "medium"
        assert TaskPriority.LOW == "low"
