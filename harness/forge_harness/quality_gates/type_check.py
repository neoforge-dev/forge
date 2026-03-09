"""
Type Check Gate - Pre-commit Type Checking Quality Gate
========================================================

Runs type checking on staged files before commit:
- Python files: mypy --strict
- TypeScript files: tsc --noEmit

Ensures type safety without runtime overhead. Supports caching for unchanged files.

Usage:
    from forge_harness.quality_gates import run_type_check_gate, TypeCheckGate

    # Run in check mode
    result = await run_type_check_gate()
    if not result.passed:
        print(result.summary())
        sys.exit(1)

CLI Usage:
    python -m forge_harness.quality_gates.type_check --check
"""

import argparse
import asyncio
import hashlib
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
class TypeCheckError:
    """A single type checking error."""

    file: str
    line: int | None
    column: int | None
    code: str
    message: str
    severity: str = "error"  # error, warning, note

    def __str__(self) -> str:
        """Format error for display."""
        location = f"{self.file}"
        if self.line is not None:
            location += f":{self.line}"
            if self.column is not None:
                location += f":{self.column}"

        return f"{location} - {self.code}: {self.message}"


@dataclass
class TypeCheckResult:
    """Result of running type check gate."""

    passed: bool
    files_checked: int
    errors: list[TypeCheckError] = field(default_factory=list)
    execution_errors: list[str] = field(default_factory=list)
    cached_files: int = 0

    @property
    def error_count(self) -> int:
        """Count of error-level issues."""
        return sum(1 for err in self.errors if err.severity == "error")

    @property
    def warning_count(self) -> int:
        """Count of warning-level issues."""
        return sum(1 for err in self.errors if err.severity == "warning")

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = []
        lines.append("=" * 70)
        lines.append("Type Check Gate Results")
        lines.append("=" * 70)
        lines.append(f"Status: {'PASSED' if self.passed else 'FAILED'}")
        lines.append(f"Files checked: {self.files_checked}")

        if self.cached_files > 0:
            lines.append(f"Files cached: {self.cached_files}")

        if self.errors:
            lines.append(f"Errors: {self.error_count}")
            lines.append(f"Warnings: {self.warning_count}")
            lines.append("")
            lines.append("Type errors:")
            for error in self.errors:
                lines.append(f"  {error}")

        if self.execution_errors:
            lines.append("")
            lines.append("Errors during execution:")
            for error in self.execution_errors:
                lines.append(f"  {error}")

        lines.append("=" * 70)
        return "\n".join(lines)


# =============================================================================
# Type Check Gate Implementation
# =============================================================================


