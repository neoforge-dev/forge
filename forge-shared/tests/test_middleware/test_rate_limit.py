"""
Comprehensive tests for rate limiting middleware.

Tests cover:
- Rate limit middleware behavior
- Redis-based rate limiting
- In-memory fallback rate limiting
- Sliding window algorithm
- Endpoint-specific limits with glob patterns
- Path exclusions
- Identifier extraction (IP, user, API key)
- Error handling
- Integration scenarios
"""

import pytest
import time
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from forge_shared.middleware.rate_limit import (
    RateLimitMiddleware,
    RateLimitConfig,
    parse_rate_limit,
    create_rate_limits,
    InMemoryRateLimiter,
    close_redis,
)


class TestParseRateLimit:
    """Tests for parse_rate_limit function."""

    def test_parse_minute(self):
        """Test parsing '5/minute' format."""
        count, window = parse_rate_limit("5/minute")
        assert count == 5
        assert window == 60

    def test_parse_hour(self):
        """Test parsing '100/hour' format."""
        count, window = parse_rate_limit("100/hour")
        assert count == 100
        assert window == 3600

    def test_parse_day(self):
        """Test parsing '1000/day' format."""
        count, window = parse_rate_limit("1000/day")
        assert count == 1000
        assert window == 86400

    def test_parse_second(self):
        """Test parsing '10/second' format."""
        count, window = parse_rate_limit("10/second")
        assert count == 10
        assert window == 1

    def test_parse_abbreviations(self):
        """Test parsing abbreviated periods."""
        assert parse_rate_limit("5/m") == (5, 60)
        assert parse_rate_limit("5/h") == (5, 3600)
        assert parse_rate_limit("5/d") == (5, 86400)
        assert parse_rate_limit("5/s") == (5, 1)

    def test_parse_invalid_format_returns_default(self):
        """Test invalid format returns default (100/hour)."""
        count, window = parse_rate_limit("invalid")
        assert count == 100
        assert window == 3600

    def test_parse_invalid_count_returns_default(self):
        """Test invalid count returns default."""
        count, window = parse_rate_limit("abc/minute")
        assert count == 100
        assert window == 3600


