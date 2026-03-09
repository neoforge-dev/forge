"""Flywheel Debt Scanner - Tech debt detection across the portfolio."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..logging_config import get_logger

logger = get_logger(__name__)


class DebtScanner:
    """Detect tech debt across the FORGE portfolio."""

    def __init__(self, forge_root: Path):
        self.forge_root = forge_root

    async def scan_project(
        self,
        domain: str,
        project: str,
        max_features: int = 10,
        priority_threshold: str = "medium",
    ) -> list[dict[str, Any]]:
        """Scan a single project for tech debt."""
        project_path = self.forge_root / domain / project
        if not project_path.exists():
            logger.warning(f"Project path {project_path} does not exist")
            return []

        return await scan_project_for_debt(
            domain=domain,
            project=project,
            project_path=project_path,
            max_features=max_features,
            priority_threshold=priority_threshold,
        )

    async def scan_portfolio(
        self,
        priority_threshold: str = "medium",
        max_features_per_project: int = 10,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        include_harness: bool = True,
    ) -> list[dict[str, Any]]:
        """Scan entire portfolio for tech debt."""
        return await generate_portfolio_features(
            forge_root=self.forge_root,
            priority_threshold=priority_threshold,
            max_features_per_project=max_features_per_project,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            include_harness=include_harness,
        )


async def scan_project_for_debt(
    domain: str,
    project: str,
    project_path: Path,
    max_features: int = 10,
    priority_threshold: str = "medium",
    use_local_fallback: bool = True,
) -> list[dict[str, Any]]:
    """Scan a single project for tech debt and generate features."""
    from ..meta_learning.bridges.atlas_prioritizer import AtlasPrioritizer
    from ..meta_learning.bridges.code_atlas import CodeAtlasBridge
    from ..meta_learning.bridges.tech_diligence import TechDiligenceBridge
    from ..meta_learning.config import get_config

    tech_diligence_url = os.getenv("TECH_DILIGENCE_URL", "http://localhost:8000")
    bridge = TechDiligenceBridge(base_url=tech_diligence_url)

    # Initialize Code Atlas integration
    config = get_config()
    atlas_bridge = CodeAtlasBridge.from_config(config.code_atlas)
    prioritizer = AtlasPrioritizer(atlas_bridge)

    # Check if we can do incremental scanning
    scan_scope = None
    if await prioritizer.is_available():
        recently_changed = await prioritizer.get_recently_changed_files(
            domain=domain,
            project=project,
            days_back=7,
            limit=100
        )
        if recently_changed:
            logger.info(f"Incremental scan: scoping to {len(recently_changed)} files")
            scan_scope = recently_changed

    # Try remote Tech Diligence first
    try:
        report = await bridge.analyze_and_wait(
            repo_path=str(project_path),
            max_wait_seconds=300,
        )
    except Exception as e:
        logger.warning(f"Tech Diligence unavailable: {e}")
        report = None

    if report is None and use_local_fallback:
        logger.info(f"Falling back to local scanning for {domain}/{project}")
        return await scan_project_local(
            domain=domain,
            project=project,
            project_path=project_path,
            max_features=max_features,
            priority_threshold=priority_threshold,
        )
    elif report is None:
        logger.warning(f"Tech Diligence analysis failed for {domain}/{project}")
        return []

    # Priority ordering for filtering
    priority_order = ["critical", "high", "medium", "low"]
    threshold_idx = priority_order.index(priority_threshold)
    allowed_priorities = set(priority_order[: threshold_idx + 1])

    # Convert findings to dicts for Atlas processing
    finding_dicts = [
        {
            "category": getattr(finding, "rule_id", "unknown"),
            "title": finding.title or finding.message[:60],
            "description": finding.message,
            "severity": finding.severity.lower(),
            "file_path": finding.file_path,
            "scanner": getattr(finding, "scanner", "unknown"),
        }
        for finding in report.findings[:max_features]
    ]

    # Apply Code Atlas enhancements if available
    if await prioritizer.is_available():
        logger.info(f"Applying Code Atlas enhancements for {domain}/{project}")
        finding_dicts = await prioritizer.filter_by_historical_success(finding_dicts)
        finding_dicts = await prioritizer.boost_hotspot_priorities(
            finding_dicts,
            domain=domain,
            project=project
        )

    features = []
    for finding_dict in finding_dicts:
        severity = finding_dict["severity"]
        if severity not in allowed_priorities:
            continue

        base_metadata = {
            "source": "tech_diligence",
            "domain": domain,
            "project": project,
            "finding_id": finding_dict["category"],
            "scanner": finding_dict["scanner"],
            "generated_at": datetime.now(UTC).isoformat(),
        }

        if "metadata" in finding_dict:
            base_metadata.update(finding_dict["metadata"])

        feature = {
            "id": f"debt-{domain}-{project}-{finding_dict['category']}-{len(features)}",
            "name": f"[{project}] {finding_dict['title']}",
            "description": finding_dict.get("description", ""),
            "status": "pending",
            "priority": severity,
            "acceptance_criteria": [
                f"Fix {finding_dict['category']} in {finding_dict['file_path'] or 'codebase'}",
                "All tests pass",
                "No new issues introduced",
            ],
            "depends_on": [],
            "tests": [],
            "estimated_tokens": 4000,
            "attempts": 0,
            "metadata": base_metadata,
        }
        features.append(feature)

    return features


async def scan_project_local(
    domain: str,
    project: str,
    project_path: Path,
    max_features: int = 10,
    priority_threshold: str = "medium",
    metrics_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Scan a project locally for quality issues."""
    from ..quality_loop import QualityLoopHarness
    from .generator import quality_report_to_features

    if metrics_dir is None:
        metrics_dir = project_path / "quality_metrics"

    harness = QualityLoopHarness(
        forge_root=project_path.parent.parent,
        metrics_dir=metrics_dir,
    )

    try:
        report = await harness.scan_project(
            {
                "name": project,
                "domain": domain,
                "path": str(project_path),
            }
        )
    except Exception as e:
        logger.warning(f"Local quality scan failed for {domain}/{project}: {e}")
        return []

    return quality_report_to_features(
        report=report,
        max_features=max_features,
        priority_threshold=priority_threshold,
    )


