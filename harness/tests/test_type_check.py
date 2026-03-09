"""Tests for type_check.py - Type checking quality gate.

Tests cover:
- TypeCheckError and TypeCheckResult models
- TypeCheckGate class methods
- run_type_check function
- Error parsing for mypy and tsc
- Cache functionality
- Config handling
- Exception handling paths
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.quality_gates.type_check import (
    TypeCheckError,
    TypeCheckGate,
    TypeCheckResult,
    run_type_check_gate,
)

# =============================================================================
# TypeCheckError Tests
# =============================================================================


class TestTypeCheckError:
    """Tests for TypeCheckError dataclass."""

    def test_str_format_with_all_fields(self) -> None:
        """Should format error with all fields present."""
        error = TypeCheckError(
            file="src/main.py",
            line=42,
            column=5,
            code="arg-type",
            message="Argument has incompatible type",
            severity="error",
        )

        result = str(error)

        assert "src/main.py:42:5" in result
        assert "arg-type" in result
        assert "Argument has incompatible type" in result

    def test_str_format_without_column(self) -> None:
        """Should format error without column."""
        error = TypeCheckError(
            file="src/main.py",
            line=42,
            column=None,
            code="import-error",
            message="Cannot find module",
        )

        result = str(error)

        assert "src/main.py:42" in result
        assert ":5" not in result  # No column

    def test_str_format_without_line(self) -> None:
        """Should format error without line number."""
        error = TypeCheckError(
            file="src/main.py",
            line=None,
            column=None,
            code="misc",
            message="General error",
        )

        result = str(error)

        assert "src/main.py" in result
        assert ":42" not in result  # No line

    def test_default_severity(self) -> None:
        """Should default severity to 'error'."""
        error = TypeCheckError(
            file="test.py",
            line=1,
            column=1,
            code="test",
            message="Test message",
        )

        assert error.severity == "error"


# =============================================================================
# TypeCheckResult Tests
# =============================================================================


class TestTypeCheckResult:
    """Tests for TypeCheckResult dataclass."""

    def test_error_count(self) -> None:
        """Should count only error-level issues."""
        result = TypeCheckResult(
            passed=False,
            files_checked=3,
            errors=[
                TypeCheckError("a.py", 1, 1, "E1", "Error 1", "error"),
                TypeCheckError("a.py", 2, 1, "E2", "Error 2", "error"),
                TypeCheckError("b.py", 1, 1, "W1", "Warning 1", "warning"),
                TypeCheckError("b.py", 2, 1, "N1", "Note 1", "note"),
            ],
        )

        assert result.error_count == 2
        assert result.warning_count == 1

    def test_summary_passed(self) -> None:
        """Summary should indicate passed status."""
        result = TypeCheckResult(
            passed=True,
            files_checked=5,
            errors=[],
        )

        summary = result.summary()

        assert "PASSED" in summary
        assert "Files checked: 5" in summary

    def test_summary_failed_with_errors(self) -> None:
        """Summary should show errors when failed."""
        result = TypeCheckResult(
            passed=False,
            files_checked=3,
            errors=[
                TypeCheckError("test.py", 10, 5, "type-error", "Type mismatch", "error"),
            ],
            execution_errors=[],
        )

        summary = result.summary()

        assert "FAILED" in summary
        assert "Errors: 1" in summary
        assert "test.py:10:5" in summary

    def test_summary_with_cached_files(self) -> None:
        """Summary should show cached files count."""
        result = TypeCheckResult(
            passed=True,
            files_checked=10,
            cached_files=5,
            errors=[],
        )

        summary = result.summary()

        assert "Files cached: 5" in summary

    def test_summary_with_execution_errors(self) -> None:
        """Summary should show execution errors."""
        result = TypeCheckResult(
            passed=False,
            files_checked=0,
            errors=[],
            execution_errors=["mypy not found", "tsc execution failed"],
        )

        summary = result.summary()

        assert "Errors during execution:" in summary
        assert "mypy not found" in summary


# =============================================================================
# TypeCheckGate Initialization Tests
# =============================================================================


class TestTypeCheckGateInit:
    """Tests for TypeCheckGate initialization."""

    def test_default_initialization(self, tmp_path: Path) -> None:
        """Should initialize with default values."""
        gate = TypeCheckGate(repo_root=tmp_path)

        assert gate.repo_root == tmp_path
        assert gate.use_cache is True
        assert gate.cache_dir == tmp_path / ".forge_cache" / "type_check"
        assert gate.cache_dir.exists()  # Directory created

    def test_custom_cache_dir(self, tmp_path: Path) -> None:
        """Should accept custom cache directory."""
        custom_cache = tmp_path / "custom_cache"
        gate = TypeCheckGate(
            repo_root=tmp_path,
            cache_dir=custom_cache,
        )

        assert gate.cache_dir == custom_cache
        assert custom_cache.exists()

    def test_cache_disabled(self, tmp_path: Path) -> None:
        """Should not create cache directory when disabled."""
        gate = TypeCheckGate(
            repo_root=tmp_path,
            use_cache=False,
        )

        assert gate.use_cache is False
        # Cache dir attribute set but not created
        assert not gate.cache_dir.exists()


# =============================================================================
# File Categorization Tests
# =============================================================================


class TestCategorizeFiles:
    """Tests for file categorization."""

    def test_categorize_python_files(self, tmp_path: Path) -> None:
        """Should categorize Python files correctly."""
        gate = TypeCheckGate(repo_root=tmp_path)
        files = [
            tmp_path / "main.py",
            tmp_path / "utils.py",
            tmp_path / "test.py",
        ]

        categorized = gate.categorize_files(files)

        assert len(categorized["python"]) == 3
        assert len(categorized["typescript"]) == 0

    def test_categorize_typescript_files(self, tmp_path: Path) -> None:
        """Should categorize TypeScript files correctly."""
        gate = TypeCheckGate(repo_root=tmp_path)
        files = [
            tmp_path / "main.ts",
            tmp_path / "component.tsx",
        ]

        categorized = gate.categorize_files(files)

        assert len(categorized["python"]) == 0
        assert len(categorized["typescript"]) == 2

    def test_categorize_mixed_files(self, tmp_path: Path) -> None:
        """Should categorize mixed file types."""
        gate = TypeCheckGate(repo_root=tmp_path)
        files = [
            tmp_path / "main.py",
            tmp_path / "app.ts",
            tmp_path / "utils.py",
            tmp_path / "component.tsx",
        ]

        categorized = gate.categorize_files(files)

        assert len(categorized["python"]) == 2
        assert len(categorized["typescript"]) == 2

    def test_categorize_ignores_other_files(self, tmp_path: Path) -> None:
        """Should ignore non-Python/TypeScript files."""
        gate = TypeCheckGate(repo_root=tmp_path)
        files = [
            tmp_path / "main.py",
            tmp_path / "README.md",
            tmp_path / "config.json",
            tmp_path / "styles.css",
        ]

        categorized = gate.categorize_files(files)

        assert len(categorized["python"]) == 1
        assert len(categorized["typescript"]) == 0


# =============================================================================
# Cache Tests
# =============================================================================


class TestCacheFunctionality:
    """Tests for caching functionality."""

    def test_get_file_hash(self, tmp_path: Path) -> None:
        """Should compute consistent file hash."""
        gate = TypeCheckGate(repo_root=tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")

        hash1 = gate._get_file_hash(test_file)
        hash2 = gate._get_file_hash(test_file)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex length

    def test_get_file_hash_nonexistent(self, tmp_path: Path) -> None:
        """Should return empty string for non-existent file."""
        gate = TypeCheckGate(repo_root=tmp_path)

        result = gate._get_file_hash(tmp_path / "nonexistent.py")

        assert result == ""

    def test_cache_key_generation(self, tmp_path: Path) -> None:
        """Should generate cache key from relative path and hash."""
        gate = TypeCheckGate(repo_root=tmp_path)
        test_file = tmp_path / "src" / "main.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("x = 1")

        cache_key = gate._get_cache_key(test_file)

        assert "src/main.py" in cache_key or "src\\main.py" in cache_key

    def test_cache_key_outside_repo(self, tmp_path: Path) -> None:
        """Should return empty cache key for files outside repo."""
        gate = TypeCheckGate(repo_root=tmp_path)
        outside_file = tmp_path.parent / "outside.py"
        outside_file.write_text("x = 1")

        cache_key = gate._get_cache_key(outside_file)

        assert cache_key == ""

    def test_is_cached_with_cache_disabled(self, tmp_path: Path) -> None:
        """Should return False when cache is disabled."""
        gate = TypeCheckGate(repo_root=tmp_path, use_cache=False)
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")

        assert gate._is_cached(test_file) is False

    def test_cache_roundtrip(self, tmp_path: Path) -> None:
        """Should cache and retrieve errors correctly."""
        gate = TypeCheckGate(repo_root=tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")

        errors = [
            TypeCheckError("test.py", 1, 1, "E1", "Error 1", "error"),
            TypeCheckError("test.py", 2, 1, "E2", "Error 2", "error"),
        ]

        # Cache the errors
        gate._cache_result(test_file, errors)

        # Retrieve from cache
        cached = gate._get_cached_errors(test_file)

        assert cached is not None
        assert len(cached) == 2
        assert cached[0].code == "E1"
        assert cached[1].code == "E2"

    def test_is_cached_true(self, tmp_path: Path) -> None:
        """Should return True when file is cached."""
        gate = TypeCheckGate(repo_root=tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")

        # Cache empty errors (passed check)
        gate._cache_result(test_file, [])

        assert gate._is_cached(test_file) is True

    def test_get_cached_errors_not_cached(self, tmp_path: Path) -> None:
        """Should return None for non-cached file."""
        gate = TypeCheckGate(repo_root=tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")

        cached = gate._get_cached_errors(test_file)

        assert cached is None

    def test_cache_result_when_disabled(self, tmp_path: Path) -> None:
        """Should not cache when disabled."""
        gate = TypeCheckGate(repo_root=tmp_path, use_cache=False)
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")

        # Should not raise
        gate._cache_result(test_file, [])

        assert gate._is_cached(test_file) is False

    def test_cache_key_with_empty_hash(self, tmp_path: Path) -> None:
        """Should handle cache key generation when file can't be hashed."""
        gate = TypeCheckGate(repo_root=tmp_path)

        # Test by mocking _get_file_hash to return empty
        with patch.object(gate, "_get_file_hash", return_value=""):
            test_file = tmp_path / "test.py"
            test_file.write_text("x = 1")

            cache_key = gate._get_cache_key(test_file)
            # Cache key format is {rel_path}_{file_hash}, so it's "test.py_" when hash is empty
            assert cache_key == "test.py_"

    def test_cache_result_with_empty_key(self, tmp_path: Path) -> None:
        """Should not cache when cache key is empty."""
        gate = TypeCheckGate(repo_root=tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")

        with patch.object(gate, "_get_cache_key", return_value=""):
            gate._cache_result(test_file, [TypeCheckError("test.py", 1, 1, "E1", "Error")])

            assert gate._is_cached(test_file) is False

    def test_get_cached_errors_corrupted_cache(self, tmp_path: Path) -> None:
        """Should handle corrupted cache files gracefully."""
        gate = TypeCheckGate(repo_root=tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")

        # Create a corrupted cache file
        cache_key = gate._get_cache_key(test_file)
        cache_file = gate.cache_dir / f"{cache_key}.json"
        cache_file.write_text("not valid json")

        cached = gate._get_cached_errors(test_file)
        assert cached is None

    def test_cache_write_error(self, tmp_path: Path) -> None:
        """Should handle cache write errors gracefully."""
        gate = TypeCheckGate(repo_root=tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")

        # Mock write_text to raise an error
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            # Should not raise
            gate._cache_result(test_file, [TypeCheckError("test.py", 1, 1, "E1", "Error")])


# =============================================================================
# Mypy Output Parsing Tests
# =============================================================================


class TestMypyParsing:
    """Tests for mypy output parsing."""

    def test_parse_mypy_error_format(self, tmp_path: Path) -> None:
        """Should parse standard mypy error format using the same regex as the gate."""
        import re

        mypy_output = """\
main.py:1:1: error: Incompatible types in assignment (expression has type "str", variable has type "int") [assignment]
main.py:5:10: error: Argument 1 to "func" has incompatible type "int"; expected "str" [arg-type]
main.py:10:1: warning: Unused import [unused-import]
main.py:20:5: note: See https://mypy.readthedocs.io [note]
"""

        # Parse using the same regex pattern as used in type_check.py
        pattern = re.compile(
            r"^(.+?):(\d+):(\d+):\s+(error|warning|note):\s+(.+?)(?:\s+\[(.+?)\])?$",
            re.MULTILINE,
        )

        errors = []
        for match in pattern.finditer(mypy_output):
            error = TypeCheckError(
                file=match.group(1),
                line=int(match.group(2)),
                column=int(match.group(3)),
                code=match.group(6) or "type-error",
                message=match.group(5),
                severity=match.group(4),
            )
            errors.append(error)

        assert len(errors) == 4
        assert errors[0].severity == "error"
        assert errors[0].code == "assignment"
        assert errors[0].line == 1
        assert errors[0].column == 1
        assert errors[1].severity == "error"
        assert errors[1].code == "arg-type"
        assert errors[2].severity == "warning"
        assert errors[2].code == "unused-import"
        assert errors[3].severity == "note"
        assert errors[3].code == "note"


# =============================================================================
# TypeScript/tsconfig Tests
# =============================================================================


class TestTsconfigFinding:
    """Tests for tsconfig.json finding."""

    def test_find_tsconfig_in_same_directory(self, tmp_path: Path) -> None:
        """Should find tsconfig in same directory as file."""
        gate = TypeCheckGate(repo_root=tmp_path)

        # Create project structure
        project_dir = tmp_path / "frontend"
        project_dir.mkdir()
        (project_dir / "tsconfig.json").write_text("{}")
        ts_file = project_dir / "app.ts"
        ts_file.write_text("const x: number = 1")

        result = gate._find_tsconfig_for_files([ts_file])

        assert project_dir in result
        assert ts_file in result[project_dir]

    def test_find_tsconfig_in_parent_directory(self, tmp_path: Path) -> None:
        """Should find tsconfig in parent directory."""
        gate = TypeCheckGate(repo_root=tmp_path)

        # Create nested structure
        (tmp_path / "tsconfig.json").write_text("{}")
        src_dir = tmp_path / "src" / "components"
        src_dir.mkdir(parents=True)
        ts_file = src_dir / "Button.tsx"
        ts_file.write_text("export const Button = () => {}")

        result = gate._find_tsconfig_for_files([ts_file])

        assert tmp_path in result

    def test_no_tsconfig_found(self, tmp_path: Path) -> None:
        """Should return empty dict when no tsconfig found."""
        gate = TypeCheckGate(repo_root=tmp_path)

        ts_file = tmp_path / "app.ts"
        ts_file.write_text("const x = 1")

        result = gate._find_tsconfig_for_files([ts_file])

        assert result == {}


# =============================================================================
# Command Execution Tests
# =============================================================================


class TestCommandExecution:
    """Tests for command execution."""

    @pytest.mark.asyncio
    async def test_command_exists_found(self, tmp_path: Path) -> None:
        """Should return True for existing commands."""
        gate = TypeCheckGate(repo_root=tmp_path)

        # Mock _run_command to return a path
        with patch.object(gate, "_run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "/usr/bin/python"

            result = await gate._command_exists("python")

            assert result is True

    @pytest.mark.asyncio
    async def test_command_exists_not_found(self, tmp_path: Path) -> None:
        """Should return False for non-existing commands."""
        gate = TypeCheckGate(repo_root=tmp_path)

        with patch.object(gate, "_run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ""  # Empty result

            result = await gate._command_exists("nonexistent_command_12345")

            assert result is False

    @pytest.mark.asyncio
    async def test_run_command_success(self, tmp_path: Path) -> None:
        """Should return stdout on success."""
        gate = TypeCheckGate(repo_root=tmp_path)

        result = await gate._run_command(
            ["echo", "hello world"],
            cwd=tmp_path,
        )

        assert result is not None
        assert "hello world" in result

    @pytest.mark.asyncio
    async def test_run_command_allow_failure(self, tmp_path: Path) -> None:
        """Should return output even on non-zero exit when allowed."""
        gate = TypeCheckGate(repo_root=tmp_path)

        result = await gate._run_command(
            ["ls", "nonexistent_directory_12345"],
            cwd=tmp_path,
            allow_failure=True,
        )

        # Command output may vary, but should not raise
        assert result is None or "No such file" in result or "cannot access" in result.lower()

    @pytest.mark.asyncio
    async def test_run_command_not_found(self, tmp_path: Path) -> None:
        """Should return None for non-existent command."""
        gate = TypeCheckGate(repo_root=tmp_path)

        result = await gate._run_command(
            ["command_that_does_not_exist_12345"],
            cwd=tmp_path,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_run_command_timeout(self, tmp_path: Path) -> None:
        """Should handle command timeout."""
        gate = TypeCheckGate(repo_root=tmp_path)

        # Use a command that will timeout (sleep longer than timeout)
        result = await gate._run_command(
            ["sleep", "10"],
            cwd=tmp_path,
            timeout=0.1,
        )

        assert result is None


# =============================================================================
# Staged Files Tests
# =============================================================================


class TestGetStagedFiles:
    """Tests for getting staged files."""

    @pytest.mark.asyncio
    async def test_get_staged_files_empty(self, tmp_path: Path) -> None:
        """Should return empty list when no staged files."""
        gate = TypeCheckGate(repo_root=tmp_path)

        with patch.object(gate, "_run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ""  # No output = no staged files

            files = await gate.get_staged_files()

            assert files == []

    @pytest.mark.asyncio
    async def test_get_staged_files_with_files(self, tmp_path: Path) -> None:
        """Should parse staged files from git output."""
        gate = TypeCheckGate(repo_root=tmp_path)

        # Create files
        (tmp_path / "main.py").write_text("x = 1")
        (tmp_path / "utils.py").write_text("y = 2")

        with patch.object(gate, "_run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "main.py\nutils.py\n"

            files = await gate.get_staged_files()

            assert len(files) == 2
            assert all(isinstance(f, Path) for f in files)

    @pytest.mark.asyncio
    async def test_get_staged_files_git_failure(self, tmp_path: Path) -> None:
        """Should return empty list on git error."""
        gate = TypeCheckGate(repo_root=tmp_path)

        with patch.object(gate, "_run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = None  # Command failed

            files = await gate.get_staged_files()

            assert files == []

    @pytest.mark.asyncio
    async def test_get_staged_files_exception(self, tmp_path: Path) -> None:
        """Should handle exceptions gracefully."""
        gate = TypeCheckGate(repo_root=tmp_path)

        with patch.object(gate, "_run_command", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = Exception("git error")

            files = await gate.get_staged_files()

            assert files == []

    @pytest.mark.asyncio
    async def test_get_staged_files_filters_nonexistent(self, tmp_path: Path) -> None:
        """Should filter out non-existent files."""
        gate = TypeCheckGate(repo_root=tmp_path)

        # Create only one file
        (tmp_path / "exists.py").write_text("x = 1")

        with patch.object(gate, "_run_command", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "exists.py\nnonexistent.py\n"

            files = await gate.get_staged_files()

            assert len(files) == 1
            assert files[0].name == "exists.py"


# =============================================================================
# Mypy Integration Tests
# =============================================================================


class TestMypyIntegration:
    """Tests for mypy integration."""

    @pytest.mark.asyncio
    async def test_run_mypy_no_files(self, tmp_path: Path) -> None:
        """Should return empty results with no files."""
        gate = TypeCheckGate(repo_root=tmp_path)

        errors, cached = await gate.run_mypy([])

        assert errors == []
        assert cached == []

    @pytest.mark.asyncio
    async def test_run_mypy_all_cached(self, tmp_path: Path) -> None:
        """Should use cached results when available."""
        gate = TypeCheckGate(repo_root=tmp_path)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1")

        # Pre-populate cache
        gate._cache_result(py_file, [TypeCheckError("test.py", 1, 1, "E1", "Error")])

        errors, cached = await gate.run_mypy([py_file])

        assert len(errors) == 1
        assert len(cached) == 1
        assert cached[0] == py_file

    @pytest.mark.asyncio
    async def test_run_mypy_command_not_found(self, tmp_path: Path) -> None:
        """Should handle missing mypy gracefully."""
        gate = TypeCheckGate(repo_root=tmp_path)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1")

        with patch.object(gate, "_command_exists", new_callable=AsyncMock) as mock_exists:
            mock_exists.return_value = False

            errors, cached = await gate.run_mypy([py_file])

            assert errors == []
            assert cached == []

    @pytest.mark.asyncio
    async def test_run_mypy_with_actual_errors(self, tmp_path: Path) -> None:
        """Should run mypy and parse errors."""
        gate = TypeCheckGate(repo_root=tmp_path)
        py_file = tmp_path / "test.py"
        py_file.write_text("x: int = 'string'")

        with patch.object(gate, "_command_exists", new_callable=AsyncMock) as mock_exists:
            mock_exists.return_value = True

            # Simulate mypy output
            mypy_output = "test.py:1:1: error: Incompatible types in assignment [assignment]\n"

            with patch.object(gate, "_run_command", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = mypy_output

                errors, cached = await gate.run_mypy([py_file])

                assert len(errors) == 1
                assert errors[0].severity == "error"
                assert errors[0].code == "assignment"
                assert len(cached) == 0  # Not using cache

    @pytest.mark.asyncio
    async def test_run_mypy_exception(self, tmp_path: Path) -> None:
        """Should handle mypy execution exceptions."""
        gate = TypeCheckGate(repo_root=tmp_path)
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1")

        with patch.object(gate, "_command_exists", new_callable=AsyncMock) as mock_exists:
            mock_exists.return_value = True

            with patch.object(gate, "_run_command", new_callable=AsyncMock) as mock_run:
                mock_run.side_effect = Exception("mypy crashed")

                errors, cached = await gate.run_mypy([py_file])

                # Should return cached/empty results, not raise
                assert isinstance(errors, list)
                assert isinstance(cached, list)


# =============================================================================
# TSC Integration Tests
# =============================================================================


class TestTscIntegration:
    """Tests for TypeScript compiler integration."""

    @pytest.mark.asyncio
    async def test_run_tsc_no_files(self, tmp_path: Path) -> None:
        """Should return empty results with no files."""
        gate = TypeCheckGate(repo_root=tmp_path)

        errors, cached = await gate.run_tsc([])

        assert errors == []
        assert cached == []

    @pytest.mark.asyncio
    async def test_run_tsc_no_tsconfig(self, tmp_path: Path) -> None:
        """Should return empty results when no tsconfig found."""
        gate = TypeCheckGate(repo_root=tmp_path)
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("const x = 1")

        errors, cached = await gate.run_tsc([ts_file])

        assert errors == []
        assert cached == []

    @pytest.mark.asyncio
    async def test_run_tsc_command_not_found(self, tmp_path: Path) -> None:
        """Should handle missing tsc gracefully."""
        gate = TypeCheckGate(repo_root=tmp_path)

        # Create tsconfig
        (tmp_path / "tsconfig.json").write_text("{}")
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("const x = 1")

        with patch.object(gate, "_command_exists", new_callable=AsyncMock) as mock_exists:
            mock_exists.return_value = False

            errors, cached = await gate.run_tsc([ts_file])

            assert errors == []
            assert cached == []

    @pytest.mark.asyncio
    async def test_run_tsc_all_cached(self, tmp_path: Path) -> None:
        """Should use cached results for TypeScript files."""
        gate = TypeCheckGate(repo_root=tmp_path)

        (tmp_path / "tsconfig.json").write_text("{}")
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("const x: number = 1")

        # Pre-populate cache
        gate._cache_result(ts_file, [TypeCheckError("app.ts", 1, 1, "TS1234", "Error")])

        errors, cached = await gate.run_tsc([ts_file])

        assert len(errors) == 1
        assert len(cached) == 1

    @pytest.mark.asyncio
    async def test_run_tsc_with_errors(self, tmp_path: Path) -> None:
        """Should run tsc and parse errors."""
        gate = TypeCheckGate(repo_root=tmp_path)

        (tmp_path / "tsconfig.json").write_text("{}")
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("const x: number = 'string'")

        with patch.object(gate, "_command_exists", new_callable=AsyncMock) as mock_exists:
            mock_exists.return_value = True

            # Simulate tsc output format: file.ts(line,col): error TSxxxx: message
            tsc_output = (
                'app.ts(1,7): error TS2322: Type "string" is not assignable to type "number".\n'
            )

            with patch.object(gate, "_run_command", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = tsc_output

                errors, cached = await gate.run_tsc([ts_file])

                assert len(errors) == 1
                assert errors[0].severity == "error"
                assert errors[0].code == "TS2322"

    @pytest.mark.asyncio
    async def test_run_tsc_exception(self, tmp_path: Path) -> None:
        """Should handle tsc execution exceptions."""
        gate = TypeCheckGate(repo_root=tmp_path)

        (tmp_path / "tsconfig.json").write_text("{}")
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("const x = 1")

        with patch.object(gate, "_command_exists", new_callable=AsyncMock) as mock_exists:
            mock_exists.return_value = True

            with patch.object(gate, "_run_command", new_callable=AsyncMock) as mock_run:
                mock_run.side_effect = Exception("tsc crashed")

                errors, cached = await gate.run_tsc([ts_file])

                # Should return cached/empty results, not raise
                assert isinstance(errors, list)
                assert isinstance(cached, list)


# =============================================================================
# Main Run Method Tests
# =============================================================================


class TestRunMethod:
    """Tests for the main run() method."""

    @pytest.mark.asyncio
    async def test_run_no_staged_files(self, tmp_path: Path) -> None:
        """Should pass with no files to check."""
        gate = TypeCheckGate(repo_root=tmp_path)

        with patch.object(gate, "get_staged_files", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []

            result = await gate.run()

            assert result.passed is True
            assert result.files_checked == 0

    @pytest.mark.asyncio
    async def test_run_with_python_files(self, tmp_path: Path) -> None:
        """Should check Python files and return result."""
        gate = TypeCheckGate(repo_root=tmp_path)
        py_file = tmp_path / "main.py"
        py_file.write_text("x = 1")

        with patch.object(gate, "get_staged_files", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [py_file]

            with patch.object(gate, "run_mypy", new_callable=AsyncMock) as mock_mypy:
                mock_mypy.return_value = ([], [])  # No errors, no cached

                result = await gate.run()

                assert result.files_checked == 1
                mock_mypy.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_passes_with_warnings_only(self, tmp_path: Path) -> None:
        """Should pass when only warnings (no errors)."""
        gate = TypeCheckGate(repo_root=tmp_path)
        py_file = tmp_path / "main.py"
        py_file.write_text("x = 1")

        with patch.object(gate, "get_staged_files", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [py_file]

            with patch.object(gate, "run_mypy", new_callable=AsyncMock) as mock_mypy:
                # Only warnings, no errors
                mock_mypy.return_value = (
                    [TypeCheckError("main.py", 1, 1, "W1", "Warning", "warning")],
                    [],
                )

                result = await gate.run()

                assert result.passed is True  # Warnings don't fail
                assert result.warning_count == 1

    @pytest.mark.asyncio
    async def test_run_fails_with_errors(self, tmp_path: Path) -> None:
        """Should fail when errors found."""
        gate = TypeCheckGate(repo_root=tmp_path)
        py_file = tmp_path / "main.py"
        py_file.write_text("x = 1")

        with patch.object(gate, "get_staged_files", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [py_file]

            with patch.object(gate, "run_mypy", new_callable=AsyncMock) as mock_mypy:
                mock_mypy.return_value = (
                    [TypeCheckError("main.py", 1, 1, "E1", "Type error", "error")],
                    [],
                )

                result = await gate.run()

                assert result.passed is False
                assert result.error_count == 1

    @pytest.mark.asyncio
    async def test_run_with_typescript_files(self, tmp_path: Path) -> None:
        """Should check TypeScript files."""
        gate = TypeCheckGate(repo_root=tmp_path)

        (tmp_path / "tsconfig.json").write_text("{}")
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("const x = 1")

        with patch.object(gate, "get_staged_files", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [ts_file]

            with patch.object(gate, "run_tsc", new_callable=AsyncMock) as mock_tsc:
                mock_tsc.return_value = ([], [])

                result = await gate.run()

                assert result.files_checked == 1
                mock_tsc.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_with_both_file_types(self, tmp_path: Path) -> None:
        """Should check both Python and TypeScript files."""
        gate = TypeCheckGate(repo_root=tmp_path)

        (tmp_path / "tsconfig.json").write_text("{}")
        py_file = tmp_path / "main.py"
        ts_file = tmp_path / "app.ts"
        py_file.write_text("x = 1")
        ts_file.write_text("const x = 1")

        with patch.object(gate, "get_staged_files", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [py_file, ts_file]

            with patch.object(gate, "run_mypy", new_callable=AsyncMock) as mock_mypy:
                mock_mypy.return_value = ([], [])

                with patch.object(gate, "run_tsc", new_callable=AsyncMock) as mock_tsc:
                    mock_tsc.return_value = ([], [])

                    result = await gate.run()

                    assert result.files_checked == 2
                    mock_mypy.assert_called_once()
                    mock_tsc.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_execution_errors_fail_gate(self, tmp_path: Path) -> None:
        """Should fail when execution errors occur."""
        gate = TypeCheckGate(repo_root=tmp_path)
        py_file = tmp_path / "main.py"
        py_file.write_text("x = 1")

        with patch.object(gate, "get_staged_files", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [py_file]

            with patch.object(gate, "run_mypy", new_callable=AsyncMock) as mock_mypy:
                # Simulate exception in mypy
                mock_mypy.side_effect = Exception("mypy crashed")

                result = await gate.run()

                assert result.passed is False
                assert "mypy check failed" in result.execution_errors[0]


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestRunTypeCheckGate:
    """Tests for run_type_check_gate factory function."""

    @pytest.mark.asyncio
    async def test_factory_function(self, tmp_path: Path) -> None:
        """Should create gate and run check."""
        with patch.object(TypeCheckGate, "get_staged_files", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []

            result = await run_type_check_gate(
                repo_root=tmp_path,
                use_cache=False,
            )

            assert isinstance(result, TypeCheckResult)
            assert result.passed is True


# =============================================================================
# CLI Tests
# =============================================================================


class TestCLI:
    """Tests for CLI functionality."""

    @pytest.mark.asyncio
    async def test_main_async_no_args(self, tmp_path: Path) -> None:
        """Should run with default args."""
        from forge_harness.quality_gates.type_check import main_async

        with patch.object(TypeCheckGate, "get_staged_files", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []

            with patch("sys.argv", ["type_check"]):
                exit_code = await main_async()

                assert exit_code == 0

    @pytest.mark.asyncio
    async def test_main_async_fails_on_errors(self, tmp_path: Path) -> None:
        """Should exit with code 1 on errors."""
        from forge_harness.quality_gates.type_check import main_async

        py_file = tmp_path / "main.py"
        py_file.write_text("x = 1")

        with patch.object(TypeCheckGate, "get_staged_files", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [py_file]

            with patch.object(TypeCheckGate, "run_mypy", new_callable=AsyncMock) as mock_mypy:
                mock_mypy.return_value = (
                    [TypeCheckError("main.py", 1, 1, "E1", "Error", "error")],
                    [],
                )

                with patch("sys.argv", ["type_check"]):
                    exit_code = await main_async()

                    assert exit_code == 1

    @pytest.mark.asyncio
    async def test_main_async_with_no_cache(self, tmp_path: Path) -> None:
        """Should accept --no-cache flag."""
        from forge_harness.quality_gates.type_check import main_async

        with patch.object(TypeCheckGate, "get_staged_files", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []

            with patch("sys.argv", ["type_check", "--no-cache"]):
                exit_code = await main_async()

                assert exit_code == 0

    @pytest.mark.asyncio
    async def test_main_async_with_verbose(self, tmp_path: Path) -> None:
        """Should accept --verbose flag."""
        from forge_harness.quality_gates.type_check import main_async

        with patch.object(TypeCheckGate, "get_staged_files", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []

            with patch("sys.argv", ["type_check", "--verbose"]):
                exit_code = await main_async()

                assert exit_code == 0

    @pytest.mark.asyncio
    async def test_main_async_with_repo_root(self, tmp_path: Path) -> None:
        """Should accept --repo-root flag."""
        from forge_harness.quality_gates.type_check import main_async

        with patch.object(TypeCheckGate, "get_staged_files", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []

            with patch("sys.argv", ["type_check", "--repo-root", str(tmp_path)]):
                exit_code = await main_async()

                assert exit_code == 0
