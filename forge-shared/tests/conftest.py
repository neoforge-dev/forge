"""
Pytest configuration and shared fixtures for forge-shared.

Provides common fixtures and configuration for all test modules including:
- FastAPI application fixtures
- Authentication fixtures (JWT, users)
- Analytics fixtures (PostHog)
- Database mocks (Redis, PostgreSQL)
- HTTP client fixtures
- Async test support

Usage:
    Simply import fixtures in your test files - they are auto-discovered.

Example:
    def test_with_auth(jwt_auth, test_user):
        token = jwt_auth.create_access_token(user_id=test_user.id, ...)
        assert token is not None
"""

import asyncio
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx import AsyncClient

from forge_shared.analytics import PostHogClient
from forge_shared.auth import JWTAuth
from forge_shared.auth.models import User
from forge_shared.config import BaseConfig


@pytest.fixture
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """
    Create event loop for async tests.

    Yields:
        Event loop instance
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def test_config() -> BaseConfig:
    """
    Get test configuration.

    Returns:
        BaseConfig instance with test settings
    """
    return BaseConfig(
        app_name="test-app",
        environment="testing",
        debug=True,
        secret_key="test-secret-key",
        database_url="sqlite:///:memory:",
        redis_url="redis://localhost:6379/1",
    )


@pytest.fixture
def jwt_auth() -> JWTAuth:
    """
    Get JWT auth instance for testing.

    Returns:
        JWTAuth instance configured for testing
    """
    return JWTAuth(
        secret_key="test-secret-key",
        algorithm="HS256",
        access_token_expire_minutes=30,
    )


@pytest.fixture
def test_user() -> User:
    """
    Get test user.

    Returns:
        User instance for testing
    """
    return User(
        id="test-user-123",
        email="test@example.com",
        roles=["user"],
        permissions=["read:own"],
    )


@pytest.fixture
def test_user_admin() -> User:
    """
    Get test admin user.

    Returns:
        Admin User instance for testing
    """
    return User(
        id="admin-user-123",
        email="admin@example.com",
        roles=["admin", "user"],
        permissions=["read:all", "write:all"],
    )


@pytest.fixture
def posthog_client() -> PostHogClient:
    """
    Get PostHog client for testing.

    Returns:
        PostHogClient instance with disabled analytics
    """
    return PostHogClient(api_key="test-key", enabled=False)


@pytest.fixture
def fastapi_app() -> FastAPI:
    """
    Get FastAPI app for testing.

    Returns:
        FastAPI application instance
    """
    app = FastAPI()

    @app.get("/")
    async def root():
        return {"message": "Hello, World!"}

    @app.get("/protected")
    async def protected():
        return {"message": "Protected"}

    return app


@pytest.fixture
def test_client(fastapi_app: FastAPI) -> TestClient:
    """
    Get test client for FastAPI app.

    Args:
        fastapi_app: FastAPI application

    Returns:
        TestClient instance
    """
    return TestClient(fastapi_app)


@pytest.fixture
def mock_request() -> Request:
    """
    Get mock request object.

    Returns:
        Mock Request object
    """
    return MagicMock(spec=Request)


@pytest.fixture
async def async_mock() -> AsyncMock:
    """
    Get async mock.

    Returns:
        AsyncMock instance
    """
    return AsyncMock()


@pytest.fixture
def redis_mock() -> MagicMock:
    """
    Get Redis mock.

    Returns:
        Mocked Redis client with common async operations
    """
    mock = MagicMock()
    mock.zremrangebyscore = AsyncMock(return_value=0)
    mock.zcard = AsyncMock(return_value=0)
    mock.zadd = AsyncMock(return_value=1)
    mock.zrange = AsyncMock(return_value=[])
    mock.expire = AsyncMock(return_value=True)
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    mock.setex = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=1)
    mock.exists = AsyncMock(return_value=0)
    mock.incr = AsyncMock(return_value=1)
    mock.decr = AsyncMock(return_value=0)
    mock.hget = AsyncMock(return_value=None)
    mock.hset = AsyncMock(return_value=1)
    mock.hgetall = AsyncMock(return_value={})
    mock.ping = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def db_session_mock() -> MagicMock:
    """
    Get database session mock.

    Returns:
        Mocked SQLAlchemy async session
    """
    mock = MagicMock()
    mock.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    mock.close = AsyncMock()
    mock.refresh = AsyncMock()
    mock.add = MagicMock()
    mock.delete = AsyncMock()
    return mock


@pytest.fixture
def stripe_mock() -> MagicMock:
    """
    Get Stripe API mock.

    Returns:
        Mocked Stripe client
    """
    mock = MagicMock()

    # Checkout session mock
    mock.checkout.Session.create = MagicMock(return_value=MagicMock(
        id="cs_test_123",
        url="https://checkout.stripe.com/test",
        status="open",
    ))

    # Customer mock
    mock.Customer.create = MagicMock(return_value=MagicMock(
        id="cus_test_123",
        email="test@example.com",
    ))

    # Subscription mock
    mock.Subscription.retrieve = MagicMock(return_value=MagicMock(
        id="sub_test_123",
        status="active",
        current_period_end=int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp()),
    ))

    return mock


@pytest.fixture
def http_client_mock() -> MagicMock:
    """
    Get HTTP client mock for external API calls.

    Returns:
        Mocked httpx client
    """
    mock = MagicMock()
    mock.get = AsyncMock(return_value=MagicMock(
        status_code=200,
        json=MagicMock(return_value={}),
        text="",
    ))
    mock.post = AsyncMock(return_value=MagicMock(
        status_code=200,
        json=MagicMock(return_value={}),
        text="",
    ))
    return mock


@pytest.fixture
async def async_client(fastapi_app: FastAPI) -> AsyncClient:
    """
    Get async HTTP client for FastAPI app testing.

    Args:
        fastapi_app: FastAPI application

    Returns:
        Async HTTP client
    """
    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        yield client


@pytest.fixture
def sample_jwt_token(jwt_auth: JWTAuth, test_user: User) -> str:
    """
    Get a sample JWT token for testing.

    Args:
        jwt_auth: JWT auth fixture
        test_user: Test user fixture

    Returns:
        JWT access token string
    """
    return jwt_auth.create_access_token(
        user_id=test_user.id,
        email=test_user.email,
        roles=test_user.roles,
        permissions=test_user.permissions,
    )


@pytest.fixture
def auth_headers(sample_jwt_token: str) -> dict[str, str]:
    """
    Get authorization headers with JWT token.

    Args:
        sample_jwt_token: JWT token

    Returns:
        Headers dict with Authorization
    """
    return {"Authorization": f"Bearer {sample_jwt_token}"}


@pytest.fixture
def api_key_headers() -> dict[str, str]:
    """
    Get headers with API key.

    Returns:
        Headers dict with X-API-Key
    """
    return {"X-API-Key": "test-api-key-123"}


@pytest.fixture
def mock_datetime() -> MagicMock:
    """
    Get mocked datetime for time-dependent tests.

    Returns:
        Mocked datetime module
    """
    mock = MagicMock()
    mock.now.return_value = datetime(2026, 2, 5, 12, 0, 0, tzinfo=timezone.utc)
    mock.utcnow.return_value = datetime(2026, 2, 5, 12, 0, 0)
    return mock


# Test markers
pytest_plugins = []


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests (fast, no external deps)")
    config.addinivalue_line("markers", "integration: Integration tests (may need external services)")
    config.addinivalue_line("markers", "slow: Slow running tests (>1s)")
    config.addinivalue_line("markers", "redis: Tests requiring Redis connection")
    config.addinivalue_line("markers", "stripe: Tests requiring Stripe mocks")
    config.addinivalue_line("markers", "database: Tests requiring database")
    config.addinivalue_line("markers", "auth: Authentication/authorization tests")
    config.addinivalue_line("markers", "billing: Billing/subscription tests")
    config.addinivalue_line("markers", "analytics: Analytics/tracking tests")


def pytest_collection_modifyitems(config, items):
    """
    Automatically mark tests based on their location.

    Tests in test_integration/ are marked as integration.
    """
    # Known markers from pytest_configure
    known_markers = {"unit", "integration", "slow", "redis", "stripe", "database", "auth", "billing", "analytics"}

    for item in items:
        # Auto-mark based on path
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)

        # Auto-mark based on test directory name
        path_parts = str(item.fspath).split("/")
        for part in path_parts:
            if part.startswith("test_"):
                module_name = part.replace("test_", "").replace(".py", "")
                # Only add marker if it's a known registered marker
                if module_name in known_markers:
                    item.add_marker(getattr(pytest.mark, module_name))
