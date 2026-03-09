# Flywheel features.json Format Testing - Complete Guide

This directory contains comprehensive test coverage for features.json format handling in the flywheel system.

## Quick Links

- **Test File**: [`test_flywheel_formats.py`](test_flywheel_formats.py) - 757 lines, 32+ tests
- **Full Report**: [`../docs/TEST_REPORT_FLYWHEEL_FORMATS.md`](../docs/TEST_REPORT_FLYWHEEL_FORMATS.md) - Detailed bug reports and fixes
- **Summary**: [`../docs/FLYWHEEL_FORMAT_TEST_SUMMARY.md`](../docs/FLYWHEEL_FORMAT_TEST_SUMMARY.md) - Implementation summary
- **Quick Reference**: [`README_FLYWHEEL_FORMATS.md`](README_FLYWHEEL_FORMATS.md) - Test execution guide
- **Validation Script**: [`validate_format_handling.py`](validate_format_handling.py) - Demonstrates bugs and fixes

## What's Covered

### 8 Core Format Scenarios

1. Plain list: `[{"id": "...", ...}]`
2. Wrapper with features: `{"version": "1.0", "features": [...]}`
3. Wrapper without features: `{"version": "1.0", "project": "..."}`
4. Empty list: `[]`
5. Empty object: `{}`
6. Malformed JSON
7. Missing file
8. Format preservation on write

### Additional Test Coverage

- Status value variations (7 status types)
- Mixed valid/invalid items
- Roundtrip integrity (save/load)
- File system edge cases
- Error handling and graceful degradation

## Bugs Discovered

3 critical bugs found in `forge_harness/ralph_loop.py`:

1. **AttributeError on plain list format** - HIGH severity
2. **ValueError on invalid status not caught** - MEDIUM severity
3. **TypeError on mixed items not caught** - MEDIUM severity

All bugs documented with reproduction steps and proposed fixes.

## Quick Start

### Run All Tests
```bash
uv run pytest tests/test_flywheel_formats.py -v
```

### Run Validation Script
```bash
python tests/validate_format_handling.py
```

This script demonstrates:
- Current buggy behavior
- Proposed fixes
- All format variations

### Run Specific Test Class
```bash
# Test plain list format
uv run pytest tests/test_flywheel_formats.py::TestPlainListFormat -v

# Test wrapper format
uv run pytest tests/test_flywheel_formats.py::TestWrapperWithFeaturesKey -v

# Test error handling
uv run pytest tests/test_flywheel_formats.py::TestMalformedJson -v
```

### Check Coverage
```bash
uv run pytest tests/test_flywheel_formats.py \
  --cov=forge_harness.ralph_loop \
  --cov=forge_harness.flywheel \
  --cov-report=term-missing
```

## Test Organization

```
tests/
├── test_flywheel_formats.py           # Main test file (757 lines)
│   ├── TestPlainListFormat            # 3 tests
│   ├── TestWrapperWithFeaturesKey     # 4 tests
│   ├── TestWrapperWithoutFeaturesKey  # 3 tests
│   ├── TestMalformedJson              # 4 tests
│   ├── TestEmptyFile                  # 2 tests
│   ├── TestEmptyStructures            # 3 tests
│   ├── TestMixedFeatures              # 3 tests
│   ├── TestStatusVariations           # 3 tests
│   ├── TestRoundtrip                  # 2 tests
│   ├── TestFileSystemEdgeCases        # 3 tests
│   └── TestFlywheelFormatHandling     # 2 tests
│
├── validate_format_handling.py        # Validation script
├── README_FLYWHEEL_FORMATS.md         # Test execution guide
└── FLYWHEEL_FORMATS_README.md         # This file

docs/
├── TEST_REPORT_FLYWHEEL_FORMATS.md    # Full bug report (300+ lines)
└── FLYWHEEL_FORMAT_TEST_SUMMARY.md    # Implementation summary
```

## Expected Test Results

### Before Bug Fixes
- **Passing**: 27+ tests
- **Failing**: 5 tests (documented bugs)
  - `test_load_plain_list_raises_error`
  - `test_load_empty_list`
  - `test_load_null`
  - `test_invalid_status_value`
  - `test_wrapper_with_mixed_items`

### After Bug Fixes
- **Passing**: All 32+ tests
- **Failing**: None

## Documentation Structure

