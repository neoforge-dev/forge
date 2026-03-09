"""Authentication API Endpoints

Provides JWT authentication with token management, refresh rotation, and logout.
Uses the jwt_auth infrastructure module.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from forge_harness.webhook_server.core.dependencies import require_permission
from forge_harness.webhook_server.infrastructure.api_key_auth import (
    generate_api_key,
    get_key_hash,
    register_api_key,
)
from forge_harness.webhook_server.infrastructure.jwt_auth import (
    JWTAuthConfig,
    JWTAuthResult,
    create_access_token,
    create_refresh_token,
    revoke_token,
    rotate_refresh_token,
    verify_access_token,
    verify_bearer_token_jwt,
)
from forge_harness.webhook_server.infrastructure.user_store import get_user_store
from forge_harness.webhook_server.services.audit import get_audit_logger

router = APIRouter()


class TokenRequest(BaseModel):
    """Request body for POST /api/auth/token (static webhook-token exchange)."""

    grant_type: str = Field(default="static_token", description="Grant type (static_token or password)")
    token: str | None = Field(None, description="FORGE_WEBHOOK_TOKEN for static_token grant")
    username: str | None = Field(None, description="Username for password grant")
    password: str | None = Field(None, description="Password for password grant")


class LoginRequest(BaseModel):
    # ... (same as before)
    username: str = Field(..., description="Username")
    password: str = Field(..., description="Password")


class APIKeyCreateRequest(BaseModel):
    """Request body for creating an API key."""
    owner: str = Field(..., description="Owner of the API key")
    permissions: list[str] = Field(default=["read"], description="Permissions for the key")
    expires_in_days: int | None = Field(None, description="Expiration in days")


class APIKeyCreateResponse(BaseModel):
    """Response body for creating an API key."""
    api_key: str = Field(..., description="The generated API key (ONLY SHOWN ONCE)")
    key_id: str = Field(..., description="Public identifier for the key")
    owner: str = Field(..., description="Owner of the key")


class LoginResponse(BaseModel):
    """Login response model."""
    access_token: str = Field(..., description="Access token")
    refresh_token: str = Field(..., description="Refresh token")
    expires_in: int = Field(..., description="Access token expiration in seconds")
    token_type: str = Field(default="Bearer", description="Token type")


class RefreshRequest(BaseModel):
    """Refresh token request model."""
    refresh_token: str = Field(..., description="Refresh token to rotate")


class RefreshResponse(BaseModel):
    """Refresh token response model."""
    access_token: str = Field(..., description="New access token")
    refresh_token: str = Field(..., description="New refresh token")
    expires_in: int = Field(..., description="Access token expiration in seconds")
    token_type: str = Field(default="Bearer", description="Token type")


class VerifyResponse(BaseModel):
    """Token verification response."""
    valid: bool = Field(..., description="Whether token is valid")
    token_type: str = Field(..., description="Token type (access/refresh)")
    sub: str = Field(..., description="Subject (user_id)")
    permissions: list[str] = Field(default_factory=list, description="Granted permissions")
    exp: str | None = Field(None, description="Expiration timestamp")


class RevokeResponse(BaseModel):
    """Token revocation response."""
    status: str = Field(..., description="Revocation status")
    message: str = Field(..., description="Status message")


def _get_client_info(request: Request) -> tuple[str, str]:
    """Extract client IP and User-Agent from request."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("User-Agent", "unknown")
    return client_ip, user_agent


def _get_jwt_config() -> JWTAuthConfig:
    """Get JWT configuration from environment."""
    return JWTAuthConfig.from_env()


@router.post("/api/auth/token", response_model=LoginResponse)
async def get_token(request: TokenRequest, http_request: Request) -> LoginResponse:
    """Issue JWT access + refresh token pair.

    Supports two grant types:
    - ``static_token``: Exchange the legacy FORGE_WEBHOOK_TOKEN for JWT tokens.
      Maintains backward compatibility with existing bearer-token clients.
    - ``password``: Authenticate with username + password and receive JWT tokens.

    Returns:
        LoginResponse with access_token, refresh_token, expires_in, token_type
    """
    import secrets as _secrets

    config = _get_jwt_config()
    client_ip, user_agent = _get_client_info(http_request)

    if request.grant_type == "static_token":
        # Exchange existing FORGE_WEBHOOK_TOKEN for JWT tokens
        from forge_harness.webhook_server.infrastructure.auth import AuthConfig
        auth_config = AuthConfig.from_env()

        if not auth_config.bearer_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Static token auth not configured (FORGE_WEBHOOK_TOKEN unset)",
            )
        if not request.token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="token field required for static_token grant",
            )
        if not _secrets.compare_digest(request.token, auth_config.bearer_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid static token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        subject = "forge_client"
        permissions = ["read", "write", "admin"]

    elif request.grant_type == "password":
        if not request.username or not request.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="username and password required for password grant",
            )
        user_store = get_user_store()
        user = user_store.authenticate(request.username, request.password)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        subject = user.username
        permissions = user.permissions

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported grant_type '{request.grant_type}'. Use 'static_token' or 'password'.",
        )

    access_token = create_access_token(subject=subject, config=config, permissions=permissions)
    refresh_token = create_refresh_token(subject=subject, config=config, permissions=permissions)

    audit_logger = get_audit_logger()
    await audit_logger.log(
        action="token_issued",
        actor={"id": subject, "type": "client"},
        target={"id": subject, "type": "client"},
        context={"ip": client_ip, "user_agent": user_agent, "grant_type": request.grant_type},
        source="auth_api",
    )

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=config.access_token_expire_minutes * 60,
        token_type="Bearer",
    )


