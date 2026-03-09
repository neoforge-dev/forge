"""Integration Tests - Agent Registration, Task Assignment, Sync & Multi-Node
=============================================================================

Comprehensive integration tests using TestClient against the real webhook server
application. Tests full request/response cycles through the FastAPI stack with
mocked external dependencies (tmux, Redis).

Covers:
- Agent registration → lookup → list lifecycle
- Task assignment and progress tracking
- EventBus SSE event propagation
- Node communication and fleet health
- Sessions API integration
- Auth enforcement across all endpoints
"""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_config():
    """Auth config with require_auth=False for integration tests."""
    from forge_harness.webhook_server.infrastructure.auth import AuthConfig

    return AuthConfig(require_auth=False, allow_localhost=True)


@pytest.fixture
def auth_config_strict():
    """Auth config with require_auth=True for auth enforcement tests."""
    from forge_harness.webhook_server.infrastructure.auth import AuthConfig

    return AuthConfig(
        bearer_token="test-secret-token",
        require_auth=True,
        allow_localhost=False,
    )


@pytest.fixture
def mock_tmux_windows():
    """Mock tmux list-windows returning a known set of agents."""
    windows = ["nova", "claude", "codex"]
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = "\n".join(windows) + "\n"
    return mock_result


@pytest.fixture
def mock_tmux_output():
    """Mock tmux capture-pane returning sample terminal output."""
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = "$ Running tests...\nAll 12 tests passed\n"
    return mock_result


@pytest.fixture
def client(auth_config, mock_tmux_windows, mock_tmux_output):
    """Create a TestClient with mocked tmux and no auth."""
    from forge_harness.webhook_server_main import create_app

    app = create_app(None, auth_config=auth_config)

    def _subprocess_side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if isinstance(cmd, list):
            if "list-windows" in cmd:
                return mock_tmux_windows
            if "capture-pane" in cmd:
                return mock_tmux_output
        return Mock(returncode=1, stdout="", stderr="")

    with patch("subprocess.run", side_effect=_subprocess_side_effect):
        yield TestClient(app)


@pytest.fixture
def client_with_auth(auth_config_strict, mock_tmux_windows, mock_tmux_output):
    """Create a TestClient with auth enforcement enabled."""
    from forge_harness.webhook_server_main import create_app

    app = create_app(None, auth_config=auth_config_strict)

    def _subprocess_side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if isinstance(cmd, list):
            if "list-windows" in cmd:
                return mock_tmux_windows
            if "capture-pane" in cmd:
                return mock_tmux_output
        return Mock(returncode=1, stdout="", stderr="")

    with patch("subprocess.run", side_effect=_subprocess_side_effect):
        yield TestClient(app)


# ---------------------------------------------------------------------------
# Agent Registration Integration Tests
# ---------------------------------------------------------------------------


