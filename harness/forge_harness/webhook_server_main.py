"""FORGE Command Center — FastAPI application entry point.

Main webhook server with 90+ API endpoints for fleet management,
task orchestration, approvals, and real-time agent monitoring.

Usage:
    # Programmatic startup
    uvicorn forge_harness.webhook_server:app --port 8080

    # Or: python -m forge_harness.webhook_server_main

    # Or programmatically
    from forge_harness.webhook_server import create_app, WebhookHandler

    handler = WebhookHandler(notification_harness)
    app = create_app(handler)

Endpoints:
    POST /api/webhooks/slack   - Slack interactive messages
    POST /api/webhooks/github  - GitHub webhook events
    GET  /health               - Health check
    GET  /api/metrics          - Server metrics (uptime, request count, active agents)
    GET  /api/version          - Version information

    # Approval Queue API (requires Bearer token authentication)
    GET    /api/approvals                    - List pending approvals
    GET    /api/approvals/{request_id}       - Get approval details
    POST   /api/approvals/{request_id}/approve - Approve a request
    POST   /api/approvals/{request_id}/reject  - Reject a request
    POST   /api/approvals/bulk/approve       - Bulk approve multiple requests
    POST   /api/approvals/bulk/reject        - Bulk reject multiple requests
    GET    /api/approvals/stats              - Get queue statistics

    # Task Queue API (requires Bearer token authentication)
    GET    /api/tasks                        - List all tasks with filters
    POST   /api/tasks                        - Create a new task
    GET    /api/tasks/stats                  - Get task queue statistics
    GET    /api/tasks/{task_id}              - Get task details
    PUT    /api/tasks/{task_id}              - Update a task
    DELETE /api/tasks/{task_id}              - Delete a task
    POST   /api/tasks/{task_id}/claim        - Claim a task for an agent

    # Command Center Dashboard API (requires Bearer token authentication)
    GET    /api/mvp-check/status             - Get MVP check results
    GET    /api/ralph-loop/status            - Get Ralph Loop state
    GET    /api/ralph-loop/decisions         - Get Ralph Loop decision history
    POST   /api/ralph-loop/start             - Start the Ralph Loop
    POST   /api/ralph-loop/pause             - Pause the Ralph Loop
    POST   /api/ralph-loop/stop              - Stop the Ralph Loop
    GET    /api/errors/recent                - Get recent errors
    GET    /api/supervisor/status            - Get agent supervisor health
    POST   /api/orchestrator/events          - Report orchestrator heartbeat/dispatch (dashboard visibility)

    # Handoffs API (requires Bearer token authentication)
    GET    /api/handoffs                     - List all handoffs (filter by status)
    POST   /api/handoffs                     - Create a new handoff
    POST   /api/handoffs/{handoff_id}/accept - Accept a handoff
    POST   /api/handoffs/{handoff_id}/reject - Reject a handoff
    POST   /api/handoffs/{handoff_id}/complete - Complete a handoff

    # Config API (requires Bearer token authentication)
    GET    /api/config/llm                   - Get LLM configuration
    POST   /api/config/llm                   - Update LLM configuration

    # Quality Metrics API (requires Bearer token authentication)
    GET    /api/quality                      - Get quality metrics for all projects
    GET    /api/quality/{project}            - Get quality metrics for a specific project

    # Content Library API (requires Bearer token authentication)
    GET    /api/content                      - Get content library status for all projects
    GET    /api/content/{domain}/{project}   - Get detailed content for a specific project
    GET    /api/content/stats                - Get aggregate content statistics

    # XNode Realtime Bridge (requires Bearer token authentication)
    POST   /api/xnode/events                 - Receive xnode realtime events (lead.send, lead.ack, xnode.relay.exception)
"""

import asyncio
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge_harness.webhook_server.handlers.approval_handler import (
    ApprovalQueueHandler,
    get_approval_handler,
)
from forge_harness.webhook_server.handlers.handoff_handler import (
    get_handoff_handler,
)
from forge_harness.webhook_server.handlers.task_handler import get_task_handler
from forge_harness.webhook_server.handlers.webhook_handler import WebhookHandler
from forge_harness.webhook_server.services.agent_registry import (
    get_agent_registry,
)
from forge_harness.webhook_server.services.event_bus import get_event_bus
from forge_harness.webhook_server.services.pattern_store import (
    PatternStore,
    get_pattern_store,
)
from forge_harness.webhook_server.services.portfolio_service import (
    get_portfolio_service,  # noqa: F401 — re-exported for test mock targets
)

