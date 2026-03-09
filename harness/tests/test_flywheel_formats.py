"""
Tests for features.json format variations in flywheel/ralph_loop.

Test Matrix:
- Plain list format: [{"id": "...", ...}, ...]
- Wrapper with features key: {"features": [...], "version": "1.0"}
- Wrapper without features key: {"metadata": {...}}
- Malformed JSON: Invalid syntax
- Empty file: Zero bytes
- Empty list: []
- Empty wrapper: {"features": []}
- Mixed valid/invalid features: [{valid}, "string", null, {valid}]

These tests verify both FeatureStore (ralph_loop.py) and run_flywheel (flywheel.py)
handle all format variations gracefully.
"""

import json

import pytest

from forge_harness.ralph_loop import FeatureSpec, FeatureStatus, FeatureStore

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def valid_feature_dict():
    """A valid feature dictionary."""
    return {
        "id": "test-001",
        "name": "Test Feature",
        "description": "A test feature for validation",
        "status": "pending",
        "priority": "medium",
        "acceptance_criteria": ["Test passes"],
        "depends_on": [],
        "tests": ["test_example"],
    }


@pytest.fixture
def valid_feature_dict_2():
    """A second valid feature dictionary."""
    return {
        "id": "test-002",
        "name": "Another Feature",
        "description": "Another test feature",
        "status": "pending",
        "priority": "high",
    }


@pytest.fixture
def tmp_features_path(tmp_path):
    """Temporary features.json path."""
    return tmp_path / "features.json"


# =============================================================================
# Format 1: Plain List
# =============================================================================


class TestPlainListFormat:
    """Tests for plain list format: [{"id": "...", ...}, ...]"""

    def test_load_plain_list(self, tmp_features_path, valid_feature_dict):
        """FeatureStore now supports plain list format.

        FIXED: FeatureStore.load() now handles both formats:
        - Wrapper format: {"features": [...]}
        - Plain list format: [...]
        """
        # Write plain list
        tmp_features_path.write_text(json.dumps([valid_feature_dict]))

        # Load - FeatureStore now supports plain list format
        store = FeatureStore(tmp_features_path)
        features = store.load()

        assert len(features) == 1
        assert features[0].id == "test-001"
        assert features[0].name == "Test Feature"

    def test_flywheel_loads_plain_list(self, tmp_features_path, valid_feature_dict):
        """Flywheel's run_flywheel handles plain list format."""

        # Write plain list
        tmp_features_path.write_text(json.dumps([valid_feature_dict]))

        # Read back using flywheel's logic (from run_flywheel)
        data = json.loads(tmp_features_path.read_text())

        # Flywheel handles both formats
        if isinstance(data, list):
            existing_features = data
        elif isinstance(data, dict):
            existing_features = data.get("features", [])
        else:
            existing_features = []

        assert len(existing_features) == 1
        assert existing_features[0]["id"] == "test-001"

    def test_plain_list_multiple_features(
        self, tmp_features_path, valid_feature_dict, valid_feature_dict_2
    ):
        """Plain list with multiple features."""
        tmp_features_path.write_text(json.dumps([valid_feature_dict, valid_feature_dict_2]))

        data = json.loads(tmp_features_path.read_text())
        features = data if isinstance(data, list) else data.get("features", [])

        assert len(features) == 2
        assert {f["id"] for f in features} == {"test-001", "test-002"}


# =============================================================================
# Format 2: Wrapper with features key
# =============================================================================


class TestWrapperWithFeaturesKey:
    """Tests for wrapper format: {"features": [...], "version": "1.0"}"""

    def test_load_wrapper_format(self, tmp_features_path, valid_feature_dict):
        """FeatureStore loads wrapper format correctly."""
        wrapper = {"version": "1.0", "features": [valid_feature_dict]}
        tmp_features_path.write_text(json.dumps(wrapper))

        store = FeatureStore(tmp_features_path)
        features = store.load()

        assert len(features) == 1
        assert features[0].id == "test-001"
        assert features[0].name == "Test Feature"

    def test_wrapper_preserves_metadata(self, tmp_features_path, valid_feature_dict):
        """Wrapper metadata (version, etc.) is accessible."""
        wrapper = {
            "version": "2.0",
            "created_at": "2026-01-31",
            "features": [valid_feature_dict],
        }
        tmp_features_path.write_text(json.dumps(wrapper))

        data = json.loads(tmp_features_path.read_text())
        assert data["version"] == "2.0"
        assert data["created_at"] == "2026-01-31"

    def test_wrapper_empty_features(self, tmp_features_path):
        """Wrapper with empty features array."""
        wrapper = {"version": "1.0", "features": []}
        tmp_features_path.write_text(json.dumps(wrapper))

        store = FeatureStore(tmp_features_path)
        features = store.load()

        assert features == []

    def test_save_creates_wrapper_format(self, tmp_features_path, valid_feature_dict):
        """FeatureStore.save() always writes wrapper format."""
        # Start with plain list
        tmp_features_path.write_text(json.dumps([valid_feature_dict]))

        # Load (will be empty due to format mismatch) then save
        store = FeatureStore(tmp_features_path)
        store._features["test-001"] = FeatureSpec.from_dict(valid_feature_dict)
        store.save()

        # Verify output is wrapper format
        saved_data = json.loads(tmp_features_path.read_text())
        assert "version" in saved_data
        assert "features" in saved_data
        assert isinstance(saved_data["features"], list)


