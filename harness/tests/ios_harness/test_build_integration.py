"""
iOS Build Integration Tests
============================

These tests run real xcodebuild and xcodegen commands to verify
that the iOS harness generates valid project structures.

Run with: pytest -m integration tests/ios_harness/
Skip with: pytest -m "not integration" tests/

Requirements:
- macOS with Xcode installed
- xcodegen (brew install xcodegen)
- iOS Simulator runtime
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.integration
class TestXcodeGenIntegration:
    """Integration tests for XcodeGen project generation."""

    @pytest.mark.skipif(
        shutil.which("xcodegen") is None,
        reason="xcodegen not available",
    )
    def test_xcodegen_generates_valid_project(self, minimal_ios_project: Path):
        """Verify project.yml generates a valid .xcodeproj."""
        result = subprocess.run(
            ["xcodegen", "generate", "--spec", "project.yml"],
            cwd=minimal_ios_project,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0, f"xcodegen failed: {result.stderr}"

        # Verify xcodeproj structure
        xcodeproj = minimal_ios_project / "TestApp.xcodeproj"
        assert xcodeproj.exists(), "xcodeproj not created"
        assert (xcodeproj / "project.pbxproj").exists(), "project.pbxproj not created"

    @pytest.mark.skipif(
        shutil.which("xcodegen") is None,
        reason="xcodegen not available",
    )
    def test_xcodegen_project_contains_targets(self, minimal_ios_project: Path):
        """Verify generated project contains expected targets."""
        subprocess.run(
            ["xcodegen", "generate", "--spec", "project.yml"],
            cwd=minimal_ios_project,
            capture_output=True,
            timeout=60,
            check=True,
        )

        # Read project.pbxproj to verify targets exist
        pbxproj = minimal_ios_project / "TestApp.xcodeproj" / "project.pbxproj"
        content = pbxproj.read_text()

        assert "TestApp" in content, "TestApp target not found"
        assert "TestAppTests" in content, "TestAppTests target not found"


@pytest.mark.integration
class TestXcodeBuildIntegration:
    """Integration tests that run real xcodebuild."""

    @pytest.mark.skipif(
        shutil.which("xcodebuild") is None,
        reason="xcodebuild not available (requires macOS with Xcode)",
    )
    def test_xcodebuild_show_build_settings(
        self, generated_xcodeproj: Path, generic_ios_destination: str
    ):
        """Verify xcodebuild can show build settings for generated project."""
        result = subprocess.run(
            [
                "xcodebuild",
                "-project",
                str(generated_xcodeproj),
                "-scheme",
                "TestApp",
                "-destination",
                generic_ios_destination,
                "-showBuildSettings",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, f"xcodebuild -showBuildSettings failed: {result.stderr}"
        # Verify key build settings are present
        assert "PRODUCT_BUNDLE_IDENTIFIER" in result.stdout
        assert "SWIFT_VERSION" in result.stdout

    @pytest.mark.skipif(
        shutil.which("xcodebuild") is None,
        reason="xcodebuild not available (requires macOS with Xcode)",
    )
    def test_xcodebuild_list_schemes(self, generated_xcodeproj: Path):
        """Verify xcodebuild can list schemes from generated project."""
        result = subprocess.run(
            [
                "xcodebuild",
                "-project",
                str(generated_xcodeproj),
                "-list",
                "-json",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0, f"xcodebuild -list failed: {result.stderr}"

        import json

        project_info = json.loads(result.stdout)
        schemes = project_info.get("project", {}).get("schemes", [])

        # TestApp scheme should exist (defined in project.yml)
        assert "TestApp" in schemes, f"TestApp not in schemes: {schemes}"

    @pytest.mark.skipif(
        shutil.which("xcodebuild") is None,
        reason="xcodebuild not available (requires macOS with Xcode)",
    )
    def test_xcodebuild_list_targets(self, generated_xcodeproj: Path):
        """Verify xcodebuild can list targets from generated project."""
        result = subprocess.run(
            [
                "xcodebuild",
                "-project",
                str(generated_xcodeproj),
                "-list",
                "-json",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0, f"xcodebuild -list failed: {result.stderr}"

        import json

        project_info = json.loads(result.stdout)
        targets = project_info.get("project", {}).get("targets", [])

        assert "TestApp" in targets, f"TestApp not in targets: {targets}"
        assert "TestAppTests" in targets, f"TestAppTests not in targets: {targets}"

    @pytest.mark.skipif(
        shutil.which("xcodebuild") is None,
        reason="xcodebuild not available (requires macOS with Xcode)",
    )
    @pytest.mark.slow
    def test_xcodebuild_actual_build(self, generated_xcodeproj: Path, generic_ios_destination: str):
        """
        Verify generated project actually compiles.

        This test performs a real build and may take longer.
        Marked as 'slow' so it can be excluded from quick test runs.
        """
        result = subprocess.run(
            [
                "xcodebuild",
                "-project",
                str(generated_xcodeproj),
                "-scheme",
                "TestApp",
                "-destination",
                generic_ios_destination,
                "build",
                "-quiet",
            ],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minutes for actual build
        )

        # Build might fail due to signing, but should at least compile
        # Check for common compilation success indicators
        if result.returncode != 0:
            # Check if it's just a signing error (expected in CI)
            if "Signing" in result.stderr or "DEVELOPMENT_TEAM" in result.stderr:
                pytest.skip("Build failed due to code signing (expected in CI)")
            # Also skip for code sign errors in stdout
            if (
                "Code Signing Error" in result.stdout
                or "requires a provisioning profile" in result.stdout
            ):
                pytest.skip("Build failed due to code signing (expected in CI)")
            assert False, f"xcodebuild failed: {result.stdout}\n{result.stderr}"


@pytest.mark.integration
class TestProjectValidation:
    """Integration tests for project structure validation."""

    @pytest.mark.skipif(
        shutil.which("xcodegen") is None,
        reason="xcodegen not available",
    )
    def test_project_yml_validates_with_xcodegen(self, minimal_ios_project: Path):
        """Verify project.yml passes xcodegen validation."""
        # xcodegen doesn't have a dedicated validate command,
        # but generate with --dry-run-ish behavior can be simulated
        # by checking if it produces valid output
        result = subprocess.run(
            ["xcodegen", "dump", "--spec", "project.yml"],
            cwd=minimal_ios_project,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # dump should succeed if project.yml is valid
        assert result.returncode == 0, f"project.yml validation failed: {result.stderr}"

    def test_minimal_project_structure_complete(self, minimal_ios_project: Path):
        """Verify minimal project has all required files."""
        assert (minimal_ios_project / "project.yml").exists()
        assert (minimal_ios_project / "Sources").is_dir()
        assert (minimal_ios_project / "Sources" / "TestApp.swift").exists()
        assert (minimal_ios_project / "Tests").is_dir()
        assert (minimal_ios_project / "Tests" / "TestAppTests.swift").exists()


@pytest.mark.integration
class TestSimulatorDiscovery:
    """Integration tests for iOS Simulator discovery."""

    @pytest.mark.skipif(
        shutil.which("xcrun") is None,
        reason="xcrun not available (requires macOS with Xcode)",
    )
    def test_simctl_lists_devices(self):
        """Verify simctl can list available simulators."""
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "-j"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, f"simctl list failed: {result.stderr}"

        import json

        devices_info = json.loads(result.stdout)
        assert "devices" in devices_info, "No devices key in simctl output"

    @pytest.mark.skipif(
        shutil.which("xcrun") is None,
        reason="xcrun not available (requires macOS with Xcode)",
    )
    def test_simctl_lists_runtimes(self):
        """Verify simctl can list available runtimes."""
        result = subprocess.run(
            ["xcrun", "simctl", "list", "runtimes", "-j"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, f"simctl list runtimes failed: {result.stderr}"

        import json

        runtimes_info = json.loads(result.stdout)
        assert "runtimes" in runtimes_info, "No runtimes key in simctl output"

        # Verify at least one iOS runtime exists
        ios_runtimes = [r for r in runtimes_info["runtimes"] if r.get("platform") == "iOS"]
        # Don't fail if no iOS runtimes - just skip
        if not ios_runtimes:
            pytest.skip("No iOS runtimes installed")
