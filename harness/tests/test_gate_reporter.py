"""Tests for quality gate reporter."""

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from forge_harness.quality_gates.reporter import (
    OutputFormat,
    QualityGateReporter,
    ReportConfig,
    create_reporter,
)


@dataclass
class MockGateResult:
    """Mock gate result for testing."""

    gate_name: str
    status: object
    duration: float
    error: str = None


@dataclass
class MockStatus:
    """Mock status enum."""

    value: str


@dataclass
class MockOrchestratorResult:
    """Mock orchestrator result for testing."""

    overall_success: bool
    gate_results: list[MockGateResult]
    passed_gates: int
    failed_gates: int
    total_duration: float
    started_at: datetime = None

    def __post_init__(self):
        if self.started_at is None:
            self.started_at = datetime(2025, 1, 29, 12, 0, 0)


@pytest.fixture
def passing_result():
    """Create a mock passing result."""
    return MockOrchestratorResult(
        overall_success=True,
        gate_results=[
            MockGateResult(gate_name="lint", status=MockStatus(value="passed"), duration=1.23),
            MockGateResult(gate_name="tests", status=MockStatus(value="passed"), duration=5.67),
        ],
        passed_gates=2,
        failed_gates=0,
        total_duration=6.9,
    )


@pytest.fixture
def failing_result():
    """Create a mock failing result."""
    return MockOrchestratorResult(
        overall_success=False,
        gate_results=[
            MockGateResult(gate_name="lint", status=MockStatus(value="passed"), duration=1.23),
            MockGateResult(
                gate_name="tests",
                status=MockStatus(value="failed"),
                duration=3.45,
                error="Test suite failed: 2 tests failed",
            ),
        ],
        passed_gates=1,
        failed_gates=1,
        total_duration=4.68,
    )


