"""Tests for living documentation module."""

from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from forge_harness.living_docs import (
    DocumentState,
    LivingDocs,
)


class TestDocumentState:
    """Tests for DocumentState dataclass."""

    def test_document_state_exists(self):
        """DocumentState correctly reports existing files."""
        state = DocumentState(
            path=Path("/test/file.md"),
            exists=True,
            content="# Test",
            last_updated=datetime.now(),
            is_stale=False,
        )
        assert state.exists is True
        assert state.is_stale is False

    def test_document_state_stale(self):
        """DocumentState correctly reports stale files."""
        state = DocumentState(
            path=Path("/test/file.md"),
            exists=True,
            content="# Test",
            last_updated=datetime.now() - timedelta(days=10),
            is_stale=True,
        )
        assert state.is_stale is True


class TestLivingDocs:
    """Tests for LivingDocs class."""

    def setup_method(self):
        """Create a temporary FORGE structure for testing."""
        self.tmpdir = TemporaryDirectory()
        self.forge_root = Path(self.tmpdir.name)

        # Create portfolio-level docs
        docs_dir = self.forge_root / "docs"
        docs_dir.mkdir()
        (docs_dir / "00-portfolio-digest.md").write_text("""# Portfolio Digest

Last Updated: 2025-01-15

## Current Blockers
- API rate limiting issues
- CI/CD pipeline slow

## Priorities
1. Interview Simulator launch
2. Tech Diligence MVP
""")

        # Create domain-level docs
        domains_dir = docs_dir / "domains"
        domains_dir.mkdir()
        (domains_dir / "codeswiftr-com.md").write_text("""# codeswiftr-com Domain

## Priority Projects
| Rank | Project | Status |
|------|---------|--------|
| 1 | interview-simulator | Active |
| 2 | tech-diligence | Planning |
""")

        # Create project-level docs
        project_dir = self.forge_root / "codeswiftr-com" / "interview-simulator" / "docs"
        project_dir.mkdir(parents=True)

        (project_dir / "PLAN.md").write_text("""# Interview Simulator Plan

## Sprint 12 - Frontend Tests

### Priorities
1. Component test coverage
2. E2E test framework
3. Performance optimization
""")

        (project_dir / "progress.md").write_text("""# Progress

## 2025-01-14 - Sprint 11 Complete

Completed authentication flow.

## 2025-01-10 - API Integration

Connected frontend to backend API.
""")

        (project_dir / "active-context.md").write_text("""# Active Context

**Last Updated**: 2025-01-15

## Current Blockers
- Test coverage below target
- CI pipeline slow

## Recent Decisions
### 2025-01-14: Testing Strategy
- Using Vitest for unit tests.
""")

        self.living_docs = LivingDocs(self.forge_root)

    def teardown_method(self):
        """Clean up temporary directory."""
        self.tmpdir.cleanup()

    def test_consult_loads_all_levels(self):
        """consult() loads documents from all pyramid levels."""
        context = self.living_docs.consult("codeswiftr-com", "interview-simulator")

        assert context.portfolio_digest.exists
        assert context.domain_snapshot.exists
        assert context.project_plan.exists
        assert context.project_progress.exists
        assert context.active_context.exists

    def test_consult_extracts_blockers(self):
        """consult() extracts blockers from documents."""
        context = self.living_docs.consult("codeswiftr-com", "interview-simulator")

        assert len(context.blockers) > 0
        assert any("test" in b.lower() or "rate" in b.lower() for b in context.blockers)

    def test_consult_extracts_priorities(self):
        """consult() extracts priorities from documents."""
        context = self.living_docs.consult("codeswiftr-com", "interview-simulator")

        assert len(context.priorities) > 0
        assert len(context.priorities) <= 5  # Top 5 only

    def test_consult_extracts_sprint(self):
        """consult() extracts current sprint from PLAN.md."""
        context = self.living_docs.consult("codeswiftr-com", "interview-simulator")

        assert context.current_sprint is not None
        assert "Sprint 12" in context.current_sprint

    def test_consult_extracts_milestones(self):
        """consult() extracts recent milestones from progress.md."""
        context = self.living_docs.consult("codeswiftr-com", "interview-simulator")

        assert len(context.recent_milestones) > 0
        assert any("2025-01-14" in m for m in context.recent_milestones)

    def test_consult_missing_project(self):
        """consult() handles missing project gracefully."""
        context = self.living_docs.consult("codeswiftr-com", "nonexistent-project")

        assert context.portfolio_digest.exists
        assert not context.project_plan.exists
        assert not context.project_progress.exists

    def test_update_modifies_progress(self):
        """update() adds entry to progress.md."""
        updated = self.living_docs.update(
            domain="codeswiftr-com",
            project="interview-simulator",
            milestone="Test Coverage Improved",
            details="Added 50 new component tests, coverage now at 65%.",
            files_changed=["src/components/__tests__/Button.test.tsx"],
            metrics={"coverage": "65%", "tests": 150},
        )

        assert len(updated) > 0

        # Check progress.md was updated
        progress_path = (
            self.forge_root / "codeswiftr-com" / "interview-simulator" / "docs" / "progress.md"
        )
        content = progress_path.read_text()
        assert "Test Coverage Improved" in content
        assert "65%" in content

    def test_update_modifies_active_context(self):
        """update() updates active-context.md timestamp."""
        today = datetime.now().strftime("%Y-%m-%d")

        self.living_docs.update(
            domain="codeswiftr-com",
            project="interview-simulator",
            milestone="Minor Update",
            details="Small fix.",
        )

        active_path = (
            self.forge_root
            / "codeswiftr-com"
            / "interview-simulator"
            / "docs"
            / "active-context.md"
        )
        content = active_path.read_text()
        assert today in content

    def test_sync_reports_status(self):
        """sync() returns status report of all documents."""
        report = self.living_docs.sync("codeswiftr-com", "interview-simulator")

        assert "synced_at" in report
        assert "files_checked" in report
        assert len(report["files_checked"]) > 0

    def test_sync_identifies_missing(self):
        """sync() identifies missing documents."""
        # Remove a required file
        active_path = (
            self.forge_root
            / "codeswiftr-com"
            / "interview-simulator"
            / "docs"
            / "active-context.md"
        )
        active_path.unlink()

        report = self.living_docs.sync("codeswiftr-com", "interview-simulator")

        assert len(report["missing_files"]) > 0
        assert any("active-context" in f for f in report["missing_files"])

    def test_get_context_summary(self):
        """get_context_summary() returns formatted markdown."""
        context = self.living_docs.consult("codeswiftr-com", "interview-simulator")
        summary = self.living_docs.get_context_summary(context)

        assert "# Living Docs Context" in summary
        assert "Sprint" in summary or "Priorities" in summary


