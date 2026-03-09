"""
FORGE Harness Context Rotation
==============================

Monitors agent context usage and rotates to fresh instances when needed.

Usage:
    from forge_harness.fleet.context_rotation import ContextRotator, RotationResult

    rotator = ContextRotator()

    # Check if rotation needed
    if rotator.should_rotate("forge:tech", threshold=70):
        result = rotator.rotate_agent("forge:tech", task_context={
            "task": "Implement feature X",
            "files": ["src/main.py"],
        })
        print(f"Rotated: {result.success}")
"""

import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

# Claude Code default context budget
DEFAULT_MAX_TOKENS = 200_000
CHARS_PER_TOKEN = 4  # Rough heuristic


class RotationStatus(str, Enum):
    """Status of a rotation operation."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"  # Already below threshold


@dataclass
class RotationResult:
    """Result of a rotation operation."""

    session_id: str
    status: RotationStatus
    new_session_id: str | None = None
    old_context_pct: int = 0
    handoff_prompt: str = ""
    error: str | None = None
    duration: float = 0.0

    @property
    def success(self) -> bool:
        return self.status == RotationStatus.SUCCESS

    @property
    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [f"Rotation: {self.session_id}"]
        lines.append(f"Status: {self.status.value}")
        if self.new_session_id:
            lines.append(f"New session: {self.new_session_id}")
        lines.append(f"Old context: {self.old_context_pct}%")
        if self.error:
            lines.append(f"Error: {self.error}")
        lines.append(f"Duration: {self.duration:.1f}s")
        return "\n".join(lines)


@dataclass
class TaskContext:
    """Context to transfer during rotation."""

    task: str
    description: str = ""
    files: list[str] = field(default_factory=list)
    progress: str = ""
    notes: str = ""
    test_command: str | None = None

    def to_handoff_prompt(self) -> str:
        """Generate handoff prompt for new agent."""
        lines = [
            "# Context Rotation Handoff",
            "",
            "You are continuing work from a previous agent session that ran low on context.",
            "",
            "## Task",
            self.task,
        ]

        if self.description:
            lines.extend(["", "## Description", self.description])

        if self.progress:
            lines.extend(["", "## Progress So Far", self.progress])

        if self.files:
            lines.extend(["", "## Files to Review"])
            for f in self.files:
                lines.append(f"- `{f}`")

        if self.notes:
            lines.extend(["", "## Notes", self.notes])

        if self.test_command:
            lines.extend(["", "## Test Command", "```bash", self.test_command, "```"])

        lines.extend(
            [
                "",
                "---",
                "Please continue the task. Start by reviewing the mentioned files to understand the current state.",
            ]
        )

        return "\n".join(lines)


class LegacyContextRotator:
    """Monitors and rotates agents based on context usage."""

    def __init__(
        self,
        session_prefix: str = "forge",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        chars_per_token: int = CHARS_PER_TOKEN,
    ):
        """Initialize context rotator.

        Args:
            session_prefix: Tmux session prefix (default "forge")
            max_tokens: Maximum context tokens (default 200k)
            chars_per_token: Characters per token estimate (default 4)
        """
        self.session_prefix = session_prefix
        self.max_tokens = max_tokens
        self.chars_per_token = chars_per_token
        self._rotating_sessions: set[str] = set()  # Sessions being rotated

    def _normalize_session_id(self, session_id: str) -> str:
        """Normalize session ID to full tmux target."""
        if ":" not in session_id:
            return f"{self.session_prefix}:{session_id}"
        return session_id

    def _capture_pane(self, session_id: str, lines: int = 1000) -> str | None:
        """Capture full pane content for context estimation."""
        target = self._normalize_session_id(session_id)
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", target, "-S", "-"],  # All scrollback
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return result.stdout
            logger.warning(f"tmux capture failed for {target}: {result.stderr}")
            return None
        except subprocess.TimeoutExpired:
            logger.error(f"tmux capture timed out for {target}")
            return None
        except FileNotFoundError:
            logger.error("tmux not found")
            return None
        except Exception as e:
            logger.error(f"Error capturing pane {target}: {e}")
            return None

    def estimate_context_usage(self, session_id: str) -> int:
        """Estimate context usage percentage.

        Uses heuristic: chars / 4 = tokens, compare to max context.

        Args:
            session_id: Tmux session/window target

        Returns:
            Estimated context usage percentage (0-100)
        """
        content = self._capture_pane(session_id)
        if not content:
            return 0

        # Count characters
        char_count = len(content)

        # Estimate tokens
        estimated_tokens = char_count // self.chars_per_token

        # Calculate percentage
        percentage = (estimated_tokens / self.max_tokens) * 100

        # Clamp to 0-100
        return min(100, max(0, int(percentage)))

    def should_rotate(self, session_id: str, threshold: int = 70) -> bool:
        """Check if session should be rotated.

        Args:
            session_id: Tmux session/window target
            threshold: Context percentage threshold (default 70)

        Returns:
            True if context usage exceeds threshold
        """
        # Don't rotate if already in rotation
        normalized = self._normalize_session_id(session_id)
        if normalized in self._rotating_sessions:
            logger.debug(f"{session_id} already rotating, skipping check")
            return False

        usage = self.estimate_context_usage(session_id)
        should = usage >= threshold

        if should:
            logger.info(f"{session_id} at {usage}% context, above {threshold}% threshold")
        else:
            logger.debug(f"{session_id} at {usage}% context, below {threshold}% threshold")

        return should

    def _generate_new_window_name(self, old_session_id: str) -> str:
        """Generate new window name for rotated agent."""
        # Extract window name from session:window format
        if ":" in old_session_id:
            window_name = old_session_id.split(":")[1]
        else:
            window_name = old_session_id

        # Append rotation counter or timestamp
        timestamp = datetime.now().strftime("%H%M")
        return f"{window_name}-r{timestamp}"

    def _spawn_new_window(self, new_window_name: str) -> bool:
        """Spawn a new tmux window for the rotated agent.

        Args:
            new_window_name: Name for the new window

        Returns:
            True if successful
        """
        try:
            result = subprocess.run(
                [
                    "tmux",
                    "new-window",
                    "-t",
                    self.session_prefix,
                    "-n",
                    new_window_name,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info(f"Created new window: {self.session_prefix}:{new_window_name}")
                return True
            logger.error(f"Failed to create window: {result.stderr}")
            return False
        except Exception as e:
            logger.error(f"Error creating window: {e}")
            return False

    def _send_to_session(self, session_id: str, text: str) -> bool:
        """Send text to a tmux session.

        Args:
            session_id: Tmux target
            text: Text to send

        Returns:
            True if successful
        """
        target = self._normalize_session_id(session_id)
        try:
            # Escape special characters for tmux
            escaped = text.replace("'", "'\\''")

            result = subprocess.run(
                ["tmux", "send-keys", "-t", target, "-l", escaped],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                # Send Enter to submit
                subprocess.run(
                    ["tmux", "send-keys", "-t", target, "Enter"],
                    capture_output=True,
                    timeout=5,
                )
                return True
            logger.error(f"Failed to send keys: {result.stderr}")
            return False
        except Exception as e:
            logger.error(f"Error sending to session: {e}")
            return False

    def _start_claude_in_window(self, window_name: str) -> bool:
        """Start Claude Code in the new window.

        Args:
            window_name: Window name (without session prefix)

        Returns:
            True if successful
        """
        target = f"{self.session_prefix}:{window_name}"
        try:
            result = subprocess.run(
                ["tmux", "send-keys", "-t", target, "claude", "Enter"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Error starting Claude: {e}")
            return False

    def _mark_session_rotating(self, session_id: str, rotating: bool = True) -> None:
        """Mark a session as rotating out."""
        normalized = self._normalize_session_id(session_id)
        if rotating:
            self._rotating_sessions.add(normalized)
        else:
            self._rotating_sessions.discard(normalized)

    def rotate_agent(
        self,
        session_id: str,
        task_context: dict | None = None,
    ) -> RotationResult:
        """Rotate an agent to a fresh instance.

        Args:
            session_id: Tmux session/window target
            task_context: Dict with task, files, progress, notes, test_command

        Returns:
            RotationResult with operation outcome
        """
        start_time = time.time()
        self._normalize_session_id(session_id)

        # Get current context usage
        old_context_pct = self.estimate_context_usage(session_id)

        # Mark as rotating
        self._mark_session_rotating(session_id, True)

        try:
            # Generate new window name
            new_window_name = self._generate_new_window_name(session_id)
            new_session_id = f"{self.session_prefix}:{new_window_name}"

            # Build task context
            if task_context:
                ctx = TaskContext(**task_context)
            else:
                ctx = TaskContext(
                    task="Continue the previous task",
                    notes="Context rotation - previous agent ran low on context",
                )

            handoff_prompt = ctx.to_handoff_prompt()

            # Create new window
            if not self._spawn_new_window(new_window_name):
                return RotationResult(
                    session_id=session_id,
                    status=RotationStatus.FAILED,
                    old_context_pct=old_context_pct,
                    error="Failed to create new tmux window",
                    duration=time.time() - start_time,
                )

            # Wait for window to be ready
            time.sleep(1)

            # Start Claude in new window
            if not self._start_claude_in_window(new_window_name):
                return RotationResult(
                    session_id=session_id,
                    status=RotationStatus.FAILED,
                    new_session_id=new_session_id,
                    old_context_pct=old_context_pct,
                    error="Failed to start Claude in new window",
                    duration=time.time() - start_time,
                )

            # Wait for Claude to initialize
            time.sleep(3)

            # Send handoff prompt to new agent
            if not self._send_to_session(new_session_id, handoff_prompt):
                return RotationResult(
                    session_id=session_id,
                    status=RotationStatus.FAILED,
                    new_session_id=new_session_id,
                    old_context_pct=old_context_pct,
                    handoff_prompt=handoff_prompt,
                    error="Failed to send handoff prompt",
                    duration=time.time() - start_time,
                )

            logger.info(f"Successfully rotated {session_id} -> {new_session_id}")

            return RotationResult(
                session_id=session_id,
                status=RotationStatus.SUCCESS,
                new_session_id=new_session_id,
                old_context_pct=old_context_pct,
                handoff_prompt=handoff_prompt,
                duration=time.time() - start_time,
            )

        except Exception as e:
            logger.exception(f"Error rotating {session_id}")
            return RotationResult(
                session_id=session_id,
                status=RotationStatus.FAILED,
                old_context_pct=old_context_pct,
                error=str(e),
                duration=time.time() - start_time,
            )
        finally:
            # Keep old session marked as rotating (don't clear)
            pass


# Convenience function
def create_rotator() -> "ContextRotator":
    """Create a context rotator with default settings."""
    return ContextRotator()


"""
Context Rotation System for FORGE Fleet
========================================

