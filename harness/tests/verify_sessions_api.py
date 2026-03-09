from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from forge_harness.webhook_server import AuthConfig, create_app


@pytest.fixture
def app():
    auth_config = AuthConfig(require_auth=False)
    return create_app(auth_config=auth_config)


@patch("forge_harness.session_tracker.SessionTracker.get_sessions_summary")
def test_list_active_sessions_data(mock_summary, app):
    # Mock data from session tracker
    mock_summary.return_value = {
        "total": 1,
        "by_status": {"active": 1},
        "by_type": {"claude": 1},
        "sessions": [
            {
                "session_name": "forge:test",
                "window_name": "test",
                "agent_type": "claude",
                "status": "active",
                "terminal_output": "Some recent output from the agent...",
                "domain": "test-domain",
                "project": "test-project",
                "current_task": "test-task",
                "started_at": "2026-02-04T10:00:00",
                "last_activity": "2026-02-04T10:05:00",
            }
        ],
    }

    client = TestClient(app)
    response = client.get("/api/sessions/active")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert len(data["data"]) == 1

    session = data["data"][0]
    assert session["session_name"] == "forge:test"
    assert session["window_name"] == "test"
    assert session["agent_type"] == "claude"
    assert session["status"] == "active"
    assert "context_usage" in session
    assert session["terminal_output"] == "Some recent output from the agent..."
    # context_usage should be calculated: len("Some recent output from the agent...") is 37
    # (37 / 4000) * 100 = 0.925 -> int is 0
    assert session["context_usage"] == 0


def test_list_active_sessions_auth(app):
    # Enable auth
    from forge_harness.webhook_server import AuthConfig

    auth_config = AuthConfig(bearer_token="secret", require_auth=True, allow_localhost=False)

    with patch("forge_harness.webhook_server.AuthConfig.from_env", return_value=auth_config):
        # We need to recreate the app or mock the dependency
        app_with_auth = create_app(auth_config=auth_config)
        client = TestClient(app_with_auth)

        # Unauthorized
        response = client.get("/api/sessions/active")
        assert response.status_code == 401

        # Authorized
        response = client.get("/api/sessions/active", headers={"Authorization": "Bearer secret"})
        assert response.status_code == 200
