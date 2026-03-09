"""
Git Worktree Manager
====================

Manages git worktrees for parallel agent execution.
Enables "1 human, multiple agents" by isolating agent workspaces.
"""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Worktree:
    path: Path
    branch: str
    head_hash: str


class WorktreeManager:
    """Manages git worktrees for a repository."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def list_worktrees(self) -> list[Worktree]:
        """List all worktrees."""
        try:
            result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=True,
            )

            worktrees = []
            current_worktree = {}

            for line in result.stdout.splitlines():
                if line.startswith("worktree "):
                    if current_worktree:
                        worktrees.append(self._parse_worktree(current_worktree))
                        current_worktree = {}
                    current_worktree["path"] = line.split(" ", 1)[1]
                elif line.startswith("branch "):
                    current_worktree["branch"] = line.split(" ", 1)[1].replace("refs/heads/", "")
                elif line.startswith("HEAD "):
                    current_worktree["head"] = line.split(" ", 1)[1]

            if current_worktree:
                worktrees.append(self._parse_worktree(current_worktree))

            return worktrees
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to list worktrees: {e}")
            return []

    def _parse_worktree(self, data: dict) -> Worktree:
        return Worktree(
            path=Path(data.get("path", "")),
            branch=data.get("branch", "detached"),
            head_hash=data.get("head", ""),
        )

    def create_worktree(self, branch: str, path: Path | None = None) -> Path:
        """Create a new worktree for a branch.

        Args:
            branch: Branch name to check out
            path: Optional path for worktree. Defaults to ../{repo_name}-{branch}

        Returns:
            Path to the created worktree
        """
        if path is None:
            # Default convention: sibling directory
            repo_name = self.repo_root.name
            safe_branch = branch.replace("/", "-")
            path = self.repo_root.parent / f"{repo_name}-{safe_branch}"

        if path.exists():
            logger.info(f"Worktree path {path} already exists")
            return path

        try:
            # Create branch if it doesn't exist
            # Check if branch exists
            branch_exists = (
                subprocess.run(
                    ["git", "show-ref", "--verify", f"refs/heads/{branch}"],
                    cwd=self.repo_root,
                    capture_output=True,
                ).returncode
                == 0
            )

            cmd = ["git", "worktree", "add"]
            if not branch_exists:
                cmd.append("-b")
            cmd.extend([str(path), branch])

            subprocess.run(cmd, cwd=self.repo_root, check=True, capture_output=True)
            logger.info(f"Created worktree for {branch} at {path}")
            return path
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to create worktree: {e.stderr}")
            raise RuntimeError(f"Failed to create worktree: {e.stderr}")

    def remove_worktree(self, path: Path, force: bool = False):
        """Remove a worktree."""
        try:
            cmd = ["git", "worktree", "remove", str(path)]
            if force:
                cmd.append("--force")

            subprocess.run(cmd, cwd=self.repo_root, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to remove worktree: {e}")
            raise

    def prune(self):
        """Prune stale worktree information."""
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo_root, check=True)
