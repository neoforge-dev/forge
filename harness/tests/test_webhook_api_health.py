"""Tests for forge_harness/webhook_server/api/health.py"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestHealthCheck:
    """Tests for /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_check_returns_service_info(self):
        """Test basic health check returns service info and timestamp."""
        from forge_harness.webhook_server.api.health import health_check

        # Mock both imports that health_check does internally
        mock_store = Mock()
        mock_store.is_connected.return_value = True
        mock_store.get_store_type.return_value = "sqlite"

        mock_sync = Mock()
        mock_sync._running = True
        mock_sync.get_stats.return_value = {"sync_count": 5, "error_count": 0}

        with (
            patch(
                "forge_harness.state_store.get_state_store", return_value=mock_store
            ),
            patch(
                "forge_harness.state_synchronizer.get_state_synchronizer",
                return_value=mock_sync,
            ),
        ):
            response = await health_check()

        assert response["service"] == "forge-harness-webhooks"
        assert "timestamp" in response
        assert "status" in response

    @pytest.mark.asyncio
    async def test_health_check_with_connected_sqlite(self):
        """Test health check includes store info when SQLite connected."""
        from forge_harness.webhook_server.api.health import health_check

        mock_store = Mock()
        mock_store.is_connected.return_value = True
        mock_store.get_store_type.return_value = "sqlite"

        mock_sync = Mock()
        mock_sync._running = True
        mock_sync.get_stats.return_value = {"sync_count": 0, "error_count": 0}

        with (
            patch(
                "forge_harness.state_store.get_state_store", return_value=mock_store
            ),
            patch(
                "forge_harness.state_store.StateStore", return_value=mock_store
            ),
            patch(
                "forge_harness.state_synchronizer.get_state_synchronizer",
                return_value=mock_sync,
            ),
        ):
            response = await health_check()

        assert response["redis_status"] == "connected"
        assert response["store_type"] == "sqlite"
        assert response["sqlite_status"] == "connected"

    @pytest.mark.asyncio
    async def test_health_check_degraded_on_import_error(self):
        """Test health check returns degraded when imports fail."""
        from forge_harness.webhook_server.api.health import health_check

        with (
            patch(
                "forge_harness.state_store.StateStore",
                side_effect=ImportError("no module"),
            ),
            patch(
                "forge_harness.state_synchronizer.get_state_synchronizer",
                side_effect=ImportError("no module"),
            ),
        ):
            response = await health_check()

        assert response["status"] == "degraded"


class TestAPIHealthCheck:
    """Tests for /api/health endpoint."""

    @pytest.mark.asyncio
    async def test_api_health_alias(self):
        """Test that /api/health is an alias for /health."""
        from forge_harness.webhook_server.api.health import api_health_check

        with patch("forge_harness.webhook_server.api.health.health_check") as mock_health:
            mock_health.return_value = {"status": "ok"}

            response = await api_health_check()

            mock_health.assert_called_once()
            assert response["status"] == "ok"