class TestDocumentExtraction:
    """Tests for specific extraction methods."""

    def setup_method(self):
        """Create temporary directory."""
        self.tmpdir = TemporaryDirectory()
        self.forge_root = Path(self.tmpdir.name)
        self.living_docs = LivingDocs(self.forge_root)

    def teardown_method(self):
        """Clean up."""
        self.tmpdir.cleanup()

    def test_extract_blockers_bullet_list(self):
        """Extract blockers from bullet list format."""
        content = """
## Current Blockers
- First blocker item
- Second blocker item
* Third blocker with asterisk
"""
        blockers = self.living_docs._extract_blockers(content)

        assert len(blockers) == 3
        assert "First blocker item" in blockers

    def test_extract_priorities_numbered_list(self):
        """Extract priorities from numbered list."""
        content = """
## Priorities
1. First priority
2. Second priority
3. Third priority
"""
        priorities = self.living_docs._extract_priorities(content)

        assert len(priorities) >= 3

    def test_extract_priorities_table(self):
        """Extract priorities from markdown table."""
        content = """
## Top 5 Priorities

| Rank | Task |
|------|------|
| 1 | Deploy to production |
| 2 | Fix authentication bug |
| 3 | Add unit tests |
"""
        priorities = self.living_docs._extract_priorities(content)

        assert len(priorities) >= 2

    def test_extract_milestones_dated(self):
        """Extract milestones with dates."""
        content = """
## 2025-01-15 - Major Release

Launched version 2.0

## 2025-01-10 - Bug Fixes

Fixed critical issues
"""
        milestones = self.living_docs._extract_milestones(content)

        assert len(milestones) == 2
        assert "2025-01-15" in milestones[0]

    def test_extract_sprint_from_plan(self):
        """Extract current sprint from PLAN.md format."""
        content = """
# Project Plan

## Sprint 15 - Authentication Overhaul

### Goals
- Implement OAuth
"""
        sprint = self.living_docs._extract_sprint(content)

        assert sprint is not None
        assert "Sprint 15" in sprint


class TestStalenessDetection:
    """Tests for document staleness detection."""

    def setup_method(self):
        """Create temporary directory."""
        self.tmpdir = TemporaryDirectory()
        self.forge_root = Path(self.tmpdir.name)
        self.living_docs = LivingDocs(self.forge_root)

    def teardown_method(self):
        """Clean up."""
        self.tmpdir.cleanup()

    def test_recent_document_not_stale(self):
        """Documents updated recently are not stale."""
        today = datetime.now().strftime("%Y-%m-%d")

        doc_path = self.forge_root / "test.md"
        doc_path.write_text(f"""
# Test Document

Last Updated: {today}

Content here.
""")

        state = self.living_docs._read_doc(doc_path)

        assert state.exists
        assert state.is_stale is False

    def test_old_document_is_stale(self):
        """Documents older than 7 days are stale."""
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

        doc_path = self.forge_root / "test.md"
        doc_path.write_text(f"""
# Test Document

Last Updated: {old_date}

Content here.
""")

        state = self.living_docs._read_doc(doc_path)

        assert state.exists
        assert state.is_stale is True

    def test_no_date_uses_mtime(self):
        """Documents without date header use file mtime."""
        doc_path = self.forge_root / "test.md"
        doc_path.write_text("# Test\n\nNo date here.")

        state = self.living_docs._read_doc(doc_path)

        assert state.exists
        assert state.last_updated is not None
        # Just created, so should not be stale
        assert state.is_stale is False
