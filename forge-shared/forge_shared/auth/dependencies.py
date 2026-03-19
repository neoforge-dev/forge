"""
FastAPI dependencies for authentication and authorization.

Provides dependency injection functions for protecting routes with JWT
authentication, role-based access control (RBAC), product access control,
and plan tier validation.

Features:
    - JWT authentication with optional/required modes
    - Role-based access control (RBAC)
    - Permission-based authorization
    - Product access control
    - Plan tier validation
    - Forge-core compatible user context

Example:
    ```python
    from fastapi import Depends, FastAPI
    from forge_shared.auth.dependencies import (
        get_current_user,
        get_optional_user,
        require_role,
        require_permission,
        require_product,
        require_plan
    )
    from forge_shared.auth.models import UserRole, Permission, PlanTier

    app = FastAPI()

    # Require authentication
    @app.get("/profile")
    async def profile(user: ForgeUser = Depends(get_current_user)):
        return {"user": user.email}

    # Optional authentication
    @app.get("/public")
    async def public(user: ForgeUser | None = Depends(get_optional_user)):
        return {"authenticated": user is not None}

    # Require specific role
    @app.get("/admin")
    async def admin(user: ForgeUser = Depends(require_role(UserRole.ADMIN))):
        return {"message": "Welcome admin"}

    # Require specific permission
    @app.post("/users")
    async def create_user(user: ForgeUser = Depends(require_permission(Permission.MANAGE_USERS))):
        return {"message": "User created"}

    # Require product access
    @app.get("/interview-simulator")
    async def interview_sim(user: ForgeUser = Depends(require_product("interview-simulator"))):
        return {"message": "Welcome to Interview Simulator"}

    # Require plan tier
    @app.get("/pro-features")
    async def pro_features(user: ForgeUser = Depends(require_plan(PlanTier.PRO))):
        return {"message": "Pro features unlocked"}
    ```
"""

from typing import Annotated, Any, Callable, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from forge_shared.auth.jwt import JWTAuth
from forge_shared.auth.models import ForgeUser, ForgeTokenPayload, Permission, PlanTier, UserRole

# Global JWT auth instance (should be configured on app startup)
_jwt_auth: Optional[JWTAuth] = None

# HTTP Bearer security scheme
security = HTTPBearer(auto_error=False)


def get_jwt_auth() -> JWTAuth:
    """
    Get the global JWT authentication instance.

    Returns:
        JWTAuth instance

    Raises:
        HTTPException: If JWT auth is not configured (500 Internal Server Error)
    """
    global _jwt_auth
    if _jwt_auth is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT authentication not configured. Call set_jwt_auth() on startup.",
        )
    return _jwt_auth


