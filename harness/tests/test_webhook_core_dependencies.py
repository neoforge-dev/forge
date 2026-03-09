"""Tests for forge_harness.webhook_server.core.dependencies

Covers:
- get_auth_config: initialization, caching, ForgeUser dataclass
- verify_auth / verify_unified_auth: all auth paths and error branches
- verify_jwt_auth: JWT success, missing token, expired, invalid
- _log_auth_failure: logging side-effect
- check_rate_limit: no-op pass-through
- require_permission: admin bypass, granted, denied
- require_jwt_permission: granted, denied
"""

import logging
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Helpers to build mock Request objects
# ---------------------------------------------------------------------------

def _make_request(
    *,
    auth_header: str | None = None,
    api_key_header: str | None = None,
    client_host: str = "10.0.0.1",
    path: str = "/test",
    app_auth=None,
) -> MagicMock:
    """Build a minimal mock of a FastAPI Request."""
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = client_host
    request.url = MagicMock()
    request.url.path = path

    headers: dict[str, str] = {}
    if auth_header is not None:
        headers["authorization"] = auth_header
    if api_key_header is not None:
        headers["x-api-key"] = api_key_header

    # headers.get(key) — delegate to real dict
    request.headers = MagicMock()
    request.headers.get = lambda k, d=None: headers.get(k, d)

    # app.state
    request.app = MagicMock()
    request.app.state = MagicMock()
    request.app.state.auth_config = app_auth
    # Make getattr(request.app.state, "auth_config", None) work
    request.app.state.__dict__["auth_config"] = app_auth

    return request


# ---------------------------------------------------------------------------
# Module-level reset of the global _auth_config between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_auth_config():
    """Reset the global _auth_config singleton between tests."""
    import forge_harness.webhook_server.core.dependencies as dep
    dep._auth_config = None
    yield
    dep._auth_config = None


# ===========================================================================
# get_auth_config
# ===========================================================================

class TestGetAuthConfig:
    def test_returns_dict(self):
        from forge_harness.webhook_server.core.dependencies import get_auth_config
        cfg = get_auth_config()
        assert isinstance(cfg, dict)

    def test_default_keys_present(self):
        from forge_harness.webhook_server.core.dependencies import get_auth_config
        cfg = get_auth_config()
        assert "jwt_secret" in cfg
        assert "jwt_algorithm" in cfg
        assert "token_expire_minutes" in cfg
        assert "allow_localhost" in cfg
        assert "required_permissions" in cfg
        assert "forge_user_class" in cfg

    def test_jwt_algorithm_default(self):
        from forge_harness.webhook_server.core.dependencies import get_auth_config
        cfg = get_auth_config()
        assert cfg["jwt_algorithm"] == "HS256"

    def test_token_expire_minutes_default(self):
        from forge_harness.webhook_server.core.dependencies import get_auth_config
        cfg = get_auth_config()
        assert cfg["token_expire_minutes"] == 60

    def test_allow_localhost_default_false(self):
        from forge_harness.webhook_server.core.dependencies import get_auth_config
        cfg = get_auth_config()
        assert cfg["allow_localhost"] is False

    def test_required_permissions_default_empty(self):
        from forge_harness.webhook_server.core.dependencies import get_auth_config
        cfg = get_auth_config()
        assert cfg["required_permissions"] == []

    def test_jwt_secret_from_env(self, monkeypatch):
        monkeypatch.setenv("FORGE_JWT_SECRET", "mysecret")
        from forge_harness.webhook_server.core.dependencies import get_auth_config
        cfg = get_auth_config()
        assert cfg["jwt_secret"] == "mysecret"

    def test_jwt_secret_none_when_not_set(self, monkeypatch):
        monkeypatch.delenv("FORGE_JWT_SECRET", raising=False)
        from forge_harness.webhook_server.core.dependencies import get_auth_config
        cfg = get_auth_config()
        assert cfg["jwt_secret"] is None

    def test_cached_on_second_call(self):
        from forge_harness.webhook_server.core.dependencies import get_auth_config
        cfg1 = get_auth_config()
        cfg2 = get_auth_config()
        assert cfg1 is cfg2  # same object — cached

    def test_forge_user_class_is_dataclass(self):
        import dataclasses

        from forge_harness.webhook_server.core.dependencies import get_auth_config
        cfg = get_auth_config()
        ForgeUser = cfg["forge_user_class"]
        assert dataclasses.is_dataclass(ForgeUser)

    def test_forge_user_instantiation(self):
        from forge_harness.webhook_server.core.dependencies import get_auth_config
        cfg = get_auth_config()
        ForgeUser = cfg["forge_user_class"]
        user = ForgeUser(
            user_id="u1",
            permissions=["read"],
            authenticated=True,
        )
        assert user.user_id == "u1"
        assert user.permissions == ["read"]
        assert user.authenticated is True
        assert user.token_hash is None
        assert user.client_ip is None
        assert user.timestamp == ""

    def test_forge_user_all_fields(self):
        from forge_harness.webhook_server.core.dependencies import get_auth_config
        cfg = get_auth_config()
        ForgeUser = cfg["forge_user_class"]
        user = ForgeUser(
            user_id="u2",
            permissions=["admin"],
            authenticated=True,
            token_hash="abc123",
            client_ip="127.0.0.1",
            timestamp="2026-02-22T00:00:00",
        )
        assert user.token_hash == "abc123"
        assert user.client_ip == "127.0.0.1"
        assert user.timestamp == "2026-02-22T00:00:00"


