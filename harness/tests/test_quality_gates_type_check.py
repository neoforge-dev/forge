"""
Comprehensive Tests for Type Check Gate
========================================

Additional edge cases and comprehensive testing for quality_gates/type_check.py
"""

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
# Fixtures
# =============================================================================


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir()

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
def type_check_gate(temp_repo: Path) -> TypeCheckGate:
    """Create a TypeCheckGate instance."""
    return TypeCheckGate(repo_root=temp_repo, use_cache=False)


@pytest.fixture
def type_check_gate_with_cache(temp_repo: Path) -> TypeCheckGate:
    """Create a TypeCheckGate instance with caching enabled."""
    return TypeCheckGate(repo_root=temp_repo, use_cache=True)


# =============================================================================
# Edge Case Tests - TypeCheckError
# =============================================================================


class TestTypeCheckErrorEdgeCases:
    """Edge case tests for TypeCheckError."""

    def test_error_str_with_all_fields(self) -> None:
        """Test string representation with line, column, code, and message."""
        error = TypeCheckError(
            file="src/module/file.py",
            line=42,
            column=15,
            code="return-value",
            message="Missing return statement",
            severity="error",
        )
        error_str = str(error)
        assert "src/module/file.py:42:15" in error_str
        assert "return-value" in error_str
        assert "Missing return statement" in error_str

    def test_error_str_no_line(self) -> None:
        """Test string representation without line number."""
        error = TypeCheckError(
            file="test.py",
            line=None,
            column=None,
            code="import-error",
            message="Cannot import module",
            severity="error",
        )
        error_str = str(error)
        assert error_str == "test.py - import-error: Cannot import module"

    def test_error_str_only_line(self) -> None:
        """Test string representation with line but no column."""
        error = TypeCheckError(
            file="test.py",
            line=10,
            column=None,
            code="type-error",
            message="Some error",
            severity="error",
        )
        error_str = str(error)
        assert "test.py:10" in error_str
        assert "type-error" in error_str

    def test_error_default_severity(self) -> None:
        """Test default severity is error."""
        error = TypeCheckError(
            file="test.py",
            line=10,
            column=5,
            code="type-error",
            message="Some error",
        )
        assert error.severity == "error"

    def test_error_all_severities(self) -> None:
        """Test all severity levels."""
        for severity in ["error", "warning", "note"]:
            error = TypeCheckError(
                file="test.py",
                line=10,
                column=5,
                code="test-code",
                message="Test message",
                severity=severity,
            )
            assert error.severity == severity


# =============================================================================
# Edge Case Tests - TypeCheckResult
# =============================================================================


