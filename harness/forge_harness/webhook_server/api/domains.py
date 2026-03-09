"""Domain Context API Endpoint

REST API for Royal Jelly domain health data.

Reads lead-context.md files from .forge/context/*/lead-context.md and returns
parsed domain health summaries for the Unified Fleet Dashboard.

Endpoint:
    GET /api/domains  - List all domain health summaries (auth required)
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.core.dependencies import verify_auth

logger = get_logger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Path constants (4 parents up from this file to FORGE root)
# nodes.py: nodes.py -> api/ -> webhook_server/ -> forge_harness/ -> harness/ -> FORGE/
# ---------------------------------------------------------------------------

_FORGE_ROOT: Path = Path(__file__).resolve().parents[4]
_CONTEXT_DIR: Path = _FORGE_ROOT / ".forge" / "context"

# ---------------------------------------------------------------------------
# Static domain map: short code -> (display name, full domain slug)
# ---------------------------------------------------------------------------

DOMAIN_MAP: dict[str, tuple[str, str]] = {
    "ag": ("AdGuild", "adguild-io"),
    "cc": ("CalmConnect", "calmconnect"),
    "da": ("DiscoverAI", "discoverai-co"),
    "forge": ("FORGE Infra", "forge"),
    "is": ("Interview Simulator", "codeswiftr-com"),
    "lv": ("LeanVibe", "leanvibe-ai"),
    "nf": ("NeoForge", "neoforge-dev"),
    "oc": ("OpenClaw", "openclaw"),
    "pk": ("PKM.ai", "brandfocus-ai"),
    "sf": ("StudyFlow", "thebrightharbor-com"),
    "vc": ("Voice Coach", "brandfocus-ai"),
}

# ---------------------------------------------------------------------------
# Regex patterns for parsing lead-context.md files
# ---------------------------------------------------------------------------

_RE_LAST_UPDATED = re.compile(r"\*\*Last Updated:\*\*\s*(.+)")
_RE_LEAD = re.compile(r"\*\*Lead:\*\*\s*(.+)")
_RE_DOMAIN = re.compile(r"\*\*Domain:\*\*\s*(.+)")

# Matches lines containing "blocker" (case-insensitive), used for counting
_RE_BLOCKER_LINE = re.compile(r"blocker", re.IGNORECASE)

# Matches numbered list items under any "Next N Priorities" heading
_RE_PRIORITY_ITEM = re.compile(r"^\d+\.")

# Section heading for priorities
_RE_PRIORITIES_HEADING = re.compile(r"^##\s+Next\s+\d+\s+Priorities", re.IGNORECASE)

# "None" or "none known" blocker text — these don't count as real blockers
_RE_NONE_BLOCKER = re.compile(r"^(none|none known)[.!]?\s*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_context_file(path: Path, code: str) -> dict[str, Any] | None:
    """Parse a lead-context.md file and return a domain health dict.

    Returns None if the file cannot be read or parsed.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Cannot read context file %s: %s", path, exc)
        return None

    name, full_domain = DOMAIN_MAP.get(code, (code, code))

    # --- Scalar fields ---
    last_updated = ""
    lead = "unassigned"

    m = _RE_LAST_UPDATED.search(text)
    if m:
        last_updated = m.group(1).strip()

    m = _RE_LEAD.search(text)
    if m:
        lead = m.group(1).strip()

    # --- Blocker count ---
    # Count lines that contain "blocker" but exclude lines where the blocker
    # value is explicitly "none" or "none known".
    blocker_count = 0
    for line in text.splitlines():
        if _RE_BLOCKER_LINE.search(line):
            # Extract the value portion after a colon, if present
            value_part = line.split(":", 1)[-1].strip() if ":" in line else line.strip()
            if _RE_NONE_BLOCKER.match(value_part):
                # Explicitly "none" — skip
                continue
            blocker_count += 1

    # --- Priority count ---
    # Count numbered items that appear *after* the "## Next N Priorities" heading
    priority_count = 0
    in_priorities_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if _RE_PRIORITIES_HEADING.match(stripped):
            in_priorities_section = True
            continue
        if in_priorities_section:
            # A new heading ends the section
            if stripped.startswith("##"):
                break
            if _RE_PRIORITY_ITEM.match(stripped):
                priority_count += 1

    # --- Status ---
    # "blocked" when blocker_count > 0, else "active"
    status = "blocked" if blocker_count > 0 else "active"

    rel_path = str(path.relative_to(_FORGE_ROOT))

    return {
        "code": code,
        "name": name,
        "full_domain": full_domain,
        "lead": lead,
        "last_updated": last_updated,
        "status": status,
        "blocker_count": blocker_count,
        "priority_count": priority_count,
        "context_path": rel_path,
    }


def _collect_domains() -> list[dict[str, Any]]:
    """Read all domain context files and return parsed health dicts.

    Only domains present in DOMAIN_MAP are returned; unknown directories are
    skipped.  Files that cannot be parsed are silently skipped.
    """
    domains: list[dict[str, Any]] = []

    if not _CONTEXT_DIR.is_dir():
        logger.warning("Context directory not found: %s", _CONTEXT_DIR)
        return domains

    for code in sorted(DOMAIN_MAP.keys()):
        context_path = _CONTEXT_DIR / code / "lead-context.md"
        if not context_path.is_file():
            logger.debug("No lead-context.md for domain %s at %s", code, context_path)
            continue

        parsed = _parse_context_file(context_path, code)
        if parsed is not None:
            domains.append(parsed)

    return domains


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/api/domains")
async def list_domains(_: None = Depends(verify_auth)) -> JSONResponse:
    """Return parsed Royal Jelly domain health summaries.

    Reads ``.forge/context/*/lead-context.md`` and extracts lead, last-updated,
    blocker count, and priority count for each known domain.

    Response shape::

        {
          "domains": [
            {
              "code": "is",
              "name": "Interview Simulator",
              "full_domain": "codeswiftr-com",
              "lead": "kimi (sati)",
              "last_updated": "2026-03-01 (S63)",
              "status": "active",
              "blocker_count": 1,
              "priority_count": 3,
              "context_path": ".forge/context/is/lead-context.md"
            }
          ],
          "total_domains": 11,
          "generated_at": "<ISO-8601>"
        }

    Requires Bearer token authentication.
    """
    try:
        domains = _collect_domains()
    except Exception as exc:
        logger.error("Failed to collect domain summaries: %s", exc)
        domains = []

    return JSONResponse(
        {
            "domains": domains,
            "total_domains": len(DOMAIN_MAP),
            "generated_at": datetime.now(UTC).isoformat(),
        }
    )
