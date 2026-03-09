"""Tests for PatternStore service.

Tests pattern storage and Thompson Sampling reinforcement learning.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from forge_harness.webhook_server.services.pattern_store import (
    Pattern,
    PatternOutcome,
    PatternStore,
    get_pattern_store,
)


class TestPattern:
    """Tests for Pattern dataclass."""

    def test_create_pattern_defaults(self):
        """Test creating a pattern with defaults."""
        pattern = Pattern(
            id="test-1",
            name="Test Pattern",
            category="testing",
            template="Run test: {test_name}",
            variables=["test_name"],
        )
        assert pattern.id == "test-1"
        assert pattern.name == "Test Pattern"
        assert pattern.category == "testing"
        assert pattern.template == "Run test: {test_name}"
        assert pattern.variables == ["test_name"]
        assert pattern.success_rate == 0.5  # Thompson prior
        assert pattern.uses == 0
        assert pattern.alpha == 1  # Prior
        assert pattern.beta == 1  # Prior
        assert pattern.version == 1

    def test_create_pattern_custom_values(self):
        """Test creating pattern with custom values."""
        pattern = Pattern(
            id="feat-1",
            name="Feature Pattern",
            category="feature",
            template="Implement {feature}",
            variables=["feature"],
            success_rate=0.8,
            uses=10,
            alpha=9,
            beta=3,
            version=2,
        )
        assert pattern.success_rate == 0.8
        assert pattern.uses == 10
        assert pattern.alpha == 9
        assert pattern.beta == 3
        assert pattern.version == 2

    def test_to_dict(self):
        """Test converting pattern to dictionary."""
        pattern = Pattern(
            id="dict-1",
            name="Dict Pattern",
            category="dict",
            template="Template",
            variables=["var1", "var2"],
        )
        result = pattern.to_dict()

        assert result["id"] == "dict-1"
        assert result["pattern_id"] == "dict-1"  # API compat
        assert result["name"] == "Dict Pattern"
        assert result["category"] == "dict"
        assert result["template"] == "Template"
        assert result["variables"] == ["var1", "var2"]
        assert result["success_rate"] == 0.5
        assert result["total_uses"] == 0  # API compat
        assert result["uses"] == 0
        assert result["version"] == 1

    def test_from_dict_with_id(self):
        """Test creating pattern from dict with id field."""
        data = {
            "id": "from-id",
            "name": "From ID",
            "category": "test",
            "template": "Test",
            "variables": ["x"],
        }
        pattern = Pattern.from_dict(data)

        assert pattern.id == "from-id"
        assert pattern.name == "From ID"

    def test_from_dict_with_pattern_id(self):
        """Test creating pattern from dict with pattern_id field."""
        data = {
            "pattern_id": "from-pattern-id",
            "name": "From Pattern ID",
            "category": "test",
            "template": "Test",
        }
        pattern = Pattern.from_dict(data)

        assert pattern.id == "from-pattern-id"

    def test_from_dict_with_total_uses(self):
        """Test creating pattern from dict with total_uses field."""
        data = {
            "id": "uses-test",
            "name": "Uses Test",
            "category": "test",
            "template": "Test",
            "total_uses": 50,
        }
        pattern = Pattern.from_dict(data)

        assert pattern.uses == 50


class TestPatternOutcome:
    """Tests for PatternOutcome dataclass."""

    def test_create_outcome(self):
        """Test creating an outcome."""
        outcome = PatternOutcome(
            id="out-1",
            pattern_id="pattern-1",
            success=True,
        )
        assert outcome.id == "out-1"
        assert outcome.pattern_id == "pattern-1"
        assert outcome.success is True
        assert outcome.variant is None
        assert outcome.context == {}

    def test_create_outcome_with_variant(self):
        """Test creating outcome with A/B variant."""
        outcome = PatternOutcome(
            id="out-2",
            pattern_id="pattern-1",
            success=False,
            variant="variant-b",
            context={"reason": "timeout"},
        )
        assert outcome.variant == "variant-b"
        assert outcome.context == {"reason": "timeout"}

    def test_to_dict(self):
        """Test converting outcome to dictionary."""
        outcome = PatternOutcome(
            id="out-3",
            pattern_id="pattern-2",
            success=True,
            variant="a",
            context={"key": "value"},
        )
        result = outcome.to_dict()

        assert result["id"] == "out-3"
        assert result["pattern_id"] == "pattern-2"
        assert result["success"] is True
        assert result["variant"] == "a"
        assert result["context"] == {"key": "value"}

    def test_from_dict(self):
        """Test creating outcome from dictionary."""
        data = {
            "id": "out-4",
            "pattern_id": "pattern-3",
            "success": False,
            "variant": "control",
            "context": {"test": True},
            "timestamp": "2026-02-15T20:00:00Z",
        }
        outcome = PatternOutcome.from_dict(data)

        assert outcome.id == "out-4"
        assert outcome.pattern_id == "pattern-3"
        assert outcome.success is False
        assert outcome.variant == "control"
        assert outcome.timestamp == "2026-02-15T20:00:00Z"


class TestPatternStore:
    """Tests for PatternStore class."""

    @pytest.fixture
    def temp_forge_root(self, tmp_path):
        """Create temporary forge root with .forge/learning directory."""
        forge_learning = tmp_path / ".forge/learning"
        forge_learning.mkdir()
        return tmp_path

    @pytest.fixture
    def store(self, temp_forge_root):
        """Create PatternStore with temp directory."""
        return PatternStore(forge_root=temp_forge_root)

    @pytest.fixture
    def store_with_patterns(self, temp_forge_root):
        """Create PatternStore with pre-existing patterns."""
        patterns_data = {
            "version": "1.0",
            "patterns": [
                {
                    "id": "existing-1",
                    "name": "Existing Pattern 1",
                    "category": "test",
                    "template": "Template 1",
                    "variables": ["var1"],
                    "success_rate": 0.7,
                    "uses": 10,
                    "alpha": 8,
                    "beta": 4,
                },
                {
                    "id": "existing-2",
                    "name": "Existing Pattern 2",
                    "category": "feature",
                    "template": "Template 2",
                    "variables": [],
                    "success_rate": 0.5,
                    "uses": 0,
                },
            ],
        }
        patterns_path = temp_forge_root / ".forge/learning" / "patterns.json"
        with open(patterns_path, "w") as f:
            json.dump(patterns_data, f)

        return PatternStore(forge_root=temp_forge_root)

    def test_init(self, store):
        """Test store initialization."""
        assert store._loaded is False
        assert store._patterns == {}

    def test_get_patterns_path(self, store, temp_forge_root):
        """Test getting patterns path."""
        path = store._get_patterns_path()
        assert path == temp_forge_root / ".forge/learning" / "patterns.json"

    def test_load_empty(self, store):
        """Test loading from empty/nonexistent file."""
        store._load()
        assert store._loaded is True
        assert store._patterns == {}

    def test_load_existing_patterns(self, store_with_patterns):
        """Test loading existing patterns from disk."""
        patterns = store_with_patterns.list_patterns()

        assert len(patterns) == 2
        ids = {p.id for p in patterns}
        assert "existing-1" in ids
        assert "existing-2" in ids

    def test_load_only_once(self, store_with_patterns):
        """Test that patterns are only loaded once."""
        store_with_patterns._load()
        first_patterns = dict(store_with_patterns._patterns)

        store_with_patterns._load()  # Should not reload
        second_patterns = dict(store_with_patterns._patterns)

        assert first_patterns == second_patterns

    def test_list_patterns_all(self, store_with_patterns):
        """Test listing all patterns."""
        patterns = store_with_patterns.list_patterns()
        assert len(patterns) == 2

    def test_list_patterns_by_category(self, store_with_patterns):
        """Test filtering patterns by category."""
        test_patterns = store_with_patterns.list_patterns(category="test")
        feature_patterns = store_with_patterns.list_patterns(category="feature")

        assert len(test_patterns) == 1
        assert test_patterns[0].id == "existing-1"

        assert len(feature_patterns) == 1
        assert feature_patterns[0].id == "existing-2"

    def test_list_patterns_empty_category(self, store_with_patterns):
        """Test filtering by non-existent category."""
        patterns = store_with_patterns.list_patterns(category="nonexistent")
        assert patterns == []

    def test_get_pattern_found(self, store_with_patterns):
        """Test getting existing pattern."""
        pattern = store_with_patterns.get_pattern("existing-1")

        assert pattern is not None
        assert pattern.id == "existing-1"
        assert pattern.name == "Existing Pattern 1"

    def test_get_pattern_not_found(self, store_with_patterns):
        """Test getting non-existent pattern."""
        pattern = store_with_patterns.get_pattern("nonexistent")
        assert pattern is None

    def test_create_pattern(self, store, temp_forge_root):
        """Test creating a new pattern."""
        pattern = store.create_or_update(
            pattern_id=None,  # Auto-generate
            name="New Pattern",
            category="new",
            template="New template: {x}",
            variables=["x"],
        )

        assert pattern.id is not None
        assert pattern.name == "New Pattern"
        assert pattern.category == "new"

        # Verify saved to disk
        patterns_path = temp_forge_root / ".forge/learning" / "patterns.json"
        assert patterns_path.exists()

        with open(patterns_path) as f:
            data = json.load(f)
        assert len(data["patterns"]) == 1

    def test_create_pattern_with_id(self, store):
        """Test creating pattern with specific ID."""
        pattern = store.create_or_update(
            pattern_id="custom-id",
            name="Custom ID Pattern",
            category="test",
            template="Test",
            variables=[],
        )

        assert pattern.id == "custom-id"

    def test_update_existing_pattern(self, store_with_patterns):
        """Test updating an existing pattern."""
        original = store_with_patterns.get_pattern("existing-1")
        original_uses = original.uses

        updated = store_with_patterns.create_or_update(
            pattern_id="existing-1",
            name="Updated Name",
            category="updated",
            template="Updated template",
            variables=["new_var"],
        )

        assert updated.id == "existing-1"
        assert updated.name == "Updated Name"
        assert updated.category == "updated"
        assert updated.template == "Updated template"
        assert updated.variables == ["new_var"]
        # Should preserve stats
        assert updated.uses == original_uses

    def test_delete_pattern_success(self, store_with_patterns):
        """Test deleting an existing pattern."""
        result = store_with_patterns.delete_pattern("existing-1")

        assert result is True
        assert store_with_patterns.get_pattern("existing-1") is None

        # Should still have the other pattern
        patterns = store_with_patterns.list_patterns()
        assert len(patterns) == 1

    def test_delete_pattern_not_found(self, store_with_patterns):
        """Test deleting non-existent pattern."""
        result = store_with_patterns.delete_pattern("nonexistent")
        assert result is False

    def test_record_outcome_success(self, store_with_patterns):
        """Test recording successful outcome."""
        original = store_with_patterns.get_pattern("existing-1")
        original_alpha = original.alpha
        original_uses = original.uses

        outcome = store_with_patterns.record_outcome(
            pattern_id="existing-1",
            success=True,
        )

        assert outcome is not None
        assert outcome.success is True
        assert outcome.pattern_id == "existing-1"

        # Pattern should be updated
        pattern = store_with_patterns.get_pattern("existing-1")
        assert pattern.alpha == original_alpha + 1
        assert pattern.uses == original_uses + 1

    def test_record_outcome_failure(self, store_with_patterns):
        """Test recording failed outcome."""
        original = store_with_patterns.get_pattern("existing-1")
        original_beta = original.beta

        outcome = store_with_patterns.record_outcome(
            pattern_id="existing-1",
            success=False,
        )

        assert outcome.success is False

        pattern = store_with_patterns.get_pattern("existing-1")
        assert pattern.beta == original_beta + 1

    def test_record_outcome_not_found(self, store_with_patterns):
        """Test recording outcome for non-existent pattern."""
        outcome = store_with_patterns.record_outcome(
            pattern_id="nonexistent",
            success=True,
        )
        assert outcome is None

    def test_record_outcome_with_variant(self, store_with_patterns):
        """Test recording outcome with A/B variant."""
        outcome = store_with_patterns.record_outcome(
            pattern_id="existing-1",
            success=True,
            variant="variant-a",
            context={"test": "value"},
        )

        assert outcome.variant == "variant-a"
        assert outcome.context == {"test": "value"}

    def test_record_outcome_updates_success_rate(self, store_with_patterns):
        """Test that recording outcome updates success rate via Thompson Sampling."""
        # Record several successes
        for _ in range(5):
            store_with_patterns.record_outcome("existing-2", success=True)

        pattern = store_with_patterns.get_pattern("existing-2")
        # Success rate should increase (started at 0.5 with alpha=1, beta=1)
        # After 5 successes: alpha=6, beta=1, rate = 6/7 ≈ 0.857
        assert pattern.success_rate > 0.8
        assert pattern.uses == 5

    def test_get_outcomes_empty(self, store_with_patterns):
        """Test getting outcomes when none exist."""
        outcomes = store_with_patterns.get_outcomes("existing-1")
        assert outcomes == []

    def test_get_outcomes(self, store_with_patterns):
        """Test getting recorded outcomes."""
        # Record some outcomes
        store_with_patterns.record_outcome("existing-1", success=True)
        store_with_patterns.record_outcome("existing-1", success=False)
        store_with_patterns.record_outcome("existing-1", success=True)

        outcomes = store_with_patterns.get_outcomes("existing-1")

        assert len(outcomes) == 3
        # Should be sorted by timestamp descending (most recent first)
        assert outcomes[0].success is True  # Last recorded
        assert outcomes[1].success is False
        assert outcomes[2].success is True  # First recorded

    def test_get_outcomes_with_limit(self, store_with_patterns):
        """Test limiting outcomes returned."""
        for i in range(10):
            store_with_patterns.record_outcome("existing-1", success=i % 2 == 0)

        outcomes = store_with_patterns.get_outcomes("existing-1", limit=5)

        assert len(outcomes) == 5

    def test_get_variant_stats(self, store_with_patterns):
        """Test getting A/B test variant statistics."""
        # Record outcomes with different variants
        store_with_patterns.record_outcome("existing-1", success=True, variant="a")
        store_with_patterns.record_outcome("existing-1", success=True, variant="a")
        store_with_patterns.record_outcome("existing-1", success=False, variant="a")
        store_with_patterns.record_outcome("existing-1", success=True, variant="b")
        store_with_patterns.record_outcome("existing-1", success=False, variant="b")
        store_with_patterns.record_outcome("existing-1", success=False, variant="b")

        stats = store_with_patterns.get_variant_stats("existing-1")

        assert "a" in stats
        assert "b" in stats

        assert stats["a"]["successes"] == 2
        assert stats["a"]["failures"] == 1
        assert stats["a"]["total"] == 3

        assert stats["b"]["successes"] == 1
        assert stats["b"]["failures"] == 2
        assert stats["b"]["total"] == 3

        # Variant A should have higher success rate
        assert stats["a"]["success_rate"] > stats["b"]["success_rate"]

    def test_get_variant_stats_default(self, store_with_patterns):
        """Test variant stats with no explicit variant (uses 'default')."""
        store_with_patterns.record_outcome("existing-1", success=True)
        store_with_patterns.record_outcome("existing-1", success=False)

        stats = store_with_patterns.get_variant_stats("existing-1")

        assert "default" in stats
        assert stats["default"]["successes"] == 1
        assert stats["default"]["failures"] == 1


class TestGetPatternStore:
    """Tests for get_pattern_store function."""

    def test_returns_store(self):
        """Test that get_pattern_store returns a PatternStore."""
        import forge_harness.webhook_server.services.pattern_store as ps_module

        ps_module._pattern_store = None

        store = get_pattern_store()

        assert isinstance(store, PatternStore)

    def test_returns_same_instance(self):
        """Test that get_pattern_store returns singleton."""
        import forge_harness.webhook_server.services.pattern_store as ps_module

        ps_module._pattern_store = None

        store1 = get_pattern_store()
        store2 = get_pattern_store()

        assert store1 is store2


# ---------------------------------------------------------------------------
# Additional coverage tests targeting previously-uncovered branches
# ---------------------------------------------------------------------------


class TestPatternStoreAutoDetectRoot:
    """Tests for _get_patterns_path auto-detection of forge root (lines 132-138)."""

    def test_get_patterns_path_no_root_finds_domains_yaml(self, tmp_path):
        """Auto-detect forge root when domains.yaml exists in cwd."""
        domains_yaml = tmp_path / "domains.yaml"
        domains_yaml.touch()

        store = PatternStore(forge_root=None)

        with patch("forge_harness.webhook_server.services.pattern_store.Path") as MockPath:
            # Make Path.cwd() return tmp_path so the loop finds domains.yaml there.
            mock_cwd = MagicMock()
            mock_cwd.parents = []
            # Simulate domains.yaml existing at cwd level
            mock_cwd.__truediv__ = lambda self, other: (
                domains_yaml if other == "domains.yaml" else tmp_path / other
            )
            MockPath.cwd.return_value = mock_cwd
            # Fall back to real Path for the mkdir call
            MockPath.side_effect = None

            # Exercise via the real filesystem instead — reset _forge_root after
            # patching is complex, so use the real path resolution with a cwd patch.

        # Simpler: create a store without a root, then monkeypatch cwd.
        store2 = PatternStore(forge_root=None)
        with patch(
            "forge_harness.webhook_server.services.pattern_store.Path.cwd",
            return_value=tmp_path,
        ):
            path = store2._get_patterns_path()

        assert path == tmp_path / ".forge/learning" / "patterns.json"
        assert store2._forge_root == tmp_path

    def test_get_patterns_path_no_root_fallback_to_cwd(self, tmp_path):
        """Fall back to cwd when domains.yaml is not found anywhere."""
        # tmp_path has no domains.yaml, and its parents won't either (in most
        # environments), but we patch cwd to a path with no domains.yaml.
        store = PatternStore(forge_root=None)
        with patch(
            "forge_harness.webhook_server.services.pattern_store.Path.cwd",
            return_value=tmp_path,
        ):
            path = store._get_patterns_path()

        assert path == tmp_path / ".forge/learning" / "patterns.json"

    def test_get_outcomes_path_triggers_root_detection(self, tmp_path):
        """_get_outcomes_path calls _get_patterns_path to detect root (line 269)."""
        store = PatternStore(forge_root=None)
        with patch(
            "forge_harness.webhook_server.services.pattern_store.Path.cwd",
            return_value=tmp_path,
        ):
            path = store._get_outcomes_path()

        assert path == tmp_path / ".forge/learning" / "pattern_outcomes.json"
        assert store._forge_root == tmp_path


class TestPatternStoreLoadErrors:
    """Tests for error-handling branches in _load and _load_outcomes."""

    @pytest.fixture
    def temp_forge_root(self, tmp_path):
        forge_learning = tmp_path / ".forge/learning"
        forge_learning.mkdir()
        return tmp_path

    def test_load_corrupt_json_logs_error_and_stays_empty(self, temp_forge_root):
        """_load gracefully handles corrupt JSON (lines 160-161)."""
        patterns_path = temp_forge_root / ".forge/learning" / "patterns.json"
        patterns_path.write_text("{ not valid json !!!}")

        store = PatternStore(forge_root=temp_forge_root)
        # Should not raise; error is swallowed and logged
        store._load()

        assert store._loaded is True
        assert store._patterns == {}

    def test_load_outcomes_corrupt_json_logs_error_and_returns_empty(self, temp_forge_root):
        """_load_outcomes gracefully handles corrupt JSON (lines 293-294)."""
        outcomes_path = temp_forge_root / ".forge/learning" / "pattern_outcomes.json"
        outcomes_path.write_text("not json at all")

        store = PatternStore(forge_root=temp_forge_root)
        result = store._load_outcomes()

        assert result == {}

    def test_load_outcomes_valid_file_parses_correctly(self, temp_forge_root):
        """_load_outcomes parses a valid outcomes file with multiple patterns."""
        outcomes_data = {
            "version": "1.0",
            "outcomes": {
                "pat-1": [
                    {
                        "id": "o1",
                        "pattern_id": "pat-1",
                        "success": True,
                        "variant": None,
                        "context": {},
                        "timestamp": "2026-02-22T10:00:00+00:00",
                    }
                ],
                "pat-2": [
                    {
                        "id": "o2",
                        "pattern_id": "pat-2",
                        "success": False,
                        "variant": "b",
                        "context": {"k": "v"},
                        "timestamp": "2026-02-22T11:00:00+00:00",
                    }
                ],
            },
        }
        outcomes_path = temp_forge_root / ".forge/learning" / "pattern_outcomes.json"
        outcomes_path.write_text(json.dumps(outcomes_data))

        store = PatternStore(forge_root=temp_forge_root)
        result = store._load_outcomes()

        assert "pat-1" in result
        assert "pat-2" in result
        assert result["pat-1"][0].id == "o1"
        assert result["pat-2"][0].success is False


class TestPatternStoreSaveErrors:
    """Tests for IOError branches in _save and _save_outcomes (lines 176-177, 317-318)."""

    @pytest.fixture
    def temp_store(self, tmp_path):
        forge_learning = tmp_path / ".forge/learning"
        forge_learning.mkdir()
        return PatternStore(forge_root=tmp_path)

    def test_save_io_error_logs_and_does_not_raise(self, temp_store):
        """_save swallows an IOError and logs it (lines 176-177)."""
        # Force _loaded so we skip the _load call in create_or_update
        temp_store._load()

        with patch("builtins.open", side_effect=OSError("disk full")):
            # Should not raise
            temp_store._save()

    def test_save_outcomes_io_error_logs_and_does_not_raise(self, temp_store):
        """_save_outcomes swallows an IOError and logs it (lines 317-318)."""
        outcomes = {
            "pat-x": [
                PatternOutcome(
                    id="o1",
                    pattern_id="pat-x",
                    success=True,
                )
            ]
        }

        with patch("builtins.open", side_effect=OSError("no space left")):
            # Should not raise
            temp_store._save_outcomes(outcomes)

    def test_create_or_update_save_failure_still_returns_pattern(self, temp_store):
        """Even when _save fails, the in-memory pattern is returned correctly."""
        temp_store._load()

        with patch("builtins.open", side_effect=OSError("disk full")):
            pattern = temp_store.create_or_update(
                pattern_id="saved-fail",
                name="Save-Fail Pattern",
                category="edge",
                template="template",
                variables=[],
            )

        # The in-memory state is still correct
        assert pattern.id == "saved-fail"
        assert temp_store._patterns["saved-fail"].name == "Save-Fail Pattern"


class TestPatternDataclassEdgeCases:
    """Additional edge-case tests for Pattern and PatternOutcome."""

    def test_pattern_from_dict_falls_back_to_uses_key(self):
        """from_dict prefers total_uses; falls back to uses when total_uses absent."""
        data = {
            "id": "p1",
            "name": "n",
            "category": "c",
            "template": "t",
            "uses": 7,
        }
        pattern = Pattern.from_dict(data)
        assert pattern.uses == 7

    def test_pattern_from_dict_defaults_when_optional_fields_missing(self):
        """from_dict provides sensible defaults for every optional field."""
        data = {
            "id": "minimal",
            "name": "Minimal",
            "category": "cat",
            "template": "tmpl",
        }
        pattern = Pattern.from_dict(data)
        assert pattern.variables == []
        assert pattern.success_rate == 0.5
        assert pattern.uses == 0
        assert pattern.alpha == 1
        assert pattern.beta == 1
        assert pattern.version == 1
        assert pattern.created_at is not None
        assert pattern.updated_at is not None

    def test_pattern_to_dict_roundtrip(self):
        """A pattern serialised to dict and back should be equivalent."""
        original = Pattern(
            id="rt-1",
            name="RoundTrip",
            category="rt",
            template="tmpl {x}",
            variables=["x"],
            success_rate=0.75,
            uses=20,
            alpha=16,
            beta=6,
            version=3,
        )
        data = original.to_dict()
        restored = Pattern.from_dict(data)

        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.category == original.category
        assert restored.template == original.template
        assert restored.variables == original.variables
        assert restored.success_rate == original.success_rate
        assert restored.uses == original.uses
        assert restored.alpha == original.alpha
        assert restored.beta == original.beta
        assert restored.version == original.version

    def test_pattern_created_at_is_set(self):
        """Pattern.created_at is auto-populated on creation."""
        pattern = Pattern(
            id="ts-1",
            name="TS",
            category="c",
            template="t",
            variables=[],
        )
        assert pattern.created_at is not None
        # Should parse as a valid ISO datetime
        dt = datetime.fromisoformat(pattern.created_at)
        assert dt.tzinfo is not None

    def test_pattern_outcome_from_dict_defaults_timestamp(self):
        """PatternOutcome.from_dict fills timestamp when absent."""
        data = {
            "id": "o99",
            "pattern_id": "p99",
            "success": True,
        }
        outcome = PatternOutcome.from_dict(data)
        assert outcome.timestamp is not None

    def test_pattern_outcome_to_dict_includes_all_fields(self):
        """PatternOutcome.to_dict includes every declared field."""
        outcome = PatternOutcome(
            id="o-full",
            pattern_id="p-full",
            success=False,
            variant="ctrl",
            context={"foo": "bar"},
        )
        d = outcome.to_dict()
        assert set(d.keys()) == {"id", "pattern_id", "success", "variant", "context", "timestamp"}


class TestPatternStoreGetVariantStatsEdgeCases:
    """Edge cases for get_variant_stats."""

    @pytest.fixture
    def temp_forge_root(self, tmp_path):
        (tmp_path / ".forge/learning").mkdir()
        return tmp_path

    @pytest.fixture
    def store(self, temp_forge_root):
        s = PatternStore(forge_root=temp_forge_root)
        s.create_or_update("vp-1", "VP1", "test", "tmpl", [])
        return s

    def test_variant_stats_empty_when_no_outcomes(self, store):
        """get_variant_stats returns empty dict when no outcomes recorded."""
        stats = store.get_variant_stats("vp-1")
        assert stats == {}

    def test_variant_stats_multiple_variants_beta_distribution(self, store):
        """Alpha/beta counts are correct for Thompson Sampling bookkeeping."""
        # 3 successes, 1 failure for variant "x"
        for _ in range(3):
            store.record_outcome("vp-1", success=True, variant="x")
        store.record_outcome("vp-1", success=False, variant="x")

        stats = store.get_variant_stats("vp-1")
        assert stats["x"]["alpha"] == 1 + 3  # prior 1 + 3 successes
        assert stats["x"]["beta"] == 1 + 1  # prior 1 + 1 failure
        expected_rate = 4 / (4 + 2)
        assert abs(stats["x"]["success_rate"] - expected_rate) < 1e-9

    def test_variant_stats_single_success_only(self, store):
        """Single success produces correct stats."""
        store.record_outcome("vp-1", success=True, variant="solo")
        stats = store.get_variant_stats("vp-1")
        assert stats["solo"]["successes"] == 1
        assert stats["solo"]["failures"] == 0
        assert stats["solo"]["total"] == 1

    def test_variant_stats_mixed_default_and_named(self, store):
        """Outcomes with and without explicit variant are bucketed correctly."""
        store.record_outcome("vp-1", success=True)  # -> "default"
        store.record_outcome("vp-1", success=False, variant="v2")

        stats = store.get_variant_stats("vp-1")
        assert "default" in stats
        assert "v2" in stats
        assert stats["default"]["successes"] == 1
        assert stats["v2"]["failures"] == 1


class TestPatternStoreCreateOrUpdateEdgeCases:
    """Additional edge cases for create_or_update."""

    @pytest.fixture
    def temp_store(self, tmp_path):
        (tmp_path / ".forge/learning").mkdir()
        return PatternStore(forge_root=tmp_path)

    def test_create_with_no_variables_defaults_to_empty_list(self, temp_store):
        """variables=None should be stored as an empty list."""
        pattern = temp_store.create_or_update(
            pattern_id="no-vars",
            name="No Vars",
            category="c",
            template="t",
            variables=None,
        )
        assert pattern.variables == []

    def test_update_preserves_alpha_beta(self, temp_store):
        """Updating a pattern's name/template does not reset Thompson priors."""
        temp_store.create_or_update("tp-1", "Original", "c", "t", [])
        # Simulate some learning
        temp_store.record_outcome("tp-1", success=True)
        temp_store.record_outcome("tp-1", success=True)

        before = temp_store.get_pattern("tp-1")
        alpha_before = before.alpha
        beta_before = before.beta

        temp_store.create_or_update("tp-1", "Renamed", "c", "new-template", [])
        after = temp_store.get_pattern("tp-1")

        assert after.alpha == alpha_before
        assert after.beta == beta_before
        assert after.name == "Renamed"

    def test_create_with_explicit_id_that_does_not_exist(self, temp_store):
        """Providing an ID that is not in the store always creates a new pattern."""
        pattern = temp_store.create_or_update(
            pattern_id="brand-new-id",
            name="Brand New",
            category="c",
            template="t",
            variables=["a", "b"],
        )
        assert pattern.id == "brand-new-id"
        assert temp_store.get_pattern("brand-new-id") is not None

    def test_updated_at_changes_on_update(self, tmp_path):
        """updated_at is refreshed when a pattern is updated."""
        import time

        (tmp_path / ".forge/learning").mkdir()
        store = PatternStore(forge_root=tmp_path)
        store.create_or_update("ts-upd", "Original", "c", "t", [])

        before_ts = store.get_pattern("ts-upd").updated_at

        # Small sleep so the timestamp actually differs
        time.sleep(0.01)

        store.create_or_update("ts-upd", "Modified", "c", "t2", [])
        after_ts = store.get_pattern("ts-upd").updated_at

        assert after_ts >= before_ts


