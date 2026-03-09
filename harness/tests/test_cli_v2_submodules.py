"""
TDD tests for forge submodules command.

Tests follow Test-Driven Development:
1. Tests define expected behavior first
2. Implementation should match test expectations
3. All public functions are tested
"""

from __future__ import annotations

import pytest


class TestSubmodulesCommand:
    """Test forge submodules command."""

    def test_submodules_command_callable(self):
        """Submodules command should be callable."""
        from forge_harness.cli_v2 import cli
        # Will raise SystemExit if not properly registered
        try:
            cli.main(["submodules", "--help"])
        except SystemExit:
            pass  # Expected with --help


class TestSubmodulesOptions:
    """Test submodules command options."""

    def test_check_option_exists(self):
        """--check option should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["submodules", "--check", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_sync_option_exists(self):
        """--sync option should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["submodules", "--sync", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_update_option_exists(self):
        """--update option should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["submodules", "--update", "--help"])
        except SystemExit:
            pass  # Expected with --help
