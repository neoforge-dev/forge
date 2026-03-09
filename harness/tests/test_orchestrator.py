"""Comprehensive tests for quality gate orchestrator."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

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

# ============================================================================
# Enum and Dataclass Tests
# ============================================================================


class TestGateStatus:
    """Tests for GateStatus enum."""

    def test_gate_status_values(self):
        """Test all GateStatus enum values exist."""
        assert GateStatus.PENDING == "pending"
        assert GateStatus.RUNNING == "running"
        assert GateStatus.PASSED == "passed"
        assert GateStatus.FAILED == "failed"
        assert GateStatus.SKIPPED == "skipped"

    def test_gate_status_is_string_enum(self):
        """Test GateStatus inherits from str."""
        assert isinstance(GateStatus.PASSED, str)
        # GateStatus.PASSED equals "passed" but str() includes the enum name
        assert GateStatus.PASSED == "passed"

    def test_gate_status_comparison(self):
        """Test GateStatus equality comparisons."""
        assert GateStatus.PASSED == "passed"
        assert GateStatus.FAILED != GateStatus.PASSED
        assert GateStatus.PENDING == GateStatus.PENDING


class TestGateResult:
    """Tests for GateResult dataclass."""

    def test_gate_result_basic_creation(self):
        """Test creating a basic GateResult."""
        result = GateResult(gate_name="lint", status=GateStatus.PASSED)
        assert result.gate_name == "lint"
        assert result.status == GateStatus.PASSED
        assert result.duration == 0.0
        assert result.details is None
        assert result.error is None

    def test_gate_result_with_duration(self):
        """Test GateResult with duration."""
        result = GateResult(gate_name="type_check", status=GateStatus.PASSED, duration=1.5)
        assert result.duration == 1.5

    def test_gate_result_with_details(self):
        """Test GateResult with details."""
        details = {"files": 10, "issues": 0}
        result = GateResult(gate_name="security", status=GateStatus.PASSED, details=details)
        assert result.details == details

    def test_gate_result_with_error(self):
        """Test GateResult with error."""
        result = GateResult(gate_name="tests", status=GateStatus.FAILED, error="Test suite failed")
        assert result.error == "Test suite failed"

    def test_gate_result_full_fields(self):
        """Test GateResult with all fields."""
        result = GateResult(
            gate_name="lint",
            status=GateStatus.FAILED,
            duration=2.3,
            details={"files_checked": 5},
            error="Linting errors found",
        )
        assert result.gate_name == "lint"
        assert result.status == GateStatus.FAILED
        assert result.duration == 2.3
        assert result.details == {"files_checked": 5}
        assert result.error == "Linting errors found"


class TestOrchestratorResult:
    """Tests for OrchestratorResult dataclass."""

    def test_orchestrator_result_basic_creation(self):
        """Test creating a basic OrchestratorResult."""
        result = OrchestratorResult(overall_success=True)
        assert result.overall_success is True
        assert result.gate_results == []
        assert result.total_duration == 0.0
        assert isinstance(result.started_at, datetime)
        assert result.completed_at is None

    def test_orchestrator_result_with_gates(self):
        """Test OrchestratorResult with gate results."""
        gate_results = [
            GateResult("lint", GateStatus.PASSED),
            GateResult("type_check", GateStatus.PASSED),
        ]
        result = OrchestratorResult(
            overall_success=True, gate_results=gate_results, total_duration=3.5
        )
        assert len(result.gate_results) == 2
        assert result.total_duration == 3.5

    def test_passed_gates_property(self):
        """Test passed_gates property counts correctly."""
        gate_results = [
            GateResult("lint", GateStatus.PASSED),
            GateResult("type_check", GateStatus.PASSED),
            GateResult("security", GateStatus.FAILED),
            GateResult("tests", GateStatus.PASSED),
        ]
        result = OrchestratorResult(overall_success=False, gate_results=gate_results)
        assert result.passed_gates == 3

    def test_failed_gates_property(self):
        """Test failed_gates property counts correctly."""
        gate_results = [
            GateResult("lint", GateStatus.PASSED),
            GateResult("type_check", GateStatus.FAILED),
            GateResult("security", GateStatus.FAILED),
            GateResult("tests", GateStatus.PASSED),
        ]
        result = OrchestratorResult(overall_success=False, gate_results=gate_results)
        assert result.failed_gates == 2

    def test_passed_gates_with_skipped(self):
        """Test passed_gates doesn't count skipped gates."""
        gate_results = [
            GateResult("lint", GateStatus.PASSED),
            GateResult("type_check", GateStatus.SKIPPED),
            GateResult("security", GateStatus.PASSED),
        ]
        result = OrchestratorResult(overall_success=True, gate_results=gate_results)
        assert result.passed_gates == 2
        assert result.failed_gates == 0

    def test_empty_gate_results(self):
        """Test properties with empty gate results."""
        result = OrchestratorResult(overall_success=True)
        assert result.passed_gates == 0
        assert result.failed_gates == 0


