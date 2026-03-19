"""
Exception handler middleware for consistent error responses.

Catches exceptions and returns consistent JSON error responses
with proper HTTP status codes.

Example:
    ```python
    from fastapi import FastAPI
    from forge_shared.middleware import ExceptionHandlerMiddleware

    app = FastAPI()
    app.add_middleware(ExceptionHandlerMiddleware)
    ```
"""

from typing import Any, Optional
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from pydantic import ValidationError
import logging

logger = logging.getLogger(__name__)


class ExceptionHandlerMiddleware(BaseHTTPMiddleware):
    """
    Exception handler middleware for consistent error responses.

    Catches exceptions and returns formatted JSON error responses.

    Attributes:
        app: ASGI application
        debug: Whether to include debug information
        log_errors: Whether to log errors

    Example:
        ```python
        app.add_middleware(
            ExceptionHandlerMiddleware,
            debug=True
        )
        ```
    """

    def __init__(
        self,
        app: ASGIApp,
        debug: bool = False,
        log_errors: bool = True,
    ) -> None:
        """
        Initialize exception handler middleware.

        Args:
            app: ASGI application
            debug: Include debug information in responses
            log_errors: Log errors to logging system
        """
        super().__init__(app)
        self.debug = debug
        self.log_errors = log_errors

    async def dispatch(self, request: Request, call_next) -> Response:
        """
        Process request and handle exceptions.

        Args:
            request: Incoming request
            call_next: Next middleware or route handler

        Returns:
            Response or error response
        """
        try:
            response = await call_next(request)
            return response

        except ValidationError as exc:
            return self._handle_validation_error(request, exc)

        except ValueError as exc:
            return self._handle_value_error(request, exc)

        except Exception as exc:
            return self._handle_unexpected_error(request, exc)

    def _handle_validation_error(
        self,
        request: Request,
        exc: ValidationError,
    ) -> JSONResponse:
        """
        Handle validation errors.

        Args:
            request: Request object
            exc: Validation exception

        Returns:
            JSON error response
        """
        errors = [
            {"field": ".".join(str(loc) for loc in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]

        if self.log_errors:
            logger.warning(f"Validation error: {errors}")

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "Validation Error",
                "message": "Request validation failed",
                "details": errors,
                "request_id": self._get_request_id(request),
            },
        )

    def _handle_value_error(
        self,
        request: Request,
        exc: ValueError,
    ) -> JSONResponse:
        """
        Handle value errors.

        Args:
            request: Request object
            exc: Value exception

        Returns:
            JSON error response
        """
        if self.log_errors:
            logger.warning(f"Value error: {str(exc)}")

        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "Bad Request",
                "message": str(exc),
                "request_id": self._get_request_id(request),
            },
        )

    def _handle_unexpected_error(
        self,
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """
        Handle unexpected errors.

        Args:
            request: Request object
            exc: Unexpected exception

        Returns:
            JSON error response
        """
        if self.log_errors:
            logger.error(f"Unexpected error: {str(exc)}", exc_info=True)

        content: dict[str, Any] = {
            "error": "Internal Server Error",
            "message": "An unexpected error occurred",
            "request_id": self._get_request_id(request),
        }

        if self.debug:
            content["debug"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=content,
        )

    def _get_request_id(self, request: Request) -> Optional[str]:
        """
        Get request ID from request state.

        Args:
            request: Request object

        Returns:
            Request ID or None
        """
        return getattr(request.state, "request_id", None)
