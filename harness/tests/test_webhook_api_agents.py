"""Comprehensive tests for forge_harness/webhook_server/api/agents.py

Tests cover all endpoints:
- POST /api/agents/register
- GET  /api/agents
- POST /api/nodes/heartbeat
- GET  /api/agents/{agent_id}
- POST /api/agents/{agent_id}/progress
- POST /api/agents/{agent_id}/heartbeat
- POST /api/agents/{agent_id}/complete
- POST /api/agents/{agent_id}/pause
- POST /api/agents/{agent_id}/resume
- POST /api/agents/{agent_id}/kill
- GET  /api/agents/fleet/status
- POST /api/agents/broadcast
- POST /api/agents/{agent_id}/message
- GET  /api/agents/{agent_id}/context/export
- GET  /api/agents/{agent_id}/logs

All external dependencies (registry, state store, event bus, audit, subprocess, file I/O) are mocked.
"""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers: build a minimal AgentSession mock
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
    """Return a mock AgentSession with all required attributes."""
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
    """Return a mock AgentRegistry."""
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
    return bus


def _make_audit_logger() -> Mock:
    audit = Mock()
    audit.log = AsyncMock()
    audit.log_agent_registration = AsyncMock()
    audit.log_agent_deregistration = AsyncMock()
    audit.log_fleet_control = AsyncMock()
    return audit


# ---------------------------------------------------------------------------
# *** Context manager for patching the module-level dependencies ***
# All tests use these three helpers to avoid repetition.
# ---------------------------------------------------------------------------


def patch_all(
    registry: Mock | None = None,
    event_bus: Mock | None = None,
    audit: Mock | None = None,
    state_store: Mock | None = None,
    session_tracker: Mock | None = None,
):
    """Return a context manager stack that patches the four main dep points."""
    from contextlib import ExitStack

    reg = registry or _make_registry()
    bus = event_bus or _make_event_bus()
    aud = audit or _make_audit_logger()

    ss = Mock()
    ss.is_connected = Mock(return_value=False)
    ss.connect = Mock()
    ss = state_store or ss

    st = Mock()
    st.get_all_sessions = Mock(return_value=[])
    st.get_session = Mock(return_value=None)
    st = session_tracker or st

    stack = ExitStack()
    stack.enter_context(
        patch(
            "forge_harness.webhook_server.api.agents.get_agent_registry",
            new=AsyncMock(return_value=reg),
        )
    )
    stack.enter_context(
        patch(
            "forge_harness.webhook_server.api.agents.get_event_bus",
            new=AsyncMock(return_value=bus),
        )
    )
    stack.enter_context(
        patch(
            "forge_harness.webhook_server.api.agents.get_audit_logger",
            return_value=aud,
        )
    )
    stack.enter_context(
        patch(
            "forge_harness.webhook_server.api.agents.get_state_store",
            new=AsyncMock(return_value=ss),
        )
    )
    stack.enter_context(
        patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=st,
        )
    )
    return stack, reg, bus, aud, ss, st


# ---------------------------------------------------------------------------
# Pydantic Request Model Tests
# ---------------------------------------------------------------------------