class TestOrchestratorConfig:
    """Tests for OrchestratorConfig dataclass."""

    def test_config_defaults(self):
        """Test OrchestratorConfig default values."""
        config = OrchestratorConfig()
        assert config.run_lint is True
        assert config.run_type_check is True
        assert config.run_security is True
        assert config.run_tests is True
        assert config.parallel is False
        assert config.fail_fast is False
        assert config.type_config is None
        assert config.security_config is None
        assert config.test_config is None

    def test_config_custom_values(self):
        """Test OrchestratorConfig with custom values."""
        config = OrchestratorConfig(
            run_lint=False,
            run_type_check=True,
            run_security=False,
            run_tests=True,
            parallel=True,
            fail_fast=True,
        )
        assert config.run_lint is False
        assert config.run_type_check is True
        assert config.run_security is False
        assert config.run_tests is True
        assert config.parallel is True
        assert config.fail_fast is True

    def test_config_with_subconfigs(self):
        """Test OrchestratorConfig with nested configs."""
        from forge_harness.quality_gates.security_gate import SecurityScannerConfig
        from forge_harness.quality_gates.test_runner import TestRunnerConfig
        from forge_harness.quality_gates.type_gate import TypeCheckerConfig

        type_config = TypeCheckerConfig(strict=True)
        security_config = SecurityScannerConfig(timeout=120)
        test_config = TestRunnerConfig(pattern="test_*.py")

        config = OrchestratorConfig(
            type_config=type_config, security_config=security_config, test_config=test_config
        )
        assert config.type_config.strict is True
        assert config.security_config.timeout == 120
        assert config.test_config.pattern == "test_*.py"


# ============================================================================
# QualityGateOrchestrator Tests
# ============================================================================


class TestQualityGateOrchestratorInit:
    """Tests for QualityGateOrchestrator initialization."""

    def test_orchestrator_init_default_config(self):
        """Test orchestrator initializes with default config."""
        orchestrator = QualityGateOrchestrator()
        assert orchestrator.config is not None
        assert orchestrator.config.run_lint is True
        assert orchestrator._type_checker is not None
        assert orchestrator._security_scanner is not None
        assert orchestrator._test_runner is not None

    def test_orchestrator_init_custom_config(self):
        """Test orchestrator initializes with custom config."""
        config = OrchestratorConfig(run_lint=False, parallel=True, fail_fast=True)
        orchestrator = QualityGateOrchestrator(config)
        assert orchestrator.config.run_lint is False
        assert orchestrator.config.parallel is True
        assert orchestrator.config.fail_fast is True

    def test_orchestrator_init_with_nested_configs(self):
        """Test orchestrator initializes with nested configs."""
        from forge_harness.quality_gates.type_gate import TypeCheckerConfig

        type_config = TypeCheckerConfig(strict=True)
        config = OrchestratorConfig(type_config=type_config)
        orchestrator = QualityGateOrchestrator(config)
        assert orchestrator._type_checker.config.strict is True


