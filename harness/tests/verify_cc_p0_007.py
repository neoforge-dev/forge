import json
from pathlib import Path

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


def test_cc_p0_007_agent_handoff(app, auth_headers):
    client = TestClient(app)

    # 1. Register an agent
    reg_resp = client.post(
        "/api/agents/register",
        json={"role": "builder", "project": "test", "task": "handoff-test"},
        headers=auth_headers,
    )
    agent_id = reg_resp.json()["data"]["session_id"]

    # 2. Initiate handoff
    resp = client.post(
        f"/api/agents/{agent_id}/handoff", json={"reason": "Changing shift"}, headers=auth_headers
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    handoff_id = data["handoff_id"]
    assert data["status"] == "initiated"

    # 3. Verify agent status
    agent_resp = client.get(f"/api/agents/{agent_id}", headers=auth_headers)
    assert agent_resp.json()["data"]["status"] == "handing_off"

    # 4. Verify handoff document exists
    handoff_dir = Path.cwd() / ".forge/handoffs"
    handoff_path = handoff_dir / f"{handoff_id}.json"
    assert handoff_path.exists()

    doc = json.loads(handoff_path.read_text())
    assert doc["handoff_id"] == handoff_id
    assert doc["source_agent_id"] == agent_id
    assert doc["reason"] == "Changing shift"