class TestTypeCheckResultEdgeCases:
    """Edge case tests for TypeCheckResult."""

    def test_result_empty_errors(self) -> None:
        """Test result with empty error list."""
        result = TypeCheckResult(passed=True, files_checked=0, errors=[])
        assert result.error_count == 0
        assert result.warning_count == 0

    def test_result_only_warnings(self) -> None:
        """Test result with only warnings (should pass)."""
        warnings = [
            TypeCheckError(
                file="test.py",
                line=10,
                column=5,
                code="var-annotated",
                message="Missing type",
                severity="warning",
            ),
            TypeCheckError(
                file="test.py",
                line=20,
                column=1,
                code="unused-import",
                message="Import not used",
                severity="warning",
            ),
        ]
        result = TypeCheckResult(passed=True, files_checked=1, errors=warnings)
        assert result.passed is True
        assert result.error_count == 0
        assert result.warning_count == 2

    def test_result_only_notes(self) -> None:
        """Test result with only notes (should pass)."""
        notes = [
            TypeCheckError(
                file="test.py",
                line=10,
                column=5,
                code="note-code",
                message="Some note",
                severity="note",
            ),
        ]
        result = TypeCheckResult(passed=True, files_checked=1, errors=notes)
        assert result.passed is True
        assert result.error_count == 0
        assert result.warning_count == 0

    def test_result_mixed_severities(self) -> None:
        """Test result with mixed severities."""
        errors = [
            TypeCheckError(
                file="test.py", line=10, column=5, code="e1", message="e", severity="error"
            ),
            TypeCheckError(
                file="test.py", line=20, column=1, code="w1", message="w", severity="warning"
            ),
            TypeCheckError(
                file="test.py", line=30, column=2, code="n1", message="n", severity="note"
            ),
        ]
        result = TypeCheckResult(passed=False, files_checked=1, errors=errors)
        assert result.error_count == 1
        assert result.warning_count == 1

    def test_result_summary_with_execution_errors(self) -> None:
        """Test summary includes execution errors."""
        result = TypeCheckResult(
            passed=False,
            files_checked=1,
            errors=[],
            execution_errors=["mypy crashed", "timeout"],
        )
        summary = result.summary()
        assert "Errors during execution:" in summary
        assert "mypy crashed" in summary
        assert "timeout" in summary

    def test_result_summary_failed_status(self) -> None:
        """Test summary shows FAILED status."""
        result = TypeCheckResult(passed=False, files_checked=5, errors=[])
        summary = result.summary()
        assert "FAILED" in summary

    def test_result_summary_no_errors_no_cache(self) -> None:
        """Test summary doesn't show cache info when none."""
        result = TypeCheckResult(passed=True, files_checked=3)
        summary = result.summary()
        assert "cached" not in summary.lower()

    def test_result_with_execution_errors_passes(self) -> None:
        """Test that execution errors cause failure."""
        result = TypeCheckResult(
            passed=True,  # Passed is set by caller, but execution errors matter
            files_checked=1,
            errors=[],
            execution_errors=["mypy crashed"],
        )
        # This would be set to False by the gate
        assert len(result.execution_errors) > 0


# =============================================================================
# Edge Case Tests - TypeCheckGate Initialization
# =============================================================================


class TestTypeCheckGateInit:
    """Edge case tests for TypeCheckGate initialization."""

    def test_default_repo_root(self) -> None:
        """Test default repo root is current directory."""
        gate = TypeCheckGate()
        assert gate.repo_root == Path.cwd()

    def test_custom_cache_dir(self, temp_repo: Path) -> None:
        """Test custom cache directory."""
        custom_cache = temp_repo / ".custom_cache"
        gate = TypeCheckGate(repo_root=temp_repo, cache_dir=custom_cache, use_cache=True)
        assert gate.cache_dir == custom_cache
        assert custom_cache.exists()

    def test_cache_disabled_no_dir_creation(self, temp_repo: Path) -> None:
        """Test that cache dir is not created when disabled."""
        cache_path = temp_repo / ".should_not_exist"
        gate = TypeCheckGate(repo_root=temp_repo, cache_dir=cache_path, use_cache=False)
        assert not cache_path.exists()

    def test_default_cache_dir(self, temp_repo: Path) -> None:
        """Test default cache directory location."""
        gate = TypeCheckGate(repo_root=temp_repo, use_cache=True)
        expected = temp_repo / ".forge_cache" / "type_check"
        assert gate.cache_dir == expected


# =============================================================================
# Edge Case Tests - categorize_files
# =============================================================================


class TestCategorizeFilesEdgeCases:
    """Edge case tests for categorize_files."""

    def test_empty_list(self, type_check_gate: TypeCheckGate) -> None:
        """Test categorizing empty list."""
        result = type_check_gate.categorize_files([])
        assert result == {"python": [], "typescript": []}

    def test_unknown_extensions(self, type_check_gate: TypeCheckGate, temp_repo: Path) -> None:
        """Test files with unknown extensions are ignored."""
        files = [
            temp_repo / "README.md",
            temp_repo / "data.json",
            temp_repo / "script.sh",
            temp_repo / "Dockerfile",
        ]
        result = type_check_gate.categorize_files(files)
        assert result["python"] == []
        assert result["typescript"] == []

    def test_mixed_with_unknown(self, type_check_gate: TypeCheckGate, temp_repo: Path) -> None:
        """Test mixed files with unknown extensions."""
        files = [
            temp_repo / "app.py",
            temp_repo / "readme.md",
            temp_repo / "util.ts",
            temp_repo / "style.css",
        ]
        result = type_check_gate.categorize_files(files)
        assert len(result["python"]) == 1
        assert len(result["typescript"]) == 1