class TestRunLintGate:
    """Tests for run_lint_gate method."""

    @pytest.mark.asyncio
    async def test_run_lint_gate_success(self):
        """Test run_lint_gate with successful lint."""
        orchestrator = QualityGateOrchestrator()

        mock_lint_result = LintResult(passed=True, errors=[], auto_fixed=0)

        with patch(
            "forge_harness.quality_gates.orchestrator.lint_staged_files",
            return_value=mock_lint_result,
        ):
            result = await orchestrator.run_lint_gate()

        assert result.gate_name == "lint"
        assert result.status == GateStatus.PASSED
        assert result.duration >= 0
        assert result.details == mock_lint_result
        assert result.error is None

    @pytest.mark.asyncio
    async def test_run_lint_gate_failure(self):
        """Test run_lint_gate with lint failures."""
        orchestrator = QualityGateOrchestrator()

        mock_lint_result = LintResult(
            passed=False, errors=["file.py:10:E501 line too long"], auto_fixed=0
        )

        with patch(
            "forge_harness.quality_gates.orchestrator.lint_staged_files",
            return_value=mock_lint_result,
        ):
            result = await orchestrator.run_lint_gate()

        assert result.gate_name == "lint"
        assert result.status == GateStatus.FAILED
        assert result.details == mock_lint_result
        assert result.error is None

    @pytest.mark.asyncio
    async def test_run_lint_gate_exception(self):
        """Test run_lint_gate handles exceptions."""
        orchestrator = QualityGateOrchestrator()

        with patch(
            "forge_harness.quality_gates.orchestrator.lint_staged_files",
            side_effect=RuntimeError("Lint failed"),
        ):
            result = await orchestrator.run_lint_gate()

        assert result.gate_name == "lint"
        assert result.status == GateStatus.FAILED
        assert result.error == "Lint failed"
        assert result.details is None


class TestRunTypeGate:
    """Tests for run_type_gate method."""

    @pytest.mark.asyncio
    async def test_run_type_gate_success(self):
        """Test run_type_gate with successful type check."""
        orchestrator = QualityGateOrchestrator()

        mock_result = TypeCheckResult(success=True, errors=[], file_count=5)
        orchestrator._type_checker.check_staged = AsyncMock(return_value=mock_result)

        result = await orchestrator.run_type_gate()

        assert result.gate_name == "type_check"
        assert result.status == GateStatus.PASSED
        assert result.details == mock_result
        assert result.error is None

    @pytest.mark.asyncio
    async def test_run_type_gate_with_paths(self):
        """Test run_type_gate with specific paths."""
        orchestrator = QualityGateOrchestrator()

        paths = [Path("src/module.py"), Path("src/utils.py")]
        mock_result = TypeCheckResult(success=True, errors=[], file_count=2)
        orchestrator._type_checker.check_files = AsyncMock(return_value=mock_result)

        result = await orchestrator.run_type_gate(paths)

        assert result.status == GateStatus.PASSED
        orchestrator._type_checker.check_files.assert_called_once_with(paths)

    @pytest.mark.asyncio
    async def test_run_type_gate_failure(self):
        """Test run_type_gate with type errors."""
        orchestrator = QualityGateOrchestrator()

        from forge_harness.quality_gates.type_gate import TypeCheckError

        error = TypeCheckError(
            file="src/module.py",
            line=10,
            column=5,
            code="arg-type",
            message="Argument type incompatible",
        )
        mock_result = TypeCheckResult(success=False, errors=[error], file_count=1)
        orchestrator._type_checker.check_staged = AsyncMock(return_value=mock_result)

        result = await orchestrator.run_type_gate()

        assert result.gate_name == "type_check"
        assert result.status == GateStatus.FAILED
        assert result.details == mock_result

    @pytest.mark.asyncio
    async def test_run_type_gate_exception(self):
        """Test run_type_gate handles exceptions."""
        orchestrator = QualityGateOrchestrator()

        orchestrator._type_checker.check_staged = AsyncMock(
            side_effect=ValueError("Type check failed")
        )

        result = await orchestrator.run_type_gate()

        assert result.gate_name == "type_check"
        assert result.status == GateStatus.FAILED
        assert result.error == "Type check failed"


