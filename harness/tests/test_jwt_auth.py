"""Tests for JWT Authentication Infrastructure.

Tests JWT token generation, validation, and refresh capabilities.
"""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from forge_harness.webhook_server.infrastructure.jwt_auth import (
    JWTAuthConfig,
    JWTAuthResult,
    JWTPayload,
    _revoked_tokens,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_token_hash,
    is_token_revoked,
    refresh_access_token,
    revoke_token,
    verify_access_token,
    verify_bearer_token_jwt,
    verify_refresh_token,
)


class TestJWTAuthResult:
    """Tests for JWTAuthResult enum."""

    def test_enum_values(self):
        """Test all enum values exist."""
        assert JWTAuthResult.SUCCESS.value == "success"
        assert JWTAuthResult.MISSING_TOKEN.value == "missing_token"
        assert JWTAuthResult.INVALID_TOKEN.value == "invalid_token"
        assert JWTAuthResult.EXPIRED_TOKEN.value == "expired_token"
        assert JWTAuthResult.INVALID_FORMAT.value == "invalid_format"


class TestJWTAuthConfig:
    """Tests for JWTAuthConfig dataclass."""

    def test_create_config(self):
        """Test creating config with all fields."""
        config = JWTAuthConfig(
            secret_key="test-secret",
            algorithm="HS384",
            access_token_expire_minutes=60,
            refresh_token_expire_days=14,
            require_auth=False,
        )
        assert config.secret_key == "test-secret"
        assert config.algorithm == "HS384"
        assert config.access_token_expire_minutes == 60
        assert config.refresh_token_expire_days == 14
        assert config.require_auth is False

    def test_create_config_defaults(self):
        """Test default values."""
        config = JWTAuthConfig(secret_key="secret")
        assert config.algorithm == "HS256"
        assert config.access_token_expire_minutes == 60  # 1 hour per security requirements
        assert config.refresh_token_expire_days == 7
        assert config.require_auth is True

    def test_from_env_with_secret(self):
        """Test from_env with FORGE_JWT_SECRET set."""
        env = {"FORGE_JWT_SECRET": "env-secret-key"}
        with patch.dict(os.environ, env, clear=True):
            config = JWTAuthConfig.from_env()
            assert config.secret_key == "env-secret-key"

    def test_from_env_without_secret(self):
        """Test from_env generates random secret when not set."""
        with patch.dict(os.environ, {}, clear=True):
            config = JWTAuthConfig.from_env()
            assert len(config.secret_key) > 20  # Random secret is long

    def test_from_env_custom_values(self):
        """Test from_env reads all custom env vars."""
        env = {
            "FORGE_JWT_SECRET": "custom-secret",
            "FORGE_JWT_ALGORITHM": "HS512",
            "FORGE_JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "120",
            "FORGE_JWT_REFRESH_TOKEN_EXPIRE_DAYS": "30",
            "FORGE_JWT_REQUIRE_AUTH": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            config = JWTAuthConfig.from_env()
            assert config.algorithm == "HS512"
            assert config.access_token_expire_minutes == 120
            assert config.refresh_token_expire_days == 30
            assert config.require_auth is False

    def test_from_env_require_auth_variants(self):
        """Test require_auth parses various true values."""
        for true_val in ["true", "1", "yes", "TRUE", "Yes"]:
            with patch.dict(os.environ, {"FORGE_JWT_SECRET": "s", "FORGE_JWT_REQUIRE_AUTH": true_val}):
                config = JWTAuthConfig.from_env()
                assert config.require_auth is True

        for false_val in ["false", "0", "no", "anything"]:
            with patch.dict(os.environ, {"FORGE_JWT_SECRET": "s", "FORGE_JWT_REQUIRE_AUTH": false_val}):
                config = JWTAuthConfig.from_env()
                assert config.require_auth is False


class TestJWTPayload:
    """Tests for JWTPayload dataclass."""

    def test_create_payload(self):
        """Test creating payload."""
        now = datetime.now(UTC)
        payload = JWTPayload(
            sub="user-123",
            exp=now + timedelta(hours=1),
            iat=now,
            jti="token-id-456",
            type="access",
            permissions=["read", "write"],
        )
        assert payload.sub == "user-123"
        assert payload.type == "access"
        assert payload.permissions == ["read", "write"]

    def test_to_dict(self):
        """Test converting payload to dict."""
        now = datetime.now(UTC)
        exp = now + timedelta(hours=1)
        payload = JWTPayload(
            sub="user",
            exp=exp,
            iat=now,
            jti="jti-123",
            type="access",
            permissions=["admin"],
        )

        result = payload.to_dict()

        assert result["sub"] == "user"
        assert result["exp"] == exp
        assert result["iat"] == now
        assert result["jti"] == "jti-123"
        assert result["type"] == "access"
        assert result["permissions"] == ["admin"]

    def test_from_dict(self):
        """Test creating payload from dict."""
        now_ts = datetime.now(UTC).timestamp()
        exp_ts = now_ts + 3600

        data = {
            "sub": "service-a",
            "exp": exp_ts,
            "iat": now_ts,
            "jti": "abc-123",
            "type": "refresh",
            "permissions": ["api:read"],
        }

        payload = JWTPayload.from_dict(data)

        assert payload.sub == "service-a"
        assert payload.jti == "abc-123"
        assert payload.type == "refresh"
        assert payload.permissions == ["api:read"]

    def test_from_dict_missing_fields(self):
        """Test from_dict handles missing fields."""
        data = {}

        payload = JWTPayload.from_dict(data)

        assert payload.sub == ""
        assert payload.jti == ""
        assert payload.type == "access"
        assert payload.permissions == []


class TestCreateAccessToken:
    """Tests for create_access_token function."""

    @pytest.fixture
    def config(self):
        """Create test config."""
        return JWTAuthConfig(
            secret_key="test-secret-key-12345",
            algorithm="HS256",
            access_token_expire_minutes=30,
        )

    def test_create_token(self, config):
        """Test creating an access token."""
        token = create_access_token("user-123", config)

        assert isinstance(token, str)
        assert len(token) > 50  # JWT tokens are long

    def test_create_token_with_permissions(self, config):
        """Test creating token with permissions."""
        token = create_access_token(
            "user-456",
            config,
            permissions=["read", "write", "admin"],
        )

        # Decode to verify
        result, payload = decode_token(token, config)
        assert result == JWTAuthResult.SUCCESS
        assert payload.permissions == ["read", "write", "admin"]

    def test_create_token_custom_expiry(self, config):
        """Test creating token with custom expiration."""
        token = create_access_token(
            "user-789",
            config,
            expires_delta=timedelta(hours=2),
        )

        result, payload = decode_token(token, config)
        assert result == JWTAuthResult.SUCCESS
        # Should expire in ~2 hours
        assert payload.exp > datetime.now(UTC) + timedelta(hours=1, minutes=50)


class TestCreateRefreshToken:
    """Tests for create_refresh_token function."""

    @pytest.fixture
    def config(self):
        """Create test config."""
        return JWTAuthConfig(
            secret_key="test-secret-key-12345",
            refresh_token_expire_days=7,
        )

    def test_create_refresh_token(self, config):
        """Test creating a refresh token."""
        token = create_refresh_token("user-123", config)

        assert isinstance(token, str)
        assert len(token) > 50

    def test_refresh_token_type(self, config):
        """Test refresh token has correct type."""
        token = create_refresh_token("user-123", config)

        result, payload = decode_token(token, config)
        assert result == JWTAuthResult.SUCCESS
        assert payload.type == "refresh"


class TestDecodeToken:
    """Tests for decode_token function."""

    @pytest.fixture
    def config(self):
        """Create test config."""
        return JWTAuthConfig(secret_key="test-secret-key-12345")

    def test_decode_valid_token(self, config):
        """Test decoding a valid token."""
        token = create_access_token("user-123", config)

        result, payload = decode_token(token, config)

        assert result == JWTAuthResult.SUCCESS
        assert payload is not None
        assert payload.sub == "user-123"
        assert payload.type == "access"

    def test_decode_expired_token(self, config):
        """Test decoding an expired token."""
        token = create_access_token(
            "user-123",
            config,
            expires_delta=timedelta(seconds=-10),  # Already expired
        )

        result, payload = decode_token(token, config)

        assert result == JWTAuthResult.EXPIRED_TOKEN
        assert payload is None

    def test_decode_invalid_token(self, config):
        """Test decoding an invalid token."""
        result, payload = decode_token("invalid.jwt.token", config)

        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None

    def test_decode_wrong_secret(self, config):
        """Test decoding with wrong secret fails."""
        token = create_access_token("user-123", config)

        wrong_config = JWTAuthConfig(secret_key="wrong-secret")
        result, payload = decode_token(token, wrong_config)

        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None


class TestVerifyAccessToken:
    """Tests for verify_access_token function."""

    @pytest.fixture
    def config(self):
        """Create test config."""
        return JWTAuthConfig(secret_key="test-secret-key-12345")

    @pytest.fixture(autouse=True)
    def clear_revoked(self):
        """Clear revoked tokens before each test."""
        _revoked_tokens.clear()

    def test_verify_valid_access_token(self, config):
        """Test verifying a valid access token."""
        token = create_access_token("user-123", config)

        result, payload = verify_access_token(token, config)

        assert result == JWTAuthResult.SUCCESS
        assert payload is not None
        assert payload.sub == "user-123"

    def test_verify_refresh_token_as_access_fails(self, config):
        """Test that refresh token fails access verification."""
        token = create_refresh_token("user-123", config)

        result, payload = verify_access_token(token, config)

        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None

    def test_verify_revoked_token_fails(self, config):
        """Test that revoked token fails verification."""
        token = create_access_token("user-123", config)

        # Decode to get JTI
        _, payload = decode_token(token, config)
        revoke_token(payload.jti)

        result, payload = verify_access_token(token, config)

        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None


class TestVerifyRefreshToken:
    """Tests for verify_refresh_token function."""

    @pytest.fixture
    def config(self):
        """Create test config."""
        return JWTAuthConfig(secret_key="test-secret-key-12345")

    @pytest.fixture(autouse=True)
    def clear_revoked(self):
        """Clear revoked tokens before each test."""
        _revoked_tokens.clear()

    def test_verify_valid_refresh_token(self, config):
        """Test verifying a valid refresh token."""
        token = create_refresh_token("user-123", config)

        result, payload = verify_refresh_token(token, config)

        assert result == JWTAuthResult.SUCCESS
        assert payload is not None
        assert payload.type == "refresh"

    def test_verify_access_token_as_refresh_fails(self, config):
        """Test that access token fails refresh verification."""
        token = create_access_token("user-123", config)

        result, payload = verify_refresh_token(token, config)

        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None


class TestRefreshAccessToken:
    """Tests for refresh_access_token function."""

    @pytest.fixture
    def config(self):
        """Create test config."""
        return JWTAuthConfig(secret_key="test-secret-key-12345")

    @pytest.fixture(autouse=True)
    def clear_revoked(self):
        """Clear revoked tokens before each test."""
        _revoked_tokens.clear()

    def test_refresh_success(self, config):
        """Test successful token refresh."""
        refresh_tok = create_refresh_token(
            "user-123",
            config,
            permissions=["read", "write"],
        )

        result, new_access = refresh_access_token(refresh_tok, config)

        assert result == JWTAuthResult.SUCCESS
        assert new_access is not None

        # Verify new access token
        verify_result, payload = verify_access_token(new_access, config)
        assert verify_result == JWTAuthResult.SUCCESS
        assert payload.sub == "user-123"
        assert payload.permissions == ["read", "write"]

    def test_refresh_with_access_token_fails(self, config):
        """Test refreshing with access token fails."""
        access_tok = create_access_token("user-123", config)

        result, new_access = refresh_access_token(access_tok, config)

        assert result == JWTAuthResult.INVALID_TOKEN
        assert new_access is None


class TestTokenRevocation:
    """Tests for token revocation functions."""

    @pytest.fixture(autouse=True)
    def clear_revoked(self):
        """Clear revoked tokens before each test."""
        _revoked_tokens.clear()

    def test_revoke_token(self):
        """Test revoking a token."""
        jti = "test-jti-123"
        assert is_token_revoked(jti) is False

        revoke_token(jti)

        assert is_token_revoked(jti) is True

    def test_is_token_revoked_false(self):
        """Test is_token_revoked returns False for unknown JTI."""
        assert is_token_revoked("unknown-jti") is False


class TestGetTokenHash:
    """Tests for get_token_hash function."""

    def test_get_hash(self):
        """Test getting token hash."""
        token = "some.jwt.token"

        hash_val = get_token_hash(token)

        assert isinstance(hash_val, str)
        assert len(hash_val) == 16  # Truncated to 16 chars

    def test_hash_deterministic(self):
        """Test hash is deterministic."""
        token = "test.token.value"

        hash1 = get_token_hash(token)
        hash2 = get_token_hash(token)

        assert hash1 == hash2

    def test_different_tokens_different_hashes(self):
        """Test different tokens produce different hashes."""
        hash1 = get_token_hash("token1")
        hash2 = get_token_hash("token2")

        assert hash1 != hash2


class TestVerifyBearerTokenJWT:
    """Tests for verify_bearer_token_jwt function."""

    @pytest.fixture
    def config(self):
        """Create test config."""
        return JWTAuthConfig(secret_key="test-secret-key-12345")

    @pytest.fixture(autouse=True)
    def clear_revoked(self):
        """Clear revoked tokens before each test."""
        _revoked_tokens.clear()

    def test_missing_authorization(self, config):
        """Test with missing authorization header."""
        result, payload = verify_bearer_token_jwt(None, config)

        assert result == JWTAuthResult.MISSING_TOKEN
        assert payload is None

    def test_invalid_format_no_bearer(self, config):
        """Test with non-Bearer authorization."""
        result, payload = verify_bearer_token_jwt("Basic abc123", config)

        assert result == JWTAuthResult.INVALID_FORMAT
        assert payload is None

    def test_valid_bearer_token(self, config):
        """Test with valid Bearer token."""
        token = create_access_token("user-123", config)

        result, payload = verify_bearer_token_jwt(f"Bearer {token}", config)

        assert result == JWTAuthResult.SUCCESS
        assert payload is not None
        assert payload.sub == "user-123"

    def test_revoked_bearer_token(self, config):
        """Test with revoked Bearer token."""
        token = create_access_token("user-123", config)

        # Revoke the token
        _, payload = decode_token(token, config)
        revoke_token(payload.jti)

        result, _ = verify_bearer_token_jwt(f"Bearer {token}", config)

        assert result == JWTAuthResult.INVALID_TOKEN

    def test_bearer_with_whitespace(self, config):
        """Test Bearer token with extra whitespace."""
        token = create_access_token("user-123", config)

        result, payload = verify_bearer_token_jwt(f"Bearer   {token}  ", config)

        assert result == JWTAuthResult.SUCCESS
        assert payload is not None
