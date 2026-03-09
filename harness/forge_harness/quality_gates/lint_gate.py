#!/usr/bin/env python3
"""
Lint Gate Module for FORGE Harness

Auto-fixes and reports linting issues for staged files:
- Python: ruff check + ruff format
- JavaScript/TypeScript: eslint

Implements HRN-007 from harness/features.json
"""

import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class LintResult:
    """Result of linting staged files."""

    passed: bool
    errors: list[str] = field(default_factory=list)  # file:line:message format
    auto_fixed: int = 0

    @property
    def summary(self) -> str:
        """Generate human-readable summary."""
        status = "PASSED" if self.passed else "FAILED"
        lines = [f"Lint Gate: {status}"]
        if self.auto_fixed > 0:
            lines.append(f"Auto-fixed {self.auto_fixed} issues")
        if self.errors:
            lines.append(f"Remaining errors ({len(self.errors)}):")
            for err in self.errors[:10]:
                lines.append(f"  {err}")
            if len(self.errors) > 10:
                lines.append(f"  ... and {len(self.errors) - 10} more")
        return "\n".join(lines)


@dataclass
class _InternalLintResult:
    """Internal result structure for detailed linting of a file type."""

    file_type: str  # "Python" or "JavaScript/TypeScript"
    passed: bool
    files_checked: int
    fixes_applied: int
    remaining_errors: list[str] = field(default_factory=list)
    output: str = ""
    duration_seconds: float = 0.0


@dataclass
class _LintGateResult:
    """Internal overall result of all linting checks."""

    passed: bool
    results: list[_InternalLintResult] = field(default_factory=list)
    total_duration: float = 0.0


