"""
Tests for middleware module.

Test coverage for rate limiting, security headers, CORS, request ID,
and exception handling middleware.
"""

import pytest
from fastapi import Request, Response
from unittest.mock import MagicMock, AsyncMock

from forge_shared.middleware import (
    RateLimitMiddleware,
    SecurityMiddleware,
    RequestIDMiddleware,
    CORSMiddleware,
)


class TestSecurityMiddleware:
    """
    Tests for SecurityMiddleware.

    Tests security headers are added correctly to responses.
    """

    @pytest.mark.asyncio
    async def test_security_headers_added(self, fastapi_app):
        """
        Test that security headers are added to responses.

        Args:
            fastapi_app: FastAPI app fixture
        """
        middleware = SecurityMiddleware(app=fastapi_app)

        # Mock request and response
        request = MagicMock(spec=Request)
        request.url.path = "/test"

        response = MagicMock(spec=Response)
        response.headers = {}

        # Mock call_next
        async def call_next(req):
            return response

        # Process request
        result = await middleware.dispatch(request, call_next)

        # Verify headers
        assert "X-Content-Type-Options" in response.headers
        assert "X-Frame-Options" in response.headers
        assert "X-XSS-Protection" in response.headers
        assert "Referrer-Policy" in response.headers
        assert "Permissions-Policy" in response.headers

    @pytest.mark.asyncio
    async def test_hsts_header(self, fastapi_app):
        """
        Test HSTS header is added when enabled.

        Args:
            fastapi_app: FastAPI app fixture
        """
        middleware = SecurityMiddleware(
            app=fastapi_app,
            hsts_enabled=True,
            hsts_max_age=31536000,
        )

        request = MagicMock(spec=Request)
        request.url.path = "/test"

        response = MagicMock(spec=Response)
        response.headers = {}

        async def call_next(req):
            return response

        await middleware.dispatch(request, call_next)

        assert "Strict-Transport-Security" in response.headers

    def test_default_csp_policy(self):
        """
        Test default CSP policy.
        """
        middleware = SecurityMiddleware(app=MagicMock())
        policy = middleware._default_csp_policy()

        assert "default-src 'self'" in policy
        assert "script-src" in policy
        assert "style-src" in policy


class TestRequestIDMiddleware:
    """
    Tests for RequestIDMiddleware.

    Tests request ID generation, extraction, and header injection.
    """

    @pytest.mark.asyncio
    async def test_request_id_generated(self, fastapi_app):
        """
        Test that request ID is generated when not provided.

        Args:
            fastapi_app: FastAPI app fixture
        """
        middleware = RequestIDMiddleware(app=fastapi_app)

        request = MagicMock(spec=Request)
        request.url.path = "/test"
        request.headers = {}
        request.state = MagicMock()

        response = MagicMock(spec=Response)
        response.headers = {}

        async def call_next(req):
            return response

        await middleware.dispatch(request, call_next)

        # Verify request ID was added
        assert "X-Request-ID" in response.headers
        assert len(response.headers["X-Request-ID"]) > 0

    @pytest.mark.asyncio
    async def test_request_id_from_header(self, fastapi_app):
        """
        Test that request ID from header is used.

        Args:
            fastapi_app: FastAPI app fixture
        """
        middleware = RequestIDMiddleware(app=fastapi_app)

        request = MagicMock(spec=Request)
        request.url.path = "/test"
        request.headers = {"X-Request-ID": "existing-id-123"}
        request.state = MagicMock()

        response = MagicMock(spec=Response)
        response.headers = {}

        async def call_next(req):
            return response

        await middleware.dispatch(request, call_next)

        # Verify existing ID was used
        assert response.headers["X-Request-ID"] == "existing-id-123"


