"""Tests for JWT refresh token rotation functionality."""

import time
import pytest
from datetime import datetime, timezone, timedelta

from forge_shared.auth.jwt import JWTAuth, JWTConfig
from forge_shared.auth.models import RefreshTokenStatus


class TestJWTRefreshTokenRotation:
    """Test JWT refresh token rotation."""

    @pytest.fixture
    def jwt_auth(self):
        """Create JWT auth instance for testing."""
        config = JWTConfig(
            secret_key="test-secret-key",
            algorithm="HS256",
            access_token_expire_minutes=30,
            refresh_token_expire_days=7,
        )
        return JWTAuth(config)

    @pytest.fixture
    def user_data(self):
        """Sample user data for testing."""
        return {
            "user_id": "550e8400-e29b-41d4-a716-446655440000",
            "email": "test@example.com",
            "domain": "test.example.com",
        }

    def test_create_refresh_token_with_id(self, jwt_auth, user_data):
        """Test creating a refresh token with ID."""
        token_id = "test-token-id"
        refresh_token, metadata = jwt_auth.create_refresh_token_with_id(
            user_id=user_data["user_id"],
            token_id=token_id,
        )

        # Verify token format (JWT should have 3 parts)
        assert len(refresh_token.split(".")) == 3

        # Verify metadata
        assert metadata.token_id == token_id
        assert metadata.user_id == user_data["user_id"]
        assert metadata.status == RefreshTokenStatus.ACTIVE
        assert metadata.created_at > 0
        assert metadata.expires_at > 0
        assert metadata.rotation_count == 0

    def test_refresh_token_rotation(self, jwt_auth, user_data):
        """Test refresh token rotation mechanism."""
        # Create initial refresh token
        initial_token, initial_metadata = jwt_auth.create_refresh_token_with_id(
            user_id=user_data["user_id"],
            token_id="initial-token",
        )

        # Create access token for the user
        access_token = jwt_auth.create_access_token(**user_data)

        # Pre-populate rotation store with initial token
        rotation_store = {"initial-token": initial_metadata}

        # Rotate the refresh token
        result = jwt_auth.rotate_refresh_token(
            current_refresh_token=initial_token,
            user_id=user_data["user_id"],
            rotation_store=rotation_store,
            ip_address="127.0.0.1",
            user_agent="test-agent",
        )

        # Verify rotation result
        assert result.access_token is not None
        assert result.refresh_token is not None
        assert result.expires_in == jwt_auth.config.access_token_expire_minutes * 60
        assert result.token_type == "Bearer"
        assert result.rotation_count == 1
        assert result.previous_token_id == "initial-token"

        # Verify rotation store updated correctly
        assert "initial-token" in rotation_store
        assert rotation_store["initial-token"].status == RefreshTokenStatus.ROTATED

        # Find the new token in the store
        new_token_in_store = None
        for token_id, token_metadata in rotation_store.items():
            if token_metadata.token_id != "initial-token":
                new_token_in_store = token_metadata
                break

        assert new_token_in_store is not None
        assert new_token_in_store.status == RefreshTokenStatus.ACTIVE
        assert new_token_in_store.rotation_count == 1
        assert new_token_in_store.ip_address == "127.0.0.1"
        assert new_token_in_store.user_agent == "test-agent"

    def test_verify_refresh_token(self, jwt_auth, user_data):
        """Test refresh token verification."""
        # Create a refresh token
        refresh_token, metadata = jwt_auth.create_refresh_token_with_id(
            user_id=user_data["user_id"],
            token_id="verify-test-token",
        )

        rotation_store = {metadata.token_id: metadata}

        # Verify valid token
        is_valid, token_id, status = jwt_auth.verify_refresh_token(refresh_token, rotation_store)
        assert is_valid is True
        assert token_id == metadata.token_id
        assert status == RefreshTokenStatus.ACTIVE

        # Verify revoked token
        metadata.revoke()
        is_valid, token_id, status = jwt_auth.verify_refresh_token(refresh_token, rotation_store)
        assert is_valid is False
        assert status == RefreshTokenStatus.REVOKED

    def test_verify_refresh_token_expired(self, jwt_auth, user_data):
        """Test verification of expired refresh token."""
        # Test with an already expired token by creating one with past expiration
        # We'll manually create an expired token for testing
        import time
        import json

        # Create token data that's already expired
        past_time = int(time.time()) - 3600  # 1 hour ago

        expired_payload = {
            "sub": user_data["user_id"],
            "jti": "expired-token",
            "iat": past_time - 60,  # Issued 1 minute before expiration
            "exp": past_time,  # Expired 1 hour ago
            "iss": "forge-core",
            "aud": "forge-services",
            "type": "refresh",
        }

        # Manually encode the expired token
        from jose import jwt as jose_jwt

        expired_token = jose_jwt.encode(
            expired_payload, jwt_auth.config.secret_key, algorithm=jwt_auth.config.algorithm
        )

        # Verify expired token
        is_valid, token_id, status = jwt_auth.verify_refresh_token(expired_token, None)
        assert is_valid is False
        assert status == RefreshTokenStatus.EXPIRED

    def test_revoke_refresh_token(self, jwt_auth, user_data):
        """Test refresh token revocation."""
        # Create refresh token
        refresh_token, metadata = jwt_auth.create_refresh_token_with_id(
            user_id=user_data["user_id"],
            token_id="revoke-test-token",
        )

        rotation_store = {metadata.token_id: metadata}

        # Verify token is initially active
        assert metadata.status == RefreshTokenStatus.ACTIVE

        # Revoke the token
        revoked = jwt_auth.revoke_refresh_token(refresh_token, rotation_store)

        # Verify revocation
        assert revoked is True
        assert metadata.status == RefreshTokenStatus.REVOKED

        # Try to revoke already revoked token (should still return True)
        revoked_again = jwt_auth.revoke_refresh_token(refresh_token, rotation_store)
        assert revoked_again is True  # Still returns True, just no change

        # Verify token is no longer valid
        is_valid, token_id, status = jwt_auth.verify_refresh_token(refresh_token, rotation_store)
        assert is_valid is False
        assert status == RefreshTokenStatus.REVOKED

    def test_rotation_prevents_reuse(self, jwt_auth, user_data):
        """Test that rotated tokens cannot be reused."""
        # Create initial refresh token
        initial_token, initial_metadata = jwt_auth.create_refresh_token_with_id(
            user_id=user_data["user_id"],
            token_id="reuse-test-token",
        )

        rotation_store = {initial_metadata.token_id: initial_metadata}

        # Rotate the token
        result = jwt_auth.rotate_refresh_token(
            current_refresh_token=initial_token,
            user_id=user_data["user_id"],
            rotation_store=rotation_store,
        )

        # Try to use the old token again
        with pytest.raises(Exception):  # Should raise JWTError
            jwt_auth.rotate_refresh_token(
                current_refresh_token=initial_token,
                user_id=user_data["user_id"],
                rotation_store=rotation_store,
            )

    def test_multiple_rotations(self, jwt_auth, user_data):
        """Test multiple successive token rotations."""
        current_token, current_metadata = jwt_auth.create_refresh_token_with_id(
            user_id=user_data["user_id"],
            token_id="multi-rotate-1",
        )

        rotation_store = {"multi-rotate-1": current_metadata}

        # Perform multiple rotations
        for i in range(5):
            result = jwt_auth.rotate_refresh_token(
                current_refresh_token=current_token,
                user_id=user_data["user_id"],
                rotation_store=rotation_store,
            )
            current_token = result.refresh_token

            # Verify rotation count increases
            assert result.rotation_count == i + 1

        # Verify we have 5 rotated tokens + 1 active token in store
        active_count = sum(
            1 for token in rotation_store.values() if token.status == RefreshTokenStatus.ACTIVE
        )
        rotated_count = sum(
            1 for token in rotation_store.values() if token.status == RefreshTokenStatus.ROTATED
        )

        assert active_count == 1  # Only the latest token is active
        assert rotated_count == 5  # Previous 5 tokens are rotated

    def test_invalid_refresh_token_type(self, jwt_auth, user_data):
        """Test that access tokens cannot be used for refresh."""
        # Create an access token (not a refresh token)
        access_token = jwt_auth.create_access_token(**user_data)

        rotation_store = {}

        # Try to rotate with access token (should fail)
        with pytest.raises(Exception):  # Should raise JWTError
            jwt_auth.rotate_refresh_token(
                current_refresh_token=access_token,
                user_id=user_data["user_id"],
                rotation_store=rotation_store,
            )
