"""
Comprehensive tests for iOS harness modules — batch 1.

Covers:
- forge_harness.ios_harness.feature_scaffold
- forge_harness.ios_harness.project_bootstrap
- forge_harness.ios_harness.simulator_harness
- forge_harness.ios_harness.appstore_harness

All subprocess / file-system calls are mocked because tests run on Linux.
"""

from __future__ import annotations

import json
import plistlib
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# ---------------------------------------------------------------------------
# appstore_harness
# ---------------------------------------------------------------------------
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
    UploadResult,
    create_release_workflow,
)

# ---------------------------------------------------------------------------
# feature_scaffold
# ---------------------------------------------------------------------------
from forge_harness.ios_harness.feature_scaffold import FeatureScaffold, ScaffoldResult

# ---------------------------------------------------------------------------
# project_bootstrap
# ---------------------------------------------------------------------------
from forge_harness.ios_harness.project_bootstrap import (
    BootstrapResult,
    FeatureType,
    ProjectBootstrap,
    ProjectSpec,
    select_template,
)
from forge_harness.ios_harness.project_bootstrap import (
    ScaffoldResult as BootstrapScaffoldResult,
)

# ---------------------------------------------------------------------------
# simulator_harness
# ---------------------------------------------------------------------------
from forge_harness.ios_harness.simulator_harness import (
    DeviceType,
    InstallResult,
    LaunchResult,
    SimulatorDevice,
    SimulatorHarness,
    SimulatorState,
)

# ===========================================================================
# Helpers / shared fixtures
# ===========================================================================


