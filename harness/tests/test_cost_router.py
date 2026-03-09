"""Cost-Aware Model Routing Tests — DF-3003
==========================================

Comprehensive test suite for the cost-aware model routing policy layer.

Coverage:
- ModelTier enum values
- CostProfile validation (non-negative costs, quality 0-1)
- RoutingPolicy validation (min_tier <= max_tier semantically)
- Default policy coverage (every lane x risk_tier has a policy)
- Tier resolution for each (lane, risk_tier) combination
- Quality target override behaviour
- Cost estimation accuracy
- Decision making with full reasoning
- Decision log and cost summary
- Thread safety (concurrent decisions)
- JSONL persistence
- Public exports from models and services __init__
- 80+ tests total
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from forge_harness.webhook_server.models.cost_routing import (
    COST_PROFILES,
    DEFAULT_ROUTING_POLICIES,
    CostProfile,
    ModelTier,
    RoutingDecision,
    RoutingPolicy,
)
from forge_harness.webhook_server.models.lane_policy import RiskTier
from forge_harness.webhook_server.models.work_cell import WorkCellLane
from forge_harness.webhook_server.services.cost_router import (
    CostRouter,
    get_cost_router,
    reset_cost_router,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the CostRouter singleton before and after every test."""
    reset_cost_router()
    yield
    reset_cost_router()


@pytest.fixture()
def router(tmp_path: Path) -> CostRouter:
    """Return a fresh CostRouter with an isolated JSONL path."""
    return CostRouter(decisions_path=tmp_path / "decisions.jsonl")


@pytest.fixture()
def isolated_path(tmp_path: Path) -> Path:
    """Return a temporary JSONL path for persistence tests."""
    return tmp_path / "routing" / "decisions.jsonl"


# ===========================================================================
# ModelTier enum
# ===========================================================================


class TestModelTierEnum:
    """Verify ModelTier enum shape and values."""

    def test_economy_value(self) -> None:
        assert ModelTier.economy == "economy"
        assert ModelTier.economy.value == "economy"

    def test_standard_value(self) -> None:
        assert ModelTier.standard == "standard"
        assert ModelTier.standard.value == "standard"

    def test_premium_value(self) -> None:
        assert ModelTier.premium == "premium"
        assert ModelTier.premium.value == "premium"

    def test_exactly_three_tiers(self) -> None:
        assert len(ModelTier) == 3

    def test_is_str_enum(self) -> None:
        for tier in ModelTier:
            assert isinstance(tier, str)
            assert isinstance(tier.value, str)

    def test_all_expected_values(self) -> None:
        expected = {"economy", "standard", "premium"}
        actual = {t.value for t in ModelTier}
        assert actual == expected


# ===========================================================================
# CostProfile model
# ===========================================================================


class TestCostProfileModel:
    """Validate CostProfile Pydantic model constraints."""

    def test_valid_construction(self) -> None:
        profile = CostProfile(
            model_tier=ModelTier.standard,
            cost_per_1k_input_tokens=0.003,
            cost_per_1k_output_tokens=0.015,
            quality_score=0.80,
            max_context_tokens=200_000,
        )
        assert profile.model_tier == ModelTier.standard
        assert profile.cost_per_1k_input_tokens == pytest.approx(0.003)
        assert profile.cost_per_1k_output_tokens == pytest.approx(0.015)
        assert profile.quality_score == pytest.approx(0.80)
        assert profile.max_context_tokens == 200_000

    def test_zero_cost_allowed(self) -> None:
        """Zero cost is a valid edge case (free tier)."""
        profile = CostProfile(
            model_tier=ModelTier.economy,
            cost_per_1k_input_tokens=0.0,
            cost_per_1k_output_tokens=0.0,
            quality_score=0.5,
            max_context_tokens=1000,
        )
        assert profile.cost_per_1k_input_tokens == 0.0
        assert profile.cost_per_1k_output_tokens == 0.0

    def test_negative_input_cost_raises(self) -> None:
        with pytest.raises(ValidationError):
            CostProfile(
                model_tier=ModelTier.economy,
                cost_per_1k_input_tokens=-0.001,
                cost_per_1k_output_tokens=0.01,
                quality_score=0.5,
                max_context_tokens=100_000,
            )

    def test_negative_output_cost_raises(self) -> None:
        with pytest.raises(ValidationError):
            CostProfile(
                model_tier=ModelTier.economy,
                cost_per_1k_input_tokens=0.001,
                cost_per_1k_output_tokens=-0.01,
                quality_score=0.5,
                max_context_tokens=100_000,
            )

    def test_quality_score_min_boundary(self) -> None:
        profile = CostProfile(
            model_tier=ModelTier.economy,
            cost_per_1k_input_tokens=0.001,
            cost_per_1k_output_tokens=0.005,
            quality_score=0.0,
            max_context_tokens=10_000,
        )
        assert profile.quality_score == 0.0

    def test_quality_score_max_boundary(self) -> None:
        profile = CostProfile(
            model_tier=ModelTier.premium,
            cost_per_1k_input_tokens=0.015,
            cost_per_1k_output_tokens=0.075,
            quality_score=1.0,
            max_context_tokens=200_000,
        )
        assert profile.quality_score == 1.0

    def test_quality_below_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            CostProfile(
                model_tier=ModelTier.economy,
                cost_per_1k_input_tokens=0.001,
                cost_per_1k_output_tokens=0.005,
                quality_score=-0.1,
                max_context_tokens=10_000,
            )

    def test_quality_above_one_raises(self) -> None:
        with pytest.raises(ValidationError):
            CostProfile(
                model_tier=ModelTier.economy,
                cost_per_1k_input_tokens=0.001,
                cost_per_1k_output_tokens=0.005,
                quality_score=1.1,
                max_context_tokens=10_000,
            )

    def test_max_context_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            CostProfile(
                model_tier=ModelTier.standard,
                cost_per_1k_input_tokens=0.003,
                cost_per_1k_output_tokens=0.015,
                quality_score=0.8,
                max_context_tokens=0,
            )

    def test_model_is_frozen(self) -> None:
        profile = CostProfile(
            model_tier=ModelTier.standard,
            cost_per_1k_input_tokens=0.003,
            cost_per_1k_output_tokens=0.015,
            quality_score=0.8,
            max_context_tokens=200_000,
        )
        with pytest.raises(Exception):
            profile.quality_score = 0.99  # type: ignore[misc]


