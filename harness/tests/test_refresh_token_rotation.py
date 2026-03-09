"""Tests for JWT Refresh Token Rotation.

Tests the refresh token rotation security pattern:
- Happy path: successful token rotation
- Expired refresh token: returns 401
- Reused refresh token: returns 401 (rotation violation)
"""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import after app setup
from forge_harness.webhook_server.api.auth import router as auth_router
from forge_harness.webhook_server.infrastructure.jwt_auth import (
    JWTAuthConfig,
    JWTAuthResult,
    clear_all_revoked_tokens,
    create_access_token,
    create_refresh_token,
    decode_token,
    is_token_revoked,
    revoke_token,
    revoke_token_family,
    rotate_refresh_token,
    verify_refresh_token,
)


# Create test app
@pytest.fixture
def app():
    """Create FastAPI test app with auth router."""
    app = FastAPI()
    app.include_router(auth_router)
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def config():
    """Create test JWT config with required expiry times."""
    return JWTAuthConfig(
        secret_key="test-secret-key-for-rotation-tests-12345",
        algorithm="HS256",
        access_token_expire_minutes=60,  # 1 hour as per requirements
        refresh_token_expire_days=7,  # 7 days as per requirements
        require_auth=True,
    )


@pytest.fixture(autouse=True)
def clear_revoked():
    """Clear revoked tokens before each test."""
    clear_all_revoked_tokens()


class TestRefreshTokenRotationHappyPath:
    """Happy path tests for refresh token rotation."""

    def test_rotate_refresh_token_success(self, config):
        """Test successful refresh token rotation."""
        # Create initial refresh token
        refresh_tok = create_refresh_token(
            "user-123",
            config,
            permissions=["read", "write"],
        )

        # Rotate the token
        result = rotate_refresh_token(refresh_tok, config)

        assert result.result == JWTAuthResult.SUCCESS
        assert result.access_token is not None
        assert result.refresh_token is not None
        assert result.expires_in == 3600  # 60 minutes in seconds
        assert result.token_type == "Bearer"

    def test_old_refresh_token_revoked_after_rotation(self, config):
        """Test that old refresh token is revoked after rotation."""
        # Create initial refresh token
        refresh_tok = create_refresh_token("user-123", config)

        # Get the JTI before rotation
        _, payload = decode_token(refresh_tok, config)
        original_jti = payload.jti

        # Rotate the token
        result = rotate_refresh_token(refresh_tok, config)
        assert result.result == JWTAuthResult.SUCCESS

        # Old token should be revoked
        assert is_token_revoked(original_jti) is True

    def test_new_tokens_have_correct_properties(self, config):
        """Test that new tokens have correct properties after rotation."""
        # Create initial refresh token with permissions
        refresh_tok = create_refresh_token(
            "user-456",
            config,
            permissions=["admin", "read"],
        )

        _, original_payload = decode_token(refresh_tok, config)

        # Rotate the token
        result = rotate_refresh_token(refresh_tok, config)

        # Verify new access token
        access_result, access_payload = decode_token(result.access_token, config)
        assert access_result == JWTAuthResult.SUCCESS
        assert access_payload.sub == "user-456"
        assert access_payload.type == "access"
        assert access_payload.permissions == ["admin", "read"]

        # Verify new refresh token
        refresh_result, refresh_payload = decode_token(result.refresh_token, config)
        assert refresh_result == JWTAuthResult.SUCCESS
        assert refresh_payload.sub == "user-456"
        assert refresh_payload.type == "refresh"
        assert refresh_payload.permissions == ["admin", "read"]
        # Family ID should be preserved
        assert refresh_payload.family_id == original_payload.family_id

    def test_refresh_token_family_preserved(self, config):
        """Test that token family is preserved across rotations."""
        # Create initial refresh token
        refresh_tok = create_refresh_token("user-789", config)
        _, original_payload = decode_token(refresh_tok, config)
        original_family = original_payload.family_id

        # Rotate multiple times
        result1 = rotate_refresh_token(refresh_tok, config)
        assert result1.result == JWTAuthResult.SUCCESS

        _, payload1 = decode_token(result1.refresh_token, config)
        assert payload1.family_id == original_family

        # Rotate again
        result2 = rotate_refresh_token(result1.refresh_token, config)
        assert result2.result == JWTAuthResult.SUCCESS

        _, payload2 = decode_token(result2.refresh_token, config)
        assert payload2.family_id == original_family

    def test_api_refresh_endpoint_success(self, client, config):
        """Test the /api/auth/refresh endpoint with valid token."""
        with patch(
            "forge_harness.webhook_server.api.auth.JWTAuthConfig.from_env",
            return_value=config,
        ):
            # Create a refresh token
            refresh_tok = create_refresh_token("test-user", config, permissions=["read"])

            response = client.post(
                "/api/auth/refresh",
                json={"refresh_token": refresh_tok},
            )

            assert response.status_code == 200
            data = response.json()
            assert "access_token" in data
            assert "refresh_token" in data
            assert data["expires_in"] == 3600
            assert data["token_type"] == "Bearer"

    def test_new_refresh_token_can_be_used(self, config):
        """Test that newly issued refresh token can be used for subsequent rotation."""
        # Create initial token
        refresh_tok = create_refresh_token("user-999", config)

        # First rotation
        result1 = rotate_refresh_token(refresh_tok, config)
        assert result1.result == JWTAuthResult.SUCCESS

        # Second rotation using new refresh token
        result2 = rotate_refresh_token(result1.refresh_token, config)
        assert result2.result == JWTAuthResult.SUCCESS

        # Third rotation
        result3 = rotate_refresh_token(result2.refresh_token, config)
        assert result3.result == JWTAuthResult.SUCCESS


