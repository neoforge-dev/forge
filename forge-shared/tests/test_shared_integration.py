"""
Integration tests for forge-shared services.
Verifies that Auth, Analytics, and Middleware work together in a FastAPI app.
"""

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from forge_shared.auth import JWTAuth, JWTConfig, get_current_user, set_jwt_auth
from forge_shared.auth.models import ForgeUser, UserRole, Permission, PlanTier
from forge_shared.analytics.posthog import PostHogClient
from forge_shared.middleware import MiddlewareRegistry
from unittest.mock import MagicMock, patch

@pytest.fixture
def mock_posthog():
    with patch("forge_shared.analytics.posthog.PostHogBase") as mock:
        yield mock

@pytest.fixture
def app_settings():
    return {
        "jwt_secret": "test-secret-key-12345",
        "posthog_api_key": "phc_test_key",
        "redis_url": None,  # Use in-memory for tests
    }

@pytest.fixture
def app(app_settings, mock_posthog):
    app = FastAPI()
    
    # 1. Setup Auth
    jwt_config = JWTConfig(secret_key=app_settings["jwt_secret"])
    auth = JWTAuth(jwt_config)
    set_jwt_auth(auth)
    
    # 2. Setup Analytics
    analytics = PostHogClient(api_key=app_settings["posthog_api_key"])
    
    # 3. Setup Middleware
    registry = MiddlewareRegistry()
    registry.register_request_id()
    registry.register_security()
    registry.apply_to_app(app)
    
    @app.get("/api/test")
    async def test_route(user: ForgeUser = Depends(get_current_user)):
        await analytics.track(
            event="test_event",
            distinct_id=user.id,
            properties={"email": user.email}
        )
        return {"status": "success", "user": user.id}
    
    return app

def test_shared_services_integration(app, app_settings):
    client = TestClient(app)
    
    # Create a valid token
    auth = JWTAuth(JWTConfig(secret_key=app_settings["jwt_secret"]))
    token = auth.create_access_token(
        user_id="user-123",
        email="test@example.com",
        domain="test.com",
        plan=PlanTier.PRO
    )
    
    # Make request
    response = client.get(
        "/api/test",
        headers={"Authorization": f"Bearer {token}"}
    )
    
    # Verify response
    assert response.status_code == 200
    assert response.json()["user"] == "user-123"
    assert "X-Request-ID" in response.headers
    assert "Strict-Transport-Security" in response.headers
    
def test_unauthorized_request(app):
    client = TestClient(app)
    response = client.get("/api/test")
    assert response.status_code == 401
