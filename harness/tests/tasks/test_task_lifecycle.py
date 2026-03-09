"""Tests for Task Lifecycle."""

import tempfile
from pathlib import Path

import pytest

from forge_harness.task_manager.database import TaskDatabase, get_task_db


@pytest.fixture
def temp_db(tmp_path):
    """Create temporary database."""
    db = TaskDatabase(db_path=tmp_path / "test.db")
    yield db
    if db.db_path.exists():
        db.db_path.unlink()


def test_task_create(temp_db):
    """Test creating a new task."""
    task_id = temp_db.create_task(
        title="Test Task", description="Test Description", priority="high"
    )

    assert task_id.startswith("TC-")

    task = temp_db.get_task_status(task_id)
    assert task is not None
    assert task["title"] == "Test Task"
    assert task["description"] == "Test Description"
    assert task["priority"] == "high"
    assert task["status"] == "pending"


def test_task_claim(temp_db):
    """Test claiming a pending task."""
    task_id = temp_db.create_task("Test Task", "Description")

    result = temp_db.claim_task(task_id, "kimi")

    assert result is True

    task = temp_db.get_task_status(task_id)
    assert task["status"] == "assigned"
    assert task["assigned_agent"] == "kimi"


def test_task_claim_already_claimed(temp_db):
    """Test claiming an already claimed task fails."""
    task_id = temp_db.create_task("Test Task", "Description")

    temp_db.claim_task(task_id, "kimi")
    result = temp_db.claim_task(task_id, "glm")

    assert result is False


def test_task_claim_nonexistent(temp_db):
    """Test claiming nonexistent task fails."""
    result = temp_db.claim_task("TC-NONEXISTENT", "kimi")

    assert result is False


def test_task_complete(temp_db):
    """Test completing a task."""
    task_id = temp_db.create_task("Test Task", "Description")
    temp_db.claim_task(task_id, "kimi")

    result = temp_db.complete_task(task_id, "kimi", "Task completed successfully")

    assert result is True

    task = temp_db.get_task_status(task_id)
    assert task["status"] == "completed"


def test_task_complete_wrong_agent(temp_db):
    """Test completing task with wrong agent fails."""
    task_id = temp_db.create_task("Test Task", "Description")
    temp_db.claim_task(task_id, "kimi")

    result = temp_db.complete_task(task_id, "glm", "Wrong agent")

    assert result is False


def test_task_complete_nonexistent(temp_db):
    """Test completing nonexistent task fails."""
    result = temp_db.complete_task("TC-NONEXISTENT", "kimi", "Result")

    assert result is False


def test_full_task_lifecycle(temp_db):
    """Test full task lifecycle: create -> claim -> complete."""
    task_id = temp_db.create_task(
        title="Full Lifecycle Test", description="Testing full flow", priority="high"
    )

    assert temp_db.get_task_status(task_id)["status"] == "pending"

    success = temp_db.claim_task(task_id, "kimi")
    assert success is True
    task = temp_db.get_task_status(task_id)
    assert task["status"] == "assigned"
    assert task["assigned_agent"] == "kimi"

    success = temp_db.complete_task(task_id, "kimi", "Done")
    assert success is True
    task = temp_db.get_task_status(task_id)
    assert task["status"] == "completed"


def test_get_pending_tasks(temp_db):
    """Test getting pending tasks ordered by priority."""
    temp_db.create_task("Low Priority", "Desc", "low")
    temp_db.create_task("High Priority", "Desc", "high")
    temp_db.create_task("Critical Priority", "Desc", "critical")
    temp_db.create_task("Medium Priority", "Desc", "medium")

    pending = temp_db.get_pending_tasks()

    assert len(pending) == 4
    assert pending[0]["priority"] == "critical"
    assert pending[1]["priority"] == "high"
    assert pending[2]["priority"] == "medium"
    assert pending[3]["priority"] == "low"


def test_get_agent_tasks(temp_db):
    """Test getting tasks assigned to an agent."""
    task1 = temp_db.create_task("Task 1", "Desc")
    task2 = temp_db.create_task("Task 2", "Desc")

    temp_db.claim_task(task1, "kimi")
    temp_db.claim_task(task2, "kimi")

    tasks = temp_db.get_agent_tasks("kimi")

    assert len(tasks) == 2


def test_task_with_metadata(temp_db):
    """Test creating task with metadata."""
    task_id = temp_db.create_task(
        title="Meta Task",
        description="With metadata",
        metadata={"source": "xnode", "correlation_id": "corr-001"},
    )

    task = temp_db.get_task_status(task_id)
    import json

    metadata = json.loads(task["metadata"])
    assert metadata["source"] == "xnode"
    assert metadata["correlation_id"] == "corr-001"


def test_task_attempt_count(temp_db):
    """Test attempt count increments on claim."""
    task_id = temp_db.create_task("Retry Task", "Description")

    temp_db.claim_task(task_id, "kimi")
    temp_db.complete_task(task_id, "kimi")

    task_id2 = temp_db.create_task("Retry Task 2", "Description")
    temp_db.claim_task(task_id2, "kimi")

    task = temp_db.get_task_status(task_id2)
    assert task["attempt_count"] == 1
