"""Agent restart and recovery for FORGE Harness.

Manages detection and recovery of stale/crashed fleet agents.

Recovery Workflow:
1. Detect stale/crashed agents
2. Extract last task state from session output
3. Restart agent with recovered context
4. Re-send last task if available

Status Indicators:
- stale: No activity for threshold_minutes (default 30)
- crashed: Shell prompt visible, no Claude process
- healthy: Active or idle within threshold

Usage:
    from forge_harness.fleet import AgentRestarter

    restarter = AgentRestarter()
    result = restarter.auto_recover("tech")
    if result.success:
        print(f"Recovered {result.window_name}")
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..logging_config import get_logger

logger = get_logger(__name__)

# Recovery thresholds
DEFAULT_STALE_THRESHOLD_MINUTES = 30
DEFAULT_RESTART_COOLDOWN_SECONDS = 300  # 5 minutes
COMMAND_TIMEOUT = 30  # seconds

# Restart rate limiting
_last_restart_times: dict[str, datetime] = {}


@dataclass
class TaskState:
    """Captured state from a crashed/stale agent.

    Attributes:
        prompt: Last task prompt sent to agent
        working_directory: Directory agent was working in
        files_modified: Files touched in session
        last_tool_call: Last tool being executed
        context_percentage: Estimated context usage
        extracted_at: When state was captured
    """

    prompt: str
    working_directory: str
    files_modified: list[str]
    last_tool_call: str | None
    context_percentage: int
    extracted_at: datetime


@dataclass
class RecoveryResult:
    """Result of a recovery attempt.

    Attributes:
        success: Whether recovery succeeded
        window_name: Agent window name
        previous_status: Status before recovery ('stale', 'crashed', 'healthy')
        task_recovered: Whether task state was recovered
        task_state: Recovered task state if available
        error: Error message if failed
        timestamp: When recovery was attempted
    """

    success: bool
    window_name: str
    previous_status: str
    task_recovered: bool
    task_state: TaskState | None
    error: str | None
    timestamp: datetime


class AgentRestarter:
    """Manages detection and recovery of stale/crashed fleet agents."""

    def __init__(self, session: str = "forge"):
        """Initialize with tmux session name.

        Args:
            session: Tmux session name (default: "forge")
        """
        self.session = session

    def _run_tmux_command(self, cmd: list[str], timeout: int = COMMAND_TIMEOUT) -> str:
        """Run a tmux command and return output.

        Args:
            cmd: Command arguments (without 'tmux' prefix)
            timeout: Command timeout in seconds

        Returns:
            Command output as string, empty string on error
        """
        try:
            result = subprocess.run(
                ["tmux"] + cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return ""
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.debug(f"tmux command failed: {e}")
            return ""

    def _get_pane_activity_time(self, target: str) -> datetime | None:
        """Get last activity time from tmux pane.

        Args:
            target: Tmux target (e.g., "forge:tech")

        Returns:
            Last activity timestamp, None if not available
        """
        activity = self._run_tmux_command(
            ["display-message", "-t", target, "-p", "#{pane_activity}"]
        )
        if activity and activity.isdigit():
            timestamp = int(activity)
            return datetime.fromtimestamp(timestamp, tz=UTC)
        return None

    def _get_pane_command(self, target: str) -> str:
        """Get current command running in pane.

        Args:
            target: Tmux target (e.g., "forge:tech")

        Returns:
            Command name (e.g., "claude", "bash", "zsh")
        """
        command = self._run_tmux_command(
            ["display-message", "-t", target, "-p", "#{pane_current_command}"]
        )
        return command.lower() if command else ""

    def _capture_pane_content(self, target: str, lines: int = 500) -> str:
        """Capture content from tmux pane.

        Args:
            target: Tmux target (e.g., "forge:tech")
            lines: Number of lines to capture

        Returns:
            Pane content as string
        """
        return self._run_tmux_command(["capture-pane", "-t", target, "-p", "-S", f"-{lines}"])

    def is_stale(
        self, window_name: str, threshold_minutes: int = DEFAULT_STALE_THRESHOLD_MINUTES
    ) -> bool:
        """Detect if agent has no activity for threshold_minutes.

        Args:
            window_name: Window name (e.g., "tech")
            threshold_minutes: Inactivity threshold in minutes

        Returns:
            True if agent is stale
        """
        target = f"{self.session}:{window_name}"

        # Get pane activity time
        activity_time = self._get_pane_activity_time(target)
        if not activity_time:
            logger.debug(f"Could not get activity time for {target}")
            return False

        # Calculate time delta
        now = datetime.now(UTC)
        minutes_inactive = (now - activity_time).total_seconds() / 60

        is_stale = minutes_inactive >= threshold_minutes
        logger.debug(
            f"{window_name}: inactive for {minutes_inactive:.1f} minutes "
            f"(threshold: {threshold_minutes})"
        )

        return is_stale

    def is_crashed(self, window_name: str) -> bool:
        """Detect if agent has exited to shell prompt.

        Indicators of crash:
        - Pane ends with shell prompt ($ or ❯ or %)
        - No 'INSERT', 'NORMAL', or '>' indicators
        - Process is shell (bash/zsh), not claude

        Args:
            window_name: Window name (e.g., "tech")

        Returns:
            True if agent is crashed
        """
        target = f"{self.session}:{window_name}"

        # Check current command
        command = self._get_pane_command(target)
        if command in ("bash", "zsh", "sh"):
            logger.debug(f"{window_name}: running shell ({command}), may be crashed")

            # Verify by checking pane content
            content = self._capture_pane_content(target, lines=50)
            if not content:
                return False

            # Look for indicators that Claude is NOT running
            lines = content.splitlines()
            if not lines:
                return False

            last_line = lines[-1]

            # Shell prompt patterns
            shell_patterns = [
                r"\$\s*$",  # Bash prompt ending with $
                r"❯\s*$",  # Zsh/Fish prompt
                r">\s*$",  # Generic prompt
                r"%\s*$",  # Zsh prompt
                r"#\s*$",  # Root prompt
            ]

            for pattern in shell_patterns:
                if re.search(pattern, last_line):
                    # Looks like shell prompt
                    # Check if Claude activity indicators are present
                    active_indicators = [
                        r"\[INSERT\]",
                        r"\[NORMAL\]",
                        r"Reading",
                        r"Writing",
                        r"Thinking",
                    ]
                    has_active_indicator = any(
                        re.search(p, content, re.IGNORECASE) for p in active_indicators
                    )

                    if not has_active_indicator:
                        logger.debug(f"{window_name}: crashed (shell prompt, no Claude activity)")
                        return True

        return False

    def recover_task_state(self, window_name: str) -> TaskState | None:
        """Extract the last task/prompt from session output.

        Parse patterns:
        1. Last user prompt: Look for "❯ " or "> " followed by text
        2. Working directory: Parse from prompt or 'pwd' output
        3. Files modified: Look for "Edit", "Write" tool outputs
        4. Last tool call: Find most recent tool invocation
        5. Context: Parse "Context: XX%" if visible

        Args:
            window_name: Window name (e.g., "tech")

        Returns:
            TaskState if recoverable, None otherwise
        """
        target = f"{self.session}:{window_name}"
        content = self._capture_pane_content(target)

        if not content:
            logger.debug(f"No content to recover from {window_name}")
            return None

        # Extract working directory
        # Look for prompt with directory: "~/work/FORGE ❯"
        working_dir = str(Path.cwd())  # Default
        dir_match = re.search(r"([~/][^\s❯>$]+)\s*[❯>$]", content)
        if dir_match:
            working_dir = dir_match.group(1).replace("~", str(Path.home()))
            logger.debug(f"Recovered working directory: {working_dir}")

        # Extract last prompt
        # Look for user input after prompt markers
        prompt_patterns = [
            r"❯\s+([^\n]+)",
            r">\s+([^\n]+)",
            r"\$\s+([^\n]+)",
        ]

        prompt = ""
        for pattern in prompt_patterns:
            matches = re.findall(pattern, content)
            if matches:
                # Get last match that's not a command like "cd" or "ls"
                for match in reversed(matches):
                    if not match.startswith(("cd ", "ls", "pwd", "tmux")):
                        prompt = match.strip()
                        break
                if prompt:
                    break

        logger.debug(f"Recovered prompt: {prompt[:50]}..." if prompt else "No prompt recovered")

        # Extract modified files
        # Look for tool outputs mentioning files
        files_modified = []
        file_patterns = [
            r"Edit.*?([/\w.-]+\.(?:py|ts|tsx|js|jsx|md))",
            r"Write.*?([/\w.-]+\.(?:py|ts|tsx|js|jsx|md))",
            r"Read.*?([/\w.-]+\.(?:py|ts|tsx|js|jsx|md))",
        ]

        for pattern in file_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            files_modified.extend(matches)

        # Deduplicate
        files_modified = list(set(files_modified))
        logger.debug(f"Recovered {len(files_modified)} modified files")

        # Extract last tool call
        last_tool = None
        tool_patterns = [
            r"(Reading|Writing|Editing|Running|Searching)\s+",
        ]

        for pattern in tool_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                last_tool = matches[-1]
                break

        # Extract context percentage
        context = 0
        context_patterns = [
            r"context[:\s]+(\d+)%",
            r"(\d+)%\s+context",
        ]

        for pattern in context_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                context = int(match.group(1))
                break

        # Create TaskState if we recovered meaningful state
        if prompt or files_modified or last_tool:
            return TaskState(
                prompt=prompt,
                working_directory=working_dir,
                files_modified=files_modified,
                last_tool_call=last_tool,
                context_percentage=context,
                extracted_at=datetime.now(UTC),
            )

        return None

    def restart_agent(self, window_name: str, task_state: TaskState | None = None) -> bool:
        """Kill old agent and spawn new one with recovered task.

        Steps:
        1. Send Ctrl+C to stop current process (if any)
        2. Wait for shell prompt
        3. If task_state provided, cd to working_directory
        4. Launch new claude session
        5. If task_state.prompt provided, send it after startup

        Args:
            window_name: Window name (e.g., "tech")
            task_state: Recovered task state to restore

        Returns:
            True if restart succeeded
        """
        # Check rate limiting
        if window_name in _last_restart_times:
            last_restart = _last_restart_times[window_name]
            seconds_since = (datetime.now(UTC) - last_restart).total_seconds()
            if seconds_since < DEFAULT_RESTART_COOLDOWN_SECONDS:
                logger.warning(
                    f"Rate limiting restart of {window_name} "
                    f"(last restart {seconds_since:.0f}s ago)"
                )
                return False

        target = f"{self.session}:{window_name}"
        logger.info(f"Restarting agent: {window_name}")

        try:
            # Step 1: Stop current process
            self._run_tmux_command(["send-keys", "-t", target, "C-c"])
            time.sleep(1)

            # Send another Ctrl+C to ensure clean state
            self._run_tmux_command(["send-keys", "-t", target, "C-c"])
            time.sleep(0.5)

            # Step 2: Change directory if we have task state
            if task_state and task_state.working_directory:
                cd_cmd = f"cd {task_state.working_directory}"
                self._run_tmux_command(["send-keys", "-t", target, cd_cmd, "Enter"])
                time.sleep(0.5)
                logger.debug(f"Changed to directory: {task_state.working_directory}")

            # Step 3: Launch Claude
            self._run_tmux_command(["send-keys", "-t", target, "claude", "Enter"])
            logger.debug("Launched Claude")

            # Wait for Claude to initialize
            time.sleep(3)

            # Step 4: Send recovered prompt if available
            if task_state and task_state.prompt:
                # Wait a bit more for Claude to be ready
                time.sleep(2)

                # Construct recovery message
                recovery_prompt = (
                    f"[Session recovered] Continuing previous task:\n\n{task_state.prompt}"
                )

                # Send prompt
                self._run_tmux_command(["send-keys", "-t", target, recovery_prompt])
                time.sleep(0.5)
                self._run_tmux_command(["send-keys", "-t", target, "Enter"])
                logger.debug(f"Sent recovered prompt: {task_state.prompt[:50]}...")

            # Update rate limiting tracker
            _last_restart_times[window_name] = datetime.now(UTC)

            logger.info(f"Successfully restarted {window_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to restart {window_name}: {e}")
            return False

    def auto_recover(self, window_name: str) -> RecoveryResult:
        """Full automatic recovery workflow.

        1. Check if agent is healthy (return early if so)
        2. Determine status (stale vs crashed)
        3. Attempt to recover task state
        4. Restart agent with recovered state
        5. Return result with details

        Args:
            window_name: Window name (e.g., "tech")

        Returns:
            RecoveryResult with operation details
        """
        timestamp = datetime.now(UTC)
        logger.info(f"Auto-recovery check for {window_name}")

        # Step 1: Check if agent is healthy
        is_stale = self.is_stale(window_name)
        is_crashed = self.is_crashed(window_name)

        if not is_stale and not is_crashed:
            logger.debug(f"{window_name} is healthy, no recovery needed")
            return RecoveryResult(
                success=True,
                window_name=window_name,
                previous_status="healthy",
                task_recovered=False,
                task_state=None,
                error=None,
                timestamp=timestamp,
            )

        # Step 2: Determine status
        status = "crashed" if is_crashed else "stale"
        logger.info(f"{window_name} is {status}, attempting recovery")

        # Step 3: Recover task state
        task_state = self.recover_task_state(window_name)
        task_recovered = task_state is not None

        if task_recovered:
            logger.info(
                f"Recovered task state: "
                f"prompt='{task_state.prompt[:50]}...', "
                f"dir={task_state.working_directory}"
            )
        else:
            logger.warning(f"Could not recover task state for {window_name}")

        # Step 4: Restart agent
        try:
            success = self.restart_agent(window_name, task_state)

            if success:
                logger.info(f"Successfully recovered {window_name}")
            else:
                logger.error(f"Failed to restart {window_name}")

            return RecoveryResult(
                success=success,
                window_name=window_name,
                previous_status=status,
                task_recovered=task_recovered,
                task_state=task_state,
                error=None if success else "Restart failed",
                timestamp=timestamp,
            )

        except Exception as e:
            error_msg = f"Recovery failed: {e}"
            logger.error(error_msg)

            return RecoveryResult(
                success=False,
                window_name=window_name,
                previous_status=status,
                task_recovered=task_recovered,
                task_state=task_state,
                error=error_msg,
                timestamp=timestamp,
            )


def auto_recover_all(session: str = "forge") -> list[RecoveryResult]:
    """Auto-recover all unhealthy agents in session.

    Args:
        session: Tmux session name (default: "forge")

    Returns:
        List of recovery results
    """
    from .dashboard import get_fleet_status

    logger.info(f"Auto-recovering all unhealthy agents in {session}")

    # Get fleet status
    status = get_fleet_status()
    restarter = AgentRestarter(session=session)

    results = []
    for agent in status.agents:
        if agent.status in ("stale", "crashed"):
            logger.info(f"Recovering {agent.name} ({agent.status})")
            result = restarter.auto_recover(agent.name)
            results.append(result)
        else:
            logger.debug(f"Skipping {agent.name} ({agent.status})")

    logger.info(
        f"Recovery complete: {sum(1 for r in results if r.success)}/{len(results)} succeeded"
    )

    return results
