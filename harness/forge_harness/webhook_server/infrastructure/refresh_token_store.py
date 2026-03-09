"""File-Based Refresh Token Store

Persists refresh token state to .forge/auth/refresh_tokens.json.
Used for refresh token rotation — tracks issued/revoked refresh token JTIs
and token families so that single-use enforcement survives process restarts.

Schema: {
    "revoked_jtis": ["jti1", "jti2", ...],
    "revoked_families": ["family_id1", ...],
    "updated_at": "<ISO timestamp>"
}
"""

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_STORE_PATH = Path(os.environ.get("FORGE_ROOT", "")) / ".forge" / "auth" / "refresh_tokens.json"

# Module-level lock for thread-safe file access
_file_lock = threading.Lock()


def _get_store_path() -> Path:
    """Resolve store path, preferring FORGE_ROOT env var."""
    forge_root = os.environ.get("FORGE_ROOT")
    if forge_root:
        return Path(forge_root) / ".forge" / "auth" / "refresh_tokens.json"
    # Walk up from this file to find FORGE repo root (contains .forge dir)
    current = Path(__file__).resolve()
    for _ in range(10):
        current = current.parent
        if (current / ".forge").is_dir():
            return current / ".forge" / "auth" / "refresh_tokens.json"
    # Fallback: use cwd
    return Path.cwd() / ".forge" / "auth" / "refresh_tokens.json"


def _load_store(store_path: Path) -> dict:
    """Load token store from disk. Returns empty store on missing/corrupt file."""
    if not store_path.exists():
        return {"revoked_jtis": [], "revoked_families": [], "updated_at": ""}
    try:
        with open(store_path) as f:
            data = json.load(f)
        # Ensure required keys exist
        if "revoked_jtis" not in data:
            data["revoked_jtis"] = []
        if "revoked_families" not in data:
            data["revoked_families"] = []
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load refresh token store from {store_path}: {e}. Starting fresh.")
        return {"revoked_jtis": [], "revoked_families": [], "updated_at": ""}


def _save_store(store_path: Path, data: dict) -> None:
    """Persist token store to disk atomically."""
    data["updated_at"] = datetime.now(UTC).isoformat()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to .tmp then rename
    tmp_path = store_path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        tmp_path.replace(store_path)
    except OSError as e:
        logger.error(f"Failed to save refresh token store to {store_path}: {e}")
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


class FileRefreshTokenStore:
    """File-backed store for tracking revoked refresh token JTIs and families.

    Thread-safe. File is read on every check and written on every mutation
    to ensure consistency across process restarts without requiring a daemon.

    Attributes:
        store_path: Path to the JSON store file
    """

    def __init__(self, store_path: Path | None = None) -> None:
        self.store_path = store_path or _get_store_path()

    def revoke_jti(self, jti: str) -> None:
        """Mark a token JTI as revoked (single-use enforcement).

        Args:
            jti: JWT ID to revoke
        """
        with _file_lock:
            data = _load_store(self.store_path)
            if jti not in data["revoked_jtis"]:
                data["revoked_jtis"].append(jti)
                _save_store(self.store_path, data)
                logger.debug(f"Revoked refresh token jti={jti}")

    def revoke_family(self, family_id: str) -> None:
        """Revoke an entire token family (reuse/replay attack response).

        Args:
            family_id: Token family ID to invalidate
        """
        with _file_lock:
            data = _load_store(self.store_path)
            if family_id not in data["revoked_families"]:
                data["revoked_families"].append(family_id)
                _save_store(self.store_path, data)
                logger.warning(f"Revoked token family {family_id} (possible replay attack)")

    def is_jti_revoked(self, jti: str) -> bool:
        """Check if a token JTI has been revoked.

        Args:
            jti: JWT ID to check

        Returns:
            True if the JTI has been revoked
        """
        with _file_lock:
            data = _load_store(self.store_path)
        return jti in data["revoked_jtis"]

    def is_family_revoked(self, family_id: str) -> bool:
        """Check if a token family has been revoked.

        Args:
            family_id: Token family ID to check

        Returns:
            True if the family has been revoked
        """
        with _file_lock:
            data = _load_store(self.store_path)
        return family_id in data["revoked_families"]

    def clear(self) -> None:
        """Clear all revocation records. For testing only."""
        with _file_lock:
            _save_store(self.store_path, {"revoked_jtis": [], "revoked_families": [], "updated_at": ""})


# Module-level singleton — shared across all requests in the process
_store_instance: FileRefreshTokenStore | None = None


def get_refresh_token_store(store_path: Path | None = None) -> FileRefreshTokenStore:
    """Get the global FileRefreshTokenStore instance.

    Args:
        store_path: Override path (used in tests). If None, uses resolved default.

    Returns:
        Singleton FileRefreshTokenStore
    """
    global _store_instance
    if _store_instance is None or store_path is not None:
        _store_instance = FileRefreshTokenStore(store_path=store_path)
    return _store_instance


def reset_store_singleton() -> None:
    """Reset the singleton (for testing only)."""
    global _store_instance
    _store_instance = None
