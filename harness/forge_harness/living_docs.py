"""
Living Documentation Integration
================================

Manages the FORGE living documentation pyramid:
- Level 0: Portfolio digest and sprint plans
- Level 1: Domain snapshots and market research
- Level 2: Project docs (brief, plan, active-context)
- Level 3: Implementation details

Provides three modes:
- consult: Read and summarize docs before planning
- update: Propagate changes after completing work
- sync: Mechanical sync across levels before handoff
"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class DocumentState:
    """State of a living document."""

    path: Path
    exists: bool
    content: str | None
    last_updated: datetime | None
    is_stale: bool  # True if >7 days old


@dataclass
class LivingDocsContext:
    """Context loaded from living docs pyramid."""

    portfolio_digest: DocumentState
    domain_snapshot: DocumentState
    project_plan: DocumentState
    project_progress: DocumentState
    active_context: DocumentState
    project_brief: DocumentState | None

    # Extracted metadata
    current_sprint: str | None
    blockers: list[str]
    priorities: list[str]
    recent_milestones: list[str]


class LivingDocs:
    """
    Interface for reading and updating the living documentation pyramid.

    Usage:
        docs = LivingDocs(forge_root)

        # Before work: load context
        context = docs.consult("codeswiftr-com", "interview-simulator")

        # After work: update docs
        docs.update(
            domain="codeswiftr-com",
            project="interview-simulator",
            milestone="Sprint 12 - Frontend Tests",
            details="Added 45 component tests, coverage now 42%",
            files_changed=["src/test/setup.ts", "src/components/__tests__/*.tsx"]
        )

        # Before handoff: full sync
        docs.sync("codeswiftr-com", "interview-simulator")
    """

    def __init__(self, forge_root: Path):
        self.forge_root = Path(forge_root)

    def _read_doc(self, path: Path) -> DocumentState:
        """Read a document and extract metadata."""
        if not path.exists():
            return DocumentState(
                path=path,
                exists=False,
                content=None,
                last_updated=None,
                is_stale=True,
            )

        content = path.read_text()

        # Try to extract last_updated from content
        last_updated = None
        date_match = re.search(
            r"(?:Last [Uu]pdated|Updated|Date):\s*(\d{4}-\d{2}-\d{2})",
            content,
        )
        if date_match:
            try:
                last_updated = datetime.strptime(date_match.group(1), "%Y-%m-%d")
            except ValueError:
                pass

        # Fall back to file modification time
        if last_updated is None:
            last_updated = datetime.fromtimestamp(path.stat().st_mtime)

        # Check if stale (>7 days old)
        is_stale = (datetime.now() - last_updated).days > 7

        return DocumentState(
            path=path,
            exists=True,
            content=content,
            last_updated=last_updated,
            is_stale=is_stale,
        )

    def _extract_blockers(self, content: str) -> list[str]:
        """Extract blockers from document content."""
        blockers = []

        # Look for blockers section
        blockers_match = re.search(
            r"##\s*(?:Current\s+)?Blockers?\s*\n((?:[-*]\s+.*\n)+)",
            content,
            re.IGNORECASE,
        )
        if blockers_match:
            for line in blockers_match.group(1).strip().split("\n"):
                blocker = re.sub(r"^[-*]\s+", "", line).strip()
                if blocker:
                    blockers.append(blocker)

        return blockers

    def _extract_priorities(self, content: str) -> list[str]:
        """Extract priorities from document content."""
        priorities = []

        # Look for priorities table or list
        # Matches: "Priorities", "Top 5", "Top 5 Priorities", "Next Steps"
        priority_match = re.search(
            r"##\s*(?:(?:Top\s+\d+\s+)?Priorities?|Top\s+\d+|Next\s+Steps?)\s*\n((?:.*\n)+?)(?=\n##|\Z)",
            content,
            re.IGNORECASE,
        )
        if priority_match:
            for line in priority_match.group(1).strip().split("\n"):
                # Extract from table rows or list items
                if "|" in line and "---" not in line:
                    parts = [p.strip() for p in line.split("|") if p.strip()]
                    if len(parts) >= 2:
                        priorities.append(parts[1] if parts[0].isdigit() else parts[0])
                elif line.startswith(("-", "*", "1", "2", "3")):
                    priority = re.sub(r"^[-*\d.]+\s+", "", line).strip()
                    if priority:
                        priorities.append(priority)

        return priorities[:5]  # Top 5 only

    def _extract_milestones(self, content: str) -> list[str]:
        """Extract recent milestones from progress.md."""
        milestones = []

        # Look for dated entries (## YYYY-MM-DD - Description)
        milestone_matches = re.findall(
            r"##\s*(\d{4}-\d{2}-\d{2})\s*[-–]\s*(.+)",
            content,
        )
        for date, description in milestone_matches[:5]:  # Last 5
            milestones.append(f"{date}: {description.strip()}")

        return milestones

    def _extract_sprint(self, content: str) -> str | None:
        """Extract current sprint from PLAN.md."""
        sprint_match = re.search(
            r"##\s*(Sprint\s+\d+[^#\n]*)",
            content,
            re.IGNORECASE,
        )
        if sprint_match:
            return sprint_match.group(1).strip()
        return None

    def consult(self, domain: str, project: str) -> LivingDocsContext:
        """
        Load context from living docs pyramid before starting work.

        Returns structured context with:
        - All relevant documents
        - Extracted blockers, priorities, milestones
        - Staleness warnings
        """
        # Level 0: Portfolio
        portfolio_digest = self._read_doc(self.forge_root / "docs" / "00-portfolio-digest.md")

        # Level 1: Domain
        domain_snapshot = self._read_doc(self.forge_root / "docs" / "domains" / f"{domain}.md")

        # Level 2: Project
        project_dir = self.forge_root / domain / project / "docs"
        project_plan = self._read_doc(project_dir / "PLAN.md")
        project_progress = self._read_doc(project_dir / "progress.md")
        active_context = self._read_doc(project_dir / "active-context.md")
        project_brief = self._read_doc(project_dir / "project-brief.md")

        # Extract metadata
        blockers = []
        priorities = []
        milestones = []
        current_sprint = None

        if active_context.content:
            blockers.extend(self._extract_blockers(active_context.content))
            priorities.extend(self._extract_priorities(active_context.content))

        if project_plan.content:
            current_sprint = self._extract_sprint(project_plan.content)
            priorities.extend(self._extract_priorities(project_plan.content))

        if project_progress.content:
            milestones = self._extract_milestones(project_progress.content)

        if portfolio_digest.content:
            blockers.extend(self._extract_blockers(portfolio_digest.content))

        return LivingDocsContext(
            portfolio_digest=portfolio_digest,
            domain_snapshot=domain_snapshot,
            project_plan=project_plan,
            project_progress=project_progress,
            active_context=active_context,
            project_brief=project_brief if project_brief.exists else None,
            current_sprint=current_sprint,
            blockers=list(set(blockers)),  # Dedupe
            priorities=priorities[:5],  # Top 5
            recent_milestones=milestones,
        )

    def update(
        self,
        domain: str,
        project: str,
        milestone: str,
        details: str,
        files_changed: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> list[Path]:
        """
        Update living docs after completing work.

        Follows bubble-up pattern:
        1. Update Level 3 (project progress.md) first
        2. Update active-context.md
        3. Bubble to domain snapshot if significant
        4. Update portfolio digest if priorities shift

        Returns list of files updated.
        """
        updated_files = []
        today = datetime.now().strftime("%Y-%m-%d")

        # 1. Update project progress.md (Level 3)
        progress_path = self.forge_root / domain / project / "docs" / "progress.md"
        if progress_path.exists():
            progress_content = progress_path.read_text()

            # Build new entry
            entry_lines = [f"\n## {today} - {milestone}\n"]
            entry_lines.append(f"\n{details}\n")

            if files_changed:
                entry_lines.append("\n### Files Changed\n")
                for f in files_changed[:10]:  # Limit to 10
                    entry_lines.append(f"- `{f}`\n")

            if metrics:
                entry_lines.append("\n### Metrics\n")
                for key, value in metrics.items():
                    entry_lines.append(f"- **{key}**: {value}\n")

            new_entry = "".join(entry_lines)

            # Insert after the first heading or at the top
            if "## " in progress_content:
                # Find first ## and insert before it
                first_section = progress_content.find("\n## ")
                if first_section > 0:
                    progress_content = (
                        progress_content[:first_section]
                        + new_entry
                        + progress_content[first_section:]
                    )
                else:
                    progress_content = new_entry + progress_content
            else:
                progress_content = f"# Progress\n{new_entry}\n{progress_content}"

            progress_path.write_text(progress_content)
            updated_files.append(progress_path)

        # 2. Update active-context.md
        active_path = self.forge_root / domain / project / "docs" / "active-context.md"
        if active_path.exists():
            active_content = active_path.read_text()

            # Update Last Updated
            active_content = re.sub(
                r"\*\*Last Updated\*\*:\s*\d{4}-\d{2}-\d{2}",
                f"**Last Updated**: {today}",
                active_content,
            )

            # Add to recent decisions if not already there
            if "Recent Decisions" in active_content and milestone not in active_content:
                decision_entry = f"\n### {today}: {milestone}\n- {details.split('.')[0]}.\n"
                active_content = re.sub(
                    r"(## Recent Decisions\n)",
                    rf"\1{decision_entry}",
                    active_content,
                )

            active_path.write_text(active_content)
            updated_files.append(active_path)

        return updated_files

    def sync(self, domain: str, project: str) -> dict[str, Any]:
        """
        Full pyramid sync before session handoff.

        Returns sync status report.
        """
        report = {
            "synced_at": datetime.now().isoformat(),
            "files_checked": [],
            "files_updated": [],
            "stale_files": [],
            "missing_files": [],
            "warnings": [],
        }

        # Check all pyramid files
        files_to_check = [
            (self.forge_root / "docs" / "00-portfolio-digest.md", "Level 0"),
            (self.forge_root / "docs" / "domains" / f"{domain}.md", "Level 1"),
            (self.forge_root / domain / project / "docs" / "PLAN.md", "Level 2"),
            (self.forge_root / domain / project / "docs" / "progress.md", "Level 2"),
            (self.forge_root / domain / project / "docs" / "active-context.md", "Level 2"),
        ]

        for path, level in files_to_check:
            report["files_checked"].append(str(path))

            if not path.exists():
                report["missing_files"].append(str(path))
                report["warnings"].append(f"{level} missing: {path.name}")
                continue

            doc_state = self._read_doc(path)
            if doc_state.is_stale:
                report["stale_files"].append(str(path))
                report["warnings"].append(
                    f"{level} stale ({doc_state.last_updated.strftime('%Y-%m-%d') if doc_state.last_updated else 'unknown'}): {path.name}"
                )

        return report

    def get_context_summary(self, context: LivingDocsContext) -> str:
        """
        Generate a concise context summary for agent prompts.

        Returns markdown-formatted summary of current state.
        """
        lines = ["# Living Docs Context\n"]

        # Current sprint
        if context.current_sprint:
            lines.append(f"**Current Sprint:** {context.current_sprint}\n")

        # Blockers
        if context.blockers:
            lines.append("\n## Blockers\n")
            for blocker in context.blockers:
                lines.append(f"- {blocker}\n")

        # Priorities
        if context.priorities:
            lines.append("\n## Priorities\n")
            for i, priority in enumerate(context.priorities, 1):
                lines.append(f"{i}. {priority}\n")

        # Recent milestones
        if context.recent_milestones:
            lines.append("\n## Recent Milestones\n")
            for milestone in context.recent_milestones[:3]:
                lines.append(f"- {milestone}\n")

        # Stale warnings
        stale_docs = []
        for doc in [
            context.portfolio_digest,
            context.domain_snapshot,
            context.project_plan,
            context.project_progress,
            context.active_context,
        ]:
            if doc.is_stale:
                stale_docs.append(doc.path.name)

        if stale_docs:
            lines.append("\n## Stale Documents (needs update)\n")
            for doc in stale_docs:
                lines.append(f"- {doc}\n")

        return "".join(lines)
