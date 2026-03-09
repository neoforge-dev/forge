"""
TaskQueueHandler Unit Tests
===========================

Tests for TaskQueueHandler CRUD operations with TaskQueue file-based backend.
These tests verify the core business logic without requiring Redis.
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from forge_harness.task_queue import TaskQueue
from forge_harness.webhook_server.handlers.task_handler import TaskQueueHandler


class TestTaskQueueHandlerCRUD:
    """Test class for TaskQueueHandler CRUD operations."""

    @pytest.fixture
    def mock_state_store(self):
        """Create a mock state store that returns None for Redis client."""
        store = Mock()
        store.is_connected = Mock(return_value=False)
        store.get_store_type = Mock(return_value="sqlite")
        return store

    @pytest.fixture
    def task_handler(self, mock_state_store, tmp_path):
        """Create TaskQueueHandler with mocked dependencies."""
        queue = TaskQueue(tmp_path / ".forge/tasks")
        handler = TaskQueueHandler(state_store=mock_state_store, task_queue=queue)
        return handler

    @pytest.mark.asyncio
    async def test_create_task(self, task_handler):
        """Test creating a new task."""
        result = await task_handler.create_task(
            subject="Test Task",
            description="Test Description",
            priority="high",
            required_role="backend-engineer",
        )

        assert result is not None
        assert "id" in result
        assert result["subject"] == "Test Task"
        assert result["description"] == "Test Description"
        assert result["priority"] == "high"
        assert result["status"] == "pending"
        assert result["required_role"] == "backend-engineer"
        assert "created_at" in result

    @pytest.mark.asyncio
    async def test_create_task_with_lease(self, task_handler):
        """Test creating a task with lease metadata."""
        lease = {
            "owner_node": "nova",
            "owner_agent": "forge:codex",
            "lease_expires_at": "2026-02-21T12:00:00Z",
            "path_lock": "forge-terminal#FT-123",
        }
        result = await task_handler.create_task(
            subject="Leased Task",
            description="Task with ownership metadata",
            priority="high",
            lease=lease,
        )

        assert result is not None
        assert result["lease"] == lease

        retrieved = await task_handler.get_task(result["id"])
        assert retrieved is not None
        assert retrieved["lease"] == lease

    @pytest.mark.asyncio
    async def test_get_task(self, task_handler):
        """Test getting a specific task."""
        # Create a task first
        created = await task_handler.create_task(
            subject="Get Test", description="Test get task", priority="medium"
        )
        task_id = created["id"]

        # Retrieve the task
        result = await task_handler.get_task(task_id)

        assert result is not None
        assert result["id"] == task_id
        assert result["subject"] == "Get Test"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, task_handler):
        """Test getting a non-existent task returns None."""
        result = await task_handler.get_task("non-existent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_tasks(self, task_handler):
        """Test listing all tasks."""
        # Create multiple tasks
        await task_handler.create_task("Task 1", "Desc 1", "high")
        await task_handler.create_task("Task 2", "Desc 2", "medium")
        await task_handler.create_task("Task 3", "Desc 3", "low")

        result = await task_handler.list_tasks()

        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_list_tasks_filter_by_status(self, task_handler):
        """Test listing tasks filtered by status."""
        # Create tasks with different statuses
        task1 = await task_handler.create_task("Task 1", "Desc 1", "high")
        task2 = await task_handler.create_task("Task 2", "Desc 2", "medium")

        # Update one task to in_progress
        await task_handler.update_task(task1["id"], {"status": "in_progress"})

        # Filter by pending
        pending = await task_handler.list_tasks(status="pending")
        assert len(pending) == 1
        assert pending[0]["id"] == task2["id"]

        # Filter by in_progress
        in_progress = await task_handler.list_tasks(status="in_progress")
        assert len(in_progress) == 1
        assert in_progress[0]["id"] == task1["id"]

    @pytest.mark.asyncio
    async def test_list_tasks_filter_by_priority(self, task_handler):
        """Test listing tasks filtered by priority."""
        await task_handler.create_task("High Task", "Desc", "high")
        await task_handler.create_task("Medium Task", "Desc", "medium")
        await task_handler.create_task("Another High", "Desc", "high")

        high_priority = await task_handler.list_tasks(priority="high")
        assert len(high_priority) == 2

    @pytest.mark.asyncio
    async def test_update_task(self, task_handler):
        """Test updating a task."""
        # Create a task
        created = await task_handler.create_task("Original", "Original description", "low")
        task_id = created["id"]

        # Update the task
        updated = await task_handler.update_task(
            task_id, {"subject": "Updated", "priority": "high"}
        )

        assert updated is not None
        assert updated["subject"] == "Updated"
        assert updated["priority"] == "high"
        # Original fields should remain
        assert updated["description"] == "Original description"

    @pytest.mark.asyncio
    async def test_update_task_can_set_lease_on_existing_task(self, task_handler):
        """Test setting lease metadata on a task created without lease."""
        created = await task_handler.create_task("Original", "Original description", "medium")
        lease = {
            "owner_node": "atlas",
            "owner_agent": "forge:tech",
            "lease_expires_at": "2026-02-21T13:00:00Z",
            "path_lock": "harness#CP-2001",
        }

        updated = await task_handler.update_task(created["id"], {"lease": lease})
        assert updated is not None
        assert updated["lease"] == lease

        retrieved = await task_handler.get_task(created["id"])
        assert retrieved is not None
        assert retrieved["lease"] == lease

    @pytest.mark.asyncio
    async def test_update_task_not_found(self, task_handler):
        """Test updating a non-existent task returns None."""
        result = await task_handler.update_task("non-existent", {"subject": "New Subject"})
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_task(self, task_handler):
        """Test deleting a task."""
        # Create a task
        created = await task_handler.create_task("To Delete", "Desc", "high")
        task_id = created["id"]

        # Verify it exists
        task = await task_handler.get_task(task_id)
        assert task is not None

        # Delete the task
        result = await task_handler.delete_task(task_id)
        assert result is True

        # Verify it's gone
        task = await task_handler.get_task(task_id)
        assert task is None

    @pytest.mark.asyncio
    async def test_delete_task_not_found(self, task_handler):
        """Test deleting a non-existent task returns False."""
        result = await task_handler.delete_task("non-existent-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_claim_task(self, task_handler):
        """Test claiming a task for an agent."""
        # Create a task
        created = await task_handler.create_task("Claim Me", "Desc", "high")
        task_id = created["id"]

        # Claim the task
        claimed = await task_handler.claim_task(task_id, "agent-001")

        assert claimed is not None
        assert claimed["status"] == "assigned"
        assert claimed["claimed_by"] == "agent-001"

    @pytest.mark.asyncio
    async def test_claim_task_with_lease(self, task_handler):
        """Test lease claim sets lease metadata and ownership."""
        created = await task_handler.create_task("Lease Claim", "Desc", "high")
        lease = {
            "owner_node": "nova",
            "owner_agent": "forge:codex",
            "lease_expires_at": "2099-01-01T00:00:00Z",
            "path_lock": "forge-terminal#FT-123",
        }

        claimed = await task_handler.claim_task_with_lease(created["id"], lease)
        assert claimed is not None
        assert claimed["status"] == "assigned"
        assert claimed["claimed_by"] == "forge:codex"
        assert claimed["lease"] == lease

    @pytest.mark.asyncio
    async def test_claim_task_with_lease_rejects_path_lock_conflict(self, task_handler):
        """Lease claim should fail when another active task holds same path_lock."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        task_a = await task_handler.create_task("Task A", "Desc", "high")
        task_b = await task_handler.create_task("Task B", "Desc", "high")

        lease_a = {
            "owner_node": "nova",
            "owner_agent": "forge:codex",
            "lease_expires_at": "2099-01-01T00:00:00Z",
            "path_lock": "shared-lock",
        }
        await task_handler.claim_task_with_lease(task_a["id"], lease_a)

        lease_b = {
            "owner_node": "atlas",
            "owner_agent": "forge:tech",
            "lease_expires_at": "2099-01-01T00:00:00Z",
            "path_lock": "shared-lock",
        }
        with pytest.raises(TaskLeaseError) as exc_info:
            await task_handler.claim_task_with_lease(task_b["id"], lease_b)

        assert exc_info.value.code == "PATH_LOCK_CONFLICT"

    @pytest.mark.asyncio
    async def test_renew_task_lease_requires_same_owner(self, task_handler):
        """Lease renew should reject owner changes."""
        from forge_harness.webhook_server.handlers.task_handler import TaskLeaseError

        created = await task_handler.create_task("Renew Test", "Desc", "high")
        await task_handler.claim_task_with_lease(
            created["id"],
            {
                "owner_node": "nova",
                "owner_agent": "forge:codex",
                "lease_expires_at": "2099-01-01T00:00:00Z",
                "path_lock": "renew-lock",
            },
        )

        with pytest.raises(TaskLeaseError) as exc_info:
            await task_handler.renew_task_lease(
                created["id"],
                {
                    "owner_node": "nova",
                    "owner_agent": "forge:other",
                    "lease_expires_at": "2099-01-02T00:00:00Z",
                    "path_lock": "renew-lock",
                },
            )

        assert exc_info.value.code == "LEASE_OWNER_MISMATCH"

    @pytest.mark.asyncio
    async def test_release_task_lease_clears_ownership(self, task_handler):
        """Release clears lease metadata and agent ownership."""
        created = await task_handler.create_task("Release Test", "Desc", "high")
        await task_handler.claim_task_with_lease(
            created["id"],
            {
                "owner_node": "nova",
                "owner_agent": "forge:codex",
                "lease_expires_at": "2099-01-01T00:00:00Z",
                "path_lock": "release-lock",
            },
        )

        released = await task_handler.release_task_lease(
            created["id"], owner_node="nova", owner_agent="forge:codex"
        )
        assert released is not None
        assert released["lease"] is None
        assert released["claimed_by"] == ""
        assert released["status"] == "pending"

    @pytest.mark.asyncio
    async def test_requeue_task_resets_task_state(self, task_handler):
        """Requeue should set task back to pending and clear ownership fields."""
        created = await task_handler.create_task("Requeue Test", "Desc", "high")
        await task_handler.update_task(
            created["id"],
            {
                "status": "in_progress",
                "claimed_by": "forge:codex",
                "lease": {
                    "owner_node": "nova",
                    "owner_agent": "forge:codex",
                    "lease_expires_at": "2099-01-01T00:00:00Z",
                    "path_lock": "requeue-lock",
                },
            },
        )

        requeued = await task_handler.requeue_task(created["id"])
        assert requeued is not None
        assert requeued["status"] == "pending"
        assert requeued["claimed_by"] == ""
        assert requeued["lease"] is None

    @pytest.mark.asyncio
    async def test_get_stats(self, task_handler):
        """Test getting task statistics."""
        # Create tasks with different statuses
        await task_handler.create_task("Task 1", "Desc", "high")
        await task_handler.create_task("Task 2", "Desc", "medium")

        task3 = await task_handler.create_task("Task 3", "Desc", "low")
        await task_handler.update_task(task3["id"], {"status": "completed"})

        stats = await task_handler.get_stats()

        assert stats["total"] == 3
        assert stats["pending"] == 2
        assert stats["completed"] == 1


