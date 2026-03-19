"""
Migration guide for integrating forge-shared into existing applications.

This guide shows how to incrementally migrate an existing FastAPI application
to use forge-shared components.
"""

# ============================================================================
# STEP 1: Install forge-shared
# ============================================================================
"""
pip install forge-shared
"""

# ============================================================================
# STEP 2: Replace authentication
# ============================================================================

# BEFORE: Custom JWT implementation
"""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer
import jwt

security = HTTPBearer()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, "secret", algorithms=["HS256"])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
"""

# AFTER: Using forge-shared authentication
"""
from forge_shared.auth import JWTAuth, get_current_user
from forge_shared.auth.dependencies import set_jwt_auth

# Initialize JWT auth
jwt_auth = JWTAuth(
    secret_key="your-secret-key",
    algorithm="HS256",
    access_token_expire_minutes=30,
)
set_jwt_auth(jwt_auth)

# Use in routes
@app.get("/protected")
async def protected_route(user: User = Depends(get_current_user)):
    return {"user": user.email}
"""

# ============================================================================
# STEP 3: Replace middleware
# ============================================================================

# BEFORE: Custom middleware implementations
"""
from starlette.middleware.base import BaseHTTPMiddleware

class CustomSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

app.add_middleware(CustomSecurityMiddleware)
"""

# AFTER: Using forge-shared middleware
"""
from forge_shared.middleware import SecurityMiddleware

app.add_middleware(SecurityMiddleware)

# Or with custom configuration
app.add_middleware(
    SecurityMiddleware,
    hsts_max_age=31536000,
    csp_policy="default-src 'self'",
)
"""

# ============================================================================
# STEP 4: Replace configuration
# ============================================================================

# BEFORE: Environment variables scattered throughout code
"""
import os

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "default")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
"""

# AFTER: Using forge-shared configuration
"""
from forge_shared.config import BaseConfig, get_config

class AppConfig(BaseConfig):
    # Additional config fields
    custom_field: str = "default"

config = get_config(AppConfig)

# Use configuration
database_url = config.database_url
secret_key = config.secret_key
is_debug = config.debug
"""

# ============================================================================
# STEP 5: Add analytics
# ============================================================================

# BEFORE: No analytics or custom implementation
"""
# Manual tracking
@app.post("/signup")
async def signup(email: str):
    # Create user...
    # No tracking
    return {"status": "success"}
"""

# AFTER: Using forge-shared analytics
"""
from forge_shared.analytics import PostHogClient, AnalyticsMiddleware

# Add middleware
app.add_middleware(
    AnalyticsMiddleware,
    posthog_api_key="your-api-key",
)

# Track custom events
@app.post("/signup")
async def signup(email: str):
    # Create user...
    from forge_shared.analytics.events import track_event
    await track_event("user_signup", {"email": email})
    return {"status": "success"}
"""

# ============================================================================
# STEP 6: Replace logging
# ============================================================================

# BEFORE: Basic logging setup
"""
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
"""

# AFTER: Using forge-shared logging
"""
from forge_shared.logging import setup_logging, get_logger, LoggingMiddleware

# Setup logging
setup_logging(log_level="INFO", log_format="json")

# Get logger
logger = get_logger(__name__)

# Add middleware for request logging
app.add_middleware(LoggingMiddleware)
"""

# ============================================================================
# STEP 7: Add rate limiting
# ============================================================================

# BEFORE: No rate limiting or custom implementation
"""
# Complex custom rate limiter implementation
"""

# AFTER: Using forge-shared rate limiting
"""
from forge_shared.middleware import RateLimitMiddleware

app.add_middleware(
    RateLimitMiddleware,
    redis_url="redis://localhost:6379",
    requests_per_minute=60,
)
"""

# ============================================================================
# STEP 8: Complete migration example
# ============================================================================

"""
Complete example of a migrated application:

from fastapi import FastAPI
from forge_shared.auth import JWTAuth
from forge_shared.analytics import AnalyticsMiddleware
from forge_shared.config import BaseConfig
from forge_shared.middleware import (
    SecurityMiddleware,
    RequestIDMiddleware,
    RateLimitMiddleware,
)
from forge_shared.logging import setup_logging, LoggingMiddleware

# Setup
config = BaseConfig()
setup_logging(log_level=config.log_level)

app = FastAPI()

# Authentication
jwt_auth = JWTAuth(secret_key=config.secret_key)
from forge_shared.auth.dependencies import set_jwt_auth
set_jwt_auth(jwt_auth)

# Middleware
app.add_middleware(AnalyticsMiddleware, posthog_api_key=config.posthog_api_key)
app.add_middleware(RateLimitMiddleware, redis_url=config.redis_url)
app.add_middleware(SecurityMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(LoggingMiddleware)

# Routes...
"""

# ============================================================================
# MIGRATION CHECKLIST
# ============================================================================

"""
Migration Checklist:

Phase 1 - Setup:
- [ ] Install forge-shared package
- [ ] Update requirements.txt
- [ ] Run tests to ensure compatibility

Phase 2 - Authentication:
- [ ] Replace JWT implementation with forge-shared.auth.JWTAuth
- [ ] Update authentication dependencies
- [ ] Test protected routes
- [ ] Update role/permission checks

Phase 3 - Middleware:
- [ ] Add SecurityMiddleware
- [ ] Add RequestIDMiddleware
- [ ] Add LoggingMiddleware
- [ ] Add RateLimitMiddleware (if needed)
- [ ] Add CORSMiddleware (if needed)

Phase 4 - Configuration:
- [ ] Replace config loading with BaseConfig
- [ ] Move environment variables to .env
- [ ] Test configuration loading

Phase 5 - Analytics:
- [ ] Add AnalyticsMiddleware
- [ ] Track key events
- [ ] Verify PostHog integration

Phase 6 - Testing:
- [ ] Run full test suite
- [ ] Test authentication flow
- [ ] Test rate limiting
- [ ] Test analytics tracking
- [ ] Load testing

Phase 7 - Cleanup:
- [ ] Remove old authentication code
- [ ] Remove old middleware
- [ ] Update documentation
- [ ] Deploy to staging
- [ ] Monitor for issues
"""

# ============================================================================
# COMMON MIGRATION ISSUES
# ============================================================================

"""
Issue 1: Circular imports
Solution: Use dependency injection pattern or lazy imports

Issue 2: Configuration conflicts
Solution: Use BaseConfig with custom fields instead of separate config

Issue 3: Middleware order
Solution: Add middleware in correct order:
  1. Exception handler (first)
  2. Request ID
  3. Security
  4. Rate limiting
  5. Logging
  6. Analytics (last)

Issue 4: Missing dependencies
Solution: Install required dependencies:
  pip install redis posthog

Issue 5: Redis connection
Solution: Ensure Redis is running or configure rate limiter to fail open
"""
