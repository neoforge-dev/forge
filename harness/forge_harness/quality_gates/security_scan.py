"""
Security Scan Gate - Dependency Vulnerability Detection
=======================================================

Runs security audits on project dependencies before commit:
- Python projects: pip-audit
- JavaScript/TypeScript projects: npm audit

Blocks on HIGH or CRITICAL vulnerabilities.
Warns on MEDIUM vulnerabilities.
Caches results for 24 hours to avoid repeated scans.

Usage:
    from forge_harness.quality_gates import run_security_scan, SecurityScanGate

    # Run security scan
    result = await run_security_scan(project_path="/path/to/project")
    if not result.passed:
        print(result.summary())
        sys.exit(1)

CLI Usage:
    python -m forge_harness.quality_gates.security_scan /path/to/project
    python -m forge_harness.quality_gates.security_scan --verbose
"""

import argparse
import asyncio
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Models
# =============================================================================


@dataclass
class Vulnerability:
    """A single security vulnerability."""

    package: str
    version: str
    vulnerability_id: str  # CVE-xxx or GHSA-xxx
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    description: str
    fixed_version: str | None = None

    def __str__(self) -> str:
        """Format vulnerability for display."""
        fix_info = f" (fix: {self.fixed_version})" if self.fixed_version else " (no fix available)"
        return f"{self.package}@{self.version} - {self.vulnerability_id} [{self.severity}]: {self.description}{fix_info}"


@dataclass
class ScanResult:
    """Result of running security scan."""

    passed: bool
    project_type: str  # "python", "javascript", "unknown"
    vulnerabilities: list[Vulnerability] = field(default_factory=list)
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    scan_time: float = 0.0
    cached: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        """Total number of vulnerabilities."""
        return len(self.vulnerabilities)

    @property
    def blocking_count(self) -> int:
        """Count of blocking vulnerabilities (CRITICAL + HIGH)."""
        return self.critical_count + self.high_count

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = []
        lines.append("=" * 70)
        lines.append("Security Scan Results")
        lines.append("=" * 70)
        lines.append(f"Status: {'PASSED' if self.passed else 'FAILED'}")
        lines.append(f"Project type: {self.project_type}")
        lines.append(f"Scan time: {self.scan_time:.2f}s")

        if self.cached:
            lines.append("(Results from cache)")

        if self.total_count > 0:
            lines.append(f"\nVulnerabilities found: {self.total_count}")
            lines.append(f"  CRITICAL: {self.critical_count}")
            lines.append(f"  HIGH: {self.high_count}")
            lines.append(f"  MEDIUM: {self.medium_count}")
            lines.append(f"  LOW: {self.low_count}")

            if self.vulnerabilities:
                lines.append("\nDetails:")
                for vuln in self.vulnerabilities:
                    lines.append(f"  {vuln}")
        else:
            lines.append("\nNo vulnerabilities found!")

        if self.errors:
            lines.append("\nErrors during scan:")
            for error in self.errors:
                lines.append(f"  {error}")

        lines.append("=" * 70)
        return "\n".join(lines)


# =============================================================================
# Security Scan Gate Implementation
# =============================================================================