@router.post("/api/auth/login", response_model=LoginResponse)
async def login(request: LoginRequest, http_request: Request) -> LoginResponse:
    """Authenticate user and issue JWT tokens.
    
    Validates against the UserStore (populated from environment).
    """
    config = _get_jwt_config()
    client_ip, user_agent = _get_client_info(http_request)
    user_store = get_user_store()
    
    # Validate credentials
    user = user_store.authenticate(request.username, request.password)
    
    if not user:
        # Audit log failed login
        audit_logger = get_audit_logger()
        await audit_logger.log(
            action="login_failed",
            actor={"id": request.username, "type": "user"},
            target={"id": request.username, "type": "user"},
            context={"ip": client_ip, "user_agent": user_agent, "reason": "invalid_credentials"},
            status="failure",
            source="auth_api",
        )
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create tokens
    access_token = create_access_token(
        subject=user.username,
        config=config,
        permissions=user.permissions,
    )
    
    refresh_token = create_refresh_token(
        subject=user.username,
        config=config,
        permissions=user.permissions,
    )
    
    # Audit log success
    audit_logger = get_audit_logger()
    await audit_logger.log(
        action="login",
        actor={"id": user.username, "type": "user"},
        target={"id": user.username, "type": "user"},
        context={"ip": client_ip, "user_agent": user_agent},
        source="auth_api",
    )
    
    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=config.access_token_expire_minutes * 60,
        token_type="Bearer",
    )


@router.post("/api/auth/api-keys", response_model=APIKeyCreateResponse)
async def create_api_key_endpoint(
    request: APIKeyCreateRequest,
    http_request: Request,
    user: dict = Depends(require_permission("admin")),
) -> APIKeyCreateResponse:
    """Create a new API key for a service or agent.
    
    Requires 'admin' permission.
    """
    client_ip, user_agent = _get_client_info(http_request)
    
    # Generate new key
    api_key = generate_api_key()
    
    # Register it
    register_api_key(
        key=api_key,
        owner=request.owner,
        permissions=request.permissions,
        expires_in_days=request.expires_in_days,
    )
    
    key_hash = get_key_hash(api_key)
    key_id = key_hash[:12]
    
    # Audit log
    audit_logger = get_audit_logger()
    await audit_logger.log(
        action="api_key_created",
        actor={"id": user["sub"], "type": "user"},
        target={"id": key_id, "type": "api_key"},
        context={
            "owner": request.owner,
            "permissions": request.permissions,
            "expires_in_days": request.expires_in_days,
            "ip": client_ip,
        },
        source="auth_api",
    )
    
    return APIKeyCreateResponse(
        api_key=api_key,
        key_id=key_id,
        owner=request.owner,
    )


@router.post("/api/auth/refresh", response_model=RefreshResponse)
async def refresh_token_endpoint(
    request: RefreshRequest, http_request: Request
) -> RefreshResponse:
    """Rotate refresh token and issue new access token.
    
    Implements refresh token rotation security pattern:
    1. Validates the current refresh token
    2. Revokes the old refresh token (one-time use)
    3. Issues new access and refresh tokens
    4. Returns 401 if refresh token is expired or already used
    
    Args:
        request: Refresh token request
        http_request: FastAPI Request object for client info
        
    Returns:
        RefreshResponse with new tokens
        
    Raises:
        HTTPException: 401 if refresh token is expired, invalid, or reused
    """
    config = _get_jwt_config()
    client_ip, user_agent = _get_client_info(http_request)
    
    # Perform token rotation (one-time use refresh tokens)
    rotation_result = rotate_refresh_token(request.refresh_token, config)
    
    if rotation_result.result == JWTAuthResult.EXPIRED_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    elif rotation_result.result == JWTAuthResult.TOKEN_REUSED:
        # Security violation - token reuse detected
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has already been used",
            headers={"WWW-Authenticate": "Bearer"},
        )
    elif rotation_result.result != JWTAuthResult.SUCCESS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Audit log
    audit_logger = get_audit_logger()
    await audit_logger.log(
        action="token_refresh",
        actor={"id": "user", "type": "user"},  # Extracted from token in rotation
        target={"id": "user", "type": "user"},
        context={"ip": client_ip, "user_agent": user_agent},
        source="auth_api",
    )
    
    return RefreshResponse(
        access_token=rotation_result.access_token,
        refresh_token=rotation_result.refresh_token,
        expires_in=rotation_result.expires_in,
        token_type="Bearer",
    )