class TestRunSecurityGate:
    """Tests for run_security_gate method."""

    @pytest.mark.asyncio
    async def test_run_security_gate_success(self):
        """Test run_security_gate with no vulnerabilities."""
        orchestrator = QualityGateOrchestrator()

        mock_result = SecurityResult(success=True, vulnerabilities=[], severity_counts={})
        orchestrator._security_scanner.scan_staged = AsyncMock(return_value=mock_result)

        result = await orchestrator.run_security_gate()

        assert result.gate_name == "security"
        assert result.status == GateStatus.PASSED
        assert result.details == mock_result

    @pytest.mark.asyncio
    async def test_run_security_gate_with_paths(self):
        """Test run_security_gate with specific paths."""
        orchestrator = QualityGateOrchestrator()

        paths = [Path("src/api.py")]
        mock_result = SecurityResult(success=True, vulnerabilities=[])
        orchestrator._security_scanner.scan_files = AsyncMock(return_value=mock_result)

        result = await orchestrator.run_security_gate(paths)

        assert result.status == GateStatus.PASSED
        orchestrator._security_scanner.scan_files.assert_called_once_with(paths)

    @pytest.mark.asyncio
    async def test_run_security_gate_failure(self):
        """Test run_security_gate with vulnerabilities."""
        orchestrator = QualityGateOrchestrator()

        from forge_harness.quality_gates.security_gate import Severity, Vulnerability

        vuln = Vulnerability(
            file="src/auth.py",
            line=42,
            code="B501",
            severity=Severity.HIGH,
            message="Use of weak crypto",
        )
        mock_result = SecurityResult(
            success=False, vulnerabilities=[vuln], severity_counts={"HIGH": 1}
        )
        orchestrator._security_scanner.scan_staged = AsyncMock(return_value=mock_result)

        result = await orchestrator.run_security_gate()

        assert result.gate_name == "security"
        assert result.status == GateStatus.FAILED
        assert result.details == mock_result

    @pytest.mark.asyncio
    async def test_run_security_gate_exception(self):
        """Test run_security_gate handles exceptions."""
        orchestrator = QualityGateOrchestrator()

        orchestrator._security_scanner.scan_staged = AsyncMock(side_effect=OSError("Scan failed"))

        result = await orchestrator.run_security_gate()

        assert result.gate_name == "security"
        assert result.status == GateStatus.FAILED
        assert result.error == "Scan failed"


class TestRunTestGate:
    """Tests for run_test_gate method."""

    @pytest.mark.asyncio
    async def test_run_test_gate_success(self):
        """Test run_test_gate with passing tests."""
        orchestrator = QualityGateOrchestrator()

        # Create mock with proper TestResult attributes
        mock_result = Mock(spec=TestResult)
        mock_result.passed = 10
        mock_result.failed = 0
        mock_result.errors = 0
        mock_result.skipped = 0
        mock_result.duration = 2.5
        mock_result.coverage_pct = 85.0
        mock_result.exit_code = 0
        mock_result.output = "All tests passed"
        mock_result.success = True

        with patch.object(orchestrator._test_runner, "run_tests", return_value=mock_result):
            result = await orchestrator.run_test_gate()

        assert result.gate_name == "tests"
        assert result.status == GateStatus.PASSED
        assert result.details == mock_result

    @pytest.mark.asyncio
    async def test_run_test_gate_with_paths(self):
        """Test run_test_gate with specific paths."""
        orchestrator = QualityGateOrchestrator()

        paths = [Path("tests/unit/")]

        mock_result = Mock(spec=TestResult)
        mock_result.passed = 5
        mock_result.failed = 0
        mock_result.errors = 0
        mock_result.skipped = 0
        mock_result.duration = 1.0
        mock_result.coverage_pct = 90.0
        mock_result.exit_code = 0
        mock_result.output = "Tests passed"
        mock_result.success = True

        mock_run_tests = Mock(return_value=mock_result)
        with patch.object(orchestrator._test_runner, "run_tests", mock_run_tests):
            result = await orchestrator.run_test_gate(paths)

        assert result.status == GateStatus.PASSED
        mock_run_tests.assert_called_once_with(paths)

    @pytest.mark.asyncio
    async def test_run_test_gate_failure(self):
        """Test run_test_gate with test failures."""
        orchestrator = QualityGateOrchestrator()

        mock_result = Mock(spec=TestResult)
        mock_result.passed = 8
        mock_result.failed = 2
        mock_result.errors = 0
        mock_result.skipped = 0
        mock_result.duration = 3.0
        mock_result.coverage_pct = 70.0
        mock_result.exit_code = 1
        mock_result.output = "2 tests failed"
        mock_result.success = False

        with patch.object(orchestrator._test_runner, "run_tests", return_value=mock_result):
            result = await orchestrator.run_test_gate()

        assert result.gate_name == "tests"
        assert result.status == GateStatus.FAILED
        assert result.details == mock_result

    @pytest.mark.asyncio
    async def test_run_test_gate_exception(self):
        """Test run_test_gate handles exceptions."""
        orchestrator = QualityGateOrchestrator()

        with patch.object(
            orchestrator._test_runner, "run_tests", side_effect=RuntimeError("Tests crashed")
        ):
            result = await orchestrator.run_test_gate()

        assert result.gate_name == "tests"
        assert result.status == GateStatus.FAILED
        assert result.error == "Tests crashed"


