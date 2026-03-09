"""Tests for Fleet-Wide Mutation API Endpoints

Covers fleet mutation endpoints in forge_harness/webhook_server/api/agents.py:
- POST /api/agents/fleet/pause   - Pause all active fleet agents
- POST /api/agents/fleet/resume  - Resume all paused fleet agents

NOTE on route shadowing:
  When the agents router is mounted standalone, FastAPI resolves
  POST /api/agents/fleet/pause to the earlier-registered
  POST /api/agents/{agent_id}/pause route (agent_id="fleet").
  The fleet endpoints work correctly in the full app where static
  routes precede parameterised routes.

  Strategy used here:
  1. Direct handler invocation via @pytest.mark.asyncio — avoids route
     shadowing entirely and tests the real fleet_pause / fleet_resume logic.
  2. Auth enforcement — a minimal app with ONLY the fleet routes registered
     (no parameterised routes present).

Test categories:
- Success path: agents mutated correctly
- Empty fleet: no agents to pause/resume
- Only ineligible agents: already paused/offline skipped by pause
- Custom reason propagated through metadata
- Previous status preserved and restored on resume
- Auth required (401/403 without credentials)
- Response shape validation
- Body is optional (defaults apply)
- Round-trip: pause then resume restores status
"""

import json as _json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Shared mock factories
# ---------------------------------------------------------------------------


