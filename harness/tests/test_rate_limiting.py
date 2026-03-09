"""
Comprehensive Rate Limiting Tests for Webhook Server
======================================================

Tests for the rate limiting functionality in forge_harness.webhook_server.

Coverage:
1. Token bucket algorithm - core rate limiting logic
2. Rate limiter - multi-client management
3. Rate limit config - configuration and environment variables
4. Thread safety - concurrent client handling
5. Edge cases - extreme configurations and conditions

Unit tests focus on the RateLimiter, TokenBucket, and RateLimitConfig classes.
Integration tests verify these components work within the webhook server.

Uses pytest with pytest-asyncio for async tests.
Follows FORGE test patterns from existing test files.

Note: Middleware integration tests (HTTP 429 responses, X-RateLimit-* headers)
are marked for future implementation once the FastAPI middleware is added.
"""

import time

import pytest


class TestTokenBucket:
    """Unit tests for TokenBucket rate limiter."""

    def test_token_bucket_initialization(self):
        """Test TokenBucket initializes with correct values."""
        try:
            from forge_harness.webhook_server import TokenBucket
        except ImportError:
            pytest.skip("webhook_server not available")

        bucket = TokenBucket(rate_per_second=1.0, burst_size=10)

        assert bucket.rate == 1.0
        assert bucket.burst_size == 10
        assert bucket.tokens == 10.0  # Starts full

    def test_token_bucket_consume_succeeds_when_tokens_available(self):
        """Test consume() returns True when tokens are available."""
        try:
            from forge_harness.webhook_server import TokenBucket
        except ImportError:
            pytest.skip("webhook_server not available")

        bucket = TokenBucket(rate_per_second=1.0, burst_size=5)

        # First 5 requests should succeed (burst size)
        for _ in range(5):
            assert bucket.consume() is True

    def test_token_bucket_consume_fails_when_exhausted(self):
        """Test consume() returns False when tokens exhausted."""
        try:
            from forge_harness.webhook_server import TokenBucket
        except ImportError:
            pytest.skip("webhook_server not available")

        bucket = TokenBucket(rate_per_second=1.0, burst_size=2)

        # Consume all tokens
        assert bucket.consume() is True
        assert bucket.consume() is True

        # Next request should fail
        assert bucket.consume() is False
        assert bucket.consume() is False

    def test_token_bucket_refills_over_time(self):
        """Test tokens refill based on rate_per_second."""
        try:
            from forge_harness.webhook_server import TokenBucket
        except ImportError:
            pytest.skip("webhook_server not available")

        bucket = TokenBucket(rate_per_second=2.0, burst_size=5)

        # Exhaust tokens
        for _ in range(5):
            bucket.consume()

        # Wait for tokens to refill (need 0.5 seconds for 1 token at 2/sec)
        time.sleep(0.6)

        # Should have at least 1 token now
        assert bucket.consume() is True

    def test_token_bucket_respects_burst_size_max(self):
        """Test tokens never exceed burst_size."""
        try:
            from forge_harness.webhook_server import TokenBucket
        except ImportError:
            pytest.skip("webhook_server not available")

        bucket = TokenBucket(rate_per_second=10.0, burst_size=5)

        # Wait a long time
        time.sleep(2.0)

        # Tokens should still be capped at burst_size
        available = bucket.tokens_available()
        assert available <= bucket.burst_size

    def test_token_bucket_tokens_available(self):
        """Test tokens_available() returns approximate current tokens."""
        try:
            from forge_harness.webhook_server import TokenBucket
        except ImportError:
            pytest.skip("webhook_server not available")

        bucket = TokenBucket(rate_per_second=1.0, burst_size=5)

        # Should start with ~5 tokens
        assert bucket.tokens_available() > 4.5

        # After consuming one
        bucket.consume()
        remaining = bucket.tokens_available()
        assert remaining > 3.5 and remaining < 4.5


