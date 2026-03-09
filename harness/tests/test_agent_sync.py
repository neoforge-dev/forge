"""
Tests for TmuxDBSyncService - Synchronize tmux agent state to database.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.sync.agent_sync import (
    AgentState,
    SyncResult,
    TmuxDBSyncService,
)

# =============================================================================
# Helpers
# =============================================================================


def make_agent_state(
    session_id: str = "forge:test-agent",
    agent_name: str = "test-agent",
    agent_role: str = "developer",
    domain: str | None = None,
    project: str | None = None,
    status: str = "active",
    current_task: str | None = None,
    context_percentage: float = 50.0,
) -> AgentState:
    return AgentState(
        session_id=session_id,
        agent_name=agent_name,
        agent_role=agent_role,
        domain=domain,
        project=project,
        status=status,
        current_task=current_task,
        context_percentage=context_percentage,
        last_activity=datetime.now(UTC),
    )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def forge_root(tmp_path: Path) -> Path:
    """Create a temporary FORGE root directory."""
    root = tmp_path / "forge"
    root.mkdir()
    return root


@pytest.fixture
def sync_service(forge_root: Path) -> TmuxDBSyncService:
    """Create a TmuxDBSyncService with no optional dependencies."""
    return TmuxDBSyncService(forge_root=forge_root)


@pytest.fixture
def mock_event_bus() -> AsyncMock:
    """Create a mock event bus."""
    bus = AsyncMock()
    bus.publish = AsyncMock()
    return bus


@pytest.fixture
def mock_state_store() -> MagicMock:
    """Create a mock state store with update_agent."""
    store = MagicMock()
    store.update_agent = MagicMock()
    return store


@pytest.fixture
def sync_service_with_deps(
    forge_root: Path,
    mock_event_bus: AsyncMock,
    mock_state_store: MagicMock,
) -> TmuxDBSyncService:
    """Create a TmuxDBSyncService with event bus and state store."""
    return TmuxDBSyncService(
        forge_root=forge_root,
        state_store=mock_state_store,
        event_bus=mock_event_bus,
    )


# =============================================================================
# AgentState dataclass
# =============================================================================


class TestAgentState:
    def test_basic_construction(self):
        now = datetime.now(UTC)
        state = AgentState(
            session_id="forge:kimi",
            agent_name="kimi",
            agent_role="developer",
            domain="codeswiftr",
            project="interview-simulator",
            status="active",
            current_task="fixing tests",
            context_percentage=60.0,
            last_activity=now,
        )
        assert state.session_id == "forge:kimi"
        assert state.agent_name == "kimi"
        assert state.agent_role == "developer"
        assert state.domain == "codeswiftr"
        assert state.project == "interview-simulator"
        assert state.status == "active"
        assert state.current_task == "fixing tests"
        assert state.context_percentage == 60.0
        assert state.last_activity is now

    def test_optional_pane_output_defaults_none(self):
        state = make_agent_state()
        assert state.pane_output is None

    def test_pane_output_set(self):
        state = AgentState(
            session_id="forge:x",
            agent_name="x",
            agent_role="developer",
            domain=None,
            project=None,
            status="idle",
            current_task=None,
            context_percentage=30.0,
            last_activity=datetime.now(UTC),
            pane_output="some output",
        )
        assert state.pane_output == "some output"

    def test_none_domain_and_project(self):
        state = make_agent_state(domain=None, project=None)
        assert state.domain is None
        assert state.project is None


# =============================================================================
# SyncResult dataclass
# =============================================================================


class TestSyncResult:
    def test_defaults(self):
        result = SyncResult()
        assert result.agents_found == 0
        assert result.agents_updated == 0
        assert result.agents_unchanged == 0
        assert result.errors == []
        assert isinstance(result.timestamp, datetime)

    def test_errors_are_independent_per_instance(self):
        r1 = SyncResult()
        r2 = SyncResult()
        r1.errors.append("err")
        assert r2.errors == []

    def test_timestamp_is_utc(self):
        result = SyncResult()
        # tzinfo should not be None for UTC-aware datetimes
        assert result.timestamp.tzinfo is not None


# =============================================================================
# TmuxDBSyncService — construction & initial state
# =============================================================================


class TestTmuxDBSyncServiceInit:
    def test_forge_root_is_path(self, forge_root: Path):
        svc = TmuxDBSyncService(forge_root=forge_root)
        assert svc.forge_root == forge_root
        assert isinstance(svc.forge_root, Path)

    def test_forge_root_from_string(self, forge_root: Path):
        svc = TmuxDBSyncService(forge_root=str(forge_root))
        assert svc.forge_root == forge_root

    def test_initial_state(self, sync_service: TmuxDBSyncService):
        assert sync_service._running is False
        assert sync_service._sync_task is None
        assert sync_service._poll_interval == 5.0
        assert sync_service._previous_states == {}
        assert sync_service._sync_count == 0
        assert sync_service._last_sync_result is None

    def test_optional_state_store_defaults_none(self, sync_service: TmuxDBSyncService):
        assert sync_service._state_store is None

    def test_optional_event_bus_defaults_none(self, sync_service: TmuxDBSyncService):
        assert sync_service._event_bus is None

    def test_with_state_store(self, forge_root: Path, mock_state_store: MagicMock):
        svc = TmuxDBSyncService(forge_root=forge_root, state_store=mock_state_store)
        assert svc._state_store is mock_state_store

    def test_with_event_bus(self, forge_root: Path, mock_event_bus: AsyncMock):
        svc = TmuxDBSyncService(forge_root=forge_root, event_bus=mock_event_bus)
        assert svc._event_bus is mock_event_bus


# =============================================================================
# TmuxDBSyncService.get_stats
# =============================================================================


class TestGetStats:
    def test_initial_stats(self, sync_service: TmuxDBSyncService):
        stats = sync_service.get_stats()
        assert stats["running"] is False
        assert stats["sync_count"] == 0
        assert stats["poll_interval"] == 5.0
        assert stats["tracked_agents"] == 0
        assert stats["last_sync"] is None

    def test_stats_after_tracking(self, sync_service: TmuxDBSyncService):
        sync_service._previous_states = {
            "forge:a": make_agent_state("forge:a"),
            "forge:b": make_agent_state("forge:b"),
        }
        sync_service._sync_count = 3
        sync_service._last_sync_result = SyncResult()

        stats = sync_service.get_stats()
        assert stats["tracked_agents"] == 2
        assert stats["sync_count"] == 3
        assert stats["last_sync"] is not None

    def test_stats_last_sync_is_iso_string(self, sync_service: TmuxDBSyncService):
        sync_service._last_sync_result = SyncResult()
        stats = sync_service.get_stats()
        # Should be parseable as ISO datetime
        dt = datetime.fromisoformat(stats["last_sync"])
        assert isinstance(dt, datetime)

    def test_running_flag_in_stats(self, sync_service: TmuxDBSyncService):
        sync_service._running = True
        stats = sync_service.get_stats()
        assert stats["running"] is True


# =============================================================================
# TmuxDBSyncService.start / stop
# =============================================================================


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_sets_running(self, sync_service: TmuxDBSyncService):
        with patch.object(sync_service, "_sync_loop", new_callable=AsyncMock):
            await sync_service.start(poll_interval=1.0)
            assert sync_service._running is True
            assert sync_service._poll_interval == 1.0
            await sync_service.stop()

    @pytest.mark.asyncio
    async def test_start_creates_task(self, sync_service: TmuxDBSyncService):
        with patch.object(sync_service, "_sync_loop", new_callable=AsyncMock):
            await sync_service.start(poll_interval=1.0)
            assert sync_service._sync_task is not None
            await sync_service.stop()

    @pytest.mark.asyncio
    async def test_start_when_already_running_is_noop(self, sync_service: TmuxDBSyncService):
        with patch.object(sync_service, "_sync_loop", new_callable=AsyncMock):
            await sync_service.start(poll_interval=1.0)
            first_task = sync_service._sync_task
            # Second start should not replace the task
            await sync_service.start(poll_interval=2.0)
            assert sync_service._sync_task is first_task
            await sync_service.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running(self, sync_service: TmuxDBSyncService):
        with patch.object(sync_service, "_sync_loop", new_callable=AsyncMock):
            await sync_service.start()
            await sync_service.stop()
            assert sync_service._running is False
            assert sync_service._sync_task is None

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self, sync_service: TmuxDBSyncService):
        # Should not raise
        await sync_service.stop()
        assert sync_service._running is False

    @pytest.mark.asyncio
    async def test_default_poll_interval(self, sync_service: TmuxDBSyncService):
        with patch.object(sync_service, "_sync_loop", new_callable=AsyncMock):
            await sync_service.start()
            assert sync_service._poll_interval == 5.0
            await sync_service.stop()


# =============================================================================
# TmuxDBSyncService._state_changed
# =============================================================================


class TestStateChanged:
    def test_unchanged(self, sync_service: TmuxDBSyncService):
        a = make_agent_state(status="active", current_task="task1", context_percentage=50.0)
        b = make_agent_state(status="active", current_task="task1", context_percentage=50.0)
        assert sync_service._state_changed(a, b) is False

    def test_status_changed(self, sync_service: TmuxDBSyncService):
        a = make_agent_state(status="active")
        b = make_agent_state(status="idle")
        assert sync_service._state_changed(a, b) is True

    def test_current_task_changed(self, sync_service: TmuxDBSyncService):
        a = make_agent_state(current_task="task1")
        b = make_agent_state(current_task="task2")
        assert sync_service._state_changed(a, b) is True

    def test_current_task_changed_none_to_value(self, sync_service: TmuxDBSyncService):
        a = make_agent_state(current_task=None)
        b = make_agent_state(current_task="new task")
        assert sync_service._state_changed(a, b) is True

    def test_context_percentage_within_threshold(self, sync_service: TmuxDBSyncService):
        a = make_agent_state(context_percentage=50.0)
        b = make_agent_state(context_percentage=59.9)
        assert sync_service._state_changed(a, b) is False

    def test_context_percentage_exceeds_threshold(self, sync_service: TmuxDBSyncService):
        a = make_agent_state(context_percentage=50.0)
        b = make_agent_state(context_percentage=61.0)
        assert sync_service._state_changed(a, b) is True

    def test_context_percentage_exactly_at_threshold(self, sync_service: TmuxDBSyncService):
        # Exactly 10 difference — not > 10, so should be False
        a = make_agent_state(context_percentage=50.0)
        b = make_agent_state(context_percentage=60.0)
        assert sync_service._state_changed(a, b) is False

    def test_context_drops_by_more_than_threshold(self, sync_service: TmuxDBSyncService):
        a = make_agent_state(context_percentage=80.0)
        b = make_agent_state(context_percentage=60.0)
        assert sync_service._state_changed(a, b) is True


# =============================================================================
# TmuxDBSyncService._publish_event
# =============================================================================


class TestPublishEvent:
    @pytest.mark.asyncio
    async def test_publishes_to_event_bus(
        self,
        sync_service_with_deps: TmuxDBSyncService,
        mock_event_bus: AsyncMock,
    ):
        await sync_service_with_deps._publish_event("agent.added", {"key": "val"})
        mock_event_bus.publish.assert_awaited_once_with("agent.added", {"key": "val"})

    @pytest.mark.asyncio
    async def test_no_event_bus_is_noop(self, sync_service: TmuxDBSyncService):
        # Should not raise even with no event bus
        await sync_service._publish_event("agent.added", {})

    @pytest.mark.asyncio
    async def test_event_bus_exception_is_swallowed(
        self,
        sync_service_with_deps: TmuxDBSyncService,
        mock_event_bus: AsyncMock,
    ):
        mock_event_bus.publish.side_effect = RuntimeError("bus failure")
        # Should not raise
        await sync_service_with_deps._publish_event("agent.added", {})


# =============================================================================
# TmuxDBSyncService._on_agent_added / _updated / _removed
# =============================================================================


class TestAgentLifecycleEvents:
    @pytest.mark.asyncio
    async def test_on_agent_added_publishes_event(
        self,
        sync_service_with_deps: TmuxDBSyncService,
        mock_event_bus: AsyncMock,
    ):
        agent = make_agent_state(
            session_id="forge:kimi",
            agent_name="kimi",
            status="active",
            domain="codeswiftr",
            project="is",
        )
        await sync_service_with_deps._on_agent_added(agent)

        mock_event_bus.publish.assert_awaited_once()
        call_args = mock_event_bus.publish.call_args
        assert call_args[0][0] == "agent.added"
        payload = call_args[0][1]
        assert payload["session_id"] == "forge:kimi"
        assert payload["agent_name"] == "kimi"
        assert payload["status"] == "active"
        assert payload["domain"] == "codeswiftr"
        assert payload["project"] == "is"

    @pytest.mark.asyncio
    async def test_on_agent_updated_publishes_event(
        self,
        sync_service_with_deps: TmuxDBSyncService,
        mock_event_bus: AsyncMock,
    ):
        previous = make_agent_state(status="active", current_task=None)
        current = make_agent_state(status="idle", current_task="new task")
        await sync_service_with_deps._on_agent_updated(current, previous)

        mock_event_bus.publish.assert_awaited_once()
        call_args = mock_event_bus.publish.call_args
        assert call_args[0][0] == "agent.updated"
        payload = call_args[0][1]
        assert payload["status"] == "idle"
        assert payload["previous_status"] == "active"
        assert payload["current_task"] == "new task"

    @pytest.mark.asyncio
    async def test_on_agent_removed_publishes_event(
        self,
        sync_service_with_deps: TmuxDBSyncService,
        mock_event_bus: AsyncMock,
    ):
        agent = make_agent_state(
            session_id="forge:gone",
            agent_name="gone",
        )
        await sync_service_with_deps._on_agent_removed(agent)

        mock_event_bus.publish.assert_awaited_once()
        call_args = mock_event_bus.publish.call_args
        assert call_args[0][0] == "agent.removed"
        payload = call_args[0][1]
        assert payload["session_id"] == "forge:gone"
        assert payload["agent_name"] == "gone"

    @pytest.mark.asyncio
    async def test_on_agent_added_no_event_bus(self, sync_service: TmuxDBSyncService):
        agent = make_agent_state()
        # Should not raise
        await sync_service._on_agent_added(agent)

    @pytest.mark.asyncio
    async def test_on_agent_updated_no_event_bus(self, sync_service: TmuxDBSyncService):
        prev = make_agent_state()
        curr = make_agent_state(status="idle")
        await sync_service._on_agent_updated(curr, prev)

    @pytest.mark.asyncio
    async def test_on_agent_removed_no_event_bus(self, sync_service: TmuxDBSyncService):
        agent = make_agent_state()
        await sync_service._on_agent_removed(agent)


# =============================================================================
# TmuxDBSyncService._update_state_store
# =============================================================================


class TestUpdateStateStore:
    @pytest.mark.asyncio
    async def test_no_state_store_is_noop(self, sync_service: TmuxDBSyncService):
        agent = make_agent_state()
        # Must not raise
        await sync_service._update_state_store(agent)

    @pytest.mark.asyncio
    async def test_calls_update_agent_on_store(
        self,
        sync_service_with_deps: TmuxDBSyncService,
        mock_state_store: MagicMock,
    ):
        agent = make_agent_state(
            session_id="forge:kimi",
            agent_name="kimi",
            agent_role="developer",
            domain="test-domain",
            project="test-project",
            status="active",
            current_task="task1",
            context_percentage=55.0,
        )
        await sync_service_with_deps._update_state_store(agent)

        mock_state_store.update_agent.assert_called_once()
        call_args = mock_state_store.update_agent.call_args
        assert call_args[0][0] == "forge:kimi"
        agent_data = call_args[0][1]
        assert agent_data["session_id"] == "forge:kimi"
        assert agent_data["agent_name"] == "kimi"
        assert agent_data["agent_role"] == "developer"
        assert agent_data["domain"] == "test-domain"
        assert agent_data["project"] == "test-project"
        assert agent_data["status"] == "active"
        assert agent_data["current_task"] == "task1"
        assert agent_data["context_used_percentage"] == 55.0
        assert "last_heartbeat" in agent_data

    @pytest.mark.asyncio
    async def test_uses_set_when_no_update_agent(self, forge_root: Path):
        store = MagicMock(spec=["set"])
        svc = TmuxDBSyncService(forge_root=forge_root, state_store=store)
        agent = make_agent_state(session_id="forge:x", agent_name="x")
        await svc._update_state_store(agent)

        store.set.assert_called_once()
        key, value = store.set.call_args[0]
        assert key == "agent:forge:x"
        data = json.loads(value)
        assert data["session_id"] == "forge:x"

    @pytest.mark.asyncio
    async def test_state_store_exception_is_swallowed(
        self,
        sync_service_with_deps: TmuxDBSyncService,
        mock_state_store: MagicMock,
    ):
        mock_state_store.update_agent.side_effect = RuntimeError("store failure")
        agent = make_agent_state()
        # Should not raise
        await sync_service_with_deps._update_state_store(agent)


# =============================================================================
# TmuxDBSyncService._get_tmux_agents_blocking
# =============================================================================


class TestGetTmuxAgentsBlocking:
    def test_no_tmux_server_returns_empty(self, sync_service: TmuxDBSyncService):
        """When tmux returncode != 0, return empty list."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            agents = sync_service._get_tmux_agents_blocking()
        assert agents == []

    def test_tmux_not_found_returns_empty(self, sync_service: TmuxDBSyncService):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            agents = sync_service._get_tmux_agents_blocking()
        assert agents == []

    def test_tmux_timeout_returns_empty(self, sync_service: TmuxDBSyncService):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 10)):
            agents = sync_service._get_tmux_agents_blocking()
        assert agents == []

    def test_non_forge_sessions_filtered(self, sync_service: TmuxDBSyncService):
        list_result = MagicMock()
        list_result.returncode = 0
        list_result.stdout = "other-session\nnot-forge\n"
        with patch("subprocess.run", return_value=list_result):
            agents = sync_service._get_tmux_agents_blocking()
        assert agents == []

    def test_forge_sessions_processed(self, sync_service: TmuxDBSyncService):
        list_result = MagicMock()
        list_result.returncode = 0
        list_result.stdout = "forge:kimi\nforge:codex\nnot-forge:other\n"

        pane_result = MagicMock()
        pane_result.returncode = 0
        pane_result.stdout = "$ "

        def fake_run(cmd, **kwargs):
            if "list-sessions" in cmd:
                return list_result
            return pane_result

        with patch("subprocess.run", side_effect=fake_run):
            agents = sync_service._get_tmux_agents_blocking()

        assert len(agents) == 2
        session_ids = {a.session_id for a in agents}
        assert "forge:kimi" in session_ids
        assert "forge:codex" in session_ids

    def test_generic_exception_returns_empty(self, sync_service: TmuxDBSyncService):
        with patch("subprocess.run", side_effect=OSError("unexpected")):
            agents = sync_service._get_tmux_agents_blocking()
        assert agents == []

    def test_empty_session_lines_skipped(self, sync_service: TmuxDBSyncService):
        list_result = MagicMock()
        list_result.returncode = 0
        list_result.stdout = "\n\n"
        with patch("subprocess.run", return_value=list_result):
            agents = sync_service._get_tmux_agents_blocking()
        assert agents == []