class TestRunAllSequential:
    """Tests for run_all method in sequential mode."""

    @pytest.mark.asyncio
    async def test_run_all_all_gates_pass(self):
        """Test run_all when all gates pass."""
        config = OrchestratorConfig(parallel=False)
        orchestrator = QualityGateOrchestrator(config)

        # Mock all gates to pass
        orchestrator.run_lint_gate = AsyncMock(return_value=GateResult("lint", GateStatus.PASSED))
        orchestrator.run_type_gate = AsyncMock(
            return_value=GateResult("type_check", GateStatus.PASSED)
        )
        orchestrator.run_security_gate = AsyncMock(
            return_value=GateResult("security", GateStatus.PASSED)
        )
        orchestrator.run_test_gate = AsyncMock(return_value=GateResult("tests", GateStatus.PASSED))

        result = await orchestrator.run_all()

        assert result.overall_success is True
        assert len(result.gate_results) == 4
        assert result.passed_gates == 4
        assert result.failed_gates == 0
        assert result.completed_at is not None

    @pytest.mark.asyncio
    async def test_run_all_one_gate_fails(self):
        """Test run_all when one gate fails."""
        config = OrchestratorConfig(parallel=False)
        orchestrator = QualityGateOrchestrator(config)

        orchestrator.run_lint_gate = AsyncMock(return_value=GateResult("lint", GateStatus.PASSED))
        orchestrator.run_type_gate = AsyncMock(
            return_value=GateResult("type_check", GateStatus.FAILED)
        )
        orchestrator.run_security_gate = AsyncMock(
            return_value=GateResult("security", GateStatus.PASSED)
        )
        orchestrator.run_test_gate = AsyncMock(return_value=GateResult("tests", GateStatus.PASSED))

        result = await orchestrator.run_all()

        assert result.overall_success is False
        assert len(result.gate_results) == 4
        assert result.passed_gates == 3
        assert result.failed_gates == 1

    @pytest.mark.asyncio
    async def test_run_all_selective_gates(self):
        """Test run_all with selective gates enabled."""
        config = OrchestratorConfig(
            run_lint=True, run_type_check=False, run_security=True, run_tests=False, parallel=False
        )
        orchestrator = QualityGateOrchestrator(config)

        orchestrator.run_lint_gate = AsyncMock(return_value=GateResult("lint", GateStatus.PASSED))
        orchestrator.run_security_gate = AsyncMock(
            return_value=GateResult("security", GateStatus.PASSED)
        )

        result = await orchestrator.run_all()

        assert len(result.gate_results) == 2
        assert all(r.gate_name in ["lint", "security"] for r in result.gate_results)

    @pytest.mark.asyncio
    async def test_run_all_with_paths(self):
        """Test run_all passes paths to gates."""
        config = OrchestratorConfig(parallel=False)
        orchestrator = QualityGateOrchestrator(config)

        paths = [Path("src/module.py")]

        orchestrator.run_lint_gate = AsyncMock(return_value=GateResult("lint", GateStatus.PASSED))
        orchestrator.run_type_gate = AsyncMock(
            return_value=GateResult("type_check", GateStatus.PASSED)
        )
        orchestrator.run_security_gate = AsyncMock(
            return_value=GateResult("security", GateStatus.PASSED)
        )
        orchestrator.run_test_gate = AsyncMock(return_value=GateResult("tests", GateStatus.PASSED))

        result = await orchestrator.run_all(paths)

        orchestrator.run_type_gate.assert_called_once_with(paths)
        orchestrator.run_security_gate.assert_called_once_with(paths)
        orchestrator.run_test_gate.assert_called_once_with(paths)

    @pytest.mark.asyncio
    async def test_run_all_measures_duration(self):
        """Test run_all measures total duration."""
        config = OrchestratorConfig(parallel=False)
        orchestrator = QualityGateOrchestrator(config)

        async def slow_gate():
            await asyncio.sleep(0.1)
            return GateResult("slow", GateStatus.PASSED, duration=0.1)

        orchestrator.run_lint_gate = AsyncMock(side_effect=slow_gate)
        orchestrator.run_type_gate = AsyncMock(
            return_value=GateResult("type_check", GateStatus.PASSED)
        )
        orchestrator.run_security_gate = AsyncMock(
            return_value=GateResult("security", GateStatus.PASSED)
        )
        orchestrator.run_test_gate = AsyncMock(return_value=GateResult("tests", GateStatus.PASSED))

        result = await orchestrator.run_all()

        assert result.total_duration >= 0.1
        assert result.started_at < result.completed_at


