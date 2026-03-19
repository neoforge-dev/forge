"""
Build Harness for iOS Projects
================================

Automates xcodebuild operations for FORGE iOS projects.

Features:
- Clean build automation
- Swift 6 strict concurrency enforcement
- Build output parsing and error extraction
- Configuration management (Debug/Release)
- Destination targeting (Simulator/Device)
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal


class BuildConfiguration(Enum):
    """Xcode build configurations."""

    DEBUG = "Debug"
    RELEASE = "Release"


@dataclass
class BuildConfig:
    """Build configuration for xcodebuild.

    Attributes:
        scheme: Xcode scheme name
        configuration: Build configuration (Debug/Release)
        destination: Build destination (Simulator/Device)
        enable_strict_concurrency: Enable Swift 6 strict concurrency checking
        enable_testing: Build for testing
        clean_build: Clean before build
        derived_data_path: Custom DerivedData location
        dry_run: If True, skip actual build and return simulated success
        simulation_artifacts: Simulated artifacts to return in dry_run mode
        simulation_build_time: Simulated build time in seconds for dry_run mode
    """

    scheme: str  # Xcode scheme name
    configuration: BuildConfiguration = BuildConfiguration.DEBUG
    destination: str = "platform=iOS Simulator,name=iPhone 15 Pro,OS=17.0"
    enable_strict_concurrency: bool = True  # Swift 6 strict concurrency
    enable_testing: bool = True  # Build for testing
    clean_build: bool = True  # Clean before build
    derived_data_path: Path | None = None  # Custom DerivedData location
    dry_run: bool = False  # Skip actual build, return simulated success
    simulation_artifacts: list[str] = field(default_factory=list)
    simulation_build_time: float = (
        0.0  # Simulated build time in seconds  # Custom DerivedData location
    )


@dataclass
class BuildError:
    """Represents a build error."""

    file_path: str
    line: int | None
    message: str
    error_type: Literal["error", "warning"]


@dataclass
class BuildResult:
    """Result of a build operation.

    Attributes:
        success: Whether the build succeeded
        errors: List of build errors
        warnings: List of build warnings
        build_time_seconds: Time taken to build
        stdout: Standard output from build
        stderr: Standard error from build
        exit_code: Exit code from xcodebuild
        is_simulation: True if this is a dry-run/simulated result
        artifacts: List of build artifacts produced
    """

    success: bool
    errors: list[BuildError] = field(default_factory=list)
    warnings: list[BuildError] = field(default_factory=list)
    build_time_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    is_simulation: bool = False
    artifacts: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        """Count of errors."""
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        """Count of warnings."""
        return len(self.warnings)


class BuildHarness:
    """Automate iOS builds with xcodebuild."""

    def __init__(self, project_path: Path):
        """
        Initialize build harness.

        Args:
            project_path: Path to iOS project directory (contains .xcodeproj)
        """
        self.project_path = Path(project_path)
        self._validate_project()

    def _validate_project(self) -> None:
        """Validate project directory exists and contains .xcodeproj."""
        if not self.project_path.exists():
            raise ValueError(f"Project path does not exist: {self.project_path}")

        # Find .xcodeproj file
        xcodeproj_files = list(self.project_path.glob("*.xcodeproj"))
        if not xcodeproj_files:
            raise ValueError(f"No .xcodeproj found in {self.project_path}")

        self.xcodeproj_path = xcodeproj_files[0]

    async def build(self, config: BuildConfig) -> BuildResult:
        """
        Build iOS project.

        Args:
            config: Build configuration

        Returns:
            BuildResult with success status, errors, warnings

        Example:
            # Normal build
            result = await harness.build(BuildConfig(scheme="MyApp"))

            # Dry-run mode (no actual build)
            result = await harness.build(BuildConfig(
                scheme="MyApp",
                dry_run=True,
                simulation_artifacts=["MyApp.app"],
                simulation_build_time=5.0,
            ))
        """
        import logging
        import time

        logger = logging.getLogger("forge_harness.ios_harness")

        # Handle dry-run mode - return simulated success without actual build
        if config.dry_run:
            logger.info(f"Dry-run mode: skipping actual build for scheme '{config.scheme}'")
            return BuildResult(
                success=True,
                errors=[],
                warnings=[],
                build_time_seconds=config.simulation_build_time,
                stdout=f"[DRY-RUN] Would build scheme '{config.scheme}' with configuration '{config.configuration.value}'",
                stderr="",
                exit_code=0,
                is_simulation=True,
                artifacts=config.simulation_artifacts,
            )

        start_time = time.time()

        # Construct xcodebuild command
        cmd = self._build_command(config)

        try:
            # Run xcodebuild
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await proc.communicate()
            build_time = time.time() - start_time

            stdout_str = stdout.decode() if stdout else ""
            stderr_str = stderr.decode() if stderr else ""

            # Parse build output for errors and warnings
            errors, warnings = self._parse_build_output(stdout_str + stderr_str)

            return BuildResult(
                success=proc.returncode == 0,
                errors=errors,
                warnings=warnings,
                build_time_seconds=round(build_time, 2),
                stdout=stdout_str,
                stderr=stderr_str,
                exit_code=proc.returncode or 0,
                is_simulation=False,
                artifacts=[],  # Could parse for artifacts in real build
            )

        except FileNotFoundError:
            return BuildResult(
                success=False,
                errors=[
                    BuildError(
                        file_path="",
                        line=None,
                        message="xcodebuild not found. Install Xcode Command Line Tools: xcode-select --install",
                        error_type="error",
                    )
                ],
            )
        except Exception as e:
            return BuildResult(
                success=False,
                errors=[
                    BuildError(
                        file_path="",
                        line=None,
                        message=f"Build failed: {str(e)}",
                        error_type="error",
                    )
                ],
            )

    def _build_command(self, config: BuildConfig) -> list[str]:
        """Construct xcodebuild command."""
        cmd = ["xcodebuild"]

        # Add clean if requested
        if config.clean_build:
            cmd.append("clean")

        # Add build action
        if config.enable_testing:
            cmd.append("build-for-testing")
        else:
            cmd.append("build")

        # Add project and scheme
        cmd.extend(["-project", str(self.xcodeproj_path)])
        cmd.extend(["-scheme", config.scheme])
        cmd.extend(["-configuration", config.configuration.value])
        cmd.extend(["-destination", config.destination])

        # Add strict concurrency if enabled
        if config.enable_strict_concurrency:
            cmd.extend(
                [
                    "-Xswiftc",
                    "-strict-concurrency=complete",
                    "-Xswiftc",
                    "-enable-actor-data-race-checks",
                ]
            )

        # Add derived data path if specified
        if config.derived_data_path:
            cmd.extend(["-derivedDataPath", str(config.derived_data_path)])

        return cmd

    def _parse_build_output(self, output: str) -> tuple[list[BuildError], list[BuildError]]:
        """
        Parse xcodebuild output for errors and warnings.

        Returns:
            Tuple of (errors, warnings)
        """
        errors = []
        warnings = []

        # Pattern: /path/to/file.swift:123:45: error: message
        # Pattern: /path/to/file.swift:123:45: warning: message
        error_pattern = re.compile(
            r"^(.+?):(\d+):(?:\d+:)?\s+(error|warning):\s+(.+)$", re.MULTILINE
        )

        for match in error_pattern.finditer(output):
            file_path = match.group(1)
            line_num = int(match.group(2))
            error_type = match.group(3)
            message = match.group(4).strip()

            build_error = BuildError(
                file_path=file_path,
                line=line_num,
                message=message,
                error_type=error_type,  # type: ignore
            )

            if error_type == "error":
                errors.append(build_error)
            else:
                warnings.append(build_error)

        # Also check for generic BUILD FAILED message
        if "** BUILD FAILED **" in output and not errors:
            errors.append(
                BuildError(
                    file_path="",
                    line=None,
                    message="Build failed - check build output for details",
                    error_type="error",
                )
            )

        return errors, warnings

    async def list_schemes(self) -> list[str]:
        """
        List available Xcode schemes in the project.

        Returns:
            List of scheme names
        """
        cmd = ["xcodebuild", "-list", "-project", str(self.xcodeproj_path), "-json"]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, _ = await proc.communicate()

            if proc.returncode == 0 and stdout:
                import json

                data = json.loads(stdout.decode())
                # Extract schemes from project info
                if "project" in data and "schemes" in data["project"]:
                    return data["project"]["schemes"]

            return []

        except Exception:
            return []

    async def clean(self) -> bool:
        """
        Clean build artifacts.

        Returns:
            True if clean succeeded
        """
        cmd = [
            "xcodebuild",
            "clean",
            "-project",
            str(self.xcodeproj_path),
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            await proc.communicate()
            return proc.returncode == 0

        except Exception:
            return False
