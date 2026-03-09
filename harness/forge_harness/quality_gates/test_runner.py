"""Test runner quality gate for FORGE Harness.

Runs pytest tests with coverage and configurable options for use in pre-commit hooks
and continuous integration pipelines.
"""

import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class TestResult:
    """Result of a test run."""

    passed: int
    """Number of passing tests."""

    failed: int
    """Number of failed tests."""

    errors: int
    """Number of error tests."""

    skipped: int
    """Number of skipped tests."""

    duration: float
    """Total duration in seconds."""

    coverage_pct: float | None
    """Coverage percentage if available."""

    exit_code: int
    """Pytest exit code."""

    output: str
    """Full test output."""

    failed_tests: list[str]
    """Names of failed tests."""

    timestamp: datetime
    """When the test run completed."""

    @property
    def total(self) -> int:
        """Total number of tests."""
        return self.passed + self.failed + self.errors + self.skipped

    @property
    def success(self) -> bool:
        """True if all tests passed (no failures or errors)."""
        return self.failed == 0 and self.errors == 0


@dataclass
class TestRunnerConfig:
    """Configuration for test runner."""

    pattern: str = "test_*.py"
    """Test file pattern."""

    timeout: int = 300
    """Timeout in seconds (5 min default)."""

    coverage_threshold: float | None = None
    """Min coverage % required."""

    fail_fast: bool = False
    """Stop on first failure."""

    verbose: bool = True
    """Verbose output."""

    markers: str | None = None
    """Pytest markers to select."""

    ignore_patterns: list[str] = field(default_factory=list)
    """Patterns to ignore."""

    extra_args: list[str] = field(default_factory=list)
    """Extra pytest args."""


