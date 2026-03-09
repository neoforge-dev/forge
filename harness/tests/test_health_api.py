"""Tests for Health API endpoints.

Tests /health, /api/health, /health/full, /health/metrics, and related endpoints.
These tests exercise the endpoints without mocking internals - they still provide
coverage of the code paths by running through the actual logic.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge_harness.webhook_server.api.health import router


@pytest.fixture
def app():
    """Create FastAPI app with health router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


class TestHealthCheck:
    """Tests for /health endpoint."""

    def test_health_check_response_format(self, client):
        """Test health check returns expected response format."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        # Core fields always present
        assert "status" in data
        assert "service" in data
        assert "timestamp" in data
        assert data["service"] == "forge-harness-webhooks"
        # Status should be one of the valid values
        assert data["status"] in ["ok", "degraded"]


class TestApiHealth:
    """Tests for /api/health endpoint (alias)."""

    def test_api_health_alias(self, client):
        """Test /api/health returns same format as /health."""
        response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["service"] == "forge-harness-webhooks"


class TestFullHealthCheck:
    """Tests for /health/full endpoint."""

    def test_full_health_check_response_format(self, client):
        """Test full health check returns component status structure."""
        response = client.get("/health/full")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ["ok", "degraded"]
        # May have components if imports work
        if "components" in data:
            assert isinstance(data["components"], dict)


class TestHealthMetrics:
    """Tests for /health/metrics endpoint."""

    def test_health_metrics_response_format(self, client):
        """Test health metrics endpoint returns expected format."""
        response = client.get("/health/metrics")

        assert response.status_code == 200
        data = response.json()
        # Should have timestamp or error
        assert "timestamp" in data or "error" in data
        if "components" in data:
            assert isinstance(data["components"], dict)


class TestServiceHealthCheck:
    """Tests for /health/{service_name} endpoint."""

    def test_state_store_health(self, client):
        """Test state_store service health check returns valid response."""
        response = client.get("/health/state_store")

        assert response.status_code == 200
        data = response.json()
        assert "service" in data
        assert data["service"] == "state_store"
        assert "status" in data

    def test_synchronizer_health(self, client):
        """Test synchronizer service health check returns valid response."""
        response = client.get("/health/synchronizer")

        assert response.status_code == 200
        data = response.json()
        assert "service" in data
        assert data["service"] == "synchronizer"
        assert "status" in data

    def test_redis_health(self, client):
        """Test Redis service health check returns valid response."""
        response = client.get("/health/redis")

        assert response.status_code == 200
        data = response.json()
        assert "service" in data
        assert data["service"] == "redis"
        assert "status" in data

    def test_approval_queue_health(self, client):
        """Test approval_queue service health check returns valid response."""
        response = client.get("/health/approval_queue")

        assert response.status_code == 200
        data = response.json()
        assert "service" in data
        assert data["service"] == "approval_queue"
        assert "status" in data

    def test_unknown_service(self, client):
        """Test unknown service returns error."""
        response = client.get("/health/unknown_service")

        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert "Unknown service" in data["error"]


class TestApiMetrics:
    """Tests for /api/metrics endpoint."""

    def test_api_metrics_response_format(self, client):
        """Test API metrics endpoint returns expected format."""
        response = client.get("/api/metrics")

        assert response.status_code == 200
        data = response.json()
        # Should have timestamp and version, or error
        if "error" not in data:
            assert "timestamp" in data
            assert "api_version" in data
            assert data["api_version"] == "1.0.0"


class TestApiVersion:
    """Tests for /api/version endpoint."""

    def test_api_version(self, client):
        """Test API version endpoint."""
        response = client.get("/api/version")

        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "1.0.0"
        assert data["name"] == "forge-harness-webhooks"
        assert data["api_prefix"] == "/api"