class TestRunAllFailFast:
    """Tests for run_all with fail_fast enabled."""

    @pytest.mark.asyncio
    async def test_fail_fast_stops_on_first_failure(self):
        """Test fail_fast stops after first failure."""
        config = OrchestratorConfig(fail_fast=True, parallel=False)
        orchestrator = QualityGateOrchestrator(config)

        # Track which gates were called
        called_gates = []

        # Create coroutines that return gate results
        async def mock_lint():
            called_gates.append("lint")
            return GateResult("lint", GateStatus.PASSED)

        async def mock_type(paths=None):
            called_gates.append("type_check")
            return GateResult("type_check", GateStatus.FAILED)

        async def mock_security(paths=None):
            called_gates.append("security")
            return GateResult("security", GateStatus.PASSED)

        async def mock_test(paths=None):
            called_gates.append("tests")
            return GateResult("tests", GateStatus.PASSED)

        orchestrator.run_lint_gate = mock_lint
        orchestrator.run_type_gate = mock_type
        orchestrator.run_security_gate = mock_security
        orchestrator.run_test_gate = mock_test

        result = await orchestrator.run_all()

        assert result.overall_success is False
        assert len(result.gate_results) == 2  # Only lint and type_check ran
        assert result.gate_results[1].status == GateStatus.FAILED

        # Security and tests should not have been called
        assert "lint" in called_gates
        assert "type_check" in called_gates
        assert "security" not in called_gates
        assert "tests" not in called_gates

    @pytest.mark.asyncio
    async def test_fail_fast_continues_when_passing(self):
        """Test fail_fast doesn't stop when gates pass."""
        config = OrchestratorConfig(fail_fast=True, parallel=False)
        orchestrator = QualityGateOrchestrator(config)

        orchestrator.run_lint_gate = AsyncMock(return_value=GateResult("lint", GateStatus.PASSED))
        orchestrator.run_type_gate = AsyncMock(
            return_value=GateResult("type_check", GateStatus.PASSED)
        )
        orchestrator.run_security_gate = AsyncMock(
            return_value=GateResult("security", GateStatus.PASSED)
        )
        orchestrator.run_test_gate = AsyncMock(return_value=GateResult("tests", GateStatus.PASSED))

        result = await orchestrator.run_all()

        assert result.overall_success is True
        assert len(result.gate_results) == 4  # All gates ran

    @pytest.mark.asyncio
    async def test_fail_fast_with_first_gate_failure(self):
        """Test fail_fast stops immediately if first gate fails."""
        config = OrchestratorConfig(fail_fast=True, parallel=False)
        orchestrator = QualityGateOrchestrator(config)

        orchestrator.run_lint_gate = AsyncMock(return_value=GateResult("lint", GateStatus.FAILED))
        orchestrator.run_type_gate = AsyncMock(
            return_value=GateResult("type_check", GateStatus.PASSED)
        )
        orchestrator.run_security_gate = AsyncMock(
            return_value=GateResult("security", GateStatus.PASSED)
        )
        orchestrator.run_test_gate = AsyncMock(return_value=GateResult("tests", GateStatus.PASSED))

        result = await orchestrator.run_all()

        assert len(result.gate_results) == 1
        assert result.gate_results[0].status == GateStatus.FAILED


