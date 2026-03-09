"""
Comprehensive tests for iOS harness batch 2 — targeting coverage gaps in:
  - swift_codegen.py   (need lines 251, 257-264, 341)
  - quality_gates.py   (need lines 146-147, 193-194, 226-227, 266-277, 310-311, 327-402)
  - build_harness.py   (need lines 224-225, 249, 270, 348-351, 378-379)
  - screenshot_automation.py (need lines 196-237, 246-283)

All macOS-specific system calls are mocked since tests run on Linux.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Imports from target modules
# ---------------------------------------------------------------------------
from forge_harness.ios_harness.build_harness import (
    BuildConfig,
    BuildConfiguration,
    BuildError,
    BuildHarness,
    BuildResult,
)
from forge_harness.ios_harness.quality_gates import (
    QualityGatesHarness,
    QualityReport,
    Severity,
    Violation,
)
from forge_harness.ios_harness.screenshot_automation import (
    Locale,
    ScreenshotAutomation,
    ScreenshotResult,
    ScreenshotSet,
    ScreenshotSize,
    ScreenshotSpec,
    create_standard_screenshot_specs,
)
from forge_harness.ios_harness.simulator_harness import (
    DeviceType,
    InstallResult,
    LaunchResult,
    SimulatorDevice,
    SimulatorState,
)
from forge_harness.ios_harness.swift_codegen import (
    ActorSpec,
    Method,
    Property,
    SwiftCodeGen,
    SwiftType,
    ViewModelSpec,
    ViewSpec,
)

# ===========================================================================
# Helpers / fixtures
# ===========================================================================


@pytest.fixture
def codegen():
    """SwiftCodeGen instance for a test project."""
    return SwiftCodeGen("MyApp")


@pytest.fixture
def project_dir(tmp_path):
    """iOS project directory with a valid .xcodeproj."""
    project = tmp_path / "TestApp"
    project.mkdir()
    xcodeproj = project / "TestApp.xcodeproj"
    xcodeproj.mkdir()
    (xcodeproj / "project.pbxproj").write_text("// Xcode project")
    return project


@pytest.fixture
def build_harness(project_dir):
    return BuildHarness(project_dir)


@pytest.fixture
def quality_project(tmp_path):
    """Project directory with a nested App folder for quality gate tests."""
    project = tmp_path / "QAProject"
    project.mkdir()
    (project / "App").mkdir()
    return project


@pytest.fixture
def quality_harness(quality_project):
    return QualityGatesHarness(quality_project)


@pytest.fixture
def output_dir(tmp_path):
    return tmp_path / "screenshots"


@pytest.fixture
def mock_simulator_harness():
    """Pre-wired mock SimulatorHarness for screenshot automation tests."""
    harness = MagicMock()

    device = SimulatorDevice(
        udid="DEVICE-UDID-0001",
        name="iPhone 15 Pro",
        state=SimulatorState.BOOTED,
        device_type="iPhone 15 Pro",
        runtime="iOS 17.0",
        is_available=True,
    )
    harness.boot = AsyncMock(return_value=device)
    harness.install = AsyncMock(
        return_value=InstallResult(success=True, bundle_id="com.test.app", app_path=Path("app"))
    )
    harness.launch = AsyncMock(
        return_value=LaunchResult(success=True, bundle_id="com.test.app", process_id=1234)
    )
    harness.screenshot = AsyncMock(return_value=True)
    harness.uninstall = AsyncMock(return_value=True)
    return harness


@pytest.fixture
def automation(output_dir, mock_simulator_harness):
    """ScreenshotAutomation with a fully-mocked SimulatorHarness."""
    return ScreenshotAutomation(
        bundle_id="com.test.app",
        output_dir=output_dir,
        simulator_harness=mock_simulator_harness,
    )


# ===========================================================================
# swift_codegen.py — gap coverage
# ===========================================================================


class TestSwiftCodeGenActorDependencies:
    """Cover actor generation with dependencies (lines 251, 257-264)."""

    def test_generate_actor_with_single_dependency(self, codegen):
        """Actor with one dependency should include it in init and properties."""
        spec = ActorSpec(
            name="Profile",
            dependencies=["NetworkService"],
        )
        code = codegen.generate_actor(spec)

        # Line 251: property loop over dependencies
        assert "private let network: NetworkService" in code
        # Lines 257-264: init with session + dependencies
        assert "init(session: URLSession = .shared, network: NetworkService)" in code
        assert "self.session = session" in code
        assert "self.network = network" in code

    def test_generate_actor_with_multiple_dependencies(self, codegen):
        """Actor with multiple dependencies should inject all in init."""
        spec = ActorSpec(
            name="Data",
            dependencies=["CacheService", "DatabaseService"],
        )
        code = codegen.generate_actor(spec)

        assert "private let cache: CacheService" in code
        assert "private let database: DatabaseService" in code
        assert "cache: CacheService" in code
        assert "database: DatabaseService" in code
        assert "self.cache = cache" in code
        assert "self.database = database" in code

    def test_generate_actor_with_properties_and_dependencies(self, codegen):
        """Actor should include both properties section and dependencies section."""
        spec = ActorSpec(
            name="Auth",
            properties=[Property("token", "String?")],
            dependencies=["KeychainService"],
        )
        code = codegen.generate_actor(spec)

        assert "var token: String?" in code
        assert "private let keychain: KeychainService" in code
        assert "init(session: URLSession = .shared, keychain: KeychainService)" in code

    def test_generate_actor_dependency_camel_case_strips_service(self, codegen):
        """Dependency name should have 'Service' stripped in property and init."""
        spec = ActorSpec(name="Upload", dependencies=["UploadService"])
        code = codegen.generate_actor(spec)

        # _camel_case("UploadService") -> "upload" (removes "Service", lowercases first)
        assert "private let upload: UploadService" in code
        assert "upload: UploadService" in code

    def test_camel_case_empty_string_returns_empty(self, codegen):
        """_camel_case on empty string should return empty string."""
        assert codegen._camel_case("") == ""

    def test_camel_case_service_only_returns_empty(self, codegen):
        """_camel_case on 'Service' alone returns empty string."""
        assert codegen._camel_case("Service") == ""


class TestSwiftCodeGenViewNoNavigation:
    """Cover the else branch of has_navigation (line 341)."""

    def test_generate_view_without_navigation(self, codegen):
        """View without navigation should omit NavigationStack."""
        spec = ViewSpec(
            name="Detail",
            viewmodel_name="DetailViewModel",
            has_navigation=False,
        )
        code = codegen.generate_view(spec)

        assert "NavigationStack" not in code
        # The else branch outputs plain 'content'
        assert "content" in code

    def test_generate_view_without_loading_or_error_state(self, codegen):
        """View with no loading/error states should still compile correctly."""
        spec = ViewSpec(
            name="Static",
            viewmodel_name="StaticViewModel",
            has_loading_state=False,
            has_error_state=False,
        )
        code = codegen.generate_view(spec)

        assert "ProgressView()" not in code
        assert "ContentUnavailableView" not in code
        # Should still have body and mainContent
        assert "var body: some View" in code
        assert "mainContent" in code

    def test_generate_view_no_error_no_error_view_func(self, codegen):
        """View with has_error_state=False should not emit the errorView function."""
        spec = ViewSpec(
            name="Simple",
            viewmodel_name="SimpleViewModel",
            has_error_state=False,
        )
        code = codegen.generate_view(spec)
        assert "private func errorView" not in code

    def test_generate_view_with_error_state_emits_error_view_func(self, codegen):
        """View with has_error_state=True should include the errorView helper."""
        spec = ViewSpec(
            name="Detailed",
            viewmodel_name="DetailedViewModel",
            has_error_state=True,
        )
        code = codegen.generate_view(spec)
        assert "private func errorView(_ error: Error)" in code


class TestSwiftCodeGenSwiftType:
    """Test SwiftType enum values."""

    def test_swift_type_enum_values(self):
        assert SwiftType.STRING.value == "String"
        assert SwiftType.INT.value == "Int"
        assert SwiftType.BOOL.value == "Bool"
        assert SwiftType.DOUBLE.value == "Double"
        assert SwiftType.DATE.value == "Date"
        assert SwiftType.OPTIONAL_STRING.value == "String?"
        assert SwiftType.OPTIONAL_INT.value == "Int?"
        assert SwiftType.ARRAY_STRING.value == "[String]"
        assert SwiftType.CUSTOM.value == "Custom"


class TestGenerateMethodEdgeCases:
    """Test _generate_method edge cases for full branch coverage."""

    def test_method_internal_access_omits_keyword(self, codegen):
        """internal access should NOT emit the 'internal' keyword."""
        method = Method("doWork", access="internal")
        code = codegen._generate_method(method, indent="    ")
        assert "internal " not in code
        assert "func doWork()" in code

    def test_method_private_access(self, codegen):
        """private access should emit 'private'."""
        method = Method("helper", access="private")
        code = codegen._generate_method(method, indent="    ")
        assert "private func helper()" in code

    def test_method_public_access(self, codegen):
        """public access should emit 'public'."""
        method = Method("publicFunc", access="public")
        code = codegen._generate_method(method, indent="")
        assert "public func publicFunc()" in code

    def test_method_with_return_type(self, codegen):
        """Non-Void return type should include '-> Type'."""
        method = Method("getValue", return_type="Int")
        code = codegen._generate_method(method)
        assert "-> Int" in code

    def test_method_void_return_omits_arrow(self, codegen):
        """Void return type should not emit '-> Void'."""
        method = Method("doSomething", return_type="Void")
        code = codegen._generate_method(method)
        assert "-> Void" not in code

    def test_method_async_throws_combined(self, codegen):
        """async throws method should have both modifiers."""
        method = Method("fetch", is_async=True, throws=True, return_type="Data")
        code = codegen._generate_method(method)
        assert "func fetch() async throws" in code
        assert "-> Data" in code

    def test_method_body_is_indented(self, codegen):
        """Body lines should be indented inside the method."""
        method = Method("work", body="let x = 1\nreturn x")
        code = codegen._generate_method(method, indent="    ")
        assert "        let x = 1" in code
        assert "        return x" in code


class TestViewModelSpecNoProperties:
    """ViewModel with no properties should skip the properties MARK section."""

    def test_viewmodel_no_properties_no_mark(self, codegen):
        spec = ViewModelSpec("Empty")
        code = codegen.generate_viewmodel(spec)
        # No MARK - Properties section expected when there are no properties
        assert "// MARK: - Properties" not in code

    def test_viewmodel_no_methods_no_mark(self, codegen):
        spec = ViewModelSpec("Empty")
        code = codegen.generate_viewmodel(spec)
        assert "// MARK: - Methods" not in code

    def test_viewmodel_no_dependencies_simple_init(self, codegen):
        spec = ViewModelSpec("Simple")
        code = codegen.generate_viewmodel(spec)
        assert "init() {}" in code


class TestActorSpecNoMethods:
    """Actor with no methods should skip the methods MARK."""

    def test_actor_no_methods_no_mark(self, codegen):
        spec = ActorSpec("Auth")
        code = codegen.generate_actor(spec)
        assert "// MARK: - Methods" not in code

    def test_actor_with_methods_mark_present(self, codegen):
        spec = ActorSpec(
            "Auth",
            methods=[Method("ping", is_async=True)],
        )
        code = codegen.generate_actor(spec)
        assert "// MARK: - Methods" in code


# ===========================================================================
# quality_gates.py — gap coverage
# ===========================================================================


class TestQualityGatesExceptionHandling:
    """Ensure exception paths (lines 146-147, 193-194, 226-227, 276-277, 310-311) are hit."""

    @pytest.mark.asyncio
    async def test_check_no_uikit_handles_unreadable_file(self, quality_project):
        """Files that raise on read_text() should be silently skipped."""
        harness = QualityGatesHarness(quality_project)
        bad_file = quality_project / "App" / "Broken.swift"
        bad_file.write_text("import UIKit\n")

        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            violations = await harness._check_no_uikit()

        # No crash; violations empty because exception was caught
        assert isinstance(violations, list)

    @pytest.mark.asyncio
    async def test_check_observable_viewmodels_handles_unreadable_file(self, quality_project):
        """Unreadable ViewModel files should be skipped gracefully."""
        harness = QualityGatesHarness(quality_project)
        bad_vm = quality_project / "App" / "BrokenViewModel.swift"
        bad_vm.write_text("class BrokenViewModel {}")

        with patch.object(Path, "read_text", side_effect=OSError("io error")):
            violations = await harness._check_observable_viewmodels()

        assert isinstance(violations, list)

    @pytest.mark.asyncio
    async def test_check_actor_services_handles_unreadable_file(self, quality_project):
        """Unreadable Service files should be skipped gracefully."""
        harness = QualityGatesHarness(quality_project)
        bad_service = quality_project / "App" / "BrokenService.swift"
        bad_service.write_text("class BrokenService { func go() async {} }")

        with patch.object(Path, "read_text", side_effect=OSError("io error")):
            violations = await harness._check_actor_services()

        assert isinstance(violations, list)

    @pytest.mark.asyncio
    async def test_check_swiftdata_models_handles_unreadable_file(self, quality_project):
        """Unreadable Model files should be skipped gracefully."""
        harness = QualityGatesHarness(quality_project)
        bad_model = quality_project / "App" / "BrokenModel.swift"
        bad_model.write_text("class BrokenModel: NSManagedObject {}")

        with patch.object(Path, "read_text", side_effect=OSError("io error")):
            violations = await harness._check_swiftdata_models()

        assert isinstance(violations, list)

    @pytest.mark.asyncio
    async def test_check_mvvm_structure_handles_unreadable_file(self, quality_project):
        """Unreadable ViewModel files should be skipped in MVVM check."""
        harness = QualityGatesHarness(quality_project)
        bad_vm = quality_project / "App" / "BadViewModel.swift"
        bad_vm.write_text("import SwiftUI\nclass BadViewModel {}")

        with patch.object(Path, "read_text", side_effect=OSError("io error")):
            violations = await harness._check_mvvm_structure()

        assert isinstance(violations, list)


class TestQualityGatesSwiftDataModelInfo:
    """Cover lines 266-277: SwiftData info violation for models using @Attribute/@Relationship."""

    @pytest.mark.asyncio
    async def test_check_swiftdata_attribute_without_model_macro(self, quality_project):
        """Model using @Attribute but not @Model should get an INFO violation."""
        harness = QualityGatesHarness(quality_project)
        model_file = quality_project / "App" / "ItemModel.swift"
        model_file.write_text(
            """
