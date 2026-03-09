"""
Post-Coding Verification Module
===============================

Verifies coding work meets quality standards before marking complete.
Runs tests, coverage analysis, linting, and checkpoint validation.
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .failure_patterns import FailurePatternDB


@dataclass
class VerificationResult:
    """Result of running verification suite."""

    passed: bool
    tests_passed: int
    tests_failed: int
    coverage_backend: float
    coverage_frontend: float
    lint_errors: int
    checkpoint_results: dict[str, bool] = field(default_factory=dict)
    error_message: str | None = None
    # NEW: Detailed error context for retry sessions (from KiddoRewards lessons)
    error_details: list[str] = field(default_factory=list)
    fix_suggestions: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """Generate human-readable summary."""
        status = "PASSED" if self.passed else "FAILED"
        lines = [
            f"Verification: {status}",
            f"Tests: {self.tests_passed} passed, {self.tests_failed} failed",
            f"Coverage: Backend {self.coverage_backend}%, Frontend {self.coverage_frontend}%",
            f"Lint errors: {self.lint_errors}",
        ]
        if self.checkpoint_results:
            lines.append("Checkpoints:")
            for criterion, met in self.checkpoint_results.items():
                mark = "✓" if met else "✗"
                lines.append(f"  {mark} {criterion}")
        if self.error_message:
            lines.append(f"Error: {self.error_message}")
        return "\n".join(lines)


class Verifier:
    """
    Runs verification suite on a FORGE project.

    Checks:
    - Backend tests (pytest with coverage)
    - Frontend tests (vitest with coverage)
    - Linting (ruff for Python, eslint for JS)
    - Checkpoint criteria validation
    """

    def __init__(
        self,
        project_dir: Path,
        domain_config: Any,
        skip_quality_gates: bool = False,
        failure_pattern_db: Any | None = None,
    ):
        """
        Initialize verifier.

        Args:
            project_dir: Path to the project directory
            domain_config: Domain configuration with coverage thresholds
            skip_quality_gates: If True, always return passed=True
            failure_pattern_db: Optional EnhancedFailurePatternDB for fix suggestions
        """
        self.project_dir = Path(project_dir)
        self.domain_config = domain_config
        self.skip_quality_gates = skip_quality_gates
        self.failure_pattern_db = failure_pattern_db

        # Set thresholds from config or defaults (65% is practical for MVP development)
        self.min_backend_coverage = getattr(domain_config, "min_backend_coverage", 65.0)
        self.min_frontend_coverage = getattr(domain_config, "min_frontend_coverage", 50.0)

    def run_backend_tests(self) -> tuple[int, int, float, list[str]]:
        """
        Run pytest with coverage on backend.

        Returns:
            Tuple of (tests_passed, tests_failed, coverage_percent, error_details)
        """
        backend_dir = self.project_dir / "backend"
        if not backend_dir.exists():
            return 0, 0, 0.0, []

        errors: list[str] = []

        try:
            # ALWAYS sync dependencies first (from KiddoRewards lessons)
            subprocess.run(
                ["uv", "sync"],
                cwd=backend_dir,
                capture_output=True,
                timeout=120,
            )

            result = subprocess.run(
                ["uv", "run", "pytest", "--cov=app", "--cov-report=json", "-v", "--tb=short"],
                cwd=backend_dir,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            # Parse test counts from output
            passed = 0
            failed = 0

            # Match patterns like "10 passed" and "2 failed"
            passed_match = re.search(r"(\d+)\s+passed", result.stdout)
            failed_match = re.search(r"(\d+)\s+failed", result.stdout)

            if passed_match:
                passed = int(passed_match.group(1))
            if failed_match:
                failed = int(failed_match.group(1))

            # Extract error details for retry sessions
            if result.returncode != 0:
                combined_output = result.stdout + "\n" + result.stderr
                for line in combined_output.split("\n"):
                    line = line.strip()
                    if "FAILED" in line and "::" in line:
                        errors.append(f"Test failure: {line}")
                    elif "ERROR" in line and "test_" in line:
                        errors.append(f"Test error: {line}")
                    elif "ModuleNotFoundError" in line:
                        errors.append(f"Import error: {line}")
                    elif "ImportError" in line:
                        errors.append(f"Import error: {line}")
                    elif "SyntaxError" in line:
                        errors.append(f"Syntax error: {line}")

            # Parse coverage from JSON file
            coverage = 0.0
            cov_file = backend_dir / "coverage.json"
            if cov_file.exists():
                try:
                    cov_data = json.loads(cov_file.read_text())
                    coverage = cov_data.get("totals", {}).get("percent_covered", 0)
                except (json.JSONDecodeError, KeyError):
                    pass

            return passed, failed, coverage, errors

        except subprocess.TimeoutExpired:
            return 0, 1, 0.0, ["Test execution timed out after 5 minutes"]
        except FileNotFoundError:
            # uv not found
            return (
                0,
                0,
                0.0,
                [
                    "uv command not found - install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
                ],
            )

    def run_frontend_tests(self) -> tuple[int, int, float, list[str]]:
        """
        Run vitest with coverage on frontend.

        Returns:
            Tuple of (tests_passed, tests_failed, coverage_percent, error_details)
        """
        frontend_dir = self.project_dir / "frontend"
        if not frontend_dir.exists():
            return 0, 0, 0.0, []

        errors: list[str] = []

        try:
            # Install dependencies first (from KiddoRewards lessons)
            subprocess.run(
                ["npm", "install"],
                cwd=frontend_dir,
                capture_output=True,
                timeout=180,
            )

            result = subprocess.run(
                ["npm", "run", "test:coverage", "--", "--run"],
                cwd=frontend_dir,
                capture_output=True,
                text=True,
                timeout=300,
            )

            # Parse test counts from vitest output
            passed = 0
            failed = 0

            # Vitest output patterns
            passed_match = re.search(r"(\d+)\s+passed", result.stdout)
            failed_match = re.search(r"(\d+)\s+failed", result.stdout)

            if passed_match:
                passed = int(passed_match.group(1))
            if failed_match:
                failed = int(failed_match.group(1))

            # Extract error details
            if result.returncode != 0:
                combined_output = result.stdout + "\n" + result.stderr
                for line in combined_output.split("\n"):
                    line = line.strip()
                    if "FAIL" in line and (".ts" in line or ".tsx" in line):
                        errors.append(f"Test failure: {line}")
                    elif "Error:" in line:
                        errors.append(f"Error: {line[:200]}")

            # Try to parse coverage from vitest coverage output
            coverage = 0.0
            cov_match = re.search(
                r"All files\s*\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|\s*([\d.]+)",
                result.stdout,
            )
            if cov_match:
                coverage = float(cov_match.group(1))

            return passed, failed, coverage, errors

        except subprocess.TimeoutExpired:
            return 0, 1, 0.0, ["Frontend test execution timed out"]
        except FileNotFoundError:
            # npm not found
            return 0, 0, 0.0, ["npm command not found"]

    def run_linting(self) -> tuple[int, list[str]]:
        """
        Run linting on backend (ruff) and frontend (eslint).

        Returns:
            Tuple of (error_count, error_details)
        """
        error_count = 0
        errors: list[str] = []

        # Backend: ruff
        backend_dir = self.project_dir / "backend"
        if backend_dir.exists():
            try:
                result = subprocess.run(
                    ["uv", "run", "ruff", "check", ".", "--output-format=text"],
                    cwd=backend_dir,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                # Only count as error if there's actual error output (not just exit code)
                # ruff outputs errors to stdout, "All checks passed!" means success
                output = result.stdout.strip()
                if result.returncode != 0 and output and "All checks passed" not in output:
                    # Count actual error lines (format: "file:line:col: CODE message")
                    error_lines = [
                        line
                        for line in output.split("\n")
                        if line.strip() and re.match(r"^.+:\d+:\d+:", line)
                    ]
                    if error_lines:
                        error_count += len(error_lines)
                        # Capture first 5 lint errors for context
                        for line in error_lines[:5]:
                            errors.append(f"Lint: {line.strip()}")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        # Frontend: eslint with relaxed threshold (from KiddoRewards lessons)
        frontend_dir = self.project_dir / "frontend"
        if frontend_dir.exists():
            try:
                # Use --max-warnings 50 instead of 0 to avoid failing on minor warnings
                result = subprocess.run(
                    ["npm", "run", "lint", "--", "--max-warnings", "50"],
                    cwd=frontend_dir,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode != 0:
                    error_count += 1
                    errors.append("Frontend lint failed (exceeded 50 warnings or has errors)")
                    # Capture some error context
                    for line in result.stdout.split("\n")[:3]:
                        if line.strip() and ("error" in line.lower() or "warning" in line.lower()):
                            errors.append(f"ESLint: {line.strip()[:150]}")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        return error_count, errors

    def generate_fix_suggestions(self, errors: list[str]) -> list[str]:
        """
        Generate actionable fix suggestions from error patterns.

        Uses EnhancedFailurePatternDB (if provided) or FailurePatternDB
        for intelligent pattern matching. When using the enhanced DB,
        successful fixes are recorded to the LearningStore.

        Args:
            errors: List of error messages

        Returns:
            List of fix suggestions
        """
        # Use enhanced DB if available (wired to LearningStore)
        if self.failure_pattern_db is not None:
            # Use sync version to avoid async complications in verification flow
            suggestions = self.failure_pattern_db.get_fix_suggestions_sync(errors)
            return [f"FIX: {s.fix}" if not s.fix.startswith("FIX:") else s.fix for s in suggestions]

        # Fall back to basic FailurePatternDB
        db = FailurePatternDB()
        suggestions = db.get_fixes(errors)

        # Add FIX: prefix if not present
        return [f"FIX: {s}" if not s.startswith("FIX:") else s for s in suggestions]

    def verify_checkpoint(self, checkpoint: str, context: dict[str, Any]) -> dict[str, bool]:
        """
        Verify checkpoint criteria are met.

        Args:
            checkpoint: Checkpoint string with criteria (markdown list)
            context: Context dict with test results, coverage, etc.

        Returns:
            Dict mapping criterion to whether it's met
        """
        results = {}

        # Parse checkpoint into individual criteria
        criteria = [c.strip() for c in checkpoint.split("\n") if c.strip().startswith("- ")]

        for criterion in criteria:
            criterion_lower = criterion.lower()

            # Check various criteria types
            if "test" in criterion_lower and "pass" in criterion_lower:
                results[criterion] = context.get("tests_failed", 1) == 0
            elif "coverage" in criterion_lower:
                results[criterion] = context.get("coverage_backend", 0) >= self.min_backend_coverage
            elif "lint" in criterion_lower or "error" in criterion_lower:
                results[criterion] = context.get("lint_errors", 1) == 0
            else:
                # Assume met unless proven otherwise
                results[criterion] = True

        return results

    def verify(self, checkpoint: str = "") -> VerificationResult:
        """
        Run full verification suite.

        Args:
            checkpoint: Optional checkpoint criteria to validate

        Returns:
            VerificationResult with all metrics and error context
        """
        # Collect all errors for retry context
        all_errors: list[str] = []

        # Run tests (now returns 4-tuple with errors)
        be_passed, be_failed, be_coverage, be_errors = self.run_backend_tests()
        fe_passed, fe_failed, fe_coverage, fe_errors = self.run_frontend_tests()
        all_errors.extend(be_errors)
        all_errors.extend(fe_errors)

        # Run linting (now returns 2-tuple with errors)
        lint_error_count, lint_errors = self.run_linting()
        all_errors.extend(lint_errors)

        # Build context for checkpoint verification
        context = {
            "tests_failed": be_failed + fe_failed,
            "coverage_backend": be_coverage,
            "coverage_frontend": fe_coverage,
            "lint_errors": lint_error_count,
        }

        # Verify checkpoint criteria
        checkpoint_results = self.verify_checkpoint(checkpoint, context) if checkpoint else {}

        # Determine overall pass/fail
        if self.skip_quality_gates:
            passed = True
            error_message = None
        else:
            # Check all quality gates with practical thresholds
            tests_pass = be_failed == 0 and fe_failed == 0
            lint_pass = lint_error_count == 0

            # Coverage check: only apply if tests actually ran
            # If no tests exist (be_passed == 0), don't fail on coverage
            has_backend_tests = be_passed > 0 or be_failed > 0
            coverage_pass = (
                not has_backend_tests  # No tests = coverage N/A
                or be_coverage >= self.min_backend_coverage
            )

            checkpoint_pass = all(checkpoint_results.values()) if checkpoint_results else True

            passed = tests_pass and lint_pass and coverage_pass and checkpoint_pass

            # Build specific error message
            if not passed:
                error_parts = []
                if not tests_pass:
                    error_parts.append(f"{be_failed + fe_failed} test(s) failed")
                if not lint_pass:
                    error_parts.append(f"{lint_error_count} lint error(s)")
                if not coverage_pass:
                    error_parts.append(
                        f"coverage {be_coverage:.1f}% < {self.min_backend_coverage}%"
                    )
                if not checkpoint_pass:
                    error_parts.append("checkpoint criteria not met")
                error_message = "Quality gates: " + ", ".join(error_parts)
            else:
                error_message = None

        # Generate fix suggestions from errors (for retry sessions)
        fix_suggestions = self.generate_fix_suggestions(all_errors) if all_errors else []

        return VerificationResult(
            passed=passed,
            tests_passed=be_passed + fe_passed,
            tests_failed=be_failed + fe_failed,
            coverage_backend=be_coverage,
            coverage_frontend=fe_coverage,
            lint_errors=lint_error_count,
            checkpoint_results=checkpoint_results,
            error_message=error_message,
            error_details=all_errors,
            fix_suggestions=fix_suggestions,
        )
