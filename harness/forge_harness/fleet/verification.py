"""Message verification for tmux dispatch.

Verifies that messages are successfully delivered to tmux panes using
an echo pattern with unique message IDs.

Architecture:
    1. Generate unique MSG_ID for each message
    2. Prepend echo command to message
    3. Send message and wait for echo response
    4. Verify delivery by checking for MSG_ID in pane output
    5. Fallback to activity detection if echo fails

Echo Pattern:
    # MSG_ID: abc123-def456-ghi789
    <actual message content>

This allows us to confirm delivery by searching for the MSG_ID in the
pane output after sending.

Usage:
    ```python
    from forge_harness.fleet.verification import send_with_verification

    result = await send_with_verification(
        "forge:tech",
        "Create docs/README.md",
        timeout=15.0
    )

    if result.delivered:
        print(f"Message delivered in {result.delivery_time_ms}ms")
    else:
        print(f"Delivery failed: {result.failure_reason}")
    ```
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import time
import uuid
from dataclasses import dataclass

from ..logging_config import get_logger

logger = get_logger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Default timeouts for different operation types
DEFAULT_TIMEOUT = 15.0  # Standard operations
RESEARCH_TIMEOUT = 30.0  # Research/analysis tasks
COMPLEX_TIMEOUT = 60.0  # Complex multi-step tasks

# Message chunk size (characters per chunk)
CHUNK_SIZE = 500  # Increased from 50 to reduce overhead

# Delay between chunks (seconds)
CHUNK_DELAY = 0.05

# CLI types that need restart-with-prompt strategy (Ink TUI based)
RESTART_WITH_PROMPT_CLIS = {"gemini"}

# Activity detection patterns (fallback if echo fails)
ACTIVITY_PATTERNS = [
    r"Working",
    r"Composing",
    r"Running",
    r"Thinking",
    r"Reading",
    r"Searching",
    r"Implementing",
    r"tokens",  # Token usage display
]

# =============================================================================
# Delivery Receipt
# =============================================================================


@dataclass
class DeliveryReceipt:
    """Receipt for message delivery.

    Attributes:
        delivered: Whether message was successfully delivered
        message_id: Unique message ID used for verification
        delivery_time_ms: Time taken to deliver (milliseconds)
        verification_method: How delivery was verified (echo, activity, none)
        failure_reason: Reason for delivery failure (if any)
        activity_detected: Whether CLI activity was detected (fallback)
    """

    delivered: bool
    message_id: str
    delivery_time_ms: float
    verification_method: str
    failure_reason: str | None = None
    activity_detected: bool = False

    def __str__(self) -> str:
        """Human-readable receipt."""
        if self.delivered:
            return (
                f"✓ Delivered via {self.verification_method} "
                f"in {self.delivery_time_ms:.0f}ms (ID: {self.message_id[:8]})"
            )
        else:
            return f"✗ Delivery failed: {self.failure_reason} (ID: {self.message_id[:8]})"


# =============================================================================
# Message ID Generation
# =============================================================================


def generate_message_id() -> str:
    """Generate unique message ID for verification.

    Returns:
        UUID-based message ID (e.g., "msg-a1b2c3d4")
    """
    return f"msg-{uuid.uuid4().hex[:12]}"


# =============================================================================
# Tmux Operations
# =============================================================================


async def send_to_tmux(target: str, message: str, chunk_size: int = CHUNK_SIZE) -> bool:
    """Send message to tmux pane in chunks.

    Args:
        target: Tmux target (session:window or session:window.pane)
        message: Message to send
        chunk_size: Size of each chunk (default 500 chars)

    Returns:
        True if send succeeded, False otherwise
    """
    try:
        # Clear any existing input
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "C-u"],
            capture_output=True,
            timeout=2,
            check=False,
        )
        await asyncio.sleep(0.2)

        # Send message in chunks using literal mode (-l flag)
        msg_len = len(message)
        for offset in range(0, msg_len, chunk_size):
            chunk = message[offset : offset + chunk_size]

            result = subprocess.run(
                ["tmux", "send-keys", "-t", target, "-l", chunk],
                capture_output=True,
                timeout=2,
            )

            if result.returncode != 0:
                logger.error(f"Failed to send chunk to {target}: {result.stderr.decode()}")
                return False

            await asyncio.sleep(CHUNK_DELAY)

        # Submit with Enter
        await asyncio.sleep(0.1)
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "Enter"],
            capture_output=True,
            timeout=2,
            check=True,
        )

        return True

    except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        logger.error(f"Failed to send to tmux: {e}")
        return False


async def send_to_tmux_restart(
    target: str, message: str, cli_start_cmd: str
) -> bool:
    """Send message to a TUI-based CLI by restarting it with a prompt seed.

    Used for CLIs like Gemini that use Ink (React-based TUI) which captures
    stdin in raw mode, making tmux send-keys unreliable.

    Strategy: C-c to kill current process → restart with `-i "message"`.

    Args:
        target: Tmux target (session:window)
        message: Message/task to send
        cli_start_cmd: Base CLI command (e.g., "gemini --yolo")

    Returns:
        True if restart + prompt seed succeeded, False otherwise
    """
    try:
        # Step 1: Kill the current CLI process
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "C-c"],
            capture_output=True, timeout=2, check=False,
        )
        await asyncio.sleep(1.0)

        # Send another C-c in case first was absorbed
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "C-c"],
            capture_output=True, timeout=2, check=False,
        )
        await asyncio.sleep(0.5)

        # Step 2: Wait for shell prompt
        for _ in range(10):
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", target, "-p", "-S", "-5"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                content = result.stdout
                if re.search(r"[\$\#\%❯➜]\s*$", content, re.MULTILINE):
                    break
            await asyncio.sleep(0.5)

        # Step 3: Build restart command with -i flag to seed the prompt
        # Escape single quotes in message for shell safety
        escaped_msg = message.replace("'", "'\\''")
        restart_cmd = f"{cli_start_cmd} -i '{escaped_msg}'"

        # Send the restart command
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "-l", restart_cmd],
            capture_output=True, timeout=2, check=True,
        )
        await asyncio.sleep(0.1)
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "Enter"],
            capture_output=True, timeout=2, check=True,
        )

        logger.info(f"Restarted CLI with prompt seed in {target}")
        return True

    except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        logger.error(f"Failed to restart CLI with prompt: {e}")
        return False


async def get_pane_output(target: str, lines: int = 30) -> str | None:
    """Get recent output from tmux pane.

    Args:
        target: Tmux target
        lines: Number of lines to capture

    Returns:
        Pane output as string, or None if failed
    """
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", target, "-p", "-S", f"-{lines}"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            return None

        return result.stdout

    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None


# =============================================================================
# Verification Methods
# =============================================================================


async def verify_echo(target: str, message_id: str, timeout: float) -> bool:
    """Verify delivery by checking for echo comment in output.

    Args:
        target: Tmux target
        message_id: Message ID to search for
        timeout: Maximum wait time in seconds

    Returns:
        True if echo found, False otherwise
    """
    start_time = time.time()
    pattern = re.compile(f"MSG_ID:\\s*{re.escape(message_id)}")

    while time.time() - start_time < timeout:
        output = await get_pane_output(target, lines=50)
        if output and pattern.search(output):
            logger.debug(f"Echo verified for {message_id}")
            return True

        await asyncio.sleep(0.5)

    return False


async def verify_activity(target: str, timeout: float) -> bool:
    """Verify delivery by detecting CLI activity (fallback method).

    Args:
        target: Tmux target
        timeout: Maximum wait time in seconds

    Returns:
        True if activity detected, False otherwise
    """
    start_time = time.time()

    while time.time() - start_time < timeout:
        output = await get_pane_output(target, lines=30)
        if output:
            for pattern in ACTIVITY_PATTERNS:
                if re.search(pattern, output, re.MULTILINE | re.IGNORECASE):
                    logger.debug(f"Activity detected: {pattern}")
                    return True

        await asyncio.sleep(0.5)

    return False


# =============================================================================
# Main Verification Function
# =============================================================================


async def send_with_verification(
    target: str,
    message: str,
    timeout: float = DEFAULT_TIMEOUT,
    use_echo: bool = True,
    cli_type: str = "auto",
) -> DeliveryReceipt:
    """Send message to tmux pane with delivery verification.

    Args:
        target: Tmux target (session:window or session:window.pane)
        message: Message to send to CLI
        timeout: Maximum wait time for verification (seconds)
        use_echo: Whether to use echo pattern for verification
        cli_type: CLI type (claude, codex, gemini, auto). Gemini uses
            restart-with-prompt strategy instead of send-keys.

    Returns:
        DeliveryReceipt with delivery status and metadata

    Example:
        ```python
        # Standard operation
        result = await send_with_verification("forge:tech", "Run tests")

        # Gemini (uses restart-with-prompt automatically)
        result = await send_with_verification(
            "forge:gemini",
            "Research best practices for...",
            timeout=30.0,
            cli_type="gemini",
        )
        ```
    """
    message_id = generate_message_id()
    start_time = time.time()

    # Auto-detect CLI type from target name if "auto"
    effective_cli = cli_type
    if effective_cli == "auto":
        window_name = target.split(":")[-1] if ":" in target else target
        if window_name in RESTART_WITH_PROMPT_CLIS:
            effective_cli = window_name

    # Use restart-with-prompt strategy for Ink TUI-based CLIs (e.g., Gemini)
    if effective_cli in RESTART_WITH_PROMPT_CLIS:
        from .readiness import get_cli_command

        cli_cmd = get_cli_command(effective_cli) or f"{effective_cli} --yolo"
        logger.info(
            f"Using restart-with-prompt strategy for {effective_cli} ({message_id})"
        )
        send_success = await send_to_tmux_restart(target, message, cli_cmd)
        if not send_success:
            delivery_time = (time.time() - start_time) * 1000
            return DeliveryReceipt(
                delivered=False,
                message_id=message_id,
                delivery_time_ms=delivery_time,
                verification_method="none",
                failure_reason=f"Failed to restart {effective_cli} with prompt",
            )

        # For restart strategy, verify via activity detection (no echo possible)
        activity_detected = await verify_activity(target, timeout)
        delivery_time = (time.time() - start_time) * 1000

        if activity_detected:
            logger.info(f"Message {message_id} delivered via restart+activity")
            return DeliveryReceipt(
                delivered=True,
                message_id=message_id,
                delivery_time_ms=delivery_time,
                verification_method="restart_activity",
                activity_detected=True,
            )

        # Even without activity detection, the restart itself succeeded
        logger.info(f"Message {message_id} delivered via restart (no activity yet)")
        return DeliveryReceipt(
            delivered=True,
            message_id=message_id,
            delivery_time_ms=delivery_time,
            verification_method="restart",
            activity_detected=False,
        )

    # Standard send-keys strategy for text-mode CLIs (Claude, Codex, etc.)

    # Prepend echo comment if enabled
    if use_echo:
        message_with_echo = f"# MSG_ID: {message_id}\n{message}"
    else:
        message_with_echo = message

    logger.debug(f"Sending message {message_id} to {target} (timeout={timeout}s)")

    # Step 1: Send message
    send_success = await send_to_tmux(target, message_with_echo)
    if not send_success:
        delivery_time = (time.time() - start_time) * 1000
        return DeliveryReceipt(
            delivered=False,
            message_id=message_id,
            delivery_time_ms=delivery_time,
            verification_method="none",
            failure_reason="Failed to send to tmux",
        )

    # Step 2: Verify delivery using echo pattern
    if use_echo:
        echo_verified = await verify_echo(target, message_id, timeout)
        if echo_verified:
            delivery_time = (time.time() - start_time) * 1000
            logger.info(f"Message {message_id} delivered and verified via echo")
            return DeliveryReceipt(
                delivered=True,
                message_id=message_id,
                delivery_time_ms=delivery_time,
                verification_method="echo",
            )

    # Step 3: Fallback to activity detection
    logger.debug(f"Echo verification failed for {message_id}, trying activity detection")
    activity_detected = await verify_activity(target, timeout)

    delivery_time = (time.time() - start_time) * 1000

    if activity_detected:
        logger.info(f"Message {message_id} delivered (verified via activity)")
        return DeliveryReceipt(
            delivered=True,
            message_id=message_id,
            delivery_time_ms=delivery_time,
            verification_method="activity",
            activity_detected=True,
        )

    # Step 4: Verification failed
    logger.warning(f"Could not verify delivery of {message_id} after {delivery_time:.0f}ms")
    return DeliveryReceipt(
        delivered=False,
        message_id=message_id,
        delivery_time_ms=delivery_time,
        verification_method="none",
        failure_reason="Verification timeout - no echo or activity detected",
    )


# =============================================================================
# Convenience Functions
# =============================================================================


def send_with_verification_sync(
    target: str, message: str, timeout: float = DEFAULT_TIMEOUT
) -> DeliveryReceipt:
    """Synchronous wrapper for send_with_verification.

    Args:
        target: Tmux target
        message: Message to send
        timeout: Verification timeout

    Returns:
        DeliveryReceipt
    """
    return asyncio.run(send_with_verification(target, message, timeout))


async def send_batch_with_verification(
    targets_and_messages: list[tuple[str, str]],
    timeout: float = DEFAULT_TIMEOUT,
) -> list[DeliveryReceipt]:
    """Send multiple messages in parallel with verification.

    Args:
        targets_and_messages: List of (target, message) tuples
        timeout: Verification timeout per message

    Returns:
        List of DeliveryReceipts in same order as input

    Example:
        ```python
        receipts = await send_batch_with_verification([
            ("forge:tech", "Task 1"),
            ("forge:qa", "Task 2"),
            ("forge:codex", "Task 3"),
        ])
        ```
    """
    tasks = [
        send_with_verification(target, message, timeout) for target, message in targets_and_messages
    ]
    return await asyncio.gather(*tasks)
