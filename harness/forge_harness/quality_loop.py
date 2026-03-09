"""
Quality Loop Harness - Automated Portfolio Quality Analysis
===========================================================

Implements automated weekly quality scans across all FORGE projects
using Technical Debt Analyzer and Tech Diligence integrations.

The loop:
1. Discovers all FORGE projects
2. Runs debt analysis (complexity, coverage, duplication, security, docs)
3. Runs security scans (Bandit, Semgrep)
4. Stores metrics for trend tracking
5. Alerts on quality degradation via approval queue
6. Generates portfolio health dashboard

Usage:
    from forge_harness.quality_loop import (
        QualityLoopHarness,
        QualityConfig,
        ProjectQualityReport,
        PortfolioQualityReport,
        create_quality_loop,
    )

    loop = create_quality_loop(
        forge_root=Path("/path/to/FORGE"),
        metrics_dir=Path("./quality_metrics"),
    )

    report = await loop.run_portfolio_scan()
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Quality Models
# =============================================================================


class QualityTrend(Enum):
    """Trend direction for quality metrics."""

    IMPROVING = "improving"
    STABLE = "stable"
    DEGRADING = "degrading"
    UNKNOWN = "unknown"


class SeverityLevel(Enum):
    """Severity levels for quality issues."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class DebtMetrics:
    """Technical debt metrics from debt analyzer."""

    complexity_score: float = 0.0
    duplication_percent: float = 0.0
    coverage_percent: float = 0.0
    documentation_percent: float = 0.0
    security_issues: int = 0
    total_debt_hours: float = 0.0
    velocity_impact_percent: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "complexity_score": self.complexity_score,
            "duplication_percent": self.duplication_percent,
            "coverage_percent": self.coverage_percent,
            "documentation_percent": self.documentation_percent,
            "security_issues": self.security_issues,
            "total_debt_hours": self.total_debt_hours,
            "velocity_impact_percent": self.velocity_impact_percent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DebtMetrics":
        """Create from dict."""
        return cls(
            complexity_score=data.get("complexity_score", 0.0),
            duplication_percent=data.get("duplication_percent", 0.0),
            coverage_percent=data.get("coverage_percent", 0.0),
            documentation_percent=data.get("documentation_percent", 0.0),
            security_issues=data.get("security_issues", 0),
            total_debt_hours=data.get("total_debt_hours", 0.0),
            velocity_impact_percent=data.get("velocity_impact_percent", 0.0),
        )


@dataclass
class SecurityFinding:
    """Security finding from diligence scan."""

    tool: str
    severity: SeverityLevel
    rule_id: str
    message: str
    file_path: str
    line_number: int | None = None
    confidence: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "tool": self.tool,
            "severity": self.severity.value,
            "rule_id": self.rule_id,
            "message": self.message,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SecurityFinding":
        """Create from dict."""
        return cls(
            tool=data["tool"],
            severity=SeverityLevel(data["severity"]),
            rule_id=data["rule_id"],
            message=data["message"],
            file_path=data["file_path"],
            line_number=data.get("line_number"),
            confidence=data.get("confidence", "medium"),
        )


@dataclass
class ProjectQualityReport:
    """Quality report for a single project."""

    project_name: str
    domain: str
    project_path: str
    scan_timestamp: datetime
    debt_metrics: DebtMetrics
    security_findings: list[SecurityFinding] = field(default_factory=list)
    quality_score: float = 0.0  # 0-100
    trend: QualityTrend = QualityTrend.UNKNOWN
    previous_score: float | None = None
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "project_name": self.project_name,
            "domain": self.domain,
            "project_path": self.project_path,
            "scan_timestamp": self.scan_timestamp.isoformat(),
            "debt_metrics": self.debt_metrics.to_dict(),
            "security_findings": [f.to_dict() for f in self.security_findings],
            "quality_score": self.quality_score,
            "trend": self.trend.value,
            "previous_score": self.previous_score,
            "issues": self.issues,
            "recommendations": self.recommendations,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectQualityReport":
        """Create from dict."""
        return cls(
            project_name=data["project_name"],
            domain=data["domain"],
            project_path=data["project_path"],
            scan_timestamp=datetime.fromisoformat(data["scan_timestamp"]),
            debt_metrics=DebtMetrics.from_dict(data["debt_metrics"]),
            security_findings=[
                SecurityFinding.from_dict(f) for f in data.get("security_findings", [])
            ],
            quality_score=data.get("quality_score", 0.0),
            trend=QualityTrend(data.get("trend", "unknown")),
            previous_score=data.get("previous_score"),
            issues=data.get("issues", []),
            recommendations=data.get("recommendations", []),
        )


