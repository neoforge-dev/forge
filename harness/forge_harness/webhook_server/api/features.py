"""Feature Progress API Endpoints

REST API endpoints for tracking feature progress across the portfolio,
linking agent work sessions to features.

Endpoints:
    GET  /api/features                       — List all features (filterable)
    GET  /api/features/stats                 — Feature statistics
    GET  /api/features/{feature_id}          — Get feature details
    PUT  /api/features/{feature_id}          — Update progress/status
    POST /api/features/{feature_id}/assign   — Assign agent to feature
    POST /api/features/{feature_id}/link-task — Link task to feature
"""

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


class UpdateFeatureRequest(BaseModel):
    """Request body for updating feature progress.

    At least one of ``completion_pct`` or ``status`` must be provided.
    """

    completion_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Completion percentage (0-100)",
    )
    status: str | None = Field(
        default=None,
        description="Feature status: planned, in_progress, testing, done",
    )
    name: str | None = Field(
        default=None,
        description="Feature name (sets name when creating an implicit record)",
    )
    domain: str | None = Field(
        default=None,
        description="Domain identifier",
    )
    project: str | None = Field(
        default=None,
        description="Project identifier",
    )


class AssignAgentRequest(BaseModel):
    """Request body for assigning an agent to a feature."""

    agent_id: str = Field(..., description="Agent identifier to assign")


class LinkTaskRequest(BaseModel):
    """Request body for linking a task to a feature."""

    task_id: str = Field(..., description="Task identifier to link")


# ---------------------------------------------------------------------------
# Dependency helper
# ---------------------------------------------------------------------------


def _get_tracker():
    """Return the FeatureTracker singleton (lazy import avoids circular refs)."""
    from forge_harness.webhook_server.services.feature_tracker import get_feature_tracker

    return get_feature_tracker()


def _api_response(data: Any) -> dict[str, Any]:
    """Build a canonical success response body."""
    return api_success_body(data)


def _feature_to_dict(fp: Any) -> dict[str, Any]:
    """Serialise a FeatureProgress to a plain dict for API responses."""
    return fp.to_dict()


# ---------------------------------------------------------------------------
# NOTE: /api/features/stats MUST be registered before /api/features/{feature_id}
# to prevent FastAPI from treating "stats" as a path parameter.
# ---------------------------------------------------------------------------


@router.get("/api/features/stats")
async def get_feature_stats():
    """Return aggregate feature statistics.

    Returns::

        {
            "total": int,
            "by_status": {"planned": int, "in_progress": int, "testing": int, "done": int},
            "by_domain": {"domain-name": int, ...}
        }
    """
    tracker = _get_tracker()
    stats = tracker.get_stats()
    return JSONResponse(content=_api_response(stats))


# ---------------------------------------------------------------------------
# Collection routes
# ---------------------------------------------------------------------------


@router.get("/api/features")
async def list_features(
    domain: str | None = Query(None, description="Filter by domain"),
    project: str | None = Query(None, description="Filter by project"),
    status: str | None = Query(None, description="Filter by status"),
):
    """List all feature progress records with optional filters.

    Features with no progress record yet are returned with ``planned`` status
    and 0% completion if they appear in a ``features.json`` file under the
    portfolio root.

    Query params:
        domain:  Filter to a specific domain.
        project: Filter to a specific project.
        status:  Filter by status (planned, in_progress, testing, done).
    """
    tracker = _get_tracker()
    features = tracker.load_features(domain=domain, project=project)

    # Apply status filter post-load (load_features only filters domain/project)
    if status is not None:
        features = [f for f in features if f.status == status]

    return JSONResponse(
        content=_api_response(
            {
                "features": [_feature_to_dict(f) for f in features],
                "count": len(features),
            }
        )
    )


# ---------------------------------------------------------------------------
# Feature-ID routes — MUST come after /api/features/stats
# ---------------------------------------------------------------------------


@router.get("/api/features/{feature_id}")
async def get_feature(feature_id: str):
    """Get progress details for a specific feature.

    Args:
        feature_id: The feature identifier.

    Returns:
        FeatureProgress dict, or 404 if not found.
    """
    tracker = _get_tracker()
    fp = tracker.get_feature(feature_id)
    if fp is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code=NOT_FOUND,
                message=f"Feature '{feature_id}' not found",
            ).to_dict(),
        )
    return JSONResponse(content=_api_response(_feature_to_dict(fp)))


@router.put("/api/features/{feature_id}")
async def update_feature(feature_id: str, body: UpdateFeatureRequest):
    """Update progress or status for a feature.

    Creates a new progress record if none exists yet.

    Args:
        feature_id: The feature identifier.
        body:       Fields to update (completion_pct, status, name, domain, project).

    Returns:
        Updated FeatureProgress dict.
    """
    if body.completion_pct is None and body.status is None:
        raise HTTPException(
            status_code=422,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message="At least one of 'completion_pct' or 'status' must be provided",
            ).to_dict(),
        )

    tracker = _get_tracker()
    try:
        # Use existing record's pct if caller only provides status
        if body.completion_pct is None:
            existing = tracker.get_feature(feature_id)
            pct = existing.completion_pct if existing is not None else 0.0
        else:
            pct = body.completion_pct

        fp = tracker.update_progress(
            feature_id=feature_id,
            completion_pct=pct,
            status=body.status,
            name=body.name or "",
            domain=body.domain or "unknown",
            project=body.project or "unknown",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=str(exc),
            ).to_dict(),
        )

    return JSONResponse(content=_api_response(_feature_to_dict(fp)))


@router.post("/api/features/{feature_id}/assign")
async def assign_agent(feature_id: str, body: AssignAgentRequest):
    """Assign an agent to a feature.

    Automatically transitions the feature from ``planned`` to ``in_progress``
    when an agent is assigned.

    Args:
        feature_id: The feature identifier.
        body:       ``{"agent_id": "..."}``

    Returns:
        Updated FeatureProgress dict.
    """
    tracker = _get_tracker()
    fp = tracker.assign_agent(feature_id=feature_id, agent_id=body.agent_id)
    return JSONResponse(content=_api_response(_feature_to_dict(fp)))


@router.post("/api/features/{feature_id}/link-task")
async def link_task(feature_id: str, body: LinkTaskRequest):
    """Link a task ID to a feature.

    Duplicate task IDs are silently ignored.

    Args:
        feature_id: The feature identifier.
        body:       ``{"task_id": "..."}``

    Returns:
        Updated FeatureProgress dict.
    """
    tracker = _get_tracker()
    fp = tracker.link_task(feature_id=feature_id, task_id=body.task_id)
    return JSONResponse(content=_api_response(_feature_to_dict(fp)))
