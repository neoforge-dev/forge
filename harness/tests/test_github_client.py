"""Tests for GitHub Issues integration module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.github_client import (
    Epic,
    GitHubClient,
    Issue,
    IssuePriority,
    IssueStatus,
    IssueType,
    create_meta_issue,
    epic_to_issue,
    format_session_summary,
    parse_epics_from_plan,
)


class TestIssue:
    """Tests for Issue dataclass."""

    def test_issue_defaults(self):
        """Issue has sensible defaults."""
        issue = Issue(
            number=None,
            title="Test Issue",
            body="Description",
        )

        assert issue.priority == IssuePriority.MEDIUM
        assert issue.issue_type == IssueType.FEATURE
        assert issue.status == IssueStatus.OPEN
        assert issue.labels == []

    def test_to_github_labels(self):
        """to_github_labels generates correct label format."""
        issue = Issue(
            number=1,
            title="Test",
            body="Body",
            priority=IssuePriority.HIGH,
            issue_type=IssueType.BUG,
            domain="codeswiftr-com",
        )

        labels = issue.to_github_labels()

        assert "priority:high" in labels
        assert "type:bug" in labels
        assert "domain:codeswiftr-com" in labels
        assert "agent:autonomous" in labels

    def test_to_github_labels_with_status(self):
        """Status labels added for non-open issues."""
        issue = Issue(
            number=1,
            title="Test",
            body="Body",
            status=IssueStatus.IN_PROGRESS,
        )

        labels = issue.to_github_labels()
        assert "status:in-progress" in labels

    def test_to_github_labels_blocked(self):
        """Blocked status adds blocked label."""
        issue = Issue(
            number=1,
            title="Test",
            body="Body",
            status=IssueStatus.BLOCKED,
        )

        labels = issue.to_github_labels()
        assert "status:blocked" in labels

    def test_to_github_labels_with_project(self):
        """Project label added when project specified."""
        issue = Issue(
            number=1,
            title="Test",
            body="Body",
            domain="leanvibe-ai",
            project="burnout-prevention",
        )

        labels = issue.to_github_labels()
        assert "project:burnout-prevention" in labels
        assert "domain:leanvibe-ai" in labels

    def test_to_github_labels_with_custom_labels(self):
        """Custom labels are preserved."""
        issue = Issue(
            number=1,
            title="Test",
            body="Body",
            labels=["custom-label", "another-label"],
        )

        labels = issue.to_github_labels()
        assert "custom-label" in labels
        assert "another-label" in labels
        assert "agent:autonomous" in labels

    def test_to_github_labels_open_status_no_label(self):
        """Open status does not add status label."""
        issue = Issue(
            number=1,
            title="Test",
            body="Body",
            status=IssueStatus.OPEN,
        )

        labels = issue.to_github_labels()
        # Should not have any status: prefix label
        status_labels = [l for l in labels if l.startswith("status:")]
        assert len(status_labels) == 0


class TestEpic:
    """Tests for Epic dataclass."""

    def test_epic_creation(self):
        """Epic can be created with all fields."""
        epic = Epic(
            number=1,
            title="Test Epic",
            description="Epic description",
            ice_score=(9, 8, 7),
            priority=IssuePriority.HIGH,
            effort_hours=16,
            acceptance_criteria=["Criterion 1", "Criterion 2"],
            phases=[{"number": "1.1", "title": "Phase 1", "deliverables": []}],
            source_line=42,
        )

        assert epic.number == 1
        assert epic.title == "Test Epic"
        assert epic.ice_score == (9, 8, 7)
        assert epic.priority == IssuePriority.HIGH
        assert epic.effort_hours == 16
        assert len(epic.acceptance_criteria) == 2
        assert len(epic.phases) == 1
        assert epic.source_line == 42

    def test_epic_minimal(self):
        """Epic can be created with minimal fields."""
        epic = Epic(
            number=1,
            title="Minimal",
            description="",
            ice_score=None,
            priority=IssuePriority.MEDIUM,
            effort_hours=None,
            acceptance_criteria=[],
            phases=[],
        )

        assert epic.number == 1
        assert epic.ice_score is None
        assert epic.effort_hours is None
        assert epic.source_line is None


class TestParseEpicsFromPlan:
    """Tests for PLAN.md epic parsing."""

    def test_parse_basic_epic(self):
        """Parse a basic epic structure."""
        plan_content = """# Project Plan

