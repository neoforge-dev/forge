"""Intake Queue API Endpoints — DF-2001

REST endpoints that expose the event-driven task intake queue to the
Command Center frontend and the forge CLI.

Endpoints:
    POST /api/intake              — Submit a new intake item
    GET  /api/intake/stats        — Aggregate queue statistics
    GET  /api/intake              — List pending items (optional ?status= filter)
    GET  /api/intake/{item_id}    — Get a specific item by ID
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forge_harness.error_schema import (
    NOT_FOUND,
    VALIDATION_ERROR,
    ForgeError,
    api_success_body,
)
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class SubmitIntakeRequest(BaseModel):
    """Request body for submitting a new intake item."""

    title: str = Field(..., min_length=1, max_length=200, description="Short task summary.")
    description: str = Field(default="", description="Extended task description.")
    task_type: str = Field(..., description="Canonical task type (e.g. 'bug-fix', 'api-endpoint').")
    risk_tier: str = Field(..., description="Risk classification: low, medium, high, critical.")
    priority: int = Field(default=3, ge=1, le=5, description="Priority 1 (lowest) to 5 (highest).")
    source: str = Field(..., description="Origin: api, dispatch_file, heartbeat, cli.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra context.")


# ---------------------------------------------------------------------------
# Dependency helper
# ---------------------------------------------------------------------------


def _get_service():
    """Lazy import of IntakeService singleton to avoid circular imports."""
    from forge_harness.webhook_server.services.intake_service import get_intake_service

    return get_intake_service()


def _api_response(data: Any) -> dict[str, Any]:
    """Build a canonical success response body."""
    return api_success_body(data)


# ---------------------------------------------------------------------------
# NOTE: /api/intake/stats MUST be registered before /api/intake/{item_id}
# to prevent FastAPI from treating "stats" as a path parameter.
# ---------------------------------------------------------------------------


@router.get("/api/intake/stats")
async def get_intake_stats():
    """Return aggregate statistics for the intake queue.

    Returns::

        {
            "by_status": {"pending": int, "assigned": int, "rejected": int, "expired": int},
            "by_source": {"api": int, ...},
            "total": int
        }
    """
    service = _get_service()
    stats = service.get_stats()
    return JSONResponse(content=_api_response(stats))


# ---------------------------------------------------------------------------
# Collection routes
# ---------------------------------------------------------------------------


@router.get("/api/intake")
async def list_intake_items(
    status: str | None = Query(
        None, description="Filter by status: pending, assigned, rejected, expired."
    ),
):
    """List intake items with an optional status filter.

    When no status filter is provided, returns all items with ``pending`` status
    (the default behaviour of the underlying service).

    Query params:
        status: Optional lifecycle status filter.
    """
    service = _get_service()

    if status is not None:
        # Validate status value
        from forge_harness.webhook_server.models.intake_queue import IntakeStatus

        try:
            filter_status = IntakeStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=ForgeError(
                    code=VALIDATION_ERROR,
                    message=f"Invalid status {status!r}. Must be one of: {[s.value for s in IntakeStatus]}",
                ).to_dict(),
            )

        # Retrieve all stored items and filter by status
        with service._lock:
            items = [
                item
                for item_id, item in service._items.items()
                if service._status_index.get(item_id) == filter_status
            ]
    else:
        # Default: list pending items
        items = service.list_pending()

    return JSONResponse(
        content=_api_response(
            {
                "items": [item.model_dump(mode="json") for item in items],
                "count": len(items),
            }
        )
    )


@router.post("/api/intake")
async def submit_intake_item(body: SubmitIntakeRequest):
    """Submit a new task intake item.

    Validates the payload, assigns a work-cell lane, checks WIP capacity,
    and emits the appropriate SSE event.

    Args:
        body: Intake item payload.

    Returns:
        IntakeResult dict with item_id, status, assigned_lane, and assigned_task_id.
    """
    from forge_harness.webhook_server.models.intake_queue import IntakeItem
    from forge_harness.webhook_server.models.lane_policy import RiskTier, TaskType

    try:
        task_type = TaskType(body.task_type)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=f"Invalid task_type {body.task_type!r}. Valid values: {[t.value for t in TaskType]}",
            ).to_dict(),
        )

    try:
        risk_tier = RiskTier(body.risk_tier)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=f"Invalid risk_tier {body.risk_tier!r}. Valid values: {[r.value for r in RiskTier]}",
            ).to_dict(),
        )

    try:
        item = IntakeItem(
            title=body.title,
            description=body.description,
            task_type=task_type,
            risk_tier=risk_tier,
            priority=body.priority,
            source=body.source,
            metadata=body.metadata,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=str(exc),
            ).to_dict(),
        )

    service = _get_service()
    result = service.submit(item)
    return JSONResponse(
        status_code=201,
        content=_api_response(result.model_dump(mode="json")),
    )


# ---------------------------------------------------------------------------
# Item-ID routes — MUST come after /api/intake/stats
# ---------------------------------------------------------------------------


@router.get("/api/intake/{item_id}")
async def get_intake_item(item_id: str):
    """Get details for a specific intake item by ID.

    Args:
        item_id: The intake item identifier.

    Returns:
        IntakeItem dict with current status, or 404 if not found.
    """
    service = _get_service()

    with service._lock:
        item = service._items.get(item_id)
        status = service._status_index.get(item_id)
        result = service._results.get(item_id)

    if item is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=NOT_FOUND,
                message=f"Intake item '{item_id}' not found",
            ).to_dict(),
        )

    item_dict = item.model_dump(mode="json")
    item_dict["current_status"] = status.value if status else None
    if result:
        item_dict["result"] = result.model_dump(mode="json")

    return JSONResponse(content=_api_response(item_dict))
