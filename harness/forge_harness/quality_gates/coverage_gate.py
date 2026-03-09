"""Coverage threshold quality gate for FORGE Harness.

Measures test coverage and enforces minimum thresholds to prevent coverage regression.
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class FileCoverage:
    """Coverage data for a single file."""

    file_path: str
    statements: int
    missed: int
    coverage_pct: float


@dataclass
class SimpleCoverageResult:
    """Result of simple coverage analysis."""

    passed: bool
    overall_coverage: float
    threshold: float
    file_threshold: float
    files: list[FileCoverage] = field(default_factory=list)
    failing_files: list[FileCoverage] = field(default_factory=list)
    error: str | None = None
    raw_output: str = ""


@dataclass
class CoverageConfig:
    """Configuration for coverage checking."""

    threshold: float = 80.0  # Overall minimum coverage
    file_threshold: float = 70.0  # Per-file minimum coverage
    source_dir: str = "forge_harness"  # Source directory to measure
    test_command: str = "pytest"  # Test command to run
    include_patterns: list[str] = field(default_factory=lambda: ["*.py"])
    exclude_patterns: list[str] = field(default_factory=lambda: ["*test*", "*__pycache__*"])


class SimpleCoverageGate:
    """Simple coverage gate that runs pytest with coverage and checks thresholds."""

    def __init__(self, config: CoverageConfig | None = None, cwd: Path | None = None):
        self.config = config or CoverageConfig()
        self.cwd = cwd or Path.cwd()

    def run(self, test_paths: list[str] | None = None) -> SimpleCoverageResult:
        """Run coverage analysis and check thresholds."""
        try:
            # Build pytest command with coverage
            cmd = [
                "uv",
                "run",
                self.config.test_command,
                f"--cov={self.config.source_dir}",
                "--cov-report=json",
                "--cov-report=term",
                "-q",
            ]

            if test_paths:
                cmd.extend(test_paths)
            else:
                cmd.append("tests/")

            logger.info(f"Running coverage: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            raw_output = result.stdout + result.stderr

            # Parse coverage JSON report
            coverage_json = self.cwd / "coverage.json"
            if coverage_json.exists():
                return self._parse_coverage_json(coverage_json, raw_output)

            # Fallback to parsing terminal output
            return self._parse_terminal_output(raw_output)

        except subprocess.TimeoutExpired:
            return SimpleCoverageResult(
                passed=False,
                overall_coverage=0.0,
                threshold=self.config.threshold,
                file_threshold=self.config.file_threshold,
                error="Coverage check timed out after 5 minutes",
            )
        except Exception as e:
            return SimpleCoverageResult(
                passed=False,
                overall_coverage=0.0,
                threshold=self.config.threshold,
                file_threshold=self.config.file_threshold,
                error=str(e),
            )

    def _parse_coverage_json(self, json_path: Path, raw_output: str) -> SimpleCoverageResult:
        """Parse coverage.json report."""
        try:
            data = json.loads(json_path.read_text())

            files = []
            failing_files = []

            for file_path, file_data in data.get("files", {}).items():
                summary = file_data.get("summary", {})
                coverage_pct = summary.get("percent_covered", 0.0)

                file_cov = FileCoverage(
                    file_path=file_path,
                    statements=summary.get("num_statements", 0),
                    missed=summary.get("missing_lines", 0),
                    coverage_pct=coverage_pct,
                )
                files.append(file_cov)

                if coverage_pct < self.config.file_threshold:
                    failing_files.append(file_cov)

            # Get overall coverage
            totals = data.get("totals", {})
            overall = totals.get("percent_covered", 0.0)

            passed = overall >= self.config.threshold and len(failing_files) == 0

            return SimpleCoverageResult(
                passed=passed,
                overall_coverage=overall,
                threshold=self.config.threshold,
                file_threshold=self.config.file_threshold,
                files=files,
                failing_files=failing_files,
                raw_output=raw_output,
            )

        except Exception as e:
            logger.warning(f"Failed to parse coverage JSON: {e}")
            return self._parse_terminal_output(raw_output)

    def _parse_terminal_output(self, output: str) -> SimpleCoverageResult:
        """Parse coverage from terminal output (fallback)."""
        files = []
        failing_files = []
        overall = 0.0

        # Parse TOTAL line for overall coverage
        total_match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", output)
        if total_match:
            overall = float(total_match.group(1))

        # Parse individual file lines
        file_pattern = re.compile(r"^([\w/\.]+\.py)\s+(\d+)\s+(\d+)\s+(\d+)%", re.MULTILINE)
        for match in file_pattern.finditer(output):
            file_path = match.group(1)
            statements = int(match.group(2))
            missed = int(match.group(3))
            pct = float(match.group(4))

            file_cov = FileCoverage(
                file_path=file_path, statements=statements, missed=missed, coverage_pct=pct
            )
            files.append(file_cov)

            if pct < self.config.file_threshold:
                failing_files.append(file_cov)

        passed = overall >= self.config.threshold and len(failing_files) == 0

        return SimpleCoverageResult(
            passed=passed,
            overall_coverage=overall,
            threshold=self.config.threshold,
            file_threshold=self.config.file_threshold,
            files=files,
            failing_files=failing_files,
            raw_output=output,
        )

    def check_diff_coverage(self, base_branch: str = "main") -> SimpleCoverageResult:
        """Check coverage only for changed files."""
        try:
            # Get changed files
            result = subprocess.run(
                ["git", "diff", "--name-only", base_branch, "--", "*.py"],
                cwd=self.cwd,
                capture_output=True,
                text=True,
            )

            changed_files = [
                f
                for f in result.stdout.strip().split("\n")
                if f and f.endswith(".py") and not f.startswith("test")
            ]

            if not changed_files:
                return SimpleCoverageResult(
                    passed=True,
                    overall_coverage=100.0,
                    threshold=self.config.threshold,
                    file_threshold=self.config.file_threshold,
                    error="No Python files changed",
                )

            # Run coverage on related tests
            return self.run()

        except Exception as e:
            return SimpleCoverageResult(
                passed=False,
                overall_coverage=0.0,
                threshold=self.config.threshold,
                file_threshold=self.config.file_threshold,
                error=str(e),
            )


def create_coverage_gate(
    threshold: float = 80.0, file_threshold: float = 70.0, cwd: Path | None = None
) -> SimpleCoverageGate:
    """Factory function to create a SimpleCoverageGate."""
    config = CoverageConfig(threshold=threshold, file_threshold=file_threshold)
    return SimpleCoverageGate(config=config, cwd=cwd)


"""
Coverage Gate - Pre-commit Coverage Threshold Quality Gate
===========================================================

