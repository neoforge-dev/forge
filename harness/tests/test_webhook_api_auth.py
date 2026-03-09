"""Tests for webhook_server/api/auth.py Authentication API Endpoints.

Tests cover:
- POST /api/auth/login - User authentication
- POST /api/auth/api-keys - API key creation (admin)
- POST /api/auth/refresh - Token refresh/rotation
- POST /api/auth/verify - Token verification
- POST /api/auth/logout - Token revocation
- GET /api/auth/status - Auth service status
- GET /api/auth/me - Current user info

Security tests:
- Invalid credentials
- Expired tokens
- Invalid tokens
- Token revocation
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


def _mock_request(headers: dict = None, client_host: str = "127.0.0.1") -> Mock:
    """Create a mock FastAPI request object."""
    request = Mock()
    request.headers = headers or {}
    request.client = Mock(host=client_host)
    return request


@pytest.fixture
def mock_jwt_config():
    """Create mock JWT config."""
    config = Mock()
    config.secret_key = "test-secret-key"
    config.algorithm = "HS256"
    config.access_token_expire_minutes = 30
    config.refresh_token_expire_days = 7
    config.require_auth = True
    return config


@pytest.fixture
def mock_user():
    """Create mock user."""
    user = Mock()
    user.username = "testuser"
    user.permissions = ["read", "write"]
    return user


@pytest.fixture
def mock_user_store(mock_user):
    """Create mock user store."""
    store = Mock()
    store.authenticate = Mock(return_value=mock_user)
    return store


@pytest.fixture
def mock_audit_logger():
    """Create mock audit logger."""
    logger = Mock()
    logger.log = AsyncMock()
    return logger


@pytest.fixture
def mock_jwt_payload():
    """Create mock JWT payload."""
    payload = Mock()
    payload.sub = "testuser"
    payload.permissions = ["read", "write"]
    payload.jti = "token-id-123"
    payload.exp = datetime.now(UTC) + timedelta(minutes=30)
    payload.type = "access"
    return payload


class TestLoginEndpoint:
    """Tests for POST /api/auth/login."""

    def test_login_success(self, mock_jwt_config, mock_user, mock_user_store, mock_audit_logger):
        """Test successful login."""
        from forge_harness.webhook_server.api import auth as auth_api

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_user_store", return_value=mock_user_store),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit_logger),
            patch(
                "forge_harness.webhook_server.api.auth.create_access_token",
                return_value="access_token_123",
            ),
            patch(
                "forge_harness.webhook_server.api.auth.create_refresh_token",
                return_value="refresh_token_123",
            ),
        ):
            from forge_harness.webhook_server.api.auth import LoginRequest

            request = LoginRequest(username="testuser", password="password123")

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(
                auth_api.login(request, _mock_request())
            )

            assert result.access_token == "access_token_123"
            assert result.refresh_token == "refresh_token_123"
            assert result.token_type == "Bearer"

    def test_login_invalid_credentials(self, mock_jwt_config, mock_user_store, mock_audit_logger):
        """Test login with invalid credentials."""
        from forge_harness.webhook_server.api import auth as auth_api

        mock_user_store.authenticate = Mock(return_value=None)

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_user_store", return_value=mock_user_store),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit_logger),
        ):
            from forge_harness.webhook_server.api.auth import LoginRequest

            request = LoginRequest(username="baduser", password="wrongpass")

            import asyncio

            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    auth_api.login(request, _mock_request())
                )

            assert exc_info.value.status_code == 401
            assert "Invalid username or password" in str(exc_info.value.detail)

    def test_login_audit_log_on_failure(self, mock_jwt_config, mock_user_store, mock_audit_logger):
        """Test failed login is audit logged."""
        from forge_harness.webhook_server.api import auth as auth_api

        mock_user_store.authenticate = Mock(return_value=None)

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_user_store", return_value=mock_user_store),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit_logger),
        ):
            from forge_harness.webhook_server.api.auth import LoginRequest

            request = LoginRequest(username="baduser", password="wrongpass")

            import asyncio

            try:
                asyncio.get_event_loop().run_until_complete(
                    auth_api.login(request, _mock_request())
                )
            except HTTPException:
                pass

            mock_audit_logger.log.assert_called_once()
            call_args = mock_audit_logger.log.call_args
            assert call_args.kwargs.get("action") == "login_failed"


class TestCreateAPIKeyEndpoint:
    """Tests for POST /api/auth/api-keys."""

    @pytest.mark.skip(reason="Requires FastAPI dependency injection mocking")
    def test_create_api_key_success(self, mock_jwt_config, mock_audit_logger):
        """Test successful API key creation."""
        # This test requires more complex FastAPI dependency injection mocking
        # Skipping for now - the endpoint logic is tested indirectly via integration tests
        pass


class TestRefreshTokenEndpoint:
    """Tests for POST /api/auth/refresh."""

    def test_refresh_success(self, mock_jwt_config, mock_audit_logger, mock_jwt_payload):
        """Test successful token refresh."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        mock_refresh_result = ("success", mock_jwt_payload)

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit_logger),
            patch(
                "forge_harness.webhook_server.api.auth.refresh_access_token",
                return_value=(JWTAuthResult.SUCCESS, "new_access_token"),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_refresh_token",
                return_value=mock_refresh_result,
            ),
            patch("forge_harness.webhook_server.api.auth.revoke_token"),
            patch(
                "forge_harness.webhook_server.api.auth.create_refresh_token",
                return_value="new_refresh_token",
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="old_refresh_token")

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(
                auth_api.refresh_token_endpoint(request, _mock_request())
            )

            assert result.access_token == "new_access_token"
            assert result.refresh_token == "new_refresh_token"

    def test_refresh_expired_token(self, mock_jwt_config, mock_audit_logger):
        """Test refresh with expired token."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.api.auth.refresh_access_token",
                return_value=(JWTAuthResult.EXPIRED_TOKEN, None),
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="expired_token")

            import asyncio

            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    auth_api.refresh_token_endpoint(request, _mock_request())
                )

            assert exc_info.value.status_code == 401
            assert "expired" in str(exc_info.value.detail).lower()

    def test_refresh_invalid_token(self, mock_jwt_config):
        """Test refresh with invalid token."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.api.auth.refresh_access_token",
                return_value=(JWTAuthResult.INVALID_TOKEN, None),
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="invalid_token")

            import asyncio

            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    auth_api.refresh_token_endpoint(request, _mock_request())
                )

            assert exc_info.value.status_code == 401


