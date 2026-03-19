"""
Analytics module for PostHog integration.

Provides PostHog client for event tracking, user identification,
and automatic analytics middleware for FastAPI applications.

Example:
    ```python
    from forge_shared.analytics import PostHogClient, track_event

    # Initialize client
    client = PostHogClient(api_key="your-api-key")

    # Track event
    await track_event("user_signup", {"email": "user@example.com"})
    ```

Submodules:
    - posthog: PostHog client implementation
    - events: Event tracking utilities
    - middleware: FastAPI middleware for automatic tracking
    - conversion: UTM attribution and conversion tracking
"""

from forge_shared.analytics.posthog import PostHogClient
from forge_shared.analytics.conversion import (
    ConversionTracker,
    UTMParams,
    get_conversion_tracker,
    set_conversion_tracker,
    track_conversion,
    track_funnel_step,
    track_payment_conversion,
    track_signup_conversion,
    track_stripe_conversion,
    track_subscription_conversion,
)
from forge_shared.analytics.events import alias, identify, track_event, get_posthog_client, set_posthog_client
from forge_shared.analytics.middleware import AnalyticsMiddleware

__all__ = [
    # Client
    "PostHogClient",
    # Events
    "track_event",
    "identify",
    "alias",
    # Client management
    "get_posthog_client",
    "set_posthog_client",
    # Middleware
    "AnalyticsMiddleware",
    # Conversion Tracking
    "ConversionTracker",
    "UTMParams",
    "track_conversion",
    "track_stripe_conversion",
    "track_signup_conversion",
    "track_subscription_conversion",
    "track_payment_conversion",
    "track_funnel_step",
    "get_conversion_tracker",
    "set_conversion_tracker",
]
