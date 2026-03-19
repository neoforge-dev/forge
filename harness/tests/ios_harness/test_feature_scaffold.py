"""Tests for feature scaffolding."""

import pytest

from forge_harness.ios_harness.feature_scaffold import FeatureScaffold, ScaffoldResult


@pytest.fixture
def scaffold():
    """Create FeatureScaffold instance."""
    return FeatureScaffold("TestApp", "https://api.test.com")


@pytest.fixture
def output_dir(tmp_path):
    """Create temporary output directory."""
    return tmp_path / "project"


class TestFeatureScaffold:
    """Test FeatureScaffold class."""

    def test_init(self, scaffold):
        """Should initialize with project info."""
        assert scaffold.project_name == "TestApp"
        assert scaffold.backend_url == "https://api.test.com"

    @pytest.mark.asyncio
    async def test_scaffold_auth_creates_files(self, scaffold, output_dir):
        """Should create auth feature files."""
        result = await scaffold.scaffold_auth(output_dir)

        assert result.success is True
        assert result.feature_name == "Auth"
        assert len(result.files_created) > 0
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_scaffold_auth_creates_service(self, scaffold, output_dir):
        """Should create AuthService file."""
        result = await scaffold.scaffold_auth(output_dir)

        # Check AuthService exists
        auth_service_created = any("AuthService.swift" in f for f in result.files_created)
        assert auth_service_created is True

        # Verify file content
        service_file = output_dir / "App" / "Features" / "Auth" / "AuthService.swift"
        assert service_file.exists()

        content = service_file.read_text()
        assert "actor AuthService" in content
        assert "func login" in content
        assert "func register" in content
        assert "func logout" in content

    @pytest.mark.asyncio
    async def test_scaffold_auth_creates_viewmodel(self, scaffold, output_dir):
        """Should create AuthViewModel file."""
        result = await scaffold.scaffold_auth(output_dir)

        # Check AuthViewModel exists
        viewmodel_created = any("AuthViewModel.swift" in f for f in result.files_created)
        assert viewmodel_created is True

        # Verify file content
        viewmodel_file = output_dir / "App" / "Features" / "Auth" / "AuthViewModel.swift"
        assert viewmodel_file.exists()

        content = viewmodel_file.read_text()
        assert "@Observable" in content
        assert "final class AuthViewModel" in content
        assert "var email: String" in content
        assert "var password: String" in content
        assert "var isLoading: Bool" in content

    @pytest.mark.asyncio
    async def test_scaffold_auth_creates_view(self, scaffold, output_dir):
        """Should create LoginView file."""
        result = await scaffold.scaffold_auth(output_dir)

        # Check LoginView exists
        view_created = any("LoginView.swift" in f for f in result.files_created)
        assert view_created is True

        # Verify file content
        view_file = output_dir / "App" / "Features" / "Auth" / "LoginView.swift"
        assert view_file.exists()

        content = view_file.read_text()
        assert "struct LoginView: View" in content
        assert "TextField" in content  # Email field
        assert "SecureField" in content  # Password field
        assert "Button" in content  # Login button

    @pytest.mark.asyncio
    async def test_scaffold_auth_creates_user_model(self, scaffold, output_dir):
        """Should create User model file."""
        result = await scaffold.scaffold_auth(output_dir)

        # Check User model exists
        model_created = any("User.swift" in f for f in result.files_created)
        assert model_created is True

        # Verify file content
        model_file = output_dir / "App" / "Models" / "User.swift"
        assert model_file.exists()

        content = model_file.read_text()
        assert "@Model" in content
        assert "final class User" in content
        assert "var id: String" in content
        assert "var email: String" in content

    @pytest.mark.asyncio
    async def test_scaffold_dashboard_creates_files(self, scaffold, output_dir):
        """Should create dashboard feature files."""
        result = await scaffold.scaffold_dashboard(output_dir)

        assert result.success is True
        assert result.feature_name == "Dashboard"
        assert len(result.files_created) >= 4  # Service, ViewModel, View, Model

    @pytest.mark.asyncio
    async def test_scaffold_dashboard_creates_service(self, scaffold, output_dir):
        """Should create DashboardService file."""
        result = await scaffold.scaffold_dashboard(output_dir)

        service_file = output_dir / "App" / "Features" / "Dashboard" / "DashboardService.swift"
        assert service_file.exists()

        content = service_file.read_text()
        assert "actor DashboardService" in content
        assert "func fetchData" in content
        assert "func refresh" in content

    @pytest.mark.asyncio
    async def test_scaffold_dashboard_creates_viewmodel(self, scaffold, output_dir):
        """Should create DashboardViewModel file."""
        result = await scaffold.scaffold_dashboard(output_dir)

        viewmodel_file = output_dir / "App" / "Features" / "Dashboard" / "DashboardViewModel.swift"
        assert viewmodel_file.exists()

        content = viewmodel_file.read_text()
        assert "@Observable" in content
        assert "final class DashboardViewModel" in content
        assert "func loadData" in content
        assert "func refresh" in content

    @pytest.mark.asyncio
    async def test_scaffold_dashboard_creates_view(self, scaffold, output_dir):
        """Should create DashboardView with refresh."""
        result = await scaffold.scaffold_dashboard(output_dir)

        view_file = output_dir / "App" / "Features" / "Dashboard" / "DashboardView.swift"
        assert view_file.exists()

        content = view_file.read_text()
        assert "struct DashboardView: View" in content
        assert ".refreshable" in content  # Pull to refresh
        assert ".task" in content  # Load on appear

    @pytest.mark.asyncio
    async def test_scaffold_settings_creates_files(self, scaffold, output_dir):
        """Should create settings feature files."""
        result = await scaffold.scaffold_settings(output_dir)

        assert result.success is True
        assert result.feature_name == "Settings"
        assert len(result.files_created) >= 2  # ViewModel, View

    @pytest.mark.asyncio
    async def test_scaffold_settings_creates_viewmodel(self, scaffold, output_dir):
        """Should create SettingsViewModel file."""
        result = await scaffold.scaffold_settings(output_dir)

        viewmodel_file = output_dir / "App" / "Features" / "Settings" / "SettingsViewModel.swift"
        assert viewmodel_file.exists()

        content = viewmodel_file.read_text()
        assert "@Observable" in content
        assert "final class SettingsViewModel" in content
        assert "var notificationsEnabled: Bool" in content
        assert "func saveSettings" in content
        assert "UserDefaults" in content  # Persistence

    @pytest.mark.asyncio
    async def test_scaffold_settings_creates_view(self, scaffold, output_dir):
        """Should create SettingsView with Form."""
        result = await scaffold.scaffold_settings(output_dir)

        view_file = output_dir / "App" / "Features" / "Settings" / "SettingsView.swift"
        assert view_file.exists()

        content = view_file.read_text()
        assert "struct SettingsView: View" in content
        assert "Form" in content  # Settings form
        assert "Toggle" in content  # Toggle controls
        assert ".onChange" in content  # Auto-save

    @pytest.mark.asyncio
    async def test_backend_url_in_service(self, scaffold, output_dir):
        """Should include backend URL in service methods."""
        result = await scaffold.scaffold_auth(output_dir)

        service_file = output_dir / "App" / "Features" / "Auth" / "AuthService.swift"
        content = service_file.read_text()

        assert "https://api.test.com" in content


class TestScaffoldResult:
    """Test ScaffoldResult dataclass."""

    def test_result_success(self):
        """Should create successful result."""
        result = ScaffoldResult(
            feature_name="Auth",
            files_created=["file1.swift", "file2.swift"],
            success=True,
        )

        assert result.success is True
        assert len(result.files_created) == 2
        assert len(result.errors) == 0

    def test_result_failure(self):
        """Should create failure result."""
        result = ScaffoldResult(
            feature_name="Auth",
            success=False,
            errors=["Error 1", "Error 2"],
        )

        assert result.success is False
        assert len(result.errors) == 2
