"""
Security middleware for HTTP headers.

Provides security-related HTTP headers for FastAPI applications including
HSTS, CSP, X-Frame-Options, and other security best practices.

Example:
    ```python
    from fastapi import FastAPI
    from forge_shared.middleware import SecurityMiddleware

    app = FastAPI()
    app.add_middleware(
        SecurityMiddleware,
        hsts_max_age=31536000
    )
    ```
"""

from typing import Optional, Literal
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


class SecurityMiddleware(BaseHTTPMiddleware):
    """
    Security middleware for HTTP security headers.

    Adds comprehensive security headers to all responses.

    Attributes:
        app: ASGI application
        hsts_enabled: Enable HTTP Strict Transport Security
        hsts_max_age: HSTS max age in seconds
        hsts_include_subdomains: Include subdomains in HSTS
        hsts_preload: Enable HSTS preload
        csp_enabled: Enable Content-Security-Policy
        csp_policy: CSP policy string
        x_frame_options: X-Frame-Options header value
        x_content_type_options: X-Content-Type-Options header value
        x_xss_protection: X-XSS-Protection header value
        referrer_policy: Referrer-Policy header value
        permissions_policy: Permissions-Policy header value

    Example:
        ```python
        app.add_middleware(
            SecurityMiddleware,
            hsts_max_age=31536000,
            csp_policy="default-src 'self'"
        )
        ```
    """

    def __init__(
        self,
        app: ASGIApp,
        hsts_enabled: bool = True,
        hsts_max_age: int = 31536000,
        hsts_include_subdomains: bool = True,
        hsts_preload: bool = False,
        csp_enabled: bool = True,
        csp_policy: Optional[str] = None,
        x_frame_options: Literal["DENY", "SAMEORIGIN"] = "SAMEORIGIN",
        x_content_type_options: str = "nosniff",
        x_xss_protection: str = "1; mode=block",
        referrer_policy: str = "strict-origin-when-cross-origin",
        permissions_policy: Optional[str] = None,
    ) -> None:
        """
        Initialize security middleware.

        Args:
            app: ASGI application
            hsts_enabled: Enable HSTS
            hsts_max_age: HSTS max age
            hsts_include_subdomains: Include subdomains in HSTS
            hsts_preload: Enable HSTS preload
            csp_enabled: Enable CSP
            csp_policy: CSP policy
            x_frame_options: X-Frame-Options value
            x_content_type_options: X-Content-Type-Options value
            x_xss_protection: X-XSS-Protection value
            referrer_policy: Referrer-Policy value
            permissions_policy: Permissions-Policy value
        """
        super().__init__(app)
        self.hsts_enabled = hsts_enabled
        self.hsts_max_age = hsts_max_age
        self.hsts_include_subdomains = hsts_include_subdomains
        self.hsts_preload = hsts_preload
        self.csp_enabled = csp_enabled
        self.csp_policy = csp_policy or self._default_csp_policy()
        self.x_frame_options = x_frame_options
        self.x_content_type_options = x_content_type_options
        self.x_xss_protection = x_xss_protection
        self.referrer_policy = referrer_policy
        self.permissions_policy = permissions_policy or self._default_permissions_policy()

    def _default_csp_policy(self) -> str:
        """Get default CSP policy."""
        return (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'self';"
        )

    def _default_permissions_policy(self) -> str:
        """Get default permissions policy."""
        return (
            "geolocation=(), "
            "microphone=(), "
            "camera=(), "
            "payment=(), "
            "usb=(), "
            "magnetometer=(), "
            "gyroscope=(), "
            "accelerometer=()"
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        """
        Process request and add security headers.

        Args:
            request: Incoming request
            call_next: Next middleware or route handler

        Returns:
            Response with security headers
        """
        response = await call_next(request)

        # Add security headers (works for both success and error responses)
        response.headers["X-Content-Type-Options"] = self.x_content_type_options
        response.headers["X-Frame-Options"] = self.x_frame_options
        response.headers["X-XSS-Protection"] = self.x_xss_protection
        response.headers["Referrer-Policy"] = self.referrer_policy
        response.headers["Permissions-Policy"] = self.permissions_policy

        # HSTS
        if self.hsts_enabled:
            hsts_value = f"max-age={self.hsts_max_age}"
            if self.hsts_include_subdomains:
                hsts_value += "; includeSubDomains"
            if self.hsts_preload:
                hsts_value += "; preload"
            response.headers["Strict-Transport-Security"] = hsts_value

        # CSP
        if self.csp_enabled:
            response.headers["Content-Security-Policy"] = self.csp_policy

        return response
