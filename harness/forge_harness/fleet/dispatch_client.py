"""Unified dispatch client for tmux agents.

High-level API that integrates readiness detection, message verification,
retry logic, circuit breaker, and git mutex coordination.

Architecture:
    1. Check CLI readiness before dispatch
    2. Acquire git mutex if needed (prevent concurrent commits)
    3. Send message with verification
    4. Retry with exponential backoff on transient failures
    5. Track failures with circuit breaker
    6. Record metrics for observability

Usage:
    ```python
    from forge_harness.fleet.dispatch_client import DispatchClient

    client = DispatchClient()

    # Simple dispatch
    result = await client.send("forge:tech", "Create docs/README.md")

    # With custom configuration
    result = await client.send(
        "forge:gemini",
        "Research best practices...",
        verify=True,
        timeout=30.0,
        max_retries=3
    )

    if result.success:
        print(f"Dispatched in {result.delivery_time_ms}ms")
    else:
        print(f"Failed: {result.error}")
    ```
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..logging_config import get_logger
from .readiness import CLIState, check_readiness, restart_agent, wait_for_ready
from .retry import CircuitBreaker, FailureType, RetryConfig, dispatch_with_retry
from .verification import (
    DEFAULT_TIMEOUT,
    DeliveryReceipt,
    send_with_verification,
)

logger = get_logger(__name__)

# =============================================================================
# Git Mutex (single source of truth: uses utils.git_guard for lock detection)
# =============================================================================

from forge_harness.utils.git_guard import GIT_LOCK_FILE, check_git_unsafe_state


class GitMutex:
    """Git mutex to prevent concurrent commits.

    Uses file-based locking to coordinate git operations across agents.
    Delegates lock detection to utils.git_guard (single source of truth).
    """

    def __init__(self, repo_root: Path | None = None):
        """Initialize git mutex.

        Args:
            repo_root: Repository root path (auto-detects if None)
        """
        if repo_root is None:
            # Try to detect from environment or current directory
            forge_root = os.environ.get("FORGE_ROOT")
            if forge_root:
                repo_root = Path(forge_root)
            else:
                repo_root = Path.cwd()

        self.repo_root = Path(repo_root)
        self.lock_file = self.repo_root / GIT_LOCK_FILE

    def is_locked(self) -> bool:
        """Check if git is currently locked or in unsafe state.

        Uses utils.git_guard.check_git_unsafe_state (index.lock, HEAD.lock,
        rebase-apply, rebase-merge).
        """
        is_unsafe, _ = check_git_unsafe_state(self.repo_root)
        return is_unsafe

    async def wait_for_unlock(self, timeout: float = 30.0) -> bool:
        """Wait for git lock to be released (async).

        Args:
            timeout: Maximum wait time in seconds

        Returns:
            True if lock was released, False if timeout
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            if not self.is_locked():
                return True

            # Stale index.lock removal (only; rebase/HEAD.lock not auto-removed)
            # 120s threshold: normal git ops <5s, push <30s, rebase <60s.
            # Any lock >120s is certainly from a crashed process.
            try:
                if self.lock_file.exists():
                    lock_age = time.time() - self.lock_file.stat().st_mtime
                    if lock_age > 120:  # 2 minutes (was 5 min, reduced per session review)
                        logger.warning(f"Removing stale git lock (age: {lock_age:.0f}s)")
                        self.lock_file.unlink(missing_ok=True)
                        return True
            except (FileNotFoundError, OSError):
                return True

            await asyncio.sleep(0.5)

        logger.error(f"Git lock timeout after {timeout}s")
        return False

    def wait_for_unlock_sync(self, timeout: float = 30.0) -> bool:
        """Wait for git lock to be released (sync, for use from scripts/CLI).

        Args:
            timeout: Maximum wait time in seconds

        Returns:
            True if lock was released, False if timeout
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            if not self.is_locked():
                return True

            # Stale index.lock removal (120s threshold, matching async version)
            try:
                if self.lock_file.exists():
                    lock_age = time.time() - self.lock_file.stat().st_mtime
                    if lock_age > 120:  # 2 minutes (was 5 min, reduced per session review)
                        logger.warning(f"Removing stale git lock (age: {lock_age:.0f}s)")
                        self.lock_file.unlink(missing_ok=True)
                        return True
            except (FileNotFoundError, OSError):
                return True

            time.sleep(0.5)

        logger.error(f"Git lock timeout after {timeout}s")
        return False


# =============================================================================
# Dispatch Result
# =============================================================================


@dataclass
class DispatchResult:
    """Result of dispatch operation.

    Attributes:
        success: Whether dispatch succeeded
        target: Tmux target
        message_id: Unique message ID
        delivery_time_ms: Time to deliver message
        total_time_ms: Total time including retries
        attempts: Number of dispatch attempts
        verification_method: How delivery was verified
        error: Error message (if failed)
        failure_type: Type of failure (if failed)
        circuit_state: Final circuit breaker state
        cli_state: CLI state at time of dispatch
        git_lock_waited: Whether we waited for git lock
    """

    success: bool
    target: str
    message_id: str
    delivery_time_ms: float
    total_time_ms: float
    attempts: int
    verification_method: str
    error: str | None = None
    failure_type: FailureType | None = None
    circuit_state: str = "closed"
    cli_state: CLIState | None = None
    git_lock_waited: bool = False

    def __str__(self) -> str:
        """Human-readable result."""
        if self.success:
            return (
                f"✓ Dispatched to {self.target} via {self.verification_method} "
                f"in {self.delivery_time_ms:.0f}ms "
                f"({self.attempts} attempt(s), total {self.total_time_ms:.0f}ms)"
            )
        else:
            return f"✗ Dispatch to {self.target} failed: {self.error} ({self.attempts} attempts)"


# =============================================================================
# Dispatch Client
# =============================================================================


class DispatchClient:
    """High-level client for dispatching messages to tmux agents.

    Integrates all reliability layers:
        - Readiness detection
        - Message verification
        - Retry logic
        - Circuit breaker
        - Git mutex coordination

    Example:
        ```python
        client = DispatchClient()

        # Dispatch with defaults
        result = await client.send("forge:tech", "Create README")

        # Custom configuration
        result = await client.send(
            "forge:gemini",
            "Research patterns",
            cli_type="gemini",
            verify=True,
            timeout=30.0,
            max_retries=5,
            wait_for_ready_timeout=15.0
        )
        ```
    """

    def __init__(
        self,
        default_retry_config: RetryConfig | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        git_mutex: GitMutex | None = None,
    ):
        """Initialize dispatch client.

        Args:
            default_retry_config: Default retry configuration
            circuit_breaker: Circuit breaker instance (shared across clients)
            git_mutex: Git mutex instance (shared across clients)
        """
        self.default_retry_config = default_retry_config or RetryConfig()
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.git_mutex = git_mutex or GitMutex()

    async def send(
        self,
        target: str,
        message: str,
        cli_type: str = "auto",
        verify: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int | None = None,
        wait_for_ready_timeout: float = 30.0,
        check_git_lock: bool = True,
        skip_readiness: bool = False,
    ) -> DispatchResult:
        """Send message to tmux agent with full reliability stack.

        Args:
            target: Tmux target (e.g., "forge:tech", "forge:gemini")
            message: Message to send to CLI
            cli_type: Expected CLI type ("claude", "codex", "gemini", "auto")
            verify: Enable verification (echo + activity detection)
            timeout: Verification timeout (seconds)
            max_retries: Maximum retry attempts (uses default if None)
            wait_for_ready_timeout: Max time to wait for CLI ready (seconds)
            check_git_lock: Check for git lock before dispatch
            skip_readiness: Skip readiness check (use when you know agent is idle)

        Returns:
            DispatchResult with success status and metadata

        Example:
            ```python
            # Simple dispatch
            result = await client.send("forge:tech", "Run tests")

            # Research task with longer timeout
            result = await client.send(
                "forge:gemini",
                "Research GraphQL best practices...",
                cli_type="gemini",
                timeout=30.0
            )

            # Complex task with git coordination
            result = await client.send(
                "forge:tech",
                "Refactor auth module",
                check_git_lock=True,
                max_retries=5
            )
            ```
        """
        start_time = time.time()
        git_lock_waited = False
        readiness = None

        # Step 1: Check CLI readiness (unless skipped)
        if not skip_readiness:
            logger.debug(f"Checking readiness for {target} (expect: {cli_type})")
            readiness = check_readiness(target, cli_type)

            if readiness.state == CLIState.CRASHED:
                # CLI is not running — do NOT auto-restart.
                # Auto-restart previously sent wrong CLI commands to agent windows
                # (e.g., sending "claude" to a kimi window). Let the operator
                # restart agents manually or via forge-restore.sh.
                total_time = (time.time() - start_time) * 1000
                logger.warning(
                    f"CLI crashed/not running in {target} "
                    f"(detected: {readiness.cli_type}). "
                    f"Restart manually: forge-restore.sh or start the CLI in the tmux window."
                )
                return DispatchResult(
                    success=False,
                    target=target,
                    message_id="none",
                    delivery_time_ms=0.0,
                    total_time_ms=total_time,
                    attempts=0,
                    verification_method="none",
                    error=f"CLI crashed or not running in {target}. Restart the agent manually.",
                    failure_type=FailureType.CLI_CRASHED,
                    cli_state=CLIState.CRASHED,
                )

            # Always restart agent before dispatch to ensure fresh context.
            # This unconditionally kills and relaunches the CLI regardless of
            # current state (READY, BUSY, STARTING, CONTEXT_EXHAUSTED, etc.).
            # Only CRASHED is exempt — a crashed pane cannot be restarted this way.
            logger.info(
                f"Always restart before dispatch: restarting {target} "
                f"(current state: {readiness.state.value})"
            )
            # Resolve the effective CLI type for restart (use target window name when auto)
            restart_cli = cli_type
            if restart_cli == "auto":
                restart_cli = target.split(":")[-1] if ":" in target else target

            restarted = restart_agent(target, restart_cli)
            if not restarted:
                total_time = (time.time() - start_time) * 1000
                return DispatchResult(
                    success=False,
                    target=target,
                    message_id="none",
                    delivery_time_ms=0.0,
                    total_time_ms=total_time,
                    attempts=0,
                    verification_method="none",
                    error=(
                        f"Failed to restart agent {target} before dispatch "
                        f"(state: {readiness.state.value})"
                    ),
                    failure_type=FailureType.CLI_NOT_READY,
                    cli_state=readiness.state,
                )

            # Wait for the freshly-started CLI to become ready
            if not wait_for_ready(target, cli_type, timeout=wait_for_ready_timeout):
                total_time = (time.time() - start_time) * 1000
                return DispatchResult(
                    success=False,
                    target=target,
                    message_id="none",
                    delivery_time_ms=0.0,
                    total_time_ms=total_time,
                    attempts=0,
                    verification_method="none",
                    error=(
                        f"Agent {target} restarted but did not become ready "
                        f"within {wait_for_ready_timeout}s"
                    ),
                    failure_type=FailureType.CLI_NOT_READY,
                    cli_state=readiness.state,
                )

            logger.info(f"Agent {target} restarted successfully, proceeding with dispatch")
            # Update readiness so the final DispatchResult records the refreshed state
            readiness = check_readiness(target, cli_type)

            logger.info(
                f"CLI ready: {target} ({readiness.cli_type}, confidence={readiness.confidence:.2f})"
            )
        else:
            logger.info(f"Skipping readiness check for {target} (skip_readiness=True)")

        # Step 2: Check git lock (if enabled)
        if check_git_lock and self.git_mutex.is_locked():
            logger.warning("Git lock detected, waiting for release...")
            if not await self.git_mutex.wait_for_unlock(timeout=30.0):
                total_time = (time.time() - start_time) * 1000
                return DispatchResult(
                    success=False,
                    target=target,
                    message_id="none",
                    delivery_time_ms=0.0,
                    total_time_ms=total_time,
                    attempts=0,
                    verification_method="none",
                    error="Git lock timeout",
                    failure_type=FailureType.GIT_LOCK,
                    cli_state=readiness.state if readiness else None,
                )
            git_lock_waited = True
            logger.info("Git lock released, proceeding with dispatch")

        # Step 3: Dispatch with retry logic
        retry_config = self.default_retry_config
        if max_retries is not None:
            retry_config = RetryConfig(max_attempts=max_retries)

        # Resolve effective CLI type for dispatch
        effective_cli = cli_type
        if effective_cli == "auto":
            # Infer from target window name
            window_name = target.split(":")[-1] if ":" in target else target
            effective_cli = window_name

        # Create dispatch function for retry wrapper
        async def dispatch_func(tgt: str, msg: str) -> DeliveryReceipt:
            return await send_with_verification(
                tgt, msg, timeout, use_echo=verify, cli_type=effective_cli
            )

        retry_result = await dispatch_with_retry(
            target,
            message,
            dispatch_func,
            config=retry_config,
            circuit_breaker=self.circuit_breaker,
        )

        total_time = (time.time() - start_time) * 1000

        if retry_result.success:
            # Extract delivery receipt from last successful attempt
            # Note: This is a simplified version - in production we'd track the receipt
            return DispatchResult(
                success=True,
                target=target,
                message_id=str(uuid.uuid4()),
                delivery_time_ms=retry_result.total_time_ms,
                total_time_ms=total_time,
                attempts=retry_result.attempts,
                verification_method="verified",
                circuit_state=retry_result.circuit_state.value,
                cli_state=readiness.state if readiness else None,
                git_lock_waited=git_lock_waited,
            )
        else:
            return DispatchResult(
                success=False,
                target=target,
                message_id=str(uuid.uuid4()),
                delivery_time_ms=0.0,
                total_time_ms=total_time,
                attempts=retry_result.attempts,
                verification_method="none",
                error=retry_result.failure_reason,
                failure_type=retry_result.failure_type,
                circuit_state=retry_result.circuit_state.value,
                cli_state=readiness.state if readiness else None,
                git_lock_waited=git_lock_waited,
            )

    async def send_batch(
        self,
        targets_and_messages: list[tuple[str, str]],
        cli_type: str = "auto",
        verify: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> list[DispatchResult]:
        """Send messages to multiple targets in parallel.

        Args:
            targets_and_messages: List of (target, message) tuples
            cli_type: Expected CLI type for all targets
            verify: Enable verification
            timeout: Verification timeout

        Returns:
            List of DispatchResults in same order as input

        Example:
            ```python
            results = await client.send_batch([
                ("forge:tech", "Task 1"),
                ("forge:qa", "Task 2"),
                ("forge:codex", "Task 3"),
            ])

            success_count = sum(1 for r in results if r.success)
            print(f"{success_count}/{len(results)} dispatches succeeded")
            ```
        """
        tasks = [
            self.send(target, message, cli_type, verify, timeout)
            for target, message in targets_and_messages
        ]
        return await asyncio.gather(*tasks)

    def get_circuit_state(self, target: str) -> str:
        """Get circuit breaker state for target.

        Args:
            target: Tmux target

        Returns:
            Circuit state: "closed", "open", or "half_open"
        """
        return self.circuit_breaker.get_state(target).value

    def reset_circuit(self, target: str) -> None:
        """Reset circuit breaker for target.

        Args:
            target: Tmux target
        """
        self.circuit_breaker.reset(target)
        logger.info(f"Circuit breaker reset for {target}")


# =============================================================================
# Convenience Functions
# =============================================================================

# Global client instance (lazy initialized)
_global_client: DispatchClient | None = None


def get_client() -> DispatchClient:
    """Get global dispatch client instance.

    Returns:
        Shared DispatchClient instance
    """
    global _global_client
    if _global_client is None:
        _global_client = DispatchClient()
    return _global_client


async def send(target: str, message: str, **kwargs) -> DispatchResult:
    """Convenience function for sending with global client.

    Args:
        target: Tmux target
        message: Message to send
        **kwargs: Additional arguments passed to DispatchClient.send()

    Returns:
        DispatchResult

    Example:
        ```python
        from forge_harness.fleet.dispatch_client import send

        result = await send("forge:tech", "Run tests", verify=True)
        ```
    """
    client = get_client()
    return await client.send(target, message, **kwargs)


def send_sync(target: str, message: str, **kwargs) -> DispatchResult:
    """Synchronous wrapper for send().

    Args:
        target: Tmux target
        message: Message to send
        **kwargs: Additional arguments

    Returns:
        DispatchResult
    """
    return asyncio.run(send(target, message, **kwargs))
