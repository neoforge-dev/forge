"""Tests for iOS build harness."""

from unittest.mock import AsyncMock, patch

import pytest

from forge_harness.ios_harness.build_harness import (
    BuildConfig,
    BuildConfiguration,
    BuildError,
    BuildHarness,
    BuildResult,
)


@pytest.fixture
def project_dir(tmp_path):
    """Create temporary iOS project directory."""
    project = tmp_path / "TestApp"
    project.mkdir()

    # Create .xcodeproj
    xcodeproj = project / "TestApp.xcodeproj"
    xcodeproj.mkdir()
    (xcodeproj / "project.pbxproj").write_text("// Xcode project")

    return project


@pytest.fixture
def build_harness(project_dir):
    """Create BuildHarness instance."""
    return BuildHarness(project_dir)


class TestBuildHarness:
    """Test BuildHarness class."""

    def test_init_validates_project_exists(self, tmp_path):
        """Should raise error if project doesn't exist."""
        with pytest.raises(ValueError, match="Project path does not exist"):
            BuildHarness(tmp_path / "nonexistent")

    def test_init_validates_xcodeproj_exists(self, tmp_path):
        """Should raise error if no .xcodeproj found."""
        project = tmp_path / "TestApp"
        project.mkdir()

        with pytest.raises(ValueError, match="No .xcodeproj found"):
            BuildHarness(project)

    def test_init_finds_xcodeproj(self, project_dir):
        """Should find and store .xcodeproj path."""
        harness = BuildHarness(project_dir)

        assert harness.xcodeproj_path == project_dir / "TestApp.xcodeproj"

    @pytest.mark.asyncio
    async def test_build_success(self, build_harness):
        """Should build successfully and return result."""
        config = BuildConfig(scheme="TestApp")

        # Mock subprocess
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"** BUILD SUCCEEDED **\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await build_harness.build(config)

            assert result.success is True
            assert result.exit_code == 0
            assert result.error_count == 0
            assert result.build_time_seconds >= 0  # Can be 0 for mocked builds

    @pytest.mark.asyncio
    async def test_build_failure(self, build_harness):
        """Should handle build failure."""
        config = BuildConfig(scheme="TestApp")

        # Mock subprocess with failure
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"** BUILD FAILED **\nSome error\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await build_harness.build(config)

            assert result.success is False
            assert result.exit_code == 1
            assert result.error_count > 0

    @pytest.mark.asyncio
    async def test_build_xcodebuild_not_found(self, build_harness):
        """Should handle xcodebuild not being installed."""
        config = BuildConfig(scheme="TestApp")

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
            result = await build_harness.build(config)

            assert result.success is False
            assert len(result.errors) > 0
            assert "xcodebuild not found" in result.errors[0].message

    def test_build_command_basic(self, build_harness):
        """Should construct basic build command."""
        config = BuildConfig(
            scheme="TestApp",
            configuration=BuildConfiguration.DEBUG,
            clean_build=False,
            enable_strict_concurrency=False,
        )

        cmd = build_harness._build_command(config)

        assert cmd[0] == "xcodebuild"
        assert "build-for-testing" in cmd
        assert "-scheme" in cmd
        assert "TestApp" in cmd
        assert "-configuration" in cmd
        assert "Debug" in cmd

    def test_build_command_with_strict_concurrency(self, build_harness):
        """Should add strict concurrency flags."""
        config = BuildConfig(scheme="TestApp", enable_strict_concurrency=True)

        cmd = build_harness._build_command(config)

        assert "-Xswiftc" in cmd
        assert "-strict-concurrency=complete" in cmd
        assert "-enable-actor-data-race-checks" in cmd

    def test_build_command_with_clean(self, build_harness):
        """Should include clean command."""
        config = BuildConfig(scheme="TestApp", clean_build=True)

        cmd = build_harness._build_command(config)

        assert "clean" in cmd

    def test_build_command_release_configuration(self, build_harness):
        """Should use Release configuration."""
        config = BuildConfig(scheme="TestApp", configuration=BuildConfiguration.RELEASE)

        cmd = build_harness._build_command(config)

        assert "Release" in cmd

    def test_parse_build_output_errors(self, build_harness):
        """Should extract build errors from output."""
        output = """
/path/to/File.swift:42:10: error: Cannot find 'foo' in scope
/path/to/Other.swift:100:5: error: Type mismatch
        """

        errors, warnings = build_harness._parse_build_output(output)

        assert len(errors) == 2
        assert errors[0].file_path == "/path/to/File.swift"
        assert errors[0].line == 42
        assert "Cannot find 'foo'" in errors[0].message

    def test_parse_build_output_warnings(self, build_harness):
        """Should extract build warnings from output."""
        output = """
/path/to/File.swift:10:5: warning: Variable 'x' was never used
        """

        errors, warnings = build_harness._parse_build_output(output)

        assert len(errors) == 0
        assert len(warnings) == 1
        assert warnings[0].file_path == "/path/to/File.swift"
        assert warnings[0].line == 10

    def test_parse_build_output_build_failed(self, build_harness):
        """Should detect generic BUILD FAILED message."""
        output = """
** BUILD FAILED **
        """

        errors, warnings = build_harness._parse_build_output(output)

        assert len(errors) > 0
        assert "Build failed" in errors[0].message

    @pytest.mark.asyncio
    async def test_list_schemes(self, build_harness):
        """Should list available schemes."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(
                b'{"project": {"schemes": ["TestApp", "TestAppDebug"]}}',
                b"",
            )
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            schemes = await build_harness.list_schemes()

            assert len(schemes) == 2
            assert "TestApp" in schemes
            assert "TestAppDebug" in schemes

    @pytest.mark.asyncio
    async def test_clean(self, build_harness):
        """Should clean build artifacts."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            success = await build_harness.clean()

            assert success is True


