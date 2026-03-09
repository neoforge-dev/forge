"""
GitHub Issues Integration
=========================

Replaces Linear with GitHub Issues for task tracking.
Uses the gh CLI for direct GitHub API access.

Issue Label Convention:
- priority:critical / priority:high / priority:medium / priority:low
- type:feature / type:bug / type:infra / type:docs
- domain:{domain-name}
- status:in-progress / status:blocked
- agent:autonomous

Requirements:
- gh CLI installed and authenticated (`gh auth login`)
"""

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class IssuePriority(Enum):
    """Issue priority levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IssueType(Enum):
    """Issue types."""

    FEATURE = "feature"
    BUG = "bug"
    INFRA = "infra"
    DOCS = "docs"


class IssueStatus(Enum):
    """Issue status (via labels)."""

    OPEN = "open"
    IN_PROGRESS = "in-progress"
    BLOCKED = "blocked"
    CLOSED = "closed"


@dataclass
class Issue:
    """Represents a GitHub Issue."""

    number: int | None
    title: str
    body: str
    labels: list[str] = field(default_factory=list)
    priority: IssuePriority = IssuePriority.MEDIUM
    issue_type: IssueType = IssueType.FEATURE
    domain: str | None = None
    project: str | None = None  # Project label for multi-project domains
    status: IssueStatus = IssueStatus.OPEN
    created_at: datetime | None = None
    updated_at: datetime | None = None
    assignee: str | None = None
    milestone: str | None = None

    def to_github_labels(self) -> list[str]:
        """Convert to GitHub label format."""
        labels = list(self.labels)

        # Add priority label
        labels.append(f"priority:{self.priority.value}")

        # Add type label
        labels.append(f"type:{self.issue_type.value}")

        # Add domain label
        if self.domain:
            labels.append(f"domain:{self.domain}")

        # Add project label (critical for multi-project domains)
        if self.project:
            labels.append(f"project:{self.project}")

        # Add status label (if not open)
        if self.status == IssueStatus.IN_PROGRESS:
            labels.append("status:in-progress")
        elif self.status == IssueStatus.BLOCKED:
            labels.append("status:blocked")

        # Mark as autonomous
        labels.append("agent:autonomous")

        return labels


@dataclass
class Epic:
    """An epic parsed from PLAN.md."""

    number: int
    title: str
    description: str
    ice_score: tuple[int, int, int] | None  # Impact, Confidence, Ease
    priority: IssuePriority
    effort_hours: int | None
    acceptance_criteria: list[str]
    phases: list[dict[str, Any]]
    source_line: int | None = None


class GitHubClient:
    """
    GitHub client using gh CLI.

    Requires gh CLI to be installed and authenticated.
    """

    def __init__(self, repo: str):
        """
        Initialize GitHub client.

        Args:
            repo: Repository in owner/name format (e.g., "myorg/myrepo")
        """
        self.repo = repo
        self._verify_gh_cli()

    def _verify_gh_cli(self) -> None:
        """Verify gh CLI is available and authenticated."""
        try:
            result = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                raise RuntimeError("gh CLI not authenticated. Run 'gh auth login' first.")
        except FileNotFoundError:
            raise RuntimeError("gh CLI not found. Install from https://cli.github.com/")

    def _run_gh(
        self,
        args: list[str],
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run a gh CLI command."""
        cmd = ["gh"] + args + ["--repo", self.repo]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"gh command failed: {result.stderr}")
        return result

    def create_issue(self, issue: Issue) -> int:
        """
        Create a GitHub issue.

        Args:
            issue: Issue to create

        Returns:
            Issue number
        """
        args = [
            "issue",
            "create",
            "--title",
            issue.title,
            "--body",
            issue.body,
        ]

        # Add labels
        labels = issue.to_github_labels()
        if labels:
            args.extend(["--label", ",".join(labels)])

        # Add assignee if specified
        if issue.assignee:
            args.extend(["--assignee", issue.assignee])

        # Add milestone if specified
        if issue.milestone:
            args.extend(["--milestone", issue.milestone])

        result = self._run_gh(args)

        # Parse issue number from URL output
        # Output format: https://github.com/owner/repo/issues/123
        url = result.stdout.strip()
        issue_number = int(url.split("/")[-1])

        return issue_number

    def get_issue(self, issue_number: int) -> dict[str, Any]:
        """
        Get issue details.

        Args:
            issue_number: Issue number

        Returns:
            Issue data as dict
        """
        result = self._run_gh(
            [
                "issue",
                "view",
                str(issue_number),
                "--json",
                "number,title,body,labels,state,assignees,milestone,createdAt,updatedAt",
            ]
        )
        return json.loads(result.stdout)

    def list_issues(
        self,
        state: str = "open",
        labels: list[str] | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """
        List issues.

        Args:
            state: Issue state (open, closed, all)
            labels: Filter by labels
            limit: Maximum number of issues

        Returns:
            List of issue data dicts
        """
        args = [
            "issue",
            "list",
            "--state",
            state,
            "--limit",
            str(limit),
            "--json",
            "number,title,labels,state,assignees,createdAt",
        ]

        if labels:
            args.extend(["--label", ",".join(labels)])

        result = self._run_gh(args)
        return json.loads(result.stdout)

    def update_issue(
        self,
        issue_number: int,
        title: str | None = None,
        body: str | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> None:
        """
        Update an issue.

        Args:
            issue_number: Issue number
            title: New title (optional)
            body: New body (optional)
            add_labels: Labels to add (optional)
            remove_labels: Labels to remove (optional)
        """
        args = ["issue", "edit", str(issue_number)]

        if title:
            args.extend(["--title", title])
        if body:
            args.extend(["--body", body])
        if add_labels:
            args.extend(["--add-label", ",".join(add_labels)])
        if remove_labels:
            args.extend(["--remove-label", ",".join(remove_labels)])

        self._run_gh(args)

    def close_issue(self, issue_number: int, reason: str = "completed") -> None:
        """
        Close an issue.

        Args:
            issue_number: Issue number
            reason: Close reason (completed, not_planned)
        """
        self._run_gh(
            [
                "issue",
                "close",
                str(issue_number),
                "--reason",
                reason,
            ]
        )

    def reopen_issue(self, issue_number: int) -> None:
        """Reopen a closed issue."""
        self._run_gh(["issue", "reopen", str(issue_number)])

    def add_comment(self, issue_number: int, body: str) -> str:
        """
        Add a comment to an issue.

        Args:
            issue_number: Issue number
            body: Comment body

        Returns:
            Comment URL
        """
        result = self._run_gh(
            [
                "issue",
                "comment",
                str(issue_number),
                "--body",
                body,
            ]
        )
        return result.stdout.strip()

    def list_comments(self, issue_number: int) -> list[dict[str, Any]]:
        """
        List comments on an issue.

        Args:
            issue_number: Issue number

        Returns:
            List of comment data dicts
        """
        result = self._run_gh(
            [
                "issue",
                "view",
                str(issue_number),
                "--json",
                "comments",
            ]
        )
        data = json.loads(result.stdout)
        return data.get("comments", [])

    def search_issues(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        """
        Search issues.

        Args:
            query: Search query (GitHub search syntax)
            limit: Maximum results

        Returns:
            List of matching issue data dicts
        """
        # Construct full query with repo filter
        full_query = f"repo:{self.repo} {query}"

        result = subprocess.run(
            [
                "gh",
                "search",
                "issues",
                "--json",
                "number,title,labels,state,repository",
                "--limit",
                str(limit),
                full_query,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise RuntimeError(f"gh search failed: {result.stderr}")

        return json.loads(result.stdout)

    def ensure_labels_exist(self, labels: list[str]) -> None:
        """
        Ensure all required labels exist in the repository.

        Creates missing labels with default colors.
        """
        # Get existing labels
        result = self._run_gh(
            [
                "label",
                "list",
                "--json",
                "name",
                "--limit",
                "200",
            ]
        )
        existing = {label["name"] for label in json.loads(result.stdout)}

        # Define label colors
        label_colors = {
            "priority:critical": "B60205",
            "priority:high": "D93F0B",
            "priority:medium": "FBCA04",
            "priority:low": "0E8A16",
            "type:feature": "1D76DB",
            "type:bug": "E11D21",
            "type:infra": "5319E7",
            "type:docs": "0052CC",
            "status:in-progress": "FEF2C0",
            "status:blocked": "F9D0C4",
            "agent:autonomous": "BFD4F2",
            "meta": "EDEDED",
            "tracking": "C5DEF5",
        }

        # Create missing labels
        for label in labels:
            if label not in existing:
                color = label_colors.get(label, "EDEDED")

                # Handle domain labels
                if label.startswith("domain:"):
                    color = "D4C5F9"

                # Handle project labels (slightly different shade)
                if label.startswith("project:"):
                    color = "C2E0C6"

                self._run_gh(
                    [
                        "label",
                        "create",
                        label,
                        "--color",
                        color,
                        "--force",  # Don't error if exists
                    ],
                    check=False,
                )


# Epic parsing functions (kept from original)

import re


def parse_epics_from_plan(plan_content: str, source_path: Path | None = None) -> list[Epic]:
    """
    Parse epics from a PLAN.md file.

    Expected format:
    ## Epic N: Title
    **ICE Score**: I/C/E
    **Priority**: P0/P1/P2
    **Effort**: Xh

    ### Rationale
    ...

    ### Acceptance Criteria
    - [ ] Criterion 1
    - [ ] Criterion 2

    ### Implementation Plan
    #### Phase N.1: Phase Title
    ...
    """
    epics = []

    # Split by epic headers
    epic_pattern = r"##\s*Epic\s+(\d+)(?:\.\d+)?:\s*(.+?)(?=\n##\s*Epic|\n##\s*[A-Z]|\Z)"
    epic_matches = list(re.finditer(epic_pattern, plan_content, re.DOTALL | re.IGNORECASE))

    for match in epic_matches:
        epic_num = int(match.group(1))
        epic_title = match.group(2).split("\n")[0].strip()
        epic_content = match.group(0)

        # Extract ICE score
        ice_match = re.search(r"\*\*ICE\s+Score\*\*:\s*(\d+)/(\d+)/(\d+)", epic_content)
        ice_score = None
        if ice_match:
            ice_score = (
                int(ice_match.group(1)),
                int(ice_match.group(2)),
                int(ice_match.group(3)),
            )

        # Extract priority
        priority = IssuePriority.MEDIUM
        priority_match = re.search(
            r"\*\*Priority\*\*:\s*(P\d|CRITICAL|HIGH|MEDIUM|LOW)", epic_content, re.IGNORECASE
        )
        if priority_match:
            p = priority_match.group(1).upper()
            if p in ("P0", "CRITICAL"):
                priority = IssuePriority.CRITICAL
            elif p in ("P1", "HIGH"):
                priority = IssuePriority.HIGH
            elif p in ("P2", "MEDIUM"):
                priority = IssuePriority.MEDIUM
            else:
                priority = IssuePriority.LOW

        # Extract effort
        effort_hours = None
        effort_match = re.search(r"\*\*Effort\*\*:\s*(\d+)(?:-\d+)?h", epic_content)
        if effort_match:
            effort_hours = int(effort_match.group(1))

        # Extract description (from Rationale section or first paragraph)
        description = ""
        rationale_match = re.search(
            r"###\s*Rationale\s*\n(.*?)(?=\n###|\Z)",
            epic_content,
            re.DOTALL | re.IGNORECASE,
        )
        if rationale_match:
            description = rationale_match.group(1).strip()
        else:
            # Use first non-header paragraph
            for line in epic_content.split("\n"):
                if line.strip() and not line.startswith("#") and not line.startswith("**"):
                    description = line.strip()
                    break

        # Extract acceptance criteria
        acceptance_criteria = []
        criteria_match = re.search(
            r"###\s*(?:Acceptance\s+)?Criteria\s*\n((?:[-*\[\]x ]+.+\n)+)",
            epic_content,
            re.IGNORECASE,
        )
        if criteria_match:
            for line in criteria_match.group(1).strip().split("\n"):
                criterion = re.sub(r"^[-*]\s*\[[ x]\]\s*", "", line).strip()
                if criterion:
                    acceptance_criteria.append(criterion)

        # Extract phases
        phases = []
        phase_pattern = r"####\s*Phase\s+(\d+\.\d+):\s*(.+?)(?=\n####|\n###|\Z)"
        for phase_match in re.finditer(phase_pattern, epic_content, re.DOTALL):
            phase_num = phase_match.group(1)
            phase_title = phase_match.group(2).split("\n")[0].strip()
            phase_content = phase_match.group(2)

            # Extract deliverables from phase
            deliverables = []
            for line in phase_content.split("\n"):
                if line.strip().startswith("-"):
                    deliverables.append(line.strip()[1:].strip())

            phases.append(
                {
                    "number": phase_num,
                    "title": phase_title,
                    "deliverables": deliverables,
                }
            )

        # Find source line number
        source_line = None
        if source_path:
            lines = plan_content.split("\n")
            for i, line in enumerate(lines):
                if f"Epic {epic_num}" in line and epic_title.split()[0] in line:
                    source_line = i + 1
                    break

        epics.append(
            Epic(
                number=epic_num,
                title=epic_title,
                description=description,
                ice_score=ice_score,
                priority=priority,
                effort_hours=effort_hours,
                acceptance_criteria=acceptance_criteria,
                phases=phases,
                source_line=source_line,
            )
        )

    return epics


def epic_to_issue(
    epic: Epic,
    domain: str,
    project: str,
    plan_path: Path | None = None,
) -> Issue:
    """Convert an Epic to a GitHub Issue."""

    # Build issue body
    body_parts = []

    # Source reference
    if plan_path and epic.source_line:
        body_parts.append(f"**Source:** `{domain}/{project}/docs/PLAN.md` line {epic.source_line}")

    # ICE score
    if epic.ice_score:
        body_parts.append(
            f"**ICE Score:** {epic.ice_score[0]}/{epic.ice_score[1]}/{epic.ice_score[2]}"
        )

    # Effort
    if epic.effort_hours:
        body_parts.append(f"**Effort:** {epic.effort_hours}h")

    body_parts.append("")  # Blank line

    # Description
    if epic.description:
        body_parts.append("## Description")
        body_parts.append(epic.description)
        body_parts.append("")

    # Acceptance criteria
    if epic.acceptance_criteria:
        body_parts.append("## Acceptance Criteria")
        for criterion in epic.acceptance_criteria:
            body_parts.append(f"- [ ] {criterion}")
        body_parts.append("")

    # Phases as test steps
    if epic.phases:
        body_parts.append("## Implementation Phases")
        for phase in epic.phases:
            body_parts.append(f"\n### Phase {phase['number']}: {phase['title']}")
            for deliverable in phase.get("deliverables", []):
                body_parts.append(f"- [ ] {deliverable}")
        body_parts.append("")

    return Issue(
        number=None,  # Will be assigned by GitHub
        title=f"[Epic {epic.number}] {epic.title}",
        body="\n".join(body_parts),
        priority=epic.priority,
        issue_type=IssueType.FEATURE,
        domain=domain,
        project=project,  # Project label for multi-project domains
    )


def create_meta_issue(
    domain: str,
    project: str,
    project_name: str,
    total_issues: int,
) -> Issue:
    """Create the META tracking issue for a project."""

    body = f"""## Project Overview
**Domain:** {domain}
**Project:** {project}
**Name:** {project_name}

## Session Tracking
This issue is used for session handoff between autonomous coding agents.
Each agent should add a comment summarizing their session.

## Progress Milestones
- [ ] Project initialized
- [ ] Core infrastructure working
- [ ] Primary features implemented
- [ ] All {total_issues} issues complete
- [ ] Deployed to production

## Session Log
_Sessions will be logged as comments below._
"""

    return Issue(
        number=None,
        title=f"[META] {project_name} - Autonomous Progress Tracker",
        body=body,
        labels=["meta", "tracking"],
        priority=IssuePriority.HIGH,
        issue_type=IssueType.DOCS,
        domain=domain,
        project=project,  # Project label for multi-project domains
    )


def format_session_summary(
    session_num: int,
    issues_completed: list[str],
    issues_in_progress: list[str],
    tests_status: dict[str, int],
    notes: str | None = None,
    next_recommendations: list[str] | None = None,
) -> str:
    """Format a session summary comment for the META issue."""

    lines = [f"## Session {session_num} Complete\n"]
    lines.append(f"**Timestamp:** {datetime.now().isoformat()}\n")

    # Completed
    if issues_completed:
        lines.append("\n### Completed")
        for issue in issues_completed:
            lines.append(f"- {issue}")

    # In progress
    if issues_in_progress:
        lines.append("\n### In Progress")
        for issue in issues_in_progress:
            lines.append(f"- {issue}")

    # Tests
    if tests_status:
        lines.append("\n### Test Status")
        for key, value in tests_status.items():
            lines.append(f"- **{key}:** {value}")

    # Notes
    if notes:
        lines.append(f"\n### Notes\n{notes}")

    # Recommendations
    if next_recommendations:
        lines.append("\n### Recommendations for Next Session")
        for rec in next_recommendations:
            lines.append(f"- {rec}")

    return "\n".join(lines)
