"""V3 WebSocket Server - Real-time multinode communication.

Replaces file-based XNode with WebSocket connections.
All nodes connect to central WebSocket hub for real-time task sync.
"""

import asyncio
import json
import logging
from datetime import datetime

import websockets
from websockets.server import WebSocketServerProtocol

from .database import get_task_db

logger = logging.getLogger(__name__)


class WebSocketServer:
    """WebSocket server for real-time V3 multinode sync."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8082):
        self.host = host
        self.port = port
        self.clients: dict[str, WebSocketServerProtocol] = {}
        self.node_types: dict[str, str] = {}  # websocket -> node_name
        self.db = get_task_db()

    async def register(self, websocket: WebSocketServerProtocol, node_name: str):
        """Register a new node connection."""
        self.clients[node_name] = websocket
        self.node_types[websocket] = node_name
        logger.info(f"Node connected: {node_name}")

        # Notify other nodes
        await self.broadcast(
            {
                "type": "node_connected",
                "node": node_name,
                "timestamp": datetime.utcnow().isoformat(),
            },
            exclude=node_name,
        )

    async def unregister(self, websocket: WebSocketServerProtocol):
        """Unregister a node connection."""
        node_name = self.node_types.get(websocket)
        if node_name:
            del self.clients[node_name]
            del self.node_types[websocket]
            logger.info(f"Node disconnected: {node_name}")

            await self.broadcast(
                {
                    "type": "node_disconnected",
                    "node": node_name,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )

    async def broadcast(self, message: dict, exclude: str | None = None):
        """Broadcast message to all connected nodes."""
        message_json = json.dumps(message)

        # Create a snapshot to avoid "dictionary changed size during iteration"
        clients_snapshot = list(self.clients.items())
        for node_name, client in clients_snapshot:
            if node_name != exclude:
                try:
                    await client.send(message_json)
                except Exception as e:
                    logger.error(f"Failed to send to {node_name}: {e}")

    async def handle_message(self, websocket: WebSocketServerProtocol, message: str):
        """Handle incoming message from a node."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            node_name = self.node_types.get(websocket, "unknown")

            if msg_type == "handshake":
                # Initial connection handshake
                await self.register(websocket, data.get("node_name", "unknown"))
                await websocket.send(
                    json.dumps(
                        {
                            "type": "handshake_ack",
                            "status": "connected",
                            "connected_nodes": list(self.clients.keys()),
                        }
                    )
                )

            elif msg_type == "task_created":
                # New task created on a node
                task = data.get("task", {})
                logger.info(f"Task {task.get('id')} created on {node_name}")

                # Broadcast to all other nodes
                await self.broadcast(
                    {"type": "task_available", "task": task, "source_node": node_name},
                    exclude=node_name,
                )

            elif msg_type == "task_claimed":
                # Task claimed by an agent
                task_id = data.get("task_id")
                agent = data.get("agent")
                logger.info(f"Task {task_id} claimed by {agent} on {node_name}")

                await self.broadcast(
                    {"type": "task_claimed", "task_id": task_id, "agent": agent, "node": node_name}
                )

            elif msg_type == "task_completed":
                # Task completed
                task_id = data.get("task_id")
                result = data.get("result", {})
                logger.info(f"Task {task_id} completed on {node_name}")

                await self.broadcast(
                    {
                        "type": "task_completed",
                        "task_id": task_id,
                        "result": result,
                        "node": node_name,
                    }
                )

            elif msg_type == "ping":
                # Keepalive ping
                await websocket.send(
                    json.dumps({"type": "pong", "timestamp": datetime.utcnow().isoformat()})
                )

            else:
                logger.warning(f"Unknown message type: {msg_type}")

        except json.JSONDecodeError:
            logger.error(f"Invalid JSON received: {message[:100]}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def handle_go_message(
        self, websocket: WebSocketServerProtocol, message: str, agent_id: str
    ):
        """Handle Go protocol messages."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "task.started":
                logger.info(f"Task {data.get('task_id')} started by {agent_id}")
                # Update task status in DB

            elif msg_type == "task.completed":
                logger.info(f"Task {data.get('task_id')} completed by {agent_id}")
                # Mark task as completed

            elif msg_type == "task.failed":
                logger.error(f"Task {data.get('task_id')} failed by {agent_id}")
                # Mark task as failed

            elif msg_type == "task.progress":
                logger.info(
                    f"Task {data.get('task_id')} progress by {agent_id}: {data.get('payload', {})}"
                )

            elif msg_type == "ping":
                await websocket.send(
                    json.dumps(
                        {
                            "v": "1",
                            "type": "pong",
                            "id": data.get("id", ""),
                            "ts": datetime.utcnow().isoformat(),
                        }
                    )
                )

            else:
                logger.warning(f"Unknown Go message type: {msg_type}")

        except json.JSONDecodeError:
            logger.error(f"Invalid JSON received: {message[:100]}")
        except Exception as e:
            logger.error(f"Error handling Go message: {e}")

    async def handler(self, websocket):
        """WebSocket connection handler (websockets 16.0+ compatible)."""
        try:
            # Wait for handshake message
            message = await websocket.recv()
            data = json.loads(message)
            msg_type = data.get("type")

            # Support both Python protocol (handshake) and Go protocol (agent.register)
            if msg_type == "handshake":
                node_name = data.get("node_name", f"node_{id(websocket)}")
                await self.register(websocket, node_name)

                # Keep connection open and handle messages
                async for message in websocket:
                    await self.handle_message(websocket, message)

            elif msg_type == "agent.register":
                # Go protocol compatibility
                payload = data.get("payload", {})
                agent_id = (
                    payload.get("agent_id") or data.get("agent_id") or f"agent_{id(websocket)}"
                )
                await self.register(websocket, agent_id)

                # Send Go-compatible ack
                await websocket.send(
                    json.dumps(
                        {
                            "v": "1",
                            "type": "agent.register_ack",
                            "id": data.get("id", ""),
                            "payload": {
                                "agent_id": agent_id,
                                "status": "registered",
                                "connected_nodes": list(self.clients.keys()),
                            },
                            "ts": datetime.utcnow().isoformat(),
                        }
                    )
                )

                # Keep connection open and handle messages
                async for message in websocket:
                    await self.handle_go_message(websocket, message, agent_id)
            else:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "error",
                            "message": "First message must be handshake or agent.register",
                        }
                    )
                )

        except websockets.exceptions.ConnectionClosed:
            logger.info("Connection closed")
        except Exception as e:
            logger.error(f"Handler error: {e}")
        finally:
            await self.unregister(websocket)

    async def start(self):
        """Start WebSocket server."""
        logger.info(f"Starting V3 WebSocket server on {self.host}:{self.port}")

        async with websockets.serve(self.handler, self.host, self.port):
            logger.info(f"WebSocket server running on ws://{self.host}:{self.port}")
            await asyncio.Future()  # Run forever

    def run(self):
        """Run server (blocking)."""
        asyncio.run(self.start())


# Client-side WebSocket handler for nodes
class WebSocketClient:
    """WebSocket client for V3 nodes to connect to hub."""

    def __init__(self, node_name: str, hub_url: str = "ws://localhost:8082"):
        self.node_name = node_name
        self.hub_url = hub_url
        self.websocket = None
        self.connected = False

    async def connect(self):
        """Connect to WebSocket hub."""
        try:
            self.websocket = await websockets.connect(self.hub_url)

            # Send handshake
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "handshake",
                        "node_name": self.node_name,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                )
            )

            # Wait for acknowledgment
            response = await self.websocket.recv()
            data = json.loads(response)

            if data.get("type") == "handshake_ack":
                self.connected = True
                logger.info(f"Connected to V3 hub as {self.node_name}")
                logger.info(f"Connected nodes: {data.get('connected_nodes', [])}")
                return True
            else:
                logger.error(f"Handshake failed: {data}")
                return False

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    async def send_task_created(self, task: dict):
        """Notify hub of new task."""
        if self.connected:
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "task_created",
                        "task": task,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                )
            )

    async def send_task_claimed(self, task_id: str, agent: str):
        """Notify hub of task claim."""
        if self.connected:
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "task_claimed",
                        "task_id": task_id,
                        "agent": agent,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                )
            )

    async def send_task_completed(self, task_id: str, result: dict):
        """Notify hub of task completion."""
        if self.connected:
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "task_completed",
                        "task_id": task_id,
                        "result": result,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                )
            )

    async def listen(self):
        """Listen for messages from hub."""
        if not self.connected:
            logger.error("Not connected")
            return

        try:
            async for message in self.websocket:
                data = json.loads(message)
                await self.handle_hub_message(data)
        except websockets.exceptions.ConnectionClosed:
            logger.info("Connection to hub closed")
            self.connected = False

    async def handle_hub_message(self, data: dict):
        """Handle message from hub."""
        msg_type = data.get("type")

        if msg_type == "task_available":
            # New task available from another node
            task = data.get("task", {})
            source = data.get("source_node")
            logger.info(f"New task {task.get('id')} available from {source}")

            # Add to local database
            # db = get_task_db()
            # Task will be synced via normal flow

        elif msg_type == "task_claimed":
            # Task claimed elsewhere
            logger.info(
                f"Task {data.get('task_id')} claimed by {data.get('agent')} on {data.get('node')}"
            )

        elif msg_type == "task_completed":
            # Task completed elsewhere
            logger.info(f"Task {data.get('task_id')} completed on {data.get('node')}")

        elif msg_type == "node_connected":
            logger.info(f"Node {data.get('node')} joined the cluster")

        elif msg_type == "node_disconnected":
            logger.info(f"Node {data.get('node')} left the cluster")

    async def close(self):
        """Close connection."""
        if self.websocket:
            await self.websocket.close()
            self.connected = False


# Convenience functions
def start_websocket_server(host: str = "0.0.0.0", port: int = 8082):
    """Start WebSocket server (blocking)."""
    server = WebSocketServer(host, port)
    server.run()


async def connect_node(node_name: str, hub_url: str = "ws://localhost:8082"):
    """Connect a node to WebSocket hub."""
    client = WebSocketClient(node_name, hub_url)
    success = await client.connect()
    if success:
        # Start listening in background
        asyncio.create_task(client.listen())
    return client


if __name__ == "__main__":
    # Start server when run directly
    logging.basicConfig(level=logging.INFO)
    start_websocket_server()
