"""Comprehensive tests for quality gate reporter."""

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import pytest

from forge_harness.quality_gates.reporter import (
    OutputFormat,
    QualityGateReporter,
    ReportConfig,
    create_reporter,
)

# ============================================================================
# Mock Classes (matching actual orchestrator structures)
# ============================================================================


class GateStatus(str, Enum):
    """Mock gate status enum."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class MockGateResult:
    """Mock gate result for testing."""

    gate_name: str
    status: GateStatus
    duration: float = 0.0
    error: str = None


@dataclass
class MockOrchestratorResult:
    """Mock orchestrator result for testing."""

    overall_success: bool
    gate_results: list
    total_duration: float = 0.0
    started_at: datetime = None

    def __post_init__(self):
        if self.started_at is None:
            self.started_at = datetime.now()

    @property
    def passed_gates(self) -> int:
        return sum(1 for g in self.gate_results if g.status == GateStatus.PASSED)

    @property
    def failed_gates(self) -> int:
        return sum(1 for g in self.gate_results if g.status == GateStatus.FAILED)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def passing_result():
    """Create a result with all gates passing."""
    return MockOrchestratorResult(
        overall_success=True,
        gate_results=[
            MockGateResult("lint", GateStatus.PASSED, 1.5),
            MockGateResult("type_check", GateStatus.PASSED, 2.3),
            MockGateResult("security", GateStatus.PASSED, 0.8),
            MockGateResult("tests", GateStatus.PASSED, 5.2),
        ],
        total_duration=9.8,
        started_at=datetime(2026, 1, 29, 10, 30, 0),
    )


@pytest.fixture
def failing_result():
    """Create a result with some gates failing."""
    return MockOrchestratorResult(
        overall_success=False,
        gate_results=[
            MockGateResult("lint", GateStatus.PASSED, 1.2),
            MockGateResult("type_check", GateStatus.FAILED, 1.8, "Type errors found in 3 files"),
            MockGateResult("security", GateStatus.PASSED, 0.9),
            MockGateResult("tests", GateStatus.FAILED, 3.5, "2 tests failed"),
        ],
        total_duration=7.4,
        started_at=datetime(2026, 1, 29, 10, 35, 0),
    )


@pytest.fixture
def empty_result():
    """Create a result with no gates."""
    return MockOrchestratorResult(
        overall_success=True,
        gate_results=[],
        total_duration=0.0,
        started_at=datetime(2026, 1, 29, 11, 0, 0),
    )


@pytest.fixture
def mixed_result():
    """Create a result with passed, failed, and skipped gates."""
    return MockOrchestratorResult(
        overall_success=False,
        gate_results=[
            MockGateResult("lint", GateStatus.PASSED, 1.1),
            MockGateResult("type_check", GateStatus.SKIPPED, 0.0),
            MockGateResult("security", GateStatus.FAILED, 0.7, "High severity issue found"),
            MockGateResult("tests", GateStatus.PASSED, 4.3),
        ],
        total_duration=6.1,
        started_at=datetime(2026, 1, 29, 11, 15, 0),
    )


# ============================================================================
# OutputFormat Tests
# ============================================================================


class TestOutputFormat:
    """Tests for OutputFormat enum."""

    def test_enum_values(self):
        """Test all enum values are defined correctly."""
        assert OutputFormat.TEXT.value == "text"
        assert OutputFormat.JSON.value == "json"
        assert OutputFormat.MARKDOWN.value == "markdown"
        assert OutputFormat.GITHUB.value == "github"

    def test_enum_membership(self):
        """Test checking enum membership."""
        assert "text" in [e.value for e in OutputFormat]
        assert "json" in [e.value for e in OutputFormat]
        assert "markdown" in [e.value for e in OutputFormat]
        assert "github" in [e.value for e in OutputFormat]

    def test_enum_from_string(self):
        """Test creating enum from string values."""
        assert OutputFormat("text") == OutputFormat.TEXT
        assert OutputFormat("json") == OutputFormat.JSON
        assert OutputFormat("markdown") == OutputFormat.MARKDOWN
        assert OutputFormat("github") == OutputFormat.GITHUB


# ============================================================================
# ReportConfig Tests
# ============================================================================


class TestReportConfig:
    """Tests for ReportConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = ReportConfig()
        assert config.format == OutputFormat.TEXT
        assert config.verbose is False
        assert config.include_details is True
        assert config.colorize is True

    def test_custom_values(self):
        """Test creating config with custom values."""
        config = ReportConfig(
            format=OutputFormat.JSON,
            verbose=True,
            include_details=False,
            colorize=False,
        )
        assert config.format == OutputFormat.JSON
        assert config.verbose is True
        assert config.include_details is False
        assert config.colorize is False

    def test_partial_custom_values(self):
        """Test creating config with some custom values."""
        config = ReportConfig(format=OutputFormat.MARKDOWN, verbose=True)
        assert config.format == OutputFormat.MARKDOWN
        assert config.verbose is True
        assert config.include_details is True  # Default
        assert config.colorize is True  # Default