Enforces test coverage thresholds before commit:
- Overall coverage must be >= 80%
- Per-file coverage must be >= 70% for modified files
- Prevents coverage regression

Runs pytest with --cov flag on affected modules and compares against baseline.

Usage:
    from forge_harness.quality_gates import run_coverage_gate, CoverageGate

    # Run in check mode
    result = await run_coverage_gate(check_only=True)
    if not result.passed:
        print(result.summary())
        sys.exit(1)

CLI Usage:
    python -m forge_harness.quality_gates.coverage_gate --check
    python -m forge_harness.quality_gates.coverage_gate
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Models
# =============================================================================


@dataclass
class CoverageStats:
    """Coverage statistics for a module or file."""

    name: str
    statements: int
    covered: int
    missing: int
    excluded: int
    coverage: float  # Percentage

    @property
    def coverage_percent(self) -> float:
        """Get coverage as a percentage."""
        return self.coverage

    def __str__(self) -> str:
        """Format stats for display."""
        return f"{self.name}: {self.coverage:.1f}% ({self.covered}/{self.statements})"


@dataclass
class CoverageIssue:
    """A coverage issue for a specific file."""

    file: str
    current_coverage: float
    required_coverage: float
    statements: int
    covered: int
    missing: int

    @property
    def gap(self) -> float:
        """Calculate coverage gap."""
        return self.required_coverage - self.current_coverage

    def __str__(self) -> str:
        """Format issue for display."""
        return (
            f"{self.file}: {self.current_coverage:.1f}% "
            f"(requires {self.required_coverage:.1f}%, "
            f"gap: {self.gap:.1f}%)"
        )