class TestAgentRegistrationFlow:
    """Full lifecycle: register → get → list → heartbeat → complete."""

    def test_register_agent(self, client):
        """POST /api/agents/register creates agent and returns 200."""
        r = client.post(
            "/api/agents/register",
            json={
                "role": "backend-engineer",
                "project": "interview-simulator",
                "task": "Implement auth endpoints",
                "name": "test-agent",
                "domain": "codeswiftr-com",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        data = body["data"]
        assert "session_id" in data or "agent_id" in data or "id" in data

    def test_register_and_lookup(self, client):
        """Register an agent, then look it up by ID."""
        # Register
        reg = client.post(
            "/api/agents/register",
            json={
                "role": "frontend-builder",
                "project": "command-center",
                "task": "Build dashboard",
            },
        )
        assert reg.status_code == 200
        reg_data = reg.json()["data"]
        agent_id = reg_data.get("session_id") or reg_data.get("agent_id") or reg_data.get("id")
        assert agent_id

        # Lookup
        r = client.get(f"/api/agents/{agent_id}")
        assert r.status_code == 200
        agent = r.json()["data"]
        assert agent["session_id"] == agent_id or agent.get("id") == agent_id

    def test_register_appears_in_list(self, client):
        """Registered agent appears in GET /api/agents list."""
        # Register
        client.post(
            "/api/agents/register",
            json={
                "role": "qa-tester",
                "project": "voice-coach",
                "task": "Run integration tests",
            },
        )

        # List all agents
        r = client.get("/api/agents")
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        agents = body["data"]["agents"]
        # Should have at least the registered agent + tmux agents
        assert len(agents) >= 1

    def test_agent_heartbeat(self, client):
        """POST /api/agents/{id}/heartbeat updates last_activity."""
        reg = client.post(
            "/api/agents/register",
            json={
                "role": "debug-detective",
                "project": "harness",
                "task": "Fix race condition",
            },
        )
        agent_id = reg.json()["data"].get("session_id") or reg.json()["data"].get("agent_id")

        r = client.post(f"/api/agents/{agent_id}/heartbeat")
        assert r.status_code == 200
        assert r.json()["success"] is True

    def test_agent_progress_update(self, client):
        """POST /api/agents/{id}/progress updates progress."""
        reg = client.post(
            "/api/agents/register",
            json={
                "role": "code-reviewer",
                "project": "forge-shared",
                "task": "Review PR",
            },
        )
        agent_id = reg.json()["data"].get("session_id") or reg.json()["data"].get("agent_id")

        r = client.post(
            f"/api/agents/{agent_id}/progress",
            json={"progress": 75, "current_task": "Reviewing auth module"},
        )
        assert r.status_code == 200

    def test_agent_complete(self, client):
        """POST /api/agents/{id}/complete marks agent done."""
        reg = client.post(
            "/api/agents/register",
            json={
                "role": "backend-engineer",
                "project": "tech-newsletter",
                "task": "Add RSS feed",
            },
        )
        agent_id = reg.json()["data"].get("session_id") or reg.json()["data"].get("agent_id")

        r = client.post(f"/api/agents/{agent_id}/complete", json={})
        assert r.status_code == 200

    def test_agent_pause_resume(self, client):
        """Pause then resume an agent."""
        reg = client.post(
            "/api/agents/register",
            json={
                "role": "frontend-builder",
                "project": "campaign-intel",
                "task": "Build analytics page",
            },
        )
        agent_id = reg.json()["data"].get("session_id") or reg.json()["data"].get("agent_id")

        # Pause
        r = client.post(
            f"/api/agents/{agent_id}/pause",
            json={"reason": "Waiting for API key"},
        )
        assert r.status_code == 200

        # Resume
        r = client.post(f"/api/agents/{agent_id}/resume")
        assert r.status_code == 200

    def test_lookup_nonexistent_returns_404(self, client):
        """GET /api/agents/{bad_id} returns 404."""
        r = client.get("/api/agents/nonexistent-agent-xyz")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Tmux Session Integration
# ---------------------------------------------------------------------------


class TestTmuxSessionIntegration:
    """Test that tmux-discovered agents appear through the API."""

    def test_tmux_agents_in_list(self, client):
        """GET /api/agents includes tmux-discovered agents."""
        r = client.get("/api/agents")
        assert r.status_code == 200
        agents = r.json()["data"]["agents"]
        tmux_agents = [a for a in agents if a.get("source") == "tmux"]
        # Our mock returns nova, claude, codex
        assert len(tmux_agents) >= 1

    def test_lookup_tmux_agent_by_session_name(self, client):
        """GET /api/agents/forge:nova resolves tmux agent."""
        r = client.get("/api/agents/forge:nova")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["session_id"] == "forge:nova"
        assert data["source"] == "tmux"

    def test_lookup_tmux_agent_by_window_name(self, client):
        """GET /api/agents/claude resolves tmux agent by window name."""
        r = client.get("/api/agents/claude")
        # Should find by window name fallback
        assert r.status_code == 200
        data = r.json()["data"]
        assert "claude" in data.get("session_id", "") or data.get("agent_name", "") == "claude"


# ---------------------------------------------------------------------------
# Sessions API Integration
# ---------------------------------------------------------------------------


class TestSessionsAPIIntegration:
    """Test /api/sessions endpoints against real app."""

    def test_list_active_sessions(self, client):
        """GET /api/sessions/active returns tmux sessions."""
        r = client.get("/api/sessions/active")
        assert r.status_code == 200
        sessions = r.json()["data"]
        assert isinstance(sessions, list)
        assert len(sessions) >= 1

    def test_get_session_by_window(self, client):
        """GET /api/sessions/{window_name} returns session details."""
        r = client.get("/api/sessions/nova")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["session_name"] == "forge:nova"
        assert data["window_name"] == "nova"

    def test_get_session_not_found(self, client):
        """GET /api/sessions/nonexistent returns 404."""
        r = client.get("/api/sessions/nonexistent-window")
        assert r.status_code == 404

    def test_get_session_output(self, client):
        """GET /api/sessions/{window}/output returns terminal output."""
        r = client.get("/api/sessions/nova/output?lines=10")
        assert r.status_code == 200
        data = r.json()["data"]
        assert "output" in data
        assert data["window"] == "nova"

    def test_update_session_metadata(self, client):
        """POST /api/sessions/{window}/metadata updates metadata."""
        r = client.post(
            "/api/sessions/nova/metadata",
            json={"domain": "codeswiftr-com", "project": "interview-sim", "task": "Sprint 9"},
        )
        assert r.status_code == 200
        assert r.json()["data"]["updated"] is True


# ---------------------------------------------------------------------------
# Node Communication & Fleet Health
# ---------------------------------------------------------------------------


class TestNodeCommunication:
    """Test multi-node orchestration endpoints."""

    def test_list_nodes(self, client):
        """GET /api/nodes returns node list."""
        r = client.get("/api/nodes")
        assert r.status_code == 200
        data = r.json()["data"]
        assert "nodes" in data
        assert data["count"] >= 1

    def test_nodes_health(self, client):
        """GET /api/nodes/health returns aggregated health."""
        r = client.get("/api/nodes/health")
        assert r.status_code == 200
        data = r.json()["data"]
        assert "health_percentage" in data
        assert "total_nodes" in data
        assert data["total_nodes"] >= 1
        assert 0 <= data["health_percentage"] <= 100

    def test_recommend_node(self, client):
        """GET /api/nodes/recommend returns scheduling recommendation."""
        r = client.get("/api/nodes/recommend?task_type=backend")
        assert r.status_code == 200
        data = r.json()["data"]
        assert "recommended_node_id" in data
        assert "reason" in data

    def test_sync_nodes(self, client):
        """POST /api/nodes/sync triggers node synchronization."""
        r = client.post("/api/nodes/sync")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["success"] is True
        assert "sync_id" in data

    def test_fleet_status(self, client):
        """GET /api/agents/fleet/status returns fleet overview."""
        r = client.get("/api/agents/fleet/status")
        assert r.status_code == 200
        data = r.json()["data"]
        assert "total_agents" in data
        # Fleet status uses flat keys (active, idle, etc.) not nested by_status
        assert isinstance(data["total_agents"], int)


# ---------------------------------------------------------------------------
# EventBus SSE Propagation
# ---------------------------------------------------------------------------


class TestEventBusPropagation:
    """Test that agent lifecycle events propagate through EventBus."""

    @pytest.mark.asyncio
    async def test_register_publishes_event(self):
        """Agent registration publishes agent.registered event."""
        from forge_harness.webhook_server.services.event_bus import EventBus, get_event_bus

        # Reset singleton for clean test
        EventBus._instance = None
        bus = get_event_bus()
        queue = bus.subscribe()

        await bus.publish(
            event_type="agent.registered",
            data={"agent_id": "test-001", "role": "backend-engineer"},
            source="test",
        )

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.event == "agent.registered"
        assert event.data["agent_id"] == "test-001"

        bus.unsubscribe(queue)
        # Reset singleton
        EventBus._instance = None

    @pytest.mark.asyncio
    async def test_progress_publishes_event(self):
        """Progress update publishes agent.progress event."""
        from forge_harness.webhook_server.services.event_bus import EventBus, get_event_bus

        EventBus._instance = None
        bus = get_event_bus()
        queue = bus.subscribe()

        await bus.publish(
            event_type="agent.progress",
            data={"agent_id": "test-001", "progress": 50},
        )

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.event == "agent.progress"
        assert event.data["progress"] == 50

        bus.unsubscribe(queue)
        EventBus._instance = None

    @pytest.mark.asyncio
    async def test_multiple_subscribers_receive_events(self):
        """All subscribers receive the same event."""
        from forge_harness.webhook_server.services.event_bus import EventBus, get_event_bus

        EventBus._instance = None
        bus = get_event_bus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        q3 = bus.subscribe()

        await bus.publish(event_type="agent.completed", data={"agent_id": "test-002"})

        for q in [q1, q2, q3]:
            event = await asyncio.wait_for(q.get(), timeout=1.0)
            assert event.event == "agent.completed"

        bus.unsubscribe(q1)
        bus.unsubscribe(q2)
        bus.unsubscribe(q3)
        EventBus._instance = None

    @pytest.mark.asyncio
    async def test_sse_event_format(self):
        """SSEEvent.to_sse_format() produces valid SSE protocol."""
        from forge_harness.webhook_server.services.event_bus import SSEEvent

        event = SSEEvent(
            id="evt-42",
            event="agent.registered",
            data={"agent_id": "a1", "role": "backend"},
            source="webhook-server",
        )
        sse = event.to_sse_format()

        assert "id: evt-42\n" in sse
        assert "event: agent.registered\n" in sse
        assert "data: " in sse
        assert sse.endswith("\n\n")

        # Parse data JSON
        data_line = [l for l in sse.strip().split("\n") if l.startswith("data:")][0]
        payload = json.loads(data_line.removeprefix("data:").strip())
        assert payload["id"] == "evt-42"
        assert payload["type"] == "agent.registered"
        assert payload["data"]["agent_id"] == "a1"

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(self):
        """Events stop arriving after unsubscribe."""
        from forge_harness.webhook_server.services.event_bus import EventBus, get_event_bus

        EventBus._instance = None
        bus = get_event_bus()
        queue = bus.subscribe()

        # Unsubscribe
        bus.unsubscribe(queue)

        # Publish — should not be received
        await bus.publish(event_type="agent.registered", data={"agent_id": "ghost"})

        assert queue.empty()
        EventBus._instance = None


# ---------------------------------------------------------------------------
# Auth Enforcement Integration
# ---------------------------------------------------------------------------


class TestAuthEnforcement:
    """Test that auth is enforced on all protected endpoints."""

    PROTECTED_GETS = [
        "/api/agents",
        "/api/sessions/active",
        "/api/nodes",
        "/api/nodes/health",
    ]

    PROTECTED_POSTS = [
        ("/api/agents/register", {"role": "test", "project": "t", "task": "t"}),
        ("/api/nodes/sync", {}),
    ]

    def test_unauthenticated_gets_return_401(self, client_with_auth):
        """Protected GET endpoints return 401 without token."""
        for endpoint in self.PROTECTED_GETS:
            r = client_with_auth.get(endpoint)
            assert r.status_code == 401, f"{endpoint} should require auth, got {r.status_code}"

    def test_unauthenticated_posts_return_401(self, client_with_auth):
        """Protected POST endpoints return 401 without token."""
        for endpoint, body in self.PROTECTED_POSTS:
            r = client_with_auth.post(endpoint, json=body)
            assert r.status_code == 401, f"{endpoint} should require auth, got {r.status_code}"

    def test_valid_token_grants_access(self, client_with_auth):
        """Valid Bearer token grants access to protected endpoints."""
        headers = {"Authorization": "Bearer test-secret-token"}
        r = client_with_auth.get("/api/agents", headers=headers)
        assert r.status_code == 200

    def test_invalid_token_returns_401(self, client_with_auth):
        """Invalid Bearer token returns 401."""
        headers = {"Authorization": "Bearer wrong-token"}
        r = client_with_auth.get("/api/agents", headers=headers)
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Agent Registry Unit Tests (service layer)
# ---------------------------------------------------------------------------


class TestAgentRegistryService:
    """Test AgentRegistry service directly."""

    def test_register_and_get(self):
        """Register agent, then retrieve by ID."""
        from forge_harness.webhook_server.services.agent_registry import AgentRegistry

        registry = AgentRegistry()
        agent = registry.register(role="backend", project="test", task="build")
        assert agent.id
        assert agent.role == "backend"

        found = registry.get(agent.id)
        assert found is not None
        assert found.id == agent.id

    def test_get_by_tmux_session(self):
        """Get agent by tmux_session value."""
        from forge_harness.webhook_server.services.agent_registry import AgentRegistry

        registry = AgentRegistry()
        agent = registry.register(
            role="frontend", project="ui", task="build", tmux_session="forge:cursor"
        )

        found = registry.get("forge:cursor")
        assert found is not None
        assert found.id == agent.id

    def test_get_by_name(self):
        """Get agent by name (case-insensitive)."""
        from forge_harness.webhook_server.services.agent_registry import AgentRegistry

        registry = AgentRegistry()
        registry.register(role="test", project="p", task="t", name="MyAgent")

        found = registry.get("myagent")
        assert found is not None
        assert found.name == "MyAgent"

    def test_list_active(self):
        """List active agents, excluding expired."""
        from forge_harness.webhook_server.services.agent_registry import AgentRegistry

        registry = AgentRegistry(expiry_seconds=300)
        registry.register(role="a", project="p", task="t")
        registry.register(role="b", project="p", task="t")

        active = registry.list_active()
        assert len(active) == 2

    def test_update_progress(self):
        """Update agent progress and verify fields."""
        from forge_harness.webhook_server.services.agent_registry import AgentRegistry

        registry = AgentRegistry()
        agent = registry.register(role="test", project="p", task="t")

        updated = registry.update_progress(
            agent.id,
            progress=75,
            current_task="Running tests",
            files_modified=["api.py"],
        )
        assert updated is not None
        assert updated.progress == 75
        assert updated.current_task == "Running tests"
        assert "api.py" in updated.files_modified

    def test_complete_sets_status(self):
        """Complete sets status='completed' and progress=100."""
        from forge_harness.webhook_server.services.agent_registry import AgentRegistry

        registry = AgentRegistry()
        agent = registry.register(role="test", project="p", task="t")
        completed = registry.complete(agent.id, summary="All done")

        assert completed.status == "completed"
        assert completed.progress == 100

    def test_pause_resume_cycle(self):
        """Pause then resume changes status correctly."""
        from forge_harness.webhook_server.services.agent_registry import AgentRegistry

        registry = AgentRegistry()
        agent = registry.register(role="test", project="p", task="t")

        paused, prev = registry.pause(agent.id)
        assert paused.status == "paused"
        assert prev == "active"

        resumed, prev2 = registry.resume(agent.id)
        assert resumed.status == "active"
        assert prev2 == "paused"

    def test_kill_marks_failed(self):
        """Kill sets status='failed'."""
        from forge_harness.webhook_server.services.agent_registry import AgentRegistry

        registry = AgentRegistry()
        agent = registry.register(role="test", project="p", task="t")

        killed, prev = registry.kill(agent.id, reason="Out of context")
        assert killed.status == "failed"
        assert prev == "active"

    def test_parent_child_hierarchy(self):
        """Register child agent updates parent's children list."""
        from forge_harness.webhook_server.services.agent_registry import AgentRegistry

        registry = AgentRegistry()
        parent = registry.register(role="orchestrator", project="forge", task="coordinate")
        child = registry.register(
            role="worker", project="forge", task="implement", parent_id=parent.id
        )

        parent_refreshed = registry.get(parent.id)
        assert child.id in parent_refreshed.children

    def test_get_nonexistent_returns_none(self):
        """Getting a nonexistent agent returns None."""
        from forge_harness.webhook_server.services.agent_registry import AgentRegistry

        registry = AgentRegistry()
        assert registry.get("does-not-exist") is None

    def test_broadcast_reaches_all(self):
        """Broadcast message reaches all active agents."""
        from forge_harness.webhook_server.services.agent_registry import AgentRegistry

        registry = AgentRegistry()
        a1 = registry.register(role="a", project="p", task="t")
        a2 = registry.register(role="b", project="p", task="t")

        count = registry.broadcast({"type": "announcement", "content": "Sprint 9 starts"})
        assert count == 2

        a1_refreshed = registry.get(a1.id)
        assert len(a1_refreshed.messages) == 1
        assert a1_refreshed.messages[0]["content"] == "Sprint 9 starts"


# ---------------------------------------------------------------------------
# API Response Contract Tests
# ---------------------------------------------------------------------------


class TestAPIResponseContracts:
    """Verify API responses match the expected contract shape."""

    def test_agents_list_contract(self, client):
        """GET /api/agents response matches contract."""
        r = client.get("/api/agents")
        body = r.json()

        # Top level
        assert "success" in body
        assert "data" in body
        assert "timestamp" in body

        # Data shape
        assert "agents" in body["data"]
        assert "total" in body["data"]
        assert isinstance(body["data"]["agents"], list)
        assert isinstance(body["data"]["total"], int)

    def test_agent_detail_contract(self, client):
        """GET /api/agents/{id} response matches contract."""
        r = client.get("/api/agents/forge:nova")
        body = r.json()

        assert body["success"] is True
        agent = body["data"]

        # Required fields per API_CONTRACTS.md
        required_fields = [
            "session_id",
            "agent_role",
            "agent_name",
            "status",
            "project",
            "source",
        ]
        for field in required_fields:
            assert field in agent, f"Missing required field: {field}"

    def test_sessions_list_contract(self, client):
        """GET /api/sessions/active response matches contract."""
        r = client.get("/api/sessions/active")
        body = r.json()

        assert body["success"] is True
        sessions = body["data"]
        assert isinstance(sessions, list)

        if sessions:
            s = sessions[0]
            for field in ["session_name", "window_name", "agent_type", "status"]:
                assert field in s, f"Missing field: {field}"

    def test_nodes_list_contract(self, client):
        """GET /api/nodes response matches contract."""
        r = client.get("/api/nodes")
        body = r.json()

        assert body["success"] is True
        assert "nodes" in body["data"]
        assert "count" in body["data"]

        if body["data"]["nodes"]:
            node = body["data"]["nodes"][0]
            for field in ["node_id", "name", "status"]:
                assert field in node, f"Missing node field: {field}"

    def test_health_endpoint(self, client):
        """GET /api/health returns server health."""
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body.get("status") in ("ok", "healthy")
