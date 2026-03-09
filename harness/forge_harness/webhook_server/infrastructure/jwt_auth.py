"""JWT Authentication Infrastructure

JWT token authentication for webhook server API endpoints.
Provides token generation, validation, and refresh capabilities with
refresh token rotation for enhanced security.
"""

import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

try:
    import jwt
    from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False

logger = logging.getLogger(__name__)


class JWTAuthResult(Enum):
    """JWT Authentication result."""
    SUCCESS = "success"
    MISSING_TOKEN = "missing_token"
    INVALID_TOKEN = "invalid_token"
    EXPIRED_TOKEN = "expired_token"
    INVALID_FORMAT = "invalid_format"
    TOKEN_REUSED = "token_reused"


@dataclass
class JWTAuthConfig:
    """JWT Authentication configuration.

    Attributes:
        secret_key: Secret key for JWT signing
        algorithm: JWT algorithm (default: HS256)
        access_token_expire_minutes: Access token expiration time (default: 15 min)
        refresh_token_expire_days: Refresh token expiration time (default: 7 days)
        require_auth: Whether authentication is required
    """
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 15  # 15 minutes (short-lived access token)
    refresh_token_expire_days: int = 7  # 7 days
    require_auth: bool = True

    @classmethod
    def from_env(cls) -> "JWTAuthConfig":
        """Create JWTAuthConfig from environment variables."""
        secret = os.environ.get("FORGE_JWT_SECRET")
        if not secret:
            # Generate a random secret if not provided (for dev only)
            logger.warning("FORGE_JWT_SECRET not set, generating random secret (dev only)")
            secret = secrets.token_urlsafe(32)

        return cls(
            secret_key=secret,
            algorithm=os.environ.get("FORGE_JWT_ALGORITHM", "HS256"),
            access_token_expire_minutes=int(
                os.environ.get("FORGE_JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "15")
            ),
            refresh_token_expire_days=int(
                os.environ.get("FORGE_JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7")
            ),
            require_auth=os.environ.get("FORGE_JWT_REQUIRE_AUTH", "true").lower() in (
                "true", "1", "yes"
            ),
        )


@dataclass
class JWTPayload:
    """JWT Token payload structure.

    Attributes:
        sub: Subject (user_id or service_id)
        exp: Expiration timestamp
        iat: Issued at timestamp
        jti: JWT ID (unique token identifier)
        type: Token type (access or refresh)
        permissions: List of granted permissions
        family_id: Token family ID for rotation tracking (refresh tokens only)
    """
    sub: str
    exp: datetime
    iat: datetime
    jti: str
    type: str  # "access" or "refresh"
    permissions: list[str]
    family_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert payload to dictionary for JWT encoding."""
        result = {
            "sub": self.sub,
            "exp": self.exp,
            "iat": self.iat,
            "jti": self.jti,
            "type": self.type,
            "permissions": self.permissions,
        }
        if self.family_id:
            result["family_id"] = self.family_id
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JWTPayload":
        """Create payload from decoded JWT dictionary."""
        return cls(
            sub=data.get("sub", ""),
            exp=datetime.fromtimestamp(data["exp"], tz=UTC) if "exp" in data else datetime.now(UTC),
            iat=datetime.fromtimestamp(data["iat"], tz=UTC) if "iat" in data else datetime.now(UTC),
            jti=data.get("jti", ""),
            type=data.get("type", "access"),
            permissions=data.get("permissions", []),
            family_id=data.get("family_id"),
        )


@dataclass
class TokenRotationResult:
    """Result of a token rotation operation.

    Attributes:
        result: Authentication result
        access_token: New access token (if successful)
        refresh_token: New refresh token (if successful)
        expires_in: Access token expiration in seconds
        token_type: Token type (Bearer)
    """
    result: JWTAuthResult
    access_token: str | None = None
    refresh_token: str | None = None
    expires_in: int = 900  # 15 minutes default
    token_type: str = "Bearer"


def create_access_token(
    subject: str,
    config: JWTAuthConfig,
    permissions: list[str] | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a new JWT access token.

    Args:
        subject: User or service identifier
        config: JWT configuration
        permissions: List of permissions to grant
        expires_delta: Custom expiration time

    Returns:
        Encoded JWT access token
    """
    if not JWT_AVAILABLE:
        raise RuntimeError("PyJWT library not installed. Install with: pip install PyJWT")

    now = datetime.now(UTC)
    if expires_delta is None:
        expires_delta = timedelta(minutes=config.access_token_expire_minutes)

    payload = JWTPayload(
        sub=subject,
        exp=now + expires_delta,
        iat=now,
        jti=secrets.token_hex(16),
        type="access",
        permissions=permissions or [],
        family_id=None,  # Access tokens don't have family tracking
    )

    token = jwt.encode(payload.to_dict(), config.secret_key, algorithm=config.algorithm)
    logger.debug(f"Created access token for subject={subject}, jti={payload.jti}")
    return token


def create_refresh_token(
    subject: str,
    config: JWTAuthConfig,
    permissions: list[str] | None = None,
    family_id: str | None = None,
) -> str:
    """Create a new JWT refresh token.

    Args:
        subject: User or service identifier
        config: JWT configuration
        permissions: List of permissions to grant
        family_id: Token family ID for rotation tracking (creates new family if None)

    Returns:
        Encoded JWT refresh token
    """
    if not JWT_AVAILABLE:
        raise RuntimeError("PyJWT library not installed. Install with: pip install PyJWT")

    now = datetime.now(UTC)
    expires_delta = timedelta(days=config.refresh_token_expire_days)

    # Create new family if not provided
    if family_id is None:
        family_id = secrets.token_hex(16)

    payload = JWTPayload(
        sub=subject,
        exp=now + expires_delta,
        iat=now,
        jti=secrets.token_hex(16),
        type="refresh",
        permissions=permissions or [],
        family_id=family_id,
    )

    token = jwt.encode(payload.to_dict(), config.secret_key, algorithm=config.algorithm)
    logger.debug(f"Created refresh token for subject={subject}, jti={payload.jti}, family={family_id}")
    return token


def decode_token(token: str, config: JWTAuthConfig) -> tuple[JWTAuthResult, JWTPayload | None]:
    """Decode and validate a JWT token.

    Args:
        token: JWT token string
        config: JWT configuration

    Returns:
        Tuple of (result, payload). Payload is None if result is not SUCCESS.
    """
    if not JWT_AVAILABLE:
        logger.error("PyJWT library not installed")
        return JWTAuthResult.INVALID_TOKEN, None

    try:
        decoded = jwt.decode(token, config.secret_key, algorithms=[config.algorithm])
        payload = JWTPayload.from_dict(decoded)
        logger.debug(f"Successfully decoded token, jti={payload.jti}, type={payload.type}")
        return JWTAuthResult.SUCCESS, payload
    except ExpiredSignatureError:
        logger.warning("Token has expired")
        return JWTAuthResult.EXPIRED_TOKEN, None
    except InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        return JWTAuthResult.INVALID_TOKEN, None
    except Exception as e:
        logger.error(f"Error decoding token: {e}")
        return JWTAuthResult.INVALID_TOKEN, None


def verify_access_token(token: str, config: JWTAuthConfig) -> tuple[JWTAuthResult, JWTPayload | None]:
    """Verify an access token.

    Args:
        token: JWT access token
        config: JWT configuration

    Returns:
        Tuple of (result, payload)
    """
    result, payload = decode_token(token, config)

    if result != JWTAuthResult.SUCCESS:
        return result, None

    if payload.type != "access":
        logger.warning(f"Expected access token, got {payload.type}")
        return JWTAuthResult.INVALID_TOKEN, None

    # Check in-memory revocation list (fast path)
    if is_token_revoked(payload.jti):
        logger.warning(f"Access token jti={payload.jti} has been revoked (in-memory)")
        return JWTAuthResult.INVALID_TOKEN, None

    # Check file-backed revocation store (survives process restarts)
    try:
        from forge_harness.webhook_server.infrastructure.refresh_token_store import (
            get_refresh_token_store,
        )
        if get_refresh_token_store().is_jti_revoked(payload.jti):
            logger.warning(f"Access token jti={payload.jti} has been revoked (file store)")
            return JWTAuthResult.INVALID_TOKEN, None
    except Exception as e:
        logger.warning(f"Could not check file refresh token store: {e}")

    return result, payload


def verify_refresh_token(token: str, config: JWTAuthConfig) -> tuple[JWTAuthResult, JWTPayload | None]:
    """Verify a refresh token.

    Args:
        token: JWT refresh token
        config: JWT configuration

    Returns:
        Tuple of (result, payload)
    """
    result, payload = decode_token(token, config)

    if result != JWTAuthResult.SUCCESS:
        return result, None

    if payload.type != "refresh":
        logger.warning(f"Expected refresh token, got {payload.type}")
        return JWTAuthResult.INVALID_TOKEN, None

    # Check in-memory revocation list first (fast path)
    if is_token_revoked(payload.jti):
        logger.warning(f"Refresh token jti={payload.jti} has been revoked (in-memory)")
        return JWTAuthResult.INVALID_TOKEN, None

    # Check in-memory family revocation
    if payload.family_id and is_token_family_revoked(payload.family_id):
        logger.warning(f"Refresh token family {payload.family_id} has been revoked (in-memory)")
        return JWTAuthResult.TOKEN_REUSED, None

    # Check file-backed store (survives process restarts)
    try:
        from forge_harness.webhook_server.infrastructure.refresh_token_store import (
            get_refresh_token_store,
        )
        store = get_refresh_token_store()
        if store.is_jti_revoked(payload.jti):
            logger.warning(f"Refresh token jti={payload.jti} has been revoked (file store)")
            return JWTAuthResult.INVALID_TOKEN, None
        if payload.family_id and store.is_family_revoked(payload.family_id):
            logger.warning(
                f"Refresh token family {payload.family_id} has been revoked (file store, possible reuse)"
            )
            return JWTAuthResult.TOKEN_REUSED, None
    except Exception as e:
        logger.warning(f"Could not check file refresh token store: {e}")

    return result, payload


def refresh_access_token(
    refresh_token: str,
    config: JWTAuthConfig,
) -> tuple[JWTAuthResult, str | None]:
    """Create a new access token using a refresh token.

    Args:
        refresh_token: Valid JWT refresh token
        config: JWT configuration

    Returns:
        Tuple of (result, new_access_token). Token is None if refresh failed.
    """
    result, payload = verify_refresh_token(refresh_token, config)

    if result != JWTAuthResult.SUCCESS:
        return result, None

    # Create new access token with same subject and permissions
    new_token = create_access_token(
        subject=payload.sub,
        config=config,
        permissions=payload.permissions,
    )

    logger.info(f"Refreshed access token for subject={payload.sub}")
    return JWTAuthResult.SUCCESS, new_token


def rotate_refresh_token(
    refresh_token: str,
    config: JWTAuthConfig,
) -> TokenRotationResult:
    """Rotate a refresh token and issue new tokens.

    Implements refresh token rotation security pattern:
    1. Validates the current refresh token
    2. Revokes the old refresh token (one-time use) in both in-memory and file store
    3. Issues new access and refresh tokens in the same family
    4. Returns error result if token is expired or already used

    Args:
        refresh_token: Current refresh token
        config: JWT configuration

    Returns:
        TokenRotationResult with new tokens or error
    """
    # Verify the refresh token
    result, payload = verify_refresh_token(refresh_token, config)

    if result == JWTAuthResult.EXPIRED_TOKEN:
        return TokenRotationResult(result=JWTAuthResult.EXPIRED_TOKEN)

    if result == JWTAuthResult.TOKEN_REUSED:
        return TokenRotationResult(result=JWTAuthResult.TOKEN_REUSED)

    if result != JWTAuthResult.SUCCESS or payload is None:
        return TokenRotationResult(result=JWTAuthResult.INVALID_TOKEN)

    # Revoke the old refresh token (one-time use) — in-memory + file store
    revoke_token(payload.jti)
    try:
        from forge_harness.webhook_server.infrastructure.refresh_token_store import (
            get_refresh_token_store,
        )
        get_refresh_token_store().revoke_jti(payload.jti)
    except Exception as e:
        logger.warning(f"Could not persist token revocation to file store: {e}")

    # Create new tokens with same family for tracking
    new_access = create_access_token(
        subject=payload.sub,
        config=config,
        permissions=payload.permissions,
    )

    new_refresh = create_refresh_token(
        subject=payload.sub,
        config=config,
        permissions=payload.permissions,
        family_id=payload.family_id,
    )

    logger.info(f"Rotated tokens for subject={payload.sub}, family={payload.family_id}")

    return TokenRotationResult(
        result=JWTAuthResult.SUCCESS,
        access_token=new_access,
        refresh_token=new_refresh,
        expires_in=config.access_token_expire_minutes * 60,
        token_type="Bearer",
    )


def get_token_hash(token: str) -> str:
    """Get a hash of the token for audit/logging purposes.

    Args:
        token: JWT token

    Returns:
        Truncated SHA-256 hash of the token
    """
    return hashlib.sha256(token.encode()).hexdigest()[:16]


# Token blacklist for revoked tokens (in production, use Redis)
_revoked_tokens: set[str] = set()
_revoked_families: set[str] = set()


def revoke_token(jti: str) -> None:
    """Revoke a token by its JTI (in-memory only).

    Args:
        jti: JWT ID to revoke
    """
    _revoked_tokens.add(jti)
    logger.info(f"Revoked token jti={jti}")


def revoke_token_family(family_id: str) -> None:
    """Revoke an entire token family.

    Used when token reuse is detected — revokes entire family to prevent
    further replay attacks. Persists to both in-memory and file store.

    Args:
        family_id: Token family ID to revoke
    """
    _revoked_families.add(family_id)
    logger.warning(f"Revoked token family {family_id} (possible replay attack)")
    try:
        from forge_harness.webhook_server.infrastructure.refresh_token_store import (
            get_refresh_token_store,
        )
        get_refresh_token_store().revoke_family(family_id)
    except Exception as e:
        logger.warning(f"Could not persist family revocation to file store: {e}")


def is_token_revoked(jti: str) -> bool:
    """Check if a token has been revoked (in-memory check only).

    Args:
        jti: JWT ID to check

    Returns:
        True if token is revoked
    """
    return jti in _revoked_tokens


def is_token_family_revoked(family_id: str) -> bool:
    """Check if a token family has been revoked (in-memory check only).

    Args:
        family_id: Token family ID to check

    Returns:
        True if token family is revoked
    """
    return family_id in _revoked_families


def clear_all_revoked_tokens() -> None:
    """Clear all revoked tokens (for testing only)."""
    _revoked_tokens.clear()
    _revoked_families.clear()


def verify_bearer_token_jwt(
    authorization: str | None,
    config: JWTAuthConfig,
) -> tuple[JWTAuthResult, JWTPayload | None]:
    """Verify Bearer token with JWT authentication.

    Args:
        authorization: Authorization header value
        config: JWT configuration

    Returns:
        Tuple of (result, payload)
    """
    if not authorization:
        return JWTAuthResult.MISSING_TOKEN, None

    if not authorization.startswith("Bearer "):
        return JWTAuthResult.INVALID_FORMAT, None

    token = authorization[7:].strip()

    result, payload = verify_access_token(token, config)

    if result == JWTAuthResult.SUCCESS and payload:
        # Double-check in-memory revocation (verify_access_token already does this,
        # but kept here for explicit contract clarity)
        if is_token_revoked(payload.jti):
            logger.warning(f"Token jti={payload.jti} has been revoked")
            return JWTAuthResult.INVALID_TOKEN, None

    return result, payload
