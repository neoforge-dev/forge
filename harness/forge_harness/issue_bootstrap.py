"""
Issue Bootstrap for FORGE Projects
===================================

Creates GitHub Issues from a project's PLAN.md file.
This is the "missing link" for from-scratch implementations:

    User writes spec → intake-mvp.sh scaffold → issue_bootstrap.py → forge_harness agent

Usage:
    from forge_harness.issue_bootstrap import bootstrap_project

    result = bootstrap_project(
        domain="thebrightharbor-com",
        project="kiddo-rewards",
        forge_root=Path("/path/to/FORGE"),
        github_repo="myorg/forge",
    )
"""

from dataclasses import dataclass, field
from pathlib import Path

from .github_client import (
    GitHubClient,
    Issue,
    create_meta_issue,
    epic_to_issue,
    parse_epics_from_plan,
)


@dataclass
class BootstrapResult:
    """Result of project bootstrapping."""

    success: bool
    domain: str
    project: str
    meta_issue_number: int | None
    epic_issues: list[dict[str, int | str]]  # [{"number": 1, "title": "...", "epic": "1"}]
    total_issues_created: int
    labels_created: list[str]
    errors: list[str] = field(default_factory=list)


def bootstrap_project(
    domain: str,
    project: str,
    forge_root: Path,
    github_repo: str,
    project_name: str | None = None,
    dry_run: bool = False,
) -> BootstrapResult:
    """
    Bootstrap a FORGE project by creating GitHub Issues from PLAN.md.

    This function reads the project's PLAN.md file, parses all epics,
    and creates corresponding GitHub Issues plus a META tracking issue.

    Args:
        domain: FORGE domain (e.g., "thebrightharbor-com")
        project: Project name (e.g., "kiddo-rewards")
        forge_root: Path to FORGE repository root
        github_repo: GitHub repository in owner/repo format
        project_name: Human-readable project name (defaults to project)
        dry_run: If True, don't create issues, just return what would be created

    Returns:
        BootstrapResult with created issue numbers and status

    Example:
        >>> result = bootstrap_project(
        ...     domain="thebrightharbor-com",
        ...     project="kiddo-rewards",
        ...     forge_root=Path("/home/user/FORGE"),
        ...     github_repo="myorg/forge",
        ... )
        >>> print(f"Created {result.total_issues_created} issues")
        >>> print(f"META issue: #{result.meta_issue_number}")
    """
    errors: list[str] = []
    epic_issues: list[dict[str, int | str]] = []
    labels_created: list[str] = []
    meta_issue_number: int | None = None

    # Resolve paths
    project_dir = forge_root / domain / project
    plan_path = project_dir / "docs" / "PLAN.md"

    # Validate project exists
    if not project_dir.exists():
        return BootstrapResult(
            success=False,
            domain=domain,
            project=project,
            meta_issue_number=None,
            epic_issues=[],
            total_issues_created=0,
            labels_created=[],
            errors=[f"Project directory not found: {project_dir}"],
        )

    # Validate PLAN.md exists
    if not plan_path.exists():
        return BootstrapResult(
            success=False,
            domain=domain,
            project=project,
            meta_issue_number=None,
            epic_issues=[],
            total_issues_created=0,
            labels_created=[],
            errors=[f"PLAN.md not found: {plan_path}"],
        )

    # Read and parse PLAN.md
    plan_content = plan_path.read_text()
    epics = parse_epics_from_plan(plan_content, source_path=plan_path)

    if not epics:
        return BootstrapResult(
            success=False,
            domain=domain,
            project=project,
            meta_issue_number=None,
            epic_issues=[],
            total_issues_created=0,
            labels_created=[],
            errors=["No epics found in PLAN.md. Expected format: '## Epic N: Title'"],
        )

    # Initialize GitHub client
    if not dry_run:
        try:
            github = GitHubClient(github_repo)
        except RuntimeError as e:
            return BootstrapResult(
                success=False,
                domain=domain,
                project=project,
                meta_issue_number=None,
                epic_issues=[],
                total_issues_created=0,
                labels_created=[],
                errors=[f"GitHub client error: {e}"],
            )

        # Ensure required labels exist
        required_labels = _get_required_labels(domain, epics, project)
        try:
            github.ensure_labels_exist(required_labels)
            labels_created = required_labels
        except Exception as e:
            errors.append(f"Warning: Could not create all labels: {e}")

    # Convert epics to issues
    issues_to_create: list[Issue] = []
    for epic in epics:
        issue = epic_to_issue(epic, domain, project, plan_path)
        issues_to_create.append(issue)

    # Create META tracking issue first
    display_name = project_name or _format_project_name(project)
    meta_issue = create_meta_issue(domain, project, display_name, len(issues_to_create))

    if dry_run:
        # Return what would be created
        return BootstrapResult(
            success=True,
            domain=domain,
            project=project,
            meta_issue_number=None,
            epic_issues=[
                {"number": 0, "title": issue.title, "epic": str(epic.number)}
                for issue, epic in zip(issues_to_create, epics)
            ],
            total_issues_created=len(issues_to_create) + 1,  # +1 for META
            labels_created=_get_required_labels(domain, epics, project),
            errors=["DRY RUN: No issues created"],
        )

    # Create META issue
    try:
        meta_issue_number = github.create_issue(meta_issue)
    except Exception as e:
        errors.append(f"Failed to create META issue: {e}")

    # Create epic issues
    for issue, epic in zip(issues_to_create, epics):
        try:
            issue_number = github.create_issue(issue)
            epic_issues.append(
                {
                    "number": issue_number,
                    "title": issue.title,
                    "epic": str(epic.number),
                }
            )
        except Exception as e:
            errors.append(f"Failed to create issue for Epic {epic.number}: {e}")

    # Update META issue with links to created issues
    if meta_issue_number and epic_issues:
        try:
            issue_links = "\n".join(
                f"- #{item['number']} - {item['title']}" for item in epic_issues
            )
            github.add_comment(meta_issue_number, f"## Linked Issues\n\n{issue_links}")
        except Exception as e:
            errors.append(f"Warning: Could not update META issue: {e}")

    return BootstrapResult(
        success=len(epic_issues) > 0,
        domain=domain,
        project=project,
        meta_issue_number=meta_issue_number,
        epic_issues=epic_issues,
        total_issues_created=len(epic_issues) + (1 if meta_issue_number else 0),
        labels_created=labels_created,
        errors=errors if errors else [],
    )


