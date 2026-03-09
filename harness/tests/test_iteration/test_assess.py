"""Tests for forge_harness.iteration.assess module."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from forge_harness.iteration.assess import (
    AgentStatus,
    AgentType,
    AssessmentReport,
    GitStatus,
    Issue,
    IssueType,
    ProjectStatus,
    TestResults,
    _classify_agent_type,
    assess_status,
    collect_test_results,
    identify_issues,
    query_tmux_sessions,
)


class TestAgentType:
    """Test AgentType enum."""

    def test_agent_types(self):
        """Test all agent types are defined."""
        assert AgentType.BACKEND == "backend-engineer"
        assert AgentType.FRONTEND == "frontend-builder"
        assert AgentType.DEBUG == "debug-detective"
        assert AgentType.QA == "qa-tester"
        assert AgentType.CONTENT == "content-writer"
        assert AgentType.EXTERNAL == "external"
        assert AgentType.UNKNOWN == "unknown"


class TestAgentStatus:
    """Test AgentStatus dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        status = AgentStatus(
            session_name="test-session",
            agent_type=AgentType.BACKEND,
            is_active=True,
            working_directory=Path("/tmp"),
            last_activity=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            current_task="test task",
            metadata={"key": "value"},
        )

        data = status.to_dict()
        assert data["session_name"] == "test-session"
        assert data["agent_type"] == "backend-engineer"
        assert data["is_active"] is True
        assert data["working_directory"] == "/tmp"
        assert data["last_activity"] == "2024-01-01T12:00:00+00:00"
        assert data["current_task"] == "test task"
        assert data["metadata"] == {"key": "value"}


class TestProjectStatus:
    """Test ProjectStatus dataclass."""

    def test_is_clean(self):
        """Test is_clean property."""
        clean = ProjectStatus(domain="test", project="proj", path=Path("/tmp"), branch="main")
        assert clean.is_clean is True

        dirty = ProjectStatus(
            domain="test",
            project="proj",
            path=Path("/tmp"),
            branch="main",
            modified=["file.py"],
        )
        assert dirty.is_clean is False

    def test_has_changes(self):
        """Test has_changes property."""
        no_changes = ProjectStatus(domain="test", project="proj", path=Path("/tmp"), branch="main")
        assert no_changes.has_changes is False

        with_changes = ProjectStatus(
            domain="test",
            project="proj",
            path=Path("/tmp"),
            branch="main",
            untracked=["new.py"],
        )
        assert with_changes.has_changes is True


class TestGitStatus:
    """Test GitStatus dataclass."""

    def test_has_uncommitted_changes(self):
        """Test has_uncommitted_changes property."""
        no_changes = GitStatus()
        assert no_changes.has_uncommitted_changes is False

        with_staged = GitStatus(total_staged=1)
        assert with_staged.has_uncommitted_changes is True

        with_modified = GitStatus(total_modified=1)
        assert with_modified.has_uncommitted_changes is True


class TestTestResults:
    """Test TestResults dataclass."""

    def test_success_rate(self):
        """Test success_rate calculation."""
        # No tests
        no_tests = TestResults()
        assert no_tests.success_rate == 1.0

        # All passed
        all_passed = TestResults(total_tests=10, passed_count=10)
        assert all_passed.success_rate == 1.0

        # Some failed
        some_failed = TestResults(total_tests=10, passed_count=8)
        assert some_failed.success_rate == 0.8


