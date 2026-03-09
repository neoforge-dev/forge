"""
FORGE Dashboard - Local TUI for Harness Visibility
===================================================

Provides a Rich-based terminal UI for monitoring:
- Active pipelines and their status
- Pending approval requests
- Active agents (via Command Center API)
- Ralph loop progress
- Recent errors and warnings

Usage:
    from forge_harness.dashboard import ForgeDashboard

    # Create dashboard (local file-based)
    dashboard = ForgeDashboard(
        approval_storage_dir=Path(".forge/approvals"),
        checkpoint_dir=Path(".forge/orchestration_checkpoints"),
    )

    # Create dashboard with API support
    dashboard = ForgeDashboard(
        api_url="http://localhost:8080",
    )

    # Run interactive dashboard
    await dashboard.run()

CLI:
    forge-harness dashboard
    forge-harness dashboard --refresh 2.0
    forge-harness dashboard --api-url http://localhost:8080
"""

from __future__ import annotations

import asyncio
import json
import os
import select
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Unix terminal handling for keyboard input
try:
    import termios
    import tty

    HAS_TERMIOS = True
except ImportError:
    HAS_TERMIOS = False

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import dashboard_detail_views
from .dashboard_core import (
    AgentState,
    MVPStatusSummary,
    RalphStatus,
)
from .logging_config import get_logger
from .metrics_common import (
    ApprovalSummary,
    PipelineStatus,
    filter_approvals_by_time,
    format_age,
    format_duration,
    sort_approvals_by_priority,
)

logger = get_logger(__name__)

COMPATIBILITY_MODE = True


def deprecation_notice() -> str:
    """Return deprecation warning for legacy TUI dashboard.

    The legacy ForgeDashboard TUI is being phased out in favor of the
    Command Center PWA dashboard, which provides:
    - Real-time SSE updates
    - Mobile-responsive design
    - Better multi-agent visibility
    - Approval workflow integration

    Returns:
        Deprecation warning string with migration guidance.
    """
    return (
        "⚠️  DEPRECATION NOTICE: The legacy TUI dashboard is deprecated.\n"
        "   Migrate to the Command Center PWA for real-time updates:\n"
        "   → forge-harness dashboard --api-url http://localhost:8080\n"
        "   → Or open https://command-center.forge.app in your browser\n"
        "   Set COMPATIBILITY_MODE=False in dashboard.py to disable this notice."
    )


