"""
Base middleware class with common patterns.

Provides a foundation for custom middleware with consistent error handling,
logging, and request ID tracking.
"""

import logging
import time
from typing import Any, Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp


logger = logging.getLogger(__name__)


def should_skip_path(path: str, exclude_paths: list[str]) -> bool:
    """
    Check if a path should be skipped by middleware.

    Args:
        path: Request path to check
        exclude_paths: List of paths/prefixes to exclude

    Returns:
        True if path should be skipped
    """
    for exclude in exclude_paths:
        if path == exclude or path.startswith(exclude + "/"):
            return True
    return False


def get_request_duration(start_time: float) -> float:
    """
    Calculate request duration in seconds.

    Args:
        start_time: Start time from time.time()

    Returns:
        Duration in seconds
    """
    return time.time() - start_time


class BaseMiddleware(BaseHTTPMiddleware):
    """
    Base middleware class with common functionality.

    Provides consistent error handling, logging, and request ID tracking
    for all middleware implementations.

    Attributes:
        app: ASGI application
        log_requests: Whether to log requests
        log_responses: Whether to log responses
        include_request_id: Whether to include request ID in logs
        config: Optional configuration dictionary
        exclude_paths: Paths to exclude from middleware processing

    Example:
        ```python
        class CustomMiddleware(BaseMiddleware):
            async def dispatch(self, request: Request, call_next) -> Response:
                # Pre-processing
                response = await super().dispatch(request, call_next)
                # Post-processing
                return response
        ```
    """

    def __init__(
        self,
        app: ASGIApp,
        log_requests: bool = False,
        log_responses: bool = False,
        include_request_id: bool = True,
        config: Optional[dict[str, Any]] = None,
        exclude_paths: Optional[list[str]] = None,
    ) -> None:
        """
        Initialize base middleware.

        Args:
            app: ASGI application
            log_requests: Whether to log incoming requests
            log_responses: Whether to log outgoing responses
            include_request_id: Whether to include request ID in logs
            config: Optional configuration dictionary
            exclude_paths: Paths to exclude from middleware processing
        """
        super().__init__(app)
        self.log_requests = log_requests
        self.log_responses = log_responses
        self.include_request_id = include_request_id
        self.config = config or {}
        self.exclude_paths = exclude_paths or []

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """
        Process request with logging and error handling.

        Args:
            request: Incoming request
            call_next: Next middleware or route handler

        Returns:
            Response from downstream handler
        """
        request_id = self._get_request_id(request)

        if self.log_requests:
            self._log_request(request, request_id)

        try:
            response = await self._process_request(request, call_next)

            if self.log_responses:
                self._log_response(request, response, request_id)

            return response

        except Exception as exc:
            logger.error(
                f"Middleware error in {self.__class__.__name__}: {exc}",
                extra={"request_id": request_id} if self.include_request_id else {},
                exc_info=True,
            )
            raise

    async def _process_request(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """
        Process request through middleware chain.

        Override this method in subclasses to add custom logic.

        Args:
            request: Incoming request
            call_next: Next middleware or route handler

        Returns:
            Response from downstream handler
        """
        return await call_next(request)

    def _get_request_id(self, request: Request) -> Optional[str]:
        """
        Get request ID from request state.

        Args:
            request: Request object

        Returns:
            Request ID or None
        """
        return getattr(request.state, "request_id", None)

    def _log_request(self, request: Request, request_id: Optional[str] = None) -> None:
        """
        Log incoming request.

        Args:
            request: Request object
            request_id: Request ID for tracing
        """
        log_data = {
            "method": request.method,
            "url": str(request.url),
            "client": str(request.client) if request.client else None,
        }

        if self.include_request_id and request_id:
            log_data["request_id"] = request_id

        logger.info(f"Incoming request: {request.method} {request.url}", extra=log_data)

    def _log_response(
        self,
        request: Request,
        response: Response,
        request_id: Optional[str] = None,
    ) -> None:
        """
        Log outgoing response.

        Args:
            request: Request object
            response: Response object
            request_id: Request ID for tracing
        """
        log_data = {
            "method": request.method,
            "url": str(request.url),
            "status_code": response.status_code,
        }

        if self.include_request_id and request_id:
            log_data["request_id"] = request_id

        logger.info(
            f"Outgoing response: {response.status_code} for {request.method} {request.url}",
            extra=log_data,
        )

    def _add_response_header(self, response: Response, name: str, value: str) -> None:
        """
        Add header to response.

        Args:
            response: Response object
            name: Header name
            value: Header value
        """
        response.headers[name] = value

    def _get_response_header(self, response: Response, name: str, default: Optional[str] = None) -> Optional[str]:
        """
        Get header from response.

        Args:
            response: Response object
            name: Header name
            default: Default value if header not found

        Returns:
            Header value or default
        """
        return response.headers.get(name, default)