class TestFullHealthCheck:
    """Tests for /health/full endpoint."""

    @pytest.mark.asyncio
    async def test_full_health_check_healthy(self):
        """Test full health check with all components healthy."""
        from forge_harness.webhook_server.api.health import full_health_check

        mock_store = Mock()
        mock_store.is_connected.return_value = True
        mock_store.get_store_type.return_value = "redis"

        mock_sync = Mock()
        mock_sync._running = True

        mock_queue = Mock()
        mock_tracker = Mock()

        with (
            patch(
                "forge_harness.state_store.get_state_store",
                return_value=mock_store,
            ),
            patch(
                "forge_harness.state_store.StateStore",
                return_value=mock_store,
            ),
            patch(
                "forge_harness.state_synchronizer.get_state_synchronizer",
                return_value=mock_sync,
            ),
            patch(
                "forge_harness.approval_queue.get_approval_queue",
                return_value=mock_queue,
            ),
            patch(
                "forge_harness.session_tracker.get_session_tracker",
                return_value=mock_tracker,
            ),
        ):
            response = await full_health_check()

        assert response["status"] == "ok"
        assert "components" in response
        assert response["components"]["state_store"]["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_full_health_check_degraded_on_store_error(self):
        """Test full health check returns degraded when state store fails."""
        from forge_harness.webhook_server.api.health import full_health_check

        mock_sync = Mock()
        mock_sync._running = True

        mock_queue = Mock()
        mock_tracker = Mock()

        with (
            patch(
                "forge_harness.state_store.get_state_store",
                side_effect=Exception("Connection failed"),
            ),
            patch(
                "forge_harness.state_store.StateStore",
                side_effect=Exception("Connection failed"),
            ),
            patch(
                "forge_harness.state_synchronizer.get_state_synchronizer",
                return_value=mock_sync,
            ),
            patch(
                "forge_harness.approval_queue.get_approval_queue",
                return_value=mock_queue,
            ),
            patch(
                "forge_harness.session_tracker.get_session_tracker",
                return_value=mock_tracker,
            ),
        ):
            response = await full_health_check()

        assert response["status"] == "degraded"
        assert response["components"]["state_store"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_full_health_check_component_errors(self):
        """Test full health check when components raise errors."""
        from forge_harness.webhook_server.api.health import full_health_check

        with (
            patch(
                "forge_harness.state_store.get_state_store",
                side_effect=Exception("connection failed"),
            ),
            patch(
                "forge_harness.state_store.StateStore",
                return_value=Mock(),
            ),
            patch(
                "forge_harness.state_synchronizer.get_state_synchronizer",
                side_effect=Exception("not available"),
            ),
            patch(
                "forge_harness.approval_queue.get_approval_queue",
                side_effect=Exception("not available"),
            ),
            patch(
                "forge_harness.session_tracker.get_session_tracker",
                side_effect=Exception("not available"),
            ),
        ):
            response = await full_health_check()

        assert response["status"] == "degraded"
        assert response["components"]["state_store"]["status"] == "error"


class TestHealthMetrics:
    """Tests for /health/metrics endpoint."""

    @pytest.mark.asyncio
    async def test_health_metrics_success(self):
        """Test getting health metrics."""
        from forge_harness.webhook_server.api.health import health_metrics

        mock_store = Mock()
        mock_store.is_connected.return_value = True
        mock_store.get_store_type.return_value = "redis"

        mock_sync = Mock()
        mock_sync._running = True
        mock_sync.get_stats.return_value = {"sync_count": 5, "error_count": 0}

        with (
            patch(
                "forge_harness.state_store.get_state_store",
                return_value=mock_store,
            ),
            patch(
                "forge_harness.state_synchronizer.get_state_synchronizer",
                return_value=mock_sync,
            ),
        ):
            response = await health_metrics()

        assert "timestamp" in response
        assert "components" in response
        assert response["components"]["state_store"]["connected"] is True

    @pytest.mark.asyncio
    async def test_health_metrics_store_error(self):
        """Test metrics when state store is unavailable."""
        from forge_harness.webhook_server.api.health import health_metrics

        with (
            patch(
                "forge_harness.state_store.get_state_store",
                side_effect=Exception("unavailable"),
            ),
            patch(
                "forge_harness.state_synchronizer.get_state_synchronizer",
                side_effect=Exception("unavailable"),
            ),
        ):
            response = await health_metrics()

        assert "timestamp" in response
        assert response["components"]["state_store"]["connected"] is False


class TestServiceHealthCheck:
    """Tests for /health/{service_name} endpoint."""

    @pytest.mark.asyncio
    async def test_check_state_store_healthy(self):
        """Test state store health check when healthy."""
        from forge_harness.webhook_server.api.health import service_health_check

        mock_store = Mock()
        mock_store.is_connected.return_value = True
        mock_store.get_store_type.return_value = "sqlite"

        with patch(
            "forge_harness.state_store.get_state_store",
            return_value=mock_store,
        ):
            response = await service_health_check("state_store")

        assert response["service"] == "state_store"
        assert response["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_check_state_store_unhealthy(self):
        """Test state store health check when unhealthy."""
        from forge_harness.webhook_server.api.health import service_health_check

        mock_store = Mock()
        mock_store.is_connected.return_value = False

        with patch(
            "forge_harness.state_store.get_state_store",
            return_value=mock_store,
        ):
            response = await service_health_check("state_store")

        assert response["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_check_synchronizer_healthy(self):
        """Test synchronizer health check when healthy."""
        from forge_harness.webhook_server.api.health import service_health_check

        mock_sync = Mock()
        mock_sync._running = True

        with patch(
            "forge_harness.state_synchronizer.get_state_synchronizer",
            return_value=mock_sync,
        ):
            response = await service_health_check("synchronizer")

        assert response["service"] == "synchronizer"
        assert response["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_check_synchronizer_degraded(self):
        """Test synchronizer health check when not running."""
        from forge_harness.webhook_server.api.health import service_health_check

        mock_sync = Mock()
        mock_sync._running = False

        with patch(
            "forge_harness.state_synchronizer.get_state_synchronizer",
            return_value=mock_sync,
        ):
            response = await service_health_check("synchronizer")

        assert response["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_check_redis_healthy(self):
        """Test Redis health check when healthy."""
        from forge_harness.webhook_server.api.health import service_health_check

        mock_store = Mock()
        mock_store.is_connected.return_value = True
        mock_store.get_store_type.return_value = "redis"

        with patch(
            "forge_harness.state_store.get_state_store",
            return_value=mock_store,
        ):
            response = await service_health_check("redis")

        assert response["service"] == "redis"
        assert response["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_check_approval_queue_healthy(self):
        """Test approval queue health check when healthy."""
        from forge_harness.webhook_server.api.health import service_health_check

        with patch(
            "forge_harness.approval_queue.get_approval_queue",
            return_value=Mock(),
        ):
            response = await service_health_check("approval_queue")

        assert response["service"] == "approval_queue"
        assert response["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_check_unknown_service(self):
        """Test health check for unknown service."""
        from forge_harness.webhook_server.api.health import service_health_check

        response = await service_health_check("unknown_service")

        assert "error" in response
        assert "Unknown service" in response["error"]


class TestCheckHelperFunctions:
    """Tests for helper check functions."""

    def test_check_state_store_helper(self):
        """Test _check_state_store helper function."""
        from forge_harness.webhook_server.api.health import _check_state_store

        mock_store = Mock()
        mock_store.is_connected.return_value = True
        mock_store.get_store_type.return_value = "redis"

        with patch(
            "forge_harness.state_store.get_state_store",
            return_value=mock_store,
        ):
            result = _check_state_store()

        assert result["status"] == "healthy"
        assert result["type"] == "redis"

    def test_check_state_store_error(self):
        """Test _check_state_store with error."""
        from forge_harness.webhook_server.api.health import _check_state_store

        with patch(
            "forge_harness.state_store.get_state_store",
            side_effect=Exception("Error"),
        ):
            result = _check_state_store()

        assert result["status"] == "error"
        assert "error" in result

    def test_check_synchronizer_helper(self):
        """Test _check_synchronizer helper function."""
        from forge_harness.webhook_server.api.health import _check_synchronizer

        mock_sync = Mock()
        mock_sync._running = True

        with patch(
            "forge_harness.state_synchronizer.get_state_synchronizer",
            return_value=mock_sync,
        ):
            result = _check_synchronizer()

        assert result["status"] == "healthy"
        assert result["running"] is True

    def test_check_redis_helper(self):
        """Test _check_redis helper function."""
        from forge_harness.webhook_server.api.health import _check_redis

        mock_store = Mock()
        mock_store.is_connected.return_value = True
        mock_store.get_store_type.return_value = "redis"

        with patch(
            "forge_harness.state_store.get_state_store",
            return_value=mock_store,
        ):
            result = _check_redis()

        assert result["status"] == "healthy"

    def test_check_approval_queue_helper(self):
        """Test _check_approval_queue helper function."""
        from forge_harness.webhook_server.api.health import _check_approval_queue

        with patch(
            "forge_harness.approval_queue.get_approval_queue",
            return_value=Mock(),
        ):
            result = _check_approval_queue()

        assert result["status"] == "healthy"


class TestAPIMetrics:
    """Tests for /api/metrics endpoint."""

    @pytest.mark.asyncio
    async def test_api_metrics_success(self):
        """Test getting API metrics."""
        from forge_harness.webhook_server.api.health import api_metrics

        mock_store = Mock()
        mock_store.is_connected.return_value = True
        mock_store.get_store_type.return_value = "redis"

        with patch(
            "forge_harness.state_store.get_state_store",
            return_value=mock_store,
        ):
            response = await api_metrics()

        assert "timestamp" in response
        assert "api_version" in response
        assert response["state_store"]["connected"] is True

    @pytest.mark.asyncio
    async def test_api_metrics_store_error(self):
        """Test API metrics when state store is unavailable."""
        from forge_harness.webhook_server.api.health import api_metrics

        with patch(
            "forge_harness.state_store.get_state_store",
            side_effect=Exception("unavailable"),
        ):
            response = await api_metrics()

        assert "timestamp" in response
        assert response["state_store"]["connected"] is False


class TestAPIVersion:
    """Tests for /api/version endpoint."""

    @pytest.mark.asyncio
    async def test_api_version(self):
        """Test getting API version information."""
        from forge_harness.webhook_server.api.health import api_version

        response = await api_version()

        assert response["version"] == "1.0.0"
        assert response["name"] == "forge-harness-webhooks"
        assert response["api_prefix"] == "/api"


class TestHealthResponse:
    """Tests for HealthResponse model."""

    def test_health_response_model(self):
        """Test HealthResponse model creation."""
        from forge_harness.webhook_server.api.health import HealthResponse

        response = HealthResponse(
            status="ok",
            service="test-service",
            timestamp="2026-02-16T12:00:00Z",
            redis_status="connected",
        )

        assert response.status == "ok"
        assert response.service == "test-service"
        assert response.redis_status == "connected"
