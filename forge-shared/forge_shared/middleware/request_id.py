"""
Request ID middleware for request tracing.

Generates unique request IDs for tracing requests through the system
and logs them for debugging and monitoring.

Example:
    ```python
    from fastapi import FastAPI
    from forge_shared.middleware import RequestIDMiddleware

    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    ```
"""

from typing import Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
import uuid


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Request ID middleware for request tracing.

    Generates or extracts request IDs and adds them to responses
    for distributed tracing.

    Attributes:
        app: ASGI application
        header_name: Header name for request ID
        generator: Function to generate request IDs
        validate_uuid: Whether to validate UUID format

    Example:
        ```python
        app.add_middleware(
            RequestIDMiddleware,
            header_name="X-Request-ID"
        )
        ```
    """

    def __init__(
        self,
        app: ASGIApp,
        header_name: str = "X-Request-ID",
        generator: Optional[callable] = None,
        validate_uuid: bool = False,
    ) -> None:
        """
        Initialize request ID middleware.

        Args:
            app: ASGI application
            header_name: Header name for request ID
            generator: Custom ID generator function
            validate_uuid: Whether to validate UUID format
        """
        super().__init__(app)
        self.header_name = header_name
        self.generator = generator or self._default_generator
        self.validate_uuid = validate_uuid

    def _default_generator(self) -> str:
        """
        Generate default request ID using UUID4.

        Returns:
            Request ID string
        """
        return str(uuid.uuid4())

    async def dispatch(self, request: Request, call_next) -> Response:
        """
        Process request and add request ID.

        Args:
            request: Incoming request
            call_next: Next middleware or route handler

        Returns:
            Response with request ID header
        """
        # Extract or generate request ID
        request_id = request.headers.get(self.header_name)

        if not request_id:
            request_id = self.generator()
        elif self.validate_uuid:
            # Validate UUID format
            try:
                uuid.UUID(request_id)
            except ValueError:
                request_id = self.generator()

        # Store request ID for use in other middleware/handlers
        request.state.request_id = request_id

        # Process request
        response = await call_next(request)

        # Add request ID to response
        response.headers[self.header_name] = request_id

        return response
