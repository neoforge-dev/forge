"""
Data models for authentication and authorization.

Defines Pydantic models for users, tokens, roles, and permissions with
forge-core compatibility.

This module provides the core data structures used across the FORGE portfolio
for authentication and authorization. All models are Pydantic v2 models for
strict validation and serialization.

Forge-Core Compatibility:
    The JWT token structure matches forge-core format with domain, plan, and
    products fields for cross-service authentication.

Example:
    ```python
    from forge_shared.auth.models import ForgeUser, ForgeTokenPayload

    user = ForgeUser(
        id="123",
        email="user@example.com",
        domain="codeswiftr.com",
        plan="pro",
        products=["interview-simulator"],
        roles=[UserRole.USER],
        permissions=[Permission.READ_OWN]
    )

    payload = ForgeTokenPayload(
        sub="123",
        email="user@example.com",
        domain="codeswiftr.com",
        plan="pro",
        products=["interview-simulator"],
        exp=1734136200,
        iat=1734134400
    )
    ```
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


class UserRole(str, Enum):
    """
    User roles for RBAC.

    Roles define high-level access levels within the FORGE portfolio.
    Each role grants specific permissions and capabilities.

    Attributes:
        GUEST: Unauthenticated guest user
        USER: Standard authenticated user
        ADMIN: Administrator with full access
        SUPER_ADMIN: Super administrator with cross-domain access
    """

    GUEST = "guest"
    USER = "user"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


class Permission(str, Enum):
    """
    Fine-grained permissions for resource access control.

    Permissions follow the pattern `resource:action` for clear semantics.
    These are used in conjunction with roles for RBAC implementation.

    Attributes:
        User Permissions:
            READ_OWN: Read own data
            WRITE_OWN: Write own data
            DELETE_OWN: Delete own data

        Admin Permissions:
            READ_ALL: Read all data
            WRITE_ALL: Write all data
            DELETE_ALL: Delete all data
            MANAGE_USERS: Manage user accounts
            MANAGE_ROLES: Manage role assignments

        Product Permissions:
            ACCESS_PRODUCT: Access a specific product
            MANAGE_PRODUCT: Manage product settings

        Billing Permissions:
            VIEW_BILLING: View billing information
            MANAGE_BILLING: Manage billing and subscriptions
    """

    # User permissions
    READ_OWN = "read:own"
    WRITE_OWN = "write:own"
    DELETE_OWN = "delete:own"

    # Admin permissions
    READ_ALL = "read:all"
    WRITE_ALL = "write:all"
    DELETE_ALL = "delete:all"
    MANAGE_USERS = "manage:users"
    MANAGE_ROLES = "manage:roles"

    # Product permissions
    ACCESS_PRODUCT = "access:product"
    MANAGE_PRODUCT = "manage:product"

    # Billing permissions
    VIEW_BILLING = "view:billing"
    MANAGE_BILLING = "manage:billing"


class PlanTier(str, Enum):
    """
    Subscription plan tiers.

    Defines the available subscription tiers across the FORGE portfolio.
    Each tier has specific feature access and limits.

    Attributes:
        FREE: Free tier with basic features
        PRO: Professional tier with advanced features
        ENTERPRISE: Enterprise tier with custom features and support
    """

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


@dataclass(frozen=True)
class ForgeUser:
    """
    Forge user dataclass with forge-core compatibility.

    Represents an authenticated user within the FORGE portfolio ecosystem.
    This immutable dataclass ensures thread-safety and prevents accidental
    mutation of user data.

    Attributes:
        id: Unique user identifier (UUID)
        email: User email address
        domain: User's domain within the FORGE portfolio (e.g., codeswiftr.com)
        plan: User's subscription plan tier
        products: List of products the user has access to
        roles: List of user roles
        permissions: List of user permissions

    Example:
        ```python
        user = ForgeUser(
            id="550e8400-e29b-41d4-a716-446655440000",
            email="user@codeswiftr.com",
            domain="codeswiftr.com",
            plan=PlanTier.PRO,
            products=["interview-simulator", "code-atlas"],
            roles=[UserRole.USER],
            permissions=[Permission.READ_OWN, Permission.WRITE_OWN]
        )
        ```
    """

    id: str
    email: str
    domain: str
    plan: PlanTier
    products: list[str]
    roles: list[UserRole]
    permissions: list[Permission]

    def __post_init__(self) -> None:
        """
        Validate user data after initialization.

        Raises:
            ValueError: If validation fails
        """
        # Validate email format
        if "@" not in self.email:
            raise ValueError(f"Invalid email format: {self.email}")

        # Validate domain format
        if "." not in self.domain:
            raise ValueError(f"Invalid domain format: {self.domain}")

        # Ensure roles and permissions are lists
        if not isinstance(self.roles, list):
            raise ValueError("roles must be a list")

        if not isinstance(self.permissions, list):
            raise ValueError("permissions must be a list")

        if not isinstance(self.products, list):
            raise ValueError("products must be a list")

    @property
    def is_admin(self) -> bool:
        """
        Check if user has admin privileges.

        Returns:
            True if user has admin or super_admin role
        """
        return UserRole.ADMIN in self.roles or UserRole.SUPER_ADMIN in self.roles

    @property
    def is_super_admin(self) -> bool:
        """
        Check if user has super admin privileges.

        Returns:
            True if user has super_admin role
        """
        return UserRole.SUPER_ADMIN in self.roles

    def has_role(self, role: UserRole) -> bool:
        """
        Check if user has a specific role.

        Args:
            role: Role to check

        Returns:
            True if user has the role
        """
        return role in self.roles

    def has_permission(self, permission: Permission) -> bool:
        """
        Check if user has a specific permission.

        Args:
            permission: Permission to check

        Returns:
            True if user has the permission
        """
        return permission in self.permissions

    def has_product_access(self, product: str) -> bool:
        """
        Check if user has access to a specific product.

        Args:
            product: Product identifier (e.g., "interview-simulator")

        Returns:
            True if user has access to the product
        """
        return product in self.products

    def has_plan_tier(self, plan: PlanTier) -> bool:
        """
        Check if user has a specific plan tier or higher.

        Plan hierarchy: FREE < PRO < ENTERPRISE

        Args:
            plan: Plan tier to check

        Returns:
            True if user has the plan tier or higher
        """
        tier_order = {
            PlanTier.FREE: 0,
            PlanTier.PRO: 1,
            PlanTier.ENTERPRISE: 2,
        }
        return tier_order.get(self.plan, 0) >= tier_order.get(plan, 0)

    def can_access_product(self, product: str, required_plan: PlanTier | None = None) -> bool:
        """
        Check if user can access a product with optional plan requirement.

        Args:
            product: Product identifier
            required_plan: Optional required plan tier

        Returns:
            True if user can access the product
        """
        if not self.has_product_access(product):
            return False

        if required_plan is not None:
            return self.has_plan_tier(required_plan)

        return True


class ForgeTokenPayload(BaseModel):
    """
    JWT token payload model with forge-core compatibility.

    This model matches the forge-core token structure for cross-service
    authentication within the FORGE portfolio.

    Token Structure:
        {
            "sub": "user-uuid",
            "email": "user@example.com",
            "domain": "codeswiftr.com",
            "plan": "pro",
            "products": ["interview-simulator", "code-atlas"],
            "roles": ["user"],
            "permissions": ["read:own", "write:own"],
            "iat": 1734134400,
            "exp": 1734136200,
            "iss": "forge-core",
            "aud": "forge-services",
            "type": "access"
        }

    Attributes:
        sub: Subject (user ID / UUID)
        email: User email address
        domain: User's domain within FORGE portfolio
        plan: User's subscription plan tier
        products: List of products user has access to
        roles: List of user roles
        permissions: List of user permissions
        exp: Expiration time (Unix timestamp)
        iat: Issued at time (Unix timestamp)
        iss: Issuer (default: "forge-core")
        aud: Audience (default: "forge-services")
        type: Token type ("access" or "refresh")

    Example:
        ```python
        payload = ForgeTokenPayload(
            sub="550e8400-e29b-41d4-a716-446655440000",
            email="user@codeswiftr.com",
            domain="codeswiftr.com",
            plan=PlanTier.PRO,
            products=["interview-simulator"],
            roles=[UserRole.USER],
            permissions=[Permission.READ_OWN],
            exp=1734136200,
            iat=1734134400
        )
        ```
    """

    sub: str = Field(..., description="Subject (user ID)")
    email: EmailStr = Field(..., description="User email")
    domain: str = Field(..., description="User's domain")
    plan: PlanTier = Field(default=PlanTier.FREE, description="Subscription plan tier")
    products: list[str] = Field(default_factory=list, description="Accessible products")
    roles: list[UserRole] = Field(default_factory=list, description="User roles")
    permissions: list[Permission] = Field(default_factory=list, description="User permissions")
    exp: int = Field(..., description="Expiration time (Unix timestamp)", gt=0)
    iat: int = Field(..., description="Issued at time (Unix timestamp)", gt=0)
    iss: Optional[str] = Field(default="forge-core", description="Issuer")
    aud: Optional[str] = Field(default="forge-services", description="Audience")
    type: str = Field(default="access", description="Token type")

    @field_validator("roles", mode="before")
    @classmethod
    def validate_roles(cls, v: list[str | UserRole]) -> list[UserRole]:
        """
        Validate and convert roles to UserRole enum.

        Args:
            v: List of roles (strings or enums)

        Returns:
            List of UserRole enums

        Raises:
            ValueError: If invalid role provided
        """
        validated_roles: list[UserRole] = []
        for role in v:
            if isinstance(role, str):
                try:
                    validated_roles.append(UserRole(role))
                except ValueError:
                    raise ValueError(f"Invalid role: {role}")
            elif isinstance(role, UserRole):
                validated_roles.append(role)
            else:
                raise ValueError(f"Invalid role type: {type(role)}")
        return validated_roles

    @field_validator("permissions", mode="before")
    @classmethod
    def validate_permissions(cls, v: list[str | Permission]) -> list[Permission]:
        """
        Validate and convert permissions to Permission enum.

        Args:
            v: List of permissions (strings or enums)

        Returns:
            List of Permission enums

        Raises:
            ValueError: If invalid permission provided
        """
        validated_permissions: list[Permission] = []
        for perm in v:
            if isinstance(perm, str):
                try:
                    validated_permissions.append(Permission(perm))
                except ValueError:
                    # Allow custom permissions not in enum
                    validated_permissions.append(Permission(perm))
            elif isinstance(perm, Permission):
                validated_permissions.append(perm)
            else:
                raise ValueError(f"Invalid permission type: {type(perm)}")
        return validated_permissions

    @field_validator("type")
    @classmethod
    def validate_token_type(cls, v: str) -> str:
        """
        Validate token type.

        Args:
            v: Token type string

        Returns:
            Validated token type

        Raises:
            ValueError: If invalid token type
        """
        if v not in ("access", "refresh"):
            raise ValueError(f"Invalid token type: {v}")
        return v

    def to_user(self) -> ForgeUser:
        """
        Convert token payload to ForgeUser.

        Returns:
            ForgeUser instance
        """
        return ForgeUser(
            id=self.sub,
            email=str(self.email),
            domain=self.domain,
            plan=self.plan,
            products=self.products,
            roles=self.roles,
            permissions=self.permissions,
        )

    def model_dump_custom(self) -> dict[str, Any]:
        """
        Dump model to dict with enums as strings.

        Returns:
            Dictionary with string values for enums
        """
        data = self.model_dump()
        data["roles"] = [r.value for r in self.roles]
        data["permissions"] = [p.value for p in self.permissions]
        data["plan"] = self.plan.value
        return data


# Legacy models for backward compatibility
class User(BaseModel):
    """
    Legacy User model for backward compatibility.

    .. deprecated::
        Use ForgeUser dataclass instead. This model will be removed in v2.0.
    """

    id: str = Field(..., description="User ID")
    email: EmailStr = Field(..., description="User email")
    roles: list[str] = Field(default_factory=list, description="User roles")
    permissions: list[str] = Field(default_factory=list, description="User permissions")

    @property
    def is_admin(self) -> bool:
        """Check if user has admin role."""
        return "admin" in self.roles

    def has_role(self, role: str) -> bool:
        """Check if user has a specific role."""
        return role in self.roles

    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission."""
        return permission in self.permissions


