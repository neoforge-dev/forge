#!/usr/bin/env python3
"""E2E contract tests for V3 WebSocket protocol."""

import asyncio
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
import websockets
from websockets.exceptions import ConnectionClosedError


def _v3_binary_path() -> Path:
    """Return the expected path to the v3 server binary."""
    return Path("/home/bogdan/FORGE/cmd/forge-v3/forge-v3")


async def _wait_for_http(url: str, timeout: float = 10.0) -> None:
    """Wait until an HTTP endpoint responds or timeout expires."""
    start = time.time()
    last_error: Exception | None = None

    while time.time() - start < timeout:
        try:
            await asyncio.to_thread(urllib.request.urlopen, url, timeout=2)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            await asyncio.sleep(0.2)

    raise RuntimeError(f"HTTP endpoint not ready at {url}: {last_error}")


@pytest.fixture
def v3_server():
    """Start the Go v3 server binary on test ports and tear it down."""
    binary_path = _v3_binary_path()
    if not binary_path.exists():
        pytest.skip("V3 binary not found - build cmd/forge-v3/forge-v3 first")

    env = os.environ.copy()
    # Use test-specific ports to avoid clashing with any running instance
    env.setdefault("PORT", "18081")
    env.setdefault("WS_PORT", "18082")

    proc = subprocess.Popen(
        [str(binary_path)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Give the process a brief moment to start listening
    time.sleep(0.5)

    try:
        yield {
            "process": proc,
            "http_port": int(env["PORT"]),
            "ws_port": int(env["WS_PORT"]),
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.asyncio
async def test_agent_register_contract_matches_go_server(v3_server):
    """End-to-end: Python client sends agent.register that Go server accepts.

    This test verifies that the first WebSocket message (handshake) matches the
    Go server's expectations in cmd/forge-v3/websocket.go:
      - Envelope fields: v, type, id, task_id, payload
      - Payload contains: agent_id, name, node, tier, capabilities
    The server should respond with agent.register_ack instead of closing with 1011.
    """
    http_port = v3_server["http_port"]
    ws_port = v3_server["ws_port"]

    # Ensure the HTTP status/health API is up before attempting WebSocket
    await _wait_for_http(f"http://localhost:{http_port}/api/health")

    ws_url = f"ws://localhost:{ws_port}/ws"
    agent_id = "test-agent-contract"
    capabilities = ["code", "test"]

    try:
        async with websockets.connect(ws_url) as ws:
            # Handshake must follow WSMessage + registration payload schema
            handshake = {
                "v": "1",
                "type": "agent.register",
                "id": "test-handshake-1",
                "task_id": "",
                "payload": {
                    "agent_id": agent_id,
                    "name": "pytest-agent",
                    "node": "test-node",
                    "tier": "T1",
                    "capabilities": capabilities,
                },
            }

            await ws.send(json.dumps(handshake))

            # Expect an agent.register_ack from the server
            raw = await ws.recv()
            message = json.loads(raw)

            assert message["type"] == "agent.register_ack"
            assert message.get("v") in ("1", "1.0")

            payload = message.get("payload") or {}
            assert payload.get("agent_id") == agent_id
            assert payload.get("capabilities") == capabilities

    except ConnectionClosedError as exc:  # pragma: no cover - explicit failure path
        pytest.fail(f"WebSocket closed unexpectedly with code={exc.code}, reason={exc.reason}")

