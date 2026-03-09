"""
FORGE Autonomous MVP Harness
============================

A Claude Agent SDK harness for autonomous FORGE portfolio MVP development.

Key Features:
- GitHub Issues integration for task tracking
- Living-docs pyramid sync for context and handoff
- Domain-aware configuration (COPPA, HIPAA-lite, etc.)
- Railway + Cloudflare deployment automation
- PostHog session analytics
- Project bootstrap from PLAN.md
- Content generation from living-docs
- Launch orchestration with quality gates
"""

__version__ = "0.3.0"

# Track which optional dependencies are available
_HTTPX_AVAILABLE = False
try:
    import httpx  # noqa: F401

    _HTTPX_AVAILABLE = True
except ImportError:
    pass

# Lazy imports for SDK-dependent modules
# This allows tests to import forge_harness without claude_code_sdk installed
_LAZY_IMPORTS = {
    "ForgeAgent": ".agent",
    "AgentResult": ".agent",
}

# Explicit whitelist of allowed module imports (security: prevents arbitrary code loading)
_ALLOWED_MODULES = frozenset([".agent"])


def __getattr__(name: str):
    """Lazy import SDK-dependent modules."""
    if name in _LAZY_IMPORTS:
        module_name = _LAZY_IMPORTS[name]
        # Security: Only allow explicitly whitelisted modules
        if module_name not in _ALLOWED_MODULES:
            raise ImportError(f"Module {module_name!r} not in allowed list")
        import importlib

        # Safe: module_name is validated against _ALLOWED_MODULES frozenset above
        module = importlib.import_module(module_name, __package__)  # nosemgrep: non-literal-import
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# =============================================================================
# Core imports (no httpx dependency)
# =============================================================================

from .approval_queue import (
    ApprovalPriority,
    ApprovalQueueHarness,
    ApprovalQueueStats,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStorage,
    ApprovalType,
    JSONFileStorage,
    NotionApprovalStorage,
    create_approval_queue,
    create_approval_queue_from_env,
    create_notion_approval_queue,
)
from .content_harness import (
    DOMAIN_TIERS,
    TIER_ALLOCATIONS,
    # Calendar
    CalendarEntry,
    ContentBrief,
    ContentHarness,
    ContentItem,
    ContentResult,
    ContentSpec,
    ContentStage,
    ContentStorage,
    # Tier system
    ContentTier,
    FileContentStorage,
    GenerationConfig,
    TierAllocation,
    create_content_harness,
    create_file_storage,
    create_storage,
    format_calendar_markdown,
    generate_content_calendar,
    get_all_priorities,
    get_content_allocation,
    get_domain_tier,
    get_tier_adjusted_spec,
    get_tier_allocation,
)
from .deployment import (
    _LEGACY_AVAILABLE,
    DeploymentProvider,
    DeploymentResult,
    ProviderCapabilities,
    ProviderRegistry,
)

# =============================================================================
# Deployment provider system (base types, no httpx)
# =============================================================================
from .deployment import (
    DeploymentConfig as ProviderDeploymentConfig,
)
from .domain_config import DomainConfig, get_domain_config
from .domain_registry import (
    ContentConfig,
    DeploymentConfig,
    DomainEntry,
    DomainRegistry,
    get_domain_from_registry,
)
from .domain_registry import (
    NotionConfig as DomainNotionConfig,
)
from .failure_patterns import (
    # Enhanced with Code Atlas integration
    EnhancedFailurePatternDB,
    FailurePattern,
    FailurePatternDB,
    FixSuggestion,
    PatternMatch,
    get_fix_suggestions,
)
from .harness_registry import (
    HarnessConfig,
    HarnessRegistry,
    create_harness_registry,
)
from .issue_bootstrap import BootstrapResult, bootstrap_project, list_epics, validate_plan_md
from .logging_config import (
    ConsoleFormatter,
    JSONFormatter,
    LogContext,
    configure_logging,
    get_logger,
    log_task_complete,
    log_task_error,
    log_task_start,
    log_with_context,
)
from .portfolio_scanner import (
    FORGE_PROJECTS,
    FeatureSpec,
    PortfolioHealthReport,
    PortfolioScanner,
    ProjectHealth,
    ScannerConfig,
    scan_and_generate_fixes,
)
from .preflight import PreflightChecker, PreflightResult
from .session_state import (
    HumanDecision,
    SessionState,
    SessionStateManager,
    create_session_state_manager,
)
from .test_harness import (
    CoverageGap,
    CoverageHarness,
    CoverageReport,
    GeneratedTestResult,
    QualityReport,
    create_coverage_harness,
)
from .verification import VerificationResult, Verifier
from .workflow_harness import (
    StepResult,
    StepStatus,
    WorkflowCheckpoint,
    WorkflowHarness,
    WorkflowResult,
    WorkflowStep,
    create_workflow_harness,
)

# =============================================================================
# Optional imports (require httpx)
# =============================================================================

