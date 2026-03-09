"""Hardened Authentication Integration Tests

Tests for PR #60 hardened authentication:
- Unified authentication (JWT, API Key, Legacy Token)
- Actual credential validation in login
- API Key generation and verification
- Audit logging for auth events
"""

import pytest
from fastapi.testclient import TestClient

from forge_harness.webhook_server.infrastructure.api_key_auth import _api_keys, generate_api_key
from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthConfig, create_access_token
from forge_harness.webhook_server.infrastructure.user_store import _user_store, get_user_store
from forge_harness.webhook_server_main import create_app


@pytest.fixture
def client():
    # Set environment variables for testing
    import os
    os.environ["FORGE_JWT_SECRET"] = "test-secret-hardening"
    os.environ["FORGE_AUTH_USERS"] = "testuser:testpass,admin:adminpass"

    # Reset stores
    _api_keys.clear()
    user_store = get_user_store()
    user_store._users.clear()
    user_store._load_from_env()

    app = create_app()
    return TestClient(app)

class TestLoginHardening:
    """Test actual credential validation in login endpoint."""

    def test_login_success(self, client):
        response = client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "testpass"
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

    def test_login_invalid_password(self, client):
        response = client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "wrongpassword"
        })
        assert response.status_code == 401
        assert "Invalid username or password" in response.json()["error"]["message"]

    def test_login_invalid_user(self, client):
        response = client.post("/api/auth/login", json={
            "username": "nonexistent",
            "password": "testpass"
        })
        assert response.status_code == 401

class TestAPIKeyHardening:
    """Test API key generation and verification."""

    def test_create_api_key_unauthorized(self, client):
        # Without any auth
        response = client.post("/api/auth/api-keys", json={
            "owner": "service-a",
            "permissions": ["read"]
        })
        assert response.status_code == 401

    def test_create_api_key_as_admin(self, client):
        # 1. Login as admin
        login_res = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "adminpass"
        })
        token = login_res.json()["access_token"]

        # 2. Create API key
        response = client.post(
            "/api/auth/api-keys",
            json={"owner": "service-a", "permissions": ["read"]},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "api_key" in data
        assert data["owner"] == "service-a"

        # 3. Use API key to access a protected endpoint
        api_key = data["api_key"]
        protected_res = client.get(
            "/api/agents",
            headers={"X-API-Key": api_key}
        )
        assert protected_res.status_code == 200

    def test_create_api_key_forbidden_for_regular_user(self, client):
        # 1. Login as regular user
        login_res = client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "testpass"
        })
        token = login_res.json()["access_token"]

        # 2. Try to create API key
        response = client.post(
            "/api/auth/api-keys",
            json={"owner": "illegal", "permissions": ["admin"]},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403 # Forbidden

class TestUnifiedAuth:
    """Test verify_unified_auth across all methods."""

    def test_jwt_auth(self, client):
        login_res = client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "testpass"
        })
        token = login_res.json()["access_token"]

        response = client.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200

    def test_legacy_token_fallback(self, client):
        # Mock FORGE_WEBHOOK_TOKEN
        import os
        os.environ["FORGE_WEBHOOK_TOKEN"] = "legacy-secret-token"

        # Re-create app to pick up new env var
        app = create_app()
        c = TestClient(app)

        response = c.get(
            "/api/agents",
            headers={"Authorization": "Bearer legacy-secret-token"}
        )
        assert response.status_code == 200

    def test_invalid_bearer_token(self, client):
        response = client.get(
            "/api/agents",
            headers={"Authorization": "Bearer invalid-junk"}
        )
        assert response.status_code == 401
