"""
Forge v3 Python Worker Adapter
==============================

Connects to the Go orchestrator via WebSocket, registers as an agent,
and executes assigned tasks. Uses ADR-011 WebSocket message envelope (v, type, id, payload, ts).
"""

import asyncio
import json
import logging
import socket
import uuid
from datetime import UTC, datetime
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger("forge_harness.worker")

# Protocol version and message envelope (matches Go websocket.go WSMessage)
PROTOCOL_VERSION = "1"


def _envelope(msg_type: str, payload: dict[str, Any], task_id: str | None = None) -> dict[str, Any]:
    """Build ADR-011 WSMessage envelope for the Go server."""
    return {
        "v": PROTOCOL_VERSION,
        "type": msg_type,
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "task_id": task_id or "",
        "payload": payload,
        "ts": datetime.now(UTC).isoformat(),
    }

class ForgeWorker:
    """Python worker adapter for Forge v3 orchestrator."""

    def __init__(
        self,
        server_url: str,
        agent_id: str | None = None,
        capabilities: list[str] | None = None,
        heartbeat_interval: float = 30.0,
    ):
        self.server_url = server_url
        self.agent_id = agent_id or f"python-{socket.gethostname()}"
        self.capabilities = capabilities or ["shell", "python"]
        self.heartbeat_interval = heartbeat_interval

        self._ws: websockets.WebSocketClientProtocol | None = None
        self._running = False
        self._backoff = 1.0
        self._max_backoff = 60.0

    async def start(self):
        """Start the worker loop with auto-reconnect."""
        self._running = True
        logger.info(
            "ForgeWorker starting — agent_id=%s url=%s capabilities=%s",
            self.agent_id, self.server_url, self.capabilities
        )

        while self._running:
            try:
                await self._connect_and_run()
                # If it returns cleanly, reset backoff
                self._backoff = 1.0
            except (ConnectionClosed, OSError, ConnectionRefusedError) as e:
                logger.warning("Connection lost/failed: %s. Retrying in %.1fs", e, self._backoff)
            except Exception as e:
                logger.error("Unexpected error in worker loop: %s", e, exc_info=True)

            if not self._running:
                break

            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, self._max_backoff)

    async def stop(self):
        """Stop the worker."""
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _connect_and_run(self):
        """Connect to WebSocket and handle messages."""
        async with websockets.connect(self.server_url) as ws:
            self._ws = ws
            logger.info("WebSocket connected to %s", self.server_url)

            # 1. Register with full WSMessage envelope (Go expects handshake first)
            await self._send_envelope(
                "agent.register",
                {
                    "agent_id": self.agent_id,
                    "name": self.agent_id,
                    "node": socket.gethostname(),
                    "tier": "worker",
                    "capabilities": self.capabilities,
                },
            )

            # 2. Start heartbeat and listener
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            try:
                async for message in ws:
                    try:
                        data = json.loads(message)
                        await self._handle_message(data)
                    except json.JSONDecodeError:
                        logger.warning("Received invalid JSON: %r", message)
                    except Exception as e:
                        logger.error("Error handling message: %s", e, exc_info=True)
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def _heartbeat_loop(self):
        """Send periodic pong (application-level) to match Go heartbeat."""
        while self._running:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                await self._send_envelope("pong", {"ts": datetime.now(UTC).isoformat()})
            except Exception as e:
                logger.warning("Failed to send heartbeat: %s", e)
                break

    async def _handle_message(self, data: dict[str, Any]):
        """Route incoming messages (Go sends WSMessage envelope)."""
        msg_type = data.get("type")
        payload = data.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        task_id = data.get("task_id") or (payload.get("task_id") if isinstance(payload, dict) else None)
        if msg_type == "task.assigned":
            if not task_id and isinstance(payload, dict):
                task_id = payload.get("task_id")
            task_payload = payload if isinstance(payload, dict) else {}
            asyncio.create_task(self._execute_task(task_id or "unknown", task_payload))
        elif msg_type == "agent.register_ack":
            logger.info("Registered: %s", payload)
        elif msg_type == "heartbeat.ack":
            logger.debug("Heartbeat acknowledged")
        else:
            logger.debug("Unhandled message type: %s", msg_type)

    async def _execute_task(self, task_id: str, payload: dict[str, Any]):
        """Execute a task and report status using WSMessage envelope."""
        if not task_id or task_id == "unknown":
            logger.error("Received task.assigned without task_id")
            return

        logger.info("Executing task %s", task_id)

        try:
            await self._send_envelope(
                "task.started",
                {"task_id": task_id},
                task_id=task_id,
            )

            command = payload.get("command")
            if not command and isinstance(payload.get("payload"), dict):
                command = payload["payload"].get("command")
            if not command:
                await self._send_envelope(
                    "task.completed",
                    {"message": "No command provided, nothing to do."},
                    task_id=task_id,
                )
                return

            logger.info("Running command for task %s: %s", task_id, command)

            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            result = {
                "stdout": stdout.decode().strip(),
                "stderr": stderr.decode().strip(),
                "exit_code": process.returncode,
            }

            if process.returncode == 0:
                await self._send_envelope(
                    "task.completed",
                    {"result": result},
                    task_id=task_id,
                )
                logger.info("Task %s completed successfully", task_id)
            else:
                await self._send_envelope(
                    "task.failed",
                    {
                        "error": f"Command failed with exit code {process.returncode}",
                        "result": result,
                    },
                    task_id=task_id,
                )
                logger.warning("Task %s failed with exit code %d", task_id, process.returncode)

        except Exception as e:
            logger.error("Failed to execute task %s: %s", task_id, e, exc_info=True)
            try:
                await self._send_envelope(
                    "task.failed",
                    {"error": str(e)},
                    task_id=task_id,
                )
            except Exception as send_err:
                logger.error("Failed to send task.failed for %s: %s", task_id, send_err)

    async def _send_envelope(self, msg_type: str, payload: dict[str, Any], task_id: str | None = None):
        """Send a WSMessage-envelope JSON message to the Go server."""
        if self._ws:
            msg = _envelope(msg_type, payload, task_id)
            await self._ws.send(json.dumps(msg))
        else:
            raise ConnectionError("WebSocket not connected")

    async def _send(self, message: dict[str, Any]):
        """Send a JSON message over the WebSocket (legacy flat format). Prefer _send_envelope for Go."""
        if self._ws:
            await self._ws.send(json.dumps(message))
        else:
            raise ConnectionError("WebSocket not connected")

if __name__ == "__main__":
    # Minimal CLI for the worker
    import argparse

    parser = argparse.ArgumentParser(description="Forge v3 Python Worker")
    parser.add_argument("--url", default="ws://localhost:8082/ws", help="Orchestrator WS URL")
    parser.add_argument("--agent-id", help="Override agent ID")
    parser.add_argument("--capabilities", nargs="+", help="Agent capabilities")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    worker = ForgeWorker(
        server_url=args.url,
        agent_id=args.agent_id,
        capabilities=args.capabilities
    )

    try:
        asyncio.run(worker.start())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