# ===========================================================================
# verify_auth (alias for verify_unified_auth)
# ===========================================================================

class TestVerifyAuth:
    def test_delegates_to_verify_unified_auth(self):
        from forge_harness.webhook_server.core.dependencies import verify_auth
        request = _make_request()
        sentinel = {"sub": "test", "permissions": [], "authenticated": False}
        with patch(
            "forge_harness.webhook_server.core.dependencies.verify_unified_auth",
            return_value=sentinel,
        ) as mock_unified:
            result = verify_auth(request)
        mock_unified.assert_called_once_with(request)
        assert result is sentinel


# ===========================================================================
# check_rate_limit
# ===========================================================================

class TestCheckRateLimit:
    @pytest.mark.asyncio
    async def test_returns_none(self):
        from forge_harness.webhook_server.core.dependencies import check_rate_limit
        request = _make_request()
        result = await check_rate_limit(request)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_exception_raised(self):
        from forge_harness.webhook_server.core.dependencies import check_rate_limit
        request = _make_request()
        # Should not raise under any circumstances (stub implementation)
        await check_rate_limit(request)


# ===========================================================================
# _log_auth_failure
# ===========================================================================

class TestLogAuthFailure:
    def test_logs_warning(self):
        from forge_harness.webhook_server.core.dependencies import _log_auth_failure
        with patch("forge_harness.webhook_server.core.dependencies.logging") as mock_log:
            mock_logger = MagicMock()
            mock_log.getLogger.return_value = mock_logger
            _log_auth_failure("missing_token", "1.2.3.4", "/api/test", "No header")
        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "missing_token" in warning_msg
        assert "1.2.3.4" in warning_msg

    def test_uses_audit_logger_name(self):
        from forge_harness.webhook_server.core.dependencies import _log_auth_failure
        with patch("forge_harness.webhook_server.core.dependencies.logging") as mock_log:
            mock_logger = MagicMock()
            mock_log.getLogger.return_value = mock_logger
            _log_auth_failure("invalid_token", "10.0.0.1", "/api/agents", "details here")
        mock_log.getLogger.assert_called_with("forge_harness.auth.audit")
        mock_logger.warning.assert_called_once()

    def test_message_contains_all_fields(self):
        from forge_harness.webhook_server.core.dependencies import _log_auth_failure
        with patch("forge_harness.webhook_server.core.dependencies.logging") as mock_log:
            mock_logger = MagicMock()
            mock_log.getLogger.return_value = mock_logger
            _log_auth_failure("bad_creds", "5.5.5.5", "/api/tasks", "extra info")
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "bad_creds" in warning_msg
        assert "5.5.5.5" in warning_msg
        assert "/api/tasks" in warning_msg
        assert "extra info" in warning_msg


