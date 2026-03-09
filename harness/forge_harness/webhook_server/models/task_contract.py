"""Task Contract Extension — Dark Factory Fields.

Defines the opt-in Dark Factory contract fields that can accompany any FORGE
task.  These fields are intentionally separate from the core task API so they
can be added to ``CreateTaskRequest`` in a subsequent ticket without breaking
existing callers.

Classes
-------
AcceptanceCheck
    A single automated verification step that the evaluator agent runs after
    the task agent declares the work complete.

EvaluatorProfile
    Describes how the evaluator should run the acceptance checks for a task
    and what to do when they pass or fail.

TaskContractExtension
    Top-level contract extension block.  Carries the Dark Factory routing
    fields (risk_tier, task_type) and derives *lane* and
    *requires_human_approval* automatically via a model validator.

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-0005)
docs/runbooks/LANE_POLICY_MATRIX.md
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from forge_harness.webhook_server.models.lane_policy import (
    Lane,
    RiskTier,
    TaskType,
    get_lane,
)
from forge_harness.webhook_server.models.lane_policy import (
    requires_human_approval as _requires_human_approval,
)

# ---------------------------------------------------------------------------
# AcceptanceCheck
# ---------------------------------------------------------------------------


class AcceptanceCheck(BaseModel):
    """A single automated verification step executed by the evaluator agent.

    The evaluator runs ``command`` in the task's working context.  If the
    command exits with a non-zero code and ``required`` is ``True``, the task
    is not eligible for auto-approval and must go to human review.

    Attributes:
        name:             Human-readable label for the check (e.g. "unit-tests").
        command:          Shell command the evaluator runs to verify completion.
        timeout_seconds:  Maximum wall-clock seconds before the check is treated
                          as failed.  Defaults to 300 (5 minutes).
        required:         When ``True`` a failure blocks automatic approval.
                          Non-required checks are informational only.
    """

    name: str = Field(
        ...,
        min_length=1,
        description="Human-readable label for this acceptance check.",
    )
    command: str = Field(
        ...,
        min_length=1,
        description="Shell command the evaluator runs to verify completion.",
    )
    timeout_seconds: int = Field(
        default=300,
        ge=1,
        le=86400,
        description="Maximum seconds before the check is treated as failed (default 300).",
    )
    required: bool = Field(
        default=True,
        description="When True a failure blocks automatic approval.",
    )

    model_config = {"frozen": False, "populate_by_name": True}


# ---------------------------------------------------------------------------
# EvaluatorProfile
# ---------------------------------------------------------------------------


class EvaluatorProfile(BaseModel):
    """Describes how the evaluator agent should assess a completed task.

    Attributes:
        checks:               Ordered list of acceptance checks to execute.
        auto_approve_on_pass: When ``True`` the evaluator marks the task
                              completed automatically if all *required* checks
                              pass.  When ``False`` (default) the outcome is
                              reported but a human must still approve.
        max_retries:          How many times the evaluator may re-run the
                              checks if a required check fails transiently
                              (e.g. a flaky network call).  Defaults to 1
                              (one retry, two total attempts).
    """

    checks: list[AcceptanceCheck] = Field(
        default_factory=list,
        description="Ordered list of acceptance checks to execute.",
    )
    auto_approve_on_pass: bool = Field(
        default=False,
        description=(
            "When True the evaluator marks the task completed automatically "
            "if all required checks pass."
        ),
    )
    max_retries: int = Field(
        default=1,
        ge=0,
        le=10,
        description="Maximum number of re-runs when a required check fails transiently.",
    )

    model_config = {"frozen": False, "populate_by_name": True}


# ---------------------------------------------------------------------------
# TaskContractExtension
# ---------------------------------------------------------------------------


class TaskContractExtension(BaseModel):
    """Dark Factory contract extension fields for a FORGE task.

    Callers supply ``risk_tier`` and ``task_type``; the model validator
    computes ``lane`` and ``requires_human_approval`` automatically.

    If either ``risk_tier`` or ``task_type`` is ``None`` (the defaults), the
    computed fields also remain ``None`` / ``False`` so that tasks that do
    not participate in the Dark Factory routing model are unaffected.

    Attributes:
        risk_tier:               Risk classification of this specific task
                                 instance.  ``None`` means unclassified.
        task_type:               Canonical work category.  ``None`` means
                                 unclassified.
        lane:                    Execution lane derived from
                                 ``(task_type, risk_tier)``.  ``None`` when
                                 either input is ``None``.  **Read-only** —
                                 do not set this field directly.
        acceptance_checks:       Flat list of checks (convenience shorthand).
                                 If an ``evaluator_profile`` is also provided
                                 both sets of checks are available; callers
                                 should use ``evaluator_profile.checks`` for
                                 the full evaluator configuration.
        evaluator_profile:       Full evaluator configuration, including retry
                                 policy and auto-approve behaviour.
        requires_human_approval: ``True`` when the computed lane is
                                 ``human_review`` or ``blocked``.  ``False``
                                 only for the ``autonomous`` lane.  Always
                                 ``False`` when no lane can be computed.
                                 **Read-only** — do not set this field directly.
    """

    # ------------------------------------------------------------------
    # Input fields (caller-supplied)
    # ------------------------------------------------------------------

    risk_tier: RiskTier | None = Field(
        default=None,
        description="Risk classification of this task instance.",
    )
    task_type: TaskType | None = Field(
        default=None,
        description="Canonical work category for Dark Factory routing.",
    )
    acceptance_checks: list[AcceptanceCheck] = Field(
        default_factory=list,
        description="Flat list of acceptance checks (shorthand; see evaluator_profile for full config).",
    )
    evaluator_profile: EvaluatorProfile | None = Field(
        default=None,
        description="Full evaluator configuration including retry policy and auto-approve behaviour.",
    )

    # ------------------------------------------------------------------
    # Computed fields (set by model_validator, should not be provided by caller)
    # ------------------------------------------------------------------

    lane: Lane | None = Field(
        default=None,
        description="Execution lane derived from (task_type, risk_tier). Computed automatically.",
    )
    requires_human_approval: bool = Field(
        default=False,
        description=(
            "True when the lane is human_review or blocked. "
            "Computed automatically from risk_tier and task_type."
        ),
    )

    model_config = {"frozen": False, "populate_by_name": True}

    # ------------------------------------------------------------------
    # Validator: derive lane + requires_human_approval
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _compute_lane_fields(self) -> TaskContractExtension:
        """Auto-compute ``lane`` and ``requires_human_approval``.

        Both fields are derived exclusively from ``risk_tier`` and
        ``task_type``.  When either is absent the task is treated as
        unclassified and the computed fields are left at their defaults
        (``None`` and ``False`` respectively).

        Any caller-supplied values for ``lane`` or ``requires_human_approval``
        are overwritten by this validator to ensure consistency.
        """
        if self.risk_tier is not None and self.task_type is not None:
            self.lane = get_lane(self.task_type, self.risk_tier)
            self.requires_human_approval = _requires_human_approval(self.task_type, self.risk_tier)
        else:
            self.lane = None
            self.requires_human_approval = False
        return self
