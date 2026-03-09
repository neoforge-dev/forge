"""PR generator for FORGE Harness.

Creates GitHub pull requests with iteration context and summaries.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class PRContext:
    """Context for generating a pull request."""

    title: str
    features: list[str] = field(default_factory=list)
    tests_added: int = 0
    lines_added: int = 0
    iterations_completed: int = 0
    base_branch: str = "main"
    labels: list[str] = field(default_factory=list)
    reviewers: list[str] = field(default_factory=list)
    draft: bool = False


@dataclass
class PRResult:
    """Result of PR creation."""

    success: bool
    pr_url: str | None = None
    pr_number: int | None = None
    error: str | None = None


class PRGenerator:
    """Generates and creates GitHub pull requests."""

    def __init__(self, repo_path: Path | None = None):
        self.repo_path = repo_path or Path.cwd()

    def get_current_branch(self) -> str:
        """Get current git branch name."""
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def get_commits_since_base(self, base_branch: str = "main") -> list[str]:
        """Get commit messages since branching from base."""
        result = subprocess.run(
            ["git", "log", f"{base_branch}..HEAD", "--oneline"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        return [line for line in result.stdout.strip().split("\n") if line]

    def generate_body(self, context: PRContext) -> str:
        """Generate PR body content."""
        lines = []

        # Summary section
        lines.append("## Summary")
        if context.features:
            for feat in context.features[:5]:
                lines.append(f"- {feat}")
            if len(context.features) > 5:
                lines.append(f"- ... and {len(context.features) - 5} more features")
        else:
            lines.append("- Code improvements and updates")

        # Metrics section
        if context.iterations_completed or context.tests_added or context.lines_added:
            lines.append("")
            lines.append("## Metrics")
            if context.iterations_completed:
                lines.append(f"- **Iterations completed:** {context.iterations_completed}")
            if context.tests_added:
                lines.append(f"- **Tests added:** {context.tests_added}")
            if context.lines_added:
                lines.append(f"- **Lines added:** {context.lines_added:,}")

        # Test plan section
        lines.append("")
        lines.append("## Test Plan")
        lines.append("- [ ] All tests passing")
        lines.append("- [ ] No type errors")
        lines.append("- [ ] No security vulnerabilities")
        lines.append("- [ ] Documentation updated if needed")

        # Footer
        lines.append("")
        lines.append("---")
        lines.append("Generated with [Claude Code](https://claude.com/claude-code)")

        return "\n".join(lines)

    def create_pr(self, context: PRContext) -> PRResult:
        """Create a GitHub pull request using gh CLI."""
        body = self.generate_body(context)

        cmd = [
            "gh",
            "pr",
            "create",
            "--title",
            context.title,
            "--body",
            body,
            "--base",
            context.base_branch,
        ]

        if context.draft:
            cmd.append("--draft")

        for label in context.labels:
            cmd.extend(["--label", label])

        for reviewer in context.reviewers:
            cmd.extend(["--reviewer", reviewer])

        try:
            result = subprocess.run(cmd, cwd=self.repo_path, capture_output=True, text=True)

            if result.returncode != 0:
                return PRResult(success=False, error=result.stderr)

            # Extract PR URL from output
            pr_url = result.stdout.strip()
            pr_number = None

            # Try to parse PR number from URL
            if "/" in pr_url:
                try:
                    pr_number = int(pr_url.split("/")[-1])
                except ValueError:
                    pass

            return PRResult(success=True, pr_url=pr_url, pr_number=pr_number)

        except FileNotFoundError:
            return PRResult(success=False, error="gh CLI not found. Install with: brew install gh")
        except Exception as e:
            return PRResult(success=False, error=str(e))

    def check_gh_auth(self) -> bool:
        """Check if gh CLI is authenticated."""
        try:
            result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
            return result.returncode == 0
        except FileNotFoundError:
            return False


def create_pr_generator(repo_path: Path | None = None) -> PRGenerator:
    """Factory function to create a PRGenerator."""
    return PRGenerator(repo_path)
