"""
Health monitoring for Continuous Runner.
"""

import time
from typing import Any

from pydantic import BaseModel


class HealthStatus(BaseModel):
    """Health status information."""
    status: str
    timestamp: float
    uptime_seconds: float
    components: dict[str, Any]


class HealthMonitor:
    """Monitors health of the continuous runner."""

    def __init__(self):
        self.start_time = time.time()
        self.last_check = time.time()
        self._components_status = {
            "ralph_loop": "unknown",
            "webhook_server": "starting",
            "railway": "unknown"
        }

    def update_component(self, component: str, status: str):
        """Update status of a component."""
        self._components_status[component] = status
        self.last_check = time.time()

    def get_status(self) -> HealthStatus:
        """Get current health status."""
        return HealthStatus(
            status="healthy" if all(s != "error" for s in self._components_status.values()) else "degraded",
            timestamp=time.time(),
            uptime_seconds=time.time() - self.start_time,
            components=self._components_status
        )


monitor = HealthMonitor()
