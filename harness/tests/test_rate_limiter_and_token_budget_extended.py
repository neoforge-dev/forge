"""
Comprehensive tests for:
  - forge_harness/webhook_server/infrastructure/rate_limiter.py
  - forge_harness/token_budget.py

Coverage targets:
  - rate_limiter.py:  29% → 85%+
  - token_budget.py:  extend existing coverage with edge cases
"""

import os
import time
from datetime import datetime, timedelta
from threading import Thread
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# token_budget imports
# ---------------------------------------------------------------------------
from forge_harness.token_budget import (
    AgentBudget,
    TokenBudgetTracker,
    create_tracker_from_env,
)

# ---------------------------------------------------------------------------
# rate_limiter imports
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.infrastructure.rate_limiter import (
    RateLimitConfig,
    RateLimiter,
    TokenBucket,
    get_rate_limiter,
)

# ===========================================================================
# TokenBucket — algorithm unit tests
# ===========================================================================


class TestTokenBucketInitialization:
    """TokenBucket correctly initialises its internal state."""

    def test_starts_with_full_bucket(self):
        bucket = TokenBucket(rate_per_second=1.0, burst_size=10)
        assert bucket.tokens == float(10)

    def test_stores_rate_and_burst(self):
        bucket = TokenBucket(rate_per_second=2.5, burst_size=5)
        assert bucket.rate == 2.5
        assert bucket.burst_size == 5

    def test_last_update_is_monotonic(self):
        before = time.monotonic()
        bucket = TokenBucket(rate_per_second=1.0, burst_size=10)
        after = time.monotonic()
        assert before <= bucket.last_update <= after

    def test_lock_is_created(self):
        bucket = TokenBucket(rate_per_second=1.0, burst_size=10)
        assert bucket._lock is not None

    def test_burst_size_one(self):
        bucket = TokenBucket(rate_per_second=1.0, burst_size=1)
        assert bucket.tokens == 1.0
        assert bucket.consume() is True
        assert bucket.consume() is False


class TestTokenBucketConsume:
    """TokenBucket.consume() correctly applies the token-bucket algorithm."""

    def test_consume_returns_true_when_tokens_available(self):
        bucket = TokenBucket(rate_per_second=1.0, burst_size=10)
        assert bucket.consume() is True

    def test_consume_decrements_tokens(self):
        bucket = TokenBucket(rate_per_second=1.0, burst_size=10)
        bucket.consume()
        assert bucket.tokens == pytest.approx(9.0, abs=0.01)

    def test_consume_exhaustion(self):
        bucket = TokenBucket(rate_per_second=0.0001, burst_size=3)
        assert bucket.consume() is True
        assert bucket.consume() is True
        assert bucket.consume() is True
        # Fourth call should fail — no refill at near-zero rate
        assert bucket.consume() is False

    def test_consume_returns_false_when_empty(self):
        bucket = TokenBucket(rate_per_second=0.0, burst_size=1)
        bucket.consume()  # use the only token
        assert bucket.consume() is False

    def test_consume_refills_over_time(self):
        bucket = TokenBucket(rate_per_second=100.0, burst_size=5)
        # Drain all tokens
        for _ in range(5):
            bucket.consume()
        # Force-advance the clock by manipulating last_update
        bucket.last_update -= 0.1  # 0.1 s at 100/s = +10 tokens → capped to burst_size
        # Should be able to consume again
        assert bucket.consume() is True

    def test_consume_does_not_exceed_burst_size_on_refill(self):
        bucket = TokenBucket(rate_per_second=1000.0, burst_size=5)
        # Wind the clock far back to accumulate huge refill
        bucket.last_update -= 9999.0
        bucket.consume()
        # After a huge refill the bucket should be capped at burst_size; after
        # consuming 1 it should be burst_size - 1.
        assert bucket.tokens == pytest.approx(4.0, abs=0.1)

    def test_multiple_consumes_from_full_bucket(self):
        burst = 8
        bucket = TokenBucket(rate_per_second=0.0, burst_size=burst)
        successes = sum(1 for _ in range(burst + 2) if bucket.consume())
        assert successes == burst


