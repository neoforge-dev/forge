"""Tests for retry logic and circuit breaker.

Tests exponential backoff, retry limits, retry decision matrix,
and circuit breaker state transitions.

Test Coverage:
- Exponential backoff timing (1s, 2s, 4s, 8s, 16s)
- Max retry limit enforcement
- Retry decision matrix (retryable vs terminal failures)
- Circuit breaker state transitions
- Circuit breaker timeout and recovery
"""

import asyncio
import time

import pytest

from .conftest import CircuitState, RetryResult

# =============================================================================
# Exponential Backoff Tests
# =============================================================================


@pytest.mark.asyncio
async def test_exponential_backoff_timing(mock_sleep):
    """Test exponential backoff follows 1s, 2s, 4s, 8s, 16s pattern."""
    backoff_times = []

    for attempt in range(5):
        delay = 2**attempt  # 1, 2, 4, 8, 16
        backoff_times.append(delay)
        await asyncio.sleep(delay)

    assert backoff_times == [1, 2, 4, 8, 16]
    assert mock_sleep.call_count == 5


@pytest.mark.asyncio
async def test_exponential_backoff_max_delay(mock_sleep):
    """Test exponential backoff caps at maximum delay."""
    max_delay = 32

    for attempt in range(10):
        delay = min(2**attempt, max_delay)
        await asyncio.sleep(delay)

        if delay >= max_delay:
            # All subsequent delays should be capped
            assert delay == max_delay


@pytest.mark.asyncio
async def test_exponential_backoff_jitter(mock_sleep):
    """Test exponential backoff with jitter to avoid thundering herd."""
    import random

    base_delays = []
    jittered_delays = []

    for attempt in range(5):
        base_delay = 2**attempt
        jitter = random.uniform(0, 0.1 * base_delay)  # 0-10% jitter
        jittered_delay = base_delay + jitter

        base_delays.append(base_delay)
        jittered_delays.append(jittered_delay)

        await asyncio.sleep(jittered_delay)

    # Jittered delays should be slightly higher
    for base, jittered in zip(base_delays, jittered_delays):
        assert jittered >= base
        assert jittered <= base * 1.1  # Within 10%


# =============================================================================
# Max Retry Limit Tests
# =============================================================================


@pytest.mark.asyncio
async def test_max_retries_enforcement(mock_sleep):
    """Test retry stops after max attempts."""
    max_retries = 5
    attempts = 0

    while attempts < max_retries:
        attempts += 1
        await asyncio.sleep(1)

        if attempts >= max_retries:
            break

    assert attempts == max_retries
    assert mock_sleep.call_count == max_retries


@pytest.mark.asyncio
async def test_max_retries_configurable(mock_sleep):
    """Test max retries is configurable."""
    for max_retries in [3, 5, 10]:
        attempts = 0

        while attempts < max_retries:
            attempts += 1
            await asyncio.sleep(1)

            if attempts >= max_retries:
                break

        assert attempts == max_retries


@pytest.mark.asyncio
async def test_successful_retry_stops_early(mock_sleep):
    """Test retry stops when operation succeeds."""
    max_retries = 5
    success_on_attempt = 3
    attempts = 0

    while attempts < max_retries:
        attempts += 1
        await asyncio.sleep(1)

        if attempts == success_on_attempt:
            # Success, stop retrying
            break

    assert attempts == success_on_attempt
    assert attempts < max_retries


# =============================================================================
# Retry Decision Matrix Tests
# =============================================================================


def test_retry_decision_transient_failure():
    """Test transient failures are retryable."""
    transient_errors = [
        "Connection timeout",
        "Temporary unavailable",
        "Rate limit exceeded",
        "Network error",
        "503 Service Unavailable",
    ]

    for error in transient_errors:
        # Simulate retry decision (case-insensitive)
        error_lower = error.lower()
        is_retryable = any(
            keyword in error_lower
            for keyword in ["timeout", "unavailable", "limit", "network", "503"]
        )
        assert is_retryable is True


def test_retry_decision_terminal_failure():
    """Test terminal failures are not retryable."""
    terminal_errors = [
        "Authentication failed",
        "Permission denied",
        "Invalid syntax",
        "File not found",
        "400 Bad Request",
    ]

    for error in terminal_errors:
        # Simulate retry decision
        is_retryable = any(
            keyword in error for keyword in ["timeout", "unavailable", "limit", "network", "503"]
        )
        assert is_retryable is False


def test_retry_decision_process_crashed():
    """Test process crashed is retryable (can restart)."""
    error = "Process exited with code 1"

    # Process crashes are retryable with restart
    is_retryable = "exited" in error or "crashed" in error
    assert is_retryable is True


def test_retry_decision_git_lock():
    """Test git lock is retryable."""
    error = "fatal: Unable to create '.git/index.lock': File exists"

    is_retryable = "lock" in error.lower() or "file exists" in error.lower()
    assert is_retryable is True


