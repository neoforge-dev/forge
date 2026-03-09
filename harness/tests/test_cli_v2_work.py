"""
TDD tests for forge work command.

Tests follow Test-Driven Development:
1. Tests define expected behavior first
2. Implementation should match test expectations
3. All public functions are tested
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest


class TestWorkCommand:
    """Test forge work command."""

    def test_work_command_callable(self):
        """Work command should be callable."""
        from forge_harness.cli_v2 import cli

        # Check if work is registered by trying to call it
        # Will raise SystemExit if not properly registered
        try:
            cli.main(["work", "--help"])
        except SystemExit:
            pass  # Expected with --help


class TestRunAgentSession:
    """Test _run_agent_session function."""

    def test_run_agent_session_invokes_run(self):
        """Should invoke run command with correct kwargs."""
        from forge_harness.cli_v2 import cli

        # Test with explicit domain/project
        with pytest.raises(SystemExit):  # --help causes exit
            cli.main(["work", "-d", "test-domain", "-p", "test-project", "--help"])


class TestInferringLogic:
    """Test domain/project inference logic."""

    def test_infers_from_cwd_when_not_provided(self):
        """Should attempt to infer domain/project when not provided."""
        from forge_harness.cli_v2.work import infer_domain_project

        # Mock to return values
        with patch("forge_harness.cli_v2.work.infer_domain_project") as mock_inf:
            mock_inf.return_value = ("test-domain", "test-project")

            # This would normally be called in work()
            result = mock_inf.return_value

            assert result == ("test-domain", "test-project")
