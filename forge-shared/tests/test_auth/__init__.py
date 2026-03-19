"""
Tests for authentication module.

Test coverage for JWT authentication, dependencies, models, and middleware.
"""

import pytest
from datetime import datetime, timedelta, timezone

from forge_shared.auth import JWTAuth
from forge_shared.auth.models import User, TokenPayload
from forge_shared.auth.dependencies import (
    get_current_user,
    require_role,
    require_permission,
    set_jwt_auth,
)


class TestJWTAuth:
    """
    Tests for JWT authentication handler.

    Tests JWT token creation, validation, and expiration.
    """

    def test_create_access_token(self, jwt_auth: JWTAuth, test_user: User):
        """
        Test access token creation.

        Args:
            jwt_auth: JWT auth fixture
            test_user: Test user fixture
        """
        token = jwt_auth.create_access_token(
            user_id=test_user.id,
            email=test_user.email,
            roles=test_user.roles,
            permissions=test_user.permissions,
        )

        assert token is not None
        assert isinstance(token, str)

        # Decode and verify payload
        payload = jwt_auth.decode_token(token)
        assert payload.sub == test_user.id
        assert payload.email == test_user.email
        assert payload.roles == test_user.roles
        assert payload.permissions == test_user.permissions

    def test_create_refresh_token(self, jwt_auth: JWTAuth, test_user: User):
        """
        Test refresh token creation.

        Args:
            jwt_auth: JWT auth fixture
            test_user: Test user fixture
        """
        token = jwt_auth.create_refresh_token(user_id=test_user.id)

        assert token is not None
        assert isinstance(token, str)

        # Decode and verify payload
        payload = jwt_auth.decode_token(token)
        assert payload.sub == test_user.id
        assert payload.type == "refresh"

    def test_decode_invalid_token(self, jwt_auth: JWTAuth):
        """
        Test decoding invalid token raises error.

        Args:
            jwt_auth: JWT auth fixture
        """
        with pytest.raises(Exception):  # JWTError
            jwt_auth.decode_token("invalid-token")

    def test_token_expiration(self, jwt_auth: JWTAuth, test_user: User):
        """
        Test token expiration is set correctly.

        Args:
            jwt_auth: JWT auth fixture
            test_user: Test user fixture
        """
        token = jwt_auth.create_access_token(
            user_id=test_user.id,
            email=test_user.email,
            roles=test_user.roles,
            permissions=test_user.permissions,
        )

        payload = jwt_auth.decode_token(token)

        # Check expiration is in the future
        expire_time = datetime.fromtimestamp(payload.exp, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        assert expire_time > now

        # Check expiration is approximately 30 minutes
        expected_expire = now + timedelta(minutes=30)
        time_diff = abs((expire_time - expected_expire).total_seconds())
        assert time_diff < 5  # Within 5 seconds


class TestUserModel:
    """
    Tests for User model.

    Tests user properties, role checking, and permission checking.
    """

    def test_user_creation(self, test_user: User):
        """
        Test user model creation.

        Args:
            test_user: Test user fixture
        """
        assert test_user.id == "test-user-123"
        assert test_user.email == "test@example.com"
        assert test_user.roles == ["user"]
        assert test_user.permissions == ["read:own"]

    def test_has_role(self, test_user: User, test_user_admin: User):
        """
        Test has_role method.

        Args:
            test_user: Test user fixture
            test_user_admin: Test admin user fixture
        """
        assert test_user.has_role("user")
        assert not test_user.has_role("admin")

        assert test_user_admin.has_role("admin")
        assert test_user_admin.has_role("user")

    def test_has_permission(self, test_user: User, test_user_admin: User):
        """
        Test has_permission method.

        Args:
            test_user: Test user fixture
            test_user_admin: Test admin user fixture
        """
        assert test_user.has_permission("read:own")
        assert not test_user.has_permission("read:all")

        assert test_user_admin.has_permission("read:all")
        assert test_user_admin.has_permission("write:all")

    def test_is_admin(self, test_user: User, test_user_admin: User):
        """
        Test is_admin property.

        Args:
            test_user: Test user fixture
            test_user_admin: Test admin user fixture
        """
        assert not test_user.is_admin
        assert test_user_admin.is_admin


class TestDependencies:
    """
    Tests for FastAPI dependencies.

    Tests authentication dependencies, role requirements, and permission
    requirements.
    """

    def test_set_jwt_auth(self, jwt_auth: JWTAuth):
        """
        Test setting JWT auth globally.

        Args:
            jwt_auth: JWT auth fixture
        """
        set_jwt_auth(jwt_auth)
        # Should not raise exception
        from forge_shared.auth.dependencies import get_jwt_auth

        auth = get_jwt_auth()
        assert auth is not None

    def test_require_role_factory(self):
        """
        Test require_role dependency factory.
        """
        dependency = require_role("admin")
        assert dependency is not None

    def test_require_permission_factory(self):
        """
        Test require_permission dependency factory.
        """
        dependency = require_permission("users:create")
        assert dependency is not None


class TestTokenPayload:
    """
    Tests for TokenPayload model.

    Tests token payload validation and serialization.
    """

    def test_token_payload_creation(self, test_user: User):
        """
        Test token payload creation.

        Args:
            test_user: Test user fixture
        """
        payload = TokenPayload(
            sub=test_user.id,
            email=test_user.email,
            roles=test_user.roles,
            permissions=test_user.permissions,
            exp=int(datetime.now(timezone.utc).timestamp()) + 1800,
            iat=int(datetime.now(timezone.utc).timestamp()),
        )

        assert payload.sub == test_user.id
        assert payload.email == test_user.email
        assert payload.roles == test_user.roles
