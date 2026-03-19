"""
Comprehensive tests for UTM tracking functions.

Tests for get_utm_params, store_utm, and get_attribution functions
in forge_shared/utm/tracking.py
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request

from forge_shared.utm.tracking import (
    get_utm_params,
    store_utm,
    get_attribution,
    UTM_REDIS_PREFIX,
    UTM_REDIS_TTL,
)
from forge_shared.utm.models import UTMParams


class TestGetUTMParams:
    """Tests for get_utm_params function."""

    def test_get_utm_params_from_request_state(self):
        """Test getting UTM params from request.state."""
        request = MagicMock(spec=Request)
        request.state = MagicMock()

        utm = UTMParams(
            source="google",
            medium="cpc",
            campaign="spring_sale"
        )
        request.state.utm = utm

        result = get_utm_params(request)

        assert result.source == "google"
        assert result.medium == "cpc"
        assert result.campaign == "spring_sale"

    def test_get_utm_params_empty_when_not_set(self):
        """Test getting UTM params when none in request.state."""
        # Create request.state without utm attribute (it will call UTMParams())
        request = MagicMock(spec=Request)
        request.state = MagicMock(spec=[])
        # Delete utm to simulate it not existing
        if hasattr(request.state, 'utm'):
            delattr(request.state, 'utm')

        result = get_utm_params(request)
        assert isinstance(result, UTMParams)


class TestStoreUTM:
    """Tests for store_utm function."""

    @pytest.mark.asyncio
    async def test_store_utm_with_valid_params(self):
        """Test storing valid UTM parameters."""
        utm = UTMParams(
            source="linkedin",
            medium="social",
            campaign="b2b_campaign",
            content="post_1",
            term="enterprise"
        )

        redis_client = AsyncMock()
        redis_client.setex = AsyncMock()

        await store_utm("user_123", utm, redis_client)

        # Verify the call was made with correct key and TTL
        call_args = redis_client.setex.call_args[0]
        assert call_args[0] == f"{UTM_REDIS_PREFIX}user_123"
        assert call_args[1] == UTM_REDIS_TTL

        # Verify stored data contains expected values
        stored_data = json.loads(call_args[2])
        assert stored_data["source"] == "linkedin"
        assert stored_data["medium"] == "social"
        assert stored_data["campaign"] == "b2b_campaign"

    @pytest.mark.asyncio
    async def test_store_utm_skips_empty_params(self):
        """Test that empty UTM params are not stored."""
        utm = UTMParams()  # Empty UTM params

        redis_client = AsyncMock()
        redis_client.setex = AsyncMock()

        await store_utm("user_123", utm, redis_client)

        # Should not call setex for empty UTM
        redis_client.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_store_utm_without_redis_client(self):
        """Test storing UTM without Redis client (logs warning)."""
        utm = UTMParams(source="google", medium="cpc")

        # Should not raise exception
        await store_utm("user_123", utm, redis_client=None)

    @pytest.mark.asyncio
    async def test_store_utm_handles_redis_error(self):
        """Test handling Redis storage errors."""
        utm = UTMParams(source="twitter", medium="social")

        redis_client = AsyncMock()
        redis_client.setex = AsyncMock(side_effect=Exception("Redis connection failed"))

        # Should not raise exception
        await store_utm("user_456", utm, redis_client)

    @pytest.mark.asyncio
    async def test_store_utm_uses_correct_ttl(self):
        """Test that correct TTL is used for Redis storage."""
        utm = UTMParams(source="facebook", medium="social")

        redis_client = AsyncMock()
        redis_client.setex = AsyncMock()

        await store_utm("user_ttl_test", utm, redis_client)

        # Check TTL value (90 days in seconds)
        expected_ttl = 90 * 24 * 60 * 60  # 7,776,000 seconds
        call_args = redis_client.setex.call_args[0]

        assert call_args[1] == expected_ttl

    @pytest.mark.asyncio
    async def test_store_utm_serializes_correctly(self):
        """Test UTM params are serialized correctly."""
        utm = UTMParams(
            source="organic",
            medium="search",
            campaign="brand",
            content="homepage",
            term="free trial"
        )

        redis_client = AsyncMock()
        redis_client.setex = AsyncMock()

        await store_utm("user_serial", utm, redis_client)

        call_args = redis_client.setex.call_args[0]
        stored_data = json.loads(call_args[2])

        assert stored_data["source"] == "organic"
        assert stored_data["medium"] == "search"
        assert stored_data["campaign"] == "brand"
        assert stored_data["content"] == "homepage"
        assert stored_data["term"] == "free trial"


class TestGetAttribution:
    """Tests for get_attribution function."""

    @pytest.mark.asyncio
    async def test_get_attribution_found(self):
        """Test retrieving existing UTM attribution."""
        # Use the actual to_dict format (source not utm_source)
        utm_data = json.dumps({
            "source": "google",
            "medium": "cpc",
            "campaign": "summer_sale",
            "content": None,
            "term": None,
            "landing_page": None,
            "referrer": None,
            "timestamp": None
        })

        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value=utm_data)

        result = await get_attribution("user_123", redis_client)

        assert isinstance(result, UTMParams)
        assert result.source == "google"
        assert result.medium == "cpc"
        assert result.campaign == "summer_sale"

    @pytest.mark.asyncio
    async def test_get_attribution_not_found(self):
        """Test retrieving attribution when none exists."""
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value=None)

        result = await get_attribution("user_new", redis_client)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_attribution_without_redis_client(self):
        """Test getting attribution without Redis client."""
        result = await get_attribution("user_no_redis", redis_client=None)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_attribution_handles_redis_error(self):
        """Test handling Redis retrieval errors."""
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(side_effect=Exception("Redis error"))

        result = await get_attribution("user_error", redis_client)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_attribution_with_all_params(self):
        """Test retrieving UTM with all parameters set."""
        utm_data = json.dumps({
            "source": "twitter",
            "medium": "social",
            "campaign": "launch",
            "content": "video",
            "term": "tutorial",
            "landing_page": None,
            "referrer": None,
            "timestamp": None
        })

        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value=utm_data)

        result = await get_attribution("user_full", redis_client)

        assert result.source == "twitter"
        assert result.medium == "social"
        assert result.campaign == "launch"
        assert result.content == "video"
        assert result.term == "tutorial"

    @pytest.mark.asyncio
    async def test_get_attribution_uses_correct_key_format(self):
        """Test that correct Redis key format is used."""
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value=None)

        await get_attribution("user_key_test", redis_client)

        expected_key = f"{UTM_REDIS_PREFIX}user_key_test"
        redis_client.get.assert_called_once_with(expected_key)

    @pytest.mark.asyncio
    async def test_get_attribution_handles_invalid_json(self):
        """Test handling invalid JSON in Redis."""
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value="invalid json")

        result = await get_attribution("user_bad_json", redis_client)

        assert result is None


class TestUTMConstants:
    """Tests for UTM tracking constants."""

    def test_redis_prefix_constant(self):
        """Test UTM_REDIS_PREFIX is correctly set."""
        assert UTM_REDIS_PREFIX == "utm:attribution:"

    def test_redis_ttl_constant(self):
        """Test UTM_REDIS_TTL is set to 90 days."""
        expected_ttl = 90 * 24 * 60 * 60  # 7,776,000 seconds
        assert UTM_REDIS_TTL == expected_ttl


class TestEndToEndFlow:
    """Tests for complete UTM tracking workflows."""

    @pytest.mark.asyncio
    async def test_store_and_retrieve_attribution(self):
        """Test storing and then retrieving UTM attribution."""
        utm = UTMParams(
            source="instagram",
            medium="social",
            campaign="influencer_1"
        )

        redis_client = AsyncMock()
        redis_client.setex = AsyncMock()

        # Store UTM
        await store_utm("user_flow", utm, redis_client)

        # Mock retrieval
        utm_data = json.dumps({
            "source": "instagram",
            "medium": "social",
            "campaign": "influencer_1",
            "content": None,
            "term": None,
            "landing_page": None,
            "referrer": None,
            "timestamp": None
        })
        redis_client.get = AsyncMock(return_value=utm_data)

        # Retrieve UTM
        result = await get_attribution("user_flow", redis_client)

        assert result.source == "instagram"
        assert result.medium == "social"
        assert result.campaign == "influencer_1"

    @pytest.mark.asyncio
    async def test_multiple_users_different_attribution(self):
        """Test storing different attribution for multiple users."""
        redis_client = AsyncMock()
        redis_client.setex = AsyncMock()

        utm1 = UTMParams(source="google", medium="cpc")
        utm2 = UTMParams(source="linkedin", medium="social")
        utm3 = UTMParams(source="direct", medium="none")

        # Store for different users
        await store_utm("user1", utm1, redis_client)
        await store_utm("user2", utm2, redis_client)
        await store_utm("user3", utm3, redis_client)

        # Verify all were stored
        assert redis_client.setex.call_count == 3


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_get_attribution_empty_user_id(self):
        """Test getting attribution with empty user_id."""
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value=None)

        result = await get_attribution("", redis_client)

        assert result is None

    @pytest.mark.asyncio
    async def test_store_and_get_same_user_id(self):
        """Test storing and getting for same user ID."""
        utm = UTMParams(source="referral", medium="affiliate")

        redis_client = AsyncMock()
        redis_client.setex = AsyncMock()

        # Store
        await store_utm("user_same", utm, redis_client)

        # Mock get to return stored data
        utm_data = json.dumps({
            "source": "referral",
            "medium": "affiliate",
            "campaign": None,
            "content": None,
            "term": None,
            "landing_page": None,
            "referrer": None,
            "timestamp": None
        })
        redis_client.get = AsyncMock(return_value=utm_data)

        # Get
        result = await get_attribution("user_same", redis_client)

        assert result.source == "referral"
        assert result.medium == "affiliate"


class TestUTMParamsModel:
    """Tests for UTMParams model."""

    def test_utm_params_creation(self):
        """Test creating UTMParams instance."""
        utm = UTMParams(
            source="google",
            medium="cpc",
            campaign="spring_sale"
        )

        assert utm.source == "google"
        assert utm.medium == "cpc"
        assert utm.campaign == "spring_sale"

    def test_utm_params_empty(self):
        """Test empty UTMParams."""
        utm = UTMParams()

        assert utm.source is None
        assert utm.medium is None
        assert utm.campaign is None

    def test_utm_params_is_empty_true(self):
        """Test is_empty returns True when all None."""
        utm = UTMParams()
        assert utm.is_empty() is True

    def test_utm_params_is_empty_false(self):
        """Test is_empty returns False when has value."""
        utm = UTMParams(source="google")
        assert utm.is_empty() is False

    def test_utm_params_to_dict(self):
        """Test to_dict method."""
        utm = UTMParams(
            source="linkedin",
            medium="social",
            campaign="test"
        )

        data = utm.to_dict()

        assert data["source"] == "linkedin"
        assert data["medium"] == "social"
        assert data["campaign"] == "test"

    def test_utm_params_from_dict(self):
        """Test from_dict class method."""
        data = {
            "source": "twitter",
            "medium": "social",
            "campaign": "launch"
        }

        utm = UTMParams.from_dict(data)

        assert utm.source == "twitter"
        assert utm.medium == "social"
        assert utm.campaign == "launch"

    def test_utm_params_to_posthog_properties(self):
        """Test to_posthog_properties method."""
        utm = UTMParams(
            source="facebook",
            medium="social",
            campaign="v1",
            referrer="https://example.com"
        )

        props = utm.to_posthog_properties()

        assert props["utm_source"] == "facebook"
        assert props["utm_medium"] == "social"
        assert props["utm_campaign"] == "v1"
        assert props["$referrer"] == "https://example.com"
