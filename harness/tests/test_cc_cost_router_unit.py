"""Unit tests for forge_harness.webhook_server.services.cost_router.

Covers:
- CostRouter.__init__ (with and without custom args, with persisted file)
- CostRouter.resolve_tier (policy match, fallback, quality_target override, clamping)
- CostRouter.estimate_cost (economy, standard, premium tiers)
- CostRouter.make_decision (with/without estimated_tokens, fallback policy path)
- CostRouter.get_decision_log (limit, ordering)
- CostRouter.get_cost_summary (zero, single, multiple decisions)
- CostRouter._load_persisted_decisions (missing file, valid lines, malformed lines, empty lines)
- CostRouter._persist_decision (happy path, OSError)
- CostRouter._append_decision (stats updated, log appended, persist called)
- get_cost_router / reset_cost_router (singleton lifecycle, thread-safety)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
import forge_harness.webhook_server.services.cost_router as cost_router_module
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
    _DEFAULT_INPUT_TOKENS,
    _DEFAULT_OUTPUT_TOKENS,
    _MAX_LOG_ENTRIES,
    CostRouter,
    get_cost_router,
    reset_cost_router,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ECONOMY_PROFILE = COST_PROFILES[ModelTier.economy]
_STANDARD_PROFILE = COST_PROFILES[ModelTier.standard]
_PREMIUM_PROFILE = COST_PROFILES[ModelTier.premium]


def _make_router(tmp_path: Path, **kwargs) -> CostRouter:
    """Create a CostRouter that writes to *tmp_path* (no file pre-exists)."""
    decisions_path = tmp_path / ".forge" / "routing" / "decisions.jsonl"
    return CostRouter(decisions_path=decisions_path, **kwargs)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    """Write *records* to *path* as JSONL, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


# ===========================================================================
# TestCostRouterInit
# ===========================================================================