class TokenPayload(BaseModel):
    """
    Legacy TokenPayload model for backward compatibility.

    .. deprecated::
        Use ForgeTokenPayload instead. This model will be removed in v2.0.
    """

    sub: str = Field(..., description="Subject (user ID)")
    email: EmailStr = Field(..., description="User email")
    roles: list[str] = Field(default_factory=list, description="User roles")
    permissions: list[str] = Field(default_factory=list, description="User permissions")
    exp: int = Field(..., description="Expiration time (Unix timestamp)")
    iat: int = Field(..., description="Issued at time (Unix timestamp)")
    iss: Optional[str] = Field(None, description="Issuer")
    aud: Optional[str] = Field(None, description="Audience")
    type: str = Field(default="access", description="Token type")


# =============================================================================
# Refresh Token Rotation Models
# =============================================================================


class RefreshTokenStatus(str, Enum):
    """
    Status of a refresh token.

    Attributes:
        ACTIVE: Token is valid and can be used
        REVOKED: Token was explicitly revoked (logout, password change, etc.)
        ROTATED: Token was replaced by a newer refresh token
        EXPIRED: Token has passed its expiration date
    """

    ACTIVE = "active"
    REVOKED = "revoked"
    ROTATED = "rotated"
    EXPIRED = "expired"