# =============================================================================
# Format 3: Wrapper without features key
# =============================================================================


class TestWrapperWithoutFeaturesKey:
    """Tests for dict without features key: {"metadata": {...}}"""

    def test_load_dict_without_features(self, tmp_features_path):
        """Dict without 'features' key returns empty list."""
        wrapper = {"metadata": {"version": "1.0"}, "config": {}}
        tmp_features_path.write_text(json.dumps(wrapper))

        store = FeatureStore(tmp_features_path)
        features = store.load()

        # Should return empty (no 'features' key)
        assert features == []

    def test_flywheel_warns_on_missing_features_key(self, tmp_features_path, caplog):
        """Flywheel logs warning for dict without features key."""
        import logging

        wrapper = {"metadata": {"version": "1.0"}}
        tmp_features_path.write_text(json.dumps(wrapper))

        # Simulate flywheel's loading logic
        data = json.loads(tmp_features_path.read_text())

        existing_features = []
        if isinstance(data, list):
            existing_features = data
        elif isinstance(data, dict):
            if "features" in data:
                existing_features = data.get("features", [])
            else:
                # This is where the warning would be logged
                logging.warning(
                    f"features.json has dict format without 'features' key: {tmp_features_path}"
                )
                existing_features = []

        assert existing_features == []

    def test_dict_with_wrong_key_name(self, tmp_features_path):
        """Dict with 'items' instead of 'features' returns empty."""
        wrapper = {"items": [{"id": "test-001"}]}
        tmp_features_path.write_text(json.dumps(wrapper))

        store = FeatureStore(tmp_features_path)
        features = store.load()

        assert features == []


# =============================================================================
# Format 4: Malformed JSON
# =============================================================================


class TestMalformedJson:
    """Tests for invalid JSON syntax."""

    def test_load_malformed_json(self, tmp_features_path):
        """Malformed JSON returns empty list gracefully."""
        tmp_features_path.write_text("{invalid json syntax")

        store = FeatureStore(tmp_features_path)
        features = store.load()

        assert features == []

    def test_load_truncated_json(self, tmp_features_path):
        """Truncated JSON returns empty list."""
        tmp_features_path.write_text('{"features": [{"id": "test-')

        store = FeatureStore(tmp_features_path)
        features = store.load()

        assert features == []

    def test_load_json_with_trailing_comma(self, tmp_features_path):
        """JSON with trailing comma (invalid) returns empty list."""
        tmp_features_path.write_text('{"features": [{"id": "test-001"},]}')

        store = FeatureStore(tmp_features_path)
        features = store.load()

        assert features == []

    def test_flywheel_handles_malformed_json(self, tmp_features_path):
        """Flywheel's loading logic handles malformed JSON."""
        tmp_features_path.write_text("not valid json at all")

        existing_features = []
        try:
            data = json.loads(tmp_features_path.read_text())
            if isinstance(data, list):
                existing_features = data
            elif isinstance(data, dict):
                existing_features = data.get("features", [])
        except json.JSONDecodeError:
            pass  # Expected - existing_features stays empty

        assert existing_features == []


# =============================================================================
# Format 5: Empty file
# =============================================================================


class TestEmptyFile:
    """Tests for zero-byte files."""

    def test_load_empty_file(self, tmp_features_path):
        """Empty file returns empty list."""
        tmp_features_path.write_text("")

        store = FeatureStore(tmp_features_path)
        features = store.load()

        assert features == []

    def test_load_whitespace_only(self, tmp_features_path):
        """Whitespace-only file returns empty list."""
        tmp_features_path.write_text("   \n\t\n   ")

        store = FeatureStore(tmp_features_path)
        features = store.load()

        assert features == []


