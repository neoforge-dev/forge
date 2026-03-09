"""Extended tests for JWT Authentication Infrastructure.

Covers uncovered paths: JWT unavailable branches, general exception handler,
early-return paths in verify functions, revoked refresh tokens, duplicate
revocation check in verify_bearer_token_jwt, and additional edge cases.
"""

import hashlib
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import forge_harness.webhook_server.infrastructure.jwt_auth as jwt_auth_module
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(**kwargs) -> JWTAuthConfig:
    """Return a JWTAuthConfig suitable for testing."""
    defaults = {"secret_key": "test-extended-secret-key-9999"}
    defaults.update(kwargs)
    return JWTAuthConfig(**defaults)


# ---------------------------------------------------------------------------
# JWT unavailable branches (lines 20-21, 136-137, 172-173, 202-204)
# ---------------------------------------------------------------------------

class TestJWTUnavailable:
    """Tests for paths executed when the PyJWT library is not installed."""

    def test_create_access_token_raises_when_jwt_unavailable(self):
        """create_access_token raises RuntimeError when JWT_AVAILABLE is False."""
        config = make_config()
        with patch.object(jwt_auth_module, "JWT_AVAILABLE", False):
            with pytest.raises(RuntimeError, match="PyJWT library not installed"):
                create_access_token("user-x", config)

    def test_create_refresh_token_raises_when_jwt_unavailable(self):
        """create_refresh_token raises RuntimeError when JWT_AVAILABLE is False."""
        config = make_config()
        with patch.object(jwt_auth_module, "JWT_AVAILABLE", False):
            with pytest.raises(RuntimeError, match="PyJWT library not installed"):
                create_refresh_token("user-x", config)

    def test_decode_token_returns_invalid_when_jwt_unavailable(self):
        """decode_token returns INVALID_TOKEN, None when JWT_AVAILABLE is False."""
        config = make_config()
        with patch.object(jwt_auth_module, "JWT_AVAILABLE", False):
            result, payload = decode_token("any.token.here", config)
        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None

    def test_decode_token_logs_error_when_jwt_unavailable(self):
        """decode_token calls logger.error when JWT is unavailable."""
        config = make_config()
        with patch.object(jwt_auth_module, "JWT_AVAILABLE", False):
            with patch.object(jwt_auth_module.logger, "error") as mock_error:
                decode_token("any.token.here", config)
        mock_error.assert_called_once()
        call_args = mock_error.call_args[0][0]
        assert "pyjwt" in call_args.lower() or "not installed" in call_args.lower()


# ---------------------------------------------------------------------------
# General exception handler in decode_token (lines 217-219)
# ---------------------------------------------------------------------------

class TestDecodeTokenGeneralException:
    """Tests for the bare Exception handler inside decode_token."""

    def test_decode_token_handles_unexpected_exception(self):
        """decode_token returns INVALID_TOKEN on unexpected errors."""
        config = make_config()
        # Patch jwt.decode to raise an arbitrary non-JWT exception
        with patch.object(jwt_auth_module.jwt, "decode", side_effect=ValueError("unexpected")):
            result, payload = decode_token("some.token.value", config)
        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None

    def test_decode_token_handles_os_error(self):
        """decode_token handles OSError from jwt.decode gracefully."""
        config = make_config()
        with patch.object(jwt_auth_module.jwt, "decode", side_effect=OSError("disk error")):
            result, payload = decode_token("some.token.value", config)
        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None

    def test_decode_token_logs_error_on_unexpected_exception(self):
        """decode_token calls logger.error on unexpected exceptions."""
        config = make_config()
        with patch.object(jwt_auth_module.jwt, "decode", side_effect=RuntimeError("boom")):
            with patch.object(jwt_auth_module.logger, "error") as mock_error:
                decode_token("some.token.value", config)
        mock_error.assert_called_once()
        call_args = mock_error.call_args[0][0]
        assert "error" in call_args.lower() or "boom" in call_args.lower()


# ---------------------------------------------------------------------------
# verify_access_token early-return path (line 235)
# ---------------------------------------------------------------------------