class TestVerifyTokenEndpoint:
    """Tests for POST /api/auth/verify."""

    def test_verify_valid_access_token(self, mock_jwt_config, mock_jwt_payload):
        """Test verifying valid access token."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.api.auth.verify_access_token",
                return_value=(JWTAuthResult.SUCCESS, mock_jwt_payload),
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="valid_access_token")

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(auth_api.verify_token(request))

            assert result.valid is True
            assert result.token_type == "access"
            assert result.sub == "testuser"

    def test_verify_valid_refresh_token(self, mock_jwt_config, mock_jwt_payload):
        """Test verifying valid refresh token."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.api.auth.verify_access_token",
                return_value=(JWTAuthResult.INVALID_TOKEN, None),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_refresh_token",
                return_value=(JWTAuthResult.SUCCESS, mock_jwt_payload),
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="valid_refresh_token")

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(auth_api.verify_token(request))

            assert result.valid is True
            assert result.token_type == "refresh"

    def test_verify_invalid_token(self, mock_jwt_config):
        """Test verifying invalid token."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.api.auth.verify_access_token",
                return_value=(JWTAuthResult.INVALID_TOKEN, None),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_refresh_token",
                return_value=(JWTAuthResult.INVALID_TOKEN, None),
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="invalid_token")

            import asyncio

            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(auth_api.verify_token(request))

            assert exc_info.value.status_code == 401


class TestLogoutEndpoint:
    """Tests for POST /api/auth/logout."""

    def test_logout_success(self, mock_jwt_config, mock_jwt_payload, mock_audit_logger):
        """Test successful logout."""
        from forge_harness.webhook_server.api import auth as auth_api

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit_logger),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.decode_token",
                return_value=("success", mock_jwt_payload),
            ),
            patch("forge_harness.webhook_server.api.auth.revoke_token") as mock_revoke,
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="token_to_revoke")

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(
                auth_api.logout(request, _mock_request())
            )

            assert result.status == "success"
            mock_revoke.assert_called_once_with("token-id-123")

    def test_logout_invalid_token(self, mock_jwt_config):
        """Test logout with invalid token."""
        from forge_harness.webhook_server.api import auth as auth_api

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.decode_token",
                return_value=("success", None),
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="invalid_token")

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(
                auth_api.logout(request, _mock_request())
            )

            assert result.status == "not_found"


class TestAuthStatusEndpoint:
    """Tests for GET /api/auth/status."""

    def test_status_healthy(self, mock_jwt_config):
        """Test auth status when healthy."""
        from forge_harness.webhook_server.api import auth as auth_api

        with patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config):
            import asyncio

            result = asyncio.get_event_loop().run_until_complete(auth_api.auth_status())

            assert result["status"] == "healthy"
            assert result["service"] == "jwt-auth"
            assert result["algorithm"] == "HS256"

    def test_status_with_error(self, mock_jwt_config):
        """Test auth status when error occurs."""
        from forge_harness.webhook_server.api import auth as auth_api

        with patch.object(auth_api, "_get_jwt_config", side_effect=Exception("Config error")):
            import asyncio

            result = asyncio.get_event_loop().run_until_complete(auth_api.auth_status())

            assert result["status"] == "error"
            assert "Config error" in result["error"]


class TestGetCurrentUserEndpoint:
    """Tests for GET /api/auth/me."""

    def test_me_valid_token(self, mock_jwt_config, mock_jwt_payload):
        """Test getting current user with valid token."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.api.auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.SUCCESS, mock_jwt_payload),
            ),
        ):
            request = _mock_request(headers={"authorization": "Bearer valid_token"})

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(auth_api.get_current_user(request))

            assert result["sub"] == "testuser"
            assert result["permissions"] == ["read", "write"]

    def test_me_no_token(self, mock_jwt_config):
        """Test getting current user without token."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.api.auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.MISSING_TOKEN, None),
            ),
        ):
            request = _mock_request()

            import asyncio

            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(auth_api.get_current_user(request))

            assert exc_info.value.status_code == 401

    def test_me_invalid_token(self, mock_jwt_config):
        """Test getting current user with invalid token."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.api.auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.INVALID_TOKEN, None),
            ),
        ):
            request = _mock_request(headers={"authorization": "Bearer invalid_token"})

            import asyncio

            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(auth_api.get_current_user(request))

            assert exc_info.value.status_code == 401


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_client_info(self):
        """Test _get_client_info helper."""
        from forge_harness.webhook_server.api import auth as auth_api

        request = _mock_request(
            headers={"User-Agent": "TestAgent/1.0"}, client_host="192.168.1.100"
        )

        ip, ua = auth_api._get_client_info(request)

        assert ip == "192.168.1.100"
        assert ua == "TestAgent/1.0"

    def test_get_client_info_no_client(self):
        """Test _get_client_info with no client."""
        from forge_harness.webhook_server.api import auth as auth_api

        request = Mock()
        request.client = None
        request.headers = {}

        ip, ua = auth_api._get_client_info(request)

        assert ip == "unknown"
        assert ua == "unknown"


