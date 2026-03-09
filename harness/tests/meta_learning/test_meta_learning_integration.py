"""
Integration tests for the Meta-Learning System.

Tests the full flow from RalphLoopHarness through DecisionEngine
to approval flow and outcome recording.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge_harness.meta_learning import (
    DecisionEngine,
    LearningStore,
)
from forge_harness.meta_learning.schemas import (
    ConfidenceLevel,
    DecisionAction,
    DecisionContext,
    DecisionRecommendation,
    DiligenceFinding,
    DiligenceSignals,
    FindingCategory,
    FindingSeverity,
    ScoreCard,
)
from forge_harness.ralph_loop import (
    FeatureStatus,
    create_ralph_loop,
)


@pytest.fixture
def temp_dir(tmp_path):
    """Create temp directory for tests."""
    return tmp_path


@pytest.fixture
def features_json(temp_dir):
    """Create a features.json file."""
    features_data = {
        "features": [
            {
                "id": "F-001",
                "name": "Test feature",
                "description": "A test feature for integration testing",
                "status": "pending",
                "priority": "high",
            },
            {
                "id": "F-002",
                "name": "Security auth feature",
                "description": "Security-related feature",
                "status": "pending",
                "priority": "critical",
            },
            {
                "id": "F-003",
                "name": "Simple feature",
                "description": "A simple low-risk feature",
                "status": "pending",
                "priority": "low",
            },
        ]
    }
    features_path = temp_dir / "features.json"
    features_path.write_text(json.dumps(features_data, indent=2))
    return features_path


@pytest.fixture
def learning_store(temp_dir):
    """Create a LearningStore instance."""
    return LearningStore(storage_path=temp_dir / "learning")


@pytest.fixture
def decision_engine(learning_store):
    """Create a DecisionEngine with LearningStore."""
    return DecisionEngine(learning_store=learning_store)


class TestRalphLoopWithDecisionEngine:
    """Tests for RalphLoopHarness with DecisionEngine integration."""

    @pytest.mark.asyncio
    async def test_ralph_loop_without_engine(self, features_json, temp_dir):
        """Test Ralph loop works without decision engine."""
        loop = create_ralph_loop(
            features_path=features_json,
            max_iterations=1,
            dry_run=True,
            domain="test",
            project="test",
        )

        result = await loop.run()

        # Should run without errors
        assert result.iterations >= 0

    @pytest.mark.asyncio
    async def test_ralph_loop_with_engine_proceed(self, features_json, temp_dir, decision_engine):
        """Test Ralph loop proceeds with PROCEED recommendation."""
        loop = create_ralph_loop(
            features_path=features_json,
            max_iterations=1,
            dry_run=True,
            domain="test",
            project="test",
            decision_engine=decision_engine,
        )

        result = await loop.run()

        # Should proceed with feature
        assert result.iterations >= 1

    @pytest.mark.asyncio
    async def test_ralph_loop_blocks_on_block_recommendation(
        self, features_json, temp_dir, learning_store
    ):
        """Test Ralph loop blocks feature on BLOCK recommendation."""
        # Create mock engine that returns BLOCK
        mock_engine = AsyncMock()
        mock_engine.get_recommendation = AsyncMock(
            return_value=DecisionRecommendation(
                action=DecisionAction.BLOCK,
                confidence=ConfidenceLevel.HIGH,
                scores=ScoreCard(
                    risk_score=0.9,
                    confidence_score=0.8,
                    complexity_score=0.5,
                    precedent_score=0.2,
                ),
                reasoning="Critical issues detected - blocking",
                warnings=[],
            )
        )
        mock_engine.record_outcome = AsyncMock()
        mock_engine.learning_store = None  # Disable threshold adjustment in test

        loop = create_ralph_loop(
            features_path=features_json,
            max_iterations=3,
            dry_run=True,
            domain="test",
            project="test",
            decision_engine=mock_engine,
        )

        result = await loop.run()

        # All features should be blocked (check in-memory state)
        # Note: dry_run=True means features are NOT saved to disk, so we check
        # the in-memory store state directly without reloading from file.
        # We must NOT call loop.store.load() here as it would reload the original
        # file and overwrite the in-memory BLOCKED status.
        blocked_count = sum(
            1 for f in loop.store._features.values() if f.status == FeatureStatus.BLOCKED
        )
        assert blocked_count >= 1

    @pytest.mark.asyncio
    async def test_decision_context_built_from_feature(
        self, features_json, temp_dir, decision_engine
    ):
        """Test DecisionContext is built correctly from FeatureSpec."""
        loop = create_ralph_loop(
            features_path=features_json,
            max_iterations=0,  # Don't actually run
            dry_run=True,
            domain="test-domain",
            project="test-project",
            decision_engine=decision_engine,
        )
        loop.store.load()

        # Get the security feature
        feature = loop.store.get("F-002")
        assert feature is not None

        # Build context
        context = loop._build_decision_context(feature)

        assert context.domain == "test-domain"
        assert context.project == "test-project"
        assert context.feature_id == "F-002"
        assert "security" in context.tags
        assert "critical" in context.tags

    @pytest.mark.asyncio
    async def test_outcome_recorded_after_feature(self, features_json, temp_dir, learning_store):
        """Test outcome is recorded after feature completion."""
        decision_engine = DecisionEngine(learning_store=learning_store)

        loop = create_ralph_loop(
            features_path=features_json,
            max_iterations=1,
            dry_run=True,
            domain="test",
            project="test",
            decision_engine=decision_engine,
        )

        # Run once
        await loop.run()

        # Check that a decision was recorded
        stats = learning_store.get_statistics()
        assert stats["total_decisions"] >= 1


class TestDecisionEngineIntegration:
    """Integration tests for DecisionEngine with bridges."""

    @pytest.mark.asyncio
    async def test_engine_graceful_degradation(self, learning_store):
        """Test engine works with no bridges configured."""
        engine = DecisionEngine(learning_store=learning_store)

        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["main.py"],
            tags=[],
        )

        recommendation = await engine.get_recommendation(context)

        # Should still provide a recommendation
        assert recommendation.action in list(DecisionAction)
        assert recommendation.scores is not None

    @pytest.mark.asyncio
    async def test_engine_with_mock_diligence_signals(self, learning_store):
        """Test engine responds to diligence signals."""
        # Create mock diligence bridge with critical findings
        mock_diligence = AsyncMock()
        mock_diligence.get_signals = AsyncMock(
            return_value=DiligenceSignals(
                risk_level="critical",
                blocking_issues=[
                    DiligenceFinding(
                        finding_id="f1",
                        title="Critical SQL Injection",
                        severity=FindingSeverity.CRITICAL,
                        category=FindingCategory.SECURITY,
                    )
                ],
            )
        )

        engine = DecisionEngine(
            learning_store=learning_store,
            diligence_bridge=mock_diligence,
        )

        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["main.py"],
            tags=[],
        )

        recommendation = await engine.get_recommendation(context)

        # Should block due to critical finding
        assert recommendation.action == DecisionAction.BLOCK

    @pytest.mark.asyncio
    async def test_engine_records_decisions(self, learning_store):
        """Test engine records decisions for learning."""
        engine = DecisionEngine(learning_store=learning_store)

        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["main.py"],
            tags=["feature"],
        )

        # Make multiple decisions
        await engine.get_recommendation(context)
        await engine.get_recommendation(context)

        stats = learning_store.get_statistics()
        assert stats["total_decisions"] >= 2


class TestApprovalFlowIntegration:
    """Integration tests for approval flow."""

    @pytest.mark.asyncio
    async def test_human_review_skipped_without_queue(
        self, features_json, temp_dir, learning_store
    ):
        """Test human review is skipped when no approval queue."""
        # Create engine that returns HUMAN_REVIEW_REQUIRED
        mock_engine = AsyncMock()
        mock_engine.get_recommendation = AsyncMock(
            return_value=DecisionRecommendation(
                action=DecisionAction.HUMAN_REVIEW_REQUIRED,
                confidence=ConfidenceLevel.LOW,
                scores=ScoreCard(
                    risk_score=0.5,
                    confidence_score=0.3,
                    complexity_score=0.4,
                    precedent_score=0.1,
                ),
                reasoning="Requires human review",
                warnings=[],
            )
        )
        mock_engine.record_outcome = AsyncMock()
        mock_engine.learning_store = None  # Disable threshold adjustment in test

        loop = create_ralph_loop(
            features_path=features_json,
            max_iterations=1,
            dry_run=True,
            domain="test",
            project="test",
            decision_engine=mock_engine,
            approval_queue=None,  # No approval queue
        )

        result = await loop.run()

        # Should proceed (skip human review) when no queue
        assert result.iterations >= 1

    @pytest.mark.asyncio
    async def test_human_review_blocks_on_rejection(self, features_json, temp_dir, learning_store):
        """Test human review blocks feature on rejection."""
        # Create engine that returns HUMAN_REVIEW_REQUIRED
        mock_engine = AsyncMock()
        mock_engine.get_recommendation = AsyncMock(
            return_value=DecisionRecommendation(
                action=DecisionAction.HUMAN_REVIEW_REQUIRED,
                confidence=ConfidenceLevel.LOW,
                scores=ScoreCard(
                    risk_score=0.5,
                    confidence_score=0.3,
                    complexity_score=0.4,
                    precedent_score=0.1,
                ),
                reasoning="Requires human review",
                warnings=[],
            )
        )
        mock_engine.record_outcome = AsyncMock()
        mock_engine.learning_store = None  # Disable threshold adjustment in test

        # Create mock approval queue that rejects
        from forge_harness.approval_queue import ApprovalStatus

        mock_approval = AsyncMock()
        mock_request = MagicMock()
        mock_request.id = "request-123"
        mock_request.status = ApprovalStatus.PENDING
        mock_approval.create_request = AsyncMock(return_value=mock_request)

        # First call returns pending, second returns rejected
        rejected_request = MagicMock()
        rejected_request.status = ApprovalStatus.REJECTED
        mock_approval.get_request = AsyncMock(return_value=rejected_request)

        loop = create_ralph_loop(
            features_path=features_json,
            max_iterations=1,
            dry_run=True,
            domain="test",
            project="test",
            decision_engine=mock_engine,
            approval_queue=mock_approval,
        )

        result = await loop.run()

        # Feature F-002 (critical priority) should be blocked due to rejection
        # Note: dry_run=True means features are NOT saved to disk, so we check
        # the in-memory store state directly without reloading from file.
        # We must NOT call loop.store.load() here as it would reload the original
        # file and overwrite the in-memory BLOCKED status.
        feature = loop.store.get("F-002")
        assert feature.status == FeatureStatus.BLOCKED


class TestLearningStoreIntegration:
    """Integration tests for LearningStore with full system."""

    @pytest.mark.asyncio
    async def test_learning_improves_confidence(self, temp_dir):
        """Test learning store improves confidence over time."""
        learning_store = LearningStore(storage_path=temp_dir / "learning")
        engine = DecisionEngine(learning_store=learning_store)

        # Record a series of successful decisions
        for i in range(5):
            context = DecisionContext(
                domain="test",
                project="test",
                file_paths=["main.py"],
                tags=["feature"],
            )

            rec1 = await engine.get_recommendation(context)

            # Record successful outcome
            decision_id = list(engine._decision_cache.keys())[-1]
            await engine.record_outcome(decision_id, success=True)

        # Check accuracy improved
        stats = learning_store.get_statistics()
        assert stats["decision_success_rate"] > 0.0

    @pytest.mark.asyncio
    async def test_learning_store_persists(self, temp_dir):
        """Test learning store persists across sessions."""
        store_path = temp_dir / "learning"

        # First session - record some data
        store1 = LearningStore(storage_path=store_path)
        engine1 = DecisionEngine(learning_store=store1)

        context = DecisionContext(
            domain="test",
            project="test",
            file_paths=["main.py"],
            tags=["feature"],
        )
        await engine1.get_recommendation(context)

        # Force save
        store1.persist()

        # Second session - check data persists
        store2 = LearningStore(storage_path=store_path)
        stats2 = store2.get_statistics()

        assert stats2["total_decisions"] >= 1


class TestRegistryIntegration:
    """Integration tests for HarnessRegistry with meta-learning."""

    def test_registry_creates_decision_engine(self, temp_dir):
        """Test registry creates DecisionEngine correctly."""
        from forge_harness.harness_registry import HarnessConfig, HarnessRegistry

        config = HarnessConfig(
            domain="test",
            project="test",
            meta_learning_enabled=True,
            learning_store_dir=temp_dir / "learning",
        )

        registry = HarnessRegistry(config)
        engine = registry.get("decision_engine")

        from forge_harness.meta_learning import DecisionEngine

        assert isinstance(engine, DecisionEngine)

    def test_registry_wires_components(self, temp_dir):
        """Test registry wires components together."""
        from forge_harness.harness_registry import HarnessConfig, HarnessRegistry

        config = HarnessConfig(
            domain="test",
            project="test",
            meta_learning_enabled=True,
            learning_store_dir=temp_dir / "learning",
        )

        registry = HarnessRegistry(config)

        # Get engine and store
        engine = registry.get("decision_engine")
        store = registry.get("learning_store")

        # Engine should reference the same store
        assert engine.learning_store is store