class TestQualityGateReporter:
    """Test suite for QualityGateReporter."""

    def test_create_reporter_default_config(self):
        """Test creating reporter with default config."""
        reporter = create_reporter()
        assert isinstance(reporter, QualityGateReporter)
        assert reporter.config.format == OutputFormat.TEXT
        assert reporter.config.verbose is False

    def test_create_reporter_custom_config(self):
        """Test creating reporter with custom config."""
        config = ReportConfig(format=OutputFormat.JSON, verbose=True)
        reporter = create_reporter(config)
        assert reporter.config.format == OutputFormat.JSON
        assert reporter.config.verbose is True

    def test_format_text_passing(self, passing_result):
        """Test text format output for passing gates."""
        reporter = QualityGateReporter()
        output = reporter.format_result(passing_result)

        assert "Quality Gate Results: PASSED" in output
        assert "[+] lint: passed (1.23s)" in output
        assert "[+] tests: passed (5.67s)" in output
        assert "Total: 2/2 passed" in output
        assert "Duration: 6.90s" in output

    def test_format_text_failing(self, failing_result):
        """Test text format output for failing gates."""
        reporter = QualityGateReporter()
        output = reporter.format_result(failing_result)

        assert "Quality Gate Results: FAILED" in output
        assert "[+] lint: passed (1.23s)" in output
        assert "[-] tests: failed (3.45s)" in output
        assert "Total: 1/2 passed" in output
        assert "Duration: 4.68s" in output

    def test_format_text_verbose(self, failing_result):
        """Test text format output with verbose mode enabled."""
        config = ReportConfig(verbose=True)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)

        assert "Error: Test suite failed: 2 tests failed" in output

    def test_format_text_non_verbose(self, failing_result):
        """Test text format output with verbose mode disabled."""
        config = ReportConfig(verbose=False)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)

        assert "Error:" not in output

    def test_format_json_passing(self, passing_result):
        """Test JSON format output for passing gates."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)

        data = json.loads(output)
        assert data["success"] is True
        assert len(data["gates"]) == 2
        assert data["gates"][0]["name"] == "lint"
        assert data["gates"][0]["status"] == "passed"
        assert data["gates"][0]["duration"] == 1.23
        assert data["summary"]["passed"] == 2
        assert data["summary"]["failed"] == 0
        assert data["summary"]["total"] == 2
        assert data["timestamp"] == "2025-01-29T12:00:00"

    def test_format_json_failing(self, failing_result):
        """Test JSON format output for failing gates."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)

        data = json.loads(output)
        assert data["success"] is False
        assert len(data["gates"]) == 2
        assert data["gates"][1]["name"] == "tests"
        assert data["gates"][1]["status"] == "failed"
        assert data["gates"][1]["error"] == "Test suite failed: 2 tests failed"
        assert data["summary"]["passed"] == 1
        assert data["summary"]["failed"] == 1

    def test_format_markdown_passing(self, passing_result):
        """Test Markdown format output for passing gates."""
        config = ReportConfig(format=OutputFormat.MARKDOWN)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)

        assert "# Quality Gate Report :white_check_mark:" in output
        assert "| Gate | Status | Duration |" in output
        assert "| lint | :white_check_mark: passed | 1.23s |" in output
        assert "| tests | :white_check_mark: passed | 5.67s |" in output
        assert "**Summary:** 2/2 gates passed" in output
        assert "**Total Duration:** 6.90s" in output

    def test_format_markdown_failing(self, failing_result):
        """Test Markdown format output for failing gates."""
        config = ReportConfig(format=OutputFormat.MARKDOWN)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)

        assert "# Quality Gate Report :x:" in output
        assert "| lint | :white_check_mark: passed | 1.23s |" in output
        assert "| tests | :x: failed | 3.45s |" in output
        assert "**Summary:** 1/2 gates passed" in output

    def test_format_github_passing(self, passing_result):
        """Test GitHub Actions format output for passing gates."""
        config = ReportConfig(format=OutputFormat.GITHUB)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)

        assert "::notice::Quality gate 'lint' passed" in output
        assert "::notice::Quality gate 'tests' passed" in output
        assert "::notice::All quality gates passed!" in output
        assert "::error::" not in output

    def test_format_github_failing(self, failing_result):
        """Test GitHub Actions format output for failing gates."""
        config = ReportConfig(format=OutputFormat.GITHUB)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)

        assert "::notice::Quality gate 'lint' passed" in output
        assert "::error::Quality gate 'tests' failed: Test suite failed: 2 tests failed" in output
        assert "::error::1 quality gate(s) failed" in output

    def test_write_result_text(self, passing_result):
        """Test writing result to file in text format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.txt"
            reporter = QualityGateReporter()
            reporter.write_result(passing_result, str(output_path))

            assert output_path.exists()
            content = output_path.read_text()
            assert "Quality Gate Results: PASSED" in content

    def test_write_result_json(self, passing_result):
        """Test writing result to file in JSON format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.json"
            config = ReportConfig(format=OutputFormat.JSON)
            reporter = QualityGateReporter(config)
            reporter.write_result(passing_result, str(output_path))

            assert output_path.exists()
            content = output_path.read_text()
            data = json.loads(content)
            assert data["success"] is True

    def test_write_result_markdown(self, failing_result):
        """Test writing result to file in Markdown format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.md"
            config = ReportConfig(format=OutputFormat.MARKDOWN)
            reporter = QualityGateReporter(config)
            reporter.write_result(failing_result, str(output_path))

            assert output_path.exists()
            content = output_path.read_text()
            assert "# Quality Gate Report" in content

    def test_write_result_github(self, failing_result):
        """Test writing result to file in GitHub Actions format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.txt"
            config = ReportConfig(format=OutputFormat.GITHUB)
            reporter = QualityGateReporter(config)
            reporter.write_result(failing_result, str(output_path))

            assert output_path.exists()
            content = output_path.read_text()
            assert "::error::" in content
            assert "::notice::" in content

    def test_print_result(self, passing_result, capsys):
        """Test printing result to stdout."""
        reporter = QualityGateReporter()
        reporter.print_result(passing_result)

        captured = capsys.readouterr()
        assert "Quality Gate Results: PASSED" in captured.out

    def test_multiple_formats_same_reporter(self, passing_result):
        """Test that changing format config works correctly."""
        # Start with text
        config = ReportConfig(format=OutputFormat.TEXT)
        reporter = QualityGateReporter(config)
        text_output = reporter.format_result(passing_result)
        assert "Quality Gate Results:" in text_output

        # Change to JSON
        reporter.config.format = OutputFormat.JSON
        json_output = reporter.format_result(passing_result)
        data = json.loads(json_output)
        assert "success" in data

        # Change to Markdown
        reporter.config.format = OutputFormat.MARKDOWN
        md_output = reporter.format_result(passing_result)
        assert "# Quality Gate Report" in md_output

    def test_empty_gate_results(self):
        """Test handling of empty gate results."""
        empty_result = MockOrchestratorResult(
            overall_success=True,
            gate_results=[],
            passed_gates=0,
            failed_gates=0,
            total_duration=0.0,
        )

        reporter = QualityGateReporter()
        output = reporter.format_result(empty_result)

        assert "Total: 0/0 passed" in output

    def test_verbose_with_no_errors(self, passing_result):
        """Test verbose mode when there are no errors to display."""
        config = ReportConfig(verbose=True)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)

        # Should not crash and should not show error lines
        assert "Error:" not in output
        assert "Quality Gate Results: PASSED" in output

    def test_json_output_is_valid(self, failing_result):
        """Test that JSON output is always valid JSON."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)

        # Should not raise JSONDecodeError
        data = json.loads(output)
        assert isinstance(data, dict)
        assert "success" in data
        assert "gates" in data
        assert "summary" in data

    def test_markdown_table_structure(self, passing_result):
        """Test that Markdown output has proper table structure."""
        config = ReportConfig(format=OutputFormat.MARKDOWN)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)

        lines = output.split("\n")
        # Find table header
        header_idx = next(i for i, line in enumerate(lines) if "| Gate |" in line)
        # Check separator exists
        assert "|------|" in lines[header_idx + 1]
        # Check data rows follow
        assert "| lint |" in lines[header_idx + 2]
