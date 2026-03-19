"""Tests for XcodeGen wrapper."""

from unittest.mock import AsyncMock, patch

import pytest
import yaml

from forge_harness.ios_harness.xcodegen_wrapper import XcodeGenResult, XcodeGenWrapper


@pytest.fixture
def sample_project_yml():
    """Sample project.yml content."""
    return {
        "name": "TestApp",
        "targets": {
            "TestApp": {
                "type": "application",
                "platform": "iOS",
                "sources": [{"path": "App"}],
            }
        },
    }


class TestXcodeGenWrapper:
    """Test XcodeGenWrapper class."""

    @pytest.mark.asyncio
    async def test_generate_fails_if_no_project_yml(self, tmp_path):
        """Should fail if project.yml doesn't exist."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        result = await XcodeGenWrapper.generate(project_dir)

        assert not result.success
        assert "project.yml not found" in result.error

    @pytest.mark.asyncio
    async def test_generate_calls_xcodegen(self, tmp_path, sample_project_yml):
        """Should call xcodegen command."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Create project.yml
        project_yml = project_dir / "project.yml"
        with open(project_yml, "w") as f:
            yaml.dump(sample_project_yml, f)

        # Mock subprocess
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Success", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await XcodeGenWrapper.generate(project_dir)

            # Verify xcodegen was called
            mock_exec.assert_called_once()
            call_args = mock_exec.call_args
            assert call_args[0][0] == "xcodegen"
            assert call_args[0][1] == "generate"

    @pytest.mark.asyncio
    async def test_generate_handles_xcodegen_not_found(self, tmp_path, sample_project_yml):
        """Should handle xcodegen not being installed."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Create project.yml
        project_yml = project_dir / "project.yml"
        with open(project_yml, "w") as f:
            yaml.dump(sample_project_yml, f)

        # Mock FileNotFoundError
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
            result = await XcodeGenWrapper.generate(project_dir)

            assert not result.success
            assert "xcodegen not found" in result.error
            assert "brew install xcodegen" in result.error

    def test_validate_project_yml_valid(self, tmp_path, sample_project_yml):
        """Should validate correct project.yml."""
        project_yml = tmp_path / "project.yml"
        with open(project_yml, "w") as f:
            yaml.dump(sample_project_yml, f)

        valid, error = XcodeGenWrapper.validate_project_yml(project_yml)

        assert valid
        assert error is None

    def test_validate_project_yml_missing_file(self, tmp_path):
        """Should fail if file doesn't exist."""
        project_yml = tmp_path / "nonexistent.yml"

        valid, error = XcodeGenWrapper.validate_project_yml(project_yml)

        assert not valid
        assert "not found" in error

    def test_validate_project_yml_missing_name(self, tmp_path):
        """Should fail if name field is missing."""
        project_yml = tmp_path / "project.yml"
        with open(project_yml, "w") as f:
            yaml.dump({"targets": {}}, f)

        valid, error = XcodeGenWrapper.validate_project_yml(project_yml)

        assert not valid
        assert "Missing required field: name" in error

    def test_validate_project_yml_missing_targets(self, tmp_path):
        """Should fail if targets field is missing."""
        project_yml = tmp_path / "project.yml"
        with open(project_yml, "w") as f:
            yaml.dump({"name": "Test"}, f)

        valid, error = XcodeGenWrapper.validate_project_yml(project_yml)

        assert not valid
        assert "Missing required field: targets" in error

    def test_validate_project_yml_invalid_yaml(self, tmp_path):
        """Should fail for invalid YAML syntax."""
        project_yml = tmp_path / "project.yml"
        project_yml.write_text("invalid: yaml: syntax:")

        valid, error = XcodeGenWrapper.validate_project_yml(project_yml)

        assert not valid
        assert "Invalid YAML" in error

    def test_create_minimal_project_yml(self):
        """Should create valid minimal project.yml config."""
        config = XcodeGenWrapper.create_minimal_project_yml(
            name="TestApp", bundle_id="com.test.app", ios_version="17.0"
        )

        assert config["name"] == "TestApp"
        assert "TestApp" in config["targets"]
        assert "TestAppTests" in config["targets"]
        assert config["settings"]["base"]["PRODUCT_BUNDLE_IDENTIFIER"] == "com.test.app"
        assert config["options"]["deploymentTarget"]["iOS"] == "17.0"
        assert config["settings"]["base"]["SWIFT_VERSION"] == "6.0"

    def test_create_minimal_project_yml_derives_bundle_prefix(self):
        """Should derive bundle ID prefix from full bundle ID."""
        config = XcodeGenWrapper.create_minimal_project_yml(
            name="TestApp", bundle_id="com.forge.test.app"
        )

        assert config["options"]["bundleIdPrefix"] == "com.forge.test"


class TestXcodeGenResult:
    """Test XcodeGenResult dataclass."""

    def test_result_structure(self):
        """Should store result data correctly."""
        result = XcodeGenResult(success=True, stdout="Generated project", stderr="", error=None)

        assert result.success is True
        assert result.stdout == "Generated project"
        assert result.stderr == ""
        assert result.error is None

    def test_result_with_error(self):
        """Should store error information."""
        result = XcodeGenResult(
            success=False, stdout="", stderr="Error occurred", error="Failed to generate"
        )

        assert result.success is False
        assert result.error == "Failed to generate"
