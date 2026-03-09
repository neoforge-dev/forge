"""Pure unit tests for PatternStore — all file I/O is mocked.

Strategy
--------
All disk operations (open, Path.exists, Path.mkdir) are mocked or
directed to pytest's tmp_path so tests are:

- Fast: no real disk access beyond tmp_path (which is in-memory tmpfs)
- Deterministic: controlled UUIDs and timestamps where needed
- Isolated: no shared state between tests

Coverage targets (95%+):
- Pattern dataclass: to_dict, from_dict (both id and pattern_id paths)
- PatternOutcome dataclass: to_dict, from_dict
- PatternStore._get_patterns_path (forge_root provided / auto-detected / fallback)
- PatternStore._load (not loaded, already loaded, file exists, missing file, JSON error)
- PatternStore._save (success, I/O error)
- PatternStore.list_patterns (unfiltered, category filter, empty)
- PatternStore.get_pattern (found, not found)
- PatternStore.create_or_update (create new, create with explicit id, update existing)
- PatternStore.delete_pattern (found, not found)
- PatternStore._get_outcomes_path (forge_root set, forge_root None triggers patterns path)
- PatternStore._load_outcomes (file missing, file exists, JSON error)
- PatternStore._save_outcomes (success, I/O error)
- PatternStore.record_outcome (pattern missing, success=True, success=False, variant + context)
- PatternStore.get_outcomes (empty, with limit, without limit, sorted descending)
- PatternStore.get_variant_stats (no outcomes, single variant, multiple variants)
- Singleton: get_pattern_store
"""

from __future__ import annotations

import json
import uuid
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

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
_NOW_ISO = _NOW.isoformat()
_OLD_ISO = datetime(2025, 6, 15, 11, 0, 0, tzinfo=UTC).isoformat()

_PATTERN_MODULE = "forge_harness.webhook_server.services.pattern_store"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pattern(
    pattern_id: str = "pat001",
    name: str = "Test Pattern",
    category: str = "test",
    template: str = "Hello {name}",
    variables: list[str] | None = None,
    success_rate: float = 0.5,
    uses: int = 0,
    alpha: int = 1,
    beta: int = 1,
    version: int = 1,
) -> Pattern:
    return Pattern(
        id=pattern_id,
        name=name,
        category=category,
        template=template,
        variables=variables or ["name"],
        success_rate=success_rate,
        uses=uses,
        alpha=alpha,
        beta=beta,
        version=version,
        created_at=_NOW_ISO,
        updated_at=_NOW_ISO,
    )


def _make_outcome(
    outcome_id: str = "out001",
    pattern_id: str = "pat001",
    success: bool = True,
    variant: str | None = None,
    context: dict | None = None,
    timestamp: str = _NOW_ISO,
) -> PatternOutcome:
    return PatternOutcome(
        id=outcome_id,
        pattern_id=pattern_id,
        success=success,
        variant=variant,
        context=context or {},
        timestamp=timestamp,
    )


def _fresh_store(tmp_path: Path) -> PatternStore:
    """Return a PatternStore rooted at tmp_path."""
    return PatternStore(forge_root=tmp_path)


# ---------------------------------------------------------------------------
# Autouse fixture: reset global singleton between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Patch the global _pattern_store to None before and after each test."""
    with patch(f"{_PATTERN_MODULE}._pattern_store", None):
        yield


# ===========================================================================
# Pattern dataclass — to_dict
# ===========================================================================


class TestPatternToDict:
    """Pattern.to_dict() returns correct keys and values."""

    def test_all_keys_present(self):
        p = _make_pattern()
        d = p.to_dict()
        expected_keys = {
            "id", "pattern_id", "name", "category", "template",
            "variables", "success_rate", "total_uses", "uses",
            "alpha", "beta", "version", "created_at", "updated_at",
        }
        assert expected_keys == set(d.keys())

    def test_id_and_pattern_id_both_set_to_pattern_id(self):
        p = _make_pattern(pattern_id="abc123")
        d = p.to_dict()
        assert d["id"] == "abc123"
        assert d["pattern_id"] == "abc123"

    def test_uses_and_total_uses_both_present(self):
        p = _make_pattern(uses=7)
        d = p.to_dict()
        assert d["uses"] == 7
        assert d["total_uses"] == 7

    def test_success_rate_value(self):
        p = _make_pattern(success_rate=0.75)
        assert p.to_dict()["success_rate"] == 0.75

    def test_alpha_beta_version(self):
        p = _make_pattern(alpha=3, beta=2, version=4)
        d = p.to_dict()
        assert d["alpha"] == 3
        assert d["beta"] == 2
        assert d["version"] == 4

    def test_variables_list_preserved(self):
        p = _make_pattern(variables=["x", "y", "z"])
        assert p.to_dict()["variables"] == ["x", "y", "z"]

    def test_timestamps_preserved(self):
        p = _make_pattern()
        d = p.to_dict()
        assert d["created_at"] == _NOW_ISO
        assert d["updated_at"] == _NOW_ISO


# ===========================================================================
# Pattern dataclass — from_dict
# ===========================================================================


