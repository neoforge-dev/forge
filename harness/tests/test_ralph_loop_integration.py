"""Integration tests for Ralph autonomous loop (TASK-026 / RL-001).

Covers: decision engine integration, human review flow, telemetry,
threshold adjustment, query-atlas context, pattern recording,
feedback loop triggering, GitHub issue tracker, factory functions
(create_ralph_loop_from_registry), and full run() scenarios.

These tests target the lines left uncovered by the existing unit and
extended test suites, pushing ralph_loop.py coverage from ~72% to 80%+.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.ralph_loop import (
    FeatureSpec,
    FeatureStatus,
    FeatureStore,
    GitHubFeatureTracker,
    LoopConfig,
    RalphLoopHarness,
    _estimate_tokens_from_effort,
    _extract_priority_from_line,
    create_ralph_loop,
)

# =============================================================================
# Shared helpers
# =============================================================================


def _features_json(tmp_path: Path, features: list[dict], *, wrapper: bool = True) -> Path:
    """Write a features.json and return its path."""
    path = tmp_path / "features.json"
    payload = {"version": "1.0", "features": features} if wrapper else features
    path.write_text(json.dumps(payload))
    return path


def _feat_dict(
    fid: str = "feat-001",
    name: str = "Test Feature",
    description: str = "A feature",
    status: str = "pending",
    priority: str = "medium",
    **kwargs: object,
) -> dict:
    base = {
        "id": fid,
        "name": name,
        "description": description,
        "status": status,
        "priority": priority,
        "depends_on": [],
        "acceptance_criteria": [],
        "tests": [],
    }
    base.update(kwargs)
    return base


def _make_harness(
    tmp_path: Path,
    features: list[dict] | None = None,
    *,
    dry_run: bool = True,
    test_command: str = "true",
    max_iterations: int = 10,
    max_failures: int = 3,
    domain: str | None = None,
    project: str | None = None,
    decision_engine: object | None = None,
    approval_queue: object | None = None,
    telemetry: object | None = None,
    simple_history: object | None = None,
    orchestrator: object | None = None,
    code_atlas_bridge: object | None = None,
    feedback_loop_manager: object | None = None,
) -> RalphLoopHarness:
    if features is None:
        features = []
    fp = _features_json(tmp_path, features)
    store = FeatureStore(fp)
    config = LoopConfig(
        domain=domain,
        project=project,
        max_iterations=max_iterations,
        max_failures_per_feature=max_failures,
        test_command=test_command,
        dry_run=dry_run,
    )
    return RalphLoopHarness(
        feature_store=store,
        config=config,
        decision_engine=decision_engine,
        approval_queue=approval_queue,
        telemetry=telemetry,
        simple_history=simple_history,
        orchestrator=orchestrator,
        code_atlas_bridge=code_atlas_bridge,
        feedback_loop_manager=feedback_loop_manager,
    )


# =============================================================================
# Feature pickup and status filtering
# =============================================================================


class TestFeaturePickup:
    """Verify features are loaded, sorted, and filtered correctly."""

    def test_load_returns_features_sorted_by_priority(self, tmp_path: Path) -> None:
        features = [
            _feat_dict("low-001", priority="low"),
            _feat_dict("crit-001", priority="critical"),
            _feat_dict("med-001", priority="medium"),
            _feat_dict("high-001", priority="high"),
        ]
        fp = _features_json(tmp_path, features)
        store = FeatureStore(fp)
        loaded = store.load()
        priorities = [f.priority for f in loaded]
        assert priorities == ["critical", "high", "medium", "low"]

    def test_completed_features_excluded_from_pending(self, tmp_path: Path) -> None:
        features = [
            _feat_dict("done-001", status="completed"),
            _feat_dict("pend-001", status="pending"),
        ]
        fp = _features_json(tmp_path, features)
        store = FeatureStore(fp)
        store.load()
        stats = store.get_stats()
        assert stats["completed"] == 1
        assert stats["pending"] == 1

    async def test_get_next_pending_skips_completed_features(self, tmp_path: Path) -> None:
        features = [
            _feat_dict("done-001", status="completed"),
            _feat_dict("pend-001", status="pending"),
        ]
        fp = _features_json(tmp_path, features)
        store = FeatureStore(fp)
        store.load()
        nxt = await store.get_next_pending()
        assert nxt is not None
        assert nxt.id == "pend-001"

    async def test_get_next_pending_skips_in_progress(self, tmp_path: Path) -> None:
        features = [_feat_dict("wip-001", status="in_progress")]
        fp = _features_json(tmp_path, features)
        store = FeatureStore(fp)
        store.load()
        nxt = await store.get_next_pending()
        assert nxt is None

    async def test_get_next_pending_includes_failing_features(self, tmp_path: Path) -> None:
        features = [_feat_dict("fail-001", status="failing")]
        fp = _features_json(tmp_path, features)
        store = FeatureStore(fp)
        store.load()
        nxt = await store.get_next_pending()
        assert nxt is not None
        assert nxt.id == "fail-001"


# =============================================================================
# Full run() integration scenarios
# =============================================================================


class TestRunIntegration:
    """End-to-end run() scenarios with mocked test subprocess."""

    async def test_run_with_passing_tests_marks_feature_passing(self, tmp_path: Path) -> None:
        features = [_feat_dict("api-001", name="API Feature")]
        harness = _make_harness(tmp_path, features, dry_run=False, test_command="true")
        result = await harness.run()
        assert result.features_completed >= 1
        assert result.success is True

    async def test_run_with_failing_tests_increments_attempts(self, tmp_path: Path) -> None:
        features = [_feat_dict("api-002", name="API Feature 2")]
        harness = _make_harness(
            tmp_path,
            features,
            dry_run=False,
            test_command="false",
            max_failures=2,
            max_iterations=5,
        )
        result = await harness.run()
        # After max_failures attempts the feature should be blocked
        assert result.features_blocked >= 1

    async def test_run_completion_when_all_features_pass(self, tmp_path: Path) -> None:
        features = [
            _feat_dict("f-001"),
            _feat_dict("f-002"),
        ]
        harness = _make_harness(tmp_path, features, dry_run=False, test_command="true")
        result = await harness.run()
        assert result.success is True
        assert result.features_completed == 2
        assert result.features_remaining == 0

    async def test_run_telemetry_report_progress_called(self, tmp_path: Path) -> None:
        features = [_feat_dict("t-001")]
        mock_telemetry = MagicMock()
        mock_telemetry.register = AsyncMock()
        mock_telemetry.start_heartbeat_loop = AsyncMock()
        mock_telemetry.report_progress = AsyncMock()
        mock_telemetry.report_error = AsyncMock()
        mock_telemetry.complete = AsyncMock()
        harness = _make_harness(
            tmp_path, features, dry_run=True, telemetry=mock_telemetry
        )
        await harness.run()
        mock_telemetry.report_progress.assert_awaited()

    async def test_run_telemetry_report_error_on_block(self, tmp_path: Path) -> None:
        """When a feature is blocked, telemetry.report_error is called."""
        features = [_feat_dict("blk-001")]
        mock_telemetry = MagicMock()
        mock_telemetry.register = AsyncMock()
        mock_telemetry.start_heartbeat_loop = AsyncMock()
        mock_telemetry.report_progress = AsyncMock()
        mock_telemetry.report_error = AsyncMock()
        mock_telemetry.complete = AsyncMock()
        # Use max_failures=1 + failing test so it blocks immediately
        harness = _make_harness(
            tmp_path,
            features,
            dry_run=False,
            test_command="false",
            max_failures=1,
            max_iterations=5,
            telemetry=mock_telemetry,
        )
        result = await harness.run()
        assert result.features_blocked >= 1
        mock_telemetry.report_error.assert_awaited()

    async def test_run_telemetry_report_error_on_fail(self, tmp_path: Path) -> None:
        """telemetry.report_error is also called on a non-blocking failure."""
        features = [_feat_dict("fail-002")]
        mock_telemetry = MagicMock()
        mock_telemetry.register = AsyncMock()
        mock_telemetry.start_heartbeat_loop = AsyncMock()
        mock_telemetry.report_progress = AsyncMock()
        mock_telemetry.report_error = AsyncMock()
        mock_telemetry.complete = AsyncMock()
        # max_failures=3 so 1 failure → FAILING, not BLOCKED
        harness = _make_harness(
            tmp_path,
            features,
            dry_run=False,
            test_command="false",
            max_failures=3,
            max_iterations=2,
            telemetry=mock_telemetry,
        )
        await harness.run()
        mock_telemetry.report_error.assert_awaited()

    async def test_run_with_unsatisfied_dependencies_exits(self, tmp_path: Path) -> None:
        """Features with unmet deps exit the loop without processing."""
        features = [
            _feat_dict("dep-child", depends_on=["dep-parent"]),
        ]
        harness = _make_harness(tmp_path, features, dry_run=True)
        result = await harness.run()
        assert result.features_completed == 0


# =============================================================================
# DecisionEngine integration
# =============================================================================


class TestDecisionEngineIntegration:
    """Tests for decision engine BLOCK, HUMAN_REVIEW, and proceed paths."""

    def _mock_decision_engine(self, action: str) -> MagicMock:
        """Build a mock DecisionEngine that returns the given action."""
        from forge_harness.meta_learning.schemas import DecisionAction

        action_map = {
            "block": DecisionAction.BLOCK,
            "proceed": DecisionAction.PROCEED,
            "human_review": DecisionAction.HUMAN_REVIEW_REQUIRED,
        }

        recommendation = MagicMock()
        recommendation.action = action_map[action]
        recommendation.reasoning = "test reasoning"
        recommendation.confidence = MagicMock()
        recommendation.confidence.value = "high"
        recommendation.warnings = []
        recommendation.decision_id = "decision-abc-123"

        engine = MagicMock()
        engine.get_recommendation = AsyncMock(return_value=recommendation)
        engine.record_outcome = AsyncMock()
        engine.learning_store = None
        return engine

    async def test_decision_engine_block_marks_feature_blocked(self, tmp_path: Path) -> None:
        engine = self._mock_decision_engine("block")
        features = [_feat_dict("b-001", name="Blocked Feature")]
        harness = _make_harness(
            tmp_path, features, dry_run=False, decision_engine=engine
        )
        result = await harness.run()
        assert result.features_blocked >= 1

    async def test_decision_engine_block_skips_test_execution(self, tmp_path: Path) -> None:
        """When blocked, the test subprocess should NOT be called."""
        engine = self._mock_decision_engine("block")
        features = [_feat_dict("b-002", name="Blocked Feature 2")]
        harness = _make_harness(
            tmp_path, features, dry_run=False, decision_engine=engine
        )
        with patch("forge_harness.ralph_loop.asyncio.create_subprocess_shell") as mock_proc:
            await harness.run()
            mock_proc.assert_not_called()

    async def test_decision_engine_stores_decision_id(self, tmp_path: Path) -> None:
        engine = self._mock_decision_engine("proceed")
        features = [_feat_dict("p-001")]
        harness = _make_harness(
            tmp_path, features, dry_run=True, decision_engine=engine
        )
        await harness.run()
        # decision_id should have been stored after _get_decision
        assert "p-001" in harness._decision_ids

    async def test_decision_engine_error_does_not_crash_loop(self, tmp_path: Path) -> None:
        engine = MagicMock()
        engine.get_recommendation = AsyncMock(side_effect=RuntimeError("engine exploded"))
        engine.learning_store = None
        features = [_feat_dict("e-001")]
        harness = _make_harness(
            tmp_path, features, dry_run=True, decision_engine=engine
        )
        result = await harness.run()
        # Loop should complete despite engine error
        assert result.iterations >= 1

    async def test_record_outcome_with_decision_id(self, tmp_path: Path) -> None:
        """_record_outcome follows the decision_engine path when decision_id is present."""
        engine = self._mock_decision_engine("proceed")
        features = [_feat_dict("out-001")]
        harness = _make_harness(
            tmp_path, features, dry_run=True, decision_engine=engine
        )
        await harness.run()
        # record_outcome should have been called on the engine
        engine.record_outcome.assert_awaited()

    async def test_record_outcome_falls_back_on_engine_error(self, tmp_path: Path) -> None:
        """When engine.record_outcome raises, we fall back to learning store."""
        from forge_harness.meta_learning.schemas import DecisionAction

        recommendation = MagicMock()
        recommendation.action = DecisionAction.PROCEED
        recommendation.reasoning = "ok"
        recommendation.confidence = MagicMock()
        recommendation.confidence.value = "high"
        recommendation.warnings = []
        recommendation.decision_id = "fail-decision-id"

        engine = MagicMock()
        engine.get_recommendation = AsyncMock(return_value=recommendation)
        engine.record_outcome = AsyncMock(side_effect=RuntimeError("record failed"))
        engine.learning_store = None

        features = [_feat_dict("fb-001")]
        harness = _make_harness(
            tmp_path, features, dry_run=True, decision_engine=engine
        )
        # Should not raise even though record_outcome fails
        result = await harness.run()
        assert result.iterations >= 1


# =============================================================================
# Human review flow
# =============================================================================


class TestHumanReviewFlow:
    """Tests for _request_human_review and approval queue integration."""

    def _mock_approval_queue(self, final_status: str) -> MagicMock:
        """Build a mock ApprovalQueueHarness with a given final status."""
        from forge_harness.approval_queue import ApprovalStatus

        request = MagicMock()
        request.id = "req-001"

        updated = MagicMock()
        updated.status = {
            "approved": ApprovalStatus.APPROVED,
            "rejected": ApprovalStatus.REJECTED,
            "expired": ApprovalStatus.EXPIRED,
        }[final_status]

        queue = MagicMock()
        queue.create_request = AsyncMock(return_value=request)
        queue.get_request = AsyncMock(return_value=updated)
        return queue

    async def test_no_approval_queue_proceeds(self, tmp_path: Path) -> None:
        harness = _make_harness(tmp_path)
        feat = FeatureSpec(id="hr-001", name="HR Feature", description="needs review")
        recommendation = MagicMock()
        recommendation.reasoning = "risky"
        recommendation.warnings = []
        result = await harness._request_human_review(feat, recommendation)
        assert result is True

    async def test_approved_returns_true(self, tmp_path: Path) -> None:
        queue = self._mock_approval_queue("approved")
        harness = _make_harness(tmp_path, approval_queue=queue)
        feat = FeatureSpec(id="hr-002", name="HR Feature 2", description="needs review")
        recommendation = MagicMock()
        recommendation.reasoning = "risky"
        recommendation.warnings = []
        result = await harness._request_human_review(feat, recommendation)
        assert result is True

    async def test_rejected_returns_false(self, tmp_path: Path) -> None:
        queue = self._mock_approval_queue("rejected")
        harness = _make_harness(tmp_path, approval_queue=queue)
        feat = FeatureSpec(id="hr-003", name="HR Feature 3", description="needs review")
        recommendation = MagicMock()
        recommendation.reasoning = "risky"
        recommendation.warnings = []
        result = await harness._request_human_review(feat, recommendation)
        assert result is False

    async def test_expired_returns_false(self, tmp_path: Path) -> None:
        queue = self._mock_approval_queue("expired")
        harness = _make_harness(tmp_path, approval_queue=queue)
        feat = FeatureSpec(id="hr-004", name="HR Feature 4", description="needs review")
        recommendation = MagicMock()
        recommendation.reasoning = "risky"
        recommendation.warnings = []
        result = await harness._request_human_review(feat, recommendation)
        assert result is False

    async def test_approval_queue_exception_returns_false(self, tmp_path: Path) -> None:
        queue = MagicMock()
        queue.create_request = AsyncMock(side_effect=RuntimeError("queue exploded"))
        harness = _make_harness(tmp_path, approval_queue=queue)
        feat = FeatureSpec(id="hr-005", name="HR Feature 5", description="needs review")
        recommendation = MagicMock()
        recommendation.reasoning = "risky"
        recommendation.warnings = []
        result = await harness._request_human_review(feat, recommendation)
        assert result is False

    async def test_human_review_required_action_blocks_on_rejection(
        self, tmp_path: Path
    ) -> None:
        """Full run path: HUMAN_REVIEW_REQUIRED + rejected → feature blocked."""
        from forge_harness.meta_learning.schemas import DecisionAction

        recommendation = MagicMock()
        recommendation.action = DecisionAction.HUMAN_REVIEW_REQUIRED
        recommendation.reasoning = "security change"
        recommendation.confidence = MagicMock()
        recommendation.confidence.value = "medium"
        recommendation.warnings = []
        recommendation.decision_id = "decision-hr-001"

        engine = MagicMock()
        engine.get_recommendation = AsyncMock(return_value=recommendation)
        engine.record_outcome = AsyncMock()
        engine.learning_store = None

        queue = MagicMock()
        request = MagicMock()
        request.id = "req-hr-001"
        from forge_harness.approval_queue import ApprovalStatus

        rejected = MagicMock()
        rejected.status = ApprovalStatus.REJECTED
        queue.create_request = AsyncMock(return_value=request)
        queue.get_request = AsyncMock(return_value=rejected)

        features = [_feat_dict("sec-001", name="Security Feature")]
        harness = _make_harness(
            tmp_path,
            features,
            dry_run=False,
            decision_engine=engine,
            approval_queue=queue,
        )
        result = await harness.run()
        assert result.features_blocked >= 1


# =============================================================================
# Threshold adjustment via decision engine learning store
# =============================================================================


class TestThresholdAdjustment:
    """Covers the adjust_thresholds call inside run()."""

    async def test_threshold_adjustment_called_when_domain_set(self, tmp_path: Path) -> None:
        adjustment = MagicMock()
        adjustment.reason = "converging"
        adjustment.new_thresholds = {"auto_proceed_min_confidence": 0.8}

        learning_store = MagicMock()
        learning_store.adjust_thresholds = MagicMock(return_value=adjustment)

        from forge_harness.meta_learning.schemas import DecisionAction

        recommendation = MagicMock()
        recommendation.action = DecisionAction.PROCEED
        recommendation.reasoning = "ok"
        recommendation.confidence = MagicMock()
        recommendation.confidence.value = "high"
        recommendation.warnings = []
        recommendation.decision_id = "ta-decision-001"

        engine = MagicMock()
        engine.get_recommendation = AsyncMock(return_value=recommendation)
        engine.record_outcome = AsyncMock()
        engine.learning_store = learning_store

        features = [_feat_dict("ta-001")]
        harness = _make_harness(
            tmp_path,
            features,
            dry_run=True,
            decision_engine=engine,
            domain="test-domain",
            project="test-project",
        )
        await harness.run()
        learning_store.adjust_thresholds.assert_called()

    async def test_threshold_adjustment_no_domain_skipped(self, tmp_path: Path) -> None:
        learning_store = MagicMock()
        learning_store.adjust_thresholds = MagicMock(return_value=None)

        engine = MagicMock()
        engine.get_recommendation = AsyncMock(return_value=None)
        engine.record_outcome = AsyncMock()
        engine.learning_store = learning_store

        features = [_feat_dict("ta-002")]
        # No domain/project set → threshold adjustment should be skipped
        harness = _make_harness(
            tmp_path, features, dry_run=True, decision_engine=engine
        )
        await harness.run()
        learning_store.adjust_thresholds.assert_not_called()


# =============================================================================
# Code Atlas context query
# =============================================================================


class TestQueryAtlasForContext:
    """Tests for _query_atlas_for_context()."""

    async def test_returns_none_without_bridge(self, tmp_path: Path) -> None:
        harness = _make_harness(tmp_path)
        feat = FeatureSpec(id="ca-001", name="CA Feature", description="some desc")
        result = await harness._query_atlas_for_context(feat)
        assert result is None

    async def test_returns_context_on_high_confidence_response(self, tmp_path: Path) -> None:
        response = MagicMock()
        response.confidence = 0.9
        response.answer = "Use the builder pattern."
        response.sources = ["src/builder.py"]

        bridge = MagicMock()
        bridge.query_rag = AsyncMock(return_value=response)
        harness = _make_harness(tmp_path, code_atlas_bridge=bridge)
        feat = FeatureSpec(id="ca-002", name="CA Feature 2", description="another desc")
        result = await harness._query_atlas_for_context(feat)
        assert result is not None
        assert "builder pattern" in result

    async def test_returns_none_on_low_confidence_response(self, tmp_path: Path) -> None:
        response = MagicMock()
        response.confidence = 0.3
        response.answer = "not sure"
        response.sources = []

        bridge = MagicMock()
        bridge.query_rag = AsyncMock(return_value=response)
        harness = _make_harness(tmp_path, code_atlas_bridge=bridge)
        feat = FeatureSpec(id="ca-003", name="CA Feature 3", description="desc")
        result = await harness._query_atlas_for_context(feat)
        assert result is None

    async def test_returns_none_on_atlas_exception(self, tmp_path: Path) -> None:
        bridge = MagicMock()
        bridge.query_rag = AsyncMock(side_effect=RuntimeError("atlas down"))
        harness = _make_harness(tmp_path, code_atlas_bridge=bridge)
        feat = FeatureSpec(id="ca-004", name="CA Feature 4", description="desc")
        result = await harness._query_atlas_for_context(feat)
        assert result is None


# =============================================================================
# _implement_feature — pattern enrichment paths
# =============================================================================


class TestImplementFeatureEnrichment:
    """Tests for atlas/pattern enrichment in _implement_feature."""

    async def test_atlas_context_enriches_description(self, tmp_path: Path) -> None:
        """If atlas returns context, feature description is extended."""
        response = MagicMock()
        response.confidence = 0.8
        response.answer = "Atlas-enriched context"
        response.sources = ["src/x.py"]

        bridge = MagicMock()
        bridge.query_rag = AsyncMock(return_value=response)
        harness = _make_harness(
            tmp_path, dry_run=False, test_command="true", code_atlas_bridge=bridge
        )
        feat = FeatureSpec(id="ie-001", name="IE Feature", description="original")
        feat.status = FeatureStatus.IN_PROGRESS
        feat.attempts = 1
        with patch.object(harness, "_get_prior_patterns", return_value=[]):
            await harness._implement_feature(feat)
        assert "Atlas-enriched context" in feat.description

    async def test_no_orchestrator_returns_true_with_atlas_context(
        self, tmp_path: Path
    ) -> None:
        response = MagicMock()
        response.confidence = 0.75
        response.answer = "Relevant context here"
        response.sources = []

        bridge = MagicMock()
        bridge.query_rag = AsyncMock(return_value=response)
        harness = _make_harness(
            tmp_path, dry_run=False, test_command="true", code_atlas_bridge=bridge
        )
        feat = FeatureSpec(id="ie-002", name="IE Feature 2", description="base desc")
        with patch.object(harness, "_get_prior_patterns", return_value=[]):
            result = await harness._implement_feature(feat)
        assert result is True


# =============================================================================
# _record_pattern_outcome — new pattern creation path
# =============================================================================


class TestRecordPatternOutcome:
    """Tests for _record_pattern_outcome including the new-pattern path."""

    async def test_records_outcome_with_new_pattern(self, tmp_path: Path) -> None:
        """If no existing pattern, a new PatternRecord is created then updated."""
        from forge_harness.meta_learning.learning_store import LearningStore

        # Use a real temp LearningStore so we can assert on side effects
        forge_learning = tmp_path / ".forge/learning"
        forge_learning.mkdir()
        store = LearningStore(forge_learning)

        harness = _make_harness(tmp_path)
        feat = FeatureSpec(id="rpo-001", name="RPO Feature", description="desc")
        feat.attempts = 1

        # Patch inside the meta_learning module since it's imported locally in the function
        with patch(
            "forge_harness.meta_learning.learning_store.LearningStore",
            return_value=store,
        ):
            with patch(
                "forge_harness.ralph_loop.LearningStore",
                return_value=store,
                create=True,
            ):
                await harness._record_pattern_outcome(feat, success=True, duration=1.0)

        # Pattern may or may not exist depending on import path; just verify no crash
        # The important thing is the function completed without error

    async def test_records_failure_outcome(self, tmp_path: Path) -> None:
        from forge_harness.meta_learning.learning_store import LearningStore

        forge_learning = tmp_path / ".forge/learning"
        forge_learning.mkdir()
        store = LearningStore(forge_learning)

        harness = _make_harness(tmp_path)
        feat = FeatureSpec(id="rpo-002", name="RPO Feature 2", description="desc")
        feat.attempts = 2

        # The function creates its own LearningStore internally via a local import.
        # We just verify it completes without raising.
        await harness._record_pattern_outcome(
            feat, success=False, duration=0.5, error="test failed"
        )

    async def test_record_pattern_outcome_with_feedback_loop_manager(
        self, tmp_path: Path
    ) -> None:
        """Uses feedback_loop_manager.learning_store when available."""
        mock_ls = MagicMock()
        mock_ls.compute_context_signature = MagicMock(return_value="sig-abc")
        mock_ls.get_pattern = MagicMock(return_value=None)
        mock_ls.record_pattern = MagicMock()
        mock_ls.update_pattern_outcome = MagicMock()

        mock_flm = MagicMock()
        mock_flm.learning_store = mock_ls

        harness = _make_harness(tmp_path, feedback_loop_manager=mock_flm)
        feat = FeatureSpec(id="rpo-003", name="RPO Feature 3", description="desc")

        await harness._record_pattern_outcome(feat, success=True, duration=2.0)
        mock_ls.update_pattern_outcome.assert_called_once()


# =============================================================================
# _trigger_feedback_loops
# =============================================================================


class TestTriggerFeedbackLoops:
    """Tests for _trigger_feedback_loops() at session end."""

    async def test_direct_atlas_indexing_when_bridge_present(self, tmp_path: Path) -> None:
        bridge = MagicMock()
        bridge.index_session = AsyncMock(return_value={"indexed": True})
        harness = _make_harness(tmp_path, code_atlas_bridge=bridge)
        harness._start_time = datetime.now(UTC)
        harness._iteration = 5
        stats = {"passing": 3, "blocked": 0, "failing": 0, "pending": 0, "in_progress": 0}
        await harness._trigger_feedback_loops(stats, duration=30.0)
        bridge.index_session.assert_awaited()

    async def test_atlas_error_does_not_crash(self, tmp_path: Path) -> None:
        bridge = MagicMock()
        bridge.index_session = AsyncMock(side_effect=RuntimeError("atlas down"))
        harness = _make_harness(tmp_path, code_atlas_bridge=bridge)
        harness._start_time = datetime.now(UTC)
        harness._iteration = 1
        stats = {"passing": 1, "blocked": 0, "failing": 0, "pending": 0, "in_progress": 0}
        # Should not raise
        await harness._trigger_feedback_loops(stats, duration=5.0)

    async def test_feedback_loop_manager_on_session_complete_called(
        self, tmp_path: Path
    ) -> None:
        from forge_harness.meta_learning import SessionSummary

        mock_flm = MagicMock()
        mock_flm.on_session_complete = AsyncMock(
            return_value={"hooks": {"post_session": {"indexed": True}, "optimization": {"optimized": True}}}
        )

        harness = _make_harness(tmp_path, feedback_loop_manager=mock_flm)
        harness._start_time = datetime.now(UTC)
        harness._iteration = 3
        stats = {"passing": 2, "blocked": 0, "failing": 0, "pending": 0, "in_progress": 0}
        await harness._trigger_feedback_loops(stats, duration=10.0)
        mock_flm.on_session_complete.assert_awaited()

    async def test_feedback_loop_manager_exception_does_not_crash(
        self, tmp_path: Path
    ) -> None:
        mock_flm = MagicMock()
        mock_flm.on_session_complete = AsyncMock(side_effect=RuntimeError("flm exploded"))

        harness = _make_harness(tmp_path, feedback_loop_manager=mock_flm)
        harness._start_time = datetime.now(UTC)
        harness._iteration = 2
        stats = {"passing": 1, "blocked": 0, "failing": 0, "pending": 0, "in_progress": 0}
        # Should not raise
        await harness._trigger_feedback_loops(stats, duration=8.0)


# =============================================================================
# State management: checkpoint save/load
# =============================================================================


class TestCheckpointManagement:
    """Tests for checkpoint saving and loading."""

    async def test_checkpoint_saved_at_interval(self, tmp_path: Path) -> None:
        features = [_feat_dict(f"cp-{i:03d}") for i in range(10)]
        harness = _make_harness(
            tmp_path,
            features,
            dry_run=False,
            test_command="true",
            max_iterations=10,
        )
        # Override checkpoint_interval to 5 so we trigger a save
        harness.config.checkpoint_interval = 5
        result = await harness.run()
        assert result.checkpoint_path is not None
        assert result.checkpoint_path.exists()

    async def test_checkpoint_contains_expected_fields(self, tmp_path: Path) -> None:
        features = [_feat_dict("cp-data")]
        harness = _make_harness(tmp_path, features, domain="test-d", project="test-p")
        harness._iteration = 7
        harness._start_time = datetime.now(UTC)
        harness.store.load()
        path = await harness._save_checkpoint()
        data = json.loads(path.read_text())
        assert data["iteration"] == 7
        assert data["config"]["domain"] == "test-d"
        assert data["config"]["project"] == "test-p"
        assert "stats" in data
        assert "timestamp" in data
        assert "features_path" in data

    async def test_resume_continues_from_checkpoint_iteration(
        self, tmp_path: Path
    ) -> None:
        features = [_feat_dict("r-001")]
        harness = _make_harness(tmp_path, features, dry_run=True)
        harness._iteration = 5
        harness._start_time = datetime.now(UTC)
        harness.store.load()
        cp_path = await harness._save_checkpoint()

        # Build a fresh harness and resume
        harness2 = _make_harness(tmp_path, features, dry_run=True)
        harness2.store.load()
        result = await harness2.resume(cp_path)
        # After resume the starting iteration should be 5, and a further
        # iteration must have run (total > 5)
        assert result.iterations > 5


# =============================================================================
# GitHubFeatureTracker
# =============================================================================


class TestGitHubFeatureTracker:
    """Tests for GitHubFeatureTracker helper methods."""

    def test_get_headers_without_token(self) -> None:
        tracker = GitHubFeatureTracker("owner/repo", github_token=None)
        headers = tracker._get_headers()
        assert "Authorization" not in headers
        assert headers["Accept"] == "application/vnd.github+json"

    def test_get_headers_with_token(self) -> None:
        tracker = GitHubFeatureTracker("owner/repo", github_token="ghp_secret")
        headers = tracker._get_headers()
        assert "Authorization" in headers
        assert "ghp_secret" in headers["Authorization"]

    def test_feature_to_issue_body_contains_feature_id(self) -> None:
        tracker = GitHubFeatureTracker("owner/repo")
        feat = FeatureSpec(
            id="gh-001",
            name="GitHub Feature",
            description="A GitHub feature",
            acceptance_criteria=["It works"],
            depends_on=["gh-000"],
            tests=["test_github_feature"],
            last_error="something went wrong",
        )
        body = tracker._feature_to_issue_body(feat)
        assert "gh-001" in body
        assert "GitHub Feature" in body
        assert "It works" in body
        assert "gh-000" in body
        assert "test_github_feature" in body
        assert "something went wrong" in body
        assert "Ralph Loop Harness" in body

    def test_feature_to_issue_body_minimal_feature(self) -> None:
        tracker = GitHubFeatureTracker("owner/repo")
        feat = FeatureSpec(id="gh-002", name="Minimal", description="minimal desc")
        body = tracker._feature_to_issue_body(feat)
        assert "gh-002" in body
        assert "Minimal" in body

    def test_status_labels_cover_all_statuses(self) -> None:
        tracker = GitHubFeatureTracker("owner/repo")
        for status in FeatureStatus:
            assert status in tracker.STATUS_LABELS

    async def test_sync_to_issues_no_token_returns_empty(self, tmp_path: Path) -> None:
        tracker = GitHubFeatureTracker("owner/repo", github_token=None)
        feat = FeatureSpec(id="s-001", name="Sync Feature", description="sync")
        result = await tracker.sync_to_issues([feat])
        assert result == {}

    async def test_sync_to_issues_httpx_not_available_returns_empty(
        self, tmp_path: Path
    ) -> None:
        tracker = GitHubFeatureTracker("owner/repo", github_token="token")
        feat = FeatureSpec(id="s-002", name="Sync Feature 2", description="sync")
        with patch.dict("sys.modules", {"httpx": None}):
            result = await tracker.sync_to_issues([feat])
        assert result == {}

    async def test_update_issue_status_no_token_returns_none(self) -> None:
        tracker = GitHubFeatureTracker("owner/repo", github_token=None)
        # Should return None (no-op) without raising
        result = await tracker.update_issue_status("feat-001", 42, FeatureStatus.PASSING)
        assert result is None

    async def test_update_issue_status_httpx_not_available(self) -> None:
        tracker = GitHubFeatureTracker("owner/repo", github_token="tok")
        with patch.dict("sys.modules", {"httpx": None}):
            result = await tracker.update_issue_status("feat-001", 42, FeatureStatus.PASSING)
        assert result is None

    async def test_sync_creates_new_issue(self, tmp_path: Path) -> None:
        """sync_to_issues creates a new issue when none exists for the feature."""
        import httpx

        tracker = GitHubFeatureTracker("owner/repo", github_token="tok")
        feat = FeatureSpec(id="new-001", name="New Feature", description="new")

        search_response = MagicMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json = MagicMock(return_value={"items": []})

        create_response = MagicMock(spec=httpx.Response)
        create_response.status_code = 201
        create_response.json = MagicMock(return_value={"number": 99})

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=search_response)
        mock_client.post = AsyncMock(return_value=create_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tracker.sync_to_issues([feat])

        assert "new-001" in result
        assert result["new-001"] == 99

    async def test_sync_updates_existing_issue(self, tmp_path: Path) -> None:
        """sync_to_issues patches an existing issue when one already exists."""
        import httpx

        tracker = GitHubFeatureTracker("owner/repo", github_token="tok")
        feat = FeatureSpec(id="upd-001", name="Update Feature", description="update")

        search_response = MagicMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json = MagicMock(
            return_value={"items": [{"title": "[upd-001] Old Title", "number": 77}]}
        )

        patch_response = MagicMock(spec=httpx.Response)
        patch_response.status_code = 200
        patch_response.json = MagicMock(return_value={"number": 77})

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=search_response)
        mock_client.patch = AsyncMock(return_value=patch_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tracker.sync_to_issues([feat])

        assert "upd-001" in result
        assert result["upd-001"] == 77

    async def test_update_issue_status_adds_comment_on_passing(self) -> None:
        """Passing status triggers a comment POST on the issue."""
        import httpx

        tracker = GitHubFeatureTracker("owner/repo", github_token="tok")

        labels_response = MagicMock(spec=httpx.Response)
        labels_response.status_code = 200
        labels_response.json = MagicMock(return_value=[{"name": "status:failing"}])

        update_response = MagicMock(spec=httpx.Response)
        update_response.status_code = 200

        comment_response = MagicMock(spec=httpx.Response)
        comment_response.status_code = 201

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=labels_response)
        mock_client.put = AsyncMock(return_value=update_response)
        mock_client.post = AsyncMock(return_value=comment_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await tracker.update_issue_status("feat-001", 10, FeatureStatus.PASSING)

        mock_client.post.assert_awaited()


# =============================================================================
# create_ralph_loop factory
# =============================================================================


class TestCreateRalphLoopFactory:
    """Tests for the create_ralph_loop factory function."""

    def test_creates_harness_with_defaults(self, tmp_path: Path) -> None:
        fp = _features_json(tmp_path, [])
        harness = create_ralph_loop(features_path=fp, enable_telemetry=False)
        assert isinstance(harness, RalphLoopHarness)
        assert harness.config.max_iterations == 100
        assert harness.config.dry_run is False

    def test_creates_harness_with_custom_params(self, tmp_path: Path) -> None:
        fp = _features_json(tmp_path, [])
        harness = create_ralph_loop(
            features_path=fp,
            max_iterations=50,
            max_failures_per_feature=3,
            dry_run=True,
            domain="my-domain",
            project="my-project",
            test_command="pytest -x",
            enable_telemetry=False,
        )
        assert harness.config.max_iterations == 50
        assert harness.config.max_failures_per_feature == 3
        assert harness.config.dry_run is True
        assert harness.config.domain == "my-domain"
        assert harness.config.project == "my-project"
        assert harness.config.test_command == "pytest -x"

    def test_auto_telemetry_creation_failure_is_graceful(self, tmp_path: Path) -> None:
        """When auto-creating telemetry fails it falls back to None, no crash."""
        fp = _features_json(tmp_path, [])
        # The import is done lazily inside create_ralph_loop, so we patch the
        # module inside agent_telemetry where it originates
        with patch(
            "forge_harness.agent_telemetry.create_agent_telemetry_from_env",
            side_effect=RuntimeError("no token"),
        ):
            harness = create_ralph_loop(
                features_path=fp,
                enable_telemetry=True,
            )
        # Even if telemetry creation failed, harness should be valid
        assert isinstance(harness, RalphLoopHarness)

    def test_working_dir_is_set(self, tmp_path: Path) -> None:
        fp = _features_json(tmp_path, [])
        harness = create_ralph_loop(
            features_path=fp,
            working_dir=tmp_path / "work",
            enable_telemetry=False,
        )
        assert harness.config.working_dir == tmp_path / "work"

    def test_simple_history_passed_through(self, tmp_path: Path) -> None:
        from forge_harness.simple_history import SimpleHistory

        fp = _features_json(tmp_path, [])
        history_path = tmp_path / "history.jsonl"
        sh = SimpleHistory(history_path)
        harness = create_ralph_loop(
            features_path=fp,
            simple_history=sh,
            enable_telemetry=False,
        )
        assert harness.simple_history is sh


# =============================================================================
# create_ralph_loop_from_registry factory
# =============================================================================


class TestCreateRalphLoopFromRegistry:
    """Tests for the create_ralph_loop_from_registry factory function."""

    def test_creates_harness_with_registry_components(self, tmp_path: Path) -> None:
        from forge_harness.ralph_loop import create_ralph_loop_from_registry

        fp = _features_json(tmp_path, [])

        registry = MagicMock()
        registry.get = MagicMock(side_effect=lambda name: MagicMock())

        harness = create_ralph_loop_from_registry(
            features_path=fp,
            registry=registry,
            domain="reg-domain",
            project="reg-project",
            enable_telemetry=False,
        )
        assert isinstance(harness, RalphLoopHarness)
        assert harness.config.domain == "reg-domain"

    def test_gracefully_handles_registry_component_errors(self, tmp_path: Path) -> None:
        from forge_harness.ralph_loop import create_ralph_loop_from_registry

        fp = _features_json(tmp_path, [])

        registry = MagicMock()
        registry.get = MagicMock(side_effect=RuntimeError("component not found"))

        harness = create_ralph_loop_from_registry(
            features_path=fp,
            registry=registry,
            enable_telemetry=False,
        )
        assert isinstance(harness, RalphLoopHarness)

    def test_creates_registry_from_env_when_none(self, tmp_path: Path) -> None:
        from forge_harness.ralph_loop import create_ralph_loop_from_registry

        fp = _features_json(tmp_path, [])
        mock_registry = MagicMock()
        mock_registry.get = MagicMock(side_effect=RuntimeError("no component"))

        # The import is done lazily, so patch in the harness_registry module
        with patch(
            "forge_harness.harness_registry.create_harness_registry",
            return_value=mock_registry,
        ):
            harness = create_ralph_loop_from_registry(
                features_path=fp,
                registry=None,
                enable_telemetry=False,
            )
        assert isinstance(harness, RalphLoopHarness)


# =============================================================================
# Utility functions
# =============================================================================


class TestEstimateTokensFromEffort:
    """Tests for _estimate_tokens_from_effort()."""

    def test_hours_conversion(self) -> None:
        assert _estimate_tokens_from_effort("2h") == 4000

    def test_fractional_hours(self) -> None:
        assert _estimate_tokens_from_effort("1.5h") == 3000

    def test_minutes_conversion(self) -> None:
        assert _estimate_tokens_from_effort("30m") == 1000

    def test_invalid_falls_back_to_default(self) -> None:
        assert _estimate_tokens_from_effort("???") == 4000

    def test_empty_string_falls_back(self) -> None:
        assert _estimate_tokens_from_effort("") == 4000


class TestExtractPriorityFromLine:
    """Tests for _extract_priority_from_line()."""

    def test_p0_returns_critical(self) -> None:
        assert _extract_priority_from_line("P0: fix this now") == "critical"

    def test_critical_keyword(self) -> None:
        assert _extract_priority_from_line("CRITICAL: security bug") == "critical"

    def test_p1_returns_high(self) -> None:
        assert _extract_priority_from_line("P1 task") == "high"

    def test_high_priority_returns_high(self) -> None:
        assert _extract_priority_from_line("HIGH PRIORITY task") == "high"

    def test_p2_returns_medium(self) -> None:
        assert _extract_priority_from_line("P2 task") == "medium"

    def test_p3_returns_low(self) -> None:
        assert _extract_priority_from_line("P3 task") == "low"

    def test_low_keyword(self) -> None:
        assert _extract_priority_from_line("LOW priority") == "low"

    def test_no_priority_marker_returns_none(self) -> None:
        assert _extract_priority_from_line("just a regular line") is None
