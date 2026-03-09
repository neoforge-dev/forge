"""Git operations module for FORGE Harness.

Provides programmatic git operations for the iteration loop.
"""

import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class BranchStatus(str, Enum):
    """Branch status relative to remote."""

    UP_TO_DATE = "up_to_date"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    NO_REMOTE = "no_remote"


@dataclass
class GitStatus:
    """Current git status."""

    branch: str
    staged: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    branch_status: BranchStatus = BranchStatus.UP_TO_DATE
    ahead_count: int = 0
    behind_count: int = 0


@dataclass
class MergeResult:
    """Result of a merge operation."""

    success: bool
    conflicts: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class BranchInfo:
    """Information about a branch."""

    name: str
    is_current: bool = False
    remote: str | None = None
    last_commit: str | None = None


class GitOps:
    """Git operations helper."""

    def __init__(self, repo_path: Path | None = None):
        self.repo_path = repo_path or Path.cwd()

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command."""
        cmd = ["git"] + list(args)
        return subprocess.run(cmd, cwd=self.repo_path, capture_output=True, text=True, check=check)

    def get_status(self) -> GitStatus:
        """Get current git status."""
        try:
            # Get branch name
            result = self._run("rev-parse", "--abbrev-ref", "HEAD")
            branch = result.stdout.strip()

            # Get status
            result = self._run("status", "--porcelain")

            staged = []
            modified = []
            untracked = []
            conflicts = []

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue

                status = line[:2]
                file_path = line[3:]

                if status == "??":
                    untracked.append(file_path)
                elif status[0] in "MADRC":
                    staged.append(file_path)
                elif status[1] in "MD":
                    modified.append(file_path)
                elif "U" in status:
                    conflicts.append(file_path)

            # Check branch status
            branch_status, ahead, behind = self._get_branch_status()

            return GitStatus(
                branch=branch,
                staged=staged,
                modified=modified,
                untracked=untracked,
                conflicts=conflicts,
                branch_status=branch_status,
                ahead_count=ahead,
                behind_count=behind,
            )

        except Exception as e:
            logger.error(f"Failed to get status: {e}")
            return GitStatus(branch="unknown")

    def _get_branch_status(self) -> tuple[BranchStatus, int, int]:
        """Get branch status relative to remote."""
        try:
            result = self._run("status", "-sb", check=False)
            output = result.stdout

            ahead = 0
            behind = 0

            # Parse ahead/behind from status
            match = re.search(r"\[ahead (\d+)", output)
            if match:
                ahead = int(match.group(1))

            match = re.search(r"behind (\d+)\]", output)
            if match:
                behind = int(match.group(1))

            if ahead > 0 and behind > 0:
                return BranchStatus.DIVERGED, ahead, behind
            elif ahead > 0:
                return BranchStatus.AHEAD, ahead, behind
            elif behind > 0:
                return BranchStatus.BEHIND, ahead, behind
            elif "..." not in output:
                return BranchStatus.NO_REMOTE, 0, 0
            else:
                return BranchStatus.UP_TO_DATE, 0, 0

        except Exception:
            return BranchStatus.NO_REMOTE, 0, 0

    def create_branch(self, name: str, from_branch: str | None = None) -> bool:
        """Create a new branch."""
        try:
            if from_branch:
                self._run("checkout", "-b", name, from_branch)
            else:
                self._run("checkout", "-b", name)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to create branch: {e.stderr}")
            return False

    def switch_branch(self, name: str) -> bool:
        """Switch to a branch."""
        try:
            self._run("checkout", name)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to switch branch: {e.stderr}")
            return False

    def delete_branch(self, name: str, force: bool = False) -> bool:
        """Delete a branch."""
        try:
            flag = "-D" if force else "-d"
            self._run("branch", flag, name)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to delete branch: {e.stderr}")
            return False

    def list_branches(self, include_remote: bool = False) -> list[BranchInfo]:
        """List all branches."""
        try:
            args = ["branch", "-v"]
            if include_remote:
                args.append("-a")

            result = self._run(*args)
            branches = []

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue

                is_current = line.startswith("*")
                line = line.lstrip("* ")
                parts = line.split(None, 2)

                if len(parts) >= 2:
                    branches.append(
                        BranchInfo(
                            name=parts[0],
                            is_current=is_current,
                            last_commit=parts[1] if len(parts) > 1 else None,
                        )
                    )

            return branches

        except Exception as e:
            logger.error(f"Failed to list branches: {e}")
            return []

    def get_current_branch(self) -> str:
        """Get current branch name."""
        result = self._run("rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout.strip()

    def has_conflicts(self) -> bool:
        """Check if there are merge conflicts."""
        status = self.get_status()
        return len(status.conflicts) > 0

    def merge(self, branch: str, no_ff: bool = False) -> MergeResult:
        """Merge a branch into current branch."""
        try:
            args = ["merge", branch]
            if no_ff:
                args.append("--no-ff")

            result = self._run(*args, check=False)

            if result.returncode != 0:
                # Check for conflicts
                status = self.get_status()
                if status.conflicts:
                    return MergeResult(
                        success=False,
                        conflicts=status.conflicts,
                    )
                return MergeResult(
                    success=False,
                    error=result.stderr,
                )

            return MergeResult(success=True)

        except Exception as e:
            return MergeResult(success=False, error=str(e))

    def abort_merge(self) -> bool:
        """Abort an in-progress merge."""
        try:
            self._run("merge", "--abort")
            return True
        except subprocess.CalledProcessError:
            return False

    def stash(self, message: str | None = None) -> bool:
        """Stash current changes."""
        try:
            args = ["stash", "push"]
            if message:
                args.extend(["-m", message])
            self._run(*args)
            return True
        except subprocess.CalledProcessError:
            return False

    def stash_pop(self) -> bool:
        """Pop the most recent stash."""
        try:
            self._run("stash", "pop")
            return True
        except subprocess.CalledProcessError:
            return False

    def stash_list(self) -> list[str]:
        """List all stashes."""
        try:
            result = self._run("stash", "list")
            return [line for line in result.stdout.strip().split("\n") if line]
        except subprocess.CalledProcessError:
            return []

    def fetch(self, remote: str = "origin") -> bool:
        """Fetch from remote."""
        try:
            self._run("fetch", remote)
            return True
        except subprocess.CalledProcessError:
            return False

    def pull(self, remote: str = "origin", branch: str | None = None) -> bool:
        """Pull from remote."""
        try:
            args = ["pull", remote]
            if branch:
                args.append(branch)
            self._run(*args)
            return True
        except subprocess.CalledProcessError:
            return False

    def push(
        self, remote: str = "origin", branch: str | None = None, set_upstream: bool = False
    ) -> bool:
        """Push to remote."""
        try:
            args = ["push"]
            if set_upstream:
                args.append("-u")
            args.append(remote)
            if branch:
                args.append(branch)
            self._run(*args)
            return True
        except subprocess.CalledProcessError:
            return False


def create_git_ops(repo_path: Path | None = None) -> GitOps:
    """Factory function to create a GitOps instance."""
    return GitOps(repo_path)
