"""
V3 Dispatch Bridge - Makes v3 dispatch actually work.

Bridges the gap between v3 task tracking and working v2 tmux dispatch.
"""

from __future__ import annotations

import asyncio

# Simple logging
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass

import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class BridgeResult:
    """Result of v3 bridged dispatch."""
    task_id: str | None
    dispatch_success: bool
    tmux_target: str
    message: str
    delivery_time_ms: float
    error: str | None = None
    bridge_error: str | None = None


class TmuxDispatchClient:
    """
    Standalone tmux dispatch client (v2 compatible).
    Avoids importing the full forge_harness which has dependency issues.
    """

    def __init__(self):
        self.forge_root = os.environ.get("FORGE_ROOT", "/home/bogdan/FORGE")

    def send_sync(
        self,
        target: str,
        message: str,
        timeout: float = 30.0,
    ) -> tuple[bool, float, str | None]:
        """
        Send message to tmux agent synchronously.
        
        Returns: (success, delivery_time_ms, error)
        """
        start_time = time.time()

        # Parse target (e.g., "forge:kimi" -> "kimi")
        if ":" in target:
            window_name = target.split(":")[-1]
        else:
            window_name = target

        tmux_target = f"forge:{window_name}"

        logger.info(f"Dispatching to tmux target: {tmux_target}")

        try:
            # Check if tmux window exists by trying to send a simple command
            check_result = subprocess.run(
                ["tmux", "list-windows", "-t", "forge", "-F", "#{window_name}"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )

            if check_result.returncode != 0:
                elapsed = (time.time() - start_time) * 1000
                return False, elapsed, f"Cannot access tmux session: {check_result.stderr}"

            # Check if window exists in list
            window_list = check_result.stdout.strip().split("\n")
            window_base = window_name.rstrip("*")  # Remove active marker
            if window_base not in [w.rstrip("*") for w in window_list]:
                elapsed = (time.time() - start_time) * 1000
                return False, elapsed, f"Tmux window {tmux_target} (looking for {window_base}) not found in: {window_list}"

            # Clear any existing input and send message
            # C-c: Cancel current operation
            # C-u: Clear line
            subprocess.run(
                ["tmux", "send-keys", "-t", tmux_target, "C-c"],
                capture_output=True,
                timeout=2.0,
            )
            time.sleep(0.1)

            subprocess.run(
                ["tmux", "send-keys", "-t", tmux_target, "C-u"],
                capture_output=True,
                timeout=2.0,
            )
            time.sleep(0.1)

            # Send the actual message
            subprocess.run(
                ["tmux", "send-keys", "-t", tmux_target, message],
                capture_output=True,
                timeout=2.0,
            )

            # Press Enter
            subprocess.run(
                ["tmux", "send-keys", "-t", tmux_target, "Enter"],
                capture_output=True,
                timeout=2.0,
            )

            elapsed = (time.time() - start_time) * 1000
            logger.info(f"Dispatched to {tmux_target} in {elapsed:.0f}ms")
            return True, elapsed, None

        except subprocess.TimeoutExpired:
            elapsed = (time.time() - start_time) * 1000
            return False, elapsed, "Tmux command timed out"
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return False, elapsed, str(e)


class DispatchBridge:
    """Bridge v3 task creation with working v2 tmux dispatch."""

    def __init__(self, api_url: str = "http://localhost:8081"):
        self.api_url = api_url
        self.tmux_client = TmuxDispatchClient()
        self.http_client = httpx.Client(base_url=api_url, timeout=10.0)

    async def dispatch(
        self,
        target: str,
        message: str,
        domain: str = "default",
        project: str = "default",
        priority: int = 1,
        verify: bool = False,
    ) -> BridgeResult:
        """
        Dispatch message to agent via bridge.
        
        1. Creates task in v3 database
        2. Dispatches to tmux agent
        3. Updates task with result
        """
        start_time = asyncio.get_event_loop().time()

        # Step 1: Create task in v3 database
        task_id = await self._create_task(
            domain=domain,
            project=project,
            target=target,
            message=message,
            priority=priority,
        )

        if not task_id:
            return BridgeResult(
                task_id=None,
                dispatch_success=False,
                tmux_target=target,
                message=message,
                delivery_time_ms=0,
                error="Failed to create v3 task",
            )

        logger.info(f"Created v3 task {task_id} for {target}")

        # Step 2: Actually dispatch to tmux agent (WORKING v2 client)
        try:
            # Run sync tmux dispatch in thread pool
            loop = asyncio.get_event_loop()
            success, tmux_time, error = await loop.run_in_executor(
                None,
                lambda: self.tmux_client.send_sync(target, message)
            )
        except Exception as e:
            logger.error(f"Dispatch failed: {e}")
            await self._update_task(task_id, "failed", str(e))
            elapsed = (asyncio.get_event_loop().time() - start_time) * 1000
            return BridgeResult(
                task_id=task_id,
                dispatch_success=False,
                tmux_target=target,
                message=message,
                delivery_time_ms=elapsed,
                error=str(e),
            )

        # Step 3: Update v3 task with result
        elapsed = (asyncio.get_event_loop().time() - start_time) * 1000

        if success:
            await self._update_task(
                task_id,
                "dispatched",
                f"Delivered via tmux in {tmux_time:.0f}ms",
            )
            logger.info(f"Dispatched to {target} in {elapsed:.0f}ms (v3: {task_id})")
        else:
            await self._update_task(
                task_id,
                "failed",
                error or "Unknown error",
            )
            logger.warning(f"Dispatch to {target} failed: {error}")

        return BridgeResult(
            task_id=task_id,
            dispatch_success=success,
            tmux_target=target,
            message=message,
            delivery_time_ms=elapsed,
            error=error,
        )

    async def _create_task(
        self,
        domain: str,
        project: str,
        target: str,
        message: str,
        priority: int,
    ) -> str | None:
        """Create task in v3 database."""
        payload = {
            "id": "",
            "domain": domain,
            "project": project,
            "type": "dispatch",
            "priority": priority,
            "status": "creating",
            "result": f"Dispatch to {target}: {message}",
            "error": "",
            "assigned_to": target.replace("forge:", ""),
        }

        try:
            response = self.http_client.post("/api/tasks", json=payload)
            if response.status_code == 200:
                data = response.json()
                return data.get("id")
            else:
                logger.error(f"Failed to create v3 task: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error creating v3 task: {e}")
            return None

    async def _update_task(
        self,
        task_id: str,
        status: str,
        result: str,
    ) -> bool:
        """Update task status in v3 database."""
        # Note: v3 API doesn't have an update endpoint yet
        # This would need to be added to main.go
        # For now, we just log it
        logger.info(f"Task {task_id} status: {status} - {result}")
        return True

    async def dispatch_batch(
        self,
        dispatches: list[tuple[str, str]],
        **kwargs,
    ) -> list[BridgeResult]:
        """Dispatch multiple messages in parallel."""
        tasks = [
            self.dispatch(target, message, **kwargs)
            for target, message in dispatches
        ]
        return await asyncio.gather(*tasks)


# Singleton instance
_bridge: DispatchBridge | None = None


def get_bridge() -> DispatchBridge:
    """Get global bridge instance."""
    global _bridge
    if _bridge is None:
        _bridge = DispatchBridge()
    return _bridge


async def dispatch(target: str, message: str, **kwargs) -> BridgeResult:
    """Convenience function for dispatch."""
    bridge = get_bridge()
    return await bridge.dispatch(target, message, **kwargs)


# For testing
if __name__ == "__main__":
    import sys

    async def main():
        target = sys.argv[1] if len(sys.argv) > 1 else "forge:kimi"
        message = sys.argv[2] if len(sys.argv) > 2 else "Test message from bridge"

        print(f"Dispatching to {target}: {message}")
        result = await dispatch(target, message)

        print("\nResult:")
        print(f"  Success: {result.dispatch_success}")
        print(f"  V3 Task ID: {result.task_id}")
        print(f"  Delivery time: {result.delivery_time_ms:.0f}ms")
        if result.error:
            print(f"  Error: {result.error}")

    asyncio.run(main())