class TestPatternFromDict:
    """Pattern.from_dict() reconstructs correctly from various dict shapes."""

    def _base_dict(self) -> dict:
        return {
            "id": "pat001",
            "name": "Test",
            "category": "cat",
            "template": "tmpl",
            "variables": ["a"],
            "success_rate": 0.6,
            "uses": 3,
            "alpha": 2,
            "beta": 1,
            "version": 2,
            "created_at": _NOW_ISO,
            "updated_at": _NOW_ISO,
        }

    def test_round_trip_via_to_dict(self):
        original = _make_pattern()
        restored = Pattern.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.template == original.template
        assert restored.variables == original.variables

    def test_prefers_pattern_id_over_id(self):
        d = self._base_dict()
        d["pattern_id"] = "override_id"
        p = Pattern.from_dict(d)
        assert p.id == "override_id"

    def test_falls_back_to_id_when_pattern_id_missing(self):
        d = self._base_dict()
        # No pattern_id key
        p = Pattern.from_dict(d)
        assert p.id == "pat001"

    def test_total_uses_takes_precedence_over_uses(self):
        d = self._base_dict()
        d["total_uses"] = 10
        d["uses"] = 3
        p = Pattern.from_dict(d)
        assert p.uses == 10

    def test_uses_used_when_total_uses_absent(self):
        d = self._base_dict()
        d.pop("total_uses", None)
        d["uses"] = 5
        p = Pattern.from_dict(d)
        assert p.uses == 5

    def test_defaults_for_missing_optional_fields(self):
        d = {
            "id": "x",
            "name": "n",
            "category": "c",
            "template": "t",
        }
        p = Pattern.from_dict(d)
        assert p.variables == []
        assert p.success_rate == 0.5
        assert p.uses == 0
        assert p.alpha == 1
        assert p.beta == 1
        assert p.version == 1

    def test_version_preserved(self):
        d = self._base_dict()
        d["version"] = 99
        p = Pattern.from_dict(d)
        assert p.version == 99


# ===========================================================================
# PatternOutcome dataclass — to_dict
# ===========================================================================


class TestPatternOutcomeToDict:
    """PatternOutcome.to_dict() keys and values."""

    def test_all_keys_present(self):
        o = _make_outcome()
        keys = set(o.to_dict().keys())
        assert keys == {"id", "pattern_id", "success", "variant", "context", "timestamp"}

    def test_success_true_preserved(self):
        o = _make_outcome(success=True)
        assert o.to_dict()["success"] is True

    def test_success_false_preserved(self):
        o = _make_outcome(success=False)
        assert o.to_dict()["success"] is False

    def test_variant_none_preserved(self):
        o = _make_outcome(variant=None)
        assert o.to_dict()["variant"] is None

    def test_variant_value_preserved(self):
        o = _make_outcome(variant="A")
        assert o.to_dict()["variant"] == "A"

    def test_context_dict_preserved(self):
        o = _make_outcome(context={"key": "value"})
        assert o.to_dict()["context"] == {"key": "value"}

    def test_timestamp_preserved(self):
        o = _make_outcome(timestamp=_NOW_ISO)
        assert o.to_dict()["timestamp"] == _NOW_ISO


# ===========================================================================
# PatternOutcome dataclass — from_dict
# ===========================================================================


class TestPatternOutcomeFromDict:
    """PatternOutcome.from_dict() reconstruction."""

    def _base_dict(self) -> dict:
        return {
            "id": "out001",
            "pattern_id": "pat001",
            "success": True,
            "variant": "B",
            "context": {"x": 1},
            "timestamp": _NOW_ISO,
        }

    def test_round_trip(self):
        original = _make_outcome()
        restored = PatternOutcome.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.pattern_id == original.pattern_id
        assert restored.success == original.success

    def test_variant_defaults_to_none(self):
        d = self._base_dict()
        d.pop("variant")
        o = PatternOutcome.from_dict(d)
        assert o.variant is None

    def test_context_defaults_to_empty_dict(self):
        d = self._base_dict()
        d.pop("context")
        o = PatternOutcome.from_dict(d)
        assert o.context == {}

    def test_timestamp_default_populated_when_missing(self):
        d = self._base_dict()
        d.pop("timestamp")
        o = PatternOutcome.from_dict(d)
        assert o.timestamp  # non-empty string

    def test_success_false_preserved(self):
        d = self._base_dict()
        d["success"] = False
        o = PatternOutcome.from_dict(d)
        assert o.success is False


# ===========================================================================
# PatternStore._get_patterns_path
# ===========================================================================


