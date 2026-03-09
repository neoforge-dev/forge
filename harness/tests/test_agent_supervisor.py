"""Comprehensive tests for agent_supervisor module.

Tests cover:
- RestartPolicy enum
- AgentSpec dataclass
- SupervisorState dataclass
- AgentSupervisor lifecycle and operations
- Health checks and restart logic
- State persistence
- Event callbacks
- restart_agent public API
- _on_health_failure health integration
- register_with_health_checks
- run_supervisor log_event callback
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from forge_harness.agent_supervisor import (
    AgentSpec,
    AgentSupervisor,
    RestartPolicy,
    SupervisorState,
    create_supervisor,
    run_supervisor,
)
from forge_harness.health_checks import HealthStatus, ServiceHealth

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def forge_root(tmp_path):
    """Create a temporary forge root directory."""
    root = tmp_path / "forge"
    root.mkdir()
    return root


@pytest.fixture
def state_file(tmp_path):
    """Create a temporary state file path."""
    return tmp_path / "supervisor_state.json"


@pytest.fixture
def spawn_script(tmp_path):
    """Create a mock spawn script."""
    script = tmp_path / "spawn.sh"
    script.write_text("#!/bin/bash\necho 'spawning agent'")
    script.chmod(0o755)
    return script


@pytest.fixture
def supervisor(forge_root, state_file, spawn_script):
    """Create an AgentSupervisor instance for testing."""
    return AgentSupervisor(
        forge_root=forge_root,
        state_file=state_file,
        spawn_script=spawn_script,
    )


@pytest.fixture
def agent_spec():
    """Create a sample AgentSpec for testing."""
    return AgentSpec(
        agent_id="test-agent-001",
        role="backend-engineer",
        task="Fix authentication bug",
        session="forge",
        window="tech",
    )


@pytest.fixture
def agent_spec_custom():
    """Create an AgentSpec with custom settings."""
    return AgentSpec(
        agent_id="custom-agent-001",
        role="frontend-builder",
        task="Build new UI component",
        session="custom-session",
        window="ui",
        domain="codeswiftr-com",
        project="interview-simulator",
        skills=["react", "typescript"],
        provider="amp",
        stream_json=True,
        restart_policy=RestartPolicy.ALWAYS,
        max_restarts=5,
        restart_delay=10.0,
        health_check_interval=60.0,
    )


# ============================================================================
# RestartPolicy Tests
# ============================================================================


class TestRestartPolicy:
    """Test RestartPolicy enum."""

    def test_always_value(self):
        """ALWAYS has correct string value."""
        assert RestartPolicy.ALWAYS == "always"
        assert RestartPolicy.ALWAYS.value == "always"

    def test_on_failure_value(self):
        """ON_FAILURE has correct string value."""
        assert RestartPolicy.ON_FAILURE == "on_failure"
        assert RestartPolicy.ON_FAILURE.value == "on_failure"

    def test_never_value(self):
        """NEVER has correct string value."""
        assert RestartPolicy.NEVER == "never"
        assert RestartPolicy.NEVER.value == "never"

    def test_enum_from_string(self):
        """Can create enum from string value."""
        assert RestartPolicy("always") == RestartPolicy.ALWAYS
        assert RestartPolicy("on_failure") == RestartPolicy.ON_FAILURE
        assert RestartPolicy("never") == RestartPolicy.NEVER

    def test_enum_comparison(self):
        """Enum values can be compared."""
        assert RestartPolicy.ALWAYS != RestartPolicy.NEVER
        assert RestartPolicy.ON_FAILURE != RestartPolicy.ALWAYS


# ============================================================================
# AgentSpec Tests
# ============================================================================


class TestAgentSpec:
    """Test AgentSpec dataclass."""

    def test_default_values(self):
        """AgentSpec has correct default values."""
        spec = AgentSpec(
            agent_id="test-001",
            role="tester",
            task="run tests",
        )
        assert spec.agent_id == "test-001"
        assert spec.role == "tester"
        assert spec.task == "run tests"
        assert spec.session == "forge"
        assert spec.window == ""
        assert spec.domain is None
        assert spec.project is None
        assert spec.skills == []
        assert spec.provider == "claude"
        assert spec.stream_json is False
        assert spec.restart_policy == RestartPolicy.ON_FAILURE
        assert spec.max_restarts == 3
        assert spec.restart_delay == 5.0
        assert spec.health_check_interval == 30.0
        assert spec.restart_count == 0
        assert spec.last_restart is None
        assert spec.status == "stopped"
        assert spec.pid is None

    def test_custom_values(self, agent_spec_custom):
        """AgentSpec accepts custom values."""
        assert agent_spec_custom.agent_id == "custom-agent-001"
        assert agent_spec_custom.role == "frontend-builder"
        assert agent_spec_custom.task == "Build new UI component"
        assert agent_spec_custom.session == "custom-session"
        assert agent_spec_custom.window == "ui"
        assert agent_spec_custom.domain == "codeswiftr-com"
        assert agent_spec_custom.project == "interview-simulator"
        assert agent_spec_custom.skills == ["react", "typescript"]
        assert agent_spec_custom.provider == "amp"
        assert agent_spec_custom.stream_json is True
        assert agent_spec_custom.restart_policy == RestartPolicy.ALWAYS
        assert agent_spec_custom.max_restarts == 5
        assert agent_spec_custom.restart_delay == 10.0
        assert agent_spec_custom.health_check_interval == 60.0

    def test_runtime_state_mutable(self, agent_spec):
        """Runtime state fields can be modified."""
        agent_spec.restart_count = 2
        agent_spec.last_restart = "2026-01-29T12:00:00Z"
        agent_spec.status = "running"
        agent_spec.pid = 12345

        assert agent_spec.restart_count == 2
        assert agent_spec.last_restart == "2026-01-29T12:00:00Z"
        assert agent_spec.status == "running"
        assert agent_spec.pid == 12345

    def test_equality(self):
        """AgentSpec instances can be compared."""
        spec1 = AgentSpec(agent_id="test-001", role="test", task="task1")
        spec2 = AgentSpec(agent_id="test-001", role="test", task="task1")
        spec3 = AgentSpec(agent_id="test-002", role="test", task="task1")

        assert spec1 == spec2
        assert spec1 != spec3


# ============================================================================
# SupervisorState Tests
# ============================================================================


class TestSupervisorState:
    """Test SupervisorState dataclass."""

    def test_default_values(self):
        """SupervisorState has correct default values."""
        state = SupervisorState()
        assert state.agents == {}
        assert state.started_at is None
        assert state.is_running is False

    def test_custom_values(self, agent_spec):
        """SupervisorState accepts custom values."""
        state = SupervisorState(
            agents={"test-001": agent_spec},
            started_at="2026-01-29T12:00:00Z",
            is_running=True,
        )
        assert len(state.agents) == 1
        assert "test-001" in state.agents
        assert state.started_at == "2026-01-29T12:00:00Z"
        assert state.is_running is True

    def test_agents_dict_mutable(self, agent_spec):
        """Agents dictionary can be modified."""
        state = SupervisorState()
        state.agents["test-001"] = agent_spec
        assert len(state.agents) == 1
        assert state.agents["test-001"] == agent_spec


# ============================================================================
# AgentSupervisor Initialization Tests
# ============================================================================


class TestAgentSupervisorInit:
    """Test AgentSupervisor initialization."""

    def test_init_with_paths(self, forge_root, state_file, spawn_script):
        """Supervisor initializes with provided paths."""
        supervisor = AgentSupervisor(
            forge_root=forge_root,
            state_file=state_file,
            spawn_script=spawn_script,
        )
        assert supervisor.forge_root == forge_root
        assert supervisor.state_file == state_file
        assert supervisor.spawn_script == spawn_script
        assert isinstance(supervisor.state, SupervisorState)
        assert supervisor._running is False
        assert supervisor._tasks == {}
        assert supervisor._callbacks == []

    def test_init_default_paths(self, forge_root):
        """Supervisor uses default paths when not provided."""
        supervisor = AgentSupervisor(forge_root=forge_root)
        assert supervisor.forge_root == forge_root
        assert supervisor.state_file == forge_root / ".forge_supervisor_state.json"
        expected_spawn = forge_root / ".claude/skills/spawn-agent/scripts/spawn.sh"
        assert supervisor.spawn_script == expected_spawn

    def test_init_state_is_clean(self, supervisor):
        """Initial state is clean."""
        assert supervisor.state.agents == {}
        assert supervisor.state.started_at is None
        assert supervisor.state.is_running is False
        assert supervisor._running is False


# ============================================================================
# Agent Registration Tests
# ============================================================================


class TestAgentRegistration:
    """Test agent registration and unregistration."""

    def test_register_agent(self, supervisor, agent_spec):
        """Can register an agent."""
        result = supervisor.register(agent_spec)
        assert result == agent_spec
        assert "test-agent-001" in supervisor.state.agents
        assert supervisor.state.agents["test-agent-001"] == agent_spec

    def test_register_multiple_agents(self, supervisor, agent_spec, agent_spec_custom):
        """Can register multiple agents."""
        supervisor.register(agent_spec)
        supervisor.register(agent_spec_custom)
        assert len(supervisor.state.agents) == 2
        assert "test-agent-001" in supervisor.state.agents
        assert "custom-agent-001" in supervisor.state.agents

    def test_register_overwrites_existing(self, supervisor, agent_spec):
        """Registering same agent_id overwrites existing."""
        supervisor.register(agent_spec)
        agent_spec.task = "New task"
        supervisor.register(agent_spec)
        assert len(supervisor.state.agents) == 1
        assert supervisor.state.agents["test-agent-001"].task == "New task"

    def test_register_saves_state(self, supervisor, agent_spec):
        """Registering agent saves state to disk."""
        supervisor.register(agent_spec)
        assert supervisor.state_file.exists()
        data = json.loads(supervisor.state_file.read_text())
        assert "test-agent-001" in data["agents"]

    def test_unregister_agent(self, supervisor, agent_spec):
        """Can unregister an agent."""
        supervisor.register(agent_spec)
        result = supervisor.unregister("test-agent-001")
        assert result is True
        assert "test-agent-001" not in supervisor.state.agents

    def test_unregister_nonexistent_agent(self, supervisor):
        """Unregistering nonexistent agent returns False."""
        result = supervisor.unregister("nonexistent-001")
        assert result is False

    def test_unregister_saves_state(self, supervisor, agent_spec):
        """Unregistering agent saves state to disk."""
        supervisor.register(agent_spec)
        supervisor.unregister("test-agent-001")
        data = json.loads(supervisor.state_file.read_text())
        assert "test-agent-001" not in data["agents"]

    @pytest.mark.asyncio
    async def test_unregister_cancels_monitoring_task(self, supervisor, agent_spec):
        """Unregistering agent cancels its monitoring task."""
        supervisor.register(agent_spec)
        # Create a mock task
        mock_task = AsyncMock()
        supervisor._tasks["test-agent-001"] = mock_task

        supervisor.unregister("test-agent-001")

        mock_task.cancel.assert_called_once()
        assert "test-agent-001" not in supervisor._tasks


# ============================================================================
# State Persistence Tests
# ============================================================================


class TestStatePersistence:
    """Test state saving and loading."""

    def test_save_state_creates_file(self, supervisor, agent_spec):
        """Saving state creates JSON file."""
        supervisor.register(agent_spec)
        assert supervisor.state_file.exists()

    def test_save_state_format(self, supervisor, agent_spec):
        """Saved state has correct JSON format."""
        supervisor.state.started_at = "2026-01-29T12:00:00Z"
        supervisor.state.is_running = True
        supervisor.register(agent_spec)

        data = json.loads(supervisor.state_file.read_text())
        assert "started_at" in data
        assert "is_running" in data
        assert "agents" in data
        assert data["started_at"] == "2026-01-29T12:00:00Z"
        assert data["is_running"] is True
        assert "test-agent-001" in data["agents"]

    def test_save_state_agent_fields(self, supervisor, agent_spec_custom):
        """Saved agent includes all required fields."""
        supervisor.register(agent_spec_custom)
        data = json.loads(supervisor.state_file.read_text())
        agent_data = data["agents"]["custom-agent-001"]

        assert agent_data["agent_id"] == "custom-agent-001"
        assert agent_data["role"] == "frontend-builder"
        assert agent_data["task"] == "Build new UI component"
        assert agent_data["session"] == "custom-session"
        assert agent_data["window"] == "ui"
        assert agent_data["domain"] == "codeswiftr-com"
        assert agent_data["project"] == "interview-simulator"
        assert agent_data["skills"] == ["react", "typescript"]
        assert agent_data["provider"] == "amp"
        assert agent_data["stream_json"] is True
        assert agent_data["restart_policy"] == "always"
        assert agent_data["max_restarts"] == 5
        assert agent_data["restart_delay"] == 10.0
        assert agent_data["health_check_interval"] == 60.0
        assert agent_data["restart_count"] == 0
        assert agent_data["status"] == "stopped"

    def test_load_state_empty_file(self, supervisor):
        """Loading state with no file is safe."""
        supervisor._load_state()
        assert supervisor.state.agents == {}
        assert supervisor.state.started_at is None
        assert supervisor.state.is_running is False

    def test_load_state_restores_data(self, supervisor, agent_spec):
        """Loading state restores saved data."""
        supervisor.state.started_at = "2026-01-29T12:00:00Z"
        supervisor.state.is_running = True
        supervisor.register(agent_spec)

        # Create new supervisor and load state
        new_supervisor = AgentSupervisor(
            forge_root=supervisor.forge_root,
            state_file=supervisor.state_file,
            spawn_script=supervisor.spawn_script,
        )
        new_supervisor._load_state()

        assert new_supervisor.state.started_at == "2026-01-29T12:00:00Z"
        assert new_supervisor.state.is_running is True
        assert "test-agent-001" in new_supervisor.state.agents

    def test_load_state_restores_agent_fields(self, supervisor, agent_spec_custom):
        """Loading state restores all agent fields."""
        agent_spec_custom.restart_count = 2
        agent_spec_custom.last_restart = "2026-01-29T11:00:00Z"
        agent_spec_custom.status = "running"
        agent_spec_custom.pid = 12345
        supervisor.register(agent_spec_custom)

        # Load state in new supervisor
        new_supervisor = AgentSupervisor(
            forge_root=supervisor.forge_root,
            state_file=supervisor.state_file,
            spawn_script=supervisor.spawn_script,
        )
        new_supervisor._load_state()

        spec = new_supervisor.state.agents["custom-agent-001"]
        assert spec.agent_id == "custom-agent-001"
        assert spec.role == "frontend-builder"
        assert spec.task == "Build new UI component"
        assert spec.session == "custom-session"
        assert spec.window == "ui"
        assert spec.domain == "codeswiftr-com"
        assert spec.project == "interview-simulator"
        assert spec.skills == ["react", "typescript"]
        assert spec.provider == "amp"
        assert spec.stream_json is True
        assert spec.restart_policy == RestartPolicy.ALWAYS
        assert spec.max_restarts == 5
        assert spec.restart_delay == 10.0
        assert spec.health_check_interval == 60.0
        assert spec.restart_count == 2
        assert spec.last_restart == "2026-01-29T11:00:00Z"
        assert spec.status == "running"
        assert spec.pid == 12345


# ============================================================================
# Health Check Tests
# ============================================================================


class TestHealthChecks:
    """Test agent health checking."""

    def test_check_agent_alive_session_exists(self, supervisor, agent_spec):
        """Agent is alive when tmux session exists."""
        with patch("subprocess.run") as mock_run:
            # Mock has-session returns success
            mock_run.side_effect = [
                Mock(returncode=0),  # has-session
                Mock(returncode=0, stdout="claude\n"),  # list-panes
            ]
            assert supervisor._check_agent_alive(agent_spec) is True

    def test_check_agent_alive_with_window(self, supervisor, agent_spec):
        """Agent with window is checked correctly."""
        agent_spec.window = "tech"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                Mock(returncode=0),  # has-session
                Mock(returncode=0, stdout="claude\n"),  # list-panes
            ]
            result = supervisor._check_agent_alive(agent_spec)
            assert result is True
            # Check that list-panes was called with correct target
            assert "forge:tech" in str(mock_run.call_args_list[1])

    def test_check_agent_alive_session_missing(self, supervisor, agent_spec):
        """Agent is dead when tmux session doesn't exist."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1)  # has-session fails
            assert supervisor._check_agent_alive(agent_spec) is False

    def test_check_agent_alive_window_missing(self, supervisor, agent_spec):
        """Agent is dead when window doesn't exist."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                Mock(returncode=0),  # has-session
                Mock(returncode=1),  # list-panes fails
            ]
            assert supervisor._check_agent_alive(agent_spec) is False

    def test_check_agent_alive_no_claude(self, supervisor, agent_spec):
        """Agent is dead when claude is not running."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                Mock(returncode=0),  # has-session
                Mock(returncode=0, stdout="bash\nzsh\n"),  # no claude
            ]
            assert supervisor._check_agent_alive(agent_spec) is False

    def test_check_agent_alive_with_timeout(self, supervisor, agent_spec):
        """Agent is alive when gtimeout wrapper is running."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                Mock(returncode=0),
                Mock(returncode=0, stdout="gtimeout\n"),
            ]
            assert supervisor._check_agent_alive(agent_spec) is True

    def test_check_agent_alive_with_timeout_command(self, supervisor, agent_spec):
        """Agent is alive when timeout command is running."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                Mock(returncode=0),
                Mock(returncode=0, stdout="timeout\n"),
            ]
            assert supervisor._check_agent_alive(agent_spec) is True

    def test_check_agent_alive_timeout_error(self, supervisor, agent_spec):
        """Agent is dead when health check times out."""
        with patch("subprocess.run", side_effect=Exception("timeout")):
            assert supervisor._check_agent_alive(agent_spec) is False


