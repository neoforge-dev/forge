"""Legacy Health, Metrics, Version, Sync, and Auth endpoints.

Extracted from the inline routes in webhook_server_main.py create_app().
These are the /legacy/health/*, /api/legacy/*, /api/state/*, /api/sync/*,
/api/auth/validate, /api/auth/sse-session, /api/legacy/auth/* routes.
"""

import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.infrastructure.auth import (
    AuthConfig,
    AuthResult,
    verify_bearer_token,
)
from forge_harness.webhook_server.models.legacy_models import api_response

logger = get_logger(__name__)

router = APIRouter(tags=["legacy-health"])

security = HTTPBearer(auto_error=False)


def _get_verify_auth(app_state: Any):
    """Build verify_auth dependency from app.state."""
    auth_config = getattr(app_state, "auth_config", None) or AuthConfig.from_env()

    async def verify_auth(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(security),
    ) -> None:
        authorization = f"Bearer {credentials.credentials}" if credentials else None
        client_host = request.client.host if request.client else None
        host_header = request.headers.get("host", "")

        if auth_config.allow_localhost and _is_localhost_request(client_host, host_header):
            return

        result = verify_bearer_token(authorization, auth_config, client_host)
        if result == AuthResult.MISSING_TOKEN:
            raise HTTPException(
                status_code=401,
                detail="Missing authentication token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        elif result == AuthResult.INVALID_TOKEN:
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return verify_auth


def _is_localhost_request(client_host: str | None, host_header: str) -> bool:
    """Check if the request is from localhost."""
    if not client_host:
        return False
    localhost_patterns = ["localhost", "127.0.0.1", "::1", "0.0.0.0"]
    return any(pattern in client_host.lower() for pattern in localhost_patterns) or any(
        pattern in host_header.lower() for pattern in localhost_patterns
    )


# =========================================================================
# Health Check
# =========================================================================


@router.get("/legacy/health")
async def health_check(request: Request):
    """Health check endpoint."""
    from forge_harness.webhook_server_main import get_state_synchronizer

    response = {
        "status": "ok",
        "service": "forge-harness-webhooks",
        "timestamp": datetime.now(UTC).isoformat(),
    }

    # Add Redis health check
    try:
        from forge_harness.state_store import StateStore

        state_store = None
        try:
            # Try app.state first (for testability), then fall back to global
            get_store = getattr(request.app.state, "get_state_store", None)
            if get_store:
                state_store = get_store()
            else:
                state_store = StateStore()
                state_store.connect()
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

            elif state_store.get_store_type() == "sqlite":
                response["sqlite_status"] = "connected"
            else:
                response["status"] = "degraded"
    except Exception as e:
        response["status"] = "degraded"
        response["redis_error"] = str(e)

    # Add state synchronizer health check
    try:
        synchronizer = get_state_synchronizer()
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


@router.get("/api/legacy/health")
async def api_health_check(request: Request):
    """Health check at /api/health (alias for /health)."""
    return await health_check(request)


@router.get("/legacy/health/full")
async def full_health_check():
    """Comprehensive health check for all services."""
    from forge_harness.circuit_breaker import get_circuit_breaker, list_circuit_breakers
    from forge_harness.health_checks import get_health_registry

    registry = get_health_registry()
    health = await registry.check_all()
    payload = health.to_dict()
    get_circuit_breaker("tech_diligence")
    get_circuit_breaker("code_atlas")
    circuits = {}
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


@router.get("/legacy/health/metrics")
async def health_metrics():
    """Export health metrics in Prometheus format."""
    from fastapi.responses import Response

    from forge_harness.health_checks import get_health_registry

    registry = get_health_registry()
    health = await registry.check_all()
    return Response(
        content=health.to_prometheus(),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/legacy/health/{service_name}")
async def service_health_check(service_name: str):
    """Check health of a specific service."""
    from forge_harness.health_checks import get_health_registry

    registry = get_health_registry()
    result = await registry.check_service(service_name)
    return JSONResponse(content=result.to_dict())


# =========================================================================
# Metrics
# =========================================================================


@router.get("/api/legacy/metrics")
async def api_metrics(request: Request):
    """Get basic server metrics."""
    try:
        server_start_time = getattr(request.app.state, "server_start_time", time.time())
        request_counter = getattr(request.app.state, "request_counter", {"total": 0})
        uptime_seconds = int(time.time() - server_start_time)

        active_agents_count = 0
        try:
            from forge_harness.state_store import StateStore

            state_store = StateStore()
            if state_store and state_store.is_connected():
                agents = state_store.get_active_agents()
                active_agents_count = len(agents) if agents else 0
        except Exception as e:
            logger.warning(f"Failed to get active agents count: {e}")

        response = {
            "uptime_seconds": uptime_seconds,
            "request_count": request_counter["total"],
            "active_agents": active_agents_count,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        return JSONResponse(content=api_response(response))

    except Exception as e:
        logger.error(f"Failed to get metrics: {e}")
        return JSONResponse(
            status_code=500,
            content=api_response(
                error_code="METRICS_ERROR",
                error_message=f"Failed to retrieve metrics: {str(e)}",
            ),
        )


@router.get("/api/rate-limit/stats")
async def rate_limit_stats(request: Request):
    """Get rate limiter statistics (requires auth)."""
    # Auth checked via middleware / dependency override
    rate_limiter = getattr(request.app.state, "rate_limiter", None)
    if rate_limiter is None:
        return JSONResponse(content={"error": "rate limiter not available"}, status_code=503)
    return JSONResponse(content=rate_limiter.get_stats())


# =========================================================================
# Sync status
# =========================================================================


@router.get("/api/sync/status")
async def api_sync_status():
    """Get tmux sync service status."""
    from forge_harness.webhook_server_main import get_tmux_sync_service

    try:
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
        logger.error(f"Failed to get sync status: {e}")
        return JSONResponse(
            status_code=500,
            content=api_response(
                error_code="SYNC_STATUS_ERROR",
                error_message=f"Failed to retrieve sync status: {str(e)}",
            ),
        )


# =========================================================================
# Version (legacy)
# =========================================================================


@router.get("/api/version/legacy")
async def api_version_legacy():
    """Legacy version endpoint kept for migration compatibility."""
    try:
        version = "1.0.0"
        try:
            import tomllib

            pyproject_path = Path(__file__).resolve().parent.parent.parent.parent / "pyproject.toml"
            if pyproject_path.exists():
                with open(pyproject_path, "rb") as f:
                    pyproject = tomllib.load(f)
                    version = pyproject.get("project", {}).get("version", "1.0.0")
        except Exception as e:
            logger.warning(f"Failed to read version from pyproject.toml: {e}")

        response = {
            "version": version,
            "service": "forge-harness-webhooks",
            "api_version": "1.0.0",
        }

        return JSONResponse(content=api_response(response))

    except Exception as e:
        logger.error(f"Failed to get version: {e}")
        return JSONResponse(
            status_code=500,
            content=api_response(
                error_code="VERSION_ERROR",
                error_message=f"Failed to retrieve version: {str(e)}",
            ),
        )


# =========================================================================
# State Synchronization Endpoints
# =========================================================================


@router.get("/api/state/snapshot")
async def get_state_snapshot(request: Request):
    """Get unified state snapshot from all checkpoint sources."""
    from forge_harness.webhook_server_main import get_state_synchronizer

    synchronizer = get_state_synchronizer()
    if synchronizer is None:
        return JSONResponse(
            content=api_response(
                error_code="SYNCHRONIZER_NOT_AVAILABLE",
                error_message="State synchronizer is not initialized",
            ),
            status_code=503,
        )

    try:
        snapshot = synchronizer.get_state_snapshot()
        return JSONResponse(
            content=api_response(
                {
                    "approvals": [a.__dict__ for a in snapshot.approvals],
                    "pipelines": [p.__dict__ for p in snapshot.pipelines],
                    "ralph": snapshot.ralph.__dict__ if snapshot.ralph else None,
                    "sessions": [s.__dict__ for s in snapshot.sessions],
                    "sync_stats": synchronizer.get_stats(),
                    "last_sync": snapshot.timestamp.isoformat() if snapshot.timestamp else None,
                }
            )
        )
    except Exception as e:
        logger.error(f"Error getting state snapshot: {e}")
        return JSONResponse(
            content=api_response(
                error_code="SNAPSHOT_ERROR",
                error_message=str(e),
            ),
            status_code=500,
        )


@router.post("/api/state/sync")
async def trigger_state_sync(request: Request):
    """Trigger an immediate state synchronization."""
    from forge_harness.webhook_server_main import get_state_synchronizer

    synchronizer = get_state_synchronizer()
    if synchronizer is None:
        return JSONResponse(
            content=api_response(
                error_code="SYNCHRONIZER_NOT_AVAILABLE",
                error_message="State synchronizer is not initialized",
            ),
            status_code=503,
        )

    try:
        snapshot = await synchronizer.sync_all()
        stats = synchronizer.get_stats()
        return JSONResponse(
            content=api_response(
                {
                    "status": "synced",
                    "approvals_count": len(snapshot.approvals),
                    "pipelines_count": len(snapshot.pipelines),
                    "sessions_count": len(snapshot.sessions),
                    "sync_stats": stats,
                }
            )
        )
    except Exception as e:
        logger.error(f"Error triggering state sync: {e}")
        return JSONResponse(
            content=api_response(
                error_code="SYNC_ERROR",
                error_message=str(e),
            ),
            status_code=500,
        )


@router.get("/api/state/stats")
async def get_sync_stats(request: Request):
    """Get synchronizer statistics."""
    from forge_harness.webhook_server_main import get_state_synchronizer

    synchronizer = get_state_synchronizer()
    if synchronizer is None:
        return JSONResponse(
            content=api_response(
                {
                    "status": "unavailable",
                    "message": "State synchronizer is not initialized",
                }
            )
        )

    stats = synchronizer.get_stats()
    return JSONResponse(content=api_response(stats))


# =========================================================================
# Auth validation endpoints
# =========================================================================


@router.post("/api/auth/validate")
async def validate_auth_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """Validate authentication token for remote access."""
    auth_config = getattr(request.app.state, "auth_config", None) or AuthConfig.from_env()

    if not credentials or not credentials.credentials:
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="MISSING_TOKEN",
                error_message="Authorization token is required",
            ),
        )

    token = credentials.credentials

    if not auth_config.bearer_token:
        logger.warning("Auth validation attempted but no FORGE_WEBHOOK_TOKEN configured")
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="AUTH_NOT_CONFIGURED",
                error_message="Server authentication is not configured",
            ),
        )

    if not secrets.compare_digest(token, auth_config.bearer_token):
        client_ip = request.client.host if request.client else "unknown"
        logger.warning(f"Invalid token validation attempt from {client_ip}")
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="INVALID_TOKEN",
                error_message="Invalid authentication token",
            ),
        )

    return JSONResponse(
        content=api_response(
            {
                "valid": True,
                "message": "Authentication successful",
            }
        )
    )


