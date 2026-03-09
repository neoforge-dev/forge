"""Tests for forge_harness.fleet.retry module."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.fleet.retry import (
    CB_FAILURE_THRESHOLD,
    CB_HALF_OPEN_MAX_REQUESTS,
    CB_SUCCESS_THRESHOLD,
    CB_TIMEOUT_SECONDS,
    DEFAULT_BACKOFF_MULTIPLIER,
    DEFAULT_BASE_DELAY,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_DELAY,
    CircuitBreaker,
    CircuitBreakerState,
    CircuitState,
    FailureType,
    RetryConfig,
    RetryResult,
    dispatch_with_retry,
    is_retryable,
)


class TestCircuitState:
    """Tests for CircuitState enum."""

    def test_all_states_defined(self):
        """Test all circuit states are defined."""
        assert CircuitState.CLOSED.value == "closed"
        assert CircuitState.OPEN.value == "open"
        assert CircuitState.HALF_OPEN.value == "half_open"


class TestFailureType:
    """Tests for FailureType enum."""

    def test_all_types_defined(self):
        """Test all failure types are defined."""
        assert FailureType.GIT_LOCK.value == "git_lock"
        assert FailureType.CLI_NOT_READY.value == "cli_not_ready"
        assert FailureType.CLI_CRASHED.value == "cli_crashed"
        assert FailureType.VERIFICATION_TIMEOUT.value == "verification_timeout"
        assert FailureType.SEND_FAILED.value == "send_failed"
        assert FailureType.UNKNOWN.value == "unknown"


class TestIsRetryable:
    """Tests for is_retryable function."""

    def test_git_lock_retryable(self):
        """Test that GIT_LOCK is retryable."""
        assert is_retryable(FailureType.GIT_LOCK) is True

    def test_cli_not_ready_retryable(self):
        """Test that CLI_NOT_READY is retryable."""
        assert is_retryable(FailureType.CLI_NOT_READY) is True

    def test_verification_timeout_retryable(self):
        """Test that VERIFICATION_TIMEOUT is retryable."""
        assert is_retryable(FailureType.VERIFICATION_TIMEOUT) is True

    def test_cli_crashed_not_retryable(self):
        """Test that CLI_CRASHED is not retryable."""
        assert is_retryable(FailureType.CLI_CRASHED) is False

    def test_send_failed_not_retryable(self):
        """Test that SEND_FAILED is not retryable."""
        assert is_retryable(FailureType.SEND_FAILED) is False

    def test_unknown_not_retryable(self):
        """Test that UNKNOWN is not retryable."""
        assert is_retryable(FailureType.UNKNOWN) is False


class TestRetryConfig:
    """Tests for RetryConfig dataclass."""

    def test_default_values(self):
        """Test RetryConfig default values."""
        config = RetryConfig()
        assert config.max_attempts == DEFAULT_MAX_ATTEMPTS
        assert config.base_delay == DEFAULT_BASE_DELAY
        assert config.max_delay == DEFAULT_MAX_DELAY
        assert config.backoff_multiplier == DEFAULT_BACKOFF_MULTIPLIER
        assert config.enable_jitter is True
        assert config.classify_failure is None

    def test_custom_values(self):
        """Test RetryConfig with custom values."""
        config = RetryConfig(
            max_attempts=10,
            base_delay=0.5,
            max_delay=30.0,
            backoff_multiplier=3.0,
            enable_jitter=False,
        )
        assert config.max_attempts == 10
        assert config.base_delay == 0.5
        assert config.max_delay == 30.0
        assert config.backoff_multiplier == 3.0
        assert config.enable_jitter is False

    def test_get_delay_no_jitter(self):
        """Test get_delay without jitter."""
        config = RetryConfig(base_delay=1.0, backoff_multiplier=2.0, enable_jitter=False)
        assert config.get_delay(0) == 1.0
        assert config.get_delay(1) == 2.0
        assert config.get_delay(2) == 4.0

    def test_get_delay_with_jitter(self):
        """Test get_delay with jitter."""
        config = RetryConfig(base_delay=1.0, backoff_multiplier=2.0, enable_jitter=True)
        delay = config.get_delay(0)
        # With jitter, delay should be between 1.0 and 1.2
        assert 1.0 <= delay <= 1.2

    def test_get_delay_max_delay(self):
        """Test get_delay respects max_delay."""
        config = RetryConfig(
            base_delay=1.0, backoff_multiplier=2.0, max_delay=5.0, enable_jitter=False
        )
        # Even with high attempt, should cap at max_delay
        assert config.get_delay(10) == 5.0


class TestCircuitBreakerState:
    """Tests for CircuitBreakerState dataclass."""

    def test_default_values(self):
        """Test default values."""
        state = CircuitBreakerState()
        assert state.state == CircuitState.CLOSED
        assert state.failure_count == 0
        assert state.success_count == 0
        assert state.last_failure_time is None
        assert state.opened_at is None
        assert state.half_open_request_count == 0


class TestCircuitBreaker:
    """Tests for CircuitBreaker class."""

    def test_default_initialization(self):
        """Test default initialization."""
        breaker = CircuitBreaker()
        assert breaker.failure_threshold == CB_FAILURE_THRESHOLD
        assert breaker.success_threshold == CB_SUCCESS_THRESHOLD
        assert breaker.timeout_seconds == CB_TIMEOUT_SECONDS
        assert breaker.half_open_max_requests == CB_HALF_OPEN_MAX_REQUESTS

    def test_custom_initialization(self):
        """Test custom initialization."""
        breaker = CircuitBreaker(
            failure_threshold=10,
            success_threshold=3,
            timeout_seconds=120,
            half_open_max_requests=5,
        )
        assert breaker.failure_threshold == 10
        assert breaker.success_threshold == 3
        assert breaker.timeout_seconds == 120
        assert breaker.half_open_max_requests == 5

    def test_allow_request_closed_state(self):
        """Test request allowed in CLOSED state."""
        breaker = CircuitBreaker()
        assert breaker.allow_request("test-target") is True

    def test_allow_request_open_state_timeout_not_elapsed(self):
        """Test request rejected in OPEN state when timeout not elapsed."""
        breaker = CircuitBreaker(timeout_seconds=60)
        circuit = breaker._get_circuit("test-target")
        circuit.state = CircuitState.OPEN
        circuit.opened_at = datetime.now(UTC)  # Just now

        assert breaker.allow_request("test-target") is False

    def test_allow_request_open_state_timeout_elapsed(self):
        """Test request allowed in OPEN state when timeout elapsed."""
        breaker = CircuitBreaker(timeout_seconds=60)
        circuit = breaker._get_circuit("test-target")
        circuit.state = CircuitState.OPEN
        circuit.opened_at = datetime.now(UTC) - timedelta(seconds=61)

        assert breaker.allow_request("test-target") is True
        assert circuit.state == CircuitState.HALF_OPEN

    def test_allow_request_half_open_under_limit(self):
        """Test request allowed in HALF_OPEN under limit."""
        breaker = CircuitBreaker(half_open_max_requests=3)
        circuit = breaker._get_circuit("test-target")
        circuit.state = CircuitState.HALF_OPEN
        circuit.half_open_request_count = 2

        assert breaker.allow_request("test-target") is True
        assert circuit.half_open_request_count == 3

    def test_allow_request_half_open_over_limit(self):
        """Test request rejected in HALF_OPEN over limit."""
        breaker = CircuitBreaker(half_open_max_requests=3)
        circuit = breaker._get_circuit("test-target")
        circuit.state = CircuitState.HALF_OPEN
        circuit.half_open_request_count = 3

        assert breaker.allow_request("test-target") is False

    def test_record_success_closed_state(self):
        """Test recording success in CLOSED state."""
        breaker = CircuitBreaker()
        circuit = breaker._get_circuit("test-target")
        circuit.failure_count = 5

        breaker.record_success("test-target")

        assert circuit.failure_count == 0

    def test_record_success_half_open_under_threshold(self):
        """Test recording success in HALF_OPEN under threshold."""
        breaker = CircuitBreaker(success_threshold=3)
        circuit = breaker._get_circuit("test-target")
        circuit.state = CircuitState.HALF_OPEN
        circuit.success_count = 1  # Will become 2, still under threshold of 3

        breaker.record_success("test-target")

        assert circuit.success_count == 2
        assert circuit.state == CircuitState.HALF_OPEN  # Not closed yet

    def test_record_success_half_open_at_threshold(self):
        """Test recording success in HALF_OPEN at threshold."""
        breaker = CircuitBreaker(success_threshold=2)
        circuit = breaker._get_circuit("test-target")
        circuit.state = CircuitState.HALF_OPEN
        circuit.success_count = 1
        circuit.failure_count = 10

        breaker.record_success("test-target")

        assert circuit.state == CircuitState.CLOSED
        assert circuit.failure_count == 0
        assert circuit.success_count == 0

    def test_record_failure_closed_under_threshold(self):
        """Test recording failure in CLOSED state under threshold."""
        breaker = CircuitBreaker(failure_threshold=5)

        breaker.record_failure("test-target")

        assert breaker._get_circuit("test-target").failure_count == 1

    def test_record_failure_closed_at_threshold(self):
        """Test recording failure in CLOSED state at threshold."""
        breaker = CircuitBreaker(failure_threshold=5)
        circuit = breaker._get_circuit("test-target")
        circuit.failure_count = 4

        breaker.record_failure("test-target")

        assert circuit.state == CircuitState.OPEN
        assert circuit.opened_at is not None

    def test_record_failure_half_open(self):
        """Test recording failure in HALF_OPEN state."""
        breaker = CircuitBreaker()
        circuit = breaker._get_circuit("test-target")
        circuit.state = CircuitState.HALF_OPEN

        breaker.record_failure("test-target")

        assert circuit.state == CircuitState.OPEN

    def test_get_state(self):
        """Test getting circuit state."""
        breaker = CircuitBreaker()
        circuit = breaker._get_circuit("test-target")
        circuit.state = CircuitState.OPEN

        state = breaker.get_state("test-target")

        assert state == CircuitState.OPEN

    def test_reset(self):
        """Test resetting circuit."""
        breaker = CircuitBreaker()
        circuit = breaker._get_circuit("test-target")
        circuit.state = CircuitState.OPEN
        circuit.failure_count = 100

        breaker.reset("test-target")

        assert breaker.get_state("test-target") == CircuitState.CLOSED
        assert breaker._get_circuit("test-target").failure_count == 0


class TestDispatchWithRetry:
    """Tests for dispatch_with_retry function."""

    @pytest.mark.asyncio
    async def test_success_first_attempt(self):
        """Test successful dispatch on first attempt."""
        mock_result = MagicMock()
        mock_result.delivered = True

        async def mock_dispatch(target, message):
            return mock_result

        result = await dispatch_with_retry(
            target="forge:tech",
            message="test message",
            dispatch_func=mock_dispatch,
        )

        assert result.success is True
        assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_success_after_retries(self):
        """Test successful dispatch after retries."""
        call_count = 0

        async def mock_dispatch(target, message):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Not ready")
            result = MagicMock()
            result.delivered = True
            return result

        result = await dispatch_with_retry(
            target="forge:tech",
            message="test message",
            dispatch_func=mock_dispatch,
            config=RetryConfig(max_attempts=5, base_delay=0.01),
        )

        assert result.success is True
        assert result.attempts == 3

    @pytest.mark.asyncio
    async def test_circuit_breaker_open(self):
        """Test dispatch rejected when circuit breaker is open."""
        breaker = CircuitBreaker()
        breaker.record_failure("forge:tech")  # 5 failures to open
        breaker.record_failure("forge:tech")
        breaker.record_failure("forge:tech")
        breaker.record_failure("forge:tech")
        breaker.record_failure("forge:tech")

        async def mock_dispatch(target, message):
            return MagicMock(delivered=True)

        result = await dispatch_with_retry(
            target="forge:tech",
            message="test message",
            dispatch_func=mock_dispatch,
            circuit_breaker=breaker,
        )

        assert result.success is False
        assert "Circuit breaker is open" in result.failure_reason

    @pytest.mark.asyncio
    async def test_terminal_failure_no_retry(self):
        """Test terminal failure doesn't retry."""

        async def mock_dispatch(target, message):
            raise Exception("CLI crashed")

        result = await dispatch_with_retry(
            target="forge:tech",
            message="test message",
            dispatch_func=mock_dispatch,
            config=RetryConfig(max_attempts=5, base_delay=0.01),
        )

        assert result.success is False
        assert result.failure_type == FailureType.CLI_CRASHED
        assert result.attempts == 1  # No retries for terminal failure

    @pytest.mark.asyncio
    async def test_retryable_failure_retries(self):
        """Test retryable failure triggers retries."""
        call_count = 0

        async def mock_dispatch(target, message):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Git lock")  # Retryable
            result = MagicMock()
            result.delivered = True
            return result

        result = await dispatch_with_retry(
            target="forge:tech",
            message="test message",
            dispatch_func=mock_dispatch,
            config=RetryConfig(max_attempts=5, base_delay=0.01),
        )

        assert result.success is True
        assert result.attempts == 3

    @pytest.mark.asyncio
    async def test_verification_timeout_retryable(self):
        """Test verification timeout is treated as retryable."""
        call_count = 0

        async def mock_dispatch(target, message):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            result.delivered = False
            result.failure_reason = "Timeout"
            return result

        result = await dispatch_with_retry(
            target="forge:tech",
            message="test message",
            dispatch_func=mock_dispatch,
            config=RetryConfig(max_attempts=3, base_delay=0.01),
        )

        # First attempt returns not delivered, should retry
        assert result.failure_type == FailureType.VERIFICATION_TIMEOUT

    @pytest.mark.asyncio
    async def test_custom_classify_failure(self):
        """Test custom failure classification."""

        def custom_classifier(e):
            return FailureType.CLI_CRASHED

        async def mock_dispatch(target, message):
            raise Exception("Some error")

        result = await dispatch_with_retry(
            target="forge:tech",
            message="test message",
            dispatch_func=mock_dispatch,
            config=RetryConfig(
                max_attempts=3,
                base_delay=0.01,
                classify_failure=custom_classifier,
            ),
        )

        assert result.failure_type == FailureType.CLI_CRASHED

    @pytest.mark.asyncio
    async def test_max_attempts_exhausted(self):
        """Test all retries exhausted."""

        async def mock_dispatch(target, message):
            raise Exception("Git lock")  # Retryable

        result = await dispatch_with_retry(
            target="forge:tech",
            message="test message",
            dispatch_func=mock_dispatch,
            config=RetryConfig(max_attempts=3, base_delay=0.01),
        )

        assert result.success is False
        assert result.failure_reason is not None
        assert result.failure_type == FailureType.GIT_LOCK
        assert result.attempts == 3


class TestRetryResult:
    """Tests for RetryResult dataclass."""

    def test_default_values(self):
        """Test RetryResult default values."""
        result = RetryResult(success=True, attempts=1, total_time_ms=100.0)
        assert result.success is True
        assert result.attempts == 1
        assert result.total_time_ms == 100.0
        assert result.failure_reason is None
        assert result.failure_type is None
        assert result.circuit_state == CircuitState.CLOSED


class TestConstants:
    """Tests for module constants."""

    def test_default_constants(self):
        """Test default constants are defined."""
        assert DEFAULT_MAX_ATTEMPTS == 5
        assert DEFAULT_BASE_DELAY == 1.0
        assert DEFAULT_MAX_DELAY == 16.0
        assert DEFAULT_BACKOFF_MULTIPLIER == 2.0

    def test_circuit_breaker_constants(self):
        """Test circuit breaker constants are defined."""
        assert CB_FAILURE_THRESHOLD == 5
        assert CB_SUCCESS_THRESHOLD == 2
        assert CB_TIMEOUT_SECONDS == 60
        assert CB_HALF_OPEN_MAX_REQUESTS == 3


# Import UTC for use in tests
from datetime import UTC, timezone

UTC = UTC
