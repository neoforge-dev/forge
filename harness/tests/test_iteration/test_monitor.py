"""Tests for forge_harness.iteration.monitor module."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from forge_harness.iteration.monitor import (
    COMPLETION_MARKERS,
    FAILURE_MARKERS,
    ProgressMonitor,
    SessionMetrics,
    SessionState,
)


class TestSessionState:
    """Test SessionState enum."""

    def test_session_states(self):
        """Test all session states are defined."""
        assert SessionState.ACTIVE == "active"
        assert SessionState.STALLED == "stalled"
        assert SessionState.COMPLETED == "completed"
        assert SessionState.FAILED == "failed"
        assert SessionState.TIMEOUT == "timeout"
        assert SessionState.UNKNOWN == "unknown"


class TestSessionMetrics:
    """Test SessionMetrics dataclass."""

    def test_age_minutes(self):
        """Test age calculation."""
        # Create metrics with known start time
        past = datetime.now(UTC) - timedelta(minutes=10)
        metrics = SessionMetrics(session_id="test", started_at=past)

        # Age should be approximately 10 minutes
        assert 9.5 <= metrics.age_minutes <= 10.5

    def test_idle_minutes_no_activity(self):
        """Test idle time when no activity recorded."""
        past = datetime.now(UTC) - timedelta(minutes=5)
        metrics = SessionMetrics(session_id="test", started_at=past)

        # Idle should equal age when no activity (with small tolerance for timing)
        assert abs(metrics.idle_minutes - metrics.age_minutes) < 0.001

    def test_idle_minutes_with_activity(self):
        """Test idle time with recorded activity."""
        past = datetime.now(UTC) - timedelta(minutes=10)
        recent = datetime.now(UTC) - timedelta(minutes=2)
        metrics = SessionMetrics(session_id="test", started_at=past, last_activity_at=recent)

        # Idle should be ~2 minutes
        assert 1.5 <= metrics.idle_minutes <= 2.5

    def test_is_timeout_normal(self):
        """Test timeout detection."""
        past = datetime.now(UTC) - timedelta(minutes=20)
        metrics = SessionMetrics(
            session_id="test",
            started_at=past,
            last_activity_at=past,
            timeout_minutes=15,
        )

        # Should timeout after 15min idle
        assert metrics.is_timeout is True

    def test_is_timeout_not_reached(self):
        """Test no timeout when still active."""
        recent = datetime.now(UTC) - timedelta(minutes=5)
        metrics = SessionMetrics(
            session_id="test",
            started_at=recent,
            last_activity_at=recent,
            timeout_minutes=15,
        )

        assert metrics.is_timeout is False

    def test_is_timeout_extended(self):
        """Test timeout with extension."""
        past = datetime.now(UTC) - timedelta(minutes=20)
        future = datetime.now(UTC) + timedelta(minutes=10)
        metrics = SessionMetrics(
            session_id="test",
            started_at=past,
            last_activity_at=past,
            timeout_minutes=15,
            extended_until=future,
        )

        # Should not timeout yet due to extension
        assert metrics.is_timeout is False

    def test_is_timeout_extension_expired(self):
        """Test timeout after extension expires."""
        past = datetime.now(UTC) - timedelta(minutes=30)
        expired = datetime.now(UTC) - timedelta(minutes=5)
        metrics = SessionMetrics(
            session_id="test",
            started_at=past,
            last_activity_at=past,
            timeout_minutes=15,
            extended_until=expired,
        )

        # Should timeout since extension expired
        assert metrics.is_timeout is True

    def test_to_dict(self):
        """Test conversion to dictionary."""
        now = datetime.now(UTC)
        metrics = SessionMetrics(
            session_id="test-session",
            task_id="HRN-001",
            state=SessionState.ACTIVE,
            started_at=now,
            last_activity_at=now,
            output_size=1024,
            timeout_minutes=15,
            metadata={"domain": "test"},
        )

        data = metrics.to_dict()
        assert data["session_id"] == "test-session"
        assert data["task_id"] == "HRN-001"
        assert data["state"] == "active"
        assert data["output_size"] == 1024
        assert data["timeout_minutes"] == 15
        assert "age_minutes" in data
        assert "idle_minutes" in data
        assert "is_timeout" in data
        assert data["metadata"] == {"domain": "test"}


class TestProgressMonitor:
    """Test ProgressMonitor class."""

    def test_init_defaults(self):
        """Test initialization with defaults."""
        monitor = ProgressMonitor()
        assert monitor.poll_interval == 30
        assert monitor.default_timeout == 15
        assert monitor.completion_markers == COMPLETION_MARKERS
        assert monitor.failure_markers == FAILURE_MARKERS
        assert len(monitor.sessions) == 0
        assert monitor._running is False

    def test_init_custom(self):
        """Test initialization with custom values."""
        custom_completion = ["DONE"]
        custom_failure = ["ERROR"]
        monitor = ProgressMonitor(
            poll_interval=60,
            default_timeout=30,
            completion_markers=custom_completion,
            failure_markers=custom_failure,
        )
        assert monitor.poll_interval == 60
        assert monitor.default_timeout == 30
        assert monitor.completion_markers == custom_completion
        assert monitor.failure_markers == custom_failure

    def test_register_session(self):
        """Test registering a session."""
        monitor = ProgressMonitor()
        monitor.register_session("test-session", task_id="HRN-001", timeout_minutes=20)

        assert "test-session" in monitor.sessions
        metrics = monitor.sessions["test-session"]
        assert metrics.session_id == "test-session"
        assert metrics.task_id == "HRN-001"
        assert metrics.timeout_minutes == 20

    def test_register_session_with_metadata(self):
        """Test registering a session with metadata."""
        monitor = ProgressMonitor()
        metadata = {"domain": "voice-coach", "type": "backend"}
        monitor.register_session("test-session", metadata=metadata)

        metrics = monitor.sessions["test-session"]
        assert metrics.metadata == metadata

    def test_unregister_session(self):
        """Test unregistering a session."""
        monitor = ProgressMonitor()
        monitor.register_session("test-session")
        assert "test-session" in monitor.sessions

        monitor.unregister_session("test-session")
        assert "test-session" not in monitor.sessions

    def test_unregister_nonexistent(self):
        """Test unregistering a nonexistent session."""
        monitor = ProgressMonitor()
        # Should not raise
        monitor.unregister_session("nonexistent")

    def test_is_completed(self):
        """Test checking if session is completed."""
        monitor = ProgressMonitor()
        monitor.register_session("test-session")

        # Not completed initially
        assert monitor.is_completed("test-session") is False

        # Mark as completed
        monitor.sessions["test-session"].state = SessionState.COMPLETED
        assert monitor.is_completed("test-session") is True

    def test_is_completed_nonexistent(self):
        """Test checking completion of nonexistent session."""
        monitor = ProgressMonitor()
        assert monitor.is_completed("nonexistent") is False

    def test_get_state(self):
        """Test getting session state."""
        monitor = ProgressMonitor()
        monitor.register_session("test-session")
        monitor.sessions["test-session"].state = SessionState.ACTIVE

        assert monitor.get_state("test-session") == SessionState.ACTIVE

    def test_get_state_nonexistent(self):
        """Test getting state of nonexistent session."""
        monitor = ProgressMonitor()
        assert monitor.get_state("nonexistent") is None

    def test_get_metrics(self):
        """Test getting session metrics."""
        monitor = ProgressMonitor()
        monitor.register_session("test-session", task_id="HRN-001")

        metrics = monitor.get_metrics("test-session")
        assert metrics is not None
        assert metrics.session_id == "test-session"
        assert metrics.task_id == "HRN-001"

    def test_get_all_metrics(self):
        """Test getting all metrics."""
        monitor = ProgressMonitor()
        monitor.register_session("session-1")
        monitor.register_session("session-2")

        all_metrics = monitor.get_all_metrics()
        assert len(all_metrics) == 2
        assert "session-1" in all_metrics
        assert "session-2" in all_metrics

    def test_extend_timeout(self):
        """Test extending timeout."""
        monitor = ProgressMonitor()
        monitor.register_session("test-session")

        before = datetime.now(UTC)
        monitor.extend_timeout("test-session", minutes=30)
        after = datetime.now(UTC)

        metrics = monitor.sessions["test-session"]
        assert metrics.extended_until is not None
        # Should be ~30 minutes in the future
        expected = before + timedelta(minutes=30)
        assert metrics.extended_until >= expected
        assert metrics.extended_until <= after + timedelta(minutes=30)

    def test_extend_timeout_nonexistent(self):
        """Test extending timeout of nonexistent session."""
        monitor = ProgressMonitor()
        # Should not raise
        monitor.extend_timeout("nonexistent", minutes=30)

    def test_collect_output(self):
        """Test collecting output."""
        monitor = ProgressMonitor()
        expected_output = "test output content"

        with patch.object(monitor, "_get_session_output", return_value=expected_output):
            output = monitor.collect_output("test-session")
            assert output == expected_output

    def test_collect_output_with_tail(self):
        """Test collecting output with tail limit."""
        monitor = ProgressMonitor()

        with patch.object(monitor, "_get_session_output") as mock_get:
            monitor.collect_output("test-session", tail_lines=100)
            mock_get.assert_called_once_with("test-session", 100)

    @patch("subprocess.run")
    def test_session_exists_true(self, mock_run):
        """Test session exists check when session exists."""
        mock_run.return_value = Mock(returncode=0)
        monitor = ProgressMonitor()

        assert monitor._session_exists("test-session") is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args == ["tmux", "has-session", "-t", "test-session"]

    @patch("subprocess.run")
    def test_session_exists_false(self, mock_run):
        """Test session exists check when session doesn't exist."""
        mock_run.return_value = Mock(returncode=1)
        monitor = ProgressMonitor()

        assert monitor._session_exists("test-session") is False

    @patch("subprocess.run")
    def test_get_session_output(self, mock_run):
        """Test getting session output."""
        expected = "line 1\nline 2\nline 3"
        mock_run.return_value = Mock(stdout=expected, returncode=0)
        monitor = ProgressMonitor()

        output = monitor._get_session_output("test-session")
        assert output == expected

    @patch("subprocess.run")
    def test_get_session_output_with_tail(self, mock_run):
        """Test getting session output with tail lines."""
        mock_run.return_value = Mock(stdout="output", returncode=0)
        monitor = ProgressMonitor()

        monitor._get_session_output("test-session", tail_lines=100)
        args = mock_run.call_args[0][0]
        assert "-S" in args
        assert "-100" in args

    @patch("subprocess.run")
    def test_get_exit_status_running(self, mock_run):
        """Test getting exit status of running pane."""
        # Pane is not dead
        mock_run.return_value = Mock(stdout="0\n", returncode=0)
        monitor = ProgressMonitor()

        status = monitor._get_exit_status("test-session")
        assert status is None

    @patch("subprocess.run")
    def test_get_exit_status_exited(self, mock_run):
        """Test getting exit status of exited pane."""
        monitor = ProgressMonitor()

        # First call: pane_dead = 1
        # Second call: pane_dead_status = 0
        mock_run.side_effect = [
            Mock(stdout="1\n", returncode=0),  # pane_dead
            Mock(stdout="0\n", returncode=0),  # pane_dead_status
        ]

        status = monitor._get_exit_status("test-session")
        assert status == 0

    def test_hash_output(self):
        """Test output hashing."""
        monitor = ProgressMonitor()

        hash1 = monitor._hash_output("test content")
        hash2 = monitor._hash_output("test content")
        hash3 = monitor._hash_output("different content")

        # Same content should produce same hash
        assert hash1 == hash2
        # Different content should produce different hash
        assert hash1 != hash3

    def test_detect_completion_exit_status(self):
        """Test completion detection via exit status."""
        monitor = ProgressMonitor()

        with patch.object(monitor, "_get_exit_status", return_value=0):
            assert monitor.detect_completion("test-session") is True

    def test_detect_completion_marker(self):
        """Test completion detection via marker in output."""
        monitor = ProgressMonitor()
        output = "Running tests...\nAll tests pass\nDone."

        with patch.object(monitor, "_get_exit_status", return_value=None):
            assert monitor.detect_completion("test-session", output) is True

    def test_detect_completion_no_marker(self):
        """Test completion detection when no markers present."""
        monitor = ProgressMonitor()
        output = "Running tests...\nStill working..."

        with patch.object(monitor, "_get_exit_status", return_value=None):
            assert monitor.detect_completion("test-session", output) is False

    def test_detect_completion_custom_marker(self):
        """Test completion detection with custom marker."""
        monitor = ProgressMonitor(completion_markers=["CUSTOM_DONE"])
        output = "Process running\nCUSTOM_DONE\n"

        with patch.object(monitor, "_get_exit_status", return_value=None):
            assert monitor.detect_completion("test-session", output) is True

    def test_detect_stall_not_stalled(self):
        """Test stall detection when session is active."""
        monitor = ProgressMonitor()
        monitor.register_session("test-session")

        # Recent activity, should not be stalled
        assert monitor.detect_stall("test-session") is False

    def test_detect_stall_stalled(self):
        """Test stall detection when session is stalled."""
        monitor = ProgressMonitor()
        monitor.register_session("test-session")

        # Set last activity to 12 minutes ago (stall threshold is 10min)
        past = datetime.now(UTC) - timedelta(minutes=12)
        monitor.sessions["test-session"].last_activity_at = past

        assert monitor.detect_stall("test-session") is True

    def test_detect_stall_nonexistent(self):
        """Test stall detection for nonexistent session."""
        monitor = ProgressMonitor()
        assert monitor.detect_stall("nonexistent") is False

    def test_detect_failure(self):
        """Test failure detection in output."""
        monitor = ProgressMonitor()
        output = "Running command...\nError: something went wrong\n"

        assert monitor._detect_failure(output) is True

    def test_detect_failure_no_marker(self):
        """Test failure detection when no markers present."""
        monitor = ProgressMonitor()
        output = "Running command...\nSuccess!\n"

        assert monitor._detect_failure(output) is False

    @pytest.mark.asyncio
    async def test_poll_single_session_active(self):
        """Test polling a single active session."""
        monitor = ProgressMonitor()
        monitor.register_session("test-session")
        metrics = monitor.sessions["test-session"]

        with (
            patch.object(monitor, "_session_exists", return_value=True),
            patch.object(monitor, "_get_session_output", return_value="active output"),
            patch.object(monitor, "detect_completion", return_value=False),
            patch.object(monitor, "_detect_failure", return_value=False),
        ):
            state = await monitor._poll_single_session("test-session", metrics)
            assert state == SessionState.ACTIVE

    @pytest.mark.asyncio
    async def test_poll_single_session_completed(self):
        """Test polling a completed session."""
        monitor = ProgressMonitor()
        monitor.register_session("test-session")
        metrics = monitor.sessions["test-session"]

        with (
            patch.object(monitor, "_session_exists", return_value=True),
            patch.object(monitor, "_get_session_output", return_value="Task completed"),
            patch.object(monitor, "detect_completion", return_value=True),
        ):
            state = await monitor._poll_single_session("test-session", metrics)
            assert state == SessionState.COMPLETED

    @pytest.mark.asyncio
    async def test_poll_single_session_failed(self):
        """Test polling a failed session."""
        monitor = ProgressMonitor()
        monitor.register_session("test-session")
        metrics = monitor.sessions["test-session"]

        with (
            patch.object(monitor, "_session_exists", return_value=True),
            patch.object(monitor, "_get_session_output", return_value="Error: failed"),
            patch.object(monitor, "detect_completion", return_value=False),
            patch.object(monitor, "_detect_failure", return_value=True),
        ):
            state = await monitor._poll_single_session("test-session", metrics)
            assert state == SessionState.FAILED

    @pytest.mark.asyncio
    async def test_poll_single_session_timeout(self):
        """Test polling a timed out session."""
        monitor = ProgressMonitor()
        monitor.register_session("test-session")
        metrics = monitor.sessions["test-session"]

        # Set last activity to trigger timeout
        past = datetime.now(UTC) - timedelta(minutes=20)
        metrics.last_activity_at = past

        with (
            patch.object(monitor, "_session_exists", return_value=True),
            patch.object(monitor, "_get_session_output", return_value="old output"),
            patch.object(monitor, "detect_completion", return_value=False),
            patch.object(monitor, "_detect_failure", return_value=False),
        ):
            # Use same hash to simulate no new output
            old_hash = monitor._hash_output("old output")
            metrics.last_output_hash = old_hash

            state = await monitor._poll_single_session("test-session", metrics)
            assert state == SessionState.TIMEOUT

    @pytest.mark.asyncio
    async def test_poll_single_session_not_exists(self):
        """Test polling a session that no longer exists."""
        monitor = ProgressMonitor()
        monitor.register_session("test-session")
        metrics = monitor.sessions["test-session"]

        with patch.object(monitor, "_session_exists", return_value=False):
            state = await monitor._poll_single_session("test-session", metrics)
            assert state == SessionState.FAILED

    @pytest.mark.asyncio
    async def test_poll_sessions(self):
        """Test polling all registered sessions."""
        monitor = ProgressMonitor()
        monitor.register_session("session-1")
        monitor.register_session("session-2")

        with patch.object(
            monitor, "_poll_single_session", return_value=SessionState.ACTIVE
        ) as mock_poll:
            results = await monitor.poll_sessions()

            assert len(results) == 2
            assert results["session-1"] == SessionState.ACTIVE
            assert results["session-2"] == SessionState.ACTIVE
            assert mock_poll.call_count == 2

    @pytest.mark.asyncio
    async def test_poll_sessions_with_error(self):
        """Test polling sessions with error handling."""
        monitor = ProgressMonitor()
        monitor.register_session("session-1")
        monitor.register_session("session-2")

        # First session errors, second succeeds
        async def mock_poll(sid, metrics):
            if sid == "session-1":
                raise RuntimeError("Test error")
            return SessionState.ACTIVE

        with patch.object(monitor, "_poll_single_session", side_effect=mock_poll):
            results = await monitor.poll_sessions()

            assert results["session-1"] == SessionState.UNKNOWN
            assert results["session-2"] == SessionState.ACTIVE

    @pytest.mark.asyncio
    async def test_run_with_max_iterations(self):
        """Test running monitor with max iterations."""
        monitor = ProgressMonitor(poll_interval=0.1)
        monitor.register_session("test-session")

        with patch.object(monitor, "poll_sessions", return_value={}) as mock_poll:
            await monitor.run(max_iterations=3)

            assert mock_poll.call_count == 3
            assert monitor._running is False

    @pytest.mark.asyncio
    async def test_run_until_stopped(self):
        """Test running monitor until stopped."""
        monitor = ProgressMonitor(poll_interval=0.1)

        async def stop_after_delay():
            await asyncio.sleep(0.3)
            monitor.stop()

        with patch.object(monitor, "poll_sessions", return_value={}):
            # Start monitor and stop task concurrently
            await asyncio.gather(monitor.run(), stop_after_delay())

            assert monitor._running is False

    def test_stop(self):
        """Test stopping the monitor."""
        monitor = ProgressMonitor()
        monitor._running = True

        monitor.stop()
        assert monitor._running is False