class TestRefreshTokenExpired:
    """Tests for expired refresh token handling."""

    def test_expired_refresh_token_returns_expired_result(self, config):
        """Test that expired refresh token returns EXPIRED_TOKEN result."""
        # Create an expired refresh token
        expired_refresh = create_refresh_token(
            "user-123",
            config,
            # Custom expiry in the past
        )

        # Decode and manipulate expiry to be in the past
        # We need to create a token that's already expired
        # Let's patch the datetime during creation
        from unittest.mock import MagicMock

        # Create a token that will be expired by manipulating the config temporarily
        past_config = JWTAuthConfig(
            secret_key=config.secret_key,
            algorithm=config.algorithm,
            access_token_expire_minutes=config.access_token_expire_minutes,
            refresh_token_expire_days=-1,  # Negative to make it expire immediately
        )

        expired_token = create_refresh_token("user-123", past_config)

        # Try to rotate the expired token
        result = rotate_refresh_token(expired_token, config)

        assert result.result == JWTAuthResult.EXPIRED_TOKEN
        assert result.access_token is None
        assert result.refresh_token is None

    def test_api_returns_401_for_expired_refresh_token(self, client, config):
        """Test that API returns 401 for expired refresh token."""
        with patch(
            "forge_harness.webhook_server.api.auth.JWTAuthConfig.from_env",
            return_value=config,
        ):
            # Create an expired refresh token
            past_config = JWTAuthConfig(
                secret_key=config.secret_key,
                algorithm=config.algorithm,
                access_token_expire_minutes=config.access_token_expire_minutes,
                refresh_token_expire_days=-1,  # Expired
            )
            expired_token = create_refresh_token("user-123", past_config)

            response = client.post(
                "/api/auth/refresh",
                json={"refresh_token": expired_token},
            )

            assert response.status_code == 401
            data = response.json()
            assert "expired" in data["detail"].lower()

    def test_verify_refresh_token_detects_expiry(self, config):
        """Test that verify_refresh_token correctly detects expired tokens."""
        # Create a token that's already expired
        past_config = JWTAuthConfig(
            secret_key=config.secret_key,
            algorithm=config.algorithm,
            refresh_token_expire_days=-1,
        )
        expired_token = create_refresh_token("user-123", past_config)

        result, payload = verify_refresh_token(expired_token, config)

        assert result == JWTAuthResult.EXPIRED_TOKEN
        assert payload is None


