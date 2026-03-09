"""
Tests for Lint Gate Module
===========================

Tests linting of staged files with auto-fix support for Python (ruff) and
JavaScript/TypeScript (eslint).
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from forge_harness.quality_gates.lint_gate import (
    LintResult,
    _InternalLintResult,
    _LintGate,
    _LintGateResult,
    lint_staged_files,
)

# =============================================================================
# LintResult Tests
# =============================================================================


class TestLintResult:
    """Tests for LintResult dataclass."""

    def test_result_passed(self):
        """LintResult indicates passed status."""
        result = LintResult(passed=True, errors=[], auto_fixed=0)
        assert result.passed is True
        assert len(result.errors) == 0
        assert result.auto_fixed == 0

    def test_result_failed(self):
        """LintResult indicates failed status with errors."""
        result = LintResult(
            passed=False,
            errors=["file.py:10: E501 Line too long", "file.py:20: F401 Unused import"],
            auto_fixed=0,
        )
        assert result.passed is False
        assert len(result.errors) == 2

    def test_result_with_auto_fix(self):
        """LintResult tracks auto-fixed issues."""
        result = LintResult(passed=True, errors=[], auto_fixed=5)
        assert result.auto_fixed == 5

    def test_summary_passed(self):
        """Summary shows passed status."""
        result = LintResult(passed=True, errors=[], auto_fixed=0)
        summary = result.summary
        assert "PASSED" in summary
        assert "Lint Gate: PASSED" in summary

    def test_summary_failed(self):
        """Summary shows failed status and errors."""
        result = LintResult(
            passed=False,
            errors=["file.py:10: E501 Line too long"],
            auto_fixed=0,
        )
        summary = result.summary
        assert "FAILED" in summary
        assert "Remaining errors (1)" in summary
        assert "file.py:10: E501 Line too long" in summary

    def test_summary_with_auto_fixed(self):
        """Summary shows auto-fixed count."""
        result = LintResult(passed=True, errors=[], auto_fixed=3)
        summary = result.summary
        assert "Auto-fixed 3 issues" in summary

    def test_summary_truncates_many_errors(self):
        """Summary truncates error list when > 10 errors."""
        errors = [f"file.py:{i}: Error {i}" for i in range(15)]
        result = LintResult(passed=False, errors=errors, auto_fixed=0)
        summary = result.summary

        # Should show first 10
        assert "file.py:0: Error 0" in summary
        assert "file.py:9: Error 9" in summary

        # Should not show 11th
        assert "file.py:10: Error 10" not in summary

        # Should indicate truncation
        assert "... and 5 more" in summary

    def test_summary_combined_status(self):
        """Summary shows both passed status and auto-fixed count."""
        result = LintResult(passed=True, errors=[], auto_fixed=2)
        summary = result.summary
        assert "PASSED" in summary
        assert "Auto-fixed 2 issues" in summary


# =============================================================================
# _InternalLintResult Tests
# =============================================================================


class TestInternalLintResult:
    """Tests for _InternalLintResult dataclass."""

    def test_create_minimal_result(self):
        """Create _InternalLintResult with minimal fields."""
        result = _InternalLintResult(
            file_type="Python",
            passed=True,
            files_checked=5,
            fixes_applied=0,
        )
        assert result.file_type == "Python"
        assert result.passed is True
        assert result.files_checked == 5
        assert result.fixes_applied == 0
        assert result.remaining_errors == []
        assert result.output == ""
        assert result.duration_seconds == 0.0

    def test_create_full_result(self):
        """Create _InternalLintResult with all fields."""
        result = _InternalLintResult(
            file_type="JavaScript/TypeScript",
            passed=False,
            files_checked=3,
            fixes_applied=2,
            remaining_errors=["error1", "error2"],
            output="Lint output",
            duration_seconds=1.5,
        )
        assert result.file_type == "JavaScript/TypeScript"
        assert result.passed is False
        assert result.files_checked == 3
        assert result.fixes_applied == 2
        assert len(result.remaining_errors) == 2
        assert result.output == "Lint output"
        assert result.duration_seconds == 1.5


# =============================================================================
# _LintGateResult Tests
# =============================================================================


class TestLintGateResult:
    """Tests for _LintGateResult dataclass."""

    def test_create_minimal_result(self):
        """Create _LintGateResult with minimal fields."""
        result = _LintGateResult(passed=True, results=[], total_duration=0.0)
        assert result.passed is True
        assert len(result.results) == 0
        assert result.total_duration == 0.0

    def test_create_with_results(self):
        """Create _LintGateResult with multiple lint results."""
        py_result = _InternalLintResult(
            file_type="Python", passed=True, files_checked=2, fixes_applied=1
        )
        ts_result = _InternalLintResult(
            file_type="JavaScript/TypeScript", passed=True, files_checked=3, fixes_applied=0
        )

        result = _LintGateResult(
            passed=True,
            results=[py_result, ts_result],
            total_duration=2.5,
        )
        assert result.passed is True
        assert len(result.results) == 2
        assert result.total_duration == 2.5


# =============================================================================
# _LintGate Tests
# =============================================================================


@pytest.fixture
def mock_forge_root(tmp_path):
    """Create a mock FORGE root directory with .git."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    return tmp_path


