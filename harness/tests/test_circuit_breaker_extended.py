"""Extended tests for circuit breaker pattern - targeting 80%+ coverage."""

from __future__ import annotations

import asyncio

import pytest

from forge_harness.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    circuit_breaker,
    get_circuit_breaker,
    reset_circuit_breaker,
)


class TestCircuitBreakerConcurrency:
    """Tests for concurrent access and thread safety."""

    @pytest.mark.asyncio
    async def test_concurrent_successful_requests(self):
        """Multiple concurrent successful requests work correctly."""
        breaker = CircuitBreaker(name="test_concurrent", failure_threshold=5)

        async def successful_call(value):
            async with breaker:
                await asyncio.sleep(0.01)
                return value

        # Execute 10 concurrent requests
        results = await asyncio.gather(*[successful_call(i) for i in range(10)])

        assert results == list(range(10))
        assert breaker.state == CircuitState.CLOSED
        assert breaker.stats.successful_requests == 10
        assert breaker.stats.failed_requests == 0

    @pytest.mark.asyncio
    async def test_concurrent_failures_trip_circuit(self):
        """Concurrent failures correctly trip the circuit."""
        breaker = CircuitBreaker(
            name="test_concurrent_fail",
            failure_threshold=3,
            failure_window=10.0,
        )

        async def failing_call():
            try:
                async with breaker:
                    await asyncio.sleep(0.01)
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        # Execute 5 concurrent failing requests
        await asyncio.gather(*[failing_call() for _ in range(5)])

        assert breaker.state == CircuitState.OPEN
        assert breaker.stats.failed_requests >= 3

    @pytest.mark.asyncio
    async def test_concurrent_access_during_state_transition(self):
        """Circuit correctly handles concurrent access during state transitions."""
        breaker = CircuitBreaker(
            name="test_transition",
            failure_threshold=3,
            recovery_timeout=0.5,
        )

        # Trip the circuit
        for _ in range(3):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        assert breaker.state == CircuitState.OPEN

        # Wait for recovery
        await asyncio.sleep(0.6)

        # Multiple concurrent requests during transition to half-open
        results = []

        async def try_request(idx):
            try:
                async with breaker:
                    await asyncio.sleep(0.01)
                    return f"success-{idx}"
            except CircuitOpenError:
                return f"rejected-{idx}"

        results = await asyncio.gather(*[try_request(i) for i in range(5)])

        # At least one should succeed and transition to half-open
        success_count = sum(1 for r in results if r.startswith("success"))
        assert success_count >= 1

    @pytest.mark.asyncio
    async def test_half_open_concurrent_successes_close_circuit(self):
        """Concurrent successes in half-open correctly close circuit."""
        breaker = CircuitBreaker(
            name="test_half_open_success",
            failure_threshold=2,
            recovery_timeout=0.3,
            success_threshold=3,
        )

        # Trip circuit
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        await asyncio.sleep(0.4)

        # Multiple successful requests
        async def success_call():
            async with breaker:
                await asyncio.sleep(0.01)
                return "ok"

        results = await asyncio.gather(*[success_call() for _ in range(5)])

        assert all(r == "ok" for r in results)
        assert breaker.state == CircuitState.CLOSED


