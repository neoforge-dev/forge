"""Tests for fleet health dashboard."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.dashboard_core import AgentState, compute_fleet_health
from forge_harness.fleet.dashboard import (
    _classify_status_by_time,
    _detect_status,
    _estimate_last_activity,
    _extract_context_percentage,
    _list_forge_windows,
    _run_tmux_command,
    get_fleet_status,
)


@pytest.fixture
def mock_subprocess():
    """Mock subprocess.run for tmux commands."""
    with patch("subprocess.run") as mock:
        yield mock


@pytest.fixture
def mock_datetime():
    """Mock datetime.now for consistent testing."""
    fixed_time = datetime(2026, 1, 29, 12, 0, 0, tzinfo=UTC)
    with patch("forge_harness.fleet.dashboard.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_time
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        yield mock_dt


class TestTmuxCommands:
    """Tests for tmux command execution."""

    def test_run_tmux_command_success(self, mock_subprocess):
        """Test successful tmux command execution."""
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="forge\nforge:tech\n",
        )

        result = _run_tmux_command(["list-sessions"])

        mock_subprocess.assert_called_once()
        assert result == "forge\nforge:tech"

    def test_run_tmux_command_failure(self, mock_subprocess):
        """Test tmux command failure handling."""
        mock_subprocess.return_value = MagicMock(
            returncode=1,
            stdout="",
        )

        result = _run_tmux_command(["list-sessions"])

        assert result == ""

    def test_run_tmux_command_timeout(self, mock_subprocess):
        """Test tmux command timeout handling."""
        import subprocess

        mock_subprocess.side_effect = subprocess.TimeoutExpired(cmd=["tmux"], timeout=10)

        result = _run_tmux_command(["list-sessions"])

        assert result == ""

    def test_run_tmux_command_not_found(self, mock_subprocess):
        """Test handling when tmux is not installed."""
        mock_subprocess.side_effect = FileNotFoundError("tmux not found")

        result = _run_tmux_command(["list-sessions"])

        assert result == ""


class TestWindowDiscovery:
    """Tests for forge window discovery."""

    def test_list_forge_windows_success(self, mock_subprocess):
        """Test successful window listing."""

        def tmux_side_effect(*args, **kwargs):
            cmd = args[0]
            if "list-sessions" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout="forge\nother-session\n",
                )
            elif "list-windows" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout="tech\ncontent\nqa\n",
                )
            return MagicMock(returncode=0, stdout="")

        mock_subprocess.side_effect = tmux_side_effect

        windows = _list_forge_windows()

        assert len(windows) == 3
        assert ("forge", "tech") in windows
        assert ("forge", "content") in windows
        assert ("forge", "qa") in windows

    def test_list_forge_windows_no_sessions(self, mock_subprocess):
        """Test when no tmux sessions exist."""
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="",
        )

        windows = _list_forge_windows()

        assert windows == []

    def test_list_forge_windows_no_forge_session(self, mock_subprocess):
        """Test when forge session doesn't exist."""
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="other-session\nanother-session\n",
        )

        windows = _list_forge_windows()

        assert windows == []

    def test_list_forge_windows_multiple_forge_sessions(self, mock_subprocess):
        """Test multiple forge sessions."""

        def tmux_side_effect(*args, **kwargs):
            cmd = args[0]
            if "list-sessions" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout="forge\nforge-backup\n",
                )
            elif "list-windows" in cmd and "forge-backup" in str(cmd):
                return MagicMock(
                    returncode=0,
                    stdout="research\n",
                )
            elif "list-windows" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout="tech\n",
                )
            return MagicMock(returncode=0, stdout="")

        mock_subprocess.side_effect = tmux_side_effect

        windows = _list_forge_windows()

        assert len(windows) == 2
        assert ("forge", "tech") in windows
        assert ("forge-backup", "research") in windows