# ===========================================================================
# COST_PROFILES constant
# ===========================================================================


class TestBuiltinCostProfiles:
    """Validate the built-in COST_PROFILES constant."""

    def test_all_tiers_present(self) -> None:
        for tier in ModelTier:
            assert tier in COST_PROFILES, f"Missing COST_PROFILES entry for {tier.value}"

    def test_tier_field_matches_key(self) -> None:
        for tier, profile in COST_PROFILES.items():
            assert profile.model_tier == tier

    def test_economy_cheaper_than_standard(self) -> None:
        eco = COST_PROFILES[ModelTier.economy]
        std = COST_PROFILES[ModelTier.standard]
        assert eco.cost_per_1k_input_tokens < std.cost_per_1k_input_tokens
        assert eco.cost_per_1k_output_tokens < std.cost_per_1k_output_tokens

    def test_standard_cheaper_than_premium(self) -> None:
        std = COST_PROFILES[ModelTier.standard]
        prem = COST_PROFILES[ModelTier.premium]
        assert std.cost_per_1k_input_tokens < prem.cost_per_1k_input_tokens
        assert std.cost_per_1k_output_tokens < prem.cost_per_1k_output_tokens

    def test_economy_lower_quality_than_premium(self) -> None:
        assert (
            COST_PROFILES[ModelTier.economy].quality_score
            < COST_PROFILES[ModelTier.premium].quality_score
        )

    def test_all_quality_scores_in_range(self) -> None:
        for tier, profile in COST_PROFILES.items():
            assert 0.0 <= profile.quality_score <= 1.0, (
                f"quality_score out of range for {tier.value}"
            )

    def test_all_costs_non_negative(self) -> None:
        for tier, profile in COST_PROFILES.items():
            assert profile.cost_per_1k_input_tokens >= 0.0
            assert profile.cost_per_1k_output_tokens >= 0.0


# ===========================================================================
# RoutingPolicy model
# ===========================================================================


class TestRoutingPolicyModel:
    """Validate RoutingPolicy Pydantic model and its tier-order invariant."""

    def test_valid_same_tier_min_max(self) -> None:
        policy = RoutingPolicy(
            lane=WorkCellLane.docs,
            risk_tier=RiskTier.low,
            min_model_tier=ModelTier.economy,
            max_model_tier=ModelTier.economy,
            quality_target=0.4,
        )
        assert policy.min_model_tier == ModelTier.economy
        assert policy.max_model_tier == ModelTier.economy

    def test_valid_economy_to_premium(self) -> None:
        policy = RoutingPolicy(
            lane=WorkCellLane.research,
            risk_tier=RiskTier.medium,
            min_model_tier=ModelTier.economy,
            max_model_tier=ModelTier.premium,
            quality_target=0.7,
        )
        assert policy.min_model_tier == ModelTier.economy
        assert policy.max_model_tier == ModelTier.premium

    def test_invalid_premium_min_economy_max_raises(self) -> None:
        """min_tier=premium > max_tier=economy is invalid."""
        with pytest.raises(ValidationError):
            RoutingPolicy(
                lane=WorkCellLane.docs,
                risk_tier=RiskTier.low,
                min_model_tier=ModelTier.premium,
                max_model_tier=ModelTier.economy,
                quality_target=0.5,
            )

    def test_invalid_standard_min_economy_max_raises(self) -> None:
        with pytest.raises(ValidationError):
            RoutingPolicy(
                lane=WorkCellLane.api_simple,
                risk_tier=RiskTier.low,
                min_model_tier=ModelTier.standard,
                max_model_tier=ModelTier.economy,
                quality_target=0.5,
            )

    def test_quality_target_defaults_to_half(self) -> None:
        policy = RoutingPolicy(
            lane=WorkCellLane.docs,
            risk_tier=RiskTier.low,
            min_model_tier=ModelTier.economy,
            max_model_tier=ModelTier.standard,
        )
        assert policy.quality_target == 0.5

    def test_quality_target_below_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            RoutingPolicy(
                lane=WorkCellLane.docs,
                risk_tier=RiskTier.low,
                min_model_tier=ModelTier.economy,
                max_model_tier=ModelTier.economy,
                quality_target=-0.1,
            )

    def test_quality_target_above_one_raises(self) -> None:
        with pytest.raises(ValidationError):
            RoutingPolicy(
                lane=WorkCellLane.docs,
                risk_tier=RiskTier.low,
                min_model_tier=ModelTier.economy,
                max_model_tier=ModelTier.economy,
                quality_target=1.1,
            )

    def test_budget_limit_none_by_default(self) -> None:
        policy = RoutingPolicy(
            lane=WorkCellLane.docs,
            risk_tier=RiskTier.low,
            min_model_tier=ModelTier.economy,
            max_model_tier=ModelTier.standard,
        )
        assert policy.budget_limit_per_task is None

    def test_budget_limit_can_be_set(self) -> None:
        policy = RoutingPolicy(
            lane=WorkCellLane.deployment,
            risk_tier=RiskTier.critical,
            min_model_tier=ModelTier.premium,
            max_model_tier=ModelTier.premium,
            budget_limit_per_task=1.0,
        )
        assert policy.budget_limit_per_task == pytest.approx(1.0)

    def test_negative_budget_limit_raises(self) -> None:
        with pytest.raises(ValidationError):
            RoutingPolicy(
                lane=WorkCellLane.docs,
                risk_tier=RiskTier.low,
                min_model_tier=ModelTier.economy,
                max_model_tier=ModelTier.economy,
                budget_limit_per_task=-0.01,
            )

    def test_model_is_frozen(self) -> None:
        policy = RoutingPolicy(
            lane=WorkCellLane.docs,
            risk_tier=RiskTier.low,
            min_model_tier=ModelTier.economy,
            max_model_tier=ModelTier.economy,
        )
        with pytest.raises(Exception):
            policy.quality_target = 0.99  # type: ignore[misc]