@pytest.fixture
def mock_harness_root(tmp_path):
    """Create a mock harness directory structure."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    harness_dir = tmp_path / "harness"
    harness_dir.mkdir()
    (harness_dir / "pyproject.toml").write_text("[project]\nname = 'test'")

    cc_dir = harness_dir / "command_center"
    cc_dir.mkdir()
    (cc_dir / "package.json").write_text('{"name": "command_center"}')

    node_modules = cc_dir / "node_modules" / ".bin"
    node_modules.mkdir(parents=True)
    (node_modules / "eslint").touch()
    (node_modules / "eslint").chmod(0o755)

    return tmp_path


class TestLintGateInit:
    """Tests for _LintGate initialization."""

    def test_init_with_explicit_root(self, mock_forge_root):
        """Initialize with explicit forge_root."""
        gate = _LintGate(forge_root=mock_forge_root, auto_fix=True)
        assert gate.forge_root == mock_forge_root
        assert gate.auto_fix is True

    def test_init_defaults_to_cwd(self):
        """Initialize defaults to current working directory."""
        with patch("forge_harness.quality_gates.lint_gate.Path.cwd") as mock_cwd:
            mock_cwd.return_value = Path("/mock/path")
            gate = _LintGate()
            # Should attempt to find .git, but if not found, uses cwd
            assert gate.auto_fix is True

    def test_init_finds_git_root_from_subdir(self, mock_forge_root):
        """Initialize finds git root when started in subdirectory."""
        subdir = mock_forge_root / "subdir" / "nested"
        subdir.mkdir(parents=True)

        gate = _LintGate(forge_root=subdir, auto_fix=False)
        # Should traverse up to find .git
        assert gate.forge_root == mock_forge_root
        assert gate.auto_fix is False

    def test_init_auto_fix_default(self, mock_forge_root):
        """auto_fix defaults to True."""
        gate = _LintGate(forge_root=mock_forge_root)
        assert gate.auto_fix is True


class TestGetStagedFiles:
    """Tests for _LintGate.get_staged_files."""

    def test_get_staged_files_success(self, mock_forge_root):
        """Successfully retrieves staged files from git."""
        gate = _LintGate(forge_root=mock_forge_root)

        mock_result = Mock()
        mock_result.stdout = "file1.py\nfile2.py\nfile3.ts\n"
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            files = gate.get_staged_files()

            mock_run.assert_called_once_with(
                ["git", "diff", "--cached", "--name-only"],
                cwd=mock_forge_root,
                capture_output=True,
                text=True,
                check=True,
            )
            assert files == ["file1.py", "file2.py", "file3.ts"]

    def test_get_staged_files_empty(self, mock_forge_root):
        """Returns empty list when no staged files."""
        gate = _LintGate(forge_root=mock_forge_root)

        mock_result = Mock()
        mock_result.stdout = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            files = gate.get_staged_files()
            assert files == []

    def test_get_staged_files_git_error(self, mock_forge_root):
        """Returns empty list on git error."""
        gate = _LintGate(forge_root=mock_forge_root)

        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            files = gate.get_staged_files()
            assert files == []

    def test_get_staged_files_strips_whitespace(self, mock_forge_root):
        """Strips whitespace from file paths."""
        gate = _LintGate(forge_root=mock_forge_root)

        mock_result = Mock()
        mock_result.stdout = "  file1.py  \n\n  file2.ts  \n  \n"
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            files = gate.get_staged_files()
            assert files == ["file1.py", "file2.ts"]


class TestRestageFiles:
    """Tests for _LintGate.restage_files."""

    def test_restage_files_success(self, mock_forge_root):
        """Successfully re-stages files after fixes."""
        gate = _LintGate(forge_root=mock_forge_root)

        mock_result = Mock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = gate.restage_files(["file1.py", "file2.py"])

            mock_run.assert_called_once_with(
                ["git", "add", "file1.py", "file2.py"],
                cwd=mock_forge_root,
                capture_output=True,
                text=True,
                check=True,
            )
            assert result is True

    def test_restage_files_empty_list(self, mock_forge_root):
        """Returns True for empty file list without calling git."""
        gate = _LintGate(forge_root=mock_forge_root)

        with patch("subprocess.run") as mock_run:
            result = gate.restage_files([])
            mock_run.assert_not_called()
            assert result is True

    def test_restage_files_git_error(self, mock_forge_root):
        """Returns False on git error."""
        gate = _LintGate(forge_root=mock_forge_root)

        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            result = gate.restage_files(["file1.py"])
            assert result is False


class TestLintPython:
    """Tests for _LintGate.lint_python."""

    def test_lint_python_no_files(self, mock_forge_root):
        """Returns passed result when no Python files."""
        gate = _LintGate(forge_root=mock_forge_root)

        result = gate.lint_python(["file.ts", "file.js"])

        assert result.passed is True
        assert result.files_checked == 0
        assert result.fixes_applied == 0
        assert result.file_type == "Python"

    def test_lint_python_all_passed(self, mock_harness_root):
        """All Python files pass linting."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=False)

        # Mock successful ruff check
        check_result = Mock()
        check_result.returncode = 0
        check_result.stdout = ""
        check_result.stderr = ""

        # Mock successful ruff format
        format_result = Mock()
        format_result.returncode = 0
        format_result.stdout = ""
        format_result.stderr = ""

        with patch("subprocess.run", side_effect=[check_result, format_result]):
            result = gate.lint_python(["file1.py", "file2.py"])

        assert result.passed is True
        assert result.files_checked == 2
        assert result.fixes_applied == 0
        assert len(result.remaining_errors) == 0

    def test_lint_python_with_auto_fix(self, mock_harness_root):
        """Auto-fixes Python files and re-stages."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=True)

        # Mock ruff check --fix (with fixes)
        fix_result = Mock()
        fix_result.returncode = 0
        fix_result.stdout = "Fixed 3 errors in file1.py\nFixed 2 errors in file2.py"
        fix_result.stderr = ""

        # Mock git add (restage)
        restage_result = Mock()
        restage_result.returncode = 0

        # Mock ruff check --no-fix (clean)
        check_result = Mock()
        check_result.returncode = 0
        check_result.stdout = ""
        check_result.stderr = ""

        # Mock ruff format --check (clean)
        format_result = Mock()
        format_result.returncode = 0
        format_result.stdout = ""
        format_result.stderr = ""

        with patch(
            "subprocess.run",
            side_effect=[fix_result, restage_result, check_result, format_result],
        ):
            result = gate.lint_python(["file1.py", "file2.py"])

        assert result.passed is True
        assert result.files_checked == 2
        assert result.fixes_applied == 2  # Lines with "Fixed"
        assert len(result.remaining_errors) == 0

    def test_lint_python_remaining_errors(self, mock_harness_root):
        """Reports remaining errors after auto-fix."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=True)

        # Mock ruff check --fix
        fix_result = Mock()
        fix_result.returncode = 0
        fix_result.stdout = ""
        fix_result.stderr = ""

        # Mock ruff check --no-fix (errors)
        check_result = Mock()
        check_result.returncode = 1
        check_result.stdout = "file1.py:10:1: E501 Line too long\nfile1.py:20:1: F401 Unused import"
        check_result.stderr = ""

        # Mock ruff format --check (clean)
        format_result = Mock()
        format_result.returncode = 0
        format_result.stdout = ""
        format_result.stderr = ""

        with patch(
            "subprocess.run",
            side_effect=[fix_result, check_result, format_result],
        ):
            result = gate.lint_python(["file1.py"])

        assert result.passed is False
        assert result.files_checked == 1
        assert len(result.remaining_errors) == 2
        assert "E501 Line too long" in result.remaining_errors[0]

    def test_lint_python_format_issues(self, mock_harness_root):
        """Reports formatting issues."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=False)

        # Mock ruff check (clean)
        check_result = Mock()
        check_result.returncode = 0
        check_result.stdout = ""
        check_result.stderr = ""

        # Mock ruff format --check (issues)
        format_result = Mock()
        format_result.returncode = 1
        format_result.stdout = "Would reformat file1.py\nWould reformat file2.py"
        format_result.stderr = ""

        with patch("subprocess.run", side_effect=[check_result, format_result]):
            result = gate.lint_python(["file1.py", "file2.py"])

        assert result.passed is False
        assert result.files_checked == 2
        assert len(result.remaining_errors) == 2
        assert any("Format:" in err for err in result.remaining_errors)

    def test_lint_python_ruff_not_found(self, mock_harness_root):
        """Handles ruff not found error."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=False)

        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = gate.lint_python(["file1.py"])

        assert result.passed is False
        assert result.files_checked == 1
        assert any("ruff not found" in err for err in result.remaining_errors)

    def test_lint_python_exception(self, mock_harness_root):
        """Handles unexpected exceptions."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=False)

        with patch("subprocess.run", side_effect=Exception("Unexpected error")):
            result = gate.lint_python(["file1.py"])

        assert result.passed is False
        assert result.files_checked == 1
        assert any("Error running ruff" in err for err in result.remaining_errors)

    def test_lint_python_uses_uv_when_pyproject_exists(self, mock_harness_root):
        """Uses 'uv run ruff' when harness/pyproject.toml exists."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=False)

        check_result = Mock()
        check_result.returncode = 0
        check_result.stdout = ""
        check_result.stderr = ""

        format_result = Mock()
        format_result.returncode = 0
        format_result.stdout = ""
        format_result.stderr = ""

        with patch("subprocess.run", side_effect=[check_result, format_result]) as mock_run:
            gate.lint_python(["file1.py"])

            # Check that first call uses uv run ruff
            first_call_args = mock_run.call_args_list[0][0][0]
            assert first_call_args[0:3] == ["uv", "run", "ruff"]


