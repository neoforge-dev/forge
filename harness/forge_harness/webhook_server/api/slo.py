"""SLO Monitoring API Endpoints — DF-2004

REST endpoints for querying SLO compliance status, recording task lifecycle
events, and retrieving breach history.

Endpoints:
    GET  /api/slo/breaches             — Get lanes currently in breached state
    GET  /api/slo/{lane}               — Check SLO for a specific lane
    GET  /api/slo                      — Check SLOs for all lanes
    POST /api/slo/tasks/{task_id}/start    — Record task start
    POST /api/slo/tasks/{task_id}/complete — Record task completion
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forge_harness.error_schema import (
    NOT_FOUND,
    ForgeError,
    api_success_body,
)
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class RecordTaskStartRequest(BaseModel):
    """Request body for recording a task start event."""

    lane: str = Field(
        ...,
        description="Work-cell lane the task executes in (e.g. 'api_simple').",
    )


class RecordTaskCompleteRequest(BaseModel):
    """Request body for recording a task completion event."""

    passed: bool = Field(
        ...,
        description="True when the task completed successfully; False otherwise.",
    )


# ---------------------------------------------------------------------------
# Dependency helper
# ---------------------------------------------------------------------------


def _get_monitor():
    """Lazy import of SLOMonitor singleton to avoid circular imports."""
    from forge_harness.webhook_server.services.slo_monitor import get_slo_monitor

    return get_slo_monitor()


def _api_response(data: Any) -> dict[str, Any]:
    """Build a canonical success response body."""
    return api_success_body(data)


def _slo_result_to_dict(result: Any) -> dict[str, Any]:
    """Serialise a SLOCheckResult to a plain dict for API responses."""
    return result.model_dump(mode="json")


def _parse_lane(lane_str: str) -> Any:
    """Parse a lane string into a WorkCellLane enum value.

    Args:
        lane_str: The lane identifier string.

    Returns:
        WorkCellLane enum value.

    Raises:
        HTTPException: 404 when the lane is not recognised.
    """
    from forge_harness.webhook_server.models.work_cell import WorkCellLane

    try:
        return WorkCellLane(lane_str)
    except ValueError:
        valid = [lane.value for lane in WorkCellLane]
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=NOT_FOUND,
                message=f"Lane '{lane_str}' not found. Valid lanes: {valid}",
            ).to_dict(),
        )


# ---------------------------------------------------------------------------
# NOTE: /api/slo/breaches and /api/slo/tasks/* MUST be registered before
# /api/slo/{lane} to prevent FastAPI from treating "breaches" or "tasks"
# as path parameters.
# ---------------------------------------------------------------------------


@router.get("/api/slo/breaches")
async def get_slo_breaches(
    window_minutes: int = Query(
        default=60,
        ge=1,
        description="How far back (minutes) to look when evaluating SLO status.",
    ),
):
    """Return SLO results for lanes currently in a breached state.

    Only lanes whose current computed status is 'breached' are returned.

    Query params:
        window_minutes: Lookback window in minutes (default 60, min 1).

    Returns:
        List of SLOCheckResult dicts for breached lanes.
    """
    monitor = _get_monitor()
    breaches = monitor.get_breaches(window_minutes=window_minutes)
    return JSONResponse(
        content=_api_response(
            {
                "breaches": [_slo_result_to_dict(r) for r in breaches],
                "count": len(breaches),
                "window_minutes": window_minutes,
            }
        )
    )


# ---------------------------------------------------------------------------
# Task lifecycle recording routes
# ---------------------------------------------------------------------------


@router.post("/api/slo/tasks/{task_id}/start")
async def record_task_start(task_id: str, body: RecordTaskStartRequest):
    """Record the start of a task in the SLO monitor.

    Args:
        task_id: Unique identifier of the task.
        body:    ``{"lane": "api_simple"}``

    Returns:
        Confirmation dict with task_id and lane.
    """
    lane = _parse_lane(body.lane)
    monitor = _get_monitor()
    monitor.record_task_start(task_id, lane)
    return JSONResponse(
        content=_api_response(
            {
                "task_id": task_id,
                "lane": lane.value,
                "recorded": "start",
            }
        )
    )


@router.post("/api/slo/tasks/{task_id}/complete")
async def record_task_complete(task_id: str, body: RecordTaskCompleteRequest):
    """Record the completion of a task in the SLO monitor.

    If the task was never started, the monitor logs a warning and returns
    a 404 response.

    Args:
        task_id: Unique identifier of the task.
        body:    ``{"passed": true}``

    Returns:
        Confirmation dict with task_id and pass status.
    """
    monitor = _get_monitor()
    record = monitor.record_task_complete(task_id, passed=body.passed)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=NOT_FOUND,
                message=f"Task '{task_id}' was never started; cannot record completion.",
            ).to_dict(),
        )
    return JSONResponse(
        content=_api_response(
            {
                "task_id": task_id,
                "passed": body.passed,
                "lead_time_seconds": record.lead_time_seconds,
                "lane": record.lane.value,
                "recorded": "complete",
            }
        )
    )


# ---------------------------------------------------------------------------
# Collection route — all SLOs
# ---------------------------------------------------------------------------


@router.get("/api/slo")
async def check_all_slos():
    """Check SLO compliance for all registered work-cell lanes.

    Returns:
        List of SLOCheckResult dicts — one entry per WorkCellLane.
    """
    monitor = _get_monitor()
    results = monitor.check_all_slos()
    return JSONResponse(
        content=_api_response(
            {
                "slos": [_slo_result_to_dict(r) for r in results],
                "count": len(results),
            }
        )
    )


# ---------------------------------------------------------------------------
# Lane-specific route — MUST come after /api/slo/breaches and task routes
# ---------------------------------------------------------------------------


@router.get("/api/slo/{lane}")
async def check_slo_for_lane(lane: str):
    """Check the SLO compliance status for a specific work-cell lane.

    Args:
        lane: Work-cell lane identifier (e.g. 'api_simple', 'deployment').

    Returns:
        SLOCheckResult dict, or 404 if the lane is not recognised.
    """
    work_cell_lane = _parse_lane(lane)
    monitor = _get_monitor()
    result = monitor.check_slo(work_cell_lane)
    return JSONResponse(content=_api_response(_slo_result_to_dict(result)))
