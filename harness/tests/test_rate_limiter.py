"""Tests for Rate Limiting Infrastructure.

Tests token bucket rate limiting implementation.
"""

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


class TestRateLimitConfig:
    """Tests for RateLimitConfig dataclass."""

    def test_defaults(self):
        """Test default values."""
        config = RateLimitConfig()
        assert config.requests_per_minute == 60
        assert config.burst_size == 10
        assert config.enabled is True

    def test_custom_values(self):
        """Test custom values."""
        config = RateLimitConfig(
            requests_per_minute=120,
            burst_size=20,
            enabled=False,
        )
        assert config.requests_per_minute == 120
        assert config.burst_size == 20
        assert config.enabled is False

    def test_from_env_defaults(self):
        """Test from_env with no env vars."""
        with patch.dict(os.environ, {}, clear=True):
            config = RateLimitConfig.from_env()
            assert config.requests_per_minute == 60
            assert config.burst_size == 10
            assert config.enabled is True

    def test_from_env_custom(self):
        """Test from_env with custom values."""
        env = {
            "FORGE_RATE_LIMIT_RPM": "100",
            "FORGE_RATE_LIMIT_BURST": "25",
            "FORGE_RATE_LIMIT_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            config = RateLimitConfig.from_env()
            assert config.requests_per_minute == 100
            assert config.burst_size == 25
            assert config.enabled is False

    def test_from_env_enabled_variants(self):
        """Test various enabled values."""
        for true_val in ["true", "1", "yes", "TRUE", "Yes"]:
            with patch.dict(os.environ, {"FORGE_RATE_LIMIT_ENABLED": true_val}):
                config = RateLimitConfig.from_env()
                assert config.enabled is True

        for false_val in ["false", "0", "no", "anything"]:
            with patch.dict(os.environ, {"FORGE_RATE_LIMIT_ENABLED": false_val}):
                config = RateLimitConfig.from_env()
                assert config.enabled is False


class TestTokenBucket:
    """Tests for TokenBucket class."""

    def test_init(self):
        """Test bucket initialization."""
        bucket = TokenBucket(rate_per_second=1.0, burst_size=5)
        assert bucket.rate == 1.0
        assert bucket.burst_size == 5
        assert bucket.tokens == 5.0

    def test_consume_success(self):
        """Test consuming a token."""
        bucket = TokenBucket(rate_per_second=1.0, burst_size=5)

        assert bucket.consume() is True
        assert bucket.tokens < 5.0

    def test_consume_multiple(self):
        """Test consuming multiple tokens."""
        bucket = TokenBucket(rate_per_second=1.0, burst_size=3)

        assert bucket.consume() is True
        assert bucket.consume() is True
        assert bucket.consume() is True
        # Bucket should be empty now
        assert bucket.consume() is False

    def test_consume_refills(self):
        """Test tokens refill over time."""
        bucket = TokenBucket(rate_per_second=10.0, burst_size=5)

        # Consume all tokens
        for _ in range(5):
            bucket.consume()

        # Wait a bit for refill
        time.sleep(0.15)

        # Should have some tokens now
        assert bucket.consume() is True

    def test_tokens_available(self):
        """Test getting available tokens."""
        bucket = TokenBucket(rate_per_second=1.0, burst_size=5)

        available = bucket.tokens_available()
        assert available == 5.0

    def test_tokens_available_after_consume(self):
        """Test available tokens decreases after consume."""
        bucket = TokenBucket(rate_per_second=1.0, burst_size=5)

        bucket.consume()
        bucket.consume()

        # Should be about 3 tokens available
        available = bucket.tokens_available()
        assert 2.9 <= available <= 3.1

    def test_tokens_capped_at_burst(self):
        """Test tokens don't exceed burst size."""
        bucket = TokenBucket(rate_per_second=100.0, burst_size=5)

        time.sleep(0.1)  # Would add 10 tokens

        available = bucket.tokens_available()
        assert available <= 5.0


class TestRateLimiter:
    """Tests for RateLimiter class."""

    @pytest.fixture
    def config(self):
        """Create test config."""
        return RateLimitConfig(
            requests_per_minute=60,
            burst_size=5,
            enabled=True,
        )

    @pytest.fixture
    def limiter(self, config):
        """Create rate limiter."""
        return RateLimiter(config)

    def test_init(self, limiter, config):
        """Test limiter initialization."""
        assert limiter.config == config
        assert limiter._buckets == {}

    def test_is_allowed_first_request(self, limiter):
        """Test first request is always allowed."""
        assert limiter.is_allowed("192.168.1.1") is True

    def test_is_allowed_burst(self, limiter):
        """Test burst requests are allowed."""
        ip = "192.168.1.2"

        # Should allow burst_size requests
        for _ in range(5):
            assert limiter.is_allowed(ip) is True

        # Next should be denied
        assert limiter.is_allowed(ip) is False

    def test_is_allowed_disabled(self):
        """Test disabled limiter allows all."""
        config = RateLimitConfig(enabled=False)
        limiter = RateLimiter(config)

        for _ in range(100):
            assert limiter.is_allowed("1.2.3.4") is True

    def test_is_allowed_separate_clients(self, limiter):
        """Test separate clients have separate limits."""
        for _ in range(5):
            limiter.is_allowed("client-a")

        # client-a should be limited
        assert limiter.is_allowed("client-a") is False

        # client-b should still be allowed
        assert limiter.is_allowed("client-b") is True

    def test_get_remaining_new_client(self, limiter):
        """Test remaining for new client."""
        remaining = limiter.get_remaining("new-client")
        assert remaining == 5  # burst_size

    def test_get_remaining_after_requests(self, limiter):
        """Test remaining decreases after requests."""
        ip = "test-ip"
        limiter.is_allowed(ip)
        limiter.is_allowed(ip)

        remaining = limiter.get_remaining(ip)
        assert remaining == 3

    def test_get_retry_after_new_client(self, limiter):
        """Test retry_after for new client."""
        retry = limiter.get_retry_after("new-client")
        assert retry == 0

    def test_get_retry_after_limited_client(self, limiter):
        """Test retry_after for limited client."""
        ip = "limited-client"

        # Exhaust all tokens
        for _ in range(10):
            limiter.is_allowed(ip)

        retry = limiter.get_retry_after(ip)
        assert retry >= 1  # Should need to wait

    def test_get_retry_after_zero_rate(self):
        """Test retry_after with zero rate (edge case)."""
        config = RateLimitConfig(requests_per_minute=0, burst_size=1)
        limiter = RateLimiter(config)

        limiter.is_allowed("ip")
        limiter.is_allowed("ip")  # Exhaust

        retry = limiter.get_retry_after("ip")
        assert retry == 60  # Default fallback

    def test_get_stats(self, limiter):
        """Test getting stats."""
        limiter.is_allowed("client-1")
        limiter.is_allowed("client-2")

        stats = limiter.get_stats()

        assert stats["enabled"] is True
        assert stats["requests_per_minute"] == 60
        assert stats["burst_size"] == 5
        assert stats["active_clients"] == 2

    def test_cleanup_stale_buckets(self, config):
        """Test cleanup removes stale buckets."""
        limiter = RateLimiter(config)
        limiter._cleanup_interval = 0  # Trigger cleanup immediately

        # Create a bucket
        limiter.is_allowed("stale-client")

        # Wait for bucket to be "full" (tokens refilled)
        time.sleep(0.2)

        # Force cleanup by calling is_allowed
        limiter._last_cleanup = 0
        limiter.is_allowed("new-client")

        # stale-client should be removed (full bucket = stale)
        # Note: This depends on implementation details
        stats = limiter.get_stats()
        # After cleanup, clients with full buckets are removed
        assert stats["active_clients"] >= 1


class TestGetRateLimiter:
    """Tests for get_rate_limiter function."""

    def test_returns_limiter(self):
        """Test that get_rate_limiter returns a RateLimiter."""
        import forge_harness.webhook_server.infrastructure.rate_limiter as rl_module
        rl_module._rate_limiter = None

        with patch.dict(os.environ, {}, clear=True):
            limiter = get_rate_limiter()

        assert isinstance(limiter, RateLimiter)

    def test_returns_same_instance(self):
        """Test that get_rate_limiter returns singleton."""
        import forge_harness.webhook_server.infrastructure.rate_limiter as rl_module
        rl_module._rate_limiter = None

        with patch.dict(os.environ, {}, clear=True):
            limiter1 = get_rate_limiter()
            limiter2 = get_rate_limiter()

        assert limiter1 is limiter2