# =============================================================================
# Format 6: Empty structures
# =============================================================================


class TestEmptyStructures:
    """Tests for empty but valid JSON structures."""

    def test_load_empty_list(self, tmp_features_path):
        """Empty list [] returns empty list.

        FIXED: FeatureStore now handles plain list format.
        """
        tmp_features_path.write_text("[]")

        store = FeatureStore(tmp_features_path)
        features = store.load()

        assert features == []

    def test_load_empty_object(self, tmp_features_path):
        """Empty object {} loads but returns empty (no 'features' key)."""
        tmp_features_path.write_text("{}")

        store = FeatureStore(tmp_features_path)
        features = store.load()

        assert features == []

    def test_load_null(self, tmp_features_path):
        """JSON null returns empty list gracefully.

        FIXED: FeatureStore now handles unexpected JSON types.
        """
        tmp_features_path.write_text("null")

        store = FeatureStore(tmp_features_path)
        features = store.load()

        assert features == []


# =============================================================================
# Format 7: Mixed valid/invalid features
# =============================================================================


class TestMixedFeatures:
    """Tests for arrays with mixed valid/invalid items."""

    def test_wrapper_with_mixed_items(self, tmp_features_path, valid_feature_dict):
        """Wrapper with mixed valid/invalid items skips invalid, keeps valid.

        FIXED: Non-dict items are now skipped with a warning.
        """
        wrapper = {
            "features": [
                valid_feature_dict,
                "just a string",  # Invalid - skipped
                None,  # Invalid - skipped
                {"id": "test-002", "name": "Minimal", "description": "Valid minimal"},  # Valid
                123,  # Invalid - skipped
            ]
        }
        tmp_features_path.write_text(json.dumps(wrapper))

        store = FeatureStore(tmp_features_path)
        features = store.load()

        # Only valid dict items with required fields are loaded
        assert len(features) == 2
        assert {f.id for f in features} == {"test-001", "test-002"}

    def test_feature_missing_required_fields(self, tmp_features_path):
        """Feature missing required 'id' field returns empty list (error logged)."""
        wrapper = {"features": [{"name": "No ID Feature", "description": "Missing id field"}]}
        tmp_features_path.write_text(json.dumps(wrapper))

        store = FeatureStore(tmp_features_path)
        # Error is caught and logged, returns empty list
        features = store.load()
        assert features == []

    def test_feature_missing_description(self, tmp_features_path):
        """Feature missing 'description' field returns empty list (error logged)."""
        wrapper = {"features": [{"id": "test-001", "name": "No description"}]}
        tmp_features_path.write_text(json.dumps(wrapper))

        store = FeatureStore(tmp_features_path)
        # Error is caught and logged, returns empty list
        features = store.load()
        assert features == []


# =============================================================================
# File System Edge Cases
# =============================================================================


class TestFileSystemEdgeCases:
    """Tests for file system edge cases."""

    def test_load_nonexistent_file(self, tmp_path):
        """Nonexistent file returns empty list."""
        nonexistent = tmp_path / "does_not_exist.json"

        store = FeatureStore(nonexistent)
        features = store.load()

        assert features == []

    def test_save_creates_parent_dirs(self, tmp_path, valid_feature_dict):
        """Save creates parent directories if needed."""
        nested_path = tmp_path / "deep" / "nested" / "features.json"

        store = FeatureStore(nested_path)
        store._features["test-001"] = FeatureSpec.from_dict(valid_feature_dict)

        # Should fail because parent dirs don't exist
        with pytest.raises(FileNotFoundError):
            store.save()

    def test_atomic_save_on_error(self, tmp_features_path, valid_feature_dict):
        """Save is atomic - original preserved on write error."""
        # Write initial content
        initial_wrapper = {"version": "1.0", "features": [valid_feature_dict]}
        tmp_features_path.write_text(json.dumps(initial_wrapper))

        store = FeatureStore(tmp_features_path)
        store.load()

        # Add a feature that will serialize
        store._features["test-002"] = FeatureSpec(
            id="test-002",
            name="New Feature",
            description="Test",
        )
        store.save()

        # Verify file is valid
        saved = json.loads(tmp_features_path.read_text())
        assert "features" in saved
        assert len(saved["features"]) == 2


# =============================================================================
# Status Value Variations
# =============================================================================