class _LintGate:
    """Internal lint gate implementation for staged files with auto-fix support."""

    def __init__(self, forge_root: Path | None = None, auto_fix: bool = True):
        """Initialize lint gate.

        Args:
            forge_root: Root directory of FORGE repo. Defaults to current directory.
            auto_fix: Whether to attempt auto-fixing issues.
        """
        self.forge_root = forge_root or Path.cwd()
        self.auto_fix = auto_fix

        if not (self.forge_root / ".git").exists():
            # Try to find git root
            current = self.forge_root
            while current != current.parent:
                if (current / ".git").exists():
                    self.forge_root = current
                    break
                current = current.parent

        logger.debug(f"Initialized LintGate with root: {self.forge_root}")

    def get_staged_files(self) -> list[str]:
        """Get list of staged files from git.

        Returns:
            List of relative file paths staged for commit.
        """
        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=self.forge_root,
                capture_output=True,
                text=True,
                check=True,
            )
            files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
            logger.debug(f"Found {len(files)} staged files")
            return files
        except subprocess.CalledProcessError as e:
            logger.error(f"Error getting staged files: {e}")
            return []

    def restage_files(self, files: list[str]) -> bool:
        """Re-stage files after auto-fixes.

        Args:
            files: List of file paths to re-stage.

        Returns:
            True if successful, False otherwise.
        """
        if not files:
            return True

        try:
            subprocess.run(
                ["git", "add"] + files,
                cwd=self.forge_root,
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info(f"Re-staged {len(files)} files after auto-fix")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Error re-staging files: {e}")
            return False

    def lint_python(self, files: list[str]) -> _InternalLintResult:
        """Lint Python files with ruff.

        Args:
            files: List of file paths to check (will filter for .py).

        Returns:
            _InternalLintResult with check outcome.
        """
        py_files = [f for f in files if f.endswith(".py")]

        if not py_files:
            logger.debug("No Python files to check")
            return _InternalLintResult(
                file_type="Python",
                passed=True,
                files_checked=0,
                fixes_applied=0,
                output="No Python files to check",
            )

        start_time = time.time()
        fixes_applied = 0
        remaining_errors = []
        all_output = []

        try:
            # Convert relative paths to absolute for ruff
            abs_py_files = [str((self.forge_root / f).resolve()) for f in py_files]

            # Determine ruff command and working directory
            working_dir = self.forge_root
            if (self.forge_root / "harness" / "pyproject.toml").exists():
                ruff_cmd_base = ["uv", "run", "ruff"]
                working_dir = self.forge_root / "harness"
            else:
                ruff_cmd_base = ["ruff"]

            # Step 1: Auto-fix if enabled
            if self.auto_fix:
                logger.info(f"Running ruff check --fix on {len(py_files)} files...")
                fix_result = subprocess.run(
                    ruff_cmd_base + ["check", "--fix"] + abs_py_files,
                    cwd=working_dir,
                    capture_output=True,
                    text=True,
                )

                if fix_result.returncode == 0 and fix_result.stdout:
                    # Count fixes from output
                    fix_lines = [
                        line
                        for line in fix_result.stdout.split("\n")
                        if "Fixed" in line or "fixed" in line
                    ]
                    fixes_applied = len(fix_lines)
                    all_output.append("=== Auto-fixes Applied ===")
                    all_output.append(fix_result.stdout)
                    logger.info(f"Applied {fixes_applied} auto-fixes")

                    # Re-stage fixed files
                    if fixes_applied > 0:
                        self.restage_files(py_files)

            # Step 2: Check for remaining issues (no fix)
            logger.info("Running ruff check (no-fix) to detect remaining issues...")
            check_result = subprocess.run(
                ruff_cmd_base + ["check", "--no-fix"] + abs_py_files,
                cwd=working_dir,
                capture_output=True,
                text=True,
            )

            if check_result.returncode != 0:
                all_output.append("\n=== Remaining Ruff Errors ===")
                all_output.append(check_result.stdout)
                if check_result.stderr:
                    all_output.append(check_result.stderr)

                # Parse errors for summary
                for line in check_result.stdout.split("\n"):
                    if line.strip() and not line.startswith("Found"):
                        remaining_errors.append(line.strip())

            # Step 3: Check formatting
            logger.info("Running ruff format --check...")
            format_result = subprocess.run(
                ruff_cmd_base + ["format", "--check"] + abs_py_files,
                cwd=working_dir,
                capture_output=True,
                text=True,
            )

            if format_result.returncode != 0:
                all_output.append("\n=== Formatting Issues ===")
                all_output.append(format_result.stdout)
                if format_result.stderr:
                    all_output.append(format_result.stderr)

                # Count formatting issues
                format_lines = [
                    line
                    for line in format_result.stdout.split("\n")
                    if "would reformat" in line.lower()
                ]
                for line in format_lines:
                    remaining_errors.append(f"Format: {line.strip()}")

            duration = time.time() - start_time
            passed = len(remaining_errors) == 0

            output = (
                "\n".join(all_output)
                if all_output
                else f"✓ All {len(py_files)} Python files passed linting"
            )

            return _InternalLintResult(
                file_type="Python",
                passed=passed,
                files_checked=len(py_files),
                fixes_applied=fixes_applied,
                remaining_errors=remaining_errors,
                output=output,
                duration_seconds=duration,
            )

        except FileNotFoundError:
            logger.error("ruff not found")
            return _InternalLintResult(
                file_type="Python",
                passed=False,
                files_checked=len(py_files),
                fixes_applied=0,
                remaining_errors=["Error: ruff not found. Install with: uv pip install ruff"],
                output="Error: ruff not found. Install with: uv pip install ruff",
                duration_seconds=time.time() - start_time,
            )
        except Exception as e:
            logger.exception("Error running ruff")
            return _InternalLintResult(
                file_type="Python",
                passed=False,
                files_checked=len(py_files),
                fixes_applied=0,
                remaining_errors=[f"Error running ruff: {e}"],
                output=f"Error running ruff: {e}",
                duration_seconds=time.time() - start_time,
            )

    def lint_typescript(self, files: list[str]) -> _InternalLintResult:
        """Lint JavaScript/TypeScript files with eslint.

        Args:
            files: List of file paths to check (will filter for .ts/.tsx/.js/.jsx).

        Returns:
            _InternalLintResult with check outcome.
        """
        js_files = [
            f for f in files if f.endswith((".ts", ".tsx", ".js", ".jsx")) and "command_center" in f
        ]

        if not js_files:
            logger.debug("No JavaScript/TypeScript files to check")
            return _InternalLintResult(
                file_type="JavaScript/TypeScript",
                passed=True,
                files_checked=0,
                fixes_applied=0,
                output="No JavaScript/TypeScript files to check",
            )

        start_time = time.time()
        fixes_applied = 0
        remaining_errors = []
        all_output = []

        # Find command_center directory
        cc_path = self.forge_root / "harness" / "command_center"
        if not cc_path.exists():
            logger.warning("command_center directory not found")
            return _InternalLintResult(
                file_type="JavaScript/TypeScript",
                passed=False,
                files_checked=len(js_files),
                fixes_applied=0,
                remaining_errors=["command_center directory not found"],
                output="command_center directory not found",
                duration_seconds=time.time() - start_time,
            )

        eslint_path = cc_path / "node_modules" / ".bin" / "eslint"
        if not eslint_path.exists():
            logger.warning("eslint not found in command_center")
            return _InternalLintResult(
                file_type="JavaScript/TypeScript",
                passed=False,
                files_checked=len(js_files),
                fixes_applied=0,
                remaining_errors=["eslint not found. Run: cd command_center && npm install"],
                output="eslint not found. Run: cd command_center && npm install",
                duration_seconds=time.time() - start_time,
            )

        try:
            # Convert file paths to be relative to command_center
            relative_files = []
            for f in js_files:
                full_path = self.forge_root / f
                try:
                    rel_path = full_path.relative_to(cc_path)
                    relative_files.append(str(rel_path))
                except ValueError:
                    # File is not under command_center
                    logger.warning(f"File {f} is not under command_center, skipping")
                    continue

            if not relative_files:
                return _InternalLintResult(
                    file_type="JavaScript/TypeScript",
                    passed=True,
                    files_checked=0,
                    fixes_applied=0,
                    output="No files under command_center to check",
                )

            # Step 1: Auto-fix if enabled
            if self.auto_fix:
                logger.info(f"Running eslint --fix on {len(relative_files)} files...")
                fix_result = subprocess.run(
                    [str(eslint_path), "--fix"] + relative_files,
                    cwd=cc_path,
                    capture_output=True,
                    text=True,
                )

                # eslint --fix always returns 0 if it can apply fixes
                # We need to check if files were modified
                if fix_result.stdout:
                    all_output.append("=== Auto-fixes Applied ===")
                    all_output.append(fix_result.stdout)

                    # Assume fixes were applied if output is present
                    fixes_applied = len(relative_files)
                    logger.info(f"Applied auto-fixes to {fixes_applied} files")

                    # Re-stage fixed files
                    if fixes_applied > 0:
                        self.restage_files(js_files)

            # Step 2: Check for remaining issues (no fix)
            logger.info("Running eslint (no-fix) to detect remaining issues...")
            check_result = subprocess.run(
                [str(eslint_path)] + relative_files,
                cwd=cc_path,
                capture_output=True,
                text=True,
            )

            if check_result.returncode != 0:
                all_output.append("\n=== Remaining ESLint Errors ===")
                all_output.append(check_result.stdout)
                if check_result.stderr:
                    all_output.append(check_result.stderr)

                # Parse errors for summary
                for line in check_result.stdout.split("\n"):
                    if line.strip() and ("error" in line.lower() or "warning" in line.lower()):
                        remaining_errors.append(line.strip())

            duration = time.time() - start_time
            passed = len(remaining_errors) == 0

            output = (
                "\n".join(all_output)
                if all_output
                else f"✓ All {len(relative_files)} JS/TS files passed linting"
            )

            return _InternalLintResult(
                file_type="JavaScript/TypeScript",
                passed=passed,
                files_checked=len(relative_files),
                fixes_applied=fixes_applied,
                remaining_errors=remaining_errors,
                output=output,
                duration_seconds=duration,
            )

        except Exception as e:
            logger.exception("Error running eslint")
            return _InternalLintResult(
                file_type="JavaScript/TypeScript",
                passed=False,
                files_checked=len(js_files),
                fixes_applied=0,
                remaining_errors=[f"Error running eslint: {e}"],
                output=f"Error running eslint: {e}",
                duration_seconds=time.time() - start_time,
            )

    def run_all(self, python_only: bool = False, typescript_only: bool = False) -> _LintGateResult:
        """Run all lint checks.

        Args:
            python_only: Only check Python files.
            typescript_only: Only check TypeScript files.

        Returns:
            _LintGateResult with all check outcomes.
        """
        start_time = time.time()

        staged_files = self.get_staged_files()

        if not staged_files:
            logger.info("No staged files to check")
            return _LintGateResult(
                passed=True,
                results=[],
                total_duration=time.time() - start_time,
            )

        results = []

        # Run Python checks
        if not typescript_only:
            py_result = self.lint_python(staged_files)
            results.append(py_result)

        # Run TypeScript checks
        if not python_only:
            ts_result = self.lint_typescript(staged_files)
            results.append(ts_result)

        total_duration = time.time() - start_time
        all_passed = all(result.passed for result in results)

        return _LintGateResult(
            passed=all_passed,
            results=results,
            total_duration=total_duration,
        )


def lint_staged_files(
    forge_root: Path | None = None,
    auto_fix: bool = True,
) -> LintResult:
    """
    Lint staged files with ruff (Python) and eslint (TypeScript).

    Args:
        forge_root: Root of FORGE repository. Defaults to finding git root.
        auto_fix: Whether to auto-fix issues.

    Returns:
        LintResult with passed status, errors list, and auto_fixed count.
    """
    gate = _LintGate(forge_root=forge_root, auto_fix=auto_fix)
    gate_result = gate.run_all()

    # Aggregate results into simplified LintResult
    errors = []
    auto_fixed = 0
    for result in gate_result.results:
        auto_fixed += result.fixes_applied
        errors.extend(result.remaining_errors)

    return LintResult(
        passed=gate_result.passed,
        errors=errors,
        auto_fixed=auto_fixed,
    )


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Lint gate for staged files")
    parser.add_argument("--check", action="store_true", help="Report only, no fixes")
    args = parser.parse_args()

    # Configure logging for CLI
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    result = lint_staged_files(auto_fix=not args.check)
    print(result.summary)
    sys.exit(0 if result.passed else 1)

"""
Lint Gate - Pre-commit Linting Quality Gate
===========================================

Runs linting checks on staged files before commit:
- Python files: ruff check
- JS/TS files: eslint (if available)

Supports auto-fix mode and provides detailed error reporting.

Usage:
    from forge_harness.quality_gates import run_lint_gate, LintGate

    # Run in check mode (no fixes)
    result = await run_lint_gate(check_only=True)
    if not result.passed:
        print(result.summary())
        sys.exit(1)

    # Run with auto-fix
    result = await run_lint_gate(fix=True)

CLI Usage:
    python -m forge_harness.quality_gates.lint_gate --check
    python -m forge_harness.quality_gates.lint_gate --fix
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Models
# =============================================================================


@dataclass
class LintIssue:
    """A single linting issue."""

    file: str
    line: int | None
    column: int | None
    code: str
    message: str
    severity: str = "error"  # error, warning, info
    fixable: bool = False

    def __str__(self) -> str:
        """Format issue for display."""
        location = f"{self.file}"
        if self.line is not None:
            location += f":{self.line}"
            if self.column is not None:
                location += f":{self.column}"

        fix_marker = " [fixable]" if self.fixable else ""
        return f"{location} - {self.code}: {self.message}{fix_marker}"


@dataclass
class LintResult:
    """Result of running lint gate."""

    passed: bool
    files_checked: int
    issues: list[LintIssue] = field(default_factory=list)
    fixed_issues: list[LintIssue] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        """Count of error-level issues."""
        return sum(1 for issue in self.issues if issue.severity == "error")

    @property
    def warning_count(self) -> int:
        """Count of warning-level issues."""
        return sum(1 for issue in self.issues if issue.severity == "warning")

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = []
        lines.append("=" * 70)
        lines.append("Lint Gate Results")
        lines.append("=" * 70)
        lines.append(f"Status: {'PASSED' if self.passed else 'FAILED'}")
        lines.append(f"Files checked: {self.files_checked}")

        if self.fixed_issues:
            lines.append(f"Issues fixed: {len(self.fixed_issues)}")

        if self.issues:
            lines.append(f"Errors: {self.error_count}")
            lines.append(f"Warnings: {self.warning_count}")
            lines.append("")
            lines.append("Issues:")
            for issue in self.issues:
                lines.append(f"  {issue}")

        if self.errors:
            lines.append("")
            lines.append("Errors during execution:")
            for error in self.errors:
                lines.append(f"  {error}")

        lines.append("=" * 70)
        return "\n".join(lines)


# =============================================================================
# Lint Gate Implementation
# =============================================================================


class LintGate:
    """
    Linting quality gate for staged files.

    Runs appropriate linters based on file type:
    - Python (.py): ruff check
    - JavaScript/TypeScript (.js, .ts, .tsx, .jsx): eslint
    """

    PYTHON_EXTENSIONS = {".py"}
    JS_TS_EXTENSIONS = {".js", ".ts", ".tsx", ".jsx"}

    def __init__(self, repo_root: Path | None = None):
        """
        Initialize lint gate.

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
        Categorize files by type for appropriate linting.

        Args:
            files: List of file paths

        Returns:
            Dictionary mapping category to file list
            Categories: "python", "js_ts"
        """
        categorized: dict[str, list[Path]] = {
            "python": [],
            "js_ts": [],
        }

        for file_path in files:
            suffix = file_path.suffix
            if suffix in self.PYTHON_EXTENSIONS:
                categorized["python"].append(file_path)
            elif suffix in self.JS_TS_EXTENSIONS:
                categorized["js_ts"].append(file_path)

        return categorized

    async def run_ruff(
        self,
        files: list[Path],
        fix: bool = False,
    ) -> tuple[list[LintIssue], list[LintIssue]]:
        """
        Run ruff linter on Python files.

        Args:
            files: Python files to check
            fix: If True, attempt to auto-fix issues

        Returns:
            Tuple of (remaining_issues, fixed_issues)
        """
        if not files:
            return [], []

        issues: list[LintIssue] = []
        fixed_issues: list[LintIssue] = []

        # Build ruff command
        cmd = ["ruff", "check"]
        if fix:
            cmd.append("--fix")

        # Add output format
        cmd.extend(["--output-format", "json"])

        # Add file paths
        cmd.extend(str(f) for f in files)

        try:
            # Run ruff (note: it returns non-zero if issues found)
            result = await self._run_command(
                cmd,
                cwd=self.repo_root,
                allow_failure=True,
            )

            if result:
                # Parse JSON output
                try:
                    ruff_output = json.loads(result)

                    for item in ruff_output:
                        # Determine relative path
                        file_path = item.get("filename", "")
                        try:
                            file_path = str(Path(file_path).relative_to(self.repo_root))
                        except ValueError:
                            pass

                        issue = LintIssue(
                            file=file_path,
                            line=item.get("location", {}).get("row"),
                            column=item.get("location", {}).get("column"),
                            code=item.get("code", "unknown"),
                            message=item.get("message", ""),
                            severity="error" if item.get("level") == "error" else "warning",
                            fixable=item.get("fix") is not None,
                        )

                        # If we ran with --fix and the issue is fixable, it should be fixed
                        if fix and issue.fixable:
                            fixed_issues.append(issue)
                        else:
                            issues.append(issue)

                except json.JSONDecodeError:
                    logger.warning("Failed to parse ruff JSON output")

        except Exception as e:
            logger.error(f"Ruff execution failed: {e}")

        return issues, fixed_issues

    async def run_eslint(
        self,
        files: list[Path],
        fix: bool = False,
    ) -> tuple[list[LintIssue], list[LintIssue]]:
        """
        Run eslint on JS/TS files.

        Args:
            files: JS/TS files to check
            fix: If True, attempt to auto-fix issues

        Returns:
            Tuple of (remaining_issues, fixed_issues)
        """
        if not files:
            return [], []

        issues: list[LintIssue] = []
        fixed_issues: list[LintIssue] = []

        # Check if eslint is available
        if not await self._command_exists("eslint"):
            logger.info("eslint not found, skipping JS/TS linting")
            return [], []

        # Build eslint command
        cmd = ["eslint"]
        if fix:
            cmd.append("--fix")

        # Add output format
        cmd.extend(["--format", "json"])

        # Add file paths
        cmd.extend(str(f) for f in files)

        try:
            # Run eslint (note: it returns non-zero if issues found)
            result = await self._run_command(
                cmd,
                cwd=self.repo_root,
                allow_failure=True,
            )

            if result:
                # Parse JSON output
                try:
                    eslint_output = json.loads(result)

                    for file_result in eslint_output:
                        file_path = file_result.get("filePath", "")
                        try:
                            file_path = str(Path(file_path).relative_to(self.repo_root))
                        except ValueError:
                            pass

                        # Track how many issues were fixed
                        fixed_count = file_result.get("fixableErrorCount", 0) + file_result.get(
                            "fixableWarningCount", 0
                        )

                        # If fix was run and issues were fixed, record them
                        if fix and fixed_count > 0:
                            # We don't get details of fixed issues, so create a summary
                            fixed_issues.append(
                                LintIssue(
                                    file=file_path,
                                    line=None,
                                    column=None,
                                    code="auto-fix",
                                    message=f"{fixed_count} issues auto-fixed",
                                    severity="info",
                                    fixable=True,
                                )
                            )

                        # Process remaining messages
                        for msg in file_result.get("messages", []):
                            issue = LintIssue(
                                file=file_path,
                                line=msg.get("line"),
                                column=msg.get("column"),
                                code=msg.get("ruleId", "unknown"),
                                message=msg.get("message", ""),
                                severity=("error" if msg.get("severity") == 2 else "warning"),
                                fixable=msg.get("fix") is not None,
                            )
                            issues.append(issue)

                except json.JSONDecodeError:
                    logger.warning("Failed to parse eslint JSON output")

        except Exception as e:
            logger.error(f"eslint execution failed: {e}")

        return issues, fixed_issues

    async def run(
        self,
        check_only: bool = False,
        fix: bool = False,
    ) -> LintResult:
        """
        Run lint gate on staged files.

        Args:
            check_only: If True, only check without fixing (same as fix=False)
            fix: If True, attempt to auto-fix issues

        Returns:
            LintResult with details of issues found
        """
        # Get staged files
        logger.info("Getting staged files...")
        staged_files = await self.get_staged_files()

        if not staged_files:
            logger.info("No staged files to check")
            return LintResult(passed=True, files_checked=0)

        logger.info(f"Found {len(staged_files)} staged files")

        # Categorize files
        categorized = self.categorize_files(staged_files)
        python_files = categorized["python"]
        js_ts_files = categorized["js_ts"]

        logger.info(f"Python files: {len(python_files)}, JS/TS files: {len(js_ts_files)}")

        all_issues: list[LintIssue] = []
        all_fixed: list[LintIssue] = []
        errors: list[str] = []

        # Determine if we should fix
        should_fix = fix and not check_only

        # Run ruff on Python files
        if python_files:
            logger.info(f"Running ruff on {len(python_files)} Python files...")
            try:
                ruff_issues, ruff_fixed = await self.run_ruff(python_files, fix=should_fix)
                all_issues.extend(ruff_issues)
                all_fixed.extend(ruff_fixed)
            except Exception as e:
                error_msg = f"Ruff check failed: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        # Run eslint on JS/TS files
        if js_ts_files:
            logger.info(f"Running eslint on {len(js_ts_files)} JS/TS files...")
            try:
                eslint_issues, eslint_fixed = await self.run_eslint(js_ts_files, fix=should_fix)
                all_issues.extend(eslint_issues)
                all_fixed.extend(eslint_fixed)
            except Exception as e:
                error_msg = f"eslint check failed: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        # Determine pass/fail
        # Gate passes if no unfixable errors remain
        error_issues = [i for i in all_issues if i.severity == "error"]
        passed = len(error_issues) == 0 and len(errors) == 0

        result = LintResult(
            passed=passed,
            files_checked=len(python_files) + len(js_ts_files),
            issues=all_issues,
            fixed_issues=all_fixed,
            errors=errors,
        )

        logger.info(
            f"Lint gate {'PASSED' if passed else 'FAILED'}: "
            f"{result.error_count} errors, {result.warning_count} warnings, "
            f"{len(all_fixed)} fixed"
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

            return stdout.decode()

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


async def run_lint_gate(
    repo_root: Path | None = None,
    check_only: bool = False,
    fix: bool = False,
) -> LintResult:
    """
    Run lint gate on staged files.

    Args:
        repo_root: Repository root (defaults to current directory)
        check_only: If True, only check without fixing
        fix: If True, attempt to auto-fix issues

    Returns:
        LintResult with details
    """
    gate = LintGate(repo_root=repo_root)
    return await gate.run(check_only=check_only, fix=fix)


async def main_async() -> int:
    """Async main entry point."""
    parser = argparse.ArgumentParser(
        description="Lint gate for staged files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check staged files (dry-run)
  python -m forge_harness.quality_gates.lint_gate --check

  # Check and auto-fix staged files
  python -m forge_harness.quality_gates.lint_gate --fix

  # Use in pre-commit hook
  python -m forge_harness.quality_gates.lint_gate --check || exit 1
        """,
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: report issues without fixing (default)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Fix mode: attempt to auto-fix issues",
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

    # Default to check mode if neither specified
    if not args.check and not args.fix:
        args.check = True

    # Run lint gate
    result = await run_lint_gate(
        repo_root=args.repo_root,
        check_only=args.check,
        fix=args.fix,
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
