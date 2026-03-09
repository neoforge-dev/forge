"""Tests for git operations module."""

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from forge_harness.iteration.git_ops import (
    BranchInfo,
    BranchStatus,
    GitOps,
    GitStatus,
    MergeResult,
    create_git_ops,
)


class TestBranchStatus:
    """Tests for BranchStatus enum."""

    def test_branch_status_values(self):
        """Test all branch status values exist."""
        assert BranchStatus.UP_TO_DATE == "up_to_date"
        assert BranchStatus.AHEAD == "ahead"
        assert BranchStatus.BEHIND == "behind"
        assert BranchStatus.DIVERGED == "diverged"
        assert BranchStatus.NO_REMOTE == "no_remote"


class TestGitStatus:
    """Tests for GitStatus dataclass."""

    def test_git_status_defaults(self):
        """Test GitStatus default values."""
        status = GitStatus(branch="main")
        assert status.branch == "main"
        assert status.staged == []
        assert status.modified == []
        assert status.untracked == []
        assert status.conflicts == []
        assert status.branch_status == BranchStatus.UP_TO_DATE
        assert status.ahead_count == 0
        assert status.behind_count == 0

    def test_git_status_with_values(self):
        """Test GitStatus with custom values."""
        status = GitStatus(
            branch="feature",
            staged=["file1.py"],
            modified=["file2.py"],
            untracked=["file3.py"],
            conflicts=["file4.py"],
            branch_status=BranchStatus.AHEAD,
            ahead_count=2,
            behind_count=1,
        )
        assert status.branch == "feature"
        assert status.staged == ["file1.py"]
        assert status.modified == ["file2.py"]
        assert status.untracked == ["file3.py"]
        assert status.conflicts == ["file4.py"]
        assert status.branch_status == BranchStatus.AHEAD
        assert status.ahead_count == 2
        assert status.behind_count == 1


class TestMergeResult:
    """Tests for MergeResult dataclass."""

    def test_merge_result_success(self):
        """Test successful merge result."""
        result = MergeResult(success=True)
        assert result.success is True
        assert result.conflicts == []
        assert result.error is None

    def test_merge_result_with_conflicts(self):
        """Test merge result with conflicts."""
        result = MergeResult(
            success=False,
            conflicts=["file1.py", "file2.py"],
        )
        assert result.success is False
        assert result.conflicts == ["file1.py", "file2.py"]
        assert result.error is None

    def test_merge_result_with_error(self):
        """Test merge result with error."""
        result = MergeResult(
            success=False,
            error="Merge failed",
        )
        assert result.success is False
        assert result.conflicts == []
        assert result.error == "Merge failed"


class TestBranchInfo:
    """Tests for BranchInfo dataclass."""

    def test_branch_info_defaults(self):
        """Test BranchInfo default values."""
        info = BranchInfo(name="main")
        assert info.name == "main"
        assert info.is_current is False
        assert info.remote is None
        assert info.last_commit is None

    def test_branch_info_with_values(self):
        """Test BranchInfo with custom values."""
        info = BranchInfo(
            name="feature",
            is_current=True,
            remote="origin/feature",
            last_commit="abc1234",
        )
        assert info.name == "feature"
        assert info.is_current is True
        assert info.remote == "origin/feature"
        assert info.last_commit == "abc1234"