class TestStatusDetection:
    """Tests for agent status detection."""

    def test_detect_status_active_insert_mode(self):
        """Test detection of active status via INSERT mode."""
        content = """
Some output here
[INSERT] editing code
more content
"""
        status = _detect_status(content)
        assert status == "active"

    def test_detect_status_active_tool_activity(self):
        """Test detection of active status via tool activity."""
        test_cases = [
            "Reading file: config.py\n",
            "Writing to database\n",
            "Running tests...\n",
            "Editing component.tsx\n",
            "Searching for pattern\n",
            "Thinking about the problem\n",
            "Working on the feature\n",
        ]

        for content in test_cases:
            status = _detect_status(content)
            assert status == "active", f"Failed for: {content}"

    def test_detect_status_idle_bash_prompt(self):
        """Test detection of idle status via bash prompt."""
        content = """
Last command output
Some more text
$ """
        status = _detect_status(content)
        assert status == "idle"

    def test_detect_status_idle_zsh_prompt(self):
        """Test detection of idle status via zsh prompt."""
        content = """
Command output
❯ """
        status = _detect_status(content)
        assert status == "idle"

    def test_detect_status_idle_generic_prompt(self):
        """Test detection of idle status via generic prompt."""
        content = """
Output here
> """
        status = _detect_status(content)
        assert status == "idle"

    def test_detect_status_stale_empty(self):
        """Test detection of stale status with empty content."""
        status = _detect_status("")
        assert status == "stale"

    def test_detect_status_idle_with_content(self):
        """Test idle status when content exists but no clear indicators."""
        content = """
Some historical output
from previous commands
but nothing recent
"""
        status = _detect_status(content)
        assert status == "idle"


class TestActivityEstimation:
    """Tests for last activity estimation."""

    def test_estimate_last_activity_active(self, mock_datetime):
        """Test activity estimation for active agents."""
        content = "Working on something"
        last_activity = _estimate_last_activity(content, "active")

        # Should be current time for active agents
        assert last_activity == mock_datetime.now.return_value

    def test_estimate_last_activity_with_timestamp(self, mock_datetime):
        """Test activity estimation with timestamp in output."""
        # Timestamp 30 minutes ago
        content = """
[11:30:00] Last activity here
Some other content
"""
        last_activity = _estimate_last_activity(content, "idle")

        # Should extract 11:30:00
        expected_time = mock_datetime.now.return_value.replace(
            hour=11, minute=30, second=0, microsecond=0
        )
        assert last_activity == expected_time

    def test_estimate_last_activity_idle_no_timestamp(self, mock_datetime):
        """Test activity estimation for idle without timestamp."""
        content = "Some content without timestamp"
        last_activity = _estimate_last_activity(content, "idle")

        # Should estimate ~10 minutes ago for idle
        now = mock_datetime.now.return_value
        delta = (now - last_activity).total_seconds() / 60
        assert 8 <= delta <= 12  # Around 10 minutes

    def test_estimate_last_activity_stale_no_timestamp(self, mock_datetime):
        """Test activity estimation for stale without timestamp."""
        content = "Old content"
        last_activity = _estimate_last_activity(content, "stale")

        # Should estimate at least 30 minutes ago for stale
        now = mock_datetime.now.return_value
        delta = (now - last_activity).total_seconds() / 60
        assert delta >= 30

    def test_estimate_last_activity_multiple_timestamps(self, mock_datetime):
        """Test activity estimation with multiple timestamps."""
        content = """
[10:00:00] Old activity
[11:00:00] Newer activity
[11:45:00] Most recent activity
"""
        last_activity = _estimate_last_activity(content, "idle")

        # Should extract the latest timestamp (11:45:00)
        expected_time = mock_datetime.now.return_value.replace(
            hour=11, minute=45, second=0, microsecond=0
        )
        assert last_activity == expected_time


class TestContextEstimation:
    """Tests for context usage estimation."""

    def test_extract_context_explicit_colon(self):
        """Test extraction of explicit context with colon."""
        content = "Context: 45%"
        context = _extract_context_percentage(content)
        assert context == 45

    def test_extract_context_explicit_suffix(self):
        """Test extraction of explicit context as suffix."""
        content = "Using 67% context"
        context = _extract_context_percentage(content)
        assert context == 67

    def test_extract_context_usage_label(self):
        """Test extraction with 'context usage' label."""
        content = "Context usage: 82%"
        context = _extract_context_percentage(content)
        assert context == 82

    def test_extract_context_bounds_high(self):
        """Test context percentage bounded to 100."""
        content = "Context: 150%"  # Invalid, should be capped
        context = _extract_context_percentage(content)
        assert context == 100

    def test_extract_context_bounds_low(self):
        """Test context percentage with no match falls back to heuristic."""
        # Negative percentages won't match regex, falls back to heuristic
        content = "Context: -10%"
        context = _extract_context_percentage(content)
        # Falls back to heuristic based on line count (very short = 5%)
        assert context == 5

    def test_extract_context_heuristic_high(self):
        """Test heuristic estimation for high output volume."""
        # Create content with 600 lines
        content = "\n".join([f"Line {i}" for i in range(600)])
        context = _extract_context_percentage(content)
        assert context == 60

    def test_extract_context_heuristic_medium(self):
        """Test heuristic estimation for medium output volume."""
        # Create content with 200 lines
        content = "\n".join([f"Line {i}" for i in range(200)])
        context = _extract_context_percentage(content)
        assert context == 25

    def test_extract_context_heuristic_low(self):
        """Test heuristic estimation for low output volume."""
        # Create content with 30 lines
        content = "\n".join([f"Line {i}" for i in range(30)])
        context = _extract_context_percentage(content)
        assert context == 5


