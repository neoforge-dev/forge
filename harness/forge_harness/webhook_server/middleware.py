"""Middleware — extracted from webhook_server_main.py.

Contains:
- RequestTrackingMiddleware  — counts requests for the /api/legacy/metrics endpoint
- WebhookRateLimitMiddleware — per-path rate limiting with tiered limits
- security_headers_middleware — adds security headers (registered via @app.middleware)
"""

import time
from collections import defaultdict
from threading import Lock
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.infrastructure.rate_limiter import (
    RateLimitConfig,
    RateLimiter,
)

logger = get_logger(__name__)


class RequestTrackingMiddleware(BaseHTTPMiddleware):
    """Track request counts for metrics endpoint."""

    def __init__(self, app: Any, request_counter: dict[str, Any]):
        super().__init__(app)
        self._request_counter = request_counter

    async def dispatch(self, request: Request, call_next):
        # Increment total counter
        self._request_counter["total"] += 1
        # Increment endpoint-specific counter
        endpoint = request.url.path
        self._request_counter["by_endpoint"][endpoint] += 1
        return await call_next(request)


def create_request_counter() -> dict[str, Any]:
    """Create a fresh request counter dict."""
    return {"total": 0, "by_endpoint": defaultdict(int)}


class WebhookRateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware for all API endpoints.

    Applies global rate limiting with per-path overrides:
    - Default: 60 requests/minute per IP (configurable via FORGE_RATE_LIMIT_RPM)
    - /api/webhooks/slack: 100 requests/minute per source
    - /api/webhooks/github: 500 requests/minute per source

    Exempt endpoints (no rate limiting):
    - /health, /api/health
    - /api/events (SSE)
    - Any path starting with /api/sse

    Adds standard rate limit headers (X-RateLimit-*) to all responses.
    Returns 429 with proper error format when rate limited.
    """

    def __init__(self, app: Any, global_limiter: RateLimiter):
        super().__init__(app)
        # Per-path rate limiters
        self._limiters: dict[str, RateLimiter] = {}
        self._lock = Lock()
        # Global rate limiter for all other endpoints (passed from create_app)
        self._global_limiter = global_limiter

    def _is_exempt_path(self, path: str) -> bool:
        """Check if path is exempt from rate limiting."""
        exempt_paths = [
            "/health",
            "/api/health",
            "/api/events",
        ]
        # Check exact matches
        if path in exempt_paths:
            return True
        # Check prefix matches
        if path.startswith("/api/sse"):
            return True
        return False

    def _get_limiter_for_path(self, path: str) -> RateLimiter | None:
        """Get rate limiter for specific path.

        Tiered rate limits:
        - /api/auth/login: 10/min (brute-force protection)
        - /api/auth/*: 30/min (auth endpoints)
        - /api/webhooks/slack: 100/min
        - /api/webhooks/github: 500/min
        - /api/*: 60/min (default for all API endpoints)
        - other: no rate limit (health, docs, etc.)
        """
        # Exact path matches first
        exact_limits: dict[str, tuple[int, int]] = {
            "/api/auth/login": (10, 3),  # 10/min, burst 3 (brute-force)
            "/api/webhooks/slack": (100, 10),  # 100/min, burst 10
            "/api/webhooks/github": (500, 50),  # 500/min, burst 50
        }

        if path in exact_limits:
            rpm, burst = exact_limits[path]
            return self._get_or_create_limiter(path, rpm, burst)

        # Prefix matches
        if path.startswith("/api/auth/"):
            return self._get_or_create_limiter("_prefix:/api/auth/", 30, 5)
        if path.startswith("/api/"):
            return self._get_or_create_limiter("_prefix:/api/", 60, 10)

        return None  # No rate limiting for non-API paths

    def _get_or_create_limiter(self, key: str, rpm: int, burst: int) -> RateLimiter:
        """Get or create a rate limiter for the given key."""
        with self._lock:
            if key not in self._limiters:
                config = RateLimitConfig(
                    requests_per_minute=rpm,
                    burst_size=burst,
                    enabled=True,
                )
                self._limiters[key] = RateLimiter(config)
            return self._limiters[key]

    def _get_identifier(self, request: Request) -> str:
        """Get client identifier for rate limiting.

        Uses IP address as identifier, following forge-shared pattern.
        """
        return request.client.host if request.client else "unknown"

    def _get_bucket_tokens(self, limiter: RateLimiter, identifier: str) -> float:
        """Get current token count for client (approximate)."""
        with limiter._lock:
            bucket = limiter._buckets.get(identifier)
            if bucket is None:
                return float(limiter.config.burst_size)
            return bucket.tokens_available()

    async def dispatch(self, request: Request, call_next):
        """Process request with rate limiting."""
        path = request.url.path

        # Skip rate limiting for exempt endpoints
        if self._is_exempt_path(path):
            return await call_next(request)

        # Get limiter for this path (custom or None for non-API paths)
        limiter = self._get_limiter_for_path(path)

        # No rate limiting for non-API paths
        if limiter is None:
            return await call_next(request)

        # Get client identifier
        identifier = self._get_identifier(request)

        # Get current tokens before consuming
        self._get_bucket_tokens(limiter, identifier)

        # Check rate limit
        if not limiter.is_allowed(identifier):
            retry_after = limiter.get_retry_after(identifier)
            logger.warning(f"Rate limit exceeded for {identifier} on {path}")

            # Get rate limit config for headers
            limit = limiter.config.requests_per_minute
            reset_time = int(time.time()) + retry_after

            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": f"Too many requests. Try again in {retry_after} seconds.",
                    "retry_after": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_time),
                },
            )

        # Process request
        response = await call_next(request)

        # Add rate limit headers to successful responses
        limit = limiter.config.requests_per_minute
        remaining = limiter.get_remaining(identifier)
        reset_time = int(time.time()) + 60  # Reset in 1 minute

        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_time)

        return response


async def security_headers_middleware(request: Request, call_next):
    """Add security headers to all responses.

    Headers added:
    - X-Content-Type-Options: nosniff (prevent MIME type sniffing)
    - X-Frame-Options: DENY (prevent clickjacking)
    - X-XSS-Protection: 1; mode=block (legacy XSS protection)
    - Strict-Transport-Security: max-age=31536000; includeSubDomains (HTTPS only)
    - Referrer-Policy: strict-origin-when-cross-origin (control referrer info)

    Note: Content-Security-Policy is intentionally excluded to avoid breaking
    the Command Center frontend.
    """
    response = await call_next(request)

    # Always add these headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Only add HSTS if using HTTPS (check scheme from request)
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response
