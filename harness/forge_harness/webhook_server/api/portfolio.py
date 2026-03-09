"""Portfolio API Endpoints

REST API endpoints for portfolio management.
Extracted from webhook_server_main.py for better modularity.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.core.dependencies import verify_auth
from forge_harness.webhook_server.services.portfolio_service import (
    PortfolioService,
    get_portfolio_service,
)

logger = get_logger(__name__)

router = APIRouter()


def get_portfolio_service_dep() -> PortfolioService:
    """Dependency to get portfolio service instance."""
    return get_portfolio_service()


def api_response(data: Any = None, error_code: str | None = None, error_message: str | None = None) -> dict[str, Any]:
    """Create a standardized API response."""
    from datetime import UTC, datetime
    
    if error_code or error_message:
        return {
            "success": False,
            "data": None,
            "error": {
                "code": error_code or "UNKNOWN_ERROR",
                "message": error_message or "An error occurred",
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }
    return {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/api/portfolio")
async def get_portfolio_summary(
    _: None = Depends(verify_auth),
    portfolio_service: PortfolioService = Depends(get_portfolio_service_dep),
):
    """Get portfolio summary with project counts by status."""
    summary = portfolio_service.get_portfolio_summary()
    return JSONResponse(content=api_response(summary))


@router.get("/api/portfolio/{domain}")
async def get_domain_projects(
    domain: str,
    _: None = Depends(verify_auth),
    portfolio_service: PortfolioService = Depends(get_portfolio_service_dep),
):
    """Get projects in a domain."""
    result = portfolio_service.get_domain_projects(domain)
    if result is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    return JSONResponse(content=api_response(result))


@router.get("/api/portfolio/{domain}/{project}")
async def get_project_details(
    domain: str,
    project: str,
    _: None = Depends(verify_auth),
    portfolio_service: PortfolioService = Depends(get_portfolio_service_dep),
):
    """Get detailed project information."""
    result = portfolio_service.get_project_details(domain, project)
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return JSONResponse(content=api_response(result))
