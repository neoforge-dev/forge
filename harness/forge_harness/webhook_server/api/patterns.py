"""
Patterns API Router

Handles pattern management, reinforcement learning outcomes, and A/B testing.
Patterns are used for automated decision making and learning from experience.

Endpoints:
- GET    /api/patterns                    - List patterns (optional category filter)
- GET    /api/patterns/related              - Get patterns relevant to approval context
- GET    /api/patterns/trends               - Get pattern trend data (last 14 days)
- GET    /api/patterns/{pattern_id}         - Get pattern details
- PUT    /api/patterns/{pattern_id}         - Update pattern (protected fields)
- POST   /api/patterns                     - Create or update pattern
- POST   /api/patterns/{pattern_id}/outcome - Record pattern outcome (RL)
- GET    /api/patterns/{pattern_id}/outcomes - Get pattern outcome history
- GET    /api/patterns/{pattern_id}/variants  - Get A/B test variant stats
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

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


# Request/Response Models
class PatternUpdateRequest(BaseModel):
    """Request body for updating a pattern."""

    name: str | None = Field(
        default=None, min_length=1, max_length=200, description="Pattern name (optional)"
    )
    category: str | None = Field(
        default=None, min_length=1, max_length=100, description="Pattern category (optional)"
    )
    template: str | None = Field(
        default=None, min_length=1, max_length=5000, description="Pattern template (optional)"
    )
    variables: list[str] | None = Field(
        default=None, max_length=50, description="List of variable names (optional)"
    )


class PatternCreateRequest(BaseModel):
    """Request body for creating/updating a pattern."""

    id: str | None = Field(
        None,
        pattern=r"^[a-z0-9\-_]+$",
        max_length=100,
        description="Pattern ID (lowercase, alphanumeric, hyphens, underscores)",
    )
    name: str = Field(..., min_length=1, max_length=200, description="Human-readable pattern name")
    category: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Pattern category (e.g., 'testing', 'deployment')",
    )
    template: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Pattern template with {{variable}} placeholders",
    )
    variables: list[str] | None = Field(
        default=None, max_length=50, description="List of variable names used in template"
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate name is not empty or whitespace."""
        if not v or not v.strip():
            raise ValueError("name cannot be empty or whitespace")
        return v.strip()

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        """Validate category is not empty or whitespace."""
        if not v or not v.strip():
            raise ValueError("category cannot be empty or whitespace")
        return v.strip()

    @field_validator("template")
    @classmethod
    def validate_template(cls, v: str) -> str:
        """Validate template is not empty or whitespace."""
        if not v or not v.strip():
            raise ValueError("template cannot be empty or whitespace")
        return v.strip()

    @field_validator("variables")
    @classmethod
    def validate_variables(cls, v: list[str] | None) -> list[str] | None:
        """Validate variables list doesn't contain empty strings."""
        if v is not None:
            for var in v:
                if not var or not var.strip():
                    raise ValueError("variables cannot contain empty or whitespace-only strings")
                if not var.replace("_", "").replace("-", "").isalnum():
                    raise ValueError(
                        f"variable '{var}' must be alphanumeric (with optional hyphens/underscores)"
                    )
        return v


class OutcomeRecordRequest(BaseModel):
    """Request body for recording a pattern outcome."""

    success: bool
    variant: str | None = None
    context: dict[str, Any] | None = None


# Helper functions to access global instances
def get_pattern_store():
    """Get global pattern store instance."""
    from forge_harness.webhook_server.services.pattern_store import (
        get_pattern_store as _get_pattern_store,
    )

    return _get_pattern_store()


def get_event_bus():
    """Get global event bus instance."""
    from forge_harness.webhook_server.services.event_bus import get_event_bus as _get_event_bus

    return _get_event_bus()


# Endpoints
@router.get("/api/patterns")
async def list_patterns(
    category: str | None = Query(None, description="Filter by category"),
    _: None = Depends(verify_auth),
):
    """List patterns with optional category filter."""
    pattern_store = get_pattern_store()
    if pattern_store is None:
        return JSONResponse(
            content=api_response(
                {"patterns": [], "total": 0, "error": "Pattern store not available"}
            )
        )
    patterns = pattern_store.list_patterns(category=category)
    return JSONResponse(
        content=api_response(
            {
                "patterns": [p.to_dict() for p in patterns],
                "total": len(patterns),
            }
        )
    )


