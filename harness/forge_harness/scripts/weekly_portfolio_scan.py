#!/usr/bin/env python3
"""
Weekly Portfolio Scanner for FORGE.

Scans all FORGE projects using Tech Diligence and generates features.json
entries for automated remediation via Ralph Loop.

This script is designed to be run weekly via cron or CI/CD:
    0 9 * * 1 cd /path/to/FORGE/harness && uv run python -m forge_harness.scripts.weekly_portfolio_scan

Usage:
    python -m forge_harness.scripts.weekly_portfolio_scan
    python -m forge_harness.scripts.weekly_portfolio_scan --tier 1 --max-features 20
    python -m forge_harness.scripts.weekly_portfolio_scan --dry-run --json
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def run_weekly_scan(
    forge_root: Path,
    tiers: list[int] | None = None,
    domains: list[str] | None = None,
    max_features_per_project: int = 10,
    min_severity: str = "medium",
    dry_run: bool = False,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Run weekly portfolio scan and generate features.

    Args:
        forge_root: Path to FORGE repository root
        tiers: Filter to specific tiers (1=revenue, 2=growth, 3=consumer, 4=dev)
        domains: Filter to specific domains
        max_features_per_project: Max features per project
        min_severity: Minimum severity for feature generation
        dry_run: Preview mode (don't write files)
        output_dir: Directory for scan outputs

    Returns:
        Scan results summary
    """
    from forge_harness.portfolio_scanner import (
        FORGE_PROJECTS,
        PortfolioScanner,
        ScannerConfig,
    )

    # Try to import TechDiligenceBridge
    tech_diligence = None
    try:
        from forge_harness.meta_learning.bridges.tech_diligence import (
            create_tech_diligence_bridge,
        )

        tech_diligence = create_tech_diligence_bridge()
        logger.info("TechDiligenceBridge initialized")
    except Exception as e:
        logger.warning(f"TechDiligenceBridge not available: {e}")
        logger.info("Falling back to local scanning")

    # Configure scanner
    config = ScannerConfig(
        concurrency=3,
        timeout_seconds=120,
        history_dir=str(output_dir / "portfolio_health")
        if output_dir
        else ".forge/learning/portfolio_health",
        filter_domains=domains,
        filter_tiers=tiers,
        min_severity_for_feature=min_severity,
    )

    scanner = PortfolioScanner(tech_diligence=tech_diligence, config=config)

    # Filter projects
    projects = FORGE_PROJECTS.copy()
    if tiers:
        projects = [p for p in projects if p["tier"] in tiers]
    if domains:
        projects = [p for p in projects if p["domain"] in domains]

    logger.info(f"Scanning {len(projects)} projects...")

    # Run scan
    report = await scanner.scan_portfolio(projects=projects)

    # Generate prioritized fixes
    features = scanner.prioritize_fixes(
        report, max_features=max_features_per_project * len(projects)
    )

    # Generate per-project features for Ralph loop queuing
    features_by_project: dict[str, list[dict]] = {}
    for feature in features:
        source = feature.source_project or "unknown"
        if source not in features_by_project:
            features_by_project[source] = []
        features_by_project[source].append(feature.to_dict())

    # Write features to each project's directory for Ralph processing
    features_written = 0
    if not dry_run:
        for project_key, project_features in features_by_project.items():
            parts = project_key.split("/")
            if len(parts) == 2:
                domain, proj = parts
                project_path = forge_root / domain / proj
                if project_path.exists():
                    features_path = project_path / "features_quality.json"
                    features_data = {
                        "project": project_key,
                        "version": "1.0.0",
                        "generated_at": datetime.now(UTC).isoformat(),
                        "source": "weekly_portfolio_scan",
                        "features": project_features,
                    }
                    features_path.write_text(json.dumps(features_data, indent=2))
                    logger.info(f"Wrote {len(project_features)} features to {features_path}")
                    features_written += len(project_features)

    # Write portfolio summary
    summary_path = (output_dir or Path(".")) / "portfolio_scan_summary.json"
    summary = {
        "scan_timestamp": datetime.now(UTC).isoformat(),
        "total_projects": report.total_projects,
        "healthy_projects": report.healthy_projects,
        "at_risk_projects": report.at_risk_projects,
        "critical_projects": report.critical_projects,
        "total_debt_hours": report.total_debt_hours,
        "total_blocking_issues": report.total_blocking_issues,
        "aggregate_security_score": report.aggregate_security_score,
        "features_generated": len(features),
        "features_by_project": {k: len(v) for k, v in features_by_project.items()},
    }

    if not dry_run:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2))
        logger.info(f"Wrote scan summary to {summary_path}")

    return {
        "success": True,
        "scan_timestamp": summary["scan_timestamp"],
        "total_projects_scanned": report.total_projects,
        "healthy_projects": report.healthy_projects,
        "at_risk_projects": report.at_risk_projects,
        "critical_projects": report.critical_projects,
        "total_debt_hours": round(report.total_debt_hours, 1),
        "total_blocking_issues": report.total_blocking_issues,
        "features_generated": len(features),
        "features_written": features_written,
        "features_by_project": {k: len(v) for k, v in features_by_project.items()},
        "dry_run": dry_run,
    }


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Weekly portfolio scanner for FORGE projects")
    parser.add_argument(
        "--forge-root",
        type=Path,
        default=None,
        help="FORGE repository root (default: auto-detect)",
    )
    parser.add_argument(
        "--tier",
        type=int,
        action="append",
        dest="tiers",
        help="Filter to specific tier(s): 1=revenue, 2=growth, 3=consumer, 4=dev",
    )
    parser.add_argument(
        "--domain",
        action="append",
        dest="domains",
        help="Filter to specific domain(s)",
    )
    parser.add_argument(
        "--max-features",
        type=int,
        default=10,
        help="Max features per project (default: 10)",
    )
    parser.add_argument(
        "--min-severity",
        choices=["critical", "high", "medium", "low"],
        default="medium",
        help="Minimum severity for features (default: medium)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for scan results",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview mode - don't write features",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output as JSON",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Find FORGE root
    if args.forge_root:
        forge_root = args.forge_root
    else:
        # Try to auto-detect
        current = Path.cwd()
        for _ in range(10):
            if (current / "domains.yaml").exists() or (current / ".forge-root").exists():
                forge_root = current
                break
            if (current / "harness").exists() and (current / "harness" / "forge_harness").exists():
                forge_root = current
                break
            parent = current.parent
            if parent == current:
                break
            current = parent
        else:
            # Default to parent of harness
            forge_root = Path(__file__).parent.parent.parent.parent
            if not (forge_root / "harness").exists():
                logger.error("Could not detect FORGE root. Use --forge-root.")
                return 1

    if not forge_root.exists():
        logger.error(f"FORGE root not found: {forge_root}")
        return 1

    logger.info(f"FORGE root: {forge_root}")

    try:
        result = asyncio.run(
            run_weekly_scan(
                forge_root=forge_root,
                tiers=args.tiers,
                domains=args.domains,
                max_features_per_project=args.max_features,
                min_severity=args.min_severity,
                dry_run=args.dry_run,
                output_dir=args.output_dir,
            )
        )

        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print("\n" + "=" * 60)
            print("FORGE Weekly Portfolio Scan Complete")
            print("=" * 60)
            print(f"Timestamp: {result['scan_timestamp']}")
            print(f"Projects Scanned: {result['total_projects_scanned']}")
            print(f"  Healthy: {result['healthy_projects']}")
            print(f"  At Risk: {result['at_risk_projects']}")
            print(f"  Critical: {result['critical_projects']}")
            print(f"Total Debt Hours: {result['total_debt_hours']}h")
            print(f"Blocking Issues: {result['total_blocking_issues']}")
            print(f"\nFeatures Generated: {result['features_generated']}")
            print(f"Features Written: {result['features_written']}")

            if result["features_by_project"]:
                print("\nFeatures by Project:")
                for proj, count in sorted(
                    result["features_by_project"].items(), key=lambda x: -x[1]
                ):
                    print(f"  {proj}: {count}")

            if result["dry_run"]:
                print("\n(DRY RUN - no features written)")

        return 0

    except Exception as e:
        logger.exception(f"Scan failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