from .logging_config import get_logger
from .webhook_server.core.models import WebhookPayload
from .webhook_server.infrastructure.auth import AuthConfig
from .webhook_server.infrastructure.rate_limiter import (
    RateLimitConfig,
    RateLimiter,
)

logger = get_logger(__name__)


# =============================================================================
# Repository Discovery
# =============================================================================


def _get_forge_repo_root() -> Path:
    """Find FORGE repo root (directory containing .forge).

    Traverses up from this file's location until it finds a directory
    containing a `.forge` subdirectory.  Falls back to ``Path.cwd()`` when
    the filesystem root is reached without finding one.
    """
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / ".forge").is_dir():
            return current
        current = current.parent
    return Path.cwd()


# =============================================================================
# Background Services (module-level singletons)
# =============================================================================
_state_synchronizer: Any | None = None
_synchronizer_task: asyncio.Task | None = None


def get_state_synchronizer() -> Any:
    """Get the global StateSynchronizer instance.

    Returns:
        StateSynchronizer instance or None if not initialized
    """
    return _state_synchronizer


async def start_state_synchronizer(forge_root: Path | None = None) -> Any:
    """Initialize and start the global state synchronizer.

    Args:
        forge_root: FORGE project root directory

    Returns:
        StateSynchronizer instance
    """
    global _state_synchronizer, _synchronizer_task

    if _state_synchronizer is not None:
        return _state_synchronizer

    try:
        from .state_synchronizer import create_synchronizer

        # Use the module-level singleton factory so all consumers share one instance
        event_bus = get_event_bus()
        _state_synchronizer = create_synchronizer(
            forge_root=forge_root or Path.cwd(),
            event_bus=event_bus,
        )
        # Also register as the module-level singleton so get_synchronizer()
        # returns the same instance (avoids two separate StateSynchronizer objects)
        import forge_harness.state_synchronizer as _ss_mod

        _ss_mod._synchronizer = _state_synchronizer

        # Start background synchronization
        _synchronizer_task = asyncio.create_task(_state_synchronizer.start(poll_interval=5.0))
        logger.info("State synchronizer started with 5s poll interval")

        return _state_synchronizer
    except ImportError as e:
        logger.warning(f"StateSynchronizer not available: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to start state synchronizer: {e}")
        return None


async def stop_state_synchronizer() -> None:
    """Stop the global state synchronizer."""
    global _state_synchronizer, _synchronizer_task

    if _state_synchronizer is not None:
        try:
            await _state_synchronizer.stop()
            logger.info("State synchronizer stopped")
        except Exception as e:
            logger.error(f"Error stopping state synchronizer: {e}")

    if _synchronizer_task is not None:
        _synchronizer_task.cancel()
        try:
            await _synchronizer_task
        except asyncio.CancelledError:
            pass

    _state_synchronizer = None
    _synchronizer_task = None

    # Also reset the module-level singleton
    try:
        from .state_synchronizer import reset_synchronizer

        reset_synchronizer()
    except ImportError:
        pass


# =============================================================================
# Tmux DB Sync Service for Agent State
# =============================================================================

_tmux_sync_service: Any | None = None
_tmux_sync_task: asyncio.Task | None = None


def get_tmux_sync_service() -> Any:
    """Get the global TmuxDBSyncService instance.

    Returns:
        TmuxDBSyncService instance or None if not initialized
    """
    return _tmux_sync_service


async def start_tmux_sync_service(forge_root: Path | None = None) -> Any:
    """Initialize and start the tmux sync service.

    Args:
        forge_root: FORGE project root directory

    Returns:
        TmuxDBSyncService instance
    """
    global _tmux_sync_service, _tmux_sync_task

    if _tmux_sync_service is not None:
        return _tmux_sync_service

    try:
        from forge_harness.sync.agent_sync import TmuxDBSyncService

        # Create sync service with the global event bus
        event_bus = get_event_bus()
        _tmux_sync_service = TmuxDBSyncService(
            forge_root=forge_root or Path.cwd(),
            event_bus=event_bus,
        )

        # Start background synchronization with 5s interval
        await _tmux_sync_service.start(poll_interval=5.0)
        logger.info("TmuxDBSyncService started with 5s poll interval")

        return _tmux_sync_service
    except ImportError as e:
        logger.warning(f"TmuxDBSyncService not available: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to start tmux sync service: {e}")
        return None