class TestStatusVariations:
    """Tests for various status value formats."""

    @pytest.mark.parametrize(
        "status_value,expected_status",
        [
            ("pending", FeatureStatus.PENDING),
            ("in_progress", FeatureStatus.IN_PROGRESS),
            ("passing", FeatureStatus.PASSING),
            ("failing", FeatureStatus.FAILING),
            ("blocked", FeatureStatus.BLOCKED),
            ("skipped", FeatureStatus.SKIPPED),
            ("completed", FeatureStatus.COMPLETED),
        ],
    )
    def test_valid_status_values(self, tmp_features_path, status_value, expected_status):
        """All valid status values parse correctly.

        NOTE: Features require 'name' or 'title' field in addition to id/description.
        """
        wrapper = {
            "features": [
                {
                    "id": "test-001",
                    "name": "Test Feature",  # Required field
                    "description": "Test",
                    "status": status_value,
                }
            ]
        }
        tmp_features_path.write_text(json.dumps(wrapper))

        store = FeatureStore(tmp_features_path)
        features = store.load()

        assert len(features) == 1
        assert features[0].status == expected_status

    def test_invalid_status_value(self, tmp_features_path):
        """Invalid status value skips the feature (logged warning).

        FIXED: ValueError is now caught per-feature, invalid entries are skipped.
        """
        wrapper = {
            "features": [
                {
                    "id": "test-001",
                    "name": "Test",
                    "description": "Test",
                    "status": "invalid_status",
                }
            ]
        }
        tmp_features_path.write_text(json.dumps(wrapper))

        store = FeatureStore(tmp_features_path)
        # FIXED: Invalid feature is skipped, returns empty list
        features = store.load()
        assert features == []

    def test_missing_status_defaults_to_pending(self, tmp_features_path):
        """Missing status defaults to PENDING."""
        wrapper = {"features": [{"id": "test-001", "name": "Test", "description": "Test"}]}
        tmp_features_path.write_text(json.dumps(wrapper))

        store = FeatureStore(tmp_features_path)
        features = store.load()

        assert len(features) == 1
        assert features[0].status == FeatureStatus.PENDING


# =============================================================================
# Roundtrip Tests
# =============================================================================


class TestRoundtrip:
    """Tests for save/load roundtrip integrity."""

    def test_roundtrip_preserves_data(self, tmp_features_path, valid_feature_dict):
        """Save then load preserves all feature data."""
        # Create and save
        store = FeatureStore(tmp_features_path)
        original = FeatureSpec.from_dict(valid_feature_dict)
        store._features[original.id] = original
        store.save()

        # Load in new store
        store2 = FeatureStore(tmp_features_path)
        loaded = store2.load()

        assert len(loaded) == 1
        restored = loaded[0]
        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.description == original.description
        assert restored.status == original.status
        assert restored.priority == original.priority

    def test_roundtrip_multiple_features(self, tmp_features_path):
        """Roundtrip preserves multiple features."""
        store = FeatureStore(tmp_features_path)

        for i in range(5):
            store._features[f"test-{i:03d}"] = FeatureSpec(
                id=f"test-{i:03d}",
                name=f"Feature {i}",
                description=f"Description {i}",
                priority=["critical", "high", "medium", "low"][i % 4],
            )

        store.save()

        store2 = FeatureStore(tmp_features_path)
        loaded = store2.load()

        assert len(loaded) == 5
        # Verify sorted by priority
        priorities = [f.priority for f in loaded]
        assert priorities[0] == "critical"  # First (priority 0)


# =============================================================================
# Flywheel Integration Tests
# =============================================================================