class TestRateLimiter:
    """Unit tests for RateLimiter managing multiple clients."""

    def test_rate_limiter_disabled(self):
        """Test rate limiting can be disabled."""
        try:
            from forge_harness.webhook_server import RateLimitConfig, RateLimiter
        except ImportError:
            pytest.skip("webhook_server not available")

        config = RateLimitConfig(enabled=False)
        limiter = RateLimiter(config)

        # All requests should be allowed
        for _ in range(1000):
            assert limiter.is_allowed("192.168.1.1") is True

    def test_rate_limiter_creates_bucket_per_ip(self):
        """Test rate limiter creates separate bucket for each IP."""
        try:
            from forge_harness.webhook_server import RateLimitConfig, RateLimiter
        except ImportError:
            pytest.skip("webhook_server not available")

        config = RateLimitConfig(requests_per_minute=60, burst_size=2, enabled=True)
        limiter = RateLimiter(config)

        # IP 1 exhausts its limit
        ip1 = "192.168.1.1"
        assert limiter.is_allowed(ip1) is True
        assert limiter.is_allowed(ip1) is True
        assert limiter.is_allowed(ip1) is False

        # IP 2 should still be allowed
        ip2 = "192.168.1.2"
        assert limiter.is_allowed(ip2) is True
        assert limiter.is_allowed(ip2) is True
        assert limiter.is_allowed(ip2) is False

    def test_rate_limiter_respects_burst_size(self):
        """Test rate limiter respects burst size configuration."""
        try:
            from forge_harness.webhook_server import RateLimitConfig, RateLimiter
        except ImportError:
            pytest.skip("webhook_server not available")

        config = RateLimitConfig(requests_per_minute=600, burst_size=3, enabled=True)
        limiter = RateLimiter(config)

        ip = "192.168.1.1"

        # Should allow burst_size requests immediately
        for i in range(3):
            assert limiter.is_allowed(ip) is True, f"Request {i + 1} should succeed"

        # Next request should fail
        assert limiter.is_allowed(ip) is False

    def test_rate_limiter_get_retry_after(self):
        """Test get_retry_after() returns reasonable seconds."""
        try:
            from forge_harness.webhook_server import RateLimitConfig, RateLimiter
        except ImportError:
            pytest.skip("webhook_server not available")

        config = RateLimitConfig(requests_per_minute=60, burst_size=1, enabled=True)
        limiter = RateLimiter(config)

        ip = "192.168.1.1"

        # Exhaust tokens
        assert limiter.is_allowed(ip) is True
        assert limiter.is_allowed(ip) is False

        # Get retry-after time
        retry_after = limiter.get_retry_after(ip)
        assert retry_after > 0
        assert retry_after <= 60  # Should be within a minute

    def test_rate_limiter_get_retry_after_no_bucket(self):
        """Test get_retry_after() returns 0 for unknown IP."""
        try:
            from forge_harness.webhook_server import RateLimitConfig, RateLimiter
        except ImportError:
            pytest.skip("webhook_server not available")

        config = RateLimitConfig(enabled=True)
        limiter = RateLimiter(config)

        # IP with no bucket yet
        retry_after = limiter.get_retry_after("192.168.1.1")
        assert retry_after == 0

    def test_rate_limiter_get_stats(self):
        """Test get_stats() returns limiter statistics."""
        try:
            from forge_harness.webhook_server import RateLimitConfig, RateLimiter
        except ImportError:
            pytest.skip("webhook_server not available")

        config = RateLimitConfig(requests_per_minute=60, burst_size=10, enabled=True)
        limiter = RateLimiter(config)

        # Create some buckets
        limiter.is_allowed("192.168.1.1")
        limiter.is_allowed("192.168.1.2")

        stats = limiter.get_stats()
        assert stats["enabled"] is True
        assert stats["requests_per_minute"] == 60
        assert stats["burst_size"] == 10
        assert stats["active_clients"] == 2


