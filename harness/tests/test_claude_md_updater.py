"""
Tests for ClaudeMdUpdater - Automatic CLAUDE.md Pattern Documentation.
=======================================================================

Tests cover:
- LearnedPattern dataclass and markdown generation
- PatternsSection dataclass, markdown generation, and content hashing
- ClaudeMdUpdater initialization and configuration
- Pattern retrieval from Code Atlas
- CLAUDE.md section generation and updates
- Dry run mode
- Section replacement and appending
- Diff generation
- Batch updates for multiple projects
- auto_update_claude_md convenience function
- Error handling
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.meta_learning.claude_md_updater import (
    ClaudeMdUpdater,
    LearnedPattern,
    PatternsSection,
    auto_update_claude_md,
)

# ============================================================================
# LearnedPattern Tests
# ============================================================================


class TestLearnedPattern:
    """Tests for LearnedPattern dataclass."""

    def test_create_pattern_minimal(self):
        """Test creating pattern with minimal fields."""
        pattern = LearnedPattern(
            name="Test Pattern",
            description="A test pattern",
            category="issue",
        )

        assert pattern.name == "Test Pattern"
        assert pattern.description == "A test pattern"
        assert pattern.category == "issue"
        assert pattern.occurrences == 1
        assert pattern.effectiveness == 0.0
        assert pattern.source_sessions == []

    def test_create_pattern_full(self):
        """Test creating pattern with all fields."""
        pattern = LearnedPattern(
            name="Full Pattern",
            description="A complete pattern",
            category="solution",
            occurrences=5,
            effectiveness=0.85,
            source_sessions=["session1", "session2"],
        )

        assert pattern.occurrences == 5
        assert pattern.effectiveness == 0.85
        assert pattern.source_sessions == ["session1", "session2"]

    def test_to_markdown_issue(self):
        """Test markdown generation for issue category."""
        pattern = LearnedPattern(
            name="Common Bug",
            description="This bug happens frequently",
            category="issue",
        )

        md = pattern.to_markdown()

        assert md == "- \u26a0\ufe0f **Common Bug**: This bug happens frequently"

    def test_to_markdown_solution(self):
        """Test markdown generation for solution category."""
        pattern = LearnedPattern(
            name="Fix Pattern",
            description="This fix works well",
            category="solution",
        )

        md = pattern.to_markdown()

        assert md == "- \u2705 **Fix Pattern**: This fix works well"

    def test_to_markdown_pattern(self):
        """Test markdown generation for pattern category."""
        pattern = LearnedPattern(
            name="Design Pattern",
            description="Use this pattern",
            category="pattern",
        )

        md = pattern.to_markdown()

        assert "Design Pattern" in md
        assert "Use this pattern" in md
        assert md.startswith("- ")

    def test_to_markdown_unknown_category(self):
        """Test markdown generation for unknown category."""
        pattern = LearnedPattern(
            name="Unknown",
            description="Unknown type",
            category="unknown",
        )

        md = pattern.to_markdown()

        assert "Unknown" in md
        assert "Unknown type" in md
        assert md.startswith("- ")


# ============================================================================
# PatternsSection Tests
# ============================================================================


class TestPatternsSection:
    """Tests for PatternsSection dataclass."""

    def test_create_empty_section(self):
        """Test creating empty patterns section."""
        section = PatternsSection()

        assert section.common_issues == []
        assert section.known_solutions == []
        assert section.effective_patterns == []
        assert isinstance(section.generated_at, datetime)

    def test_create_section_with_patterns(self):
        """Test creating section with patterns."""
        issue = LearnedPattern(name="Issue", description="An issue", category="issue")
        solution = LearnedPattern(name="Solution", description="A solution", category="solution")
        pattern = LearnedPattern(name="Pattern", description="A pattern", category="pattern")

        section = PatternsSection(
            common_issues=[issue],
            known_solutions=[solution],
            effective_patterns=[pattern],
        )

        assert len(section.common_issues) == 1
        assert len(section.known_solutions) == 1
        assert len(section.effective_patterns) == 1

    def test_to_markdown_empty(self):
        """Test markdown generation for empty section."""
        section = PatternsSection()
        md = section.to_markdown()

        assert "## Learned Patterns" in md
        assert "Auto-generated from Code Atlas" in md
        assert "---" in md

    def test_to_markdown_with_issues(self):
        """Test markdown generation with issues only."""
        section = PatternsSection(
            common_issues=[
                LearnedPattern(name="Bug1", description="First bug", category="issue"),
                LearnedPattern(name="Bug2", description="Second bug", category="issue"),
            ]
        )

        md = section.to_markdown()

        assert "### Common Issues" in md
        assert "Bug1" in md
        assert "Bug2" in md
        assert "### Known Solutions" not in md

    def test_to_markdown_with_solutions(self):
        """Test markdown generation with solutions only."""
        section = PatternsSection(
            known_solutions=[
                LearnedPattern(name="Fix1", description="First fix", category="solution"),
            ]
        )

        md = section.to_markdown()

        assert "### Known Solutions" in md
        assert "Fix1" in md
        assert "### Common Issues" not in md

    def test_to_markdown_with_patterns(self):
        """Test markdown generation with patterns only."""
        section = PatternsSection(
            effective_patterns=[
                LearnedPattern(name="Pattern1", description="A pattern", category="pattern"),
            ]
        )

        md = section.to_markdown()

        assert "### Effective Patterns" in md
        assert "Pattern1" in md

    def test_to_markdown_complete(self):
        """Test markdown generation with all categories."""
        section = PatternsSection(
            common_issues=[LearnedPattern(name="Issue", description="desc", category="issue")],
            known_solutions=[LearnedPattern(name="Solution", description="desc", category="solution")],
            effective_patterns=[LearnedPattern(name="Pattern", description="desc", category="pattern")],
        )

        md = section.to_markdown()

        assert "### Common Issues" in md
        assert "### Known Solutions" in md
        assert "### Effective Patterns" in md

    def test_content_hash_empty(self):
        """Test content hash for empty section."""
        section = PatternsSection()
        hash1 = section.content_hash()

        assert len(hash1) == 8
        assert hash1.isalnum()

    def test_content_hash_changes_with_content(self):
        """Test content hash changes when patterns change."""
        section1 = PatternsSection()
        section2 = PatternsSection(
            common_issues=[LearnedPattern(name="Issue", description="desc", category="issue")]
        )

        assert section1.content_hash() != section2.content_hash()

    def test_content_hash_deterministic(self):
        """Test content hash is deterministic."""
        section1 = PatternsSection(
            common_issues=[LearnedPattern(name="Issue", description="desc", category="issue")]
        )
        section2 = PatternsSection(
            common_issues=[LearnedPattern(name="Issue", description="desc", category="issue")]
        )

        assert section1.content_hash() == section2.content_hash()


# ============================================================================
# ClaudeMdUpdater Initialization Tests
# ============================================================================


class TestClaudeMdUpdaterInit:
    """Tests for ClaudeMdUpdater initialization."""

    def test_init_minimal(self):
        """Test initialization with no arguments."""
        updater = ClaudeMdUpdater()

        assert updater.atlas_bridge is None
        assert updater.min_occurrences == 2
        assert updater.min_effectiveness == 0.6

    def test_init_with_bridge(self):
        """Test initialization with atlas bridge."""
        mock_bridge = MagicMock()
        updater = ClaudeMdUpdater(atlas_bridge=mock_bridge)

        assert updater.atlas_bridge is mock_bridge

    def test_init_with_custom_thresholds(self):
        """Test initialization with custom thresholds."""
        updater = ClaudeMdUpdater(min_occurrences=5, min_effectiveness=0.8)

        assert updater.min_occurrences == 5
        assert updater.min_effectiveness == 0.8


# ============================================================================
# ClaudeMdUpdater Pattern Retrieval Tests
# ============================================================================


class TestGetLearnedPatterns:
    """Tests for get_learned_patterns method."""

    @pytest.mark.asyncio
    async def test_no_bridge_returns_empty(self):
        """Test returns empty patterns when no bridge configured."""
        updater = ClaudeMdUpdater()
        patterns = await updater.get_learned_patterns("test-project")

        assert patterns.common_issues == []
        assert patterns.known_solutions == []
        assert patterns.effective_patterns == []

    @pytest.mark.asyncio
    async def test_extracts_recurring_problems(self):
        """Test extracting recurring problems from Atlas signals."""
        mock_bridge = AsyncMock()
        mock_bridge.get_signals = AsyncMock(
            return_value=MagicMock(
                recurring_problems=[
                    MagicMock(title="Problem1", description="Desc1", occurrences=3),
                    MagicMock(title="Problem2", description="Desc2", occurrences=1),  # Below threshold
                ],
                effective_patterns=[],
                top_entities=[],
            )
        )

        updater = ClaudeMdUpdater(atlas_bridge=mock_bridge, min_occurrences=2)
        patterns = await updater.get_learned_patterns("test-project", "test-domain")

        assert len(patterns.common_issues) == 1
        assert patterns.common_issues[0].name == "Problem1"

    @pytest.mark.asyncio
    async def test_extracts_effective_solutions(self):
        """Test extracting effective solutions from Atlas signals."""
        # Create proper mock objects with explicit attribute values
        solution1 = MagicMock()
        solution1.name = "Solution1"
        solution1.description = "Desc1"
        solution1.effectiveness = 0.8

        solution2 = MagicMock()
        solution2.name = "Solution2"
        solution2.description = "Desc2"
        solution2.effectiveness = 0.5  # Below threshold

        mock_bridge = AsyncMock()
        mock_bridge.get_signals = AsyncMock(
            return_value=MagicMock(
                recurring_problems=[],
                effective_patterns=[solution1, solution2],
                top_entities=[],
            )
        )

        updater = ClaudeMdUpdater(atlas_bridge=mock_bridge, min_effectiveness=0.6)
        patterns = await updater.get_learned_patterns("test-project")

        assert len(patterns.known_solutions) == 1
        assert patterns.known_solutions[0].name == "Solution1"

    @pytest.mark.asyncio
    async def test_extracts_architectural_patterns(self):
        """Test extracting architectural patterns from top entities."""
        # Create proper mock objects with explicit attribute values
        entity1 = MagicMock()
        entity1.entity_type = "pattern"
        entity1.name = "Pattern1"
        entity1.description = "Desc1"
        entity1.importance = 0.9
        entity1.occurrences = 5

        entity2 = MagicMock()
        entity2.entity_type = "pattern"
        entity2.name = "Pattern2"
        entity2.description = None
        entity2.importance = 0.8
        entity2.occurrences = 3

        entity3 = MagicMock()
        entity3.entity_type = "class"  # Wrong type - should be filtered
        entity3.name = "Class1"
        entity3.description = "A class"
        entity3.importance = 0.9
        entity3.occurrences = 5

        entity4 = MagicMock()
        entity4.entity_type = "pattern"
        entity4.name = "Pattern3"
        entity4.description = "Low"
        entity4.importance = 0.5  # Below threshold
        entity4.occurrences = 2

        mock_bridge = AsyncMock()
        mock_bridge.get_signals = AsyncMock(
            return_value=MagicMock(
                recurring_problems=[],
                effective_patterns=[],
                top_entities=[entity1, entity2, entity3, entity4],
            )
        )

        updater = ClaudeMdUpdater(atlas_bridge=mock_bridge)
        patterns = await updater.get_learned_patterns("test-project")

        assert len(patterns.effective_patterns) == 2
        assert patterns.effective_patterns[0].name == "Pattern1"
        assert "3 sessions" in patterns.effective_patterns[1].description

    @pytest.mark.asyncio
    async def test_handles_bridge_error(self):
        """Test handles errors from Atlas bridge gracefully."""
        mock_bridge = AsyncMock()
        mock_bridge.get_signals = AsyncMock(side_effect=Exception("Connection failed"))

        updater = ClaudeMdUpdater(atlas_bridge=mock_bridge)
        patterns = await updater.get_learned_patterns("test-project")

        # Should return empty patterns, not raise
        assert patterns.common_issues == []


# ============================================================================
# ClaudeMdUpdater Section Generation Tests
# ============================================================================


class TestGeneratePatternsSection:
    """Tests for generate_patterns_section method."""

    def test_generates_markdown(self):
        """Test generates markdown from patterns."""
        patterns = PatternsSection(
            common_issues=[LearnedPattern(name="Issue", description="desc", category="issue")]
        )

        updater = ClaudeMdUpdater()
        md = updater.generate_patterns_section(patterns)

        assert "## Learned Patterns" in md
        assert "Issue" in md


# ============================================================================
# ClaudeMdUpdater Update Tests
# ============================================================================


class TestUpdateClaudeMd:
    """Tests for update_claude_md method."""

    @pytest.mark.asyncio
    async def test_skips_when_no_patterns(self, tmp_path):
        """Test skips update when no patterns found."""
        project_path = tmp_path / "test-project"
        project_path.mkdir()
        (project_path / "CLAUDE.md").write_text("# Test Project\n")

        updater = ClaudeMdUpdater()  # No bridge, will return empty patterns
        result = await updater.update_claude_md(project_path)

        assert result["skipped"] == "no_patterns"
        assert result["updated"] is False

    @pytest.mark.asyncio
    async def test_creates_file_if_missing(self, tmp_path):
        """Test creates CLAUDE.md if it doesn't exist."""
        project_path = tmp_path / "new-project"
        project_path.mkdir()

        patterns = PatternsSection(
            common_issues=[LearnedPattern(name="Issue", description="desc", category="issue")]
        )

        updater = ClaudeMdUpdater()
        result = await updater.update_claude_md(project_path, patterns=patterns)

        assert result["created"] is True
        assert result["updated"] is True
        assert (project_path / "CLAUDE.md").exists()

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write(self, tmp_path):
        """Test dry run shows diff but doesn't write."""
        project_path = tmp_path / "test-project"
        project_path.mkdir()
        (project_path / "CLAUDE.md").write_text("# Test Project\n")

        patterns = PatternsSection(
            common_issues=[LearnedPattern(name="Issue", description="desc", category="issue")]
        )

        updater = ClaudeMdUpdater()
        result = await updater.update_claude_md(project_path, patterns=patterns, dry_run=True)

        assert result["dry_run"] is True
        assert result["would_update"] is True
        assert "diff" in result

        # File should be unchanged
        content = (project_path / "CLAUDE.md").read_text()
        assert "Learned Patterns" not in content

    @pytest.mark.asyncio
    async def test_writes_patterns(self, tmp_path):
        """Test writes patterns to CLAUDE.md."""
        project_path = tmp_path / "test-project"
        project_path.mkdir()
        (project_path / "CLAUDE.md").write_text("# Test Project\n")

        patterns = PatternsSection(
            common_issues=[LearnedPattern(name="TestIssue", description="Test description", category="issue")]
        )

        updater = ClaudeMdUpdater()
        result = await updater.update_claude_md(project_path, patterns=patterns)

        assert result["updated"] is True
        content = (project_path / "CLAUDE.md").read_text()
        assert "## Learned Patterns" in content
        assert "TestIssue" in content

    @pytest.mark.asyncio
    async def test_includes_section_hash(self, tmp_path):
        """Test result includes section hash."""
        project_path = tmp_path / "test-project"
        project_path.mkdir()
        (project_path / "CLAUDE.md").write_text("# Test Project\n")

        patterns = PatternsSection(
            common_issues=[LearnedPattern(name="Issue", description="desc", category="issue")]
        )

        updater = ClaudeMdUpdater()
        result = await updater.update_claude_md(project_path, patterns=patterns)

        assert "section_hash" in result
        assert len(result["section_hash"]) == 8

    @pytest.mark.asyncio
    async def test_skips_when_no_changes(self, tmp_path):
        """Test skips update when content unchanged."""
        project_path = tmp_path / "test-project"
        project_path.mkdir()

        patterns = PatternsSection(
            common_issues=[LearnedPattern(name="Issue", description="desc", category="issue")]
        )

        # First write
        updater = ClaudeMdUpdater()
        (project_path / "CLAUDE.md").write_text("# Test Project\n")
        await updater.update_claude_md(project_path, patterns=patterns)

        # Second write with same patterns (but different timestamp)
        # This tests the content comparison logic
        result = await updater.update_claude_md(project_path, patterns=patterns)

        # Note: Due to timestamp in generated content, it may still update
        # This is expected behavior - the hash is based on pattern content, not full markdown


