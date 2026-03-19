"""
Authentication module for JWT-based authentication and authorization.

This module provides JWT token creation, validation, and dependency injection
for FastAPI applications. It supports role-based access control (RBAC) and
permission-based authorization.

Example:
    ```python
    from fastapi import FastAPI, Depends
    from forge_shared.auth import JWTAuth, get_current_user

    app = FastAPI()
    auth = JWTAuth(secret_key="your-secret")

    @app.get("/protected")
    async def protected(user: User = Depends(get_current_user)):
        return {"user": user.email}
    ```
"""

from forge_shared.auth.jwt import JWTAuth, JWTConfig
from forge_shared.auth.dependencies import (
    get_current_user,
    require_role,
    require_permission,
    get_optional_user,
    set_jwt_auth,
)
from forge_shared.auth.models import User, TokenPayload, UserRole, Permission

__all__ = [
    "JWTAuth",
    "JWTConfig",
    "get_current_user",
    "require_role",
    "require_permission",
    "get_optional_user",
    "set_jwt_auth",
    "User",
    "TokenPayload",
    "UserRole",
    "Permission",
]
