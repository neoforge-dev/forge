"""Retry logic with circuit breaker for tmux dispatch.

Implements exponential backoff with configurable retry policies and
circuit breaker pattern to prevent cascading failures.

Architecture:
    1. Classify failures as retryable vs terminal
    2. Apply exponential backoff: 1s, 2s, 4s, 8s, 16s
    3. Track failure rate per agent with circuit breaker
    4. Open circuit after threshold failures
    5. Half-open circuit for gradual recovery

Circuit Breaker States:
    - CLOSED: Normal operation, failures tracked
    - OPEN: Too many failures, reject immediately
    - HALF_OPEN: Testing recovery with limited requests

Usage:
    ```python
    from forge_harness.fleet.retry import dispatch_with_retry, RetryConfig

    config = RetryConfig(max_attempts=5, base_delay=1.0)
    result = await dispatch_with_retry(
        target="forge:tech",
        message="Create docs/README.md",
        config=config
    )

    if result.success:
        print(f"Success after {result.attempts} attempts")
    else:
        print(f"Failed: {result.failure_reason}")
    ```
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from ..circuit_breaker import CircuitState
from ..logging_config import get_logger

logger = get_logger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Default retry configuration
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_DELAY = 1.0  # seconds
DEFAULT_MAX_DELAY = 16.0  # seconds
DEFAULT_BACKOFF_MULTIPLIER = 2.0

# Circuit breaker configuration
CB_FAILURE_THRESHOLD = 5  # Failures before opening circuit
CB_SUCCESS_THRESHOLD = 2  # Successes needed to close circuit from half-open
CB_TIMEOUT_SECONDS = 60  # Time before trying half-open
CB_HALF_OPEN_MAX_REQUESTS = 3  # Max requests in half-open state


# =============================================================================
# Failure Classification
# =============================================================================


class FailureType(str, Enum):
    """Types of dispatch failures."""

    GIT_LOCK = "git_lock"  # Git index.lock blocking commit
    CLI_NOT_READY = "cli_not_ready"  # CLI not ready to receive input
    CLI_CRASHED = "cli_crashed"  # CLI process is dead
    VERIFICATION_TIMEOUT = "verification_timeout"  # Message not verified
    SEND_FAILED = "send_failed"  # Failed to send to tmux
    UNKNOWN = "unknown"  # Unknown failure


def is_retryable(failure_type: FailureType) -> bool:
    """Determine if a failure type is retryable.

    Args:
        failure_type: Type of failure

    Returns:
        True if failure is retryable (transient), False if terminal

    Retryable Failures:
        - git_lock: Retry after lock is released
        - cli_not_ready: Retry after CLI becomes ready
        - verification_timeout: May succeed on retry

    Terminal Failures:
        - cli_crashed: Need manual restart
        - send_failed: System-level issue
    """
    return failure_type in [
        FailureType.GIT_LOCK,
        FailureType.CLI_NOT_READY,
        FailureType.VERIFICATION_TIMEOUT,
    ]


# =============================================================================
# Retry Configuration
# =============================================================================


@dataclass
class RetryConfig:
    """Configuration for retry behavior.

    Attributes:
        max_attempts: Maximum number of retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)
        backoff_multiplier: Multiplier for exponential backoff
        enable_jitter: Add random jitter to delays
        classify_failure: Function to classify failure type
    """

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay: float = DEFAULT_BASE_DELAY
    max_delay: float = DEFAULT_MAX_DELAY
    backoff_multiplier: float = DEFAULT_BACKOFF_MULTIPLIER
    enable_jitter: bool = True
    classify_failure: Callable[[Exception], FailureType] | None = None

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number.

        Args:
            attempt: Attempt number (0-indexed)

        Returns:
            Delay in seconds with exponential backoff
        """
        delay = min(self.base_delay * (self.backoff_multiplier**attempt), self.max_delay)

        if self.enable_jitter:
            # Add up to 20% random jitter
            import random

            jitter = delay * 0.2 * random.random()
            delay += jitter

        return delay


# =============================================================================
# Circuit Breaker
# =============================================================================


@dataclass
class CircuitBreakerState:
    """State of a circuit breaker.

    Attributes:
        state: Current circuit state
        failure_count: Number of consecutive failures
        success_count: Number of consecutive successes (in half-open)
        last_failure_time: Timestamp of last failure
        opened_at: Timestamp when circuit was opened
        half_open_request_count: Number of requests in half-open state
    """

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: datetime | None = None
    opened_at: datetime | None = None
    half_open_request_count: int = 0


