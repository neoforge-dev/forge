"""Version API Endpoints

API version information and metadata.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/version")
async def api_version() -> dict[str, Any]:
    """Get API version information using the standard response shape.

    Returns:
        dict: Version information
    """
    version = "1.0.0"
    try:
        import tomllib

        pyproject_path = Path(__file__).resolve().parents[3] / "pyproject.toml"
        if pyproject_path.exists():
            with pyproject_path.open("rb") as f:
                pyproject = tomllib.load(f)
                version = pyproject.get("project", {}).get("version", "1.0.0")
    except Exception:
        pass

    return {
        "version": version,
        "name": "forge-harness-webhooks",
        "api_prefix": "/api",
    }


@router.get("/api/build")
async def build_info() -> dict[str, Any]:
    """Get build information.

    Returns:
        dict: Build metadata
    """
    return {
        "build_number": "unknown",
        "commit": "unknown",
        "timestamp": datetime.now(UTC).isoformat(),
    }