class SecurityScanGate:
    """
    Security scanning quality gate for project dependencies.

    Runs appropriate security audits based on project type:
    - Python (pyproject.toml): pip-audit
    - JavaScript/TypeScript (package.json): npm audit
    """

    # Cache directory and TTL
    CACHE_DIR = Path(".forge_cache/security_scans")
    CACHE_TTL_HOURS = 24

    def __init__(self, project_path: Path | None = None):
        """
        Initialize security scan gate.

        Args:
            project_path: Project root directory (defaults to current directory)
        """
        self.project_path = project_path or Path.cwd()
        self._ensure_cache_dir()

    def _ensure_cache_dir(self) -> None:
        """Ensure cache directory exists."""
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def detect_project_type(self) -> str:
        """
        Detect project type from config files.

        Returns:
            Project type: "python", "javascript", or "unknown"
        """
        if (self.project_path / "pyproject.toml").exists():
            return "python"
        elif (self.project_path / "package.json").exists():
            return "javascript"
        else:
            return "unknown"

    def _get_cache_key(self, project_type: str) -> str:
        """
        Generate cache key for project.

        Args:
            project_type: Type of project

        Returns:
            Cache key string
        """
        # Hash project path and type for cache key
        key_str = f"{self.project_path}:{project_type}"

        # Add dependency file hash to invalidate cache when dependencies change
        if project_type == "python":
            dep_file = self.project_path / "pyproject.toml"
        elif project_type == "javascript":
            dep_file = self.project_path / "package.json"
        else:
            dep_file = None

        if dep_file and dep_file.exists():
            try:
                content = dep_file.read_text()
                key_str += f":{hashlib.md5(content.encode()).hexdigest()}"
            except Exception as e:
                logger.debug(f"Failed to hash dependency file: {e}")

        return hashlib.sha256(key_str.encode()).hexdigest()

    def _get_cached_result(self, cache_key: str) -> ScanResult | None:
        """
        Get cached scan result if valid.

        Args:
            cache_key: Cache key

        Returns:
            Cached ScanResult or None if cache miss/expired
        """
        cache_file = self.CACHE_DIR / f"{cache_key}.json"

        if not cache_file.exists():
            return None

        try:
            with open(cache_file) as f:
                cached_data = json.load(f)

            # Check if cache is expired
            cached_time = cached_data.get("timestamp", 0)
            age_hours = (time.time() - cached_time) / 3600

            if age_hours > self.CACHE_TTL_HOURS:
                logger.debug(f"Cache expired ({age_hours:.1f}h old)")
                return None

            # Reconstruct ScanResult from cached data
            vulnerabilities = [
                Vulnerability(**vuln_data) for vuln_data in cached_data.get("vulnerabilities", [])
            ]

            result = ScanResult(
                passed=cached_data["passed"],
                project_type=cached_data["project_type"],
                vulnerabilities=vulnerabilities,
                critical_count=cached_data["critical_count"],
                high_count=cached_data["high_count"],
                medium_count=cached_data["medium_count"],
                low_count=cached_data["low_count"],
                scan_time=cached_data["scan_time"],
                cached=True,
                errors=cached_data.get("errors", []),
            )

            logger.info(f"Using cached scan result ({age_hours:.1f}h old)")
            return result

        except Exception as e:
            logger.debug(f"Failed to load cache: {e}")
            return None

    def _save_to_cache(self, cache_key: str, result: ScanResult) -> None:
        """
        Save scan result to cache.

        Args:
            cache_key: Cache key
            result: ScanResult to cache
        """
        cache_file = self.CACHE_DIR / f"{cache_key}.json"

        try:
            # Convert to dict for JSON serialization
            cached_data = {
                "timestamp": time.time(),
                "passed": result.passed,
                "project_type": result.project_type,
                "vulnerabilities": [
                    {
                        "package": v.package,
                        "version": v.version,
                        "vulnerability_id": v.vulnerability_id,
                        "severity": v.severity,
                        "description": v.description,
                        "fixed_version": v.fixed_version,
                    }
                    for v in result.vulnerabilities
                ],
                "critical_count": result.critical_count,
                "high_count": result.high_count,
                "medium_count": result.medium_count,
                "low_count": result.low_count,
                "scan_time": result.scan_time,
                "errors": result.errors,
            }

            with open(cache_file, "w") as f:
                json.dump(cached_data, f, indent=2)

            logger.debug(f"Saved scan result to cache: {cache_file}")

        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")

    async def run_pip_audit(self) -> tuple[list[Vulnerability], list[str]]:
        """
        Run pip-audit on Python project.

        Returns:
            Tuple of (vulnerabilities, errors)
        """
        vulnerabilities: list[Vulnerability] = []
        errors: list[str] = []

        # Check if pip-audit is available
        if not await self._command_exists("pip-audit"):
            error_msg = "pip-audit not installed. Install with: pip install pip-audit"
            logger.warning(error_msg)
            errors.append(error_msg)
            return vulnerabilities, errors

        # Build pip-audit command
        cmd = ["pip-audit", "--format=json", "--desc"]

        try:
            result = await self._run_command(
                cmd,
                cwd=self.project_path,
                allow_failure=True,
                timeout=120,  # pip-audit can be slow
            )

            if result:
                try:
                    audit_data = json.loads(result)

                    # Parse pip-audit output
                    dependencies = audit_data.get("dependencies", [])

                    for dep in dependencies:
                        package_name = dep.get("name", "unknown")
                        package_version = dep.get("version", "unknown")

                        for vuln in dep.get("vulns", []):
                            # Get severity (pip-audit doesn't always provide it)
                            # We'll infer from CVE data if available
                            vuln_id = vuln.get("id", "UNKNOWN")
                            description = vuln.get("description", "No description available")
                            fix_versions = vuln.get("fix_versions", [])
                            fixed_version = fix_versions[0] if fix_versions else None

                            # pip-audit doesn't provide severity, so we'll mark as HIGH by default
                            # This ensures blocking behavior
                            severity = "HIGH"

                            vulnerability = Vulnerability(
                                package=package_name,
                                version=package_version,
                                vulnerability_id=vuln_id,
                                severity=severity,
                                description=description,
                                fixed_version=fixed_version,
                            )
                            vulnerabilities.append(vulnerability)

                except json.JSONDecodeError as e:
                    error_msg = f"Failed to parse pip-audit output: {e}"
                    logger.warning(error_msg)
                    errors.append(error_msg)

        except Exception as e:
            error_msg = f"pip-audit execution failed: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

        return vulnerabilities, errors

    async def run_npm_audit(self) -> tuple[list[Vulnerability], list[str]]:
        """
        Run npm audit on JavaScript project.

        Returns:
            Tuple of (vulnerabilities, errors)
        """
        vulnerabilities: list[Vulnerability] = []
        errors: list[str] = []

        # Check if npm is available
        if not await self._command_exists("npm"):
            error_msg = "npm not installed"
            logger.warning(error_msg)
            errors.append(error_msg)
            return vulnerabilities, errors

        # Build npm audit command
        cmd = ["npm", "audit", "--json"]

        try:
            result = await self._run_command(
                cmd,
                cwd=self.project_path,
                allow_failure=True,
                timeout=120,
            )

            if result:
                try:
                    audit_data = json.loads(result)

                    # Parse npm audit output
                    vulns = audit_data.get("vulnerabilities", {})

                    for package_name, vuln_data in vulns.items():
                        severity = vuln_data.get("severity", "unknown").upper()

                        # Get via array for vulnerability details
                        via_list = vuln_data.get("via", [])

                        for via in via_list:
                            if isinstance(via, dict):  # Skip string references
                                vuln_id = via.get("url", "").split("/")[-1] or "UNKNOWN"
                                title = via.get("title", "No title available")
                                version_range = via.get("range", "unknown")

                                # Check if fix is available
                                fix_available = vuln_data.get("fixAvailable", False)
                                fixed_version = "available" if fix_available else None

                                vulnerability = Vulnerability(
                                    package=package_name,
                                    version=version_range,
                                    vulnerability_id=vuln_id,
                                    severity=severity,
                                    description=title,
                                    fixed_version=fixed_version,
                                )
                                vulnerabilities.append(vulnerability)

                except json.JSONDecodeError as e:
                    error_msg = f"Failed to parse npm audit output: {e}"
                    logger.warning(error_msg)
                    errors.append(error_msg)

        except Exception as e:
            error_msg = f"npm audit execution failed: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

        return vulnerabilities, errors

    async def scan(self, use_cache: bool = True) -> ScanResult:
        """
        Run security scan on project.

        Args:
            use_cache: If True, use cached results when available

        Returns:
            ScanResult with vulnerability details
        """
        start_time = time.time()

        # Detect project type
        project_type = self.detect_project_type()
        logger.info(f"Detected project type: {project_type}")

        if project_type == "unknown":
            logger.warning("Unknown project type, no security scan performed")
            return ScanResult(
                passed=True,
                project_type=project_type,
                scan_time=time.time() - start_time,
                errors=["Unknown project type - no pyproject.toml or package.json found"],
            )

        # Check cache
        cache_key = self._get_cache_key(project_type)
        if use_cache:
            cached_result = self._get_cached_result(cache_key)
            if cached_result:
                return cached_result

        # Run appropriate scanner
        vulnerabilities: list[Vulnerability] = []
        errors: list[str] = []

        if project_type == "python":
            logger.info("Running pip-audit...")
            py_vulns, py_errors = await self.run_pip_audit()
            vulnerabilities.extend(py_vulns)
            errors.extend(py_errors)
        elif project_type == "javascript":
            logger.info("Running npm audit...")
            js_vulns, js_errors = await self.run_npm_audit()
            vulnerabilities.extend(js_vulns)
            errors.extend(js_errors)

        # Count by severity
        critical_count = sum(1 for v in vulnerabilities if v.severity == "CRITICAL")
        high_count = sum(1 for v in vulnerabilities if v.severity == "HIGH")
        medium_count = sum(1 for v in vulnerabilities if v.severity == "MEDIUM")
        low_count = sum(1 for v in vulnerabilities if v.severity == "LOW")

        # Determine pass/fail: block on CRITICAL or HIGH
        passed = critical_count == 0 and high_count == 0 and len(errors) == 0

        scan_time = time.time() - start_time

        result = ScanResult(
            passed=passed,
            project_type=project_type,
            vulnerabilities=vulnerabilities,
            critical_count=critical_count,
            high_count=high_count,
            medium_count=medium_count,
            low_count=low_count,
            scan_time=scan_time,
            cached=False,
            errors=errors,
        )

        # Save to cache
        if use_cache:
            self._save_to_cache(cache_key, result)

        logger.info(
            f"Security scan {'PASSED' if passed else 'FAILED'}: "
            f"{critical_count} critical, {high_count} high, "
            f"{medium_count} medium, {low_count} low ({scan_time:.2f}s)"
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
            cwd = self.project_path

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


async def run_security_scan(
    project_path: Path | None = None,
    use_cache: bool = True,
) -> ScanResult:
    """
    Run security scan on project dependencies.

    Args:
        project_path: Project root (defaults to current directory)
        use_cache: If True, use cached results when available

    Returns:
        ScanResult with vulnerability details
    """
    gate = SecurityScanGate(project_path=project_path)
    return await gate.scan(use_cache=use_cache)


async def main_async() -> int:
    """Async main entry point."""
    parser = argparse.ArgumentParser(
        description="Security scan gate for project dependencies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan current directory
  python -m forge_harness.quality_gates.security_scan

  # Scan specific project
  python -m forge_harness.quality_gates.security_scan /path/to/project

  # Disable cache
  python -m forge_harness.quality_gates.security_scan --no-cache

  # Use in pre-commit hook
  python -m forge_harness.quality_gates.security_scan || exit 1
        """,
    )

    parser.add_argument(
        "project_path",
        nargs="?",
        type=Path,
        default=None,
        help="Project root directory (default: current directory)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable cache and run fresh scan",
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

    # Run security scan
    result = await run_security_scan(
        project_path=args.project_path,
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