import SwiftData

class ItemModel {
    @Attribute var title: String = ""
}
"""
        )
        violations = await harness._check_swiftdata_models()

        info_violations = [v for v in violations if v.severity == Severity.INFO]
        assert len(info_violations) > 0
        assert "should use @Model macro" in info_violations[0].message

    @pytest.mark.asyncio
    async def test_check_swiftdata_relationship_without_model_macro(self, quality_project):
        """Model using @Relationship but not @Model should get an INFO violation."""
        harness = QualityGatesHarness(quality_project)
        model_file = quality_project / "App" / "TagModel.swift"
        model_file.write_text(
            """
class TagModel {
    @Relationship var items: [String] = []
}
"""
        )
        violations = await harness._check_swiftdata_models()

        info_violations = [v for v in violations if v.severity == Severity.INFO]
        assert len(info_violations) > 0

    @pytest.mark.asyncio
    async def test_check_swiftdata_persistent_model_without_macro(self, quality_project):
        """Model conforming to PersistentModel but not @Model should get INFO violation."""
        harness = QualityGatesHarness(quality_project)
        model_file = quality_project / "App" / "NodeModel.swift"
        model_file.write_text(
            """
class NodeModel: PersistentModel {
    var name: String = ""
}
"""
        )
        violations = await harness._check_swiftdata_models()

        info_violations = [v for v in violations if v.severity == Severity.INFO]
        assert len(info_violations) > 0

    @pytest.mark.asyncio
    async def test_check_swiftdata_with_model_macro_no_violation(self, quality_project):
        """Model with @Model macro should not trigger INFO violation."""
        harness = QualityGatesHarness(quality_project)
        model_file = quality_project / "App" / "GoodModel.swift"
        model_file.write_text(
            """
