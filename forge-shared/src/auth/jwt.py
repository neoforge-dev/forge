"""
Service Specification: Authentication (JWT)

This file defines the interface for the standardized FORGE JWT authentication service.
Implementation: forge_shared.auth.jwt
"""

from typing import Optional, List, Any
from pydantic import BaseModel

class JWTConfig(BaseModel):
    """Configuration for JWT service."""
    secret_key: str
    public_key: Optional[str] = None
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

class JWTAuthInterface:
    """Standardized JWT authentication interface."""
    
    def create_access_token(self, user_id: str, email: str, **kwargs) -> str:
        """Create a standardized access token."""
        pass

    def create_refresh_token(self, user_id: str, **kwargs) -> str:
        """Create a minimal refresh token."""
        pass

    def decode_token(self, token: str) -> Any:
        """Decode and validate a JWT token."""
        pass

    def refresh_access_token(self, refresh_token: str) -> tuple:
        """Generate a new access token from a valid refresh token."""
        pass
