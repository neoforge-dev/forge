"""Tests for PR generator module."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from forge_harness.iteration.pr_generator import (
    PRContext,
    PRGenerator,
    PRResult,
    create_pr_generator,
)


class TestPRContext:
    """Tests for PRContext dataclass."""

    def test_default_values(self):
        """Test default values are set correctly."""
        context = PRContext(title="Test PR")
        assert context.title == "Test PR"
        assert context.features == []
        assert context.tests_added == 0
        assert context.lines_added == 0
        assert context.iterations_completed == 0
        assert context.base_branch == "main"
        assert context.labels == []
        assert context.reviewers == []
        assert context.draft is False

    def test_custom_values(self):
        """Test custom values are set correctly."""
        context = PRContext(
            title="Custom PR",
            features=["Feature 1", "Feature 2"],
            tests_added=5,
            lines_added=100,
            iterations_completed=3,
            base_branch="develop",
            labels=["bug", "enhancement"],
            reviewers=["reviewer1", "reviewer2"],
            draft=True,
        )
        assert context.title == "Custom PR"
        assert len(context.features) == 2
        assert context.tests_added == 5
        assert context.lines_added == 100
        assert context.iterations_completed == 3
        assert context.base_branch == "develop"
        assert len(context.labels) == 2
        assert len(context.reviewers) == 2
        assert context.draft is True


class TestPRResult:
    """Tests for PRResult dataclass."""

    def test_success_result(self):
        """Test successful PR result."""
        result = PRResult(
            success=True, pr_url="https://github.com/user/repo/pull/123", pr_number=123
        )
        assert result.success is True
        assert result.pr_url == "https://github.com/user/repo/pull/123"
        assert result.pr_number == 123
        assert result.error is None

    def test_error_result(self):
        """Test error PR result."""
        result = PRResult(success=False, error="Failed to create PR")
        assert result.success is False
        assert result.error == "Failed to create PR"
        assert result.pr_url is None
        assert result.pr_number is None


class TestPRGenerator:
    """Tests for PRGenerator class."""

    @pytest.fixture
    def generator(self, tmp_path):
        """Create a PR generator with temp repo path."""
        return PRGenerator(repo_path=tmp_path)

    def test_init_with_path(self, tmp_path):
        """Test initialization with custom path."""
        gen = PRGenerator(repo_path=tmp_path)
        assert gen.repo_path == tmp_path

    def test_init_without_path(self):
        """Test initialization without path uses cwd."""
        gen = PRGenerator()
        assert gen.repo_path == Path.cwd()

    @patch("subprocess.run")
    def test_get_current_branch(self, mock_run, generator):
        """Test getting current branch name."""
        mock_run.return_value = Mock(stdout="feature/test-branch\n", returncode=0)
        branch = generator.get_current_branch()
        assert branch == "feature/test-branch"
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_get_commits_since_base(self, mock_run, generator):
        """Test getting commits since base branch."""
        mock_run.return_value = Mock(
            stdout="abc123 First commit\ndef456 Second commit\n", returncode=0
        )
        commits = generator.get_commits_since_base("main")
        assert len(commits) == 2
        assert "abc123 First commit" in commits
        assert "def456 Second commit" in commits
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_get_commits_since_base_empty(self, mock_run, generator):
        """Test getting commits when there are none."""
        mock_run.return_value = Mock(stdout="", returncode=0)
        commits = generator.get_commits_since_base("main")
        assert commits == []  # Empty output should return empty list

    def test_generate_body_minimal(self, generator):
        """Test generating PR body with minimal context."""
        context = PRContext(title="Test PR")
        body = generator.generate_body(context)
        assert "## Summary" in body
        assert "Code improvements and updates" in body
        assert "## Test Plan" in body
        assert "Generated with [Claude Code]" in body

    def test_generate_body_with_features(self, generator):
        """Test generating PR body with features."""
        context = PRContext(title="Test PR", features=["Add login", "Add logout", "Add profile"])
        body = generator.generate_body(context)
        assert "## Summary" in body
        assert "- Add login" in body
        assert "- Add logout" in body
        assert "- Add profile" in body

    def test_generate_body_with_many_features(self, generator):
        """Test generating PR body with many features (truncation)."""
        features = [f"Feature {i}" for i in range(10)]
        context = PRContext(title="Test PR", features=features)
        body = generator.generate_body(context)
        assert "## Summary" in body
        assert "... and 5 more features" in body

    def test_generate_body_with_metrics(self, generator):
        """Test generating PR body with metrics."""
        context = PRContext(
            title="Test PR", iterations_completed=5, tests_added=10, lines_added=1500
        )
        body = generator.generate_body(context)
        assert "## Metrics" in body
        assert "**Iterations completed:** 5" in body
        assert "**Tests added:** 10" in body
        assert "**Lines added:** 1,500" in body

    def test_generate_body_no_metrics_section_when_empty(self, generator):
        """Test no metrics section when all metrics are zero."""
        context = PRContext(title="Test PR")
        body = generator.generate_body(context)
        assert "## Metrics" not in body

    def test_generate_body_test_plan(self, generator):
        """Test PR body includes test plan checklist."""
        context = PRContext(title="Test PR")
        body = generator.generate_body(context)
        assert "## Test Plan" in body
        assert "- [ ] All tests passing" in body
        assert "- [ ] No type errors" in body
        assert "- [ ] No security vulnerabilities" in body
        assert "- [ ] Documentation updated if needed" in body

    @patch("subprocess.run")
    def test_create_pr_success(self, mock_run, generator):
        """Test successful PR creation."""
        mock_run.return_value = Mock(
            stdout="https://github.com/user/repo/pull/123", stderr="", returncode=0
        )
        context = PRContext(title="Test PR")
        result = generator.create_pr(context)
        assert result.success is True
        assert result.pr_url == "https://github.com/user/repo/pull/123"
        assert result.pr_number == 123
        assert result.error is None

    @patch("subprocess.run")
    def test_create_pr_with_draft(self, mock_run, generator):
        """Test creating draft PR."""
        mock_run.return_value = Mock(
            stdout="https://github.com/user/repo/pull/123", stderr="", returncode=0
        )
        context = PRContext(title="Test PR", draft=True)
        result = generator.create_pr(context)
        assert result.success is True
        # Check that --draft was in the command
        call_args = mock_run.call_args[0][0]
        assert "--draft" in call_args

    @patch("subprocess.run")
    def test_create_pr_with_labels(self, mock_run, generator):
        """Test creating PR with labels."""
        mock_run.return_value = Mock(
            stdout="https://github.com/user/repo/pull/123", stderr="", returncode=0
        )
        context = PRContext(title="Test PR", labels=["bug", "enhancement"])
        result = generator.create_pr(context)
        assert result.success is True
        # Check that labels were in the command
        call_args = mock_run.call_args[0][0]
        assert "--label" in call_args
        assert "bug" in call_args
        assert "enhancement" in call_args

    @patch("subprocess.run")
    def test_create_pr_with_reviewers(self, mock_run, generator):
        """Test creating PR with reviewers."""
        mock_run.return_value = Mock(
            stdout="https://github.com/user/repo/pull/123", stderr="", returncode=0
        )
        context = PRContext(title="Test PR", reviewers=["reviewer1", "reviewer2"])
        result = generator.create_pr(context)
        assert result.success is True
        # Check that reviewers were in the command
        call_args = mock_run.call_args[0][0]
        assert "--reviewer" in call_args
        assert "reviewer1" in call_args
        assert "reviewer2" in call_args

    @patch("subprocess.run")
    def test_create_pr_with_custom_base(self, mock_run, generator):
        """Test creating PR with custom base branch."""
        mock_run.return_value = Mock(
            stdout="https://github.com/user/repo/pull/123", stderr="", returncode=0
        )
        context = PRContext(title="Test PR", base_branch="develop")
        result = generator.create_pr(context)
        assert result.success is True
        # Check that base branch was in the command
        call_args = mock_run.call_args[0][0]
        assert "--base" in call_args
        assert "develop" in call_args

    @patch("subprocess.run")
    def test_create_pr_failure(self, mock_run, generator):
        """Test failed PR creation."""
        mock_run.return_value = Mock(stdout="", stderr="Error: failed to create PR", returncode=1)
        context = PRContext(title="Test PR")
        result = generator.create_pr(context)
        assert result.success is False
        assert result.error == "Error: failed to create PR"
        assert result.pr_url is None
        assert result.pr_number is None

    @patch("subprocess.run", side_effect=FileNotFoundError())
    def test_create_pr_gh_not_found(self, mock_run, generator):
        """Test PR creation when gh CLI is not installed."""
        context = PRContext(title="Test PR")
        result = generator.create_pr(context)
        assert result.success is False
        assert "gh CLI not found" in result.error

    @patch("subprocess.run", side_effect=Exception("Unexpected error"))
    def test_create_pr_unexpected_error(self, mock_run, generator):
        """Test PR creation with unexpected error."""
        context = PRContext(title="Test PR")
        result = generator.create_pr(context)
        assert result.success is False
        assert "Unexpected error" in result.error

    @patch("subprocess.run")
    def test_check_gh_auth_success(self, mock_run, generator):
        """Test checking gh auth when authenticated."""
        mock_run.return_value = Mock(returncode=0)
        assert generator.check_gh_auth() is True

    @patch("subprocess.run")
    def test_check_gh_auth_failure(self, mock_run, generator):
        """Test checking gh auth when not authenticated."""
        mock_run.return_value = Mock(returncode=1)
        assert generator.check_gh_auth() is False

    @patch("subprocess.run", side_effect=FileNotFoundError())
    def test_check_gh_auth_not_installed(self, mock_run, generator):
        """Test checking gh auth when gh CLI is not installed."""
        assert generator.check_gh_auth() is False


class TestCreatePRGenerator:
    """Tests for create_pr_generator factory function."""

    def test_factory_without_path(self):
        """Test factory creates generator with default path."""
        gen = create_pr_generator()
        assert isinstance(gen, PRGenerator)
        assert gen.repo_path == Path.cwd()

    def test_factory_with_path(self, tmp_path):
        """Test factory creates generator with custom path."""
        gen = create_pr_generator(repo_path=tmp_path)
        assert isinstance(gen, PRGenerator)
        assert gen.repo_path == tmp_path