import SwiftData

@Model
final class GoodModel {
    var title: String = ""
}
"""
        )
        violations = await harness._check_swiftdata_models()
        assert len(violations) == 0


class TestQualityGatesSwift6Concurrency:
    """Cover lines 327-402: check_swift6_concurrency method."""

    @pytest.mark.asyncio
    async def test_check_swift6_concurrency_no_schemes(self, quality_project):
        """Should return failed report when no schemes found."""
        harness = QualityGatesHarness(quality_project)
        xcodeproj = quality_project / "TestApp.xcodeproj"
        xcodeproj.mkdir()

        mock_build_harness = MagicMock()
        mock_build_harness.list_schemes = AsyncMock(return_value=[])

        with patch(
            "forge_harness.ios_harness.build_harness.BuildHarness",
            return_value=mock_build_harness,
        ):
            report = await harness.check_swift6_concurrency(quality_project)

        assert report.passed is False
        assert report.errors >= 1
        assert any("No Xcode schemes" in v.message for v in report.violations)

    @pytest.mark.asyncio
    async def test_check_swift6_concurrency_with_concurrency_warnings(self, quality_project):
        """Concurrency warnings in build result should populate the report."""
        harness = QualityGatesHarness(quality_project)
        xcodeproj = quality_project / "TestApp.xcodeproj"
        xcodeproj.mkdir()

        concurrency_warning = BuildError(
            file_path="App/Auth.swift",
            line=10,
            message="Passing argument of non-sendable type across concurrency",
            error_type="warning",
        )
        mock_result = BuildResult(
            success=True,
            warnings=[concurrency_warning],
            errors=[],
        )
        mock_build_harness = MagicMock()
        mock_build_harness.list_schemes = AsyncMock(return_value=["TestApp"])
        mock_build_harness.build = AsyncMock(return_value=mock_result)

        with patch(
            "forge_harness.ios_harness.build_harness.BuildHarness",
            return_value=mock_build_harness,
        ):
            report = await harness.check_swift6_concurrency(quality_project)

        concurrency_violations = [
            v for v in report.violations if v.rule == "swift6-concurrency"
        ]
        assert len(concurrency_violations) > 0

    @pytest.mark.asyncio
    async def test_check_swift6_concurrency_with_sendable_warning(self, quality_project):
        """Sendable warnings should also be captured as violations."""
        harness = QualityGatesHarness(quality_project)
        xcodeproj = quality_project / "TestApp.xcodeproj"
        xcodeproj.mkdir()

        sendable_warning = BuildError(
            file_path="App/Model.swift",
            line=5,
            message="Type 'User' does not conform to 'Sendable'",
            error_type="warning",
        )
        mock_result = BuildResult(
            success=True,
            warnings=[sendable_warning],
            errors=[],
        )
        mock_build_harness = MagicMock()
        mock_build_harness.list_schemes = AsyncMock(return_value=["App"])
        mock_build_harness.build = AsyncMock(return_value=mock_result)

        with patch(
            "forge_harness.ios_harness.build_harness.BuildHarness",
            return_value=mock_build_harness,
        ):
            report = await harness.check_swift6_concurrency(quality_project)

        sendable_violations = [
            v for v in report.violations
            if v.rule == "swift6-concurrency" and "sendable" in v.message.lower()
        ]
        assert len(sendable_violations) > 0

    @pytest.mark.asyncio
    async def test_check_swift6_concurrency_with_build_errors(self, quality_project):
        """Build errors should appear as ERROR violations in report."""
        harness = QualityGatesHarness(quality_project)
        xcodeproj = quality_project / "TestApp.xcodeproj"
        xcodeproj.mkdir()

        build_err = BuildError(
            file_path="App/Service.swift",
            line=42,
            message="Cannot convert value of type 'Int' to expected argument type 'String'",
            error_type="error",
        )
        mock_result = BuildResult(
            success=False,
            errors=[build_err],
            warnings=[],
        )
        mock_build_harness = MagicMock()
        mock_build_harness.list_schemes = AsyncMock(return_value=["App"])
        mock_build_harness.build = AsyncMock(return_value=mock_result)

        with patch(
            "forge_harness.ios_harness.build_harness.BuildHarness",
            return_value=mock_build_harness,
        ):
            report = await harness.check_swift6_concurrency(quality_project)

        assert report.passed is False
        error_violations = [
            v for v in report.violations if v.severity == Severity.ERROR
        ]
        assert len(error_violations) > 0

    @pytest.mark.asyncio
    async def test_check_swift6_concurrency_exception_returns_error_report(self, quality_project):
        """An exception during concurrency check should result in a failed report."""
        harness = QualityGatesHarness(quality_project)

        with patch(
            "forge_harness.ios_harness.build_harness.BuildHarness",
            side_effect=RuntimeError("xcodebuild unavailable"),
        ):
            report = await harness.check_swift6_concurrency(quality_project)

        assert report.passed is False
        assert report.errors >= 1
        assert any("Concurrency check failed" in v.message for v in report.violations)

    @pytest.mark.asyncio
    async def test_check_swift6_concurrency_clean_build_passes(self, quality_project):
        """Build with no concurrency warnings/errors should pass."""
        harness = QualityGatesHarness(quality_project)
        xcodeproj = quality_project / "TestApp.xcodeproj"
        xcodeproj.mkdir()

        mock_result = BuildResult(
            success=True,
            errors=[],
            warnings=[],
        )
        mock_build_harness = MagicMock()
        mock_build_harness.list_schemes = AsyncMock(return_value=["App"])
        mock_build_harness.build = AsyncMock(return_value=mock_result)

        with patch(
            "forge_harness.ios_harness.build_harness.BuildHarness",
            return_value=mock_build_harness,
        ):
            report = await harness.check_swift6_concurrency(quality_project)

        assert report.passed is True
        assert report.errors == 0

    @pytest.mark.asyncio
    async def test_check_swift6_warning_not_about_concurrency_is_filtered(self, quality_project):
        """Warnings unrelated to concurrency/sendable should not appear as violations."""
        harness = QualityGatesHarness(quality_project)
        xcodeproj = quality_project / "TestApp.xcodeproj"
        xcodeproj.mkdir()

        unrelated_warning = BuildError(
            file_path="App/Util.swift",
            line=2,
            message="Variable 'x' was never used",
            error_type="warning",
        )
        mock_result = BuildResult(
            success=True,
            warnings=[unrelated_warning],
            errors=[],
        )
        mock_build_harness = MagicMock()
        mock_build_harness.list_schemes = AsyncMock(return_value=["App"])
        mock_build_harness.build = AsyncMock(return_value=mock_result)

        with patch(
            "forge_harness.ios_harness.build_harness.BuildHarness",
            return_value=mock_build_harness,
        ):
            report = await harness.check_swift6_concurrency(quality_project)

        # No concurrency-related violations expected
        concurrency_violations = [
            v for v in report.violations if v.rule == "swift6-concurrency"
        ]
        assert len(concurrency_violations) == 0


class TestQualityGatesActorServicesPass:
    """Verify that correct actor services produce no violations."""

    @pytest.mark.asyncio
    async def test_proper_actor_service_no_violation(self, quality_project):
        """Service already using actor keyword should not trigger warning."""
        harness = QualityGatesHarness(quality_project)
        service_file = quality_project / "App" / "AuthService.swift"
        service_file.write_text(
            """
