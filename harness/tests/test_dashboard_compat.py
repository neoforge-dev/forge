"""Tests for dashboard deprecation notice and compatibility mode.

CP-1004: Legacy TUI dashboard deprecation toward Command Center PWA.

Run: uv run pytest tests/test_dashboard_compat.py -v
"""

from __future__ import annotations

from typing import Any

import pytest

from forge_harness.dashboard import COMPATIBILITY_MODE, create_dashboard, deprecation_notice


class TestDeprecationNotice:
    """Test deprecation notice functionality."""

    def test_deprecation_notice_returns_string(self) -> None:
        """deprecation_notice() returns a non-empty string."""
        notice = deprecation_notice()
        assert isinstance(notice, str)
        assert len(notice) > 0

    def test_deprecation_notice_contains_warning(self) -> None:
        """Notice contains DEPRECATION NOTICE marker."""
        notice = deprecation_notice()
        assert "DEPRECATION NOTICE" in notice

    def test_deprecation_notice_mentions_command_center(self) -> None:
        """Notice mentions Command Center as migration target."""
        notice = deprecation_notice()
        assert "Command Center" in notice

    def test_deprecation_notice_mentions_alternatives(self) -> None:
        """Notice provides migration alternatives."""
        notice = deprecation_notice()
        assert "forge-harness dashboard" in notice or "command-center" in notice


class TestCompatibilityMode:
    """Test COMPATIBILITY_MODE constant."""

    def test_compatibility_mode_is_bool(self) -> None:
        """COMPATIBILITY_MODE is a boolean."""
        assert isinstance(COMPATIBILITY_MODE, bool)

    def test_compatibility_mode_defaults_true(self) -> None:
        """COMPATIBILITY_MODE defaults to True for backward compatibility."""
        assert COMPATIBILITY_MODE is True


class TestCreateDashboard:
    """Test create_dashboard factory with deprecation behavior."""

    def test_create_dashboard_returns_instance(self) -> None:
        """create_dashboard returns a ForgeDashboard instance."""
        from forge_harness.dashboard import ForgeDashboard

        dashboard = create_dashboard()
        assert isinstance(dashboard, ForgeDashboard)

    def test_create_dashboard_accepts_forge_root(self) -> None:
        """create_dashboard accepts forge_root parameter."""
        from pathlib import Path

        dashboard = create_dashboard(forge_root=Path("/tmp"))
        assert dashboard.forge_root == Path("/tmp")

    def test_create_dashboard_accepts_api_url(self) -> None:
        """create_dashboard accepts api_url parameter."""
        dashboard = create_dashboard(api_url="http://localhost:8080")
        assert dashboard.api_url == "http://localhost:8080"

    def test_create_dashboard_logs_deprecation_when_enabled(self, mocker: Any) -> None:
        """create_dashboard logs deprecation warning when COMPATIBILITY_MODE is True."""
        import forge_harness.dashboard as dashboard_module

        mock_logger = mocker.patch.object(dashboard_module, "logger")

        create_dashboard()

        if COMPATIBILITY_MODE:
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args[0][0]
            assert "DEPRECATION NOTICE" in call_args
        else:
            mock_logger.warning.assert_not_called()
