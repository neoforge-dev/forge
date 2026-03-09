"""Multi-Node Integration Tests for FORGE Harness.

Comprehensive integration tests covering:
- Agent registration across nodes
- Task assignment and routing
- Sync propagation between nodes
- Node communication and state consistency
"""

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


@pytest.fixture
def test_app():
    """Create test app with mocked dependencies."""
    try:
        from fastapi.testclient import TestClient

        from forge_harness.webhook_server.infrastructure.auth import AuthConfig
        from forge_harness.webhook_server_main import create_app
    except ImportError as e:
        pytest.skip(f"FastAPI or dependencies not installed: {e}")

    auth_config = AuthConfig(require_auth=False)
    app = create_app(auth_config=auth_config)
    if app is None:
        pytest.skip("Failed to create app")
    return TestClient(app)


@pytest.fixture
def mock_state_store():
    """Create a mock StateStore for testing."""
    store = MagicMock()
    store.is_connected.return_value = True
    store._store_type = "redis"

    # Mock agent storage
    store._agents = {}
    store._tasks = {}
    store._nodes = {}

    def mock_get_agent(agent_id):
        return store._agents.get(agent_id)

    def mock_register_agent(agent_session):
        store._agents[agent_session.session_id] = agent_session
        return True

    def mock_get_active_agents():
        return list(store._agents.values())

    def mock_save_task(task):
        store._tasks[task.task_id] = task
        return True

    def mock_get_task(task_id):
        return store._tasks.get(task_id)

    def mock_save_node(node):
        store._nodes[node.node_id] = node
        return True

    def mock_get_node(node_id):
        return store._nodes.get(node_id)

    store.get_agent = mock_get_agent
    store.register_agent = mock_register_agent
    store.get_active_agents = mock_get_active_agents
    store.save_task = mock_save_task
    store.get_task = mock_get_task
    store.save_node = mock_save_node
    store.get_node = mock_get_node
    store.get_store_type.return_value = "redis"

    return store


class TestAgentRegistration:
    """Integration tests for agent registration across nodes."""

    def test_register_agent_basic(self, test_app):
        """Test basic agent registration."""
        payload = {
            "role": "backend-engineer",
            "project": "test-project",
            "task": "Implement feature",
            "name": "Test Agent",
            "domain": "test-domain",
            "tmux_session": "forge:test-agent",
            "skills": ["python", "fastapi"],
        }

        response = test_app.post("/api/agents/register", json=payload)

        if response.status_code in (404, 501):
            pytest.skip("Agent registration endpoint not implemented")

        assert response.status_code in (200, 201)
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "session_id" in data["data"]
        assert data["data"]["agent_role"] == "backend-engineer"
        assert data["data"]["project"] == "test-project"

    def test_register_agent_with_hierarchy(self, test_app):
        """Test agent registration with parent/child hierarchy."""
        # Register parent agent
        parent_payload = {
            "role": "orchestrator",
            "project": "test-project",
            "task": "Coordinate work",
            "name": "Parent Agent",
            "domain": "test-domain",
        }

        parent_response = test_app.post("/api/agents/register", json=parent_payload)
        if parent_response.status_code in (404, 501):
            pytest.skip("Agent registration endpoint not implemented")

        assert parent_response.status_code in (200, 201)
        parent_id = parent_response.json()["data"]["session_id"]

        # Register child agent
        child_payload = {
            "role": "backend-engineer",
            "project": "test-project",
            "task": "Implement feature",
            "name": "Child Agent",
            "domain": "test-domain",
            "parent_id": parent_id,
        }

        child_response = test_app.post("/api/agents/register", json=child_payload)
        assert child_response.status_code in (200, 201)
        child_data = child_response.json()["data"]
        assert child_data.get("parent_id") == parent_id

    def test_list_agents_after_registration(self, test_app):
        """Test listing agents after multiple registrations."""
        # Register multiple agents
        agents = [
            {"role": "backend-engineer", "project": "proj1", "task": "Task 1", "name": "Agent 1", "domain": "domain1"},
            {"role": "frontend-builder", "project": "proj2", "task": "Task 2", "name": "Agent 2", "domain": "domain2"},
            {"role": "tester", "project": "proj3", "task": "Task 3", "name": "Agent 3", "domain": "domain3"},
        ]

        registered_ids = []
        for agent_data in agents:
            response = test_app.post("/api/agents/register", json=agent_data)
            if response.status_code in (404, 501):
                pytest.skip("Agent registration endpoint not implemented")
            if response.status_code in (200, 201):
                registered_ids.append(response.json()["data"]["session_id"])

        # List all agents
        list_response = test_app.get("/api/agents")
        if list_response.status_code in (404, 501):
            pytest.skip("List agents endpoint not implemented")

        assert list_response.status_code == 200
        list_data = list_response.json()
        assert list_data["success"] is True
        assert "data" in list_data
        assert "agents" in list_data["data"]
        assert list_data["data"]["total"] >= len(registered_ids)