class TestTaskPersistence:
    """Test class for task persistence across handler instances."""

    @pytest.mark.asyncio
    async def test_tasks_persist_across_handler_instances(self, tmp_path):
        """Test that tasks persist when creating new handler instances."""
        queue_dir = tmp_path / ".forge/tasks"
        mock_store = Mock()
        mock_store.is_connected = Mock(return_value=False)
        mock_store.get_store_type = Mock(return_value="sqlite")

        # Create first handler and add a task
        queue1 = TaskQueue(queue_dir)
        handler1 = TaskQueueHandler(state_store=mock_store, task_queue=queue1)

        task = await handler1.create_task("Persistent Task", "Desc", "high")
        task_id = task["id"]

        # Create a new handler instance (simulates server restart)
        queue2 = TaskQueue(queue_dir)
        handler2 = TaskQueueHandler(state_store=mock_store, task_queue=queue2)

        # Verify task is still there
        retrieved = await handler2.get_task(task_id)

        assert retrieved is not None
        assert retrieved["subject"] == "Persistent Task"


class TestTaskSerialization:
    """Test class for task serialization/deserialization (Redis path)."""

    @pytest.fixture
    def mock_state_store(self):
        """Create a mock state store."""
        store = Mock()
        store.is_connected = Mock(return_value=False)
        store.get_store_type = Mock(return_value="sqlite")
        return store

    @pytest.fixture
    def task_handler(self, mock_state_store, tmp_path):
        """Create TaskQueueHandler."""
        queue = TaskQueue(tmp_path / ".forge/tasks")
        return TaskQueueHandler(state_store=mock_state_store, task_queue=queue)

    def test_serialize_task(self, task_handler):
        """Test task serialization for Redis storage."""
        task = {
            "id": "test-123",
            "subject": "Test Subject",
            "description": "Test Description",
            "priority": "high",
            "status": "pending",
            "required_role": "backend",
            "claimed_by": "",
            "order": 0,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }

        serialized = task_handler._serialize_task(task)

        assert serialized["id"] == "test-123"
        assert serialized["subject"] == "Test Subject"
        assert serialized["priority"] == "high"
        assert serialized["status"] == "pending"
        assert serialized["required_role"] == "backend"
        assert serialized["claimed_by"] == ""
        assert serialized["order"] == "0"

    def test_deserialize_task(self, task_handler):
        """Test task deserialization from Redis storage."""
        data = {
            "id": "test-456",
            "subject": "Deserialized Subject",
            "description": "Desc",
            "priority": "low",
            "status": "completed",
            "required_role": "frontend",
            "claimed_by": "agent-99",
            "order": "5",
            "created_at": "2026-01-02T00:00:00Z",
            "updated_at": "2026-01-03T00:00:00Z",
        }

        deserialized = task_handler._deserialize_task(data)

        assert deserialized["id"] == "test-456"
        assert deserialized["subject"] == "Deserialized Subject"
        assert deserialized["priority"] == "low"
        assert deserialized["status"] == "completed"
        assert deserialized["required_role"] == "frontend"
        assert deserialized["claimed_by"] == "agent-99"
        assert deserialized["order"] == 5

    def test_deserialize_task_empty_strings_to_none(self, task_handler):
        """Test that empty strings are converted to None for optional fields."""
        data = {
            "id": "test-789",
            "subject": "Subject",
            "description": "Desc",
            "priority": "medium",
            "status": "pending",
            "required_role": "",
            "claimed_by": "",
            "order": "0",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }

        deserialized = task_handler._deserialize_task(data)

        assert deserialized["required_role"] is None
        assert deserialized["claimed_by"] is None

    def test_serialize_task_with_lease(self, task_handler):
        """Test lease is JSON-encoded when serializing task data."""
        lease = {
            "owner_node": "nova",
            "owner_agent": "forge:codex",
            "lease_expires_at": "2026-02-21T12:00:00Z",
            "path_lock": "forge-terminal#FT-123",
        }
        task = {
            "id": "test-lease-1",
            "subject": "Lease Subject",
            "description": "Desc",
            "priority": "high",
            "status": "pending",
            "required_role": "",
            "claimed_by": "",
            "lease": lease,
            "order": 0,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }

        serialized = task_handler._serialize_task(task)
        assert serialized["lease"] == json.dumps(lease)

    def test_deserialize_task_with_lease_json(self, task_handler):
        """Test lease is JSON-decoded when reading task data."""
        lease = {
            "owner_node": "atlas",
            "owner_agent": "forge:tech",
            "lease_expires_at": "2026-02-21T13:00:00Z",
            "path_lock": "harness#CP-2001",
        }
        data = {
            "id": "test-lease-2",
            "subject": "Lease Subject",
            "description": "Desc",
            "priority": "low",
            "status": "pending",
            "required_role": "",
            "claimed_by": "",
            "lease": json.dumps(lease),
            "order": "0",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }

        deserialized = task_handler._deserialize_task(data)
        assert deserialized["lease"] == lease

    def test_deserialize_task_with_invalid_lease_json(self, task_handler):
        """Test invalid lease payload gracefully degrades to None."""
        data = {
            "id": "test-lease-3",
            "subject": "Lease Subject",
            "description": "Desc",
            "priority": "low",
            "status": "pending",
            "required_role": "",
            "claimed_by": "",
            "lease": "{not-json",
            "order": "0",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }

        deserialized = task_handler._deserialize_task(data)
        assert deserialized["lease"] is None