@router.get("/api/legacy/auth/status")
async def auth_status(request: Request):
    """Check authentication status without validating token."""
    auth_config = getattr(request.app.state, "auth_config", None) or AuthConfig.from_env()
    client_ip = request.client.host if request.client else None
    is_localhost = client_ip in ("127.0.0.1", "localhost", "::1")

    return JSONResponse(
        content=api_response(
            {
                "auth_required": not is_localhost,
                "is_localhost": is_localhost,
                "token_configured": bool(auth_config.bearer_token),
            }
        )
    )


@router.post("/api/auth/sse-session")
async def create_sse_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """Create short-lived session token for SSE (Phase 1.3)."""
    auth_config = getattr(request.app.state, "auth_config", None) or AuthConfig.from_env()

    if not credentials or not credentials.credentials:
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="MISSING_TOKEN",
                error_message="Authorization required",
            ),
        )
    token = credentials.credentials
    if not auth_config.bearer_token or not secrets.compare_digest(
        token, auth_config.bearer_token
    ):
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="INVALID_TOKEN",
                error_message="Invalid authentication token",
            ),
        )
    from forge_harness.webhook_server.infrastructure.sse_session import (
        get_sse_session_store,
    )

    store = get_sse_session_store()
    session_token = store.create(token)
    return JSONResponse(
        content=api_response(
            {
                "session_token": session_token,
                "expires_in": 300,
            }
        )
    )