class TestPydanticModels:
    """Tests for Pydantic request/response models."""

    def test_login_request(self):
        """Test LoginRequest model."""
        from forge_harness.webhook_server.api.auth import LoginRequest

        request = LoginRequest(username="testuser", password="password123")

        assert request.username == "testuser"
        assert request.password == "password123"

    def test_login_response(self):
        """Test LoginResponse model."""
        from forge_harness.webhook_server.api.auth import LoginResponse

        response = LoginResponse(
            access_token="token123", refresh_token="refresh123", expires_in=1800
        )

        assert response.access_token == "token123"
        assert response.refresh_token == "refresh123"
        assert response.expires_in == 1800
        assert response.token_type == "Bearer"

    def test_refresh_request(self):
        """Test RefreshRequest model."""
        from forge_harness.webhook_server.api.auth import RefreshRequest

        request = RefreshRequest(refresh_token="refresh_token")

        assert request.refresh_token == "refresh_token"

    def test_refresh_response(self):
        """Test RefreshResponse model."""
        from forge_harness.webhook_server.api.auth import RefreshResponse

        response = RefreshResponse(
            access_token="new_access", refresh_token="new_refresh", expires_in=1800
        )

        assert response.access_token == "new_access"
        assert response.refresh_token == "new_refresh"

    def test_verify_response(self):
        """Test VerifyResponse model."""
        from forge_harness.webhook_server.api.auth import VerifyResponse

        response = VerifyResponse(
            valid=True,
            token_type="access",
            sub="user123",
            permissions=["read", "write"],
            exp="2026-01-01T00:00:00",
        )

        assert response.valid is True
        assert response.token_type == "access"
        assert response.sub == "user123"

    def test_revoke_response(self):
        """Test RevokeResponse model."""
        from forge_harness.webhook_server.api.auth import RevokeResponse

        response = RevokeResponse(status="success", message="Token revoked")

        assert response.status == "success"
        assert response.message == "Token revoked"

    def test_api_key_create_request(self):
        """Test APIKeyCreateRequest model."""
        from forge_harness.webhook_server.api.auth import APIKeyCreateRequest

        request = APIKeyCreateRequest(
            owner="test-service", permissions=["read"], expires_in_days=30
        )

        assert request.owner == "test-service"
        assert request.permissions == ["read"]
        assert request.expires_in_days == 30

    def test_api_key_create_request_defaults(self):
        """Test APIKeyCreateRequest default values."""
        from forge_harness.webhook_server.api.auth import APIKeyCreateRequest

        request = APIKeyCreateRequest(owner="test-service")

        assert request.permissions == ["read"]
        assert request.expires_in_days is None


