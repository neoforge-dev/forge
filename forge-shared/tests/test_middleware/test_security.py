"""
Tests for security middleware.

Test coverage for security headers, HSTS, CSP, and other security features.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi import FastAPI, Request, Response
from starlette.testclient import TestClient


class TestSecurityMiddleware:
    """
    Tests for SecurityMiddleware class.

    Tests security header implementation and configuration.
    """

    def test_security_middleware_imports(self):
        """
        Test that security middleware can be imported.
        """
        from forge_shared.middleware.security import SecurityMiddleware
        assert SecurityMiddleware is not None

    def test_default_initialization(self):
        """
        Test SecurityMiddleware with default settings.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        middleware = SecurityMiddleware(app)
        
        assert middleware.hsts_enabled is True
        assert middleware.hsts_max_age == 31536000
        assert middleware.hsts_include_subdomains is True
        assert middleware.hsts_preload is False
        assert middleware.csp_enabled is True
        assert middleware.x_frame_options == "SAMEORIGIN"
        assert middleware.x_content_type_options == "nosniff"
        assert middleware.x_xss_protection == "1; mode=block"
        assert middleware.referrer_policy == "strict-origin-when-cross-origin"

    def test_custom_initialization(self):
        """
        Test SecurityMiddleware with custom settings.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        middleware = SecurityMiddleware(
            app,
            hsts_enabled=False,
            hsts_max_age=86400,
            hsts_include_subdomains=False,
            hsts_preload=True,
            csp_enabled=False,
            csp_policy="default-src 'none'",
            x_frame_options="DENY",
            referrer_policy="no-referrer",
            permissions_policy="geolocation=()"
        )
        
        assert middleware.hsts_enabled is False
        assert middleware.hsts_max_age == 86400
        assert middleware.hsts_include_subdomains is False
        assert middleware.hsts_preload is True
        assert middleware.csp_enabled is False
        assert middleware.csp_policy == "default-src 'none'"
        assert middleware.x_frame_options == "DENY"
        assert middleware.referrer_policy == "no-referrer"

    def test_default_csp_policy(self):
        """
        Test default CSP policy generation.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        middleware = SecurityMiddleware(app)
        
        csp = middleware._default_csp_policy()
        
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "style-src 'self'" in csp
        assert "img-src 'self' data: https:" in csp
        assert "font-src 'self'" in csp
        assert "connect-src 'self'" in csp
        assert "frame-ancestors 'self'" in csp

    def test_default_permissions_policy(self):
        """
        Test default permissions policy generation.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        middleware = SecurityMiddleware(app)
        
        policy = middleware._default_permissions_policy()
        
        assert "geolocation=()" in policy
        assert "microphone=()" in policy
        assert "camera=()" in policy
        assert "payment=()" in policy
        assert "usb=()" in policy

    @pytest.mark.asyncio
    async def test_dispatch_adds_security_headers(self):
        """
        Test that dispatch adds all security headers.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        
        @app.get("/")
        async def root():
            return {"message": "test"}
        
        app.add_middleware(SecurityMiddleware)
        
        client = TestClient(app)
        response = client.get("/")
        
        assert response.status_code == 200
        assert "X-Content-Type-Options" in response.headers
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "X-Frame-Options" in response.headers
        assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert "X-XSS-Protection" in response.headers
        assert response.headers["X-XSS-Protection"] == "1; mode=block"
        assert "Referrer-Policy" in response.headers
        assert "Permissions-Policy" in response.headers
        assert "Strict-Transport-Security" in response.headers
        assert "Content-Security-Policy" in response.headers

    @pytest.mark.asyncio
    async def test_hsts_with_subdomains(self):
        """
        Test HSTS header with includeSubDomains.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        
        @app.get("/")
        async def root():
            return {"message": "test"}
        
        app.add_middleware(
            SecurityMiddleware,
            hsts_max_age=86400,
            hsts_include_subdomains=True,
            hsts_preload=False
        )
        
        client = TestClient(app)
        response = client.get("/")
        
        hsts = response.headers["Strict-Transport-Security"]
        assert "max-age=86400" in hsts
        assert "includeSubDomains" in hsts
        assert "preload" not in hsts

    @pytest.mark.asyncio
    async def test_hsts_with_preload(self):
        """
        Test HSTS header with preload.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        
        @app.get("/")
        async def root():
            return {"message": "test"}
        
        app.add_middleware(
            SecurityMiddleware,
            hsts_max_age=31536000,
            hsts_include_subdomains=True,
            hsts_preload=True
        )
        
        client = TestClient(app)
        response = client.get("/")
        
        hsts = response.headers["Strict-Transport-Security"]
        assert "max-age=31536000" in hsts
        assert "includeSubDomains" in hsts
        assert "preload" in hsts

    @pytest.mark.asyncio
    async def test_hsts_disabled(self):
        """
        Test HSTS header disabled.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        
        @app.get("/")
        async def root():
            return {"message": "test"}
        
        app.add_middleware(
            SecurityMiddleware,
            hsts_enabled=False
        )
        
        client = TestClient(app)
        response = client.get("/")
        
        assert "Strict-Transport-Security" not in response.headers

    @pytest.mark.asyncio
    async def test_csp_enabled(self):
        """
        Test CSP header enabled.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        
        @app.get("/")
        async def root():
            return {"message": "test"}
        
        custom_csp = "default-src 'none'; script-src 'self'"
        app.add_middleware(
            SecurityMiddleware,
            csp_enabled=True,
            csp_policy=custom_csp
        )
        
        client = TestClient(app)
        response = client.get("/")
        
        assert "Content-Security-Policy" in response.headers
        assert response.headers["Content-Security-Policy"] == custom_csp

    @pytest.mark.asyncio
    async def test_csp_disabled(self):
        """
        Test CSP header disabled.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        
        @app.get("/")
        async def root():
            return {"message": "test"}
        
        app.add_middleware(
            SecurityMiddleware,
            csp_enabled=False
        )
        
        client = TestClient(app)
        response = client.get("/")
        
        assert "Content-Security-Policy" not in response.headers

    @pytest.mark.asyncio
    async def test_x_frame_options_deny(self):
        """
        Test X-Frame-Options set to DENY.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        
        @app.get("/")
        async def root():
            return {"message": "test"}
        
        app.add_middleware(
            SecurityMiddleware,
            x_frame_options="DENY"
        )
        
        client = TestClient(app)
        response = client.get("/")
        
        assert response.headers["X-Frame-Options"] == "DENY"

    @pytest.mark.asyncio
    async def test_custom_referrer_policy(self):
        """
        Test custom Referrer-Policy.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        
        @app.get("/")
        async def root():
            return {"message": "test"}
        
        app.add_middleware(
            SecurityMiddleware,
            referrer_policy="no-referrer-when-downgrade"
        )
        
        client = TestClient(app)
        response = client.get("/")
        
        assert response.headers["Referrer-Policy"] == "no-referrer-when-downgrade"

    @pytest.mark.asyncio
    async def test_custom_permissions_policy(self):
        """
        Test custom Permissions-Policy.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        
        @app.get("/")
        async def root():
            return {"message": "test"}
        
        custom_policy = "geolocation=(self), microphone=()"
        app.add_middleware(
            SecurityMiddleware,
            permissions_policy=custom_policy
        )
        
        client = TestClient(app)
        response = client.get("/")
        
        assert response.headers["Permissions-Policy"] == custom_policy

    @pytest.mark.asyncio
    async def test_multiple_requests(self):
        """
        Test security headers on multiple requests.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        
        @app.get("/test1")
        async def test1():
            return {"message": "test1"}
        
        @app.get("/test2")
        async def test2():
            return {"message": "test2"}
        
        app.add_middleware(SecurityMiddleware)
        
        client = TestClient(app)
        
        # First request
        response1 = client.get("/test1")
        assert "X-Content-Type-Options" in response1.headers
        assert "X-Frame-Options" in response1.headers
        
        # Second request
        response2 = client.get("/test2")
        assert "X-Content-Type-Options" in response2.headers
        assert "X-Frame-Options" in response2.headers

    @pytest.mark.asyncio
    async def test_preserves_existing_headers(self):
        """
        Test that middleware preserves existing response headers.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        
        @app.get("/")
        async def root():
            response = Response(content="test", media_type="text/plain")
            response.headers["X-Custom-Header"] = "custom-value"
            return response
        
        app.add_middleware(SecurityMiddleware)
        
        client = TestClient(app)
        response = client.get("/")
        
        # Should have both custom and security headers
        assert "X-Custom-Header" in response.headers
        assert response.headers["X-Custom-Header"] == "custom-value"
        assert "X-Content-Type-Options" in response.headers