def _get_required_labels(domain: str, epics: list, project: str = "") -> list[str]:
    """Get all labels needed for the project's issues."""
    labels = [
        # Priority labels
        "priority:critical",
        "priority:high",
        "priority:medium",
        "priority:low",
        # Type labels
        "type:feature",
        "type:bug",
        "type:infra",
        "type:docs",
        # Status labels
        "status:in-progress",
        "status:blocked",
        "status:verification-failed",
        # Meta labels
        "agent:autonomous",
        "meta",
        "tracking",
        "needs-human-review",
        # Domain label
        f"domain:{domain}",
    ]
    # Project label (critical for multi-project domains)
    if project:
        labels.append(f"project:{project}")
    return labels


def _format_project_name(project: str) -> str:
    """Convert project slug to human-readable name."""
    return project.replace("-", " ").replace("_", " ").title()


def validate_plan_md(plan_path: Path) -> dict[str, list[str]]:
    """
    Validate a PLAN.md file for bootstrap readiness.

    Returns a dict with 'errors' and 'warnings' lists.
    """
    result = {"errors": [], "warnings": []}

    if not plan_path.exists():
        result["errors"].append(f"PLAN.md not found: {plan_path}")
        return result

    content = plan_path.read_text()

    # Check for epics
    epics = parse_epics_from_plan(content, source_path=plan_path)
    if not epics:
        result["errors"].append("No epics found. Expected '## Epic N: Title' format.")
        return result

    # Check each epic for required fields
    for epic in epics:
        if not epic.description:
            result["warnings"].append(f"Epic {epic.number}: Missing description/rationale")
        if not epic.acceptance_criteria:
            result["warnings"].append(f"Epic {epic.number}: Missing acceptance criteria")
        if not epic.ice_score:
            result["warnings"].append(f"Epic {epic.number}: Missing ICE score")
        if not epic.effort_hours:
            result["warnings"].append(f"Epic {epic.number}: Missing effort estimate")

    return result


def list_epics(plan_path: Path) -> list[dict]:
    """
    List epics from a PLAN.md file without creating issues.

    Returns a list of dicts with epic summary info.
    """
    if not plan_path.exists():
        return []

    content = plan_path.read_text()
    epics = parse_epics_from_plan(content, source_path=plan_path)

    return [
        {
            "number": epic.number,
            "title": epic.title,
            "priority": epic.priority.value,
            "ice_score": f"{epic.ice_score[0]}/{epic.ice_score[1]}/{epic.ice_score[2]}"
            if epic.ice_score
            else "N/A",
            "effort": f"{epic.effort_hours}h" if epic.effort_hours else "N/A",
            "criteria_count": len(epic.acceptance_criteria),
            "phases_count": len(epic.phases),
        }
        for epic in epics
    ]
