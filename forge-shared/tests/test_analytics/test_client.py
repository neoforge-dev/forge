"""
Comprehensive tests for PostHog analytics client.

Tests cover:
- Client initialization and configuration
- Event tracking
- User identification
- Alias management
- Flush and shutdown operations
- Async operations
- Error handling
- Edge cases
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio

from forge_shared.analytics.posthog import PostHogClient


class TestPostHogClientInitialization:
    """Tests for PostHogClient initialization."""

    def test_initialization_with_required_params(self):
        """Test client initialization with required parameters."""
        client = PostHogClient(api_key="test-key")
        
        assert client.api_key == "test-key"
        assert client.host == "https://app.posthog.com"
        assert client.enabled is True
        assert client.flush_interval == 30
        assert client.flush_at == 20
        assert client._client is not None

    def test_initialization_with_custom_params(self):
        """Test client initialization with custom parameters."""
        client = PostHogClient(
            api_key="test-key",
            host="https://custom.posthog.com",
            enabled=False,
            flush_interval=60,
            flush_at=50
        )
        
        assert client.api_key == "test-key"
        assert client.host == "https://custom.posthog.com"
        assert client.enabled is False
        assert client.flush_interval == 60
        assert client.flush_at == 50
        assert client._client is None  # No client when disabled

    def test_initialization_disabled_client(self):
        """Test that disabled client doesn't create PostHog instance."""
        client = PostHogClient(api_key="test-key", enabled=False)
        
        assert client.enabled is False
        assert client._client is None

    def test_initialization_enabled_client_creates_instance(self):
        """Test that enabled client creates PostHog instance."""
        with patch('forge_shared.analytics.posthog.PostHogBase') as mock_posthog:
            client = PostHogClient(api_key="test-key", enabled=True)
            
            mock_posthog.assert_called_once_with(
                "test-key",
                host="https://app.posthog.com",
                flush_interval=30,
                flush_at=20
            )


class TestPostHogClientTrack:
    """Tests for event tracking."""

    @pytest.mark.asyncio
    async def test_track_event_success(self):
        """Test successful event tracking."""
        with patch('forge_shared.analytics.posthog.PostHogBase') as mock_posthog:
            mock_instance = MagicMock()
            mock_posthog.return_value = mock_instance
            
            client = PostHogClient(api_key="test-key")
            
            await client.track(
                event="user_signup",
                distinct_id="user123",
                properties={"plan": "pro"}
            )
            
            # Give time for async executor
            await asyncio.sleep(0.1)
            
            mock_instance.capture.assert_called_once()

    @pytest.mark.asyncio
    async def test_track_event_without_properties(self):
        """Test event tracking without properties."""
        with patch('forge_shared.analytics.posthog.PostHogBase') as mock_posthog:
            mock_instance = MagicMock()
            mock_posthog.return_value = mock_instance
            
            client = PostHogClient(api_key="test-key")
            
            await client.track(
                event="page_view",
                distinct_id="user123"
            )
            
            await asyncio.sleep(0.1)
            
            mock_instance.capture.assert_called_once()

    @pytest.mark.asyncio
    async def test_track_event_with_timestamp(self):
        """Test event tracking with custom timestamp."""
        with patch('forge_shared.analytics.posthog.PostHogBase') as mock_posthog:
            mock_instance = MagicMock()
            mock_posthog.return_value = mock_instance
            
            client = PostHogClient(api_key="test-key")
            
            await client.track(
                event="custom_event",
                distinct_id="user123",
                properties={"key": "value"},
                timestamp=1234567890.0
            )
            
            await asyncio.sleep(0.1)
            
            mock_instance.capture.assert_called_once()

    @pytest.mark.asyncio
    async def test_track_event_disabled_client(self):
        """Test that tracking is skipped when client is disabled."""
        client = PostHogClient(api_key="test-key", enabled=False)
        
        # Should not raise any errors
        await client.track(
            event="test_event",
            distinct_id="user123"
        )

    @pytest.mark.asyncio
    async def test_track_event_no_client_instance(self):
        """Test tracking when client instance is None."""
        client = PostHogClient(api_key="test-key", enabled=False)
        client._client = None
        
        # Should not raise any errors
        await client.track(
            event="test_event",
            distinct_id="user123"
        )


class TestPostHogClientIdentify:
    """Tests for user identification."""

    @pytest.mark.asyncio
    async def test_identify_user_success(self):
        """Test successful user identification."""
        with patch('forge_shared.analytics.posthog.PostHogBase') as mock_posthog:
            mock_instance = MagicMock()
            mock_posthog.return_value = mock_instance
            
            client = PostHogClient(api_key="test-key")
            
            await client.identify(
                distinct_id="user123",
                properties={"email": "user@example.com", "name": "John"}
            )
            
            await asyncio.sleep(0.1)
            
            mock_instance.identify.assert_called_once()

    @pytest.mark.asyncio
    async def test_identify_user_with_empty_properties(self):
        """Test user identification with empty properties."""
        with patch('forge_shared.analytics.posthog.PostHogBase') as mock_posthog:
            mock_instance = MagicMock()
            mock_posthog.return_value = mock_instance
            
            client = PostHogClient(api_key="test-key")
            
            await client.identify(
                distinct_id="user123",
                properties={}
            )
            
            await asyncio.sleep(0.1)
            
            mock_instance.identify.assert_called_once()

    @pytest.mark.asyncio
    async def test_identify_disabled_client(self):
        """Test that identification is skipped when client is disabled."""
        client = PostHogClient(api_key="test-key", enabled=False)
        
        # Should not raise any errors
        await client.identify(
            distinct_id="user123",
            properties={"email": "user@example.com"}
        )