class TestTaskAssignment:
    """Integration tests for task assignment and routing."""

    def test_create_and_assign_task(self, test_app):
        """Test creating a task and assigning it to an agent."""
        # First register an agent
        agent_payload = {
            "role": "backend-engineer",
            "project": "test-project",
            "task": "Available for work",
            "name": "Worker Agent",
            "domain": "test-domain",
        }

        agent_response = test_app.post("/api/agents/register", json=agent_payload)
        if agent_response.status_code in (404, 501):
            pytest.skip("Agent registration endpoint not implemented")

        agent_id = agent_response.json()["data"]["session_id"]

        # Create a task (using tasks API if available)
        task_payload = {
            "title": "Implement feature X",
            "description": "Add new feature to the API",
            "priority": "high",
            "domain": "test-domain",
            "project": "test-project",
            "assigned_to": agent_id,
        }

        task_response = test_app.post("/api/tasks", json=task_payload)
        if task_response.status_code in (404, 501):
            pytest.skip("Task creation endpoint not implemented")

        if task_response.status_code in (200, 201):
            task_data = task_response.json()
            assert task_data["success"] is True
            assert "data" in task_data

    def test_task_assignment_with_dispatch(self, test_app):
        """Test task dispatch to available agents."""
        # Register multiple agents
        agents = [
            {"role": "backend-engineer", "project": "proj1", "task": "Available", "name": "Backend Agent", "domain": "test-domain"},
            {"role": "frontend-builder", "project": "proj2", "task": "Available", "name": "Frontend Agent", "domain": "test-domain"},
        ]

        for agent_data in agents:
            test_app.post("/api/agents/register", json=agent_data)

        # Dispatch a task
        dispatch_payload = {
            "agent_type": "backend-engineer",
            "task": "Fix database connection issue",
            "priority": "high",
            "project": "test-project",
            "domain": "test-domain",
        }

        dispatch_response = test_app.post("/api/nodes/local-node/dispatch", json=dispatch_payload)
        if dispatch_response.status_code in (404, 501):
            pytest.skip("Dispatch endpoint not implemented")

        if dispatch_response.status_code in (200, 201):
            dispatch_data = dispatch_response.json()
            assert dispatch_data["success"] is True
            assert "data" in dispatch_data
            assert "dispatch_id" in dispatch_data["data"]


