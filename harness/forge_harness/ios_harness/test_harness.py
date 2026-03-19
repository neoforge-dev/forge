"""
Test Harness for iOS Projects
==============================

Automates XCTest execution and reporting for FORGE iOS projects.

Features:
- XCTest suite execution
- Test result parsing
- Code coverage collection
- Test failure analysis
- Performance metrics
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestConfig:
    """Configuration for XCTest execution."""

    scheme: str  # Xcode scheme name
    destination: str = "platform=iOS Simulator,name=iPhone 15 Pro,OS=17.0"
    enable_code_coverage: bool = True  # Collect code coverage
    test_plan: str | None = None  # Test plan name (if using)
    parallel_testing: bool = True  # Run tests in parallel
    derived_data_path: Path | None = None  # Custom DerivedData location


@dataclass
class TestCase:
    """Represents a single test case result."""

    name: str  # Test method name (e.g., "testUserLogin")
    class_name: str  # Test class (e.g., "AuthServiceTests")
    passed: bool
    duration: float  # Seconds
    failure_message: str | None = None


@dataclass
class TestSuite:
    """Represents a test suite (test class)."""

    name: str
    test_cases: list[TestCase] = field(default_factory=list)
    passed: bool = True
    total_duration: float = 0.0

    @property
    def pass_count(self) -> int:
        """Count of passed tests."""
        return sum(1 for tc in self.test_cases if tc.passed)

    @property
    def fail_count(self) -> int:
        """Count of failed tests."""
        return sum(1 for tc in self.test_cases if not tc.passed)

    @property
    def total_count(self) -> int:
        """Total test count."""
        return len(self.test_cases)


@dataclass
class CoverageReport:
    """Code coverage metrics."""

    line_coverage: float  # Percentage (0-100)
    files_covered: int
    total_files: int
    lines_covered: int
    total_lines: int


@dataclass
class TestResult:
    """Result of test execution."""

    success: bool
    test_suites: list[TestSuite] = field(default_factory=list)
    coverage: CoverageReport | None = None
    total_duration: float = 0.0
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0

    @property
    def total_tests(self) -> int:
        """Total number of tests."""
        return sum(suite.total_count for suite in self.test_suites)

    @property
    def passed_tests(self) -> int:
        """Number of passed tests."""
        return sum(suite.pass_count for suite in self.test_suites)

    @property
    def failed_tests(self) -> int:
        """Number of failed tests."""
        return sum(suite.fail_count for suite in self.test_suites)

    @property
    def pass_rate(self) -> float:
        """Test pass rate percentage."""
        if self.total_tests == 0:
            return 0.0
        return round((self.passed_tests / self.total_tests) * 100, 2)


class TestHarness:
    """Automate XCTest execution."""

    def __init__(self, project_path: Path):
        """
        Initialize test harness.

        Args:
            project_path: Path to iOS project directory (contains .xcodeproj)
        """
        self.project_path = Path(project_path)
        self._validate_project()

    def _validate_project(self) -> None:
        """Validate project directory exists and contains .xcodeproj."""
        if not self.project_path.exists():
            raise ValueError(f"Project path does not exist: {self.project_path}")

        # Find .xcodeproj file
        xcodeproj_files = list(self.project_path.glob("*.xcodeproj"))
        if not xcodeproj_files:
            raise ValueError(f"No .xcodeproj found in {self.project_path}")

        self.xcodeproj_path = xcodeproj_files[0]

    async def test(self, config: TestConfig) -> TestResult:
        """
        Run XCTest suite.

        Args:
            config: Test configuration

        Returns:
            TestResult with pass/fail status, coverage data
        """
        import tempfile
        import time

        start_time = time.time()

        # Use a temp dir for the xcresult bundle so we can parse results
        # via xcresulttool — xcodebuild routes test-case output through a
        # daemon that doesn't write to stdout/stderr when piped.
        with tempfile.TemporaryDirectory() as tmp_dir:
            result_bundle = Path(tmp_dir) / "result.xcresult"

            # Construct xcodebuild test command
            cmd = self._build_test_command(config, result_bundle_path=result_bundle)

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=self.project_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

                stdout, _ = await proc.communicate()
                total_duration = time.time() - start_time

                stdout_str = stdout.decode() if stdout else ""
                returncode = proc.returncode or 0

                # Primary: parse structured results from xcresult bundle
                test_suites = await self._parse_xcresult(result_bundle)

                # Fallback: parse plain-text output if xcresult parsing fails
                if not test_suites:
                    test_suites = self._parse_test_output(stdout_str)

                # Extract coverage if enabled
                coverage = None
                if config.enable_code_coverage:
                    coverage = await self._extract_coverage(config, result_bundle)

                return TestResult(
                    success=returncode == 0,
                    test_suites=test_suites,
                    coverage=coverage,
                    total_duration=round(total_duration, 2),
                    stdout=stdout_str,
                    stderr="",
                    exit_code=returncode,
                )

            except FileNotFoundError:
                return TestResult(
                    success=False,
                    stdout="",
                    stderr="xcodebuild not found. Install Xcode Command Line Tools: xcode-select --install",
                )
            except Exception as e:
                return TestResult(
                    success=False,
                    stdout="",
                    stderr=f"Test execution failed: {str(e)}",
                )

    def _build_test_command(
        self,
        config: TestConfig,
        result_bundle_path: Path | None = None,
    ) -> list[str]:
        """Construct xcodebuild test command."""
        cmd = [
            "xcodebuild",
            "test",
            "-project",
            str(self.xcodeproj_path),
            "-scheme",
            config.scheme,
            "-destination",
            config.destination,
        ]

        # Add code coverage
        if config.enable_code_coverage:
            cmd.append("-enableCodeCoverage")
            cmd.append("YES")

        # Add test plan if specified
        if config.test_plan:
            cmd.extend(["-testPlan", config.test_plan])

        # Add parallel testing
        if config.parallel_testing:
            cmd.extend(["-parallel-testing-enabled", "YES"])

        # Add derived data path if specified
        if config.derived_data_path:
            cmd.extend(["-derivedDataPath", str(config.derived_data_path)])

        # Save structured results so we can parse via xcresulttool
        if result_bundle_path:
            cmd.extend(["-resultBundlePath", str(result_bundle_path)])

        return cmd

    async def _xcresulttool(self, *args: str) -> dict | None:
        """Run xcrun xcresulttool get object --legacy and return parsed JSON."""
        import json

        proc = await asyncio.create_subprocess_exec(
            "xcrun",
            "xcresulttool",
            "get",
            "object",
            "--legacy",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0 or not stdout:
            return None
        try:
            return json.loads(stdout.decode())
        except Exception:
            return None

    async def _parse_xcresult(self, bundle_path: Path) -> list[TestSuite]:
        """
        Parse test results from an .xcresult bundle using xcresulttool.

        xcodebuild routes test-case output through XPC daemons that do not
        write to stdout/stderr when the process is piped.  The xcresult
        bundle is the authoritative source for pass/fail data.

        Xcode >= 16 requires --legacy flag and a two-step fetch:
          1. Get root object  → extract testsRef.id
          2. Get testsRef object by ID → walk ActionTestMetadata nodes
        """
        if not bundle_path.exists():
            return []

        try:
            # Step 1: root object
            root = await self._xcresulttool("--path", str(bundle_path), "--format", "json")
            if not root:
                return []

            # Step 2: collect testsRef IDs from all actions
            suites: dict[str, TestSuite] = {}
            for action in root.get("actions", {}).get("_values", []):
                tests_ref = action.get("actionResult", {}).get("testsRef", {})
                ref_id = tests_ref.get("id", {}).get("_value", "")
                if not ref_id:
                    continue

                tests_data = await self._xcresulttool(
                    "--path", str(bundle_path), "--id", ref_id, "--format", "json"
                )
                if not tests_data:
                    continue

                # Walk testableSummaries → tests → subtests recursively
                for summary in tests_data.get("summaries", {}).get("_values", []):
                    for testable in summary.get("testableSummaries", {}).get("_values", []):
                        for top_group in testable.get("tests", {}).get("_values", []):
                            self._walk_test_node(top_group, suites)

            return list(suites.values())

        except Exception:
            return []

    def _walk_test_node(self, node: dict, suites: dict[str, TestSuite]) -> None:
        """Recursively walk xcresult node tree, collecting ActionTestMetadata entries."""
        node_type = node.get("_type", {}).get("_name", "")

        if node_type == "ActionTestMetadata":
            identifier = node.get("identifier", {}).get("_value", "")
            # identifier format: "ClassName/testMethodName()"
            parts = identifier.split("/", 1)
            class_name = parts[0] if len(parts) == 2 else "Unknown"
            test_name = parts[1] if len(parts) == 2 else identifier

            status = node.get("testStatus", {}).get("_value", "")
            passed = status == "Success"
            duration = float(node.get("duration", {}).get("_value", "0") or "0")

            failure_msg = None
            for fsummary in node.get("failureSummaries", {}).get("_values", []):
                failure_msg = fsummary.get("message", {}).get("_value", "")
                break

            if class_name not in suites:
                suites[class_name] = TestSuite(name=class_name)
            tc = TestCase(
                name=test_name,
                class_name=class_name,
                passed=passed,
                duration=duration,
                failure_message=failure_msg,
            )
            suites[class_name].test_cases.append(tc)
            suites[class_name].total_duration += duration
            if not passed:
                suites[class_name].passed = False
            return

        # Recurse into subtests and nested groups
        for subtests in node.get("subtests", {}).get("_values", []):
            self._walk_test_node(subtests, suites)

    def _parse_test_output(self, output: str) -> list[TestSuite]:
        """
        Parse xcodebuild test output.

        Extracts test results from output like:
        Test Case '-[MyAppTests.AuthTests testLogin]' passed (0.123 seconds).
        Test Case '-[MyAppTests.AuthTests testLogout]' failed (0.045 seconds).
        """
        test_suites: dict[str, TestSuite] = {}

        # Pattern: Test Case '-[Target.ClassName testMethod]' passed/failed (duration seconds).
        test_pattern = re.compile(
            r"Test Case '-\[.+?\.(.+?)\s+(.+?)\]' (passed|failed) \((.+?) seconds\)\."
        )

        # Pattern for failure messages
        failure_pattern = re.compile(
            r"Test Case '-\[.+?\.(.+?)\s+(.+?)\]' failed.*?\n\s*(.+?)(?=\n\n|\nTest)",
            re.DOTALL,
        )

        # Extract failures first to get failure messages
        failures = {}
        for match in failure_pattern.finditer(output):
            class_name = match.group(1)
            test_name = match.group(2)
            failure_msg = match.group(3).strip()
            key = f"{class_name}.{test_name}"
            failures[key] = failure_msg

        # Extract test results
        for match in test_pattern.finditer(output):
            class_name = match.group(1)
            test_name = match.group(2)
            status = match.group(3)
            duration = float(match.group(4))

            # Get or create test suite
            if class_name not in test_suites:
                test_suites[class_name] = TestSuite(name=class_name)

            # Create test case
            test_key = f"{class_name}.{test_name}"
            test_case = TestCase(
                name=test_name,
                class_name=class_name,
                passed=(status == "passed"),
                duration=duration,
                failure_message=failures.get(test_key),
            )

            test_suites[class_name].test_cases.append(test_case)
            test_suites[class_name].total_duration += duration

            # Update suite pass status
            if not test_case.passed:
                test_suites[class_name].passed = False

        return list(test_suites.values())

    async def _extract_coverage(
        self,
        config: TestConfig,
        result_bundle_path: Path | None = None,
    ) -> CoverageReport | None:
        """
        Extract code coverage from the xcresult bundle via xcrun xccov.
        """
        if not result_bundle_path or not result_bundle_path.exists():
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                "xcrun",
                "xccov",
                "view",
                "--report",
                "--legacy",
                "--json",
                str(result_bundle_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0 or not stdout:
                return None

            import json

            data = json.loads(stdout.decode())
            line_cov = data.get("lineCoverage", 0.0) * 100
            targets = data.get("targets", [])
            files_covered = sum(
                1 for t in targets for f in t.get("files", []) if f.get("lineCoverage", 0) > 0
            )
            total_files = sum(len(t.get("files", [])) for t in targets)
            lines_covered = data.get("coveredLines", 0)
            total_lines = data.get("executableLines", 0)
            return CoverageReport(
                line_coverage=round(line_cov, 2),
                files_covered=files_covered,
                total_files=total_files,
                lines_covered=lines_covered,
                total_lines=total_lines,
            )
        except Exception:
            return None

    async def list_test_targets(self) -> list[str]:
        """
        List available test targets in the project.

        Returns:
            List of test target names
        """
        cmd = ["xcodebuild", "-list", "-project", str(self.xcodeproj_path), "-json"]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, _ = await proc.communicate()

            if proc.returncode == 0 and stdout:
                import json

                data = json.loads(stdout.decode())
                # Extract test targets (targets ending with "Tests")
                if "project" in data and "targets" in data["project"]:
                    return [t for t in data["project"]["targets"] if t.endswith("Tests")]

            return []

        except Exception:
            return []
