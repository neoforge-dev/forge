"""User Data Store for Authentication

Provides user lookup and password validation.
In production, this would use a real database.
"""

import hashlib
import logging
import os
import secrets
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class UserInfo:
    """User data structure."""
    username: str
    password_hash: str
    salt: str
    permissions: list[str]
    is_active: bool = True

class UserStore:
    """In-memory user store populated from environment."""
    
    def __init__(self):
        self._users: dict[str, UserInfo] = {}
        self._load_from_env()
    
    def _load_from_env(self):
        """Load users from FORGE_AUTH_USERS environment variable.
        
        Format: username:password:perm1|perm2,username:password
        Note: This is insecure for production but better than accepting any password.
        Hardening step: uses SHA-256 with salt.
        """
        raw_users = os.environ.get("FORGE_AUTH_USERS")
        if not raw_users:
            # Default admin for dev
            logger.warning("FORGE_AUTH_USERS not set, using default admin:forge_admin")
            self.add_user("admin", "forge_admin", ["read", "write", "admin"])
            return
        
        for user_entry in raw_users.split(","):
            parts = user_entry.split(":")
            if len(parts) >= 2:
                username = parts[0]
                password = parts[1]
                permissions = parts[2].split("|") if len(parts) > 2 else ["read", "write"]
                
                # Special case: if username is 'admin', ensure it has 'admin' permission
                if username == "admin" and "admin" not in permissions:
                    permissions.append("admin")
                    
                self.add_user(username, password, permissions)
    
    def add_user(self, username: str, password: str, permissions: list[str] | None = None):
        """Add a user with salted hash."""
        salt = secrets.token_hex(16)
        password_hash = self._hash_password(password, salt)
        self._users[username] = UserInfo(
            username=username,
            password_hash=password_hash,
            salt=salt,
            permissions=permissions or ["read"]
        )
    
    def _hash_password(self, password: str, salt: str) -> str:
        """Hash password with salt."""
        return hashlib.sha256((password + salt).encode()).hexdigest()
    
    def authenticate(self, username: str, password: str) -> UserInfo | None:
        """Authenticate user."""
        user = self._users.get(username)
        if not user or not user.is_active:
            return None
        
        computed_hash = self._hash_password(password, user.salt)
        if secrets.compare_digest(computed_hash, user.password_hash):
            return user
        return None

    def get_user(self, username: str) -> UserInfo | None:
        """Get user info."""
        return self._users.get(username)

# Singleton
_user_store = UserStore()

def get_user_store() -> UserStore:
    """Get the global user store."""
    return _user_store