# =============================================================================
# TmuxDBSyncService._parse_agent_session_blocking
# =============================================================================


class TestParseAgentSessionBlocking:
    def _make_pane_result(self, output: str, returncode: int = 0) -> MagicMock:
        r = MagicMock()
        r.returncode = returncode
        r.stdout = output
        return r

    def test_simple_session_no_dash(self, sync_service: TmuxDBSyncService):
        with patch("subprocess.run", return_value=self._make_pane_result("")):
            agent = sync_service._parse_agent_session_blocking("forge:dev")
        assert agent is not None
        assert agent.session_id == "forge:dev"
        assert agent.agent_name == "dev"

    def test_pane_capture_failure_uses_empty_string(self, sync_service: TmuxDBSyncService):
        with patch("subprocess.run", return_value=self._make_pane_result("", returncode=1)):
            agent = sync_service._parse_agent_session_blocking("forge:dev")
        assert agent is not None
        assert agent.pane_output is None  # empty string stored as None

    def test_idle_status_from_pane_with_prompt(self, sync_service: TmuxDBSyncService):
        pane = "some output\n$ "
        with patch("subprocess.run", return_value=self._make_pane_result(pane)):
            agent = sync_service._parse_agent_session_blocking("forge:dev")
        assert agent is not None
        assert agent.status == "idle"

    def test_active_status_when_thinking(self, sync_service: TmuxDBSyncService):
        pane = "thinking about this problem\n$"
        with patch("subprocess.run", return_value=self._make_pane_result(pane)):
            agent = sync_service._parse_agent_session_blocking("forge:dev")
        assert agent is not None
        assert agent.status == "active"

    def test_active_status_no_idle_indicator(self, sync_service: TmuxDBSyncService):
        pane = "running tests"
        with patch("subprocess.run", return_value=self._make_pane_result(pane)):
            agent = sync_service._parse_agent_session_blocking("forge:dev")
        assert agent is not None
        assert agent.status == "active"

    def test_current_task_extracted_from_pane(self, sync_service: TmuxDBSyncService):
        pane = "some noise\nTask: writing tests for agent_sync\n$ "
        with patch("subprocess.run", return_value=self._make_pane_result(pane)):
            agent = sync_service._parse_agent_session_blocking("forge:dev")
        assert agent is not None
        assert agent.current_task is not None
        assert "Task" in agent.current_task

    def test_working_on_extracted_from_pane(self, sync_service: TmuxDBSyncService):
        pane = "Working on feature X\n$ "
        with patch("subprocess.run", return_value=self._make_pane_result(pane)):
            agent = sync_service._parse_agent_session_blocking("forge:dev")
        assert agent is not None
        assert agent.current_task is not None

    def test_context_percentage_high_output(self, sync_service: TmuxDBSyncService):
        pane = "x" * 51000  # > 50000
        with patch("subprocess.run", return_value=self._make_pane_result(pane)):
            agent = sync_service._parse_agent_session_blocking("forge:dev")
        assert agent is not None
        assert agent.context_percentage == 90.0

    def test_context_percentage_medium_high_output(self, sync_service: TmuxDBSyncService):
        pane = "x" * 31000  # 30000 < len < 50000
        with patch("subprocess.run", return_value=self._make_pane_result(pane)):
            agent = sync_service._parse_agent_session_blocking("forge:dev")
        assert agent is not None
        assert agent.context_percentage == 70.0

    def test_context_percentage_medium_output(self, sync_service: TmuxDBSyncService):
        pane = "x" * 15000  # 10000 < len < 30000
        with patch("subprocess.run", return_value=self._make_pane_result(pane)):
            agent = sync_service._parse_agent_session_blocking("forge:dev")
        assert agent is not None
        assert agent.context_percentage == 50.0

    def test_context_percentage_low_output(self, sync_service: TmuxDBSyncService):
        pane = "short"
        with patch("subprocess.run", return_value=self._make_pane_result(pane)):
            agent = sync_service._parse_agent_session_blocking("forge:dev")
        assert agent is not None
        assert agent.context_percentage == 30.0

    def test_default_context_percentage_no_pane(self, sync_service: TmuxDBSyncService):
        with patch("subprocess.run", return_value=self._make_pane_result("")):
            agent = sync_service._parse_agent_session_blocking("forge:dev")
        assert agent is not None
        assert agent.context_percentage == 50.0

    def test_pane_output_truncated_to_500(self, sync_service: TmuxDBSyncService):
        pane = "x" * 2000
        with patch("subprocess.run", return_value=self._make_pane_result(pane)):
            agent = sync_service._parse_agent_session_blocking("forge:dev")
        assert agent is not None
        assert len(agent.pane_output) == 500

    def test_role_detection_kimi_in_name(self, sync_service: TmuxDBSyncService):
        with patch("subprocess.run", return_value=self._make_pane_result("")):
            agent = sync_service._parse_agent_session_blocking("forge:project-kimi")
        assert agent is not None
        # kimi in name — should set role to last part
        assert agent.agent_role == "kimi"

    def test_domain_project_extracted_without_tech_keywords(self, sync_service: TmuxDBSyncService):
        with patch("subprocess.run", return_value=self._make_pane_result("")):
            agent = sync_service._parse_agent_session_blocking("forge:codeswiftr-interview-sim")
        assert agent is not None
        assert agent.domain == "codeswiftr"
        assert agent.project == "interview-sim"

    def test_exception_during_parse_returns_none(self, sync_service: TmuxDBSyncService):
        with patch("subprocess.run", side_effect=RuntimeError("boom")):
            agent = sync_service._parse_agent_session_blocking("forge:broken")
        assert agent is None

    def test_session_name_no_colon(self, sync_service: TmuxDBSyncService):
        with patch("subprocess.run", return_value=self._make_pane_result("")):
            agent = sync_service._parse_agent_session_blocking("nocoion")
        assert agent is not None
        assert agent.agent_name == "nocoion"


