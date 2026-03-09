"""Shared Dependencies for Webhook Server

Provides common FastAPI dependencies for authentication, rate limiting, etc.
"""

import logging
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer

from forge_harness.error_schema import (
    AUTH_INVALID_TOKEN,
    AUTH_MISSING_CREDENTIALS,
    AUTH_PERMISSION_DENIED,
    AUTH_TOKEN_EXPIRED,
    ForgeError,
)

# Auth configuration
_auth_config: Any = None
_auth_security = HTTPBearer()


def get_auth_config() -> Any:
    """Get authentication configuration."""
    global _auth_config
    if _auth_config is None:
        import os
        from dataclasses import dataclass

        @dataclass
        class ForgeUser:
            """Standardized user context object for authentication.

            Attributes:
                user_id: Unique user identifier
                permissions: List of granted permissions
                authenticated: Whether the user is authenticated
                token_hash: Hash of the authenticated token (for audit)
                client_ip: Client IP address
                timestamp: Authentication timestamp
            """

            user_id: str
            permissions: list[str]
            authenticated: bool
            token_hash: str | None = None
            client_ip: str | None = None
            timestamp: str = ""

        _auth_config = {
            "jwt_secret": os.environ.get("FORGE_JWT_SECRET"),
            "jwt_algorithm": "HS256",
            "token_expire_minutes": 60,
            "allow_localhost": False,  # Security: default to False, require explicit enablement
            "required_permissions": [],
            "forge_user_class": ForgeUser,
        }

    return _auth_config


def verify_auth(request: Request) -> dict[str, Any]:
    """Legacy authentication dependency (aliased to unified auth)."""
    return verify_unified_auth(request)


def verify_jwt_auth(request: Request) -> dict[str, Any]:
    """Verify JWT authentication and return user info.

    Args:
        request: FastAPI request object

    Returns:
        dict: User info with sub, permissions, etc.

    Raises:
        HTTPException: If authentication fails
    """
    from forge_harness.webhook_server.infrastructure.jwt_auth import (
        JWTAuthConfig,
        JWTAuthResult,
        verify_bearer_token_jwt,
    )

    # Get Authorization header
    auth_header = request.headers.get("authorization")

    # Get JWT config
    config = JWTAuthConfig.from_env()

    # If auth not required, return a default user
    if not config.require_auth:
        return {
            "sub": "anonymous",
            "permissions": ["read", "write"],
            "authenticated": False,
        }

    # Verify the token
    result, payload = verify_bearer_token_jwt(auth_header, config)

    if result == JWTAuthResult.MISSING_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ForgeError(code=AUTH_MISSING_CREDENTIALS, message="Missing authentication credentials").to_dict(),
            headers={"WWW-Authenticate": "Bearer"},
        )
    elif result == JWTAuthResult.EXPIRED_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ForgeError(code=AUTH_TOKEN_EXPIRED, message="Token has expired").to_dict(),
            headers={"WWW-Authenticate": "Bearer"},
        )
    elif result != JWTAuthResult.SUCCESS or payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ForgeError(code=AUTH_INVALID_TOKEN, message="Invalid authentication token").to_dict(),
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "sub": payload.sub,
        "permissions": payload.permissions,
        "token_type": payload.type,
        "jti": payload.jti,
        "authenticated": True,
    }


def _log_auth_failure(
    failure_type: str,
    client_ip: str,
    endpoint: str,
    details: str,
) -> None:
    """Log authentication failure for audit purposes.

    Args:
        failure_type: Type of failure (missing_token, invalid_token)
        client_ip: Client IP address
        endpoint: Requested endpoint
        details: Additional details about the failure
    """
    logger = logging.getLogger("forge_harness.auth.audit")
    logger.warning(
        f"AUTH_FAILURE: type={failure_type} ip={client_ip} endpoint={endpoint} details={details}"
    )


async def check_rate_limit(request: Request) -> None:
    """Check rate limit for request.

    Args:
        request: FastAPI request object

    Raises:
        HTTPException: If rate limit exceeded
    """
    from ..infrastructure.rate_limiter import get_rate_limiter

    limiter = get_rate_limiter()
    client_ip = request.client.host if request.client else "unknown"

    if not limiter.is_allowed(client_ip):
        retry_after = limiter.get_retry_after(client_ip)
        raise HTTPException(
            status_code=429,
            detail="Too many requests",
            headers={"Retry-After": str(retry_after)},
        )