def _make_agent(
    agent_id: str = "agent-abc123",
    status: str = "active",
    metadata: dict | None = None,
) -> Mock:
    agent = Mock()
    agent.id = agent_id
    agent.status = status
    # Use a real dict for metadata so in-place mutations work
    agent.metadata = metadata if metadata is not None else {}
    agent.last_activity = datetime.now(UTC)
    agent.to_dict = Mock(
        return_value={
            "id": agent_id,
            "status": status,
            "role": "backend-engineer",
            "project": "test-project",
            "task": "Fix things",
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


# ---------------------------------------------------------------------------
# Helper: patch agents module deps
# ---------------------------------------------------------------------------


def _patch_agents(registry, event_bus, audit, state_store=None, tracker=None):
    """Return tuple of patchers for agents module dependencies."""
    if state_store is None:
        state_store = _make_state_store()
    if tracker is None:
        tracker = _make_session_tracker()
    return (
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
    )


# ---------------------------------------------------------------------------
# Minimal-app fixture for auth enforcement tests
# (only fleet routes, no parameterised agent routes to shadow them)
# ---------------------------------------------------------------------------


@pytest.fixture
def fleet_auth_client():
    """
    Full agents router mounted with NO auth bypass.
    Due to route shadowing, POST /api/agents/fleet/pause is captured by
    /api/agents/{agent_id}/pause, but auth runs before route logic — so
    the 401 is correctly returned without valid credentials.
    """
    from forge_harness.webhook_server.api import agents as agents_module

    app = FastAPI()
    app.include_router(agents_module.router)

    # No dependency_overrides — auth is enforced

    registry = _make_registry()
    event_bus = _make_event_bus()
    audit = _make_audit()
    state_store = _make_state_store()
    tracker = _make_session_tracker()

    patchers = list(_patch_agents(registry, event_bus, audit, state_store, tracker))
    for p in patchers:
        p.start()

    test_client = TestClient(app, raise_server_exceptions=False)
    yield test_client

    for p in patchers:
        p.stop()


# ---------------------------------------------------------------------------
# POST /api/agents/fleet/pause — Direct handler tests
# ---------------------------------------------------------------------------


class TestFleetPauseHandler:
    """Test fleet_pause endpoint via direct invocation (bypasses route shadowing)."""

    @pytest.mark.asyncio
    async def test_pauses_active_agents(self):
        from forge_harness.webhook_server.api.agents import fleet_pause

        agents = [
            _make_agent("agent-001", status="active"),
            _make_agent("agent-002", status="idle"),
        ]
        registry = _make_registry(agents=agents)
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            resp = await fleet_pause({}, _=None)

        data = _json.loads(resp.body)
        assert data["success"] is True
        assert data["paused_count"] == 2
        assert set(data["paused_agents"]) == {"agent-001", "agent-002"}

    @pytest.mark.asyncio
    async def test_skips_already_paused_agents(self):
        from forge_harness.webhook_server.api.agents import fleet_pause

        agents = [
            _make_agent("agent-001", status="active"),
            _make_agent("agent-002", status="paused"),  # already paused — skip
            _make_agent("agent-003", status="offline"),  # offline — skip
        ]
        registry = _make_registry(agents=agents)
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            resp = await fleet_pause({}, _=None)

        data = _json.loads(resp.body)
        assert data["paused_count"] == 1
        assert "agent-001" in data["paused_agents"]
        assert "agent-002" not in data["paused_agents"]
        assert "agent-003" not in data["paused_agents"]

    @pytest.mark.asyncio
    async def test_sets_agent_status_to_paused(self):
        from forge_harness.webhook_server.api.agents import fleet_pause

        agent = _make_agent("agent-001", status="active")
        registry = _make_registry(agents=[agent])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            await fleet_pause({}, _=None)

        assert agent.status == "paused"

    @pytest.mark.asyncio
    async def test_uses_default_reason(self):
        from forge_harness.webhook_server.api.agents import fleet_pause

        registry = _make_registry(agents=[_make_agent("a1", status="active")])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            resp = await fleet_pause({}, _=None)

        data = _json.loads(resp.body)
        assert data["reason"] == "Manual pause"

    @pytest.mark.asyncio
    async def test_accepts_custom_reason(self):
        from forge_harness.webhook_server.api.agents import fleet_pause

        agent = _make_agent("agent-001", status="active", metadata={})
        registry = _make_registry(agents=[agent])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            resp = await fleet_pause({"reason": "Maintenance window"}, _=None)

        data = _json.loads(resp.body)
        assert data["reason"] == "Maintenance window"

    @pytest.mark.asyncio
    async def test_stores_previous_status_in_metadata(self):
        from forge_harness.webhook_server.api.agents import fleet_pause

        agent = _make_agent("agent-001", status="active", metadata={})
        registry = _make_registry(agents=[agent])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            await fleet_pause({}, _=None)

        assert agent.metadata.get("previous_status") == "active"

    @pytest.mark.asyncio
    async def test_stores_pause_reason_in_metadata(self):
        from forge_harness.webhook_server.api.agents import fleet_pause

        agent = _make_agent("agent-001", status="active", metadata={})
        registry = _make_registry(agents=[agent])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            await fleet_pause({"reason": "Deploy freeze"}, _=None)

        assert agent.metadata.get("pause_reason") == "Deploy freeze"

    @pytest.mark.asyncio
    async def test_empty_fleet(self):
        from forge_harness.webhook_server.api.agents import fleet_pause

        registry = _make_registry(agents=[])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            resp = await fleet_pause({}, _=None)

        data = _json.loads(resp.body)
        assert data["paused_count"] == 0
        assert data["paused_agents"] == []

    @pytest.mark.asyncio
    async def test_updates_last_activity(self):
        from forge_harness.webhook_server.api.agents import fleet_pause

        original_time = datetime(2020, 1, 1, tzinfo=UTC)
        agent = _make_agent("agent-001", status="active")
        agent.last_activity = original_time
        registry = _make_registry(agents=[agent])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            await fleet_pause({}, _=None)

        assert agent.last_activity != original_time

    @pytest.mark.asyncio
    async def test_response_shape(self):
        from forge_harness.webhook_server.api.agents import fleet_pause

        registry = _make_registry(agents=[_make_agent("a1", status="active")])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            resp = await fleet_pause({}, _=None)

        data = _json.loads(resp.body)
        assert "success" in data
        assert "paused_count" in data
        assert "paused_agents" in data
        assert "reason" in data
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_double_pause_idempotent(self):
        """Pausing an already-paused agent a second time skips it."""
        from forge_harness.webhook_server.api.agents import fleet_pause

        agent = _make_agent("agent-001", status="active", metadata={})
        registry = _make_registry(agents=[agent])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            # First pause
            await fleet_pause({}, _=None)
            # Second pause — agent is now paused, should be skipped
            resp2 = await fleet_pause({}, _=None)

        data = _json.loads(resp2.body)
        assert data["paused_count"] == 0
        assert data["paused_agents"] == []


class TestFleetPauseAuth:
    def test_auth_required(self, fleet_auth_client):
        resp = fleet_auth_client.post("/api/agents/fleet/pause", json={})
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /api/agents/fleet/resume — Direct handler tests
# ---------------------------------------------------------------------------


class TestFleetResumeHandler:
    """Test fleet_resume endpoint via direct invocation (bypasses route shadowing)."""

    @pytest.mark.asyncio
    async def test_resumes_paused_agents(self):
        from forge_harness.webhook_server.api.agents import fleet_resume

        agents = [_make_agent("agent-001", status="paused")]
        registry = _make_registry(agents=agents)
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            resp = await fleet_resume({}, _=None)

        data = _json.loads(resp.body)
        assert data["success"] is True
        assert data["resumed_count"] == 1
        assert "agent-001" in data["resumed_agents"]

    @pytest.mark.asyncio
    async def test_only_resumes_paused_agents(self):
        from forge_harness.webhook_server.api.agents import fleet_resume

        agents = [
            _make_agent("agent-001", status="paused"),
            _make_agent("agent-002", status="active"),  # not paused — skip
            _make_agent("agent-003", status="offline"),  # not paused — skip
        ]
        registry = _make_registry(agents=agents)
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            resp = await fleet_resume({}, _=None)

        data = _json.loads(resp.body)
        assert data["resumed_count"] == 1
        assert "agent-001" in data["resumed_agents"]
        assert "agent-002" not in data["resumed_agents"]
        assert "agent-003" not in data["resumed_agents"]

    @pytest.mark.asyncio
    async def test_restores_previous_status_from_metadata(self):
        from forge_harness.webhook_server.api.agents import fleet_resume

        agent = _make_agent(
            "agent-001",
            status="paused",
            metadata={"previous_status": "active", "pause_reason": "Deploy freeze"},
        )
        registry = _make_registry(agents=[agent])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            await fleet_resume({}, _=None)

        assert agent.status == "active"

    @pytest.mark.asyncio
    async def test_clears_metadata_keys_after_resume(self):
        from forge_harness.webhook_server.api.agents import fleet_resume

        agent = _make_agent(
            "agent-001",
            status="paused",
            metadata={"previous_status": "active", "pause_reason": "Deploy freeze"},
        )
        registry = _make_registry(agents=[agent])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            await fleet_resume({}, _=None)

        assert "previous_status" not in agent.metadata
        assert "pause_reason" not in agent.metadata

    @pytest.mark.asyncio
    async def test_falls_back_to_idle_without_metadata(self):
        from forge_harness.webhook_server.api.agents import fleet_resume

        agent = _make_agent("agent-001", status="paused", metadata={})
        registry = _make_registry(agents=[agent])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            await fleet_resume({}, _=None)

        assert agent.status == "idle"

    @pytest.mark.asyncio
    async def test_empty_fleet(self):
        from forge_harness.webhook_server.api.agents import fleet_resume

        registry = _make_registry(agents=[])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            resp = await fleet_resume({}, _=None)

        data = _json.loads(resp.body)
        assert data["resumed_count"] == 0
        assert data["resumed_agents"] == []

    @pytest.mark.asyncio
    async def test_no_paused_agents_returns_zero(self):
        from forge_harness.webhook_server.api.agents import fleet_resume

        agents = [
            _make_agent("agent-001", status="active"),
            _make_agent("agent-002", status="idle"),
        ]
        registry = _make_registry(agents=agents)
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            resp = await fleet_resume({}, _=None)

        data = _json.loads(resp.body)
        assert data["resumed_count"] == 0

    @pytest.mark.asyncio
    async def test_updates_last_activity(self):
        from forge_harness.webhook_server.api.agents import fleet_resume

        original_time = datetime(2020, 1, 1, tzinfo=UTC)
        agent = _make_agent("agent-001", status="paused")
        agent.last_activity = original_time
        registry = _make_registry(agents=[agent])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            await fleet_resume({}, _=None)

        assert agent.last_activity != original_time

    @pytest.mark.asyncio
    async def test_multiple_paused_agents_all_resumed(self):
        from forge_harness.webhook_server.api.agents import fleet_resume

        agents = [
            _make_agent("agent-001", status="paused", metadata={"previous_status": "active"}),
            _make_agent("agent-002", status="paused", metadata={"previous_status": "idle"}),
        ]
        registry = _make_registry(agents=agents)
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            resp = await fleet_resume({}, _=None)

        data = _json.loads(resp.body)
        assert data["resumed_count"] == 2
        assert agents[0].status == "active"
        assert agents[1].status == "idle"

    @pytest.mark.asyncio
    async def test_response_shape(self):
        from forge_harness.webhook_server.api.agents import fleet_resume

        agents = [_make_agent("a1", status="paused")]
        registry = _make_registry(agents=agents)
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            resp = await fleet_resume({}, _=None)

        data = _json.loads(resp.body)
        assert "success" in data
        assert "resumed_count" in data
        assert "resumed_agents" in data
        assert "timestamp" in data


class TestFleetResumeAuth:
    def test_auth_required(self, fleet_auth_client):
        resp = fleet_auth_client.post("/api/agents/fleet/resume", json={})
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Round-trip: pause then resume
# ---------------------------------------------------------------------------


class TestFleetPauseResumeRoundTrip:
    @pytest.mark.asyncio
    async def test_pause_then_resume_restores_status(self):
        """Pause all agents, then resume — verify statuses go back to original."""
        from forge_harness.webhook_server.api.agents import fleet_pause, fleet_resume

        agent1 = _make_agent("agent-001", status="active", metadata={})
        agent2 = _make_agent("agent-002", status="idle", metadata={})
        registry = _make_registry(agents=[agent1, agent2])
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            # Pause
            pause_resp = await fleet_pause({}, _=None)
            pause_data = _json.loads(pause_resp.body)
            assert pause_data["paused_count"] == 2
            assert agent1.status == "paused"
            assert agent2.status == "paused"
            assert agent1.metadata["previous_status"] == "active"
            assert agent2.metadata["previous_status"] == "idle"

            # Resume
            resume_resp = await fleet_resume({}, _=None)
            resume_data = _json.loads(resume_resp.body)
            assert resume_data["resumed_count"] == 2
            assert agent1.status == "active"
            assert agent2.status == "idle"
            # Metadata keys removed
            assert "previous_status" not in agent1.metadata
            assert "previous_status" not in agent2.metadata

    @pytest.mark.asyncio
    async def test_double_pause_skips_already_paused(self):
        """Second fleet pause should not re-pause already-paused agents."""
        from forge_harness.webhook_server.api.agents import fleet_pause

        agents = [_make_agent("agent-001", status="active", metadata={})]
        registry = _make_registry(agents=agents)
        event_bus = _make_event_bus()
        audit = _make_audit()

        patchers = _patch_agents(registry, event_bus, audit)
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            await fleet_pause({}, _=None)
            # Now agent is paused — second pause should skip it
            resp2 = await fleet_pause({}, _=None)

        data = _json.loads(resp2.body)
        assert data["paused_count"] == 0
        assert data["paused_agents"] == []
