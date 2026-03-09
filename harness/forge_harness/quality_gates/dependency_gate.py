"""Dependency audit quality gate for FORGE Harness.

Checks for security vulnerabilities in project dependencies.
"""

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class VulnerabilityInfo:
    """Information about a vulnerability."""

    id: str
    package: str
    version: str
    severity: str  # low, medium, high, critical
    description: str
    fix_version: str | None = None


@dataclass
class AuditResult:
    """Result of dependency audit."""

    passed: bool
    vulnerabilities: list[VulnerabilityInfo] = field(default_factory=list)
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    error: str | None = None
    audit_type: str = ""  # "pip" or "npm"
    cached: bool = False


@dataclass
class DependencyGateConfig:
    """Configuration for dependency auditing."""

    fail_on_critical: bool = True
    fail_on_high: bool = True
    fail_on_medium: bool = False
    cache_duration_hours: int = 24
    timeout_seconds: int = 120


class DependencyGate:
    """Quality gate for dependency security audits."""

    def __init__(self, config: DependencyGateConfig | None = None, cwd: Path | None = None):
        self.config = config or DependencyGateConfig()
        self.cwd = cwd or Path.cwd()
        self._cache_dir = self.cwd / ".forge_cache"

    def _get_cache_key(self, audit_type: str, lock_file: Path) -> str:
        """Generate cache key based on lock file hash."""
        if lock_file.exists():
            content = lock_file.read_bytes()
            return hashlib.md5(content).hexdigest()
        return ""

    def _get_cached_result(self, cache_key: str, audit_type: str) -> AuditResult | None:
        """Get cached audit result if valid."""
        cache_file = self._cache_dir / f"audit_{audit_type}_{cache_key}.json"
        if not cache_file.exists():
            return None

        try:
            data = json.loads(cache_file.read_text())
            cached_at = datetime.fromisoformat(data["cached_at"])
            if datetime.now() - cached_at > timedelta(hours=self.config.cache_duration_hours):
                return None

            result = AuditResult(
                passed=data["passed"],
                critical_count=data.get("critical_count", 0),
                high_count=data.get("high_count", 0),
                medium_count=data.get("medium_count", 0),
                low_count=data.get("low_count", 0),
                audit_type=audit_type,
                cached=True,
            )
            result.vulnerabilities = [
                VulnerabilityInfo(**v) for v in data.get("vulnerabilities", [])
            ]
            return result
        except Exception as e:
            logger.debug(f"Cache read failed: {e}")
            return None

    def _save_cache(self, cache_key: str, result: AuditResult) -> None:
        """Save audit result to cache."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self._cache_dir / f"audit_{result.audit_type}_{cache_key}.json"

        data = {
            "cached_at": datetime.now().isoformat(),
            "passed": result.passed,
            "critical_count": result.critical_count,
            "high_count": result.high_count,
            "medium_count": result.medium_count,
            "low_count": result.low_count,
            "vulnerabilities": [
                {
                    "id": v.id,
                    "package": v.package,
                    "version": v.version,
                    "severity": v.severity,
                    "description": v.description,
                    "fix_version": v.fix_version,
                }
                for v in result.vulnerabilities
            ],
        }
        cache_file.write_text(json.dumps(data, indent=2))

    def audit_pip(self) -> AuditResult:
        """Audit Python dependencies using pip-audit."""
        lock_file = self.cwd / "uv.lock"
        if not lock_file.exists():
            lock_file = self.cwd / "requirements.txt"

        cache_key = self._get_cache_key("pip", lock_file)
        if cache_key:
            cached = self._get_cached_result(cache_key, "pip")
            if cached:
                return cached

        try:
            result = subprocess.run(
                ["uv", "run", "pip-audit", "--format", "json"],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
            )

            vulnerabilities = []
            counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}

            if result.stdout:
                try:
                    data = json.loads(result.stdout)
                    for vuln in data:
                        severity = vuln.get("vulnerability", {}).get("severity", "unknown").lower()
                        if severity in counts:
                            counts[severity] += 1

                        vulnerabilities.append(
                            VulnerabilityInfo(
                                id=vuln.get("vulnerability", {}).get("id", "unknown"),
                                package=vuln.get("name", "unknown"),
                                version=vuln.get("version", "unknown"),
                                severity=severity,
                                description=vuln.get("vulnerability", {}).get("description", ""),
                                fix_version=vuln.get("fix_versions", [None])[0]
                                if vuln.get("fix_versions")
                                else None,
                            )
                        )
                except json.JSONDecodeError:
                    pass

            passed = self._check_passed(counts)

            audit_result = AuditResult(
                passed=passed,
                vulnerabilities=vulnerabilities,
                critical_count=counts["critical"],
                high_count=counts["high"],
                medium_count=counts["medium"],
                low_count=counts["low"],
                audit_type="pip",
            )

            if cache_key:
                self._save_cache(cache_key, audit_result)

            return audit_result

        except FileNotFoundError:
            return AuditResult(
                passed=True,
                error="pip-audit not installed",
                audit_type="pip",
            )
        except subprocess.TimeoutExpired:
            return AuditResult(
                passed=False,
                error="Audit timed out",
                audit_type="pip",
            )
        except Exception as e:
            return AuditResult(
                passed=False,
                error=str(e),
                audit_type="pip",
            )

    def audit_npm(self) -> AuditResult:
        """Audit JavaScript dependencies using npm audit."""
        package_lock = self.cwd / "package-lock.json"
        if not package_lock.exists():
            return AuditResult(
                passed=True,
                error="No package-lock.json found",
                audit_type="npm",
            )

        cache_key = self._get_cache_key("npm", package_lock)
        if cache_key:
            cached = self._get_cached_result(cache_key, "npm")
            if cached:
                return cached

        try:
            result = subprocess.run(
                ["npm", "audit", "--json"],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
            )

            vulnerabilities = []
            counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}

            if result.stdout:
                try:
                    data = json.loads(result.stdout)
                    for adv_id, adv in data.get("advisories", {}).items():
                        severity = adv.get("severity", "unknown").lower()
                        if severity in counts:
                            counts[severity] += 1

                        vulnerabilities.append(
                            VulnerabilityInfo(
                                id=str(adv_id),
                                package=adv.get("module_name", "unknown"),
                                version=adv.get("findings", [{}])[0].get("version", "unknown"),
                                severity=severity,
                                description=adv.get("title", ""),
                                fix_version=adv.get("patched_versions", None),
                            )
                        )
                except json.JSONDecodeError:
                    pass

            passed = self._check_passed(counts)

            audit_result = AuditResult(
                passed=passed,
                vulnerabilities=vulnerabilities,
                critical_count=counts["critical"],
                high_count=counts["high"],
                medium_count=counts["medium"],
                low_count=counts["low"],
                audit_type="npm",
            )

            if cache_key:
                self._save_cache(cache_key, audit_result)

            return audit_result

        except FileNotFoundError:
            return AuditResult(
                passed=True,
                error="npm not installed",
                audit_type="npm",
            )
        except subprocess.TimeoutExpired:
            return AuditResult(
                passed=False,
                error="Audit timed out",
                audit_type="npm",
            )
        except Exception as e:
            return AuditResult(
                passed=False,
                error=str(e),
                audit_type="npm",
            )

    def _check_passed(self, counts: dict[str, int]) -> bool:
        """Check if audit passed based on configuration."""
        if self.config.fail_on_critical and counts["critical"] > 0:
            return False
        if self.config.fail_on_high and counts["high"] > 0:
            return False
        if self.config.fail_on_medium and counts["medium"] > 0:
            return False
        return True

    def run(self) -> list[AuditResult]:
        """Run all applicable audits."""
        results = []

        # Check for Python project
        if (self.cwd / "pyproject.toml").exists() or (self.cwd / "requirements.txt").exists():
            results.append(self.audit_pip())

        # Check for JavaScript project
        if (self.cwd / "package.json").exists():
            results.append(self.audit_npm())

        if not results:
            results.append(
                AuditResult(
                    passed=True,
                    error="No dependencies found to audit",
                    audit_type="none",
                )
            )

        return results


def create_dependency_gate(
    fail_on_critical: bool = True,
    fail_on_high: bool = True,
    fail_on_medium: bool = False,
    cwd: Path | None = None,
) -> DependencyGate:
    """Factory function to create a DependencyGate."""
    config = DependencyGateConfig(
        fail_on_critical=fail_on_critical,
        fail_on_high=fail_on_high,
        fail_on_medium=fail_on_medium,
    )
    return DependencyGate(config=config, cwd=cwd)