### For Developers
1. **Start here**: [`README_FLYWHEEL_FORMATS.md`](README_FLYWHEEL_FORMATS.md)
2. **Run tests**: `uv run pytest tests/test_flywheel_formats.py -v`
3. **Review bugs**: [`../docs/TEST_REPORT_FLYWHEEL_FORMATS.md`](../docs/TEST_REPORT_FLYWHEEL_FORMATS.md)
4. **Apply fixes**: See proposed code changes in bug report

### For QA
1. **Test summary**: [`../docs/FLYWHEEL_FORMAT_TEST_SUMMARY.md`](../docs/FLYWHEEL_FORMAT_TEST_SUMMARY.md)
2. **Test matrix**: See coverage table in test report
3. **Run validation**: `python tests/validate_format_handling.py`

### For Stakeholders
1. **Executive summary**: See "Executive Summary" in test report
2. **Bug impact**: 3 critical bugs, all documented with fixes
3. **Test coverage**: 32+ tests, 11 test classes, 8 format variations

## Format Specification

### Supported Formats

**Plain List (Recommended for simplicity)**
```json
[
  {"id": "feat-001", "name": "Feature 1", "description": "..."},
  {"id": "feat-002", "name": "Feature 2", "description": "..."}
]
```

**Wrapper Format (Recommended for metadata)**
```json
{
  "version": "1.0",
  "created_at": "2026-02-01",
  "features": [
    {"id": "feat-001", "name": "Feature 1", "description": "..."}
  ]
}
```

### Required Feature Fields
- `id` - Unique identifier
- `name` or `title` - Human-readable name
- `description` - Detailed description

### Optional Feature Fields
- `status` - pending, in_progress, passing, failing, blocked, skipped, completed
- `priority` - critical, high, medium, low
- `acceptance_criteria` - Array of strings
- `depends_on` or `dependencies` - Array of feature IDs
- `tests` - Array of test names
- Plus many more (see FeatureSpec in ralph_loop.py)

## Common Issues

### Issue: Tests failing with AttributeError
**Cause**: Bug #1 - FeatureStore doesn't support plain list format
**Solution**: Apply Fix #1 from bug report or use wrapper format

### Issue: Tests failing with ValueError
**Cause**: Bug #2 - Invalid status values not caught
**Solution**: Apply Fix #2 from bug report or use valid status values

### Issue: Tests failing with TypeError
**Cause**: Bug #3 - Mixed items not handled
**Solution**: Apply Fix #3 from bug report or ensure all items are valid dicts

## CI/CD Integration

### Add to CI Pipeline
```yaml
# .github/workflows/test.yml
- name: Run Flywheel Format Tests
  run: |
    uv run pytest tests/test_flywheel_formats.py -v \
      --cov=forge_harness.ralph_loop \
      --cov=forge_harness.flywheel \
      --cov-report=xml

- name: Upload Coverage
  uses: codecov/codecov-action@v3
  with:
    files: ./coverage.xml
```

### Pre-commit Hook
```bash
#!/bin/bash
# .git/hooks/pre-commit

# Run format tests
uv run pytest tests/test_flywheel_formats.py -v

if [ $? -ne 0 ]; then
  echo "Flywheel format tests failed. Fix before committing."
  exit 1
fi
```

## Maintenance

### Adding New Format Tests
1. Add test method to appropriate test class
2. Use existing fixtures: `tmp_features_path`, `valid_feature_dict`
3. Follow naming convention: `test_<scenario>_<expected_behavior>`
4. Add docstring explaining scenario
5. Update test count in this README

### Updating After Code Changes
1. Run full test suite after any ralph_loop.py changes
2. Update bug report if behavior changes
3. Add regression tests for any new bugs found
4. Keep coverage above 80%

## Related Files

### Implementation
- `forge_harness/ralph_loop.py` - FeatureStore class
- `forge_harness/flywheel.py` - run_flywheel function

### Tests
- `test_flywheel_formats.py` - This test suite
- `test_flywheel.py` - Integration tests
- `test_ralph_loop.py` - Ralph loop tests

### Documentation
- `CLAUDE.md` - Harness documentation
- `docs/RALPH_LOOP_GUIDE.md` - Ralph loop guide
- `docs/FLYWHEEL.md` - Flywheel documentation

## Support

For questions or issues:
1. Check test docstrings for expected behavior
2. Review bug report for known issues
3. Run validation script to see examples
4. Check test_flywheel_formats.py for comprehensive examples

## License

Part of the FORGE Harness project. See main project LICENSE.

---

**Last Updated**: 2026-02-01
**Test Coverage**: 32+ tests across 11 test classes
**Bug Count**: 3 critical bugs documented with fixes
**Status**: Ready for bug fixes and integration
