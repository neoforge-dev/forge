#!/usr/bin/env python3
"""
Verification script for forge-shared package structure.

Run this script to verify all required files and structure are in place.
"""

import os
import sys
from pathlib import Path

# Required files and directories
REQUIRED_STRUCTURE = {
    "root": [
        "pyproject.toml",
        "README.md",
        "LICENSE",
        ".gitignore",
        ".env.example",
        "CHANGELOG.md",
        "SETUP.md",
        "Makefile",
    ],
    "forge_shared": [
        "__init__.py",
        "auth/__init__.py",
        "auth/jwt.py",
        "auth/dependencies.py",
        "auth/models.py",
        "auth/middleware.py",
        "analytics/__init__.py",
        "analytics/client.py",
        "analytics/events.py",
        "analytics/middleware.py",
        "middleware/__init__.py",
        "middleware/rate_limit.py",
        "middleware/security.py",
        "middleware/cors.py",
        "middleware/request_id.py",
        "middleware/exceptions.py",
        "config/__init__.py",
        "config/base.py",
        "config/domain.py",
        "config/loaders.py",
        "logging/__init__.py",
        "logging/formatter.py",
        "logging/context.py",
        "logging/middleware.py",
        "utils/__init__.py",
        "utils/ip.py",
        "utils/headers.py",
    ],
    "tests": [
        "__init__.py",
        "conftest.py",
        "test_auth/__init__.py",
        "test_analytics/__init__.py",
        "test_middleware/__init__.py",
        "test_config/__init__.py",
    ],
    "examples": [
        "fastapi_app.py",
        "migration_guide.py",
    ],
}


def check_file(path: Path) -> bool:
    """Check if a file exists and is not empty."""
    if not path.exists():
        print(f"  ❌ MISSING: {path}")
        return False
    if path.stat().st_size == 0:
        print(f"  ⚠️  EMPTY: {path}")
        return False
    print(f"  ✅ OK: {path}")
    return True


def verify_structure(base_path: Path) -> bool:
    """Verify the package structure."""
    print("🔍 Verifying forge-shared package structure...\n")

    all_ok = True

    # Check root files
    print("📁 Root files:")
    for file in REQUIRED_STRUCTURE["root"]:
        if not check_file(base_path / file):
            all_ok = False
    print()

    # Check forge_shared module
    print("📦 forge_shared module:")
    for file in REQUIRED_STRUCTURE["forge_shared"]:
        if not check_file(base_path / "forge_shared" / file):
            all_ok = False
    print()

    # Check tests
    print("🧪 Tests:")
    for file in REQUIRED_STRUCTURE["tests"]:
        if not check_file(base_path / "tests" / file):
            all_ok = False
    print()

    # Check examples
    print("📚 Examples:")
    for file in REQUIRED_STRUCTURE["examples"]:
        if not check_file(base_path / "examples" / file):
            all_ok = False
    print()

    return all_ok


def main():
    """Main verification function."""
    # Get base directory (assume script is in forge-shared)
    base_path = Path(__file__).parent.absolute()

    print(f"📂 Base path: {base_path}\n")

    # Verify structure
    all_ok = verify_structure(base_path)

    # Print summary
    if all_ok:
        print("\n✅ SUCCESS: All required files are present!")
        print("\n🚀 Next steps:")
        print("   1. Install dependencies: make dev-install")
        print("   2. Run tests: make test")
        print("   3. Check examples: ls examples/")
        return 0
    else:
        print("\n❌ FAILURE: Some required files are missing or empty!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
