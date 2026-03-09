"""Tests for security headers middleware in webhook server."""

import pytest


@pytest.fixture
def client():
    """Create test client for webhook server."""
    try:
        from fastapi.testclient import TestClient

        from forge_harness.webhook_server import AuthConfig, create_app
    except ImportError:
        pytest.skip("FastAPI not installed")

    # Create app with auth disabled for testing
    auth_config = AuthConfig(require_auth=False)
    app = create_app(auth_config=auth_config)

    if app is None:
        pytest.skip("FastAPI not installed")

    return TestClient(app)


def test_security_headers_on_health_endpoint(client):
    """Test that security headers are present on health endpoint."""
    response = client.get("/health")

    # Check all security headers are present
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-XSS-Protection") == "1; mode=block"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    # HSTS should NOT be present for HTTP requests (only HTTPS)
    assert "Strict-Transport-Security" not in response.headers


def test_security_headers_on_api_endpoints(client):
    """Test that security headers are present on API endpoints."""
    # Test with a typical API endpoint (may return 401 but headers should be present)
    response = client.get("/api/sessions")

    # Check all security headers are present even on protected endpoints
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-XSS-Protection") == "1; mode=block"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


def test_security_headers_on_error_responses(client):
    """Test that security headers are present even on error responses."""
    # Request a non-existent endpoint
    response = client.get("/this-does-not-exist")

    # Even 404 responses should have security headers
    assert response.status_code == 404
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-XSS-Protection") == "1; mode=block"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


def test_no_csp_header(client):
    """Test that Content-Security-Policy is NOT added (to avoid breaking frontend)."""
    response = client.get("/health")

    # CSP should NOT be present (intentionally excluded)
    assert "Content-Security-Policy" not in response.headers
    assert "Content-Security-Policy-Report-Only" not in response.headers
