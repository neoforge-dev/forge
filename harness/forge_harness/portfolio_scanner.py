"""
Portfolio Scanner - Autonomous Health Checks for FORGE Projects.

Scans all FORGE projects using Tech Diligence and generates prioritized
features.json entries for automated remediation.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from forge_harness.meta_learning.bridges.tech_diligence import TechDiligenceBridge

logger = logging.getLogger(__name__)


# FORGE portfolio project registry
FORGE_PROJECTS = [
    # Tier 1: Revenue Focus
    {"domain": "codeswiftr-com", "project": "interview-simulator", "tier": 1},
    {"domain": "codeswiftr-com", "project": "code-atlas", "tier": 1},
    {"domain": "leanvibe-dev", "project": "tech-diligence-snapshot", "tier": 1},
    {"domain": "leanvibe-dev", "project": "technical-debt-analyzer", "tier": 1},
    # Tier 2: Growth
    {"domain": "neoforge-dev", "project": "mindflow", "tier": 2},
    {"domain": "neoforge-dev", "project": "discovergpt", "tier": 2},
    {"domain": "neoforge-dev", "project": "sedma", "tier": 2},
    {"domain": "brandfocus-ai", "project": "pkm-ai", "tier": 2},
    {"domain": "brandfocus-ai", "project": "voice-coach", "tier": 2},
    # Tier 3: Consumer
    {"domain": "thebrightharbor-com", "project": "study-flow", "tier": 3},
    {"domain": "thebrightharbor-com", "project": "story-grow", "tier": 3},
    {"domain": "thebrightharbor-com", "project": "code-ship", "tier": 3},
    {"domain": "thebrightharbor-com", "project": "kiddo-rewards", "tier": 3},
    {"domain": "bogdanveliscu-com", "project": "septica", "tier": 3},
    # Tier 4: Development
    {"domain": "calmconnect-io", "project": "calm-connect-api", "tier": 4},
    {"domain": "babybit-es", "project": "babybites", "tier": 4},
    {"domain": "leanvibe-ai", "project": "orchestrator-cli", "tier": 4},
    {"domain": "codeswiftr-com", "project": "strategic-tech-newsletter", "tier": 4},
    # Meta
    {"domain": "forge-harness", "project": "harness", "tier": 0},
]


@dataclass
class ProjectHealth:
    """Health metrics for a single project."""

    domain: str
    project: str
    tier: int
    security_score: float = 100.0
    quality_score: float = 100.0
    debt_hours: float = 0.0
    coverage_pct: float = 0.0
    blocking_issues: int = 0
    warnings: int = 0
    risk_level: str = "low"
    last_scanned: datetime | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "domain": self.domain,
            "project": self.project,
            "tier": self.tier,
            "security_score": self.security_score,
            "quality_score": self.quality_score,
            "debt_hours": self.debt_hours,
            "coverage_pct": self.coverage_pct,
            "blocking_issues": self.blocking_issues,
            "warnings": self.warnings,
            "risk_level": self.risk_level,
            "last_scanned": self.last_scanned.isoformat() if self.last_scanned else None,
            "error": self.error,
        }


@dataclass
class PortfolioHealthReport:
    """Aggregated health report for the entire portfolio."""

    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    projects: list[ProjectHealth] = field(default_factory=list)
    total_projects: int = 0
    healthy_projects: int = 0
    at_risk_projects: int = 0
    critical_projects: int = 0
    aggregate_security_score: float = 100.0
    aggregate_quality_score: float = 100.0
    total_debt_hours: float = 0.0
    total_blocking_issues: int = 0
    total_warnings: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "total_projects": self.total_projects,
            "healthy_projects": self.healthy_projects,
            "at_risk_projects": self.at_risk_projects,
            "critical_projects": self.critical_projects,
            "aggregate_security_score": self.aggregate_security_score,
            "aggregate_quality_score": self.aggregate_quality_score,
            "total_debt_hours": self.total_debt_hours,
            "total_blocking_issues": self.total_blocking_issues,
            "total_warnings": self.total_warnings,
            "projects": [p.to_dict() for p in self.projects],
        }


@dataclass
class ScannerConfig:
    """Configuration for the portfolio scanner."""

    concurrency: int = 3
    timeout_seconds: int = 60
    history_dir: str = ".forge/learning/portfolio_health"
    filter_domains: list[str] | None = None
    filter_tiers: list[int] | None = None
    min_severity_for_feature: str = "medium"


@dataclass
class FeatureSpec:
    """A feature specification for features.json."""

    id: str
    name: str
    description: str
    priority: str = "medium"
    estimated_hours: float = 4.0
    source_project: str | None = None
    source_finding: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": "pending",
            "priority": self.priority,
            "estimated_hours": self.estimated_hours,
            "acceptance_criteria": [
                f"Issue resolved in {self.source_project}",
                "Tests pass after fix",
            ],
            "metadata": {
                "source": "portfolio_scanner",
                "source_project": self.source_project,
                "source_finding": self.source_finding,
            },
        }


class PortfolioScanner:
    """
    Scans the FORGE portfolio for health issues and generates fix features.

    Usage:
        scanner = PortfolioScanner(tech_diligence_bridge, config)

        # Scan the entire portfolio
        report = await scanner.scan_portfolio()

        # Get prioritized fixes
        features = scanner.prioritize_fixes(report)

        # Export to features.json
        scanner.export_features_json(features, Path("fixes.json"))
    """

    def __init__(
        self,
        tech_diligence: Optional["TechDiligenceBridge"] = None,
        config: ScannerConfig | None = None,
    ):
        """Initialize the portfolio scanner.

        Args:
            tech_diligence: TechDiligence bridge for analysis
            config: Scanner configuration
        """
        self.tech_diligence = tech_diligence
        self.config = config or ScannerConfig()
        self._semaphore: asyncio.Semaphore | None = None

    async def scan_portfolio(
        self,
        projects: list[dict[str, Any]] | None = None,
    ) -> PortfolioHealthReport:
        """Scan all projects and generate a health report.

        Args:
            projects: Optional list of projects to scan (defaults to FORGE_PROJECTS)

        Returns:
            PortfolioHealthReport with per-project and aggregate metrics
        """
        projects = projects or FORGE_PROJECTS

        # Apply filters
        if self.config.filter_domains:
            projects = [p for p in projects if p["domain"] in self.config.filter_domains]
        if self.config.filter_tiers:
            projects = [p for p in projects if p["tier"] in self.config.filter_tiers]

        logger.info(f"Scanning {len(projects)} projects with concurrency {self.config.concurrency}")

        # Create semaphore for concurrency control
        self._semaphore = asyncio.Semaphore(self.config.concurrency)

        # Scan all projects in parallel (with concurrency limit)
        tasks = [self._scan_project(p) for p in projects]
        project_healths = await asyncio.gather(*tasks)

        # Build report
        report = self._build_report(project_healths)

        # Save to history
        await self._save_to_history(report)

        return report

    async def _scan_project(self, project: dict[str, Any]) -> ProjectHealth:
        """Scan a single project.

        Args:
            project: Project dict with domain, project, tier

        Returns:
            ProjectHealth with metrics
        """
        async with self._semaphore:
            domain = project["domain"]
            proj_name = project["project"]
            tier = project["tier"]

            health = ProjectHealth(
                domain=domain,
                project=proj_name,
                tier=tier,
                last_scanned=datetime.now(UTC),
            )

            if not self.tech_diligence:
                health.error = "TechDiligence bridge not available"
                return health

            try:
                signals = await self.tech_diligence.get_signals(
                    domain=domain,
                    project=proj_name,
                )

                # Map signals to health metrics
                health.security_score = signals.overall_score
                health.quality_score = signals.overall_score  # Can be refined
                health.blocking_issues = len(signals.blocking_issues)
                health.warnings = len(signals.warnings)
                health.risk_level = signals.risk_level

                # Estimate debt hours from findings
                for finding in signals.blocking_issues:
                    health.debt_hours += self._estimate_effort(finding.severity.value)
                for warning in signals.warnings:
                    health.debt_hours += self._estimate_effort(warning.severity.value) * 0.5

                logger.debug(
                    f"Scanned {domain}/{proj_name}: score={health.security_score}, issues={health.blocking_issues}"
                )

            except Exception as e:
                logger.warning(f"Error scanning {domain}/{proj_name}: {e}")
                health.error = str(e)

            return health

    def _estimate_effort(self, severity: str) -> float:
        """Estimate fix effort in hours based on severity."""
        estimates = {
            "critical": 8.0,
            "high": 4.0,
            "medium": 2.0,
            "low": 1.0,
            "info": 0.5,
        }
        return estimates.get(severity.lower(), 2.0)

    def _build_report(self, project_healths: list[ProjectHealth]) -> PortfolioHealthReport:
        """Build aggregate report from project healths."""
        report = PortfolioHealthReport(
            projects=project_healths,
            total_projects=len(project_healths),
        )

        # Calculate aggregates
        scores = []
        for health in project_healths:
            if not health.error:
                scores.append(health.security_score)
                report.total_debt_hours += health.debt_hours
                report.total_blocking_issues += health.blocking_issues
                report.total_warnings += health.warnings

                if health.risk_level == "critical":
                    report.critical_projects += 1
                elif health.risk_level in ("high", "medium"):
                    report.at_risk_projects += 1
                else:
                    report.healthy_projects += 1

        if scores:
            report.aggregate_security_score = sum(scores) / len(scores)
            report.aggregate_quality_score = report.aggregate_security_score

        return report

    def prioritize_fixes(
        self,
        report: PortfolioHealthReport,
        max_features: int = 20,
    ) -> list[FeatureSpec]:
        """Convert health issues to prioritized feature specs.

        Priority formula: severity * 0.4 + velocity_impact * 0.4 + (1/effort) * 0.2

        Args:
            report: Portfolio health report
            max_features: Maximum features to generate

        Returns:
            List of FeatureSpec sorted by priority
        """
        features: list[FeatureSpec] = []
        feature_id = 1

        for project in report.projects:
            if project.error:
                continue

            # Calculate project velocity impact based on tier
            tier_velocity = {0: 1.0, 1: 0.9, 2: 0.7, 3: 0.5, 4: 0.3}
            velocity_impact = tier_velocity.get(project.tier, 0.5)

            # Generate features for blocking issues
            if project.blocking_issues > 0:
                severity_score = 1.0 if project.risk_level == "critical" else 0.8
                effort = max(project.debt_hours, 1.0)

                priority_score = severity_score * 0.4 + velocity_impact * 0.4 + (1 / effort) * 0.2

                priority = self._score_to_priority(priority_score)

                feature = FeatureSpec(
                    id=f"FIX-{feature_id:04d}",
                    name=f"Fix {project.blocking_issues} blocking issues in {project.project}",
                    description=f"""
