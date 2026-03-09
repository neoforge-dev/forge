"""
Webhook Server Error Utilities
================================

Provides helpers for raising FastAPI HTTPExceptions and building JSONResponse
bodies that conform to the FORGE canonical error schema:

    {
        "code":        str,
        "message":     str,
        "next_action": str,
        "details":     dict | None
    }

All API error surfaces (task lifecycle, auth/token, contract/doctor operations)
MUST use these helpers to guarantee schema consistency between the API and CLI.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from forge_harness.error_schema import api_error_body


def forge_http_error(
    status_code: int,
    code: str,
    message: str,
    *,
    next_action: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    """Raise a FastAPI HTTPException with a structured canonical error body.

    Args:
        status_code: HTTP status code (400, 401, 403, 404, 409, 500 etc.).
        code: Machine-readable error code from forge_harness.error_schema.
        message: Human-readable description.
        next_action: Operator guidance. Auto-filled from catalogue when empty.
        details: Optional structured context.

    Raises:
        HTTPException with the canonical error envelope as detail.
    """
    body = api_error_body(code=code, message=message, next_action=next_action, details=details)
    raise HTTPException(status_code=status_code, detail=body["error"])


def forge_json_error(
    status_code: int,
    code: str,
    message: str,
    *,
    next_action: str = "",
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build a JSONResponse with a structured canonical error body (does not raise).

    Args:
        status_code: HTTP status code.
        code: Machine-readable error code.
        message: Human-readable description.
        next_action: Operator guidance. Auto-filled from catalogue when empty.
        details: Optional structured context.

    Returns:
        JSONResponse with the canonical error envelope as body.
    """
    body = api_error_body(code=code, message=message, next_action=next_action, details=details)
    return JSONResponse(status_code=status_code, content=body)


def forge_auth_error(
    status_code: int = 401,
    code: str = "AUTH_MISSING_CREDENTIALS",
    message: str = "Authentication required",
    *,
    next_action: str = "",
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build a 401/403 JSONResponse with canonical auth error body.

    Includes the required WWW-Authenticate: Bearer header for 401 responses.

    Args:
        status_code: 401 or 403.
        code: Auth error code.
        message: Human-readable description.
        next_action: Operator guidance.
        details: Optional structured context.

    Returns:
        JSONResponse with auth error body.
    """
    body = api_error_body(code=code, message=message, next_action=next_action, details=details)
    headers: dict[str, str] = {}
    if status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(status_code=status_code, content=body, headers=headers)
