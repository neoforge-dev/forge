"""
SessionTracker - Monitor and track spawned agent tmux sessions.

Provides:
- List all active tmux sessions in the FORGE fleet
- Track session metadata (start time, domain, project, task)
- Capture last N lines of terminal output
- Resource monitoring (if available)
"""

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class AgentSession:
    """Represents an active agent session in tmux."""

    session_name: str  # e.g., "forge:cursor"
    window_name: str  # e.g., "cursor"
    agent_type: str  # claude, pi, amp, opencode, etc.
    domain: str | None = None
    project: str | None = None
    current_task: str | None = None
    started_at: str | None = None
    last_activity: str | None = None
    terminal_output: str | None = None  # Last 4000 chars (~50 lines)
    status: str = "unknown"  # active, idle, waiting, error


class SessionTracker:
    """Track and monitor spawned tmux agent sessions."""

    def __init__(self, session_prefix: str = "forge"):
        self.session_prefix = session_prefix
        self.sessions_dir = Path.cwd() / ".forge/sessions"
        self.sessions_dir.mkdir(exist_ok=True)

    def list_tmux_sessions(self) -> list[str]:
        """List all tmux sessions matching prefix."""
        try:
            result = subprocess.run(
                ["tmux", "list-windows", "-t", self.session_prefix, "-F", "#{window_name}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return [w.strip() for w in result.stdout.strip().split("\n") if w.strip()]
        except subprocess.TimeoutExpired:
            pass
        except FileNotFoundError:
            # tmux not installed
            pass
        except Exception:
            pass
        return []

    def get_session_output(self, window: str, lines: int = 50) -> str:
        """Capture last N lines from tmux pane."""
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", f"{self.session_prefix}:{window}", "-p"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                output_lines = result.stdout.strip().split("\n")
                return "\n".join(output_lines[-lines:])
        except subprocess.TimeoutExpired:
            pass
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return ""

    def get_all_sessions(self) -> list[AgentSession]:
        """Get all active agent sessions with metadata."""
        sessions = []
        for window in self.list_tmux_sessions():
            # Detect agent type from window name or output
            agent_type = self._detect_agent_type(window)
            output = self.get_session_output(window)

            # Load saved metadata if exists
            metadata = self._load_session_metadata(window)

            # Get last activity from metadata or fall back to session file mtime
            last_activity = metadata.get("last_activity")
            if not last_activity:
                meta_file = self.sessions_dir / f"{window}.json"
                if meta_file.exists():
                    mtime = meta_file.stat().st_mtime
                    last_activity = datetime.fromtimestamp(mtime).isoformat()
                else:
                    # Fall back to current time if no metadata exists
                    from datetime import UTC

                    last_activity = datetime.now(UTC).isoformat()

            session = AgentSession(
                session_name=f"{self.session_prefix}:{window}",
                window_name=window,
                agent_type=agent_type,
                domain=metadata.get("domain"),
                project=metadata.get("project"),
                current_task=metadata.get("task"),
                started_at=metadata.get("started_at"),
                last_activity=last_activity,
                terminal_output=output[-4000:] if output else None,
                status=self._detect_status(output),
            )
            sessions.append(session)
        return sessions

    def get_session(self, window: str) -> AgentSession | None:
        """Get a specific session by window name."""
        windows = self.list_tmux_sessions()
        if window not in windows:
            return None

        agent_type = self._detect_agent_type(window)
        output = self.get_session_output(window)
        metadata = self._load_session_metadata(window)

        # Get last activity from metadata or fall back to session file mtime
        last_activity = metadata.get("last_activity")
        if not last_activity:
            meta_file = self.sessions_dir / f"{window}.json"
            if meta_file.exists():
                mtime = meta_file.stat().st_mtime
                last_activity = datetime.fromtimestamp(mtime).isoformat()
            else:
                # Fall back to current time if no metadata exists
                from datetime import UTC

                last_activity = datetime.now(UTC).isoformat()

        return AgentSession(
            session_name=f"{self.session_prefix}:{window}",
            window_name=window,
            agent_type=agent_type,
            domain=metadata.get("domain"),
            project=metadata.get("project"),
            current_task=metadata.get("task"),
            started_at=metadata.get("started_at"),
            last_activity=last_activity,
            terminal_output=output[-4000:] if output else None,
            status=self._detect_status(output),
        )

    def _detect_agent_type(self, window: str) -> str:
        """Detect agent type from window name."""
        type_map = {
            "claude": "claude",
            "cursor": "claude",
            "tech": "claude",
            "game": "claude",
            "pi": "pi",
            "amp": "amp",
            "opencode": "opencode",
            "codex": "codex",
            "gemini": "gemini",
        }
        # Check if window name starts with or contains a known type
        window_lower = window.lower()
        for key, value in type_map.items():
            if key in window_lower:
                return value
        return "unknown"

    def _detect_status(self, output: str) -> str:
        """Detect agent status from terminal output."""
        if not output:
            return "unknown"
        lower = output.lower()

        # Check for error indicators
        if "error" in lower or "failed" in lower or "traceback" in lower:
            return "error"

        # Check for waiting/idle indicators
        if "waiting" in lower or "idle" in lower or "paused" in lower:
            return "idle"

        # Check for active indicators
        if any(
            indicator in lower
            for indicator in [
                "generating",
                "running",
                "processing",
                "thinking",
                "writing",
                "reading",
                "executing",
                "working",
            ]
        ):
            return "active"

        # Check for completion indicators
        if "completed" in lower or "finished" in lower or "done" in lower:
            return "completed"

        return "idle"

    def _load_session_metadata(self, window: str) -> dict[str, Any]:
        """Load saved session metadata."""
        meta_file = self.sessions_dir / f"{window}.json"
        if meta_file.exists():
            try:
                return json.loads(meta_file.read_text())
            except json.JSONDecodeError:
                pass
            except Exception:
                pass
        return {}

    def save_session_metadata(self, window: str, metadata: dict[str, Any]) -> None:
        """Save session metadata for a window."""
        meta_file = self.sessions_dir / f"{window}.json"
        # Merge with existing metadata
        existing = self._load_session_metadata(window)
        existing.update(metadata)
        # Ensure started_at is set
        if "started_at" not in existing:
            existing["started_at"] = datetime.now().isoformat()
        meta_file.write_text(json.dumps(existing, indent=2))

    def clear_session_metadata(self, window: str) -> bool:
        """Clear metadata for a session."""
        meta_file = self.sessions_dir / f"{window}.json"
        if meta_file.exists():
            meta_file.unlink()
            return True
        return False

    def to_dict(self, sessions: list[AgentSession]) -> list[dict[str, Any]]:
        """Convert sessions to dict for API response."""
        return [asdict(s) for s in sessions]

    def get_sessions_summary(self) -> dict[str, Any]:
        """Get a summary of all sessions."""
        sessions = self.get_all_sessions()

        status_counts = {"active": 0, "idle": 0, "error": 0, "unknown": 0, "completed": 0}
        type_counts: dict[str, int] = {}

        for session in sessions:
            status_counts[session.status] = status_counts.get(session.status, 0) + 1
            type_counts[session.agent_type] = type_counts.get(session.agent_type, 0) + 1

        return {
            "total": len(sessions),
            "by_status": status_counts,
            "by_type": type_counts,
            "sessions": self.to_dict(sessions),
        }


# Singleton instance
_tracker: SessionTracker | None = None


def get_session_tracker() -> SessionTracker:
    """Get the singleton session tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = SessionTracker()
    return _tracker
