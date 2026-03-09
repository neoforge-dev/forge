"""Dark Factory Audit API Endpoints — DF-4003.

Endpoints:
    GET  /api/audit/config    - Current audit sampler configuration
    POST /api/audit/config    - Update audit sampler configuration
    GET  /api/audit/queue     - List sampled audits by status
    POST /api/audit/sample    - Manually trigger audit sampling for a task
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forge_harness.dark_factory.audit_sampler import (
    MAX_SAMPLE_RATE,
    MIN_SAMPLE_RATE,
    get_audit_sampler,
)
from forge_harness.error_schema import VALIDATION_ERROR, ForgeError, api_success_body
from forge_harness.webhook_server.core.dependencies import require_permission
from forge_harness.webhook_server.services.audit import get_audit_logger

router = APIRouter()

_VALID_QUEUE_STATUSES = {"pending", "passed", "failed", "skipped", "all"}


class UpdateAuditConfigRequest(BaseModel):
    """Request body for updating audit sampler configuration."""

    sample_rate: float = Field(..., ge=MIN_SAMPLE_RATE, le=MAX_SAMPLE_RATE)
    operator: str = "api"
    reason: str = ""


class ManualAuditSampleRequest(BaseModel):
    """Request body for manually triggering audit sampling."""

    task_id: str = Field(..., min_length=1)
    lane: str = "manual"


@router.get("/api/audit/config")
async def get_audit_config(
    _=Depends(require_permission("tasks:read")),
):
    """Return current audit-sampling config and summary metrics."""
    sampler = get_audit_sampler()
    pending = sampler.get_pending_audits()
    records = sampler.get_all_records()
    summary = sampler.get_daily_summary()

    return JSONResponse(
        content=api_success_body(
            {
                "sample_rate": sampler.sample_rate,
                "min_sample_rate": MIN_SAMPLE_RATE,
                "max_sample_rate": MAX_SAMPLE_RATE,
                "pending_count": len(pending),
                "total_records": len(records),
                "summary": summary.to_dict(),
            }
        )
    )


@router.post("/api/audit/config")
async def update_audit_config(
    body: UpdateAuditConfigRequest,
    _=Depends(require_permission("tasks:write")),
):
    """Update audit sample rate (bounded by DF sampler limits)."""
    sampler = get_audit_sampler()
    previous = sampler.sample_rate
    updated = sampler.update_config(body.sample_rate)

    # Best-effort audit trail; does not block API response.
    try:
        audit_logger = get_audit_logger()
        await audit_logger.log_configuration_change(
            config_key="audit_sampler:sample_rate",
            actor={"id": body.operator or "api", "type": "system"},
            context={"previous": previous, "updated": updated, "reason": body.reason},
        )
    except Exception:
        pass

    return JSONResponse(
        content=api_success_body(
            {
                "sample_rate": updated,
                "previous_sample_rate": previous,
            }
        )
    )


@router.post("/api/audit/sample")
async def trigger_audit_sample(
    body: ManualAuditSampleRequest,
    _=Depends(require_permission("tasks:write")),
):
    """Manually trigger audit sampling for a task."""
    sampler = get_audit_sampler()
    decision = sampler.should_audit(task_id=body.task_id, lane=body.lane)

    return JSONResponse(
        content=api_success_body(
            {
                "task_id": decision.task_id,
                "selected": decision.selected,
                "decision_id": decision.decision_id,
                "random_value": decision.random_value,
                "sample_rate": decision.sample_rate,
            }
        )
    )


@router.get("/api/audit/queue")
async def get_audit_queue(
    status: str = Query("pending", description="pending|passed|failed|skipped|all"),
    limit: int = Query(50, ge=1, le=500),
    _=Depends(require_permission("tasks:read")),
):
    """Return sampled audit records filtered by review status."""
    status_norm = status.strip().lower()
    if status_norm not in _VALID_QUEUE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=ForgeError(
                code=VALIDATION_ERROR,
                message=(
                    f"Invalid status {status!r}. "
                    f"Valid values: {', '.join(sorted(_VALID_QUEUE_STATUSES))}"
                ),
            ).to_dict(),
        )

    sampler = get_audit_sampler()
    records = sampler.get_all_records()
    rows = [record.to_dict() for record in records]
    if status_norm != "all":
        rows = [row for row in rows if row.get("status") == status_norm]

    rows.sort(key=lambda row: str(row.get("decision_timestamp", "")), reverse=True)
    rows = rows[:limit]

    return JSONResponse(
        content=api_success_body(
            {
                "status": status_norm,
                "count": len(rows),
                "audits": rows,
            }
        )
    )