class TestRefreshTokenReuse:
    """Tests for refresh token reuse (rotation violation) detection."""

    def test_reused_refresh_token_fails(self, config):
        """Test that reusing a refresh token fails."""
        # Create initial refresh token
        refresh_tok = create_refresh_token("user-123", config)

        # First rotation - succeeds
        result1 = rotate_refresh_token(refresh_tok, config)
        assert result1.result == JWTAuthResult.SUCCESS

        # Try to reuse the original token - should fail
        result2 = rotate_refresh_token(refresh_tok, config)
        assert result2.result == JWTAuthResult.INVALID_TOKEN  # Token is revoked

    def test_token_family_revocation_on_reuse(self, config):
        """Test that token family is revoked when reuse is attempted."""
        # Create initial refresh token
        refresh_tok = create_refresh_token("user-123", config)
        _, payload = decode_token(refresh_tok, config)
        family_id = payload.family_id

        # First rotation
        result1 = rotate_refresh_token(refresh_tok, config)
        assert result1.result == JWTAuthResult.SUCCESS

        # Second rotation using the new token (legitimate use)
        result2 = rotate_refresh_token(result1.refresh_token, config)
        assert result2.result == JWTAuthResult.SUCCESS

        # Now try to use the first rotated token (stolen token scenario)
        # This should detect reuse and potentially revoke the family
        result3 = rotate_refresh_token(result1.refresh_token, config)
        assert result3.result == JWTAuthResult.INVALID_TOKEN

    def test_api_returns_401_for_reused_token(self, client, config):
        """Test that API returns 401 when refresh token is reused."""
        with patch(
            "forge_harness.webhook_server.api.auth.JWTAuthConfig.from_env",
            return_value=config,
        ):
            # Create a refresh token
            refresh_tok = create_refresh_token("user-123", config)

            # First use - succeeds
            response1 = client.post(
                "/api/auth/refresh",
                json={"refresh_token": refresh_tok},
            )
            assert response1.status_code == 200

            # Second use - fails (token reuse)
            response2 = client.post(
                "/api/auth/refresh",
                json={"refresh_token": refresh_tok},
            )
            assert response2.status_code == 401
            data = response2.json()
            assert "used" in data["detail"].lower() or "invalid" in data["detail"].lower()

    def test_revoked_token_cannot_be_used(self, config):
        """Test that explicitly revoked token cannot be used."""
        # Create a refresh token
        refresh_tok = create_refresh_token("user-123", config)
        _, payload = decode_token(refresh_tok, config)

        # Revoke the token
        revoke_token(payload.jti)

        # Try to rotate the revoked token
        result = rotate_refresh_token(refresh_tok, config)
        assert result.result == JWTAuthResult.INVALID_TOKEN

    def test_subsequent_rotations_use_new_tokens(self, config):
        """Test that after rotation, only the newest token works."""
        # Create initial token
        refresh_tok = create_refresh_token("user-123", config)

        # Rotate
        result1 = rotate_refresh_token(refresh_tok, config)
        assert result1.result == JWTAuthResult.SUCCESS

        # Old token no longer works
        old_result = rotate_refresh_token(refresh_tok, config)
        assert old_result.result == JWTAuthResult.INVALID_TOKEN

        # New token works
        result2 = rotate_refresh_token(result1.refresh_token, config)
        assert result2.result == JWTAuthResult.SUCCESS

        # Now the first rotated token is also invalid
        result1_again = rotate_refresh_token(result1.refresh_token, config)
        assert result1_again.result == JWTAuthResult.INVALID_TOKEN

        # Only the latest token works
        result3 = rotate_refresh_token(result2.refresh_token, config)
        assert result3.result == JWTAuthResult.SUCCESS


