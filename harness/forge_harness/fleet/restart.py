"""
Agent Restart System for FORGE Fleet
=====================================

Automatically detects and restarts stale or crashed agents with task context recovery.

Features:
- Detect stale agents (>30min without activity)
- Detect crashed agents (non-zero exit codes)
- Recover task state from tmux session output
- Restart agents with recovered context
- Log all restart events

Usage:
    from forge_harness.fleet.restart import AgentRestarter

    restarter = AgentRestarter()

    # Detect stale agents
    stale = restarter.detect_stale_agents(threshold_minutes=30)

    # Auto-restart stale agents
    events = restarter.auto_restart_stale()

    # Manual restart with custom context
    restarter.restart_agent("forge:tech", "Continue implementing feature X")
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class HealthStatus:
    """Health status of an agent."""

    agent_id: str
    status: str  # "healthy", "stale", "crashed"
    last_activity: datetime | None
    current_task: str | None = None
    context_percent: float | None = None
    error_message: str | None = None


@dataclass
class TaskState:
    """State of a task being executed by an agent."""

    task_description: str
    progress: str | None = None
    context_remaining: int | None = None
    file_being_edited: str | None = None


@dataclass
class RestartEvent:
    """Record of an agent restart."""

    agent_id: str
    reason: str
    recovered_task: str | None
    new_session_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    old_session_id: str | None = None


class AgentRestarter:
    """Manages agent health monitoring and restart operations."""

    def __init__(
        self,
        forge_root: Path | None = None,
        stale_threshold_minutes: int = 30,
    ):
        """Initialize agent restarter.

        Args:
            forge_root: Path to FORGE repository root
            stale_threshold_minutes: Minutes of inactivity before agent is considered stale
        """
        if forge_root is None:
            forge_root = Path.cwd()
            # Try to find FORGE root
            for _ in range(10):
                if (forge_root / ".forge/fleet").exists():
                    break
                parent = forge_root.parent
                if parent == forge_root:
                    forge_root = Path.cwd()
                    break
                forge_root = parent

        self.forge_root = Path(forge_root)
        self.stale_threshold = timedelta(minutes=stale_threshold_minutes)
        self.fleet_dir = self.forge_root / ".forge/fleet"
        self.restart_log = self.fleet_dir / "restart.log"

        # Ensure directories exist
        self.fleet_dir.mkdir(exist_ok=True)

    def _get_session_list(self) -> list[dict]:
        """Get list of all tmux sessions with metadata."""
        sessions = []

        try:
            result = subprocess.run(
                [
                    "tmux",
                    "list-sessions",
                    "-F",
                    "#{session_name}:#{session_created}:#{session_activity}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                logger.debug("No tmux sessions found or tmux not running")
                return sessions

            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue

                parts = line.split(":")
                if len(parts) >= 3:
                    session_name = parts[0]
                    created = int(parts[1])
                    activity = int(parts[2])

                    sessions.append(
                        {
                            "name": session_name,
                            "created": datetime.fromtimestamp(created, tz=UTC),
                            "last_activity": datetime.fromtimestamp(activity, tz=UTC),
                        }
                    )

        except subprocess.TimeoutExpired:
            logger.warning("tmux list-sessions timed out")
        except FileNotFoundError:
            logger.warning("tmux not found - restart system unavailable")
        except Exception as e:
            logger.error(f"Error listing tmux sessions: {e}")

        return sessions

    def _capture_session_output(self, session_id: str, lines: int = 200) -> str:
        """Capture output from a tmux session."""
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", session_id, "-p", "-S", f"-{lines}"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                return result.stdout

        except subprocess.TimeoutExpired:
            logger.warning(f"tmux capture-pane timed out for {session_id}")
        except Exception as e:
            logger.error(f"Error capturing output from {session_id}: {e}")

        return ""

    def _check_session_exit_code(self, session_id: str) -> int | None:
        """Check if a session has exited and get exit code."""
        try:
            # Try to get session info - if it fails, session doesn't exist
            result = subprocess.run(
                ["tmux", "display-message", "-t", session_id, "-p", "#{session_id}"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                # Session doesn't exist - consider it crashed
                return 1

            # Session exists - check for error indicators in recent output
            output = self._capture_session_output(session_id, lines=20)

            # Check for common error patterns
            error_patterns = [
                r"error",
                r"exception",
                r"traceback",
                r"failed",
                r"panic",
                r"exit code (\d+)",
            ]

            for pattern in error_patterns:
                if re.search(pattern, output, re.IGNORECASE):
                    return 1

            return 0  # Healthy

        except Exception as e:
            logger.debug(f"Could not check exit code for {session_id}: {e}")
            return None

    def detect_stale_agents(self, threshold_minutes: int | None = None) -> list[HealthStatus]:
        """Detect agents that haven't had activity for threshold_minutes.

        Args:
            threshold_minutes: Minutes of inactivity (default: use instance threshold)

        Returns:
            List of HealthStatus objects for stale agents
        """
        if threshold_minutes is None:
            threshold = self.stale_threshold
        else:
            threshold = timedelta(minutes=threshold_minutes)

        stale_agents = []
        now = datetime.now(UTC)

        sessions = self._get_session_list()

        for session in sessions:
            time_since_activity = now - session["last_activity"]

            if time_since_activity > threshold:
                # Get current task from output
                output = self._capture_session_output(session["name"], lines=50)
                task = self._extract_current_task(output)
                context_pct = self._extract_context_usage(output)

                health = HealthStatus(
                    agent_id=session["name"],
                    status="stale",
                    last_activity=session["last_activity"],
                    current_task=task,
                    context_percent=context_pct,
                )

                stale_agents.append(health)
                logger.info(
                    f"Detected stale agent: {session['name']} "
                    f"(inactive for {time_since_activity.total_seconds() / 60:.1f} minutes)"
                )

        return stale_agents

    def detect_crashed_agents(self) -> list[HealthStatus]:
        """Detect agents that have crashed (non-zero exit or error state).

        Returns:
            List of HealthStatus objects for crashed agents
        """
        crashed_agents = []
        sessions = self._get_session_list()

        for session in sessions:
            exit_code = self._check_session_exit_code(session["name"])

            if exit_code is not None and exit_code != 0:
                output = self._capture_session_output(session["name"], lines=50)
                task = self._extract_current_task(output)
                error = self._extract_error_message(output)

                health = HealthStatus(
                    agent_id=session["name"],
                    status="crashed",
                    last_activity=session["last_activity"],
                    current_task=task,
                    error_message=error,
                )

                crashed_agents.append(health)
                logger.warning(f"Detected crashed agent: {session['name']}")

        return crashed_agents

    def _extract_current_task(self, output: str) -> str | None:
        """Extract current task description from session output."""
        if not output:
            return None

        # Look for common task indicators
        patterns = [
            r"Task:\s*(.+?)(?:\n|$)",
            r"Working on:\s*(.+?)(?:\n|$)",
            r"Implementing:\s*(.+?)(?:\n|$)",
            r"Feature:\s*(.+?)(?:\n|$)",
            r"# (.+?)(?:\n|$)",  # Markdown headers
        ]

        for pattern in patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                task = match.group(1).strip()
                if len(task) > 10:  # Ignore very short matches
                    return task[:200]  # Truncate long descriptions

        # Fallback: last non-empty line that looks like a task
        lines = [l.strip() for l in output.split("\n") if l.strip()]
        if lines:
            last_line = lines[-1]
            if len(last_line) > 10 and not last_line.startswith(">"):
                return last_line[:200]

        return None

    def _extract_context_usage(self, output: str) -> float | None:
        """Extract context usage percentage from session output."""
        if not output:
            return None

        # Look for context usage indicators
        patterns = [
            r"context[:\s]+(\d+)%",
            r"(\d+)%\s+context",
            r"usage[:\s]+(\d+)%",
        ]

        for pattern in patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                try:
                    return float(match.group(1)) / 100.0
                except ValueError:
                    pass

        return None

    def _extract_error_message(self, output: str) -> str | None:
        """Extract error message from session output."""
        if not output:
            return None

        # Look for error patterns
        error_patterns = [
            r"Error:\s*(.+?)(?:\n|$)",
            r"Exception:\s*(.+?)(?:\n|$)",
            r"Failed:\s*(.+?)(?:\n|$)",
            r"Traceback[^\n]*\n(.*?)(?:\n\n|$)",
        ]

        for pattern in error_patterns:
            match = re.search(pattern, output, re.IGNORECASE | re.DOTALL)
            if match:
                error = match.group(1).strip()
                return error[:500]  # Truncate long errors

        return None

    def recover_task_state(self, session_id: str) -> TaskState | None:
        """Recover task state from a session's output.

        Args:
            session_id: tmux session identifier

        Returns:
            TaskState object or None if no task state found
        """
        output = self._capture_session_output(session_id, lines=200)

        if not output:
            logger.warning(f"No output captured from {session_id}")
            return None

        task = self._extract_current_task(output)
        if not task:
            logger.warning(f"Could not extract task from {session_id}")
            return None

        # Try to extract progress information
        progress_match = re.search(
            r"(?:progress|step|stage)[:\s]+(.+?)(?:\n|$)", output, re.IGNORECASE
        )
        progress = progress_match.group(1).strip() if progress_match else None

        # Try to extract file being edited
        file_match = re.search(
            r"(?:editing|modifying|working on)[:\s]+([^\s]+\.(?:py|ts|tsx|js|jsx|md))",
            output,
            re.IGNORECASE,
        )
        file_being_edited = file_match.group(1).strip() if file_match else None

        # Get context remaining
        context_pct = self._extract_context_usage(output)
        context_remaining = None
        if context_pct is not None:
            context_remaining = int((1.0 - context_pct) * 100)

        return TaskState(
            task_description=task,
            progress=progress,
            context_remaining=context_remaining,
            file_being_edited=file_being_edited,
        )

    def restart_agent(
        self,
        session_id: str,
        recovered_context: str,
        reason: str = "manual_restart",
    ) -> RestartEvent:
        """Restart an agent with recovered context.

        Args:
            session_id: Original session identifier
            recovered_context: Context to provide to new agent
            reason: Reason for restart

        Returns:
            RestartEvent object

        Raises:
            subprocess.CalledProcessError: If tmux commands fail
        """
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        new_session_id = f"{session_id}-restart-{timestamp}"

        logger.info(f"Restarting agent: {session_id} -> {new_session_id}")

        # Save restart context to file
        restart_dir = self.forge_root / ".forge/restarts"
        restart_dir.mkdir(exist_ok=True)
        context_file = restart_dir / f"{session_id.replace(':', '_')}_{timestamp}.md"

        with open(context_file, "w") as f:
            f.write("# Agent Restart Context\n\n")
            f.write(f"**Original Session:** {session_id}\n")
            f.write(f"**New Session:** {new_session_id}\n")
            f.write(f"**Reason:** {reason}\n")
            f.write(f"**Timestamp:** {timestamp}\n\n")
            f.write("## Recovered Context\n\n")
            f.write(recovered_context)

        logger.info(f"Saved restart context: {context_file}")

        # Create new tmux session
        try:
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", new_session_id],
                check=True,
                timeout=10,
            )
            logger.info(f"Created new tmux session: {new_session_id}")

            # Send restart notification to new session
            subprocess.run(
                [
                    "tmux",
                    "send-keys",
                    "-t",
                    new_session_id,
                    f"# Agent restarted from {session_id}",
                    "Enter",
                ],
                check=True,
                timeout=5,
            )
            subprocess.run(
                [
                    "tmux",
                    "send-keys",
                    "-t",
                    new_session_id,
                    f"# Restart context: {context_file}",
                    "Enter",
                ],
                check=True,
                timeout=5,
            )

            logger.info("Initialized new session with restart reference")

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to restart agent: {e}")
            raise
        except subprocess.TimeoutExpired as e:
            logger.error(f"Timeout during agent restart: {e}")
            raise

        # Create restart event
        event = RestartEvent(
            agent_id=new_session_id,
            reason=reason,
            recovered_task=recovered_context[:200] if recovered_context else None,
            new_session_id=new_session_id,
            old_session_id=session_id,
        )

        # Log the restart event
        self._log_restart_event(event)

        return event

    def _log_restart_event(self, event: RestartEvent) -> None:
        """Log a restart event to the restart log file."""
        try:
            with open(self.restart_log, "a") as f:
                log_entry = {
                    "timestamp": event.timestamp,
                    "old_session": event.old_session_id,
                    "new_session": event.new_session_id,
                    "reason": event.reason,
                    "recovered_task": event.recovered_task,
                }
                f.write(json.dumps(log_entry) + "\n")

            logger.info(f"Logged restart event to {self.restart_log}")

        except Exception as e:
            logger.error(f"Failed to log restart event: {e}")

    def auto_restart_stale(
        self,
        threshold_minutes: int | None = None,
        dry_run: bool = False,
    ) -> list[RestartEvent]:
        """Automatically detect and restart stale agents.

        Args:
            threshold_minutes: Minutes of inactivity (default: use instance threshold)
            dry_run: If True, only detect but don't restart

        Returns:
            List of RestartEvent objects for restarted agents
        """
        stale_agents = self.detect_stale_agents(threshold_minutes)

        if not stale_agents:
            logger.info("No stale agents detected")
            return []

        logger.info(f"Found {len(stale_agents)} stale agents")

        if dry_run:
            logger.info("Dry run mode - no restarts performed")
            return []

        restart_events = []

        for health in stale_agents:
            try:
                # Recover task state
                task_state = self.recover_task_state(health.agent_id)

                if task_state:
                    recovered_context = (
                        f"Task: {task_state.task_description}\n"
                        f"Progress: {task_state.progress or 'Unknown'}\n"
                    )
                    if task_state.file_being_edited:
                        recovered_context += f"File: {task_state.file_being_edited}\n"
                    if task_state.context_remaining:
                        recovered_context += f"Context remaining: {task_state.context_remaining}%\n"
                else:
                    recovered_context = f"Previous task: {health.current_task or 'Unknown'}"

                # Restart the agent
                event = self.restart_agent(
                    health.agent_id,
                    recovered_context,
                    reason="stale_agent",
                )
                restart_events.append(event)

                logger.info(f"Successfully restarted stale agent: {health.agent_id}")

            except Exception as e:
                logger.error(f"Failed to restart agent {health.agent_id}: {e}")

        return restart_events

    def get_restart_history(self, limit: int = 10) -> list[dict]:
        """Get recent restart events from log.

        Args:
            limit: Maximum number of events to return

        Returns:
            List of restart event dictionaries
        """
        if not self.restart_log.exists():
            return []

        events = []

        try:
            with open(self.restart_log) as f:
                for line in f:
                    if line.strip():
                        events.append(json.loads(line))

            # Return most recent events
            return events[-limit:]

        except Exception as e:
            logger.error(f"Failed to read restart history: {e}")
            return []


def create_agent_restarter(
    forge_root: Path | None = None,
    stale_threshold_minutes: int = 30,
) -> AgentRestarter:
    """Factory function to create AgentRestarter instance.

    Args:
        forge_root: Path to FORGE repository root
        stale_threshold_minutes: Minutes of inactivity before agent is considered stale

    Returns:
        Configured AgentRestarter instance
    """
    return AgentRestarter(
        forge_root=forge_root,
        stale_threshold_minutes=stale_threshold_minutes,
    )