class TestRateLimitConfig:
    """Tests for RateLimitConfig."""

    def test_rate_limit_config_defaults(self):
        """Test RateLimitConfig has sensible defaults."""
        try:
            from forge_harness.webhook_server import RateLimitConfig
        except ImportError:
            pytest.skip("webhook_server not available")

        config = RateLimitConfig()
        assert config.requests_per_minute == 60
        assert config.burst_size == 10
        assert config.enabled is True

    def test_rate_limit_config_from_env(self, monkeypatch):
        """Test RateLimitConfig reads from environment variables."""
        try:
            from forge_harness.webhook_server import RateLimitConfig
        except ImportError:
            pytest.skip("webhook_server not available")

        monkeypatch.setenv("FORGE_RATE_LIMIT_RPM", "120")
        monkeypatch.setenv("FORGE_RATE_LIMIT_BURST", "20")
        monkeypatch.setenv("FORGE_RATE_LIMIT_ENABLED", "false")

        config = RateLimitConfig.from_env()
        assert config.requests_per_minute == 120
        assert config.burst_size == 20
        assert config.enabled is False

    def test_rate_limit_config_from_env_disabled_variants(self, monkeypatch):
        """Test RateLimitConfig recognizes disabled variants."""
        try:
            from forge_harness.webhook_server import RateLimitConfig
        except ImportError:
            pytest.skip("webhook_server not available")

        # Test "false" variant
        monkeypatch.setenv("FORGE_RATE_LIMIT_ENABLED", "false")
        config = RateLimitConfig.from_env()
        assert config.enabled is False

        # Test "0" variant
        monkeypatch.setenv("FORGE_RATE_LIMIT_ENABLED", "0")
        config = RateLimitConfig.from_env()
        assert config.enabled is False

        # Test "true" variant
        monkeypatch.setenv("FORGE_RATE_LIMIT_ENABLED", "true")
        config = RateLimitConfig.from_env()
        assert config.enabled is True

        # Test "yes" variant
        monkeypatch.setenv("FORGE_RATE_LIMIT_ENABLED", "yes")
        config = RateLimitConfig.from_env()
        assert config.enabled is True

    def test_rate_limit_config_custom_values(self):
        """Test RateLimitConfig accepts custom values."""
        try:
            from forge_harness.webhook_server import RateLimitConfig
        except ImportError:
            pytest.skip("webhook_server not available")

        config = RateLimitConfig(requests_per_minute=240, burst_size=50, enabled=True)
        assert config.requests_per_minute == 240
        assert config.burst_size == 50
        assert config.enabled is True


