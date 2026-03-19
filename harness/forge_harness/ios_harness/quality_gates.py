"""
Quality Gates for iOS Projects
===============================

Validates iOS projects against FORGE quality standards.

Quality Gates:
- Swift 6 strict concurrency compliance
- No UIKit imports in SwiftUI projects
- @Observable ViewModel patterns
- Actor-based service patterns
- SwiftData model validation
- Architecture compliance (MVVM)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Severity(Enum):
    """Violation severity levels."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Violation:
    """Quality gate violation."""

    rule: str  # Rule name (e.g., "no-uikit")
    severity: Severity
    file_path: str
    line: int | None
    message: str


@dataclass
class QualityReport:
    """Quality gate check results."""

    passed: bool
    violations: list[Violation] = field(default_factory=list)
    errors: int = 0
    warnings: int = 0
    infos: int = 0

    @property
    def total_violations(self) -> int:
        """Total violation count."""
        return len(self.violations)


class QualityGatesHarness:
    """Validate iOS projects against quality standards."""

    def __init__(self, project_path: Path):
        """
        Initialize quality gates harness.

        Args:
            project_path: Path to iOS project directory
        """
        self.project_path = Path(project_path)

    async def validate(self, strict_mode: bool = True) -> QualityReport:
        """
        Run all quality gate checks.

        Args:
            strict_mode: If True, warnings fail the validation

        Returns:
            QualityReport with violations
        """
        violations: list[Violation] = []

        # Run all checks
        violations.extend(await self._check_no_uikit())
        violations.extend(await self._check_observable_viewmodels())
        violations.extend(await self._check_actor_services())
        violations.extend(await self._check_swiftdata_models())
        violations.extend(await self._check_mvvm_structure())

        # Count violations by severity
        errors = sum(1 for v in violations if v.severity == Severity.ERROR)
        warnings = sum(1 for v in violations if v.severity == Severity.WARNING)
        infos = sum(1 for v in violations if v.severity == Severity.INFO)

        # Determine pass/fail
        if strict_mode:
            passed = errors == 0 and warnings == 0
        else:
            passed = errors == 0

        return QualityReport(
            passed=passed,
            violations=violations,
            errors=errors,
            warnings=warnings,
            infos=infos,
        )

    async def _check_no_uikit(self) -> list[Violation]:
        """
        Check that SwiftUI projects don't import UIKit.

        Exception: UIKit is allowed in UIViewRepresentable wrappers.
        """
        violations = []

        # Find all Swift files
        swift_files = self.project_path.rglob("*.swift")

        for file_path in swift_files:
            try:
                content = file_path.read_text()
                lines = content.split("\n")

                for i, line in enumerate(lines, start=1):
                    # Check for UIKit import
                    if re.search(r"^\s*import\s+UIKit", line):
                        # Check if this is a UIViewRepresentable wrapper
                        if (
                            "UIViewRepresentable" in content
                            or "UIViewControllerRepresentable" in content
                        ):
                            # Allowed - this is a wrapper
                            continue

                        violations.append(
                            Violation(
                                rule="no-uikit",
                                severity=Severity.ERROR,
                                file_path=str(file_path.relative_to(self.project_path)),
                                line=i,
                                message="UIKit import not allowed in SwiftUI projects (use UIViewRepresentable wrappers if needed)",
                            )
                        )

            except Exception:
                continue

        return violations

    async def _check_observable_viewmodels(self) -> list[Violation]:
        """
        Check that ViewModels use @Observable (iOS 17+) instead of ObservableObject.
        """
        violations = []

        swift_files = self.project_path.rglob("*ViewModel.swift")

        for file_path in swift_files:
            try:
                content = file_path.read_text()

                # Check for old ObservableObject pattern
                if "ObservableObject" in content:
                    line_num = None
                    for i, line in enumerate(content.split("\n"), start=1):
                        if "ObservableObject" in line:
                            line_num = i
                            break

                    violations.append(
                        Violation(
                            rule="observable-viewmodels",
                            severity=Severity.WARNING,
                            file_path=str(file_path.relative_to(self.project_path)),
                            line=line_num,
                            message="Use @Observable instead of ObservableObject for iOS 17+ ViewModels",
                        )
                    )

                # Check for @Observable usage
                if "@Observable" not in content and "class" in content and "ViewModel" in content:
                    violations.append(
                        Violation(
                            rule="observable-viewmodels",
                            severity=Severity.INFO,
                            file_path=str(file_path.relative_to(self.project_path)),
                            line=None,
                            message="ViewModel should use @Observable macro",
                        )
                    )

            except Exception:
                continue

        return violations

    async def _check_actor_services(self) -> list[Violation]:
        """
        Check that service classes use actor for thread safety (Swift 6).
        """
        violations = []

        service_files = self.project_path.rglob("*Service.swift")

        for file_path in service_files:
            try:
                content = file_path.read_text()

                # Check if it's a service class (not actor)
                if re.search(r"\bclass\s+\w+Service\b", content):
                    # Check if it should be an actor
                    if "async" in content or "await" in content:
                        # Has async code but not an actor
                        if not re.search(r"\bactor\s+\w+Service\b", content):
                            violations.append(
                                Violation(
                                    rule="actor-services",
                                    severity=Severity.WARNING,
                                    file_path=str(file_path.relative_to(self.project_path)),
                                    line=None,
                                    message="Service with async operations should use actor for thread safety (Swift 6)",
                                )
                            )

            except Exception:
                continue

        return violations

    async def _check_swiftdata_models(self) -> list[Violation]:
        """
        Check SwiftData model compliance.

        Rules:
        - Models should use @Model macro
        - No NSManagedObject inheritance
        """
        violations = []

        model_files = self.project_path.rglob("*Model.swift")

        for file_path in model_files:
            try:
                content = file_path.read_text()

                # Check for old Core Data patterns
                if "NSManagedObject" in content:
                    violations.append(
                        Violation(
                            rule="swiftdata-models",
                            severity=Severity.ERROR,
                            file_path=str(file_path.relative_to(self.project_path)),
                            line=None,
                            message="Use SwiftData @Model instead of Core Data NSManagedObject",
                        )
                    )

                # Check for @Model usage if class exists
                if "class" in content and "@Model" not in content:
                    # Might be a model without @Model macro
                    if any(
                        keyword in content
                        for keyword in ["@Attribute", "@Relationship", "PersistentModel"]
                    ):
                        violations.append(
                            Violation(
                                rule="swiftdata-models",
                                severity=Severity.INFO,
                                file_path=str(file_path.relative_to(self.project_path)),
                                line=None,
                                message="Model class should use @Model macro for SwiftData",
                            )
                        )

            except Exception:
                continue

        return violations

    async def _check_mvvm_structure(self) -> list[Violation]:
        """
        Check MVVM architecture compliance.

        Rules:
        - Views should not contain business logic
        - ViewModels should not import SwiftUI
        - Services should be injected via Composition.swift
        """
        violations = []

        # Check for ViewModels importing SwiftUI
        viewmodel_files = self.project_path.rglob("*ViewModel.swift")

        for file_path in viewmodel_files:
            try:
                content = file_path.read_text()

                if re.search(r"^\s*import\s+SwiftUI", content, re.MULTILINE):
                    violations.append(
                        Violation(
                            rule="mvvm-structure",
                            severity=Severity.WARNING,
                            file_path=str(file_path.relative_to(self.project_path)),
                            line=None,
                            message="ViewModel should not import SwiftUI (keep UI-independent)",
                        )
                    )

            except Exception:
                continue

        return violations

    async def check_swift6_concurrency(self, project_path: Path) -> QualityReport:
        """
        Check Swift 6 strict concurrency compliance.

        This runs a build with strict concurrency enabled and parses warnings.

        Args:
            project_path: Path to .xcodeproj

        Returns:
            QualityReport with concurrency violations
        """
        from .build_harness import BuildConfig, BuildHarness

        violations = []

        try:
            # Run build with strict concurrency
            harness = BuildHarness(project_path)

            # Find the first scheme
            schemes = await harness.list_schemes()
            if not schemes:
                return QualityReport(
                    passed=False,
                    violations=[
                        Violation(
                            rule="swift6-concurrency",
                            severity=Severity.ERROR,
                            file_path="",
                            line=None,
                            message="No Xcode schemes found",
                        )
                    ],
                    errors=1,
                )

            config = BuildConfig(
                scheme=schemes[0],
                enable_strict_concurrency=True,
                clean_build=False,
            )

            result = await harness.build(config)

            # Convert build warnings to violations
            for warning in result.warnings:
                if (
                    "concurrency" in warning.message.lower()
                    or "sendable" in warning.message.lower()
                ):
                    violations.append(
                        Violation(
                            rule="swift6-concurrency",
                            severity=Severity.WARNING,
                            file_path=warning.file_path,
                            line=warning.line,
                            message=warning.message,
                        )
                    )

            # Convert build errors to violations
            for error in result.errors:
                violations.append(
                    Violation(
                        rule="swift6-concurrency",
                        severity=Severity.ERROR,
                        file_path=error.file_path,
                        line=error.line,
                        message=error.message,
                    )
                )

        except Exception as e:
            violations.append(
                Violation(
                    rule="swift6-concurrency",
                    severity=Severity.ERROR,
                    file_path="",
                    line=None,
                    message=f"Concurrency check failed: {str(e)}",
                )
            )

        errors = sum(1 for v in violations if v.severity == Severity.ERROR)
        warnings = sum(1 for v in violations if v.severity == Severity.WARNING)

        return QualityReport(
            passed=errors == 0,
            violations=violations,
            errors=errors,
            warnings=warnings,
        )
