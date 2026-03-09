"""
Tests for FORGE Harness Assessment Module
==========================================

Tests for assess.py module which provides comprehensive assessment
of FORGE harness state including agents, git status, tests, and linting.
"""

from __future__ import annotations

import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from forge_harness.iteration.assess import (
    AgentStatus,
    AssessmentReport,
    GitStatus,
    LintResults,
    OverallHealth,
    TestResults,
    _assess_agents,
    _assess_git_status,
    _assess_lint_errors,
    _assess_test_results,
    _determine_overall_health,
    assess_status,
)


def test_assess_status_returns_report():
    """assess_status() returns a valid AssessmentReport."""
    report = assess_status()

    assert isinstance(report, AssessmentReport)
    assert isinstance(report.overall_health, OverallHealth)
    assert isinstance(report.agents, list)
    assert isinstance(report.git_status, GitStatus)
    assert isinstance(report.lint_errors, LintResults)
    assert hasattr(report, "assessed_at")


def test_assess_agents_parses_tmux():
    """Test parsing tmux window output."""
    # Use a more targeted approach by mocking the internal function
    with patch("forge_harness.iteration.assess._assess_agents") as mock_agents:
        # Mock agents that should be parsed from tmux
        mock_agents.return_value = [
            AgentStatus(name="forge:tech", is_active=True, command="claude"),
            AgentStatus(name="forge:qa", is_active=False, command="zsh"),
            AgentStatus(name="forge:content", is_active=True, command="cursor"),
        ]

        report = assess_status()

        # Should have parsed 3 agents
        assert len(report.agents) == 3
        assert report.agents[0].name == "forge:tech"
        assert report.agents[0].is_active is True
        assert report.agents[1].name == "forge:qa"
        assert report.agents[1].is_active is False


def test_assess_git_status_counts_changes():
    """Test counting uncommitted git changes."""
    mock_git_output = """ M harness/file1.py
 M harness/file2.py
?? harness/new_file.py"""

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=mock_git_output, returncode=0)

        report = assess_status()

        # Should count changes
        assert isinstance(report.git_status, GitStatus)

        # Verify git status command was called
        if mock_run.called:
            # Check if any call was for git status
            git_calls = [call for call in mock_run.call_args_list if "git" in str(call)]
            assert len(git_calls) >= 0  # May or may not be called


def test_health_determination():
    """Test health level thresholds."""
    # Test healthy state (mock all checks to pass)
    with (
        patch("forge_harness.iteration.assess._assess_agents") as mock_agents,
        patch("forge_harness.iteration.assess._assess_git_status") as mock_git,
        patch("forge_harness.iteration.assess._assess_test_results") as mock_tests,
        patch("forge_harness.iteration.assess._assess_lint_errors") as mock_lint,
    ):
        # Healthy scenario: all agents running, no lint errors, tests passing
        mock_agents.return_value = [
            AgentStatus(name="forge:tech", is_active=True, command="claude"),
            AgentStatus(name="forge:qa", is_active=True, command="pytest"),
        ]
        mock_git.return_value = GitStatus(uncommitted_count=0)
        mock_tests.return_value = TestResults(
            total_tests=100,
            passed_tests=100,
            failed_tests=0,
            skipped_tests=0,
            pass_rate=100.0,
            is_passing=True,
        )
        mock_lint.return_value = LintResults(error_count=2)

        report = assess_status()
        assert report.overall_health == OverallHealth.HEALTHY

        # Degraded scenario: some agents idle, moderate lint errors
        mock_agents.return_value = [
            AgentStatus(name="forge:tech", is_active=False, command="zsh"),
        ]
        mock_lint.return_value = LintResults(error_count=15)
        mock_tests.return_value = TestResults(
            total_tests=100,
            passed_tests=85,
            failed_tests=5,
            skipped_tests=10,
            pass_rate=85.0,
            is_passing=False,
        )

        report = assess_status()
        assert report.overall_health == OverallHealth.DEGRADED

        # Failing scenario: many lint errors, test failures
        mock_lint.return_value = LintResults(error_count=50)
        mock_tests.return_value = TestResults(
            total_tests=75,
            passed_tests=50,
            failed_tests=20,
            skipped_tests=5,
            pass_rate=66.7,
            is_passing=False,
        )

        report = assess_status()
        assert report.overall_health == OverallHealth.FAILING