class TestStatusClassification:
    """Tests for time-based status classification."""

    def test_classify_status_active_remains_active(self, mock_datetime):
        """Test active status remains active if recent."""
        now = mock_datetime.now.return_value
        last_activity = now - timedelta(minutes=2)

        status = _classify_status_by_time("active", last_activity)
        assert status == "active"

    def test_classify_status_idle_remains_idle(self, mock_datetime):
        """Test idle status remains idle within threshold."""
        now = mock_datetime.now.return_value
        last_activity = now - timedelta(minutes=15)

        status = _classify_status_by_time("idle", last_activity)
        assert status == "idle"

    def test_classify_status_idle_to_stale(self, mock_datetime):
        """Test idle status becomes stale after threshold."""
        now = mock_datetime.now.return_value
        last_activity = now - timedelta(minutes=35)

        status = _classify_status_by_time("idle", last_activity)
        assert status == "stale"

    def test_classify_status_active_to_stale(self, mock_datetime):
        """Test active status becomes stale after threshold."""
        now = mock_datetime.now.return_value
        last_activity = now - timedelta(minutes=40)

        status = _classify_status_by_time("active", last_activity)
        assert status == "stale"

    def test_classify_status_stale_remains_stale(self, mock_datetime):
        """Test stale status remains stale."""
        now = mock_datetime.now.return_value
        last_activity = now - timedelta(hours=2)

        status = _classify_status_by_time("stale", last_activity)
        assert status == "stale"


class TestHealthDetermination:
    """Tests for overall fleet health determination."""

    def test_health_determination_healthy(self):
        """Test healthy fleet with no stale agents."""
        agents = [
            AgentState(id="forge:tech", status="active", is_stale=False),
            AgentState(id="forge:content", status="idle", is_stale=False),
            AgentState(id="forge:qa", status="active", is_stale=False),
        ]

        health = compute_fleet_health(agents)
        assert health == "healthy"

    def test_health_determination_degraded(self):
        """Test degraded fleet with <50% stale agents."""
        agents = [
            AgentState(id="forge:tech", status="active", is_stale=False),
            AgentState(id="forge:content", status="stale", is_stale=True),
            AgentState(id="forge:qa", status="active", is_stale=False),
            AgentState(id="forge:research", status="idle", is_stale=False),
        ]

        health = compute_fleet_health(agents)
        assert health == "degraded"

    def test_health_determination_critical_half_stale(self):
        """Test critical fleet with >=50% stale agents."""
        agents = [
            AgentState(id="forge:tech", status="stale", is_stale=True),
            AgentState(id="forge:content", status="stale", is_stale=True),
            AgentState(id="forge:qa", status="active", is_stale=False),
        ]

        health = compute_fleet_health(agents)
        assert health == "critical"

    def test_health_determination_critical_no_agents(self):
        """Test critical health when no agents are running."""
        agents = []

        health = compute_fleet_health(agents)
        assert health == "critical"


