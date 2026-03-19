"""
Tests for analytics module.

Test coverage for PostHog client, event tracking, and analytics middleware.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from forge_shared.analytics import PostHogClient, track_event, identify, alias
from forge_shared.analytics.events import set_posthog_client


class TestPostHogClient:
    """
    Tests for PostHog client.

    Tests client initialization, event tracking, user identification, and
    alias creation.
    """

    def test_client_creation(self):
        """
        Test PostHog client creation.
        """
        client = PostHogClient(
            api_key="test-key",
            host="https://test.posthog.com",
            enabled=False,
        )

        assert client.api_key == "test-key"
        assert client.host == "https://test.posthog.com"
        assert client.enabled is False

    def test_client_disabled(self):
        """
        Test that disabled client doesn't track events.
        """
        client = PostHogClient(api_key="test-key", enabled=False)

        # Should not raise exception
        import asyncio

        asyncio.run(client.track("test_event", distinct_id="user123"))

    @pytest.mark.asyncio
    async def test_track_event(self, posthog_client: PostHogClient):
        """
        Test tracking an event.

        Args:
            posthog_client: PostHog client fixture
        """
        # Should not raise exception (client is disabled)
        await posthog_client.track(
            event="test_event",
            distinct_id="user123",
            properties={"key": "value"},
        )

    @pytest.mark.asyncio
    async def test_identify_user(self, posthog_client: PostHogClient):
        """
        Test identifying a user.

        Args:
            posthog_client: PostHog client fixture
        """
        await posthog_client.identify(
            distinct_id="user123",
            properties={"email": "test@example.com"},
        )

    @pytest.mark.asyncio
    async def test_create_alias(self, posthog_client: PostHogClient):
        """
        Test creating an alias.

        Args:
            posthog_client: PostHog client fixture
        """
        await posthog_client.alias(distinct_id="user123", alias="anonymous123")


class TestEventTracking:
    """
    Tests for event tracking utilities.

    Tests event tracking, user identification, and alias creation with
    global client.
    """

    def test_set_posthog_client(self, posthog_client: PostHogClient):
        """
        Test setting global PostHog client.

        Args:
            posthog_client: PostHog client fixture
        """
        set_posthog_client(posthog_client)
        # Should not raise exception

    @pytest.mark.asyncio
    async def test_track_event_no_client(self):
        """
        Test tracking event without client configured.
        """
        # Clear any existing client
        set_posthog_client(None)

        # Should not raise exception
        await track_event("test_event", {"key": "value"})

    @pytest.mark.asyncio
    async def test_identify_no_client(self):
        """
        Test identifying user without client configured.
        """
        set_posthog_client(None)

        # Should not raise exception
        await identify("user123", {"email": "test@example.com"})

    @pytest.mark.asyncio
    async def test_alias_no_client(self):
        """
        Test creating alias without client configured.
        """
        set_posthog_client(None)

        # Should not raise exception
        await alias("user123", "anonymous123")

    @pytest.mark.asyncio
    async def test_track_event_with_request(self, posthog_client: PostHogClient):
        """
        Test tracking event with request context.

        Args:
            posthog_client: PostHog client fixture
        """
        set_posthog_client(posthog_client)

        # Mock request with user
        request = MagicMock()
        request.state.user = MagicMock()
        request.state.user.id = "user123"

        await track_event("test_event", {"key": "value"}, request=request)

    @pytest.mark.asyncio
    async def test_track_event_anonymous(self, posthog_client: PostHogClient):
        """
        Test tracking event without user context.

        Args:
            posthog_client: PostHog client fixture
        """
        set_posthog_client(posthog_client)

        # Mock request without user
        request = MagicMock()
        request.state.user = None

        await track_event("test_event", {"key": "value"}, request=request)
