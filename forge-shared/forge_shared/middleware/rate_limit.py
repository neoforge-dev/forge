"""
Rate limiting middleware using Redis with sliding window algorithm.

Provides token bucket rate limiting for API endpoints with configurable
limits and sliding window counters. Supports:
- Per-IP rate limits
- Per-API-key rate limits
- Endpoint-specific limits with glob patterns
- Sliding window algorithm
- Redis-backed storage with in-memory fallback

Example:
    ```python
    from fastapi import FastAPI
    from forge_shared.middleware import RateLimitMiddleware, RateLimitConfig

    # Basic usage
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        redis_url="redis://localhost:6379",
        requests_per_minute=60
    )

    # With endpoint-specific limits
    config = RateLimitConfig(
        default_limit=100,  # requests per hour
        endpoint_limits={
            "/api/auth/login": "5/minute",
            "/api/auth/signup": "10/hour",
            "/api/admin/*": "20/minute",
        }
    )
    app.add_middleware(RateLimitMiddleware, config=config, redis_url="redis://localhost:6379")
    ```
"""

import logging
import time
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any

from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Type alias for Redis client (lazy loaded)
RedisClient = Any


def _get_redis_module():
    """Lazy import redis module to avoid import errors when not using Redis."""
    try:
        import redis as redis_module

        return redis_module
    except ImportError:
        logger.warning("Redis package not installed. Rate limiting will use in-memory fallback.")
        return None


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting.

    Attributes:
        default_limit: Default requests per window (default: 100/hour)
        default_window_seconds: Default window size in seconds (default: 3600)
        endpoint_limits: Dict mapping path patterns to rate limit strings
        exempt_paths: Paths exempt from rate limiting
        trusted_proxies: Trusted proxy networks (CIDR notation)
        enable_api_key_limits: Enable per-API-key rate limiting via X-API-Key header
        key_prefix: Redis key prefix for rate limit counters
    """

    default_limit: int = 100
    default_window_seconds: int = 3600  # 1 hour
    endpoint_limits: dict[str, str] = field(default_factory=dict)
    exempt_paths: list = field(
        default_factory=lambda: ["/health", "/docs", "/openapi.json", "/metrics"]
    )
    trusted_proxies: list = field(default_factory=list)
    enable_api_key_limits: bool = True
    key_prefix: str = "rate_limit"


def parse_rate_limit(limit_str: str) -> tuple[int, int]:
    """Parse rate limit string like '5/minute' or '100/hour'.

    Args:
        limit_str: Rate limit string in format "count/period"

    Returns:
        Tuple of (count, window_seconds)

    Examples:
        >>> parse_rate_limit("5/minute")
        (5, 60)
        >>> parse_rate_limit("100/hour")
        (100, 3600)
    """
    parts = limit_str.lower().split("/")
    if len(parts) != 2:
        return 100, 3600  # Default: 100/hour

    try:
        count = int(parts[0])
    except ValueError:
        return 100, 3600

    period = parts[1]

    period_map = {
        "second": 1,
        "seconds": 1,
        "s": 1,
        "minute": 60,
        "minutes": 60,
        "m": 60,
        "hour": 3600,
        "hours": 3600,
        "h": 3600,
        "day": 86400,
        "days": 86400,
        "d": 86400,
    }

    window = period_map.get(period, 3600)
    return count, window


class InMemoryRateLimiter:
    """In-memory rate limiter for fallback when Redis is unavailable.

    Uses a simple sliding window with cleanup on each check.
    Not suitable for multi-instance deployments.
    """

    def __init__(self):
        self._store: dict[str, list] = {}
        self._last_cleanup = time.time()

    def check_and_increment(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int, float]:
        """Check rate limit and increment counter.

        Args:
            key: Unique identifier for the client
            limit: Maximum requests allowed
            window_seconds: Window size in seconds

        Returns:
            Tuple of (allowed, remaining, retry_after)
        """
        current_time = time.time()
        window_start = current_time - window_seconds

        # Periodic cleanup (every 60 seconds)
        if current_time - self._last_cleanup > 60:
            self._cleanup(current_time)
            self._last_cleanup = current_time

        # Get or create entry
        if key not in self._store:
            self._store[key] = []

        # Remove expired entries
        self._store[key] = [t for t in self._store[key] if t > window_start]

        # Check limit
        current_count = len(self._store[key])

        if current_count >= limit:
            # Calculate retry after
            if self._store[key]:
                retry_after = self._store[key][0] + window_seconds - current_time
            else:
                retry_after = window_seconds
            return False, 0, max(0, retry_after)

        # Increment
        self._store[key].append(current_time)
        return True, limit - current_count - 1, 0

    def _cleanup(self, current_time: float, max_age: int = 86400):
        """Remove entries older than max_age seconds."""
        cutoff = current_time - max_age
        keys_to_remove = []
        for key, timestamps in self._store.items():
            self._store[key] = [t for t in timestamps if t > cutoff]
            if not self._store[key]:
                keys_to_remove.append(key)
        for key in keys_to_remove:
            del self._store[key]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware with Redis backend and in-memory fallback.

    Implements sliding window algorithm with:
    - Endpoint-specific limits via glob patterns
    - Per-API-key rate limiting via X-API-Key header
    - Per-IP rate limiting as fallback
    - Redis-backed storage with automatic in-memory fallback

    Attributes:
        app: ASGI application
        redis_url: Redis connection URL (None for in-memory only)
        config: RateLimitConfig instance
        requests_per_minute: Legacy parameter for backward compatibility

    Example:
        ```python
        # Basic usage
        app.add_middleware(
            RateLimitMiddleware,
            redis_url="redis://localhost:6379",
            requests_per_minute=60
        )

        # With endpoint-specific limits
        config = RateLimitConfig(
            endpoint_limits={
                "/api/auth/login": "5/minute",
                "/api/admin/*": "20/minute",
            }
        )
        app.add_middleware(RateLimitMiddleware, config=config, redis_url="redis://localhost:6379")
        ```
    """

    def __init__(
        self,
        app: ASGIApp,
        redis_url: str | None = None,
        requests_per_minute: int = 60,
        burst_size: int = 10,
        key_prefix: str = "rate_limit",
        config: RateLimitConfig | None = None,
    ) -> None:
        """
        Initialize rate limiting middleware.

        Args:
            app: ASGI application
            redis_url: Redis connection URL (None to use in-memory only)
            requests_per_minute: Maximum requests per minute (legacy, use config instead)
            burst_size: Burst capacity (legacy, not used in sliding window)
            key_prefix: Redis key prefix (legacy, use config instead)
            config: RateLimitConfig instance for full configuration
        """
        super().__init__(app)
        self.redis_url = redis_url
        self._redis: RedisClient | None = None
        self._redis_module = None

        # Use config if provided, otherwise create from legacy params
        if config is None:
            config = RateLimitConfig(
                default_limit=requests_per_minute,
                default_window_seconds=60,
                key_prefix=key_prefix,
            )
        self.config = config

        # In-memory fallback
        self._memory_limiter = InMemoryRateLimiter()

    async def _get_redis(self) -> RedisClient | None:
        """Get or create Redis connection."""
        if self._redis is not None:
            return self._redis

        if self.redis_url is None:
            return None

        # Lazy import redis
        if self._redis_module is None:
            self._redis_module = _get_redis_module()

        if self._redis_module is None:
            logger.warning("Redis not available, using in-memory rate limiting")
            return None

        try:
            self._redis = self._redis_module.from_url(self.redis_url, decode_responses=True)
            return self._redis
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            return None

    async def dispatch(self, request: Request, call_next) -> Response:
        """
        Process request with rate limiting.

        Args:
            request: Incoming request
            call_next: Next middleware or route handler

        Returns:
            Response or 429 Too Many Requests
        """
        path = request.url.path

        # Check exempt paths
        if path in self.config.exempt_paths:
            return await call_next(request)

        # Get rate limit for this endpoint
        limit, window = self._get_endpoint_limit(path)

        # Get client identifier
        identifier = self._get_identifier(request)
        key = f"{self.config.key_prefix}:{identifier}:{path}"

        # Check rate limit
        allowed, remaining, retry_after = await self._check_rate_limit(key, limit, window)

        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "Too many requests",
                    "retry_after": int(retry_after),
                    "limit": limit,
                    "window_seconds": window,
                },
                headers={
                    "Retry-After": str(int(retry_after)),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time() + retry_after)),
                },
            )

        # Process request
        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(time.time() + window))

        return response

    def _get_endpoint_limit(self, path: str) -> tuple[int, int]:
        """Get rate limit for a specific endpoint.

        Matches against glob patterns in endpoint_limits.

        Args:
            path: Request path

        Returns:
            Tuple of (limit, window_seconds)
        """
        # Check for exact match first
        if path in self.config.endpoint_limits:
            return parse_rate_limit(self.config.endpoint_limits[path])

        # Check for glob pattern matches
        for pattern, limit_str in self.config.endpoint_limits.items():
            if fnmatch(path, pattern):
                return parse_rate_limit(limit_str)

        # Return default
        return self.config.default_limit, self.config.default_window_seconds

    def _get_identifier(self, request: Request) -> str:
        """
        Get client identifier for rate limiting.

        Priority:
        1. API key from X-API-Key header (if enabled)
        2. User ID from request.state.user (if authenticated)
        3. IP address (fallback)

        Args:
            request: Request object

        Returns:
            Client identifier string
        """
        # Check for API key
        if self.config.enable_api_key_limits:
            api_key = request.headers.get("X-API-Key")
            if api_key:
                return f"apikey:{api_key[:16]}"  # Truncate for privacy

        # Use user ID if authenticated
        user = getattr(request.state, "user", None)
        if user:
            user_id = getattr(user, "id", None) or getattr(user, "user_id", None)
            if user_id:
                return f"user:{user_id}"

        # Fall back to IP address
        ip = self._get_client_ip(request)
        return f"ip:{ip}"

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP address, respecting X-Forwarded-For.

        Args:
            request: Request object

        Returns:
            Client IP address
        """
        # Check X-Forwarded-For header
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # Take the first IP (original client)
            return forwarded.split(",")[0].strip()

        # Fall back to direct client IP
        if request.client:
            return request.client.host

        return "unknown"

    async def _check_rate_limit(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int, float]:
        """
        Check if request is within rate limit.

        Args:
            key: Redis key for this client/endpoint
            limit: Maximum requests allowed
            window_seconds: Window size in seconds

        Returns:
            Tuple of (allowed, remaining, retry_after)
        """
        redis_client = await self._get_redis()

        if redis_client is None:
            # Use in-memory fallback
            return self._memory_limiter.check_and_increment(key, limit, window_seconds)

        return await self._check_rate_limit_redis(redis_client, key, limit, window_seconds)

    async def _check_rate_limit_redis(
        self, redis_client: RedisClient, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int, float]:
        """Check rate limit using Redis sliding window.

        Args:
            redis_client: Redis client instance
            key: Redis key for this client/endpoint
            limit: Maximum requests allowed
            window_seconds: Window size in seconds

        Returns:
            Tuple of (allowed, remaining, retry_after)
        """
        current_time = int(time.time())
        window_start = current_time - window_seconds

        try:
            # Clean old entries (sliding window)
            await redis_client.zremrangebyscore(key, 0, window_start)

            # Count requests in current window
            count = await redis_client.zcard(key)

            if count >= limit:
                # Rate limit exceeded
                oldest = await redis_client.zrange(key, 0, 0, withscores=True)
                if oldest:
                    retry_after = oldest[0][1] + window_seconds - current_time
                else:
                    retry_after = window_seconds
                return False, 0, max(0, retry_after)

            # Add current request
            await redis_client.zadd(key, {str(current_time): current_time})
            await redis_client.expire(key, window_seconds)

            return True, limit - count - 1, 0

        except Exception as e:
            logger.error(f"Redis rate limit error: {e}")
            # Fall back to in-memory
            return self._memory_limiter.check_and_increment(key, limit, window_seconds)


async def close_redis() -> None:
    """
    Close Redis connection pool.

    Should be called on application shutdown.
    """
    # This would be implemented with proper connection pool management
    pass


# Convenience function for creating rate limit configs
def create_rate_limits(
    default: str = "100/hour",
    auth: str = "5/minute",
    admin: str = "20/minute",
    public: str = "1000/hour",
) -> RateLimitConfig:
    """Create a standard rate limit configuration.

    Args:
        default: Default rate limit
        auth: Rate limit for auth endpoints
        admin: Rate limit for admin endpoints
        public: Rate limit for public endpoints

    Returns:
        RateLimitConfig instance

    Example:
        ```python
        config = create_rate_limits(
            default="100/hour",
            auth="5/minute",
            admin="20/minute",
            public="1000/hour"
        )
        app.add_middleware(RateLimitMiddleware, config=config, redis_url="redis://localhost:6379")
        ```
    """
    default_limit, default_window = parse_rate_limit(default)

    return RateLimitConfig(
        default_limit=default_limit,
        default_window_seconds=default_window,
        endpoint_limits={
            "/api/auth/login": auth,
            "/api/auth/signup": auth,
            "/api/auth/*": auth,
            "/api/admin/*": admin,
            "/api/public/*": public,
        },
    )
