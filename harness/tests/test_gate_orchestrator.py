"""Tests for quality gate orchestrator."""

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from forge_harness.quality_gates.lint_gate import LintResult
from forge_harness.quality_gates.orchestrator import (
    GateResult,
    GateStatus,
    OrchestratorConfig,
    OrchestratorResult,
    QualityGateOrchestrator,
    create_orchestrator,
)
from forge_harness.quality_gates.security_gate import SecurityResult
from forge_harness.quality_gates.test_runner import TestResult
from forge_harness.quality_gates.type_gate import TypeCheckResult


@pytest.fixture
def mock_lint_result():
    """Mock successful lint result."""
    return LintResult(passed=True, errors=[], auto_fixed=0)


@pytest.fixture
def mock_type_result():
    """Mock successful type check result."""
    return TypeCheckResult(success=True, errors=[], file_count=10, duration=1.0)


@pytest.fixture
def mock_security_result():
    """Mock successful security scan result."""
    return SecurityResult(success=True, vulnerabilities=[], severity_counts={}, duration=1.0)


@pytest.fixture
def mock_test_result():
    """Mock successful test result."""
    return TestResult(
        passed=25,
        failed=0,
        errors=0,
        skipped=0,
        duration=5.0,
        coverage_pct=80.0,
        exit_code=0,
        output="",
        failed_tests=[],
        timestamp=datetime.now(),
    )


@pytest.fixture
def failing_lint_result():
    """Mock failing lint result."""
    return LintResult(passed=False, errors=["Style violation"], auto_fixed=0)


@pytest.fixture
def failing_type_result():
    """Mock failing type check result."""
    return TypeCheckResult(success=False, errors=[], file_count=10, duration=1.0)


class TestGateResult:
    """Tests for GateResult dataclass."""

    def test_gate_result_creation(self):
        """Test creating a gate result."""
        result = GateResult(
            gate_name="lint", status=GateStatus.PASSED, duration=1.5, details={"checked": 5}
        )
        assert result.gate_name == "lint"
        assert result.status == GateStatus.PASSED
        assert result.duration == 1.5
        assert result.error is None

    def test_gate_result_with_error(self):
        """Test gate result with error."""
        result = GateResult(gate_name="security", status=GateStatus.FAILED, error="Scan failed")
        assert result.status == GateStatus.FAILED
        assert result.error == "Scan failed"


class TestOrchestratorResult:
    """Tests for OrchestratorResult dataclass."""

    def test_orchestrator_result_properties(self):
        """Test orchestrator result properties."""
        results = [
            GateResult(gate_name="lint", status=GateStatus.PASSED),
            GateResult(gate_name="type", status=GateStatus.PASSED),
            GateResult(gate_name="security", status=GateStatus.FAILED),
        ]
        orchestrator_result = OrchestratorResult(
            overall_success=False, gate_results=results, total_duration=5.5
        )
        assert orchestrator_result.passed_gates == 2
        assert orchestrator_result.failed_gates == 1
        assert orchestrator_result.total_duration == 5.5

    def test_orchestrator_result_all_passed(self):
        """Test orchestrator result when all gates pass."""
        results = [
            GateResult(gate_name="lint", status=GateStatus.PASSED),
            GateResult(gate_name="type", status=GateStatus.PASSED),
        ]
        orchestrator_result = OrchestratorResult(overall_success=True, gate_results=results)
        assert orchestrator_result.passed_gates == 2
        assert orchestrator_result.failed_gates == 0