@dataclass
class CoverageResult:
    """Result of running coverage gate."""

    passed: bool
    overall_coverage: float
    threshold: float
    file_threshold: float
    files_checked: int
    issues: list[CoverageIssue] = field(default_factory=list)
    stats: list[CoverageStats] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    execution_time: float = 0.0

    @property
    def issue_count(self) -> int:
        """Count of coverage issues."""
        return len(self.issues)

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = []
        lines.append("=" * 70)
        lines.append("Coverage Gate Results")
        lines.append("=" * 70)
        lines.append(f"Status: {'PASSED' if self.passed else 'FAILED'}")
        lines.append(
            f"Overall Coverage: {self.overall_coverage:.1f}% (threshold: {self.threshold:.1f}%)"
        )
        lines.append(f"Files Checked: {self.files_checked}")
        lines.append(f"Execution Time: {self.execution_time:.2f}s")

        if self.issues:
            lines.append(f"\nCoverage Issues: {self.issue_count}")
            lines.append("")
            lines.append("Files below threshold:")
            for issue in self.issues:
                lines.append(f"  {issue}")

        if self.stats:
            lines.append("\nCoverage by File:")
            for stat in sorted(self.stats, key=lambda s: s.coverage):
                status = "✓" if stat.coverage >= self.file_threshold else "✗"
                lines.append(f"  {status} {stat}")

        if self.errors:
            lines.append("")
            lines.append("Errors during execution:")
            for error in self.errors:
                lines.append(f"  {error}")

        lines.append("=" * 70)
        return "\n".join(lines)


# =============================================================================
# Coverage Gate Implementation
# =============================================================================


