"""Tests for branch manager functionality."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.branch_manager import BranchManager, slugify


class TestSlugify:
    """Tests for slugify function."""

    def test_basic_text(self):
        """Converts basic text to slug."""
        assert slugify("Hello World") == "hello-world"

    def test_special_characters(self):
        """Removes special characters."""
        assert slugify("Fix bug #123!") == "fix-bug-123"

    def test_max_length(self):
        """Respects max length."""
        long_text = "This is a very long issue title that should be truncated"
        result = slugify(long_text, max_length=20)
        assert len(result) <= 20
        assert result == "this-is-a-very-long"

    def test_trailing_dashes(self):
        """Strips trailing dashes."""
        assert slugify("hello world---", max_length=12) == "hello-world"

    def test_leading_dashes(self):
        """Strips leading dashes."""
        assert slugify("---hello") == "hello"

    def test_numbers(self):
        """Preserves numbers."""
        assert slugify("Issue 42") == "issue-42"

    def test_empty_string(self):
        """Handles empty string."""
        assert slugify("") == ""

    def test_only_special_chars(self):
        """Handles string with only special characters."""
        assert slugify("!!!@@@###") == ""


class TestBranchManager:
    """Tests for BranchManager class."""

    @pytest.fixture
    def tmp_git_repo(self, tmp_path):
        """Create a temporary git repository."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path,
            capture_output=True,
        )
        # Create initial commit on main
        (tmp_path / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=tmp_path,
            capture_output=True,
        )
        return tmp_path

    @pytest.fixture
    def branch_manager(self, tmp_git_repo):
        """Create BranchManager instance."""
        return BranchManager(tmp_git_repo)

    def test_initialization(self, branch_manager, tmp_git_repo):
        """BranchManager initializes with project directory."""
        assert branch_manager.project_dir == tmp_git_repo

    def test_get_current_branch(self, branch_manager):
        """Gets current branch name."""
        # Initial branch might be main or master
        branch = branch_manager.get_current_branch()
        assert branch in ["main", "master"]

    def test_create_feature_branch_local(self, branch_manager):
        """Creates feature branch from current HEAD (local mode)."""
        branch_name = branch_manager.create_feature_branch(
            issue_number=42,
            issue_title="Fix login bug",
            from_remote=False,
        )

        assert branch_name == "feat/42-fix-login-bug"
        assert branch_manager.get_current_branch() == branch_name

    def test_create_feature_branch_naming(self, branch_manager):
        """Feature branch follows naming convention."""
        branch_name = branch_manager.create_feature_branch(
            issue_number=123,
            issue_title="Add user authentication with OAuth2",
            from_remote=False,
        )

        assert branch_name.startswith("feat/123-")
        assert len(branch_name) <= 50  # Reasonable max length

    def test_create_feature_branch_long_title(self, branch_manager):
        """Handles long issue titles."""
        branch_name = branch_manager.create_feature_branch(
            issue_number=1,
            issue_title="This is an extremely long issue title that should be truncated to a reasonable length",
            from_remote=False,
        )

        assert "feat/1-" in branch_name
        assert len(branch_name) <= 50

    def test_is_on_protected_branch_main(self, branch_manager):
        """Detects when on main branch."""
        assert branch_manager.is_on_protected_branch() is True

    def test_is_on_protected_branch_feature(self, branch_manager):
        """Detects when on feature branch."""
        branch_manager.create_feature_branch(1, "Test", from_remote=False)
        assert branch_manager.is_on_protected_branch() is False

    def test_ensure_not_on_main_on_feature_branch(self, branch_manager):
        """ensure_not_on_main succeeds on feature branch."""
        branch_manager.create_feature_branch(1, "Test", from_remote=False)
        # Should not raise
        branch_manager.ensure_not_on_main()

    def test_ensure_not_on_main_on_main_raises(self, branch_manager):
        """ensure_not_on_main raises on main."""
        with pytest.raises(RuntimeError, match="Cannot work on protected branch"):
            branch_manager.ensure_not_on_main()


class TestBranchManagerWithRemote:
    """Tests for BranchManager with remote operations (mocked)."""

    @pytest.fixture
    def mock_branch_manager(self, tmp_path):
        """Create BranchManager with mocked subprocess."""
        return BranchManager(tmp_path)

    def test_push_branch(self, mock_branch_manager):
        """Push feature branch to remote."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            result = mock_branch_manager.push_branch("feat/42-fix-bug")

            assert result is True
            mock_run.assert_called()
            call_args = mock_run.call_args
            assert "push" in call_args[0][0]
            assert "feat/42-fix-bug" in call_args[0][0]

    def test_push_branch_failure(self, mock_branch_manager):
        """Push returns False on failure."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)

            result = mock_branch_manager.push_branch("feat/42-fix-bug")

            assert result is False

    def test_create_feature_branch_from_remote(self, mock_branch_manager):
        """Create feature branch from remote origin/main."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")

            mock_branch_manager.create_feature_branch(
                issue_number=42,
                issue_title="Test",
                from_remote=True,
            )

            # Should have called git fetch and git checkout with origin/main
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("fetch" in c for c in calls)
            assert any("checkout" in c for c in calls)