class CircuitBreaker:
    """Circuit breaker for preventing cascading failures.

    Tracks failure rate per target and opens circuit after threshold
    is exceeded. Automatically attempts recovery after timeout.

    Example:
        ```python
        breaker = CircuitBreaker()

        # Check if circuit allows request
        if not breaker.allow_request("forge:tech"):
            print("Circuit is open, rejecting request")
            return

        try:
            # Attempt operation
            result = await dispatch(...)
            breaker.record_success("forge:tech")
        except Exception as e:
            breaker.record_failure("forge:tech")
        ```
    """

    def __init__(
        self,
        failure_threshold: int = CB_FAILURE_THRESHOLD,
        success_threshold: int = CB_SUCCESS_THRESHOLD,
        timeout_seconds: int = CB_TIMEOUT_SECONDS,
        half_open_max_requests: int = CB_HALF_OPEN_MAX_REQUESTS,
    ):
        """Initialize circuit breaker.

        Args:
            failure_threshold: Failures before opening circuit
            success_threshold: Successes needed to close from half-open
            timeout_seconds: Time before attempting half-open
            half_open_max_requests: Max requests in half-open state
        """
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout_seconds = timeout_seconds
        self.half_open_max_requests = half_open_max_requests

        # Per-target circuit breaker state
        self._circuits: dict[str, CircuitBreakerState] = {}

    def _get_circuit(self, target: str) -> CircuitBreakerState:
        """Get or create circuit state for target."""
        if target not in self._circuits:
            self._circuits[target] = CircuitBreakerState()
        return self._circuits[target]

    def allow_request(self, target: str) -> bool:
        """Check if request should be allowed through circuit.

        Args:
            target: Target identifier (e.g., "forge:tech")

        Returns:
            True if request is allowed, False if circuit is open
        """
        circuit = self._get_circuit(target)
        now = datetime.now(UTC)

        if circuit.state == CircuitState.CLOSED:
            return True

        elif circuit.state == CircuitState.OPEN:
            # Check if timeout has elapsed to try half-open
            if (
                circuit.opened_at
                and (now - circuit.opened_at).total_seconds() >= self.timeout_seconds
            ):
                logger.info(f"Circuit {target} transitioning to HALF_OPEN")
                circuit.state = CircuitState.HALF_OPEN
                circuit.half_open_request_count = 0
                return True
            else:
                logger.warning(f"Circuit {target} is OPEN, rejecting request")
                return False

        else:  # HALF_OPEN
            # Allow limited requests in half-open state
            if circuit.half_open_request_count < self.half_open_max_requests:
                circuit.half_open_request_count += 1
                return True
            else:
                logger.warning(f"Circuit {target} half-open limit reached")
                return False

    def record_success(self, target: str) -> None:
        """Record successful operation.

        Args:
            target: Target identifier
        """
        circuit = self._get_circuit(target)

        if circuit.state == CircuitState.HALF_OPEN:
            circuit.success_count += 1
            logger.debug(
                f"Circuit {target} success in HALF_OPEN "
                f"({circuit.success_count}/{self.success_threshold})"
            )

            if circuit.success_count >= self.success_threshold:
                logger.info(f"Circuit {target} transitioning to CLOSED")
                circuit.state = CircuitState.CLOSED
                circuit.failure_count = 0
                circuit.success_count = 0
                circuit.opened_at = None

        else:  # CLOSED
            # Reset failure count on success
            circuit.failure_count = 0

    def record_failure(self, target: str, failure_type: FailureType | None = None) -> None:
        """Record failed operation.

        Args:
            target: Target identifier
            failure_type: Type of failure (for logging)
        """
        circuit = self._get_circuit(target)
        circuit.failure_count += 1
        circuit.last_failure_time = datetime.now(UTC)

        failure_msg = f" ({failure_type.value})" if failure_type else ""
        logger.warning(
            f"Circuit {target} failure {circuit.failure_count}/{self.failure_threshold}{failure_msg}"
        )

        if circuit.state == CircuitState.HALF_OPEN:
            # Failure in half-open immediately opens circuit again
            logger.warning(f"Circuit {target} failure in HALF_OPEN, reopening")
            circuit.state = CircuitState.OPEN
            circuit.opened_at = datetime.now(UTC)
            circuit.success_count = 0

        elif circuit.failure_count >= self.failure_threshold:
            # Too many failures, open the circuit
            logger.error(f"Circuit {target} OPEN after {circuit.failure_count} failures")
            circuit.state = CircuitState.OPEN
            circuit.opened_at = datetime.now(UTC)

    def get_state(self, target: str) -> CircuitState:
        """Get current circuit state for target.

        Args:
            target: Target identifier

        Returns:
            Current circuit state
        """
        return self._get_circuit(target).state

    def reset(self, target: str) -> None:
        """Reset circuit breaker for target.

        Args:
            target: Target identifier
        """
        if target in self._circuits:
            self._circuits[target] = CircuitBreakerState()
            logger.info(f"Circuit {target} reset")


# =============================================================================
# Retry Result
# =============================================================================


