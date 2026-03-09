"""Tests for forge approve interactive picker."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from forge_harness.cli_v2.approve import _run_interactive_picker


def test_interactive_picker_handles_empty_queue():
    """Interactive picker should exit cleanly when no approvals are pending."""
    queue = AsyncMock()
    queue.list_pending.return_value = []

    with patch("forge_harness.approval_queue.create_approval_queue", return_value=queue):
        _run_interactive_picker(ctx=Mock())


def test_interactive_picker_can_skip_selected_request():
    """Interactive picker should allow selecting and skipping a request."""
    request = SimpleNamespace(
        id="apr_123",
        type=SimpleNamespace(value="deploy"),
        domain="codeswiftr-com",
        age_hours=1.5,
        title="Deploy backend",
        description="Deploy API to staging",
    )
    queue = AsyncMock()
    queue.list_pending.return_value = [request]

    ctx = Mock()
    with (
        patch("forge_harness.approval_queue.create_approval_queue", return_value=queue),
        patch("forge_harness.cli_v2.approve.click.prompt", side_effect=[1, "skip"]),
    ):
        _run_interactive_picker(ctx=ctx)

    ctx.invoke.assert_not_called()

