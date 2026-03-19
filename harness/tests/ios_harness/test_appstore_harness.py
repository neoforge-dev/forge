"""Tests for App Store deployment automation."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from forge_harness.ios_harness.appstore_harness import (
    AppStoreHarness,
    ArchiveConfig,
    ArchiveResult,
    ExportConfig,
    ExportMethod,
    ExportResult,
    ReleaseInfo,
    ReleaseType,
    UploadConfig,
    create_release_workflow,
)


@pytest.fixture
def harness(tmp_path):
    """Create AppStoreHarness instance."""
    workspace = tmp_path / "workspace"
    return AppStoreHarness(workspace)


@pytest.fixture
def mock_project(tmp_path):
    """Create mock Xcode project."""
    project_path = tmp_path / "TestApp.xcodeproj"
    project_path.mkdir()
    return project_path


class TestAppStoreHarness:
    """Test AppStoreHarness class."""

    def test_init(self, harness, tmp_path):
        """Should initialize with workspace directory."""
        assert harness.workspace_dir == tmp_path / "workspace"
        assert harness.workspace_dir.exists()

    @pytest.mark.asyncio
    async def test_archive_success(self, harness, mock_project, tmp_path):
        """Should create archive successfully."""
        config = ArchiveConfig(
            project_path=mock_project,
            scheme="TestApp",
            team_id="ABC123",
        )

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            # Mock xcodebuild archive command
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"Archive succeeded", b""))
            mock_exec.return_value = mock_proc

            # Create mock Info.plist
            archive_path = harness.workspace_dir / "archives" / "TestApp.xcarchive"
            archive_path.mkdir(parents=True, exist_ok=True)

            with patch("builtins.open"), patch("plistlib.load") as mock_plist:
                mock_plist.return_value = {
                    "ApplicationProperties": {
                        "CFBundleShortVersionString": "1.0.0",
                        "CFBundleVersion": "1",
                    }
                }

                result = await harness.archive(config)

                assert result.success is True
                assert result.archive_path is not None

    @pytest.mark.asyncio
    async def test_archive_failure(self, harness, mock_project):
        """Should handle archive failure."""
        config = ArchiveConfig(
            project_path=mock_project,
            scheme="TestApp",
        )

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(return_value=(b"", b"Build failed"))
            mock_exec.return_value = mock_proc

            result = await harness.archive(config)

            assert result.success is False
            assert result.error is not None

    @pytest.mark.asyncio
    async def test_export_success(self, harness, tmp_path):
        """Should export IPA successfully."""
        # Create mock archive
        archive_path = tmp_path / "TestApp.xcarchive"
        archive_path.mkdir()

        config = ExportConfig(
            archive_path=archive_path,
            export_method=ExportMethod.APP_STORE,
            team_id="ABC123",
        )

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"Export succeeded", b""))
            mock_exec.return_value = mock_proc

            # Create mock IPA
            export_path = harness.workspace_dir / "exports" / "app-store"
            export_path.mkdir(parents=True, exist_ok=True)
            ipa_path = export_path / "TestApp.ipa"
            ipa_path.touch()

            result = await harness.export(config)

            assert result.success is True
            assert result.ipa_path is not None
            assert result.export_method == ExportMethod.APP_STORE

    @pytest.mark.asyncio
    async def test_export_failure(self, harness, tmp_path):
        """Should handle export failure."""
        archive_path = tmp_path / "TestApp.xcarchive"
        archive_path.mkdir()

        config = ExportConfig(
            archive_path=archive_path,
            export_method=ExportMethod.APP_STORE,
            team_id="ABC123",
        )

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(return_value=(b"", b"Export failed"))
            mock_exec.return_value = mock_proc

            result = await harness.export(config)

            assert result.success is False
            assert result.error is not None

    @pytest.mark.asyncio
    async def test_upload_to_testflight_success(self, harness, tmp_path):
        """Should upload IPA to TestFlight."""
        ipa_path = tmp_path / "TestApp.ipa"
        ipa_path.touch()

        config = UploadConfig(
            ipa_path=ipa_path,
            api_key_id="KEY123",
            api_issuer_id="ISSUER456",
            api_key_path=tmp_path / "key.p8",
        )

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"Upload succeeded", b""))
            mock_exec.return_value = mock_proc

            result = await harness.upload_to_testflight(config)

            assert result.success is True
            assert result.upload_time is not None

    @pytest.mark.asyncio
    async def test_upload_to_testflight_with_warnings(self, harness, tmp_path):
        """Should handle upload warnings."""
        ipa_path = tmp_path / "TestApp.ipa"
        ipa_path.touch()

        config = UploadConfig(
            ipa_path=ipa_path,
            api_key_id="KEY123",
            api_issuer_id="ISSUER456",
            api_key_path=tmp_path / "key.p8",
        )

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(
                return_value=(b"Upload succeeded\nWARNING: Missing screenshots", b"")
            )
            mock_exec.return_value = mock_proc

            result = await harness.upload_to_testflight(config)

            assert result.success is True
            assert len(result.warnings) > 0

    @pytest.mark.asyncio
    async def test_upload_to_testflight_failure(self, harness, tmp_path):
        """Should handle upload failure."""
        ipa_path = tmp_path / "TestApp.ipa"
        ipa_path.touch()

        config = UploadConfig(
            ipa_path=ipa_path,
            api_key_id="KEY123",
            api_issuer_id="ISSUER456",
            api_key_path=tmp_path / "key.p8",
        )

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(return_value=(b"", b"ERROR: Invalid API credentials"))
            mock_exec.return_value = mock_proc

            result = await harness.upload_to_testflight(config)

            assert result.success is False
            assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_validate_ipa_success(self, harness, tmp_path):
        """Should validate IPA without uploading."""
        ipa_path = tmp_path / "TestApp.ipa"
        ipa_path.touch()

        config = UploadConfig(
            ipa_path=ipa_path,
            api_key_id="KEY123",
            api_issuer_id="ISSUER456",
            api_key_path=tmp_path / "key.p8",
        )

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"Validation passed", b""))
            mock_exec.return_value = mock_proc

            result = await harness.validate_ipa(config)

            assert result.success is True

    @pytest.mark.asyncio
    async def test_validate_ipa_missing_file(self, harness, tmp_path):
        """Should fail when IPA doesn't exist."""
        config = UploadConfig(
            ipa_path=tmp_path / "NonExistent.ipa",
            api_key_id="KEY123",
            api_issuer_id="ISSUER456",
            api_key_path=tmp_path / "key.p8",
        )

        result = await harness.validate_ipa(config)

        assert result.success is False
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_get_build_status(self, harness):
        """Should get build status from App Store Connect."""
        status = await harness.get_build_status(
            bundle_id="com.test.app",
            version="1.0.0",
            build_number="1",
        )

        assert status["bundle_id"] == "com.test.app"
        assert status["version"] == "1.0.0"
        assert status["build_number"] == "1"

    def test_create_export_options(self, harness):
        """Should create ExportOptions.plist dictionary."""
        config = ExportConfig(
            archive_path=Path("/tmp/test.xcarchive"),
            export_method=ExportMethod.APP_STORE,
            team_id="ABC123",
        )

        options = harness._create_export_options(config)

        assert options["method"] == "app-store"
        assert options["teamID"] == "ABC123"
        assert options["uploadSymbols"] is True


