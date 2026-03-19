"""
App Store Harness Module
==========================

App Store Connect automation for TestFlight and App Store deployment.

Features:
- Archive creation (xcodebuild archive)
- IPA export with code signing
- TestFlight upload via altool/notarytool
- App Store Connect API integration
- Release management
- Metadata updates
"""

from __future__ import annotations

import asyncio
import plistlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class ExportMethod(Enum):
    """IPA export methods."""

    APP_STORE = "app-store"
    AD_HOC = "ad-hoc"
    DEVELOPMENT = "development"
    ENTERPRISE = "enterprise"


class ReleaseType(Enum):
    """App Store release types."""

    MANUAL = "MANUAL"
    AFTER_APPROVAL = "AFTER_APPROVAL"
    SCHEDULED = "SCHEDULED"


@dataclass
class ArchiveConfig:
    """Configuration for archive creation."""

    project_path: Path
    scheme: str
    configuration: str = "Release"
    archive_path: Path | None = None
    team_id: str | None = None
    provisioning_profile: str | None = None


@dataclass
class ExportConfig:
    """Configuration for IPA export."""

    archive_path: Path
    export_method: ExportMethod
    team_id: str
    export_path: Path | None = None
    provisioning_profile: str | None = None
    signing_certificate: str | None = None
    compile_bitcode: bool = False
    upload_symbols: bool = True


@dataclass
class UploadConfig:
    """Configuration for TestFlight/App Store upload."""

    ipa_path: Path
    api_key_id: str
    api_issuer_id: str
    api_key_path: Path
    platform: str = "ios"


@dataclass
class ArchiveResult:
    """Result of archive creation."""

    success: bool
    archive_path: Path | None = None
    build_number: str | None = None
    version: str | None = None
    error: str | None = None


@dataclass
class ExportResult:
    """Result of IPA export."""

    success: bool
    ipa_path: Path | None = None
    export_method: ExportMethod | None = None
    error: str | None = None


@dataclass
class UploadResult:
    """Result of upload to App Store Connect."""

    success: bool
    build_id: str | None = None
    upload_time: datetime | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ReleaseInfo:
    """App Store release information."""

    version: str
    build_number: str
    release_type: ReleaseType
    release_date: datetime | None = None
    what_s_new: str | None = None
    phased_release: bool = False