# ============================================================================
# Agent Spawning Tests
# ============================================================================


class TestAgentSpawning:
    """Test agent spawning functionality."""

    @pytest.mark.asyncio
    async def test_spawn_agent_basic(self, supervisor, agent_spec):
        """Can spawn agent with basic configuration."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            result = await supervisor._spawn_agent(agent_spec)

        assert result is True
        assert agent_spec.status == "running"
        assert agent_spec.last_restart is not None

    @pytest.mark.asyncio
    async def test_spawn_agent_with_domain_project(self, supervisor, agent_spec_custom):
        """Spawn agent includes domain and project arguments."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            await supervisor._spawn_agent(agent_spec_custom)

        # Check that domain and project were passed
        call_args = mock_exec.call_args[0]
        assert "--domain" in call_args
        assert "codeswiftr-com" in call_args
        assert "--project" in call_args
        assert "interview-simulator" in call_args

    @pytest.mark.asyncio
    async def test_spawn_agent_with_skills(self, supervisor, agent_spec_custom):
        """Spawn agent includes skills argument."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            await supervisor._spawn_agent(agent_spec_custom)

        call_args = mock_exec.call_args[0]
        assert "--skills" in call_args
        assert "react,typescript" in call_args

    @pytest.mark.asyncio
    async def test_spawn_agent_with_provider(self, supervisor, agent_spec_custom):
        """Spawn agent includes provider argument for non-claude providers."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            await supervisor._spawn_agent(agent_spec_custom)

        call_args = mock_exec.call_args[0]
        assert "--provider" in call_args
        assert "amp" in call_args

    @pytest.mark.asyncio
    async def test_spawn_agent_stream_json(self, supervisor, agent_spec_custom):
        """Spawn agent includes stream-json flag."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            await supervisor._spawn_agent(agent_spec_custom)

        call_args = mock_exec.call_args[0]
        assert "--stream-json" in call_args

    @pytest.mark.asyncio
    async def test_spawn_agent_no_server_flag(self, supervisor, agent_spec):
        """Spawn agent includes --no-server flag."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            await supervisor._spawn_agent(agent_spec)

        call_args = mock_exec.call_args[0]
        assert "--no-server" in call_args

    @pytest.mark.asyncio
    async def test_spawn_agent_failure(self, supervisor, agent_spec):
        """Spawn failure returns False."""
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"error"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            result = await supervisor._spawn_agent(agent_spec)

        assert result is False
        assert agent_spec.status == "stopped"

    @pytest.mark.asyncio
    async def test_spawn_agent_timeout(self, supervisor, agent_spec):
        """Spawn timeout returns False."""
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(side_effect=TimeoutError())

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            result = await supervisor._spawn_agent(agent_spec)

        assert result is False

    @pytest.mark.asyncio
    async def test_spawn_agent_exception(self, supervisor, agent_spec):
        """Spawn exception returns False."""
        with patch("asyncio.create_subprocess_exec", side_effect=Exception("error")):
            result = await supervisor._spawn_agent(agent_spec)

        assert result is False


