"""Tests for graduation_policy.py — Lane Graduation Policy Service.

Tests cover:
- LaneMetrics dataclass construction
- GraduationDecision dataclass construction and defaults
- LaneGraduationPolicy.check_graduation_eligibility — all happy and sad paths
- Hard-blocked lane short-circuit for each blocked lane
- Every individual threshold blocker
- Multiple simultaneous blockers
- Boundary values (exact threshold pass/fail)
- get_graduation_policy singleton creation and thread-safety
"""

from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError

import pytest

from forge_harness.webhook_server.services.graduation_policy import (
    _MAX_ESCAPED_DEFECTS,
    _MAX_HUMAN_TOUCH_RATIO,
    _MAX_REQUEUE_RATE,
    _MIN_EVALUATOR_PASS_RATE,
    _MIN_SAMPLE_SIZE,
    HARD_BLOCKED_LANES,
    GraduationDecision,
    LaneGraduationPolicy,
    LaneMetrics,
    get_graduation_policy,
)

# =============================================================================
# Constants/module-level attribute tests
# =============================================================================


class TestModuleConstants:
    """Verify public and private module constants are sensible."""

    def test_hard_blocked_lanes_is_frozenset(self) -> None:
        assert isinstance(HARD_BLOCKED_LANES, frozenset)

    def test_hard_blocked_lanes_contains_expected_entries(self) -> None:
        assert HARD_BLOCKED_LANES == frozenset({"auth", "security", "deploy", "billing"})

    def test_threshold_values(self) -> None:
        assert _MIN_EVALUATOR_PASS_RATE == 0.95
        assert _MAX_REQUEUE_RATE == 0.05
        assert _MAX_ESCAPED_DEFECTS == 0
        assert _MAX_HUMAN_TOUCH_RATIO == 0.10
        assert _MIN_SAMPLE_SIZE == 20


# =============================================================================
# LaneMetrics tests
# =============================================================================


class TestLaneMetrics:
    """Tests for LaneMetrics dataclass."""

    def test_create_lane_metrics(self) -> None:
        m = LaneMetrics(
            evaluator_pass_rate=0.97,
            requeue_rate=0.02,
            escaped_defects=0,
            human_touch_ratio=0.05,
            sample_size=50,
        )
        assert m.evaluator_pass_rate == 0.97
        assert m.requeue_rate == 0.02
        assert m.escaped_defects == 0
        assert m.human_touch_ratio == 0.05
        assert m.sample_size == 50

    def test_lane_metrics_is_frozen(self) -> None:
        m = LaneMetrics(
            evaluator_pass_rate=0.97,
            requeue_rate=0.02,
            escaped_defects=0,
            human_touch_ratio=0.05,
            sample_size=50,
        )
        with pytest.raises((FrozenInstanceError, AttributeError)):
            m.evaluator_pass_rate = 0.5  # type: ignore[misc]

    def test_lane_metrics_equality(self) -> None:
        m1 = LaneMetrics(0.97, 0.02, 0, 0.05, 50)
        m2 = LaneMetrics(0.97, 0.02, 0, 0.05, 50)
        assert m1 == m2

    def test_lane_metrics_inequality(self) -> None:
        m1 = LaneMetrics(0.97, 0.02, 0, 0.05, 50)
        m2 = LaneMetrics(0.80, 0.02, 0, 0.05, 50)
        assert m1 != m2


# =============================================================================
# GraduationDecision tests
# =============================================================================


class TestGraduationDecision:
    """Tests for GraduationDecision dataclass."""

    def test_default_values(self) -> None:
        d = GraduationDecision(eligible=True)
        assert d.eligible is True
        assert d.blockers == []
        assert d.recommended_action == "monitor"
        assert d.lane == ""

    def test_create_with_all_fields(self) -> None:
        d = GraduationDecision(
            eligible=False,
            blockers=["reason 1", "reason 2"],
            recommended_action="blocked",
            lane="auth",
        )
        assert d.eligible is False
        assert len(d.blockers) == 2
        assert d.recommended_action == "blocked"
        assert d.lane == "auth"

    def test_graduation_decision_is_frozen(self) -> None:
        d = GraduationDecision(eligible=True)
        with pytest.raises((FrozenInstanceError, AttributeError)):
            d.eligible = False  # type: ignore[misc]


