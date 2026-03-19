"""
Tests for UTM models.

Test coverage for UTMParams and AttributionEvent models.
"""

from datetime import datetime

from forge_shared.utm.models import AttributionEvent, UTMParams


class TestUTMParams:
    """
    Tests for UTMParams model.

    Tests parameter validation, conversion methods, and utility functions.
    """

    def test_create_empty_utm_params(self):
        """Test creating empty UTMParams."""
        params = UTMParams()

        assert params.source is None
        assert params.medium is None
        assert params.campaign is None
        assert params.content is None
        assert params.term is None
        assert params.landing_page is None
        assert params.referrer is None
        assert params.timestamp is None

    def test_create_utm_params_with_core_fields(self):
        """Test creating UTMParams with core fields."""
        params = UTMParams(
            source="google",
            medium="cpc",
            campaign="spring_sale",
        )

        assert params.source == "google"
        assert params.medium == "cpc"
        assert params.campaign == "spring_sale"

    def test_create_utm_params_with_all_fields(self):
        """Test creating UTMParams with all fields."""
        timestamp = datetime.utcnow()
        params = UTMParams(
            source="google",
            medium="cpc",
            campaign="spring_sale",
            content="ad_variant_a",
            term="saas tools",
            landing_page="https://example.com/landing",
            referrer="https://google.com",
            timestamp=timestamp,
        )

        assert params.source == "google"
        assert params.medium == "cpc"
        assert params.campaign == "spring_sale"
        assert params.content == "ad_variant_a"
        assert params.term == "saas tools"
        assert params.landing_page == "https://example.com/landing"
        assert params.referrer == "https://google.com"
        assert params.timestamp == timestamp

    def test_is_empty_with_empty_params(self):
        """Test is_empty returns True for empty params."""
        params = UTMParams()
        assert params.is_empty() is True

    def test_is_empty_with_source_only(self):
        """Test is_empty returns False when source is set."""
        params = UTMParams(source="google")
        assert params.is_empty() is False

    def test_is_empty_with_medium_only(self):
        """Test is_empty returns False when medium is set."""
        params = UTMParams(medium="cpc")
        assert params.is_empty() is False

    def test_is_empty_with_campaign_only(self):
        """Test is_empty returns False when campaign is set."""
        params = UTMParams(campaign="spring_sale")
        assert params.is_empty() is False

    def test_is_empty_ignores_non_core_fields(self):
        """Test is_empty ignores landing_page, referrer, timestamp."""
        params = UTMParams(
            landing_page="https://example.com",
            referrer="https://google.com",
            timestamp=datetime.utcnow(),
        )
        assert params.is_empty() is True

    def test_to_posthog_properties(self):
        """Test conversion to PostHog properties."""
        params = UTMParams(
            source="google",
            medium="cpc",
            campaign="spring_sale",
            content="ad_variant_a",
            term="saas tools",
            referrer="https://google.com",
        )

        props = params.to_posthog_properties()

        assert props["utm_source"] == "google"
        assert props["utm_medium"] == "cpc"
        assert props["utm_campaign"] == "spring_sale"
        assert props["utm_content"] == "ad_variant_a"
        assert props["utm_term"] == "saas tools"
        assert props["$referrer"] == "https://google.com"

    def test_to_posthog_properties_with_none_values(self):
        """Test PostHog properties handles None values."""
        params = UTMParams(source="google")

        props = params.to_posthog_properties()

        assert props["utm_source"] == "google"
        assert props["utm_medium"] is None
        assert props["utm_campaign"] is None
        assert props["utm_content"] is None
        assert props["utm_term"] is None
        assert props["$referrer"] is None

    def test_to_dict(self):
        """Test conversion to dictionary."""
        timestamp = datetime(2024, 1, 15, 10, 30, 0)
        params = UTMParams(
            source="google",
            medium="cpc",
            campaign="spring_sale",
            content="ad_variant_a",
            term="saas tools",
            landing_page="https://example.com/landing",
            referrer="https://google.com",
            timestamp=timestamp,
        )

        data = params.to_dict()

        assert data["source"] == "google"
        assert data["medium"] == "cpc"
        assert data["campaign"] == "spring_sale"
        assert data["content"] == "ad_variant_a"
        assert data["term"] == "saas tools"
        assert data["landing_page"] == "https://example.com/landing"
        assert data["referrer"] == "https://google.com"
        assert data["timestamp"] == "2024-01-15T10:30:00"

    def test_to_dict_with_none_timestamp(self):
        """Test to_dict with None timestamp."""
        params = UTMParams(source="google")

        data = params.to_dict()

        assert data["source"] == "google"
        assert data["timestamp"] is None

    def test_from_dict(self):
        """Test creating UTMParams from dictionary."""
        data = {
            "source": "google",
            "medium": "cpc",
            "campaign": "spring_sale",
            "content": "ad_variant_a",
            "term": "saas tools",
            "landing_page": "https://example.com/landing",
            "referrer": "https://google.com",
            "timestamp": "2024-01-15T10:30:00",
        }

        params = UTMParams.from_dict(data)

        assert params.source == "google"
        assert params.medium == "cpc"
        assert params.campaign == "spring_sale"
        assert params.content == "ad_variant_a"
        assert params.term == "saas tools"
        assert params.landing_page == "https://example.com/landing"
        assert params.referrer == "https://google.com"
        assert params.timestamp == datetime(2024, 1, 15, 10, 30, 0)

    def test_from_dict_with_datetime_object(self):
        """Test from_dict with datetime object instead of string."""
        timestamp = datetime.utcnow()
        data = {
            "source": "google",
            "timestamp": timestamp,
        }

        params = UTMParams.from_dict(data)

        assert params.source == "google"
        assert params.timestamp == timestamp

    def test_from_dict_partial(self):
        """Test from_dict with partial data."""
        data = {"source": "google"}

        params = UTMParams.from_dict(data)

        assert params.source == "google"
        assert params.medium is None
        assert params.campaign is None

    def test_round_trip_conversion(self):
        """Test converting to dict and back preserves data."""
        timestamp = datetime(2024, 1, 15, 10, 30, 0)
        original = UTMParams(
            source="google",
            medium="cpc",
            campaign="spring_sale",
            content="ad_variant_a",
            term="saas tools",
            landing_page="https://example.com/landing",
            referrer="https://google.com",
            timestamp=timestamp,
        )

        data = original.to_dict()
        restored = UTMParams.from_dict(data)

        assert restored.source == original.source
        assert restored.medium == original.medium
        assert restored.campaign == original.campaign
        assert restored.content == original.content
        assert restored.term == original.term
        assert restored.landing_page == original.landing_page
        assert restored.referrer == original.referrer
        assert restored.timestamp == original.timestamp