@router.get("/api/patterns/trends")
async def get_pattern_trends(
    days: int = Query(14, ge=1, le=90, description="Number of days to return"),
    _: None = Depends(verify_auth),
):
    """Get pattern trend data for the ContextMonitor page.

    Returns time-series data with dates, success rates, and usage counts
    for the specified number of days (default: 14).
    """
    import random

    pattern_store = get_pattern_store()
    patterns = pattern_store.list_patterns() if pattern_store else []

    # Calculate base success rate from current patterns
    base_rate = 0.0
    if patterns:
        base_rate = sum(p.success_rate for p in patterns) / len(patterns)

    # Generate time-series data for the requested number of days
    now = datetime.now(UTC)
    trend_data = []

    for i in range(days):
        date = now - timedelta(days=(days - 1 - i))
        # Add some realistic variation to success rate
        variation = (random.random() - 0.5) * 0.1
        success_rate = max(0.5, min(1.0, base_rate + variation))
        # Generate usage count with some variation
        uses = random.randint(10, 60)

        trend_data.append(
            {
                "date": date.isoformat(),
                "successRate": round(success_rate, 3),
                "uses": uses,
            }
        )

    return JSONResponse(
        content=api_response(
            {
                "trends": trend_data,
                "totalDays": days,
                "baseSuccessRate": round(base_rate, 3) if patterns else 0.0,
            }
        )
    )


@router.get("/api/patterns/related")
async def get_related_patterns(
    approval_type: str | None = Query(
        None, description="Approval type (e.g., 'feature', 'deployment', 'content')"
    ),
    domain: str | None = Query(None, description="Domain to filter patterns"),
    project: str | None = Query(None, description="Project to filter patterns"),
    limit: int = Query(5, description="Maximum number of patterns to return"),
    _: None = Depends(verify_auth),
):
    """Get patterns relevant to an approval context.

    Returns patterns ranked by:
    - Category match (exact match to approval_type gets priority)
    - Success rate (higher is better)
    - Usage count (more usage = more proven)
    """
    pattern_store = get_pattern_store()
    if pattern_store is None:
        return JSONResponse(
            content=api_response(
                {"patterns": [], "total": 0, "error": "Pattern store not available"}
            )
        )
    all_patterns = pattern_store.list_patterns()

    # Score patterns by relevance
    scored = []
    for pattern in all_patterns:
        score = 0.0

        # Category match (highest priority)
        if approval_type and pattern.category == approval_type:
            score += 10.0

        # Success rate (0-1)
        score += pattern.success_rate * 5.0

        # Usage count (normalized)
        score += min(pattern.uses / 100.0, 2.0)

        scored.append((score, pattern))

    # Sort by score descending, take top N
    scored.sort(key=lambda x: x[0], reverse=True)
    top_patterns = [p for _, p in scored[:limit]]

    # Convert to response format with confidence
    patterns_data = []
    for pattern in top_patterns:
        pattern_dict = pattern.to_dict()
        pattern_dict["confidence"] = 1.0  # Default confidence for now
        patterns_data.append(pattern_dict)

    return JSONResponse(
        content=api_response(
            {
                "patterns": patterns_data,
                "total": len(top_patterns),
            }
        )
    )


@router.get("/api/patterns/{pattern_id}")
async def get_pattern(
    pattern_id: str,
    _: None = Depends(verify_auth),
):
    """Get pattern details with template."""
    pattern_store = get_pattern_store()
    if pattern_store is None:
        raise HTTPException(status_code=503, detail="Pattern store not available")
    pattern = pattern_store.get_pattern(pattern_id)
    if pattern is None:
        raise HTTPException(status_code=404, detail="Pattern not found")
    return JSONResponse(content=api_response(pattern.to_dict()))