class TestCostRouterInit:
    """CostRouter.__init__ — construction and defaults."""

    def test_defaults_are_applied(self, tmp_path):
        router = _make_router(tmp_path)
        assert router._policies is DEFAULT_ROUTING_POLICIES
        assert router._profiles is COST_PROFILES
        assert router._total_estimated_cost == 0.0
        assert all(v == 0 for v in router._tier_counts.values())

    def test_custom_policies_used(self, tmp_path):
        custom = {}
        router = _make_router(tmp_path, policies=custom)
        assert router._policies is custom

    def test_custom_profiles_used(self, tmp_path):
        custom_profiles = {t: COST_PROFILES[t] for t in ModelTier}
        router = _make_router(tmp_path, cost_profiles=custom_profiles)
        assert router._profiles is custom_profiles

    def test_decisions_path_stored(self, tmp_path):
        path = tmp_path / "custom" / "decisions.jsonl"
        router = CostRouter(decisions_path=path)
        assert router._decisions_path == path

    def test_log_is_bounded_deque(self, tmp_path):
        router = _make_router(tmp_path)
        assert router._log.maxlen == _MAX_LOG_ENTRIES

    def test_tier_counts_cover_all_tiers(self, tmp_path):
        router = _make_router(tmp_path)
        for tier in ModelTier:
            assert tier in router._tier_counts

    def test_load_called_at_init_missing_file(self, tmp_path):
        # When the file does not exist, _load_persisted_decisions must not crash
        path = tmp_path / "nonexistent" / "decisions.jsonl"
        router = CostRouter(decisions_path=path)
        assert router._total_estimated_cost == 0.0

    def test_load_called_at_init_with_existing_file(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        _write_jsonl(path, [
            {"selected_tier": "economy", "cost_estimate": 0.001},
            {"selected_tier": "premium", "cost_estimate": 0.05},
        ])
        router = CostRouter(decisions_path=path)
        assert router._tier_counts[ModelTier.economy] == 1
        assert router._tier_counts[ModelTier.premium] == 1
        assert abs(router._total_estimated_cost - 0.051) < 1e-10


# ===========================================================================
# TestResolveTier
# ===========================================================================


class TestResolveTier:
    """CostRouter.resolve_tier — tier selection logic."""

    def test_returns_model_tier(self, tmp_path):
        router = _make_router(tmp_path)
        result = router.resolve_tier(WorkCellLane.docs, RiskTier.low)
        assert isinstance(result, ModelTier)

    def test_docs_low_returns_economy(self, tmp_path):
        # Policy: min=economy, max=economy, quality_target=0.40
        # economy quality_score=0.55 >= 0.40 → economy
        router = _make_router(tmp_path)
        result = router.resolve_tier(WorkCellLane.docs, RiskTier.low)
        assert result == ModelTier.economy

    def test_docs_medium_economy_qualifies(self, tmp_path):
        # Policy: min=economy, max=standard, quality_target=0.50
        # economy quality_score=0.55 >= 0.50 → economy (cheapest that qualifies)
        router = _make_router(tmp_path)
        result = router.resolve_tier(WorkCellLane.docs, RiskTier.medium)
        assert result == ModelTier.economy

    def test_docs_high_returns_standard(self, tmp_path):
        # Policy: min=standard, max=standard, quality_target=0.70
        # standard quality_score=0.80 >= 0.70 → standard
        router = _make_router(tmp_path)
        result = router.resolve_tier(WorkCellLane.docs, RiskTier.high)
        assert result == ModelTier.standard

    def test_security_change_high_returns_premium(self, tmp_path):
        # Policy: min=premium, max=premium, quality_target=0.95
        # premium quality_score=1.00 >= 0.95 → premium
        router = _make_router(tmp_path)
        result = router.resolve_tier(WorkCellLane.security_change, RiskTier.high)
        assert result == ModelTier.premium

    def test_no_policy_falls_back_to_standard(self, tmp_path):
        # Empty policy dict → no policy found → fallback to standard
        router = CostRouter(policies={}, decisions_path=tmp_path / "d.jsonl")
        result = router.resolve_tier(WorkCellLane.docs, RiskTier.low)
        assert result == ModelTier.standard

    def test_quality_target_override_applied(self, tmp_path):
        # docs/medium: min=economy, max=standard, default quality_target=0.50
        # Caller overrides to 0.90 → economy (0.55) doesn't qualify,
        # standard (0.80) doesn't qualify → clamp to max_tier=standard
        router = _make_router(tmp_path)
        result = router.resolve_tier(
            WorkCellLane.docs, RiskTier.medium, quality_target=0.90
        )
        assert result == ModelTier.standard

    def test_quality_target_override_zero_selects_cheapest(self, tmp_path):
        # Override to 0.0 → any tier qualifies → cheapest within range = min_tier
        router = _make_router(tmp_path)
        result = router.resolve_tier(
            WorkCellLane.docs, RiskTier.medium, quality_target=0.0
        )
        assert result == ModelTier.economy

    def test_quality_target_clamped_above_one(self, tmp_path):
        # Override to 1.5 → clamped to 1.0 → only premium (1.00) qualifies
        # docs/critical: min=standard, max=premium
        router = _make_router(tmp_path)
        result = router.resolve_tier(
            WorkCellLane.docs, RiskTier.critical, quality_target=1.5
        )
        assert result == ModelTier.premium

    def test_quality_target_clamped_below_zero(self, tmp_path):
        # Override to -0.5 → clamped to 0.0 → cheapest qualifying tier
        router = _make_router(tmp_path)
        result = router.resolve_tier(
            WorkCellLane.docs, RiskTier.medium, quality_target=-0.5
        )
        # economy (0.55) >= 0.0 → economy
        assert result == ModelTier.economy

    def test_no_tier_meets_quality_target_returns_max(self, tmp_path):
        # Use a custom policy where quality_target=1.0 and max_tier=standard
        # standard.quality_score=0.80 < 1.0 → none qualifies → return max=standard
        custom_policy = {
            (WorkCellLane.docs, RiskTier.low): RoutingPolicy(
                lane=WorkCellLane.docs,
                risk_tier=RiskTier.low,
                min_model_tier=ModelTier.economy,
                max_model_tier=ModelTier.standard,
                quality_target=1.0,
            )
        }
        router = CostRouter(policies=custom_policy, decisions_path=tmp_path / "d.jsonl")
        result = router.resolve_tier(WorkCellLane.docs, RiskTier.low)
        assert result == ModelTier.standard

    def test_min_rank_skips_cheaper_tiers(self, tmp_path):
        # Policy with min=standard skips economy entirely
        custom_policy = {
            (WorkCellLane.docs, RiskTier.low): RoutingPolicy(
                lane=WorkCellLane.docs,
                risk_tier=RiskTier.low,
                min_model_tier=ModelTier.standard,
                max_model_tier=ModelTier.premium,
                quality_target=0.0,
            )
        }
        router = CostRouter(policies=custom_policy, decisions_path=tmp_path / "d.jsonl")
        result = router.resolve_tier(WorkCellLane.docs, RiskTier.low)
        # standard (rank 1) >= min_rank(1), quality_score=0.80 >= 0.0 → standard
        assert result == ModelTier.standard


# ===========================================================================
# TestEstimateCost
# ===========================================================================


class TestEstimateCost:
    """CostRouter.estimate_cost — pricing arithmetic."""

    def test_economy_cost_zero_tokens(self, tmp_path):
        router = _make_router(tmp_path)
        cost = router.estimate_cost(ModelTier.economy, 0, 0)
        assert cost == 0.0

    def test_economy_cost_exact(self, tmp_path):
        router = _make_router(tmp_path)
        # (1000 / 1000) * 0.00025 + (500 / 1000) * 0.00125
        expected = round(1.0 * 0.00025 + 0.5 * 0.00125, 8)
        cost = router.estimate_cost(ModelTier.economy, 1_000, 500)
        assert cost == expected

    def test_standard_cost_exact(self, tmp_path):
        router = _make_router(tmp_path)
        expected = round(2.0 * 0.003 + 1.0 * 0.015, 8)
        cost = router.estimate_cost(ModelTier.standard, 2_000, 1_000)
        assert cost == expected

    def test_premium_cost_exact(self, tmp_path):
        router = _make_router(tmp_path)
        expected = round(5.0 * 0.015 + 2.0 * 0.075, 8)
        cost = router.estimate_cost(ModelTier.premium, 5_000, 2_000)
        assert cost == expected

    def test_result_rounded_to_8_decimal_places(self, tmp_path):
        router = _make_router(tmp_path)
        cost = router.estimate_cost(ModelTier.economy, 1, 1)
        # Check it is exactly 8 decimal places
        parts = str(cost).split(".")
        if len(parts) == 2:
            assert len(parts[1]) <= 8

    def test_uses_custom_profile(self, tmp_path):
        custom_profile = CostProfile(
            model_tier=ModelTier.economy,
            cost_per_1k_input_tokens=1.0,
            cost_per_1k_output_tokens=2.0,
            quality_score=0.5,
            max_context_tokens=1000,
        )
        custom_profiles = {
            ModelTier.economy: custom_profile,
            ModelTier.standard: COST_PROFILES[ModelTier.standard],
            ModelTier.premium: COST_PROFILES[ModelTier.premium],
        }
        router = CostRouter(
            cost_profiles=custom_profiles, decisions_path=tmp_path / "d.jsonl"
        )
        # (1 / 1000) * 1.0 + (1 / 1000) * 2.0
        expected = round(0.001 + 0.002, 8)
        cost = router.estimate_cost(ModelTier.economy, 1, 1)
        assert cost == expected


# ===========================================================================
# TestMakeDecision
# ===========================================================================


class TestMakeDecision:
    """CostRouter.make_decision — end-to-end decision recording."""

    def test_returns_routing_decision(self, tmp_path):
        router = _make_router(tmp_path)
        decision = router.make_decision(
            task_id="t-001",
            lane=WorkCellLane.docs,
            risk_tier=RiskTier.low,
        )
        assert isinstance(decision, RoutingDecision)

    def test_task_id_preserved(self, tmp_path):
        router = _make_router(tmp_path)
        decision = router.make_decision("my-task", WorkCellLane.docs, RiskTier.low)
        assert decision.task_id == "my-task"

    def test_selected_tier_matches_resolve_tier(self, tmp_path):
        router = _make_router(tmp_path)
        expected_tier = router.resolve_tier(WorkCellLane.docs, RiskTier.low)
        decision = router.make_decision("t", WorkCellLane.docs, RiskTier.low)
        assert decision.selected_tier == expected_tier

    def test_uses_provided_token_counts(self, tmp_path):
        router = _make_router(tmp_path)
        decision = router.make_decision(
            "t-002",
            WorkCellLane.docs,
            RiskTier.low,
            estimated_tokens=(2_000, 1_000),
        )
        expected_cost = router.estimate_cost(ModelTier.economy, 2_000, 1_000)
        assert decision.cost_estimate == expected_cost

    def test_uses_default_token_counts_when_none(self, tmp_path):
        router = _make_router(tmp_path)
        decision = router.make_decision("t-003", WorkCellLane.docs, RiskTier.low)
        expected_cost = router.estimate_cost(
            ModelTier.economy, _DEFAULT_INPUT_TOKENS, _DEFAULT_OUTPUT_TOKENS
        )
        assert decision.cost_estimate == expected_cost

    def test_reasoning_contains_policy_match(self, tmp_path):
        router = _make_router(tmp_path)
        decision = router.make_decision("t-004", WorkCellLane.docs, RiskTier.low)
        assert any("Policy matched" in r for r in decision.reasoning)

    def test_reasoning_contains_selected_tier(self, tmp_path):
        router = _make_router(tmp_path)
        decision = router.make_decision("t-005", WorkCellLane.docs, RiskTier.low)
        assert any("Selected tier" in r for r in decision.reasoning)

    def test_reasoning_contains_default_token_note(self, tmp_path):
        router = _make_router(tmp_path)
        decision = router.make_decision("t-006", WorkCellLane.docs, RiskTier.low)
        assert any("Token counts not supplied" in r for r in decision.reasoning)

    def test_reasoning_no_default_token_note_when_tokens_provided(self, tmp_path):
        router = _make_router(tmp_path)
        decision = router.make_decision(
            "t-007", WorkCellLane.docs, RiskTier.low, estimated_tokens=(100, 50)
        )
        assert not any("Token counts not supplied" in r for r in decision.reasoning)

    def test_fallback_policy_ref_is_standard(self, tmp_path):
        router = CostRouter(policies={}, decisions_path=tmp_path / "d.jsonl")
        decision = router.make_decision("t-008", WorkCellLane.docs, RiskTier.low)
        assert decision.routing_policy == "fallback/standard"

    def test_policy_ref_matches_lane_and_risk(self, tmp_path):
        router = _make_router(tmp_path)
        decision = router.make_decision("t-009", WorkCellLane.docs, RiskTier.low)
        assert decision.routing_policy == f"{WorkCellLane.docs.value}/{RiskTier.low.value}"

    def test_fallback_reasoning_mentions_standard_fallback(self, tmp_path):
        router = CostRouter(policies={}, decisions_path=tmp_path / "d.jsonl")
        decision = router.make_decision("t-010", WorkCellLane.docs, RiskTier.low)
        assert any("standard tier fallback" in r for r in decision.reasoning)

    def test_quality_estimate_from_profile(self, tmp_path):
        router = _make_router(tmp_path)
        decision = router.make_decision("t-011", WorkCellLane.docs, RiskTier.low)
        assert decision.quality_estimate == _ECONOMY_PROFILE.quality_score

    def test_decision_appended_to_log(self, tmp_path):
        router = _make_router(tmp_path)
        router.make_decision("t-012", WorkCellLane.docs, RiskTier.low)
        assert len(router._log) == 1

    def test_tier_count_incremented(self, tmp_path):
        router = _make_router(tmp_path)
        router.make_decision("t-013", WorkCellLane.docs, RiskTier.low)
        # docs/low → economy tier
        assert router._tier_counts[ModelTier.economy] == 1

    def test_total_cost_accumulated(self, tmp_path):
        router = _make_router(tmp_path)
        router.make_decision("t-014", WorkCellLane.docs, RiskTier.low)
        router.make_decision("t-015", WorkCellLane.docs, RiskTier.low)
        assert router._total_estimated_cost > 0.0
        # Two identical decisions → cost should be double
        single_cost = router.estimate_cost(
            ModelTier.economy, _DEFAULT_INPUT_TOKENS, _DEFAULT_OUTPUT_TOKENS
        )
        assert abs(router._total_estimated_cost - 2 * single_cost) < 1e-10

    def test_decided_at_is_set(self, tmp_path):
        router = _make_router(tmp_path)
        decision = router.make_decision("t-016", WorkCellLane.docs, RiskTier.low)
        assert decision.decided_at is not None


# ===========================================================================
# TestGetDecisionLog
# ===========================================================================


class TestGetDecisionLog:
    """CostRouter.get_decision_log — retrieval and ordering."""

    def test_empty_log_returns_empty_list(self, tmp_path):
        router = _make_router(tmp_path)
        assert router.get_decision_log() == []

    def test_single_decision_returned(self, tmp_path):
        router = _make_router(tmp_path)
        router.make_decision("t-001", WorkCellLane.docs, RiskTier.low)
        log = router.get_decision_log()
        assert len(log) == 1

    def test_most_recent_first(self, tmp_path):
        router = _make_router(tmp_path)
        router.make_decision("first", WorkCellLane.docs, RiskTier.low)
        router.make_decision("second", WorkCellLane.docs, RiskTier.low)
        router.make_decision("third", WorkCellLane.docs, RiskTier.low)
        log = router.get_decision_log()
        assert log[0].task_id == "third"
        assert log[1].task_id == "second"
        assert log[2].task_id == "first"

    def test_limit_respected(self, tmp_path):
        router = _make_router(tmp_path)
        for i in range(10):
            router.make_decision(f"t-{i}", WorkCellLane.docs, RiskTier.low)
        log = router.get_decision_log(limit=3)
        assert len(log) == 3

    def test_default_limit_is_50(self, tmp_path):
        router = _make_router(tmp_path)
        for i in range(60):
            router.make_decision(f"t-{i}", WorkCellLane.docs, RiskTier.low)
        log = router.get_decision_log()
        assert len(log) == 50

    def test_limit_larger_than_log(self, tmp_path):
        router = _make_router(tmp_path)
        router.make_decision("t-1", WorkCellLane.docs, RiskTier.low)
        log = router.get_decision_log(limit=100)
        assert len(log) == 1

    def test_returns_routing_decision_objects(self, tmp_path):
        router = _make_router(tmp_path)
        router.make_decision("t-001", WorkCellLane.docs, RiskTier.low)
        log = router.get_decision_log()
        assert all(isinstance(d, RoutingDecision) for d in log)


# ===========================================================================
# TestGetCostSummary
# ===========================================================================


class TestGetCostSummary:
    """CostRouter.get_cost_summary — aggregate statistics."""

    def test_zero_decisions(self, tmp_path):
        router = _make_router(tmp_path)
        summary = router.get_cost_summary()
        assert summary["total_decisions"] == 0
        assert summary["total_estimated_cost"] == 0.0
        assert summary["avg_cost_per_task"] == 0.0

    def test_by_tier_keys_are_tier_values(self, tmp_path):
        router = _make_router(tmp_path)
        summary = router.get_cost_summary()
        for tier in ModelTier:
            assert tier.value in summary["by_tier"]

    def test_total_decisions_count(self, tmp_path):
        router = _make_router(tmp_path)
        router.make_decision("t-1", WorkCellLane.docs, RiskTier.low)
        router.make_decision("t-2", WorkCellLane.docs, RiskTier.low)
        summary = router.get_cost_summary()
        assert summary["total_decisions"] == 2

    def test_by_tier_counts_correct(self, tmp_path):
        router = _make_router(tmp_path)
        router.make_decision("t-1", WorkCellLane.docs, RiskTier.low)  # economy
        router.make_decision("t-2", WorkCellLane.security_change, RiskTier.high)  # premium
        summary = router.get_cost_summary()
        assert summary["by_tier"]["economy"] == 1
        assert summary["by_tier"]["premium"] == 1

    def test_total_cost_accumulated(self, tmp_path):
        router = _make_router(tmp_path)
        router.make_decision("t-1", WorkCellLane.docs, RiskTier.low)
        router.make_decision("t-2", WorkCellLane.docs, RiskTier.low)
        summary = router.get_cost_summary()
        expected = router._total_estimated_cost
        assert abs(summary["total_estimated_cost"] - round(expected, 8)) < 1e-10

    def test_avg_cost_per_task(self, tmp_path):
        router = _make_router(tmp_path)
        router.make_decision("t-1", WorkCellLane.docs, RiskTier.low)
        router.make_decision("t-2", WorkCellLane.docs, RiskTier.low)
        summary = router.get_cost_summary()
        expected_avg = round(summary["total_estimated_cost"] / 2, 8)
        assert abs(summary["avg_cost_per_task"] - expected_avg) < 1e-10

    def test_total_estimated_cost_rounded_to_8(self, tmp_path):
        router = _make_router(tmp_path)
        router.make_decision("t-1", WorkCellLane.docs, RiskTier.low)
        summary = router.get_cost_summary()
        parts = str(summary["total_estimated_cost"]).split(".")
        if len(parts) == 2:
            assert len(parts[1]) <= 8


# ===========================================================================
# TestLoadPersistedDecisions
# ===========================================================================


class TestLoadPersistedDecisions:
    """CostRouter._load_persisted_decisions — startup persistence reload."""

    def test_missing_file_no_error(self, tmp_path):
        path = tmp_path / "nonexistent.jsonl"
        router = CostRouter(decisions_path=path)
        assert router._total_estimated_cost == 0.0

    def test_valid_records_loaded(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        _write_jsonl(path, [
            {"selected_tier": "economy", "cost_estimate": 0.001},
            {"selected_tier": "standard", "cost_estimate": 0.01},
        ])
        router = CostRouter(decisions_path=path)
        assert router._tier_counts[ModelTier.economy] == 1
        assert router._tier_counts[ModelTier.standard] == 1
        assert abs(router._total_estimated_cost - 0.011) < 1e-10

    def test_malformed_json_skipped(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            fh.write("NOT_JSON\n")
            fh.write(json.dumps({"selected_tier": "economy", "cost_estimate": 0.001}) + "\n")
        router = CostRouter(decisions_path=path)
        assert router._tier_counts[ModelTier.economy] == 1

    def test_empty_lines_skipped(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            fh.write("\n")
            fh.write("   \n")
            fh.write(json.dumps({"selected_tier": "premium", "cost_estimate": 0.1}) + "\n")
        router = CostRouter(decisions_path=path)
        assert router._tier_counts[ModelTier.premium] == 1

    def test_invalid_tier_value_skipped(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        _write_jsonl(path, [
            {"selected_tier": "nonexistent_tier", "cost_estimate": 0.1},
            {"selected_tier": "economy", "cost_estimate": 0.001},
        ])
        router = CostRouter(decisions_path=path)
        assert router._tier_counts[ModelTier.economy] == 1
        assert abs(router._total_estimated_cost - 0.001) < 1e-10

    def test_missing_cost_estimate_defaults_to_zero(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        _write_jsonl(path, [
            {"selected_tier": "economy"},
        ])
        router = CostRouter(decisions_path=path)
        assert router._tier_counts[ModelTier.economy] == 1
        assert router._total_estimated_cost == 0.0

    def test_records_not_restored_to_log(self, tmp_path):
        # Only counters are rebuilt, not in-memory log entries
        path = tmp_path / "decisions.jsonl"
        _write_jsonl(path, [
            {"selected_tier": "economy", "cost_estimate": 0.001},
        ])
        router = CostRouter(decisions_path=path)
        assert len(router._log) == 0

    def test_cost_estimate_nonnumeric_skipped(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        _write_jsonl(path, [
            {"selected_tier": "economy", "cost_estimate": "not_a_number"},
        ])
        router = CostRouter(decisions_path=path)
        # ValueError on float("not_a_number") → line skipped
        assert router._tier_counts[ModelTier.economy] == 0

    def test_multiple_records_same_tier_accumulated(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        _write_jsonl(path, [
            {"selected_tier": "premium", "cost_estimate": 0.05},
            {"selected_tier": "premium", "cost_estimate": 0.10},
            {"selected_tier": "premium", "cost_estimate": 0.20},
        ])
        router = CostRouter(decisions_path=path)
        assert router._tier_counts[ModelTier.premium] == 3
        assert abs(router._total_estimated_cost - 0.35) < 1e-10


# ===========================================================================
# TestPersistDecision
# ===========================================================================


class TestPersistDecision:
    """CostRouter._persist_decision — JSONL file append behavior."""

    def _make_decision(self) -> RoutingDecision:
        return RoutingDecision(
            task_id="t-persist",
            selected_tier=ModelTier.standard,
            routing_policy="docs/low",
            cost_estimate=0.005,
            quality_estimate=0.80,
            reasoning=["test reasoning"],
        )

    def test_file_created_with_record(self, tmp_path):
        path = tmp_path / "subdir" / "decisions.jsonl"
        router = CostRouter(decisions_path=path)
        decision = self._make_decision()
        router._persist_decision(decision)
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_record_contains_expected_keys(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        router = CostRouter(decisions_path=path)
        decision = self._make_decision()
        router._persist_decision(decision)
        record = json.loads(path.read_text(encoding="utf-8").strip())
        for key in ("task_id", "selected_tier", "routing_policy",
                    "cost_estimate", "quality_estimate", "reasoning", "decided_at"):
            assert key in record

    def test_record_values_match_decision(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        router = CostRouter(decisions_path=path)
        decision = self._make_decision()
        router._persist_decision(decision)
        record = json.loads(path.read_text(encoding="utf-8").strip())
        assert record["task_id"] == decision.task_id
        assert record["selected_tier"] == decision.selected_tier.value
        assert record["cost_estimate"] == decision.cost_estimate

    def test_appends_multiple_records(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        router = CostRouter(decisions_path=path)
        d1 = self._make_decision()
        d2 = RoutingDecision(
            task_id="t-second",
            selected_tier=ModelTier.premium,
            routing_policy="security_change/high",
            cost_estimate=0.10,
            quality_estimate=1.0,
        )
        router._persist_decision(d1)
        router._persist_decision(d2)
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_parent_dirs_created(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "dir" / "decisions.jsonl"
        router = CostRouter(decisions_path=path)
        decision = self._make_decision()
        router._persist_decision(decision)
        assert path.exists()

    def test_oserror_logged_not_raised(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        router = CostRouter(decisions_path=path)
        decision = self._make_decision()
        with patch.object(Path, "open", side_effect=OSError("disk full")):
            # Should not raise
            router._persist_decision(decision)


# ===========================================================================
# TestAppendDecision
# ===========================================================================


class TestAppendDecision:
    """CostRouter._append_decision — in-memory state and persistence delegation."""

    def _make_decision(self, task_id="t-app", tier=ModelTier.economy) -> RoutingDecision:
        return RoutingDecision(
            task_id=task_id,
            selected_tier=tier,
            routing_policy="docs/low",
            cost_estimate=0.001,
            quality_estimate=0.55,
        )

    def test_decision_appended_to_log(self, tmp_path):
        router = _make_router(tmp_path)
        decision = self._make_decision()
        router._append_decision(decision)
        assert list(router._log)[-1] is decision

    def test_tier_count_incremented(self, tmp_path):
        router = _make_router(tmp_path)
        decision = self._make_decision(tier=ModelTier.premium)
        router._append_decision(decision)
        assert router._tier_counts[ModelTier.premium] == 1

    def test_total_cost_incremented(self, tmp_path):
        router = _make_router(tmp_path)
        decision = self._make_decision()
        router._append_decision(decision)
        assert abs(router._total_estimated_cost - decision.cost_estimate) < 1e-10

    def test_persist_called(self, tmp_path):
        router = _make_router(tmp_path)
        decision = self._make_decision()
        with patch.object(router, "_persist_decision") as mock_persist:
            router._append_decision(decision)
            mock_persist.assert_called_once_with(decision)

    def test_multiple_appends_accumulate_cost(self, tmp_path):
        router = _make_router(tmp_path)
        for i in range(5):
            router._append_decision(self._make_decision(task_id=f"t-{i}"))
        assert abs(router._total_estimated_cost - 5 * 0.001) < 1e-10


# ===========================================================================
# TestSingletonFunctions
# ===========================================================================


class TestSingletonFunctions:
    """get_cost_router and reset_cost_router — singleton lifecycle."""

    def setup_method(self):
        """Ensure a clean singleton state before each test."""
        reset_cost_router()

    def teardown_method(self):
        """Leave singleton clean after each test."""
        reset_cost_router()

    def test_get_cost_router_returns_cost_router(self, tmp_path):
        with patch.object(
            cost_router_module,
            "CostRouter",
            return_value=MagicMock(spec=CostRouter),
        ):
            reset_cost_router()
            router = get_cost_router()
        assert router is not None

    def test_get_cost_router_same_instance_twice(self):
        r1 = get_cost_router()
        r2 = get_cost_router()
        assert r1 is r2

    def test_reset_cost_router_clears_singleton(self):
        r1 = get_cost_router()
        reset_cost_router()
        r2 = get_cost_router()
        assert r1 is not r2

    def test_reset_then_get_creates_new_instance(self):
        get_cost_router()
        reset_cost_router()
        assert cost_router_module._router is None
        get_cost_router()
        assert cost_router_module._router is not None

    def test_get_cost_router_is_thread_safe(self):
        """Multiple threads calling get_cost_router must all get the same instance."""
        results = []

        def _get():
            results.append(get_cost_router())

        threads = [threading.Thread(target=_get) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(id(r) for r in results)) == 1, "All threads should get the same singleton"

    def test_reset_cost_router_is_idempotent(self):
        """Calling reset twice must not raise."""
        reset_cost_router()
        reset_cost_router()
        assert cost_router_module._router is None

    def test_get_returns_cost_router_type(self):
        router = get_cost_router()
        assert isinstance(router, CostRouter)


# ===========================================================================
# TestIntegration
# ===========================================================================


class TestIntegration:
    """End-to-end scenarios combining multiple methods."""

    def test_full_decision_cycle_persisted_and_reloaded(self, tmp_path):
        """Make decisions, then reload from file and verify counters."""
        path = tmp_path / "decisions.jsonl"
        router = CostRouter(decisions_path=path)
        router.make_decision("t-1", WorkCellLane.docs, RiskTier.low)
        router.make_decision("t-2", WorkCellLane.security_change, RiskTier.high)

        # Reload counters from the JSONL file
        router2 = CostRouter(decisions_path=path)
        summary = router2.get_cost_summary()
        assert summary["total_decisions"] == 2

    def test_deque_bounded_at_max_log_entries(self, tmp_path):
        """Log deque must not exceed _MAX_LOG_ENTRIES."""
        router = _make_router(tmp_path)
        for i in range(_MAX_LOG_ENTRIES + 10):
            router.make_decision(f"t-{i}", WorkCellLane.docs, RiskTier.low)
        assert len(router._log) == _MAX_LOG_ENTRIES

    def test_multiple_tiers_tracked_independently(self, tmp_path):
        router = _make_router(tmp_path)
        # docs/low → economy
        router.make_decision("eco", WorkCellLane.docs, RiskTier.low)
        # security_change/high → premium
        router.make_decision("prem", WorkCellLane.security_change, RiskTier.high)
        summary = router.get_cost_summary()
        assert summary["by_tier"]["economy"] == 1
        assert summary["by_tier"]["premium"] == 1
        assert summary["by_tier"]["standard"] == 0

    def test_get_decision_log_after_many_decisions(self, tmp_path):
        router = _make_router(tmp_path)
        for i in range(5):
            router.make_decision(f"t-{i}", WorkCellLane.docs, RiskTier.low)
        log = router.get_decision_log(limit=5)
        task_ids = [d.task_id for d in log]
        # Most recent first → t-4, t-3, t-2, t-1, t-0
        assert task_ids[0] == "t-4"
        assert task_ids[-1] == "t-0"

    def test_cost_summary_avg_zero_when_no_decisions(self, tmp_path):
        router = _make_router(tmp_path)
        summary = router.get_cost_summary()
        assert summary["avg_cost_per_task"] == 0.0

    def test_persist_then_load_recovers_costs(self, tmp_path):
        path = tmp_path / "decisions.jsonl"
        router = CostRouter(decisions_path=path)
        router.make_decision("t-1", WorkCellLane.docs, RiskTier.low)
        original_cost = router._total_estimated_cost

        router2 = CostRouter(decisions_path=path)
        assert abs(router2._total_estimated_cost - original_cost) < 1e-10