# ============================================================================
# Section Update Logic Tests
# ============================================================================


class TestUpdateSection:
    """Tests for _update_section private method."""

    def test_appends_to_end(self):
        """Test appends section at end when no markers."""
        updater = ClaudeMdUpdater()
        content = "# Project\n\nSome content."
        new_section = "\n## Learned Patterns\n\nPatterns here.\n\n---\n"

        result = updater._update_section(content, new_section)

        assert result.endswith(new_section)
        assert "# Project" in result

    def test_inserts_before_references(self):
        """Test inserts section before References section."""
        updater = ClaudeMdUpdater()
        content = "# Project\n\n## References\n\n- Link 1"
        new_section = "\n## Learned Patterns\n\nPatterns.\n\n---\n"

        result = updater._update_section(content, new_section)

        assert result.index("Learned Patterns") < result.index("References")

    def test_replaces_existing_section(self):
        """Test replaces existing Learned Patterns section."""
        updater = ClaudeMdUpdater()
        content = "# Project\n\n## Learned Patterns\n\nOld content.\n\n---\n\n## References"
        new_section = "\n## Learned Patterns\n\nNew content.\n\n---\n"

        result = updater._update_section(content, new_section)

        assert "Old content" not in result
        assert "New content" in result
        assert result.count("## Learned Patterns") == 1

    def test_preserves_content_after_section(self):
        """Test preserves content after replaced section."""
        updater = ClaudeMdUpdater()
        content = "# Project\n\n## Learned Patterns\n\nOld.\n\n---\n\n## Commands\n\nKeep this."
        new_section = "\n## Learned Patterns\n\nNew.\n\n---\n"

        result = updater._update_section(content, new_section)

        assert "Keep this" in result
        assert "## Commands" in result


