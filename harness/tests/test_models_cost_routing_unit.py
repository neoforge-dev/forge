import json
from datetime import UTC, datetime, timezone

import pytest
from pydantic import ValidationError

from forge_harness.webhook_server.models.cost_routing import (
    _TIER_ORDER,
    COST_PROFILES,
    DEFAULT_ROUTING_POLICIES,
    CostProfile,
    ModelTier,
    RoutingDecision,
    RoutingPolicy,
)
from forge_harness.webhook_server.models.lane_policy import RiskTier
from forge_harness.webhook_server.models.work_cell import WorkCellLane

# ---------------------------------------------------------------------------
# ModelTier enum
# ---------------------------------------------------------------------------


class TestModelTierEnum:
    def test_enum_values(self):
        assert ModelTier.economy.value == "economy"
        assert ModelTier.standard.value == "standard"
        assert ModelTier.premium.value == "premium"

    def test_enum_count(self):
        assert len(ModelTier) == 3

    def test_enum_is_str(self):
        for tier in ModelTier:
            assert isinstance(tier, str)

    def test_enum_members_accessible_by_value(self):
        assert ModelTier("economy") is ModelTier.economy
        assert ModelTier("standard") is ModelTier.standard
        assert ModelTier("premium") is ModelTier.premium

    def test_enum_str_equality(self):
        assert ModelTier.economy == "economy"
        assert ModelTier.standard == "standard"
        assert ModelTier.premium == "premium"

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ModelTier("ultra")

    def test_enum_identity(self):
        assert ModelTier.economy is ModelTier.economy
        assert ModelTier.standard is not ModelTier.economy


# ---------------------------------------------------------------------------
# _TIER_ORDER internal dict
# ---------------------------------------------------------------------------


class TestTierOrder:
    def test_economy_is_lowest(self):
        assert _TIER_ORDER[ModelTier.economy] == 0

    def test_standard_is_middle(self):
        assert _TIER_ORDER[ModelTier.standard] == 1

    def test_premium_is_highest(self):
        assert _TIER_ORDER[ModelTier.premium] == 2

    def test_all_tiers_present(self):
        for tier in ModelTier:
            assert tier in _TIER_ORDER

    def test_order_is_strictly_increasing(self):
        assert (
            _TIER_ORDER[ModelTier.economy]
            < _TIER_ORDER[ModelTier.standard]
            < _TIER_ORDER[ModelTier.premium]
        )


# ---------------------------------------------------------------------------
# CostProfile model
# ---------------------------------------------------------------------------


class TestCostProfileValid:
    def _make(self, **overrides):
        defaults = dict(
            model_tier=ModelTier.standard,
            cost_per_1k_input_tokens=0.003,
            cost_per_1k_output_tokens=0.015,
            quality_score=0.80,
            max_context_tokens=200_000,
        )
        defaults.update(overrides)
        return CostProfile(**defaults)

    def test_basic_construction(self):
        cp = self._make()
        assert cp.model_tier is ModelTier.standard
        assert cp.cost_per_1k_input_tokens == pytest.approx(0.003)
        assert cp.cost_per_1k_output_tokens == pytest.approx(0.015)
        assert cp.quality_score == pytest.approx(0.80)
        assert cp.max_context_tokens == 200_000

    def test_economy_tier(self):
        cp = self._make(
            model_tier=ModelTier.economy,
            cost_per_1k_input_tokens=0.00025,
            cost_per_1k_output_tokens=0.00125,
            quality_score=0.55,
        )
        assert cp.model_tier is ModelTier.economy

    def test_premium_tier(self):
        cp = self._make(
            model_tier=ModelTier.premium,
            cost_per_1k_input_tokens=0.015,
            cost_per_1k_output_tokens=0.075,
            quality_score=1.00,
        )
        assert cp.model_tier is ModelTier.premium

    def test_zero_cost_allowed(self):
        cp = self._make(cost_per_1k_input_tokens=0.0, cost_per_1k_output_tokens=0.0)
        assert cp.cost_per_1k_input_tokens == 0.0
        assert cp.cost_per_1k_output_tokens == 0.0

    def test_quality_score_zero(self):
        cp = self._make(quality_score=0.0)
        assert cp.quality_score == 0.0

    def test_quality_score_one(self):
        cp = self._make(quality_score=1.0)
        assert cp.quality_score == 1.0

    def test_max_context_tokens_minimum(self):
        cp = self._make(max_context_tokens=1)
        assert cp.max_context_tokens == 1

    def test_is_frozen(self):
        cp = self._make()
        with pytest.raises(Exception):
            cp.quality_score = 0.5

    def test_serialization_round_trip(self):
        cp = self._make()
        data = cp.model_dump()
        cp2 = CostProfile(**data)
        assert cp2 == cp

    def test_json_serialization(self):
        cp = self._make()
        raw = cp.model_dump_json()
        parsed = json.loads(raw)
        assert parsed["model_tier"] == "standard"
        assert parsed["quality_score"] == pytest.approx(0.80)