class TestTokenBucketTokensAvailable:
    """TokenBucket.tokens_available() returns a sensible estimate."""

    def test_tokens_available_at_full(self):
        bucket = TokenBucket(rate_per_second=1.0, burst_size=10)
        available = bucket.tokens_available()
        assert 9.9 <= available <= 10.0

    def test_tokens_available_after_consumption(self):
        bucket = TokenBucket(rate_per_second=0.0, burst_size=10)
        for _ in range(4):
            bucket.consume()
        assert bucket.tokens_available() == pytest.approx(6.0, abs=0.01)

    def test_tokens_available_capped_at_burst(self):
        bucket = TokenBucket(rate_per_second=1000.0, burst_size=5)
        bucket.last_update -= 9999.0
        assert bucket.tokens_available() == pytest.approx(5.0, abs=0.01)

    def test_tokens_available_never_negative(self):
        bucket = TokenBucket(rate_per_second=0.0, burst_size=2)
        bucket.consume()
        bucket.consume()
        assert bucket.tokens_available() >= 0.0


class TestTokenBucketRemainingForWindow:
    """TokenBucket.remaining_for_window() formats correctly for HTTP headers."""

    def test_remaining_full_bucket(self):
        bucket = TokenBucket(rate_per_second=1.0, burst_size=10)
        remaining = bucket.remaining_for_window(requests_per_minute=60)
        assert remaining == 10

    def test_remaining_empty_bucket(self):
        bucket = TokenBucket(rate_per_second=0.0, burst_size=5)
        for _ in range(5):
            bucket.consume()
        remaining = bucket.remaining_for_window(requests_per_minute=60)
        assert remaining == 0

    def test_remaining_is_integer(self):
        bucket = TokenBucket(rate_per_second=1.0, burst_size=10)
        remaining = bucket.remaining_for_window(requests_per_minute=60)
        assert isinstance(remaining, int)


# ===========================================================================
# RateLimitConfig
# ===========================================================================


class TestRateLimitConfigDefaults:
    """RateLimitConfig has correct default values."""

    def test_default_requests_per_minute(self):
        config = RateLimitConfig()
        assert config.requests_per_minute == 60

    def test_default_burst_size(self):
        config = RateLimitConfig()
        assert config.burst_size == 10

    def test_default_enabled(self):
        config = RateLimitConfig()
        assert config.enabled is True

    def test_custom_values(self):
        config = RateLimitConfig(requests_per_minute=120, burst_size=20, enabled=False)
        assert config.requests_per_minute == 120
        assert config.burst_size == 20
        assert config.enabled is False


class TestRateLimitConfigFromEnv:
    """RateLimitConfig.from_env() reads environment variables correctly."""

    def test_from_env_defaults_when_no_vars(self):
        with patch.dict(os.environ, {}, clear=False):
            for var in ("FORGE_RATE_LIMIT_RPM", "FORGE_RATE_LIMIT_BURST", "FORGE_RATE_LIMIT_ENABLED"):
                os.environ.pop(var, None)
            config = RateLimitConfig.from_env()
        assert config.requests_per_minute == 60
        assert config.burst_size == 10
        assert config.enabled is True

    def test_from_env_custom_rpm(self):
        with patch.dict(os.environ, {"FORGE_RATE_LIMIT_RPM": "120"}):
            config = RateLimitConfig.from_env()
        assert config.requests_per_minute == 120

    def test_from_env_custom_burst(self):
        with patch.dict(os.environ, {"FORGE_RATE_LIMIT_BURST": "25"}):
            config = RateLimitConfig.from_env()
        assert config.burst_size == 25

    def test_from_env_disabled_via_false(self):
        with patch.dict(os.environ, {"FORGE_RATE_LIMIT_ENABLED": "false"}):
            config = RateLimitConfig.from_env()
        assert config.enabled is False

    def test_from_env_disabled_via_zero(self):
        with patch.dict(os.environ, {"FORGE_RATE_LIMIT_ENABLED": "0"}):
            config = RateLimitConfig.from_env()
        assert config.enabled is False

    def test_from_env_enabled_via_yes(self):
        with patch.dict(os.environ, {"FORGE_RATE_LIMIT_ENABLED": "yes"}):
            config = RateLimitConfig.from_env()
        assert config.enabled is True

    def test_from_env_enabled_via_one(self):
        with patch.dict(os.environ, {"FORGE_RATE_LIMIT_ENABLED": "1"}):
            config = RateLimitConfig.from_env()
        assert config.enabled is True

    def test_from_env_enabled_case_insensitive(self):
        with patch.dict(os.environ, {"FORGE_RATE_LIMIT_ENABLED": "TRUE"}):
            config = RateLimitConfig.from_env()
        assert config.enabled is True


# ===========================================================================
# RateLimiter — multi-client orchestration
# ===========================================================================