class CoverageGate:
    """
    Coverage threshold quality gate for staged files.

    Runs pytest with coverage on affected modules and enforces thresholds:
    - Overall coverage >= 80%
    - Per-file coverage >= 70% for modified files
    """

    PYTHON_EXTENSIONS = {".py"}
    OVERALL_THRESHOLD = 80.0
    FILE_THRESHOLD = 70.0

    def __init__(
        self,
        repo_root: Path | None = None,
        overall_threshold: float | None = None,
        file_threshold: float | None = None,
    ):
        """
        Initialize coverage gate.

        Args:
            repo_root: Repository root directory (defaults to current directory)
            overall_threshold: Overall coverage threshold (default: 80%)
            file_threshold: Per-file coverage threshold (default: 70%)
        """
        self.repo_root = repo_root or Path.cwd()
        self.overall_threshold = overall_threshold or self.OVERALL_THRESHOLD
        self.file_threshold = file_threshold or self.FILE_THRESHOLD

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

    def filter_python_files(self, files: list[Path]) -> list[Path]:
        """
        Filter to only Python files.

        Args:
            files: List of file paths

        Returns:
            List of Python file paths
        """
        return [f for f in files if f.suffix in self.PYTHON_EXTENSIONS]

    def get_module_names(self, files: list[Path]) -> list[str]:
        """
        Convert file paths to Python module names.

        Args:
            files: List of Python file paths

        Returns:
            List of module names (e.g., 'forge_harness.quality_gates.coverage_gate')
        """
        modules = []
        for file_path in files:
            try:
                rel_path = file_path.relative_to(self.repo_root)
                # Convert path to module name
                # e.g., forge_harness/quality_gates/coverage_gate.py
                # -> forge_harness.quality_gates.coverage_gate
                module_parts = list(rel_path.parts[:-1]) + [rel_path.stem]
                module_name = ".".join(module_parts)
                modules.append(module_name)
            except ValueError:
                logger.debug(f"File {file_path} is outside repo root")

        return modules

    async def run_coverage(self, modules: list[str]) -> tuple[dict[str, CoverageStats], float]:
        """
        Run pytest with coverage on specified modules.

        Args:
            modules: List of module names to check coverage for

        Returns:
            Tuple of (file_stats_dict, overall_coverage_percentage)
        """
        if not modules:
            return {}, 0.0

        file_stats: dict[str, CoverageStats] = {}
        overall_coverage = 0.0

        # Build pytest command with coverage
        cmd = [
            "pytest",
            "--cov=" + ",".join(modules),
            "--cov-report=json",
            "--cov-report=term-missing",
            "-v",
            "--tb=short",
        ]

        try:
            import time

            start = time.time()

            # Run pytest with coverage
            await self._run_command(
                cmd,
                cwd=self.repo_root,
                allow_failure=True,  # Tests might fail, but we still want coverage
                timeout=120,  # 2 minute timeout for coverage
            )

            execution_time = time.time() - start
            logger.info(f"Coverage run completed in {execution_time:.2f}s")

            # Look for coverage.json file
            coverage_json_path = self.repo_root / "coverage.json"
            if coverage_json_path.exists():
                try:
                    with open(coverage_json_path) as f:
                        coverage_data = json.load(f)

                    # Extract overall coverage
                    totals = coverage_data.get("totals", {})
                    overall_coverage = totals.get("percent_covered", 0.0)

                    # Extract per-file coverage
                    files = coverage_data.get("files", {})
                    for file_path, file_data in files.items():
                        # Convert absolute path to relative
                        try:
                            rel_path = Path(file_path).relative_to(self.repo_root)
                            file_name = str(rel_path)
                        except ValueError:
                            file_name = file_path

                        summary = file_data.get("summary", {})
                        file_stats[file_name] = CoverageStats(
                            name=file_name,
                            statements=summary.get("num_statements", 0),
                            covered=summary.get("covered_lines", 0),
                            missing=summary.get("missing_lines", 0),
                            excluded=summary.get("excluded_lines", 0),
                            coverage=summary.get("percent_covered", 0.0),
                        )

                    logger.info(f"Overall coverage: {overall_coverage:.1f}%")

                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse coverage.json: {e}")
                except Exception as e:
                    logger.error(f"Error reading coverage data: {e}")

        except Exception as e:
            logger.error(f"Coverage execution failed: {e}")

        return file_stats, overall_coverage

    async def check_coverage(
        self,
        file_stats: dict[str, CoverageStats],
        overall_coverage: float,
        changed_files: list[Path],
    ) -> list[CoverageIssue]:
        """
        Check coverage against thresholds.

        Args:
            file_stats: Dictionary of file coverage stats
            overall_coverage: Overall coverage percentage
            changed_files: List of changed Python files

        Returns:
            List of coverage issues
        """
        issues: list[CoverageIssue] = []

        # Check overall coverage
        if overall_coverage < self.overall_threshold:
            # Create a synthetic issue for overall coverage
            total_statements = sum(s.statements for s in file_stats.values())
            total_covered = sum(s.covered for s in file_stats.values())
            total_missing = sum(s.missing for s in file_stats.values())

            issues.append(
                CoverageIssue(
                    file="<overall>",
                    current_coverage=overall_coverage,
                    required_coverage=self.overall_threshold,
                    statements=total_statements,
                    covered=total_covered,
                    missing=total_missing,
                )
            )

        # Check per-file coverage for changed files
        for file_path in changed_files:
            try:
                rel_path = file_path.relative_to(self.repo_root)
                file_name = str(rel_path)

                # Find coverage stats for this file
                stats = file_stats.get(file_name)
                if stats and stats.coverage < self.file_threshold:
                    issues.append(
                        CoverageIssue(
                            file=file_name,
                            current_coverage=stats.coverage,
                            required_coverage=self.file_threshold,
                            statements=stats.statements,
                            covered=stats.covered,
                            missing=stats.missing,
                        )
                    )
            except ValueError:
                logger.debug(f"File {file_path} is outside repo root")

        return issues

    async def run(self, check_only: bool = True) -> CoverageResult:
        """
        Run coverage gate on staged files.

        Args:
            check_only: If True, runs in check mode (always True for coverage)

        Returns:
            CoverageResult with coverage details
        """
        import time

        overall_start = time.time()

        # Get staged files
        logger.info("Getting staged files...")
        staged_files = await self.get_staged_files()

        if not staged_files:
            logger.info("No staged files to check")
            return CoverageResult(
                passed=True,
                overall_coverage=0.0,
                threshold=self.overall_threshold,
                file_threshold=self.file_threshold,
                files_checked=0,
            )

        logger.info(f"Found {len(staged_files)} staged files")

        # Filter to Python files
        python_files = self.filter_python_files(staged_files)

        if not python_files:
            logger.info("No Python files to check coverage for")
            return CoverageResult(
                passed=True,
                overall_coverage=0.0,
                threshold=self.overall_threshold,
                file_threshold=self.file_threshold,
                files_checked=0,
            )

        logger.info(f"Found {len(python_files)} Python files")

        # Get module names
        modules = self.get_module_names(python_files)
        logger.info(f"Checking coverage for {len(modules)} modules")

        errors: list[str] = []

        try:
            # Run coverage
            file_stats, overall_coverage = await self.run_coverage(modules)

            # Check against thresholds
            issues = await self.check_coverage(file_stats, overall_coverage, python_files)

            # Calculate overall execution time
            overall_time = time.time() - overall_start

            # Determine pass/fail
            passed = len(issues) == 0

            result = CoverageResult(
                passed=passed,
                overall_coverage=overall_coverage,
                threshold=self.overall_threshold,
                file_threshold=self.file_threshold,
                files_checked=len(python_files),
                issues=issues,
                stats=list(file_stats.values()),
                errors=errors,
                execution_time=overall_time,
            )

            logger.info(
                f"Coverage gate {'PASSED' if passed else 'FAILED'}: "
                f"{overall_coverage:.1f}% overall, "
                f"{len(issues)} issues"
            )

            return result

        except Exception as e:
            error_msg = f"Coverage check failed: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

            overall_time = time.time() - overall_start

            return CoverageResult(
                passed=False,
                overall_coverage=0.0,
                threshold=self.overall_threshold,
                file_threshold=self.file_threshold,
                files_checked=len(python_files),
                issues=[],
                stats=[],
                errors=errors,
                execution_time=overall_time,
            )

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

            # Return combined output
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


