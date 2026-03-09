"""Quality metrics API for FORGE Command Center."""

import asyncio
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from forge_harness.webhook_server.core.dependencies import verify_auth

logger = logging.getLogger(__name__)
router = APIRouter()

FORGE_ROOT = Path(os.environ.get("FORGE_ROOT", Path(__file__).resolve().parents[4]))
QUALITY_CACHE = FORGE_ROOT / ".forge" / "quality_cache.json"

# Cache TTL: 5 minutes
CACHE_TTL_SECONDS = 300

# Project to domain mapping (used if not in project path)
PROJECT_DOMAIN_MAP = {
    "voice-coach": "brandfocus-ai",
    "brandfocus-platform": "brandfocus-ai",
    "adguild-platform": "adguild-io",
    "interview-simulator": "codeswiftr-com",
    "skillswiftr": "codeswiftr-com",
    "campaign-intelligence": "adguild-io",
    "ad-asset-finder": "adguild-io",
    "discover-ai": "brandfocus-ai",
    "rag-cost-optimizer": "neoforge-dev",
    "graphrag-starter": "neoforge-dev",
    "newsletter": "brandfocus-ai",
    "brand-brain": "brandfocus-ai",
    "allergen-coach": "babybit-es",
    "burnout-pulse": "calmconnect-io",
    "api-bridge": "neoforge-dev",
    "forge-shared": "forge-shared",
    "harness": "harness",
}

# Hard-coded quality scorecard from the orchestrator's tracking
# This is the ground truth from manual quality sweeps
QUALITY_SCORECARD = {
    "voice-coach": {"domain": "brandfocus-ai", "tests": 1191, "coverage": 91, "ruff": 0, "mypy": 0},
    "brandfocus-platform": {
        "domain": "brandfocus-ai",
        "tests": 57,
        "coverage": 99,
        "ruff": 0,
        "mypy": 0,
    },
    "adguild-platform": {
        "domain": "adguild-io",
        "tests": 440,
        "coverage": 80,
        "ruff": 0,
        "mypy": 0,
    },
    "interview-simulator": {
        "domain": "codeswiftr-com",
        "tests": 772,
        "coverage": 70,
        "ruff": 0,
        "mypy": 52,
    },
    "skillswiftr": {"domain": "codeswiftr-com", "tests": 340, "coverage": 85, "ruff": 0, "mypy": 0},
    "campaign-intelligence": {
        "domain": "adguild-io",
        "tests": 310,
        "coverage": 72,
        "ruff": 0,
        "mypy": 0,
    },
    "ad-asset-finder": {"domain": "adguild-io", "tests": 77, "coverage": 88, "ruff": 0, "mypy": 0},
    "discover-ai": {"domain": "brandfocus-ai", "tests": 287, "coverage": 75, "ruff": 0, "mypy": 0},
    "rag-cost-optimizer": {
        "domain": "neoforge-dev",
        "tests": 16,
        "coverage": None,
        "ruff": 0,
        "mypy": 0,
    },
    "graphrag-starter": {
        "domain": "neoforge-dev",
        "tests": 184,
        "coverage": 71,
        "ruff": 0,
        "mypy": 0,
    },
    "newsletter": {"domain": "brandfocus-ai", "tests": 212, "coverage": 96, "ruff": 0, "mypy": 0},
    "brand-brain": {"domain": "brandfocus-ai", "tests": 148, "coverage": 78, "ruff": 0, "mypy": 0},
    "allergen-coach": {
        "domain": "babybit-es",
        "tests": None,
        "coverage": None,
        "ruff": 0,
        "mypy": 0,
        "note": "blocked by SQLite UUID",
    },
    "burnout-pulse": {
        "domain": "calmconnect-io",
        "tests": 115,
        "coverage": 98,
        "ruff": 0,
        "mypy": 0,
    },
    "api-bridge": {"domain": "neoforge-dev", "tests": 7, "coverage": None, "ruff": 0, "mypy": 0},
    "forge-shared": {
        "domain": "forge-shared",
        "tests": 190,
        "coverage": None,
        "ruff": 0,
        "mypy": 0,
    },
    "harness": {"domain": "harness", "tests": 75, "coverage": 6, "ruff": 0, "mypy": None},
}


def _get_project_path(project: str) -> Path | None:
    """Find the project directory in portfolio or harness."""
    # Check portfolio
    portfolio_path = FORGE_ROOT / "portfolio"
    if portfolio_path.exists():
        for domain_dir in portfolio_path.iterdir():
            if domain_dir.is_dir():
                project_path = domain_dir / project
                if project_path.exists():
                    return project_path
                # Check nested structure
                for subdir in domain_dir.iterdir():
                    if subdir.is_dir() and subdir.name == project:
                        return subdir

    # Check harness
    harness_path = FORGE_ROOT / "harness"
    if harness_path.exists():
        project_path = harness_path / project
        if project_path.exists():
            return project_path

    # Check forge-shared
    shared_path = FORGE_ROOT / "forge-shared"
    if shared_path.exists():
        project_path = shared_path / "projects" / project
        if project_path.exists():
            return project_path

    return None