class TestLintTypescript:
    """Tests for _LintGate.lint_typescript."""

    def test_lint_typescript_no_files(self, mock_forge_root):
        """Returns passed result when no JS/TS files."""
        gate = _LintGate(forge_root=mock_forge_root)

        result = gate.lint_typescript(["file.py", "file.md"])

        assert result.passed is True
        assert result.files_checked == 0
        assert result.fixes_applied == 0
        assert result.file_type == "JavaScript/TypeScript"

    def test_lint_typescript_no_command_center_files(self, mock_forge_root):
        """Returns passed result when no files in command_center."""
        gate = _LintGate(forge_root=mock_forge_root)

        # Files that don't contain "command_center" in path
        result = gate.lint_typescript(["other/file.ts", "another/file.tsx"])

        assert result.passed is True
        assert result.files_checked == 0

    def test_lint_typescript_command_center_not_found(self, mock_forge_root):
        """Returns error when command_center directory not found."""
        gate = _LintGate(forge_root=mock_forge_root)

        result = gate.lint_typescript(["harness/command_center/src/App.tsx"])

        assert result.passed is False
        assert "command_center directory not found" in result.remaining_errors[0]

    def test_lint_typescript_eslint_not_found(self, mock_forge_root):
        """Returns error when eslint not installed."""
        # Create command_center but no eslint
        cc_dir = mock_forge_root / "harness" / "command_center"
        cc_dir.mkdir(parents=True)

        gate = _LintGate(forge_root=mock_forge_root)

        result = gate.lint_typescript(["harness/command_center/src/App.tsx"])

        assert result.passed is False
        assert any("eslint not found" in err for err in result.remaining_errors)

    def test_lint_typescript_all_passed(self, mock_harness_root):
        """All TypeScript files pass linting."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=False)

        # Mock successful eslint check
        check_result = Mock()
        check_result.returncode = 0
        check_result.stdout = ""
        check_result.stderr = ""

        with patch("subprocess.run", return_value=check_result):
            result = gate.lint_typescript(["harness/command_center/src/App.tsx"])

        assert result.passed is True
        assert result.files_checked == 1
        assert result.fixes_applied == 0
        assert len(result.remaining_errors) == 0

    def test_lint_typescript_with_auto_fix(self, mock_harness_root):
        """Auto-fixes TypeScript files and re-stages."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=True)

        # Mock eslint --fix (with output indicating fixes)
        fix_result = Mock()
        fix_result.returncode = 0
        fix_result.stdout = "Fixed 2 issues"
        fix_result.stderr = ""

        # Mock git add (restage)
        restage_result = Mock()
        restage_result.returncode = 0

        # Mock eslint check (clean)
        check_result = Mock()
        check_result.returncode = 0
        check_result.stdout = ""
        check_result.stderr = ""

        with patch(
            "subprocess.run",
            side_effect=[fix_result, restage_result, check_result],
        ):
            result = gate.lint_typescript(["harness/command_center/src/App.tsx"])

        assert result.passed is True
        assert result.files_checked == 1
        assert result.fixes_applied == 1  # Assumes fixes for all files

    def test_lint_typescript_remaining_errors(self, mock_harness_root):
        """Reports remaining errors after auto-fix."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=True)

        # Mock eslint --fix
        fix_result = Mock()
        fix_result.returncode = 0
        fix_result.stdout = ""
        fix_result.stderr = ""

        # Mock eslint check (errors)
        check_result = Mock()
        check_result.returncode = 1
        check_result.stdout = (
            "src/App.tsx:10:5: error 'x' is never used\nsrc/App.tsx:20:1: warning Missing semicolon"
        )
        check_result.stderr = ""

        with patch("subprocess.run", side_effect=[fix_result, check_result]):
            result = gate.lint_typescript(["harness/command_center/src/App.tsx"])

        assert result.passed is False
        assert result.files_checked == 1
        assert len(result.remaining_errors) == 2
        assert any("error" in err for err in result.remaining_errors)

    def test_lint_typescript_exception(self, mock_harness_root):
        """Handles unexpected exceptions."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=False)

        with patch("subprocess.run", side_effect=Exception("Unexpected error")):
            result = gate.lint_typescript(["harness/command_center/src/App.tsx"])

        assert result.passed is False
        assert any("Error running eslint" in err for err in result.remaining_errors)

    def test_lint_typescript_filters_by_extension(self, mock_harness_root):
        """Only lints .ts, .tsx, .js, .jsx files."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=False)

        check_result = Mock()
        check_result.returncode = 0
        check_result.stdout = ""
        check_result.stderr = ""

        with patch("subprocess.run", return_value=check_result) as mock_run:
            gate.lint_typescript(
                [
                    "harness/command_center/src/App.tsx",
                    "harness/command_center/src/utils.ts",
                    "harness/command_center/src/index.js",
                    "harness/command_center/README.md",  # Should be filtered out
                ]
            )

            # Should only process .ts/.tsx/.js files
            call_args = mock_run.call_args_list[0][0][0]
            files_arg = [arg for arg in call_args if arg.endswith((".ts", ".tsx", ".js", ".jsx"))]
            assert len(files_arg) == 3


class TestRunAll:
    """Tests for _LintGate.run_all."""

    def test_run_all_no_staged_files(self, mock_forge_root):
        """Returns passed result when no staged files."""
        gate = _LintGate(forge_root=mock_forge_root)

        with patch.object(gate, "get_staged_files", return_value=[]):
            result = gate.run_all()

        assert result.passed is True
        assert len(result.results) == 0
        assert result.total_duration >= 0

    def test_run_all_both_pass(self, mock_harness_root):
        """Runs both Python and TypeScript checks successfully."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=False)

        py_result = _InternalLintResult(
            file_type="Python", passed=True, files_checked=2, fixes_applied=0
        )
        ts_result = _InternalLintResult(
            file_type="JavaScript/TypeScript", passed=True, files_checked=1, fixes_applied=0
        )

        with patch.object(gate, "get_staged_files", return_value=["file.py", "file.ts"]):
            with patch.object(gate, "lint_python", return_value=py_result):
                with patch.object(gate, "lint_typescript", return_value=ts_result):
                    result = gate.run_all()

        assert result.passed is True
        assert len(result.results) == 2

    def test_run_all_one_fails(self, mock_harness_root):
        """Returns failed when one check fails."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=False)

        py_result = _InternalLintResult(
            file_type="Python",
            passed=False,
            files_checked=2,
            fixes_applied=0,
            remaining_errors=["Error in Python"],
        )
        ts_result = _InternalLintResult(
            file_type="JavaScript/TypeScript", passed=True, files_checked=1, fixes_applied=0
        )

        with patch.object(gate, "get_staged_files", return_value=["file.py", "file.ts"]):
            with patch.object(gate, "lint_python", return_value=py_result):
                with patch.object(gate, "lint_typescript", return_value=ts_result):
                    result = gate.run_all()

        assert result.passed is False
        assert len(result.results) == 2

    def test_run_all_python_only(self, mock_harness_root):
        """Only runs Python checks when python_only=True."""
        gate = _LintGate(forge_root=mock_harness_root)

        py_result = _InternalLintResult(
            file_type="Python", passed=True, files_checked=2, fixes_applied=0
        )

        with patch.object(gate, "get_staged_files", return_value=["file.py", "file.ts"]):
            with patch.object(gate, "lint_python", return_value=py_result) as mock_py:
                with patch.object(gate, "lint_typescript") as mock_ts:
                    result = gate.run_all(python_only=True)

                    mock_py.assert_called_once()
                    mock_ts.assert_not_called()

        assert result.passed is True
        assert len(result.results) == 1

    def test_run_all_typescript_only(self, mock_harness_root):
        """Only runs TypeScript checks when typescript_only=True."""
        gate = _LintGate(forge_root=mock_harness_root)

        ts_result = _InternalLintResult(
            file_type="JavaScript/TypeScript", passed=True, files_checked=1, fixes_applied=0
        )

        with patch.object(gate, "get_staged_files", return_value=["file.py", "file.ts"]):
            with patch.object(gate, "lint_python") as mock_py:
                with patch.object(gate, "lint_typescript", return_value=ts_result) as mock_ts:
                    result = gate.run_all(typescript_only=True)

                    mock_py.assert_not_called()
                    mock_ts.assert_called_once()

        assert result.passed is True
        assert len(result.results) == 1


# =============================================================================
# lint_staged_files Factory Function Tests
# =============================================================================


class TestLintStagedFiles:
    """Tests for lint_staged_files factory function."""

    def test_lint_staged_files_success(self, mock_harness_root):
        """Successfully lints staged files."""
        py_result = _InternalLintResult(
            file_type="Python", passed=True, files_checked=2, fixes_applied=3
        )
        ts_result = _InternalLintResult(
            file_type="JavaScript/TypeScript", passed=True, files_checked=1, fixes_applied=1
        )
        gate_result = _LintGateResult(
            passed=True, results=[py_result, ts_result], total_duration=1.0
        )

        with patch("forge_harness.quality_gates.lint_gate._LintGate") as mock_gate_class:
            mock_gate = Mock()
            mock_gate.run_all.return_value = gate_result
            mock_gate_class.return_value = mock_gate

            result = lint_staged_files(forge_root=mock_harness_root, auto_fix=True)

            mock_gate_class.assert_called_once_with(forge_root=mock_harness_root, auto_fix=True)
            mock_gate.run_all.assert_called_once()

        assert result.passed is True
        assert result.auto_fixed == 4  # 3 + 1
        assert len(result.errors) == 0

    def test_lint_staged_files_with_errors(self, mock_harness_root):
        """Aggregates errors from multiple checks."""
        py_result = _InternalLintResult(
            file_type="Python",
            passed=False,
            files_checked=2,
            fixes_applied=1,
            remaining_errors=["Python error 1", "Python error 2"],
        )
        ts_result = _InternalLintResult(
            file_type="JavaScript/TypeScript",
            passed=False,
            files_checked=1,
            fixes_applied=0,
            remaining_errors=["TS error 1"],
        )
        gate_result = _LintGateResult(
            passed=False, results=[py_result, ts_result], total_duration=1.0
        )

        with patch("forge_harness.quality_gates.lint_gate._LintGate") as mock_gate_class:
            mock_gate = Mock()
            mock_gate.run_all.return_value = gate_result
            mock_gate_class.return_value = mock_gate

            result = lint_staged_files(forge_root=mock_harness_root)

        assert result.passed is False
        assert result.auto_fixed == 1
        assert len(result.errors) == 3
        assert "Python error 1" in result.errors
        assert "TS error 1" in result.errors

    def test_lint_staged_files_defaults(self):
        """Uses default parameters when not specified."""
        gate_result = _LintGateResult(passed=True, results=[], total_duration=0.0)

        with patch("forge_harness.quality_gates.lint_gate._LintGate") as mock_gate_class:
            mock_gate = Mock()
            mock_gate.run_all.return_value = gate_result
            mock_gate_class.return_value = mock_gate

            result = lint_staged_files()

            # Should use None for forge_root (finds git root)
            mock_gate_class.assert_called_once_with(forge_root=None, auto_fix=True)

        assert result.passed is True

    def test_lint_staged_files_check_mode(self, mock_harness_root):
        """Runs in check mode (no auto-fix)."""
        gate_result = _LintGateResult(passed=True, results=[], total_duration=0.0)

        with patch("forge_harness.quality_gates.lint_gate._LintGate") as mock_gate_class:
            mock_gate = Mock()
            mock_gate.run_all.return_value = gate_result
            mock_gate_class.return_value = mock_gate

            lint_staged_files(forge_root=mock_harness_root, auto_fix=False)

            mock_gate_class.assert_called_once_with(forge_root=mock_harness_root, auto_fix=False)


# =============================================================================
# Edge Cases and Integration Tests
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_mixed_file_types(self, mock_harness_root):
        """Handles mixed Python and TypeScript files."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=False)

        py_result = _InternalLintResult(
            file_type="Python", passed=True, files_checked=2, fixes_applied=0
        )
        ts_result = _InternalLintResult(
            file_type="JavaScript/TypeScript", passed=True, files_checked=3, fixes_applied=0
        )

        with patch.object(
            gate,
            "get_staged_files",
            return_value=[
                "backend/app/main.py",
                "backend/app/api.py",
                "harness/command_center/src/App.tsx",
                "harness/command_center/src/api.ts",
                "harness/command_center/src/utils.js",
            ],
        ):
            with patch.object(gate, "lint_python", return_value=py_result):
                with patch.object(gate, "lint_typescript", return_value=ts_result):
                    result = gate.run_all()

        assert result.passed is True
        assert len(result.results) == 2

    def test_no_linters_installed(self, mock_forge_root):
        """Handles case when linters are not installed."""
        gate = _LintGate(forge_root=mock_forge_root, auto_fix=False)

        with patch.object(gate, "get_staged_files", return_value=["file.py"]):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                result = gate.run_all()

        assert result.passed is False
        assert any(
            "ruff not found" in err for result in result.results for err in result.remaining_errors
        )

    def test_large_number_of_files(self, mock_harness_root):
        """Handles large number of files efficiently."""
        gate = _LintGate(forge_root=mock_harness_root, auto_fix=False)

        # Generate 100 file paths
        py_files = [f"backend/app/module{i}.py" for i in range(100)]

        py_result = _InternalLintResult(
            file_type="Python", passed=True, files_checked=100, fixes_applied=0
        )

        with patch.object(gate, "get_staged_files", return_value=py_files):
            with patch.object(gate, "lint_python", return_value=py_result):
                with patch.object(
                    gate,
                    "lint_typescript",
                    return_value=_InternalLintResult(
                        file_type="JavaScript/TypeScript",
                        passed=True,
                        files_checked=0,
                        fixes_applied=0,
                    ),
                ):
                    result = gate.run_all()

        assert result.passed is True
        assert result.results[0].files_checked == 100

    def test_concurrent_git_changes(self, mock_forge_root):
        """Handles case where git state changes during linting."""
        gate = _LintGate(forge_root=mock_forge_root)

        # First call returns files, second call returns empty (files unstaged)
        with patch.object(
            gate,
            "get_staged_files",
            side_effect=[
                ["file.py"],
                [],
            ],
        ):
            # First call succeeds
            result1 = gate.run_all()
            # Second call handles empty gracefully
            result2 = gate.run_all()

        assert result2.passed is True
        assert len(result2.results) == 0


