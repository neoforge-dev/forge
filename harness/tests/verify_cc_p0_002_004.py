from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from forge_harness.webhook_server import AuthConfig, create_app


@pytest.fixture
def auth_headers():
    token = "test-token"
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def app():
    auth_config = AuthConfig(bearer_token="test-token", require_auth=True, allow_localhost=False)
    # We need to mock get_agent_registry and TaskQueueHandler to avoid Redis dependency if possible,
    # but the current app setup in webhook_server.py does it at module level or during create_app.
    with patch("forge_harness.webhook_server.AuthConfig.from_env", return_value=auth_config):
        return create_app(auth_config=auth_config)


@pytest.mark.asyncio
async def test_cc_p0_002_task_dispatch(app, auth_headers):
    client = TestClient(app)

    # 1. Create a task
    create_resp = client.post(
        "/api/tasks", json={"subject": "Dispatch Test", "description": "Test"}, headers=auth_headers
    )
    assert create_resp.status_code == 200
    task_id = create_resp.json()["data"]["id"]

    # 2. Register an agent
    reg_resp = client.post(
        "/api/agents/register",
        json={"role": "builder", "project": "test", "task": "idle"},
        headers=auth_headers,
    )
    assert reg_resp.status_code == 200
    agent_id = reg_resp.json()["data"]["session_id"]

    # Set agent status to idle (it defaults to active)
    from forge_harness.webhook_server import get_agent_registry

    registry = get_agent_registry()
    agent = registry.get(agent_id)
    agent.status = "idle"

    # 3. Dispatch task
    dispatch_resp = client.post(
        f"/api/tasks/{task_id}/dispatch", json={"agent_id": agent_id}, headers=auth_headers
    )

    assert dispatch_resp.status_code == 200
    data = dispatch_resp.json()
    assert data["success"] is True
    assert data["data"]["claimed_by"] == agent_id
    assert data["data"]["status"] == "assigned"


@pytest.mark.asyncio
async def test_cc_p0_003_available_agents(app, auth_headers):
    client = TestClient(app)

    # Register an idle agent
    client.post(
        "/api/agents/register",
        json={"role": "available-role", "project": "test", "task": "idle"},
        headers=auth_headers,
    )
    # Registry is singleton, so we can find it
    from forge_harness.webhook_server import get_agent_registry

    registry = get_agent_registry()
    for agent in registry.list_active():
        if agent.role == "available-role":
            agent.status = "idle"
            agent_id = agent.id
            break

    # Check available agents
    resp = client.get("/api/tasks/agents/available", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True

    available_ids = [a["agent_id"] for a in data["data"]["agents"]]
    assert agent_id in available_ids

    # Filter by role
    resp = client.get("/api/tasks/agents/available?role=available-role", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert all(a["role"] == "available-role" for a in data["data"]["agents"])


@pytest.mark.asyncio
async def test_cc_p0_004_task_reorder(app, auth_headers):
    client = TestClient(app)

    # 1. Create two tasks
    t1 = client.post(
        "/api/tasks", json={"subject": "T1", "description": "D1"}, headers=auth_headers
    ).json()["data"]["id"]
    t2 = client.post(
        "/api/tasks", json={"subject": "T2", "description": "D2"}, headers=auth_headers
    ).json()["data"]["id"]

    # 2. Reorder
    reorder_resp = client.post(
        "/api/tasks/reorder", json={"task_ids": [t2, t1]}, headers=auth_headers
    )

    assert reorder_resp.status_code == 200
    data = reorder_resp.json()
    assert data["success"] is True

    # 3. Verify order
    v2 = client.get(f"/api/tasks/{t2}", headers=auth_headers).json()["data"]
    v1 = client.get(f"/api/tasks/{t1}", headers=auth_headers).json()["data"]

    assert v2["order"] == 0
    assert v1["order"] == 1