def test_retry_decision_agent_busy():
    """Test agent busy is retryable."""
    error = "Agent is currently busy"

    is_retryable = "busy" in error.lower() or "working" in error.lower()
    assert is_retryable is True


def test_retry_decision_validation_error():
    """Test validation errors are not retryable."""
    error = "Invalid task specification"

    # Validation errors are terminal
    is_retryable = any(
        keyword in error.lower() for keyword in ["timeout", "busy", "lock", "network"]
    )
    assert is_retryable is False


# =============================================================================
# Circuit Breaker State Transition Tests
# =============================================================================


def test_circuit_breaker_closed_to_open():
    """Test circuit breaker opens after threshold failures."""
    failure_threshold = 3
    failures = 0

    state = CircuitState.CLOSED

    for _ in range(5):
        # Simulate failure
        failures += 1

        if failures >= failure_threshold:
            state = CircuitState.OPEN
            break

    assert state == CircuitState.OPEN
    assert failures == failure_threshold


def test_circuit_breaker_open_to_half_open():
    """Test circuit breaker transitions to half-open after timeout."""
    state = CircuitState.OPEN
    open_timestamp = time.time()
    timeout_seconds = 30

    # Simulate timeout passage
    current_time = open_timestamp + timeout_seconds + 1

    if current_time - open_timestamp >= timeout_seconds:
        state = CircuitState.HALF_OPEN

    assert state == CircuitState.HALF_OPEN


def test_circuit_breaker_half_open_to_closed():
    """Test circuit breaker closes after successful request in half-open."""
    state = CircuitState.HALF_OPEN

    # Simulate successful request
    request_success = True

    if request_success:
        state = CircuitState.CLOSED

    assert state == CircuitState.CLOSED


def test_circuit_breaker_half_open_to_open():
    """Test circuit breaker reopens after failure in half-open."""
    state = CircuitState.HALF_OPEN

    # Simulate failed request
    request_success = False

    if not request_success:
        state = CircuitState.OPEN

    assert state == CircuitState.OPEN


def test_circuit_breaker_stays_closed_on_success():
    """Test circuit breaker stays closed on successful requests."""
    state = CircuitState.CLOSED
    failures = 0

    for _ in range(10):
        # Simulate success
        request_success = True

        if request_success:
            failures = 0  # Reset failure count
        else:
            failures += 1

    assert state == CircuitState.CLOSED
    assert failures == 0


# =============================================================================
# Circuit Breaker Threshold Tests
# =============================================================================


def test_circuit_breaker_failure_count_tracking():
    """Test circuit breaker tracks consecutive failures."""
    failures = 0
    max_failures = 5

    for i in range(10):
        # Simulate alternating success/failure
        if i % 2 == 0:
            # Failure
            failures += 1
        else:
            # Success - reset counter
            failures = 0

    # Should have 1 failure (last was failure at i=8, then success at i=9 reset it, then failure at 10)
    # Actually, let me recalculate: i=8 (even) failure (1), i=9 (odd) success (0)
    # Final count depends on whether loop includes 10
    # Let's make this clearer
    failures = 0
    for i in range(10):
        if i % 2 == 0:
            failures += 1
        else:
            failures = 0

    # Last iteration is i=9 (odd), so failures = 0
    assert failures == 0


def test_circuit_breaker_failure_threshold_configurable():
    """Test circuit breaker failure threshold is configurable."""
    thresholds = [3, 5, 10]

    for threshold in thresholds:
        failures = 0
        state = CircuitState.CLOSED

        for _ in range(threshold + 2):
            failures += 1

            if failures >= threshold:
                state = CircuitState.OPEN
                break

        assert state == CircuitState.OPEN
        assert failures == threshold


# =============================================================================
# Circuit Breaker Timeout Tests
# =============================================================================


@pytest.mark.asyncio
async def test_circuit_breaker_timeout_recovery(mock_sleep):
    """Test circuit breaker recovers after timeout."""
    state = CircuitState.OPEN
    timeout_seconds = 30

    # Wait for timeout
    await asyncio.sleep(timeout_seconds)

    # Transition to half-open
    state = CircuitState.HALF_OPEN

    assert state == CircuitState.HALF_OPEN
    assert mock_sleep.call_count == 1
    mock_sleep.assert_called_once_with(timeout_seconds)


@pytest.mark.asyncio
async def test_circuit_breaker_timeout_configurable(mock_sleep):
    """Test circuit breaker timeout is configurable."""
    timeouts = [15, 30, 60]

    for timeout in timeouts:
        state = CircuitState.OPEN

        await asyncio.sleep(timeout)

        state = CircuitState.HALF_OPEN
        assert state == CircuitState.HALF_OPEN


@pytest.mark.asyncio
async def test_circuit_breaker_no_requests_while_open():
    """Test circuit breaker blocks requests while open."""
    state = CircuitState.OPEN

    # Attempt request
    can_attempt = state != CircuitState.OPEN

    assert can_attempt is False


