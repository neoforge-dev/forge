"""Test feedback loop integration with RalphLoopHarness."""

from datetime import UTC, datetime

import pytest

from forge_harness.harness_registry import HarnessConfig, create_harness_registry
from forge_harness.meta_learning import (
    FeedbackLoopManager,
    SessionSummary,
)
from forge_harness.ralph_loop import create_ralph_loop_from_registry


@pytest.mark.asyncio
class TestFeedbackLoopIntegration:
    """Test feedback loop wiring and execution."""

    async def test_feedback_loop_manager_registered(self):
        """Verify FeedbackLoopManager is registered in HarnessRegistry."""
        registry = create_harness_registry()

        # Check it's in the available list
        available = registry.list_available()
        assert "feedback_loop_manager" in available

        # Check it can be retrieved
        manager = registry.get("feedback_loop_manager")
        assert manager is not None
        assert isinstance(manager, FeedbackLoopManager)

    async def test_feedback_loop_manager_creation(self):
        """Verify FeedbackLoopManager is created with proper dependencies."""
        config = HarnessConfig(
            domain="test-domain",
            project="test-project",
            meta_learning_enabled=True,
        )
        registry = create_harness_registry(config=config)

        manager = registry.get("feedback_loop_manager")
        assert manager is not None

        # Verify it has access to learning_store
        assert hasattr(manager, "learning_store")
        assert manager.learning_store is not None

        # Verify bridges are None (not configured)
        assert hasattr(manager, "code_atlas")
        assert manager.code_atlas is None  # No URL configured

        assert hasattr(manager, "tech_diligence")
        assert manager.tech_diligence is None  # No URL configured

    async def test_ralph_loop_wired_with_feedback_manager(self, tmp_path):
        """Verify RalphLoopHarness gets FeedbackLoopManager from registry."""
        config = HarnessConfig(
            domain="test-domain",
            project="test-project",
            meta_learning_enabled=True,
        )
        registry = create_harness_registry(config=config)

        # Create features.json
        features_path = tmp_path / "features.json"
        features_path.write_text('{"features": []}')

        # Create Ralph loop from registry
        ralph_loop = create_ralph_loop_from_registry(
            features_path=features_path,
            registry=registry,
            domain="test-domain",
            project="test-project",
            dry_run=True,
        )

        # Verify feedback_loop_manager is wired
        assert hasattr(ralph_loop, "feedback_loop_manager")
        assert ralph_loop.feedback_loop_manager is not None
        assert isinstance(ralph_loop.feedback_loop_manager, FeedbackLoopManager)

        # Verify it's the same instance as in registry (singleton)
        registry_manager = registry.get("feedback_loop_manager")
        assert ralph_loop.feedback_loop_manager is registry_manager

    async def test_feedback_loop_execution(self):
        """Verify FeedbackLoopManager can execute session hooks."""
        config = HarnessConfig(
            domain="test-domain",
            project="test-project",
            meta_learning_enabled=True,
        )
        registry = create_harness_registry(config=config)
        manager = registry.get("feedback_loop_manager")

        # Create session summary
        session = SessionSummary(
            session_id="test-session-001",
            domain="test-domain",
            project="test-project",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            features_completed=3,
            features_blocked=0,
            total_decisions=5,
            success_rate=1.0,
            files_modified=["app/main.py"],
            patterns_learned=1,
        )

        # Execute feedback loops
        result = await manager.on_session_complete(session)

        # Verify result structure
        assert "session_id" in result
        assert result["session_id"] == "test-session-001"
        assert "hooks" in result

        # Post-session hook should have run (even if skipped)
        assert "post_session" in result["hooks"]
        post_session = result["hooks"]["post_session"]
        assert "session_id" in post_session
        assert post_session["session_id"] == "test-session-001"

        # Should skip indexing (no Code Atlas configured)
        assert post_session.get("skipped") == "no_code_atlas"
        assert post_session.get("indexed") is False

    async def test_feedback_loop_graceful_degradation(self):
        """Verify feedback loops degrade gracefully without bridges."""
        # Create registry without meta-learning enabled
        config = HarnessConfig(
            domain="test-domain",
            project="test-project",
            meta_learning_enabled=False,
        )
        registry = create_harness_registry(config=config)

        # FeedbackLoopManager should still be created
        # (it just won't have bridges)
        manager = registry.get("feedback_loop_manager")
        assert manager is not None

        # Execute should not fail
        session = SessionSummary(
            session_id="test-session-002",
            domain="test-domain",
            project="test-project",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            features_completed=1,
            features_blocked=0,
            total_decisions=2,
            success_rate=1.0,
        )

        result = await manager.on_session_complete(session)
        assert result is not None
        assert "hooks" in result

    async def test_tech_debt_scan(self):
        """Verify tech debt scanning works (even without bridge)."""
        config = HarnessConfig(
            domain="test-domain",
            project="test-project",
            meta_learning_enabled=True,
        )
        registry = create_harness_registry(config=config)
        manager = registry.get("feedback_loop_manager")

        # Scan should return empty list (no bridge configured)
        features = await manager.scan_for_debt(
            domain="test-domain",
            project="test-project",
            max_features=10,
        )

        # Should return empty list, not fail
        assert features == []