class TestCostProfileInvalid:
    def _base(self):
        return dict(
            model_tier=ModelTier.standard,
            cost_per_1k_input_tokens=0.003,
            cost_per_1k_output_tokens=0.015,
            quality_score=0.80,
            max_context_tokens=200_000,
        )

    def test_negative_input_cost_raises(self):
        kw = self._base()
        kw["cost_per_1k_input_tokens"] = -0.001
        with pytest.raises(ValidationError):
            CostProfile(**kw)

    def test_negative_output_cost_raises(self):
        kw = self._base()
        kw["cost_per_1k_output_tokens"] = -0.001
        with pytest.raises(ValidationError):
            CostProfile(**kw)

    def test_quality_score_above_one_raises(self):
        kw = self._base()
        kw["quality_score"] = 1.01
        with pytest.raises(ValidationError):
            CostProfile(**kw)

    def test_quality_score_below_zero_raises(self):
        kw = self._base()
        kw["quality_score"] = -0.01
        with pytest.raises(ValidationError):
            CostProfile(**kw)

    def test_max_context_tokens_zero_raises(self):
        kw = self._base()
        kw["max_context_tokens"] = 0
        with pytest.raises(ValidationError):
            CostProfile(**kw)

    def test_max_context_tokens_negative_raises(self):
        kw = self._base()
        kw["max_context_tokens"] = -1
        with pytest.raises(ValidationError):
            CostProfile(**kw)

    def test_missing_model_tier_raises(self):
        kw = self._base()
        del kw["model_tier"]
        with pytest.raises(ValidationError):
            CostProfile(**kw)

    def test_missing_quality_score_raises(self):
        kw = self._base()
        del kw["quality_score"]
        with pytest.raises(ValidationError):
            CostProfile(**kw)

    def test_invalid_model_tier_string_raises(self):
        kw = self._base()
        kw["model_tier"] = "ultra"
        with pytest.raises(ValidationError):
            CostProfile(**kw)


# ---------------------------------------------------------------------------
# RoutingPolicy model
# ---------------------------------------------------------------------------


