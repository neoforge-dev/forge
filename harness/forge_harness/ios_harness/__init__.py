"""
iOS Harness for FORGE Portfolio
=================================

Production-grade iOS development harness for building SwiftUI MVPs with
autonomous AI assistance.

Key Features:
- Project bootstrap from templates (base-swiftui, mvvm-standard, enterprise)
- XcodeGen integration for .xcodeproj generation
- Build & test automation (xcodebuild, XCTest)
- Simulator automation (boot, install, launch, validate)
- App Store deployment (archive, sign, TestFlight, App Store)
- Swift code generation (@Observable, actors, SwiftUI)
- Quality gates (CLAUDE.md validation, strict concurrency)
- Living-docs integration (FORGE knowledge pyramid)
- Screenshot automation for App Store submissions

Usage:
    from forge_harness.ios_harness import ProjectBootstrap, ProjectSpec

    # Bootstrap new iOS project
    bootstrap = ProjectBootstrap(forge_root, templates_dir)
    result = await bootstrap.bootstrap_project(
        spec=ProjectSpec(
            name="InterviewSimulator",
            bundle_id="com.forge.codeswiftr.interview-simulator",
            domain="codeswiftr-com",
            template="mvvm-standard",
            features=["auth", "dashboard"],
            backend_url="https://api.codeswiftr.com",
        ),
        output_dir=Path("codeswiftr-com/interview-simulator/ios"),
    )
"""

__version__ = "0.4.0"

# Phase 1: Project Bootstrap
# Phase 4: Deployment & Automation
from .appstore_harness import (
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

# Phase 2: Build & Test Automation
from .build_harness import (
    BuildConfig,
    BuildConfiguration,
    BuildError,
    BuildHarness,
    BuildResult,
)

# Phase 6: Error Parsing
from .error_parser import (  # noqa: F401
    CompilationError,
    ErrorSeverity,
    ErrorType,
    XcodeErrorParser,
)

# Phase 3: Swift Code Generation
from .feature_scaffold import FeatureScaffold
from .project_bootstrap import (
    BootstrapResult,
    ProjectBootstrap,
    ProjectSpec,
    ScaffoldResult,
    TemplateType,
)
from .quality_gates import (
    QualityGatesHarness,
    QualityReport,
    Severity,
    Violation,
)
from .screenshot_automation import (
    Locale,
    ScreenshotAutomation,
    ScreenshotResult,
    ScreenshotSet,
    ScreenshotSize,
    ScreenshotSpec,
    create_standard_screenshot_specs,
)
from .simulator_harness import (
    DeviceType,
    InstallResult,
    LaunchResult,
    SimulatorDevice,
    SimulatorHarness,
    SimulatorState,
)
from .swift_codegen import (
    ActorSpec,
    Method,
    Property,
    SwiftCodeGen,
    SwiftType,
    ViewModelSpec,
    ViewSpec,
)
from .test_harness import (
    CoverageReport,
    TestCase,
    TestConfig,
    TestHarness,
    TestResult,
    TestSuite,
)

__all__ = [
    # Project Bootstrap
    "ProjectBootstrap",
    "ProjectSpec",
    "BootstrapResult",
    "ScaffoldResult",
    "TemplateType",
    # Build Automation
    "BuildHarness",
    "BuildConfig",
    "BuildConfiguration",
    "BuildResult",
    "BuildError",
    # Test Automation
    "TestHarness",
    "TestConfig",
    "TestResult",
    "TestCase",
    "TestSuite",
    "CoverageReport",
    # Quality Gates
    "QualityGatesHarness",
    "QualityReport",
    "Violation",
    "Severity",
    # Swift Code Generation
    "SwiftCodeGen",
    "ViewModelSpec",
    "ActorSpec",
    "ViewSpec",
    "Property",
    "Method",
    "SwiftType",
    "FeatureScaffold",
    # Simulator Automation
    "SimulatorHarness",
    "SimulatorDevice",
    "SimulatorState",
    "DeviceType",
    "LaunchResult",
    "InstallResult",
    # App Store Deployment
    "AppStoreHarness",
    "ArchiveConfig",
    "ArchiveResult",
    "ExportConfig",
    "ExportResult",
    "ExportMethod",
    "UploadConfig",
    "UploadResult",
    "ReleaseType",
    "ReleaseInfo",
    "create_release_workflow",
    # Screenshot Automation
    "ScreenshotAutomation",
    "ScreenshotSpec",
    "ScreenshotSet",
    "ScreenshotResult",
    "ScreenshotSize",
    "Locale",
    "create_standard_screenshot_specs",
    # Error Parsing
    "XcodeErrorParser",
    "CompilationError",
    "ErrorType",
    "ErrorSeverity",
]
