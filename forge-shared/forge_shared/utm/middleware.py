"""
UTM tracking middleware for FastAPI.
"""

import json
import logging
from datetime import datetime

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from forge_shared.utm.models import UTMParams

logger = logging.getLogger(__name__)

UTM_COOKIE_NAME = "forge_utm"
UTM_COOKIE_MAX_AGE = 30 * 24 * 60 * 60


class UTMMiddleware(BaseHTTPMiddleware):
    """
    Middleware for extracting and persisting UTM parameters.

    Extracts UTM params from query string on first visit, stores in cookie,
    and attaches to request.state for use in routes.
    """

    def __init__(
        self,
        app: ASGIApp,
        cookie_name: str = UTM_COOKIE_NAME,
        cookie_max_age: int = UTM_COOKIE_MAX_AGE,
        cookie_domain: str | None = None,
    ) -> None:
        super().__init__(app)
        self.cookie_name = cookie_name
        self.cookie_max_age = cookie_max_age
        self.cookie_domain = cookie_domain

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        utm_params = self._extract_utm_params(request)
        request.state.utm = utm_params

        response = await call_next(request)

        if not utm_params.is_empty():
            self._set_utm_cookie(response, utm_params)

        return response

    def _extract_utm_params(self, request: Request) -> UTMParams:
        """Extract UTM params from query string or cookie."""
        query_params = request.query_params

        source = query_params.get("utm_source")
        medium = query_params.get("utm_medium")
        campaign = query_params.get("utm_campaign")
        content = query_params.get("utm_content")
        term = query_params.get("utm_term")

        if any([source, medium, campaign]):
            return UTMParams(
                source=source,
                medium=medium,
                campaign=campaign,
                content=content,
                term=term,
                landing_page=str(request.url),
                referrer=request.headers.get("referer"),
                timestamp=datetime.utcnow(),
            )

        cookie_value = request.cookies.get(self.cookie_name)
        if cookie_value:
            try:
                data = json.loads(cookie_value)
                return UTMParams.from_dict(data)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to parse UTM cookie: {e}")

        return UTMParams()

    def _set_utm_cookie(self, response: Response, utm_params: UTMParams) -> None:
        """Set UTM cookie on response."""
        cookie_value = json.dumps(utm_params.to_dict())

        response.set_cookie(
            key=self.cookie_name,
            value=cookie_value,
            max_age=self.cookie_max_age,
            httponly=True,
            secure=True,
            samesite="lax",
            domain=self.cookie_domain,
        )