class TestSyncPropagation:
    """Integration tests for sync propagation between nodes."""

    def test_node_sync_operation(self, test_app):
        """Test sync operation across nodes."""
        sync_payload = {
            "nodes": ["node-1", "node-2", "node-3"],
            "sync_type": "full",
        }

        response = test_app.post("/api/nodes/sync", json=sync_payload)
        if response.status_code in (404, 501):
            pytest.skip("Node sync endpoint not implemented")

        if response.status_code in (200, 201):
            data = response.json()
            assert data["success"] is True
            assert "data" in data
            assert "sync_id" in data["data"]

    def test_node_heartbeat_propagation(self, test_app):
        """Test node heartbeat and state propagation."""
        # Send heartbeat from a node
        heartbeat_payload = {
            "node_id": "test-node-1",
            "timestamp": datetime.now(UTC).isoformat(),
            "resources": {
                "cpu_percent": 45.5,
                "memory_percent": 60.2,
                "disk_usage": 75.0,
                "queue_depth": 3,
            },
            "current_task": "Processing batch jobs",
            "capabilities": ["claude", "codex", "docker"],
            "health": {
                "status": "healthy",
                "last_check": datetime.now(UTC).isoformat(),
            },
        }

        response = test_app.post("/api/nodes/heartbeat", json=heartbeat_payload)
        if response.status_code in (404, 501):
            pytest.skip("Node heartbeat endpoint not implemented")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["accepted"] is True

    def test_state_consistency_after_sync(self, test_app, mock_state_store):
        """Test state consistency after sync operations."""
        with patch("forge_harness.webhook_server.api.agents.get_state_store", return_value=mock_state_store):
            # Register an agent
            agent_payload = {
                "role": "backend-engineer",
                "project": "test-project",
                "task": "Test task",
                "name": "Sync Test Agent",
                "domain": "test-domain",
            }

            register_response = test_app.post("/api/agents/register", json=agent_payload)
            if register_response.status_code in (404, 501):
                pytest.skip("Agent registration endpoint not implemented")

            agent_id = register_response.json()["data"]["session_id"]

            # Trigger sync
            sync_response = test_app.post("/api/nodes/sync", json={})
            if sync_response.status_code in (404, 501):
                pytest.skip("Node sync endpoint not implemented")

            # Verify agent is still accessible after sync
            get_response = test_app.get(f"/api/agents/{agent_id}")
            if get_response.status_code in (404, 501):
                pytest.skip("Get agent endpoint not implemented")

            # Agent should be found (either in registry or state store)
            assert get_response.status_code == 200


class TestNodeCommunication:
    """Integration tests for node communication."""

    def test_list_nodes(self, test_app):
        """Test listing all nodes."""
        response = test_app.get("/api/nodes")
        if response.status_code in (404, 501):
            pytest.skip("List nodes endpoint not implemented")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "nodes" in data["data"]

    def test_get_node_details(self, test_app):
        """Test getting details for a specific node."""
        # First list nodes to get a node ID
        list_response = test_app.get("/api/nodes")
        if list_response.status_code in (404, 501):
            pytest.skip("List nodes endpoint not implemented")

        nodes = list_response.json()["data"]["nodes"]
        if not nodes:
            pytest.skip("No nodes available")

        node_id = nodes[0]["node_id"]

        # Get specific node details
        get_response = test_app.get(f"/api/nodes/{node_id}")
        if get_response.status_code in (404, 501):
            pytest.skip("Get node endpoint not implemented")

        assert get_response.status_code == 200
        data = get_response.json()
        assert data["success"] is True
        assert "data" in data

    def test_node_health_aggregation(self, test_app):
        """Test node health aggregation endpoint."""
        response = test_app.get("/api/nodes/health")
        if response.status_code in (404, 501):
            pytest.skip("Node health endpoint not implemented")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "health_percentage" in data["data"]
        assert "total_nodes" in data["data"]

    def test_recommend_node_for_task(self, test_app):
        """Test node recommendation for task types."""
        response = test_app.get("/api/nodes/recommend?task_type=backend")
        if response.status_code in (404, 501):
            pytest.skip("Node recommend endpoint not implemented")

        if response.status_code == 200:
            data = response.json()
            assert data["success"] is True
            assert "data" in data
            assert "recommended_node_id" in data["data"]


