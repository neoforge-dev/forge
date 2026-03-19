"""Error helpers for FORGE CLI JSON envelopes."""

from typing import Any


def build_error(message: str, code: str | None = None, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a consistent error payload."""
    return {
        "message": message,
        "code": code,
        "details": details or {},
    }