class TestRunAllParallel:
    """Tests for run_all with parallel execution."""

    @pytest.mark.asyncio
    async def test_parallel_runs_all_gates_simultaneously(self):
        """Test parallel mode runs all gates at once."""
        config = OrchestratorConfig(parallel=True)
        orchestrator = QualityGateOrchestrator(config)

        # Track call order
        call_times = []

        async def gate_with_delay(name, delay):
            start = asyncio.get_event_loop().time()
            await asyncio.sleep(delay)
            call_times.append((name, start))
            return GateResult(name, GateStatus.PASSED, duration=delay)

        async def mock_lint():
            return await gate_with_delay("lint", 0.05)

        async def mock_type(paths=None):
            return await gate_with_delay("type_check", 0.05)

        async def mock_security(paths=None):
            return await gate_with_delay("security", 0.05)

        async def mock_test(paths=None):
            return await gate_with_delay("tests", 0.05)

        orchestrator.run_lint_gate = mock_lint
        orchestrator.run_type_gate = mock_type
        orchestrator.run_security_gate = mock_security
        orchestrator.run_test_gate = mock_test

        result = await orchestrator.run_all()

        assert result.overall_success is True
        assert len(result.gate_results) == 4

        # All gates should start at roughly the same time
        if len(call_times) >= 2:
            time_diff = abs(call_times[0][1] - call_times[1][1])
            assert time_diff < 0.01  # Started within 10ms

    @pytest.mark.asyncio
    async def test_parallel_collects_all_failures(self):
        """Test parallel mode collects all failures."""
        config = OrchestratorConfig(parallel=True)
        orchestrator = QualityGateOrchestrator(config)

        orchestrator.run_lint_gate = AsyncMock(return_value=GateResult("lint", GateStatus.FAILED))
        orchestrator.run_type_gate = AsyncMock(
            return_value=GateResult("type_check", GateStatus.FAILED)
        )
        orchestrator.run_security_gate = AsyncMock(
            return_value=GateResult("security", GateStatus.PASSED)
        )
        orchestrator.run_test_gate = AsyncMock(return_value=GateResult("tests", GateStatus.PASSED))

        result = await orchestrator.run_all()

        assert result.overall_success is False
        assert len(result.gate_results) == 4
        assert result.failed_gates == 2
        assert result.passed_gates == 2

    @pytest.mark.asyncio
    async def test_parallel_handles_exceptions(self):
        """Test parallel mode handles exceptions in gates."""
        config = OrchestratorConfig(parallel=True)
        orchestrator = QualityGateOrchestrator(config)

        orchestrator.run_lint_gate = AsyncMock(side_effect=RuntimeError("Lint crashed"))
        orchestrator.run_type_gate = AsyncMock(
            return_value=GateResult("type_check", GateStatus.PASSED)
        )
        orchestrator.run_security_gate = AsyncMock(
            return_value=GateResult("security", GateStatus.PASSED)
        )
        orchestrator.run_test_gate = AsyncMock(return_value=GateResult("tests", GateStatus.PASSED))

        result = await orchestrator.run_all()

        assert len(result.gate_results) == 4
        # The exception should be converted to a failed gate result
        lint_result = [r for r in result.gate_results if r.gate_name == "lint"][0]
        assert lint_result.status == GateStatus.FAILED
        assert "Lint crashed" in lint_result.error

    @pytest.mark.asyncio
    async def test_parallel_faster_than_sequential(self):
        """Test parallel execution is faster than sequential."""
        # Sequential run
        seq_config = OrchestratorConfig(parallel=False)
        seq_orchestrator = QualityGateOrchestrator(seq_config)

        async def slow_gate(name):
            await asyncio.sleep(0.05)
            return GateResult(name, GateStatus.PASSED, duration=0.05)

        async def seq_lint():
            return await slow_gate("lint")

        async def seq_type(paths=None):
            return await slow_gate("type_check")

        async def seq_security(paths=None):
            return await slow_gate("security")

        async def seq_test(paths=None):
            return await slow_gate("tests")

        seq_orchestrator.run_lint_gate = seq_lint
        seq_orchestrator.run_type_gate = seq_type
        seq_orchestrator.run_security_gate = seq_security
        seq_orchestrator.run_test_gate = seq_test

        seq_result = await seq_orchestrator.run_all()

        # Parallel run
        par_config = OrchestratorConfig(parallel=True)
        par_orchestrator = QualityGateOrchestrator(par_config)

        async def par_lint():
            return await slow_gate("lint")

        async def par_type(paths=None):
            return await slow_gate("type_check")

        async def par_security(paths=None):
            return await slow_gate("security")

        async def par_test(paths=None):
            return await slow_gate("tests")

        par_orchestrator.run_lint_gate = par_lint
        par_orchestrator.run_type_gate = par_type
        par_orchestrator.run_security_gate = par_security
        par_orchestrator.run_test_gate = par_test

        par_result = await par_orchestrator.run_all()

        # Parallel should be significantly faster
        # Sequential: ~0.2s (4 * 0.05), Parallel: ~0.05s
        assert par_result.total_duration < seq_result.total_duration


