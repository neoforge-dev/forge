"""Tests for forge_harness.fleet.dashboard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

from forge_harness.fleet.dashboard import (
    FleetAgent,
    FleetStatus,
    TmuxInterface,
    get_fleet_status,
    watch_fleet,
)


class TestTmuxInterfaceListSessions:
    @patch("subprocess.run")
    def test_list_sessions_success(self, mock_run: Mock) -> None:
        list_result = Mock(returncode=0, stdout="forge:tech\nforge:qa\n")
        windows_result_tech = Mock(returncode=0, stdout="main 1\nlogs 0\n")
        windows_result_qa = Mock(returncode=0, stdout="tests 1\n")

        mock_run.side_effect = [list_result, windows_result_tech, windows_result_qa]

        sessions = TmuxInterface.list_sessions()

        assert sessions == [
            {
                "session_name": "forge:tech",
                "window_name": "main",
                "full_id": "forge:tech:main",
                "is_active": True,
            },
            {
                "session_name": "forge:tech",
                "window_name": "logs",
                "full_id": "forge:tech:logs",
                "is_active": False,
            },
            {
                "session_name": "forge:qa",
                "window_name": "tests",
                "full_id": "forge:qa:tests",
                "is_active": True,
            },
        ]

    @patch("subprocess.run")
    def test_list_sessions_no_tmux(self, mock_run: Mock) -> None:
        mock_run.side_effect = FileNotFoundError()
        sessions = TmuxInterface.list_sessions()
        assert sessions == []

    @patch("subprocess.run")
    def test_list_sessions_no_sessions(self, mock_run: Mock) -> None:
        mock_run.return_value = Mock(returncode=1, stdout="")
        sessions = TmuxInterface.list_sessions()
        assert sessions == []


class TestTmuxInterfaceCaptureOutput:
    @patch("subprocess.run")
    def test_capture_output_success(self, mock_run: Mock) -> None:
        mock_run.return_value = Mock(returncode=0, stdout="hello\nworld\n")
        output = TmuxInterface.capture_output("forge:tech")
        assert output == "hello\nworld\n"

    @patch("subprocess.run")
    def test_capture_output_failure(self, mock_run: Mock) -> None:
        mock_run.return_value = Mock(returncode=1, stdout="")
        output = TmuxInterface.capture_output("forge:tech")
        assert output == ""

    @patch("subprocess.run")
    def test_capture_output_timeout(self, mock_run: Mock) -> None:
        from subprocess import TimeoutExpired

        mock_run.side_effect = TimeoutExpired(cmd="tmux", timeout=10)
        output = TmuxInterface.capture_output("forge:tech")
        assert output == ""


class TestTmuxInterfaceGetSessionTime:
    @patch("subprocess.run")
    def test_get_session_time_success(self, mock_run: Mock) -> None:
        epoch = 1700000000
        mock_run.return_value = Mock(returncode=0, stdout=f"{epoch}\n")
        result = TmuxInterface.get_session_time("forge:tech")
        assert result == datetime.fromtimestamp(epoch, tz=UTC)

    @patch("subprocess.run")
    def test_get_session_time_invalid(self, mock_run: Mock) -> None:
        mock_run.return_value = Mock(returncode=0, stdout="not-epoch")
        result = TmuxInterface.get_session_time("forge:tech")
        assert result is None

    @patch("subprocess.run")
    def test_get_session_time_timeout(self, mock_run: Mock) -> None:
        from subprocess import TimeoutExpired

        mock_run.side_effect = TimeoutExpired(cmd="tmux", timeout=10)
        result = TmuxInterface.get_session_time("forge:tech")
        assert result is None


class TestDetectAgentType:
    def test_detect_agent_type_known(self) -> None:
        assert TmuxInterface.detect_agent_type("forge:codex", "main") == "codex"
        assert TmuxInterface.detect_agent_type("forge:gemini", "main") == "gemini"
        assert TmuxInterface.detect_agent_type("forge:opencode", "main") == "opencode"
        assert TmuxInterface.detect_agent_type("forge:tech", "main") == "claude"
        assert TmuxInterface.detect_agent_type("forge:qa", "main") == "claude"

    def test_detect_agent_type_unknown(self) -> None:
        assert TmuxInterface.detect_agent_type("forge:unknown", "main") == "unknown"


class TestDetectStatus:
    def test_detect_status_error(self) -> None:
        output = "Traceback (most recent call last): error occurred"
        assert TmuxInterface.detect_status(output) == "error"

    def test_detect_status_active(self) -> None:
        output = "running analysis and processing tasks"
        assert TmuxInterface.detect_status(output) == "active"

    def test_detect_status_idle(self) -> None:
        output = "completed and waiting"
        assert TmuxInterface.detect_status(output) == "idle"

    def test_detect_status_unknown(self) -> None:
        assert TmuxInterface.detect_status("") == "unknown"


class TestGetFleetStatus:
    @patch("forge_harness.fleet.dashboard.TmuxInterface.get_session_time")
    @patch("forge_harness.fleet.dashboard.TmuxInterface.detect_status")
    @patch("forge_harness.fleet.dashboard.TmuxInterface.detect_agent_type")
    @patch("forge_harness.fleet.dashboard.TmuxInterface.capture_output")
    @patch("forge_harness.fleet.dashboard.TmuxInterface.list_sessions")
    def test_get_fleet_status(
        self,
        mock_list: Mock,
        mock_capture: Mock,
        mock_detect_agent: Mock,
        mock_detect_status: Mock,
        mock_session_time: Mock,
    ) -> None:
        mock_list.return_value = [
            {
                "session_name": "forge:tech",
                "window_name": "main",
                "full_id": "forge:tech:main",
                "is_active": True,
            }
        ]
        mock_capture.return_value = "working\n"
        mock_detect_agent.return_value = "claude"
        mock_detect_status.return_value = "active"
        mock_session_time.return_value = datetime.now(UTC) - timedelta(minutes=5)

        status = get_fleet_status()

        assert isinstance(status, FleetStatus)
        assert status.total_agents == 1
        assert status.active_agents == 1
        assert status.stale_agents == 0
        assert isinstance(status.agents[0], FleetAgent)
        assert status.agents[0].name == "forge:tech:main"
        assert status.agents[0].agent_type == "claude"
        assert status.agents[0].status == "active"


class TestFleetStatus:
    def test_from_agents_empty(self) -> None:
        status = FleetStatus.from_agents([])
        assert status.total_agents == 0
        assert status.stale_agents == 0
        assert status.active_agents == 0

    def test_from_agents_counts(self) -> None:
        now = datetime.now(UTC)
        agents = [
            FleetAgent(
                name="a1",
                session_id="a1",
                agent_type="claude",
                last_activity=now,
                context_estimate=10,
                is_stale=False,
                window_name="main",
                status="active",
            ),
            FleetAgent(
                name="a2",
                session_id="a2",
                agent_type="claude",
                last_activity=now - timedelta(minutes=40),
                context_estimate=0,
                is_stale=True,
                window_name="main",
                status="idle",
            ),
        ]
        status = FleetStatus.from_agents(agents)
        assert status.total_agents == 2
        assert status.stale_agents == 1
        assert status.active_agents == 1


class TestWatchFleet:
    @patch("forge_harness.fleet.dashboard.get_fleet_status")
    @patch("time.sleep", return_value=None)
    def test_watch_fleet_single_iteration(
        self, mock_sleep: Mock, mock_status: Mock, capsys
    ) -> None:
        mock_status.return_value = FleetStatus.from_agents([])
        watch_fleet(interval_seconds=0, max_iterations=1)
        out = capsys.readouterr().out
        assert "FORGE Fleet Status" in out
        assert "Total Agents: 0" in out
        assert "Iteration 1/1" in out

    @patch("forge_harness.fleet.dashboard.TmuxInterface.capture_output")
    @patch("forge_harness.fleet.dashboard.get_fleet_status")
    @patch("time.sleep", return_value=None)
    def test_watch_fleet_with_agent_output(
        self,
        mock_sleep: Mock,
        mock_status: Mock,
        mock_capture: Mock,
        capsys,
    ) -> None:
        now = datetime.now(UTC)
        mock_status.return_value = FleetStatus.from_agents(
            [
                FleetAgent(
                    name="forge:tech:main",
                    session_id="forge:tech",
                    agent_type="claude",
                    last_activity=now,
                    context_estimate=2048,
                    is_stale=False,
                    window_name="main",
                    status="active",
                )
            ]
        )
        mock_capture.return_value = "line1\nline2\nlast line\n"

        watch_fleet(interval_seconds=0, max_iterations=1)
        out = capsys.readouterr().out
        assert "forge:tech:main" in out
        assert "Status: active" in out
        assert "Recent: last line" in out

    @patch("forge_harness.fleet.dashboard.get_fleet_status")
    @patch("time.sleep", return_value=None)
    def test_watch_fleet_stale_and_error(
        self,
        mock_sleep: Mock,
        mock_status: Mock,
        capsys,
    ) -> None:
        now = datetime.now(UTC)
        mock_status.return_value = FleetStatus.from_agents(
            [
                FleetAgent(
                    name="forge:qa:tests",
                    session_id="forge:qa",
                    agent_type="claude",
                    last_activity=now - timedelta(minutes=45),
                    context_estimate=0,
                    is_stale=True,
                    window_name="tests",
                    status="error",
                )
            ]
        )

        watch_fleet(interval_seconds=0, max_iterations=1)
        out = capsys.readouterr().out
        assert "forge:qa:tests" in out
        assert "Status: error" in out
        assert "STALE" in out


class TestTmuxInterfaceErrors:
    @patch("subprocess.run")
    def test_list_sessions_windows_error(self, mock_run: Mock) -> None:
        list_result = Mock(returncode=0, stdout="forge:tech\n")
        windows_result = Mock(returncode=1, stdout="")
        mock_run.side_effect = [list_result, windows_result]
        sessions = TmuxInterface.list_sessions()
        assert sessions == []


class TestFleetStatusConsolidation:
    """Test that FleetStatus uses canonical FleetAgent (not legacy AgentInfo)."""

    def test_fleet_status_uses_fleet_agent(self) -> None:
        """FleetStatus.agents should contain FleetAgent instances, not AgentInfo."""
        now = datetime.now(UTC)
        agents = [
            FleetAgent(
                name="forge:tech:main",
                session_id="forge:tech",
                agent_type="claude",
                last_activity=now,
                context_estimate=100,
                is_stale=False,
                window_name="main",
                status="active",
            )
        ]
        status = FleetStatus.from_agents(agents)

        # Assert FleetAgent is used (canonical implementation)
        assert len(status.agents) == 1
        assert isinstance(status.agents[0], FleetAgent)
        assert status.agents[0].name == "forge:tech:main"
        assert status.agents[0].agent_type == "claude"

    def test_fleet_status_has_from_agents_factory(self) -> None:
        """FleetStatus should have from_agents factory method (canonical)."""
        assert hasattr(FleetStatus, "from_agents")
        assert callable(FleetStatus.from_agents)

    def test_fleet_status_supports_legacy_field_names(self) -> None:
        """Canonical FleetStatus should support legacy field names for compatibility."""
        now = datetime.now(UTC)
        agents = [
            FleetAgent(
                name="a1",
                session_id="s1",
                agent_type="claude",
                last_activity=now,
                context_estimate=50,
                is_stale=False,
                window_name="main",
                status="active",
            )
        ]
        status = FleetStatus.from_agents(agents)

        # Both legacy and new field names should work
        assert status.total_active == status.active_agents == 1
        assert status.total_stale == status.stale_agents == 0

    def test_get_fleet_status_returns_canonical_type(self) -> None:
        """get_fleet_status() should return FleetStatus with FleetAgent instances."""
        with patch("forge_harness.fleet.dashboard.TmuxInterface.list_sessions") as mock_list:
            mock_list.return_value = []

            status = get_fleet_status()

            assert isinstance(status, FleetStatus)
            # Verify canonical FleetStatus fields exist
            assert hasattr(status, "agents")
            assert hasattr(status, "active_agents")
            assert hasattr(status, "stale_agents")
            assert hasattr(status, "from_agents")
