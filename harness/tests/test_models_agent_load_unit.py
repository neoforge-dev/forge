"""Unit tests for forge_harness/webhook_server/models/agent_load.py

Tests cover:
- BalancingStrategy enum: members, values, str-subclass behaviour
- AgentCapability model: defaults, validation, field constraints, edge cases
- BalancingRecommendation model: defaults, validation, field constraints, edge cases
- Round-trip serialisation (model_dump / model_validate)
- Pydantic validation errors for out-of-range and missing required fields
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from forge_harness.webhook_server.models.agent_load import (
    AgentCapability,
    BalancingRecommendation,
    BalancingStrategy,
)
from forge_harness.webhook_server.models.work_cell import WorkCellLane

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

ALL_LANES = list(WorkCellLane)
_LANE_A = WorkCellLane.api_simple
_LANE_B = WorkCellLane.test_writing
_LANE_SEC = WorkCellLane.security_change
_LANE_DEPLOY = WorkCellLane.deployment


# ===========================================================================
# BalancingStrategy
# ===========================================================================


class TestBalancingStrategy:
    """Tests for the BalancingStrategy enum."""

    def test_all_members_present(self):
        """Enum must expose exactly the three documented members."""
        members = {s.name for s in BalancingStrategy}
        assert members == {"round_robin", "least_loaded", "affinity_first"}

    def test_string_values(self):
        assert BalancingStrategy.round_robin.value == "round_robin"
        assert BalancingStrategy.least_loaded.value == "least_loaded"
        assert BalancingStrategy.affinity_first.value == "affinity_first"

    def test_is_str_subclass(self):
        """BalancingStrategy inherits from str, so each member IS a str."""
        for member in BalancingStrategy:
            assert isinstance(member, str)

    def test_str_equality_with_plain_string(self):
        """str comparison should work without calling .value."""
        assert BalancingStrategy.round_robin == "round_robin"
        assert BalancingStrategy.least_loaded == "least_loaded"
        assert BalancingStrategy.affinity_first == "affinity_first"

    def test_lookup_by_value(self):
        assert BalancingStrategy("round_robin") is BalancingStrategy.round_robin
        assert BalancingStrategy("least_loaded") is BalancingStrategy.least_loaded
        assert BalancingStrategy("affinity_first") is BalancingStrategy.affinity_first

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            BalancingStrategy("nonexistent")

    def test_members_are_unique(self):
        values = [s.value for s in BalancingStrategy]
        assert len(values) == len(set(values))

    def test_repr_contains_name(self):
        assert "round_robin" in repr(BalancingStrategy.round_robin)

    def test_usable_as_dict_key(self):
        mapping = {s: idx for idx, s in enumerate(BalancingStrategy)}
        assert mapping[BalancingStrategy.round_robin] == 0


# ===========================================================================
# AgentCapability
# ===========================================================================


class TestAgentCapabilityDefaults:
    """Verify default values produce a valid model."""

    def test_minimal_construction_with_agent_id(self):
        cap = AgentCapability(agent_id="forge:nova")
        assert cap.agent_id == "forge:nova"
        assert cap.supported_lanes == []
        assert cap.max_concurrent == 3
        assert cap.current_active == 0
        assert cap.context_budget_remaining_pct == 100.0
        assert cap.last_heartbeat_age_seconds == 0.0

    def test_default_supported_lanes_is_empty_list(self):
        cap = AgentCapability(agent_id="agent-x")
        assert isinstance(cap.supported_lanes, list)
        assert len(cap.supported_lanes) == 0

    def test_default_max_concurrent(self):
        cap = AgentCapability(agent_id="a")
        assert cap.max_concurrent == 3

    def test_default_current_active(self):
        cap = AgentCapability(agent_id="a")
        assert cap.current_active == 0

    def test_default_context_budget(self):
        cap = AgentCapability(agent_id="a")
        assert cap.context_budget_remaining_pct == 100.0

    def test_default_heartbeat_age(self):
        cap = AgentCapability(agent_id="a")
        assert cap.last_heartbeat_age_seconds == 0.0


class TestAgentCapabilityFullConstruction:
    """Test that every field is accepted when provided explicitly."""

    def test_full_construction(self):
        cap = AgentCapability(
            agent_id="forge:nova",
            supported_lanes=[_LANE_A, _LANE_B],
            max_concurrent=5,
            current_active=2,
            context_budget_remaining_pct=72.5,
            last_heartbeat_age_seconds=30.0,
        )
        assert cap.agent_id == "forge:nova"
        assert cap.supported_lanes == [_LANE_A, _LANE_B]
        assert cap.max_concurrent == 5
        assert cap.current_active == 2
        assert cap.context_budget_remaining_pct == 72.5
        assert cap.last_heartbeat_age_seconds == 30.0

    def test_all_lanes_supported(self):
        cap = AgentCapability(agent_id="omni", supported_lanes=ALL_LANES)
        assert len(cap.supported_lanes) == len(ALL_LANES)

    def test_single_lane(self):
        cap = AgentCapability(agent_id="sec-agent", supported_lanes=[_LANE_SEC])
        assert cap.supported_lanes == [_LANE_SEC]

    def test_lanes_accept_string_values_via_pydantic_coercion(self):
        """Pydantic coerces string values to WorkCellLane enum members."""
        cap = AgentCapability(
            agent_id="agent-1",
            supported_lanes=["api_simple", "docs"],
        )
        assert WorkCellLane.api_simple in cap.supported_lanes
        assert WorkCellLane.docs in cap.supported_lanes


class TestAgentCapabilityValidation:
    """Pydantic constraint violations must raise ValidationError."""

    def test_agent_id_required(self):
        with pytest.raises(ValidationError):
            AgentCapability()

    def test_max_concurrent_minimum_is_one(self):
        with pytest.raises(ValidationError):
            AgentCapability(agent_id="a", max_concurrent=0)

    def test_max_concurrent_negative(self):
        with pytest.raises(ValidationError):
            AgentCapability(agent_id="a", max_concurrent=-1)

    def test_max_concurrent_one_is_valid(self):
        cap = AgentCapability(agent_id="a", max_concurrent=1)
        assert cap.max_concurrent == 1

    def test_current_active_negative_raises(self):
        with pytest.raises(ValidationError):
            AgentCapability(agent_id="a", current_active=-1)

    def test_current_active_zero_is_valid(self):
        cap = AgentCapability(agent_id="a", current_active=0)
        assert cap.current_active == 0

    def test_context_budget_above_100_raises(self):
        with pytest.raises(ValidationError):
            AgentCapability(agent_id="a", context_budget_remaining_pct=100.1)

    def test_context_budget_below_0_raises(self):
        with pytest.raises(ValidationError):
            AgentCapability(agent_id="a", context_budget_remaining_pct=-0.1)

    def test_context_budget_zero_is_valid(self):
        cap = AgentCapability(agent_id="a", context_budget_remaining_pct=0.0)
        assert cap.context_budget_remaining_pct == 0.0

    def test_context_budget_100_is_valid(self):
        cap = AgentCapability(agent_id="a", context_budget_remaining_pct=100.0)
        assert cap.context_budget_remaining_pct == 100.0

    def test_heartbeat_age_negative_raises(self):
        with pytest.raises(ValidationError):
            AgentCapability(agent_id="a", last_heartbeat_age_seconds=-0.001)

    def test_heartbeat_age_zero_is_valid(self):
        cap = AgentCapability(agent_id="a", last_heartbeat_age_seconds=0.0)
        assert cap.last_heartbeat_age_seconds == 0.0

    def test_heartbeat_age_large_value_is_valid(self):
        cap = AgentCapability(agent_id="a", last_heartbeat_age_seconds=99999.9)
        assert cap.last_heartbeat_age_seconds == 99999.9

    def test_invalid_lane_string_raises(self):
        with pytest.raises(ValidationError):
            AgentCapability(agent_id="a", supported_lanes=["not_a_lane"])


class TestAgentCapabilityEdgeCases:
    """Boundary and unusual-but-valid inputs."""

    def test_large_max_concurrent(self):
        cap = AgentCapability(agent_id="a", max_concurrent=1000)
        assert cap.max_concurrent == 1000

    def test_agent_id_whitespace_string_accepted(self):
        """Pydantic does not strip whitespace by default."""
        cap = AgentCapability(agent_id="   ")
        assert cap.agent_id == "   "

    def test_context_budget_boundary_values(self):
        for pct in (0.0, 50.0, 100.0):
            cap = AgentCapability(agent_id="a", context_budget_remaining_pct=pct)
            assert cap.context_budget_remaining_pct == pct

    def test_duplicate_lanes_preserved(self):
        """Pydantic does not deduplicate list contents."""
        cap = AgentCapability(agent_id="a", supported_lanes=[_LANE_A, _LANE_A])
        assert len(cap.supported_lanes) == 2

    def test_current_active_equal_to_max_concurrent_is_valid(self):
        """Model does not enforce current_active <= max_concurrent at model level."""
        cap = AgentCapability(agent_id="a", max_concurrent=3, current_active=3)
        assert cap.current_active == 3

    def test_current_active_exceeds_max_concurrent_is_structurally_valid(self):
        """
        The docstring notes an unhealthy state but the model itself has no
        cross-field validator enforcing the invariant — that belongs to the
        load-balancer service.
        """
        cap = AgentCapability(agent_id="a", max_concurrent=2, current_active=5)
        assert cap.current_active == 5


class TestAgentCapabilitySerialization:
    """Round-trip via model_dump / model_validate."""

    def test_model_dump_contains_all_fields(self):
        cap = AgentCapability(
            agent_id="forge:nova",
            supported_lanes=[_LANE_A],
            max_concurrent=4,
            current_active=1,
            context_budget_remaining_pct=80.0,
            last_heartbeat_age_seconds=5.0,
        )
        d = cap.model_dump()
        assert d["agent_id"] == "forge:nova"
        assert d["max_concurrent"] == 4
        assert d["current_active"] == 1
        assert d["context_budget_remaining_pct"] == 80.0
        assert d["last_heartbeat_age_seconds"] == 5.0
        assert WorkCellLane.api_simple in d["supported_lanes"] or "api_simple" in [
            v if isinstance(v, str) else v.value for v in d["supported_lanes"]
        ]

    def test_round_trip(self):
        original = AgentCapability(
            agent_id="rtt-agent",
            supported_lanes=[_LANE_B, _LANE_SEC],
            max_concurrent=2,
            current_active=0,
            context_budget_remaining_pct=55.5,
            last_heartbeat_age_seconds=12.3,
        )
        reconstructed = AgentCapability.model_validate(original.model_dump())
        assert reconstructed == original

    def test_model_dump_mode_json(self):
        cap = AgentCapability(agent_id="a", supported_lanes=[_LANE_A])
        data = cap.model_dump(mode="json")
        # In JSON mode supported_lanes should be serialized to string values
        assert isinstance(data["supported_lanes"], list)

    def test_model_validate_from_dict(self):
        data = {
            "agent_id": "forge:kimi",
            "supported_lanes": ["api_stateful", "deployment"],
            "max_concurrent": 6,
            "current_active": 2,
            "context_budget_remaining_pct": 90.0,
            "last_heartbeat_age_seconds": 3.0,
        }
        cap = AgentCapability.model_validate(data)
        assert cap.agent_id == "forge:kimi"
        assert cap.max_concurrent == 6
        assert WorkCellLane.api_stateful in cap.supported_lanes
        assert WorkCellLane.deployment in cap.supported_lanes


# ===========================================================================
# BalancingRecommendation
# ===========================================================================


class TestBalancingRecommendationDefaults:
    """BalancingRecommendation has three required and one optional field."""

    def test_minimal_valid_construction(self):
        rec = BalancingRecommendation(
            agent_id="forge:nova",
            lane=_LANE_A,
            score=0.85,
        )
        assert rec.agent_id == "forge:nova"
        assert rec.lane == _LANE_A
        assert rec.score == 0.85
        assert rec.reasons == []

    def test_default_reasons_is_empty_list(self):
        rec = BalancingRecommendation(agent_id="a", lane=_LANE_B, score=0.5)
        assert isinstance(rec.reasons, list)
        assert rec.reasons == []


class TestBalancingRecommendationFullConstruction:
    def test_with_reasons(self):
        rec = BalancingRecommendation(
            agent_id="forge:nova",
            lane=_LANE_SEC,
            score=0.99,
            reasons=["capacity_score=0.9", "freshness_score=0.99"],
        )
        assert len(rec.reasons) == 2
        assert "capacity_score=0.9" in rec.reasons

    def test_score_boundary_zero(self):
        rec = BalancingRecommendation(agent_id="a", lane=_LANE_A, score=0.0)
        assert rec.score == 0.0

    def test_score_boundary_one(self):
        rec = BalancingRecommendation(agent_id="a", lane=_LANE_A, score=1.0)
        assert rec.score == 1.0

    def test_all_lanes_accepted(self):
        for lane in ALL_LANES:
            rec = BalancingRecommendation(agent_id="a", lane=lane, score=0.5)
            assert rec.lane == lane

    def test_lane_from_string_value(self):
        rec = BalancingRecommendation(agent_id="a", lane="refactor", score=0.5)
        assert rec.lane == WorkCellLane.refactor


class TestBalancingRecommendationValidation:
    """Pydantic constraint violations must raise ValidationError."""

    def test_agent_id_required(self):
        with pytest.raises(ValidationError):
            BalancingRecommendation(lane=_LANE_A, score=0.5)

    def test_lane_required(self):
        with pytest.raises(ValidationError):
            BalancingRecommendation(agent_id="a", score=0.5)

    def test_score_required(self):
        with pytest.raises(ValidationError):
            BalancingRecommendation(agent_id="a", lane=_LANE_A)

    def test_score_above_1_raises(self):
        with pytest.raises(ValidationError):
            BalancingRecommendation(agent_id="a", lane=_LANE_A, score=1.0001)

    def test_score_below_0_raises(self):
        with pytest.raises(ValidationError):
            BalancingRecommendation(agent_id="a", lane=_LANE_A, score=-0.001)

    def test_invalid_lane_string_raises(self):
        with pytest.raises(ValidationError):
            BalancingRecommendation(agent_id="a", lane="bad_lane", score=0.5)

    def test_reasons_none_raises(self):
        """None is not a valid list; pydantic should reject it."""
        with pytest.raises((ValidationError, TypeError)):
            BalancingRecommendation(agent_id="a", lane=_LANE_A, score=0.5, reasons=None)


class TestBalancingRecommendationEdgeCases:
    def test_many_reasons(self):
        reasons = [f"component_{i}=0.{i:02d}" for i in range(50)]
        rec = BalancingRecommendation(agent_id="a", lane=_LANE_A, score=0.5, reasons=reasons)
        assert len(rec.reasons) == 50

    def test_empty_reasons_list_explicitly(self):
        rec = BalancingRecommendation(agent_id="a", lane=_LANE_A, score=0.5, reasons=[])
        assert rec.reasons == []

    def test_score_mid_range(self):
        for score in (0.1, 0.25, 0.5, 0.75, 0.9):
            rec = BalancingRecommendation(agent_id="a", lane=_LANE_A, score=score)
            assert rec.score == pytest.approx(score)

    def test_agent_id_special_chars(self):
        rec = BalancingRecommendation(agent_id="forge:kimi-a-is", lane=_LANE_A, score=0.7)
        assert rec.agent_id == "forge:kimi-a-is"


class TestBalancingRecommendationSerialization:
    def test_round_trip(self):
        original = BalancingRecommendation(
            agent_id="forge:nova",
            lane=_LANE_DEPLOY,
            score=0.42,
            reasons=["capacity=0.6", "freshness=0.24"],
        )
        reconstructed = BalancingRecommendation.model_validate(original.model_dump())
        assert reconstructed == original

    def test_model_dump_keys(self):
        rec = BalancingRecommendation(agent_id="x", lane=_LANE_A, score=0.1)
        d = rec.model_dump()
        assert set(d.keys()) == {"agent_id", "lane", "score", "reasons"}

    def test_model_validate_from_dict_with_string_lane(self):
        data = {
            "agent_id": "bot-1",
            "lane": "security_change",
            "score": 0.95,
            "reasons": ["strict=True"],
        }
        rec = BalancingRecommendation.model_validate(data)
        assert rec.lane == WorkCellLane.security_change
        assert rec.score == 0.95

    def test_model_dump_mode_json(self):
        rec = BalancingRecommendation(agent_id="a", lane=_LANE_B, score=0.3)
        data = rec.model_dump(mode="json")
        assert isinstance(data["score"], float)
        assert isinstance(data["agent_id"], str)


# ===========================================================================
# Cross-model interaction / higher-level scenarios
# ===========================================================================


class TestCrossModelScenarios:
    """Simulate realistic load-balancer usage patterns."""

    def test_capability_and_recommendation_share_lane(self):
        lane = WorkCellLane.api_stateful
        cap = AgentCapability(
            agent_id="forge:nova",
            supported_lanes=[lane],
            max_concurrent=5,
            current_active=2,
            context_budget_remaining_pct=60.0,
            last_heartbeat_age_seconds=10.0,
        )
        rec = BalancingRecommendation(
            agent_id=cap.agent_id,
            lane=lane,
            score=0.75,
            reasons=["capacity_score=0.6", "freshness_score=0.9"],
        )
        assert rec.lane in cap.supported_lanes
        assert rec.agent_id == cap.agent_id

    def test_sorting_recommendations_by_score(self):
        """Recommendations are expected to be sorted descending; verify comparability."""
        recs = [
            BalancingRecommendation(agent_id=f"agent-{i}", lane=_LANE_A, score=score)
            for i, score in enumerate([0.3, 0.9, 0.1, 0.7])
        ]
        sorted_recs = sorted(recs, key=lambda r: r.score, reverse=True)
        assert sorted_recs[0].score == 0.9
        assert sorted_recs[-1].score == 0.1

    def test_zero_headroom_agent_capability(self):
        """Agent at max capacity is still a structurally valid model."""
        cap = AgentCapability(
            agent_id="maxed-out",
            supported_lanes=[_LANE_A],
            max_concurrent=3,
            current_active=3,
            context_budget_remaining_pct=5.0,
            last_heartbeat_age_seconds=55.0,
        )
        assert cap.current_active == cap.max_concurrent

    def test_exhausted_context_agent_capability(self):
        cap = AgentCapability(
            agent_id="exhausted",
            supported_lanes=[],
            context_budget_remaining_pct=0.0,
        )
        assert cap.context_budget_remaining_pct == 0.0

    def test_stale_agent_high_heartbeat_age(self):
        """Simulates an unresponsive agent where heartbeat age has grown large."""
        cap = AgentCapability(
            agent_id="stale-agent",
            last_heartbeat_age_seconds=3600.0,
        )
        assert cap.last_heartbeat_age_seconds == 3600.0

    def test_multiple_capabilities_different_agents(self):
        agents = [
            AgentCapability(agent_id=f"agent-{i}", max_concurrent=i + 1)
            for i in range(5)
        ]
        assert len(agents) == 5
        assert all(isinstance(a, AgentCapability) for a in agents)

    def test_balancing_strategy_used_in_context(self):
        """BalancingStrategy is importable and usable as a config value."""
        config = {"strategy": BalancingStrategy.least_loaded, "pool_size": 3}
        assert config["strategy"] == "least_loaded"
        assert isinstance(config["strategy"], BalancingStrategy)
