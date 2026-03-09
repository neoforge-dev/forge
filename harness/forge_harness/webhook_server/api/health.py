"""Health and Metrics API Endpoints

Provides health check, metrics, and version endpoints for monitoring.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from forge_harness.approval_queue import get_approval_queue
from forge_harness.logging_config import get_logger
from forge_harness.session_tracker import get_session_tracker

router = APIRouter()
logger = get_logger(__name__)


def api_response(
    data: Any = None, error_code: str | None = None, error_message: str | None = None
) -> dict[str, Any]:
    """Create a standardized API response."""
    if error_code or error_message:
        return {
            "success": False,
            "data": None,
            "error": {
                "code": error_code or "UNKNOWN_ERROR",
                "message": error_message or "An error occurred",
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }
    return {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": datetime.now(UTC).isoformat(),
    }


class HealthResponse(BaseModel):
    """Health check response model."""

    status: str
    service: str
    timestamp: str
    redis_status: str | None = None
    store_type: str | None = None
    redis_test: str | None = None
    synchronizer_status: str | None = None
    synchronizer_sync_count: int | None = None
    synchronizer_errors: int | None = None


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Basic health check endpoint.

    Returns:
        dict: Health status including Redis and synchronizer info
    """
    response: dict[str, Any] = {
        "status": "ok",
        "service": "forge-harness-webhooks",
        "timestamp": datetime.now(UTC).isoformat(),
    }

    # Add Redis health check
    try:
        from forge_harness.state_store import StateStore

        state_store = None
        try:
            state_store = get_state_store(check_health=False)
        except Exception:
            state_store = StateStore()
            state_store.connect()

        if state_store:
            redis_connected = state_store.is_connected()
            response["redis_status"] = "connected" if redis_connected else "disconnected"
            response["store_type"] = state_store.get_store_type()

            if redis_connected and state_store.get_store_type() == "redis":
                test_result = False
                if hasattr(state_store, "_redis") and hasattr(state_store._redis, "_client"):
                    try:
                        test_key = "health-check-test"
                        test_result = state_store._redis._client.set(test_key, "test", ex=60)
                    except Exception:
                        pass

                response["redis_test"] = "passed" if test_result else "failed"
                if not test_result:
                    response["status"] = "degraded"

            elif not redis_connected:
                response["status"] = "degraded"

            elif state_store.get_store_type() == "sqlite":
                response["sqlite_status"] = "connected"
            else:
                response["status"] = "degraded"
    except Exception as e:
        response["status"] = "degraded"
        response["redis_error"] = str(e)

    # Add state synchronizer health check
    try:
        synchronizer = get_synchronizer(check_health=False)
        if synchronizer is not None:
            sync_stats = synchronizer.get_stats()
            response["synchronizer_status"] = "running" if synchronizer._running else "stopped"
            response["synchronizer_sync_count"] = sync_stats.get("sync_count", 0)
            response["synchronizer_errors"] = sync_stats.get("error_count", 0)
        else:
            response["synchronizer_status"] = "not_initialized"
    except Exception as e:
        response["synchronizer_status"] = "error"
        response["synchronizer_error"] = str(e)

    return response


@router.get("/api/health")
async def api_health_check() -> dict[str, Any]:
    """Health check at /api/health (alias for /health).

    Returns:
        dict: Same as /health endpoint
    """
    return await health_check()


