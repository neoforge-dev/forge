"""
Tests for Type Check Gate
=========================

Tests the type check gate quality gate functionality.
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
def type_check_gate(temp_repo: Path) -> TypeCheckGate:
    """Create a TypeCheckGate instance."""
    return TypeCheckGate(repo_root=temp_repo, use_cache=False)


@pytest.fixture
def type_check_gate_with_cache(temp_repo: Path) -> TypeCheckGate:
    """Create a TypeCheckGate instance with caching enabled."""
    return TypeCheckGate(repo_root=temp_repo, use_cache=True)


class TestTypeCheckGate:
    """Test TypeCheckGate class."""

    async def test_get_staged_files_empty(self, type_check_gate: TypeCheckGate) -> None:
        """Test getting staged files when none exist."""
        files = await type_check_gate.get_staged_files()
        assert files == []

    async def test_get_staged_files_with_staged(
        self, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test getting staged files."""
        # Create a test file and stage it
        test_file = temp_repo / "test.py"
        test_file.write_text("print('hello')\n")

        import subprocess

        subprocess.run(["git", "add", "test.py"], cwd=temp_repo, check=True)

        files = await type_check_gate.get_staged_files()
        assert len(files) == 1
        assert files[0].name == "test.py"

    def test_categorize_files_python(self, type_check_gate: TypeCheckGate, temp_repo: Path) -> None:
        """Test categorizing Python files."""
        files = [
            temp_repo / "test.py",
            temp_repo / "main.py",
        ]

        categorized = type_check_gate.categorize_files(files)
        assert len(categorized["python"]) == 2
        assert len(categorized["typescript"]) == 0

    def test_categorize_files_typescript(
        self, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test categorizing TypeScript files."""
        files = [
            temp_repo / "app.ts",
            temp_repo / "component.tsx",
        ]

        categorized = type_check_gate.categorize_files(files)
        assert len(categorized["python"]) == 0
        assert len(categorized["typescript"]) == 2

    def test_categorize_files_mixed(self, type_check_gate: TypeCheckGate, temp_repo: Path) -> None:
        """Test categorizing mixed file types."""
        files = [
            temp_repo / "test.py",
            temp_repo / "app.ts",
            temp_repo / "component.tsx",
            temp_repo / "README.md",  # Should be ignored
        ]

        categorized = type_check_gate.categorize_files(files)
        assert len(categorized["python"]) == 1
        assert len(categorized["typescript"]) == 2

    def test_categorize_files_ignores_js(
        self, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test that .js files are ignored (only .ts and .tsx)."""
        files = [
            temp_repo / "app.js",
            temp_repo / "app.ts",
        ]

        categorized = type_check_gate.categorize_files(files)
        assert len(categorized["python"]) == 0
        assert len(categorized["typescript"]) == 1

    def test_get_file_hash(self, type_check_gate: TypeCheckGate, temp_repo: Path) -> None:
        """Test file hashing for cache keys."""
        test_file = temp_repo / "test.py"
        test_file.write_text("print('hello')\n")

        hash1 = type_check_gate._get_file_hash(test_file)
        assert hash1
        assert len(hash1) == 64  # SHA256 hex digest

        # Same content should produce same hash
        hash2 = type_check_gate._get_file_hash(test_file)
        assert hash1 == hash2

        # Different content should produce different hash
        test_file.write_text("print('world')\n")
        hash3 = type_check_gate._get_file_hash(test_file)
        assert hash1 != hash3

    def test_cache_result(self, type_check_gate_with_cache: TypeCheckGate, temp_repo: Path) -> None:
        """Test caching type check results."""
        test_file = temp_repo / "test.py"
        test_file.write_text("print('hello')\n")

        errors = [
            TypeCheckError(
                file="test.py",
                line=10,
                column=5,
                code="type-error",
                message="Incompatible types",
                severity="error",
            )
        ]

        type_check_gate_with_cache._cache_result(test_file, errors)

        # Verify cache file exists
        cache_key = type_check_gate_with_cache._get_cache_key(test_file)
        cache_file = type_check_gate_with_cache.cache_dir / f"{cache_key}.json"
        assert cache_file.exists()

    def test_get_cached_errors(
        self, type_check_gate_with_cache: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test retrieving cached errors."""
        test_file = temp_repo / "test.py"
        test_file.write_text("print('hello')\n")

        errors = [
            TypeCheckError(
                file="test.py",
                line=10,
                column=5,
                code="type-error",
                message="Incompatible types",
                severity="error",
            )
        ]

        type_check_gate_with_cache._cache_result(test_file, errors)
        cached_errors = type_check_gate_with_cache._get_cached_errors(test_file)

        assert cached_errors is not None
        assert len(cached_errors) == 1
        assert cached_errors[0].code == "type-error"
        assert cached_errors[0].line == 10

    def test_is_cached(self, type_check_gate_with_cache: TypeCheckGate, temp_repo: Path) -> None:
        """Test checking if file is cached."""
        test_file = temp_repo / "test.py"
        test_file.write_text("print('hello')\n")

        # Not cached initially
        assert not type_check_gate_with_cache._is_cached(test_file)

        # Cache empty result
        type_check_gate_with_cache._cache_result(test_file, [])

        # Now should be cached
        assert type_check_gate_with_cache._is_cached(test_file)

        # Change file content - should not be cached
        test_file.write_text("print('world')\n")
        assert not type_check_gate_with_cache._is_cached(test_file)

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._command_exists")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._run_command")
    async def test_run_mypy_no_issues(
        self,
        mock_run: AsyncMock,
        mock_exists: AsyncMock,
        type_check_gate: TypeCheckGate,
        temp_repo: Path,
    ) -> None:
        """Test running mypy with no issues."""
        mock_exists.return_value = True
        mock_run.return_value = ""  # Empty output

        files = [temp_repo / "test.py"]
        errors, cached = await type_check_gate.run_mypy(files)

        assert errors == []
        assert cached == []
        mock_run.assert_called_once()

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._command_exists")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._run_command")
    async def test_run_mypy_with_errors(
        self,
        mock_run: AsyncMock,
        mock_exists: AsyncMock,
        type_check_gate: TypeCheckGate,
        temp_repo: Path,
    ) -> None:
        """Test running mypy with type errors."""
        mock_exists.return_value = True
        # Mock mypy output
        mypy_output = (
            "test.py:10:5: error: Incompatible types in assignment [assignment]\n"
            "test.py:15:1: warning: Missing type annotation [var-annotated]\n"
        )
        mock_run.return_value = mypy_output

        files = [temp_repo / "test.py"]
        errors, cached = await type_check_gate.run_mypy(files)

        assert len(errors) == 2
        assert errors[0].code == "assignment"
        assert errors[0].line == 10
        assert errors[0].column == 5
        assert errors[0].severity == "error"
        assert errors[1].severity == "warning"
        assert cached == []

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._command_exists")
    async def test_run_mypy_not_found(
        self,
        mock_exists: AsyncMock,
        type_check_gate: TypeCheckGate,
        temp_repo: Path,
    ) -> None:
        """Test running mypy when it's not installed."""
        mock_exists.return_value = False

        files = [temp_repo / "test.py"]
        errors, cached = await type_check_gate.run_mypy(files)

        assert errors == []
        assert cached == []

    async def test_run_mypy_with_cache(
        self,
        type_check_gate_with_cache: TypeCheckGate,
        temp_repo: Path,
    ) -> None:
        """Test running mypy with cached results - simplified test focusing on cache behavior."""
        test_file = temp_repo / "test.py"
        test_file.write_text("print('hello')\n")

        # Manually cache an empty result for the file
        type_check_gate_with_cache._cache_result(test_file, [])

        # Verify cache was created
        assert type_check_gate_with_cache._is_cached(test_file)

        # Mock command_exists to verify mypy is not called when cached
        call_count = 0

        async def mock_command_exists(cmd: str) -> bool:
            nonlocal call_count
            call_count += 1
            return True

        type_check_gate_with_cache._command_exists = mock_command_exists  # type: ignore

        # Run mypy - should use cache and not call command
        errors, cached = await type_check_gate_with_cache.run_mypy([test_file])

        # Should return cached file
        assert len(cached) == 1
        assert cached[0] == test_file
        assert errors == []
        # command_exists should not be called because we use cache
        assert call_count == 0

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._command_exists")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._run_command")
    async def test_run_tsc_no_issues(
        self,
        mock_run: AsyncMock,
        mock_exists: AsyncMock,
        type_check_gate: TypeCheckGate,
        temp_repo: Path,
    ) -> None:
        """Test running tsc with no issues."""
        mock_exists.return_value = True
        mock_run.return_value = ""  # Empty output

        # Create tsconfig.json
        tsconfig = temp_repo / "tsconfig.json"
        tsconfig.write_text(json.dumps({"compilerOptions": {}}))

        files = [temp_repo / "app.ts"]
        errors, cached = await type_check_gate.run_tsc(files)

        assert errors == []
        assert cached == []

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._command_exists")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._run_command")
    async def test_run_tsc_with_errors(
        self,
        mock_run: AsyncMock,
        mock_exists: AsyncMock,
        type_check_gate: TypeCheckGate,
        temp_repo: Path,
    ) -> None:
        """Test running tsc with type errors."""
        mock_exists.return_value = True
        # Mock tsc output
        tsc_output = (
            "app.ts(10,5): error TS2322: Type 'string' is not assignable to type 'number'.\n"
            "app.ts(15,1): warning TS7006: Parameter 'x' implicitly has an 'any' type.\n"
        )
        mock_run.return_value = tsc_output

        # Create tsconfig.json
        tsconfig = temp_repo / "tsconfig.json"
        tsconfig.write_text(json.dumps({"compilerOptions": {}}))

        files = [temp_repo / "app.ts"]
        errors, cached = await type_check_gate.run_tsc(files)

        assert len(errors) == 2
        assert errors[0].code == "TS2322"
        assert errors[0].line == 10
        assert errors[0].column == 5
        assert errors[0].severity == "error"
        assert errors[1].severity == "warning"

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate._command_exists")
    async def test_run_tsc_not_found(
        self,
        mock_exists: AsyncMock,
        type_check_gate: TypeCheckGate,
        temp_repo: Path,
    ) -> None:
        """Test running tsc when it's not installed."""
        mock_exists.return_value = False

        # Create tsconfig.json
        tsconfig = temp_repo / "tsconfig.json"
        tsconfig.write_text(json.dumps({"compilerOptions": {}}))

        files = [temp_repo / "app.ts"]
        errors, cached = await type_check_gate.run_tsc(files)

        assert errors == []
        assert cached == []

    def test_find_tsconfig_for_files(self, type_check_gate: TypeCheckGate, temp_repo: Path) -> None:
        """Test finding tsconfig.json for TypeScript files."""
        # Create nested structure
        (temp_repo / "src").mkdir()
        (temp_repo / "src" / "components").mkdir()

        # Create tsconfig at root
        (temp_repo / "tsconfig.json").write_text(json.dumps({}))

        # Create files
        file1 = temp_repo / "src" / "app.ts"
        file2 = temp_repo / "src" / "components" / "Button.tsx"

        tsconfig_map = type_check_gate._find_tsconfig_for_files([file1, file2])

        # Both files should map to root tsconfig
        assert len(tsconfig_map) == 1
        assert temp_repo in tsconfig_map
        assert len(tsconfig_map[temp_repo]) == 2

    def test_find_tsconfig_for_files_multiple_configs(
        self, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test finding multiple tsconfig.json files."""
        # Create nested structure
        (temp_repo / "frontend").mkdir()
        (temp_repo / "backend").mkdir()

        # Create tsconfig in each
        (temp_repo / "frontend" / "tsconfig.json").write_text(json.dumps({}))
        (temp_repo / "backend" / "tsconfig.json").write_text(json.dumps({}))

        # Create files
        file1 = temp_repo / "frontend" / "app.ts"
        file2 = temp_repo / "backend" / "server.ts"

        tsconfig_map = type_check_gate._find_tsconfig_for_files([file1, file2])

        # Should find separate configs
        assert len(tsconfig_map) == 2
        assert (temp_repo / "frontend") in tsconfig_map
        assert (temp_repo / "backend") in tsconfig_map

    def test_find_tsconfig_for_files_no_config(
        self, type_check_gate: TypeCheckGate, temp_repo: Path
    ) -> None:
        """Test handling files without tsconfig.json."""
        file1 = temp_repo / "app.ts"

        tsconfig_map = type_check_gate._find_tsconfig_for_files([file1])

        # Should return empty map
        assert len(tsconfig_map) == 0

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.get_staged_files")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.run_mypy")
    async def test_run_gate_no_files(
        self,
        mock_mypy: AsyncMock,
        mock_get_files: AsyncMock,
        type_check_gate: TypeCheckGate,
    ) -> None:
        """Test running gate with no staged files."""
        mock_get_files.return_value = []

        result = await type_check_gate.run()

        assert result.passed is True
        assert result.files_checked == 0
        mock_mypy.assert_not_called()

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.get_staged_files")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.run_mypy")
    async def test_run_gate_python_files_pass(
        self,
        mock_mypy: AsyncMock,
        mock_get_files: AsyncMock,
        type_check_gate: TypeCheckGate,
        temp_repo: Path,
    ) -> None:
        """Test running gate with Python files that pass."""
        mock_get_files.return_value = [temp_repo / "test.py"]
        mock_mypy.return_value = ([], [])  # No errors

        result = await type_check_gate.run()

        assert result.passed is True
        assert result.files_checked == 1
        assert result.error_count == 0

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.get_staged_files")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.run_mypy")
    async def test_run_gate_python_files_fail(
        self,
        mock_mypy: AsyncMock,
        mock_get_files: AsyncMock,
        type_check_gate: TypeCheckGate,
        temp_repo: Path,
    ) -> None:
        """Test running gate with Python files that have errors."""
        mock_get_files.return_value = [temp_repo / "test.py"]

        # Mock error
        error = TypeCheckError(
            file="test.py",
            line=10,
            column=5,
            code="assignment",
            message="Incompatible types",
            severity="error",
        )
        mock_mypy.return_value = ([error], [])

        result = await type_check_gate.run()

        assert result.passed is False
        assert result.files_checked == 1
        assert result.error_count == 1

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.get_staged_files")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.run_mypy")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.run_tsc")
    async def test_run_gate_mixed_files(
        self,
        mock_tsc: AsyncMock,
        mock_mypy: AsyncMock,
        mock_get_files: AsyncMock,
        type_check_gate: TypeCheckGate,
        temp_repo: Path,
    ) -> None:
        """Test running gate with mixed Python and TypeScript files."""
        mock_get_files.return_value = [
            temp_repo / "test.py",
            temp_repo / "app.ts",
        ]
        mock_mypy.return_value = ([], [])
        mock_tsc.return_value = ([], [])

        result = await type_check_gate.run()

        assert result.passed is True
        assert result.files_checked == 2
        mock_mypy.assert_called_once()
        mock_tsc.assert_called_once()

    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.get_staged_files")
    @patch("forge_harness.quality_gates.type_check.TypeCheckGate.run_mypy")
    async def test_run_gate_warnings_pass(
        self,
        mock_mypy: AsyncMock,
        mock_get_files: AsyncMock,
        type_check_gate: TypeCheckGate,
        temp_repo: Path,
    ) -> None:
        """Test that warnings don't fail the gate."""
        mock_get_files.return_value = [temp_repo / "test.py"]

        # Mock warning (not error)
        warning = TypeCheckError(
            file="test.py",
            line=10,
            column=5,
            code="var-annotated",
            message="Missing type annotation",
            severity="warning",
        )
        mock_mypy.return_value = ([warning], [])

        result = await type_check_gate.run()

        assert result.passed is True  # Warnings don't fail
        assert result.warning_count == 1


class TestTypeCheckResult:
    """Test TypeCheckResult class."""

    def test_type_check_result_passed(self) -> None:
        """Test TypeCheckResult when passed."""
        result = TypeCheckResult(passed=True, files_checked=5)
        assert result.passed is True
        assert result.error_count == 0
        assert result.warning_count == 0

    def test_type_check_result_with_errors(self) -> None:
        """Test TypeCheckResult with errors."""
        errors = [
            TypeCheckError(
                file="test.py",
                line=10,
                column=5,
                code="assignment",
                message="Incompatible types",
                severity="error",
            ),
            TypeCheckError(
                file="test.py",
                line=20,
                column=1,
                code="var-annotated",
                message="Missing annotation",
                severity="warning",
            ),
        ]
        result = TypeCheckResult(passed=False, files_checked=1, errors=errors)

        assert result.passed is False
        assert result.error_count == 1
        assert result.warning_count == 1

    def test_type_check_result_with_cache(self) -> None:
        """Test TypeCheckResult with cached files."""
        result = TypeCheckResult(
            passed=True,
            files_checked=5,
            cached_files=3,
        )

        assert result.passed is True
        assert result.cached_files == 3

    def test_type_check_result_summary(self) -> None:
        """Test summary generation."""
        result = TypeCheckResult(passed=True, files_checked=3)
        summary = result.summary()

        assert "PASSED" in summary
        assert "Files checked: 3" in summary

    def test_type_check_result_summary_with_cache(self) -> None:
        """Test summary generation with cached files."""
        result = TypeCheckResult(
            passed=True,
            files_checked=5,
            cached_files=2,
        )
        summary = result.summary()

        assert "Files cached: 2" in summary


class TestTypeCheckError:
    """Test TypeCheckError class."""

    def test_type_check_error_str(self) -> None:
        """Test string representation of TypeCheckError."""
        error = TypeCheckError(
            file="test.py",
            line=10,
            column=5,
            code="assignment",
            message="Incompatible types",
            severity="error",
        )

        error_str = str(error)
        assert "test.py:10:5" in error_str
        assert "assignment" in error_str
        assert "Incompatible types" in error_str

    def test_type_check_error_str_no_column(self) -> None:
        """Test string representation without column."""
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


@pytest.mark.asyncio
async def test_run_type_check_gate_factory() -> None:
    """Test run_type_check_gate factory function."""
    with patch("forge_harness.quality_gates.type_check.TypeCheckGate") as mock_gate_class:
        mock_gate = MagicMock()
        mock_gate.run = AsyncMock(return_value=TypeCheckResult(passed=True, files_checked=0))
        mock_gate_class.return_value = mock_gate

        result = await run_type_check_gate(use_cache=True)

        assert result.passed is True
        mock_gate.run.assert_called_once()


@pytest.mark.asyncio
async def test_run_type_check_gate_with_cache_disabled() -> None:
    """Test run_type_check_gate with caching disabled."""
    with patch("forge_harness.quality_gates.type_check.TypeCheckGate") as mock_gate_class:
        mock_gate = MagicMock()
        mock_gate.run = AsyncMock(return_value=TypeCheckResult(passed=True, files_checked=0))
        mock_gate_class.return_value = mock_gate

        result = await run_type_check_gate(use_cache=False)

        assert result.passed is True
        # Verify cache was disabled
        mock_gate_class.assert_called_once()
        call_kwargs = mock_gate_class.call_args[1]
        assert call_kwargs["use_cache"] is False
