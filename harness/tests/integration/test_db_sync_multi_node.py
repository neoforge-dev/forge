"""
DB + Sync + Multi-Node Integration Test Suite
==============================================

Comprehensive integration tests for:
1. Agent registration and DB persistence
2. Task assignment and updates
3. Sync propagation (StateSynchronizer)
4. Node communication (Heartbeats, Dispatch)
"""

import asyncio
import json
import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forge_harness.state_store import AgentRole, AgentStatus, AgentType, StateStore
from forge_harness.state_synchronizer import StateSynchronizer
from forge_harness.webhook_server import AuthConfig, create_app
from forge_harness.webhook_server.services.agent_registry import get_agent_registry


@pytest.fixture
def test_env():
    """Create a temporary environment for testing."""
    temp_dir = Path(tempfile.mkdtemp())
    forge_root = temp_dir / "FORGE"
    forge_root.mkdir()

    # Create checkpoint directories
    (forge_root / ".forge/approvals").mkdir()
    (forge_root / ".forge/orchestration_checkpoints").mkdir()
    (forge_root / ".forge/ralph_checkpoints").mkdir()
    (forge_root / ".forge/sessions").mkdir()
    (forge_root / ".forge/mvp_status").mkdir()

    sqlite_path = str(forge_root / ".forge/state.db")

    yield {
        "root": forge_root,
        "sqlite_path": sqlite_path,
        "temp_dir": temp_dir
    }

    shutil.rmtree(temp_dir)


@pytest.fixture
def integration_server(test_env):
    """Create a test server with the temporary environment."""
    # Use SQLite for testing DB persistence
    state_store = StateStore(sqlite_path=test_env["sqlite_path"])
    state_store.connect()

    # Set environment variable for TaskQueueHandler persistence
    os.environ["FORGE_ROOT"] = str(test_env["root"])

    # Initialize StateSynchronizer
    synchronizer = StateSynchronizer(forge_root=test_env["root"], state_store=state_store)

    # Create app
    auth_config = AuthConfig(require_auth=False)
    app = create_app(auth_config=auth_config)

    # Override dependencies if needed
    # (In a real scenario, we might need to inject the test state_store/synchronizer)

    return {
        "app": app,
        "state_store": state_store,
        "synchronizer": synchronizer,
        "client": TestClient(app)
    }