class TestRoutingPolicyValid:
    def _make(self, **overrides):
        defaults = dict(
            lane=WorkCellLane.docs,
            risk_tier=RiskTier.low,
            min_model_tier=ModelTier.economy,
            max_model_tier=ModelTier.standard,
            quality_target=0.50,
        )
        defaults.update(overrides)
        return RoutingPolicy(**defaults)

    def test_basic_construction(self):
        rp = self._make()
        assert rp.lane is WorkCellLane.docs
        assert rp.risk_tier is RiskTier.low
        assert rp.min_model_tier is ModelTier.economy
        assert rp.max_model_tier is ModelTier.standard
        assert rp.quality_target == pytest.approx(0.50)
        assert rp.budget_limit_per_task is None

    def test_same_tier_min_max_allowed(self):
        rp = self._make(
            min_model_tier=ModelTier.premium, max_model_tier=ModelTier.premium
        )
        assert rp.min_model_tier is ModelTier.premium
        assert rp.max_model_tier is ModelTier.premium

    def test_economy_to_premium_allowed(self):
        rp = self._make(
            min_model_tier=ModelTier.economy, max_model_tier=ModelTier.premium
        )
        assert rp.min_model_tier is ModelTier.economy
        assert rp.max_model_tier is ModelTier.premium

    def test_default_quality_target(self):
        rp = RoutingPolicy(
            lane=WorkCellLane.docs,
            risk_tier=RiskTier.low,
            min_model_tier=ModelTier.economy,
            max_model_tier=ModelTier.standard,
        )
        assert rp.quality_target == pytest.approx(0.5)

    def test_quality_target_zero(self):
        rp = self._make(quality_target=0.0)
        assert rp.quality_target == 0.0

    def test_quality_target_one(self):
        rp = self._make(quality_target=1.0)
        assert rp.quality_target == 1.0

    def test_budget_limit_zero_allowed(self):
        rp = self._make(budget_limit_per_task=0.0)
        assert rp.budget_limit_per_task == 0.0

    def test_budget_limit_positive(self):
        rp = self._make(budget_limit_per_task=5.00)
        assert rp.budget_limit_per_task == pytest.approx(5.00)

    def test_budget_limit_none_default(self):
        rp = self._make()
        assert rp.budget_limit_per_task is None

    def test_is_frozen(self):
        rp = self._make()
        with pytest.raises(Exception):
            rp.quality_target = 0.9

    def test_all_lanes_accepted(self):
        for lane in WorkCellLane:
            rp = self._make(lane=lane)
            assert rp.lane is lane

    def test_all_risk_tiers_accepted(self):
        for rt in RiskTier:
            rp = self._make(risk_tier=rt)
            assert rp.risk_tier is rt

    def test_serialization_round_trip(self):
        rp = self._make(budget_limit_per_task=1.23)
        data = rp.model_dump()
        rp2 = RoutingPolicy(**data)
        assert rp2 == rp


class TestRoutingPolicyInvalid:
    def _base(self):
        return dict(
            lane=WorkCellLane.docs,
            risk_tier=RiskTier.low,
            min_model_tier=ModelTier.economy,
            max_model_tier=ModelTier.standard,
            quality_target=0.50,
        )

    def test_min_above_max_raises(self):
        kw = self._base()
        kw["min_model_tier"] = ModelTier.premium
        kw["max_model_tier"] = ModelTier.economy
        with pytest.raises(ValidationError) as exc_info:
            RoutingPolicy(**kw)
        assert "min_model_tier" in str(exc_info.value)

    def test_standard_min_economy_max_raises(self):
        kw = self._base()
        kw["min_model_tier"] = ModelTier.standard
        kw["max_model_tier"] = ModelTier.economy
        with pytest.raises(ValidationError):
            RoutingPolicy(**kw)

    def test_premium_min_standard_max_raises(self):
        kw = self._base()
        kw["min_model_tier"] = ModelTier.premium
        kw["max_model_tier"] = ModelTier.standard
        with pytest.raises(ValidationError):
            RoutingPolicy(**kw)

    def test_quality_target_above_one_raises(self):
        kw = self._base()
        kw["quality_target"] = 1.01
        with pytest.raises(ValidationError):
            RoutingPolicy(**kw)

    def test_quality_target_below_zero_raises(self):
        kw = self._base()
        kw["quality_target"] = -0.01
        with pytest.raises(ValidationError):
            RoutingPolicy(**kw)

    def test_negative_budget_raises(self):
        kw = self._base()
        kw["budget_limit_per_task"] = -1.0
        with pytest.raises(ValidationError):
            RoutingPolicy(**kw)

    def test_missing_lane_raises(self):
        kw = self._base()
        del kw["lane"]
        with pytest.raises(ValidationError):
            RoutingPolicy(**kw)

    def test_missing_risk_tier_raises(self):
        kw = self._base()
        del kw["risk_tier"]
        with pytest.raises(ValidationError):
            RoutingPolicy(**kw)

    def test_missing_min_model_tier_raises(self):
        kw = self._base()
        del kw["min_model_tier"]
        with pytest.raises(ValidationError):
            RoutingPolicy(**kw)

    def test_missing_max_model_tier_raises(self):
        kw = self._base()
        del kw["max_model_tier"]
        with pytest.raises(ValidationError):
            RoutingPolicy(**kw)

    def test_invalid_lane_string_raises(self):
        kw = self._base()
        kw["lane"] = "nonexistent_lane"
        with pytest.raises(ValidationError):
            RoutingPolicy(**kw)

    def test_invalid_risk_tier_raises(self):
        kw = self._base()
        kw["risk_tier"] = "extreme"
        with pytest.raises(ValidationError):
            RoutingPolicy(**kw)


