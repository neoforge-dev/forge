"""
Authentication Infrastructure
=============================

Bearer token authentication for webhook server API endpoints.
Extracted from webhook_server.py for better modularity.

Implements:
- AuthResult: Authentication result enum
- AuthConfig: Authentication configuration from environment
- verify_bearer_token: Token verification with localhost support
"""

import logging
import os
import secrets
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class AuthResult(Enum):
    """Authentication result."""

    SUCCESS = "success"
    MISSING_TOKEN = "missing_token"
    INVALID_TOKEN = "invalid_token"


@dataclass
class AuthConfig:
    """Authentication configuration.

    Attributes:
        bearer_token: The expected Bearer token for API authentication
        require_auth: Whether authentication is required (default: True for production)
        allow_localhost: Allow unauthenticated requests from localhost (dev mode)
        sse_require_session_token: When True, SSE rejects Bearer token in query (Phase 1.3)
    """

    bearer_token: str | None = None
    require_auth: bool = True
    allow_localhost: bool = False
    sse_require_session_token: bool = False

    @classmethod
    def from_env(cls) -> "AuthConfig":
        """Create AuthConfig from environment variables.

        Note: allow_localhost defaults to False (P0-6 security). Set
        FORGE_WEBHOOK_ALLOW_LOCALHOST=true explicitly for local dev.
        """
        bearer_token = os.environ.get("FORGE_WEBHOOK_TOKEN")
        # Default allow_localhost to False; require explicit ALLOW_LOCALHOST=true for dev
        default_allow_localhost = "false"
        sse_require = os.environ.get("FORGE_SSE_REQUIRE_SESSION_TOKEN", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        return cls(
            bearer_token=bearer_token,
            require_auth=os.environ.get("FORGE_WEBHOOK_REQUIRE_AUTH", "true").lower()
            in ("true", "1", "yes"),
            allow_localhost=os.environ.get(
                "FORGE_WEBHOOK_ALLOW_LOCALHOST", default_allow_localhost
            ).lower()
            in ("true", "1", "yes"),
            sse_require_session_token=sse_require,
        )

    def generate_token(self) -> str:
        """Generate a new secure token."""
        return secrets.token_urlsafe(32)


def _is_localhost_value(value: str) -> bool:
    """Return True if value is a localhost hostname/IP, with or without port."""
    if not value:
        return False
    v = value.strip().lower()
    # Handle bracketed IPv6 like [::1]:8000
    if v.startswith("[") and "]" in v:
        host = v[1 : v.index("]")]
    else:
        host = v.split(":", 1)[0]
    return host in {"127.0.0.1", "localhost", "::1", "testclient"}


def _is_localhost_request(client_host: str | None, host_header: str | None) -> bool:
    """Return True if either client_host or host_header indicate localhost."""
    return _is_localhost_value(client_host or "") or _is_localhost_value(host_header or "")


def verify_bearer_token(
    authorization: str | None, config: AuthConfig, client_host: str | None = None
) -> AuthResult:
    """Verify Bearer token authentication.

    Args:
        authorization: Authorization header value
        config: Authentication configuration
        client_host: Client IP address (for localhost check)

    Returns:
        AuthResult indicating success or failure reason
    """
    # Allow localhost only when explicitly configured
    if config.allow_localhost and _is_localhost_value(client_host or ""):
        return AuthResult.SUCCESS

    # Skip auth if not required or no token configured
    if not config.require_auth or not config.bearer_token:
        return AuthResult.SUCCESS

    # Check for missing authorization header
    if not authorization:
        return AuthResult.MISSING_TOKEN

    # Validate Bearer token format
    if not authorization.startswith("Bearer "):
        _log_auth_attempt(client_host, "invalid_format", "Authorization header not Bearer format")
        return AuthResult.INVALID_TOKEN

    token = authorization[7:]  # Remove "Bearer " prefix

    # Compare tokens securely
    if secrets.compare_digest(token, config.bearer_token):
        return AuthResult.SUCCESS

    _log_auth_attempt(client_host, "invalid_token", "Token mismatch")
    return AuthResult.INVALID_TOKEN


def _log_auth_attempt(
    client_host: str | None,
    result: str,
    details: str,
) -> None:
    """Log authentication attempt for audit purposes.

    Args:
        client_host: Client IP address
        result: Result type (invalid_format, invalid_token)
        details: Additional details
    """
    client_ip = client_host or "unknown"
    logger.warning(f"AUTH_ATTEMPT: client_ip={client_ip} result={result} details={details}")
