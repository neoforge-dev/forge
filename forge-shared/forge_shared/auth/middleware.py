"""
Authentication middleware for FastAPI applications.

Provides middleware for automatic JWT token validation and user context
injection into requests. This middleware operates at the FastAPI/ASGI level,
processing every request before it reaches route handlers.

Features:
    - Automatic JWT token extraction from Authorization header
    - Token validation with comprehensive error handling
    - User context injection into request.state
    - Configurable path exclusion (public routes)
    - Non-blocking (authentication failures don't stop request processing)
    - Forge-core compatible user context

Example:
    ```python
    from fastapi import FastAPI
    from forge_shared.auth.middleware import AuthMiddleware

    app = FastAPI()

    # Add auth middleware with excluded paths
    app.add_middleware(
        AuthMiddleware,
        exclude_paths=["/health", "/public", "/docs"]
    )

    # Access user context in routes
    @app.get("/protected")
    async def protected(request: Request):
        user = getattr(request.state, "user", None)
        if user:
            return {"message": f"Hello {user.email}"}
        return {"message": "Hello guest"}
    ```
"""

from typing import Optional

from fastapi import Request, Response
from jose import JWTError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from forge_shared.auth.dependencies import get_jwt_auth
from forge_shared.auth.models import ForgeUser


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Authentication middleware for automatic token validation.

    This middleware extracts and validates JWT tokens from the Authorization
    header and injects the authenticated user into the request state.
    It operates transparently, allowing routes to optionally use the user
    context via request.state.user.

    Behavior:
        - Extracts JWT from Authorization: Bearer <token> header
        - Validates token with configured JWT auth instance
        - Injects ForgeUser into request.state.user on success
        - Continues processing silently on authentication failure
        - Skips processing for excluded paths

    Attributes:
        app: ASGI application
        exclude_paths: List of paths to exclude from authentication

    Example:
        ```python
        from fastapi import FastAPI, Request
        from forge_shared.auth.middleware import AuthMiddleware

        app = FastAPI()

        app.add_middleware(
            AuthMiddleware,
            exclude_paths=[
                "/health",
                "/public",
                "/docs",
                "/openapi.json",
                "/static"
            ]
        )

        @app.get("/api/protected")
        async def protected(request: Request):
            # User is available if token was valid
            user = getattr(request.state, "user", None)
            if user:
                return {"user": user.email, "domain": user.domain}
            return {"error": "Not authenticated"}
        ```
    """

    def __init__(
        self,
        app: ASGIApp,
        exclude_paths: Optional[list[str]] = None,
    ) -> None:
        """
        Initialize authentication middleware.

        Args:
            app: ASGI application (FastAPI app)
            exclude_paths: Paths to exclude from authentication processing.
                          These paths will bypass JWT validation entirely.
                          Useful for public endpoints, health checks, docs, etc.

        Example:
            ```python
            app.add_middleware(
                AuthMiddleware,
                exclude_paths=[
                    "/health",
                    "/metrics",
                    "/docs",
                    "/redoc",
                    "/openapi.json"
                ]
            )
            ```
        """
        super().__init__(app)
        self.exclude_paths = set(exclude_paths or [])

    async def dispatch(self, request: Request, call_next) -> Response:
        """
        Process request and inject user context if authenticated.

        This method is called for every request. It:
        1. Checks if the path should be excluded from auth
        2. Extracts the JWT token from the Authorization header
        3. Validates the token
        4. Injects the user into request.state.user on success
        5. Always calls the next middleware/route handler

        Args:
            request: Incoming HTTP request
            call_next: Next middleware or route handler in the chain

        Returns:
            Response from the next handler

        Note:
            Authentication failures do NOT stop request processing.
            The route handler can check if user exists via:
            ```python
            user = getattr(request.state, "user", None)
            ```
        """
        # Skip authentication for excluded paths
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        # Extract token from Authorization header
        authorization = request.headers.get("Authorization")

        if authorization and authorization.startswith("Bearer "):
            token = authorization.split(" ")[1]

            try:
                # Get JWT auth instance
                auth = get_jwt_auth()

                # Validate token and get payload
                payload = auth.decode_token(token)

                # Convert to ForgeUser and inject into request state
                user = payload.to_user()
                request.state.user = user

            except (JWTError, Exception):
                # Token invalid - continue without user context
                # This allows routes to work for both authenticated and
                # unauthenticated users
                pass

        # Always continue to next middleware/handler
        return await call_next(request)


class StrictAuthMiddleware(BaseHTTPMiddleware):
    """
    Strict authentication middleware that requires authentication.

    Unlike AuthMiddleware which continues silently on authentication failure,
    this middleware returns a 401 Unauthorized response for protected routes.
    Use this when you want to enforce authentication at the middleware level
    rather than per-route.

    Behavior:
        - Same token validation as AuthMiddleware
        - Returns 401 Unauthorized for invalid/missing tokens
        - Still allows excluded paths to pass through

    Attributes:
        app: ASGI application
        exclude_paths: List of paths to exclude from authentication

    Example:
        ```python
        from fastapi import FastAPI
        from forge_shared.auth.middleware import StrictAuthMiddleware

        app = FastAPI()

        # All routes require auth except excluded paths
        app.add_middleware(
            StrictAuthMiddleware,
            exclude_paths=["/health", "/public"]
        )

        # This route will only receive authenticated requests
        @app.get("/api/data")
        async def get_data(request: Request):
            user = request.state.user  # Guaranteed to exist
            return {"user": user.email}
        ```
    """

    def __init__(
        self,
        app: ASGIApp,
        exclude_paths: Optional[list[str]] = None,
    ) -> None:
        """
        Initialize strict authentication middleware.

        Args:
            app: ASGI application (FastAPI app)
            exclude_paths: Paths to exclude from authentication.
                          These paths will NOT require authentication.

        Example:
            ```python
            app.add_middleware(
                StrictAuthMiddleware,
                exclude_paths=["/health", "/public"]
            )
            ```
        """
        super().__init__(app)
        self.exclude_paths = set(exclude_paths or [])

    async def dispatch(self, request: Request, call_next) -> Response:
        """
        Process request with required authentication.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware or route handler

        Returns:
            Response from next handler OR 401 Unauthorized

        Note:
            Returns 401 for protected routes without valid authentication.
        """
        # Skip authentication for excluded paths
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        # Extract token from Authorization header
        authorization = request.headers.get("Authorization")

        if not authorization or not authorization.startswith("Bearer "):
            # Return 401 if no token provided
            from fastapi import status

            return Response(
                content='{"detail": "Not authenticated"}',
                status_code=status.HTTP_401_UNAUTHORIZED,
                media_type="application/json",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = authorization.split(" ")[1]

        try:
            # Get JWT auth instance
            auth = get_jwt_auth()

            # Validate token and get payload
            payload = auth.decode_token(token)

            # Convert to ForgeUser and inject into request state
            user = payload.to_user()
            request.state.user = user

        except (JWTError, Exception) as e:
            # Return 401 for invalid tokens
            from fastapi import status

            return Response(
                content=f'{{"detail": "Could not validate credentials: {e}"}}',
                status_code=status.HTTP_401_UNAUTHORIZED,
                media_type="application/json",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Continue to next middleware/handler
        return await call_next(request)


def get_request_user(request: Request) -> Optional[ForgeUser]:
    """
    Helper function to get the authenticated user from a request.

    This is a convenience function for extracting the user injected by
    AuthMiddleware from the request state.

    Args:
        request: FastAPI Request object

    Returns:
        ForgeUser if authenticated, None otherwise

    Example:
        ```python
        from fastapi import Request
        from forge_shared.auth.middleware import get_request_user

        @app.get("/api/endpoint")
        async def endpoint(request: Request):
            user = get_request_user(request)
            if user:
                return {"user": user.email}
            return {"message": "Not authenticated"}
        ```
    """
    return getattr(request.state, "user", None)


def require_request_user(request: Request) -> ForgeUser:
    """
    Helper function to get the authenticated user or raise HTTP 401.

    This is a convenience function for cases where you want to require
    authentication in a route but don't want to use FastAPI dependencies.

    Args:
        request: FastAPI Request object

    Returns:
        ForgeUser if authenticated

    Raises:
        HTTPException: If user is not authenticated (401 Unauthorized)

    Example:
        ```python
        from fastapi import Request
        from forge_shared.auth.middleware import require_request_user

        @app.get("/api/protected")
        async def protected(request: Request):
            user = require_request_user(request)  # Raises 401 if not auth
            return {"user": user.email}
        ```
    """
    user = get_request_user(request)

    if user is None:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user
