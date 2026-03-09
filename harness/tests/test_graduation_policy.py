"""Tests for LaneGraduationPolicy — DF-5005.

Coverage matrix:
- All five eligibility criteria individually (pass and fail)
- All four hard-blocked lanes are permanently ineligible
- Boundary: evaluator_pass_rate exactly at threshold (0.95 pass, 0.9499 fail)
- Boundary: requeue_rate exactly at threshold (0.05 pass, 0.0501 fail)
- Boundary: human_touch_ratio exactly at threshold (0.10 pass, 0.1001 fail)
- Boundary: sample_size exactly at minimum (20 pass, 19 fail)
- escaped_defects == 0 passes; escaped_defects >= 1 fails
- Multiple simultaneous blockers are all reported
- Eligible lane returns recommended_action == "graduate"
- Ineligible lane returns recommended_action == "monitor"
- Hard-blocked lane returns recommended_action == "blocked"
- GraduationDecision.eligible is False for hard-blocked lanes regardless of metrics
- Singleton factory get_graduation_policy() returns the same instance
- LaneMetrics dataclass is frozen (immutable)
- GraduationDecision dataclass is frozen (immutable)
- GraduationDecision.blockers is empty when eligible
- GraduationDecision.lane field matches the requested lane
- HARD_BLOCKED_LANES constant contains exactly the four expected lane names
- Non-blocked lanes with perfect metrics are eligible
- API endpoint POST /api/lanes/{lane}/check-graduation returns 200
- API endpoint returns correct JSON structure
- API endpoint respects hard-blocked lanes
- API endpoint validates request body (missing fields → 422)
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge_harness.webhook_server.services.graduation_policy import (
    HARD_BLOCKED_LANES,
    GraduationDecision,
    LaneGraduationPolicy,
    LaneMetrics,
    get_graduation_policy,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def policy() -> LaneGraduationPolicy:
    """A fresh LaneGraduationPolicy instance for each test."""
    return LaneGraduationPolicy()


@pytest.fixture()
def passing_metrics() -> LaneMetrics:
    """LaneMetrics that satisfies all eligibility criteria."""
    return LaneMetrics(
        evaluator_pass_rate=0.97,
        requeue_rate=0.03,
        escaped_defects=0,
        human_touch_ratio=0.05,
        sample_size=50,
    )


@pytest.fixture()
def client() -> TestClient:
    """FastAPI TestClient wired to the graduation router only."""
    from forge_harness.webhook_server.api.graduation import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _replace(metrics: LaneMetrics, **kwargs) -> LaneMetrics:
    """Return a new LaneMetrics with the given fields overridden."""
    return LaneMetrics(
        evaluator_pass_rate=kwargs.get("evaluator_pass_rate", metrics.evaluator_pass_rate),
        requeue_rate=kwargs.get("requeue_rate", metrics.requeue_rate),
        escaped_defects=kwargs.get("escaped_defects", metrics.escaped_defects),
        human_touch_ratio=kwargs.get("human_touch_ratio", metrics.human_touch_ratio),
        sample_size=kwargs.get("sample_size", metrics.sample_size),
    )


# ===========================================================================
# 1. Eligibility — happy path
# ===========================================================================


class TestEligibilityHappyPath:
    """Tests where all criteria are satisfied and the lane is not hard-blocked."""

    def test_perfect_metrics_eligible(self, policy, passing_metrics):
        """Perfect metrics on a regular lane must yield eligible=True."""
        decision = policy.check_graduation_eligibility("api_simple", passing_metrics)
        assert decision.eligible is True

    def test_eligible_decision_has_empty_blockers(self, policy, passing_metrics):
        """An eligible decision must have an empty blockers list."""
        decision = policy.check_graduation_eligibility("api_simple", passing_metrics)
        assert decision.blockers == []

    def test_eligible_recommended_action_is_graduate(self, policy, passing_metrics):
        """Eligible lanes must have recommended_action == 'graduate'."""
        decision = policy.check_graduation_eligibility("api_simple", passing_metrics)
        assert decision.recommended_action == "graduate"

    def test_eligible_decision_lane_matches_input(self, policy, passing_metrics):
        """GraduationDecision.lane must match the lane passed to check."""
        decision = policy.check_graduation_eligibility("docs", passing_metrics)
        assert decision.lane == "docs"

    def test_exact_threshold_pass_rate_is_eligible(self, policy, passing_metrics):
        """evaluator_pass_rate == 0.95 (the exact threshold) must pass."""
        metrics = _replace(passing_metrics, evaluator_pass_rate=0.95)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is True

    def test_exact_threshold_requeue_rate_is_eligible(self, policy, passing_metrics):
        """requeue_rate == 0.05 (the exact threshold) must pass."""
        metrics = _replace(passing_metrics, requeue_rate=0.05)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is True

    def test_exact_threshold_human_touch_ratio_is_eligible(self, policy, passing_metrics):
        """human_touch_ratio == 0.10 (the exact threshold) must pass."""
        metrics = _replace(passing_metrics, human_touch_ratio=0.10)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is True

    def test_exact_min_sample_size_is_eligible(self, policy, passing_metrics):
        """sample_size == 20 (the exact minimum) must pass."""
        metrics = _replace(passing_metrics, sample_size=20)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is True

    def test_zero_escaped_defects_is_eligible(self, policy, passing_metrics):
        """escaped_defects == 0 satisfies the criterion."""
        metrics = _replace(passing_metrics, escaped_defects=0)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is True


# ===========================================================================
# 2. Individual criterion failures
# ===========================================================================


class TestIndividualCriterionFailures:
    """Each criterion fails independently while others remain passing."""

    def test_low_evaluator_pass_rate_is_blocked(self, policy, passing_metrics):
        """evaluator_pass_rate below 0.95 makes the lane ineligible."""
        metrics = _replace(passing_metrics, evaluator_pass_rate=0.94)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is False

    def test_low_evaluator_pass_rate_has_blocker_message(self, policy, passing_metrics):
        """A failing pass rate must produce a descriptive blocker."""
        metrics = _replace(passing_metrics, evaluator_pass_rate=0.94)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert any("pass rate" in b.lower() for b in decision.blockers)

    def test_high_requeue_rate_is_blocked(self, policy, passing_metrics):
        """requeue_rate above 0.05 makes the lane ineligible."""
        metrics = _replace(passing_metrics, requeue_rate=0.06)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is False

    def test_high_requeue_rate_has_blocker_message(self, policy, passing_metrics):
        """A failing requeue rate must produce a descriptive blocker."""
        metrics = _replace(passing_metrics, requeue_rate=0.06)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert any("requeue" in b.lower() for b in decision.blockers)

    def test_nonzero_escaped_defects_is_blocked(self, policy, passing_metrics):
        """Any escaped_defects > 0 makes the lane ineligible."""
        metrics = _replace(passing_metrics, escaped_defects=1)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is False

    def test_nonzero_escaped_defects_has_blocker_message(self, policy, passing_metrics):
        """A failing escaped_defects count must produce a descriptive blocker."""
        metrics = _replace(passing_metrics, escaped_defects=1)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert any("escaped" in b.lower() or "defect" in b.lower() for b in decision.blockers)

    def test_high_human_touch_ratio_is_blocked(self, policy, passing_metrics):
        """human_touch_ratio above 0.10 makes the lane ineligible."""
        metrics = _replace(passing_metrics, human_touch_ratio=0.11)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is False

    def test_high_human_touch_ratio_has_blocker_message(self, policy, passing_metrics):
        """A failing human touch ratio must produce a descriptive blocker."""
        metrics = _replace(passing_metrics, human_touch_ratio=0.11)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert any("human" in b.lower() or "touch" in b.lower() for b in decision.blockers)

    def test_small_sample_size_is_blocked(self, policy, passing_metrics):
        """sample_size below 20 makes the lane ineligible."""
        metrics = _replace(passing_metrics, sample_size=19)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is False

    def test_small_sample_size_has_blocker_message(self, policy, passing_metrics):
        """An insufficient sample size must produce a descriptive blocker."""
        metrics = _replace(passing_metrics, sample_size=19)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert any("sample" in b.lower() for b in decision.blockers)


# ===========================================================================
# 3. Boundary conditions
# ===========================================================================


class TestBoundaryConditions:
    """Exact boundary values — one side passes, one side fails."""

    def test_pass_rate_0_9499_fails(self, policy, passing_metrics):
        """evaluator_pass_rate=0.9499 is strictly below 0.95 — must fail."""
        metrics = _replace(passing_metrics, evaluator_pass_rate=0.9499)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is False

    def test_pass_rate_0_9500_passes(self, policy, passing_metrics):
        """evaluator_pass_rate=0.9500 meets the threshold — must pass."""
        metrics = _replace(passing_metrics, evaluator_pass_rate=0.9500)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is True

    def test_requeue_rate_0_0501_fails(self, policy, passing_metrics):
        """requeue_rate=0.0501 exceeds 0.05 — must fail."""
        metrics = _replace(passing_metrics, requeue_rate=0.0501)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is False

    def test_requeue_rate_0_0500_passes(self, policy, passing_metrics):
        """requeue_rate=0.0500 is at the threshold — must pass."""
        metrics = _replace(passing_metrics, requeue_rate=0.0500)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is True

    def test_human_touch_0_1001_fails(self, policy, passing_metrics):
        """human_touch_ratio=0.1001 exceeds 0.10 — must fail."""
        metrics = _replace(passing_metrics, human_touch_ratio=0.1001)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is False

    def test_human_touch_0_1000_passes(self, policy, passing_metrics):
        """human_touch_ratio=0.1000 is at the threshold — must pass."""
        metrics = _replace(passing_metrics, human_touch_ratio=0.1000)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is True

    def test_sample_size_19_fails(self, policy, passing_metrics):
        """sample_size=19 is one below the minimum — must fail."""
        metrics = _replace(passing_metrics, sample_size=19)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is False

    def test_sample_size_20_passes(self, policy, passing_metrics):
        """sample_size=20 meets the minimum — must pass."""
        metrics = _replace(passing_metrics, sample_size=20)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is True


# ===========================================================================
# 4. Hard-blocked lanes
# ===========================================================================


class TestHardBlockedLanes:
    """Lanes in HARD_BLOCKED_LANES are permanently ineligible."""

    @pytest.mark.parametrize("lane", sorted(HARD_BLOCKED_LANES))
    def test_hard_blocked_lane_is_ineligible(self, policy, passing_metrics, lane):
        """Every hard-blocked lane must be ineligible even with perfect metrics."""
        decision = policy.check_graduation_eligibility(lane, passing_metrics)
        assert decision.eligible is False

    @pytest.mark.parametrize("lane", sorted(HARD_BLOCKED_LANES))
    def test_hard_blocked_lane_recommended_action_is_blocked(self, policy, passing_metrics, lane):
        """Hard-blocked lanes must have recommended_action == 'blocked'."""
        decision = policy.check_graduation_eligibility(lane, passing_metrics)
        assert decision.recommended_action == "blocked"

    @pytest.mark.parametrize("lane", sorted(HARD_BLOCKED_LANES))
    def test_hard_blocked_lane_has_blocker_message(self, policy, passing_metrics, lane):
        """Hard-blocked lanes must include a blocker message explaining the block."""
        decision = policy.check_graduation_eligibility(lane, passing_metrics)
        assert len(decision.blockers) >= 1

    def test_hard_blocked_lanes_constant_contains_auth(self):
        """HARD_BLOCKED_LANES must include 'auth'."""
        assert "auth" in HARD_BLOCKED_LANES

    def test_hard_blocked_lanes_constant_contains_security(self):
        """HARD_BLOCKED_LANES must include 'security'."""
        assert "security" in HARD_BLOCKED_LANES

    def test_hard_blocked_lanes_constant_contains_deploy(self):
        """HARD_BLOCKED_LANES must include 'deploy'."""
        assert "deploy" in HARD_BLOCKED_LANES

    def test_hard_blocked_lanes_constant_contains_billing(self):
        """HARD_BLOCKED_LANES must include 'billing'."""
        assert "billing" in HARD_BLOCKED_LANES

    def test_hard_blocked_lanes_is_frozenset(self):
        """HARD_BLOCKED_LANES must be a frozenset (immutable)."""
        assert isinstance(HARD_BLOCKED_LANES, frozenset)

    def test_hard_blocked_lane_lane_field_matches(self, policy, passing_metrics):
        """The decision.lane field must match the hard-blocked lane name."""
        decision = policy.check_graduation_eligibility("auth", passing_metrics)
        assert decision.lane == "auth"


# ===========================================================================
# 5. Multiple simultaneous blockers
# ===========================================================================


class TestMultipleBlockers:
    """Verify that all failed criteria are reported together."""

    def test_two_criteria_failing_reports_two_blockers(self, policy):
        """When two criteria fail, both blockers must be present."""
        metrics = LaneMetrics(
            evaluator_pass_rate=0.80,  # fails: < 0.95
            requeue_rate=0.10,  # fails: > 0.05
            escaped_defects=0,
            human_touch_ratio=0.05,
            sample_size=30,
        )
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is False
        assert len(decision.blockers) == 2

    def test_all_criteria_failing_reports_all_blockers(self, policy):
        """When all five criteria fail, all five blockers must be present."""
        metrics = LaneMetrics(
            evaluator_pass_rate=0.80,  # fails
            requeue_rate=0.20,  # fails
            escaped_defects=3,  # fails
            human_touch_ratio=0.50,  # fails
            sample_size=5,  # fails
        )
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.eligible is False
        assert len(decision.blockers) == 5

    def test_ineligible_recommended_action_is_monitor(self, policy, passing_metrics):
        """Non-blocked ineligible lanes must have recommended_action == 'monitor'."""
        metrics = _replace(passing_metrics, evaluator_pass_rate=0.80)
        decision = policy.check_graduation_eligibility("api_simple", metrics)
        assert decision.recommended_action == "monitor"


# ===========================================================================
# 6. Data model invariants
# ===========================================================================


class TestDataModelInvariants:
    """Verify LaneMetrics and GraduationDecision structural guarantees."""

    def test_lane_metrics_is_frozen(self):
        """LaneMetrics is a frozen dataclass — attributes must not be mutable."""
        metrics = LaneMetrics(
            evaluator_pass_rate=0.97,
            requeue_rate=0.03,
            escaped_defects=0,
            human_touch_ratio=0.05,
            sample_size=50,
        )
        with pytest.raises((AttributeError, TypeError)):
            metrics.evaluator_pass_rate = 0.50  # type: ignore[misc]

    def test_graduation_decision_is_frozen(self, policy, passing_metrics):
        """GraduationDecision is a frozen dataclass — attributes must not be mutable."""
        decision = policy.check_graduation_eligibility("api_simple", passing_metrics)
        with pytest.raises((AttributeError, TypeError)):
            decision.eligible = False  # type: ignore[misc]

    def test_graduation_decision_lane_is_string(self, policy, passing_metrics):
        """decision.lane must be a str."""
        decision = policy.check_graduation_eligibility("api_simple", passing_metrics)
        assert isinstance(decision.lane, str)

    def test_graduation_decision_eligible_is_bool(self, policy, passing_metrics):
        """decision.eligible must be a bool."""
        decision = policy.check_graduation_eligibility("api_simple", passing_metrics)
        assert isinstance(decision.eligible, bool)

    def test_graduation_decision_blockers_is_list(self, policy, passing_metrics):
        """decision.blockers must be a list."""
        decision = policy.check_graduation_eligibility("api_simple", passing_metrics)
        assert isinstance(decision.blockers, list)

    def test_graduation_decision_recommended_action_is_string(self, policy, passing_metrics):
        """decision.recommended_action must be a str."""
        decision = policy.check_graduation_eligibility("api_simple", passing_metrics)
        assert isinstance(decision.recommended_action, str)


# ===========================================================================
# 7. Singleton factory
# ===========================================================================


class TestSingletonFactory:
    """Verify get_graduation_policy() returns a consistent singleton."""

    def test_get_graduation_policy_returns_instance(self):
        """get_graduation_policy() must return a LaneGraduationPolicy."""
        policy = get_graduation_policy()
        assert isinstance(policy, LaneGraduationPolicy)

    def test_get_graduation_policy_returns_same_instance(self):
        """Two calls to get_graduation_policy() must return the identical object."""
        p1 = get_graduation_policy()
        p2 = get_graduation_policy()
        assert p1 is p2


# ===========================================================================
# 8. API endpoint tests
# ===========================================================================


class TestGraduationAPIEndpoint:
    """Integration-style tests for POST /api/lanes/{lane}/check-graduation."""

    def _passing_payload(self) -> dict:
        return {
            "evaluator_pass_rate": 0.97,
            "requeue_rate": 0.03,
            "escaped_defects": 0,
            "human_touch_ratio": 0.05,
            "sample_size": 50,
        }

    def test_endpoint_returns_200_for_eligible_lane(self, client):
        """Eligible lane must return HTTP 200."""
        resp = client.post("/api/lanes/api_simple/check-graduation", json=self._passing_payload())
        assert resp.status_code == 200

    def test_endpoint_returns_200_for_ineligible_lane(self, client):
        """Ineligible (non-blocked) lane must also return HTTP 200 with eligible=false."""
        payload = self._passing_payload()
        payload["evaluator_pass_rate"] = 0.80
        resp = client.post("/api/lanes/api_simple/check-graduation", json=payload)
        assert resp.status_code == 200

    def test_endpoint_returns_correct_eligible_true(self, client):
        """Passing metrics must produce eligible=true in the response body."""
        resp = client.post("/api/lanes/api_simple/check-graduation", json=self._passing_payload())
        data = resp.json()["data"]
        assert data["eligible"] is True

    def test_endpoint_returns_correct_eligible_false(self, client):
        """Failing metrics must produce eligible=false in the response body."""
        payload = self._passing_payload()
        payload["evaluator_pass_rate"] = 0.80
        resp = client.post("/api/lanes/api_simple/check-graduation", json=payload)
        data = resp.json()["data"]
        assert data["eligible"] is False

    def test_endpoint_response_has_required_fields(self, client):
        """Response data must include lane, eligible, blockers, recommended_action."""
        resp = client.post("/api/lanes/api_simple/check-graduation", json=self._passing_payload())
        data = resp.json()["data"]
        assert "lane" in data
        assert "eligible" in data
        assert "blockers" in data
        assert "recommended_action" in data

    def test_endpoint_lane_field_matches_path(self, client):
        """response data.lane must reflect the lane from the URL path."""
        resp = client.post("/api/lanes/docs/check-graduation", json=self._passing_payload())
        assert resp.json()["data"]["lane"] == "docs"

    def test_endpoint_hard_blocked_lane_eligible_false(self, client):
        """Hard-blocked lane must return eligible=false even with perfect metrics."""
        resp = client.post("/api/lanes/auth/check-graduation", json=self._passing_payload())
        assert resp.json()["data"]["eligible"] is False

    def test_endpoint_hard_blocked_lane_action_is_blocked(self, client):
        """Hard-blocked lane must return recommended_action == 'blocked'."""
        resp = client.post("/api/lanes/billing/check-graduation", json=self._passing_payload())
        assert resp.json()["data"]["recommended_action"] == "blocked"

    def test_endpoint_missing_field_returns_422(self, client):
        """Request body missing a required field must return HTTP 422."""
        payload = self._passing_payload()
        del payload["evaluator_pass_rate"]
        resp = client.post("/api/lanes/api_simple/check-graduation", json=payload)
        assert resp.status_code == 422

    def test_endpoint_rate_out_of_range_returns_422(self, client):
        """evaluator_pass_rate > 1.0 must be rejected with HTTP 422."""
        payload = self._passing_payload()
        payload["evaluator_pass_rate"] = 1.5
        resp = client.post("/api/lanes/api_simple/check-graduation", json=payload)
        assert resp.status_code == 422

    def test_endpoint_negative_sample_size_returns_422(self, client):
        """Negative sample_size must be rejected with HTTP 422."""
        payload = self._passing_payload()
        payload["sample_size"] = -1
        resp = client.post("/api/lanes/api_simple/check-graduation", json=payload)
        assert resp.status_code == 422

    def test_endpoint_blockers_empty_when_eligible(self, client):
        """blockers list must be empty when the lane is eligible."""
        resp = client.post("/api/lanes/api_simple/check-graduation", json=self._passing_payload())
        assert resp.json()["data"]["blockers"] == []

    def test_endpoint_blockers_nonempty_when_ineligible(self, client):
        """blockers list must be non-empty when the lane is ineligible."""
        payload = self._passing_payload()
        payload["escaped_defects"] = 2
        resp = client.post("/api/lanes/api_simple/check-graduation", json=payload)
        assert len(resp.json()["data"]["blockers"]) >= 1
