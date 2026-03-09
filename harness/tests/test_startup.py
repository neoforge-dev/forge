"""Startup smoke test for CI pipeline.

Verifies that create_app() works without errors and that key endpoints
are registered. This test catches import errors like missing routers
(e.g., graduation_router) before deployment.

Run with:
    uv run pytest harness/tests/test_startup.py -v
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Set env var to skip app init during import
os.environ["FORGE_SKIP_APP_INIT"] = "1"

# Mock aiofiles before any imports
aiofiles_mock = MagicMock()
aiofiles_mock.os = MagicMock()
sys.modules["aiofiles"] = aiofiles_mock
sys.modules["aiofiles.os"] = aiofiles_mock.os


class TestStartup:
    """Smoke tests for application startup."""

    def test_create_app_no_errors(self):
        """Test that create_app() can be called without errors."""
        # Patch all the handler imports at their source modules
        with (
            # Approval handler
            patch(
                "forge_harness.webhook_server.handlers.approval_handler.get_approval_handler",
                return_value=MagicMock(),
            ),
            # Task handler
            patch(
                "forge_harness.webhook_server.handlers.task_handler.get_task_handler",
                return_value=MagicMock(),
            ),
            # Handoff handler
            patch(
                "forge_harness.webhook_server.handlers.handoff_handler.get_handoff_handler",
                return_value=MagicMock(),
            ),
            # Agent registry
            patch(
                "forge_harness.webhook_server.services.agent_registry.get_agent_registry",
                return_value=MagicMock(),
            ),
            # Pattern store
            patch(
                "forge_harness.webhook_server.services.pattern_store.get_pattern_store",
                return_value=MagicMock(),
            ),
            # Event bus
            patch(
                "forge_harness.webhook_server.services.event_bus.get_event_bus",
                return_value=MagicMock(),
            ),
            # Start services that run during app creation
            patch("forge_harness.webhook_server_main.start_state_synchronizer", return_value=None),
            patch("forge_harness.webhook_server_main.start_tmux_sync_service", return_value=None),
            patch(
                "forge_harness.webhook_server_main.start_lease_recovery_service", return_value=None
            ),
        ):
            from forge_harness.webhook_server_main import create_app

            # This should not raise any exceptions
            app = create_app()

            # Verify app was created
            assert app is not None, "create_app() returned None"

    def test_health_endpoint(self):
        """Test that /health endpoint is accessible."""
        with (
            # Approval handler
            patch(
                "forge_harness.webhook_server.handlers.approval_handler.get_approval_handler",
                return_value=MagicMock(),
            ),
            # Task handler
            patch(
                "forge_harness.webhook_server.handlers.task_handler.get_task_handler",
                return_value=MagicMock(),
            ),
            # Handoff handler
            patch(
                "forge_harness.webhook_server.handlers.handoff_handler.get_handoff_handler",
                return_value=MagicMock(),
            ),
            # Agent registry
            patch(
                "forge_harness.webhook_server.services.agent_registry.get_agent_registry",
                return_value=MagicMock(),
            ),
            # Pattern store
            patch(
                "forge_harness.webhook_server.services.pattern_store.get_pattern_store",
                return_value=MagicMock(),
            ),
            # Event bus
            patch(
                "forge_harness.webhook_server.services.event_bus.get_event_bus",
                return_value=MagicMock(),
            ),
            # Start services
            patch("forge_harness.webhook_server_main.start_state_synchronizer", return_value=None),
            patch("forge_harness.webhook_server_main.start_tmux_sync_service", return_value=None),
            patch(
                "forge_harness.webhook_server_main.start_lease_recovery_service", return_value=None
            ),
        ):
            from forge_harness.webhook_server_main import create_app

            app = create_app()
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get("/health")

            assert response.status_code == 200, f"Health check failed: {response.text}"
            data = response.json()
            assert data.get("status") == "ok", f"Health status not ok: {data}"

    def test_graduation_router_imports(self):
        """Test that graduation_router can be imported without errors.

        This catches the specific issue where graduation_router might be
        missing from the API module causing deployment failures.
        """
        # This import happens inside create_app(), so we need to patch the
        # handlers and services to avoid additional failures
        with (
            # Approval handler
            patch(
                "forge_harness.webhook_server.handlers.approval_handler.get_approval_handler",
                return_value=MagicMock(),
            ),
            # Task handler
            patch(
                "forge_harness.webhook_server.handlers.task_handler.get_task_handler",
                return_value=MagicMock(),
            ),
            # Handoff handler
            patch(
                "forge_harness.webhook_server.handlers.handoff_handler.get_handoff_handler",
                return_value=MagicMock(),
            ),
            # Agent registry
            patch(
                "forge_harness.webhook_server.services.agent_registry.get_agent_registry",
                return_value=MagicMock(),
            ),
            # Pattern store
            patch(
                "forge_harness.webhook_server.services.pattern_store.get_pattern_store",
                return_value=MagicMock(),
            ),
            # Event bus
            patch(
                "forge_harness.webhook_server.services.event_bus.get_event_bus",
                return_value=MagicMock(),
            ),
            # Start services
            patch("forge_harness.webhook_server_main.start_state_synchronizer", return_value=None),
            patch("forge_harness.webhook_server_main.start_tmux_sync_service", return_value=None),
            patch(
                "forge_harness.webhook_server_main.start_lease_recovery_service", return_value=None
            ),
        ):
            # Try importing the graduation_router directly to verify it exists
            from forge_harness.webhook_server.api import graduation_router

            # Verify it's not None and has routes
            assert graduation_router is not None, "graduation_router is None"
            assert hasattr(graduation_router, "routes"), "graduation_router has no routes attribute"

    def test_app_has_routes(self):
        """Test that the created app has registered routes."""
        with (
            # Approval handler
            patch(
                "forge_harness.webhook_server.handlers.approval_handler.get_approval_handler",
                return_value=MagicMock(),
            ),
            # Task handler
            patch(
                "forge_harness.webhook_server.handlers.task_handler.get_task_handler",
                return_value=MagicMock(),
            ),
            # Handoff handler
            patch(
                "forge_harness.webhook_server.handlers.handoff_handler.get_handoff_handler",
                return_value=MagicMock(),
            ),
            # Agent registry
            patch(
                "forge_harness.webhook_server.services.agent_registry.get_agent_registry",
                return_value=MagicMock(),
            ),
            # Pattern store
            patch(
                "forge_harness.webhook_server.services.pattern_store.get_pattern_store",
                return_value=MagicMock(),
            ),
            # Event bus
            patch(
                "forge_harness.webhook_server.services.event_bus.get_event_bus",
                return_value=MagicMock(),
            ),
            # Start services
            patch("forge_harness.webhook_server_main.start_state_synchronizer", return_value=None),
            patch("forge_harness.webhook_server_main.start_tmux_sync_service", return_value=None),
            patch(
                "forge_harness.webhook_server_main.start_lease_recovery_service", return_value=None
            ),
        ):
            from forge_harness.webhook_server_main import create_app

            app = create_app()

            # Get all registered routes (filter out internal routes)
            routes = [route.path for route in app.routes]

            # The app should have at least the health endpoint
            assert "/health" in routes, "Health endpoint not registered"
            assert len(routes) > 1, "No routes registered in the app"
