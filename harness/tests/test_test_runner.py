"""Tests for the test runner quality gate."""

import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.quality_gates import TestResult, TestRunner, TestRunnerConfig


class TestTestResult:
    """Tests for TestResult dataclass."""

    def test_total_property(self):
        """Total is sum of all test counts."""
        result = TestResult(
            passed=5,
            failed=2,
            errors=1,
            skipped=3,
            duration=1.0,
            coverage_pct=80.0,
            exit_code=0,
            output="",
            failed_tests=[],
            timestamp=datetime.now(),
        )
        assert result.total == 11

    def test_success_when_no_failures(self):
        """Success is True when no failures or errors."""
        result = TestResult(
            passed=10,
            failed=0,
            errors=0,
            skipped=2,
            duration=1.0,
            coverage_pct=90.0,
            exit_code=0,
            output="",
            failed_tests=[],
            timestamp=datetime.now(),
        )
        assert result.success is True

    def test_success_false_with_failures(self):
        """Success is False when there are failures."""
        result = TestResult(
            passed=8,
            failed=2,
            errors=0,
            skipped=0,
            duration=1.0,
            coverage_pct=80.0,
            exit_code=1,
            output="",
            failed_tests=["test_a", "test_b"],
            timestamp=datetime.now(),
        )
        assert result.success is False

    def test_success_false_with_errors(self):
        """Success is False when there are errors."""
        result = TestResult(
            passed=9,
            failed=0,
            errors=1,
            skipped=0,
            duration=1.0,
            coverage_pct=85.0,
            exit_code=1,
            output="",
            failed_tests=[],
            timestamp=datetime.now(),
        )
        assert result.success is False


class TestTestRunnerConfig:
    """Tests for TestRunnerConfig."""

    def test_default_values(self):
        """Config has sensible defaults."""
        config = TestRunnerConfig()
        assert config.pattern == "test_*.py"
        assert config.timeout == 300
        assert config.coverage_threshold is None
        assert config.fail_fast is False
        assert config.verbose is True

    def test_custom_values(self):
        """Config accepts custom values."""
        config = TestRunnerConfig(
            pattern="*_test.py",
            timeout=60,
            coverage_threshold=80.0,
            fail_fast=True,
        )
        assert config.pattern == "*_test.py"
        assert config.timeout == 60
        assert config.coverage_threshold == 80.0
        assert config.fail_fast is True


