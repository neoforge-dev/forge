"""Tests for /api/nodes endpoints."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def client():
    """Create test client with nodes router."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from forge_harness.webhook_server.api.nodes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def mock_tmux():
    """Mock tmux calls so tests work without tmux session."""
    with patch("subprocess.run") as mock_run:
        # Default: tmux returns two agents
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "claude\nkimi\n"
        mock_run.return_value.stderr = ""
        yield mock_run


class TestListNodes:
    def test_returns_nodes_array(self, client):
        response = client.get("/api/nodes")
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert isinstance(data["nodes"], list)
        assert len(data["nodes"]) >= 1

    def test_node_has_required_fields(self, client):
        response = client.get("/api/nodes")
        node = response.json()["nodes"][0]
        assert "node_id" in node
        assert "name" in node
        assert "status" in node
        assert "agent_count" in node
        assert "cpu_load" in node
        assert "ram_usage" in node
        assert "agents" in node
        assert "capabilities" in node
        assert "specs" in node
        assert "alerts" in node

    def test_node_status_is_valid(self, client):
        response = client.get("/api/nodes")
        node = response.json()["nodes"][0]
        assert node["status"] in ("online", "offline", "degraded", "maintenance")


class TestGetNode:
    def test_get_existing_node(self, client):
        # First get the node ID
        list_resp = client.get("/api/nodes")
        node_id = list_resp.json()["nodes"][0]["node_id"]

        response = client.get(f"/api/nodes/{node_id}")
        assert response.status_code == 200
        assert response.json()["node_id"] == node_id

    def test_get_nonexistent_node(self, client):
        response = client.get("/api/nodes/nonexistent-node-xyz")
        assert response.status_code == 404


class TestFleetHealth:
    def test_returns_health_summary(self, client):
        response = client.get("/api/nodes/health")
        assert response.status_code == 200
        data = response.json()
        assert "total_nodes" in data
        assert "online" in data
        assert "health_percentage" in data
        assert "total_agents" in data
        assert "nodes" in data

    def test_health_counts_add_up(self, client):
        response = client.get("/api/nodes/health")
        data = response.json()
        assert data["online"] + data["degraded"] + data["offline"] == data["total_nodes"]


class TestRecommendNode:
    def test_returns_recommendation(self, client):
        response = client.get("/api/nodes/recommend?task_type=python")
        assert response.status_code == 200
        data = response.json()
        assert "recommended_node_id" in data
        assert "node_name" in data
        assert "reason" in data
        assert "current_load" in data


class TestDispatchToNode:
    def test_dispatch_returns_response(self, client):
        list_resp = client.get("/api/nodes")
        node_id = list_resp.json()["nodes"][0]["node_id"]

        response = client.post(
            f"/api/nodes/{node_id}/dispatch",
            json={
                "agent_type": "claude",
                "task": "Run tests",
                "priority": "normal",
            },
        )
        data = response.json()
        assert "success" in data
        assert "dispatch_id" in data
        assert "node_id" in data
        assert data["node_id"] == node_id


class TestSyncNodes:
    def test_sync_returns_response(self, client):
        response = client.post("/api/nodes/sync")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "synced_nodes" in data
        assert "sync_id" in data
        assert "details" in data
        assert len(data["details"]) >= 1
