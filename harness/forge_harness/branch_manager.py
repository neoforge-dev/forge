"""
Branch Manager for FORGE Agent Sessions
========================================

Manages git branch creation and management for agent coding sessions.
Enforces the FORGE rule: "Never commit to main".
"""

import re
import subprocess
from pathlib import Path


def slugify(text: str, max_length: int = 30) -> str:
    """
    Convert text to branch-safe slug.

    Args:
        text: The text to slugify
        max_length: Maximum length of the slug

    Returns:
        A lowercase, hyphen-separated slug
    """
    # Convert to lowercase and replace non-alphanumeric with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    # Strip leading/trailing hyphens
    slug = slug.strip("-")
    # Truncate to max length and clean up trailing hyphens
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug


class BranchManager:
    """
    Manages git branches for FORGE agent sessions.

    Enforces branching conventions:
    - Feature branches: feat/{issue_number}-{slug}
    - Never work on main/master
    """

    PROTECTED_BRANCHES = {"main", "master", "develop", "production"}

    def __init__(self, project_dir: Path):
        """
        Initialize branch manager.

        Args:
            project_dir: Path to the project directory (git repository)
        """
        self.project_dir = Path(project_dir)

    def get_current_branch(self) -> str:
        """
        Get the current git branch name.

        Returns:
            The current branch name
        """
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=self.project_dir,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def is_on_protected_branch(self) -> bool:
        """
        Check if currently on a protected branch (main, master, etc).

        Returns:
            True if on a protected branch
        """
        current = self.get_current_branch()
        return current in self.PROTECTED_BRANCHES

    def ensure_not_on_main(self) -> None:
        """
        Ensure we're not on a protected branch.

        Raises:
            RuntimeError: If on a protected branch
        """
        if self.is_on_protected_branch():
            current = self.get_current_branch()
            raise RuntimeError(
                f"Cannot work on protected branch '{current}'. Create a feature branch first."
            )

    def create_feature_branch(
        self,
        issue_number: int,
        issue_title: str,
        from_remote: bool = True,
    ) -> str:
        """
        Create and checkout a feature branch for an issue.

        Args:
            issue_number: GitHub issue number
            issue_title: Issue title (will be slugified)
            from_remote: If True, branch from origin/main. If False, branch from current HEAD.

        Returns:
            The created branch name
        """
        # Create branch name
        slug = slugify(issue_title)
        branch_name = f"feat/{issue_number}-{slug}"

        # Limit total length
        if len(branch_name) > 50:
            # Truncate slug portion
            max_slug = 50 - len(f"feat/{issue_number}-")
            slug = slugify(issue_title, max_length=max_slug)
            branch_name = f"feat/{issue_number}-{slug}"

        if from_remote:
            # Fetch latest from remote
            subprocess.run(
                ["git", "fetch", "origin", "main"],
                cwd=self.project_dir,
                capture_output=True,
            )

            # Create branch from origin/main
            subprocess.run(
                ["git", "checkout", "-b", branch_name, "origin/main"],
                cwd=self.project_dir,
                capture_output=True,
                check=True,
            )
        else:
            # Create branch from current HEAD (local mode)
            subprocess.run(
                ["git", "checkout", "-b", branch_name],
                cwd=self.project_dir,
                capture_output=True,
                check=True,
            )

        return branch_name

    def push_branch(self, branch_name: str) -> bool:
        """
        Push a feature branch to remote.

        Args:
            branch_name: The branch name to push

        Returns:
            True if push succeeded, False otherwise
        """
        result = subprocess.run(
            ["git", "push", "-u", "origin", branch_name],
            cwd=self.project_dir,
            capture_output=True,
        )
        return result.returncode == 0

    def checkout_existing_branch(self, branch_name: str) -> bool:
        """
        Checkout an existing branch.

        Args:
            branch_name: The branch to checkout

        Returns:
            True if checkout succeeded, False otherwise
        """
        result = subprocess.run(
            ["git", "checkout", branch_name],
            cwd=self.project_dir,
            capture_output=True,
        )
        return result.returncode == 0

    def branch_exists(self, branch_name: str) -> bool:
        """
        Check if a branch exists locally.

        Args:
            branch_name: The branch name to check

        Returns:
            True if branch exists
        """
        result = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=self.project_dir,
            capture_output=True,
            text=True,
        )
        return bool(result.stdout.strip())
