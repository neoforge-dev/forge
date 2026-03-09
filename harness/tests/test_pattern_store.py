"""Tests for pattern_store.py - Pattern Store Service.

Tests cover:
- Pattern dataclass
- PatternOutcome dataclass
- PatternStore CRUD operations
- Thompson Sampling updates
- A/B test variant stats
- get_pattern_store singleton
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from forge_harness.webhook_server.services.pattern_store import (
    Pattern,
    PatternOutcome,
    PatternStore,
    get_pattern_store,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tmp_forge_root(tmp_path: Path) -> Path:
    """Create a temporary FORGE root directory."""
    forge_root = tmp_path / "FORGE"
    forge_root.mkdir()
    # Create domains.yaml to identify as FORGE root
    (forge_root / "domains.yaml").write_text("domains: {}")
    return forge_root


@pytest.fixture
def pattern_store(tmp_forge_root: Path) -> PatternStore:
    """Create a PatternStore instance with temp forge_root."""
    return PatternStore(forge_root=tmp_forge_root)


# =============================================================================
# Pattern Dataclass Tests
# =============================================================================


class TestPattern:
    """Tests for Pattern dataclass."""

    def test_pattern_creation(self) -> None:
        """Should create pattern with default values."""
        pattern = Pattern(
            id="test-123",
            name="Test Pattern",
            category="testing",
            template="Hello {{name}}",
            variables=["name"],
        )

        assert pattern.id == "test-123"
        assert pattern.name == "Test Pattern"
        assert pattern.category == "testing"
        assert pattern.template == "Hello {{name}}"
        assert pattern.variables == ["name"]
        assert pattern.success_rate == 0.5  # Default
        assert pattern.uses == 0
        assert pattern.alpha == 1  # Default prior
        assert pattern.beta == 1  # Default prior
        assert pattern.version == 1

    def test_pattern_to_dict(self) -> None:
        """Should convert pattern to dictionary."""
        pattern = Pattern(
            id="test-123",
            name="Test Pattern",
            category="testing",
            template="Hello {{name}}",
            variables=["name"],
            success_rate=0.8,
            uses=10,
        )

        data = pattern.to_dict()

        assert data["id"] == "test-123"
        assert data["pattern_id"] == "test-123"  # API compatibility
        assert data["name"] == "Test Pattern"
        assert data["category"] == "testing"
        assert data["template"] == "Hello {{name}}"
        assert data["variables"] == ["name"]
        assert data["success_rate"] == 0.8
        assert data["total_uses"] == 10
        assert data["uses"] == 10  # Backward compatibility
        assert data["alpha"] == 1
        assert data["beta"] == 1

    def test_pattern_from_dict(self) -> None:
        """Should create pattern from dictionary."""
        data = {
            "id": "test-456",
            "pattern_id": "test-456",
            "name": "Another Pattern",
            "category": "code",
            "template": "def {{func_name}}():",
            "variables": ["func_name"],
            "success_rate": 0.9,
            "total_uses": 20,
            "uses": 20,
            "alpha": 5,
            "beta": 2,
            "version": 2,
        }

        pattern = Pattern.from_dict(data)

        assert pattern.id == "test-456"
        assert pattern.name == "Another Pattern"
        assert pattern.category == "code"
        assert pattern.success_rate == 0.9
        assert pattern.uses == 20
        assert pattern.alpha == 5
        assert pattern.beta == 2
        assert pattern.version == 2

    def test_pattern_from_dict_with_uses_only(self) -> None:
        """Should handle dict with only 'uses' field (not 'total_uses')."""
        data = {
            "id": "test-789",
            "name": "Legacy Pattern",
            "category": "legacy",
            "template": "legacy",
            "variables": [],
            "uses": 15,  # Only uses, no total_uses
        }

        pattern = Pattern.from_dict(data)

        assert pattern.uses == 15


# =============================================================================
# PatternOutcome Dataclass Tests
# =============================================================================


class TestPatternOutcome:
    """Tests for PatternOutcome dataclass."""

    def test_outcome_creation(self) -> None:
        """Should create outcome with default values."""
        outcome = PatternOutcome(
            id="outcome-1",
            pattern_id="pattern-1",
            success=True,
        )

        assert outcome.id == "outcome-1"
        assert outcome.pattern_id == "pattern-1"
        assert outcome.success is True
        assert outcome.variant is None
        assert outcome.context == {}
        assert outcome.timestamp is not None

    def test_outcome_to_dict(self) -> None:
        """Should convert outcome to dictionary."""
        outcome = PatternOutcome(
            id="outcome-2",
            pattern_id="pattern-1",
            success=False,
            variant="variant-a",
            context={"task": "testing"},
            timestamp="2024-01-01T00:00:00",
        )

        data = outcome.to_dict()

        assert data["id"] == "outcome-2"
        assert data["pattern_id"] == "pattern-1"
        assert data["success"] is False
        assert data["variant"] == "variant-a"
        assert data["context"] == {"task": "testing"}
        assert data["timestamp"] == "2024-01-01T00:00:00"

    def test_outcome_from_dict(self) -> None:
        """Should create outcome from dictionary."""
        data = {
            "id": "outcome-3",
            "pattern_id": "pattern-2",
            "success": True,
            "variant": "variant-b",
            "context": {"test": "data"},
            "timestamp": "2024-06-01T12:00:00",
        }

        outcome = PatternOutcome.from_dict(data)

        assert outcome.id == "outcome-3"
        assert outcome.pattern_id == "pattern-2"
        assert outcome.success is True
        assert outcome.variant == "variant-b"
        assert outcome.context == {"test": "data"}
        assert outcome.timestamp == "2024-06-01T12:00:00"


# =============================================================================
# PatternStore Initialization Tests
# =============================================================================


class TestPatternStoreInit:
    """Tests for PatternStore initialization."""

    def test_init_with_forge_root(self, tmp_forge_root: Path) -> None:
        """Should initialize with provided forge_root."""
        store = PatternStore(forge_root=tmp_forge_root)

        assert store._forge_root == tmp_forge_root
        assert store._patterns == {}
        assert store._loaded is False

    def test_init_without_forge_root(self, tmp_path: Path) -> None:
        """Should auto-detect forge_root when not provided."""
        # Create a FORGE-like structure
        (tmp_path / "domains.yaml").write_text("domains: {}")

        # Change to a subdirectory
        import os

        original_cwd = os.getcwd()
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        try:
            os.chdir(subdir)
            store = PatternStore()

            # Trigger the detection by calling _get_patterns_path
            path = store._get_patterns_path()

            # Should have detected the temp path as FORGE root
            # The detection walks up from cwd looking for domains.yaml
            assert store._forge_root == tmp_path
            assert ".forge/learning" in str(path)
        finally:
            os.chdir(original_cwd)


# =============================================================================
# PatternStore Path Tests
# =============================================================================


class TestPatternStorePaths:
    """Tests for PatternStore path methods."""

    def test_get_patterns_path(self, pattern_store: PatternStore) -> None:
        """Should return correct patterns file path."""
        path = pattern_store._get_patterns_path()

        assert path.name == "patterns.json"
        assert ".forge/learning" in str(path)

    def test_get_patterns_path_creates_directory(self, pattern_store: PatternStore) -> None:
        """Should create .forge/learning directory."""
        path = pattern_store._get_patterns_path()

        assert path.parent.exists()

    def test_get_outcomes_path(self, pattern_store: PatternStore) -> None:
        """Should return correct outcomes file path."""
        path = pattern_store._get_outcomes_path()

        assert path.name == "pattern_outcomes.json"
        assert ".forge/learning" in str(path)


# =============================================================================
# PatternStore CRUD Tests
# =============================================================================


class TestPatternStoreCRUD:
    """Tests for PatternStore CRUD operations."""

    def test_create_pattern(self, pattern_store: PatternStore) -> None:
        """Should create a new pattern."""
        pattern = pattern_store.create_or_update(
            pattern_id=None,
            name="New Pattern",
            category="test",
            template="Test {{var}}",
            variables=["var"],
        )

        assert pattern.name == "New Pattern"
        assert pattern.category == "test"
        assert pattern.id is not None
        assert pattern.id in pattern_store._patterns

    def test_update_existing_pattern(self, pattern_store: PatternStore) -> None:
        """Should update existing pattern."""
        # Create first
        pattern = pattern_store.create_or_update(
            pattern_id="test-pattern",
            name="Original Name",
            category="original",
            template="Original",
        )

        # Update
        updated = pattern_store.create_or_update(
            pattern_id="test-pattern",
            name="Updated Name",
            category="updated",
            template="Updated",
            variables=["new_var"],
        )

        assert updated.name == "Updated Name"
        assert updated.category == "updated"
        assert updated.template == "Updated"
        assert updated.variables == ["new_var"]

    def test_get_pattern_existing(self, pattern_store: PatternStore) -> None:
        """Should get existing pattern."""
        # Create pattern
        created = pattern_store.create_or_update(
            pattern_id="get-test",
            name="Get Test",
            category="test",
            template="test",
        )

        # Get it
        retrieved = pattern_store.get_pattern("get-test")

        assert retrieved is not None
        assert retrieved.id == "get-test"
        assert retrieved.name == "Get Test"

    def test_get_pattern_nonexistent(self, pattern_store: PatternStore) -> None:
        """Should return None for non-existent pattern."""
        result = pattern_store.get_pattern("does-not-exist")

        assert result is None

    def test_list_patterns_empty(self, pattern_store: PatternStore) -> None:
        """Should return empty list when no patterns."""
        patterns = pattern_store.list_patterns()

        assert patterns == []

    def test_list_patterns(self, pattern_store: PatternStore) -> None:
        """Should list all patterns."""
        pattern_store.create_or_update(
            pattern_id="p1", name="Pattern 1", category="cat1", template="t1"
        )
        pattern_store.create_or_update(
            pattern_id="p2", name="Pattern 2", category="cat2", template="t2"
        )
        pattern_store.create_or_update(
            pattern_id="p3", name="Pattern 3", category="cat1", template="t3"
        )

        patterns = pattern_store.list_patterns()

        assert len(patterns) == 3

    def test_list_patterns_by_category(self, pattern_store: PatternStore) -> None:
        """Should filter patterns by category."""
        pattern_store.create_or_update(
            pattern_id="p1", name="Pattern 1", category="cat1", template="t1"
        )
        pattern_store.create_or_update(
            pattern_id="p2", name="Pattern 2", category="cat2", template="t2"
        )
        pattern_store.create_or_update(
            pattern_id="p3", name="Pattern 3", category="cat1", template="t3"
        )

        patterns = pattern_store.list_patterns(category="cat1")

        assert len(patterns) == 2
        for p in patterns:
            assert p.category == "cat1"

    def test_delete_pattern_existing(self, pattern_store: PatternStore) -> None:
        """Should delete existing pattern."""
        pattern_store.create_or_update(
            pattern_id="delete-me", name="Delete Me", category="test", template="test"
        )

        result = pattern_store.delete_pattern("delete-me")

        assert result is True
        assert pattern_store.get_pattern("delete-me") is None

    def test_delete_pattern_nonexistent(self, pattern_store: PatternStore) -> None:
        """Should return False for non-existent pattern."""
        result = pattern_store.delete_pattern("does-not-exist")

        assert result is False


# =============================================================================
# PatternStore Persistence Tests
# =============================================================================


class TestPatternStorePersistence:
    """Tests for PatternStore save/load operations."""

    def test_save_and_load_patterns(self, pattern_store: PatternStore) -> None:
        """Should save and load patterns from disk."""
        # Create patterns
        pattern_store.create_or_update(
            pattern_id="persist-1", name="Persist 1", category="test", template="t1"
        )
        pattern_store.create_or_update(
            pattern_id="persist-2", name="Persist 2", category="test", template="t2"
        )

        # Create new store pointing to same location
        new_store = PatternStore(forge_root=pattern_store._forge_root)
        patterns = new_store.list_patterns()

        assert len(patterns) == 2
        ids = {p.id for p in patterns}
        assert "persist-1" in ids
        assert "persist-2" in ids

    def test_load_handles_corrupted_file(self, pattern_store: PatternStore) -> None:
        """Should handle corrupted patterns file gracefully."""
        # Write invalid JSON
        patterns_path = pattern_store._get_patterns_path()
        patterns_path.parent.mkdir(parents=True, exist_ok=True)
        patterns_path.write_text("not valid json")

        # Should not raise
        patterns = pattern_store.list_patterns()
        assert patterns == []


# =============================================================================
# Thompson Sampling Tests
# =============================================================================


class TestThompsonSampling:
    """Tests for Thompson Sampling updates."""

    def test_record_success_updates_alpha(self, pattern_store: PatternStore) -> None:
        """Should increment alpha on success."""
        pattern_store.create_or_update(
            pattern_id="thompson-test", name="Thompson Test", category="test", template="test"
        )

        outcome = pattern_store.record_outcome("thompson-test", success=True)

        assert outcome is not None
        pattern = pattern_store.get_pattern("thompson-test")
        assert pattern.alpha == 2  # 1 + 1
        assert pattern.beta == 1
        assert pattern.uses == 1

    def test_record_failure_updates_beta(self, pattern_store: PatternStore) -> None:
        """Should increment beta on failure."""
        pattern_store.create_or_update(
            pattern_id="thompson-test", name="Thompson Test", category="test", template="test"
        )

        outcome = pattern_store.record_outcome("thompson-test", success=False)

        assert outcome is not None
        pattern = pattern_store.get_pattern("thompson-test")
        assert pattern.alpha == 1
        assert pattern.beta == 2  # 1 + 1
        assert pattern.uses == 1

    def test_success_rate_calculation(self, pattern_store: PatternStore) -> None:
        """Should calculate success rate as alpha / (alpha + beta)."""
        pattern_store.create_or_update(
            pattern_id="rate-test", name="Rate Test", category="test", template="test"
        )

        # 3 successes, 1 failure
        pattern_store.record_outcome("rate-test", success=True)
        pattern_store.record_outcome("rate-test", success=True)
        pattern_store.record_outcome("rate-test", success=True)
        pattern_store.record_outcome("rate-test", success=False)

        pattern = pattern_store.get_pattern("rate-test")
        assert pattern.alpha == 4  # 1 + 3
        assert pattern.beta == 2  # 1 + 1
        # Expected success rate: 4 / (4 + 2) = 0.667
        assert abs(pattern.success_rate - 0.667) < 0.01

    def test_record_outcome_nonexistent_pattern(self, pattern_store: PatternStore) -> None:
        """Should return None for non-existent pattern."""
        result = pattern_store.record_outcome("does-not-exist", success=True)

        assert result is None


# =============================================================================
# Outcome Tests
# =============================================================================


class TestOutcomes:
    """Tests for outcome recording and retrieval."""

    def test_get_outcomes_empty(self, pattern_store: PatternStore) -> None:
        """Should return empty list when no outcomes."""
        outcomes = pattern_store.get_outcomes("any-pattern")

        assert outcomes == []

    def test_get_outcomes(self, pattern_store: PatternStore) -> None:
        """Should retrieve outcomes for pattern."""
        pattern_store.create_or_update(
            pattern_id="outcome-test", name="Outcome Test", category="test", template="test"
        )

        pattern_store.record_outcome("outcome-test", success=True)
        pattern_store.record_outcome("outcome-test", success=False)

        outcomes = pattern_store.get_outcomes("outcome-test")

        assert len(outcomes) == 2

    def test_get_outcomes_with_limit(self, pattern_store: PatternStore) -> None:
        """Should limit number of outcomes returned."""
        pattern_store.create_or_update(
            pattern_id="limit-test", name="Limit Test", category="test", template="test"
        )

        # Record 5 outcomes
        for _ in range(5):
            pattern_store.record_outcome("limit-test", success=True)

        outcomes = pattern_store.get_outcomes("limit-test", limit=3)

        assert len(outcomes) == 3

    def test_get_outcomes_sorted_by_timestamp(self, pattern_store: PatternStore) -> None:
        """Should return outcomes sorted by timestamp descending."""
        pattern_store.create_or_update(
            pattern_id="sort-test", name="Sort Test", category="test", template="test"
        )

        pattern_store.record_outcome("sort-test", success=True)
        pattern_store.record_outcome("sort-test", success=False)

        outcomes = pattern_store.get_outcomes("sort-test")

        # Most recent first
        assert outcomes[0].timestamp >= outcomes[-1].timestamp

    def test_outcome_with_variant(self, pattern_store: PatternStore) -> None:
        """Should record outcome with variant."""
        pattern_store.create_or_update(
            pattern_id="variant-test", name="Variant Test", category="test", template="test"
        )

        outcome = pattern_store.record_outcome(
            "variant-test", success=True, variant="variant-a", context={"test": "data"}
        )

        assert outcome is not None
        assert outcome.variant == "variant-a"
        assert outcome.context == {"test": "data"}


# =============================================================================
# A/B Test Variant Stats Tests
# =============================================================================


class TestVariantStats:
    """Tests for A/B test variant statistics."""

    def test_get_variant_stats(self, pattern_store: PatternStore) -> None:
        """Should calculate stats for each variant."""
        pattern_store.create_or_update(
            pattern_id="ab-test", name="A/B Test", category="test", template="test"
        )

        # Record outcomes for different variants
        pattern_store.record_outcome("ab-test", success=True, variant="control")
        pattern_store.record_outcome("ab-test", success=True, variant="control")
        pattern_store.record_outcome("ab-test", success=False, variant="control")
        pattern_store.record_outcome("ab-test", success=True, variant="treatment")
        pattern_store.record_outcome("ab-test", success=False, variant="treatment")

        stats = pattern_store.get_variant_stats("ab-test")

        assert "control" in stats
        assert "treatment" in stats

        control = stats["control"]
        assert control["successes"] == 2
        assert control["failures"] == 1
        assert control["total"] == 3
        assert "success_rate" in control

    def test_get_variant_stats_default_variant(self, pattern_store: PatternStore) -> None:
        """Should use 'default' for outcomes without variant."""
        pattern_store.create_or_update(
            pattern_id="no-variant", name="No Variant", category="test", template="test"
        )

        pattern_store.record_outcome("no-variant", success=True)  # No variant

        stats = pattern_store.get_variant_stats("no-variant")

        assert "default" in stats


# =============================================================================
# Singleton Tests
# =============================================================================


class TestGetPatternStore:
    """Tests for get_pattern_store singleton."""

    def test_get_pattern_store_returns_singleton(self) -> None:
        """Should return the same instance on multiple calls."""
        # Reset global state
        import forge_harness.webhook_server.services.pattern_store as ps

        ps._pattern_store = None

        store1 = get_pattern_store()
        store2 = get_pattern_store()

        assert store1 is store2

    def test_get_pattern_store_creates_new_if_none(self) -> None:
        """Should create new store if none exists."""
        # Reset global state
        import forge_harness.webhook_server.services.pattern_store as ps

        ps._pattern_store = None

        store = get_pattern_store()

        assert isinstance(store, PatternStore)


# =============================================================================
# Edge Case & Error Branch Tests (coverage gap closers)
# =============================================================================


class TestGetPatternPathFallback:
    """Tests for the no-domains-yaml fallback path (line 138)."""

    def test_forge_root_defaults_to_cwd_when_no_domains_yaml(self, tmp_path: Path) -> None:
        """When no domains.yaml exists anywhere, _forge_root should fall back to cwd."""
        import os

        original_cwd = os.getcwd()
        # Use a deep temp directory with no domains.yaml in its ancestry
        deep_dir = tmp_path / "a" / "b" / "c"
        deep_dir.mkdir(parents=True)

        try:
            os.chdir(deep_dir)
            store = PatternStore()  # No forge_root supplied
            # Calling _get_patterns_path forces the auto-detection
            path = store._get_patterns_path()

            # _forge_root must equal cwd because no domains.yaml was found
            assert store._forge_root == deep_dir
            assert path.name == "patterns.json"
            assert ".forge/learning" in str(path)
        finally:
            os.chdir(original_cwd)


class TestSaveErrorHandling:
    """Tests for exception handling in _save() (lines 176-177)."""

    def test_save_logs_error_on_permission_denied(
        self, pattern_store: PatternStore
    ) -> None:
        """Should catch and log exceptions when saving patterns fails."""
        # Pre-load so _save is called directly
        pattern_store._loaded = True
        pattern_store._patterns = {}

        # Make open() raise an OSError to trigger the except branch
        with patch(
            "forge_harness.webhook_server.services.pattern_store.open",
            side_effect=OSError("permission denied"),
        ):
            # Should not raise; the exception is caught and logged
            pattern_store._save()


class TestGetOutcomesPathWithNoneRoot:
    """Tests for _get_outcomes_path when _forge_root is None (line 269)."""

    def test_get_outcomes_path_triggers_root_detection(self, tmp_path: Path) -> None:
        """_get_outcomes_path should trigger _get_patterns_path when _forge_root is None."""
        import os

        original_cwd = os.getcwd()
        forge_root = tmp_path / "FORGE"
        forge_root.mkdir()
        (forge_root / "domains.yaml").write_text("domains: {}")
        subdir = forge_root / "subproject"
        subdir.mkdir()

        try:
            os.chdir(subdir)
            # Create store without supplying forge_root so _forge_root starts as None
            store = PatternStore()
            # Directly call _get_outcomes_path, which exercises line 269
            outcomes_path = store._get_outcomes_path()

            assert outcomes_path.name == "pattern_outcomes.json"
            assert ".forge/learning" in str(outcomes_path)
            # _forge_root is now populated (detected from cwd parents)
            assert store._forge_root is not None
        finally:
            os.chdir(original_cwd)


class TestLoadOutcomesErrorHandling:
    """Tests for exception handling in _load_outcomes() (lines 293-294)."""

    def test_load_outcomes_returns_empty_on_corrupted_file(
        self, pattern_store: PatternStore
    ) -> None:
        """Should return empty dict and log error when outcomes file is corrupted."""
        outcomes_path = pattern_store._get_outcomes_path()
        outcomes_path.parent.mkdir(parents=True, exist_ok=True)
        outcomes_path.write_text("{{invalid json{{")

        result = pattern_store._load_outcomes()

        assert result == {}

    def test_load_outcomes_returns_empty_on_read_exception(
        self, pattern_store: PatternStore
    ) -> None:
        """Should handle any exception during outcomes file reading."""
        # Create a valid outcomes file so it exists, then mock open to fail
        outcomes_path = pattern_store._get_outcomes_path()
        outcomes_path.parent.mkdir(parents=True, exist_ok=True)
        outcomes_path.write_text(json.dumps({"version": "1.0", "outcomes": {}}))

        with patch(
            "forge_harness.webhook_server.services.pattern_store.open",
            side_effect=OSError("read error"),
        ):
            result = pattern_store._load_outcomes()

        assert result == {}


class TestSaveOutcomesErrorHandling:
    """Tests for exception handling in _save_outcomes() (lines 317-318)."""

    def test_save_outcomes_logs_error_on_write_failure(
        self, pattern_store: PatternStore
    ) -> None:
        """Should catch and log exceptions when saving outcomes fails."""
        outcomes: dict = {}

        with patch(
            "forge_harness.webhook_server.services.pattern_store.open",
            side_effect=OSError("disk full"),
        ):
            # Should not raise; exception is caught and logged
            pattern_store._save_outcomes(outcomes)

    def test_save_outcomes_with_populated_data_write_error(
        self, pattern_store: PatternStore
    ) -> None:
        """Should handle write errors even when outcomes dict has data."""
        outcome = PatternOutcome(
            id="x1",
            pattern_id="p1",
            success=True,
        )
        outcomes = {"p1": [outcome]}

        with patch(
            "forge_harness.webhook_server.services.pattern_store.open",
            side_effect=PermissionError("no write access"),
        ):
            pattern_store._save_outcomes(outcomes)  # Must not raise