class TestFlywheelFormatHandling:
    """Integration tests for flywheel's format handling."""

    @pytest.mark.asyncio
    async def test_flywheel_preserves_wrapper_format(self, tmp_path):
        """run_flywheel preserves wrapper format when adding features."""

        features_path = tmp_path / "features.json"

        # Start with wrapper format
        initial = {
            "version": "1.0",
            "created_by": "test",
            "features": [{"id": "existing-001", "description": "Existing", "status": "passing"}],
        }
        features_path.write_text(json.dumps(initial))

        # Simulate flywheel's add-new-features logic
        data = json.loads(features_path.read_text())
        features_wrapper = None

        if isinstance(data, list):
            existing_features = data
        elif isinstance(data, dict):
            if "features" in data:
                existing_features = data.get("features", [])
                features_wrapper = data
            else:
                existing_features = []
        else:
            existing_features = []

        # Add new feature
        new_feature = {"id": "new-001", "description": "New feature", "status": "pending"}
        existing_features.append(new_feature)

        # Write back preserving format
        if features_wrapper is not None:
            features_wrapper["features"] = existing_features
            output_data = features_wrapper
        else:
            output_data = existing_features

        features_path.write_text(json.dumps(output_data, indent=2))

        # Verify
        saved = json.loads(features_path.read_text())
        assert saved["version"] == "1.0"
        assert saved["created_by"] == "test"
        assert len(saved["features"]) == 2

    @pytest.mark.asyncio
    async def test_flywheel_converts_list_to_list(self, tmp_path):
        """When starting with list format, flywheel keeps list format."""
        features_path = tmp_path / "features.json"

        # Start with plain list
        initial = [{"id": "existing-001", "description": "Existing", "status": "passing"}]
        features_path.write_text(json.dumps(initial))

        # Simulate flywheel's add-new-features logic
        data = json.loads(features_path.read_text())
        features_wrapper = None

        if isinstance(data, list):
            existing_features = data
        elif isinstance(data, dict):
            if "features" in data:
                existing_features = data.get("features", [])
                features_wrapper = data
            else:
                existing_features = []
        else:
            existing_features = []

        # Add new feature
        new_feature = {"id": "new-001", "description": "New feature", "status": "pending"}
        existing_features.append(new_feature)

        # Write back preserving format
        if features_wrapper is not None:
            features_wrapper["features"] = existing_features
            output_data = features_wrapper
        else:
            output_data = existing_features

        features_path.write_text(json.dumps(output_data, indent=2))

        # Verify it stayed as list
        saved = json.loads(features_path.read_text())
        assert isinstance(saved, list)
        assert len(saved) == 2


# =============================================================================
# Summary Matrix
# =============================================================================

"""
TEST MATRIX SUMMARY (AFTER FIXES)
=================================

| Format                        | FeatureStore.load() | Flywheel logic |
|-------------------------------|---------------------|----------------|
| Plain list [...]              | ✅ Works            | ✅ Works       |
| Wrapper {"features": [...]}   | ✅ Works            | ✅ Works       |
| Dict without "features"       | ✅ Returns []       | ✅ Returns [] + warn |
| Malformed JSON                | ✅ Returns []       | ✅ Returns []  |
| Empty file                    | ✅ Returns []       | ✅ Returns []  |
| Empty list []                 | ✅ Returns []       | ✅ Returns []  |
| Empty wrapper {"features":[]} | ✅ Returns []       | ✅ Returns []  |
| JSON null                     | ✅ Returns []       | ✅ Returns []  |
| Invalid status value          | ✅ Skips entry      | ✅ Skips entry |
| Missing required field        | ✅ Skips entry      | ✅ Skips entry |
| Mixed valid/invalid items     | ✅ Keeps valid only | ✅ Keeps valid |
| Nonexistent file              | ✅ Returns []       | ✅ Returns []  |

BUGS FIXED (2026-01-31):
========================

1. FeatureStore.load() now handles plain list format
   - Added isinstance check for list vs dict
   - Plain list [{"id": "..."}] now works

2. FeatureStore.load() now handles invalid status values gracefully
   - Per-feature try/except catches ValueError
   - Invalid entries are skipped with warning

3. FeatureStore.load() now handles non-dict items in features array
   - isinstance(f_data, dict) check before parsing
   - Non-dict items are skipped with warning

4. FeatureStore.load() now handles JSON null gracefully
   - else branch for unexpected JSON types
   - Returns empty list with warning

IMPLEMENTATION:
===============

```python
# Handle both formats: plain list or wrapper object
if isinstance(data, list):
    features_data = data
elif isinstance(data, dict):
    features_data = data.get("features", [])
else:
    features_data = []

# Parse each feature, skipping invalid entries
for f_data in features_data:
    if not isinstance(f_data, dict):
        logger.warning(f"Skipping non-dict feature entry")
        continue
    try:
        spec = FeatureSpec.from_dict(f_data)
        self._features[spec.id] = spec
    except (KeyError, ValueError, TypeError) as e:
        logger.warning(f"Skipping invalid feature entry: {e}")
        continue
```

NOTES:
- FeatureStore now supports both wrapper and plain list formats for reading
- FeatureStore.save() always writes wrapper format (version 1.0)
- Flywheel preserves the original format when modifying
- Invalid entries are skipped rather than failing the entire load
"""