@pytest.fixture
def enabled_limiter():
    config = RateLimitConfig(requests_per_minute=60, burst_size=5, enabled=True)
    return RateLimiter(config)


@pytest.fixture
def disabled_limiter():
    config = RateLimitConfig(requests_per_minute=60, burst_size=5, enabled=False)
    return RateLimiter(config)


class TestRateLimiterIsAllowed:
    """RateLimiter.is_allowed() enforces per-IP token buckets."""

    def test_first_request_always_allowed(self, enabled_limiter):
        assert enabled_limiter.is_allowed("10.0.0.1") is True

    def test_burst_allows_multiple_consecutive(self, enabled_limiter):
        ip = "10.0.0.2"
        results = [enabled_limiter.is_allowed(ip) for _ in range(5)]
        assert all(results), "All burst requests should succeed"

    def test_beyond_burst_is_denied(self, enabled_limiter):
        ip = "10.0.0.3"
        # Exhaust all 5 burst tokens
        for _ in range(5):
            enabled_limiter.is_allowed(ip)
        # The 6th should fail
        assert enabled_limiter.is_allowed(ip) is False

    def test_disabled_always_returns_true(self, disabled_limiter):
        ip = "10.0.0.4"
        results = [disabled_limiter.is_allowed(ip) for _ in range(20)]
        assert all(results)

    def test_different_ips_independent_buckets(self, enabled_limiter):
        # Exhaust IP A
        for _ in range(5):
            enabled_limiter.is_allowed("192.168.1.1")
        assert enabled_limiter.is_allowed("192.168.1.1") is False
        # IP B should still have tokens
        assert enabled_limiter.is_allowed("192.168.1.2") is True

    def test_creates_bucket_on_first_request(self, enabled_limiter):
        ip = "172.16.0.1"
        assert ip not in enabled_limiter._buckets
        enabled_limiter.is_allowed(ip)
        assert ip in enabled_limiter._buckets

    def test_rate_calculated_correctly(self):
        # 120 rpm → 2/s rate; burst=2 → after 2 immediate requests, 3rd fails
        config = RateLimitConfig(requests_per_minute=120, burst_size=2, enabled=True)
        limiter = RateLimiter(config)
        ip = "1.2.3.4"
        assert limiter.is_allowed(ip) is True
        assert limiter.is_allowed(ip) is True
        assert limiter.is_allowed(ip) is False


class TestRateLimiterGetRemaining:
    """RateLimiter.get_remaining() returns correct token counts."""

    def test_remaining_for_unknown_ip_equals_burst_size(self, enabled_limiter):
        remaining = enabled_limiter.get_remaining("0.0.0.0")
        assert remaining == enabled_limiter.config.burst_size

    def test_remaining_decreases_after_requests(self, enabled_limiter):
        ip = "10.1.0.1"
        enabled_limiter.is_allowed(ip)
        enabled_limiter.is_allowed(ip)
        remaining = enabled_limiter.get_remaining(ip)
        assert remaining == 3

    def test_remaining_is_zero_when_exhausted(self, enabled_limiter):
        ip = "10.1.0.2"
        for _ in range(5):
            enabled_limiter.is_allowed(ip)
        assert enabled_limiter.get_remaining(ip) == 0

    def test_remaining_never_negative(self, enabled_limiter):
        ip = "10.1.0.3"
        for _ in range(10):
            enabled_limiter.is_allowed(ip)
        assert enabled_limiter.get_remaining(ip) >= 0


class TestRateLimiterGetRetryAfter:
    """RateLimiter.get_retry_after() returns meaningful wait times."""

    def test_retry_after_zero_for_unknown_ip(self, enabled_limiter):
        result = enabled_limiter.get_retry_after("1.1.1.1")
        assert result == 0

    def test_retry_after_positive_when_exhausted(self, enabled_limiter):
        ip = "10.2.0.1"
        for _ in range(5):
            enabled_limiter.is_allowed(ip)
        # After exhaustion the next token takes ~1s at 60rpm=1/s
        retry = enabled_limiter.get_retry_after(ip)
        assert retry >= 1

    def test_retry_after_zero_when_tokens_available(self, enabled_limiter):
        ip = "10.2.0.2"
        enabled_limiter.is_allowed(ip)  # consumes 1 of 5
        retry = enabled_limiter.get_retry_after(ip)
        assert retry == 0

    def test_retry_after_fallback_for_zero_rate(self):
        config = RateLimitConfig(requests_per_minute=0, burst_size=1, enabled=True)
        limiter = RateLimiter(config)
        ip = "10.3.0.1"
        limiter.is_allowed(ip)  # creates bucket
        # Force bucket to empty
        bucket = limiter._buckets[ip]
        bucket.tokens = 0.0
        result = limiter.get_retry_after(ip)
        assert result == 60  # fallback


