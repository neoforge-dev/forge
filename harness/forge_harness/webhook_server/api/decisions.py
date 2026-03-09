"""
Decisions API Router

Handles decision logs from the meta-learning system for reinforcement learning
visualization and analysis.

Endpoints:
- GET /api/decisions - List decisions with optional filtering
- GET /api/decisions/{decision_id} - Get specific decision details
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from forge_harness.webhook_server.core.dependencies import verify_auth

router = APIRouter()


def api_response(data):
    """Create standardized API response."""
    return {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def get_learning_store():
    """Get global learning store instance."""
    from forge_harness.webhook_server_main import get_learning_store as _get_learning_store
    return _get_learning_store()


# Endpoints
@router.get("/api/decisions")
async def list_decisions(
    context: str | None = Query(None, description="Filter by context signature"),
    domain: str | None = Query(None, description="Filter by domain"),
    project: str | None = Query(None, description="Filter by project (requires domain)"),
    limit: int = Query(50, ge=1, le=200, description="Max decisions to return"),
    _: None = Depends(verify_auth),
):
    """List decisions from the meta-learning system.

    Returns decision logs that can be used for reinforcement learning
    visualization and analysis.
    """
    learning_store = get_learning_store()

    if learning_store is None:
        # Return empty result if learning store not available
        return JSONResponse(
            content=api_response(
                {
                    "decisions": [],
                    "count": 0,
                    "note": "Learning store not available",
                }
            )
        )

    # Filter decisions based on query parameters
    if domain and project:
        decisions = learning_store.get_decisions_by_domain_project(domain, project)
    elif context:
        decisions = learning_store.get_decisions_by_context(context)
    else:
        # Get all decisions (limit by most recent)
        decisions = learning_store.get_decisions_by_context("")[:limit]

    # Convert to dict and limit results
    decision_dicts = [d.to_dict() for d in decisions[:limit]]

    return JSONResponse(
        content=api_response(
            {
                "decisions": decision_dicts,
                "count": len(decision_dicts),
            }
        )
    )


@router.get("/api/decisions/{decision_id}")
async def get_decision(
    decision_id: str,
    _: None = Depends(verify_auth),
):
    """Get details of a specific decision."""
    learning_store = get_learning_store()

    if learning_store is None:
        raise HTTPException(
            status_code=503,
            detail="Learning store not available",
        )

    decision = learning_store.get_decision(decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")

    return JSONResponse(content=api_response(decision.to_dict()))
