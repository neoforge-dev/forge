"""
CLAUDE.md Auto-Updater - Automatic Pattern Documentation.

Automatically updates project CLAUDE.md files with patterns learned from
Code Atlas sessions. Extracts recurring problems and effective solutions.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from forge_harness.meta_learning.bridges.code_atlas import CodeAtlasBridge

logger = logging.getLogger(__name__)


@dataclass
class LearnedPattern:
    """A pattern learned from Code Atlas analysis."""

    name: str
    description: str
    category: str  # 'issue', 'solution', 'pattern'
    occurrences: int = 1
    effectiveness: float = 0.0
    source_sessions: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Convert to markdown list item."""
        emoji = {"issue": "⚠️", "solution": "✅", "pattern": "📘"}.get(self.category, "📌")
        return f"- {emoji} **{self.name}**: {self.description}"


@dataclass
class PatternsSection:
    """A complete Learned Patterns section for CLAUDE.md."""

    common_issues: list[LearnedPattern] = field(default_factory=list)
    known_solutions: list[LearnedPattern] = field(default_factory=list)
    effective_patterns: list[LearnedPattern] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_markdown(self) -> str:
        """Generate markdown content for the section."""
        lines = [
            "",
            "## Learned Patterns",
            "",
            f"_Auto-generated from Code Atlas analysis on {self.generated_at.strftime('%Y-%m-%d')}_",
            "",
        ]

        if self.common_issues:
            lines.extend(
                [
                    "### Common Issues",
                    "",
                ]
            )
            for pattern in self.common_issues:
                lines.append(pattern.to_markdown())
            lines.append("")

        if self.known_solutions:
            lines.extend(
                [
                    "### Known Solutions",
                    "",
                ]
            )
            for pattern in self.known_solutions:
                lines.append(pattern.to_markdown())
            lines.append("")

        if self.effective_patterns:
            lines.extend(
                [
                    "### Effective Patterns",
                    "",
                ]
            )
            for pattern in self.effective_patterns:
                lines.append(pattern.to_markdown())
            lines.append("")

        lines.append("---")
        lines.append("")

        return "\n".join(lines)

    def content_hash(self) -> str:
        """Generate hash of section content for change detection."""
        content = (
            f"{len(self.common_issues)},{len(self.known_solutions)},{len(self.effective_patterns)}"
        )
        for p in self.common_issues + self.known_solutions + self.effective_patterns:
            content += f",{p.name}"
        return hashlib.md5(content.encode()).hexdigest()[:8]


