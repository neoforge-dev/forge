# FORGE Shared Services Adoption Guide

This guide describes how to migrate existing FORGE projects to the unified `forge-shared` services layer. Adopting these shared services will eliminate code duplication, improve security, and standardize our infrastructure.

---

## 1. Installation

Add `forge-shared` to your project's dependencies in `pyproject.toml`:

```toml
[project]
dependencies = [
    "forge-shared @ git+https://github.com/neoforge-dev/FORGE.git#subdirectory=forge-shared"
]
```

Or using `uv`:

```bash
uv add "/Users/bogdan/work/FORGE/forge-shared"
```

---

## 2. Shared Services Architecture

The `forge-shared` library provides four core services:

1.  **JWT & Auth**: Standardized authentication compatible with `forge-core`.
2.  **PostHog Analytics**: Unified client with batching and async support.
3.  **Middleware Stack**: Production-ready rate limiting, security headers, and CORS.
4.  **Config Management**: Type-safe, environment-based configuration using Pydantic.

---

## 3. Migration Paths

### 3.1 Authentication (JWT)

**Goal**: Replace custom JWT logic with `forge_shared.auth`.

**Steps:**
1.  Initialize `JWTAuth` with `JWTConfig`.
2.  Use `get_current_user` dependency in your FastAPI routes.
3.  Update your `User` models to inherit from `forge_shared.auth.models.ForgeUser`.

**Example:**
```python
from forge_shared.auth import JWTAuth, JWTConfig, get_current_user
from forge_shared.auth.models import ForgeUser

config = JWTConfig(secret_key=settings.jwt_secret)
auth = JWTAuth(config)

@app.get("/me")
async def read_me(user: ForgeUser = Depends(get_current_user)):
    return user
```

### 3.2 Analytics (PostHog)

**Goal**: Standardize event tracking.

**Steps:**
1.  Configure `PostHogClient`.
2.  Use `analytics.track()` for event capture.

**Example:**
```python
from forge_shared.analytics import PostHogClient

analytics = PostHogClient(api_key=settings.posthog_key)

await analytics.track(
    event="interview_started",
    distinct_id=user.id,
    properties={"type": "behavioral"}
)
```

### 3.3 Middleware Stack

**Goal**: Centralize security and rate limiting.

**Steps:**
1.  Use `MiddlewareRegistry` to apply a standard stack.

**Example:**
```python
from forge_shared.middleware import MiddlewareRegistry

registry = MiddlewareRegistry()
registry.register_rate_limit(redis_url=settings.redis_url)
registry.register_security(allowed_origins=["https://codeswiftr.com"])
registry.register_cors(allow_origins=["*"])

app = FastAPI()
registry.apply_to_app(app)
```

### 3.4 Configuration

**Goal**: Standardize settings management.

**Steps:**
1.  Inherit your project `Settings` from `forge_shared.config.BaseConfig`.

**Example:**
```python
from forge_shared.config import BaseConfig

class Settings(BaseConfig):
    custom_setting: str = Field(..., env="CUSTOM_SETTING")

settings = Settings()
```

---

## 4. Integration Testing

Ensure your project remains stable by running the shared service integration tests:

```bash
cd forge-shared
make test
```

When integrating into your project, use the provided `Mock` classes for unit testing to avoid external dependencies (Redis, PostHog).

---

## 5. Success Criteria

- **Code Reduction**: Aim for 60-80% reduction in auth and middleware boilerplate.
- **Consistency**: All logs, errors, and auth tokens should follow the FORGE standard.
- **Observability**: All projects should report to the central PostHog instance.

---

## 6. References

- [Full Migration Guide](../MIGRATION_GUIDE.md)
- [Architecture Specifications](../forge_shared/README.md)