async def stop_tmux_sync_service() -> None:
    """Stop the tmux sync service."""
    global _tmux_sync_service, _tmux_sync_task

    if _tmux_sync_service is not None:
        try:
            await _tmux_sync_service.stop()
            logger.info("TmuxDBSyncService stopped")
        except Exception as e:
            logger.error(f"Error stopping tmux sync service: {e}")

    _tmux_sync_service = None
    _tmux_sync_task = None


# =============================================================================
# Task Lease Recovery Service
# =============================================================================

_lease_recovery_service: Any | None = None
_lease_recovery_task: asyncio.Task | None = None


def get_lease_recovery_service() -> Any:
    """Get the global stale lease recovery service instance."""
    return _lease_recovery_service


def _lease_recovery_poll_interval_seconds() -> float:
    """Read lease recovery interval from environment."""
    raw_value = os.environ.get("FORGE_TASK_LEASE_RECOVERY_POLL_INTERVAL_SECONDS", "30")
    try:
        return max(1.0, float(raw_value))
    except ValueError:
        logger.warning(
            "Invalid FORGE_TASK_LEASE_RECOVERY_POLL_INTERVAL_SECONDS=%s; using 30.0",
            raw_value,
        )
        return 30.0


async def start_lease_recovery_service() -> Any:
    """Initialize and start stale lease recovery worker."""
    global _lease_recovery_service, _lease_recovery_task

    if _lease_recovery_service is not None:
        return _lease_recovery_service

    try:
        from forge_harness.webhook_server.services.lease_recovery import (
            StaleLeaseRecoveryService,
        )

        _lease_recovery_service = StaleLeaseRecoveryService(
            task_handler=get_task_handler(),
            event_bus=get_event_bus(),
        )
        interval = _lease_recovery_poll_interval_seconds()
        _lease_recovery_task = asyncio.create_task(_lease_recovery_service.start(interval))
        logger.info("Stale lease recovery worker started")
        return _lease_recovery_service
    except ImportError as e:
        logger.warning(f"Stale lease recovery service not available: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to start stale lease recovery service: {e}")
        return None


async def stop_lease_recovery_service() -> None:
    """Stop stale lease recovery worker."""
    global _lease_recovery_service, _lease_recovery_task

    if _lease_recovery_service is not None:
        try:
            await _lease_recovery_service.stop()
        except Exception as e:
            logger.error(f"Error stopping stale lease recovery service: {e}")

    if _lease_recovery_task is not None:
        _lease_recovery_task.cancel()
        try:
            await _lease_recovery_task
        except asyncio.CancelledError:
            pass

    _lease_recovery_service = None
    _lease_recovery_task = None


# =============================================================================
# Relay Worker
# =============================================================================

_relay_worker_service: Any | None = None
_relay_worker_task: asyncio.Task | None = None


def get_relay_worker_service() -> Any:
    """Get the global relay worker service instance."""
    return _relay_worker_service


def _relay_worker_poll_interval_seconds() -> float:
    """Read relay worker interval from environment."""
    raw_value = os.environ.get("FORGE_RELAY_WORKER_POLL_INTERVAL_SECONDS", "5.0")
    try:
        return max(1.0, float(raw_value))
    except ValueError:
        logger.warning(
            "Invalid FORGE_RELAY_WORKER_POLL_INTERVAL_SECONDS=%s; using 5.0",
            raw_value,
        )
        return 5.0


async def start_relay_worker() -> Any:
    """Initialize and start the relay worker background task."""
    global _relay_worker_service, _relay_worker_task

    if _relay_worker_service is not None:
        return _relay_worker_service

    try:
        from forge_harness.webhook_server.services.relay_worker import get_relay_worker

        interval = _relay_worker_poll_interval_seconds()
        _relay_worker_service = get_relay_worker(poll_interval=interval)
        _relay_worker_task = asyncio.create_task(_relay_worker_service.run())
        logger.info("Relay worker started")
        return _relay_worker_service
    except ImportError as e:
        logger.warning(f"Relay worker not available: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to start relay worker: {e}")
        return None


