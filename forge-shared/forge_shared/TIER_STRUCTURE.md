# forge_shared Tier Architecture

**Document ID:** FORGE_SHARED_TIER_STRUCTURE  
**Version:** 1.0  
**Last Updated:** 2026-02-11  
**Status:** Active Architecture

---

## Overview

The `forge_shared` package is organized into a **4-tier architecture** that promotes clean separation of concerns, maintainability, and scalability. Each tier has a specific responsibility and dependency direction.

```
┌─────────────────────────────────────────────────────────────────┐
│                    TIER 4: INTEGRATION LAYER                     │
│         External Services (Email, Billing, AI APIs)              │
├─────────────────────────────────────────────────────────────────┤
│                   TIER 3: BUSINESS LOGIC LAYER                   │
│         Content, Leads, Marketing, Analytics, Compliance          │
├─────────────────────────────────────────────────────────────────┤
│                   TIER 2: CORE SERVICES LAYER                     │
│              Auth, Middleware, Health, Verification              │
├─────────────────────────────────────────────────────────────────┤
│                   TIER 1: FOUNDATION LAYER                      │
│            Config, Logging, Utils, Base Models                    │
└─────────────────────────────────────────────────────────────────┘
```

## Tier Dependency Rules

1. **Higher tiers can depend on lower tiers** (T4 → T3 → T2 → T1)
2. **Lower tiers cannot depend on higher tiers** (No reverse dependencies)
3. **Same-tier dependencies are allowed** within reason
4. **Cross-tier dependencies must flow downward**

---

## Tier 1: Foundation Layer

**Purpose:** Low-level utilities, configuration, and base models used throughout the codebase.

| Module | Responsibility | Dependencies |
|--------|----------------|--------------|
| `config/` | Configuration loading, validation, domain configs | None (pure Python) |
| `logging/` | Structured logging, context, formatting | None |
| `utils/` | Helper functions, type utilities | None |
| Base Models | Common Pydantic models | None |

### Module Details

#### config/
Configuration management for all FORGE projects.

```
config/
├── __init__.py          # Exports: BaseConfig, get_config, loaders
├── base.py              # BaseConfig abstract class
├── domain.py            # Domain-specific configuration
├── loaders.py           # YAML/JSON/Env file loaders
└── validators.py        # Config validation schemas
```

#### logging/
Structured logging with context support.

```
logging/
├── __init__.py          # Exports: get_logger, setup_logging
├── context.py           # Logging context (request ID, user ID)
├── formatter.py         # JSON/structured formatters
└── middleware.py       # FastAPI logging middleware
```

#### utils/
General-purpose utilities.

```
utils/
├── __init__.py          # Common utilities
├── datetime.py          # Date/time helpers
├── validation.py        # Validation helpers
└── cryptography.py     # Encryption utilities
```

---

## Tier 2: Core Services Layer

**Purpose:** Essential services that provide authentication, request processing, and health monitoring.

| Module | Responsibility | Dependencies |
|--------|----------------|--------------|
| `auth/` | JWT authentication, dependency injection | Tier 1 |
| `middleware/` | HTTP middleware (rate limiting, CORS, security) | Tier 1 |
| `health/` | Health checks, readiness probes | Tier 1 |
| `verification/` | Input validation, data verification | Tier 1 |

### Module Details

#### auth/
Authentication and authorization.

```
auth/
├── __init__.py          # Exports: JWTAuth, get_current_user, require_role
├── jwt.py               # JWT token creation/validation
├── models.py            # Auth models (User, Token, Session)
├── middleware.py       # FastAPI auth middleware
└── dependencies.py     # Auth dependencies for FastAPI
```

#### middleware/
HTTP request/response middleware.

```
middleware/
├── __init__.py          # Exports: MiddlewareRegistry, CORSMiddleware
├── rate_limit.py        # Token bucket rate limiting
├── security.py         # Security headers, XSS protection
├── request_id.py       # Request ID generation
├── cors.py             # CORS configuration
└── registry.py        # Middleware ordering/registration
```

#### health/
Health check endpoints and probes.

```
health/
├── __init__.py          # Exports: create_health_router, HealthCheck
├── router.py            # Health check router
└── checks.py           # Individual health checks
```