class TestCircuitBreakerCallMethodExtended:
    """Extended tests for the call() method."""

    @pytest.mark.asyncio
    async def test_call_with_positional_args(self):
        """Call method properly passes positional arguments."""
        breaker = CircuitBreaker(name="test_args", failure_threshold=3)

        async def add_numbers(a, b, c):
            return a + b + c

        result = await breaker.call(add_numbers, 1, 2, 3)
        assert result == 6

    @pytest.mark.asyncio
    async def test_call_with_keyword_args(self):
        """Call method properly passes keyword arguments."""
        breaker = CircuitBreaker(name="test_kwargs", failure_threshold=3)

        async def format_string(template, name, age):
            return template.format(name=name, age=age)

        result = await breaker.call(format_string, "{name} is {age}", name="Alice", age=30)
        assert result == "Alice is 30"

    @pytest.mark.asyncio
    async def test_call_with_mixed_args(self):
        """Call method works with both positional and keyword arguments."""
        breaker = CircuitBreaker(name="test_mixed", failure_threshold=3)

        async def complex_func(a, b, c=10, d=20):
            return a + b + c + d

        result = await breaker.call(complex_func, 1, 2, d=30)
        assert result == 43  # 1 + 2 + 10 + 30

    @pytest.mark.asyncio
    async def test_call_excluded_exception_not_tracked(self):
        """Call method doesn't track excluded exceptions."""
        breaker = CircuitBreaker(
            name="test_exclude_call",
            failure_threshold=2,
            excluded_exceptions=(ValueError,),
        )

        async def raise_excluded():
            raise ValueError("excluded")

        # Multiple excluded exceptions shouldn't trip circuit
        for _ in range(5):
            with pytest.raises(ValueError):
                await breaker.call(raise_excluded)

        assert breaker.state == CircuitState.CLOSED
        assert breaker.stats.failed_requests == 0

    @pytest.mark.asyncio
    async def test_call_tracks_other_exceptions_when_excluded_set(self):
        """Call method tracks non-excluded exceptions even when exclusions set."""
        breaker = CircuitBreaker(
            name="test_track_others",
            failure_threshold=2,
            excluded_exceptions=(ValueError,),
        )

        async def raise_connection_error():
            raise ConnectionError("connection failed")

        # Connection errors should still trip circuit
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await breaker.call(raise_connection_error)

        assert breaker.state == CircuitState.OPEN
        assert breaker.stats.failed_requests == 2


class TestCircuitBreakerRetryAfter:
    """Tests for retry_after calculation."""

    @pytest.mark.asyncio
    async def test_retry_after_when_just_opened(self):
        """Retry after equals recovery timeout when circuit just opened."""
        breaker = CircuitBreaker(
            name="test_retry",
            failure_threshold=2,
            recovery_timeout=5.0,
        )

        # Trip circuit
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        retry_after = breaker._get_retry_after()
        # Should be close to recovery_timeout (within 0.1s tolerance)
        assert 4.9 <= retry_after <= 5.0

    @pytest.mark.asyncio
    async def test_retry_after_decreases_over_time(self):
        """Retry after value decreases as time passes."""
        breaker = CircuitBreaker(
            name="test_retry_decrease",
            failure_threshold=2,
            recovery_timeout=2.0,
        )

        # Trip circuit
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        retry_1 = breaker._get_retry_after()
        await asyncio.sleep(0.5)
        retry_2 = breaker._get_retry_after()

        assert retry_2 < retry_1
        assert retry_2 >= 0

    @pytest.mark.asyncio
    async def test_retry_after_zero_when_can_try(self):
        """Retry after is zero when circuit can transition to half-open."""
        breaker = CircuitBreaker(
            name="test_retry_zero",
            failure_threshold=2,
            recovery_timeout=0.5,
        )

        # Trip circuit
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        await asyncio.sleep(0.6)
        retry_after = breaker._get_retry_after()
        assert retry_after == 0.0

    def test_retry_after_zero_when_no_failures(self):
        """Retry after is zero when there are no failures."""
        breaker = CircuitBreaker(name="test_no_fail", failure_threshold=3)
        assert breaker._get_retry_after() == 0.0


