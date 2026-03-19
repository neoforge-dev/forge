"""Tests for iOS quality gates harness."""

import pytest

from forge_harness.ios_harness.quality_gates import (
    QualityGatesHarness,
    QualityReport,
    Severity,
    Violation,
)


@pytest.fixture
def project_dir(tmp_path):
    """Create temporary iOS project directory."""
    project = tmp_path / "TestApp"
    project.mkdir()

    # Create App directory
    app_dir = project / "App"
    app_dir.mkdir()

    return project


@pytest.fixture
def quality_harness(project_dir):
    """Create QualityGatesHarness instance."""
    return QualityGatesHarness(project_dir)


class TestQualityGatesHarness:
    """Test QualityGatesHarness class."""

    @pytest.mark.asyncio
    async def test_validate_empty_project(self, quality_harness):
        """Should pass validation for empty project."""
        result = await quality_harness.validate(strict_mode=False)

        assert result.passed is True
        assert result.total_violations == 0

    @pytest.mark.asyncio
    async def test_check_no_uikit_violation(self, quality_harness, project_dir):
        """Should detect UIKit imports."""
        # Create file with UIKit import
        swift_file = project_dir / "App" / "BadView.swift"
        swift_file.write_text(
            """
import UIKit
import SwiftUI

struct BadView: View {
    var body: some View {
        Text("Hello")
    }
}
"""
        )

        violations = await quality_harness._check_no_uikit()

        assert len(violations) > 0
        assert violations[0].rule == "no-uikit"
        assert violations[0].severity == Severity.ERROR

    @pytest.mark.asyncio
    async def test_check_no_uikit_allows_representable(self, quality_harness, project_dir):
        """Should allow UIKit in UIViewRepresentable wrappers."""
        # Create UIViewRepresentable wrapper
        swift_file = project_dir / "App" / "Wrapper.swift"
        swift_file.write_text(
            """
import UIKit
import SwiftUI

struct MyWrapper: UIViewRepresentable {
    func makeUIView(context: Context) -> UIView {
        return UIView()
    }
}
"""
        )

        violations = await quality_harness._check_no_uikit()

        assert len(violations) == 0  # Allowed

    @pytest.mark.asyncio
    async def test_check_observable_viewmodels_old_pattern(self, quality_harness, project_dir):
        """Should warn about old ObservableObject pattern."""
        viewmodel_file = project_dir / "App" / "AuthViewModel.swift"
        viewmodel_file.write_text(
            """
import Combine

class AuthViewModel: ObservableObject {
    @Published var username = ""
}
"""
        )

        violations = await quality_harness._check_observable_viewmodels()

        assert len(violations) > 0
        assert violations[0].rule == "observable-viewmodels"
        assert violations[0].severity == Severity.WARNING
        assert "ObservableObject" in violations[0].message

    @pytest.mark.asyncio
    async def test_check_observable_viewmodels_missing_macro(self, quality_harness, project_dir):
        """Should suggest @Observable macro for ViewModels."""
        viewmodel_file = project_dir / "App" / "UserViewModel.swift"
        viewmodel_file.write_text(
            """
class UserViewModel {
    var username = ""
}
"""
        )

        violations = await quality_harness._check_observable_viewmodels()

        assert len(violations) > 0
        assert violations[0].rule == "observable-viewmodels"
        assert "@Observable" in violations[0].message

    @pytest.mark.asyncio
    async def test_check_actor_services_violation(self, quality_harness, project_dir):
        """Should warn about non-actor services with async."""
        service_file = project_dir / "App" / "AuthService.swift"
        service_file.write_text(
            """
class AuthService {
    func login() async throws -> User {
        // Async code without actor
    }
}
"""
        )

        violations = await quality_harness._check_actor_services()

        assert len(violations) > 0
        assert violations[0].rule == "actor-services"
        assert "actor" in violations[0].message.lower()

    @pytest.mark.asyncio
    async def test_check_swiftdata_models_old_core_data(self, quality_harness, project_dir):
        """Should error on old Core Data patterns."""
        model_file = project_dir / "App" / "UserModel.swift"
        model_file.write_text(
            """
import CoreData

class UserModel: NSManagedObject {
    @NSManaged var name: String
}
"""
        )

        violations = await quality_harness._check_swiftdata_models()

        assert len(violations) > 0
        assert violations[0].rule == "swiftdata-models"
        assert violations[0].severity == Severity.ERROR
        assert "SwiftData" in violations[0].message

    @pytest.mark.asyncio
    async def test_check_mvvm_structure_viewmodel_imports_swiftui(
        self, quality_harness, project_dir
    ):
        """Should warn about ViewModels importing SwiftUI."""
        viewmodel_file = project_dir / "App" / "HomeViewModel.swift"
        viewmodel_file.write_text(
            """
import SwiftUI

class HomeViewModel {
    var title = "Home"
}
"""
        )

        violations = await quality_harness._check_mvvm_structure()

        assert len(violations) > 0
        assert violations[0].rule == "mvvm-structure"
        assert "SwiftUI" in violations[0].message

    @pytest.mark.asyncio
    async def test_validate_strict_mode_fails_on_warnings(self, quality_harness, project_dir):
        """Should fail in strict mode with warnings."""
        # Create ViewModel with warning
        viewmodel_file = project_dir / "App" / "TestViewModel.swift"
        viewmodel_file.write_text(
            """
import SwiftUI
class TestViewModel {}
"""
        )

        result = await quality_harness.validate(strict_mode=True)

        assert result.passed is False
        assert result.warnings > 0

    @pytest.mark.asyncio
    async def test_validate_non_strict_mode_passes_with_warnings(
        self, quality_harness, project_dir
    ):
        """Should pass in non-strict mode with warnings."""
        # Create ViewModel with warning
        viewmodel_file = project_dir / "App" / "TestViewModel.swift"
        viewmodel_file.write_text(
            """
import SwiftUI
class TestViewModel {}
"""
        )

        result = await quality_harness.validate(strict_mode=False)

        # Should pass (no errors, only warnings)
        assert result.warnings > 0
        # Passes if no errors
        if result.errors == 0:
            assert result.passed is True


class TestQualityReport:
    """Test QualityReport dataclass."""

    def test_total_violations(self):
        """Should count total violations."""
        report = QualityReport(
            passed=False,
            violations=[
                Violation("rule1", Severity.ERROR, "file.swift", 10, "Error 1"),
                Violation("rule2", Severity.WARNING, "file.swift", 20, "Warning 1"),
                Violation("rule3", Severity.INFO, "file.swift", 30, "Info 1"),
            ],
            errors=1,
            warnings=1,
            infos=1,
        )

        assert report.total_violations == 3


class TestViolation:
    """Test Violation dataclass."""

    def test_violation_structure(self):
        """Should store violation data correctly."""
        violation = Violation(
            rule="no-uikit",
            severity=Severity.ERROR,
            file_path="App/View.swift",
            line=42,
            message="UIKit import not allowed",
        )

        assert violation.rule == "no-uikit"
        assert violation.severity == Severity.ERROR
        assert violation.line == 42
        assert "UIKit" in violation.message