class TestAssessmentReport:
    """Test AssessmentReport dataclass."""

    def test_get_summary(self):
        """Test get_summary method."""
        report = AssessmentReport(
            agents=[
                AgentStatus("a1", AgentType.BACKEND, True),
                AgentStatus("a2", AgentType.FRONTEND, False),
            ],
            git_status=GitStatus(
                projects=[
                    ProjectStatus("d1", "p1", Path("/tmp"), "main", modified=["f.py"]),
                    ProjectStatus("d2", "p2", Path("/tmp"), "main"),
                ]
            ),
            test_results=TestResults(total_tests=10, passed_count=9, failed_count=1),
            issues=[
                Issue(IssueType.FAILED_TEST, "critical", "test failed"),
                Issue(IssueType.LINT_ERROR, "medium", "lint error"),
            ],
        )

        summary = report.get_summary()
        assert summary["active_agents"] == 1
        assert summary["total_agents"] == 2
        assert summary["projects_with_changes"] == 1
        assert summary["total_projects"] == 2
        assert summary["failed_tests"] == 1
        assert summary["total_tests"] == 10
        assert summary["critical_issues"] == 1
        assert summary["total_issues"] == 2

    def test_to_json(self):
        """Test JSON serialization."""
        report = AssessmentReport()
        json_str = report.to_json()
        data = json.loads(json_str)

        assert "agents" in data
        assert "git_status" in data
        assert "test_results" in data
        assert "issues" in data
        assert "assessed_at" in data
        assert "duration_seconds" in data
        assert "summary" in data


class TestClassifyAgentType:
    """Test _classify_agent_type function."""

    def test_backend_classification(self):
        """Test backend agent classification."""
        assert _classify_agent_type("backend-task") == AgentType.BACKEND
        assert _classify_agent_type("api-service") == AgentType.BACKEND

    def test_frontend_classification(self):
        """Test frontend agent classification."""
        assert _classify_agent_type("frontend-build") == AgentType.FRONTEND
        assert _classify_agent_type("ui-components") == AgentType.FRONTEND
        assert _classify_agent_type("web-app") == AgentType.FRONTEND

    def test_debug_classification(self):
        """Test debug agent classification."""
        assert _classify_agent_type("debug-issue") == AgentType.DEBUG
        assert _classify_agent_type("fix-bug") == AgentType.DEBUG

    def test_qa_classification(self):
        """Test QA agent classification."""
        assert _classify_agent_type("qa-tests") == AgentType.QA
        assert _classify_agent_type("test-runner") == AgentType.QA

    def test_content_classification(self):
        """Test content agent classification."""
        assert _classify_agent_type("content-gen") == AgentType.CONTENT
        assert _classify_agent_type("marketing-copy") == AgentType.CONTENT

    def test_external_classification(self):
        """Test external agent classification."""
        assert _classify_agent_type("gemini-task") == AgentType.EXTERNAL
        assert _classify_agent_type("codex-gen") == AgentType.EXTERNAL
        assert _classify_agent_type("opencode-api") == AgentType.EXTERNAL
        assert _classify_agent_type("cursor-session") == AgentType.EXTERNAL
        assert _classify_agent_type("amp-refactor") == AgentType.EXTERNAL

    def test_unknown_classification(self):
        """Test unknown agent classification."""
        assert _classify_agent_type("random-session") == AgentType.UNKNOWN


class TestQueryTmuxSessions:
    """Test query_tmux_sessions function."""

    @patch("subprocess.run")
    def test_query_success(self, mock_run):
        """Test successful tmux query."""
        # Mock tmux list-sessions
        list_result = Mock()
        list_result.returncode = 0
        list_result.stdout = "session1:1704110400:1704110500\nsession2:1704110400:1704110600\n"

        # Mock tmux display-message (for working directory)
        cwd_result = Mock()
        cwd_result.returncode = 0
        cwd_result.stdout = "/tmp/work\n"

        mock_run.side_effect = [list_result, cwd_result, cwd_result]

        agents = query_tmux_sessions()

        assert len(agents) == 2
        assert agents[0].session_name == "session1"
        assert agents[1].session_name == "session2"

    @patch("subprocess.run")
    def test_query_no_sessions(self, mock_run):
        """Test query when no tmux sessions exist."""
        mock_run.return_value = Mock(returncode=1, stdout="")

        agents = query_tmux_sessions()
        assert len(agents) == 0

    @patch("subprocess.run")
    def test_query_timeout(self, mock_run):
        """Test query timeout handling."""
        mock_run.side_effect = subprocess.TimeoutExpired("tmux", 5)

        agents = query_tmux_sessions()
        assert len(agents) == 0

    @patch("subprocess.run")
    def test_query_no_tmux(self, mock_run):
        """Test handling when tmux is not installed."""
        mock_run.side_effect = FileNotFoundError()

        agents = query_tmux_sessions()
        assert len(agents) == 0