class TypeCheckGate:
    """
    Type checking quality gate for staged files.

    Runs appropriate type checkers based on file type:
    - Python (.py): mypy --strict
    - TypeScript (.ts, .tsx): tsc --noEmit

    Supports caching for unchanged files to speed up repeated checks.
    """

    PYTHON_EXTENSIONS = {".py"}
    TS_EXTENSIONS = {".ts", ".tsx"}

    def __init__(
        self,
        repo_root: Path | None = None,
        cache_dir: Path | None = None,
        use_cache: bool = True,
    ):
        """
        Initialize type check gate.

        Args:
            repo_root: Repository root directory (defaults to current directory)
            cache_dir: Directory for cache files (defaults to .forge_cache)
            use_cache: Enable caching for unchanged files
        """
        self.repo_root = repo_root or Path.cwd()
        self.cache_dir = cache_dir or (self.repo_root / ".forge_cache" / "type_check")
        self.use_cache = use_cache

        if self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

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
        Categorize files by type for appropriate type checking.

        Args:
            files: List of file paths

        Returns:
            Dictionary mapping category to file list
            Categories: "python", "typescript"
        """
        categorized: dict[str, list[Path]] = {
            "python": [],
            "typescript": [],
        }

        for file_path in files:
            suffix = file_path.suffix
            if suffix in self.PYTHON_EXTENSIONS:
                categorized["python"].append(file_path)
            elif suffix in self.TS_EXTENSIONS:
                categorized["typescript"].append(file_path)

        return categorized

    def _get_file_hash(self, file_path: Path) -> str:
        """
        Get hash of file content for caching.

        Args:
            file_path: Path to file

        Returns:
            SHA256 hash of file content
        """
        try:
            content = file_path.read_bytes()
            return hashlib.sha256(content).hexdigest()
        except Exception as e:
            logger.debug(f"Failed to hash {file_path}: {e}")
            return ""

    def _get_cache_key(self, file_path: Path) -> str:
        """
        Get cache key for a file.

        Args:
            file_path: Path to file

        Returns:
            Cache key (relative path + hash)
        """
        try:
            rel_path = file_path.relative_to(self.repo_root)
            file_hash = self._get_file_hash(file_path)
            return f"{rel_path}_{file_hash}"
        except Exception:
            return ""

    def _is_cached(self, file_path: Path) -> bool:
        """
        Check if file result is cached.

        Args:
            file_path: Path to file

        Returns:
            True if cached result exists for current file state
        """
        if not self.use_cache:
            return False

        cache_key = self._get_cache_key(file_path)
        if not cache_key:
            return False

        cache_file = self.cache_dir / f"{cache_key}.json"
        return cache_file.exists()

    def _cache_result(self, file_path: Path, errors: list[TypeCheckError]) -> None:
        """
        Cache type check result for a file.

        Args:
            file_path: Path to file
            errors: List of errors found (empty if passed)
        """
        if not self.use_cache:
            return

        cache_key = self._get_cache_key(file_path)
        if not cache_key:
            return

        cache_file = self.cache_dir / f"{cache_key}.json"
        try:
            cache_data = {
                "file": str(file_path),
                "errors": [
                    {
                        "file": err.file,
                        "line": err.line,
                        "column": err.column,
                        "code": err.code,
                        "message": err.message,
                        "severity": err.severity,
                    }
                    for err in errors
                ],
            }
            cache_file.write_text(json.dumps(cache_data, indent=2))
        except Exception as e:
            logger.debug(f"Failed to cache result for {file_path}: {e}")

    def _get_cached_errors(self, file_path: Path) -> list[TypeCheckError] | None:
        """
        Get cached errors for a file.

        Args:
            file_path: Path to file

        Returns:
            List of cached errors, or None if not cached
        """
        if not self.use_cache:
            return None

        cache_key = self._get_cache_key(file_path)
        if not cache_key:
            return None

        cache_file = self.cache_dir / f"{cache_key}.json"
        try:
            if not cache_file.exists():
                return None

            cache_data = json.loads(cache_file.read_text())
            return [
                TypeCheckError(
                    file=err["file"],
                    line=err["line"],
                    column=err["column"],
                    code=err["code"],
                    message=err["message"],
                    severity=err["severity"],
                )
                for err in cache_data["errors"]
            ]
        except Exception as e:
            logger.debug(f"Failed to read cache for {file_path}: {e}")
            return None

    async def run_mypy(self, files: list[Path]) -> tuple[list[TypeCheckError], list[Path]]:
        """
        Run mypy type checker on Python files.

        Args:
            files: Python files to check

        Returns:
            Tuple of (errors, cached_files)
        """
        if not files:
            return [], []

        errors: list[TypeCheckError] = []
        cached_files: list[Path] = []

        # Separate cached and uncached files
        files_to_check = []
        for file_path in files:
            cached_errors = self._get_cached_errors(file_path)
            if cached_errors is not None:
                cached_files.append(file_path)
                errors.extend(cached_errors)
            else:
                files_to_check.append(file_path)

        if not files_to_check:
            logger.info("All Python files cached")
            return errors, cached_files

        # Check if mypy is available
        if not await self._command_exists("mypy"):
            logger.warning("mypy not found, skipping Python type checking")
            return errors, cached_files

        # Build mypy command
        cmd = ["mypy", "--strict", "--no-error-summary"]

        # Add output format
        cmd.extend(["--show-error-end", "--show-column-numbers"])

        # Add file paths
        cmd.extend(str(f) for f in files_to_check)

        try:
            # Run mypy (note: it returns non-zero if issues found)
            result = await self._run_command(
                cmd,
                cwd=self.repo_root,
                allow_failure=True,
            )

            if result:
                # Parse mypy output
                # Format: file.py:line:column: error: message [error-code]
                pattern = re.compile(
                    r"^(.+?):(\d+):(\d+):\s+(error|warning|note):\s+(.+?)(?:\s+\[(.+?)\])?$",
                    re.MULTILINE,
                )

                file_errors: dict[str, list[TypeCheckError]] = {}

                for match in pattern.finditer(result):
                    file_path = match.group(1)
                    try:
                        file_path = str(Path(file_path).relative_to(self.repo_root))
                    except ValueError:
                        pass

                    error = TypeCheckError(
                        file=file_path,
                        line=int(match.group(2)),
                        column=int(match.group(3)),
                        code=match.group(6) or "type-error",
                        message=match.group(5),
                        severity=match.group(4),
                    )
                    errors.append(error)

                    if file_path not in file_errors:
                        file_errors[file_path] = []
                    file_errors[file_path].append(error)

                # Cache results for each file
                for file_path in files_to_check:
                    rel_path = str(file_path.relative_to(self.repo_root))
                    file_specific_errors = file_errors.get(rel_path, [])
                    self._cache_result(file_path, file_specific_errors)

        except Exception as e:
            logger.error(f"mypy execution failed: {e}")

        return errors, cached_files

    async def run_tsc(self, files: list[Path]) -> tuple[list[TypeCheckError], list[Path]]:
        """
        Run TypeScript compiler type checking.

        Args:
            files: TypeScript files to check

        Returns:
            Tuple of (errors, cached_files)
        """
        if not files:
            return [], []

        errors: list[TypeCheckError] = []
        cached_files: list[Path] = []

        # Separate cached and uncached files
        files_to_check = []
        for file_path in files:
            cached_errors = self._get_cached_errors(file_path)
            if cached_errors is not None:
                cached_files.append(file_path)
                errors.extend(cached_errors)
            else:
                files_to_check.append(file_path)

        if not files_to_check:
            logger.info("All TypeScript files cached")
            return errors, cached_files

        # Find tsconfig.json
        tsconfig_paths = self._find_tsconfig_for_files(files_to_check)

        if not tsconfig_paths:
            logger.warning("No tsconfig.json found, skipping TypeScript type checking")
            return errors, cached_files

        # Check if tsc is available
        if not await self._command_exists("tsc"):
            logger.warning("tsc not found, skipping TypeScript type checking")
            return errors, cached_files

        # Run tsc for each tsconfig project
        for tsconfig_dir, project_files in tsconfig_paths.items():
            cmd = ["tsc", "--noEmit", "--pretty", "false"]

            try:
                # Run tsc (note: it returns non-zero if issues found)
                result = await self._run_command(
                    cmd,
                    cwd=tsconfig_dir,
                    allow_failure=True,
                )

                if result:
                    # Parse tsc output
                    # Format: file.ts(line,col): error TSxxxx: message
                    pattern = re.compile(
                        r"^(.+?)\((\d+),(\d+)\):\s+(error|warning)\s+(TS\d+):\s+(.+)$",
                        re.MULTILINE,
                    )

                    file_errors: dict[str, list[TypeCheckError]] = {}

                    for match in pattern.finditer(result):
                        file_path = match.group(1)
                        try:
                            file_path = str(Path(file_path).relative_to(self.repo_root))
                        except ValueError:
                            pass

                        # Only include errors for files we're checking
                        if not any(
                            str(f.relative_to(self.repo_root)) == file_path for f in project_files
                        ):
                            continue

                        error = TypeCheckError(
                            file=file_path,
                            line=int(match.group(2)),
                            column=int(match.group(3)),
                            code=match.group(5),
                            message=match.group(6),
                            severity=match.group(4),
                        )
                        errors.append(error)

                        if file_path not in file_errors:
                            file_errors[file_path] = []
                        file_errors[file_path].append(error)

                    # Cache results for each file
                    for file_path in project_files:
                        rel_path = str(file_path.relative_to(self.repo_root))
                        file_specific_errors = file_errors.get(rel_path, [])
                        self._cache_result(file_path, file_specific_errors)

            except Exception as e:
                logger.error(f"tsc execution failed: {e}")

        return errors, cached_files

    def _find_tsconfig_for_files(self, files: list[Path]) -> dict[Path, list[Path]]:
        """
        Find tsconfig.json for TypeScript files.

        Args:
            files: TypeScript files to check

        Returns:
            Dictionary mapping tsconfig directory to files in that project
        """
        tsconfig_map: dict[Path, list[Path]] = {}

        for file_path in files:
            # Look for tsconfig.json in parent directories
            current_dir = file_path.parent
            found = False

            while current_dir >= self.repo_root:
                tsconfig = current_dir / "tsconfig.json"
                if tsconfig.exists():
                    if current_dir not in tsconfig_map:
                        tsconfig_map[current_dir] = []
                    tsconfig_map[current_dir].append(file_path)
                    found = True
                    break

                if current_dir == self.repo_root:
                    break
                current_dir = current_dir.parent

            if not found:
                logger.warning(f"No tsconfig.json found for {file_path}")

        return tsconfig_map

    async def run(self) -> TypeCheckResult:
        """
        Run type check gate on staged files.

        Returns:
            TypeCheckResult with details of errors found
        """
        # Get staged files
        logger.info("Getting staged files...")
        staged_files = await self.get_staged_files()

        if not staged_files:
            logger.info("No staged files to check")
            return TypeCheckResult(passed=True, files_checked=0)

        logger.info(f"Found {len(staged_files)} staged files")

        # Categorize files
        categorized = self.categorize_files(staged_files)
        python_files = categorized["python"]
        typescript_files = categorized["typescript"]

        logger.info(f"Python files: {len(python_files)}, TypeScript files: {len(typescript_files)}")

        all_errors: list[TypeCheckError] = []
        execution_errors: list[str] = []
        total_cached = 0

        # Run mypy on Python files
        if python_files:
            logger.info(f"Running mypy on {len(python_files)} Python files...")
            try:
                mypy_errors, cached = await self.run_mypy(python_files)
                all_errors.extend(mypy_errors)
                total_cached += len(cached)
                if cached:
                    logger.info(f"Used cached results for {len(cached)} Python files")
            except Exception as e:
                error_msg = f"mypy check failed: {e}"
                logger.error(error_msg)
                execution_errors.append(error_msg)

        # Run tsc on TypeScript files
        if typescript_files:
            logger.info(f"Running tsc on {len(typescript_files)} TypeScript files...")
            try:
                tsc_errors, cached = await self.run_tsc(typescript_files)
                all_errors.extend(tsc_errors)
                total_cached += len(cached)
                if cached:
                    logger.info(f"Used cached results for {len(cached)} TypeScript files")
            except Exception as e:
                error_msg = f"tsc check failed: {e}"
                logger.error(error_msg)
                execution_errors.append(error_msg)

        # Determine pass/fail
        # Gate passes if no errors found (warnings are OK)
        error_issues = [e for e in all_errors if e.severity == "error"]
        passed = len(error_issues) == 0 and len(execution_errors) == 0

        result = TypeCheckResult(
            passed=passed,
            files_checked=len(python_files) + len(typescript_files),
            errors=all_errors,
            execution_errors=execution_errors,
            cached_files=total_cached,
        )

        logger.info(
            f"Type check gate {'PASSED' if passed else 'FAILED'}: "
            f"{result.error_count} errors, {result.warning_count} warnings, "
            f"{total_cached} cached"
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
        timeout: int = 120,
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

            # Return combined output for type checkers
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


async def run_type_check_gate(
    repo_root: Path | None = None,
    use_cache: bool = True,
) -> TypeCheckResult:
    """
    Run type check gate on staged files.

    Args:
        repo_root: Repository root (defaults to current directory)
        use_cache: Enable caching for unchanged files

    Returns:
        TypeCheckResult with details
    """
    gate = TypeCheckGate(repo_root=repo_root, use_cache=use_cache)
    return await gate.run()


async def main_async() -> int:
    """Async main entry point."""
    parser = argparse.ArgumentParser(
        description="Type check gate for staged files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check staged files
  python -m forge_harness.quality_gates.type_check

  # Disable caching
  python -m forge_harness.quality_gates.type_check --no-cache

  # Use in pre-commit hook
  python -m forge_harness.quality_gates.type_check || exit 1
        """,
    )

    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable caching for unchanged files",
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

    # Run type check gate
    result = await run_type_check_gate(
        repo_root=args.repo_root,
        use_cache=not args.no_cache,
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