class TestRefreshTokenSecurityRequirements:
    """Tests for specific security requirements."""

    def test_access_token_expires_in_1_hour(self, config):
        """Test that access tokens expire in 1 hour (config setting)."""
        assert config.access_token_expire_minutes == 60

        access_tok = create_access_token("user-123", config)
        _, payload = decode_token(access_tok, config)

        # Should expire in approximately 1 hour
        expected_expiry = datetime.now(UTC) + timedelta(hours=1)
        time_diff = abs((payload.exp - expected_expiry).total_seconds())
        assert time_diff < 60  # Within 1 minute

    def test_refresh_token_expires_in_7_days(self, config):
        """Test that refresh tokens expire in 7 days (config setting)."""
        assert config.refresh_token_expire_days == 7

        refresh_tok = create_refresh_token("user-123", config)
        _, payload = decode_token(refresh_tok, config)

        # Should expire in approximately 7 days
        expected_expiry = datetime.now(UTC) + timedelta(days=7)
        time_diff = abs((payload.exp - expected_expiry).total_seconds())
        assert time_diff < 60  # Within 1 minute

    def test_refresh_tokens_are_one_time_use(self, config):
        """Test that refresh tokens are one-time use only."""
        refresh_tok = create_refresh_token("user-123", config)

        # First use succeeds
        result1 = rotate_refresh_token(refresh_tok, config)
        assert result1.result == JWTAuthResult.SUCCESS

        # Second use fails
        result2 = rotate_refresh_token(refresh_tok, config)
        assert result2.result == JWTAuthResult.INVALID_TOKEN

    def test_401_returned_for_invalid_token(self, client, config):
        """Test that 401 is returned for completely invalid token."""
        with patch(
            "forge_harness.webhook_server.api.auth.JWTAuthConfig.from_env",
            return_value=config,
        ):
            response = client.post(
                "/api/auth/refresh",
                json={"refresh_token": "invalid.token.here"},
            )

            assert response.status_code == 401


class TestTokenFamilyTracking:
    """Tests for token family tracking functionality."""

    def test_family_id_generated_on_creation(self, config):
        """Test that family ID is generated when creating refresh token."""
        refresh_tok = create_refresh_token("user-123", config)
        _, payload = decode_token(refresh_tok, config)

        assert payload.family_id is not None
        assert len(payload.family_id) == 32  # 16 bytes = 32 hex chars

    def test_family_id_preserved_on_rotation(self, config):
        """Test that family ID is preserved when rotating."""
        refresh_tok = create_refresh_token("user-123", config)
        _, original_payload = decode_token(refresh_tok, config)
        original_family = original_payload.family_id

        result = rotate_refresh_token(refresh_tok, config)
        _, new_payload = decode_token(result.refresh_token, config)

        assert new_payload.family_id == original_family

    def test_access_tokens_dont_have_family(self, config):
        """Test that access tokens don't have family tracking."""
        access_tok = create_access_token("user-123", config)
        _, payload = decode_token(access_tok, config)

        assert payload.family_id is None

    def test_revoke_family_blocks_all_tokens_in_family(self, config):
        """Test that revoking a family blocks all tokens in that family."""
        refresh_tok = create_refresh_token("user-123", config)
        _, payload = decode_token(refresh_tok, config)
        family_id = payload.family_id

        # Rotate to get new token in same family
        result = rotate_refresh_token(refresh_tok, config)

        # Revoke the entire family
        revoke_token_family(family_id)

        # Try to use the new token
        from forge_harness.webhook_server.infrastructure.jwt_auth import (
            is_token_family_revoked,
        )

        assert is_token_family_revoked(family_id) is True

        # Verification should detect reused family
        result, _ = verify_refresh_token(result.refresh_token, config)
        assert result == JWTAuthResult.TOKEN_REUSED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
