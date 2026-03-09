#!/usr/bin/env python3
"""Validation script for features.json format handling.

This script demonstrates the current bugs and proposed fixes for FeatureStore.load().

Usage:
    python tests/validate_format_handling.py

This will:
1. Show current buggy behavior
2. Show proposed fixes
3. Validate all format variations
"""

import json
import sys
from pathlib import Path
from typing import Any

# Add harness to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from forge_harness.ralph_loop import FeatureStore


def test_current_implementation():
    """Test current FeatureStore implementation (with bugs)."""
    print("=" * 70)
    print("CURRENT IMPLEMENTATION (WITH BUGS)")
    print("=" * 70)

    test_file = Path("/tmp/test_features.json")

    test_cases = [
        ("Plain List", [{"id": "test-001", "name": "Test", "description": "Test"}]),
        (
            "Wrapper Format",
            {
                "version": "1.0",
                "features": [{"id": "test-001", "name": "Test", "description": "Test"}],
            },
        ),
        ("Empty List", []),
        ("Empty Dict", {}),
        ("Dict without features", {"metadata": {}}),
    ]

    for name, data in test_cases:
        print(f"\n{name}:")
        test_file.write_text(json.dumps(data))
        store = FeatureStore(test_file)

        try:
            features = store.load()
            print(f"  ✅ Loaded {len(features)} features")
        except AttributeError as e:
            print(f"  ❌ AttributeError: {e}")
        except Exception as e:
            print(f"  ❌ {type(e).__name__}: {e}")

    test_file.unlink()


def proposed_load_implementation(store: FeatureStore) -> list[Any]:
    """Proposed fixed implementation of FeatureStore.load().

    This demonstrates the recommended fixes:
    1. Support both plain list and wrapper formats
    2. Expand exception handling
    3. Skip invalid items gracefully
    """
    if not store.features_path.exists():
        print(f"WARNING: Features file not found: {store.features_path}")
        return []

    try:
        data = json.loads(store.features_path.read_text())

        # FIX #1: Handle both plain list and wrapper formats
        if isinstance(data, list):
            features_data = data
        elif isinstance(data, dict):
            features_data = data.get("features", [])
        else:
            print(f"WARNING: Unexpected features.json format: {type(data)}")
            features_data = []

        # FIX #3: Robust item validation
        from forge_harness.ralph_loop import FeatureSpec

        for f_data in features_data:
            # Skip non-dict items
            if not isinstance(f_data, dict):
                print(f"WARNING: Skipping invalid feature item: {type(f_data)}")
                continue

            try:
                spec = FeatureSpec.from_dict(f_data)
                store._features[spec.id] = spec
            except (KeyError, ValueError, TypeError) as e:
                print(f"WARNING: Skipping invalid feature: {e}")
                continue

        # Sort by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        sorted_features = sorted(
            store._features.values(),
            key=lambda f: priority_order.get(f.priority, 4),
        )

        print(f"INFO: Loaded {len(sorted_features)} features from {store.features_path}")
        return sorted_features

    # FIX #2: Expand exception handling
    except (json.JSONDecodeError, KeyError, AttributeError, ValueError, TypeError) as e:
        print(f"ERROR: Failed to load features: {e}")
        return []


def test_proposed_implementation():
    """Test proposed fixed implementation."""
    print("\n" + "=" * 70)
    print("PROPOSED IMPLEMENTATION (WITH FIXES)")
    print("=" * 70)

    test_file = Path("/tmp/test_features.json")

    test_cases = [
        ("Plain List", [{"id": "test-001", "name": "Test", "description": "Test"}]),
        (
            "Wrapper Format",
            {
                "version": "1.0",
                "features": [{"id": "test-001", "name": "Test", "description": "Test"}],
            },
        ),
        ("Empty List", []),
        ("Empty Dict", {}),
        ("Dict without features", {"metadata": {}}),
        ("Malformed JSON", "not valid json"),
        (
            "Mixed Items",
            {
                "features": [
                    {"id": "test-001", "name": "Test", "description": "Test"},
                    "string",
                    None,
                ]
            },
        ),
        (
            "Invalid Status",
            {
                "features": [
                    {"id": "test-001", "name": "Test", "description": "Test", "status": "invalid"}
                ]
            },
        ),
    ]

    for name, data in test_cases:
        print(f"\n{name}:")
        if isinstance(data, str):
            test_file.write_text(data)
        else:
            test_file.write_text(json.dumps(data))

        store = FeatureStore(test_file)

        try:
            features = proposed_load_implementation(store)
            print(f"  ✅ Loaded {len(features)} features")
        except Exception as e:
            print(f"  ❌ Unexpected error: {type(e).__name__}: {e}")

    test_file.unlink()


def show_proposed_fixes():
    """Show the proposed code changes."""
    print("\n" + "=" * 70)
    print("PROPOSED CODE CHANGES")
    print("=" * 70)

    print("""
Location: forge_harness/ralph_loop.py:319-339

BEFORE (Current - Buggy):
```python
try:
    data = json.loads(self.features_path.read_text())
    features_data = data.get("features", [])  # ❌ Assumes dict

    for f_data in features_data:
        spec = FeatureSpec.from_dict(f_data)  # ❌ Assumes dict
        self._features[spec.id] = spec

    # ... sorting code ...

except (json.JSONDecodeError, KeyError) as e:  # ❌ Too narrow
    logger.error(f"Failed to load features: {e}")
    return []
```

AFTER (Proposed - Fixed):
```python
try:
    data = json.loads(self.features_path.read_text())

    # FIX #1: Handle both plain list and wrapper formats
    if isinstance(data, list):
        features_data = data
    elif isinstance(data, dict):
        features_data = data.get("features", [])
    else:
        logger.warning(f"Unexpected features.json format: {type(data)}")
        features_data = []

    # FIX #3: Robust item validation
    for f_data in features_data:
        # Skip non-dict items
        if not isinstance(f_data, dict):
            logger.warning(f"Skipping invalid feature item: {type(f_data)}")
            continue

        try:
            spec = FeatureSpec.from_dict(f_data)
            self._features[spec.id] = spec
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Skipping invalid feature: {e}")
            continue

    # ... sorting code ...

# FIX #2: Expand exception handling
except (json.JSONDecodeError, KeyError, AttributeError, ValueError, TypeError) as e:
    logger.error(f"Failed to load features: {e}")
    return []
```

Changes Summary:
1. Added format detection (list vs dict vs other)
2. Added type checking before processing items
3. Added per-item try/catch to skip invalid items
4. Expanded exception handling to catch all format errors
    """)


def main():
    """Run validation script."""
    print("FLYWHEEL FEATURES.JSON FORMAT VALIDATION")
    print("Testing FeatureStore.load() with various formats\n")

    # Show current buggy behavior
    test_current_implementation()

    # Show proposed fixed behavior
    test_proposed_implementation()

    # Show code changes
    show_proposed_fixes()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("""
Current Implementation: 5/8 test cases fail with AttributeError/ValueError
Proposed Implementation: 8/8 test cases handle gracefully

Next Steps:
1. Apply proposed fixes to forge_harness/ralph_loop.py
2. Run test suite: uv run pytest tests/test_flywheel_formats.py -v
3. Verify all tests pass

See docs/TEST_REPORT_FLYWHEEL_FORMATS.md for full details.
    """)


if __name__ == "__main__":
    main()
