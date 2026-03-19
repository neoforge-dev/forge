"""
PostHog analytics client.

Provides a Python client for PostHog analytics with support for
event tracking, user identification, and group analytics.

Example:
    ```python
    client = PostHogClient(
        api_key="your-api-key",
        host="https://app.posthog.com"
    )

    # Track event
    await client.track("user_signup", distinct_id="user123", properties={"plan": "pro"})

    # Identify user
    await client.identify(distinct_id="user123", properties={"email": "user@example.com"})
    ```
"""

import asyncio
from typing import Any, Optional
from posthog import Posthog as PostHogBase
from pydantic import BaseModel


class PostHogClient(BaseModel):
    """
    PostHog analytics client.

    Attributes:
        api_key: PostHog API key
        host: PostHog host URL
        enabled: Whether analytics is enabled
        flush_interval: Seconds between automatic flushes
        flush_at: Number of events to trigger automatic flush

    Example:
        ```python
        client = PostHogClient(
            api_key="your-api-key",
            host="https://app.posthog.com"
        )
        ```
    """

    api_key: str
    host: str = "https://app.posthog.com"
    enabled: bool = True
    flush_interval: int = 30
    flush_at: int = 20
    _client: Optional[PostHogBase] = None

    class Config:
        arbitrary_types_allowed = True

    def model_post_init(self, __context: Any) -> None:
        """Initialize PostHog client after model creation."""
        if self.enabled:
            self._client = PostHogBase(
                self.api_key,
                host=self.host,
                flush_interval=self.flush_interval,
                flush_at=self.flush_at,
            )

    async def track(
        self,
        event: str,
        distinct_id: str,
        properties: Optional[dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        """
        Track an analytics event.

        Args:
            event: Event name
            distinct_id: User distinct ID
            properties: Event properties
            timestamp: Optional event timestamp
        """
        if not self.enabled or self._client is None:
            return

        def _track() -> None:
            assert self._client is not None
            self._client.capture(
                distinct_id=distinct_id,
                event=event,
                properties=properties or {},
                timestamp=timestamp,
            )

        # Run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _track)

    async def identify(
        self,
        distinct_id: str,
        properties: dict[str, Any],
    ) -> None:
        """
        Identify a user with properties.

        Args:
            distinct_id: User distinct ID
            properties: User properties
        """
        if not self.enabled or self._client is None:
            return

        def _identify() -> None:
            assert self._client is not None
            self._client.identify(
                distinct_id=distinct_id,
                properties=properties,
            )

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _identify)

    async def alias(
        self,
        distinct_id: str,
        alias: str,
    ) -> None:
        """
        Create an alias for a user ID.

        Args:
            distinct_id: User distinct ID
            alias: Alias to create
        """
        if not self.enabled or self._client is None:
            return

        def _alias() -> None:
            assert self._client is not None
            self._client.alias(
                distinct_id=distinct_id,
                alias=alias,
            )

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _alias)

    async def flush(self) -> None:
        """Flush all queued events."""
        if not self.enabled or self._client is None:
            return

        def _flush() -> None:
            assert self._client is not None
            self._client.flush()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _flush)

    def shutdown(self) -> None:
        """Shutdown the PostHog client and flush remaining events."""
        if self._client is not None:
            self._client.shutdown()