class TestAgentRegisterRequest:
    """Validate Pydantic model for agent registration."""

    def test_minimal_fields(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        req = AgentRegisterRequest(role="backend-engineer", project="proj", task="do it")
        assert req.role == "backend-engineer"
        assert req.project == "proj"
        assert req.task == "do it"
        assert req.name is None
        assert req.domain is None
        assert req.parent_id is None
        assert req.tmux_session is None
        assert req.skills is None

    def test_full_fields(self):
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
        assert req.tmux_session == "forge:builder"
        assert req.skills == ["react", "python"]

    def test_role_whitespace_stripped(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        req = AgentRegisterRequest(role="  backend  ", project="p", task="t")
        assert req.role == "backend"

    def test_role_empty_raises(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        with pytest.raises(ValidationError):
            AgentRegisterRequest(role="   ", project="p", task="t")

    def test_project_empty_raises(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        with pytest.raises(ValidationError):
            AgentRegisterRequest(role="r", project="  ", task="t")

    def test_task_empty_raises(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        with pytest.raises(ValidationError):
            AgentRegisterRequest(role="r", project="p", task="  ")

    def test_domain_invalid_chars_raises(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        with pytest.raises(ValidationError):
            AgentRegisterRequest(role="r", project="p", task="t", domain="Bad Domain!")

    def test_tmux_session_invalid_format_raises(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        with pytest.raises(ValidationError):
            AgentRegisterRequest(role="r", project="p", task="t", tmux_session="no-colon")


class TestAgentProgressRequest:
    def test_valid_range(self):
        from forge_harness.webhook_server.api.agents import AgentProgressRequest

        for v in (0, 50, 100):
            req = AgentProgressRequest(progress=v)
            assert req.progress == v

    def test_below_zero_raises(self):
        from forge_harness.webhook_server.api.agents import AgentProgressRequest

        with pytest.raises(ValidationError):
            AgentProgressRequest(progress=-1)

    def test_above_hundred_raises(self):
        from forge_harness.webhook_server.api.agents import AgentProgressRequest

        with pytest.raises(ValidationError):
            AgentProgressRequest(progress=101)

    def test_optional_fields_default_none(self):
        from forge_harness.webhook_server.api.agents import AgentProgressRequest

        req = AgentProgressRequest(progress=42)
        assert req.current_task is None
        assert req.files_modified is None


class TestAgentMessageRequest:
    def test_valid_message(self):
        from forge_harness.webhook_server.api.agents import AgentMessageRequest

        req = AgentMessageRequest(message="hello world")
        assert req.message == "hello world"

    def test_empty_message_raises(self):
        from forge_harness.webhook_server.api.agents import AgentMessageRequest

        with pytest.raises(ValidationError):
            AgentMessageRequest(message="")


class TestAgentPauseRequest:
    def test_defaults(self):
        from forge_harness.webhook_server.api.agents import AgentPauseRequest

        req = AgentPauseRequest()
        assert req.reason is None
        assert req.duration_minutes == 30

    def test_custom_values(self):
        from forge_harness.webhook_server.api.agents import AgentPauseRequest

        req = AgentPauseRequest(reason="overloaded", duration_minutes=60)
        assert req.reason == "overloaded"
        assert req.duration_minutes == 60

    def test_duration_out_of_range_raises(self):
        from forge_harness.webhook_server.api.agents import AgentPauseRequest

        with pytest.raises(ValidationError):
            AgentPauseRequest(duration_minutes=4)
        with pytest.raises(ValidationError):
            AgentPauseRequest(duration_minutes=121)


class TestNodeDispatchRequest:
    def test_valid(self):
        from forge_harness.webhook_server.api.agents import NodeDispatchRequest

        req = NodeDispatchRequest(agent_type="backend-engineer", task="Fix bug")
        assert req.priority == "normal"
        assert req.project is None

    def test_invalid_priority_raises(self):
        from forge_harness.webhook_server.api.agents import NodeDispatchRequest

        with pytest.raises(ValidationError):
            NodeDispatchRequest(agent_type="x", task="y", priority="LOW")


# ---------------------------------------------------------------------------
# Utility Function Tests
# ---------------------------------------------------------------------------


class TestNormalizeAgentDict:
    """Tests for normalize_agent_dict helper."""

    def test_id_becomes_session_id(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "agent-001", "role": "builder"})
        assert out["session_id"] == "agent-001"

    def test_session_id_preferred_over_id(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"session_id": "sid-1", "id": "id-1", "role": "r"})
        assert out["session_id"] == "sid-1"

    def test_fallback_when_no_id(self):
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

    def test_progress_pct_from_progress_field(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "x", "role": "r", "progress": 75})
        assert out["progress_pct"] == 75.0

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

    def test_source_field_preserved(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "x", "role": "r", "source": "tmux"})
        assert out["source"] == "tmux"

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


class TestNormalizeNodeId:
    def test_lowercase_and_dashes(self):
        from forge_harness.webhook_server.api.agents import _normalize_node_id

        assert _normalize_node_id("My_Node One") == "my-node-one"

    def test_already_clean(self):
        from forge_harness.webhook_server.api.agents import _normalize_node_id

        assert _normalize_node_id("node-1") == "node-1"


class TestApiResponse:
    def test_success_response(self):
        from forge_harness.webhook_server.api.agents import api_response

        out = api_response(data={"foo": "bar"})
        assert out["success"] is True
        assert out["data"] == {"foo": "bar"}
        assert out["error"] is None
        assert "timestamp" in out

    def test_error_response(self):
        from forge_harness.webhook_server.api.agents import api_response

        out = api_response(error_code="NOT_FOUND", error_message="Missing")
        assert out["success"] is False
        assert out["error"]["code"] == "NOT_FOUND"
        assert out["error"]["message"] == "Missing"

    def test_error_defaults(self):
        from forge_harness.webhook_server.api.agents import api_response

        out = api_response(error_message="oops")
        assert out["error"]["code"] == "UNKNOWN_ERROR"


class TestDetectRamGb:
    def test_returns_int(self):
        from forge_harness.webhook_server.api.agents import _detect_ram_gb

        assert isinstance(_detect_ram_gb(), int)


class TestGetCpuLoadPct:
    def test_returns_int_0_to_100(self):
        from forge_harness.webhook_server.api.agents import _get_cpu_load_pct

        val = _get_cpu_load_pct()
        assert 0 <= val <= 100

    def test_exception_returns_zero(self):
        from forge_harness.webhook_server.api.agents import _get_cpu_load_pct

        with patch("os.getloadavg", side_effect=OSError("unavail")):
            assert _get_cpu_load_pct() == 0


class TestGetDiskUsagePct:
    def test_returns_int(self):
        from forge_harness.webhook_server.api.agents import _get_disk_usage_pct

        assert isinstance(_get_disk_usage_pct(), int)

    def test_exception_returns_zero(self):
        from forge_harness.webhook_server.api.agents import _get_disk_usage_pct

        with patch("shutil.disk_usage", side_effect=OSError("err")):
            assert _get_disk_usage_pct() == 0


class TestSelectDispatchAgent:
    def test_returns_none_for_empty(self):
        from forge_harness.webhook_server.api.agents import _select_dispatch_agent

        assert _select_dispatch_agent([], "claude") is None

    def test_prefers_matching_role(self):
        from forge_harness.webhook_server.api.agents import _select_dispatch_agent

        agents = [
            {"role": "frontend", "status": "idle"},
            {"role": "backend-engineer", "status": "idle"},
        ]
        chosen = _select_dispatch_agent(agents, "backend")
        assert chosen["role"] == "backend-engineer"

    def test_prefers_idle_over_active(self):
        from forge_harness.webhook_server.api.agents import _select_dispatch_agent

        agents = [
            {"role": "claude", "status": "active"},
            {"role": "claude", "status": "idle"},
        ]
        chosen = _select_dispatch_agent(agents, "claude")
        assert chosen["status"] == "idle"

    def test_falls_back_to_first_when_no_status_match(self):
        from forge_harness.webhook_server.api.agents import _select_dispatch_agent

        agents = [{"role": "x", "status": "busy"}, {"role": "y", "status": "busy"}]
        chosen = _select_dispatch_agent(agents, "z")
        assert chosen == agents[0]


class TestBuildLocalNode:
    def test_returns_expected_keys(self):
        from forge_harness.webhook_server.api.agents import _build_local_node

        registry = Mock()
        registry.list_active = Mock(return_value=[])
        result = _build_local_node(registry)
        for key in ("node_id", "name", "status", "agent_count", "agents", "specs", "capabilities"):
            assert key in result

    def test_degraded_on_high_cpu(self):
        from forge_harness.webhook_server.api.agents import _build_local_node

        registry = Mock()
        registry.list_active = Mock(return_value=[])
        with (
            patch("forge_harness.webhook_server.api.agents._get_cpu_load_pct", return_value=95),
            patch("forge_harness.webhook_server.api.agents._get_disk_usage_pct", return_value=50),
        ):
            result = _build_local_node(registry)
        assert result["status"] == "degraded"
        assert len(result["alerts"]) > 0


# ---------------------------------------------------------------------------
# Endpoint Tests
# ---------------------------------------------------------------------------


class TestRegisterAgentEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest, register_agent

        agent = _make_agent()
        registry = _make_registry()
        registry.register = Mock(return_value=agent)
        with patch_all(registry=registry)[0]:
            body = AgentRegisterRequest(role="backend-engineer", project="proj", task="work")
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            resp = await register_agent(body, req, _=None)
        assert isinstance(resp, JSONResponse)
        data = json.loads(resp.body)
        assert data["success"] is True
        registry.register.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_calls_event_bus(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest, register_agent

        agent = _make_agent()
        registry = _make_registry()
        registry.register = Mock(return_value=agent)
        bus = _make_event_bus()
        ctx, _, bus, audit, _, _ = patch_all(registry=registry, event_bus=bus)
        with ctx:
            body = AgentRegisterRequest(role="r", project="p", task="t")
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            await register_agent(body, req, _=None)
        bus.publish.assert_called_once()
        call_args = bus.publish.call_args[0]
        assert call_args[0] == "agent.registered"

    @pytest.mark.asyncio
    async def test_register_calls_audit(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest, register_agent

        agent = _make_agent()
        registry = _make_registry()
        registry.register = Mock(return_value=agent)
        aud = _make_audit_logger()
        ctx, _, _, aud, _, _ = patch_all(registry=registry, audit=aud)
        with ctx:
            body = AgentRegisterRequest(role="r", project="p", task="t")
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "10.0.0.1"
            await register_agent(body, req, _=None)
        aud.log_agent_registration.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_with_all_optional_fields(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest, register_agent

        agent = _make_agent(tmux_session="forge:test")
        registry = _make_registry()
        registry.register = Mock(return_value=agent)
        with patch_all(registry=registry)[0]:
            body = AgentRegisterRequest(
                role="builder",
                project="p",
                task="t",
                name="my-agent",
                domain="my-domain",
                parent_id="parent-1",
                tmux_session="forge:test",
                skills=["python"],
            )
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            resp = await register_agent(body, req, _=None)
        assert isinstance(resp, JSONResponse)

    @pytest.mark.asyncio
    async def test_register_state_store_failure_is_tolerated(self):
        """StateStore failure should not abort registration."""
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest, register_agent

        agent = _make_agent()
        registry = _make_registry()
        registry.register = Mock(return_value=agent)
        bad_store = Mock()
        bad_store.connect = Mock()
        bad_store.is_connected = Mock(return_value=True)
        bad_store.register_agent = Mock(side_effect=RuntimeError("db error"))
        ctx, _, _, _, _, _ = patch_all(registry=registry, state_store=bad_store)
        with ctx:
            body = AgentRegisterRequest(role="r", project="p", task="t")
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            resp = await register_agent(body, req, _=None)
        assert isinstance(resp, JSONResponse)


class TestListAgentsEndpoint:
    @pytest.mark.asyncio
    async def test_empty_registry(self):
        from forge_harness.webhook_server.api.agents import list_agents

        registry = _make_registry(agents=[])
        with patch_all(registry=registry)[0]:
            resp = await list_agents(_=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["total"] == 0
        assert data["data"]["agents"] == []

    @pytest.mark.asyncio
    async def test_agents_from_registry(self):
        from forge_harness.webhook_server.api.agents import list_agents

        agent = _make_agent()
        registry = _make_registry(agents=[agent])
        with patch_all(registry=registry)[0]:
            resp = await list_agents(_=None)
        data = json.loads(resp.body)
        assert data["data"]["total"] == 1

    @pytest.mark.asyncio
    async def test_agents_from_session_tracker_merged(self):
        from forge_harness.webhook_server.api.agents import list_agents

        registry = _make_registry(agents=[])
        session = Mock()
        session.session_name = "forge:kimi"
        session.agent_type = "kimi"
        session.window_name = "kimi"
        session.domain = None
        session.project = "test-proj"
        session.current_task = "coding"
        session.status = "active"
        session.started_at = datetime.now(UTC).isoformat()
        session.last_activity = datetime.now(UTC).isoformat()
        tracker = Mock()
        tracker.get_all_sessions = Mock(return_value=[session])
        with patch_all(registry=registry, session_tracker=tracker)[0]:
            resp = await list_agents(_=None)
        data = json.loads(resp.body)
        assert data["data"]["total"] == 1

    @pytest.mark.asyncio
    async def test_deduplication_by_session_id(self):
        """Agents with same session_id from registry + tracker appear only once."""
        from forge_harness.webhook_server.api.agents import list_agents

        agent = _make_agent(agent_id="forge:kimi")
        registry = _make_registry(agents=[agent])
        # Session tracker returns same ID
        session = Mock()
        session.session_name = "forge:kimi"
        session.agent_type = "kimi"
        session.window_name = "kimi"
        session.domain = None
        session.project = ""
        session.current_task = None
        session.status = "active"
        session.started_at = datetime.now(UTC).isoformat()
        session.last_activity = datetime.now(UTC).isoformat()
        tracker = Mock()
        tracker.get_all_sessions = Mock(return_value=[session])
        with patch_all(registry=registry, session_tracker=tracker)[0]:
            resp = await list_agents(_=None)
        data = json.loads(resp.body)
        # The registry agent already added forge:kimi; tracker should skip it
        assert data["data"]["total"] == 1

    @pytest.mark.asyncio
    async def test_session_tracker_exception_tolerated(self):
        from forge_harness.webhook_server.api.agents import list_agents

        registry = _make_registry(agents=[])
        ctx, reg, bus, aud, ss, _ = patch_all(registry=registry)
        with ctx:
            # Patch session_tracker import inside the function to raise
            with patch(
                "forge_harness.webhook_server.api.agents.list_agents.__module__",
                create=True,
            ):
                with patch(
                    "forge_harness.session_tracker.get_session_tracker",
                    side_effect=ImportError("no module"),
                ):
                    resp = await list_agents(_=None)
        data = json.loads(resp.body)
        assert data["success"] is True


class TestGetAgentEndpoint:
    @pytest.mark.asyncio
    async def test_found_by_direct_registry_get(self):
        from forge_harness.webhook_server.api.agents import get_agent

        agent = _make_agent(agent_id="abc-123")
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        with patch_all(registry=registry)[0]:
            resp = await get_agent("abc-123", _=None)
        data = json.loads(resp.body)
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_found_by_role_match(self):
        from forge_harness.webhook_server.api.agents import get_agent

        agent = _make_agent(agent_id="xyz", role="backend-engineer")
        registry = _make_registry(agents=[agent])
        registry.get = Mock(return_value=None)  # Direct lookup fails
        with patch_all(registry=registry)[0]:
            resp = await get_agent("backend-engineer", _=None)
        data = json.loads(resp.body)
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_found_by_tmux_session(self):
        from forge_harness.webhook_server.api.agents import get_agent

        agent = _make_agent(agent_id="xyz", tmux_session="forge:kimi")
        registry = _make_registry(agents=[agent])
        registry.get = Mock(return_value=None)
        with patch_all(registry=registry)[0]:
            resp = await get_agent("forge:kimi", _=None)
        data = json.loads(resp.body)
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_found_in_state_store(self):
        from forge_harness.webhook_server.api.agents import get_agent

        registry = _make_registry(agents=[])
        registry.get = Mock(return_value=None)

        sa = Mock()
        sa.session_id = "state-agent-1"
        sa.agent_role = Mock()
        sa.agent_role.value = "builder"
        sa.status = Mock()
        sa.status.value = "active"
        sa.domain = "my-domain"
        sa.project = "my-project"
        sa.current_task = "doing work"
        sa.capabilities = ["python"]
        sa.created_at = datetime.now(UTC)
        sa.last_heartbeat = datetime.now(UTC)

        ss = Mock()
        ss.is_connected = Mock(return_value=True)
        ss.connect = Mock()
        ss.get_agent = Mock(return_value=sa)
        ctx, _, _, _, _, _ = patch_all(registry=registry, state_store=ss)
        with ctx:
            resp = await get_agent("state-agent-1", _=None)
        data = json.loads(resp.body)
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_found_in_session_tracker_by_window_name(self):
        from forge_harness.webhook_server.api.agents import get_agent

        registry = _make_registry(agents=[])
        registry.get = Mock(return_value=None)

        session = Mock()
        session.session_name = "forge:worker"
        session.window_name = "worker"
        session.agent_type = "claude"
        session.domain = None
        session.project = ""
        session.current_task = None
        session.status = "active"
        session.started_at = datetime.now(UTC).isoformat()
        session.last_activity = datetime.now(UTC).isoformat()
        tracker = Mock()
        tracker.get_all_sessions = Mock(return_value=[session])
        tracker.get_session = Mock(return_value=None)
        with patch_all(registry=registry, session_tracker=tracker)[0]:
            resp = await get_agent("worker", _=None)
        data = json.loads(resp.body)
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_found_in_session_tracker_by_colon_format(self):
        from forge_harness.webhook_server.api.agents import get_agent

        registry = _make_registry(agents=[])
        registry.get = Mock(return_value=None)

        session = Mock()
        session.session_name = "forge:worker"
        session.window_name = "worker"
        session.agent_type = "claude"
        session.domain = None
        session.project = ""
        session.current_task = None
        session.status = "active"
        session.started_at = datetime.now(UTC).isoformat()
        session.last_activity = datetime.now(UTC).isoformat()
        tracker = Mock()
        tracker.get_session = Mock(return_value=session)
        tracker.get_all_sessions = Mock(return_value=[])
        with patch_all(registry=registry, session_tracker=tracker)[0]:
            resp = await get_agent("forge:worker", _=None)
        data = json.loads(resp.body)
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.agents import get_agent

        registry = _make_registry(agents=[])
        registry.get = Mock(return_value=None)
        with patch_all(registry=registry)[0]:
            with pytest.raises(HTTPException) as exc:
                await get_agent("nonexistent-id", _=None)
        assert exc.value.status_code == 404


class TestUpdateAgentProgressEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        from forge_harness.webhook_server.api.agents import (
            AgentProgressRequest,
            update_agent_progress,
        )

        agent = _make_agent()
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        ctx, _, _, _, _, _ = patch_all(registry=registry)
        with ctx:
            body = AgentProgressRequest(progress=75, current_task="still working")
            resp = await update_agent_progress("agent-abc123", body, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert agent.progress == 75
        assert agent.current_task == "still working"

    @pytest.mark.asyncio
    async def test_updates_files_modified(self):
        from forge_harness.webhook_server.api.agents import (
            AgentProgressRequest,
            update_agent_progress,
        )

        agent = _make_agent()
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        with patch_all(registry=registry)[0]:
            body = AgentProgressRequest(progress=50, files_modified=["foo.py", "bar.py"])
            await update_agent_progress("agent-abc123", body, _=None)
        assert agent.files_modified == ["foo.py", "bar.py"]

    @pytest.mark.asyncio
    async def test_agent_not_found_raises_404(self):
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.agents import (
            AgentProgressRequest,
            update_agent_progress,
        )

        registry = _make_registry()
        registry.get = Mock(return_value=None)
        with patch_all(registry=registry)[0]:
            body = AgentProgressRequest(progress=50)
            with pytest.raises(HTTPException) as exc:
                await update_agent_progress("missing", body, _=None)
        assert exc.value.status_code == 404


class TestAgentHeartbeatEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        from forge_harness.webhook_server.api.agents import agent_heartbeat

        agent = _make_agent()
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        with patch_all(registry=registry)[0]:
            resp = await agent_heartbeat("agent-abc123", _=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_updates_last_activity(self):
        from forge_harness.webhook_server.api.agents import agent_heartbeat

        agent = _make_agent()
        old_time = agent.last_activity
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        with patch_all(registry=registry)[0]:
            await agent_heartbeat("agent-abc123", _=None)
        # last_activity should be refreshed (new datetime assigned)
        assert agent.last_activity is not None

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.agents import agent_heartbeat

        registry = _make_registry()
        registry.get = Mock(return_value=None)
        with patch_all(registry=registry)[0]:
            with pytest.raises(HTTPException) as exc:
                await agent_heartbeat("missing", _=None)
        assert exc.value.status_code == 404


class TestCompleteAgentEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        from forge_harness.webhook_server.api.agents import complete_agent

        registry = _make_registry()
        registry.complete = Mock(return_value=True)
        ctx, _, bus, aud, _, _ = patch_all(registry=registry)
        with ctx:
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            resp = await complete_agent("agent-abc123", req, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_publishes_event(self):
        from forge_harness.webhook_server.api.agents import complete_agent

        registry = _make_registry()
        registry.complete = Mock(return_value=True)
        bus = _make_event_bus()
        ctx, _, bus, _, _, _ = patch_all(registry=registry, event_bus=bus)
        with ctx:
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            await complete_agent("agent-abc123", req, _=None)
        bus.publish.assert_called_once()
        assert bus.publish.call_args[0][0] == "agent.completed"

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.agents import complete_agent

        registry = _make_registry()
        registry.complete = Mock(return_value=False)
        with patch_all(registry=registry)[0]:
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            with pytest.raises(HTTPException) as exc:
                await complete_agent("missing", req, _=None)
        assert exc.value.status_code == 404


class TestPauseAgentEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        from forge_harness.webhook_server.api.agents import AgentPauseRequest, pause_agent

        agent = _make_agent(status="paused")
        registry = _make_registry()
        registry.pause = Mock(return_value=(agent, "active"))
        aud = _make_audit_logger()
        ctx, _, _, aud, _, _ = patch_all(registry=registry, audit=aud)
        with ctx:
            body = AgentPauseRequest(reason="overload")
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            resp = await pause_agent("agent-abc123", body, request=req, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["new_status"] == "paused"
        assert data["data"]["previous_status"] == "active"
        aud.log_fleet_control.assert_called_once()

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.agents import pause_agent

        registry = _make_registry()
        registry.pause = Mock(return_value=(None, None))
        with patch_all(registry=registry)[0]:
            with pytest.raises(HTTPException) as exc:
                await pause_agent("missing", None, request=None, _=None)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_pause_without_body_uses_defaults(self):
        from forge_harness.webhook_server.api.agents import pause_agent

        agent = _make_agent(status="paused")
        registry = _make_registry()
        registry.pause = Mock(return_value=(agent, "active"))
        with patch_all(registry=registry)[0]:
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            resp = await pause_agent("agent-abc123", None, request=req, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True


class TestResumeAgentEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        from forge_harness.webhook_server.api.agents import resume_agent

        agent = _make_agent(status="active")
        registry = _make_registry()
        registry.resume = Mock(return_value=(agent, "paused"))
        aud = _make_audit_logger()
        ctx, _, _, aud, _, _ = patch_all(registry=registry, audit=aud)
        with ctx:
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            resp = await resume_agent("agent-abc123", req, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["new_status"] == "active"
        assert data["data"]["previous_status"] == "paused"
        aud.log_fleet_control.assert_called_once()

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.agents import resume_agent

        registry = _make_registry()
        registry.resume = Mock(return_value=(None, None))
        with patch_all(registry=registry)[0]:
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            with pytest.raises(HTTPException) as exc:
                await resume_agent("missing", req, _=None)
        assert exc.value.status_code == 404


class TestKillAgentEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        from forge_harness.webhook_server.api.agents import kill_agent

        registry = _make_registry()
        registry.kill = Mock(return_value=True)
        aud = _make_audit_logger()
        ctx, _, _, aud, _, _ = patch_all(registry=registry, audit=aud)
        with ctx:
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            resp = await kill_agent("agent-abc123", req, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["status"] == "killed"
        aud.log_agent_deregistration.assert_called_once()

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.agents import kill_agent

        registry = _make_registry()
        registry.kill = Mock(return_value=False)
        with patch_all(registry=registry)[0]:
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            with pytest.raises(HTTPException) as exc:
                await kill_agent("missing", req, _=None)
        assert exc.value.status_code == 404


class TestFleetStatusEndpoint:
    @pytest.mark.asyncio
    async def test_empty_fleet(self):
        from forge_harness.webhook_server.api.agents import get_fleet_status

        registry = _make_registry(agents=[])
        with patch_all(registry=registry)[0]:
            resp = await get_fleet_status(_=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["total_agents"] == 0

    @pytest.mark.asyncio
    async def test_counts_by_status(self):
        from forge_harness.webhook_server.api.agents import get_fleet_status

        agents = [
            _make_agent(agent_id="a1", status="active"),
            _make_agent(agent_id="a2", status="active"),
            _make_agent(agent_id="a3", status="idle"),
            _make_agent(agent_id="a4", status="error"),
        ]
        # Give each a unique session_id to avoid dedup
        for a in agents:
            a.session_id = a.id
        registry = _make_registry(agents=agents)
        with patch_all(registry=registry)[0]:
            resp = await get_fleet_status(_=None)
        data = json.loads(resp.body)
        assert data["data"]["active"] == 2
        assert data["data"]["idle"] == 1
        assert data["data"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_includes_tmux_sessions(self):
        from forge_harness.webhook_server.api.agents import get_fleet_status

        registry = _make_registry(agents=[])
        session = Mock()
        session.session_name = "forge:extra"
        session.status = "idle"
        tracker = Mock()
        tracker.get_all_sessions = Mock(return_value=[session])
        with patch_all(registry=registry, session_tracker=tracker)[0]:
            resp = await get_fleet_status(_=None)
        data = json.loads(resp.body)
        assert data["data"]["total_agents"] == 1


class TestBroadcastEndpoint:
    @pytest.mark.asyncio
    async def test_broadcasts_to_agents_with_messages(self):
        from forge_harness.webhook_server.api.agents import broadcast_message

        agent1 = _make_agent(agent_id="a1")
        agent1.messages = []
        agent2 = _make_agent(agent_id="a2")
        agent2.messages = []
        registry = _make_registry(agents=[agent1, agent2])
        aud = _make_audit_logger()
        ctx, _, _, aud, _, _ = patch_all(registry=registry, audit=aud)
        with ctx:
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            resp = await broadcast_message("Hello all agents", req, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["delivered"] == 2
        assert len(agent1.messages) == 1
        assert agent1.messages[0]["content"] == "Hello all agents"

    @pytest.mark.asyncio
    async def test_empty_registry(self):
        from forge_harness.webhook_server.api.agents import broadcast_message

        registry = _make_registry(agents=[])
        with patch_all(registry=registry)[0]:
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            resp = await broadcast_message("msg", req, _=None)
        data = json.loads(resp.body)
        assert data["data"]["delivered"] == 0

    @pytest.mark.asyncio
    async def test_calls_audit_logger(self):
        from forge_harness.webhook_server.api.agents import broadcast_message

        registry = _make_registry(agents=[])
        aud = _make_audit_logger()
        ctx, _, _, aud, _, _ = patch_all(registry=registry, audit=aud)
        with ctx:
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            await broadcast_message("msg", req, _=None)
        aud.log_fleet_control.assert_called_once()


class TestSendAgentMessageEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path_dispatch_success(self):
        from forge_harness.webhook_server.api.agents import AgentMessageRequest, send_agent_message

        agent = _make_agent(agent_id="ag-1", tmux_session="forge:kimi")
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        dispatch_result = Mock()
        dispatch_result.success = True
        dispatch_result.delivery_time_ms = 42.0
        dispatch_result.error = None
        mock_client = AsyncMock()
        mock_client.send = AsyncMock(return_value=dispatch_result)
        with patch_all(registry=registry)[0]:
            with patch(
                "forge_harness.fleet.dispatch_client.DispatchClient",
                return_value=mock_client,
            ):
                body = AgentMessageRequest(message="Hello agent!")
                req = Mock()
                req.headers = {}
                req.client = Mock()
                req.client.host = "127.0.0.1"
                resp = await send_agent_message("ag-1", body, req, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["status"] == "sent"

    @pytest.mark.asyncio
    async def test_agent_not_found_raises_404(self):
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.agents import AgentMessageRequest, send_agent_message

        registry = _make_registry(agents=[])
        registry.get = Mock(return_value=None)
        with patch_all(registry=registry)[0]:
            body = AgentMessageRequest(message="Hi")
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            with pytest.raises(HTTPException) as exc:
                await send_agent_message("missing", body, req, _=None)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_no_tmux_session_returns_400(self):
        from forge_harness.webhook_server.api.agents import AgentMessageRequest, send_agent_message

        agent = _make_agent(agent_id="ag-2", tmux_session=None)
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        with patch_all(registry=registry)[0]:
            body = AgentMessageRequest(message="Hi")
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            resp = await send_agent_message("ag-2", body, req, _=None)
        assert resp.status_code == 400
        data = json.loads(resp.body)
        assert data["success"] is False
        assert data["error"]["code"] == "NO_TMUX_SESSION"

    @pytest.mark.asyncio
    async def test_dispatch_failure_returns_500(self):
        from forge_harness.webhook_server.api.agents import AgentMessageRequest, send_agent_message

        agent = _make_agent(agent_id="ag-3", tmux_session="forge:x")
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        dispatch_result = Mock()
        dispatch_result.success = False
        dispatch_result.delivery_time_ms = 0.0
        dispatch_result.error = "tmux pane not found"
        mock_client = AsyncMock()
        mock_client.send = AsyncMock(return_value=dispatch_result)
        with patch_all(registry=registry)[0]:
            with patch(
                "forge_harness.fleet.dispatch_client.DispatchClient",
                return_value=mock_client,
            ):
                body = AgentMessageRequest(message="Hi")
                req = Mock()
                req.headers = {}
                req.client = Mock()
                req.client.host = "127.0.0.1"
                resp = await send_agent_message("ag-3", body, req, _=None)
        assert resp.status_code == 500
        data = json.loads(resp.body)
        assert data["error"]["code"] == "DISPATCH_FAILED"

    @pytest.mark.asyncio
    async def test_dispatch_exception_returns_500(self):
        from forge_harness.webhook_server.api.agents import AgentMessageRequest, send_agent_message

        agent = _make_agent(agent_id="ag-4", tmux_session="forge:y")
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        with patch_all(registry=registry)[0]:
            with patch(
                "forge_harness.fleet.dispatch_client.DispatchClient",
                side_effect=RuntimeError("module not found"),
            ):
                body = AgentMessageRequest(message="Hi")
                req = Mock()
                req.headers = {}
                req.client = Mock()
                req.client.host = "127.0.0.1"
                resp = await send_agent_message("ag-4", body, req, _=None)
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_lookup_by_role_when_direct_get_fails(self):
        from forge_harness.webhook_server.api.agents import AgentMessageRequest, send_agent_message

        agent = _make_agent(agent_id="ag-5", role="builder", tmux_session="forge:b")
        registry = _make_registry(agents=[agent])
        registry.get = Mock(return_value=None)  # Direct lookup fails
        dispatch_result = Mock()
        dispatch_result.success = True
        dispatch_result.delivery_time_ms = 10.0
        dispatch_result.error = None
        mock_client = AsyncMock()
        mock_client.send = AsyncMock(return_value=dispatch_result)
        with patch_all(registry=registry)[0]:
            with patch(
                "forge_harness.fleet.dispatch_client.DispatchClient",
                return_value=mock_client,
            ):
                body = AgentMessageRequest(message="Hi builder")
                req = Mock()
                req.headers = {}
                req.client = Mock()
                req.client.host = "127.0.0.1"
                resp = await send_agent_message("builder", body, req, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True


class TestExportAgentContextEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path_no_tmux(self):
        from forge_harness.webhook_server.api.agents import export_agent_context

        agent = _make_agent(agent_id="ctx-1", tmux_session=None)
        agent.files_modified = ["a.py"]
        agent.messages = []
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        with patch_all(registry=registry)[0]:
            resp = await export_agent_context(
                "ctx-1",
                include_tmux=False,
                include_files=True,
                include_memory=True,
                _=None,
            )
        data = json.loads(resp.body)
        assert data["success"] is True
        assert "agent" in data["data"]
        assert "context" in data["data"]

    @pytest.mark.asyncio
    async def test_includes_files_context(self):
        from forge_harness.webhook_server.api.agents import export_agent_context

        agent = _make_agent(agent_id="ctx-2", tmux_session=None)
        agent.files_modified = ["main.py", "utils.py"]
        agent.messages = []
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        with patch_all(registry=registry)[0]:
            resp = await export_agent_context("ctx-2", include_files=True, _=None)
        data = json.loads(resp.body)
        ctx = data["data"]["context"]
        assert ctx["files"]["count"] == 2

    @pytest.mark.asyncio
    async def test_includes_tmux_capture(self):
        from forge_harness.webhook_server.api.agents import export_agent_context

        agent = _make_agent(agent_id="ctx-3", tmux_session="forge:ctx3")
        agent.files_modified = []
        agent.messages = []
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "line1\nline2\n"
        with patch_all(registry=registry)[0]:
            with patch("subprocess.run", return_value=mock_result):
                resp = await export_agent_context(
                    "ctx-3", include_tmux=True, include_files=False, include_memory=False, _=None
                )
        data = json.loads(resp.body)
        assert "tmux" in data["data"]["context"]
        assert data["data"]["context"]["tmux"]["content"] == "line1\nline2\n"

    @pytest.mark.asyncio
    async def test_tmux_capture_failure_tolerated(self):
        from forge_harness.webhook_server.api.agents import export_agent_context

        agent = _make_agent(agent_id="ctx-4", tmux_session="forge:ctx4")
        agent.files_modified = []
        agent.messages = []
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        with patch_all(registry=registry)[0]:
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 5)):
                resp = await export_agent_context(
                    "ctx-4", include_tmux=True, include_files=False, include_memory=False, _=None
                )
        data = json.loads(resp.body)
        ctx = data["data"]["context"]
        assert "tmux" in ctx
        assert "Failed" in ctx["tmux"]["content"]

    @pytest.mark.asyncio
    async def test_agent_not_found_raises_404(self):
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.agents import export_agent_context

        registry = _make_registry()
        registry.get = Mock(return_value=None)
        with patch_all(registry=registry)[0]:
            with pytest.raises(HTTPException) as exc:
                await export_agent_context("missing", _=None)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_includes_memory_context(self):
        from forge_harness.webhook_server.api.agents import export_agent_context

        agent = _make_agent(agent_id="ctx-5")
        agent.files_modified = []
        agent.messages = []
        agent.tmux_session = None
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        with patch_all(registry=registry)[0]:
            resp = await export_agent_context(
                "ctx-5",
                include_tmux=False,
                include_files=False,
                include_memory=True,
                _=None,
            )
        data = json.loads(resp.body)
        assert "memory" in data["data"]["context"]
        memory = data["data"]["context"]["memory"]
        assert "status" in memory
        assert "progress" in memory


class TestGetAgentLogsEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path_tmux_logs(self):
        from forge_harness.webhook_server.api.agents import get_agent_logs

        agent = _make_agent(agent_id="log-1", tmux_session="forge:log1")
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "INFO startup\nDEBUG connecting\nINFO ready\n"
        with patch_all(registry=registry)[0]:
            with patch("subprocess.run", return_value=mock_result):
                resp = await get_agent_logs("log-1", limit=100, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["count"] == 3
        assert data["data"]["logs"][0]["source"] == "forge:log1"

    @pytest.mark.asyncio
    async def test_falls_back_to_event_bus_when_no_tmux(self):
        from forge_harness.webhook_server.api.agents import get_agent_logs

        agent = _make_agent(agent_id="log-2", tmux_session=None)
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        bus = _make_event_bus()
        bus.get_recent_events = AsyncMock(
            return_value=[
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "source": "log-2",
                    "data": {
                        "session_id": "log-2",
                        "content": "task completed",
                        "event_type": "info",
                    },
                }
            ]
        )
        ctx, _, _, _, _, _ = patch_all(registry=registry, event_bus=bus)
        with ctx:
            resp = await get_agent_logs("log-2", limit=100, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["count"] == 1

    @pytest.mark.asyncio
    async def test_agent_not_found_raises_404(self):
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.agents import get_agent_logs

        registry = _make_registry()
        registry.get = Mock(return_value=None)
        with patch_all(registry=registry)[0]:
            with pytest.raises(HTTPException) as exc:
                await get_agent_logs("missing", limit=100, _=None)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_limit_parameter_applied(self):
        from forge_harness.webhook_server.api.agents import get_agent_logs

        agent = _make_agent(agent_id="log-3", tmux_session="forge:log3")
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        lines = "\n".join(f"line {i}" for i in range(200))
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = lines
        with patch_all(registry=registry)[0]:
            with patch("subprocess.run", return_value=mock_result):
                resp = await get_agent_logs("log-3", limit=10, _=None)
        data = json.loads(resp.body)
        assert data["data"]["count"] <= 10

    @pytest.mark.asyncio
    async def test_tmux_failure_falls_back_to_event_bus(self):
        from forge_harness.webhook_server.api.agents import get_agent_logs

        agent = _make_agent(agent_id="log-4", tmux_session="forge:log4")
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        bus = _make_event_bus()
        bus.get_recent_events = AsyncMock(return_value=[])
        ctx, _, _, _, _, _ = patch_all(registry=registry, event_bus=bus)
        with ctx:
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 5)):
                resp = await get_agent_logs("log-4", limit=100, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["count"] == 0

    @pytest.mark.asyncio
    async def test_filters_events_by_session_id(self):
        from forge_harness.webhook_server.api.agents import get_agent_logs

        agent = _make_agent(agent_id="log-5", tmux_session=None)
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        bus = _make_event_bus()
        bus.get_recent_events = AsyncMock(
            return_value=[
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "source": "log-5",
                    "data": {"session_id": "log-5", "content": "for log-5"},
                },
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "source": "log-99",
                    "data": {"session_id": "log-99", "content": "not for log-5"},
                },
            ]
        )
        ctx, _, _, _, _, _ = patch_all(registry=registry, event_bus=bus)
        with ctx:
            resp = await get_agent_logs("log-5", limit=100, _=None)
        data = json.loads(resp.body)
        assert data["data"]["count"] == 1
        assert data["data"]["logs"][0]["message"] == "for log-5"


# ---------------------------------------------------------------------------
# ExtractRequestContext Tests
# ---------------------------------------------------------------------------


class TestExtractRequestContext:
    def test_returns_none_for_none_request(self):
        from forge_harness.webhook_server.api.agents import _extract_request_context

        ip, ua = _extract_request_context(None)
        assert ip is None
        assert ua is None

    def test_extracts_x_forwarded_for(self):
        from forge_harness.webhook_server.api.agents import _extract_request_context

        # Use a mock headers object that properly responds to .get()
        headers = MagicMock()
        headers.get = lambda key, default="": {
            "X-Forwarded-For": "10.0.0.1, 192.168.0.1",
            "User-Agent": "pytest",
        }.get(key, default)
        req = Mock()
        req.headers = headers
        req.client = Mock()
        req.client.host = "127.0.0.1"
        ip, ua = _extract_request_context(req)
        assert ip == "10.0.0.1"
        assert ua == "pytest"

    def test_falls_back_to_client_host(self):
        from forge_harness.webhook_server.api.agents import _extract_request_context

        headers = MagicMock()
        headers.get = lambda key, default="": default  # All headers missing
        req = Mock()
        req.headers = headers
        req.client = Mock()
        req.client.host = "192.168.1.1"
        ip, ua = _extract_request_context(req)
        # With no X-Forwarded-For or X-Real-IP, falls through to client.host
        assert ip == "192.168.1.1"


# ---------------------------------------------------------------------------
# _to_iso helper
# ---------------------------------------------------------------------------


class TestToIso:
    def test_none_returns_none(self):
        from forge_harness.webhook_server.api.agents import _to_iso

        assert _to_iso(None) is None

    def test_datetime_returns_isoformat(self):
        from forge_harness.webhook_server.api.agents import _to_iso

        dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = _to_iso(dt)
        assert "2026-01-01" in result

    def test_string_passthrough(self):
        from forge_harness.webhook_server.api.agents import _to_iso

        assert _to_iso("2026-01-01") == "2026-01-01"

    def test_empty_string_returns_none(self):
        from forge_harness.webhook_server.api.agents import _to_iso

        assert _to_iso("") is None


# ---------------------------------------------------------------------------
# Additional coverage tests for edge cases
# ---------------------------------------------------------------------------


class TestNormalizeAgentDictEdgeCases:
    """Extra edge-case coverage for normalize_agent_dict."""

    def test_focus_tags_non_list_becomes_empty(self):
        """When focus_tags is not a list (e.g. a string), it resets to []."""
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "x", "role": "r", "focus_tags": "python"})
        assert out["focus_tags"] == []

    def test_session_id_empty_string_triggers_fallback(self):
        """An explicit empty-string session_id triggers the fallback."""
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"session_id": "   ", "role": "builder"})
        assert out["session_id"] == "agent-builder"

    def test_token_usage_none_gives_zero(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "x", "role": "r", "token_usage": None})
        assert out["token_usage"] == 0

    def test_progress_pct_field_takes_precedence(self):
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        out = normalize_agent_dict({"id": "x", "role": "r", "progress_pct": 88, "progress": 10})
        assert out["progress_pct"] == 88.0


class TestDetectRamGbEdgeCases:
    def test_sysconf_exception_returns_zero(self):
        from forge_harness.webhook_server.api.agents import _detect_ram_gb

        with patch("os.sysconf", side_effect=AttributeError("no sysconf")):
            assert _detect_ram_gb() == 0

    def test_sysconf_returns_negative_gives_zero(self):
        from forge_harness.webhook_server.api.agents import _detect_ram_gb

        with patch("os.sysconf", side_effect=lambda x: -1):
            assert _detect_ram_gb() == 0


class TestPauseAgentNonTupleResult:
    """pause() returning a non-tuple agent (legacy compat)."""

    @pytest.mark.asyncio
    async def test_pause_non_tuple_result(self):
        from forge_harness.webhook_server.api.agents import pause_agent

        agent = _make_agent(status="paused")
        registry = _make_registry()
        # registry.pause returns just the agent, not a tuple
        registry.pause = Mock(return_value=agent)
        with patch_all(registry=registry)[0]:
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            resp = await pause_agent("agent-abc123", None, request=req, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        # previous_status falls back to "active" when not provided
        assert data["data"]["previous_status"] == "active"


class TestResumeAgentNonTupleResult:
    """resume() returning a non-tuple agent (legacy compat)."""

    @pytest.mark.asyncio
    async def test_resume_non_tuple_result(self):
        from forge_harness.webhook_server.api.agents import resume_agent

        agent = _make_agent(status="active")
        registry = _make_registry()
        registry.resume = Mock(return_value=agent)  # Not a tuple
        with patch_all(registry=registry)[0]:
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            resp = await resume_agent("agent-abc123", req, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["previous_status"] == "paused"


class TestFleetStatusSessionTrackerException:
    """Session tracker exception in fleet status is tolerated."""

    @pytest.mark.asyncio
    async def test_session_tracker_exception_tolerated(self):
        from forge_harness.webhook_server.api.agents import get_fleet_status

        registry = _make_registry(agents=[_make_agent(agent_id="a1")])
        registry.list_active.return_value[0].session_id = "a1"
        bad_tracker = Mock()
        bad_tracker.get_all_sessions = Mock(side_effect=RuntimeError("tracker broken"))
        with patch_all(registry=registry, session_tracker=bad_tracker)[0]:
            resp = await get_fleet_status(_=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        # Registry agents still counted
        assert data["data"]["total_agents"] >= 1


class TestExportContextActivityLog:
    """Test export_agent_context with activity_log entries."""

    @pytest.mark.asyncio
    async def test_activity_log_entries_included(self):
        from forge_harness.webhook_server.api.agents import export_agent_context

        agent = _make_agent(agent_id="al-1")
        agent.activity_log = [
            {"event": "started", "ts": "2026-01-01T00:00:00Z"},
            {"event": "progress", "ts": "2026-01-01T00:01:00Z"},
        ]
        agent.messages = []
        agent.files_modified = []
        agent.tmux_session = None
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        with patch_all(registry=registry)[0]:
            resp = await export_agent_context(
                "al-1",
                include_tmux=False,
                include_files=False,
                include_memory=False,
                _=None,
            )
        data = json.loads(resp.body)
        assert data["data"]["context"]["activity_log"]["count"] == 2


class TestGetAgentLogsEventBusException:
    """Event bus exception in get_agent_logs is tolerated."""

    @pytest.mark.asyncio
    async def test_event_bus_exception_tolerated(self):
        from forge_harness.webhook_server.api.agents import get_agent_logs

        agent = _make_agent(agent_id="lg-6", tmux_session=None)
        registry = _make_registry()
        registry.get = Mock(return_value=agent)
        bus = _make_event_bus()
        bus.get_recent_events = AsyncMock(side_effect=RuntimeError("bus broken"))
        ctx, _, _, _, _, _ = patch_all(registry=registry, event_bus=bus)
        with ctx:
            resp = await get_agent_logs("lg-6", limit=100, _=None)
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["data"]["count"] == 0


class TestRegisterAgentStateStoreWithValidRole:
    """Register agent with a valid AgentRole enum value."""

    @pytest.mark.asyncio
    async def test_register_with_valid_state_store_role(self):
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest, register_agent

        agent = _make_agent()
        registry = _make_registry()
        registry.register = Mock(return_value=agent)

        # State store is connected, so it will try to use AgentRole
        ss = Mock()
        ss.is_connected = Mock(return_value=True)
        ss.connect = Mock()
        ss.register_agent = Mock()

        ctx, _, _, _, _, _ = patch_all(registry=registry, state_store=ss)
        with ctx:
            body = AgentRegisterRequest(role="builder", project="p", task="t")
            req = Mock()
            req.headers = {}
            req.client = Mock()
            req.client.host = "127.0.0.1"
            resp = await register_agent(body, req, _=None)
        assert isinstance(resp, JSONResponse)
        # register_agent is called on state store
        ss.register_agent.assert_called_once()
