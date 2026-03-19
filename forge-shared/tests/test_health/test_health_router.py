"""
Tests for health check module.

Test coverage for health router, health checks, and status reporting.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge_shared.health import create_health_router, HealthCheck, HealthStatus


class TestHealthRouter:
    """
    Tests for health check router.

    Tests health endpoint creation and response format.
    """

    def test_create_health_router(self):
        """
        Test health router creation.
        """
        router = create_health_router(
            name="test-service",
            version="1.0.0",
        )
        assert router is not None

    def test_health_endpoint_basic(self):
        """
        Test basic health endpoint returns 200.
        """
        app = FastAPI()
        router = create_health_router(
            name="test-service",
            version="1.0.0",
        )
        app.include_router(router)

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["name"] == "test-service"
        assert data["version"] == "1.0.0"

    def test_health_endpoint_with_prefix(self):
        """
        Test health endpoint with API prefix.
        """
        app = FastAPI()
        router = create_health_router(
            name="test-service",
            version="1.0.0",
        )
        app.include_router(router, prefix="/api/v1")

        client = TestClient(app)
        response = client.get("/api/v1/health")

        assert response.status_code == 200


class TestHealthStatus:
    """
    Tests for HealthStatus enum.

    Tests health status values.
    """

    def test_health_status_values(self):
        """
        Test HealthStatus enum values.
        """
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.UNHEALTHY.value == "unhealthy"
        assert HealthStatus.DEGRADED.value == "degraded"


class TestHealthCheck:
    """
    Tests for HealthCheck configuration.

    Tests health check creation with check functions.
    """

    def test_health_check_creation(self):
        """
        Test HealthCheck creation with check function.
        """
        def check_fn():
            return (True, 5.0, "Connected")

        check = HealthCheck(
            name="database",
            check_fn=check_fn,
        )

        assert check.name == "database"
        assert check.check_fn is not None
        ok, latency, message = check.check_fn()
        assert ok is True
        assert message == "Connected"

    def test_health_check_with_failure(self):
        """
        Test HealthCheck with failing check function.
        """
        def check_fn():
            return (False, None, "Connection failed")

        check = HealthCheck(
            name="redis",
            check_fn=check_fn,
        )

        ok, latency, message = check.check_fn()
        assert ok is False
        assert message == "Connection failed"