def verify_unified_auth(request: Request) -> dict[str, Any]:
    """Unified authentication dependency.
    
    Tries the following in order:
    1. JWT Authentication (Modern)
    2. API Key Authentication (Automated)
    3. Legacy Bearer Token (Backward Compatibility)
    
    Args:
        request: FastAPI request object
        
    Returns:
        dict: User context with subject and permissions
        
    Raises:
        HTTPException: If all authentication methods fail
    """
    from forge_harness.webhook_server.infrastructure.api_key_auth import (
        APIKeyResult,
        verify_api_key,
    )
    from forge_harness.webhook_server.infrastructure.auth import (
        AuthConfig,
        AuthResult,
        verify_bearer_token,
    )
    from forge_harness.webhook_server.infrastructure.jwt_auth import (
        JWTAuthConfig,
        JWTAuthResult,
        verify_bearer_token_jwt,
    )

    client_ip = request.client.host if request.client else "unknown"
    auth_header = request.headers.get("authorization")
    api_key_header = request.headers.get("x-api-key")

    # 0. Check app-level auth config: if require_auth=False, allow all
    app_auth = getattr(request.app.state, "auth_config", None)
    if app_auth and not getattr(app_auth, "require_auth", True):
        return {
            "sub": "anonymous",
            "permissions": ["read", "write", "admin"],
            "auth_method": "no_auth",
            "authenticated": False,
        }

    # 1. Check for API Key first (explicit header)
    if api_key_header:
        res, payload = verify_api_key(api_key_header)
        if res == APIKeyResult.SUCCESS and payload:
            return {
                "sub": payload.owner,
                "permissions": payload.permissions,
                "auth_method": "api_key",
                "authenticated": True,
            }
        elif res != APIKeyResult.MISSING_KEY:
            _log_auth_failure("invalid_api_key", client_ip, request.url.path, f"Result: {res.value}")

    # 2. Check for JWT
    if auth_header and auth_header.lower().startswith("bearer "):
        jwt_config = JWTAuthConfig.from_env()
        res, payload = verify_bearer_token_jwt(auth_header, jwt_config)
        
        if res == JWTAuthResult.SUCCESS and payload:
            return {
                "sub": payload.sub,
                "permissions": payload.permissions,
                "auth_method": "jwt",
                "authenticated": True,
                "jti": payload.jti,
            }
        
        # 3. Fallback to Legacy Bearer Token if JWT failed but token format is correct
        # This allows existing CLI tools using the static token to still work
        # Prefer app.state.auth_config (test-injectable) over from_env()
        legacy_config = app_auth if app_auth else AuthConfig.from_env()
        res_legacy = verify_bearer_token(auth_header, legacy_config, client_ip)
        
        if res_legacy == AuthResult.SUCCESS:
            return {
                "sub": "legacy_client",
                "permissions": ["read", "write"], # Full access for legacy token
                "auth_method": "legacy_token",
                "authenticated": True,
            }
        
        # If it was Bearer but neither JWT nor legacy worked
        if res == JWTAuthResult.EXPIRED_TOKEN:
            _err_code = AUTH_TOKEN_EXPIRED
            _err_msg = "Token has expired"
        else:
            _err_code = AUTH_INVALID_TOKEN
            _err_msg = "Invalid authentication token"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ForgeError(code=_err_code, message=_err_msg).to_dict(),
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 4. Localhost bypass (Dev mode only)
    app_auth = getattr(request.app.state, "auth_config", None)
    if app_auth and app_auth.allow_localhost:
        from forge_harness.webhook_server.infrastructure.auth import _is_localhost_value
        if _is_localhost_value(client_ip):
            return {
                "sub": "localhost",
                "permissions": ["read", "write", "admin"],
                "auth_method": "localhost",
                "authenticated": True,
            }

    # All methods failed
    _log_auth_failure("missing_credentials", client_ip, request.url.path, "No valid auth found")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=ForgeError(
            code=AUTH_MISSING_CREDENTIALS,
            message="Authentication credentials were not provided or are invalid",
        ).to_dict(),
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_permission(permission: str):
    """Create dependency that requires specific permission.

    Args:
        permission: Required permission string

    Returns:
        callable: Dependency function
    """

    async def permission_checker(user: dict = Depends(verify_unified_auth)) -> dict:
        perms = user.get("permissions", [])
        if "admin" in perms:
            return user
        # Check exact permission (e.g. "tasks:write")
        if permission in perms:
            return user
        # Check generic permission (e.g. "write" matches "tasks:write")
        generic = permission.split(":")[-1] if ":" in permission else None
        if generic and generic in perms:
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ForgeError(
                code=AUTH_PERMISSION_DENIED,
                message=f"Permission '{permission}' required",
            ).to_dict(),
        )
        return user

    return permission_checker


def require_jwt_permission(permission: str):
    """Create JWT dependency that requires specific permission.

    Args:
        permission: Required permission string

    Returns:
        callable: Dependency function
    """

    async def permission_checker(user: dict = Depends(verify_jwt_auth)) -> dict:
        if permission not in user.get("permissions", []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ForgeError(
                    code=AUTH_PERMISSION_DENIED,
                    message=f"Permission '{permission}' required",
                ).to_dict(),
            )
        return user

    return permission_checker