class TestRateLimitConfig:
    """Tests for RateLimitConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = RateLimitConfig()
        assert config.default_limit == 100
        assert config.default_window_seconds == 3600
        assert config.endpoint_limits == {}
        assert "/health" in config.exempt_paths

    def test_custom_config(self):
        """Test custom configuration values."""
        config = RateLimitConfig(
            default_limit=50,
            default_window_seconds=60,
            endpoint_limits={"/api/auth/login": "5/minute"},
            exempt_paths=["/health", "/metrics"],
        )
        assert config.default_limit == 50
        assert config.default_window_seconds == 60
        assert config.endpoint_limits["/api/auth/login"] == "5/minute"


class TestCreateRateLimits:
    """Tests for create_rate_limits convenience function."""

    def test_create_rate_limits_default(self):
        """Test creating rate limits with default values."""
        config = create_rate_limits()
        assert config.default_limit == 100
        assert config.default_window_seconds == 3600
        assert "/api/auth/login" in config.endpoint_limits

    def test_create_rate_limits_custom(self):
        """Test creating rate limits with custom values."""
        config = create_rate_limits(
            default="50/minute",
            auth="3/minute",
            admin="10/minute",
        )
        assert config.default_limit == 50
        assert config.default_window_seconds == 60
        assert config.endpoint_limits["/api/auth/login"] == "3/minute"


class TestInMemoryRateLimiter:
    """Tests for in-memory rate limiter."""

    def test_basic_rate_limiting(self):
        """Test basic rate limiting works."""
        limiter = InMemoryRateLimiter()

        # First 5 requests should succeed
        for i in range(5):
            allowed, remaining, retry_after = limiter.check_and_increment(
                "test_key", limit=5, window_seconds=60
            )
            assert allowed is True
            assert remaining == 4 - i

        # 6th request should fail
        allowed, remaining, retry_after = limiter.check_and_increment(
            "test_key", limit=5, window_seconds=60
        )
        assert allowed is False
        assert remaining == 0
        assert retry_after > 0

    def test_separate_keys(self):
        """Test different keys have separate limits."""
        limiter = InMemoryRateLimiter()

        # Use up limit for key1
        for i in range(5):
            limiter.check_and_increment("key1", limit=5, window_seconds=60)

        # key2 should still have full limit
        allowed, remaining, _ = limiter.check_and_increment("key2", limit=5, window_seconds=60)
        assert allowed is True
        assert remaining == 4

    def test_cleanup_removes_old_entries(self):
        """Test cleanup removes expired entries."""
        limiter = InMemoryRateLimiter()

        # Add some old entries by manipulating internal store
        old_time = time.time() - 86500  # Older than max_age
        limiter._store["old_key"] = [old_time]

        # Trigger cleanup by checking a different key
        limiter._last_cleanup = 0  # Force cleanup
        limiter.check_and_increment("new_key", limit=5, window_seconds=60)

        # Old key should be cleaned up
        assert "old_key" not in limiter._store


class TestRateLimitMiddleware:
    """Tests for rate limiting middleware."""

    @pytest.fixture
    def app(self):
        """Create test FastAPI app."""
        app = FastAPI()

        @app.get("/api/test")
        async def test_endpoint():
            return {"message": "success"}

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        @app.get("/api/auth/login")
        async def login():
            return {"token": "test"}

        return app

    def test_middleware_initialization(self, app):
        """Test middleware initialization with default config."""
        middleware = RateLimitMiddleware(
            app,
            requests_per_minute=60,
        )

        assert middleware.config.default_limit == 60
        assert middleware.redis_url is None

    def test_middleware_with_config(self, app):
        """Test middleware initialization with RateLimitConfig."""
        config = RateLimitConfig(
            default_limit=50,
            endpoint_limits={"/api/auth/login": "5/minute"},
        )
        middleware = RateLimitMiddleware(app, config=config)

        assert middleware.config.default_limit == 50
        assert middleware.config.endpoint_limits["/api/auth/login"] == "5/minute"

    def test_middleware_with_redis_url(self, app):
        """Test middleware initialization with Redis URL."""
        middleware = RateLimitMiddleware(
            app,
            redis_url="redis://localhost:6379",
            requests_per_minute=100,
        )

        assert middleware.redis_url == "redis://localhost:6379"
        assert middleware.config.default_limit == 100

    def test_exempt_paths_pass_through(self, app):
        """Test exempt paths bypass rate limiting."""
        config = RateLimitConfig(
            default_limit=1,  # Very low limit
        )
        app.add_middleware(RateLimitMiddleware, config=config)
        client = TestClient(app)

        # Health endpoint should always succeed
        for i in range(5):
            response = client.get("/health")
            assert response.status_code == 200

    def test_in_memory_rate_limiting(self, app):
        """Test in-memory rate limiting works without Redis."""
        config = RateLimitConfig(default_limit=3, default_window_seconds=60)
        app.add_middleware(RateLimitMiddleware, config=config)
        client = TestClient(app)

        # First 3 requests should succeed
        for i in range(3):
            response = client.get("/api/test")
            assert response.status_code == 200

        # 4th request should fail
        response = client.get("/api/test")
        assert response.status_code == 429
        assert "Retry-After" in response.headers

    def test_rate_limit_headers(self, app):
        """Test rate limit headers are added to response."""
        config = RateLimitConfig(default_limit=60, default_window_seconds=60)
        app.add_middleware(RateLimitMiddleware, config=config)
        client = TestClient(app)

        response = client.get("/api/test")

        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert response.headers["X-RateLimit-Limit"] == "60"

    def test_identifier_extraction_from_ip(self, app):
        """Test identifier extraction from IP address."""
        middleware = RateLimitMiddleware(app, requests_per_minute=60)

        # Mock request without user
        request = MagicMock()
        request.state = MagicMock()
        request.headers = {}
        delattr(request.state, "user")
        request.client = MagicMock()
        request.client.host = "192.168.1.1"
        request.url = MagicMock()
        request.url.path = "/api/test"

        identifier = middleware._get_identifier(request)

        assert identifier == "ip:192.168.1.1"

    def test_identifier_extraction_from_user(self, app):
        """Test identifier extraction from authenticated user."""
        middleware = RateLimitMiddleware(app, requests_per_minute=60)

        # Mock request with user
        request = MagicMock()
        request.state = MagicMock()
        request.state.user = MagicMock()
        request.state.user.id = "user123"
        request.headers = {}
        request.url = MagicMock()
        request.url.path = "/api/test"

        identifier = middleware._get_identifier(request)

        assert identifier == "user:user123"

    def test_identifier_extraction_from_api_key(self, app):
        """Test identifier extraction from X-API-Key header."""
        config = RateLimitConfig(enable_api_key_limits=True)
        middleware = RateLimitMiddleware(app, config=config)

        # Mock request with API key (truncated to 16 chars)
        request = MagicMock()
        request.state = MagicMock()
        request.headers = {"X-API-Key": "sk-test-api-key-12345"}
        request.url = MagicMock()
        request.url.path = "/api/test"

        identifier = middleware._get_identifier(request)

        # API key is truncated to 16 chars: "sk-test-api-key-" (16 chars)
        assert identifier == "apikey:sk-test-api-key-"

    def test_identifier_extraction_unknown_ip(self, app):
        """Test identifier extraction when IP is unknown."""
        middleware = RateLimitMiddleware(app, requests_per_minute=60)

        # Mock request without client
        request = MagicMock()
        request.state = MagicMock()
        request.headers = {}
        delattr(request.state, "user")
        request.client = None
        request.url = MagicMock()
        request.url.path = "/api/test"

        identifier = middleware._get_identifier(request)

        assert identifier == "ip:unknown"

    def test_get_endpoint_limit_exact_match(self, app):
        """Test endpoint limit matching with exact path."""
        config = RateLimitConfig(
            default_limit=100,
            endpoint_limits={"/api/auth/login": "5/minute"},
        )
        middleware = RateLimitMiddleware(app, config=config)

        limit, window = middleware._get_endpoint_limit("/api/auth/login")

        assert limit == 5
        assert window == 60

    def test_get_endpoint_limit_glob_pattern(self, app):
        """Test endpoint limit matching with glob pattern."""
        config = RateLimitConfig(
            default_limit=100,
            endpoint_limits={"/api/admin/*": "20/minute"},
        )
        middleware = RateLimitMiddleware(app, config=config)

        limit, window = middleware._get_endpoint_limit("/api/admin/users")

        assert limit == 20
        assert window == 60

    def test_get_endpoint_limit_default(self, app):
        """Test endpoint limit falls back to default."""
        config = RateLimitConfig(default_limit=50, default_window_seconds=60)
        middleware = RateLimitMiddleware(app, config=config)

        limit, window = middleware._get_endpoint_limit("/api/unknown")

        assert limit == 50
        assert window == 60

    def test_x_forwarded_for_header(self, app):
        """Test X-Forwarded-For header is respected."""
        middleware = RateLimitMiddleware(app, requests_per_minute=60)

        request = MagicMock()
        request.state = MagicMock()
        request.headers = {"X-Forwarded-For": "10.0.0.1, 192.168.1.1"}
        request.url = MagicMock()
        request.url.path = "/api/test"

        ip = middleware._get_client_ip(request)

        assert ip == "10.0.0.1"


class TestRateLimitIntegration:
    """Integration tests for rate limiting with mocked Redis."""

    @pytest.fixture
    def app(self):
        """Create comprehensive test app."""
        app = FastAPI()

        @app.get("/api/public")
        async def public():
            return {"public": True}

        @app.get("/api/private")
        async def private():
            return {"private": True}

        @app.post("/api/data")
        async def create_data():
            return {"created": True}

        return app

    @pytest.mark.asyncio
    async def test_rate_limit_with_mocked_redis(self, app):
        """Test rate limiting with mocked Redis."""
        with patch("forge_shared.middleware.rate_limit._get_redis_module") as mock_get_redis:
            # Setup mock Redis module
            mock_redis_module = MagicMock()
            mock_redis = AsyncMock()
            mock_redis.zremrangebyscore = AsyncMock()
            mock_redis.zcard = AsyncMock(return_value=5)  # Under limit
            mock_redis.zadd = AsyncMock()
            mock_redis.expire = AsyncMock()
            mock_redis.zrange = AsyncMock(return_value=[])

            mock_redis_module.from_url = MagicMock(return_value=mock_redis)
            mock_get_redis.return_value = mock_redis_module

            config = RateLimitConfig(default_limit=10)
            app.add_middleware(
                RateLimitMiddleware, config=config, redis_url="redis://localhost:6379"
            )

            client = TestClient(app)
            response = client.get("/api/public")

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_with_mocked_redis(self, app):
        """Test rate limit exceeded response."""
        with patch("forge_shared.middleware.rate_limit._get_redis_module") as mock_get_redis:
            # Setup mock Redis module
            mock_redis_module = MagicMock()
            mock_redis = AsyncMock()
            mock_redis.zremrangebyscore = AsyncMock()
            mock_redis.zcard = AsyncMock(return_value=15)  # Over limit
            mock_redis.zrange = AsyncMock(return_value=[("1000", 1000)])

            mock_redis_module.from_url = MagicMock(return_value=mock_redis)
            mock_get_redis.return_value = mock_redis_module

            config = RateLimitConfig(default_limit=10)
            app.add_middleware(
                RateLimitMiddleware, config=config, redis_url="redis://localhost:6379"
            )

            client = TestClient(app)
            response = client.get("/api/public")

            assert response.status_code == 429
            assert "Retry-After" in response.headers

    @pytest.mark.asyncio
    async def test_rate_limit_redis_failure_falls_back_to_memory(self, app):
        """Test that Redis failures fall back to in-memory rate limiting."""
        # Configure with Redis URL but Redis module not available
        config = RateLimitConfig(default_limit=5)
        app.add_middleware(RateLimitMiddleware, config=config, redis_url="redis://localhost:6379")

        client = TestClient(app)

        # Should still work with in-memory fallback
        for i in range(5):
            response = client.get("/api/public")
            assert response.status_code == 200

        # Should hit limit
        response = client.get("/api/public")
        assert response.status_code == 429


class TestCloseRedis:
    """Tests for Redis cleanup."""

    @pytest.mark.asyncio
    async def test_close_redis(self):
        """Test close_redis function exists and is callable."""
        await close_redis()
        # Should not raise


class TestEndpointSpecificLimits:
    """Tests for endpoint-specific rate limiting."""

    @pytest.fixture
    def app(self):
        """Create test app with various endpoints."""
        app = FastAPI()

        @app.post("/api/auth/login")
        async def login():
            return {"token": "test"}

        @app.post("/api/auth/signup")
        async def signup():
            return {"user": "created"}

        @app.get("/api/admin/users")
        async def admin_users():
            return {"users": []}

        @app.get("/api/public/data")
        async def public_data():
            return {"data": "public"}

        @app.get("/api/other")
        async def other():
            return {"other": True}

        return app

    def test_auth_endpoint_lower_limit(self, app):
        """Test auth endpoints have lower limits."""
        config = RateLimitConfig(
            default_limit=10,
            default_window_seconds=60,
            endpoint_limits={
                "/api/auth/login": "3/minute",
                "/api/auth/signup": "2/minute",
            },
        )
        app.add_middleware(RateLimitMiddleware, config=config)
        client = TestClient(app)

        # Login should allow 3 requests
        for i in range(3):
            response = client.post("/api/auth/login")
            assert response.status_code == 200

        # 4th should fail
        response = client.post("/api/auth/login")
        assert response.status_code == 429

    def test_admin_glob_pattern(self, app):
        """Test admin endpoints match glob pattern."""
        config = RateLimitConfig(
            default_limit=100,
            endpoint_limits={"/api/admin/*": "2/minute"},
        )
        app.add_middleware(RateLimitMiddleware, config=config)
        client = TestClient(app)

        # Admin should allow 2 requests
        for i in range(2):
            response = client.get("/api/admin/users")
            assert response.status_code == 200

        # 3rd should fail
        response = client.get("/api/admin/users")
        assert response.status_code == 429

    def test_public_endpoint_higher_limit(self, app):
        """Test public endpoints have higher limits."""
        config = RateLimitConfig(
            default_limit=3,
            default_window_seconds=60,
            endpoint_limits={"/api/public/*": "5/minute"},
        )
        app.add_middleware(RateLimitMiddleware, config=config)
        client = TestClient(app)

        # Public should allow 5 requests
        for i in range(5):
            response = client.get("/api/public/data")
            assert response.status_code == 200

        # 6th should fail
        response = client.get("/api/public/data")
        assert response.status_code == 429

    def test_default_limit_for_unmatched(self, app):
        """Test unmatched paths use default limit."""
        config = RateLimitConfig(
            default_limit=2,
            default_window_seconds=60,
            endpoint_limits={"/api/auth/*": "10/minute"},
        )
        app.add_middleware(RateLimitMiddleware, config=config)
        client = TestClient(app)

        # /api/other uses default limit
        for i in range(2):
            response = client.get("/api/other")
            assert response.status_code == 200

        response = client.get("/api/other")
        assert response.status_code == 429
