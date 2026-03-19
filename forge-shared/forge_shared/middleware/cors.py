"""
CORS middleware for cross-origin resource sharing.

Provides CORS configuration with fine-grained control over origins,
methods, headers, and credentials.

Example:
    ```python
    from fastapi import FastAPI
    from forge_shared.middleware import CORSMiddleware

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://example.com"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    ```
"""

from typing import Literal, Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


class CORSMiddleware(BaseHTTPMiddleware):
    """
    CORS middleware for cross-origin resource sharing.

    Handles preflight OPTIONS requests and adds CORS headers.

    Attributes:
        app: ASGI application
        allow_origins: List of allowed origins or "*"
        allow_methods: List of allowed HTTP methods
        allow_headers: List of allowed headers
        allow_credentials: Allow credentials in requests
        max_age: Preflight cache duration in seconds
        expose_headers: List of headers to expose to browser

    Example:
        ```python
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://example.com"],
            allow_credentials=True
        )
        ```
    """

    def __init__(
        self,
        app: ASGIApp,
        allow_origins: list[str] | Literal["*"] = "*",
        allow_methods: list[str] | Literal["*"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS",
        allow_headers: list[str] | Literal["*"] = "*",
        allow_credentials: bool = False,
        max_age: int = 600,
        expose_headers: list[str] | None = None,
    ) -> None:
        """
        Initialize CORS middleware.

        Args:
            app: ASGI application
            allow_origins: Allowed origins
            allow_methods: Allowed methods
            allow_headers: Allowed headers
            allow_credentials: Allow credentials
            max_age: Preflight cache duration
            expose_headers: Exposed headers
        """
        super().__init__(app)
        self.allow_origins = allow_origins
        self.allow_methods = allow_methods
        self.allow_headers = allow_headers
        self.allow_credentials = allow_credentials
        self.max_age = max_age
        self.expose_headers = expose_headers or []

    async def dispatch(self, request: Request, call_next) -> Response:
        """
        Process request and handle CORS.

        Args:
            request: Incoming request
            call_next: Next middleware or route handler

        Returns:
            Response with CORS headers
        """
        origin = request.headers.get("origin")

        # Handle preflight request
        if request.method == "OPTIONS":
            return await self._handle_preflight(request, origin)

        # Process request
        response = await call_next(request)

        # Add CORS headers
        if origin and self._is_allowed_origin(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            if self.allow_credentials:
                response.headers["Access-Control-Allow-Credentials"] = "true"

        if self.expose_headers:
            response.headers["Access-Control-Expose-Headers"] = ", ".join(self.expose_headers)

        return response

    async def _handle_preflight(
        self,
        request: Request,
        origin: Optional[str],
    ) -> Response:
        """
        Handle OPTIONS preflight request.

        Args:
            request: Request object
            origin: Origin header value

        Returns:
            Response with preflight CORS headers
        """
        response = Response()

        if origin and self._is_allowed_origin(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            if self.allow_credentials:
                response.headers["Access-Control-Allow-Credentials"] = "true"

        if self.allow_methods != "*":
            response.headers["Access-Control-Allow-Methods"] = ", ".join(self.allow_methods)
        else:
            response.headers["Access-Control-Allow-Methods"] = "*"

        if self.allow_headers != "*":
            response.headers["Access-Control-Allow-Headers"] = ", ".join(self.allow_headers)
        else:
            response.headers["Access-Control-Allow-Headers"] = "*"

        response.headers["Access-Control-Max-Age"] = str(self.max_age)

        return response

    def _is_allowed_origin(self, origin: str) -> bool:
        """
        Check if origin is allowed.

        Args:
            origin: Origin to check

        Returns:
            True if origin is allowed
        """
        if self.allow_origins == "*":
            return True

        # Handle wildcard subdomains
        for allowed in self.allow_origins:
            if allowed == "*":
                return True
            if allowed.startswith("*."):
                domain = allowed[2:]
                if origin.endswith(domain):
                    return True
            if allowed == origin:
                return True

        return False