actor AuthService {
    func login() async throws -> User {
        // Implemented
    }
}
"""
        )
        violations = await harness._check_actor_services()
        assert len(violations) == 0

    @pytest.mark.asyncio
    async def test_sync_class_service_no_violation(self, quality_project):
        """Service class with only sync methods should not trigger warning."""
        harness = QualityGatesHarness(quality_project)
        service_file = quality_project / "App" / "HelperService.swift"
        service_file.write_text(
            """
class HelperService {
    func format(_ value: String) -> String {
        return value.uppercased()
    }
}
"""
        )
        violations = await harness._check_actor_services()
        assert len(violations) == 0


class TestQualityGatesSeverityEnum:
    """Test Severity enum values."""

    def test_severity_values(self):
        assert Severity.ERROR.value == "error"
        assert Severity.WARNING.value == "warning"
        assert Severity.INFO.value == "info"


class TestQualityReportCounters:
    """Test QualityReport counters are properly set during validate()."""

    @pytest.mark.asyncio
    async def test_validate_counts_by_severity(self, quality_project):
        """validate() should correctly count errors, warnings, infos."""
        harness = QualityGatesHarness(quality_project)

        # Create a UIKit import (error) and SwiftUI import in ViewModel (warning)
        app_dir = quality_project / "App"
        swift_file = app_dir / "BadView.swift"
        swift_file.write_text("import UIKit\nstruct BadView {}")
        vm_file = app_dir / "HomeViewModel.swift"
        vm_file.write_text("import SwiftUI\nclass HomeViewModel {}")

        result = await harness.validate(strict_mode=False)

        assert result.errors >= 1
        assert result.warnings >= 1
        assert isinstance(result.total_violations, int)
        assert result.total_violations == result.errors + result.warnings + result.infos


# ===========================================================================
# build_harness.py — gap coverage
# ===========================================================================


class TestBuildHarnessGenericException:
    """Cover lines 224-225: generic exception during asyncio.create_subprocess_exec."""

    @pytest.mark.asyncio
    async def test_build_generic_exception_returns_failure(self, build_harness):
        """A RuntimeError during build should return a failed BuildResult."""
        config = BuildConfig(scheme="TestApp")

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=RuntimeError("unexpected failure"),
        ):
            result = await build_harness.build(config)

        assert result.success is False
        assert len(result.errors) > 0
        assert "Build failed" in result.errors[0].message
        assert "unexpected failure" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_build_os_error_returns_failure(self, build_harness):
        """An OSError during build should return a failed BuildResult."""
        config = BuildConfig(scheme="TestApp")

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=OSError("permission denied"),
        ):
            result = await build_harness.build(config)

        assert result.success is False
        assert "Build failed" in result.errors[0].message


class TestBuildCommandNoTesting:
    """Cover line 249: build action without testing flag."""

    def test_build_command_no_testing_uses_build_action(self, build_harness):
        """When enable_testing=False, command should use 'build' not 'build-for-testing'."""
        config = BuildConfig(
            scheme="TestApp",
            enable_testing=False,
            clean_build=False,
            enable_strict_concurrency=False,
        )
        cmd = build_harness._build_command(config)

        assert "build" in cmd
        assert "build-for-testing" not in cmd

    def test_build_command_no_clean_omits_clean(self, build_harness):
        """When clean_build=False, command should not include 'clean'."""
        config = BuildConfig(
            scheme="TestApp",
            clean_build=False,
            enable_strict_concurrency=False,
        )
        cmd = build_harness._build_command(config)

        # 'clean' should NOT appear as a separate action; it may be in xcodeproj path
        # We check that the first action word is not 'clean'
        assert cmd[1] != "clean"


class TestBuildCommandDerivedDataPath:
    """Cover line 270: derived data path in command."""

    def test_build_command_with_derived_data_path(self, build_harness, tmp_path):
        """derived_data_path should be appended to the command."""
        derived = tmp_path / "DerivedData"
        config = BuildConfig(
            scheme="TestApp",
            derived_data_path=derived,
            clean_build=False,
            enable_strict_concurrency=False,
        )
        cmd = build_harness._build_command(config)

        assert "-derivedDataPath" in cmd
        assert str(derived) in cmd

    def test_build_command_without_derived_data_path_omits_flag(self, build_harness):
        """No derived_data_path should mean no -derivedDataPath flag."""
        config = BuildConfig(
            scheme="TestApp",
            clean_build=False,
            enable_strict_concurrency=False,
        )
        cmd = build_harness._build_command(config)

        assert "-derivedDataPath" not in cmd


class TestListSchemesEdgeCases:
    """Cover lines 348-351: edge cases in list_schemes."""

    @pytest.mark.asyncio
    async def test_list_schemes_no_project_key(self, build_harness):
        """JSON response without 'project' key should return empty list."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b'{"workspace": {}}', b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            schemes = await build_harness.list_schemes()

        assert schemes == []

    @pytest.mark.asyncio
    async def test_list_schemes_no_schemes_key(self, build_harness):
        """JSON response with 'project' but no 'schemes' should return empty list."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(b'{"project": {"targets": ["TestApp"]}}', b"")
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            schemes = await build_harness.list_schemes()

        assert schemes == []

    @pytest.mark.asyncio
    async def test_list_schemes_failure_returns_empty(self, build_harness):
        """Non-zero return code from list command should return empty list."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            schemes = await build_harness.list_schemes()

        assert schemes == []

    @pytest.mark.asyncio
    async def test_list_schemes_exception_returns_empty(self, build_harness):
        """Exception during list should return empty list."""
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("xcodebuild not found"),
        ):
            schemes = await build_harness.list_schemes()

        assert schemes == []

    @pytest.mark.asyncio
    async def test_list_schemes_invalid_json_returns_empty(self, build_harness):
        """Malformed JSON response should return empty list."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"not-json", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            schemes = await build_harness.list_schemes()

        assert schemes == []


class TestCleanException:
    """Cover lines 378-379: exception during clean."""

    @pytest.mark.asyncio
    async def test_clean_exception_returns_false(self, build_harness):
        """Exception during clean should return False."""
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=RuntimeError("xcodebuild crashed"),
        ):
            result = await build_harness.clean()

        assert result is False

    @pytest.mark.asyncio
    async def test_clean_file_not_found_returns_false(self, build_harness):
        """FileNotFoundError during clean should return False."""
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("xcodebuild"),
        ):
            result = await build_harness.clean()

        assert result is False


class TestBuildResultProperties:
    """Additional BuildResult property tests."""

    def test_error_count_zero(self):
        result = BuildResult(success=True)
        assert result.error_count == 0

    def test_warning_count_zero(self):
        result = BuildResult(success=True)
        assert result.warning_count == 0

    def test_error_and_warning_count_combined(self):
        result = BuildResult(
            success=False,
            errors=[BuildError("f.swift", 1, "err", "error")],
            warnings=[
                BuildError("f.swift", 2, "warn1", "warning"),
                BuildError("f.swift", 3, "warn2", "warning"),
            ],
        )
        assert result.error_count == 1
        assert result.warning_count == 2


class TestBuildConfigDefaults:
    """Verify all BuildConfig field defaults."""

    def test_default_destination(self):
        config = BuildConfig(scheme="App")
        assert "iPhone" in config.destination or "Simulator" in config.destination

    def test_default_configuration(self):
        config = BuildConfig(scheme="App")
        assert config.configuration == BuildConfiguration.DEBUG

    def test_default_no_derived_data(self):
        config = BuildConfig(scheme="App")
        assert config.derived_data_path is None

    def test_build_configuration_values(self):
        assert BuildConfiguration.DEBUG.value == "Debug"
        assert BuildConfiguration.RELEASE.value == "Release"


# ===========================================================================
# screenshot_automation.py — gap coverage
# ===========================================================================


class TestCaptureSingleScreenshotLocales:
    """Verify locale forwarding to _set_locale in capture_single_screenshot."""

    @pytest.mark.asyncio
    async def test_capture_uses_specified_locale(self, automation, output_dir):
        """capture_single_screenshot should forward the locale to _set_locale."""
        screenshot_path = output_dir / "test.png"

        set_locale_calls = []

        async def fake_set_locale(udid, locale):
            set_locale_calls.append((udid, locale))

        automation._set_locale = fake_set_locale

        with (
            patch.object(automation.simulator_harness, "boot") as mock_boot,
            patch.object(automation.simulator_harness, "screenshot") as mock_screenshot,
            patch.object(automation, "_set_appearance", new=AsyncMock()),
        ):
            device = MagicMock()
            device.udid = "UDID-X"
            mock_boot.return_value = device
            mock_screenshot.return_value = True

            await automation.capture_single_screenshot(
                device_type=DeviceType.IPHONE_15_PRO,
                output_path=screenshot_path,
                locale=Locale.FR_FR,
            )

        assert len(set_locale_calls) == 1
        assert set_locale_calls[0][1] == Locale.FR_FR

    @pytest.mark.asyncio
    async def test_capture_light_mode_calls_set_appearance_false(self, automation, output_dir):
        """Light mode (dark_mode=False) should call _set_appearance with False."""
        screenshot_path = output_dir / "light.png"
        appearance_calls = []

        async def fake_set_appearance(udid, dark_mode):
            appearance_calls.append(dark_mode)

        automation._set_appearance = fake_set_appearance

        with (
            patch.object(automation.simulator_harness, "boot") as mock_boot,
            patch.object(automation.simulator_harness, "screenshot") as mock_screenshot,
            patch.object(automation, "_set_locale", new=AsyncMock()),
        ):
            device = MagicMock()
            device.udid = "UDID-Y"
            mock_boot.return_value = device
            mock_screenshot.return_value = True

            await automation.capture_single_screenshot(
                device_type=DeviceType.IPHONE_15_PRO,
                output_path=screenshot_path,
                dark_mode=False,
            )

        assert False in appearance_calls


class TestCaptureScreenshotSetInternal:
    """Cover the _capture_screenshot_set private method (lines 196-237)."""

    @pytest.mark.asyncio
    async def test_capture_screenshot_set_light_mode(self, automation, tmp_path):
        """Light-mode spec should populate light_mode_screenshots."""
        spec = ScreenshotSpec(
            name="home",
            device_type=DeviceType.IPHONE_15_PRO,
            locale=Locale.EN_US,
            light_mode=True,
            steps=["launch"],
        )
        app_path = tmp_path / "TestApp.app"
        app_path.mkdir()

        # Stub _capture_screenshots and _set_locale/_set_appearance to avoid subprocess calls
        fake_png = tmp_path / "shot.png"
        fake_png.write_bytes(b"PNG")

        async def fake_capture(udid, s, mode):
            return [fake_png]

        automation._capture_screenshots = fake_capture
        automation._set_locale = AsyncMock()
        automation._set_appearance = AsyncMock()

        screenshot_set = await automation._capture_screenshot_set(spec, app_path)

        assert screenshot_set.device_type == DeviceType.IPHONE_15_PRO
        assert screenshot_set.locale == Locale.EN_US
        assert len(screenshot_set.light_mode_screenshots) == 1
        assert fake_png in screenshot_set.screenshots

    @pytest.mark.asyncio
    async def test_capture_screenshot_set_dark_mode(self, automation, tmp_path):
        """Dark-mode spec (light_mode=False) should populate dark_mode_screenshots."""
        spec = ScreenshotSpec(
            name="settings",
            device_type=DeviceType.IPHONE_15,
            locale=Locale.DE_DE,
            light_mode=False,
            steps=["launch"],
        )
        app_path = tmp_path / "App.app"
        app_path.mkdir()

        fake_dark_png = tmp_path / "dark.png"
        fake_dark_png.write_bytes(b"PNG")

        async def fake_capture(udid, s, mode):
            return [fake_dark_png]

        automation._capture_screenshots = fake_capture
        automation._set_locale = AsyncMock()
        automation._set_appearance = AsyncMock()

        screenshot_set = await automation._capture_screenshot_set(spec, app_path)

        assert len(screenshot_set.dark_mode_screenshots) == 1
        assert fake_dark_png in screenshot_set.screenshots

    @pytest.mark.asyncio
    async def test_capture_screenshot_set_install_failure_raises(self, automation, tmp_path):
        """If installation fails, _capture_screenshot_set should raise RuntimeError."""
        spec = ScreenshotSpec(
            name="fail",
            device_type=DeviceType.IPHONE_15_PRO,
            locale=Locale.EN_US,
            steps=[],
        )
        app_path = tmp_path / "Fail.app"
        app_path.mkdir()

        automation.simulator_harness.install = AsyncMock(
            return_value=InstallResult(
                success=False,
                bundle_id="com.test.app",
                app_path=app_path,
                error="App bundle corrupt",
            )
        )
        automation._set_locale = AsyncMock()

        with pytest.raises(RuntimeError, match="Failed to install app"):
            await automation._capture_screenshot_set(spec, app_path)

    @pytest.mark.asyncio
    async def test_capture_screenshot_set_calls_uninstall(self, automation, tmp_path):
        """_capture_screenshot_set should always uninstall after capture."""
        spec = ScreenshotSpec(
            name="cleanup_test",
            device_type=DeviceType.IPHONE_15_PRO,
            locale=Locale.EN_US,
            light_mode=True,
            steps=[],
        )
        app_path = tmp_path / "Clean.app"
        app_path.mkdir()

        automation._capture_screenshots = AsyncMock(return_value=[])
        automation._set_locale = AsyncMock()
        automation._set_appearance = AsyncMock()

        await automation._capture_screenshot_set(spec, app_path)

        automation.simulator_harness.uninstall.assert_called_once_with(
            automation.bundle_id, spec.device_type.value
        )


class TestCaptureScreenshotsInternal:
    """Cover the _capture_screenshots private method (lines 246-283)."""

    @pytest.mark.asyncio
    async def test_capture_screenshots_with_steps_returns_paths(self, automation, tmp_path):
        """Each step should attempt a screenshot; successful ones should be returned."""
        spec = ScreenshotSpec(
            name="walkthrough",
            device_type=DeviceType.IPHONE_15_PRO,
            locale=Locale.EN_US,
            steps=["Launch", "Navigate", "Interact"],
        )

        # screenshot returns True -> paths accumulated
        automation.simulator_harness.screenshot = AsyncMock(return_value=True)

        with patch("asyncio.sleep", new=AsyncMock()):
            paths = await automation._capture_screenshots("UDID-SNAP", spec, "light")

        assert len(paths) == 3
        for p in paths:
            assert isinstance(p, Path)
            assert "walkthrough" in p.name

    @pytest.mark.asyncio
    async def test_capture_screenshots_failed_screenshot_excluded(self, automation):
        """Steps where screenshot() returns False should not add to returned paths."""
        spec = ScreenshotSpec(
            name="partial",
            device_type=DeviceType.IPHONE_15_PRO,
            locale=Locale.EN_US,
            steps=["step1", "step2"],
        )

        # First call True, second call False
        automation.simulator_harness.screenshot = AsyncMock(side_effect=[True, False])

        with patch("asyncio.sleep", new=AsyncMock()):
            paths = await automation._capture_screenshots("UDID-Z", spec, "dark")

        assert len(paths) == 1

    @pytest.mark.asyncio
    async def test_capture_screenshots_no_steps_returns_empty(self, automation):
        """No steps means no screenshots are captured."""
        spec = ScreenshotSpec(
            name="empty",
            device_type=DeviceType.IPHONE_15_PRO,
            locale=Locale.EN_US,
            steps=[],
        )

        with patch("asyncio.sleep", new=AsyncMock()):
            paths = await automation._capture_screenshots("UDID-W", spec, "light")

        assert paths == []

    @pytest.mark.asyncio
    async def test_capture_screenshots_launch_failure_raises(self, automation):
        """Launch failure should raise RuntimeError."""
        spec = ScreenshotSpec(
            name="launch_fail",
            device_type=DeviceType.IPHONE_15_PRO,
            locale=Locale.EN_US,
            steps=["go"],
        )

        automation.simulator_harness.launch = AsyncMock(
            return_value=LaunchResult(
                success=False,
                bundle_id="com.test.app",
                error="Process could not be launched",
            )
        )

        with pytest.raises(RuntimeError, match="Failed to launch app"):
            await automation._capture_screenshots("UDID-FAIL", spec, "light")

    @pytest.mark.asyncio
    async def test_capture_screenshots_screenshot_path_structure(self, automation, output_dir):
        """Screenshot paths should follow locale/device/mode/name_N.png structure."""
        spec = ScreenshotSpec(
            name="login",
            device_type=DeviceType.IPHONE_15_PRO,
            locale=Locale.JA_JP,
            steps=["Go"],
        )

        automation.simulator_harness.screenshot = AsyncMock(return_value=True)

        with patch("asyncio.sleep", new=AsyncMock()):
            paths = await automation._capture_screenshots("UDID-PATH", spec, "light")

        assert len(paths) == 1
        path = paths[0]
        assert Locale.JA_JP.value in str(path)
        assert DeviceType.IPHONE_15_PRO.value in str(path)
        assert "light" in str(path)
        assert "login_1.png" in path.name


class TestSetLocaleAndAppearanceVariants:
    """Additional locale and appearance coverage."""

    @pytest.mark.asyncio
    async def test_set_locale_zh_cn(self, automation):
        """_set_locale with ZH_CN should pass 'zh' as the language code."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_exec.return_value = mock_proc

            await automation._set_locale("UDID-1", Locale.ZH_CN)

            call_args = mock_exec.call_args[0]
            # The command should contain 'zh' (first part of 'zh-CN')
            assert "zh" in " ".join(str(a) for a in call_args)

    @pytest.mark.asyncio
    async def test_set_appearance_light(self, automation):
        """_set_appearance with dark_mode=False should send 'light'."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_exec.return_value = mock_proc

            await automation._set_appearance("UDID-2", dark_mode=False)

            call_args = mock_exec.call_args[0]
            assert "light" in call_args

    @pytest.mark.asyncio
    async def test_set_appearance_dark(self, automation):
        """_set_appearance with dark_mode=True should send 'dark'."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_exec.return_value = mock_proc

            await automation._set_appearance("UDID-3", dark_mode=True)

            call_args = mock_exec.call_args[0]
            assert "dark" in call_args


