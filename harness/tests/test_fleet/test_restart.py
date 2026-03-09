"""Tests for forge_harness.fleet.restart."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from forge_harness.fleet.restart import (
    AgentRestarter,
    HealthStatus,
    RestartEvent,
    TaskState,
    create_agent_restarter,
)


@pytest.fixture
def temp_forge_root(tmp_path: Path) -> Path:
    """Create a temporary FORGE root with fleet directory."""
    fleet_dir = tmp_path / ".forge/fleet"
    fleet_dir.mkdir()
    return tmp_path


@pytest.fixture
def restarter(temp_forge_root: Path) -> AgentRestarter:
    """Create an AgentRestarter instance for testing."""
    return AgentRestarter(forge_root=temp_forge_root, stale_threshold_minutes=30)


class TestAgentRestarterInit:
    def test_init_with_explicit_root(self, temp_forge_root: Path) -> None:
        restarter = AgentRestarter(forge_root=temp_forge_root)
        assert restarter.forge_root == temp_forge_root
        assert restarter.fleet_dir == temp_forge_root / ".forge/fleet"
        assert restarter.stale_threshold == timedelta(minutes=30)

    def test_init_creates_fleet_dir(self, tmp_path: Path) -> None:
        restarter = AgentRestarter(forge_root=tmp_path)
        assert restarter.fleet_dir.exists()

    def test_init_custom_threshold(self, temp_forge_root: Path) -> None:
        restarter = AgentRestarter(forge_root=temp_forge_root, stale_threshold_minutes=60)
        assert restarter.stale_threshold == timedelta(minutes=60)


class TestGetSessionList:
    @patch("subprocess.run")
    def test_get_session_list_success(self, mock_run: Mock, restarter: AgentRestarter) -> None:
        mock_run.return_value = Mock(
            returncode=0,
            stdout="forge-tech:1700000000:1700003600\nforge-qa:1700000000:1700000100\n",
        )

        sessions = restarter._get_session_list()

        assert len(sessions) == 2
        assert sessions[0]["name"] == "forge-tech"
        assert sessions[1]["name"] == "forge-qa"
        assert isinstance(sessions[0]["created"], datetime)
        assert isinstance(sessions[0]["last_activity"], datetime)

    @patch("subprocess.run")
    def test_get_session_list_no_sessions(self, mock_run: Mock, restarter: AgentRestarter) -> None:
        mock_run.return_value = Mock(returncode=1, stdout="")
        sessions = restarter._get_session_list()
        assert sessions == []

    @patch("subprocess.run")
    def test_get_session_list_tmux_not_found(
        self, mock_run: Mock, restarter: AgentRestarter
    ) -> None:
        mock_run.side_effect = FileNotFoundError()
        sessions = restarter._get_session_list()
        assert sessions == []

    @patch("subprocess.run")
    def test_get_session_list_timeout(self, mock_run: Mock, restarter: AgentRestarter) -> None:
        from subprocess import TimeoutExpired

        mock_run.side_effect = TimeoutExpired(cmd="tmux", timeout=10)
        sessions = restarter._get_session_list()
        assert sessions == []


class TestCaptureSessionOutput:
    @patch("subprocess.run")
    def test_capture_output_success(self, mock_run: Mock, restarter: AgentRestarter) -> None:
        mock_run.return_value = Mock(returncode=0, stdout="output line 1\noutput line 2\n")
        output = restarter._capture_session_output("forge:tech")
        assert output == "output line 1\noutput line 2\n"

    @patch("subprocess.run")
    def test_capture_output_failure(self, mock_run: Mock, restarter: AgentRestarter) -> None:
        mock_run.return_value = Mock(returncode=1, stdout="")
        output = restarter._capture_session_output("forge:tech")
        assert output == ""

    @patch("subprocess.run")
    def test_capture_output_timeout(self, mock_run: Mock, restarter: AgentRestarter) -> None:
        from subprocess import TimeoutExpired

        mock_run.side_effect = TimeoutExpired(cmd="tmux", timeout=10)
        output = restarter._capture_session_output("forge:tech")
        assert output == ""


class TestCheckSessionExitCode:
    @patch("subprocess.run")
    def test_check_exit_code_healthy(self, mock_run: Mock, restarter: AgentRestarter) -> None:
        # Session exists
        mock_run.return_value = Mock(returncode=0, stdout="$1\n")

        with patch.object(restarter, "_capture_session_output", return_value="working normally"):
            exit_code = restarter._check_session_exit_code("forge:tech")
            assert exit_code == 0

    @patch("subprocess.run")
    def test_check_exit_code_session_not_exist(
        self, mock_run: Mock, restarter: AgentRestarter
    ) -> None:
        mock_run.return_value = Mock(returncode=1, stdout="")
        exit_code = restarter._check_session_exit_code("forge:tech")
        assert exit_code == 1

    @patch("subprocess.run")
    def test_check_exit_code_with_error(self, mock_run: Mock, restarter: AgentRestarter) -> None:
        mock_run.return_value = Mock(returncode=0, stdout="$1\n")

        with patch.object(
            restarter, "_capture_session_output", return_value="Error: something failed"
        ):
            exit_code = restarter._check_session_exit_code("forge:tech")
            assert exit_code == 1


class TestExtractCurrentTask:
    def test_extract_task_with_task_label(self, restarter: AgentRestarter) -> None:
        output = "Task: Implement authentication module\nProgress: 50%"
        task = restarter._extract_current_task(output)
        assert task == "Implement authentication module"

    def test_extract_task_with_working_on_label(self, restarter: AgentRestarter) -> None:
        output = "Working on: fixing bug in user service"
        task = restarter._extract_current_task(output)
        assert task == "fixing bug in user service"

    def test_extract_task_with_feature_label(self, restarter: AgentRestarter) -> None:
        output = "Feature: Add API endpoint for user profile"
        task = restarter._extract_current_task(output)
        assert task == "Add API endpoint for user profile"

    def test_extract_task_with_markdown_header(self, restarter: AgentRestarter) -> None:
        output = "# Implementing payment gateway integration"
        task = restarter._extract_current_task(output)
        assert task == "Implementing payment gateway integration"

    def test_extract_task_no_match(self, restarter: AgentRestarter) -> None:
        output = "random text\nshort\n"
        task = restarter._extract_current_task(output)
        # Should return last line if it's long enough
        assert task in [None, "random text"]

    def test_extract_task_empty_output(self, restarter: AgentRestarter) -> None:
        task = restarter._extract_current_task("")
        assert task is None

    def test_extract_task_truncation(self, restarter: AgentRestarter) -> None:
        long_task = "Task: " + "a" * 300
        output = long_task
        task = restarter._extract_current_task(output)
        assert task is not None
        assert len(task) == 200


class TestExtractContextUsage:
    def test_extract_context_usage_pattern1(self, restarter: AgentRestarter) -> None:
        output = "context: 45%"
        usage = restarter._extract_context_usage(output)
        assert usage == 0.45

    def test_extract_context_usage_pattern2(self, restarter: AgentRestarter) -> None:
        output = "Current status: 65% context used"
        usage = restarter._extract_context_usage(output)
        assert usage == 0.65

    def test_extract_context_usage_pattern3(self, restarter: AgentRestarter) -> None:
        output = "usage: 80%"
        usage = restarter._extract_context_usage(output)
        assert usage == 0.80

    def test_extract_context_usage_no_match(self, restarter: AgentRestarter) -> None:
        output = "no context information here"
        usage = restarter._extract_context_usage(output)
        assert usage is None

    def test_extract_context_usage_empty_output(self, restarter: AgentRestarter) -> None:
        usage = restarter._extract_context_usage("")
        assert usage is None


class TestExtractErrorMessage:
    def test_extract_error_with_error_label(self, restarter: AgentRestarter) -> None:
        output = "Error: Connection refused on port 5432"
        error = restarter._extract_error_message(output)
        assert error == "Connection refused on port 5432"

    def test_extract_error_with_exception_label(self, restarter: AgentRestarter) -> None:
        output = "Exception: ValueError - Invalid input"
        error = restarter._extract_error_message(output)
        assert error == "ValueError - Invalid input"

    def test_extract_error_with_failed_label(self, restarter: AgentRestarter) -> None:
        output = "Failed: Test suite did not pass"
        error = restarter._extract_error_message(output)
        assert error == "Test suite did not pass"

    def test_extract_error_no_match(self, restarter: AgentRestarter) -> None:
        output = "Everything is working fine"
        error = restarter._extract_error_message(output)
        assert error is None

    def test_extract_error_truncation(self, restarter: AgentRestarter) -> None:
        long_error = "Error: " + "x" * 600
        output = long_error
        error = restarter._extract_error_message(output)
        assert error is not None
        assert len(error) == 500


class TestDetectStaleAgents:
    @patch.object(AgentRestarter, "_extract_context_usage")
    @patch.object(AgentRestarter, "_extract_current_task")
    @patch.object(AgentRestarter, "_capture_session_output")
    @patch.object(AgentRestarter, "_get_session_list")
    def test_detect_stale_agents_found(
        self,
        mock_get_sessions: Mock,
        mock_capture: Mock,
        mock_extract_task: Mock,
        mock_extract_context: Mock,
        restarter: AgentRestarter,
    ) -> None:
        now = datetime.now(UTC)
        mock_get_sessions.return_value = [
            {
                "name": "forge:tech",
                "created": now - timedelta(hours=2),
                "last_activity": now - timedelta(minutes=45),
            }
        ]
        mock_capture.return_value = "Task: old task"
        mock_extract_task.return_value = "old task"
        mock_extract_context.return_value = 0.5

        stale = restarter.detect_stale_agents()

        assert len(stale) == 1
        assert stale[0].agent_id == "forge:tech"
        assert stale[0].status == "stale"
        assert stale[0].current_task == "old task"
        assert stale[0].context_percent == 0.5

    @patch.object(AgentRestarter, "_get_session_list")
    def test_detect_stale_agents_none_found(
        self, mock_get_sessions: Mock, restarter: AgentRestarter
    ) -> None:
        now = datetime.now(UTC)
        mock_get_sessions.return_value = [
            {
                "name": "forge:tech",
                "created": now - timedelta(minutes=10),
                "last_activity": now - timedelta(minutes=5),
            }
        ]

        stale = restarter.detect_stale_agents()
        assert len(stale) == 0

    @patch.object(AgentRestarter, "_get_session_list")
    def test_detect_stale_agents_custom_threshold(
        self, mock_get_sessions: Mock, restarter: AgentRestarter
    ) -> None:
        now = datetime.now(UTC)
        mock_get_sessions.return_value = [
            {
                "name": "forge:tech",
                "created": now - timedelta(hours=1),
                "last_activity": now - timedelta(minutes=45),
            }
        ]

        with patch.object(restarter, "_capture_session_output", return_value=""):
            with patch.object(restarter, "_extract_current_task", return_value=None):
                with patch.object(restarter, "_extract_context_usage", return_value=None):
                    # 45 minutes should not be stale with 60 minute threshold
                    stale = restarter.detect_stale_agents(threshold_minutes=60)
                    assert len(stale) == 0

                    # 45 minutes should be stale with 30 minute threshold
                    stale = restarter.detect_stale_agents(threshold_minutes=30)
                    assert len(stale) == 1


class TestDetectCrashedAgents:
    @patch.object(AgentRestarter, "_extract_error_message")
    @patch.object(AgentRestarter, "_extract_current_task")
    @patch.object(AgentRestarter, "_check_session_exit_code")
    @patch.object(AgentRestarter, "_get_session_list")
    def test_detect_crashed_agents_found(
        self,
        mock_get_sessions: Mock,
        mock_check_exit: Mock,
        mock_extract_task: Mock,
        mock_extract_error: Mock,
        restarter: AgentRestarter,
    ) -> None:
        now = datetime.now(UTC)
        mock_get_sessions.return_value = [
            {
                "name": "forge:tech",
                "created": now - timedelta(hours=1),
                "last_activity": now - timedelta(minutes=10),
            }
        ]
        mock_check_exit.return_value = 1
        mock_extract_task.return_value = "failed task"
        mock_extract_error.return_value = "Connection error"

        crashed = restarter.detect_crashed_agents()

        assert len(crashed) == 1
        assert crashed[0].agent_id == "forge:tech"
        assert crashed[0].status == "crashed"
        assert crashed[0].current_task == "failed task"
        assert crashed[0].error_message == "Connection error"

    @patch.object(AgentRestarter, "_check_session_exit_code")
    @patch.object(AgentRestarter, "_get_session_list")
    def test_detect_crashed_agents_none_found(
        self, mock_get_sessions: Mock, mock_check_exit: Mock, restarter: AgentRestarter
    ) -> None:
        now = datetime.now(UTC)
        mock_get_sessions.return_value = [
            {
                "name": "forge:tech",
                "created": now - timedelta(hours=1),
                "last_activity": now - timedelta(minutes=5),
            }
        ]
        mock_check_exit.return_value = 0

        crashed = restarter.detect_crashed_agents()
        assert len(crashed) == 0


class TestRecoverTaskState:
    @patch.object(AgentRestarter, "_capture_session_output")
    def test_recover_task_state_success(
        self, mock_capture: Mock, restarter: AgentRestarter
    ) -> None:
        mock_capture.return_value = (
            "Task: Implement user authentication\n"
            "Progress: Step 2 of 5\n"
            "Working on: src/auth/login.py\n"
            "context: 35%\n"
        )

        state = restarter.recover_task_state("forge:tech")

        assert state is not None
        assert state.task_description == "Implement user authentication"
        assert state.progress == "Step 2 of 5"
        assert state.file_being_edited == "src/auth/login.py"
        assert state.context_remaining == 65

    @patch.object(AgentRestarter, "_capture_session_output")
    def test_recover_task_state_no_output(
        self, mock_capture: Mock, restarter: AgentRestarter
    ) -> None:
        mock_capture.return_value = ""
        state = restarter.recover_task_state("forge:tech")
        assert state is None

    @patch.object(AgentRestarter, "_capture_session_output")
    def test_recover_task_state_no_task(
        self, mock_capture: Mock, restarter: AgentRestarter
    ) -> None:
        # Use very short output that won't match any patterns and is below min length
        mock_capture.return_value = "short\nno"
        state = restarter.recover_task_state("forge:tech")
        assert state is None

    @patch.object(AgentRestarter, "_capture_session_output")
    def test_recover_task_state_partial_info(
        self, mock_capture: Mock, restarter: AgentRestarter
    ) -> None:
        mock_capture.return_value = "Task: Simple task without other info"
        state = restarter.recover_task_state("forge:tech")

        assert state is not None
        assert state.task_description == "Simple task without other info"
        assert state.progress is None
        assert state.file_being_edited is None
        assert state.context_remaining is None


class TestRestartAgent:
    @patch("subprocess.run")
    def test_restart_agent_success(
        self, mock_run: Mock, restarter: AgentRestarter, tmp_path: Path
    ) -> None:
        restarter.forge_root = tmp_path
        restarter.fleet_dir = tmp_path / ".forge/fleet"
        restarter.fleet_dir.mkdir(exist_ok=True)
        restarter.restart_log = restarter.fleet_dir / "restart.log"

        mock_run.return_value = Mock(returncode=0)

        event = restarter.restart_agent("forge:tech", "Recovered context", "test_reason")

        assert event.agent_id.startswith("forge:tech-restart-")
        assert event.reason == "test_reason"
        assert event.recovered_task == "Recovered context"
        assert event.old_session_id == "forge:tech"

        # Check restart context file was created
        restart_dir = tmp_path / ".forge/restarts"
        assert restart_dir.exists()
        context_files = list(restart_dir.glob("*.md"))
        assert len(context_files) == 1

        # Check restart log was created
        assert restarter.restart_log.exists()

    @patch("subprocess.run")
    def test_restart_agent_tmux_failure(self, mock_run: Mock, restarter: AgentRestarter) -> None:
        from subprocess import CalledProcessError

        mock_run.side_effect = CalledProcessError(1, "tmux")

        with pytest.raises(CalledProcessError):
            restarter.restart_agent("forge:tech", "context", "reason")


class TestAutoRestartStale:
    @patch.object(AgentRestarter, "restart_agent")
    @patch.object(AgentRestarter, "recover_task_state")
    @patch.object(AgentRestarter, "detect_stale_agents")
    def test_auto_restart_stale_success(
        self,
        mock_detect: Mock,
        mock_recover: Mock,
        mock_restart: Mock,
        restarter: AgentRestarter,
    ) -> None:
        now = datetime.now(UTC)
        mock_detect.return_value = [
            HealthStatus(
                agent_id="forge:tech",
                status="stale",
                last_activity=now - timedelta(minutes=45),
                current_task="old task",
            )
        ]
        mock_recover.return_value = TaskState(
            task_description="Implement feature X",
            progress="Step 1",
            file_being_edited="src/main.py",
        )
        mock_restart.return_value = RestartEvent(
            agent_id="forge:tech-restart-123",
            reason="stale_agent",
            recovered_task="Implement feature X",
            new_session_id="forge:tech-restart-123",
            old_session_id="forge:tech",
        )

        events = restarter.auto_restart_stale()

        assert len(events) == 1
        assert events[0].reason == "stale_agent"
        mock_restart.assert_called_once()

    @patch.object(AgentRestarter, "detect_stale_agents")
    def test_auto_restart_stale_none_detected(
        self, mock_detect: Mock, restarter: AgentRestarter
    ) -> None:
        mock_detect.return_value = []
        events = restarter.auto_restart_stale()
        assert len(events) == 0

    @patch.object(AgentRestarter, "detect_stale_agents")
    def test_auto_restart_stale_dry_run(self, mock_detect: Mock, restarter: AgentRestarter) -> None:
        now = datetime.now(UTC)
        mock_detect.return_value = [
            HealthStatus(
                agent_id="forge:tech",
                status="stale",
                last_activity=now - timedelta(minutes=45),
            )
        ]

        events = restarter.auto_restart_stale(dry_run=True)
        assert len(events) == 0

    @patch.object(AgentRestarter, "restart_agent")
    @patch.object(AgentRestarter, "recover_task_state")
    @patch.object(AgentRestarter, "detect_stale_agents")
    def test_auto_restart_stale_partial_failure(
        self,
        mock_detect: Mock,
        mock_recover: Mock,
        mock_restart: Mock,
        restarter: AgentRestarter,
    ) -> None:
        now = datetime.now(UTC)
        mock_detect.return_value = [
            HealthStatus(
                agent_id="forge:tech", status="stale", last_activity=now - timedelta(minutes=45)
            ),
            HealthStatus(
                agent_id="forge:qa", status="stale", last_activity=now - timedelta(minutes=50)
            ),
        ]
        mock_recover.side_effect = [
            TaskState(task_description="task1"),
            None,  # Recovery fails for second agent
        ]
        mock_restart.side_effect = [
            RestartEvent(
                agent_id="forge:tech-restart-123",
                reason="stale_agent",
                recovered_task="task1",
                new_session_id="forge:tech-restart-123",
            ),
        ]

        events = restarter.auto_restart_stale()

        # First agent should restart successfully, second should use fallback
        assert len(events) >= 1


class TestGetRestartHistory:
    def test_get_restart_history_success(self, restarter: AgentRestarter, tmp_path: Path) -> None:
        restarter.restart_log = tmp_path / "restart.log"

        # Write some test events
        events = [
            {"timestamp": "2026-01-01T00:00:00", "old_session": "s1", "new_session": "s1-new"},
            {"timestamp": "2026-01-02T00:00:00", "old_session": "s2", "new_session": "s2-new"},
        ]

        with open(restarter.restart_log, "w") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")

        history = restarter.get_restart_history()
        assert len(history) == 2
        assert history[0]["old_session"] == "s1"
        assert history[1]["old_session"] == "s2"

    def test_get_restart_history_limit(self, restarter: AgentRestarter, tmp_path: Path) -> None:
        restarter.restart_log = tmp_path / "restart.log"

        # Write more events than the limit
        with open(restarter.restart_log, "w") as f:
            for i in range(20):
                event = {"timestamp": f"2026-01-{i + 1:02d}", "session": f"s{i}"}
                f.write(json.dumps(event) + "\n")

        history = restarter.get_restart_history(limit=5)
        assert len(history) == 5
        # Should get the most recent 5
        assert history[-1]["session"] == "s19"

    def test_get_restart_history_no_file(self, restarter: AgentRestarter) -> None:
        history = restarter.get_restart_history()
        assert history == []


class TestCreateAgentRestarter:
    def test_create_agent_restarter_defaults(self, temp_forge_root: Path) -> None:
        restarter = create_agent_restarter(forge_root=temp_forge_root)
        assert isinstance(restarter, AgentRestarter)
        assert restarter.stale_threshold == timedelta(minutes=30)

    def test_create_agent_restarter_custom_threshold(self, temp_forge_root: Path) -> None:
        restarter = create_agent_restarter(
            forge_root=temp_forge_root,
            stale_threshold_minutes=45,
        )
        assert restarter.stale_threshold == timedelta(minutes=45)


# Import json for history test
import json