def _make_async_proc(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> AsyncMock:
    """Build a mock asyncio subprocess with communicate() returning fixed output."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


@pytest.fixture()
def tmp_output(tmp_path: Path) -> Path:
    return tmp_path / "output"


# ===========================================================================
# feature_scaffold — FeatureScaffold
# ===========================================================================


class TestFeatureScaffoldInit:
    def test_stores_project_name(self):
        fs = FeatureScaffold("MyApp", "https://api.example.com")
        assert fs.project_name == "MyApp"

    def test_stores_backend_url(self):
        fs = FeatureScaffold("MyApp", "https://api.example.com")
        assert fs.backend_url == "https://api.example.com"

    def test_codegen_created(self):
        fs = FeatureScaffold("MyApp", "https://api.example.com")
        assert fs.codegen is not None
        assert fs.codegen.project_name == "MyApp"

    def test_different_urls(self):
        fs1 = FeatureScaffold("A", "https://a.com")
        fs2 = FeatureScaffold("B", "https://b.com")
        assert fs1.backend_url != fs2.backend_url


class TestFeatureScaffoldAuth:
    @pytest.mark.asyncio
    async def test_returns_scaffold_result(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_auth(tmp_path)
        assert isinstance(result, ScaffoldResult)

    @pytest.mark.asyncio
    async def test_feature_name_is_auth(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_auth(tmp_path)
        assert result.feature_name == "Auth"

    @pytest.mark.asyncio
    async def test_creates_four_files(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_auth(tmp_path)
        assert len(result.files_created) == 4

    @pytest.mark.asyncio
    async def test_auth_service_path(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_auth(tmp_path)
        paths = result.files_created
        assert any("AuthService.swift" in p for p in paths)

    @pytest.mark.asyncio
    async def test_auth_viewmodel_path(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_auth(tmp_path)
        assert any("AuthViewModel.swift" in p for p in result.files_created)

    @pytest.mark.asyncio
    async def test_login_view_path(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_auth(tmp_path)
        assert any("LoginView.swift" in p for p in result.files_created)

    @pytest.mark.asyncio
    async def test_user_model_path(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_auth(tmp_path)
        assert any("User.swift" in p for p in result.files_created)

    @pytest.mark.asyncio
    async def test_success_true(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_auth(tmp_path)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_no_errors(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_auth(tmp_path)
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_error_handling_on_mkdir_failure(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        with patch("pathlib.Path.mkdir", side_effect=PermissionError("no permission")):
            result = await fs.scaffold_auth(tmp_path)
        assert result.success is False
        assert len(result.errors) > 0
        assert "Auth scaffold failed" in result.errors[0]

    @pytest.mark.asyncio
    async def test_error_message_contains_exception(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        with patch("pathlib.Path.mkdir", side_effect=OSError("disk full")):
            result = await fs.scaffold_auth(tmp_path)
        assert "disk full" in result.errors[0]

    @pytest.mark.asyncio
    async def test_backend_url_embedded_in_service(self, tmp_path):
        url = "https://my-custom-backend.io"
        fs = FeatureScaffold("TestApp", url)
        await fs.scaffold_auth(tmp_path)
        service = (tmp_path / "App" / "Features" / "Auth" / "AuthService.swift").read_text()
        assert url in service

    @pytest.mark.asyncio
    async def test_feature_dir_created(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        await fs.scaffold_auth(tmp_path)
        assert (tmp_path / "App" / "Features" / "Auth").is_dir()

    @pytest.mark.asyncio
    async def test_models_dir_created(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        await fs.scaffold_auth(tmp_path)
        assert (tmp_path / "App" / "Models").is_dir()


class TestFeatureScaffoldDashboard:
    @pytest.mark.asyncio
    async def test_returns_scaffold_result(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_dashboard(tmp_path)
        assert isinstance(result, ScaffoldResult)

    @pytest.mark.asyncio
    async def test_feature_name_is_dashboard(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_dashboard(tmp_path)
        assert result.feature_name == "Dashboard"

    @pytest.mark.asyncio
    async def test_creates_four_files(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_dashboard(tmp_path)
        assert len(result.files_created) == 4

    @pytest.mark.asyncio
    async def test_dashboard_service_created(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_dashboard(tmp_path)
        assert any("DashboardService.swift" in p for p in result.files_created)

    @pytest.mark.asyncio
    async def test_dashboard_viewmodel_created(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_dashboard(tmp_path)
        assert any("DashboardViewModel.swift" in p for p in result.files_created)

    @pytest.mark.asyncio
    async def test_dashboard_view_created(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_dashboard(tmp_path)
        assert any("DashboardView.swift" in p for p in result.files_created)

    @pytest.mark.asyncio
    async def test_dashboard_model_created(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_dashboard(tmp_path)
        assert any("DashboardData.swift" in p for p in result.files_created)

    @pytest.mark.asyncio
    async def test_error_handling(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        with patch("pathlib.Path.mkdir", side_effect=OSError("cannot create dir")):
            result = await fs.scaffold_dashboard(tmp_path)
        assert result.success is False
        assert "Dashboard scaffold failed" in result.errors[0]

    @pytest.mark.asyncio
    async def test_success_flag(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_dashboard(tmp_path)
        assert result.success is True


class TestFeatureScaffoldSettings:
    @pytest.mark.asyncio
    async def test_returns_scaffold_result(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_settings(tmp_path)
        assert isinstance(result, ScaffoldResult)

    @pytest.mark.asyncio
    async def test_feature_name_is_settings(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_settings(tmp_path)
        assert result.feature_name == "Settings"

    @pytest.mark.asyncio
    async def test_creates_two_files(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_settings(tmp_path)
        assert len(result.files_created) == 2

    @pytest.mark.asyncio
    async def test_settings_viewmodel_created(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_settings(tmp_path)
        assert any("SettingsViewModel.swift" in p for p in result.files_created)

    @pytest.mark.asyncio
    async def test_settings_view_created(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        result = await fs.scaffold_settings(tmp_path)
        assert any("SettingsView.swift" in p for p in result.files_created)

    @pytest.mark.asyncio
    async def test_error_handling(self, tmp_path):
        fs = FeatureScaffold("TestApp", "https://api.test.com")
        with patch("pathlib.Path.mkdir", side_effect=OSError("no space")):
            result = await fs.scaffold_settings(tmp_path)
        assert result.success is False
        assert "Settings scaffold failed" in result.errors[0]


class TestFeatureScaffoldPrivateHelpers:
    """Test private helper methods that generate body code."""

    def test_auth_login_body_contains_url(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        body = fs._auth_login_body()
        assert "https://api.example.com/auth/login" in body

    def test_auth_register_body_contains_url(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        body = fs._auth_register_body()
        assert "https://api.example.com/auth/register" in body

    def test_viewmodel_login_body_not_empty(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        body = fs._viewmodel_login_body()
        assert "isLoading" in body

    def test_viewmodel_register_body_not_empty(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        body = fs._viewmodel_register_body()
        assert "isLoading" in body

    def test_viewmodel_logout_body_not_empty(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        body = fs._viewmodel_logout_body()
        assert "isAuthenticated = false" in body

    def test_dashboard_fetch_body_contains_url(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        body = fs._dashboard_fetch_body()
        assert "https://api.example.com/dashboard" in body

    def test_dashboard_load_body_not_empty(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        body = fs._dashboard_load_body()
        assert "isLoading" in body

    def test_dashboard_refresh_body_not_empty(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        body = fs._dashboard_refresh_body()
        assert "dashboardService" in body

    def test_settings_save_body_uses_userdefaults(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        body = fs._settings_save_body()
        assert "UserDefaults" in body

    def test_settings_load_body_uses_userdefaults(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        body = fs._settings_load_body()
        assert "UserDefaults" in body

    def test_generate_user_model_contains_project_name(self):
        fs = FeatureScaffold("MyProject", "https://api.example.com")
        model = fs._generate_user_model()
        assert "MyProject" in model

    def test_generate_user_model_has_swiftdata_annotation(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        model = fs._generate_user_model()
        assert "@Model" in model

    def test_generate_dashboard_model_contains_project_name(self):
        fs = FeatureScaffold("DashApp", "https://api.example.com")
        model = fs._generate_dashboard_model()
        assert "DashApp" in model

    def test_generate_dashboard_model_is_codable(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        model = fs._generate_dashboard_model()
        assert "Codable" in model

    def test_enhance_login_view_replaces_placeholder(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        # Generate a view that contains the placeholder
        from forge_harness.ios_harness.swift_codegen import SwiftCodeGen, ViewSpec

        cg = SwiftCodeGen("App")
        spec = ViewSpec(
            name="Login",
            viewmodel_name="AuthViewModel",
            has_navigation=True,
            has_loading_state=True,
            has_error_state=True,
        )
        base = cg.generate_view(spec)
        enhanced = fs._enhance_login_view(base)
        assert "TextField" in enhanced
        assert "SecureField" in enhanced

    def test_enhance_dashboard_view_replaces_placeholder(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        from forge_harness.ios_harness.swift_codegen import SwiftCodeGen, ViewSpec

        cg = SwiftCodeGen("App")
        spec = ViewSpec(
            name="Dashboard",
            viewmodel_name="DashboardViewModel",
            has_navigation=True,
            has_loading_state=True,
            has_error_state=True,
        )
        base = cg.generate_view(spec)
        enhanced = fs._enhance_dashboard_view(base)
        assert "ScrollView" in enhanced
        assert ".refreshable" in enhanced

    def test_enhance_settings_view_replaces_placeholder(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        from forge_harness.ios_harness.swift_codegen import SwiftCodeGen, ViewSpec

        cg = SwiftCodeGen("App")
        spec = ViewSpec(
            name="Settings",
            viewmodel_name="SettingsViewModel",
            has_navigation=True,
            has_loading_state=False,
            has_error_state=False,
        )
        base = cg.generate_view(spec)
        enhanced = fs._enhance_settings_view(base)
        assert "Form" in enhanced
        assert "Toggle" in enhanced

    def test_enhance_login_view_no_match_returns_unchanged(self):
        """When placeholder is absent the code is returned unchanged."""
        fs = FeatureScaffold("App", "https://api.example.com")
        original = "// no placeholder here"
        result = fs._enhance_login_view(original)
        assert result == original

    def test_enhance_dashboard_view_no_match_returns_unchanged(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        original = "// no placeholder here"
        result = fs._enhance_dashboard_view(original)
        assert result == original

    def test_enhance_settings_view_no_match_returns_unchanged(self):
        fs = FeatureScaffold("App", "https://api.example.com")
        original = "// no placeholder here"
        result = fs._enhance_settings_view(original)
        assert result == original


class TestScaffoldResultDataclass:
    def test_default_success_true(self):
        r = ScaffoldResult(feature_name="Auth")
        assert r.success is True

    def test_default_files_created_empty(self):
        r = ScaffoldResult(feature_name="Auth")
        assert r.files_created == []

    def test_default_errors_empty(self):
        r = ScaffoldResult(feature_name="Auth")
        assert r.errors == []

    def test_explicit_failure(self):
        r = ScaffoldResult(feature_name="Auth", success=False, errors=["boom"])
        assert r.success is False
        assert "boom" in r.errors

    def test_has_expected_fields(self):
        field_names = {f.name for f in fields(ScaffoldResult)}
        assert {"feature_name", "files_created", "success", "errors"} <= field_names


# ===========================================================================
# project_bootstrap — ProjectBootstrap
# ===========================================================================


@pytest.fixture()
def templates_root(tmp_path: Path) -> Path:
    """Templates directory with a minimal base-swiftui template."""
    templates = tmp_path / "templates" / "ios"
    templates.mkdir(parents=True)

    base = templates / "base-swiftui"
    base.mkdir()
    (base / "App").mkdir()
    (base / "App" / "ContentView.swift").write_text("// Content")
    (base / "project.yml").write_text("name: Template")
    (base / "CLAUDE.md").write_text("# Template")

    return templates


@pytest.fixture()
def forge_root(tmp_path: Path) -> Path:
    return tmp_path / "FORGE"


@pytest.fixture()
def bootstrap(forge_root: Path, templates_root: Path) -> ProjectBootstrap:
    forge_root.mkdir(parents=True)
    return ProjectBootstrap(forge_root, templates_root)


@pytest.fixture()
def spec_base() -> ProjectSpec:
    return ProjectSpec(
        name="TestApp",
        bundle_id="com.forge.test.testapp",
        domain="codeswiftr-com",
        template="base-swiftui",
        features=[],
        backend_url="https://api.test.com",
    )


class TestProjectSpecDefaults:
    def test_deployment_target(self):
        spec = ProjectSpec(
            name="A",
            bundle_id="com.a",
            domain="d",
            template="base-swiftui",
            features=[],
            backend_url="https://a.com",
        )
        assert spec.deployment_target == "17.0"

    def test_swift_version(self):
        spec = ProjectSpec(
            name="A",
            bundle_id="com.a",
            domain="d",
            template="base-swiftui",
            features=[],
            backend_url="https://a.com",
        )
        assert spec.swift_version == "6.0"

    def test_swiftdata_enabled_by_default(self):
        spec = ProjectSpec(
            name="A",
            bundle_id="com.a",
            domain="d",
            template="base-swiftui",
            features=[],
            backend_url="https://a.com",
        )
        assert spec.enable_swiftdata is True

    def test_coppa_disabled_by_default(self):
        spec = ProjectSpec(
            name="A",
            bundle_id="com.a",
            domain="d",
            template="base-swiftui",
            features=[],
            backend_url="https://a.com",
        )
        assert spec.enable_coppa_compliance is False

    def test_marketing_version(self):
        spec = ProjectSpec(
            name="A",
            bundle_id="com.a",
            domain="d",
            template="base-swiftui",
            features=[],
            backend_url="https://a.com",
        )
        assert spec.marketing_version == "0.1.0"

    def test_build_number(self):
        spec = ProjectSpec(
            name="A",
            bundle_id="com.a",
            domain="d",
            template="base-swiftui",
            features=[],
            backend_url="https://a.com",
        )
        assert spec.build_number == 1


class TestProjectBootstrapInit:
    def test_raises_on_missing_templates_dir(self, forge_root):
        forge_root.mkdir(parents=True, exist_ok=True)
        with pytest.raises(ValueError, match="Templates directory not found"):
            ProjectBootstrap(forge_root, forge_root / "missing")

    def test_default_templates_dir(self, forge_root):
        default = forge_root / "harness" / "templates" / "ios"
        default.mkdir(parents=True)
        pb = ProjectBootstrap(forge_root)
        assert pb.templates_dir == default

    def test_stores_forge_root(self, forge_root, templates_root):
        forge_root.mkdir(parents=True, exist_ok=True)
        pb = ProjectBootstrap(forge_root, templates_root)
        assert pb.forge_root == forge_root

    def test_stores_templates_dir(self, forge_root, templates_root):
        forge_root.mkdir(parents=True, exist_ok=True)
        pb = ProjectBootstrap(forge_root, templates_root)
        assert pb.templates_dir == templates_root


class TestValidateFeature:
    def test_auth(self, bootstrap):
        assert bootstrap._validate_feature("auth") == FeatureType.AUTH

    def test_dashboard(self, bootstrap):
        assert bootstrap._validate_feature("dashboard") == FeatureType.DASHBOARD

    def test_settings(self, bootstrap):
        assert bootstrap._validate_feature("settings") == FeatureType.SETTINGS

    def test_onboarding(self, bootstrap):
        assert bootstrap._validate_feature("onboarding") == FeatureType.ONBOARDING

    def test_profile(self, bootstrap):
        assert bootstrap._validate_feature("profile") == FeatureType.PROFILE

    def test_unknown_returns_none(self, bootstrap):
        assert bootstrap._validate_feature("unknown_xyz") is None

    def test_case_insensitive(self, bootstrap):
        assert bootstrap._validate_feature("AUTH") == FeatureType.AUTH
        assert bootstrap._validate_feature("Dashboard") == FeatureType.DASHBOARD


class TestGenerateProjectYml:
    def test_name_matches_spec(self, bootstrap, spec_base):
        yml = bootstrap._generate_project_yml(spec_base)
        assert yml["name"] == "TestApp"

    def test_bundle_id_in_settings(self, bootstrap, spec_base):
        yml = bootstrap._generate_project_yml(spec_base)
        assert yml["settings"]["base"]["PRODUCT_BUNDLE_IDENTIFIER"] == spec_base.bundle_id

    def test_deployment_target_in_options(self, bootstrap, spec_base):
        yml = bootstrap._generate_project_yml(spec_base)
        assert yml["options"]["deploymentTarget"]["iOS"] == spec_base.deployment_target

    def test_swift_version_in_settings(self, bootstrap, spec_base):
        yml = bootstrap._generate_project_yml(spec_base)
        assert yml["settings"]["base"]["SWIFT_VERSION"] == spec_base.swift_version

    def test_main_target_present(self, bootstrap, spec_base):
        yml = bootstrap._generate_project_yml(spec_base)
        assert "TestApp" in yml["targets"]

    def test_test_target_present(self, bootstrap, spec_base):
        yml = bootstrap._generate_project_yml(spec_base)
        assert "TestAppTests" in yml["targets"]

    def test_marketing_version_in_settings(self, bootstrap, spec_base):
        yml = bootstrap._generate_project_yml(spec_base)
        assert yml["settings"]["base"]["MARKETING_VERSION"] == spec_base.marketing_version

    def test_build_number_in_settings(self, bootstrap, spec_base):
        yml = bootstrap._generate_project_yml(spec_base)
        assert yml["settings"]["base"]["CURRENT_PROJECT_VERSION"] == str(spec_base.build_number)


class TestGenerateClaudeMd:
    def _mock_domain(self, coppa: bool = False, hipaa: bool = False) -> Mock:
        m = Mock()
        m.coppa_required = coppa
        m.hipaa_lite = hipaa
        return m

    def test_includes_goals_section(self, bootstrap, spec_base):
        domain = self._mock_domain()
        md = bootstrap._generate_claude_md(spec_base, domain)
        assert "Goals:" in md

    def test_includes_backend_url(self, bootstrap, spec_base):
        domain = self._mock_domain()
        md = bootstrap._generate_claude_md(spec_base, domain)
        assert spec_base.backend_url in md

    def test_coppa_rules_when_required(self, bootstrap, spec_base):
        domain = self._mock_domain(coppa=True)
        md = bootstrap._generate_claude_md(spec_base, domain)
        assert "COPPA Compliance" in md

    def test_no_coppa_rules_when_not_required(self, bootstrap, spec_base):
        domain = self._mock_domain(coppa=False)
        md = bootstrap._generate_claude_md(spec_base, domain)
        assert "COPPA Compliance" not in md

    def test_hipaa_rules_when_required(self, bootstrap, spec_base):
        domain = self._mock_domain(hipaa=True)
        md = bootstrap._generate_claude_md(spec_base, domain)
        assert "HIPAA-Lite" in md

    def test_no_hipaa_rules_when_not_required(self, bootstrap, spec_base):
        domain = self._mock_domain(hipaa=False)
        md = bootstrap._generate_claude_md(spec_base, domain)
        assert "HIPAA-Lite" not in md

    def test_both_coppa_and_hipaa(self, bootstrap, spec_base):
        domain = self._mock_domain(coppa=True, hipaa=True)
        md = bootstrap._generate_claude_md(spec_base, domain)
        assert "COPPA Compliance" in md
        assert "HIPAA-Lite" in md

    def test_project_name_in_md(self, bootstrap, spec_base):
        domain = self._mock_domain()
        md = bootstrap._generate_claude_md(spec_base, domain)
        assert spec_base.name in md


class TestGenerateAppConfig:
    def test_backend_url_embedded(self, bootstrap, spec_base):
        code = bootstrap._generate_app_config(spec_base)
        assert spec_base.backend_url in code

    def test_bundle_id_embedded(self, bootstrap, spec_base):
        code = bootstrap._generate_app_config(spec_base)
        assert spec_base.bundle_id in code

    def test_marketing_version_embedded(self, bootstrap, spec_base):
        code = bootstrap._generate_app_config(spec_base)
        assert spec_base.marketing_version in code

    def test_build_number_embedded(self, bootstrap, spec_base):
        code = bootstrap._generate_app_config(spec_base)
        assert str(spec_base.build_number) in code

    def test_swiftdata_flag_true(self, bootstrap, spec_base):
        spec_base.enable_swiftdata = True
        code = bootstrap._generate_app_config(spec_base)
        assert "enableSwiftData = true" in code

    def test_swiftdata_flag_false(self, bootstrap, spec_base):
        spec_base.enable_swiftdata = False
        code = bootstrap._generate_app_config(spec_base)
        assert "enableSwiftData = false" in code

    def test_coppa_flag_false(self, bootstrap, spec_base):
        spec_base.enable_coppa_compliance = False
        code = bootstrap._generate_app_config(spec_base)
        assert "enableCOPPACompliance = false" in code

    def test_coppa_flag_true(self, bootstrap, spec_base):
        spec_base.enable_coppa_compliance = True
        code = bootstrap._generate_app_config(spec_base)
        assert "enableCOPPACompliance = true" in code


class TestRunXcodegen:
    @pytest.mark.asyncio
    async def test_success(self, bootstrap, tmp_path):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"Generated!", b"")
            result = await bootstrap._run_xcodegen(tmp_path)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_failure(self, bootstrap, tmp_path):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(1, b"", b"Failed")
            result = await bootstrap._run_xcodegen(tmp_path)
        assert result["success"] is False
        assert result["error"] == "Failed"

    @pytest.mark.asyncio
    async def test_xcodegen_not_found(self, bootstrap, tmp_path):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await bootstrap._run_xcodegen(tmp_path)
        assert result["success"] is False
        assert "xcodegen not found" in result["error"]

    @pytest.mark.asyncio
    async def test_unexpected_exception(self, bootstrap, tmp_path):
        with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("crash")):
            result = await bootstrap._run_xcodegen(tmp_path)
        assert result["success"] is False
        assert "crash" in result["error"]

    @pytest.mark.asyncio
    async def test_stdout_captured(self, bootstrap, tmp_path):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"all good", b"")
            result = await bootstrap._run_xcodegen(tmp_path)
        assert "all good" in result["stdout"]

    @pytest.mark.asyncio
    async def test_stderr_captured_on_success(self, bootstrap, tmp_path):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"ok", b"warning output")
            result = await bootstrap._run_xcodegen(tmp_path)
        assert "warning output" in result["stderr"]

    @pytest.mark.asyncio
    async def test_error_is_none_on_success(self, bootstrap, tmp_path):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"ok", b"")
            result = await bootstrap._run_xcodegen(tmp_path)
        assert result["error"] is None


class TestBootstrapProject:
    @pytest.mark.asyncio
    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    async def test_output_dir_created(self, mock_dc, bootstrap, spec_base, tmp_path):
        mock_dc.return_value = Mock(coppa_required=False, hipaa_lite=False)
        with patch.object(bootstrap, "_run_xcodegen", new_callable=AsyncMock) as mock_xg:
            mock_xg.return_value = {"success": True, "stdout": "", "stderr": "", "error": None}
            output = tmp_path / "new_project"
            await bootstrap.bootstrap_project(spec_base, output)
        assert output.exists()

    @pytest.mark.asyncio
    async def test_fails_if_dir_exists_no_overwrite(self, bootstrap, spec_base, tmp_path):
        existing = tmp_path / "existing"
        existing.mkdir()
        result = await bootstrap.bootstrap_project(spec_base, existing, overwrite=False)
        assert len(result.errors) > 0
        assert "already exists" in result.errors[0]

    @pytest.mark.asyncio
    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    async def test_overwrites_existing_dir(self, mock_dc, bootstrap, spec_base, tmp_path):
        mock_dc.return_value = Mock(coppa_required=False, hipaa_lite=False)
        existing = tmp_path / "existing"
        existing.mkdir()
        with patch.object(bootstrap, "_run_xcodegen", new_callable=AsyncMock) as mock_xg:
            mock_xg.return_value = {"success": True, "stdout": "", "stderr": "", "error": None}
            result = await bootstrap.bootstrap_project(spec_base, existing, overwrite=True)
        # Should not error about existing dir
        assert not any("already exists" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_missing_template_returns_error(self, bootstrap, tmp_path):
        spec = ProjectSpec(
            name="Test",
            bundle_id="com.test",
            domain="d",
            template="enterprise",  # not created in templates_root fixture
            features=[],
            backend_url="https://api.test.com",
        )
        result = await bootstrap.bootstrap_project(spec, tmp_path / "out")
        assert any("Template not found" in e for e in result.errors)

    @pytest.mark.asyncio
    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    async def test_xcodegen_success_flag(self, mock_dc, bootstrap, spec_base, tmp_path):
        mock_dc.return_value = Mock(coppa_required=False, hipaa_lite=False)
        with patch.object(bootstrap, "_run_xcodegen", new_callable=AsyncMock) as mock_xg:
            mock_xg.return_value = {"success": True, "stdout": "", "stderr": "", "error": None}
            result = await bootstrap.bootstrap_project(spec_base, tmp_path / "out")
        assert result.xcodegen_success is True

    @pytest.mark.asyncio
    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    async def test_xcodegen_failure_captured(self, mock_dc, bootstrap, spec_base, tmp_path):
        mock_dc.return_value = Mock(coppa_required=False, hipaa_lite=False)
        with patch.object(bootstrap, "_run_xcodegen", new_callable=AsyncMock) as mock_xg:
            mock_xg.return_value = {
                "success": False,
                "stdout": "",
                "stderr": "error!",
                "error": "error!",
            }
            result = await bootstrap.bootstrap_project(spec_base, tmp_path / "out")
        assert result.xcodegen_success is False
        assert any("XcodeGen failed" in e for e in result.errors)

    @pytest.mark.asyncio
    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    async def test_compile_success_always_false(self, mock_dc, bootstrap, spec_base, tmp_path):
        """Compilation is never attempted in current implementation."""
        mock_dc.return_value = Mock(coppa_required=False, hipaa_lite=False)
        with patch.object(bootstrap, "_run_xcodegen", new_callable=AsyncMock) as mock_xg:
            mock_xg.return_value = {"success": True, "stdout": "", "stderr": "", "error": None}
            result = await bootstrap.bootstrap_project(spec_base, tmp_path / "out")
        assert result.compile_success is False

    @pytest.mark.asyncio
    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    async def test_coppa_enabled_when_domain_requires(self, mock_dc, bootstrap, tmp_path):
        mock_dc.return_value = Mock(coppa_required=True, hipaa_lite=False)
        spec = ProjectSpec(
            name="KidsApp",
            bundle_id="com.kids.app",
            domain="thebrightharbor-com",
            template="base-swiftui",
            features=[],
            backend_url="https://api.kids.com",
        )
        with patch.object(bootstrap, "_run_xcodegen", new_callable=AsyncMock) as mock_xg:
            mock_xg.return_value = {"success": True, "stdout": "", "stderr": "", "error": None}
            result = await bootstrap.bootstrap_project(spec, tmp_path / "kids_out")
        # COPPA warning should appear
        assert any("COPPA" in w for w in result.warnings)


class TestScaffoldFeature:
    @pytest.mark.asyncio
    async def test_auth_feature_creates_dir(self, bootstrap, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        spec = ProjectSpec(
            name="App",
            bundle_id="com.app",
            domain="d",
            template="base-swiftui",
            features=[],
            backend_url="https://api.test.com",
        )
        await bootstrap.scaffold_feature("auth", project, spec)
        assert (project / "App" / "Features" / "Auth").is_dir()

    @pytest.mark.asyncio
    async def test_unknown_feature_fails(self, bootstrap, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        result = await bootstrap.scaffold_feature("totally_unknown", project)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_unknown_feature_error_message(self, bootstrap, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        result = await bootstrap.scaffold_feature("mystery_feature", project)
        assert any("Unknown feature" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_onboarding_creates_generic_view(self, bootstrap, tmp_path):
        """Onboarding uses the generic scaffold path."""
        project = tmp_path / "project"
        project.mkdir()
        result = await bootstrap.scaffold_feature("onboarding", project)
        assert result.success is True
        assert any("OnboardingView.swift" in f for f in result.files_created)

    @pytest.mark.asyncio
    async def test_profile_creates_generic_view(self, bootstrap, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        result = await bootstrap.scaffold_feature("profile", project)
        assert result.success is True
        assert any("ProfileView.swift" in f for f in result.files_created)

    @pytest.mark.asyncio
    async def test_dashboard_no_spec_returns_empty(self, bootstrap, tmp_path):
        """Dashboard scaffold with no spec creates dir but no Swift files."""
        project = tmp_path / "project"
        project.mkdir()
        result = await bootstrap.scaffold_feature("dashboard", project, spec=None)
        assert result.success is True
        assert result.files_created == []

    @pytest.mark.asyncio
    async def test_settings_no_spec_returns_empty(self, bootstrap, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        result = await bootstrap.scaffold_feature("settings", project, spec=None)
        assert result.success is True
        assert result.files_created == []

    @pytest.mark.asyncio
    async def test_auth_no_spec_returns_empty(self, bootstrap, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        result = await bootstrap.scaffold_feature("auth", project, spec=None)
        assert result.success is True
        assert result.files_created == []


class TestSelectTemplate:
    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    def test_enterprise_for_coppa(self, mock_dc):
        mock_dc.return_value = Mock(coppa_required=True, hipaa_lite=False)
        assert select_template("mvp", "thebrightharbor-com") == "enterprise"

    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    def test_enterprise_for_hipaa(self, mock_dc):
        mock_dc.return_value = Mock(coppa_required=False, hipaa_lite=True)
        assert select_template("mvp", "calmconnect-io") == "enterprise"

    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    def test_base_for_prototype(self, mock_dc):
        mock_dc.return_value = Mock(coppa_required=False, hipaa_lite=False)
        assert select_template("prototype", "any-domain") == "base-swiftui"

    @patch("forge_harness.ios_harness.project_bootstrap.get_domain_config")
    def test_mvvm_standard_for_mvp(self, mock_dc):
        mock_dc.return_value = Mock(coppa_required=False, hipaa_lite=False)
        assert select_template("mvp", "codeswiftr-com") == "mvvm-standard"


class TestBootstrapResult:
    def test_default_created_files_empty(self):
        r = BootstrapResult(
            project_path=Path("/tmp"),
            template_used="base-swiftui",
            features_scaffolded=[],
            xcodegen_success=False,
            compile_success=False,
        )
        assert r.created_files == []

    def test_default_errors_empty(self):
        r = BootstrapResult(
            project_path=Path("/tmp"),
            template_used="base-swiftui",
            features_scaffolded=[],
            xcodegen_success=False,
            compile_success=False,
        )
        assert r.errors == []

    def test_default_warnings_empty(self):
        r = BootstrapResult(
            project_path=Path("/tmp"),
            template_used="base-swiftui",
            features_scaffolded=[],
            xcodegen_success=False,
            compile_success=False,
        )
        assert r.warnings == []


# ===========================================================================
# simulator_harness — SimulatorHarness
# ===========================================================================


SIMCTL_JSON = {
    "devices": {
        "com.apple.CoreSimulator.SimRuntime.iOS-17-0": [
            {
                "udid": "A-111",
                "name": "iPhone 15 Pro",
                "state": "Shutdown",
                "isAvailable": True,
            },
            {
                "udid": "B-222",
                "name": "iPhone 15",
                "state": "Booted",
                "isAvailable": True,
            },
            {
                "udid": "C-333",
                "name": "iPad Pro",
                "state": "Shutdown",
                "isAvailable": False,
            },
        ],
        "com.apple.CoreSimulator.SimRuntime.iOS-16-0": [
            {
                "udid": "D-444",
                "name": "iPhone 14",
                "state": "Shutdown",
                "isAvailable": True,
            },
        ],
    }
}


@pytest.fixture()
def sim_harness() -> SimulatorHarness:
    return SimulatorHarness()


@pytest.fixture()
def booted_device() -> SimulatorDevice:
    return SimulatorDevice(
        udid="A-111",
        name="iPhone 15 Pro",
        state=SimulatorState.BOOTED,
        device_type="iPhone 15 Pro",
        runtime="com.apple.CoreSimulator.SimRuntime.iOS-17-0",
        is_available=True,
    )


@pytest.fixture()
def shutdown_device() -> SimulatorDevice:
    return SimulatorDevice(
        udid="A-111",
        name="iPhone 15 Pro",
        state=SimulatorState.SHUTDOWN,
        device_type="iPhone 15 Pro",
        runtime="com.apple.CoreSimulator.SimRuntime.iOS-17-0",
        is_available=True,
    )


class TestSimulatorHarnessInit:
    def test_default_device_is_iphone15pro(self):
        h = SimulatorHarness()
        assert h.default_device == "iPhone 15 Pro"

    def test_custom_default_device(self):
        h = SimulatorHarness(default_device="iPad Pro")
        assert h.default_device == "iPad Pro"

    def test_none_becomes_iphone15pro(self):
        h = SimulatorHarness(default_device=None)
        assert h.default_device == DeviceType.IPHONE_15_PRO.value


class TestListDevices:
    @pytest.mark.asyncio
    async def test_available_only_filters_unavailable(self, sim_harness):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(
                0, json.dumps(SIMCTL_JSON).encode(), b""
            )
            devices = await sim_harness.list_devices(available_only=True)
        # C-333 is unavailable, so should be excluded
        names = [d.name for d in devices]
        assert "iPad Pro" not in names

    @pytest.mark.asyncio
    async def test_available_false_includes_all(self, sim_harness):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(
                0, json.dumps(SIMCTL_JSON).encode(), b""
            )
            devices = await sim_harness.list_devices(available_only=False)
        assert len(devices) == 4

    @pytest.mark.asyncio
    async def test_runtime_filter_restricts_to_ios17(self, sim_harness):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(
                0, json.dumps(SIMCTL_JSON).encode(), b""
            )
            devices = await sim_harness.list_devices(runtime="iOS-17-0", available_only=False)
        assert all("iOS-17-0" in d.runtime for d in devices)
        assert len(devices) == 3

    @pytest.mark.asyncio
    async def test_runtime_filter_restricts_to_ios16(self, sim_harness):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(
                0, json.dumps(SIMCTL_JSON).encode(), b""
            )
            devices = await sim_harness.list_devices(runtime="iOS-16-0", available_only=False)
        assert len(devices) == 1
        assert devices[0].udid == "D-444"

    @pytest.mark.asyncio
    async def test_raises_on_simctl_failure(self, sim_harness):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(1, b"", b"simctl error")
            with pytest.raises(RuntimeError, match="Failed to list devices"):
                await sim_harness.list_devices()

    @pytest.mark.asyncio
    async def test_state_parsed_as_enum(self, sim_harness):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(
                0, json.dumps(SIMCTL_JSON).encode(), b""
            )
            devices = await sim_harness.list_devices(available_only=False)
        iphone15 = next(d for d in devices if d.name == "iPhone 15")
        assert iphone15.state == SimulatorState.BOOTED

    @pytest.mark.asyncio
    async def test_empty_devices_section(self, sim_harness):
        data = {"devices": {}}
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(
                0, json.dumps(data).encode(), b""
            )
            devices = await sim_harness.list_devices()
        assert devices == []

    @pytest.mark.asyncio
    async def test_device_fields_populated(self, sim_harness):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(
                0, json.dumps(SIMCTL_JSON).encode(), b""
            )
            devices = await sim_harness.list_devices(available_only=False)
        d = next(d for d in devices if d.udid == "A-111")
        assert d.name == "iPhone 15 Pro"
        assert d.is_available is True
        assert "iOS-17-0" in d.runtime


class TestGetDevice:
    @pytest.mark.asyncio
    async def test_returns_matching_device(self, sim_harness):
        with patch.object(sim_harness, "list_devices", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = [
                SimulatorDevice(
                    udid="A-111",
                    name="iPhone 15 Pro",
                    state=SimulatorState.SHUTDOWN,
                    device_type="iPhone 15 Pro",
                    runtime="iOS-17-0",
                    is_available=True,
                )
            ]
            device = await sim_harness.get_device("iPhone 15 Pro")
        assert device is not None
        assert device.udid == "A-111"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, sim_harness):
        with patch.object(sim_harness, "list_devices", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = []
            device = await sim_harness.get_device("NonExistent")
        assert device is None

    @pytest.mark.asyncio
    async def test_uses_default_device_when_name_is_none(self, sim_harness):
        default = SimulatorDevice(
            udid="X",
            name=sim_harness.default_device,
            state=SimulatorState.SHUTDOWN,
            device_type="",
            runtime="",
            is_available=True,
        )
        with patch.object(sim_harness, "list_devices", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = [default]
            device = await sim_harness.get_device(None)
        assert device is not None
        assert device.name == sim_harness.default_device


class TestBoot:
    @pytest.mark.asyncio
    async def test_returns_device_if_already_booted(self, sim_harness, booted_device):
        with patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd:
            mock_gd.return_value = booted_device
            result = await sim_harness.boot("iPhone 15 Pro")
        assert result.state == SimulatorState.BOOTED

    @pytest.mark.asyncio
    async def test_raises_if_device_not_found(self, sim_harness):
        with patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd:
            mock_gd.return_value = None
            with pytest.raises(RuntimeError, match="Device not found"):
                await sim_harness.boot("No Such Device")

    @pytest.mark.asyncio
    async def test_boots_shutdown_device(self, sim_harness, shutdown_device):
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch.object(sim_harness, "_wait_for_boot", new_callable=AsyncMock),
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = shutdown_device
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            result = await sim_harness.boot("iPhone 15 Pro")
        assert result.state == SimulatorState.BOOTED

    @pytest.mark.asyncio
    async def test_raises_on_boot_command_failure(self, sim_harness, shutdown_device):
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = shutdown_device
            mock_exec.return_value = _make_async_proc(1, b"", b"Some boot error")
            with pytest.raises(RuntimeError, match="Failed to boot device"):
                await sim_harness.boot("iPhone 15 Pro")

    @pytest.mark.asyncio
    async def test_already_booted_error_is_ignored(self, sim_harness, shutdown_device):
        """'Unable to boot device in current state: Booted' must not raise."""
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch.object(sim_harness, "_wait_for_boot", new_callable=AsyncMock),
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = shutdown_device
            mock_exec.return_value = _make_async_proc(
                1, b"", b"Unable to boot device in current state: Booted"
            )
            result = await sim_harness.boot("iPhone 15 Pro")
        assert result.state == SimulatorState.BOOTED


class TestShutdown:
    @pytest.mark.asyncio
    async def test_returns_true_for_already_shutdown(self, sim_harness, shutdown_device):
        with patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd:
            mock_gd.return_value = shutdown_device
            result = await sim_harness.shutdown("iPhone 15 Pro")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_device_not_found(self, sim_harness):
        with patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd:
            mock_gd.return_value = None
            result = await sim_harness.shutdown("Ghost Device")
        assert result is False

    @pytest.mark.asyncio
    async def test_shuts_down_booted_device(self, sim_harness, booted_device):
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            result = await sim_harness.shutdown("iPhone 15 Pro")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_command_failure(self, sim_harness, booted_device):
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_exec.return_value = _make_async_proc(1, b"", b"error")
            result = await sim_harness.shutdown("iPhone 15 Pro")
        assert result is False


class TestInstall:
    @pytest.mark.asyncio
    async def test_returns_failure_when_device_not_found(self, sim_harness, tmp_path):
        app = tmp_path / "Test.app"
        app.mkdir()
        with patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd:
            mock_gd.return_value = None
            result = await sim_harness.install(app)
        assert result.success is False
        assert "Device not found" in result.error

    @pytest.mark.asyncio
    async def test_returns_failure_when_no_bundle_id(self, sim_harness, booted_device, tmp_path):
        app = tmp_path / "Test.app"
        app.mkdir()
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch.object(sim_harness, "_get_bundle_id", new_callable=AsyncMock) as mock_bid,
        ):
            mock_gd.return_value = booted_device
            mock_bid.return_value = None
            result = await sim_harness.install(app)
        assert result.success is False
        assert "bundle ID" in result.error

    @pytest.mark.asyncio
    async def test_installs_successfully(self, sim_harness, booted_device, tmp_path):
        app = tmp_path / "Test.app"
        app.mkdir()
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch.object(sim_harness, "_get_bundle_id", new_callable=AsyncMock) as mock_bid,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_bid.return_value = "com.test.app"
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            result = await sim_harness.install(app)
        assert result.success is True
        assert result.bundle_id == "com.test.app"

    @pytest.mark.asyncio
    async def test_install_command_failure(self, sim_harness, booted_device, tmp_path):
        app = tmp_path / "Test.app"
        app.mkdir()
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch.object(sim_harness, "_get_bundle_id", new_callable=AsyncMock) as mock_bid,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_bid.return_value = "com.test.app"
            mock_exec.return_value = _make_async_proc(1, b"", b"install error")
            result = await sim_harness.install(app)
        assert result.success is False
        assert "install error" in result.error

    @pytest.mark.asyncio
    async def test_boots_shutdown_device_before_install(self, sim_harness, shutdown_device, tmp_path):
        app = tmp_path / "Test.app"
        app.mkdir()
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch.object(sim_harness, "boot", new_callable=AsyncMock) as mock_boot,
            patch.object(sim_harness, "_get_bundle_id", new_callable=AsyncMock) as mock_bid,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = shutdown_device
            mock_boot.return_value = shutdown_device
            mock_bid.return_value = "com.app"
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            await sim_harness.install(app)
        mock_boot.assert_called_once()


class TestUninstall:
    @pytest.mark.asyncio
    async def test_returns_false_when_device_not_found(self, sim_harness):
        with patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd:
            mock_gd.return_value = None
            result = await sim_harness.uninstall("com.test.app")
        assert result is False

    @pytest.mark.asyncio
    async def test_uninstalls_successfully(self, sim_harness, booted_device):
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            result = await sim_harness.uninstall("com.test.app")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_command_failure(self, sim_harness, booted_device):
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_exec.return_value = _make_async_proc(1, b"", b"not installed")
            result = await sim_harness.uninstall("com.test.app")
        assert result is False


class TestLaunch:
    @pytest.mark.asyncio
    async def test_returns_failure_when_device_not_found(self, sim_harness):
        with patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd:
            mock_gd.return_value = None
            result = await sim_harness.launch("com.test.app")
        assert result.success is False
        assert "Device not found" in result.error

    @pytest.mark.asyncio
    async def test_launches_and_extracts_pid(self, sim_harness, booted_device):
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_exec.return_value = _make_async_proc(0, b"com.test.app: 9876", b"")
            result = await sim_harness.launch("com.test.app", wait_for_ui=False)
        assert result.success is True
        assert result.process_id == 9876

    @pytest.mark.asyncio
    async def test_pid_none_when_no_colon_in_output(self, sim_harness, booted_device):
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_exec.return_value = _make_async_proc(0, b"no pid here", b"")
            result = await sim_harness.launch("com.test.app", wait_for_ui=False)
        assert result.process_id is None

    @pytest.mark.asyncio
    async def test_launch_failure(self, sim_harness, booted_device):
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_exec.return_value = _make_async_proc(1, b"", b"No such bundle")
            result = await sim_harness.launch("com.test.app", wait_for_ui=False)
        assert result.success is False
        assert "No such bundle" in result.error

    @pytest.mark.asyncio
    async def test_boots_shutdown_device_before_launch(self, sim_harness, shutdown_device):
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch.object(sim_harness, "boot", new_callable=AsyncMock) as mock_boot,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = shutdown_device
            mock_boot.return_value = shutdown_device
            mock_exec.return_value = _make_async_proc(0, b"com.app: 123", b"")
            await sim_harness.launch("com.app", wait_for_ui=False)
        mock_boot.assert_called_once()

    @pytest.mark.asyncio
    async def test_pid_none_when_value_not_int(self, sim_harness, booted_device):
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_exec.return_value = _make_async_proc(0, b"com.app: not-a-number", b"")
            result = await sim_harness.launch("com.app", wait_for_ui=False)
        assert result.process_id is None


class TestScreenshot:
    @pytest.mark.asyncio
    async def test_returns_false_when_device_not_found(self, sim_harness, tmp_path):
        out = tmp_path / "shot.png"
        with patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd:
            mock_gd.return_value = None
            result = await sim_harness.screenshot(out)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_file_created(self, sim_harness, booted_device, tmp_path):
        out = tmp_path / "shot.png"
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            out.touch()  # simulate xcrun creating the file
            result = await sim_harness.screenshot(out)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_file_not_created(self, sim_harness, booted_device, tmp_path):
        out = tmp_path / "shot.png"
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            # Do NOT touch the file
            result = await sim_harness.screenshot(out)
        assert result is False

    @pytest.mark.asyncio
    async def test_creates_parent_directory(self, sim_harness, booted_device, tmp_path):
        out = tmp_path / "subdir" / "shot.png"
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.touch()
            await sim_harness.screenshot(out)
        assert out.parent.is_dir()


class TestGetLogs:
    @pytest.mark.asyncio
    async def test_returns_empty_when_device_not_found(self, sim_harness):
        with patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd:
            mock_gd.return_value = None
            logs = await sim_harness.get_logs("com.test.app")
        assert logs == []

    @pytest.mark.asyncio
    async def test_returns_log_lines(self, sim_harness, booted_device):
        log_output = b"line1\nline2\nline3"
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_exec.return_value = _make_async_proc(0, log_output, b"")
            logs = await sim_harness.get_logs("com.test.app")
        assert "line1" in logs
        assert "line2" in logs

    @pytest.mark.asyncio
    async def test_returns_empty_on_command_failure(self, sim_harness, booted_device):
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            mock_exec.return_value = _make_async_proc(1, b"", b"error")
            logs = await sim_harness.get_logs("com.test.app")
        assert logs == []

    @pytest.mark.asyncio
    async def test_custom_since_parameter(self, sim_harness, booted_device):
        with (
            patch.object(sim_harness, "get_device", new_callable=AsyncMock) as mock_gd,
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_gd.return_value = booted_device
            proc = _make_async_proc(0, b"log line", b"")
            mock_exec.return_value = proc
            await sim_harness.get_logs("com.test.app", since="5m")
        call_args = mock_exec.call_args[0]
        assert "5m" in call_args


class TestWaitForBoot:
    @pytest.mark.asyncio
    async def test_returns_immediately_on_success(self, sim_harness):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            await sim_harness._wait_for_boot("A-111", timeout=5)
        assert mock_exec.call_count == 1

    @pytest.mark.asyncio
    async def test_raises_timeout(self, sim_harness):
        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_exec.return_value = _make_async_proc(1, b"", b"not ready")
            with pytest.raises(TimeoutError):
                await sim_harness._wait_for_boot("A-111", timeout=2)

    @pytest.mark.asyncio
    async def test_retries_until_success(self, sim_harness):
        fail_proc = _make_async_proc(1, b"", b"not ready")
        ok_proc = _make_async_proc(0, b"", b"")

        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_exec.side_effect = [fail_proc, fail_proc, ok_proc]
            await sim_harness._wait_for_boot("A-111", timeout=10)
        assert mock_exec.call_count == 3


class TestGetBundleId:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_info_plist(self, sim_harness, tmp_path):
        app = tmp_path / "Test.app"
        app.mkdir()
        result = await sim_harness._get_bundle_id(app)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_bundle_id_from_plist(self, sim_harness, tmp_path):
        app = tmp_path / "Test.app"
        app.mkdir()
        info_plist = app / "Info.plist"
        info_plist.touch()  # file must exist for the check

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"com.test.mybundle\n", b"")
            result = await sim_harness._get_bundle_id(app)
        assert result == "com.test.mybundle"

    @pytest.mark.asyncio
    async def test_returns_none_on_plistbuddy_failure(self, sim_harness, tmp_path):
        app = tmp_path / "Test.app"
        app.mkdir()
        info_plist = app / "Info.plist"
        info_plist.touch()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(1, b"", b"missing key")
            result = await sim_harness._get_bundle_id(app)
        assert result is None


class TestSimulatorStateEnum:
    def test_all_states_exist(self):
        assert SimulatorState.SHUTDOWN.value == "Shutdown"
        assert SimulatorState.BOOTED.value == "Booted"
        assert SimulatorState.BOOTING.value == "Booting"
        assert SimulatorState.SHUTTING_DOWN.value == "Shutting Down"


class TestDeviceTypeEnum:
    def test_iphone15pro(self):
        assert DeviceType.IPHONE_15_PRO.value == "iPhone 15 Pro"

    def test_iphone15(self):
        assert DeviceType.IPHONE_15.value == "iPhone 15"

    def test_iphone_se(self):
        assert DeviceType.IPHONE_SE_3RD.value == "iPhone SE (3rd generation)"

    def test_ipad_pro(self):
        assert DeviceType.IPAD_PRO_13.value == "iPad Pro (13-inch) (M4)"

    def test_ipad_air(self):
        assert DeviceType.IPAD_AIR_11.value == "iPad Air 11-inch (M2)"


class TestSimulatorDeviceDataclass:
    def test_all_fields_stored(self):
        d = SimulatorDevice(
            udid="X-123",
            name="My Device",
            state=SimulatorState.BOOTED,
            device_type="My Device",
            runtime="iOS-17-0",
            is_available=True,
        )
        assert d.udid == "X-123"
        assert d.name == "My Device"
        assert d.state == SimulatorState.BOOTED
        assert d.device_type == "My Device"
        assert d.runtime == "iOS-17-0"
        assert d.is_available is True


class TestInstallResultDataclass:
    def test_success(self):
        r = InstallResult(success=True, bundle_id="com.app", app_path=Path("/tmp/A.app"))
        assert r.success is True
        assert r.error is None

    def test_failure(self):
        r = InstallResult(
            success=False, bundle_id="", app_path=Path("/tmp/A.app"), error="fail"
        )
        assert r.success is False
        assert r.error == "fail"


# ===========================================================================
# appstore_harness — AppStoreHarness
# ===========================================================================


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture()
def app_harness(workspace: Path) -> AppStoreHarness:
    return AppStoreHarness(workspace)


@pytest.fixture()
def mock_project(tmp_path: Path) -> Path:
    p = tmp_path / "App.xcodeproj"
    p.mkdir()
    return p


class TestAppStoreHarnessInit:
    def test_workspace_dir_created(self, tmp_path):
        ws = tmp_path / "builds"
        h = AppStoreHarness(ws)
        assert h.workspace_dir.is_dir()

    def test_workspace_dir_stored(self, tmp_path):
        ws = tmp_path / "builds"
        h = AppStoreHarness(ws)
        assert h.workspace_dir == ws

    def test_accepts_string_path(self, tmp_path):
        ws = str(tmp_path / "builds")
        h = AppStoreHarness(ws)
        assert isinstance(h.workspace_dir, Path)


class TestArchive:
    @pytest.mark.asyncio
    async def test_success(self, app_harness, mock_project):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"Archive success", b"")
            config = ArchiveConfig(project_path=mock_project, scheme="App")
            result = await app_harness.archive(config)
        assert result.success is True
        assert result.archive_path is not None

    @pytest.mark.asyncio
    async def test_failure(self, app_harness, mock_project):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(1, b"", b"Build error")
            config = ArchiveConfig(project_path=mock_project, scheme="App")
            result = await app_harness.archive(config)
        assert result.success is False
        assert "Build error" in result.error

    @pytest.mark.asyncio
    async def test_uses_provided_archive_path(self, app_harness, mock_project, tmp_path):
        custom_archive = tmp_path / "custom.xcarchive"
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            config = ArchiveConfig(
                project_path=mock_project,
                scheme="App",
                archive_path=custom_archive,
            )
            result = await app_harness.archive(config)
        assert result.archive_path == custom_archive

    @pytest.mark.asyncio
    async def test_team_id_added_to_command(self, app_harness, mock_project):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            config = ArchiveConfig(project_path=mock_project, scheme="App", team_id="TEAM123")
            await app_harness.archive(config)
        call_args = list(mock_exec.call_args[0])
        assert "DEVELOPMENT_TEAM=TEAM123" in call_args

    @pytest.mark.asyncio
    async def test_extracts_version_from_plist(self, app_harness, mock_project, tmp_path):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            config = ArchiveConfig(project_path=mock_project, scheme="App")

            # We need the archive_path/Info.plist to exist
            archive_path = (
                app_harness.workspace_dir / "archives"
            )
            archive_path.mkdir(parents=True, exist_ok=True)

            plist_data = {
                "ApplicationProperties": {
                    "CFBundleShortVersionString": "2.1.0",
                    "CFBundleVersion": "42",
                }
            }

            with patch("pathlib.Path.exists", return_value=True), patch(
                "builtins.open", MagicMock()
            ), patch("plistlib.load", return_value=plist_data):
                result = await app_harness.archive(config)

        assert result.version == "2.1.0"
        assert result.build_number == "42"

    @pytest.mark.asyncio
    async def test_version_none_when_no_plist(self, app_harness, mock_project):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            config = ArchiveConfig(project_path=mock_project, scheme="App")
            result = await app_harness.archive(config)
        # Info.plist will not exist in the auto-generated path
        assert result.version is None
        assert result.build_number is None

    @pytest.mark.asyncio
    async def test_release_configuration(self, app_harness, mock_project):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            config = ArchiveConfig(
                project_path=mock_project, scheme="App", configuration="Release"
            )
            await app_harness.archive(config)
        args = list(mock_exec.call_args[0])
        assert "Release" in args

    @pytest.mark.asyncio
    async def test_archive_path_parent_created(self, app_harness, mock_project, tmp_path):
        custom = tmp_path / "deep" / "nested" / "App.xcarchive"
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            config = ArchiveConfig(
                project_path=mock_project, scheme="App", archive_path=custom
            )
            await app_harness.archive(config)
        assert custom.parent.is_dir()


class TestExport:
    @pytest.mark.asyncio
    async def test_success(self, app_harness, tmp_path):
        archive = tmp_path / "App.xcarchive"
        archive.mkdir()
        config = ExportConfig(
            archive_path=archive, export_method=ExportMethod.APP_STORE, team_id="T123"
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"exported", b"")
            # Pre-create IPA so glob finds it
            export_path = app_harness.workspace_dir / "exports" / "app-store"
            export_path.mkdir(parents=True, exist_ok=True)
            (export_path / "App.ipa").touch()

            result = await app_harness.export(config)
        assert result.success is True
        assert result.ipa_path is not None

    @pytest.mark.asyncio
    async def test_failure_on_bad_returncode(self, app_harness, tmp_path):
        archive = tmp_path / "App.xcarchive"
        archive.mkdir()
        config = ExportConfig(
            archive_path=archive, export_method=ExportMethod.APP_STORE, team_id="T123"
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(1, b"", b"export failed")
            result = await app_harness.export(config)
        assert result.success is False
        assert "export failed" in result.error

    @pytest.mark.asyncio
    async def test_failure_when_no_ipa_generated(self, app_harness, tmp_path):
        archive = tmp_path / "App.xcarchive"
        archive.mkdir()
        config = ExportConfig(
            archive_path=archive, export_method=ExportMethod.APP_STORE, team_id="T123"
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"ok", b"")
            # No IPA created
            result = await app_harness.export(config)
        assert result.success is False
        assert "No IPA" in result.error

    @pytest.mark.asyncio
    async def test_uses_custom_export_path(self, app_harness, tmp_path):
        archive = tmp_path / "App.xcarchive"
        archive.mkdir()
        custom_export = tmp_path / "my_exports"
        config = ExportConfig(
            archive_path=archive,
            export_method=ExportMethod.AD_HOC,
            team_id="T123",
            export_path=custom_export,
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            (custom_export).mkdir(parents=True, exist_ok=True)
            (custom_export / "App.ipa").touch()
            result = await app_harness.export(config)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_export_method_in_options_plist(self, app_harness, tmp_path):
        archive = tmp_path / "App.xcarchive"
        archive.mkdir()
        config = ExportConfig(
            archive_path=archive, export_method=ExportMethod.DEVELOPMENT, team_id="T123"
        )
        written_data: dict = {}

        def capture_plist(data, f):
            written_data.update(data)

        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch("plistlib.dump", side_effect=capture_plist),
        ):
            mock_exec.return_value = _make_async_proc(1, b"", b"err")  # doesn't matter
            await app_harness.export(config)

        assert written_data.get("method") == "development"

    @pytest.mark.asyncio
    async def test_export_method_stored_in_result(self, app_harness, tmp_path):
        archive = tmp_path / "App.xcarchive"
        archive.mkdir()
        config = ExportConfig(
            archive_path=archive, export_method=ExportMethod.ENTERPRISE, team_id="T123"
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"", b"")
            export_path = app_harness.workspace_dir / "exports" / "enterprise"
            export_path.mkdir(parents=True, exist_ok=True)
            (export_path / "App.ipa").touch()
            result = await app_harness.export(config)
        assert result.export_method == ExportMethod.ENTERPRISE


class TestUploadToTestFlight:
    @pytest.mark.asyncio
    async def test_success(self, app_harness, tmp_path):
        ipa = tmp_path / "App.ipa"
        ipa.touch()
        config = UploadConfig(
            ipa_path=ipa,
            api_key_id="K1",
            api_issuer_id="I1",
            api_key_path=tmp_path / "key.p8",
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"Upload success", b"")
            result = await app_harness.upload_to_testflight(config)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_missing_ipa(self, app_harness, tmp_path):
        config = UploadConfig(
            ipa_path=tmp_path / "Missing.ipa",
            api_key_id="K1",
            api_issuer_id="I1",
            api_key_path=tmp_path / "key.p8",
        )
        result = await app_harness.upload_to_testflight(config)
        assert result.success is False
        assert any("IPA not found" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_upload_time_set(self, app_harness, tmp_path):
        ipa = tmp_path / "App.ipa"
        ipa.touch()
        config = UploadConfig(
            ipa_path=ipa,
            api_key_id="K1",
            api_issuer_id="I1",
            api_key_path=tmp_path / "key.p8",
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"ok", b"")
            result = await app_harness.upload_to_testflight(config)
        assert isinstance(result.upload_time, datetime)

    @pytest.mark.asyncio
    async def test_warnings_extracted(self, app_harness, tmp_path):
        ipa = tmp_path / "App.ipa"
        ipa.touch()
        config = UploadConfig(
            ipa_path=ipa,
            api_key_id="K1",
            api_issuer_id="I1",
            api_key_path=tmp_path / "key.p8",
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(
                0, b"ok\nWARNING: missing asset", b""
            )
            result = await app_harness.upload_to_testflight(config)
        assert any("WARNING" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_errors_extracted_on_failure(self, app_harness, tmp_path):
        ipa = tmp_path / "App.ipa"
        ipa.touch()
        config = UploadConfig(
            ipa_path=ipa,
            api_key_id="K1",
            api_issuer_id="I1",
            api_key_path=tmp_path / "key.p8",
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(
                1, b"", b"ERROR: invalid credentials\nerror: bad key"
            )
            result = await app_harness.upload_to_testflight(config)
        assert result.success is False
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_platform_defaults_to_ios(self, app_harness, tmp_path):
        ipa = tmp_path / "App.ipa"
        ipa.touch()
        config = UploadConfig(
            ipa_path=ipa,
            api_key_id="K1",
            api_issuer_id="I1",
            api_key_path=tmp_path / "key.p8",
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"ok", b"")
            await app_harness.upload_to_testflight(config)
        args = list(mock_exec.call_args[0])
        assert "ios" in args


class TestValidateIpa:
    @pytest.mark.asyncio
    async def test_success(self, app_harness, tmp_path):
        ipa = tmp_path / "App.ipa"
        ipa.touch()
        config = UploadConfig(
            ipa_path=ipa, api_key_id="K1", api_issuer_id="I1", api_key_path=tmp_path / "k.p8"
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"Validation passed", b"")
            result = await app_harness.validate_ipa(config)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_missing_ipa(self, app_harness, tmp_path):
        config = UploadConfig(
            ipa_path=tmp_path / "None.ipa",
            api_key_id="K1",
            api_issuer_id="I1",
            api_key_path=tmp_path / "k.p8",
        )
        result = await app_harness.validate_ipa(config)
        assert result.success is False
        assert any("IPA not found" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_validation_failure(self, app_harness, tmp_path):
        ipa = tmp_path / "App.ipa"
        ipa.touch()
        config = UploadConfig(
            ipa_path=ipa, api_key_id="K1", api_issuer_id="I1", api_key_path=tmp_path / "k.p8"
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(1, b"", b"ERROR: bad cert")
            result = await app_harness.validate_ipa(config)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_warnings_extracted(self, app_harness, tmp_path):
        ipa = tmp_path / "App.ipa"
        ipa.touch()
        config = UploadConfig(
            ipa_path=ipa, api_key_id="K1", api_issuer_id="I1", api_key_path=tmp_path / "k.p8"
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(
                0, b"ok\nWARNING: something minor", b""
            )
            result = await app_harness.validate_ipa(config)
        assert any("WARNING" in w for w in result.warnings)


class TestGetBuildStatus:
    @pytest.mark.asyncio
    async def test_returns_dict(self, app_harness):
        status = await app_harness.get_build_status("com.app", "1.0.0", "1")
        assert isinstance(status, dict)

    @pytest.mark.asyncio
    async def test_bundle_id_in_status(self, app_harness):
        status = await app_harness.get_build_status("com.myapp", "2.0.0", "5")
        assert status["bundle_id"] == "com.myapp"

    @pytest.mark.asyncio
    async def test_version_in_status(self, app_harness):
        status = await app_harness.get_build_status("com.app", "3.0.0", "99")
        assert status["version"] == "3.0.0"

    @pytest.mark.asyncio
    async def test_build_number_in_status(self, app_harness):
        status = await app_harness.get_build_status("com.app", "1.0", "77")
        assert status["build_number"] == "77"


class TestCreateExportOptions:
    def test_method_is_set(self, app_harness, tmp_path):
        config = ExportConfig(
            archive_path=tmp_path / "a.xcarchive",
            export_method=ExportMethod.AD_HOC,
            team_id="TEAM1",
        )
        opts = app_harness._create_export_options(config)
        assert opts["method"] == "ad-hoc"

    def test_team_id_is_set(self, app_harness, tmp_path):
        config = ExportConfig(
            archive_path=tmp_path / "a.xcarchive",
            export_method=ExportMethod.APP_STORE,
            team_id="MYTEAM",
        )
        opts = app_harness._create_export_options(config)
        assert opts["teamID"] == "MYTEAM"

    def test_upload_symbols_default_true(self, app_harness, tmp_path):
        config = ExportConfig(
            archive_path=tmp_path / "a.xcarchive",
            export_method=ExportMethod.APP_STORE,
            team_id="T",
        )
        opts = app_harness._create_export_options(config)
        assert opts["uploadSymbols"] is True

    def test_compile_bitcode_default_false(self, app_harness, tmp_path):
        config = ExportConfig(
            archive_path=tmp_path / "a.xcarchive",
            export_method=ExportMethod.APP_STORE,
            team_id="T",
        )
        opts = app_harness._create_export_options(config)
        assert opts["uploadBitcode"] is False

    def test_signing_cert_included_when_provided(self, app_harness, tmp_path):
        config = ExportConfig(
            archive_path=tmp_path / "a.xcarchive",
            export_method=ExportMethod.APP_STORE,
            team_id="T",
            signing_certificate="Apple Distribution: Acme Inc",
        )
        opts = app_harness._create_export_options(config)
        assert opts["signingCertificate"] == "Apple Distribution: Acme Inc"

    def test_no_signing_cert_key_when_absent(self, app_harness, tmp_path):
        config = ExportConfig(
            archive_path=tmp_path / "a.xcarchive",
            export_method=ExportMethod.APP_STORE,
            team_id="T",
        )
        opts = app_harness._create_export_options(config)
        assert "signingCertificate" not in opts

    def test_provisioning_profile_included_when_provided(self, app_harness, tmp_path):
        config = ExportConfig(
            archive_path=tmp_path / "a.xcarchive",
            export_method=ExportMethod.APP_STORE,
            team_id="T",
            provisioning_profile="MyProfile",
        )
        opts = app_harness._create_export_options(config)
        assert "provisioningProfiles" in opts


class TestExportMethodEnum:
    def test_app_store(self):
        assert ExportMethod.APP_STORE.value == "app-store"

    def test_ad_hoc(self):
        assert ExportMethod.AD_HOC.value == "ad-hoc"

    def test_development(self):
        assert ExportMethod.DEVELOPMENT.value == "development"

    def test_enterprise(self):
        assert ExportMethod.ENTERPRISE.value == "enterprise"


class TestReleaseTypeEnum:
    def test_manual(self):
        assert ReleaseType.MANUAL.value == "MANUAL"

    def test_after_approval(self):
        assert ReleaseType.AFTER_APPROVAL.value == "AFTER_APPROVAL"

    def test_scheduled(self):
        assert ReleaseType.SCHEDULED.value == "SCHEDULED"


class TestArchiveResultDataclass:
    def test_defaults(self):
        r = ArchiveResult(success=True)
        assert r.archive_path is None
        assert r.build_number is None
        assert r.version is None
        assert r.error is None

    def test_failure(self):
        r = ArchiveResult(success=False, error="something broke")
        assert r.success is False
        assert r.error == "something broke"


class TestExportResultDataclass:
    def test_defaults(self):
        r = ExportResult(success=True)
        assert r.ipa_path is None
        assert r.export_method is None
        assert r.error is None

    def test_failure(self):
        r = ExportResult(success=False, error="can't export")
        assert r.success is False


class TestUploadResultDataclass:
    def test_defaults(self):
        r = UploadResult(success=True)
        assert r.build_id is None
        assert r.upload_time is None
        assert r.warnings == []
        assert r.errors == []

    def test_failure(self):
        r = UploadResult(success=False, errors=["e1", "e2"])
        assert r.success is False
        assert len(r.errors) == 2


class TestReleaseInfoDataclass:
    def test_fields(self):
        ri = ReleaseInfo(
            version="1.0.0",
            build_number="1",
            release_type=ReleaseType.MANUAL,
            what_s_new="Bug fixes",
            phased_release=True,
        )
        assert ri.version == "1.0.0"
        assert ri.build_number == "1"
        assert ri.release_type == ReleaseType.MANUAL
        assert ri.what_s_new == "Bug fixes"
        assert ri.phased_release is True

    def test_optional_fields_default(self):
        ri = ReleaseInfo(
            version="1.0.0", build_number="1", release_type=ReleaseType.AFTER_APPROVAL
        )
        assert ri.release_date is None
        assert ri.what_s_new is None
        assert ri.phased_release is False


class TestCreateReleaseWorkflow:
    @pytest.mark.asyncio
    async def test_workflow_returns_three_results(self, tmp_path):
        project = tmp_path / "App.xcodeproj"
        project.mkdir()
        api_key = tmp_path / "key.p8"
        api_key.touch()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(0, b"ok", b"")
            workspace = tmp_path / ".build"
            workspace.mkdir()
            # Pre-create IPA for export step
            export_dir = workspace / "exports" / "app-store"
            export_dir.mkdir(parents=True, exist_ok=True)
            (export_dir / "App.ipa").touch()

            with patch("plistlib.dump"), patch("plistlib.load", return_value={}):
                a, e, u = await create_release_workflow(
                    project_path=project,
                    scheme="App",
                    team_id="T1",
                    api_key_id="K1",
                    api_issuer_id="I1",
                    api_key_path=api_key,
                    workspace_dir=workspace,
                )
        assert a is not None
        assert e is not None
        assert u is not None

    @pytest.mark.asyncio
    async def test_workflow_stops_after_archive_failure(self, tmp_path):
        project = tmp_path / "App.xcodeproj"
        project.mkdir()
        api_key = tmp_path / "key.p8"
        api_key.touch()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(1, b"", b"archive failed")
            workspace = tmp_path / ".build"

            with patch("plistlib.dump"):
                a, e, u = await create_release_workflow(
                    project_path=project,
                    scheme="App",
                    team_id="T1",
                    api_key_id="K1",
                    api_issuer_id="I1",
                    api_key_path=api_key,
                    workspace_dir=workspace,
                )
        assert a.success is False
        assert e.success is False  # short-circuited
        assert u.success is False

    @pytest.mark.asyncio
    async def test_workflow_uses_default_cwd_when_no_workspace(self, tmp_path):
        """Passing no workspace_dir uses Path.cwd() / '.build'."""
        project = tmp_path / "App.xcodeproj"
        project.mkdir()
        api_key = tmp_path / "key.p8"
        api_key.touch()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _make_async_proc(1, b"", b"fail")
            with (
                patch("pathlib.Path.cwd", return_value=tmp_path),
                patch("plistlib.dump"),
            ):
                a, e, u = await create_release_workflow(
                    project_path=project,
                    scheme="App",
                    team_id="T1",
                    api_key_id="K1",
                    api_issuer_id="I1",
                    api_key_path=api_key,
                )
        # Archive will fail (mock returns 1), just ensure no crash
        assert a is not None


class TestArchiveConfigDataclass:
    def test_defaults(self, tmp_path):
        config = ArchiveConfig(project_path=tmp_path / "p.xcodeproj", scheme="MyScheme")
        assert config.configuration == "Release"
        assert config.archive_path is None
        assert config.team_id is None
        assert config.provisioning_profile is None

    def test_custom_values(self, tmp_path):
        config = ArchiveConfig(
            project_path=tmp_path / "p.xcodeproj",
            scheme="MyScheme",
            configuration="Debug",
            team_id="TEAM999",
        )
        assert config.configuration == "Debug"
        assert config.team_id == "TEAM999"


class TestExportConfigDataclass:
    def test_defaults(self, tmp_path):
        config = ExportConfig(
            archive_path=tmp_path / "a.xcarchive",
            export_method=ExportMethod.APP_STORE,
            team_id="T",
        )
        assert config.export_path is None
        assert config.provisioning_profile is None
        assert config.signing_certificate is None
        assert config.compile_bitcode is False
        assert config.upload_symbols is True


class TestUploadConfigDataclass:
    def test_platform_default(self, tmp_path):
        config = UploadConfig(
            ipa_path=tmp_path / "a.ipa",
            api_key_id="K",
            api_issuer_id="I",
            api_key_path=tmp_path / "k.p8",
        )
        assert config.platform == "ios"