async def stop_relay_worker() -> None:
    """Stop the relay worker background task."""
    global _relay_worker_service, _relay_worker_task

    if _relay_worker_service is not None:
        try:
            if hasattr(_relay_worker_service, "stop"):
                await _relay_worker_service.stop()
        except Exception as e:
            logger.error(f"Error stopping relay worker service: {e}")

    if _relay_worker_task is not None:
        _relay_worker_task.cancel()
        try:
            await _relay_worker_task
        except asyncio.CancelledError:
            pass

    _relay_worker_service = None
    _relay_worker_task = None
    logger.info("Relay worker stopped")


# =============================================================================
# Meta-Learning Store for Decisions
# =============================================================================

# Global learning store
_learning_store: Any = None


def get_learning_store() -> Any:
    """Get or create global learning store.

    Returns the LearningStore from meta_learning module for accessing
    decision logs and reinforcement learning data.
    """
    global _learning_store
    if _learning_store is None:
        try:
            from .meta_learning.config import LearningStoreConfig
            from .meta_learning.learning_store import LearningStore

            config = LearningStoreConfig()  # Use defaults
            _learning_store = LearningStore.from_config(config)
        except (ImportError, Exception) as e:
            logger.warning(
                f"meta_learning module not available or failed to initialize: {e} - decisions endpoint will return empty"
            )
            _learning_store = None
    return _learning_store


# =============================================================================
# Global Orchestration Harness Reference (for pipeline status)
# =============================================================================

_orchestration_harness: Any = None


def set_orchestration_harness(harness: Any) -> None:
    """Set the global orchestration harness reference.

    This should be called when creating the webhook server if you want
    the /api/pipelines endpoint to return live pipeline status.

    Args:
        harness: OrchestrationHarness instance
    """
    global _orchestration_harness
    _orchestration_harness = harness


def get_orchestration_harness() -> Any:
    """Get the global orchestration harness reference."""
    return _orchestration_harness


# =============================================================================
# Webhook Handler
# =============================================================================


# FastAPI Application
# =============================================================================


