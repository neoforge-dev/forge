"""Tests for coverage threshold gate."""

import json
from unittest.mock import Mock, patch

import pytest

from forge_harness.quality_gates.coverage_gate import (
    CoverageConfig,
    CoverageGate,
    CoverageResult,
    FileCoverage,
    SimpleCoverageGate,
    SimpleCoverageResult,
    create_coverage_gate,
)


class TestCoverageConfig:
    """Tests for CoverageConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = CoverageConfig()
        assert config.threshold == 80.0
        assert config.file_threshold == 70.0
        assert config.source_dir == "forge_harness"
        assert config.test_command == "pytest"

    def test_custom_values(self):
        """Test custom configuration values."""
        config = CoverageConfig(
            threshold=90.0,
            file_threshold=80.0,
            source_dir="my_app",
            test_command="python -m pytest",
        )
        assert config.threshold == 90.0
        assert config.file_threshold == 80.0
        assert config.source_dir == "my_app"


class TestFileCoverage:
    """Tests for FileCoverage dataclass."""

    def test_file_coverage(self):
        """Test file coverage data."""
        fc = FileCoverage(file_path="src/module.py", statements=100, missed=20, coverage_pct=80.0)
        assert fc.file_path == "src/module.py"
        assert fc.statements == 100
        assert fc.missed == 20
        assert fc.coverage_pct == 80.0


class TestSimpleCoverageResult:
    """Tests for SimpleCoverageResult dataclass."""

    def test_passing_result(self):
        """Test passing coverage result."""
        result = SimpleCoverageResult(
            passed=True,
            overall_coverage=85.0,
            threshold=80.0,
            file_threshold=70.0,
            files=[
                FileCoverage("a.py", 50, 5, 90.0),
                FileCoverage("b.py", 100, 20, 80.0),
            ],
        )
        assert result.passed is True
        assert result.overall_coverage == 85.0
        assert len(result.files) == 2
        assert result.error is None

    def test_failing_result(self):
        """Test failing coverage result."""
        failing_file = FileCoverage("low.py", 100, 50, 50.0)
        result = SimpleCoverageResult(
            passed=False,
            overall_coverage=75.0,
            threshold=80.0,
            file_threshold=70.0,
            failing_files=[failing_file],
        )
        assert result.passed is False
        assert result.overall_coverage == 75.0
        assert len(result.failing_files) == 1


class TestSimpleCoverageGate:
    """Tests for SimpleCoverageGate class."""

    @pytest.fixture
    def gate(self, tmp_path):
        """Create a simple coverage gate with temp directory."""
        return SimpleCoverageGate(cwd=tmp_path)

    @patch("subprocess.run")
    def test_run_with_json_report(self, mock_run, gate, tmp_path):
        """Test running coverage with JSON report parsing."""
        # Create mock coverage.json
        coverage_data = {
            "totals": {"percent_covered": 85.5},
            "files": {
                "src/module.py": {
                    "summary": {"percent_covered": 90.0, "num_statements": 100, "missing_lines": 10}
                },
                "src/other.py": {
                    "summary": {"percent_covered": 80.0, "num_statements": 50, "missing_lines": 10}
                },
            },
        }
        (tmp_path / "coverage.json").write_text(json.dumps(coverage_data))

        mock_run.return_value = Mock(stdout="test output", stderr="", returncode=0)

        result = gate.run()

        assert result.passed is True
        assert result.overall_coverage == 85.5
        assert len(result.files) == 2
        assert len(result.failing_files) == 0

    @patch("subprocess.run")
    def test_run_with_failing_files(self, mock_run, gate, tmp_path):
        """Test running coverage with files below threshold."""
        coverage_data = {
            "totals": {"percent_covered": 75.0},
            "files": {
                "src/good.py": {
                    "summary": {"percent_covered": 90.0, "num_statements": 100, "missing_lines": 10}
                },
                "src/bad.py": {
                    "summary": {"percent_covered": 50.0, "num_statements": 100, "missing_lines": 50}
                },
            },
        }
        (tmp_path / "coverage.json").write_text(json.dumps(coverage_data))

        mock_run.return_value = Mock(stdout="", stderr="", returncode=0)

        result = gate.run()

        assert result.passed is False
        assert len(result.failing_files) == 1
        assert result.failing_files[0].file_path == "src/bad.py"

    @patch("subprocess.run")
    def test_run_terminal_fallback(self, mock_run, gate):
        """Test parsing terminal output when JSON not available."""
        mock_run.return_value = Mock(
            stdout="""