## Epic 1: User Authentication

**ICE Score**: 8/7/6
**Priority**: P1
**Effort**: 16h

### Rationale

Users need to log in securely.

### Acceptance Criteria
- [ ] User can register
- [ ] User can log in
- [ ] Password reset works

### Implementation Plan

#### Phase 1.1: Database Setup
- Create user model
- Add migrations
"""

        epics = parse_epics_from_plan(plan_content)

        assert len(epics) == 1
        epic = epics[0]
        assert epic.number == 1
        assert "Authentication" in epic.title
        assert epic.ice_score == (8, 7, 6)
        assert epic.priority == IssuePriority.HIGH
        assert epic.effort_hours == 16
        assert len(epic.acceptance_criteria) == 3
        assert len(epic.phases) >= 1

    def test_parse_multiple_epics(self):
        """Parse multiple epics from a plan."""
        plan_content = """# Plan

## Epic 1: First Feature

**Priority**: P0

### Rationale
First feature rationale.

## Epic 2: Second Feature

**Priority**: P2

### Rationale
Second feature rationale.

## Epic 3: Third Feature

**Priority**: LOW

### Rationale
Third feature rationale.
"""

        epics = parse_epics_from_plan(plan_content)

        assert len(epics) == 3
        assert epics[0].priority == IssuePriority.CRITICAL
        assert epics[1].priority == IssuePriority.MEDIUM
        assert epics[2].priority == IssuePriority.LOW

    def test_parse_epic_with_phases(self):
        """Parse epic phases correctly."""
        plan_content = """
## Epic 1: Multi-Phase Feature

### Implementation Plan

#### Phase 1.1: Setup
- Initialize project
- Configure CI

#### Phase 1.2: Core Logic
- Implement main algorithm
- Add tests

#### Phase 1.3: Polish
- Documentation
- Performance tuning
"""

        epics = parse_epics_from_plan(plan_content)

        assert len(epics) == 1
        epic = epics[0]
        assert len(epic.phases) == 3
        assert epic.phases[0]["number"] == "1.1"
        assert "Setup" in epic.phases[0]["title"]
        assert len(epic.phases[0]["deliverables"]) == 2

    def test_parse_effort_range(self):
        """Parse effort with range format."""
        plan_content = """
## Epic 1: Variable Effort

**Effort**: 8-16h
"""

        epics = parse_epics_from_plan(plan_content)

        assert epics[0].effort_hours == 8  # Uses lower bound

    def test_parse_empty_plan(self):
        """Empty plan returns no epics."""
        epics = parse_epics_from_plan("")
        assert epics == []

    def test_parse_plan_without_epics(self):
        """Plan without epic headers returns no epics."""
        plan_content = """# Project Plan

## Overview

Just a regular plan document.

## Features

- Feature 1
- Feature 2
"""
        epics = parse_epics_from_plan(plan_content)
        assert epics == []

    def test_parse_epic_with_decimal_number(self):
        """Epic with decimal number (e.g., Epic 1.2) is parsed correctly."""
        plan_content = """
## Epic 1.2: Sub-Epic Feature

**Priority**: P1
"""
        epics = parse_epics_from_plan(plan_content)

        assert len(epics) == 1
        assert epics[0].number == 1  # Takes first number

    def test_parse_epic_without_ice_score(self):
        """Epic without ICE score has None."""
        plan_content = """
## Epic 1: No ICE Score

**Priority**: P2
"""
        epics = parse_epics_from_plan(plan_content)

        assert epics[0].ice_score is None

    def test_parse_epic_without_effort(self):
        """Epic without effort has None."""
        plan_content = """
## Epic 1: No Effort

**Priority**: P1
"""
        epics = parse_epics_from_plan(plan_content)

        assert epics[0].effort_hours is None

    def test_parse_epic_with_missing_rationale(self):
        """Epic without Rationale section uses first paragraph."""
        plan_content = """
## Epic 1: Feature Without Rationale

This is the description from the first paragraph.

### Acceptance Criteria
- Criterion 1
"""
        epics = parse_epics_from_plan(plan_content)

        assert "first paragraph" in epics[0].description

    def test_parse_epic_with_checked_criteria(self):
        """Epic with checked criteria parses correctly."""
        plan_content = """
## Epic 1: With Progress

