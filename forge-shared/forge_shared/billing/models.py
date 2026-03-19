"""
Data models for billing and Stripe integration.

Defines Pydantic models for checkout sessions, subscriptions, webhooks,
and customers for unified billing across the FORGE portfolio.

Example:
    ```python
    from forge_shared.billing.models import CheckoutSession, Subscription, PricingTier

    session = CheckoutSession(
        id="cs_123",
        url="https://checkout.stripe.com/...",
        product="interview-simulator",
        tier=PricingTier.PRO,
        user_id="user_123"
    )
    ```
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PricingTier(str, Enum):
    """
    Pricing tier levels for FORGE products.

    Attributes:
        FREE: Free tier with basic features
        STARTER: Starter tier with limited features
        PRO: Professional tier with full features
        ENTERPRISE: Enterprise tier with custom features and support
    """

    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, Enum):
    """
    Subscription status values from Stripe.

    Attributes:
        ACTIVE: Subscription is active
        PAST_DUE: Payment failed, subscription pending
        CANCELED: Subscription canceled
        UNPAID: Subscription unpaid
        TRIALING: Subscription in trial period
        INCOMPLETE: Initial payment failed
        INCOMPLETE_EXPIRED: Initial payment expired
        PAUSED: Subscription paused
    """

    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    TRIALING = "trialing"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    PAUSED = "paused"


class CheckoutSession(BaseModel):
    """
    Stripe checkout session data.

    Represents a Stripe Checkout session for initiating payments.

    Attributes:
        id: Stripe checkout session ID
        url: Checkout page URL for redirecting user
        product: FORGE product identifier
        tier: Pricing tier for the checkout
        user_id: Internal user identifier
        customer_id: Stripe customer ID (optional)
        expires_at: Session expiration time (optional)

    Example:
        ```python
        session = CheckoutSession(
            id="cs_test_123",
            url="https://checkout.stripe.com/pay/cs_test_123",
            product="interview-simulator",
            tier=PricingTier.PRO,
            user_id="user_550e8400"
        )
        ```
    """

    id: str = Field(..., description="Stripe checkout session ID")
    url: str = Field(..., description="Checkout page URL")
    product: str = Field(..., description="FORGE product identifier")
    tier: PricingTier = Field(..., description="Pricing tier")
    user_id: str = Field(..., description="Internal user ID")
    customer_id: str | None = Field(default=None, description="Stripe customer ID")
    expires_at: datetime | None = Field(default=None, description="Session expiration time")


class Subscription(BaseModel):
    """
    Stripe subscription data.

    Represents an active or inactive subscription.

    Attributes:
        id: Stripe subscription ID
        status: Subscription status
        product: FORGE product identifier
        tier: Pricing tier
        user_id: Internal user identifier
        customer_id: Stripe customer ID
        current_period_start: Current billing period start
        current_period_end: Current billing period end
        cancel_at_period_end: Whether subscription cancels at period end
        canceled_at: When subscription was canceled (if applicable)

    Example:
        ```python
        subscription = Subscription(
            id="sub_123",
            status=SubscriptionStatus.ACTIVE,
            product="interview-simulator",
            tier=PricingTier.PRO,
            user_id="user_123",
            customer_id="cus_123",
            current_period_end=datetime(2024, 2, 1)
        )
        ```
    """

    id: str = Field(..., description="Stripe subscription ID")
    status: SubscriptionStatus = Field(..., description="Subscription status")
    product: str = Field(..., description="FORGE product identifier")
    tier: PricingTier = Field(..., description="Pricing tier")
    user_id: str = Field(..., description="Internal user ID")
    customer_id: str = Field(..., description="Stripe customer ID")
    current_period_start: datetime | None = Field(default=None, description="Current period start")
    current_period_end: datetime | None = Field(default=None, description="Current period end")
    cancel_at_period_end: bool = Field(default=False, description="Cancel at period end")
    canceled_at: datetime | None = Field(default=None, description="Cancellation time")

    @property
    def is_active(self) -> bool:
        """Check if subscription is currently active."""
        return self.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING)


class Customer(BaseModel):
    """
    Stripe customer data.

    Represents a Stripe customer linked to an internal user.

    Attributes:
        id: Stripe customer ID
        email: Customer email address
        user_id: Internal user identifier
        name: Customer name (optional)
        created_at: Customer creation time (optional)

    Example:
        ```python
        customer = Customer(
            id="cus_123",
            email="user@example.com",
            user_id="user_123"
        )
        ```
    """

    id: str = Field(..., description="Stripe customer ID")
    email: str = Field(..., description="Customer email")
    user_id: str = Field(..., description="Internal user ID")
    name: str | None = Field(default=None, description="Customer name")
    created_at: datetime | None = Field(default=None, description="Creation time")


class WebhookEvent(BaseModel):
    """
    Stripe webhook event data.

    Represents a processed Stripe webhook event.

    Attributes:
        id: Stripe event ID
        type: Event type (e.g., "checkout.session.completed")
        data: Event data payload
        processed: Whether event has been processed
        created_at: Event creation time

    Example:
        ```python
        event = WebhookEvent(
            id="evt_123",
            type="checkout.session.completed",
            data={"session_id": "cs_123", "customer": "cus_123"},
            processed=True
        )
        ```
    """

    id: str = Field(..., description="Stripe event ID")
    type: str = Field(..., description="Event type")
    data: dict[str, Any] = Field(default_factory=dict, description="Event data")
    processed: bool = Field(default=False, description="Processing status")
    created_at: datetime | None = Field(default=None, description="Event creation time")


class PaymentIntent(BaseModel):
    """
    Stripe payment intent data.

    Attributes:
        id: Stripe payment intent ID
        amount: Amount in smallest currency unit (e.g., cents)
        currency: ISO 4217 currency code (e.g., "usd")
        status: Payment intent status
        customer_id: Stripe customer ID (optional)
    """

    id: str = Field(..., description="Stripe payment intent ID")
    amount: int = Field(..., description="Amount in smallest currency unit")
    currency: str = Field(default="usd", description="ISO 4217 currency code")
    status: str = Field(default="requires_payment_method", description="Payment intent status")
    customer_id: str | None = Field(default=None, description="Stripe customer ID")


class Invoice(BaseModel):
    """
    Stripe invoice data.

    Attributes:
        id: Stripe invoice ID
        customer_id: Stripe customer ID (optional)
        amount_due: Amount due in smallest currency unit
        status: Invoice status
    """

    id: str = Field(..., description="Stripe invoice ID")
    customer_id: str | None = Field(default=None, description="Stripe customer ID")
    amount_due: int = Field(default=0, description="Amount due in smallest currency unit")
    status: str = Field(default="draft", description="Invoice status")


class Product(BaseModel):
    """
    Stripe product data.

    Attributes:
        id: Stripe product ID
        name: Product name
        active: Whether the product is active
    """

    id: str = Field(..., description="Stripe product ID")
    name: str = Field(default="", description="Product name")
    active: bool = Field(default=True, description="Whether the product is active")


class Price(BaseModel):
    """
    Stripe price data.

    Attributes:
        id: Stripe price ID
        product_id: Stripe product ID
        unit_amount: Price amount in smallest currency unit
        currency: ISO 4217 currency code
    """

    id: str = Field(..., description="Stripe price ID")
    product_id: str = Field(default="", description="Stripe product ID")
    unit_amount: int = Field(default=0, description="Price amount in smallest currency unit")
    currency: str = Field(default="usd", description="ISO 4217 currency code")


class BillingError(Exception):
    """
    Custom exception for billing operations.

    Attributes:
        message: Error message
        code: Error code for programmatic handling
        details: Additional error details

    Example:
        ```python
        raise BillingError(
            message="Checkout session creation failed",
            code="checkout_failed",
            details={"stripe_error": "card_declined"}
        )
        ```
    """

    def __init__(
        self,
        message: str,
        code: str = "billing_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        """Convert error to dictionary for API responses."""
        return {
            "error": self.code,
            "message": self.message,
            "details": self.details,
        }
