"""Tests for Task Queue CRUD API endpoints."""

from unittest.mock import Mock

import pytest

from forge_harness.task_queue import TaskQueue
from forge_harness.webhook_server.handlers.task_handler import TaskQueueHandler


@pytest.fixture
def task_handler(tmp_path):
    """Create a TaskQueueHandler instance with file-based TaskQueue."""
    mock_store = Mock()
    mock_store.is_connected = Mock(return_value=False)
    mock_store.get_store_type = Mock(return_value="sqlite")
    queue = TaskQueue(tmp_path / ".forge/tasks")
    return TaskQueueHandler(state_store=mock_store, task_queue=queue)


@pytest.mark.asyncio
async def test_create_task(task_handler):
    """Test creating a new task."""
    task = await task_handler.create_task(
        subject="Test Task",
        description="This is a test task",
        priority="high",
        required_role="builder",
    )

    assert task["id"] is not None
    assert task["subject"] == "Test Task"
    assert task["description"] == "This is a test task"
    assert task["priority"] == "high"
    assert task["status"] == "pending"
    assert task["required_role"] == "builder"
    assert task["claimed_by"] == ""


@pytest.mark.asyncio
async def test_list_tasks(task_handler):
    """Test listing tasks with filters."""
    # Create some test tasks
    await task_handler.create_task(
        subject="Task 1",
        description="Description 1",
        priority="high",
    )
    await task_handler.create_task(
        subject="Task 2",
        description="Description 2",
        priority="medium",
    )

    # List all tasks
    tasks = await task_handler.list_tasks()
    assert len(tasks) >= 2

    # List by priority
    high_tasks = await task_handler.list_tasks(priority="high")
    assert all(t["priority"] == "high" for t in high_tasks)


@pytest.mark.asyncio
async def test_get_task(task_handler):
    """Test getting a specific task."""
    created = await task_handler.create_task(
        subject="Get Test",
        description="Test get task",
    )

    task = await task_handler.get_task(created["id"])
    assert task is not None
    assert task["id"] == created["id"]
    assert task["subject"] == "Get Test"


@pytest.mark.asyncio
async def test_update_task(task_handler):
    """Test updating a task."""
    created = await task_handler.create_task(
        subject="Update Test",
        description="Before update",
    )

    # Use skip_validation=True for direct status update testing
    # (valid path would be: PENDING -> QUEUED -> ASSIGNED -> IN_PROGRESS)
    updated = await task_handler.update_task(
        created["id"],
        {"description": "After update", "status": "in_progress"},
        skip_validation=True,
    )

    assert updated is not None
    assert updated["description"] == "After update"
    assert updated["status"] == "in_progress"


@pytest.mark.asyncio
async def test_delete_task(task_handler):
    """Test deleting a task."""
    created = await task_handler.create_task(
        subject="Delete Test",
        description="Will be deleted",
    )

    # Delete the task
    success = await task_handler.delete_task(created["id"])
    assert success is True

    # Verify it's gone
    task = await task_handler.get_task(created["id"])
    assert task is None


@pytest.mark.asyncio
async def test_claim_task(task_handler):
    """Test claiming a task for an agent."""
    created = await task_handler.create_task(
        subject="Claim Test",
        description="Will be claimed",
    )

    claimed = await task_handler.claim_task(created["id"], "agent-123")
    assert claimed is not None
    assert claimed["claimed_by"] == "agent-123"
    assert claimed["status"] == "assigned"


@pytest.mark.asyncio
async def test_get_stats(task_handler):
    """Test getting task queue statistics."""
    # Create tasks with different statuses
    task1 = await task_handler.create_task(
        subject="Stats Test 1",
        description="Pending task",
    )
    task2 = await task_handler.create_task(
        subject="Stats Test 2",
        description="In progress task",
    )
    # Use skip_validation=True for testing (valid path: PENDING -> QUEUED -> ASSIGNED -> IN_PROGRESS)
    await task_handler.update_task(task2["id"], {"status": "in_progress"}, skip_validation=True)

    stats = await task_handler.get_stats()
    assert stats["total"] >= 2
    assert stats["pending"] >= 1
    assert stats["in_progress"] >= 1


@pytest.mark.asyncio
async def test_task_filters(task_handler):
    """Test filtering tasks by status and priority."""
    # Create tasks with different combinations
    await task_handler.create_task(
        subject="High Priority Pending",
        description="Test",
        priority="high",
    )
    task2 = await task_handler.create_task(
        subject="Medium Priority",
        description="Test",
        priority="medium",
    )
    # Use skip_validation=True for testing (valid path: PENDING -> QUEUED -> ... -> COMPLETED)
    await task_handler.update_task(task2["id"], {"status": "completed"}, skip_validation=True)

    # Filter by status
    pending = await task_handler.list_tasks(status="pending")
    assert all(t["status"] == "pending" for t in pending)

    # Filter by priority
    high = await task_handler.list_tasks(priority="high")
    assert all(t["priority"] == "high" for t in high)

    # Filter by both
    high_pending = await task_handler.list_tasks(status="pending", priority="high")
    assert all(t["status"] == "pending" and t["priority"] == "high" for t in high_pending)