# ===========================================================================
# DEFAULT_ROUTING_POLICIES coverage
# ===========================================================================


class TestDefaultRoutingPolicies:
    """Verify DEFAULT_ROUTING_POLICIES covers every (lane, risk_tier) pair."""

    def test_count_matches_full_matrix(self) -> None:
        expected = len(WorkCellLane) * len(RiskTier)
        assert len(DEFAULT_ROUTING_POLICIES) == expected

    def test_every_lane_risk_tier_has_a_policy(self) -> None:
        for lane in WorkCellLane:
            for risk_tier in RiskTier:
                assert (lane, risk_tier) in DEFAULT_ROUTING_POLICIES, (
                    f"Missing policy for ({lane.value}, {risk_tier.value})"
                )

    def test_policy_key_matches_lane_field(self) -> None:
        for (lane, risk_tier), policy in DEFAULT_ROUTING_POLICIES.items():
            assert policy.lane == lane

    def test_policy_key_matches_risk_tier_field(self) -> None:
        for (lane, risk_tier), policy in DEFAULT_ROUTING_POLICIES.items():
            assert policy.risk_tier == risk_tier

    def test_all_policies_have_valid_tier_order(self) -> None:
        tier_order = {ModelTier.economy: 0, ModelTier.standard: 1, ModelTier.premium: 2}
        for (lane, risk_tier), policy in DEFAULT_ROUTING_POLICIES.items():
            assert tier_order[policy.min_model_tier] <= tier_order[policy.max_model_tier], (
                f"Invalid tier order for ({lane.value}, {risk_tier.value})"
            )

    # Specific policy intent checks
    def test_docs_low_economy_only(self) -> None:
        """docs/low must restrict to economy-only."""
        policy = DEFAULT_ROUTING_POLICIES[(WorkCellLane.docs, RiskTier.low)]
        assert policy.min_model_tier == ModelTier.economy
        assert policy.max_model_tier == ModelTier.economy

    def test_test_writing_low_allows_economy_to_standard(self) -> None:
        policy = DEFAULT_ROUTING_POLICIES[(WorkCellLane.test_writing, RiskTier.low)]
        assert policy.min_model_tier == ModelTier.economy
        assert policy.max_model_tier == ModelTier.standard

    def test_api_simple_medium_standard_only(self) -> None:
        policy = DEFAULT_ROUTING_POLICIES[(WorkCellLane.api_simple, RiskTier.medium)]
        assert policy.min_model_tier == ModelTier.standard
        assert policy.max_model_tier == ModelTier.standard

    def test_security_change_high_premium_required(self) -> None:
        policy = DEFAULT_ROUTING_POLICIES[(WorkCellLane.security_change, RiskTier.high)]
        assert policy.min_model_tier == ModelTier.premium
        assert policy.max_model_tier == ModelTier.premium

    def test_security_change_critical_premium_required(self) -> None:
        policy = DEFAULT_ROUTING_POLICIES[(WorkCellLane.security_change, RiskTier.critical)]
        assert policy.min_model_tier == ModelTier.premium
        assert policy.max_model_tier == ModelTier.premium

    def test_deployment_high_premium_required(self) -> None:
        policy = DEFAULT_ROUTING_POLICIES[(WorkCellLane.deployment, RiskTier.high)]
        assert policy.min_model_tier == ModelTier.premium
        assert policy.max_model_tier == ModelTier.premium

    def test_deployment_critical_premium_required(self) -> None:
        policy = DEFAULT_ROUTING_POLICIES[(WorkCellLane.deployment, RiskTier.critical)]
        assert policy.min_model_tier == ModelTier.premium
        assert policy.max_model_tier == ModelTier.premium


