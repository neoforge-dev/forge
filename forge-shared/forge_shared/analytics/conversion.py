"""
Conversion tracking module for UTM attribution.

Provides conversion funnel tracking, first-touch attribution, and ROI calculation
for marketing campaigns.

Example:
    from forge_shared.analytics.conversion import (
        track_conversion,
        ConversionTracker,
    )

    # Initialize with PostHog client
    tracker = ConversionTracker(
        posthog_client=client,
        redis_client=redis,
    )

    # Track a conversion
    await tracker.track_conversion(
        user_id=user_id,
        event_type="subscription_created",
        value_usd=29.00,
    )
"""

import json
import logging
from datetime import datetime
from typing import Any

# Direct imports to avoid circular imports
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Redis keys
CONVERSION_PREFIX = "conversion:"
ATTRIBUTION_PREFIX = "attribution:"
FUNNEL_PREFIX = "funnel:"
STRIPE_PREFIX = "stripe:conversion:"

# Cookie names
UTM_COOKIE_NAME = "forge_utm"


class UTMParams(BaseModel):
    """UTM parameters for attribution tracking."""

    source: str | None = None
    medium: str | None = None
    campaign: str | None = None
    content: str | None = None
    term: str | None = None
    landing_page: str | None = None
    referrer: str | None = None
    timestamp: datetime | None = None

    def is_empty(self) -> bool:
        """Check if all core UTM params are empty."""
        return all(v is None for v in [self.source, self.medium, self.campaign])

    def to_posthog_properties(self) -> dict[str, Any]:
        """Convert to PostHog event properties."""
        return {
            "utm_source": self.source,
            "utm_medium": self.medium,
            "utm_campaign": self.campaign,
            "utm_content": self.content,
            "utm_term": self.term,
            "$referrer": self.referrer,
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "source": self.source,
            "medium": self.medium,
            "campaign": self.campaign,
            "content": self.content,
            "term": self.term,
            "landing_page": self.landing_page,
            "referrer": self.referrer,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UTMParams":
        """Create from dictionary."""
        if data.get("timestamp") and isinstance(data["timestamp"], str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)


class ConversionTracker:
    """
    Track conversions with UTM attribution and PostHog integration.

    Features:
    - First-touch attribution
    - Conversion funnel tracking
    - Stripe revenue attribution
    - ROI calculation
    """

    def __init__(
        self,
        posthog_client: Any,
        redis_client: Any | None = None,
        cookie_name: str = UTM_COOKIE_NAME,
    ):
        """
        Initialize conversion tracker.

        Args:
            posthog_client: PostHog client for event tracking
            redis_client: Redis client for persistence (optional)
            cookie_name: Cookie name for UTM storage
        """
        self.client = posthog_client
        self.redis = redis_client
        self.cookie_name = cookie_name

    # ==========================================================================
    # Conversion Tracking
    # ==========================================================================

    async def track_conversion(
        self,
        user_id: str,
        event_type: str,
        value_usd: float = 0.0,
        utm: UTMParams | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Track a conversion event with UTM attribution.

        Args:
            user_id: User identifier
            event_type: Conversion type (signup, subscription, payment, etc.)
            value_usd: Revenue value in USD
            utm: UTM parameters from request
            properties: Additional event properties

        Returns:
            Attribution result with UTM data
        """
        # Get attribution (first-touch)
        attribution = await self.get_conversion_attribution(user_id)

        # Build event properties
        event_properties = {
            "event_type": event_type,
            "value_usd": value_usd,
            "revenue": value_usd,
            "conversion_date": datetime.utcnow().isoformat(),
            **attribution,
        }

        if properties:
            event_properties.update(properties)

        # Store conversion
        await self._store_conversion(user_id, event_type, value_usd, attribution)

        # Track in PostHog
        await self.client.track(
            event=f"conversion_{event_type}",
            distinct_id=user_id,
            properties=event_properties,
        )

        logger.info(f"Tracked conversion: {event_type} = ${value_usd} for {user_id}")

        return attribution

    async def track_stripe_conversion(
        self,
        user_id: str,
        stripe_event_id: str,
        event_type: str,
        amount_usd: float,
        plan_id: str | None = None,
        utm: UTMParams | None = None,
    ) -> dict[str, Any]:
        """
        Track a Stripe conversion event.

        Args:
            user_id: User identifier
            stripe_event_id: Stripe event ID for deduplication
            event_type: Event type (payment_succeeded, subscription_created, etc.)
            amount_usd: Amount in USD
            plan_id: Subscription plan ID
            utm: UTM parameters (optional, will use stored if not provided)

        Returns:
            Attribution result
        """
        # Check for duplicate
        if self.redis:
            dup_key = f"{STRIPE_PREFIX}{stripe_event_id}"
            existing = await self.redis.get(dup_key)
            if existing:
                logger.info(f"Skipping duplicate Stripe event: {stripe_event_id}")
                return {}

        # Get attribution
        attribution = await self.get_conversion_attribution(user_id)

        # Build properties
        properties = {
            "stripe_event_id": stripe_event_id,
            "event_type": event_type,
            "amount_usd": amount_usd,
            "revenue": amount_usd,
            "plan_id": plan_id,
            "source": "stripe",
            "conversion_date": datetime.utcnow().isoformat(),
            **attribution,
        }

        # Store Stripe conversion
        if self.redis:
            await self.redis.setex(
                f"{STRIPE_PREFIX}{stripe_event_id}",
                86400 * 7,  # 7 days TTL
                json.dumps({"user_id": user_id, "amount_usd": amount_usd}),
            )

        # Track in PostHog
        await self.client.track(
            event="stripe_conversion",
            distinct_id=user_id,
            properties=properties,
        )

        # Identify user with revenue property
        await self.client.identify(
            distinct_id=user_id,
            properties={
                "total_revenue_usd": amount_usd,
                "last_conversion": event_type,
                "last_conversion_date": datetime.utcnow().isoformat(),
            },
        )

        logger.info(f"Tracked Stripe conversion: {stripe_event_id} = ${amount_usd}")

        return attribution

    # ==========================================================================
    # Attribution
    # ==========================================================================

    async def get_conversion_attribution(
        self,
        user_id: str,
        request_utm: UTMParams | None = None,
    ) -> dict[str, Any]:
        """
        Get first-touch attribution for a user.

        Priority:
        1. Stored attribution for existing users
        2. UTM from current request (for new users)

        Args:
            user_id: User identifier
            request_utm: UTM from current request (optional)

        Returns:
            Attribution dictionary with UTM parameters
        """
        # Check for existing stored attribution
        if self.redis:
            stored = await self.redis.get(f"{ATTRIBUTION_PREFIX}{user_id}")
            if stored:
                return json.loads(stored)

        # Use request UTM if no stored attribution
        if request_utm and not request_utm.is_empty():
            attribution = request_utm.to_posthog_properties()
            attribution["attribution_model"] = "first_touch"
            attribution["attribution_date"] = datetime.utcnow().isoformat()

            # Store for future reference
            if self.redis:
                await self.redis.setex(
                    f"{ATTRIBUTION_PREFIX}{user_id}",
                    86400 * 90,  # 90 days
                    json.dumps(attribution),
                )

            return attribution

        # No attribution available
        return {
            "utm_source": None,
            "utm_medium": None,
            "utm_campaign": None,
            "utm_content": None,
            "utm_term": None,
            "attribution_model": None,
            "attribution_date": None,
        }

    async def store_first_touch(
        self,
        user_id: str,
        utm: UTMParams,
    ) -> None:
        """
        Store first-touch attribution for a user.

        Args:
            user_id: User identifier
            utm: UTM parameters
        """
        if utm.is_empty():
            return

        attribution = utm.to_posthog_properties()
        attribution["attribution_model"] = "first_touch"
        attribution["attribution_date"] = datetime.utcnow().isoformat()

        if self.redis:
            await self.redis.setex(
                f"{ATTRIBUTION_PREFIX}{user_id}",
                86400 * 90,  # 90 days
                json.dumps(attribution),
            )
            logger.info(f"Stored first-touch attribution for {user_id}")

    # ==========================================================================
    # Funnel Tracking
    # ==========================================================================

    async def track_funnel_step(
        self,
        user_id: str,
        funnel_name: str,
        step: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """
        Track a step in a conversion funnel.

        Args:
            user_id: User identifier
            funnel_name: Funnel identifier
            step: Step name (landing_viewed, signup_started, etc.)
            properties: Additional properties
        """
        funnel_properties = {
            "funnel_name": funnel_name,
            "step": step,
            "step_timestamp": datetime.utcnow().isoformat(),
            **await self.get_conversion_attribution(user_id),
        }

        if properties:
            funnel_properties.update(properties)

        await self.client.track(
            event=f"funnel_{funnel_name}_{step}",
            distinct_id=user_id,
            properties=funnel_properties,
        )

        # Store funnel state
        if self.redis:
            await self.redis.setex(
                f"{FUNNEL_PREFIX}{user_id}:{funnel_name}",
                86400 * 30,  # 30 days
                json.dumps({"last_step": step, "timestamp": datetime.utcnow().isoformat()}),
            )

    # ==========================================================================
    # ROI Calculation
    # ==========================================================================

    async def calculate_campaign_roi(
        self,
        campaign: str,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any]:
        """
        Calculate ROI for a campaign.

        Args:
            campaign: Campaign name
            start_date: Start of period
            end_date: End of period

        Returns:
            ROI metrics
        """
        # This would query PostHog for conversion data
        # For now, return structure

        return {
            "campaign": campaign,
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "visits": 0,
            "conversions": 0,
            "revenue_usd": 0.0,
            "cost_usd": 0.0,  # Would come from ad platform
            "roi_percent": 0.0,
            "cac_usd": 0.0,  # Customer acquisition cost
            "ltv_usd": 0.0,  # Lifetime value (would come from DB)
        }

    # ==========================================================================
    # Private Methods
    # ==========================================================================

    async def _store_conversion(
        self,
        user_id: str,
        event_type: str,
        value_usd: float,
        attribution: dict[str, Any],
    ) -> None:
        """Store conversion for reporting."""
        if not self.redis:
            return

        conversion = {
            "user_id": user_id,
            "event_type": event_type,
            "value_usd": value_usd,
            "timestamp": datetime.utcnow().isoformat(),
            "attribution": attribution,
        }

        await self.redis.rpush(
            f"{CONVERSION_PREFIX}{user_id}",
            json.dumps(conversion),
        )


# =============================================================================
# Convenience Functions
# =============================================================================

_conversion_tracker: ConversionTracker | None = None


def get_conversion_tracker() -> ConversionTracker | None:
    """Get global conversion tracker."""
    return _conversion_tracker


def set_conversion_tracker(tracker: ConversionTracker) -> None:
    """Set global conversion tracker."""
    global _conversion_tracker
    _conversion_tracker = tracker


async def track_signup_conversion(
    user_id: str,
    utm: UTMParams | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Track user signup conversion.

    Args:
        user_id: User identifier
        utm: UTM parameters
        properties: Additional properties

    Returns:
        Attribution result
    """
    tracker = get_conversion_tracker()
    if not tracker:
        return {}

    return await tracker.track_conversion(
        user_id=user_id,
        event_type="signup",
        value_usd=0.0,
        utm=utm,
        properties=properties,
    )


async def track_subscription_conversion(
    user_id: str,
    plan_id: str,
    amount_usd: float,
    utm: UTMParams | None = None,
) -> dict[str, Any]:
    """
    Track subscription conversion.

    Args:
        user_id: User identifier
        plan_id: Subscription plan ID
        amount_usd: Monthly amount in USD
        utm: UTM parameters

    Returns:
        Attribution result
    """
    tracker = get_conversion_tracker()
    if not tracker:
        return {}

    return await tracker.track_conversion(
        user_id=user_id,
        event_type="subscription_created",
        value_usd=amount_usd,
        utm=utm,
        properties={"plan_id": plan_id, "billing_period": "monthly"},
    )


async def track_payment_conversion(
    user_id: str,
    amount_usd: float,
    utm: UTMParams | None = None,
) -> dict[str, Any]:
    """
    Track one-time payment conversion.

    Args:
        user_id: User identifier
        amount_usd: Payment amount
        utm: UTM parameters

    Returns:
        Attribution result
    """
    tracker = get_conversion_tracker()
    if not tracker:
        return {}

    return await tracker.track_conversion(
        user_id=user_id,
        event_type="payment",
        value_usd=amount_usd,
        utm=utm,
    )


async def track_stripe_conversion(
    user_id: str,
    stripe_event_id: str,
    event_type: str,
    amount_usd: float,
    plan_id: str | None = None,
    utm: UTMParams | None = None,
) -> dict[str, Any]:
    """
    Track a Stripe conversion event.

    Args:
        user_id: User identifier
        stripe_event_id: Stripe event ID for deduplication
        event_type: Event type (payment_succeeded, subscription_created, etc.)
        amount_usd: Amount in USD
        plan_id: Subscription plan ID
        utm: UTM parameters

    Returns:
        Attribution result
    """
    tracker = get_conversion_tracker()
    if not tracker:
        return {}

    return await tracker.track_stripe_conversion(
        user_id=user_id,
        stripe_event_id=stripe_event_id,
        event_type=event_type,
        amount_usd=amount_usd,
        plan_id=plan_id,
        utm=utm,
    )


async def track_conversion(
    user_id: str,
    event_type: str,
    value_usd: float = 0.0,
    utm: UTMParams | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Track a generic conversion event.

    Args:
        user_id: User identifier
        event_type: Conversion type (signup, subscription, payment, etc.)
        value_usd: Revenue value in USD
        utm: UTM parameters
        properties: Additional event properties

    Returns:
        Attribution result
    """
    tracker = get_conversion_tracker()
    if not tracker:
        return {}

    return await tracker.track_conversion(
        user_id=user_id,
        event_type=event_type,
        value_usd=value_usd,
        utm=utm,
        properties=properties,
    )


async def track_funnel_step(
    user_id: str,
    funnel_name: str,
    step: str,
    properties: dict[str, Any] | None = None,
) -> None:
    """
    Track a step in a conversion funnel.

    Args:
        user_id: User identifier
        funnel_name: Funnel identifier
        step: Step name (landing_viewed, signup_started, etc.)
        properties: Additional properties
    """
    tracker = get_conversion_tracker()
    if not tracker:
        return

    await tracker.track_funnel_step(
        user_id=user_id,
        funnel_name=funnel_name,
        step=step,
        properties=properties,
    )


async def store_first_touch(
    user_id: str,
    utm: UTMParams,
) -> None:
    """
    Store first-touch attribution for a user.

    Args:
        user_id: User identifier
        utm: UTM parameters
    """
    tracker = get_conversion_tracker()
    if not tracker:
        return

    await tracker.store_first_touch(user_id, utm)


async def get_conversion_attribution(
    user_id: str,
    request_utm: UTMParams | None = None,
) -> dict[str, Any]:
    """
    Get first-touch attribution for a user.

    Args:
        user_id: User identifier
        request_utm: UTM from current request (optional)

    Returns:
        Attribution dictionary with UTM parameters
    """
    tracker = get_conversion_tracker()
    if not tracker:
        return {}

    return await tracker.get_conversion_attribution(user_id, request_utm)


async def calculate_campaign_roi(
    campaign: str,
    start_date: datetime,
    end_date: datetime,
) -> dict[str, Any]:
    """
    Calculate ROI for a campaign.

    Args:
        campaign: Campaign name
        start_date: Start of period
        end_date: End of period

    Returns:
        ROI metrics
    """
    tracker = get_conversion_tracker()
    if not tracker:
        return {}

    return await tracker.calculate_campaign_roi(campaign, start_date, end_date)


__all__ = [
    "ConversionTracker",
    "UTMParams",
    "track_conversion",
    "track_stripe_conversion",
    "track_signup_conversion",
    "track_subscription_conversion",
    "track_payment_conversion",
    "track_funnel_step",
    "get_conversion_attribution",
    "store_first_touch",
    "calculate_campaign_roi",
    "get_conversion_tracker",
    "set_conversion_tracker",
]