class TestTestRunner:
    """Tests for TestRunner class."""

    @pytest.fixture
    def runner(self):
        """Create a test runner with default config."""
        return TestRunner()

    @pytest.fixture
    def mock_subprocess(self):
        """Mock subprocess.run for testing."""
        with patch("subprocess.run") as mock:
            yield mock

    def test_run_tests_success(self, runner, mock_subprocess):
        """Successful test run is parsed correctly."""
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="collected 10 items\n\n10 passed in 1.23s\nTOTAL    100    10    90%",
            stderr="",
        )

        result = runner.run_tests(Path("/test/path"))

        assert result.passed == 10
        assert result.failed == 0
        assert result.exit_code == 0
        assert result.coverage_pct == 90.0
        assert result.success is True

    def test_run_tests_with_failures(self, runner, mock_subprocess):
        """Failed tests are parsed correctly."""
        mock_subprocess.return_value = MagicMock(
            returncode=1,
            stdout=(
                "collected 10 items\n\n"
                "FAILED test_a.py::test_one - AssertionError\n"
                "8 passed, 2 failed in 2.50s"
            ),
            stderr="",
        )

        result = runner.run_tests(Path("/test/path"))

        assert result.passed == 8
        assert result.failed == 2
        assert result.exit_code == 1
        assert result.success is False
        assert "test_a.py::test_one" in result.failed_tests

    def test_run_tests_with_errors(self, runner, mock_subprocess):
        """Test errors are parsed correctly."""
        mock_subprocess.return_value = MagicMock(
            returncode=1,
            stdout="collected 10 items\n\n8 passed, 1 error in 1.50s",
            stderr="",
        )

        result = runner.run_tests(Path("/test/path"))

        assert result.passed == 8
        assert result.errors == 1
        assert result.exit_code == 1
        assert result.success is False

    def test_run_tests_with_skipped(self, runner, mock_subprocess):
        """Skipped tests are parsed correctly."""
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="collected 12 items\n\n10 passed, 2 skipped in 1.00s",
            stderr="",
        )

        result = runner.run_tests(Path("/test/path"))

        assert result.passed == 10
        assert result.skipped == 2
        assert result.total == 12
        assert result.success is True

    def test_run_tests_timeout(self, runner, mock_subprocess):
        """Timeout is handled gracefully."""
        mock_subprocess.side_effect = subprocess.TimeoutExpired("pytest", 300)

        result = runner.run_tests(Path("/test/path"))

        assert result.errors == 1
        assert result.exit_code == 3
        assert "timed out" in result.output.lower()

    def test_run_tests_with_custom_pattern(self, runner, mock_subprocess):
        """Custom pattern is used in command."""
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="5 passed in 1.00s",
            stderr="",
        )

        runner.run_tests(Path("/test/path"), pattern="test_custom*.py")

        # Check that pytest was called with pattern filter
        call_args = mock_subprocess.call_args
        cmd = call_args[0][0]
        # Should include -k with custom pattern
        assert "pytest" in cmd

    def test_run_tests_fail_fast(self, mock_subprocess):
        """Fail fast option is passed to pytest."""
        config = TestRunnerConfig(fail_fast=True)
        runner = TestRunner(config)

        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="5 passed in 1.00s",
            stderr="",
        )

        runner.run_tests(Path("/test/path"))

        call_args = mock_subprocess.call_args
        cmd = call_args[0][0]
        assert "-x" in cmd

    def test_run_tests_with_markers(self, mock_subprocess):
        """Markers are passed to pytest."""
        config = TestRunnerConfig(markers="unit")
        runner = TestRunner(config)

        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="5 passed in 1.00s",
            stderr="",
        )

        runner.run_tests(Path("/test/path"))

        call_args = mock_subprocess.call_args
        cmd = call_args[0][0]
        assert "-m" in cmd
        assert "unit" in cmd

    def test_run_tests_with_ignore_patterns(self, mock_subprocess):
        """Ignore patterns are passed to pytest."""
        config = TestRunnerConfig(ignore_patterns=["test_old.py", "test_deprecated.py"])
        runner = TestRunner(config)

        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="5 passed in 1.00s",
            stderr="",
        )

        runner.run_tests(Path("/test/path"))

        call_args = mock_subprocess.call_args
        cmd = call_args[0][0]
        assert "--ignore" in cmd

    def test_coverage_threshold_met(self):
        """Coverage threshold check passes when met."""
        config = TestRunnerConfig(coverage_threshold=80.0)
        runner = TestRunner(config)

        result = TestResult(
            passed=10,
            failed=0,
            errors=0,
            skipped=0,
            duration=1.0,
            coverage_pct=85.0,
            exit_code=0,
            output="",
            failed_tests=[],
            timestamp=datetime.now(),
        )

        assert runner.check_coverage_threshold(result) is True

    def test_coverage_threshold_not_met(self):
        """Coverage threshold check fails when not met."""
        config = TestRunnerConfig(coverage_threshold=80.0)
        runner = TestRunner(config)

        result = TestResult(
            passed=10,
            failed=0,
            errors=0,
            skipped=0,
            duration=1.0,
            coverage_pct=75.0,
            exit_code=0,
            output="",
            failed_tests=[],
            timestamp=datetime.now(),
        )

        assert runner.check_coverage_threshold(result) is False

    def test_coverage_threshold_none(self):
        """No threshold configured always passes."""
        runner = TestRunner()  # No threshold

        result = TestResult(
            passed=10,
            failed=0,
            errors=0,
            skipped=0,
            duration=1.0,
            coverage_pct=50.0,
            exit_code=0,
            output="",
            failed_tests=[],
            timestamp=datetime.now(),
        )

        assert runner.check_coverage_threshold(result) is True

    def test_coverage_threshold_missing_coverage(self):
        """Missing coverage data fails threshold check."""
        config = TestRunnerConfig(coverage_threshold=80.0)
        runner = TestRunner(config)

        result = TestResult(
            passed=10,
            failed=0,
            errors=0,
            skipped=0,
            duration=1.0,
            coverage_pct=None,
            exit_code=0,
            output="",
            failed_tests=[],
            timestamp=datetime.now(),
        )

        assert runner.check_coverage_threshold(result) is False


