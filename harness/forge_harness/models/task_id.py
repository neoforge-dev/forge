"""JIRA-style task ID generation.

Generates human-readable task IDs like IS-042, HRN-007 instead of random hex.
Counter state is persisted in .forge/tasks/_counters.json.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

DOMAIN_PREFIXES: dict[str, str] = {
    "interview-simulator": "IS",
    "voice-coach": "VC",
    "code-atlas": "CA",
    "command-center": "CC",
    "harness": "HRN",
    "forge": "FRG",
    "brandfocus-ai": "BF",
    "codeswiftr-com": "CS",
    "leanvibe-fit": "LV",
    "discover-ai": "DA",
    "allergen-coach": "AC",
}

DEFAULT_PREFIX = "TASK"

_lock = threading.Lock()


def _counters_path() -> Path:
    """Return the path to the counters persistence file."""
    return Path(".forge/tasks/_counters.json")


def _load_counters() -> dict[str, int]:
    """Load counters from disk, returning empty dict if missing/corrupt."""
    path = _counters_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_counters(counters: dict[str, int]) -> None:
    """Persist counters to disk."""
    path = _counters_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(counters, indent=2) + "\n")


def resolve_prefix(domain: str | None) -> str:
    """Resolve a domain name to its short prefix."""
    if not domain:
        return DEFAULT_PREFIX
    return DOMAIN_PREFIXES.get(domain, DEFAULT_PREFIX)


def generate_task_id(domain: str | None = None) -> str:
    """Generate a JIRA-style task ID like IS-042 or TASK-007.

    Thread-safe. Counter state is persisted to .forge/tasks/_counters.json.

    Args:
        domain: Optional domain name (e.g., "interview-simulator").
                Maps to a prefix via DOMAIN_PREFIXES. Falls back to TASK.

    Returns:
        A string like "IS-042" or "TASK-001".
    """
    prefix = resolve_prefix(domain)

    with _lock:
        counters = _load_counters()
        counter = counters.get(prefix, 0) + 1
        counters[prefix] = counter
        _save_counters(counters)

    return f"{prefix}-{counter:03d}"