Name                    Stmts   Miss  Cover
-------------------------------------------
forge_harness/a.py         50     10    80%
forge_harness/b.py        100     30    70%
-------------------------------------------
TOTAL                     150     40    73%
""",
            stderr="",
            returncode=0,
        )

        result = gate.run()

        assert result.overall_coverage == 73.0
        assert len(result.files) == 2

    @patch("subprocess.run")
    def test_run_timeout(self, mock_run, gate):
        """Test handling of timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired("pytest", 300)

        result = gate.run()

        assert result.passed is False
        assert "timed out" in result.error

    @patch("subprocess.run")
    def test_run_exception(self, mock_run, gate):
        """Test handling of exceptions."""
        mock_run.side_effect = Exception("pytest not found")

        result = gate.run()

        assert result.passed is False
        assert "pytest not found" in result.error

    @patch("subprocess.run")
    def test_check_diff_coverage_no_changes(self, mock_run, gate):
        """Test diff coverage with no Python changes."""
        mock_run.return_value = Mock(stdout="", returncode=0)

        result = gate.check_diff_coverage()

        assert result.passed is True
        assert "No Python files changed" in result.error

    @patch("subprocess.run")
    def test_check_diff_coverage_with_changes(self, mock_run, gate, tmp_path):
        """Test diff coverage with changed files."""
        # First call: git diff
        # Second call: pytest coverage
        coverage_data = {"totals": {"percent_covered": 85.0}, "files": {}}
        (tmp_path / "coverage.json").write_text(json.dumps(coverage_data))

        mock_run.side_effect = [
            Mock(stdout="src/changed.py\n", returncode=0),
            Mock(stdout="", stderr="", returncode=0),
        ]

        result = gate.check_diff_coverage()

        assert result.overall_coverage == 85.0


class TestCreateCoverageGate:
    """Tests for factory function."""

    def test_factory_defaults(self):
        """Test factory with default values."""
        gate = create_coverage_gate()
        assert gate.config.threshold == 80.0
        assert gate.config.file_threshold == 70.0

    def test_factory_custom_values(self):
        """Test factory with custom values."""
        gate = create_coverage_gate(threshold=90.0, file_threshold=80.0)
        assert gate.config.threshold == 90.0
        assert gate.config.file_threshold == 80.0

    def test_factory_with_cwd(self, tmp_path):
        """Test factory with custom working directory."""
        gate = create_coverage_gate(cwd=tmp_path)
        assert gate.cwd == tmp_path


class TestIntegration:
    """Integration tests for coverage gate."""

    @patch("subprocess.run")
    def test_full_workflow(self, mock_run, tmp_path):
        """Test complete coverage checking workflow."""
        # Create coverage.json
        coverage_data = {
            "totals": {"percent_covered": 82.5},
            "files": {
                "forge_harness/module.py": {
                    "summary": {"percent_covered": 85.0, "num_statements": 200, "missing_lines": 30}
                },
                "forge_harness/util.py": {
                    "summary": {"percent_covered": 75.0, "num_statements": 100, "missing_lines": 25}
                },
            },
        }
        (tmp_path / "coverage.json").write_text(json.dumps(coverage_data))

        mock_run.return_value = Mock(stdout="Tests passed", stderr="", returncode=0)

        gate = create_coverage_gate(threshold=80.0, file_threshold=70.0, cwd=tmp_path)
        result = gate.run()

        assert result.passed is True
        assert result.overall_coverage == 82.5
        assert len(result.files) == 2
        assert len(result.failing_files) == 0


"""
Tests for Coverage Gate
========================

Comprehensive test suite for the coverage threshold quality gate.
"""

from unittest.mock import AsyncMock, patch

import pytest

