"""Tests for GET /api/agents/{agent_id}/logs endpoint.

Covers:
- Successful log retrieval (tmux path)
- Successful log retrieval (activity-events fallback path)
- Agent not found returns 404
- Default limit (100) is applied
- Custom limit parameter is respected
- Empty logs response when no tmux and no matching events
- Tmux failure falls back gracefully to activity events
- Log entries have the correct schema
- Limit bounds: only the last N lines are returned from tmux
- Authentication is required
"""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SUBPROCESS_PATCH = "forge_harness.webhook_server.api.agents.subprocess.run"
REGISTRY_PATCH = "forge_harness.webhook_server.api.agents.get_agent_registry"
EVENT_BUS_PATCH = "forge_harness.webhook_server.api.agents.get_event_bus"


def _make_agent(agent_id: str = "agent-abc", tmux_session: str | None = None) -> Mock:
    """Create a minimal mock agent object."""
    agent = Mock()
    agent.id = agent_id
    agent.tmux_session = tmux_session
    agent.status = "active"
    return agent


def _make_registry(agent: Mock | None = None) -> Mock:
    """Create a mock agent registry that returns *agent* for any lookup."""
    registry = Mock()
    registry.get = Mock(return_value=agent)
    return registry


def _make_event_bus(events: list | None = None) -> AsyncMock:
    """Create a mock event bus with optional recent events."""
    bus = AsyncMock()
    bus.get_recent_events = AsyncMock(return_value=events or [])
    return bus