class RefreshToken(BaseModel):
    """
    Refresh token model with rotation support.

    Tracks refresh token state for implementing token rotation security.
    Each refresh token has a unique ID and tracks whether it has been used,
    revoked, or rotated.

    Attributes:
        token_id: Unique identifier for this refresh token
        user_id: User this token belongs to
        status: Current status of the token
        created_at: When the token was issued
        expires_at: When the token expires
        last_used_at: When the token was last used for rotation
        ip_address: IP address that originally requested the token
        user_agent: User agent string from original request
        rotated_from: Token ID this was rotated from (if any)
        rotation_count: Number of times this token has been rotated
    """

    token_id: str = Field(..., description="Unique token identifier (jti claim)")
    user_id: str = Field(..., description="User ID this token belongs to")
    status: RefreshTokenStatus = Field(
        default=RefreshTokenStatus.ACTIVE, description="Token status"
    )
    created_at: int = Field(..., description="Creation timestamp (Unix)")
    expires_at: int = Field(..., description="Expiration timestamp (Unix)")
    last_used_at: Optional[int] = Field(None, description="Last usage timestamp (Unix)")
    ip_address: Optional[str] = Field(None, description="Original client IP")
    user_agent: Optional[str] = Field(None, description="Original user agent")
    rotated_from: Optional[str] = Field(None, description="Previous token ID if rotated")
    rotation_count: int = Field(default=0, description="Number of rotations")

    @property
    def is_valid(self) -> bool:
        """Check if token is still valid for use."""
        import time

        return self.status == RefreshTokenStatus.ACTIVE and self.expires_at > int(time.time())

    @property
    def is_expired(self) -> bool:
        """Check if token has expired."""
        import time

        return self.expires_at <= int(time.time())

    def revoke(self, reason: str = "user_revoke") -> None:
        """
        Revoke this refresh token.

        Args:
            reason: Reason for revocation
        """
        self.status = RefreshTokenStatus.REVOKED

    def mark_rotated(self, new_token_id: str) -> None:
        """
        Mark this token as rotated (replaced by a newer token).

        Args:
            new_token_id: ID of the new refresh token
        """
        self.status = RefreshTokenStatus.ROTATED


class TokenRotationResult(BaseModel):
    """
    Result of a token rotation operation.

    Contains both the new access token and refresh token, along with
    metadata about the rotation.

    Attributes:
        access_token: New access token
        refresh_token: New refresh token (replaces the old one)
        expires_in: Access token expiration in seconds
        token_type: Type of token (always "Bearer")
        rotation_count: Total number of rotations for this session
        previous_token_id: ID of the token that was rotated out
    """

    access_token: str = Field(..., description="New access token")
    refresh_token: str = Field(..., description="New refresh token")
    expires_in: int = Field(..., description="Access token expiration seconds")
    token_type: str = Field(default="Bearer", description="Token type")
    rotation_count: int = Field(default=0, description="Rotation count")
    previous_token_id: Optional[str] = Field(None, description="Previous token ID")