class TestPostHogClientAlias:
    """Tests for user alias management."""

    @pytest.mark.asyncio
    async def test_alias_user_success(self):
        """Test successful user alias creation."""
        with patch('forge_shared.analytics.posthog.PostHogBase') as mock_posthog:
            mock_instance = MagicMock()
            mock_posthog.return_value = mock_instance
            
            client = PostHogClient(api_key="test-key")
            
            await client.alias(
                distinct_id="user123",
                alias="anonymous_456"
            )
            
            await asyncio.sleep(0.1)
            
            mock_instance.alias.assert_called_once()

    @pytest.mark.asyncio
    async def test_alias_disabled_client(self):
        """Test that alias creation is skipped when client is disabled."""
        client = PostHogClient(api_key="test-key", enabled=False)
        
        # Should not raise any errors
        await client.alias(
            distinct_id="user123",
            alias="anonymous_456"
        )


class TestPostHogClientFlush:
    """Tests for event flushing."""

    @pytest.mark.asyncio
    async def test_flush_success(self):
        """Test successful event flush."""
        with patch('forge_shared.analytics.posthog.PostHogBase') as mock_posthog:
            mock_instance = MagicMock()
            mock_posthog.return_value = mock_instance
            
            client = PostHogClient(api_key="test-key")
            
            await client.flush()
            
            await asyncio.sleep(0.1)
            
            mock_instance.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_flush_disabled_client(self):
        """Test that flush is skipped when client is disabled."""
        client = PostHogClient(api_key="test-key", enabled=False)
        
        # Should not raise any errors
        await client.flush()


class TestPostHogClientShutdown:
    """Tests for client shutdown."""

    def test_shutdown_success(self):
        """Test successful client shutdown."""
        with patch('forge_shared.analytics.posthog.PostHogBase') as mock_posthog:
            mock_instance = MagicMock()
            mock_posthog.return_value = mock_instance
            
            client = PostHogClient(api_key="test-key")
            client.shutdown()
            
            mock_instance.shutdown.assert_called_once()

    def test_shutdown_disabled_client(self):
        """Test shutdown when client is disabled."""
        client = PostHogClient(api_key="test-key", enabled=False)
        
        # Should not raise any errors
        client.shutdown()

    def test_shutdown_no_client_instance(self):
        """Test shutdown when client instance is None."""
        client = PostHogClient(api_key="test-key", enabled=False)
        client._client = None
        
        # Should not raise any errors
        client.shutdown()


class TestPostHogClientEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_multiple_concurrent_tracks(self):
        """Test multiple concurrent event tracking."""
        with patch('forge_shared.analytics.posthog.PostHogBase') as mock_posthog:
            mock_instance = MagicMock()
            mock_posthog.return_value = mock_instance
            
            client = PostHogClient(api_key="test-key")
            
            # Track multiple events concurrently
            tasks = [
                client.track(f"event_{i}", f"user_{i}")
                for i in range(10)
            ]
            
            await asyncio.gather(*tasks)
            await asyncio.sleep(0.2)
            
            # Should have tracked all events
            assert mock_instance.capture.call_count == 10

    @pytest.mark.asyncio
    async def test_track_with_large_properties(self):
        """Test tracking with large properties dict."""
        with patch('forge_shared.analytics.posthog.PostHogBase') as mock_posthog:
            mock_instance = MagicMock()
            mock_posthog.return_value = mock_instance
            
            client = PostHogClient(api_key="test-key")
            
            large_properties = {f"key_{i}": f"value_{i}" for i in range(100)}
            
            await client.track(
                event="test_event",
                distinct_id="user123",
                properties=large_properties
            )
            
            await asyncio.sleep(0.1)
            
            mock_instance.capture.assert_called_once()

    def test_model_serialization(self):
        """Test client can be serialized to dict."""
        client = PostHogClient(
            api_key="test-key",
            host="https://custom.posthog.com",
            enabled=True
        )
        
        data = client.model_dump()
        
        assert data["api_key"] == "test-key"
        assert data["host"] == "https://custom.posthog.com"
        assert data["enabled"] is True

    def test_model_json_serialization(self):
        """Test client can be serialized to JSON."""
        client = PostHogClient(api_key="test-key")
        
        json_str = client.model_dump_json()
        
        assert "test-key" in json_str
        assert "https://app.posthog.com" in json_str
