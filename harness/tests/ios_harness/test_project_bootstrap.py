"""Tests for iOS project bootstrap."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from forge_harness.ios_harness.project_bootstrap import (
    FeatureType,
    ProjectBootstrap,
    ProjectSpec,
    select_template,
)


@pytest.fixture
def forge_root(tmp_path):
    """Create temporary FORGE root directory."""
    forge_dir = tmp_path / "FORGE"
    forge_dir.mkdir()
    return forge_dir


@pytest.fixture
def templates_dir(tmp_path):
    """Create temporary templates directory with base template."""
    templates = tmp_path / "templates" / "ios"
    templates.mkdir(parents=True)

    # Create minimal base-swiftui template
    base_template = templates / "base-swiftui"
    base_template.mkdir()

    # Create App directory with minimal files
    app_dir = base_template / "App"
    app_dir.mkdir()
    (app_dir / "ContentView.swift").write_text("// ContentView")
    (app_dir / "App.swift").write_text("// App")

    # Create App/Core/Config directory (for AppConfig.swift)
    config_dir = app_dir / "Core" / "Config"
    config_dir.mkdir(parents=True)

    # Create project.yml
    (base_template / "project.yml").write_text("name: BaseApp")

    # Create CLAUDE.md (will be overwritten by bootstrap)
    (base_template / "CLAUDE.md").write_text("# Template CLAUDE.md")

    return templates


@pytest.fixture
def bootstrap(forge_root, templates_dir):
    """Create ProjectBootstrap instance."""
    return ProjectBootstrap(forge_root, templates_dir)


@pytest.fixture
def sample_spec():
    """Create sample project spec."""
    return ProjectSpec(
        name="TestApp",
        bundle_id="com.forge.test.testapp",
        domain="codeswiftr-com",
        template="base-swiftui",
        features=["auth"],
        backend_url="https://api.test.com",
    )


class TestProjectSpec:
    """Test ProjectSpec dataclass."""

    def test_spec_defaults(self):
        """Test default values are set correctly."""
        spec = ProjectSpec(
            name="Test",
            bundle_id="com.test",
            domain="test-domain",
            template="base-swiftui",
            features=[],
            backend_url="https://api.test.com",
        )

        assert spec.deployment_target == "17.0"
        assert spec.swift_version == "6.0"
        assert spec.enable_swiftdata is True
        assert spec.enable_coppa_compliance is False
        assert spec.marketing_version == "0.1.0"
        assert spec.build_number == 1


class TestTemplateSelection:
    """Test template selection logic."""

    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    def test_select_enterprise_for_coppa(self, mock_config):
        """Should select enterprise template for COPPA-required domains."""
        mock_domain_config = Mock()
        mock_domain_config.coppa_required = True
        mock_domain_config.hipaa_lite = False
        mock_config.return_value = mock_domain_config

        result = select_template("mvp", "thebrightharbor-com")

        assert result == "enterprise"

    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    def test_select_enterprise_for_hipaa(self, mock_config):
        """Should select enterprise template for HIPAA-lite domains."""
        mock_domain_config = Mock()
        mock_domain_config.coppa_required = False
        mock_domain_config.hipaa_lite = True
        mock_config.return_value = mock_domain_config

        result = select_template("mvp", "calmconnect-io")

        assert result == "enterprise"

    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    def test_select_base_for_prototype(self, mock_config):
        """Should select base template for prototypes."""
        mock_domain_config = Mock()
        mock_domain_config.coppa_required = False
        mock_domain_config.hipaa_lite = False
        mock_config.return_value = mock_domain_config

        result = select_template("prototype", "test-domain")

        assert result == "base-swiftui"

    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    def test_select_mvvm_standard_default(self, mock_config):
        """Should select mvvm-standard for regular MVPs."""
        mock_domain_config = Mock()
        mock_domain_config.coppa_required = False
        mock_domain_config.hipaa_lite = False
        mock_config.return_value = mock_domain_config

        result = select_template("mvp", "codeswiftr-com")

        assert result == "mvvm-standard"


class TestProjectBootstrap:
    """Test ProjectBootstrap class."""

    def test_init_validates_templates_dir(self, forge_root):
        """Should raise error if templates directory doesn't exist."""
        with pytest.raises(ValueError, match="Templates directory not found"):
            ProjectBootstrap(forge_root, forge_root / "nonexistent")

    def test_init_uses_default_templates_dir(self, forge_root):
        """Should use default templates directory if not provided."""
        # Create default templates dir
        default_templates = forge_root / "harness" / "templates" / "ios"
        default_templates.mkdir(parents=True)

        bootstrap = ProjectBootstrap(forge_root)

        assert bootstrap.templates_dir == default_templates

    @pytest.mark.asyncio
    async def test_bootstrap_project_creates_directory(self, bootstrap, sample_spec, tmp_path):
        """Should create output directory if it doesn't exist."""
        output_dir = tmp_path / "output"

        with patch.object(bootstrap, "_run_xcodegen", new_callable=AsyncMock) as mock_xcodegen:
            mock_xcodegen.return_value = {
                "success": True,
                "stdout": "",
                "stderr": "",
                "error": None,
            }

            result = await bootstrap.bootstrap_project(spec=sample_spec, output_dir=output_dir)

            assert output_dir.exists()

    @pytest.mark.asyncio
    async def test_bootstrap_project_fails_if_exists_no_overwrite(
        self, bootstrap, sample_spec, tmp_path
    ):
        """Should fail if output directory exists and overwrite=False."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result = await bootstrap.bootstrap_project(
            spec=sample_spec, output_dir=output_dir, overwrite=False
        )

        assert not result.xcodegen_success
        assert len(result.errors) > 0
        assert "already exists" in result.errors[0]

    @pytest.mark.asyncio
    async def test_bootstrap_project_generates_project_yml(self, bootstrap, sample_spec, tmp_path):
        """Should generate valid project.yml file."""
        output_dir = tmp_path / "output"

        with patch.object(bootstrap, "_run_xcodegen", new_callable=AsyncMock) as mock_xcodegen:
            mock_xcodegen.return_value = {
                "success": True,
                "stdout": "",
                "stderr": "",
                "error": None,
            }

            result = await bootstrap.bootstrap_project(spec=sample_spec, output_dir=output_dir)

            project_yml = output_dir / "project.yml"
            assert project_yml.exists()
            assert "project.yml" in [Path(f).name for f in result.created_files]

    @pytest.mark.asyncio
    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    async def test_bootstrap_project_generates_claude_md(
        self, mock_domain_config, bootstrap, sample_spec, tmp_path
    ):
        """Should generate CLAUDE.md file."""
        # Mock domain config
        mock_config = Mock()
        mock_config.coppa_required = False
        mock_config.hipaa_lite = False
        mock_domain_config.return_value = mock_config

        output_dir = tmp_path / "output"

        with patch.object(bootstrap, "_run_xcodegen", new_callable=AsyncMock) as mock_xcodegen:
            mock_xcodegen.return_value = {
                "success": True,
                "stdout": "",
                "stderr": "",
                "error": None,
            }

            result = await bootstrap.bootstrap_project(spec=sample_spec, output_dir=output_dir)

            claude_md = output_dir / "CLAUDE.md"
            assert claude_md.exists()

            content = claude_md.read_text()
            assert "Goals:" in content
            assert sample_spec.backend_url in content

    @pytest.mark.asyncio
    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    async def test_bootstrap_project_generates_app_config(
        self, mock_domain_config, bootstrap, sample_spec, tmp_path
    ):
        """Should generate AppConfig.swift file."""
        # Mock domain config
        mock_config = Mock()
        mock_config.coppa_required = False
        mock_config.hipaa_lite = False
        mock_domain_config.return_value = mock_config

        output_dir = tmp_path / "output"

        with patch.object(bootstrap, "_run_xcodegen", new_callable=AsyncMock) as mock_xcodegen:
            mock_xcodegen.return_value = {
                "success": True,
                "stdout": "",
                "stderr": "",
                "error": None,
            }

            result = await bootstrap.bootstrap_project(spec=sample_spec, output_dir=output_dir)

            app_config = output_dir / "App" / "Core" / "Config" / "AppConfig.swift"
            assert app_config.exists()

            content = app_config.read_text()
            assert sample_spec.backend_url in content
            assert sample_spec.bundle_id in content

    def test_generate_project_yml_structure(self, bootstrap, sample_spec):
        """Should generate valid project.yml structure."""
        config = bootstrap._generate_project_yml(sample_spec)

        assert config["name"] == sample_spec.name
        assert sample_spec.name in config["targets"]
        assert f"{sample_spec.name}Tests" in config["targets"]
        assert config["settings"]["base"]["PRODUCT_BUNDLE_IDENTIFIER"] == sample_spec.bundle_id

    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    def test_generate_claude_md_includes_coppa_rules(self, mock_config, bootstrap, sample_spec):
        """Should include COPPA rules in CLAUDE.md for COPPA domains."""
        mock_domain_config = Mock()
        mock_domain_config.coppa_required = True
        mock_domain_config.hipaa_lite = False
        mock_config.return_value = mock_domain_config

        claude_md = bootstrap._generate_claude_md(sample_spec, mock_domain_config)

        assert "COPPA Compliance" in claude_md
        assert "age gate" in claude_md.lower()
        assert "parental consent" in claude_md.lower()

    def test_validate_feature_valid(self, bootstrap):
        """Should validate known feature types."""
        assert bootstrap._validate_feature("auth") == FeatureType.AUTH
        assert bootstrap._validate_feature("dashboard") == FeatureType.DASHBOARD
        assert bootstrap._validate_feature("settings") == FeatureType.SETTINGS

    def test_validate_feature_invalid(self, bootstrap):
        """Should return None for unknown feature types."""
        assert bootstrap._validate_feature("unknown") is None


class TestFeatureScaffolding:
    """Test feature scaffolding functionality."""

    @pytest.mark.asyncio
    async def test_scaffold_feature_creates_directory(self, bootstrap, tmp_path):
        """Should create feature directory."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        result = await bootstrap.scaffold_feature("auth", project_dir)

        feature_dir = project_dir / "App" / "Features" / "Auth"
        assert feature_dir.exists()

    @pytest.mark.asyncio
    async def test_scaffold_feature_unknown_type_fails(self, bootstrap, tmp_path):
        """Should fail for unknown feature types."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        result = await bootstrap.scaffold_feature("unknown_feature", project_dir)

        assert not result.success
        assert len(result.errors) > 0