# ---------------------------------------------------------------------------
# RoutingDecision model
# ---------------------------------------------------------------------------


class TestRoutingDecisionValid:
    def _make(self, **overrides):
        defaults = dict(
            task_id="task-001",
            selected_tier=ModelTier.standard,
            routing_policy="docs/low",
        )
        defaults.update(overrides)
        return RoutingDecision(**defaults)

    def test_basic_construction(self):
        rd = self._make()
        assert rd.task_id == "task-001"
        assert rd.selected_tier is ModelTier.standard
        assert rd.routing_policy == "docs/low"
        assert rd.cost_estimate == pytest.approx(0.0)
        assert rd.quality_estimate == pytest.approx(0.0)
        assert rd.reasoning == []
        assert isinstance(rd.decided_at, datetime)

    def test_decided_at_is_utc(self):
        rd = self._make()
        assert rd.decided_at.tzinfo is not None
        assert rd.decided_at.utcoffset().total_seconds() == 0

    def test_custom_decided_at(self):
        ts = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
        rd = self._make(decided_at=ts)
        assert rd.decided_at == ts

    def test_cost_estimate_zero_default(self):
        rd = self._make()
        assert rd.cost_estimate == 0.0

    def test_cost_estimate_positive(self):
        rd = self._make(cost_estimate=0.05)
        assert rd.cost_estimate == pytest.approx(0.05)

    def test_quality_estimate_zero_default(self):
        rd = self._make()
        assert rd.quality_estimate == 0.0

    def test_quality_estimate_one(self):
        rd = self._make(quality_estimate=1.0)
        assert rd.quality_estimate == 1.0

    def test_reasoning_default_empty_list(self):
        rd = self._make()
        assert rd.reasoning == []

    def test_reasoning_provided(self):
        rd = self._make(reasoning=["selected economy", "quality target met"])
        assert rd.reasoning == ["selected economy", "quality target met"]

    def test_reasoning_single_entry(self):
        rd = self._make(reasoning=["only reason"])
        assert len(rd.reasoning) == 1

    def test_all_model_tiers_accepted(self):
        for tier in ModelTier:
            rd = self._make(selected_tier=tier)
            assert rd.selected_tier is tier

    def test_serialization_round_trip(self):
        ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        rd = self._make(
            cost_estimate=0.01,
            quality_estimate=0.8,
            reasoning=["r1", "r2"],
            decided_at=ts,
        )
        data = rd.model_dump()
        rd2 = RoutingDecision(**data)
        assert rd2.task_id == rd.task_id
        assert rd2.cost_estimate == rd.cost_estimate
        assert rd2.reasoning == rd.reasoning

    def test_task_id_arbitrary_string(self):
        rd = self._make(task_id="abc-xyz-123")
        assert rd.task_id == "abc-xyz-123"

    def test_routing_policy_arbitrary_string(self):
        rd = self._make(routing_policy="security_change/critical")
        assert rd.routing_policy == "security_change/critical"

    def test_json_serialization_contains_tier(self):
        rd = self._make(selected_tier=ModelTier.economy)
        raw = rd.model_dump_json()
        parsed = json.loads(raw)
        assert parsed["selected_tier"] == "economy"