class TestCORSMiddleware:
    """
    Tests for CORSMiddleware.

    Tests CORS headers, preflight requests, and origin validation.
    """

    @pytest.mark.asyncio
    async def test_cors_headers_added(self, fastapi_app):
        """
        Test that CORS headers are added to responses.

        Args:
            fastapi_app: FastAPI app fixture
        """
        middleware = CORSMiddleware(
            app=fastapi_app,
            allow_origins=["https://example.com"],
        )

        request = MagicMock(spec=Request)
        request.url.path = "/test"
        request.method = "GET"
        request.headers = {"origin": "https://example.com"}

        response = MagicMock(spec=Response)
        response.headers = {}

        async def call_next(req):
            return response

        await middleware.dispatch(request, call_next)

        # Verify CORS header
        assert response.headers.get("Access-Control-Allow-Origin") == "https://example.com"

    @pytest.mark.asyncio
    async def test_preflight_request(self, fastapi_app):
        """
        Test handling of OPTIONS preflight request.

        Args:
            fastapi_app: FastAPI app fixture
        """
        middleware = CORSMiddleware(
            app=fastapi_app,
            allow_origins=["*"],
        )

        request = MagicMock(spec=Request)
        request.url.path = "/test"
        request.method = "OPTIONS"
        request.headers = {"origin": "https://example.com"}

        async def call_next(req):
            return MagicMock(spec=Response)

        response = await middleware.dispatch(request, call_next)

        # Verify preflight headers
        assert "Access-Control-Allow-Methods" in response.headers
        assert "Access-Control-Max-Age" in response.headers

    def test_wildcard_origin(self):
        """
        Test wildcard origin validation.
        """
        middleware = CORSMiddleware(app=MagicMock(), allow_origins=["*"])

        assert middleware._is_allowed_origin("https://any-origin.com")
        assert middleware._is_allowed_origin("http://localhost:3000")

    def test_specific_origin(self):
        """
        Test specific origin validation.
        """
        middleware = CORSMiddleware(
            app=MagicMock(),
            allow_origins=["https://example.com"],
        )

        assert middleware._is_allowed_origin("https://example.com")
        assert not middleware._is_allowed_origin("https://other.com")

    def test_subdomain_wildcard(self):
        """
        Test subdomain wildcard validation.
        """
        middleware = CORSMiddleware(
            app=MagicMock(),
            allow_origins=["*.example.com"],
        )

        assert middleware._is_allowed_origin("https://sub.example.com")
        assert middleware._is_allowed_origin("https://api.example.com")
        assert not middleware._is_allowed_origin("https://example.com")
        assert not middleware._is_allowed_origin("https://other.com")


class TestRateLimitMiddleware:
    """
    Tests for RateLimitMiddleware.

    Tests rate limiting logic, Redis interaction, and rate limit headers.
    """

    def test_get_identifier_from_user(self):
        """
        Test getting identifier from authenticated user.
        """
        middleware = RateLimitMiddleware(app=MagicMock())

        request = MagicMock(spec=Request)
        request.state.user = MagicMock()
        request.state.user.id = "user123"

        identifier = middleware._get_identifier(request)

        assert identifier == "user:user123"

    def test_get_identifier_from_ip(self):
        """
        Test getting identifier from IP address.
        """
        middleware = RateLimitMiddleware(app=MagicMock())

        request = MagicMock(spec=Request)
        request.state.user = None
        request.client = MagicMock()
        request.client.host = "192.168.1.1"

        identifier = middleware._get_identifier(request)

        assert identifier == "ip:192.168.1.1"

    @pytest.mark.asyncio
    async def test_check_rate_limit_redis_unavailable(self):
        """
        Test that rate limit allows requests when Redis is unavailable.

        Args:
            redis_mock: Redis mock fixture
        """
        middleware = RateLimitMiddleware(app=MagicMock(), redis_url="redis://invalid")

        # Should fail open and allow request
        allowed, retry_after = await middleware._check_rate_limit("user123")

        assert allowed is True
        assert retry_after == 0
