"""API Routers for FORGE Webhook Server.

This package contains FastAPI routers for all API endpoints:
- agents: Agent registry and fleet management
- approvals: Approval queue management
- tasks: Task queue management
- And various other API modules
"""

from .agents import router as agents_router
from .approvals import router as approvals_router
from .audit import router as audit_router
from .auth import router as auth_router
from .balancer import router as balancer_router
from .canary import router as canary_router
from .claims import router as claims_router
from .completions import router as completions_router
from .config import router as config_router
from .content import router as content_router
from .dashboard import router as dashboard_router
from .decisions import router as decisions_router
from .decomposition import router as decomposition_router
from .domains import router as domains_router
from .errors import router as errors_router
from .events import router as events_router
from .features import router as features_router
from .fleet_summary import router as fleet_summary_router
from .graduation import router as graduation_router
from .handoffs import router as handoffs_router
from .health import router as health_router
from .heartbeat_loop import router as heartbeat_loop_router
from .intake import router as intake_router
from .lanes import router as lanes_router
from .lead_state import router as lead_state_router

# Legacy routers (extracted from webhook_server_main.py inline routes)
from .legacy_agents import router as legacy_agents_router
from .legacy_config import router as legacy_config_router
from .legacy_health import router as legacy_health_router
from .legacy_prime import router as legacy_prime_router
from .legacy_sse import router as legacy_sse_router
from .legacy_webhooks import router as legacy_webhooks_router
from .memories import router as memories_router
from .messages import router as messages_router
from .mvp_check import router as mvp_check_router
from .nodes import router as nodes_router
from .orchestrator_events import router as orchestrator_events_router
from .patterns import router as patterns_router
from .pipelines import router as pipelines_router
from .portfolio import router as portfolio_router
from .prime import router as prime_router
from .quality import router as quality_router
from .ralph import router as ralph_router
from .relay import router as relay_router
from .sessions import router as sessions_router
from .slo import router as slo_router
from .state_sync import router as state_sync_router
from .supervisor import router as supervisor_router
from .tasks import router as tasks_router
from .telegram import router as telegram_router
from .version import router as version_router
from .webhooks import router as webhooks_router
from .xnode import router as xnode_router

__all__ = [
    "agents_router",
    "claims_router",
    "audit_router",
    "approvals_router",
    "auth_router",
    "balancer_router",
    "canary_router",
    "completions_router",
    "config_router",
    "content_router",
    "dashboard_router",
    "decisions_router",
    "domains_router",
    "decomposition_router",
    "domains_router",
    "errors_router",
    "events_router",
    "features_router",
    "fleet_summary_router",
    "graduation_router",
    "handoffs_router",
    "health_router",
    "heartbeat_loop_router",
    "intake_router",
    "lead_state_router",
    "lanes_router",
    "memories_router",
    "messages_router",
    "mvp_check_router",
    "nodes_router",
    "orchestrator_events_router",
    "patterns_router",
    "pipelines_router",
    "portfolio_router",
    "prime_router",
    "quality_router",
    "ralph_router",
    "relay_router",
    "sessions_router",
    "slo_router",
    "state_sync_router",
    "supervisor_router",
    "tasks_router",
    "telegram_router",
    "version_router",
    "webhooks_router",
    "xnode_router",
    # Legacy routers
    "legacy_agents_router",
    "legacy_config_router",
    "legacy_health_router",
    "legacy_prime_router",
    "legacy_sse_router",
    "legacy_webhooks_router",
]