class TestGitOps:
    """Tests for GitOps class."""

    @pytest.fixture
    def git_ops(self, tmp_path):
        """Create a GitOps instance with temp path."""
        return GitOps(repo_path=tmp_path)

    @pytest.fixture
    def mock_run(self):
        """Mock subprocess.run."""
        with patch("forge_harness.iteration.git_ops.subprocess.run") as mock:
            yield mock

    def test_init_with_path(self, tmp_path):
        """Test GitOps initialization with path."""
        ops = GitOps(repo_path=tmp_path)
        assert ops.repo_path == tmp_path

    def test_init_without_path(self):
        """Test GitOps initialization without path."""
        ops = GitOps()
        assert ops.repo_path == Path.cwd()

    def test_run_command(self, git_ops, mock_run):
        """Test _run helper method."""
        mock_run.return_value = Mock(stdout="output", stderr="", returncode=0)
        result = git_ops._run("status")

        mock_run.assert_called_once_with(
            ["git", "status"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout == "output"

    def test_run_command_no_check(self, git_ops, mock_run):
        """Test _run with check=False."""
        mock_run.return_value = Mock(stdout="", stderr="", returncode=1)
        git_ops._run("status", check=False)

        mock_run.assert_called_once_with(
            ["git", "status"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_get_status_clean(self, git_ops, mock_run):
        """Test get_status with clean repo."""
        mock_run.side_effect = [
            Mock(stdout="main\n", stderr="", returncode=0),
            Mock(stdout="", stderr="", returncode=0),
            Mock(stdout="## main...origin/main\n", stderr="", returncode=0),
        ]

        status = git_ops.get_status()
        assert status.branch == "main"
        assert status.staged == []
        assert status.modified == []
        assert status.untracked == []
        assert status.conflicts == []
        assert status.branch_status == BranchStatus.UP_TO_DATE

    def test_get_status_with_changes(self, git_ops, mock_run):
        """Test get_status with various changes."""
        mock_run.side_effect = [
            Mock(stdout="feature\n", stderr="", returncode=0),
            Mock(
                stdout="M  staged.py\n M modified.py\n?? untracked.py\nUU conflict.py\n",
                stderr="",
                returncode=0,
            ),
            Mock(stdout="## feature...origin/feature [ahead 2]\n", stderr="", returncode=0),
        ]

        status = git_ops.get_status()
        assert status.branch == "feature"
        assert status.staged == ["staged.py"]
        assert status.modified == ["modified.py"]
        assert status.untracked == ["untracked.py"]
        assert status.conflicts == ["conflict.py"]
        assert status.branch_status == BranchStatus.AHEAD
        assert status.ahead_count == 2

    def test_get_status_error(self, git_ops, mock_run):
        """Test get_status with error."""
        mock_run.side_effect = Exception("Git error")

        status = git_ops.get_status()
        assert status.branch == "unknown"

    def test_get_branch_status_up_to_date(self, git_ops, mock_run):
        """Test _get_branch_status when up to date."""
        mock_run.return_value = Mock(stdout="## main...origin/main\n", returncode=0)

        status, ahead, behind = git_ops._get_branch_status()
        assert status == BranchStatus.UP_TO_DATE
        assert ahead == 0
        assert behind == 0

    def test_get_branch_status_ahead(self, git_ops, mock_run):
        """Test _get_branch_status when ahead."""
        mock_run.return_value = Mock(stdout="## main...origin/main [ahead 3]\n", returncode=0)

        status, ahead, behind = git_ops._get_branch_status()
        assert status == BranchStatus.AHEAD
        assert ahead == 3
        assert behind == 0

    def test_get_branch_status_behind(self, git_ops, mock_run):
        """Test _get_branch_status when behind."""
        mock_run.return_value = Mock(stdout="## main...origin/main [behind 2]\n", returncode=0)

        status, ahead, behind = git_ops._get_branch_status()
        assert status == BranchStatus.BEHIND
        assert ahead == 0
        assert behind == 2

    def test_get_branch_status_diverged(self, git_ops, mock_run):
        """Test _get_branch_status when diverged."""
        mock_run.return_value = Mock(
            stdout="## main...origin/main [ahead 1, behind 2]\n", returncode=0
        )

        status, ahead, behind = git_ops._get_branch_status()
        assert status == BranchStatus.DIVERGED
        assert ahead == 1
        assert behind == 2

    def test_get_branch_status_no_remote(self, git_ops, mock_run):
        """Test _get_branch_status with no remote."""
        mock_run.return_value = Mock(stdout="## main\n", returncode=0)

        status, ahead, behind = git_ops._get_branch_status()
        assert status == BranchStatus.NO_REMOTE
        assert ahead == 0
        assert behind == 0

    def test_create_branch(self, git_ops, mock_run):
        """Test create_branch."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.create_branch("feature")
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "checkout", "-b", "feature"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_create_branch_from_branch(self, git_ops, mock_run):
        """Test create_branch from another branch."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.create_branch("feature", from_branch="main")
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "checkout", "-b", "feature", "main"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_create_branch_failure(self, git_ops, mock_run):
        """Test create_branch failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git", stderr="error")

        result = git_ops.create_branch("feature")
        assert result is False

    def test_switch_branch(self, git_ops, mock_run):
        """Test switch_branch."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.switch_branch("main")
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "checkout", "main"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_switch_branch_failure(self, git_ops, mock_run):
        """Test switch_branch failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git", stderr="error")

        result = git_ops.switch_branch("main")
        assert result is False

    def test_delete_branch(self, git_ops, mock_run):
        """Test delete_branch."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.delete_branch("feature")
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "branch", "-d", "feature"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_delete_branch_force(self, git_ops, mock_run):
        """Test delete_branch with force."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.delete_branch("feature", force=True)
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "branch", "-D", "feature"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_delete_branch_failure(self, git_ops, mock_run):
        """Test delete_branch failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git", stderr="error")

        result = git_ops.delete_branch("feature")
        assert result is False

    def test_list_branches(self, git_ops, mock_run):
        """Test list_branches."""
        mock_run.return_value = Mock(
            stdout="* main abc123 Latest commit\n  feature def456 Feature commit\n",
            returncode=0,
        )

        branches = git_ops.list_branches()
        assert len(branches) == 2
        assert branches[0].name == "main"
        assert branches[0].is_current is True
        assert branches[0].last_commit == "abc123"
        assert branches[1].name == "feature"
        assert branches[1].is_current is False
        assert branches[1].last_commit == "def456"

    def test_list_branches_with_remote(self, git_ops, mock_run):
        """Test list_branches with remote."""
        mock_run.return_value = Mock(stdout="* main abc123\n", returncode=0)

        git_ops.list_branches(include_remote=True)
        mock_run.assert_called_once_with(
            ["git", "branch", "-v", "-a"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_list_branches_error(self, git_ops, mock_run):
        """Test list_branches with error."""
        mock_run.side_effect = Exception("Error")

        branches = git_ops.list_branches()
        assert branches == []

    def test_get_current_branch(self, git_ops, mock_run):
        """Test get_current_branch."""
        mock_run.return_value = Mock(stdout="feature\n", returncode=0)

        branch = git_ops.get_current_branch()
        assert branch == "feature"

    def test_has_conflicts_true(self, git_ops, mock_run):
        """Test has_conflicts when conflicts exist."""
        mock_run.side_effect = [
            Mock(stdout="main\n", returncode=0),
            Mock(stdout="UU conflict.py\n", returncode=0),
            Mock(stdout="## main\n", returncode=0),
        ]

        result = git_ops.has_conflicts()
        assert result is True

    def test_has_conflicts_false(self, git_ops, mock_run):
        """Test has_conflicts when no conflicts."""
        mock_run.side_effect = [
            Mock(stdout="main\n", returncode=0),
            Mock(stdout="M  file.py\n", returncode=0),
            Mock(stdout="## main\n", returncode=0),
        ]

        result = git_ops.has_conflicts()
        assert result is False

    def test_merge_success(self, git_ops, mock_run):
        """Test successful merge."""
        mock_run.return_value = Mock(stdout="", returncode=0)

        result = git_ops.merge("feature")
        assert result.success is True
        assert result.conflicts == []
        assert result.error is None
        mock_run.assert_called_once_with(
            ["git", "merge", "feature"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_merge_with_no_ff(self, git_ops, mock_run):
        """Test merge with no-ff."""
        mock_run.return_value = Mock(stdout="", returncode=0)

        git_ops.merge("feature", no_ff=True)
        mock_run.assert_called_once_with(
            ["git", "merge", "feature", "--no-ff"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_merge_with_conflicts(self, git_ops, mock_run):
        """Test merge with conflicts."""
        mock_run.side_effect = [
            Mock(stdout="", stderr="CONFLICT", returncode=1),
            Mock(stdout="main\n", returncode=0),
            Mock(stdout="UU conflict.py\n", returncode=0),
            Mock(stdout="## main\n", returncode=0),
        ]

        result = git_ops.merge("feature")
        assert result.success is False
        assert result.conflicts == ["conflict.py"]
        assert result.error is None

    def test_merge_with_error(self, git_ops, mock_run):
        """Test merge with error."""
        mock_run.return_value = Mock(stdout="", stderr="Error", returncode=1)

        with patch.object(git_ops, "get_status", return_value=GitStatus(branch="main")):
            result = git_ops.merge("feature")
            assert result.success is False
            assert result.error == "Error"

    def test_merge_exception(self, git_ops, mock_run):
        """Test merge with exception."""
        mock_run.side_effect = Exception("Test error")

        result = git_ops.merge("feature")
        assert result.success is False
        assert result.error == "Test error"

    def test_abort_merge(self, git_ops, mock_run):
        """Test abort_merge."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.abort_merge()
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "merge", "--abort"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_abort_merge_failure(self, git_ops, mock_run):
        """Test abort_merge failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        result = git_ops.abort_merge()
        assert result is False

    def test_stash(self, git_ops, mock_run):
        """Test stash without message."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.stash()
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "stash", "push"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_stash_with_message(self, git_ops, mock_run):
        """Test stash with message."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.stash(message="WIP")
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "stash", "push", "-m", "WIP"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_stash_failure(self, git_ops, mock_run):
        """Test stash failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        result = git_ops.stash()
        assert result is False

    def test_stash_pop(self, git_ops, mock_run):
        """Test stash_pop."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.stash_pop()
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "stash", "pop"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_stash_pop_failure(self, git_ops, mock_run):
        """Test stash_pop failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        result = git_ops.stash_pop()
        assert result is False

    def test_stash_list(self, git_ops, mock_run):
        """Test stash_list."""
        mock_run.return_value = Mock(
            stdout="stash@{0}: WIP on main\nstash@{1}: WIP on feature\n",
            returncode=0,
        )

        stashes = git_ops.stash_list()
        assert len(stashes) == 2
        assert stashes[0] == "stash@{0}: WIP on main"
        assert stashes[1] == "stash@{1}: WIP on feature"

    def test_stash_list_empty(self, git_ops, mock_run):
        """Test stash_list when empty."""
        mock_run.return_value = Mock(stdout="", returncode=0)

        stashes = git_ops.stash_list()
        assert stashes == []

    def test_stash_list_failure(self, git_ops, mock_run):
        """Test stash_list failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        stashes = git_ops.stash_list()
        assert stashes == []

    def test_fetch(self, git_ops, mock_run):
        """Test fetch."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.fetch()
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "fetch", "origin"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_fetch_custom_remote(self, git_ops, mock_run):
        """Test fetch with custom remote."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.fetch(remote="upstream")
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "fetch", "upstream"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_fetch_failure(self, git_ops, mock_run):
        """Test fetch failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        result = git_ops.fetch()
        assert result is False

    def test_pull(self, git_ops, mock_run):
        """Test pull."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.pull()
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "pull", "origin"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_pull_with_branch(self, git_ops, mock_run):
        """Test pull with branch."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.pull(branch="main")
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "pull", "origin", "main"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_pull_failure(self, git_ops, mock_run):
        """Test pull failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        result = git_ops.pull()
        assert result is False

    def test_push(self, git_ops, mock_run):
        """Test push."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.push()
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "push", "origin"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_push_with_branch(self, git_ops, mock_run):
        """Test push with branch."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.push(branch="feature")
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "push", "origin", "feature"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_push_set_upstream(self, git_ops, mock_run):
        """Test push with set_upstream."""
        mock_run.return_value = Mock(returncode=0)

        result = git_ops.push(branch="feature", set_upstream=True)
        assert result is True
        mock_run.assert_called_once_with(
            ["git", "push", "-u", "origin", "feature"],
            cwd=git_ops.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_push_failure(self, git_ops, mock_run):
        """Test push failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        result = git_ops.push()
        assert result is False


class TestFactoryFunction:
    """Tests for factory function."""

    def test_create_git_ops_with_path(self, tmp_path):
        """Test factory function with path."""
        ops = create_git_ops(tmp_path)
        assert isinstance(ops, GitOps)
        assert ops.repo_path == tmp_path

    def test_create_git_ops_without_path(self):
        """Test factory function without path."""
        ops = create_git_ops()
        assert isinstance(ops, GitOps)
        assert ops.repo_path == Path.cwd()