class TestScreenshotAutomationWithExplicitSimulator:
    """Verify that explicitly-passed SimulatorHarness is used instead of default."""

    def test_custom_simulator_harness_stored(self, output_dir, mock_simulator_harness):
        automation = ScreenshotAutomation(
            bundle_id="com.example.custom",
            output_dir=output_dir,
            simulator_harness=mock_simulator_harness,
        )
        assert automation.simulator_harness is mock_simulator_harness

    def test_no_simulator_harness_creates_default(self, output_dir):
        """When no harness is provided, a default SimulatorHarness should be created."""
        with patch(
            "forge_harness.ios_harness.screenshot_automation.SimulatorHarness"
        ) as MockSH:
            instance = MagicMock()
            MockSH.return_value = instance
            automation = ScreenshotAutomation(
                bundle_id="com.example.test",
                output_dir=output_dir,
            )
        MockSH.assert_called_once()
        assert automation.simulator_harness is instance


class TestCreateStandardScreenshotSpecsEdgeCases:
    """Additional create_standard_screenshot_specs coverage."""

    def test_create_specs_all_locales(self):
        """Specs with all locales should cover 6 locales x 2 devices x 3 screens."""
        all_locales = list(Locale)
        specs = create_standard_screenshot_specs(
            "FullApp",
            devices=[DeviceType.IPHONE_15_PRO],
            locales=all_locales,
        )
        expected = len(all_locales) * 3  # 3 screens per locale per device
        assert len(specs) == expected

    def test_spec_steps_are_populated(self):
        """Each spec should have at least one step."""
        specs = create_standard_screenshot_specs("App")
        for spec in specs:
            assert len(spec.steps) > 0

    def test_spec_light_mode_default(self):
        """All generated specs should default to light_mode=True."""
        specs = create_standard_screenshot_specs("App")
        for spec in specs:
            assert spec.light_mode is True