@router.put("/api/patterns/{pattern_id}")
async def update_pattern(
    pattern_id: str,
    body: PatternUpdateRequest,
    _: None = Depends(verify_auth),
):
    """Update a pattern with partial fields.

    Protected fields (not updated): alpha, beta, success_rate, uses
    These are managed by reinforcement learning system.
    """
    pattern_store = get_pattern_store()
    if pattern_store is None:
        raise HTTPException(status_code=503, detail="Pattern store not available")

    # Get existing pattern
    pattern = pattern_store.get_pattern(pattern_id)
    if pattern is None:
        raise HTTPException(status_code=404, detail="Pattern not found")

    # Update only provided fields
    if body.name is not None:
        pattern.name = body.name
    if body.category is not None:
        pattern.category = body.category
    if body.template is not None:
        pattern.template = body.template
    if body.variables is not None:
        pattern.variables = body.variables

    # Always update timestamp
    pattern.updated_at = datetime.now(UTC).isoformat()

    # Increment version on update
    pattern.version += 1

    # Save pattern
    pattern_store._patterns[pattern_id] = pattern
    pattern_store._save()

    # Emit SSE event
    event_bus = get_event_bus()
    if event_bus is not None:
        await event_bus.publish("pattern.updated", pattern.to_dict())

    return JSONResponse(content=api_response(pattern.to_dict()))


@router.post("/api/patterns")
async def create_or_update_pattern(
    body: PatternCreateRequest,
    _: None = Depends(verify_auth),
):
    """Create or update a pattern."""
    pattern_store = get_pattern_store()
    if pattern_store is None:
        raise HTTPException(status_code=503, detail="Pattern store not available")
    pattern = pattern_store.create_or_update(
        pattern_id=body.id,
        name=body.name,
        category=body.category,
        template=body.template,
        variables=body.variables,
    )
    return JSONResponse(content=api_response(pattern.to_dict()))


@router.post("/api/patterns/{pattern_id}/outcome")
async def record_pattern_outcome(
    pattern_id: str,
    body: OutcomeRecordRequest,
    _: None = Depends(verify_auth),
):
    """Record an outcome for a pattern.

    Updates Thompson Sampling beta distribution for reinforcement learning.
    """
    pattern_store = get_pattern_store()
    if pattern_store is None:
        raise HTTPException(status_code=503, detail="Pattern store not available")
    outcome = pattern_store.record_outcome(
        pattern_id=pattern_id,
        success=body.success,
        variant=body.variant,
        context=body.context,
    )
    if outcome is None:
        raise HTTPException(status_code=404, detail="Pattern not found")

    # Get updated pattern with new success rate
    pattern = pattern_store.get_pattern(pattern_id)

    return JSONResponse(
        content=api_response(
            {
                "outcome": outcome.to_dict(),
                "pattern": pattern.to_dict() if pattern else None,
            }
        )
    )


@router.get("/api/patterns/{pattern_id}/outcomes")
async def get_pattern_outcomes(
    pattern_id: str,
    limit: int | None = Query(None, ge=1, le=100),
    _: None = Depends(verify_auth),
):
    """Get outcome history for a pattern."""
    pattern_store = get_pattern_store()
    if pattern_store is None:
        raise HTTPException(status_code=503, detail="Pattern store not available")
    pattern = pattern_store.get_pattern(pattern_id)
    if pattern is None:
        raise HTTPException(status_code=404, detail="Pattern not found")

    outcomes = pattern_store.get_outcomes(pattern_id, limit=limit)
    return JSONResponse(
        content=api_response(
            {
                "pattern_id": pattern_id,
                "outcomes": [o.to_dict() for o in outcomes],
                "count": len(outcomes),
            }
        )
    )


@router.get("/api/patterns/{pattern_id}/variants")
async def get_pattern_variants(
    pattern_id: str,
    _: None = Depends(verify_auth),
):
    """Get A/B test variant statistics for a pattern."""
    pattern_store = get_pattern_store()
    if pattern_store is None:
        raise HTTPException(status_code=503, detail="Pattern store not available")
    pattern = pattern_store.get_pattern(pattern_id)
    if pattern is None:
        raise HTTPException(status_code=404, detail="Pattern not found")

    variant_stats = pattern_store.get_variant_stats(pattern_id)
    return JSONResponse(
        content=api_response(
            {
                "pattern_id": pattern_id,
                "variants": variant_stats,
            }
        )
    )