#### verification/
Input validation and data verification.

```
verification/
├── __init__.py          # Exports: verify_email, verify_phone
├── email.py            # Email validation
├── phone.py            # Phone number validation
└── inputs.py          # Input sanitization
```

---

## Tier 3: Business Logic Layer

**Purpose:** Domain-specific business logic that builds on core services.

| Module | Responsibility | Dependencies |
|--------|----------------|--------------|
| `analytics/` | Event tracking, conversion metrics | Tier 1, Tier 2 |
| `leads/` | Lead management, enrichment, deduplication | Tier 1, Tier 2 |
| `marketing/` | Marketing campaigns, UTM tracking | Tier 1, Tier 2 |
| `compliance/` | GDPR, COPPA compliance utilities | Tier 1 |
| `content/` | Content processing, recycling | Tier 1 |
| `billing/` | Payment processing, subscriptions | Tier 1, Tier 2 |

### Module Details

#### analytics/
User analytics and event tracking.

```
analytics/
├── __init__.py          # Exports: PostHogClient, track_event
├── events.py            # Event models and tracking
├── posthog.py          # PostHog integration
├── conversion.py       # Conversion funnel tracking
└── middleware.py      # Analytics middleware
```

#### leads/
Lead management system.

```
leads/
├── __init__.py          # Exports: Lead, LeadService
├── models.py            # Lead models
├── enrichment.py        # Lead data enrichment
├── filtering.py        # Lead filtering/sorting
├── deduplication.py   # Duplicate lead detection
└── router.py          # Lead API router
```

#### marketing/
Marketing automation and attribution.

```
marketing/
├── __init__.py          # Exports: Campaign, AttributionService
├── campaigns.py        # Campaign management
├── attribution.py      # Marketing attribution
├── tracking.py        # Click/conversion tracking
└── segments.py        # User segmentation
```

#### compliance/
Data privacy and compliance.

```
compliance/
├── __init__.py          # Exports: GDPRService, COPPAComplianceService
├── models.py            # Compliance models
├── gdpr.py             # GDPR utilities
└── coppa.py            # COPPA (children's privacy)
```

#### content/
Content processing and recycling.

```
content/
├── __init__.py          # Exports: ContentService
├── models.py            # Content models
├── recycling.py        # Content recycling/reuse
└── processing.py      # Content transformation
```

#### billing/
Payment and subscription handling.

```
billing/
├── __init__.py          # Exports: BillingService, StripeClient
├── models.py            # Billing models
├── client.py           # Payment provider client
├── webhooks.py         # Webhook handling
└── config.py          # Billing configuration
```

---

## Tier 4: Integration Layer

**Purpose:** External service integrations and API clients.

| Module | Responsibility | Dependencies |
|--------|----------------|--------------|
| `email/` | Email sending, sequences, tracking | Tier 1, Tier 3 |
| `ai/` | AI/LLM API clients, parsing | Tier 1 |
| `utm/` | UTM parameter handling | Tier 1 |

### Module Details

#### email/
Email services and automation.

```
email/
├── __init__.py          # Exports: EmailService, send_email
├── models.py            # Email models
├── sender.py           # Email delivery
├── sequence.py        # Email sequences/drip campaigns
└── tracking.py        # Email open/click tracking
```

#### ai/
AI and LLM integration.

```
ai/
├── __init__.py          # Exports: AIClient
├── client.py            # AI API client
└── parsing.py          # AI response parsing
```

#### utm/
UTM parameter tracking.

```
utm/
├── __init__.py          # Exports: UTMParams, UTMMiddleware
├── params.py            # UTM parameter models
└── middleware.py       # UTM tracking middleware
```

---

## Tier Assignment by Module

### Tier 1: Foundation

```
forge_shared/
├── config/          ✅ Config loading and validation
├── logging/        ✅ Structured logging
└── utils/          ✅ Helper utilities
```

### Tier 2: Core Services

```
forge_shared/
├── auth/           ✅ Authentication & authorization
├── middleware/     ✅ HTTP middleware stack
├── health/        ✅ Health checks
└── verification/  ✅ Input validation
```

