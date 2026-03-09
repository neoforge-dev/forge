"""
Rate Limiting Infrastructure
=============================

Token bucket rate limiting implementation for webhook server.
Extracted from webhook_server.py for better modularity.

Implements:
- RateLimitConfig: Configuration from environment
- TokenBucket: Token bucket algorithm for single client
- RateLimiter: Multi-client rate limiter with auto-cleanup
"""

import os
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any

from ...logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class RateLimitConfig:
    """Rate limiting configuration.

    Uses token bucket algorithm for flexible rate limiting.

    Attributes:
        requests_per_minute: Maximum requests per minute per IP (0 = disabled)
        burst_size: Maximum burst size (tokens available at once)
        enabled: Whether rate limiting is active
    """

    requests_per_minute: int = 60
    burst_size: int = 10
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "RateLimitConfig":
        """Create RateLimitConfig from environment variables."""
        return cls(
            requests_per_minute=int(os.environ.get("FORGE_RATE_LIMIT_RPM", "60")),
            burst_size=int(os.environ.get("FORGE_RATE_LIMIT_BURST", "10")),
            enabled=os.environ.get("FORGE_RATE_LIMIT_ENABLED", "true").lower()
            in ("true", "1", "yes"),
        )


class TokenBucket:
    """Token bucket rate limiter for a single client.

    Implements the token bucket algorithm:
    - Tokens are added at a fixed rate (requests_per_minute / 60)
    - Burst allows temporary spikes up to burst_size
    - Each request consumes one token
    - Requests are denied when bucket is empty
    """

    def __init__(self, rate_per_second: float, burst_size: int):
        """Initialize token bucket.

        Args:
            rate_per_second: Token refill rate per second
            burst_size: Maximum tokens in bucket
        """
        self.rate = rate_per_second
        self.burst_size = burst_size
        self.tokens = float(burst_size)
        self.last_update = time.monotonic()
        self._lock = Lock()

    def consume(self) -> bool:
        """Try to consume one token.

        Returns:
            True if token consumed, False if rate limited
        """
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.last_update = now

            # Add tokens based on elapsed time
            self.tokens = min(self.burst_size, self.tokens + elapsed * self.rate)

            # Try to consume
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False

    def tokens_available(self) -> float:
        """Get current available tokens (approximate)."""
        now = time.monotonic()
        elapsed = now - self.last_update
        return min(self.burst_size, self.tokens + elapsed * self.rate)

    def remaining_for_window(self, requests_per_minute: int) -> int:
        """Estimate remaining requests in the current window.

        Returns an integer suitable for X-RateLimit-Remaining header.
        """
        available = self.tokens_available()
        return max(0, int(available))


class RateLimiter:
    """Rate limiter managing multiple clients.

    Thread-safe rate limiter using token bucket algorithm per client IP.
    Automatically cleans up stale buckets to prevent memory leaks.
    """

    def __init__(self, config: RateLimitConfig):
        """Initialize rate limiter.

        Args:
            config: Rate limiting configuration
        """
        self.config = config
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = Lock()
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 300  # Clean up every 5 minutes

    def is_allowed(self, client_ip: str) -> bool:
        """Check if request from client is allowed.

        Args:
            client_ip: Client IP address

        Returns:
            True if request allowed, False if rate limited
        """
        if not self.config.enabled:
            return True

        # Periodic cleanup of stale buckets
        self._maybe_cleanup()

        with self._lock:
            if client_ip not in self._buckets:
                rate_per_second = self.config.requests_per_minute / 60.0
                self._buckets[client_ip] = TokenBucket(
                    rate_per_second=rate_per_second,
                    burst_size=self.config.burst_size,
                )

            return self._buckets[client_ip].consume()

    def get_remaining(self, client_ip: str) -> int:
        """Get remaining requests for a client.

        Args:
            client_ip: Client IP address

        Returns:
            Number of remaining requests (tokens) available
        """
        with self._lock:
            bucket = self._buckets.get(client_ip)
            if bucket is None:
                return self.config.burst_size
            return max(0, int(bucket.tokens_available()))

    def get_retry_after(self, client_ip: str) -> int:
        """Get recommended retry-after time in seconds.

        Args:
            client_ip: Client IP address

        Returns:
            Seconds until a token will be available
        """
        with self._lock:
            bucket = self._buckets.get(client_ip)
            if bucket is None:
                return 0

            tokens_needed = 1.0 - bucket.tokens_available()
            if tokens_needed <= 0:
                return 0

            rate_per_second = self.config.requests_per_minute / 60.0
            if rate_per_second <= 0:
                return 60  # Default fallback

            return max(1, int(tokens_needed / rate_per_second))

    def _maybe_cleanup(self) -> None:
        """Clean up stale buckets if enough time has passed."""
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return

        with self._lock:
            self._last_cleanup = now
            # Remove buckets that haven't been used in 10 minutes
            # and have full tokens (inactive clients)
            stale_ips = []
            for ip, bucket in self._buckets.items():
                if bucket.tokens_available() >= bucket.burst_size:
                    stale_ips.append(ip)

            for ip in stale_ips:
                del self._buckets[ip]

            if stale_ips:
                logger.debug(f"Cleaned up {len(stale_ips)} stale rate limit buckets")

    def get_stats(self) -> dict[str, Any]:
        """Get rate limiter statistics.

        Returns:
            Dict with stats about current state
        """
        with self._lock:
            return {
                "enabled": self.config.enabled,
                "requests_per_minute": self.config.requests_per_minute,
                "burst_size": self.config.burst_size,
                "active_clients": len(self._buckets),
            }


# Global rate limiter instance
_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Get or create global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(RateLimitConfig.from_env())
    return _rate_limiter
