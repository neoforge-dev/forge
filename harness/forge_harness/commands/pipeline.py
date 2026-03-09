"""Pipeline command group."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from .common import find_forge_root


@click.group()
@click.pass_context
def pipeline(ctx: click.Context) -> None:
    """Pipeline management commands."""
    pass


@pipeline.command("execute")
@click.argument("pipeline_name")
@click.option("--context", "-c", "context_json", default="{}", help="JSON context for pipeline")
@click.option("--checkpoint-dir", type=click.Path(), help="Checkpoint directory")
@click.option("--dry-run", is_flag=True, help="Dry run mode")
@click.pass_context
def pipeline_execute(
    ctx: click.Context,
    pipeline_name: str,
    context_json: str,
    checkpoint_dir: str | None,
    dry_run: bool,
) -> None:
    """Execute a pipeline by name."""
    import tempfile

    from ..orchestration_harness import OrchestrationHarness

    try:
        context = json.loads(context_json)
    except json.JSONDecodeError as e:
        click.echo(f"Error: Invalid JSON context: {e}", err=True)
        raise SystemExit(1)

    checkpoint_path = Path(checkpoint_dir) if checkpoint_dir else Path(tempfile.gettempdir())
    orchestrator = OrchestrationHarness(
        harnesses={},
        checkpoint_dir=checkpoint_path,
        enable_checkpoints=not dry_run,
    )

    pipeline_obj = orchestrator.get_builtin_pipeline(pipeline_name)
    if not pipeline_obj:
        click.echo(f"Error: Pipeline '{pipeline_name}' not found.", err=True)
        click.echo("Use 'forge-harness pipeline list' to see available pipelines.", err=True)
        raise SystemExit(1)

    pipeline_obj.context.update(context)

    if dry_run:
        click.echo(f"Dry run: Pipeline '{pipeline_name}'")
        click.echo(f"  Steps: {len(pipeline_obj.steps)}")
        for i, step in enumerate(pipeline_obj.steps, 1):
            click.echo(f"    {i}. {step.name} ({step.harness}.{step.method})")
        click.echo(f"  Context: {json.dumps(pipeline_obj.context, indent=2)}")
        return

    try:
        from ..harness_registry import HarnessRegistry

        registry = HarnessRegistry()
        orchestrator.harnesses = registry.get_harnesses()
    except ImportError:
        click.echo("Error: Could not import HarnessRegistry for pipeline execution.", err=True)
        raise SystemExit(1)

    click.echo(f"Executing pipeline: {pipeline_name}")
    try:
        result = asyncio.run(orchestrator.execute(pipeline_obj))
        if result.success:
            click.echo("\n✓ Pipeline completed successfully")
            click.echo(f"  Steps completed: {len(result.step_results)}")
            click.echo(f"  Duration: {result.duration_seconds:.1f}s")
        else:
            click.echo("\n✗ Pipeline failed", err=True)
            for step_result in result.step_results:
                if step_result.error:
                    click.echo(f"  - {step_result.name}: {step_result.error}", err=True)
            raise SystemExit(1)
    except Exception as e:
        click.echo(f"\n✗ Pipeline error: {e}", err=True)
        raise SystemExit(1)


@pipeline.command("list")
@click.option("--yaml", "show_yaml", is_flag=True, help="Include YAML pipelines")
@click.pass_context
def pipeline_list(ctx: click.Context, show_yaml: bool) -> None:
    """List available pipelines."""
    import tempfile

    from ..orchestration_harness import OrchestrationHarness

    orchestrator = OrchestrationHarness(
        harnesses={},
        checkpoint_dir=Path(tempfile.gettempdir()),
        enable_checkpoints=False,
    )

    click.echo("Available Pipelines:\n")
    click.echo("Built-in:")
    for info in orchestrator.list_builtin_pipelines():
        pipeline_obj = orchestrator.get_builtin_pipeline(info["name"])
        step_count = len(pipeline_obj.steps) if pipeline_obj else 0
        click.echo(f"  {info['name']}: {info['description']}")
        click.echo(f"    Steps: {step_count}")

    if show_yaml:
        root = find_forge_root()
        if root:
            pipelines_dir = root / "harness" / "pipelines"
            if pipelines_dir.exists():
                click.echo("\nYAML Pipelines:")
                yaml_files = list(pipelines_dir.glob("*.yaml")) + list(pipelines_dir.glob("*.yml"))
                if yaml_files:
                    for yaml_file in yaml_files:
                        click.echo(f"  {yaml_file.stem}")
                else:
                    click.echo("  (none found)")
            else:
                click.echo("\nYAML Pipelines: (no pipelines/ directory)")


@pipeline.command("status")
@click.argument("checkpoint_path", type=click.Path(exists=True))
@click.pass_context
def pipeline_status(ctx: click.Context, checkpoint_path: str) -> None:
    """Show status of a pipeline checkpoint."""
    checkpoint_file = Path(checkpoint_path)
    try:
        with checkpoint_file.open() as f:
            checkpoint = json.load(f)
    except json.JSONDecodeError as e:
        click.echo(f"Error: Invalid checkpoint file: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"Pipeline Checkpoint: {checkpoint_file.name}\n")

    pipeline_name = checkpoint.get("pipeline_name", "unknown")
    click.echo(f"Pipeline: {pipeline_name}")
    click.echo(f"Created: {checkpoint.get('created_at', 'unknown')}")
    click.echo(f"Updated: {checkpoint.get('updated_at', 'unknown')}")

    step_results = checkpoint.get("step_results", [])
    if step_results:
        click.echo(f"\nStep Status ({len(step_results)} steps):")
        for result in step_results:
            status = result.get("status", "unknown")
            name = result.get("name", "unknown")
            duration = result.get("duration_seconds", 0)
            error = result.get("error")

            status_icon = {"completed": "✓", "failed": "✗", "pending": "○", "running": "▸"}.get(
                status, "?"
            )
            click.echo(f"  {status_icon} {name} ({status}) - {duration:.1f}s")
            if error:
                click.echo(f"      Error: {error}")

    context = checkpoint.get("context", {})
    if context:
        click.echo("\nContext:")
        for key, value in context.items():
            click.echo(f"  {key}: {value}")


@pipeline.command("resume")
@click.argument("checkpoint_path", type=click.Path(exists=True))
@click.option(
    "--domain", "-d", help="Domain for harness configuration (overrides checkpoint context)"
)
@click.pass_context
def pipeline_resume(ctx: click.Context, checkpoint_path: str, domain: str | None) -> None:
    """Resume a paused pipeline from checkpoint."""
    from ..harness_registry import create_harness_registry
    from ..orchestration_harness import create_orchestration_harness

    checkpoint_file = Path(checkpoint_path)

    try:
        with checkpoint_file.open() as f:
            checkpoint_data = json.load(f)
    except json.JSONDecodeError as e:
        click.echo(f"Error: Invalid checkpoint file: {e}", err=True)
        raise SystemExit(1)

    if not domain:
        domain = checkpoint_data.get("context", {}).get("domain")

    click.echo(f"Resuming pipeline from: {checkpoint_file.name}")
    if domain:
        click.echo(f"Domain: {domain}")

    try:
        registry = create_harness_registry(domain=domain)
        harnesses = registry.get_for_orchestration()

        checkpoint_dir = checkpoint_file.parent
        orchestrator = create_orchestration_harness(
            checkpoint_dir=checkpoint_dir,
            harnesses=harnesses,
        )

        result = asyncio.run(orchestrator.resume(checkpoint_file))

        if result is None:
            click.echo("❌ Failed to resume pipeline - invalid checkpoint", err=True)
            raise SystemExit(1)

        if result.success:
            click.echo("\n✅ Pipeline completed successfully")
            click.echo(f"   Pipeline: {result.pipeline_name}")
            click.echo(f"   Duration: {result.duration_seconds:.1f}s")
            click.echo(
                f"   Steps completed: {len([s for s in result.step_results if s.status.value == 'completed'])}"
            )

            if result.context:
                click.echo("\nFinal Context:")
                for key, value in result.context.items():
                    value_str = str(value)
                    if len(value_str) > 80:
                        value_str = value_str[:77] + "..."
                    click.echo(f"  {key}: {value_str}")
        else:
            click.echo(f"\n⚠️  Pipeline failed: {result.error}")
            click.echo(f"   Pipeline: {result.pipeline_name}")
            click.echo(f"   Duration: {result.duration_seconds:.1f}s")

            if result.checkpoint_path:
                click.echo("\n   Pipeline paused - checkpoint saved:")
                click.echo(f"   {result.checkpoint_path}")

            raise SystemExit(1)

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
