"""
Configuration for billing and Stripe integration.

Provides configuration classes and product pricing lookup tables
for unified billing across the FORGE portfolio.

Example:
    ```python
    from forge_shared.billing.config import BillingConfig, get_price_id

    config = BillingConfig(
        stripe_api_key="sk_test_...",
        stripe_webhook_secret="whsec_..."
    )

    price_id = get_price_id("interview-simulator", "pro")
    ```
"""

from pydantic import Field
from pydantic_settings import BaseSettings

from forge_shared.billing.models import PricingTier


PRODUCT_PRICING: dict[str, dict[str, str | None]] = {
    # --- codeswiftr-com ---
    "interview-simulator": {
        "free": None,
        "starter": "price_interview_starter",
        "pro": "price_interview_pro",
        "enterprise": "price_interview_enterprise",
    },
    "graph-rag-mastery": {
        "self-paced": "price_grm_self_paced",
        "cohort": "price_grm_cohort",
        "enterprise": "price_grm_enterprise",
    },
    # --- brandfocus-ai ---
    "voice-coach": {
        "free": None,
        "starter": "price_voice_starter",
        "pro": "price_voice_pro",
        "enterprise": "price_voice_enterprise",
    },
    # --- neoforge-dev ---
    "mindflow": {
        "free": None,
        "starter": "price_mindflow_starter",
        "pro": "price_mindflow_pro",
        "enterprise": "price_mindflow_enterprise",
    },
    "synapse-graph-rag": {
        "free": None,
        "starter": "price_synapse_starter",
        "pro": "price_synapse_pro",
        "enterprise": "price_synapse_enterprise",
    },
    "code-atlas": {
        "free": None,
        "starter": "price_codeatlas_starter",
        "pro": "price_codeatlas_pro",
        "enterprise": "price_codeatlas_enterprise",
    },
    # --- leanvibe-dev ---
    "startup-factory": {
        "starter": "price_sf_starter",
        "pro": "price_sf_pro",
        "enterprise": "price_sf_enterprise",
    },
    "graph-rag-blueprint": {
        "poc": "price_grb_poc",
        "implementation": "price_grb_implementation",
        "license": "price_grb_license",
    },
    "leanvibe": {
        "free": None,
        "starter": "price_leanvibe_starter",
        "pro": "price_leanvibe_pro",
        "enterprise": "price_leanvibe_enterprise",
    },
    # --- calmconnect-io ---
    "calmconnect-scheduler": {
        "pilot": "price_cc_pilot",
        "pro": "price_cc_pro",
        "enterprise": "price_cc_enterprise",
    },
    # --- babybit-es ---
    "babybites-mobile": {
        "monthly": "price_babybites_monthly",
        "annual": "price_babybites_annual",
    },
    # --- mumchef-io ---
    "family-meal-planner": {
        "free": None,
        "monthly": "price_fmp_monthly",
        "annual": "price_fmp_annual",
    },
    # --- adguild-io ---
    "campaign-intelligence": {
        "starter": "price_ci_starter",
        "pro": "price_ci_pro",
        "enterprise": "price_ci_enterprise",
    },
    # --- leanvibe-ai ---
    "desk-workout": {
        "monthly": "price_dw_monthly",
        "annual": "price_dw_annual",
    },
}

SUPPORTED_PRODUCTS = list(PRODUCT_PRICING.keys())


class BillingConfig(BaseSettings):
    """
    Billing configuration settings.

    Loads configuration from environment variables with sensible defaults.
    Environment variable names are prefixed with the specified prefix.

    Attributes:
        stripe_api_key: Stripe API secret key
        stripe_webhook_secret: Stripe webhook signing secret
        stripe_publishable_key: Stripe publishable key (for frontend)
        default_currency: Default currency for pricing (ISO 4217)
        checkout_success_url: Default success redirect URL
        checkout_cancel_url: Default cancel redirect URL

    Environment Variables:
        STRIPE_API_KEY: Stripe secret key
        STRIPE_WEBHOOK_SECRET: Webhook signing secret
        STRIPE_PUBLISHABLE_KEY: Publishable key
        STRIPE_DEFAULT_CURRENCY: Default currency (default: usd)

    Example:
        ```python
        config = BillingConfig()
        # Or with explicit values
        config = BillingConfig(
            stripe_api_key="sk_test_...",
            stripe_webhook_secret="whsec_..."
        )
        ```
    """

    stripe_api_key: str = Field(
        default="",
        description="Stripe API secret key",
        alias="STRIPE_API_KEY",
    )
    stripe_webhook_secret: str = Field(
        default="",
        description="Stripe webhook signing secret",
        alias="STRIPE_WEBHOOK_SECRET",
    )
    stripe_publishable_key: str = Field(
        default="",
        description="Stripe publishable key",
        alias="STRIPE_PUBLISHABLE_KEY",
    )
    default_currency: str = Field(
        default="usd",
        description="Default currency (ISO 4217)",
        alias="STRIPE_DEFAULT_CURRENCY",
    )
    checkout_success_url: str = Field(
        default="",
        description="Default success URL",
        alias="STRIPE_SUCCESS_URL",
    )
    checkout_cancel_url: str = Field(
        default="",
        description="Default cancel URL",
        alias="STRIPE_CANCEL_URL",
    )

    model_config = {
        "env_prefix": "",
        "extra": "ignore",
        "populate_by_name": True,
    }


def get_price_id(product: str, tier: str | PricingTier) -> str | None:
    """
    Get Stripe price ID for a product and tier.

    Args:
        product: FORGE product identifier (e.g., "interview-simulator")
        tier: Pricing tier (string or PricingTier enum)

    Returns:
        Stripe price ID or None for free tier

    Raises:
        ValueError: If product or tier is not found

    Example:
        ```python
        price_id = get_price_id("interview-simulator", PricingTier.PRO)
        # Returns: "price_interview_pro"
        ```
    """
    tier_str = tier.value if isinstance(tier, PricingTier) else tier

    if product not in PRODUCT_PRICING:
        raise ValueError(f"Unknown product: {product}")

    product_prices = PRODUCT_PRICING[product]
    if tier_str not in product_prices:
        raise ValueError(f"Unknown tier '{tier_str}' for product '{product}'")

    return product_prices[tier_str]


def get_product_tiers(product: str) -> list[str]:
    """
    Get available pricing tiers for a product.

    Args:
        product: FORGE product identifier

    Returns:
        List of available tier names

    Raises:
        ValueError: If product is not found
    """
    if product not in PRODUCT_PRICING:
        raise ValueError(f"Unknown product: {product}")

    return list(PRODUCT_PRICING[product].keys())


def is_valid_product(product: str) -> bool:
    """Check if product identifier is valid."""
    return product in PRODUCT_PRICING


def is_valid_tier(product: str, tier: str | PricingTier) -> bool:
    """Check if tier is valid for product."""
    tier_str = tier.value if isinstance(tier, PricingTier) else tier
    return product in PRODUCT_PRICING and tier_str in PRODUCT_PRICING[product]