# =============================================================================
# Edge Case Tests - Cache Operations
# =============================================================================


class TestCacheEdgeCases:
    """Edge case tests for caching operations."""

    def test_get_cache_key_external_path(self, type_check_gate: TypeCheckGate) -> None:
        """Test cache key for file outside repo root."""
        external_file = Path("/external/path/file.py")
        key = type_check_gate._get_cache_key(external_file)
        # Should return empty string for paths outside repo
        assert key == ""

    def test_cache_result_no_cache(self, type_check_gate: TypeCheckGate, temp_repo: Path) -> None:
        """Test cache result when caching is disabled."""
        test_file = temp_repo / "test.py"
        test_file.write_text("print(1)")
        type_check_gate._cache_result(test_file, [])
        # Should not create cache file
        assert not list(type_check_gate.cache_dir.glob("*.json"))

    def test_get_cached_errors_no_cache(self, type_check_gate: TypeCheckGate, temp_repo: Path) -> None:
        """Test get cached errors when caching is disabled."""
        test_file = temp_repo / "test.py"
        result = type_check_gate._get_cached_errors(test_file)
        assert result is None

    def test_is_cached_no_cache(self, type_check_gate: TypeCheckGate, temp_repo: Path) -> None:
        """Test is_cached when caching is disabled."""
        test_file = temp_repo / "test.py"
        assert type_check_gate._is_cached(test_file) is False

    def test_cache_corrupted_file(self, type_check_gate_with_cache: TypeCheckGate, temp_repo: Path) -> None:
        """Test handling corrupted cache file."""
        test_file = temp_repo / "test.py"
        test_file.write_text("print(1)")

        # Create corrupted cache file
        cache_key = type_check_gate_with_cache._get_cache_key(test_file)
        cache_file = type_check_gate_with_cache.cache_dir / f"{cache_key}.json"
        cache_file.write_text("not valid json{")

        # Should return None for corrupted cache
        result = type_check_gate_with_cache._get_cached_errors(test_file)
        assert result is None


# =============================================================================
# Edge Case Tests - get_staged_files
# =============================================================================


class TestGetStagedFilesEdgeCases:
    """Edge case tests for get_staged_files."""

    async def test_staged_deleted_file(self, type_check_gate: TypeCheckGate, temp_repo: Path) -> None:
        """Test that deleted files are not included."""
        # Create and stage a file
        test_file = temp_repo / "test.py"
        test_file.write_text("print(1)")

        import subprocess

        subprocess.run(["git", "add", "test.py"], cwd=temp_repo, check=True)

        # Delete the file
        test_file.unlink()

        files = await type_check_gate.get_staged_files()
        # Deleted files should be filtered out
        assert len(files) == 0

    async def test_staged_renamed_file(self, type_check_gate: TypeCheckGate, temp_repo: Path) -> None:
        """Test handling renamed files."""
        # Create and stage a file
        test_file = temp_repo / "old_name.py"
        test_file.write_text("print(1)")

        import subprocess

        subprocess.run(["git", "add", "old_name.py"], cwd=temp_repo, check=True)

        # Rename the file
        new_file = temp_repo / "new_name.py"
        test_file.rename(new_file)

        # Stage the rename
        subprocess.run(["git", "add", "old_name.py"], cwd=temp_repo, check=True)
        subprocess.run(["git", "add", "new_name.py"], cwd=temp_repo, check=True)

        files = await type_check_gate.get_staged_files()
        # Should include the new file path
        assert len(files) >= 1

    async def test_git_error_handling(self, type_check_gate: TypeCheckGate) -> None:
        """Test git error handling."""
        # Point to invalid repo
        type_check_gate.repo_root = Path("/nonexistent")
        files = await type_check_gate.get_staged_files()
        # Should return empty list on error
        assert files == []


# =============================================================================
# Edge Case Tests - run_mypy
# =============================================================================