# ===========================================================================
# verify_jwt_auth
# ===========================================================================

class TestVerifyJwtAuth:

    def _jwt_config(self, require_auth: bool = True):
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthConfig
        return JWTAuthConfig(secret_key="test-secret", require_auth=require_auth)

    def _jwt_payload(self, sub="svc", permissions=None, jti="jti-1", token_type="access"):
        from datetime import UTC, datetime

        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTPayload
        return JWTPayload(
            sub=sub,
            exp=datetime(2099, 1, 1, tzinfo=UTC),
            iat=datetime(2026, 1, 1, tzinfo=UTC),
            jti=jti,
            type=token_type,
            permissions=permissions or ["read"],
        )

    def test_no_auth_required_returns_anonymous(self):
        from forge_harness.webhook_server.core.dependencies import verify_jwt_auth
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthConfig
        request = _make_request(auth_header=None)
        no_auth_config = JWTAuthConfig(secret_key="x", require_auth=False)
        with patch(
            "forge_harness.webhook_server.infrastructure.jwt_auth.JWTAuthConfig.from_env",
            return_value=no_auth_config,
        ):
            result = verify_jwt_auth(request)
        assert result["sub"] == "anonymous"
        assert result["authenticated"] is False

    def test_success_returns_user_info(self):
        from forge_harness.webhook_server.core.dependencies import verify_jwt_auth
        from forge_harness.webhook_server.infrastructure.jwt_auth import (
            JWTAuthConfig,
            JWTAuthResult,
        )
        request = _make_request(auth_header="Bearer valid.token.here")
        cfg = self._jwt_config(require_auth=True)
        payload = self._jwt_payload(sub="user1", permissions=["read", "write"], jti="tok-99")
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.JWTAuthConfig.from_env",
                return_value=cfg,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.SUCCESS, payload),
            ),
        ):
            result = verify_jwt_auth(request)
        assert result["sub"] == "user1"
        assert result["permissions"] == ["read", "write"]
        assert result["authenticated"] is True
        assert result["jti"] == "tok-99"
        assert result["token_type"] == "access"

    def test_missing_token_raises_401(self):
        from forge_harness.webhook_server.core.dependencies import verify_jwt_auth
        from forge_harness.webhook_server.infrastructure.jwt_auth import (
            JWTAuthConfig,
            JWTAuthResult,
        )
        request = _make_request(auth_header=None)
        cfg = self._jwt_config(require_auth=True)
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.JWTAuthConfig.from_env",
                return_value=cfg,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.MISSING_TOKEN, None),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                verify_jwt_auth(request)
        assert exc_info.value.status_code == 401
        assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}

    def test_expired_token_raises_401(self):
        from forge_harness.webhook_server.core.dependencies import verify_jwt_auth
        from forge_harness.webhook_server.infrastructure.jwt_auth import (
            JWTAuthConfig,
            JWTAuthResult,
        )
        request = _make_request(auth_header="Bearer expired.token")
        cfg = self._jwt_config(require_auth=True)
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.JWTAuthConfig.from_env",
                return_value=cfg,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.EXPIRED_TOKEN, None),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                verify_jwt_auth(request)
        assert exc_info.value.status_code == 401
        detail = exc_info.value.detail
        from forge_harness.error_schema import AUTH_TOKEN_EXPIRED
        assert detail["code"] == AUTH_TOKEN_EXPIRED

    def test_invalid_token_raises_401(self):
        from forge_harness.webhook_server.core.dependencies import verify_jwt_auth
        from forge_harness.webhook_server.infrastructure.jwt_auth import (
            JWTAuthConfig,
            JWTAuthResult,
        )
        request = _make_request(auth_header="Bearer bad.token")
        cfg = self._jwt_config(require_auth=True)
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.JWTAuthConfig.from_env",
                return_value=cfg,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.INVALID_TOKEN, None),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                verify_jwt_auth(request)
        assert exc_info.value.status_code == 401
        from forge_harness.error_schema import AUTH_INVALID_TOKEN
        assert exc_info.value.detail["code"] == AUTH_INVALID_TOKEN

    def test_success_with_none_payload_raises_401(self):
        from forge_harness.webhook_server.core.dependencies import verify_jwt_auth
        from forge_harness.webhook_server.infrastructure.jwt_auth import (
            JWTAuthConfig,
            JWTAuthResult,
        )
        request = _make_request(auth_header="Bearer ok.but.no.payload")
        cfg = self._jwt_config(require_auth=True)
        # SUCCESS result but payload is None — treated as invalid
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.JWTAuthConfig.from_env",
                return_value=cfg,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.SUCCESS, None),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                verify_jwt_auth(request)
        assert exc_info.value.status_code == 401


