"""API Key Authentication Infrastructure

Provides API key generation, storage, and validation.
For non-interactive clients like CLI and automated agents.
"""

import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)

class APIKeyResult(Enum):
    """API Key authentication result."""
    SUCCESS = "success"
    MISSING_KEY = "missing_key"
    INVALID_KEY = "invalid_key"
    EXPIRED_KEY = "expired_key"
    REVOKED_KEY = "revoked_key"

@dataclass
class APIKeyPayload:
    """API Key data structure."""
    key_id: str
    owner: str
    permissions: list[str]
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    is_active: bool = True

def generate_api_key() -> str:
    """Generate a new secure API key.
    
    Format: forge_key_<random_string>
    """
    return f"forge_key_{secrets.token_urlsafe(32)}"

def get_key_hash(key: str) -> str:
    """Get a hash of the API key for secure storage."""
    return hashlib.sha256(key.encode()).hexdigest()

# Simple in-memory store for now (can be moved to StateStore later)
# In production, this should be in SQLite or Redis
_api_keys: dict[str, APIKeyPayload] = {}

def register_api_key(
    key: str,
    owner: str,
    permissions: list[str] | None = None,
    expires_in_days: int | None = None
) -> None:
    """Register an API key."""
    key_hash = get_key_hash(key)
    expires_at = None
    if expires_in_days:
        expires_at = datetime.now(UTC).replace(microsecond=0) + \
                     timedelta(days=expires_in_days)
    
    _api_keys[key_hash] = APIKeyPayload(
        key_id=key_hash[:12],
        owner=owner,
        permissions=permissions or ["read"],
        created_at=datetime.now(UTC),
        expires_at=expires_at,
        is_active=True
    )
    logger.info(f"Registered API key for {owner} (id={key_hash[:12]})")

def verify_api_key(key: str) -> tuple[APIKeyResult, APIKeyPayload | None]:
    """Verify an API key and return its payload."""
    if not key:
        return APIKeyResult.MISSING_KEY, None
    
    key_hash = get_key_hash(key)
    payload = _api_keys.get(key_hash)
    
    if not payload:
        # Fallback to environment variable for a master key if needed
        master_key = os.environ.get("FORGE_MASTER_API_KEY")
        if master_key and secrets.compare_digest(key, master_key):
            return APIKeyResult.SUCCESS, APIKeyPayload(
                key_id="master",
                owner="admin",
                permissions=["admin", "read", "write"],
                created_at=datetime.now(UTC),
                is_active=True
            )
        return APIKeyResult.INVALID_KEY, None
    
    if not payload.is_active:
        return APIKeyResult.REVOKED_KEY, None
    
    if payload.expires_at and payload.expires_at < datetime.now(UTC):
        return APIKeyResult.EXPIRED_KEY, None
    
    # Update last used timestamp
    payload.last_used_at = datetime.now(UTC)
    
    return APIKeyResult.SUCCESS, payload

def revoke_api_key(key_hash: str) -> bool:
    """Revoke an API key by its hash (or prefix)."""
    if key_hash in _api_keys:
        _api_keys[key_hash].is_active = False
        return True
    return False
