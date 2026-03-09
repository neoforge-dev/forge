"""Tests for StateAdapter.

Tests the unified interface for reading/writing tasks, agents, and nodes state.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest

from forge_harness.state_adapter import (
    AgentState,
    NodeState,
    StateAdapter,
    TaskState,
    get_state_adapter,
)

# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def temp_forge_root(tmp_path: Path) -> Path:
    """Create a temporary FORGE root with required directories."""
    (tmp_path / ".forge/tasks").mkdir(parents=True)
    (tmp_path / ".forge" / "heartbeat" / "nodes").mkdir(parents=True)
    (tmp_path / ".forge" / "agents").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def adapter(temp_forge_root: Path) -> StateAdapter:
    """Create a StateAdapter with temporary directories."""
    return StateAdapter(temp_forge_root)


@pytest.fixture
def sample_task_data() -> dict:
    """Sample task data."""
    return {
        "id": "test123",
        "subject": "Write tests for state adapter",
        "description": "Create comprehensive tests",
        "priority": "high",
        "status": "pending",
        "domain": "harness",
        "project": "forge-harness",
        "required_role": "developer",
        "order": 0,
        "created_at": "2026-02-22T12:00:00+00:00",
    }


@pytest.fixture
def sample_agent_data() -> dict:
    """Sample agent data."""
    return {
        "id": "forge:kimi",
        "role": "backend-engineer",
        "name": "Kimi",
        "domain": "harness",
        "project": "forge-harness",
        "task": "Write tests",
        "status": "active",
        "progress": 50,
        "tmux_session": "forge:kimi",
        "registered_at": "2026-02-22T12:00:00+00:00",
        "last_activity": "2026-02-22T12:30:00+00:00",
    }


@pytest.fixture
def sample_node_data() -> dict:
    """Sample node data."""
    return {
        "node_id": "nova",
        "name": "nova",
        "status": "online",
        "agent_count": 3,
        "cpu_load": 45.5,
        "ram_usage": 62.0,
        "disk_usage": 30.0,
        "load_average": 2.5,
        "last_heartbeat": "2026-02-22T12:30:00+00:00",
        "capabilities": {
            "claude": True,
            "docker": True,
            "kimi": True,
        },
        "specs": {
            "cpu": "Apple M3 Max",
            "ram_gb": 49,
            "cores": 16,
            "os": "Darwin",
        },
        "current_task": "",
        "received_at": "2026-02-22T12:30:00+00:00",
    }


# =============================================================================
# TaskState Tests
# =============================================================================

class TestTaskState:
    """Tests for TaskState dataclass."""

    def test_from_dict_with_standard_fields(self, sample_task_data: dict) -> None:
        """Test creating TaskState from standard dict."""
        task = TaskState.from_dict(sample_task_data)

        assert task.id == "test123"
        assert task.subject == "Write tests for state adapter"
        assert task.description == "Create comprehensive tests"
        assert task.priority == "high"
        assert task.status == "pending"
        assert task.domain == "harness"
        assert task.project == "forge-harness"
        assert task.required_role == "developer"
        assert task.order == 0

    def test_from_dict_handles_title_variation(self) -> None:
        """Test from_dict handles 'title' field (alternative to 'subject')."""
        data = {
            "id": "abc",
            "title": "My Task",
            "description": "Desc",
            "priority": "medium",
            "status": "pending",
        }
        task = TaskState.from_dict(data)

        assert task.subject == "My Task"

    def test_from_dict_handles_claimed_by_variation(self) -> None:
        """Test from_dict handles 'claimed_by' field (alternative to 'assigned_agent')."""
        data = {
            "id": "abc",
            "subject": "Task",
            "claimed_by": "forge:kimi",
            "status": "assigned",
        }
        task = TaskState.from_dict(data)

        assert task.assigned_agent == "forge:kimi"

    def test_to_dict_roundtrip(self, sample_task_data: dict) -> None:
        """Test to_dict creates valid dictionary."""
        task = TaskState.from_dict(sample_task_data)
        result = task.to_dict()

        assert result["id"] == "test123"
        assert result["subject"] == "Write tests for state adapter"
        assert result["priority"] == "high"
        assert result["status"] == "pending"


# =============================================================================
# AgentState Tests
# =============================================================================

class TestAgentState:
    """Tests for AgentState dataclass."""

    def test_from_dict_with_standard_fields(self, sample_agent_data: dict) -> None:
        """Test creating AgentState from standard dict."""
        agent = AgentState.from_dict(sample_agent_data)

        assert agent.id == "forge:kimi"
        assert agent.role == "backend-engineer"
        assert agent.name == "Kimi"
        assert agent.status == "active"
        assert agent.progress == 50
        assert agent.tmux_session == "forge:kimi"

    def test_from_dict_handles_agent_role_variation(self) -> None:
        """Test from_dict handles 'agent_role' field."""
        data = {
            "id": "forge:kimi",
            "agent_role": "backend-engineer",
            "status": "active",
        }
        agent = AgentState.from_dict(data)

        assert agent.role == "backend-engineer"

    def test_from_dict_handles_agent_name_variation(self) -> None:
        """Test from_dict handles 'agent_name' field."""
        data = {
            "id": "forge:kimi",
            "agent_name": "Kimi",
            "role": "developer",
        }
        agent = AgentState.from_dict(data)

        assert agent.name == "Kimi"

    def test_from_dict_handles_progress_pct_variation(self) -> None:
        """Test from_dict handles 'progress_pct' field."""
        data = {
            "id": "forge:kimi",
            "role": "developer",
            "progress_pct": 75,
        }
        agent = AgentState.from_dict(data)

        assert agent.progress == 75

    def test_from_dict_handles_focus_tags_variation(self) -> None:
        """Test from_dict handles 'focus_tags' field."""
        data = {
            "id": "forge:kimi",
            "role": "developer",
            "focus_tags": ["python", "fastapi"],
        }
        agent = AgentState.from_dict(data)

        assert agent.skills == ["python", "fastapi"]

    def test_to_dict_roundtrip(self, sample_agent_data: dict) -> None:
        """Test to_dict creates valid dictionary."""
        agent = AgentState.from_dict(sample_agent_data)
        result = agent.to_dict()

        assert result["id"] == "forge:kimi"
        assert result["role"] == "backend-engineer"
        assert result["name"] == "Kimi"
        assert result["status"] == "active"


# =============================================================================
# NodeState Tests
# =============================================================================

class TestNodeState:
    """Tests for NodeState dataclass."""

    def test_from_dict_with_standard_fields(self, sample_node_data: dict) -> None:
        """Test creating NodeState from standard dict."""
        node = NodeState.from_dict(sample_node_data)

        assert node.node_id == "nova"
        assert node.name == "nova"
        assert node.status == "online"
        assert node.agent_count == 3
        assert node.cpu_load == 45.5
        assert node.ram_usage == 62.0

    def test_from_dict_handles_cpu_percent_variation(self) -> None:
        """Test from_dict handles 'cpu_percent' field."""
        data = {
            "node_id": "test",
            "cpu_percent": 55.0,
            "status": "online",
        }
        node = NodeState.from_dict(data)

        assert node.cpu_load == 55.0

    def test_from_dict_handles_memory_percent_variation(self) -> None:
        """Test from_dict handles 'memory_percent' field."""
        data = {
            "node_id": "test",
            "memory_percent": 70.0,
            "status": "online",
        }
        node = NodeState.from_dict(data)

        assert node.ram_usage == 70.0

    def test_from_dict_defaults_missing_fields(self) -> None:
        """Test from_dict provides sensible defaults."""
        data = {"node_id": "minimal"}
        node = NodeState.from_dict(data)

        assert node.node_id == "minimal"
        assert node.name == "minimal"
        assert node.status == "offline"
        assert node.cpu_load == 0.0
        assert node.agent_count == 0

    def test_to_dict_roundtrip(self, sample_node_data: dict) -> None:
        """Test to_dict creates valid dictionary."""
        node = NodeState.from_dict(sample_node_data)
        result = node.to_dict()

        assert result["node_id"] == "nova"
        assert result["status"] == "online"
        assert result["cpu_load"] == 45.5


# =============================================================================
# StateAdapter Tests - Tasks
# =============================================================================

class TestStateAdapterTasks:
    """Tests for StateAdapter task operations."""

    @pytest.mark.asyncio
    async def test_create_task(self, adapter: StateAdapter) -> None:
        """Test creating a new task."""
        data = {
            "subject": "New feature",
            "description": "Implement something",
            "priority": "high",
        }
        task = await adapter.create_task(data)

        assert task.subject == "New feature"
        assert task.description == "Implement something"
        assert task.priority == "high"
        assert task.status == "pending"
        assert len(task.id) > 0

        # Verify file was created
        task_file = adapter._task_path(task.id)
        assert task_file.exists()

    @pytest.mark.asyncio
    async def test_get_task(self, adapter: StateAdapter) -> None:
        """Test getting a specific task."""
        # Create a task first
        data = {"subject": "Test task", "priority": "medium"}
        created = await adapter.create_task(data)

        # Get the task
        retrieved = await adapter.get_task(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.subject == "Test task"

    @pytest.mark.asyncio
    async def test_get_task_returns_none_for_missing(self, adapter: StateAdapter) -> None:
        """Test get_task returns None for non-existent task."""
        result = await adapter.get_task("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_tasks_empty(self, adapter: StateAdapter) -> None:
        """Test getting tasks when none exist."""
        tasks = await adapter.get_tasks()
        assert tasks == []

    @pytest.mark.asyncio
    async def test_get_tasks_returns_all(self, adapter: StateAdapter) -> None:
        """Test getting all tasks."""
        # Create multiple tasks
        await adapter.create_task({"subject": "Task A", "priority": "high"})
        await adapter.create_task({"subject": "Task B", "priority": "low"})
        await adapter.create_task({"subject": "Task C", "priority": "medium"})

        tasks = await adapter.get_tasks()

        assert len(tasks) == 3
        subjects = [t.subject for t in tasks]
        assert "Task A" in subjects
        assert "Task B" in subjects
        assert "Task C" in subjects

    @pytest.mark.asyncio
    async def test_get_tasks_filters_by_status(self, adapter: StateAdapter) -> None:
        """Test filtering tasks by status."""
        await adapter.create_task({"subject": "Pending task", "status": "pending"})
        await adapter.create_task({"subject": "Completed task", "status": "completed"})

        pending_tasks = await adapter.get_tasks(status="pending")
        completed_tasks = await adapter.get_tasks(status="completed")

        assert len(pending_tasks) == 1
        assert len(completed_tasks) == 1
        assert pending_tasks[0].subject == "Pending task"
        assert completed_tasks[0].subject == "Completed task"

    @pytest.mark.asyncio
    async def test_get_tasks_filters_by_priority(self, adapter: StateAdapter) -> None:
        """Test filtering tasks by priority."""
        await adapter.create_task({"subject": "High task", "priority": "high"})
        await adapter.create_task({"subject": "Low task", "priority": "low"})

        high_tasks = await adapter.get_tasks(priority="high")
        low_tasks = await adapter.get_tasks(priority="low")

        assert len(high_tasks) == 1
        assert len(low_tasks) == 1
        assert high_tasks[0].subject == "High task"
        assert low_tasks[0].subject == "Low task"

    @pytest.mark.asyncio
    async def test_get_tasks_filters_by_domain(self, adapter: StateAdapter) -> None:
        """Test filtering tasks by domain."""
        await adapter.create_task({"subject": "Harness task", "domain": "harness"})
        await adapter.create_task({"subject": "IS task", "domain": "interview-simulator"})

        harness_tasks = await adapter.get_tasks(domain="harness")
        is_tasks = await adapter.get_tasks(domain="interview-simulator")

        assert len(harness_tasks) == 1
        assert len(is_tasks) == 1

    @pytest.mark.asyncio
    async def test_get_tasks_respects_limit(self, adapter: StateAdapter) -> None:
        """Test get_tasks respects limit parameter."""
        await adapter.create_task({"subject": "Task 1"})
        await adapter.create_task({"subject": "Task 2"})
        await adapter.create_task({"subject": "Task 3"})

        tasks = await adapter.get_tasks(limit=2)

        assert len(tasks) == 2

    @pytest.mark.asyncio
    async def test_get_tasks_sorts_by_order_then_priority(self, adapter: StateAdapter) -> None:
        """Test tasks are sorted by order, then priority, then created_at."""
        now = datetime.now(UTC)
        await adapter.create_task({
            "subject": "Low priority",
            "priority": "low",
            "order": 0,
            "created_at": (now.replace(microsecond=100000).isoformat()),
        })
        await adapter.create_task({
            "subject": "High priority",
            "priority": "high",
            "order": 0,
            "created_at": (now.replace(microsecond=200000).isoformat()),
        })
        await adapter.create_task({
            "subject": "Second order",
            "priority": "medium",
            "order": 1,
            "created_at": (now.replace(microsecond=300000).isoformat()),
        })

        tasks = await adapter.get_tasks()

        # High priority should come first (same order, higher priority)
        assert tasks[0].subject == "High priority"
        # Low priority comes after high (same order, lower priority)
        assert tasks[1].subject == "Low priority"
        # Order 1 comes after order 0
        assert tasks[2].subject == "Second order"

    @pytest.mark.asyncio
    async def test_update_task(self, adapter: StateAdapter) -> None:
        """Test updating a task."""
        created = await adapter.create_task({
            "subject": "Original",
            "status": "pending",
            "progress": 0,
        })

        updated = await adapter.update_task(created.id, {
            "status": "in_progress",
            "progress": 50,
        })

        assert updated is not None
        assert updated.status == "in_progress"
        # Note: progress isn't a TaskState field, but update should still work

        # Verify persistence
        retrieved = await adapter.get_task(created.id)
        assert retrieved.status == "in_progress"

    @pytest.mark.asyncio
    async def test_update_task_returns_none_for_missing(self, adapter: StateAdapter) -> None:
        """Test update_task returns None for non-existent task."""
        result = await adapter.update_task("missing", {"status": "completed"})
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_task(self, adapter: StateAdapter) -> None:
        """Test deleting a task."""
        created = await adapter.create_task({"subject": "To delete"})

        # Verify it exists
        assert await adapter.get_task(created.id) is not None

        # Delete it
        result = await adapter.delete_task(created.id)
        assert result is True

        # Verify it's gone
        assert await adapter.get_task(created.id) is None

    @pytest.mark.asyncio
    async def test_delete_task_returns_false_for_missing(self, adapter: StateAdapter) -> None:
        """Test delete_task returns False for non-existent task."""
        result = await adapter.delete_task("missing")
        assert result is False


# =============================================================================
# StateAdapter Tests - Agents
# =============================================================================

class TestStateAdapterAgents:
    """Tests for StateAdapter agent operations."""

    @pytest.mark.asyncio
    async def test_update_agent_creates_new(self, adapter: StateAdapter) -> None:
        """Test update_agent creates a new agent if it doesn't exist."""
        agent = await adapter.update_agent("forge:kimi", {
            "role": "backend-engineer",
            "status": "active",
        })

        assert agent is not None
        assert agent.id == "forge:kimi"
        assert agent.role == "backend-engineer"
        assert agent.status == "active"

    @pytest.mark.asyncio
    async def test_update_agent_updates_existing(self, adapter: StateAdapter) -> None:
        """Test update_agent updates an existing agent."""
        # Create agent
        await adapter.update_agent("forge:kimi", {
            "role": "backend-engineer",
            "progress": 0,
        })

        # Update it
        updated = await adapter.update_agent("forge:kimi", {
            "progress": 75,
            "status": "idle",
        })

        assert updated is not None
        assert updated.progress == 75
        assert updated.status == "idle"

    @pytest.mark.asyncio
    async def test_get_agent(self, adapter: StateAdapter) -> None:
        """Test getting a specific agent."""
        # Create agent
        await adapter.update_agent("forge:kimi", {
            "role": "developer",
            "name": "Kimi",
        })

        # Get it
        agent = await adapter.get_agent("forge:kimi")

        assert agent is not None
        assert agent.id == "forge:kimi"
        assert agent.name == "Kimi"

    @pytest.mark.asyncio
    async def test_get_agent_returns_none_for_missing(self, adapter: StateAdapter) -> None:
        """Test get_agent returns None for non-existent agent."""
        result = await adapter.get_agent("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_agents_empty(self, adapter: StateAdapter) -> None:
        """Test getting agents when none exist."""
        agents = await adapter.get_agents()
        assert agents == []

    @pytest.mark.asyncio
    async def test_get_agents_filters_by_status(self, adapter: StateAdapter) -> None:
        """Test filtering agents by status."""
        await adapter.update_agent("agent:1", {"role": "dev", "status": "active"})
        await adapter.update_agent("agent:2", {"role": "dev", "status": "idle"})

        active = await adapter.get_agents(status="active")
        idle = await adapter.get_agents(status="idle")

        assert len(active) == 1
        assert len(idle) == 1
        assert active[0].id == "agent:1"
        assert idle[0].id == "agent:2"

    @pytest.mark.asyncio
    async def test_get_agents_filters_by_role(self, adapter: StateAdapter) -> None:
        """Test filtering agents by role."""
        await adapter.update_agent("agent:1", {"role": "backend", "status": "active"})
        await adapter.update_agent("agent:2", {"role": "frontend", "status": "active"})

        backend = await adapter.get_agents(role="backend")
        frontend = await adapter.get_agents(role="frontend")

        assert len(backend) == 1
        assert len(frontend) == 1
        assert backend[0].role == "backend"
        assert frontend[0].role == "frontend"

    @pytest.mark.asyncio
    async def test_persist_agent(self, adapter: StateAdapter) -> None:
        """Test persist_agent stores agent data."""
        agent_data = {
            "id": "forge:kimi",
            "role": "developer",
            "name": "Kimi",
            "status": "active",
            "project": "forge-harness",
            "last_activity": "2026-02-22T12:00:00+00:00",
        }

        agent = await adapter.persist_agent(agent_data)

        assert agent.id == "forge:kimi"
        assert agent.name == "Kimi"

        # Verify retrieval
        retrieved = await adapter.get_agent("forge:kimi")
        assert retrieved is not None
        assert retrieved.name == "Kimi"

    @pytest.mark.asyncio
    async def test_persist_agent_handles_session_id(self, adapter: StateAdapter) -> None:
        """Test persist_agent handles session_id field."""
        agent_data = {
            "session_id": "forge:kimi",
            "role": "developer",
            "status": "active",
        }

        agent = await adapter.persist_agent(agent_data)

        assert agent.id == "forge:kimi"


# =============================================================================
# StateAdapter Tests - Nodes
# =============================================================================

class TestStateAdapterNodes:
    """Tests for StateAdapter node operations."""

    @pytest.mark.asyncio
    async def test_get_nodes_empty(self, adapter: StateAdapter) -> None:
        """Test getting nodes when none exist."""
        nodes = await adapter.get_nodes()
        assert nodes == []

    @pytest.mark.asyncio
    async def test_get_nodes_returns_all(self, adapter: StateAdapter, sample_node_data: dict) -> None:
        """Test getting all nodes."""
        # Create node files
        node_path = adapter._node_path("nova")
        with open(node_path, "w") as f:
            json.dump(sample_node_data, f)

        nodes = await adapter.get_nodes()

        assert len(nodes) == 1
        assert nodes[0].node_id == "nova"
        assert nodes[0].status == "online"

    @pytest.mark.asyncio
    async def test_get_node(self, adapter: StateAdapter, sample_node_data: dict) -> None:
        """Test getting a specific node."""
        node_path = adapter._node_path("nova")
        with open(node_path, "w") as f:
            json.dump(sample_node_data, f)

        node = await adapter.get_node("nova")

        assert node is not None
        assert node.node_id == "nova"
        assert node.cpu_load == 45.5

    @pytest.mark.asyncio
    async def test_get_node_returns_none_for_missing(self, adapter: StateAdapter) -> None:
        """Test get_node returns None for non-existent node."""
        result = await adapter.get_node("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_nodes_filters_by_status(self, adapter: StateAdapter) -> None:
        """Test filtering nodes by status."""
        # Create nodes
        online_data = {"node_id": "node1", "name": "Node 1", "status": "online"}
        offline_data = {"node_id": "node2", "name": "Node 2", "status": "offline"}

        with open(adapter._node_path("node1"), "w") as f:
            json.dump(online_data, f)
        with open(adapter._node_path("node2"), "w") as f:
            json.dump(offline_data, f)

        online_nodes = await adapter.get_nodes(status="online")
        offline_nodes = await adapter.get_nodes(status="offline")

        assert len(online_nodes) == 1
        assert len(offline_nodes) == 1
        assert online_nodes[0].node_id == "node1"
        assert offline_nodes[0].node_id == "node2"

    @pytest.mark.asyncio
    async def test_update_node(self, adapter: StateAdapter) -> None:
        """Test updating a node."""
        # Create node
        original_data = {
            "node_id": "nova",
            "name": "nova",
            "status": "online",
            "cpu_load": 50.0,
        }
        node_path = adapter._node_path("nova")
        with open(node_path, "w") as f:
            json.dump(original_data, f)

        # Update it
        updated = await adapter.update_node("nova", {
            "cpu_load": 75.5,
            "ram_usage": 80.0,
        })

        assert updated is not None
        assert updated.cpu_load == 75.5
        assert updated.ram_usage == 80.0

        # Verify persistence
        retrieved = await adapter.get_node("nova")
        assert retrieved.cpu_load == 75.5

    @pytest.mark.asyncio
    async def test_update_node_returns_none_for_missing(self, adapter: StateAdapter) -> None:
        """Test update_node returns None for non-existent node."""
        result = await adapter.update_node("missing", {"status": "offline"})
        assert result is None


# =============================================================================
# StateAdapter Tests - Registry Sync
# =============================================================================

class TestStateAdapterRegistrySync:
    """Tests for StateAdapter registry sync operations."""

    @pytest.mark.asyncio
    async def test_sync_from_registry_without_registry(self, adapter: StateAdapter) -> None:
        """Test sync_from_registry when agent_registry is not available."""
        # Should not crash, just log warning and return synced=0
        result = await adapter.sync_from_registry(agent_registry=None)

        # When registry import fails, it returns synced=0
        assert result["synced"] == 0

    @pytest.mark.asyncio
    async def test_sync_from_registry_with_mock(self, adapter: StateAdapter) -> None:
        """Test sync_from_registry with mocked registry."""
        # Create mock agent registry
        mock_registry = mock.MagicMock()
        mock_agent = mock.MagicMock()
        mock_agent.id = "forge:kimi"
        mock_agent.to_dict.return_value = {
            "id": "forge:kimi",
            "role": "developer",
            "status": "active",
            "project": "test-project",
        }
        mock_registry.list_active.return_value = [mock_agent]

        # Sync
        result = await adapter.sync_from_registry(agent_registry=mock_registry)

        assert result["synced"] == 1

        # Verify agent was persisted
        agent = await adapter.get_agent("forge:kimi")
        assert agent is not None
        assert agent.role == "developer"


# =============================================================================
# Singleton Tests
# =============================================================================

class TestStateAdapterSingleton:
    """Tests for StateAdapter singleton pattern."""

    def test_get_state_adapter_returns_singleton(self, temp_forge_root: Path) -> None:
        """Test get_state_adapter returns the same instance."""
        # Reset singleton for test
        import forge_harness.state_adapter
        forge_harness.state_adapter._state_adapter_instance = None

        adapter1 = get_state_adapter(temp_forge_root)
        adapter2 = get_state_adapter()

        assert adapter1 is adapter2

    def test_get_state_adapter_with_forge_root(self, temp_forge_root: Path) -> None:
        """Test get_state_adapter uses provided forge_root."""
        # Reset singleton for test
        import forge_harness.state_adapter
        forge_harness.state_adapter._state_adapter_instance = None

        adapter = get_state_adapter(temp_forge_root)

        assert adapter._forge_root == temp_forge_root


# =============================================================================
# Path Resolution Tests
# =============================================================================

class TestPathResolution:
    """Tests for FORGE root path resolution."""

    def test_get_forge_root_from_env(self, monkeypatch) -> None:
        """Test _get_forge_root uses FORGE_ROOT environment variable."""
        import forge_harness.state_adapter

        monkeypatch.setenv("FORGE_ROOT", "/custom/forge")
        result = forge_harness.state_adapter._get_forge_root()

        assert result == Path("/custom/forge")

    def test_get_forge_root_default(self) -> None:
        """Test _get_forge_root defaults to relative path."""
        import forge_harness.state_adapter

        result = forge_harness.state_adapter._get_forge_root()

        # Should resolve to FORGE root directory
        # The result should be a valid path that exists
        assert result.exists() or result.name == "FORGE"
        # Verify it's pointing at the right place by checking structure
        assert (result / ".forge").exists() or (result / "harness").exists()


# =============================================================================
# Integration Tests
# =============================================================================

class TestStateAdapterIntegration:
    """Integration tests for StateAdapter."""

    @pytest.mark.asyncio
    async def test_full_task_lifecycle(self, adapter: StateAdapter) -> None:
        """Test complete task lifecycle: create, read, update, delete."""
        # Create
        task = await adapter.create_task({
            "subject": "Integration test task",
            "priority": "high",
        })
        assert task.id

        # Read
        retrieved = await adapter.get_task(task.id)
        assert retrieved is not None
        assert retrieved.subject == "Integration test task"

        # Update
        updated = await adapter.update_task(task.id, {"status": "completed"})
        assert updated.status == "completed"

        # List
        all_tasks = await adapter.get_tasks()
        assert len(all_tasks) == 1
        assert all_tasks[0].status == "completed"

        # Delete
        deleted = await adapter.delete_task(task.id)
        assert deleted is True

        # Verify gone
        assert await adapter.get_task(task.id) is None

    @pytest.mark.asyncio
    async def test_full_agent_lifecycle(self, adapter: StateAdapter) -> None:
        """Test complete agent lifecycle: create, read, update."""
        # Create/update
        agent = await adapter.update_agent("forge:test", {
            "role": "tester",
            "status": "active",
        })
        assert agent.id == "forge:test"

        # Read
        retrieved = await adapter.get_agent("forge:test")
        assert retrieved is not None
        assert retrieved.role == "tester"

        # Update
        updated = await adapter.update_agent("forge:test", {
            "progress": 100,
            "status": "completed",
        })
        assert updated.progress == 100

        # List
        all_agents = await adapter.get_agents()
        assert len(all_agents) == 1

    @pytest.mark.asyncio
    async def test_corrupted_files_are_skipped(self, adapter: StateAdapter) -> None:
        """Test that corrupted JSON files are skipped gracefully."""
        # Create a valid task
        await adapter.create_task({"subject": "Valid task"})

        # Create a corrupted file
        bad_path = adapter._task_path("corrupted")
        with open(bad_path, "w") as f:
            f.write("{invalid json")

        # Should not crash, just skip the bad file
        tasks = await adapter.get_tasks()
        assert len(tasks) == 1
        assert tasks[0].subject == "Valid task"

    @pytest.mark.asyncio
    async def test_concurrent_updates(self, adapter: StateAdapter) -> None:
        """Test that concurrent updates are handled safely."""
        task = await adapter.create_task({"subject": "Concurrent test"})

        # Simulate concurrent updates
        import asyncio

        async def update_task(value: str) -> None:
            await adapter.update_task(task.id, {"description": value})

        await asyncio.gather(
            update_task("Update 1"),
            update_task("Update 2"),
            update_task("Update 3"),
        )

        # Should have one of the updates
        final = await adapter.get_task(task.id)
        assert final.description in ["Update 1", "Update 2", "Update 3"]
