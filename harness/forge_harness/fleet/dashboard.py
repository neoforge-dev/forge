"""Fleet health dashboard for FORGE Harness.

Provides real-time monitoring of agent status, context usage, and activity.

Status Detection:
- active: Agent is actively working (INSERT mode, recent tool activity)
- idle: Agent is at shell prompt, no recent activity (<30 min)
- stale: Agent has been inactive for 30+ minutes

Health Determination:
- healthy: No stale agents
- degraded: <50% agents are stale
- critical: >=50% agents are stale or no agents running

Usage:
    from forge_harness.fleet import get_fleet_status

    status = get_fleet_status()
    print(f"Health: {status.overall_health}")
    for agent in status.agents:
        print(f"{agent.name}: {agent.status} (context: {agent.context_estimate}%)")
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..dashboard_core import AgentState, compute_fleet_health
from ..logging_config import get_logger

logger = get_logger(__name__)

# Time thresholds
IDLE_THRESHOLD_MINUTES = 5
STALE_THRESHOLD_MINUTES = 30

# Activity patterns for status detection
ACTIVE_PATTERNS = [
    r"\[INSERT\]",  # Vim insert mode
    r"Reading",
    r"Writing",
    r"Running",
    r"Editing",
    r"Searching",
    r"Thinking",
    r"Working",
    r"Processing",
    r"Analyzing",
]

PROMPT_PATTERNS = [
    r"\$\s*$",  # Bash prompt ending with $
    r"❯\s*$",  # Zsh/Fish prompt
    r">\s*$",  # Generic prompt
    r"#\s*$",  # Root prompt
]


def _run_tmux_command(cmd: list[str], timeout: int = 10) -> str:
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


def _list_forge_windows() -> list[tuple[str, str]]:
    """List all windows in the forge session.

    Returns:
        List of (session_name, window_name) tuples
    """
    # Check if forge session exists
    sessions = _run_tmux_command(["list-sessions", "-F", "#{session_name}"])
    if not sessions:
        logger.debug("No tmux sessions found")
        return []

    forge_sessions = [s for s in sessions.splitlines() if s.startswith("forge")]
    if not forge_sessions:
        logger.debug("No forge sessions found")
        return []

    windows = []
    for session in forge_sessions:
        window_output = _run_tmux_command(["list-windows", "-t", session, "-F", "#{window_name}"])
        if window_output:
            for window_name in window_output.splitlines():
                windows.append((session, window_name))

    return windows


def _capture_pane_content(target: str, lines: int = 100) -> str:
    """Capture content from a tmux pane.

    Args:
        target: Tmux target (e.g., "forge:tech")
        lines: Number of lines to capture

    Returns:
        Pane content as string
    """
    return _run_tmux_command(["capture-pane", "-t", target, "-p", "-S", f"-{lines}"])


def _detect_status(content: str) -> str:
    """Detect agent status from pane content.

    Args:
        content: Captured pane content

    Returns:
        Status: 'active', 'idle', or 'stale'
    """
    if not content or not content.strip():
        return "stale"

    # Check for active patterns
    for pattern in ACTIVE_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return "active"

    # Check for idle (shell prompt visible)
    lines = content.splitlines()
    if lines:
        last_line = lines[-1]
        for pattern in PROMPT_PATTERNS:
            if re.search(pattern, last_line):
                return "idle"

    # If we have content but no clear indicators, assume idle
    return "idle"


def _estimate_last_activity(content: str, status: str) -> datetime:
    """Estimate when the agent was last active.

    Args:
        content: Captured pane content
        status: Detected status

    Returns:
        Estimated last activity timestamp
    """
    now = datetime.now(UTC)

    # Active agents: activity is now
    if status == "active":
        return now

    # For idle/stale, look for timestamp patterns in output
    # Common patterns: [HH:MM:SS], (HH:MM:SS), HH:MM:SS
    timestamp_patterns = [
        r"\[(\d{2}:\d{2}:\d{2})\]",
        r"\((\d{2}:\d{2}:\d{2})\)",
        r"(\d{2}:\d{2}:\d{2})",
    ]

    latest_time = None
    for pattern in timestamp_patterns:
        matches = re.findall(pattern, content)
        if matches:
            # Get the last timestamp found
            time_str = matches[-1]
            try:
                time_parts = [int(x) for x in time_str.split(":")]
                hours, minutes, seconds = time_parts
                # Create datetime with today's date
                activity_time = now.replace(
                    hour=hours, minute=minutes, second=seconds, microsecond=0
                )
                # If time is in the future, it's from yesterday
                if activity_time > now:
                    activity_time -= timedelta(days=1)
                if latest_time is None or activity_time > latest_time:
                    latest_time = activity_time
            except (ValueError, IndexError):
                continue

    if latest_time:
        return latest_time

    # No timestamp found - estimate based on status
    if status == "idle":
        # Assume idle for ~10 minutes
        return now - timedelta(minutes=10)
    else:  # stale
        # Assume stale for at least threshold
        return now - timedelta(minutes=STALE_THRESHOLD_MINUTES)


def _extract_context_percentage(content: str) -> int:
    """Extract context usage percentage from pane content.

    Looks for patterns like:
    - "Context: 45%"
    - "45% context"
    - "Context usage: 45%"

    Args:
        content: Captured pane content

    Returns:
        Context percentage (0-100), or 0 if not found
    """
    # Claude Code style: "Context: 45%" or "45% context"
    patterns = [
        r"context[:\s]+(\d+)%",
        r"(\d+)%\s+context",
        r"context\s+usage[:\s]+(\d+)%",
    ]

    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            try:
                percentage = int(match.group(1))
                return min(100, max(0, percentage))
            except (ValueError, IndexError):
                continue

    # If no explicit context found, estimate from output length and status
    lines = content.splitlines()
    line_count = len(lines)

    # Rough heuristic: more output = higher context usage
    if line_count > 500:
        return 60
    elif line_count > 300:
        return 40
    elif line_count > 150:
        return 25
    elif line_count > 50:
        return 15
    else:
        return 5


def _classify_status_by_time(status: str, last_activity: datetime) -> str:
    """Reclassify status based on time since last activity.

    Args:
        status: Initial status detection
        last_activity: Timestamp of last activity

    Returns:
        Refined status: 'active', 'idle', or 'stale'
    """
    now = datetime.now(UTC)
    minutes_since_activity = (now - last_activity).total_seconds() / 60

    # Override to stale if been inactive too long
    if minutes_since_activity >= STALE_THRESHOLD_MINUTES:
        return "stale"

    # Keep original status if within threshold
    return status


import time


@dataclass
class FleetAgent:
    """Represents a single agent in the fleet."""

    name: str  # Full session identifier (e.g., "forge:tech")
    session_id: str  # Session name (e.g., "forge")
    agent_type: str  # Agent type (claude, pi, amp, opencode, etc.)
    last_activity: datetime | None  # Last activity timestamp
    context_estimate: int  # Estimated context usage in characters
    is_stale: bool  # True if >30min without activity
    window_name: str  # Window name within session
    status: str  # active, idle, error, unknown
    current_task: str | None = None  # Current task if known

    def to_agent_state(self) -> AgentState:
        """Convert FleetAgent to canonical AgentState model.

        This enables interoperability between fleet monitoring
        and the unified dashboard service.
        """
        from ..dashboard_core import AgentState

        return AgentState(
            id=self.name,
            name=self.name,
            source="tmux",
            status=self.status,
            last_activity=self.last_activity,
            is_stale=self.is_stale,
            context_estimate=self.context_estimate,
            session_id=self.session_id,
            window_name=self.window_name,
            agent_type=self.agent_type,
            task=self.current_task,
        )


@dataclass
class FleetStatus:
    """Canonical fleet status - uses FleetAgent and TmuxInterface.

    This is the recommended FleetStatus dataclass for new code.
    Supports both legacy field names (total_active, etc.) and new names
    (active_agents, stale_agents) via __post_init__ normalization.
    """

    agents: list[FleetAgent]
    timestamp: datetime
    total_agents: int = 0
    stale_agents: int = 0
    active_agents: int = 0
    total_active: int = 0
    total_idle: int = 0
    total_stale: int = 0
    overall_health: str = "healthy"

    def __post_init__(self) -> None:
        """Normalize legacy and new field names."""
        # Derive totals from agents if not provided.
        if self.total_agents == 0 and self.agents:
            self.total_agents = len(self.agents)

        # Map between legacy and current field names.
        if self.active_agents == 0 and self.total_active:
            self.active_agents = self.total_active
        if self.stale_agents == 0 and self.total_stale:
            self.stale_agents = self.total_stale
        if self.total_active == 0 and self.active_agents:
            self.total_active = self.active_agents
        if self.total_stale == 0 and self.stale_agents:
            self.total_stale = self.stale_agents

        if self.total_agents == 0 and (self.total_active or self.total_idle or self.total_stale):
            self.total_agents = self.total_active + self.total_idle + self.total_stale

        if self.total_idle == 0 and self.total_agents:
            self.total_idle = max(0, self.total_agents - self.total_active - self.total_stale)

        if not self.overall_health:
            agent_states = [
                AgentState(id=a.name, status=a.status, is_stale=a.is_stale) for a in self.agents
            ]
            self.overall_health = compute_fleet_health(agent_states)

    @classmethod
    def from_agents(cls, agents: list[FleetAgent]) -> FleetStatus:
        """Create FleetStatus from agent list."""
        now = datetime.now(UTC)
        stale_count = sum(1 for agent in agents if agent.is_stale)
        active_count = sum(1 for agent in agents if agent.status == "active")
        idle_count = max(0, len(agents) - active_count - stale_count)

        agent_states = [AgentState(id=a.name, status=a.status, is_stale=a.is_stale) for a in agents]
        overall_health = compute_fleet_health(agent_states)

        return cls(
            agents=agents,
            timestamp=now,
            total_agents=len(agents),
            stale_agents=stale_count,
            active_agents=active_count,
            total_active=active_count,
            total_idle=idle_count,
            total_stale=stale_count,
            overall_health=overall_health,
        )


class TmuxInterface:
    """Interface for interacting with tmux sessions."""

    # Agent type detection patterns
    AGENT_TYPE_MAP = {
        "claude": "claude",
        "cursor": "claude",
        "tech": "claude",
        "cto": "claude",
        "game": "claude",
        "pi": "pi",
        "amp": "amp",
        "opencode": "opencode",
        "codex": "codex",
        "gemini": "gemini",
        "product": "claude",
        "content": "claude",
        "qa": "claude",
    }

    # Status detection patterns
    STATUS_PATTERNS = {
        "error": ["error", "failed", "traceback", "exception", "panic"],
        "active": [
            "generating",
            "running",
            "processing",
            "thinking",
            "writing",
            "reading",
            "executing",
            "working",
            "analyzing",
        ],
        "idle": ["waiting", "idle", "paused", "ready", "completed", "finished", "done"],
    }

    @staticmethod
    def list_sessions() -> list[dict]:
        """List all tmux sessions with their windows."""
        sessions = []

        try:
            # Get all session:window combinations
            result = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_name}"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                return sessions

            session_names = [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]

            for session_name in session_names:
                # Get windows for this session
                windows_result = subprocess.run(
                    [
                        "tmux",
                        "list-windows",
                        "-t",
                        session_name,
                        "-F",
                        "#{window_name} #{window_active}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if windows_result.returncode == 0:
                    for line in windows_result.stdout.strip().split("\n"):
                        if line.strip():
                            parts = line.split(" ", 1)
                            window_name = parts[0]
                            is_active = len(parts) > 1 and parts[1] == "1"

                            sessions.append(
                                {
                                    "session_name": session_name,
                                    "window_name": window_name,
                                    "full_id": f"{session_name}:{window_name}",
                                    "is_active": is_active,
                                }
                            )

        except subprocess.TimeoutExpired:
            logger.warning("tmux list-sessions timed out")
        except FileNotFoundError:
            logger.warning("tmux not found - fleet monitoring unavailable")
        except Exception as e:
            logger.error(f"Error listing tmux sessions: {e}")

        return sessions

    @staticmethod
    def capture_output(target: str, lines: int = 100) -> str:
        """Capture terminal output from a tmux pane."""
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", target, "-p", "-S", f"-{lines}"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                return result.stdout
        except subprocess.TimeoutExpired:
            logger.warning(f"tmux capture-pane timed out for {target}")
        except Exception as e:
            logger.error(f"Error capturing output from {target}: {e}")

        return ""

    @staticmethod
    def get_session_time(target: str) -> datetime | None:
        """Get session creation time from tmux."""
        try:
            # Get session activity time
            result = subprocess.run(
                ["tmux", "display-message", "-t", target, "-p", "#{session_activity}"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0 and result.stdout.strip():
                # tmux returns epoch time in seconds
                epoch_seconds = int(result.stdout.strip())
                return datetime.fromtimestamp(epoch_seconds, tz=UTC)

        except (subprocess.TimeoutExpired, ValueError, Exception) as e:
            logger.debug(f"Could not get session time for {target}: {e}")

        return None

    @classmethod
    def detect_agent_type(cls, session_name: str, window_name: str) -> str:
        """Detect agent type from session and window names."""
        combined = f"{session_name}:{window_name}".lower()

        # Check exact matches first
        for key, agent_type in cls.AGENT_TYPE_MAP.items():
            if key in combined:
                return agent_type

        return "unknown"

    @classmethod
    def detect_status(cls, output: str) -> str:
        """Detect agent status from terminal output."""
        if not output:
            return "unknown"

        output_lower = output.lower()

        # Check error patterns first (highest priority)
        for error_pattern in cls.STATUS_PATTERNS["error"]:
            if error_pattern in output_lower:
                return "error"

        # Check active patterns
        for active_pattern in cls.STATUS_PATTERNS["active"]:
            if active_pattern in output_lower:
                return "active"

        # Check idle patterns
        for idle_pattern in cls.STATUS_PATTERNS["idle"]:
            if idle_pattern in output_lower:
                return "idle"

        return "unknown"


def get_fleet_status() -> FleetStatus:
    """Get current fleet status - CANONICAL IMPLEMENTATION.

    Uses TmuxInterface for tmux interactions and FleetAgent for agent data.
    This is the recommended function for new code.

    Returns:
        FleetStatus with complete fleet information
    """
    logger.debug("Fetching fleet status (canonical implementation)")

    agents = []
    now = datetime.now(UTC)
    stale_threshold = timedelta(minutes=30)

    # Get all tmux sessions
    sessions = TmuxInterface.list_sessions()

    for session in sessions:
        target = session["full_id"]

        # Capture output for context estimation and status detection
        output = TmuxInterface.capture_output(target, lines=50)
        context_estimate = len(output)

        # Detect agent type
        agent_type = TmuxInterface.detect_agent_type(
            session["session_name"], session["window_name"]
        )

        # Detect status
        status = TmuxInterface.detect_status(output)

        # Get last activity time
        last_activity = TmuxInterface.get_session_time(target)

        # Check if stale
        is_stale = False
        if last_activity:
            is_stale = (now - last_activity) > stale_threshold

        agent = FleetAgent(
            name=target,
            session_id=session["session_name"],
            agent_type=agent_type,
            last_activity=last_activity,
            context_estimate=context_estimate,
            is_stale=is_stale,
            window_name=session["window_name"],
            status=status,
        )

        agents.append(agent)

    logger.debug(f"Found {len(agents)} agents, {sum(1 for a in agents if a.is_stale)} stale")
    return FleetStatus.from_agents(agents)


def watch_fleet(interval_seconds: int = 10, max_iterations: int | None = None):
    """Watch fleet status in a loop, printing updates."""
    iteration = 0

    try:
        while True:
            if max_iterations and iteration >= max_iterations:
                break

            status = get_fleet_status()

            # Clear screen and print status
            print("\033[2J\033[H")  # ANSI escape codes to clear screen and move cursor to top
            print(f"FORGE Fleet Status - {status.timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            print("=" * 70)
            print(
                f"Total Agents: {status.total_agents} | Active: {status.active_agents} | Stale: {status.stale_agents}"
            )
            print()

            if not status.agents:
                print("No active agents found.")
            else:
                # Sort by is_stale (stale first), then by name
                sorted_agents = sorted(status.agents, key=lambda a: (-a.is_stale, a.name))

                for agent in sorted_agents:
                    stale_marker = " ⚠️ STALE" if agent.is_stale else ""
                    activity = (
                        agent.last_activity.strftime("%H:%M:%S")
                        if agent.last_activity
                        else "Unknown"
                    )
                    context_kb = agent.context_estimate // 1024

                    print(
                        f"{agent.name:<30} [{agent.agent_type:<8}] {activity} {context_kb:3d}KB{stale_marker}"
                    )
                    print(f"  Status: {agent.status}")

                    # Show snippet of recent activity if available
                    if agent.context_estimate > 0:
                        output = TmuxInterface.capture_output(agent.name, lines=3)
                        if output.strip():
                            # Get last non-empty line
                            lines = [l.strip() for l in output.split("\n") if l.strip()]
                            if lines:
                                print(f"  Recent: {lines[-1][:80]}...")
                    print()

            if not max_iterations:
                print(f"Refreshing every {interval_seconds}s... (Ctrl+C to stop)")
            else:
                print(f"Iteration {iteration + 1}/{max_iterations}")

            iteration += 1
            time.sleep(interval_seconds)

    except KeyboardInterrupt:
        print("\nFleet monitoring stopped.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="FORGE Fleet Health Dashboard")
    parser.add_argument("--watch", "-w", action="store_true", help="Watch mode with auto-refresh")
    parser.add_argument(
        "--interval", "-i", type=int, default=10, help="Refresh interval in seconds (default: 10)"
    )
    parser.add_argument(
        "--count", "-c", type=int, help="Number of updates before stopping (watch mode only)"
    )

    args = parser.parse_args()

    if args.watch:
        watch_fleet(interval_seconds=args.interval, max_iterations=args.count)
    else:
        status = get_fleet_status()
        print(f"FORGE Fleet Status - {status.timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(
            f"Total: {status.total_agents}, Active: {status.active_agents}, Stale: {status.stale_agents}"
        )

        for agent in status.agents:
            print(f"  {agent.name}: {agent.agent_type} ({agent.status})")
