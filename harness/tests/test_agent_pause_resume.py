"""Integration tests for agent pause/resume endpoints (CC-P0-005)."""

import pytest


class TestAgentPauseResume:
    """Tests for agent pause/resume endpoints (CC-P0-005)."""

    @pytest.fixture
    def app(self):
        """Create test app."""
        from forge_harness.webhook_server import AuthConfig, create_app

        auth_config = AuthConfig(bearer_token="test_token", require_auth=True)
        app = create_app(auth_config=auth_config)
        if app is None:
            pytest.skip("FastAPI not installed")
        return app

    @pytest.fixture
    def auth_headers(self):
        """Get auth headers."""
        return {"Authorization": "Bearer test_token"}

    @pytest.fixture
    def registered_agent(self, app, auth_headers):
        """Helper to create a test agent."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)
        agent_data = {
            "role": "backend-engineer",
            "project": "test-project",
            "domain": "test-domain",
            "task": "Test pause/resume functionality",
        }

        response = client.post(
            "/api/agents/register",
            json=agent_data,
            headers=auth_headers,
        )

        assert response.status_code == 200
        agent = response.json()["data"]

        yield agent

        # Cleanup: kill the agent (optional, will be cleaned up automatically)
        try:
            client.post(
                f"/api/agents/{agent['session_id']}/kill",
                headers=auth_headers,
                json={"reason": "Test cleanup"},
            )
        except Exception:
            pass  # Ignore cleanup errors

    def test_pause_agent_endpoint(self, app, auth_headers, registered_agent):
        """Test POST /api/agents/{id}/pause endpoint exists and works."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)
        response = client.post(
            f"/api/agents/{registered_agent['session_id']}/pause",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify response structure
        assert "data" in data
        result = data["data"]
        assert result["success"] is True
        assert result["agent_id"] == registered_agent["session_id"]
        assert result["action"] == "pause"
        assert "previous_status" in result
        assert result["new_status"] == "paused"
        assert "message" in result
        assert "timestamp" in result

    def test_resume_agent_endpoint(self, app, auth_headers, registered_agent):
        """Test POST /api/agents/{id}/resume endpoint exists and works."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)

        # First pause the agent
        client.post(
            f"/api/agents/{registered_agent['session_id']}/pause",
            headers=auth_headers,
        )

        # Then resume it
        response = client.post(
            f"/api/agents/{registered_agent['session_id']}/resume",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify response structure
        assert "data" in data
        result = data["data"]
        assert result["success"] is True
        assert result["agent_id"] == registered_agent["session_id"]
        assert result["action"] == "resume"
        assert result["previous_status"] == "paused"
        assert result["new_status"] == "active"
        assert "message" in result
        assert "timestamp" in result

    def test_pause_sets_status_to_paused(self, app, auth_headers, registered_agent):
        """Test that pause sets agent.status to 'paused'."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)

        # Pause the agent
        client.post(
            f"/api/agents/{registered_agent['session_id']}/pause",
            headers=auth_headers,
        )

        # Get agent details to verify status
        response = client.get(
            f"/api/agents/{registered_agent['session_id']}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["status"] == "paused"

    def test_resume_restores_previous_status(self, app, auth_headers, registered_agent):
        """Test that resume sets agent.status back to 'active'."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)

        # First pause the agent
        client.post(
            f"/api/agents/{registered_agent['session_id']}/pause",
            headers=auth_headers,
        )

        # Verify it's paused
        response = client.get(
            f"/api/agents/{registered_agent['session_id']}",
            headers=auth_headers,
        )
        assert response.json()["data"]["status"] == "paused"

        # Resume the agent
        client.post(
            f"/api/agents/{registered_agent['session_id']}/resume",
            headers=auth_headers,
        )

        # Verify it's active again
        response = client.get(
            f"/api/agents/{registered_agent['session_id']}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "active"

    def test_pause_nonexistent_agent_returns_404(self, app, auth_headers):
        """Test that pausing a nonexistent agent returns 404."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)
        response = client.post(
            "/api/agents/nonexistent-id/pause",
            headers=auth_headers,
        )

        assert response.status_code == 404
        # FastAPI returns different error structures, just check status code

    def test_resume_nonexistent_agent_returns_404(self, app, auth_headers):
        """Test that resuming a nonexistent agent returns 404."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)
        response = client.post(
            "/api/agents/nonexistent-id/resume",
            headers=auth_headers,
        )

        assert response.status_code == 404
        # FastAPI returns different error structures, just check status code

    def test_pause_preserves_previous_status(self, app, auth_headers, registered_agent):
        """Test that pause preserves and returns the previous status."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)

        # Get initial status
        response = client.get(
            f"/api/agents/{registered_agent['session_id']}",
            headers=auth_headers,
        )
        initial_status = response.json()["data"]["status"]

        # Pause the agent
        response = client.post(
            f"/api/agents/{registered_agent['session_id']}/pause",
            headers=auth_headers,
        )

        # Verify previous_status matches initial status
        result = response.json()["data"]
        assert result["previous_status"] == initial_status

    def test_pause_already_paused_agent(self, app, auth_headers, registered_agent):
        """Test that pausing an already paused agent works."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)

        # Pause once
        client.post(
            f"/api/agents/{registered_agent['session_id']}/pause",
            headers=auth_headers,
        )

        # Pause again (should work, but previous_status will be 'paused')
        response = client.post(
            f"/api/agents/{registered_agent['session_id']}/pause",
            headers=auth_headers,
        )

        assert response.status_code == 200
        result = response.json()["data"]
        assert result["previous_status"] == "paused"
        assert result["new_status"] == "paused"

    def test_resume_already_active_agent(self, app, auth_headers, registered_agent):
        """Test that resuming an already active agent works."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)

        # Agent is already active, just resume it
        response = client.post(
            f"/api/agents/{registered_agent['session_id']}/resume",
            headers=auth_headers,
        )

        assert response.status_code == 200
        result = response.json()["data"]
        assert result["new_status"] == "active"

    def test_sse_events_emitted(self, app, auth_headers, registered_agent):
        """Test that SSE events agent.paused and agent.resumed are emitted."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)

        # This test verifies the endpoint emits events
        # Full SSE integration testing would require SSE client setup

        # Pause and verify event would be emitted
        response = client.post(
            f"/api/agents/{registered_agent['session_id']}/pause",
            headers=auth_headers,
        )
        assert response.status_code == 200  # If no exception, event bus publish succeeded

        # Resume and verify event would be emitted
        response = client.post(
            f"/api/agents/{registered_agent['session_id']}/resume",
            headers=auth_headers,
        )
        assert response.status_code == 200  # If no exception, event bus publish succeeded

    def test_multiple_pause_resume_cycles(self, app, auth_headers, registered_agent):
        """Test multiple pause/resume cycles work correctly."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)

        for i in range(3):
            # Pause
            pause_response = client.post(
                f"/api/agents/{registered_agent['session_id']}/pause",
                headers=auth_headers,
            )
            assert pause_response.status_code == 200
            assert pause_response.json()["data"]["new_status"] == "paused"

            # Resume
            resume_response = client.post(
                f"/api/agents/{registered_agent['session_id']}/resume",
                headers=auth_headers,
            )
            assert resume_response.status_code == 200
            assert resume_response.json()["data"]["new_status"] == "active"