@router.get("/health/full")
async def full_health_check() -> dict[str, Any]:
    """Detailed health check including all dependencies.

    Returns:
        dict: Full health status with all service dependencies
    """
    # Import dependencies for full health check
    try:
        from forge_harness.approval_queue import get_approval_queue
        from forge_harness.session_tracker import get_session_tracker

        response: dict[str, Any] = {
            "status": "ok",
            "service": "forge-harness-webhooks",
            "timestamp": datetime.now(UTC).isoformat(),
            "components": {},
        }

        # Check StateStore
        try:
            state_store = get_state_store(check_health=False)
            if state_store and state_store.is_connected():
                response["components"]["state_store"] = {
                    "status": "healthy",
                    "type": state_store.get_store_type(),
                }
            else:
                response["components"]["state_store"] = {"status": "degraded"}
                response["status"] = "degraded"
        except Exception as e:
            response["components"]["state_store"] = {"status": "error", "error": str(e)}
            response["status"] = "degraded"

        # Check StateSynchronizer
        try:
            synchronizer = get_synchronizer(check_health=False)
            if synchronizer is not None:
                response["components"]["synchronizer"] = {
                    "status": "healthy" if synchronizer._running else "degraded",
                    "running": synchronizer._running,
                }
        except Exception as e:
            response["components"]["synchronizer"] = {"status": "error", "error": str(e)}

        # Check Approval Queue
        try:
            approval_queue = get_approval_queue()
            if approval_queue is not None:
                response["components"]["approval_queue"] = {"status": "healthy"}
        except Exception as e:
            response["components"]["approval_queue"] = {"status": "degraded", "error": str(e)}

        # Check Session Tracker
        try:
            session_tracker = get_session_tracker()
            if session_tracker is not None:
                response["components"]["session_tracker"] = {"status": "healthy"}
        except Exception as e:
            response["components"]["session_tracker"] = {"status": "degraded", "error": str(e)}

        # Include circuit breaker states
        try:
            from forge_harness.circuit_breaker import list_circuit_breakers

            circuits: dict[str, Any] = {}
            for cb in list_circuit_breakers():
                name = cb["name"]
                circuits[name] = {
                    "state": cb["state"],
                    "failures": cb["recent_failures"],
                    "last_failure": cb["stats"].get("last_failure_time"),
                    "retry_after": cb["retry_after"],
                }
            response["circuits"] = circuits
        except Exception as e:
            response["circuits"] = {}
            response["circuits_error"] = str(e)

        return response

    except ImportError as e:
        return {
            "status": "degraded",
            "error": f"Import error: {e}",
            "timestamp": datetime.now(UTC).isoformat(),
        }


@router.get("/health/metrics")
async def health_metrics() -> dict[str, Any]:
    """Get health metrics for monitoring systems.

    Returns:
        dict: Metrics in Prometheus-compatible format
    """
    try:
        metrics: dict[str, Any] = {"timestamp": datetime.now(UTC).isoformat(), "components": {}}

        # State store metrics
        try:
            state_store = get_state_store(check_health=False)
            if state_store and state_store.is_connected():
                metrics["components"]["state_store"] = {
                    "connected": True,
                    "type": state_store.get_store_type(),
                }
        except Exception:
            metrics["components"]["state_store"] = {"connected": False}

        # Synchronizer metrics
        try:
            synchronizer = get_synchronizer(check_health=False)
            if synchronizer is not None:
                sync_stats = synchronizer.get_stats()
                metrics["components"]["synchronizer"] = {
                    "running": synchronizer._running,
                    "sync_count": sync_stats.get("sync_count", 0),
                    "error_count": sync_stats.get("error_count", 0),
                }
        except Exception:
            metrics["components"]["synchronizer"] = {"running": False}

        return metrics

    except Exception as e:
        return {"error": f"Failed to retrieve metrics: {str(e)}"}


# =============================================================================
# Legacy Health Endpoints
# =============================================================================


@router.get("/legacy/health/full")
async def full_health_check_legacy() -> dict[str, Any]:
    """Comprehensive health check for all services (legacy endpoint).

    Returns aggregated health status for Code Atlas, Tech Diligence,
    Redis, and other configured services.

    Response includes:
    - Overall status (healthy/degraded/unhealthy)
    - Individual service health with latency
    - Summary counts
    """
    try:
        from forge_harness.circuit_breaker import get_circuit_breaker, list_circuit_breakers
        from forge_harness.health_checks import get_health_registry

        registry = get_health_registry()
        health = await registry.check_all()
        payload = health.to_dict()
        # Ensure default circuits are registered
        get_circuit_breaker("tech_diligence")
        get_circuit_breaker("code_atlas")
        circuits: dict[str, Any] = {}
        for circuit in list_circuit_breakers():
            circuit_state = dict(circuit)
            if "failures" not in circuit_state:
                circuit_state["failures"] = circuit_state.get("recent_failures", 0)
            if "last_failure" not in circuit_state:
                circuit_state["last_failure"] = circuit_state.get("stats", {}).get(
                    "last_failure_time"
                )
            circuits[circuit_state["name"]] = circuit_state
        payload["circuits"] = circuits
        return JSONResponse(content=payload)
    except Exception as e:
        return JSONResponse(
            content={
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(UTC).isoformat(),
            },
            status_code=500,
        )


@router.get("/legacy/health/metrics")
async def health_metrics_prometheus() -> Response:
    """Export health metrics in Prometheus format (legacy endpoint).

    Returns plain text metrics suitable for Prometheus scraping.
    """
    try:
        from forge_harness.health_checks import get_health_registry

        registry = get_health_registry()
        health = await registry.check_all()
        return Response(
            content=health.to_prometheus(),
            media_type="text/plain; charset=utf-8",
        )
    except Exception as e:
        return Response(
            content=f"# Error generating metrics\n# {str(e)}",
            media_type="text/plain; charset=utf-8",
            status_code=500,
        )


