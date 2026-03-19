"""
XcodeGen Wrapper
================

Utilities for working with XcodeGen to generate Xcode projects.

XcodeGen is a command-line tool that generates .xcodeproj files from
YAML project specifications.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class XcodeGenResult:
    """Result of xcodegen execution."""

    success: bool
    stdout: str
    stderr: str
    error: str | None = None


class XcodeGenWrapper:
    """Wrapper for XcodeGen command-line tool."""

    @staticmethod
    async def generate(project_dir: Path, dry_run: bool = False) -> XcodeGenResult:
        """
        Run xcodegen generate in the specified directory.

        Args:
            project_dir: Directory containing project.yml
            dry_run: If True, only validate without generating

        Returns:
            XcodeGenResult with status and output
        """
        if not (project_dir / "project.yml").exists():
            return XcodeGenResult(
                success=False,
                stdout="",
                stderr="",
                error="project.yml not found in project directory",
            )

        try:
            cmd = ["xcodegen", "generate"]
            if dry_run:
                cmd.append("--spec")
                cmd.append(str(project_dir / "project.yml"))

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await proc.communicate()

            return XcodeGenResult(
                success=proc.returncode == 0,
                stdout=stdout.decode() if stdout else "",
                stderr=stderr.decode() if stderr else "",
                error=stderr.decode() if proc.returncode != 0 else None,
            )

        except FileNotFoundError:
            return XcodeGenResult(
                success=False,
                stdout="",
                stderr="",
                error="xcodegen not found. Install with: brew install xcodegen",
            )
        except Exception as e:
            return XcodeGenResult(
                success=False,
                stdout="",
                stderr="",
                error=str(e),
            )

    @staticmethod
    def validate_project_yml(project_yml_path: Path) -> tuple[bool, str | None]:
        """
        Validate project.yml syntax and required fields.

        Args:
            project_yml_path: Path to project.yml file

        Returns:
            Tuple of (valid, error_message)
        """
        if not project_yml_path.exists():
            return False, f"File not found: {project_yml_path}"

        try:
            with open(project_yml_path) as f:
                config = yaml.safe_load(f)

            # Check required fields
            if "name" not in config:
                return False, "Missing required field: name"

            if "targets" not in config:
                return False, "Missing required field: targets"

            if not isinstance(config["targets"], dict):
                return False, "targets must be a dictionary"

            return True, None

        except yaml.YAMLError as e:
            return False, f"Invalid YAML: {str(e)}"
        except Exception as e:
            return False, f"Validation error: {str(e)}"

    @staticmethod
    def create_minimal_project_yml(
        name: str,
        bundle_id: str,
        ios_version: str = "17.0",
    ) -> dict[str, Any]:
        """
        Create a minimal valid project.yml configuration.

        Args:
            name: Project name
            bundle_id: Bundle identifier
            ios_version: Minimum iOS version

        Returns:
            Dictionary that can be serialized to project.yml
        """
        return {
            "name": name,
            "options": {
                "bundleIdPrefix": ".".join(bundle_id.split(".")[:-1]),
                "deploymentTarget": {"iOS": ios_version},
            },
            "settings": {
                "base": {
                    "PRODUCT_BUNDLE_IDENTIFIER": bundle_id,
                    "SWIFT_VERSION": "6.0",
                }
            },
            "targets": {
                name: {
                    "type": "application",
                    "platform": "iOS",
                    "sources": [{"path": "App"}],
                },
                f"{name}Tests": {
                    "type": "bundle.unit-test",
                    "platform": "iOS",
                    "sources": [{"path": "Tests"}],
                    "dependencies": [{"target": name}],
                },
            },
        }
