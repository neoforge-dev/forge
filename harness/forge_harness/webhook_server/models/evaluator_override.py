"""Evaluator Override Protocol — Models and Policy Enforcement.
==============================================================

Defines the data model for audited evaluator overrides and the policy engine
that determines whether a given override is permissible.

An evaluator override is an exceptional action that allows a task to reach a
terminal or recovery state without all required acceptance checks passing.
Every override must be recorded in the audit trail with the fields defined by
:class:`EvaluatorOverride`.

Classes
-------
OverrideType
    Enum of the three override mechanisms.

OverriderRole
    Enum of the roles authorised to issue overrides.

EvaluatorOverride
    Pydantic model capturing the full audit record for a single override.

OverrideViolationError
    Exception raised when a proposed override violates the policy.

OverridePolicy
    Stateless policy engine.  Call :meth:`OverridePolicy.validate_override` to
    check whether an :class:`EvaluatorOverride` is permissible under the
    current rules.

Reference
---------
docs/runbooks/EVALUATOR_OVERRIDE_PROTOCOL.md  (DF-1006)
harness/forge_harness/webhook_server/models/lane_policy.py
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from forge_harness.webhook_server.models.lane_policy import Lane, RiskTier

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OverrideType(str, Enum):
    """Mechanism used to override the evaluator.

    Attributes:
        force_complete:  Advance the task directly to ``completed`` regardless
                         of evaluator outcome.
        force_requeue:   Reset the task to ``in_progress`` from ``blocked`` or
                         ``evaluator_failed``, bypassing normal retry gating.
        bypass_checks:   Skip one or more specific acceptance checks; remaining
                         checks are still executed and must pass.
    """

    force_complete = "force_complete"
    force_requeue = "force_requeue"
    bypass_checks = "bypass_checks"


class OverriderRole(str, Enum):
    """Roles that are authorised to issue evaluator overrides.

    Attributes:
        operator:    Named system operator.  May override low-risk tasks only.
        lead_agent:  Lead agent.  May override low- and medium-risk tasks in
                     non-blocked lanes.
        senior_dev:  Senior developer.  May override any risk tier and any
                     lane, including blocked.  Critical-risk overrides require
                     a second ``senior_dev`` co-signer.
    """

    operator = "operator"
    lead_agent = "lead-agent"
    senior_dev = "senior-dev"


# ---------------------------------------------------------------------------
# Audit model
# ---------------------------------------------------------------------------


class EvaluatorOverride(BaseModel):
    """Audit record for a single evaluator override action.

    All fields except ``cosigner_id`` and ``ticket_ref`` are required.  The
    ``timestamp`` field defaults to the current UTC time when not supplied,
    but may be explicitly provided to support replay / import scenarios.

    Attributes:
        task_id:           The task whose evaluator is being overridden.
        overrider_id:      Identity of the operator issuing the override.
                           Must match a registered operator.
        reason:            Free-text justification.  Minimum 20 characters.
                           Must describe which Section 3 condition applies
                           (evaluator flaky, environment issue, time-critical,
                           or known-safe failure).
        risk_acknowledged: Must be ``True``.  Confirms the overrider accepts
                           responsibility for quality risk.
        override_type:     One of ``force_complete``, ``force_requeue``, or
                           ``bypass_checks``.
        lane_at_time:      The lane the task was assigned to when the override
                           was issued.  One of ``autonomous``, ``human-review``,
                           or ``blocked``.
        timestamp:         UTC datetime of the override.  Defaults to now.
        role:              The role the overrider is acting under.  Used by
                           :class:`OverridePolicy` for validation.
        task_risk_tier:    Optional risk tier from the task contract.  When
                           present it is used by :class:`OverridePolicy` to
                           apply tier-specific rules.
        cosigner_id:       Identity of the second approver.  Required for
                           ``critical``-risk overrides (mandatory 2PA).
                           Strongly recommended for any blocked-lane override.
        ticket_ref:        Optional reference to an incident or issue that
                           provides additional context (e.g. ``FORGE#1234``).
    """

    task_id: str = Field(
        ...,
        min_length=1,
        description="ID of the task being overridden.",
    )
    overrider_id: str = Field(
        ...,
        min_length=1,
        description="Identity of the operator issuing the override.",
    )
    reason: str = Field(
        ...,
        min_length=20,
        description=(
            "Free-text justification (minimum 20 characters). "
            "Reference the applicable condition from the runbook: "
            "evaluator_flaky | environment_issue | time_critical | known_safe_failure."
        ),
    )
    risk_acknowledged: bool = Field(
        ...,
        description=(
            "Must be True.  Confirms the overrider accepts quality risk "
            "responsibility for bypassing the evaluator."
        ),
    )
    override_type: OverrideType = Field(
        ...,
        description="Mechanism used: force_complete, force_requeue, or bypass_checks.",
    )
    lane_at_time: str = Field(
        ...,
        description=(
            "Lane the task was in at override time: autonomous, human-review, or blocked."
        ),
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of the override.  Defaults to now.",
    )
    role: OverriderRole = Field(
        ...,
        description="Role the overrider is acting under (operator, lead-agent, senior-dev).",
    )
    task_risk_tier: RiskTier | None = Field(
        default=None,
        description="Risk tier from the task contract.  Used for tier-specific policy checks.",
    )
    cosigner_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Second approver identity.  Required for critical-risk overrides (2PA). "
            "Recommended for any blocked-lane override."
        ),
    )
    ticket_ref: str | None = Field(
        default=None,
        description="Optional incident or issue reference (e.g. FORGE#1234).",
    )

    model_config = {"frozen": False, "populate_by_name": True}

    # ------------------------------------------------------------------
    # Field validators
    # ------------------------------------------------------------------

    @field_validator("risk_acknowledged")
    @classmethod
    def _risk_must_be_acknowledged(cls, value: bool) -> bool:
        """Ensure risk_acknowledged is True — False is never a valid override."""
        if not value:
            raise ValueError(
                "risk_acknowledged must be True to issue an evaluator override. "
                "Set risk_acknowledged=True to confirm you accept quality responsibility."
            )
        return value

    @field_validator("lane_at_time")
    @classmethod
    def _lane_at_time_must_be_valid(cls, value: str) -> str:
        """Validate that lane_at_time is a known lane string."""
        valid = {lane.value for lane in Lane}
        if value not in valid:
            raise ValueError(
                f"lane_at_time {value!r} is not a valid lane. "
                f"Must be one of: {sorted(valid)}"
            )
        return value

    # ------------------------------------------------------------------
    # Cross-field validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _critical_risk_requires_cosigner(self) -> EvaluatorOverride:
        """Enforce 2PA requirement for critical-risk overrides."""
        if (
            self.task_risk_tier == RiskTier.critical
            and not self.cosigner_id
        ):
            raise ValueError(
                "critical-risk overrides require a co-signer (2PA). "
                "Provide cosigner_id with the identity of a second senior-dev."
            )
        return self


# ---------------------------------------------------------------------------
# Policy violation exception
# ---------------------------------------------------------------------------


class OverrideViolationError(ValueError):
    """Raised when a proposed override violates the policy rules.

    Attributes:
        override:      The :class:`EvaluatorOverride` that was rejected.
        violated_rule: Short description of the specific rule that was violated.
    """

    def __init__(self, override: EvaluatorOverride, violated_rule: str) -> None:
        self.override = override
        self.violated_rule = violated_rule
        super().__init__(
            f"Override rejected for task {override.task_id!r}: {violated_rule}"
        )


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------


class OverridePolicy:
    """Stateless policy engine for evaluator override validation.

    Call :meth:`validate_override` to check whether a given
    :class:`EvaluatorOverride` is permissible under the current rules.  A
    violation raises :class:`OverrideViolationError` with a precise description of
    the violated rule.

    Policy rules (from the runbook)
    --------------------------------
    1. ``operator``    may override low-risk tasks only.
    2. ``lead-agent``  may override low- and medium-risk tasks; blocked lane
                       is prohibited regardless of risk tier.
    3. ``senior-dev``  may override any risk tier and any lane.
    4. Critical-risk overrides require a co-signer (2PA) — enforced at model
       validation time by :class:`EvaluatorOverride` AND at policy time here.
    5. Blocked-lane overrides require at minimum a ``senior-dev`` role.
    6. ``risk_acknowledged`` must be ``True`` (enforced at model level but
       re-checked here for defence-in-depth).
    """

    # Risk tiers that each role may override (when not in blocked lane)
    _ROLE_ALLOWED_TIERS: dict[OverriderRole, set[RiskTier]] = {
        OverriderRole.operator: {RiskTier.low},
        OverriderRole.lead_agent: {RiskTier.low, RiskTier.medium},
        OverriderRole.senior_dev: {RiskTier.low, RiskTier.medium, RiskTier.high, RiskTier.critical},
    }

    # Roles that may override tasks in the blocked lane
    _BLOCKED_LANE_ALLOWED_ROLES: set[OverriderRole] = {OverriderRole.senior_dev}

    @classmethod
    def validate_override(cls, override: EvaluatorOverride) -> None:
        """Validate that *override* is permissible under the current policy.

        Args:
            override: The :class:`EvaluatorOverride` to validate.

        Returns:
            ``None`` when the override is permitted.

        Raises:
            :class:`OverrideViolationError`: When any policy rule is violated.
                The exception message identifies the specific rule.
        """
        # Defence-in-depth: re-check risk acknowledgment
        if not override.risk_acknowledged:
            raise OverrideViolationError(
                override,
                "risk_acknowledged must be True; override is not valid without "
                "explicit risk acceptance",
            )

        is_blocked_lane = override.lane_at_time == Lane.blocked.value

        # Rule 5: blocked lane requires senior-dev
        if is_blocked_lane and override.role not in cls._BLOCKED_LANE_ALLOWED_ROLES:
            raise OverrideViolationError(
                override,
                f"role {override.role.value!r} cannot override tasks in the blocked lane; "
                f"minimum required role is 'senior-dev'",
            )

        # Rules 1–3: check risk tier against role allowance
        if override.task_risk_tier is not None:
            allowed_tiers = cls._ROLE_ALLOWED_TIERS.get(override.role, set())
            if override.task_risk_tier not in allowed_tiers:
                raise OverrideViolationError(
                    override,
                    f"role {override.role.value!r} is not permitted to override tasks at "
                    f"risk tier {override.task_risk_tier.value!r}; "
                    f"allowed tiers for this role: "
                    f"{sorted(t.value for t in allowed_tiers)}",
                )

        # Rule 4: critical risk requires 2PA (co-signer)
        if (
            override.task_risk_tier == RiskTier.critical
            and not override.cosigner_id
        ):
            raise OverrideViolationError(
                override,
                "critical-risk overrides require two-person approval (2PA); "
                "provide cosigner_id with the identity of a second senior-dev",
            )

        # Rule 2 extension: lead-agent cannot override blocked lane tasks even at low risk
        # (redundant with Rule 5 check above, but explicit for clarity)
        if is_blocked_lane and override.role == OverriderRole.lead_agent:
            raise OverrideViolationError(
                override,
                "lead-agent role is prohibited from overriding tasks in the blocked lane",
            )

    @classmethod
    def is_permitted(cls, override: EvaluatorOverride) -> bool:
        """Return ``True`` when *override* passes all policy checks.

        Unlike :meth:`validate_override`, this method never raises — it
        returns a boolean.  Use :meth:`validate_override` when you need the
        specific violation reason.

        Args:
            override: The :class:`EvaluatorOverride` to test.

        Returns:
            ``True`` when the override is permitted; ``False`` otherwise.
        """
        try:
            cls.validate_override(override)
            return True
        except OverrideViolationError:
            return False