# ===========================================================================
# verify_unified_auth
# ===========================================================================

class TestVerifyUnifiedAuth:

    # --- Helper fixtures ---

    def _api_key_payload(self, owner="agent1", permissions=None):
        from datetime import UTC, datetime

        from forge_harness.webhook_server.infrastructure.api_key_auth import APIKeyPayload
        return APIKeyPayload(
            key_id="kid-1",
            owner=owner,
            permissions=permissions or ["read", "write"],
            created_at=datetime.now(UTC),
            is_active=True,
        )

    def _jwt_payload(self, sub="svc", permissions=None, jti="jti-1"):
        from datetime import UTC, datetime

        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTPayload
        return JWTPayload(
            sub=sub,
            exp=datetime(2099, 1, 1, tzinfo=UTC),
            iat=datetime(2026, 1, 1, tzinfo=UTC),
            jti=jti,
            type="access",
            permissions=permissions or ["read"],
        )

    # --- app-level require_auth=False ---

    def test_no_auth_configured_returns_anonymous(self):
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        app_auth = MagicMock()
        app_auth.require_auth = False
        request = _make_request(app_auth=app_auth)
        result = verify_unified_auth(request)
        assert result["sub"] == "anonymous"
        assert result["auth_method"] == "no_auth"
        assert result["authenticated"] is False
        assert "admin" in result["permissions"]

    # --- API Key path ---

    def test_api_key_success(self):
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        from forge_harness.webhook_server.infrastructure.api_key_auth import APIKeyResult
        request = _make_request(api_key_header="forge_key_abc")
        payload = self._api_key_payload(owner="bot1", permissions=["read"])
        with patch(
            "forge_harness.webhook_server.infrastructure.api_key_auth.verify_api_key",
            return_value=(APIKeyResult.SUCCESS, payload),
        ):
            result = verify_unified_auth(request)
        assert result["sub"] == "bot1"
        assert result["auth_method"] == "api_key"
        assert result["authenticated"] is True

    def test_invalid_api_key_logs_failure(self):
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        from forge_harness.webhook_server.infrastructure.api_key_auth import APIKeyResult
        # Invalid key — non-MISSING_KEY result → logs failure, then falls through to other methods
        request = _make_request(api_key_header="bad_key")
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.api_key_auth.verify_api_key",
                return_value=(APIKeyResult.INVALID_KEY, None),
            ),
            patch(
                "forge_harness.webhook_server.core.dependencies._log_auth_failure"
            ) as mock_log,
            pytest.raises(HTTPException),
        ):
            verify_unified_auth(request)
        assert mock_log.call_count >= 1
        # First call should be the invalid_api_key log
        first_call_args = mock_log.call_args_list[0][0]
        assert "invalid_api_key" in first_call_args[0]

    def test_missing_api_key_does_not_log(self):
        """MISSING_KEY result means header simply wasn't provided — no log."""
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        from forge_harness.webhook_server.infrastructure.api_key_auth import APIKeyResult
        # api_key_header present but empty-ish — verify_api_key returns MISSING_KEY
        request = _make_request(api_key_header="")
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.api_key_auth.verify_api_key",
                return_value=(APIKeyResult.MISSING_KEY, None),
            ),
            patch(
                "forge_harness.webhook_server.core.dependencies._log_auth_failure"
            ) as mock_log,
            pytest.raises(HTTPException),
        ):
            verify_unified_auth(request)
        mock_log.assert_called_once()  # will log "missing_credentials" at the final fallback
        # Confirm the "invalid_api_key" path was NOT triggered
        assert "invalid_api_key" not in mock_log.call_args[0][0]

    # --- JWT path ---

    def test_jwt_success(self):
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        from forge_harness.webhook_server.infrastructure.jwt_auth import (
            JWTAuthConfig,
            JWTAuthResult,
        )
        request = _make_request(auth_header="Bearer jwt.token.here")
        payload = self._jwt_payload(sub="svc1", permissions=["admin"], jti="j1")
        cfg = JWTAuthConfig(secret_key="s", require_auth=True)
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.JWTAuthConfig.from_env",
                return_value=cfg,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.SUCCESS, payload),
            ),
        ):
            result = verify_unified_auth(request)
        assert result["sub"] == "svc1"
        assert result["auth_method"] == "jwt"
        assert result["authenticated"] is True
        assert result["jti"] == "j1"

    # --- Legacy bearer token fallback ---

    def test_legacy_token_fallback(self):
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        from forge_harness.webhook_server.infrastructure.auth import AuthConfig, AuthResult
        from forge_harness.webhook_server.infrastructure.jwt_auth import (
            JWTAuthConfig,
            JWTAuthResult,
        )
        request = _make_request(auth_header="Bearer legacy-static-token")
        cfg = JWTAuthConfig(secret_key="s", require_auth=True)
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.JWTAuthConfig.from_env",
                return_value=cfg,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.INVALID_TOKEN, None),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.auth.verify_bearer_token",
                return_value=AuthResult.SUCCESS,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.auth.AuthConfig.from_env",
                return_value=MagicMock(),
            ),
        ):
            result = verify_unified_auth(request)
        assert result["sub"] == "legacy_client"
        assert result["auth_method"] == "legacy_token"
        assert result["authenticated"] is True

    def test_legacy_token_fallback_uses_app_auth_when_available(self):
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        from forge_harness.webhook_server.infrastructure.auth import AuthResult
        from forge_harness.webhook_server.infrastructure.jwt_auth import (
            JWTAuthConfig,
            JWTAuthResult,
        )
        app_auth = MagicMock()
        app_auth.require_auth = True
        request = _make_request(auth_header="Bearer legacy-token", app_auth=app_auth)
        cfg = JWTAuthConfig(secret_key="s", require_auth=True)
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.JWTAuthConfig.from_env",
                return_value=cfg,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.INVALID_TOKEN, None),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.auth.verify_bearer_token",
                return_value=AuthResult.SUCCESS,
            ) as mock_vbt,
        ):
            result = verify_unified_auth(request)
        # app_auth should be used as legacy_config (passed in place of AuthConfig.from_env())
        mock_vbt.assert_called_once()
        args = mock_vbt.call_args[0]
        assert args[1] is app_auth

    def test_expired_jwt_raises_401_with_expired_code(self):
        from forge_harness.error_schema import AUTH_TOKEN_EXPIRED
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        from forge_harness.webhook_server.infrastructure.auth import AuthResult
        from forge_harness.webhook_server.infrastructure.jwt_auth import (
            JWTAuthConfig,
            JWTAuthResult,
        )
        request = _make_request(auth_header="Bearer expired.jwt")
        cfg = JWTAuthConfig(secret_key="s", require_auth=True)
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.JWTAuthConfig.from_env",
                return_value=cfg,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.EXPIRED_TOKEN, None),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.auth.verify_bearer_token",
                return_value=AuthResult.INVALID_TOKEN,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.auth.AuthConfig.from_env",
                return_value=MagicMock(),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                verify_unified_auth(request)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail["code"] == AUTH_TOKEN_EXPIRED

    def test_invalid_jwt_and_invalid_legacy_raises_401(self):
        from forge_harness.error_schema import AUTH_INVALID_TOKEN
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        from forge_harness.webhook_server.infrastructure.auth import AuthResult
        from forge_harness.webhook_server.infrastructure.jwt_auth import (
            JWTAuthConfig,
            JWTAuthResult,
        )
        request = _make_request(auth_header="Bearer bad.jwt")
        cfg = JWTAuthConfig(secret_key="s", require_auth=True)
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.JWTAuthConfig.from_env",
                return_value=cfg,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.INVALID_TOKEN, None),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.auth.verify_bearer_token",
                return_value=AuthResult.INVALID_TOKEN,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.auth.AuthConfig.from_env",
                return_value=MagicMock(),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                verify_unified_auth(request)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail["code"] == AUTH_INVALID_TOKEN

    # --- Localhost bypass ---

    def test_localhost_bypass_when_allowed(self):
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        app_auth = MagicMock()
        app_auth.require_auth = True
        app_auth.allow_localhost = True
        request = _make_request(client_host="127.0.0.1", app_auth=app_auth)
        with patch(
            "forge_harness.webhook_server.infrastructure.auth._is_localhost_value",
            return_value=True,
        ):
            result = verify_unified_auth(request)
        assert result["sub"] == "localhost"
        assert result["auth_method"] == "localhost"
        assert result["authenticated"] is True
        assert "admin" in result["permissions"]

    def test_localhost_bypass_not_triggered_when_disallowed(self):
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        app_auth = MagicMock()
        app_auth.require_auth = True
        app_auth.allow_localhost = False
        request = _make_request(client_host="127.0.0.1", app_auth=app_auth)
        # No auth headers → should fall through to missing_credentials 401
        with pytest.raises(HTTPException) as exc_info:
            verify_unified_auth(request)
        assert exc_info.value.status_code == 401

    # --- All methods failed (missing credentials) ---

    def test_no_credentials_raises_401_missing(self):
        from forge_harness.error_schema import AUTH_MISSING_CREDENTIALS
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        request = _make_request()  # no headers, no app_auth
        with pytest.raises(HTTPException) as exc_info:
            verify_unified_auth(request)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail["code"] == AUTH_MISSING_CREDENTIALS
        assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}

    def test_missing_credentials_logs_failure(self):
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        request = _make_request()
        with (
            patch(
                "forge_harness.webhook_server.core.dependencies._log_auth_failure"
            ) as mock_log,
            pytest.raises(HTTPException),
        ):
            verify_unified_auth(request)
        mock_log.assert_called_once()
        assert "missing_credentials" in mock_log.call_args[0][0]

    def test_no_client_host_uses_unknown(self):
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        request = _make_request()
        request.client = None  # No client info
        with pytest.raises(HTTPException):
            verify_unified_auth(request)
        # Should not crash — falls back to "unknown"

    def test_app_auth_none_proceeds_to_auth_check(self):
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        # app_auth is None → no bypass, should still check api key / jwt / legacy
        request = _make_request(app_auth=None)
        with pytest.raises(HTTPException) as exc_info:
            verify_unified_auth(request)
        assert exc_info.value.status_code == 401