@dataclass
class PortfolioQualityReport:
    """Quality report for entire portfolio."""

    scan_timestamp: datetime
    projects_scanned: int
    total_projects: int
    average_quality_score: float
    portfolio_trend: QualityTrend
    critical_issues: int
    high_issues: int
    projects: list[ProjectQualityReport] = field(default_factory=list)
    degraded_projects: list[str] = field(default_factory=list)
    improved_projects: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "scan_timestamp": self.scan_timestamp.isoformat(),
            "projects_scanned": self.projects_scanned,
            "total_projects": self.total_projects,
            "average_quality_score": self.average_quality_score,
            "portfolio_trend": self.portfolio_trend.value,
            "critical_issues": self.critical_issues,
            "high_issues": self.high_issues,
            "projects": [p.to_dict() for p in self.projects],
            "degraded_projects": self.degraded_projects,
            "improved_projects": self.improved_projects,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PortfolioQualityReport":
        """Create from dict."""
        return cls(
            scan_timestamp=datetime.fromisoformat(data["scan_timestamp"]),
            projects_scanned=data["projects_scanned"],
            total_projects=data["total_projects"],
            average_quality_score=data["average_quality_score"],
            portfolio_trend=QualityTrend(data["portfolio_trend"]),
            critical_issues=data["critical_issues"],
            high_issues=data["high_issues"],
            projects=[ProjectQualityReport.from_dict(p) for p in data.get("projects", [])],
            degraded_projects=data.get("degraded_projects", []),
            improved_projects=data.get("improved_projects", []),
        )


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class QualityConfig:
    """Configuration for quality loop execution.

    Attributes:
        min_coverage: Minimum acceptable test coverage (0-100)
        max_complexity: Maximum acceptable complexity score
        max_duplication: Maximum acceptable duplication percent
        security_block_severity: Block deployment if findings at this severity
        degradation_threshold: Score drop that triggers alert (points)
        tools: List of security scanning tools to use
        exclude_patterns: File patterns to exclude from scanning
        scan_backend_only: Only scan backend directories
    """

    min_coverage: float = 70.0
    max_complexity: float = 20.0
    max_duplication: float = 10.0
    security_block_severity: SeverityLevel = SeverityLevel.CRITICAL
    degradation_threshold: float = 5.0
    tools: list[str] = field(default_factory=lambda: ["bandit", "semgrep"])
    exclude_patterns: list[str] = field(
        default_factory=lambda: [
            "*node_modules*",
            "*.venv*",
            "*__pycache__*",
            "*.git*",
            "*dist*",
            "*build*",
            "*site-packages*",
            "*test*",  # Exclude test files from security scanning
            "*conftest*",
        ]
    )
    scan_backend_only: bool = True


# =============================================================================
# Quality Loop Harness
# =============================================================================