def _run(coro):
    """Run a coroutine synchronously, creating a new loop if necessary."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Unit tests (call handler directly, mock all external dependencies)
# ---------------------------------------------------------------------------


class TestGetAgentLogsUnit:
    """Unit tests for the get_agent_logs handler function."""

    def test_agent_not_found_raises_404(self):
        """When no agent exists for the given ID a 404 HTTPException is raised."""
        from forge_harness.webhook_server.api.agents import get_agent_logs

        registry = _make_registry(agent=None)

        with patch(REGISTRY_PATCH, new=AsyncMock(return_value=registry)):
            with pytest.raises(HTTPException) as exc_info:
                _run(get_agent_logs(agent_id="missing-agent", limit=100))

        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail.lower()

    def test_successful_tmux_log_retrieval(self):
        """When tmux capture succeeds the logs are populated from pane output."""
        from forge_harness.webhook_server.api.agents import get_agent_logs

        agent = _make_agent(agent_id="agent-123", tmux_session="forge:kimi")
        registry = _make_registry(agent=agent)

        pane_output = "line one\nline two\nline three\n"
        mock_proc = Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = pane_output

        with (
            patch(REGISTRY_PATCH, new=AsyncMock(return_value=registry)),
            patch(SUBPROCESS_PATCH, return_value=mock_proc),
        ):
            response = _run(get_agent_logs(agent_id="agent-123", limit=100))

        assert response.status_code == 200
        body = json.loads(response.body.decode())
        assert body["success"] is True
        data = body["data"]
        assert data["count"] == 3
        assert len(data["logs"]) == 3
        # Verify log entry schema
        entry = data["logs"][0]
        assert "timestamp" in entry
        assert entry["level"] == "info"
        assert entry["message"] == "line one"
        assert entry["source"] == "forge:kimi"
        assert "metadata" in entry

    def test_default_limit_caps_tmux_output(self):
        """Without an explicit limit (100), only the last 100 tmux lines are returned."""
        from forge_harness.webhook_server.api.agents import get_agent_logs

        agent = _make_agent(agent_id="agent-lim", tmux_session="forge:test")
        registry = _make_registry(agent=agent)

        # Produce 150 non-empty lines in pane output
        lines = [f"line {i}" for i in range(150)]
        mock_proc = Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = "\n".join(lines) + "\n"

        with (
            patch(REGISTRY_PATCH, new=AsyncMock(return_value=registry)),
            patch(SUBPROCESS_PATCH, return_value=mock_proc),
        ):
            # Call with the default limit of 100
            response = _run(get_agent_logs(agent_id="agent-lim", limit=100))

        body = json.loads(response.body.decode())
        data = body["data"]
        assert data["count"] == 100
        assert len(data["logs"]) == 100
        # The last 100 lines (50-149) should be present
        assert data["logs"][0]["message"] == "line 50"
        assert data["logs"][-1]["message"] == "line 149"

    def test_custom_limit_parameter(self):
        """A custom limit restricts how many log lines are returned."""
        from forge_harness.webhook_server.api.agents import get_agent_logs

        agent = _make_agent(agent_id="agent-clim", tmux_session="forge:test")
        registry = _make_registry(agent=agent)

        lines = [f"line {i}" for i in range(50)]
        mock_proc = Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = "\n".join(lines) + "\n"

        with (
            patch(REGISTRY_PATCH, new=AsyncMock(return_value=registry)),
            patch(SUBPROCESS_PATCH, return_value=mock_proc),
        ):
            response = _run(get_agent_logs(agent_id="agent-clim", limit=10))

        body = json.loads(response.body.decode())
        data = body["data"]
        assert data["count"] == 10
        assert len(data["logs"]) == 10
        # The last 10 lines (40-49)
        assert data["logs"][0]["message"] == "line 40"

    def test_empty_logs_when_no_tmux_and_no_events(self):
        """When there is no tmux session and no matching activity events, logs is empty."""
        from forge_harness.webhook_server.api.agents import get_agent_logs

        agent = _make_agent(agent_id="agent-empty", tmux_session=None)
        registry = _make_registry(agent=agent)
        event_bus = _make_event_bus(events=[])

        with (
            patch(REGISTRY_PATCH, new=AsyncMock(return_value=registry)),
            patch(EVENT_BUS_PATCH, new=AsyncMock(return_value=event_bus)),
        ):
            response = _run(get_agent_logs(agent_id="agent-empty", limit=100))

        body = json.loads(response.body.decode())
        assert body["success"] is True
        data = body["data"]
        assert data["count"] == 0
        assert data["logs"] == []

    def test_tmux_failure_falls_back_to_activity_events(self):
        """When tmux capture raises, logs fall back to activity events for the agent."""
        from forge_harness.webhook_server.api.agents import get_agent_logs

        agent = _make_agent(agent_id="agent-fall", tmux_session="forge:broken")
        registry = _make_registry(agent=agent)

        now = datetime.now(UTC).isoformat()
        activity_events = [
            {
                "timestamp": now,
                "source": "agent-fall",
                "data": {
                    "session_id": "agent-fall",
                    "event_type": "info",
                    "content": "Activity message 1",
                    "metadata": {},
                },
            },
            {
                "timestamp": now,
                "source": "agent-fall",
                "data": {
                    "session_id": "agent-fall",
                    "event_type": "error",
                    "content": "Activity error",
                    "metadata": {},
                },
            },
            # Event for a different agent — must be filtered out
            {
                "timestamp": now,
                "source": "other-agent",
                "data": {
                    "session_id": "other-agent",
                    "event_type": "info",
                    "content": "Should not appear",
                    "metadata": {},
                },
            },
        ]
        event_bus = _make_event_bus(events=activity_events)

        with (
            patch(REGISTRY_PATCH, new=AsyncMock(return_value=registry)),
            patch(SUBPROCESS_PATCH, side_effect=RuntimeError("tmux not available")),
            patch(EVENT_BUS_PATCH, new=AsyncMock(return_value=event_bus)),
        ):
            response = _run(get_agent_logs(agent_id="agent-fall", limit=100))

        body = json.loads(response.body.decode())
        assert body["success"] is True
        data = body["data"]
        assert data["count"] == 2
        assert data["logs"][0]["message"] == "Activity message 1"
        assert data["logs"][0]["level"] == "info"
        assert data["logs"][1]["message"] == "Activity error"
        assert data["logs"][1]["level"] == "error"

    def test_tmux_non_zero_exit_falls_back_to_empty(self):
        """When tmux returns non-zero exit code, logs fall back (empty when no events)."""
        from forge_harness.webhook_server.api.agents import get_agent_logs

        agent = _make_agent(agent_id="agent-nz", tmux_session="forge:gone")
        registry = _make_registry(agent=agent)
        event_bus = _make_event_bus(events=[])

        mock_proc = Mock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""

        with (
            patch(REGISTRY_PATCH, new=AsyncMock(return_value=registry)),
            patch(SUBPROCESS_PATCH, return_value=mock_proc),
            patch(EVENT_BUS_PATCH, new=AsyncMock(return_value=event_bus)),
        ):
            response = _run(get_agent_logs(agent_id="agent-nz", limit=100))

        body = json.loads(response.body.decode())
        assert body["success"] is True
        assert body["data"]["count"] == 0
        assert body["data"]["logs"] == []

    def test_response_has_standard_api_envelope(self):
        """Response follows the standard {success, data, error, timestamp} envelope."""
        from forge_harness.webhook_server.api.agents import get_agent_logs

        agent = _make_agent(agent_id="agent-env", tmux_session=None)
        registry = _make_registry(agent=agent)
        event_bus = _make_event_bus(events=[])

        with (
            patch(REGISTRY_PATCH, new=AsyncMock(return_value=registry)),
            patch(EVENT_BUS_PATCH, new=AsyncMock(return_value=event_bus)),
        ):
            response = _run(get_agent_logs(agent_id="agent-env", limit=100))

        body = json.loads(response.body.decode())
        assert "success" in body
        assert "data" in body
        assert "error" in body
        assert "timestamp" in body
        assert body["success"] is True
        assert body["error"] is None


# ---------------------------------------------------------------------------
# Integration tests (via TestClient + create_app)
# ---------------------------------------------------------------------------


class TestGetAgentLogsIntegration:
    """Integration tests using FastAPI TestClient via create_app."""

    @pytest.fixture
    def app(self):
        """Create a test application."""
        from forge_harness.webhook_server import AuthConfig, create_app

        auth_config = AuthConfig(bearer_token="test_token", require_auth=True)
        app = create_app(auth_config=auth_config)
        if app is None:
            pytest.skip("FastAPI not installed")
        return app

    @pytest.fixture
    def auth_headers(self):
        return {"Authorization": "Bearer test_token"}

    @pytest.fixture
    def registered_agent_id(self, app, auth_headers):
        """Register a throwaway agent and return its session_id."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)
        resp = client.post(
            "/api/agents/register",
            json={
                "role": "backend-engineer",
                "project": "test-project",
                "task": "Integration test for logs endpoint",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        agent_id = resp.json()["data"]["session_id"]
        yield agent_id
        # Cleanup
        try:
            client.post(f"/api/agents/{agent_id}/kill", headers=auth_headers, json={})
        except Exception:
            pass

    def test_logs_endpoint_returns_200_for_existing_agent(
        self, app, auth_headers, registered_agent_id
    ):
        """GET /api/agents/{id}/logs returns 200 for a registered agent."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)
        with patch(SUBPROCESS_PATCH, side_effect=FileNotFoundError("tmux not present")):
            resp = client.get(
                f"/api/agents/{registered_agent_id}/logs",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "logs" in body["data"]
        assert "count" in body["data"]

    def test_logs_endpoint_404_for_unknown_agent(self, app, auth_headers):
        """GET /api/agents/{id}/logs returns 404 for an unknown agent ID."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)
        resp = client.get(
            "/api/agents/definitely-does-not-exist-xyz/logs",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_logs_endpoint_requires_auth(self, app):
        """GET /api/agents/{id}/logs returns 401/403 without auth token."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)
        resp = client.get("/api/agents/any-agent/logs")
        assert resp.status_code in (401, 403)

    def test_logs_endpoint_custom_limit_query_param(
        self, app, auth_headers, registered_agent_id
    ):
        """Custom limit query param is accepted and only that many lines returned."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app)

        lines = [f"line {i}" for i in range(20)]
        mock_proc = Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = "\n".join(lines) + "\n"

        with patch(SUBPROCESS_PATCH, return_value=mock_proc):
            resp = client.get(
                f"/api/agents/{registered_agent_id}/logs?limit=5",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["count"] <= 5
        assert len(body["data"]["logs"]) <= 5
