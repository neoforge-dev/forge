"""Extended tests for rate_limiter.py infrastructure module.

Targets the 6 uncovered lines left after test_rate_limiter.py:
  - 106-107: TokenBucket.remaining_for_window
  - 185:     RateLimiter.get_retry_after – rate_per_second == 0 branch
  - 206, 209, 212: RateLimiter._maybe_cleanup – stale-bucket removal + debug log
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest

from forge_harness.webhook_server.infrastructure.rate_limiter import (
    RateLimitConfig,
    RateLimiter,
    TokenBucket,
    get_rate_limiter,
)

# ---------------------------------------------------------------------------
# TokenBucket.remaining_for_window  (lines 101-107)
# ---------------------------------------------------------------------------


class TestTokenBucketRemainingForWindow:
    """Tests for TokenBucket.remaining_for_window."""

    def test_full_bucket_returns_burst_as_int(self):
        """A full bucket should return burst_size as an integer."""
        bucket = TokenBucket(rate_per_second=1.0, burst_size=10)
        result = bucket.remaining_for_window(requests_per_minute=60)
        assert isinstance(result, int)
        assert result == 10

    def test_partial_bucket_returns_floored_int(self):
        """A partially consumed bucket returns floor(tokens_available)."""
        bucket = TokenBucket(rate_per_second=1.0, burst_size=10)
        # Consume 3 tokens
        bucket.consume()
        bucket.consume()
        bucket.consume()
        result = bucket.remaining_for_window(requests_per_minute=60)
        assert isinstance(result, int)
        # 7 tokens remain (approximately – no time passes so no refill)
        assert 6 <= result <= 7

    def test_empty_bucket_returns_zero(self):
        """An empty bucket (0 tokens) returns 0."""
        bucket = TokenBucket(rate_per_second=1.0, burst_size=3)
        # Drain the bucket
        bucket.consume()
        bucket.consume()
        bucket.consume()
        # Force tokens to exactly 0.0 by setting directly
        bucket.tokens = 0.0
        result = bucket.remaining_for_window(requests_per_minute=60)
        assert result == 0

    def test_negative_tokens_clamped_to_zero(self):
        """Negative token counts (edge case) are clamped to 0 via max(0, ...)."""
        bucket = TokenBucket(rate_per_second=0.0, burst_size=5)
        bucket.tokens = -1.0  # force negative
        result = bucket.remaining_for_window(requests_per_minute=60)
        assert result == 0

    def test_requests_per_minute_argument_not_used_in_calculation(self):
        """requests_per_minute param is accepted but result depends only on tokens."""
        bucket = TokenBucket(rate_per_second=1.0, burst_size=5)
        # Same bucket, different rpm values – result should be the same
        r1 = bucket.remaining_for_window(requests_per_minute=30)
        r2 = bucket.remaining_for_window(requests_per_minute=120)
        assert r1 == r2

    def test_return_type_is_int(self):
        """Return type must be int (not float)."""
        bucket = TokenBucket(rate_per_second=2.0, burst_size=7)
        result = bucket.remaining_for_window(requests_per_minute=60)
        assert type(result) is int


# ---------------------------------------------------------------------------
# RateLimiter.get_retry_after with zero rate  (line 185)
# ---------------------------------------------------------------------------


class TestRateLimiterGetRetryAfterZeroRate:
    """Tests for the zero-rate fallback path in get_retry_after (line 185 and 189)."""

    def test_get_retry_after_returns_zero_when_tokens_available(self):
        """When tokens are available (tokens_needed <= 0), returns 0 (line 185)."""
        config = RateLimitConfig(requests_per_minute=60, burst_size=5, enabled=True)
        limiter = RateLimiter(config)
        # Create a bucket with full tokens
        limiter.is_allowed("rich-ip")  # creates bucket with 4 tokens remaining
        # Manually give it back full tokens
        limiter._buckets["rich-ip"].tokens = 5.0
        # tokens_available >= 1.0 -> tokens_needed <= 0 -> return 0
        retry = limiter.get_retry_after("rich-ip")
        assert retry == 0

    def test_zero_rpm_returns_60_seconds_fallback(self):
        """When requests_per_minute==0, retry_after falls back to 60 (line 185)."""
        config = RateLimitConfig(requests_per_minute=0, burst_size=1, enabled=True)
        limiter = RateLimiter(config)

        # Create a bucket for the IP by calling is_allowed
        limiter.is_allowed("10.0.0.1")  # consumes the 1 burst token
        limiter.is_allowed("10.0.0.1")  # now exhausted

        retry = limiter.get_retry_after("10.0.0.1")
        assert retry == 60

    def test_zero_rpm_bucket_with_no_tokens_available(self):
        """Force tokens below 1 with zero rate – must still return 60 fallback."""
        config = RateLimitConfig(requests_per_minute=0, burst_size=2, enabled=True)
        limiter = RateLimiter(config)

        # Manually insert a bucket with zero tokens
        bucket = TokenBucket(rate_per_second=0.0, burst_size=2)
        bucket.tokens = 0.0
        limiter._buckets["10.0.0.2"] = bucket

        retry = limiter.get_retry_after("10.0.0.2")
        assert retry == 60


# ---------------------------------------------------------------------------
# RateLimiter._maybe_cleanup – stale bucket removal (lines 206, 209, 212)
# ---------------------------------------------------------------------------


class TestRateLimiterCleanup:
    """Tests for stale-bucket cleanup logic in _maybe_cleanup."""

    def _make_limiter_with_zero_cleanup_interval(self):
        """Return a RateLimiter whose cleanup interval is set to 0 so it triggers immediately."""
        config = RateLimitConfig(requests_per_minute=60, burst_size=5, enabled=True)
        limiter = RateLimiter(config)
        limiter._cleanup_interval = 0  # trigger on every call
        limiter._last_cleanup = 0.0  # mark as overdue immediately
        return limiter

    def test_cleanup_removes_full_bucket(self):
        """A bucket that has tokens_available >= burst_size (i.e. full) is removed."""
        limiter = self._make_limiter_with_zero_cleanup_interval()

        # Add a full bucket manually
        full_bucket = TokenBucket(rate_per_second=1.0, burst_size=5)
        full_bucket.tokens = 5.0
        limiter._buckets["192.168.0.1"] = full_bucket

        # Trigger cleanup by calling is_allowed for a *new* IP
        limiter._last_cleanup = 0.0
        limiter.is_allowed("new-ip")

        # The full-token IP should have been cleaned up
        assert "192.168.0.1" not in limiter._buckets

    def test_cleanup_does_not_remove_active_bucket(self):
        """A bucket with tokens_available < burst_size (active client) is NOT removed."""
        limiter = self._make_limiter_with_zero_cleanup_interval()

        # Add a partially consumed bucket
        active_bucket = TokenBucket(rate_per_second=1.0, burst_size=5)
        active_bucket.tokens = 2.0  # below burst_size – treated as active
        limiter._buckets["active-ip"] = active_bucket

        limiter._last_cleanup = 0.0
        limiter.is_allowed("another-ip")

        assert "active-ip" in limiter._buckets

    def test_cleanup_logs_debug_when_buckets_removed(self):
        """Debug log is emitted when stale buckets are cleaned (line 212)."""
        limiter = self._make_limiter_with_zero_cleanup_interval()

        full_bucket = TokenBucket(rate_per_second=1.0, burst_size=5)
        full_bucket.tokens = 5.0
        limiter._buckets["stale-1"] = full_bucket
        full_bucket2 = TokenBucket(rate_per_second=1.0, burst_size=5)
        full_bucket2.tokens = 5.0
        limiter._buckets["stale-2"] = full_bucket2

        limiter._last_cleanup = 0.0

        with patch(
            "forge_harness.webhook_server.infrastructure.rate_limiter.logger"
        ) as mock_logger:
            limiter.is_allowed("trigger-ip")

        # Debug should have been called mentioning stale buckets
        mock_logger.debug.assert_called_once()
        debug_msg = mock_logger.debug.call_args[0][0]
        assert "stale" in debug_msg.lower() or "Cleaned" in debug_msg

    def test_cleanup_no_debug_log_when_nothing_removed(self):
        """Debug log is NOT emitted when no stale buckets exist (line 212 not reached)."""
        config = RateLimitConfig(requests_per_minute=60, burst_size=5, enabled=True)
        limiter = RateLimiter(config)
        limiter._cleanup_interval = 0
        limiter._last_cleanup = 0.0

        # Add a partially consumed bucket (not stale)
        active_bucket = TokenBucket(rate_per_second=1.0, burst_size=5)
        active_bucket.tokens = 1.0
        limiter._buckets["active"] = active_bucket

        with patch(
            "forge_harness.webhook_server.infrastructure.rate_limiter.logger"
        ) as mock_logger:
            limiter.is_allowed("trigger-ip")

        mock_logger.debug.assert_not_called()

    def test_cleanup_interval_not_elapsed_skips(self):
        """If _last_cleanup was recent, cleanup is skipped entirely."""
        config = RateLimitConfig(requests_per_minute=60, burst_size=5, enabled=True)
        limiter = RateLimiter(config)
        limiter._cleanup_interval = 9999  # very long interval
        limiter._last_cleanup = time.monotonic()  # just ran

        # Fill bucket with a stale (full) entry
        full_bucket = TokenBucket(rate_per_second=1.0, burst_size=5)
        full_bucket.tokens = 5.0
        limiter._buckets["would-be-cleaned"] = full_bucket

        with patch(
            "forge_harness.webhook_server.infrastructure.rate_limiter.logger"
        ) as mock_logger:
            limiter.is_allowed("some-ip")

        # Stale bucket should NOT have been removed
        assert "would-be-cleaned" in limiter._buckets
        mock_logger.debug.assert_not_called()

    def test_cleanup_removes_multiple_stale_buckets(self):
        """All stale buckets are removed in a single cleanup pass."""
        limiter = self._make_limiter_with_zero_cleanup_interval()

        for i in range(5):
            b = TokenBucket(rate_per_second=1.0, burst_size=5)
            b.tokens = 5.0  # full = stale
            limiter._buckets[f"stale-{i}"] = b

        limiter._last_cleanup = 0.0
        limiter.is_allowed("cleanup-trigger")

        stale_remaining = [k for k in limiter._buckets if k.startswith("stale-")]
        assert len(stale_remaining) == 0

    def test_stats_reflect_post_cleanup_count(self):
        """get_stats active_clients count reflects state after cleanup."""
        limiter = self._make_limiter_with_zero_cleanup_interval()

        full_bucket = TokenBucket(rate_per_second=1.0, burst_size=5)
        full_bucket.tokens = 5.0
        limiter._buckets["stale"] = full_bucket

        active_bucket = TokenBucket(rate_per_second=1.0, burst_size=5)
        active_bucket.tokens = 1.0
        limiter._buckets["active"] = active_bucket

        limiter._last_cleanup = 0.0
        limiter.is_allowed("trigger")

        stats = limiter.get_stats()
        # "stale" removed, "active" kept, "trigger" added
        assert stats["active_clients"] == 2  # "active" + "trigger"


# ---------------------------------------------------------------------------
# Integration: combined cleanup + rate limit enforcement
# ---------------------------------------------------------------------------


class TestCleanupAndRateLimitIntegration:
    """Integration tests ensuring cleanup doesn't break rate limiting."""

    def test_cleanup_does_not_affect_active_rate_limiting(self):
        """Active clients should not be rate-limited incorrectly after cleanup."""
        config = RateLimitConfig(requests_per_minute=60, burst_size=3, enabled=True)
        limiter = RateLimiter(config)
        limiter._cleanup_interval = 0
        limiter._last_cleanup = 0.0

        # Consume 2 of 3 tokens for the active client
        limiter.is_allowed("active-client")
        limiter.is_allowed("active-client")
        # 1 token left – should still be allowed
        limiter._last_cleanup = 0.0
        assert limiter.is_allowed("active-client") is True

    def test_disabled_limiter_bypasses_cleanup(self):
        """A disabled limiter returns True without creating any buckets."""
        config = RateLimitConfig(requests_per_minute=60, burst_size=5, enabled=False)
        limiter = RateLimiter(config)
        limiter._cleanup_interval = 0
        limiter._last_cleanup = 0.0

        for _ in range(100):
            assert limiter.is_allowed("unlimited-client") is True

        # Disabled limiter should not create buckets
        assert "unlimited-client" not in limiter._buckets

    def test_get_remaining_for_client_without_bucket(self):
        """get_remaining for an unknown client returns burst_size."""
        config = RateLimitConfig(requests_per_minute=60, burst_size=7, enabled=True)
        limiter = RateLimiter(config)
        assert limiter.get_remaining("unknown") == 7

    def test_full_rate_limit_cycle(self):
        """Full cycle: allow burst, hit limit, wait (simulated), allow again."""
        config = RateLimitConfig(requests_per_minute=600, burst_size=3, enabled=True)
        limiter = RateLimiter(config)
        ip = "cycle-client"

        # Consume burst
        assert limiter.is_allowed(ip) is True
        assert limiter.is_allowed(ip) is True
        assert limiter.is_allowed(ip) is True
        # Exhausted
        assert limiter.is_allowed(ip) is False

        # Manually refill tokens to simulate time passing
        limiter._buckets[ip].tokens = 3.0
        assert limiter.is_allowed(ip) is True
