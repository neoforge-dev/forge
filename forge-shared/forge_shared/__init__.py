"""
forge-shared: Shared services library for FORGE portfolio.

This package provides common functionality for authentication, analytics,
middleware, configuration, and utilities across all FORGE projects.

Architecture: 4-tier system (see TIER_STRUCTURE.md)

Example:
    ```python
    from fastapi import FastAPI
    from forge_shared.auth import JWTAuth
    from forge_shared.analytics import PostHogClient

    app = FastAPI()
    auth = JWTAuth(secret_key="your-secret")
    analytics = PostHogClient(api_key="your-api-key")
    ```
"""

__version__ = "0.1.0"
__author__ = "FORGE Team"
__email__ = "tech@forge.dev"

# ============================================================================
# TIER 1: FOUNDATION
# Low-level utilities, configuration, and base models
# ============================================================================

# Config
from forge_shared.config import (
    BaseConfig,
    get_config,
    set_config,
    DomainConfig,
    get_domain_config,
    load_config_from_dict,
    load_config_from_env,
    load_config_from_file,
)

# Logging
from forge_shared.logging import (
    get_logger,
    setup_logging,
    LoggingContext,
)

# Utils
from forge_shared.utils import (
    get_client_ip,
    is_valid_ip,
    is_private_ip,
    get_user_agent,
    parse_accept_header,
    get_bearer_token,
)

# ============================================================================
# TIER 2: CORE SERVICES
# Essential services (auth, middleware, health, verification)
# ============================================================================

# Authentication
from forge_shared.auth import (
    JWTAuth,
    get_current_user,
    require_role,
    require_permission,
)

# Middleware
from forge_shared.middleware import (
    RateLimitMiddleware,
    SecurityMiddleware,
    RequestIDMiddleware,
    CORSMiddleware,
    MiddlewareRegistry,
)

# Health Checks
from forge_shared.health import (
    create_health_router,
    HealthCheck,
    HealthStatus,
)

# Verification
from forge_shared.verification import (
    VerificationClaim,
    VerificationEvidence,
    VerificationType,
    VerificationStatus,
)

# ============================================================================
# TIER 3: BUSINESS LOGIC
# Domain-specific business logic
# ============================================================================

# Analytics
from forge_shared.analytics import (
    PostHogClient,
    track_event,
    identify,
    alias,
    AnalyticsMiddleware,
    ConversionTracker,
    UTMParams,
    track_conversion,
    track_funnel_step,
    track_stripe_conversion,
    track_signup_conversion,
    track_subscription_conversion,
    track_payment_conversion,
)

# Leads
from forge_shared.leads import (
    Lead,
    LeadStatus,
    LeadSource,
    LeadScore,
    LeadFilter,
    DeduplicationService,
    LeadEnrichmentService,
    LeadRouter,
)

# Marketing
from forge_shared.marketing import (
    Channel,
    ContentSequence,
    DistributionScheduler,
    MultiChannelDistributor,
    SequenceSchedule,
    SequenceStep,
    SequenceTemplate,
    create_sequence,
    format_content,
    schedule_sequence,
)

# Compliance
from forge_shared.compliance import (
    AgeGroup,
    ChildProfile,
    ConsentStatus,
    ConsentType,
    COPPAComplianceService,
    DataExportFormat,
    DataRetentionPolicy,
    DeletionRequest,
    GDPRService,
    ParentalConsent,
    ConsentRecord,
    DataExportRequest,
)

# Content
from forge_shared.content import (
    AtomizationResult,
    BlogPost,
    ContentAsset,
    ContentRecycler,
    ContentType,
    Platform,
    atomize_blog_post,
)

# Billing
from forge_shared.billing import (
    StripeClient,
    BillingError,
    CheckoutSession,
    Customer,
    PricingTier,
    Subscription,
    SubscriptionStatus,
    BillingConfig,
    PRODUCT_PRICING,
    SUPPORTED_PRODUCTS,
    get_price_id,
    get_product_tiers,
    is_valid_product,
    is_valid_tier,
    HANDLED_EVENTS,
    handle_webhook,
    is_handled_event,
    verify_signature,
)

# ============================================================================
# TIER 4: INTEGRATIONS
# External service integrations
# ============================================================================

# Email
from forge_shared.email import (
    EmailTemplate,
    EmailSequence,
    SequenceStep as EmailSequenceStep,
    EmailEvent,
    SubscriberStatus,
    SequenceService,
    SequenceExecutor,
    EmailSender,
    EmailProvider,
    EmailTracker,
)

# AI
from forge_shared.ai import (
    LLMClient,
    LLMProvider,
    RetryConfig,
    create_client,
    extract_json,
)