class AppStoreHarness:
    """App Store Connect automation harness."""

    def __init__(self, workspace_dir: Path):
        """
        Initialize App Store harness.

        Args:
            workspace_dir: Directory for build artifacts
        """
        self.workspace_dir = Path(workspace_dir)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    async def archive(self, config: ArchiveConfig) -> ArchiveResult:
        """
        Create archive (.xcarchive) from project.

        Args:
            config: Archive configuration

        Returns:
            ArchiveResult with archive path
        """
        # Determine archive path
        archive_path = config.archive_path or (
            self.workspace_dir
            / "archives"
            / f"{config.scheme}_{datetime.now():%Y%m%d_%H%M%S}.xcarchive"
        )
        archive_path.parent.mkdir(parents=True, exist_ok=True)

        # Build command
        cmd = [
            "xcodebuild",
            "archive",
            "-project",
            str(config.project_path),
            "-scheme",
            config.scheme,
            "-configuration",
            config.configuration,
            "-archivePath",
            str(archive_path),
        ]

        # Add team ID if specified
        if config.team_id:
            cmd.extend(["-allowProvisioningUpdates", "DEVELOPMENT_TEAM=" + config.team_id])

        # Run archive
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            return ArchiveResult(
                success=False,
                error=stderr.decode(),
            )

        # Extract version info from Info.plist
        info_plist_path = archive_path / "Info.plist"
        version, build_number = None, None

        if info_plist_path.exists():
            with open(info_plist_path, "rb") as f:
                plist_data = plistlib.load(f)
                version = plist_data.get("ApplicationProperties", {}).get(
                    "CFBundleShortVersionString"
                )
                build_number = plist_data.get("ApplicationProperties", {}).get("CFBundleVersion")

        return ArchiveResult(
            success=True,
            archive_path=archive_path,
            build_number=build_number,
            version=version,
        )

    async def export(self, config: ExportConfig) -> ExportResult:
        """
        Export IPA from archive.

        Args:
            config: Export configuration

        Returns:
            ExportResult with IPA path
        """
        # Determine export path
        export_path = config.export_path or (
            self.workspace_dir / "exports" / config.export_method.value
        )
        export_path.mkdir(parents=True, exist_ok=True)

        # Create export options plist
        export_options = self._create_export_options(config)
        export_options_path = export_path / "ExportOptions.plist"

        with open(export_options_path, "wb") as f:
            plistlib.dump(export_options, f)

        # Build command
        cmd = [
            "xcodebuild",
            "-exportArchive",
            "-archivePath",
            str(config.archive_path),
            "-exportPath",
            str(export_path),
            "-exportOptionsPlist",
            str(export_options_path),
        ]

        # Run export
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            return ExportResult(
                success=False,
                error=stderr.decode(),
            )

        # Find generated IPA
        ipa_files = list(export_path.glob("*.ipa"))
        if not ipa_files:
            return ExportResult(
                success=False,
                error="No IPA file generated",
            )

        return ExportResult(
            success=True,
            ipa_path=ipa_files[0],
            export_method=config.export_method,
        )

    async def upload_to_testflight(self, config: UploadConfig) -> UploadResult:
        """
        Upload IPA to TestFlight via App Store Connect API.

        Args:
            config: Upload configuration

        Returns:
            UploadResult with build ID
        """
        # Validate IPA exists
        if not config.ipa_path.exists():
            return UploadResult(
                success=False,
                errors=[f"IPA not found: {config.ipa_path}"],
            )

        # Build command using altool (legacy) or xcrun altool
        cmd = [
            "xcrun",
            "altool",
            "--upload-app",
            "--type",
            config.platform,
            "--file",
            str(config.ipa_path),
            "--apiKey",
            config.api_key_id,
            "--apiIssuer",
            config.api_issuer_id,
        ]

        # Run upload
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        result = UploadResult(success=proc.returncode == 0, upload_time=datetime.now())

        # Parse output for warnings/errors
        output = stdout.decode() + stderr.decode()
        if "WARNING" in output:
            result.warnings = [line for line in output.split("\n") if "WARNING" in line]

        if proc.returncode != 0:
            result.errors = [
                line for line in output.split("\n") if "ERROR" in line or "error" in line
            ]

        return result

    async def validate_ipa(self, config: UploadConfig) -> UploadResult:
        """
        Validate IPA without uploading.

        Args:
            config: Upload configuration

        Returns:
            UploadResult with validation results
        """
        if not config.ipa_path.exists():
            return UploadResult(
                success=False,
                errors=[f"IPA not found: {config.ipa_path}"],
            )

        cmd = [
            "xcrun",
            "altool",
            "--validate-app",
            "--type",
            config.platform,
            "--file",
            str(config.ipa_path),
            "--apiKey",
            config.api_key_id,
            "--apiIssuer",
            config.api_issuer_id,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        result = UploadResult(success=proc.returncode == 0)

        output = stdout.decode() + stderr.decode()
        if "WARNING" in output:
            result.warnings = [line for line in output.split("\n") if "WARNING" in line]

        if proc.returncode != 0:
            result.errors = [
                line for line in output.split("\n") if "ERROR" in line or "error" in line
            ]

        return result

    async def get_build_status(
        self, bundle_id: str, version: str, build_number: str
    ) -> dict[str, Any]:
        """
        Get build processing status from App Store Connect.

        Note: Requires App Store Connect API token with proper permissions.

        Args:
            bundle_id: App bundle identifier
            version: App version
            build_number: Build number

        Returns:
            Build status information
        """
        # This would use App Store Connect API
        # For now, return placeholder
        return {
            "status": "processing",
            "version": version,
            "build_number": build_number,
            "bundle_id": bundle_id,
        }

    def _create_export_options(self, config: ExportConfig) -> dict[str, Any]:
        """Create ExportOptions.plist dictionary."""
        options: dict[str, Any] = {
            "method": config.export_method.value,
            "teamID": config.team_id,
            "uploadBitcode": config.compile_bitcode,
            "uploadSymbols": config.upload_symbols,
        }

        if config.provisioning_profile:
            options["provisioningProfiles"] = {
                # Will be filled with bundle ID -> profile mapping
            }

        if config.signing_certificate:
            options["signingCertificate"] = config.signing_certificate

        return options


async def create_release_workflow(
    project_path: Path,
    scheme: str,
    team_id: str,
    api_key_id: str,
    api_issuer_id: str,
    api_key_path: Path,
    workspace_dir: Path | None = None,
) -> tuple[ArchiveResult, ExportResult, UploadResult]:
    """
    Complete release workflow: archive → export → upload.

    Args:
        project_path: Path to .xcodeproj
        scheme: Xcode scheme name
        team_id: Apple Developer Team ID
        api_key_id: App Store Connect API Key ID
        api_issuer_id: App Store Connect API Issuer ID
        api_key_path: Path to API key file (.p8)
        workspace_dir: Working directory for builds

    Returns:
        Tuple of (ArchiveResult, ExportResult, UploadResult)
    """
    harness = AppStoreHarness(workspace_dir or Path.cwd() / ".build")

    # Step 1: Archive
    archive_result = await harness.archive(
        ArchiveConfig(
            project_path=project_path,
            scheme=scheme,
            team_id=team_id,
        )
    )

    if not archive_result.success or not archive_result.archive_path:
        return archive_result, ExportResult(success=False), UploadResult(success=False)

    # Step 2: Export
    export_result = await harness.export(
        ExportConfig(
            archive_path=archive_result.archive_path,
            export_method=ExportMethod.APP_STORE,
            team_id=team_id,
        )
    )

    if not export_result.success or not export_result.ipa_path:
        return archive_result, export_result, UploadResult(success=False)

    # Step 3: Upload to TestFlight
    upload_result = await harness.upload_to_testflight(
        UploadConfig(
            ipa_path=export_result.ipa_path,
            api_key_id=api_key_id,
            api_issuer_id=api_issuer_id,
            api_key_path=api_key_path,
        )
    )

    return archive_result, export_result, upload_result
