"""Progress Monitor for FORGE Harness.

Tracks multi-agent progress with timeout detection and automatic retry.
Polls agent sessions every 30s and detects completion or stall conditions.

Features:
- Polls tmux sessions every 30s for output changes
- Detects task completion via exit status or markers
- Triggers timeout after 15 minutes of no progress
- Collects final output and result status
- Supports manual override to extend timeout

Usage:
    from forge_harness.iteration.monitor import ProgressMonitor, SessionState

    monitor = ProgressMonitor()

    # Start monitoring a session
    monitor.register_session("task-session", task_id="HRN-001")

    # Poll all sessions
    await monitor.poll_sessions()

    # Check completion
    if monitor.is_completed("task-session"):
        output = monitor.collect_output("task-session")
        print(f"Task completed: {output}")

    # Extend timeout for long-running task
    monitor.extend_timeout("task-session", minutes=30)

CLI:
    python -m forge_harness.iteration.monitor --watch
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class SessionState(str, Enum):
    """State of a monitored session."""

    ACTIVE = "active"  # Session running with progress
    STALLED = "stalled"  # No progress for timeout period
    COMPLETED = "completed"  # Task completed successfully
    FAILED = "failed"  # Task failed or crashed
    TIMEOUT = "timeout"  # No progress for extended period
    UNKNOWN = "unknown"  # Unable to determine state


class SessionStatus(str, Enum):
    """Compatibility status for tests."""

    RUNNING = "running"
    COMPLETED = "completed"
    STALLED = "stalled"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class MonitorResult:
    """Result of a single monitor poll."""

    session_id: str
    status: SessionStatus
    output: str = ""
    duration: float = 0.0
    completion_marker: str | None = None

    @property
    def summary(self) -> str:
        return f"{self.session_id}: {self.status.value} ({self.duration:.1f}s)"


# Completion markers that indicate task success
COMPLETION_MARKERS = [
    "Task completed",
    "All tests pass",
    "✓ Success",
    "✅",
    "DONE",
    "Build successful",
    "Deployment complete",
    "Tests passed",
]

# Failure markers that indicate task failure
FAILURE_MARKERS = [
    "Error:",
    "FAILED",
    "❌",
    "Exception:",
    "Traceback",
    "BUILD FAILED",
    "Tests failed",
    "fatal:",
]


@dataclass
class SessionMetrics:
    """Metrics for a monitored session."""

    session_id: str
    task_id: str | None = None
    state: SessionState = SessionState.UNKNOWN
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_activity_at: datetime | None = None
    last_output_hash: str | None = None
    output_size: int = 0
    timeout_minutes: int = 15
    extended_until: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def age_minutes(self) -> float:
        """Age of session in minutes."""
        now = datetime.now(UTC)
        return (now - self.started_at).total_seconds() / 60

    @property
    def idle_minutes(self) -> float:
        """Minutes since last activity."""
        if not self.last_activity_at:
            return self.age_minutes
        now = datetime.now(UTC)
        return (now - self.last_activity_at).total_seconds() / 60

    @property
    def is_timeout(self) -> bool:
        """Check if session has timed out."""
        # Check if extended timeout is active
        if self.extended_until:
            now = datetime.now(UTC)
            if now < self.extended_until:
                return False
            # Extended timeout expired, check idle time
            return self.idle_minutes >= self.timeout_minutes
        # Normal timeout check
        return self.idle_minutes >= self.timeout_minutes

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "state": self.state.value,
            "started_at": self.started_at.isoformat(),
            "last_activity_at": self.last_activity_at.isoformat()
            if self.last_activity_at
            else None,
            "output_size": self.output_size,
            "timeout_minutes": self.timeout_minutes,
            "age_minutes": self.age_minutes,
            "idle_minutes": self.idle_minutes,
            "is_timeout": self.is_timeout,
            "extended_until": self.extended_until.isoformat() if self.extended_until else None,
            "metadata": self.metadata,
        }


class ProgressMonitor:
    """Monitor progress of multi-agent tasks in tmux sessions."""

    def __init__(
        self,
        poll_interval: int = 30,
        default_timeout: int = 15,
        completion_markers: list[str] | None = None,
        failure_markers: list[str] | None = None,
    ):
        """Initialize progress monitor.

        Args:
            poll_interval: Seconds between polls (default: 30)
            default_timeout: Minutes before timeout (default: 15)
            completion_markers: Custom completion markers
            failure_markers: Custom failure markers
        """
        self.poll_interval = poll_interval
        self.default_timeout = default_timeout
        self.completion_markers = completion_markers or COMPLETION_MARKERS
        self.failure_markers = failure_markers or FAILURE_MARKERS
        self.sessions: dict[str, SessionMetrics] = {}
        self._running = False
        # Compatibility tracking for tests
        self._session_last_output: dict[str, str] = {}
        self._session_last_change: dict[str, float] = {}

    def register_session(
        self,
        session_id: str,
        task_id: str | None = None,
        timeout_minutes: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a session for monitoring.

        Args:
            session_id: Tmux session identifier
            task_id: Optional task ID (e.g., "HRN-001")
            timeout_minutes: Override default timeout
            metadata: Additional metadata to track
        """
        timeout = timeout_minutes or self.default_timeout
        self.sessions[session_id] = SessionMetrics(
            session_id=session_id,
            task_id=task_id,
            timeout_minutes=timeout,
            metadata=metadata or {},
        )
        logger.info(f"Registered session {session_id} with {timeout}min timeout")

    def unregister_session(self, session_id: str) -> None:
        """Stop monitoring a session.

        Args:
            session_id: Tmux session identifier
        """
        if session_id in self.sessions:
            del self.sessions[session_id]
            logger.info(f"Unregistered session {session_id}")

    def poll_session(self, session_id: str) -> MonitorResult:
        """Poll a session output and return MonitorResult."""
        start_time = time.time()
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session_id, "-p"],
            capture_output=True,
            text=True,
            check=False,
        )
        duration = time.time() - start_time

        if result.returncode != 0:
            return MonitorResult(
                session_id=session_id,
                status=SessionStatus.ERROR,
                output=result.stderr,
                duration=duration,
            )

        output = result.stdout or ""
        completion_marker = None
        if re.search(r"Cogitated for", output):
            completion_marker = "Cogitated for"
        elif re.search(r"❯\\s*$", output, re.MULTILINE):
            completion_marker = "shell_prompt"
        elif re.search(r"Completed", output):
            completion_marker = "Completed"
        elif re.search(r"Task completed", output):
            completion_marker = "Task completed"

        # Track output changes
        previous = self._session_last_output.get(session_id)
        if previous != output:
            self._session_last_output[session_id] = output
            self._session_last_change[session_id] = time.time()

        if completion_marker:
            return MonitorResult(
                session_id=session_id,
                status=SessionStatus.COMPLETED,
                output=output,
                duration=duration,
                completion_marker=completion_marker,
            )

        return MonitorResult(
            session_id=session_id, status=SessionStatus.RUNNING, output=output, duration=duration
        )

    def detect_stall(self, session_id: str, threshold_seconds: int | None = None) -> bool:
        """Detect whether a session has stalled.

        When the session is registered and ``threshold_seconds`` is not
        provided, uses session-metrics-based detection (idle_minutes >= 10
        but not yet timed out).

        When ``threshold_seconds`` is provided, or when the session is not
        registered, uses time-based detection against the last recorded
        output change (default threshold: 300 seconds).

        Args:
            session_id: Tmux session identifier
            threshold_seconds: If set, forces time-based detection with
                this threshold in seconds.

        Returns:
            True if session is stalled
        """
        if threshold_seconds is not None:
            # Explicit time-based detection using _session_last_change
            last_change = self._session_last_change.get(session_id, time.time())
            return (time.time() - last_change) > threshold_seconds

        # If session is registered, use metrics-based detection
        metrics = self.sessions.get(session_id)
        if metrics is not None:
            # Consider stalled if idle for >10 minutes but <timeout
            return metrics.idle_minutes >= 10 and not metrics.is_timeout

        # Session not registered — fall back to time-based with 300s default
        last_change = self._session_last_change.get(session_id, time.time())
        return (time.time() - last_change) > 300

    async def wait_for_completion(
        self,
        session_ids: list[str],
        timeout: float = 900.0,
        stall_threshold: int = 300,
    ) -> dict[str, MonitorResult]:
        """Wait for sessions to complete or timeout."""
        start = time.time()
        results: dict[str, MonitorResult] = {}
        while True:
            for session_id in session_ids:
                result = self.poll_session(session_id)
                if result.status == SessionStatus.COMPLETED:
                    results[session_id] = result
                elif self.detect_stall(session_id, threshold_seconds=stall_threshold):
                    results[session_id] = MonitorResult(
                        session_id=session_id,
                        status=SessionStatus.STALLED,
                        output=result.output,
                        duration=result.duration,
                    )
            if len(results) == len(session_ids):
                return results
            if time.time() - start >= timeout:
                for session_id in session_ids:
                    if session_id not in results:
                        results[session_id] = MonitorResult(
                            session_id=session_id,
                            status=SessionStatus.TIMEOUT,
                            output="",
                            duration=time.time() - start,
                        )
                return results
            await asyncio.sleep(self.poll_interval)

    async def poll_sessions(self) -> dict[str, SessionState]:
        """Poll all registered sessions for changes.

        Returns:
            Dict mapping session_id to current state
        """
        results = {}
        for session_id, metrics in self.sessions.items():
            try:
                new_state = await self._poll_single_session(session_id, metrics)
                results[session_id] = new_state
            except Exception as e:
                logger.error(f"Error polling session {session_id}: {e}")
                results[session_id] = SessionState.UNKNOWN
        return results

    async def _poll_single_session(self, session_id: str, metrics: SessionMetrics) -> SessionState:
        """Poll a single session for changes.

        Args:
            session_id: Tmux session identifier
            metrics: Current session metrics

        Returns:
            Updated session state
        """
        # Check if session exists
        if not self._session_exists(session_id):
            logger.warning(f"Session {session_id} no longer exists")
            metrics.state = SessionState.FAILED
            return SessionState.FAILED

        # Get current output
        output = self._get_session_output(session_id)
        output_hash = self._hash_output(output)
        output_size = len(output)

        # Detect output changes
        if output_hash != metrics.last_output_hash:
            # Activity detected
            metrics.last_activity_at = datetime.now(UTC)
            metrics.last_output_hash = output_hash
            metrics.output_size = output_size
            logger.debug(f"Session {session_id}: activity detected ({output_size} chars)")

        # Check for completion markers
        if self.detect_completion(session_id, output):
            metrics.state = SessionState.COMPLETED
            logger.info(f"Session {session_id}: completed")
            return SessionState.COMPLETED

        # Check for failure markers
        if self._detect_failure(output):
            metrics.state = SessionState.FAILED
            logger.warning(f"Session {session_id}: failed")
            return SessionState.FAILED

        # Check for timeout
        if metrics.is_timeout:
            metrics.state = SessionState.TIMEOUT
            logger.warning(f"Session {session_id}: timeout after {metrics.idle_minutes:.1f}min")
            return SessionState.TIMEOUT

        # Check for stall (metrics-based, no threshold_seconds)
        if self.detect_stall(session_id):
            metrics.state = SessionState.STALLED
            logger.warning(f"Session {session_id}: stalled")
            return SessionState.STALLED

        # Session is active
        metrics.state = SessionState.ACTIVE
        return SessionState.ACTIVE

    def detect_completion(self, session_id: str, output: str | None = None) -> bool:
        """Detect if a session has completed successfully.

        Args:
            session_id: Tmux session identifier
            output: Optional output to check (fetched if not provided)

        Returns:
            True if session has completed
        """
        # Check if session has exited with success
        exit_status = self._get_exit_status(session_id)
        if exit_status is not None and exit_status == 0:
            return True

        # Check output for completion markers
        if output is None:
            output = self._get_session_output(session_id)

        # Look for completion markers in last 1000 chars
        tail = output[-1000:] if len(output) > 1000 else output
        for marker in self.completion_markers:
            if marker in tail:
                logger.debug(f"Found completion marker: {marker}")
                return True

        return False

    def _detect_failure(self, output: str) -> bool:
        """Detect failure markers in output.

        Args:
            output: Session output

        Returns:
            True if failure detected
        """
        # Look for failure markers in last 1000 chars
        tail = output[-1000:] if len(output) > 1000 else output
        for marker in self.failure_markers:
            if marker in tail:
                logger.debug(f"Found failure marker: {marker}")
                return True
        return False

    def collect_output(self, session_id: str, tail_lines: int | None = None) -> str:
        """Collect final output from a session.

        Args:
            session_id: Tmux session identifier
            tail_lines: Number of lines to collect (None = all)

        Returns:
            Session output
        """
        output = self._get_session_output(session_id, tail_lines)
        logger.info(f"Collected {len(output)} chars from session {session_id}")
        return output

    def extend_timeout(self, session_id: str, minutes: int) -> None:
        """Extend the timeout for a session.

        Args:
            session_id: Tmux session identifier
            minutes: Additional minutes to add
        """
        metrics = self.sessions.get(session_id)
        if not metrics:
            logger.warning(f"Cannot extend timeout: session {session_id} not found")
            return

        now = datetime.now(UTC)
        new_deadline = now + timedelta(minutes=minutes)
        metrics.extended_until = new_deadline

        logger.info(f"Extended timeout for {session_id} by {minutes}min until {new_deadline}")

    def is_completed(self, session_id: str) -> bool:
        """Check if a session is completed.

        Args:
            session_id: Tmux session identifier

        Returns:
            True if session is completed
        """
        metrics = self.sessions.get(session_id)
        return metrics.state == SessionState.COMPLETED if metrics else False

    def get_state(self, session_id: str) -> SessionState | None:
        """Get the current state of a session.

        Args:
            session_id: Tmux session identifier

        Returns:
            Current session state or None if not found
        """
        metrics = self.sessions.get(session_id)
        return metrics.state if metrics else None

    def get_metrics(self, session_id: str) -> SessionMetrics | None:
        """Get metrics for a session.

        Args:
            session_id: Tmux session identifier

        Returns:
            Session metrics or None if not found
        """
        return self.sessions.get(session_id)

    def get_all_metrics(self) -> dict[str, SessionMetrics]:
        """Get metrics for all monitored sessions.

        Returns:
            Dict mapping session_id to metrics
        """
        return self.sessions.copy()

    async def run(self, max_iterations: int | None = None) -> None:
        """Run the monitor loop.

        Args:
            max_iterations: Maximum iterations (None = run forever)
        """
        self._running = True
        iteration = 0

        logger.info(f"Starting monitor loop (poll_interval={self.poll_interval}s)")

        try:
            while self._running:
                if max_iterations and iteration >= max_iterations:
                    logger.info(f"Reached max iterations ({max_iterations})")
                    break

                await self.poll_sessions()
                iteration += 1

                if self._running:
                    await asyncio.sleep(self.poll_interval)

        except KeyboardInterrupt:
            logger.info("Monitor interrupted by user")
        finally:
            self._running = False
            logger.info("Monitor stopped")

    def stop(self) -> None:
        """Stop the monitor loop."""
        self._running = False

    # ============================================================================
    # Tmux Interface Methods
    # ============================================================================

    def _session_exists(self, session_id: str) -> bool:
        """Check if a tmux session exists.

        Args:
            session_id: Tmux session identifier

        Returns:
            True if session exists
        """
        try:
            result = subprocess.run(
                ["tmux", "has-session", "-t", session_id],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _get_session_output(self, session_id: str, tail_lines: int | None = None) -> str:
        """Get output from a tmux session.

        Args:
            session_id: Tmux session identifier
            tail_lines: Number of lines to capture (None = all)

        Returns:
            Session output
        """
        try:
            cmd = ["tmux", "capture-pane", "-t", session_id, "-p"]
            if tail_lines:
                cmd.extend(["-S", f"-{tail_lines}"])
            else:
                # Capture entire scrollback
                cmd.extend(["-S", "-"])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.error(f"Failed to capture output from {session_id}")
            return ""

    def _get_exit_status(self, session_id: str) -> int | None:
        """Get exit status of a tmux pane.

        Args:
            session_id: Tmux session identifier

        Returns:
            Exit code or None if still running
        """
        try:
            # Check if pane is dead
            result = subprocess.run(
                ["tmux", "display-message", "-t", session_id, "-p", "#{pane_dead}"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.stdout.strip() == "1":
                # Pane is dead, get exit status
                result = subprocess.run(
                    ["tmux", "display-message", "-t", session_id, "-p", "#{pane_dead_status}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return int(result.stdout.strip())
            return None
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            return None

    def _hash_output(self, output: str) -> str:
        """Create a hash of output for change detection.

        Args:
            output: Output to hash

        Returns:
            Hash string
        """
        import hashlib

        return hashlib.sha256(output.encode()).hexdigest()


# ============================================================================
# Factory
# ============================================================================


def create_monitor(poll_interval: float = 30.0) -> ProgressMonitor:
    """Factory for ProgressMonitor (compat)."""
    return ProgressMonitor(poll_interval=int(poll_interval))


# ============================================================================
# CLI Interface
# ============================================================================


async def main():
    """CLI entry point for progress monitor."""
    import argparse

    parser = argparse.ArgumentParser(description="Monitor tmux session progress")
    parser.add_argument("--watch", action="store_true", help="Watch mode (continuous monitoring)")
    parser.add_argument(
        "--poll-interval", type=int, default=30, help="Poll interval in seconds (default: 30)"
    )
    parser.add_argument("--timeout", type=int, default=15, help="Timeout in minutes (default: 15)")
    parser.add_argument("sessions", nargs="*", help="Sessions to monitor")

    args = parser.parse_args()

    monitor = ProgressMonitor(poll_interval=args.poll_interval, default_timeout=args.timeout)

    # Register sessions
    for session_id in args.sessions:
        monitor.register_session(session_id)

    if args.watch:
        # Watch mode
        await monitor.run()
    else:
        # Single poll
        results = await monitor.poll_sessions()
        for session_id, state in results.items():
            print(f"{session_id}: {state.value}")


if __name__ == "__main__":
    asyncio.run(main())