class TestCreateOrchestrator:
    """Tests for create_orchestrator factory function."""

    def test_create_orchestrator_default(self):
        """Test create_orchestrator with default config."""
        orchestrator = create_orchestrator()
        assert isinstance(orchestrator, QualityGateOrchestrator)
        assert orchestrator.config.run_lint is True
        assert orchestrator.config.parallel is False

    def test_create_orchestrator_with_config(self):
        """Test create_orchestrator with custom config."""
        config = OrchestratorConfig(run_lint=False, parallel=True, fail_fast=True)
        orchestrator = create_orchestrator(config)
        assert isinstance(orchestrator, QualityGateOrchestrator)
        assert orchestrator.config.run_lint is False
        assert orchestrator.config.parallel is True
        assert orchestrator.config.fail_fast is True

    def test_create_orchestrator_none_config(self):
        """Test create_orchestrator with None config."""
        orchestrator = create_orchestrator(None)
        assert isinstance(orchestrator, QualityGateOrchestrator)
        assert orchestrator.config is not None


class TestIntegrationScenarios:
    """Integration tests for realistic scenarios."""

    @pytest.mark.asyncio
    async def test_ci_pipeline_scenario(self):
        """Test typical CI pipeline scenario."""
        # CI typically runs all gates in parallel
        config = OrchestratorConfig(parallel=True)
        orchestrator = QualityGateOrchestrator(config)

        # Mock successful CI run
        orchestrator.run_lint_gate = AsyncMock(
            return_value=GateResult("lint", GateStatus.PASSED, duration=1.2)
        )
        orchestrator.run_type_gate = AsyncMock(
            return_value=GateResult("type_check", GateStatus.PASSED, duration=2.5)
        )
        orchestrator.run_security_gate = AsyncMock(
            return_value=GateResult("security", GateStatus.PASSED, duration=3.0)
        )
        orchestrator.run_test_gate = AsyncMock(
            return_value=GateResult("tests", GateStatus.PASSED, duration=5.0)
        )

        result = await orchestrator.run_all()

        assert result.overall_success is True
        assert result.passed_gates == 4
        assert result.failed_gates == 0

    @pytest.mark.asyncio
    async def test_pre_commit_hook_scenario(self):
        """Test pre-commit hook scenario with fail-fast."""
        # Pre-commit hooks should fail fast to give quick feedback
        config = OrchestratorConfig(
            fail_fast=True,
            parallel=False,
            run_tests=False,  # Pre-commit might skip tests for speed
        )
        orchestrator = QualityGateOrchestrator(config)

        # Track which gates were called
        called_gates = []

        async def mock_lint():
            called_gates.append("lint")
            return GateResult("lint", GateStatus.PASSED)

        async def mock_type(paths=None):
            called_gates.append("type_check")
            return GateResult("type_check", GateStatus.FAILED)

        async def mock_security(paths=None):
            called_gates.append("security")
            return GateResult("security", GateStatus.PASSED)

        orchestrator.run_lint_gate = mock_lint
        orchestrator.run_type_gate = mock_type
        orchestrator.run_security_gate = mock_security

        result = await orchestrator.run_all()

        assert result.overall_success is False
        assert len(result.gate_results) == 2  # Stopped after type check
        assert "lint" in called_gates
        assert "type_check" in called_gates
        assert "security" not in called_gates

    @pytest.mark.asyncio
    async def test_quick_lint_only_check(self):
        """Test quick lint-only check."""
        config = OrchestratorConfig(
            run_lint=True, run_type_check=False, run_security=False, run_tests=False
        )
        orchestrator = QualityGateOrchestrator(config)

        orchestrator.run_lint_gate = AsyncMock(return_value=GateResult("lint", GateStatus.PASSED))

        result = await orchestrator.run_all()

        assert len(result.gate_results) == 1
        assert result.gate_results[0].gate_name == "lint"

    @pytest.mark.asyncio
    async def test_mixed_results_scenario(self):
        """Test scenario with mixed pass/fail results."""
        config = OrchestratorConfig(parallel=True)
        orchestrator = QualityGateOrchestrator(config)

        orchestrator.run_lint_gate = AsyncMock(
            return_value=GateResult("lint", GateStatus.PASSED, duration=0.5)
        )
        orchestrator.run_type_gate = AsyncMock(
            return_value=GateResult(
                "type_check", GateStatus.FAILED, duration=1.0, error="Type errors found"
            )
        )
        orchestrator.run_security_gate = AsyncMock(
            return_value=GateResult(
                "security", GateStatus.FAILED, duration=0.8, error="Security issues"
            )
        )
        orchestrator.run_test_gate = AsyncMock(
            return_value=GateResult("tests", GateStatus.PASSED, duration=3.0)
        )

        result = await orchestrator.run_all()

        assert result.overall_success is False
        assert result.passed_gates == 2
        assert result.failed_gates == 2
        assert len([r for r in result.gate_results if r.error]) == 2