class TestRateLimiterGetStats:
    """RateLimiter.get_stats() returns accurate statistics."""

    def test_stats_enabled(self, enabled_limiter):
        stats = enabled_limiter.get_stats()
        assert stats["enabled"] is True

    def test_stats_disabled(self, disabled_limiter):
        stats = disabled_limiter.get_stats()
        assert stats["enabled"] is False

    def test_stats_active_clients_starts_zero(self, enabled_limiter):
        stats = enabled_limiter.get_stats()
        assert stats["active_clients"] == 0

    def test_stats_active_clients_increments(self, enabled_limiter):
        enabled_limiter.is_allowed("a.b.c.d")
        enabled_limiter.is_allowed("a.b.c.e")
        stats = enabled_limiter.get_stats()
        assert stats["active_clients"] == 2

    def test_stats_contains_config_values(self, enabled_limiter):
        stats = enabled_limiter.get_stats()
        assert stats["requests_per_minute"] == 60
        assert stats["burst_size"] == 5


class TestRateLimiterCleanup:
    """RateLimiter._maybe_cleanup() removes stale buckets."""

    def test_cleanup_removes_full_buckets(self):
        config = RateLimitConfig(requests_per_minute=60, burst_size=5, enabled=True)
        limiter = RateLimiter(config)

        # Add a client
        ip = "stale.client.ip"
        limiter.is_allowed(ip)
        assert ip in limiter._buckets

        # Refill the bucket fully (simulate time passage)
        limiter._buckets[ip].tokens = float(limiter._buckets[ip].burst_size)

        # Force cleanup to run by resetting the interval timer
        limiter._last_cleanup -= limiter._cleanup_interval + 1
        limiter._maybe_cleanup()

        # The stale (full) bucket should have been removed
        assert ip not in limiter._buckets

    def test_cleanup_skips_when_interval_not_passed(self, enabled_limiter):
        ip = "active.client"
        enabled_limiter.is_allowed(ip)
        initial_count = len(enabled_limiter._buckets)

        # _last_cleanup is recent — cleanup should not run
        enabled_limiter._maybe_cleanup()
        assert len(enabled_limiter._buckets) == initial_count

    def test_cleanup_preserves_active_buckets(self):
        config = RateLimitConfig(requests_per_minute=60, burst_size=5, enabled=True)
        limiter = RateLimiter(config)

        active_ip = "active.ip"
        for _ in range(5):
            limiter.is_allowed(active_ip)  # drain tokens → not stale

        limiter._last_cleanup -= limiter._cleanup_interval + 1
        limiter._maybe_cleanup()

        # Active (partially empty) bucket should survive
        assert active_ip in limiter._buckets


# ===========================================================================
# get_rate_limiter singleton
# ===========================================================================


class TestGetRateLimiterSingleton:
    """get_rate_limiter() returns a stable singleton."""

    def test_returns_rate_limiter_instance(self):
        import forge_harness.webhook_server.infrastructure.rate_limiter as rl_module
        rl_module._rate_limiter = None  # reset singleton
        limiter = get_rate_limiter()
        assert isinstance(limiter, RateLimiter)

    def test_singleton_same_object(self):
        import forge_harness.webhook_server.infrastructure.rate_limiter as rl_module
        rl_module._rate_limiter = None
        limiter1 = get_rate_limiter()
        limiter2 = get_rate_limiter()
        assert limiter1 is limiter2

    def test_singleton_respects_env(self):
        import forge_harness.webhook_server.infrastructure.rate_limiter as rl_module
        rl_module._rate_limiter = None
        with patch.dict(os.environ, {"FORGE_RATE_LIMIT_RPM": "999"}):
            rl_module._rate_limiter = None
            limiter = get_rate_limiter()
        assert limiter.config.requests_per_minute == 999
        # Reset for other tests
        rl_module._rate_limiter = None


# ===========================================================================
# Concurrent access
# ===========================================================================