class TestVerifyAccessTokenEarlyReturn:
    """Tests for the early-return when decode_token does not succeed."""

    @pytest.fixture(autouse=True)
    def clear_revoked(self):
        _revoked_tokens.clear()

    def test_verify_access_token_returns_expired_from_decode(self):
        """verify_access_token propagates EXPIRED_TOKEN from decode_token."""
        config = make_config()
        expired_token = create_access_token(
            "user-x", config, expires_delta=timedelta(seconds=-1)
        )
        result, payload = verify_access_token(expired_token, config)
        assert result == JWTAuthResult.EXPIRED_TOKEN
        assert payload is None

    def test_verify_access_token_returns_invalid_from_decode(self):
        """verify_access_token propagates INVALID_TOKEN for malformed token."""
        config = make_config()
        result, payload = verify_access_token("not.a.real.token", config)
        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None

    def test_verify_access_token_wrong_secret_propagates(self):
        """verify_access_token propagates INVALID_TOKEN for wrong-secret token."""
        config = make_config()
        token = create_access_token("user-x", config)
        wrong_config = make_config(secret_key="completely-different-secret")
        result, payload = verify_access_token(token, wrong_config)
        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None


# ---------------------------------------------------------------------------
# verify_refresh_token — early-return + revoked token paths (lines 262, 270-271)
# ---------------------------------------------------------------------------

class TestVerifyRefreshTokenPaths:
    """Tests for the various return paths in verify_refresh_token."""

    @pytest.fixture(autouse=True)
    def clear_revoked(self):
        _revoked_tokens.clear()

    def test_verify_refresh_token_returns_expired(self):
        """verify_refresh_token propagates EXPIRED_TOKEN result."""
        config = make_config(refresh_token_expire_days=0)
        # Create a token that expires immediately (using access token machinery
        # with refresh type by patching, or just verify an expired refresh token)
        # We'll build a legitimate refresh token then let it expire via negative delta.
        # The refresh token creation does not accept expires_delta, so we craft
        # an access-path-expired token via decode.
        token = create_access_token(
            "user-y", config, expires_delta=timedelta(seconds=-5)
        )
        # Even though this is technically an access token, decode will return
        # EXPIRED_TOKEN before we check the type field — hitting line 262.
        result, payload = verify_refresh_token(token, config)
        assert result == JWTAuthResult.EXPIRED_TOKEN
        assert payload is None

    def test_verify_refresh_token_returns_invalid_on_bad_token(self):
        """verify_refresh_token propagates INVALID_TOKEN for garbage input."""
        config = make_config()
        result, payload = verify_refresh_token("garbage.token.data", config)
        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None

    def test_verify_refresh_token_revoked_returns_invalid(self):
        """verify_refresh_token returns INVALID_TOKEN for a revoked refresh token."""
        config = make_config()
        token = create_refresh_token("user-z", config)

        # Obtain JTI from the token
        _, decoded = decode_token(token, config)
        assert decoded is not None
        revoke_token(decoded.jti)

        result, payload = verify_refresh_token(token, config)
        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None

    def test_verify_refresh_token_revoked_logs_warning(self):
        """verify_refresh_token calls logger.warning when refresh token is revoked."""
        config = make_config()
        token = create_refresh_token("user-warn", config)
        _, decoded = decode_token(token, config)
        assert decoded is not None
        revoke_token(decoded.jti)

        with patch.object(jwt_auth_module.logger, "warning") as mock_warning:
            verify_refresh_token(token, config)
        assert mock_warning.called
        call_args = mock_warning.call_args[0][0]
        assert "revoked" in call_args.lower()


# ---------------------------------------------------------------------------
# verify_bearer_token_jwt — duplicate revocation check (lines 369-370)
# ---------------------------------------------------------------------------

