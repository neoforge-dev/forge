"""Standard JSON output contract for FORGE CLIs."""

from datetime import datetime
from typing import Any

from forge_shared.cli.timing import duration_ms, iso_utc_now


def build_response(
    *,
    command: str,
    data: Any = None,
    error: str | None = None,
    error_code: str | None = None,
    start_time: datetime | None = None,
    version: str = "0.1.0",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a standard response envelope for CLI output."""
    envelope_metadata: dict[str, Any] = {
        "command": command,
        "version": version,
    }
    if metadata:
        envelope_metadata.update(metadata)

    return {
        "success": error is None,
        "data": data,
        "error": error,
        "error_code": error_code,
        "timestamp": iso_utc_now(),
        "duration_ms": duration_ms(start_time),
        "metadata": envelope_metadata,
    }


def output_success(
    *,
    command: str,
    data: Any,
    start_time: datetime | None = None,
    version: str = "0.1.0",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a successful CLI output envelope."""
    return build_response(
        command=command,
        data=data,
        start_time=start_time,
        version=version,
        metadata=metadata,
    )


def output_error(
    *,
    command: str,
    message: str,
    error_code: str | None = None,
    start_time: datetime | None = None,
    version: str = "0.1.0",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a failed CLI output envelope."""
    return build_response(
        command=command,
        data=None,
        error=message,
        error_code=error_code,
        start_time=start_time,
        version=version,
        metadata=metadata,
    )

