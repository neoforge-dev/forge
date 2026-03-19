"""
Logging middleware for request/response logging.

Provides automatic logging of HTTP requests and responses with
timing information and request context.

Example:
    ```python
    from fastapi import FastAPI
    from forge_shared.logging.middleware import LoggingMiddleware

    app = FastAPI()
    app.add_middleware(LoggingMiddleware)
    ```
"""

import time
import logging
from typing import Optional, Callable, Any
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from forge_shared.logging.context import (
    set_request_id,
    set_user_id,
    get_logging_context,
)

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Logging middleware for HTTP request/response logging.

    Automatically logs all HTTP requests with timing, status codes,
    and request context.

    Attributes:
        app: ASGI application
        logger_name: Logger name to use
        log_level: Logging level for requests
        log_request_body: Whether to log request bodies
        log_response_body: Whether to log response bodies
        skip_paths: Paths to skip logging
        exclude_paths: Alias for skip_paths
        log_format: Log format ('json' or 'text')
        sensitive_headers: Headers to mask in logs

    Example:
        ```python
        app.add_middleware(
            LoggingMiddleware,
            skip_paths=["/health", "/metrics"]
        )
        ```
    """

    def __init__(
        self,
        app: Any,
        logger_name: str = "forge.request",
        log_level: int = logging.INFO,
        log_request_body: bool = False,
        log_response_body: bool = False,
        skip_paths: Optional[list[str]] = None,
        exclude_paths: Optional[list[str]] = None,
        log_format: str = "text",
        sensitive_headers: Optional[list[str]] = None,
    ) -> None:
        """
        Initialize logging middleware.

        Args:
            app: ASGI application
            logger_name: Logger name
            log_level: Logging level
            log_request_body: Log request bodies
            log_response_body: Log response bodies
            skip_paths: Paths to skip logging
            exclude_paths: Alias for skip_paths
            log_format: Log format ('json' or 'text')
            sensitive_headers: Headers to mask in logs
        """
        super().__init__(app)
        self.logger = logging.getLogger(logger_name)
        self.log_level = log_level
        self.log_request_body = log_request_body
        self.log_response_body = log_response_body
        self.skip_paths = skip_paths or exclude_paths or ["/health", "/metrics"]
        self.log_format = log_format
        self.sensitive_headers = sensitive_headers or [
            "authorization",
            "x-api-key",
            "cookie",
            "set-cookie",
        ]

    async def dispatch(self, request: Request, call_next) -> Response:
        """
        Process request and log with context.

        Args:
            request: Incoming request
            call_next: Next middleware or route handler

        Returns:
            Response from next handler
        """
        # Skip logging for specified paths
        if request.url.path in self.skip_paths:
            return await call_next(request)

        # Start timing
        start_time = time.time()

        # Extract request ID
        request_id = request.headers.get("X-Request-ID")
        if request_id:
            set_request_id(request_id)

        # Extract user ID if authenticated
        user = getattr(request.state, "user", None)
        if user:
            set_user_id(user.id)

        # Log request
        self._log_request(request)

        # Process request
        response = await call_next(request)

        # Calculate duration
        duration = time.time() - start_time

        # Log response
        self._log_response(request, response, duration)

        return response

    def _log_request(self, request: Request) -> None:
        """
        Log incoming request.

        Args:
            request: Request object
        """
        log_data = {
            "type": "request",
            "method": request.method,
            "path": request.url.path,
            "query_params": str(request.url.query) if request.url.query else None,
            "user_agent": request.headers.get("user-agent"),
            "ip": request.client.host if request.client else None,
            **get_logging_context(),
        }

        self.logger.log(self.log_level, "Incoming request", extra=log_data)

    def _log_response(
        self,
        request: Request,
        response: Response,
        duration: float,
    ) -> None:
        """
        Log outgoing response.

        Args:
            request: Request object
            response: Response object
            duration: Request duration in seconds
        """
        log_data = {
            "type": "response",
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round(duration * 1000, 2),
            **get_logging_context(),
        }

        self.logger.log(self.log_level, "Outgoing response", extra=log_data)
