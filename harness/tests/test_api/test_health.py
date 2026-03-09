"""Tests for /api/health and /health endpoints.

Tests cover:
- GET /health - Basic health check
- GET /api/health - API health check (alias for /health)
- GET /health/full - Detailed health check
- GET /health/metrics - Prometheus-compatible metrics
- GET /health/{service_name} - Specific service health
- GET /api/metrics - API metrics
- GET /api/version - API version info
"""

from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

# =============================================================================
# Module-level fixtures (accessible by all test classes)
# =============================================================================


@pytest.fixture
def mock_state_store():
    """Create a mock state store."""
    store = Mock()
    store.is_connected = Mock(return_value=True)
    store.get_store_type = Mock(return_value="redis")
    return store


@pytest.fixture
def mock_synchronizer():
    """Create a mock state synchronizer."""
    sync = Mock()
    sync._running = True
    sync.get_stats = Mock(
        return_value={
            "sync_count": 100,
            "error_count": 2,
        }
    )
    return sync


@pytest.fixture
def mock_approval_queue():
    """Create a mock approval queue."""
    queue = Mock()
    return queue


@pytest.fixture
def mock_session_tracker():
    """Create a mock session tracker."""
    tracker = Mock()
    return tracker


class TestHealthAPI:
    async def test_health_check_basic(self, mock_state_store, mock_synchronizer):
        """Test basic health check endpoint."""
        from forge_harness.webhook_server.api.health import health_check

        with (
            patch("forge_harness.webhook_server.api.health.get_state_store") as mock_store,
            patch("forge_harness.webhook_server.api.health.get_state_synchronizer") as mock_sync,
        ):
            mock_store.return_value = mock_state_store
            mock_sync.return_value = mock_synchronizer

            result = await health_check()

            assert "status" in result
            assert result["status"] in ["ok", "degraded"]
            assert "service" in result
            assert "timestamp" in result

    async def test_health_check_redis_connected(self, mock_state_store, mock_synchronizer):
        """Test health check when Redis is connected."""
        mock_state_store.is_connected = Mock(return_value=True)
        mock_state_store.get_store_type = Mock(return_value="redis")

        from forge_harness.webhook_server.api.health import health_check

        with (
            patch("forge_harness.webhook_server.api.health.get_state_store") as mock_store,
            patch("forge_harness.webhook_server.api.health.get_state_synchronizer") as mock_sync,
        ):
            mock_store.return_value = mock_state_store
            mock_sync.return_value = mock_synchronizer

            result = await health_check()

            assert result["redis_status"] == "connected"
            assert result["store_type"] == "redis"

    async def test_health_check_redis_disconnected(self):
        """Test health check when Redis is disconnected."""
        mock_state_store = Mock()
        mock_state_store.is_connected = Mock(return_value=False)
        mock_state_store.get_store_type = Mock(return_value="sqlite")

        mock_synchronizer = Mock()
        mock_synchronizer._running = False
        mock_synchronizer.get_stats = Mock(return_value={})

        from forge_harness.webhook_server.api.health import health_check

        with (
            patch("forge_harness.webhook_server.api.health.get_state_store") as mock_store,
            patch("forge_harness.webhook_server.api.health.get_state_synchronizer") as mock_sync,
        ):
            mock_store.return_value = mock_state_store
            mock_sync.return_value = mock_synchronizer

            result = await health_check()

            assert result["status"] == "degraded"
            assert result["redis_status"] == "disconnected"

    async def test_api_health_alias(self, mock_state_store, mock_synchronizer):
        """Test /api/health is alias for /health."""
        from forge_harness.webhook_server.api.health import api_health_check, health_check

        with (
            patch("forge_harness.webhook_server.api.health.get_state_store") as mock_store,
            patch("forge_harness.webhook_server.api.health.get_state_synchronizer") as mock_sync,
        ):
            mock_store.return_value = mock_state_store
            mock_sync.return_value = mock_synchronizer

            health_result = await health_check()
            api_result = await api_health_check()

            # Both should return similar structure
            assert health_result["status"] == api_result["status"]
            assert health_result["service"] == api_result["service"]

    async def test_full_health_check(
        self, mock_state_store, mock_synchronizer, mock_approval_queue, mock_session_tracker
    ):
        """Test full health check with all components."""
        from forge_harness.webhook_server.api.health import full_health_check

        with (
            patch("forge_harness.webhook_server.api.health.get_state_store") as mock_store,
            patch("forge_harness.webhook_server.api.health.get_state_synchronizer") as mock_sync,
            patch("forge_harness.webhook_server.api.health.get_approval_queue") as mock_queue,
            patch("forge_harness.webhook_server.api.health.get_session_tracker") as mock_tracker,
        ):
            mock_store.return_value = mock_state_store
            mock_sync.return_value = mock_synchronizer
            mock_queue.return_value = mock_approval_queue
            mock_tracker.return_value = mock_session_tracker

            result = await full_health_check()

            assert "status" in result
            assert "components" in result
            assert "state_store" in result["components"]
            assert "synchronizer" in result["components"]
            assert "approval_queue" in result["components"]
            assert "session_tracker" in result["components"]

    async def test_full_health_check_degraded(self):
        """Test full health check with degraded components."""
        mock_state_store = Mock()
        mock_state_store.is_connected = Mock(return_value=False)

        from forge_harness.webhook_server.api.health import full_health_check

        with patch("forge_harness.webhook_server.api.health.get_state_store") as mock_store:
            mock_store.return_value = mock_state_store

            result = await full_health_check()

            assert result["status"] == "degraded"
            assert result["components"]["state_store"]["status"] == "degraded"

    async def test_health_metrics(self, mock_state_store, mock_synchronizer):
        """Test health metrics endpoint."""
        from forge_harness.webhook_server.api.health import health_metrics

        with (
            patch("forge_harness.webhook_server.api.health.get_state_store") as mock_store,
            patch("forge_harness.webhook_server.api.health.get_state_synchronizer") as mock_sync,
        ):
            mock_store.return_value = mock_state_store
            mock_sync.return_value = mock_synchronizer

            result = await health_metrics()

            assert "timestamp" in result
            assert "components" in result

    def test_service_health_check_state_store(self, mock_state_store):
        """Test specific service health check for state_store."""
        from forge_harness.webhook_server.api.health import service_health_check

        with patch("forge_harness.webhook_server.api.health._check_state_store") as mock_check:
            mock_check.return_value = {
                "service": "state_store",
                "status": "healthy",
                "type": "redis",
            }

            result = service_health_check("state_store")

            assert result["service"] == "state_store"
            assert result["status"] == "healthy"

    def test_service_health_check_synchronizer(self, mock_synchronizer):
        """Test specific service health check for synchronizer."""
        from forge_harness.webhook_server.api.health import service_health_check

        with patch("forge_harness.webhook_server.api.health._check_synchronizer") as mock_check:
            mock_check.return_value = {
                "service": "synchronizer",
                "status": "healthy",
                "running": True,
            }

            result = service_health_check("synchronizer")

            assert result["service"] == "synchronizer"
            assert result["status"] == "healthy"

    def test_service_health_check_redis(self, mock_state_store):
        """Test specific service health check for redis."""
        from forge_harness.webhook_server.api.health import service_health_check

        with patch("forge_harness.webhook_server.api.health._check_redis") as mock_check:
            mock_check.return_value = {"service": "redis", "status": "healthy"}

            result = service_health_check("redis")

            assert result["service"] == "redis"

    def test_service_health_check_unknown(self):
        """Test specific service health check for unknown service."""
        from forge_harness.webhook_server.api.health import service_health_check

        result = service_health_check("unknown_service")

        assert "error" in result
        assert "Unknown service" in result["error"]

    async def test_api_metrics(self, mock_state_store):
        """Test API metrics endpoint."""
        from forge_harness.webhook_server.api.health import api_metrics

        with patch("forge_harness.webhook_server.api.health.get_state_store") as mock_store:
            mock_store.return_value = mock_state_store

            result = await api_metrics()

            assert "timestamp" in result
            assert "api_version" in result
            assert "state_store" in result

    def test_api_version(self):
        """Test API version endpoint."""
        from forge_harness.webhook_server.api.health import api_version

        result = api_version()

        assert "version" in result
        assert "name" in result
        assert "api_prefix" in result
        assert result["api_prefix"] == "/api"