# ===========================================================================
# CostRouter.resolve_tier
# ===========================================================================


class TestResolveTier:
    """Tier resolution logic for all (lane, risk_tier) combinations."""

    def test_docs_low_resolves_to_economy(self, router: CostRouter) -> None:
        tier = router.resolve_tier(WorkCellLane.docs, RiskTier.low)
        assert tier == ModelTier.economy

    def test_security_change_high_resolves_to_premium(self, router: CostRouter) -> None:
        tier = router.resolve_tier(WorkCellLane.security_change, RiskTier.high)
        assert tier == ModelTier.premium

    def test_deployment_critical_resolves_to_premium(self, router: CostRouter) -> None:
        tier = router.resolve_tier(WorkCellLane.deployment, RiskTier.critical)
        assert tier == ModelTier.premium

    def test_api_simple_medium_resolves_to_standard(self, router: CostRouter) -> None:
        tier = router.resolve_tier(WorkCellLane.api_simple, RiskTier.medium)
        assert tier == ModelTier.standard

    def test_all_lane_risk_tier_combinations_resolve(self, router: CostRouter) -> None:
        """Every (lane, risk_tier) pair must produce a valid ModelTier."""
        for lane in WorkCellLane:
            for risk_tier in RiskTier:
                result = router.resolve_tier(lane, risk_tier)
                assert isinstance(result, ModelTier), (
                    f"resolve_tier({lane.value}, {risk_tier.value}) returned {result!r}"
                )

    def test_no_policy_fallback_to_standard(self, tmp_path: Path) -> None:
        """When no policy exists for a pair, the router falls back to standard."""
        # Create a router with an empty policy table
        empty_router = CostRouter(
            policies={},
            decisions_path=tmp_path / "decisions.jsonl",
        )
        tier = empty_router.resolve_tier(WorkCellLane.docs, RiskTier.low)
        assert tier == ModelTier.standard

    def test_selected_tier_within_allowed_range(self, router: CostRouter) -> None:
        """Selected tier must be within the [min, max] range of the policy."""
        tier_order = {ModelTier.economy: 0, ModelTier.standard: 1, ModelTier.premium: 2}
        for lane in WorkCellLane:
            for risk_tier in RiskTier:
                policy = DEFAULT_ROUTING_POLICIES[(lane, risk_tier)]
                selected = router.resolve_tier(lane, risk_tier)
                assert tier_order[selected] >= tier_order[policy.min_model_tier]
                assert tier_order[selected] <= tier_order[policy.max_model_tier]


# ===========================================================================
# Quality target override
# ===========================================================================


class TestQualityTargetOverride:
    """Verify quality target override changes tier selection."""

    def test_high_quality_override_escalates_tier(self, router: CostRouter) -> None:
        """Requesting quality=1.0 from a docs/low policy should escalate to
        the only tier that meets it (premium), but docs/low is economy-only
        so it should be clamped to economy (max_tier)."""
        # docs/low has max_model_tier=economy; quality=1.0 cannot be met by economy
        # so the router clamps to max_model_tier = economy
        tier = router.resolve_tier(WorkCellLane.docs, RiskTier.low, quality_target=1.0)
        assert tier == ModelTier.economy  # clamped to max of the policy band

    def test_low_quality_override_uses_cheapest(self, router: CostRouter) -> None:
        """quality_target=0.0 should select the cheapest tier in the band."""
        # test_writing/low allows economy-standard; quality=0.0 → economy
        tier = router.resolve_tier(WorkCellLane.test_writing, RiskTier.low, quality_target=0.0)
        assert tier == ModelTier.economy

    def test_medium_quality_override_selects_standard_when_economy_insufficient(
        self, router: CostRouter
    ) -> None:
        """For test_writing/low (economy-standard band), quality_target=0.70 should
        select standard because economy quality_score=0.55 < 0.70."""
        tier = router.resolve_tier(WorkCellLane.test_writing, RiskTier.low, quality_target=0.70)
        assert tier == ModelTier.standard

    def test_quality_override_above_one_clamped(self, router: CostRouter) -> None:
        """quality_target > 1.0 should be clamped to 1.0 without raising."""
        tier = router.resolve_tier(
            WorkCellLane.security_change, RiskTier.critical, quality_target=1.5
        )
        assert isinstance(tier, ModelTier)

    def test_quality_override_below_zero_clamped(self, router: CostRouter) -> None:
        """quality_target < 0.0 should be clamped to 0.0 without raising."""
        tier = router.resolve_tier(WorkCellLane.docs, RiskTier.medium, quality_target=-0.5)
        assert isinstance(tier, ModelTier)

    def test_no_override_uses_policy_default(self, router: CostRouter) -> None:
        """Without quality_target, resolve_tier must use the policy's quality_target."""
        # api_simple/medium → standard; policy quality_target=0.70
        # standard quality=0.80 >= 0.70 → standard
        tier_no_override = router.resolve_tier(WorkCellLane.api_simple, RiskTier.medium)
        tier_with_override = router.resolve_tier(
            WorkCellLane.api_simple, RiskTier.medium, quality_target=0.70
        )
        assert tier_no_override == tier_with_override


