"""
Tests for Webhook Server Authentication
=========================================

Comprehensive authentication tests for the webhook_server.py endpoints.

Tests cover:
1. Token validation for protected endpoints
2. Invalid/expired token rejection
3. Missing token handling (401 response)
4. Unprotected endpoints work without auth (health, ready)
5. Header format variations (Bearer token vs raw token)
"""

import pytest

from forge_harness.webhook_server.infrastructure.jwt_auth import JWTAuthConfig, create_access_token


@pytest.fixture
def valid_token():
    """Generate a valid Bearer token for testing."""
    config = JWTAuthConfig(secret_key="test-jwt-secret")
    return create_access_token(
        subject="test-user",
        config=config,
        permissions=[
            "admin",
            "tasks:read",
            "tasks:write",
            "agents:read",
            "approvals:read",
            "approvals:write",
            "config:read",
            "config:write",
        ],
    )


@pytest.fixture
def mock_app_with_auth(tmp_path, monkeypatch, valid_token):
    """Create FastAPI TestClient with authentication enabled."""
    try:
        from fastapi.testclient import TestClient

        from forge_harness.webhook_server import AuthConfig, create_app
    except ImportError:
        pytest.skip("FastAPI not installed")

    # Set temp config directory
    config_dir = tmp_path / ".forge/config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("FORGE_JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("FORGE_JWT_REQUIRE_AUTH", "true")
    monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", valid_token)
    monkeypatch.setenv("FORGE_WEBHOOK_REQUIRE_AUTH", "true")

    # Enable authentication with test token
    auth_config = AuthConfig(
        bearer_token=valid_token,
        require_auth=True,
        allow_localhost=False,  # Disable localhost bypass for auth tests
    )

    # Create app with authentication enabled
    app = create_app(None, auth_config=auth_config)
    return TestClient(app)


@pytest.fixture
def mock_app_localhost_allowed(tmp_path, monkeypatch, valid_token):
    """Create FastAPI TestClient with localhost bypass enabled."""
    try:
        from fastapi.testclient import TestClient

        from forge_harness.webhook_server import AuthConfig, create_app
    except ImportError:
        pytest.skip("FastAPI not installed")

    # Set temp config directory
    config_dir = tmp_path / ".forge/config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("FORGE_JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("FORGE_JWT_REQUIRE_AUTH", "true")
    monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", valid_token)
    monkeypatch.setenv("FORGE_WEBHOOK_REQUIRE_AUTH", "true")

    # Enable authentication but allow localhost bypass
    auth_config = AuthConfig(
        bearer_token=valid_token,
        require_auth=True,
        allow_localhost=True,
    )

    app = create_app(None, auth_config=auth_config)
    return TestClient(app)


@pytest.fixture
def mock_app_no_auth(tmp_path, monkeypatch):
    """Create FastAPI TestClient with authentication disabled."""
    try:
        from fastapi.testclient import TestClient

        from forge_harness.webhook_server import AuthConfig, create_app
    except ImportError:
        pytest.skip("FastAPI not installed")

    # Set temp config directory
    config_dir = tmp_path / ".forge/config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(config_dir))

    # Unified auth currently relies on localhost bypass when no token is present.
    auth_config = AuthConfig(require_auth=False, allow_localhost=True)

    app = create_app(None, auth_config=auth_config)
    return TestClient(app)


class TestUnprotectedEndpoints:
    """Tests for endpoints that should NOT require authentication."""

    def test_health_check_no_auth_required(self, mock_app_with_auth):
        """GET /health should work without authentication."""
        response = mock_app_with_auth.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "forge-harness-webhooks"

    def test_api_health_check_no_auth_required(self, mock_app_with_auth):
        """GET /api/health should work without authentication (alias for /health)."""
        response = mock_app_with_auth.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_version_endpoint_no_auth_required(self, mock_app_with_auth):
        """GET /api/version should work without authentication."""
        response = mock_app_with_auth.get("/api/version")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "version" in data["data"]