### Acceptance Criteria
- [x] Completed criterion
- [ ] Pending criterion
"""
        epics = parse_epics_from_plan(plan_content)

        assert len(epics[0].acceptance_criteria) == 2
        assert "Completed criterion" in epics[0].acceptance_criteria[0]

    def test_parse_epic_with_complex_phase_deliverables(self):
        """Epic with complex phase structure parses deliverables."""
        plan_content = """
## Epic 1: Complex Phases

### Implementation Plan

#### Phase 1.1: Setup
- Initialize project structure
- Configure dependencies
- Set up CI/CD pipeline

#### Phase 1.2: Implementation
- Build core features
- Write tests
"""
        epics = parse_epics_from_plan(plan_content)

        assert len(epics[0].phases) == 2
        assert len(epics[0].phases[0]["deliverables"]) == 3
        assert "Initialize project structure" in epics[0].phases[0]["deliverables"]

    def test_parse_epic_case_insensitive_priority(self):
        """Epic priority parsing is case insensitive."""
        plan_content = """
## Epic 1: Case Test

**priority**: p0
"""
        epics = parse_epics_from_plan(plan_content)

        assert epics[0].priority == IssuePriority.CRITICAL

    def test_parse_epic_with_source_line(self):
        """Epic parsing with source_path sets source_line."""
        plan_content = """# Plan

Some intro text.

## Epic 1: Tracked Epic