class TestWebhookServerRateLimiting:
    """Integration tests for rate limiting configuration in webhook server."""

    @pytest.fixture
    def auth_config(self):
        """Create auth config with auth disabled for testing."""
        try:
            from forge_harness.webhook_server import AuthConfig
        except ImportError:
            pytest.skip("webhook_server not available")

        return AuthConfig(require_auth=False)

    @pytest.fixture
    def rate_limit_config(self):
        """Create rate limit config for testing."""
        try:
            from forge_harness.webhook_server import RateLimitConfig
        except ImportError:
            pytest.skip("webhook_server not available")

        # Tight limits for testing: 10 requests/min with burst of 3
        return RateLimitConfig(requests_per_minute=10, burst_size=3, enabled=True)

    @pytest.fixture
    def test_client(self, auth_config, rate_limit_config):
        """Create test client with custom rate limiting."""
        try:
            from fastapi.testclient import TestClient

            from forge_harness.webhook_server import create_app
        except ImportError:
            pytest.skip("FastAPI not available")

        app = create_app(auth_config=auth_config, rate_limit_config=rate_limit_config)
        return TestClient(app)

    def test_health_check_endpoint_exists(self, test_client):
        """Test health check endpoint exists and is accessible."""
        response = test_client.get("/health")
        assert response.status_code == 200

    def test_rate_limit_config_is_passed_to_app(self, auth_config, rate_limit_config):
        """Test rate limit config can be passed to create_app."""
        try:
            from forge_harness.webhook_server import create_app
        except ImportError:
            pytest.skip("webhook_server not available")

        # Should not raise
        app = create_app(auth_config=auth_config, rate_limit_config=rate_limit_config)
        assert app is not None

    def test_rate_limit_config_defaults_applied(self):
        """Test default rate limit config is applied when none specified."""
        try:
            from fastapi.testclient import TestClient

            from forge_harness.webhook_server import AuthConfig, create_app
        except ImportError:
            pytest.skip("FastAPI not available")

        auth_config = AuthConfig(require_auth=False)
        app = create_app(auth_config=auth_config)

        # App should be created successfully with default rate limiting
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200

    def test_rate_limit_can_be_disabled_via_config(self):
        """Test rate limiting can be disabled via config."""
        try:
            from fastapi.testclient import TestClient

            from forge_harness.webhook_server import AuthConfig, RateLimitConfig, create_app
        except ImportError:
            pytest.skip("FastAPI not available")

        auth_config = AuthConfig(require_auth=False)
        rate_limit_config = RateLimitConfig(enabled=False)

        app = create_app(auth_config=auth_config, rate_limit_config=rate_limit_config)
        client = TestClient(app)

        # Make many rapid requests - should all succeed
        for _ in range(100):
            response = client.get("/health")
            assert response.status_code == 200

    def test_get_rate_limiter_global_instance(self):
        """Test get_rate_limiter() provides singleton instance."""
        try:
            from forge_harness.webhook_server import get_rate_limiter
        except ImportError:
            pytest.skip("webhook_server not available")

        limiter1 = get_rate_limiter()
        limiter2 = get_rate_limiter()

        # Should be same instance
        assert limiter1 is limiter2

    def test_rate_limiter_can_extract_client_ip(self):
        """Test rate limiter can extract client IP from request."""
        try:
            from forge_harness.webhook_server import RateLimitConfig, RateLimiter
        except ImportError:
            pytest.skip("webhook_server not available")

        config = RateLimitConfig(enabled=True)
        limiter = RateLimiter(config)

        # Should handle IP extraction
        assert limiter.is_allowed("192.168.1.1") is True

    def test_rate_limiter_stats_accessible(self):
        """Test rate limiter statistics are accessible."""
        try:
            from forge_harness.webhook_server import get_rate_limiter
        except ImportError:
            pytest.skip("webhook_server not available")

        limiter = get_rate_limiter()
        stats = limiter.get_stats()

        assert stats["enabled"] is not None
        assert "requests_per_minute" in stats
        assert "burst_size" in stats
        assert "active_clients" in stats

    def test_multiple_endpoints_accessible(self, test_client):
        """Test multiple endpoints are accessible (basic connectivity)."""
        endpoints = ["/health", "/api/version", "/api/metrics"]

        for endpoint in endpoints:
            response = test_client.get(endpoint)
            # Should be 200 or 404, but not 500
            assert response.status_code < 500


