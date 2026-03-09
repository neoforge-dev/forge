"""
Webhook Server - Modular refactored structure
==============================================

This package contains the refactored webhook server components.
All exports maintain backward compatibility with the original webhook_server.py module.

Phase 1 (Foundation Layer) - Extracted Components:
- core.models: Pydantic models for requests/responses
- core.errors: Custom exception classes
- core.middleware: Request ID and common middleware
- infrastructure.rate_limiter: Token bucket rate limiting
- infrastructure.auth: JWT validation and authentication
- infrastructure.registry: State registry patterns

Phase 2 (API Layer):
- api/: FastAPI routers for all endpoints
- handlers/: Request handlers for webhooks, approvals, tasks
- services/: Business logic services (registry, etc.)

Note: Exports from webhook_server_main are lazy-loaded via __getattr__ to avoid
circular imports (webhook_server_main imports from .webhook_server.core, which
would load this package).
"""

import importlib

from .core.models import (
    PatternUpdateRequest,
    WebhookPayload,
    WebhookResponse,
)
from .handlers.approval_handler import ApprovalQueueHandler
from .handlers.task_handler import TaskQueueHandler
from .handlers.webhook_handler import WebhookHandler
from .infrastructure.auth import (
    AuthConfig,
    AuthResult,
    verify_bearer_token,
)
from .infrastructure.rate_limiter import (
    RateLimitConfig,
    RateLimiter,
    TokenBucket,
    get_rate_limiter,
)

# Direct imports from submodules (no circular import risk)
from .services.agent_registry import (
    AgentRegistry,
    AgentSession,
    get_agent_registry,
)
from .services.event_bus import (
    EventBus,
    SSEEvent,
    get_event_bus,
)
from .services.message_queue import (
    MessageQueue,
    MessageStatus,
    QueuedMessage,
)
from .services.pattern_store import (
    Pattern,
    PatternOutcome,
    PatternStore,
    get_pattern_store,
)

__all__ = [
    # Models
    "PatternUpdateRequest",
    "WebhookPayload",
    "WebhookResponse",
    # Rate Limiting
    "RateLimitConfig",
    "TokenBucket",
    "RateLimiter",
    "get_rate_limiter",
    # Authentication
    "AuthResult",
    "AuthConfig",
    "verify_bearer_token",
    # Event Bus
    "EventBus",
    "SSEEvent",
    "get_event_bus",
    # Message Queue
    "MessageQueue",
    "MessageStatus",
    "QueuedMessage",
    # Handlers
    "ApprovalQueueHandler",
    "TaskQueueHandler",
    "WebhookHandler",
    "WebhookHumanGate",
    # Patterns
    "Pattern",
    "PatternOutcome",
    "PatternStore",
    "get_pattern_store",
    # App
    "app",
    "create_app",
    "run_server",
    "get_agent_registry",
    "AgentRegistry",
    "AgentSession",
    # Constants
    "SSE_HEARTBEAT_INTERVAL",
]

# Lazy-loaded exports from webhook_server_main only (avoids circular import
# when webhook_server_main loads this package via .webhook_server.core.models)
_LAZY_FROM_MAIN = frozenset({
    "WebhookHumanGate",
    "create_app",
    "app",
    "run_server",
    "SSE_HEARTBEAT_INTERVAL",
})


def __getattr__(name: str):
    """Lazy-load webhook_server_main exports to avoid circular import."""
    if name in _LAZY_FROM_MAIN:
        module = importlib.import_module("forge_harness.webhook_server_main")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
