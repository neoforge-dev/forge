"""
Billing module for Stripe integration.

Provides unified Stripe integration for all FORGE products including
checkout sessions, webhooks, and subscription management.

Example:
    ```python
    from forge_shared.billing import StripeClient, create_checkout_session

    client = StripeClient(api_key="sk_...")
    session = await client.create_checkout_session(
        product="interview-simulator",
        tier="pro",
        user_id="user_123",
        success_url="https://app.codeswiftr.com/success",
        cancel_url="https://app.codeswiftr.com/cancel"
    )
    ```
"""

from forge_shared.billing.client import StripeClient
from forge_shared.billing.config import (
    PRODUCT_PRICING,
    SUPPORTED_PRODUCTS,
    BillingConfig,
    get_price_id,
    get_product_tiers,
    is_valid_product,
    is_valid_tier,
)
from forge_shared.billing.models import (
    BillingError,
    CheckoutSession,
    Customer,
    Invoice,
    PaymentIntent,
    Price,
    PricingTier,
    Product,
    Subscription,
    SubscriptionStatus,
    WebhookEvent,
)
from forge_shared.billing.webhooks import (
    HANDLED_EVENTS,
    handle_webhook,
    is_handled_event,
    verify_signature,
)

__all__ = [
    # Client
    "StripeClient",
    # Models
    "BillingError",
    "CheckoutSession",
    "Customer",
    "Invoice",
    "PaymentIntent",
    "Price",
    "PricingTier",
    "Product",
    "Subscription",
    "SubscriptionStatus",
    "WebhookEvent",
    # Config
    "BillingConfig",
    "PRODUCT_PRICING",
    "SUPPORTED_PRODUCTS",
    "get_price_id",
    "get_product_tiers",
    "is_valid_product",
    "is_valid_tier",
    # Webhooks
    "HANDLED_EVENTS",
    "handle_webhook",
    "is_handled_event",
    "verify_signature",
]
