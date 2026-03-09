"""State Synchronization API Endpoints

Provides state snapshot, sync trigger, and sync stats endpoints.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger
from forge_harness.state_synchronizer import get_state_synchronizer
from forge_harness.webhook_server.core.dependencies import verify_auth

router = APIRouter()
logger = get_logger(__name__)


def api_response(
    data: Any = None, error_code: str | None = None, error_message: str | None = None
) -> dict:
    """Create standardized API response.

    Args:
        data: Response data (for success)
        error_code: Error code (for failures)
        error_message: Error description (for failures)

    Returns:
        Standardized response dict
    """
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


@router.get("/api/state/snapshot")
async def get_state_snapshot(_: None = Depends(verify_auth)):
    """Get unified state snapshot from all checkpoint sources.

    Returns a combined view of:
    - Pending approvals
    - Active pipelines
    - Ralph loop status
    - Active sessions

    This endpoint uses the StateSynchronizer for consistent state.
    """
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
        # Get current state snapshot
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
async def trigger_state_sync(_: None = Depends(verify_auth)):
    """Trigger an immediate state synchronization.

    Useful for forcing a sync when files have been modified externally.
    """
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
async def get_sync_stats(_: None = Depends(verify_auth)):
    """Get synchronizer statistics.

    Returns metrics about sync performance, errors, and file tracking.
    """
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
