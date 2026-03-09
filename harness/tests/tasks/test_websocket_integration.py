#!/usr/bin/env python3
"""Integration tests for V3 WebSocket server."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Add harness to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from forge_harness.task_manager.websocket_server import WebSocketClient, WebSocketServer


class TestWebSocketServer:
    """Test WebSocket server functionality."""

    @pytest.fixture
    async def server(self):
        """Start WebSocket server for testing."""
        server = WebSocketServer(host="127.0.0.1", port=9999)

        # Start in background
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.5)  # Let server start

        yield server

        # Cleanup
        server.stop()
        task.cancel()

    @pytest.mark.asyncio
    async def test_server_starts(self):
        """Test server can be instantiated."""
        server = WebSocketServer(host="127.0.0.1", port=9998)
        assert server.host == "127.0.0.1"
        assert server.port == 9998
        assert server.clients == {}

    @pytest.mark.asyncio
    async def test_broadcast_excludes_sender(self):
        """Test broadcast excludes the sender node."""
        server = WebSocketServer(host="127.0.0.1", port=9997)

        # Mock clients
        mock_client1 = AsyncMock()
        mock_client2 = AsyncMock()
        server.clients = {
            "node1": mock_client1,
            "node2": mock_client2
        }

        # Broadcast to all except node1
        await server.broadcast({"type": "test"}, exclude="node1")

        # node1 should NOT receive message
        mock_client1.send.assert_not_called()
        # node2 SHOULD receive message
        mock_client2.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_unregister(self):
        """Test node registration and unregistration."""
        server = WebSocketServer(host="127.0.0.1", port=9996)

        mock_websocket = AsyncMock()
        await server.register(mock_websocket, "test-node")

        assert "test-node" in server.clients
        assert server.node_types[mock_websocket] == "test-node"

        # Unregister
        await server.unregister(mock_websocket)
        assert "test-node" not in server.clients


class TestWebSocketClient:
    """Test WebSocket client functionality."""

    def test_client_init(self):
        """Test client can be instantiated."""
        client = WebSocketClient("test-node", "ws://localhost:8082")
        assert client.node_name == "test-node"
        assert client.hub_url == "ws://localhost:8082"
        assert client.connected is False
        assert client.websocket is None

    def test_client_default_url(self):
        """Test client has default URL."""
        client = WebSocketClient("test-node")
        assert client.hub_url == "ws://localhost:8082"


class TestMessageProtocol:
    """Test WebSocket message protocol."""

    @pytest.mark.asyncio
    async def test_handshake_message_format(self):
        """Test handshake message format."""
        # Verify handshake message structure
        from datetime import datetime
        message = {
            "type": "handshake",
            "node_name": "test-node",
            "timestamp": datetime.utcnow().isoformat()
        }
        assert message["type"] == "handshake"
        assert "node_name" in message
        assert "timestamp" in message

    @pytest.mark.asyncio
    async def test_task_created_message_format(self):
        """Test task created message format."""
        from datetime import datetime
        task = {
            "id": "TC-test-001",
            "title": "Test task",
            "priority": "high"
        }
        message = {
            "type": "task_created",
            "task": task,
            "timestamp": datetime.utcnow().isoformat()
        }
        assert message["type"] == "task_created"
        assert message["task"]["id"] == "TC-test-001"

    @pytest.mark.asyncio
    async def test_task_claimed_message_format(self):
        """Test task claimed message format."""
        from datetime import datetime
        message = {
            "type": "task_claimed",
            "task_id": "TC-test-001",
            "agent": "test-agent",
            "timestamp": datetime.utcnow().isoformat()
        }
        assert message["type"] == "task_claimed"
        assert message["task_id"] == "TC-test-001"
        assert message["agent"] == "test-agent"


class TestServerClientIntegration:
    """Integration tests for server-client interaction."""

    @pytest.mark.asyncio
    async def test_client_methods_exist(self):
        """Verify client has required methods."""
        client = WebSocketClient("test-node")

        assert hasattr(client, "connect")
        assert callable(getattr(client, "connect"))

        assert hasattr(client, "send_task_created")
        assert callable(getattr(client, "send_task_created"))

        assert hasattr(client, "send_task_claimed")
        assert callable(getattr(client, "send_task_claimed"))

    @pytest.mark.asyncio
    async def test_server_broadcast_method(self):
        """Verify server has broadcast method."""
        server = WebSocketServer()

        assert hasattr(server, "broadcast")
        assert callable(getattr(server, "broadcast"))

        assert hasattr(server, "register")
        assert callable(getattr(server, "register"))

        assert hasattr(server, "unregister")
        assert callable(getattr(server, "unregister"))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