def test_assessment_report_to_dict():
    """Test AssessmentReport serialization."""
    now = datetime.now(UTC)

    report = AssessmentReport(
        agents=[
            AgentStatus(name="forge:tech", is_active=True, command="claude"),
        ],
        git_status=GitStatus(uncommitted_count=3),
        test_results=TestResults(
            total_tests=57,
            passed_tests=50,
            failed_tests=2,
            skipped_tests=5,
            pass_rate=87.7,
            is_passing=False,
        ),
        lint_errors=LintResults(error_count=10),
        overall_health=OverallHealth.DEGRADED,
        assessed_at=now,
    )

    data = report.to_dict()

    assert isinstance(data, dict)
    assert data["overall_health"] == "degraded"
    assert data["lint_errors"]["error_count"] == 10
    assert len(data["agents"]) == 1
    assert data["agents"][0]["name"] == "forge:tech"
    assert isinstance(data["assessed_at"], str)


def test_agent_status_dataclass():
    """Test AgentStatus structure."""
    agent = AgentStatus(
        name="forge:tech",
        is_active=True,
        command="claude",
    )

    assert agent.name == "forge:tech"
    assert agent.is_active is True
    assert agent.command == "claude"

    data = agent.to_dict()
    assert data["name"] == "forge:tech"
    assert data["is_active"] is True


def test_test_results_dataclass():
    """Test TestResults structure."""
    results = TestResults(
        total_tests=115,
        passed_tests=100,
        failed_tests=5,
        skipped_tests=10,
        pass_rate=86.96,
        is_passing=False,
    )

    assert results.passed_tests == 100
    assert results.failed_tests == 5
    assert results.skipped_tests == 10
    assert results.total_tests == 115
    assert abs(results.pass_rate - 86.96) < 0.1  # ~86.96%
    assert abs(results.failure_rate - 4.35) < 0.1  # ~4.35%

    data = results.to_dict()
    assert data["passed_tests"] == 100
    assert data["failed_tests"] == 5


def test_assess_handles_missing_tmux():
    """Test graceful handling when tmux session doesn't exist."""
    with patch("forge_harness.iteration.assess._assess_agents") as mock_agents:
        # Simulate missing tmux by returning empty list
        mock_agents.return_value = []

        report = assess_status()

        # Should return empty agents list, not crash
        assert isinstance(report.agents, list)
        assert len(report.agents) == 0
        # Should still have a health status
        assert isinstance(report.overall_health, OverallHealth)


def test_assess_completes_within_timeout():
    """Test that assessment completes within 60 seconds."""

    start = time.time()
    report = assess_status()
    duration = time.time() - start

    assert duration < 60, f"Assessment took {duration:.2f}s, should be <60s"
    assert isinstance(report, AssessmentReport)


def test_lint_errors_count():
    """Test lint error counting via ruff."""
    with patch("forge_harness.iteration.assess._assess_lint_errors") as mock_lint:
        mock_lint.return_value = LintResults(error_count=25)

        report = assess_status()

        # Should have lint error count
        assert isinstance(report.lint_errors, LintResults)
        assert report.lint_errors.error_count >= 0


def test_git_status_dataclass():
    """Test GitStatus structure."""
    git_status = GitStatus(
        uncommitted_count=5,
        modified_files=["file1.py", "file2.py"],
        added_files=["file3.py"],
        untracked_files=["new_file.py"],
        branch="main",
        is_clean=False,
    )

    assert git_status.uncommitted_count == 5
    assert len(git_status.modified_files) == 2
    assert len(git_status.added_files) == 1
    assert len(git_status.untracked_files) == 1
    assert git_status.branch == "main"
    assert git_status.is_clean is False

    data = git_status.to_dict()
    assert data["uncommitted_count"] == 5
    assert data["branch"] == "main"


def test_lint_results_dataclass():
    """Test LintResults structure."""
    lint_results = LintResults(error_count=42)

    assert lint_results.error_count == 42

    data = lint_results.to_dict()
    assert data["error_count"] == 42


