"""Security scanning quality gate using bandit."""

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from pathlib import Path

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class Severity(IntEnum):
    """Severity levels with proper ordering for comparison."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3

    def __str__(self) -> str:
        return self.name


@dataclass
class Vulnerability:
    file: str
    line: int
    code: str
    severity: Severity
    message: str
    cwe_id: str | None = None


@dataclass
class SecurityResult:
    success: bool
    vulnerabilities: list[Vulnerability] = field(default_factory=list)
    severity_counts: dict[str, int] = field(default_factory=dict)
    duration: float = 0.0


@dataclass
class SecurityScannerConfig:
    severity_threshold: Severity = Severity.MEDIUM
    ignore_patterns: list[str] = field(default_factory=list)
    skip_rules: list[str] = field(default_factory=list)
    timeout: int = 300


class SecurityScanner:
    def __init__(self, config: SecurityScannerConfig | None = None):
        self.config = config or SecurityScannerConfig()

    async def scan_files(self, paths: list[Path]) -> SecurityResult:
        start = datetime.now()
        cmd = ["bandit", "-f", "json", "-r"] + [str(p) for p in paths]
        try:
            result = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                ),
                timeout=self.config.timeout,
            )
            stdout, _ = await result.communicate()
            return self._parse_output(stdout.decode(), start)
        except TimeoutError:
            return SecurityResult(success=False, duration=self.config.timeout)

    def _parse_output(self, output: str, start: datetime) -> SecurityResult:
        vulns = []
        severity_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
        severity_map = {"LOW": Severity.LOW, "MEDIUM": Severity.MEDIUM, "HIGH": Severity.HIGH}
        try:
            data = json.loads(output)
            for issue in data.get("results", []):
                sev_str = issue.get("issue_severity", "LOW")
                sev = severity_map.get(sev_str, Severity.LOW)
                severity_counts[sev_str] += 1
                vulns.append(
                    Vulnerability(
                        file=issue.get("filename", ""),
                        line=issue.get("line_number", 0),
                        code=issue.get("test_id", ""),
                        severity=sev,
                        message=issue.get("issue_text", ""),
                        cwe_id=issue.get("cwe", {}).get("id"),
                    )
                )
        except json.JSONDecodeError:
            pass
        threshold = self.config.severity_threshold
        has_critical = any(v.severity >= threshold for v in vulns)
        return SecurityResult(
            success=not has_critical,
            vulnerabilities=vulns,
            severity_counts=severity_counts,
            duration=(datetime.now() - start).total_seconds(),
        )

    async def scan_staged(self) -> SecurityResult:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            capture_output=True,
            text=True,
        )
        py_files = [Path(f) for f in result.stdout.strip().split("\n") if f.endswith(".py")]
        if not py_files:
            return SecurityResult(success=True, duration=0.0)
        return await self.scan_files(py_files)


def create_security_scanner(config: SecurityScannerConfig | None = None) -> SecurityScanner:
    return SecurityScanner(config)
