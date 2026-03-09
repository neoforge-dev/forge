"""Changelog generator for FORGE Harness.

Generates changelogs from conventional commits.
"""

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class CommitEntry:
    """A parsed commit entry."""

    hash: str
    type: str  # feat, fix, docs, etc.
    scope: str | None
    description: str
    body: str = ""
    breaking: bool = False


@dataclass
class ChangelogSection:
    """A section of the changelog."""

    title: str
    emoji: str
    commits: list[CommitEntry] = field(default_factory=list)


@dataclass
class ChangelogResult:
    """Result of changelog generation."""

    success: bool
    version: str | None = None
    content: str = ""
    sections: list[ChangelogSection] = field(default_factory=list)
    error: str | None = None


class ChangelogGenerator:
    """Generates changelogs from conventional commits."""

    # Commit type mappings
    TYPE_MAPPING = {
        "feat": ("Features", "✨"),
        "fix": ("Bug Fixes", "🐛"),
        "docs": ("Documentation", "📚"),
        "refactor": ("Code Refactoring", "♻️"),
        "test": ("Tests", "✅"),
        "chore": ("Maintenance", "🔧"),
        "perf": ("Performance", "⚡"),
        "style": ("Styling", "💄"),
        "ci": ("CI/CD", "👷"),
        "build": ("Build", "🏗️"),
    }

    COMMIT_PATTERN = re.compile(
        r"^(?P<type>\w+)(?:\((?P<scope>[^)]+)\))?"
        r"(?P<breaking>!)?"
        r":\s*(?P<description>.+)$"
    )

    def __init__(self, repo_path: Path | None = None):
        self.repo_path = repo_path or Path.cwd()

    def get_commits_since_tag(self, tag: str | None = None) -> list[CommitEntry]:
        """Get commits since the given tag (or all commits if no tag)."""
        try:
            if tag:
                cmd = ["git", "log", f"{tag}..HEAD", "--pretty=format:%H|%s|%b|||"]
            else:
                cmd = ["git", "log", "--pretty=format:%H|%s|%b|||"]

            result = subprocess.run(cmd, cwd=self.repo_path, capture_output=True, text=True)

            if result.returncode != 0:
                return []

            commits = []
            for entry in result.stdout.split("|||"):
                entry = entry.strip()
                if not entry:
                    continue

                parts = entry.split("|", 2)
                if len(parts) < 2:
                    continue

                hash_val = parts[0]
                subject = parts[1]
                body = parts[2] if len(parts) > 2 else ""

                parsed = self._parse_commit_message(hash_val, subject, body)
                if parsed:
                    commits.append(parsed)

            return commits

        except Exception as e:
            logger.error(f"Failed to get commits: {e}")
            return []

    def _parse_commit_message(self, hash_val: str, subject: str, body: str) -> CommitEntry | None:
        """Parse a commit message into structured format."""
        match = self.COMMIT_PATTERN.match(subject)
        if not match:
            return None

        breaking = bool(match.group("breaking")) or "BREAKING CHANGE" in body

        return CommitEntry(
            hash=hash_val[:8],
            type=match.group("type"),
            scope=match.group("scope"),
            description=match.group("description"),
            body=body.strip(),
            breaking=breaking,
        )

    def get_latest_tag(self) -> str | None:
        """Get the latest version tag."""
        try:
            result = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except Exception:
            return None

    def determine_version_bump(
        self, commits: list[CommitEntry], current_version: str = "0.0.0"
    ) -> str:
        """Determine the next version based on commits."""
        # Parse current version
        match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", current_version)
        if match:
            major, minor, patch = map(int, match.groups())
        else:
            major, minor, patch = 0, 0, 0

        has_breaking = any(c.breaking for c in commits)
        has_features = any(c.type == "feat" for c in commits)
        has_fixes = any(c.type == "fix" for c in commits)

        if has_breaking:
            major += 1
            minor = 0
            patch = 0
        elif has_features:
            minor += 1
            patch = 0
        elif has_fixes or commits:
            patch += 1

        return f"v{major}.{minor}.{patch}"

    def generate(self, since_tag: str | None = None, version: str | None = None) -> ChangelogResult:
        """Generate changelog content."""
        try:
            commits = self.get_commits_since_tag(since_tag)
            if not commits:
                return ChangelogResult(
                    success=True,
                    content="No changes to document.",
                    error="No conventional commits found",
                )

            # Determine version
            if not version:
                current = since_tag or "0.0.0"
                version = self.determine_version_bump(commits, current)

            # Group commits by type
            sections: dict[str, ChangelogSection] = {}
            breaking_changes: list[CommitEntry] = []

            for commit in commits:
                if commit.breaking:
                    breaking_changes.append(commit)

                if commit.type in self.TYPE_MAPPING:
                    title, emoji = self.TYPE_MAPPING[commit.type]
                    if commit.type not in sections:
                        sections[commit.type] = ChangelogSection(title=title, emoji=emoji)
                    sections[commit.type].commits.append(commit)

            # Generate markdown
            lines = [
                f"# {version}",
                "",
                f"*Released: {datetime.now().strftime('%Y-%m-%d')}*",
                "",
            ]

            # Breaking changes first
            if breaking_changes:
                lines.append("## ⚠️ Breaking Changes")
                lines.append("")
                for commit in breaking_changes:
                    scope_str = f"**{commit.scope}:** " if commit.scope else ""
                    lines.append(f"- {scope_str}{commit.description} ({commit.hash})")
                lines.append("")

            # Other sections in order
            for type_key in [
                "feat",
                "fix",
                "perf",
                "refactor",
                "docs",
                "test",
                "chore",
                "style",
                "ci",
                "build",
            ]:
                if type_key in sections:
                    section = sections[type_key]
                    lines.append(f"## {section.emoji} {section.title}")
                    lines.append("")
                    for commit in section.commits:
                        scope_str = f"**{commit.scope}:** " if commit.scope else ""
                        lines.append(f"- {scope_str}{commit.description} ({commit.hash})")
                    lines.append("")

            content = "\n".join(lines)

            return ChangelogResult(
                success=True,
                version=version,
                content=content,
                sections=list(sections.values()),
            )

        except Exception as e:
            return ChangelogResult(success=False, error=str(e))

    def write_changelog(
        self, output_path: Path | None = None, prepend: bool = True
    ) -> ChangelogResult:
        """Generate and write changelog to file."""
        result = self.generate()
        if not result.success:
            return result

        output_path = output_path or self.repo_path / "CHANGELOG.md"

        try:
            if prepend and output_path.exists():
                existing = output_path.read_text()
                content = result.content + "\n\n---\n\n" + existing
            else:
                content = result.content

            output_path.write_text(content)
            logger.info(f"Wrote changelog to {output_path}")

            return result

        except Exception as e:
            result.success = False
            result.error = str(e)
            return result


def create_changelog_generator(repo_path: Path | None = None) -> ChangelogGenerator:
    """Factory function to create a ChangelogGenerator."""
    return ChangelogGenerator(repo_path)
