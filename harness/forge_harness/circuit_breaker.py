"""Circuit Breaker pattern for external service resilience.

Prevents cascading failures when external services are unavailable.
Implements the standard CLOSED -> OPEN -> HALF_OPEN state machine.

Usage:
    from forge_harness.circuit_breaker import CircuitBreaker, circuit_breaker

    # Create a circuit breaker
    breaker = CircuitBreaker(name="code_atlas", failure_threshold=5)

    # Use as context manager
    async with breaker:
        response = await http_client.get(url)

    # Or use decorator
    @circuit_breaker("tech_diligence")
    async def call_tech_diligence():
        ...
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, TypeVar

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation, requests pass through
    OPEN = "open"  # Circuit tripped, requests fail fast
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitOpenError(Exception):
    """Raised when circuit is open and request is rejected."""

    def __init__(self, name: str, retry_after: float):
        self.name = name
        self.retry_after = retry_after
        super().__init__(f"Circuit breaker '{name}' is open. Retry after {retry_after:.1f}s")


@dataclass
class CircuitStats:
    """Statistics for a circuit breaker."""

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rejected_requests: int = 0  # Rejected due to open circuit
    last_failure_time: datetime | None = None
    last_success_time: datetime | None = None
    state_changes: list[tuple[datetime, CircuitState]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "rejected_requests": self.rejected_requests,
            "success_rate": (
                self.successful_requests / self.total_requests * 100
                if self.total_requests > 0
                else 0
            ),
            "last_failure_time": (
                self.last_failure_time.isoformat() if self.last_failure_time else None
            ),
            "last_success_time": (
                self.last_success_time.isoformat() if self.last_success_time else None
            ),
            "recent_state_changes": [
                {"time": t.isoformat(), "state": s.value} for t, s in self.state_changes[-5:]
            ],
        }


class CircuitBreaker:
    """Circuit breaker implementation.

    States:
    - CLOSED: Normal operation, all requests pass through
    - OPEN: Service failing, requests fail fast without calling service
    - HALF_OPEN: Testing if service recovered, limited requests allowed

    Transitions:
    - CLOSED -> OPEN: After failure_threshold failures within failure_window
    - OPEN -> HALF_OPEN: After recovery_timeout seconds
    - HALF_OPEN -> CLOSED: After success_threshold successes
    - HALF_OPEN -> OPEN: On any failure
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        failure_window: float = 60.0,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
        excluded_exceptions: tuple[type[Exception], ...] | None = None,
    ):
        """Initialize circuit breaker.

        Args:
            name: Identifier for logging and metrics
            failure_threshold: Number of failures to trip circuit
            failure_window: Window in seconds for counting failures
            recovery_timeout: Seconds to wait before trying half-open
            success_threshold: Successes needed in half-open to close
            excluded_exceptions: Exceptions that don't count as failures
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.failure_window = failure_window
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.excluded_exceptions = excluded_exceptions or ()

        self._state = CircuitState.CLOSED
        self._failures: list[float] = []  # Timestamps of recent failures
        self._half_open_successes = 0
        self._last_failure_time: float | None = None
        self._lock = asyncio.Lock()
        self._stats = CircuitStats()

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        return self._state

    @property
    def stats(self) -> CircuitStats:
        """Get circuit statistics."""
        return self._stats

    def _set_state(self, new_state: CircuitState) -> None:
        """Change state with logging and stats tracking."""
        if self._state != new_state:
            logger.info(f"Circuit '{self.name}': {self._state.value} -> {new_state.value}")
            self._state = new_state
            self._stats.state_changes.append((datetime.now(UTC), new_state))

    def _clean_old_failures(self) -> None:
        """Remove failures outside the failure window."""
        now = time.monotonic()
        cutoff = now - self.failure_window
        self._failures = [t for t in self._failures if t > cutoff]

    def _should_trip(self) -> bool:
        """Check if circuit should trip to OPEN."""
        self._clean_old_failures()
        return len(self._failures) >= self.failure_threshold

    def _can_try_half_open(self) -> bool:
        """Check if enough time has passed to try half-open."""
        if self._last_failure_time is None:
            return True
        return (time.monotonic() - self._last_failure_time) >= self.recovery_timeout

    def _get_retry_after(self) -> float:
        """Get seconds until circuit might recover."""
        if self._last_failure_time is None:
            return 0.0
        elapsed = time.monotonic() - self._last_failure_time
        return max(0.0, self.recovery_timeout - elapsed)

    async def __aenter__(self) -> CircuitBreaker:
        """Enter circuit breaker context."""
        await self._before_call()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Exit circuit breaker context."""
        if exc_type is None:
            await self._on_success()
        elif exc_type is not None and not issubclass(exc_type, self.excluded_exceptions):
            await self._on_failure(exc_val)
        return False  # Don't suppress exceptions

    async def _before_call(self) -> None:
        """Check circuit state before making a call."""
        async with self._lock:
            self._stats.total_requests += 1

            if self._state == CircuitState.CLOSED:
                return  # Allow request

            if self._state == CircuitState.OPEN:
                if self._can_try_half_open():
                    self._set_state(CircuitState.HALF_OPEN)
                    self._half_open_successes = 0
                    return  # Allow test request
                else:
                    self._stats.rejected_requests += 1
                    raise CircuitOpenError(self.name, self._get_retry_after())

            if self._state == CircuitState.HALF_OPEN:
                return  # Allow test request

    async def _on_success(self) -> None:
        """Record a successful call."""
        async with self._lock:
            self._stats.successful_requests += 1
            self._stats.last_success_time = datetime.now(UTC)

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= self.success_threshold:
                    self._set_state(CircuitState.CLOSED)
                    self._failures.clear()

    async def _on_failure(self, error: Exception | None = None) -> None:
        """Record a failed call."""
        async with self._lock:
            now = time.monotonic()
            self._failures.append(now)
            self._last_failure_time = now
            self._stats.failed_requests += 1
            self._stats.last_failure_time = datetime.now(UTC)

            if self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open trips back to open
                self._set_state(CircuitState.OPEN)
            elif self._state == CircuitState.CLOSED and self._should_trip():
                self._set_state(CircuitState.OPEN)

            if error:
                logger.warning(f"Circuit '{self.name}' recorded failure: {error}")

    async def call(
        self,
        func: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute a function with circuit breaker protection.

        Args:
            func: Async function to call
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result of func

        Raises:
            CircuitOpenError: If circuit is open
            Exception: Any exception from func (after recording failure)
        """
        await self._before_call()
        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except self.excluded_exceptions:
            # Don't count as failure, just re-raise
            raise
        except Exception as e:
            await self._on_failure(e)
            raise

    def to_dict(self) -> dict[str, Any]:
        """Convert circuit breaker state to dictionary."""
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_threshold": self.failure_threshold,
            "failure_window": self.failure_window,
            "recovery_timeout": self.recovery_timeout,
            "success_threshold": self.success_threshold,
            "recent_failures": len(self._failures),
            "half_open_successes": self._half_open_successes,
            "retry_after": self._get_retry_after() if self._state == CircuitState.OPEN else 0,
            "stats": self._stats.to_dict(),
        }


# Global registry of circuit breakers
_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    failure_window: float = 60.0,
    recovery_timeout: float = 30.0,
    success_threshold: int = 2,
) -> CircuitBreaker:
    """Get or create a circuit breaker by name.

    Args:
        name: Breaker name
        failure_threshold: Failures to trip (only used on creation)
        failure_window: Window for counting failures (only used on creation)
        recovery_timeout: Recovery wait time (only used on creation)
        success_threshold: Successes to close (only used on creation)

    Returns:
        Circuit breaker instance
    """
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            failure_window=failure_window,
            recovery_timeout=recovery_timeout,
            success_threshold=success_threshold,
        )
    return _breakers[name]


def list_circuit_breakers() -> list[dict[str, Any]]:
    """List all circuit breakers and their states."""
    return [breaker.to_dict() for breaker in _breakers.values()]


def reset_circuit_breaker(name: str) -> bool:
    """Reset a circuit breaker to closed state.

    Args:
        name: Breaker name

    Returns:
        True if reset, False if not found
    """
    if name in _breakers:
        breaker = _breakers[name]
        breaker._state = CircuitState.CLOSED
        breaker._failures.clear()
        breaker._half_open_successes = 0
        breaker._last_failure_time = None
        logger.info(f"Circuit '{name}' manually reset to CLOSED")
        return True
    return False


def circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    failure_window: float = 60.0,
    recovery_timeout: float = 30.0,
    success_threshold: int = 2,
):
    """Decorator to wrap async functions with circuit breaker.

    Args:
        name: Circuit breaker name
        failure_threshold: Number of failures to trip
        failure_window: Window in seconds for counting failures
        recovery_timeout: Seconds to wait before trying half-open
        success_threshold: Successes needed to close

    Returns:
        Decorator function

    Example:
        @circuit_breaker("external_api")
        async def call_external_api():
            ...
    """

    def decorator(
        func: Callable[..., Coroutine[Any, Any, T]],
    ) -> Callable[..., Coroutine[Any, Any, T]]:
        breaker = get_circuit_breaker(
            name=name,
            failure_threshold=failure_threshold,
            failure_window=failure_window,
            recovery_timeout=recovery_timeout,
            success_threshold=success_threshold,
        )

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await breaker.call(func, *args, **kwargs)

        return wrapper

    return decorator


# Pre-configure circuit breakers for known services
def setup_default_breakers() -> None:
    """Set up circuit breakers for known external services."""
    # Code Atlas - critical for codebase analysis
    get_circuit_breaker(
        "code_atlas",
        failure_threshold=5,
        failure_window=60.0,
        recovery_timeout=30.0,
    )

    # Tech Diligence - used for quality analysis
    get_circuit_breaker(
        "tech_diligence",
        failure_threshold=5,
        failure_window=60.0,
        recovery_timeout=30.0,
    )

    # Notion API - used for storage
    get_circuit_breaker(
        "notion",
        failure_threshold=3,
        failure_window=120.0,
        recovery_timeout=60.0,
    )

    # GitHub API
    get_circuit_breaker(
        "github",
        failure_threshold=5,
        failure_window=60.0,
        recovery_timeout=30.0,
    )

    logger.debug("Default circuit breakers configured")


# Initialize default breakers on import
setup_default_breakers()