def set_jwt_auth(auth: JWTAuth) -> None:
    """
    Set the global JWT authentication instance.

    Should be called during application startup.

    Args:
        auth: JWTAuth instance to use globally

    Example:
        ```python
        from fastapi import FastAPI
        from forge_shared.auth.jwt import JWTAuth, JWTConfig
        from forge_shared.auth.dependencies import set_jwt_auth

        app = FastAPI()

        @app.on_event("startup")
        async def startup():
            config = JWTConfig(secret_key="your-secret-key")
            auth = JWTAuth(config)
            set_jwt_auth(auth)
        ```
    """
    global _jwt_auth
    _jwt_auth = auth


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    auth: JWTAuth = Depends(get_jwt_auth),
) -> ForgeUser:
    """
    Dependency to get the currently authenticated user.

    This dependency requires a valid JWT token in the Authorization header.
    Returns a ForgeUser instance with forge-core compatibility.

    Args:
        credentials: HTTP bearer credentials from Authorization header
        auth: JWT authentication handler

    Returns:
        Authenticated ForgeUser instance

    Raises:
        HTTPException: If token is missing (401 Unauthorized)
        HTTPException: If token is invalid (401 Unauthorized)

    Example:
        ```python
        @app.get("/profile")
        async def profile(user: ForgeUser = Depends(get_current_user)):
            return {"email": user.email, "domain": user.domain}
        ```
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        token = credentials.credentials
        payload = auth.decode_token(token)
        if payload.type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return payload.to_user()

    except (JWTError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    auth: JWTAuth = Depends(get_jwt_auth),
) -> Optional[ForgeUser]:
    """
    Dependency to get the current user if authenticated, otherwise None.

    This is useful for routes that work for both authenticated and
    unauthenticated users, providing different functionality based on
    authentication status.

    Args:
        credentials: HTTP bearer credentials from Authorization header
        auth: JWT authentication handler

    Returns:
        Authenticated ForgeUser instance or None

    Example:
        ```python
        @app.get("/content")
        async def get_content(user: ForgeUser | None = Depends(get_optional_user)):
            if user:
                return {"content": "premium content", "user": user.email}
            return {"content": "free content"}
        ```
    """
    if credentials is None:
        return None

    try:
        token = credentials.credentials
        payload = auth.decode_token(token)
        return payload.to_user()

    except (JWTError, Exception):
        return None


def require_role(role: UserRole) -> Callable[[ForgeUser], ForgeUser]:
    """
    Dependency factory to require a specific role.

    Creates a FastAPI dependency that checks if the authenticated user
    has the required role.

    Args:
        role: Required role (UserRole enum)

    Returns:
        FastAPI dependency function

    Raises:
        HTTPException: If user doesn't have the required role (403 Forbidden)

    Example:
        ```python
        @app.get("/admin")
        async def admin_route(user: ForgeUser = Depends(require_role(UserRole.ADMIN))):
            return {"message": "Welcome admin"}

        @app.get("/super-admin")
        async def super_admin_route(user: ForgeUser = Depends(require_role(UserRole.SUPER_ADMIN))):
            return {"message": "Welcome super admin"}
        ```
    """

    async def role_dependency(user: ForgeUser = Depends(get_current_user)) -> ForgeUser:
        if not user.has_role(role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role.value}' required",
            )
        return user

    return role_dependency


def require_permission(permission: Permission) -> Callable[[ForgeUser], ForgeUser]:
    """
    Dependency factory to require a specific permission.

    Creates a FastAPI dependency that checks if the authenticated user
    has the required permission.

    Args:
        permission: Required permission (Permission enum)

    Returns:
        FastAPI dependency function

    Raises:
        HTTPException: If user doesn't have the required permission (403 Forbidden)

    Example:
        ```python
        @app.post("/users")
        async def create_user(user: ForgeUser = Depends(require_permission(Permission.MANAGE_USERS))):
            return {"message": "User created"}

        @app.delete("/users/{user_id}")
        async def delete_user(user: ForgeUser = Depends(require_permission(Permission.DELETE_ALL))):
            return {"message": "User deleted"}
        ```
    """

    async def permission_dependency(user: ForgeUser = Depends(get_current_user)) -> ForgeUser:
        if not user.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission.value}' required",
            )
        return user

    return permission_dependency


def require_product(product: str) -> Callable[[ForgeUser], ForgeUser]:
    """
    Dependency factory to require access to a specific product.

    Creates a FastAPI dependency that checks if the authenticated user
    has access to the specified product.

    Args:
        product: Product identifier (e.g., "interview-simulator")

    Returns:
        FastAPI dependency function

    Raises:
        HTTPException: If user doesn't have access to the product (403 Forbidden)

    Example:
        ```python
        @app.get("/interview-simulator")
        async def interview_simulator(user: ForgeUser = Depends(require_product("interview-simulator"))):
            return {"message": "Welcome to Interview Simulator"}

        @app.get("/code-atlas")
        async def code_atlas(user: ForgeUser = Depends(require_product("code-atlas"))):
            return {"message": "Welcome to Code Atlas"}
        ```
    """

    async def product_dependency(user: ForgeUser = Depends(get_current_user)) -> ForgeUser:
        if not user.has_product_access(product):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access to product '{product}' required. Subscribe to access this product.",
            )
        return user

    return product_dependency


def require_plan(minimum_plan: PlanTier) -> Callable[[ForgeUser], ForgeUser]:
    """
    Dependency factory to require a minimum plan tier.

    Creates a FastAPI dependency that checks if the authenticated user
    has the required plan tier or higher. The plan hierarchy is:
    FREE < PRO < ENTERPRISE

    Args:
        minimum_plan: Minimum required plan tier (PlanTier enum)

    Returns:
        FastAPI dependency function

    Raises:
        HTTPException: If user doesn't have the required plan tier (403 Forbidden)

    Example:
        ```python
        @app.get("/pro-features")
        async def pro_features(user: ForgeUser = Depends(require_plan(PlanTier.PRO))):
            return {"message": "Pro features unlocked"}

        @app.get("/enterprise-features")
        async def enterprise_features(user: ForgeUser = Depends(require_plan(PlanTier.ENTERPRISE))):
            return {"message": "Enterprise features unlocked"}
        ```
    """

    async def plan_dependency(user: ForgeUser = Depends(get_current_user)) -> ForgeUser:
        if not user.has_plan_tier(minimum_plan):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Plan tier '{minimum_plan.value}' or higher required. Upgrade to access this feature.",
            )
        return user

    return plan_dependency


def require_product_with_plan(
    product: str,
    minimum_plan: PlanTier,
) -> Callable[[ForgeUser], ForgeUser]:
    """
    Dependency factory to require product access AND minimum plan tier.

    Creates a FastAPI dependency that checks both product access and
    plan tier requirements. This is useful for premium features within
    a product.

    Args:
        product: Product identifier (e.g., "interview-simulator")
        minimum_plan: Minimum required plan tier (PlanTier enum)

    Returns:
        FastAPI dependency function

    Raises:
        HTTPException: If user doesn't have product access (403 Forbidden)
        HTTPException: If user doesn't have required plan tier (403 Forbidden)

    Example:
        ```python
        @app.get("/interview-simulator/advanced-analytics")
        async def advanced_analytics(
            user: ForgeUser = Depends(require_product_with_plan("interview-simulator", PlanTier.PRO))
        ):
            return {"analytics": "Advanced analytics data"}
        ```
    """

    async def combined_dependency(user: ForgeUser = Depends(get_current_user)) -> ForgeUser:
        # Check product access first
        if not user.has_product_access(product):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access to product '{product}' required. Subscribe to access this product.",
            )

        # Check plan tier
        if not user.has_plan_tier(minimum_plan):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Plan tier '{minimum_plan.value}' or higher required. Upgrade to access this feature.",
            )

        return user

    return combined_dependency


def require_admin() -> Callable[[ForgeUser], ForgeUser]:
    """
    Convenience dependency to require admin privileges.

    Shortcut for require_role(UserRole.ADMIN).

    Returns:
        FastAPI dependency function

    Raises:
        HTTPException: If user is not an admin (403 Forbidden)

    Example:
        ```python
        @app.get("/admin/dashboard")
        async def admin_dashboard(user: ForgeUser = Depends(require_admin())):
            return {"message": "Admin dashboard"}
        ```
    """
    return require_role(UserRole.ADMIN)


def require_super_admin() -> Callable[[ForgeUser], ForgeUser]:
    """
    Convenience dependency to require super admin privileges.

    Shortcut for require_role(UserRole.SUPER_ADMIN).

    Returns:
        FastAPI dependency function

    Raises:
        HTTPException: If user is not a super admin (403 Forbidden)

    Example:
        ```python
        @app.get("/super-admin/settings")
        async def super_admin_settings(user: ForgeUser = Depends(require_super_admin())):
            return {"message": "Super admin settings"}
        ```
    """
    return require_role(UserRole.SUPER_ADMIN)


# Legacy dependencies for backward compatibility
async def get_current_user_legacy(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    auth: JWTAuth = Depends(get_jwt_auth),
) -> Any:
    """
    Legacy get_current_user for backward compatibility.

    .. deprecated::
        Use get_current_user instead. Returns ForgeUser.
    """
    return await get_current_user(credentials, auth)


async def get_optional_user_legacy(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    auth: JWTAuth = Depends(get_jwt_auth),
) -> Any:
    """
    Legacy get_optional_user for backward compatibility.

    .. deprecated::
        Use get_optional_user instead. Returns ForgeUser | None.
    """
    return await get_optional_user(credentials, auth)