if _HTTPX_AVAILABLE:
    # Deployment (legacy)
    from .deployment import (
        ForgeDeployer,
        QualityGateResult,
        SmokeTestResult,
    )
    from .deployment_legacy import (
        DeploymentResult as LegacyDeploymentResult,
    )

    # Flywheel (depends on orchestration_harness)
    from .flywheel import (
        FlywheelConfig,
        FlywheelResult,
        create_flywheel_loop,
        create_fully_wired_loop,
        generate_portfolio_features,
        run_flywheel,
        run_flywheel_sync,
        scan_portfolio,
        scan_project_for_debt,
    )
    from .human_gate_harness import (
        ApprovalResult,
        GateStatus,
        HumanGateHarness,
        create_human_gate_harness,
    )

    # Notification & Human Gate (require httpx for webhook delivery)
    from .notification_harness import (
        EmailConfig,
        HumanResponse,
        NotificationChannel,
        NotificationConfig,
        NotificationHarness,
        NotificationResult,
        NotificationUrgency,
        create_notification_harness,
    )

    # Orchestration (depends on notification_harness)
    from .orchestration_harness import (
        HumanGateConfig,
        OrchestrationHarness,
        Pipeline,
        PipelineResult,
        PipelineStep,
        create_orchestration_harness,
        create_orchestration_harness_from_registry,
    )
    from .orchestration_harness import (
        StepResult as PipelineStepResult,
    )
    from .orchestration_harness import (
        StepStatus as PipelineStepStatus,
    )

    # Quality Loop (depends on httpx for API calls)
    from .quality_loop import (
        DebtMetrics,
        PortfolioQualityReport,
        ProjectQualityReport,
        QualityConfig,
        QualityLoopHarness,
        QualityTrend,
        SecurityFinding,
        SeverityLevel,
        create_quality_loop,
    )

    # Self-Improvement
    from .self_improve import (
        HarnessSelfImprover,
        SelfAnalysisResult,
        run_self_analysis,
        self_analyze_sync,
    )
else:
    # Stubs for when httpx is not available
    # Deployment legacy
    ForgeDeployer = None  # type: ignore[misc, assignment]
    QualityGateResult = None  # type: ignore[misc, assignment]
    SmokeTestResult = None  # type: ignore[misc, assignment]
    LegacyDeploymentResult = None  # type: ignore[misc, assignment]

    # Notification
    EmailConfig = None  # type: ignore[misc, assignment]
    HumanResponse = None  # type: ignore[misc, assignment]
    NotificationChannel = None  # type: ignore[misc, assignment]
    NotificationConfig = None  # type: ignore[misc, assignment]
    NotificationHarness = None  # type: ignore[misc, assignment]
    NotificationResult = None  # type: ignore[misc, assignment]
    NotificationUrgency = None  # type: ignore[misc, assignment]
    create_notification_harness = None  # type: ignore[misc, assignment]

    # Human Gate
    ApprovalResult = None  # type: ignore[misc, assignment]
    GateStatus = None  # type: ignore[misc, assignment]
    HumanGateHarness = None  # type: ignore[misc, assignment]
    create_human_gate_harness = None  # type: ignore[misc, assignment]

    # Orchestration
    HumanGateConfig = None  # type: ignore[misc, assignment]
    OrchestrationHarness = None  # type: ignore[misc, assignment]
    Pipeline = None  # type: ignore[misc, assignment]
    PipelineResult = None  # type: ignore[misc, assignment]
    PipelineStep = None  # type: ignore[misc, assignment]
    create_orchestration_harness = None  # type: ignore[misc, assignment]
    create_orchestration_harness_from_registry = None  # type: ignore[misc, assignment]
    PipelineStepResult = None  # type: ignore[misc, assignment]
    PipelineStepStatus = None  # type: ignore[misc, assignment]

    # Quality Loop
    DebtMetrics = None  # type: ignore[misc, assignment]
    PortfolioQualityReport = None  # type: ignore[misc, assignment]
    ProjectQualityReport = None  # type: ignore[misc, assignment]
    QualityConfig = None  # type: ignore[misc, assignment]
    QualityLoopHarness = None  # type: ignore[misc, assignment]
    QualityTrend = None  # type: ignore[misc, assignment]
    SecurityFinding = None  # type: ignore[misc, assignment]
    SeverityLevel = None  # type: ignore[misc, assignment]
    create_quality_loop = None  # type: ignore[misc, assignment]

    # Flywheel
    FlywheelConfig = None  # type: ignore[misc, assignment]
    FlywheelResult = None  # type: ignore[misc, assignment]
    create_flywheel_loop = None  # type: ignore[misc, assignment]
    create_fully_wired_loop = None  # type: ignore[misc, assignment]
    generate_portfolio_features = None  # type: ignore[misc, assignment]
    run_flywheel = None  # type: ignore[misc, assignment]
    run_flywheel_sync = None  # type: ignore[misc, assignment]
    scan_portfolio = None  # type: ignore[misc, assignment]
    scan_project_for_debt = None  # type: ignore[misc, assignment]

    # Self-Improvement
    HarnessSelfImprover = None  # type: ignore[misc, assignment]
    SelfAnalysisResult = None  # type: ignore[misc, assignment]
    run_self_analysis = None  # type: ignore[misc, assignment]
    self_analyze_sync = None  # type: ignore[misc, assignment]