async def _scan_project_quality(project: str) -> dict[str, Any]:
    """Scan a single project for quality metrics.

    Runs pytest --collect-only to get test count, attempts ruff/mypy check.

    Args:
        project: Project name

    Returns:
        Dict with tests, coverage, ruff_errors, mypy_errors
    """
    result = {
        "tests": None,
        "coverage": None,
        "ruff_errors": None,
        "mypy_errors": None,
    }

    project_path = _get_project_path(project)
    if not project_path:
        logger.warning(f"Project path not found for: {project}")
        return result

    # Try to collect pytest tests
    test_paths = [
        project_path / "tests",
        project_path / "test",
        project_path / "backend" / "tests",
        project_path / "backend" / "test",
    ]

    test_path = None
    for tp in test_paths:
        if tp.exists():
            test_path = tp
            break

    if test_path:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pytest",
                "--collect-only",
                "-q",
                str(test_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(project_path),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode()

            # Parse test count from pytest output
            # Format: "X tests collected" or "No tests collected"
            if "tests collected" in output:
                parts = output.split()
                for i, part in enumerate(parts):
                    if part == "tests" and i > 0:
                        try:
                            result["tests"] = int(parts[i - 1])
                            break
                        except (ValueError, IndexError):
                            pass
            elif "test collected" in output:  # singular
                parts = output.split()
                for i, part in enumerate(parts):
                    if part == "test" and i > 0:
                        try:
                            result["tests"] = int(parts[i - 1])
                            break
                        except (ValueError, IndexError):
                            pass
        except TimeoutError:
            logger.warning(f"pytest --collect-only timed out for {project}")
        except FileNotFoundError:
            logger.debug(f"pytest not found for {project}")
        except Exception as e:
            logger.warning(f"Error scanning {project}: {e}")

    # Try ruff check
    try:
        proc = await asyncio.create_subprocess_exec(
            "ruff",
            "check",
            ".",
            "--output-format=json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(project_path),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode()
        # Count errors from JSON output
        try:
            issues = json.loads(output) if output.strip() else []
            result["ruff_errors"] = len(issues)
        except json.JSONDecodeError:
            result["ruff_errors"] = 0
    except TimeoutError:
        logger.warning(f"ruff check timed out for {project}")
    except FileNotFoundError:
        logger.debug(f"ruff not found for {project}")
    except Exception as e:
        logger.debug(f"ruff check error for {project}: {e}")

    # Try mypy check
    try:
        proc = await asyncio.create_subprocess_exec(
            "mypy",
            ".",
            "--no-error-summary",
            "--output-format=json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(project_path),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        output = stdout.decode() + stderr.decode()
        # Count mypy errors (lines with "error:")
        result["mypy_errors"] = output.count(": error:")
    except TimeoutError:
        logger.warning(f"mypy timed out for {project}")
    except FileNotFoundError:
        logger.debug(f"mypy not found for {project}")
    except Exception as e:
        logger.debug(f"mypy error for {project}: {e}")

    return result


async def scan_all_projects() -> dict[str, Any]:
    """Scan all projects in the scorecard for quality metrics.

    Returns:
        Dict mapping project name to quality metrics
    """
    results = {}

    # Scan projects concurrently with limit
    semaphore = asyncio.Semaphore(5)

    async def scan_with_limit(project: str):
        async with semaphore:
            return project, await _scan_project_quality(project)

    tasks = [scan_with_limit(p) for p in QUALITY_SCORECARD.keys()]
    scan_results = await asyncio.gather(*tasks, return_exceptions=True)

    for item in scan_results:
        if isinstance(item, Exception):
            logger.warning(f"Scan failed: {item}")
            continue
        project, metrics = item
        results[project] = metrics

    return results


def _load_cache() -> dict:
    """Load cached quality data with TTL check."""
    if QUALITY_CACHE.exists():
        try:
            data = json.loads(QUALITY_CACHE.read_text())
            # Check if cache is still valid
            cached_at = data.get("_cached_at")
            if cached_at:
                cached_time = datetime.fromisoformat(cached_at)
                if datetime.now(UTC) - cached_time < timedelta(seconds=CACHE_TTL_SECONDS):
                    return data
            return data
        except Exception:
            pass
    return {}


def _save_cache(data: dict) -> None:
    """Save quality data to cache with timestamp."""
    data["_cached_at"] = datetime.now(UTC).isoformat()
    try:
        QUALITY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        QUALITY_CACHE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning(f"Failed to save quality cache: {e}")


def _merge_with_scorecard(cache: dict) -> list[dict]:
    """Merge cache with hardcoded scorecard, cache takes precedence."""
    results = []
    for project, metrics in QUALITY_SCORECARD.items():
        entry = {
            "project": project,
            "domain": metrics["domain"],
            "tests": metrics.get("tests"),
            "coverage": metrics.get("coverage"),
            "ruff_errors": metrics.get("ruff", 0),
            "mypy_errors": metrics.get("mypy", 0),
            "note": metrics.get("note"),
            "last_updated": "2026-02-08",
            "source": "scorecard",
        }
        # Override with cache if fresher
        if project in cache:
            cached = cache[project]
            # Check if this is scanned data (has test count from scan)
            has_scanned = cached.get("tests") is not None
            entry.update({k: v for k, v in cached.items() if v is not None})
            entry["source"] = "scanned" if has_scanned else "cache"
            if has_scanned:
                entry["last_updated"] = cache.get("_cached_at", datetime.now(UTC).isoformat())
        results.append(entry)
    return results


def api_response(data, error=None):
    """Standard API response format."""
    return {
        "success": error is None,
        "data": data,
        "error": error,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/api/quality")
async def get_quality_metrics(_=Depends(verify_auth)):
    """Get quality metrics for all projects.

    Returns cached metrics if fresh, otherwise returns scorecard data.
    Use POST /api/quality/scan to refresh.

    Returns:
        JSONResponse: Quality metrics for all projects with summary stats
    """
    cache = _load_cache()
    projects = _merge_with_scorecard(cache)

    # Summary stats
    total_tests = sum(p["tests"] or 0 for p in projects)
    avg_coverage = None
    coverage_projects = [p for p in projects if p["coverage"] is not None]
    if coverage_projects:
        avg_coverage = round(
            sum(p["coverage"] for p in coverage_projects) / len(coverage_projects), 1
        )
    total_ruff = sum(p["ruff_errors"] or 0 for p in projects)
    total_mypy = sum(p["mypy_errors"] or 0 for p in projects)

    # Determine if we have fresh scanned data
    has_scanned_data = any(p.get("source") == "scanned" for p in projects)

    return JSONResponse(
        content=api_response(
            {
                "projects": projects,
                "summary": {
                    "total_projects": len(projects),
                    "total_tests": total_tests,
                    "average_coverage": avg_coverage,
                    "total_ruff_errors": total_ruff,
                    "total_mypy_errors": total_mypy,
                    "projects_at_zero_lint": sum(1 for p in projects if p["ruff_errors"] == 0),
                    "has_scanned_data": has_scanned_data,
                },
            }
        )
    )


@router.post("/api/quality/scan")
async def trigger_quality_scan(_=Depends(verify_auth)):
    """Trigger a fresh quality scan of all projects.

    Runs pytest --collect-only, ruff check, and mypy on each project.
    Results are cached for 5 minutes.

    Returns:
        JSONResponse: Scan status and results
    """
    logger.info("Starting quality scan of all projects...")

    try:
        # Run the scan
        scan_results = await scan_all_projects()

        # Save to cache
        _save_cache(scan_results)

        logger.info(f"Quality scan complete: {len(scan_results)} projects scanned")

        # Count results
        projects_with_tests = sum(1 for r in scan_results.values() if r.get("tests"))
        projects_with_ruff = sum(1 for r in scan_results.values() if r.get("ruff_errors") is not None)
        projects_with_mypy = sum(1 for r in scan_results.values() if r.get("mypy_errors") is not None)

        return JSONResponse(
            content=api_response(
                {
                    "status": "complete",
                    "projects_scanned": len(scan_results),
                    "projects_with_tests": projects_with_tests,
                    "projects_with_ruff": projects_with_ruff,
                    "projects_with_mypy": projects_with_mypy,
                    "cache_ttl_seconds": CACHE_TTL_SECONDS,
                }
            )
        )
    except Exception as e:
        logger.error(f"Quality scan failed: {e}")
        return JSONResponse(
            status_code=500,
            content=api_response(None, {"code": "scan_failed", "message": str(e)}),
        )


@router.get("/api/quality/scorecard")
async def get_quality_scorecard(_=Depends(verify_auth)):
    """Get the hard-coded quality scorecard.

    Returns the master quality metrics from manual sweeps.
    """
    return JSONResponse(
        content=api_response(
            {
                "scorecard": QUALITY_SCORECARD,
                "description": "Hard-coded quality metrics from orchestrator tracking",
                "last_updated": "2026-02-08",
            }
        )
    )


@router.get("/api/quality/{project}")
async def get_project_quality(project: str, _=Depends(verify_auth)):
    """Get quality metrics for a specific project.

    Args:
        project: Project name to get metrics for

    Returns:
        JSONResponse: Quality metrics for the requested project
    """
    if project not in QUALITY_SCORECARD:
        return JSONResponse(
            status_code=404,
            content=api_response(
                None, {"code": "not_found", "message": f"Project '{project}' not found"}
            ),
        )

    cache = _load_cache()
    metrics = dict(QUALITY_SCORECARD[project])
    if project in cache:
        metrics.update({k: v for k, v in cache[project].items() if v is not None})

    return JSONResponse(
        content=api_response(
            {
                "project": project,
                "domain": metrics["domain"],
                "tests": metrics.get("tests"),
                "coverage": metrics.get("coverage"),
                "ruff_errors": metrics.get("ruff", 0),
                "mypy_errors": metrics.get("mypy", 0),
                "note": metrics.get("note"),
                "last_updated": "2026-02-08",
            }
        )
    )