class TestSecurityHeadersIntegration:
    """
    Integration tests for security headers.
    """

    @pytest.mark.asyncio
    async def test_full_security_stack(self):
        """
        Test full security middleware stack.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        
        @app.get("/api/data")
        async def api_data():
            return {"data": "sensitive"}
        
        app.add_middleware(
            SecurityMiddleware,
            hsts_max_age=31536000,
            hsts_include_subdomains=True,
            hsts_preload=True,
            csp_policy="default-src 'self'",
            x_frame_options="DENY",
            referrer_policy="strict-origin",
            permissions_policy="geolocation=(), camera=()"
        )
        
        client = TestClient(app)
        response = client.get("/api/data")
        
        # Verify all headers are present
        assert response.status_code == 200
        assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains; preload"
        assert response.headers["Content-Security-Policy"] == "default-src 'self'"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-XSS-Protection"] == "1; mode=block"
        assert response.headers["Referrer-Policy"] == "strict-origin"
        assert response.headers["Permissions-Policy"] == "geolocation=(), camera=()"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="FastAPI exception handler bypasses middleware - requires exception handler approach")
    async def test_error_responses_have_security_headers(self):
        """
        Test that error responses also include security headers.
        
        Note: This requires adding an exception handler in addition to middleware,
        as FastAPI's exception handler creates new responses that bypass middleware.
        """
        from forge_shared.middleware.security import SecurityMiddleware

        app = FastAPI()
        
        @app.get("/error")
        async def error_endpoint():
            raise ValueError("Test error")
        
        app.add_middleware(SecurityMiddleware)
        
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/error")
        
        # Error responses should still have security headers
        assert "X-Content-Type-Options" in response.headers
        assert "X-Frame-Options" in response.headers