# =============================================================================
# LaneGraduationPolicy — happy path
# =============================================================================


@pytest.fixture
def policy() -> LaneGraduationPolicy:
    """Return a fresh (non-singleton) policy instance."""
    return LaneGraduationPolicy()


def _passing_metrics(**overrides: float | int) -> LaneMetrics:
    """Return metrics that pass all criteria, optionally overriding fields."""
    defaults: dict[str, float | int] = {
        "evaluator_pass_rate": 0.97,
        "requeue_rate": 0.03,
        "escaped_defects": 0,
        "human_touch_ratio": 0.05,
        "sample_size": 50,
    }
    defaults.update(overrides)
    return LaneMetrics(**defaults)  # type: ignore[arg-type]


class TestCheckGraduationEligibilityHappyPath:
    """Tests for lanes that should graduate."""

    def test_eligible_lane(self, policy: LaneGraduationPolicy) -> None:
        decision = policy.check_graduation_eligibility("api_simple", _passing_metrics())
        assert decision.eligible is True
        assert decision.blockers == []
        assert decision.recommended_action == "graduate"
        assert decision.lane == "api_simple"

    def test_eligible_with_exact_minimum_sample_size(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(sample_size=_MIN_SAMPLE_SIZE)
        decision = policy.check_graduation_eligibility("worker", metrics)
        assert decision.eligible is True

    def test_eligible_with_exact_minimum_pass_rate(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(evaluator_pass_rate=_MIN_EVALUATOR_PASS_RATE)
        decision = policy.check_graduation_eligibility("worker", metrics)
        assert decision.eligible is True

    def test_eligible_with_exact_maximum_requeue_rate(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(requeue_rate=_MAX_REQUEUE_RATE)
        decision = policy.check_graduation_eligibility("worker", metrics)
        assert decision.eligible is True

    def test_eligible_with_zero_escaped_defects(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(escaped_defects=0)
        decision = policy.check_graduation_eligibility("worker", metrics)
        assert decision.eligible is True

    def test_eligible_with_exact_maximum_human_touch_ratio(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(human_touch_ratio=_MAX_HUMAN_TOUCH_RATIO)
        decision = policy.check_graduation_eligibility("worker", metrics)
        assert decision.eligible is True

    def test_perfect_metrics(self, policy: LaneGraduationPolicy) -> None:
        metrics = LaneMetrics(
            evaluator_pass_rate=1.0,
            requeue_rate=0.0,
            escaped_defects=0,
            human_touch_ratio=0.0,
            sample_size=100,
        )
        decision = policy.check_graduation_eligibility("perfect_lane", metrics)
        assert decision.eligible is True
        assert decision.recommended_action == "graduate"


# =============================================================================
# LaneGraduationPolicy — hard-blocked lanes
# =============================================================================


class TestHardBlockedLanes:
    """Ensure hard-blocked lanes are rejected regardless of metrics."""

    @pytest.mark.parametrize("lane", sorted(HARD_BLOCKED_LANES))
    def test_hard_blocked_lane_is_ineligible(
        self, policy: LaneGraduationPolicy, lane: str
    ) -> None:
        decision = policy.check_graduation_eligibility(lane, _passing_metrics())
        assert decision.eligible is False
        assert decision.recommended_action == "blocked"
        assert decision.lane == lane
        assert len(decision.blockers) == 1
        assert lane in decision.blockers[0]

    @pytest.mark.parametrize("lane", sorted(HARD_BLOCKED_LANES))
    def test_hard_blocked_lane_ignores_perfect_metrics(
        self, policy: LaneGraduationPolicy, lane: str
    ) -> None:
        """Blocked lane must not graduate even with a perfect metrics set."""
        metrics = LaneMetrics(1.0, 0.0, 0, 0.0, 1000)
        decision = policy.check_graduation_eligibility(lane, metrics)
        assert decision.eligible is False
        assert decision.recommended_action == "blocked"

    def test_non_blocked_lane_not_affected(self, policy: LaneGraduationPolicy) -> None:
        decision = policy.check_graduation_eligibility("docs_gen", _passing_metrics())
        assert decision.recommended_action != "blocked"


# =============================================================================
# LaneGraduationPolicy — individual blockers
# =============================================================================


class TestIndividualBlockers:
    """Verify each threshold generates exactly the correct blocker message."""

    def test_low_evaluator_pass_rate_generates_blocker(
        self, policy: LaneGraduationPolicy
    ) -> None:
        metrics = _passing_metrics(evaluator_pass_rate=0.94)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is False
        assert any("Evaluator pass rate too low" in b for b in decision.blockers)
        assert decision.recommended_action == "monitor"

    def test_high_requeue_rate_generates_blocker(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(requeue_rate=0.06)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is False
        assert any("Requeue rate too high" in b for b in decision.blockers)

    def test_escaped_defects_generates_blocker(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(escaped_defects=1)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is False
        assert any("Escaped defects present" in b for b in decision.blockers)

    def test_high_human_touch_ratio_generates_blocker(
        self, policy: LaneGraduationPolicy
    ) -> None:
        metrics = _passing_metrics(human_touch_ratio=0.11)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is False
        assert any("Human touch ratio too high" in b for b in decision.blockers)

    def test_insufficient_sample_size_generates_blocker(
        self, policy: LaneGraduationPolicy
    ) -> None:
        metrics = _passing_metrics(sample_size=_MIN_SAMPLE_SIZE - 1)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is False
        assert any("Insufficient sample size" in b for b in decision.blockers)

    def test_zero_sample_size_generates_blocker(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(sample_size=0)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is False
        assert any("Insufficient sample size" in b for b in decision.blockers)


# =============================================================================
# LaneGraduationPolicy — boundary conditions
# =============================================================================


class TestBoundaryValues:
    """Boundary / off-by-one tests for every threshold."""

    def test_evaluator_pass_rate_just_below_minimum(
        self, policy: LaneGraduationPolicy
    ) -> None:
        # 0.9499... < 0.95 → should block
        metrics = _passing_metrics(evaluator_pass_rate=0.9499)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is False

    def test_evaluator_pass_rate_at_minimum(self, policy: LaneGraduationPolicy) -> None:
        # exactly 0.95 → should pass
        metrics = _passing_metrics(evaluator_pass_rate=0.95)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is True

    def test_requeue_rate_just_above_maximum(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(requeue_rate=0.0501)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is False

    def test_requeue_rate_at_maximum(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(requeue_rate=0.05)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is True

    def test_human_touch_ratio_just_above_maximum(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(human_touch_ratio=0.1001)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is False

    def test_human_touch_ratio_at_maximum(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(human_touch_ratio=0.10)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is True

    def test_sample_size_one_below_minimum(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(sample_size=_MIN_SAMPLE_SIZE - 1)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is False

    def test_sample_size_at_minimum(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(sample_size=_MIN_SAMPLE_SIZE)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is True

    def test_escaped_defects_one(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(escaped_defects=1)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is False

    def test_escaped_defects_large(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(escaped_defects=99)
        decision = policy.check_graduation_eligibility("lane", metrics)
        assert decision.eligible is False


# =============================================================================
# LaneGraduationPolicy — multiple simultaneous blockers
# =============================================================================


class TestMultipleBlockers:
    """Verify that all failing criteria accumulate in a single decision."""

    def test_all_criteria_fail(self, policy: LaneGraduationPolicy) -> None:
        metrics = LaneMetrics(
            evaluator_pass_rate=0.50,
            requeue_rate=0.30,
            escaped_defects=5,
            human_touch_ratio=0.50,
            sample_size=5,
        )
        decision = policy.check_graduation_eligibility("chaos_lane", metrics)
        assert decision.eligible is False
        # Expect blockers for: sample_size, pass_rate, requeue_rate, escaped_defects, human_touch
        assert len(decision.blockers) == 5
        assert decision.recommended_action == "monitor"

    def test_two_criteria_fail(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(evaluator_pass_rate=0.80, requeue_rate=0.20)
        decision = policy.check_graduation_eligibility("partial_fail", metrics)
        assert decision.eligible is False
        assert len(decision.blockers) == 2

    def test_sample_size_and_escaped_defects_fail(self, policy: LaneGraduationPolicy) -> None:
        metrics = _passing_metrics(sample_size=5, escaped_defects=2)
        decision = policy.check_graduation_eligibility("partial_fail", metrics)
        assert decision.eligible is False
        assert len(decision.blockers) == 2


# =============================================================================
# LaneGraduationPolicy — decision structure details
# =============================================================================


class TestDecisionStructureDetails:
    """Check the returned GraduationDecision is well-formed in all paths."""

    def test_eligible_decision_has_no_blockers(self, policy: LaneGraduationPolicy) -> None:
        decision = policy.check_graduation_eligibility("ok_lane", _passing_metrics())
        assert decision.blockers == []

    def test_ineligible_decision_blockers_are_strings(
        self, policy: LaneGraduationPolicy
    ) -> None:
        metrics = _passing_metrics(evaluator_pass_rate=0.50)
        decision = policy.check_graduation_eligibility("lane", metrics)
        for b in decision.blockers:
            assert isinstance(b, str)

    def test_blocked_lane_recommended_action_is_blocked(
        self, policy: LaneGraduationPolicy
    ) -> None:
        decision = policy.check_graduation_eligibility("auth", _passing_metrics())
        assert decision.recommended_action == "blocked"

    def test_eligible_lane_recommended_action_is_graduate(
        self, policy: LaneGraduationPolicy
    ) -> None:
        decision = policy.check_graduation_eligibility("pipeline", _passing_metrics())
        assert decision.recommended_action == "graduate"

    def test_ineligible_non_blocked_lane_recommended_action_is_monitor(
        self, policy: LaneGraduationPolicy
    ) -> None:
        metrics = _passing_metrics(evaluator_pass_rate=0.50)
        decision = policy.check_graduation_eligibility("pipeline", metrics)
        assert decision.recommended_action == "monitor"

    def test_lane_field_is_preserved(self, policy: LaneGraduationPolicy) -> None:
        for lane in ("foo", "bar_baz", "123-lane"):
            decision = policy.check_graduation_eligibility(lane, _passing_metrics())
            assert decision.lane == lane


# =============================================================================
# get_graduation_policy — singleton
# =============================================================================


class TestGetGraduationPolicySingleton:
    """Tests for the get_graduation_policy() factory function."""

    def test_returns_lane_graduation_policy_instance(self) -> None:
        policy = get_graduation_policy()
        assert isinstance(policy, LaneGraduationPolicy)

    def test_returns_same_instance_on_multiple_calls(self) -> None:
        p1 = get_graduation_policy()
        p2 = get_graduation_policy()
        assert p1 is p2

    def test_singleton_is_functional(self) -> None:
        policy = get_graduation_policy()
        decision = policy.check_graduation_eligibility("test_lane", _passing_metrics())
        assert isinstance(decision, GraduationDecision)

    def test_singleton_thread_safety(self) -> None:
        """Multiple threads should all get the same singleton instance."""
        results: list[LaneGraduationPolicy] = []
        lock = threading.Lock()

        def capture() -> None:
            p = get_graduation_policy()
            with lock:
                results.append(p)

        threads = [threading.Thread(target=capture) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        first = results[0]
        assert all(p is first for p in results)


# =============================================================================
# Statelessness — fresh policy per call
# =============================================================================


class TestStatelessness:
    """The policy is stateless: each call is independent."""

    def test_repeated_calls_return_independent_decisions(
        self, policy: LaneGraduationPolicy
    ) -> None:
        decision1 = policy.check_graduation_eligibility("lane", _passing_metrics())
        decision2 = policy.check_graduation_eligibility(
            "lane", _passing_metrics(evaluator_pass_rate=0.50)
        )
        assert decision1.eligible is True
        assert decision2.eligible is False

    def test_different_lanes_independent(self, policy: LaneGraduationPolicy) -> None:
        d_auth = policy.check_graduation_eligibility("auth", _passing_metrics())
        d_other = policy.check_graduation_eligibility("api", _passing_metrics())
        assert d_auth.eligible is False
        assert d_other.eligible is True
