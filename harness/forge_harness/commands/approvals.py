"""Approvals command group."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC
from pathlib import Path

import click

# Default storage dir: when this is used, we delegate to create_approval_queue_from_env()
# so FORGE_APPROVALS_V3=1 and other env-driven backends (Redis, Notion) are used.
DEFAULT_STORAGE_DIR = ".forge/approvals"


def _get_approval_queue(storage_dir: str):
    """Return ApprovalQueueHarness: env-driven when storage_dir is default, else file-backed."""
    from ..approval_queue import create_approval_queue, create_approval_queue_from_env

    if storage_dir == DEFAULT_STORAGE_DIR or not storage_dir:
        return create_approval_queue_from_env()
    return create_approval_queue(storage_dir=Path(storage_dir))


@click.group()
@click.pass_context
def approvals(ctx: click.Context) -> None:
    """Manage approval queue for human-in-the-loop workflows."""
    pass


@approvals.command("list")
@click.option("--domain", "-d", help="Filter by domain")
@click.option(
    "--type", "-t", "approval_type", help="Filter by type (content, deploy, feature, etc.)"
)
@click.option("--all", "show_all", is_flag=True, help="Include resolved approvals")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.option(
    "--storage-dir",
    type=click.Path(),
    default=DEFAULT_STORAGE_DIR,
    help="Approval storage directory",
)
@click.pass_context
def approvals_list(
    ctx: click.Context,
    domain: str | None,
    approval_type: str | None,
    show_all: bool,
    json_output: bool,
    storage_dir: str,
) -> None:
    """List pending approval requests."""
    from ..approval_queue import ApprovalType

    queue = _get_approval_queue(storage_dir)

    type_filter = None
    if approval_type:
        try:
            type_filter = ApprovalType(approval_type.lower())
        except ValueError:
            valid_types = ", ".join(t.value for t in ApprovalType)
            message = f"Invalid type '{approval_type}'. Valid types: {valid_types}"
            if json_output:
                click.echo(json.dumps({"status": "error", "error": message}))
            else:
                click.echo(f"Error: {message}", err=True)
            raise SystemExit(1)

    try:
        if show_all:
            requests = asyncio.run(queue.storage.list_all())
            if domain:
                requests = [r for r in requests if r.domain == domain]
            if type_filter:
                requests = [r for r in requests if r.type == type_filter]
        else:
            requests = asyncio.run(queue.list_pending(domain=domain, type=type_filter))
    except Exception as e:
        if json_output:
            click.echo(json.dumps({"status": "error", "error": str(e)}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    if json_output:
        output = [r.to_dict() for r in requests]
        click.echo(json.dumps(output, indent=2, default=str))
        return

    if not requests:
        click.echo("No pending approval requests.")
        return

    click.echo(f"\n{'ID':<16} {'Type':<10} {'Domain':<25} {'Title':<35} {'Status':<10} {'Age'}")
    click.echo("-" * 115)

    for req in requests:
        status_icon = {
            "pending": "⏳",
            "approved": "✅",
            "rejected": "❌",
            "expired": "⏰",
            "cancelled": "🚫",
        }.get(req.status.value, "?")

        age_str = f"{req.age_hours:.1f}h"
        title = req.title[:33] + ".." if len(req.title) > 35 else req.title
        domain_str = req.domain[:23] + ".." if len(req.domain) > 25 else req.domain

        click.echo(
            f"{req.id:<16} {req.type.value:<10} {domain_str:<25} {title:<35} {status_icon} {req.status.value:<9} {age_str}"
        )

    click.echo(f"\nTotal: {len(requests)} requests")


@approvals.command("show")
@click.argument("request_id")
@click.option(
    "--storage-dir",
    type=click.Path(),
    default=DEFAULT_STORAGE_DIR,
    help="Approval storage directory",
)
@click.pass_context
def approvals_show(ctx: click.Context, request_id: str, storage_dir: str) -> None:
    """Show details of an approval request."""

    queue = _get_approval_queue(storage_dir)
    request = asyncio.run(queue.get_request(request_id))

    if not request:
        click.echo(f"Error: Approval request not found: {request_id}", err=True)
        raise SystemExit(1)

    click.echo(f"\nApproval Request: {request.id}")
    click.echo("=" * 60)
    click.echo(f"Type:        {request.type.value}")
    click.echo(f"Domain:      {request.domain}")
    click.echo(f"Title:       {request.title}")
    click.echo(f"Status:      {request.status.value}")
    click.echo(f"Priority:    {request.priority.value}")
    click.echo(f"Created:     {request.created_at.isoformat()}")

    if request.expires_at:
        click.echo(f"Expires:     {request.expires_at.isoformat()}")

    if request.approved_by:
        click.echo(f"Resolved by: {request.approved_by}")

    if request.resolved_at:
        click.echo(f"Resolved:    {request.resolved_at.isoformat()}")

    if request.resolution_reason:
        click.echo(f"Reason:      {request.resolution_reason}")

    click.echo(f"\nDescription:\n{request.description}")

    if request.metadata:
        click.echo("\nMetadata:")
        for key, value in request.metadata.items():
            click.echo(f"  {key}: {value}")

    if request.workflow_checkpoint:
        click.echo(f"\nWorkflow checkpoint: {request.workflow_checkpoint}")

    if request.tags:
        click.echo(f"Tags: {', '.join(request.tags)}")


@approvals.command("approve")
@click.argument("request_id")
@click.option("--approver", "-a", default="cli-user", help="Approver identifier")
@click.option("--comment", "-c", help="Optional approval comment")
@click.option(
    "--resume/--no-resume", default=True, help="Auto-resume associated pipeline (default: yes)"
)
@click.option(
    "--storage-dir",
    type=click.Path(),
    default=DEFAULT_STORAGE_DIR,
    help="Approval storage directory",
)
@click.option(
    "--checkpoint-dir",
    type=click.Path(),
    default=".forge/orchestration_checkpoints",
    help="Pipeline checkpoint directory",
)
@click.pass_context
def approvals_approve(
    ctx: click.Context,
    request_id: str,
    approver: str,
    comment: str | None,
    resume: bool,
    storage_dir: str,
    checkpoint_dir: str,
) -> None:
    """Approve a pending request and optionally resume associated pipeline."""

    queue = _get_approval_queue(storage_dir)

    try:
        approved = asyncio.run(queue.approve(request_id, approver, comment))
        click.echo(f"✅ Approved: {approved.title}")
        click.echo(f"   ID: {approved.id}")
        click.echo(f"   Domain: {approved.domain}")
        click.echo(f"   Approved by: {approver}")

        if approved.workflow_checkpoint:
            if resume:
                click.echo("\n   Resuming pipeline from checkpoint...")
                try:
                    from ..harness_registry import create_harness_registry
                    from ..orchestration_harness import create_orchestration_harness

                    registry = create_harness_registry(domain=approved.domain)
                    harnesses = registry.get_for_orchestration()

                    orchestrator = create_orchestration_harness(
                        checkpoint_dir=Path(checkpoint_dir),
                        harnesses=harnesses,
                    )

                    result = asyncio.run(orchestrator.resume(Path(approved.workflow_checkpoint)))

                    if result and result.success:
                        click.echo("   ✅ Pipeline resumed and completed successfully")
                        click.echo(f"   Duration: {result.duration_seconds:.1f}s")
                    elif result:
                        click.echo(f"   ⚠️  Pipeline resumed but failed: {result.error}")
                    else:
                        click.echo("   ❌ Failed to resume pipeline from checkpoint")

                except Exception as e:
                    click.echo(f"   ❌ Auto-resume failed: {e}", err=True)
                    click.echo("\n   Manual resume command:")
                    click.echo(f"   forge-harness pipeline resume {approved.workflow_checkpoint}")
            else:
                click.echo("\n   To resume workflow manually:")
                click.echo(f"   forge-harness pipeline resume {approved.workflow_checkpoint}")

    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@approvals.command("reject")
@click.argument("request_id")
@click.option("--approver", "-a", default="cli-user", help="Rejector identifier")
@click.option("--reason", "-r", required=True, help="Reason for rejection")
@click.option(
    "--storage-dir",
    type=click.Path(),
    default=DEFAULT_STORAGE_DIR,
    help="Approval storage directory",
)
@click.pass_context
def approvals_reject(
    ctx: click.Context,
    request_id: str,
    approver: str,
    reason: str,
    storage_dir: str,
) -> None:
    """Reject a pending request."""

    queue = _get_approval_queue(storage_dir)

    try:
        rejected = asyncio.run(queue.reject(request_id, approver, reason))
        click.echo(f"❌ Rejected: {rejected.title}")
        click.echo(f"   ID: {rejected.id}")
        click.echo(f"   Domain: {rejected.domain}")
        click.echo(f"   Rejected by: {approver}")
        click.echo(f"   Reason: {reason}")

    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@approvals.command("cancel")
@click.argument("request_id")
@click.option("--reason", "-r", help="Optional cancellation reason")
@click.option(
    "--storage-dir",
    type=click.Path(),
    default=DEFAULT_STORAGE_DIR,
    help="Approval storage directory",
)
@click.pass_context
def approvals_cancel(
    ctx: click.Context,
    request_id: str,
    reason: str | None,
    storage_dir: str,
) -> None:
    """Cancel a pending request."""

    queue = _get_approval_queue(storage_dir)

    try:
        cancelled = asyncio.run(queue.cancel(request_id, reason))
        click.echo(f"🚫 Cancelled: {cancelled.title}")
        click.echo(f"   ID: {cancelled.id}")
        if reason:
            click.echo(f"   Reason: {reason}")

    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@approvals.command("stats")
@click.option(
    "--storage-dir",
    type=click.Path(),
    default=DEFAULT_STORAGE_DIR,
    help="Approval storage directory",
)
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.pass_context
def approvals_stats(ctx: click.Context, storage_dir: str, json_output: bool) -> None:
    """Show approval queue statistics."""
    from dataclasses import asdict


    queue = _get_approval_queue(storage_dir)
    stats = asyncio.run(queue.get_stats())

    if json_output:
        click.echo(json.dumps(asdict(stats), indent=2))
        return

    click.echo("\nApproval Queue Statistics")
    click.echo("=" * 40)
    click.echo(f"Total requests:      {stats.total_requests}")
    click.echo(f"Pending:             {stats.pending_count}")
    click.echo(f"Approved:            {stats.approved_count}")
    click.echo(f"Rejected:            {stats.rejected_count}")
    click.echo(f"Expired:             {stats.expired_count}")

    if stats.avg_resolution_hours > 0:
        click.echo(f"Avg resolution time: {stats.avg_resolution_hours:.1f}h")

    if stats.oldest_pending_hours:
        click.echo(f"Oldest pending:      {stats.oldest_pending_hours:.1f}h")

    if stats.by_type:
        click.echo("\nBy Type:")
        for type_name, count in stats.by_type.items():
            click.echo(f"  {type_name}: {count}")

    if stats.by_domain:
        click.echo("\nBy Domain:")
        for domain, count in sorted(stats.by_domain.items(), key=lambda x: -x[1]):
            click.echo(f"  {domain}: {count}")


@approvals.command("create")
@click.option(
    "--type",
    "-t",
    "approval_type",
    required=True,
    type=click.Choice(["feature", "deploy", "content", "config", "data", "security", "compliance"]),
    help="Type of approval request",
)
@click.option("--title", required=True, help="Short title for the request")
@click.option("--description", "-d", default="", help="Detailed description")
@click.option("--domain", default="forge-harness", help="Domain this request relates to")
@click.option(
    "--priority",
    "-p",
    type=click.Choice(["low", "normal", "high", "critical"]),
    default="normal",
    help="Priority level",
)
@click.option(
    "--storage-dir",
    type=click.Path(),
    default=DEFAULT_STORAGE_DIR,
    help="Approval storage directory",
)
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.pass_context
def approvals_create(
    ctx: click.Context,
    approval_type: str,
    title: str,
    description: str,
    domain: str,
    priority: str,
    storage_dir: str,
    json_output: bool,
) -> None:
    """Create a new approval request."""
    from ..approval_queue import ApprovalPriority, ApprovalType

    type_map = {
        "feature": ApprovalType.FEATURE,
        "deploy": ApprovalType.DEPLOY,
        "content": ApprovalType.CONTENT,
        "config": ApprovalType.CONFIG,
        "data": ApprovalType.DATA,
        "security": ApprovalType.SECURITY,
        "compliance": ApprovalType.COMPLIANCE,
    }
    priority_map = {
        "low": ApprovalPriority.LOW,
        "normal": ApprovalPriority.NORMAL,
        "high": ApprovalPriority.HIGH,
        "critical": ApprovalPriority.CRITICAL,
    }

    queue = _get_approval_queue(storage_dir)
    request = asyncio.run(
        queue.create_request(
            type=type_map[approval_type],
            domain=domain,
            title=title,
            description=description or f"Approval request: {title}",
            priority=priority_map[priority],
        )
    )

    if json_output:
        click.echo(json.dumps(request.to_dict(), indent=2, default=str))
        return

    click.echo(f"\nApproval request created: {request.id}")
    click.echo(f"  Type:     {request.type.value}")
    click.echo(f"  Domain:   {request.domain}")
    click.echo(f"  Title:    {request.title}")
    click.echo(f"  Priority: {request.priority.value}")
    click.echo(f"  Status:   {request.status.value}")
    if request.expires_at:
        click.echo(f"  Expires:  {request.expires_at.isoformat()}")


@approvals.command("cleanup")
@click.option(
    "--days", type=int, default=30, help="Delete resolved requests older than this many days"
)
@click.option(
    "--storage-dir",
    type=click.Path(),
    default=DEFAULT_STORAGE_DIR,
    help="Approval storage directory",
)
@click.option("--dry-run", is_flag=True, help="Show what would be deleted without deleting")
@click.pass_context
def approvals_cleanup(
    ctx: click.Context,
    days: int,
    storage_dir: str,
    dry_run: bool,
) -> None:
    """Clean up old resolved approval requests."""
    from datetime import timedelta

    from ..approval_queue import ApprovalStatus

    queue = _get_approval_queue(storage_dir)

    if dry_run:
        from datetime import datetime

        cutoff = datetime.now(UTC) - timedelta(days=days)
        all_requests = asyncio.run(queue.storage.list_all())

        to_delete = []
        for request in all_requests:
            if request.status not in (ApprovalStatus.PENDING,):
                resolved_at = request.resolved_at or request.created_at
                if resolved_at < cutoff:
                    to_delete.append(request)

        if not to_delete:
            click.echo(f"No requests older than {days} days to clean up.")
            return

        click.echo(f"Would delete {len(to_delete)} requests:")
        for req in to_delete:
            click.echo(f"  {req.id}: {req.title} ({req.status.value})")
    else:
        deleted = asyncio.run(queue.cleanup_old_requests(days=days))
        click.echo(f"Cleaned up {deleted} old approval requests.")