class TestGenerateAppStoreScreenshotsEdgeCases:
    """Additional generate_appstore_screenshots tests."""

    @pytest.mark.asyncio
    async def test_generate_empty_specs_returns_empty_result(self, automation, tmp_path):
        """Empty spec list should return success with zero screenshots."""
        app_path = tmp_path / "App.app"
        app_path.mkdir()

        result = await automation.generate_appstore_screenshots([], app_path)

        assert result.success is True
        assert result.total_screenshots == 0
        assert result.screenshot_sets == []

    @pytest.mark.asyncio
    async def test_generate_multiple_specs_aggregates_counts(self, automation, tmp_path):
        """Multiple specs should aggregate total_screenshots correctly."""
        app_path = tmp_path / "App.app"
        app_path.mkdir()

        specs = [
            ScreenshotSpec(
                name="screen1",
                device_type=DeviceType.IPHONE_15_PRO,
                steps=["launch"],
            ),
            ScreenshotSpec(
                name="screen2",
                device_type=DeviceType.IPHONE_15,
                steps=["launch"],
            ),
        ]

        async def fake_capture_set(spec, path):
            return ScreenshotSet(
                device_type=spec.device_type,
                locale=spec.locale,
                screenshots=[tmp_path / "shot.png"],
            )

        with patch.object(automation, "_capture_screenshot_set", side_effect=fake_capture_set):
            result = await automation.generate_appstore_screenshots(specs, app_path)

        assert result.success is True
        assert result.total_screenshots == 2
        assert len(result.screenshot_sets) == 2

    @pytest.mark.asyncio
    async def test_generate_partial_failure_marks_result_failed(self, automation, tmp_path):
        """One failing spec in a batch should mark success=False but continue others."""
        app_path = tmp_path / "App.app"
        app_path.mkdir()

        specs = [
            ScreenshotSpec(
                name="good",
                device_type=DeviceType.IPHONE_15_PRO,
                steps=["launch"],
            ),
            ScreenshotSpec(
                name="bad",
                device_type=DeviceType.IPHONE_15,
                steps=["launch"],
            ),
        ]

        call_count = 0

        async def mixed_capture(spec, path):
            nonlocal call_count
            call_count += 1
            if spec.name == "bad":
                raise RuntimeError("Boom")
            return ScreenshotSet(
                device_type=spec.device_type,
                locale=spec.locale,
                screenshots=[],
            )

        with patch.object(automation, "_capture_screenshot_set", side_effect=mixed_capture):
            result = await automation.generate_appstore_screenshots(specs, app_path)

        assert result.success is False
        assert len(result.errors) == 1
        assert "Boom" in result.errors[0]
        # The good spec still got collected
        assert len(result.screenshot_sets) == 1


