"""Auto-commit generator for FORGE Harness.

Generates conventional commits based on changes and iteration context.
"""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class CommitContext:
    """Context for generating a commit message."""

    features: list[str] = field(default_factory=list)
    tests_added: int = 0
    lines_added: int = 0
    files_changed: list[str] = field(default_factory=list)
    iteration_number: int | None = None
    scope: str = "harness"
    breaking_change: bool = False


@dataclass
class CommitResult:
    """Result of a commit operation."""

    success: bool
    commit_hash: str | None = None
    message: str = ""
    error: str | None = None


class AutoCommitter:
    """Generates and creates conventional commits."""

    COMMIT_TYPES = {
        "feat": "New feature",
        "fix": "Bug fix",
        "docs": "Documentation",
        "refactor": "Code refactoring",
        "test": "Adding tests",
        "chore": "Maintenance",
        "perf": "Performance improvement",
        "style": "Code style",
    }

    def __init__(self, repo_path: Path | None = None):
        self.repo_path = repo_path or Path.cwd()

    def get_staged_changes(self) -> tuple[list[str], int, int]:
        """Get list of staged files and change counts."""
        result = subprocess.run(
            ["git", "diff", "--cached", "--stat"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )

        files = []
        additions = 0
        deletions = 0

        for line in result.stdout.strip().split("\n"):
            if "|" in line:
                file_part = line.split("|")[0].strip()
                files.append(file_part)
            elif "insertions" in line or "deletions" in line:
                # Parse summary line
                match = re.search(r"(\d+) insertions?", line)
                if match:
                    additions = int(match.group(1))
                match = re.search(r"(\d+) deletions?", line)
                if match:
                    deletions = int(match.group(1))

        return files, additions, deletions

    def detect_commit_type(self, files: list[str], context: CommitContext) -> str:
        """Detect appropriate commit type based on changes."""
        if context.features:
            return "feat"

        test_files = [f for f in files if "test" in f.lower()]
        if len(test_files) == len(files):
            return "test"

        doc_files = [f for f in files if f.endswith((".md", ".rst", ".txt"))]
        if len(doc_files) == len(files):
            return "docs"

        return "chore"

    def generate_message(self, context: CommitContext) -> str:
        """Generate a conventional commit message."""
        files, additions, deletions = self.get_staged_changes()
        if not files:
            files = context.files_changed

        commit_type = self.detect_commit_type(files, context)

        # Build subject line
        if context.features:
            subject = f"add {', '.join(context.features[:2])}"
            if len(context.features) > 2:
                subject += f" and {len(context.features) - 2} more"
        else:
            subject = f"update {len(files)} file(s)"

        subject = f"{commit_type}({context.scope}): {subject}"

        # Build body
        body_lines = []

        if context.iteration_number:
            body_lines.append(f"Iteration {context.iteration_number} of FORGE Harness engineering.")

        if context.features:
            body_lines.append("")
            body_lines.append("Features:")
            for feat in context.features:
                body_lines.append(f"- {feat}")

        if context.tests_added:
            body_lines.append("")
            body_lines.append(f"Tests: {context.tests_added} added")

        if context.breaking_change:
            body_lines.append("")
            body_lines.append("BREAKING CHANGE: This commit contains breaking changes.")

        # Add co-author
        body_lines.append("")
        body_lines.append("Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>")

        return subject + "\n\n" + "\n".join(body_lines)

    def commit(self, context: CommitContext) -> CommitResult:
        """Create a commit with generated message."""
        message = self.generate_message(context)

        try:
            result = subprocess.run(
                ["git", "commit", "-m", message], cwd=self.repo_path, capture_output=True, text=True
            )

            if result.returncode != 0:
                return CommitResult(success=False, message=message, error=result.stderr)

            # Get commit hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.repo_path, capture_output=True, text=True
            )

            return CommitResult(
                success=True, commit_hash=hash_result.stdout.strip()[:8], message=message
            )

        except Exception as e:
            return CommitResult(success=False, message=message, error=str(e))

    def stage_files(self, paths: list[Path]) -> bool:
        """Stage files for commit."""
        try:
            result = subprocess.run(
                ["git", "add"] + [str(p) for p in paths],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except Exception:
            return False


def create_auto_committer(repo_path: Path | None = None) -> AutoCommitter:
    """Factory function to create an AutoCommitter."""
    return AutoCommitter(repo_path)