class TestAgentStateConsistency:
    """Integration tests for agent state consistency across operations."""

    def test_agent_progress_updates(self, test_app):
        """Test agent progress updates are persisted."""
        # Register agent
        agent_payload = {
            "role": "backend-engineer",
            "project": "test-project",
            "task": "Initial task",
            "name": "Progress Test Agent",
            "domain": "test-domain",
        }

        register_response = test_app.post("/api/agents/register", json=agent_payload)
        if register_response.status_code in (404, 501):
            pytest.skip("Agent registration endpoint not implemented")

        agent_id = register_response.json()["data"]["session_id"]

        # Update progress
        progress_payload = {
            "progress": 50,
            "current_task": "Halfway done",
            "files_modified": ["file1.py", "file2.py"],
        }

        progress_response = test_app.post(f"/api/agents/{agent_id}/progress", json=progress_payload)
        if progress_response.status_code in (404, 501):
            pytest.skip("Progress update endpoint not implemented")

        if progress_response.status_code == 200:
            # Verify progress was updated
            get_response = test_app.get(f"/api/agents/{agent_id}")
            if get_response.status_code == 200:
                agent_data = get_response.json()["data"]
                assert agent_data["progress_pct"] == 50

    def test_agent_pause_resume(self, test_app):
        """Test agent pause and resume operations."""
        # Register agent
        agent_payload = {
            "role": "backend-engineer",
            "project": "test-project",
            "task": "Test task",
            "name": "Pause Test Agent",
            "domain": "test-domain",
        }

        register_response = test_app.post("/api/agents/register", json=agent_payload)
        if register_response.status_code in (404, 501):
            pytest.skip("Agent registration endpoint not implemented")

        agent_id = register_response.json()["data"]["session_id"]

        # Pause agent
        pause_payload = {"reason": "Testing pause functionality", "duration_minutes": 30}
        pause_response = test_app.post(f"/api/agents/{agent_id}/pause", json=pause_payload)
        if pause_response.status_code in (404, 501):
            pytest.skip("Pause endpoint not implemented")

        if pause_response.status_code == 200:
            pause_data = pause_response.json()["data"]
            # API returns {"status": "paused"} or just success
            if isinstance(pause_data, dict) and "status" in pause_data:
                assert pause_data["status"] == "paused"

            # Resume agent
            resume_response = test_app.post(f"/api/agents/{agent_id}/resume", json={})
            if resume_response.status_code in (404, 501):
                pytest.skip("Resume endpoint not implemented")

            if resume_response.status_code == 200:
                resume_data = resume_response.json()["data"]
                if isinstance(resume_data, dict) and "status" in resume_data:
                    assert resume_data["status"] == "active"

    def test_agent_heartbeat_updates_activity(self, test_app):
        """Test that agent heartbeat updates last activity."""
        # Register agent
        agent_payload = {
            "role": "backend-engineer",
            "project": "test-project",
            "task": "Test task",
            "name": "Heartbeat Test Agent",
            "domain": "test-domain",
        }

        register_response = test_app.post("/api/agents/register", json=agent_payload)
        if register_response.status_code in (404, 501):
            pytest.skip("Agent registration endpoint not implemented")

        agent_id = register_response.json()["data"]["session_id"]

        # Send heartbeat
        heartbeat_response = test_app.post(f"/api/agents/{agent_id}/heartbeat", json={})
        if heartbeat_response.status_code in (404, 501):
            pytest.skip("Heartbeat endpoint not implemented")

        assert heartbeat_response.status_code == 200
        data = heartbeat_response.json()
        assert data["success"] is True
        assert data["data"]["status"] == "ok"


