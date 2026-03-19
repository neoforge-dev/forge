"""
Event tracking utilities and helper functions.

Provides convenience functions for tracking common analytics events
and managing the global PostHog client.

Example:
    ```python
    from forge_shared.analytics.events import track_event, identify

    # Track event
    await track_event("user_signup", {"email": "user@example.com"})

    # Identify user
    await identify("user123", {"name": "John Doe"})
    ```
"""

from typing import Any, Optional
from fastapi import Request

# Global PostHog client instance
_posthog_client: Optional["PostHogClient"] = None


def get_posthog_client() -> Optional["PostHogClient"]:
    """
    Get the global PostHog client instance.

    Returns:
        PostHogClient instance or None if not configured
    """
    global _posthog_client
    return _posthog_client


def set_posthog_client(client: "PostHogClient") -> None:
    """
    Set the global PostHog client instance.

    Args:
        client: PostHogClient instance to use globally
    """
    global _posthog_client
    _posthog_client = client


async def track_event(
    event: str,
    properties: Optional[dict[str, Any]] = None,
    distinct_id: Optional[str] = None,
    request: Optional[Request] = None,
) -> None:
    """
    Track an analytics event.

    Args:
        event: Event name
        properties: Event properties
        distinct_id: User distinct ID (optional if request provided)
        request: FastAPI request object (for extracting user ID)

    Example:
        ```python
        await track_event("user_signup", {"email": "user@example.com"})
        ```
    """
    client = get_posthog_client()
    if not client:
        return

    # Extract distinct_id from request if not provided
    if distinct_id is None and request is not None:
        user = getattr(request.state, "user", None)
        if user:
            distinct_id = user.id

    if distinct_id is None:
        distinct_id = "anonymous"

    await client.track(
        event=event,
        distinct_id=distinct_id,
        properties=properties,
    )


async def identify(
    distinct_id: str,
    properties: dict[str, Any],
) -> None:
    """
    Identify a user with properties.

    Args:
        distinct_id: User distinct ID
        properties: User properties

    Example:
        ```python
        await identify("user123", {"email": "user@example.com", "name": "John"})
        ```
    """
    client = get_posthog_client()
    if not client:
        return

    await client.identify(distinct_id=distinct_id, properties=properties)


async def alias(
    distinct_id: str,
    alias: str,
) -> None:
    """
    Create an alias for a user ID.

    Args:
        distinct_id: User distinct ID
        alias: Alias to create

    Example:
        ```python
        await alias("user123", "anonymous_123")
        ```
    """
    client = get_posthog_client()
    if not client:
        return

    await client.alias(distinct_id=distinct_id, alias=alias)


async def track_page_view(
    request: Request,
    properties: Optional[dict[str, Any]] = None,
) -> None:
    """
    Track a page view event.

    Args:
        request: FastAPI request object
        properties: Additional properties

    Example:
        ```python
        @app.middleware("http")
        async def track_pages(request: Request, call_next):
            response = await call_next(request)
            await track_page_view(request)
            return response
        ```
    """
    page_properties = {
        "path": request.url.path,
        "method": request.method,
        "user_agent": request.headers.get("user-agent"),
        "ip": request.client.host if request.client else None,
    }

    if properties:
        page_properties.update(properties)

    await track_event("page_view", page_properties, request=request)