**Priority**: P1
"""
        source_path = Path("/test/PLAN.md")
        epics = parse_epics_from_plan(plan_content, source_path)

        assert epics[0].source_line is not None
        assert epics[0].source_line > 0


class TestEpicToIssue:
    """Tests for epic to issue conversion."""

    def test_basic_conversion(self):
        """Convert epic to issue with basic fields."""
        epic = Epic(
            number=5,
            title="API Integration",
            description="Integrate with external APIs.",
            ice_score=(9, 8, 7),
            priority=IssuePriority.HIGH,
            effort_hours=24,
            acceptance_criteria=["API connected", "Error handling"],
            phases=[],
        )

        issue = epic_to_issue(epic, "codeswiftr-com", "interview-simulator")

        assert issue.title == "[Epic 5] API Integration"
        assert "## Description" in issue.body
        assert "Integrate with external APIs" in issue.body
        assert "ICE Score" in issue.body
        assert issue.priority == IssuePriority.HIGH
        assert issue.domain == "codeswiftr-com"

    def test_conversion_with_acceptance_criteria(self):
        """Acceptance criteria appear as checkboxes."""
        epic = Epic(
            number=1,
            title="Test",
            description="Test epic",
            ice_score=None,
            priority=IssuePriority.MEDIUM,
            effort_hours=None,
            acceptance_criteria=["First criterion", "Second criterion"],
            phases=[],
        )

        issue = epic_to_issue(epic, "test-domain", "test-project")

        assert "## Acceptance Criteria" in issue.body
        assert "- [ ] First criterion" in issue.body
        assert "- [ ] Second criterion" in issue.body

    def test_conversion_with_phases(self):
        """Phases appear as implementation sections."""
        epic = Epic(
            number=1,
            title="Test",
            description="Test",
            ice_score=None,
            priority=IssuePriority.MEDIUM,
            effort_hours=None,
            acceptance_criteria=[],
            phases=[
                {"number": "1.1", "title": "Setup", "deliverables": ["Task 1", "Task 2"]},
                {"number": "1.2", "title": "Build", "deliverables": ["Task 3"]},
            ],
        )

        issue = epic_to_issue(epic, "test-domain", "test-project")

        assert "## Implementation Phases" in issue.body
        assert "### Phase 1.1: Setup" in issue.body
        assert "- [ ] Task 1" in issue.body

    def test_conversion_with_source_reference(self):
        """Source reference appears in issue body."""
        epic = Epic(
            number=1,
            title="Test",
            description="Test",
            ice_score=None,
            priority=IssuePriority.MEDIUM,
            effort_hours=None,
            acceptance_criteria=[],
            phases=[],
            source_line=42,
        )

        issue = epic_to_issue(
            epic,
            "codeswiftr-com",
            "interview-simulator",
            plan_path=Path("docs/PLAN.md"),
        )

        assert "line 42" in issue.body
        assert "PLAN.md" in issue.body


class TestCreateMetaIssue:
    """Tests for META tracking issue creation."""

    def test_meta_issue_format(self):
        """META issue has correct format."""
        issue = create_meta_issue(
            domain="codeswiftr-com",
            project="interview-simulator",
            project_name="Interview Simulator",
            total_issues=15,
        )

        assert "[META]" in issue.title
        assert "Interview Simulator" in issue.title
        assert "meta" in issue.labels
        assert "tracking" in issue.labels
        assert "codeswiftr-com" in issue.body
        assert "15 issues" in issue.body

    def test_meta_issue_priority(self):
        """META issue has HIGH priority."""
        issue = create_meta_issue(
            domain="test",
            project="test",
            project_name="Test",
            total_issues=5,
        )

        assert issue.priority == IssuePriority.HIGH
        assert issue.issue_type == IssueType.DOCS


class TestFormatSessionSummary:
    """Tests for session summary formatting."""

    def test_basic_summary(self):
        """Format basic session summary."""
        summary = format_session_summary(
            session_num=3,
            issues_completed=["#42 - Auth flow", "#43 - Login page"],
            issues_in_progress=["#44 - Dashboard"],
            tests_status={"backend": 45, "frontend": 30},
        )

        assert "## Session 3 Complete" in summary
        assert "#42 - Auth flow" in summary
        assert "#44 - Dashboard" in summary
        assert "**backend:** 45" in summary

    def test_summary_with_notes(self):
        """Summary includes notes when provided."""
        summary = format_session_summary(
            session_num=1,
            issues_completed=[],
            issues_in_progress=[],
            tests_status={},
            notes="Encountered rate limiting on API.",
        )

        assert "### Notes" in summary
        assert "rate limiting" in summary

    def test_summary_with_recommendations(self):
        """Summary includes next session recommendations."""
        summary = format_session_summary(
            session_num=2,
            issues_completed=[],
            issues_in_progress=[],
            tests_status={},
            next_recommendations=["Fix flaky tests", "Update dependencies"],
        )

        assert "### Recommendations" in summary
        assert "Fix flaky tests" in summary

    def test_summary_has_timestamp(self):
        """Summary includes ISO timestamp."""
        summary = format_session_summary(
            session_num=1,
            issues_completed=[],
            issues_in_progress=[],
            tests_status={},
        )

        # Should have a timestamp in ISO format
        assert "**Timestamp:**" in summary


class TestGitHubClient:
    """Tests for GitHubClient using gh CLI."""

    @patch("forge_harness.github_client.subprocess.run")
    def test_verify_gh_cli_success(self, mock_run):
        """GitHubClient initializes when gh CLI is authenticated."""
        mock_run.return_value = MagicMock(returncode=0)

        client = GitHubClient("owner/repo")
        assert client.repo == "owner/repo"

    @patch("forge_harness.github_client.subprocess.run")
    def test_verify_gh_cli_not_authenticated(self, mock_run):
        """GitHubClient raises error when gh CLI not authenticated."""
        mock_run.return_value = MagicMock(returncode=1, stderr="Not logged in")

        with pytest.raises(RuntimeError, match="not authenticated"):
            GitHubClient("owner/repo")

    @patch("forge_harness.github_client.subprocess.run")
    def test_verify_gh_cli_not_found(self, mock_run):
        """GitHubClient raises error when gh CLI not installed."""
        mock_run.side_effect = FileNotFoundError()

        with pytest.raises(RuntimeError, match="not found"):
            GitHubClient("owner/repo")

    @patch("forge_harness.github_client.subprocess.run")
    def test_verify_gh_cli_timeout(self, mock_run):
        """GitHubClient handles auth check timeout."""
        from subprocess import TimeoutExpired

        mock_run.side_effect = TimeoutExpired(cmd=["gh", "auth", "status"], timeout=10)

        with pytest.raises(TimeoutExpired):
            GitHubClient("owner/repo")

    @patch("forge_harness.github_client.subprocess.run")
    def test_create_issue(self, mock_run):
        """create_issue calls gh CLI correctly."""
        # First call is auth check, second is create
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo/issues/42\n",
            ),
        ]

        client = GitHubClient("owner/repo")
        issue = Issue(
            number=None,
            title="Test Issue",
            body="Test body",
            priority=IssuePriority.HIGH,
            domain="test-domain",
        )

        issue_number = client.create_issue(issue)
        assert issue_number == 42

        # Verify the create call
        create_call = mock_run.call_args_list[1]
        cmd = create_call[0][0]
        assert "issue" in cmd
        assert "create" in cmd
        assert "--title" in cmd
        assert "Test Issue" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_list_issues(self, mock_run):
        """list_issues returns parsed JSON."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(
                returncode=0,
                stdout='[{"number": 1, "title": "Issue 1"}, {"number": 2, "title": "Issue 2"}]',
            ),
        ]

        client = GitHubClient("owner/repo")
        issues = client.list_issues(state="open", labels=["bug"])

        assert len(issues) == 2
        assert issues[0]["number"] == 1

    @patch("forge_harness.github_client.subprocess.run")
    def test_add_comment(self, mock_run):
        """add_comment calls gh CLI correctly."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo/issues/1#issuecomment-123\n",
            ),
        ]

        client = GitHubClient("owner/repo")
        url = client.add_comment(1, "Test comment")

        assert "issuecomment" in url

    @patch("forge_harness.github_client.subprocess.run")
    def test_ensure_labels_exist(self, mock_run):
        """ensure_labels_exist creates missing labels."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0, stdout='[{"name": "existing-label"}]'),  # list labels
            MagicMock(returncode=0),  # create label
        ]

        client = GitHubClient("owner/repo")
        client.ensure_labels_exist(["existing-label", "new-label"])

        # Should have called label create for new-label only
        create_calls = [
            c for c in mock_run.call_args_list if "label" in str(c) and "create" in str(c)
        ]
        assert len(create_calls) == 1

    @patch("forge_harness.github_client.subprocess.run")
    def test_get_issue(self, mock_run):
        """get_issue retrieves issue details."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(
                returncode=0,
                stdout='{"number": 42, "title": "Test Issue", "body": "Body", "state": "open", "labels": [{"name": "bug"}]}',
            ),
        ]

        client = GitHubClient("owner/repo")
        issue_data = client.get_issue(42)

        assert issue_data["number"] == 42
        assert issue_data["title"] == "Test Issue"
        assert issue_data["state"] == "open"

    @patch("forge_harness.github_client.subprocess.run")
    def test_update_issue_title(self, mock_run):
        """update_issue can change title."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0),  # update issue
        ]

        client = GitHubClient("owner/repo")
        client.update_issue(42, title="Updated Title")

        # Verify the update call
        update_call = mock_run.call_args_list[1]
        cmd = update_call[0][0]
        assert "issue" in cmd
        assert "edit" in cmd
        assert "42" in cmd
        assert "--title" in cmd
        assert "Updated Title" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_update_issue_body(self, mock_run):
        """update_issue can change body."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0),  # update issue
        ]

        client = GitHubClient("owner/repo")
        client.update_issue(42, body="Updated body")

        update_call = mock_run.call_args_list[1]
        cmd = update_call[0][0]
        assert "--body" in cmd
        assert "Updated body" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_update_issue_labels(self, mock_run):
        """update_issue can add and remove labels."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0),  # update issue
        ]

        client = GitHubClient("owner/repo")
        client.update_issue(
            42,
            add_labels=["bug", "urgent"],
            remove_labels=["wontfix"],
        )

        update_call = mock_run.call_args_list[1]
        cmd = update_call[0][0]
        assert "--add-label" in cmd
        assert "bug,urgent" in cmd
        assert "--remove-label" in cmd
        assert "wontfix" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_close_issue(self, mock_run):
        """close_issue closes issue with reason."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0),  # close issue
        ]

        client = GitHubClient("owner/repo")
        client.close_issue(42, reason="completed")

        close_call = mock_run.call_args_list[1]
        cmd = close_call[0][0]
        assert "issue" in cmd
        assert "close" in cmd
        assert "42" in cmd
        assert "--reason" in cmd
        assert "completed" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_close_issue_not_planned(self, mock_run):
        """close_issue can use not_planned reason."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0),  # close issue
        ]

        client = GitHubClient("owner/repo")
        client.close_issue(99, reason="not_planned")

        close_call = mock_run.call_args_list[1]
        cmd = close_call[0][0]
        assert "not_planned" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_reopen_issue(self, mock_run):
        """reopen_issue reopens closed issue."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0),  # reopen issue
        ]

        client = GitHubClient("owner/repo")
        client.reopen_issue(42)

        reopen_call = mock_run.call_args_list[1]
        cmd = reopen_call[0][0]
        assert "issue" in cmd
        assert "reopen" in cmd
        assert "42" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_list_comments(self, mock_run):
        """list_comments retrieves issue comments."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(
                returncode=0,
                stdout='{"comments": [{"body": "First comment"}, {"body": "Second comment"}]}',
            ),
        ]

        client = GitHubClient("owner/repo")
        comments = client.list_comments(42)

        assert len(comments) == 2
        assert comments[0]["body"] == "First comment"

    @patch("forge_harness.github_client.subprocess.run")
    def test_list_comments_empty(self, mock_run):
        """list_comments handles issues with no comments."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0, stdout="{}"),  # no comments key
        ]

        client = GitHubClient("owner/repo")
        comments = client.list_comments(42)

        assert comments == []

    @patch("forge_harness.github_client.subprocess.run")
    def test_search_issues(self, mock_run):
        """search_issues performs GitHub search."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(
                returncode=0,
                stdout='[{"number": 10, "title": "Search result"}]',
            ),
        ]

        client = GitHubClient("owner/repo")
        results = client.search_issues("is:open label:bug", limit=10)

        assert len(results) == 1
        assert results[0]["number"] == 10

        # Verify search call includes repo filter
        search_call = mock_run.call_args_list[1]
        cmd = search_call[0][0]
        assert "search" in cmd
        assert "issues" in cmd
        assert any("repo:owner/repo" in arg for arg in cmd)

    @patch("forge_harness.github_client.subprocess.run")
    def test_search_issues_error(self, mock_run):
        """search_issues raises error on failure."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=1, stderr="Search failed"),  # search error
        ]

        client = GitHubClient("owner/repo")

        with pytest.raises(RuntimeError, match="search failed"):
            client.search_issues("invalid query")

    @patch("forge_harness.github_client.subprocess.run")
    def test_run_gh_error_handling(self, mock_run):
        """_run_gh raises error when command fails."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=1, stderr="Command failed"),  # failing command
        ]

        client = GitHubClient("owner/repo")

        with pytest.raises(RuntimeError, match="gh command failed"):
            client._run_gh(["issue", "view", "999"])

    @patch("forge_harness.github_client.subprocess.run")
    def test_run_gh_check_false(self, mock_run):
        """_run_gh with check=False doesn't raise on error."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=1, stderr="Not an error"),  # non-critical failure
        ]

        client = GitHubClient("owner/repo")
        result = client._run_gh(["some", "command"], check=False)

        assert result.returncode == 1
        assert "Not an error" in result.stderr

    @patch("forge_harness.github_client.subprocess.run")
    def test_create_issue_with_assignee(self, mock_run):
        """create_issue includes assignee when specified."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo/issues/100\n",
            ),
        ]

        client = GitHubClient("owner/repo")
        issue = Issue(
            number=None,
            title="Assigned Issue",
            body="Body",
            assignee="johndoe",
        )

        issue_number = client.create_issue(issue)
        assert issue_number == 100

        create_call = mock_run.call_args_list[1]
        cmd = create_call[0][0]
        assert "--assignee" in cmd
        assert "johndoe" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_create_issue_with_milestone(self, mock_run):
        """create_issue includes milestone when specified."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo/issues/101\n",
            ),
        ]

        client = GitHubClient("owner/repo")
        issue = Issue(
            number=None,
            title="Milestone Issue",
            body="Body",
            milestone="v1.0",
        )

        issue_number = client.create_issue(issue)
        assert issue_number == 101

        create_call = mock_run.call_args_list[1]
        cmd = create_call[0][0]
        assert "--milestone" in cmd
        assert "v1.0" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_ensure_labels_domain_color(self, mock_run):
        """ensure_labels_exist uses special color for domain labels."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0, stdout="[]"),  # no existing labels
            MagicMock(returncode=0),  # create domain label
        ]

        client = GitHubClient("owner/repo")
        client.ensure_labels_exist(["domain:codeswiftr-com"])

        create_call = mock_run.call_args_list[2]
        cmd = create_call[0][0]
        assert "domain:codeswiftr-com" in cmd
        assert "D4C5F9" in cmd  # Domain label color

    @patch("forge_harness.github_client.subprocess.run")
    def test_ensure_labels_project_color(self, mock_run):
        """ensure_labels_exist uses special color for project labels."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0, stdout="[]"),  # no existing labels
            MagicMock(returncode=0),  # create project label
        ]

        client = GitHubClient("owner/repo")
        client.ensure_labels_exist(["project:my-project"])

        create_call = mock_run.call_args_list[2]
        cmd = create_call[0][0]
        assert "project:my-project" in cmd
        assert "C2E0C6" in cmd  # Project label color

    @patch("forge_harness.github_client.subprocess.run")
    def test_ensure_labels_priority_colors(self, mock_run):
        """ensure_labels_exist creates priority labels with correct colors."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0, stdout="[]"),  # no existing labels
            MagicMock(returncode=0),  # create critical
            MagicMock(returncode=0),  # create high
            MagicMock(returncode=0),  # create medium
            MagicMock(returncode=0),  # create low
        ]

        client = GitHubClient("owner/repo")
        client.ensure_labels_exist(
            [
                "priority:critical",
                "priority:high",
                "priority:medium",
                "priority:low",
            ]
        )

        # Verify all labels created with force flag
        create_calls = [c for c in mock_run.call_args_list[2:] if "label" in str(c)]
        assert len(create_calls) == 4
        for call in create_calls:
            cmd = call[0][0]
            assert "--force" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_list_issues_with_label_filter(self, mock_run):
        """list_issues filters by labels."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0, stdout="[]"),  # empty results
        ]

        client = GitHubClient("owner/repo")
        client.list_issues(state="all", labels=["bug", "urgent"], limit=50)

        list_call = mock_run.call_args_list[1]
        cmd = list_call[0][0]
        assert "--state" in cmd
        assert "all" in cmd
        assert "--label" in cmd
        assert "bug,urgent" in cmd
        assert "--limit" in cmd
        assert "50" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_subprocess_timeout(self, mock_run):
        """GitHubClient handles subprocess timeout."""
        from subprocess import TimeoutExpired

        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            TimeoutExpired(cmd=["gh", "issue", "list"], timeout=30),
        ]

        client = GitHubClient("owner/repo")

        with pytest.raises(TimeoutExpired):
            client.list_issues()

    @patch("forge_harness.github_client.subprocess.run")
    def test_issue_with_project_label(self, mock_run):
        """Issue with project generates project label."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo/issues/200\n",
            ),
        ]

        client = GitHubClient("owner/repo")
        issue = Issue(
            number=None,
            title="Multi-project Issue",
            body="Body",
            domain="leanvibe-ai",
            project="mvp-validator",
        )

        client.create_issue(issue)

        create_call = mock_run.call_args_list[1]
        cmd = create_call[0][0]
        # Check that project label is included
        label_arg_index = cmd.index("--label")
        labels = cmd[label_arg_index + 1]
        assert "project:mvp-validator" in labels

    @patch("forge_harness.github_client.subprocess.run")
    def test_create_issue_no_labels(self, mock_run):
        """create_issue works with no labels."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo/issues/5\n",
            ),
        ]

        client = GitHubClient("owner/repo")
        issue = Issue(
            number=None,
            title="Simple Issue",
            body="Body",
        )

        issue_number = client.create_issue(issue)
        assert issue_number == 5

        create_call = mock_run.call_args_list[1]
        cmd = create_call[0][0]
        # Should still have agent:autonomous label
        assert "--label" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_create_issue_url_parsing_error(self, mock_run):
        """create_issue handles malformed URL output."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0, stdout="Invalid output\n"),
        ]

        client = GitHubClient("owner/repo")
        issue = Issue(number=None, title="Test", body="Body")

        with pytest.raises(ValueError):
            client.create_issue(issue)

    @patch("forge_harness.github_client.subprocess.run")
    def test_get_issue_json_parse_error(self, mock_run):
        """get_issue handles invalid JSON."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0, stdout="not json"),
        ]

        client = GitHubClient("owner/repo")

        with pytest.raises(Exception):  # json.JSONDecodeError
            client.get_issue(1)

    @patch("forge_harness.github_client.subprocess.run")
    def test_list_issues_json_parse_error(self, mock_run):
        """list_issues handles invalid JSON."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0, stdout="[invalid json"),
        ]

        client = GitHubClient("owner/repo")

        with pytest.raises(Exception):  # json.JSONDecodeError
            client.list_issues()

    @patch("forge_harness.github_client.subprocess.run")
    def test_update_issue_multiple_fields(self, mock_run):
        """update_issue can update multiple fields at once."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0),  # update issue
        ]

        client = GitHubClient("owner/repo")
        client.update_issue(
            42,
            title="New Title",
            body="New body",
            add_labels=["enhancement"],
            remove_labels=["bug"],
        )

        update_call = mock_run.call_args_list[1]
        cmd = update_call[0][0]
        assert "--title" in cmd
        assert "--body" in cmd
        assert "--add-label" in cmd
        assert "--remove-label" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_update_issue_no_changes(self, mock_run):
        """update_issue with no changes still calls gh."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0),  # update issue
        ]

        client = GitHubClient("owner/repo")
        client.update_issue(42)  # No fields to update

        update_call = mock_run.call_args_list[1]
        cmd = update_call[0][0]
        assert "issue" in cmd
        assert "edit" in cmd
        assert "42" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_ensure_labels_default_color(self, mock_run):
        """ensure_labels_exist uses default color for unknown labels."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0, stdout="[]"),  # no existing labels
            MagicMock(returncode=0),  # create custom label
        ]

        client = GitHubClient("owner/repo")
        client.ensure_labels_exist(["custom-unknown-label"])

        create_call = mock_run.call_args_list[2]
        cmd = create_call[0][0]
        assert "custom-unknown-label" in cmd
        assert "EDEDED" in cmd  # Default color

    @patch("forge_harness.github_client.subprocess.run")
    def test_ensure_labels_skips_existing(self, mock_run):
        """ensure_labels_exist skips labels that already exist."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(
                returncode=0,
                stdout='[{"name": "bug"}, {"name": "feature"}]',
            ),  # existing labels
        ]

        client = GitHubClient("owner/repo")
        client.ensure_labels_exist(["bug", "feature"])

        # Should not call label create
        create_calls = [c for c in mock_run.call_args_list if "create" in str(c)]
        assert len(create_calls) == 0

    @patch("forge_harness.github_client.subprocess.run")
    def test_ensure_labels_mixed_existing_new(self, mock_run):
        """ensure_labels_exist creates only missing labels."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0, stdout='[{"name": "bug"}]'),  # existing
            MagicMock(returncode=0),  # create feature
            MagicMock(returncode=0),  # create docs
        ]

        client = GitHubClient("owner/repo")
        client.ensure_labels_exist(["bug", "feature", "docs"])

        create_calls = [c for c in mock_run.call_args_list[2:] if "label" in str(c)]
        assert len(create_calls) == 2

    @patch("forge_harness.github_client.subprocess.run")
    def test_list_issues_default_params(self, mock_run):
        """list_issues uses default parameters."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0, stdout="[]"),
        ]

        client = GitHubClient("owner/repo")
        client.list_issues()  # Use all defaults

        list_call = mock_run.call_args_list[1]
        cmd = list_call[0][0]
        assert "--state" in cmd
        assert "open" in cmd
        assert "--limit" in cmd
        assert "30" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_close_issue_default_reason(self, mock_run):
        """close_issue uses default completed reason."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0),  # close issue
        ]

        client = GitHubClient("owner/repo")
        client.close_issue(42)  # No reason specified

        close_call = mock_run.call_args_list[1]
        cmd = close_call[0][0]
        assert "completed" in cmd

    @patch("forge_harness.github_client.subprocess.run")
    def test_search_issues_with_limit(self, mock_run):
        """search_issues respects limit parameter."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(returncode=0, stdout="[]"),
        ]

        client = GitHubClient("owner/repo")
        client.search_issues("is:open", limit=100)

        search_call = mock_run.call_args_list[1]
        # Find the command

        args = search_call[0][0]
        assert "--limit" in args
        assert "100" in args

    @patch("forge_harness.github_client.subprocess.run")
    def test_add_comment_multiline(self, mock_run):
        """add_comment handles multiline comments."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # auth check
            MagicMock(
                returncode=0,
                stdout="https://github.com/owner/repo/issues/1#issuecomment-999\n",
            ),
        ]

        client = GitHubClient("owner/repo")
        multiline_comment = "Line 1\nLine 2\nLine 3"
        url = client.add_comment(1, multiline_comment)

        assert "issuecomment-999" in url

        comment_call = mock_run.call_args_list[1]
        cmd = comment_call[0][0]
        assert "--body" in cmd