class TestIntegration:
    """Integration tests for ProgressMonitor."""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self):
        """Test full lifecycle: register, poll, detect completion, collect output."""
        monitor = ProgressMonitor()

        # Register session
        monitor.register_session("test-session", task_id="HRN-001", timeout_minutes=15)

        # Mock session as running with output
        with (
            patch.object(monitor, "_session_exists", return_value=True),
            patch.object(monitor, "_get_session_output", return_value="Task in progress..."),
            patch.object(monitor, "_get_exit_status", return_value=None),
        ):
            # First poll - should be active
            results = await monitor.poll_sessions()
            assert results["test-session"] == SessionState.ACTIVE

        # Mock session as completed
        with (
            patch.object(monitor, "_session_exists", return_value=True),
            patch.object(monitor, "_get_session_output", return_value="Task completed"),
            patch.object(monitor, "_get_exit_status", return_value=0),
        ):
            # Second poll - should be completed
            results = await monitor.poll_sessions()
            assert results["test-session"] == SessionState.COMPLETED

        # Collect output
        with patch.object(monitor, "_get_session_output", return_value="Task completed"):
            output = monitor.collect_output("test-session")
            assert "completed" in output

        # Verify state
        assert monitor.is_completed("test-session") is True
        metrics = monitor.get_metrics("test-session")
        assert metrics.state == SessionState.COMPLETED

    @pytest.mark.asyncio
    async def test_timeout_detection(self):
        """Test timeout detection integration."""
        monitor = ProgressMonitor(default_timeout=1)  # 1 minute timeout
        monitor.register_session("test-session")

        # Set initial activity
        past = datetime.now(UTC) - timedelta(minutes=2)
        monitor.sessions["test-session"].last_activity_at = past
        monitor.sessions["test-session"].last_output_hash = "initial"

        # Mock no new output
        with (
            patch.object(monitor, "_session_exists", return_value=True),
            patch.object(monitor, "_get_session_output", return_value="same output"),
            patch.object(monitor, "_get_exit_status", return_value=None),
        ):
            # Hash will be same, so no activity detected
            monitor.sessions["test-session"].last_output_hash = monitor._hash_output("same output")

            results = await monitor.poll_sessions()
            assert results["test-session"] == SessionState.TIMEOUT

    @pytest.mark.asyncio
    async def test_extend_timeout_integration(self):
        """Test timeout extension integration."""
        monitor = ProgressMonitor(default_timeout=1)  # 1 minute timeout
        monitor.register_session("test-session")

        # Set old activity
        past = datetime.now(UTC) - timedelta(minutes=2)
        monitor.sessions["test-session"].last_activity_at = past

        # Extend timeout
        monitor.extend_timeout("test-session", minutes=30)

        # Mock no new output
        with (
            patch.object(monitor, "_session_exists", return_value=True),
            patch.object(monitor, "_get_session_output", return_value="same output"),
            patch.object(monitor, "_get_exit_status", return_value=None),
        ):
            # Set hash to simulate no change
            hash_val = monitor._hash_output("same output")
            monitor.sessions["test-session"].last_output_hash = hash_val

            results = await monitor.poll_sessions()
            # Should not timeout due to extension
            assert results["test-session"] == SessionState.ACTIVE