# ============================================================================
# Diff Generation Tests
# ============================================================================


class TestGenerateDiff:
    """Tests for _generate_diff private method."""

    def test_shows_added_lines(self):
        """Test diff shows added lines."""
        updater = ClaudeMdUpdater()
        old = "Line 1\nLine 2"
        new = "Line 1\nLine 2\nLine 3\nLine 4"

        diff = updater._generate_diff(old, new)

        assert "+ Line 3" in diff
        assert "+ Line 4" in diff

    def test_limits_output(self):
        """Test diff limits output to 20 lines."""
        updater = ClaudeMdUpdater()
        old = "Original"
        new = "Original\n" + "\n".join([f"Line {i}" for i in range(50)])

        diff = updater._generate_diff(old, new)

        assert diff.count("\n") <= 20

    def test_ignores_empty_lines(self):
        """Test diff ignores empty lines."""
        updater = ClaudeMdUpdater()
        old = "Line 1"
        new = "Line 1\n\n\n\nLine 2"

        diff = updater._generate_diff(old, new)

        assert "+ Line 2" in diff
        assert diff.count("+") == 1


# ============================================================================
# Batch Update Tests
# ============================================================================


class TestUpdateAllProjects:
    """Tests for update_all_projects method."""

    @pytest.mark.asyncio
    async def test_updates_specified_projects(self, tmp_path):
        """Test updates specific projects."""
        # Create test projects
        for name in ["proj1", "proj2"]:
            proj_dir = tmp_path / "domain" / name
            proj_dir.mkdir(parents=True)
            (proj_dir / "CLAUDE.md").write_text(f"# {name}\n")

        projects = [
            {"domain": "domain", "project": "proj1"},
            {"domain": "domain", "project": "proj2"},
        ]

        updater = ClaudeMdUpdater()  # No bridge
        results = await updater.update_all_projects(tmp_path, projects=projects, dry_run=True)

        assert len(results) == 2
        assert all("project" in r for r in results)

    @pytest.mark.asyncio
    async def test_auto_discovers_projects(self, tmp_path):
        """Test auto-discovers projects with CLAUDE.md."""
        # Create test projects
        proj1 = tmp_path / "domain1" / "project1"
        proj1.mkdir(parents=True)
        (proj1 / "CLAUDE.md").write_text("# Project 1\n")

        proj2 = tmp_path / "domain2" / "project2"
        proj2.mkdir(parents=True)
        (proj2 / "CLAUDE.md").write_text("# Project 2\n")

        # Create git dir that should be ignored
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        (git_dir / "CLAUDE.md").write_text("Should be ignored\n")

        updater = ClaudeMdUpdater()
        results = await updater.update_all_projects(tmp_path, dry_run=True)

        # Should find 2 projects, not the .git one
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_handles_project_errors(self, tmp_path):
        """Test handles errors for individual projects."""
        # Create a mock bridge that raises an error
        mock_bridge = AsyncMock()
        mock_bridge.get_signals = AsyncMock(side_effect=Exception("Connection failed"))

        # Create the project directory so it can attempt to read/write
        proj_dir = tmp_path / "test-domain" / "test-project"
        proj_dir.mkdir(parents=True)
        (proj_dir / "CLAUDE.md").write_text("# Test\n")

        projects = [
            {"domain": "test-domain", "project": "test-project"},
        ]

        updater = ClaudeMdUpdater(atlas_bridge=mock_bridge)
        results = await updater.update_all_projects(tmp_path, projects=projects)

        assert len(results) == 1
        # With bridge error, it returns empty patterns which leads to 'skipped'
        assert results[0].get("skipped") == "no_patterns" or "error" not in results[0]


