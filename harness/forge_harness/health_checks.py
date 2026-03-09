"""Health Check System for FORGE Harness.

Provides aggregated health monitoring for all external services
with timeout detection and Prometheus-style metrics.

Usage:
    from forge_harness.health_checks import HealthCheckRegistry, get_health_registry

    registry = get_health_registry()
    health = await registry.check_all()
    print(health.status)  # "healthy", "degraded", or "unhealthy"
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

import httpx

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class HealthFailureCallback(Protocol):
    """Protocol for health failure callbacks."""

    def __call__(self, service_name: str, failure_count: int, health: ServiceHealth) -> None:
        """Called when a service fails health check.

        Args:
            service_name: Name of the failing service
            failure_count: Number of consecutive failures
            health: ServiceHealth result
        """
        ...


class HealthStatus(str, Enum):
    """Health status levels."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ServiceHealth:
    """Health status for a single service."""

    name: str
    status: HealthStatus
    latency_ms: float
    message: str | None = None
    last_check: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "name": self.name,
            "status": self.status.value,
            "latency_ms": round(self.latency_ms, 2),
            "message": self.message,
            "last_check": self.last_check.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class AggregatedHealth:
    """Aggregated health status for all services."""

    status: HealthStatus
    services: list[ServiceHealth]
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    total_latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "status": self.status.value,
            "checked_at": self.checked_at.isoformat(),
            "total_latency_ms": round(self.total_latency_ms, 2),
            "services": [s.to_dict() for s in self.services],
            "summary": {
                "total": len(self.services),
                "healthy": sum(1 for s in self.services if s.status == HealthStatus.HEALTHY),
                "degraded": sum(1 for s in self.services if s.status == HealthStatus.DEGRADED),
                "unhealthy": sum(1 for s in self.services if s.status == HealthStatus.UNHEALTHY),
            },
        }

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus format."""
        lines = [
            "# HELP forge_health_status Service health status (1=healthy, 0.5=degraded, 0=unhealthy)",
            "# TYPE forge_health_status gauge",
        ]
        for service in self.services:
            value = {"healthy": 1, "degraded": 0.5, "unhealthy": 0}[service.status.value]
            lines.append(f'forge_health_status{{service="{service.name}"}} {value}')

        lines.extend(
            [
                "",
                "# HELP forge_health_latency_ms Service response latency in milliseconds",
                "# TYPE forge_health_latency_ms gauge",
            ]
        )
        for service in self.services:
            lines.append(
                f'forge_health_latency_ms{{service="{service.name}"}} {service.latency_ms:.2f}'
            )

        lines.extend(
            [
                "",
                "# HELP forge_health_check_timestamp_seconds Timestamp of last health check",
                "# TYPE forge_health_check_timestamp_seconds gauge",
                f"forge_health_check_timestamp_seconds {self.checked_at.timestamp():.0f}",
            ]
        )

        return "\n".join(lines)


# Type alias for health check functions
HealthCheckFunc = Callable[[], Coroutine[Any, Any, ServiceHealth]]


class HealthCheckRegistry:
    """Registry for health check functions.

    Manages health checks for all external services with configurable
    timeouts and aggregation logic.
    """

    # Timeout thresholds in seconds
    DEGRADED_THRESHOLD = 5.0  # >5s = degraded
    UNHEALTHY_THRESHOLD = 10.0  # >10s = unhealthy
    MAX_CONSECUTIVE_FAILURES = 3  # Trigger callback after 3 failures

    def __init__(self):
        """Initialize the registry."""
        self._checks: dict[str, HealthCheckFunc] = {}
        self._last_results: dict[str, ServiceHealth] = {}
        self._failure_counts: dict[str, int] = {}
        self._failure_callbacks: list[HealthFailureCallback] = []
        self._http_client: httpx.AsyncClient | None = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=15.0)
        return self._http_client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    def register(self, name: str, check_func: HealthCheckFunc) -> None:
        """Register a health check function.

        Args:
            name: Service name
            check_func: Async function that returns ServiceHealth
        """
        self._checks[name] = check_func
        logger.debug(f"Registered health check: {name}")

    def unregister(self, name: str) -> None:
        """Unregister a health check."""
        self._checks.pop(name, None)
        self._last_results.pop(name, None)
        self._failure_counts.pop(name, None)

    def on_health_failure(self, callback: HealthFailureCallback) -> None:
        """Register a callback for health check failures.

        Callback is invoked when a service fails MAX_CONSECUTIVE_FAILURES times.

        Args:
            callback: Function to call on failure
        """
        self._failure_callbacks.append(callback)
        logger.debug(f"Registered health failure callback: {callback.__name__}")

    def get_failure_count(self, name: str) -> int:
        """Get consecutive failure count for a service.

        Args:
            name: Service name

        Returns:
            Number of consecutive failures
        """
        return self._failure_counts.get(name, 0)

    async def check_service(self, name: str) -> ServiceHealth:
        """Run health check for a single service.

        Args:
            name: Service name

        Returns:
            ServiceHealth result
        """
        if name not in self._checks:
            return ServiceHealth(
                name=name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=0,
                message=f"Unknown service: {name}",
            )

        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self._checks[name](),
                timeout=self.UNHEALTHY_THRESHOLD + 1,
            )
            # Adjust status based on latency
            if result.latency_ms > self.UNHEALTHY_THRESHOLD * 1000:
                result.status = HealthStatus.UNHEALTHY
                result.message = result.message or f"Timeout: {result.latency_ms:.0f}ms"
            elif result.latency_ms > self.DEGRADED_THRESHOLD * 1000:
                if result.status == HealthStatus.HEALTHY:
                    result.status = HealthStatus.DEGRADED
                    result.message = result.message or f"Slow response: {result.latency_ms:.0f}ms"

            # Track failures and trigger callbacks
            if result.status == HealthStatus.UNHEALTHY:
                self._failure_counts[name] = self._failure_counts.get(name, 0) + 1
                failure_count = self._failure_counts[name]

                if failure_count >= self.MAX_CONSECUTIVE_FAILURES:
                    logger.warning(
                        f"Service {name} failed {failure_count} consecutive health checks"
                    )
                    # Notify callbacks
                    for callback in self._failure_callbacks:
                        try:
                            callback(name, failure_count, result)
                        except Exception as e:
                            logger.error(f"Health failure callback error: {e}")
            else:
                # Reset failure count on success
                self._failure_counts[name] = 0

            self._last_results[name] = result
            return result
        except TimeoutError:
            elapsed = (time.perf_counter() - start) * 1000
            result = ServiceHealth(
                name=name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=elapsed,
                message="Health check timed out",
            )
            self._last_results[name] = result
            self._failure_counts[name] = self._failure_counts.get(name, 0) + 1
            return result
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            result = ServiceHealth(
                name=name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=elapsed,
                message=f"Error: {str(e)[:100]}",
            )
            self._last_results[name] = result
            self._failure_counts[name] = self._failure_counts.get(name, 0) + 1
            logger.warning(f"Health check failed for {name}: {e}")
            return result

    async def check_all(self) -> AggregatedHealth:
        """Run all health checks concurrently.

        Returns:
            AggregatedHealth with all results
        """
        if not self._checks:
            return AggregatedHealth(
                status=HealthStatus.HEALTHY,
                services=[],
            )

        start = time.perf_counter()
        results = await asyncio.gather(
            *[self.check_service(name) for name in self._checks],
            return_exceptions=True,
        )

        services = []
        for result in results:
            if isinstance(result, ServiceHealth):
                services.append(result)
            elif isinstance(result, Exception):
                logger.error(f"Health check error: {result}")

        total_latency = (time.perf_counter() - start) * 1000

        # Determine overall status
        if any(s.status == HealthStatus.UNHEALTHY for s in services):
            overall_status = HealthStatus.UNHEALTHY
        elif any(s.status == HealthStatus.DEGRADED for s in services):
            overall_status = HealthStatus.DEGRADED
        else:
            overall_status = HealthStatus.HEALTHY

        return AggregatedHealth(
            status=overall_status,
            services=services,
            total_latency_ms=total_latency,
        )

    def get_last_result(self, name: str) -> ServiceHealth | None:
        """Get last health check result for a service."""
        return self._last_results.get(name)

    # Built-in health check factories

    def create_http_check(
        self,
        name: str,
        url: str,
        expected_status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> HealthCheckFunc:
        """Create an HTTP health check function.

        Args:
            name: Service name
            url: Health check URL
            expected_status: Expected HTTP status code
            headers: Optional headers to include

        Returns:
            Health check function
        """

        async def check() -> ServiceHealth:
            start = time.perf_counter()
            try:
                client = await self._get_http_client()
                response = await client.get(url, headers=headers or {})
                elapsed = (time.perf_counter() - start) * 1000

                if response.status_code == expected_status:
                    return ServiceHealth(
                        name=name,
                        status=HealthStatus.HEALTHY,
                        latency_ms=elapsed,
                        metadata={"status_code": response.status_code},
                    )
                else:
                    return ServiceHealth(
                        name=name,
                        status=HealthStatus.UNHEALTHY,
                        latency_ms=elapsed,
                        message=f"Unexpected status: {response.status_code}",
                        metadata={"status_code": response.status_code},
                    )
            except httpx.TimeoutException:
                elapsed = (time.perf_counter() - start) * 1000
                return ServiceHealth(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=elapsed,
                    message="Connection timeout",
                )
            except httpx.ConnectError as e:
                elapsed = (time.perf_counter() - start) * 1000
                return ServiceHealth(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=elapsed,
                    message=f"Connection failed: {str(e)[:50]}",
                )
            except Exception as e:
                elapsed = (time.perf_counter() - start) * 1000
                return ServiceHealth(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=elapsed,
                    message=f"Error: {str(e)[:50]}",
                )

        return check

    def create_redis_check(self, name: str = "redis") -> HealthCheckFunc:
        """Create a Redis health check function.

        Returns:
            Health check function
        """

        async def check() -> ServiceHealth:
            start = time.perf_counter()
            try:
                import redis.asyncio as redis

                from forge_harness.config import get_config

                config = get_config()
                redis_url = config.redis_url or "redis://localhost:6379"

                client = redis.from_url(redis_url, decode_responses=True)
                await client.ping()
                elapsed = (time.perf_counter() - start) * 1000
                await client.aclose()

                return ServiceHealth(
                    name=name,
                    status=HealthStatus.HEALTHY,
                    latency_ms=elapsed,
                    metadata={"url": redis_url.split("@")[-1]},  # Hide credentials
                )
            except ImportError:
                return ServiceHealth(
                    name=name,
                    status=HealthStatus.DEGRADED,
                    latency_ms=0,
                    message="redis package not installed",
                )
            except Exception as e:
                elapsed = (time.perf_counter() - start) * 1000
                return ServiceHealth(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=elapsed,
                    message=f"Redis error: {str(e)[:50]}",
                )

        return check


# Singleton instance
_registry: HealthCheckRegistry | None = None


def get_health_registry() -> HealthCheckRegistry:
    """Get the global health check registry.

    Returns:
        HealthCheckRegistry singleton
    """
    global _registry
    if _registry is None:
        _registry = HealthCheckRegistry()
        _setup_default_checks(_registry)
    return _registry


def _setup_default_checks(registry: HealthCheckRegistry) -> None:
    """Set up default health checks for known services."""
    import os

    # Code Atlas (harness config uses FORGE_CODE_ATLAS_URL)
    code_atlas_url = os.environ.get("FORGE_CODE_ATLAS_URL") or os.environ.get("CODE_ATLAS_URL", "")
    if code_atlas_url:
        registry.register(
            "code_atlas",
            registry.create_http_check("code_atlas", f"{code_atlas_url}/health"),
        )

    # Tech Diligence (harness config uses FORGE_TECH_DILIGENCE_URL)
    tech_diligence_url = os.environ.get("FORGE_TECH_DILIGENCE_URL") or os.environ.get("TECH_DILIGENCE_URL", "")
    if tech_diligence_url:
        registry.register(
            "tech_diligence",
            registry.create_http_check("tech_diligence", f"{tech_diligence_url}/health"),
        )

    # Webhook server (self-check)
    webhook_url = os.environ.get("FORGE_WEBHOOK_URL", "")
    if webhook_url:
        registry.register(
            "webhook_server",
            registry.create_http_check("webhook_server", f"{webhook_url}/health"),
        )

    # Redis (if configured)
    redis_url = os.environ.get("REDIS_URL", "")
    if redis_url:
        registry.register("redis", registry.create_redis_check())

    logger.info(f"Initialized {len(registry._checks)} default health checks")


async def run_health_check() -> AggregatedHealth:
    """Convenience function to run all health checks.

    Returns:
        AggregatedHealth result
    """
    registry = get_health_registry()
    return await registry.check_all()
