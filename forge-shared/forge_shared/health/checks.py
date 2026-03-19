"""
Standard health check functions for common dependencies.

Provides async check functions for database, Redis, and external services.
These are intentionally simple stubs that can be overridden by callers.
"""


async def health_check(**kwargs: object) -> dict[str, str]:
    """Basic health check returning healthy status."""
    return {"status": "healthy"}


async def check_database(**kwargs: object) -> dict[str, str]:
    """Database connectivity check stub."""
    return {"database": "ok"}


async def check_redis(**kwargs: object) -> dict[str, str]:
    """Redis connectivity check stub."""
    return {"redis": "ok"}


async def check_external_service(
    host: str = "localhost",
    timeout: float = 5.0,
    **kwargs: object,
) -> dict[str, str]:
    """External service health check stub."""
    return {"status": "ok", "host": host}
