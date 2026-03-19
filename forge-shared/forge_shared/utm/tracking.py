"""
UTM tracking functions for attribution.
"""

import json
import logging
from typing import Any

from fastapi import Request

from forge_shared.utm.models import UTMParams

logger = logging.getLogger(__name__)

UTM_REDIS_PREFIX = "utm:attribution:"
UTM_REDIS_TTL = 90 * 24 * 60 * 60


def get_utm_params(request: Request) -> UTMParams:
    """
    Get UTM parameters from request state.

    Args:
        request: FastAPI request object

    Returns:
        UTMParams from request.state or empty UTMParams
    """
    return getattr(request.state, "utm", UTMParams())


async def store_utm(
    user_id: str,
    utm: UTMParams,
    redis_client: Any | None = None,
) -> None:
    """
    Store UTM attribution for a user.

    Args:
        user_id: User identifier
        utm: UTM parameters to store
        redis_client: Optional Redis client for persistence
    """
    if utm.is_empty():
        logger.debug(f"Skipping empty UTM storage for user {user_id}")
        return

    if redis_client is None:
        logger.warning(f"No Redis client provided, UTM not stored for user {user_id}")
        return

    key = f"{UTM_REDIS_PREFIX}{user_id}"
    try:
        await redis_client.setex(key, UTM_REDIS_TTL, json.dumps(utm.to_dict()))
        logger.info(f"Stored UTM attribution for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to store UTM for user {user_id}: {e}")


async def get_attribution(
    user_id: str,
    redis_client: Any | None = None,
) -> UTMParams | None:
    """
    Retrieve UTM attribution for a user.

    Args:
        user_id: User identifier
        redis_client: Optional Redis client for retrieval

    Returns:
        UTMParams if found, None otherwise
    """
    if redis_client is None:
        logger.warning(f"No Redis client provided, cannot retrieve UTM for user {user_id}")
        return None

    key = f"{UTM_REDIS_PREFIX}{user_id}"
    try:
        data = await redis_client.get(key)
        if data:
            return UTMParams.from_dict(json.loads(data))
        return None
    except Exception as e:
        logger.error(f"Failed to retrieve UTM for user {user_id}: {e}")
        return None
