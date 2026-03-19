# forge-shared Import Guide

Standardized import patterns for FORGE projects.

## Import Tiers

### Core (always available)

Required modules available in all projects.

```python
# Authentication & JWT
from forge_shared.core.auth import JWTHandler, create_token, verify_token
from forge_shared.core.auth.dependencies import get_current_user

# Analytics & PostHog
from forge_shared.core.analytics import PostHogClient, track_event
from forge_shared.core.analytics.config import PostHogConfig

# Middleware
from forge_shared.core.middleware import rate_limit, cors_config, security_headers
from forge_shared.core.middleware.rate_limit import RateLimitMiddleware

# Configuration
from forge_shared.core.config import load_config, Config
```

### Extended (optional)

Specialized modules for specific use cases.

```python
# COPPA Compliance
from forge_shared.extended.coppa import (
    COPPAComplianceService,
    AgeGroup,
    ConsentStatus,
    ChildProfile,
    ParentalConsent,
)

# GDPR Compliance
from forge_shared.extended.gdpr import (
    GDPRService,
    ConsentType,
    DataExportFormat,
)

# Claims Verification
from forge_shared.extended.verification import (
    ClaimsVerifier,
    EvidenceRecord,
    ClaimType,
)
```

### Exceptions (per-project)

Custom overrides in `forge_shared.exceptions/`.

```
forge_shared/
└── exceptions/
    ├── __init__.py
    ├── custom_auth.py      # Project-specific auth
    └── overrides.py        # Method overrides
```

## Usage Examples

### Basic JWT Auth

```python
from forge_shared.core.auth import JWTHandler

handler = JWTHandler(secret_key="your-secret")
token = handler.create_token({"user_id": "123"})
claims = handler.verify_token(token)
```

### PostHog Analytics

```python
from forge_shared.core.analytics import PostHogClient

client = PostHogClient(
    api_key="phc_xxx",
    host="https://app.posthog.com",
)
client.track_event("user_signup", distinct_id="user_123")
```

### Rate Limiting

```python
from forge_shared.core.middleware import rate_limit

@app.middleware("http")
async def limit_requests(request, call_next):
    return await rate_limit(request, calls=100, period=60)
```

### COPPA Compliance

```python
from forge_shared.extended.coppa import COPPAComplianceService

service = COPPAComplianceService()
result = await service.verify_age(user_id, birth_date)
```

## Version Compatibility

| forge-shared | Python | FastAPI |
|--------------|--------|---------|
| 0.1.x | 3.10+ | 0.100+ |
| 0.2.x | 3.11+ | 0.110+ |

## Changelog

See [CHANGELOG.md](../CHANGELOG.md) for updates.