def create_app(
    handler: WebhookHandler | None = None,
    approval_handler: ApprovalQueueHandler | None = None,
    auth_config: AuthConfig | None = None,
    rate_limit_config: RateLimitConfig | None = None,
    pattern_store: PatternStore | None = None,
) -> Any:
    """Create FastAPI application for webhook handling.

    Args:
        handler: Optional pre-configured WebhookHandler
        approval_handler: Optional ApprovalQueueHandler for approval endpoints
        auth_config: Optional authentication configuration
        rate_limit_config: Optional rate limiting configuration
        pattern_store: Optional PatternStore for pattern operations

    Returns:
        FastAPI application instance
    """
    try:
        from contextlib import asynccontextmanager

        from fastapi import FastAPI, HTTPException, Request
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import JSONResponse
    except ImportError:
        logger.warning("FastAPI not installed. Run: uv add fastapi uvicorn")
        return None

    from .webhook_server.middleware import (
        RequestTrackingMiddleware,
        WebhookRateLimitMiddleware,
        create_request_counter,
        security_headers_middleware,
    )
    from .webhook_server.models.legacy_models import api_response, error_code_from_status

    # Background SSE session cleanup
    _sse_cleanup_task: asyncio.Task | None = None

    async def _sse_session_cleanup_loop() -> None:
        """Periodically evict expired SSE session tokens (every 60s)."""
        from .webhook_server.infrastructure.sse_session import get_sse_session_store

        store = get_sse_session_store()
        while True:
            try:
                await asyncio.sleep(60)
                removed = store.cleanup_expired()
                if removed:
                    logger.debug("SSE session cleanup: removed %d expired tokens", removed)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("SSE session cleanup error: %s", e)

    # Lifespan context manager for startup/shutdown
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage application lifecycle."""
        nonlocal _sse_cleanup_task

        logger.info("Starting FORGE Harness webhook server...")
        try:
            await start_state_synchronizer()
        except Exception as e:
            logger.warning(f"State synchronizer startup failed: {e}")

        try:
            await start_tmux_sync_service()
        except Exception as e:
            logger.warning(f"Tmux sync service startup failed: {e}")

        try:
            await start_lease_recovery_service()
        except Exception as e:
            logger.warning(f"Lease recovery service startup failed: {e}")

        try:
            await start_relay_worker()
        except Exception as e:
            logger.warning(f"Relay worker startup failed: {e}")

        try:
            from forge_harness.webhook_server.services.telegram_notifier import (
                start_telegram_notifier,
            )

            await start_telegram_notifier()
        except Exception as e:
            logger.warning(f"Telegram notifier startup failed: {e}")

        _sse_cleanup_task = asyncio.create_task(_sse_session_cleanup_loop())

        yield

        logger.info("Shutting down FORGE Harness webhook server...")
        try:
            from forge_harness.webhook_server.services.telegram_notifier import (
                stop_telegram_notifier,
            )

            await stop_telegram_notifier()
        except Exception as e:
            logger.warning(f"Telegram notifier shutdown failed: {e}")

        try:
            await stop_relay_worker()
        except Exception as e:
            logger.warning(f"Relay worker shutdown failed: {e}")

        try:
            await stop_lease_recovery_service()
        except Exception as e:
            logger.warning(f"Lease recovery service shutdown failed: {e}")

        try:
            await stop_state_synchronizer()
        except Exception as e:
            logger.warning(f"State synchronizer shutdown failed: {e}")

        try:
            await stop_tmux_sync_service()
        except Exception as e:
            logger.warning(f"Tmux sync service shutdown failed: {e}")

        if _sse_cleanup_task and not _sse_cleanup_task.done():
            _sse_cleanup_task.cancel()
            try:
                await _sse_cleanup_task
            except asyncio.CancelledError:
                pass

    # -------------------------------------------------------------------------
    # FastAPI app
    # -------------------------------------------------------------------------
    app = FastAPI(
        title="FORGE Harness Webhooks",
        description="Webhook endpoints for human gate resolution and approval queue management",
        version="1.0.0",
        lifespan=lifespan,
    )

    # -------------------------------------------------------------------------
    # CORS
    # -------------------------------------------------------------------------
    cors_origins = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5176",
        "http://localhost:5177",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
        "http://127.0.0.1:5176",
        "http://127.0.0.1:5177",
        "http://localhost:3000",
        "https://command-center.forge.app",
        "http://nova.queue-great.ts.net:5173",
        "http://nova.queue-great.ts.net:8080",
    ]

    dashboard_url = os.environ.get("FORGE_DASHBOARD_URL")
    if dashboard_url and dashboard_url not in cors_origins:
        cors_origins.append(dashboard_url)
        logger.info(f"Added custom CORS origin from FORGE_DASHBOARD_URL: {dashboard_url}")

    _cors_allowed_methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
    _cors_allowed_headers = [
        "Authorization",
        "Content-Type",
        "X-Correlation-ID",
        "X-Request-ID",
        "X-Requested-With",
        "Accept",
    ]

    _forge_env = os.environ.get("FORGE_ENV", "production").lower()
    _is_dev_mode = _forge_env in ("development", "dev", "local")
    if not _is_dev_mode:
        _unsafe_origins = [
            o for o in cors_origins if o == "*" or "localhost" in o or "127.0.0.1" in o
        ]
        if _unsafe_origins:
            logger.warning(
                "CORS configuration includes localhost/wildcard origins in a "
                "non-development environment (FORGE_ENV=%s). "
                "Unsafe origins: %s. "
                "Set FORGE_ENV=development to suppress this warning.",
                _forge_env,
                _unsafe_origins,
            )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_origin_regex=r"https://.*\.ts\.net",
        allow_credentials=True,
        allow_methods=_cors_allowed_methods,
        allow_headers=_cors_allowed_headers,
    )


    # Security headers middleware (extracted to webhook_server/middleware.py)
    app.middleware("http")(security_headers_middleware)

    # Use provided handlers or create defaults
    _handler = handler or WebhookHandler(notification_harness=None)
    _approval_handler = approval_handler or get_approval_handler()
    if approval_handler is not None:
        # Keep modular approval routes in sync with the injected handler.
        # api/approvals.py resolves handlers via get_approval_handler() singleton.
        from forge_harness.webhook_server.handlers import (
            approval_handler as approval_handler_module,
        )

        approval_handler_module._approval_handler = approval_handler
    _task_handler = get_task_handler()
    _handoff_handler = get_handoff_handler()
    _agent_registry = get_agent_registry()
    _auth_config = auth_config or AuthConfig.from_env()
    # Store auth config on app.state so router dependencies can access it
    app.state.auth_config = _auth_config
    _rate_limiter = RateLimiter(rate_limit_config or RateLimitConfig.from_env())
    _pattern_store = pattern_store or get_pattern_store()

    # Server metrics tracking
    _server_start_time = time.time()
    _request_counter = create_request_counter()

    # Store shared state on app for router access
    app.state.rate_limiter = _rate_limiter
    app.state.webhook_handler = _handler
    app.state.server_start_time = _server_start_time
    app.state.request_counter = _request_counter

    # Exception handlers
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Return standard error payloads for HTTP errors."""
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=api_response(
                error_code=error_code_from_status(exc.status_code),
                error_message=message,
            ),
        )

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Return standard error payloads for validation errors."""
        return JSONResponse(
            status_code=422,
            content=api_response(
                error_code="validation_error",
                error_message="Validation error",
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """Return standard error payloads for unexpected errors."""
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=api_response(
                error_code="internal_error",
                error_message="Internal server error",
            ),
        )

    # Add tracking and rate limiting middleware (extracted to webhook_server/middleware.py)
    app.add_middleware(RequestTrackingMiddleware, request_counter=_request_counter)
    app.add_middleware(WebhookRateLimitMiddleware, global_limiter=_rate_limiter)

    # Include modular API routers
    from .webhook_server.api import (
        agents_router,
        approvals_router,
        audit_router,
        auth_router,
        balancer_router,
        canary_router,
        claims_router,
        completions_router,
        config_router,
        content_router,
        dashboard_router,
        decisions_router,
        decomposition_router,
        domains_router,
        errors_router,
        events_router,
        features_router,
        fleet_summary_router,
        graduation_router,
        handoffs_router,
        health_router,
        heartbeat_loop_router,
        intake_router,
        lanes_router,
        lead_state_router,
        # Legacy routers (extracted from inline routes in this file)
        legacy_agents_router,
        legacy_config_router,
        legacy_health_router,
        legacy_prime_router,
        legacy_sse_router,
        legacy_webhooks_router,
        memories_router,
        messages_router,
        mvp_check_router,
        nodes_router,
        orchestrator_events_router,
        patterns_router,
        pipelines_router,
        portfolio_router,
        prime_router,
        quality_router,
        ralph_router,
        relay_router,
        sessions_router,
        slo_router,
        state_sync_router,
        supervisor_router,
        tasks_router,
        telegram_router,
        version_router,
        webhooks_router,
        xnode_router,
    )

    app.include_router(health_router)
    app.include_router(webhooks_router)
    app.include_router(config_router)
    app.include_router(version_router)
    app.include_router(approvals_router)
    app.include_router(claims_router)
    app.include_router(dashboard_router)
    app.include_router(tasks_router)
    app.include_router(agents_router)
    app.include_router(patterns_router)
    app.include_router(pipelines_router)
    app.include_router(decisions_router)
    app.include_router(events_router)
    app.include_router(quality_router)
    app.include_router(mvp_check_router)
    app.include_router(content_router)
    app.include_router(ralph_router)
    app.include_router(sessions_router)
    app.include_router(decomposition_router)
    app.include_router(handoffs_router)
    app.include_router(heartbeat_loop_router)
    app.include_router(lead_state_router)
    app.include_router(memories_router)
    app.include_router(messages_router)
    app.include_router(nodes_router)
    app.include_router(relay_router)
    app.include_router(auth_router)
    app.include_router(xnode_router)
    app.include_router(lanes_router)
    app.include_router(audit_router)
    app.include_router(canary_router)
    app.include_router(balancer_router)
    app.include_router(features_router)
    app.include_router(fleet_summary_router)
    app.include_router(telegram_router)
    app.include_router(graduation_router)
    app.include_router(intake_router)
    app.include_router(slo_router)
    app.include_router(portfolio_router)
    app.include_router(domains_router)
    app.include_router(errors_router)
    app.include_router(supervisor_router)
    app.include_router(state_sync_router)
    app.include_router(prime_router)
    app.include_router(completions_router)
    app.include_router(orchestrator_events_router)

    # Legacy routers (extracted from inline route handlers in this file)
    app.include_router(legacy_health_router)
    app.include_router(legacy_agents_router)
    app.include_router(legacy_webhooks_router)
    app.include_router(legacy_sse_router)
    app.include_router(legacy_config_router)
    app.include_router(legacy_prime_router)

    # Modular routers use verify_unified_auth (JWT + API key + legacy token
    # + localhost bypass).  No dependency_overrides needed — unified auth is
    # a strict superset of the legacy static-token check.

    # Register OpenClaw gateway routes (lazy import to avoid circular dependency)
    from .openclaw import register_openclaw_routes

    register_openclaw_routes(app, auth_config)

    return app


# =============================================================================
# Standalone Server
# =============================================================================


def run_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    notification_harness: Any = None,
    approval_queue: Any = None,
    orchestrator: Any = None,
    auth_config: AuthConfig | None = None,
) -> None:
    """Run webhook server standalone.

    Args:
        host: Host to bind to
        port: Port to listen on
        notification_harness: NotificationHarness instance
        approval_queue: ApprovalQueueHarness instance
        orchestrator: OrchestrationHarness instance for pipeline resumption
        auth_config: Authentication configuration
    """
    try:
        import uvicorn
    except ImportError:
        raise ImportError("uvicorn not installed. Run: uv add uvicorn")

    # Create handlers
    handler = WebhookHandler(notification_harness=notification_harness)
    approval_handler = ApprovalQueueHandler(
        approval_queue=approval_queue,
        orchestrator=orchestrator,
    )

    # Create app
    app = create_app(
        handler=handler,
        approval_handler=approval_handler,
        auth_config=auth_config or AuthConfig.from_env(),
    )

    if app is None:
        raise RuntimeError("Failed to create FastAPI app - is FastAPI installed?")

    # Run server
    logger.info(f"Starting webhook server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


# =============================================================================
# Webhook-Enabled Human Gate
# =============================================================================


@dataclass
class PendingGate:
    """Represents a pending human gate waiting for webhook response.

    Attributes:
        notification_id: Unique ID for this gate
        event: asyncio.Event to signal when response received
        response: The webhook response once received
        created_at: When the gate was created
    """

    notification_id: str
    event: asyncio.Event
    response: WebhookPayload | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class WebhookHumanGate:
    """Human gate that uses webhooks instead of polling.

    Unlike the polling-based HumanGate in harness_registry.py,
    this gate registers for webhook callbacks and awaits them.
    """

    def __init__(
        self,
        notification_harness: Any,
        webhook_handler: WebhookHandler | None = None,
        callback_url: str = "",
    ):
        """Initialize webhook human gate.

        Args:
            notification_harness: For sending notifications
            webhook_handler: For receiving webhook responses
            callback_url: URL where webhooks will be received
        """
        self.notification = notification_harness
        self.webhook_handler = webhook_handler
        self.callback_url = callback_url
        self._pending_gates: dict[str, PendingGate] = {}

    def _generate_notification_id(self) -> str:
        """Generate a unique notification ID."""
        return f"gate_{uuid.uuid4().hex[:12]}"

    async def await_feedback(
        self,
        page_ids: list[str] | None = None,
        message: str = "Review required",
        timeout_hours: float = 24.0,
    ) -> dict[str, Any]:
        """Wait for human feedback via webhook.

        Args:
            page_ids: Optional Notion page IDs to review
            message: Message to display
            timeout_hours: How long to wait

        Returns:
            Dict with approved_ids list and response details
        """
        notification_id = self._generate_notification_id()

        # Create pending gate with asyncio.Event
        gate = PendingGate(
            notification_id=notification_id,
            event=asyncio.Event(),
        )
        self._pending_gates[notification_id] = gate

        try:
            # Send notification with action buttons
            if self.notification is not None:
                await self.notification.notify(
                    message=message,
                    notification_id=notification_id,
                    callback_url=self.callback_url,
                    page_ids=page_ids or [],
                    actions=["approve", "reject"],
                )

            # Wait for webhook response with timeout
            timeout_seconds = timeout_hours * 3600
            try:
                await asyncio.wait_for(gate.event.wait(), timeout=timeout_seconds)
            except TimeoutError:
                logger.warning(
                    f"Human gate {notification_id} timed out after {timeout_hours} hours"
                )
                return {
                    "approved_ids": [],
                    "status": "timeout",
                    "notification_id": notification_id,
                    "message": f"Timed out after {timeout_hours} hours",
                }

            # Return results from webhook response
            response = gate.response
            if response is None:
                return {
                    "approved_ids": [],
                    "status": "error",
                    "notification_id": notification_id,
                    "message": "No response received",
                }

            approved = response.response_type == "approved"
            return {
                "approved_ids": page_ids if approved else [],
                "status": response.response_type,
                "notification_id": notification_id,
                "responder": response.responder,
                "message": response.message,
            }
        finally:
            # Clean up pending gate
            self._pending_gates.pop(notification_id, None)

    async def request_decision(
        self,
        question: str,
        options: list[str],
        context: dict[str, Any] | None = None,
        timeout_hours: float = 24.0,
    ) -> dict[str, Any]:
        """Request a human decision via webhook.

        Args:
            question: The question to ask
            options: Available options (rendered as buttons)
            context: Additional context
            timeout_hours: How long to wait

        Returns:
            Dict with decision and rationale
        """
        notification_id = self._generate_notification_id()

        # Create pending gate with asyncio.Event
        gate = PendingGate(
            notification_id=notification_id,
            event=asyncio.Event(),
        )
        self._pending_gates[notification_id] = gate

        try:
            # Send notification with option buttons
            if self.notification is not None:
                await self.notification.notify(
                    message=question,
                    notification_id=notification_id,
                    callback_url=self.callback_url,
                    context=context or {},
                    actions=options,
                )

            # Wait for webhook response with timeout
            timeout_seconds = timeout_hours * 3600
            try:
                await asyncio.wait_for(gate.event.wait(), timeout=timeout_seconds)
            except TimeoutError:
                logger.warning(
                    f"Decision gate {notification_id} timed out after {timeout_hours} hours"
                )
                return {
                    "decision": None,
                    "status": "timeout",
                    "notification_id": notification_id,
                    "message": f"Timed out after {timeout_hours} hours",
                }

            # Return decision from webhook response
            response = gate.response
            if response is None:
                return {
                    "decision": None,
                    "status": "error",
                    "notification_id": notification_id,
                    "message": "No response received",
                }

            return {
                "decision": response.response_type,
                "status": "resolved",
                "notification_id": notification_id,
                "responder": response.responder,
                "rationale": response.message,
            }
        finally:
            # Clean up pending gate
            self._pending_gates.pop(notification_id, None)

    def resolve_gate(self, notification_id: str, response: WebhookPayload) -> bool:
        """Called by webhook handler when response received.

        Args:
            notification_id: ID of the notification
            response: Webhook payload with response

        Returns:
            True if gate was found and resolved, False otherwise
        """
        gate = self._pending_gates.get(notification_id)
        if gate is None:
            logger.warning(f"No pending gate found for notification {notification_id}")
            return False

        # Store response and signal event
        gate.response = response
        gate.event.set()
        logger.info(f"Resolved gate {notification_id} with response: {response.response_type}")
        return True

    def get_pending_gates(self) -> list[str]:
        """Get list of pending gate notification IDs.

        Returns:
            List of notification IDs waiting for responses
        """
        return list(self._pending_gates.keys())


# Default app instance for uvicorn (Railway/production deployment)
# This will be created with default configuration from environment variables
# Skip app creation during test imports to prevent hanging

if "pytest" not in sys.modules and os.environ.get("FORGE_SKIP_APP_INIT") != "1":
    try:
        # Import approval queue factory
        from .approval_queue import create_approval_queue_from_env

        # Create file-based approval queue from environment variables
        # Uses FORGE_APPROVALS_DIR or APPROVALS_DIR for storage path
        approval_queue = create_approval_queue_from_env()
        logger.info(f"Created approval queue with storage: {approval_queue.storage}")

        # Create approval handler with the configured queue
        approval_handler = ApprovalQueueHandler(approval_queue=approval_queue)

        # Create default app instance with environment-based configuration
        app = create_app(
            handler=None,  # Will create default handler
            approval_handler=approval_handler,
            auth_config=AuthConfig.from_env(),
        )
        if app:
            logger.info("Default FastAPI app instance created successfully with approval queue")
    except Exception as e:
        logger.warning(f"Failed to create default app instance: {e}")
        import traceback

        traceback.print_exc()
        app = None
else:
    # During tests or when explicitly skipped, don't create the app
    app = None


# =============================================================================
# __main__ entry point: run uvicorn when executed as python -m forge_harness.webhook_server_main
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.environ.get("WEBHOOK_PORT", "8080"))
    if app is None:
        logger.error("Cannot start server: app not initialized (check FORGE_SKIP_APP_INIT)")
        sys.exit(1)
    logger.info("Starting webhook server on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)
