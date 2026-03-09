"""
Task Management Endpoint Tests
================================

Tests for the task management API endpoints added to webhook_server.py:
- POST /api/tasks/{task_id}/dispatch
- GET  /api/tasks/agents/available
- POST /api/tasks/reorder
"""

import pytest

from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthConfig, create_access_token

try:
    from fastapi.testclient import TestClient

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def test_app(monkeypatch):
    """Create FastAPI test app with deterministic JWT auth config."""
    if not FASTAPI_AVAILABLE:
        pytest.skip("FastAPI not installed")

    try:
        from forge_harness.webhook_server import AuthConfig, create_app
    except ImportError:
        pytest.skip("webhook_server not available")

    monkeypatch.setenv("FORGE_JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("FORGE_JWT_REQUIRE_AUTH", "true")

    auth_config = AuthConfig(require_auth=True, allow_localhost=False)
    app = create_app(auth_config=auth_config)

    if app is None:
        pytest.skip("Failed to create app")

    return app


@pytest.fixture
def auth_headers():
    """Create authenticated headers with admin/task permissions."""
    config = JWTAuthConfig(secret_key="test-jwt-secret")
    token = create_access_token(
        subject="test-user",
        config=config,
        permissions=["admin", "tasks:read", "tasks:write", "agents:read"],
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(test_app, auth_headers):
    """Create FastAPI test client."""
    client = TestClient(test_app)
    client.headers.update(auth_headers)
    return client


# ============================================================================
# POST /api/tasks/{task_id}/dispatch
# ============================================================================


def test_dispatch_task_endpoint_exists(client):
    """Test that dispatch endpoint exists (will fail with 404 if task not found)."""
    response = client.post("/api/tasks/nonexistent_task/dispatch", json={"agent_id": "agent_123"})

    # Should return 404 for nonexistent task, not 405 Method Not Allowed
    assert response.status_code in (404, 500), f"Expected 404/500, got {response.status_code}"


def test_dispatch_task_requires_agent_id(client):
    """Test that dispatch requires agent_id in request body."""
    response = client.post(
        "/api/tasks/task_123/dispatch",
        json={},  # Missing agent_id
    )

    # Should fail validation (422) or task not found (404)
    assert response.status_code in (422, 404, 500)


def test_dispatch_task_response_format(client):
    """Test dispatch task response follows standard format."""
    # This will fail with agent not found, but we can still check response structure
    response = client.post("/api/tasks/test_task/dispatch", json={"agent_id": "test_agent"})

    data = response.json()

    # With agent validation, we now get standardized api_response format
    assert "success" in data
    assert isinstance(data["success"], bool)

    # For agent not found, response should be structured error
    if response.status_code == 404:
        assert data["success"] is False
        assert "error" in data
        assert data["error"]["code"] == "AGENT_NOT_FOUND"
        assert "Agent test_agent not found" in data["error"]["message"]

    # For agent not available (status not idle/waiting)
    if response.status_code == 400:
        assert data["success"] is False
        assert "error" in data
        assert data["error"]["code"] == "AGENT_NOT_AVAILABLE"
        assert "not available" in data["error"]["message"].lower()

    # For success responses
    if data["success"]:
        assert "data" in data
        assert data["error"] is None


def test_dispatch_task_validates_agent_availability(client):
    """Test that dispatch validates agent is available (idle or waiting)."""
    # This test assumes we have an agent registered but not in idle/waiting state
    # In the test environment, we expect an error since agents won't be available
    response = client.post("/api/tasks/test_task/dispatch", json={"agent_id": "test_agent"})

    data = response.json()

    # Should fail with either AGENT_NOT_FOUND or AGENT_NOT_AVAILABLE
    # Both are acceptable outcomes for this test
    assert response.status_code in (400, 404, 500)
    if response.status_code == 400:
        assert "success" in data
        assert data["success"] is False
        if "error" in data:
            # If we got AGENT_NOT_AVAILABLE error, that's correct behavior
            assert data["error"]["code"] in ("AGENT_NOT_AVAILABLE", "AGENT_NOT_FOUND")


# ============================================================================
# GET /api/tasks/agents/available
# ============================================================================


def test_get_available_agents_endpoint_exists(client):
    """Test that available agents endpoint exists."""
    response = client.get("/api/tasks/agents/available")

    # Should succeed (200) with agents list (may be empty)
    assert response.status_code == 200


def test_get_available_agents_response_format(client):
    """Test available agents response follows standard format."""
    response = client.get("/api/tasks/agents/available")

    assert response.status_code == 200
    data = response.json()

    # Standard response format
    assert "success" in data
    assert data["success"] is True

    # Should have data with agents and count
    assert "data" in data
    assert "agents" in data["data"]
    assert "count" in data["data"]

    assert isinstance(data["data"]["agents"], list)
    assert isinstance(data["data"]["count"], int)


def test_available_agents_have_required_fields(client):
    """Test that available agents have all required fields."""
    response = client.get("/api/tasks/agents/available")

    assert response.status_code == 200
    data = response.json()

    agents = data["data"]["agents"]

    # If there are agents, verify structure
    for agent in agents:
        assert "agent_id" in agent
        assert "name" in agent
        assert "role" in agent
        assert "current_task_count" in agent
        assert isinstance(agent["current_task_count"], int)


# ============================================================================
# POST /api/tasks/reorder
# ============================================================================


def test_reorder_tasks_endpoint_exists(client):
    """Test that reorder endpoint exists."""
    response = client.post("/api/tasks/reorder", json={"task_order": ["task_1", "task_2"]})

    # Should not return 405 Method Not Allowed
    # May return 404 if tasks don't exist, but endpoint should exist
    assert response.status_code in (200, 404, 500)


def test_reorder_tasks_requires_task_order(client):
    """Test that reorder requires task_order in request body."""
    response = client.post(
        "/api/tasks/reorder",
        json={},  # Missing task_order
    )

    # Should fail validation
    assert response.status_code == 422


def test_reorder_tasks_rejects_empty_list(client):
    """Test that reorder rejects empty task list."""
    response = client.post("/api/tasks/reorder", json={"task_order": []})

    # Should return 400 Bad Request
    assert response.status_code == 400

    data = response.json()
    # FastAPI HTTPException returns {"detail": "..."}
    # Our custom api_response would return {"success": false, "error": {...}}
    if "detail" in data:
        assert "empty" in data["detail"].lower()
    else:
        assert not data["success"]
        assert "empty" in str(data).lower()


def test_reorder_tasks_response_format(client):
    """Test reorder tasks response follows standard format."""
    # Will fail with 404 since tasks don't exist, but we can check response structure
    response = client.post(
        "/api/tasks/reorder", json={"task_order": ["task_1", "task_2", "task_3"]}
    )

    data = response.json()

    # Standard response format
    assert "success" in data
    assert isinstance(data["success"], bool)

    if data["success"]:
        # Success response should have updated tasks
        assert "data" in data
        assert "updated" in data["data"]
        assert "count" in data["data"]
        assert isinstance(data["data"]["updated"], list)
        assert isinstance(data["data"]["count"], int)


def test_reorder_tasks_single_task_format(client):
    """Test reordering a single task."""
    response = client.post("/api/tasks/reorder", json={"task_order": ["task_1"]})

    data = response.json()

    # Should accept single task
    assert "success" in data
    # May fail with 404 if task doesn't exist, but format should be correct
    if not data["success"]:
        assert "error" in data or "detail" in data


def test_reorder_tasks_validates_task_ids_exist(client):
    """Test that reorder validates all task IDs exist."""
    # Try to reorder non-existent tasks
    response = client.post(
        "/api/tasks/reorder", json={"task_order": ["nonexistent_task_1", "nonexistent_task_2"]}
    )

    data = response.json()

    # Should fail with 404 since tasks don't exist
    assert response.status_code == 404
    if "detail" in data:
        assert "not found" in data["detail"].lower()
    else:
        assert not data["success"]
        assert "error" in data


def test_reorder_tasks_persists_order_field(client):
    """Test that reorder persists the order field correctly."""
    # Create tasks first
    task1_response = client.post(
        "/api/tasks",
        json={
            "subject": "Reorder baseline alpha",
            "description": "First task used for reorder persistence validation.",
            "priority": "high",
        },
    )

    task2_response = client.post(
        "/api/tasks",
        json={
            "subject": "Reorder baseline beta",
            "description": "Second task used for reorder persistence validation.",
            "priority": "medium",
        },
    )

    task3_response = client.post(
        "/api/tasks",
        json={
            "subject": "Reorder baseline gamma",
            "description": "Third task used for reorder persistence validation.",
            "priority": "low",
        },
    )

    # Extract task IDs
    task1_id = task1_response.json()["data"]["id"]
    task2_id = task2_response.json()["data"]["id"]
    task3_id = task3_response.json()["data"]["id"]

    # Reorder tasks in reverse order
    reorder_response = client.post(
        "/api/tasks/reorder", json={"task_order": [task3_id, task2_id, task1_id]}
    )

    # Verify reorder succeeded
    assert reorder_response.status_code == 200
    reorder_data = reorder_response.json()
    assert reorder_data["success"] is True
    assert reorder_data["data"]["count"] == 3

    # Verify order field was set correctly by retrieving each task
    task1_updated = client.get(f"/api/tasks/{task1_id}").json()["data"]
    task2_updated = client.get(f"/api/tasks/{task2_id}").json()["data"]
    task3_updated = client.get(f"/api/tasks/{task3_id}").json()["data"]

    # task3_id is at index 0, so order should be 0
    assert task3_updated["order"] == 0
    # task2_id is at index 1, so order should be 1
    assert task2_updated["order"] == 1
    # task1_id is at index 2, so order should be 2
    assert task1_updated["order"] == 2

    # Verify tasks are returned in correct order when listing
    list_response = client.get("/api/tasks")
    list_data = list_response.json()["data"]["tasks"]

    # Find our tasks in the list
    task_ids_in_order = [t["id"] for t in list_data if t["id"] in [task1_id, task2_id, task3_id]]

    # They should be in the reordered sequence
    assert task_ids_in_order == [task3_id, task2_id, task1_id]