class TestGenerateDeviceFramesVariants:
    """Test generate_device_frames for various input sizes."""

    @pytest.mark.asyncio
    async def test_generate_device_frames_empty_list(self, automation):
        framed = await automation.generate_device_frames([], DeviceType.IPHONE_15_PRO)
        assert framed == []

    @pytest.mark.asyncio
    async def test_generate_device_frames_suffix_naming(self, automation, tmp_path):
        """Framed files should have _framed suffix before extension."""
        screenshots = [tmp_path / "img.png"]
        for s in screenshots:
            s.write_bytes(b"PNG")

        framed = await automation.generate_device_frames(screenshots, DeviceType.IPAD_PRO_13)

        assert len(framed) == 1
        assert framed[0].name == "img_framed.png"

    @pytest.mark.asyncio
    async def test_generate_device_frames_multiple_images(self, automation, tmp_path):
        """Multiple screenshots should each get a framed variant."""
        screenshots = [tmp_path / f"s{i}.png" for i in range(5)]
        for s in screenshots:
            s.write_bytes(b"PNG")

        framed = await automation.generate_device_frames(screenshots, DeviceType.IPHONE_15_PRO)

        assert len(framed) == 5
        assert all("_framed" in p.name for p in framed)


# ===========================================================================
# Module import / __init__ smoke tests
# ===========================================================================