class TestGetFleetStatus:
    """Tests for get_fleet_status main function."""

    def test_get_fleet_status_success(self, mock_subprocess):
        """Test successful fleet status retrieval.

        Note: get_fleet_status() uses TmuxInterface (second implementation)
        which expects list-windows format "name active_flag", uses
        context_estimate=len(output), and agent.name is "session:window".
        """

        def tmux_side_effect(*args, **kwargs):
            cmd = args[0]
            if "list-sessions" in cmd:
                return MagicMock(returncode=0, stdout="forge\n")
            elif "list-windows" in cmd:
                return MagicMock(returncode=0, stdout="tech 1\ncontent 0\n")
            elif "capture-pane" in cmd:
                if "tech" in str(cmd):
                    return MagicMock(
                        returncode=0,
                        stdout="[INSERT] working on code\n",
                    )
                elif "content" in str(cmd):
                    return MagicMock(
                        returncode=0,
                        stdout="$ idle waiting\n",
                    )
            return MagicMock(returncode=0, stdout="")

        mock_subprocess.side_effect = tmux_side_effect

        status = get_fleet_status()

        assert status.overall_health == "healthy"
        assert status.total_active == 1  # tech is active ("working" pattern)
        assert status.total_idle == 1  # content is idle ("idle" pattern)
        assert status.total_stale == 0
        assert len(status.agents) == 2

        # Check tech agent (name is "session:window" in TmuxInterface impl)
        tech = next(a for a in status.agents if "tech" in a.name)
        assert tech.status == "active"
        assert tech.context_estimate > 0  # len(output), not parsed percentage

        # Check content agent
        content = next(a for a in status.agents if "content" in a.name)
        assert content.status == "idle"
        assert content.context_estimate > 0

    def test_get_fleet_status_no_forge_session(self, mock_subprocess):
        """Test fleet status when forge session doesn't exist."""
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="",  # No sessions
        )

        status = get_fleet_status()

        assert status.overall_health == "critical"
        assert status.total_active == 0
        assert status.total_idle == 0
        assert status.total_stale == 0
        assert len(status.agents) == 0

    def test_get_fleet_status_capture_failure(self, mock_subprocess):
        """Test fleet status with individual window capture failures.

        Note: TmuxInterface implementation includes all windows even
        when capture returns empty output (agent gets status='unknown').
        """

        def tmux_side_effect(*args, **kwargs):
            cmd = args[0]
            if "list-sessions" in cmd:
                return MagicMock(returncode=0, stdout="forge\n")
            elif "list-windows" in cmd:
                return MagicMock(returncode=0, stdout="tech 1\nbroken 0\n")
            elif "capture-pane" in cmd:
                if "tech" in str(cmd):
                    return MagicMock(
                        returncode=0,
                        stdout="[INSERT] working\n",
                    )
                elif "broken" in str(cmd):
                    # Simulate capture failure
                    return MagicMock(returncode=0, stdout="")
            return MagicMock(returncode=0, stdout="")

        mock_subprocess.side_effect = tmux_side_effect

        status = get_fleet_status()

        # TmuxInterface includes all windows, even with empty capture
        assert len(status.agents) == 2
        tech = next(a for a in status.agents if "tech" in a.name)
        assert tech.status == "active"
        broken = next(a for a in status.agents if "broken" in a.name)
        assert broken.status == "unknown"

    def test_get_fleet_status_tmux_not_installed(self, mock_subprocess):
        """Test fleet status when tmux is not installed."""
        mock_subprocess.side_effect = FileNotFoundError("tmux not found")

        status = get_fleet_status()

        assert status.overall_health == "critical"
        assert len(status.agents) == 0


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_empty_pane_content(self):
        """Test handling of empty pane content."""
        status = _detect_status("")
        assert status == "stale"

        context = _extract_context_percentage("")
        assert context == 5  # Minimum heuristic

    def test_malformed_context_string(self):
        """Test handling of malformed context strings."""
        content = "Context: invalid%"
        context = _extract_context_percentage(content)
        # Should fall back to heuristic
        assert context >= 0

    def test_future_timestamp_handling(self, mock_datetime):
        """Test handling of timestamps that appear in the future."""
        # This can happen if system clock changes or timezone issues
        now = mock_datetime.now.return_value
        future_time = now + timedelta(hours=2)

        content = f"[{future_time.strftime('%H:%M:%S')}] Some activity"
        last_activity = _estimate_last_activity(content, "idle")

        # Should handle gracefully, treat as yesterday
        assert last_activity <= now

    def test_very_long_output(self):
        """Test handling of very long pane output."""
        # Create 10000 lines of output
        content = "\n".join([f"Line {i}" for i in range(10000)])
        context = _extract_context_percentage(content)
        # Should cap at 100
        assert context <= 100

    def test_unicode_in_output(self):
        """Test handling of unicode characters in output."""
        content = """
[INSERT] 编辑代码 🚀
Context: 45%
✓ Tests passing
"""
        status = _detect_status(content)
        assert status == "active"

        context = _extract_context_percentage(content)
        assert context == 45