@router.get("/legacy/health/{service_name}")
async def legacy_service_health_check(service_name: str) -> dict[str, Any]:
    """Check health of a specific service (legacy endpoint).

    Args:
        service_name: Service to check (e.g., redis, code_atlas)
    """
    try:
        from forge_harness.health_checks import get_health_registry

        registry = get_health_registry()
        result = await registry.check_service(service_name)
        return JSONResponse(content=result.to_dict())
    except Exception as e:
        return JSONResponse(
            content={"error": f"Failed to check service: {str(e)}", "service": service_name},
            status_code=500,
        )


@router.get("/api/sync/status")
async def api_sync_status() -> dict[str, Any]:
    """Get tmux sync service status.

    Returns:
        - running: Whether sync service is active
        - sync_count: Number of sync cycles completed
        - poll_interval: Seconds between sync cycles
        - tracked_agents: Number of agents being tracked
        - last_sync: Timestamp of last sync
    """
    from datetime import UTC, datetime

    def api_response(
        data: Any = None, error_code: str | None = None, error_message: str | None = None
    ) -> dict[str, Any]:
        """Create a standardized API response."""
        if error_code or error_message:
            return {
                "success": False,
                "data": None,
                "error": {
                    "code": error_code or "UNKNOWN_ERROR",
                    "message": error_message or "An error occurred",
                },
                "timestamp": datetime.now(UTC).isoformat(),
            }
        return {
            "success": True,
            "data": data,
            "error": None,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    try:
        from forge_harness.webhook_server_main import get_tmux_sync_service

        sync_service = get_tmux_sync_service()
        if sync_service is None:
            return JSONResponse(
                content=api_response(
                    {"running": False, "message": "Tmux sync service not available"}
                )
            )

        stats = sync_service.get_stats()
        return JSONResponse(content=api_response(stats))

    except Exception as e:
        logger = get_logger(__name__)
        logger.error(f"Failed to get sync status: {e}")
        return JSONResponse(
            content=api_response(
                error_code="SYNC_STATUS_ERROR",
                error_message=f"Failed to retrieve sync status: {str(e)}",
            ),
            status_code=500,
        )


@router.get("/health/{service_name}")
def service_health_check(service_name: str) -> dict[str, Any]:
    """Get health status of a specific service.

    Args:
        service_name: Name of the service to check

    Returns:
        dict: Health status of the specified service
    """
    service_name = service_name.lower()

    service_checks = {
        "state_store": lambda: _check_state_store(),
        "synchronizer": lambda: _check_synchronizer(),
        "redis": lambda: _check_redis(),
        "approval_queue": lambda: _check_approval_queue(),
    }

    if service_name in service_checks:
        return service_checks[service_name]()

    return {"error": f"Unknown service: {service_name}"}


def get_state_store(check_health: bool = True) -> Any:
    """Get state store instance or health status."""
    try:
        from forge_harness.state_store import get_state_store as _get

        state_store = _get()
        if not check_health:
            return state_store

        if state_store and state_store.is_connected():
            return {
                "service": "state_store",
                "status": "healthy",
                "type": state_store.get_store_type(),
            }
        return {"service": "state_store", "status": "unhealthy"}
    except Exception as e:
        return {"service": "state_store", "status": "error", "error": str(e)}


def get_synchronizer(check_health: bool = True) -> Any:
    """Get state synchronizer instance or health status."""
    try:
        from forge_harness.state_synchronizer import get_state_synchronizer

        synchronizer = get_state_synchronizer()
        if not check_health:
            return synchronizer

        if synchronizer is not None:
            return {
                "service": "synchronizer",
                "status": "healthy" if synchronizer._running else "degraded",
                "running": synchronizer._running,
            }
        return {"service": "synchronizer", "status": "not_initialized"}
    except Exception as e:
        return {"service": "synchronizer", "status": "error", "error": str(e)}


# Alias for test compatibility
get_state_synchronizer = get_synchronizer


def _check_redis() -> dict[str, Any]:
    """Check Redis health."""
    try:
        from forge_harness.state_store import get_state_store

        state_store = get_state_store()
        if state_store and state_store.is_connected() and state_store.get_store_type() == "redis":
            return {"service": "redis", "status": "healthy"}
        return {"service": "redis", "status": "unhealthy"}
    except Exception as e:
        return {"service": "redis", "status": "error", "error": str(e)}


def _check_approval_queue() -> dict[str, Any]:
    """Check approval queue health."""
    try:
        from forge_harness.approval_queue import get_approval_queue

        queue = get_approval_queue()
        if queue is not None:
            return {"service": "approval_queue", "status": "healthy"}
        return {"service": "approval_queue", "status": "not_initialized"}
    except Exception as e:
        return {"service": "approval_queue", "status": "error", "error": str(e)}


def _check_state_store() -> dict[str, Any]:
    """Check state store health."""
    try:
        from forge_harness.state_store import get_state_store

        state_store = get_state_store()
        if state_store and state_store.is_connected():
            return {
                "service": "state_store",
                "status": "healthy",
                "type": state_store.get_store_type(),
            }
        return {"service": "state_store", "status": "unhealthy"}
    except Exception as e:
        return {"service": "state_store", "status": "error", "error": str(e)}


def _check_synchronizer() -> dict[str, Any]:
    """Check state synchronizer health."""
    try:
        from forge_harness.state_synchronizer import get_state_synchronizer

        synchronizer = get_state_synchronizer()
        if synchronizer is not None:
            return {
                "service": "synchronizer",
                "status": "healthy" if synchronizer._running else "degraded",
                "running": synchronizer._running,
            }
        return {"service": "synchronizer", "status": "not_initialized"}
    except Exception as e:
        return {"service": "synchronizer", "status": "error", "error": str(e)}


@router.get("/api/version")
def api_version() -> dict[str, Any]:
    """Get API version information.

    Returns:
        dict: Version info including name, version, and API prefix
    """
    return {
        "name": "forge-harness-webhooks",
        "version": "1.0.0",
        "api_prefix": "/api",
    }


@router.get("/api/metrics")
async def api_metrics() -> dict[str, Any]:
    """Get API metrics for monitoring.

    Returns:
        dict: API metrics including uptime, request counts, active agents
    """
    try:
        state_store = get_state_store(check_health=False)

        active_agents_count = 0
        if state_store and state_store.is_connected():
            try:
                agents = state_store.get_active_agents()
                active_agents_count = len(agents) if agents else 0
            except Exception:
                pass

        return {
            "api_version": "1.0.0",
            "uptime_seconds": 0,
            "request_count": 0,
            "active_agents": active_agents_count,
            "timestamp": datetime.now(UTC).isoformat(),
            "state_store": {
                "connected": state_store.is_connected() if state_store else False,
                "type": state_store.get_store_type() if state_store else None,
            },
        }

    except Exception as e:
        logger.error(f"Failed to get metrics: {e}")
        return {
            "error": f"Failed to retrieve metrics: {str(e)}",
            "timestamp": datetime.now(UTC).isoformat(),
        }


@router.get("/api/data-quality")
async def get_data_quality() -> JSONResponse:
    """Get data quality status for dashboard panel (CP-001).

    Returns health status of all key backend endpoints used by the
    Command Center dashboard. Provides explicit states:
    - healthy: endpoint responding normally
    - stale: endpoint responding but data is old
    - error: endpoint returned an error
    - no_signal: no response from endpoint (backend unavailable)

    Returns:
        JSONResponse with endpoint health statuses
    """
    endpoints_to_check = [
        {"name": "health", "url": "/health"},
        {"name": "nodes", "url": "/api/nodes"},
        {"name": "tasks", "url": "/api/tasks"},
        {"name": "agents", "url": "/api/agents"},
        {"name": "fleet", "url": "/api/fleet/status"},
    ]

    results: list[dict] = []
    healthy_count = 0
    error_count = 0

    # Check basic health to determine backend availability
    try:
        health_status = await health_check()
        backend_available = health_status.get("status") != "degraded"
    except Exception:
        backend_available = False

    for endpoint in endpoints_to_check:
        if not backend_available:
            results.append(
                {
                    "name": endpoint["name"],
                    "status": "no_signal",
                    "last_check": None,
                    "error": "Backend unavailable",
                }
            )
            error_count += 1
        else:
            # Endpoint is reachable (health check passed)
            results.append(
                {
                    "name": endpoint["name"],
                    "status": "healthy",
                    "last_check": datetime.now(UTC).isoformat(),
                    "error": None,
                }
            )
            healthy_count += 1

    # If no endpoints responded, it's a no_backend_signal state
    if healthy_count == 0 and error_count > 0:
        overall_status = "no_backend_signal"
    elif error_count > 0:
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    response_data = {
        "status": overall_status,
        "endpoints": results,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    return JSONResponse(content=response_data)


# End of health.py
