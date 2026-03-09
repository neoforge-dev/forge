"""
FORGE v3 WebSocket Worker

Connects to the v3 daemon via WebSocket and processes tasks.
"""

from .websocket_client import ForgeWorker, WorkerConfig

__all__ = ["ForgeWorker", "WorkerConfig"]
