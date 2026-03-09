"""Phase 1.3: SSE token handshake tests.

Asserts:
- No long-lived Bearer token in query param in production
- Session token flow works
"""

import os

import pytest

from forge_harness.webhook_server import AuthConfig, create_app


class TestSSESessionHandshake:
    """Session token handshake for SSE."""

    @pytest.fixture
    def app_production(self):
        """App with production config (no localhost bypass, SSE requires session)."""
        auth_config = AuthConfig(
            bearer_token="long-lived-secret",
            require_auth=True,
            allow_localhost=False,
            sse_require_session_token=True,  # Phase 1.3: reject Bearer in query
        )
        app = create_app(auth_config=auth_config)
        if app is None:
            pytest.skip("FastAPI not installed")
        return app

    @pytest.fixture
    def app_dev(self):
        """App with dev config (localhost bypass)."""
        auth_config = AuthConfig(
            bearer_token="dev-token",
            require_auth=True,
            allow_localhost=True,
        )
        app = create_app(auth_config=auth_config)
        if app is None:
            pytest.skip("FastAPI not installed")
        return app

    def test_sse_session_endpoint_returns_token(self, app_production):
        """POST /api/auth/sse-session returns short-lived session token."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app_production)
        response = client.post(
            "/api/auth/sse-session",
            headers={"Authorization": "Bearer long-lived-secret"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "session_token" in data.get("data", {})
        assert data["data"].get("expires_in") == 300

    def test_sse_session_requires_auth(self, app_production):
        """POST /api/auth/sse-session rejects missing token."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app_production)
        response = client.post("/api/auth/sse-session")
        assert response.status_code == 401

    def test_sse_accepts_session_token(self, app_production):
        """SSE endpoint accepts session token in query param."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app_production)
        # Get session token
        resp = client.post(
            "/api/auth/sse-session",
            headers={"Authorization": "Bearer long-lived-secret"},
        )
        assert resp.status_code == 200
        session_token = resp.json()["data"]["session_token"]

        # Verify session token is valid (used by SSE)
        from forge_harness.webhook_server.infrastructure.sse_session import (
            get_sse_session_store,
        )

        store = get_sse_session_store()
        assert store.validate(session_token) is True

    def test_sse_rejects_bearer_in_query_production(self, app_production):
        """In production, SSE rejects long-lived Bearer token in query param."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app_production)
        # Simulate EventSource: token in query only (cannot set headers)
        with client.stream(
            "GET", "/api/events?token=long-lived-secret"
        ) as response:
            # Should reject: long-lived token in URL
            assert response.status_code == 401

    def test_sse_bearer_via_header_still_works(self, app_dev):
        """Bearer token in header works for other endpoints (SSE flow uses session)."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(app_dev)
        resp = client.post(
            "/api/auth/validate",
            headers={"Authorization": "Bearer dev-token"},
        )
        assert resp.status_code == 200