# UTM
from forge_shared.utm import (
    UTMParams as UTMMiddleware,
    UTMMiddleware,
    get_utm_params,
    store_utm,
    get_attribution,
)

# ============================================================================
# PUBLIC API
# ============================================================================

__all__ = [
    # Version
    "__version__",
    # Tier 1: Foundation
    "BaseConfig",
    "get_config",
    "set_config",
    "DomainConfig",
    "get_domain_config",
    "load_config_from_dict",
    "load_config_from_env",
    "load_config_from_file",
    "get_logger",
    "setup_logging",
    "LoggingContext",
    "get_client_ip",
    "is_valid_ip",
    "is_private_ip",
    "get_user_agent",
    "parse_accept_header",
    "get_bearer_token",
    # Tier 2: Core Services
    "JWTAuth",
    "get_current_user",
    "require_role",
    "require_permission",
    "RateLimitMiddleware",
    "SecurityMiddleware",
    "RequestIDMiddleware",
    "CORSMiddleware",
    "MiddlewareRegistry",
    "create_health_router",
    "HealthCheck",
    "HealthStatus",
    "VerificationClaim",
    "VerificationEvidence",
    "VerificationType",
    "VerificationStatus",
    # Tier 3: Business Logic
    "PostHogClient",
    "track_event",
    "identify",
    "alias",
    "AnalyticsMiddleware",
    "ConversionTracker",
    "UTMParams",
    "track_conversion",
    "track_funnel_step",
    "track_stripe_conversion",
    "track_signup_conversion",
    "track_subscription_conversion",
    "track_payment_conversion",
    "Lead",
    "LeadStatus",
    "LeadSource",
    "LeadScore",
    "LeadFilter",
    "DeduplicationService",
    "LeadEnrichmentService",
    "LeadRouter",
    "Channel",
    "ContentSequence",
    "DistributionScheduler",
    "MultiChannelDistributor",
    "SequenceSchedule",
    "SequenceStep",
    "SequenceTemplate",
    "create_sequence",
    "format_content",
    "schedule_sequence",
    "AgeGroup",
    "ChildProfile",
    "ConsentStatus",
    "ConsentType",
    "COPPAComplianceService",
    "DataExportFormat",
    "DataRetentionPolicy",
    "DeletionRequest",
    "GDPRService",
    "ParentalConsent",
    "ConsentRecord",
    "DataExportRequest",
    "AtomizationResult",
    "BlogPost",
    "ContentAsset",
    "ContentRecycler",
    "ContentType",
    "Platform",
    "atomize_blog_post",
    "StripeClient",
    "BillingError",
    "CheckoutSession",
    "Customer",
    "PricingTier",
    "Subscription",
    "SubscriptionStatus",
    "BillingConfig",
    "PRODUCT_PRICING",
    "SUPPORTED_PRODUCTS",
    "get_price_id",
    "get_product_tiers",
    "is_valid_product",
    "is_valid_tier",
    "HANDLED_EVENTS",
    "handle_webhook",
    "is_handled_event",
    "verify_signature",
    # Tier 4: Integrations
    "EmailTemplate",
    "EmailSequence",
    "EmailSequenceStep",
    "EmailEvent",
    "SubscriberStatus",
    "SequenceService",
    "SequenceExecutor",
    "EmailSender",
    "EmailProvider",
    "EmailTracker",
    "LLMClient",
    "LLMProvider",
    "RetryConfig",
    "create_client",
    "extract_json",
    "UTMMiddleware",
    "get_utm_params",
    "store_utm",
    "get_attribution",
]

# ============================================================================
# TIER MODULE ACCESS
# Access by tier for advanced use cases
# ============================================================================

# Tier 1: Foundation
TIER_1_FOUNDATION = {
    "config": "forge_shared.config",
    "logging": "forge_shared.logging",
    "utils": "forge_shared.utils",
}

# Tier 2: Core Services
TIER_2_CORE = {
    "auth": "forge_shared.auth",
    "middleware": "forge_shared.middleware",
    "health": "forge_shared.health",
    "verification": "forge_shared.verification",
}

# Tier 3: Business Logic
TIER_3_BUSINESS = {
    "analytics": "forge_shared.analytics",
    "leads": "forge_shared.leads",
    "marketing": "forge_shared.marketing",
    "compliance": "forge_shared.compliance",
    "content": "forge_shared.content",
    "billing": "forge_shared.billing",
}

# Tier 4: Integrations
TIER_4_INTEGRATIONS = {
    "email": "forge_shared.email",
    "ai": "forge_shared.ai",
    "utm": "forge_shared.utm",
}
