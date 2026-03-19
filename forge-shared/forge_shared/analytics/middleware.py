"""
Analytics middleware for automatic event tracking.

Provides middleware for automatic tracking of HTTP requests, page views,
and user sessions in FastAPI applications.

Example:
    ```python
    from fastapi import FastAPI
    from forge_shared.analytics.middleware import AnalyticsMiddleware

    app = FastAPI()
    app.add_middleware(
        AnalyticsMiddleware,
        posthog_api_key="your-api-key"
    )
    ```
"""

from typing import Optional
from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from forge_shared.analytics.posthog import PostHogClient
from forge_shared.analytics.events import set_posthog_client


class AnalyticsMiddleware(BaseHTTPMiddleware):
    """
    Analytics middleware for automatic event tracking.

    Automatically tracks HTTP requests, page views, and performance metrics.

    Attributes:
        app: ASGI application
        posthog_api_key: PostHog API key
        posthog_host: PostHog host URL
        exclude_paths: Paths to exclude from tracking
        track_requests: Whether to track HTTP requests
        track_page_views: Whether to track page views

    Example:
        ```python
        app.add_middleware(
            AnalyticsMiddleware,
            posthog_api_key="your-api-key",
            exclude_paths=["/health", "/metrics"]
        )
        ```
    """

    def __init__(
        self,
        app: ASGIApp,
        posthog_api_key: str,
        posthog_host: str = "https://app.posthog.com",
        exclude_paths: Optional[list[str]] = None,
        track_requests: bool = True,
        track_page_views: bool = True,
    ) -> None:
        """
        Initialize analytics middleware.

        Args:
            app: ASGI application
            posthog_api_key: PostHog API key
            posthog_host: PostHog host URL
            exclude_paths: Paths to exclude from tracking
            track_requests: Whether to track HTTP requests
            track_page_views: Whether to track page views
        """
        super().__init__(app)
        self.exclude_paths = exclude_paths or ["/health", "/metrics"]
        self.track_requests = track_requests
        self.track_page_views = track_page_views

        # Initialize PostHog client
        self.client = PostHogClient(api_key=posthog_api_key, host=posthog_host)
        set_posthog_client(self.client)

    async def dispatch(self, request: Request, call_next) -> Response:
        """
        Process request and track analytics events.

        Args:
            request: Incoming request
            call_next: Next middleware or route handler

        Returns:
            Response from next handler
        """
        # Skip tracking for excluded paths
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        import time

        start_time = time.time()

        # Process request
        response = await call_next(request)

        # Calculate request duration
        duration = time.time() - start_time

        # Track HTTP request
        if self.track_requests:
            await self._track_request(request, response, duration)

        # Track page view
        if self.track_page_views and request.method == "GET":
            await self._track_page_view(request)

        return response

    async def _track_request(
        self,
        request: Request,
        response: Response,
        duration: float,
    ) -> None:
        """
        Track HTTP request event.

        Args:
            request: Request object
            response: Response object
            duration: Request duration in seconds
        """
        properties = {
            "path": request.url.path,
            "method": request.method,
            "status_code": response.status_code,
            "duration_ms": round(duration * 1000, 2),
            "user_agent": request.headers.get("user-agent"),
            "ip": request.client.host if request.client else None,
        }

        # Get user ID if authenticated
        user = getattr(request.state, "user", None)
        distinct_id = user.id if user else "anonymous"

        await self.client.track(
            event="http_request",
            distinct_id=distinct_id,
            properties=properties,
        )

    async def _track_page_view(self, request: Request) -> None:
        """
        Track page view event.

        Args:
            request: Request object
        """
        properties = {
            "path": request.url.path,
            "referrer": request.headers.get("referer"),
            "user_agent": request.headers.get("user-agent"),
        }

        # Get user ID if authenticated
        user = getattr(request.state, "user", None)
        distinct_id = user.id if user else "anonymous"

        await self.client.track(
            event="page_view",
            distinct_id=distinct_id,
            properties=properties,
        )
