"""
Health check module for FastAPI applications.

Provides a standardized health router with configurable health checks.

Example:
    ```python
    from fastapi import FastAPI
    from forge_shared.health import create_health_router

    app = FastAPI()
    app.include_router(
        create_health_router(
            name="my-service",
            version="1.0.0",
        ),
        prefix="/api"
    )
    ```
"""

from forge_shared.health.checks import (
    check_database,
    check_external_service,
    check_redis,
    health_check,
)
from forge_shared.health.router import (
    DependencyCheck,
    HealthCheck,
    HealthStatus,
    create_health_router,
)

__all__ = [
    "create_health_router",
    "HealthCheck",
    "HealthStatus",
    "DependencyCheck",
    "health_check",
    "check_database",
    "check_redis",
    "check_external_service",
]