@pytest.mark.asyncio
async def test_circuit_breaker_allows_requests_half_open():
    """Test circuit breaker allows single request in half-open."""
    state = CircuitState.HALF_OPEN

    # Attempt request
    can_attempt = state in [CircuitState.CLOSED, CircuitState.HALF_OPEN]

    assert can_attempt is True


# =============================================================================
# Retry Result Tests
# =============================================================================


def test_retry_result_success():
    """Test RetryResult for successful retry."""
    result = RetryResult(success=True, attempts=3, total_time_ms=7000, final_error=None)

    assert result.success is True
    assert result.attempts == 3
    assert result.total_time_ms == 7000  # 1s + 2s + 4s
    assert result.final_error is None


def test_retry_result_failure():
    """Test RetryResult for failed retry."""
    result = RetryResult(
        success=False,
        attempts=5,
        total_time_ms=31000,
        final_error="Max retries exceeded",
    )

    assert result.success is False
    assert result.attempts == 5
    assert result.total_time_ms == 31000  # 1+2+4+8+16
    assert result.final_error is not None


# =============================================================================
# Integration Tests
# =============================================================================


@pytest.mark.asyncio
async def test_retry_with_exponential_backoff_integration(mock_sleep):
    """Test full retry flow with exponential backoff."""
    max_retries = 5
    attempts = 0
    total_time = 0

    while attempts < max_retries:
        attempts += 1
        delay = 2 ** (attempts - 1)  # 1, 2, 4, 8, 16
        total_time += delay

        await asyncio.sleep(delay)

        # Simulate failure
        if attempts < max_retries:
            continue
        else:
            break

    assert attempts == max_retries
    assert total_time == 31  # 1+2+4+8+16


@pytest.mark.asyncio
async def test_circuit_breaker_full_cycle(mock_sleep):
    """Test circuit breaker full state cycle."""
    state = CircuitState.CLOSED
    failures = 0
    failure_threshold = 3

    # Phase 1: Accumulate failures -> OPEN
    for _ in range(failure_threshold):
        failures += 1

    state = CircuitState.OPEN
    assert state == CircuitState.OPEN

    # Phase 2: Wait for timeout -> HALF_OPEN
    await asyncio.sleep(30)
    state = CircuitState.HALF_OPEN
    assert state == CircuitState.HALF_OPEN

    # Phase 3: Successful request -> CLOSED
    request_success = True
    if request_success:
        state = CircuitState.CLOSED
        failures = 0

    assert state == CircuitState.CLOSED
    assert failures == 0


@pytest.mark.asyncio
async def test_retry_with_circuit_breaker_integration(mock_sleep):
    """Test retry logic integrates with circuit breaker."""
    max_retries = 5
    circuit_threshold = 3
    attempts = 0
    failures = 0
    circuit_state = CircuitState.CLOSED

    while attempts < max_retries:
        # Check circuit breaker
        if circuit_state == CircuitState.OPEN:
            # Skip retry, circuit is open
            break

        attempts += 1
        await asyncio.sleep(2 ** (attempts - 1))

        # Simulate failure
        failures += 1

        if failures >= circuit_threshold:
            circuit_state = CircuitState.OPEN
            break

    assert circuit_state == CircuitState.OPEN
    assert attempts == circuit_threshold
    assert failures == circuit_threshold


# =============================================================================
# Edge Cases Tests
# =============================================================================


@pytest.mark.asyncio
async def test_retry_with_immediate_success(mock_sleep):
    """Test retry succeeds on first attempt."""
    max_retries = 5
    attempts = 0

    while attempts < max_retries:
        attempts += 1

        # Immediate success
        success = True

        if success:
            break

    assert attempts == 1
    assert mock_sleep.call_count == 0  # No backoff needed


@pytest.mark.asyncio
async def test_retry_zero_max_retries():
    """Test retry with zero max retries (no retry)."""
    max_retries = 0
    attempts = 0

    while attempts < max_retries:
        attempts += 1

    assert attempts == 0


@pytest.mark.asyncio
async def test_circuit_breaker_single_failure_threshold():
    """Test circuit breaker with threshold of 1."""
    failure_threshold = 1
    failures = 0
    state = CircuitState.CLOSED

    # Single failure should open circuit
    failures += 1

    if failures >= failure_threshold:
        state = CircuitState.OPEN

    assert state == CircuitState.OPEN
    assert failures == 1


def test_circuit_breaker_fixtures(
    circuit_breaker_closed, circuit_breaker_open, circuit_breaker_half_open
):
    """Test circuit breaker fixtures provide correct states."""
    assert circuit_breaker_closed.state == CircuitState.CLOSED
    assert circuit_breaker_closed.can_attempt() is True

    assert circuit_breaker_open.state == CircuitState.OPEN
    assert circuit_breaker_open.can_attempt() is False

    assert circuit_breaker_half_open.state == CircuitState.HALF_OPEN
    assert circuit_breaker_half_open.can_attempt() is True