"""
Tests for Lint Gate
===================

Tests the lint gate quality gate functionality.
"""

from unittest.mock import AsyncMock

import pytest

from forge_harness.quality_gates.lint_gate import (
    LintGate,
    LintIssue,
    run_lint_gate,
)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

    # Initialize git repo
    import subprocess

    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    return repo_path


@pytest.fixture
def lint_gate(temp_repo: Path) -> LintGate:
    """Create a LintGate instance."""
    return LintGate(repo_root=temp_repo)


class TestLintGate:
    """Test LintGate class."""

    async def test_get_staged_files_empty(self, lint_gate: LintGate) -> None:
        """Test getting staged files when none exist."""
        files = await lint_gate.get_staged_files()
        assert files == []

    async def test_get_staged_files_with_staged(self, lint_gate: LintGate, temp_repo: Path) -> None:
        """Test getting staged files."""
        # Create a test file and stage it
        test_file = temp_repo / "test.py"
        test_file.write_text("print('hello')\n")

        import subprocess

        subprocess.run(["git", "add", "test.py"], cwd=temp_repo, check=True)

        files = await lint_gate.get_staged_files()
        assert len(files) == 1
        assert files[0].name == "test.py"

    def test_categorize_files_python(self, lint_gate: LintGate, temp_repo: Path) -> None:
        """Test categorizing Python files."""
        files = [
            temp_repo / "test.py",
            temp_repo / "main.py",
        ]

        categorized = lint_gate.categorize_files(files)
        assert len(categorized["python"]) == 2
        assert len(categorized["js_ts"]) == 0

    def test_categorize_files_js_ts(self, lint_gate: LintGate, temp_repo: Path) -> None:
        """Test categorizing JS/TS files."""
        files = [
            temp_repo / "app.js",
            temp_repo / "component.tsx",
            temp_repo / "utils.ts",
        ]

        categorized = lint_gate.categorize_files(files)
        assert len(categorized["python"]) == 0
        assert len(categorized["js_ts"]) == 3

    def test_categorize_files_mixed(self, lint_gate: LintGate, temp_repo: Path) -> None:
        """Test categorizing mixed file types."""
        files = [
            temp_repo / "test.py",
            temp_repo / "app.js",
            temp_repo / "component.tsx",
            temp_repo / "README.md",  # Should be ignored
        ]

        categorized = lint_gate.categorize_files(files)
        assert len(categorized["python"]) == 1
        assert len(categorized["js_ts"]) == 2

    @patch("forge_harness.quality_gates.lint_gate.LintGate._run_command")
    async def test_run_ruff_no_issues(
        self, mock_run: AsyncMock, lint_gate: LintGate, temp_repo: Path
    ) -> None:
        """Test running ruff with no issues."""
        mock_run.return_value = "[]"  # Empty JSON array

        files = [temp_repo / "test.py"]
        issues, fixed = await lint_gate.run_ruff(files, fix=False)

        assert issues == []
        assert fixed == []
        mock_run.assert_called_once()

    @patch("forge_harness.quality_gates.lint_gate.LintGate._run_command")
    async def test_run_ruff_with_issues(
        self, mock_run: AsyncMock, lint_gate: LintGate, temp_repo: Path
    ) -> None:
        """Test running ruff with issues found."""
        # Mock ruff JSON output
        ruff_output = [
            {
                "filename": str(temp_repo / "test.py"),
                "location": {"row": 10, "column": 5},
                "code": "F401",
                "message": "Module imported but unused",
                "level": "error",
                "fix": None,
            }
        ]
        mock_run.return_value = str(ruff_output).replace("'", '"').replace("None", "null")

        files = [temp_repo / "test.py"]
        issues, fixed = await lint_gate.run_ruff(files, fix=False)

        assert len(issues) == 1
        assert issues[0].code == "F401"
        assert issues[0].line == 10
        assert issues[0].column == 5
        assert fixed == []

    @patch("forge_harness.quality_gates.lint_gate.LintGate._run_command")
    async def test_run_ruff_with_fix(
        self, mock_run: AsyncMock, lint_gate: LintGate, temp_repo: Path
    ) -> None:
        """Test running ruff with auto-fix."""
        # Mock ruff JSON output with fixable issue
        ruff_output = [
            {
                "filename": str(temp_repo / "test.py"),
                "location": {"row": 10, "column": 5},
                "code": "F401",
                "message": "Module imported but unused",
                "level": "error",
                "fix": {"content": "", "location": {}, "message": "Remove unused import"},
            }
        ]
        mock_run.return_value = str(ruff_output).replace("'", '"')

        files = [temp_repo / "test.py"]
        issues, fixed = await lint_gate.run_ruff(files, fix=True)

        # With fix=True and fixable issue, it should be in fixed list
        assert len(fixed) == 1
        assert fixed[0].code == "F401"
        assert fixed[0].fixable is True
        assert issues == []

    @patch("forge_harness.quality_gates.lint_gate.LintGate._command_exists")
    @patch("forge_harness.quality_gates.lint_gate.LintGate._run_command")
    async def test_run_eslint_not_found(
        self,
        mock_run: AsyncMock,
        mock_exists: AsyncMock,
        lint_gate: LintGate,
        temp_repo: Path,
    ) -> None:
        """Test running eslint when it's not installed."""
        mock_exists.return_value = False

        files = [temp_repo / "app.js"]
        issues, fixed = await lint_gate.run_eslint(files, fix=False)

        assert issues == []
        assert fixed == []
        mock_run.assert_not_called()

    @patch("forge_harness.quality_gates.lint_gate.LintGate._command_exists")
    @patch("forge_harness.quality_gates.lint_gate.LintGate._run_command")
    async def test_run_eslint_with_issues(
        self,
        mock_run: AsyncMock,
        mock_exists: AsyncMock,
        lint_gate: LintGate,
        temp_repo: Path,
    ) -> None:
        """Test running eslint with issues found."""
        mock_exists.return_value = True

        # Mock eslint JSON output - use json.dumps for proper formatting
        import json

        eslint_output = [
            {
                "filePath": str(temp_repo / "app.js"),
                "messages": [
                    {
                        "line": 5,
                        "column": 10,
                        "ruleId": "no-unused-vars",
                        "message": "Variable 'x' is defined but never used",
                        "severity": 2,  # Error
                    }
                ],
                "fixableErrorCount": 0,
                "fixableWarningCount": 0,
            }
        ]
        mock_run.return_value = json.dumps(eslint_output)

        files = [temp_repo / "app.js"]
        issues, fixed = await lint_gate.run_eslint(files, fix=False)

        assert len(issues) == 1
        assert issues[0].code == "no-unused-vars"
        assert issues[0].severity == "error"
        assert fixed == []

    @patch("forge_harness.quality_gates.lint_gate.LintGate.get_staged_files")
    @patch("forge_harness.quality_gates.lint_gate.LintGate.run_ruff")
    async def test_run_gate_no_files(
        self,
        mock_ruff: AsyncMock,
        mock_get_files: AsyncMock,
        lint_gate: LintGate,
    ) -> None:
        """Test running gate with no staged files."""
        mock_get_files.return_value = []

        result = await lint_gate.run(check_only=True)

        assert result.passed is True
        assert result.files_checked == 0
        mock_ruff.assert_not_called()

    @patch("forge_harness.quality_gates.lint_gate.LintGate.get_staged_files")
    @patch("forge_harness.quality_gates.lint_gate.LintGate.run_ruff")
    async def test_run_gate_python_files_pass(
        self,
        mock_ruff: AsyncMock,
        mock_get_files: AsyncMock,
        lint_gate: LintGate,
        temp_repo: Path,
    ) -> None:
        """Test running gate with Python files that pass."""
        mock_get_files.return_value = [temp_repo / "test.py"]
        mock_ruff.return_value = ([], [])  # No issues

        result = await lint_gate.run(check_only=True)

        assert result.passed is True
        assert result.files_checked == 1
        assert result.error_count == 0

    @patch("forge_harness.quality_gates.lint_gate.LintGate.get_staged_files")
    @patch("forge_harness.quality_gates.lint_gate.LintGate.run_ruff")
    async def test_run_gate_python_files_fail(
        self,
        mock_ruff: AsyncMock,
        mock_get_files: AsyncMock,
        lint_gate: LintGate,
        temp_repo: Path,
    ) -> None:
        """Test running gate with Python files that have errors."""
        mock_get_files.return_value = [temp_repo / "test.py"]

        # Mock issue
        issue = LintIssue(
            file="test.py",
            line=10,
            column=5,
            code="F401",
            message="Unused import",
            severity="error",
        )
        mock_ruff.return_value = ([issue], [])

        result = await lint_gate.run(check_only=True)

        assert result.passed is False
        assert result.files_checked == 1
        assert result.error_count == 1