__all__ = [
    # Availability flags
    "_HTTPX_AVAILABLE",
    "_LEGACY_AVAILABLE",
    # Lazy imports
    "ForgeAgent",
    "AgentResult",
    # Issue Bootstrap
    "bootstrap_project",
    "BootstrapResult",
    "validate_plan_md",
    "list_epics",
    # Domain Config
    "get_domain_config",
    "DomainConfig",
    # Pre-flight & Verification
    "PreflightChecker",
    "PreflightResult",
    "Verifier",
    "VerificationResult",
    # Failure Patterns
    "FailurePatternDB",
    "FailurePattern",
    "PatternMatch",
    "get_fix_suggestions",
    "EnhancedFailurePatternDB",
    "FixSuggestion",
    # Portfolio Scanner
    "PortfolioScanner",
    "ProjectHealth",
    "PortfolioHealthReport",
    "ScannerConfig",
    "FeatureSpec",
    "FORGE_PROJECTS",
    "scan_and_generate_fixes",
    # Content Harness
    "ContentHarness",
    "ContentSpec",
    "ContentResult",
    "ContentStage",
    "ContentBrief",
    "ContentItem",
    "GenerationConfig",
    "ContentStorage",
    "FileContentStorage",
    "create_content_harness",
    "create_file_storage",
    "create_storage",
    # Content Tier System
    "ContentTier",
    "TierAllocation",
    "TIER_ALLOCATIONS",
    "DOMAIN_TIERS",
    "get_domain_tier",
    "get_tier_allocation",
    "get_content_allocation",
    "get_tier_adjusted_spec",
    "get_all_priorities",
    # Content Calendar
    "CalendarEntry",
    "generate_content_calendar",
    "format_calendar_markdown",
    # Coverage Harness (formerly Test Harness)
    "CoverageHarness",
    "CoverageGap",
    "CoverageReport",
    "GeneratedTestResult",
    "QualityReport",
    "create_coverage_harness",
    # Domain Registry
    "DomainRegistry",
    "DomainEntry",
    "ContentConfig",
    "DeploymentConfig",
    "DomainNotionConfig",
    "get_domain_from_registry",
    # Workflow Harness
    "WorkflowHarness",
    "WorkflowStep",
    "WorkflowResult",
    "WorkflowCheckpoint",
    "StepStatus",
    "StepResult",
    "create_workflow_harness",
    # Logging
    "configure_logging",
    "get_logger",
    "LogContext",
    "log_with_context",
    "log_task_start",
    "log_task_complete",
    "log_task_error",
    "JSONFormatter",
    "ConsoleFormatter",
    # Harness Registry
    "HarnessRegistry",
    "HarnessConfig",
    "create_harness_registry",
    # Session State
    "SessionStateManager",
    "SessionState",
    "HumanDecision",
    "create_session_state_manager",
    # Approval Queue
    "ApprovalQueueHarness",
    "ApprovalRequest",
    "ApprovalStatus",
    "ApprovalType",
    "ApprovalPriority",
    "ApprovalQueueStats",
    "ApprovalStorage",
    "JSONFileStorage",
    "NotionApprovalStorage",
    "create_approval_queue",
    "create_notion_approval_queue",
    "create_approval_queue_from_env",
    # Deployment (new provider system - always available)
    "DeploymentProvider",
    "ProviderDeploymentConfig",
    "DeploymentResult",
    "ProviderCapabilities",
    "ProviderRegistry",
    # --- Optional (require httpx) ---
    # Deployment (legacy)
    "ForgeDeployer",
    "QualityGateResult",
    "SmokeTestResult",
    "LegacyDeploymentResult",
    # Notification Harness
    "NotificationHarness",
    "NotificationConfig",
    "NotificationResult",
    "NotificationChannel",
    "NotificationUrgency",
    "HumanResponse",
    "EmailConfig",
    "create_notification_harness",
    # Human Gate Harness
    "HumanGateHarness",
    "ApprovalResult",
    "GateStatus",
    "create_human_gate_harness",
    # Orchestration Harness
    "OrchestrationHarness",
    "Pipeline",
    "PipelineStep",
    "PipelineResult",
    "HumanGateConfig",
    "create_orchestration_harness",
    "create_orchestration_harness_from_registry",
    "PipelineStepStatus",
    "PipelineStepResult",
    # Quality Loop
    "QualityLoopHarness",
    "QualityConfig",
    "QualityTrend",
    "SeverityLevel",
    "DebtMetrics",
    "SecurityFinding",
    "ProjectQualityReport",
    "PortfolioQualityReport",
    "create_quality_loop",
    # Flywheel - Compounding Autonomous Development
    "FlywheelConfig",
    "FlywheelResult",
    "create_flywheel_loop",
    "create_fully_wired_loop",
    "generate_portfolio_features",
    "scan_portfolio",
    "scan_project_for_debt",
    "run_flywheel",
    "run_flywheel_sync",
    # Self-Improvement
    "SelfAnalysisResult",
    "HarnessSelfImprover",
    "run_self_analysis",
    "self_analyze_sync",
]