class TestVerifyBearerTokenJWTRevocationPath:
    """Tests for the secondary revocation check in verify_bearer_token_jwt.

    verify_access_token already checks the blacklist, but verify_bearer_token_jwt
    has an additional guard (lines 366-370).  This extra check is hit when
    verify_access_token returns SUCCESS but the jti is in _revoked_tokens at
    the time the outer function runs its own check.  We trigger it by injecting
    a mock verify_access_token that returns SUCCESS + a payload whose jti is
    already revoked.
    """

    @pytest.fixture(autouse=True)
    def clear_revoked(self):
        _revoked_tokens.clear()

    def test_bearer_secondary_revocation_check_returns_invalid(self):
        """verify_bearer_token_jwt secondary revoke-check returns INVALID_TOKEN."""
        config = make_config()
        token = create_access_token("user-sec", config)

        # Decode to get the real JTI so we can add it to the blacklist
        _, real_payload = decode_token(token, config)
        assert real_payload is not None
        jti = real_payload.jti

        # Build a fake payload object whose jti is revoked
        fake_payload = JWTPayload(
            sub="user-sec",
            exp=datetime.now(UTC) + timedelta(hours=1),
            iat=datetime.now(UTC),
            jti=jti,
            type="access",
            permissions=[],
        )

        # Add the jti to the revocation set
        _revoked_tokens.add(jti)

        # Patch verify_access_token to return SUCCESS + fake_payload,
        # bypassing its own revocation check, so the outer guard is what fires.
        with patch.object(
            jwt_auth_module,
            "verify_access_token",
            return_value=(JWTAuthResult.SUCCESS, fake_payload),
        ):
            result, payload = verify_bearer_token_jwt(f"Bearer {token}", config)

        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None

    def test_bearer_secondary_revocation_logs_warning(self):
        """verify_bearer_token_jwt calls logger.warning on secondary revocation."""
        config = make_config()
        token = create_access_token("user-log", config)

        _, real_payload = decode_token(token, config)
        assert real_payload is not None
        jti = real_payload.jti

        fake_payload = JWTPayload(
            sub="user-log",
            exp=datetime.now(UTC) + timedelta(hours=1),
            iat=datetime.now(UTC),
            jti=jti,
            type="access",
            permissions=[],
        )
        _revoked_tokens.add(jti)

        with patch.object(
            jwt_auth_module,
            "verify_access_token",
            return_value=(JWTAuthResult.SUCCESS, fake_payload),
        ):
            with patch.object(jwt_auth_module.logger, "warning") as mock_warning:
                verify_bearer_token_jwt(f"Bearer {token}", config)

        assert mock_warning.called
        call_args = mock_warning.call_args[0][0]
        assert "revoked" in call_args.lower()


# ---------------------------------------------------------------------------
# Additional edge-case tests not in the original suite
# ---------------------------------------------------------------------------

class TestJWTPayloadEdgeCases:
    """Edge cases for JWTPayload construction and serialisation."""

    def test_from_dict_no_exp_field_uses_now(self):
        """JWTPayload.from_dict falls back to datetime.now when 'exp' absent."""
        before = datetime.now(UTC)
        payload = JWTPayload.from_dict({"sub": "s", "jti": "j", "type": "access", "permissions": []})
        after = datetime.now(UTC)
        assert before <= payload.exp <= after

    def test_from_dict_no_iat_field_uses_now(self):
        """JWTPayload.from_dict falls back to datetime.now when 'iat' absent."""
        before = datetime.now(UTC)
        payload = JWTPayload.from_dict({"sub": "s", "exp": (before + timedelta(hours=1)).timestamp()})
        after = datetime.now(UTC)
        assert before <= payload.iat <= after

    def test_to_dict_round_trips_permissions(self):
        """Serialise and deserialise permissions list correctly."""
        now = datetime.now(UTC)
        original = JWTPayload(
            sub="svc",
            exp=now + timedelta(minutes=15),
            iat=now,
            jti="rnd",
            type="access",
            permissions=["fleet:read", "fleet:write", "admin"],
        )
        d = original.to_dict()
        assert d["permissions"] == ["fleet:read", "fleet:write", "admin"]

    def test_to_dict_preserves_refresh_type(self):
        """to_dict preserves 'refresh' type field."""
        now = datetime.now(UTC)
        payload = JWTPayload(
            sub="svc",
            exp=now + timedelta(days=7),
            iat=now,
            jti="rid",
            type="refresh",
            permissions=[],
        )
        assert payload.to_dict()["type"] == "refresh"

    def test_from_dict_defaults_type_to_access(self):
        """Missing 'type' key defaults to 'access'."""
        payload = JWTPayload.from_dict({"sub": "u"})
        assert payload.type == "access"

    def test_from_dict_defaults_permissions_to_empty(self):
        """Missing 'permissions' key defaults to empty list."""
        payload = JWTPayload.from_dict({"sub": "u"})
        assert payload.permissions == []


