"""
Complete FastAPI application example using forge-shared.

This example demonstrates a production-ready FastAPI application with
authentication, analytics, middleware, and configuration.

Run with:
    uvicorn fastapi_app:app --reload
"""

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr

from forge_shared.auth import JWTAuth, get_current_user, require_role
from forge_shared.auth.models import User
from forge_shared.analytics import PostHogClient, AnalyticsMiddleware
from forge_shared.config import BaseConfig, get_config
from forge_shared.middleware import (
    RateLimitMiddleware,
    SecurityMiddleware,
    RequestIDMiddleware,
    CORSMiddleware,
)
from forge_shared.logging import setup_logging, get_logger, LoggingMiddleware

# Initialize configuration
config = get_config()
setup_logging(log_level=config.log_level, log_format=config.log_format)
logger = get_logger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title=config.app_name,
    version=config.app_version,
    debug=config.debug,
)

# Initialize JWT authentication
jwt_auth = JWTAuth(
    secret_key=config.secret_key,
    algorithm=config.jwt_algorithm,
    access_token_expire_minutes=config.jwt_expire_minutes,
)

# Set JWT auth globally
from forge_shared.auth.dependencies import set_jwt_auth

set_jwt_auth(jwt_auth)

# Add middleware
app.add_middleware(
    AnalyticsMiddleware,
    posthog_api_key=config.posthog_api_key or "test-key",
    posthog_host=config.posthog_host,
)
app.add_middleware(
    RateLimitMiddleware,
    redis_url=config.redis_url,
    requests_per_minute=60,
)
app.add_middleware(SecurityMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
)
app.add_middleware(LoggingMiddleware)


# Pydantic models
class LoginRequest(BaseModel):
    """Login request model."""

    email: EmailStr
    password: str


class SignupRequest(BaseModel):
    """Signup request model."""

    email: EmailStr
    password: str
    name: str


class UserResponse(BaseModel):
    """User response model."""

    id: str
    email: str
    roles: list[str]


# Startup event
@app.on_event("startup")
async def startup_event():
    """Run on application startup."""
    logger.info(f"Starting {config.app_name} v{config.app_version}")
    logger.info(f"Environment: {config.environment}")


# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """Run on application shutdown."""
    logger.info("Shutting down application")


# Health check
@app.get("/health")
async def health_check():
    """
    Health check endpoint.

    Returns:
        Health status
    """
    return {"status": "healthy", "version": config.app_version}


# Root endpoint
@app.get("/")
async def root():
    """
    Root endpoint.

    Returns:
        Welcome message
    """
    return {"message": "Welcome to FORGE!"}


# Public endpoints
@app.post("/auth/signup")
async def signup(request: SignupRequest):
    """
    Signup endpoint.

    Args:
        request: Signup request

    Returns:
        Created user and token
    """
    # Track signup event
    from forge_shared.analytics.events import track_event

    await track_event("user_signup", {"email": request.email})

    # In a real app, you would:
    # 1. Validate password strength
    # 2. Check if user exists
    # 3. Hash password
    # 4. Create user in database

    # For demo, create a fake user
    user_id = "user-123"

    # Create JWT token
    access_token = jwt_auth.create_access_token(
        user_id=user_id,
        email=request.email,
        roles=["user"],
        permissions=["read:own", "write:own"],
    )

    refresh_token = jwt_auth.create_refresh_token(user_id=user_id)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": user_id,
            "email": request.email,
            "name": request.name,
        },
    }


@app.post("/auth/login")
async def login(request: LoginRequest):
    """
    Login endpoint.

    Args:
        request: Login request

    Returns:
        Access token and refresh token
    """
    # Track login event
    from forge_shared.analytics.events import track_event

    await track_event("user_login", {"email": request.email})

    # In a real app, you would:
    # 1. Verify user exists
    # 2. Verify password hash
    # 3. Get user roles and permissions

    # For demo, create a fake token
    access_token = jwt_auth.create_access_token(
        user_id="user-123",
        email=request.email,
        roles=["user"],
        permissions=["read:own", "write:own"],
    )

    refresh_token = jwt_auth.create_refresh_token(user_id="user-123")

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


# Protected endpoints
@app.get("/users/me")
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """
    Get current user information.

    Args:
        current_user: Current authenticated user

    Returns:
        User information
    """
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        roles=current_user.roles,
    )


@app.get("/admin")
async def admin_endpoint(admin_user: User = Depends(require_role("admin"))):
    """
    Admin-only endpoint.

    Args:
        admin_user: Admin user

    Returns:
        Admin message
    """
    return {"message": f"Welcome admin {admin_user.email}"}


# Example CRUD endpoints
@app.get("/items")
async def list_items(
    skip: int = 0,
    limit: int = 10,
    current_user: User = Depends(get_current_user),
):
    """
    List items.

    Args:
        skip: Number of items to skip
        limit: Maximum number of items to return
        current_user: Current authenticated user

    Returns:
        List of items
    """
    # Track list items event
    from forge_shared.analytics.events import track_event

    await track_event("items_listed", {"skip": skip, "limit": limit})

    # In a real app, query database
    return {
        "items": [],
        "skip": skip,
        "limit": limit,
        "total": 0,
    }


@app.post("/items")
async def create_item(
    item: dict,
    current_user: User = Depends(get_current_user),
):
    """
    Create item.

    Args:
        item: Item data
        current_user: Current authenticated user

    Returns:
        Created item
    """
    # Track create item event
    from forge_shared.analytics.events import track_event

    await track_event("item_created", {"user_id": current_user.id})

    # In a real app, save to database
    return {
        "id": "item-123",
        **item,
        "owner_id": current_user.id,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "fastapi_app:app",
        host=config.host,
        port=config.port,
        reload=config.debug,
    )
