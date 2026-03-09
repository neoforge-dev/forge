"""Dark Factory Canary API Endpoints — DF-4004.

Endpoints:
    GET  /api/canary/rollouts                      - List rollout states
    POST /api/canary/rollouts                      - Create/update rollout state
    GET  /api/canary/rollouts/{rollout_id}         - Get rollout status
    POST /api/canary/rollouts/{rollout_id}/pause   - Pause a rollout
    POST /api/canary/rollouts/{rollout_id}/resume  - Resume a rollout
    POST /api/canary/rollouts/{rollout_id}/promote - Promote rollout stage
    POST /api/canary/rollouts/{rollout_id}/rollback - Revert rollout stage
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forge_harness.dark_factory.canary_control import (
    RolloutStage,
    get_canary_control,
)
from forge_harness.error_schema import VALIDATION_ERROR, ForgeError, api_success_body
from forge_harness.webhook_server.core.dependencies import require_permission
from forge_harness.webhook_server.models.work_cell import WorkCellLane

router = APIRouter()


class CreateCanaryRolloutRequest(BaseModel):
    """Create/update a rollout for a specific (node, lane) pair."""

    node_id: str = Field(..., min_length=1)
    lane: str = Field(..., min_length=1)
    stage: str = Field(default=RolloutStage.canary.value)
    operator: str = Field(default="api", min_length=1)
    reason: str = ""


class CanaryActionRequest(BaseModel):
    """Action payload for pause/resume/promote/rollback operations."""

    operator: str = Field(default="api", min_length=1)
    reason: str = ""
    stage: str | None = None  # Optional explicit target for promote


def _valid_lanes() -> str:
    return ", ".join(sorted(lane.value for lane in WorkCellLane))


def _parse_lane(lane: str) -> str:
    raw = lane.strip().lower()
    try:
        return WorkCellLane(raw).value
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=f"Invalid lane {lane!r}. Valid lanes: {_valid_lanes()}",
            ).to_dict(),
        ) from exc


def _parse_stage(stage: str) -> RolloutStage:
    try:
        return RolloutStage(stage.strip().lower())
    except ValueError as exc:
        valid = ", ".join(s.value for s in RolloutStage)
        raise HTTPException(
            status_code=400,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=f"Invalid stage {stage!r}. Valid stages: {valid}",
            ).to_dict(),
        ) from exc


def _rollout_id(node_id: str, lane: str) -> str:
    return f"{node_id}:{lane}"


def _parse_rollout_id(rollout_id: str) -> tuple[str, str]:
    raw = rollout_id.strip()
    if ":" not in raw:
        raise HTTPException(
            status_code=400,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=(
                    "Invalid rollout_id format. Expected '<node_id>:<lane>' "
                    "(e.g. 'prya:docs')."
                ),
            ).to_dict(),
        )
    node_id, lane = raw.split(":", 1)
    node_id = node_id.strip().lower()
    lane = _parse_lane(lane)
    if not node_id:
        raise HTTPException(
            status_code=400,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message="rollout_id must include non-empty node_id",
            ).to_dict(),
        )
    return node_id, lane


def _state_row(node_id: str, lane: str, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "rollout_id": _rollout_id(node_id, lane),
        "node_id": node_id,
        "lane": lane,
        "stage": state.get("stage", RolloutStage.disabled.value),
        "effective_stage": state.get("effective_stage", RolloutStage.disabled.value),
        "paused": bool(state.get("paused", False)),
        "pre_pause_stage": state.get("pre_pause_stage"),
        "updated_at": state.get("updated_at"),
    }


def _all_rollout_rows(
    *,
    node_filter: str | None = None,
    lane_filter: str | None = None,
) -> list[dict[str, Any]]:
    controller = get_canary_control()
    exported = controller.export_state()
    states = exported.get("states", {})

    rows: list[dict[str, Any]] = []
    for key, state in states.items():
        parts = key.split("/", 1)
        if len(parts) != 2:
            continue
        node_id, lane = parts
        if node_filter and node_id != node_filter:
            continue
        if lane_filter and lane != lane_filter:
            continue
        if not isinstance(state, dict):
            continue
        rows.append(_state_row(node_id, lane, state))

    rows.sort(key=lambda row: str(row.get("updated_at", "")), reverse=True)
    return rows


@router.get("/api/canary/rollouts")
async def list_canary_rollouts(
    node_id: str | None = Query(None, description="Filter by node_id"),
    lane: str | None = Query(None, description="Filter by lane"),
    limit: int = Query(100, ge=1, le=500),
    _=Depends(require_permission("tasks:read")),
):
    """List rollout states across nodes/lanes."""
    node_filter = node_id.strip().lower() if node_id else None
    lane_filter = _parse_lane(lane) if lane else None

    controller = get_canary_control()
    rows = _all_rollout_rows(node_filter=node_filter, lane_filter=lane_filter)[:limit]

    return JSONResponse(
        content=api_success_body(
            {
                "count": len(rows),
                "rollouts": rows,
                "summary": controller.get_summary().to_dict(),
            }
        )
    )


@router.post("/api/canary/rollouts")
async def create_canary_rollout(
    body: CreateCanaryRolloutRequest,
    _=Depends(require_permission("tasks:write")),
):
    """Create or update rollout stage for a (node, lane) pair."""
    node_id = body.node_id.strip().lower()
    lane = _parse_lane(body.lane)
    stage = _parse_stage(body.stage)
    controller = get_canary_control()

    transition = controller.set_stage(
        node_id=node_id,
        lane=lane,
        stage=stage,
        operator=body.operator.strip() or "api",
        reason=body.reason,
    )
    state = controller.get_state(node_id, lane)
    if state is None:
        raise HTTPException(
            status_code=500,
            detail=ForgeError(
                code="INTERNAL_ERROR",
                message="Rollout state missing after set_stage",
            ).to_dict(),
        )

    return JSONResponse(
        content=api_success_body(
            {
                "rollout": _state_row(node_id, lane, state.to_dict()),
                "transition": transition.to_dict(),
            }
        )
    )


@router.get("/api/canary/rollouts/{rollout_id}")
async def get_canary_rollout(
    rollout_id: str,
    _=Depends(require_permission("tasks:read")),
):
    """Get rollout state and recent transitions for one rollout ID."""
    node_id, lane = _parse_rollout_id(rollout_id)
    controller = get_canary_control()
    state = controller.get_state(node_id, lane)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=ForgeError(
                code="NOT_FOUND",
                message=f"Rollout {rollout_id!r} not found",
            ).to_dict(),
        )

    transitions = [
        transition.to_dict()
        for transition in controller.get_transitions(node_id=node_id, lane=lane, limit=20)
    ]
    return JSONResponse(
        content=api_success_body(
            {
                "rollout": _state_row(node_id, lane, state.to_dict()),
                "transitions": transitions,
            }
        )
    )


@router.post("/api/canary/rollouts/{rollout_id}/pause")
async def pause_canary_rollout(
    rollout_id: str,
    body: CanaryActionRequest,
    _=Depends(require_permission("tasks:write")),
):
    """Pause one rollout while preserving pre-pause stage."""
    node_id, lane = _parse_rollout_id(rollout_id)
    controller = get_canary_control()
    try:
        transition = controller.pause_pair(
            node_id=node_id,
            lane=lane,
            operator=body.operator.strip() or "api",
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=ForgeError(code=VALIDATION_ERROR, message=str(exc)).to_dict(),
        ) from exc

    state = controller.get_state(node_id, lane)
    if state is None:
        raise HTTPException(
            status_code=500,
            detail=ForgeError(code="INTERNAL_ERROR", message="Rollout state missing").to_dict(),
        )

    return JSONResponse(
        content=api_success_body(
            {
                "rollout": _state_row(node_id, lane, state.to_dict()),
                "transition": transition.to_dict(),
            }
        )
    )


@router.post("/api/canary/rollouts/{rollout_id}/resume")
async def resume_canary_rollout(
    rollout_id: str,
    body: CanaryActionRequest,
    _=Depends(require_permission("tasks:write")),
):
    """Resume one paused rollout to its pre-pause stage."""
    node_id, lane = _parse_rollout_id(rollout_id)
    controller = get_canary_control()
    try:
        transition = controller.resume_pair(
            node_id=node_id,
            lane=lane,
            operator=body.operator.strip() or "api",
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=ForgeError(code=VALIDATION_ERROR, message=str(exc)).to_dict(),
        ) from exc

    state = controller.get_state(node_id, lane)
    if state is None:
        raise HTTPException(
            status_code=500,
            detail=ForgeError(code="INTERNAL_ERROR", message="Rollout state missing").to_dict(),
        )

    return JSONResponse(
        content=api_success_body(
            {
                "rollout": _state_row(node_id, lane, state.to_dict()),
                "transition": transition.to_dict(),
            }
        )
    )


@router.post("/api/canary/rollouts/{rollout_id}/promote")
async def promote_canary_rollout(
    rollout_id: str,
    body: CanaryActionRequest,
    _=Depends(require_permission("tasks:write")),
):
    """Promote rollout one step (or to explicit target stage)."""
    node_id, lane = _parse_rollout_id(rollout_id)
    controller = get_canary_control()
    current = controller.get_state(node_id, lane)
    effective = current.effective_stage if current else RolloutStage.disabled

    if body.stage:
        target_stage = _parse_stage(body.stage)
    else:
        if effective == RolloutStage.disabled:
            target_stage = RolloutStage.canary
        elif effective == RolloutStage.canary:
            target_stage = RolloutStage.enabled
        else:
            raise HTTPException(
                status_code=409,
                detail=ForgeError(
                    code=VALIDATION_ERROR,
                    message=f"Rollout {rollout_id!r} is already at enabled stage",
                ).to_dict(),
            )

    transition = controller.set_stage(
        node_id=node_id,
        lane=lane,
        stage=target_stage,
        operator=body.operator.strip() or "api",
        reason=body.reason,
    )
    state = controller.get_state(node_id, lane)
    if state is None:
        raise HTTPException(
            status_code=500,
            detail=ForgeError(code="INTERNAL_ERROR", message="Rollout state missing").to_dict(),
        )

    return JSONResponse(
        content=api_success_body(
            {
                "rollout": _state_row(node_id, lane, state.to_dict()),
                "transition": transition.to_dict(),
            }
        )
    )


@router.post("/api/canary/rollouts/{rollout_id}/rollback")
async def rollback_canary_rollout(
    rollout_id: str,
    body: CanaryActionRequest,
    _=Depends(require_permission("tasks:write")),
):
    """Rollback rollout one stage down (enabled→canary→disabled)."""
    node_id, lane = _parse_rollout_id(rollout_id)
    controller = get_canary_control()
    try:
        transition = controller.revert(
            node_id=node_id,
            lane=lane,
            operator=body.operator.strip() or "api",
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=ForgeError(code=VALIDATION_ERROR, message=str(exc)).to_dict(),
        ) from exc

    state = controller.get_state(node_id, lane)
    if state is None:
        raise HTTPException(
            status_code=500,
            detail=ForgeError(code="INTERNAL_ERROR", message="Rollout state missing").to_dict(),
        )

    return JSONResponse(
        content=api_success_body(
            {
                "rollout": _state_row(node_id, lane, state.to_dict()),
                "transition": transition.to_dict(),
            }
        )
    )
