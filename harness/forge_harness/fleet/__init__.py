"""Fleet management module for FORGE Harness.

Provides monitoring and management capabilities for tmux-based agent fleets.
"""

# Unified Dashboard Service (PREFERRED)
# Import from the unified service to provide backward-compatible aliases
from forge_harness.webhook_server.services.dashboard_service import (
    AgentMetricsSummary,
    DashboardService,
    DashboardSummary,
    TaskThroughputMetrics,
    get_dashboard_service,
)

from .agent_restart import AgentRestarter, RecoveryResult, TaskState, auto_recover_all
from .context_rotation import (
    CONTEXT_WINDOW_TOKENS,
    ROTATION_THRESHOLD,
    TOKENS_PER_LINE,
    ContextRotator,
    ContextStats,
    RotationResult,
    TaskContext,
    create_context_rotator,
    create_rotator,
)
from .dashboard import FleetAgent, FleetStatus, get_fleet_status, watch_fleet
from .dispatch_client import DispatchClient, GitMutex, get_client, send, send_sync
from .dispatch_client import DispatchResult as ClientDispatchResult
from .permissions import (
    CRITICAL_PERMISSIONS,
    DEFAULT_PERMISSIONS,
    DOMAIN_PERMISSIONS,
    PermissionPreloader,
    PermissionProfile,
    PermissionVerificationResult,
    create_permission_preloader,
)
from .priority_dispatch import (
    AgentStatus,
    DispatchResult,
    dispatch_task,
    get_idle_agents,
    get_pending_tasks,
    match_task_to_agent,
    run_dispatch_loop,
)
from .readiness import CLIState, ReadinessResult, check_readiness, wait_for_ready
from .restart import (
    AgentRestarter,
    HealthStatus,
    RestartEvent,
    TaskState,
    create_agent_restarter,
)
from .retry import CircuitBreaker, CircuitState, FailureType, RetryConfig, dispatch_with_retry
from .verification import DeliveryReceipt, send_with_verification, send_with_verification_sync

__all__ = [
    # Dashboard
    "get_fleet_status",
    "FleetAgent",
    "FleetStatus",
    "watch_fleet",
    # Context rotation
    "ContextRotator",
    "ContextStats",
    "RotationResult",
    "TaskContext",
    "create_context_rotator",
    "create_rotator",
    "CONTEXT_WINDOW_TOKENS",
    "ROTATION_THRESHOLD",
    "TOKENS_PER_LINE",
    # Unified Dashboard Service (PREFERRED)
    "DashboardService",
    "DashboardSummary",
    "AgentMetricsSummary",
    "TaskThroughputMetrics",
    "get_dashboard_service",
    # Permissions
    "PermissionPreloader",
    "PermissionProfile",
    "PermissionVerificationResult",
    "create_permission_preloader",
    "DEFAULT_PERMISSIONS",
    "CRITICAL_PERMISSIONS",
    "DOMAIN_PERMISSIONS",
    # Restart
    "AgentRestarter",
    "HealthStatus",
    "TaskState",
    "RestartEvent",
    "RecoveryResult",
    "create_agent_restarter",
    "auto_recover_all",
    # Priority dispatch
    "AgentStatus",
    "DispatchResult",
    "get_pending_tasks",
    "get_idle_agents",
    "match_task_to_agent",
    "dispatch_task",
    "run_dispatch_loop",
    # Dispatch client (NEW - Reliability Framework)
    "DispatchClient",
    "ClientDispatchResult",
    "GitMutex",
    "get_client",
    "send",
    "send_sync",
    # Readiness detection
    "CLIState",
    "ReadinessResult",
    "check_readiness",
    "wait_for_ready",
    # Verification
    "DeliveryReceipt",
    "send_with_verification",
    "send_with_verification_sync",
    # Retry and circuit breaker
    "CircuitBreaker",
    "CircuitState",
    "FailureType",
    "RetryConfig",
    "dispatch_with_retry",
]
