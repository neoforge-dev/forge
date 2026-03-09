"""Pure unit tests for forge_harness.agent_supervisor.

Covers:
- RestartPolicy enum
- AgentSpec dataclass (defaults, custom, mutable runtime state, equality)
- SupervisorState dataclass
- AgentSupervisor.__init__ (default/explicit paths, clean initial state)
- _save_state / _load_state (round-trip, missing file, all field types)
- register / unregister (state mutations, task cancellation, disk persistence)
- _check_agent_alive (all subprocess branches, command variations)
- _spawn_agent (success, failure, timeout, exception, arg construction)
- _monitor_agent (alive / dead / NEVER / ALWAYS / ON_FAILURE, max_restarts,
                  restart_count, callbacks on restarted / failed, callback exceptions)
- on_event / callbacks
- restart_agent (unknown id, NEVER policy, max exceeded, success, failure,
                 callbacks, callback exception, custom reason)
- _on_health_failure (unknown service, known agent, multi-agent targeting)
- register_with_health_checks
- get_status (empty, with agents, field content, running state)
- create_supervisor (default env, explicit path)
- run_supervisor (log_event callback wire-up, KeyboardInterrupt flow)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

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

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def forge_root(tmp_path):
    """Temporary forge root directory."""
    root = tmp_path / "forge"
    root.mkdir()
    return root


@pytest.fixture
def state_file(tmp_path):
    """Temporary supervisor state file path (not yet created)."""
    return tmp_path / "sv_state.json"


@pytest.fixture
def spawn_script(tmp_path):
    """Minimal executable spawn script for testing."""
    script = tmp_path / "spawn.sh"
    script.write_text("#!/bin/bash\necho ok")
    script.chmod(0o755)
    return script


@pytest.fixture
def supervisor(forge_root, state_file, spawn_script):
    """Fully-configured AgentSupervisor for each test."""
    return AgentSupervisor(
        forge_root=forge_root,
        state_file=state_file,
        spawn_script=spawn_script,
    )


@pytest.fixture
def spec():
    """Minimal AgentSpec used across many tests."""
    return AgentSpec(
        agent_id="agent-001",
        role="backend-engineer",
        task="Fix auth bug",
        session="forge",
        window="backend",
    )


@pytest.fixture
def full_spec():
    """AgentSpec with every optional field populated."""
    return AgentSpec(
        agent_id="agent-full",
        role="frontend-builder",
        task="Build UI",
        session="my-session",
        window="ui",
        domain="codeswiftr-com",
        project="interview-simulator",
        skills=["react", "ts"],
        provider="amp",
        stream_json=True,
        restart_policy=RestartPolicy.ALWAYS,
        max_restarts=5,
        restart_delay=10.0,
        health_check_interval=60.0,
    )


def _unhealthy_service(name: str = "svc") -> ServiceHealth:
    return ServiceHealth(name=name, status=HealthStatus.UNHEALTHY, latency_ms=100.0)


# ===========================================================================
# 1. RestartPolicy
# ===========================================================================


class TestRestartPolicy:
    def test_always_string_value(self):
        assert RestartPolicy.ALWAYS == "always"
        assert RestartPolicy.ALWAYS.value == "always"

    def test_on_failure_string_value(self):
        assert RestartPolicy.ON_FAILURE == "on_failure"
        assert RestartPolicy.ON_FAILURE.value == "on_failure"

    def test_never_string_value(self):
        assert RestartPolicy.NEVER == "never"
        assert RestartPolicy.NEVER.value == "never"

    def test_construct_from_string(self):
        assert RestartPolicy("always") is RestartPolicy.ALWAYS
        assert RestartPolicy("on_failure") is RestartPolicy.ON_FAILURE
        assert RestartPolicy("never") is RestartPolicy.NEVER

    def test_distinct_values(self):
        assert RestartPolicy.ALWAYS != RestartPolicy.NEVER
        assert RestartPolicy.ON_FAILURE != RestartPolicy.ALWAYS
        assert RestartPolicy.ON_FAILURE != RestartPolicy.NEVER

    def test_is_str_subclass(self):
        assert isinstance(RestartPolicy.ALWAYS, str)


# ===========================================================================
# 2. AgentSpec
# ===========================================================================


class TestAgentSpec:
    def test_required_fields(self):
        s = AgentSpec(agent_id="x", role="r", task="t")
        assert s.agent_id == "x"
        assert s.role == "r"
        assert s.task == "t"

    def test_default_session_and_window(self):
        s = AgentSpec(agent_id="x", role="r", task="t")
        assert s.session == "forge"
        assert s.window == ""

    def test_default_optional_fields_none(self):
        s = AgentSpec(agent_id="x", role="r", task="t")
        assert s.domain is None
        assert s.project is None
        assert s.last_restart is None
        assert s.pid is None

    def test_default_skills_is_empty_list(self):
        s1 = AgentSpec(agent_id="a", role="r", task="t")
        s2 = AgentSpec(agent_id="b", role="r", task="t")
        # Each instance should have its own list (no shared mutable default)
        s1.skills.append("foo")
        assert s2.skills == []

    def test_default_numeric_thresholds(self):
        s = AgentSpec(agent_id="x", role="r", task="t")
        assert s.max_restarts == 3
        assert s.restart_delay == 5.0
        assert s.health_check_interval == 30.0

    def test_default_provider_and_flags(self):
        s = AgentSpec(agent_id="x", role="r", task="t")
        assert s.provider == "claude"
        assert s.stream_json is False
        assert s.restart_policy == RestartPolicy.ON_FAILURE

    def test_default_runtime_state(self):
        s = AgentSpec(agent_id="x", role="r", task="t")
        assert s.restart_count == 0
        assert s.status == "stopped"

    def test_custom_values_stored(self, full_spec):
        assert full_spec.domain == "codeswiftr-com"
        assert full_spec.project == "interview-simulator"
        assert full_spec.skills == ["react", "ts"]
        assert full_spec.provider == "amp"
        assert full_spec.stream_json is True
        assert full_spec.restart_policy == RestartPolicy.ALWAYS
        assert full_spec.max_restarts == 5
        assert full_spec.restart_delay == 10.0
        assert full_spec.health_check_interval == 60.0

    def test_runtime_state_mutable(self, spec):
        spec.restart_count = 3
        spec.last_restart = "2026-01-01T00:00:00Z"
        spec.status = "running"
        spec.pid = 99
        assert spec.restart_count == 3
        assert spec.last_restart == "2026-01-01T00:00:00Z"
        assert spec.status == "running"
        assert spec.pid == 99

    def test_dataclass_equality(self):
        a = AgentSpec(agent_id="x", role="r", task="t")
        b = AgentSpec(agent_id="x", role="r", task="t")
        c = AgentSpec(agent_id="y", role="r", task="t")
        assert a == b
        assert a != c


# ===========================================================================
# 3. SupervisorState
# ===========================================================================


class TestSupervisorState:
    def test_defaults(self):
        st = SupervisorState()
        assert st.agents == {}
        assert st.started_at is None
        assert st.is_running is False

    def test_independent_agents_dicts(self):
        st1 = SupervisorState()
        st2 = SupervisorState()
        st1.agents["k"] = MagicMock()
        assert "k" not in st2.agents

    def test_custom_construction(self, spec):
        st = SupervisorState(
            agents={"agent-001": spec},
            started_at="2026-01-01T00:00:00Z",
            is_running=True,
        )
        assert len(st.agents) == 1
        assert st.started_at == "2026-01-01T00:00:00Z"
        assert st.is_running is True


# ===========================================================================
# 4. AgentSupervisor.__init__
# ===========================================================================


class TestAgentSupervisorInit:
    def test_explicit_paths_stored(self, forge_root, state_file, spawn_script):
        sv = AgentSupervisor(
            forge_root=forge_root,
            state_file=state_file,
            spawn_script=spawn_script,
        )
        assert sv.forge_root == forge_root
        assert sv.state_file == state_file
        assert sv.spawn_script == spawn_script

    def test_default_state_file_path(self, forge_root):
        sv = AgentSupervisor(forge_root=forge_root)
        assert sv.state_file == forge_root / ".forge_supervisor_state.json"

    def test_default_spawn_script_path(self, forge_root):
        sv = AgentSupervisor(forge_root=forge_root)
        expected = forge_root / ".claude/skills/spawn-agent/scripts/spawn.sh"
        assert sv.spawn_script == expected

    def test_forge_root_coerced_to_path(self, tmp_path):
        sv = AgentSupervisor(forge_root=str(tmp_path))
        assert isinstance(sv.forge_root, Path)

    def test_clean_initial_runtime_state(self, supervisor):
        assert supervisor._running is False
        assert supervisor._tasks == {}
        assert supervisor._callbacks == []
        assert isinstance(supervisor.state, SupervisorState)
        assert supervisor.state.agents == {}


# ===========================================================================
# 5. _save_state / _load_state
# ===========================================================================


class TestStatePersistence:
    def test_save_creates_file(self, supervisor, spec):
        supervisor.register(spec)
        assert supervisor.state_file.exists()

    def test_save_valid_json(self, supervisor, spec):
        supervisor.register(spec)
        data = json.loads(supervisor.state_file.read_text())
        assert isinstance(data, dict)

    def test_save_top_level_keys(self, supervisor, spec):
        supervisor.state.started_at = "2026-01-01T00:00:00Z"
        supervisor.state.is_running = True
        supervisor.register(spec)
        data = json.loads(supervisor.state_file.read_text())
        assert data["started_at"] == "2026-01-01T00:00:00Z"
        assert data["is_running"] is True
        assert "agents" in data

    def test_save_all_agent_fields(self, supervisor, full_spec):
        full_spec.restart_count = 2
        full_spec.last_restart = "2026-01-01T12:00:00Z"
        full_spec.status = "running"
        full_spec.pid = 42
        supervisor.register(full_spec)
        data = json.loads(supervisor.state_file.read_text())
        ad = data["agents"]["agent-full"]
        assert ad["agent_id"] == "agent-full"
        assert ad["role"] == "frontend-builder"
        assert ad["task"] == "Build UI"
        assert ad["session"] == "my-session"
        assert ad["window"] == "ui"
        assert ad["domain"] == "codeswiftr-com"
        assert ad["project"] == "interview-simulator"
        assert ad["skills"] == ["react", "ts"]
        assert ad["provider"] == "amp"
        assert ad["stream_json"] is True
        assert ad["restart_policy"] == "always"
        assert ad["max_restarts"] == 5
        assert ad["restart_delay"] == 10.0
        assert ad["health_check_interval"] == 60.0
        assert ad["restart_count"] == 2
        assert ad["last_restart"] == "2026-01-01T12:00:00Z"
        assert ad["status"] == "running"
        assert ad["pid"] == 42

    def test_load_no_file_is_safe(self, supervisor):
        assert not supervisor.state_file.exists()
        supervisor._load_state()  # must not raise
        assert supervisor.state.agents == {}
        assert supervisor.state.started_at is None
        assert supervisor.state.is_running is False

    def test_load_restores_top_level_state(self, supervisor, spec):
        supervisor.state.started_at = "2026-02-01T00:00:00Z"
        supervisor.state.is_running = True
        supervisor.register(spec)

        sv2 = AgentSupervisor(
            forge_root=supervisor.forge_root,
            state_file=supervisor.state_file,
            spawn_script=supervisor.spawn_script,
        )
        sv2._load_state()
        assert sv2.state.started_at == "2026-02-01T00:00:00Z"
        assert sv2.state.is_running is True

    def test_load_restores_agent(self, supervisor, full_spec):
        full_spec.restart_count = 3
        full_spec.last_restart = "2026-01-01T10:00:00Z"
        full_spec.status = "crashed"
        full_spec.pid = 1234
        supervisor.register(full_spec)

        sv2 = AgentSupervisor(
            forge_root=supervisor.forge_root,
            state_file=supervisor.state_file,
            spawn_script=supervisor.spawn_script,
        )
        sv2._load_state()

        s = sv2.state.agents["agent-full"]
        assert s.agent_id == "agent-full"
        assert s.role == "frontend-builder"
        assert s.domain == "codeswiftr-com"
        assert s.project == "interview-simulator"
        assert s.skills == ["react", "ts"]
        assert s.provider == "amp"
        assert s.stream_json is True
        assert s.restart_policy == RestartPolicy.ALWAYS
        assert s.max_restarts == 5
        assert s.restart_delay == 10.0
        assert s.health_check_interval == 60.0
        assert s.restart_count == 3
        assert s.last_restart == "2026-01-01T10:00:00Z"
        assert s.status == "crashed"
        assert s.pid == 1234

    def test_load_default_values_for_missing_fields(self, supervisor, state_file):
        # Write minimal JSON without optional fields
        minimal = {
            "started_at": None,
            "is_running": False,
            "agents": {
                "minimal-agent": {
                    "agent_id": "minimal-agent",
                    "role": "r",
                    "task": "t",
                }
            },
        }
        state_file.write_text(json.dumps(minimal))
        supervisor._load_state()
        s = supervisor.state.agents["minimal-agent"]
        assert s.session == "forge"
        assert s.window == ""
        assert s.domain is None
        assert s.project is None
        assert s.skills == []
        assert s.provider == "claude"
        assert s.stream_json is False
        assert s.restart_policy == RestartPolicy.ON_FAILURE
        assert s.max_restarts == 3
        assert s.restart_delay == 5.0
        assert s.health_check_interval == 30.0
        assert s.restart_count == 0
        assert s.last_restart is None
        assert s.status == "stopped"
        assert s.pid is None


# ===========================================================================
# 6. register / unregister
# ===========================================================================


class TestRegisterUnregister:
    def test_register_returns_spec(self, supervisor, spec):
        result = supervisor.register(spec)
        assert result is spec

    def test_register_adds_to_state(self, supervisor, spec):
        supervisor.register(spec)
        assert "agent-001" in supervisor.state.agents

    def test_register_multiple(self, supervisor, spec, full_spec):
        supervisor.register(spec)
        supervisor.register(full_spec)
        assert len(supervisor.state.agents) == 2

    def test_register_overwrites_same_id(self, supervisor, spec):
        supervisor.register(spec)
        spec.task = "Updated task"
        supervisor.register(spec)
        assert len(supervisor.state.agents) == 1
        assert supervisor.state.agents["agent-001"].task == "Updated task"

    def test_register_saves_to_disk(self, supervisor, spec):
        supervisor.register(spec)
        data = json.loads(supervisor.state_file.read_text())
        assert "agent-001" in data["agents"]

    def test_unregister_existing_returns_true(self, supervisor, spec):
        supervisor.register(spec)
        assert supervisor.unregister("agent-001") is True

    def test_unregister_removes_from_state(self, supervisor, spec):
        supervisor.register(spec)
        supervisor.unregister("agent-001")
        assert "agent-001" not in supervisor.state.agents

    def test_unregister_nonexistent_returns_false(self, supervisor):
        assert supervisor.unregister("ghost-agent") is False

    def test_unregister_saves_to_disk(self, supervisor, spec):
        supervisor.register(spec)
        supervisor.unregister("agent-001")
        data = json.loads(supervisor.state_file.read_text())
        assert "agent-001" not in data["agents"]

    def test_unregister_cancels_monitoring_task(self, supervisor, spec):
        supervisor.register(spec)
        mock_task = Mock()
        supervisor._tasks["agent-001"] = mock_task
        supervisor.unregister("agent-001")
        mock_task.cancel.assert_called_once()
        assert "agent-001" not in supervisor._tasks

    def test_unregister_no_task_does_not_raise(self, supervisor, spec):
        supervisor.register(spec)
        # No task in _tasks — should not raise
        supervisor.unregister("agent-001")


# ===========================================================================
# 7. _check_agent_alive
# ===========================================================================


class TestCheckAgentAlive:
    def _mock_alive(self, commands=("claude",)):
        """Return subprocess.run side_effect that makes the agent look alive."""
        return [
            Mock(returncode=0),  # has-session
            Mock(returncode=0, stdout="\n".join(commands) + "\n"),  # list-panes
        ]

    def test_alive_with_claude(self, supervisor, spec):
        with patch("subprocess.run", side_effect=self._mock_alive(["claude"])):
            assert supervisor._check_agent_alive(spec) is True

    def test_alive_with_gtimeout(self, supervisor, spec):
        with patch("subprocess.run", side_effect=self._mock_alive(["gtimeout"])):
            assert supervisor._check_agent_alive(spec) is True

    def test_alive_with_timeout(self, supervisor, spec):
        with patch("subprocess.run", side_effect=self._mock_alive(["timeout"])):
            assert supervisor._check_agent_alive(spec) is True

    def test_not_alive_when_session_missing(self, supervisor, spec):
        with patch("subprocess.run", return_value=Mock(returncode=1)):
            assert supervisor._check_agent_alive(spec) is False

    def test_not_alive_when_list_panes_fails(self, supervisor, spec):
        with patch("subprocess.run", side_effect=[
            Mock(returncode=0),  # has-session OK
            Mock(returncode=1),  # list-panes fails
        ]):
            assert supervisor._check_agent_alive(spec) is False

    def test_not_alive_when_no_matching_command(self, supervisor, spec):
        with patch("subprocess.run", side_effect=[
            Mock(returncode=0),
            Mock(returncode=0, stdout="bash\nzsh\n"),
        ]):
            assert supervisor._check_agent_alive(spec) is False

    def test_not_alive_on_exception(self, supervisor, spec):
        with patch("subprocess.run", side_effect=Exception("unexpected")):
            assert supervisor._check_agent_alive(spec) is False

    def test_target_with_window(self, supervisor, spec):
        """When spec.window is set, list-panes target is session:window."""
        spec.window = "backend"
        with patch("subprocess.run", side_effect=[
            Mock(returncode=0),
            Mock(returncode=0, stdout="claude\n"),
        ]) as mock_run:
            supervisor._check_agent_alive(spec)
        # Second call should target "forge:backend"
        pane_call = mock_run.call_args_list[1]
        assert "forge:backend" in pane_call[0][0]

    def test_target_without_window_uses_session_only(self, supervisor, spec):
        """When spec.window is empty, list-panes target is just the session."""
        spec.window = ""
        with patch("subprocess.run", side_effect=[
            Mock(returncode=0),
            Mock(returncode=0, stdout="claude\n"),
        ]) as mock_run:
            supervisor._check_agent_alive(spec)
        pane_call = mock_run.call_args_list[1]
        target = pane_call[0][0][pane_call[0][0].index("-t") + 1]
        assert target == "forge"


# ===========================================================================
# 8. _spawn_agent
# ===========================================================================


class TestSpawnAgent:
    def _make_process(self, returncode=0, stdout=b"", stderr=b""):
        p = AsyncMock()
        p.returncode = returncode
        p.communicate = AsyncMock(return_value=(stdout, stderr))
        return p

    @pytest.mark.asyncio
    async def test_success_returns_true(self, supervisor, spec):
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process(0)):
            assert await supervisor._spawn_agent(spec) is True

    @pytest.mark.asyncio
    async def test_success_sets_status_running(self, supervisor, spec):
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process(0)):
            await supervisor._spawn_agent(spec)
        assert spec.status == "running"

    @pytest.mark.asyncio
    async def test_success_sets_last_restart(self, supervisor, spec):
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process(0)):
            await supervisor._spawn_agent(spec)
        assert spec.last_restart is not None

    @pytest.mark.asyncio
    async def test_failure_returns_false(self, supervisor, spec):
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process(1, stderr=b"err")):
            assert await supervisor._spawn_agent(spec) is False

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self, supervisor, spec):
        proc = AsyncMock()
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            assert await supervisor._spawn_agent(spec) is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, supervisor, spec):
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("no such file")):
            assert await supervisor._spawn_agent(spec) is False

    @pytest.mark.asyncio
    async def test_includes_role_and_task(self, supervisor, spec):
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process()) as ex:
            await supervisor._spawn_agent(spec)
        args = ex.call_args[0]
        assert spec.role in args
        assert spec.task in args

    @pytest.mark.asyncio
    async def test_includes_session(self, supervisor, spec):
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process()) as ex:
            await supervisor._spawn_agent(spec)
        args = ex.call_args[0]
        assert "--session" in args
        assert spec.session in args

    @pytest.mark.asyncio
    async def test_includes_window_when_set(self, supervisor, full_spec):
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process()) as ex:
            await supervisor._spawn_agent(full_spec)
        args = ex.call_args[0]
        assert "--window" in args
        assert full_spec.window in args

    @pytest.mark.asyncio
    async def test_omits_window_when_empty(self, supervisor, spec):
        spec.window = ""
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process()) as ex:
            await supervisor._spawn_agent(spec)
        args = ex.call_args[0]
        assert "--window" not in args

    @pytest.mark.asyncio
    async def test_includes_domain_when_set(self, supervisor, full_spec):
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process()) as ex:
            await supervisor._spawn_agent(full_spec)
        args = ex.call_args[0]
        assert "--domain" in args
        assert full_spec.domain in args

    @pytest.mark.asyncio
    async def test_includes_project_when_set(self, supervisor, full_spec):
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process()) as ex:
            await supervisor._spawn_agent(full_spec)
        args = ex.call_args[0]
        assert "--project" in args
        assert full_spec.project in args

    @pytest.mark.asyncio
    async def test_includes_skills_comma_joined(self, supervisor, full_spec):
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process()) as ex:
            await supervisor._spawn_agent(full_spec)
        args = ex.call_args[0]
        assert "--skills" in args
        assert "react,ts" in args

    @pytest.mark.asyncio
    async def test_includes_provider_when_not_claude(self, supervisor, full_spec):
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process()) as ex:
            await supervisor._spawn_agent(full_spec)
        args = ex.call_args[0]
        assert "--provider" in args
        assert full_spec.provider in args

    @pytest.mark.asyncio
    async def test_omits_provider_when_claude(self, supervisor, spec):
        spec.provider = "claude"
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process()) as ex:
            await supervisor._spawn_agent(spec)
        args = ex.call_args[0]
        assert "--provider" not in args

    @pytest.mark.asyncio
    async def test_includes_stream_json_flag(self, supervisor, full_spec):
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process()) as ex:
            await supervisor._spawn_agent(full_spec)
        args = ex.call_args[0]
        assert "--stream-json" in args

    @pytest.mark.asyncio
    async def test_omits_stream_json_when_false(self, supervisor, spec):
        spec.stream_json = False
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process()) as ex:
            await supervisor._spawn_agent(spec)
        args = ex.call_args[0]
        assert "--stream-json" not in args

    @pytest.mark.asyncio
    async def test_always_includes_no_server(self, supervisor, spec):
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process()) as ex:
            await supervisor._spawn_agent(spec)
        args = ex.call_args[0]
        assert "--no-server" in args

    @pytest.mark.asyncio
    async def test_saves_state_on_success(self, supervisor, spec):
        with patch("asyncio.create_subprocess_exec", return_value=self._make_process()):
            with patch.object(supervisor, "_save_state") as mock_save:
                await supervisor._spawn_agent(spec)
        mock_save.assert_called()


# ===========================================================================
# 9. _monitor_agent
# ===========================================================================


class TestMonitorAgent:
    @pytest.mark.asyncio
    async def test_alive_agent_keeps_status_running(self, supervisor, spec):
        spec.health_check_interval = 0.05
        supervisor.register(spec)
        supervisor._running = True

        with patch.object(supervisor, "_check_agent_alive", return_value=True):
            task = asyncio.create_task(supervisor._monitor_agent("agent-001"))
            await asyncio.sleep(0.2)
            supervisor._running = False
            await task

        assert spec.status == "running"

    @pytest.mark.asyncio
    async def test_never_policy_blocks_restart(self, supervisor, spec):
        spec.restart_policy = RestartPolicy.NEVER
        spec.health_check_interval = 0.05
        supervisor.register(spec)
        supervisor._running = True

        with (
            patch.object(supervisor, "_check_agent_alive", return_value=False),
            patch.object(supervisor, "_spawn_agent") as mock_spawn,
        ):
            task = asyncio.create_task(supervisor._monitor_agent("agent-001"))
            await asyncio.sleep(0.2)
            supervisor._running = False
            await task

        mock_spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_max_restarts_exhausted_sets_failed_status(self, supervisor, spec):
        spec.max_restarts = 2
        spec.restart_count = 2
        spec.health_check_interval = 0.05
        supervisor.register(spec)
        supervisor._running = True

        with (
            patch.object(supervisor, "_check_agent_alive", return_value=False),
            patch.object(supervisor, "_spawn_agent") as mock_spawn,
        ):
            task = asyncio.create_task(supervisor._monitor_agent("agent-001"))
            await asyncio.sleep(0.2)
            supervisor._running = False
            await task

        mock_spawn.assert_not_called()
        assert spec.status == "failed"

    @pytest.mark.asyncio
    async def test_max_restarts_fires_agent_failed_callback(self, supervisor, spec):
        callback = Mock()
        supervisor.on_event(callback)

        spec.max_restarts = 1
        spec.restart_count = 1
        spec.health_check_interval = 0.05
        supervisor.register(spec)
        supervisor._running = True

        with patch.object(supervisor, "_check_agent_alive", return_value=False):
            task = asyncio.create_task(supervisor._monitor_agent("agent-001"))
            await asyncio.sleep(0.3)
            supervisor._running = False
            await task

        callback.assert_called()
        event_type = callback.call_args[0][0]
        assert event_type == "agent_failed"

    @pytest.mark.asyncio
    async def test_successful_restart_fires_agent_restarted_callback(self, supervisor, spec):
        callback = Mock()
        supervisor.on_event(callback)

        spec.health_check_interval = 0.05
        spec.restart_delay = 0.05
        supervisor.register(spec)
        supervisor._running = True

        with (
            patch.object(supervisor, "_check_agent_alive", return_value=False),
            patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True),
        ):
            task = asyncio.create_task(supervisor._monitor_agent("agent-001"))
            await asyncio.sleep(0.4)
            supervisor._running = False
            await task

        # At least one "agent_restarted" event should have been fired
        # (agent_failed may also fire once max_restarts is exhausted)
        callback.assert_called()
        event_types = [c[0][0] for c in callback.call_args_list]
        assert "agent_restarted" in event_types

    @pytest.mark.asyncio
    async def test_restart_count_increments_on_each_attempt(self, supervisor, spec):
        spec.health_check_interval = 0.05
        spec.restart_delay = 0.05
        spec.max_restarts = 10
        supervisor.register(spec)
        supervisor._running = True
        initial = spec.restart_count

        with (
            patch.object(supervisor, "_check_agent_alive", return_value=False),
            patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True),
        ):
            task = asyncio.create_task(supervisor._monitor_agent("agent-001"))
            await asyncio.sleep(0.4)
            supervisor._running = False
            await task

        assert spec.restart_count > initial

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_crash_monitor(self, supervisor, spec):
        def bad_cb(event_type, s):
            raise ValueError("boom")

        supervisor.on_event(bad_cb)
        spec.max_restarts = 1
        spec.restart_count = 1
        spec.health_check_interval = 0.05
        supervisor.register(spec)
        supervisor._running = True

        with patch.object(supervisor, "_check_agent_alive", return_value=False):
            task = asyncio.create_task(supervisor._monitor_agent("agent-001"))
            await asyncio.sleep(0.3)
            supervisor._running = False
            await task  # Must not propagate ValueError

    @pytest.mark.asyncio
    async def test_monitor_exits_when_agent_unregistered(self, supervisor, spec):
        spec.health_check_interval = 0.05
        supervisor.register(spec)
        supervisor._running = True

        with patch.object(supervisor, "_check_agent_alive", return_value=True):
            task = asyncio.create_task(supervisor._monitor_agent("agent-001"))
            await asyncio.sleep(0.1)
            # Remove agent while monitoring — loop should exit cleanly
            del supervisor.state.agents["agent-001"]
            supervisor._running = False
            await task

    @pytest.mark.asyncio
    async def test_restart_callback_exception_on_restarted_event(self, supervisor, spec):
        """Cover the callback exception path in the 'agent_restarted' branch."""
        def bad_cb(event_type, s):
            raise RuntimeError("cb fail")

        supervisor.on_event(bad_cb)
        spec.health_check_interval = 0.05
        spec.restart_delay = 0.05
        spec.max_restarts = 1
        supervisor.register(spec)
        supervisor._running = True

        with (
            patch.object(supervisor, "_check_agent_alive", return_value=False),
            patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True),
        ):
            task = asyncio.create_task(supervisor._monitor_agent("agent-001"))
            await asyncio.sleep(0.4)
            supervisor._running = False
            await task  # Must not raise


# ===========================================================================
# 10. on_event
# ===========================================================================


class TestOnEvent:
    def test_registers_single_callback(self, supervisor):
        cb = Mock()
        supervisor.on_event(cb)
        assert cb in supervisor._callbacks

    def test_registers_multiple_callbacks(self, supervisor):
        cb1, cb2, cb3 = Mock(), Mock(), Mock()
        supervisor.on_event(cb1)
        supervisor.on_event(cb2)
        supervisor.on_event(cb3)
        assert supervisor._callbacks == [cb1, cb2, cb3]


# ===========================================================================
# 11. restart_agent
# ===========================================================================


class TestRestartAgent:
    @pytest.mark.asyncio
    async def test_unknown_agent_returns_false(self, supervisor):
        assert await supervisor.restart_agent("ghost") is False

    @pytest.mark.asyncio
    async def test_never_policy_returns_false(self, supervisor, spec):
        spec.restart_policy = RestartPolicy.NEVER
        supervisor.register(spec)
        assert await supervisor.restart_agent("agent-001") is False

    @pytest.mark.asyncio
    async def test_max_restarts_reached_returns_false(self, supervisor, spec):
        spec.max_restarts = 3
        spec.restart_count = 3
        supervisor.register(spec)
        assert await supervisor.restart_agent("agent-001") is False

    @pytest.mark.asyncio
    async def test_success_returns_true(self, supervisor, spec):
        supervisor.register(spec)
        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True):
            assert await supervisor.restart_agent("agent-001") is True

    @pytest.mark.asyncio
    async def test_failure_returns_false(self, supervisor, spec):
        supervisor.register(spec)
        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=False):
            assert await supervisor.restart_agent("agent-001") is False

    @pytest.mark.asyncio
    async def test_increments_restart_count(self, supervisor, spec):
        supervisor.register(spec)
        before = spec.restart_count
        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True):
            await supervisor.restart_agent("agent-001")
        assert supervisor.state.agents["agent-001"].restart_count == before + 1

    @pytest.mark.asyncio
    async def test_fires_agent_restarted_callback_on_success(self, supervisor, spec):
        cb = Mock()
        supervisor.on_event(cb)
        supervisor.register(spec)
        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True):
            await supervisor.restart_agent("agent-001")
        cb.assert_called_once()
        assert cb.call_args[0][0] == "agent_restarted"

    @pytest.mark.asyncio
    async def test_no_callback_on_failure(self, supervisor, spec):
        cb = Mock()
        supervisor.on_event(cb)
        supervisor.register(spec)
        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=False):
            await supervisor.restart_agent("agent-001")
        cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_propagate(self, supervisor, spec):
        def bad_cb(event_type, s):
            raise RuntimeError("boom")

        supervisor.on_event(bad_cb)
        supervisor.register(spec)
        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True):
            result = await supervisor.restart_agent("agent-001")
        assert result is True  # Callback exception must not change return value

    @pytest.mark.asyncio
    async def test_custom_reason_accepted(self, supervisor, spec):
        supervisor.register(spec)
        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True):
            assert await supervisor.restart_agent("agent-001", reason="health_failure") is True

    @pytest.mark.asyncio
    async def test_always_policy_allowed(self, supervisor, spec):
        spec.restart_policy = RestartPolicy.ALWAYS
        supervisor.register(spec)
        with patch.object(supervisor, "_spawn_agent", new_callable=AsyncMock, return_value=True):
            assert await supervisor.restart_agent("agent-001") is True


# ===========================================================================
# 12. _on_health_failure
# ===========================================================================


class TestOnHealthFailure:
    def test_unknown_service_is_noop(self, supervisor):
        health = _unhealthy_service("unknown-svc")
        with patch("asyncio.create_task") as mock_task:
            supervisor._on_health_failure("unknown-svc", 3, health)
        mock_task.assert_not_called()

    def test_known_agent_schedules_restart_task(self, supervisor, spec):
        supervisor.register(spec)
        health = _unhealthy_service("agent-001")
        with patch("asyncio.create_task") as mock_task:
            supervisor._on_health_failure("agent-001", 3, health)
        mock_task.assert_called_once()

    def test_only_matching_agent_gets_task(self, supervisor, spec, full_spec):
        supervisor.register(spec)
        supervisor.register(full_spec)
        health = _unhealthy_service("agent-001")
        with patch("asyncio.create_task") as mock_task:
            supervisor._on_health_failure("agent-001", 5, health)
        # Exactly one create_task call, not two
        assert mock_task.call_count == 1

    def test_failure_count_passed_via_log_not_raised(self, supervisor, spec):
        """High failure count: should log warning but not raise."""
        supervisor.register(spec)
        health = _unhealthy_service("agent-001")
        with patch("asyncio.create_task"):
            supervisor._on_health_failure("agent-001", 999, health)  # Must not raise


# ===========================================================================
# 13. register_with_health_checks
# ===========================================================================


class TestRegisterWithHealthChecks:
    def test_calls_get_health_registry(self, supervisor):
        mock_registry = MagicMock()
        with patch("forge_harness.agent_supervisor.get_health_registry", return_value=mock_registry) as mock_get:
            supervisor.register_with_health_checks()
        mock_get.assert_called_once()

    def test_registers_on_health_failure_callback(self, supervisor):
        mock_registry = MagicMock()
        with patch("forge_harness.agent_supervisor.get_health_registry", return_value=mock_registry):
            supervisor.register_with_health_checks()
        mock_registry.on_health_failure.assert_called_once_with(supervisor._on_health_failure)


# ===========================================================================
# 14. Supervisor lifecycle (start / stop)
# ===========================================================================


class TestSupervisorLifecycle:
    @pytest.mark.asyncio
    async def test_start_sets_running_flag(self, supervisor):
        task = asyncio.create_task(supervisor.start())
        await asyncio.sleep(0.1)
        assert supervisor._running is True
        assert supervisor.state.is_running is True
        assert supervisor.state.started_at is not None
        await supervisor.stop()
        await task

    @pytest.mark.asyncio
    async def test_start_creates_monitoring_tasks_for_registered_agents(self, supervisor, spec):
        supervisor.register(spec)
        task = asyncio.create_task(supervisor.start())
        await asyncio.sleep(0.1)
        assert "agent-001" in supervisor._tasks
        await supervisor.stop()
        await task

    @pytest.mark.asyncio
    async def test_stop_clears_running_flags(self, supervisor):
        task = asyncio.create_task(supervisor.start())
        await asyncio.sleep(0.1)
        await supervisor.stop()
        assert supervisor._running is False
        assert supervisor.state.is_running is False
        await task

    @pytest.mark.asyncio
    async def test_stop_clears_tasks_dict(self, supervisor, spec):
        supervisor.register(spec)
        task = asyncio.create_task(supervisor.start())
        await asyncio.sleep(0.1)
        await supervisor.stop()
        assert supervisor._tasks == {}
        await task

    @pytest.mark.asyncio
    async def test_stop_saves_state_with_not_running(self, supervisor):
        task = asyncio.create_task(supervisor.start())
        await asyncio.sleep(0.1)
        await supervisor.stop()
        data = json.loads(supervisor.state_file.read_text())
        assert data["is_running"] is False
        await task

    @pytest.mark.asyncio
    async def test_start_loads_existing_state(self, supervisor, spec):
        # Pre-persist an agent via another supervisor instance
        supervisor.register(spec)

        sv2 = AgentSupervisor(
            forge_root=supervisor.forge_root,
            state_file=supervisor.state_file,
            spawn_script=supervisor.spawn_script,
        )
        task = asyncio.create_task(sv2.start())
        await asyncio.sleep(0.1)
        assert "agent-001" in sv2.state.agents
        await sv2.stop()
        await task


# ===========================================================================
# 15. get_status
# ===========================================================================


class TestGetStatus:
    def test_empty_status_keys(self, supervisor):
        s = supervisor.get_status()
        assert "is_running" in s
        assert "started_at" in s
        assert "agents" in s

    def test_not_running_by_default(self, supervisor):
        assert supervisor.get_status()["is_running"] is False

    def test_empty_agents_dict(self, supervisor):
        assert supervisor.get_status()["agents"] == {}

    def test_status_includes_registered_agents(self, supervisor, spec, full_spec):
        supervisor.register(spec)
        supervisor.register(full_spec)
        s = supervisor.get_status()
        assert "agent-001" in s["agents"]
        assert "agent-full" in s["agents"]

    def test_agent_status_fields(self, supervisor, spec):
        spec.status = "running"
        spec.restart_count = 2
        spec.last_restart = "2026-01-01T00:00:00Z"
        supervisor.register(spec)
        agent_s = supervisor.get_status()["agents"]["agent-001"]
        assert agent_s["status"] == "running"
        assert agent_s["restart_count"] == 2
        assert agent_s["last_restart"] == "2026-01-01T00:00:00Z"
        assert agent_s["role"] == "backend-engineer"
        assert agent_s["provider"] == "claude"
        assert agent_s["session"] == "forge"
        assert agent_s["window"] == "backend"

    @pytest.mark.asyncio
    async def test_status_is_running_reflects_start(self, supervisor):
        task = asyncio.create_task(supervisor.start())
        await asyncio.sleep(0.1)
        assert supervisor.get_status()["is_running"] is True
        await supervisor.stop()
        await task


# ===========================================================================
# 16. create_supervisor
# ===========================================================================


class TestCreateSupervisor:
    def test_explicit_forge_root(self, tmp_path):
        sv = create_supervisor(forge_root=tmp_path)
        assert sv.forge_root == tmp_path
        assert isinstance(sv, AgentSupervisor)

    def test_reads_forge_root_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
        sv = create_supervisor()
        assert sv.forge_root == tmp_path

    def test_falls_back_to_dot_when_no_env(self, monkeypatch):
        monkeypatch.delenv("FORGE_ROOT", raising=False)
        sv = create_supervisor()
        assert sv.forge_root == Path(".")


# ===========================================================================
# 17. run_supervisor
# ===========================================================================


class TestRunSupervisor:
    @pytest.mark.asyncio
    async def test_run_supervisor_calls_start(self, tmp_path):
        with patch.object(AgentSupervisor, "start", side_effect=KeyboardInterrupt):
            with patch.object(AgentSupervisor, "stop", new_callable=AsyncMock):
                try:
                    await run_supervisor(forge_root=tmp_path)
                except KeyboardInterrupt:
                    pass

    @pytest.mark.asyncio
    async def test_run_supervisor_registers_log_event_callback(self, tmp_path):
        captured_callbacks: list = []

        original_on_event = AgentSupervisor.on_event

        def capture(self, cb):
            captured_callbacks.append(cb)
            original_on_event(self, cb)

        with (
            patch.object(AgentSupervisor, "on_event", capture),
            patch.object(AgentSupervisor, "start", side_effect=KeyboardInterrupt),
            patch.object(AgentSupervisor, "stop", new_callable=AsyncMock),
        ):
            try:
                await run_supervisor(forge_root=tmp_path)
            except KeyboardInterrupt:
                pass

        assert len(captured_callbacks) == 1
        # The callback should be callable with (event_type, spec)
        dummy_spec = AgentSpec(agent_id="cb-test", role="r", task="t")
        captured_callbacks[0]("agent_restarted", dummy_spec)  # Must not raise

    @pytest.mark.asyncio
    async def test_run_supervisor_handles_keyboard_interrupt(self, tmp_path):
        """run_supervisor catches KeyboardInterrupt and calls stop."""
        stop_called = []

        async def fake_stop():
            stop_called.append(True)

        with (
            patch.object(AgentSupervisor, "start", side_effect=KeyboardInterrupt),
            patch.object(AgentSupervisor, "stop", side_effect=fake_stop),
        ):
            await run_supervisor(forge_root=tmp_path)

        assert stop_called