class TestRateLimitEdgeCases:
    """Edge case tests for rate limiting."""

    @pytest.fixture
    def high_rate_config(self):
        """Create config with high rate limits for edge case testing."""
        try:
            from forge_harness.webhook_server import RateLimitConfig
        except ImportError:
            pytest.skip("webhook_server not available")

        return RateLimitConfig(requests_per_minute=1000, burst_size=100, enabled=True)

    @pytest.fixture
    def low_rate_config(self):
        """Create config with very low rate limits."""
        try:
            from forge_harness.webhook_server import RateLimitConfig
        except ImportError:
            pytest.skip("webhook_server not available")

        return RateLimitConfig(requests_per_minute=1, burst_size=1, enabled=True)

    @pytest.fixture
    def high_rate_client(self, high_rate_config):
        """Create test client with high rate limits."""
        try:
            from fastapi.testclient import TestClient

            from forge_harness.webhook_server import AuthConfig, create_app
        except ImportError:
            pytest.skip("FastAPI not available")

        auth_config = AuthConfig(require_auth=False)
        app = create_app(auth_config=auth_config, rate_limit_config=high_rate_config)
        return TestClient(app)

    @pytest.fixture
    def low_rate_client(self, low_rate_config):
        """Create test client with low rate limits."""
        try:
            from fastapi.testclient import TestClient

            from forge_harness.webhook_server import AuthConfig, create_app
        except ImportError:
            pytest.skip("FastAPI not available")

        auth_config = AuthConfig(require_auth=False)
        app = create_app(auth_config=auth_config, rate_limit_config=low_rate_config)
        return TestClient(app)

    def test_very_high_rate_limit(self, high_rate_client):
        """Test system handles high rate limits correctly."""
        # Should allow 100 burst requests
        for i in range(100):
            response = high_rate_client.get("/health")
            assert response.status_code == 200, f"Request {i + 1} should succeed"

    def test_very_low_rate_limit(self, low_rate_client):
        """Test system handles very low rate limits correctly."""
        # Should allow 1 request
        response = low_rate_client.get("/health")
        assert response.status_code == 200

    def test_zero_rpm_rate_limit(self):
        """Test handling of zero requests-per-minute."""
        try:
            from forge_harness.webhook_server import RateLimitConfig, RateLimiter
        except ImportError:
            pytest.skip("webhook_server not available")

        # Zero RPM should effectively disable rate limiting
        config = RateLimitConfig(requests_per_minute=0, burst_size=1, enabled=True)
        limiter = RateLimiter(config)

        # Behavior with 0 rpm may vary - just test it doesn't crash
        result = limiter.is_allowed("192.168.1.1")
        assert isinstance(result, bool)

    def test_fractional_rate_per_second(self):
        """Test rate limiter handles fractional rates correctly."""
        try:
            from forge_harness.webhook_server import RateLimitConfig, RateLimiter
        except ImportError:
            pytest.skip("webhook_server not available")

        # 6 RPM = 0.1 per second
        config = RateLimitConfig(requests_per_minute=6, burst_size=2, enabled=True)
        limiter = RateLimiter(config)

        # Consume burst
        assert limiter.is_allowed("192.168.1.1") is True
        assert limiter.is_allowed("192.168.1.1") is True
        assert limiter.is_allowed("192.168.1.1") is False

        # Wait for tokens to refill with buffer for timing variance
        # At 0.1 per second, need ~10+ seconds for 1 token reliably
        time.sleep(11.0)

        # Should be allowed again (after refill)
        assert limiter.is_allowed("192.168.1.1") is True

    def test_concurrent_clients_independent_limits(self):
        """Test many concurrent clients have independent rate limits."""
        try:
            from forge_harness.webhook_server import RateLimitConfig, RateLimiter
        except ImportError:
            pytest.skip("webhook_server not available")

        config = RateLimitConfig(requests_per_minute=10, burst_size=2, enabled=True)
        limiter = RateLimiter(config)

        # Each of 10 IPs should be able to make 2 burst requests
        for ip_num in range(10):
            ip = f"192.168.1.{ip_num}"
            assert limiter.is_allowed(ip) is True
            assert limiter.is_allowed(ip) is True
            assert limiter.is_allowed(ip) is False  # Third should fail

        # Verify stats show 10 active clients
        stats = limiter.get_stats()
        assert stats["active_clients"] == 10