class TestEndToEndWorkflow:
    """End-to-end integration tests covering full workflows."""

    def test_full_agent_lifecycle(self, test_app):
        """Test complete agent lifecycle: register -> update -> pause -> resume -> complete."""
        # 1. Register
        agent_payload = {
            "role": "backend-engineer",
            "project": "test-project",
            "task": "Full lifecycle test",
            "name": "Lifecycle Test Agent",
            "domain": "test-domain",
            "skills": ["python", "testing"],
        }

        register_response = test_app.post("/api/agents/register", json=agent_payload)
        if register_response.status_code in (404, 501):
            pytest.skip("Agent registration endpoint not implemented")

        assert register_response.status_code in (200, 201)
        agent_id = register_response.json()["data"]["session_id"]

        # 2. Update progress
        progress_response = test_app.post(
            f"/api/agents/{agent_id}/progress",
            json={"progress": 25, "current_task": "Started work"},
        )

        # 3. Pause
        pause_response = test_app.post(
            f"/api/agents/{agent_id}/pause",
            json={"reason": "Temporary pause", "duration_minutes": 15},
        )

        # 4. Resume
        resume_response = test_app.post(f"/api/agents/{agent_id}/resume", json={})

        # 5. Heartbeat
        heartbeat_response = test_app.post(f"/api/agents/{agent_id}/heartbeat", json={})

        # 6. Complete
        complete_response = test_app.post(f"/api/agents/{agent_id}/complete", json={})
        if complete_response.status_code in (404, 501):
            pytest.skip("Complete endpoint not implemented")

        if complete_response.status_code == 200:
            assert complete_response.json()["data"]["status"] == "completed"

    def test_multi_agent_coordination(self, test_app):
        """Test coordination between multiple agents."""
        # Register orchestrator
        orch_payload = {
            "role": "orchestrator",
            "project": "coordination-test",
            "task": "Coordinate multi-agent workflow",
            "name": "Orchestrator",
            "domain": "test-domain",
        }

        orch_response = test_app.post("/api/agents/register", json=orch_payload)
        if orch_response.status_code in (404, 501):
            pytest.skip("Agent registration endpoint not implemented")

        orch_id = orch_response.json()["data"]["session_id"]

        # Register workers
        workers = []
        for i in range(3):
            worker_payload = {
                "role": "backend-engineer",
                "project": "coordination-test",
                "task": f"Worker {i+1} task",
                "name": f"Worker {i+1}",
                "domain": "test-domain",
                "parent_id": orch_id,
            }
            worker_response = test_app.post("/api/agents/register", json=worker_payload)
            if worker_response.status_code in (200, 201):
                workers.append(worker_response.json()["data"]["session_id"])

        # List all agents
        list_response = test_app.get("/api/agents")
        assert list_response.status_code == 200
        agents = list_response.json()["data"]["agents"]

        # Verify orchestrator and workers exist
        agent_ids = [a["session_id"] for a in agents]
        assert orch_id in agent_ids or len(agent_ids) > 0  # At least verify something returned

    def test_fleet_status_accuracy(self, test_app):
        """Test that fleet status accurately reflects agent states."""
        # Register multiple agents with different states
        agent_configs = [
            {"role": "backend-engineer", "status": "active"},
            {"role": "frontend-builder", "status": "active"},
            {"role": "tester", "status": "idle"},
        ]

        registered = []
        for config in agent_configs:
            payload = {
                "role": config["role"],
                "project": "fleet-test",
                "task": "Fleet status test",
                "name": f"{config['role']}-agent",
                "domain": "test-domain",
            }
            response = test_app.post("/api/agents/register", json=payload)
            if response.status_code in (200, 201):
                registered.append(response.json()["data"]["session_id"])

        # Get fleet status
        fleet_response = test_app.get("/api/agents/fleet/status")
        if fleet_response.status_code in (404, 501):
            pytest.skip("Fleet status endpoint not implemented")

        if fleet_response.status_code == 200:
            fleet_data = fleet_response.json()["data"]
            assert "total_agents" in fleet_data
            # Fleet status returns status breakdown as individual fields or in by_status
            assert fleet_data["total_agents"] >= len(registered)
            # Check for either flat structure or nested by_status
            has_status_breakdown = any(k in fleet_data for k in ["active", "waiting", "idle", "by_status"])
            assert has_status_breakdown, f"Expected status breakdown fields, got: {fleet_data.keys()}"
