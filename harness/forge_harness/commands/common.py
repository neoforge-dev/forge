"""Shared helpers for command groups."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def find_forge_root() -> Path | None:
    """Find the FORGE repository root by looking for markers."""
    current = Path.cwd()

    for _ in range(10):
        if (current / ".forge-root").exists():
            return current
        if (current / "domains.yaml").exists():
            return current
        if (current / ".git").exists():
            if any(d.is_dir() and "-" in d.name for d in current.iterdir() if d.is_dir()):
                return current
        parent = current.parent
        if parent == current:
            break
        current = parent

    return None


def detect_github_repo(forge_root: Path) -> str | None:
    """Detect GitHub repository from git remote."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=forge_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None

        url = result.stdout.strip()

        if url.startswith("git@github.com:"):
            repo = url.replace("git@github.com:", "").replace(".git", "")
            return repo

        if "github.com" in url:
            parts = url.split("github.com/")
            if len(parts) > 1:
                repo = parts[1].replace(".git", "")
                return repo

        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_client(url: str, token: str | None = None) -> Any:
    """Get Command Center client with token from environment or parameter."""
    from ..command_center_client import CommandCenterClient

    if token is None:
        token = os.environ.get("FORGE_WEBHOOK_TOKEN")
    return CommandCenterClient(base_url=url, token=token)
