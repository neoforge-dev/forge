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
    from unittest.mock import patch

    with patch("forge_harness.webhook_server.AuthConfig.from_env", return_value=auth_config):
        return create_app(auth_config=auth_config)


def test_cc_p0_006_kill_agent(app, auth_headers):
    client = TestClient(app)

    # 1. Register an agent
    reg_resp = client.post(
        "/api/agents/register",
        json={"role": "builder", "project": "test", "task": "to-be-killed"},
        headers=auth_headers,
    )
    agent_id = reg_resp.json()["data"]["session_id"]

    # 2. Try kill without confirmation_token
    resp = client.post(
        f"/api/agents/{agent_id}/kill", json={"reason": "test"}, headers=auth_headers
    )
    assert resp.status_code == 422  # Validation error

    # 3. Try kill with wrong confirmation_token
    resp = client.post(
        f"/api/agents/{agent_id}/kill", json={"confirmation_token": "WRONG"}, headers=auth_headers
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_CONFIRMATION"

    # 4. Kill with correct token
    resp = client.post(
        f"/api/agents/{agent_id}/kill", json={"confirmation_token": "CONFIRM"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["new_status"] == "failed"


def test_cc_p0_006_logs_endpoint(app, auth_headers):
    client = TestClient(app)

    # 1. Register an agent
    reg_resp = client.post(
        "/api/agents/register",
        json={"role": "builder", "project": "test", "task": "logs-test"},
        headers=auth_headers,
    )
    agent_id = reg_resp.json()["data"]["session_id"]

    # 2. Get logs
    resp = client.get(f"/api/agents/{agent_id}/logs", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "logs" in data["data"]
    assert "count" in data["data"]


def test_cc_p0_006_export_endpoint(app, auth_headers):
    client = TestClient(app)

    # 1. Register an agent
    reg_resp = client.post(
        "/api/agents/register",
        json={"role": "builder", "project": "test", "task": "export-test"},
        headers=auth_headers,
    )
    agent_id = reg_resp.json()["data"]["session_id"]

    # 2. Get export (via alias)
    resp = client.get(f"/api/agents/{agent_id}/export", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["agent_id"] == agent_id
    assert "terminal_output" in data["data"]