class TestTaskQueueIntegration:
    """Integration tests verifying TaskQueue backend works correctly."""

    @pytest.fixture
    def task_handler(self, tmp_path):
        """Create handler with real TaskQueue backend."""
        mock_store = Mock()
        mock_store.is_connected = Mock(return_value=False)
        mock_store.get_store_type = Mock(return_value="sqlite")
        queue = TaskQueue(tmp_path / ".forge/tasks")
        return TaskQueueHandler(state_store=mock_store, task_queue=queue)

    @pytest.mark.asyncio
    async def test_create_and_list_with_file_backend(self, task_handler):
        """Test that tasks created via API are stored in file-based TaskQueue."""
        await task_handler.create_task("File Task 1", "Desc 1", "high")
        await task_handler.create_task("File Task 2", "Desc 2", "medium")

        tasks = await task_handler.list_tasks()
        assert len(tasks) == 2
        subjects = {t["subject"] for t in tasks}
        assert "File Task 1" in subjects
        assert "File Task 2" in subjects

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, task_handler):
        """Test full task lifecycle: create -> claim -> update -> complete."""
        # Create
        task = await task_handler.create_task("Lifecycle Test", "Full lifecycle", "high")
        assert task["status"] == "pending"

        # Claim
        claimed = await task_handler.claim_task(task["id"], "agent-42")
        assert claimed["status"] == "assigned"
        assert claimed["claimed_by"] == "agent-42"

        # Update to in_progress
        updated = await task_handler.update_task(task["id"], {"status": "in_progress"})
        assert updated["status"] == "in_progress"

        # Complete
        completed = await task_handler.update_task(task["id"], {"status": "completed"})
        assert completed["status"] == "completed"

        # Stats reflect the lifecycle
        stats = await task_handler.get_stats()
        assert stats["completed"] == 1
        assert stats["total"] == 1

    @pytest.mark.asyncio
    async def test_domain_and_project_fields(self, task_handler):
        """Test that domain and project fields are preserved."""
        task = await task_handler.create_task("Domain Task", "Desc", "medium")
        task_id = task["id"]

        # Update with domain and project
        updated = await task_handler.update_task(
            task_id, {"domain": "codeswiftr-com", "project": "voice-coach"}
        )
        assert updated["domain"] == "codeswiftr-com"
        assert updated["project"] == "voice-coach"

        # Verify persistence
        retrieved = await task_handler.get_task(task_id)
        assert retrieved["domain"] == "codeswiftr-com"
        assert retrieved["project"] == "voice-coach"

    @pytest.mark.asyncio
    async def test_critical_priority(self, task_handler):
        """Test critical priority tasks."""
        task = await task_handler.create_task("Critical Bug", "Production down", "critical")
        assert task["priority"] == "critical"

        # Filter by critical
        critical_tasks = await task_handler.list_tasks(priority="critical")
        assert len(critical_tasks) == 1
        assert critical_tasks[0]["subject"] == "Critical Bug"

    @pytest.mark.asyncio
    async def test_list_limit(self, task_handler):
        """Test that list_tasks respects the limit parameter."""
        for i in range(10):
            await task_handler.create_task(f"Task {i}", f"Desc {i}", "medium")

        limited = await task_handler.list_tasks(limit=3)
        assert len(limited) == 3

        all_tasks = await task_handler.list_tasks(limit=50)
        assert len(all_tasks) == 10
