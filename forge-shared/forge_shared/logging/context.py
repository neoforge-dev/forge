"""
Logging context for request-scoped data.

Provides context management for adding request-specific data to logs.

Example:
    ```python
    from forge_shared.logging.context import LoggingContext

    with LoggingContext(request_id="123", user_id="456"):
        logger.info("Processing request")
    ```
"""

import contextvars
from typing import Any

# Context variables for logging
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("user_id", default=None)
_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)
_custom_fields: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "custom_fields", default={}
)


class LoggingContext:
    """
    Context manager for logging context.

    Manages request-scoped logging data like request ID, user ID, and
    correlation ID.

    Example:
        ```python
        with LoggingContext(request_id="123", user_id="456"):
            logger.info("Processing request")
        ```
    """

    def __init__(
        self,
        request_id: str | None = None,
        user_id: str | None = None,
        correlation_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize logging context.

        Args:
            request_id: Request ID
            user_id: User ID
            correlation_id: Correlation ID
            **kwargs: Additional custom fields
        """
        self.request_id = request_id
        self.user_id = user_id
        self.correlation_id = correlation_id
        self.custom_fields = kwargs
        self._tokens: list[contextvars.Token] = []

    def __enter__(self) -> "LoggingContext":
        """Enter context and set context variables."""
        if self.request_id:
            self._tokens.append(_request_id.set(self.request_id))
        if self.user_id:
            self._tokens.append(_user_id.set(self.user_id))
        if self.correlation_id:
            self._tokens.append(_correlation_id.set(self.correlation_id))
        if self.custom_fields:
            self._tokens.append(_custom_fields.set(self.custom_fields))
        return self

    def __exit__(self, *args: Any) -> None:
        """Exit context and reset context variables."""
        for token in self._tokens:
            _custom_fields.reset(token)


def get_logging_context() -> dict[str, Any]:
    """
    Get current logging context as dictionary.

    Returns:
        Dictionary with current context values

    Example:
        ```python
        context = get_logging_context()
        logger.info("Processing", extra=context)
        ```
    """
    context = {}

    request_id = _request_id.get()
    if request_id:
        context["request_id"] = request_id

    user_id = _user_id.get()
    if user_id:
        context["user_id"] = user_id

    correlation_id = _correlation_id.get()
    if correlation_id:
        context["correlation_id"] = correlation_id

    custom_fields = _custom_fields.get()
    if custom_fields:
        context.update(custom_fields)

    return context


def set_request_id(request_id: str) -> None:
    """
    Set request ID in logging context.

    Args:
        request_id: Request ID to set
    """
    _request_id.set(request_id)


def set_user_id(user_id: str) -> None:
    """
    Set user ID in logging context.

    Args:
        user_id: User ID to set
    """
    _user_id.set(user_id)


def set_correlation_id(correlation_id: str) -> None:
    """
    Set correlation ID in logging context.

    Args:
        correlation_id: Correlation ID to set
    """
    _correlation_id.set(correlation_id)


def get_request_id() -> str | None:
    """
    Get current request ID from logging context.

    Returns:
        Request ID string or None if not set
    """
    return _request_id.get()


def get_user_id() -> str | None:
    """
    Get current user ID from logging context.

    Returns:
        User ID string or None if not set
    """
    return _user_id.get()


def get_correlation_id() -> str | None:
    """
    Get current correlation ID from logging context.

    Returns:
        Correlation ID string or None if not set
    """
    return _correlation_id.get()


def get_context_dict() -> dict[str, Any]:
    """
    Get all context variables as a dictionary.

    Returns:
        Dict with request_id, user_id, correlation_id (if set) plus custom fields
    """
    return get_logging_context()


def clear_context() -> None:
    """
    Clear all context variables (reset to defaults).
    """
    _request_id.set(None)
    _user_id.set(None)
    _correlation_id.set(None)
    _custom_fields.set({})


def add_custom_field(key: str, value: Any) -> None:
    """
    Add custom field to logging context.

    Args:
        key: Field name
        value: Field value
    """
    fields = _custom_fields.get()
    fields[key] = value
    _custom_fields.set(fields)


class _RequestContext:
    """
    Context manager that sets logging context variables for the duration of a block
    and clears them on exit.

    Example:
        ```python
        with request_context(request_id="req-1", user_id="user-2"):
            logger.info("Handling request")
        ```
    """

    def __init__(
        self, request_id: str | None = None, user_id: str | None = None, **kwargs: Any
    ) -> None:
        self._request_id = request_id
        self._user_id = user_id
        self._kwargs = kwargs
        self._tokens: list[Any] = []

    def __enter__(self) -> "_RequestContext":
        if self._request_id is not None:
            self._tokens.append(_request_id.set(self._request_id))
        if self._user_id is not None:
            self._tokens.append(_user_id.set(self._user_id))
        if self._kwargs:
            self._tokens.append(_custom_fields.set(self._kwargs))
        return self

    def __exit__(self, *args: Any) -> None:
        for token in self._tokens:
            try:
                token.var.reset(token)
            except Exception:
                pass
        self._tokens.clear()


def request_context(
    request_id: str | None = None,
    user_id: str | None = None,
    **kwargs: Any,
) -> "_RequestContext":
    """
    Return a context manager that sets logging context variables.

    Args:
        request_id: Request ID to set for the duration of the block
        user_id: User ID to set for the duration of the block
        **kwargs: Additional custom fields

    Returns:
        Context manager
    """
    return _RequestContext(request_id=request_id, user_id=user_id, **kwargs)


try:
    import uuid

    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import Response

    class ContextMiddleware(BaseHTTPMiddleware):
        """
        Middleware that automatically sets up logging context for each request.

        Generates a new request ID for every incoming request and stores it
        in the logging context so that all logs within the request share the
        same request ID.

        Example:
            ```python
            app.add_middleware(ContextMiddleware)
            ```
        """

        async def dispatch(self, request: Request, call_next):  # type: ignore[override]
            """Set context variables for the request lifetime."""
            # Use incoming header if present, otherwise generate one
            request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
            set_request_id(request_id)

            try:
                response: Response = await call_next(request)
            finally:
                clear_context()

            return response

except ImportError:
    # starlette/fastapi not available — define a stub so imports don't fail
    class ContextMiddleware:  # type: ignore[no-redef]
        """Stub when starlette is not installed."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError("starlette is required for ContextMiddleware")