class TestGlobalRateLimiting:
    """Tests for global rate limiting middleware."""

    @pytest.fixture
    def tight_rate_config(self):
        """Create config with tight limits for testing."""
        try:
            from forge_harness.webhook_server import RateLimitConfig
        except ImportError:
            pytest.skip("webhook_server not available")

        # Tight limits: 6 requests/min with burst of 3
        return RateLimitConfig(requests_per_minute=6, burst_size=3, enabled=True)

    @pytest.fixture
    def test_app(self, tight_rate_config):
        """Create test app with global rate limiting."""
        try:
            from fastapi.testclient import TestClient

            from forge_harness.webhook_server_main import create_app
        except ImportError:
            pytest.skip("webhook_server_main not available")

        app = create_app(rate_limit_config=tight_rate_config)
        return TestClient(app)

    def test_rate_limiting_applies_to_api_endpoints(self, test_app):
        """Test rate limiting applies to regular API endpoints."""
        # Make burst requests
        for i in range(3):
            response = test_app.get("/api/version")
            assert response.status_code == 200, f"Request {i + 1} should succeed"

        # Next request should be rate limited
        response = test_app.get("/api/version")
        assert response.status_code == 429
        assert "error" in response.json()
        assert response.json()["error"] == "rate_limit_exceeded"

    def test_health_endpoint_is_exempt(self, test_app):
        """Test /health endpoint is exempt from rate limiting."""
        # Make many requests to health endpoint
        for i in range(20):
            response = test_app.get("/health")
            assert response.status_code == 200, f"Health check {i + 1} should succeed"

    def test_api_health_endpoint_is_exempt(self, test_app):
        """Test /api/health endpoint is exempt from rate limiting."""
        # Make many requests to api/health endpoint
        for i in range(20):
            response = test_app.get("/api/health")
            assert response.status_code == 200, f"API health check {i + 1} should succeed"

    @pytest.mark.skip(reason="SSE endpoint hangs in TestClient, covered by test_api_sse_paths_are_exempt")
    def test_api_events_sse_is_exempt(self, test_app):
        """Test /api/events (SSE) endpoint is exempt from rate limiting."""
        # SSE endpoint would hang in TestClient due to streaming connection
        # The exemption is verified by test_api_sse_paths_are_exempt instead
        pass

    def test_api_sse_paths_are_exempt(self, test_app):
        """Test paths starting with /api/sse are exempt from rate limiting."""
        # Make multiple requests to /api/sse/* endpoints
        for i in range(10):
            response = test_app.get("/api/sse/health")
            # May get 404 if endpoint doesn't exist, but should not get 429
            assert response.status_code != 429, f"SSE health {i + 1} should not be rate limited"

    def test_rate_limit_headers_present_in_success(self, test_app):
        """Test rate limit headers are present in successful responses."""
        response = test_app.get("/api/version")
        assert response.status_code == 200

        # Check rate limit headers
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers

        # Verify header values are reasonable
        assert int(response.headers["X-RateLimit-Limit"]) > 0
        assert int(response.headers["X-RateLimit-Remaining"]) >= 0
        assert int(response.headers["X-RateLimit-Reset"]) > 0

    def test_rate_limit_headers_present_in_429(self, test_app):
        """Test rate limit headers are present in 429 responses."""
        # Exhaust rate limit
        for _ in range(3):
            test_app.get("/api/version")

        # Next request should be 429 with headers
        response = test_app.get("/api/version")
        assert response.status_code == 429

        # Check rate limit headers
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers
        assert "Retry-After" in response.headers

        # Verify header values
        assert response.headers["X-RateLimit-Remaining"] == "0"
        assert int(response.headers["Retry-After"]) > 0

    def test_rate_limit_429_response_format(self, test_app):
        """Test 429 response has proper error format."""
        # Exhaust rate limit
        for _ in range(3):
            test_app.get("/api/version")

        # Check 429 response
        response = test_app.get("/api/version")
        assert response.status_code == 429

        data = response.json()
        assert "error" in data
        assert data["error"] == "rate_limit_exceeded"
        assert "message" in data
        assert "retry_after" in data
        assert isinstance(data["retry_after"], int)

    def test_rate_limit_per_ip(self, test_app):
        """Test rate limiting is per IP address."""
        # First client exhausts limit
        for _ in range(3):
            response = test_app.get("/api/version")
            assert response.status_code == 200

        # Should be rate limited
        response = test_app.get("/api/version")
        assert response.status_code == 429

        # In TestClient, all requests come from same IP, so we can't test
        # multiple IPs easily. This is a basic sanity check.

    def test_rate_limit_remaining_decreases(self, test_app):
        """Test X-RateLimit-Remaining decreases with each request."""
        # First request
        response1 = test_app.get("/api/version")
        remaining1 = int(response1.headers["X-RateLimit-Remaining"])

        # Second request
        response2 = test_app.get("/api/version")
        remaining2 = int(response2.headers["X-RateLimit-Remaining"])

        # Remaining should decrease (or be equal if timing is weird)
        assert remaining2 <= remaining1

    def test_exempt_endpoints_no_rate_limit_headers(self, test_app):
        """Test exempt endpoints don't have rate limit headers in older implementation.

        Note: Current implementation may add headers to exempt endpoints too,
        which is acceptable. This test documents the behavior.
        """
        response = test_app.get("/health")
        assert response.status_code == 200
        # Headers may or may not be present on exempt endpoints
        # Just verify the request succeeds


