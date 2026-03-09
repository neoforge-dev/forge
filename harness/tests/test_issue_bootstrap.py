"""Tests for FORGE issue bootstrap module."""

from unittest.mock import MagicMock, patch

from forge_harness.issue_bootstrap import (
    BootstrapResult,
    _format_project_name,
    _get_required_labels,
    bootstrap_project,
    list_epics,
    validate_plan_md,
)


class TestFormatProjectName:
    """Tests for _format_project_name helper."""

    def test_basic_conversion(self):
        """Converts dashes to spaces and title cases."""
        assert _format_project_name("kiddo-rewards") == "Kiddo Rewards"

    def test_underscores(self):
        """Converts underscores to spaces."""
        assert _format_project_name("my_cool_project") == "My Cool Project"

    def test_single_word(self):
        """Single word is title cased."""
        assert _format_project_name("project") == "Project"


class TestGetRequiredLabels:
    """Tests for _get_required_labels helper."""

    def test_includes_domain_label(self):
        """Domain label is included."""
        labels = _get_required_labels("test-domain", [])

        assert "domain:test-domain" in labels

    def test_includes_priority_labels(self):
        """All priority labels are included."""
        labels = _get_required_labels("test", [])

        assert "priority:critical" in labels
        assert "priority:high" in labels
        assert "priority:medium" in labels
        assert "priority:low" in labels

    def test_includes_type_labels(self):
        """All type labels are included."""
        labels = _get_required_labels("test", [])

        assert "type:feature" in labels
        assert "type:bug" in labels
        assert "type:infra" in labels
        assert "type:docs" in labels

    def test_includes_status_labels(self):
        """Status labels are included."""
        labels = _get_required_labels("test", [])

        assert "status:in-progress" in labels
        assert "status:blocked" in labels
        assert "status:verification-failed" in labels


class TestValidatePlanMd:
    """Tests for validate_plan_md function."""

    def test_missing_file(self, tmp_path):
        """Returns error when file doesn't exist."""
        result = validate_plan_md(tmp_path / "nonexistent.md")

        assert len(result["errors"]) > 0
        assert "not found" in result["errors"][0]

    def test_no_epics(self, tmp_path):
        """Returns error when no epics found."""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("# Just a title\nNo epics here.")

        result = validate_plan_md(plan_path)

        assert len(result["errors"]) > 0
        assert "No epics found" in result["errors"][0]

    def test_valid_plan(self, tmp_path):
        """Valid PLAN.md passes validation."""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("""# Project Plan

## Epic 1: Setup Infrastructure

**ICE Score**: 9/9/8
**Priority**: P0
**Effort**: 4h

### Rationale
This epic sets up the core infrastructure.

### Acceptance Criteria
- [ ] Backend runs locally
- [ ] Tests pass
""")

        result = validate_plan_md(plan_path)

        assert len(result["errors"]) == 0

    def test_warnings_for_missing_fields(self, tmp_path):
        """Returns warnings for missing optional fields."""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("""# Project Plan

## Epic 1: Minimal Epic

Just a title, nothing else.
""")

        result = validate_plan_md(plan_path)

        # Should have warnings for missing ICE score, criteria, etc.
        assert len(result["warnings"]) > 0


class TestListEpics:
    """Tests for list_epics function."""

    def test_missing_file(self, tmp_path):
        """Returns empty list for missing file."""
        result = list_epics(tmp_path / "nonexistent.md")

        assert result == []

    def test_parses_epics(self, tmp_path):
        """Parses epics from PLAN.md."""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("""# Project Plan

## Epic 1: First Epic

**ICE Score**: 9/8/7
**Priority**: P0
**Effort**: 4h

### Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Epic 2: Second Epic

**ICE Score**: 7/7/6
**Priority**: P1
**Effort**: 8h

### Acceptance Criteria
- [ ] Single criterion
""")

        result = list_epics(plan_path)

        assert len(result) == 2
        assert result[0]["number"] == 1
        assert result[0]["title"] == "First Epic"
        assert result[0]["priority"] == "critical"
        assert result[0]["criteria_count"] == 2
        assert result[1]["number"] == 2
        assert result[1]["title"] == "Second Epic"