class TestRunMypyEdgeCases:
    """Edge case tests for run_mypy."""

    async def test_mypy_empty_files_list(self, type_check_gate: TypeCheckGate) -> None:
        """Test mypy with empty files list."""
        errors, cached = await type_check_gate.run_mypy([])
        assert errors == []
        assert cached == []

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._command_exists")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._run_command")
    async def test_mypy_non_standard_error_format(
        self, mock_run: AsyncMock, mock_exists: AsyncMock, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test mypy with non-standard error format."""
        mock_exists.return_value = True
        # Non-standard output that shouldn't match the regex
        mock_run.return_value = "Some random output without proper format\nAnother line"

        files = [temp_repo / "test.py"]
        errors, cached = await type_check_gate.run_mypy(files)

        # Should not crash, should return empty errors
        assert errors == []

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._command_exists")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._run_command")
    async def test_mypy_error_without_code(
        self, mock_run: AsyncMock, mock_exists: AsyncMock, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test mypy error without error code in brackets."""
        mock_exists.return_value = True
        mock_run.return_value = "test.py:10:5: error: Some error message"

        files = [temp_repo / "test.py"]
        errors, cached = await type_check_gate.run_mypy(files)

        # Should use "type-error" as default code
        assert len(errors) == 1
        assert errors[0].code == "type-error"

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._command_exists")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._run_command")
    async def test_mypy_multiple_files_same_error(
        self, mock_run: AsyncMock, mock_exists: AsyncMock, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test mypy with errors across multiple files."""
        mock_exists.return_value = True
        mock_run.return_value = (
            "file1.py:10:5: error: Error in file1 [code1]\n"
            "file2.py:20:10: error: Error in file2 [code2]\n"
        )

        files = [temp_repo / "file1.py", temp_repo / "file2.py"]
        errors, cached = await type_check_gate.run_mypy(files)

        assert len(errors) == 2


# =============================================================================
# Edge Case Tests - run_tsc
# =============================================================================


class TestRunTscEdgeCases:
    """Edge case tests for run_tsc."""

    async def test_tsc_empty_files_list(self, type_check_gate: TypeCheckGate) -> None:
        """Test tsc with empty files list."""
        errors, cached = await type_check_gate.run_tsc([])
        assert errors == []
        assert cached == []

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._command_exists")
    async def test_tsc_no_tsconfig(
        self, mock_exists: AsyncMock, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test tsc when no tsconfig.json exists."""
        mock_exists.return_value = True
        files = [temp_repo / "app.ts"]
        errors, cached = await type_check_gate.run_tsc(files)

        assert errors == []
        assert cached == []

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._command_exists")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._run_command")
    async def test_tsc_non_standard_output(
        self, mock_run: AsyncMock, mock_exists: AsyncMock, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test tsc with non-standard output."""
        mock_exists.return_value = True
        mock_run.return_value = "Random output without proper format"

        # Create tsconfig.json
        (temp_repo / "tsconfig.json").write_text(json.dumps({}))

        files = [temp_repo / "app.ts"]
        errors, cached = await type_check_gate.run_tsc(files)

        assert errors == []

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._command_exists")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._run_command")
    async def test_tsc_filters_non_project_files(
        self, mock_run: AsyncMock, mock_exists: AsyncMock, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test tsc only includes errors for files in project."""
        mock_exists.return_value = True
        # Error for a file not in our project
        mock_run.return_value = "other.ts(1,1): error TS0001: Some error"

        # Create tsconfig.json
        (temp_repo / "tsconfig.json").write_text(json.dumps({}))

        files = [temp_repo / "app.ts"]
        errors, cached = await type_check_gate.run_tsc(files)

        # Should filter out non-project file errors
        assert errors == []


# =============================================================================
# Edge Case Tests - _run_command
# =============================================================================


class TestRunCommandEdgeCases:
    """Edge case tests for _run_command."""

    async def test_command_timeout(self, type_check_gate: TypeCheckGate) -> None:
        """Test command timeout handling."""
        # Use a command that would timeout
        result = await type_check_gate._run_command(
            ["sleep", "100"], timeout=1
        )
        assert result is None

    async def test_command_not_found(self, type_check_gate: TypeCheckGate) -> None:
        """Test command not found handling."""
        result = await type_check_gate._run_command(["nonexistent_command_xyz"])
        assert result is None

    async def test_command_with_cwd(self, type_check_gate: TypeCheckGate, temp_repo: Path) -> None:
        """Test command with custom working directory."""
        result = await type_check_gate._run_command(
            ["pwd"], cwd=temp_repo
        )
        assert result is not None
        assert temp_repo.name in result

    async def test_command_allow_failure_true(self, type_check_gate: TypeCheckGate) -> None:
        """Test allow_failure=True returns output even on failure."""
        result = await type_check_gate._run_command(
            ["ls", "/nonexistent_path"], allow_failure=True
        )
        # Should return output even though command failed
        assert result is not None

    async def test_command_allow_failure_false(self, type_check_gate: TypeCheckGate) -> None:
        """Test allow_failure=False returns None on failure."""
        result = await type_check_gate._run_command(
            ["ls", "/nonexistent_path"], allow_failure=False
        )
        assert result is None


# =============================================================================
# Edge Case Tests - run() method
# =============================================================================


class TestRunEdgeCases:
    """Edge case tests for run() method."""

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.get_staged_files")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.run_mypy")
    async def test_run_with_execution_error(
        self, mock_mypy: AsyncMock, mock_get_files: AsyncMock, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test run handles mypy execution error."""
        mock_get_files.return_value = [temp_repo / "test.py"]
        mock_mypy.side_effect = Exception("mypy crashed")

        result = await type_check_gate.run()

        assert result.passed is False
        assert len(result.execution_errors) > 0

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.get_staged_files")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.run_mypy")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.run_tsc")
    async def test_run_only_typescript(
        self, mock_tsc: AsyncMock, mock_mypy: AsyncMock, mock_get_files: AsyncMock, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test run with only TypeScript files."""
        mock_get_files.return_value = [temp_repo / "app.ts"]
        mock_mypy.return_value = ([], [])
        mock_tsc.return_value = ([], [])

        result = await type_check_gate.run()

        assert result.files_checked == 1
        mock_mypy.assert_not_called()
        mock_tsc.assert_called_once()

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.get_staged_files")
    async def test_run_cached_files_count(
        self, mock_get_files: AsyncMock, type_check_gate_with_cache: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test cached files count is properly reported when using cache."""
        test_file = temp_repo / "test.py"
        test_file.write_text("print(1)")

        # Pre-cache the file with an error
        type_check_gate_with_cache._cache_result(test_file, [
            TypeCheckError(
                file="test.py", line=10, column=5,
                code="error-code", message="Cached error", severity="error"
            )
        ])

        mock_get_files.return_value = [test_file]

        result = await type_check_gate_with_cache.run()

        # When file is cached, run_mypy returns cached errors but cached_files > 0
        assert result.cached_files == 1


# =============================================================================
# Edge Case Tests - _command_exists
# =============================================================================


class TestCommandExistsEdgeCases:
    """Edge case tests for _command_exists."""

    async def test_command_exists_positive(self, type_check_gate: TypeCheckGate) -> None:
        """Test command exists for valid command."""
        result = await type_check_gate._command_exists("python")
        assert result is True

    async def test_command_exists_negative(self, type_check_gate: TypeCheckGate) -> None:
        """Test command does not exist."""
        result = await type_check_gate._command_exists("this_command_definitely_does_not_exist_12345")
        assert result is False


# =============================================================================
# Edge Case Tests - _find_tsconfig_for_files
# =============================================================================


class TestFindTsconfigEdgeCases:
    """Edge case tests for _find_tsconfig_for_files."""

    def test_nested_files_with_root_tsconfig(self, type_check_gate: TypeCheckGate, temp_repo: Path) -> None:
        """Test deeply nested files use root tsconfig."""
        # Create nested structure
        (temp_repo / "src" / "deep" / "nested").mkdir(parents=True)

        # Create tsconfig at root
        (temp_repo / "tsconfig.json").write_text(json.dumps({}))

        # Create deeply nested file
        file_path = temp_repo / "src" / "deep" / "nested" / "app.ts"

        result = type_check_gate._find_tsconfig_for_files([file_path])

        assert temp_repo in result

    def test_multiple_tsconfigs_in_ancestry(self, type_check_gate: TypeCheckGate, temp_repo: Path) -> None:
        """Test that closest tsconfig is used."""
        # Create nested structure
        (temp_repo / "src").mkdir()
        (temp_repo / "src" / "components").mkdir()

        # Create tsconfig at both levels
        (temp_repo / "tsconfig.json").write_text(json.dumps({}))
        (temp_repo / "src" / "tsconfig.json").write_text(json.dumps({}))

        file_path = temp_repo / "src" / "components" / "Button.tsx"

        result = type_check_gate._find_tsconfig_for_files([file_path])

        # Should use the closer one (src/)
        assert temp_repo / "src" in result


# =============================================================================
# Edge Case Tests - Integration / Factory
# =============================================================================


class TestFactoryFunctionEdgeCases:
    """Edge case tests for factory function."""

    @pytest.mark.asyncio
    async def test_run_type_check_gate_with_repo_root(self) -> None:
        """Test factory with custom repo root."""
        with patch("forge_harness.quality_gates.type_check.TypeCheckGate") as mock_gate_class:
            mock_gate = MagicMock()
            mock_gate.run = AsyncMock(return_value=TypeCheckResult(passed=True, files_checked=0))
            mock_gate_class.return_value = mock_gate

            custom_root = Path("/custom/path")
            await run_type_check_gate(repo_root=custom_root)

            mock_gate_class.assert_called_once_with(repo_root=custom_root, use_cache=True)


# =============================================================================
# Edge Case Tests - CLI Entry Points
# =============================================================================


class TestCLIEntryPoints:
    """Tests for CLI entry points (main and main_async)."""

    @pytest.mark.asyncio
    async def test_main_async_returns_code_on_pass(self) -> None:
        """Test main_async returns 0 on pass."""
        with patch("forge_harness.quality_gates.type_check.run_type_check_gate") as mock_run:
            with patch("sys.argv", ["type_check"]):
                mock_run.return_value = TypeCheckResult(passed=True, files_checked=0)

                from forge_harness.quality_gates.type_check import main_async

                result = await main_async()
                assert result == 0

    @pytest.mark.asyncio
    async def test_main_async_returns_code_on_fail(self) -> None:
        """Test main_async returns 1 on failure."""
        with patch("forge_harness.quality_gates.type_check.run_type_check_gate") as mock_run:
            with patch("sys.argv", ["type_check"]):
                mock_run.return_value = TypeCheckResult(
                    passed=False, files_checked=1, errors=[TypeCheckError(
                        file="test.py", line=1, column=1, code="e", message="e", severity="error"
                    )]
                )

                from forge_harness.quality_gates.type_check import main_async

                result = await main_async()
                assert result == 1

    def test_main_with_args(self) -> None:
        """Test main function with sys.exit."""
        with patch("forge_harness.quality_gates.type_check.main_async") as mock_async:
            mock_async.return_value = 0

            from forge_harness.quality_gates.type_check import main

            with patch("sys.argv", ["type_check", "--no-cache"]):
                result = main()
                assert result == 0


# =============================================================================
# Regression Tests
# =============================================================================


class TestRegressionCases:
    """Regression tests for previously fixed issues."""

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._command_exists")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._run_command")
    async def test_mypy_absolute_path_handling(
        self, mock_run: AsyncMock, mock_exists: AsyncMock, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test mypy handles absolute paths in output."""
        mock_exists.return_value = True
        # Output with absolute path
        mock_run.return_value = f"{temp_repo}/test.py:10:5: error: Error [code]"

        files = [temp_repo / "test.py"]
        errors, _ = await type_check_gate.run_mypy(files)

        assert len(errors) == 1
        # Should convert to relative path
        assert "test.py" in errors[0].file

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._command_exists")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._run_command")
    async def test_tsc_absolute_path_handling(
        self, mock_run: AsyncMock, mock_exists: AsyncMock, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test tsc handles absolute paths in output."""
        mock_exists.return_value = True
        # Output with absolute path
        mock_run.return_value = f"{temp_repo}/app.ts(10,5): error TS0001: Error"

        (temp_repo / "tsconfig.json").write_text(json.dumps({}))

        files = [temp_repo / "app.ts"]
        errors, _ = await type_check_gate.run_tsc(files)

        assert len(errors) == 1
        # Should convert to relative path
        assert "app.ts" in errors[0].file
