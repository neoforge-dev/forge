"""
Pytest configuration for iOS harness integration tests.

This module provides fixtures for creating minimal iOS project structures
that can be used to test real xcodebuild and xcodegen operations.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

if TYPE_CHECKING:
    from collections.abc import Generator


# ============================================================================
# iOS Project Fixtures
# ============================================================================


@pytest.fixture
def minimal_ios_project(tmp_path: Path) -> Path:
    """
    Create minimal valid iOS project structure.

    This fixture creates a complete iOS project structure with:
    - project.yml for XcodeGen
    - Minimal SwiftUI App.swift source
    - Tests directory structure

    The project is designed to be valid for xcodegen and xcodebuild
    but minimal enough to generate/build quickly.

    Returns:
        Path to the project directory containing project.yml
    """
    project_dir = tmp_path / "TestApp"
    project_dir.mkdir()

    # Create project.yml for XcodeGen
    project_yml = {
        "name": "TestApp",
        "options": {
            "bundleIdPrefix": "com.test",
            "deploymentTarget": {"iOS": "17.0"},
            "xcodeVersion": "16.0",
            "generateEmptyDirectories": True,
            "createIntermediateGroups": True,
        },
        "settings": {
            "base": {
                "SWIFT_VERSION": "6.0",
                "IPHONEOS_DEPLOYMENT_TARGET": "17.0",
                "GENERATE_INFOPLIST_FILE": "YES",
            }
        },
        "targets": {
            "TestApp": {
                "type": "application",
                "platform": "iOS",
                "sources": [{"path": "Sources"}],
                "settings": {
                    "base": {
                        "PRODUCT_BUNDLE_IDENTIFIER": "com.test.TestApp",
                        "GENERATE_INFOPLIST_FILE": "YES",
                        "INFOPLIST_KEY_UIApplicationSceneManifest_Generation": "YES",
                        "INFOPLIST_KEY_UILaunchScreen_Generation": "YES",
                        "INFOPLIST_KEY_UISupportedInterfaceOrientations": "UIInterfaceOrientationPortrait",
                    }
                },
            },
            "TestAppTests": {
                "type": "bundle.unit-test",
                "platform": "iOS",
                "sources": [{"path": "Tests"}],
                "dependencies": [{"target": "TestApp"}],
                "settings": {
                    "base": {
                        "PRODUCT_BUNDLE_IDENTIFIER": "com.test.TestAppTests",
                        "GENERATE_INFOPLIST_FILE": "YES",
                    }
                },
            },
        },
        "schemes": {
            "TestApp": {
                "build": {
                    "targets": {"TestApp": "all"},
                },
                "test": {
                    "targets": ["TestAppTests"],
                },
            },
        },
    }
    (project_dir / "project.yml").write_text(yaml.dump(project_yml, sort_keys=False))

    # Create minimal Swift source
    sources = project_dir / "Sources"
    sources.mkdir()
    (sources / "TestApp.swift").write_text(
        """\
import SwiftUI

@main
struct TestApp: App {
    var body: some Scene {
        WindowGroup {
            Text("Hello, World!")
        }
    }
}
"""
    )

    # Create minimal test source
    tests = project_dir / "Tests"
    tests.mkdir()
    (tests / "TestAppTests.swift").write_text(
        """\
import XCTest
@testable import TestApp

final class TestAppTests: XCTestCase {
    func testExample() throws {
        XCTAssertTrue(true)
    }
}
"""
    )

    return project_dir


@pytest.fixture
def generated_xcodeproj(minimal_ios_project: Path) -> Generator[Path, None, None]:
    """
    Generate .xcodeproj using xcodegen from minimal_ios_project.

    This fixture runs xcodegen to create a real Xcode project
    from the project.yml specification.

    Skips the test if xcodegen is not installed.

    Yields:
        Path to the generated TestApp.xcodeproj
    """
    if shutil.which("xcodegen") is None:
        pytest.skip("xcodegen not installed (brew install xcodegen)")

    result = subprocess.run(
        ["xcodegen", "generate", "--spec", "project.yml"],
        cwd=minimal_ios_project,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        pytest.fail(f"xcodegen failed: {result.stderr}")

    xcodeproj = minimal_ios_project / "TestApp.xcodeproj"
    if not xcodeproj.exists():
        pytest.fail(f"xcodegen did not create TestApp.xcodeproj: {result.stdout}")

    yield xcodeproj


@pytest.fixture
def xcodebuild_available() -> bool:
    """Check if xcodebuild is available on the system."""
    return shutil.which("xcodebuild") is not None


@pytest.fixture
def xcodegen_available() -> bool:
    """Check if xcodegen is available on the system."""
    return shutil.which("xcodegen") is not None


# ============================================================================
# Helper Fixtures
# ============================================================================


@pytest.fixture
def ios_simulator_destination() -> str:
    """Get a valid iOS Simulator destination string."""
    return "platform=iOS Simulator,name=iPhone 16,OS=latest"


@pytest.fixture
def generic_ios_destination() -> str:
    """Get a generic iOS destination for dry-run builds."""
    return "generic/platform=iOS Simulator"