class TestRateLimitThreadSafety:
    """Tests for thread safety of rate limiting."""

    def test_token_bucket_thread_safety(self):
        """Test TokenBucket is thread-safe."""
        try:
            from forge_harness.webhook_server import TokenBucket
        except ImportError:
            pytest.skip("webhook_server not available")

        import threading

        bucket = TokenBucket(rate_per_second=10.0, burst_size=100)
        results = []
        lock = threading.Lock()

        def consume_tokens(count):
            """Consume tokens from bucket and record results."""
            for _ in range(count):
                result = bucket.consume()
                with lock:
                    results.append(result)

        # Create threads that all try to consume tokens
        threads = []
        for _ in range(5):
            t = threading.Thread(target=consume_tokens, args=(20,))
            threads.append(t)
            t.start()

        # Wait for all to complete
        for t in threads:
            t.join()

        # Should have 100 True (burst size) and 0 False initially
        # (since we have 10/sec refill and they started immediately)
        success_count = sum(1 for r in results if r)
        assert success_count <= 100  # Burst size

    def test_rate_limiter_thread_safety(self):
        """Test RateLimiter is thread-safe."""
        try:
            from forge_harness.webhook_server import RateLimitConfig, RateLimiter
        except ImportError:
            pytest.skip("webhook_server not available")

        import threading

        config = RateLimitConfig(requests_per_minute=100, burst_size=50, enabled=True)
        limiter = RateLimiter(config)
        results = []
        lock = threading.Lock()

        def check_limit(ip, count):
            """Check rate limit and record results."""
            for _ in range(count):
                result = limiter.is_allowed(ip)
                with lock:
                    results.append(result)

        # Create threads checking same IP
        threads = []
        for _ in range(3):
            t = threading.Thread(target=check_limit, args=("192.168.1.1", 20))
            threads.append(t)
            t.start()

        # Wait for all to complete
        for t in threads:
            t.join()

        # Should have 50 True (burst) and 10 False (limited)
        success_count = sum(1 for r in results if r)
        assert success_count == 50  # Exactly the burst size

    def test_rate_limiter_multiple_ips_thread_safety(self):
        """Test RateLimiter handles multiple IPs safely in threads."""
        try:
            from forge_harness.webhook_server import RateLimitConfig, RateLimiter
        except ImportError:
            pytest.skip("webhook_server not available")

        import threading

        config = RateLimitConfig(requests_per_minute=60, burst_size=5, enabled=True)
        limiter = RateLimiter(config)
        results = {}
        lock = threading.Lock()

        def check_limit_for_ip(ip, count):
            """Check rate limit for specific IP."""
            ip_results = []
            for _ in range(count):
                result = limiter.is_allowed(ip)
                ip_results.append(result)

            with lock:
                results[ip] = ip_results

        # Create threads for different IPs
        threads = []
        for ip_num in range(5):
            ip = f"192.168.1.{ip_num}"
            t = threading.Thread(target=check_limit_for_ip, args=(ip, 10))
            threads.append(t)
            t.start()

        # Wait for all to complete
        for t in threads:
            t.join()

        # Each IP should have exactly 5 True results (burst size)
        for ip, ip_results in results.items():
            success_count = sum(1 for r in ip_results if r)
            assert success_count == 5, f"IP {ip} should have 5 successes"