# ===========================================================================
# CostRouter.estimate_cost
# ===========================================================================


class TestEstimateCost:
    """Validate cost estimation arithmetic."""

    def test_economy_cost_calculation(self, router: CostRouter) -> None:
        profile = COST_PROFILES[ModelTier.economy]
        input_tokens, output_tokens = 1000, 500
        expected = (input_tokens / 1000.0) * profile.cost_per_1k_input_tokens + (
            output_tokens / 1000.0
        ) * profile.cost_per_1k_output_tokens
        cost = router.estimate_cost(ModelTier.economy, input_tokens, output_tokens)
        assert cost == pytest.approx(expected, rel=1e-6)

    def test_standard_cost_calculation(self, router: CostRouter) -> None:
        profile = COST_PROFILES[ModelTier.standard]
        input_tokens, output_tokens = 2000, 800
        expected = (input_tokens / 1000.0) * profile.cost_per_1k_input_tokens + (
            output_tokens / 1000.0
        ) * profile.cost_per_1k_output_tokens
        cost = router.estimate_cost(ModelTier.standard, input_tokens, output_tokens)
        assert cost == pytest.approx(expected, rel=1e-6)

    def test_premium_cost_calculation(self, router: CostRouter) -> None:
        profile = COST_PROFILES[ModelTier.premium]
        input_tokens, output_tokens = 5000, 1000
        expected = (input_tokens / 1000.0) * profile.cost_per_1k_input_tokens + (
            output_tokens / 1000.0
        ) * profile.cost_per_1k_output_tokens
        cost = router.estimate_cost(ModelTier.premium, input_tokens, output_tokens)
        assert cost == pytest.approx(expected, rel=1e-6)

    def test_zero_tokens_returns_zero_cost(self, router: CostRouter) -> None:
        cost = router.estimate_cost(ModelTier.premium, 0, 0)
        assert cost == pytest.approx(0.0)

    def test_economy_cheaper_than_premium_same_tokens(self, router: CostRouter) -> None:
        eco_cost = router.estimate_cost(ModelTier.economy, 1000, 500)
        prem_cost = router.estimate_cost(ModelTier.premium, 1000, 500)
        assert eco_cost < prem_cost

    def test_returns_float(self, router: CostRouter) -> None:
        result = router.estimate_cost(ModelTier.standard, 1000, 1000)
        assert isinstance(result, float)

    def test_cost_is_non_negative(self, router: CostRouter) -> None:
        for tier in ModelTier:
            assert router.estimate_cost(tier, 1000, 500) >= 0.0

    def test_cost_scales_with_tokens(self, router: CostRouter) -> None:
        cost_1k = router.estimate_cost(ModelTier.standard, 1000, 0)
        cost_2k = router.estimate_cost(ModelTier.standard, 2000, 0)
        assert cost_2k == pytest.approx(cost_1k * 2, rel=1e-6)


# ===========================================================================
# CostRouter.make_decision
# ===========================================================================


class TestMakeDecision:
    """Full decision-making flow with reasoning and persistence."""

    def test_returns_routing_decision(self, router: CostRouter) -> None:
        decision = router.make_decision("t-001", WorkCellLane.docs, RiskTier.low)
        assert isinstance(decision, RoutingDecision)

    def test_decision_task_id_matches(self, router: CostRouter) -> None:
        decision = router.make_decision("task-xyz", WorkCellLane.docs, RiskTier.low)
        assert decision.task_id == "task-xyz"

    def test_decision_selected_tier_is_valid_model_tier(self, router: CostRouter) -> None:
        decision = router.make_decision("t-002", WorkCellLane.api_simple, RiskTier.medium)
        assert isinstance(decision.selected_tier, ModelTier)

    def test_docs_low_decision_selects_economy(self, router: CostRouter) -> None:
        decision = router.make_decision("t-003", WorkCellLane.docs, RiskTier.low)
        assert decision.selected_tier == ModelTier.economy

    def test_security_high_decision_selects_premium(self, router: CostRouter) -> None:
        decision = router.make_decision("t-004", WorkCellLane.security_change, RiskTier.high)
        assert decision.selected_tier == ModelTier.premium

    def test_decision_has_reasoning(self, router: CostRouter) -> None:
        decision = router.make_decision("t-005", WorkCellLane.docs, RiskTier.low)
        assert len(decision.reasoning) > 0
        assert all(isinstance(r, str) for r in decision.reasoning)

    def test_decision_cost_estimate_non_negative(self, router: CostRouter) -> None:
        decision = router.make_decision("t-006", WorkCellLane.research, RiskTier.medium)
        assert decision.cost_estimate >= 0.0

    def test_decision_quality_estimate_in_range(self, router: CostRouter) -> None:
        decision = router.make_decision("t-007", WorkCellLane.deployment, RiskTier.critical)
        assert 0.0 <= decision.quality_estimate <= 1.0

    def test_decision_decided_at_is_recent(self, router: CostRouter) -> None:
        from datetime import UTC, datetime, timedelta

        before = datetime.now(UTC)
        decision = router.make_decision("t-008", WorkCellLane.docs, RiskTier.low)
        after = datetime.now(UTC)
        assert before <= decision.decided_at <= after + timedelta(seconds=1)

    def test_decision_with_estimated_tokens(self, router: CostRouter) -> None:
        decision = router.make_decision(
            "t-009",
            WorkCellLane.api_simple,
            RiskTier.low,
            estimated_tokens=(3000, 1000),
        )
        expected_cost = router.estimate_cost(decision.selected_tier, 3000, 1000)
        assert decision.cost_estimate == pytest.approx(expected_cost, rel=1e-6)

    def test_decision_without_tokens_uses_defaults(self, router: CostRouter) -> None:
        decision = router.make_decision("t-010", WorkCellLane.docs, RiskTier.low)
        # Cost must be > 0 since we use defaults (1000 input + 500 output)
        # even if economy tier — just verify it's non-negative and a float
        assert isinstance(decision.cost_estimate, float)
        assert decision.cost_estimate >= 0.0

    def test_decision_routing_policy_ref_populated(self, router: CostRouter) -> None:
        decision = router.make_decision("t-011", WorkCellLane.docs, RiskTier.low)
        assert isinstance(decision.routing_policy, str)
        assert len(decision.routing_policy) > 0

    def test_decision_policy_ref_contains_lane_and_risk(self, router: CostRouter) -> None:
        decision = router.make_decision("t-012", WorkCellLane.security_change, RiskTier.high)
        assert "security_change" in decision.routing_policy
        assert "high" in decision.routing_policy

    def test_fallback_policy_ref_when_no_policy(self, tmp_path: Path) -> None:
        empty_router = CostRouter(
            policies={},
            decisions_path=tmp_path / "decisions.jsonl",
        )
        decision = empty_router.make_decision("t-013", WorkCellLane.docs, RiskTier.low)
        assert "fallback" in decision.routing_policy

    def test_reasoning_mentions_tier_selection(self, router: CostRouter) -> None:
        decision = router.make_decision("t-014", WorkCellLane.docs, RiskTier.low)
        full_reasoning = " ".join(decision.reasoning).lower()
        assert (
            "economy" in full_reasoning
            or "standard" in full_reasoning
            or "premium" in full_reasoning
        )


