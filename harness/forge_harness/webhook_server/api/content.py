"""Content library API for FORGE Command Center."""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from forge_harness.webhook_server.core.dependencies import verify_auth

logger = logging.getLogger(__name__)
router = APIRouter()

FORGE_ROOT = Path(os.environ.get("FORGE_ROOT", Path(__file__).resolve().parents[4]))

# Content file types to scan for
CONTENT_FILE_PATTERNS = [
    "blog-posts.md",
    "blog_posts.md",
    "social-posts.md",
    "social_posts.md",
    "case-studies.md",
    "case_studies.md",
    "email-sequences.md",
    "email_sequences.md",
    "lead-magnets.md",
    "lead_magnets.md",
    "linkedin-posts*.md",
    "README.md",
    "CONTENT_STRATEGY.md",
]


def api_response(data: Any, error: dict[str, str] | None = None) -> dict:
    """Standard API response format."""
    return {
        "success": error is None,
        "data": data,
        "error": error,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def _count_words(file_path: Path) -> int:
    """Count words in a markdown file."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        # Simple word count - split by whitespace
        words = len(text.split())
        return words
    except Exception as e:
        logger.warning(f"Failed to count words in {file_path}: {e}")
        return 0


def _get_content_types(content_dir: Path) -> list[str]:
    """Identify content types present in a content directory."""
    types = set()
    for file in content_dir.iterdir():
        if not file.is_file() or not file.name.endswith(".md"):
            continue

        name_lower = file.name.lower()
        if "blog" in name_lower:
            types.add("blog_posts")
        if "social" in name_lower or "linkedin" in name_lower:
            types.add("social_media")
        if "case" in name_lower and "stud" in name_lower:
            types.add("case_studies")
        if "email" in name_lower or "sequence" in name_lower:
            types.add("email_sequences")
        if "lead" in name_lower and "magnet" in name_lower:
            types.add("lead_magnets")
        if "strategy" in name_lower:
            types.add("strategy")

    return sorted(types)


def _scan_content_directories() -> dict[str, Any]:
    """Scan all content directories in the FORGE portfolio."""
    projects = []
    total_files = 0
    total_words = 0

    # Scan for content directories: {domain}/{project}/content/
    for domain_dir in FORGE_ROOT.iterdir():
        if not domain_dir.is_dir() or domain_dir.name.startswith("."):
            continue

        for project_dir in domain_dir.iterdir():
            if not project_dir.is_dir() or project_dir.name.startswith("."):
                continue

            content_dir = project_dir / "content"
            if not content_dir.exists() or not content_dir.is_dir():
                continue

            # Count markdown files
            md_files = list(content_dir.glob("*.md"))
            if not md_files:
                continue

            # Count words across all markdown files
            project_words = sum(_count_words(f) for f in md_files)

            # Get last modified time
            last_updated = max(f.stat().st_mtime for f in md_files)
            last_updated_dt = datetime.fromtimestamp(last_updated, tz=UTC)

            # Identify content types
            content_types = _get_content_types(content_dir)

            projects.append(
                {
                    "domain": domain_dir.name,
                    "project": project_dir.name,
                    "has_content": True,
                    "content_types": content_types,
                    "file_count": len(md_files),
                    "total_words": project_words,
                    "last_updated": last_updated_dt.isoformat(),
                    "path": str(content_dir.relative_to(FORGE_ROOT)),
                }
            )

            total_files += len(md_files)
            total_words += project_words

    # Sort by last_updated descending
    projects.sort(key=lambda p: p["last_updated"], reverse=True)

    return {
        "projects": projects,
        "summary": {
            "total_projects_with_content": len(projects),
            "total_files": total_files,
            "total_words": total_words,
        },
    }


@router.get("/api/content")
async def get_content_libraries(_=Depends(verify_auth)):
    """Get content library status for all projects.

    Scans the FORGE portfolio for content directories and returns
    information about files, word counts, and content types.

    Returns:
        JSONResponse: Content library data with summary stats
    """
    try:
        data = _scan_content_directories()
        return JSONResponse(content=api_response(data))
    except Exception as e:
        logger.exception("Failed to scan content directories")
        return JSONResponse(
            status_code=500,
            content=api_response(
                None,
                {"code": "scan_failed", "message": f"Failed to scan content directories: {e!s}"},
            ),
        )


@router.get("/api/content/stats")
async def get_content_stats(_=Depends(verify_auth)):
    """Get aggregate content statistics across the portfolio.

    Returns:
        JSONResponse: Aggregate statistics about content libraries
    """
    try:
        data = _scan_content_directories()
        projects = data["projects"]

        # Calculate statistics
        stats = {
            "total_projects": len(projects),
            "total_files": data["summary"]["total_files"],
            "total_words": data["summary"]["total_words"],
            "avg_words_per_project": (
                round(data["summary"]["total_words"] / len(projects)) if projects else 0
            ),
            "avg_files_per_project": (
                round(data["summary"]["total_files"] / len(projects), 1) if projects else 0
            ),
            "content_types": {},
            "domains": {},
        }

        # Count content types
        for project in projects:
            for content_type in project["content_types"]:
                stats["content_types"][content_type] = (
                    stats["content_types"].get(content_type, 0) + 1
                )

            # Count by domain
            domain = project["domain"]
            if domain not in stats["domains"]:
                stats["domains"][domain] = {
                    "projects": 0,
                    "files": 0,
                    "words": 0,
                }
            stats["domains"][domain]["projects"] += 1
            stats["domains"][domain]["files"] += project["file_count"]
            stats["domains"][domain]["words"] += project["total_words"]

        return JSONResponse(content=api_response(stats))

    except Exception as e:
        logger.exception("Failed to calculate content statistics")
        return JSONResponse(
            status_code=500,
            content=api_response(
                None,
                {"code": "stats_failed", "message": f"Failed to calculate statistics: {e!s}"},
            ),
        )


@router.get("/api/content/{domain}/{project}")
async def get_project_content(domain: str, project: str, _=Depends(verify_auth)):
    """Get detailed content information for a specific project.

    Args:
        domain: Domain name (e.g., "brandfocus-ai")
        project: Project name (e.g., "voice-coach")

    Returns:
        JSONResponse: Detailed content information for the project
    """
    content_dir = FORGE_ROOT / domain / project / "content"

    if not content_dir.exists() or not content_dir.is_dir():
        return JSONResponse(
            status_code=404,
            content=api_response(
                None,
                {
                    "code": "not_found",
                    "message": f"No content directory found for {domain}/{project}",
                },
            ),
        )

    # Get all markdown files
    md_files = list(content_dir.glob("*.md"))
    if not md_files:
        return JSONResponse(
            status_code=404,
            content=api_response(
                None,
                {
                    "code": "no_content",
                    "message": f"Content directory exists but contains no markdown files for {domain}/{project}",
                },
            ),
        )

    # Build detailed file list
    files = []
    total_words = 0

    for file in md_files:
        word_count = _count_words(file)
        total_words += word_count

        files.append(
            {
                "name": file.name,
                "size_bytes": file.stat().st_size,
                "word_count": word_count,
                "last_modified": datetime.fromtimestamp(file.stat().st_mtime, tz=UTC).isoformat(),
            }
        )

    # Sort by last modified descending
    files.sort(key=lambda f: f["last_modified"], reverse=True)

    # Get content types
    content_types = _get_content_types(content_dir)

    return JSONResponse(
        content=api_response(
            {
                "domain": domain,
                "project": project,
                "content_types": content_types,
                "file_count": len(files),
                "total_words": total_words,
                "files": files,
                "path": str(content_dir.relative_to(FORGE_ROOT)),
            }
        )
    )