class TestRoutingDecisionInvalid:
    def _base(self):
        return dict(
            task_id="task-001",
            selected_tier=ModelTier.standard,
            routing_policy="docs/low",
        )

    def test_missing_task_id_raises(self):
        kw = self._base()
        del kw["task_id"]
        with pytest.raises(ValidationError):
            RoutingDecision(**kw)

    def test_missing_selected_tier_raises(self):
        kw = self._base()
        del kw["selected_tier"]
        with pytest.raises(ValidationError):
            RoutingDecision(**kw)

    def test_missing_routing_policy_raises(self):
        kw = self._base()
        del kw["routing_policy"]
        with pytest.raises(ValidationError):
            RoutingDecision(**kw)

    def test_negative_cost_estimate_raises(self):
        kw = self._base()
        kw["cost_estimate"] = -0.01
        with pytest.raises(ValidationError):
            RoutingDecision(**kw)

    def test_negative_quality_estimate_raises(self):
        kw = self._base()
        kw["quality_estimate"] = -0.01
        with pytest.raises(ValidationError):
            RoutingDecision(**kw)

    def test_quality_estimate_above_one_raises(self):
        kw = self._base()
        kw["quality_estimate"] = 1.01
        with pytest.raises(ValidationError):
            RoutingDecision(**kw)

    def test_invalid_selected_tier_raises(self):
        kw = self._base()
        kw["selected_tier"] = "ultra"
        with pytest.raises(ValidationError):
            RoutingDecision(**kw)


# ---------------------------------------------------------------------------
# COST_PROFILES constant
# ---------------------------------------------------------------------------


class TestCostProfiles:
    def test_all_tiers_present(self):
        for tier in ModelTier:
            assert tier in COST_PROFILES

    def test_keys_are_model_tiers(self):
        for key in COST_PROFILES:
            assert isinstance(key, ModelTier)

    def test_values_are_cost_profiles(self):
        for value in COST_PROFILES.values():
            assert isinstance(value, CostProfile)

    def test_tier_matches_key(self):
        for tier, profile in COST_PROFILES.items():
            assert profile.model_tier is tier

    def test_economy_pricing(self):
        cp = COST_PROFILES[ModelTier.economy]
        assert cp.cost_per_1k_input_tokens == pytest.approx(0.00025)
        assert cp.cost_per_1k_output_tokens == pytest.approx(0.00125)
        assert cp.quality_score == pytest.approx(0.55)

    def test_standard_pricing(self):
        cp = COST_PROFILES[ModelTier.standard]
        assert cp.cost_per_1k_input_tokens == pytest.approx(0.003)
        assert cp.cost_per_1k_output_tokens == pytest.approx(0.015)
        assert cp.quality_score == pytest.approx(0.80)

    def test_premium_pricing(self):
        cp = COST_PROFILES[ModelTier.premium]
        assert cp.cost_per_1k_input_tokens == pytest.approx(0.015)
        assert cp.cost_per_1k_output_tokens == pytest.approx(0.075)
        assert cp.quality_score == pytest.approx(1.00)

    def test_economy_context_window(self):
        assert COST_PROFILES[ModelTier.economy].max_context_tokens == 200_000

    def test_standard_context_window(self):
        assert COST_PROFILES[ModelTier.standard].max_context_tokens == 200_000

    def test_premium_context_window(self):
        assert COST_PROFILES[ModelTier.premium].max_context_tokens == 200_000

    def test_quality_ordering(self):
        economy_q = COST_PROFILES[ModelTier.economy].quality_score
        standard_q = COST_PROFILES[ModelTier.standard].quality_score
        premium_q = COST_PROFILES[ModelTier.premium].quality_score
        assert economy_q < standard_q < premium_q

    def test_cost_ordering_input_tokens(self):
        economy_c = COST_PROFILES[ModelTier.economy].cost_per_1k_input_tokens
        standard_c = COST_PROFILES[ModelTier.standard].cost_per_1k_input_tokens
        premium_c = COST_PROFILES[ModelTier.premium].cost_per_1k_input_tokens
        assert economy_c < standard_c < premium_c

    def test_cost_ordering_output_tokens(self):
        economy_c = COST_PROFILES[ModelTier.economy].cost_per_1k_output_tokens
        standard_c = COST_PROFILES[ModelTier.standard].cost_per_1k_output_tokens
        premium_c = COST_PROFILES[ModelTier.premium].cost_per_1k_output_tokens
        assert economy_c < standard_c < premium_c

    def test_all_quality_scores_in_range(self):
        for cp in COST_PROFILES.values():
            assert 0.0 <= cp.quality_score <= 1.0

    def test_all_costs_non_negative(self):
        for cp in COST_PROFILES.values():
            assert cp.cost_per_1k_input_tokens >= 0
            assert cp.cost_per_1k_output_tokens >= 0

    def test_profiles_are_frozen(self):
        cp = COST_PROFILES[ModelTier.economy]
        with pytest.raises(Exception):
            cp.quality_score = 0.9