class TestRateLimiterConcurrency:
    """RateLimiter is thread-safe under concurrent load."""

    def test_concurrent_requests_do_not_exceed_burst(self):
        config = RateLimitConfig(requests_per_minute=600, burst_size=10, enabled=True)
        limiter = RateLimiter(config)
        ip = "concurrent.ip"
        results = []

        def make_request():
            results.append(limiter.is_allowed(ip))

        threads = [Thread(target=make_request) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        allowed = sum(1 for r in results if r)
        # Should not exceed burst_size
        assert allowed <= 10

    def test_concurrent_different_ips_all_allowed(self):
        config = RateLimitConfig(requests_per_minute=60, burst_size=5, enabled=True)
        limiter = RateLimiter(config)
        results = {}

        def make_request(ip):
            results[ip] = limiter.is_allowed(ip)

        threads = [Thread(target=make_request, args=(f"10.0.0.{i}",)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Every unique IP's first request should succeed
        assert all(results.values())

    def test_concurrent_stats_consistent(self):
        config = RateLimitConfig(requests_per_minute=60, burst_size=5, enabled=True)
        limiter = RateLimiter(config)

        def make_request(i):
            limiter.is_allowed(f"10.0.{i}.1")

        threads = [Thread(target=make_request, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = limiter.get_stats()
        assert stats["active_clients"] == 10


# ===========================================================================
# Edge cases
# ===========================================================================


class TestRateLimiterEdgeCases:
    """Edge cases for RateLimiter and TokenBucket."""

    def test_zero_rpm_config(self):
        # 0 rpm → rate_per_second = 0; bucket starts full (burst_size tokens)
        config = RateLimitConfig(requests_per_minute=0, burst_size=3, enabled=True)
        limiter = RateLimiter(config)
        ip = "0.rpm.test"
        # Should succeed burst_size times then fail
        results = [limiter.is_allowed(ip) for _ in range(5)]
        assert results[:3] == [True, True, True]
        assert results[3] is False

    def test_large_burst_size(self):
        config = RateLimitConfig(requests_per_minute=60, burst_size=1000, enabled=True)
        limiter = RateLimiter(config)
        ip = "burst.test"
        results = [limiter.is_allowed(ip) for _ in range(1000)]
        assert all(results)

    def test_empty_string_ip_is_handled(self, enabled_limiter):
        # Empty string should be treated as a valid key
        result = enabled_limiter.is_allowed("")
        assert isinstance(result, bool)

    def test_very_high_rpm_config(self):
        config = RateLimitConfig(requests_per_minute=100000, burst_size=5, enabled=True)
        limiter = RateLimiter(config)
        ip = "fast.ip"
        # Burst should still enforce the hard limit
        results = [limiter.is_allowed(ip) for _ in range(10)]
        allowed = sum(1 for r in results if r)
        assert allowed == 5

    def test_token_bucket_zero_rate_no_refill(self):
        bucket = TokenBucket(rate_per_second=0.0, burst_size=2)
        bucket.consume()
        bucket.consume()
        # Force elapsed time but zero rate → no refill
        bucket.last_update -= 100.0
        available = bucket.tokens_available()
        assert available == pytest.approx(0.0, abs=0.01)

    def test_rate_limiter_get_remaining_returns_int(self, enabled_limiter):
        result = enabled_limiter.get_remaining("any.ip")
        assert isinstance(result, int)


# ===========================================================================
# TokenBudgetTracker — extended / edge-case tests
# ===========================================================================


@pytest.fixture
def fresh_tracker():
    return TokenBudgetTracker(auto_reset=False)


class TestAgentBudgetEdgeCases:
    """Edge cases for AgentBudget dataclass."""

    def test_session_start_set_at_creation(self):
        before = datetime.now()
        budget = AgentBudget(agent_id="test", hourly_limit=100000)
        after = datetime.now()
        assert before <= budget.session_start <= after

    def test_should_auto_reset_exactly_at_one_hour(self):
        budget = AgentBudget(agent_id="test", hourly_limit=100000)
        budget.session_start = datetime.now() - timedelta(hours=1, seconds=1)
        assert budget.should_auto_reset() is True

    def test_should_not_auto_reset_just_before_one_hour(self):
        budget = AgentBudget(agent_id="test", hourly_limit=100000)
        budget.session_start = datetime.now() - timedelta(minutes=59)
        assert budget.should_auto_reset() is False

    def test_reset_updates_session_start(self):
        budget = AgentBudget(agent_id="test", hourly_limit=100000)
        budget.session_start = datetime(2000, 1, 1)
        before = datetime.now()
        budget.reset()
        after = datetime.now()
        assert before <= budget.session_start <= after

    def test_reset_clears_last_output(self):
        budget = AgentBudget(agent_id="test", hourly_limit=100000)
        budget.last_output = datetime.now()
        budget.reset()
        assert budget.last_output is None


class TestTokenBudgetTrackerInitialization:
    """TokenBudgetTracker initialises correctly."""

    def test_empty_budgets_on_init(self):
        tracker = TokenBudgetTracker()
        assert tracker.budgets == {}

    def test_custom_limits_stored(self):
        tracker = TokenBudgetTracker(custom_limits={"my_agent": 999})
        assert tracker.custom_limits == {"my_agent": 999}

    def test_auto_reset_default_true(self):
        tracker = TokenBudgetTracker()
        assert tracker.auto_reset is True

    def test_warning_threshold_constant(self, fresh_tracker):
        assert fresh_tracker.WARNING_THRESHOLD == 0.75

    def test_critical_threshold_constant(self, fresh_tracker):
        assert fresh_tracker.CRITICAL_THRESHOLD == 0.90

    def test_default_limits_for_all_known_agents(self, fresh_tracker):
        expected = {"kimi", "codex", "gemini", "claude", "gpt4"}
        assert expected.issubset(set(fresh_tracker.DEFAULT_LIMITS.keys()))


class TestRecordOutputStatusTransitions:
    """record_output transitions between OK / WARNING / CRITICAL correctly."""

    def test_ok_below_75_percent(self, fresh_tracker):
        # 74% of 250000 = 185000
        result = fresh_tracker.record_output("kimi", tokens_used=185000)
        assert result["status"] == "OK"
        assert result["should_handoff"] is False

    def test_warning_at_exactly_75_percent(self, fresh_tracker):
        result = fresh_tracker.record_output("kimi", tokens_used=187500)
        assert result["status"] == "WARNING"
        assert result["should_handoff"] is True

    def test_critical_at_exactly_90_percent(self, fresh_tracker):
        result = fresh_tracker.record_output("kimi", tokens_used=225000)
        assert result["status"] == "CRITICAL"
        assert result["should_handoff"] is True

    def test_critical_above_100_percent(self, fresh_tracker):
        # Over-budget
        result = fresh_tracker.record_output("kimi", tokens_used=300000)
        assert result["status"] == "CRITICAL"

    def test_ok_to_warning_transition(self, fresh_tracker):
        r1 = fresh_tracker.record_output("kimi", tokens_used=100000)  # 40%
        assert r1["status"] == "OK"
        r2 = fresh_tracker.record_output("kimi", tokens_used=100000)  # 80%
        assert r2["status"] == "WARNING"

    def test_warning_to_critical_transition(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=200000)  # 80%
        r = fresh_tracker.record_output("kimi", tokens_used=30000)  # ~92%
        assert r["status"] == "CRITICAL"

    def test_utilization_pct_format(self, fresh_tracker):
        result = fresh_tracker.record_output("kimi", tokens_used=125000)  # 50%
        assert result["utilization_pct"] == "50%"

    def test_tokens_remaining_correct(self, fresh_tracker):
        result = fresh_tracker.record_output("kimi", tokens_used=50000)
        assert result["tokens_remaining"] == 200000

    def test_hourly_limit_in_result(self, fresh_tracker):
        result = fresh_tracker.record_output("kimi", tokens_used=1000)
        assert result["hourly_limit"] == 250000

    def test_session_duration_present_and_numeric(self, fresh_tracker):
        result = fresh_tracker.record_output("kimi", tokens_used=1000)
        assert "session_duration_minutes" in result
        assert isinstance(result["session_duration_minutes"], float)

    def test_last_output_set_after_record(self, fresh_tracker):
        before = datetime.now()
        fresh_tracker.record_output("kimi", tokens_used=1000)
        after = datetime.now()
        budget = fresh_tracker.budgets["kimi"]
        assert budget.last_output is not None
        assert before <= budget.last_output <= after


class TestMultipleAgentTracking:
    """Tracker correctly isolates budgets per agent."""

    def test_agents_do_not_share_budgets(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=200000)  # 80%
        result = fresh_tracker.record_output("codex", tokens_used=10000)  # 2%
        assert result["status"] == "OK"

    def test_each_agent_uses_own_hourly_limit(self, fresh_tracker):
        r_kimi = fresh_tracker.record_output("kimi", tokens_used=250000)
        r_gemini = fresh_tracker.record_output("gemini", tokens_used=250000)
        assert r_kimi["utilization"] == pytest.approx(1.0)
        assert r_gemini["utilization"] == pytest.approx(0.25)

    def test_get_all_statuses_empty_when_no_agents(self, fresh_tracker):
        assert fresh_tracker.get_all_statuses() == {}

    def test_get_all_statuses_contains_all_tracked(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=1000)
        fresh_tracker.record_output("codex", tokens_used=2000)
        fresh_tracker.record_output("gemini", tokens_used=3000)
        statuses = fresh_tracker.get_all_statuses()
        assert set(statuses.keys()) == {"kimi", "codex", "gemini"}

    def test_custom_agent_uses_conservative_default(self, fresh_tracker):
        result = fresh_tracker.record_output("my_custom_agent", tokens_used=125000)
        # conservative default = 250000, so 125000 = 50%
        assert result["utilization"] == pytest.approx(0.5)


class TestEstimateRemainingOutputs:
    """estimate_remaining_outputs() applies safety margin correctly."""

    def test_estimate_fresh_agent(self, fresh_tracker):
        # 250000 / (40000 * 1.1) = 5.68 → 5
        estimate = fresh_tracker.estimate_remaining_outputs("kimi", avg_tokens=40000)
        assert estimate == 5

    def test_estimate_after_partial_use(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=100000)
        # remaining = 150000; 150000 / (40000 * 1.1) = 3.4 → 3
        estimate = fresh_tracker.estimate_remaining_outputs("kimi", avg_tokens=40000)
        assert estimate == 3

    def test_estimate_zero_when_exhausted(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=250000)
        assert fresh_tracker.estimate_remaining_outputs("kimi", avg_tokens=1000) == 0

    def test_estimate_zero_when_over_budget(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=300000)
        assert fresh_tracker.estimate_remaining_outputs("kimi", avg_tokens=1000) == 0

    def test_estimate_safety_margin_applied(self, fresh_tracker):
        # Use a nice round number to verify the 1.1x margin
        # 250000 / (100000 * 1.1) = 2.27 → 2
        estimate = fresh_tracker.estimate_remaining_outputs("kimi", avg_tokens=100000)
        assert estimate == 2

    def test_estimate_returns_int(self, fresh_tracker):
        estimate = fresh_tracker.estimate_remaining_outputs("kimi", avg_tokens=40000)
        assert isinstance(estimate, int)


class TestShouldHandoff:
    """should_handoff() returns correct boolean based on utilization."""

    def test_no_handoff_at_74_percent(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=185000)
        assert fresh_tracker.should_handoff("kimi") is False

    def test_handoff_at_75_percent(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=187500)
        assert fresh_tracker.should_handoff("kimi") is True

    def test_handoff_at_90_percent(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=225000)
        assert fresh_tracker.should_handoff("kimi") is True

    def test_no_handoff_for_fresh_agent(self, fresh_tracker):
        # Agent created lazily with 0 tokens used
        assert fresh_tracker.should_handoff("brand_new") is False


class TestGetStatus:
    """get_status() returns the full canonical status dict."""

    def test_status_keys_present(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=1000)
        status = fresh_tracker.get_status("kimi")
        required_keys = {
            "agent_id", "status", "utilization", "utilization_pct",
            "tokens_used", "tokens_remaining", "hourly_limit",
            "output_count", "recommendation", "session_start",
            "session_duration_minutes", "last_output",
        }
        assert required_keys.issubset(set(status.keys()))

    def test_last_output_none_before_first_record(self, fresh_tracker):
        status = fresh_tracker.get_status("kimi")
        assert status["last_output"] is None

    def test_last_output_set_after_record(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=1000)
        status = fresh_tracker.get_status("kimi")
        assert status["last_output"] is not None

    def test_status_warning_recommendation(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=187500)
        status = fresh_tracker.get_status("kimi")
        assert status["status"] == "WARNING"
        assert "PREPARE HANDOFF" in status["recommendation"]

    def test_status_critical_recommendation(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=225000)
        status = fresh_tracker.get_status("kimi")
        assert status["status"] == "CRITICAL"
        assert "HANDOFF NOW" in status["recommendation"]

    def test_status_ok_recommendation(self, fresh_tracker):
        status = fresh_tracker.get_status("kimi")
        assert status["status"] == "OK"
        assert "Continue" in status["recommendation"]


class TestResetBehavior:
    """reset_agent() and reset_all() work correctly."""

    def test_reset_agent_clears_tokens_and_count(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=200000)
        fresh_tracker.reset_agent("kimi")
        status = fresh_tracker.get_status("kimi")
        assert status["tokens_used"] == 0
        assert status["output_count"] == 0

    def test_reset_agent_unknown_does_not_raise(self, fresh_tracker):
        fresh_tracker.reset_agent("ghost")  # should not raise

    def test_reset_all_clears_all_agents(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=100000)
        fresh_tracker.record_output("codex", tokens_used=200000)
        fresh_tracker.reset_all()
        assert fresh_tracker.get_status("kimi")["tokens_used"] == 0
        assert fresh_tracker.get_status("codex")["tokens_used"] == 0

    def test_reset_all_with_no_agents_does_not_raise(self, fresh_tracker):
        fresh_tracker.reset_all()

    def test_budget_continues_tracking_after_reset(self, fresh_tracker):
        fresh_tracker.record_output("kimi", tokens_used=100000)
        fresh_tracker.reset_agent("kimi")
        result = fresh_tracker.record_output("kimi", tokens_used=50000)
        assert result["tokens_used"] == 50000
        assert result["output_count"] == 1


class TestAutoReset:
    """auto_reset=True triggers hourly window resets."""

    def test_auto_reset_fires_when_window_expired(self):
        tracker = TokenBudgetTracker(auto_reset=True)
        tracker.record_output("kimi", tokens_used=200000)
        # Simulate >1 hour passing
        tracker.budgets["kimi"].session_start = datetime.now() - timedelta(hours=2)
        result = tracker.record_output("kimi", tokens_used=10000)
        # Post-reset so only the new 10000 should be counted
        assert result["tokens_used"] == 10000

    def test_auto_reset_does_not_fire_within_window(self):
        tracker = TokenBudgetTracker(auto_reset=True)
        tracker.record_output("kimi", tokens_used=100000)
        result = tracker.record_output("kimi", tokens_used=50000)
        assert result["tokens_used"] == 150000

    def test_no_auto_reset_when_disabled(self):
        tracker = TokenBudgetTracker(auto_reset=False)
        tracker.record_output("kimi", tokens_used=200000)
        tracker.budgets["kimi"].session_start = datetime.now() - timedelta(hours=2)
        result = tracker.record_output("kimi", tokens_used=10000)
        # Without auto_reset the 10000 accumulates on top of 200000
        assert result["tokens_used"] == 210000


class TestCreateTrackerFromEnv:
    """create_tracker_from_env() reads TOKEN_LIMIT_* variables."""

    def test_custom_limit_applied(self):
        with patch.dict(os.environ, {"TOKEN_LIMIT_KIMI": "300000"}):
            tracker = create_tracker_from_env()
        budget = tracker._get_or_create_budget("kimi")
        assert budget.hourly_limit == 300000

    def test_all_known_agents_configurable(self):
        env = {
            "TOKEN_LIMIT_KIMI": "111000",
            "TOKEN_LIMIT_CODEX": "222000",
            "TOKEN_LIMIT_GEMINI": "333000",
            "TOKEN_LIMIT_CLAUDE": "444000",
            "TOKEN_LIMIT_GPT4": "555000",
        }
        with patch.dict(os.environ, env):
            tracker = create_tracker_from_env()
        assert tracker._get_or_create_budget("kimi").hourly_limit == 111000
        assert tracker._get_or_create_budget("codex").hourly_limit == 222000
        assert tracker._get_or_create_budget("gemini").hourly_limit == 333000
        assert tracker._get_or_create_budget("claude").hourly_limit == 444000
        assert tracker._get_or_create_budget("gpt4").hourly_limit == 555000

    def test_invalid_env_var_falls_back_to_default(self):
        with patch.dict(os.environ, {"TOKEN_LIMIT_KIMI": "not_a_number"}):
            tracker = create_tracker_from_env()
        budget = tracker._get_or_create_budget("kimi")
        assert budget.hourly_limit == 250000

    def test_no_env_vars_uses_defaults(self):
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("TOKEN_LIMIT_")}
        with patch.dict(os.environ, env_clean, clear=True):
            tracker = create_tracker_from_env()
        budget = tracker._get_or_create_budget("kimi")
        assert budget.hourly_limit == 250000

    def test_returns_token_budget_tracker_instance(self):
        tracker = create_tracker_from_env()
        assert isinstance(tracker, TokenBudgetTracker)
