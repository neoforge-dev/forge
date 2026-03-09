"""SSE session token store for Phase 1.3.

Short-lived session tokens for SSE connections. Prevents long-lived
Bearer token in query param (EventSource cannot set headers).
"""

import secrets
import time
from dataclasses import dataclass, field


@dataclass
class SessionEntry:
    """A session token with expiry."""

    token: str
    created_at: float = field(default_factory=time.time)
    ttl_seconds: int = 300  # 5 minutes

    def is_valid(self) -> bool:
        return (time.time() - self.created_at) < self.ttl_seconds


class SSESessionStore:
    """In-memory store for short-lived SSE session tokens."""

    def __init__(self, ttl_seconds: int = 300):
        self._sessions: dict[str, SessionEntry] = {}
        self._ttl = ttl_seconds

    def create(self, bearer_token: str) -> str:
        """Create a session token. Caller must verify bearer_token first."""
        session_token = secrets.token_urlsafe(32)
        self._sessions[session_token] = SessionEntry(
            token=session_token, ttl_seconds=self._ttl
        )
        return session_token

    def validate(self, token: str) -> bool:
        """Check if token is a valid (non-expired) session token."""
        entry = self._sessions.get(token)
        if entry is None:
            return False
        if not entry.is_valid():
            del self._sessions[token]
            return False
        return True

    def revoke(self, token: str) -> None:
        """Remove a session token."""
        self._sessions.pop(token, None)

    def cleanup_expired(self) -> int:
        """Remove expired entries. Returns count removed."""
        time.time()
        expired = [k for k, v in self._sessions.items() if not v.is_valid()]
        for k in expired:
            del self._sessions[k]
        return len(expired)


# Module-level singleton for use by create_app
_sse_session_store: SSESessionStore | None = None


def get_sse_session_store() -> SSESessionStore:
    """Get or create the SSE session store singleton."""
    global _sse_session_store
    if _sse_session_store is None:
        _sse_session_store = SSESessionStore(ttl_seconds=300)
    return _sse_session_store
