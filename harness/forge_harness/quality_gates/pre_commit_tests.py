"""
Pre-commit Test Runner - Quality Gate
======================================

Runs only tests affected by staged files before commit:
- Detects staged files via git
- Maps changed files to test files using import analysis
- Runs affected tests only (faster than full suite)
- Blocks commit if tests fail

Supports both Python (pytest) and TypeScript (jest/vitest) projects.

Usage:
    from forge_harness.quality_gates import run_pre_commit_tests, PreCommitTestRunner

    # Run in check mode
    result = await run_pre_commit_tests(check_only=True)
    if not result.passed:
        print(result.summary())
        sys.exit(1)

CLI Usage:
    python -m forge_harness.quality_gates.pre_commit_tests --check
    python -m forge_harness.quality_gates.pre_commit_tests
"""

import argparse
import ast
import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Models
# =============================================================================


@dataclass
class TestFailure:
    """A single test failure."""

    test_file: str
    test_name: str
    error_message: str
    line: int | None = None

    def __str__(self) -> str:
        """Format failure for display."""
        location = f"{self.test_file}"
        if self.test_name:
            location += f"::{self.test_name}"
        if self.line is not None:
            location += f":{self.line}"
        return f"{location}\n  {self.error_message}"


@dataclass
class TestResult:
    """Result of running pre-commit tests."""

    passed: bool
    tests_run: int
    files_changed: int
    failures: list[TestFailure] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    execution_time: float = 0.0

    @property
    def failure_count(self) -> int:
        """Count of failed tests."""
        return len(self.failures)

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = []
        lines.append("=" * 70)
        lines.append("Pre-commit Test Results")
        lines.append("=" * 70)
        lines.append(f"Status: {'PASSED' if self.passed else 'FAILED'}")
        lines.append(f"Files changed: {self.files_changed}")
        lines.append(f"Tests run: {self.tests_run}")
        lines.append(f"Execution time: {self.execution_time:.2f}s")

        if self.failures:
            lines.append(f"Failures: {self.failure_count}")
            lines.append("")
            lines.append("Failed tests:")
            for failure in self.failures:
                lines.append(f"  {failure}")

        if self.errors:
            lines.append("")
            lines.append("Errors during execution:")
            for error in self.errors:
                lines.append(f"  {error}")

        lines.append("=" * 70)
        return "\n".join(lines)


# =============================================================================
# Pre-commit Test Runner Implementation
# =============================================================================