async def generate_portfolio_features(
    forge_root: Path,
    output_path: Path | None = None,
    priority_threshold: str = "medium",
    max_features_per_project: int = 10,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    include_harness: bool = True,
    scan_fn: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
) -> list[dict[str, Any]]:
    """Generate features.json entries for entire FORGE portfolio."""
    import json

    all_features: list[dict[str, Any]] = []
    exclude_domains = exclude_domains or []

    # Find domains
    domains = []
    for item in forge_root.iterdir():
        if not item.is_dir() or item.name.startswith((".", "_")):
            continue
        if item.name in ["docs", "scripts", "templates", "node_modules", ".git"]:
            continue
        if include_domains and item.name not in include_domains:
            continue
        if item.name in exclude_domains:
            continue
        domains.append(item)

    # Allow injection of custom scan function (used by tests and callers that
    # want to override the scanning behavior).
    scan = scan_fn or scan_project_for_debt

    for domain_path in domains:
        domain_name = domain_path.name
        for project_path in domain_path.iterdir():
            if not project_path.is_dir() or project_path.name.startswith("."):
                continue

            # Check markers
            if not any(
                (project_path / m).exists()
                for m in ["CLAUDE.md", "pyproject.toml", "package.json", "Cargo.toml"]
            ):
                continue

            try:
                project_name = project_path.name
                features = await scan(
                    domain=domain_name,
                    project=project_name,
                    project_path=project_path,
                    max_features=max_features_per_project,
                    priority_threshold=priority_threshold,
                )
                all_features.extend(features)
            except Exception as e:
                logger.warning(f"Failed to scan {domain_name}/{project_path.name}: {e}")

    if include_harness:
        harness_path = forge_root / "harness"
        if harness_path.exists():
            try:
                from ..self_improve import HarnessSelfImprover
                improver = HarnessSelfImprover(harness_root=harness_path)
                result = await improver.analyze(
                    max_features=max_features_per_project,
                    priority_threshold=priority_threshold,
                )
                for feature in result.features_generated:
                    all_features.append(feature.to_dict())
            except Exception as e:
                logger.warning(f"Failed to scan harness: {e}")

    # Sort and deduplicate
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_features.sort(key=lambda f: priority_order.get(f.get("priority", "medium"), 2))

    seen_ids = set()
    unique_features = []
    for f in all_features:
        if f["id"] not in seen_ids:
            seen_ids.add(f["id"])
            unique_features.append(f)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(unique_features, indent=2, default=str))

    return unique_features


async def scan_portfolio_for_debt(
    forge_root: Path,
    priority_threshold: str = "medium",
    max_features_per_project: int = 10,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    include_harness: bool = True,
) -> list[dict[str, Any]]:
    """Alias for generate_portfolio_features."""
    return await generate_portfolio_features(
        forge_root=forge_root,
        priority_threshold=priority_threshold,
        max_features_per_project=max_features_per_project,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        include_harness=include_harness,
    )
