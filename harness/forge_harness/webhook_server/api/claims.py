"""Claims API Endpoints

REST API endpoints for evidence-based claims management.
Integrates with ClaimsService for claim submission, verification, and querying.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.core.dependencies import require_permission
from forge_harness.webhook_server.models.claim import (
    ClaimStatus,
)

logger = get_logger(__name__)

router = APIRouter()


# =============================================================================
# Helper Functions
# =============================================================================

def api_response(data: Any = None, error_code: str | None = None, error_message: str | None = None) -> dict[str, Any]:
    """Create a standardized API response."""
    from datetime import UTC, datetime
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


async def get_claims_service():
    """Get the claims service singleton."""
    from forge_harness.webhook_server.services.claims_service import (
        get_claims_service as _get_claims_service,
    )
    return _get_claims_service()


async def get_event_bus():
    """Get the event bus singleton."""
    from forge_harness.webhook_server.services.event_bus import get_event_bus as _get_event_bus
    return _get_event_bus()


# =============================================================================
# Request Models
# =============================================================================

class VerifyClaimRequest(BaseModel):
    """Request body for claim verification (approve/reject)."""

    action: str = Field(
        ..., description="Action to take: 'approve' or 'reject'"
    )
    approver: str = Field(
        ..., min_length=1, max_length=200, description="Person or system performing the verification"
    )
    reason: str | None = Field(
        None, max_length=2000, description="Reason for rejection (required if action is 'reject')"
    )


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/api/claims")
async def list_claims(
    status: str | None = Query(None, description="Filter by status (submitted, verifying, approved, rejected)"),
    limit: int = Query(100, ge=1, le=500, description="Maximum results"),
    _=Depends(require_permission("claims:read")),
):
    """List claims with optional filtering by status.

    Returns claims ordered by submission time (oldest first).
    """
    service = await get_claims_service()

    # Parse status filter if provided
    status_filter = None
    if status:
        try:
            status_filter = ClaimStatus(status.lower())
        except ValueError:
            valid_statuses = [s.value for s in ClaimStatus]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Valid values: {valid_statuses}"
            )

    claims = service.list_claims(status=status_filter)

    # Apply limit
    claims = claims[:limit]

    # Serialize claims
    claims_data = []
    for claim in claims:
        claim_dict = claim.model_dump()
        # Convert datetime to ISO string
        claim_dict["submitted_at"] = claim.submitted_at.isoformat()
        if claim.verified_at:
            claim_dict["verified_at"] = claim.verified_at.isoformat()
        # Convert status enum to string
        claim_dict["status"] = claim.status.value if isinstance(claim.status, ClaimStatus) else claim.status
        claims_data.append(claim_dict)

    return JSONResponse(
        content=api_response({
            "claims": claims_data,
            "count": len(claims_data),
        })
    )


@router.get("/api/claims/stats")
async def get_claim_stats(
    _=Depends(require_permission("claims:read")),
):
    """Get claim counts by status.

    Returns a dict mapping each status to its count.
    """
    service = await get_claims_service()
    stats = service.get_stats()
    return JSONResponse(content=api_response(stats))


@router.get("/api/claims/{claim_id}")
async def get_claim(
    claim_id: str,
    _=Depends(require_permission("claims:read")),
):
    """Get a single claim by ID."""
    service = await get_claims_service()
    claim = service.get_claim(claim_id)

    if claim is None:
        raise HTTPException(status_code=404, detail=f"Claim not found: {claim_id}")

    claim_dict = claim.model_dump()
    claim_dict["submitted_at"] = claim.submitted_at.isoformat()
    if claim.verified_at:
        claim_dict["verified_at"] = claim.verified_at.isoformat()
    claim_dict["status"] = claim.status.value if isinstance(claim.status, ClaimStatus) else claim.status

    return JSONResponse(content=api_response(claim_dict))


@router.post("/api/claims/{claim_id}/verify")
async def verify_claim(
    claim_id: str,
    body: VerifyClaimRequest,
    request: Request,
    _=Depends(require_permission("claims:write")),
):
    """Verify (approve or reject) a claim.

    This endpoint allows manual override of the automatic verification process.
    - Use action='approve' to approve a claim
    - Use action='reject' to reject a claim (reason is required)

    The claim will transition through the appropriate states:
    - submitted -> verifying -> approved/rejected
    """
    service = await get_claims_service()

    # Validate action
    if body.action.lower() not in ("approve", "reject"):
        raise HTTPException(
            status_code=400,
            detail="Invalid action. Must be 'approve' or 'reject'"
        )

    # Require reason for rejection
    if body.action.lower() == "reject" and not body.reason:
        raise HTTPException(
            status_code=400,
            detail="Reason is required when rejecting a claim"
        )

    # Get the claim first
    claim = service.get_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Claim not found: {claim_id}")

    # Check if already in terminal state
    if claim.status in (ClaimStatus.APPROVED, ClaimStatus.REJECTED):
        raise HTTPException(
            status_code=400,
            detail=f"Claim is already {claim.status.value} and cannot be modified"
        )

    try:
        if body.action.lower() == "approve":
            # Run automatic verification which will approve if all checks pass
            claim = service.verify(claim_id)
            result_status = claim.status.value
        else:
            # For manual rejection, we need to transition through verifying first
            # then reject with the provided reason
            from datetime import UTC, datetime

            from forge_harness.webhook_server.models.claim import validate_claim_transition

            # Transition to verifying first (if in submitted state)
            if claim.status == ClaimStatus.SUBMITTED:
                validate_claim_transition(claim.status, ClaimStatus.VERIFYING)
                claim = claim.model_copy(update={"status": ClaimStatus.VERIFYING})
                service._persist_claim(claim)

            # Now reject
            validate_claim_transition(claim.status, ClaimStatus.REJECTED)
            claim = claim.model_copy(
                update={
                    "status": ClaimStatus.REJECTED,
                    "verified_at": datetime.now(UTC),
                    "rejection_reason": body.reason,
                }
            )
            service._persist_claim(claim)
            result_status = "rejected"

        # Emit SSE event
        event_bus = await get_event_bus()
        await event_bus.publish(
            "claim.verified",
            {
                "claim_id": claim_id,
                "action": body.action.lower(),
                "approver": body.approver,
                "reason": body.reason,
                "status": result_status,
            },
        )

        # Prepare response
        claim_dict = claim.model_dump()
        claim_dict["submitted_at"] = claim.submitted_at.isoformat()
        if claim.verified_at:
            claim_dict["verified_at"] = claim.verified_at.isoformat()
        claim_dict["status"] = claim.status.value if isinstance(claim.status, ClaimStatus) else claim.status

        return JSONResponse(
            content=api_response({
                "success": True,
                "claim": claim_dict,
                "action": body.action.lower(),
            })
        )

    except Exception as e:
        logger.error(f"Error verifying claim {claim_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