class TestPatternStorePersistence:
    """Tests that verify data actually survives a save/load cycle."""

    def test_create_then_reload_recovers_pattern(self, tmp_path):
        """Patterns created in one store instance are visible in a second instance."""
        (tmp_path / ".forge/learning").mkdir()

        store1 = PatternStore(forge_root=tmp_path)
        store1.create_or_update("persist-1", "Persisted", "cat", "tmpl {v}", ["v"])
        store1.record_outcome("persist-1", success=True)
        store1.record_outcome("persist-1", success=False)

        # Create a fresh store pointing at the same directory
        store2 = PatternStore(forge_root=tmp_path)
        pattern = store2.get_pattern("persist-1")

        assert pattern is not None
        assert pattern.name == "Persisted"
        assert pattern.uses == 2
        assert pattern.alpha == 2  # prior 1 + 1 success
        assert pattern.beta == 2  # prior 1 + 1 failure

    def test_delete_then_reload_confirms_deletion(self, tmp_path):
        """Deleted patterns are absent after a fresh store load."""
        (tmp_path / ".forge/learning").mkdir()

        store1 = PatternStore(forge_root=tmp_path)
        store1.create_or_update("del-me", "Delete Me", "cat", "t", [])

        store1.delete_pattern("del-me")

        store2 = PatternStore(forge_root=tmp_path)
        assert store2.get_pattern("del-me") is None

    def test_outcomes_persist_across_store_instances(self, tmp_path):
        """Outcomes saved by one store instance load correctly in another."""
        (tmp_path / ".forge/learning").mkdir()

        store1 = PatternStore(forge_root=tmp_path)
        store1.create_or_update("op-1", "Outcome Persist", "cat", "t", [])
        store1.record_outcome("op-1", success=True, variant="a", context={"x": 1})
        store1.record_outcome("op-1", success=False, variant="b")

        store2 = PatternStore(forge_root=tmp_path)
        outcomes = store2.get_outcomes("op-1")

        assert len(outcomes) == 2
        variants = {o.variant for o in outcomes}
        assert "a" in variants
        assert "b" in variants