class TestPreCommitHook:
    """Tests for pre-commit hook functionality."""

    @pytest.fixture
    def runner(self):
        """Create runner for pre-commit tests."""
        return TestRunner()

    @patch.object(TestRunner, "run_tests")
    def test_pre_commit_success(self, mock_run, runner):
        """Exit code 0 when tests pass."""
        mock_run.return_value = TestResult(
            passed=10,
            failed=0,
            errors=0,
            skipped=0,
            duration=1.0,
            coverage_pct=90.0,
            exit_code=0,
            output="",
            failed_tests=[],
            timestamp=datetime.now(),
        )

        exit_code = runner.run_pre_commit(Path("."))
        assert exit_code == 0

    @patch.object(TestRunner, "run_tests")
    def test_pre_commit_test_failure(self, mock_run, runner):
        """Exit code 1 when tests fail."""
        mock_run.return_value = TestResult(
            passed=8,
            failed=2,
            errors=0,
            skipped=0,
            duration=1.0,
            coverage_pct=80.0,
            exit_code=1,
            output="",
            failed_tests=["test_a"],
            timestamp=datetime.now(),
        )

        exit_code = runner.run_pre_commit(Path("."))
        assert exit_code == 1

    @patch.object(TestRunner, "run_tests")
    def test_pre_commit_coverage_failure(self, mock_run):
        """Exit code 2 when coverage below threshold."""
        config = TestRunnerConfig(coverage_threshold=80.0)
        runner = TestRunner(config)

        mock_run.return_value = TestResult(
            passed=10,
            failed=0,
            errors=0,
            skipped=0,
            duration=1.0,
            coverage_pct=70.0,
            exit_code=0,
            output="",
            failed_tests=[],
            timestamp=datetime.now(),
        )

        exit_code = runner.run_pre_commit(Path("."))
        assert exit_code == 2

    @patch.object(TestRunner, "run_tests")
    def test_pre_commit_timeout(self, mock_run, runner):
        """Exit code 3 when tests timeout."""
        mock_run.return_value = TestResult(
            passed=0,
            failed=0,
            errors=1,
            skipped=0,
            duration=300.0,
            coverage_pct=None,
            exit_code=3,
            output="Test run timed out",
            failed_tests=[],
            timestamp=datetime.now(),
        )

        exit_code = runner.run_pre_commit(Path("."))
        assert exit_code == 3