class TestBootstrapProject:
    """Tests for bootstrap_project function."""

    def test_missing_project_dir(self, tmp_path):
        """Returns error when project directory doesn't exist."""
        result = bootstrap_project(
            domain="test-domain",
            project="nonexistent-project",
            forge_root=tmp_path,
            github_repo="owner/repo",
        )

        assert result.success is False
        assert "not found" in result.errors[0]

    def test_missing_plan_md(self, tmp_path):
        """Returns error when PLAN.md doesn't exist."""
        project_dir = tmp_path / "test-domain" / "test-project"
        project_dir.mkdir(parents=True)
        # Create docs dir but no PLAN.md
        (project_dir / "docs").mkdir()

        result = bootstrap_project(
            domain="test-domain",
            project="test-project",
            forge_root=tmp_path,
            github_repo="owner/repo",
        )

        assert result.success is False
        assert "PLAN.md not found" in result.errors[0]

    def test_no_epics_in_plan(self, tmp_path):
        """Returns error when no epics in PLAN.md."""
        project_dir = tmp_path / "test-domain" / "test-project" / "docs"
        project_dir.mkdir(parents=True)
        (project_dir / "PLAN.md").write_text("# Empty plan\nNo epics here.")

        result = bootstrap_project(
            domain="test-domain",
            project="test-project",
            forge_root=tmp_path,
            github_repo="owner/repo",
        )

        assert result.success is False
        assert "No epics found" in result.errors[0]

    def test_dry_run_returns_info(self, tmp_path):
        """Dry run returns what would be created without creating issues."""
        project_dir = tmp_path / "test-domain" / "test-project" / "docs"
        project_dir.mkdir(parents=True)
        (project_dir / "PLAN.md").write_text("""# Project Plan

## Epic 1: First Epic

**ICE Score**: 9/8/7
**Priority**: P0
**Effort**: 4h

### Acceptance Criteria
- [ ] Test criterion

## Epic 2: Second Epic

**ICE Score**: 7/7/6
**Priority**: P1
**Effort**: 8h
""")

        result = bootstrap_project(
            domain="test-domain",
            project="test-project",
            forge_root=tmp_path,
            github_repo="owner/repo",
            dry_run=True,
        )

        assert result.success is True
        assert result.total_issues_created == 3  # 2 epics + 1 META
        assert len(result.epic_issues) == 2
        assert "DRY RUN" in result.errors[0]

    @patch("forge_harness.issue_bootstrap.GitHubClient")
    def test_creates_issues(self, mock_github_class, tmp_path):
        """Creates GitHub issues when not dry run."""
        project_dir = tmp_path / "test-domain" / "test-project" / "docs"
        project_dir.mkdir(parents=True)
        (project_dir / "PLAN.md").write_text("""# Project Plan

## Epic 1: Setup Infrastructure

**ICE Score**: 9/8/7
**Priority**: P0
**Effort**: 4h

### Acceptance Criteria
- [ ] Test criterion
""")

        # Mock GitHub client
        mock_github = MagicMock()
        mock_github_class.return_value = mock_github
        mock_github.create_issue.side_effect = [100, 101]  # META #100, Epic #101

        result = bootstrap_project(
            domain="test-domain",
            project="test-project",
            forge_root=tmp_path,
            github_repo="owner/repo",
        )

        assert result.success is True
        assert result.meta_issue_number == 100
        assert len(result.epic_issues) == 1
        assert result.epic_issues[0]["number"] == 101
        assert mock_github.create_issue.call_count == 2
        assert mock_github.ensure_labels_exist.called

    @patch("forge_harness.issue_bootstrap.GitHubClient")
    def test_handles_github_error(self, mock_github_class, tmp_path):
        """Handles GitHub client errors gracefully."""
        project_dir = tmp_path / "test-domain" / "test-project" / "docs"
        project_dir.mkdir(parents=True)
        (project_dir / "PLAN.md").write_text("""# Project Plan

## Epic 1: Test Epic

**ICE Score**: 9/8/7
**Priority**: P0

### Acceptance Criteria
- [ ] Test
""")

        # Mock GitHub client that raises on auth
        mock_github_class.side_effect = RuntimeError("gh CLI not authenticated")

        result = bootstrap_project(
            domain="test-domain",
            project="test-project",
            forge_root=tmp_path,
            github_repo="owner/repo",
        )

        assert result.success is False
        assert "GitHub client error" in result.errors[0]

    def test_custom_project_name(self, tmp_path):
        """Uses custom project name when provided."""
        project_dir = tmp_path / "test-domain" / "test-project" / "docs"
        project_dir.mkdir(parents=True)
        (project_dir / "PLAN.md").write_text("""# Project Plan

## Epic 1: Test Epic

**Priority**: P0

### Acceptance Criteria
- [ ] Test
""")

        result = bootstrap_project(
            domain="test-domain",
            project="test-project",
            forge_root=tmp_path,
            github_repo="owner/repo",
            project_name="My Custom Name",
            dry_run=True,
        )

        assert result.success is True
        # Would create META issue with custom name


class TestBootstrapResult:
    """Tests for BootstrapResult dataclass."""

    def test_creation(self):
        """BootstrapResult can be created."""
        result = BootstrapResult(
            success=True,
            domain="test",
            project="project",
            meta_issue_number=1,
            epic_issues=[{"number": 2, "title": "Epic", "epic": "1"}],
            total_issues_created=2,
            labels_created=["priority:high"],
        )

        assert result.success is True
        assert result.domain == "test"
        assert result.meta_issue_number == 1

    def test_errors_default_to_empty(self):
        """Errors default to empty list."""
        result = BootstrapResult(
            success=True,
            domain="test",
            project="project",
            meta_issue_number=None,
            epic_issues=[],
            total_issues_created=0,
            labels_created=[],
        )

        assert result.errors == []
