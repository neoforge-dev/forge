"""
Forge v3 WebSocket Worker Client

Connects to forge-v3 WebSocket hub and handles task lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

from ..logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class WorkerConfig:
    """Configuration for v3 worker."""
    agent_id: str
    orchestrator_url: str = "ws://localhost:8082/ws"
    http_url: str = "http://localhost:8081"
    capabilities: list[str] = field(default_factory=lambda: ["code", "test"])
    tier: str = "t2"
    name: str | None = None

    def __post_init__(self):
        if self.name is None:
            self.name = self.agent_id.split(":")[-1] if ":" in self.agent_id else self.agent_id


class ForgeWorker:
    """
    WebSocket worker that connects to forge-v3 daemon.
    
    Handles:
    - WebSocket connection with auto-reconnect
    - Task claiming and execution
    - Heartbeat ping/pong
    - Status reporting
    """

    def __init__(self, config: WorkerConfig):
        self.config = config
        self.ws: websockets.WebSocketClientProtocol | None = None
        self.current_task: dict | None = None
        self.reconnect_attempts = 0
        self.running = False
        self.last_ping = 0
        self._task_handlers: dict[str, Callable] = {}

        # Register default handlers
        self._register_default_handlers()

    def _register_default_handlers(self):
        """Register default message handlers."""
        self._task_handlers["agent.register_ack"] = self._on_register_ack
        self._task_handlers["task.assigned"] = self._on_task_assigned
        self._task_handlers["ping"] = self._on_ping
        self._task_handlers["task.pause"] = self._on_task_pause
        self._task_handlers["task.resume"] = self._on_task_resume
        self._task_handlers["generate_envelope"] = self._on_generate_envelope

    async def start(self):
        """Start the worker and connect to orchestrator."""
        self.running = True
        logger.info(f"Starting worker {self.config.agent_id}")

        while self.running:
            try:
                await self._connect()
                await self._run()
            except ConnectionClosed as e:
                logger.warning(f"Connection closed: {e}")
                await self._reconnect()
            except Exception as e:
                logger.error(f"Worker error: {e}")
                await self._reconnect()

    async def stop(self):
        """Stop the worker gracefully."""
        logger.info("Stopping worker...")
        self.running = False
        if self.ws:
            await self.ws.close()

    async def _connect(self):
        """Connect to WebSocket server."""
        logger.info(f"Connecting to {self.config.orchestrator_url}")

        try:
            self.ws = await websockets.connect(
                self.config.orchestrator_url,
                additional_headers={
                    "X-Agent-ID": self.config.agent_id,
                }
            )
            self.reconnect_attempts = 0
            logger.info("Connected to orchestrator")

            # Send registration
            await self._send({
                "v": "1",
                "type": "agent.register",
                "id": self._generate_id(),
                "payload": {
                    "agent_id": self.config.agent_id,
                    "name": self.config.name,
                    "tier": self.config.tier,
                    "capabilities": self.config.capabilities,
                    "version": "1.0.0",
                }
            })

        except InvalidStatusCode as e:
            logger.error(f"Connection refused: {e}")
            raise
        except Exception as e:
            logger.error(f"Connection error: {e}")
            raise

    async def _run(self):
        """Main message loop."""
        logger.info("Entering main message loop")

        async for message in self.ws:
            try:
                msg = json.loads(message)
                await self._handle_message(msg)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON received: {e}")
            except Exception as e:
                logger.error(f"Error handling message: {e}")

    async def _handle_message(self, msg: dict):
        """Dispatch message to appropriate handler."""
        msg_type = msg.get("type")
        handler = self._task_handlers.get(msg_type)

        if handler:
            logger.debug(f"Handling {msg_type}")
            await handler(msg.get("payload", {}), msg)
        else:
            logger.warning(f"Unknown message type: {msg_type}")

    async def _on_register_ack(self, payload: dict, raw_msg: dict):
        """Handle registration acknowledgment."""
        logger.info(f"Registration acknowledged: {payload}")

        # Start claiming tasks
        asyncio.create_task(self._claim_tasks_loop())

    async def _on_task_assigned(self, payload: dict, raw_msg: dict):
        """Handle task assignment."""
        task_id = payload.get("task_id")
        task_type = payload.get("type", "unknown")

        logger.info(f"Task assigned: {task_id} ({task_type})")

        if self.current_task:
            logger.warning("Already have a task, rejecting new one")
            await self._send({
                "v": "1",
                "type": "task.failed",
                "id": self._generate_id(),
                "task_id": task_id,
                "payload": {
                    "error": "Worker busy",
                    "error_type": "worker_busy",
                    "can_retry": True,
                }
            })
            return

        self.current_task = payload

        # Acknowledge start
        await self._send({
            "v": "1",
            "type": "task.started",
            "id": self._generate_id(),
            "task_id": task_id,
            "payload": {
                "task_id": task_id,
                "agent_id": self.config.agent_id,
                "started_at": datetime.utcnow().isoformat(),
            }
        })

        # Execute task
        try:
            result = await self._execute_task(payload)

            # Report completion
            await self._send({
                "v": "1",
                "type": "task.completed",
                "id": self._generate_id(),
                "task_id": task_id,
                "payload": {
                    "task_id": task_id,
                    "agent_id": self.config.agent_id,
                    "result": result,
                    "completed_at": datetime.utcnow().isoformat(),
                }
            })

        except Exception as e:
            logger.error(f"Task execution failed: {e}")
            await self._send({
                "v": "1",
                "type": "task.failed",
                "id": self._generate_id(),
                "task_id": task_id,
                "payload": {
                    "error": str(e),
                    "error_type": "execution_error",
                    "can_retry": True,
                }
            })

        finally:
            self.current_task = None
            # Start claiming again
            asyncio.create_task(self._claim_tasks_loop())

    async def _execute_task(self, payload: dict) -> dict:
        """Execute the assigned task. Override this for custom behavior."""
        task_type = payload.get("type", "unknown")
        task_data = payload.get("payload", {})

        logger.info(f"Executing task type: {task_type}")

        # Default implementation - just return success
        # Subclasses should override this
        return {
            "status": "completed",
            "output": f"Task {task_type} executed by {self.config.agent_id}",
            "task_data": task_data,
        }

    async def _on_ping(self, payload: dict, raw_msg: dict):
        """Respond to heartbeat."""
        self.last_ping = time.time()

        await self._send({
            "v": "1",
            "type": "pong",
            "id": self._generate_id(),
            "payload": {
                "agent_id": self.config.agent_id,
                "status": "busy" if self.current_task else "idle",
                "current_task": self.current_task.get("task_id") if self.current_task else None,
                "context_pct": 0.0,  # TODO: Get actual context usage
                "client_time": datetime.utcnow().isoformat(),
                "server_time": payload.get("server_time"),
            }
        })

    async def _on_task_pause(self, payload: dict, raw_msg: dict):
        """Handle task pause request."""
        logger.info("Task pause requested")
        # Implementation depends on task type

    async def _on_task_resume(self, payload: dict, raw_msg: dict):
        """Handle task resume request."""
        logger.info("Task resume requested")
        # Implementation depends on task type

    async def _on_generate_envelope(self, payload: dict, raw_msg: dict):
        """Handle context envelope generation request."""
        logger.info("Context envelope generation requested")
        # TODO: Implement context envelope generation

    async def _claim_tasks_loop(self):
        """Periodically try to claim tasks."""
        if self.current_task:
            return

        # Wait a bit before claiming
        await asyncio.sleep(2)

        while self.running and not self.current_task:
            try:
                # This would call the HTTP API to claim tasks
                # For now, we rely on orchestrator pushing tasks
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Error in claim loop: {e}")
                await asyncio.sleep(10)

    async def _send(self, msg: dict):
        """Send message to orchestrator."""
        if self.ws:
            await self.ws.send(json.dumps(msg))

    def _generate_id(self) -> str:
        """Generate unique message ID."""
        import uuid
        return str(uuid.uuid4())[:8]

    async def _reconnect(self):
        """Reconnect with exponential backoff."""
        self.reconnect_attempts += 1
        delay = min(60, 2 ** self.reconnect_attempts)

        logger.info(f"Reconnecting in {delay}s (attempt {self.reconnect_attempts})")
        await asyncio.sleep(delay)


class DispatchWorker(ForgeWorker):
    """
    Worker that handles dispatch tasks by forwarding to tmux.
    
    This bridges v3 tasks to actual tmux agents until full v3 migration.
    """

    def __init__(self, config: WorkerConfig):
        super().__init__(config)
        # Import here to avoid circular dependency
        from ..fleet.dispatch_client import DispatchClient
        self.dispatch_client = DispatchClient()

    async def _execute_task(self, payload: dict) -> dict:
        """Execute dispatch task by forwarding to tmux."""
        task_data = payload.get("payload", {})

        # Extract dispatch info
        target = task_data.get("target", f"forge:{self.config.name}")
        message = task_data.get("message", task_data.get("result", ""))

        logger.info(f"Dispatching to {target}: {message[:50]}...")

        # Use working v2 dispatch client
        result = await self.dispatch_client.send(target, message)

        return {
            "status": "completed" if result.success else "failed",
            "delivery_time_ms": result.delivery_time_ms,
            "attempts": result.attempts,
            "error": result.error,
        }


async def main():
    """Example main function."""
    import argparse

    parser = argparse.ArgumentParser(description="FORGE v3 Worker")
    parser.add_argument("--agent-id", default="forge:worker-1", help="Agent ID")
    parser.add_argument("--tier", default="t2", help="Agent tier")
    parser.add_argument("--capabilities", default="code,test", help="Capabilities (comma-separated)")
    parser.add_argument("--dispatch-mode", action="store_true", help="Run in dispatch mode (forwards to tmux)")

    args = parser.parse_args()

    config = WorkerConfig(
        agent_id=args.agent_id,
        capabilities=args.capabilities.split(","),
        tier=args.tier,
    )

    if args.dispatch_mode:
        worker = DispatchWorker(config)
    else:
        worker = ForgeWorker(config)

    try:
        await worker.start()
    except KeyboardInterrupt:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
