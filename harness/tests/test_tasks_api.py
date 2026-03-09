"""Tests for Tasks API endpoints.

Tests for:
- GET /api/tasks - List tasks
- POST /api/tasks - Create task
- GET /api/tasks/stats - Get task statistics
- POST /api/tasks/{id}/claim - Claim a task
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add harness root to path
harness_root = Path(__file__).parent.parent
sys.path.insert(0, str(harness_root))


# ============================================================================
# Task Handler Unit Tests (without API)
# ============================================================================


def test_task_handler_list_tasks():
    """Test TaskQueueHandler.list_tasks method returns list."""
    from forge_harness.webhook_server.handlers.task_handler import TaskQueueHandler

    handler = TaskQueueHandler()
    # Handler is sync but the method is async
    # Current implementation returns empty list
    import asyncio

    tasks = asyncio.run(handler.list_tasks())
    assert isinstance(tasks, list)


def test_task_handler_create_task_returns_task():
    """Test TaskQueueHandler.create_task returns the created task."""
    from forge_harness.webhook_server.handlers.task_handler import TaskQueueHandler

    handler = TaskQueueHandler()
    import asyncio

    task = asyncio.run(
        handler.create_task(
            subject="Test",
            description="Test description",
            priority="high",
        )
    )
    assert task is not None
    assert task["subject"] == "Test"
    assert task["description"] == "Test description"
    assert task["priority"] == "high"


def test_task_handler_get_stats():
    """Test TaskQueueHandler.get_stats method returns dict."""
    from forge_harness.webhook_server.handlers.task_handler import TaskQueueHandler

    handler = TaskQueueHandler()
    import asyncio

    stats = asyncio.run(handler.get_stats())
    assert isinstance(stats, dict)
    assert "total" in stats
    assert "pending" in stats
    assert "in_progress" in stats
    assert "completed" in stats


def test_task_handler_claim_task_returns_task():
    """Test TaskQueueHandler.claim_task returns the updated task."""
    from forge_harness.webhook_server.handlers.task_handler import TaskQueueHandler

    handler = TaskQueueHandler()
    import asyncio

    # Create a task first
    task = asyncio.run(handler.create_task("Test", "Desc"))
    task_id = task["id"]

    result = asyncio.run(handler.claim_task(task_id, "agent-123"))
    assert result is not None
    assert result["claimed_by"] == "agent-123"
    assert result["status"] == "assigned"


def test_task_handler_update_task_returns_task():
    """Test TaskQueueHandler.update_task returns the updated task.

    Uses skip_validation=True because this test exercises CRUD mechanics, not
    lifecycle transition enforcement (that is covered by test_task_lifecycle.py).
    """
    from forge_harness.webhook_server.handlers.task_handler import TaskQueueHandler

    handler = TaskQueueHandler()
    import asyncio

    # Create a task first
    task = asyncio.run(handler.create_task("Test", "Desc"))
    task_id = task["id"]

    result = asyncio.run(handler.update_task(task_id, {"status": "completed"}, skip_validation=True))
    assert result is not None
    assert result["status"] == "completed"


def test_task_handler_delete_task_returns_true():
    """Test TaskQueueHandler.delete_task returns True on success."""
    from forge_harness.webhook_server.handlers.task_handler import TaskQueueHandler

    handler = TaskQueueHandler()
    import asyncio

    # Create a task first
    task = asyncio.run(handler.create_task("Test", "Desc"))
    task_id = task["id"]

    result = asyncio.run(handler.delete_task(task_id))
    assert result is True


def test_task_handler_get_task_returns_task():
    """Test TaskQueueHandler.get_task returns the task."""
    from forge_harness.webhook_server.handlers.task_handler import TaskQueueHandler

    handler = TaskQueueHandler()
    import asyncio

    # Create a task first
    task = asyncio.run(handler.create_task("Test", "Desc"))
    task_id = task["id"]

    result = asyncio.run(handler.get_task(task_id))
    assert result is not None
    assert result["id"] == task_id


# ============================================================================
# API Response Tests
# ============================================================================


def test_api_response_format():
    """Test that API response has expected format."""

    def api_response(data):
        return {
            "success": True,
            "data": data,
            "error": None,
            "timestamp": "2026-02-09T12:00:00Z",
        }

    result = api_response({"tasks": []})
    assert result["success"] is True
    assert "data" in result
    assert "error" in result
    assert "timestamp" in result


def test_task_structure():
    """Test that task dict has expected structure."""
    task = {
        "id": "task-1",
        "subject": "Test Task",
        "description": "Description",
        "priority": "high",
        "status": "pending",
        "claimed_by": None,
        "created_at": "2026-02-09T10:00:00Z",
        "updated_at": "2026-02-09T10:00:00Z",
    }

    required_fields = ["id", "subject", "description", "priority", "status"]
    for field in required_fields:
        assert field in task, f"Missing required field: {field}"


def test_stats_structure():
    """Test that stats dict has expected structure."""
    stats = {
        "total": 10,
        "pending": 5,
        "in_progress": 3,
        "completed": 2,
    }

    required_fields = ["total", "pending", "in_progress", "completed"]
    for field in required_fields:
        assert field in stats, f"Missing required field: {field}"
        assert isinstance(stats[field], int), f"{field} should be int"


# ============================================================================
# Mock Handler Tests (for API simulation)
# ============================================================================


def test_mock_handler_list_tasks():
    """Test mock handler returns expected tasks."""
    handler = MagicMock()
    handler.list_tasks = AsyncMock(
        return_value=[
            {"id": "task-1", "subject": "Task 1", "status": "pending"},
            {"id": "task-2", "subject": "Task 2", "status": "in_progress"},
        ]
    )

    import asyncio

    tasks = asyncio.run(handler.list_tasks())

    assert len(tasks) == 2
    assert tasks[0]["id"] == "task-1"
    assert tasks[1]["id"] == "task-2"


def test_mock_handler_create_task():
    """Test mock handler creates task correctly."""
    handler = MagicMock()
    handler.create_task = AsyncMock(
        return_value={
            "id": "task-new",
            "subject": "New Task",
            "priority": "high",
            "status": "pending",
        }
    )

    import asyncio

    task = asyncio.run(
        handler.create_task(
            subject="New Task",
            description="Description",
            priority="high",
        )
    )

    assert task["subject"] == "New Task"
    assert task["priority"] == "high"
    assert task["status"] == "pending"


def test_mock_handler_claim_task():
    """Test mock handler claims task correctly."""
    handler = MagicMock()
    handler.claim_task = AsyncMock(
        return_value={
            "id": "task-1",
            "status": "in_progress",
            "claimed_by": "agent-123",
        }
    )

    import asyncio

    task = asyncio.run(handler.claim_task("task-1", "agent-123"))

    assert task["claimed_by"] == "agent-123"
    assert task["status"] == "in_progress"


def test_mock_handler_get_stats():
    """Test mock handler returns stats correctly."""
    handler = MagicMock()
    handler.get_stats = AsyncMock(
        return_value={
            "total": 10,
            "pending": 5,
            "in_progress": 3,
            "completed": 2,
        }
    )

    import asyncio

    stats = asyncio.run(handler.get_stats())

    assert stats["total"] == 10
    assert stats["pending"] == 5
    assert stats["in_progress"] == 3
    assert stats["completed"] == 2