@router.post("/api/auth/verify", response_model=VerifyResponse)
async def verify_token(request: RefreshRequest) -> VerifyResponse:
    """Verify a token and return its payload information.
    
    Args:
        request: Token request (accepts either access or refresh token)
        
    Returns:
        Token verification details
    """
    config = _get_jwt_config()
    
    # Try to verify as access token first
    result, payload = verify_access_token(request.refresh_token, config)
    
    if result != JWTAuthResult.SUCCESS:
        # Try as refresh token
        from forge_harness.webhook_server.infrastructure.jwt_auth import verify_refresh_token
        result, payload = verify_refresh_token(request.refresh_token, config)
        token_type = "refresh"
    else:
        token_type = "access"
    
    if result != JWTAuthResult.SUCCESS or payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {result.value}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return VerifyResponse(
        valid=True,
        token_type=token_type,
        sub=payload.sub,
        permissions=payload.permissions,
        exp=payload.exp.isoformat() if payload.exp else None,
    )


@router.post("/api/auth/logout", response_model=RevokeResponse)
async def logout(request: RefreshRequest, http_request: Request) -> RevokeResponse:
    """Logout and revoke the refresh token.
    
    Args:
        request: Refresh token to revoke
        http_request: FastAPI Request object for client info
        
    Returns:
        Revocation status
    """
    config = _get_jwt_config()
    client_ip, user_agent = _get_client_info(http_request)
    
    # Decode token to get JTI for revocation
    from forge_harness.webhook_server.infrastructure.jwt_auth import decode_token
    _, payload = decode_token(request.refresh_token, config)
    
    if payload:
        revoke_token(payload.jti)
        
        # Audit log
        audit_logger = get_audit_logger()
        await audit_logger.log(
            action="logout",
            actor={"id": payload.sub, "type": "user"},
            target={"id": payload.sub, "type": "user"},
            context={"ip": client_ip, "user_agent": user_agent},
            source="auth_api",
        )
        
        return RevokeResponse(
            status="success",
            message="Token successfully revoked",
        )
    
    return RevokeResponse(
        status="not_found",
        message="Token not found or already expired",
    )


@router.get("/api/auth/status")
async def auth_status() -> dict[str, Any]:
    """Get authentication service status.
    
    Returns:
        Authentication service status and configuration
    """
    try:
        config = _get_jwt_config()
        
        return {
            "status": "healthy",
            "service": "jwt-auth",
            "algorithm": config.algorithm,
            "access_token_expire_minutes": config.access_token_expire_minutes,
            "refresh_token_expire_days": config.refresh_token_expire_days,
            "require_auth": config.require_auth,
        }
    except Exception as e:
        return {
            "status": "error",
            "service": "jwt-auth",
            "error": str(e),
        }


@router.get("/api/auth/me")
async def get_current_user(request: Request) -> dict[str, Any]:
    """Get current authenticated user info.
    
    Args:
        request: FastAPI request with Authorization header
        
    Returns:
        User information from JWT token
    """
    config = _get_jwt_config()
    
    # Get Authorization header
    auth_header = request.headers.get("authorization")
    
    result, payload = verify_bearer_token_jwt(auth_header, config)
    
    if result != JWTAuthResult.SUCCESS or payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {result.value}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return {
        "sub": payload.sub,
        "permissions": payload.permissions,
        "token_type": payload.type,
        "expires_at": payload.exp.isoformat() if payload.exp else None,
    }


# =============================================================================
# Legacy Auth Endpoints (static token-based)
# =============================================================================

import secrets
from datetime import UTC, datetime

from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.infrastructure.auth import (
    AuthConfig,
)

logger = get_logger(__name__)
security = HTTPBearer(auto_error=False)


def _get_auth_config() -> AuthConfig:
    """Get auth configuration from environment."""
    return AuthConfig.from_env()


def _is_localhost_request(client_host: str | None, host_header: str) -> bool:
    """Check if the request is from localhost."""
    if not client_host:
        return False
    localhost_patterns = ["localhost", "127.0.0.1", "::1", "0.0.0.0"]
    return any(pattern in client_host.lower() for pattern in localhost_patterns) or any(
        pattern in host_header.lower() for pattern in localhost_patterns
    )