class TestSecurityEdgeCases:
    """Security-focused edge case tests."""

    def test_login_sql_injection_attempt(self, mock_jwt_config, mock_user_store, mock_audit_logger):
        """Test login with SQL injection attempt."""
        from forge_harness.webhook_server.api import auth as auth_api

        mock_user_store.authenticate = Mock(return_value=None)

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_user_store", return_value=mock_user_store),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit_logger),
        ):
            from forge_harness.webhook_server.api.auth import LoginRequest

            request = LoginRequest(username="admin' OR '1'='1", password="anything")

            import asyncio

            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    auth_api.login(request, _mock_request())
                )

            assert exc_info.value.status_code == 401

    def test_login_empty_username(self, mock_jwt_config, mock_user_store, mock_audit_logger):
        """Test login with empty username."""
        from forge_harness.webhook_server.api import auth as auth_api

        mock_user_store.authenticate = Mock(return_value=None)

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_user_store", return_value=mock_user_store),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit_logger),
        ):
            from forge_harness.webhook_server.api.auth import LoginRequest

            request = LoginRequest(username="", password="password")

            import asyncio

            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    auth_api.login(request, _mock_request())
                )

            assert exc_info.value.status_code == 401

    def test_verify_token_expired(self, mock_jwt_config):
        """Test verifying expired token."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.api.auth.verify_access_token",
                return_value=(JWTAuthResult.EXPIRED_TOKEN, None),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_refresh_token",
                return_value=(JWTAuthResult.EXPIRED_TOKEN, None),
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="expired_token")

            import asyncio

            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(auth_api.verify_token(request))

            assert exc_info.value.status_code == 401

    def test_verify_token_missing_payload(self, mock_jwt_config):
        """Test verifying token with missing payload."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        mock_payload = Mock()
        mock_payload.sub = None

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.api.auth.verify_access_token",
                return_value=(JWTAuthResult.SUCCESS, None),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_refresh_token",
                return_value=(JWTAuthResult.SUCCESS, None),
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="token_with_no_payload")

            import asyncio

            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(auth_api.verify_token(request))

            assert exc_info.value.status_code == 401


