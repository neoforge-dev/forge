"""Tests for agent restart and recovery functionality."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from forge_harness.fleet.agent_restart import (
    AgentRestarter,
    RecoveryResult,
    TaskState,
    auto_recover_all,
)


@pytest.fixture
def restarter():
    """Create AgentRestarter instance for testing."""
    return AgentRestarter(session="test-forge")


@pytest.fixture
def mock_tmux_command():
    """Mock tmux command execution."""
    with patch("forge_harness.fleet.agent_restart.AgentRestarter._run_tmux_command") as mock:
        yield mock


class TestStaleDetection:
    """Test stale agent detection."""

    def test_detect_stale_no_activity(self, restarter, mock_tmux_command):
        """Agent with no output change for 30+ min is stale."""
        # Mock pane activity time from 45 minutes ago
        old_time = datetime.now(UTC) - timedelta(minutes=45)
        mock_tmux_command.return_value = str(int(old_time.timestamp()))

        assert restarter.is_stale("tech", threshold_minutes=30)

    def test_detect_stale_recent_activity(self, restarter, mock_tmux_command):
        """Agent with recent activity is not stale."""
        # Mock pane activity time from 5 minutes ago
        recent_time = datetime.now(UTC) - timedelta(minutes=5)
        mock_tmux_command.return_value = str(int(recent_time.timestamp()))

        assert not restarter.is_stale("tech", threshold_minutes=30)

    def test_detect_stale_custom_threshold(self, restarter, mock_tmux_command):
        """Custom threshold is respected."""
        # Mock pane activity time from 8 minutes ago
        time_ago = datetime.now(UTC) - timedelta(minutes=8)
        mock_tmux_command.return_value = str(int(time_ago.timestamp()))

        # Should not be stale with 10 minute threshold
        assert not restarter.is_stale("tech", threshold_minutes=10)

        # Should be stale with 5 minute threshold
        assert restarter.is_stale("tech", threshold_minutes=5)

    def test_detect_stale_no_activity_time(self, restarter, mock_tmux_command):
        """Gracefully handle missing activity time."""
        mock_tmux_command.return_value = ""

        assert not restarter.is_stale("tech")

    def test_detect_stale_boundary_condition(self, restarter, mock_tmux_command):
        """Test exactly at threshold boundary."""
        # Mock pane activity time exactly at threshold
        exact_time = datetime.now(UTC) - timedelta(minutes=30)
        mock_tmux_command.return_value = str(int(exact_time.timestamp()))

        assert restarter.is_stale("tech", threshold_minutes=30)


class TestCrashDetection:
    """Test crashed agent detection."""

    def test_detect_crash_shell_prompt(self, restarter):
        """Shell prompt without Claude indicates crash."""
        with (
            patch.object(restarter, "_get_pane_command", return_value="bash"),
            patch.object(
                restarter,
                "_capture_pane_content",
                return_value="user@host ~/work $ ",
            ),
        ):
            assert restarter.is_crashed("tech")

    def test_detect_crash_claude_running(self, restarter):
        """Active Claude process is not crashed."""
        with (
            patch.object(restarter, "_get_pane_command", return_value="claude"),
            patch.object(
                restarter,
                "_capture_pane_content",
                return_value="Reading file...",
            ),
        ):
            assert not restarter.is_crashed("tech")

    def test_detect_crash_insert_mode(self, restarter):
        """INSERT mode indicates active, not crashed."""
        with (
            patch.object(restarter, "_get_pane_command", return_value="bash"),
            patch.object(
                restarter,
                "_capture_pane_content",
                return_value="[INSERT] Editing file\nuser@host ~ $ ",
            ),
        ):
            assert not restarter.is_crashed("tech")

    def test_detect_crash_various_shell_prompts(self, restarter):
        """Detect crash with various shell prompt styles."""
        prompts = [
            "user@host ~/work $ ",
            "~/work ❯ ",
            "~/work > ",
            "~/work % ",
            "root@host # ",
        ]

        for prompt in prompts:
            with (
                patch.object(restarter, "_get_pane_command", return_value="zsh"),
                patch.object(restarter, "_capture_pane_content", return_value=prompt),
            ):
                assert restarter.is_crashed("tech"), f"Failed to detect crash for: {prompt}"

    def test_detect_crash_node_process(self, restarter):
        """Node process (Claude running) is not crashed."""
        with patch.object(restarter, "_get_pane_command", return_value="node"):
            assert not restarter.is_crashed("tech")

    def test_detect_crash_empty_content(self, restarter):
        """Gracefully handle empty pane content."""
        with (
            patch.object(restarter, "_get_pane_command", return_value="bash"),
            patch.object(restarter, "_capture_pane_content", return_value=""),
        ):
            assert not restarter.is_crashed("tech")


class TestTaskRecovery:
    """Test task state recovery."""

    def test_recover_task_prompt(self, restarter):
        """Last user prompt is extracted."""
        content = """
        user@host ~/work/FORGE ❯ Fix the authentication bug in src/auth.ts
        Reading src/auth.ts...
        Thinking about the issue...
        """

        with patch.object(restarter, "_capture_pane_content", return_value=content):
            state = restarter.recover_task_state("tech")

            assert state is not None
            assert "authentication bug" in state.prompt
            assert "auth.ts" in state.prompt

    def test_recover_working_directory(self, restarter):
        """Working directory is captured."""
        content = """
        user@host ~/work/FORGE/harness ❯ Run tests
        Running tests...
        """

        with patch.object(restarter, "_capture_pane_content", return_value=content):
            state = restarter.recover_task_state("tech")

            assert state is not None
            assert "harness" in state.working_directory

    def test_recover_files_modified(self, restarter):
        """Modified files are tracked."""
        content = """
        Edit harness/forge_harness/agent.py
        Write tests/test_agent.py
        Read harness/forge_harness/config.py
        """

        with patch.object(restarter, "_capture_pane_content", return_value=content):
            state = restarter.recover_task_state("tech")

            assert state is not None
            assert "agent.py" in str(state.files_modified)
            assert "test_agent.py" in str(state.files_modified)
            assert "config.py" in str(state.files_modified)
            assert len(state.files_modified) >= 3

    def test_recover_no_state(self, restarter):
        """Gracefully handle no recoverable state."""
        with patch.object(restarter, "_capture_pane_content", return_value=""):
            state = restarter.recover_task_state("tech")
            assert state is None

    def test_recover_context_percentage(self, restarter):
        """Context percentage is extracted."""
        content = """
        user@host ~/work ❯ Fix the bug
        Working on task...
        Context: 45%
        """

        with patch.object(restarter, "_capture_pane_content", return_value=content):
            state = restarter.recover_task_state("tech")

            assert state is not None
            assert state.context_percentage == 45

    def test_recover_last_tool_call(self, restarter):
        """Last tool call is captured."""
        content = """
        Reading file...
        Editing content...
        Writing changes...
        """

        with patch.object(restarter, "_capture_pane_content", return_value=content):
            state = restarter.recover_task_state("tech")

            assert state is not None
            assert state.last_tool_call is not None

    def test_recover_skip_command_prompts(self, restarter):
        """Skip shell commands when recovering prompts."""
        content = """
        user@host ~/work ❯ cd harness
        user@host ~/work/harness ❯ ls -la
        user@host ~/work/harness ❯ Fix the bug in agent.py
        """

        with patch.object(restarter, "_capture_pane_content", return_value=content):
            state = restarter.recover_task_state("tech")

            assert state is not None
            assert "Fix the bug" in state.prompt
            assert "cd harness" not in state.prompt
            assert "ls -la" not in state.prompt


class TestAgentRestart:
    """Test agent restart functionality."""

    def test_restart_agent_basic(self, restarter):
        """Agent is killed and respawned."""
        with patch.object(restarter, "_run_tmux_command") as mock_cmd:
            success = restarter.restart_agent("tech")

            assert success

            # Verify commands sent
            calls = [str(call) for call in mock_cmd.call_args_list]

            # Should have sent Ctrl+C
            assert any("C-c" in str(call) for call in calls)

            # Should have launched Claude
            assert any("claude" in str(call) for call in calls)

    def test_restart_agent_with_task(self, restarter):
        """Agent is respawned with recovered task."""
        task_state = TaskState(
            prompt="Fix authentication bug",
            working_directory="/work/FORGE",
            files_modified=["auth.py"],
            last_tool_call="Editing",
            context_percentage=30,
            extracted_at=datetime.now(UTC),
        )

        with (
            patch.object(restarter, "_run_tmux_command") as mock_cmd,
            patch("forge_harness.fleet.agent_restart._last_restart_times", {}),
        ):
            success = restarter.restart_agent("tech", task_state)

            assert success

            # Verify prompt was sent
            calls = [str(call) for call in mock_cmd.call_args_list]
            assert any("authentication bug" in str(call).lower() for call in calls)

    def test_restart_agent_directory(self, restarter):
        """Agent starts in correct directory."""
        task_state = TaskState(
            prompt="Run tests",
            working_directory="/work/FORGE/harness",
            files_modified=[],
            last_tool_call=None,
            context_percentage=10,
            extracted_at=datetime.now(UTC),
        )

        with (
            patch.object(restarter, "_run_tmux_command") as mock_cmd,
            patch("forge_harness.fleet.agent_restart._last_restart_times", {}),
        ):
            success = restarter.restart_agent("tech", task_state)

            assert success

            # Verify cd command was sent
            calls = [str(call) for call in mock_cmd.call_args_list]
            assert any("cd /work/FORGE/harness" in str(call) for call in calls)

    def test_restart_rate_limiting(self, restarter):
        """Restart is rate limited to prevent loops."""
        with (
            patch.object(restarter, "_run_tmux_command"),
            patch("forge_harness.fleet.agent_restart._last_restart_times", {}) as mock_times,
        ):
            # First restart should succeed
            success1 = restarter.restart_agent("tech")
            assert success1

            # Immediate second restart should fail (rate limited)
            success2 = restarter.restart_agent("tech")
            assert not success2

    def test_restart_exception_handling(self, restarter):
        """Restart handles exceptions gracefully."""
        with patch.object(
            restarter,
            "_run_tmux_command",
            side_effect=Exception("tmux error"),
        ):
            success = restarter.restart_agent("tech")
            assert not success


class TestAutoRecover:
    """Test automatic recovery workflow."""

    def test_auto_recover_stale(self, restarter):
        """Full recovery workflow for stale agent."""
        with (
            patch.object(restarter, "is_stale", return_value=True),
            patch.object(restarter, "is_crashed", return_value=False),
            patch.object(
                restarter,
                "recover_task_state",
                return_value=TaskState(
                    prompt="Test task",
                    working_directory="/work",
                    files_modified=[],
                    last_tool_call=None,
                    context_percentage=20,
                    extracted_at=datetime.now(UTC),
                ),
            ),
            patch.object(restarter, "restart_agent", return_value=True),
        ):
            result = restarter.auto_recover("tech")

            assert result.success
            assert result.previous_status == "stale"
            assert result.task_recovered
            assert result.task_state is not None

    def test_auto_recover_crashed(self, restarter):
        """Full recovery workflow for crashed agent."""
        with (
            patch.object(restarter, "is_stale", return_value=False),
            patch.object(restarter, "is_crashed", return_value=True),
            patch.object(restarter, "recover_task_state", return_value=None),
            patch.object(restarter, "restart_agent", return_value=True),
        ):
            result = restarter.auto_recover("tech")

            assert result.success
            assert result.previous_status == "crashed"
            assert not result.task_recovered

    def test_auto_recover_healthy(self, restarter):
        """Healthy agent is not restarted."""
        with (
            patch.object(restarter, "is_stale", return_value=False),
            patch.object(restarter, "is_crashed", return_value=False),
        ):
            result = restarter.auto_recover("tech")

            assert result.success
            assert result.previous_status == "healthy"
            assert not result.task_recovered

    def test_auto_recover_restart_fails(self, restarter):
        """Recovery fails if restart fails."""
        with (
            patch.object(restarter, "is_stale", return_value=True),
            patch.object(restarter, "is_crashed", return_value=False),
            patch.object(restarter, "recover_task_state", return_value=None),
            patch.object(restarter, "restart_agent", return_value=False),
        ):
            result = restarter.auto_recover("tech")

            assert not result.success
            assert result.error == "Restart failed"

    def test_auto_recover_exception(self, restarter):
        """Recovery handles exceptions gracefully."""
        with (
            patch.object(restarter, "is_stale", return_value=True),
            patch.object(restarter, "is_crashed", return_value=False),
            patch.object(
                restarter,
                "recover_task_state",
                return_value=None,
            ),
            patch.object(
                restarter,
                "restart_agent",
                side_effect=Exception("Test error"),
            ),
        ):
            result = restarter.auto_recover("tech")

            assert not result.success
            assert "Test error" in result.error


class TestAutoRecoverAll:
    """Test batch recovery of all agents."""

    def test_auto_recover_all_multiple_agents(self):
        """Multiple stale agents are recovered."""
        from forge_harness.fleet.dashboard import FleetAgent, FleetStatus

        # Mock fleet status with 2 stale agents (using canonical FleetAgent)
        mock_status = FleetStatus(
            agents=[
                FleetAgent(
                    name="test-forge:tech",
                    session_id="test-forge",
                    agent_type="claude",
                    last_activity=datetime.now(UTC) - timedelta(minutes=45),
                    context_estimate=30,
                    is_stale=True,
                    window_name="tech",
                    status="stale",
                ),
                FleetAgent(
                    name="test-forge:content",
                    session_id="test-forge",
                    agent_type="claude",
                    last_activity=datetime.now(UTC) - timedelta(minutes=60),
                    context_estimate=20,
                    is_stale=True,
                    window_name="content",
                    status="stale",
                ),
                FleetAgent(
                    name="test-forge:qa",
                    session_id="test-forge",
                    agent_type="claude",
                    last_activity=datetime.now(UTC),
                    context_estimate=50,
                    is_stale=False,
                    window_name="qa",
                    status="active",
                ),
            ],
            total_active=1,
            total_idle=0,
            total_stale=2,
            overall_health="degraded",
            timestamp=datetime.now(UTC),
        )

        with (
            patch("forge_harness.fleet.dashboard.get_fleet_status", return_value=mock_status),
            patch.object(
                AgentRestarter,
                "auto_recover",
                return_value=RecoveryResult(
                    success=True,
                    window_name="test",
                    previous_status="stale",
                    task_recovered=False,
                    task_state=None,
                    error=None,
                    timestamp=datetime.now(UTC),
                ),
            ) as mock_recover,
        ):
            results = auto_recover_all("test-forge")

            # Should have attempted recovery of 2 stale agents
            assert len(results) == 2
            assert mock_recover.call_count == 2

    def test_auto_recover_all_no_stale_agents(self):
        """No recovery attempted for healthy fleet."""
        from forge_harness.fleet.dashboard import FleetAgent, FleetStatus

        mock_status = FleetStatus(
            agents=[
                FleetAgent(
                    name="test-forge:tech",
                    session_id="test-forge",
                    agent_type="claude",
                    last_activity=datetime.now(UTC),
                    context_estimate=40,
                    is_stale=False,
                    window_name="tech",
                    status="active",
                ),
            ],
            total_active=1,
            total_idle=0,
            total_stale=0,
            overall_health="healthy",
            timestamp=datetime.now(UTC),
        )

        with patch("forge_harness.fleet.dashboard.get_fleet_status", return_value=mock_status):
            results = auto_recover_all("test-forge")

            assert len(results) == 0

    def test_auto_recover_all_partial_success(self):
        """Some recoveries succeed, some fail."""
        from forge_harness.fleet.dashboard import FleetAgent, FleetStatus

        mock_status = FleetStatus(
            agents=[
                FleetAgent(
                    name="test-forge:tech",
                    session_id="test-forge",
                    agent_type="claude",
                    last_activity=datetime.now(UTC) - timedelta(minutes=45),
                    context_estimate=30,
                    is_stale=True,
                    window_name="tech",
                    status="stale",
                ),
                FleetAgent(
                    name="test-forge:content",
                    session_id="test-forge",
                    agent_type="claude",
                    last_activity=datetime.now(UTC) - timedelta(minutes=60),
                    context_estimate=20,
                    is_stale=True,
                    window_name="content",
                    status="crashed",
                ),
            ],
            total_active=0,
            total_idle=0,
            total_stale=2,
            overall_health="critical",
            timestamp=datetime.now(UTC),
        )

        # First recovery succeeds, second fails
        results = [
            RecoveryResult(
                success=True,
                window_name="tech",
                previous_status="stale",
                task_recovered=True,
                task_state=None,
                error=None,
                timestamp=datetime.now(UTC),
            ),
            RecoveryResult(
                success=False,
                window_name="content",
                previous_status="crashed",
                task_recovered=False,
                task_state=None,
                error="Restart failed",
                timestamp=datetime.now(UTC),
            ),
        ]

        with (
            patch("forge_harness.fleet.dashboard.get_fleet_status", return_value=mock_status),
            patch.object(
                AgentRestarter,
                "auto_recover",
                side_effect=results,
            ),
        ):
            recovery_results = auto_recover_all("test-forge")

            assert len(recovery_results) == 2
            assert recovery_results[0].success
            assert not recovery_results[1].success


class TestIntegration:
    """Integration tests with dashboard."""

    def test_integration_with_dashboard(self):
        """Restarter works with dashboard data."""
        from forge_harness.fleet import get_fleet_status

        with (
            patch("forge_harness.fleet.dashboard._list_forge_windows", return_value=[]),
            patch("forge_harness.fleet.agent_restart.AgentRestarter._run_tmux_command"),
        ):
            status = get_fleet_status()
            restarter = AgentRestarter()

            # Should not crash with empty fleet
            for agent in status.agents:
                if agent.status == "stale":
                    result = restarter.auto_recover(agent.name)
                    assert isinstance(result, RecoveryResult)