class ClaudeMdUpdater:
    """
    Updates CLAUDE.md files with patterns learned from Code Atlas.

    Usage:
        from forge_harness.meta_learning.bridges.code_atlas import CodeAtlasBridge

        atlas = CodeAtlasBridge(...)
        updater = ClaudeMdUpdater(atlas_bridge=atlas)

        # Get patterns for a project
        patterns = await updater.get_learned_patterns("interview-simulator")

        # Update CLAUDE.md (dry run)
        diff = await updater.update_claude_md(project_path, dry_run=True)

        # Update for real
        result = await updater.update_claude_md(project_path)
    """

    SECTION_START = "## Learned Patterns"
    SECTION_END_MARKERS = ["## References", "## Quick", "## Commands", "---\n\n##", "---\n##"]

    def __init__(
        self,
        atlas_bridge: Optional["CodeAtlasBridge"] = None,
        min_occurrences: int = 2,
        min_effectiveness: float = 0.6,
    ):
        """Initialize the CLAUDE.md updater.

        Args:
            atlas_bridge: CodeAtlasBridge for pattern retrieval
            min_occurrences: Minimum occurrences to include a pattern
            min_effectiveness: Minimum effectiveness score for solutions
        """
        self.atlas_bridge = atlas_bridge
        self.min_occurrences = min_occurrences
        self.min_effectiveness = min_effectiveness

    async def get_learned_patterns(
        self,
        project: str,
        domain: str | None = None,
    ) -> PatternsSection:
        """Query Code Atlas for project-specific patterns.

        Args:
            project: Project name
            domain: Optional domain name

        Returns:
            PatternsSection with categorized patterns
        """
        section = PatternsSection()

        if not self.atlas_bridge:
            logger.warning("No Code Atlas bridge - returning empty patterns")
            return section

        try:
            # Get signals from Atlas
            signals = await self.atlas_bridge.get_signals(
                domain=domain or "unknown",
                project=project,
            )

            # Extract patterns from recurring problems
            for problem in signals.recurring_problems:
                if problem.occurrences >= self.min_occurrences:
                    section.common_issues.append(
                        LearnedPattern(
                            name=problem.title,
                            description=problem.description,
                            category="issue",
                            occurrences=problem.occurrences,
                        )
                    )

            # Extract patterns from effective solutions
            for pattern in signals.effective_patterns:
                if pattern.effectiveness >= self.min_effectiveness:
                    section.known_solutions.append(
                        LearnedPattern(
                            name=pattern.name,
                            description=pattern.description,
                            category="solution",
                            effectiveness=pattern.effectiveness,
                        )
                    )

            # Extract architectural patterns
            for entity in signals.top_entities:
                if entity.entity_type == "pattern" and entity.importance > 0.7:
                    section.effective_patterns.append(
                        LearnedPattern(
                            name=entity.name,
                            description=entity.description
                            or f"Pattern used in {entity.occurrences} sessions",
                            category="pattern",
                            occurrences=entity.occurrences,
                        )
                    )

            logger.info(
                f"Retrieved {len(section.common_issues)} issues, "
                f"{len(section.known_solutions)} solutions, "
                f"{len(section.effective_patterns)} patterns for {project}"
            )

        except Exception as e:
            logger.warning(f"Error getting patterns from Atlas: {e}")

        return section

    def generate_patterns_section(self, patterns: PatternsSection) -> str:
        """Generate markdown content for the Learned Patterns section.

        Args:
            patterns: PatternsSection object

        Returns:
            Markdown string
        """
        return patterns.to_markdown()

    async def update_claude_md(
        self,
        project_path: Path,
        patterns: PatternsSection | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Update the CLAUDE.md file with learned patterns.

        Args:
            project_path: Path to project directory
            patterns: Optional pre-fetched patterns
            dry_run: If True, show diff without writing

        Returns:
            Dict with update results
        """
        result: dict[str, Any] = {
            "updated": False,
            "dry_run": dry_run,
            "project_path": str(project_path),
        }

        claude_md_path = project_path / "CLAUDE.md"

        # Get patterns if not provided
        if patterns is None:
            project_name = project_path.name
            domain = project_path.parent.name if project_path.parent.name != "." else None
            patterns = await self.get_learned_patterns(project_name, domain)

        # Check if we have any patterns
        if not any([patterns.common_issues, patterns.known_solutions, patterns.effective_patterns]):
            result["skipped"] = "no_patterns"
            logger.info(f"No patterns found for {project_path}")
            return result

        # Read existing content
        if claude_md_path.exists():
            original_content = claude_md_path.read_text()
        else:
            original_content = f"# {project_path.name}\n\n"
            result["created"] = True

        # Generate new section
        new_section = self.generate_patterns_section(patterns)
        result["section_hash"] = patterns.content_hash()

        # Find and replace or append section
        new_content = self._update_section(original_content, new_section)

        # Check if content actually changed
        if new_content == original_content:
            result["skipped"] = "no_changes"
            return result

        # Generate diff
        result["diff"] = self._generate_diff(original_content, new_content)

        if dry_run:
            result["would_update"] = True
            return result

        # Write updated content
        claude_md_path.write_text(new_content)
        result["updated"] = True
        logger.info(f"Updated CLAUDE.md for {project_path}")

        return result

    def _update_section(self, content: str, new_section: str) -> str:
        """Update or append the Learned Patterns section.

        Args:
            content: Original CLAUDE.md content
            new_section: New Learned Patterns section

        Returns:
            Updated content
        """
        # Check if section already exists
        if self.SECTION_START in content:
            # Find section boundaries
            start_idx = content.index(self.SECTION_START)

            # Find end of section (next ## heading or end of file)
            end_idx = len(content)
            for marker in self.SECTION_END_MARKERS:
                try:
                    marker_idx = content.index(marker, start_idx + len(self.SECTION_START))
                    if marker_idx < end_idx:
                        end_idx = marker_idx
                except ValueError:
                    continue

            # Replace existing section
            return content[:start_idx] + new_section + content[end_idx:]

        # Append before References section if it exists
        for marker in ["## References", "## Quick Navigation"]:
            if marker in content:
                idx = content.index(marker)
                return content[:idx] + new_section + content[idx:]

        # Append at end
        return content.rstrip() + "\n\n" + new_section

    def _generate_diff(self, old: str, new: str) -> str:
        """Generate a simple diff showing changes.

        Args:
            old: Original content
            new: New content

        Returns:
            Diff string
        """
        old_lines = old.split("\n")
        new_lines = new.split("\n")

        diff_lines = []

        # Simple diff: show added lines
        old_set = set(old_lines)
        for line in new_lines:
            if line not in old_set and line.strip():
                diff_lines.append(f"+ {line}")

        return "\n".join(diff_lines[:20])  # Limit output

    async def update_all_projects(
        self,
        forge_root: Path,
        projects: list[dict[str, str]] | None = None,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """Update CLAUDE.md for multiple projects.

        Args:
            forge_root: Root path of FORGE repository
            projects: Optional list of {domain, project} dicts
            dry_run: If True, show diffs without writing

        Returns:
            List of update results
        """
        if projects is None:
            # Auto-discover projects with CLAUDE.md
            projects = []
            for claude_md in forge_root.rglob("CLAUDE.md"):
                if ".git" not in str(claude_md) and "node_modules" not in str(claude_md):
                    project_dir = claude_md.parent
                    projects.append(
                        {
                            "domain": project_dir.parent.name,
                            "project": project_dir.name,
                            "path": project_dir,
                        }
                    )

        results = []
        for proj in projects:
            try:
                path = proj.get("path") or (forge_root / proj["domain"] / proj["project"])
                result = await self.update_claude_md(path, dry_run=dry_run)
                result["domain"] = proj.get("domain", "")
                result["project"] = proj.get("project", "")
                results.append(result)
            except Exception as e:
                results.append(
                    {
                        "domain": proj.get("domain", ""),
                        "project": proj.get("project", ""),
                        "error": str(e),
                    }
                )

        return results


async def auto_update_claude_md(
    atlas_bridge: Optional["CodeAtlasBridge"] = None,
    project_path: Path | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Convenience function to update a single project's CLAUDE.md.

    Args:
        atlas_bridge: CodeAtlasBridge for pattern retrieval
        project_path: Path to project (defaults to current directory)
        dry_run: If True, show diff without writing

    Returns:
        Update result dict
    """
    updater = ClaudeMdUpdater(atlas_bridge=atlas_bridge)
    path = project_path or Path.cwd()
    return await updater.update_claude_md(path, dry_run=dry_run)