class TestQualityGateOrchestrator:
    """Tests for QualityGateOrchestrator."""

    def test_orchestrator_initialization(self):
        """Test orchestrator initializes with default config."""
        orchestrator = QualityGateOrchestrator()
        assert orchestrator.config.run_lint is True
        assert orchestrator.config.run_type_check is True
        assert orchestrator.config.run_security is True
        assert orchestrator.config.run_tests is True
        assert orchestrator.config.parallel is False
        assert orchestrator.config.fail_fast is False

    def test_orchestrator_with_custom_config(self):
        """Test orchestrator with custom configuration."""
        config = OrchestratorConfig(
            run_lint=True, run_type_check=False, run_security=True, run_tests=False, parallel=True
        )
        orchestrator = QualityGateOrchestrator(config)
        assert orchestrator.config.run_type_check is False
        assert orchestrator.config.parallel is True

    @pytest.mark.asyncio
    async def test_run_lint_gate_success(self, mock_lint_result):
        """Test running lint gate successfully."""
        orchestrator = QualityGateOrchestrator()
        with patch(
            "forge_harness.quality_gates.orchestrator.lint_staged_files",
            return_value=mock_lint_result,
        ):
            result = await orchestrator.run_lint_gate()
            assert result.gate_name == "lint"
            assert result.status == GateStatus.PASSED
            assert result.error is None
            assert result.details == mock_lint_result

    @pytest.mark.asyncio
    async def test_run_lint_gate_failure(self, failing_lint_result):
        """Test running lint gate with failures."""
        orchestrator = QualityGateOrchestrator()
        with patch(
            "forge_harness.quality_gates.orchestrator.lint_staged_files",
            return_value=failing_lint_result,
        ):
            result = await orchestrator.run_lint_gate()
            assert result.gate_name == "lint"
            assert result.status == GateStatus.FAILED
            assert result.details == failing_lint_result

    @pytest.mark.asyncio
    async def test_run_lint_gate_exception(self):
        """Test lint gate handles exceptions."""
        orchestrator = QualityGateOrchestrator()
        with patch(
            "forge_harness.quality_gates.orchestrator.lint_staged_files",
            side_effect=Exception("Lint error"),
        ):
            result = await orchestrator.run_lint_gate()
            assert result.status == GateStatus.FAILED
            assert "Lint error" in result.error

    @pytest.mark.asyncio
    async def test_run_type_gate_success(self, mock_type_result):
        """Test running type check gate successfully."""
        orchestrator = QualityGateOrchestrator()
        orchestrator._type_checker.check_staged = AsyncMock(return_value=mock_type_result)
        result = await orchestrator.run_type_gate()
        assert result.gate_name == "type_check"
        assert result.status == GateStatus.PASSED
        assert result.details == mock_type_result

    @pytest.mark.asyncio
    async def test_run_type_gate_with_paths(self, mock_type_result):
        """Test running type check gate with specific paths."""
        orchestrator = QualityGateOrchestrator()
        orchestrator._type_checker.check_files = AsyncMock(return_value=mock_type_result)
        paths = [Path("src/main.py")]
        result = await orchestrator.run_type_gate(paths)
        assert result.status == GateStatus.PASSED
        orchestrator._type_checker.check_files.assert_called_once_with(paths)

    @pytest.mark.asyncio
    async def test_run_security_gate_success(self, mock_security_result):
        """Test running security gate successfully."""
        orchestrator = QualityGateOrchestrator()
        orchestrator._security_scanner.scan_staged = AsyncMock(return_value=mock_security_result)
        result = await orchestrator.run_security_gate()
        assert result.gate_name == "security"
        assert result.status == GateStatus.PASSED
        assert result.details == mock_security_result

    @pytest.mark.asyncio
    async def test_run_test_gate_success(self, mock_test_result):
        """Test running test gate successfully."""
        orchestrator = QualityGateOrchestrator()
        with patch.object(orchestrator._test_runner, "run_tests", return_value=mock_test_result):
            result = await orchestrator.run_test_gate()
            assert result.gate_name == "tests"
            assert result.status == GateStatus.PASSED
            assert result.details == mock_test_result

    @pytest.mark.asyncio
    async def test_run_all_sequential_success(
        self, mock_lint_result, mock_type_result, mock_security_result, mock_test_result
    ):
        """Test running all gates sequentially with success."""
        config = OrchestratorConfig(parallel=False)
        orchestrator = QualityGateOrchestrator(config)

        with patch(
            "forge_harness.quality_gates.orchestrator.lint_staged_files",
            return_value=mock_lint_result,
        ):
            orchestrator._type_checker.check_staged = AsyncMock(return_value=mock_type_result)
            orchestrator._security_scanner.scan_staged = AsyncMock(
                return_value=mock_security_result
            )
            with patch.object(
                orchestrator._test_runner, "run_tests", return_value=mock_test_result
            ):
                result = await orchestrator.run_all()

        assert result.overall_success is True
        assert len(result.gate_results) == 4
        assert result.passed_gates == 4
        assert result.failed_gates == 0
        assert result.completed_at is not None

    @pytest.mark.asyncio
    async def test_run_all_sequential_with_failure(
        self, mock_lint_result, failing_type_result, mock_security_result, mock_test_result
    ):
        """Test running all gates sequentially with one failure."""
        config = OrchestratorConfig(parallel=False)
        orchestrator = QualityGateOrchestrator(config)

        with patch(
            "forge_harness.quality_gates.orchestrator.lint_staged_files",
            return_value=mock_lint_result,
        ):
            orchestrator._type_checker.check_staged = AsyncMock(return_value=failing_type_result)
            orchestrator._security_scanner.scan_staged = AsyncMock(
                return_value=mock_security_result
            )
            with patch.object(
                orchestrator._test_runner, "run_tests", return_value=mock_test_result
            ):
                result = await orchestrator.run_all()

        assert result.overall_success is False
        assert result.passed_gates == 3
        assert result.failed_gates == 1

    @pytest.mark.asyncio
    async def test_run_all_parallel_success(
        self, mock_lint_result, mock_type_result, mock_security_result, mock_test_result
    ):
        """Test running all gates in parallel with success."""
        config = OrchestratorConfig(parallel=True)
        orchestrator = QualityGateOrchestrator(config)

        with patch(
            "forge_harness.quality_gates.orchestrator.lint_staged_files",
            return_value=mock_lint_result,
        ):
            orchestrator._type_checker.check_staged = AsyncMock(return_value=mock_type_result)
            orchestrator._security_scanner.scan_staged = AsyncMock(
                return_value=mock_security_result
            )
            with patch.object(
                orchestrator._test_runner, "run_tests", return_value=mock_test_result
            ):
                result = await orchestrator.run_all()

        assert result.overall_success is True
        assert len(result.gate_results) == 4
        assert result.passed_gates == 4

    @pytest.mark.asyncio
    async def test_run_all_parallel_with_exception(
        self, mock_lint_result, mock_type_result, mock_security_result
    ):
        """Test running gates in parallel handles exceptions."""
        config = OrchestratorConfig(parallel=True)
        orchestrator = QualityGateOrchestrator(config)

        with patch(
            "forge_harness.quality_gates.orchestrator.lint_staged_files",
            return_value=mock_lint_result,
        ):
            orchestrator._type_checker.check_staged = AsyncMock(return_value=mock_type_result)
            orchestrator._security_scanner.scan_staged = AsyncMock(
                return_value=mock_security_result
            )
            with patch.object(
                orchestrator._test_runner, "run_tests", side_effect=Exception("Test runner failed")
            ):
                result = await orchestrator.run_all()

        assert result.overall_success is False
        failed_gate = next(g for g in result.gate_results if g.status == GateStatus.FAILED)
        assert "Test runner failed" in failed_gate.error

    @pytest.mark.asyncio
    async def test_fail_fast_mode(self, mock_lint_result, failing_type_result):
        """Test fail-fast mode stops on first failure."""
        config = OrchestratorConfig(parallel=False, fail_fast=True)
        orchestrator = QualityGateOrchestrator(config)

        with patch(
            "forge_harness.quality_gates.orchestrator.lint_staged_files",
            return_value=mock_lint_result,
        ):
            orchestrator._type_checker.check_staged = AsyncMock(return_value=failing_type_result)
            # Security and test gates should not be called
            orchestrator._security_scanner.scan_staged = AsyncMock()
            with patch.object(orchestrator._test_runner, "run_tests") as mock_test:
                result = await orchestrator.run_all()

        assert result.overall_success is False
        # Should only have lint and type results (stopped after type failure)
        assert len(result.gate_results) == 2
        orchestrator._security_scanner.scan_staged.assert_not_called()
        mock_test.assert_not_called()

    @pytest.mark.asyncio
    async def test_selective_gate_execution(self, mock_lint_result):
        """Test running only selected gates."""
        config = OrchestratorConfig(
            run_lint=True, run_type_check=False, run_security=False, run_tests=False
        )
        orchestrator = QualityGateOrchestrator(config)

        with patch(
            "forge_harness.quality_gates.orchestrator.lint_staged_files",
            return_value=mock_lint_result,
        ):
            result = await orchestrator.run_all()

        assert len(result.gate_results) == 1
        assert result.gate_results[0].gate_name == "lint"

    @pytest.mark.asyncio
    async def test_run_all_with_specific_paths(self, mock_type_result, mock_security_result):
        """Test running gates with specific file paths."""
        config = OrchestratorConfig(run_lint=False, run_tests=False)
        orchestrator = QualityGateOrchestrator(config)

        paths = [Path("src/module.py"), Path("src/utils.py")]
        orchestrator._type_checker.check_files = AsyncMock(return_value=mock_type_result)
        orchestrator._security_scanner.scan_files = AsyncMock(return_value=mock_security_result)

        result = await orchestrator.run_all(paths)

        assert result.overall_success is True
        orchestrator._type_checker.check_files.assert_called_once_with(paths)
        orchestrator._security_scanner.scan_files.assert_called_once_with(paths)

    @pytest.mark.asyncio
    async def test_duration_tracking(self, mock_lint_result):
        """Test that gate durations are tracked correctly."""
        config = OrchestratorConfig(
            run_lint=True, run_type_check=False, run_security=False, run_tests=False
        )
        orchestrator = QualityGateOrchestrator(config)

        with patch(
            "forge_harness.quality_gates.orchestrator.lint_staged_files",
            return_value=mock_lint_result,
        ):
            result = await orchestrator.run_all()

        assert result.total_duration > 0
        assert result.gate_results[0].duration > 0
        assert result.started_at is not None
        assert result.completed_at is not None
        assert result.completed_at > result.started_at


class TestCreateOrchestrator:
    """Tests for orchestrator factory function."""

    def test_create_orchestrator_default(self):
        """Test creating orchestrator with defaults."""
        orchestrator = create_orchestrator()
        assert isinstance(orchestrator, QualityGateOrchestrator)
        assert orchestrator.config.run_lint is True

    def test_create_orchestrator_with_config(self):
        """Test creating orchestrator with custom config."""
        config = OrchestratorConfig(parallel=True, fail_fast=True)
        orchestrator = create_orchestrator(config)
        assert orchestrator.config.parallel is True
        assert orchestrator.config.fail_fast is True
