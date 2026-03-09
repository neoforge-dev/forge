"""Lane Graduation Policy API Endpoints — DF-5005.

REST endpoint for checking whether a Dark Factory work-cell lane is eligible
to graduate from human-supervised to fully autonomous operation.

Endpoints:
    POST /api/lanes/{lane}/check-graduation — Evaluate graduation eligibility
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forge_harness.error_schema import (
    api_success_body,
)
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class CheckGraduationRequest(BaseModel):
    """Metrics snapshot supplied by the caller for the graduation check.

    All rate fields must be in the range [0.0, 1.0].  ``sample_size`` must
    be a non-negative integer.

    Fields:
        evaluator_pass_rate: Fraction of tasks that passed the automated
            evaluator in the measurement window.
        requeue_rate: Fraction of tasks that were re-queued.
        escaped_defects: Count of defects that bypassed the evaluator.
        human_touch_ratio: Fraction of tasks with human intervention.
        sample_size: Total number of tasks in the measurement window.
    """

    evaluator_pass_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Fraction of tasks that passed the evaluator [0.0, 1.0].",
    )
    requeue_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Fraction of tasks that were re-queued [0.0, 1.0].",
    )
    escaped_defects: int = Field(
        ...,
        ge=0,
        description="Count of defects that escaped the evaluator (must be >= 0).",
    )
    human_touch_ratio: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Fraction of tasks with human intervention [0.0, 1.0].",
    )
    sample_size: int = Field(
        ...,
        ge=0,
        description="Total number of tasks in the measurement window (must be >= 0).",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_policy():
    """Lazy import of the graduation policy singleton to avoid circular imports."""
    from forge_harness.webhook_server.services.graduation_policy import (
        get_graduation_policy,
    )

    return get_graduation_policy()


def _api_response(data: Any) -> dict[str, Any]:
    """Build a canonical success response body."""
    return api_success_body(data)


def _decision_to_dict(decision) -> dict[str, Any]:
    """Serialise a GraduationDecision to a plain dict for JSON responses."""
    return {
        "lane": decision.lane,
        "eligible": decision.eligible,
        "blockers": decision.blockers,
        "recommended_action": decision.recommended_action,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/api/lanes/{lane}/check-graduation")
async def check_lane_graduation(
    lane: str,
    body: CheckGraduationRequest,
):
    """Evaluate whether a work-cell lane is eligible for graduation.

    Graduation means the lane can operate fully autonomously without requiring
    human oversight.  Hard-blocked lanes (``auth``, ``security``, ``deploy``,
    ``billing``) are **never** eligible regardless of metrics.

    Args:
        lane: Work-cell lane identifier (path parameter), e.g. ``api_simple``.
        body: Current operational metrics for the lane.

    Returns:
        200 with a ``GraduationDecision`` JSON body on all successful
        evaluations (including ineligible lanes — the decision object contains
        the blockers list).

    Example request body::

        {
            "evaluator_pass_rate": 0.97,
            "requeue_rate": 0.02,
            "escaped_defects": 0,
            "human_touch_ratio": 0.05,
            "sample_size": 50
        }

    Example response (eligible)::

        {
            "status": "success",
            "data": {
                "lane": "api_simple",
                "eligible": true,
                "blockers": [],
                "recommended_action": "graduate"
            }
        }

    Example response (ineligible)::

        {
            "status": "success",
            "data": {
                "lane": "api_simple",
                "eligible": false,
                "blockers": [
                    "Evaluator pass rate too low: 0.9000 (minimum 0.95 required)."
                ],
                "recommended_action": "monitor"
            }
        }
    """
    from forge_harness.webhook_server.services.graduation_policy import LaneMetrics

    policy = _get_policy()
    metrics = LaneMetrics(
        evaluator_pass_rate=body.evaluator_pass_rate,
        requeue_rate=body.requeue_rate,
        escaped_defects=body.escaped_defects,
        human_touch_ratio=body.human_touch_ratio,
        sample_size=body.sample_size,
    )

    decision = policy.check_graduation_eligibility(lane=lane, metrics=metrics)

    logger.info(
        "check_lane_graduation: lane=%s eligible=%s action=%s",
        lane,
        decision.eligible,
        decision.recommended_action,
    )

    return JSONResponse(content=_api_response(_decision_to_dict(decision)))