class TestLintResult:
    """Test LintResult class."""

    def test_lint_result_passed(self) -> None:
        """Test LintResult when passed."""
        result = LintResult(passed=True, files_checked=5)
        assert result.passed is True
        assert result.error_count == 0
        assert result.warning_count == 0

    def test_lint_result_with_errors(self) -> None:
        """Test LintResult with errors."""
        issues = [
            LintIssue(
                file="test.py",
                line=10,
                column=5,
                code="F401",
                message="Unused",
                severity="error",
            ),
            LintIssue(
                file="test.py",
                line=20,
                column=1,
                code="W503",
                message="Line break",
                severity="warning",
            ),
        ]
        result = LintResult(passed=False, files_checked=1, issues=issues)

        assert result.passed is False
        assert result.error_count == 1
        assert result.warning_count == 1

    def test_lint_result_summary(self) -> None:
        """Test summary generation."""
        result = LintResult(passed=True, files_checked=3)
        summary = result.summary()

        assert "PASSED" in summary
        assert "Files checked: 3" in summary


class TestLintIssue:
    """Test LintIssue class."""

    def test_lint_issue_str(self) -> None:
        """Test string representation of LintIssue."""
        issue = LintIssue(
            file="test.py",
            line=10,
            column=5,
            code="F401",
            message="Unused import",
            severity="error",
            fixable=True,
        )

        issue_str = str(issue)
        assert "test.py:10:5" in issue_str
        assert "F401" in issue_str
        assert "Unused import" in issue_str
        assert "[fixable]" in issue_str


@pytest.mark.asyncio
async def test_run_lint_gate_factory() -> None:
    """Test run_lint_gate factory function."""
    with patch("forge_harness.quality_gates.lint_gate.LintGate") as mock_gate_class:
        mock_gate = MagicMock()
        mock_gate.run = AsyncMock(return_value=LintResult(passed=True, files_checked=0))
        mock_gate_class.return_value = mock_gate

        result = await run_lint_gate(check_only=True)

        assert result.passed is True
        mock_gate.run.assert_called_once_with(check_only=True, fix=False)
