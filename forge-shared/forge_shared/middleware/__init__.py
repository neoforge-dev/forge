"""
Middleware module for FastAPI applications.

Provides production-ready middleware for rate limiting, security headers,
CORS configuration, request ID tracking, and exception handling.

Example:
    ```python
    from fastapi import FastAPI
    from forge_shared.middleware import (
        MiddlewareRegistry,
        RateLimitMiddleware,
        SecurityMiddleware,
        RequestIDMiddleware
    )

    registry = MiddlewareRegistry()
    registry.register_rate_limit(redis_url="redis://localhost:6379")
    registry.register_security(allowed_origins=["https://app.example.com"])
    registry.register_request_id()

    app = FastAPI()
    registry.apply_to_app(app)
    ```
"""

from .base import BaseMiddleware
from .rate_limit import (
    RateLimitMiddleware,
    RateLimitConfig,
    InMemoryRateLimiter,
    parse_rate_limit,
    create_rate_limits,
)
from .security import SecurityMiddleware
from .cors import CORSMiddleware
from .request_id import RequestIDMiddleware
from .exceptions import ExceptionHandlerMiddleware
from .logging import LoggingMiddleware, StructuredLoggingMiddleware, JSONFormatter
from .registry import (
    MiddlewareRegistry,
    register_rate_limit,
    register_security,
    register_cors,
    register_request_id,
    register_exception_handler,
    register_logging,
    apply_standard_stack,
    apply_minimal_stack,
    configure_backend,
)

__all__ = [
    "BaseMiddleware",
    "RateLimitMiddleware",
    "RateLimitConfig",
    "InMemoryRateLimiter",
    "parse_rate_limit",
    "create_rate_limits",
    "SecurityMiddleware",
    "CORSMiddleware",
    "RequestIDMiddleware",
    "ExceptionHandlerMiddleware",
    "LoggingMiddleware",
    "MiddlewareRegistry",
]
