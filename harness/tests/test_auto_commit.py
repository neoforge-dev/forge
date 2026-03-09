"""Tests for auto-commit module."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from forge_harness.iteration.auto_commit import (
    AutoCommitter,
    CommitContext,
    CommitResult,
    create_auto_committer,
)


@pytest.fixture
def committer():
    """Create an AutoCommitter instance."""
    return AutoCommitter(repo_path=Path("/tmp/test-repo"))


@pytest.fixture
def basic_context():
    """Create a basic CommitContext."""
    return CommitContext(
        features=["auto-commit", "conventional commits"],
        tests_added=5,
        files_changed=["auto_commit.py", "test_auto_commit.py"],
        iteration_number=3,
        scope="harness",
    )


class TestCommitContext:
    """Tests for CommitContext dataclass."""

    def test_default_values(self):
        """Test that default values are set correctly."""
        ctx = CommitContext()
        assert ctx.features == []
        assert ctx.tests_added == 0
        assert ctx.lines_added == 0
        assert ctx.files_changed == []
        assert ctx.iteration_number is None
        assert ctx.scope == "harness"
        assert ctx.breaking_change is False

    def test_custom_values(self):
        """Test that custom values are set correctly."""
        ctx = CommitContext(
            features=["feature1", "feature2"],
            tests_added=10,
            scope="backend",
            breaking_change=True,
        )
        assert ctx.features == ["feature1", "feature2"]
        assert ctx.tests_added == 10
        assert ctx.scope == "backend"
        assert ctx.breaking_change is True


class TestCommitResult:
    """Tests for CommitResult dataclass."""

    def test_success_result(self):
        """Test successful commit result."""
        result = CommitResult(
            success=True,
            commit_hash="abc12345",
            message="feat(harness): add feature",
        )
        assert result.success is True
        assert result.commit_hash == "abc12345"
        assert result.error is None

    def test_error_result(self):
        """Test error commit result."""
        result = CommitResult(
            success=False,
            message="feat(harness): add feature",
            error="nothing to commit",
        )
        assert result.success is False
        assert result.commit_hash is None
        assert result.error == "nothing to commit"


class TestAutoCommitter:
    """Tests for AutoCommitter class."""

    def test_init_default_path(self):
        """Test initialization with default path."""
        committer = AutoCommitter()
        assert committer.repo_path == Path.cwd()

    def test_init_custom_path(self):
        """Test initialization with custom path."""
        custom_path = Path("/custom/path")
        committer = AutoCommitter(repo_path=custom_path)
        assert committer.repo_path == custom_path

    def test_commit_types_defined(self):
        """Test that commit types are properly defined."""
        assert "feat" in AutoCommitter.COMMIT_TYPES
        assert "fix" in AutoCommitter.COMMIT_TYPES
        assert "test" in AutoCommitter.COMMIT_TYPES
        assert "docs" in AutoCommitter.COMMIT_TYPES

    @patch("subprocess.run")
    def test_get_staged_changes(self, mock_run, committer):
        """Test getting staged changes from git."""
        mock_run.return_value = Mock(
            stdout=" auto_commit.py           | 100 ++++++++\n test_auto_commit.py    |  50 +++++\n 2 files changed, 150 insertions(+)\n",
            returncode=0,
        )

        files, additions, deletions = committer.get_staged_changes()

        assert "auto_commit.py" in files
        assert "test_auto_commit.py" in files
        assert additions == 150
        assert deletions == 0
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_get_staged_changes_with_deletions(self, mock_run, committer):
        """Test getting staged changes with deletions."""
        mock_run.return_value = Mock(
            stdout=" old_file.py | 50 ----------\n 1 file changed, 50 deletions(-)\n", returncode=0
        )

        files, additions, deletions = committer.get_staged_changes()

        assert "old_file.py" in files
        assert additions == 0
        assert deletions == 50

    @patch("subprocess.run")
    def test_get_staged_changes_no_changes(self, mock_run, committer):
        """Test getting staged changes when nothing is staged."""
        mock_run.return_value = Mock(stdout="", returncode=0)

        files, additions, deletions = committer.get_staged_changes()

        assert files == []
        assert additions == 0
        assert deletions == 0

    def test_detect_commit_type_feature(self, committer):
        """Test detecting feature commit type."""
        context = CommitContext(features=["new feature"])
        commit_type = committer.detect_commit_type(["file.py"], context)
        assert commit_type == "feat"

    def test_detect_commit_type_test(self, committer):
        """Test detecting test commit type."""
        context = CommitContext()
        files = ["test_file.py", "test_another.py"]
        commit_type = committer.detect_commit_type(files, context)
        assert commit_type == "test"

    def test_detect_commit_type_docs(self, committer):
        """Test detecting docs commit type."""
        context = CommitContext()
        files = ["README.md", "CONTRIBUTING.md"]
        commit_type = committer.detect_commit_type(files, context)
        assert commit_type == "docs"

    def test_detect_commit_type_chore(self, committer):
        """Test detecting chore commit type."""
        context = CommitContext()
        files = ["config.yaml", "setup.py"]
        commit_type = committer.detect_commit_type(files, context)
        assert commit_type == "chore"

    @patch.object(AutoCommitter, "get_staged_changes")
    def test_generate_message_with_features(self, mock_staged, committer, basic_context):
        """Test generating commit message with features."""
        mock_staged.return_value = (["auto_commit.py"], 100, 0)

        message = committer.generate_message(basic_context)

        assert "feat(harness): add auto-commit, conventional commits" in message
        assert "Iteration 3" in message
        assert "Features:" in message
        assert "- auto-commit" in message
        assert "- conventional commits" in message
        assert "Tests: 5 added" in message
        assert "Co-Authored-By: Claude Opus 4.5" in message

    @patch.object(AutoCommitter, "get_staged_changes")
    def test_generate_message_many_features(self, mock_staged, committer):
        """Test generating commit message with many features."""
        mock_staged.return_value = (["file.py"], 100, 0)
        context = CommitContext(
            features=["feat1", "feat2", "feat3", "feat4"],
            scope="harness",
        )

        message = committer.generate_message(context)

        assert "feat(harness): add feat1, feat2 and 2 more" in message

    @patch.object(AutoCommitter, "get_staged_changes")
    def test_generate_message_breaking_change(self, mock_staged, committer):
        """Test generating commit message with breaking change."""
        mock_staged.return_value = (["api.py"], 100, 0)
        context = CommitContext(
            features=["new API"],
            breaking_change=True,
            scope="api",
        )

        message = committer.generate_message(context)

        assert "BREAKING CHANGE" in message

    @patch.object(AutoCommitter, "get_staged_changes")
    def test_generate_message_no_features(self, mock_staged, committer):
        """Test generating commit message without features."""
        mock_staged.return_value = (["file1.py", "file2.py"], 50, 10)
        context = CommitContext(scope="harness")

        message = committer.generate_message(context)

        assert "chore(harness): update 2 file(s)" in message

    @patch("subprocess.run")
    def test_commit_success(self, mock_run, committer, basic_context):
        """Test successful commit."""
        mock_run.side_effect = [
            Mock(returncode=0, stdout="", stderr=""),  # git commit
            Mock(returncode=0, stdout="abc12345\n", stderr=""),  # git rev-parse
        ]

        with patch.object(committer, "generate_message", return_value="test message"):
            result = committer.commit(basic_context)

        assert result.success is True
        assert result.commit_hash == "abc12345"
        assert result.message == "test message"
        assert result.error is None

    @patch("subprocess.run")
    def test_commit_failure(self, mock_run, committer, basic_context):
        """Test commit failure."""
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="nothing to commit")

        with patch.object(committer, "generate_message", return_value="test message"):
            result = committer.commit(basic_context)

        assert result.success is False
        assert result.commit_hash is None
        assert result.error == "nothing to commit"

    @patch("subprocess.run")
    def test_commit_exception(self, mock_run, committer, basic_context):
        """Test commit with exception."""
        mock_run.side_effect = Exception("git not found")

        with patch.object(committer, "generate_message", return_value="test message"):
            result = committer.commit(basic_context)

        assert result.success is False
        assert result.error == "git not found"

    @patch("subprocess.run")
    def test_stage_files_success(self, mock_run, committer):
        """Test staging files successfully."""
        mock_run.return_value = Mock(returncode=0)
        paths = [Path("file1.py"), Path("file2.py")]

        success = committer.stage_files(paths)

        assert success is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "git"
        assert args[1] == "add"
        assert "file1.py" in args[2]
        assert "file2.py" in args[3]

    @patch("subprocess.run")
    def test_stage_files_failure(self, mock_run, committer):
        """Test staging files failure."""
        mock_run.return_value = Mock(returncode=1)
        paths = [Path("nonexistent.py")]

        success = committer.stage_files(paths)

        assert success is False

    @patch("subprocess.run")
    def test_stage_files_exception(self, mock_run, committer):
        """Test staging files with exception."""
        mock_run.side_effect = Exception("git error")
        paths = [Path("file.py")]

        success = committer.stage_files(paths)

        assert success is False


class TestFactory:
    """Tests for factory function."""

    def test_create_auto_committer_default(self):
        """Test factory with default path."""
        committer = create_auto_committer()
        assert isinstance(committer, AutoCommitter)
        assert committer.repo_path == Path.cwd()

    def test_create_auto_committer_custom_path(self):
        """Test factory with custom path."""
        custom_path = Path("/custom/path")
        committer = create_auto_committer(repo_path=custom_path)
        assert isinstance(committer, AutoCommitter)
        assert committer.repo_path == custom_path


class TestIntegration:
    """Integration tests for complete workflows."""

    @patch("subprocess.run")
    def test_full_commit_workflow(self, mock_run):
        """Test complete commit workflow from staging to commit."""
        committer = AutoCommitter(repo_path=Path("/tmp/test"))

        # Set up mock responses for the full workflow:
        # 1. git add (staging)
        # 2. git diff --stat (get_staged_changes in test)
        # 3. git diff --stat (get_staged_changes inside generate_message)
        # 4. git commit
        # 5. git rev-parse
        diff_output = Mock(
            stdout=" file.py | 100 +++++\n 1 file changed, 100 insertions(+)\n", returncode=0
        )
        mock_run.side_effect = [
            Mock(returncode=0),  # git add
            diff_output,  # git diff --stat (test)
            diff_output,  # git diff --stat (generate_message)
            Mock(returncode=0, stdout="", stderr=""),  # git commit
            Mock(returncode=0, stdout="abc12345\n", stderr=""),  # git rev-parse
        ]

        # Stage files
        stage_success = committer.stage_files([Path("file.py")])
        assert stage_success is True

        # Get staged changes
        files, additions, _ = committer.get_staged_changes()
        assert "file.py" in files

        # Commit
        context = CommitContext(
            features=["test feature"],
            files_changed=["file.py"],
            scope="test",
        )
        result = committer.commit(context)

        assert result.success is True
        assert result.commit_hash == "abc12345"
        assert result.commit_hash == "abc12345"
        assert "feat(test):" in result.message