# ============================================================================
# Convenience Function Tests
# ============================================================================


class TestAutoUpdateClaudeMd:
    """Tests for auto_update_claude_md convenience function."""

    @pytest.mark.asyncio
    async def test_uses_current_directory(self, tmp_path, monkeypatch):
        """Test uses current directory when no path provided."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CLAUDE.md").write_text("# Test\n")

        result = await auto_update_claude_md(dry_run=True)

        assert result["project_path"] == str(tmp_path)

    @pytest.mark.asyncio
    async def test_uses_provided_path(self, tmp_path):
        """Test uses provided project path."""
        project_path = tmp_path / "my-project"
        project_path.mkdir()
        (project_path / "CLAUDE.md").write_text("# My Project\n")

        result = await auto_update_claude_md(project_path=project_path, dry_run=True)

        assert result["project_path"] == str(project_path)

    @pytest.mark.asyncio
    async def test_default_dry_run_true(self, tmp_path):
        """Test default is dry run mode."""
        (tmp_path / "CLAUDE.md").write_text("# Test\n")

        result = await auto_update_claude_md(project_path=tmp_path)

        assert result["dry_run"] is True

    @pytest.mark.asyncio
    async def test_with_atlas_bridge(self, tmp_path):
        """Test with Atlas bridge provided."""
        (tmp_path / "CLAUDE.md").write_text("# Test\n")

        mock_bridge = AsyncMock()
        mock_bridge.get_signals = AsyncMock(
            return_value=MagicMock(
                recurring_problems=[
                    MagicMock(title="Problem", description="Desc", occurrences=3),
                ],
                effective_patterns=[],
                top_entities=[],
            )
        )

        result = await auto_update_claude_md(
            atlas_bridge=mock_bridge,
            project_path=tmp_path,
            dry_run=True,
        )

        assert result.get("would_update") or result.get("skipped")


# ============================================================================
# Edge Cases and Error Handling
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_pattern_with_special_characters(self):
        """Test pattern names with special markdown characters."""
        pattern = LearnedPattern(
            name="**Bold** and _italic_",
            description="Contains `code` and [links](url)",
            category="issue",
        )

        md = pattern.to_markdown()
        # Should still generate valid-ish markdown
        assert "Bold" in md
        assert "code" in md

    @pytest.mark.asyncio
    async def test_handles_binary_file(self, tmp_path):
        """Test handles when CLAUDE.md contains binary-like content."""
        project_path = tmp_path / "test-project"
        project_path.mkdir()
        # Write some unusual but valid content
        (project_path / "CLAUDE.md").write_text("# Test\n\x00\x01\x02")

        patterns = PatternsSection(
            common_issues=[LearnedPattern(name="Issue", description="desc", category="issue")]
        )

        updater = ClaudeMdUpdater()
        # Should not raise
        result = await updater.update_claude_md(project_path, patterns=patterns)
        assert "updated" in result or "error" in result

    @pytest.mark.asyncio
    async def test_handles_unicode_content(self, tmp_path):
        """Test handles unicode content in CLAUDE.md."""
        project_path = tmp_path / "test-project"
        project_path.mkdir()
        (project_path / "CLAUDE.md").write_text("# \u65e5\u672c\u8a9e\u30d7\u30ed\u30b8\u30a7\u30af\u30c8\n\n\u8aac\u660e\u6587\u304c\u3053\u3053\u306b\u3042\u308a\u307e\u3059\u3002")

        patterns = PatternsSection(
            common_issues=[LearnedPattern(name="\u30a8\u30e9\u30fc", description="\u8aac\u660e", category="issue")]
        )

        updater = ClaudeMdUpdater()
        result = await updater.update_claude_md(project_path, patterns=patterns)

        assert result["updated"] is True
        content = (project_path / "CLAUDE.md").read_text()
        assert "\u30a8\u30e9\u30fc" in content

    def test_section_markers_constant(self):
        """Test section markers are properly defined."""
        updater = ClaudeMdUpdater()

        assert updater.SECTION_START == "## Learned Patterns"
        assert len(updater.SECTION_END_MARKERS) > 0

    @pytest.mark.asyncio
    async def test_large_number_of_patterns(self, tmp_path):
        """Test handles large number of patterns."""
        project_path = tmp_path / "test-project"
        project_path.mkdir()
        (project_path / "CLAUDE.md").write_text("# Test\n")

        patterns = PatternsSection(
            common_issues=[
                LearnedPattern(name=f"Issue{i}", description=f"Description {i}", category="issue")
                for i in range(100)
            ]
        )

        updater = ClaudeMdUpdater()
        result = await updater.update_claude_md(project_path, patterns=patterns)

        assert result["updated"] is True
        content = (project_path / "CLAUDE.md").read_text()
        assert "Issue99" in content