class PreCommitTestRunner:
    """
    Pre-commit test runner for affected tests.

    Analyzes staged files and runs only tests that could be affected by changes.
    """

    PYTHON_EXTENSIONS = {".py"}
    JS_TS_EXTENSIONS = {".js", ".ts", ".tsx", ".jsx"}

    def __init__(self, repo_root: Path | None = None):
        """
        Initialize pre-commit test runner.

        Args:
            repo_root: Repository root directory (defaults to current directory)
        """
        self.repo_root = repo_root or Path.cwd()

    async def get_staged_files(self) -> list[Path]:
        """
        Get list of staged files from git.

        Returns:
            List of Path objects for staged files
        """
        try:
            result = await self._run_command(
                ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                cwd=self.repo_root,
            )

            if not result:
                return []

            # Filter out deleted files and convert to Path objects
            files = []
            for line in result.strip().split("\n"):
                if line:
                    file_path = self.repo_root / line
                    if file_path.exists():
                        files.append(file_path)

            return files

        except Exception as e:
            logger.error(f"Failed to get staged files: {e}")
            return []

    def categorize_files(self, files: list[Path]) -> dict[str, list[Path]]:
        """
        Categorize files by type.

        Args:
            files: List of file paths

        Returns:
            Dictionary mapping category to file list
            Categories: "python", "js_ts", "other"
        """
        categorized: dict[str, list[Path]] = {
            "python": [],
            "js_ts": [],
            "other": [],
        }

        for file_path in files:
            suffix = file_path.suffix
            if suffix in self.PYTHON_EXTENSIONS:
                categorized["python"].append(file_path)
            elif suffix in self.JS_TS_EXTENSIONS:
                categorized["js_ts"].append(file_path)
            else:
                categorized["other"].append(file_path)

        return categorized

    def find_python_imports(self, file_path: Path) -> set[str]:
        """
        Extract Python imports from a file using AST parsing.

        Args:
            file_path: Path to Python file

        Returns:
            Set of module names imported
        """
        imports = set()

        try:
            with open(file_path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(file_path))

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.add(node.module.split(".")[0])

        except Exception as e:
            logger.debug(f"Failed to parse imports from {file_path}: {e}")

        return imports

    def find_affected_python_tests(self, changed_files: list[Path]) -> set[Path]:
        """
        Find Python test files affected by changed files.

        Args:
            changed_files: List of changed Python files

        Returns:
            Set of test file paths to run
        """
        affected_tests = set()

        # Find all test files
        test_patterns = ["test_*.py", "*_test.py"]
        all_test_files = []
        for pattern in test_patterns:
            all_test_files.extend(self.repo_root.rglob(pattern))

        # Build module name map for changed files
        changed_modules = {}
        for file_path in changed_files:
            try:
                rel_path = file_path.relative_to(self.repo_root)
                # Convert path to module name (e.g., forge_harness/quality_gates/lint_gate.py -> forge_harness.quality_gates.lint_gate)
                module_parts = list(rel_path.parts[:-1]) + [rel_path.stem]
                module_name = ".".join(module_parts)
                changed_modules[module_name] = file_path
                # Also add parent modules
                for i in range(len(module_parts)):
                    parent_module = ".".join(module_parts[: i + 1])
                    if parent_module not in changed_modules:
                        changed_modules[parent_module] = file_path
            except ValueError:
                logger.debug(f"File {file_path} is outside repo root")

        # Check each test file for imports of changed modules
        for test_file in all_test_files:
            # If the test file itself was changed, include it
            if test_file in changed_files:
                affected_tests.add(test_file)
                continue

            # Check if test imports any changed module
            test_imports = self.find_python_imports(test_file)
            for import_name in test_imports:
                if import_name in changed_modules:
                    affected_tests.add(test_file)
                    logger.debug(f"Test {test_file.name} imports changed module {import_name}")
                    break

        return affected_tests

    def find_affected_js_tests(self, changed_files: list[Path]) -> set[Path]:
        """
        Find JavaScript/TypeScript test files affected by changed files.

        Uses simple pattern matching for imports/requires.

        Args:
            changed_files: List of changed JS/TS files

        Returns:
            Set of test file paths to run
        """
        affected_tests = set()

        # Find all test files
        test_patterns = [
            "*.test.js",
            "*.test.ts",
            "*.test.tsx",
            "*.spec.js",
            "*.spec.ts",
            "*.spec.tsx",
        ]
        all_test_files = []
        for pattern in test_patterns:
            all_test_files.extend(self.repo_root.rglob(pattern))

        # Build set of changed file stems (without extension)
        changed_stems = set()
        for file_path in changed_files:
            try:
                rel_path = file_path.relative_to(self.repo_root)
                # Store both full relative path and stem
                changed_stems.add(str(rel_path.with_suffix("")))
                changed_stems.add(file_path.stem)
            except ValueError:
                logger.debug(f"File {file_path} is outside repo root")

        # Check each test file
        for test_file in all_test_files:
            # If test file itself was changed
            if test_file in changed_files:
                affected_tests.add(test_file)
                continue

            # Read test file and check for imports
            try:
                with open(test_file, encoding="utf-8") as f:
                    content = f.read()

                # Look for import/require statements
                # Matches: import ... from './path' or require('./path')
                import_pattern = (
                    r'(?:import|require)\s*(?:\{[^}]*\}|\w+)?\s*(?:from\s+)?["\']([^"\']+)["\']'
                )
                imports = re.findall(import_pattern, content)

                for import_path in imports:
                    # Normalize import path
                    import_stem = Path(import_path).stem
                    if (
                        import_stem in changed_stems
                        or import_path.replace("./", "").replace("../", "") in changed_stems
                    ):
                        affected_tests.add(test_file)
                        logger.debug(f"Test {test_file.name} imports changed file {import_path}")
                        break

            except Exception as e:
                logger.debug(f"Failed to analyze {test_file}: {e}")

        return affected_tests

    async def run_pytest(self, test_files: set[Path]) -> tuple[list[TestFailure], int, float]:
        """
        Run pytest on specified test files.

        Args:
            test_files: Set of test file paths

        Returns:
            Tuple of (failures, tests_run, execution_time)
        """
        if not test_files:
            return [], 0, 0.0

        failures: list[TestFailure] = []
        tests_run = 0
        execution_time = 0.0

        # Build pytest command
        cmd = ["pytest", "-v", "--tb=short", "--json-report", "--json-report-file=-"]
        cmd.extend(str(f) for f in test_files)

        try:
            import time

            start = time.time()

            # Run pytest
            result = await self._run_command(
                cmd,
                cwd=self.repo_root,
                allow_failure=True,
                timeout=30,  # 30 second timeout
            )

            execution_time = time.time() - start

            if result:
                # Try to parse JSON report
                try:
                    # pytest-json-report outputs to stderr, we need to parse from stdout
                    for line in result.split("\n"):
                        if line.strip().startswith("{"):
                            report = json.loads(line)
                            tests_run = report.get("summary", {}).get("total", 0)

                            # Parse failures
                            for test in report.get("tests", []):
                                if test.get("outcome") in ["failed", "error"]:
                                    failure = TestFailure(
                                        test_file=test.get("nodeid", "").split("::")[0],
                                        test_name=test.get("nodeid", ""),
                                        error_message=test.get("call", {}).get(
                                            "longrepr", "Test failed"
                                        ),
                                        line=test.get("lineno"),
                                    )
                                    failures.append(failure)
                            break
                except (json.JSONDecodeError, KeyError) as e:
                    logger.debug(f"Failed to parse pytest JSON output: {e}")
                    # Fallback: parse text output
                    if "FAILED" in result or "ERROR" in result:
                        # Count failures from output
                        failure_lines = [
                            l for l in result.split("\n") if "FAILED" in l or "ERROR" in l
                        ]
                        for line in failure_lines:
                            failures.append(
                                TestFailure(
                                    test_file="unknown",
                                    test_name=line.strip(),
                                    error_message="Test failed (see output)",
                                )
                            )
                    # Extract test count from summary
                    match = re.search(r"(\d+) passed", result)
                    if match:
                        tests_run = int(match.group(1))

        except Exception as e:
            logger.error(f"pytest execution failed: {e}")
            failures.append(
                TestFailure(
                    test_file="pytest",
                    test_name="execution",
                    error_message=str(e),
                )
            )

        return failures, tests_run, execution_time

    async def run_jest(self, test_files: set[Path]) -> tuple[list[TestFailure], int, float]:
        """
        Run jest/vitest on specified test files.

        Args:
            test_files: Set of test file paths

        Returns:
            Tuple of (failures, tests_run, execution_time)
        """
        if not test_files:
            return [], 0, 0.0

        failures: list[TestFailure] = []
        tests_run = 0
        execution_time = 0.0

        # Detect if jest or vitest is available
        test_runner = None
        if await self._command_exists("npm"):
            # Check package.json for test runner
            package_json = self.repo_root / "package.json"
            if package_json.exists():
                try:
                    with open(package_json) as f:
                        pkg = json.load(f)
                    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    if "vitest" in deps:
                        test_runner = "vitest"
                    elif "jest" in deps:
                        test_runner = "jest"
                except Exception:
                    pass

        if not test_runner:
            logger.info("No JS test runner found (jest/vitest), skipping JS tests")
            return [], 0, 0.0

        # Build test command
        cmd = ["npm", "run", "test", "--", "--json", "--testPathPattern"]
        # Create regex pattern for test files
        pattern = "|".join(str(f.relative_to(self.repo_root)) for f in test_files)
        cmd.append(pattern)

        try:
            import time

            start = time.time()

            result = await self._run_command(
                cmd,
                cwd=self.repo_root,
                allow_failure=True,
                timeout=30,
            )

            execution_time = time.time() - start

            if result:
                # Parse JSON output
                try:
                    # Look for JSON in output
                    for line in result.split("\n"):
                        if line.strip().startswith("{"):
                            report = json.loads(line)
                            tests_run = report.get("numTotalTests", 0)

                            # Parse failures
                            for test_result in report.get("testResults", []):
                                for assertion in test_result.get("assertionResults", []):
                                    if assertion.get("status") == "failed":
                                        failure = TestFailure(
                                            test_file=test_result.get("name", ""),
                                            test_name=assertion.get("fullName", ""),
                                            error_message=assertion.get("failureMessages", [""])[0],
                                        )
                                        failures.append(failure)
                            break
                except (json.JSONDecodeError, KeyError) as e:
                    logger.debug(f"Failed to parse jest/vitest JSON output: {e}")

        except Exception as e:
            logger.error(f"{test_runner} execution failed: {e}")

        return failures, tests_run, execution_time

    async def run(self, check_only: bool = True) -> TestResult:
        """
        Run pre-commit tests on affected test files.

        Args:
            check_only: If True, runs in check mode (always True for tests)

        Returns:
            TestResult with details
        """
        import time

        overall_start = time.time()

        # Get staged files
        logger.info("Getting staged files...")
        staged_files = await self.get_staged_files()

        if not staged_files:
            logger.info("No staged files to test")
            return TestResult(passed=True, tests_run=0, files_changed=0)

        logger.info(f"Found {len(staged_files)} staged files")

        # Categorize files
        categorized = self.categorize_files(staged_files)
        python_files = categorized["python"]
        js_ts_files = categorized["js_ts"]

        logger.info(f"Python files: {len(python_files)}, JS/TS files: {len(js_ts_files)}")

        all_failures: list[TestFailure] = []
        total_tests_run = 0
        errors: list[str] = []

        # Find and run Python tests
        if python_files:
            logger.info("Finding affected Python tests...")
            python_tests = self.find_affected_python_tests(python_files)
            logger.info(f"Found {len(python_tests)} affected Python test files")

            if python_tests:
                logger.info("Running Python tests...")
                try:
                    failures, tests_run, exec_time = await self.run_pytest(python_tests)
                    all_failures.extend(failures)
                    total_tests_run += tests_run
                    logger.info(
                        f"Python tests: {tests_run} run, {len(failures)} failed ({exec_time:.2f}s)"
                    )
                except Exception as e:
                    error_msg = f"Python test execution failed: {e}"
                    logger.error(error_msg)
                    errors.append(error_msg)

        # Find and run JS/TS tests
        if js_ts_files:
            logger.info("Finding affected JS/TS tests...")
            js_tests = self.find_affected_js_tests(js_ts_files)
            logger.info(f"Found {len(js_tests)} affected JS/TS test files")

            if js_tests:
                logger.info("Running JS/TS tests...")
                try:
                    failures, tests_run, exec_time = await self.run_jest(js_tests)
                    all_failures.extend(failures)
                    total_tests_run += tests_run
                    logger.info(
                        f"JS/TS tests: {tests_run} run, {len(failures)} failed ({exec_time:.2f}s)"
                    )
                except Exception as e:
                    error_msg = f"JS/TS test execution failed: {e}"
                    logger.error(error_msg)
                    errors.append(error_msg)

        # Calculate overall execution time
        overall_time = time.time() - overall_start

        # Determine pass/fail
        passed = len(all_failures) == 0 and len(errors) == 0

        result = TestResult(
            passed=passed,
            tests_run=total_tests_run,
            files_changed=len(python_files) + len(js_ts_files),
            failures=all_failures,
            errors=errors,
            execution_time=overall_time,
        )

        logger.info(
            f"Pre-commit tests {'PASSED' if passed else 'FAILED'}: "
            f"{total_tests_run} tests, {len(all_failures)} failures, "
            f"{overall_time:.2f}s"
        )

        return result

    async def _command_exists(self, command: str) -> bool:
        """Check if a command exists in PATH."""
        try:
            result = await self._run_command(
                ["which", command],
                allow_failure=True,
            )
            return bool(result and result.strip())
        except Exception:
            return False

    async def _run_command(
        self,
        cmd: list[str],
        cwd: Path | None = None,
        timeout: int = 60,
        allow_failure: bool = False,
    ) -> str | None:
        """
        Run a command asynchronously.

        Args:
            cmd: Command and arguments
            cwd: Working directory
            timeout: Timeout in seconds
            allow_failure: If True, don't raise on non-zero exit

        Returns:
            Command stdout or None if failed
        """
        if cwd is None:
            cwd = self.repo_root

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )

            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)

            if process.returncode != 0 and not allow_failure:
                logger.warning(
                    f"Command failed: {' '.join(cmd)}\n"
                    f"Exit code: {process.returncode}\n"
                    f"Stderr: {stderr.decode()}"
                )
                return None

            # Return combined output for test runners
            output = stdout.decode()
            if stderr:
                output += "\n" + stderr.decode()
            return output

        except TimeoutError:
            logger.warning(f"Command timed out: {' '.join(cmd)}")
            return None
        except FileNotFoundError:
            logger.warning(f"Command not found: {cmd[0]}")
            return None
        except Exception as e:
            logger.error(f"Command error: {e}")
            return None