class TestAttributionEvent:
    """
    Tests for AttributionEvent model.

    Tests attribution event creation and validation.
    """

    def test_create_attribution_event(self):
        """Test creating an attribution event."""
        timestamp = datetime.utcnow()
        utm = UTMParams(source="google", medium="cpc", campaign="spring_sale")

        event = AttributionEvent(
            user_id="user123",
            product="interview-simulator",
            event_type="subscription",
            value_usd=49.99,
            utm=utm,
            timestamp=timestamp,
        )

        assert event.user_id == "user123"
        assert event.product == "interview-simulator"
        assert event.event_type == "subscription"
        assert event.value_usd == 49.99
        assert event.utm == utm
        assert event.timestamp == timestamp

    def test_attribution_event_default_value(self):
        """Test attribution event with default value."""
        timestamp = datetime.utcnow()
        utm = UTMParams(source="google")

        event = AttributionEvent(
            user_id="user123",
            product="voice-coach",
            event_type="signup",
            utm=utm,
            timestamp=timestamp,
        )

        assert event.value_usd == 0.0

    def test_attribution_event_with_empty_utm(self):
        """Test attribution event with empty UTM params."""
        timestamp = datetime.utcnow()
        utm = UTMParams()

        event = AttributionEvent(
            user_id="user123",
            product="voice-coach",
            event_type="signup",
            utm=utm,
            timestamp=timestamp,
        )

        assert event.utm.is_empty() is True
        assert event.value_usd == 0.0

    def test_attribution_event_validation(self):
        """Test that required fields are validated."""
        timestamp = datetime.utcnow()
        utm = UTMParams(source="google")

        # Should not raise exception with all required fields
        event = AttributionEvent(
            user_id="user123",
            product="voice-coach",
            event_type="signup",
            utm=utm,
            timestamp=timestamp,
        )

        assert event is not None