@dataclass
class RetryResult:
    """Result of retry operation.

    Attributes:
        success: Whether operation succeeded
        attempts: Number of attempts made
        total_time_ms: Total time taken (milliseconds)
        failure_reason: Reason for failure (if failed)
        failure_type: Type of failure
        circuit_state: Final circuit breaker state
    """

    success: bool
    attempts: int
    total_time_ms: float
    failure_reason: str | None = None
    failure_type: FailureType | None = None
    circuit_state: CircuitState = CircuitState.CLOSED


# =============================================================================
# Retry Logic
# =============================================================================


async def dispatch_with_retry(
    target: str,
    message: str,
    dispatch_func: Callable[[str, str], asyncio.Future],
    config: RetryConfig | None = None,
    circuit_breaker: CircuitBreaker | None = None,
) -> RetryResult:
    """Dispatch message with retry logic and circuit breaker.

    Args:
        target: Tmux target (e.g., "forge:tech")
        message: Message to dispatch
        dispatch_func: Async function to perform dispatch (target, message) -> result
        config: Retry configuration (uses defaults if None)
        circuit_breaker: Circuit breaker instance (creates new if None)

    Returns:
        RetryResult with success status and metadata

    Example:
        ```python
        from forge_harness.fleet.verification import send_with_verification

        result = await dispatch_with_retry(
            "forge:tech",
            "Create docs/README.md",
            send_with_verification,
            config=RetryConfig(max_attempts=3)
        )
        ```
    """
    config = config or RetryConfig()
    circuit_breaker = circuit_breaker or CircuitBreaker()

    start_time = time.time()
    attempts = 0

    # Check circuit breaker before attempting
    if not circuit_breaker.allow_request(target):
        total_time = (time.time() - start_time) * 1000
        return RetryResult(
            success=False,
            attempts=0,
            total_time_ms=total_time,
            failure_reason="Circuit breaker is open",
            failure_type=None,
            circuit_state=CircuitState.OPEN,
        )

    last_exception: Exception | None = None
    last_failure_type: FailureType | None = None

    for attempt in range(config.max_attempts):
        attempts += 1
        logger.debug(f"Dispatch attempt {attempts}/{config.max_attempts} for {target}")

        try:
            # Attempt dispatch
            result = await dispatch_func(target, message)

            # Check if dispatch was successful
            # Assume result has a .delivered attribute (from DeliveryReceipt)
            if hasattr(result, "delivered") and result.delivered:
                total_time = (time.time() - start_time) * 1000
                circuit_breaker.record_success(target)

                logger.info(f"Dispatch succeeded for {target} after {attempts} attempt(s)")
                return RetryResult(
                    success=True,
                    attempts=attempts,
                    total_time_ms=total_time,
                    circuit_state=circuit_breaker.get_state(target),
                )

            # Dispatch failed but no exception - treat as verification timeout
            last_failure_type = FailureType.VERIFICATION_TIMEOUT
            failure_reason = getattr(result, "failure_reason", "Verification failed")

            if not is_retryable(last_failure_type):
                break

        except Exception as e:
            last_exception = e

            # Classify failure type
            if config.classify_failure:
                last_failure_type = config.classify_failure(e)
            else:
                # Default classification
                error_msg = str(e).lower()
                if "lock" in error_msg or "index.lock" in error_msg:
                    last_failure_type = FailureType.GIT_LOCK
                elif "not ready" in error_msg or "timeout" in error_msg:
                    last_failure_type = FailureType.CLI_NOT_READY
                elif "crashed" in error_msg or "dead" in error_msg:
                    last_failure_type = FailureType.CLI_CRASHED
                else:
                    last_failure_type = FailureType.UNKNOWN

            logger.warning(f"Dispatch attempt {attempts} failed: {last_failure_type.value} - {e}")

            # Check if failure is retryable
            if not is_retryable(last_failure_type):
                logger.error(f"Terminal failure: {last_failure_type.value}")
                break

        # Record failure with circuit breaker
        circuit_breaker.record_failure(target, last_failure_type)

        # Check if we should retry
        if attempts >= config.max_attempts:
            break

        # Check circuit breaker before next attempt
        if not circuit_breaker.allow_request(target):
            logger.warning("Circuit breaker opened during retries")
            break

        # Wait before retry with exponential backoff
        delay = config.get_delay(attempts - 1)
        logger.debug(f"Waiting {delay:.2f}s before retry {attempts + 1}")
        await asyncio.sleep(delay)

    # All retries failed
    total_time = (time.time() - start_time) * 1000

    if last_exception:
        failure_reason = (
            f"{last_failure_type.value if last_failure_type else 'Unknown'}: {last_exception}"
        )
    else:
        failure_reason = "All retry attempts exhausted"

    logger.error(f"Dispatch failed for {target} after {attempts} attempts: {failure_reason}")

    return RetryResult(
        success=False,
        attempts=attempts,
        total_time_ms=total_time,
        failure_reason=failure_reason,
        failure_type=last_failure_type,
        circuit_state=circuit_breaker.get_state(target),
    )