from forge_harness.quality_gates.coverage_gate import (
    CoverageIssue,
    CoverageStats,
    run_coverage_gate,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary git repository structure."""
    repo_root = tmp_path / "test_repo"
    repo_root.mkdir()

    # Create a simple Python module structure
    module_dir = repo_root / "test_module"
    module_dir.mkdir()

    # Create __init__.py
    (module_dir / "__init__.py").write_text("")

    # Create a Python file
    (module_dir / "example.py").write_text(
        """
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b
"""
    )

    # Create a test file
    tests_dir = repo_root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_example.py").write_text(
        """
from test_module.example import add

def test_add():
    assert add(1, 2) == 3
"""
    )

    return repo_root


@pytest.fixture
def mock_git_staged_files():
    """Mock git diff output for staged files."""
    return "test_module/example.py\ntests/test_example.py\n"


@pytest.fixture
def mock_coverage_data_good():
    """Mock coverage data with good coverage."""
    return {
        "totals": {
            "covered_lines": 45,
            "num_statements": 50,
            "percent_covered": 90.0,
            "missing_lines": 5,
            "excluded_lines": 0,
        },
        "files": {
            "test_module/example.py": {
                "summary": {
                    "covered_lines": 9,
                    "num_statements": 10,
                    "percent_covered": 90.0,
                    "missing_lines": 1,
                    "excluded_lines": 0,
                }
            },
            "tests/test_example.py": {
                "summary": {
                    "covered_lines": 5,
                    "num_statements": 5,
                    "percent_covered": 100.0,
                    "missing_lines": 0,
                    "excluded_lines": 0,
                }
            },
        },
    }


@pytest.fixture
def mock_coverage_data_bad():
    """Mock coverage data with poor coverage."""
    return {
        "totals": {
            "covered_lines": 30,
            "num_statements": 50,
            "percent_covered": 60.0,
            "missing_lines": 20,
            "excluded_lines": 0,
        },
        "files": {
            "test_module/example.py": {
                "summary": {
                    "covered_lines": 4,
                    "num_statements": 10,
                    "percent_covered": 40.0,
                    "missing_lines": 6,
                    "excluded_lines": 0,
                }
            },
            "tests/test_example.py": {
                "summary": {
                    "covered_lines": 5,
                    "num_statements": 5,
                    "percent_covered": 100.0,
                    "missing_lines": 0,
                    "excluded_lines": 0,
                }
            },
        },
    }


# =============================================================================
# Model Tests
# =============================================================================


def test_coverage_stats():
    """Test CoverageStats model."""
    stats = CoverageStats(
        name="test_module/example.py",
        statements=100,
        covered=85,
        missing=15,
        excluded=0,
        coverage=85.0,
    )

    assert stats.coverage_percent == 85.0
    assert "test_module/example.py" in str(stats)
    assert "85.0%" in str(stats)


def test_coverage_issue():
    """Test CoverageIssue model."""
    issue = CoverageIssue(
        file="test_module/example.py",
        current_coverage=65.0,
        required_coverage=70.0,
        statements=100,
        covered=65,
        missing=35,
    )

    assert issue.gap == 5.0
    assert "test_module/example.py" in str(issue)
    assert "65.0%" in str(issue)
    assert "70.0%" in str(issue)


def test_coverage_result_passed():
    """Test CoverageResult with passing coverage."""
    result = CoverageResult(
        passed=True,
        overall_coverage=85.0,
        threshold=80.0,
        file_threshold=70.0,
        files_checked=5,
        issues=[],
        stats=[],
        errors=[],
        execution_time=2.5,
    )

    assert result.passed is True
    assert result.issue_count == 0
    assert "PASSED" in result.summary()
    assert "85.0%" in result.summary()


def test_coverage_result_failed():
    """Test CoverageResult with failing coverage."""
    issue = CoverageIssue(
        file="test_module/example.py",
        current_coverage=65.0,
        required_coverage=70.0,
        statements=100,
        covered=65,
        missing=35,
    )

    result = CoverageResult(
        passed=False,
        overall_coverage=65.0,
        threshold=80.0,
        file_threshold=70.0,
        files_checked=5,
        issues=[issue],
        stats=[],
        errors=[],
        execution_time=2.5,
    )

    assert result.passed is False
    assert result.issue_count == 1
    assert "FAILED" in result.summary()
    assert "Coverage Issues" in result.summary()


# =============================================================================
# CoverageGate Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_staged_files(temp_repo, mock_git_staged_files):
    """Test getting staged files from git."""
    gate = CoverageGate(repo_root=temp_repo)

    with patch.object(gate, "_run_command", return_value=mock_git_staged_files) as mock_cmd:
        files = await gate.get_staged_files()

        mock_cmd.assert_called_once()
        assert len(files) == 2
        assert any("example.py" in str(f) for f in files)


@pytest.mark.asyncio
async def test_get_staged_files_empty(temp_repo):
    """Test getting staged files when none exist."""
    gate = CoverageGate(repo_root=temp_repo)

    with patch.object(gate, "_run_command", return_value="") as mock_cmd:
        files = await gate.get_staged_files()

        mock_cmd.assert_called_once()
        assert len(files) == 0


@pytest.mark.asyncio
async def test_get_staged_files_error(temp_repo):
    """Test error handling when getting staged files."""
    gate = CoverageGate(repo_root=temp_repo)

    with patch.object(gate, "_run_command", side_effect=Exception("Git error")) as mock_cmd:
        files = await gate.get_staged_files()

        mock_cmd.assert_called_once()
        assert len(files) == 0


def test_filter_python_files(temp_repo):
    """Test filtering to Python files only."""
    gate = CoverageGate(repo_root=temp_repo)

    files = [
        temp_repo / "test_module" / "example.py",
        temp_repo / "README.md",
        temp_repo / "setup.py",
        temp_repo / "config.json",
    ]

    python_files = gate.filter_python_files(files)

    assert len(python_files) == 2
    assert all(f.suffix == ".py" for f in python_files)


def test_get_module_names(temp_repo):
    """Test converting file paths to module names."""
    gate = CoverageGate(repo_root=temp_repo)

    files = [
        temp_repo / "test_module" / "example.py",
        temp_repo / "test_module" / "utils.py",
    ]

    modules = gate.get_module_names(files)

    assert len(modules) == 2
    assert "test_module.example" in modules
    assert "test_module.utils" in modules


@pytest.mark.asyncio
async def test_run_coverage_success(temp_repo, mock_coverage_data_good):
    """Test running coverage successfully."""
    gate = CoverageGate(repo_root=temp_repo)

    # Create coverage.json file
    coverage_json = temp_repo / "coverage.json"
    coverage_json.write_text(json.dumps(mock_coverage_data_good))

    with patch.object(gate, "_run_command", return_value="OK"):
        file_stats, overall_coverage = await gate.run_coverage(["test_module.example"])

        assert overall_coverage == 90.0
        assert len(file_stats) == 2
        assert "test_module/example.py" in file_stats
        assert file_stats["test_module/example.py"].coverage == 90.0


@pytest.mark.asyncio
async def test_run_coverage_no_modules(temp_repo):
    """Test running coverage with no modules."""
    gate = CoverageGate(repo_root=temp_repo)

    file_stats, overall_coverage = await gate.run_coverage([])

    assert overall_coverage == 0.0
    assert len(file_stats) == 0


@pytest.mark.asyncio
async def test_run_coverage_missing_json(temp_repo):
    """Test running coverage when coverage.json is missing."""
    gate = CoverageGate(repo_root=temp_repo)

    with patch.object(gate, "_run_command", return_value="OK"):
        file_stats, overall_coverage = await gate.run_coverage(["test_module.example"])

        assert overall_coverage == 0.0
        assert len(file_stats) == 0


@pytest.mark.asyncio
async def test_check_coverage_pass(temp_repo):
    """Test checking coverage that passes thresholds."""
    gate = CoverageGate(repo_root=temp_repo)

    file_stats = {
        "test_module/example.py": CoverageStats(
            name="test_module/example.py",
            statements=10,
            covered=9,
            missing=1,
            excluded=0,
            coverage=90.0,
        )
    }

    changed_files = [temp_repo / "test_module" / "example.py"]

    issues = await gate.check_coverage(file_stats, 90.0, changed_files)

    assert len(issues) == 0


@pytest.mark.asyncio
async def test_check_coverage_overall_fail(temp_repo):
    """Test checking coverage when overall threshold fails."""
    gate = CoverageGate(repo_root=temp_repo)

    file_stats = {
        "test_module/example.py": CoverageStats(
            name="test_module/example.py",
            statements=10,
            covered=6,
            missing=4,
            excluded=0,
            coverage=60.0,
        )
    }

    changed_files = [temp_repo / "test_module" / "example.py"]

    issues = await gate.check_coverage(file_stats, 60.0, changed_files)

    # Should have 2 issues: overall and per-file
    assert len(issues) == 2
    assert any(issue.file == "<overall>" for issue in issues)
    assert any(issue.file == "test_module/example.py" for issue in issues)


@pytest.mark.asyncio
async def test_check_coverage_file_fail(temp_repo):
    """Test checking coverage when file threshold fails."""
    gate = CoverageGate(repo_root=temp_repo)

    file_stats = {
        "test_module/example.py": CoverageStats(
            name="test_module/example.py",
            statements=10,
            covered=6,
            missing=4,
            excluded=0,
            coverage=60.0,
        ),
        "test_module/utils.py": CoverageStats(
            name="test_module/utils.py",
            statements=10,
            covered=10,
            missing=0,
            excluded=0,
            coverage=100.0,
        ),
    }

    changed_files = [
        temp_repo / "test_module" / "example.py",
        temp_repo / "test_module" / "utils.py",
    ]

    # Set overall to 80% (passing) but example.py is at 60% (failing)
    issues = await gate.check_coverage(file_stats, 80.0, changed_files)

    assert len(issues) == 1
    assert issues[0].file == "test_module/example.py"
    assert issues[0].current_coverage == 60.0


@pytest.mark.asyncio
async def test_run_no_staged_files(temp_repo):
    """Test running gate with no staged files."""
    gate = CoverageGate(repo_root=temp_repo)

    with patch.object(gate, "get_staged_files", return_value=[]):
        result = await gate.run()

        assert result.passed is True
        assert result.files_checked == 0


@pytest.mark.asyncio
async def test_run_no_python_files(temp_repo):
    """Test running gate with no Python files."""
    gate = CoverageGate(repo_root=temp_repo)

    non_python_files = [temp_repo / "README.md"]

    with patch.object(gate, "get_staged_files", return_value=non_python_files):
        result = await gate.run()

        assert result.passed is True
        assert result.files_checked == 0


@pytest.mark.asyncio
async def test_run_success(temp_repo, mock_coverage_data_good):
    """Test successful coverage gate run."""
    gate = CoverageGate(repo_root=temp_repo)

    python_files = [temp_repo / "test_module" / "example.py"]

    # Create coverage.json
    coverage_json = temp_repo / "coverage.json"
    coverage_json.write_text(json.dumps(mock_coverage_data_good))

    with patch.object(gate, "get_staged_files", return_value=python_files):
        with patch.object(gate, "_run_command", return_value="OK"):
            result = await gate.run()

            assert result.passed is True
            assert result.overall_coverage == 90.0
            assert result.files_checked == 1
            assert len(result.issues) == 0


@pytest.mark.asyncio
async def test_run_failure(temp_repo, mock_coverage_data_bad):
    """Test failed coverage gate run."""
    gate = CoverageGate(repo_root=temp_repo)

    python_files = [temp_repo / "test_module" / "example.py"]

    # Create coverage.json
    coverage_json = temp_repo / "coverage.json"
    coverage_json.write_text(json.dumps(mock_coverage_data_bad))

    with patch.object(gate, "get_staged_files", return_value=python_files):
        with patch.object(gate, "_run_command", return_value="OK"):
            result = await gate.run()

            assert result.passed is False
            assert result.overall_coverage == 60.0
            assert len(result.issues) > 0


@pytest.mark.asyncio
async def test_run_error(temp_repo):
    """Test coverage gate run with error."""
    gate = CoverageGate(repo_root=temp_repo)

    python_files = [temp_repo / "test_module" / "example.py"]

    with patch.object(gate, "get_staged_files", return_value=python_files):
        with patch.object(gate, "run_coverage", side_effect=Exception("Coverage failed")):
            result = await gate.run()

            assert result.passed is False
            assert len(result.errors) > 0


# =============================================================================
# Factory Function Tests
# =============================================================================


@pytest.mark.asyncio
async def test_run_coverage_gate_factory(temp_repo):
    """Test run_coverage_gate factory function."""
    with patch("forge_harness.quality_gates.coverage_gate.CoverageGate") as MockGate:
        mock_gate = MockGate.return_value
        mock_gate.run = AsyncMock(
            return_value=CoverageResult(
                passed=True,
                overall_coverage=85.0,
                threshold=80.0,
                file_threshold=70.0,
                files_checked=5,
            )
        )

        result = await run_coverage_gate(repo_root=temp_repo)

        MockGate.assert_called_once_with(
            repo_root=temp_repo,
            overall_threshold=None,
            file_threshold=None,
        )
        mock_gate.run.assert_called_once()
        assert result.passed is True


@pytest.mark.asyncio
async def test_run_coverage_gate_custom_thresholds(temp_repo):
    """Test run_coverage_gate with custom thresholds."""
    with patch("forge_harness.quality_gates.coverage_gate.CoverageGate") as MockGate:
        mock_gate = MockGate.return_value
        mock_gate.run = AsyncMock(
            return_value=CoverageResult(
                passed=True,
                overall_coverage=85.0,
                threshold=85.0,
                file_threshold=75.0,
                files_checked=5,
            )
        )

        result = await run_coverage_gate(
            repo_root=temp_repo,
            overall_threshold=85.0,
            file_threshold=75.0,
        )

        MockGate.assert_called_once_with(
            repo_root=temp_repo,
            overall_threshold=85.0,
            file_threshold=75.0,
        )
        assert result.passed is True


# =============================================================================
# CLI Tests
# =============================================================================


@pytest.mark.asyncio
async def test_main_async_success():
    """Test main_async with successful run."""
    with patch("sys.argv", ["coverage_gate", "--check"]):
        with patch("forge_harness.quality_gates.coverage_gate.run_coverage_gate") as mock_run:
            mock_run.return_value = CoverageResult(
                passed=True,
                overall_coverage=85.0,
                threshold=80.0,
                file_threshold=70.0,
                files_checked=5,
            )

            from forge_harness.quality_gates.coverage_gate import main_async

            exit_code = await main_async()

            assert exit_code == 0


@pytest.mark.asyncio
async def test_main_async_failure():
    """Test main_async with failed run."""
    with patch("sys.argv", ["coverage_gate", "--check"]):
        with patch("forge_harness.quality_gates.coverage_gate.run_coverage_gate") as mock_run:
            mock_run.return_value = CoverageResult(
                passed=False,
                overall_coverage=65.0,
                threshold=80.0,
                file_threshold=70.0,
                files_checked=5,
                issues=[
                    CoverageIssue(
                        file="test.py",
                        current_coverage=65.0,
                        required_coverage=70.0,
                        statements=100,
                        covered=65,
                        missing=35,
                    )
                ],
            )

            from forge_harness.quality_gates.coverage_gate import main_async

            exit_code = await main_async()

            assert exit_code == 1


# =============================================================================
# Integration Tests
# =============================================================================


@pytest.mark.asyncio
async def test_integration_full_flow(temp_repo):
    """Test full integration flow."""
    gate = CoverageGate(repo_root=temp_repo)

    # Mock staged files
    python_files = [
        temp_repo / "test_module" / "example.py",
        temp_repo / "tests" / "test_example.py",
    ]

    # Mock coverage data
    coverage_data = {
        "totals": {
            "covered_lines": 14,
            "num_statements": 15,
            "percent_covered": 93.3,
            "missing_lines": 1,
            "excluded_lines": 0,
        },
        "files": {
            "test_module/example.py": {
                "summary": {
                    "covered_lines": 9,
                    "num_statements": 10,
                    "percent_covered": 90.0,
                    "missing_lines": 1,
                    "excluded_lines": 0,
                }
            },
            "tests/test_example.py": {
                "summary": {
                    "covered_lines": 5,
                    "num_statements": 5,
                    "percent_covered": 100.0,
                    "missing_lines": 0,
                    "excluded_lines": 0,
                }
            },
        },
    }

    # Create coverage.json
    coverage_json = temp_repo / "coverage.json"
    coverage_json.write_text(json.dumps(coverage_data))

    with patch.object(gate, "get_staged_files", return_value=python_files):
        with patch.object(gate, "_run_command", return_value="OK"):
            result = await gate.run()

            # Should pass with 93.3% overall coverage
            assert result.passed is True
            assert result.overall_coverage == 93.3
            assert result.files_checked == 2
            assert len(result.issues) == 0
            assert len(result.stats) == 2
