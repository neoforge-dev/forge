"""
Tests for authentication middleware.

Comprehensive tests for AuthMiddleware and StrictAuthMiddleware classes
including token validation, path exclusion, and error handling.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, Request, status
from fastapi.testclient import TestClient
from starlette.responses import Response

from forge_shared.auth.middleware import (
    AuthMiddleware,
    StrictAuthMiddleware,
    get_request_user,
    require_request_user,
)
from forge_shared.auth.models import ForgeUser


# Test fixtures
@pytest.fixture
def test_user():
    """Create test user."""
    from forge_shared.auth.models import PlanTier, UserRole, Permission
    return ForgeUser(
        id="user123",
        email="test@example.com",
        domain="example.com",
        plan=PlanTier.PRO,
        products=["test-product"],
        roles=[UserRole.USER],
        permissions=[Permission.READ_OWN]
    )


@pytest.fixture
def valid_token_payload():
    """Create valid token payload."""
    from forge_shared.auth.models import PlanTier, UserRole, Permission
    return {
        "sub": "user123",
        "email": "test@example.com",
        "domain": "example.com",
        "plan": PlanTier.PRO,
        "products": ["test-product"],
        "roles": [UserRole.USER],
        "permissions": [Permission.READ_OWN],
        "exp": 9999999999,
        "iat": 9999999999
    }


@pytest.fixture
def mock_jwt_auth():
    """Mock JWT auth instance."""
    mock = MagicMock()
    mock.decode_token = MagicMock()
    return mock


class TestAuthMiddleware:
    """Tests for AuthMiddleware (non-blocking authentication)."""

    def test_middleware_initialization(self):
        """Test middleware initialization with default values."""
        app = FastAPI()
        middleware = AuthMiddleware(app)
        
        assert middleware.app == app
        assert middleware.exclude_paths == set()

    def test_middleware_initialization_with_exclude_paths(self):
        """Test middleware initialization with custom exclude paths."""
        app = FastAPI()
        exclude_paths = ["/health", "/public", "/docs"]
        middleware = AuthMiddleware(app, exclude_paths=exclude_paths)
        
        assert middleware.exclude_paths == set(exclude_paths)

    @pytest.mark.asyncio
    async def test_excluded_path_bypasses_auth(self):
        """Test that excluded paths bypass authentication."""
        app = FastAPI()
        app.add_middleware(
            AuthMiddleware,
            exclude_paths=["/health"]
        )
        
        @app.get("/health")
        async def health():
            return {"status": "ok"}
        
        client = TestClient(app)
        response = client.get("/health")
        
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_request_without_token(self):
        """Test request without Authorization header."""
        app = FastAPI()
        app.add_middleware(AuthMiddleware)
        
        @app.get("/protected")
        async def protected(request: Request):
            user = get_request_user(request)
            return {"user": user.email if user else "anonymous"}
        
        client = TestClient(app)
        response = client.get("/protected")
        
        # Non-blocking: should still work without token
        assert response.status_code == 200
        assert response.json() == {"user": "anonymous"}

    @pytest.mark.asyncio
    async def test_request_with_valid_token(self, test_user, mock_jwt_auth):
        """Test request with valid JWT token."""
        app = FastAPI()
        app.add_middleware(AuthMiddleware)
        
        @app.get("/protected")
        async def protected(request: Request):
            user = get_request_user(request)
            return {"user": user.email if user else "anonymous"}
        
        # Mock get_jwt_auth and decode_token
        with patch('forge_shared.auth.middleware.get_jwt_auth', return_value=mock_jwt_auth):
            mock_payload = MagicMock()
            mock_payload.to_user = MagicMock(return_value=test_user)
            mock_jwt_auth.decode_token.return_value = mock_payload
            
            client = TestClient(app)
            response = client.get(
                "/protected",
                headers={"Authorization": "Bearer valid_token"}
            )
        
        assert response.status_code == 200
        assert response.json() == {"user": "test@example.com"}

    @pytest.mark.asyncio
    async def test_request_with_invalid_token(self, mock_jwt_auth):
        """Test request with invalid JWT token."""
        from jose import JWTError
        app = FastAPI()
        app.add_middleware(AuthMiddleware)
        
        @app.get("/protected")
        async def protected(request: Request):
            user = get_request_user(request)
            return {"user": user.email if user else "anonymous"}
        
        # Mock get_jwt_auth to raise JWTError
        with patch('forge_shared.auth.middleware.get_jwt_auth', return_value=mock_jwt_auth):
            mock_jwt_auth.decode_token.side_effect = JWTError("Invalid token")
            
            client = TestClient(app)
            response = client.get(
                "/protected",
                headers={"Authorization": "Bearer invalid_token"}
            )
        
        # Non-blocking: should continue without user context
        assert response.status_code == 200
        assert response.json() == {"user": "anonymous"}

    @pytest.mark.asyncio
    async def test_request_with_malformed_auth_header(self):
        """Test request with malformed Authorization header."""
        app = FastAPI()
        app.add_middleware(AuthMiddleware)
        
        @app.get("/protected")
        async def protected(request: Request):
            user = get_request_user(request)
            return {"user": user.email if user else "anonymous"}
        
        client = TestClient(app)
        response = client.get(
            "/protected",
            headers={"Authorization": "InvalidFormat"}
        )
        
        # Should not crash, just skip authentication
        assert response.status_code == 200
        assert response.json() == {"user": "anonymous"}

    @pytest.mark.asyncio
    async def test_request_without_bearer_prefix(self):
        """Test request with Authorization header but no Bearer prefix."""
        app = FastAPI()
        app.add_middleware(AuthMiddleware)
        
        @app.get("/protected")
        async def protected(request: Request):
            user = get_request_user(request)
            return {"user": user.email if user else "anonymous"}
        
        client = TestClient(app)
        response = client.get(
            "/protected",
            headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
        
        # Should not crash, just skip authentication
        assert response.status_code == 200
        assert response.json() == {"user": "anonymous"}

    @pytest.mark.asyncio
    async def test_multiple_excluded_paths(self):
        """Test multiple excluded paths."""
        app = FastAPI()
        app.add_middleware(
            AuthMiddleware,
            exclude_paths=["/health", "/metrics", "/docs"]
        )
        
        @app.get("/health")
        async def health():
            return {"status": "ok"}
        
        @app.get("/metrics")
        async def metrics():
            return {"metrics": "data"}
        
        @app.get("/docs")
        async def docs():
            return {"docs": "here"}
        
        client = TestClient(app)
        
        # All excluded paths should work without token
        assert client.get("/health").status_code == 200
        assert client.get("/metrics").status_code == 200
        assert client.get("/docs").status_code == 200


class TestStrictAuthMiddleware:
    """Tests for StrictAuthMiddleware (blocking authentication)."""

    def test_strict_middleware_initialization(self):
        """Test strict middleware initialization."""
        app = FastAPI()
        middleware = StrictAuthMiddleware(app)
        
        assert middleware.app == app
        assert middleware.exclude_paths == set()

    @pytest.mark.asyncio
    async def test_strict_excluded_path_bypasses_auth(self):
        """Test that excluded paths bypass authentication in strict mode."""
        app = FastAPI()
        app.add_middleware(
            StrictAuthMiddleware,
            exclude_paths=["/health"]
        )
        
        @app.get("/health")
        async def health():
            return {"status": "ok"}
        
        client = TestClient(app)
        response = client.get("/health")
        
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_strict_missing_token_returns_401(self):
        """Test that missing token returns 401 in strict mode."""
        app = FastAPI()
        app.add_middleware(StrictAuthMiddleware)
        
        @app.get("/protected")
        async def protected(request: Request):
            return {"data": "secret"}
        
        client = TestClient(app)
        response = client.get("/protected")
        
        assert response.status_code == 401
        assert "Not authenticated" in response.json()["detail"]
        assert response.headers.get("WWW-Authenticate") == "Bearer"

    @pytest.mark.asyncio
    async def test_strict_invalid_auth_format_returns_401(self):
        """Test that invalid Authorization format returns 401."""
        app = FastAPI()
        app.add_middleware(StrictAuthMiddleware)
        
        @app.get("/protected")
        async def protected(request: Request):
            return {"data": "secret"}
        
        client = TestClient(app)
        response = client.get(
            "/protected",
            headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
        
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_strict_valid_token_succeeds(self, test_user, mock_jwt_auth):
        """Test that valid token allows access in strict mode."""
        app = FastAPI()
        app.add_middleware(StrictAuthMiddleware)
        
        @app.get("/protected")
        async def protected(request: Request):
            user = get_request_user(request)
            return {"user": user.email}
        
        with patch('forge_shared.auth.middleware.get_jwt_auth', return_value=mock_jwt_auth):
            mock_payload = MagicMock()
            mock_payload.to_user = MagicMock(return_value=test_user)
            mock_jwt_auth.decode_token.return_value = mock_payload
            
            client = TestClient(app)
            response = client.get(
                "/protected",
                headers={"Authorization": "Bearer valid_token"}
            )
        
        assert response.status_code == 200
        assert response.json() == {"user": "test@example.com"}

    @pytest.mark.asyncio
    async def test_strict_invalid_token_returns_401(self, mock_jwt_auth):
        """Test that invalid token returns 401 in strict mode."""
        from jose import JWTError
        app = FastAPI()
        app.add_middleware(StrictAuthMiddleware)
        
        @app.get("/protected")
        async def protected(request: Request):
            return {"data": "secret"}
        
        with patch('forge_shared.auth.middleware.get_jwt_auth', return_value=mock_jwt_auth):
            mock_jwt_auth.decode_token.side_effect = JWTError("Invalid token")
            
            client = TestClient(app)
            response = client.get(
                "/protected",
                headers={"Authorization": "Bearer invalid_token"}
            )
        
        assert response.status_code == 401
        assert "Could not validate credentials" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_strict_exception_in_token_validation_returns_401(self, mock_jwt_auth):
        """Test that exceptions during token validation return 401."""
        app = FastAPI()
        app.add_middleware(StrictAuthMiddleware)
        
        @app.get("/protected")
        async def protected(request: Request):
            return {"data": "secret"}
        
        with patch('forge_shared.auth.middleware.get_jwt_auth', return_value=mock_jwt_auth):
            mock_jwt_auth.decode_token.side_effect = Exception("Unexpected error")
            
            client = TestClient(app)
            response = client.get(
                "/protected",
                headers={"Authorization": "Bearer problematic_token"}
            )
        
        assert response.status_code == 401


class TestGetRequestUser:
    """Tests for get_request_user helper function."""

    def test_get_user_when_present(self, test_user):
        """Test getting user when present in request state."""
        request = MagicMock()
        request.state.user = test_user
        
        result = get_request_user(request)
        
        assert result == test_user
        assert result.email == "test@example.com"

    def test_get_user_when_absent(self):
        """Test getting user when not present in request state."""
        request = MagicMock()
        request.state = MagicMock()
        del request.state.user
        
        result = get_request_user(request)
        
        assert result is None

    def test_get_user_with_no_user_attribute(self):
        """Test getting user when request state has no user attribute."""
        request = MagicMock()
        request.state = MagicMock(spec=[])  # No user attribute
        
        result = get_request_user(request)
        
        assert result is None


class TestRequireRequestUser:
    """Tests for require_request_user helper function."""

    def test_require_user_when_present(self, test_user):
        """Test requiring user when present."""
        request = MagicMock()
        request.state.user = test_user
        
        result = require_request_user(request)
        
        assert result == test_user
        assert result.email == "test@example.com"

    def test_require_user_when_absent_raises_401(self):
        """Test requiring user when absent raises HTTPException."""
        from fastapi import HTTPException
        
        request = MagicMock()
        request.state = MagicMock()
        del request.state.user
        
        with pytest.raises(HTTPException) as exc_info:
            require_request_user(request)
        
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Not authenticated"
        assert exc_info.value.headers.get("WWW-Authenticate") == "Bearer"

    def test_require_user_with_no_user_attribute_raises_401(self):
        """Test requiring user with no user attribute raises HTTPException."""
        from fastapi import HTTPException
        
        request = MagicMock()
        request.state = MagicMock(spec=[])  # No user attribute
        
        with pytest.raises(HTTPException) as exc_info:
            require_request_user(request)
        
        assert exc_info.value.status_code == 401


class TestMiddlewareIntegration:
    """Integration tests for middleware with realistic scenarios."""

    @pytest.mark.asyncio
    async def test_full_request_flow_with_auth(self, test_user, mock_jwt_auth):
        """Test complete request flow with authentication."""
        app = FastAPI()
        app.add_middleware(AuthMiddleware)
        
        @app.get("/api/data")
        async def get_data(request: Request):
            user = get_request_user(request)
            if user:
                return {"data": f"Secret data for {user.email}"}
            return {"data": "Public data"}
        
        with patch('forge_shared.auth.middleware.get_jwt_auth', return_value=mock_jwt_auth):
            mock_payload = MagicMock()
            mock_payload.to_user = MagicMock(return_value=test_user)
            mock_jwt_auth.decode_token.return_value = mock_payload
            
            client = TestClient(app)
            response = client.get(
                "/api/data",
                headers={"Authorization": "Bearer valid_token"}
            )
        
        assert response.status_code == 200
        assert response.json() == {"data": "Secret data for test@example.com"}

    @pytest.mark.asyncio
    async def test_mixed_public_and_protected_routes(self, test_user, mock_jwt_auth):
        """Test app with both public and protected routes."""
        app = FastAPI()
        app.add_middleware(
            AuthMiddleware,
            exclude_paths=["/health", "/public"]
        )
        
        @app.get("/health")
        async def health():
            return {"status": "ok"}
        
        @app.get("/public")
        async def public():
            return {"message": "public"}
        
        @app.get("/protected")
        async def protected(request: Request):
            user = get_request_user(request)
            return {"user": user.email if user else "anonymous"}
        
        client = TestClient(app)
        
        # Public routes work without token
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/public").json() == {"message": "public"}
        
        # Protected route without token
        assert client.get("/protected").json() == {"user": "anonymous"}
        
        # Protected route with valid token
        with patch('forge_shared.auth.middleware.get_jwt_auth', return_value=mock_jwt_auth):
            mock_payload = MagicMock()
            mock_payload.to_user = MagicMock(return_value=test_user)
            mock_jwt_auth.decode_token.return_value = mock_payload
            
            response = client.get(
                "/protected",
                headers={"Authorization": "Bearer valid_token"}
            )
            assert response.json() == {"user": "test@example.com"}

    @pytest.mark.asyncio
    async def test_strict_mode_enforces_authentication(self, test_user, mock_jwt_auth):
        """Test that strict mode enforces authentication on all non-excluded routes."""
        app = FastAPI()
        app.add_middleware(
            StrictAuthMiddleware,
            exclude_paths=["/health"]
        )
        
        @app.get("/health")
        async def health():
            return {"status": "ok"}
        
        @app.get("/api/data")
        async def data(request: Request):
            user = get_request_user(request)
            return {"user": user.email}
        
        client = TestClient(app)
        
        # Excluded path works without token
        assert client.get("/health").status_code == 200
        
        # Protected route requires token
        assert client.get("/api/data").status_code == 401
        
        # Valid token allows access
        with patch('forge_shared.auth.middleware.get_jwt_auth', return_value=mock_jwt_auth):
            mock_payload = MagicMock()
            mock_payload.to_user = MagicMock(return_value=test_user)
            mock_jwt_auth.decode_token.return_value = mock_payload
            
            response = client.get(
                "/api/data",
                headers={"Authorization": "Bearer valid_token"}
            )
            assert response.status_code == 200
            assert response.json() == {"user": "test@example.com"}