class TestHealthAPIHelpers:
    """Test helper functions for health API."""

    def test_check_state_store_healthy(self, mock_state_store):
        """Test _check_state_store when healthy."""
        from forge_harness.webhook_server.api.health import _check_state_store

        with patch("forge_harness.state_store.get_state_store") as mock_store:
            mock_store.return_value = mock_state_store

            result = _check_state_store()

            assert result["service"] == "state_store"
            assert result["status"] == "healthy"

    def test_check_state_store_unhealthy(self):
        """Test _check_state_store when unhealthy."""
        mock_state_store = Mock()
        mock_state_store.is_connected = Mock(return_value=False)

        from forge_harness.webhook_server.api.health import _check_state_store

        with patch("forge_harness.state_store.get_state_store") as mock_store:
            mock_store.return_value = mock_state_store

            result = _check_state_store()

            assert result["status"] == "unhealthy"

    def test_check_synchronizer_running(self, mock_synchronizer):
        """Test _check_synchronizer when running."""
        from forge_harness.webhook_server.api.health import _check_synchronizer

        with patch("forge_harness.state_synchronizer.get_state_synchronizer") as mock_sync:
            mock_sync.return_value = mock_synchronizer

            result = _check_synchronizer()

            assert result["service"] == "synchronizer"
            assert result["status"] == "healthy"
            assert result["running"] is True

    def test_check_synchronizer_not_initialized(self):
        """Test _check_synchronizer when not initialized."""
        from forge_harness.webhook_server.api.health import _check_synchronizer

        with patch("forge_harness.state_synchronizer.get_state_synchronizer") as mock_sync:
            mock_sync.return_value = None

            result = _check_synchronizer()

            assert result["status"] == "not_initialized"

    def test_check_redis_healthy(self, mock_state_store):
        """Test _check_redis when healthy."""
        from forge_harness.webhook_server.api.health import _check_redis

        with patch("forge_harness.state_store.get_state_store") as mock_store:
            mock_store.return_value = mock_state_store

            result = _check_redis()

            assert result["service"] == "redis"
            assert result["status"] == "healthy"

    def test_check_redis_unhealthy(self):
        """Test _check_redis when unhealthy."""
        mock_state_store = Mock()
        mock_state_store.is_connected = Mock(return_value=False)

        from forge_harness.webhook_server.api.health import _check_redis

        with patch("forge_harness.state_store.get_state_store") as mock_store:
            mock_store.return_value = mock_state_store

            result = _check_redis()

            assert result["status"] == "unhealthy"

    def test_check_approval_queue_healthy(self, mock_approval_queue):
        """Test _check_approval_queue when healthy."""
        from forge_harness.webhook_server.api.health import _check_approval_queue

        with patch("forge_harness.approval_queue.get_approval_queue") as mock_queue:
            mock_queue.return_value = mock_approval_queue

            result = _check_approval_queue()

            assert result["service"] == "approval_queue"
            assert result["status"] == "healthy"

    def test_check_approval_queue_not_initialized(self):
        """Test _check_approval_queue when not initialized."""
        from forge_harness.webhook_server.api.health import _check_approval_queue

        with patch("forge_harness.approval_queue.get_approval_queue") as mock_queue:
            mock_queue.return_value = None

            result = _check_approval_queue()

            assert result["status"] == "not_initialized"


class TestHealthResponseModel:
    """Test Pydantic models for health API."""

    def test_health_response_model(self):
        """Test HealthResponse model."""
        from forge_harness.webhook_server.api.health import HealthResponse

        response = HealthResponse(
            status="ok",
            service="forge-harness-webhooks",
            timestamp=datetime.now(UTC).isoformat(),
            redis_status="connected",
            store_type="redis",
        )

        assert response.status == "ok"
        assert response.service == "forge-harness-webhooks"
        assert response.redis_status == "connected"
        assert response.store_type == "redis"

    def test_health_response_optional_fields(self):
        """Test HealthResponse with optional fields."""
        from forge_harness.webhook_server.api.health import HealthResponse

        response = HealthResponse(
            status="degraded",
            service="test",
            timestamp="2024-01-01T00:00:00Z",
            redis_status="disconnected",
            synchronizer_status="stopped",
            synchronizer_sync_count=0,
            synchronizer_errors=5,
        )

        assert response.synchronizer_status == "stopped"
        assert response.synchronizer_sync_count == 0
        assert response.synchronizer_errors == 5
