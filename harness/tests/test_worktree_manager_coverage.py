"""Tests for forge_harness/worktree_manager.py"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from forge_harness.worktree_manager import Worktree, WorktreeManager


@pytest.fixture
def repo_root(tmp_path):
    """Create a temporary git repo root."""
    repo = tmp_path / "test-repo"
    repo.mkdir()
    return repo


@pytest.fixture
def worktree_manager(repo_root):
    """Create a WorktreeManager instance."""
    return WorktreeManager(repo_root)


class TestWorktreeDataclass:
    """Tests for Worktree dataclass."""

    def test_worktree_creation(self):
        """Test creating a Worktree instance."""
        wt = Worktree(
            path=Path("/path/to/worktree"),
            branch="main",
            head_hash="abc123",
        )
        assert wt.path == Path("/path/to/worktree")
        assert wt.branch == "main"
        assert wt.head_hash == "abc123"


class TestWorktreeManager:
    """Tests for WorktreeManager class."""

    def test_init(self, repo_root):
        """Test WorktreeManager initialization."""
        manager = WorktreeManager(repo_root)
        assert manager.repo_root == repo_root

    def test_list_worktrees_success(self, worktree_manager, repo_root):
        """Test listing worktrees successfully."""
        mock_output = """worktree /path/to/main
branch refs/heads/main
HEAD abc123

worktree /path/to/feature
branch refs/heads/feature-branch
HEAD def456
"""
        mock_result = Mock()
        mock_result.stdout = mock_output
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            worktrees = worktree_manager.list_worktrees()

            assert len(worktrees) == 2
            assert worktrees[0].path == Path("/path/to/main")
            assert worktrees[0].branch == "main"
            assert worktrees[0].head_hash == "abc123"
            assert worktrees[1].branch == "feature-branch"

    def test_list_worktrees_detached(self, worktree_manager):
        """Test listing worktrees with detached HEAD."""
        mock_output = """worktree /path/to/detached
HEAD abc123
"""
        mock_result = Mock()
        mock_result.stdout = mock_output

        with patch("subprocess.run", return_value=mock_result):
            worktrees = worktree_manager.list_worktrees()

            assert len(worktrees) == 1
            assert worktrees[0].branch == "detached"

    def test_list_worktrees_empty(self, worktree_manager):
        """Test listing worktrees when none exist."""
        mock_result = Mock()
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            worktrees = worktree_manager.list_worktrees()

            assert len(worktrees) == 0

    def test_list_worktrees_error(self, worktree_manager):
        """Test handling error when listing worktrees."""
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")):
            worktrees = worktree_manager.list_worktrees()

            assert worktrees == []

    def test_parse_worktree(self, worktree_manager):
        """Test parsing worktree data."""
        data = {
            "path": "/path/to/worktree",
            "branch": "main",
            "head": "abc123",
        }

        worktree = worktree_manager._parse_worktree(data)

        assert worktree.path == Path("/path/to/worktree")
        assert worktree.branch == "main"
        assert worktree.head_hash == "abc123"

    def test_parse_worktree_missing_data(self, worktree_manager):
        """Test parsing worktree with missing data."""
        data = {}

        worktree = worktree_manager._parse_worktree(data)

        assert worktree.path == Path("")
        assert worktree.branch == "detached"
        assert worktree.head_hash == ""

    def test_create_worktree_success(self, worktree_manager, repo_root, tmp_path):
        """Test creating a new worktree."""
        branch = "feature-branch"
        worktree_path = tmp_path / "new-worktree"

        mock_result = Mock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result_path = worktree_manager.create_worktree(branch, worktree_path)

            assert result_path == worktree_path
            # Verify git worktree add was called
            assert any("worktree" in str(call) for call in mock_run.call_args_list)

    def test_create_worktree_default_path(self, worktree_manager, repo_root):
        """Test creating worktree with default path."""
        branch = "feature-branch"

        mock_result = Mock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            with patch.object(Path, "exists", return_value=False):
                result_path = worktree_manager.create_worktree(branch)

                expected_name = f"{repo_root.name}-{branch}"
                assert result_path.name == expected_name

    def test_create_worktree_path_exists(self, worktree_manager, tmp_path):
        """Test creating worktree when path already exists."""
        branch = "feature-branch"
        worktree_path = tmp_path / "existing"
        worktree_path.mkdir()

        result_path = worktree_manager.create_worktree(branch, worktree_path)

        assert result_path == worktree_path

    def test_create_worktree_branch_exists(self, worktree_manager, tmp_path):
        """Test creating worktree for existing branch."""
        branch = "existing-branch"
        worktree_path = tmp_path / "new-worktree"

        # Mock branch exists check
        def run_side_effect(cmd, **kwargs):
            if "show-ref" in cmd:
                mock_result = Mock()
                mock_result.returncode = 0
                return mock_result
            mock_result = Mock()
            mock_result.returncode = 0
            return mock_result

        with patch("subprocess.run", side_effect=run_side_effect):
            with patch.object(Path, "exists", return_value=False):
                result_path = worktree_manager.create_worktree(branch, worktree_path)

                assert result_path == worktree_path

    def test_create_worktree_branch_with_slash(self, worktree_manager, tmp_path):
        """Test creating worktree for branch with slash in name."""
        branch = "feature/new-feature"

        mock_result = Mock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            with patch.object(Path, "exists", return_value=False):
                result_path = worktree_manager.create_worktree(branch)

                # Slash should be replaced with dash
                assert "feature-new-feature" in str(result_path)

    def test_create_worktree_error(self, worktree_manager, tmp_path):
        """Test error handling when creating worktree."""
        branch = "feature-branch"
        worktree_path = tmp_path / "new-worktree"

        mock_error = subprocess.CalledProcessError(1, "git")
        mock_error.stderr = "Error creating worktree"

        with patch("subprocess.run", side_effect=mock_error):
            with patch.object(Path, "exists", return_value=False):
                with pytest.raises(RuntimeError, match="Failed to create worktree"):
                    worktree_manager.create_worktree(branch, worktree_path)

    def test_remove_worktree_success(self, worktree_manager, tmp_path):
        """Test removing a worktree."""
        worktree_path = tmp_path / "worktree-to-remove"

        mock_result = Mock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            worktree_manager.remove_worktree(worktree_path)

            mock_run.assert_called_once()
            assert "worktree" in str(mock_run.call_args)
            assert "remove" in str(mock_run.call_args)

    def test_remove_worktree_force(self, worktree_manager, tmp_path):
        """Test force removing a worktree."""
        worktree_path = tmp_path / "worktree-to-remove"

        mock_result = Mock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            worktree_manager.remove_worktree(worktree_path, force=True)

            call_args_str = str(mock_run.call_args)
            assert "--force" in call_args_str

    def test_remove_worktree_error(self, worktree_manager, tmp_path):
        """Test error handling when removing worktree."""
        worktree_path = tmp_path / "worktree-to-remove"

        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")):
            with pytest.raises(subprocess.CalledProcessError):
                worktree_manager.remove_worktree(worktree_path)

    def test_prune(self, worktree_manager):
        """Test pruning stale worktree info."""
        mock_result = Mock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            worktree_manager.prune()

            mock_run.assert_called_once()
            assert "worktree" in str(mock_run.call_args)
            assert "prune" in str(mock_run.call_args)