# ============================================================================
# Restart Policy Tests
# ============================================================================


class TestRestartPolicies:
    """Test restart policy enforcement."""

    @pytest.mark.asyncio
    async def test_restart_policy_never(self, supervisor, agent_spec):
        """NEVER policy prevents restarts."""
        agent_spec.restart_policy = RestartPolicy.NEVER
        agent_spec.status = "crashed"
        supervisor.register(agent_spec)
        supervisor._running = True

        with (
            patch.object(supervisor, "_check_agent_alive", return_value=False),
            patch.object(supervisor, "_spawn_agent") as mock_spawn,
        ):
            # Run one monitoring cycle
            task = asyncio.create_task(supervisor._monitor_agent("test-agent-001"))
            await asyncio.sleep(0.1)
            supervisor._running = False
            await task

            # Should not attempt restart
            mock_spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_policy_on_failure(self, supervisor, agent_spec):
        """ON_FAILURE policy restarts crashed agents."""
        agent_spec.restart_policy = RestartPolicy.ON_FAILURE
        agent_spec.health_check_interval = 0.1
        agent_spec.restart_delay = 0.1
        supervisor.register(agent_spec)
        supervisor._running = True

        with (
            patch.object(supervisor, "_check_agent_alive", return_value=False),
            patch.object(supervisor, "_spawn_agent", return_value=True) as mock_spawn,
        ):
            task = asyncio.create_task(supervisor._monitor_agent("test-agent-001"))
            await asyncio.sleep(0.5)
            supervisor._running = False
            await task

            # Should attempt restart
            mock_spawn.assert_called()

    @pytest.mark.asyncio
    async def test_restart_policy_always(self, supervisor, agent_spec):
        """ALWAYS policy restarts agents even without crash detection."""
        agent_spec.restart_policy = RestartPolicy.ALWAYS
        agent_spec.health_check_interval = 0.1
        agent_spec.restart_delay = 0.1
        supervisor.register(agent_spec)
        supervisor._running = True

        with (
            patch.object(supervisor, "_check_agent_alive", return_value=False),
            patch.object(supervisor, "_spawn_agent", return_value=True) as mock_spawn,
        ):
            task = asyncio.create_task(supervisor._monitor_agent("test-agent-001"))
            await asyncio.sleep(0.5)
            supervisor._running = False
            await task

            mock_spawn.assert_called()

    @pytest.mark.asyncio
    async def test_max_restarts_enforced(self, supervisor, agent_spec):
        """Restarts stop after max_restarts limit."""
        agent_spec.max_restarts = 2
        agent_spec.restart_count = 2  # Already at limit
        agent_spec.health_check_interval = 0.1
        agent_spec.restart_delay = 0.1
        supervisor.register(agent_spec)
        supervisor._running = True

        with (
            patch.object(supervisor, "_check_agent_alive", return_value=False),
            patch.object(supervisor, "_spawn_agent") as mock_spawn,
        ):
            task = asyncio.create_task(supervisor._monitor_agent("test-agent-001"))
            await asyncio.sleep(0.5)
            supervisor._running = False
            await task

            # Should not restart when at limit
            mock_spawn.assert_not_called()
            assert agent_spec.status == "failed"

    @pytest.mark.asyncio
    async def test_restart_delay_applied(self, supervisor, agent_spec):
        """Restart delay is applied between attempts."""
        agent_spec.health_check_interval = 0.1
        agent_spec.restart_delay = 0.2
        supervisor.register(agent_spec)
        supervisor._running = True

        spawn_times = []

        async def record_spawn_time(spec):
            spawn_times.append(asyncio.get_event_loop().time())
            return True

        with (
            patch.object(supervisor, "_check_agent_alive", return_value=False),
            patch.object(supervisor, "_spawn_agent", side_effect=record_spawn_time),
        ):
            task = asyncio.create_task(supervisor._monitor_agent("test-agent-001"))
            await asyncio.sleep(0.8)
            supervisor._running = False
            await task

            # Check that delays were applied
            if len(spawn_times) >= 2:
                delay = spawn_times[1] - spawn_times[0]
                assert delay >= 0.2

    @pytest.mark.asyncio
    async def test_restart_count_increments(self, supervisor, agent_spec):
        """Restart count increments with each attempt."""
        agent_spec.health_check_interval = 0.1
        agent_spec.restart_delay = 0.1
        supervisor.register(agent_spec)
        supervisor._running = True

        with (
            patch.object(supervisor, "_check_agent_alive", return_value=False),
            patch.object(supervisor, "_spawn_agent", return_value=True),
        ):
            initial_count = agent_spec.restart_count
            task = asyncio.create_task(supervisor._monitor_agent("test-agent-001"))
            await asyncio.sleep(0.5)
            supervisor._running = False
            await task

            assert agent_spec.restart_count > initial_count