class TestCircuitBreakerStatsDetailed:
    """Detailed tests for statistics tracking."""

    @pytest.mark.asyncio
    async def test_stats_success_rate_calculation(self):
        """Success rate is correctly calculated."""
        breaker = CircuitBreaker(name="test_rate", failure_threshold=5)

        # 7 successes
        for _ in range(7):
            async with breaker:
                pass

        # 3 failures
        for _ in range(3):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        stats_dict = breaker.stats.to_dict()
        assert stats_dict["success_rate"] == 70.0  # 7/10 * 100

    def test_stats_success_rate_zero_when_no_requests(self):
        """Success rate is 0 when there are no requests."""
        breaker = CircuitBreaker(name="test_no_req", failure_threshold=3)
        stats_dict = breaker.stats.to_dict()
        assert stats_dict["success_rate"] == 0

    @pytest.mark.asyncio
    async def test_stats_success_rate_100_when_all_success(self):
        """Success rate is 100 when all requests succeed."""
        breaker = CircuitBreaker(name="test_all_success", failure_threshold=3)

        for _ in range(5):
            async with breaker:
                pass

        stats_dict = breaker.stats.to_dict()
        assert stats_dict["success_rate"] == 100.0

    @pytest.mark.asyncio
    async def test_stats_success_rate_0_when_all_fail(self):
        """Success rate is 0 when all requests fail."""
        breaker = CircuitBreaker(name="test_all_fail", failure_threshold=10)

        for _ in range(5):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        stats_dict = breaker.stats.to_dict()
        assert stats_dict["success_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_stats_state_changes_tracked(self):
        """State changes are tracked in stats."""
        breaker = CircuitBreaker(
            name="test_changes",
            failure_threshold=2,
            recovery_timeout=0.3,
            success_threshold=2,
        )

        initial_changes = len(breaker.stats.state_changes)

        # Trip to OPEN
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        # Transition to HALF_OPEN
        await asyncio.sleep(0.4)
        async with breaker:
            pass

        # Close circuit
        async with breaker:
            pass

        stats_dict = breaker.stats.to_dict()
        recent_changes = stats_dict["recent_state_changes"]

        # Should have recorded state changes
        assert len(breaker.stats.state_changes) > initial_changes
        # Recent changes should be limited to last 5
        assert len(recent_changes) <= 5

        # Verify state change format
        for change in recent_changes:
            assert "time" in change
            assert "state" in change
            assert change["state"] in ["closed", "open", "half_open"]

    @pytest.mark.asyncio
    async def test_stats_timestamps_updated(self):
        """Last success and failure timestamps are updated."""
        breaker = CircuitBreaker(name="test_timestamps", failure_threshold=5)

        # Success
        async with breaker:
            await asyncio.sleep(0.01)

        assert breaker.stats.last_success_time is not None
        success_time = breaker.stats.last_success_time

        await asyncio.sleep(0.02)

        # Failure
        try:
            async with breaker:
                raise ConnectionError("fail")
        except ConnectionError:
            pass

        assert breaker.stats.last_failure_time is not None
        failure_time = breaker.stats.last_failure_time

        # Failure timestamp should be after success
        assert failure_time > success_time

    @pytest.mark.asyncio
    async def test_stats_rejected_requests_counted(self):
        """Rejected requests are counted when circuit is open."""
        breaker = CircuitBreaker(
            name="test_rejected",
            failure_threshold=2,
            recovery_timeout=10.0,
        )

        # Trip circuit
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        assert breaker.state == CircuitState.OPEN

        # Try to make requests while open
        rejected_count = 0
        for _ in range(3):
            try:
                async with breaker:
                    pass
            except CircuitOpenError:
                rejected_count += 1

        assert rejected_count == 3
        assert breaker.stats.rejected_requests == 3


class TestCircuitBreakerEdgeCasesExtended:
    """Additional edge case tests."""

    @pytest.mark.asyncio
    async def test_failures_cleaned_up_correctly(self):
        """Old failures are removed from tracking."""
        breaker = CircuitBreaker(
            name="test_cleanup",
            failure_threshold=5,
            failure_window=0.5,
        )

        # Add 3 failures
        for _ in range(3):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        assert len(breaker._failures) == 3

        # Wait for window to expire
        await asyncio.sleep(0.6)

        # Clean old failures
        breaker._clean_old_failures()
        assert len(breaker._failures) == 0

    @pytest.mark.asyncio
    async def test_half_open_success_counter_resets(self):
        """Half-open success counter resets when circuit closes."""
        breaker = CircuitBreaker(
            name="test_counter_reset",
            failure_threshold=2,
            recovery_timeout=0.3,
            success_threshold=2,
        )

        # Trip circuit
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        await asyncio.sleep(0.4)

        # Close circuit with successes
        for _ in range(2):
            async with breaker:
                pass

        assert breaker.state == CircuitState.CLOSED
        # Counter is not exposed, but we verify behavior by checking state

    @pytest.mark.asyncio
    async def test_circuit_open_error_contains_retry_info(self):
        """CircuitOpenError includes name and retry_after."""
        breaker = CircuitBreaker(
            name="test_error_info",
            failure_threshold=2,
            recovery_timeout=5.0,
        )

        # Trip circuit
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        try:
            async with breaker:
                pass
        except CircuitOpenError as e:
            assert e.name == "test_error_info"
            assert e.retry_after > 0
            assert "test_error_info" in str(e)
            assert "Retry after" in str(e)

    @pytest.mark.asyncio
    async def test_context_manager_suppresses_no_exceptions(self):
        """Context manager doesn't suppress any exceptions."""
        breaker = CircuitBreaker(name="test_no_suppress", failure_threshold=3)

        with pytest.raises(ValueError):
            async with breaker:
                raise ValueError("test error")

    @pytest.mark.asyncio
    async def test_should_trip_with_exact_threshold(self):
        """Circuit trips when failures exactly match threshold."""
        breaker = CircuitBreaker(
            name="test_exact",
            failure_threshold=3,
            failure_window=10.0,
        )

        # Exactly 3 failures
        for _ in range(3):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        assert breaker.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_half_open_exactly_at_success_threshold(self):
        """Circuit closes when successes exactly match threshold."""
        breaker = CircuitBreaker(
            name="test_exact_success",
            failure_threshold=2,
            recovery_timeout=0.3,
            success_threshold=3,
        )

        # Trip circuit
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        await asyncio.sleep(0.4)

        # Exactly 3 successes
        for i in range(3):
            async with breaker:
                pass
            if i < 2:
                assert breaker.state == CircuitState.HALF_OPEN
            else:
                assert breaker.state == CircuitState.CLOSED

    def test_can_try_half_open_with_no_last_failure(self):
        """_can_try_half_open returns True when no failures recorded."""
        breaker = CircuitBreaker(name="test_no_failure", failure_threshold=3)
        assert breaker._can_try_half_open() is True

    @pytest.mark.asyncio
    async def test_failures_within_window_accumulate(self):
        """Failures within window accumulate correctly."""
        breaker = CircuitBreaker(
            name="test_accumulate",
            failure_threshold=3,
            failure_window=2.0,
        )

        # Add 2 failures
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        assert breaker.state == CircuitState.CLOSED

        # Add 1 more within window - should trip
        await asyncio.sleep(0.1)
        try:
            async with breaker:
                raise ConnectionError("fail")
        except ConnectionError:
            pass

        assert breaker.state == CircuitState.OPEN


class TestCircuitBreakerDecoratorExtended:
    """Extended decorator tests."""

    @pytest.mark.asyncio
    async def test_decorator_preserves_function_metadata(self):
        """Decorator preserves original function name and docstring."""

        @circuit_breaker("test_metadata", failure_threshold=3)
        async def my_documented_function():
            """This is a test function."""
            return 42

        assert my_documented_function.__name__ == "my_documented_function"
        assert "test function" in my_documented_function.__doc__

    @pytest.mark.asyncio
    async def test_decorator_with_function_args(self):
        """Decorator works with functions that have arguments."""

        @circuit_breaker("test_with_args", failure_threshold=3)
        async def multiply(a, b):
            return a * b

        result = await multiply(3, 4)
        assert result == 12

    @pytest.mark.asyncio
    async def test_decorator_reuses_circuit_breaker(self):
        """Decorated functions share the same circuit breaker."""

        @circuit_breaker("shared_breaker", failure_threshold=2)
        async def func1():
            raise ConnectionError("fail")

        @circuit_breaker("shared_breaker", failure_threshold=2)
        async def func2():
            return "success"

        # func1 trips the circuit
        for _ in range(2):
            try:
                await func1()
            except ConnectionError:
                pass

        # func2 should see the open circuit
        with pytest.raises(CircuitOpenError):
            await func2()


class TestCircuitBreakerGlobalRegistryExtended:
    """Extended tests for global registry functions."""

    def test_to_dict_complete_format(self):
        """to_dict includes all expected fields."""
        breaker = CircuitBreaker(
            name="test_complete",
            failure_threshold=5,
            failure_window=60.0,
            recovery_timeout=30.0,
            success_threshold=2,
        )

        data = breaker.to_dict()

        # Verify all fields
        assert data["name"] == "test_complete"
        assert data["state"] == "closed"
        assert data["failure_threshold"] == 5
        assert data["failure_window"] == 60.0
        assert data["recovery_timeout"] == 30.0
        assert data["success_threshold"] == 2
        assert "recent_failures" in data
        assert "half_open_successes" in data
        assert "retry_after" in data
        assert "stats" in data

    @pytest.mark.asyncio
    async def test_reset_clears_all_state(self):
        """Reset clears failures, successes, and timestamps."""
        breaker = get_circuit_breaker("test_reset_state", failure_threshold=2)

        # Create some state
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        assert breaker.state == CircuitState.OPEN
        assert len(breaker._failures) > 0

        # Reset
        reset_circuit_breaker("test_reset_state")

        assert breaker.state == CircuitState.CLOSED
        assert len(breaker._failures) == 0
        assert breaker._half_open_successes == 0
        assert breaker._last_failure_time is None


class TestCircuitBreakerStateProperty:
    """Tests for state property access."""

    def test_state_property_returns_current_state(self):
        """State property returns current state."""
        breaker = CircuitBreaker(name="test_state_prop", failure_threshold=3)
        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_state_property_reflects_transitions(self):
        """State property reflects state transitions."""
        breaker = CircuitBreaker(name="test_state_transitions", failure_threshold=2)

        assert breaker.state == CircuitState.CLOSED

        # Trip to open
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        assert breaker.state == CircuitState.OPEN


class TestCircuitBreakerStatsProperty:
    """Tests for stats property access."""

    def test_stats_property_returns_stats_object(self):
        """Stats property returns CircuitStats object."""
        breaker = CircuitBreaker(name="test_stats_prop", failure_threshold=3)
        stats = breaker.stats
        assert stats is not None
        assert hasattr(stats, "total_requests")
        assert hasattr(stats, "successful_requests")
        assert hasattr(stats, "failed_requests")


class TestCircuitBreakerInitialization:
    """Tests for circuit breaker initialization."""

    def test_default_excluded_exceptions_empty(self):
        """Default excluded_exceptions is empty tuple."""
        breaker = CircuitBreaker(name="test_defaults", failure_threshold=3)
        assert breaker.excluded_exceptions == ()

    def test_custom_excluded_exceptions(self):
        """Custom excluded_exceptions are stored."""
        breaker = CircuitBreaker(
            name="test_custom_exclude",
            failure_threshold=3,
            excluded_exceptions=(ValueError, TypeError),
        )
        assert ValueError in breaker.excluded_exceptions
        assert TypeError in breaker.excluded_exceptions

    def test_initialization_values(self):
        """Initialization sets correct values."""
        breaker = CircuitBreaker(
            name="test_init",
            failure_threshold=7,
            failure_window=120.0,
            recovery_timeout=45.0,
            success_threshold=5,
        )
        assert breaker.name == "test_init"
        assert breaker.failure_threshold == 7
        assert breaker.failure_window == 120.0
        assert breaker.recovery_timeout == 45.0
        assert breaker.success_threshold == 5