# ============================================================================
# QualityGateReporter Initialization Tests
# ============================================================================


class TestReporterInitialization:
    """Tests for reporter initialization."""

    def test_init_with_no_config(self):
        """Test initializing reporter without config uses defaults."""
        reporter = QualityGateReporter()
        assert reporter.config.format == OutputFormat.TEXT
        assert reporter.config.verbose is False

    def test_init_with_config(self):
        """Test initializing reporter with custom config."""
        config = ReportConfig(format=OutputFormat.JSON, verbose=True)
        reporter = QualityGateReporter(config)
        assert reporter.config.format == OutputFormat.JSON
        assert reporter.config.verbose is True


# ============================================================================
# Format Dispatcher Tests
# ============================================================================


class TestFormatResultDispatcher:
    """Tests for format_result dispatcher method."""

    def test_dispatches_to_text(self, passing_result):
        """Test dispatcher routes to text formatter."""
        config = ReportConfig(format=OutputFormat.TEXT)
        reporter = QualityGateReporter(config)
        result = reporter.format_result(passing_result)
        assert "Quality Gate Results:" in result
        assert "PASSED" in result

    def test_dispatches_to_json(self, passing_result):
        """Test dispatcher routes to JSON formatter."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)
        result = reporter.format_result(passing_result)
        data = json.loads(result)
        assert "success" in data
        assert "gates" in data

    def test_dispatches_to_markdown(self, passing_result):
        """Test dispatcher routes to markdown formatter."""
        config = ReportConfig(format=OutputFormat.MARKDOWN)
        reporter = QualityGateReporter(config)
        result = reporter.format_result(passing_result)
        assert "# Quality Gate Report" in result
        assert "| Gate | Status | Duration |" in result

    def test_dispatches_to_github(self, passing_result):
        """Test dispatcher routes to GitHub formatter."""
        config = ReportConfig(format=OutputFormat.GITHUB)
        reporter = QualityGateReporter(config)
        result = reporter.format_result(passing_result)
        assert "::notice::" in result


# ============================================================================
# Text Format Tests
# ============================================================================


class TestTextFormat:
    """Tests for text format output."""

    def test_passing_result_structure(self, passing_result):
        """Test text format structure for passing result."""
        reporter = QualityGateReporter(ReportConfig(format=OutputFormat.TEXT))
        output = reporter.format_result(passing_result)

        assert "Quality Gate Results: PASSED" in output
        assert "=" * 40 in output
        assert "-" * 40 in output
        assert "Total: 4/4 passed" in output
        assert "Duration: 9.80s" in output

    def test_failing_result_structure(self, failing_result):
        """Test text format structure for failing result."""
        reporter = QualityGateReporter(ReportConfig(format=OutputFormat.TEXT))
        output = reporter.format_result(failing_result)

        assert "Quality Gate Results: FAILED" in output
        assert "Total: 2/4 passed" in output

    def test_gate_status_icons(self, passing_result):
        """Test gate status icons in text format."""
        reporter = QualityGateReporter(ReportConfig(format=OutputFormat.TEXT))
        output = reporter.format_result(passing_result)

        assert "[+]" in output
        for gate in passing_result.gate_results:
            assert f"[+] {gate.gate_name}: passed" in output

    def test_failed_gate_icons(self, failing_result):
        """Test failed gate icons in text format."""
        reporter = QualityGateReporter(ReportConfig(format=OutputFormat.TEXT))
        output = reporter.format_result(failing_result)

        assert "[-]" in output
        assert "[-] type_check: failed" in output
        assert "[-] tests: failed" in output

    def test_duration_formatting(self, passing_result):
        """Test duration formatting with 2 decimal places."""
        reporter = QualityGateReporter(ReportConfig(format=OutputFormat.TEXT))
        output = reporter.format_result(passing_result)

        assert "(1.50s)" in output  # lint
        assert "(2.30s)" in output  # type_check
        assert "(0.80s)" in output  # security
        assert "(5.20s)" in output  # tests

    def test_verbose_mode_shows_errors(self, failing_result):
        """Test verbose mode includes error messages."""
        config = ReportConfig(format=OutputFormat.TEXT, verbose=True)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)

        assert "Error: Type errors found in 3 files" in output
        assert "Error: 2 tests failed" in output

    def test_non_verbose_mode_hides_errors(self, failing_result):
        """Test non-verbose mode hides error messages."""
        config = ReportConfig(format=OutputFormat.TEXT, verbose=False)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)

        assert "Error: Type errors found in 3 files" not in output
        assert "Error: 2 tests failed" not in output

    def test_empty_result(self, empty_result):
        """Test text format handles empty results."""
        reporter = QualityGateReporter(ReportConfig(format=OutputFormat.TEXT))
        output = reporter.format_result(empty_result)

        assert "Quality Gate Results: PASSED" in output
        assert "Total: 0/0 passed" in output


# ============================================================================
# JSON Format Tests
# ============================================================================


class TestJsonFormat:
    """Tests for JSON format output."""

    def test_valid_json_output(self, passing_result):
        """Test JSON output is valid and parseable."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)

        data = json.loads(output)
        assert isinstance(data, dict)

    def test_json_structure_keys(self, passing_result):
        """Test JSON output has expected top-level keys."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)
        data = json.loads(output)

        assert "success" in data
        assert "gates" in data
        assert "summary" in data
        assert "timestamp" in data

    def test_json_success_field(self, passing_result, failing_result):
        """Test JSON success field reflects overall status."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)

        passing_data = json.loads(reporter.format_result(passing_result))
        assert passing_data["success"] is True

        failing_data = json.loads(reporter.format_result(failing_result))
        assert failing_data["success"] is False

    def test_json_gates_array(self, passing_result):
        """Test JSON gates array structure."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)
        data = json.loads(output)

        gates = data["gates"]
        assert isinstance(gates, list)
        assert len(gates) == 4

        for gate in gates:
            assert "name" in gate
            assert "status" in gate
            assert "duration" in gate
            assert "error" in gate

    def test_json_gate_values(self, passing_result):
        """Test JSON gate values are correct."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)
        data = json.loads(output)

        lint_gate = data["gates"][0]
        assert lint_gate["name"] == "lint"
        assert lint_gate["status"] == "passed"
        assert lint_gate["duration"] == 1.5
        assert lint_gate["error"] is None

    def test_json_error_field(self, failing_result):
        """Test JSON includes error messages for failed gates."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)
        data = json.loads(output)

        type_check_gate = data["gates"][1]
        assert type_check_gate["error"] == "Type errors found in 3 files"

        tests_gate = data["gates"][3]
        assert tests_gate["error"] == "2 tests failed"

    def test_json_summary_structure(self, passing_result):
        """Test JSON summary structure."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)
        data = json.loads(output)

        summary = data["summary"]
        assert "passed" in summary
        assert "failed" in summary
        assert "total" in summary
        assert "duration" in summary

    def test_json_summary_values(self, failing_result):
        """Test JSON summary values are correct."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)
        data = json.loads(output)

        summary = data["summary"]
        assert summary["passed"] == 2
        assert summary["failed"] == 2
        assert summary["total"] == 4
        assert summary["duration"] == 7.4

    def test_json_timestamp_format(self, passing_result):
        """Test JSON timestamp is ISO format."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)
        data = json.loads(output)

        timestamp = data["timestamp"]
        assert timestamp is not None
        assert "2026-01-29" in timestamp
        # Verify it's parseable as ISO format
        datetime.fromisoformat(timestamp)

    def test_json_empty_result(self, empty_result):
        """Test JSON format handles empty results."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(empty_result)
        data = json.loads(output)

        assert data["success"] is True
        assert len(data["gates"]) == 0
        assert data["summary"]["total"] == 0


# ============================================================================
# Markdown Format Tests
# ============================================================================


class TestMarkdownFormat:
    """Tests for Markdown format output."""

    def test_markdown_structure(self, passing_result):
        """Test markdown has proper structure."""
        config = ReportConfig(format=OutputFormat.MARKDOWN)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)

        assert "# Quality Gate Report" in output
        assert "| Gate | Status | Duration |" in output
        assert "|------|--------|----------|" in output

    def test_markdown_passing_status_icon(self, passing_result):
        """Test markdown uses check mark for passing gates."""
        config = ReportConfig(format=OutputFormat.MARKDOWN)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)

        assert ":white_check_mark:" in output

    def test_markdown_failing_status_icon(self, failing_result):
        """Test markdown uses X for failing gates."""
        config = ReportConfig(format=OutputFormat.MARKDOWN)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)

        assert ":x:" in output

    def test_markdown_header_with_status(self, passing_result, failing_result):
        """Test markdown header includes status icon."""
        config = ReportConfig(format=OutputFormat.MARKDOWN)
        reporter = QualityGateReporter(config)

        passing_output = reporter.format_result(passing_result)
        assert "# Quality Gate Report :white_check_mark:" in passing_output

        failing_output = reporter.format_result(failing_result)
        assert "# Quality Gate Report :x:" in failing_output

    def test_markdown_table_rows(self, passing_result):
        """Test markdown table has correct number of rows."""
        config = ReportConfig(format=OutputFormat.MARKDOWN)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)

        # Count table rows (excluding header and separator)
        table_rows = [
            line
            for line in output.split("\n")
            if line.startswith("| ") and "Gate" not in line and "---" not in line
        ]
        assert len(table_rows) == 4

    def test_markdown_gate_details(self, passing_result):
        """Test markdown table includes gate details."""
        config = ReportConfig(format=OutputFormat.MARKDOWN)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)

        assert "| lint |" in output
        assert "| type_check |" in output
        assert "| security |" in output
        assert "| tests |" in output
        assert "| 1.50s |" in output
        assert "| 2.30s |" in output

    def test_markdown_summary(self, failing_result):
        """Test markdown includes summary section."""
        config = ReportConfig(format=OutputFormat.MARKDOWN)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)

        assert "**Summary:** 2/4 gates passed" in output
        assert "**Total Duration:** 7.40s" in output

    def test_markdown_empty_result(self, empty_result):
        """Test markdown format handles empty results."""
        config = ReportConfig(format=OutputFormat.MARKDOWN)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(empty_result)

        assert "# Quality Gate Report" in output
        assert "**Summary:** 0/0 gates passed" in output


# ============================================================================
# GitHub Actions Format Tests
# ============================================================================


class TestGitHubFormat:
    """Tests for GitHub Actions annotation format."""

    def test_github_annotation_syntax(self, passing_result):
        """Test GitHub format uses correct annotation syntax."""
        config = ReportConfig(format=OutputFormat.GITHUB)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)

        assert "::notice::" in output

    def test_github_passing_gates_notices(self, passing_result):
        """Test passing gates generate notice annotations."""
        config = ReportConfig(format=OutputFormat.GITHUB)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)

        assert "::notice::Quality gate 'lint' passed" in output
        assert "::notice::Quality gate 'type_check' passed" in output
        assert "::notice::Quality gate 'security' passed" in output
        assert "::notice::Quality gate 'tests' passed" in output

    def test_github_failing_gates_errors(self, failing_result):
        """Test failing gates generate error annotations."""
        config = ReportConfig(format=OutputFormat.GITHUB)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)

        assert "::error::Quality gate 'type_check' failed" in output
        assert "::error::Quality gate 'tests' failed" in output

    def test_github_error_messages_included(self, failing_result):
        """Test error messages are included in annotations."""
        config = ReportConfig(format=OutputFormat.GITHUB)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)

        assert "Type errors found in 3 files" in output
        assert "2 tests failed" in output

    def test_github_overall_success_message(self, passing_result):
        """Test overall success generates notice."""
        config = ReportConfig(format=OutputFormat.GITHUB)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(passing_result)

        assert "::notice::All quality gates passed!" in output

    def test_github_overall_failure_message(self, failing_result):
        """Test overall failure generates error."""
        config = ReportConfig(format=OutputFormat.GITHUB)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(failing_result)

        assert "::error::2 quality gate(s) failed" in output

    def test_github_mixed_statuses(self, mixed_result):
        """Test GitHub format handles mixed gate statuses."""
        config = ReportConfig(format=OutputFormat.GITHUB)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(mixed_result)

        # Passed gates
        assert "::notice::Quality gate 'lint' passed" in output
        assert "::notice::Quality gate 'tests' passed" in output

        # Failed gate
        assert "::error::Quality gate 'security' failed" in output

        # Skipped gate (shows as notice since it didn't fail)
        assert "::notice::Quality gate 'type_check' passed" in output


# ============================================================================
# Edge Cases Tests
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_all_gates_passed(self, passing_result):
        """Test handling when all gates pass."""
        reporter = QualityGateReporter()
        output = reporter.format_result(passing_result)

        assert "4/4 passed" in output
        assert "PASSED" in output

    def test_all_gates_failed(self):
        """Test handling when all gates fail."""
        result = MockOrchestratorResult(
            overall_success=False,
            gate_results=[
                MockGateResult("lint", GateStatus.FAILED, 1.0, "Lint errors"),
                MockGateResult("type_check", GateStatus.FAILED, 1.0, "Type errors"),
            ],
            total_duration=2.0,
        )
        reporter = QualityGateReporter()
        output = reporter.format_result(result)

        assert "0/2 passed" in output
        assert "FAILED" in output

    def test_zero_duration_gate(self):
        """Test handling gates with zero duration."""
        result = MockOrchestratorResult(
            overall_success=True,
            gate_results=[
                MockGateResult("fast_gate", GateStatus.PASSED, 0.0),
            ],
            total_duration=0.0,
        )
        reporter = QualityGateReporter()
        output = reporter.format_result(result)

        assert "(0.00s)" in output

    def test_long_gate_name(self):
        """Test handling gates with long names."""
        result = MockOrchestratorResult(
            overall_success=True,
            gate_results=[
                MockGateResult("very_long_gate_name_for_testing", GateStatus.PASSED, 1.0),
            ],
            total_duration=1.0,
        )
        reporter = QualityGateReporter()
        output = reporter.format_result(result)

        assert "very_long_gate_name_for_testing" in output

    def test_special_characters_in_error(self):
        """Test handling special characters in error messages."""
        result = MockOrchestratorResult(
            overall_success=False,
            gate_results=[
                MockGateResult(
                    "test", GateStatus.FAILED, 1.0, "Error: <special> & 'characters' \"here\""
                ),
            ],
            total_duration=1.0,
        )
        config = ReportConfig(verbose=True)
        reporter = QualityGateReporter(config)
        output = reporter.format_result(result)

        assert "special" in output


# ============================================================================
# Additional Format Method Tests
# ============================================================================


class TestAdditionalMethods:
    """Tests for print_result and write_result methods."""

    def test_print_result(self, passing_result, capsys):
        """Test print_result outputs to stdout."""
        reporter = QualityGateReporter()
        reporter.print_result(passing_result)

        captured = capsys.readouterr()
        assert "Quality Gate Results:" in captured.out

    def test_write_result(self, passing_result, tmp_path):
        """Test write_result saves to file."""
        reporter = QualityGateReporter()
        output_file = tmp_path / "report.txt"

        reporter.write_result(passing_result, str(output_file))

        assert output_file.exists()
        content = output_file.read_text()
        assert "Quality Gate Results:" in content

    def test_write_result_json(self, passing_result, tmp_path):
        """Test write_result with JSON format."""
        config = ReportConfig(format=OutputFormat.JSON)
        reporter = QualityGateReporter(config)
        output_file = tmp_path / "report.json"

        reporter.write_result(passing_result, str(output_file))

        assert output_file.exists()
        content = output_file.read_text()
        data = json.loads(content)
        assert "success" in data


# ============================================================================
# Factory Function Tests
# ============================================================================


class TestCreateReporter:
    """Tests for create_reporter factory function."""

    def test_create_reporter_no_config(self):
        """Test factory creates reporter with default config."""
        reporter = create_reporter()
        assert isinstance(reporter, QualityGateReporter)
        assert reporter.config.format == OutputFormat.TEXT

    def test_create_reporter_with_config(self):
        """Test factory creates reporter with custom config."""
        config = ReportConfig(format=OutputFormat.JSON, verbose=True)
        reporter = create_reporter(config)
        assert isinstance(reporter, QualityGateReporter)
        assert reporter.config.format == OutputFormat.JSON
        assert reporter.config.verbose is True

    def test_factory_returns_functional_reporter(self, passing_result):
        """Test factory returns a functional reporter."""
        reporter = create_reporter()
        output = reporter.format_result(passing_result)
        assert "Quality Gate Results:" in output
