"""Tests for progress monitor module."""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.iteration.monitor import (
    MonitorResult,
    ProgressMonitor,
    SessionStatus,
    create_monitor,
)


class TestMonitorResult:
    """Tests for MonitorResult dataclass."""

    def test_monitor_result_running(self):
        """MonitorResult with running status."""
        result = MonitorResult(
            session_id="forge:tech",
            status=SessionStatus.RUNNING,
            output="Working...",
            duration=30.0,
        )
        assert result.status == SessionStatus.RUNNING
        assert result.session_id == "forge:tech"

    def test_monitor_result_completed(self):
        """MonitorResult with completed status."""
        result = MonitorResult(
            session_id="forge:tech",
            status=SessionStatus.COMPLETED,
            output="Done.",
            duration=120.0,
            completion_marker="Done\\.",
        )
        assert result.status == SessionStatus.COMPLETED
        assert result.completion_marker is not None

    def test_monitor_result_summary(self):
        """Summary property returns formatted string."""
        result = MonitorResult(
            session_id="forge:tech",
            status=SessionStatus.COMPLETED,
            output="Task completed successfully",
            duration=60.0,
            completion_marker="Task completed",
        )
        summary = result.summary
        assert "forge:tech" in summary
        assert "completed" in summary.lower()
        assert "60.0s" in summary