def api_response(data: Any = None, error_code: str | None = None, error_message: str | None = None) -> dict[str, Any]:
    """Create a standardized API response."""
    if error_code or error_message:
        return {
            "success": False,
            "data": None,
            "error": {
                "code": error_code or "UNKNOWN_ERROR",
                "message": error_message or "An error occurred",
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }
    return {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.post("/api/auth/validate")
async def validate_auth_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """Validate authentication token for remote access (legacy endpoint).

    This endpoint is used by the Command Center frontend to validate
    tokens before granting access. Unlike other endpoints, this does
    NOT allow localhost bypass - it always validates the token.

    Returns:
        200: Token is valid
        401: Token is missing or invalid
    """
    _auth_config = _get_auth_config()
    
    # Get token from Authorization header
    if not credentials or not credentials.credentials:
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="MISSING_TOKEN",
                error_message="Authorization token is required",
            ),
        )

    token = credentials.credentials

    # Check if token is configured on server
    if not _auth_config.bearer_token:
        # No token configured - reject all remote auth attempts
        logger.warning("Auth validation attempted but no FORGE_WEBHOOK_TOKEN configured")
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="AUTH_NOT_CONFIGURED",
                error_message="Server authentication is not configured",
            ),
        )

    # Validate token using secure comparison
    if not secrets.compare_digest(token, _auth_config.bearer_token):
        client_ip = request.client.host if request.client else "unknown"
        logger.warning(f"Invalid token validation attempt from {client_ip}")
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="INVALID_TOKEN",
                error_message="Invalid authentication token",
            ),
        )

    # Token is valid
    return JSONResponse(
        content=api_response(
            {
                "valid": True,
                "message": "Authentication successful",
            }
        )
    )


@router.get("/api/legacy/auth/status")
async def auth_status_legacy(request: Request):
    """Check authentication status without validating token (legacy endpoint).

    Returns information about whether auth is required for this client.
    This is useful for the frontend to determine if it should show
    the auth prompt.
    """
    _auth_config = _get_auth_config()
    client_ip = request.client.host if request.client else None
    is_localhost = client_ip in ("127.0.0.1", "localhost", "::1")

    return JSONResponse(
        content=api_response(
            {
                "auth_required": not is_localhost,
                "is_localhost": is_localhost,
                "token_configured": bool(_auth_config.bearer_token),
            }
        )
    )


@router.post("/api/auth/sse-session")
async def create_sse_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """Create short-lived session token for SSE (legacy endpoint).

    Client sends Bearer token in header; server returns session token
    for use in ?token= query param. Prevents long-lived token in URL.
    """
    _auth_config = _get_auth_config()
    
    if not credentials or not credentials.credentials:
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="MISSING_TOKEN",
                error_message="Authorization required",
            ),
        )
    token = credentials.credentials
    if not _auth_config.bearer_token or not secrets.compare_digest(
        token, _auth_config.bearer_token
    ):
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="INVALID_TOKEN",
                error_message="Invalid authentication token",
            ),
        )
    from forge_harness.webhook_server.infrastructure.sse_session import (
        get_sse_session_store,
    )

    store = get_sse_session_store()
    session_token = store.create(token)
    return JSONResponse(
        content=api_response(
            {
                "session_token": session_token,
                "expires_in": 300,
            }
        )
    )


@router.post("/api/legacy/auth/refresh")
async def refresh_token_legacy(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """Refresh access token using refresh token rotation (legacy endpoint).

    Clients send a refresh token in the Authorization header and receive
    a new access token + new refresh token pair. The old refresh token is
    immediately invalidated (single-use rotation).

    Returns:
        200: New access and refresh tokens
        401: Missing or invalid refresh token
    """
    if not credentials or not credentials.credentials:
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="MISSING_TOKEN",
                error_message="Refresh token required in Authorization header",
            ),
        )

    refresh_token_value = credentials.credentials
    config = _get_jwt_config()

    rotation_result = rotate_refresh_token(refresh_token_value, config)

    if rotation_result.result == JWTAuthResult.EXPIRED_TOKEN:
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="TOKEN_EXPIRED",
                error_message="Refresh token has expired",
            ),
        )
    elif rotation_result.result == JWTAuthResult.TOKEN_REUSED:
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="TOKEN_REUSED",
                error_message="Refresh token has already been used",
            ),
        )
    elif rotation_result.result != JWTAuthResult.SUCCESS:
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="INVALID_REFRESH_TOKEN",
                error_message="Invalid refresh token",
            ),
        )

    return JSONResponse(
        content=api_response(
            {
                "access_token": rotation_result.access_token,
                "refresh_token": rotation_result.refresh_token,
                "expires_in": rotation_result.expires_in,
                "token_type": rotation_result.token_type,
            }
        )
    )