class TestJWTAuthConfigFromEnvEdgeCases:
    """Edge cases for JWTAuthConfig.from_env()."""

    def test_require_auth_true_values(self):
        """All accepted 'true' string values enable require_auth."""
        for val in ("true", "1", "yes", "TRUE", "YES", "True"):
            with patch.dict(os.environ, {"FORGE_JWT_SECRET": "s", "FORGE_JWT_REQUIRE_AUTH": val}, clear=True):
                cfg = JWTAuthConfig.from_env()
                assert cfg.require_auth is True, f"Expected True for require_auth with value={val!r}"

    def test_require_auth_false_values(self):
        """Non-truthy strings disable require_auth."""
        for val in ("false", "0", "no", "off", "nope"):
            with patch.dict(os.environ, {"FORGE_JWT_SECRET": "s", "FORGE_JWT_REQUIRE_AUTH": val}, clear=True):
                cfg = JWTAuthConfig.from_env()
                assert cfg.require_auth is False, f"Expected False for require_auth with value={val!r}"

    def test_from_env_generates_different_secrets_each_time(self):
        """Each call without FORGE_JWT_SECRET generates a unique random secret."""
        with patch.dict(os.environ, {}, clear=True):
            cfg1 = JWTAuthConfig.from_env()
            cfg2 = JWTAuthConfig.from_env()
        assert cfg1.secret_key != cfg2.secret_key

    def test_from_env_default_algorithm(self):
        """Default algorithm is HS256 when FORGE_JWT_ALGORITHM is not set."""
        with patch.dict(os.environ, {"FORGE_JWT_SECRET": "sec"}, clear=True):
            cfg = JWTAuthConfig.from_env()
        assert cfg.algorithm == "HS256"

    def test_from_env_custom_algorithm(self):
        """Custom algorithm is read from environment variable."""
        with patch.dict(os.environ, {"FORGE_JWT_SECRET": "sec", "FORGE_JWT_ALGORITHM": "HS512"}, clear=True):
            cfg = JWTAuthConfig.from_env()
        assert cfg.algorithm == "HS512"


class TestTokenCreationDetails:
    """Detailed tests for token creation properties."""

    @pytest.fixture(autouse=True)
    def clear_revoked(self):
        _revoked_tokens.clear()

    def test_access_token_sub_matches_subject(self):
        """Access token embeds the correct subject claim."""
        config = make_config()
        token = create_access_token("forge-agent-42", config)
        _, payload = decode_token(token, config)
        assert payload is not None
        assert payload.sub == "forge-agent-42"

    def test_access_token_has_unique_jti(self):
        """Each access token has a unique JTI."""
        config = make_config()
        tokens = [create_access_token("u", config) for _ in range(5)]
        jtis = set()
        for t in tokens:
            _, p = decode_token(t, config)
            assert p is not None
            jtis.add(p.jti)
        assert len(jtis) == 5

    def test_access_token_default_expiry_approximately_30min(self):
        """Default access token expires within ~30 minutes."""
        config = make_config(access_token_expire_minutes=30)
        token = create_access_token("u", config)
        _, payload = decode_token(token, config)
        assert payload is not None
        delta = payload.exp - datetime.now(UTC)
        assert timedelta(minutes=28) <= delta <= timedelta(minutes=31)

    def test_refresh_token_sub_matches_subject(self):
        """Refresh token embeds the correct subject claim."""
        config = make_config()
        token = create_refresh_token("svc-99", config)
        _, payload = decode_token(token, config)
        assert payload is not None
        assert payload.sub == "svc-99"

    def test_refresh_token_permissions_preserved(self):
        """Permissions are stored in the refresh token."""
        config = make_config()
        perms = ["api:read", "api:write"]
        token = create_refresh_token("svc", config, permissions=perms)
        _, payload = decode_token(token, config)
        assert payload is not None
        assert payload.permissions == perms

    def test_refresh_token_default_expiry_approximately_7days(self):
        """Refresh token expires in approximately 7 days by default."""
        config = make_config(refresh_token_expire_days=7)
        token = create_refresh_token("u", config)
        _, payload = decode_token(token, config)
        assert payload is not None
        delta = payload.exp - datetime.now(UTC)
        assert timedelta(days=6, hours=23) <= delta <= timedelta(days=7, hours=1)

    def test_access_token_empty_permissions_default(self):
        """Access token created without permissions gets an empty list."""
        config = make_config()
        token = create_access_token("u", config)
        _, payload = decode_token(token, config)
        assert payload is not None
        assert payload.permissions == []

    def test_refresh_token_empty_permissions_default(self):
        """Refresh token created without permissions gets an empty list."""
        config = make_config()
        token = create_refresh_token("u", config)
        _, payload = decode_token(token, config)
        assert payload is not None
        assert payload.permissions == []


