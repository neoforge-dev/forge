"""Deep tests for circuit breaker pattern - boundary conditions and regression coverage.

Targets all state machine transitions, error message formatting, stats integrity,
window expiration, registry isolation, decorator metadata, and the setup_default_breakers
initialization path.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from forge_harness.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    CircuitStats,
    _breakers,
    circuit_breaker,
    get_circuit_breaker,
    list_circuit_breakers,
    reset_circuit_breaker,
    setup_default_breakers,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make(name: str, **kwargs) -> CircuitBreaker:
    """Create an isolated CircuitBreaker not registered in the global registry."""
    defaults = dict(failure_threshold=3, failure_window=10.0, recovery_timeout=1.0, success_threshold=2)
    defaults.update(kwargs)
    return CircuitBreaker(name=name, **defaults)


# ---------------------------------------------------------------------------
# 1. CircuitOpenError message and attributes
# ---------------------------------------------------------------------------

class TestCircuitOpenError:
    def test_message_contains_name(self):
        err = CircuitOpenError("svc-a", 7.5)
        assert "svc-a" in str(err)

    def test_message_contains_retry_after(self):
        err = CircuitOpenError("svc-b", 12.3)
        assert "12.3" in str(err)

    def test_name_attribute_set(self):
        err = CircuitOpenError("my-svc", 0.0)
        assert err.name == "my-svc"

    def test_retry_after_attribute_set(self):
        err = CircuitOpenError("svc-c", 3.14)
        assert err.retry_after == 3.14

    def test_is_exception_subclass(self):
        err = CircuitOpenError("svc", 1.0)
        assert isinstance(err, Exception)

    def test_zero_retry_after_formatting(self):
        err = CircuitOpenError("svc", 0.0)
        assert "0.0" in str(err)


# ---------------------------------------------------------------------------
# 2. CircuitStats dataclass
# ---------------------------------------------------------------------------

class TestCircuitStatsDataclass:
    def test_default_all_zero(self):
        stats = CircuitStats()
        assert stats.total_requests == 0
        assert stats.successful_requests == 0
        assert stats.failed_requests == 0
        assert stats.rejected_requests == 0

    def test_default_timestamps_none(self):
        stats = CircuitStats()
        assert stats.last_failure_time is None
        assert stats.last_success_time is None

    def test_default_state_changes_empty_list(self):
        stats = CircuitStats()
        assert stats.state_changes == []

    def test_to_dict_keys_present(self):
        stats = CircuitStats()
        d = stats.to_dict()
        for key in ("total_requests", "successful_requests", "failed_requests",
                    "rejected_requests", "success_rate", "last_failure_time",
                    "last_success_time", "recent_state_changes"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_success_rate_with_zero_requests(self):
        stats = CircuitStats()
        assert stats.to_dict()["success_rate"] == 0

    def test_to_dict_last_failure_none_when_no_failures(self):
        stats = CircuitStats()
        assert stats.to_dict()["last_failure_time"] is None

    def test_to_dict_last_success_none_when_no_successes(self):
        stats = CircuitStats()
        assert stats.to_dict()["last_success_time"] is None

    def test_to_dict_recent_state_changes_capped_at_five(self):
        from datetime import UTC, datetime
        stats = CircuitStats()
        for i in range(10):
            stats.state_changes.append((datetime.now(UTC), CircuitState.OPEN))
        d = stats.to_dict()
        assert len(d["recent_state_changes"]) == 5

    def test_to_dict_state_change_entry_format(self):
        from datetime import UTC, datetime
        stats = CircuitStats()
        stats.state_changes.append((datetime.now(UTC), CircuitState.CLOSED))
        entry = stats.to_dict()["recent_state_changes"][0]
        assert "time" in entry
        assert "state" in entry
        assert entry["state"] == "closed"


# ---------------------------------------------------------------------------
# 3. CircuitBreaker initialization
# ---------------------------------------------------------------------------

class TestCircuitBreakerInit:
    def test_name_stored(self):
        cb = _make("init-test")
        assert cb.name == "init-test"

    def test_initial_state_closed(self):
        cb = _make("init-closed")
        assert cb.state == CircuitState.CLOSED

    def test_failures_list_empty_on_creation(self):
        cb = _make("init-empty")
        assert cb._failures == []

    def test_half_open_successes_zero_on_creation(self):
        cb = _make("init-half")
        assert cb._half_open_successes == 0

    def test_last_failure_time_none_on_creation(self):
        cb = _make("init-lft")
        assert cb._last_failure_time is None

    def test_stats_object_created(self):
        cb = _make("init-stats")
        assert isinstance(cb.stats, CircuitStats)

    def test_excluded_exceptions_defaults_to_empty_tuple(self):
        cb = CircuitBreaker(name="init-exc", failure_threshold=3)
        assert cb.excluded_exceptions == ()

    def test_excluded_exceptions_stored(self):
        cb = CircuitBreaker(name="init-exc2", failure_threshold=3,
                            excluded_exceptions=(KeyError, IndexError))
        assert KeyError in cb.excluded_exceptions
        assert IndexError in cb.excluded_exceptions


# ---------------------------------------------------------------------------
# 4. _set_state — state transition logging and no-op on same state
# ---------------------------------------------------------------------------

class TestSetState:
    def test_set_state_records_change(self):
        cb = _make("set-state")
        initial = len(cb._stats.state_changes)
        cb._set_state(CircuitState.OPEN)
        assert len(cb._stats.state_changes) == initial + 1

    def test_set_state_no_op_when_same(self):
        cb = _make("set-state-noop")
        cb._set_state(CircuitState.CLOSED)  # already CLOSED
        assert len(cb._stats.state_changes) == 0

    def test_set_state_updates_internal_state(self):
        cb = _make("set-state-update")
        cb._set_state(CircuitState.OPEN)
        assert cb._state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# 5. _clean_old_failures
# ---------------------------------------------------------------------------

class TestCleanOldFailures:
    def test_removes_expired_timestamps(self):
        cb = _make("clean-old", failure_window=0.1)
        cb._failures = [time.monotonic() - 1.0]  # 1 second old, window=0.1
        cb._clean_old_failures()
        assert cb._failures == []

    def test_keeps_recent_timestamps(self):
        cb = _make("clean-recent", failure_window=60.0)
        now = time.monotonic()
        cb._failures = [now - 1.0, now - 0.5]
        cb._clean_old_failures()
        assert len(cb._failures) == 2

    def test_mixed_old_and_recent(self):
        cb = _make("clean-mixed", failure_window=5.0)
        now = time.monotonic()
        cb._failures = [now - 10.0, now - 0.5, now - 0.1]
        cb._clean_old_failures()
        assert len(cb._failures) == 2


# ---------------------------------------------------------------------------
# 6. _should_trip boundary
# ---------------------------------------------------------------------------

class TestShouldTrip:
    def test_below_threshold_does_not_trip(self):
        cb = _make("should-trip-below", failure_threshold=3)
        now = time.monotonic()
        cb._failures = [now, now]  # 2 < 3
        assert cb._should_trip() is False

    def test_at_threshold_trips(self):
        cb = _make("should-trip-at", failure_threshold=3)
        now = time.monotonic()
        cb._failures = [now, now, now]  # exactly 3
        assert cb._should_trip() is True

    def test_above_threshold_trips(self):
        cb = _make("should-trip-above", failure_threshold=3)
        now = time.monotonic()
        cb._failures = [now] * 5
        assert cb._should_trip() is True

    def test_expired_failures_not_counted(self):
        cb = _make("should-trip-expired", failure_threshold=2, failure_window=0.01)
        old = time.monotonic() - 1.0
        cb._failures = [old, old, old]  # 3 old failures
        assert cb._should_trip() is False  # all expired


# ---------------------------------------------------------------------------
# 7. _can_try_half_open and _get_retry_after
# ---------------------------------------------------------------------------

class TestHalfOpenTiming:
    def test_can_try_when_no_failure_recorded(self):
        cb = _make("half-open-no-fail")
        assert cb._can_try_half_open() is True

    def test_cannot_try_immediately_after_failure(self):
        cb = _make("half-open-immediate", recovery_timeout=60.0)
        cb._last_failure_time = time.monotonic()
        assert cb._can_try_half_open() is False

    def test_can_try_after_timeout(self):
        cb = _make("half-open-after", recovery_timeout=0.01)
        cb._last_failure_time = time.monotonic() - 1.0  # 1s ago, timeout=0.01
        assert cb._can_try_half_open() is True

    def test_get_retry_after_zero_with_no_failures(self):
        cb = _make("retry-no-fail")
        assert cb._get_retry_after() == 0.0

    def test_get_retry_after_positive_when_recent_failure(self):
        cb = _make("retry-recent", recovery_timeout=30.0)
        cb._last_failure_time = time.monotonic()
        ra = cb._get_retry_after()
        assert 29.0 < ra <= 30.0

    def test_get_retry_after_zero_when_timeout_elapsed(self):
        cb = _make("retry-elapsed", recovery_timeout=0.01)
        cb._last_failure_time = time.monotonic() - 1.0
        assert cb._get_retry_after() == 0.0


# ---------------------------------------------------------------------------
# 8. to_dict completeness and values
# ---------------------------------------------------------------------------

class TestToDict:
    def test_all_expected_keys(self):
        cb = _make("to-dict")
        d = cb.to_dict()
        for key in ("name", "state", "failure_threshold", "failure_window",
                    "recovery_timeout", "success_threshold", "recent_failures",
                    "half_open_successes", "retry_after", "stats"):
            assert key in d, f"Missing key: {key}"

    def test_retry_after_is_zero_when_closed(self):
        cb = _make("to-dict-closed")
        assert cb.to_dict()["retry_after"] == 0

    @pytest.mark.asyncio
    async def test_retry_after_positive_when_open(self):
        cb = _make("to-dict-open", failure_threshold=2, recovery_timeout=30.0)
        for _ in range(2):
            try:
                async with cb:
                    raise RuntimeError("fail")
            except RuntimeError:
                pass
        d = cb.to_dict()
        assert d["retry_after"] > 0

    def test_recent_failures_reflects_failure_list_length(self):
        cb = _make("to-dict-recent")
        now = time.monotonic()
        cb._failures = [now, now, now]
        assert cb.to_dict()["recent_failures"] == 3


# ---------------------------------------------------------------------------
# 9. Context manager — excluded exceptions not recorded but re-raised
# ---------------------------------------------------------------------------

class TestContextManagerExcluded:
    @pytest.mark.asyncio
    async def test_excluded_exception_not_recorded_in_stats(self):
        cb = _make("ctx-excl", excluded_exceptions=(KeyError,))
        with pytest.raises(KeyError):
            async with cb:
                raise KeyError("excluded key")
        assert cb.stats.failed_requests == 0

    @pytest.mark.asyncio
    async def test_excluded_exception_does_not_trip_circuit(self):
        cb = _make("ctx-excl-trip", failure_threshold=2,
                   excluded_exceptions=(RuntimeError,))
        for _ in range(5):
            try:
                async with cb:
                    raise RuntimeError("excluded")
            except RuntimeError:
                pass
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_non_excluded_exception_is_recorded(self):
        cb = _make("ctx-non-excl", failure_threshold=5,
                   excluded_exceptions=(KeyError,))
        try:
            async with cb:
                raise ValueError("tracked")
        except ValueError:
            pass
        assert cb.stats.failed_requests == 1

    @pytest.mark.asyncio
    async def test_context_manager_returns_breaker_on_enter(self):
        cb = _make("ctx-enter")
        async with cb as entered:
            assert entered is cb


# ---------------------------------------------------------------------------
# 10. call() method edge cases
# ---------------------------------------------------------------------------

class TestCallMethod:
    @pytest.mark.asyncio
    async def test_call_returns_correct_value(self):
        cb = _make("call-return")

        async def f():
            return 99

        assert await cb.call(f) == 99

    @pytest.mark.asyncio
    async def test_call_re_raises_exception(self):
        cb = _make("call-reraise")

        async def f():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await cb.call(f)

    @pytest.mark.asyncio
    async def test_call_excluded_exception_not_tracked(self):
        cb = _make("call-excl", excluded_exceptions=(OSError,))

        async def f():
            raise OSError("excluded")

        with pytest.raises(OSError):
            await cb.call(f)
        assert cb.stats.failed_requests == 0

    @pytest.mark.asyncio
    async def test_call_open_circuit_raises_circuit_open_error(self):
        cb = _make("call-open", failure_threshold=2, recovery_timeout=60.0)
        for _ in range(2):
            try:
                await cb.call(_async_fail)
            except RuntimeError:
                pass
        with pytest.raises(CircuitOpenError):
            await cb.call(_async_ok)

    @pytest.mark.asyncio
    async def test_call_with_kwargs(self):
        cb = _make("call-kwargs")

        async def greet(greeting, name):
            return f"{greeting}, {name}!"

        result = await cb.call(greet, "Hello", name="World")
        assert result == "Hello, World!"


async def _async_fail():
    raise RuntimeError("forced")


async def _async_ok():
    return "ok"


# ---------------------------------------------------------------------------
# 11. Full state machine: CLOSED -> OPEN -> HALF_OPEN -> CLOSED
# ---------------------------------------------------------------------------

class TestFullStateMachine:
    @pytest.mark.asyncio
    async def test_full_cycle(self):
        cb = _make("fsm-full", failure_threshold=2, recovery_timeout=0.2,
                   success_threshold=2)

        # CLOSED -> OPEN
        for _ in range(2):
            try:
                async with cb:
                    raise ConnectionError("down")
            except ConnectionError:
                pass
        assert cb.state == CircuitState.OPEN

        # OPEN -> HALF_OPEN (after timeout)
        await asyncio.sleep(0.25)

        # HALF_OPEN (first success)
        async with cb:
            pass
        assert cb.state == CircuitState.HALF_OPEN

        # HALF_OPEN -> CLOSED (second success meets threshold)
        async with cb:
            pass
        assert cb.state == CircuitState.CLOSED
        assert cb._failures == []

    @pytest.mark.asyncio
    async def test_half_open_to_open_on_failure(self):
        cb = _make("fsm-hopen-fail", failure_threshold=2, recovery_timeout=0.1)
        for _ in range(2):
            try:
                async with cb:
                    raise ConnectionError()
            except ConnectionError:
                pass
        await asyncio.sleep(0.15)
        # Transitions to HALF_OPEN and then fails back to OPEN
        try:
            async with cb:
                raise ConnectionError("still down")
        except ConnectionError:
            pass
        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_closed_single_failure_below_threshold(self):
        cb = _make("fsm-below", failure_threshold=5)
        try:
            async with cb:
                raise RuntimeError("one failure")
        except RuntimeError:
            pass
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_stats_rejected_increments_on_open_circuit(self):
        cb = _make("fsm-rejected", failure_threshold=2, recovery_timeout=60.0)
        for _ in range(2):
            try:
                async with cb:
                    raise RuntimeError()
            except RuntimeError:
                pass
        for _ in range(4):
            try:
                async with cb:
                    pass
            except CircuitOpenError:
                pass
        assert cb.stats.rejected_requests == 4


# ---------------------------------------------------------------------------
# 12. Global registry functions
# ---------------------------------------------------------------------------

class TestRegistryFunctions:
    def test_get_circuit_breaker_idempotent(self):
        cb1 = get_circuit_breaker("registry-idem-deep")
        cb2 = get_circuit_breaker("registry-idem-deep")
        assert cb1 is cb2

    def test_list_circuit_breakers_returns_list_of_dicts(self):
        get_circuit_breaker("registry-list-deep")
        result = list_circuit_breakers()
        assert isinstance(result, list)
        assert all(isinstance(item, dict) for item in result)

    def test_reset_circuit_breaker_true_when_exists(self):
        get_circuit_breaker("registry-reset-deep")
        assert reset_circuit_breaker("registry-reset-deep") is True

    def test_reset_circuit_breaker_false_when_missing(self):
        assert reset_circuit_breaker("__nonexistent_deep__") is False

    def test_reset_clears_last_failure_time(self):
        name = "registry-lft-deep"
        cb = get_circuit_breaker(name, failure_threshold=1)
        cb._last_failure_time = time.monotonic()
        reset_circuit_breaker(name)
        assert cb._last_failure_time is None

    def test_reset_clears_half_open_successes(self):
        name = "registry-ho-deep"
        cb = get_circuit_breaker(name)
        cb._half_open_successes = 3
        reset_circuit_breaker(name)
        assert cb._half_open_successes == 0

    def test_reset_sets_state_to_closed(self):
        name = "registry-state-deep"
        cb = get_circuit_breaker(name)
        cb._state = CircuitState.OPEN
        reset_circuit_breaker(name)
        assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# 13. setup_default_breakers
# ---------------------------------------------------------------------------

class TestSetupDefaultBreakers:
    def test_default_breakers_registered(self):
        setup_default_breakers()
        names = [b["name"] for b in list_circuit_breakers()]
        assert "code_atlas" in names
        assert "tech_diligence" in names
        assert "notion" in names
        assert "github" in names

    def test_calling_twice_does_not_create_duplicates(self):
        setup_default_breakers()
        setup_default_breakers()
        code_atlas_entries = [b for b in list_circuit_breakers() if b["name"] == "code_atlas"]
        assert len(code_atlas_entries) == 1

    def test_notion_has_lower_failure_threshold(self):
        setup_default_breakers()
        breaker = get_circuit_breaker("notion")
        # Notion is configured with failure_threshold=3, others with 5
        assert breaker.failure_threshold == 3


# ---------------------------------------------------------------------------
# 14. Decorator
# ---------------------------------------------------------------------------

class TestDecoratorDeep:
    @pytest.mark.asyncio
    async def test_decorator_preserves_name(self):
        @circuit_breaker("dec-preserve-deep", failure_threshold=5)
        async def my_function():
            return 1

        assert my_function.__name__ == "my_function"

    @pytest.mark.asyncio
    async def test_decorator_preserves_docstring(self):
        @circuit_breaker("dec-docstring-deep", failure_threshold=5)
        async def documented():
            """My docstring."""
            return 1

        assert documented.__doc__ == "My docstring."

    @pytest.mark.asyncio
    async def test_decorator_passes_return_value(self):
        @circuit_breaker("dec-return-deep", failure_threshold=5)
        async def multiply(a, b):
            return a * b

        assert await multiply(6, 7) == 42

    @pytest.mark.asyncio
    async def test_decorator_trips_and_rejects(self):
        @circuit_breaker("dec-trip-deep", failure_threshold=2, recovery_timeout=60.0)
        async def always_fail():
            raise OSError("network gone")

        for _ in range(2):
            try:
                await always_fail()
            except OSError:
                pass

        with pytest.raises(CircuitOpenError):
            await always_fail()

    @pytest.mark.asyncio
    async def test_two_decorators_sharing_same_name(self):
        @circuit_breaker("dec-shared-deep", failure_threshold=2)
        async def func_a():
            raise TimeoutError("slow")

        @circuit_breaker("dec-shared-deep", failure_threshold=2)
        async def func_b():
            return "ok"

        for _ in range(2):
            try:
                await func_a()
            except TimeoutError:
                pass

        with pytest.raises(CircuitOpenError):
            await func_b()
