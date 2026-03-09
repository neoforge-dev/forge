import tempfile
from pathlib import Path

from forge_harness.ralph_loop import FeatureSpec, FeatureStatus, FeatureStore, LoopConfig
from forge_harness.simple_history import SimpleHistory


async def test_domain_consistency():
    """Verify domain matches between recording and querying."""
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "history.jsonl"
        history = SimpleHistory(history_path)

        # Record with domain "test-domain" - need 3+ samples for boost
        for _ in range(3):
            history.record(
                domain="test-domain",
                project="test-project",
                action="feature:authentication",
                success=True,
                context={"feature_id": "auth-001"},
            )

        # Create config with same domain
        config = LoopConfig(domain="test-domain", project="test-project")

        # Create feature store with feature
        store = FeatureStore(Path(tmpdir) / "features.json")
        store._features = {
            "auth-001": FeatureSpec(
                id="auth-001",
                name="Auth test",
                description="JWT token authentication",
                category="authentication",
                status=FeatureStatus.PENDING,
            )
        }

        # Mock extractor that returns "authentication"
        def extractor(feature):
            return feature.category or "unknown"

        # Query should find the history
        feature = await store.get_next_pending(
            simple_history=history, config=config, feature_type_extractor=extractor
        )

        # Verify the feature received history boost
        assert feature is not None
        assert feature.id == "auth-001"
        # High success rate (1.0) should result in negative boost (priority boost)
        assert hasattr(feature, "_history_boost"), "Feature should have _history_boost attribute"
        assert feature._history_boost < 0, "Should have received priority boost"


async def test_action_type_consistency():
    """Verify action type matches between recording and querying."""
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "history.jsonl"
        history = SimpleHistory(history_path)

        # Record with semantic action type
        history.record(
            domain="test-domain",
            project="test-project",
            action="feature:authentication",  # Semantic type
            success=True,
        )
        history.record(
            domain="test-domain",
            project="test-project",
            action="feature:authentication",
            success=True,
        )
        history.record(
            domain="test-domain",
            project="test-project",
            action="feature:authentication",
            success=True,
        )

        config = LoopConfig(domain="test-domain", project="test-project", history_min_samples=3)

        store = FeatureStore(Path(tmpdir) / "features.json")
        store._features = {
            "auth-001": FeatureSpec(
                id="auth-001",  # ID prefix is "auth"
                name="Auth test",
                description="JWT authentication",
                category="authentication",  # Category is "authentication"
                status=FeatureStatus.PENDING,
            )
        }

        # Extractor returns category
        def extractor(feature):
            return feature.category or "unknown"

        feature = await store.get_next_pending(
            simple_history=history, config=config, feature_type_extractor=extractor
        )

        # Should match and apply boost
        assert feature is not None
        assert hasattr(feature, "_history_boost"), "Feature should have _history_boost attribute"
        assert feature._history_boost < 0  # Priority boost for high success


def test_mismatch_scenario_before_fix():
    """Reproduce the bug: domain mismatch causes no history match."""
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "history.jsonl"
        history = SimpleHistory(history_path)

        # Record with actual domain
        history.record(
            domain="codeswiftr-com",
            project="interview-simulator",
            action="feature:authentication",
            success=True,
        )

        config = LoopConfig(domain="codeswiftr-com", project="interview-simulator")

        store = FeatureStore(Path(tmpdir) / "features.json")
        store._features = {
            "auth-001": FeatureSpec(
                id="auth-001",
                name="Auth test",
                description="JWT authentication",
                category="authentication",
                status=FeatureStatus.PENDING,
            )
        }

        # If querying with hardcoded "forge" domain, no match
        success_rate_wrong = history.get_success_rate(
            domain="forge",  # BUG: hardcoded
            action="feature:authentication",
            limit=10,
        )
        assert success_rate_wrong == 0.5  # Default, no matches

        # If querying with correct domain, match found
        success_rate_correct = history.get_success_rate(
            domain="codeswiftr-com",  # Correct
            action="feature:authentication",
            limit=10,
        )
        assert success_rate_correct == 1.0  # 1/1 success


async def test_cross_domain_pattern_propagation():
    """Test patterns from domain A boost features in domain B."""
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "history.jsonl"
        history = SimpleHistory(history_path)

        # Record successful authentication in domain A
        for _ in range(5):
            history.record(
                domain="domain-a",
                project="project-a",
                action="feature:authentication",
                success=True,
            )

        # Create config for domain B (different domain)
        config = LoopConfig(
            domain="domain-b",
            project="project-b",
            cross_domain_enabled=True,
            cross_domain_min_samples=5,
        )

        store = FeatureStore(Path(tmpdir) / "features.json")
        store._features = {
            "auth-001": FeatureSpec(
                id="auth-001",
                name="Auth feature",
                description="Authentication",
                category="authentication",
                status=FeatureStatus.PENDING,
            )
        }

        def extractor(f):
            return f.category or "unknown"

        feature = await store.get_next_pending(
            simple_history=history,
            config=config,
            feature_type_extractor=extractor,
        )

        assert feature is not None
        # Should receive cross-domain boost (-0.15), not same-domain boost
        assert feature._history_boost == -0.15


async def test_cross_domain_respects_local_data_priority():
    """Test that cross-domain boost only applies when local data is insufficient."""
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "history.jsonl"
        history = SimpleHistory(history_path)

        # Record successful authentication in domain A (cross-domain data)
        for _ in range(5):
            history.record(
                domain="domain-a",
                project="project-a",
                action="feature:authentication",
                success=True,
            )

        # Record low success rate in domain B (local data - should take priority)
        for _ in range(3):
            history.record(
                domain="domain-b",
                project="project-b",
                action="feature:authentication",
                success=False,
            )

        config = LoopConfig(
            domain="domain-b",
            project="project-b",
            cross_domain_enabled=True,
            cross_domain_min_samples=5,
            history_min_samples=3,
        )

        store = FeatureStore(Path(tmpdir) / "features.json")
        store._features = {
            "auth-001": FeatureSpec(
                id="auth-001",
                name="Auth feature",
                description="Authentication",
                category="authentication",
                status=FeatureStatus.PENDING,
            )
        }

        def extractor(f):
            return f.category or "unknown"

        feature = await store.get_next_pending(
            simple_history=history,
            config=config,
            feature_type_extractor=extractor,
        )

        assert feature is not None
        # Should receive penalty from local data (0.5), NOT cross-domain boost
        # because local data takes priority
        assert feature._history_boost == 0.5  # Penalty for low local success rate


async def test_cross_domain_disabled():
    """Test that cross-domain boost can be disabled via config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "history.jsonl"
        history = SimpleHistory(history_path)

        # Record successful authentication in domain A
        for _ in range(5):
            history.record(
                domain="domain-a",
                project="project-a",
                action="feature:authentication",
                success=True,
            )

        # Create config with cross-domain DISABLED
        config = LoopConfig(
            domain="domain-b",
            project="project-b",
            cross_domain_enabled=False,  # Disabled
            cross_domain_min_samples=5,
        )

        store = FeatureStore(Path(tmpdir) / "features.json")
        store._features = {
            "auth-001": FeatureSpec(
                id="auth-001",
                name="Auth feature",
                description="Authentication",
                category="authentication",
                status=FeatureStatus.PENDING,
            )
        }

        def extractor(f):
            return f.category or "unknown"

        feature = await store.get_next_pending(
            simple_history=history,
            config=config,
            feature_type_extractor=extractor,
        )

        assert feature is not None
        # Should NOT receive cross-domain boost when disabled
        assert feature._history_boost == 0.0
