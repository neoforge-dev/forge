"""JWT Revocation Hardening Tests

Tests for PR #59 security improvements:
- Token revocation checking in verify_access_token
- Token revocation checking in verify_refresh_token
- Old refresh token revocation during token refresh
- RateLimiter.get_remaining() method
"""

import pytest

from forge_harness.webhook_server.infrastructure.jwt_auth import (
    JWTAuthConfig,
    JWTAuthResult,
    _revoked_tokens,
    create_access_token,
    create_refresh_token,
    is_token_revoked,
    revoke_token,
    verify_access_token,
    verify_refresh_token,
)
from forge_harness.webhook_server.infrastructure.rate_limiter import (
    RateLimitConfig,
    RateLimiter,
)


class TestAccessTokenRevocation:
    """Test that verify_access_token checks revocation status."""

    def setup_method(self):
        """Clear revoked tokens before each test."""
        _revoked_tokens.clear()

    def test_valid_access_token_not_revoked(self):
        """Valid access token should pass when not revoked."""
        config = JWTAuthConfig(secret_key="test-secret-123")
        token = create_access_token("user123", config)

        result, payload = verify_access_token(token, config)

        assert result == JWTAuthResult.SUCCESS
        assert payload is not None
        assert payload.sub == "user123"

    def test_access_token_fails_when_revoked(self):
        """Revoked access token should fail verification."""
        config = JWTAuthConfig(secret_key="test-secret-123")
        token = create_access_token("user123", config)

        # First verify it works
        result, payload = verify_access_token(token, config)
        assert result == JWTAuthResult.SUCCESS

        # Revoke the token
        revoke_token(payload.jti)
        assert is_token_revoked(payload.jti)

        # Now verify should fail
        result, payload = verify_access_token(token, config)
        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None


class TestRefreshTokenRevocation:
    """Test that verify_refresh_token checks revocation status."""

    def setup_method(self):
        """Clear revoked tokens before each test."""
        _revoked_tokens.clear()

    def test_valid_refresh_token_not_revoked(self):
        """Valid refresh token should pass when not revoked."""
        config = JWTAuthConfig(secret_key="test-secret-456")
        token = create_refresh_token("user456", config)

        result, payload = verify_refresh_token(token, config)

        assert result == JWTAuthResult.SUCCESS
        assert payload is not None
        assert payload.sub == "user456"

    def test_refresh_token_fails_when_revoked(self):
        """Revoked refresh token should fail verification."""
        config = JWTAuthConfig(secret_key="test-secret-456")
        token = create_refresh_token("user456", config)

        # First verify it works
        result, payload = verify_refresh_token(token, config)
        assert result == JWTAuthResult.SUCCESS

        # Revoke the token
        revoke_token(payload.jti)
        assert is_token_revoked(payload.jti)

        # Now verify should fail
        result, payload = verify_refresh_token(token, config)
        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None


class TestRateLimiterGetRemaining:
    """Test RateLimiter.get_remaining() method."""

    def test_get_remaining_new_client(self):
        """New client should have full burst_size available."""
        config = RateLimitConfig(
            requests_per_minute=60,
            burst_size=10,
            enabled=True,
        )
        limiter = RateLimiter(config)

        remaining = limiter.get_remaining("192.168.1.1")

        assert remaining == 10

    def test_get_remaining_after_consumption(self):
        """Remaining count should decrease after consuming tokens."""
        config = RateLimitConfig(
            requests_per_minute=60,
            burst_size=10,
            enabled=True,
        )
        limiter = RateLimiter(config)
        client_ip = "192.168.1.2"

        # Consume 3 tokens
        for _ in range(3):
            assert limiter.is_allowed(client_ip)

        remaining = limiter.get_remaining(client_ip)

        assert remaining == 7

    def test_get_remaining_depleted(self):
        """Remaining should be 0 when bucket is empty."""
        config = RateLimitConfig(
            requests_per_minute=60,
            burst_size=5,
            enabled=True,
        )
        limiter = RateLimiter(config)
        client_ip = "192.168.1.3"

        # Consume all tokens
        for _ in range(5):
            assert limiter.is_allowed(client_ip)

        # Next request should be denied
        assert not limiter.is_allowed(client_ip)

        remaining = limiter.get_remaining(client_ip)

        assert remaining == 0

    def test_get_remaining_multiple_clients(self):
        """Each client should have independent token counts."""
        config = RateLimitConfig(
            requests_per_minute=60,
            burst_size=10,
            enabled=True,
        )
        limiter = RateLimiter(config)

        # Client 1 consumes 2 tokens
        client1 = "192.168.1.4"
        for _ in range(2):
            limiter.is_allowed(client1)

        # Client 2 consumes 5 tokens
        client2 = "192.168.1.5"
        for _ in range(5):
            limiter.is_allowed(client2)

        assert limiter.get_remaining(client1) == 8
        assert limiter.get_remaining(client2) == 5