# =============================================================================
# TmuxDBSyncService._get_tmux_agents (async wrapper)
# =============================================================================


class TestGetTmuxAgentsAsync:
    @pytest.mark.asyncio
    async def test_delegates_to_blocking(self, sync_service: TmuxDBSyncService):
        expected = [make_agent_state()]
        with patch.object(
            sync_service,
            "_get_tmux_agents_blocking",
            return_value=expected,
        ):
            result = await sync_service._get_tmux_agents()
        assert result == expected

    @pytest.mark.asyncio
    async def test_returns_empty_when_blocking_returns_empty(self, sync_service: TmuxDBSyncService):
        with patch.object(sync_service, "_get_tmux_agents_blocking", return_value=[]):
            result = await sync_service._get_tmux_agents()
        assert result == []


# =============================================================================
# TmuxDBSyncService._parse_agent_session (async wrapper)
# =============================================================================


class TestParseAgentSessionAsync:
    @pytest.mark.asyncio
    async def test_delegates_to_blocking(self, sync_service: TmuxDBSyncService):
        expected = make_agent_state()
        with patch.object(
            sync_service,
            "_parse_agent_session_blocking",
            return_value=expected,
        ):
            result = await sync_service._parse_agent_session("forge:x")
        assert result is expected

    @pytest.mark.asyncio
    async def test_returns_none_when_blocking_returns_none(self, sync_service: TmuxDBSyncService):
        with patch.object(sync_service, "_parse_agent_session_blocking", return_value=None):
            result = await sync_service._parse_agent_session("forge:broken")
        assert result is None