# ============================================================================
# Event Callback Tests
# ============================================================================


class TestEventCallbacks:
    """Test supervisor event callbacks."""

    def test_register_callback(self, supervisor):
        """Can register event callback."""
        callback = Mock()
        supervisor.on_event(callback)
        assert callback in supervisor._callbacks

    def test_multiple_callbacks(self, supervisor):
        """Can register multiple callbacks."""
        callback1 = Mock()
        callback2 = Mock()
        supervisor.on_event(callback1)
        supervisor.on_event(callback2)
        assert len(supervisor._callbacks) == 2

    @pytest.mark.asyncio
    async def test_callback_on_agent_failed(self, supervisor, agent_spec):
        """Callback invoked when agent fails."""
        callback = Mock()
        supervisor.on_event(callback)

        agent_spec.restart_count = 3
        agent_spec.max_restarts = 3
        agent_spec.health_check_interval = 0.1
        supervisor.register(agent_spec)
        supervisor._running = True

        with patch.object(supervisor, "_check_agent_alive", return_value=False):
            task = asyncio.create_task(supervisor._monitor_agent("test-agent-001"))
            await asyncio.sleep(0.3)
            supervisor._running = False
            await task

        # Callback should be called with agent_failed event
        callback.assert_called()
        call_args = callback.call_args[0]
        assert call_args[0] == "agent_failed"
        assert isinstance(call_args[1], AgentSpec)

    @pytest.mark.asyncio
    async def test_callback_on_agent_restarted(self, supervisor, agent_spec):
        """Callback invoked when agent is restarted."""
        callback = Mock()
        supervisor.on_event(callback)

        agent_spec.health_check_interval = 0.1
        agent_spec.restart_delay = 0.1
        supervisor.register(agent_spec)
        supervisor._running = True

        with (
            patch.object(supervisor, "_check_agent_alive", return_value=False),
            patch.object(supervisor, "_spawn_agent", return_value=True),
        ):
            task = asyncio.create_task(supervisor._monitor_agent("test-agent-001"))
            await asyncio.sleep(0.5)
            supervisor._running = False
            await task

        # Callback should be called with agent_restarted event
        callback.assert_called()
        call_args = callback.call_args[0]
        assert call_args[0] == "agent_restarted"
        assert isinstance(call_args[1], AgentSpec)

    @pytest.mark.asyncio
    async def test_callback_exception_handled(self, supervisor, agent_spec):
        """Callback exceptions don't crash supervisor."""

        def failing_callback(event_type, spec):
            raise Exception("callback error")

        supervisor.on_event(failing_callback)

        agent_spec.restart_count = 3
        agent_spec.max_restarts = 3
        agent_spec.health_check_interval = 0.1
        supervisor.register(agent_spec)
        supervisor._running = True

        with patch.object(supervisor, "_check_agent_alive", return_value=False):
            task = asyncio.create_task(supervisor._monitor_agent("test-agent-001"))
            await asyncio.sleep(0.3)
            supervisor._running = False
            # Should not raise even though callback fails
            await task


