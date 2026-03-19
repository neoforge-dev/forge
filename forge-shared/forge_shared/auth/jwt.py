"""
JWT authentication handler for token creation and validation.

This module provides a comprehensive JWT authentication implementation with
forge-core compatibility, supporting both HS256 and RS256 algorithms.

Security Features:
    - HS256 (HMAC-SHA256) for symmetric key encryption
    - RS256 (RSA-SHA256) for asymmetric key encryption
    - Token expiration validation
    - Issuer and audience validation
    - Comprehensive error handling

Forge-Core Compatibility:
    Tokens follow the forge-core structure for cross-service authentication:
    {
        "sub": "user-uuid",
        "email": "user@example.com",
        "domain": "codeswiftr.com",
        "plan": "pro",
        "products": ["interview-simulator", "code-atlas"],
        "iat": 1734134400,
        "exp": 1734136200,
        "type": "access"
    }

Example:
    ```python
    from forge_shared.auth.jwt import JWTAuth, JWTConfig
    from forge_shared.auth.models import UserRole, Permission, PlanTier

    config = JWTConfig(
        secret_key="your-secret-key",
        algorithm="HS256",
        access_token_expire_minutes=30
    )

    auth = JWTAuth(config)

    # Create access token
    token = auth.create_access_token(
        user_id="550e8400-e29b-41d4-a716-446655440000",
        email="user@codeswiftr.com",
        domain="codeswiftr.com",
        plan=PlanTier.PRO,
        products=["interview-simulator"],
        roles=[UserRole.USER],
        permissions=[Permission.READ_OWN]
    )

    # Validate token
    payload = auth.decode_token(token)
    user = payload.to_user()
    ```
"""

import time
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from jose import JWTError, jwt, ExpiredSignatureError
from pydantic import BaseModel, Field, field_validator

from forge_shared.auth.models import (
    ForgeTokenPayload,
    ForgeUser,
    Permission,
    PlanTier,
    RefreshToken,
    RefreshTokenStatus,
    TokenRotationResult,
    UserRole,
)


class JWTConfig(BaseModel):
    """
    JWT configuration settings.

    Attributes:
        secret_key: Secret key for HS256 or path to private key for RS256
        public_key: Optional public key for RS256 validation
        algorithm: JWT algorithm ("HS256" or "RS256")
        access_token_expire_minutes: Access token expiration time in minutes
        refresh_token_expire_days: Refresh token expiration time in days
        issuer: Optional issuer claim for tokens
        audience: Optional audience claim for tokens
    """

    secret_key: str = Field(..., description="Secret key or path to private key")
    public_key: Optional[str] = Field(None, description="Public key for RS256")
    algorithm: str = Field(default="HS256", description="JWT algorithm")
    access_token_expire_minutes: int = Field(
        default=30, description="Access token expiration in minutes"
    )
    refresh_token_expire_days: int = Field(
        default=7, description="Refresh token expiration in days"
    )
    issuer: str = Field(default="forge-core", description="Issuer claim")
    audience: str = Field(default="forge-services", description="Audience claim")
    verify_issuer: bool = Field(default=True, description="Validate issuer claim")
    verify_audience: bool = Field(default=True, description="Validate audience claim")

    @field_validator("algorithm")
    @classmethod
    def validate_algorithm(cls, v: str) -> str:
        """
        Validate JWT algorithm.

        Args:
            v: Algorithm string

        Returns:
            Validated algorithm

        Raises:
            ValueError: If algorithm is not supported
        """
        if v not in ("HS256", "RS256"):
            raise ValueError(f"Unsupported algorithm: {v}. Use HS256 or RS256.")
        return v

    @field_validator("access_token_expire_minutes", "refresh_token_expire_days")
    @classmethod
    def validate_expiration(cls, v: int) -> int:
        """
        Validate expiration times are positive.

        Args:
            v: Expiration time value

        Returns:
            Validated expiration time

        Raises:
            ValueError: If expiration time is not positive
        """
        if v <= 0:
            raise ValueError("Expiration time must be positive")
        return v