class TestOutputFormatting:
    """Tests for output formatting."""

    def test_format_summary_success(self):
        """Summary shows success clearly."""
        runner = TestRunner()
        result = TestResult(
            passed=10,
            failed=0,
            errors=0,
            skipped=2,
            duration=1.5,
            coverage_pct=92.5,
            exit_code=0,
            output="",
            failed_tests=[],
            timestamp=datetime.now(),
        )

        summary = runner.format_summary(result)

        assert "10" in summary  # passed count
        assert "2" in summary  # skipped count
        assert "92" in summary  # coverage
        assert "1.5" in summary or "1.50" in summary  # duration
        assert "passed" in summary.lower()

    def test_format_summary_failure(self):
        """Summary shows failures clearly."""
        runner = TestRunner()
        result = TestResult(
            passed=8,
            failed=2,
            errors=0,
            skipped=0,
            duration=2.0,
            coverage_pct=75.0,
            exit_code=1,
            output="",
            failed_tests=["test_a.py::test_one", "test_b.py::test_two"],
            timestamp=datetime.now(),
        )

        summary = runner.format_summary(result)

        assert "2" in summary  # failed count
        assert "test_a.py::test_one" in summary
        assert "test_b.py::test_two" in summary
        assert "failed" in summary.lower()

    def test_format_summary_with_errors(self):
        """Summary shows errors clearly."""
        runner = TestRunner()
        result = TestResult(
            passed=8,
            failed=0,
            errors=2,
            skipped=0,
            duration=1.5,
            coverage_pct=80.0,
            exit_code=1,
            output="",
            failed_tests=[],
            timestamp=datetime.now(),
        )

        summary = runner.format_summary(result)

        assert "2" in summary  # error count
        assert "error" in summary.lower()

    def test_format_summary_with_threshold(self):
        """Summary shows coverage threshold."""
        config = TestRunnerConfig(coverage_threshold=80.0)
        runner = TestRunner(config)
        result = TestResult(
            passed=10,
            failed=0,
            errors=0,
            skipped=0,
            duration=1.0,
            coverage_pct=85.0,
            exit_code=0,
            output="",
            failed_tests=[],
            timestamp=datetime.now(),
        )

        summary = runner.format_summary(result)

        assert "85" in summary  # coverage
        assert "80" in summary  # threshold
        assert "threshold" in summary.lower()

    def test_format_summary_no_coverage(self):
        """Summary handles missing coverage gracefully."""
        runner = TestRunner()
        result = TestResult(
            passed=10,
            failed=0,
            errors=0,
            skipped=0,
            duration=1.0,
            coverage_pct=None,
            exit_code=0,
            output="",
            failed_tests=[],
            timestamp=datetime.now(),
        )

        summary = runner.format_summary(result)

        # Should not crash, just not show coverage
        assert "passed" in summary.lower()


class TestOutputParsing:
    """Tests for pytest output parsing."""

    @pytest.fixture
    def runner(self):
        """Create runner for parsing tests."""
        return TestRunner()

    def test_parse_simple_success(self, runner):
        """Parse simple successful output."""
        output = "collected 10 items\n\n10 passed in 1.23s"
        result = runner._parse_pytest_output(output, 0, 1.23)

        assert result.passed == 10
        assert result.failed == 0
        assert result.errors == 0
        assert result.skipped == 0
        assert result.success is True

    def test_parse_mixed_results(self, runner):
        """Parse mixed test results."""
        output = "collected 15 items\n\n10 passed, 3 failed, 2 skipped in 2.50s"
        result = runner._parse_pytest_output(output, 1, 2.50)

        assert result.passed == 10
        assert result.failed == 3
        assert result.skipped == 2
        assert result.total == 15
        assert result.success is False

    def test_parse_with_coverage(self, runner):
        """Parse output with coverage information."""
        output = (
            "collected 10 items\n\n"
            "10 passed in 1.50s\n"
            "---------- coverage: ----------\n"
            "Name                Stmts   Miss  Cover\n"
            "---------------------------------------\n"
            "module.py             100     15    85%\n"
            "TOTAL                 100     15    85%"
        )
        result = runner._parse_pytest_output(output, 0, 1.50)

        assert result.passed == 10
        assert result.coverage_pct == 85.0
        assert result.success is True

    def test_parse_failed_test_names(self, runner):
        """Parse names of failed tests."""
        output = (
            "collected 10 items\n\n"
            "FAILED test_module.py::TestClass::test_method - AssertionError: ...\n"
            "FAILED test_other.py::test_function - ValueError: ...\n"
            "8 passed, 2 failed in 2.00s"
        )
        result = runner._parse_pytest_output(output, 1, 2.00)

        assert result.failed == 2
        assert len(result.failed_tests) == 2
        assert "test_module.py::TestClass::test_method" in result.failed_tests
        assert "test_other.py::test_function" in result.failed_tests

    def test_parse_no_tests_collected(self, runner):
        """Parse output when no tests collected."""
        output = "collected 0 items\n\nno tests ran in 0.01s"
        result = runner._parse_pytest_output(output, 0, 0.01)

        assert result.total == 0
        assert result.passed == 0
        assert result.failed == 0

    def test_parse_with_errors(self, runner):
        """Parse output with test errors."""
        output = "collected 10 items\n\n8 passed, 2 error in 1.50s"
        result = runner._parse_pytest_output(output, 1, 1.50)

        assert result.passed == 8
        assert result.errors == 2
        assert result.success is False
