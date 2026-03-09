"""
Memories API Router

Handles reading of .forge/memories/*.md files for the Command Center Memories screen.

Endpoints:
- GET /api/memories - List all memory files with metadata
"""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forge_harness.webhook_server.core.dependencies import verify_auth

router = APIRouter()


def api_response(data):
    """Create standardized API response."""
    return {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# Request/Response Models
class MemoryMetadata(BaseModel):
    """Memory file metadata."""

    id: str = Field(..., description="Memory file ID (filename without .md)")
    title: str = Field(..., description="Memory title derived from filename or content")
    path: str = Field(..., description="Relative path to memory file")
    summary: str = Field(..., description="First 200 characters of content")
    category: str = Field(..., description="Category derived from filename or path")
    last_modified: str = Field(..., description="Last modified timestamp")


def _get_forge_repo_root() -> Path:
    """Find FORGE repo root (directory containing .forge)."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / ".forge").is_dir():
            return current
        current = current.parent
    return Path.cwd()


def _read_memory_content(memory_path: Path, max_chars: int = 200) -> str:
    """Read first max_chars of memory file for summary."""
    try:
        with open(memory_path, encoding="utf-8") as f:
            content = f.read(max_chars)
            # Clean up whitespace for summary
            return content.strip().replace("\n", " ")
    except Exception:
        return ""


def _extract_title(filename: str, content: str) -> str:
    """Extract title from filename or content."""
    # Try to get title from first H1/H2 in content
    for line in content.split("\n")[:10]:
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line.startswith("## "):
            return line[3:].strip()
    # Fallback to filename
    return filename.replace("-", " ").replace("_", " ").replace(".md", "").title()


def _get_category(path: Path) -> str:
    """Derive category from memory path."""
    parts = path.parts
    # Look for category indicators in path
    for part in parts:
        if part in ("learnings", "patterns", "daily", "decisions"):
            return part
    return "general"


@router.get("/api/memories")
async def list_memories(
    category: str | None = Query(None, description="Filter by category"),
    limit: int = Query(100, description="Maximum memories to return"),
    _: None = Depends(verify_auth),  # Auth placeholder
) -> JSONResponse:
    """List all memory files from .forge/memories/.

    Returns list of memories with id, title, path, summary, and category.
    """
    try:
        forge_root = _get_forge_repo_root()
        memories_dir = forge_root / ".forge" / "memories"

        if not memories_dir.exists():
            return JSONResponse(
                content=api_response(
                    {
                        "memories": [],
                        "count": 0,
                        "message": "Memories directory not found",
                    }
                )
            )

        memories = []
        for memory_file in sorted(memories_dir.glob("*.md"))[:limit]:
            content = _read_memory_content(memory_file)
            title = _extract_title(memory_file.name, content)
            mem_category = _get_category(memory_file)

            # Filter by category if specified
            if category and mem_category != category:
                continue

            memories.append(
                MemoryMetadata(
                    id=memory_file.stem,
                    title=title,
                    path=str(memory_file.relative_to(forge_root)),
                    summary=content[:200] if content else "",
                    category=mem_category,
                    last_modified=datetime.fromtimestamp(
                        memory_file.stat().st_mtime, UTC
                    ).isoformat(),
                )
            )

        return JSONResponse(
            content=api_response(
                {
                    "memories": [m.model_dump() for m in memories],
                    "count": len(memories),
                }
            )
        )
    except Exception as e:
        return JSONResponse(
            content={
                "success": False,
                "data": {"memories": [], "count": 0},
                "error": str(e),
                "timestamp": datetime.now(UTC).isoformat(),
            },
            status_code=500,
        )


@router.get("/api/memories/search")
async def search_memories(
    q: str = Query(..., description="Search query"),
    limit: int = Query(50, description="Maximum results to return"),
    _: None = Depends(verify_auth),
) -> JSONResponse:
    """Search memories by query string.

    Searches through memory content and titles.
    """
    try:
        forge_root = _get_forge_repo_root()
        memories_dir = forge_root / ".forge" / "memories"

        if not memories_dir.exists():
            return JSONResponse(
                content=api_response(
                    {"results": [], "count": 0, "message": "Memories directory not found"}
                )
            )

        results = []
        query_lower = q.lower()

        for memory_file in sorted(memories_dir.glob("*.md")):
            content = _read_memory_content(memory_file, max_chars=500)
            title = _extract_title(memory_file.name, content)

            # Search in title and content
            if query_lower in title.lower() or query_lower in content.lower():
                results.append(
                    MemoryMetadata(
                        id=memory_file.stem,
                        title=title,
                        path=str(memory_file.relative_to(forge_root)),
                        summary=content[:200] if content else "",
                        category=_get_category(memory_file),
                        last_modified=datetime.fromtimestamp(
                            memory_file.stat().st_mtime, UTC
                        ).isoformat(),
                    )
                )

            if len(results) >= limit:
                break

        return JSONResponse(
            content=api_response(
                {
                    "results": [r.model_dump() for r in results],
                    "count": len(results),
                    "query": q,
                }
            )
        )
    except Exception as e:
        return JSONResponse(
            content={
                "success": False,
                "data": {"results": [], "count": 0},
                "error": str(e),
                "timestamp": datetime.now(UTC).isoformat(),
            },
            status_code=500,
        )


@router.get("/api/memories/{memory_id}")
async def get_memory(
    memory_id: str,
    _: None = Depends(verify_auth),  # Auth placeholder
) -> JSONResponse:
    """Get a specific memory file by ID."""
    try:
        forge_root = _get_forge_repo_root()
        memories_dir = forge_root / ".forge" / "memories"

        # Try both .md extension and without
        memory_path = memories_dir / f"{memory_id}.md"
        if not memory_path.exists():
            memory_path = memories_dir / memory_id
        if not memory_path.exists():
            return JSONResponse(
                content={
                    "success": False,
                    "data": None,
                    "error": f"Memory '{memory_id}' not found",
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                status_code=404,
            )

        with open(memory_path, encoding="utf-8") as f:
            content = f.read()

        return JSONResponse(
            content=api_response(
                {
                    "id": memory_path.stem,
                    "title": _extract_title(memory_path.name, content),
                    "path": str(memory_path.relative_to(forge_root)),
                    "content": content,
                    "category": _get_category(memory_path),
                    "last_modified": datetime.fromtimestamp(
                        memory_path.stat().st_mtime, UTC
                    ).isoformat(),
                }
            )
        )
    except Exception as e:
        return JSONResponse(
            content={
                "success": False,
                "data": None,
                "error": str(e),
                "timestamp": datetime.now(UTC).isoformat(),
            },
            status_code=500,
        )
