"""Tests for Task Queue API endpoints."""

import pytest
from fastapi.testclient import TestClient

from forge_harness.webhook_server import create_app
from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthConfig, create_access_token


@pytest.fixture
def app(monkeypatch):
    """Create FastAPI app for testing."""
    monkeypatch.setenv("FORGE_JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("FORGE_JWT_REQUIRE_AUTH", "true")
    return create_app()


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Create authentication headers."""
    config = JWTAuthConfig(secret_key="test-jwt-secret")
    token = create_access_token(
        subject="test-user",
        config=config,
        permissions=["admin", "tasks:read", "tasks:write", "agents:read"],
    )
    return {"Authorization": f"Bearer {token}"}


def test_create_task_endpoint(client, auth_headers):
    """Test POST /api/tasks endpoint."""
    response = client.post(
        "/api/tasks",
        json={
            "subject": "Implement auth middleware",
            "description": "Add token validation and permission checks.",
            "priority": "high",
            "required_role": "builder",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["subject"] == "Implement auth middleware"
    assert data["data"]["priority"] == "high"
    assert data["data"]["status"] == "pending"


def test_list_tasks_endpoint(client, auth_headers):
    """Test GET /api/tasks endpoint."""
    # Create a task first
    client.post(
        "/api/tasks",
        json={
            "subject": "List endpoint verification task",
            "description": "Verify listing endpoint returns created records.",
            "priority": "medium",
        },
        headers=auth_headers,
    )

    # List tasks
    response = client.get("/api/tasks", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "tasks" in data["data"]
    assert "count" in data["data"]


def test_get_task_endpoint(client, auth_headers):
    """Test GET /api/tasks/{task_id} endpoint."""
    # Create a task
    create_response = client.post(
        "/api/tasks",
        json={
            "subject": "Lookup task by id flow",
            "description": "Ensure task retrieval by identifier is stable.",
        },
        headers=auth_headers,
    )
    task_id = create_response.json()["data"]["id"]

    # Get the task
    response = client.get(f"/api/tasks/{task_id}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["id"] == task_id


def test_update_task_endpoint(client, auth_headers):
    """Test PUT /api/tasks/{task_id} endpoint."""
    # Create a task
    create_response = client.post(
        "/api/tasks",
        json={
            "subject": "Update task status behavior",
            "description": "Baseline payload before update operation.",
        },
        headers=auth_headers,
    )
    task_id = create_response.json()["data"]["id"]

    # Update the task - use valid transition: PENDING -> QUEUED (canonical DF lifecycle)
    # Full valid path: PENDING -> QUEUED -> ASSIGNED -> IN_PROGRESS
    response = client.put(
        f"/api/tasks/{task_id}",
        json={
            "description": "After",
            "status": "queued",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["description"] == "After"
    assert data["data"]["status"] == "queued"


def test_delete_task_endpoint(client, auth_headers):
    """Test DELETE /api/tasks/{task_id} endpoint."""
    # Create a task
    create_response = client.post(
        "/api/tasks",
        json={
            "subject": "Delete archived queue item",
            "description": "Create and remove a task through API lifecycle.",
        },
        headers=auth_headers,
    )
    task_id = create_response.json()["data"]["id"]

    # Delete the task
    response = client.delete(f"/api/tasks/{task_id}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["success"] is True

    # Verify it's gone
    get_response = client.get(f"/api/tasks/{task_id}", headers=auth_headers)
    assert get_response.status_code == 404


def test_claim_task_endpoint(client, auth_headers):
    """Test POST /api/tasks/{task_id}/claim endpoint."""
    # Create a task
    create_response = client.post(
        "/api/tasks",
        json={
            "subject": "Claim queue ownership ticket",
            "description": "Verify claim assigns ownership and status.",
        },
        headers=auth_headers,
    )
    task_id = create_response.json()["data"]["id"]

    # Claim the task
    response = client.post(
        f"/api/tasks/{task_id}/claim",
        json={"agent_id": "agent-123"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["claimed_by"] == "agent-123"
    assert data["data"]["status"] == "assigned"


def test_get_task_stats_endpoint(client, auth_headers):
    """Test GET /api/tasks/stats endpoint."""
    # Create some tasks
    client.post(
        "/api/tasks",
        json={
            "subject": "Stats aggregation source one",
            "description": "First task used for stats endpoint assertions.",
        },
        headers=auth_headers,
    )
    client.post(
        "/api/tasks",
        json={
            "subject": "Stats aggregation source two",
            "description": "Second task used for stats endpoint assertions.",
        },
        headers=auth_headers,
    )

    # Get stats
    response = client.get("/api/tasks/stats", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "pending" in data["data"]
    assert "in_progress" in data["data"]
    assert "completed" in data["data"]
    assert "total" in data["data"]


def test_task_filters(client, auth_headers):
    """Test filtering tasks by status and priority."""
    # Create tasks
    client.post(
        "/api/tasks",
        json={
            "subject": "High priority regression flow",
            "description": "Validate high-priority filtering behavior.",
            "priority": "high",
        },
        headers=auth_headers,
    )
    task2 = client.post(
        "/api/tasks",
        json={
            "subject": "Medium priority regression flow",
            "description": "Validate medium-priority task update path.",
            "priority": "medium",
        },
        headers=auth_headers,
    )
    task2_id = task2.json()["data"]["id"]

    # Complete one task
    client.put(
        f"/api/tasks/{task2_id}",
        json={"status": "completed"},
        headers=auth_headers,
    )

    # Filter by priority
    response = client.get("/api/tasks?priority=high", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert all(t["priority"] == "high" for t in data["data"]["tasks"])

    # Filter by status
    response = client.get("/api/tasks?status=pending", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert all(t["status"] == "pending" for t in data["data"]["tasks"])


def test_unauthorized_access(client):
    """Test that endpoints require authentication."""
    response = client.get("/api/tasks")
    assert response.status_code == 401
