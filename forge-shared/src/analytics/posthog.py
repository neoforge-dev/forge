"""
Service Specification: Analytics (PostHog)

This file defines the interface for the unified FORGE PostHog analytics service.
Implementation: forge_shared.analytics.posthog
"""

from typing import Optional, Dict, Any

class PostHogClientInterface:
    """Unified PostHog analytics interface."""
    
    def __init__(self, api_key: str, host: str = "https://app.posthog.com", **kwargs):
        """Initialize PostHog client."""
        pass

    async def track(self, event: str, distinct_id: str, properties: Optional[Dict[str, Any]] = None):
        """Capture a custom event."""
        pass

    async def identify(self, distinct_id: str, properties: Dict[str, Any]):
        """Identify a user with specific traits."""
        pass

    async def alias(self, distinct_id: str, alias: str):
        """Connect two different IDs for the same user."""
        pass

    async def flush(self):
        """Manually flush the event queue."""
        pass
