"""tmux dispatcher for OpenClaw fleet automation.

Minimal scaffolding for:
- Listing tmux sessions
- Sending commands to a session
- Capturing recent output
"""

from __future__ import annotations

import shlex
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class TmuxSession:
    name: str
    created_at: str
    windows: int
    attached: bool


@dataclass(frozen=True)
class DispatchResult:
    session: str
    command: str
    sent: bool
    error: str | None = None


def _run_tmux(args: Iterable[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def list_sessions() -> list[TmuxSession]:
    result = _run_tmux(
        [
            "list-sessions",
            "-F",
            "#{session_name}|#{session_created}|#{session_windows}|#{session_attached}",
        ]
    )
    if result.returncode != 0:
        return []
    sessions: list[TmuxSession] = []
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) != 4:
            continue
        name, created, windows, attached = parts
        try:
            created_dt = datetime.fromtimestamp(int(created), tz=UTC).isoformat()
        except ValueError:
            created_dt = datetime.now(UTC).isoformat()
        sessions.append(
            TmuxSession(
                name=name,
                created_at=created_dt,
                windows=int(windows) if windows.isdigit() else 0,
                attached=attached == "1",
            )
        )
    return sessions


def resolve_session(target: str) -> str | None:
    target = target.strip()
    for session in list_sessions():
        if session.name == target:
            return session.name
    return None


def list_windows(session: str) -> list[str]:
    """List window names in a tmux session."""
    result = _run_tmux(["list-windows", "-t", session, "-F", "#{window_name}"])
    if result.returncode != 0:
        return []
    return [w for w in result.stdout.strip().splitlines() if w.strip()]


def resolve_target(agent: str) -> str | None:
    """Resolve an agent name to a tmux target (session:window).

    Accepts:
      - "glm"        → "forge:glm"   (window lookup in forge session)
      - "forge:glm"  → "forge:glm"   (pass-through)
      - "forge"      → "forge"       (session-level)
    """
    agent = agent.strip()
    # Already a session:window target
    if ":" in agent:
        session, window = agent.split(":", 1)
        if resolve_session(session):
            return agent
        return None
    # Exact session match
    if resolve_session(agent):
        return agent
    # Search for agent as a window name in known sessions
    for session in list_sessions():
        windows = list_windows(session.name)
        if agent in windows:
            return f"{session.name}:{agent}"
    return None


def send_keys(session: str, command: str, enter: bool = True, literal: bool = True) -> DispatchResult:
    if not session:
        return DispatchResult(session=session, command=command, sent=False, error="missing session")
    args = ["send-keys", "-t", session]
    if literal:
        args.append("-l")
    args.append(command)
    result = _run_tmux(args)
    if result.returncode != 0:
        return DispatchResult(
            session=session,
            command=command,
            sent=False,
            error=result.stderr.strip() or "tmux send-keys failed",
        )
    if enter:
        time.sleep(0.15)  # prevent race — let text register before Enter
        _run_tmux(["send-keys", "-t", session, "Enter"])
    return DispatchResult(session=session, command=command, sent=True)


def dispatch_command(session: str, command: str) -> DispatchResult:
    """Dispatch a command to a tmux session.

    Uses a safe send-keys pattern: send full command, then Enter.
    """
    safe_command = shlex.quote(command) if "\n" in command else command
    return send_keys(session, safe_command, enter=True)


def capture_tail(session: str, lines: int = 20) -> str:
    result = _run_tmux(["capture-pane", "-t", session, "-p", "-S", f"-{lines}"])
    if result.returncode != 0:
        return ""
    return result.stdout
