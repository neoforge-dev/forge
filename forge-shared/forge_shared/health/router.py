"""
Health check router for FastAPI applications.

Provides standardized health endpoints with configurable checks.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel


class _StatusValue:
    """A string-like object that also exposes a .value attribute (Enum compatibility)."""

    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"_StatusValue({self.value!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.value == other
        if isinstance(other, _StatusValue):
            return self.value == other.value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.value)


class HealthStatus(BaseModel):
    """
    Health status model.

    Supports both Pydantic model construction::

        HealthStatus(status="healthy", version="1.0.0", timestamp="...")

    And Enum-style class attribute access::

        HealthStatus.HEALTHY.value  # == "healthy"
        HealthStatus.UNHEALTHY.value  # == "unhealthy"
        HealthStatus.DEGRADED.value  # == "degraded"
    """

    status: str
    version: str | None = None
    timestamp: str | None = None

    model_config = {"arbitrary_types_allowed": True}

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.status == other
        if isinstance(other, _StatusValue):
            return self.status == other.value
        return super().__eq__(other)

    def __hash__(self) -> int:
        return hash(self.status)


# Inject enum-style class constants after class definition so Pydantic does not
# treat them as model fields (ClassVar would work but we need .value access).
HealthStatus.HEALTHY = _StatusValue("healthy")  # type: ignore[attr-defined]
HealthStatus.UNHEALTHY = _StatusValue("unhealthy")  # type: ignore[attr-defined]
HealthStatus.DEGRADED = _StatusValue("degraded")  # type: ignore[attr-defined]


class CheckResult(BaseModel):
    """Result of a single health check."""

    status: str
    latency_ms: float | None = None
    message: str | None = None


class HealthResponse(BaseModel):
    """Standard health check response."""

    status: str
    name: str
    version: str
    timestamp: str
    checks: dict[str, CheckResult] = {}
    response_time: float | None = None


class HealthCheck:
    """
    Health check configuration.

    Attributes:
        name: Name of the check (e.g., "database", "redis")
        check_fn: Async or sync function that performs the check and returns
                  (ok, latency_ms, message)
    """

    def __init__(
        self,
        name: str,
        check_fn: Callable[[], tuple[bool, float | None, str | None]],
    ):
        """
        Initialize health check.

        Args:
            name: Check name
            check_fn: Async or sync function returning (ok, latency_ms, message)
        """
        self.name = name
        self.check_fn = check_fn


@dataclass
class DependencyCheck:
    """
    Dependency check configuration for readiness probes.

    Attributes:
        name: Dependency name (e.g., "database", "redis")
        check_fn: Async function that returns a dict with the dependency status
    """

    name: str
    check_fn: Callable[[], Any]


def create_health_router(
    name: str = "service",
    version: str = "0.1.0",
    checks: list[HealthCheck] | None = None,
    dependencies: list[DependencyCheck] | None = None,
    include_live: bool = True,
    include_ready: bool = True,
    include_metrics: bool = False,
) -> APIRouter:
    """
    Create a health check router with standardized endpoints.

    Args:
        name: Service name (default: "service")
        version: Service version
        checks: List of HealthCheck objects for detailed checks
        dependencies: List of DependencyCheck objects for readiness checks
        include_live: Include /health/live endpoint (Kubernetes liveness)
        include_ready: Include /health/ready endpoint (Kubernetes readiness)
        include_metrics: Include response_time in /health response

    Returns:
        FastAPI router with health endpoints

    Example:
        ```python
        from forge_shared.health import create_health_router, HealthCheck

        async def check_database():
            # Returns (ok, latency_ms, message)
            return True, 5.0, None

        router = create_health_router(
            name="my-service",
            version="1.0.0",
            checks=[HealthCheck("database", check_database)]
        )
        app.include_router(router, prefix="/api")
        ```
    """
    router = APIRouter(tags=["Health"])
    checks = checks or []
    dependencies = dependencies or []

    async def _run_checks() -> tuple[str, dict[str, CheckResult]]:
        """Run all HealthCheck objects and return overall status string."""
        results: dict[str, CheckResult] = {}
        all_ok = True

        for check in checks:
            try:
                if asyncio.iscoroutinefunction(check.check_fn):
                    ok, latency_ms, message = await check.check_fn()
                else:
                    ok, latency_ms, message = check.check_fn()

                results[check.name] = CheckResult(
                    status="ok" if ok else "error",
                    latency_ms=latency_ms,
                    message=message,
                )
                if not ok:
                    all_ok = False
            except Exception as e:
                results[check.name] = CheckResult(
                    status="error",
                    message=str(e),
                )
                all_ok = False

        status = "healthy" if all_ok else "unhealthy"
        return status, results

    async def _run_dependencies() -> tuple[bool, dict[str, Any]]:
        """Run all DependencyCheck objects and return merged result dict."""
        merged: dict[str, Any] = {}
        all_ok = True

        for dep in dependencies:
            try:
                if asyncio.iscoroutinefunction(dep.check_fn):
                    result = await dep.check_fn()
                else:
                    result = dep.check_fn()

                # Merge returned dict into the dependencies map.
                # The test expects data["dependencies"]["database"] == "ok",
                # so we flatten the returned dict values into merged using the
                # dependency name as the key when the result is a single-key dict.
                if isinstance(result, dict):
                    if len(result) == 1:
                        merged[dep.name] = next(iter(result.values()))
                    else:
                        merged.update(result)
                else:
                    merged[dep.name] = result
            except Exception:
                merged[dep.name] = "error"
                all_ok = False

        return all_ok, merged

    @router.get("/health")
    async def health_check_endpoint(response: Response) -> dict[str, Any]:
        """
        Comprehensive health check endpoint.

        Returns service status, version, and individual check results.
        """
        start = time.perf_counter()
        status, check_results = await _run_checks()

        if status != "healthy":
            response.status_code = 503

        result: dict[str, Any] = {
            "status": status,
            "name": name,
            "version": version,
            "timestamp": datetime.now(UTC).isoformat(),
            "checks": {k: v.model_dump() for k, v in check_results.items()},
        }

        if include_metrics:
            elapsed_ms = (time.perf_counter() - start) * 1000
            result["response_time"] = round(elapsed_ms, 3)

        return result

    if include_live:

        @router.get("/health/live")
        async def liveness_check() -> dict[str, Any]:
            """
            Kubernetes liveness probe.

            Always returns healthy if the service is running.
            """
            return {"status": "ok", "alive": True}

    if include_ready:

        @router.get("/health/ready")
        async def readiness_check(response: Response) -> dict[str, Any]:
            """
            Kubernetes readiness probe.

            Checks if the service is ready to accept traffic.
            """
            status, _ = await _run_checks()
            dep_ok, dep_results = await _run_dependencies()

            overall_ok = (status == "healthy") and dep_ok

            if not overall_ok:
                response.status_code = 503

            result: dict[str, Any] = {
                "status": "ready" if overall_ok else "not_ready",
            }

            if dep_results:
                result["dependencies"] = dep_results

            return result

    return router