class TestRefreshAccessTokenEdgeCases:
    """Additional edge cases for refresh_access_token."""

    @pytest.fixture(autouse=True)
    def clear_revoked(self):
        _revoked_tokens.clear()

    def test_refresh_with_expired_refresh_token_fails(self):
        """refresh_access_token fails when the refresh token itself has expired."""
        config = make_config()
        # We'll use an expired access token to simulate the expiry path through
        # verify_refresh_token (decode returns EXPIRED_TOKEN before type check).
        expired_token = create_access_token(
            "u", config, expires_delta=timedelta(seconds=-1)
        )
        result, new_token = refresh_access_token(expired_token, config)
        assert result == JWTAuthResult.EXPIRED_TOKEN
        assert new_token is None

    def test_refresh_with_revoked_refresh_token_fails(self):
        """refresh_access_token fails when the refresh token has been revoked."""
        config = make_config()
        refresh_tok = create_refresh_token("user-rv", config)
        _, payload = decode_token(refresh_tok, config)
        assert payload is not None
        revoke_token(payload.jti)

        result, new_token = refresh_access_token(refresh_tok, config)
        assert result == JWTAuthResult.INVALID_TOKEN
        assert new_token is None

    def test_refresh_produces_access_type_token(self):
        """Refreshed token has type='access'."""
        config = make_config()
        refresh_tok = create_refresh_token("u", config)
        result, new_access = refresh_access_token(refresh_tok, config)
        assert result == JWTAuthResult.SUCCESS
        assert new_access is not None
        _, payload = decode_token(new_access, config)
        assert payload is not None
        assert payload.type == "access"

    def test_refresh_new_token_has_new_jti(self):
        """Refreshed access token receives a brand-new JTI."""
        config = make_config()
        refresh_tok = create_refresh_token("u", config, permissions=["x"])
        _, refresh_payload = decode_token(refresh_tok, config)
        assert refresh_payload is not None

        _, new_access = refresh_access_token(refresh_tok, config)
        assert new_access is not None
        _, access_payload = decode_token(new_access, config)
        assert access_payload is not None
        assert access_payload.jti != refresh_payload.jti


class TestGetTokenHashEdgeCases:
    """Edge cases for get_token_hash."""

    def test_hash_length_always_16(self):
        """Hash is always exactly 16 hex characters."""
        for token in ("a", "x" * 1000, "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"):
            h = get_token_hash(token)
            assert len(h) == 16

    def test_hash_is_hex_string(self):
        """Hash contains only valid hex characters."""
        h = get_token_hash("some-token")
        int(h, 16)  # Raises ValueError if not valid hex

    def test_hash_matches_sha256_prefix(self):
        """Hash equals the first 16 chars of the SHA-256 digest."""
        token = "deterministic-test-token"
        expected = hashlib.sha256(token.encode()).hexdigest()[:16]
        assert get_token_hash(token) == expected

    def test_empty_string_token_hashes(self):
        """get_token_hash handles an empty string without error."""
        h = get_token_hash("")
        assert isinstance(h, str)
        assert len(h) == 16


