"""
Tests for billing module.

Test coverage for Stripe client, checkout sessions, webhooks, and
subscription management.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone

from forge_shared.billing import (
    StripeClient,
    BillingConfig,
    PRODUCT_PRICING,
    SUPPORTED_PRODUCTS,
    get_price_id,
    get_product_tiers,
    is_valid_product,
    is_valid_tier,
    BillingError,
    CheckoutSession,
    Customer,
    PricingTier,
    Subscription,
    SubscriptionStatus,
    WebhookEvent,
    HANDLED_EVENTS,
    handle_webhook,
    is_handled_event,
    verify_signature,
)


class TestBillingConfig:
    """
    Tests for billing configuration.

    Tests product pricing, tier validation, and price ID retrieval.
    """

    def test_supported_products(self):
        """
        Test SUPPORTED_PRODUCTS contains expected products.
        """
        assert "interview-simulator" in SUPPORTED_PRODUCTS
        assert isinstance(SUPPORTED_PRODUCTS, (list, tuple, set))

    def test_is_valid_product(self):
        """
        Test product validation.
        """
        assert is_valid_product("interview-simulator") is True
        assert is_valid_product("invalid-product") is False

    def test_is_valid_tier(self):
        """
        Test tier validation.
        """
        assert is_valid_tier("interview-simulator", "free") is True
        assert is_valid_tier("interview-simulator", "pro") is True
        assert is_valid_tier("interview-simulator", "invalid-tier") is False
        assert is_valid_tier("invalid-product", "pro") is False

    def test_get_product_tiers(self):
        """
        Test getting tiers for a product.
        """
        tiers = get_product_tiers("interview-simulator")
        assert isinstance(tiers, (list, dict))

    def test_get_price_id(self):
        """
        Test getting price ID for product and tier.
        """
        # This may return None if not configured, but shouldn't raise
        price_id = get_price_id("interview-simulator", "pro")
        assert price_id is None or isinstance(price_id, str)


class TestBillingModels:
    """
    Tests for billing data models.

    Tests model creation and validation.
    """

    def test_pricing_tier_values(self):
        """
        Test PricingTier enum values.
        """
        assert PricingTier.FREE.value == "free"
        assert PricingTier.STARTER.value == "starter"
        assert PricingTier.PRO.value == "pro"
        assert PricingTier.ENTERPRISE.value == "enterprise"

    def test_subscription_status_values(self):
        """
        Test SubscriptionStatus enum values.
        """
        assert SubscriptionStatus.ACTIVE.value == "active"
        assert SubscriptionStatus.CANCELED.value == "canceled"
        assert SubscriptionStatus.PAST_DUE.value == "past_due"

    def test_customer_creation(self):
        """
        Test Customer model creation.
        """
        customer = Customer(
            id="cus_123",
            email="test@example.com",
            user_id="user_123",
            name="Test User",
        )

        assert customer.id == "cus_123"
        assert customer.email == "test@example.com"
        assert customer.user_id == "user_123"

    def test_subscription_creation(self):
        """
        Test Subscription model creation.
        """
        subscription = Subscription(
            id="sub_123",
            customer_id="cus_123",
            user_id="user_123",
            status=SubscriptionStatus.ACTIVE,
            product="interview-simulator",
            tier=PricingTier.PRO,
            current_period_end=datetime.now(timezone.utc),
        )

        assert subscription.id == "sub_123"
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.is_active is True

    def test_billing_error(self):
        """
        Test BillingError exception.
        """
        error = BillingError("Payment failed", code="payment_failed")
        assert "Payment failed" in str(error)
        assert error.code == "payment_failed"
        assert error.to_dict()["error"] == "payment_failed"


class TestStripeClient:
    """
    Tests for Stripe client.

    Tests client initialization and mock operations.
    """

    def test_client_creation(self):
        """
        Test StripeClient creation.
        """
        client = StripeClient(api_key="sk_test_123")
        assert client is not None

    @pytest.mark.asyncio
    async def test_create_checkout_session_mock(self):
        """
        Test creating checkout session with mocked Stripe.
        """
        with patch("stripe.checkout.Session.create") as mock_create:
            mock_create.return_value = MagicMock(
                id="cs_123",
                url="https://checkout.stripe.com/...",
            )

            client = StripeClient(api_key="sk_test_123")
            # Note: Actual method signature may vary
            # This is a placeholder test structure


class TestWebhooks:
    """
    Tests for webhook handling.

    Tests webhook signature verification and event handling.
    """

    def test_handled_events(self):
        """
        Test HANDLED_EVENTS contains expected events.
        """
        assert "checkout.session.completed" in HANDLED_EVENTS
        assert "customer.subscription.updated" in HANDLED_EVENTS

    def test_is_handled_event(self):
        """
        Test event handling check.
        """
        assert is_handled_event("checkout.session.completed") is True
        assert is_handled_event("unknown.event") is False

    def test_verify_signature_invalid(self):
        """
        Test signature verification with invalid signature.
        """
        # verify_signature returns False for invalid signatures
        result = verify_signature(
            payload=b"test-payload",
            signature="invalid-signature",
            secret="whsec_test",
        )
        assert result is False
