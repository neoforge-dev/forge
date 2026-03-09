"""Pipelines API Endpoints

REST API endpoints for pipeline execution management.
Extracted from webhook_server_main.py for better modularity.

Endpoints:
    GET /api/pipelines              - List pipeline executions
    GET /api/pipelines/recent       - Get recently executed pipelines
    GET /api/pipelines/stats        - Get pipeline execution statistics
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.core.dependencies import verify_auth

logger = get_logger(__name__)

router = APIRouter()


def _get_forge_repo_root() -> Path:
    """Find FORGE repo root (directory containing .forge)."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / ".forge").is_dir():
            return current
        current = current.parent
    return Path.cwd()


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


@router.get("/api/pipelines")
async def list_pipelines(
    status: str | None = Query(None, description="Filter by status"),
    type: str | None = Query(None, description="Filter by type (orchestration, ralph_loop)"),
    limit: int = Query(50, description="Maximum number of pipelines to return"),
    _: None = Depends(verify_auth),
):
    """List pipeline executions from checkpoint files.

    Returns real pipeline execution data from orchestration and Ralph loop checkpoints.
    Supports filtering by status and type.
    """
    from forge_harness.pipeline_data import create_pipeline_reader

    try:
        forge_root = _get_forge_repo_root()
        reader = create_pipeline_reader(forge_root=forge_root)
        pipelines = reader.get_recent_pipelines(
            limit=limit,
            status=status,
            type_filter=type,
        )

        return JSONResponse(
            content=api_response(
                {
                    "pipelines": [p.to_dict() for p in pipelines],
                    "count": len(pipelines),
                }
            )
        )
    except Exception as e:
        logger.error(f"Failed to list pipelines: {e}")
        return JSONResponse(
            content=api_response(
                {
                    "pipelines": [],
                    "count": 0,
                    "error": str(e),
                }
            ),
            status_code=500,
        )


@router.get("/api/pipelines/recent")
async def get_recent_pipelines(
    limit: int = Query(10, description="Maximum number of pipelines to return"),
    type: str | None = Query(None, description="Filter by type (orchestration, ralph_loop)"),
    _: None = Depends(verify_auth),
):
    """Get recently executed pipelines.

    Returns pipeline executions from checkpoint files, sorted by most recent.
    """
    from forge_harness.pipeline_data import create_pipeline_reader

    try:
        forge_root = _get_forge_repo_root()
        reader = create_pipeline_reader(forge_root=forge_root)
        pipelines = reader.get_recent_pipelines(limit=limit, type_filter=type)

        return JSONResponse(
            content=api_response(
                {
                    "pipelines": [p.to_dict() for p in pipelines],
                    "count": len(pipelines),
                }
            )
        )
    except Exception as e:
        logger.error(f"Failed to get recent pipelines: {e}")
        return JSONResponse(
            content=api_response(
                {
                    "pipelines": [],
                    "count": 0,
                    "error": str(e),
                }
            ),
            status_code=500,
        )


@router.get("/api/pipelines/stats")
async def get_pipeline_stats(
    _: None = Depends(verify_auth),
):
    """Get statistics about pipeline executions.

    Returns counts of pipelines by type and status from checkpoint data.
    """
    from forge_harness.pipeline_data import create_pipeline_reader

    try:
        forge_root = _get_forge_repo_root()
        reader = create_pipeline_reader(forge_root=forge_root)
        stats = reader.get_pipeline_stats()

        return JSONResponse(content=api_response(stats))
    except Exception as e:
        logger.error(f"Failed to get pipeline stats: {e}")
        return JSONResponse(
            content=api_response(
                {
                    "total": 0,
                    "by_status": {},
                    "by_type": {},
                    "error": str(e),
                }
            ),
            status_code=500,
        )
