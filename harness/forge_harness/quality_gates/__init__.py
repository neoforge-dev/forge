"""Quality gates for FORGE Harness pre-commit checks."""

from .coverage_gate import (
    CoverageConfig,
    CoverageGate,
    CoverageResult,
    FileCoverage,
    SimpleCoverageGate,
    SimpleCoverageResult,  # noqa: F401
    create_coverage_gate,
)
from .dependency_gate import (
    AuditResult,
    DependencyGate,
    DependencyGateConfig,
    VulnerabilityInfo,
    create_dependency_gate,
)
from .lint_gate import LintResult, lint_staged_files
from .orchestrator import (
    GateResult,
    GateStatus,
    OrchestratorConfig,
    OrchestratorResult,
    QualityGateOrchestrator,
    create_orchestrator,
)
from .reporter import OutputFormat, QualityGateReporter, ReportConfig, create_reporter
from .security_gate import (
    SecurityResult,
    SecurityScanner,
    SecurityScannerConfig,
    Severity,
    Vulnerability,
    create_security_scanner,
)
from .test_runner import TestResult, TestRunner, TestRunnerConfig
from .type_gate import (
    TypeChecker,
    TypeCheckerConfig,
    TypeCheckError,
    TypeCheckResult,
    create_type_checker,
)

__all__ = [
    # Lint gate
    "LintResult",
    "lint_staged_files",
    # Test runner
    "TestResult",
    "TestRunner",
    "TestRunnerConfig",
    # Type gate
    "TypeCheckError",
    "TypeCheckResult",
    "TypeChecker",
    "TypeCheckerConfig",
    "create_type_checker",
    # Security gate
    "Severity",
    "Vulnerability",
    "SecurityResult",
    "SecurityScanner",
    "SecurityScannerConfig",
    "create_security_scanner",
    # Coverage gate
    "FileCoverage",
    "CoverageResult",
    "CoverageConfig",
    "CoverageGate",
    "create_coverage_gate",
    # Orchestrator
    "GateStatus",
    "GateResult",
    "OrchestratorResult",
    "OrchestratorConfig",
    "QualityGateOrchestrator",
    "create_orchestrator",
    # Reporter
    "OutputFormat",
    "ReportConfig",
    "QualityGateReporter",
    "create_reporter",
    # Dependency gate
    "VulnerabilityInfo",
    "AuditResult",
    "DependencyGateConfig",
    "DependencyGate",
    "create_dependency_gate",
    # Simple coverage gate (legacy)
    "SimpleCoverageGate",
]

# Legacy re-exports for backwards compatibility with __main__ blocks
from .coverage_gate import run_coverage_gate  # noqa: E402, F401
from .lint_gate import LintGate, run_lint_gate  # noqa: E402, F401
from .pre_commit_tests import PreCommitTestRunner, run_pre_commit_tests  # noqa: E402, F401
from .security_scan import ScanResult, SecurityScanGate, run_security_scan  # noqa: E402, F401
from .type_check import TypeCheckGate, run_type_check_gate  # noqa: E402, F401