Address {project.blocking_issues} blocking issues identified in {project.domain}/{project.project}.

**Risk Level**: {project.risk_level}
**Security Score**: {project.security_score:.1f}
**Estimated Debt Hours**: {project.debt_hours:.1f}h

This is a Tier {project.tier} project with {"high" if velocity_impact > 0.7 else "moderate"} velocity impact.
""".strip(),
                    priority=priority,
                    estimated_hours=project.debt_hours,
                    source_project=f"{project.domain}/{project.project}",
                )
                features.append(feature)
                feature_id += 1

        # Sort by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        features.sort(key=lambda f: priority_order.get(f.priority, 4))

        return features[:max_features]

    def _score_to_priority(self, score: float) -> str:
        """Convert priority score to priority label."""
        if score >= 0.8:
            return "critical"
        elif score >= 0.6:
            return "high"
        elif score >= 0.4:
            return "medium"
        else:
            return "low"

    def export_features_json(
        self,
        features: list[FeatureSpec],
        path: Path,
        merge_existing: bool = True,
    ) -> None:
        """Export features to a features.json file.

        Args:
            features: List of features to export
            path: Path to output file
            merge_existing: If True, merge with existing features
        """
        output = {
            "project": "forge-portfolio-fixes",
            "version": "1.0.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "features": [],
        }

        # Load existing if merging
        existing_ids = set()
        if merge_existing and path.exists():
            try:
                existing = json.loads(path.read_text())
                output["features"] = existing.get("features", [])
                existing_ids = {f["id"] for f in output["features"]}
            except (json.JSONDecodeError, KeyError):
                pass

        # Add new features (skip duplicates)
        for feature in features:
            if feature.id not in existing_ids:
                output["features"].append(feature.to_dict())

        # Write output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(output, indent=2))
        logger.info(f"Exported {len(features)} features to {path}")

    async def _save_to_history(self, report: PortfolioHealthReport) -> None:
        """Save report to history directory.

        Args:
            report: Report to save
        """
        history_dir = Path(self.config.history_dir)
        history_dir.mkdir(parents=True, exist_ok=True)

        date_str = report.generated_at.strftime("%Y-%m-%d")
        history_path = history_dir / f"{date_str}.json"

        history_path.write_text(json.dumps(report.to_dict(), indent=2))
        logger.info(f"Saved portfolio health to {history_path}")


async def scan_and_generate_fixes(
    tech_diligence: Optional["TechDiligenceBridge"] = None,
    output_path: Path = Path("portfolio-fixes.json"),
    max_features: int = 20,
    config: ScannerConfig | None = None,
) -> PortfolioHealthReport:
    """Convenience function to scan portfolio and generate fixes.

    Args:
        tech_diligence: TechDiligence bridge
        output_path: Path for features.json output
        max_features: Maximum features to generate
        config: Scanner configuration

    Returns:
        PortfolioHealthReport
    """
    scanner = PortfolioScanner(tech_diligence, config)
    report = await scanner.scan_portfolio()
    features = scanner.prioritize_fixes(report, max_features)
    scanner.export_features_json(features, output_path)
    return report