class JWTAuth:
    """
    JWT authentication handler with forge-core compatibility.

    Supports both HS256 (symmetric) and RS256 (asymmetric) algorithms for
    token creation and validation. Designed for cross-service authentication
    within the FORGE portfolio ecosystem.

    Attributes:
        config: JWT configuration settings

    Example:
        ```python
        # HS256 (symmetric)
        config = JWTConfig(secret_key="your-secret-key", algorithm="HS256")
        auth = JWTAuth(config)

        # RS256 (asymmetric)
        config = JWTConfig(
            secret_key="/path/to/private.pem",
            public_key="/path/to/public.pem",
            algorithm="RS256"
        )
        auth = JWTAuth(config)
        ```
    """

    def __init__(self, config: JWTConfig) -> None:
        """
        Initialize JWT authentication handler.

        Args:
            config: JWT configuration settings

        Raises:
            ValueError: If configuration is invalid
        """
        self.config = config
        self._private_key: Optional[str] = None
        self._public_key: Optional[str] = None

        # Load keys for RS256
        if config.algorithm == "RS256":
            self._load_rsa_keys()

    def _load_rsa_keys(self) -> None:
        """
        Load RSA keys from files or config.

        Raises:
            FileNotFoundError: If key files don't exist
            ValueError: If keys are invalid
        """
        # Check if secret_key is a file path
        secret_path = Path(self.config.secret_key)
        if secret_path.exists():
            try:
                self._private_key = secret_path.read_text()
            except Exception as e:
                raise ValueError(f"Failed to read private key: {e}") from e
        else:
            # Use secret_key directly as the key
            self._private_key = self.config.secret_key

        # Load public key
        if self.config.public_key:
            public_path = Path(self.config.public_key)
            if public_path.exists():
                try:
                    self._public_key = public_path.read_text()
                except Exception as e:
                    raise ValueError(f"Failed to read public key: {e}") from e
            else:
                self._public_key = self.config.public_key
        else:
            raise ValueError("public_key is required for RS256 algorithm")

    def _get_signing_key(self) -> str:
        """
        Get the signing key.

        Returns:
            Signing key string
        """
        return self._private_key if self.config.algorithm == "RS256" else self.config.secret_key

    def _get_validation_key(self) -> str:
        """
        Get the validation key.

        Returns:
            Validation key string
        """
        if self.config.algorithm == "RS256":
            return self._public_key or ""  # type: ignore[return-value]
        return self.config.secret_key

    def create_access_token(
        self,
        user_id: str,
        email: str,
        domain: str,
        plan: PlanTier = PlanTier.FREE,
        products: list[str] | None = None,
        roles: list[UserRole] | None = None,
        permissions: list[Permission] | None = None,
        extra_claims: dict[str, object] | None = None,
    ) -> str:
        """
        Create an access token with forge-core compatibility.

        Args:
            user_id: User ID (UUID)
            email: User email address
            domain: User's domain within FORGE portfolio
            plan: User's subscription plan tier
            products: List of products user has access to
            roles: List of user roles
            permissions: List of user permissions
            extra_claims: Additional claims to include in token

        Returns:
            Encoded JWT access token

        Raises:
            ValueError: If required fields are missing
        """
        # Set defaults
        products = products or []
        roles = roles or [UserRole.USER]
        permissions = permissions or [Permission.READ_OWN]

        # Calculate expiration
        now = datetime.now(timezone.utc)
        expire_time = now + timedelta(minutes=self.config.access_token_expire_minutes)

        # Build payload
        payload_data = {
            "sub": user_id,
            "email": email,
            "domain": domain,
            "plan": plan.value if isinstance(plan, PlanTier) else plan,
            "products": products,
            "roles": [r.value if isinstance(r, UserRole) else r for r in roles],
            "permissions": [p.value if isinstance(p, Permission) else p for p in permissions],
            "iat": int(now.timestamp()),
            "exp": int(expire_time.timestamp()),
            "iss": self.config.issuer,
            "aud": self.config.audience,
            "type": "access",
        }

        # Add extra claims
        if extra_claims:
            payload_data.update(extra_claims)

        # Encode token
        try:
            token = jwt.encode(
                payload_data, self._get_signing_key(), algorithm=self.config.algorithm
            )
            return token  # type: ignore[return-value]
        except Exception as e:
            raise ValueError(f"Failed to encode token: {e}") from e

    def create_refresh_token(
        self,
        user_id: str,
        extra_claims: dict[str, object] | None = None,
    ) -> str:
        """
        Create a refresh token.

        Refresh tokens have minimal claims and longer expiration.

        Args:
            user_id: User ID (UUID)
            extra_claims: Additional claims to include in token

        Returns:
            Encoded JWT refresh token

        Raises:
            ValueError: If token creation fails
        """
        now = datetime.now(timezone.utc)
        expire_time = now + timedelta(days=self.config.refresh_token_expire_days)

        payload_data = {
            "sub": user_id,
            "iat": int(now.timestamp()),
            "exp": int(expire_time.timestamp()),
            "iss": self.config.issuer,
            "aud": self.config.audience,
            "type": "refresh",
        }

        # Add extra claims
        if extra_claims:
            payload_data.update(extra_claims)

        try:
            token = jwt.encode(
                payload_data, self._get_signing_key(), algorithm=self.config.algorithm
            )
            return token  # type: ignore[return-value]
        except Exception as e:
            raise ValueError(f"Failed to encode refresh token: {e}") from e

    def decode_token(self, token: str) -> ForgeTokenPayload:
        """
        Decode and validate a JWT token.

        Args:
            token: JWT token to decode

        Returns:
            ForgeTokenPayload with decoded claims

        Raises:
            JWTError: If token is invalid or expired
            ValueError: If token payload is malformed
        """
        try:
            # Decode token - optionally skip issuer/audience validation for legacy tokens
            decode_options: dict[str, bool] = {}
            if not self.config.verify_issuer:
                decode_options["verify_iss"] = False
            if not self.config.verify_audience:
                decode_options["verify_aud"] = False

            payload = jwt.decode(
                token,
                self._get_validation_key(),
                algorithms=[self.config.algorithm],
                audience=self.config.audience if self.config.verify_audience else None,
                issuer=self.config.issuer if self.config.verify_issuer else None,
                options=decode_options or None,
            )

            # Validate required fields; provide defaults for optional email/domain (legacy tokens)
            required_fields = ["sub", "exp", "iat"]
            missing_fields = [f for f in required_fields if f not in payload]
            if missing_fields:
                raise ValueError(f"Missing required fields: {', '.join(missing_fields)}")
            if "email" not in payload:
                payload["email"] = f"{payload['sub']}@forge.dev"
            if "domain" not in payload:
                payload["domain"] = "forge.dev"

            # Create ForgeTokenPayload
            return ForgeTokenPayload(**payload)

        except JWTError as e:
            raise JWTError(f"Invalid token: {e}") from e
        except Exception as e:
            raise ValueError(f"Failed to decode token: {e}") from e

    def refresh_access_token(self, refresh_token: str) -> tuple[str, ForgeTokenPayload]:
        """
        Create a new access token from a refresh token.

        Args:
            refresh_token: Valid refresh token

        Returns:
            Tuple of (new_access_token, refresh_token_payload)

        Raises:
            JWTError: If refresh token is invalid
            ValueError: If token creation fails
        """
        # Decode refresh token
        payload = self.decode_token(refresh_token)

        # Verify it's a refresh token
        if payload.type != "refresh":
            raise JWTError("Token is not a refresh token")

        # Get user info from token claims (if available)
        # For refresh tokens, we may need to fetch user info from database
        # For now, create minimal access token
        user_id = payload.sub
        email = payload.email if hasattr(payload, "email") else f"{user_id}@forge.dev"
        domain = payload.domain if hasattr(payload, "domain") else "forge.dev"

        # Create new access token
        access_token = self.create_access_token(
            user_id=user_id,
            email=email,
            domain=domain,
            plan=payload.plan,
            products=payload.products,
            roles=payload.roles,
            permissions=payload.permissions,
        )

        return access_token, payload

    def verify_token(self, token: str) -> bool:
        """
        Verify a token without returning the payload.

        Useful for quick validation checks.

        Args:
            token: JWT token to verify

        Returns:
            True if token is valid, False otherwise
        """
        try:
            self.decode_token(token)
            return True
        except (JWTError, ValueError):
            return False

    # =========================================================================
    # Refresh Token Rotation Methods
    # =========================================================================

    def create_refresh_token_with_id(
        self,
        user_id: str,
        token_id: str | None = None,
        extra_claims: dict[str, object] | None = None,
    ) -> tuple[str, RefreshToken]:
        """
        Create a refresh token with tracking metadata.

        Args:
            user_id: User ID (UUID)
            token_id: Optional unique token ID (auto-generated if None)
            extra_claims: Additional claims to include in token

        Returns:
            Tuple of (encoded_refresh_token, RefreshToken metadata)
        """
        import uuid

        # Generate token ID if not provided
        jti = token_id or str(uuid_lib.uuid4())

        now = datetime.now(timezone.utc)
        expire_time = now + timedelta(days=self.config.refresh_token_expire_days)

        # Create JWT payload
        payload_data = {
            "sub": user_id,
            "jti": jti,  # Token ID for tracking
            "iat": int(now.timestamp()),
            "exp": int(expire_time.timestamp()),
            "iss": self.config.issuer,
            "aud": self.config.audience,
            "type": "refresh",
        }

        # Add extra claims
        if extra_claims:
            payload_data.update(extra_claims)

        # Encode token
        try:
            token = jwt.encode(
                payload_data, self._get_signing_key(), algorithm=self.config.algorithm
            )
        except Exception as e:
            raise ValueError(f"Failed to encode refresh token: {e}") from e

        # Create refresh token metadata
        refresh_token = RefreshToken(
            token_id=jti,
            user_id=user_id,
            status=RefreshTokenStatus.ACTIVE,
            created_at=int(now.timestamp()),
            expires_at=int(expire_time.timestamp()),
        )

        return token, refresh_token  # type: ignore[return-value]

    def rotate_refresh_token(
        self,
        current_refresh_token: str,
        user_id: str,
        rotation_store: dict[str, RefreshToken] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> TokenRotationResult:
        """
        Rotate a refresh token: revoke old, issue new.

        Implements the refresh token rotation security pattern:
        1. Validate the current refresh token
        2. Revoke the current token
        3. Issue a new refresh token
        4. Return both new access and refresh tokens

        Args:
            current_refresh_token: The refresh token to rotate
            user_id: User ID for the new tokens
            rotation_store: Optional dict to store token metadata (for in-memory testing)
            ip_address: Client IP for token metadata
            user_agent: Client user agent for token metadata

        Returns:
            TokenRotationResult with new access and refresh tokens

        Raises:
            JWTError: If current token is invalid or revoked
            ValueError: If token creation fails
        """
        import time
        import json
        import base64

        # Decode and validate current refresh token
        payload = self.decode_token(current_refresh_token)

        if payload.type != "refresh":
            raise JWTError("Token is not a refresh token")

        # Extract token_id from the raw JWT payload (jti claim)
        try:
            parts = current_refresh_token.split(".")
            if len(parts) == 3:
                payload_b64 = parts[1]
                padding = 4 - len(payload_b64) % 4
                if padding != 4:
                    payload_b64 += "=" * padding
                raw_payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                token_id = raw_payload.get("jti") or payload.sub
            else:
                token_id = payload.sub
        except Exception:
            token_id = payload.sub

        # Check if token is already revoked/rotated in the store
        # First, if we have a rotation store, add the current token if not present
        if rotation_store is not None and token_id not in rotation_store:
            # Create metadata for the current token if not in store
            current_metadata = RefreshToken(
                token_id=token_id,
                user_id=payload.sub,
                status=RefreshTokenStatus.ACTIVE,
                created_at=int(payload.iat),
                expires_at=int(payload.exp),
            )
            rotation_store[token_id] = current_metadata

        if rotation_store and token_id in rotation_store:
            stored_token = rotation_store[token_id]
            if stored_token.status != RefreshTokenStatus.ACTIVE:
                raise JWTError(f"Refresh token has been {stored_token.status.value}")

        # Generate new token ID
        new_token_id = str(uuid_lib.uuid4())

        # Create new refresh token
        new_refresh_token, refresh_metadata = self.create_refresh_token_with_id(
            user_id=user_id,
            token_id=new_token_id,
            extra_claims={
                "rotated_from": token_id,
            },
        )

        # Update metadata
        refresh_metadata.ip_address = ip_address
        refresh_metadata.user_agent = user_agent
        refresh_metadata.rotation_count = (
            rotation_store.get(token_id).rotation_count + 1
            if rotation_store and token_id in rotation_store
            else 1
        )

        # Create new access token
        new_access_token = self.create_access_token(
            user_id=user_id,
            email=payload.email,
            domain=payload.domain,
            plan=payload.plan,
            products=payload.products,
            roles=payload.roles,
            permissions=payload.permissions,
        )

        # Update store: revoke old token, add new token
        if rotation_store is not None:
            # Mark old token as rotated
            if token_id in rotation_store:
                rotation_store[token_id].mark_rotated(new_token_id)

            # Store new token
            rotation_store[new_token_id] = refresh_metadata

        # Return rotation result
        return TokenRotationResult(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            expires_in=self.config.access_token_expire_minutes * 60,
            token_type="Bearer",
            rotation_count=refresh_metadata.rotation_count,
            previous_token_id=token_id,
        )

    def verify_refresh_token(
        self,
        refresh_token: str,
        rotation_store: dict[str, RefreshToken] | None = None,
    ) -> tuple[bool, str, RefreshTokenStatus]:
        """
        Verify a refresh token is valid and active.

        Args:
            refresh_token: The refresh token to verify
            rotation_store: Optional store checking for revoked/rotated tokens

        Returns:
            Tuple of (is_valid, token_id, status)
        """
        # First, try to decode without verification to check expiration and type
        try:
            import json
            import base64

            # Get the middle part of the JWT (payload)
            parts = refresh_token.split(".")
            if len(parts) != 3:
                return False, "", RefreshTokenStatus.REVOKED

            # Add padding if needed for base64 decoding
            payload_b64 = parts[1]
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding

            raw_payload = json.loads(base64.urlsafe_b64decode(payload_b64))

            # Check expiration first
            import time

            if raw_payload.get("exp", 0) < int(time.time()):
                token_id = raw_payload.get("jti") or raw_payload.get("sub", "")
                return False, token_id, RefreshTokenStatus.EXPIRED

            # Check token type
            if raw_payload.get("type") != "refresh":
                return False, "", RefreshTokenStatus.REVOKED

            # Get token ID
            token_id = raw_payload.get("jti") or raw_payload.get("sub", "")

        except Exception:
            # If we can't decode or parse, it's revoked
            return False, "", RefreshTokenStatus.REVOKED

        # Now verify with proper JWT validation
        try:
            payload = self.decode_token(refresh_token)
        except ExpiredSignatureError:
            return False, token_id, RefreshTokenStatus.EXPIRED
        except (JWTError, ValueError):
            return False, token_id, RefreshTokenStatus.REVOKED

        # Check rotation store
        if rotation_store is not None and token_id in rotation_store:
            stored = rotation_store[token_id]
            return stored.status == RefreshTokenStatus.ACTIVE, token_id, stored.status

        # If no store, token is valid if not expired
        return True, token_id, RefreshTokenStatus.ACTIVE

    def revoke_refresh_token(
        self,
        refresh_token: str,
        rotation_store: dict[str, RefreshToken] | None = None,
    ) -> bool:
        """
        Revoke a refresh token.

        Args:
            refresh_token: The refresh token to revoke
            rotation_store: Optional store to update

        Returns:
            True if token was revoked, False if not found/already revoked
        """
        try:
            # First try to extract token_id from the raw JWT (jti claim)
            import json
            import base64

            try:
                parts = refresh_token.split(".")
                if len(parts) == 3:
                    payload_b64 = parts[1]
                    padding = 4 - len(payload_b64) % 4
                    if padding != 4:
                        payload_b64 += "=" * padding
                    raw_payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                    token_id = raw_payload.get("jti") or raw_payload.get("sub")
                else:
                    # Fallback: decode and use sub
                    payload = self.decode_token(refresh_token)
                    token_id = payload.sub
            except Exception:
                # Fallback: decode and use sub
                payload = self.decode_token(refresh_token)
                token_id = payload.sub

            if rotation_store is not None and token_id in rotation_store:
                rotation_store[token_id].revoke()
                return True

            return False

        except (JWTError, ValueError):
            return False


# Legacy JWTAuth class for backward compatibility
class LegacyJWTAuth:
    """
    Legacy JWT authentication handler.

    .. deprecated::
        Use JWTAuth with JWTConfig instead. This will be removed in v2.0.
    """

    def __init__(
        self,
        secret_key: str,
        algorithm: str = "HS256",
        access_token_expire_minutes: int = 30,
        refresh_token_expire_days: int = 7,
        issuer: str | None = None,
        audience: str | None = None,
    ) -> None:
        """Initialize legacy JWT auth."""
        config = JWTConfig(
            secret_key=secret_key,
            algorithm=algorithm,
            access_token_expire_minutes=access_token_expire_minutes,
            refresh_token_expire_days=refresh_token_expire_days,
            issuer=issuer or "forge-core",
            audience=audience or "forge-services",
        )
        self._auth = JWTAuth(config)

    def create_access_token(
        self,
        user_id: str,
        email: str,
        roles: list[str],
        permissions: list[str],
        extra_claims: dict[str, object] | None = None,
    ) -> str:
        """Create access token (legacy)."""
        return self._auth.create_access_token(
            user_id=user_id,
            email=email,
            domain="forge.dev",  # Default domain for legacy
            roles=[UserRole(r) for r in roles],
            permissions=[Permission(p) for p in permissions],
            extra_claims=extra_claims,
        )

    def create_refresh_token(
        self, user_id: str, extra_claims: dict[str, object] | None = None
    ) -> str:
        """Create refresh token (legacy)."""
        return self._auth.create_refresh_token(user_id=user_id, extra_claims=extra_claims)

    def decode_token(self, token: str) -> ForgeTokenPayload:
        """Decode token (legacy)."""
        return self._auth.decode_token(token)
