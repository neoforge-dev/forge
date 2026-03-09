"""CLI readiness detection for tmux agents.

Detects whether an LLM CLI (Claude, Codex, Gemini) is ready to receive input
by analyzing tmux pane content and process state.

Architecture:
    1. Check process aliveness via tmux list-panes
    2. Analyze pane content for CLI-specific prompts
    3. Score confidence (0.0-1.0) based on indicators
    4. Return readiness state with confidence level

CLI Detection Patterns:
    - Claude Code: "INSERT", "Claude Code", "❯", "shortcuts"
    - OpenAI Codex: "›", "context left", "OpenAI Codex"
    - Gemini: "╭", "╰", "Type your message", "YOLO mode"

Usage:
    ```python
    from forge_harness.fleet.readiness import check_readiness, CLIState

    state = check_readiness("forge:tech", "claude")
    if state.state == CLIState.READY and state.confidence > 0.8:
        # Safe to send message
        pass
    ```
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import Enum

from ..logging_config import get_logger

logger = get_logger(__name__)

# =============================================================================
# CLI State
# =============================================================================


class CLIState(str, Enum):
    """CLI readiness states."""

    READY = "ready"  # CLI is ready to receive input
    BUSY = "busy"  # CLI is processing a request
    STARTING = "starting"  # CLI is initializing
    CRASHED = "crashed"  # CLI process is dead
    UNKNOWN = "unknown"  # Cannot determine state
    CONTEXT_EXHAUSTED = "context_exhausted"  # CLI context nearly full


# =============================================================================
# CLI Detection Patterns
# =============================================================================

# Readiness indicators (CLI is waiting for input)
READY_PATTERNS = {
    "claude": [
        r"INSERT",  # Vim-style insert mode
        r"❯\s*$",  # Prompt at end of line
        r"shortcuts",  # Help text visible
        r"Claude Code",  # CLI name in output
        r"Try \"",  # Suggestion prompt
    ],
    "codex": [
        r"›",  # Codex prompt
        r"context left",  # Token counter
        r"OpenAI Codex",  # CLI name
        r"Type your message",
    ],
    "gemini": [
        r"╭",  # Box drawing characters
        r"╰",
        r"Type your message",
        r"Gemini",  # CLI name
        r"YOLO mode",  # Gemini's danger mode
    ],
}

# Busy indicators (CLI is working)
# NOTE: Patterns should be specific to avoid false positives
# Avoid matching static UI elements like "context: 70.0%"
BUSY_PATTERNS = [
    r"Working\.\.\.",  # More specific - with ellipsis
    r"Composing",
    r"Running\s+\w+",  # "Running tests", not just "Running"
    r"Implementing",
    r"Writing\s+\w+",  # "Writing code", not just "Writing"
    r"Executing",
    r"Testing",
    r"Building",
    r"🌗",  # Moon emoji = thinking
    r"Thinking\.\.\.",  # More specific
    r"Reading\s+file",  # More specific - "Reading file", not history
    r"Searching\s+for",  # More specific
    r"Bash\s+tool",  # Tool execution, not just word "bash"
    r"Discombobulating",
    r"Deciphering",
    r"Billowing",
    r"Ideating",
    r"Explored\s+\d+",  # "Explored 5 files"
    r"Constructing",
    r"Worked for\s+\d+",  # "Worked for 30 seconds"
    r"\d+k?\s+tokens?\s+used",  # Token usage: "45k tokens used", not "context: 70%"
    r"⏳",  # Hourglass emoji
    r"Processing",
    r"Analyzing",
]

# Starting indicators (CLI is initializing)
STARTING_PATTERNS = [
    r"Initializing",
    r"Loading",
    r"Starting",
    r"Welcome to",
    r"Connecting",
]

# Shell prompt indicators (no CLI running)
SHELL_PATTERNS = [
    r"^\s*[\$\#\%][\s>]*$",  # $ # % prompts
    r"^\s*[❯➜]\s*$",  # Fancy shell prompts
    r"bash-[0-9]",  # bash version prompt
    r"zsh:",  # zsh prompt
]

# =============================================================================
# Readiness Result
# =============================================================================


@dataclass
class ReadinessResult:
    """Result of readiness check.

    Attributes:
        state: Current CLI state
        confidence: Confidence score (0.0-1.0)
        cli_type: Detected CLI type (claude, codex, gemini, shell, unknown)
        indicators: List of matched indicators
        pane_alive: Whether tmux pane process is alive
        raw_content: Last N lines of pane content (for debugging)
    """

    state: CLIState
    confidence: float
    cli_type: str
    indicators: list[str]
    pane_alive: bool
    raw_content: str | None = None
    context_percent: int | None = None

    def is_ready(self, min_confidence: float = 0.7) -> bool:
        """Check if CLI is ready with minimum confidence.

        Args:
            min_confidence: Minimum confidence threshold (default 0.7)

        Returns:
            True if state is READY and confidence meets threshold
        """
        return self.state == CLIState.READY and self.confidence >= min_confidence


# =============================================================================
# Readiness Detection
# =============================================================================


def check_pane_alive(target: str) -> bool:
    """Check if tmux pane process is alive.

    Args:
        target: Tmux target (session:window or session:window.pane)

    Returns:
        True if pane exists and has a running process
    """
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-t", target, "-F", "#{pane_pid}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            logger.debug(f"Pane {target} not found")
            return False

        pid = result.stdout.strip()
        if not pid:
            return False

        # Check if process is alive
        # Use ps to verify process exists
        ps_result = subprocess.run(
            ["ps", "-p", pid],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return ps_result.returncode == 0

    except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        logger.warning(f"Failed to check pane alive: {e}")
        return False


def get_pane_content(target: str, lines: int = 30) -> str | None:
    """Get content from tmux pane.

    Args:
        target: Tmux target (session:window or session:window.pane)
        lines: Number of lines to capture from history

    Returns:
        Pane content as string, or None if failed
    """
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", target, "-p", "-S", f"-{lines}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            logger.warning(f"Failed to capture pane {target}")
            return None

        return result.stdout

    except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        logger.warning(f"Failed to get pane content: {e}")
        return None


def detect_cli_type(content: str) -> str:
    """Detect CLI type from pane content.

    Args:
        content: Pane content to analyze

    Returns:
        CLI type: "claude", "codex", "gemini", "shell", or "unknown"
    """
    if not content:
        return "unknown"

    # Check each CLI type
    for cli_type, patterns in READY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                return cli_type

    # Check for shell prompt
    for pattern in SHELL_PATTERNS:
        if re.search(pattern, content, re.MULTILINE):
            return "shell"

    return "unknown"


def detect_context_percent(content: str) -> int | None:
    """Detect context usage percentage from pane content.

    Parses context usage from CLI-specific output formats:
        - Claude Code: "Context left until auto-compact: 5%" → 95 (used)
        - Kimi CLI:    "context: 17.3%"                     → 17 (used)
        - Pi CLI:      "60.0%/205k (auto)"                  → 60 (used)
        - Gemini CLI:  no context percentage visible         → None

    Args:
        content: Pane content to analyze

    Returns:
        Integer percentage of context USED (0-100), or None if not detectable.
    """
    if not content:
        return None

    # Claude Code: "Context left until auto-compact: N%" → N% LEFT means (100-N)% used
    claude_match = re.search(
        r"Context left until auto-compact:\s*(\d+(?:\.\d+)?)%",
        content,
        re.IGNORECASE,
    )
    if claude_match:
        left = float(claude_match.group(1))
        return min(100, max(0, round(100 - left)))

    # Kimi CLI: "context: N%" → N% used directly
    kimi_match = re.search(
        r"\bcontext:\s*(\d+(?:\.\d+)?)%",
        content,
        re.IGNORECASE,
    )
    if kimi_match:
        used = float(kimi_match.group(1))
        return min(100, max(0, round(used)))

    # Pi CLI: "N%/XYZk (auto)" or "N%/XYZ (auto)" → first percentage is used
    pi_match = re.search(
        r"(\d+(?:\.\d+)?)%\s*/\s*\d+[kKmM]?\s*\(auto\)",
        content,
        re.IGNORECASE,
    )
    if pi_match:
        used = float(pi_match.group(1))
        return min(100, max(0, round(used)))

    # Gemini CLI: no context percentage — return None
    return None


def score_readiness(content: str, cli_type: str, include_raw: bool = False) -> ReadinessResult:
    """Score CLI readiness based on pane content.

    Args:
        content: Pane content to analyze
        cli_type: Expected CLI type (or "auto" to detect)
        include_raw: Include raw content in result (for debugging)

    Returns:
        ReadinessResult with state and confidence score
    """
    if not content:
        return ReadinessResult(
            state=CLIState.UNKNOWN,
            confidence=0.0,
            cli_type="unknown",
            indicators=[],
            pane_alive=False,
            raw_content=content if include_raw else None,
        )

    # Auto-detect CLI type if not specified
    detected_cli = detect_cli_type(content) if cli_type == "auto" else cli_type

    indicators: list[str] = []
    confidence = 0.0

    # Check for busy indicators (highest priority)
    busy_matches = 0
    for pattern in BUSY_PATTERNS:
        if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
            busy_matches += 1
            indicators.append(f"busy:{pattern}")

    if busy_matches > 0:
        # Definitely busy
        confidence = min(0.9, 0.5 + (busy_matches * 0.1))
        return ReadinessResult(
            state=CLIState.BUSY,
            confidence=confidence,
            cli_type=detected_cli,
            indicators=indicators,
            pane_alive=True,
            raw_content=content if include_raw else None,
        )

    # Check for starting indicators
    starting_matches = 0
    for pattern in STARTING_PATTERNS:
        if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
            starting_matches += 1
            indicators.append(f"starting:{pattern}")

    if starting_matches > 0:
        confidence = min(0.8, 0.4 + (starting_matches * 0.2))
        return ReadinessResult(
            state=CLIState.STARTING,
            confidence=confidence,
            cli_type=detected_cli,
            indicators=indicators,
            pane_alive=True,
            raw_content=content if include_raw else None,
        )

    # Check for ready indicators
    ready_matches = 0
    if detected_cli in READY_PATTERNS:
        for pattern in READY_PATTERNS[detected_cli]:
            if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                ready_matches += 1
                indicators.append(f"ready:{pattern}")

    if ready_matches > 0:
        # CLI is ready — check context before returning READY
        context_pct = detect_context_percent(content)
        if context_pct is not None:
            indicators.append(f"context:{context_pct}%")

        if context_pct is not None and context_pct > 90:
            # Context nearly full — agent needs restart before it can do useful work
            confidence = min(0.95, 0.6 + (ready_matches * 0.15))
            return ReadinessResult(
                state=CLIState.CONTEXT_EXHAUSTED,
                confidence=confidence,
                cli_type=detected_cli,
                indicators=indicators,
                pane_alive=True,
                raw_content=content if include_raw else None,
                context_percent=context_pct,
            )

        # Confidence based on number of indicators
        confidence = min(0.95, 0.6 + (ready_matches * 0.15))
        return ReadinessResult(
            state=CLIState.READY,
            confidence=confidence,
            cli_type=detected_cli,
            indicators=indicators,
            pane_alive=True,
            raw_content=content if include_raw else None,
            context_percent=context_pct,
        )

    # Check if it's a shell prompt
    shell_matches = 0
    for pattern in SHELL_PATTERNS:
        if re.search(pattern, content, re.MULTILINE):
            shell_matches += 1
            indicators.append(f"shell:{pattern}")

    if shell_matches > 0:
        return ReadinessResult(
            state=CLIState.CRASHED,  # CLI not running, just shell
            confidence=0.8,
            cli_type="shell",
            indicators=indicators,
            pane_alive=True,
            raw_content=content if include_raw else None,
        )

    # Unknown state - not enough indicators
    return ReadinessResult(
        state=CLIState.UNKNOWN,
        confidence=0.2,
        cli_type=detected_cli,
        indicators=indicators,
        pane_alive=True,
        raw_content=content if include_raw else None,
    )


def check_readiness(
    target: str, cli_type: str = "auto", include_raw: bool = False
) -> ReadinessResult:
    """Check if CLI is ready to receive input.

    Args:
        target: Tmux target (session:window or session:window.pane)
        cli_type: Expected CLI type ("claude", "codex", "gemini", or "auto")
        include_raw: Include raw pane content in result (for debugging)

    Returns:
        ReadinessResult with state, confidence, and indicators

    Example:
        ```python
        result = check_readiness("forge:tech", "claude")
        if result.is_ready(min_confidence=0.8):
            # Safe to send message
            pass
        elif result.state == CLIState.BUSY:
            # Wait and retry
            pass
        elif result.state == CLIState.CRASHED:
            # Restart CLI
            pass
        ```
    """
    # Step 1: Check if pane is alive
    if not check_pane_alive(target):
        return ReadinessResult(
            state=CLIState.CRASHED,
            confidence=0.9,
            cli_type="unknown",
            indicators=["process_dead"],
            pane_alive=False,
            raw_content=None,
        )

    # Step 2: Get pane content
    content = get_pane_content(target, lines=30)
    if content is None:
        return ReadinessResult(
            state=CLIState.UNKNOWN,
            confidence=0.1,
            cli_type="unknown",
            indicators=["content_unavailable"],
            pane_alive=True,
            raw_content=None,
        )

    # Step 3: Score readiness
    return score_readiness(content, cli_type, include_raw=include_raw)


# =============================================================================
# Convenience Functions
# =============================================================================


def wait_for_ready(
    target: str,
    cli_type: str = "auto",
    timeout: int = 30,
    min_confidence: float = 0.7,
) -> bool:
    """Wait for CLI to become ready.

    Args:
        target: Tmux target
        cli_type: Expected CLI type
        timeout: Maximum wait time in seconds
        min_confidence: Minimum confidence threshold

    Returns:
        True if CLI became ready within timeout, False otherwise
    """
    import time

    start_time = time.time()
    while time.time() - start_time < timeout:
        result = check_readiness(target, cli_type)

        if result.is_ready(min_confidence=min_confidence):
            logger.info(
                f"CLI ready: {target} ({result.cli_type}, confidence={result.confidence:.2f})"
            )
            return True

        if result.state == CLIState.CRASHED:
            logger.error(f"CLI crashed: {target}")
            return False

        time.sleep(1)

    logger.warning(f"Timeout waiting for CLI ready: {target}")
    return False


def get_cli_command(cli_type: str) -> str | None:
    """Get the start command for a CLI type.

    Args:
        cli_type: CLI type (claude, codex, gemini, opencode, kimi, cursor)

    Returns:
        Start command string or None if unknown
    """
    commands = {
        "claude": "claude --dangerously-skip-permissions",
        "codex": "codex --dangerously-bypass-approvals-and-sandbox",
        "gemini": "gemini --yolo",
        "opencode": "opencode --dangerously-skip-permissions",
        "kimi": "kimi -y",
        "cursor": "cursor",
        "glm": "claude --dangerously-skip-permissions",
        "minimax": "claude --dangerously-skip-permissions",
        "pi": "pi",
    }
    return commands.get(cli_type.lower())


def restart_agent(target: str, cli_type: str) -> bool:
    """Restart an agent CLI by sending Ctrl-C, exit, then starting fresh CLI.

    Sends a graceful shutdown sequence to the tmux pane and then launches
    the appropriate CLI command for the given cli_type.

    Steps:
        1. Resolve CLI start command via get_cli_command()
        2. Fall back to window name inference if cli_type not recognized
        3. Interrupt current process: Ctrl-C
        4. Send /exit (Claude Code) or exit (shell fallback)
        5. Start fresh CLI

    Args:
        target: Tmux target (e.g., "forge:glm")
        cli_type: Detected CLI type to know what command to restart with

    Returns:
        True if restart command was sent successfully, False otherwise
    """
    import time

    # Resolve start command
    start_cmd = get_cli_command(cli_type)

    if start_cmd is None:
        # Try inferring from window name (the part after ":")
        window_name = target.split(":")[-1] if ":" in target else target
        start_cmd = get_cli_command(window_name)

    if start_cmd is None:
        logger.warning(
            f"Cannot restart {target}: unknown cli_type '{cli_type}' "
            f"and no matching command found."
        )
        return False

    # Determine exit command: Claude Code uses /exit; others use shell exit
    if "claude" in start_cmd or cli_type.lower() in ("claude", "glm", "minimax"):
        exit_cmd = "/exit"
    else:
        exit_cmd = "exit"

    try:
        # Step 1: Send Ctrl-C to interrupt any running operation
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "C-c", ""],
            check=False,
            timeout=5,
        )
        time.sleep(1)

        # Step 2: Send exit command + Enter to close the CLI
        subprocess.run(
            ["tmux", "send-keys", "-t", target, exit_cmd, "Enter"],
            check=False,
            timeout=5,
        )
        time.sleep(2)

        # Step 3: Start fresh CLI
        subprocess.run(
            ["tmux", "send-keys", "-t", target, start_cmd, "Enter"],
            check=False,
            timeout=5,
        )

        logger.info(f"Restart sequence sent to {target} (cmd: {start_cmd})")
        return True

    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as exc:
        logger.error(f"Failed to restart agent {target}: {exc}")
        return False