class QualityLoopHarness:
    """
    Automated quality scanning for FORGE portfolio.

    Capabilities:
    - Discover all FORGE projects
    - Run technical debt analysis
    - Run security scans
    - Track metrics over time
    - Alert on quality degradation
    - Generate reports
    """

    # FORGE domain directories
    FORGE_DOMAINS = [
        "brandfocus-ai",
        "calmconnect-io",
        "codeswiftr-com",
        "leanvibe-dev",
        "leanvibe-ai",
        "neoforge-dev",
        "thebrightharbor-com",
        "bogdanveliscu-com",
    ]

    def __init__(
        self,
        forge_root: Path,
        metrics_dir: Path,
        config: QualityConfig | None = None,
    ):
        """Initialize quality loop harness.

        Args:
            forge_root: Path to FORGE repository root
            metrics_dir: Directory to store quality metrics
            config: Quality configuration
        """
        self.forge_root = Path(forge_root)
        self.metrics_dir = Path(metrics_dir)
        self.config = config or QualityConfig()

        # Ensure metrics directory exists
        self.metrics_dir.mkdir(parents=True, exist_ok=True)

    async def discover_projects(self) -> list[dict[str, str]]:
        """Discover all FORGE projects.

        Returns:
            List of project info dicts with name, domain, path
        """
        projects = []

        # First, scan root-level infrastructure projects (harness, marketing-api, etc.)
        for item in self.forge_root.iterdir():
            if not item.is_dir():
                continue

            # Skip hidden directories and known non-project dirs
            if item.name.startswith(".") or item.name in [
                "docs",
                "scripts",
                "node_modules",
                ".git",
            ]:
                continue

            # Check if it's a root-level project (infrastructure)
            is_root_project = (
                (item / "forge_harness").exists()  # harness package
                or (item / "pyproject.toml").exists()
                and (item / "tests").exists()
                or (item / "CLAUDE.md").exists()
                and item.name in ["harness", "marketing-api", "forge-shared"]
            )

            if is_root_project:
                projects.append(
                    {
                        "name": item.name,
                        "domain": "infrastructure",  # Virtual domain for root projects
                        "path": str(item),
                    }
                )

        # Then, scan domain-based projects
        for domain in self.FORGE_DOMAINS:
            domain_path = self.forge_root / domain
            if not domain_path.exists():
                continue

            # Find all project directories (contain CLAUDE.md or backend/)
            for item in domain_path.iterdir():
                if not item.is_dir():
                    continue

                # Skip hidden directories
                if item.name.startswith("."):
                    continue

                # Check if it's a project (has backend, frontend, or CLAUDE.md)
                is_project = (
                    (item / "backend").exists()
                    or (item / "frontend").exists()
                    or (item / "CLAUDE.md").exists()
                    or (item / "pyproject.toml").exists()
                    or (item / "package.json").exists()
                )

                if is_project:
                    projects.append(
                        {
                            "name": item.name,
                            "domain": domain,
                            "path": str(item),
                        }
                    )

        logger.info(
            f"Discovered {len(projects)} projects across {len(self.FORGE_DOMAINS) + 1} domains (including infrastructure)"
        )
        return projects

    async def run_debt_analysis(self, project_path: Path) -> DebtMetrics:
        """Run technical debt analysis on a project.

        Args:
            project_path: Path to project directory

        Returns:
            DebtMetrics instance
        """
        metrics = DebtMetrics()

        # Find Python files for analysis - check common project structures
        scan_path = project_path
        for candidate in ["backend", "app/backend", "app", "src"]:
            candidate_path = project_path / candidate
            if candidate_path.exists() and candidate_path.is_dir():
                scan_path = candidate_path
                break

        python_files = list(scan_path.rglob("*.py"))
        python_files = [
            f for f in python_files if not any(ex in str(f) for ex in self.config.exclude_patterns)
        ]

        if not python_files:
            logger.warning(f"No Python files found in {project_path}")
            return metrics

        # Run radon for complexity analysis
        try:
            # Radon uses -e/--exclude for patterns (comma-separated)
            exclude_pattern = ",".join(f"*{p}*" for p in self.config.exclude_patterns)
            result = await self._run_command(
                ["radon", "cc", str(scan_path), "-a", "-j", "-e", exclude_pattern],
                cwd=scan_path,
            )
            if result:
                cc_data = json.loads(result)
                # Calculate average complexity
                total_complexity = 0
                count = 0
                for file_data in cc_data.values():
                    if isinstance(file_data, list):
                        for func in file_data:
                            if isinstance(func, dict) and "complexity" in func:
                                total_complexity += func["complexity"]
                                count += 1
                if count > 0:
                    metrics.complexity_score = total_complexity / count
        except Exception as e:
            logger.warning(f"Complexity analysis failed: {e}")

        # Run coverage check if coverage file exists
        coverage_file = scan_path / ".coverage"
        if coverage_file.exists():
            try:
                result = await self._run_command(
                    ["coverage", "json", "-o", "-"],
                    cwd=scan_path,
                )
                if result:
                    cov_data = json.loads(result)
                    metrics.coverage_percent = cov_data.get("totals", {}).get(
                        "percent_covered", 0.0
                    )
            except Exception as e:
                logger.warning(f"Coverage analysis failed: {e}")

        # Estimate documentation coverage
        total_functions = 0
        documented_functions = 0
        for py_file in python_files[:50]:  # Limit to first 50 files
            try:
                content = py_file.read_text()
                # Count function definitions
                import re

                functions = re.findall(r"^\s*def\s+\w+", content, re.MULTILINE)
                total_functions += len(functions)
                # Count docstrings after functions
                docstrings = re.findall(r'^\s*def\s+\w+[^:]*:\s*\n\s*"""', content, re.MULTILINE)
                documented_functions += len(docstrings)
            except Exception:
                pass

        if total_functions > 0:
            metrics.documentation_percent = (documented_functions / total_functions) * 100

        # Calculate overall debt score
        metrics.total_debt_hours = self._estimate_debt_hours(metrics)
        metrics.velocity_impact_percent = min(metrics.total_debt_hours / 100, 50)

        return metrics

    async def run_security_scan(self, project_path: Path) -> list[SecurityFinding]:
        """Run security scans on a project.

        Args:
            project_path: Path to project directory

        Returns:
            List of SecurityFinding instances
        """
        findings = []

        # Check common project structures
        scan_path = project_path
        for candidate in ["backend", "app/backend", "app", "src"]:
            candidate_path = project_path / candidate
            if candidate_path.exists() and candidate_path.is_dir():
                scan_path = candidate_path
                break

        # Run Bandit if available
        if "bandit" in self.config.tools:
            bandit_findings = await self._run_bandit(scan_path)
            findings.extend(bandit_findings)

        # Run Semgrep if available
        if "semgrep" in self.config.tools:
            semgrep_findings = await self._run_semgrep(scan_path)
            findings.extend(semgrep_findings)

        return findings

    async def _run_bandit(self, scan_path: Path) -> list[SecurityFinding]:
        """Run Bandit security scanner."""
        findings = []
        try:
            result = await self._run_command(
                [
                    "bandit",
                    "-r",
                    str(scan_path),
                    "-f",
                    "json",
                    "-q",
                    "--exclude",
                    ",".join(self.config.exclude_patterns),
                ],
                cwd=scan_path,
                allow_failure=True,
                timeout=120,  # Increase timeout for larger codebases
            )
            if result:
                data = json.loads(result)
                for issue in data.get("results", []):
                    severity_map = {
                        "HIGH": SeverityLevel.HIGH,
                        "MEDIUM": SeverityLevel.MEDIUM,
                        "LOW": SeverityLevel.LOW,
                    }
                    findings.append(
                        SecurityFinding(
                            tool="bandit",
                            severity=severity_map.get(
                                issue.get("severity", "LOW"), SeverityLevel.LOW
                            ),
                            rule_id=issue.get("test_id", "unknown"),
                            message=issue.get("issue_text", ""),
                            file_path=issue.get("filename", ""),
                            line_number=issue.get("line_number"),
                            confidence=issue.get("confidence", "MEDIUM").lower(),
                        )
                    )
        except Exception as e:
            logger.warning(f"Bandit scan failed: {e}")
        return findings

    async def _run_semgrep(self, scan_path: Path) -> list[SecurityFinding]:
        """Run Semgrep security scanner."""
        findings = []
        try:
            # Build exclude args for semgrep
            exclude_args = []
            for pattern in self.config.exclude_patterns:
                exclude_args.extend(["--exclude", pattern])

            result = await self._run_command(
                [
                    "semgrep",
                    "--config",
                    "auto",
                    "--json",
                    "-q",
                    *exclude_args,
                    str(scan_path),
                ],
                cwd=scan_path,
                allow_failure=True,
                timeout=120,
            )
            if result:
                data = json.loads(result)
                for match in data.get("results", []):
                    severity_str = match.get("extra", {}).get("severity", "WARNING")
                    severity_map = {
                        "ERROR": SeverityLevel.HIGH,
                        "WARNING": SeverityLevel.MEDIUM,
                        "INFO": SeverityLevel.LOW,
                    }
                    findings.append(
                        SecurityFinding(
                            tool="semgrep",
                            severity=severity_map.get(severity_str, SeverityLevel.MEDIUM),
                            rule_id=match.get("check_id", "unknown"),
                            message=match.get("extra", {}).get("message", ""),
                            file_path=match.get("path", ""),
                            line_number=match.get("start", {}).get("line"),
                            confidence="high",
                        )
                    )
        except Exception as e:
            logger.warning(f"Semgrep scan failed: {e}")
        return findings

    async def scan_project(self, project_info: dict[str, str]) -> ProjectQualityReport:
        """Scan a single project for quality metrics.

        Args:
            project_info: Dict with name, domain, path

        Returns:
            ProjectQualityReport instance
        """
        project_path = Path(project_info["path"])
        logger.info(f"Scanning project: {project_info['name']}")

        # Run analyses in parallel
        debt_task = self.run_debt_analysis(project_path)
        security_task = self.run_security_scan(project_path)

        debt_metrics, security_findings = await asyncio.gather(debt_task, security_task)

        # Calculate quality score (0-100)
        quality_score = self._calculate_quality_score(debt_metrics, security_findings)

        # Get previous score for trend
        previous_score = await self._get_previous_score(project_info["name"])
        trend = self._calculate_trend(quality_score, previous_score)

        # Generate issues and recommendations
        issues = self._identify_issues(debt_metrics, security_findings)
        recommendations = self._generate_recommendations(debt_metrics, security_findings)

        report = ProjectQualityReport(
            project_name=project_info["name"],
            domain=project_info["domain"],
            project_path=str(project_path),
            scan_timestamp=datetime.now(UTC),
            debt_metrics=debt_metrics,
            security_findings=security_findings,
            quality_score=quality_score,
            trend=trend,
            previous_score=previous_score,
            issues=issues,
            recommendations=recommendations,
        )

        # Store metrics for trending
        await self._store_metrics(report)

        return report

    async def run_portfolio_scan(
        self,
        projects: list[dict[str, str]] | None = None,
        max_concurrent: int = 4,
    ) -> PortfolioQualityReport:
        """Run quality scan on entire portfolio.

        Args:
            projects: Optional list of projects to scan (defaults to all)
            max_concurrent: Maximum concurrent scans

        Returns:
            PortfolioQualityReport instance
        """
        if projects is None:
            projects = await self.discover_projects()

        total_projects = len(projects)
        logger.info(f"Starting portfolio scan of {total_projects} projects")

        # Scan projects with concurrency limit
        semaphore = asyncio.Semaphore(max_concurrent)

        async def scan_with_limit(project_info: dict[str, str]) -> ProjectQualityReport | None:
            async with semaphore:
                try:
                    return await self.scan_project(project_info)
                except Exception as e:
                    logger.error(f"Failed to scan {project_info['name']}: {e}")
                    return None

        tasks = [scan_with_limit(p) for p in projects]
        results = await asyncio.gather(*tasks)

        # Filter out None results
        project_reports = [r for r in results if r is not None]

        # Calculate portfolio metrics
        avg_score = (
            sum(p.quality_score for p in project_reports) / len(project_reports)
            if project_reports
            else 0.0
        )

        critical_issues = sum(
            1
            for p in project_reports
            for f in p.security_findings
            if f.severity == SeverityLevel.CRITICAL
        )
        high_issues = sum(
            1
            for p in project_reports
            for f in p.security_findings
            if f.severity == SeverityLevel.HIGH
        )

        degraded = [p.project_name for p in project_reports if p.trend == QualityTrend.DEGRADING]
        improved = [p.project_name for p in project_reports if p.trend == QualityTrend.IMPROVING]

        # Determine portfolio trend
        if len(degraded) > len(improved):
            portfolio_trend = QualityTrend.DEGRADING
        elif len(improved) > len(degraded):
            portfolio_trend = QualityTrend.IMPROVING
        else:
            portfolio_trend = QualityTrend.STABLE

        report = PortfolioQualityReport(
            scan_timestamp=datetime.now(UTC),
            projects_scanned=len(project_reports),
            total_projects=total_projects,
            average_quality_score=avg_score,
            portfolio_trend=portfolio_trend,
            critical_issues=critical_issues,
            high_issues=high_issues,
            projects=project_reports,
            degraded_projects=degraded,
            improved_projects=improved,
        )

        # Store portfolio report
        await self._store_portfolio_report(report)

        # Alert on degradation
        if degraded:
            await self._alert_degradation(degraded, report)

        logger.info(
            f"Portfolio scan complete: {len(project_reports)}/{total_projects} projects, "
            f"avg score: {avg_score:.1f}, critical: {critical_issues}, high: {high_issues}"
        )

        return report

    def _calculate_quality_score(self, debt: DebtMetrics, findings: list[SecurityFinding]) -> float:
        """Calculate overall quality score (0-100)."""
        score = 100.0

        # Deduct for low coverage
        if debt.coverage_percent < self.config.min_coverage:
            score -= (self.config.min_coverage - debt.coverage_percent) * 0.5

        # Deduct for high complexity
        if debt.complexity_score > self.config.max_complexity:
            score -= (debt.complexity_score - self.config.max_complexity) * 2

        # Deduct for duplication
        if debt.duplication_percent > self.config.max_duplication:
            score -= (debt.duplication_percent - self.config.max_duplication) * 1.5

        # Deduct for security findings (capped to avoid excessive penalty)
        critical_count = sum(1 for f in findings if f.severity == SeverityLevel.CRITICAL)
        high_count = sum(1 for f in findings if f.severity == SeverityLevel.HIGH)
        medium_count = sum(1 for f in findings if f.severity == SeverityLevel.MEDIUM)
        low_count = sum(1 for f in findings if f.severity == SeverityLevel.LOW)

        # Critical and high are significant, deduct fully
        score -= critical_count * 20
        score -= high_count * 10
        # Medium/low are informational, cap the impact
        score -= min(medium_count * 2, 15)  # Cap at 15 points
        score -= min(low_count * 0.5, 10)  # Cap at 10 points

        return max(0.0, min(100.0, score))

    def _calculate_trend(self, current_score: float, previous_score: float | None) -> QualityTrend:
        """Calculate quality trend."""
        if previous_score is None:
            return QualityTrend.UNKNOWN

        diff = current_score - previous_score
        if diff > self.config.degradation_threshold:
            return QualityTrend.IMPROVING
        elif diff < -self.config.degradation_threshold:
            return QualityTrend.DEGRADING
        return QualityTrend.STABLE

    def _estimate_debt_hours(self, metrics: DebtMetrics) -> float:
        """Estimate hours to address technical debt."""
        hours = 0.0

        # Coverage gap
        if metrics.coverage_percent < self.config.min_coverage:
            gap = self.config.min_coverage - metrics.coverage_percent
            hours += gap * 0.5  # 0.5 hours per coverage point

        # Complexity
        if metrics.complexity_score > self.config.max_complexity:
            excess = metrics.complexity_score - self.config.max_complexity
            hours += excess * 2  # 2 hours per complexity point

        # Duplication
        if metrics.duplication_percent > self.config.max_duplication:
            excess = metrics.duplication_percent - self.config.max_duplication
            hours += excess * 1  # 1 hour per duplication point

        # Security issues
        hours += metrics.security_issues * 4  # 4 hours per security issue

        return hours

    def _identify_issues(self, debt: DebtMetrics, findings: list[SecurityFinding]) -> list[str]:
        """Identify issues from metrics."""
        issues = []

        if debt.coverage_percent < self.config.min_coverage:
            issues.append(
                f"Test coverage ({debt.coverage_percent:.1f}%) below minimum ({self.config.min_coverage}%)"
            )

        if debt.complexity_score > self.config.max_complexity:
            issues.append(
                f"Average complexity ({debt.complexity_score:.1f}) exceeds maximum ({self.config.max_complexity})"
            )

        if debt.duplication_percent > self.config.max_duplication:
            issues.append(
                f"Code duplication ({debt.duplication_percent:.1f}%) exceeds maximum ({self.config.max_duplication}%)"
            )

        critical = [f for f in findings if f.severity == SeverityLevel.CRITICAL]
        if critical:
            issues.append(f"{len(critical)} critical security issues found")

        high = [f for f in findings if f.severity == SeverityLevel.HIGH]
        if high:
            issues.append(f"{len(high)} high severity security issues found")

        return issues

    def _generate_recommendations(
        self, debt: DebtMetrics, findings: list[SecurityFinding]
    ) -> list[str]:
        """Generate recommendations based on metrics."""
        recommendations = []

        if debt.coverage_percent < 50:
            recommendations.append(
                "Priority: Add tests for critical code paths to reach 50% coverage"
            )
        elif debt.coverage_percent < self.config.min_coverage:
            recommendations.append(
                f"Add tests to reach {self.config.min_coverage}% coverage target"
            )

        if debt.complexity_score > 25:
            recommendations.append(
                "Refactor complex functions (>25 cyclomatic complexity) into smaller units"
            )

        if debt.documentation_percent < 50:
            recommendations.append("Add docstrings to public functions and classes")

        critical = [f for f in findings if f.severity == SeverityLevel.CRITICAL]
        if critical:
            recommendations.append(
                f"URGENT: Address {len(critical)} critical security issues before deployment"
            )

        return recommendations

    async def _get_previous_score(self, project_name: str) -> float | None:
        """Get previous quality score for project."""
        history_file = self.metrics_dir / f"{project_name}_history.json"
        if not history_file.exists():
            return None

        try:
            data = json.loads(history_file.read_text())
            if data:
                return data[-1].get("quality_score")
        except Exception:
            pass
        return None

    async def _store_metrics(self, report: ProjectQualityReport) -> None:
        """Store project metrics for trending."""
        history_file = self.metrics_dir / f"{report.project_name}_history.json"

        history = []
        if history_file.exists():
            try:
                history = json.loads(history_file.read_text())
            except Exception:
                pass

        # Add new entry
        history.append(
            {
                "timestamp": report.scan_timestamp.isoformat(),
                "quality_score": report.quality_score,
                "coverage_percent": report.debt_metrics.coverage_percent,
                "complexity_score": report.debt_metrics.complexity_score,
                "security_issues": len(report.security_findings),
            }
        )

        # Keep last 52 entries (1 year of weekly scans)
        history = history[-52:]

        history_file.write_text(json.dumps(history, indent=2))

    async def _store_portfolio_report(self, report: PortfolioQualityReport) -> None:
        """Store portfolio report."""
        report_file = (
            self.metrics_dir / f"portfolio_{report.scan_timestamp.strftime('%Y%m%d_%H%M%S')}.json"
        )
        report_file.write_text(json.dumps(report.to_dict(), indent=2))

        # Also update latest symlink
        latest_file = self.metrics_dir / "portfolio_latest.json"
        latest_file.write_text(json.dumps(report.to_dict(), indent=2))

    async def _alert_degradation(
        self, degraded_projects: list[str], report: PortfolioQualityReport
    ) -> None:
        """Alert on quality degradation via approval queue."""
        try:
            from .approval_queue import (
                ApprovalPriority,
                ApprovalType,
                create_approval_queue_from_env,
            )

            queue = create_approval_queue_from_env()
            await queue.create_request(
                type=ApprovalType.QUALITY_GATE,
                title=f"Quality degradation detected in {len(degraded_projects)} projects",
                description=(
                    "The following projects show declining quality scores:\n"
                    + "\n".join(f"- {p}" for p in degraded_projects)
                    + f"\n\nPortfolio average: {report.average_quality_score:.1f}"
                ),
                priority=ApprovalPriority.HIGH
                if len(degraded_projects) > 3
                else ApprovalPriority.MEDIUM,
                metadata={
                    "degraded_projects": degraded_projects,
                    "average_score": report.average_quality_score,
                    "critical_issues": report.critical_issues,
                    "high_issues": report.high_issues,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to create approval request: {e}")

    async def _run_command(
        self,
        cmd: list[str],
        cwd: Path,
        timeout: int = 60,
        allow_failure: bool = False,
    ) -> str | None:
        """Run a command asynchronously."""
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )

            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)

            if process.returncode != 0 and not allow_failure:
                logger.warning(f"Command failed: {' '.join(cmd)}: {stderr.decode()}")
                return None

            return stdout.decode()

        except TimeoutError:
            logger.warning(f"Command timed out: {' '.join(cmd)}")
            return None
        except FileNotFoundError:
            logger.warning(f"Command not found: {cmd[0]}")
            return None
        except Exception as e:
            logger.warning(f"Command error: {e}")
            return None

    def generate_markdown_report(self, report: PortfolioQualityReport) -> str:
        """Generate markdown report from portfolio scan."""
        lines = [
            "# FORGE Portfolio Quality Report",
            "",
            f"**Scan Date:** {report.scan_timestamp.strftime('%Y-%m-%d %H:%M UTC')}",
            f"**Projects Scanned:** {report.projects_scanned}/{report.total_projects}",
            f"**Average Quality Score:** {report.average_quality_score:.1f}/100",
            f"**Portfolio Trend:** {report.portfolio_trend.value}",
            "",
            "## Summary",
            "",
            f"- Critical Issues: {report.critical_issues}",
            f"- High Issues: {report.high_issues}",
            f"- Degraded Projects: {len(report.degraded_projects)}",
            f"- Improved Projects: {len(report.improved_projects)}",
            "",
        ]

        if report.degraded_projects:
            lines.extend(
                [
                    "## Degraded Projects",
                    "",
                ]
                + [f"- {p}" for p in report.degraded_projects]
                + [""]
            )

        if report.improved_projects:
            lines.extend(
                [
                    "## Improved Projects",
                    "",
                ]
                + [f"- {p}" for p in report.improved_projects]
                + [""]
            )

        lines.extend(
            [
                "## Project Details",
                "",
                "| Project | Domain | Score | Trend | Coverage | Complexity | Security |",
                "|---------|--------|-------|-------|----------|------------|----------|",
            ]
        )

        for p in sorted(report.projects, key=lambda x: x.quality_score):
            trend_icon = {
                QualityTrend.IMPROVING: "\u2191",
                QualityTrend.DEGRADING: "\u2193",
                QualityTrend.STABLE: "\u2192",
                QualityTrend.UNKNOWN: "?",
            }[p.trend]

            lines.append(
                f"| {p.project_name} | {p.domain} | {p.quality_score:.0f} | {trend_icon} | "
                f"{p.debt_metrics.coverage_percent:.0f}% | {p.debt_metrics.complexity_score:.1f} | "
                f"{len(p.security_findings)} |"
            )

        return "\n".join(lines)

    # =========================================================================
    # Scheduled Scanning Methods
    # =========================================================================

    async def store_baseline(self, baseline: dict) -> None:
        """Store baseline metrics for comparison.

        Args:
            baseline: Dictionary of baseline metrics (e.g., average_score, critical_issues)
        """
        import json

        baseline_path = self.metrics_dir / "baseline.json"
        baseline_path.write_text(json.dumps(baseline, indent=2))

    async def get_baseline(self) -> dict | None:
        """Get stored baseline metrics.

        Returns:
            Baseline metrics dict or None if no baseline exists
        """
        import json

        baseline_path = self.metrics_dir / "baseline.json"
        if not baseline_path.exists():
            return None
        return json.loads(baseline_path.read_text())

    def compare_to_baseline(self, current: dict, baseline: dict) -> dict:
        """Compare current metrics to baseline and detect changes.

        Args:
            current: Current metrics dictionary
            baseline: Baseline metrics dictionary

        Returns:
            Comparison result with degradation and improvement flags
        """
        result = {
            "degraded": False,
            "improved": False,
            "score_delta": 0.0,
            "critical_delta": 0,
            "details": [],
        }

        # Compare average score
        current_score = current.get("average_score", 0.0)
        baseline_score = baseline.get("average_score", 0.0)
        result["score_delta"] = current_score - baseline_score

        # Compare critical issues
        current_critical = current.get("critical_issues", 0)
        baseline_critical = baseline.get("critical_issues", 0)
        result["critical_delta"] = current_critical - baseline_critical

        # Detect degradation (score dropped by >5% or critical issues increased)
        if result["score_delta"] < -5.0:
            result["degraded"] = True
            result["details"].append(
                f"Quality score degraded: {baseline_score:.1f} -> {current_score:.1f}"
            )

        if result["critical_delta"] > 0:
            result["degraded"] = True
            result["details"].append(
                f"Critical issues increased: {baseline_critical} -> {current_critical}"
            )

        # Detect improvement (score improved by >5% and no new critical issues)
        if result["score_delta"] > 5.0 and result["critical_delta"] <= 0:
            result["improved"] = True
            result["details"].append(
                f"Quality score improved: {baseline_score:.1f} -> {current_score:.1f}"
            )

        return result

    async def run_scheduled_scan(
        self,
        interval_hours: int = 168,
        max_runs: int | None = None,
    ) -> None:
        """Run quality scans on a schedule.

        Args:
            interval_hours: Hours between scans (default 168 = weekly)
            max_runs: Maximum number of scan runs (None for infinite)
        """
        import asyncio

        runs = 0
        interval_seconds = interval_hours * 3600

        while max_runs is None or runs < max_runs:
            logger.info("Starting scheduled quality scan (run %d)", runs + 1)

            # Run portfolio scan
            report = await self.run_portfolio_scan()

            # Extract summary metrics
            current_metrics = {
                "average_score": report.average_quality_score,
                "critical_issues": report.critical_issues,
                "high_issues": report.high_issues,
                "projects_scanned": report.projects_scanned,
                "scan_timestamp": report.scan_timestamp.isoformat(),
            }

            # Get or store baseline
            baseline = await self.get_baseline()
            if baseline is None:
                # First run - store as baseline
                logger.info("First scheduled scan - storing as baseline")
                await self.store_baseline(current_metrics)
            else:
                # Compare to baseline
                comparison = self.compare_to_baseline(current_metrics, baseline)

                if comparison["degraded"]:
                    logger.warning(
                        "Quality degradation detected: %s",
                        "; ".join(comparison["details"]),
                    )
                    # Alert via existing degradation mechanism
                    self._alert_degradation(
                        f"Scheduled scan detected degradation: {'; '.join(comparison['details'])}"
                    )
                elif comparison["improved"]:
                    logger.info(
                        "Quality improvement detected: %s",
                        "; ".join(comparison["details"]),
                    )

            runs += 1

            # Sleep until next scan (if not at max_runs)
            if max_runs is None or runs < max_runs:
                logger.info("Next scan in %d hours", interval_hours)
                await asyncio.sleep(interval_seconds)