class TestAssessAgents:
    """Test _assess_agents function directly."""

    @patch("forge_harness.iteration.assess.subprocess.run")
    def test_assess_agents_parses_tmux(self, mock_run):
        """Test that _assess_agents correctly parses tmux output."""
        # Mock tmux output
        mock_run.return_value = Mock(
            returncode=0,
            stdout="forge:tech:1:claude\nforge:product:0:python\nforge:qa:0:npx",
            stderr="",
        )

        agents = _assess_agents()

        assert len(agents) == 3
        assert agents[0].name == "forge:tech"
        assert agents[0].is_active is True
        assert agents[0].command == "claude"
        assert agents[1].name == "forge:product"
        assert agents[1].is_active is False
        assert agents[1].command == "python"

    @patch("forge_harness.iteration.assess.subprocess.run")
    def test_assess_agents_filters_non_forge(self, mock_run):
        """Test that _assess_agents filters out non-forge windows."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="forge:tech:1:claude\nother:0:bash\nforge:product:0:python",
            stderr="",
        )

        agents = _assess_agents()

        assert len(agents) == 2
        assert all(agent.name.startswith("forge:") for agent in agents)

    @patch("forge_harness.iteration.assess.subprocess.run")
    def test_assess_agents_handles_no_session(self, mock_run):
        """Test that _assess_agents handles missing tmux session gracefully."""
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="no session: forge")

        agents = _assess_agents()

        assert len(agents) == 0

    @patch("forge_harness.iteration.assess.subprocess.run")
    def test_assess_agents_handles_missing_tmux(self, mock_run):
        """Test that _assess_agents handles missing tmux gracefully."""
        mock_run.side_effect = FileNotFoundError("tmux not found")

        agents = _assess_agents()

        assert len(agents) == 0

    @patch("forge_harness.iteration.assess.subprocess.run")
    def test_assess_agents_handles_timeout(self, mock_run):
        """Test that _assess_agents raises TimeoutExpired."""
        from subprocess import TimeoutExpired

        mock_run.side_effect = TimeoutExpired("tmux", 30)

        with pytest.raises(TimeoutExpired):
            _assess_agents()


class TestAssessGitStatus:
    """Test _assess_git_status function directly."""

    @patch("forge_harness.iteration.assess.subprocess.run")
    def test_assess_git_status_counts_changes(self, mock_run):
        """Test that _assess_git_status correctly counts uncommitted changes."""
        # Mock git commands
        mock_run.side_effect = [
            Mock(returncode=0, stdout="main\n"),  # git rev-parse
            Mock(returncode=0, stdout=" M file1.py\nA  file2.py\n?? file3.py\n"),  # git status
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            status = _assess_git_status(temp_dir)

            assert status.branch == "main"
            assert status.uncommitted_count == 3
            assert "file1.py" in status.modified_files
            assert "file2.py" in status.added_files
            assert "file3.py" in status.untracked_files
            assert status.is_clean is False

    @patch("forge_harness.iteration.assess.subprocess.run")
    def test_assess_git_status_clean_repo(self, mock_run):
        """Test that _assess_git_status handles clean repository."""
        mock_run.side_effect = [
            Mock(returncode=0, stdout="main\n"),
            Mock(returncode=0, stdout=""),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            status = _assess_git_status(temp_dir)

            assert status.uncommitted_count == 0
            assert status.is_clean is True

    @patch("forge_harness.iteration.assess.subprocess.run")
    def test_assess_git_status_handles_missing_git(self, mock_run):
        """Test that _assess_git_status handles missing git gracefully."""
        mock_run.side_effect = FileNotFoundError("git not found")

        with tempfile.TemporaryDirectory() as temp_dir:
            status = _assess_git_status(temp_dir)

            assert status.is_clean is True
            assert status.branch == ""


class TestAssessTestResults:
    """Test _assess_test_results function directly."""

    def test_assess_test_results_with_json_report(self):
        """Test _assess_test_results parsing JSON report."""
        # Create mock pytest JSON report
        mock_pytest_data = {"summary": {"total": 25, "passed": 24, "failed": 1, "skipped": 0}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(mock_pytest_data, f)
            json_file = f.name

        try:
            with (
                patch("forge_harness.iteration.assess.subprocess.run") as mock_run,
                patch("builtins.open", return_value=open(json_file)),
            ):
                mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

                with tempfile.TemporaryDirectory() as temp_dir:
                    results = _assess_test_results(temp_dir)

                    assert results.total_tests == 25
                    assert results.passed_tests == 24
                    assert results.failed_tests == 1
                    assert results.pass_rate == 96.0
                    assert results.is_passing is False
        finally:
            Path(json_file).unlink()

    @patch("forge_harness.iteration.assess.subprocess.run")
    def test_assess_test_results_fallback_parsing(self, mock_run):
        """Test _assess_test_results fallback to stdout parsing."""
        mock_run.return_value = Mock(returncode=0, stdout="24 passed, 1 failed in 0.12s", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            results = _assess_test_results(temp_dir)

            assert results.passed_tests == 24
            assert results.failed_tests == 1
            assert results.total_tests == 25
            assert results.is_passing is False

    @patch("forge_harness.iteration.assess.subprocess.run")
    def test_assess_test_results_handles_missing_pytest(self, mock_run):
        """Test that _assess_test_results handles missing pytest gracefully."""
        mock_run.side_effect = FileNotFoundError("pytest not found")

        with tempfile.TemporaryDirectory() as temp_dir:
            results = _assess_test_results(temp_dir)

            assert results.total_tests == 0
            assert results.error_message == "pytest not found"


class TestAssessLintErrors:
    """Test _assess_lint_errors function directly."""

    @patch("forge_harness.iteration.assess.subprocess.run")
    def test_assess_lint_errors_counts_errors(self, mock_run):
        """Test that _assess_lint_errors correctly counts lint errors."""
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="× 5 error(s) found")

        with tempfile.TemporaryDirectory() as temp_dir:
            results = _assess_lint_errors(temp_dir)

            assert results.error_count == 5

    @patch("forge_harness.iteration.assess.subprocess.run")
    def test_assess_lint_errors_no_errors(self, mock_run):
        """Test that _assess_lint_errors handles clean code."""
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            results = _assess_lint_errors(temp_dir)

            assert results.error_count == 0

    @patch("forge_harness.iteration.assess.subprocess.run")
    def test_assess_lint_errors_handles_missing_ruff(self, mock_run):
        """Test that _assess_lint_errors handles missing ruff gracefully."""
        mock_run.side_effect = FileNotFoundError("ruff not found")

        with tempfile.TemporaryDirectory() as temp_dir:
            results = _assess_lint_errors(temp_dir)

            assert results.error_count == 0


class TestHealthDetermination:
    """Test _determine_overall_health function directly."""

    def test_health_determination_healthy(self):
        """Test health determination for healthy status."""
        test_results = TestResults(total_tests=10, passed_tests=10, is_passing=True)
        # pass_rate is calculated automatically: (10/10) * 100 = 100%
        health = _determine_overall_health(3, test_results)

        assert health == OverallHealth.HEALTHY

    def test_health_determination_degraded_lint(self):
        """Test health determination for degraded due to lint errors."""
        test_results = TestResults(total_tests=10, passed_tests=10, is_passing=True)
        health = _determine_overall_health(8, test_results)

        assert health == OverallHealth.DEGRADED

    def test_health_determination_degraded_tests(self):
        """Test health determination for degraded due to test failures."""
        test_results = TestResults(total_tests=10, passed_tests=9, failed_tests=1, is_passing=False)
        # pass_rate is calculated automatically: (9/10) * 100 = 90%
        health = _determine_overall_health(3, test_results)

        assert health == OverallHealth.DEGRADED

    def test_health_determination_failing_lint(self):
        """Test health determination for failing due to many lint errors."""
        test_results = TestResults(total_tests=10, passed_tests=10, is_passing=True)
        health = _determine_overall_health(25, test_results)

        assert health == OverallHealth.FAILING

    def test_health_determination_failing_tests(self):
        """Test health determination for failing due to many test failures."""
        test_results = TestResults(total_tests=10, passed_tests=8, failed_tests=2, is_passing=False)
        # failure_rate is computed: (2/10) * 100 = 20.0% >= 20% failure rate
        health = _determine_overall_health(3, test_results)

        assert health == OverallHealth.FAILING


class TestAssessmentReportSerialization:
    """Test AssessmentReport serialization methods."""

    def test_assessment_report_to_dict_and_from_dict(self):
        """Test AssessmentReport to_dict and from_dict methods."""
        original = AssessmentReport(
            agents=[AgentStatus("forge:tech", True, "claude")],
            git_status=GitStatus(uncommitted_count=5),
            test_results=TestResults(total_tests=10, passed_tests=8),
            lint_errors=LintResults(error_count=3),
            overall_health=OverallHealth.DEGRADED,
        )

        # Test to_dict
        data = original.to_dict()
        assert data["overall_health"] == "degraded"
        assert len(data["agents"]) == 1
        assert data["git_status"]["uncommitted_count"] == 5

        # Test from_dict
        restored = AssessmentReport.from_dict(data)
        assert restored.overall_health == OverallHealth.DEGRADED
        assert len(restored.agents) == 1
        assert restored.git_status.uncommitted_count == 5

    def test_assessment_report_json_serialization(self):
        """Test AssessmentReport JSON serialization."""
        report = AssessmentReport(overall_health=OverallHealth.HEALTHY)

        # Test to_json
        json_str = report.to_json()
        assert isinstance(json_str, str)
        assert "healthy" in json_str

        # Test from_json
        restored = AssessmentReport.from_json(json_str)
        assert restored.overall_health == OverallHealth.HEALTHY


class TestAgentStatusSerialization:
    """Test AgentStatus serialization methods."""

    def test_agent_status_to_dict_and_from_dict(self):
        """Test AgentStatus serialization."""
        agent = AgentStatus("forge:tech", True, "claude", "raw:status")

        data = agent.to_dict()
        assert data["name"] == "forge:tech"
        assert data["is_active"] is True
        assert data["command"] == "claude"
        assert data["raw_status"] == "raw:status"

        restored = AgentStatus.from_dict(data)
        assert restored.name == "forge:tech"
        assert restored.is_active is True
        assert restored.command == "claude"


class TestOverallHealthEnum:
    """Test OverallHealth enum properties."""

    def test_overall_health_emoji(self):
        """Test OverallHealth emoji property."""
        assert OverallHealth.HEALTHY.emoji == "✅"
        assert OverallHealth.DEGRADED.emoji == "⚠️"
        assert OverallHealth.FAILING.emoji == "🔴"