@pytest.mark.skip(reason="Fixture state_store not wired into app global registry — needs DI rework")
class TestDBSyncMultiNode:
    """Integration tests for DB, Sync, and Multi-Node components."""

    @pytest.mark.asyncio
    async def test_agent_registration_persistence(self, integration_server):
        """Test that registering an agent persists it in the DB."""
        client = integration_server["client"]
        state_store = integration_server["state_store"]

        agent_id = f"test-agent-{uuid.uuid4().hex[:4]}"
        register_payload = {
            "role": "builder",
            "project": "test-project",
            "task": "Integration test task",
            "name": "Persistence Test Agent",
            "domain": "test-domain",
            "tmux_session": f"forge:{agent_id}"
        }

        # 1. Register agent via API
        response = client.post("/api/agents/register", json=register_payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        assigned_id = data["data"]["session_id"]

        # 2. Verify in StateStore (DB)
        agent = state_store.get_agent(assigned_id)
        assert agent is not None
        assert agent.session_id == assigned_id
        assert agent.project == "test-project"
        assert agent.domain == "test-domain"

    @pytest.mark.asyncio
    async def test_task_assignment_flow(self, integration_server):
        """Test task creation, listing, and claiming."""
        client = integration_server["client"]

        # 1. Create a task
        task_payload = {
            "subject": "Integration Test Task",
            "description": "A task for testing assignment",
            "priority": "high",
            "required_role": "builder"
        }
        response = client.post("/api/tasks", json=task_payload)
        assert response.status_code == 200
        task_data = response.json()["data"]
        task_id = task_data["id"]

        # 2. List tasks and find our task
        response = client.get("/api/tasks")
        assert response.status_code == 200
        tasks_data = response.json()["data"]
        tasks = tasks_data["tasks"]
        assert any(t["id"] == task_id for t in tasks)

        # 3. Claim the task
        agent_id = "test-agent-123"
        response = client.post(f"/api/tasks/{task_id}/claim", json={"agent_id": agent_id})
        assert response.status_code == 200
        updated_task = response.json()["data"]
        assert updated_task["status"] == "assigned"
        assert updated_task["claimed_by"] == agent_id

    @pytest.mark.asyncio
    async def test_sync_propagation(self, integration_server, test_env):
        """Test that file-based checkpoints are synced to the snapshot."""
        synchronizer = integration_server["synchronizer"]
        forge_root = test_env["root"]

        # 1. Create a session checkpoint file manually (simulating an external process)
        session_id = "external-session-001"
        session_file = forge_root / ".forge/sessions" / f"{session_id}.json"
        session_data = {
            "session_id": session_id,
            "agent_type": "claude-code",
            "agent_role": "builder",
            "domain": "test-domain",
            "project": "test-project",
            "status": "active",
            "current_task": "External task",
            "registered_at": datetime.now(UTC).isoformat(),
            "last_heartbeat": datetime.now(UTC).isoformat()
        }
        session_file.write_text(json.dumps(session_data))

        # 2. Trigger manual sync
        snapshot = await synchronizer.sync_all()

        # 3. Verify session appears in snapshot
        assert any(s.session_id == session_id for s in snapshot.sessions)

        # 4. Create an approval checkpoint
        approval_id = "app-001"
        approval_file = forge_root / ".forge/approvals" / f"approval_{approval_id}.json"
        approval_data = {
            "id": approval_id,
            "type": "test",
            "status": "pending",
            "title": "Test Approval",
            "domain": "test-domain",
            "priority": "normal",
            "tier": "PHONE",
            "created_at": datetime.now(UTC).isoformat()
        }
        approval_file.write_text(json.dumps(approval_data))

        # 5. Sync again
        snapshot = await synchronizer.sync_all()
        assert any(a.id == approval_id for a in snapshot.approvals)

    @pytest.mark.asyncio
    async def test_node_communication(self, integration_server):
        """Test node heartbeat and dispatch logic."""
        client = integration_server["client"]

        # 1. Register a node via heartbeat
        node_id = "test-node-alpha"
        heartbeat_payload = {
            "node_id": node_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "resources": {"cpu_load": 10, "ram_usage": 20},
            "current_task": "Idle",
            "capabilities": ["claude", "python"],
            "health": {"status": "ok"}
        }
        response = client.post("/api/nodes/heartbeat", json=heartbeat_payload)
        assert response.status_code == 200
        assert response.json()["data"]["accepted"] is True

        # 2. List nodes and verify our node is there
        response = client.get("/api/nodes")
        # Note: Current implementation of GET /api/nodes returns only local node.
        # But let's check if the endpoint works.
        assert response.status_code == 200

        # 3. Test node recommendation
        response = client.get("/api/nodes/recommend?task_type=python")
        assert response.status_code == 200
        recommendation = response.json()["data"]
        assert "recommended_node_id" in recommendation

    @pytest.mark.asyncio
    async def test_multi_node_dispatch(self, integration_server):
        """Test dispatching work to a specific node."""
        client = integration_server["client"]

        # 1. Get local node ID
        nodes_response = client.get("/api/nodes")
        node_id = nodes_response.json()["data"]["nodes"][0]["node_id"]

        # 2. Dispatch work to this node
        dispatch_payload = {
            "agent_type": "claude",
            "task": "Test multi-node dispatch",
            "priority": "high",
            "project": "test-project"
        }
        response = client.post(f"/api/nodes/{node_id}/dispatch", json=dispatch_payload)
        assert response.status_code == 200
        dispatch_data = response.json()["data"]
        assert dispatch_data["success"] is True
        assert "dispatch_id" in dispatch_data
        assert dispatch_data["status"] in ("dispatched", "queued")

if __name__ == "__main__":
    # This allows running the test script directly for quick verification
    pytest.main([__file__])
