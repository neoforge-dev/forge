# forge-shared Quick Start Guide

**Last Updated:** February 10, 2026
**Status:** Sprint 6 - Ready for Adoption
**Related:** [Migration Roadmap](../FORGE_SHARED_MIGRATION_ROADMAP.md)

---

## What is forge-shared?

`forge-shared` is a unified shared services layer that eliminates code duplication across FORGE projects. It provides:

- **Authentication:** JWT validation, rate limiting, plan requirements
- **Analytics:** PostHog event tracking with batching
- **Middleware:** Security headers, CORS, rate limiting, request ID

**Benefit:** Eliminates ~4,050 lines of duplicated code (82% reduction).

---

## Installation

```bash
# Add to your project
uv add forge-shared
```

---

## Authentication

### Product-Level Auth

Protect endpoints for a specific product:

```python
from forge_shared.auth import require_product
from forge_shared.auth import ForgeUser

@router.get("/api/report")
async def get_report(
    user: ForgeUser = Depends(require_product("my-product"))
):
    # User is authenticated for "my-product"
    return {"user_id": user.id, "email": user.email}
```

### Plan Requirements

Require paid plans for premium features:

```python
from forge_shared.auth import require_plan

@router.get("/api/premium")
async def premium_feature(
    user: ForgeUser = Depends(require_plan("my-product", "premium"))
):
    # User has premium plan
    return {"features": ["advanced-analytics"]}
```

### Optional Auth

Public endpoints with optional user context:

```python
from forge_shared.auth import optional_product

@router.get("/api/content")
async def get_content(
    user: ForgeUser | AnonymousUser = Depends(optional_product("my-product"))
):
    if isinstance(user, AnonymousUser):
        return {"content": "public"}
    return {"content": "personalized", "user_id": user.id}
```

---

## Analytics

### Event Tracking

```python
from forge_shared.analytics import track_event

track_event("user_signed_up", {
    "user_id": user.id,
    "plan": "premium",
    "source": "github",
})
```

### User Identification

```python
from forge_shared.analytics import identify_user

identify_user(user.id, {
    "email": user.email,
    "name": user.name,
    "plan": user.plan,
})
```

### Page Views

```python
from forge_shared.analytics import track_page_view

track_page_view("/dashboard", {"user_id": user.id})
```

### Configuration

```python
from forge_shared.analytics import configure_analytics

configure_analytics(
    posthog_key=os.getenv("POSTHOG_KEY"),
    batch_size=100,  # Flush after 100 events
    flush_interval=10,  # Flush every 10 seconds
)
```

---

## Middleware

### Standard Stack (Production)

Apply all recommended middleware at once:

```python
from forge_shared.middleware import apply_standard_stack

app = FastAPI()
apply_standard_stack(
    app,
    redis_url=os.getenv("REDIS_URL"),
    rate_limit_requests_per_minute=60,
    cors_allow_origins=["https://app.example.com"],
    cors_allow_credentials=True,
)
```

### Minimal Stack (Development)

```python
from forge_shared.middleware import apply_minimal_stack

app = FastAPI()
apply_minimal_stack(app)
```

### Custom Configuration

```python
from forge_shared.middleware import MiddlewareRegistry

registry = MiddlewareRegistry()

# Rate limiting
registry.register_rate_limit(
    redis_url=os.getenv("REDIS_URL"),
    requests_per_minute=100,
    burst_size=20,
)

# Security headers
registry.register_security(
    hsts_max_age=63072000,  # 2 years
    csp_enabled=False,  # Disable for compatibility
)

# CORS
registry.register_cors(
    allow_origins=["https://app.example.com"],
    allow_credentials=True,
)

# Request ID for distributed tracing
registry.register_request_id()

# Apply all
registry.apply_to_app(app)
```

---

## Environment Variables

```bash
# Required
POSTHOG_KEY=phc_your_key_here
REDIS_URL=redis://localhost:6379

# Optional
FORGE_WEBHOOK_TOKEN=your_token_here
FORGE_API_URL=http://localhost:8081
```

---

## Migration Checklist

### Before You Start

- [ ] Have `REDIS_URL` available (for rate limiting)
- [ ] Have `POSTHOG_KEY` available (for analytics)
- [ ] Back up your current code

### Migration Steps