# =============================================================================
# TmuxDBSyncService.sync_from_tmux
# =============================================================================


class TestSyncFromTmux:
    @pytest.mark.asyncio
    async def test_no_agents_returns_empty_result(
        self,
        sync_service: TmuxDBSyncService,
    ):
        with patch.object(sync_service, "_get_tmux_agents", new_callable=AsyncMock, return_value=[]):
            result = await sync_service.sync_from_tmux()
        assert result.agents_found == 0
        assert result.agents_updated == 0
        assert result.agents_unchanged == 0
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_new_agent_triggers_added_event(
        self,
        sync_service_with_deps: TmuxDBSyncService,
        mock_event_bus: AsyncMock,
    ):
        agent = make_agent_state(session_id="forge:new")
        with patch.object(
            sync_service_with_deps, "_get_tmux_agents", new_callable=AsyncMock, return_value=[agent]
        ):
            result = await sync_service_with_deps.sync_from_tmux()

        assert result.agents_found == 1
        assert result.agents_updated == 1
        mock_event_bus.publish.assert_awaited()
        event_types = [c[0][0] for c in mock_event_bus.publish.call_args_list]
        assert "agent.added" in event_types

    @pytest.mark.asyncio
    async def test_unchanged_agent_not_counted_as_updated(
        self,
        sync_service_with_deps: TmuxDBSyncService,
        mock_event_bus: AsyncMock,
    ):
        agent = make_agent_state(
            session_id="forge:existing",
            status="active",
            current_task="task1",
            context_percentage=50.0,
        )
        # Pre-populate previous states with same agent
        sync_service_with_deps._previous_states = {"forge:existing": agent}

        with patch.object(
            sync_service_with_deps,
            "_get_tmux_agents",
            new_callable=AsyncMock,
            return_value=[agent],
        ):
            result = await sync_service_with_deps.sync_from_tmux()

        assert result.agents_unchanged == 1
        assert result.agents_updated == 0

    @pytest.mark.asyncio
    async def test_changed_agent_triggers_updated_event(
        self,
        sync_service_with_deps: TmuxDBSyncService,
        mock_event_bus: AsyncMock,
    ):
        prev = make_agent_state(session_id="forge:x", status="active")
        curr = make_agent_state(session_id="forge:x", status="idle")
        sync_service_with_deps._previous_states = {"forge:x": prev}

        with patch.object(
            sync_service_with_deps,
            "_get_tmux_agents",
            new_callable=AsyncMock,
            return_value=[curr],
        ):
            result = await sync_service_with_deps.sync_from_tmux()

        assert result.agents_updated == 1
        event_types = [c[0][0] for c in mock_event_bus.publish.call_args_list]
        assert "agent.updated" in event_types

    @pytest.mark.asyncio
    async def test_removed_agent_triggers_removed_event(
        self,
        sync_service_with_deps: TmuxDBSyncService,
        mock_event_bus: AsyncMock,
    ):
        prev = make_agent_state(session_id="forge:gone")
        sync_service_with_deps._previous_states = {"forge:gone": prev}

        # No agents returned this cycle
        with patch.object(
            sync_service_with_deps,
            "_get_tmux_agents",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await sync_service_with_deps.sync_from_tmux()

        assert result.agents_updated == 1
        event_types = [c[0][0] for c in mock_event_bus.publish.call_args_list]
        assert "agent.removed" in event_types

    @pytest.mark.asyncio
    async def test_previous_states_updated(self, sync_service: TmuxDBSyncService):
        agent = make_agent_state(session_id="forge:a")
        with patch.object(sync_service, "_get_tmux_agents", new_callable=AsyncMock, return_value=[agent]):
            await sync_service.sync_from_tmux()
        assert "forge:a" in sync_service._previous_states

    @pytest.mark.asyncio
    async def test_exception_in_get_agents_returns_error_result(
        self,
        sync_service: TmuxDBSyncService,
    ):
        with patch.object(
            sync_service,
            "_get_tmux_agents",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            result = await sync_service.sync_from_tmux()
        assert len(result.errors) == 1
        assert "boom" in result.errors[0]

    @pytest.mark.asyncio
    async def test_multiple_agents_all_processed(
        self,
        sync_service_with_deps: TmuxDBSyncService,
        mock_state_store: MagicMock,
    ):
        agents = [
            make_agent_state(session_id=f"forge:agent{i}", agent_name=f"agent{i}")
            for i in range(3)
        ]
        with patch.object(
            sync_service_with_deps,
            "_get_tmux_agents",
            new_callable=AsyncMock,
            return_value=agents,
        ):
            result = await sync_service_with_deps.sync_from_tmux()

        assert result.agents_found == 3
        assert result.agents_updated == 3  # all new
        assert mock_state_store.update_agent.call_count == 3

    @pytest.mark.asyncio
    async def test_state_store_called_for_each_agent(
        self,
        sync_service_with_deps: TmuxDBSyncService,
        mock_state_store: MagicMock,
    ):
        agents = [
            make_agent_state(session_id="forge:a"),
            make_agent_state(session_id="forge:b"),
        ]
        with patch.object(
            sync_service_with_deps,
            "_get_tmux_agents",
            new_callable=AsyncMock,
            return_value=agents,
        ):
            await sync_service_with_deps.sync_from_tmux()

        assert mock_state_store.update_agent.call_count == 2


# =============================================================================
# TmuxDBSyncService._sync_loop
# =============================================================================


class TestSyncLoop:
    @pytest.mark.asyncio
    async def test_sync_loop_increments_count(self, sync_service: TmuxDBSyncService):
        """Verify _sync_loop calls sync_from_tmux and increments counter."""
        call_count = 0

        async def fake_sync():
            nonlocal call_count
            call_count += 1
            sync_service._running = False  # Stop after first iteration
            return SyncResult()

        sync_service._running = True
        sync_service._poll_interval = 0.001
        with patch.object(sync_service, "sync_from_tmux", side_effect=fake_sync):
            await sync_service._sync_loop()

        assert call_count == 1
        assert sync_service._sync_count == 1

    @pytest.mark.asyncio
    async def test_sync_loop_stores_last_result(self, sync_service: TmuxDBSyncService):
        result = SyncResult(agents_found=5)

        async def fake_sync():
            sync_service._running = False
            return result

        sync_service._running = True
        sync_service._poll_interval = 0.001
        with patch.object(sync_service, "sync_from_tmux", side_effect=fake_sync):
            await sync_service._sync_loop()

        assert sync_service._last_sync_result is result

    @pytest.mark.asyncio
    async def test_sync_loop_handles_exception_and_continues(self, sync_service: TmuxDBSyncService):
        """Exception in sync_from_tmux should not crash the loop."""
        call_count = 0

        async def fake_sync():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")
            sync_service._running = False
            return SyncResult()

        sync_service._running = True
        sync_service._poll_interval = 0.001
        with patch.object(sync_service, "sync_from_tmux", side_effect=fake_sync):
            await sync_service._sync_loop()

        assert call_count == 2
        # sync_count only increments on success
        assert sync_service._sync_count == 1


# =============================================================================
# Integration: full start/stop cycle
# =============================================================================


class TestStartStopIntegration:
    @pytest.mark.asyncio
    async def test_full_start_stop_cycle(self, sync_service: TmuxDBSyncService):
        """Start the service, let it run briefly, then stop cleanly."""
        with patch.object(
            sync_service,
            "_get_tmux_agents_blocking",
            return_value=[],
        ):
            await sync_service.start(poll_interval=0.05)
            await asyncio.sleep(0.15)
            await sync_service.stop()

        assert sync_service._running is False
        assert sync_service._sync_task is None
        assert sync_service._sync_count >= 1

    @pytest.mark.asyncio
    async def test_stats_updated_after_sync(self, sync_service: TmuxDBSyncService):
        with patch.object(sync_service, "_get_tmux_agents_blocking", return_value=[]):
            await sync_service.start(poll_interval=0.05)
            await asyncio.sleep(0.15)
            await sync_service.stop()

        stats = sync_service.get_stats()
        assert stats["running"] is False
        assert stats["sync_count"] >= 1
        assert stats["last_sync"] is not None