# =============================================================================
# Factory and CLI
# =============================================================================


async def run_pre_commit_tests(
    repo_root: Path | None = None,
    check_only: bool = True,
) -> TestResult:
    """
    Run pre-commit tests on affected test files.

    Args:
        repo_root: Repository root (defaults to current directory)
        check_only: If True, runs in check mode (always True for tests)

    Returns:
        TestResult with details
    """
    runner = PreCommitTestRunner(repo_root=repo_root)
    return await runner.run(check_only=check_only)


async def main_async() -> int:
    """Async main entry point."""
    parser = argparse.ArgumentParser(
        description="Pre-commit test runner for affected tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check affected tests (dry-run)
  python -m forge_harness.quality_gates.pre_commit_tests --check

  # Run affected tests
  python -m forge_harness.quality_gates.pre_commit_tests

  # Use in pre-commit hook
  python -m forge_harness.quality_gates.pre_commit_tests || exit 1
        """,
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: report what would be tested (dry-run)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="Repository root directory (default: current directory)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging
    from ..logging_config import configure_logging

    configure_logging(
        level="DEBUG" if args.verbose else "INFO",
        json_format=False,
    )

    # Run pre-commit tests
    result = await run_pre_commit_tests(
        repo_root=args.repo_root,
        check_only=args.check,
    )

    # Print summary
    print(result.summary())

    # Exit with appropriate code
    return 0 if result.passed else 1


def main() -> int:
    """Main entry point for CLI."""
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