Automatically monitors agent context usage and triggers rotation/handoff
when context approaches 70% of capacity (~140K tokens).

Usage:
    from forge_harness.fleet.context_rotation import ContextRotator

    rotator = ContextRotator()

    # Check if rotation needed
    if rotator.should_rotate("forge:tech"):
        rotator.rotate_agent("forge:tech")

    # Manual context check
    usage = rotator.estimate_context_usage("forge:tech")
    print(f"Context usage: {usage:.1%}")
"""

import json
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from ..handoff_generator import HandoffGenerator
from ..logging_config import get_logger

logger = get_logger(__name__)

# Context window and rotation thresholds
CONTEXT_WINDOW_TOKENS = 200_000  # Claude Sonnet 4.5 context window
ROTATION_THRESHOLD = 0.7  # Rotate at 70% usage (~140K tokens)
TOKENS_PER_LINE = 100  # Rough heuristic: 1 line ≈ 100 tokens


@dataclass
class ContextStats:
    """Statistics about agent context usage."""

    session_name: str
    estimated_lines: int
    estimated_tokens: int
    context_percent: float
    should_rotate: bool
    last_checked: str


class FleetContextRotator:
    """Manages context rotation for FORGE fleet agents."""

    def __init__(
        self,
        forge_root: Path | None = None,
        rotation_threshold: float = ROTATION_THRESHOLD,
        tokens_per_line: int = TOKENS_PER_LINE,
    ):
        """Initialize context rotator.

        Args:
            forge_root: Path to FORGE repository root
            rotation_threshold: Context percentage to trigger rotation (default: 0.7)
            tokens_per_line: Estimated tokens per line of output (default: 100)
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
        self.rotation_threshold = rotation_threshold
        self.tokens_per_line = tokens_per_line
        self.fleet_dir = self.forge_root / ".forge/fleet"
        self.handoff_generator = HandoffGenerator(
            forge_root=forge_root,
            context_threshold=int(rotation_threshold * 100),
        )

        # Ensure fleet directory exists
        self.fleet_dir.mkdir(exist_ok=True)

    def get_tmux_pane_lines(self, session_name: str) -> int:
        """Get number of lines in a tmux pane's scrollback buffer.

        Args:
            session_name: tmux session name (e.g., "forge:tech")

        Returns:
            Number of lines in scrollback buffer

        Raises:
            subprocess.CalledProcessError: If tmux command fails
        """
        try:
            # Get scrollback buffer history limit
            result = subprocess.run(
                ["tmux", "display-message", "-p", "-t", session_name, "#{history_size}"],
                capture_output=True,
                text=True,
                check=True,
            )
            history_size = int(result.stdout.strip())

            # Get number of lines currently in buffer
            result = subprocess.run(
                ["tmux", "display-message", "-p", "-t", session_name, "#{history_limit}"],
                capture_output=True,
                text=True,
                check=True,
            )
            history_limit = int(result.stdout.strip())

            # Use the actual history size (capped at limit)
            lines = min(history_size, history_limit)
            logger.debug(f"Session {session_name}: {lines} lines in scrollback")
            return lines

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get tmux pane lines for {session_name}: {e}")
            raise
        except (ValueError, IndexError) as e:
            logger.error(f"Failed to parse tmux output for {session_name}: {e}")
            return 0

    def estimate_context_usage(self, session_name: str) -> float:
        """Estimate context usage as percentage based on output size.

        Uses heuristic: 1 line ≈ 100 tokens, 200K context window.

        Args:
            session_name: tmux session name (e.g., "forge:tech")

        Returns:
            Estimated context usage as float (0.0 to 1.0)
        """
        try:
            lines = self.get_tmux_pane_lines(session_name)
            estimated_tokens = lines * self.tokens_per_line
            context_percent = estimated_tokens / CONTEXT_WINDOW_TOKENS

            logger.info(
                f"Session {session_name}: ~{estimated_tokens:,} tokens "
                f"({context_percent:.1%} of context)"
            )

            return context_percent

        except Exception as e:
            logger.error(f"Failed to estimate context usage for {session_name}: {e}")
            return 0.0

    def should_rotate(
        self,
        session_name: str,
        threshold: float | None = None,
    ) -> bool:
        """Check if agent needs context rotation.

        Args:
            session_name: tmux session name (e.g., "forge:tech")
            threshold: Optional custom threshold (default: use instance threshold)

        Returns:
            True if agent should be rotated, False otherwise
        """
        if threshold is None:
            threshold = self.rotation_threshold

        context_usage = self.estimate_context_usage(session_name)
        should_rotate = context_usage >= threshold

        if should_rotate:
            logger.warning(
                f"Session {session_name} exceeds rotation threshold "
                f"({context_usage:.1%} >= {threshold:.1%})"
            )

        return should_rotate

    def get_context_stats(self, session_name: str) -> ContextStats:
        """Get detailed context statistics for an agent.

        Args:
            session_name: tmux session name (e.g., "forge:tech")

        Returns:
            ContextStats object with usage details
        """
        lines = self.get_tmux_pane_lines(session_name)
        tokens = lines * self.tokens_per_line
        percent = tokens / CONTEXT_WINDOW_TOKENS

        return ContextStats(
            session_name=session_name,
            estimated_lines=lines,
            estimated_tokens=tokens,
            context_percent=percent,
            should_rotate=percent >= self.rotation_threshold,
            last_checked=datetime.now(UTC).isoformat(),
        )

    def generate_handoff_prompt(self, session_name: str) -> str:
        """Generate handoff prompt with task state and next steps.

        Args:
            session_name: tmux session name (e.g., "forge:tech")

        Returns:
            Markdown handoff prompt content

        Raises:
            FileNotFoundError: If fleet state not found
        """
        # Load fleet state
        state_file = self.fleet_dir / "state.json"
        if not state_file.exists():
            raise FileNotFoundError(
                f"Fleet state not found: {state_file}\nRun 'forge-harness fleet save' first."
            )

        with open(state_file) as f:
            state = json.load(f)

        # Find agent data
        agent_data = None
        agent_id = None
        for aid, data in state.get("agents", {}).items():
            session = data.get("session", "")
            window = data.get("window", "")
            # Match exact session:window or just session name
            if f"{session}:{window}" == session_name:
                agent_data = data
                agent_id = aid
                break
            # Also match if session_name is just the session part and this is the first match
            if ":" not in session_name and session == session_name and agent_data is None:
                agent_data = data
                agent_id = aid
                # Don't break - keep looking for exact match

        if not agent_data:
            raise ValueError(f"No agent found for session: {session_name}")

        # Get context stats
        stats = self.get_context_stats(session_name)

        # Update agent data with current context
        agent_data["context_percent"] = f"{int(stats.context_percent * 100)}"
        agent_data["needs_handoff"] = True

        # Generate handoff prompt using HandoffGenerator
        prompt = self.handoff_generator.generate_handoff_prompt(agent_id, agent_data)

        # Add rotation-specific context
        rotation_header = f"""
---
**CONTEXT ROTATION TRIGGERED**

This handoff was automatically generated due to high context usage.

- **Estimated Context:** {stats.context_percent:.1%} ({stats.estimated_tokens:,} tokens)
- **Rotation Threshold:** {self.rotation_threshold:.1%}
- **Lines Analyzed:** {stats.estimated_lines:,}
- **Triggered At:** {stats.last_checked}

The agent will be rotated to a fresh session to prevent context overflow.
---

"""
        # Insert rotation header after the main header
        lines = prompt.split("\n")
        header_end = next((i for i, line in enumerate(lines) if line.startswith("## ")), 10)
        lines.insert(header_end, rotation_header)

        return "\n".join(lines)

    def spawn_fresh_agent(
        self,
        session_name: str,
        handoff_prompt: str,
    ) -> str:
        """Spawn a fresh agent session and transfer task.

        Args:
            session_name: Original session name (e.g., "forge:tech")
            handoff_prompt: Handoff prompt content

        Returns:
            New session name

        Raises:
            subprocess.CalledProcessError: If tmux commands fail
        """
        # Parse session components
        parts = session_name.split(":")
        if len(parts) == 2:
            session, window = parts
        else:
            session = parts[0]
            window = "main"

        # Create new session name with timestamp
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        new_session = f"{session}-{timestamp}"

        logger.info(f"Spawning fresh agent session: {new_session}")

        # Save handoff prompt to file
        handoff_dir = self.forge_root / ".forge/handoffs"
        handoff_dir.mkdir(exist_ok=True)
        handoff_file = handoff_dir / f"{session}_{window}_{timestamp}.md"

        with open(handoff_file, "w") as f:
            f.write(handoff_prompt)

        logger.info(f"Saved handoff prompt: {handoff_file}")

        # Create new tmux session
        try:
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", new_session],
                check=True,
            )
            logger.info(f"Created new tmux session: {new_session}")

            # Send handoff prompt to new session
            # Note: In real usage, this would trigger the agent with the handoff prompt
            # For now, we just create the session and log the handoff file path
            subprocess.run(
                ["tmux", "send-keys", "-t", new_session, f"# Handoff from {session_name}", "Enter"],
                check=True,
            )
            subprocess.run(
                ["tmux", "send-keys", "-t", new_session, f"# See handoff: {handoff_file}", "Enter"],
                check=True,
            )

            logger.info("Initialized new session with handoff reference")

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to spawn fresh agent: {e}")
            raise

        return new_session

    def retire_agent(self, session_name: str) -> None:
        """Gracefully retire old agent session.

        Args:
            session_name: Session name to retire (e.g., "forge:tech")

        Raises:
            subprocess.CalledProcessError: If tmux command fails
        """
        logger.info(f"Retiring agent session: {session_name}")

        try:
            # Send graceful shutdown message
            subprocess.run(
                [
                    "tmux",
                    "send-keys",
                    "-t",
                    session_name,
                    "# Context rotation complete - retiring session",
                    "Enter",
                ],
                check=True,
            )

            # Kill the session
            subprocess.run(
                ["tmux", "kill-session", "-t", session_name],
                check=True,
            )

            logger.info(f"Retired session: {session_name}")

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to retire agent {session_name}: {e}")
            raise

    def rotate_agent(self, session_name: str) -> tuple[str, Path]:
        """Perform full context rotation: new session, transfer, retire old.

        This is the main entry point for context rotation.

        Args:
            session_name: Session name to rotate (e.g., "forge:tech")

        Returns:
            Tuple of (new_session_name, handoff_file_path)

        Raises:
            ValueError: If rotation conditions not met
            FileNotFoundError: If fleet state not found
        """
        logger.info(f"Starting context rotation for: {session_name}")

        # Check if rotation is needed
        if not self.should_rotate(session_name):
            raise ValueError(
                f"Session {session_name} does not need rotation "
                f"(context below {self.rotation_threshold:.1%} threshold)"
            )

        # Generate handoff prompt
        handoff_prompt = self.generate_handoff_prompt(session_name)

        # Spawn fresh agent
        new_session = self.spawn_fresh_agent(session_name, handoff_prompt)

        # Get handoff file path
        parts = session_name.split(":")
        session = parts[0]
        window = parts[1] if len(parts) == 2 else "main"
        # Extract full timestamp from new_session (e.g., "forge-20260204-173417" -> "20260204-173417")
        timestamp = "-".join(new_session.split("-")[1:])
        handoff_file = self.forge_root / ".forge/handoffs" / f"{session}_{window}_{timestamp}.md"

        # Retire old agent (optional - can be done manually)
        # For safety, we might want to keep the old session around temporarily
        logger.info(
            f"Context rotation complete. Old session: {session_name}, New session: {new_session}"
        )
        logger.info(f"To retire old session manually: tmux kill-session -t {session_name}")

        return new_session, handoff_file


def create_context_rotator(
    forge_root: Path | None = None,
    rotation_threshold: float = ROTATION_THRESHOLD,
) -> "ContextRotator":
    """Factory function to create ContextRotator instance.

    Args:
        forge_root: Path to FORGE repository root
        rotation_threshold: Context percentage to trigger rotation

    Returns:
        Configured ContextRotator instance
    """
    return ContextRotator(
        forge_root=forge_root,
        rotation_threshold=rotation_threshold,
    )


class ContextRotator:
    """Facade for legacy and fleet context rotators."""

    def __init__(
        self,
        session_prefix: str = "forge",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        chars_per_token: int = CHARS_PER_TOKEN,
        forge_root: Path | None = None,
        rotation_threshold: float = ROTATION_THRESHOLD,
        tokens_per_line: int = TOKENS_PER_LINE,
    ):
        if forge_root is None:
            self._impl = LegacyContextRotator(
                session_prefix=session_prefix,
                max_tokens=max_tokens,
                chars_per_token=chars_per_token,
            )
        else:
            self._impl = FleetContextRotator(
                forge_root=forge_root,
                rotation_threshold=rotation_threshold,
                tokens_per_line=tokens_per_line,
            )

    def __getattr__(self, name: str):
        return getattr(self._impl, name)