class TestModuleImports:
    """Verify all four target modules export their public symbols."""

    def test_swift_codegen_exports(self):
        from forge_harness.ios_harness.swift_codegen import (
            ActorSpec,
            Method,
            Property,
            SwiftCodeGen,
            SwiftType,
            ViewModelSpec,
            ViewSpec,
        )
        assert SwiftCodeGen is not None
        assert SwiftType is not None

    def test_quality_gates_exports(self):
        from forge_harness.ios_harness.quality_gates import (
            QualityGatesHarness,
            QualityReport,
            Severity,
            Violation,
        )
        assert QualityGatesHarness is not None
        assert Severity.ERROR is not None

    def test_build_harness_exports(self):
        from forge_harness.ios_harness.build_harness import (
            BuildConfig,
            BuildConfiguration,
            BuildError,
            BuildHarness,
            BuildResult,
        )
        assert BuildHarness is not None
        assert BuildConfiguration.DEBUG is not None

    def test_screenshot_automation_exports(self):
        from forge_harness.ios_harness.screenshot_automation import (
            Locale,
            ScreenshotAutomation,
            ScreenshotResult,
            ScreenshotSet,
            ScreenshotSize,
            ScreenshotSpec,
            create_standard_screenshot_specs,
        )
        assert ScreenshotAutomation is not None
        assert create_standard_screenshot_specs is not None

    def test_ios_harness_init_exports(self):
        """The ios_harness package __init__ should export all major symbols."""
        from forge_harness.ios_harness import (
            BuildHarness,
            QualityGatesHarness,
            ScreenshotAutomation,
            SwiftCodeGen,
            ViewModelSpec,
        )
        assert all(
            x is not None
            for x in [BuildHarness, QualityGatesHarness, ScreenshotAutomation, SwiftCodeGen, ViewModelSpec]
        )