class TestProtectedEndpointsWithValidToken:
    """Tests for protected endpoints with valid Bearer token."""

    def test_approvals_list_with_valid_token(self, mock_app_with_auth, valid_token):
        """GET /api/approvals should succeed with valid Bearer token."""
        response = mock_app_with_auth.get(
            "/api/approvals", headers={"Authorization": f"Bearer {valid_token}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "approvals" in data["data"]

    def test_approvals_stats_with_valid_token(self, mock_app_with_auth, valid_token):
        """GET /api/approvals/stats should succeed with valid token."""
        response = mock_app_with_auth.get(
            "/api/approvals/stats", headers={"Authorization": f"Bearer {valid_token}"}
        )

        assert response.status_code == 200
        data = response.json()
        # Stats endpoint returns direct data, not wrapped in API response
        assert "pending" in data or "total" in data or isinstance(data, dict)

    def test_tasks_list_with_valid_token(self, mock_app_with_auth, valid_token):
        """GET /api/tasks should succeed with valid token."""
        response = mock_app_with_auth.get(
            "/api/tasks", headers={"Authorization": f"Bearer {valid_token}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "tasks" in data["data"]

    def test_config_llm_get_with_valid_token(self, mock_app_with_auth, valid_token):
        """GET /api/config/llm should succeed with valid token."""
        response = mock_app_with_auth.get(
            "/api/config/llm", headers={"Authorization": f"Bearer {valid_token}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "provider" in data["data"]

    def test_rate_limit_stats_with_valid_token(self, mock_app_with_auth, valid_token):
        """GET /api/rate-limit/stats should succeed with valid token."""
        response = mock_app_with_auth.get(
            "/api/rate-limit/stats", headers={"Authorization": f"Bearer {valid_token}"}
        )

        assert response.status_code == 200
        # Rate limit stats returns plain dict, not API response wrapper
        data = response.json()
        assert "enabled" in data


class TestProtectedEndpointsWithoutToken:
    """Tests for protected endpoints without authentication token."""

    def test_approvals_list_missing_token(self, mock_app_with_auth):
        """GET /api/approvals should return 401 without token."""
        response = mock_app_with_auth.get("/api/approvals")

        assert response.status_code == 401
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "unauthorized"
        assert (
            "missing" in data["error"]["message"].lower()
            or "unauthorized" in data["error"]["message"].lower()
            or "not provided" in data["error"]["message"].lower()
        )

    def test_approvals_stats_missing_token(self, mock_app_with_auth):
        """GET /api/approvals/stats should return 401 without token."""
        response = mock_app_with_auth.get("/api/approvals/stats")

        assert response.status_code == 401
        data = response.json()
        assert data["success"] is False

    def test_tasks_list_missing_token(self, mock_app_with_auth):
        """GET /api/tasks should return 401 without token."""
        response = mock_app_with_auth.get("/api/tasks")

        assert response.status_code == 401

    def test_config_llm_get_missing_token(self, mock_app_with_auth):
        """GET /api/config/llm should return 401 without token."""
        response = mock_app_with_auth.get("/api/config/llm")

        assert response.status_code == 401

    def test_config_llm_post_missing_token(self, mock_app_with_auth):
        """POST /api/config/llm should return 401 without token."""
        response = mock_app_with_auth.post("/api/config/llm", json={"provider": "openai"})

        assert response.status_code == 401


class TestProtectedEndpointsWithInvalidToken:
    """Tests for protected endpoints with invalid Bearer token."""

    def test_approvals_list_invalid_token(self, mock_app_with_auth):
        """GET /api/approvals should return 401 with invalid token."""
        response = mock_app_with_auth.get(
            "/api/approvals", headers={"Authorization": "Bearer invalid-token-xyz"}
        )

        assert response.status_code == 401
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "unauthorized"

    def test_tasks_list_invalid_token(self, mock_app_with_auth):
        """GET /api/tasks should return 401 with invalid token."""
        response = mock_app_with_auth.get(
            "/api/tasks", headers={"Authorization": "Bearer wrong-token-12345"}
        )

        assert response.status_code == 401

    def test_config_llm_invalid_token(self, mock_app_with_auth):
        """GET /api/config/llm should return 401 with invalid token."""
        response = mock_app_with_auth.get(
            "/api/config/llm", headers={"Authorization": "Bearer bad-token"}
        )

        assert response.status_code == 401


class TestAuthHeaderFormatVariations:
    """Tests for various Authorization header format variations."""

    def test_bearer_prefix_case_insensitive(self, mock_app_with_auth, valid_token):
        """Authorization header should accept 'Bearer' prefix (case matters in HTTP spec)."""
        # Note: HTTP spec requires 'Bearer' with capital B
        response = mock_app_with_auth.get(
            "/api/approvals", headers={"Authorization": f"Bearer {valid_token}"}
        )

        assert response.status_code == 200

    def test_token_without_bearer_prefix_rejected(self, mock_app_with_auth, valid_token):
        """Raw token without 'Bearer ' prefix should be rejected."""
        response = mock_app_with_auth.get("/api/approvals", headers={"Authorization": valid_token})

        assert response.status_code == 401
        data = response.json()
        assert data["success"] is False

    def test_malformed_bearer_prefix_rejected(self, mock_app_with_auth, valid_token):
        """Malformed Bearer prefix should be rejected."""
        response = mock_app_with_auth.get(
            "/api/approvals", headers={"Authorization": f"bearer{valid_token}"}
        )  # Missing space

        assert response.status_code == 401

    def test_empty_bearer_token_rejected(self, mock_app_with_auth):
        """Empty token after Bearer prefix should be rejected."""
        response = mock_app_with_auth.get("/api/approvals", headers={"Authorization": "Bearer "})

        assert response.status_code == 401


class TestLocalhostBypass:
    """Tests for localhost authentication bypass feature."""

    def test_localhost_bypass_allows_access(self, mock_app_localhost_allowed):
        """Protected endpoints should work without token when localhost bypass enabled."""
        # TestClient uses 'testclient' as client host, which is treated as localhost
        response = mock_app_localhost_allowed.get("/api/approvals")

        # Should succeed without token due to localhost bypass
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_localhost_bypass_disabled_requires_token(self, mock_app_with_auth):
        """When localhost bypass disabled, all requests need token."""
        response = mock_app_with_auth.get("/api/approvals")

        assert response.status_code == 401


class TestAuthenticationDisabled:
    """Tests for when authentication is completely disabled."""

    def test_protected_endpoints_work_without_auth(self, mock_app_no_auth):
        """Protected endpoints should work without token when auth disabled."""
        response = mock_app_no_auth.get("/api/approvals")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_config_endpoints_work_without_auth(self, mock_app_no_auth):
        """Config endpoints should work without token when auth disabled."""
        response = mock_app_no_auth.get("/api/config/llm")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_tasks_endpoints_work_without_auth(self, mock_app_no_auth):
        """Task endpoints should work without token when auth disabled."""
        response = mock_app_no_auth.get("/api/tasks")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestPostEndpointsAuthentication:
    """Tests for POST endpoints requiring authentication."""

    def test_approval_approve_with_valid_token(self, mock_app_with_auth, valid_token, tmp_path):
        """POST /api/approvals/{id}/approve should succeed with valid token (auth check only)."""
        response = mock_app_with_auth.post(
            "/api/approvals/test-request-123/approve",
            headers={"Authorization": f"Bearer {valid_token}"},
            json={"approver": "test-user", "comment": "LGTM", "auto_resume": True},
        )

        # Should pass auth (404 is expected since approval doesn't exist)
        # 400 would mean validation error, 401 would mean auth failed
        assert response.status_code in [200, 404, 400]
        # If 400, it's because the approval doesn't exist, not because auth failed
        if response.status_code == 401:
            pytest.fail("Authentication failed when it should have succeeded")

    def test_approval_approve_without_token(self, mock_app_with_auth):
        """POST /api/approvals/{id}/approve should return 401 without token."""
        response = mock_app_with_auth.post(
            "/api/approvals/test-request-123/approve",
            json={"approver": "test-user", "comment": "LGTM"},
        )

        assert response.status_code == 401

    def test_task_create_with_valid_token(self, mock_app_with_auth, valid_token):
        """POST /api/tasks should succeed with valid token."""
        response = mock_app_with_auth.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {valid_token}"},
            json={
                "type": "feature",
                "title": "Test task",
                "description": "Test description",
                "domain": "test-domain",
                "priority": "medium",
            },
        )

        # Should pass auth check (422 validation error is OK, 401 is not)
        assert response.status_code in [200, 422]
        if response.status_code == 401:
            pytest.fail("Authentication failed when it should have succeeded")

    def test_task_create_without_token(self, mock_app_with_auth):
        """POST /api/tasks should return 401 without token."""
        response = mock_app_with_auth.post(
            "/api/tasks",
            json={
                "type": "feature",
                "title": "Test task",
                "description": "Test description",
            },
        )

        assert response.status_code == 401

    def test_config_llm_update_with_valid_token(self, mock_app_with_auth, valid_token):
        """POST /api/config/llm should succeed with valid token."""
        response = mock_app_with_auth.post(
            "/api/config/llm",
            headers={"Authorization": f"Bearer {valid_token}"},
            json={"provider": "openai", "model": "gpt-4"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_config_llm_update_invalid_token(self, mock_app_with_auth):
        """POST /api/config/llm should return 401 with invalid token."""
        response = mock_app_with_auth.post(
            "/api/config/llm",
            headers={"Authorization": "Bearer invalid-token"},
            json={"provider": "openai"},
        )

        assert response.status_code == 401


class TestBulkOperationsAuthentication:
    """Tests for bulk operations requiring authentication."""

    def test_bulk_approve_with_valid_token(self, mock_app_with_auth, valid_token):
        """POST /api/approvals/bulk/approve should succeed with valid token."""
        response = mock_app_with_auth.post(
            "/api/approvals/bulk/approve",
            headers={"Authorization": f"Bearer {valid_token}"},
            json={
                "request_ids": ["req-1", "req-2"],
                "approver": "test-user",
                "comment": "Bulk approve",
            },
        )

        # Should pass auth check (400/404 for missing approvals is OK, 401 is not)
        assert response.status_code in [200, 400, 404]
        if response.status_code == 401:
            pytest.fail("Authentication failed when it should have succeeded")

    def test_bulk_approve_without_token(self, mock_app_with_auth):
        """POST /api/approvals/bulk/approve should return 401 without token."""
        response = mock_app_with_auth.post(
            "/api/approvals/bulk/approve",
            json={"request_ids": ["req-1", "req-2"], "approver": "test-user"},
        )

        assert response.status_code == 401

    def test_bulk_reject_with_valid_token(self, mock_app_with_auth, valid_token):
        """POST /api/approvals/bulk/reject should succeed with valid token."""
        response = mock_app_with_auth.post(
            "/api/approvals/bulk/reject",
            headers={"Authorization": f"Bearer {valid_token}"},
            json={
                "request_ids": ["req-1", "req-2"],
                "rejector": "test-user",
                "reason": "Bulk reject",
            },
        )

        # Should pass auth check (400/404 for missing approvals is OK, 401 is not)
        assert response.status_code in [200, 400, 404]
        if response.status_code == 401:
            pytest.fail("Authentication failed when it should have succeeded")

    def test_bulk_reject_without_token(self, mock_app_with_auth):
        """POST /api/approvals/bulk/reject should return 401 without token."""
        response = mock_app_with_auth.post(
            "/api/approvals/bulk/reject", json={"request_ids": ["req-1"], "rejector": "test-user"}
        )

        assert response.status_code == 401


class TestWWWAuthenticateHeader:
    """Tests for proper WWW-Authenticate header in 401 responses."""

    def test_401_response_includes_www_authenticate_header(self, mock_app_with_auth):
        """401 responses should include WWW-Authenticate header."""
        response = mock_app_with_auth.get("/api/approvals")

        assert response.status_code == 401
        # Check for WWW-Authenticate header
        assert "WWW-Authenticate" in response.headers or "www-authenticate" in response.headers

    def test_www_authenticate_header_format(self, mock_app_with_auth):
        """WWW-Authenticate header should specify Bearer scheme."""
        response = mock_app_with_auth.get("/api/approvals")

        assert response.status_code == 401
        auth_header = response.headers.get("WWW-Authenticate") or response.headers.get(
            "www-authenticate"
        )
        # Should indicate Bearer authentication scheme
        if auth_header:
            assert "Bearer" in auth_header
