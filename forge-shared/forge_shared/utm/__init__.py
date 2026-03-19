"""
UTM tracking module for cross-domain attribution.

Provides UTM parameter tracking, storage, and attribution for all FORGE products.

Example:
    from forge_shared.utm import UTMMiddleware, get_utm_params

    app.add_middleware(UTMMiddleware)

    @app.post("/signup")
    async def signup(request: Request):
        utm = get_utm_params(request)
        # Store utm with user for attribution
"""

from forge_shared.utm.middleware import UTMMiddleware
from forge_shared.utm.models import AttributionEvent, UTMParams
from forge_shared.utm.tracking import get_attribution, get_utm_params, store_utm

__all__ = [
    "UTMParams",
    "AttributionEvent",
    "UTMMiddleware",
    "get_utm_params",
    "store_utm",
    "get_attribution",
]