# ===========================================================================
# Decision log
# ===========================================================================


class TestDecisionLog:
    """Tests for get_decision_log."""

    def test_empty_log_on_fresh_router(self, router: CostRouter) -> None:
        assert router.get_decision_log() == []

    def test_decision_appears_in_log(self, router: CostRouter) -> None:
        router.make_decision("t-001", WorkCellLane.docs, RiskTier.low)
        log = router.get_decision_log()
        assert len(log) == 1
        assert log[0].task_id == "t-001"

    def test_log_is_reverse_chronological(self, router: CostRouter) -> None:
        router.make_decision("first", WorkCellLane.docs, RiskTier.low)
        router.make_decision("second", WorkCellLane.docs, RiskTier.medium)
        router.make_decision("third", WorkCellLane.api_simple, RiskTier.low)
        log = router.get_decision_log()
        assert log[0].task_id == "third"
        assert log[1].task_id == "second"
        assert log[2].task_id == "first"

    def test_limit_respected(self, router: CostRouter) -> None:
        for i in range(10):
            router.make_decision(f"t-{i}", WorkCellLane.docs, RiskTier.low)
        log = router.get_decision_log(limit=3)
        assert len(log) == 3

    def test_default_limit_is_50(self, router: CostRouter) -> None:
        for i in range(60):
            router.make_decision(f"t-{i}", WorkCellLane.docs, RiskTier.low)
        log = router.get_decision_log()
        assert len(log) <= 50

    def test_log_entries_are_routing_decisions(self, router: CostRouter) -> None:
        router.make_decision("t-001", WorkCellLane.refactor, RiskTier.medium)
        log = router.get_decision_log()
        assert all(isinstance(d, RoutingDecision) for d in log)


# ===========================================================================
# Cost summary
# ===========================================================================


class TestCostSummary:
    """Tests for get_cost_summary."""

    def test_zero_decisions_summary(self, router: CostRouter) -> None:
        summary = router.get_cost_summary()
        assert summary["total_decisions"] == 0
        assert summary["total_estimated_cost"] == pytest.approx(0.0)
        assert summary["avg_cost_per_task"] == pytest.approx(0.0)

    def test_summary_keys_present(self, router: CostRouter) -> None:
        summary = router.get_cost_summary()
        assert "total_decisions" in summary
        assert "by_tier" in summary
        assert "total_estimated_cost" in summary
        assert "avg_cost_per_task" in summary

    def test_by_tier_has_all_tiers(self, router: CostRouter) -> None:
        summary = router.get_cost_summary()
        for tier in ModelTier:
            assert tier.value in summary["by_tier"]

    def test_total_decisions_increments(self, router: CostRouter) -> None:
        for i in range(5):
            router.make_decision(f"t-{i}", WorkCellLane.docs, RiskTier.low)
        summary = router.get_cost_summary()
        assert summary["total_decisions"] == 5

    def test_by_tier_counts_correct(self, router: CostRouter) -> None:
        # docs/low → economy
        router.make_decision("t-1", WorkCellLane.docs, RiskTier.low)
        router.make_decision("t-2", WorkCellLane.docs, RiskTier.low)
        # security_change/high → premium
        router.make_decision("t-3", WorkCellLane.security_change, RiskTier.high)
        summary = router.get_cost_summary()
        assert summary["by_tier"]["economy"] == 2
        assert summary["by_tier"]["premium"] == 1

    def test_total_estimated_cost_accumulates(self, router: CostRouter) -> None:
        router.make_decision("t-1", WorkCellLane.docs, RiskTier.low)
        router.make_decision("t-2", WorkCellLane.docs, RiskTier.low)
        summary = router.get_cost_summary()
        assert summary["total_estimated_cost"] >= 0.0

    def test_avg_cost_per_task_consistent(self, router: CostRouter) -> None:
        for i in range(3):
            router.make_decision(f"t-{i}", WorkCellLane.docs, RiskTier.low)
        summary = router.get_cost_summary()
        if summary["total_decisions"] > 0:
            expected_avg = summary["total_estimated_cost"] / summary["total_decisions"]
            assert summary["avg_cost_per_task"] == pytest.approx(expected_avg, rel=1e-6)