# ---------------------------------------------------------------------------
# DEFAULT_ROUTING_POLICIES constant
# ---------------------------------------------------------------------------


class TestDefaultRoutingPolicies:
    def test_completeness(self):
        expected = len(WorkCellLane) * len(RiskTier)
        assert len(DEFAULT_ROUTING_POLICIES) == expected

    def test_all_lanes_covered(self):
        lanes_covered = {k[0] for k in DEFAULT_ROUTING_POLICIES}
        for lane in WorkCellLane:
            assert lane in lanes_covered

    def test_all_risk_tiers_covered(self):
        tiers_covered = {k[1] for k in DEFAULT_ROUTING_POLICIES}
        for rt in RiskTier:
            assert rt in tiers_covered

    def test_all_combinations_present(self):
        for lane in WorkCellLane:
            for rt in RiskTier:
                assert (lane, rt) in DEFAULT_ROUTING_POLICIES

    def test_keys_are_tuples(self):
        for key in DEFAULT_ROUTING_POLICIES:
            assert isinstance(key, tuple)
            assert len(key) == 2

    def test_values_are_routing_policies(self):
        for value in DEFAULT_ROUTING_POLICIES.values():
            assert isinstance(value, RoutingPolicy)

    def test_lane_matches_key(self):
        for (lane, rt), policy in DEFAULT_ROUTING_POLICIES.items():
            assert policy.lane is lane

    def test_risk_tier_matches_key(self):
        for (lane, rt), policy in DEFAULT_ROUTING_POLICIES.items():
            assert policy.risk_tier is rt

    def test_min_lte_max_for_all_policies(self):
        for policy in DEFAULT_ROUTING_POLICIES.values():
            assert _TIER_ORDER[policy.min_model_tier] <= _TIER_ORDER[policy.max_model_tier]

    def test_quality_targets_in_range(self):
        for policy in DEFAULT_ROUTING_POLICIES.values():
            assert 0.0 <= policy.quality_target <= 1.0

    # ------------------------------------------------------------------
    # docs lane
    # ------------------------------------------------------------------

    def test_docs_low_economy_only(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.docs, RiskTier.low)]
        assert p.min_model_tier is ModelTier.economy
        assert p.max_model_tier is ModelTier.economy

    def test_docs_medium_economy_to_standard(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.docs, RiskTier.medium)]
        assert p.min_model_tier is ModelTier.economy
        assert p.max_model_tier is ModelTier.standard

    def test_docs_high_standard_only(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.docs, RiskTier.high)]
        assert p.min_model_tier is ModelTier.standard
        assert p.max_model_tier is ModelTier.standard

    def test_docs_critical_standard_to_premium(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.docs, RiskTier.critical)]
        assert p.min_model_tier is ModelTier.standard
        assert p.max_model_tier is ModelTier.premium

    # ------------------------------------------------------------------
    # test_writing lane
    # ------------------------------------------------------------------

    def test_test_writing_low_quality_target(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.test_writing, RiskTier.low)]
        assert p.quality_target == pytest.approx(0.50)

    def test_test_writing_critical_standard_to_premium(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.test_writing, RiskTier.critical)]
        assert p.min_model_tier is ModelTier.standard
        assert p.max_model_tier is ModelTier.premium
        assert p.quality_target == pytest.approx(0.85)

    def test_test_writing_high_standard_only(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.test_writing, RiskTier.high)]
        assert p.min_model_tier is ModelTier.standard
        assert p.max_model_tier is ModelTier.standard

    # ------------------------------------------------------------------
    # api_simple lane
    # ------------------------------------------------------------------

    def test_api_simple_low_economy_start(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.api_simple, RiskTier.low)]
        assert p.min_model_tier is ModelTier.economy

    def test_api_simple_critical_premium_only(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.api_simple, RiskTier.critical)]
        assert p.min_model_tier is ModelTier.premium
        assert p.max_model_tier is ModelTier.premium
        assert p.quality_target == pytest.approx(0.95)

    def test_api_simple_medium_standard_only(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.api_simple, RiskTier.medium)]
        assert p.min_model_tier is ModelTier.standard
        assert p.max_model_tier is ModelTier.standard

    # ------------------------------------------------------------------
    # api_stateful lane — standard minimum everywhere
    # ------------------------------------------------------------------

    def test_api_stateful_low_standard_min(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.api_stateful, RiskTier.low)]
        assert p.min_model_tier is ModelTier.standard

    def test_api_stateful_medium_standard_only(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.api_stateful, RiskTier.medium)]
        assert p.min_model_tier is ModelTier.standard
        assert p.max_model_tier is ModelTier.standard

    def test_api_stateful_critical_premium_only(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.api_stateful, RiskTier.critical)]
        assert p.min_model_tier is ModelTier.premium
        assert p.max_model_tier is ModelTier.premium

    def test_api_stateful_no_economy(self):
        for rt in RiskTier:
            p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.api_stateful, rt)]
            assert p.min_model_tier is not ModelTier.economy

    # ------------------------------------------------------------------
    # research lane
    # ------------------------------------------------------------------

    def test_research_low_standard_to_premium(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.research, RiskTier.low)]
        assert p.min_model_tier is ModelTier.standard
        assert p.max_model_tier is ModelTier.premium

    def test_research_critical_premium_only(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.research, RiskTier.critical)]
        assert p.min_model_tier is ModelTier.premium
        assert p.max_model_tier is ModelTier.premium

    def test_research_no_economy_minimum(self):
        for rt in RiskTier:
            p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.research, rt)]
            assert p.min_model_tier is not ModelTier.economy

    # ------------------------------------------------------------------
    # refactor lane
    # ------------------------------------------------------------------

    def test_refactor_low_economy_start(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.refactor, RiskTier.low)]
        assert p.min_model_tier is ModelTier.economy

    def test_refactor_critical_premium_only(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.refactor, RiskTier.critical)]
        assert p.min_model_tier is ModelTier.premium
        assert p.max_model_tier is ModelTier.premium

    # ------------------------------------------------------------------
    # security_change lane
    # ------------------------------------------------------------------

    def test_security_change_low_standard_min(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.security_change, RiskTier.low)]
        assert p.min_model_tier is ModelTier.standard

    def test_security_change_high_premium_only(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.security_change, RiskTier.high)]
        assert p.min_model_tier is ModelTier.premium
        assert p.max_model_tier is ModelTier.premium

    def test_security_change_critical_premium_quality_one(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.security_change, RiskTier.critical)]
        assert p.min_model_tier is ModelTier.premium
        assert p.max_model_tier is ModelTier.premium
        assert p.quality_target == pytest.approx(1.00)

    def test_security_change_no_economy(self):
        for rt in RiskTier:
            p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.security_change, rt)]
            assert p.min_model_tier is not ModelTier.economy

    # ------------------------------------------------------------------
    # deployment lane
    # ------------------------------------------------------------------

    def test_deployment_low_standard_min(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.deployment, RiskTier.low)]
        assert p.min_model_tier is ModelTier.standard

    def test_deployment_high_premium_only(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.deployment, RiskTier.high)]
        assert p.min_model_tier is ModelTier.premium
        assert p.max_model_tier is ModelTier.premium

    def test_deployment_critical_premium_quality_one(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.deployment, RiskTier.critical)]
        assert p.min_model_tier is ModelTier.premium
        assert p.max_model_tier is ModelTier.premium
        assert p.quality_target == pytest.approx(1.00)

    def test_deployment_no_economy(self):
        for rt in RiskTier:
            p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.deployment, rt)]
            assert p.min_model_tier is not ModelTier.economy

    # ------------------------------------------------------------------
    # Cross-cutting invariants
    # ------------------------------------------------------------------

    def test_higher_risk_never_lower_quality_target(self):
        risk_order = [RiskTier.low, RiskTier.medium, RiskTier.high, RiskTier.critical]
        for lane in WorkCellLane:
            targets = [
                DEFAULT_ROUTING_POLICIES[(lane, rt)].quality_target
                for rt in risk_order
            ]
            for i in range(len(targets) - 1):
                assert targets[i] <= targets[i + 1], (
                    f"Lane {lane}: quality_target decreased from "
                    f"{risk_order[i]} ({targets[i]}) to "
                    f"{risk_order[i+1]} ({targets[i+1]})"
                )

    def test_higher_risk_min_tier_never_decreases(self):
        risk_order = [RiskTier.low, RiskTier.medium, RiskTier.high, RiskTier.critical]
        for lane in WorkCellLane:
            min_tiers = [
                _TIER_ORDER[DEFAULT_ROUTING_POLICIES[(lane, rt)].min_model_tier]
                for rt in risk_order
            ]
            for i in range(len(min_tiers) - 1):
                assert min_tiers[i] <= min_tiers[i + 1], (
                    f"Lane {lane}: min_model_tier decreased from "
                    f"{risk_order[i]} to {risk_order[i+1]}"
                )

    def test_policies_are_frozen(self):
        p = DEFAULT_ROUTING_POLICIES[(WorkCellLane.docs, RiskTier.low)]
        with pytest.raises(Exception):
            p.quality_target = 0.99

    def test_no_budget_limit_by_default(self):
        for policy in DEFAULT_ROUTING_POLICIES.values():
            assert policy.budget_limit_per_task is None


# ---------------------------------------------------------------------------
# RoutingDecision default_factory isolation
# ---------------------------------------------------------------------------


class TestRoutingDecisionDefaultFactory:
    def test_two_instances_have_independent_reasoning_lists(self):
        rd1 = RoutingDecision(
            task_id="a", selected_tier=ModelTier.economy, routing_policy="x"
        )
        rd2 = RoutingDecision(
            task_id="b", selected_tier=ModelTier.economy, routing_policy="x"
        )
        rd1.reasoning.append("note")
        assert "note" not in rd2.reasoning

    def test_decided_at_auto_populated(self):
        before = datetime.now(UTC)
        rd = RoutingDecision(
            task_id="t", selected_tier=ModelTier.economy, routing_policy="p"
        )
        after = datetime.now(UTC)
        assert before <= rd.decided_at <= after
