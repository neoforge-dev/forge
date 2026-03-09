"""Lane Policy API Endpoints — DF-4001.

Exposes runtime lane-policy controls via the Command Center API.

Endpoints:
    GET  /api/lanes/policy         - List all lane policies
    GET  /api/lanes/{lane}/policy  - Get a specific lane policy
    POST /api/lanes/{lane}/policy  - Update a specific lane policy
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from forge_harness.dark_factory.lane_policy_enforcer import get_lane_policy_enforcer
from forge_harness.error_schema import VALIDATION_ERROR, ForgeError, api_success_body
from forge_harness.webhook_server.core.dependencies import require_permission
from forge_harness.webhook_server.models.work_cell import WorkCellLane
from forge_harness.webhook_server.services.audit import get_audit_logger

router = APIRouter()


class UpdateLanePolicyRequest(BaseModel):
    """Mutable lane-policy fields for a single lane."""

    can_auto_complete: bool | None = None
    requires_human_review: bool | None = None
    is_hard_gated: bool | None = None
    operator: str = "api"
    reason: str = ""


def _valid_lanes() -> str:
    return ", ".join(sorted(lane.value for lane in WorkCellLane))


def _parse_lane(lane: str) -> WorkCellLane:
    raw = lane.strip().lower()
    try:
        return WorkCellLane(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=f"Invalid lane {lane!r}. Valid lanes: {_valid_lanes()}",
            ).to_dict(),
        ) from exc


def _policy_row(
    lane: str,
    flags: dict[str, Any],
    *,
    policy_version: str = "default",
) -> dict[str, Any]:
    return {
        "lane": lane,
        "policy_version": policy_version,
        "can_auto_complete": bool(flags.get("can_auto_complete", False)),
        "requires_human_review": bool(flags.get("requires_human_review", False)),
        "is_hard_gated": bool(flags.get("is_hard_gated", False)),
    }


@router.get("/api/lanes/policy")
async def list_lane_policies(
    _=Depends(require_permission("tasks:read")),
):
    """Return all configured lane policies."""
    enforcer = get_lane_policy_enforcer()
    snapshot = enforcer.get_config_snapshot()
    rows = [
        _policy_row(lane, flags)
        for lane, flags in sorted(snapshot.items(), key=lambda item: item[0])
    ]

    return JSONResponse(
        content=api_success_body(
            {
                "policy_version": "default",
                "count": len(rows),
                "lanes": rows,
            }
        )
    )


@router.get("/api/lanes/{lane}/policy")
async def get_lane_policy(
    lane: str,
    _=Depends(require_permission("tasks:read")),
):
    """Return policy flags for a single lane."""
    lane_enum = _parse_lane(lane)
    enforcer = get_lane_policy_enforcer()
    snapshot = enforcer.get_config_snapshot()
    flags = snapshot.get(lane_enum.value, {})

    return JSONResponse(
        content=api_success_body(
            _policy_row(lane_enum.value, flags)
        )
    )


@router.post("/api/lanes/{lane}/policy")
async def update_lane_policy(
    lane: str,
    body: UpdateLanePolicyRequest,
    _=Depends(require_permission("tasks:write")),
):
    """Update one lane policy in-place (partial update)."""
    lane_enum = _parse_lane(lane)

    updates: dict[str, bool] = {}
    if body.can_auto_complete is not None:
        updates["can_auto_complete"] = body.can_auto_complete
    if body.requires_human_review is not None:
        updates["requires_human_review"] = body.requires_human_review
    if body.is_hard_gated is not None:
        updates["is_hard_gated"] = body.is_hard_gated

    if not updates:
        raise HTTPException(
            status_code=400,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=(
                    "At least one policy field is required: "
                    "can_auto_complete, requires_human_review, is_hard_gated"
                ),
            ).to_dict(),
        )

    enforcer = get_lane_policy_enforcer()
    try:
        enforcer.update_config({lane_enum.value: updates})
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=ForgeError(code=VALIDATION_ERROR, message=str(exc)).to_dict(),
        ) from exc

    # Best-effort audit trail; does not block API response.
    try:
        audit_logger = get_audit_logger()
        await audit_logger.log_configuration_change(
            config_key=f"lane_policy:{lane_enum.value}",
            actor={"id": body.operator or "api", "type": "system"},
            context={"updates": updates, "reason": body.reason},
        )
    except Exception:
        pass

    snapshot = enforcer.get_config_snapshot()
    flags = snapshot.get(lane_enum.value, {})
    return JSONResponse(
        content=api_success_body(
            {
                "updated": updates,
                "policy": _policy_row(lane_enum.value, flags),
            }
        )
    )