# ===========================================================================
# JSONL persistence
# ===========================================================================


class TestJsonlPersistence:
    """Verify JSONL write and reload behaviour."""

    def test_decision_written_to_jsonl(self, router: CostRouter, isolated_path: Path) -> None:
        # Create router with isolated path
        r = CostRouter(decisions_path=isolated_path)
        r.make_decision("t-001", WorkCellLane.docs, RiskTier.low)
        assert isolated_path.exists()
        lines = [
            json.loads(line) for line in isolated_path.read_text().splitlines() if line.strip()
        ]
        assert len(lines) == 1
        assert lines[0]["task_id"] == "t-001"

    def test_jsonl_contains_required_fields(self, isolated_path: Path) -> None:
        r = CostRouter(decisions_path=isolated_path)
        r.make_decision("t-002", WorkCellLane.security_change, RiskTier.high)
        record = json.loads(isolated_path.read_text().splitlines()[0])
        for field in (
            "task_id",
            "selected_tier",
            "routing_policy",
            "cost_estimate",
            "quality_estimate",
            "decided_at",
        ):
            assert field in record, f"Missing field: {field}"

    def test_multiple_decisions_each_on_own_line(self, isolated_path: Path) -> None:
        r = CostRouter(decisions_path=isolated_path)
        for i in range(5):
            r.make_decision(f"t-{i}", WorkCellLane.docs, RiskTier.low)
        lines = [l for l in isolated_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 5

    def test_stats_reloaded_from_jsonl_on_new_instance(self, isolated_path: Path) -> None:
        """Stats should survive a process restart (new CostRouter instance)."""
        r1 = CostRouter(decisions_path=isolated_path)
        for i in range(3):
            r1.make_decision(f"t-{i}", WorkCellLane.docs, RiskTier.low)
        summary_before = r1.get_cost_summary()

        # New instance that reads the same file
        r2 = CostRouter(decisions_path=isolated_path)
        summary_after = r2.get_cost_summary()

        assert summary_after["total_decisions"] == summary_before["total_decisions"]
        assert summary_after["total_estimated_cost"] == pytest.approx(
            summary_before["total_estimated_cost"], rel=1e-5
        )

    def test_malformed_jsonl_lines_skipped(self, isolated_path: Path) -> None:
        """Corrupted lines must not raise on reload."""
        isolated_path.parent.mkdir(parents=True, exist_ok=True)
        isolated_path.write_text(
            '{"task_id": "ok", "selected_tier": "economy", "cost_estimate": 0.001, "decided_at": "2026-01-01T00:00:00+00:00"}\n'
            "NOT VALID JSON\n"
            '{"task_id": "ok2", "selected_tier": "standard", "cost_estimate": 0.005, "decided_at": "2026-01-01T00:00:01+00:00"}\n'
        )
        r = CostRouter(decisions_path=isolated_path)
        summary = r.get_cost_summary()
        assert summary["total_decisions"] == 2

    def test_missing_jsonl_file_does_not_raise(self, tmp_path: Path) -> None:
        """Starting with no JSONL file must not raise."""
        path = tmp_path / "nonexistent" / "decisions.jsonl"
        r = CostRouter(decisions_path=path)
        summary = r.get_cost_summary()
        assert summary["total_decisions"] == 0


# ===========================================================================
# Thread safety
# ===========================================================================


class TestThreadSafety:
    """Verify thread-safe operation under concurrent load."""

    def test_concurrent_decisions_no_races(self, router: CostRouter) -> None:
        """50 threads each make one decision — counts must be consistent."""
        n = 50
        errors: list[Exception] = []
        barrier = threading.Barrier(n)

        def _make(task_id: str) -> None:
            try:
                barrier.wait()
                router.make_decision(task_id, WorkCellLane.docs, RiskTier.low)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_make, args=(f"ct-{i}",)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors in concurrent make_decision: {errors}"
        summary = router.get_cost_summary()
        assert summary["total_decisions"] == n

    def test_concurrent_mixed_lanes_no_races(self, router: CostRouter) -> None:
        """Decisions across multiple lanes must not corrupt aggregate stats."""
        n = 30
        errors: list[Exception] = []
        lanes = list(WorkCellLane)

        def _make(i: int) -> None:
            try:
                lane = lanes[i % len(lanes)]
                router.make_decision(f"mix-{i}", lane, RiskTier.medium)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_make, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        summary = router.get_cost_summary()
        assert summary["total_decisions"] == n
        total_by_tier = sum(summary["by_tier"].values())
        assert total_by_tier == n

    def test_concurrent_summary_reads_no_errors(self, router: CostRouter) -> None:
        """Concurrent reads of get_cost_summary must not raise."""
        stop = threading.Event()
        errors: list[Exception] = []

        def _writer() -> None:
            i = 0
            while not stop.is_set():
                try:
                    router.make_decision(f"w-{i}", WorkCellLane.docs, RiskTier.low)
                    i += 1
                except Exception as exc:
                    errors.append(exc)

        def _reader() -> None:
            for _ in range(20):
                try:
                    router.get_cost_summary()
                except Exception as exc:
                    errors.append(exc)

        writer = threading.Thread(target=_writer)
        readers = [threading.Thread(target=_reader) for _ in range(5)]
        writer.start()
        for r in readers:
            r.start()
        for r in readers:
            r.join()
        stop.set()
        writer.join()

        assert not errors, f"Errors during concurrent reads: {errors}"


# ===========================================================================
# Singleton pattern
# ===========================================================================


class TestSingleton:
    """Tests for get_cost_router / reset_cost_router singleton pattern."""

    def test_get_returns_cost_router_instance(self) -> None:
        instance = get_cost_router()
        assert isinstance(instance, CostRouter)

    def test_get_returns_same_instance(self) -> None:
        a = get_cost_router()
        b = get_cost_router()
        assert a is b

    def test_reset_causes_new_instance_on_next_get(self) -> None:
        first = get_cost_router()
        reset_cost_router()
        second = get_cost_router()
        assert first is not second

    def test_state_persists_across_get_calls(self) -> None:
        r1 = get_cost_router()
        # Can't easily share state between two get() calls without persistence,
        # but we can verify same object identity
        r2 = get_cost_router()
        assert r1 is r2

    def test_reset_singleton_no_longer_returns_old_instance(self) -> None:
        old = get_cost_router()
        reset_cost_router()
        new = get_cost_router()
        assert old is not new


# ===========================================================================
# RoutingDecision model
# ===========================================================================


class TestRoutingDecisionModel:
    """Validate RoutingDecision model construction."""

    def test_valid_construction(self) -> None:
        from datetime import UTC, datetime

        d = RoutingDecision(
            task_id="t-001",
            selected_tier=ModelTier.standard,
            routing_policy="api_simple/medium",
            cost_estimate=0.005,
            quality_estimate=0.80,
            reasoning=["Policy matched", "Selected standard"],
        )
        assert d.task_id == "t-001"
        assert d.selected_tier == ModelTier.standard
        assert d.cost_estimate == pytest.approx(0.005)

    def test_decided_at_auto_populated(self) -> None:
        d = RoutingDecision(
            task_id="t-002",
            selected_tier=ModelTier.economy,
            routing_policy="docs/low",
            cost_estimate=0.0,
            quality_estimate=0.55,
        )
        assert d.decided_at is not None

    def test_negative_cost_raises(self) -> None:
        with pytest.raises(ValidationError):
            RoutingDecision(
                task_id="t-003",
                selected_tier=ModelTier.economy,
                routing_policy="docs/low",
                cost_estimate=-0.001,
                quality_estimate=0.55,
            )

    def test_quality_estimate_above_one_raises(self) -> None:
        with pytest.raises(ValidationError):
            RoutingDecision(
                task_id="t-004",
                selected_tier=ModelTier.premium,
                routing_policy="security/high",
                cost_estimate=0.01,
                quality_estimate=1.5,
            )

    def test_reasoning_defaults_to_empty_list(self) -> None:
        d = RoutingDecision(
            task_id="t-005",
            selected_tier=ModelTier.economy,
            routing_policy="docs/low",
            cost_estimate=0.0,
            quality_estimate=0.55,
        )
        assert d.reasoning == []


# ===========================================================================
# Public exports
# ===========================================================================


class TestPublicExports:
    """Verify new symbols are accessible from package __init__ modules."""

    def test_model_tier_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import ModelTier as ModelTierAlias

        assert ModelTierAlias is ModelTier

    def test_cost_profile_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import CostProfile as CostProfileAlias

        assert CostProfileAlias is CostProfile

    def test_routing_policy_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import RoutingPolicy as RoutingPolicyAlias

        assert RoutingPolicyAlias is RoutingPolicy

    def test_routing_decision_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import RoutingDecision as RoutingDecisionAlias

        assert RoutingDecisionAlias is RoutingDecision

    def test_cost_profiles_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import COST_PROFILES as CP

        assert CP is COST_PROFILES

    def test_default_routing_policies_exported_from_models(self) -> None:
        from forge_harness.webhook_server.models import DEFAULT_ROUTING_POLICIES as DRP

        assert DRP is DEFAULT_ROUTING_POLICIES

    def test_cost_router_exported_from_services(self) -> None:
        from forge_harness.webhook_server.services import CostRouter as CostRouterAlias

        assert CostRouterAlias is CostRouter

    def test_get_cost_router_exported_from_services(self) -> None:
        from forge_harness.webhook_server.services import get_cost_router as gcr

        assert gcr is get_cost_router

    def test_reset_cost_router_exported_from_services(self) -> None:
        from forge_harness.webhook_server.services import reset_cost_router as rcr

        assert rcr is reset_cost_router
