"""
V3 Bridge - Connects v3 task tracking with working v2 dispatch.

This module bridges the gap between v3's SQLite task tracking and
the working v2 tmux dispatch system.
"""

from .dispatch_bridge import BridgeResult, DispatchBridge, dispatch, get_bridge

__all__ = ["DispatchBridge", "BridgeResult", "dispatch", "get_bridge"]
