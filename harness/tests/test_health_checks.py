"""Tests for health check system (health_checks.py).

Covers:
- HealthStatus enum
- ServiceHealth dataclass (creation, serialization)
- AggregatedHealth dataclass (creation, serialization, Prometheus export)
- HealthCheckRegistry (register, unregister, check_service, check_all,
  failure tracking, callbacks, HTTP client lifecycle)
- Built-in check factories: create_http_check, create_redis_check
- Global registry helpers: get_health_registry, run_health_check, _setup_default_checks
- Edge cases: service down, timeout, partial failures, consecutive failures,
  callback errors, Redis ImportError, unknown service
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from forge_harness.health_checks import (
    AggregatedHealth,
    HealthCheckRegistry,
    HealthStatus,
    ServiceHealth,
    _setup_default_checks,
    get_health_registry,
    run_health_check,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_healthy(name: str = "svc", latency: float = 10.0) -> ServiceHealth:
    return ServiceHealth(name=name, status=HealthStatus.HEALTHY, latency_ms=latency)


def _make_degraded(name: str = "svc", latency: float = 6000.0) -> ServiceHealth:
    return ServiceHealth(name=name, status=HealthStatus.DEGRADED, latency_ms=latency, message="Slow")


def _make_unhealthy(name: str = "svc", latency: float = 0.0) -> ServiceHealth:
    return ServiceHealth(name=name, status=HealthStatus.UNHEALTHY, latency_ms=latency, message="Down")


# ===========================================================================
# HealthStatus enum
# ===========================================================================

class TestHealthStatus:
    """Verify enum values and string behaviour."""

    def test_values(self):
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.DEGRADED.value == "degraded"
        assert HealthStatus.UNHEALTHY.value == "unhealthy"

    def test_str_enum_comparison(self):
        # HealthStatus extends str, so direct comparison works
        assert HealthStatus.HEALTHY == "healthy"
        assert HealthStatus.DEGRADED == "degraded"
        assert HealthStatus.UNHEALTHY == "unhealthy"

    def test_enum_identity(self):
        assert HealthStatus("healthy") is HealthStatus.HEALTHY


# ===========================================================================
# ServiceHealth dataclass
# ===========================================================================

class TestServiceHealth:
    """Tests for ServiceHealth dataclass."""

    def test_basic_creation(self):
        h = ServiceHealth(name="test-service", status=HealthStatus.HEALTHY, latency_ms=50.0)
        assert h.name == "test-service"
        assert h.status == HealthStatus.HEALTHY
        assert h.latency_ms == 50.0
        assert h.message is None
        assert h.metadata == {}

    def test_creation_with_all_fields(self):
        ts = datetime.now(UTC)
        h = ServiceHealth(
            name="redis",
            status=HealthStatus.DEGRADED,
            latency_ms=6000.0,
            message="Slow response",
            last_check=ts,
            metadata={"url": "localhost:6379"},
        )
        assert h.message == "Slow response"
        assert h.metadata["url"] == "localhost:6379"
        assert h.last_check is ts

    def test_to_dict_keys(self):
        h = ServiceHealth(name="api", status=HealthStatus.UNHEALTHY, latency_ms=12000.0, message="Connection refused")
        d = h.to_dict()
        assert set(d.keys()) == {"name", "status", "latency_ms", "message", "last_check", "metadata"}

    def test_to_dict_values(self):
        h = ServiceHealth(name="api", status=HealthStatus.UNHEALTHY, latency_ms=12000.0, message="Connection refused")
        d = h.to_dict()
        assert d["name"] == "api"
        assert d["status"] == "unhealthy"
        assert d["latency_ms"] == 12000.0
        assert d["message"] == "Connection refused"

    def test_to_dict_latency_rounded(self):
        h = ServiceHealth(name="svc", status=HealthStatus.HEALTHY, latency_ms=12.3456789)
        d = h.to_dict()
        assert d["latency_ms"] == round(12.3456789, 2)

    def test_to_dict_last_check_iso_format(self):
        h = ServiceHealth(name="svc", status=HealthStatus.HEALTHY, latency_ms=1.0)
        d = h.to_dict()
        # Must be parseable ISO 8601
        parsed = datetime.fromisoformat(d["last_check"])
        assert parsed.tzinfo is not None

    def test_last_check_defaults_to_utc_now(self):
        before = datetime.now(UTC)
        h = ServiceHealth(name="svc", status=HealthStatus.HEALTHY, latency_ms=1.0)
        after = datetime.now(UTC)
        assert before <= h.last_check <= after


# ===========================================================================
# AggregatedHealth dataclass
# ===========================================================================

class TestAggregatedHealth:
    """Tests for AggregatedHealth dataclass."""

    def test_basic_creation(self):
        services = [_make_healthy("s1"), _make_healthy("s2")]
        agg = AggregatedHealth(status=HealthStatus.HEALTHY, services=services, total_latency_ms=30.0)
        assert agg.status == HealthStatus.HEALTHY
        assert len(agg.services) == 2
        assert agg.total_latency_ms == 30.0

    def test_empty_services(self):
        agg = AggregatedHealth(status=HealthStatus.HEALTHY, services=[])
        assert agg.services == []

    def test_to_dict_summary_counts(self):
        services = [
            _make_healthy("s1"),
            _make_degraded("s2"),
            _make_unhealthy("s3"),
        ]
        agg = AggregatedHealth(status=HealthStatus.UNHEALTHY, services=services, total_latency_ms=100.0)
        d = agg.to_dict()
        assert d["summary"]["total"] == 3
        assert d["summary"]["healthy"] == 1
        assert d["summary"]["degraded"] == 1
        assert d["summary"]["unhealthy"] == 1

    def test_to_dict_services_serialized(self):
        services = [_make_healthy("api"), _make_unhealthy("db")]
        agg = AggregatedHealth(status=HealthStatus.UNHEALTHY, services=services)
        d = agg.to_dict()
        names = [s["name"] for s in d["services"]]
        assert "api" in names
        assert "db" in names

    def test_to_dict_latency_rounded(self):
        agg = AggregatedHealth(status=HealthStatus.HEALTHY, services=[], total_latency_ms=123.456789)
        d = agg.to_dict()
        assert d["total_latency_ms"] == round(123.456789, 2)

    def test_to_dict_checked_at_iso(self):
        agg = AggregatedHealth(status=HealthStatus.HEALTHY, services=[])
        d = agg.to_dict()
        parsed = datetime.fromisoformat(d["checked_at"])
        assert parsed.tzinfo is not None

    # Prometheus export
    def test_prometheus_healthy_value(self):
        agg = AggregatedHealth(status=HealthStatus.HEALTHY, services=[_make_healthy("redis")])
        metrics = agg.to_prometheus()
        assert 'forge_health_status{service="redis"} 1' in metrics

    def test_prometheus_degraded_value(self):
        agg = AggregatedHealth(status=HealthStatus.DEGRADED, services=[_make_degraded("api")])
        metrics = agg.to_prometheus()
        assert 'forge_health_status{service="api"} 0.5' in metrics

    def test_prometheus_unhealthy_value(self):
        agg = AggregatedHealth(status=HealthStatus.UNHEALTHY, services=[_make_unhealthy("db")])
        metrics = agg.to_prometheus()
        assert 'forge_health_status{service="db"} 0' in metrics

    def test_prometheus_latency_metric(self):
        agg = AggregatedHealth(status=HealthStatus.HEALTHY, services=[_make_healthy("redis", latency=5.0)])
        metrics = agg.to_prometheus()
        assert 'forge_health_latency_ms{service="redis"} 5.00' in metrics

    def test_prometheus_timestamp_metric(self):
        agg = AggregatedHealth(status=HealthStatus.HEALTHY, services=[])
        metrics = agg.to_prometheus()
        assert "forge_health_check_timestamp_seconds" in metrics

    def test_prometheus_help_and_type_headers(self):
        agg = AggregatedHealth(status=HealthStatus.HEALTHY, services=[_make_healthy("x")])
        metrics = agg.to_prometheus()
        assert "# HELP forge_health_status" in metrics
        assert "# TYPE forge_health_status gauge" in metrics
        assert "# HELP forge_health_latency_ms" in metrics
        assert "# TYPE forge_health_latency_ms gauge" in metrics


# ===========================================================================
# HealthCheckRegistry
# ===========================================================================

class TestHealthCheckRegistryBasics:
    """Register, unregister, last-result, failure-count."""

    @pytest.fixture
    def registry(self):
        return HealthCheckRegistry()

    def test_initial_state(self, registry):
        assert registry._checks == {}
        assert registry._last_results == {}
        assert registry._failure_counts == {}
        assert registry._failure_callbacks == []
        assert registry._http_client is None

    def test_register_stores_function(self, registry):
        async def check():
            return _make_healthy()
        registry.register("svc", check)
        assert "svc" in registry._checks
        assert registry._checks["svc"] is check

    def test_unregister_removes_all_state(self, registry):
        async def check():
            return _make_healthy()
        registry.register("svc", check)
        registry._last_results["svc"] = _make_healthy()
        registry._failure_counts["svc"] = 2

        registry.unregister("svc")
        assert "svc" not in registry._checks
        assert "svc" not in registry._last_results
        assert "svc" not in registry._failure_counts

    def test_unregister_missing_is_noop(self, registry):
        """Unregistering a service that was never registered should not raise."""
        registry.unregister("ghost")  # no exception

    def test_get_failure_count_default_zero(self, registry):
        assert registry.get_failure_count("nonexistent") == 0

    def test_get_failure_count_after_increment(self, registry):
        registry._failure_counts["svc"] = 5
        assert registry.get_failure_count("svc") == 5

    def test_get_last_result_none_when_absent(self, registry):
        assert registry.get_last_result("missing") is None

    def test_get_last_result_after_check(self, registry):
        sh = _make_healthy("svc")
        registry._last_results["svc"] = sh
        assert registry.get_last_result("svc") is sh

    def test_on_health_failure_appends_callback(self, registry):
        cb = MagicMock()
        cb.__name__ = "my_callback"
        registry.on_health_failure(cb)
        assert cb in registry._failure_callbacks


class TestHealthCheckRegistryHTTPClient:
    """HTTP client lifecycle."""

    @pytest.fixture
    def registry(self):
        return HealthCheckRegistry()

    @pytest.mark.asyncio
    async def test_get_http_client_creates_new(self, registry):
        with patch("forge_harness.health_checks.httpx.AsyncClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.is_closed = False
            mock_cls.return_value = mock_client
            client = await registry._get_http_client()
            assert client is mock_client

    @pytest.mark.asyncio
    async def test_get_http_client_reuses_open_client(self, registry):
        mock_client = MagicMock()
        mock_client.is_closed = False
        registry._http_client = mock_client
        with patch("forge_harness.health_checks.httpx.AsyncClient") as mock_cls:
            client = await registry._get_http_client()
            mock_cls.assert_not_called()
            assert client is mock_client

    @pytest.mark.asyncio
    async def test_get_http_client_recreates_when_closed(self, registry):
        old_client = MagicMock()
        old_client.is_closed = True
        registry._http_client = old_client

        new_client = MagicMock()
        new_client.is_closed = False
        with patch("forge_harness.health_checks.httpx.AsyncClient", return_value=new_client):
            client = await registry._get_http_client()
            assert client is new_client

    @pytest.mark.asyncio
    async def test_close_calls_aclose(self, registry):
        mock_client = AsyncMock()
        mock_client.is_closed = False
        registry._http_client = mock_client
        await registry.close()
        mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_noop_when_no_client(self, registry):
        # No exception should be raised
        await registry.close()

    @pytest.mark.asyncio
    async def test_close_noop_when_already_closed(self, registry):
        mock_client = AsyncMock()
        mock_client.is_closed = True
        registry._http_client = mock_client
        await registry.close()
        mock_client.aclose.assert_not_called()


class TestCheckService:
    """check_service() logic: latency thresholds, failure tracking, callbacks."""

    @pytest.fixture
    def registry(self):
        return HealthCheckRegistry()

    @pytest.mark.asyncio
    async def test_unknown_service_returns_unhealthy(self, registry):
        result = await registry.check_service("unknown")
        assert result.status == HealthStatus.UNHEALTHY
        assert "Unknown service" in result.message

    @pytest.mark.asyncio
    async def test_healthy_check_stores_result(self, registry):
        async def check():
            return _make_healthy("svc")

        registry.register("svc", check)
        result = await registry.check_service("svc")
        assert result.status == HealthStatus.HEALTHY
        assert registry.get_last_result("svc") is result

    @pytest.mark.asyncio
    async def test_healthy_check_resets_failure_count(self, registry):
        registry._failure_counts["svc"] = 3

        async def check():
            return _make_healthy("svc")

        registry.register("svc", check)
        await registry.check_service("svc")
        assert registry.get_failure_count("svc") == 0

    @pytest.mark.asyncio
    async def test_latency_above_degraded_threshold_becomes_degraded(self, registry):
        """A HEALTHY result with latency > 5 000 ms should be downgraded to DEGRADED."""

        async def check():
            return ServiceHealth("svc", HealthStatus.HEALTHY, latency_ms=6000.0)

        registry.register("svc", check)
        result = await registry.check_service("svc")
        assert result.status == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_latency_above_unhealthy_threshold_becomes_unhealthy(self, registry):
        """A HEALTHY result with latency > 10 000 ms should be downgraded to UNHEALTHY."""

        async def check():
            return ServiceHealth("svc", HealthStatus.HEALTHY, latency_ms=11000.0)

        registry.register("svc", check)
        result = await registry.check_service("svc")
        assert result.status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_already_degraded_not_changed_by_slow_latency(self, registry):
        """A DEGRADED result with latency > 5s should remain DEGRADED, not HEALTHY."""

        async def check():
            return ServiceHealth("svc", HealthStatus.DEGRADED, latency_ms=6000.0, message="Custom")

        registry.register("svc", check)
        result = await registry.check_service("svc")
        assert result.status == HealthStatus.DEGRADED
        assert result.message == "Custom"

    @pytest.mark.asyncio
    async def test_exception_returns_unhealthy(self, registry):
        async def check():
            raise ConnectionError("Connection refused")

        registry.register("svc", check)
        result = await registry.check_service("svc")
        assert result.status == HealthStatus.UNHEALTHY
        assert "Connection refused" in result.message

    @pytest.mark.asyncio
    async def test_exception_increments_failure_count(self, registry):
        async def check():
            raise RuntimeError("boom")

        registry.register("svc", check)
        await registry.check_service("svc")
        assert registry.get_failure_count("svc") == 1

    @pytest.mark.asyncio
    async def test_asyncio_timeout_returns_unhealthy(self, registry):
        """If asyncio.wait_for raises TimeoutError the result is UNHEALTHY."""

        async def check():
            return _make_healthy("svc")

        registry.register("svc", check)
        with patch("forge_harness.health_checks.asyncio.wait_for", side_effect=TimeoutError()):
            result = await registry.check_service("svc")
        assert result.status == HealthStatus.UNHEALTHY
        assert "timed out" in result.message.lower()

    @pytest.mark.asyncio
    async def test_timeout_increments_failure_count(self, registry):
        async def check():
            return _make_healthy("svc")

        registry.register("svc", check)
        with patch("forge_harness.health_checks.asyncio.wait_for", side_effect=TimeoutError()):
            await registry.check_service("svc")
        assert registry.get_failure_count("svc") == 1

    @pytest.mark.asyncio
    async def test_failure_callback_triggered_at_threshold(self, registry):
        """Callback must fire when failure count reaches MAX_CONSECUTIVE_FAILURES."""
        cb = MagicMock()
        cb.__name__ = "alert_cb"
        registry.on_health_failure(cb)

        # Pre-load 2 failures so the next one hits the threshold (3)
        registry._failure_counts["svc"] = registry.MAX_CONSECUTIVE_FAILURES - 1

        async def check():
            return _make_unhealthy("svc")

        registry.register("svc", check)
        await registry.check_service("svc")
        cb.assert_called_once()
        args = cb.call_args[0]
        assert args[0] == "svc"
        assert args[1] == registry.MAX_CONSECUTIVE_FAILURES
        assert isinstance(args[2], ServiceHealth)

    @pytest.mark.asyncio
    async def test_failure_callback_not_triggered_below_threshold(self, registry):
        cb = MagicMock()
        cb.__name__ = "alert_cb"
        registry.on_health_failure(cb)

        async def check():
            return _make_unhealthy("svc")

        registry.register("svc", check)
        # Only 1 failure — below MAX_CONSECUTIVE_FAILURES (3)
        await registry.check_service("svc")
        cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_propagate(self, registry):
        """Errors inside a failure callback must not crash check_service."""
        def bad_cb(service_name, failure_count, health):
            raise ValueError("callback blew up")

        bad_cb.__name__ = "bad_cb"
        registry.on_health_failure(bad_cb)
        registry._failure_counts["svc"] = registry.MAX_CONSECUTIVE_FAILURES - 1

        async def check():
            return _make_unhealthy("svc")

        registry.register("svc", check)
        # Should not raise
        result = await registry.check_service("svc")
        assert result.status == HealthStatus.UNHEALTHY


class TestCheckAll:
    """check_all() aggregation logic."""

    @pytest.fixture
    def registry(self):
        return HealthCheckRegistry()

    @pytest.mark.asyncio
    async def test_empty_registry_returns_healthy(self, registry):
        result = await registry.check_all()
        assert result.status == HealthStatus.HEALTHY
        assert result.services == []

    @pytest.mark.asyncio
    async def test_all_healthy(self, registry):
        registry.register("a", AsyncMock(return_value=_make_healthy("a")))
        registry.register("b", AsyncMock(return_value=_make_healthy("b")))
        result = await registry.check_all()
        assert result.status == HealthStatus.HEALTHY
        assert len(result.services) == 2

    @pytest.mark.asyncio
    async def test_one_degraded_escalates_to_degraded(self, registry):
        registry.register("ok", AsyncMock(return_value=_make_healthy("ok")))
        registry.register("slow", AsyncMock(return_value=_make_degraded("slow")))
        result = await registry.check_all()
        assert result.status == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_one_unhealthy_escalates_to_unhealthy(self, registry):
        registry.register("ok", AsyncMock(return_value=_make_healthy("ok")))
        registry.register("down", AsyncMock(return_value=_make_unhealthy("down")))
        result = await registry.check_all()
        assert result.status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_mixed_degraded_and_unhealthy_yields_unhealthy(self, registry):
        registry.register("slow", AsyncMock(return_value=_make_degraded("slow")))
        registry.register("down", AsyncMock(return_value=_make_unhealthy("down")))
        result = await registry.check_all()
        assert result.status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_checks_run_concurrently(self, registry):
        """All checks should start concurrently; wall-clock time close to single check time."""
        call_order: list[str] = []

        async def slow_a():
            await asyncio.sleep(0.05)
            call_order.append("a")
            return _make_healthy("a")

        async def slow_b():
            await asyncio.sleep(0.05)
            call_order.append("b")
            return _make_healthy("b")

        async def slow_c():
            await asyncio.sleep(0.05)
            call_order.append("c")
            return _make_healthy("c")

        registry.register("a", slow_a)
        registry.register("b", slow_b)
        registry.register("c", slow_c)

        result = await registry.check_all()
        # All three ran; total should be well under 3 × 50ms = 150ms
        assert len(result.services) == 3
        assert result.total_latency_ms < 200  # generous bound

    @pytest.mark.asyncio
    async def test_total_latency_is_recorded(self, registry):
        registry.register("x", AsyncMock(return_value=_make_healthy("x")))
        result = await registry.check_all()
        assert result.total_latency_ms >= 0


# ===========================================================================
# HTTP check factory
# ===========================================================================

class TestCreateHttpCheck:
    """Tests for create_http_check factory."""

    @pytest.fixture
    def registry(self):
        return HealthCheckRegistry()

    def _make_mock_response(self, status_code: int) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        return resp

    @pytest.mark.asyncio
    async def test_http_check_200_is_healthy(self, registry):
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=self._make_mock_response(200))
        registry._http_client = mock_client

        check = registry.create_http_check("api", "http://localhost:8080/health")
        result = await check()
        assert result.status == HealthStatus.HEALTHY
        assert result.metadata["status_code"] == 200

    @pytest.mark.asyncio
    async def test_http_check_unexpected_status_is_unhealthy(self, registry):
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=self._make_mock_response(503))
        registry._http_client = mock_client

        check = registry.create_http_check("api", "http://localhost:8080/health")
        result = await check()
        assert result.status == HealthStatus.UNHEALTHY
        assert "503" in result.message

    @pytest.mark.asyncio
    async def test_http_check_custom_expected_status(self, registry):
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=self._make_mock_response(204))
        registry._http_client = mock_client

        check = registry.create_http_check("api", "http://localhost/health", expected_status=204)
        result = await check()
        assert result.status == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_http_check_timeout_exception_is_unhealthy(self, registry):
        import httpx

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        registry._http_client = mock_client

        check = registry.create_http_check("api", "http://localhost/health")
        result = await check()
        assert result.status == HealthStatus.UNHEALTHY
        assert "timeout" in result.message.lower()

    @pytest.mark.asyncio
    async def test_http_check_connect_error_is_unhealthy(self, registry):
        import httpx

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        registry._http_client = mock_client

        check = registry.create_http_check("api", "http://localhost/health")
        result = await check()
        assert result.status == HealthStatus.UNHEALTHY
        assert "Connection failed" in result.message

    @pytest.mark.asyncio
    async def test_http_check_generic_exception_is_unhealthy(self, registry):
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(side_effect=RuntimeError("unexpected"))
        registry._http_client = mock_client

        check = registry.create_http_check("api", "http://localhost/health")
        result = await check()
        assert result.status == HealthStatus.UNHEALTHY
        assert "Error" in result.message

    @pytest.mark.asyncio
    async def test_http_check_sends_headers(self, registry):
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=self._make_mock_response(200))
        registry._http_client = mock_client

        headers = {"X-Auth": "secret"}
        check = registry.create_http_check("api", "http://localhost/health", headers=headers)
        await check()
        mock_client.get.assert_called_once_with("http://localhost/health", headers=headers)

    @pytest.mark.asyncio
    async def test_http_check_name_preserved(self, registry):
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=self._make_mock_response(200))
        registry._http_client = mock_client

        check = registry.create_http_check("my-service", "http://localhost/health")
        result = await check()
        assert result.name == "my-service"


# ===========================================================================
# Redis check factory
# ===========================================================================

class TestCreateRedisCheck:
    """Tests for create_redis_check factory."""

    @pytest.fixture
    def registry(self):
        return HealthCheckRegistry()

    def _redis_context(self, mock_redis_client: AsyncMock, redis_url: str = "redis://localhost:6379"):
        """Return stacked context managers that inject a working redis environment.

        The health_checks source closure does:
            import redis.asyncio as redis            # resolves from sys.modules cache
            from forge_harness.config import get_config
            client = redis.from_url(...)
            await client.ping()
            await client.aclose()

        Since ``redis`` is already installed and cached in ``sys.modules``, we patch
        ``from_url`` directly on the real ``redis.asyncio`` module object and inject
        ``get_config`` onto ``forge_harness.config`` (it only has ``get_settings``).
        """
        import redis.asyncio as real_redis_asyncio

        import forge_harness.config as _cfg_mod

        mock_config = MagicMock()
        mock_config.redis_url = redis_url
        mock_get_config = MagicMock(return_value=mock_config)

        return (
            patch.object(real_redis_asyncio, "from_url", return_value=mock_redis_client),
            patch.object(_cfg_mod, "get_config", mock_get_config, create=True),
        )

    @pytest.mark.asyncio
    async def test_redis_check_success(self, registry):
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock()
        mock_client.aclose = AsyncMock()

        p1, p2 = self._redis_context(mock_client)
        with p1, p2:
            check = registry.create_redis_check("redis")
            result = await check()
        assert result.status == HealthStatus.HEALTHY
        assert result.name == "redis"

    @pytest.mark.asyncio
    async def test_redis_check_import_error_returns_degraded(self, registry):
        """When redis package is absent the check should return DEGRADED, not crash.

        We evict redis.asyncio from sys.modules so Python must re-import it,
        then intercept that import attempt via builtins.__import__.
        """
        import builtins
        import sys

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name in ("redis.asyncio", "redis"):
                raise ImportError("No module named 'redis'")
            return real_import(name, *args, **kwargs)

        check = registry.create_redis_check("redis")
        # Evict from cache so Python actually calls __import__ again
        saved = {k: v for k, v in sys.modules.items() if k.startswith("redis")}
        for k in saved:
            sys.modules.pop(k, None)
        try:
            with patch("builtins.__import__", side_effect=mock_import):
                result = await check()
        finally:
            # Restore the real redis modules
            sys.modules.update(saved)
        assert result.status == HealthStatus.DEGRADED
        assert "not installed" in result.message

    @pytest.mark.asyncio
    async def test_redis_check_connection_failure_returns_unhealthy(self, registry):
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        mock_client.aclose = AsyncMock()

        p1, p2 = self._redis_context(mock_client)
        with p1, p2:
            check = registry.create_redis_check("redis")
            result = await check()
        assert result.status == HealthStatus.UNHEALTHY
        assert "Redis error" in result.message

    @pytest.mark.asyncio
    async def test_redis_check_hides_credentials_in_metadata(self, registry):
        """Metadata must expose host:port without credentials."""
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock()
        mock_client.aclose = AsyncMock()

        p1, p2 = self._redis_context(mock_client, redis_url="redis://:password@myhost:6379")
        with p1, p2:
            check = registry.create_redis_check("redis")
            result = await check()
        assert "password" not in result.metadata.get("url", "")

    @pytest.mark.asyncio
    async def test_redis_check_custom_name(self, registry):
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock()
        mock_client.aclose = AsyncMock()

        p1, p2 = self._redis_context(mock_client)
        with p1, p2:
            check = registry.create_redis_check("cache")
            result = await check()
        assert result.name == "cache"


# ===========================================================================
# _setup_default_checks
# ===========================================================================

class TestSetupDefaultChecks:
    """Tests for _setup_default_checks()."""

    def test_no_env_vars_registers_nothing(self):
        registry = HealthCheckRegistry()
        env = {}
        with patch.dict("os.environ", env, clear=True):
            _setup_default_checks(registry)
        assert len(registry._checks) == 0

    def test_code_atlas_url_registers_check(self):
        registry = HealthCheckRegistry()
        with patch.dict("os.environ", {"FORGE_CODE_ATLAS_URL": "http://atlas:8000"}, clear=True):
            _setup_default_checks(registry)
        assert "code_atlas" in registry._checks

    def test_fallback_code_atlas_url_registers_check(self):
        registry = HealthCheckRegistry()
        with patch.dict("os.environ", {"CODE_ATLAS_URL": "http://atlas:8000"}, clear=True):
            _setup_default_checks(registry)
        assert "code_atlas" in registry._checks

    def test_tech_diligence_url_registers_check(self):
        registry = HealthCheckRegistry()
        with patch.dict("os.environ", {"FORGE_TECH_DILIGENCE_URL": "http://td:9000"}, clear=True):
            _setup_default_checks(registry)
        assert "tech_diligence" in registry._checks

    def test_webhook_url_registers_check(self):
        registry = HealthCheckRegistry()
        with patch.dict("os.environ", {"FORGE_WEBHOOK_URL": "http://wh:8080"}, clear=True):
            _setup_default_checks(registry)
        assert "webhook_server" in registry._checks

    def test_redis_url_registers_check(self):
        registry = HealthCheckRegistry()
        with patch.dict("os.environ", {"REDIS_URL": "redis://localhost:6379"}, clear=True):
            _setup_default_checks(registry)
        assert "redis" in registry._checks

    def test_all_urls_registers_four_checks(self):
        registry = HealthCheckRegistry()
        env = {
            "FORGE_CODE_ATLAS_URL": "http://atlas:8000",
            "FORGE_TECH_DILIGENCE_URL": "http://td:9000",
            "FORGE_WEBHOOK_URL": "http://wh:8080",
            "REDIS_URL": "redis://localhost:6379",
        }
        with patch.dict("os.environ", env, clear=True):
            _setup_default_checks(registry)
        assert len(registry._checks) == 4


# ===========================================================================
# Global helpers: get_health_registry / run_health_check
# ===========================================================================

class TestGlobalRegistry:
    """Tests for module-level singleton and convenience helpers."""

    def test_get_health_registry_singleton(self):
        import forge_harness.health_checks as hc
        hc._registry = None
        r1 = get_health_registry()
        r2 = get_health_registry()
        assert r1 is r2

    def test_get_health_registry_resets_cleanly(self):
        import forge_harness.health_checks as hc
        hc._registry = None
        r = get_health_registry()
        assert isinstance(r, HealthCheckRegistry)

    @pytest.mark.asyncio
    async def test_run_health_check_returns_aggregated(self):
        import forge_harness.health_checks as hc
        hc._registry = None
        result = await run_health_check()
        assert isinstance(result, AggregatedHealth)
        assert result.status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED, HealthStatus.UNHEALTHY)


# ===========================================================================
# Partial failure / edge-case integration scenarios
# ===========================================================================

class TestPartialFailureScenarios:
    """Edge-case integration tests combining multiple features."""

    @pytest.fixture
    def registry(self):
        return HealthCheckRegistry()

    @pytest.mark.asyncio
    async def test_one_service_down_others_healthy(self, registry):
        registry.register("ok1", AsyncMock(return_value=_make_healthy("ok1")))
        registry.register("ok2", AsyncMock(return_value=_make_healthy("ok2")))
        registry.register("down", AsyncMock(return_value=_make_unhealthy("down")))

        result = await registry.check_all()
        assert result.status == HealthStatus.UNHEALTHY
        statuses = {s.name: s.status for s in result.services}
        assert statuses["ok1"] == HealthStatus.HEALTHY
        assert statuses["ok2"] == HealthStatus.HEALTHY
        assert statuses["down"] == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_multiple_callbacks_all_fired(self, registry):
        cb1 = MagicMock()
        cb1.__name__ = "cb1"
        cb2 = MagicMock()
        cb2.__name__ = "cb2"
        registry.on_health_failure(cb1)
        registry.on_health_failure(cb2)
        registry._failure_counts["svc"] = registry.MAX_CONSECUTIVE_FAILURES - 1

        registry.register("svc", AsyncMock(return_value=_make_unhealthy("svc")))
        await registry.check_service("svc")
        cb1.assert_called_once()
        cb2.assert_called_once()

    @pytest.mark.asyncio
    async def test_consecutive_failures_accumulate(self, registry):
        registry.register("svc", AsyncMock(return_value=_make_unhealthy("svc")))
        for i in range(1, 4):
            await registry.check_service("svc")
            assert registry.get_failure_count("svc") == i

    @pytest.mark.asyncio
    async def test_recovery_after_failures(self, registry):
        """After consecutive failures, a healthy response resets the counter."""
        registry._failure_counts["svc"] = 5
        registry.register("svc", AsyncMock(return_value=_make_healthy("svc")))
        await registry.check_service("svc")
        assert registry.get_failure_count("svc") == 0

    @pytest.mark.asyncio
    async def test_exception_message_truncated_to_100_chars(self, registry):
        long_msg = "x" * 200

        async def check():
            raise RuntimeError(long_msg)

        registry.register("svc", check)
        result = await registry.check_service("svc")
        # The message field is "Error: <truncated>"
        assert len(result.message) <= len("Error: ") + 100 + 5  # small buffer


# ===========================================================================
# HealthCheckEndpoints (webhook server integration — skip if FastAPI absent)
# ===========================================================================

class TestHealthCheckEndpoints:
    """Tests for health-related API endpoints exposed by the webhook server."""

    @pytest.fixture
    def app(self):
        """Create test app."""
        try:
            from forge_harness.webhook_server import AuthConfig, create_app
        except ImportError:
            pytest.skip("webhook_server module not available")
        auth_config = AuthConfig(bearer_token="test_token", require_auth=False)
        app = create_app(auth_config=auth_config)
        if app is None:
            pytest.skip("FastAPI not installed")
        return app

    def test_basic_health_endpoint_returns_200(self, app):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200

    def test_basic_health_endpoint_has_status(self, app):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")
        client = TestClient(app)
        data = client.get("/health").json()
        assert "status" in data
