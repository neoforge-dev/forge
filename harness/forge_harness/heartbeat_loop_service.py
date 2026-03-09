"""Heartbeat Loop Service

Shared Python backend service for the Heartbeat Loop.  Tracks loop state,
computes per-tick triggers, snapshots fleet status, and optionally emits
SSE events to the Command Center.

Usage::

    from forge_harness.heartbeat_loop_service import get_heartbeat_loop_service

    svc = get_heartbeat_loop_service()
    result = svc.eval()          # increment count and compute triggers
    print(result.one_line)

    svc.mark_retro_done()        # record that a retro was just run
    svc.mark_crossnode_done()    # record that a cross-node sync was just run
    svc.reset()                  # zero all counters
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RETRO_INTERVAL = 3
CROSSNODE_INTERVAL = 5
DIRTY_THRESHOLD = 3

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class LoopState(BaseModel):
    """Persisted counter state for the heartbeat loop."""

    heartbeat_count: int = 0
    last_retro: int = 0
    last_crossnode: int = 0
    last_commit_sha: str = ""
    session_start: str = ""


class TriggerSet(BaseModel):
    """Boolean flags indicating which actions should fire this tick."""

    retro: bool = False       # every RETRO_INTERVAL heartbeats since last_retro
    crossnode: bool = False   # every CROSSNODE_INTERVAL heartbeats since last_crossnode
    commit: bool = False      # more than DIRTY_THRESHOLD dirty files
    results: bool = False     # new .md files appeared in results/ since last check
    dispatch: bool = False    # idle agents detected in fleet


class FleetSnapshot(BaseModel):
    """Point-in-time fleet status parsed from the orchestrator turn file."""

    idle_count: int = 0
    busy_count: int = 0
    idle_names: str = ""
    timestamp: str = ""


class EvalResult(BaseModel):
    """Full output of a single heartbeat loop evaluation."""

    state: LoopState
    triggers: TriggerSet
    fleet: FleetSnapshot
    dirty_files: int = 0
    new_results: int = 0
    one_line: str = ""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class HeartbeatLoopService:
    """Stateful singleton service for heartbeat loop evaluation.

    Args:
        forge_root: Absolute path to the FORGE repository root.  When
            ``None`` the constructor resolves it by walking up from CWD.
    """

    def __init__(self, forge_root: Path | None = None) -> None:
        self._forge_root: Path = forge_root or self._find_forge_root()
        self._state_path: Path = self._forge_root / ".forge" / "heartbeat" / "loop_state.json"
        self._turn_path: Path = self._forge_root / ".forge" / "heartbeat" / "ORCHESTRATOR_TURN_LATEST.md"
        self._results_dir: Path = self._forge_root / ".forge" / "heartbeat" / "results"
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_forge_root() -> Path:
        """Walk up from CWD to locate the FORGE repository root."""
        current = Path.cwd()
        for _ in range(12):
            if (current / ".forge-root").exists():
                return current
            if (current / "domains.yaml").exists():
                return current
            if (current / ".git").exists():
                if any(d.is_dir() and "-" in d.name for d in current.iterdir() if d.is_dir()):
                    return current
            parent = current.parent
            if parent == current:
                break
            current = parent
        # Fall back to CWD — callers will surface errors naturally.
        return Path.cwd()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def load_state(self) -> LoopState:
        """Read ``.forge/heartbeat/loop_state.json``; return defaults if missing."""
        try:
            if self._state_path.exists():
                raw: dict[str, Any] = json.loads(self._state_path.read_text(encoding="utf-8"))
                return LoopState(**raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"HeartbeatLoopService: could not load state ({exc}); using defaults")
        return LoopState(session_start=datetime.now(UTC).isoformat())

    def save_state(self, state: LoopState) -> None:
        """Atomically write state to ``.forge/heartbeat/loop_state.json``."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"HeartbeatLoopService: save_state failed ({exc})")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Data gathering
    # ------------------------------------------------------------------

    def parse_turn_file(self) -> FleetSnapshot:
        """Parse ``ORCHESTRATOR_TURN_LATEST.md`` for idle/busy fleet counts.

        Returns:
            A :class:`FleetSnapshot` with parsed counts, or zeroed defaults
            when the file is absent or unparseable.
        """
        try:
            if not self._turn_path.exists():
                return FleetSnapshot(timestamp=datetime.now(UTC).isoformat())
            text = self._turn_path.read_text(encoding="utf-8")

            idle_count = 0
            busy_count = 0
            idle_names: list[str] = []

            # Match lines like: "| agentname | IDLE | ..." or "idle_count: 3"
            idle_m = re.search(r"idle[_\s]?count[:\s]+(\d+)", text, re.IGNORECASE)
            busy_m = re.search(r"busy[_\s]?count[:\s]+(\d+)", text, re.IGNORECASE)
            if idle_m:
                idle_count = int(idle_m.group(1))
            if busy_m:
                busy_count = int(busy_m.group(1))

            # Fallback: count table rows with IDLE / BUSY status cells
            if not idle_m and not busy_m:
                for line in text.splitlines():
                    cols = [c.strip() for c in line.split("|") if c.strip()]
                    if len(cols) >= 2:
                        status = cols[1].upper() if len(cols) > 1 else ""
                        if "IDLE" in status:
                            idle_count += 1
                            idle_names.append(cols[0])
                        elif "BUSY" in status or "ACTIVE" in status:
                            busy_count += 1

            # Collect explicit idle agent names if present
            for m in re.finditer(r"\|\s*([\w:]+)\s*\|\s*IDLE\s*\|", text, re.IGNORECASE):
                name = m.group(1).strip()
                if name not in idle_names:
                    idle_names.append(name)

            return FleetSnapshot(
                idle_count=idle_count,
                busy_count=busy_count,
                idle_names=", ".join(idle_names),
                timestamp=datetime.now(UTC).isoformat(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"HeartbeatLoopService: parse_turn_file failed ({exc})")
            return FleetSnapshot(timestamp=datetime.now(UTC).isoformat())

    def count_dirty_files(self) -> int:
        """Return the number of dirty (modified/untracked) files in the repo."""
        try:
            result = subprocess.run(
                ["git", "-C", str(self._forge_root), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
            return len(lines)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"HeartbeatLoopService: count_dirty_files failed ({exc})")
            return 0

    def count_new_results(self) -> int:
        """Count ``.md`` files in results/ newer than the state file.

        Returns:
            Number of new result files since the state file was last written.
        """
        try:
            if not self._results_dir.exists():
                return 0
            # Use state file mtime as reference; default to epoch if absent.
            ref_mtime = self._state_path.stat().st_mtime if self._state_path.exists() else 0.0
            new = [
                f for f in self._results_dir.glob("*.md")
                if f.stat().st_mtime > ref_mtime
            ]
            return len(new)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"HeartbeatLoopService: count_new_results failed ({exc})")
            return 0

    # ------------------------------------------------------------------
    # Trigger computation
    # ------------------------------------------------------------------

    def compute_triggers(
        self,
        state: LoopState,
        fleet: FleetSnapshot,
        dirty: int,
        new_results: int,
    ) -> TriggerSet:
        """Compute which actions should fire based on current metrics.

        Args:
            state:       Current persisted loop state.
            fleet:       Fleet snapshot from the turn file.
            dirty:       Number of dirty git files.
            new_results: Number of new result files.

        Returns:
            A :class:`TriggerSet` with boolean flags set.
        """
        return TriggerSet(
            retro=(state.heartbeat_count - state.last_retro) >= RETRO_INTERVAL,
            crossnode=(state.heartbeat_count - state.last_crossnode) >= CROSSNODE_INTERVAL,
            commit=dirty > DIRTY_THRESHOLD,
            results=new_results > 0,
            dispatch=fleet.idle_count > 0,
        )

    # ------------------------------------------------------------------
    # Core eval
    # ------------------------------------------------------------------

    def eval(self, increment: bool = True) -> EvalResult:
        """Run a full heartbeat loop evaluation cycle.

        Loads state, optionally increments the counter, gathers metrics,
        computes triggers, saves state, formats a one-line summary, and
        emits an SSE event to the Command Center (best-effort).

        Args:
            increment: When ``True`` (default) advance ``heartbeat_count``
                       by one before computing triggers.

        Returns:
            An :class:`EvalResult` capturing the full snapshot.
        """
        with self._lock:
            state = self.load_state()
            if not state.session_start:
                state.session_start = datetime.now(UTC).isoformat()
            if increment:
                state.heartbeat_count += 1

            fleet = self.parse_turn_file()
            dirty = self.count_dirty_files()
            new_results = self.count_new_results()
            triggers = self.compute_triggers(state, fleet, dirty, new_results)

            self.save_state(state)

        # Build human-readable one-liner
        active_triggers = [
            name for name, flag in triggers.model_dump().items() if flag
        ]
        trigger_str = ", ".join(active_triggers).upper()
        one_line = (
            f"HB#{state.heartbeat_count} | {dirty} dirty | "
            f"{new_results} new results | {fleet.idle_count} idle | "
            f"triggers: {trigger_str or 'NONE'}"
        )

        result = EvalResult(
            state=state,
            triggers=triggers,
            fleet=fleet,
            dirty_files=dirty,
            new_results=new_results,
            one_line=one_line,
        )

        logger.info(f"HeartbeatLoopService eval: {one_line}")
        self.emit_sse(result)
        return result

    # ------------------------------------------------------------------
    # State mutators
    # ------------------------------------------------------------------

    def reset(self) -> LoopState:
        """Zero all counters and persist the cleared state.

        Returns:
            The fresh :class:`LoopState` after reset.
        """
        with self._lock:
            state = LoopState(session_start=datetime.now(UTC).isoformat())
            self.save_state(state)
        logger.info("HeartbeatLoopService: state reset")
        return state

    def mark_retro_done(self, state: LoopState | None = None) -> LoopState:
        """Record that a retrospective was just completed.

        Sets ``last_retro`` to the current ``heartbeat_count`` and persists.

        Args:
            state: Existing state to update.  When ``None`` the current
                   persisted state is loaded first.

        Returns:
            The updated :class:`LoopState`.
        """
        with self._lock:
            s = state if state is not None else self.load_state()
            s.last_retro = s.heartbeat_count
            self.save_state(s)
        logger.info(f"HeartbeatLoopService: retro marked at HB#{s.heartbeat_count}")
        return s

    def mark_crossnode_done(self, state: LoopState | None = None) -> LoopState:
        """Record that a cross-node sync was just completed.

        Sets ``last_crossnode`` to the current ``heartbeat_count`` and persists.

        Args:
            state: Existing state to update.  When ``None`` the current
                   persisted state is loaded first.

        Returns:
            The updated :class:`LoopState`.
        """
        with self._lock:
            s = state if state is not None else self.load_state()
            s.last_crossnode = s.heartbeat_count
            self.save_state(s)
        logger.info(f"HeartbeatLoopService: crossnode marked at HB#{s.heartbeat_count}")
        return s

    # ------------------------------------------------------------------
    # SSE emission
    # ------------------------------------------------------------------

    def emit_sse(self, result: EvalResult) -> None:
        """Publish the eval result to the Command Center via SSE (best-effort).

        All errors are swallowed gracefully — the CC may not be running.

        Args:
            result: The :class:`EvalResult` to broadcast.
        """
        try:
            from forge_harness.webhook_server.models.sse_events import SSEEventType
            from forge_harness.webhook_server.services.event_emitter import get_event_emitter

            emitter = get_event_emitter()
            emitter.emit(
                SSEEventType("heartbeat_loop.eval"),
                data={
                    "heartbeat_count": result.state.heartbeat_count,
                    "dirty_files": result.dirty_files,
                    "new_results": result.new_results,
                    "idle_count": result.fleet.idle_count,
                    "busy_count": result.fleet.busy_count,
                    "idle_names": result.fleet.idle_names,
                    "triggers": result.triggers.model_dump(),
                    "one_line": result.one_line,
                    "timestamp": result.fleet.timestamp,
                },
                source="heartbeat-loop",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"HeartbeatLoopService.emit_sse: suppressed ({exc})")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_service_instance: HeartbeatLoopService | None = None
_service_lock = threading.Lock()


def get_heartbeat_loop_service(forge_root: Path | None = None) -> HeartbeatLoopService:
    """Return the module-level :class:`HeartbeatLoopService` singleton.

    Args:
        forge_root: Optional explicit path to the FORGE root.  Honoured
                    only on the *first* call; ignored on subsequent calls.

    Returns:
        The singleton :class:`HeartbeatLoopService` instance.
    """
    global _service_instance

    with _service_lock:
        if _service_instance is None:
            _service_instance = HeartbeatLoopService(forge_root=forge_root)
            logger.info("HeartbeatLoopService singleton created")
        return _service_instance


def reset_heartbeat_loop_service() -> None:
    """Destroy the singleton instance.

    Primarily useful in tests to ensure isolation between test cases.
    """
    global _service_instance
    with _service_lock:
        _service_instance = None