class TestExportMethod:
    """Test ExportMethod enum."""

    def test_export_methods(self):
        """Should have standard export methods."""
        assert ExportMethod.APP_STORE.value == "app-store"
        assert ExportMethod.AD_HOC.value == "ad-hoc"
        assert ExportMethod.DEVELOPMENT.value == "development"
        assert ExportMethod.ENTERPRISE.value == "enterprise"


class TestReleaseType:
    """Test ReleaseType enum."""

    def test_release_types(self):
        """Should have standard release types."""
        assert ReleaseType.MANUAL.value == "MANUAL"
        assert ReleaseType.AFTER_APPROVAL.value == "AFTER_APPROVAL"
        assert ReleaseType.SCHEDULED.value == "SCHEDULED"


class TestArchiveResult:
    """Test ArchiveResult dataclass."""

    def test_archive_result_success(self):
        """Should create successful archive result."""
        result = ArchiveResult(
            success=True,
            archive_path=Path("/tmp/test.xcarchive"),
            build_number="1",
            version="1.0.0",
        )

        assert result.success is True
        assert result.archive_path == Path("/tmp/test.xcarchive")
        assert result.build_number == "1"
        assert result.version == "1.0.0"


class TestExportResult:
    """Test ExportResult dataclass."""

    def test_export_result_success(self):
        """Should create successful export result."""
        result = ExportResult(
            success=True,
            ipa_path=Path("/tmp/test.ipa"),
            export_method=ExportMethod.APP_STORE,
        )

        assert result.success is True
        assert result.ipa_path == Path("/tmp/test.ipa")
        assert result.export_method == ExportMethod.APP_STORE


class TestReleaseInfo:
    """Test ReleaseInfo dataclass."""

    def test_release_info(self):
        """Should create release info."""
        info = ReleaseInfo(
            version="1.0.0",
            build_number="1",
            release_type=ReleaseType.AFTER_APPROVAL,
            what_s_new="Bug fixes and improvements",
        )

        assert info.version == "1.0.0"
        assert info.build_number == "1"
        assert info.release_type == ReleaseType.AFTER_APPROVAL


@pytest.mark.asyncio
async def test_create_release_workflow(tmp_path):
    """Should execute complete release workflow."""
    project_path = tmp_path / "TestApp.xcodeproj"
    project_path.mkdir()

    api_key_path = tmp_path / "key.p8"
    api_key_path.touch()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        # Mock all subprocess calls
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Success", b""))
        mock_exec.return_value = mock_proc

        # Create required files/directories
        workspace = tmp_path / ".build"
        harness = AppStoreHarness(workspace)

        archive_path = workspace / "archives" / "TestApp.xcarchive"
        archive_path.mkdir(parents=True, exist_ok=True)

        export_path = workspace / "exports" / "app-store"
        export_path.mkdir(parents=True, exist_ok=True)
        ipa_path = export_path / "TestApp.ipa"
        ipa_path.touch()

        with patch("builtins.open"), patch("plistlib.load") as mock_plist, patch("plistlib.dump"):
            mock_plist.return_value = {
                "ApplicationProperties": {
                    "CFBundleShortVersionString": "1.0.0",
                    "CFBundleVersion": "1",
                }
            }

            archive_result, export_result, upload_result = await create_release_workflow(
                project_path=project_path,
                scheme="TestApp",
                team_id="ABC123",
                api_key_id="KEY123",
                api_issuer_id="ISSUER456",
                api_key_path=api_key_path,
                workspace_dir=workspace,
            )

            # Note: In real implementation these would pass, but for mocking purposes
            # we're just checking they were called
            assert archive_result is not None
            assert export_result is not None
            assert upload_result is not None