class TestTokenRevocationExtended:
    """Additional revocation scenarios."""

    @pytest.fixture(autouse=True)
    def clear_revoked(self):
        _revoked_tokens.clear()

    def test_revoke_multiple_tokens(self):
        """Multiple distinct JTIs can be revoked independently."""
        jtis = ["jti-a", "jti-b", "jti-c"]
        for jti in jtis:
            revoke_token(jti)
        for jti in jtis:
            assert is_token_revoked(jti) is True

    def test_revoking_same_jti_twice_is_idempotent(self):
        """Revoking the same JTI twice does not raise an error."""
        revoke_token("dup-jti")
        revoke_token("dup-jti")
        assert is_token_revoked("dup-jti") is True

    def test_non_revoked_jti_returns_false(self):
        """is_token_revoked returns False for JTIs that were never revoked."""
        revoke_token("other-jti")
        assert is_token_revoked("unrelated-jti") is False

    def test_revoked_access_token_fails_verify_bearer(self):
        """A revoked access token is rejected by verify_bearer_token_jwt."""
        config = make_config()
        token = create_access_token("u", config)
        _, payload = decode_token(token, config)
        assert payload is not None
        revoke_token(payload.jti)

        result, _ = verify_bearer_token_jwt(f"Bearer {token}", config)
        assert result == JWTAuthResult.INVALID_TOKEN


class TestVerifyBearerTokenJWTEdgeCases:
    """Additional edge cases for verify_bearer_token_jwt."""

    @pytest.fixture(autouse=True)
    def clear_revoked(self):
        _revoked_tokens.clear()

    def test_empty_string_authorization_returns_missing(self):
        """Empty string authorization header is treated as missing."""
        config = make_config()
        # An empty string is falsy, so it hits the MISSING_TOKEN branch
        result, payload = verify_bearer_token_jwt("", config)
        assert result == JWTAuthResult.MISSING_TOKEN
        assert payload is None

    def test_bearer_prefix_only_returns_invalid_token(self):
        """'Bearer ' with no token produces INVALID_TOKEN."""
        config = make_config()
        # After stripping "Bearer ", we get an empty string which is not a JWT
        result, payload = verify_bearer_token_jwt("Bearer ", config)
        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None

    def test_refresh_token_in_bearer_header_fails(self):
        """A refresh token in the Bearer header fails access verification."""
        config = make_config()
        refresh_tok = create_refresh_token("u", config)
        result, payload = verify_bearer_token_jwt(f"Bearer {refresh_tok}", config)
        assert result == JWTAuthResult.INVALID_TOKEN
        assert payload is None

    def test_expired_token_in_bearer_header_returns_expired(self):
        """An expired access token in the Bearer header returns EXPIRED_TOKEN."""
        config = make_config()
        expired = create_access_token("u", config, expires_delta=timedelta(seconds=-1))
        result, payload = verify_bearer_token_jwt(f"Bearer {expired}", config)
        assert result == JWTAuthResult.EXPIRED_TOKEN
        assert payload is None

    def test_bearer_valid_token_payload_has_correct_sub(self):
        """A valid Bearer token yields the correct subject in the payload."""
        config = make_config()
        token = create_access_token("fleet-monitor", config)
        result, payload = verify_bearer_token_jwt(f"Bearer {token}", config)
        assert result == JWTAuthResult.SUCCESS
        assert payload is not None
        assert payload.sub == "fleet-monitor"

    def test_bearer_token_with_permissions(self):
        """Permissions survive the full Bearer verification path."""
        config = make_config()
        perms = ["fleet:read", "tasks:write"]
        token = create_access_token("svc", config, permissions=perms)
        result, payload = verify_bearer_token_jwt(f"Bearer {token}", config)
        assert result == JWTAuthResult.SUCCESS
        assert payload is not None
        assert payload.permissions == perms
