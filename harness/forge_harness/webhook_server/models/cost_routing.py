"""Cost-Aware Model Routing Models — DF-3003
==========================================

Defines the data models for the cost-aware model routing policy layer.
Operators can configure routing policies that balance cost versus quality
across the eight work-cell lane types and four risk tiers.

Classes
-------
ModelTier
    Enum of model quality/cost tiers (economy, standard, premium).
CostProfile
    Frozen Pydantic model holding per-tier pricing and quality data.
RoutingPolicy
    Frozen Pydantic model mapping a (lane, risk_tier) pair to tier constraints.
RoutingDecision
    Pydantic model capturing a single resolved routing decision with reasoning.

Constants
---------
COST_PROFILES
    Dict mapping ModelTier → CostProfile with real-world token pricing.
DEFAULT_ROUTING_POLICIES
    Dict mapping (WorkCellLane, RiskTier) → RoutingPolicy with sensible defaults.

Reference
---------
docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md  (DF-3003)
forge_harness/webhook_server/models/work_cell.py   (WorkCellLane)
forge_harness/webhook_server/models/lane_policy.py (RiskTier)
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from forge_harness.webhook_server.models.lane_policy import RiskTier
from forge_harness.webhook_server.models.work_cell import WorkCellLane

# ---------------------------------------------------------------------------
# ModelTier enum
# ---------------------------------------------------------------------------


class ModelTier(str, Enum):
    """Model quality and cost tier classification.

    Attributes:
        economy:  Haiku-class models.  Lowest cost, suitable for simple,
                  deterministic tasks with well-defined acceptance criteria.
        standard: Sonnet-class models.  Balanced cost and quality.  Suitable
                  for most API, test-writing, and refactoring work.
        premium:  Opus-class models.  Highest quality, highest cost.  Reserved
                  for security-critical, deployment, and complex reasoning tasks.
    """

    economy = "economy"
    standard = "standard"
    premium = "premium"


# ---------------------------------------------------------------------------
# CostProfile model
# ---------------------------------------------------------------------------


class CostProfile(BaseModel):
    """Pricing and quality metadata for a single model tier.

    All token costs are expressed in USD per 1,000 tokens.

    Attributes:
        model_tier:                 The tier this profile describes.
        cost_per_1k_input_tokens:   Cost in USD per 1,000 input (prompt) tokens.
        cost_per_1k_output_tokens:  Cost in USD per 1,000 output (completion)
                                    tokens.
        quality_score:              Relative quality estimate in [0.0, 1.0].
                                    Higher values indicate stronger capability.
        max_context_tokens:         Maximum context window in tokens.
    """

    model_config = ConfigDict(frozen=True)

    model_tier: ModelTier = Field(
        ...,
        description="The model tier this profile applies to.",
    )
    cost_per_1k_input_tokens: float = Field(
        ...,
        ge=0.0,
        description="USD cost per 1,000 input tokens.",
    )
    cost_per_1k_output_tokens: float = Field(
        ...,
        ge=0.0,
        description="USD cost per 1,000 output tokens.",
    )
    quality_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Relative quality score in [0.0, 1.0].",
    )
    max_context_tokens: int = Field(
        ...,
        ge=1,
        description="Maximum context window in tokens.",
    )


# ---------------------------------------------------------------------------
# RoutingPolicy model
# ---------------------------------------------------------------------------

#: Ordinal rank used for min_tier <= max_tier comparisons.
_TIER_ORDER: dict[ModelTier, int] = {
    ModelTier.economy: 0,
    ModelTier.standard: 1,
    ModelTier.premium: 2,
}


class RoutingPolicy(BaseModel):
    """Cost and quality routing policy for a specific (lane, risk_tier) pair.

    Attributes:
        lane:                  The work-cell lane this policy applies to.
        risk_tier:             The risk tier this policy applies to.
        min_model_tier:        Minimum acceptable model tier.  The router will
                               never select a tier below this value.
        max_model_tier:        Maximum acceptable model tier.  The router will
                               never select a tier above this value.
        quality_target:        Desired quality level in [0.0, 1.0].  The router
                               selects the cheapest tier whose quality_score
                               meets or exceeds this target, subject to the
                               min/max tier bounds.
        budget_limit_per_task: Optional per-task budget cap in USD.  When set,
                               the router will not select a tier whose
                               estimated cost exceeds this limit.
    """

    model_config = ConfigDict(frozen=True)

    lane: WorkCellLane = Field(
        ...,
        description="The work-cell lane this policy applies to.",
    )
    risk_tier: RiskTier = Field(
        ...,
        description="The risk tier this policy applies to.",
    )
    min_model_tier: ModelTier = Field(
        ...,
        description="Cheapest tier the router is allowed to select.",
    )
    max_model_tier: ModelTier = Field(
        ...,
        description="Most expensive tier the router is allowed to select.",
    )
    quality_target: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Desired quality level in [0.0, 1.0].",
    )
    budget_limit_per_task: float | None = Field(
        default=None,
        ge=0.0,
        description="Optional per-task budget cap in USD.",
    )

    @model_validator(mode="after")
    def _validate_tier_order(self) -> RoutingPolicy:
        """Ensure min_model_tier is not higher than max_model_tier."""
        if _TIER_ORDER[self.min_model_tier] > _TIER_ORDER[self.max_model_tier]:
            raise ValueError(
                f"min_model_tier ({self.min_model_tier.value!r}) must be <= "
                f"max_model_tier ({self.max_model_tier.value!r})"
            )
        return self


# ---------------------------------------------------------------------------
# RoutingDecision model
# ---------------------------------------------------------------------------


class RoutingDecision(BaseModel):
    """A single resolved routing decision for a task.

    Attributes:
        task_id:          Unique identifier of the task being routed.
        selected_tier:    The model tier selected for this task.
        routing_policy:   Human-readable reference identifying which policy
                          was applied (e.g. ``"docs/low"``).
        cost_estimate:    Estimated cost in USD for this task at the selected
                          tier.  Based on estimated token counts if provided.
        quality_estimate: Expected quality level in [0.0, 1.0] at the
                          selected tier.
        reasoning:        Human-readable explanation of the routing decision,
                          including which policy matched, quality targets, and
                          why the tier was selected.
        decided_at:       UTC timestamp when the decision was made.
    """

    task_id: str = Field(..., description="Unique task identifier.")
    selected_tier: ModelTier = Field(
        ...,
        description="The model tier chosen for this task.",
    )
    routing_policy: str = Field(
        ...,
        description="Reference to the applied routing policy (e.g. 'docs/low').",
    )
    cost_estimate: float = Field(
        default=0.0,
        ge=0.0,
        description="Estimated cost in USD for this task.",
    )
    quality_estimate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Expected quality level in [0.0, 1.0] at the selected tier.",
    )
    reasoning: list[str] = Field(
        default_factory=list,
        description="Human-readable explanation of the routing decision.",
    )
    decided_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the decision was made.",
    )


# ---------------------------------------------------------------------------
# Built-in COST_PROFILES
# ---------------------------------------------------------------------------

#: Per-tier cost and quality profiles.
#:
#: Pricing is based on approximate real-world Claude API pricing (USD per 1K
#: tokens) as of early 2026.  Operators should update these values when the
#: upstream pricing changes.
COST_PROFILES: dict[ModelTier, CostProfile] = {
    ModelTier.economy: CostProfile(
        model_tier=ModelTier.economy,
        cost_per_1k_input_tokens=0.00025,  # Haiku-class: $0.25/MTok input
        cost_per_1k_output_tokens=0.00125,  # $1.25/MTok output
        quality_score=0.55,
        max_context_tokens=200_000,
    ),
    ModelTier.standard: CostProfile(
        model_tier=ModelTier.standard,
        cost_per_1k_input_tokens=0.003,  # Sonnet-class: $3/MTok input
        cost_per_1k_output_tokens=0.015,  # $15/MTok output
        quality_score=0.80,
        max_context_tokens=200_000,
    ),
    ModelTier.premium: CostProfile(
        model_tier=ModelTier.premium,
        cost_per_1k_input_tokens=0.015,  # Opus-class: $15/MTok input
        cost_per_1k_output_tokens=0.075,  # $75/MTok output
        quality_score=1.00,
        max_context_tokens=200_000,
    ),
}


# ---------------------------------------------------------------------------
# DEFAULT_ROUTING_POLICIES
# ---------------------------------------------------------------------------

#: Default routing policies keyed by (WorkCellLane, RiskTier).
#:
#: Design rationale:
#:   - docs tasks are cheap to produce and rarely high-risk → economy.
#:   - test_writing needs correctness but costs can be moderate → economy-standard.
#:   - api_simple at medium risk needs reliability → standard only.
#:   - api_stateful always needs reliability → standard minimum.
#:   - research benefits from quality but cost is secondary → standard-premium.
#:   - refactor needs accuracy → standard.
#:   - security_change is too risky for economy → premium required.
#:   - deployment at high/critical is irreversible → premium required.
#:
#: Any (lane, risk_tier) combination not explicitly listed falls back to the
#: standard tier in the CostRouter service layer.
DEFAULT_ROUTING_POLICIES: dict[tuple[WorkCellLane, RiskTier], RoutingPolicy] = {
    # ---------------------------------------------------------------------- #
    # docs — economy for low/medium; standard for high/critical
    # ---------------------------------------------------------------------- #
    (WorkCellLane.docs, RiskTier.low): RoutingPolicy(
        lane=WorkCellLane.docs,
        risk_tier=RiskTier.low,
        min_model_tier=ModelTier.economy,
        max_model_tier=ModelTier.economy,
        quality_target=0.40,
    ),
    (WorkCellLane.docs, RiskTier.medium): RoutingPolicy(
        lane=WorkCellLane.docs,
        risk_tier=RiskTier.medium,
        min_model_tier=ModelTier.economy,
        max_model_tier=ModelTier.standard,
        quality_target=0.50,
    ),
    (WorkCellLane.docs, RiskTier.high): RoutingPolicy(
        lane=WorkCellLane.docs,
        risk_tier=RiskTier.high,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.standard,
        quality_target=0.70,
    ),
    (WorkCellLane.docs, RiskTier.critical): RoutingPolicy(
        lane=WorkCellLane.docs,
        risk_tier=RiskTier.critical,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.premium,
        quality_target=0.80,
    ),
    # ---------------------------------------------------------------------- #
    # test_writing — economy at low; economy-standard at medium; standard+ higher
    # ---------------------------------------------------------------------- #
    (WorkCellLane.test_writing, RiskTier.low): RoutingPolicy(
        lane=WorkCellLane.test_writing,
        risk_tier=RiskTier.low,
        min_model_tier=ModelTier.economy,
        max_model_tier=ModelTier.standard,
        quality_target=0.50,
    ),
    (WorkCellLane.test_writing, RiskTier.medium): RoutingPolicy(
        lane=WorkCellLane.test_writing,
        risk_tier=RiskTier.medium,
        min_model_tier=ModelTier.economy,
        max_model_tier=ModelTier.standard,
        quality_target=0.60,
    ),
    (WorkCellLane.test_writing, RiskTier.high): RoutingPolicy(
        lane=WorkCellLane.test_writing,
        risk_tier=RiskTier.high,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.standard,
        quality_target=0.75,
    ),
    (WorkCellLane.test_writing, RiskTier.critical): RoutingPolicy(
        lane=WorkCellLane.test_writing,
        risk_tier=RiskTier.critical,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.premium,
        quality_target=0.85,
    ),
    # ---------------------------------------------------------------------- #
    # api_simple — economy at low; standard at medium+
    # ---------------------------------------------------------------------- #
    (WorkCellLane.api_simple, RiskTier.low): RoutingPolicy(
        lane=WorkCellLane.api_simple,
        risk_tier=RiskTier.low,
        min_model_tier=ModelTier.economy,
        max_model_tier=ModelTier.standard,
        quality_target=0.50,
    ),
    (WorkCellLane.api_simple, RiskTier.medium): RoutingPolicy(
        lane=WorkCellLane.api_simple,
        risk_tier=RiskTier.medium,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.standard,
        quality_target=0.70,
    ),
    (WorkCellLane.api_simple, RiskTier.high): RoutingPolicy(
        lane=WorkCellLane.api_simple,
        risk_tier=RiskTier.high,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.premium,
        quality_target=0.80,
    ),
    (WorkCellLane.api_simple, RiskTier.critical): RoutingPolicy(
        lane=WorkCellLane.api_simple,
        risk_tier=RiskTier.critical,
        min_model_tier=ModelTier.premium,
        max_model_tier=ModelTier.premium,
        quality_target=0.95,
    ),
    # ---------------------------------------------------------------------- #
    # api_stateful — standard minimum across all risk tiers
    # ---------------------------------------------------------------------- #
    (WorkCellLane.api_stateful, RiskTier.low): RoutingPolicy(
        lane=WorkCellLane.api_stateful,
        risk_tier=RiskTier.low,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.standard,
        quality_target=0.70,
    ),
    (WorkCellLane.api_stateful, RiskTier.medium): RoutingPolicy(
        lane=WorkCellLane.api_stateful,
        risk_tier=RiskTier.medium,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.standard,
        quality_target=0.75,
    ),
    (WorkCellLane.api_stateful, RiskTier.high): RoutingPolicy(
        lane=WorkCellLane.api_stateful,
        risk_tier=RiskTier.high,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.premium,
        quality_target=0.85,
    ),
    (WorkCellLane.api_stateful, RiskTier.critical): RoutingPolicy(
        lane=WorkCellLane.api_stateful,
        risk_tier=RiskTier.critical,
        min_model_tier=ModelTier.premium,
        max_model_tier=ModelTier.premium,
        quality_target=0.95,
    ),
    # ---------------------------------------------------------------------- #
    # research — standard-premium (quality matters; cost secondary)
    # ---------------------------------------------------------------------- #
    (WorkCellLane.research, RiskTier.low): RoutingPolicy(
        lane=WorkCellLane.research,
        risk_tier=RiskTier.low,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.premium,
        quality_target=0.70,
    ),
    (WorkCellLane.research, RiskTier.medium): RoutingPolicy(
        lane=WorkCellLane.research,
        risk_tier=RiskTier.medium,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.premium,
        quality_target=0.75,
    ),
    (WorkCellLane.research, RiskTier.high): RoutingPolicy(
        lane=WorkCellLane.research,
        risk_tier=RiskTier.high,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.premium,
        quality_target=0.85,
    ),
    (WorkCellLane.research, RiskTier.critical): RoutingPolicy(
        lane=WorkCellLane.research,
        risk_tier=RiskTier.critical,
        min_model_tier=ModelTier.premium,
        max_model_tier=ModelTier.premium,
        quality_target=0.95,
    ),
    # ---------------------------------------------------------------------- #
    # refactor — standard for correctness
    # ---------------------------------------------------------------------- #
    (WorkCellLane.refactor, RiskTier.low): RoutingPolicy(
        lane=WorkCellLane.refactor,
        risk_tier=RiskTier.low,
        min_model_tier=ModelTier.economy,
        max_model_tier=ModelTier.standard,
        quality_target=0.60,
    ),
    (WorkCellLane.refactor, RiskTier.medium): RoutingPolicy(
        lane=WorkCellLane.refactor,
        risk_tier=RiskTier.medium,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.standard,
        quality_target=0.75,
    ),
    (WorkCellLane.refactor, RiskTier.high): RoutingPolicy(
        lane=WorkCellLane.refactor,
        risk_tier=RiskTier.high,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.premium,
        quality_target=0.85,
    ),
    (WorkCellLane.refactor, RiskTier.critical): RoutingPolicy(
        lane=WorkCellLane.refactor,
        risk_tier=RiskTier.critical,
        min_model_tier=ModelTier.premium,
        max_model_tier=ModelTier.premium,
        quality_target=0.95,
    ),
    # ---------------------------------------------------------------------- #
    # security_change — premium required at high/critical; standard at low/medium
    # ---------------------------------------------------------------------- #
    (WorkCellLane.security_change, RiskTier.low): RoutingPolicy(
        lane=WorkCellLane.security_change,
        risk_tier=RiskTier.low,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.premium,
        quality_target=0.80,
    ),
    (WorkCellLane.security_change, RiskTier.medium): RoutingPolicy(
        lane=WorkCellLane.security_change,
        risk_tier=RiskTier.medium,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.premium,
        quality_target=0.85,
    ),
    (WorkCellLane.security_change, RiskTier.high): RoutingPolicy(
        lane=WorkCellLane.security_change,
        risk_tier=RiskTier.high,
        min_model_tier=ModelTier.premium,
        max_model_tier=ModelTier.premium,
        quality_target=0.95,
    ),
    (WorkCellLane.security_change, RiskTier.critical): RoutingPolicy(
        lane=WorkCellLane.security_change,
        risk_tier=RiskTier.critical,
        min_model_tier=ModelTier.premium,
        max_model_tier=ModelTier.premium,
        quality_target=1.00,
    ),
    # ---------------------------------------------------------------------- #
    # deployment — premium required at high/critical
    # ---------------------------------------------------------------------- #
    (WorkCellLane.deployment, RiskTier.low): RoutingPolicy(
        lane=WorkCellLane.deployment,
        risk_tier=RiskTier.low,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.premium,
        quality_target=0.80,
    ),
    (WorkCellLane.deployment, RiskTier.medium): RoutingPolicy(
        lane=WorkCellLane.deployment,
        risk_tier=RiskTier.medium,
        min_model_tier=ModelTier.standard,
        max_model_tier=ModelTier.premium,
        quality_target=0.85,
    ),
    (WorkCellLane.deployment, RiskTier.high): RoutingPolicy(
        lane=WorkCellLane.deployment,
        risk_tier=RiskTier.high,
        min_model_tier=ModelTier.premium,
        max_model_tier=ModelTier.premium,
        quality_target=0.95,
    ),
    (WorkCellLane.deployment, RiskTier.critical): RoutingPolicy(
        lane=WorkCellLane.deployment,
        risk_tier=RiskTier.critical,
        min_model_tier=ModelTier.premium,
        max_model_tier=ModelTier.premium,
        quality_target=1.00,
    ),
}

# Validate at import time: every (lane, risk_tier) pair has a policy entry.
_expected_policy_count = len(WorkCellLane) * len(RiskTier)
assert len(DEFAULT_ROUTING_POLICIES) == _expected_policy_count, (
    f"DEFAULT_ROUTING_POLICIES is incomplete: expected {_expected_policy_count} entries, "
    f"got {len(DEFAULT_ROUTING_POLICIES)}. Add missing (WorkCellLane, RiskTier) combinations."
)