# ===========================================================================
# require_permission
# ===========================================================================

class TestRequirePermission:
    """
    require_permission(perm) returns an async function `permission_checker(user)`.
    The default argument `user = Depends(verify_unified_auth)` only matters in
    FastAPI's DI system — we can call the returned coroutine directly with `user`.
    """

    @pytest.mark.asyncio
    async def test_admin_bypasses_specific_permission(self):
        from forge_harness.webhook_server.core.dependencies import require_permission
        checker = require_permission("write")
        user = {"sub": "admin_user", "permissions": ["admin"], "authenticated": True}
        result = await checker(user)
        assert result == user

    @pytest.mark.asyncio
    async def test_returns_user_when_permission_present(self):
        from forge_harness.webhook_server.core.dependencies import require_permission
        checker = require_permission("read")
        user = {"sub": "reader", "permissions": ["read", "write"], "authenticated": True}
        result = await checker(user)
        assert result == user

    @pytest.mark.asyncio
    async def test_raises_403_when_permission_absent(self):
        from forge_harness.error_schema import AUTH_PERMISSION_DENIED
        from forge_harness.webhook_server.core.dependencies import require_permission
        checker = require_permission("deploy")
        user = {"sub": "reader", "permissions": ["read"], "authenticated": True}
        with pytest.raises(HTTPException) as exc_info:
            await checker(user)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["code"] == AUTH_PERMISSION_DENIED

    @pytest.mark.asyncio
    async def test_403_message_names_the_required_permission(self):
        from forge_harness.webhook_server.core.dependencies import require_permission
        checker = require_permission("special_perm")
        user = {"sub": "u", "permissions": [], "authenticated": True}
        with pytest.raises(HTTPException) as exc_info:
            await checker(user)
        assert "special_perm" in exc_info.value.detail["message"]

    @pytest.mark.asyncio
    async def test_admin_bypasses_any_arbitrary_permission(self):
        from forge_harness.webhook_server.core.dependencies import require_permission
        checker = require_permission("very_special_permission")
        user = {"sub": "admin", "permissions": ["admin"], "authenticated": True}
        result = await checker(user)
        assert result is user

    @pytest.mark.asyncio
    async def test_empty_permissions_raises_403(self):
        from forge_harness.webhook_server.core.dependencies import require_permission
        checker = require_permission("write")
        user = {"sub": "nobody", "permissions": [], "authenticated": True}
        with pytest.raises(HTTPException) as exc_info:
            await checker(user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_permissions_key_raises_403(self):
        from forge_harness.webhook_server.core.dependencies import require_permission
        checker = require_permission("read")
        user = {"sub": "partial"}  # no 'permissions' key
        with pytest.raises(HTTPException) as exc_info:
            await checker(user)
        assert exc_info.value.status_code == 403

    def test_returns_callable(self):
        from forge_harness.webhook_server.core.dependencies import require_permission
        checker = require_permission("read")
        assert callable(checker)

    def test_different_permissions_produce_different_checkers(self):
        from forge_harness.webhook_server.core.dependencies import require_permission
        checker_read = require_permission("read")
        checker_write = require_permission("write")
        assert checker_read is not checker_write


# ===========================================================================
# require_jwt_permission
# ===========================================================================

class TestRequireJwtPermission:
    """
    require_jwt_permission(perm) returns an async permission_checker(user).
    Unlike require_permission, it does NOT give admin bypass.
    """

    @pytest.mark.asyncio
    async def test_granted_returns_user(self):
        from forge_harness.webhook_server.core.dependencies import require_jwt_permission
        checker = require_jwt_permission("write")
        user = {"sub": "svc", "permissions": ["read", "write"], "authenticated": True}
        result = await checker(user)
        assert result == user

    @pytest.mark.asyncio
    async def test_denied_raises_403(self):
        from forge_harness.error_schema import AUTH_PERMISSION_DENIED
        from forge_harness.webhook_server.core.dependencies import require_jwt_permission
        checker = require_jwt_permission("deploy")
        user = {"sub": "svc", "permissions": ["read"], "authenticated": True}
        with pytest.raises(HTTPException) as exc_info:
            await checker(user)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["code"] == AUTH_PERMISSION_DENIED

    @pytest.mark.asyncio
    async def test_denied_message_contains_permission_name(self):
        from forge_harness.webhook_server.core.dependencies import require_jwt_permission
        checker = require_jwt_permission("super_admin")
        user = {"sub": "svc", "permissions": [], "authenticated": True}
        with pytest.raises(HTTPException) as exc_info:
            await checker(user)
        assert "super_admin" in exc_info.value.detail["message"]

    @pytest.mark.asyncio
    async def test_empty_permissions_raises_403(self):
        from forge_harness.webhook_server.core.dependencies import require_jwt_permission
        checker = require_jwt_permission("read")
        user = {"sub": "nobody", "permissions": [], "authenticated": True}
        with pytest.raises(HTTPException) as exc_info:
            await checker(user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_is_not_bypassed(self):
        """require_jwt_permission does NOT give admin bypass — unlike require_permission."""
        from forge_harness.webhook_server.core.dependencies import require_jwt_permission
        checker = require_jwt_permission("deploy")
        user = {"sub": "admin_svc", "permissions": ["admin"], "authenticated": True}
        with pytest.raises(HTTPException) as exc_info:
            await checker(user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_exact_permission_match_succeeds(self):
        from forge_harness.webhook_server.core.dependencies import require_jwt_permission
        checker = require_jwt_permission("read")
        user = {"sub": "u", "permissions": ["read"], "authenticated": True}
        result = await checker(user)
        assert result is user

    def test_returns_callable(self):
        from forge_harness.webhook_server.core.dependencies import require_jwt_permission
        checker = require_jwt_permission("admin")
        assert callable(checker)


# ===========================================================================
# Integration: verify_auth calls verify_unified_auth with real modules
# ===========================================================================

class TestVerifyAuthIntegration:
    def test_verify_auth_same_as_unified_auth_result_for_missing_creds(self):
        from forge_harness.webhook_server.core.dependencies import verify_auth, verify_unified_auth
        request = _make_request()
        exc_auth = None
        exc_unified = None
        try:
            verify_auth(request)
        except HTTPException as e:
            exc_auth = e
        try:
            verify_unified_auth(request)
        except HTTPException as e:
            exc_unified = e
        assert exc_auth is not None
        assert exc_unified is not None
        assert exc_auth.status_code == exc_unified.status_code
        assert exc_auth.detail["code"] == exc_unified.detail["code"]


# ===========================================================================
# Edge cases
# ===========================================================================

class TestEdgeCases:
    def test_verify_unified_auth_bearer_lowercase_matched(self):
        """Lowercase 'bearer ' prefix triggers JWT/legacy path since check is case-insensitive."""
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        from forge_harness.webhook_server.infrastructure.auth import AuthResult
        from forge_harness.webhook_server.infrastructure.jwt_auth import (
            JWTAuthConfig,
            JWTAuthResult,
        )
        request = _make_request(auth_header="bearer lowercase.token")
        cfg = JWTAuthConfig(secret_key="s", require_auth=True)
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.JWTAuthConfig.from_env",
                return_value=cfg,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.INVALID_TOKEN, None),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.auth.verify_bearer_token",
                return_value=AuthResult.INVALID_TOKEN,
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.auth.AuthConfig.from_env",
                return_value=MagicMock(),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                verify_unified_auth(request)
        assert exc_info.value.status_code == 401

    def test_api_key_success_short_circuits_jwt(self):
        """When API key succeeds, JWT verification is never called."""
        from datetime import UTC, datetime

        from forge_harness.webhook_server.core.dependencies import verify_unified_auth
        from forge_harness.webhook_server.infrastructure.api_key_auth import (
            APIKeyPayload,
            APIKeyResult,
        )
        payload = APIKeyPayload(
            key_id="k1",
            owner="bot",
            permissions=["read"],
            created_at=datetime.now(UTC),
            is_active=True,
        )
        request = _make_request(api_key_header="forge_key_xyz", auth_header="Bearer extra.token")
        with (
            patch(
                "forge_harness.webhook_server.infrastructure.api_key_auth.verify_api_key",
                return_value=(APIKeyResult.SUCCESS, payload),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_bearer_token_jwt"
            ) as mock_jwt,
        ):
            result = verify_unified_auth(request)
        mock_jwt.assert_not_called()
        assert result["auth_method"] == "api_key"

    def test_get_auth_config_reset_reinitializes(self):
        """After resetting _auth_config to None, get_auth_config re-initializes."""
        import forge_harness.webhook_server.core.dependencies as dep
        from forge_harness.webhook_server.core.dependencies import get_auth_config
        cfg1 = get_auth_config()
        dep._auth_config = None  # reset
        cfg2 = get_auth_config()
        # Both are valid configs but different objects (re-initialized)
        assert cfg1 is not cfg2
        assert cfg2["jwt_algorithm"] == "HS256"
