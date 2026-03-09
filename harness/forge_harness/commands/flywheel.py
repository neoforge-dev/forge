"""Flywheel command group."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import click

from ..logging_config import get_logger
from .common import find_forge_root

logger = get_logger(__name__)


@click.group()
@click.pass_context
def flywheel(ctx: click.Context) -> None:
    """Compounding autonomous development loops."""
    pass


@flywheel.command("run")
@click.option("-d", "--domain", required=True, help="Domain to focus on (e.g., codeswiftr-com)")
@click.option(
    "-p", "--project", required=True, help="Project to focus on (e.g., interview-simulator)"
)
@click.option(
    "--forge-root",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to FORGE repository root",
)
@click.option("--max-iterations", type=int, default=100, help="Maximum Ralph loop iterations")
@click.option("--max-features", type=int, default=10, help="Maximum features to generate per scan")
@click.option(
    "--priority",
    type=click.Choice(["critical", "high", "medium", "low"]),
    default="medium",
    help="Minimum priority threshold",
)
@click.option("--dry-run", is_flag=True, help="Preview mode - don't implement")
@click.option("--json", "json_output", is_flag=True, help="Output results as JSON")
@click.pass_context
def flywheel_run(
    ctx: click.Context,
    domain: str,
    project: str,
    forge_root: Path | None,
    max_iterations: int,
    max_features: int,
    priority: str,
    dry_run: bool,
    json_output: bool,
) -> None:
    """Run the complete autonomous development flywheel."""
    from ..flywheel import FlywheelConfig, run_flywheel

    if forge_root is None:
        forge_root = Path(os.getenv("FORGE_ROOT", Path.cwd().parent))

    config = FlywheelConfig(
        max_iterations=max_iterations,
        max_features_per_project=max_features,
        priority_threshold=priority,
        dry_run=dry_run,
    )

    click.echo(f"Starting flywheel for {domain}/{project}...")
    click.echo(f"  Forge root: {forge_root}")
    click.echo(f"  Priority threshold: {priority}")
    click.echo(f"  Dry run: {dry_run}")
    click.echo()

    try:
        result = asyncio.run(run_flywheel(forge_root, domain, project, config))

        if json_output:
            click.echo(json.dumps(result.to_dict(), indent=2, default=str))
            return

        click.echo("=" * 60)
        click.echo("Flywheel Results")
        click.echo("=" * 60)
        click.echo(f"Duration: {(result.ended_at - result.started_at).total_seconds():.1f}s")
        click.echo(f"Projects scanned: {result.projects_scanned}")
        click.echo(f"Features generated: {result.features_generated}")
        click.echo(f"Features implemented: {result.features_implemented}")
        click.echo(f"Features blocked: {result.features_blocked}")
        click.echo(f"Sessions indexed: {result.sessions_indexed}")

        if result.errors:
            click.echo("\nErrors:")
            for error in result.errors:
                click.echo(f"  - {error}")

        if result.features_implemented > 0:
            click.echo(f"\n✅ Successfully implemented {result.features_implemented} features")

    except Exception as e:
        click.echo(f"Error running flywheel: {e}", err=True)
        logger.exception("Flywheel run failed")
        raise SystemExit(1)


@flywheel.command("scan")
@click.option(
    "--forge-root",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to FORGE repository root",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output path for combined features.json",
)
@click.option(
    "--priority",
    type=click.Choice(["critical", "high", "medium", "low"]),
    default="medium",
    help="Minimum priority threshold",
)
@click.option("--max-features", type=int, default=10, help="Max features per project")
@click.option("--include-domains", multiple=True, help="Only scan these domains (can be repeated)")
@click.option("--exclude-domains", multiple=True, help="Exclude these domains (can be repeated)")
@click.option(
    "--include-harness/--no-harness", default=True, help="Include harness self-improvement"
)
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.pass_context
def flywheel_scan(
    ctx: click.Context,
    forge_root: Path | None,
    output: Path | None,
    priority: str,
    max_features: int,
    include_domains: tuple[str, ...],
    exclude_domains: tuple[str, ...],
    include_harness: bool,
    json_output: bool,
) -> None:
    """Scan portfolio for tech debt and generate features."""
    from ..flywheel import generate_portfolio_features

    if forge_root is None:
        forge_root = Path(os.getenv("FORGE_ROOT", Path.cwd().parent))

    click.echo(f"Scanning portfolio at {forge_root}...")
    click.echo(f"Priority threshold: {priority}")

    try:
        features = asyncio.run(
            generate_portfolio_features(
                forge_root=forge_root,
                output_path=output,
                priority_threshold=priority,
                max_features_per_project=max_features,
                include_domains=list(include_domains) if include_domains else None,
                exclude_domains=list(exclude_domains) if exclude_domains else None,
                include_harness=include_harness,
            )
        )

        if json_output:
            click.echo(json.dumps(features, indent=2, default=str))
            return

        click.echo(f"\nGenerated {len(features)} features:")

        by_priority: dict[str, list] = {"critical": [], "high": [], "medium": [], "low": []}
        for f in features:
            p = f.get("priority", "medium")
            if p in by_priority:
                by_priority[p].append(f)

        for p in ["critical", "high", "medium", "low"]:
            if by_priority[p]:
                click.echo(f"  {p.capitalize()}: {len(by_priority[p])}")

        if features:
            click.echo("\nTop features:")
            for i, f in enumerate(features[:5], 1):
                click.echo(f"  {i}. [{f.get('priority', '?')}] {f.get('name', 'Unknown')[:60]}")

        if output:
            click.echo(f"\n✅ Wrote features to {output}")

    except Exception as e:
        click.echo(f"Error scanning portfolio: {e}", err=True)
        logger.exception("Flywheel scan failed")
        raise SystemExit(1)


@flywheel.command("loop")
@click.option("-d", "--domain", required=True, help="Domain name")
@click.option("-p", "--project", required=True, help="Project name")
@click.option(
    "--features",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to features.json",
)
@click.option("--max-iterations", type=int, default=100, help="Maximum loop iterations")
@click.option("--dry-run", is_flag=True, help="Preview mode")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.option(
    "--with-orchestrator",
    is_flag=True,
    help="Enable Claude Code SDK orchestrator for autonomous implementation",
)
@click.option("--model", default="claude-sonnet-4-20250514", help="Model for orchestrator")
@click.option(
    "--test-command",
    default=None,
    help="Override test command (e.g., 'npm test' or 'echo ok' to skip)",
)
@click.pass_context
def flywheel_loop(
    ctx: click.Context,
    domain: str,
    project: str,
    features: Path | None,
    max_iterations: int,
    dry_run: bool,
    json_output: bool,
    with_orchestrator: bool,
    model: str,
    test_command: str | None,
) -> None:
    """Run a fully-wired Ralph loop with meta-learning."""
    from ..flywheel import FlywheelConfig, create_flywheel_loop

    config = FlywheelConfig(
        max_iterations=max_iterations,
        dry_run=dry_run,
        test_command=test_command,
    )

    click.echo(f"Creating fully-wired Ralph loop for {domain}/{project}...")

    orchestrator = None
    if with_orchestrator:
        from ..agent import FeatureOrchestrator

        forge_root = Path(os.getenv("FORGE_ROOT", Path.cwd())).resolve()
        if features:
            working_dir = features.parent.resolve()
        else:
            working_dir = (forge_root / domain / project).resolve()
            if not working_dir.exists():
                working_dir = Path.cwd().resolve()

        click.echo(f"Creating FeatureOrchestrator (model={model}, cwd={working_dir})")
        orchestrator = FeatureOrchestrator(
            working_dir=working_dir,
            model=model,
            max_iterations=50,
        )

    try:
        loop = create_flywheel_loop(
            domain=domain,
            project=project,
            features_path=features,
            config=config,
            orchestrator=orchestrator,
        )

        click.echo("Meta-learning components wired:")
        click.echo(f"  Decision Engine: {'✓' if loop.decision_engine else '✗'}")
        click.echo(f"  Approval Queue: {'✓' if loop.approval_queue else '✗'}")
        click.echo(f"  Feedback Loops: {'✓' if loop.feedback_loop_manager else '✗'}")
        click.echo(f"  Code Atlas: {'✓' if loop.code_atlas_bridge else '✗'}")
        click.echo(f"  Orchestrator: {'✓' if loop.orchestrator else '✗'}")
        click.echo()

        result = asyncio.run(loop.run())

        if json_output:
            click.echo(
                json.dumps(
                    {
                        "success": result.success,
                        "iterations": result.iterations,
                        "features_completed": result.features_completed,
                        "features_blocked": result.features_blocked,
                        "features_remaining": result.features_remaining,
                        "duration_seconds": result.duration_seconds,
                    },
                    indent=2,
                )
            )
            return

        click.echo("=" * 60)
        click.echo("Ralph Loop Results")
        click.echo("=" * 60)
        click.echo(f"Success: {result.success}")
        click.echo(f"Iterations: {result.iterations}")
        click.echo(f"Features completed: {result.features_completed}")
        click.echo(f"Features blocked: {result.features_blocked}")
        click.echo(f"Features remaining: {result.features_remaining}")
        click.echo(f"Duration: {result.duration_seconds:.1f}s")

    except Exception as e:
        click.echo(f"Error running loop: {e}", err=True)
        logger.exception("Flywheel loop failed")
        raise SystemExit(1)


@flywheel.command("weekly-scan")
@click.option(
    "--forge-root",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to FORGE repository root",
)
@click.option(
    "--tier",
    type=int,
    multiple=True,
    help="Filter to specific tier(s): 1=revenue, 2=growth, 3=consumer, 4=dev",
)
@click.option(
    "--domain",
    "domains",
    multiple=True,
    help="Filter to specific domain(s)",
)
@click.option("--max-features", type=int, default=10, help="Max features per project")
@click.option(
    "--min-severity",
    type=click.Choice(["critical", "high", "medium", "low"]),
    default="medium",
    help="Minimum severity for features",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Output directory for scan results",
)
@click.option("--dry-run", is_flag=True, help="Preview mode - don't write features")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.pass_context
def flywheel_weekly_scan(
    ctx: click.Context,
    forge_root: Path | None,
    tier: tuple[int, ...],
    domains: tuple[str, ...],
    max_features: int,
    min_severity: str,
    output_dir: Path | None,
    dry_run: bool,
    json_output: bool,
) -> None:
    """Run weekly portfolio scan and generate Ralph-ready features."""
    from ..scripts.weekly_portfolio_scan import run_weekly_scan

    if forge_root is None:
        forge_root = find_forge_root()
        if not forge_root:
            click.echo("Error: Could not find FORGE repository root.", err=True)
            click.echo("Use --forge-root to specify path.", err=True)
            raise SystemExit(1)
        forge_root = Path(forge_root)

    click.echo("FORGE Weekly Portfolio Scan")
    click.echo("=" * 50)
    click.echo(f"  FORGE root: {forge_root}")
    if tier:
        click.echo(f"  Tiers: {list(tier)}")
    if domains:
        click.echo(f"  Domains: {list(domains)}")
    click.echo(f"  Min severity: {min_severity}")
    click.echo(f"  Max features/project: {max_features}")
    if dry_run:
        click.echo("  Mode: DRY RUN")
    click.echo()

    try:
        result = asyncio.run(
            run_weekly_scan(
                forge_root=forge_root,
                tiers=list(tier) if tier else None,
                domains=list(domains) if domains else None,
                max_features_per_project=max_features,
                min_severity=min_severity,
                dry_run=dry_run,
                output_dir=output_dir,
            )
        )

        if json_output:
            click.echo(json.dumps(result, indent=2))
            return

        click.echo(f"\nScan Complete: {result['scan_timestamp']}")
        click.echo(f"Projects Scanned: {result['total_projects_scanned']}")
        click.echo(f"  Healthy: {result['healthy_projects']}")
        click.echo(f"  At Risk: {result['at_risk_projects']}")
        click.echo(f"  Critical: {result['critical_projects']}")
        click.echo(f"Total Debt Hours: {result['total_debt_hours']}h")
        click.echo(f"Blocking Issues: {result['total_blocking_issues']}")
        click.echo(f"\nFeatures Generated: {result['features_generated']}")
        click.echo(f"Features Written: {result['features_written']}")

        if result["features_by_project"]:
            click.echo("\nFeatures by Project:")
            for proj, count in sorted(result["features_by_project"].items(), key=lambda x: -x[1]):
                click.echo(f"  {proj}: {count}")

        if dry_run:
            click.echo("\n(DRY RUN - no features written)")
        elif result["features_written"] > 0:
            click.echo("\n✅ Features queued for Ralph loop processing")
            click.echo("   Run 'forge-harness flywheel loop -d DOMAIN -p PROJECT' to process")

    except Exception as e:
        click.echo(f"Error during weekly scan: {e}", err=True)
        logger.exception("Weekly scan failed")
        raise SystemExit(1)
