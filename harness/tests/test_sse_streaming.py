"""Tests for the SSE streaming endpoint GET /api/sessions/{window_name}/stream.

Tests cover:
- Correct Content-Type header (text/event-stream)
- SSE event format validation (data: JSON payload)
- Keepalive comment format
- Graceful handling of unknown sessions (404)
- Session output diffing helper
- Generator behaviour with mocked session tracker
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_tracker(sessions: list[str], output_lines: list[str]) -> MagicMock:
    """Build a mock SessionTracker.

    Args:
        sessions: List of window names reported by list_tmux_sessions().
        output_lines: Lines returned by get_session_output() as a single string.

    Returns:
        A configured MagicMock that mimics SessionTracker.
    """
    tracker = MagicMock()
    tracker.list_tmux_sessions.return_value = sessions
    tracker.get_session_output.return_value = "\n".join(output_lines)
    return tracker


async def _collect_events(
    gen,
    max_events: int = 5,
    timeout: float = 3.0,
) -> list[str]:
    """Drain an async generator and collect up to *max_events* raw SSE frames.

    Args:
        gen: The async generator to drain.
        max_events: Stop after collecting this many non-empty frames.
        timeout: Wall-clock timeout in seconds.

    Returns:
        A list of raw SSE frame strings.
    """
    frames: list[str] = []
    try:
        async with asyncio.timeout(timeout):
            async for frame in gen:
                if frame.strip():
                    frames.append(frame)
                if len(frames) >= max_events:
                    break
    except TimeoutError:
        pass
    return frames


# ---------------------------------------------------------------------------
# Tests for _diff_lines helper
# ---------------------------------------------------------------------------

class TestDiffLines:
    """Unit tests for the _diff_lines() helper function."""

    def _diff(self, previous: list[str], current: list[str]) -> list[str]:
        from forge_harness.webhook_server.api.sessions import _diff_lines

        return _diff_lines(previous, current)

    def test_empty_previous_returns_all_current(self):
        """When there is no previous output all current lines are new."""
        result = self._diff([], ["line1", "line2", "line3"])
        assert result == ["line1", "line2", "line3"]

    def test_identical_snapshots_return_empty(self):
        """Identical consecutive snapshots produce no new lines."""
        lines = ["line1", "line2"]
        result = self._diff(lines, lines)
        assert result == []

    def test_appended_lines_detected(self):
        """New lines appended at the end of the buffer are returned."""
        previous = ["a", "b", "c"]
        current = ["a", "b", "c", "d", "e"]
        result = self._diff(previous, current)
        assert result == ["d", "e"]

    def test_no_overlap_returns_entire_current(self):
        """When there is no overlap the whole current snapshot is treated as new."""
        previous = ["old1", "old2"]
        current = ["new1", "new2", "new3"]
        result = self._diff(previous, current)
        assert result == ["new1", "new2", "new3"]

    def test_single_new_line(self):
        """Exactly one new line is correctly detected."""
        previous = ["x", "y"]
        current = ["x", "y", "z"]
        result = self._diff(previous, current)
        assert result == ["z"]

    def test_rolling_buffer_shrinks(self):
        """If the buffer shrinks (tmux scroll) and content is completely different, all current lines are returned."""
        previous = ["old_a", "old_b", "old_c", "old_d"]
        current = ["new_x", "new_y"]
        result = self._diff(previous, current)
        # No overlap exists; treat everything as new
        assert result == ["new_x", "new_y"]


# ---------------------------------------------------------------------------
# Tests for the SSE generator
# ---------------------------------------------------------------------------

class TestSessionOutputGenerator:
    """Tests for _session_output_generator() async generator."""

    @pytest.fixture
    def mock_request(self) -> MagicMock:
        """A mock FastAPI Request that never reports disconnection."""
        req = MagicMock()
        req.is_disconnected = AsyncMock(return_value=False)
        return req

    @pytest.fixture
    def disconnected_request(self) -> MagicMock:
        """A mock FastAPI Request that immediately reports disconnection."""
        req = MagicMock()
        req.is_disconnected = AsyncMock(return_value=True)
        return req

    @pytest.mark.asyncio
    async def test_emits_new_lines_as_sse_data_events(self, mock_request):
        """New output lines are emitted as properly-formatted SSE data frames."""
        from forge_harness.webhook_server.api.sessions import _session_output_generator

        initial_lines = ["existing line 1", "existing line 2"]
        new_lines = initial_lines + ["new line 3", "new line 4"]

        tracker = MagicMock()
        # First call seeds the baseline; subsequent calls return additional lines
        tracker.get_session_output.side_effect = [
            "\n".join(initial_lines),  # seed snapshot
            "\n".join(new_lines),      # first poll — two new lines
            "\n".join(new_lines),      # keep stable for remaining polls
            "\n".join(new_lines),
            "\n".join(new_lines),
        ]

        # The generator imports get_session_tracker inside the function body, so
        # we must patch the name in its source module, not in the sessions module.
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=tracker,
        ):
            gen = _session_output_generator("cursor", mock_request)
            frames = await _collect_events(gen, max_events=2, timeout=5.0)

        assert len(frames) == 2, f"Expected 2 data frames, got {frames!r}"
        for frame in frames:
            assert frame.startswith("data: "), f"Frame does not start with 'data: ': {frame!r}"

        payload = json.loads(frames[0][len("data: "):].strip())
        assert "line" in payload, "Payload missing 'line' key"
        assert "timestamp" in payload, "Payload missing 'timestamp' key"
        assert payload["line"] == "new line 3"

    @pytest.mark.asyncio
    async def test_sse_event_format_is_correct(self, mock_request):
        """Each SSE frame ends with a double newline as required by the spec."""
        from forge_harness.webhook_server.api.sessions import _session_output_generator

        tracker = MagicMock()
        tracker.get_session_output.side_effect = [
            "",              # seed — empty baseline
            "first line",   # one new line on first poll
            "first line",
            "first line",
        ]

        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=tracker,
        ):
            gen = _session_output_generator("cursor", mock_request)
            frames = await _collect_events(gen, max_events=1, timeout=5.0)

        assert frames, "No frames received"
        frame = frames[0]
        assert frame.endswith("\n\n"), (
            f"SSE frame must end with double newline, got {frame!r}"
        )
        assert frame.startswith("data: "), (
            f"SSE data frame must start with 'data: ', got {frame!r}"
        )

    @pytest.mark.asyncio
    async def test_keepalive_comment_is_sent_after_idle_period(self, mock_request):
        """A keepalive comment is yielded when no new output arrives within the interval."""
        from forge_harness.webhook_server.api import sessions as sessions_module
        from forge_harness.webhook_server.api.sessions import _session_output_generator

        # Force the keepalive interval to 0 so the very first idle poll triggers it.
        original_interval = sessions_module._SSE_KEEPALIVE_INTERVAL_SECONDS
        sessions_module._SSE_KEEPALIVE_INTERVAL_SECONDS = 0

        try:
            tracker = MagicMock()
            # Always return the same output — nothing new to emit.
            tracker.get_session_output.return_value = "steady output"

            with patch(
                "forge_harness.session_tracker.get_session_tracker",
                return_value=tracker,
            ):
                gen = _session_output_generator("cursor", mock_request)
                frames = await _collect_events(gen, max_events=1, timeout=5.0)
        finally:
            sessions_module._SSE_KEEPALIVE_INTERVAL_SECONDS = original_interval

        assert frames, "No keepalive frames received"
        keepalive = frames[0]
        assert keepalive.startswith(": "), (
            f"Keepalive frame must start with ': ', got {keepalive!r}"
        )
        assert "keepalive" in keepalive, (
            f"Keepalive frame must contain 'keepalive', got {keepalive!r}"
        )
        assert keepalive.endswith("\n\n"), (
            f"Keepalive frame must end with double newline, got {keepalive!r}"
        )

    @pytest.mark.asyncio
    async def test_generator_stops_on_client_disconnect(self, disconnected_request):
        """The generator yields nothing and exits cleanly when the client is already disconnected."""
        from forge_harness.webhook_server.api.sessions import _session_output_generator

        tracker = MagicMock()
        tracker.get_session_output.return_value = "some output\nmore output"

        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=tracker,
        ):
            gen = _session_output_generator("cursor", disconnected_request)
            frames = await _collect_events(gen, max_events=10, timeout=2.0)

        assert frames == [], (
            "Generator should produce no frames after client disconnects immediately"
        )

    @pytest.mark.asyncio
    async def test_payload_contains_valid_iso8601_timestamp(self, mock_request):
        """The timestamp in each SSE payload is a valid ISO-8601 UTC string."""
        from forge_harness.webhook_server.api.sessions import _session_output_generator

        tracker = MagicMock()
        tracker.get_session_output.side_effect = [
            "",
            "a new line",
            "a new line",
        ]

        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=tracker,
        ):
            gen = _session_output_generator("cursor", mock_request)
            frames = await _collect_events(gen, max_events=1, timeout=5.0)

        assert frames, "Expected at least one data frame"
        raw_payload = frames[0][len("data: "):].strip()
        payload = json.loads(raw_payload)
        ts = payload["timestamp"]

        # Must be parseable as an aware datetime
        from datetime import datetime

        dt = datetime.fromisoformat(ts)
        assert dt.tzinfo is not None, "Timestamp must be timezone-aware"


# ---------------------------------------------------------------------------
# Tests for the HTTP endpoint (via FastAPI TestClient)
# ---------------------------------------------------------------------------

class TestStreamSessionOutputEndpoint:
    """Integration-style tests for GET /api/sessions/{window_name}/stream."""

    @pytest.fixture
    def test_app(self):
        """Build a minimal FastAPI app with the sessions router and auth disabled."""
        from fastapi import FastAPI

        from forge_harness.webhook_server.api.sessions import router

        app = FastAPI()

        # Disable auth for tests by overriding verify_auth dependency
        from forge_harness.webhook_server.core.dependencies import verify_auth

        app.dependency_overrides[verify_auth] = lambda: {"sub": "test", "permissions": ["read"]}
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, test_app):
        """Create a synchronous TestClient (does not consume SSE stream)."""
        from fastapi.testclient import TestClient

        return TestClient(test_app, raise_server_exceptions=False)

    def test_stream_endpoint_returns_404_for_unknown_session(self, client):
        """Requesting a stream for a non-existent session returns 404."""
        tracker = _make_mock_tracker(sessions=[], output_lines=[])

        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=tracker,
        ):
            response = client.get("/api/sessions/nonexistent/stream")

        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "not_found"

    @pytest.mark.asyncio
    async def test_stream_endpoint_returns_correct_content_type(self, test_app):
        """The stream endpoint declares Content-Type: text/event-stream."""
        from httpx import ASGITransport, AsyncClient

        tracker = _make_mock_tracker(sessions=["cursor"], output_lines=["line1"])

        # Patch asyncio.sleep to be near-instant so the generator loop yields
        # control quickly and is cancelled cleanly when the client disconnects.
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=tracker,
        ), patch(
            "forge_harness.webhook_server.api.sessions.asyncio.sleep",
            new=AsyncMock(return_value=None),
        ):
            # Use async streaming so we read only the headers without consuming
            # the infinite SSE body.
            async with AsyncClient(
                transport=ASGITransport(app=test_app),
                base_url="http://test",
            ) as client:
                async with client.stream(
                    "GET",
                    "/api/sessions/cursor/stream",
                    timeout=2.0,
                ) as response:
                    content_type = response.headers.get("content-type", "")
                    assert "text/event-stream" in content_type, (
                        f"Expected text/event-stream, got: {content_type}"
                    )

    @pytest.mark.asyncio
    async def test_stream_endpoint_returns_cache_control_no_cache(self, test_app):
        """The stream endpoint sets Cache-Control: no-cache."""
        from httpx import ASGITransport, AsyncClient

        tracker = _make_mock_tracker(sessions=["cursor"], output_lines=["line1"])

        # Patch asyncio.sleep to be near-instant so the generator loop yields
        # control quickly and is cancelled cleanly when the client disconnects.
        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=tracker,
        ), patch(
            "forge_harness.webhook_server.api.sessions.asyncio.sleep",
            new=AsyncMock(return_value=None),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=test_app),
                base_url="http://test",
            ) as client:
                async with client.stream(
                    "GET",
                    "/api/sessions/cursor/stream",
                    timeout=2.0,
                ) as response:
                    cache_control = response.headers.get("cache-control", "")
                    assert "no-cache" in cache_control, (
                        f"Expected no-cache in Cache-Control, got: {cache_control}"
                    )

    def test_stream_endpoint_404_body_structure(self, client):
        """404 response follows the standard api_response envelope."""
        tracker = _make_mock_tracker(sessions=[], output_lines=[])

        with patch(
            "forge_harness.session_tracker.get_session_tracker",
            return_value=tracker,
        ):
            response = client.get("/api/sessions/missing_window/stream")

        assert response.status_code == 404
        body = response.json()
        assert "success" in body
        assert "data" in body
        assert "error" in body
        assert "timestamp" in body
        assert body["data"] is None
        assert body["error"]["code"] == "not_found"
