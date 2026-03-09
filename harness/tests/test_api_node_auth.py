"""Tests for /api/node-auth endpoints."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client with nodes router."""
    from forge_harness.webhook_server.api.nodes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestGetJwtSecret:
    def test_get_jwt_secret_no_auth(self, client):
        # By default, verify_unified_auth will raise 401 if no credentials provided
        response = client.get("/api/node-auth/jwt-secret")
        assert response.status_code == 401

    @patch.dict(os.environ, {"FORGE_IS_LEAD": "true", "FORGE_JWT_SECRET": "test-secret-123"})
    def test_get_jwt_secret_success(self, client):
        # Override dependency to bypass real auth check
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth

        client.app.dependency_overrides[verify_unified_auth] = lambda: {
            "sub": "admin-user",
            "permissions": ["admin"],
        }

        response = client.get("/api/node-auth/jwt-secret")
        assert response.status_code == 200
        data = response.json()
        assert data["secret"] == "test-secret-123"
        assert "created_at" in data

    @patch.dict(os.environ, {"FORGE_IS_LEAD": "false"})
    @patch("platform.node", return_value="not-prya")
    def test_get_jwt_secret_not_lead(self, mock_node, client):
        # Override dependency to bypass real auth check
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth

        client.app.dependency_overrides[verify_unified_auth] = lambda: {
            "sub": "admin-user",
            "permissions": ["admin"],
        }

        response = client.get("/api/node-auth/jwt-secret")
        assert response.status_code == 403
        assert "only allowed from lead nodes" in response.json()["detail"]

    @patch.dict(os.environ, {"FORGE_IS_LEAD": "true"})
    @patch.dict(os.environ, {"FORGE_JWT_SECRET": ""})
    def test_get_jwt_secret_missing_on_lead(self, client):
        # Ensure FORGE_JWT_SECRET is actually missing/empty
        if "FORGE_JWT_SECRET" in os.environ:
            del os.environ["FORGE_JWT_SECRET"]

        # Override dependency to bypass real auth check
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth

        client.app.dependency_overrides[verify_unified_auth] = lambda: {
            "sub": "admin-user",
            "permissions": ["admin"],
        }

        response = client.get("/api/node-auth/jwt-secret")
        assert response.status_code == 500
        assert "not configured on lead node" in response.json()["detail"]

    @patch.dict(os.environ, {"FORGE_IS_LEAD": "false"})
    @patch("platform.node", return_value="prya")
    @patch.dict(os.environ, {"FORGE_JWT_SECRET": "prya-secret"})
    def test_get_jwt_secret_prya_fallback(self, mock_node, client):
        # Prya should be allowed even if FORGE_IS_LEAD is not true
        from forge_harness.webhook_server.core.dependencies import verify_unified_auth

        client.app.dependency_overrides[verify_unified_auth] = lambda: {
            "sub": "admin-user",
            "permissions": ["admin"],
        }

        response = client.get("/api/node-auth/jwt-secret")
        assert response.status_code == 200
        assert response.json()["secret"] == "prya-secret"
