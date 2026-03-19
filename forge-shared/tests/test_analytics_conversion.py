"""
Tests for UTM conversion tracking module.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge_shared.analytics.conversion import (
    ConversionTracker,
    UTMParams,
    set_conversion_tracker,
    track_signup_conversion,
    track_subscription_conversion,
)


@pytest.fixture
def mock_posthog_client():
    """Create a mock PostHog client."""
    client = MagicMock()
    client.track = AsyncMock()
    client.identify = AsyncMock()
    return client


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    redis.rpush = AsyncMock()
    return redis


@pytest.fixture
def conversion_tracker(mock_posthog_client, mock_redis):
    """Create a ConversionTracker instance."""
    return ConversionTracker(
        posthog_client=mock_posthog_client,
        redis_client=mock_redis,
    )


class TestConversionTracker:
    """Tests for ConversionTracker class."""

    @pytest.mark.asyncio
    async def test_track_conversion_stores_attribution(self, conversion_tracker, mock_redis):
        """Test that track_conversion stores attribution in Redis."""
        utm = UTMParams(
            source="linkedin",
            medium="social",
            campaign="spring-launch",
        )

        # Mock Redis to return attribution
        mock_redis.get.return_value = json.dumps(
            {"utm_source": "linkedin", "utm_medium": "social", "utm_campaign": "spring-launch"}
        )

        result = await conversion_tracker.track_conversion(
            user_id="user123",
            event_type="signup",
            value_usd=0.0,
            utm=utm,
        )

        # Verify PostHog was called
        conversion_tracker.client.track.assert_called_once()

        call_args = conversion_tracker.client.track.call_args
        assert call_args.kwargs["event"] == "conversion_signup"
        assert call_args.kwargs["distinct_id"] == "user123"
        assert "utm_source" in call_args.kwargs["properties"]

    @pytest.mark.asyncio
    async def test_get_conversion_attribution_returns_stored(self, conversion_tracker, mock_redis):
        """Test that get_conversion_attribution returns stored attribution."""
        stored_attribution = {
            "utm_source": "twitter",
            "utm_medium": "social",
            "utm_campaign": "feb-promo",
            "attribution_model": "first_touch",
        }
        mock_redis.get.return_value = json.dumps(stored_attribution)

        result = await conversion_tracker.get_conversion_attribution("user123")

        assert result["utm_source"] == "twitter"
        assert result["attribution_model"] == "first_touch"

    @pytest.mark.asyncio
    async def test_get_conversion_attribution_new_user(self, conversion_tracker):
        """Test attribution for new user uses request UTM."""
        utm = UTMParams(
            source="newsletter",
            medium="email",
            campaign="weekly-digest",
        )

        result = await conversion_tracker.get_conversion_attribution("newuser", request_utm=utm)

        assert result["utm_source"] == "newsletter"
        assert result["attribution_model"] == "first_touch"

    @pytest.mark.asyncio
    async def test_track_stripe_conversion(self, conversion_tracker, mock_redis):
        """Test Stripe conversion tracking."""
        mock_redis.get.return_value = None  # No duplicate

        result = await conversion_tracker.track_stripe_conversion(
            user_id="user123",
            stripe_event_id="evt_123",
            event_type="payment_succeeded",
            amount_usd=29.00,
            plan_id="pro_monthly",
        )

        # Verify PostHog track was called
        conversion_tracker.client.track.assert_called()
        call_args = conversion_tracker.client.track.call_args
        assert call_args.kwargs["event"] == "stripe_conversion"

    @pytest.mark.asyncio
    async def test_track_funnel_step(self, conversion_tracker, mock_redis):
        """Test funnel step tracking."""
        mock_redis.get.return_value = None

        await conversion_tracker.track_funnel_step(
            user_id="user123",
            funnel_name="signup",
            step="landing_viewed",
        )

        conversion_tracker.client.track.assert_called_once()
        call_args = conversion_tracker.client.track.call_args
        assert call_args.kwargs["event"] == "funnel_signup_landing_viewed"

    @pytest.mark.asyncio
    async def test_store_first_touch(self, conversion_tracker, mock_redis):
        """Test first-touch attribution storage."""
        utm = UTMParams(
            source="google",
            medium="cpc",
            campaign="brand-search",
        )

        await conversion_tracker.store_first_touch("user123", utm)

        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert "attribution" in call_args[0][0] or "user123" in call_args[0][0]


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    @pytest.mark.asyncio
    async def test_track_signup_conversion(self, mock_posthog_client, mock_redis):
        """Test track_signup_conversion convenience function."""
        mock_redis.get.return_value = json.dumps({})

        tracker = ConversionTracker(mock_posthog_client, mock_redis)
        set_conversion_tracker(tracker)

        await track_signup_conversion(
            user_id="user456",
            properties={"signup_method": "email"},
        )

        mock_posthog_client.track.assert_called_once()

    @pytest.mark.asyncio
    async def test_track_subscription_conversion(self, mock_posthog_client, mock_redis):
        """Test track_subscription_conversion convenience function."""
        mock_redis.get.return_value = json.dumps({})

        tracker = ConversionTracker(mock_posthog_client, mock_redis)
        set_conversion_tracker(tracker)

        await track_subscription_conversion(
            user_id="user789",
            plan_id="pro_monthly",
            amount_usd=29.00,
        )

        mock_posthog_client.track.assert_called_once()


class TestDuplicateStripeEvent:
    """Tests for Stripe event deduplication."""

    @pytest.mark.asyncio
    async def test_duplicate_stripe_event_skipped(self, conversion_tracker, mock_redis):
        """Test that duplicate Stripe events are skipped."""
        # Mock Redis to return existing event (duplicate)
        mock_redis.get.return_value = json.dumps({"user_id": "user123", "amount_usd": 29.00})

        await conversion_tracker.track_stripe_conversion(
            user_id="user123",
            stripe_event_id="evt_duplicate",
            event_type="payment_succeeded",
            amount_usd=29.00,
        )

        # PostHog should NOT be called for duplicate
        conversion_tracker.client.track.assert_not_called()


class TestAttributionPriority:
    """Tests for attribution priority logic."""

    @pytest.mark.asyncio
    async def test_stored_attribution_prioritized(self, conversion_tracker, mock_redis):
        """Test that stored attribution is used over request UTM."""
        stored = {
            "utm_source": "google",
            "utm_medium": "cpc",
            "utm_campaign": "old-campaign",
            "attribution_model": "first_touch",
        }
        mock_redis.get.return_value = json.dumps(stored)

        # Request has new UTM
        new_utm = UTMParams(
            source="linkedin",
            medium="social",
            campaign="new-campaign",
        )

        result = await conversion_tracker.get_conversion_attribution("user123", request_utm=new_utm)

        # Should return stored attribution, not request UTM
        assert result["utm_source"] == "google"
        assert result["utm_campaign"] == "old-campaign"


class TestUTMParamsModel:
    """Tests for UTMParams model."""

    def test_is_empty_returns_true_for_empty(self):
        """Test is_empty returns True when all core params are None."""
        utm = UTMParams()
        assert utm.is_empty() is True

    def test_is_empty_returns_false_when_source_set(self):
        """Test is_empty returns False when source is set."""
        utm = UTMParams(source="google")
        assert utm.is_empty() is False

    def test_to_posthog_properties(self):
        """Test conversion to PostHog properties."""
        utm = UTMParams(
            source="linkedin",
            medium="social",
            campaign="spring-launch",
            content="cta-button",
        )
        props = utm.to_posthog_properties()

        assert props["utm_source"] == "linkedin"
        assert props["utm_medium"] == "social"
        assert props["utm_campaign"] == "spring-launch"
        assert props["utm_content"] == "cta-button"

    def test_to_dict_and_from_dict(self):
        """Test serialization round-trip."""
        utm = UTMParams(
            source="google",
            medium="cpc",
            campaign="brand-search",
        )
        utm_dict = utm.to_dict()

        restored = UTMParams.from_dict(utm_dict)

        assert restored.source == "google"
        assert restored.medium == "cpc"
        assert restored.campaign == "brand-search"