@router.post("/api/legacy/auth/refresh")
async def refresh_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """Refresh access token using refresh token rotation."""
    from forge_harness.webhook_server.infrastructure.jwt_auth import (
        JWTAuthConfig,
        JWTAuthResult,
        rotate_refresh_token,
    )

    if not credentials or not credentials.credentials:
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="MISSING_TOKEN",
                error_message="Refresh token required in Authorization header",
            ),
        )

    refresh_token_value = credentials.credentials
    config = JWTAuthConfig.from_env()
    rotation_result = rotate_refresh_token(refresh_token_value, config)

    if rotation_result.result == JWTAuthResult.EXPIRED_TOKEN:
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="TOKEN_EXPIRED",
                error_message="Refresh token has expired",
            ),
        )
    elif rotation_result.result == JWTAuthResult.TOKEN_REUSED:
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="TOKEN_REUSED",
                error_message="Refresh token has already been used",
            ),
        )
    elif rotation_result.result != JWTAuthResult.SUCCESS:
        return JSONResponse(
            status_code=401,
            content=api_response(
                error_code="INVALID_REFRESH_TOKEN",
                error_message="Invalid refresh token",
            ),
        )

    return JSONResponse(
        content=api_response(
            {
                "access_token": rotation_result.access_token,
                "refresh_token": rotation_result.refresh_token,
                "expires_in": rotation_result.expires_in,
                "token_type": rotation_result.token_type,
            }
        )
    )