# =============================================================================
# Factory Functions
# =============================================================================


def create_quality_loop(
    forge_root: Path | str | None = None,
    metrics_dir: Path | str | None = None,
    config: QualityConfig | None = None,
) -> QualityLoopHarness:
    """Create a quality loop harness.

    Args:
        forge_root: Path to FORGE repository root
        metrics_dir: Directory to store metrics
        config: Quality configuration

    Returns:
        QualityLoopHarness instance
    """
    if forge_root is None:
        # Try to find FORGE root from current directory
        forge_root = Path.cwd()
        while forge_root != forge_root.parent:
            if (forge_root / "CLAUDE.md").exists() and (forge_root / "harness").exists():
                break
            forge_root = forge_root.parent
        else:
            raise ValueError("Could not find FORGE root directory")

    if metrics_dir is None:
        metrics_dir = Path(forge_root) / "quality_metrics"

    return QualityLoopHarness(
        forge_root=Path(forge_root),
        metrics_dir=Path(metrics_dir),
        config=config,
    )


__all__ = [
    "QualityLoopHarness",
    "QualityConfig",
    "QualityTrend",
    "SeverityLevel",
    "DebtMetrics",
    "SecurityFinding",
    "ProjectQualityReport",
    "PortfolioQualityReport",
    "create_quality_loop",
]