async def run_coverage_gate(
    repo_root: Path | None = None,
    check_only: bool = True,
    overall_threshold: float | None = None,
    file_threshold: float | None = None,
) -> CoverageResult:
    """
    Run coverage gate on staged files.

    Args:
        repo_root: Repository root (defaults to current directory)
        check_only: If True, runs in check mode (always True for coverage)
        overall_threshold: Overall coverage threshold (default: 80%)
        file_threshold: Per-file coverage threshold (default: 70%)

    Returns:
        CoverageResult with coverage details
    """
    gate = CoverageGate(
        repo_root=repo_root,
        overall_threshold=overall_threshold,
        file_threshold=file_threshold,
    )
    return await gate.run(check_only=check_only)


async def main_async() -> int:
    """Async main entry point."""
    parser = argparse.ArgumentParser(
        description="Coverage gate for staged files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check coverage for staged files
  python -m forge_harness.quality_gates.coverage_gate --check

  # Check with custom thresholds
  python -m forge_harness.quality_gates.coverage_gate --overall 85 --file 75

  # Use in pre-commit hook
  python -m forge_harness.quality_gates.coverage_gate || exit 1
        """,
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: report coverage (default)",
    )
    parser.add_argument(
        "--overall",
        type=float,
        help="Overall coverage threshold (default: 80%%)",
    )
    parser.add_argument(
        "--file",
        type=float,
        help="Per-file coverage threshold (default: 70%%)",
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

    # Run coverage gate
    result = await run_coverage_gate(
        repo_root=args.repo_root,
        check_only=True,  # Always True for coverage
        overall_threshold=args.overall,
        file_threshold=args.file,
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
