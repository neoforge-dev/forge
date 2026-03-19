# forge-shared Migration Guide

**Version:** 0.1.0
**Last Updated:** February 2026
**Related Docs:** [Shared Services Plan](../docs/SHARED_SERVICES_PLAN.md), [Sprint 6](../docs/PLAN.md#sprint-6-strategic-optimization)

---

## Table of Contents

1. [Why Migrate to forge-shared](#1-why-migrate-to-forge-shared)
2. [Priority Order](#2-priority-order)
3. [Pre-Migration Checklist](#3-pre-migration-checklist)
4. [Migration Steps](#4-migration-steps)
5. [Testing Requirements](#5-testing-requirements)
6. [Rollback Plan](#6-rollback-plan)
7. [Breaking Changes & Mitigation](#7-breaking-changes--mitigation)
8. [Migration Examples](#8-migration-examples)
9. [Frequently Asked Questions](#9-frequently-asked-questions)

---

## 1. Why Migrate to forge-shared

### 1.1 The Problem

Current FORGE portfolio has significant code duplication across 19+ projects:

| Area | Duplication Found | Estimated Lines |
|------|-------------------|-----------------|
| JWT Authentication | 5+ custom implementations | 800+ lines |
| PostHog Analytics | 4 different patterns | 400+ lines |
| Rate Limiting | 3 implementations (memory, Redis, token bucket) | 600+ lines |
| Security Headers | Inconsistent across projects | 200+ lines |
| CORS Configuration | Per-project implementations | 300+ lines |
| Exception Handling | No standardization | 400+ lines |
| **Total** | | **2,700+ lines duplicated** |

### 1.2 Benefits of Migration

#### Code Reduction

- **50-70% reduction** in authentication-related code
- **40-60% reduction** in middleware code
- **Elimination** of analytics duplication

#### Security Improvements

- Centralized security patches (one fix = all projects updated)
- Consistent security headers across all projects
- Standardized rate limiting prevents bypass vulnerabilities
- Audit trails through centralized logging

#### Operational Benefits

- **60% faster** new project onboarding
- **24/7 autonomous operations** with consistent quality gates
- **Single source of truth** for configuration
- Reduced cognitive load for maintenance

#### Development Velocity

- New features only need to be implemented once
- Shared test suite covers all projects
- Consistent API across all services

### 1.3 Success Metrics

| Metric | Current | Target | Improvement |
|--------|---------|--------|------------|
| Auth code per project | ~200 lines | ~80 lines | 60% reduction |
| Analytics code per project | ~100 lines | ~30 lines | 70% reduction |
| Middleware code per project | ~300 lines | ~150 lines | 50% reduction |
| Test coverage | Variable | 80%+ | Standardized |
| Security vulnerabilities | Variable | 0 critical | Centralized audit |

---

## 2. Priority Order

Projects should migrate based on **impact**, **complexity**, and **maintenance burden**.

### 2.1 Phase 1: Pilot Migrations (Week 3-4)

| Priority | Project | Domain | Reason |
|----------|---------|--------|--------|
| **P0** | Interview Simulator | codeswiftr-com | Live production, high auth complexity |
| **P0** | Voice Coach | brandfocus-ai | Complex analytics, webhook handling |
| **P0** | Technical Debt Analyzer | leanvibe-dev | Core backend services |

#### Selection Criteria Met:

- Active development ongoing
- Well-established test suites
- High value from shared services
- Moderate complexity for pilot

### 2.2 Phase 2: Early Adopters (Week 5-6)

| Priority | Project | Domain | Reason |
|----------|---------|--------|--------|
| **P1** | Code Atlas | codeswiftr-com | Vector DB + auth patterns |
| **P1** | Tech Diligence Snapshot | codeswiftr-com | Production auth needed |
| **P1** | Startup Simulator | codeswiftr-com | Standalone auth + analytics |

### 2.3 Phase 3: Standard Migration (Week 7-10)

| Priority | Project | Domain |
|----------|---------|--------|
| **P2** | Study Flow | thebrightharbor-com |
| **P2** | Code Ship | thebrightharbor-com |
| **P2** | Investor Pitch Coach | brandfocus-ai |
| **P2** | Content Operations | neoforge-dev |

### 2.4 Phase 4: Maintenance Mode (Week 11+)

| Priority | Project | Domain |
|----------|---------|--------|
| **P3** | All other projects | All domains |

---

## 3. Pre-Migration Checklist

### 3.1 Project Assessment

Before starting migration, verify:

- [ ] Project uses Python 3.11+
- [ ] Project uses FastAPI (required for middleware)
- [ ] Tests exist and pass (>70% coverage preferred)
- [ ] Environment variables documented
- [ ] Current auth implementation identified
- [ ] Analytics implementation identified
- [ ] Middleware components identified

### 3.2 Inventory Current Code

```bash
# Count lines of code in auth/analytics/middleware
find . -name "*.py" -type f \
  -path "*/auth/*" -o -path "*/analytics/*" -o -path "*/middleware/*" \
  | xargs wc -l | tail -1
```

### 3.3 Dependencies Check

Ensure forge-shared dependencies do not conflict:

```bash
# Check current project dependencies
cat pyproject.toml | grep -E "fastapi|pydantic|pyjwt|posthog|redis"

# Verify forge-shared compatible versions
# forge-shared requires:
# - fastapi>=0.104.0
# - pydantic>=2.5.0
# - pyjwt>=2.8.0
# - posthog>=3.5.0
# - redis>=5.0.0
```

### 3.4 Environment Variables Required

After migration, these env vars must be set:

```bash
# Authentication
FORGE_SHARED_JWT_SECRET=your-secret-here
FORGE_SHARED_JWT_ALGORITHM=HS256
FORGE_SHARED_CORE_AUTH_URL=https://api.codeswiftr.com

# Analytics
FORGE_SHARED_POSTHOG_KEY=phc_xxx
FORGE_SHARED_POSTHOG_HOST=https://eu.i.posthog.com

# Rate Limiting (optional, memory fallback available)
FORGE_SHARED_RATE_LIMIT_BACKEND=redis  # or 'memory'
FORGE_SHARED_REDIS_URL=redis://localhost:6379

# Logging
FORGE_SHARED_LOG_LEVEL=INFO
FORGE_SHARED_LOG_FORMAT=json  # or 'text'
```

---

## 4. Migration Steps

### Step 1: Add forge-shared Dependency

**Option A: Edit pyproject.toml (Recommended)**

```toml
[project]
dependencies = [
    # Existing dependencies...
    "forge-shared @ git+https://github.com/forge-dev/forge-shared.git@v0.1.0",
]
```

**Option B: Edit uv.lock (After uv sync)**

```bash
cd /path/to/project
uv add /Users/bogdan/work/FORGE/forge-shared
```

### Step 2: Update Auth Implementation

**Before (custom JWT):**

```python
# old_auth.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from pydantic import BaseModel

security = HTTPBearer()

class User(BaseModel):
    id: str
    email: str

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> User:
    try:
        payload = jwt.decode(
            credentials.credentials,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )
        return User(**payload)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
```

**After (forge-shared):**

```python
from fastapi import FastAPI, Depends
from forge_shared.auth import JWTAuth, get_current_user, User

auth = JWTAuth(
    secret_key="your-jwt-secret",
    algorithm="HS256",
    auth_url="https://api.codeswiftr.com"
)

app = FastAPI()

@app.get("/protected")
async def protected_route(user: User = Depends(get_current_user)):
    return {"user_id": user.id, "email": user.email}
```

### Step 3: Update Analytics Implementation

**Before (custom PostHog):**

```python
# old_analytics.py
from posthog import Posthog

posthog = Posthog(
    api_key="phc_xxx",
    host="https://eu.i.posthog.com"
)

def track_signup(user_id: str, email: str):
    posthog.capture(
        user_id,
        event="user_signed_up",
        properties={"email": email}
    )
```

**After (forge-shared):**

```python
from forge_shared.analytics import PostHogClient, track_event

analytics = PostHogClient(
    api_key="phc_xxx",
    host="https://eu.i.posthog.com"
)

# Simple tracking
track_event("user_signed_up", user_id="123", email="user@example.com")

# Or use the client directly
analytics.capture(
    distinct_id="123",
    event="user_signed_up",
    properties={"email": "user@example.com"}
)
```

### Step 4: Update Middleware

**Before (per-project middleware):**

```python
# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom rate limiting (scattered implementation)
class RateLimitMiddleware(BaseHTTPMiddleware):
    ...
```

**After (forge-shared):**

```python
from fastapi import FastAPI
from forge_shared.middleware import (
    MiddlewareRegistry,
    register_rate_limit,
    register_security,
    register_cors,
    register_request_id,
)

registry = MiddlewareRegistry()

# Configure all middleware
registry.register_rate_limit(
    redis_url="redis://localhost:6379",
    requests_per_minute=60
)
registry.register_security(
    allowed_origins=["https://app.example.com"],
    hsts_max_age=31536000
)
registry.register_cors(
    allow_origins=["https://app.example.com"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
registry.register_request_id()

app = FastAPI()
registry.apply_to_app(app)
```

### Step 5: Update Configuration

**Before (project config.py):**

```python
# old_config.py
import os
from pydantic import BaseSettings

class Settings(BaseSettings):
    jwt_secret: str = os.getenv("JWT_SECRET")
    posthog_key: str = os.getenv("POSTHOG_KEY")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

settings = Settings()
```

**After (forge-shared):**

```python
from forge_shared.config import BaseConfig, get_config

# Option A: Using BaseConfig
class AppConfig(BaseConfig):
    jwt_secret: str
    posthog_key: str
    redis_url: str = "redis://localhost:6379"
    log_level: str = "INFO"

config = get_config(AppConfig)

# Option B: Using environment variables directly
from forge_shared.config import load_env_config

config = load_env_config(prefix="MY_APP_")
# Reads MY_APP_JWT_SECRET, MY_APP_POSTHOG_KEY, etc.
```

### Step 6: Update Imports

Migrate all imports from old modules to forge-shared:

| Old Module | New Module |
|------------|------------|
| `project.auth.jwt` | `forge_shared.auth` |
| `project.middleware.rate_limit` | `forge_shared.middleware` |
| `project.core.analytics` | `forge_shared.analytics` |
| `project.config` | `forge_shared.config` |
| `project.utils.headers` | `forge_shared.utils` |

---

## 5. Testing Requirements

### 5.1 Pre-Migration Testing

Ensure existing tests pass before migration:

```bash
# Run existing test suite
cd /path/to/project
uv run pytest -v --tb=short

# Check coverage
uv run pytest --cov --cov-report=term-missing

# Verify all tests pass
# Resolution: Fix failing tests before proceeding
```

### 5.2 Post-Migration Testing

#### Unit Tests

Verify individual components:

```python
# tests/test_auth_migration.py
import pytest
from forge_shared.auth import JWTAuth, User

@pytest.fixture
def auth():
    return JWTAuth(secret_key="test-secret", algorithm="HS256")

def test_token_validation(auth):
    token = auth.create_access_token(user_id="123", email="test@example.com")
    payload = auth.validate_token(token)
    assert payload.sub == "123"
    assert payload.email == "test@example.com"

def test_user_dependency(client, auth):
    # Test FastAPI dependency injection
    response = client.get("/protected", headers={"Authorization": "Bearer token"})
    assert response.status_code == 200
```

#### Integration Tests

Verify end-to-end flows:

```python
# tests/test_integration.py
@pytest.mark.integration
async def test_full_auth_flow(auth, client):
    # 1. Create user
    user = await create_test_user()

    # 2. Get token
    token = auth.create_access_token(
        user_id=user.id,
        email=user.email,
        products=["interview-simulator"]
    )

    # 3. Access protected route
    response = client.get(
        "/api/v1/interviews",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
```

#### E2E Tests

Verify complete user journeys:

```bash
# Run with Playwright or similar
pytest tests/e2e/ -v

# Verify:
# - Authentication flow works
# - Analytics events fire correctly
# - Rate limiting applies
# - Error handling works
```

### 5.3 Performance Testing

Compare performance before/after:

```python
# benchmarks/test_auth_benchmark.py
import time
import pytest

@pytest.fixture
def auth():
    return JWTAuth(secret_key="benchmark-secret")

def test_token_creation_benchmark(auth, benchmark):
    def create_tokens():
        for _ in range(100):
            auth.create_access_token(user_id="123")

    benchmark(create_tokens)
    # Target: < 100ms for 100 token creations

def test_token_validation_benchmark(auth, benchmark):
    token = auth.create_access_token(user_id="123")

    def validate():
        for _ in range(100):
            auth.validate_token(token)

    benchmark(validate)
    # Target: < 50ms for 100 validations
```

### 5.4 Test Coverage Requirements

| Component | Minimum Coverage | Target Coverage |
|-----------|------------------|-----------------|
| Authentication | 80% | 90% |
| Analytics | 75% | 85% |
| Middleware | 80% | 90% |
| Configuration | 90% | 95% |

---

## 6. Rollback Plan

### 6.1 When to Rollback

Rollback immediately if:

- Critical tests fail (>10% of test suite)
- Authentication breaks (users locked out)
- Performance degrades (>50% slower response times)
- Security vulnerabilities introduced
- Analytics events not firing correctly

### 6.2 Rollback Steps

#### Option A: Feature Flag (Recommended)

```python
# app/main.py
from functools import partial
from fastapi import FastAPI

app = FastAPI()

# Conditional import based on feature flag
USE_FORGE_SHARED = os.getenv("FORGE_SHARED_ENABLED", "true").lower() == "true"

if USE_FORGE_SHARED:
    from forge_shared.auth import JWTAuth, get_current_user as get_user_fs
    get_current_user = get_user_fs
else:
    from app.auth.legacy import get_current_user as get_user_legacy
    get_current_user = get_user_legacy

# Toggle via environment variable
# FORGE_SHARED_ENABLED=true  -> use forge-shared
# FORGE_SHARED_ENABLED=false -> use legacy auth
```

#### Option B: Git Revert

```bash
# Quick revert to pre-migration state
git revert --no-commit <merge-commit>
git revert --no-commit <migration-commit-1>
git revert --no-commit <migration-commit-n>
git commit -m "ROLLBACK: Revert forge-shared migration"
```

#### Option C: Dependency Revert

```bash
# Remove forge-shared and restore original dependencies
uv remove forge-shared
uv sync

# Restore any modified files from backup
git checkout -- app/auth/ app/middleware/ app/analytics/
```

### 6.3 Rollback Verification

After rollback, verify:

```bash
# 1. All tests pass
uv run pytest -v --tb=short

# 2. Authentication works
curl -X POST http://localhost:8000/auth/token -d "..."

# 3. Analytics events fire
# Check PostHog dashboard

# 4. No 5xx errors in logs
tail -1000 logs/app.log | grep "5[0-9][0-9]"
```

### 6.4 Rollback Timeline

| Issue Severity | Detection | Decision Time | Rollback Time |
|----------------|-----------|---------------|---------------|
| Critical (auth broken) | Immediate | 15 minutes | 30 minutes |
| High (performance) | Monitoring | 1 hour | 2 hours |
| Medium (tests failing) | CI/CD | 2 hours | 4 hours |

---

## 7. Breaking Changes & Mitigation

### 7.1 Token Structure Changes
**Change**: `forge-shared` enforces a specific JWT payload structure (`sub`, `email`, `domain`, `plan`, `products`, `roles`, `permissions`).
**Impact**: Legacy tokens may fail validation if missing these fields.
**Mitigation**: Use `LegacyJWTAuth` for an interim period or implement a "Token Migration" grace period where both structures are accepted.

### 7.2 Middleware Initialization
**Change**: Middleware must now be registered via `MiddlewareRegistry` or added using `app.add_middleware` with the shared classes.
**Impact**: Direct instantiation of middleware without the `app` argument will fail.
**Mitigation**: Follow the `MiddlewareRegistry` pattern shown in Section 4, Step 4.

### 7.3 PostHog Client Async
**Change**: `PostHogClient.track` and `identify` are now `async` methods.
**Impact**: Synchronous code calling these methods will not send events.
**Mitigation**: Await all analytics calls or use `asyncio.create_task` for "fire and forget" tracking.

---

## 8. Migration Examples

### 8.1 Interview Simulator Migration

**Location:** `codeswiftr-com/interview-simulator/`

**Steps Completed:**
1. [ ] Add forge-shared to dependencies
2. [ ] Replace `auth/dependencies.py` with forge-shared imports
3. [ ] Replace `core/analytics.py` with PostHogClient
4. [ ] Update `main.py` middleware configuration
5. [ ] Update environment variables
6. [ ] Run full test suite
7. [ ] Deploy to staging
8. [ ] Monitor metrics
9. [ ] Deploy to production

**Estimated Time:** 4-6 hours

### 8.2 Voice Coach Migration

**Location:** `brandfocus-ai/voice-coach/app/`

**Steps Completed:**
1. [ ] Add forge-shared to dependencies
2. [ ] Replace auth dependencies
3. [ ] Migrate webhook handling
4. [ ] Update S3 storage references
5. [ ] Replace rate limiting
6. [ ] Run integration tests
7. [ ] Deploy to staging
8. [ ] Test voice recording upload
9. [ ] Deploy to production

**Estimated Time:** 6-8 hours

### 8.3 Technical Debt Analyzer Migration

**Location:** `leanvibe-dev/technical-debt-analyzer/`

**Steps Completed:**
1. [ ] Add forge-shared to dependencies
2. [ ] Replace auth service
3. [ ] Update analytics calls
4. [ ] Migrate middleware stack
5. [ ] Update configuration
6. [ ] Run test suite
7. [ ] Deploy to staging
8. [ ] Verify analysis reports
9. [ ] Deploy to production

**Estimated Time:** 4-5 hours

---

## 9. Frequently Asked Questions

### Q1: Can I use forge-shared alongside existing code?

**Yes.** You can migrate incrementally:

```python
# Use forge-shared for new routes
from forge_shared.auth import get_current_user

@app.get("/new-route")
async def new_route(user: User = Depends(get_current_user)):
    ...

# Keep old routes working
from app.auth.legacy import get_current_user as legacy_get_user

@app.get("/old-route")
async def old_route(user = Depends(legacy_get_user)):
    ...
```

### Q2: What if my project has custom auth requirements?

**Extend forge-shared:**

```python
# app/auth/custom.py
from forge_shared.auth import JWTAuth, User

class CustomJWTAuth(JWTAuth):
    async def validate_mfa(self, token: str) -> bool:
        # Custom MFA validation
        ...

# Use custom auth
auth = CustomJWTAuth(secret_key="...")
```

### Q3: How do I handle project-specific configuration?

**Use the config system:**

```python
from forge_shared.config import BaseConfig

class InterviewSimulatorConfig(BaseConfig):
    # forge-shared settings
    jwt_secret: str
    posthog_key: str

    # Project-specific settings
    openai_api_key: str
    interview_timeout_minutes: int = 30
```

### Q4: What about projects not using FastAPI?

**Currently, forge-shared requires FastAPI** for middleware components.

For non-FastAPI projects:
- Use individual modules (auth, analytics) - these work standalone
- Middleware requires FastAPI/Starlette
- Consider migrating to FastAPI for full benefits

### Q5: How do I contribute to forge-shared?

**Development workflow:**

```bash
# 1. Fork and clone forge-shared
git clone https://github.com/forge-dev/forge-shared.git
cd forge-shared

# 2. Create development environment
uv venv
source .venv/bin/activate
uv sync --extra dev

# 3. Make changes
# Add tests for new functionality

# 4. Run tests
uv run pytest

# 5. Submit PR
git checkout -b feature/new-feature
git add .
git commit -m "feat: description"
git push origin feature/new-feature
```

### Q6: How is forge-shared versioned?

**Semantic versioning:**

- **Major:** Breaking changes to API
- **Minor:** New features (backward compatible)
- **Patch:** Bug fixes

**Migration between versions:**

```bash
# Upgrade minor version (safe)
uv add "forge-shared>=0.2.0,<0.3.0"

# Upgrade major version (review migration guide)
uv add "forge-shared>=1.0.0,<2.0.0"
```

---

## Quick Reference

### Key Imports

```python
# Authentication
from forge_shared.auth import JWTAuth, get_current_user, require_role, User

# Analytics
from forge_shared.analytics import PostHogClient, track_event

# Middleware
from forge_shared.middleware import (
    MiddlewareRegistry,
    RateLimitMiddleware,
    SecurityMiddleware,
    CORSMiddleware,
)

# Configuration
from forge_shared.config import BaseConfig, get_config

# UTM Tracking
from forge_shared.utm import UTMMiddleware, get_utm_params

# Health Checks
from forge_shared.health import create_health_router
```

### Environment Variables

```bash
# Required
FORGE_SHARED_JWT_SECRET
FORGE_SHARED_POSTHOG_KEY

# Optional
FORGE_SHARED_JWT_ALGORITHM=HS256
FORGE_SHARED_POSTHOG_HOST=https://eu.i.posthog.com
FORGE_SHARED_RATE_LIMIT_BACKEND=memory
FORGE_SHARED_REDIS_URL=redis://localhost:6379
FORGE_SHARED_LOG_LEVEL=INFO
```

### Support

- **Documentation:** [forge-shared.readthedocs.io](https://forge-shared.readthedocs.io)
- **Issues:** [GitHub Issues](https://github.com/forge-dev/forge-shared/issues)
- **Slack:** #forge-shared on FORGE workspace

---

**Document Version:** 0.1.0
**Last Updated:** February 2026
**Maintained By:** FORGE Team
