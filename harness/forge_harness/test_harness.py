"""
Test Harness Module
===================

Analyze test coverage gaps and generate tests to improve coverage.

Provides:
- Coverage gap analysis
- Automated test generation
- Test quality assessment
"""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CoverageGap:
    """Represents a coverage gap in a file."""

    file_path: str
    coverage_percent: float
    missing_lines: list[int]
    missing_branches: list[tuple[int, int]] | None = None
    priority: str = "medium"  # high, medium, low

    @property
    def gap_severity(self) -> str:
        """Determine severity based on coverage."""
        if self.coverage_percent < 50:
            return "critical"
        elif self.coverage_percent < 70:
            return "high"
        elif self.coverage_percent < 80:
            return "medium"
        return "low"


@dataclass
class GeneratedTestResult:
    """Result of test generation."""

    file_path: str
    tests_generated: int
    test_code: str
    estimated_coverage_improvement: float
    errors: list[str] = field(default_factory=list)


@dataclass
class CoverageReport:
    """Overall coverage report."""

    total_coverage: float
    files_analyzed: int
    gaps: list[CoverageGap]
    recommendations: list[str]


@dataclass
class QualityReport:
    """Test quality assessment report."""

    total_tests: int
    passing_tests: int
    failing_tests: int
    test_duration_seconds: float
    coverage_percent: float
    quality_score: float  # 0-100
    issues: list[str]


