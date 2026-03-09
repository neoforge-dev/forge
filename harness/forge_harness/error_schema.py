"""
FORGE Canonical Error Schema
=============================

Defines the single, authoritative structured-error contract used by both
the webhook-server API and the CLI.  Every error surface in the control
plane MUST produce a dict/object that conforms to this schema.

Schema
------
{
    "code":        str  — machine-readable, SCREAMING_SNAKE_CASE identifier
    "message":     str  — concise human-readable description
    "next_action": str  — what the operator should do next
    "details":     dict | None  — optional free-form context (IDs, conflict info …)
}

Design goals:
- Zero silent failures: every error MUST carry a ``next_action``.
- API and CLI share exactly the same schema; the CLI renderer converts it to
  human-readable text (default) or JSON (--json flag).
- All error codes live here so they remain consistent across surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Canonical error codes
# ---------------------------------------------------------------------------

# Auth / Token errors
AUTH_MISSING_CREDENTIALS = "AUTH_MISSING_CREDENTIALS"
AUTH_INVALID_TOKEN = "AUTH_INVALID_TOKEN"
AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
AUTH_PERMISSION_DENIED = "AUTH_PERMISSION_DENIED"

# Task lifecycle errors
TASK_NOT_FOUND = "TASK_NOT_FOUND"
TASK_VALIDATION_ERROR = "TASK_VALIDATION_ERROR"
TASK_CREATION_FAILED = "TASK_CREATION_FAILED"
TASK_UPDATE_FAILED = "TASK_UPDATE_FAILED"
TASK_DELETE_FAILED = "TASK_DELETE_FAILED"

# Lease errors (already exist on TaskLeaseError; listed here for documentation)
LEASE_INVALID = "INVALID_LEASE"
LEASE_ALREADY_OWNED = "LEASE_ALREADY_OWNED"
LEASE_NOT_FOUND = "LEASE_NOT_FOUND"
LEASE_EXPIRED = "LEASE_EXPIRED"
LEASE_OWNER_MISMATCH = "LEASE_OWNER_MISMATCH"
LEASE_PATH_LOCK_CONFLICT = "PATH_LOCK_CONFLICT"
LEASE_PATH_LOCK_IMMUTABLE = "PATH_LOCK_IMMUTABLE"

# Dispatch / Agent errors
AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
AGENT_NOT_AVAILABLE = "AGENT_NOT_AVAILABLE"
DISPATCH_FAILED = "DISPATCH_FAILED"

# Contract / Doctor operational errors
CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
DOCTOR_CHECK_FAILED = "DOCTOR_CHECK_FAILED"
DOCTOR_FIX_FAILED = "DOCTOR_FIX_FAILED"

# Generic / fallback
UNKNOWN_ERROR = "UNKNOWN_ERROR"
VALIDATION_ERROR = "VALIDATION_ERROR"
NOT_FOUND = "NOT_FOUND"
SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"

# ---------------------------------------------------------------------------
# next_action catalogue
# Keeps guidance strings close to the codes so they stay in sync.
# ---------------------------------------------------------------------------

_NEXT_ACTIONS: dict[str, str] = {
    AUTH_MISSING_CREDENTIALS: (
        "Provide a Bearer token via the Authorization header or set "
        "FORGE_WEBHOOK_TOKEN / FORGE_JWT_SECRET and re-run."
    ),
    AUTH_INVALID_TOKEN: (
        "Verify the token value matches FORGE_WEBHOOK_TOKEN or obtain a "
        "fresh JWT via `forge auth login`, then retry."
    ),
    AUTH_TOKEN_EXPIRED: (
        "Your JWT has expired. Run `forge auth login` to obtain a new token, "
        "then retry the request."
    ),
    AUTH_PERMISSION_DENIED: (
        "Your token lacks the required permission. Ask an administrator to "
        "grant the missing permission or use a token with broader scope."
    ),
    TASK_NOT_FOUND: ("Check the task ID with `forge tasks list` and retry with a valid ID."),
    TASK_VALIDATION_ERROR: (
        "Fix the invalid fields shown in 'details' and retry. "
        "Run `forge tasks create --help` for field requirements."
    ),
    TASK_CREATION_FAILED: ("Check storage availability and logs. Retry with `forge tasks create`."),
    TASK_UPDATE_FAILED: (
        "Retry the update. If the problem persists run `forge doctor` to check storage health."
    ),
    TASK_DELETE_FAILED: (
        "Verify the task exists with `forge tasks list` and retry. "
        "Run `forge doctor` if the problem persists."
    ),
    LEASE_INVALID: (
        "Provide all required lease fields: owner_node, owner_agent, path_lock, "
        "and a future lease_expires_at (UTC ISO-8601)."
    ),
    LEASE_ALREADY_OWNED: (
        "The task is already leased by another owner. Wait for the lease to "
        "expire or ask the owner to release it with "
        "`forge tasks lease release <task_id>`."
    ),
    LEASE_NOT_FOUND: (
        "The task has no active lease. Claim it first with `forge tasks lease claim <task_id>`."
    ),
    LEASE_EXPIRED: (
        "The existing lease has expired. Claim the task again with "
        "`forge tasks lease claim <task_id>`."
    ),
    LEASE_OWNER_MISMATCH: (
        "Only the current lease owner can modify this lease. "
        "Verify owner_node and owner_agent match the active lease."
    ),
    LEASE_PATH_LOCK_CONFLICT: (
        "Another task already holds this path_lock. Choose a different "
        "path_lock or wait for the conflicting task to release it."
    ),
    LEASE_PATH_LOCK_IMMUTABLE: (
        "path_lock cannot change during lease renewal. Release the lease "
        "and re-claim with the desired path_lock."
    ),
    AGENT_NOT_FOUND: ("Verify the agent ID with `forge fleet list` and retry with a valid ID."),
    AGENT_NOT_AVAILABLE: (
        "The target agent is busy or paused. Check its status with "
        "`forge fleet status <agent_id>` and retry once it is idle."
    ),
    DISPATCH_FAILED: (
        "The agent may not be running or its tmux session is unreachable. "
        "Verify with `forge fleet status` and retry."
    ),
    CONTRACT_VIOLATION: (
        "A contract check failed. Run `forge contract verify` to see "
        "which requirements are unmet and address them before retrying."
    ),
    DOCTOR_CHECK_FAILED: (
        "A system health check failed. Review the details and run "
        "`forge doctor --fix` to attempt automatic remediation."
    ),
    DOCTOR_FIX_FAILED: (
        "Auto-fix could not resolve the issue. Consult the details, fix "
        "manually, and re-run `forge doctor` to confirm."
    ),
    UNKNOWN_ERROR: (
        "An unexpected error occurred. Check the server logs for details "
        "and run `forge doctor` for a diagnostics report."
    ),
    VALIDATION_ERROR: (
        "One or more fields failed validation. Fix the issues shown in 'details' and retry."
    ),
    NOT_FOUND: ("The requested resource was not found. Verify the ID/path and retry."),
    SERVICE_UNAVAILABLE: (
        "A required service is unavailable. Run `forge doctor` to check "
        "infrastructure health and retry when services are restored."
    ),
    INTERNAL_ERROR: (
        "An internal error occurred. Check the server logs and run `forge doctor` for diagnostics."
    ),
}


def next_action_for(code: str) -> str:
    """Return the canonical next_action guidance string for a given error code.

    Falls back to the UNKNOWN_ERROR guidance if the code is unrecognised.
    """
    return _NEXT_ACTIONS.get(code, _NEXT_ACTIONS[UNKNOWN_ERROR])


# ---------------------------------------------------------------------------
# ForgeError — the canonical error object
# ---------------------------------------------------------------------------


@dataclass
class ForgeError:
    """Structured error conforming to the FORGE canonical error schema.

    All control-plane error surfaces (API, CLI) MUST produce errors in this
    form.

    Attributes:
        code:        Machine-readable error code (SCREAMING_SNAKE_CASE).
        message:     Concise, human-readable description of what went wrong.
        next_action: Actionable guidance for the operator / agent.
        details:     Optional dict with additional structured context such as
                     conflicting resource IDs, invalid field names, etc.
    """

    code: str
    message: str
    next_action: str = field(default="")
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        # Auto-populate next_action from catalogue when caller omits it.
        if not self.next_action:
            self.next_action = next_action_for(self.code)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON or CLI rendering."""
        out: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "next_action": self.next_action,
        }
        if self.details is not None:
            out["details"] = self.details
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ForgeError:
        """Deserialise from a plain dict (e.g. parsed API response body)."""
        return cls(
            code=data.get("code", UNKNOWN_ERROR),
            message=data.get("message", ""),
            next_action=data.get("next_action", ""),
            details=data.get("details"),
        )


# ---------------------------------------------------------------------------
# API envelope helpers
# ---------------------------------------------------------------------------


def api_error_body(
    code: str,
    message: str,
    *,
    next_action: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fully-formed API error response body.

    Returns::

        {
            "success": False,
            "data": None,
            "error": {
                "code": ...,
                "message": ...,
                "next_action": ...,
                "details": ...   # only present when non-None
            },
            "timestamp": "..."
        }
    """
    from datetime import UTC, datetime

    error = ForgeError(
        code=code,
        message=message,
        next_action=next_action,
        details=details,
    )
    return {
        "success": False,
        "data": None,
        "error": error.to_dict(),
        "timestamp": datetime.now(UTC).isoformat(),
    }


def api_success_body(data: Any) -> dict[str, Any]:
    """Build a fully-formed API success response body."""
    from datetime import UTC, datetime

    return {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": datetime.now(UTC).isoformat(),
    }