class TestRunner:
    """Runs pytest tests with coverage and configurable options."""

    def __init__(self, config: TestRunnerConfig | None = None):
        """Initialize with optional config.

        Args:
            config: Test runner configuration. Uses defaults if not provided.
        """
        self.config = config or TestRunnerConfig()

    def run_tests(
        self,
        path: Path,
        pattern: str | None = None,
        coverage: bool = True,
    ) -> TestResult:
        """Run tests in the given path.

        Args:
            path: Directory or file to test
            pattern: Override test file pattern
            coverage: Whether to collect coverage

        Returns:
            TestResult with all metrics
        """
        pattern = pattern or self.config.pattern

        # Build pytest command
        cmd = ["uv", "run", "pytest"]

        # Add path
        cmd.append(str(path))

        # Add pattern filter if it's not just test_*.py
        if pattern and pattern != "test_*.py":
            # Extract the test name pattern from the file pattern
            test_pattern = pattern.replace("test_", "").replace(".py", "")
            if test_pattern and test_pattern != "*":
                cmd.extend(["-k", test_pattern])

        # Add coverage if requested
        if coverage:
            cmd.extend(
                [
                    "--cov=forge_harness",
                    "--cov-report=term-missing",
                    "--cov-fail-under=0",  # Don't fail on coverage, we check manually
                ]
            )

        # Add config options
        if self.config.fail_fast:
            cmd.append("-x")
        if self.config.verbose:
            cmd.append("-v")
        if self.config.markers:
            cmd.extend(["-m", self.config.markers])
        for ignore in self.config.ignore_patterns:
            cmd.extend(["--ignore", ignore])
        cmd.extend(self.config.extra_args)

        # Run with timeout
        start_time = time.time()
        try:
            logger.info(f"Running tests: {' '.join(cmd)}")
            # Always run from current directory to maintain proper pytest context
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout,
                cwd=Path.cwd(),
            )
            duration = time.time() - start_time

            # Parse output
            test_result = self._parse_pytest_output(
                result.stdout + result.stderr,
                result.returncode,
                duration,
            )
            logger.info(
                f"Tests completed: {test_result.passed} passed, "
                f"{test_result.failed} failed, {test_result.errors} errors"
            )
            return test_result

        except subprocess.TimeoutExpired:
            logger.error(f"Test run timed out after {self.config.timeout}s")
            return TestResult(
                passed=0,
                failed=0,
                errors=1,
                skipped=0,
                duration=self.config.timeout,
                coverage_pct=None,
                exit_code=3,
                output=f"Test run timed out after {self.config.timeout}s",
                failed_tests=[],
                timestamp=datetime.now(),
            )

    def _parse_pytest_output(self, output: str, exit_code: int, duration: float) -> TestResult:
        """Parse pytest output for metrics.

        Args:
            output: Combined stdout and stderr from pytest
            exit_code: Process exit code
            duration: Test run duration in seconds

        Returns:
            TestResult with parsed metrics
        """
        # Initialize counters
        passed = failed = errors = skipped = 0

        # Parse test counts from summary line
        # Example: "5 passed, 2 failed, 1 error in 1.23s"
        # Look for various patterns pytest uses
        passed_match = re.search(r"(\d+) passed", output)
        if passed_match:
            passed = int(passed_match.group(1))

        failed_match = re.search(r"(\d+) failed", output)
        if failed_match:
            failed = int(failed_match.group(1))

        error_match = re.search(r"(\d+) error", output)
        if error_match:
            errors = int(error_match.group(1))

        skipped_match = re.search(r"(\d+) skipped", output)
        if skipped_match:
            skipped = int(skipped_match.group(1))

        # Parse coverage percentage
        # Example: "TOTAL    1234    123    90%"
        coverage_pattern = r"TOTAL\s+\d+\s+\d+\s+(\d+)%"
        coverage_match = re.search(coverage_pattern, output)
        coverage_pct = float(coverage_match.group(1)) if coverage_match else None

        # Parse failed test names
        # Example: "FAILED test_file.py::TestClass::test_method - AssertionError"
        failed_pattern = r"FAILED (.+?) -"
        failed_tests = re.findall(failed_pattern, output)

        return TestResult(
            passed=passed,
            failed=failed,
            errors=errors,
            skipped=skipped,
            duration=duration,
            coverage_pct=coverage_pct,
            exit_code=exit_code,
            output=output,
            failed_tests=failed_tests,
            timestamp=datetime.now(),
        )

    def check_coverage_threshold(self, result: TestResult) -> bool:
        """Check if coverage meets configured threshold.

        Args:
            result: Test result with coverage information

        Returns:
            True if coverage meets threshold or no threshold configured
        """
        if self.config.coverage_threshold is None:
            return True
        if result.coverage_pct is None:
            logger.warning("No coverage information available")
            return False
        meets_threshold = result.coverage_pct >= self.config.coverage_threshold
        if not meets_threshold:
            logger.warning(
                f"Coverage {result.coverage_pct}% below threshold {self.config.coverage_threshold}%"
            )
        return meets_threshold

    def run_pre_commit(self, path: Path) -> int:
        """Run as pre-commit hook.

        Args:
            path: Path to test

        Returns:
            Appropriate exit code:
            - 0: All tests pass, coverage OK
            - 1: Test failures
            - 2: Coverage below threshold
            - 3: Timeout or error
        """
        logger.info("Running pre-commit test gate")

        # Run tests
        result = self.run_tests(path)

        # Print summary
        summary = self.format_summary(result)
        print(summary)

        # Check for timeout/errors
        if result.exit_code == 3:
            logger.error("Test run timed out or had errors")
            return 3

        # Check for test failures
        if not result.success:
            logger.error(f"Tests failed: {result.failed} failed, {result.errors} errors")
            return 1

        # Check coverage threshold
        if not self.check_coverage_threshold(result):
            logger.error("Coverage below threshold")
            return 2

        logger.info("All tests passed and coverage meets threshold")
        return 0

    def format_summary(self, result: TestResult) -> str:
        """Format a human-readable summary of results.

        Args:
            result: Test result to format

        Returns:
            Formatted summary string
        """
        lines = []
        lines.append("=" * 60)
        lines.append("Test Summary")
        lines.append("=" * 60)

        # Test counts
        lines.append(f"Total:   {result.total} tests")
        lines.append(f"Passed:  {result.passed}")
        if result.failed > 0:
            lines.append(f"Failed:  {result.failed}")
        if result.errors > 0:
            lines.append(f"Errors:  {result.errors}")
        if result.skipped > 0:
            lines.append(f"Skipped: {result.skipped}")

        # Duration
        lines.append(f"Duration: {result.duration:.2f}s")

        # Coverage
        if result.coverage_pct is not None:
            coverage_str = f"Coverage: {result.coverage_pct:.1f}%"
            if self.config.coverage_threshold is not None:
                coverage_str += f" (threshold: {self.config.coverage_threshold}%)"
            lines.append(coverage_str)

        # Failed tests
        if result.failed_tests:
            lines.append("\nFailed Tests:")
            for test in result.failed_tests:
                lines.append(f"  - {test}")

        # Status
        lines.append("")
        if result.success:
            if self.check_coverage_threshold(result):
                lines.append("✅ All tests passed and coverage meets threshold")
            else:
                lines.append("⚠️  Tests passed but coverage below threshold")
        else:
            lines.append("❌ Tests failed")

        lines.append("=" * 60)

        return "\n".join(lines)