class CoverageHarness:
    """
    Analyze coverage gaps and generate tests.

    Capabilities:
    - Parse coverage reports
    - Identify untested code paths
    - Generate test stubs
    - Assess test quality
    """

    # Coverage targets by file type
    COVERAGE_TARGETS = {
        "api": 85.0,  # API endpoints
        "services": 80.0,  # Business logic
        "models": 70.0,  # Data models
        "utils": 75.0,  # Utility functions
        "default": 70.0,  # Default target
    }

    def __init__(
        self,
        project_dir: Path,
        target_coverage: float = 80.0,
    ):
        """
        Initialize test harness.

        Args:
            project_dir: Path to project root
            target_coverage: Target coverage percentage
        """
        self.project_dir = Path(project_dir)
        self.target_coverage = target_coverage
        self.backend_dir = self.project_dir / "backend"
        self.frontend_dir = self.project_dir / "frontend"

    def _get_coverage_target(self, file_path: str) -> float:
        """Get coverage target based on file path."""
        for category, target in self.COVERAGE_TARGETS.items():
            if category in file_path:
                return target
        return self.COVERAGE_TARGETS["default"]

    def _run_coverage(self) -> dict[str, Any] | None:
        """Run pytest with coverage and return JSON report."""
        if not self.backend_dir.exists():
            return None

        # Run coverage
        subprocess.run(
            ["uv", "run", "pytest", "--cov=app", "--cov-report=json", "-q"],
            cwd=self.backend_dir,
            capture_output=True,
            text=True,
        )

        # Parse coverage.json
        cov_file = self.backend_dir / "coverage.json"
        if cov_file.exists():
            try:
                return json.loads(cov_file.read_text())
            except json.JSONDecodeError:
                return None

        return None

    async def analyze_coverage_gaps(self) -> CoverageReport:
        """
        Analyze coverage and identify gaps.

        Returns:
            CoverageReport with gaps and recommendations
        """
        cov_data = self._run_coverage()

        if not cov_data:
            return CoverageReport(
                total_coverage=0.0,
                files_analyzed=0,
                gaps=[],
                recommendations=["Run coverage analysis first"],
            )

        total_coverage = cov_data.get("totals", {}).get("percent_covered", 0.0)
        files = cov_data.get("files", {})

        gaps = []
        for file_path, file_data in files.items():
            coverage = file_data.get("summary", {}).get("percent_covered", 0)
            target = self._get_coverage_target(file_path)

            if coverage < target:
                missing_lines = file_data.get("missing_lines", [])
                gap = CoverageGap(
                    file_path=file_path,
                    coverage_percent=coverage,
                    missing_lines=missing_lines,
                    priority="high" if coverage < 50 else "medium",
                )
                gaps.append(gap)

        # Sort by severity (lowest coverage first)
        gaps.sort(key=lambda g: g.coverage_percent)

        # Generate recommendations
        recommendations = self._generate_recommendations(gaps, total_coverage)

        return CoverageReport(
            total_coverage=total_coverage,
            files_analyzed=len(files),
            gaps=gaps,
            recommendations=recommendations,
        )

    def _generate_recommendations(
        self,
        gaps: list[CoverageGap],
        total_coverage: float,
    ) -> list[str]:
        """Generate actionable recommendations."""
        recommendations = []

        if total_coverage < self.target_coverage:
            diff = self.target_coverage - total_coverage
            recommendations.append(
                f"Overall coverage is {total_coverage:.1f}%, "
                f"need {diff:.1f}% more to reach {self.target_coverage}% target"
            )

        # Prioritize critical gaps
        critical_gaps = [g for g in gaps if g.gap_severity == "critical"]
        if critical_gaps:
            files = ", ".join(g.file_path.split("/")[-1] for g in critical_gaps[:3])
            recommendations.append(f"Critical: Focus on {files} (coverage < 50%)")

        # Identify high-impact files
        high_gaps = [g for g in gaps if g.gap_severity == "high"]
        if high_gaps:
            files = ", ".join(g.file_path.split("/")[-1] for g in high_gaps[:3])
            recommendations.append(f"High priority: Improve tests for {files}")

        # Look for service files (usually most important)
        service_gaps = [g for g in gaps if "service" in g.file_path.lower()]
        if service_gaps:
            recommendations.append(
                f"Business logic: {len(service_gaps)} service files need more tests"
            )

        return recommendations

    async def generate_tests_for_file(
        self,
        file_path: Path,
        missing_lines: list[int] | None = None,
    ) -> GeneratedTestResult:
        """
        Generate test stubs for a file.

        Args:
            file_path: Path to source file
            missing_lines: Specific lines to target (optional)

        Returns:
            TestGenerationResult with generated test code
        """
        if not file_path.exists():
            return GeneratedTestResult(
                file_path=str(file_path),
                tests_generated=0,
                test_code="",
                estimated_coverage_improvement=0.0,
                errors=[f"File not found: {file_path}"],
            )

        source_code = file_path.read_text()

        # Parse source to identify testable functions
        functions = self._extract_functions(source_code)
        classes = self._extract_classes(source_code)

        # Generate test stubs
        test_code = self._generate_test_stubs(
            file_path=file_path,
            functions=functions,
            classes=classes,
            missing_lines=missing_lines,
        )

        tests_count = test_code.count("def test_")

        return GeneratedTestResult(
            file_path=str(file_path),
            tests_generated=tests_count,
            test_code=test_code,
            estimated_coverage_improvement=tests_count * 5.0,  # Rough estimate
        )

    def _extract_functions(self, source: str) -> list[str]:
        """Extract function names from source code."""
        import re

        pattern = r"^(?:async\s+)?def\s+(\w+)\s*\("
        matches = re.findall(pattern, source, re.MULTILINE)
        # Filter out private functions and test functions
        return [m for m in matches if not m.startswith("_") and not m.startswith("test_")]

    def _extract_classes(self, source: str) -> list[str]:
        """Extract class names from source code."""
        import re

        pattern = r"^class\s+(\w+)\s*[:\(]"
        matches = re.findall(pattern, source, re.MULTILINE)
        return matches

    def _generate_test_stubs(
        self,
        file_path: Path,
        functions: list[str],
        classes: list[str],
        missing_lines: list[int] | None = None,
    ) -> str:
        """Generate pytest test stubs."""
        module_name = file_path.stem
        relative_path = str(
            file_path.relative_to(self.backend_dir) if self.backend_dir.exists() else file_path
        )
        import_path = relative_path.replace("/", ".").replace(".py", "")

        lines = [
            f'"""Tests for {module_name}."""',
            "",
            "import pytest",
            f"from {import_path} import (",
        ]

        # Add imports for functions and classes
        for cls in classes:
            lines.append(f"    {cls},")
        for func in functions[:5]:  # Limit to first 5 functions
            lines.append(f"    {func},")
        lines.append(")")
        lines.append("")

        # Generate class tests
        for cls in classes:
            lines.extend(
                [
                    f"class Test{cls}:",
                    f'    """Tests for {cls} class."""',
                    "",
                    "    @pytest.fixture",
                    "    def instance(self):",
                    f'        """Create {cls} instance for testing."""',
                    f"        return {cls}()  # Add required arguments",
                    "",
                    f"    def test_{cls.lower()}_creation(self, instance):",
                    f'        """{cls} can be instantiated."""',
                    "        assert instance is not None",
                    "",
                ]
            )

        # Generate function tests
        for func in functions[:5]:
            is_async = "async" in func  # Simplified check
            decorator = "@pytest.mark.asyncio\n    " if is_async else ""
            async_prefix = "async " if is_async else ""
            await_prefix = "await " if is_async else ""

            lines.extend(
                [
                    f"class Test{func.title().replace('_', '')}:",
                    f'    """Tests for {func} function."""',
                    "",
                    f"    {decorator}{async_prefix}def test_{func}_basic(self):",
                    f'        """{func} works with valid input."""',
                    f"        result = {await_prefix}{func}()  # Add required arguments",
                    "        assert result is not None  # Add specific assertions",
                    "",
                ]
            )

        return "\n".join(lines)

    async def assess_test_quality(self) -> QualityReport:
        """
        Assess overall test quality.

        Returns:
            TestQualityReport with quality metrics
        """
        if not self.backend_dir.exists():
            return QualityReport(
                total_tests=0,
                passing_tests=0,
                failing_tests=0,
                test_duration_seconds=0.0,
                coverage_percent=0.0,
                quality_score=0.0,
                issues=["Backend directory not found"],
            )

        # Run tests with timing
        result = subprocess.run(
            ["uv", "run", "pytest", "--cov=app", "-v", "-q"],
            cwd=self.backend_dir,
            capture_output=True,
            text=True,
        )

        # Parse output
        output = result.stdout
        total = output.count(" passed") + output.count(" failed")
        passing = output.count(" passed")
        failing = output.count(" failed")

        # Get coverage
        cov_data = self._run_coverage()
        coverage = cov_data.get("totals", {}).get("percent_covered", 0.0) if cov_data else 0.0

        # Calculate quality score
        quality_score = self._calculate_quality_score(
            passing_ratio=passing / max(total, 1),
            coverage=coverage,
            failing=failing,
        )

        # Identify issues
        issues = []
        if failing > 0:
            issues.append(f"{failing} failing tests need attention")
        if coverage < 70:
            issues.append(f"Coverage {coverage:.1f}% is below 70% threshold")
        if total < 10:
            issues.append("Low test count - consider adding more tests")

        return QualityReport(
            total_tests=total,
            passing_tests=passing,
            failing_tests=failing,
            test_duration_seconds=0.0,  # Would parse from output
            coverage_percent=coverage,
            quality_score=quality_score,
            issues=issues,
        )

    def _calculate_quality_score(
        self,
        passing_ratio: float,
        coverage: float,
        failing: int,
    ) -> float:
        """Calculate overall quality score (0-100)."""
        # Weights: 40% passing ratio, 40% coverage, 20% penalty for failures
        passing_score = passing_ratio * 40
        coverage_score = (coverage / 100) * 40
        failure_penalty = min(failing * 5, 20)

        return max(0, passing_score + coverage_score - failure_penalty)

    async def get_untested_functions(self) -> list[dict[str, Any]]:
        """
        Get list of functions without tests.

        Returns:
            List of untested function details
        """
        report = await self.analyze_coverage_gaps()
        untested = []

        for gap in report.gaps:
            if gap.coverage_percent < 50:
                file_path = self.backend_dir / gap.file_path
                if file_path.exists():
                    source = file_path.read_text()
                    functions = self._extract_functions(source)
                    untested.append(
                        {
                            "file": gap.file_path,
                            "coverage": gap.coverage_percent,
                            "functions": functions,
                            "missing_lines": gap.missing_lines[:10],  # First 10
                        }
                    )

        return untested


def create_coverage_harness(
    project_dir: Path,
    target_coverage: float = 80.0,
) -> CoverageHarness:
    """
    Factory function to create a coverage harness.

    Args:
        project_dir: Path to project root
        target_coverage: Target coverage percentage

    Returns:
        Configured CoverageHarness instance
    """
    return CoverageHarness(
        project_dir=project_dir,
        target_coverage=target_coverage,
    )
