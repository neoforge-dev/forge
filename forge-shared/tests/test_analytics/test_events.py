"""
Comprehensive tests for analytics events module.

Tests cover:
- Global client management
- Event tracking
- User identification
- Alias management
- Page view tracking
- Request-based tracking
- Edge cases
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import Request

from forge_shared.analytics.events import (
    get_posthog_client,
    set_posthog_client,
    track_event,
    identify,
    alias,
    track_page_view,
)


class TestGlobalClientManagement:
    """Tests for global PostHog client management."""

    def test_get_client_when_not_set(self):
        """Test getting client when not configured."""
        # Reset global client
        import forge_shared.analytics.events as events_module
        events_module._posthog_client = None
        
        result = get_posthog_client()
        
        assert result is None

    def test_set_and_get_client(self):
        """Test setting and getting global client."""
        mock_client = MagicMock()
        
        set_posthog_client(mock_client)
        result = get_posthog_client()
        
        assert result == mock_client

    def test_overwrite_client(self):
        """Test overwriting existing global client."""
        mock_client1 = MagicMock()
        mock_client2 = MagicMock()
        
        set_posthog_client(mock_client1)
        assert get_posthog_client() == mock_client1
        
        set_posthog_client(mock_client2)
        assert get_posthog_client() == mock_client2


class TestTrackEvent:
    """Tests for event tracking."""

    @pytest.mark.asyncio
    async def test_track_event_with_distinct_id(self):
        """Test tracking event with explicit distinct_id."""
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        await track_event(
            event="user_signup",
            properties={"plan": "pro"},
            distinct_id="user123"
        )
        
        mock_client.track.assert_called_once_with(
            event="user_signup",
            distinct_id="user123",
            properties={"plan": "pro"}
        )

    @pytest.mark.asyncio
    async def test_track_event_without_properties(self):
        """Test tracking event without properties."""
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        await track_event(
            event="page_view",
            distinct_id="user123"
        )
        
        mock_client.track.assert_called_once_with(
            event="page_view",
            distinct_id="user123",
            properties=None
        )

    @pytest.mark.asyncio
    async def test_track_event_with_request_no_user(self):
        """Test tracking event with request but no authenticated user."""
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        request = MagicMock(spec=Request)
        request.state = MagicMock()
        del request.state.user
        
        await track_event(
            event="api_call",
            request=request
        )
        
        mock_client.track.assert_called_once_with(
            event="api_call",
            distinct_id="anonymous",
            properties=None
        )

    @pytest.mark.asyncio
    async def test_track_event_with_request_and_user(self):
        """Test tracking event with request and authenticated user."""
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        request = MagicMock(spec=Request)
        request.state = MagicMock()
        request.state.user = MagicMock()
        request.state.user.id = "user456"
        
        await track_event(
            event="api_call",
            request=request
        )
        
        mock_client.track.assert_called_once_with(
            event="api_call",
            distinct_id="user456",
            properties=None
        )

    @pytest.mark.asyncio
    async def test_track_event_without_client(self):
        """Test tracking event when client is not configured."""
        import forge_shared.analytics.events as events_module
        events_module._posthog_client = None
        
        # Should not raise any errors
        await track_event(
            event="test_event",
            distinct_id="user123"
        )

    @pytest.mark.asyncio
    async def test_track_event_distinct_id_takes_precedence(self):
        """Test that explicit distinct_id takes precedence over request user."""
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        request = MagicMock(spec=Request)
        request.state = MagicMock()
        request.state.user = MagicMock()
        request.state.user.id = "user_from_request"
        
        await track_event(
            event="test_event",
            distinct_id="explicit_user",
            request=request
        )
        
        mock_client.track.assert_called_once_with(
            event="test_event",
            distinct_id="explicit_user",
            properties=None
        )


class TestIdentify:
    """Tests for user identification."""

    @pytest.mark.asyncio
    async def test_identify_user(self):
        """Test identifying a user."""
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        await identify(
            distinct_id="user123",
            properties={"email": "user@example.com", "name": "John"}
        )
        
        mock_client.identify.assert_called_once_with(
            distinct_id="user123",
            properties={"email": "user@example.com", "name": "John"}
        )

    @pytest.mark.asyncio
    async def test_identify_with_empty_properties(self):
        """Test identifying user with empty properties."""
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        await identify(
            distinct_id="user123",
            properties={}
        )
        
        mock_client.identify.assert_called_once()

    @pytest.mark.asyncio
    async def test_identify_without_client(self):
        """Test identifying when client is not configured."""
        import forge_shared.analytics.events as events_module
        events_module._posthog_client = None
        
        # Should not raise any errors
        await identify(
            distinct_id="user123",
            properties={"email": "user@example.com"}
        )


class TestAlias:
    """Tests for user alias management."""

    @pytest.mark.asyncio
    async def test_alias_user(self):
        """Test creating user alias."""
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        await alias(
            distinct_id="user123",
            alias="anonymous_456"
        )
        
        mock_client.alias.assert_called_once_with(
            distinct_id="user123",
            alias="anonymous_456"
        )

    @pytest.mark.asyncio
    async def test_alias_without_client(self):
        """Test alias creation when client is not configured."""
        import forge_shared.analytics.events as events_module
        events_module._posthog_client = None
        
        # Should not raise any errors
        await alias(
            distinct_id="user123",
            alias="anonymous_456"
        )


class TestTrackPageView:
    """Tests for page view tracking."""

    @pytest.mark.asyncio
    async def test_track_page_view_basic(self):
        """Test basic page view tracking."""
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/dashboard"
        request.method = "GET"
        request.headers = {"user-agent": "Mozilla/5.0"}
        request.client = MagicMock()
        request.client.host = "192.168.1.1"
        request.state = MagicMock()
        del request.state.user
        
        await track_page_view(request)
        
        # Verify track was called
        mock_client.track.assert_called_once()
        call_args = mock_client.track.call_args
        
        assert call_args[1]["event"] == "page_view"
        assert call_args[1]["properties"]["path"] == "/dashboard"
        assert call_args[1]["properties"]["method"] == "GET"
        assert call_args[1]["properties"]["user_agent"] == "Mozilla/5.0"
        assert call_args[1]["properties"]["ip"] == "192.168.1.1"

    @pytest.mark.asyncio
    async def test_track_page_view_with_user(self):
        """Test page view tracking with authenticated user."""
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/profile"
        request.method = "GET"
        request.headers = {}
        request.client = None
        request.state = MagicMock()
        request.state.user = MagicMock()
        request.state.user.id = "user789"
        
        await track_page_view(request)
        
        mock_client.track.assert_called_once()
        call_args = mock_client.track.call_args
        
        assert call_args[1]["distinct_id"] == "user789"

    @pytest.mark.asyncio
    async def test_track_page_view_with_additional_properties(self):
        """Test page view tracking with additional properties."""
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/products"
        request.method = "GET"
        request.headers = {}
        request.client = None
        request.state = MagicMock()
        del request.state.user
        
        await track_page_view(
            request,
            properties={"category": "electronics", "page": 2}
        )
        
        mock_client.track.assert_called_once()
        call_args = mock_client.track.call_args
        
        assert call_args[1]["properties"]["category"] == "electronics"
        assert call_args[1]["properties"]["page"] == 2
        assert call_args[1]["properties"]["path"] == "/products"

    @pytest.mark.asyncio
    async def test_track_page_view_without_client(self):
        """Test page view tracking when client is not configured."""
        import forge_shared.analytics.events as events_module
        events_module._posthog_client = None
        
        request = MagicMock(spec=Request)
        
        # Should not raise any errors
        await track_page_view(request)

    @pytest.mark.asyncio
    async def test_track_page_view_no_client_ip(self):
        """Test page view tracking when request has no client."""
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/test"
        request.method = "GET"
        request.headers = {}
        request.client = None
        request.state = MagicMock()
        del request.state.user
        
        await track_page_view(request)
        
        mock_client.track.assert_called_once()
        call_args = mock_client.track.call_args
        
        assert call_args[1]["properties"]["ip"] is None


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_multiple_concurrent_events(self):
        """Test tracking multiple events concurrently."""
        import asyncio
        
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        tasks = [
            track_event(f"event_{i}", distinct_id=f"user_{i}")
            for i in range(10)
        ]
        
        await asyncio.gather(*tasks)
        
        assert mock_client.track.call_count == 10

    @pytest.mark.asyncio
    async def test_large_event_properties(self):
        """Test tracking event with large properties dict."""
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        large_properties = {f"key_{i}": f"value_{i}" for i in range(100)}
        
        await track_event(
            event="large_event",
            distinct_id="user123",
            properties=large_properties
        )
        
        mock_client.track.assert_called_once()

    @pytest.mark.asyncio
    async def test_special_characters_in_event_name(self):
        """Test tracking event with special characters."""
        mock_client = AsyncMock()
        set_posthog_client(mock_client)
        
        await track_event(
            event="user_action/submit_form:v2",
            distinct_id="user123"
        )
        
        mock_client.track.assert_called_once()