### Tier 3: Business Logic

```
forge_shared/
├── analytics/      ✅ Event tracking & metrics
├── leads/         ✅ Lead management
├── marketing/     ✅ Marketing automation
├── compliance/    ✅ GDPR/COPPA
├── content/       ✅ Content processing
└── billing/       ✅ Payments
```

### Tier 4: Integrations

```
forge_shared/
├── email/         ✅ Email services
├── ai/            ✅ AI API clients
└── utm/           ✅ UTM tracking
```

---

## Import Patterns

### Recommended Import Structure

```python
# Tier 1 imports (lowest level)
from forge_shared.config import BaseConfig
from forge_shared.logging import get_logger

# Tier 2 imports (core services)
from forge_shared.auth import JWTAuth
from forge_shared.middleware import RateLimitMiddleware

# Tier 3 imports (business logic)
from forge_shared.analytics import PostHogClient
from forge_shared.compliance import GDPRService

# Tier 4 imports (integrations)
from forge_shared.email import EmailService
from forge_shared.ai import AIClient
```

### Anti-Patterns to Avoid

```python
# ❌ BAD: Tier 3 depending on Tier 4
# compliance/gdpr.py should NOT import from email/

# ❌ BAD: Tier 2 depending on Tier 3
# auth/middleware.py should NOT import from leads/

# ❌ BAD: Circular dependencies
# A imports B, B imports A
```

---

## Adding New Modules

When adding a new module to `forge_shared`, follow this process:

### Step 1: Determine Tier

| Question | Answer → Tier |
|----------|---------------|
| Does it wrap external APIs? | Tier 4 |
| Does it implement business domain logic? | Tier 3 |
| Does it provide essential services (auth, health)? | Tier 2 |
| Is it a utility or configuration? | Tier 1 |

### Step 2: Create Module Structure

```bash
forge_shared/
└── new_module/
    ├── __init__.py          # Public exports
    ├── models.py            # Pydantic models
    ├── service.py           # Business logic
    └── client.py           # External clients (if needed)
```

### Step 3: Update Exports

Update `forge_shared/__init__.py` with new public APIs:

```python
from .new_module import (
    NewService,
    NewModel,
)
```

### Step 4: Add Tests

Create test file in `forge_shared/tests/`:

```python
# forge_shared/tests/test_new_module.py
import pytest
from forge_shared.new_module import NewService
```

### Step 5: Update Documentation

Update this document with new module information.

---

## Tier Migration Guide

### Migrating Existing Code to New Tiers

If existing code doesn't follow tier structure:

1. **Identify current dependencies** using import analysis
2. **Extract shared utilities** to Tier 1
3. **Create abstractions** for cross-tier dependencies
4. **Refactor gradually** - don't break existing APIs

### Deprecation Strategy

When moving modules between tiers:

1. **Add deprecation warning** in old location
2. **Keep old imports working** for one release cycle
3. **Update documentation** with new location
4. **Remove old code** in next major version

---

## Testing Strategy by Tier

### Tier 1 Tests (Foundation)
- Unit tests for config loaders
- Logging format validation
- Utility function tests

### Tier 2 Tests (Core Services)
- Auth middleware integration tests
- Rate limiting behavior tests
- Health check endpoint tests

### Tier 3 Tests (Business Logic)
- Lead enrichment integration tests
- Compliance workflow tests
- Analytics event tracking tests

### Tier 4 Tests (Integrations)
- Email delivery tests (mock SMTP)
- AI API client tests (mock responses)
- UTM parsing tests

---

## Performance Considerations

### Tier 1: Foundation
- Lazy loading for config validators
- Cached logger instances
- Efficient validation schemas

### Tier 2: Core Services
- Auth token validation caching
- Middleware order optimization
- Health check timeout management

### Tier 3: Business Logic
- Analytics event batching
- Lead enrichment parallelization
- Compliance check caching

### Tier 4: Integrations
- Email sending rate limits
- AI API retry logic
- UTM parameter compression

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-11 | FORGE System | Initial tier structure |

---

**Document ID:** FORGE_SHARED_TIER_STRUCTURE  
**Classification:** Internal - Architecture  
**Review Cycle:** Annual