class KeyboardHandler:
    """
    Non-blocking keyboard input handler for TUI.

    Supports vim-style navigation and approval shortcuts:
    - j/k or arrow keys: Navigate up/down
    - a: Approve selected item
    - r: Reject selected item
    - q: Quit
    - ?: Show help
    """

    def __init__(self) -> None:
        self._running = False
        self._key_queue: list[str] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._old_settings: Any = None

    def start(self) -> None:
        """Start the keyboard listener thread."""
        if not HAS_TERMIOS:
            return  # Keyboard handling not available on this platform

        self._running = True
        self._thread = threading.Thread(target=self._read_keys, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the keyboard listener and restore terminal settings."""
        self._running = False
        if self._old_settings is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_settings)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=0.5)

    def get_key(self) -> str | None:
        """Get the next key from the queue, or None if empty."""
        with self._lock:
            if self._key_queue:
                return self._key_queue.pop(0)
            return None

    def _read_keys(self) -> None:
        """Background thread to read keyboard input."""
        if not HAS_TERMIOS:
            return

        try:
            fd = sys.stdin.fileno()
            self._old_settings = termios.tcgetattr(fd)
            tty.setcbreak(fd)  # Set terminal to cbreak mode (no line buffering)

            while self._running:
                # Check if input is available (non-blocking)
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    char = sys.stdin.read(1)
                    if char:
                        # Handle escape sequences (arrow keys)
                        if char == "\x1b":
                            # Read potential escape sequence
                            if select.select([sys.stdin], [], [], 0.05)[0]:
                                char2 = sys.stdin.read(1)
                                if char2 == "[":
                                    if select.select([sys.stdin], [], [], 0.05)[0]:
                                        char3 = sys.stdin.read(1)
                                        if char3 == "A":
                                            char = "UP"
                                        elif char3 == "B":
                                            char = "DOWN"
                                        else:
                                            char = "ESC"
                                else:
                                    char = "ESC"
                            else:
                                char = "ESC"

                        with self._lock:
                            self._key_queue.append(char)
        except Exception as e:
            logger.debug(f"Keyboard handler error: {e}")
        finally:
            if self._old_settings is not None:
                try:
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_settings)
                except Exception:
                    pass


class ForgeDashboard:
    """
    Local TUI dashboard for FORGE harness visibility.

    Provides real-time monitoring of:
    - Pipeline execution status
    - Approval queue
    - Ralph loop progress
    - Recent errors
    """

    def __init__(
        self,
        approval_storage_dir: Path | None = None,
        checkpoint_dir: Path | None = None,
        ralph_checkpoint_dir: Path | None = None,
        forge_root: Path | None = None,
        api_url: str | None = None,
        api_token: str | None = None,
        use_synchronizer: bool = False,
    ):
        """
        Initialize dashboard.

        Args:
            approval_storage_dir: Directory containing approval request files
            checkpoint_dir: Directory containing pipeline checkpoints
            ralph_checkpoint_dir: Directory containing Ralph loop checkpoints
            forge_root: FORGE project root directory
            api_url: Command Center API URL (enables API-based data loading)
            api_token: API authentication token (or uses FORGE_WEBHOOK_TOKEN env)
            use_synchronizer: Use StateSynchronizer for unified state access
        """
        self.console = Console()

        # Resolve paths
        if forge_root is None:
            forge_root = Path.cwd()
        self.forge_root = Path(forge_root)

        self.approval_storage_dir = (
            Path(approval_storage_dir)
            if approval_storage_dir
            else self.forge_root / ".forge/approvals"
        )
        self.checkpoint_dir = (
            Path(checkpoint_dir)
            if checkpoint_dir
            else self.forge_root / ".forge/orchestration_checkpoints"
        )
        self.ralph_checkpoint_dir = (
            Path(ralph_checkpoint_dir)
            if ralph_checkpoint_dir
            else self.forge_root / ".forge/ralph_checkpoints"
        )
        self.mvp_status_dir = self.forge_root / ".forge/mvp_status"

        # API configuration
        self.api_url = api_url
        self.api_token = api_token or os.environ.get("FORGE_WEBHOOK_TOKEN")

        # Error buffer for recent errors
        self._recent_errors: list[tuple[datetime, str]] = []
        self._max_errors = 10

        # Cached API client
        self._api_client: Any = None

        # Keyboard navigation state
        self._selected_approval_index: int = 0
        self._cached_approvals: list[ApprovalSummary] = []
        self._keyboard_handler: KeyboardHandler | None = None
        self._status_message: str | None = None
        self._status_message_time: datetime | None = None

        # Help modal state
        self._show_help_modal: bool = False

        # Time filter state ("all", "1h", "24h")
        self._time_filter: str = "all"

        # Sound alerts state
        self._sound_alerts_enabled: bool = True
        self._last_approval_count: int = 0

        # Detail view state
        self._detail_view_active: bool = False
        self._detail_view_type: str | None = None  # "agent", "approval", "pipeline", "pattern"
        self._detail_view_item: Any = None
        self._cached_pipelines: list[PipelineStatus] = []
        self._cached_patterns: list[dict[str, Any]] = []
        self._selected_pipeline_index: int = 0
        self._selected_pattern_index: int = 0
        self._selected_agent_index: int = 0
        self._cached_agents: list[AgentState] = []

        # State synchronizer for unified state access
        self._use_synchronizer = use_synchronizer
        self._synchronizer: Any | None = None
        if use_synchronizer:
            try:
                from .state_synchronizer import get_synchronizer

                self._synchronizer = get_synchronizer(forge_root=self.forge_root)
                logger.info("Dashboard using StateSynchronizer for state access")
            except ImportError:
                logger.warning("StateSynchronizer not available, using direct file access")
                self._use_synchronizer = False

        # Health check state
        self._last_health_check: Any | None = None
        self._last_health_check_time: datetime | None = None
        self._health_check_interval: float = 30.0  # 30 seconds
        self._health_check_error: str | None = None
        self._health_check_task: Any | None = None

    def _get_state_snapshot(self) -> Any | None:
        """Get unified state snapshot from synchronizer.

        Returns:
            StateSnapshot if synchronizer is available, None otherwise
        """
        if self._synchronizer is not None:
            return self._synchronizer.get_state_snapshot()
        return None

    def _get_api_client(self) -> Any:
        """Get or create API client."""
        if self._api_client is None and self.api_url:
            try:
                from .command_center_client import CommandCenterClient

                self._api_client = CommandCenterClient(
                    base_url=self.api_url,
                    token=self.api_token,
                )

                logger.debug(f"Created API client with token: {'yes' if self.api_token else 'no'}")
            except ImportError:
                logger.warning("CommandCenterClient not available")
        return self._api_client

    def _load_agents_from_api(self) -> list[AgentState]:
        """Load agents from Command Center API, tmux sessions, and fleet summary."""
        agents: list[AgentState] = []
        seen_ids: set[str] = set()

        # First try Command Center API for registered agents
        client = self._get_api_client()
        if client:
            try:
                api_agents = client.sync_list_agents()
                for a in api_agents:
                    agents.append(
                        AgentState(
                            id=a.id,
                            name=a.role,
                            role=a.role,
                            project=a.project,
                            task=a.task,
                            status=a.status.value,
                            progress=a.progress,
                            last_activity=a.last_activity,
                            is_stale=a.is_stale,
                            source="api",
                        )
                    )
                    seen_ids.add(a.id)
            except Exception as e:
                logger.debug(f"Failed to load agents from API: {e}")

        # Also load tmux sessions (unregistered agents)
        try:
            from .session_tracker import get_session_tracker

            tracker = get_session_tracker()
            sessions = tracker.get_all_sessions()
            for s in sessions:
                # Skip if already seen from API
                if s.session_name in seen_ids or s.window_name in seen_ids:
                    continue
                # Map session status to agent status
                status_map = {
                    "active": "active",
                    "idle": "idle",
                    "error": "error",
                    "completed": "idle",
                    "unknown": "idle",
                }
                # Use window name as role when agent_type is unknown
                role = s.agent_type if s.agent_type and s.agent_type != "unknown" else s.window_name
                agents.append(
                    AgentState(
                        id=s.session_name,
                        name=role,
                        role=role,
                        project=f"{s.domain}/{s.project}" if s.domain else None,
                        task=s.current_task,
                        status=status_map.get(s.status, "idle"),
                        progress=0,
                        last_activity=datetime.fromisoformat(s.last_activity)
                        if s.last_activity
                        else None,
                        is_stale=False,
                        source="tmux",
                        session_id=s.session_name,
                        window_name=s.window_name,
                        agent_type=s.agent_type,
                        domain=s.domain,
                    )
                )
        except Exception as e:
            logger.debug(f"Failed to load tmux sessions: {e}")

        # Enrich agents with real-time data from /api/fleet/summary
        # This endpoint is public (no auth required) and provides live
        # context_pct, task_id, status, and staleness for each agent.
        try:
            import httpx

            fleet_url = f"{self.api_url.rstrip('/')}/api/fleet/summary" if self.api_url else "http://localhost:8080/api/fleet/summary"
            fleet_resp = httpx.get(fleet_url, timeout=30.0)
            if fleet_resp.status_code == 200:
                fleet_data = fleet_resp.json()
                fleet_agents: list[dict] = fleet_data.get("fleet_agents", [])

                # Build a lookup keyed by agent name for O(1) enrichment
                fleet_lookup: dict[str, dict] = {
                    fa["agent"]: fa for fa in fleet_agents if isinstance(fa, dict) and "agent" in fa
                }

                # Enrich existing agents that match by name/role
                seen_fleet_names: set[str] = set()
                for agent in agents:
                    agent_name = (agent.name or agent.role or agent.id or "").lower()
                    if agent_name in fleet_lookup:
                        fa = fleet_lookup[agent_name]
                        # Overwrite with real-time fleet data
                        if fa.get("task_id"):
                            agent.task = fa["task_id"]
                        if fa.get("status"):
                            # Map fleet statuses to AgentState vocabulary
                            fleet_status_map = {
                                "working": "active",
                                "idle": "idle",
                                "error": "error",
                                "stale": "stale",
                            }
                            agent.status = fleet_status_map.get(fa["status"], fa["status"])
                        context_pct = fa.get("context_pct", 0)
                        agent.progress = int(context_pct) if context_pct is not None else 0
                        agent.is_stale = bool(fa.get("is_stale", False))
                        seen_fleet_names.add(agent_name)

                # Add fleet agents that are NOT already in the list
                agent_name_set = {
                    (a.name or a.role or a.id or "").lower() for a in agents
                }
                for fa_name, fa in fleet_lookup.items():
                    if fa_name not in agent_name_set:
                        fleet_status_map = {
                            "working": "active",
                            "idle": "idle",
                            "error": "error",
                            "stale": "stale",
                        }
                        fa_status = fleet_status_map.get(fa.get("status", "idle"), "idle")
                        context_pct = fa.get("context_pct", 0)
                        agents.append(
                            AgentState(
                                id=fa_name,
                                name=fa_name,
                                role=fa_name,
                                task=fa.get("task_id"),
                                status=fa_status,
                                progress=int(context_pct) if context_pct is not None else 0,
                                is_stale=bool(fa.get("is_stale", False)),
                                source="fleet",
                            )
                        )

                # Cache the business data from fleet summary for portfolio rendering
                self._fleet_summary_business: list[dict] = fleet_data.get("business", [])
            else:
                logger.debug(f"fetch_fleet_summary: HTTP {fleet_resp.status_code}")
        except Exception as e:
            logger.debug(f"Failed to enrich agents from fleet summary: {e}")

        self._cached_agents = agents  # Cache for detail view
        return agents

    def _load_tasks_from_api(self) -> list[dict]:
        """Load tasks from the webhook server API."""
        import httpx

        if not self.api_url:
            return []

        headers = {}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        try:
            response = httpx.get(
                f"{self.api_url}/api/tasks",
                headers=headers,
                timeout=5.0,
            )
            response.raise_for_status()
            body = response.json()
            data = body.get("data", {})
            tasks = data.get("tasks", []) if isinstance(data, dict) else []
            self._cached_tasks = tasks
            return tasks
        except Exception as e:
            logger.debug(f"Failed to load tasks from API: {e}")
            return getattr(self, "_cached_tasks", [])

    def _add_error(self, error: str) -> None:
        """Add an error to the recent errors buffer."""
        self._recent_errors.append((datetime.now(UTC), error))
        if len(self._recent_errors) > self._max_errors:
            self._recent_errors.pop(0)

    def _load_pending_approvals(self) -> list[ApprovalSummary]:
        """Load pending approvals from synchronizer, API, or storage directory."""
        approvals: list[ApprovalSummary] = []

        # Try synchronizer first if available
        if self._synchronizer is not None:
            try:
                snapshot = self._synchronizer.get_state_snapshot()
                for a in snapshot.approvals:
                    if a.status == "pending":
                        approvals.append(
                            ApprovalSummary(
                                id=a.id,
                                type=a.type,
                                title=a.title[:50] if a.title else "Untitled",
                                domain=a.domain or "unknown",
                                priority=a.priority or "normal",
                                tier=a.tier or "PHONE",
                                risk_score=a.risk_score or 0.5,
                                created_at=a.created_at,
                            )
                        )
                if approvals:
                    logger.debug(f"Loaded {len(approvals)} approvals from synchronizer")
                    return approvals
            except Exception as e:
                logger.debug(f"Failed to load approvals from synchronizer: {e}")

        # Try API if configured
        if self.api_url:
            try:
                api_approvals = self._load_pending_approvals_from_api()
                if api_approvals is not None:
                    approvals = api_approvals
            except Exception as e:
                logger.debug(f"Failed to load approvals from API: {e}")
                self._add_error(f"API error loading approvals: {e}")

        # Fallback to local files if no API or API failed
        if not approvals and self.approval_storage_dir.exists():
            for file_path in self.approval_storage_dir.glob("approval_*.json"):
                try:
                    data = json.loads(file_path.read_text())
                    if data.get("status") == "pending":
                        created_at = datetime.fromisoformat(
                            data.get("created_at", datetime.now(UTC).isoformat())
                        )
                        approvals.append(
                            ApprovalSummary(
                                id=data.get("id", "unknown"),
                                type=data.get("type", "unknown"),
                                title=data.get("title", "Untitled")[:50],
                                domain=data.get("domain", "unknown"),
                                priority=data.get("priority", "normal"),
                                tier=data.get("tier", "PHONE"),
                                risk_score=data.get("risk_score", 0.5),
                                created_at=created_at,
                            )
                        )
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    logger.debug(f"Failed to load approval {file_path}: {e}")

        # Sort by priority then age (using shared utility)
        approvals = sort_approvals_by_priority(approvals)

        # Check for new approvals and play sound if enabled
        self._check_new_approvals(approvals)

        # Apply time filter (using shared utility)
        approvals = filter_approvals_by_time(approvals, self._time_filter)

        return approvals

    def _check_new_approvals(self, approvals: list[ApprovalSummary]) -> None:
        """Check for new approvals and play alert sound if enabled."""
        current_count = len(approvals)
        if current_count > self._last_approval_count and self._last_approval_count > 0:
            self._play_alert_sound()
        self._last_approval_count = current_count

    def _play_alert_sound(self) -> None:
        """Play system beep for new approvals."""
        if not self._sound_alerts_enabled:
            return
        try:
            import curses

            curses.beep()
        except (ImportError, Exception):
            print("\a", end="", flush=True)

    def _load_pending_approvals_from_api(self) -> list[ApprovalSummary] | None:
        """Load pending approvals from webhook server API."""
        import httpx

        if not self.api_url:
            return None

        headers = {}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        try:
            # Use the approval count endpoint for efficiency
            response = httpx.get(
                f"{self.api_url}/api/approvals",
                headers=headers,
                params={"status": "pending", "limit": 50},
                timeout=5.0,
            )
            response.raise_for_status()

            data = response.json()
            approvals_data = data.get("data", {}).get("approvals", [])

            approvals = []
            for item in approvals_data:
                try:
                    created_at = datetime.fromisoformat(
                        item.get("created_at", datetime.now(UTC).isoformat())
                    )
                    approvals.append(
                        ApprovalSummary(
                            id=item.get("id", "unknown"),
                            type=item.get("type", "unknown"),
                            title=item.get("title", "Untitled")[:50],
                            domain=item.get("domain", "unknown"),
                            priority=item.get("priority", "normal"),
                            tier=item.get("tier", "PHONE"),
                            risk_score=item.get("risk_score", 0.5),
                            created_at=created_at,
                        )
                    )
                except (KeyError, ValueError) as e:
                    logger.debug(f"Failed to parse approval: {e}")

            # Sort by priority then age (using shared utility)
            approvals = sort_approvals_by_priority(approvals)

            return approvals

        except Exception as e:
            logger.debug(f"Failed to load approvals from API: {e}")
            return None

    def _load_active_pipelines(self) -> list[PipelineStatus]:
        """Load active pipelines from checkpoint directory."""
        pipelines = []

        if not self.checkpoint_dir.exists():
            return pipelines

        for file_path in self.checkpoint_dir.glob("*.json"):
            try:
                data = json.loads(file_path.read_text())
                status = data.get("status", "unknown")

                # Only show running or paused pipelines
                if status not in ("running", "paused", "waiting_human_gate"):
                    continue

                pipeline = data.get("pipeline", {})
                steps = pipeline.get("steps", [])
                step_results = data.get("step_results", {})

                # Find current step
                current_step = "unknown"
                step_index = 0
                for i, step in enumerate(steps):
                    step_name = step.get("name", f"step_{i}")
                    if step_name not in step_results:
                        current_step = step_name
                        step_index = i
                        break

                started_str = data.get("started_at")
                started_at = (
                    datetime.fromisoformat(started_str) if started_str else datetime.now(UTC)
                )

                pipelines.append(
                    PipelineStatus(
                        name=pipeline.get("name", file_path.stem),
                        current_step=current_step,
                        step_index=step_index,
                        total_steps=len(steps),
                        status=status,
                        started_at=started_at,
                        error=data.get("error"),
                    )
                )
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.debug(f"Failed to load checkpoint {file_path}: {e}")

        self._cached_pipelines = pipelines  # Cache for detail view
        return pipelines

    def _load_mvp_status(self) -> list[MVPStatusSummary]:
        """Load MVP check status from snapshots."""
        statuses: list[MVPStatusSummary] = []

        if not self.mvp_status_dir.exists():
            return statuses

        for file_path in self.mvp_status_dir.glob("*.json"):
            try:
                data = json.loads(file_path.read_text())
                timestamp = data.get("timestamp")
                parsed_ts = datetime.fromisoformat(timestamp) if timestamp else datetime.now(UTC)
                statuses.append(
                    MVPStatusSummary(
                        domain=data.get("domain", "unknown"),
                        project=data.get("project", file_path.stem),
                        status=data.get("status", "unknown"),
                        timestamp=parsed_ts,
                        missing_env_vars=len(data.get("missing_env_vars", []) or []),
                    )
                )
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.debug(f"Failed to load MVP status {file_path}: {e}")

        statuses.sort(key=lambda s: s.timestamp, reverse=True)
        return statuses

    def _load_ralph_status(self) -> RalphStatus:
        """Load Ralph loop status from checkpoint."""
        default_status = RalphStatus(
            active=False,
            current_feature=None,
            iteration=0,
            max_iterations=0,
            failure_count=0,
            state="idle",
            started_at=None,
        )

        if not self.ralph_checkpoint_dir.exists():
            return default_status

        # Look for most recent checkpoint
        checkpoints = sorted(
            self.ralph_checkpoint_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not checkpoints:
            return default_status

        try:
            data = json.loads(checkpoints[0].read_text())

            started_str = data.get("started_at")
            started_at = datetime.fromisoformat(started_str) if started_str else None

            return RalphStatus(
                active=data.get("state") in ("running", "implementing", "testing"),
                current_feature=data.get("current_feature"),
                iteration=data.get("iteration", 0),
                max_iterations=data.get("max_iterations", 100),
                failure_count=data.get("failure_count", 0),
                state=data.get("state", "idle"),
                started_at=started_at,
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.debug(f"Failed to load Ralph checkpoint: {e}")
            return default_status

    def render_header(self) -> Panel:
        """Render dashboard header with keyboard shortcuts and status."""
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        text = Text()
        text.append("FORGE Harness Dashboard", style="bold cyan")
        text.append(f"  |  {now}", style="dim")

        # Sound indicator
        text.append("  |  ", style="dim")
        if self._sound_alerts_enabled:
            text.append("🔔", style="green")
        else:
            text.append("🔕", style="dim")

        # Time filter indicator
        if self._time_filter != "all":
            text.append(f"  |  Filter: {self._time_filter}", style="cyan")

        text.append("  |  ", style="dim")
        text.append("j/k", style="bold yellow")
        text.append("=nav  ", style="dim")
        text.append("a", style="bold green")
        text.append("=approve  ", style="dim")
        text.append("r", style="bold red")
        text.append("=reject  ", style="dim")
        text.append("t", style="bold cyan")
        text.append("=filter  ", style="dim")
        text.append("?", style="bold magenta")
        text.append("=help  ", style="dim")
        text.append("q", style="bold yellow")
        text.append("=quit", style="dim")

        # Show status message if recent (within 5 seconds)
        if self._status_message and self._status_message_time:
            age = (datetime.now(UTC) - self._status_message_time).total_seconds()
            if age < 5:
                text.append("  |  ", style="dim")
                if "✓" in self._status_message:
                    text.append(self._status_message, style="bold green")
                elif "✗" in self._status_message or "Error" in self._status_message:
                    text.append(self._status_message, style="bold red")
                else:
                    text.append(self._status_message, style="yellow")
            else:
                self._status_message = None

        return Panel(text, style="blue")

    async def _load_health_status(self) -> Any | None:
        """Load health status from API or local registry.

        Returns:
            AggregatedHealth or None if unavailable
        """
        try:
            # Try API endpoint first if api_url is configured
            if self.api_url:
                try:
                    import httpx

                    async with httpx.AsyncClient(timeout=10.0) as client:
                        headers = {}
                        if self.api_token:
                            headers["Authorization"] = f"Bearer {self.api_token}"

                        response = await client.get(f"{self.api_url}/api/health", headers=headers)
                        if response.status_code == 200:
                            data = response.json()
                            # Convert API response dict to AggregatedHealth object
                            health_data = self._parse_api_health_response(data)
                            self._last_health_check = health_data
                            self._last_health_check_time = datetime.now(UTC)
                            self._health_check_error = None
                            return health_data
                except Exception as e:
                    logger.debug(f"Failed to load health status from API: {e}")
                    self._health_check_error = f"API error: {str(e)[:50]}"

            # Fall back to local HealthCheckRegistry
            try:
                from .health_checks import get_health_registry

                registry = await get_health_registry()
                health_data = await registry.check_all()
                self._last_health_check = health_data
                self._last_health_check_time = datetime.now(UTC)
                self._health_check_error = None
                return health_data
            except Exception as e:
                logger.debug(f"Failed to load health status from registry: {e}")
                self._health_check_error = f"Registry error: {str(e)[:50]}"
                return None
        except Exception as e:
            logger.warning(f"Unexpected error loading health status: {e}")
            self._health_check_error = f"Error: {str(e)[:50]}"
            return None

    def _parse_api_health_response(self, data: dict) -> Any:
        """Parse API health response dict into AggregatedHealth object.

        Args:
            data: Raw dict from /api/health endpoint

        Returns:
            AggregatedHealth object with services list, or None if parsing fails
        """
        try:
            from .health_checks import AggregatedHealth, HealthStatus, ServiceHealth

            services: list[ServiceHealth] = []

            # Map status strings to HealthStatus
            def status_to_enum(status_str: str) -> HealthStatus:
                status_lower = status_str.lower()
                if status_lower in ("ok", "healthy", "connected", "running"):
                    return HealthStatus.HEALTHY
                elif status_lower in ("degraded", "warning"):
                    return HealthStatus.DEGRADED
                else:
                    return HealthStatus.UNHEALTHY

            # Extract service statuses from API response
            # API returns: redis_status, sqlite_status, synchronizer_status, etc.
            service_mappings = [
                ("redis_status", "Redis"),
                ("sqlite_status", "SQLite"),
                ("synchronizer_status", "Synchronizer"),
            ]

            for status_key, service_name in service_mappings:
                if status_key in data:
                    status_value = data[status_key]
                    services.append(
                        ServiceHealth(
                            name=service_name,
                            status=status_to_enum(status_value),
                            latency_ms=0.0,  # API doesn't provide per-service latency
                            message=status_value,
                        )
                    )

            # Add main service if no services were extracted
            if not services and "service" in data:
                services.append(
                    ServiceHealth(
                        name=data.get("service", "forge-harness"),
                        status=status_to_enum(data.get("status", "unknown")),
                        latency_ms=0.0,
                        message=f"Service: {data.get('status', 'unknown')}",
                    )
                )

            # Determine overall status
            if all(s.status == HealthStatus.HEALTHY for s in services):
                overall_status = HealthStatus.HEALTHY
            elif any(s.status == HealthStatus.UNHEALTHY for s in services):
                overall_status = HealthStatus.UNHEALTHY
            else:
                overall_status = HealthStatus.DEGRADED

            return AggregatedHealth(
                status=overall_status,
                services=services,
                checked_at=datetime.now(UTC),
                total_latency_ms=0.0,
            )
        except Exception as e:
            logger.debug(f"Failed to parse health response: {e}")
            return None

    def _load_health_status_sync(self) -> None:
        """Synchronously fetch health status from the API.

        Used by snapshot() where no event loop is running.  Falls back silently
        so the caller always gets a panel rather than an exception.
        """
        # Default to localhost if no API URL provided
        api_url = (
            self.api_url
            or os.environ.get("COMMAND_CENTER_URL")
            or os.environ.get("FORGE_API_URL")
            or "http://127.0.0.1:8080"
        )

        try:
            import json
            import subprocess

            url = f"{api_url}/health"
            cmd = ["curl", "-s", "--max-time", "10", url]
            if self.api_token:
                cmd.extend(["-H", f"Authorization: Bearer {self.api_token}"])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                health_data = self._parse_api_health_response(data)
                self._last_health_check = health_data
                self._last_health_check_time = datetime.now(UTC)
                self._health_check_error = None
        except Exception as e:
            logger.debug(f"Sync health fetch failed: {e}")
            self._health_check_error = f"API error: {str(e)[:50]}"

    def render_health_status(self) -> Panel:
        """Render health status panel."""
        if self._health_check_error:
            text = Text(f"[red]{self._health_check_error}[/red]")
            return Panel(
                text,
                title="[bold blue]Health Status[/]",
                border_style="blue",
                padding=(1, 2),
            )

        return dashboard_detail_views.render_health_status(self._last_health_check)

    async def _health_check_loop(self, interval: float) -> None:
        """Background task for periodic health checks.

        Args:
            interval: Seconds between health checks
        """
        while True:
            try:
                await self._load_health_status()
            except Exception as e:
                logger.debug(f"Health check loop error: {e}")
            await asyncio.sleep(interval)

    def render_pipelines(self) -> Panel:
        """Render active pipelines table."""
        pipelines = self._load_active_pipelines()

        table = Table(
            title="Active Pipelines",
            title_style="bold green",
            show_header=True,
            header_style="bold",
            expand=True,
        )
        table.add_column("Pipeline", style="cyan", no_wrap=True)
        table.add_column("Step", style="white")
        table.add_column("Progress", justify="center")
        table.add_column("Status", justify="center")
        table.add_column("Duration", justify="right", style="magenta")

        if not pipelines:
            table.add_row("[dim]No active pipelines[/dim]", "", "", "", "")
        else:
            for p in pipelines:
                # Status styling
                status_style = {
                    "running": "green",
                    "paused": "yellow",
                    "waiting_human_gate": "yellow bold",
                }.get(p.status, "white")

                # Progress bar text
                progress = f"{p.step_index + 1}/{p.total_steps}"

                # Duration
                duration = (datetime.now(UTC) - p.started_at).total_seconds()

                table.add_row(
                    p.name[:25],
                    p.current_step[:20],
                    progress,
                    f"[{status_style}]{p.status}[/{status_style}]",
                    format_duration(duration),
                )

        return Panel(table, border_style="green")

    def render_tasks(self) -> Panel:
        """Render task queue panel with lease/lock visibility (CP-3003)."""
        tasks = self._load_tasks_from_api()

        table = Table(
            title=f"Task Queue ({len(tasks)})",
            title_style="bold cyan",
            show_header=True,
            header_style="bold",
            expand=True,
        )
        table.add_column("ID", style="dim", width=8, no_wrap=True)
        table.add_column("Subject", style="white")
        table.add_column("Status", justify="center", width=10)
        table.add_column("Lease", justify="center", width=14)
        table.add_column("Lock", style="dim", width=16)

        if not tasks:
            if self.api_url:
                table.add_row("[dim]No tasks[/dim]", "", "", "", "")
            else:
                table.add_row("[dim]No API[/dim]", "", "", "", "")
        else:
            # Show up to 12 tasks, sorted: assigned first, then pending
            status_order = {
                "assigned": 0,
                "in_progress": 1,
                "pending": 2,
                "blocked": 3,
                "failed": 4,
                "completed": 5,
                "cancelled": 6,
            }
            sorted_tasks = sorted(
                tasks,
                key=lambda t: status_order.get(t.get("status", "pending"), 9),
            )
            for t in sorted_tasks[:12]:
                tid = t.get("id", "")[:8]
                subject = (t.get("subject", "") or t.get("title", ""))[:28]
                status = t.get("status", "pending")

                status_style = {
                    "assigned": "green",
                    "in_progress": "green bold",
                    "pending": "yellow",
                    "blocked": "red",
                    "failed": "red bold",
                    "completed": "blue",
                }.get(status, "white")
                status_display = f"[{status_style}]{status}[/{status_style}]"

                # Lease info
                lease = t.get("lease")
                if isinstance(lease, dict) and lease:
                    owner = lease.get("owner_agent", "")
                    # Check if lease is stale
                    expires = lease.get("lease_expires_at", "")
                    is_stale = False
                    if expires:
                        try:
                            exp_dt = datetime.fromisoformat(
                                str(expires).replace("Z", "+00:00"),
                            )
                            is_stale = exp_dt < datetime.now(UTC)
                        except (ValueError, TypeError):
                            pass
                    if is_stale:
                        lease_display = "[red bold]STALE[/red bold]"
                    else:
                        lease_display = f"[green]{owner[:12]}[/green]"
                    lock_display = lease.get("path_lock", "")[:16]
                else:
                    lease_display = "[dim]-[/dim]"
                    lock_display = ""

                table.add_row(tid, subject, status_display, lease_display, lock_display)

            if len(tasks) > 12:
                table.add_row(
                    f"[dim]+{len(tasks) - 12}[/dim]",
                    "",
                    "",
                    "",
                    "",
                )

        return Panel(table, border_style="cyan")

    def render_mvp_status(self) -> Panel:
        """Render MVP check status panel."""
        statuses = self._load_mvp_status()

        table = Table(
            title="MVP Check Status",
            title_style="bold magenta",
            show_header=True,
            header_style="bold",
            expand=True,
        )
        table.add_column("Project", style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Last Run", style="dim", justify="right")
        table.add_column("Env", justify="right")

        if not statuses:
            table.add_row("[dim]No MVP status yet[/dim]", "", "", "")
            return Panel(table, border_style="magenta")

        for status in statuses[:8]:
            status_label = {
                "passed": "[green]PASSED[/green]",
                "failed": "[red]FAILED[/red]",
            }.get(status.status, "[yellow]UNKNOWN[/yellow]")

            env_label = (
                f"[yellow]{status.missing_env_vars}[/yellow]"
                if status.missing_env_vars
                else "[green]0[/green]"
            )

            table.add_row(
                f"{status.domain}/{status.project}",
                status_label,
                format_age(status.timestamp),
                env_label,
            )

        return Panel(table, border_style="magenta")

    def render_approvals(self) -> Panel:
        """Render pending approvals table with keyboard navigation."""
        approvals = self._load_pending_approvals()
        self._cached_approvals = approvals  # Cache for keyboard actions

        # Ensure selected index is valid
        if approvals:
            self._selected_approval_index = max(
                0, min(self._selected_approval_index, len(approvals) - 1)
            )
        else:
            self._selected_approval_index = 0

        table = Table(
            title=f"Pending Approvals ({len(approvals)})",
            title_style="bold yellow",
            show_header=True,
            header_style="bold",
            expand=True,
        )
        table.add_column("Tier", style="white", no_wrap=True, width=5)
        table.add_column("ID", style="cyan", no_wrap=True, width=10)
        table.add_column("Type", style="white", width=10)
        table.add_column("Title", style="white")
        table.add_column("Priority", justify="center", width=10)
        table.add_column("Age", justify="right", style="magenta", width=8)

        if not approvals:
            table.add_row("[dim]No pending approvals[/dim]", "", "", "", "", "")
        else:
            for i, a in enumerate(approvals[:10]):  # Limit to 10
                # Tier badge with color
                tier_display = {
                    "WATCH": "[green]W[/green]",
                    "PHONE": "[yellow]P[/yellow]",
                    "DESKTOP": "[red]D[/red]",
                }.get(a.tier, "[dim]?[/dim]")

                # Priority styling
                priority_style = {
                    "critical": "red bold",
                    "high": "yellow",
                    "normal": "white",
                    "low": "dim",
                }.get(a.priority, "white")

                # Highlight selected row
                is_selected = i == self._selected_approval_index
                row_style = "reverse" if is_selected else ""
                selector = "►" if is_selected else " "

                table.add_row(
                    tier_display,
                    f"{selector}{a.id[:7]}...",
                    a.type,
                    a.title[:32],  # Reduced slightly to fit tier column
                    f"[{priority_style}]{a.priority}[/{priority_style}]",
                    format_age(a.created_at),
                    style=row_style,
                )

        return Panel(table, border_style="yellow")

    def render_ralph_status(self) -> Panel:
        """Render Ralph loop status."""
        status = self._load_ralph_status()

        # Build status text
        lines = []

        # State indicator
        if status.active:
            state_text = Text()
            state_text.append("● ", style="green bold")
            state_text.append(status.state.upper(), style="green")
            lines.append(state_text)
        else:
            state_text = Text()
            state_text.append("○ ", style="dim")
            state_text.append("IDLE", style="dim")
            lines.append(state_text)

        # Current feature
        feature_text = Text()
        feature_text.append("Feature: ", style="bold")
        if status.current_feature:
            feature_text.append(status.current_feature[:40], style="cyan")
        else:
            feature_text.append("None", style="dim")
        lines.append(feature_text)

        # Progress
        progress_text = Text()
        progress_text.append("Iteration: ", style="bold")
        progress_text.append(f"{status.iteration}/{status.max_iterations}", style="white")
        lines.append(progress_text)

        # Failures
        failure_text = Text()
        failure_text.append("Failures: ", style="bold")
        failure_style = "red" if status.failure_count > 0 else "green"
        failure_text.append(str(status.failure_count), style=failure_style)
        lines.append(failure_text)

        # Duration if active
        if status.active and status.started_at:
            duration = (datetime.now(UTC) - status.started_at).total_seconds()
            duration_text = Text()
            duration_text.append("Duration: ", style="bold")
            duration_text.append(format_duration(duration), style="magenta")
            lines.append(duration_text)

        # Combine lines
        content = Text("\n").join(lines)

        return Panel(
            content,
            title="Ralph Loop",
            title_align="left",
            border_style="cyan",
        )

    def render_agents(self) -> Panel:
        """Render active agents panel from Command Center API."""
        agents = self._load_agents_from_api()

        table = Table(
            title=f"Active Agents ({len(agents)})",
            title_style="bold blue",
            show_header=True,
            header_style="bold",
            expand=True,
        )
        table.add_column("Role", style="cyan", no_wrap=True, width=15)
        table.add_column("Task", style="white")
        table.add_column("Progress", justify="center", width=10)
        table.add_column("Status", justify="center", width=10)
        table.add_column("Last Active", justify="right", style="dim", width=10)

        if not agents:
            if self.api_url:
                table.add_row("[dim]No active agents[/dim]", "", "", "", "")
            else:
                table.add_row("[dim]No API configured[/dim]", "", "", "", "")
        else:
            # Display up to 16 agents to accommodate typical fleet sizes
            max_display = 16
            for agent in agents[:max_display]:
                # Check if agent is stale (offline)
                is_stale = getattr(agent, "is_stale", False)

                # Status styling - override if stale
                if is_stale:
                    status_display = "[dim red]offline[/dim red]"
                    role_style = "dim"
                else:
                    status_style = {
                        "active": "green",
                        "idle": "yellow",
                        "completed": "blue",
                        "error": "red",
                    }.get(agent.status, "white")
                    status_display = f"[{status_style}]{agent.status}[/{status_style}]"
                    role_style = "cyan"

                # Progress bar representation
                progress_bar = self._render_progress_bar(agent.progress)

                # Task (truncated)
                task_display = (agent.task or "-")[:30]

                # Last activity
                last_active = format_age(agent.last_activity) if agent.last_activity else "-"

                table.add_row(
                    f"[{role_style}]{agent.role[:15]}[/{role_style}]",
                    task_display if not is_stale else f"[dim]{task_display}[/dim]",
                    progress_bar,
                    status_display,
                    last_active,
                )

            # Show indicator if more agents exist
            if len(agents) > max_display:
                table.add_row(
                    f"[dim]... +{len(agents) - max_display} more[/dim]",
                    "",
                    "",
                    "",
                    "",
                )

        return Panel(table, border_style="blue")

    def _render_progress_bar(self, progress: int) -> str:
        """Render a simple text progress bar."""
        filled = progress // 10
        empty = 10 - filled
        bar = "█" * filled + "░" * empty
        return f"[green]{bar}[/green]"

    def _load_portfolio_from_api(self) -> dict[str, Any] | None:
        """Load portfolio data from webhook server API."""
        import httpx

        if not self.api_url:
            return None

        headers = {}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        try:
            response = httpx.get(
                f"{self.api_url}/api/portfolio",
                headers=headers,
                timeout=5.0,
            )
            response.raise_for_status()
            return response.json().get("data")
        except Exception as e:
            logger.debug(f"Failed to load portfolio from API: {e}")
            return None

    def _load_patterns_from_api(self) -> list[dict[str, Any]]:
        """Load pattern data from webhook server API."""
        import httpx

        if not self.api_url:
            return []

        headers = {}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        try:
            response = httpx.get(
                f"{self.api_url}/api/patterns",
                headers=headers,
                timeout=5.0,
            )
            response.raise_for_status()
            data = response.json().get("data", {})
            patterns = data.get("patterns", [])
            self._cached_patterns = patterns  # Cache for detail view
            return patterns
        except Exception as e:
            logger.debug(f"Failed to load patterns from API: {e}")
            self._add_error(f"API error loading patterns: {e}")
            return []

    def render_errors(self) -> Panel:
        """Render recent errors panel."""
        table = Table(
            title="Recent Errors",
            title_style="bold red",
            show_header=True,
            header_style="bold",
            expand=True,
        )
        table.add_column("Time", style="dim", width=8)
        table.add_column("Error", style="red")

        if not self._recent_errors:
            table.add_row("[dim]No recent errors[/dim]", "")
        else:
            for dt, error in self._recent_errors[-5:]:
                table.add_row(
                    format_age(dt),
                    error[:60],
                )

        return Panel(table, border_style="red")

    def render_portfolio(self) -> Panel:
        """Render portfolio overview panel with domain/project tree and MRR data."""
        portfolio = self._load_portfolio_from_api()

        # Check if fleet summary business data is available (populated by _load_agents_from_api)
        fleet_business: list[dict] = getattr(self, "_fleet_summary_business", [])

        # If we have fleet business data, prefer it — it has deploy_status and MRR
        if fleet_business:
            table = Table(
                title="Portfolio — Business Overview",
                title_style="bold cyan",
                show_header=True,
                header_style="bold",
                expand=True,
            )
            table.add_column("Domain", style="cyan", no_wrap=True)
            table.add_column("Deploy", justify="center", width=12)
            table.add_column("MRR", justify="right", width=8)
            table.add_column("Target", justify="right", width=8)
            table.add_column("Stripe", justify="center", width=7)

            total_mrr = 0
            for biz in fleet_business[:10]:
                domain_name = biz.get("display_name", biz.get("domain", "Unknown"))
                deploy_status = biz.get("deploy_status", "none")
                current_mrr = biz.get("current_mrr", 0) or 0
                target_mrr = biz.get("target_mrr", 0) or 0
                stripe_live = biz.get("stripe_live", False)
                total_mrr += current_mrr

                # Colour-code deploy status
                if deploy_status == "live":
                    deploy_display = "[green]live[/green]"
                elif deploy_status == "config_ready":
                    deploy_display = "[yellow]ready[/yellow]"
                elif deploy_status == "none":
                    deploy_display = "[dim]none[/dim]"
                else:
                    deploy_display = f"[dim]{deploy_status[:10]}[/dim]"

                stripe_display = "[green]live[/green]" if stripe_live else "[dim]test[/dim]"
                mrr_display = f"${current_mrr:,}" if current_mrr else "[dim]-[/dim]"
                target_display = f"${target_mrr:,}" if target_mrr else "[dim]-[/dim]"

                table.add_row(
                    domain_name[:20],
                    deploy_display,
                    mrr_display,
                    target_display,
                    stripe_display,
                )

            # Summary row
            table.add_row(
                "[bold]Total MRR[/bold]",
                "",
                f"[bold]${total_mrr:,}[/bold]",
                "",
                "",
            )
            return Panel(table, border_style="cyan")

        # Fall back to portfolio API data (project counts / compliance)
        table = Table(
            title="Portfolio Overview",
            title_style="bold cyan",
            show_header=True,
            header_style="bold",
            expand=True,
        )
        table.add_column("Domain", style="cyan", no_wrap=True)
        table.add_column("Projects", justify="center", width=8)
        table.add_column("Status", justify="center", width=10)
        table.add_column("Compliance", style="dim")

        if not portfolio:
            if self.api_url:
                table.add_row("[dim]Loading...[/dim]", "", "", "")
            else:
                table.add_row("[dim]No API configured[/dim]", "", "", "")
        else:
            domains = portfolio.get("domains", [])
            total_projects = portfolio.get("total_projects", 0)
            active_domains = portfolio.get("active_domains", 0)

            # Show top domains by project count
            sorted_domains = sorted(domains, key=lambda d: d.get("project_count", 0), reverse=True)

            for domain in sorted_domains[:8]:  # Limit to 8 rows
                domain_name = domain.get("display_name", domain.get("id", "Unknown"))
                project_count = domain.get("project_count", 0)
                is_active = domain.get("active", False)
                compliance = domain.get("compliance", [])

                # Status indicator
                status = "[green]active[/green]" if is_active else "[dim]inactive[/dim]"

                # Compliance badges (truncated)
                compliance_str = ", ".join(compliance[:2])
                if len(compliance) > 2:
                    compliance_str += "..."

                table.add_row(
                    domain_name[:20],
                    str(project_count),
                    status,
                    compliance_str[:20] if compliance_str else "-",
                )

            # Add summary row
            table.add_row(
                "[bold]Total[/bold]",
                f"[bold]{total_projects}[/bold]",
                f"[bold]{active_domains}[/bold]",
                "",
            )

        return Panel(table, border_style="cyan")

    def render_patterns(self) -> Panel:
        """Render pattern stats panel with top patterns by success rate."""
        patterns = self._load_patterns_from_api()

        table = Table(
            title="Pattern Stats",
            title_style="bold magenta",
            show_header=True,
            header_style="bold",
            expand=True,
        )
        table.add_column("Pattern", style="cyan")
        table.add_column("Category", style="white", width=12)
        table.add_column("Success", justify="center", width=8)
        table.add_column("Uses", justify="right", width=6)

        if not patterns:
            if self.api_url:
                table.add_row("[dim]No patterns yet[/dim]", "", "", "")
            else:
                table.add_row("[dim]No API configured[/dim]", "", "", "")
        else:
            # Filter out test patterns and sort by success rate
            real_patterns = [
                p for p in patterns if p.get("uses", 0) > 0 and p.get("category") != "test"
            ]

            # Sort by success rate (highest first)
            sorted_patterns = sorted(
                real_patterns,
                key=lambda p: (p.get("success_rate", 0), p.get("uses", 0)),
                reverse=True,
            )

            # Show top 5 patterns or all if fewer
            display_patterns = sorted_patterns[:5] if sorted_patterns else patterns[:5]

            for pattern in display_patterns:
                name = pattern.get("name", "Unknown")
                category = pattern.get("category", "-")
                success_rate = pattern.get("success_rate", 0.0)
                uses = pattern.get("uses", 0)

                # Color code success rate
                if success_rate >= 0.8:
                    success_display = f"[green]{success_rate:.0%}[/green]"
                elif success_rate >= 0.6:
                    success_display = f"[yellow]{success_rate:.0%}[/yellow]"
                else:
                    success_display = f"[red]{success_rate:.0%}[/red]"

                table.add_row(
                    name[:25],
                    category[:12],
                    success_display,
                    str(uses),
                )

            # Add summary row
            total_patterns = len(patterns)
            avg_success = (
                sum(p.get("success_rate", 0) for p in patterns) / len(patterns) if patterns else 0
            )
            total_uses = sum(p.get("uses", 0) for p in patterns)

            table.add_row(
                f"[bold]Total: {total_patterns}[/bold]",
                "[bold]Avg[/bold]",
                f"[bold]{avg_success:.0%}[/bold]",
                f"[bold]{total_uses}[/bold]",
            )

        return Panel(table, border_style="magenta")

    def render_help_modal(self) -> Panel:
        """Render help modal with keyboard shortcuts."""
        text = Text()
        text.append("FORGE Dashboard - Keyboard Shortcuts\n\n", style="bold cyan")

        # Navigation
        text.append("Navigation\n", style="bold yellow")
        text.append("  j/↓  Move down\n")
        text.append("  k/↑  Move up\n")
        text.append("  q    Quit\n\n")

        # Actions
        text.append("Actions\n", style="bold yellow")
        text.append("  a    Approve selected\n")
        text.append("  r    Reject selected\n")
        text.append("  Enter View details\n\n")

        # Filters
        text.append("Filters\n", style="bold yellow")
        text.append("  t    Toggle time filter (all/1h/24h)\n")
        text.append("  s    Toggle sound alerts\n\n")

        # Other
        text.append("Other\n", style="bold yellow")
        text.append("  ?    Show/hide this help\n")
        text.append("  R    Force refresh\n\n")

        text.append("Press any key to close", style="dim italic")

        return Panel(
            text,
            title="[bold magenta]Help[/]",
            border_style="bold magenta",
            padding=(1, 2),
        )

    def create_layout(self) -> Layout:
        """Create the dashboard layout."""
        layout = Layout()

        layout.split(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=10),
        )

        layout["main"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="center", ratio=1),
            Layout(name="right", ratio=1),
        )

        layout["left"].split(
            Layout(name="pipelines"),
            Layout(name="tasks"),
            Layout(name="approvals"),
        )

        layout["center"].split(
            Layout(name="agents"),
            Layout(name="portfolio"),
        )

        layout["right"].split(
            Layout(name="patterns"),
            Layout(name="mvp_status"),
        )

        # Add footer split for errors and health status
        layout["footer"].split_row(
            Layout(name="errors", ratio=2),
            Layout(name="health", ratio=1),
        )

        return layout

    def render_layout(self) -> Layout:
        """Render the full dashboard layout."""
        layout = self.create_layout()

        layout["header"].update(self.render_header())

        # If detail view is active, show it in the main area
        if self._detail_view_active and self._detail_view_item:
            detail_panel = None
            if self._detail_view_type == "agent":
                detail_panel = dashboard_detail_views.render_agent_detail(
                    self._detail_view_item, self.forge_root / ".forge/sessions"
                )
            elif self._detail_view_type == "approval":
                detail_panel = dashboard_detail_views.render_approval_detail(
                    self._detail_view_item, self.approval_storage_dir
                )
            elif self._detail_view_type == "pipeline":
                detail_panel = dashboard_detail_views.render_pipeline_detail(
                    self._detail_view_item, self.checkpoint_dir
                )
            elif self._detail_view_type == "pattern":
                detail_panel = dashboard_detail_views.render_pattern_detail(self._detail_view_item)

            if detail_panel:
                # Show detail in the main area (spanning left and center)
                layout["left"].update(detail_panel)
                layout["center"].update(Panel("", border_style="dim"))
                layout["right"].split(
                    Layout(name="patterns"),
                    Layout(name="mvp_status"),
                )
                layout["patterns"].update(self.render_patterns())
                layout["mvp_status"].update(self.render_mvp_status())
                layout["footer"].update(self.render_errors())
                return layout

        # Normal layout rendering
        layout["pipelines"].update(self.render_pipelines())
        layout["tasks"].update(self.render_tasks())
        layout["patterns"].update(self.render_patterns())
        layout["errors"].update(self.render_errors())
        layout["health"].update(self.render_health_status())

        # Overlay help modal on approvals area if active
        if self._show_help_modal:
            layout["approvals"].update(self.render_help_modal())
        else:
            layout["approvals"].update(self.render_approvals())

        layout["agents"].update(self.render_agents())
        layout["portfolio"].update(self.render_portfolio())
        layout["mvp_status"].update(self.render_mvp_status())

        return layout

    async def run(self, refresh_rate: float = 1.0) -> None:
        """
        Run the live dashboard with keyboard navigation.

        Args:
            refresh_rate: How often to refresh in seconds

        Keyboard shortcuts:
            j/↓: Move selection down
            k/↑: Move selection up
            a: Approve selected item
            r: Reject selected item
            t: Toggle time filter (all/1h/24h)
            s: Toggle sound alerts
            ?: Show/hide help
            q: Quit
        """
        self.console.print("[bold cyan]Starting FORGE Dashboard...[/bold cyan]")
        self.console.print(
            "[dim]Keys: j/k=nav, a=approve, r=reject, t=filter, ?=help, q=quit[/dim]\n"
        )

        # Start keyboard handler
        self._keyboard_handler = KeyboardHandler()
        self._keyboard_handler.start()

        # Start health check background task
        self._health_check_task = asyncio.create_task(
            self._health_check_loop(self._health_check_interval)
        )

        try:
            with Live(
                self.render_layout(),
                console=self.console,
                refresh_per_second=1 / refresh_rate,
                screen=True,
            ) as live:
                while True:
                    # Process keyboard input
                    if self._keyboard_handler:
                        key = self._keyboard_handler.get_key()
                        if key:
                            # If help modal is showing, any key dismisses it
                            if self._show_help_modal:
                                self._show_help_modal = False
                            # If detail view is active, ESC closes it
                            elif self._detail_view_active and key == "ESC":
                                self._detail_view_active = False
                                self._detail_view_type = None
                                self._detail_view_item = None
                            # If detail view is active, handle detail-specific keys
                            elif self._detail_view_active:
                                if key in ("a", "A") and self._detail_view_type == "approval":
                                    await self._approve_selected()
                                    self._detail_view_active = False
                                elif key in ("r", "R") and self._detail_view_type == "approval":
                                    await self._reject_selected()
                                    self._detail_view_active = False
                            # Normal navigation
                            elif key in ("q", "Q"):
                                break
                            elif key in ("j", "DOWN"):
                                self._selected_approval_index += 1
                            elif key in ("k", "UP"):
                                self._selected_approval_index = max(
                                    0, self._selected_approval_index - 1
                                )
                            elif key == "\n" or key == "\r":
                                # Enter key - open detail view for selected item
                                self._open_detail_view()
                            elif key in ("a", "A"):
                                await self._approve_selected()
                            elif key in ("r", "R"):
                                await self._reject_selected()
                            elif key == "?":
                                self._show_help_modal = True
                            elif key in ("t", "T"):
                                # Cycle time filter: all -> 1h -> 24h -> all
                                if self._time_filter == "all":
                                    self._time_filter = "1h"
                                elif self._time_filter == "1h":
                                    self._time_filter = "24h"
                                else:
                                    self._time_filter = "all"
                                self._set_status(f"Filter: {self._time_filter}")
                            elif key in ("s", "S"):
                                self._sound_alerts_enabled = not self._sound_alerts_enabled
                                status = "on" if self._sound_alerts_enabled else "off"
                                self._set_status(f"Sound alerts: {status}")

                    live.update(self.render_layout())
                    await asyncio.sleep(refresh_rate)
        except KeyboardInterrupt:
            pass
        finally:
            # Cancel health check background task
            if self._health_check_task:
                self._health_check_task.cancel()
                try:
                    await self._health_check_task
                except asyncio.CancelledError:
                    pass

            if self._keyboard_handler:
                self._keyboard_handler.stop()
            self.console.print("\n[yellow]Dashboard stopped.[/yellow]")

    async def _approve_selected(self) -> None:
        """Approve the currently selected approval request."""
        if not self._cached_approvals:
            self._set_status("No approvals to approve")
            return

        if self._selected_approval_index >= len(self._cached_approvals):
            return

        approval = self._cached_approvals[self._selected_approval_index]

        try:
            from .approval_queue import create_approval_queue

            queue = create_approval_queue(storage_dir=self.approval_storage_dir)
            await queue.approve(approval.id, approved_by="dashboard-user")
            self._set_status(f"✓ Approved: {approval.id[:8]}")
        except Exception as e:
            self._set_status(f"Error approving: {e}")
            logger.error(f"Failed to approve {approval.id}: {e}")

    async def _reject_selected(self) -> None:
        """Reject the currently selected approval request."""
        if not self._cached_approvals:
            self._set_status("No approvals to reject")
            return

        if self._selected_approval_index >= len(self._cached_approvals):
            return

        approval = self._cached_approvals[self._selected_approval_index]

        try:
            from .approval_queue import create_approval_queue

            queue = create_approval_queue(storage_dir=self.approval_storage_dir)
            await queue.reject(approval.id, rejected_by="dashboard-user", reason="Rejected via TUI")
            self._set_status(f"✗ Rejected: {approval.id[:8]}")
        except Exception as e:
            self._set_status(f"Error rejecting: {e}")
            logger.error(f"Failed to reject {approval.id}: {e}")

    def _set_status(self, message: str) -> None:
        """Set a temporary status message."""
        self._status_message = message
        self._status_message_time = datetime.now(UTC)

    def _open_detail_view(self) -> None:
        """Open detail view for the currently selected item."""
        # Currently only supporting approval detail view
        # Can be extended to support agent/pipeline/pattern based on context
        if self._cached_approvals and self._selected_approval_index < len(self._cached_approvals):
            self._detail_view_active = True
            self._detail_view_type = "approval"
            self._detail_view_item = self._cached_approvals[self._selected_approval_index]
            self._set_status("Viewing detail - Press ESC to return")

    def snapshot(self) -> None:
        """Print a single snapshot of the dashboard (non-live)."""
        # Populate health data synchronously before rendering (no event loop here).
        self._load_health_status_sync()
        self.console.print(self.render_header())
        self.console.print(self.render_pipelines())
        self.console.print(self.render_approvals())
        self.console.print(self.render_agents())
        self.console.print(self.render_portfolio())
        self.console.print(self.render_patterns())
        self.console.print(self.render_mvp_status())
        self.console.print(self.render_errors())
        self.console.print(self.render_health_status())


def create_dashboard(
    forge_root: Path | None = None,
    approval_storage_dir: Path | None = None,
    checkpoint_dir: Path | None = None,
    ralph_checkpoint_dir: Path | None = None,
    api_url: str | None = None,
    api_token: str | None = None,
) -> ForgeDashboard:
    """
    Factory function to create a ForgeDashboard.

    Args:
        forge_root: FORGE project root directory
        approval_storage_dir: Directory for approval files
        checkpoint_dir: Directory for pipeline checkpoints
        ralph_checkpoint_dir: Directory for Ralph checkpoints
        api_url: Command Center API URL for agent data
        api_token: API authentication token

    Returns:
        Configured ForgeDashboard instance
    """
    if COMPATIBILITY_MODE:
        logger.warning(deprecation_notice())
    return ForgeDashboard(
        forge_root=forge_root,
        approval_storage_dir=approval_storage_dir,
        checkpoint_dir=checkpoint_dir,
        ralph_checkpoint_dir=ralph_checkpoint_dir,
        api_url=api_url,
        api_token=api_token,
    )
