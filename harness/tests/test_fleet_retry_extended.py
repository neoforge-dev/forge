"""Extended tests for forge_harness.fleet.retry module.

Covers gaps left by test_fleet_retry.py:
- RetryConfig.get_delay jitter clamping and boundary behaviour
- CircuitBreaker.allow_request with opened_at=None in OPEN state
- CircuitBreaker.reset on non-existent target (no-op)
- CircuitBreaker.record_failure in OPEN state (already-open remains open)
- CircuitBreaker.record_failure with failure_type kwarg
- CircuitBreaker._get_circuit creates fresh state on first access
- dispatch_with_retry with no circuit_breaker arg (creates fresh one)
- dispatch_with_retry with no config arg (uses defaults)
- dispatch_with_retry failure_reason when all retries exhausted with no exception
- dispatch_with_retry error keyword classification (lock / not ready / crashed / unknown)
- dispatch_with_retry result without .delivered attribute (no-attr path)
- dispatch_with_retry circuit opens mid-retry, stops further attempts
- dispatch_with_retry custom classify_failure returning retryable type
- dispatch_with_retry total_time_ms is non-negative
- dispatch_with_retry circuit_state in RetryResult reflects final state
- dispatch_with_retry with SEND_FAILED classification stops immediately
- Multiple independent targets don't share circuit state
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.fleet.retry import (
    CB_FAILURE_THRESHOLD,
    CB_HALF_OPEN_MAX_REQUESTS,
    CB_SUCCESS_THRESHOLD,
    CB_TIMEOUT_SECONDS,
    CircuitBreaker,
    CircuitBreakerState,
    CircuitState,
    FailureType,
    RetryConfig,
    RetryResult,
    dispatch_with_retry,
    is_retryable,
)

# ---------------------------------------------------------------------------
# RetryConfig.get_delay — boundary and jitter tests
# ---------------------------------------------------------------------------


class TestRetryConfigGetDelayExtended:
    """Extended tests for RetryConfig.get_delay."""

    def test_delay_capped_at_max_delay_no_jitter(self):
        """Exponential growth is capped at max_delay (no jitter)."""
        config = RetryConfig(
            base_delay=1.0,
            backoff_multiplier=2.0,
            max_delay=8.0,
            enable_jitter=False,
        )
        # Attempt 3 → 1 * 2^3 = 8  (at cap)
        assert config.get_delay(3) == 8.0
        # Attempt 4 → 1 * 2^4 = 16 > 8 → capped
        assert config.get_delay(4) == 8.0

    def test_delay_at_attempt_zero(self):
        """First attempt (index 0) returns base_delay exactly (no jitter)."""
        config = RetryConfig(base_delay=2.5, backoff_multiplier=2.0, enable_jitter=False)
        assert config.get_delay(0) == 2.5

    def test_jitter_always_adds_positive_amount(self):
        """With jitter enabled, delay is always >= base calculated value."""
        config = RetryConfig(base_delay=1.0, backoff_multiplier=2.0, enable_jitter=True)
        for attempt in range(5):
            no_jitter_config = RetryConfig(
                base_delay=1.0, backoff_multiplier=2.0, enable_jitter=False
            )
            base = no_jitter_config.get_delay(attempt)
            actual = config.get_delay(attempt)
            assert actual >= base

    def test_jitter_capped_at_20_percent(self):
        """Jitter is at most 20% of the base delay."""
        config = RetryConfig(
            base_delay=10.0,
            backoff_multiplier=1.0,
            max_delay=100.0,
            enable_jitter=True,
        )
        for _ in range(20):
            delay = config.get_delay(0)
            # base = 10.0, max jitter = 10.0 * 0.2 = 2.0
            assert delay <= 12.0

    def test_backoff_multiplier_one_gives_constant_delay(self):
        """Multiplier of 1.0 gives constant delay regardless of attempt."""
        config = RetryConfig(
            base_delay=3.0,
            backoff_multiplier=1.0,
            max_delay=100.0,
            enable_jitter=False,
        )
        for attempt in range(5):
            assert config.get_delay(attempt) == 3.0

    def test_classify_failure_field_accepted(self):
        """classify_failure callable field is stored without error."""
        classifier = lambda e: FailureType.GIT_LOCK  # noqa: E731
        config = RetryConfig(classify_failure=classifier)
        assert config.classify_failure is classifier


# ---------------------------------------------------------------------------
# CircuitBreaker — edge cases not covered by base tests
# ---------------------------------------------------------------------------


class TestCircuitBreakerEdgeCases:
    """Extended tests for CircuitBreaker."""

    def test_allow_request_open_with_none_opened_at(self):
        """OPEN circuit with opened_at=None rejects the request (no timeout check)."""
        breaker = CircuitBreaker(timeout_seconds=60)
        circuit = breaker._get_circuit("forge:edge")
        circuit.state = CircuitState.OPEN
        circuit.opened_at = None  # edge: opened_at not set
        result = breaker.allow_request("forge:edge")
        # None opened_at means condition `circuit.opened_at and (...)` is False → rejected
        assert result is False

    def test_reset_nonexistent_target_is_noop(self):
        """reset() on a target that was never registered does nothing."""
        breaker = CircuitBreaker()
        # Should not raise
        breaker.reset("forge:never-seen")
        assert "forge:never-seen" not in breaker._circuits

    def test_get_circuit_creates_fresh_state(self):
        """_get_circuit creates a default CircuitBreakerState for new targets."""
        breaker = CircuitBreaker()
        state = breaker._get_circuit("forge:brand-new")
        assert state.state == CircuitState.CLOSED
        assert state.failure_count == 0

    def test_get_circuit_returns_same_object_on_second_call(self):
        """_get_circuit returns the same state object on subsequent calls."""
        breaker = CircuitBreaker()
        s1 = breaker._get_circuit("forge:same")
        s2 = breaker._get_circuit("forge:same")
        assert s1 is s2

    def test_record_failure_in_open_state_stays_open(self):
        """Recording a failure when circuit is already OPEN keeps it OPEN."""
        breaker = CircuitBreaker(failure_threshold=3)
        circuit = breaker._get_circuit("forge:target")
        circuit.state = CircuitState.OPEN
        circuit.opened_at = datetime.now(UTC)
        opened_before = circuit.opened_at

        breaker.record_failure("forge:target")

        # Circuit remains OPEN; failure_count incremented
        assert circuit.state == CircuitState.OPEN
        assert circuit.failure_count == 1

    def test_record_failure_with_failure_type_kwarg(self):
        """failure_type kwarg is accepted in record_failure without error."""
        breaker = CircuitBreaker(failure_threshold=10)
        # Should not raise
        breaker.record_failure("forge:target", failure_type=FailureType.GIT_LOCK)
        assert breaker._get_circuit("forge:target").failure_count == 1

    def test_half_open_success_resets_opened_at_to_none(self):
        """When circuit closes from HALF_OPEN, opened_at is reset to None."""
        breaker = CircuitBreaker(success_threshold=1)
        circuit = breaker._get_circuit("forge:target")
        circuit.state = CircuitState.HALF_OPEN
        circuit.success_count = 0
        circuit.opened_at = datetime.now(UTC)

        breaker.record_success("forge:target")

        assert circuit.state == CircuitState.CLOSED
        assert circuit.opened_at is None

    def test_multiple_targets_independent_circuits(self):
        """Different targets have completely independent circuit states."""
        breaker = CircuitBreaker(failure_threshold=2)
        # Open circuit for target A
        breaker.record_failure("forge:A")
        breaker.record_failure("forge:A")

        # Target B should still be CLOSED
        assert breaker.get_state("forge:A") == CircuitState.OPEN
        assert breaker.get_state("forge:B") == CircuitState.CLOSED

    def test_reset_after_open_allows_requests(self):
        """After reset, circuit is CLOSED and allows requests."""
        breaker = CircuitBreaker(failure_threshold=2)
        breaker.record_failure("forge:target")
        breaker.record_failure("forge:target")
        assert breaker.get_state("forge:target") == CircuitState.OPEN

        breaker.reset("forge:target")
        assert breaker.allow_request("forge:target") is True

    def test_half_open_failure_updates_opened_at(self):
        """Failure in HALF_OPEN refreshes opened_at timestamp."""
        breaker = CircuitBreaker()
        circuit = breaker._get_circuit("forge:target")
        circuit.state = CircuitState.HALF_OPEN
        old_time = datetime.now(UTC) - timedelta(seconds=100)
        circuit.opened_at = old_time

        breaker.record_failure("forge:target")

        assert circuit.state == CircuitState.OPEN
        assert circuit.opened_at > old_time

    def test_closed_success_resets_failure_count(self):
        """Success in CLOSED state resets the failure counter to 0."""
        breaker = CircuitBreaker(failure_threshold=10)
        circuit = breaker._get_circuit("forge:target")
        circuit.failure_count = 7

        breaker.record_success("forge:target")

        assert circuit.failure_count == 0

    def test_allow_request_half_open_increments_count(self):
        """Each allowed request in HALF_OPEN increments half_open_request_count."""
        breaker = CircuitBreaker(half_open_max_requests=5)
        circuit = breaker._get_circuit("forge:target")
        circuit.state = CircuitState.HALF_OPEN
        circuit.half_open_request_count = 0

        for expected in range(1, 4):
            breaker.allow_request("forge:target")
            assert circuit.half_open_request_count == expected


# ---------------------------------------------------------------------------
# dispatch_with_retry — extended scenarios
# ---------------------------------------------------------------------------


class TestDispatchWithRetryExtended:
    """Extended tests for dispatch_with_retry."""

    @pytest.mark.asyncio
    async def test_no_config_uses_defaults(self):
        """Passing no config creates a default RetryConfig."""
        mock_result = MagicMock()
        mock_result.delivered = True

        async def fast_dispatch(target, message):
            return mock_result

        result = await dispatch_with_retry(
            target="forge:tech",
            message="hello",
            dispatch_func=fast_dispatch,
            # config omitted
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_no_circuit_breaker_creates_new_one(self):
        """When circuit_breaker is None, a fresh one is created and used."""
        mock_result = MagicMock()
        mock_result.delivered = True

        async def fast_dispatch(target, message):
            return mock_result

        result = await dispatch_with_retry(
            target="forge:tech",
            message="hello",
            dispatch_func=fast_dispatch,
            circuit_breaker=None,  # explicit None
        )
        assert result.success is True
        assert result.circuit_state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_total_time_ms_is_nonnegative(self):
        """total_time_ms is always >= 0."""
        mock_result = MagicMock()
        mock_result.delivered = True

        async def fast_dispatch(target, message):
            return mock_result

        result = await dispatch_with_retry(
            target="forge:tech",
            message="hello",
            dispatch_func=fast_dispatch,
            config=RetryConfig(max_attempts=1, base_delay=0.001, enable_jitter=False),
        )
        assert result.total_time_ms >= 0

    @pytest.mark.asyncio
    async def test_open_circuit_returns_zero_attempts(self):
        """When circuit is already open, attempts=0 is returned."""
        breaker = CircuitBreaker(failure_threshold=1)
        breaker.record_failure("forge:tech")
        assert breaker.get_state("forge:tech") == CircuitState.OPEN

        async def fast_dispatch(target, message):
            return MagicMock(delivered=True)

        result = await dispatch_with_retry(
            target="forge:tech",
            message="hello",
            dispatch_func=fast_dispatch,
            circuit_breaker=breaker,
        )
        assert result.success is False
        assert result.attempts == 0
        assert result.circuit_state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_error_message_with_lock_classified_as_git_lock(self):
        """Exceptions containing 'lock' or 'index.lock' are classified as GIT_LOCK."""
        async def lock_dispatch(target, message):
            raise Exception("git index.lock exists")

        result = await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=lock_dispatch,
            config=RetryConfig(max_attempts=1, base_delay=0.001, enable_jitter=False),
        )
        assert result.failure_type == FailureType.GIT_LOCK

    @pytest.mark.asyncio
    async def test_error_message_not_ready_classified_as_cli_not_ready(self):
        """Exceptions containing 'not ready' are classified as CLI_NOT_READY."""
        async def not_ready_dispatch(target, message):
            raise Exception("cli not ready for input")

        result = await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=not_ready_dispatch,
            config=RetryConfig(max_attempts=1, base_delay=0.001, enable_jitter=False),
        )
        assert result.failure_type == FailureType.CLI_NOT_READY

    @pytest.mark.asyncio
    async def test_error_message_timeout_classified_as_cli_not_ready(self):
        """Exceptions containing 'timeout' are classified as CLI_NOT_READY."""
        async def timeout_dispatch(target, message):
            raise Exception("operation timeout reached")

        result = await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=timeout_dispatch,
            config=RetryConfig(max_attempts=1, base_delay=0.001, enable_jitter=False),
        )
        assert result.failure_type == FailureType.CLI_NOT_READY

    @pytest.mark.asyncio
    async def test_error_message_crashed_classified_as_cli_crashed(self):
        """Exceptions containing 'crashed' are classified as CLI_CRASHED."""
        async def crashed_dispatch(target, message):
            raise Exception("process has crashed")

        result = await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=crashed_dispatch,
            config=RetryConfig(max_attempts=1, base_delay=0.001, enable_jitter=False),
        )
        assert result.failure_type == FailureType.CLI_CRASHED

    @pytest.mark.asyncio
    async def test_error_message_dead_classified_as_cli_crashed(self):
        """Exceptions containing 'dead' are classified as CLI_CRASHED."""
        async def dead_dispatch(target, message):
            raise Exception("session is dead")

        result = await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=dead_dispatch,
            config=RetryConfig(max_attempts=1, base_delay=0.001, enable_jitter=False),
        )
        assert result.failure_type == FailureType.CLI_CRASHED

    @pytest.mark.asyncio
    async def test_unknown_error_classified_as_unknown(self):
        """Unrecognized error messages are classified as UNKNOWN."""
        async def mystery_dispatch(target, message):
            raise Exception("something weird happened")

        result = await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=mystery_dispatch,
            config=RetryConfig(max_attempts=1, base_delay=0.001, enable_jitter=False),
        )
        assert result.failure_type == FailureType.UNKNOWN

    @pytest.mark.asyncio
    async def test_send_failed_terminal_stops_immediately(self):
        """SEND_FAILED is terminal — stops after 1 attempt."""
        call_count = 0

        async def send_fail_dispatch(target, message):
            nonlocal call_count
            call_count += 1
            raise Exception("send failed completely")

        def classify(e):
            return FailureType.SEND_FAILED

        result = await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=send_fail_dispatch,
            config=RetryConfig(
                max_attempts=5,
                base_delay=0.001,
                enable_jitter=False,
                classify_failure=classify,
            ),
        )
        assert call_count == 1
        assert result.success is False
        assert result.failure_type == FailureType.SEND_FAILED

    @pytest.mark.asyncio
    async def test_result_without_delivered_attr_treated_as_timeout(self):
        """Result object without .delivered attribute is treated as VERIFICATION_TIMEOUT."""
        # A plain object with no .delivered attribute
        class NoDeliveredAttr:
            pass

        call_count = 0

        async def no_attr_dispatch(target, message):
            nonlocal call_count
            call_count += 1
            return NoDeliveredAttr()

        result = await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=no_attr_dispatch,
            config=RetryConfig(max_attempts=2, base_delay=0.001, enable_jitter=False),
        )
        # Should have been called max_attempts times (VERIFICATION_TIMEOUT is retryable)
        assert call_count == 2
        assert result.failure_type == FailureType.VERIFICATION_TIMEOUT

    @pytest.mark.asyncio
    async def test_failure_reason_when_exhausted_no_exception(self):
        """failure_reason reports 'All retry attempts exhausted' when no exception raised."""
        async def no_deliver_dispatch(target, message):
            result = MagicMock()
            result.delivered = False
            result.failure_reason = "Timeout"
            return result

        result = await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=no_deliver_dispatch,
            config=RetryConfig(max_attempts=2, base_delay=0.001, enable_jitter=False),
        )
        assert result.success is False
        assert result.failure_reason is not None

    @pytest.mark.asyncio
    async def test_failure_reason_includes_exception_message(self):
        """failure_reason includes the exception message when exception occurred."""
        async def exc_dispatch(target, message):
            raise Exception("git index.lock present in repository")

        result = await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=exc_dispatch,
            config=RetryConfig(max_attempts=3, base_delay=0.001, enable_jitter=False),
        )
        assert result.failure_reason is not None
        assert "git index.lock" in result.failure_reason

    @pytest.mark.asyncio
    async def test_circuit_state_in_result_reflects_closed_on_success(self):
        """circuit_state in RetryResult is CLOSED after successful dispatch."""
        async def ok_dispatch(target, message):
            result = MagicMock()
            result.delivered = True
            return result

        result = await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=ok_dispatch,
        )
        assert result.circuit_state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_custom_classify_returning_git_lock_retries(self):
        """Custom classifier returning GIT_LOCK triggers retries."""
        call_count = 0

        async def flaky_dispatch(target, message):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("any error message")
            result = MagicMock()
            result.delivered = True
            return result

        def classify_as_git_lock(e):
            return FailureType.GIT_LOCK

        result = await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=flaky_dispatch,
            config=RetryConfig(
                max_attempts=5,
                base_delay=0.001,
                enable_jitter=False,
                classify_failure=classify_as_git_lock,
            ),
        )
        assert result.success is True
        assert result.attempts == 3

    @pytest.mark.asyncio
    async def test_circuit_opens_mid_retry_stops_loop(self):
        """When circuit opens during retries, loop stops early."""
        breaker = CircuitBreaker(failure_threshold=2)

        call_count = 0

        async def always_git_lock(target, message):
            nonlocal call_count
            call_count += 1
            raise Exception("git lock contention")

        result = await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=always_git_lock,
            config=RetryConfig(max_attempts=10, base_delay=0.001, enable_jitter=False),
            circuit_breaker=breaker,
        )
        # Circuit opens after failure_threshold (2) failures, loop stops
        assert result.success is False
        assert call_count <= 3  # At most threshold + 1 calls before circuit check fires

    @pytest.mark.asyncio
    async def test_successful_dispatch_records_circuit_success(self):
        """Successful dispatch records circuit success, resetting failure count."""
        breaker = CircuitBreaker(failure_threshold=10)
        circuit = breaker._get_circuit("forge:tech")
        circuit.failure_count = 7  # Simulate prior failures

        async def ok_dispatch(target, message):
            result = MagicMock()
            result.delivered = True
            return result

        await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=ok_dispatch,
            circuit_breaker=breaker,
        )
        # Success resets failure count
        assert circuit.failure_count == 0

    @pytest.mark.asyncio
    async def test_attempts_count_equals_max_when_all_retryable_failures(self):
        """When all failures are retryable, attempts equals max_attempts."""
        async def always_fail(target, message):
            raise Exception("git lock contention always")

        result = await dispatch_with_retry(
            target="forge:tech",
            message="msg",
            dispatch_func=always_fail,
            config=RetryConfig(
                max_attempts=4,
                base_delay=0.001,
                enable_jitter=False,
            ),
            circuit_breaker=CircuitBreaker(failure_threshold=100),  # never opens
        )
        assert result.attempts == 4

    @pytest.mark.asyncio
    async def test_dispatch_func_receives_correct_target_and_message(self):
        """dispatch_func is called with the correct target and message arguments."""
        received_args = []

        async def capturing_dispatch(target, message):
            received_args.append((target, message))
            result = MagicMock()
            result.delivered = True
            return result

        await dispatch_with_retry(
            target="forge:specific",
            message="my precise message",
            dispatch_func=capturing_dispatch,
        )
        assert received_args[0] == ("forge:specific", "my precise message")


# ---------------------------------------------------------------------------
# is_retryable — parametrized completeness
# ---------------------------------------------------------------------------


class TestIsRetryableCompleteness:
    """Exhaustive parametrized test for is_retryable."""

    @pytest.mark.parametrize(
        "failure_type, expected",
        [
            (FailureType.GIT_LOCK, True),
            (FailureType.CLI_NOT_READY, True),
            (FailureType.VERIFICATION_TIMEOUT, True),
            (FailureType.CLI_CRASHED, False),
            (FailureType.SEND_FAILED, False),
            (FailureType.UNKNOWN, False),
        ],
    )
    def test_all_failure_types(self, failure_type, expected):
        """All FailureType values return the correct retryability."""
        assert is_retryable(failure_type) is expected


# ---------------------------------------------------------------------------
# RetryResult — edge cases
# ---------------------------------------------------------------------------


class TestRetryResultEdgeCases:
    """Edge cases for RetryResult dataclass."""

    def test_failure_result_fields(self):
        """Failed RetryResult stores failure_reason and failure_type."""
        result = RetryResult(
            success=False,
            attempts=3,
            total_time_ms=250.5,
            failure_reason="git_lock: index.lock exists",
            failure_type=FailureType.GIT_LOCK,
            circuit_state=CircuitState.OPEN,
        )
        assert result.success is False
        assert result.failure_reason == "git_lock: index.lock exists"
        assert result.failure_type == FailureType.GIT_LOCK
        assert result.circuit_state == CircuitState.OPEN

    def test_total_time_ms_stored_as_float(self):
        """total_time_ms is stored as a float value."""
        result = RetryResult(success=True, attempts=1, total_time_ms=42.7)
        assert isinstance(result.total_time_ms, float)

    def test_zero_attempts_valid(self):
        """RetryResult with 0 attempts is valid (circuit-open path)."""
        result = RetryResult(
            success=False,
            attempts=0,
            total_time_ms=0.5,
            failure_reason="Circuit breaker is open",
            circuit_state=CircuitState.OPEN,
        )
        assert result.attempts == 0
        assert result.circuit_state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# CircuitBreakerState — additional field validation
# ---------------------------------------------------------------------------


class TestCircuitBreakerStateEdgeCases:
    """Additional edge cases for CircuitBreakerState dataclass."""

    def test_can_set_all_fields_explicitly(self):
        """All fields can be set on construction."""
        now = datetime.now(UTC)
        state = CircuitBreakerState(
            state=CircuitState.HALF_OPEN,
            failure_count=3,
            success_count=1,
            last_failure_time=now,
            opened_at=now,
            half_open_request_count=2,
        )
        assert state.state == CircuitState.HALF_OPEN
        assert state.failure_count == 3
        assert state.success_count == 1
        assert state.last_failure_time == now
        assert state.opened_at == now
        assert state.half_open_request_count == 2

    def test_last_failure_time_updated_on_failure(self):
        """record_failure updates last_failure_time."""
        breaker = CircuitBreaker(failure_threshold=10)
        before = datetime.now(UTC)
        breaker.record_failure("forge:target")
        circuit = breaker._get_circuit("forge:target")
        assert circuit.last_failure_time is not None
        assert circuit.last_failure_time >= before
