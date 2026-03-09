"""Quality gate orchestrator for running all gates."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from forge_harness.logging_config import get_logger
from forge_harness.quality_gates.lint_gate import lint_staged_files
from forge_harness.quality_gates.security_gate import (
    SecurityScanner,
    SecurityScannerConfig,
)
from forge_harness.quality_gates.test_runner import TestRunner, TestRunnerConfig
from forge_harness.quality_gates.type_gate import TypeChecker, TypeCheckerConfig

logger = get_logger(__name__)


class GateStatus(str, Enum):
    """Status of a quality gate."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class GateResult:
    """Result from a single quality gate."""

    gate_name: str
    status: GateStatus
    duration: float = 0.0
    details: Any | None = None
    error: str | None = None


@dataclass
class OrchestratorResult:
    """Aggregated result from all quality gates."""

    overall_success: bool
    gate_results: list[GateResult] = field(default_factory=list)
    total_duration: float = 0.0
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None

    @property
    def passed_gates(self) -> int:
        return sum(1 for g in self.gate_results if g.status == GateStatus.PASSED)

    @property
    def failed_gates(self) -> int:
        return sum(1 for g in self.gate_results if g.status == GateStatus.FAILED)


@dataclass
class OrchestratorConfig:
    """Configuration for the quality gate orchestrator."""

    run_lint: bool = True
    run_type_check: bool = True
    run_security: bool = True
    run_tests: bool = True
    parallel: bool = False  # Run gates in parallel
    fail_fast: bool = False  # Stop on first failure
    type_config: TypeCheckerConfig | None = None
    security_config: SecurityScannerConfig | None = None
    test_config: TestRunnerConfig | None = None


class QualityGateOrchestrator:
    """Orchestrates running of all quality gates."""

    def __init__(self, config: OrchestratorConfig | None = None):
        self.config = config or OrchestratorConfig()
        self._type_checker = TypeChecker(self.config.type_config)
        self._security_scanner = SecurityScanner(self.config.security_config)
        self._test_runner = TestRunner(self.config.test_config or TestRunnerConfig())

    async def run_lint_gate(self) -> GateResult:
        """Run lint gate."""
        start = datetime.now()
        try:
            result = await asyncio.to_thread(lint_staged_files)
            return GateResult(
                gate_name="lint",
                status=GateStatus.PASSED if result.passed else GateStatus.FAILED,
                duration=(datetime.now() - start).total_seconds(),
                details=result,
            )
        except Exception as e:
            return GateResult(
                gate_name="lint",
                status=GateStatus.FAILED,
                duration=(datetime.now() - start).total_seconds(),
                error=str(e),
            )

    async def run_type_gate(self, paths: list[Path] | None = None) -> GateResult:
        """Run type checking gate."""
        start = datetime.now()
        try:
            if paths:
                result = await self._type_checker.check_files(paths)
            else:
                result = await self._type_checker.check_staged()
            return GateResult(
                gate_name="type_check",
                status=GateStatus.PASSED if result.success else GateStatus.FAILED,
                duration=(datetime.now() - start).total_seconds(),
                details=result,
            )
        except Exception as e:
            return GateResult(
                gate_name="type_check",
                status=GateStatus.FAILED,
                duration=(datetime.now() - start).total_seconds(),
                error=str(e),
            )

    async def run_security_gate(self, paths: list[Path] | None = None) -> GateResult:
        """Run security scanning gate."""
        start = datetime.now()
        try:
            if paths:
                result = await self._security_scanner.scan_files(paths)
            else:
                result = await self._security_scanner.scan_staged()
            return GateResult(
                gate_name="security",
                status=GateStatus.PASSED if result.success else GateStatus.FAILED,
                duration=(datetime.now() - start).total_seconds(),
                details=result,
            )
        except Exception as e:
            return GateResult(
                gate_name="security",
                status=GateStatus.FAILED,
                duration=(datetime.now() - start).total_seconds(),
                error=str(e),
            )

    async def run_test_gate(self, paths: list[Path] | None = None) -> GateResult:
        """Run test gate."""
        start = datetime.now()
        try:
            if paths:
                result = await asyncio.to_thread(self._test_runner.run_tests, paths)
            else:
                result = await asyncio.to_thread(self._test_runner.run_tests, [Path("tests/")])
            return GateResult(
                gate_name="tests",
                status=GateStatus.PASSED if result.success else GateStatus.FAILED,
                duration=(datetime.now() - start).total_seconds(),
                details=result,
            )
        except Exception as e:
            return GateResult(
                gate_name="tests",
                status=GateStatus.FAILED,
                duration=(datetime.now() - start).total_seconds(),
                error=str(e),
            )

    async def run_all(self, paths: list[Path] | None = None) -> OrchestratorResult:
        """Run all configured quality gates.

        Args:
            paths: Optional paths to check. If None, checks staged files.

        Returns:
            OrchestratorResult with all gate results
        """
        started = datetime.now()
        results = []

        gates = []
        if self.config.run_lint:
            gates.append(("lint", self.run_lint_gate()))
        if self.config.run_type_check:
            gates.append(("type", self.run_type_gate(paths)))
        if self.config.run_security:
            gates.append(("security", self.run_security_gate(paths)))
        if self.config.run_tests:
            gates.append(("test", self.run_test_gate(paths)))

        if self.config.parallel:
            # Run all gates in parallel
            gate_results = await asyncio.gather(*[g[1] for g in gates], return_exceptions=True)
            for i, result in enumerate(gate_results):
                if isinstance(result, Exception):
                    results.append(
                        GateResult(
                            gate_name=gates[i][0], status=GateStatus.FAILED, error=str(result)
                        )
                    )
                else:
                    results.append(result)
        else:
            # Run gates sequentially
            for name, gate_coro in gates:
                result = await gate_coro
                results.append(result)
                if self.config.fail_fast and result.status == GateStatus.FAILED:
                    break

        completed = datetime.now()
        overall_success = all(r.status == GateStatus.PASSED for r in results)

        return OrchestratorResult(
            overall_success=overall_success,
            gate_results=results,
            total_duration=(completed - started).total_seconds(),
            started_at=started,
            completed_at=completed,
        )


def create_orchestrator(config: OrchestratorConfig | None = None) -> QualityGateOrchestrator:
    """Factory function to create a QualityGateOrchestrator."""
    return QualityGateOrchestrator(config)