1. **Install forge-shared**
   ```bash
   uv add forge-shared
   ```

2. **Update imports** (replace custom implementations)
   ```python
   # Before
   from myapp.auth import get_current_user

   # After
   from forge_shared.auth import require_product
   ```

3. **Update middleware** (in app/main.py)
   ```python
   # Before
   app.add_middleware(CustomMiddleware)

   # After
   from forge_shared.middleware import apply_standard_stack
   apply_standard_stack(app, redis_url=os.getenv("REDIS_URL"))
   ```

4. **Update analytics**
   ```python
   # Before
   import posthog
   posthog.capture("event", {"prop": "value"})

   # After
   from forge_shared.analytics import track_event
   track_event("event", {"prop": "value"})
   ```

5. **Run tests**
   ```bash
   .venv/bin/python -m pytest tests/ -v
   ```

6. **Deploy and verify**

---

## Common Patterns

### Protecting All Routes

```python
from fastapi import APIRouter, Depends
from forge_shared.auth import require_product
from forge_shared.auth import ForgeUser

router = APIRouter()

@router.get("/api/*")  # Requires auth for all routes
async def protected_route(
    user: ForgeUser = Depends(require_product("my-product"))
):
    return {"user": user.email}
```

### Public + Protected Routes

```python
from forge_shared.auth import optional_product, require_product

@router.get("/api/public")
async def public_route(
    user: ForgeUser | AnonymousUser = Depends(optional_product("my-product"))
):
    return {"is_authenticated": not isinstance(user, AnonymousUser)}

@router.get("/api/private")
async def private_route(
    user: ForgeUser = Depends(require_product("my-product"))
):
    return {"user_id": user.id}
```

### Custom Rate Limiting

```python
from forge_shared.middleware import RateLimitMiddleware

# Higher limit for API endpoints
app.add_middleware(
    RateLimitMiddleware,
    redis_url=os.getenv("REDIS_URL"),
    requests_per_minute=100,  # Higher limit
    burst_size=20,
)
```

---

## Troubleshooting

### Import Errors

```python
# If you see: ImportError: cannot import name 'require_product'
# Solution: Make sure forge-shared is installed
uv add forge-shared
```

### Redis Connection Errors

```python
# If you see: Error connecting to Redis
# Solution 1: Check REDIS_URL environment variable
echo $REDIS_URL

# Solution 2: Make sure Redis is running
redis-cli ping  # Should return PONG
```

### Analytics Not Working

```python
# If events aren't appearing in PostHog:
# 1. Check POSTHOG_KEY is set
echo $POSTHOG_KEY

# 2. Flush events manually
from forge_shared.analytics import analytics
analytics.flush()

# 3. Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)
```

---

## Reference Implementations

### Tech Diligence (Complete)
- **Location:** `leanvibe-ai/technical-debt-analyzer`
- **Status:** ✅ Fully migrated
- **Auth:** Using `require_product()` and `require_plan()`
- **Analytics:** Using `track_event()` with batching
- **Middleware:** Using standard stack

### Interview Simulator (Target)
- **Location:** `codeswiftr-com/interview-simulator`
- **Status:** 📋 Planned for P0 migration
- **Priority:** High (production traffic)

### Voice Coach (Target)
- **Location:** `brandfocus-ai/voice-coach`
- **Status:** 📋 Planned for P1 migration
- **Priority:** Medium (simpler auth)

---

## Support

| Issue | Solution |
|-------|----------|
| Installation problems | `uv add forge-shared` |
| Import errors | Check `forge_shared` is in dependencies |
| Redis connection | Verify `REDIS_URL` and Redis is running |
| Rate limiting not working | Check Redis connection |
| Analytics events missing | Flush manually: `analytics.flush()` |
| Security headers missing | Apply middleware stack |
| CORS errors | Check `register_cors()` configuration |

---

## Next Steps

1. **Start small:** Migrate one project (Voice Coach recommended)
2. **Test thoroughly:** Run full test suite
3. **Monitor:** Check analytics events are appearing
4. **Rollout:** Deploy to production and monitor

**For detailed migration planning, see:** [Migration Roadmap](../FORGE_SHARED_MIGRATION_ROADMAP.md)

---

**End of Quick Start Guide**

**Version:** 1.0
**Last Updated:** February 10, 2026