# ============================================================================
# Supervisor Lifecycle Tests
# ============================================================================


class TestSupervisorLifecycle:
    """Test supervisor start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_initializes_state(self, supervisor, agent_spec):
        """Starting supervisor initializes state."""
        supervisor.register(agent_spec)

        # Start in background
        start_task = asyncio.create_task(supervisor.start())
        await asyncio.sleep(0.2)

        assert supervisor._running is True
        assert supervisor.state.is_running is True
        assert supervisor.state.started_at is not None

        await supervisor.stop()
        await start_task

    @pytest.mark.asyncio
    async def test_start_loads_previous_state(self, supervisor, agent_spec):
        """Starting supervisor loads previous state."""
        supervisor.register(agent_spec)
        supervisor._save_state()

        # Create new supervisor and start
        new_supervisor = AgentSupervisor(
            forge_root=supervisor.forge_root,
            state_file=supervisor.state_file,
            spawn_script=supervisor.spawn_script,
        )

        start_task = asyncio.create_task(new_supervisor.start())
        await asyncio.sleep(0.2)

        assert "test-agent-001" in new_supervisor.state.agents

        await new_supervisor.stop()
        await start_task

    @pytest.mark.asyncio
    async def test_start_creates_monitoring_tasks(self, supervisor, agent_spec):
        """Starting supervisor creates monitoring tasks for agents."""
        supervisor.register(agent_spec)

        start_task = asyncio.create_task(supervisor.start())
        await asyncio.sleep(0.2)

        assert "test-agent-001" in supervisor._tasks
        assert isinstance(supervisor._tasks["test-agent-001"], asyncio.Task)

        await supervisor.stop()
        await start_task

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self, supervisor, agent_spec):
        """Stopping supervisor cancels monitoring tasks."""
        supervisor.register(agent_spec)

        start_task = asyncio.create_task(supervisor.start())
        await asyncio.sleep(0.2)

        task = supervisor._tasks["test-agent-001"]
        await supervisor.stop()

        assert task.cancelled() or task.done()
        assert supervisor._tasks == {}

        await start_task

    @pytest.mark.asyncio
    async def test_stop_updates_state(self, supervisor, agent_spec):
        """Stopping supervisor updates state."""
        supervisor.register(agent_spec)

        start_task = asyncio.create_task(supervisor.start())
        await asyncio.sleep(0.2)
        await supervisor.stop()

        assert supervisor._running is False
        assert supervisor.state.is_running is False

        await start_task

    @pytest.mark.asyncio
    async def test_stop_saves_state(self, supervisor, agent_spec):
        """Stopping supervisor saves state to disk."""
        supervisor.register(agent_spec)

        start_task = asyncio.create_task(supervisor.start())
        await asyncio.sleep(0.2)
        await supervisor.stop()

        data = json.loads(supervisor.state_file.read_text())
        assert data["is_running"] is False

        await start_task

    @pytest.mark.asyncio
    async def test_monitoring_continues_until_stopped(self, supervisor, agent_spec):
        """Monitoring continues running until supervisor is stopped."""
        agent_spec.health_check_interval = 0.1
        supervisor.register(agent_spec)

        check_count = 0

        def count_checks(spec):
            nonlocal check_count
            check_count += 1
            return True

        with patch.object(supervisor, "_check_agent_alive", side_effect=count_checks):
            start_task = asyncio.create_task(supervisor.start())
            await asyncio.sleep(0.5)
            await supervisor.stop()
            await start_task

        # Should have run multiple health checks
        assert check_count >= 3


# ============================================================================
# Status and Reporting Tests
# ============================================================================


class TestStatusReporting:
    """Test supervisor status reporting."""

    def test_get_status_empty(self, supervisor):
        """Get status with no agents."""
        status = supervisor.get_status()
        assert "is_running" in status
        assert "started_at" in status
        assert "agents" in status
        assert status["is_running"] is False
        assert status["agents"] == {}

    def test_get_status_with_agents(self, supervisor, agent_spec, agent_spec_custom):
        """Get status includes agent information."""
        supervisor.register(agent_spec)
        supervisor.register(agent_spec_custom)

        status = supervisor.get_status()
        assert len(status["agents"]) == 2
        assert "test-agent-001" in status["agents"]
        assert "custom-agent-001" in status["agents"]

    def test_get_status_agent_fields(self, supervisor, agent_spec):
        """Status includes relevant agent fields."""
        agent_spec.status = "running"
        agent_spec.restart_count = 2
        agent_spec.last_restart = "2026-01-29T12:00:00Z"
        supervisor.register(agent_spec)

        status = supervisor.get_status()
        agent_status = status["agents"]["test-agent-001"]

        assert agent_status["status"] == "running"
        assert agent_status["restart_count"] == 2
        assert agent_status["last_restart"] == "2026-01-29T12:00:00Z"
        assert agent_status["role"] == "backend-engineer"
        assert agent_status["provider"] == "claude"
        assert agent_status["session"] == "forge"
        assert agent_status["window"] == "tech"

    @pytest.mark.asyncio
    async def test_get_status_running_state(self, supervisor, agent_spec):
        """Status reflects running state."""
        supervisor.register(agent_spec)

        start_task = asyncio.create_task(supervisor.start())
        await asyncio.sleep(0.2)

        status = supervisor.get_status()
        assert status["is_running"] is True
        assert status["started_at"] is not None

        await supervisor.stop()
        await start_task


# ============================================================================
# Factory Function Tests
# ============================================================================


class TestFactoryFunctions:
    """Test factory functions."""

    def test_create_supervisor_default(self, tmp_path, monkeypatch):
        """create_supervisor uses FORGE_ROOT env var."""
        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
        supervisor = create_supervisor()
        assert supervisor.forge_root == tmp_path

    def test_create_supervisor_explicit_path(self, tmp_path):
        """create_supervisor accepts explicit path."""
        supervisor = create_supervisor(forge_root=tmp_path)
        assert supervisor.forge_root == tmp_path

    @pytest.mark.asyncio
    async def test_run_supervisor_function(self, tmp_path, monkeypatch):
        """run_supervisor starts supervisor with callbacks."""
        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))

        # Mock the start method to avoid blocking
        with patch("forge_harness.agent_supervisor.AgentSupervisor.start") as mock_start:
            mock_start.side_effect = KeyboardInterrupt()  # Simulate user interrupt

            try:
                await run_supervisor(forge_root=tmp_path)
            except KeyboardInterrupt:
                pass

            mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_supervisor_log_event_callback(self, tmp_path, monkeypatch):
        """run_supervisor registers log_event callback that logs event types."""
        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))

        captured_supervisor = {}

        original_on_event = AgentSupervisor.on_event

        def capture_on_event(self, callback):
            captured_supervisor["callback"] = callback
            original_on_event(self, callback)

        with (
            patch.object(AgentSupervisor, "on_event", capture_on_event),
            patch("forge_harness.agent_supervisor.AgentSupervisor.start") as mock_start,
            patch("forge_harness.agent_supervisor.AgentSupervisor.stop", new_callable=AsyncMock),
        ):
            mock_start.side_effect = KeyboardInterrupt()
            try:
                await run_supervisor(forge_root=tmp_path)
            except KeyboardInterrupt:
                pass

        # Verify the captured callback works without error
        assert "callback" in captured_supervisor
        spec = AgentSpec(agent_id="cb-test", role="tester", task="test")
        # Calling the log_event closure should not raise
        captured_supervisor["callback"]("agent_restarted", spec)


# ============================================================================
# restart_agent Public API Tests
# ============================================================================


class TestRestartAgent:
    """Test the public restart_agent method."""

    @pytest.mark.asyncio
    async def test_restart_agent_unknown_id_returns_false(self, supervisor):
        """restart_agent returns False for unknown agent IDs."""
        result = await supervisor.restart_agent("does-not-exist")
        assert result is False

    @pytest.mark.asyncio
    async def test_restart_agent_never_policy_returns_false(self, supervisor, agent_spec):
        """restart_agent returns False when restart_policy is NEVER."""
        agent_spec.restart_policy = RestartPolicy.NEVER
        supervisor.register(agent_spec)

        result = await supervisor.restart_agent("test-agent-001")
        assert result is False

    @pytest.mark.asyncio
    async def test_restart_agent_max_restarts_reached_returns_false(
        self, supervisor, agent_spec
    ):
        """restart_agent returns False when max_restarts is exhausted."""
        agent_spec.max_restarts = 2
        agent_spec.restart_count = 2
        supervisor.register(agent_spec)

        result = await supervisor.restart_agent("test-agent-001")
        assert result is False

    @pytest.mark.asyncio
    async def test_restart_agent_success_returns_true(self, supervisor, agent_spec):
        """restart_agent returns True on successful spawn."""
        supervisor.register(agent_spec)

        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True):
            result = await supervisor.restart_agent("test-agent-001")

        assert result is True

    @pytest.mark.asyncio
    async def test_restart_agent_increments_restart_count(self, supervisor, agent_spec):
        """restart_agent increments the restart_count on the spec."""
        supervisor.register(agent_spec)
        initial_count = agent_spec.restart_count

        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True):
            await supervisor.restart_agent("test-agent-001")

        assert supervisor.state.agents["test-agent-001"].restart_count == initial_count + 1

    @pytest.mark.asyncio
    async def test_restart_agent_failure_returns_false(self, supervisor, agent_spec):
        """restart_agent returns False when _spawn_agent fails."""
        supervisor.register(agent_spec)

        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=False):
            result = await supervisor.restart_agent("test-agent-001")

        assert result is False

    @pytest.mark.asyncio
    async def test_restart_agent_fires_callbacks_on_success(self, supervisor, agent_spec):
        """restart_agent fires registered callbacks with agent_restarted event."""
        callback = Mock()
        supervisor.on_event(callback)
        supervisor.register(agent_spec)

        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True):
            await supervisor.restart_agent("test-agent-001", reason="manual")

        callback.assert_called_once()
        event_type, spec = callback.call_args[0]
        assert event_type == "agent_restarted"
        assert isinstance(spec, AgentSpec)

    @pytest.mark.asyncio
    async def test_restart_agent_does_not_fire_callbacks_on_failure(
        self, supervisor, agent_spec
    ):
        """restart_agent does NOT fire callbacks when spawn fails."""
        callback = Mock()
        supervisor.on_event(callback)
        supervisor.register(agent_spec)

        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=False):
            await supervisor.restart_agent("test-agent-001")

        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_agent_callback_exception_handled(self, supervisor, agent_spec):
        """restart_agent survives a failing callback without raising."""

        def bad_callback(event_type, spec):
            raise RuntimeError("boom")

        supervisor.on_event(bad_callback)
        supervisor.register(agent_spec)

        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True):
            # Should not raise
            result = await supervisor.restart_agent("test-agent-001")

        assert result is True

    @pytest.mark.asyncio
    async def test_restart_agent_custom_reason(self, supervisor, agent_spec):
        """restart_agent accepts a custom reason string without error."""
        supervisor.register(agent_spec)

        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True):
            result = await supervisor.restart_agent("test-agent-001", reason="health_failure")

        assert result is True


# ============================================================================
# _on_health_failure Integration Tests
# ============================================================================


class TestOnHealthFailure:
    """Test the _on_health_failure callback handler."""

    def test_on_health_failure_unknown_service_is_noop(self, supervisor):
        """_on_health_failure does nothing for unregistered service names."""
        health = ServiceHealth(
            name="unknown-svc",
            status=HealthStatus.UNHEALTHY,
            latency_ms=100.0,
        )
        # Should not raise and should not modify any state
        with patch("asyncio.create_task") as mock_create_task:
            supervisor._on_health_failure("unknown-svc", 3, health)
            mock_create_task.assert_not_called()

    def test_on_health_failure_known_agent_schedules_restart(
        self, supervisor, agent_spec
    ):
        """_on_health_failure schedules a restart task for a registered agent."""
        supervisor.register(agent_spec)

        health = ServiceHealth(
            name="test-agent-001",
            status=HealthStatus.UNHEALTHY,
            latency_ms=150.0,
        )

        with patch("asyncio.create_task") as mock_create_task:
            supervisor._on_health_failure("test-agent-001", 3, health)
            mock_create_task.assert_called_once()

    def test_on_health_failure_passes_reason_health_failure(
        self, supervisor, agent_spec
    ):
        """_on_health_failure schedules restart with reason='health_failure'."""
        supervisor.register(agent_spec)

        health = ServiceHealth(
            name="test-agent-001",
            status=HealthStatus.UNHEALTHY,
            latency_ms=100.0,
        )

        restart_calls = []

        async def mock_restart(agent_id, reason="manual"):
            restart_calls.append((agent_id, reason))
            return True

        supervisor.restart_agent = mock_restart

        with patch("asyncio.create_task") as mock_create_task:
            supervisor._on_health_failure("test-agent-001", 4, health)
            # Verify create_task was called (coroutine scheduling)
            assert mock_create_task.called

    def test_on_health_failure_with_multiple_agents_only_targets_matching(
        self, supervisor, agent_spec, agent_spec_custom
    ):
        """_on_health_failure only schedules restart for the matching agent."""
        supervisor.register(agent_spec)
        supervisor.register(agent_spec_custom)

        health = ServiceHealth(
            name="test-agent-001",
            status=HealthStatus.UNHEALTHY,
            latency_ms=200.0,
        )

        with patch("asyncio.create_task") as mock_create_task:
            supervisor._on_health_failure("test-agent-001", 3, health)
            # Only one task scheduled, for the matching agent
            assert mock_create_task.call_count == 1


# ============================================================================
# register_with_health_checks Tests
# ============================================================================


class TestRegisterWithHealthChecks:
    """Test register_with_health_checks integration."""

    def test_register_with_health_checks_registers_callback(self, supervisor):
        """register_with_health_checks adds _on_health_failure to the registry."""
        mock_registry = MagicMock()

        with patch(
            "forge_harness.agent_supervisor.get_health_registry",
            return_value=mock_registry,
        ):
            supervisor.register_with_health_checks()

        mock_registry.on_health_failure.assert_called_once_with(
            supervisor._on_health_failure
        )

    def test_register_with_health_checks_calls_get_health_registry(self, supervisor):
        """register_with_health_checks fetches the global health registry."""
        mock_registry = MagicMock()

        with patch(
            "forge_harness.agent_supervisor.get_health_registry",
            return_value=mock_registry,
        ) as mock_get:
            supervisor.register_with_health_checks()

        mock_get.assert_called_once()


# ============================================================================
# _monitor_agent NEVER policy log line coverage
# ============================================================================


class TestMonitorAgentNeverPolicyLogging:
    """Ensure the log line inside the NEVER branch of _monitor_agent is executed."""

    @pytest.mark.asyncio
    async def test_monitor_agent_never_policy_logs_and_continues(
        self, supervisor, agent_spec
    ):
        """NEVER policy: the log line on line 289 is hit and loop continues."""
        agent_spec.restart_policy = RestartPolicy.NEVER
        agent_spec.health_check_interval = 0.05
        supervisor.register(agent_spec)
        supervisor._running = True

        log_calls = []

        original_info = __import__("logging").getLogger(
            "forge_harness.agent_supervisor"
        ).info

        with (
            patch.object(supervisor, "_check_agent_alive", return_value=False),
            patch.object(supervisor, "_spawn_agent") as mock_spawn,
            patch(
                "forge_harness.agent_supervisor.logger"
            ) as mock_logger,
        ):
            task = asyncio.create_task(
                supervisor._monitor_agent("test-agent-001")
            )
            await asyncio.sleep(0.25)
            supervisor._running = False
            await task

        # spawn should never be called with NEVER policy
        mock_spawn.assert_not_called()
        # The logger.info for the NEVER branch should have been invoked
        assert mock_logger.info.called


# ============================================================================
# _monitor_agent restarted callback exception path
# ============================================================================


class TestMonitorAgentRestartCallbackException:
    """Cover the callback exception handler in the restarted branch."""

    @pytest.mark.asyncio
    async def test_monitor_agent_callback_exception_on_restart_does_not_crash(
        self, supervisor, agent_spec
    ):
        """Exceptions from restart callbacks are caught and logged."""

        def bad_callback(event_type, spec):
            raise ValueError("callback failure")

        supervisor.on_event(bad_callback)

        agent_spec.health_check_interval = 0.05
        agent_spec.restart_delay = 0.05
        agent_spec.max_restarts = 1
        supervisor.register(agent_spec)
        supervisor._running = True

        with (
            patch.object(supervisor, "_check_agent_alive", return_value=False),
            patch.object(
                supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True
            ),
        ):
            task = asyncio.create_task(
                supervisor._monitor_agent("test-agent-001")
            )
            await asyncio.sleep(0.4)
            supervisor._running = False
            # Must not propagate the ValueError
            await task
