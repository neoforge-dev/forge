# Flywheel Features.json Format Tests

## Quick Reference

**Test File:** `test_flywheel_formats.py`
**Coverage:** 11 test classes, 32+ test methods
**Purpose:** Comprehensive testing of features.json format variations in flywheel system

## Test Classes

### 1. Format Parsing Tests

| Class | Tests | Purpose |
|-------|-------|---------|
| `TestPlainListFormat` | 3 | Plain array format `[...]` |
| `TestWrapperWithFeaturesKey` | 4 | Wrapper format `{"features": [...]}` |
| `TestWrapperWithoutFeaturesKey` | 3 | Dict without features key |
| `TestMalformedJson` | 4 | Invalid JSON syntax |
| `TestEmptyFile` | 2 | Zero-byte and whitespace files |
| `TestEmptyStructures` | 3 | Empty but valid JSON structures |
| `TestMixedFeatures` | 3 | Arrays with mixed valid/invalid items |
| `TestStatusVariations` | 3 | Valid/invalid status values |

### 2. Integrity Tests

| Class | Tests | Purpose |
|-------|-------|---------|
| `TestRoundtrip` | 2 | Save/load data preservation |
| `TestFileSystemEdgeCases` | 3 | File system edge cases |

### 3. Integration Tests

| Class | Tests | Purpose |
|-------|-------|---------|
| `TestFlywheelFormatHandling` | 2 | Flywheel format preservation |

## Running Tests

### All Format Tests
```bash
uv run pytest tests/test_flywheel_formats.py -v
```

### Specific Test Class
```bash
uv run pytest tests/test_flywheel_formats.py::TestPlainListFormat -v
```

### Single Test
```bash
uv run pytest tests/test_flywheel_formats.py::TestPlainListFormat::test_load_plain_list_raises_error -v
```

### With Coverage
```bash
uv run pytest tests/test_flywheel_formats.py \
  --cov=forge_harness.ralph_loop \
  --cov=forge_harness.flywheel \
  --cov-report=term-missing
```

## Key Test Scenarios

### ✅ Supported Formats (Should Pass After Fix)

1. **Plain List**
   ```json
   [{"id": "feat-001", "name": "Test", "description": "..."}]
   ```

2. **Wrapper with Features**
   ```json
   {"version": "1.0", "features": [...]}
   ```

3. **Empty Structures**
   ```json
   []
   {"features": []}
   ```

### ❌ Currently Failing (Bugs)

1. **Plain List** → AttributeError (Bug #1)
2. **Empty List `[]`** → AttributeError (Bug #1)
3. **JSON null** → AttributeError (Bug #1)
4. **Invalid Status** → ValueError (Bug #2)
5. **Mixed Items** → TypeError (Bug #3)

### ✅ Graceful Degradation (Working)

1. **Malformed JSON** → Returns `[]`
2. **Empty File** → Returns `[]`
3. **Missing File** → Returns `[]`
4. **Dict without Features** → Returns `[]`
5. **Missing Required Fields** → Returns `[]` (logged)

## Test Documentation

Each test class includes:
- Class docstring explaining format variation
- Test method docstrings with expected behavior
- Clear assertions with meaningful error messages
- BUG comments documenting known issues

Example:
```python
class TestPlainListFormat:
    """Tests for plain list format: [{"id": "...", ...}, ...]"""

    def test_load_plain_list_raises_error(self, tmp_features_path, valid_feature_dict):
        """FeatureStore raises AttributeError on plain list format.

        NOTE: This is a bug - AttributeError is not caught by the except clause.
        """
        # Test implementation...
```

## Bugs Documented

All discovered bugs are documented inline with:
- `BUG:` comment explaining the issue
- `NOTE:` explaining why it fails
- Expected vs actual behavior
- Links to recommended fixes

See `docs/TEST_REPORT_FLYWHEEL_FORMATS.md` for full bug report and fixes.

## Summary Matrix

Quick reference showing what each format should do:

| Format | FeatureStore | Flywheel | Status |
|--------|-------------|----------|--------|
| Plain list | ❌ → ✅ | ✅ | Fix needed |
| Wrapper | ✅ | ✅ | Working |
| Empty list | ❌ → ✅ | ✅ | Fix needed |
| Empty dict | ✅ | ✅ | Working |
| Malformed | ✅ | ✅ | Working |
| Invalid status | ❌ → ✅ | ❌ → ✅ | Fix needed |

Legend:
- ✅ Works correctly
- ❌ Fails
- ❌ → ✅ Fails now, will work after fix