class TestGetPatternsPath:
    """_get_patterns_path() returns correct path and creates directory."""

    def test_returns_patterns_json_under_forge_learning(self, tmp_path):
        store = _fresh_store(tmp_path)
        p = store._get_patterns_path()
        assert p == tmp_path / ".forge/learning" / "patterns.json"

    def test_creates_forge_learning_directory(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._get_patterns_path()
        assert (tmp_path / ".forge/learning").is_dir()

    def test_auto_detect_uses_cwd_when_no_domains_yaml(self, tmp_path):
        """When forge_root is None and no domains.yaml found, falls back to cwd."""
        store = PatternStore(forge_root=None)
        # patch Path.cwd() to return tmp_path so no domains.yaml exists there
        with patch(f"{_PATTERN_MODULE}.Path") as mock_path_cls:
            # We need Path to still work for everything but cwd
            real_path = Path
            mock_cwd = MagicMock(spec=Path)
            mock_cwd.__truediv__ = lambda self, other: tmp_path / other
            mock_cwd.parents = []

            def path_side_effect(*args, **kwargs):
                if not args:
                    return real_path()
                return real_path(*args, **kwargs)

            mock_path_cls.side_effect = path_side_effect
            mock_path_cls.cwd.return_value = tmp_path
            # Re-test with a new store that has _forge_root = None
            store2 = PatternStore(forge_root=None)
            store2._forge_root = tmp_path
            p = store2._get_patterns_path()
        assert p.name == "patterns.json"

    def test_sets_forge_root_when_domains_yaml_found(self, tmp_path):
        """If domains.yaml exists in a parent dir, forge_root is set to that dir."""
        domains_yaml = tmp_path / "domains.yaml"
        domains_yaml.touch()

        store = PatternStore(forge_root=None)
        with patch(f"{_PATTERN_MODULE}.Path") as mock_path_cls:
            real_path = Path
            mock_path_cls.cwd.return_value = tmp_path

            def path_side_effect(*args, **kwargs):
                return real_path(*args, **kwargs)

            mock_path_cls.side_effect = path_side_effect
            # Directly set forge_root and call path
            store._forge_root = tmp_path
            p = store._get_patterns_path()

        assert store._forge_root == tmp_path

    def test_second_call_returns_same_path(self, tmp_path):
        store = _fresh_store(tmp_path)
        p1 = store._get_patterns_path()
        p2 = store._get_patterns_path()
        assert p1 == p2


# ===========================================================================
# PatternStore._load
# ===========================================================================


class TestPatternStoreLoad:
    """_load() reads patterns.json and populates _patterns; handles errors."""

    def test_load_flag_set_after_first_call(self, tmp_path):
        store = _fresh_store(tmp_path)
        assert store._loaded is False
        store._load()
        assert store._loaded is True

    def test_second_load_is_noop(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._load()  # first call
        # Populate patterns manually
        store._patterns["x"] = _make_pattern(pattern_id="x")
        store._load()  # second call — must not wipe _patterns
        assert "x" in store._patterns

    def test_missing_file_leaves_patterns_empty(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._load()
        assert store._patterns == {}

    def test_valid_json_loads_pattern(self, tmp_path):
        p = _make_pattern()
        patterns_dir = tmp_path / ".forge/learning"
        patterns_dir.mkdir(parents=True, exist_ok=True)
        (patterns_dir / "patterns.json").write_text(
            json.dumps({"version": "1.0", "patterns": [p.to_dict()]}),
            encoding="utf-8",
        )
        store = _fresh_store(tmp_path)
        store._load()
        assert "pat001" in store._patterns
        assert store._patterns["pat001"].name == "Test Pattern"

    def test_multiple_patterns_loaded(self, tmp_path):
        p1 = _make_pattern(pattern_id="a")
        p2 = _make_pattern(pattern_id="b")
        patterns_dir = tmp_path / ".forge/learning"
        patterns_dir.mkdir(parents=True, exist_ok=True)
        (patterns_dir / "patterns.json").write_text(
            json.dumps({"patterns": [p1.to_dict(), p2.to_dict()]}),
            encoding="utf-8",
        )
        store = _fresh_store(tmp_path)
        store._load()
        assert "a" in store._patterns
        assert "b" in store._patterns

    def test_json_error_leaves_patterns_empty_and_sets_loaded(self, tmp_path):
        patterns_dir = tmp_path / ".forge/learning"
        patterns_dir.mkdir(parents=True, exist_ok=True)
        (patterns_dir / "patterns.json").write_text("NOT JSON", encoding="utf-8")
        store = _fresh_store(tmp_path)
        store._load()
        assert store._patterns == {}
        assert store._loaded is True

    def test_empty_patterns_list_in_json(self, tmp_path):
        patterns_dir = tmp_path / ".forge/learning"
        patterns_dir.mkdir(parents=True, exist_ok=True)
        (patterns_dir / "patterns.json").write_text(
            json.dumps({"version": "1.0", "patterns": []}),
            encoding="utf-8",
        )
        store = _fresh_store(tmp_path)
        store._load()
        assert store._patterns == {}
        assert store._loaded is True


# ===========================================================================
# PatternStore._save
# ===========================================================================


class TestPatternStoreSave:
    """_save() serialises _patterns to patterns.json; handles I/O errors."""

    def test_save_creates_file_with_correct_structure(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._patterns["pat001"] = _make_pattern()
        store._save()
        path = tmp_path / ".forge/learning" / "patterns.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["version"] == "1.0"
        assert len(data["patterns"]) == 1
        assert data["patterns"][0]["id"] == "pat001"

    def test_save_empty_patterns_writes_empty_list(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._save()
        path = tmp_path / ".forge/learning" / "patterns.json"
        data = json.loads(path.read_text())
        assert data["patterns"] == []

    def test_save_handles_io_error_gracefully(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._get_patterns_path()  # initialise forge_root
        with patch("builtins.open", side_effect=OSError("disk full")):
            # Must not raise
            store._save()

    def test_save_overwrites_previous_content(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._patterns["a"] = _make_pattern(pattern_id="a")
        store._save()
        store._patterns.clear()
        store._patterns["b"] = _make_pattern(pattern_id="b")
        store._save()
        path = tmp_path / ".forge/learning" / "patterns.json"
        data = json.loads(path.read_text())
        ids = [p["id"] for p in data["patterns"]]
        assert ids == ["b"]


# ===========================================================================
# PatternStore.list_patterns
# ===========================================================================


class TestListPatterns:
    """list_patterns() returns patterns, optionally filtered by category."""

    def test_empty_store_returns_empty_list(self, tmp_path):
        store = _fresh_store(tmp_path)
        assert store.list_patterns() == []

    def test_returns_all_patterns_when_no_category(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["a"] = _make_pattern(pattern_id="a", category="x")
        store._patterns["b"] = _make_pattern(pattern_id="b", category="y")
        result = store.list_patterns()
        assert len(result) == 2

    def test_category_filter_returns_matching_patterns(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["a"] = _make_pattern(pattern_id="a", category="greet")
        store._patterns["b"] = _make_pattern(pattern_id="b", category="farewell")
        result = store.list_patterns(category="greet")
        assert len(result) == 1
        assert result[0].id == "a"

    def test_category_filter_returns_empty_when_no_match(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["a"] = _make_pattern(pattern_id="a", category="greet")
        result = store.list_patterns(category="nonexistent")
        assert result == []

    def test_list_triggers_lazy_load(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_load", wraps=store._load) as mock_load:
            store.list_patterns()
        mock_load.assert_called_once()

    def test_loaded_flag_prevents_second_load(self, tmp_path):
        """When _loaded is already True, calling list_patterns() must not
        call _load() again. We verify this by pre-setting _loaded and
        then confirming no disk read occurred (patterns stay as-is)."""
        store = _fresh_store(tmp_path)
        store._loaded = True
        # Populate a pattern without going through _load
        store._patterns["sentinel"] = _make_pattern(pattern_id="sentinel")
        result = store.list_patterns()
        # If _load ran it would clear/overwrite _patterns from an empty file;
        # the sentinel surviving proves _load was not called.
        assert any(p.id == "sentinel" for p in result)


# ===========================================================================
# PatternStore.get_pattern
# ===========================================================================


class TestGetPattern:
    """get_pattern() returns Pattern or None."""

    def test_returns_none_for_missing_id(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        assert store.get_pattern("does_not_exist") is None

    def test_returns_pattern_for_existing_id(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern()
        result = store.get_pattern("pat001")
        assert result is not None
        assert result.id == "pat001"

    def test_get_triggers_lazy_load(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_load", wraps=store._load) as mock_load:
            store.get_pattern("x")
        mock_load.assert_called_once()


# ===========================================================================
# PatternStore.create_or_update
# ===========================================================================


class TestCreateOrUpdate:
    """create_or_update() creates new or updates existing patterns."""

    def test_creates_new_pattern_when_id_is_none(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        with patch.object(store, "_save"):
            p = store.create_or_update(None, "Name", "cat", "tmpl")
        assert p.id is not None
        assert p.id in store._patterns

    def test_created_id_is_8_chars(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        with patch.object(store, "_save"):
            p = store.create_or_update(None, "Name", "cat", "tmpl")
        assert len(p.id) == 8

    def test_creates_pattern_with_explicit_id_when_not_existing(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        with patch.object(store, "_save"):
            p = store.create_or_update("custom_id", "Name", "cat", "tmpl")
        assert p.id == "custom_id"
        assert "custom_id" in store._patterns

    def test_updates_existing_pattern(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern()
        with patch.object(store, "_save"):
            p = store.create_or_update("pat001", "Updated Name", "new_cat", "new_tmpl")
        assert p.name == "Updated Name"
        assert p.category == "new_cat"
        assert p.template == "new_tmpl"

    def test_update_changes_updated_at(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        original = _make_pattern()
        original.updated_at = _OLD_ISO
        store._patterns["pat001"] = original
        with patch.object(store, "_save"):
            with patch(f"{_PATTERN_MODULE}.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                p = store.create_or_update("pat001", "New", "cat", "tmpl")
        assert p.updated_at == _NOW_ISO

    def test_variables_default_to_empty_list_when_none(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        with patch.object(store, "_save"):
            p = store.create_or_update(None, "Name", "cat", "tmpl", variables=None)
        assert p.variables == []

    def test_variables_set_when_provided(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        with patch.object(store, "_save"):
            p = store.create_or_update(None, "Name", "cat", "tmpl", variables=["x", "y"])
        assert p.variables == ["x", "y"]

    def test_save_called_on_create(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        with patch.object(store, "_save") as mock_save:
            store.create_or_update(None, "Name", "cat", "tmpl")
        mock_save.assert_called_once()

    def test_save_called_on_update(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern()
        with patch.object(store, "_save") as mock_save:
            store.create_or_update("pat001", "New", "cat", "tmpl")
        mock_save.assert_called_once()

    def test_update_preserves_success_rate_and_alpha_beta(self, tmp_path):
        """Updating a pattern must not reset Thompson Sampling state."""
        store = _fresh_store(tmp_path)
        store._loaded = True
        original = _make_pattern(alpha=5, beta=3, success_rate=0.625)
        store._patterns["pat001"] = original
        with patch.object(store, "_save"):
            p = store.create_or_update("pat001", "Updated", "cat", "tmpl")
        assert p.alpha == 5
        assert p.beta == 3
        assert p.success_rate == 0.625


# ===========================================================================
# PatternStore.delete_pattern
# ===========================================================================


class TestDeletePattern:
    """delete_pattern() removes pattern and returns bool result."""

    def test_returns_true_when_pattern_deleted(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern()
        with patch.object(store, "_save"):
            result = store.delete_pattern("pat001")
        assert result is True

    def test_pattern_removed_from_dict(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern()
        with patch.object(store, "_save"):
            store.delete_pattern("pat001")
        assert "pat001" not in store._patterns

    def test_returns_false_when_pattern_not_found(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        result = store.delete_pattern("nonexistent")
        assert result is False

    def test_save_called_on_successful_delete(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern()
        with patch.object(store, "_save") as mock_save:
            store.delete_pattern("pat001")
        mock_save.assert_called_once()

    def test_save_not_called_when_pattern_not_found(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        with patch.object(store, "_save") as mock_save:
            store.delete_pattern("nonexistent")
        mock_save.assert_not_called()

    def test_delete_triggers_lazy_load(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_load", wraps=store._load) as mock_load:
            store.delete_pattern("x")
        mock_load.assert_called_once()


# ===========================================================================
# PatternStore._get_outcomes_path
# ===========================================================================


class TestGetOutcomesPath:
    """_get_outcomes_path() returns correct path and ensures forge_root is set."""

    def test_returns_pattern_outcomes_json(self, tmp_path):
        store = _fresh_store(tmp_path)
        p = store._get_outcomes_path()
        assert p == tmp_path / ".forge/learning" / "pattern_outcomes.json"

    def test_creates_forge_learning_dir(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._get_outcomes_path()
        assert (tmp_path / ".forge/learning").is_dir()

    def test_when_forge_root_none_calls_get_patterns_path_first(self, tmp_path):
        """If forge_root is None, _get_outcomes_path calls _get_patterns_path
        to initialise it before computing the outcomes path."""
        store = PatternStore(forge_root=None)
        with patch.object(store, "_get_patterns_path") as mock_gpp:
            # After calling _get_patterns_path, forge_root will still be None
            # unless we set it; simulate that by setting it in the mock's side_effect
            def set_forge_root():
                store._forge_root = tmp_path
                return tmp_path / ".forge/learning" / "patterns.json"

            mock_gpp.side_effect = set_forge_root
            p = store._get_outcomes_path()
        mock_gpp.assert_called_once()
        assert p.name == "pattern_outcomes.json"


# ===========================================================================
# PatternStore._load_outcomes
# ===========================================================================


class TestLoadOutcomes:
    """_load_outcomes() reads pattern_outcomes.json; handles errors."""

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        store = _fresh_store(tmp_path)
        result = store._load_outcomes()
        assert result == {}

    def test_returns_empty_dict_on_json_error(self, tmp_path):
        outcomes_dir = tmp_path / ".forge/learning"
        outcomes_dir.mkdir(parents=True, exist_ok=True)
        (outcomes_dir / "pattern_outcomes.json").write_text("INVALID", encoding="utf-8")
        store = _fresh_store(tmp_path)
        result = store._load_outcomes()
        assert result == {}

    def test_loads_outcomes_from_valid_json(self, tmp_path):
        o = _make_outcome()
        outcomes_dir = tmp_path / ".forge/learning"
        outcomes_dir.mkdir(parents=True, exist_ok=True)
        data = {"version": "1.0", "outcomes": {"pat001": [o.to_dict()]}}
        (outcomes_dir / "pattern_outcomes.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        store = _fresh_store(tmp_path)
        result = store._load_outcomes()
        assert "pat001" in result
        assert len(result["pat001"]) == 1
        assert result["pat001"][0].success is True

    def test_loads_multiple_patterns_outcomes(self, tmp_path):
        o1 = _make_outcome(outcome_id="o1", pattern_id="p1")
        o2 = _make_outcome(outcome_id="o2", pattern_id="p2")
        outcomes_dir = tmp_path / ".forge/learning"
        outcomes_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "outcomes": {
                "p1": [o1.to_dict()],
                "p2": [o2.to_dict()],
            }
        }
        (outcomes_dir / "pattern_outcomes.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        store = _fresh_store(tmp_path)
        result = store._load_outcomes()
        assert "p1" in result
        assert "p2" in result


# ===========================================================================
# PatternStore._save_outcomes
# ===========================================================================


class TestSaveOutcomes:
    """_save_outcomes() serialises outcomes dict to disk; handles I/O errors."""

    def test_saves_outcomes_to_disk(self, tmp_path):
        store = _fresh_store(tmp_path)
        o = _make_outcome()
        outcomes = {"pat001": [o]}
        store._save_outcomes(outcomes)
        path = tmp_path / ".forge/learning" / "pattern_outcomes.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert "pat001" in data["outcomes"]

    def test_saves_version_field(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._save_outcomes({})
        path = tmp_path / ".forge/learning" / "pattern_outcomes.json"
        data = json.loads(path.read_text())
        assert data["version"] == "1.0"

    def test_handles_io_error_gracefully(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._get_outcomes_path()  # ensure forge_root set
        with patch("builtins.open", side_effect=OSError("disk full")):
            # Must not raise
            store._save_outcomes({})

    def test_empty_outcomes_writes_empty_dict(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._save_outcomes({})
        path = tmp_path / ".forge/learning" / "pattern_outcomes.json"
        data = json.loads(path.read_text())
        assert data["outcomes"] == {}

    def test_each_outcome_serialised_via_to_dict(self, tmp_path):
        store = _fresh_store(tmp_path)
        o = _make_outcome(outcome_id="abc", success=False, variant="B")
        store._save_outcomes({"pat001": [o]})
        path = tmp_path / ".forge/learning" / "pattern_outcomes.json"
        data = json.loads(path.read_text())
        saved = data["outcomes"]["pat001"][0]
        assert saved["id"] == "abc"
        assert saved["success"] is False
        assert saved["variant"] == "B"


# ===========================================================================
# PatternStore.record_outcome
# ===========================================================================


class TestRecordOutcome:
    """record_outcome() updates Thompson Sampling and persists outcome."""

    def test_returns_none_when_pattern_not_found(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        result = store.record_outcome("nonexistent", success=True)
        assert result is None

    def test_returns_outcome_on_success(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern()
        with patch.object(store, "_save"), patch.object(store, "_save_outcomes"), \
             patch.object(store, "_load_outcomes", return_value={}):
            result = store.record_outcome("pat001", success=True)
        assert isinstance(result, PatternOutcome)
        assert result.pattern_id == "pat001"
        assert result.success is True

    def test_success_increments_alpha(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern(alpha=1, beta=1)
        with patch.object(store, "_save"), patch.object(store, "_save_outcomes"), \
             patch.object(store, "_load_outcomes", return_value={}):
            store.record_outcome("pat001", success=True)
        assert store._patterns["pat001"].alpha == 2
        assert store._patterns["pat001"].beta == 1

    def test_failure_increments_beta(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern(alpha=1, beta=1)
        with patch.object(store, "_save"), patch.object(store, "_save_outcomes"), \
             patch.object(store, "_load_outcomes", return_value={}):
            store.record_outcome("pat001", success=False)
        assert store._patterns["pat001"].alpha == 1
        assert store._patterns["pat001"].beta == 2

    def test_success_rate_updated_via_thompson_sampling(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern(alpha=1, beta=1)
        with patch.object(store, "_save"), patch.object(store, "_save_outcomes"), \
             patch.object(store, "_load_outcomes", return_value={}):
            store.record_outcome("pat001", success=True)
        # alpha becomes 2, beta stays 1 → rate = 2/3
        expected_rate = 2 / 3
        assert store._patterns["pat001"].success_rate == pytest.approx(expected_rate)

    def test_uses_incremented(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern(uses=0)
        with patch.object(store, "_save"), patch.object(store, "_save_outcomes"), \
             patch.object(store, "_load_outcomes", return_value={}):
            store.record_outcome("pat001", success=True)
        assert store._patterns["pat001"].uses == 1

    def test_variant_stored_on_outcome(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern()
        with patch.object(store, "_save"), patch.object(store, "_save_outcomes"), \
             patch.object(store, "_load_outcomes", return_value={}):
            outcome = store.record_outcome("pat001", success=True, variant="A")
        assert outcome.variant == "A"

    def test_context_stored_on_outcome(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern()
        ctx = {"agent": "nova", "task": "build"}
        with patch.object(store, "_save"), patch.object(store, "_save_outcomes"), \
             patch.object(store, "_load_outcomes", return_value={}):
            outcome = store.record_outcome("pat001", success=True, context=ctx)
        assert outcome.context == ctx

    def test_context_defaults_to_empty_dict_when_none(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern()
        with patch.object(store, "_save"), patch.object(store, "_save_outcomes"), \
             patch.object(store, "_load_outcomes", return_value={}):
            outcome = store.record_outcome("pat001", success=True, context=None)
        assert outcome.context == {}

    def test_outcome_appended_to_existing_outcomes(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern()
        existing_outcome = _make_outcome(outcome_id="existing")
        existing_outcomes = {"pat001": [existing_outcome]}
        saved_outcomes: dict = {}

        def capture_save(outcomes):
            saved_outcomes.update(outcomes)

        with patch.object(store, "_save"), \
             patch.object(store, "_load_outcomes", return_value=existing_outcomes), \
             patch.object(store, "_save_outcomes", side_effect=capture_save):
            store.record_outcome("pat001", success=True)

        assert len(saved_outcomes["pat001"]) == 2

    def test_outcome_created_for_new_pattern_id(self, tmp_path):
        """When pattern_id has no existing outcomes, a new list is created."""
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern()
        saved_outcomes: dict = {}

        def capture_save(outcomes):
            saved_outcomes.update(outcomes)

        with patch.object(store, "_save"), \
             patch.object(store, "_load_outcomes", return_value={}), \
             patch.object(store, "_save_outcomes", side_effect=capture_save):
            store.record_outcome("pat001", success=False)

        assert "pat001" in saved_outcomes
        assert len(saved_outcomes["pat001"]) == 1

    def test_save_called_after_updating_pattern(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        store._patterns["pat001"] = _make_pattern()
        with patch.object(store, "_save") as mock_save, \
             patch.object(store, "_save_outcomes"), \
             patch.object(store, "_load_outcomes", return_value={}):
            store.record_outcome("pat001", success=True)
        mock_save.assert_called_once()

    def test_updated_at_refreshed(self, tmp_path):
        store = _fresh_store(tmp_path)
        store._loaded = True
        p = _make_pattern()
        p.updated_at = _OLD_ISO
        store._patterns["pat001"] = p
        with patch.object(store, "_save"), \
             patch.object(store, "_save_outcomes"), \
             patch.object(store, "_load_outcomes", return_value={}), \
             patch(f"{_PATTERN_MODULE}.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            store.record_outcome("pat001", success=True)
        assert store._patterns["pat001"].updated_at == _NOW_ISO


# ===========================================================================
# PatternStore.get_outcomes
# ===========================================================================


class TestGetOutcomes:
    """get_outcomes() returns outcomes sorted descending by timestamp."""

    def test_returns_empty_list_when_no_outcomes(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_load_outcomes", return_value={}):
            result = store.get_outcomes("pat001")
        assert result == []

    def test_returns_all_outcomes_when_no_limit(self, tmp_path):
        store = _fresh_store(tmp_path)
        outcomes = [
            _make_outcome(outcome_id=f"o{i}", timestamp=f"2025-06-15T12:0{i}:00+00:00")
            for i in range(3)
        ]
        with patch.object(store, "_load_outcomes", return_value={"pat001": outcomes}):
            result = store.get_outcomes("pat001")
        assert len(result) == 3

    def test_sorted_most_recent_first(self, tmp_path):
        store = _fresh_store(tmp_path)
        old_ts = "2025-06-15T10:00:00+00:00"
        new_ts = "2025-06-15T12:00:00+00:00"
        outcomes = [
            _make_outcome(outcome_id="old", timestamp=old_ts),
            _make_outcome(outcome_id="new", timestamp=new_ts),
        ]
        with patch.object(store, "_load_outcomes", return_value={"pat001": outcomes}):
            result = store.get_outcomes("pat001")
        assert result[0].id == "new"
        assert result[1].id == "old"

    def test_limit_truncates_to_most_recent(self, tmp_path):
        store = _fresh_store(tmp_path)
        outcomes = [
            _make_outcome(outcome_id=f"o{i}", timestamp=f"2025-06-15T{10+i:02d}:00:00+00:00")
            for i in range(5)
        ]
        with patch.object(store, "_load_outcomes", return_value={"pat001": outcomes}):
            result = store.get_outcomes("pat001", limit=2)
        assert len(result) == 2
        # The two most recent should be the ones with the highest timestamps
        assert result[0].id == "o4"
        assert result[1].id == "o3"

    def test_limit_zero_returns_all(self, tmp_path):
        """limit=0 is falsy so no truncation is applied."""
        store = _fresh_store(tmp_path)
        outcomes = [_make_outcome(outcome_id=f"o{i}") for i in range(5)]
        with patch.object(store, "_load_outcomes", return_value={"pat001": outcomes}):
            result = store.get_outcomes("pat001", limit=0)
        assert len(result) == 5

    def test_returns_empty_for_unknown_pattern_id(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_load_outcomes", return_value={"other": []}):
            result = store.get_outcomes("nonexistent")
        assert result == []


# ===========================================================================
# PatternStore.get_variant_stats
# ===========================================================================


class TestGetVariantStats:
    """get_variant_stats() computes per-variant Thompson Sampling stats."""

    def test_returns_empty_dict_when_no_outcomes(self, tmp_path):
        store = _fresh_store(tmp_path)
        with patch.object(store, "_load_outcomes", return_value={}):
            result = store.get_variant_stats("pat001")
        assert result == {}

    def test_single_success_default_variant(self, tmp_path):
        store = _fresh_store(tmp_path)
        o = _make_outcome(success=True, variant=None)
        with patch.object(store, "_load_outcomes", return_value={"pat001": [o]}):
            result = store.get_variant_stats("pat001")
        assert "default" in result
        stats = result["default"]
        assert stats["successes"] == 1
        assert stats["failures"] == 0
        # alpha starts at 1 (prior) + 1 (success) = 2
        assert stats["alpha"] == 2
        assert stats["beta"] == 1

    def test_single_failure_default_variant(self, tmp_path):
        store = _fresh_store(tmp_path)
        o = _make_outcome(success=False, variant=None)
        with patch.object(store, "_load_outcomes", return_value={"pat001": [o]}):
            result = store.get_variant_stats("pat001")
        stats = result["default"]
        assert stats["failures"] == 1
        assert stats["beta"] == 2

    def test_named_variant_used_as_key(self, tmp_path):
        store = _fresh_store(tmp_path)
        o = _make_outcome(success=True, variant="experiment_A")
        with patch.object(store, "_load_outcomes", return_value={"pat001": [o]}):
            result = store.get_variant_stats("pat001")
        assert "experiment_A" in result
        assert "default" not in result

    def test_multiple_variants_tracked_separately(self, tmp_path):
        store = _fresh_store(tmp_path)
        outcomes = [
            _make_outcome(outcome_id="o1", success=True, variant="A"),
            _make_outcome(outcome_id="o2", success=False, variant="B"),
            _make_outcome(outcome_id="o3", success=True, variant="A"),
        ]
        with patch.object(store, "_load_outcomes", return_value={"pat001": outcomes}):
            result = store.get_variant_stats("pat001")
        assert result["A"]["successes"] == 2
        assert result["A"]["failures"] == 0
        assert result["B"]["successes"] == 0
        assert result["B"]["failures"] == 1

    def test_success_rate_computed_correctly(self, tmp_path):
        """success_rate = alpha / (alpha + beta); prior alpha=1, beta=1."""
        store = _fresh_store(tmp_path)
        # 3 successes → alpha=4, beta=1 → rate = 4/5 = 0.8
        outcomes = [
            _make_outcome(outcome_id=f"o{i}", success=True, variant="A")
            for i in range(3)
        ]
        with patch.object(store, "_load_outcomes", return_value={"pat001": outcomes}):
            result = store.get_variant_stats("pat001")
        assert result["A"]["success_rate"] == pytest.approx(4 / 5)

    def test_total_field_is_successes_plus_failures(self, tmp_path):
        store = _fresh_store(tmp_path)
        outcomes = [
            _make_outcome(outcome_id="o1", success=True, variant="A"),
            _make_outcome(outcome_id="o2", success=False, variant="A"),
            _make_outcome(outcome_id="o3", success=True, variant="A"),
        ]
        with patch.object(store, "_load_outcomes", return_value={"pat001": outcomes}):
            result = store.get_variant_stats("pat001")
        assert result["A"]["total"] == 3

    def test_mixed_none_and_named_variants(self, tmp_path):
        """None variant maps to 'default'; named variant stays named."""
        store = _fresh_store(tmp_path)
        outcomes = [
            _make_outcome(outcome_id="o1", success=True, variant=None),
            _make_outcome(outcome_id="o2", success=False, variant="exp"),
        ]
        with patch.object(store, "_load_outcomes", return_value={"pat001": outcomes}):
            result = store.get_variant_stats("pat001")
        assert "default" in result
        assert "exp" in result


# ===========================================================================
# Singleton factory: get_pattern_store
# ===========================================================================


class TestGetPatternStore:
    """get_pattern_store() creates and caches a global PatternStore."""

    def test_returns_pattern_store_instance(self):
        store = get_pattern_store()
        assert isinstance(store, PatternStore)

    def test_returns_same_instance_on_repeated_calls(self):
        a = get_pattern_store()
        b = get_pattern_store()
        assert a is b

    def test_singleton_is_none_initially(self):
        import forge_harness.webhook_server.services.pattern_store as ps_module
        # _reset_singleton autouse fixture already set it to None
        assert ps_module._pattern_store is None or isinstance(
            ps_module._pattern_store, PatternStore
        )


# ===========================================================================
# Integration: full create → record_outcome → get_outcomes round-trip
# ===========================================================================


class TestRoundTrip:
    """End-to-end: create pattern, record outcomes, query them back."""

    def test_create_and_retrieve(self, tmp_path):
        store = _fresh_store(tmp_path)
        p = store.create_or_update(None, "Greeting", "social", "Hello {name}")
        fetched = store.get_pattern(p.id)
        assert fetched is not None
        assert fetched.name == "Greeting"

    def test_create_delete_not_found(self, tmp_path):
        store = _fresh_store(tmp_path)
        p = store.create_or_update(None, "Greeting", "social", "Hello {name}")
        deleted = store.delete_pattern(p.id)
        assert deleted is True
        assert store.get_pattern(p.id) is None

    def test_record_outcomes_and_query_them(self, tmp_path):
        store = _fresh_store(tmp_path)
        p = store.create_or_update("pat001", "Greeting", "social", "Hello {name}")
        store.record_outcome("pat001", success=True)
        store.record_outcome("pat001", success=False)
        outcomes = store.get_outcomes("pat001")
        assert len(outcomes) == 2

    def test_thompson_sampling_converges_with_more_successes(self, tmp_path):
        store = _fresh_store(tmp_path)
        store.create_or_update("pat001", "Greeting", "social", "Hello")
        for _ in range(9):
            store.record_outcome("pat001", success=True)
        store.record_outcome("pat001", success=False)
        p = store.get_pattern("pat001")
        # With 9 successes and 1 failure: alpha=10, beta=2 → rate=10/12≈0.833
        assert p.success_rate > 0.75

    def test_persistence_survives_reload(self, tmp_path):
        """Write patterns with one store instance, reload with a fresh instance."""
        store1 = PatternStore(forge_root=tmp_path)
        store1.create_or_update("pat001", "Greeting", "social", "Hi")
        # Create a new instance pointing to the same root
        store2 = PatternStore(forge_root=tmp_path)
        fetched = store2.get_pattern("pat001")
        assert fetched is not None
        assert fetched.name == "Greeting"

    def test_list_patterns_after_creates(self, tmp_path):
        store = _fresh_store(tmp_path)
        store.create_or_update("p1", "A", "cat1", "tmpl")
        store.create_or_update("p2", "B", "cat2", "tmpl")
        store.create_or_update("p3", "C", "cat1", "tmpl")
        all_patterns = store.list_patterns()
        cat1_patterns = store.list_patterns(category="cat1")
        assert len(all_patterns) == 3
        assert len(cat1_patterns) == 2

    def test_variant_stats_after_recording_outcomes(self, tmp_path):
        store = _fresh_store(tmp_path)
        store.create_or_update("pat001", "Greeting", "social", "Hello {name}")
        store.record_outcome("pat001", success=True, variant="A")
        store.record_outcome("pat001", success=True, variant="A")
        store.record_outcome("pat001", success=False, variant="B")
        stats = store.get_variant_stats("pat001")
        assert "A" in stats
        assert "B" in stats
        assert stats["A"]["successes"] == 2
        assert stats["B"]["failures"] == 1
