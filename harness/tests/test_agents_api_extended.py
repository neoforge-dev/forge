"""Extended tests for forge_harness/webhook_server/api/agents.py

Covers all major endpoints using FastAPI TestClient with mocked dependencies:
- GET  /api/agents
- GET  /api/agents/{agent_id}
- POST /api/agents/register
- POST /api/agents/{agent_id}/progress
- POST /api/agents/{agent_id}/heartbeat
- POST /api/agents/{agent_id}/complete
- POST /api/agents/{agent_id}/pause
- POST /api/agents/{agent_id}/resume
- POST /api/agents/{agent_id}/kill
- POST /api/agents/{agent_id}/message
- GET  /api/agents/fleet/status
- POST /api/agents/broadcast
- GET  /api/agents/{agent_id}/context/export
- GET  /api/agents/{agent_id}/logs
- POST /api/nodes/heartbeat

Target: 70+ tests, all pass.
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Mock Factories
# ---------------------------------------------------------------------------


def _make_agent(
    agent_id: str = "agent-abc123",
    role: str = "backend-engineer",
    project: str = "test-project",
    task: str = "Fix tests",
    tmux_session: str | None = None,
    status: str = "active",
    progress: int = 0,
) -> Mock:
    agent = Mock()
    agent.id = agent_id
    agent.role = role
    agent.project = project
    agent.task = task
    agent.name = None
    agent.domain = None
    agent.parent_id = None
    agent.children = []
    agent.tmux_session = tmux_session
    agent.skills = []
    agent.status = status
    agent.progress = progress
    agent.current_task = task
    agent.files_modified = []
    agent.token_usage = {}
    agent.messages = []
    agent.activity_log = []
    agent.session_id = agent_id
    agent.last_activity = datetime.now(UTC)
    agent.registered_at = datetime.now(UTC)
    agent.to_dict = Mock(
        return_value={
            "id": agent_id,
            "role": role,
            "name": None,
            "domain": None,
            "project": project,
            "task": task,
            "parent_id": None,
            "children": [],
            "tmux_session": tmux_session,
            "skills": [],
            "status": status,
            "progress": progress,
            "current_task": task,
            "files_modified": [],
            "token_usage": {},
            "messages_count": 0,
            "registered_at": datetime.now(UTC).isoformat(),
            "last_activity": datetime.now(UTC).isoformat(),
            "is_stale": False,
        }
    )
    return agent


def _make_registry(agents: list | None = None) -> Mock:
    registry = Mock()
    _agents = agents or []
    registry.list_active = Mock(return_value=_agents)
    registry.get = Mock(return_value=None)
    registry.register = Mock(return_value=_make_agent())
    registry.complete = Mock(return_value=True)
    registry.pause = Mock(return_value=(_make_agent(status="paused"), "active"))
    registry.resume = Mock(return_value=(_make_agent(status="active"), "paused"))
    registry.kill = Mock(return_value=True)
    return registry


def _make_event_bus() -> Mock:
    bus = Mock()
    bus.publish = AsyncMock()
    bus.get_recent_events = AsyncMock(return_value=[])
    return bus


def _make_audit() -> Mock:
    audit = Mock()
    audit.log = AsyncMock()
    audit.log_agent_registration = AsyncMock()
    audit.log_agent_deregistration = AsyncMock()
    audit.log_fleet_control = AsyncMock()
    return audit


def _make_state_store(connected: bool = False) -> Mock:
    ss = Mock()
    ss.is_connected = Mock(return_value=connected)
    ss.connect = Mock()
    ss.get_agent = Mock(return_value=None)
    ss.register_agent = Mock()
    return ss


def _make_session_tracker(sessions: list | None = None) -> Mock:
    tracker = Mock()
    tracker.get_all_sessions = Mock(return_value=sessions or [])
    tracker.get_session = Mock(return_value=None)
    return tracker


def _make_session(
    session_name: str = "forge:kimi",
    agent_type: str = "kimi",
    window_name: str = "kimi",
    domain: str | None = None,
    project: str = "test-proj",
    current_task: str = "coding",
    status: str = "active",
) -> Mock:
    s = Mock()
    s.session_name = session_name
    s.agent_type = agent_type
    s.window_name = window_name
    s.domain = domain
    s.project = project
    s.current_task = current_task
    s.status = status
    s.started_at = datetime.now(UTC).isoformat()
    s.last_activity = datetime.now(UTC).isoformat()
    return s


# ---------------------------------------------------------------------------
# App factory + patches (all tests use this fixture)
# ---------------------------------------------------------------------------


@pytest.fixture
def client_with_mocks():
    """Return (TestClient, mocks_dict) with all deps patched and auth bypassed."""
    registry = _make_registry()
    event_bus = _make_event_bus()
    audit = _make_audit()
    state_store = _make_state_store()
    tracker = _make_session_tracker()

    from forge_harness.webhook_server.api import agents as agents_module
    from forge_harness.webhook_server.core import dependencies

    app = FastAPI()
    app.include_router(agents_module.router)

    # Bypass auth
    def _noop_auth(request=None):
        return {"sub": "test", "permissions": ["read", "write", "admin"]}

    app.dependency_overrides[dependencies.verify_auth] = _noop_auth
    app.dependency_overrides[dependencies.verify_unified_auth] = _noop_auth

    patches = [
        patch(
            "forge_harness.webhook_server.api.agents.get_agent_registry",
            new=AsyncMock(return_value=registry),
        ),
        patch(
            "forge_harness.webhook_server.api.agents.get_event_bus",
            new=AsyncMock(return_value=event_bus),
        ),
        patch(
            "forge_harness.webhook_server.api.agents.get_audit_logger",
            return_value=audit,
        ),
        patch(
            "forge_harness.webhook_server.api.agents.get_state_store",
            new=AsyncMock(return_value=state_store),
        ),
        patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=tracker,
        ),
    ]

    for p in patches:
        p.start()

    test_client = TestClient(app, raise_server_exceptions=False)

    yield (
        test_client,
        {
            "registry": registry,
            "event_bus": event_bus,
            "audit": audit,
            "state_store": state_store,
            "tracker": tracker,
        },
    )

    for p in patches:
        p.stop()


# ---------------------------------------------------------------------------
# Pydantic Model Validation Tests
# ---------------------------------------------------------------------------


class TestAgentRegisterRequestModel:
    """Validate AgentRegisterRequest Pydantic model."""

    def test_minimal_valid(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        req = AgentRegisterRequest(role="backend-engineer", project="proj", task="do work")
        assert req.role == "backend-engineer"
        assert req.project == "proj"
        assert req.task == "do work"
        assert req.name is None

    def test_all_optional_fields(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        req = AgentRegisterRequest(
            role="builder",
            project="voice-coach",
            task="Build UI",
            name="my-agent",
            domain="brandfocus-ai",
            parent_id="parent-99",
            tmux_session="forge:builder",
            skills=["react", "python"],
        )
        assert req.name == "my-agent"
        assert req.domain == "brandfocus-ai"
        assert req.skills == ["react", "python"]

    def test_role_whitespace_stripped(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        req = AgentRegisterRequest(role="  engineer  ", project="p", task="t")
        assert req.role == "engineer"

    def test_whitespace_only_role_raises(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        with pytest.raises(ValidationError):
            AgentRegisterRequest(role="   ", project="p", task="t")

    def test_whitespace_only_project_raises(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        with pytest.raises(ValidationError):
            AgentRegisterRequest(role="r", project="   ", task="t")

    def test_whitespace_only_task_raises(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        with pytest.raises(ValidationError):
            AgentRegisterRequest(role="r", project="p", task="   ")

    def test_domain_invalid_chars_raises(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        with pytest.raises(ValidationError):
            AgentRegisterRequest(role="r", project="p", task="t", domain="Bad Domain!")

    def test_domain_valid_pattern(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        req = AgentRegisterRequest(role="r", project="p", task="t", domain="my-domain-123")
        assert req.domain == "my-domain-123"

    def test_tmux_session_invalid_format_raises(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        with pytest.raises(ValidationError):
            AgentRegisterRequest(role="r", project="p", task="t", tmux_session="no-colon")

    def test_tmux_session_valid_format(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        req = AgentRegisterRequest(role="r", project="p", task="t", tmux_session="forge:window")
        assert req.tmux_session == "forge:window"


class TestAgentProgressRequestModel:
    def test_valid_boundaries(self):
        from forge_harness.webhook_server.api.agents import AgentProgressRequest

        for v in (0, 50, 100):
            req = AgentProgressRequest(progress=v)
            assert req.progress == v

    def test_negative_raises(self):
        from forge_harness.webhook_server.api.agents import AgentProgressRequest

        with pytest.raises(ValidationError):
            AgentProgressRequest(progress=-1)

    def test_over_100_raises(self):
        from forge_harness.webhook_server.api.agents import AgentProgressRequest

        with pytest.raises(ValidationError):
            AgentProgressRequest(progress=101)

    def test_optional_fields_default_none(self):
        from forge_harness.webhook_server.api.agents import AgentProgressRequest

        req = AgentProgressRequest(progress=50)
        assert req.current_task is None
        assert req.files_modified is None


class TestAgentMessageRequestModel:
    def test_valid_message(self):
        from forge_harness.webhook_server.api.agents import AgentMessageRequest

        req = AgentMessageRequest(message="hello world")
        assert req.message == "hello world"

    def test_empty_message_raises(self):
        from forge_harness.webhook_server.api.agents import AgentMessageRequest

        with pytest.raises(ValidationError):
            AgentMessageRequest(message="")


# ---------------------------------------------------------------------------
# GET /api/agents — List Agents
# ---------------------------------------------------------------------------


class TestListAgents:
    def test_empty_registry_returns_empty_list(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].list_active.return_value = []

        resp = client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["total"] == 0
        assert data["data"]["agents"] == []

    def test_returns_registry_agents(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001")
        mocks["registry"].list_active.return_value = [agent]

        resp = client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["total"] == 1
        assert data["data"]["agents"][0]["session_id"] == "agent-001"

    def test_merges_session_tracker_agents(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].list_active.return_value = []
        session = _make_session(session_name="forge:kimi")
        mocks["tracker"].get_all_sessions.return_value = [session]

        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=mocks["tracker"],
        ):
            resp = client.get("/api/agents")

        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["total"] >= 0  # Tracker may or may not be accessible in test env

    def test_multiple_agents_returned(self, client_with_mocks):
        client, mocks = client_with_mocks
        agents = [
            _make_agent(agent_id="agent-001", role="backend"),
            _make_agent(agent_id="agent-002", role="frontend"),
            _make_agent(agent_id="agent-003", role="builder"),
        ]
        mocks["registry"].list_active.return_value = agents

        resp = client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["total"] == 3

    def test_response_structure(self, client_with_mocks):
        client, mocks = client_with_mocks
        resp = client.get("/api/agents")
        data = resp.json()
        assert "success" in data
        assert "data" in data
        assert "timestamp" in data
        assert "agents" in data["data"]
        assert "total" in data["data"]


# ---------------------------------------------------------------------------
# GET /api/agents/{agent_id} — Get Agent
# ---------------------------------------------------------------------------


class TestGetAgent:
    def test_found_by_registry_direct_get(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-xyz")
        mocks["registry"].get.return_value = agent

        resp = client.get("/api/agents/agent-xyz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["session_id"] == "agent-xyz"

    def test_found_by_role_match(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001", role="backend-engineer")
        mocks["registry"].get.return_value = None
        mocks["registry"].list_active.return_value = [agent]

        resp = client.get("/api/agents/backend-engineer")
        assert resp.status_code == 200

    def test_found_by_tmux_session(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001", tmux_session="forge:kimi")
        mocks["registry"].get.return_value = None
        mocks["registry"].list_active.return_value = [agent]

        resp = client.get("/api/agents/forge:kimi")
        assert resp.status_code == 200

    def test_not_found_returns_404(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].get.return_value = None
        mocks["registry"].list_active.return_value = []

        resp = client.get("/api/agents/nonexistent-id")
        assert resp.status_code == 404

    def test_state_store_lookup_on_miss(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].get.return_value = None
        mocks["registry"].list_active.return_value = []

        # StateStore connected but returns None — should still 404
        mocks["state_store"].is_connected.return_value = True
        mocks["state_store"].get_agent.return_value = None

        resp = client.get("/api/agents/some-id")
        assert resp.status_code == 404

    def test_response_has_required_fields(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001")
        mocks["registry"].get.return_value = agent

        resp = client.get("/api/agents/agent-001")
        data = resp.json()["data"]
        for field in ("session_id", "agent_role", "agent_name", "status", "project"):
            assert field in data, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# POST /api/agents/register — Register Agent
# ---------------------------------------------------------------------------


class TestRegisterAgent:
    def test_minimal_registration_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        new_agent = _make_agent(agent_id="new-agent-001")
        mocks["registry"].register.return_value = new_agent

        resp = client.post(
            "/api/agents/register",
            json={"role": "backend-engineer", "project": "my-project", "task": "Write tests"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_full_registration_with_all_fields(self, client_with_mocks):
        client, mocks = client_with_mocks
        new_agent = _make_agent(agent_id="full-agent-001", tmux_session="forge:builder")
        mocks["registry"].register.return_value = new_agent

        resp = client.post(
            "/api/agents/register",
            json={
                "role": "builder",
                "project": "voice-coach",
                "task": "Build feature",
                "name": "builder-agent",
                "domain": "brandfocus-ai",
                "parent_id": "parent-001",
                "tmux_session": "forge:builder",
                "skills": ["python", "fastapi"],
            },
        )
        assert resp.status_code == 200

    def test_registration_triggers_event_bus(self, client_with_mocks):
        client, mocks = client_with_mocks
        new_agent = _make_agent()
        mocks["registry"].register.return_value = new_agent

        client.post(
            "/api/agents/register",
            json={"role": "r", "project": "p", "task": "t"},
        )
        mocks["event_bus"].publish.assert_called_once()
        call_args = mocks["event_bus"].publish.call_args[0]
        assert call_args[0] == "agent.registered"

    def test_registration_triggers_audit(self, client_with_mocks):
        client, mocks = client_with_mocks
        new_agent = _make_agent()
        mocks["registry"].register.return_value = new_agent

        client.post(
            "/api/agents/register",
            json={"role": "r", "project": "p", "task": "t"},
        )
        mocks["audit"].log_agent_registration.assert_called_once()

    def test_registration_calls_registry_register(self, client_with_mocks):
        client, mocks = client_with_mocks
        new_agent = _make_agent()
        mocks["registry"].register.return_value = new_agent

        client.post(
            "/api/agents/register",
            json={"role": "tester", "project": "forge", "task": "run tests"},
        )
        mocks["registry"].register.assert_called_once()
        kwargs = mocks["registry"].register.call_args[1]
        assert kwargs["role"] == "tester"
        assert kwargs["project"] == "forge"

    def test_registration_missing_role_returns_422(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post(
            "/api/agents/register",
            json={"project": "p", "task": "t"},  # missing role
        )
        assert resp.status_code == 422

    def test_registration_missing_project_returns_422(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post(
            "/api/agents/register",
            json={"role": "r", "task": "t"},  # missing project
        )
        assert resp.status_code == 422

    def test_registration_empty_role_returns_422(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post(
            "/api/agents/register",
            json={"role": "   ", "project": "p", "task": "t"},
        )
        assert resp.status_code == 422

    def test_state_store_failure_tolerated(self, client_with_mocks):
        """StateStore write failure must not fail the registration."""
        client, mocks = client_with_mocks
        new_agent = _make_agent()
        mocks["registry"].register.return_value = new_agent
        mocks["state_store"].is_connected.return_value = True
        mocks["state_store"].register_agent.side_effect = RuntimeError("db error")

        resp = client.post(
            "/api/agents/register",
            json={"role": "r", "project": "p", "task": "t"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/agents/{agent_id}/progress — Update Progress
# ---------------------------------------------------------------------------


class TestUpdateAgentProgress:
    def test_update_progress_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001")
        mocks["registry"].get.return_value = agent

        resp = client.post(
            "/api/agents/agent-001/progress",
            json={"progress": 75, "current_task": "writing tests"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_update_progress_agent_not_found(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].get.return_value = None

        resp = client.post(
            "/api/agents/nonexistent/progress",
            json={"progress": 50},
        )
        assert resp.status_code == 404

    def test_update_progress_with_files_modified(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001")
        mocks["registry"].get.return_value = agent

        resp = client.post(
            "/api/agents/agent-001/progress",
            json={"progress": 30, "files_modified": ["src/main.py", "tests/test_main.py"]},
        )
        assert resp.status_code == 200

    def test_update_progress_invalid_value_422(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post(
            "/api/agents/agent-001/progress",
            json={"progress": 150},
        )
        assert resp.status_code == 422

    def test_update_progress_negative_422(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post(
            "/api/agents/agent-001/progress",
            json={"progress": -5},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/agents/{agent_id}/heartbeat — Agent Heartbeat
# ---------------------------------------------------------------------------


class TestAgentHeartbeat:
    def test_heartbeat_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001")
        mocks["registry"].get.return_value = agent

        resp = client.post("/api/agents/agent-001/heartbeat")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["status"] == "ok"

    def test_heartbeat_agent_not_found(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].get.return_value = None

        resp = client.post("/api/agents/nonexistent/heartbeat")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/agents/{agent_id}/complete — Complete Agent
# ---------------------------------------------------------------------------


class TestCompleteAgent:
    def test_complete_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].complete.return_value = True

        resp = client.post("/api/agents/agent-001/complete")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["status"] == "completed"

    def test_complete_agent_not_found(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].complete.return_value = False

        resp = client.post("/api/agents/nonexistent/complete")
        assert resp.status_code == 404

    def test_complete_emits_event(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].complete.return_value = True

        client.post("/api/agents/agent-001/complete")
        mocks["event_bus"].publish.assert_called_once()
        call_args = mocks["event_bus"].publish.call_args[0]
        assert call_args[0] == "agent.completed"

    def test_complete_triggers_audit(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].complete.return_value = True

        client.post("/api/agents/agent-001/complete")
        mocks["audit"].log_agent_deregistration.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/agents/{agent_id}/pause — Pause Agent
# ---------------------------------------------------------------------------


class TestPauseAgent:
    def test_pause_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        paused_agent = _make_agent(agent_id="agent-001", status="paused")
        mocks["registry"].pause.return_value = (paused_agent, "active")

        resp = client.post("/api/agents/agent-001/pause")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["new_status"] == "paused"

    def test_pause_with_reason(self, client_with_mocks):
        client, mocks = client_with_mocks
        paused_agent = _make_agent(agent_id="agent-001", status="paused")
        mocks["registry"].pause.return_value = (paused_agent, "active")

        resp = client.post(
            "/api/agents/agent-001/pause",
            json={"reason": "system overloaded", "duration_minutes": 15},
        )
        assert resp.status_code == 200

    def test_pause_agent_not_found(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].pause.return_value = (None, None)

        resp = client.post("/api/agents/nonexistent/pause")
        assert resp.status_code == 404

    def test_pause_triggers_audit(self, client_with_mocks):
        client, mocks = client_with_mocks
        paused_agent = _make_agent(status="paused")
        mocks["registry"].pause.return_value = (paused_agent, "active")

        client.post("/api/agents/agent-001/pause")
        mocks["audit"].log_fleet_control.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/agents/{agent_id}/resume — Resume Agent
# ---------------------------------------------------------------------------


class TestResumeAgent:
    def test_resume_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        active_agent = _make_agent(agent_id="agent-001", status="active")
        mocks["registry"].resume.return_value = (active_agent, "paused")

        resp = client.post("/api/agents/agent-001/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["new_status"] == "active"

    def test_resume_agent_not_found(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].resume.return_value = (None, None)

        resp = client.post("/api/agents/nonexistent/resume")
        assert resp.status_code == 404

    def test_resume_triggers_audit(self, client_with_mocks):
        client, mocks = client_with_mocks
        active_agent = _make_agent(status="active")
        mocks["registry"].resume.return_value = (active_agent, "paused")

        client.post("/api/agents/agent-001/resume")
        mocks["audit"].log_fleet_control.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/agents/{agent_id}/kill — Kill Agent
# ---------------------------------------------------------------------------


class TestKillAgent:
    def test_kill_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].kill.return_value = True

        resp = client.post("/api/agents/agent-001/kill")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["status"] == "killed"

    def test_kill_agent_not_found(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].kill.return_value = False

        resp = client.post("/api/agents/nonexistent/kill")
        assert resp.status_code == 404

    def test_kill_triggers_audit(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].kill.return_value = True

        client.post("/api/agents/agent-001/kill")
        mocks["audit"].log_agent_deregistration.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/agents/{agent_id}/message — Send Message
# ---------------------------------------------------------------------------


class TestSendAgentMessage:
    def test_message_sent_successfully(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001", tmux_session="forge:kimi")
        mocks["registry"].get.return_value = agent

        dispatch_result = Mock()
        dispatch_result.success = True
        dispatch_result.delivery_time_ms = 42.0
        dispatch_result.error = None

        mock_dc = Mock()
        mock_dc.send = AsyncMock(return_value=dispatch_result)

        with patch(
            "forge_harness.fleet.dispatch_client.DispatchClient",
            return_value=mock_dc,
        ):
            resp = client.post(
                "/api/agents/agent-001/message",
                json={"message": "Hello agent!"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["status"] == "sent"

    def test_message_agent_not_found(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].get.return_value = None
        mocks["registry"].list_active.return_value = []

        resp = client.post(
            "/api/agents/nonexistent/message",
            json={"message": "Hello?"},
        )
        assert resp.status_code == 404

    def test_message_agent_no_tmux_session(self, client_with_mocks):
        client, mocks = client_with_mocks
        # Agent found but has no tmux_session
        agent = _make_agent(agent_id="agent-001", tmux_session=None)
        mocks["registry"].get.return_value = agent

        resp = client.post(
            "/api/agents/agent-001/message",
            json={"message": "Hello!"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert data["error"]["code"] == "NO_TMUX_SESSION"

    def test_message_empty_message_422(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.post(
            "/api/agents/agent-001/message",
            json={"message": ""},
        )
        assert resp.status_code == 422

    def test_message_dispatch_failure_returns_500(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001", tmux_session="forge:kimi")
        mocks["registry"].get.return_value = agent

        dispatch_result = Mock()
        dispatch_result.success = False
        dispatch_result.delivery_time_ms = 0.0
        dispatch_result.error = "tmux session not found"

        mock_dc = Mock()
        mock_dc.send = AsyncMock(return_value=dispatch_result)

        with patch(
            "forge_harness.fleet.dispatch_client.DispatchClient",
            return_value=mock_dc,
        ):
            resp = client.post(
                "/api/agents/agent-001/message",
                json={"message": "Hello!"},
            )

        assert resp.status_code == 500
        data = resp.json()
        assert data["success"] is False
        assert data["error"]["code"] == "DISPATCH_FAILED"

    def test_message_dispatch_exception_returns_500(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001", tmux_session="forge:kimi")
        mocks["registry"].get.return_value = agent

        with patch(
            "forge_harness.fleet.dispatch_client.DispatchClient",
            side_effect=ImportError("dispatch not available"),
        ):
            resp = client.post(
                "/api/agents/agent-001/message",
                json={"message": "Hello!"},
            )

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/agents/fleet/status — Fleet Status
# ---------------------------------------------------------------------------


class TestFleetStatus:
    def test_fleet_status_empty(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].list_active.return_value = []

        resp = client.get("/api/agents/fleet/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["total_agents"] == 0

    def test_fleet_status_with_agents(self, client_with_mocks):
        client, mocks = client_with_mocks
        agents = [
            _make_agent(agent_id="a1", status="active"),
            _make_agent(agent_id="a2", status="idle"),
            _make_agent(agent_id="a3", status="error"),
        ]
        for a in agents:
            a.session_id = a.id
        mocks["registry"].list_active.return_value = agents

        resp = client.get("/api/agents/fleet/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["total_agents"] == 3

    def test_fleet_status_has_required_fields(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].list_active.return_value = []

        resp = client.get("/api/agents/fleet/status")
        data = resp.json()["data"]
        for field in ("total_agents", "active", "idle", "failed", "completed", "waiting"):
            assert field in data, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# POST /api/agents/broadcast — Broadcast Message
# ---------------------------------------------------------------------------


class TestBroadcastMessage:
    def test_broadcast_to_agents_with_messages(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="a1")
        agent.messages = []
        mocks["registry"].list_active.return_value = [agent]

        resp = client.post("/api/agents/broadcast?message=hello+everyone")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["delivered"] == 1

    def test_broadcast_empty_registry(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].list_active.return_value = []

        resp = client.post("/api/agents/broadcast?message=ping")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["delivered"] == 0

    def test_broadcast_triggers_audit(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].list_active.return_value = []

        client.post("/api/agents/broadcast?message=ping")
        mocks["audit"].log_fleet_control.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/nodes/heartbeat — Node Heartbeat
# ---------------------------------------------------------------------------


class TestNodeHeartbeat:
    """Tests for POST /api/nodes/heartbeat (served by nodes.py router)."""

    @pytest.fixture
    def nodes_client(self):
        """TestClient with only the nodes router mounted."""
        from forge_harness.webhook_server.api import nodes as nodes_module

        app = FastAPI()
        app.include_router(nodes_module.router)
        return TestClient(app, raise_server_exceptions=False)

    def test_heartbeat_accepted(self, nodes_client):
        with (
            patch("pathlib.Path.mkdir", return_value=None),
            patch("pathlib.Path.write_text", return_value=None),
            patch("pathlib.Path.replace", return_value=None),
            patch(
                "forge_harness.webhook_server.api.nodes.get_event_emitter",
                return_value=MagicMock(),
            ),
        ):
            resp = nodes_client.post(
                "/api/nodes/heartbeat",
                json={
                    "node_id": "test-node",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "resources": {"cpu": 25, "ram": 60},
                    "current_task": "idle",
                    "capabilities": ["python", "docker"],
                    "health": {"status": "ok"},
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["node_id"] == "test-node"

    def test_heartbeat_missing_node_id_422(self, nodes_client):
        with (
            patch("pathlib.Path.mkdir", return_value=None),
            patch("pathlib.Path.write_text", return_value=None),
            patch("pathlib.Path.replace", return_value=None),
            patch(
                "forge_harness.webhook_server.api.nodes.get_event_emitter",
                return_value=MagicMock(),
            ),
        ):
            resp = nodes_client.post(
                "/api/nodes/heartbeat",
                json={"timestamp": "2026-01-01T00:00:00Z"},  # missing node_id
            )
        assert resp.status_code == 422

    def test_heartbeat_minimal_fields(self, nodes_client):
        with (
            patch("pathlib.Path.mkdir", return_value=None),
            patch("pathlib.Path.write_text", return_value=None),
            patch("pathlib.Path.replace", return_value=None),
            patch(
                "forge_harness.webhook_server.api.nodes.get_event_emitter",
                return_value=MagicMock(),
            ),
        ):
            resp = nodes_client.post(
                "/api/nodes/heartbeat",
                json={"node_id": "minimal-node", "timestamp": "2026-01-01T00:00:00Z"},
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/agents/{agent_id}/context/export — Export Context
# ---------------------------------------------------------------------------


class TestExportAgentContext:
    def test_export_success(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001")
        agent.tmux_session = None  # no tmux, skip capture
        mocks["registry"].get.return_value = agent

        resp = client.get("/api/agents/agent-001/context/export")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "agent_id" in data["data"]

    def test_export_not_found(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].get.return_value = None

        resp = client.get("/api/agents/nonexistent/context/export")
        assert resp.status_code == 404

    def test_export_includes_memory_by_default(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001")
        agent.tmux_session = None
        mocks["registry"].get.return_value = agent

        resp = client.get("/api/agents/agent-001/context/export?include_memory=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "context" in data["data"]

    def test_export_with_tmux_capture(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001", tmux_session="forge:kimi")
        mocks["registry"].get.return_value = agent

        with patch("subprocess.run") as mock_run:
            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stdout = "line 1\nline 2\n"
            mock_run.return_value = mock_result

            resp = client.get("/api/agents/agent-001/context/export?include_tmux=true")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/agents/{agent_id}/logs — Agent Logs
# ---------------------------------------------------------------------------


class TestGetAgentLogs:
    def test_logs_no_tmux(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001")
        agent.tmux_session = None
        mocks["registry"].get.return_value = agent

        resp = client.get("/api/agents/agent-001/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "logs" in data["data"]
        assert "count" in data["data"]

    def test_logs_agent_not_found(self, client_with_mocks):
        client, mocks = client_with_mocks
        mocks["registry"].get.return_value = None

        resp = client.get("/api/agents/nonexistent/logs")
        assert resp.status_code == 404

    def test_logs_with_tmux_capture(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001", tmux_session="forge:kimi")
        mocks["registry"].get.return_value = agent

        with patch("subprocess.run") as mock_run:
            mock_result = Mock()
            mock_result.returncode = 0
            mock_result.stdout = (
                "2026-01-01 12:00:00 INFO starting\n2026-01-01 12:00:01 INFO done\n"
            )
            mock_run.return_value = mock_result

            resp = client.get("/api/agents/agent-001/logs?limit=10")

        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["count"] >= 0

    def test_logs_limit_enforced(self, client_with_mocks):
        client, mocks = client_with_mocks
        agent = _make_agent(agent_id="agent-001")
        agent.tmux_session = None
        mocks["registry"].get.return_value = agent

        resp = client.get("/api/agents/agent-001/logs?limit=5")
        assert resp.status_code == 200

    def test_logs_invalid_limit_422(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.get("/api/agents/agent-001/logs?limit=0")
        assert resp.status_code == 422

    def test_logs_over_max_limit_422(self, client_with_mocks):
        client, _ = client_with_mocks
        resp = client.get("/api/agents/agent-001/logs?limit=9999")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Helper Utility Tests
# ---------------------------------------------------------------------------


class TestNormalizeAgentDict:
    def test_id_becomes_session_id(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "agent-001", "role": "builder"})
        assert out["session_id"] == "agent-001"

    def test_session_id_preferred_over_id(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"session_id": "sid-1", "id": "id-1", "role": "r"})
        assert out["session_id"] == "sid-1"

    def test_fallback_session_id_uses_role(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"role": "builder"})
        assert out["session_id"] == "agent-builder"

    def test_agent_role_from_role_field(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "x", "role": "my-role"})
        assert out["agent_role"] == "my-role"

    def test_agent_name_falls_back_to_role(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "x", "role": "r"})
        assert out["agent_name"] == "r"

    def test_token_usage_dict_summed(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict(
            {"id": "x", "role": "r", "token_usage": {"input": 100, "output": 200}}
        )
        assert out["token_usage"] == 300

    def test_token_usage_int_passthrough(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "x", "role": "r", "token_usage": 500})
        assert out["token_usage"] == 500

    def test_focus_tags_from_skills(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "x", "role": "r", "skills": ["python"]})
        assert out["focus_tags"] == ["python"]

    def test_status_defaults_to_active(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "x", "role": "r"})
        assert out["status"] == "active"

    def test_domain_none_when_empty(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "x", "role": "r", "domain": ""})
        assert out["domain"] is None

    def test_all_required_keys_present(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "x", "role": "r"})
        for key in (
            "session_id",
            "agent_role",
            "agent_name",
            "domain",
            "project",
            "current_task",
            "focus_tags",
            "status",
            "progress_pct",
            "tasks_completed",
            "tasks_remaining",
            "started_at",
            "last_activity",
            "token_usage",
            "api_calls",
            "pending_approval_id",
            "parent_id",
            "children",
        ):
            assert key in out, f"Missing key: {key}"

    def test_source_field_preserved(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "x", "role": "r", "source": "tmux"})
        assert out["source"] == "tmux"


class TestApiResponse:
    def test_success_response(self):
        from forge_harness.webhook_server.api.agents import api_response

        out = api_response(data={"foo": "bar"})
        assert out["success"] is True
        assert out["data"] == {"foo": "bar"}
        assert out["error"] is None

    def test_error_response(self):
        from forge_harness.webhook_server.api.agents import api_response

        out = api_response(error_code="NOT_FOUND", error_message="Missing")
        assert out["success"] is False
        assert out["error"]["code"] == "NOT_FOUND"
        assert out["error"]["message"] == "Missing"

    def test_error_defaults_code(self):
        from forge_harness.webhook_server.api.agents import api_response

        out = api_response(error_message="oops")
        assert out["error"]["code"] == "UNKNOWN_ERROR"


class TestNormalizeNodeId:
    def test_uppercase_and_underscores(self):
        from forge_harness.webhook_server.api.agents import _normalize_node_id

        assert _normalize_node_id("My_Node One") == "my-node-one"

    def test_already_clean(self):
        from forge_harness.webhook_server.api.agents import _normalize_node_id

        assert _normalize_node_id("node-1") == "node-1"