class TestCollectTestResults:
    """Test collect_test_results function."""

    def test_collect_with_cache(self, tmp_path):
        """Test collecting test results from pytest cache."""
        # Create pytest cache structure
        cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
        cache_dir.mkdir(parents=True)

        # Write lastfailed
        lastfailed = cache_dir / "lastfailed"
        lastfailed.write_text(
            json.dumps(
                {
                    "tests/test_foo.py::test_bar": True,
                }
            )
        )

        # Write nodeids
        nodeids = cache_dir / "nodeids"
        nodeids.write_text(
            json.dumps(
                [
                    "tests/test_foo.py::test_bar",
                    "tests/test_foo.py::test_baz",
                ]
            )
        )

        with patch("pathlib.Path.cwd", return_value=tmp_path):
            results = collect_test_results()

        assert results.total_tests == 2
        assert results.failed_count == 1
        assert results.passed_count == 1
        assert len(results.failed_tests) == 1
        assert "test_bar" in results.failed_tests[0].test_name

    def test_collect_no_cache(self):
        """Test collecting when no cache exists."""
        with patch("pathlib.Path.cwd", return_value=Path("/nonexistent")):
            results = collect_test_results()

        assert results.total_tests == 0
        assert results.failed_count == 0
        assert results.passed_count == 0


class TestIdentifyIssues:
    """Test identify_issues function."""

    def test_failed_tests_issue(self):
        """Test detection of failed tests."""
        report = AssessmentReport(test_results=TestResults(total_tests=10, failed_count=3))

        issues = identify_issues(report)

        assert len(issues) >= 1
        failed_test_issues = [i for i in issues if i.issue_type == IssueType.FAILED_TEST]
        assert len(failed_test_issues) == 1
        assert failed_test_issues[0].severity in ["high", "medium"]

    def test_git_conflicts_issue(self):
        """Test detection of git conflicts."""
        report = AssessmentReport(git_status=GitStatus(total_conflicts=2))

        issues = identify_issues(report)

        conflict_issues = [i for i in issues if i.issue_type == IssueType.GIT_CONFLICT]
        assert len(conflict_issues) == 1
        assert conflict_issues[0].severity == "critical"

    def test_uncommitted_changes_issue(self):
        """Test detection of many uncommitted changes."""
        report = AssessmentReport(git_status=GitStatus(total_modified=15, total_staged=5))

        issues = identify_issues(report)

        uncommitted_issues = [i for i in issues if i.issue_type == IssueType.UNCOMMITTED_CHANGES]
        assert len(uncommitted_issues) == 1
        assert uncommitted_issues[0].severity == "medium"

    def test_inactive_agent_issue(self):
        """Test detection of inactive agents with tasks."""
        report = AssessmentReport(
            agents=[
                AgentStatus(
                    "inactive-agent",
                    AgentType.BACKEND,
                    is_active=False,
                    current_task="pending work",
                )
            ]
        )

        issues = identify_issues(report)

        assert len(issues) >= 1
        assert any(i.issue_type == IssueType.RUNTIME_ERROR for i in issues)


class TestAssessStatus:
    """Test assess_status main function."""

    @patch("forge_harness.iteration.assess.query_tmux_sessions")
    @patch("forge_harness.iteration.assess.collect_git_status")
    @patch("forge_harness.iteration.assess.collect_test_results")
    def test_assess_status_integration(self, mock_tests, mock_git, mock_tmux):
        """Test full assess_status integration."""
        # Mock all collection functions
        mock_tmux.return_value = [AgentStatus("test", AgentType.BACKEND, True)]
        mock_git.return_value = GitStatus()
        mock_tests.return_value = TestResults()

        report = assess_status()

        assert report is not None
        assert report.agents is not None
        assert report.git_status is not None
        assert report.test_results is not None
        assert report.duration_seconds >= 0
        assert isinstance(report.assessed_at, datetime)

    def test_assess_status_under_60_seconds(self):
        """Test that assessment completes under 60 seconds."""
        report = assess_status()

        assert report.duration_seconds < 60, (
            f"Assessment took {report.duration_seconds}s, should be <60s"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