class TestProgressMonitor:
    """Tests for ProgressMonitor class."""

    @pytest.fixture
    def monitor(self):
        """Create a test monitor."""
        return ProgressMonitor(poll_interval=1.0)

    @patch("subprocess.run")
    def test_poll_captures_output(self, mock_run, monitor):
        """poll_session captures tmux output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Working on task...\nProgress: 50 percent complete",
            stderr="",
        )

        result = monitor.poll_session("forge:tech")

        assert result.status == SessionStatus.RUNNING
        assert "Working on task" in result.output
        mock_run.assert_called_once()
        # Verify tmux capture-pane was called
        call_args = mock_run.call_args[0][0]
        assert "tmux" in call_args
        assert "capture-pane" in call_args

    @patch("subprocess.run")
    def test_detect_completion_cogitated(self, mock_run, monitor):
        """Detects completion via 'Cogitated for' marker."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Task done\nCogitated for 120 seconds",
            stderr="",
        )

        result = monitor.poll_session("forge:tech")

        assert result.status == SessionStatus.COMPLETED
        assert result.completion_marker is not None

    @patch("subprocess.run")
    def test_detect_completion_shell_prompt(self, mock_run, monitor):
        """Detects completion via shell prompt marker."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Task finished\n❯ ",
            stderr="",
        )

        result = monitor.poll_session("forge:tech")

        assert result.status == SessionStatus.COMPLETED

    @patch("subprocess.run")
    def test_detect_completion_done_marker(self, mock_run, monitor):
        """Detects completion via 'Completed' marker."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Processing...\nCompleted",
            stderr="",
        )

        result = monitor.poll_session("forge:tech")

        assert result.status == SessionStatus.COMPLETED

    @patch("subprocess.run")
    def test_stall_detection(self, mock_run, monitor):
        """detect_stall returns True when no output changes."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Same output",
            stderr="",
        )

        # First poll
        monitor.poll_session("forge:tech")

        # Simulate time passing with no change
        monitor._session_last_change["forge:tech"] = time.time() - 400

        is_stalled = monitor.detect_stall("forge:tech", threshold_seconds=300)

        assert is_stalled is True

    @patch("subprocess.run")
    def test_stall_detection_not_stalled(self, mock_run, monitor):
        """detect_stall returns False when output still changing."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Still working...",
            stderr="",
        )

        # First poll
        monitor.poll_session("forge:tech")

        is_stalled = monitor.detect_stall("forge:tech", threshold_seconds=300)

        assert is_stalled is False

    @patch("subprocess.run")
    def test_error_handling(self, mock_run, monitor):
        """Handles tmux errors gracefully."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="session not found",
        )

        result = monitor.poll_session("forge:nonexistent")

        assert result.status == SessionStatus.ERROR

    @patch("subprocess.run")
    def test_timeout_handling(self, mock_run, monitor):
        """wait_for_completion handles timeout."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Still running...",
            stderr="",
        )

        # Very short timeout for testing
        async def run_test():
            results = await monitor.wait_for_completion(
                ["forge:tech"],
                timeout=0.1,  # Very short timeout
                stall_threshold=1000,  # Don't trigger stall
            )
            return results

        results = asyncio.run(run_test())

        assert "forge:tech" in results
        assert results["forge:tech"].status == SessionStatus.TIMEOUT

    @patch("subprocess.run")
    def test_session_prefix_handling(self, mock_run, monitor):
        """Session ID without prefix gets prefix added."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="output",
            stderr="",
        )

        monitor.poll_session("tech")  # Without prefix

        # Verify full target was used
        call_args = mock_run.call_args[0][0]
        assert "forge:tech" in call_args

    def test_reset_session(self, monitor):
        """reset_session clears tracking state."""
        monitor._session_start_times["forge:tech"] = time.time()
        monitor._session_last_output["forge:tech"] = "output"
        monitor._session_last_change["forge:tech"] = time.time()

        monitor.reset_session("forge:tech")

        assert "forge:tech" not in monitor._session_start_times
        assert "forge:tech" not in monitor._session_last_output
        assert "forge:tech" not in monitor._session_last_change

    def test_reset_all(self, monitor):
        """reset_all clears all tracking state."""
        monitor._session_start_times["forge:tech"] = time.time()
        monitor._session_start_times["forge:cursor"] = time.time()

        monitor.reset_all()

        assert len(monitor._session_start_times) == 0


class TestCreateMonitor:
    """Tests for create_monitor factory function."""

    def test_create_monitor_default(self):
        """create_monitor returns ProgressMonitor with defaults."""
        monitor = create_monitor()
        assert isinstance(monitor, ProgressMonitor)
        assert monitor.poll_interval == 30.0

    def test_create_monitor_custom_interval(self):
        """create_monitor accepts custom poll interval."""
        monitor = create_monitor(poll_interval=10.0)
        assert monitor.poll_interval == 10.0


class TestWaitForCompletion:
    """Tests for async wait_for_completion method."""

    @patch("subprocess.run")
    def test_wait_multiple_sessions(self, mock_run):
        """wait_for_completion handles multiple sessions."""
        call_count = 0

        def mock_capture(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First few calls: running, then completed
            if call_count <= 2:
                return MagicMock(returncode=0, stdout="Working...", stderr="")
            return MagicMock(returncode=0, stdout="Completed", stderr="")

        mock_run.side_effect = mock_capture

        monitor = ProgressMonitor(poll_interval=0.1)

        async def run_test():
            results = await monitor.wait_for_completion(
                ["forge:tech", "forge:cursor"],
                timeout=5.0,
            )
            return results

        results = asyncio.run(run_test())

        assert len(results) == 2
        # At least one should be completed
        statuses = [r.status for r in results.values()]
        assert SessionStatus.COMPLETED in statuses or SessionStatus.TIMEOUT in statuses


class TestSessionStatus:
    """Tests for SessionStatus enum."""

    def test_session_status_values(self):
        """SessionStatus has correct values."""
        assert SessionStatus.RUNNING.value == "running"
        assert SessionStatus.COMPLETED.value == "completed"
        assert SessionStatus.STALLED.value == "stalled"
        assert SessionStatus.TIMEOUT.value == "timeout"
        assert SessionStatus.ERROR.value == "error"

    def test_session_status_string_conversion(self):
        """SessionStatus converts to string correctly."""
        assert str(SessionStatus.RUNNING) == "running"
        assert str(SessionStatus.COMPLETED) == "completed"


class TestCompletionDetection:
    """Tests for completion marker detection."""

    @pytest.fixture
    def monitor(self):
        """Create a test monitor."""
        return ProgressMonitor()

    def test_detect_completion_markers(self, monitor):
        """Test detection of various completion markers."""
        test_cases = [
            ("Cogitated for 45 seconds", True),
            ("Task Completed", True),
            ("✓ Tests completed", True),
            ("Done.", True),
            ("Task completed successfully", True),
            ("Output ending with ❯ ", True),
            ("Python prompt >>> ", True),
            ("Shell prompt $ ", True),
            ("Still working...", False),
            ("Processing...", False),
        ]

        for output, should_detect in test_cases:
            marker = monitor._detect_completion(output)
            if should_detect:
                assert marker is not None, f"Should detect completion in: {output}"
            else:
                assert marker is None, f"Should not detect completion in: {output}"


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    @pytest.fixture
    def monitor(self):
        """Create a test monitor."""
        return ProgressMonitor(poll_interval=0.1)

    @patch("subprocess.run")
    def test_empty_output_handling(self, mock_run, monitor):
        """Handles empty output gracefully."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )

        result = monitor.poll_session("forge:tech")

        assert result.status == SessionStatus.RUNNING
        assert result.output == ""

    @patch("subprocess.run")
    def test_subprocess_timeout(self, mock_run, monitor):
        """Handles subprocess timeout."""
        from subprocess import TimeoutExpired

        mock_run.side_effect = TimeoutExpired("tmux", 10)

        result = monitor.poll_session("forge:tech")

        assert result.status == SessionStatus.ERROR

    @patch("subprocess.run")
    def test_file_not_found_error(self, mock_run, monitor):
        """Handles tmux not found error."""
        mock_run.side_effect = FileNotFoundError("tmux not found")

        result = monitor.poll_session("forge:tech")

        assert result.status == SessionStatus.ERROR

    @patch("subprocess.run")
    def test_multiple_completion_markers(self, mock_run, monitor):
        """Detects first matching completion marker."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Task completed successfully\nCogitated for 60 seconds\n❯ ",
            stderr="",
        )

        result = monitor.poll_session("forge:tech")

        assert result.status == SessionStatus.COMPLETED
        assert result.completion_marker is not None

    @patch("subprocess.run")
    def test_stall_on_first_poll(self, mock_run, monitor):
        """detect_stall returns False on first poll."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Output",
            stderr="",
        )

        # First call to detect_stall
        is_stalled = monitor.detect_stall("forge:tech", threshold_seconds=1)

        assert is_stalled is False

    @patch("subprocess.run")
    def test_duration_tracking(self, mock_run, monitor):
        """Tracks duration correctly across polls."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Working...",
            stderr="",
        )

        # First poll
        result1 = monitor.poll_session("forge:tech")
        assert result1.duration >= 0

        # Wait a bit
        time.sleep(0.1)

        # Second poll
        result2 = monitor.poll_session("forge:tech")
        assert result2.duration > result1.duration

    @patch("subprocess.run")
    def test_last_activity_timestamp(self, mock_run, monitor):
        """Tracks last activity timestamp."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Output 1",
            stderr="",
        )

        # First poll
        result1 = monitor.poll_session("forge:tech")
        assert result1.last_activity is not None

        # Change output
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Output 2",
            stderr="",
        )

        # Second poll - should update last_activity
        result2 = monitor.poll_session("forge:tech")
        assert result2.last_activity is not None
        assert result2.last_activity >= result1.last_activity