class TestBuildConfig:
    """Test BuildConfig dataclass."""

    def test_defaults(self):
        """Should have sensible defaults."""
        config = BuildConfig(scheme="TestApp")

        assert config.configuration == BuildConfiguration.DEBUG
        assert config.enable_strict_concurrency is True
        assert config.enable_testing is True
        assert config.clean_build is True


class TestBuildResult:
    """Test BuildResult dataclass."""

    def test_error_count(self):
        """Should count errors."""
        result = BuildResult(
            success=False,
            errors=[
                BuildError("file.swift", 10, "Error 1", "error"),
                BuildError("file.swift", 20, "Error 2", "error"),
            ],
        )

        assert result.error_count == 2

    def test_warning_count(self):
        """Should count warnings."""
        result = BuildResult(
            success=True,
            warnings=[
                BuildError("file.swift", 10, "Warning 1", "warning"),
            ],
        )

        assert result.warning_count == 1


class TestDryRunMode:
    """Test dry-run/simulation mode for builds."""

    @pytest.fixture
    def project_dir(self, tmp_path):
        """Create temporary iOS project directory."""
        project = tmp_path / "TestApp"
        project.mkdir()

        # Create .xcodeproj
        xcodeproj = project / "TestApp.xcodeproj"
        xcodeproj.mkdir()
        (xcodeproj / "project.pbxproj").write_text("// Xcode project")

        return project

    @pytest.fixture
    def build_harness(self, project_dir):
        """Create BuildHarness instance."""
        return BuildHarness(project_dir)

    @pytest.mark.asyncio
    async def test_dry_run_returns_simulated_success(self, build_harness):
        """Dry-run mode should return simulated success without actual build."""
        config = BuildConfig(
            scheme="TestApp",
            dry_run=True,
            simulation_build_time=5.0,
        )

        # No mocking needed - should not call xcodebuild
        result = await build_harness.build(config)

        assert result.success is True
        assert result.is_simulation is True
        assert result.build_time_seconds == 5.0
        assert result.exit_code == 0
        assert result.error_count == 0
        assert result.warning_count == 0
        assert "[DRY-RUN]" in result.stdout
        assert "TestApp" in result.stdout

    @pytest.mark.asyncio
    async def test_dry_run_returns_simulation_artifacts(self, build_harness):
        """Dry-run mode should return configured simulation artifacts."""
        config = BuildConfig(
            scheme="TestApp",
            dry_run=True,
            simulation_artifacts=["TestApp.app", "TestApp.ipa"],
        )

        result = await build_harness.build(config)

        assert result.is_simulation is True
        assert len(result.artifacts) == 2
        assert "TestApp.app" in result.artifacts
        assert "TestApp.ipa" in result.artifacts

    @pytest.mark.asyncio
    async def test_dry_run_does_not_execute_xcodebuild(self, build_harness):
        """Dry-run mode should not execute xcodebuild."""
        config = BuildConfig(
            scheme="TestApp",
            dry_run=True,
        )

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            result = await build_harness.build(config)

            # xcodebuild should not be called in dry-run mode
            mock_exec.assert_not_called()
            assert result.is_simulation is True

    @pytest.mark.asyncio
    async def test_dry_run_preserves_config_info_in_output(self, build_harness):
        """Dry-run output should reflect the build configuration."""
        config = BuildConfig(
            scheme="MyCustomScheme",
            configuration=BuildConfiguration.RELEASE,
            dry_run=True,
        )

        result = await build_harness.build(config)

        assert "MyCustomScheme" in result.stdout
        assert "Release" in result.stdout

    @pytest.mark.asyncio
    async def test_real_build_not_simulation(self, build_harness):
        """Real builds should not be marked as simulation."""
        config = BuildConfig(
            scheme="TestApp",
            dry_run=False,
        )

        # Mock subprocess for real build
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"** BUILD SUCCEEDED **\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await build_harness.build(config)

            assert result.is_simulation is False
            assert result.success is True

    def test_build_config_dry_run_defaults(self):
        """BuildConfig dry_run should default to False."""
        config = BuildConfig(scheme="TestApp")

        assert config.dry_run is False
        assert config.simulation_artifacts == []
        assert config.simulation_build_time == 0.0

    def test_build_result_simulation_defaults(self):
        """BuildResult is_simulation should default to False."""
        result = BuildResult(success=True)

        assert result.is_simulation is False
        assert result.artifacts == []
