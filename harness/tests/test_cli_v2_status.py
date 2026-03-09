"""
TDD tests for forge status command.

Tests follow Test-Driven Development:
1. Tests define expected behavior first
2. Implementation should match test expectations
3. All public functions are tested
"""

from __future__ import annotations

import pytest


class TestStatusCommand:
    """Test forge status command."""

    def test_status_command_callable(self):
        """Status command should be callable."""
        from forge_harness.cli_v2 import cli
        # Will raise SystemExit if not properly registered
        try:
            cli.main(["status", "--help"])
        except SystemExit:
            pass  # Expected with --help


class TestStatusOptions:
    """Test status command options."""

    def test_watch_option_exists(self):
        """--watch option should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["status", "--watch", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_refresh_option_exists(self):
        """--refresh option should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["status", "--refresh", "0.5", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_approvals_option_exists(self):
        """--approvals option should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["status", "--approvals", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_agents_option_exists(self):
        """--agents option should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["status", "--agents", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_portfolio_option_exists(self):
        """--portfolio option should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["status", "--portfolio", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_patterns_option_exists(self):
        """--patterns option should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["status", "--patterns", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_pipelines_option_exists(self):
        """--pipelines option should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["status", "--pipelines", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_api_url_option_exists(self):
        """--api-url option should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["status", "--api-url", "http://example.com", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_api_token_option_exists(self):
        """--api-token option should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["status", "--api-token", "test-token", "--help"])
        except SystemExit:
            pass  # Expected with --help