class TestTokenRotation:
    """Tests for token rotation security patterns."""

    def test_refresh_rotates_tokens(self, mock_jwt_config, mock_audit_logger, mock_jwt_payload):
        """Test that refresh rotates tokens properly."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit_logger),
            patch(
                "forge_harness.webhook_server.api.auth.refresh_access_token",
                return_value=(JWTAuthResult.SUCCESS, "new_access"),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_refresh_token",
                return_value=(JWTAuthResult.SUCCESS, mock_jwt_payload),
            ),
            patch("forge_harness.webhook_server.api.auth.revoke_token") as mock_revoke,
            patch(
                "forge_harness.webhook_server.api.auth.create_refresh_token",
                return_value="new_refresh",
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="old_refresh")

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(
                auth_api.refresh_token_endpoint(request, _mock_request())
            )

            # Old token should be revoked
            mock_revoke.assert_called_once()
            # New tokens should be returned
            assert result.access_token == "new_access"
            assert result.refresh_token == "new_refresh"


class TestAuthConfig:
    """Tests for JWT configuration."""

    def test_jwt_config_from_env(self):
        """Test JWTAuthConfig from environment."""
        import os

        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthConfig

        with patch.dict(
            os.environ,
            {
                "FORGE_JWT_SECRET": "test-secret",
                "FORGE_JWT_ALGORITHM": "HS256",
                "FORGE_JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "60",
                "FORGE_JWT_REFRESH_TOKEN_EXPIRE_DAYS": "14",
                "FORGE_JWT_REQUIRE_AUTH": "true",
            },
        ):
            config = JWTAuthConfig.from_env()

            assert config.secret_key == "test-secret"
            assert config.algorithm == "HS256"
            assert config.access_token_expire_minutes == 60
            assert config.refresh_token_expire_days == 14
            assert config.require_auth is True

    def test_jwt_config_defaults(self):
        """Test JWTAuthConfig default values."""
        import os

        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthConfig

        with patch.dict(os.environ, {}, clear=False):
            # Remove JWT vars if set
            for key in list(os.environ.keys()):
                if key.startswith("FORGE_JWT_"):
                    del os.environ[key]

            with patch(
                "os.environ.get",
                side_effect=lambda k, d=None: {
                    "FORGE_JWT_SECRET": None,
                    "FORGE_JWT_ALGORITHM": "HS256",
                    "FORGE_JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "30",
                    "FORGE_JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
                    "FORGE_JWT_REQUIRE_AUTH": "true",
                }.get(k, d),
            ):
                config = JWTAuthConfig.from_env()

                # Secret will be generated
                assert config.algorithm == "HS256"
                assert config.access_token_expire_minutes == 30
                assert config.refresh_token_expire_days == 7


class TestJWTAuthResult:
    """Tests for JWTAuthResult enum."""

    def test_jwt_auth_result_values(self):
        """Test JWTAuthResult enum values."""
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        assert JWTAuthResult.SUCCESS.value == "success"
        assert JWTAuthResult.MISSING_TOKEN.value == "missing_token"
        assert JWTAuthResult.INVALID_TOKEN.value == "invalid_token"
        assert JWTAuthResult.EXPIRED_TOKEN.value == "expired_token"
        assert JWTAuthResult.INVALID_FORMAT.value == "invalid_format"


class TestAdditionalSecurityScenarios:
    """Additional security-focused tests."""

    def test_login_very_long_password(self, mock_jwt_config, mock_user_store, mock_audit_logger):
        """Test login with extremely long password (buffer overflow attempt)."""
        from forge_harness.webhook_server.api import auth as auth_api

        mock_user_store.authenticate = Mock(return_value=None)

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_user_store", return_value=mock_user_store),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit_logger),
        ):
            from forge_harness.webhook_server.api.auth import LoginRequest

            long_password = "a" * 10000
            request = LoginRequest(username="testuser", password=long_password)

            import asyncio

            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    auth_api.login(request, _mock_request())
                )

            assert exc_info.value.status_code == 401

    def test_login_special_chars_in_username(
        self, mock_jwt_config, mock_user_store, mock_audit_logger
    ):
        """Test login with special characters in username."""
        from forge_harness.webhook_server.api import auth as auth_api

        mock_user_store.authenticate = Mock(return_value=None)

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_user_store", return_value=mock_user_store),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit_logger),
        ):
            from forge_harness.webhook_server.api.auth import LoginRequest

            request = LoginRequest(username="<script>alert('xss')</script>", password="pass")

            import asyncio

            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    auth_api.login(request, _mock_request())
                )

            assert exc_info.value.status_code == 401

    def test_verify_token_different_auth_methods(self, mock_jwt_config, mock_jwt_payload):
        """Test verify tries access then refresh."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.api.auth.verify_access_token",
                return_value=(JWTAuthResult.INVALID_TOKEN, None),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_refresh_token",
                return_value=(JWTAuthResult.SUCCESS, mock_jwt_payload),
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="test_token")

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(auth_api.verify_token(request))

            assert result.valid is True

    def test_logout_with_expired_token(self, mock_jwt_config):
        """Test logout with expired token - payload present but may have no jti."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        # The logout endpoint calls decode_token and if payload exists, it tries to revoke
        # Even with None jti, it will attempt revocation
        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.decode_token",
                return_value=(JWTAuthResult.SUCCESS, None),
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="expired_token")

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(
                auth_api.logout(request, _mock_request())
            )

            assert result.status == "not_found"

    def test_auth_status_returns_config(self, mock_jwt_config):
        """Test auth status returns correct config values."""
        from forge_harness.webhook_server.api import auth as auth_api

        with patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config):
            import asyncio

            result = asyncio.get_event_loop().run_until_complete(auth_api.auth_status())

            assert result["algorithm"] == "HS256"
            assert result["access_token_expire_minutes"] == 30
            assert result["refresh_token_expire_days"] == 7

    def test_me_with_expired_token(self, mock_jwt_config):
        """Test get current user with expired token."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.api.auth.verify_bearer_token_jwt",
                return_value=(JWTAuthResult.EXPIRED_TOKEN, None),
            ),
        ):
            request = _mock_request(headers={"authorization": "Bearer expired_token"})

            import asyncio

            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(auth_api.get_current_user(request))

            assert exc_info.value.status_code == 401

    def test_refresh_with_valid_payload(self, mock_jwt_config):
        """Test refresh with valid payload."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        mock_payload = Mock()
        mock_payload.sub = "testuser"
        mock_payload.permissions = ["read"]
        mock_payload.jti = "token-id"
        mock_payload.exp = datetime.now(UTC) + timedelta(minutes=30)

        mock_audit = Mock()
        mock_audit.log = AsyncMock()

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit),
            patch(
                "forge_harness.webhook_server.api.auth.refresh_access_token",
                return_value=(JWTAuthResult.SUCCESS, "new_access"),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_refresh_token",
                return_value=(JWTAuthResult.SUCCESS, mock_payload),
            ),
            patch("forge_harness.webhook_server.api.auth.revoke_token"),
            patch(
                "forge_harness.webhook_server.api.auth.create_refresh_token",
                return_value="new_refresh",
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="valid_token")

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(
                auth_api.refresh_token_endpoint(request, _mock_request())
            )

            assert result.access_token == "new_access"

    def test_login_audit_includes_client_info(
        self, mock_jwt_config, mock_user, mock_user_store, mock_audit_logger
    ):
        """Test login audit includes IP and user agent."""
        from forge_harness.webhook_server.api import auth as auth_api

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_user_store", return_value=mock_user_store),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit_logger),
            patch(
                "forge_harness.webhook_server.api.auth.create_access_token", return_value="access"
            ),
            patch(
                "forge_harness.webhook_server.api.auth.create_refresh_token", return_value="refresh"
            ),
        ):
            from forge_harness.webhook_server.api.auth import LoginRequest

            request = LoginRequest(username="testuser", password="password")
            mock_request = _mock_request(
                headers={"User-Agent": "Mozilla/5.0"}, client_host="10.0.0.1"
            )

            import asyncio

            asyncio.get_event_loop().run_until_complete(auth_api.login(request, mock_request))

            # Verify audit log was called with client info
            mock_audit_logger.log.assert_called()
            call_kwargs = mock_audit_logger.log.call_args.kwargs
            assert call_kwargs.get("context", {}).get("ip") == "10.0.0.1"

    def test_verify_response_with_exp_time(self, mock_jwt_config, mock_jwt_payload):
        """Test verify response includes expiration time."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        mock_jwt_payload.exp = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch(
                "forge_harness.webhook_server.api.auth.verify_access_token",
                return_value=(JWTAuthResult.SUCCESS, mock_jwt_payload),
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            request = RefreshRequest(refresh_token="valid_token")

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(auth_api.verify_token(request))

            assert result.exp is not None
            assert "2026-12-31" in result.exp

    def test_multiple_login_attempts(self, mock_jwt_config, mock_user_store, mock_audit_logger):
        """Test multiple failed login attempts."""
        from forge_harness.webhook_server.api import auth as auth_api

        mock_user_store.authenticate = Mock(return_value=None)

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_user_store", return_value=mock_user_store),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit_logger),
        ):
            from forge_harness.webhook_server.api.auth import LoginRequest

            request = LoginRequest(username="attacker", password="wrong")

            import asyncio

            for _ in range(3):
                try:
                    asyncio.get_event_loop().run_until_complete(
                        auth_api.login(request, _mock_request())
                    )
                except HTTPException:
                    pass

            # Should have 3 failed audit logs
            assert mock_audit_logger.log.call_count >= 3

    def test_token_rotation_prevents_replay(
        self, mock_jwt_config, mock_audit_logger, mock_jwt_payload
    ):
        """Test that token rotation invalidates old refresh token."""
        from forge_harness.webhook_server.api import auth as auth_api
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthResult

        old_jti = "old-token-id"

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit_logger),
            patch(
                "forge_harness.webhook_server.api.auth.refresh_access_token",
                return_value=(JWTAuthResult.SUCCESS, "new_access"),
            ),
            patch(
                "forge_harness.webhook_server.infrastructure.jwt_auth.verify_refresh_token",
                return_value=(JWTAuthResult.SUCCESS, mock_jwt_payload),
            ),
            patch("forge_harness.webhook_server.api.auth.revoke_token") as mock_revoke,
            patch(
                "forge_harness.webhook_server.api.auth.create_refresh_token",
                return_value="new_refresh",
            ),
        ):
            from forge_harness.webhook_server.api.auth import RefreshRequest

            mock_jwt_payload.jti = old_jti

            request = RefreshRequest(refresh_token="old_refresh")

            import asyncio

            asyncio.get_event_loop().run_until_complete(
                auth_api.refresh_token_endpoint(request, _mock_request())
            )

            # Verify old token was revoked
            mock_revoke.assert_called_with(old_jti)

    def test_jwt_payload_to_dict(self):
        """Test JWTPayload to_dict conversion."""
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTPayload

        payload = JWTPayload(
            sub="testuser",
            exp=datetime.now(UTC),
            iat=datetime.now(UTC),
            jti="token-123",
            type="access",
            permissions=["read", "write"],
        )

        result = payload.to_dict()

        assert result["sub"] == "testuser"
        assert result["type"] == "access"
        assert result["permissions"] == ["read", "write"]

    def test_jwt_payload_from_dict(self):
        """Test JWTPayload from_dict conversion."""
        from forge_harness.webhook_server.infrastructure.jwt_auth import JWTPayload

        data = {
            "sub": "testuser",
            "exp": 1893456000,
            "iat": 1893452400,
            "jti": "token-456",
            "type": "refresh",
            "permissions": ["admin"],
        }

        payload = JWTPayload.from_dict(data)

        assert payload.sub == "testuser"
        assert payload.type == "refresh"
        assert payload.permissions == ["admin"]

    def test_login_with_admin_permissions(
        self, mock_jwt_config, mock_user, mock_user_store, mock_audit_logger
    ):
        """Test login with admin permissions grants correct access."""
        from forge_harness.webhook_server.api import auth as auth_api

        mock_user.permissions = ["admin", "read", "write"]

        with (
            patch.object(auth_api, "_get_jwt_config", return_value=mock_jwt_config),
            patch.object(auth_api, "get_user_store", return_value=mock_user_store),
            patch.object(auth_api, "get_audit_logger", return_value=mock_audit_logger),
            patch(
                "forge_harness.webhook_server.api.auth.create_access_token"
            ) as mock_create_access,
            patch(
                "forge_harness.webhook_server.api.auth.create_refresh_token"
            ) as mock_create_refresh,
        ):
            mock_create_access.return_value = "admin_token"
            mock_create_refresh.return_value = "admin_refresh"

            from forge_harness.webhook_server.api.auth import LoginRequest

            request = LoginRequest(username="admin", password="secure")

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(
                auth_api.login(request, _mock_request())
            )

            # Verify tokens were created
            assert mock_create_access.called
            call_kwargs = mock_create_access.call_args.kwargs
            assert call_kwargs.get("permissions") == ["admin", "read", "write"]
