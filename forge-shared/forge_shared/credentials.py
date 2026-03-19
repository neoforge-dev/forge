"""
Credential-optional design pattern
==================================

Graceful handling of missing credentials with mode-based behavior.

Usage:
    from forge_shared.credentials import get_credential, CredentialMode

    # Required - fails if missing
    token = get_credential("FORGE_WEBHOOK_TOKEN", mode=CredentialMode.REQUIRED)

    # Recommended - warns if missing, returns default
    secret = get_credential(
        "FORGE_JWT_SECRET",
        mode=CredentialMode.RECOMMENDED,
        default_factory=lambda: secrets.token_urlsafe(32),
        warning_msg="FORGE_JWT_SECRET not set, generating temporary secret"
    )

    # Optional - silent if missing
    api_key = get_credential("STRIPE_API_KEY", mode=CredentialMode.OPTIONAL)
"""

import os
import secrets
import logging
from enum import Enum
from typing import Callable, Any

logger = logging.getLogger(__name__)


class CredentialMode(Enum):
    """Credential requirement mode."""
    REQUIRED = "required"       # Must be set, fail if missing
    RECOMMENDED = "recommended"  # Should be set, warn if missing
    OPTIONAL = "optional"       # Nice to have, silent if missing


class CredentialError(RuntimeError):
    """Raised when a required credential is missing."""
    pass


def get_forge_environment() -> str:
    """Detect running environment."""
    return os.environ.get("FORGE_ENV", "development")


def is_production() -> bool:
    """Check if running in production environment."""
    return get_forge_environment() == "production"


def get_credential(
    name: str,
    mode: CredentialMode,
    default_factory: Callable[[], Any] | None = None,
    default: Any = None,
    warning_msg: str | None = None,
    min_length: int | None = None,
) -> Any:
    """Get credential with mode-based behavior.

    Args:
        name: Environment variable name
        mode: Credential mode (REQUIRED/RECOMMENDED/OPTIONAL)
        default_factory: Callable to generate default value
        default: Static default value
        warning_msg: Custom warning message
        min_length: Minimum length requirement

    Returns:
        Credential value or default

    Raises:
        CredentialError: If REQUIRED and missing

    Examples:
        # Required credential
        token = get_credential("FORGE_WEBHOOK_TOKEN", mode=CredentialMode.REQUIRED)

        # Recommended with auto-generate
        secret = get_credential(
            "FORGE_JWT_SECRET",
            mode=CredentialMode.RECOMMENDED,
            default_factory=lambda: secrets.token_urlsafe(32)
        )

        # Optional
        api_key = get_credential("STRIPE_API_KEY", mode=CredentialMode.OPTIONAL)
    """
    value = os.environ.get(name)

    # Check if present and valid
    if value:
        if min_length and len(value) < min_length:
            logger.warning(f"{name} is set but too short (need >={min_length} chars)")
            if mode == CredentialMode.REQUIRED and is_production():
                raise CredentialError(f"{name} too short (need >={min_length} chars)")
        return value

    # Missing credential
    if mode == CredentialMode.REQUIRED:
        raise CredentialError(f"{name} is required but not set")

    if mode == CredentialMode.RECOMMENDED:
        msg = warning_msg or f"{name} not set, using fallback"
        logger.warning(msg)

    # Return default
    if default_factory:
        return default_factory()
    return default


def check_credential(name: str, min_length: int | None = None) -> bool:
    """Check if credential is present and valid.

    Args:
        name: Environment variable name
        min_length: Minimum length requirement

    Returns:
        True if credential is valid, False otherwise
    """
    value = os.environ.get(name)
    if not value:
        return False
    if min_length and len(value) < min_length:
        return False
    return True


def get_credential_info(name: str) -> dict:
    """Get credential information without exposing value.

    Args:
        name: Environment variable name

    Returns:
        Dict with status information
    """
    value = os.environ.get(name)
    return {
        "name": name,
        "set": bool(value),
        "length": len(value) if value else 0,
        "preview": f"{value[:4]}...{value[-4:]}" if value and len(value) > 8 else "***",
    }


# Pre-defined credential configurations
CREDENTIAL_CONFIGS = {
    "FORGE_WEBHOOK_TOKEN": {
        "mode": CredentialMode.REQUIRED,
        "min_length": 16,
        "description": "Authentication token for webhook API",
    },
    "FORGE_JWT_SECRET": {
        "mode": CredentialMode.RECOMMENDED,
        "min_length": 32,
        "default_factory": lambda: secrets.token_urlsafe(32),
        "description": "JWT signing secret",
    },
    "COMMAND_CENTER_URL": {
        "mode": CredentialMode.REQUIRED,
        "description": "Command Center API URL",
    },
    "FORGE_NODE_ID": {
        "mode": CredentialMode.RECOMMENDED,
        "default_factory": lambda: os.environ.get("HOSTNAME", "unknown"),
        "description": "Node identifier for fleet",
    },
    "STRIPE_API_KEY": {
        "mode": CredentialMode.OPTIONAL,
        "description": "Stripe API key for payments",
    },
    "POSTHOG_API_KEY": {
        "mode": CredentialMode.OPTIONAL,
        "description": "PostHog analytics API key",
    },
    "SENTRY_DSN": {
        "mode": CredentialMode.OPTIONAL,
        "description": "Sentry error tracking DSN",
    },
    "SLACK_WEBHOOK_URL": {
        "mode": CredentialMode.OPTIONAL,
        "description": "Slack webhook for notifications",
    },
    "RAILWAY_TOKEN": {
        "mode": CredentialMode.OPTIONAL,
        "description": "Railway deployment token (lead nodes only)",
    },
    "GITHUB_TOKEN": {
        "mode": CredentialMode.OPTIONAL,
        "description": "GitHub API token",
    },
}


def get_configured_credential(name: str) -> Any:
    """Get credential using pre-defined configuration.

    Args:
        name: Credential name from CREDENTIAL_CONFIGS

    Returns:
        Credential value

    Raises:
        CredentialError: If REQUIRED and missing
        KeyError: If credential not in configs
    """
    if name not in CREDENTIAL_CONFIGS:
        raise KeyError(f"Unknown credential: {name}")

    config = CREDENTIAL_CONFIGS[name]
    return get_credential(
        name,
        mode=config["mode"],
        default_factory=config.get("default_factory"),
        min_length=config.get("min_length"),
        warning_msg=f"{name} not set ({config['description']})",
    )


def check_all_credentials() -> dict:
    """Check all known credentials.

    Returns:
        Dict with status for each credential
    """
    results = {}

    for name, config in CREDENTIAL_CONFIGS.items():
        value = os.environ.get(name)
        min_length = config.get("min_length", 0)

        status = "ok"
        if not value:
            if config["mode"] == CredentialMode.REQUIRED:
                status = "missing_required"
            elif config["mode"] == CredentialMode.RECOMMENDED:
                status = "missing_recommended"
            else:
                status = "missing_optional"
        elif min_length and len(value) < min_length:
            status = "too_short"

        results[name] = {
            "status": status,
            "mode": config["mode"].value,
            "description": config["description"],
        }

    return results
