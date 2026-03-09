"""Tests for type checking quality gate."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from forge_harness.quality_gates.type_gate import (
    TypeChecker,
    TypeCheckerConfig,
    TypeCheckError,
    TypeCheckResult,
    create_type_checker,
)


@pytest.fixture
def type_checker():
    """Create a TypeChecker instance for testing."""
    config = TypeCheckerConfig(strict=False, timeout=10)
    return TypeChecker(config)


@pytest.fixture
def strict_type_checker():
    """Create a strict TypeChecker instance for testing."""
    config = TypeCheckerConfig(strict=True, timeout=10)
    return TypeChecker(config)


class TestTypeCheckerConfig:
    """Tests for TypeCheckerConfig dataclass."""

    def test_default_config(self):
        config = TypeCheckerConfig()
        assert config.strict is False
        assert config.ignore_patterns == []
        assert config.timeout == 300

    def test_custom_config(self):
        config = TypeCheckerConfig(
            strict=True, ignore_patterns=["*.test.py", "migrations/*"], timeout=60
        )
        assert config.strict is True
        assert config.ignore_patterns == ["*.test.py", "migrations/*"]
        assert config.timeout == 60


class TestTypeCheckError:
    """Tests for TypeCheckError dataclass."""

    def test_type_check_error_creation(self):
        error = TypeCheckError(
            file="test.py",
            line=10,
            column=5,
            code="type-arg",
            message="Argument 1 has incompatible type",
            severity="error",
        )
        assert error.file == "test.py"
        assert error.line == 10
        assert error.column == 5
        assert error.code == "type-arg"
        assert error.message == "Argument 1 has incompatible type"
        assert error.severity == "error"

    def test_default_severity(self):
        error = TypeCheckError(file="test.py", line=1, column=1, code="error", message="test")
        assert error.severity == "error"


class TestTypeCheckResult:
    """Tests for TypeCheckResult dataclass."""

    def test_successful_result(self):
        result = TypeCheckResult(success=True, errors=[], file_count=5, duration=1.5)
        assert result.success is True
        assert result.errors == []
        assert result.file_count == 5
        assert result.duration == 1.5

    def test_failed_result_with_errors(self):
        errors = [
            TypeCheckError(file="test.py", line=10, column=5, code="type-arg", message="Type error")
        ]
        result = TypeCheckResult(success=False, errors=errors, file_count=1, duration=0.5)
        assert result.success is False
        assert len(result.errors) == 1
        assert result.file_count == 1


class TestTypeChecker:
    """Tests for TypeChecker class."""

    def test_initialization_default_config(self):
        checker = TypeChecker()
        assert checker.config is not None
        assert checker.config.strict is False
        assert checker.config.timeout == 300

    def test_initialization_custom_config(self):
        config = TypeCheckerConfig(strict=True, timeout=60)
        checker = TypeChecker(config)
        assert checker.config.strict is True
        assert checker.config.timeout == 60

    def test_parse_output_empty(self, type_checker):
        """Test parsing empty mypy output."""
        errors = type_checker._parse_output("")
        assert errors == []

    def test_parse_output_success_message(self, type_checker):
        """Test parsing mypy success message."""
        output = "Found 0 errors in 5 files (checked 5 source files)"
        errors = type_checker._parse_output(output)
        assert errors == []

    def test_parse_output_with_errors(self, type_checker):
        """Test parsing mypy output with errors."""
        output = """test.py:10:5: error: Argument 1 has incompatible type
test.py:20:10: error: Name 'undefined' is not defined"""
        errors = type_checker._parse_output(output)
        assert len(errors) == 2
        assert errors[0].file == "test.py"
        assert errors[0].line == 10
        assert errors[0].column == 5
        assert "Argument 1" in errors[0].message
        assert errors[1].line == 20

    def test_parse_output_invalid_line_number(self, type_checker):
        """Test parsing mypy output with invalid line numbers."""
        output = "test.py:invalid:5: error: Some error"
        errors = type_checker._parse_output(output)
        assert len(errors) == 1
        assert errors[0].line == 0

    @pytest.mark.asyncio
    async def test_check_files_success(self, type_checker):
        """Test check_files with successful type checking."""
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            # Mock successful mypy run
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (
                b"Success: no issues found in 1 source file",
                b"",
            )
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            paths = [Path("test.py")]
            result = await type_checker.check_files(paths)

            assert result.success is True
            assert result.file_count == 1
            assert result.duration >= 0
            assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_check_files_with_errors(self, type_checker):
        """Test check_files with type errors."""
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            # Mock failed mypy run
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (
                b"test.py:10:5: error: Type error message",
                b"",
            )
            mock_process.returncode = 1
            mock_subprocess.return_value = mock_process

            paths = [Path("test.py")]
            result = await type_checker.check_files(paths)

            assert result.success is False
            assert result.file_count == 1
            assert len(result.errors) == 1
            assert result.errors[0].file == "test.py"

    @pytest.mark.asyncio
    async def test_check_files_strict_mode(self, strict_type_checker):
        """Test check_files with strict mode enabled."""
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"Success", b"")
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            paths = [Path("test.py")]
            await strict_type_checker.check_files(paths)

            # Verify --strict flag was added
            call_args = mock_subprocess.call_args[0]
            assert "--strict" in call_args

    @pytest.mark.asyncio
    async def test_check_files_timeout(self, type_checker):
        """Test check_files with timeout."""
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            paths = [Path("test.py")]
            result = await type_checker.check_files(paths)

            assert result.success is False
            assert result.file_count == 1
            assert result.duration == type_checker.config.timeout

    @pytest.mark.asyncio
    async def test_check_staged_no_files(self, type_checker):
        """Test check_staged with no staged Python files."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="", returncode=0)
            result = await type_checker.check_staged()

            assert result.success is True
            assert result.file_count == 0
            assert result.duration == 0.0

    @pytest.mark.asyncio
    async def test_check_staged_with_files(self, type_checker):
        """Test check_staged with staged Python files."""
        with (
            patch("subprocess.run") as mock_run,
            patch("asyncio.create_subprocess_exec") as mock_subprocess,
        ):
            # Mock git diff output
            mock_run.return_value = Mock(stdout="test1.py\ntest2.py\nREADME.md", returncode=0)

            # Mock mypy output
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"Success", b"")
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            result = await type_checker.check_staged()

            assert result.success is True
            assert result.file_count == 2  # Only .py files


class TestFactoryFunction:
    """Tests for create_type_checker factory function."""

    def test_create_type_checker_default(self):
        checker = create_type_checker()
        assert isinstance(checker, TypeChecker)
        assert checker.config.strict is False

    def test_create_type_checker_with_config(self):
        config = TypeCheckerConfig(strict=True, timeout=60)
        checker = create_type_checker(config)
        assert isinstance(checker, TypeChecker)
        assert checker.config.strict is True
        assert checker.config.timeout == 60
