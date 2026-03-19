---
name: fastapi-service-template
description: Scaffold complete FastAPI backends with standard structure, tooling, testing, and deployment configuration
auto_execute: true
disable-model-invocation: false
allowed-tools: [Bash, Read, Write, Edit, Glob]
---

# FastAPI Service Template

Comprehensive skill for scaffolding production-ready FastAPI backends following FORGE portfolio patterns.

## When to Use

- Creating a new backend service from scratch
- Aligning existing backend with FORGE standards
- Adding standard tooling (Makefile, Docker, tests) to a project
- Setting up CI/CD and deployment configuration

## Quick Start

```
/fastapi-service-template create --name my-service --domain mydomain-com
```

---

## Project Structure

### Complete Directory Layout

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app, middleware, lifespan
│   ├── config.py               # Pydantic Settings configuration
│   ├── dependencies.py         # FastAPI dependencies (get_db, get_user)
│   ├── exceptions.py           # AppError, error codes, handlers
│   ├── security.py             # Password hashing, token utilities
│   │
│   ├── api/                    # API routes
│   │   ├── __init__.py
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   ├── router.py       # Main v1 router (includes all)
│   │   │   ├── auth.py         # Authentication endpoints
│   │   │   ├── users.py        # User management endpoints
│   │   │   ├── health.py       # Health check endpoint
│   │   │   └── {feature}.py    # Feature-specific routes
│   │   └── deps.py             # Shared API dependencies
│   │
│   ├── models/                 # SQLAlchemy models
│   │   ├── __init__.py
│   │   ├── base.py             # Base model with timestamps
│   │   ├── user.py             # User model
│   │   └── {feature}.py        # Feature models
│   │
│   ├── schemas/                # Pydantic schemas
│   │   ├── __init__.py
│   │   ├── user.py             # UserCreate, UserResponse, etc.
│   │   ├── auth.py             # LoginRequest, TokenResponse
│   │   └── {feature}.py        # Feature schemas
│   │
│   ├── services/               # Business logic
│   │   ├── __init__.py
│   │   ├── user_service.py     # User operations
│   │   ├── auth_service.py     # Authentication logic
│   │   └── {feature}_service.py
│   │
│   ├── infrastructure/         # External integrations
│   │   ├── __init__.py
│   │   ├── database.py         # AsyncSession, engine setup
│   │   ├── storage/            # S3, file storage
│   │   │   ├── __init__.py
│   │   │   └── s3_storage.py
│   │   └── clients/            # External API clients
│   │       ├── __init__.py
│   │       ├── stripe_client.py
│   │       └── openai_client.py
│   │
│   ├── middleware/             # Custom middleware
│   │   ├── __init__.py
│   │   ├── rate_limit.py       # Rate limiting
│   │   ├── request_id.py       # Request ID injection
│   │   └── security.py         # Security headers
│   │
│   ├── tasks/                  # Background tasks (optional)
│   │   ├── __init__.py
│   │   └── celery_app.py
│   │
│   └── utils/                  # Utilities
│       ├── __init__.py
│       └── helpers.py
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # Fixtures, test database
│   ├── unit/                   # Unit tests (no DB/external)
│   │   ├── __init__.py
│   │   └── test_services.py
│   ├── integration/            # Integration tests (with DB)
│   │   ├── __init__.py
│   │   └── test_api.py
│   └── e2e/                    # End-to-end tests
│       ├── __init__.py
│       └── test_workflows.py
│
├── alembic/                    # Database migrations
│   ├── versions/
│   ├── env.py
│   └── script.py.mako
│
├── .env.example                # Environment template
├── .python-version             # Python version (3.11+)
├── pyproject.toml              # Dependencies and config
├── Makefile                    # Development commands
├── Dockerfile                  # Production container
├── docker-compose.yml          # Local development
├── alembic.ini                 # Alembic configuration
└── README.md                   # Setup instructions
```

---

## pyproject.toml Template

```toml
[project]
name = "{{project-name}}-backend"
version = "0.1.0"
description = "{{description}}"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    # Core Framework
    "fastapi>=0.121.0",
    "uvicorn>=0.38.0",
    "pydantic-settings>=2.11.0",
    "python-multipart>=0.0.22",
    
    # Database
    "sqlalchemy>=2.0.44",
    "asyncpg>=0.30.0",
    "alembic>=1.17.1",
    "greenlet>=3.2.4",
    
    # Authentication
    "passlib>=1.7.4",
    "pyjwt[crypto]>=2.10.0",
    "bcrypt>=4.2.0",
    
    # Monitoring
    "sentry-sdk[fastapi]>=2.19.0",
    
    # Utilities
    "httpx>=0.28.1",
    "aiohttp>=3.13.3",
]

[dependency-groups]
dev = [
    "pytest>=8.4.2",
    "pytest-asyncio>=1.2.0",
    "pytest-cov>=7.0.0",
    "httpx>=0.28.1",
    "ruff>=0.14.3",
    "mypy>=1.18.2",
    "pip-audit>=2.10.0",
]

# Optional: AI features
ai = [
    "openai>=1.59.0",
    "anthropic>=0.72.0",
]

# Optional: Background tasks
tasks = [
    "celery>=5.5.3",
    "redis>=7.0.1",
]

# Optional: Storage
storage = [
    "aioboto3>=15.5.0",
    "boto3>=1.40.61",
]

# Optional: Payments
payments = [
    "stripe>=11.7.0",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B"]
ignore = []

[tool.ruff.lint.isort]
known-first-party = ["app"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --cov=app --cov-report=term-missing"
markers = [
    "integration: marks tests as integration tests",
    "e2e: marks tests as end-to-end tests",
]

[tool.coverage.run]
source = ["app"]
omit = ["*/tests/*", "*/__pycache__/*"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
]

[tool.mypy]
python_version = "3.11"
strict = true
plugins = ["pydantic.mypy"]
```

---

## Makefile Template

```makefile
.PHONY: help install dev test lint format check clean db-migrate db-rollback deploy

.DEFAULT_GOAL := help

# Variables
PYTHON := uv run python
PYTEST := uv run pytest
RUFF := uv run ruff
MYPY := uv run mypy
UVICORN := uv run uvicorn

# ============================================================================
# Help
# ============================================================================

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ============================================================================
# Development
# ============================================================================

install: ## Install dependencies with uv
	uv sync --all-extras --dev

dev: ## Start development server with hot reload
	$(UVICORN) app.main:app --reload --host 0.0.0.0 --port 8000

dev-debug: ## Start development server with debug logging
	$(UVICORN) app.main:app --reload --host 0.0.0.0 --port 8000 --log-level debug

shell: ## Start Python shell with app context
	$(PYTHON) -i -c "from app.main import app; from app.config import settings; print('App and settings loaded')"

# ============================================================================
# Testing
# ============================================================================

test: ## Run all tests with coverage
	$(PYTEST) tests/ -v --cov=app --cov-report=term-missing --cov-report=html

test-unit: ## Run unit tests only (fast, no DB)
	$(PYTEST) tests/unit/ -v

test-integration: ## Run integration tests only (with DB)
	$(PYTEST) tests/integration/ -v -m integration

test-e2e: ## Run end-to-end tests only
	$(PYTEST) tests/e2e/ -v -m e2e

test-watch: ## Run tests in watch mode
	$(PYTEST) tests/ -v --cov=app -f

test-failed: ## Re-run only failed tests
	$(PYTEST) tests/ -v --lf

test-fast: ## Run tests without coverage (faster)
	$(PYTEST) tests/ -v -x

# ============================================================================
# Code Quality
# ============================================================================

lint: ## Run ruff linter
	$(RUFF) check .

lint-fix: ## Run ruff linter with auto-fix
	$(RUFF) check . --fix

format: ## Format code with ruff
	$(RUFF) format .

format-check: ## Check code formatting without changes
	$(RUFF) format --check .

type-check: ## Run mypy type checker
	$(MYPY) app tests

security: ## Run security audit on dependencies
	uv run pip-audit

check: lint format-check type-check test ## Run all checks (CI pipeline)

# ============================================================================
# Database
# ============================================================================

db-migrate: ## Run database migrations
	$(PYTHON) -m alembic upgrade head

db-rollback: ## Rollback last database migration
	$(PYTHON) -m alembic downgrade -1

db-migration: ## Create new migration (usage: make db-migration MSG="add users table")
	$(PYTHON) -m alembic revision --autogenerate -m "$(MSG)"

db-reset: ## Reset database (drop all and re-migrate)
	$(PYTHON) -m alembic downgrade base
	$(PYTHON) -m alembic upgrade head

db-history: ## Show migration history
	$(PYTHON) -m alembic history

# ============================================================================
# Deployment
# ============================================================================

deploy-staging: ## Deploy to staging (Railway)
	@echo "🚀 Deploying to staging..."
	railway up --environment staging

deploy-prod: ## Deploy to production (with confirmation)
	@echo "⚠️  Deploying to production..."
	@read -p "Are you sure? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		railway up --environment production; \
	fi

# ============================================================================
# Docker
# ============================================================================

docker-build: ## Build Docker image
	docker build -t $(shell basename $(CURDIR)) .

docker-run: ## Run Docker container
	docker run -p 8000:8000 --env-file .env $(shell basename $(CURDIR))

docker-up: ## Start docker-compose stack
	docker-compose up --build

docker-down: ## Stop docker-compose stack
	docker-compose down -v

# ============================================================================
# Cleanup
# ============================================================================

clean: ## Remove generated files and caches
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/ .coverage coverage.xml
```

---

## Dockerfile Template

```dockerfile
# ============================================================================
# {{project-name}} Backend Dockerfile
# ============================================================================
# Multi-stage build for minimal production image

# Stage 1: Builder
FROM python:3.11-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Export and install dependencies
RUN uv export --frozen --no-dev --no-hashes -o requirements.txt 2>/dev/null || \
    uv pip compile pyproject.toml -o requirements.txt
RUN pip install --no-cache-dir --target=/deps -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim

# Install runtime dependencies only
RUN apt-get update && apt-get install -y \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Copy dependencies from builder
COPY --from=builder /deps /usr/local/lib/python3.11/site-packages/

# Copy application code
COPY --chown=appuser:appuser . .

# Switch to non-root user
USER appuser

# Expose port (Railway uses $PORT)
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/api/v1/health || exit 1

# Run with uvicorn
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "${PORT:-8000}"]
```

---

## docker-compose.yml Template

```yaml
version: "3.8"

services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/app
      - ENVIRONMENT=development
      - DEBUG=true
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - .:/app
    command: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

  db:
    image: postgres:15-alpine
    environment:
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
      - POSTGRES_DB=app
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5

  # Optional: Redis for caching/tasks
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    profiles:
      - with-redis

volumes:
  postgres_data:
```

---

## Core File Templates

### app/main.py

```python
"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import settings
from app.infrastructure.database import engine
from app.middleware.request_id import RequestIDMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Startup
    yield
    # Shutdown
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    lifespan=lifespan,
)

# Middleware (order matters - first added = last executed)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(api_router, prefix="/api/v1")
```

### app/config.py

```python
"""Application configuration using Pydantic Settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    
    # Application
    app_name: str = "{{project-name}}"
    environment: str = "development"
    debug: bool = False
    
    # Database
    database_url: str = "postgresql+asyncpg://localhost/app"
    
    # Security
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 30
    
    # CORS
    cors_origins: list[str] = ["http://localhost:3000"]
    
    # Optional: External services
    # stripe_secret_key: str | None = None
    # openai_api_key: str | None = None
    # sentry_dsn: str | None = None
    
    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
```

### app/exceptions.py

```python
"""Application exceptions and error handling."""

from enum import Enum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ErrorCode(str, Enum):
    """Machine-readable error codes."""
    
    # Authentication
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    UNAUTHORIZED = "UNAUTHORIZED"
    
    # Resources
    NOT_FOUND = "NOT_FOUND"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    
    # Validation
    VALIDATION_ERROR = "VALIDATION_ERROR"
    
    # Server
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class AppError(Exception):
    """Base application error with structured response."""
    
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "details": self.details,
            }
        }


def register_exception_handlers(app: FastAPI) -> None:
    """Register custom exception handlers."""
    
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )
```

---

## Testing Templates

### tests/conftest.py

```python
"""Pytest configuration and fixtures."""

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.base import Base


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create test database engine."""
    # Use test database URL (append _test or use separate DB)
    test_url = settings.database_url.replace("/app", "/app_test")
    
    engine = create_async_engine(
        test_url,
        echo=False,
        poolclass=NullPool,
    )
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Create test database session with rollback."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    
    session_factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client for API tests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def test_user() -> dict:
    """Test user data."""
    return {
        "id": "test-user-123",
        "email": "test@example.com",
    }
```

### tests/unit/test_services.py

```python
"""Unit tests for services (no DB required)."""

import pytest


class TestExampleService:
    """Example service unit tests."""
    
    def test_example_logic(self):
        """Test pure business logic."""
        # Unit tests should not require DB or external services
        result = 1 + 1
        assert result == 2
    
    def test_validation_logic(self):
        """Test validation without side effects."""
        # Test input validation, transformations, etc.
        pass
```

### tests/integration/test_api.py

```python
"""Integration tests for API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.integration
class TestHealthEndpoint:
    """Health check endpoint tests."""
    
    @pytest.mark.asyncio
    async def test_health_check(self, client: AsyncClient):
        """Test health endpoint returns OK."""
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


@pytest.mark.integration
class TestAuthEndpoints:
    """Authentication endpoint tests."""
    
    @pytest.mark.asyncio
    async def test_login_invalid_credentials(self, client: AsyncClient):
        """Test login with invalid credentials."""
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "wrong@test.com", "password": "wrong"},
        )
        assert response.status_code == 401
```

---

## .env.example Template

```bash
# Application
APP_NAME={{project-name}}
ENVIRONMENT=development
DEBUG=true
SECRET_KEY=change-me-in-production-use-secrets-generate

# Database
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/app

# CORS (comma-separated origins)
CORS_ORIGINS=http://localhost:3000,http://localhost:5173

# Optional: Sentry error tracking
# SENTRY_DSN=https://...@sentry.io/...

# Optional: Stripe payments
# STRIPE_SECRET_KEY=sk_test_...
# STRIPE_WEBHOOK_SECRET=whsec_...

# Optional: OpenAI
# OPENAI_API_KEY=sk-...

# Optional: AWS S3
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
# S3_BUCKET_NAME=...
# S3_REGION=us-east-1
```

---

## Workflow

### Step 1: Generate Scaffold

```bash
# Create directory structure
mkdir -p backend/{app/{api/v1,models,schemas,services,infrastructure,middleware,utils},tests/{unit,integration,e2e},alembic/versions}

# Create __init__.py files
find backend -type d -exec touch {}/__init__.py \;

# Create core files
touch backend/app/{main,config,dependencies,exceptions,security}.py
touch backend/{pyproject.toml,Makefile,Dockerfile,.env.example,.python-version}
```

### Step 2: Configure Dependencies

1. Copy pyproject.toml template
2. Add project-specific dependencies
3. Run `uv sync --all-extras --dev`

### Step 3: Set Up Database

```bash
# Initialize alembic
cd backend && alembic init alembic

# Edit alembic/env.py to use async engine
# Create initial migration
make db-migration MSG="initial"
make db-migrate
```

### Step 4: Implement Features

1. Define models in `app/models/`
2. Create schemas in `app/schemas/`
3. Implement services in `app/services/`
4. Add routes in `app/api/v1/`
5. Write tests alongside implementation

### Step 5: Quality Gates

```bash
make check  # lint + format + type-check + test
```

---

## Checklist

- [ ] Directory structure matches template
- [ ] pyproject.toml has all required dependencies
- [ ] Makefile has all standard targets
- [ ] Dockerfile builds and runs
- [ ] docker-compose.yml works for local dev
- [ ] .env.example documents all variables
- [ ] Health endpoint at `/api/v1/health`
- [ ] Tests run with `make test`
- [ ] Code passes `make check`
- [ ] README has setup instructions

---

## Railway Deployment

### railway.toml

```toml
[build]
builder = "dockerfile"
dockerfilePath = "./Dockerfile"

[deploy]
restartPolicyType = "on_failure"
restartPolicyMaxRetries = 3

# Optional: Health check configuration
# healthcheckPath = "/api/v1/health"
# healthcheckTimeout = 10
```

### Railway Environment Variables

Set these in Railway dashboard or CLI:

```bash
# Required
DATABASE_URL=postgresql+asyncpg://...  # Railway provides this
SECRET_KEY=your-production-secret       # Generate with: openssl rand -hex 32
ENVIRONMENT=production
DEBUG=false

# Optional
CORS_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
SENTRY_DSN=https://...@sentry.io/...
```

### Railway CLI Deployment

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and link project
railway login
railway link

# Deploy
railway up

# View logs
railway logs

# Open dashboard
railway open
```

---

## Alembic Configuration

### alembic.ini

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
version_path_separator = os

sqlalchemy.url = driver://user:pass@localhost/dbname

[post_write_hooks]
hooks = ruff
ruff.type = exec
ruff.executable = ruff
ruff.options = format REVISION_SCRIPT_FILENAME

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

### alembic/env.py (Async)

```python
"""Alembic environment configuration for async SQLAlchemy."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.models.base import Base

# Import all models to register with metadata
from app.models import user  # noqa: F401
# from app.models import {feature}  # Add all model imports

config = context.config

# Override database URL from settings
config.set_main_option("sqlalchemy.url", str(settings.database_url))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with the given connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in async mode."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

---

## Additional File Templates

### app/infrastructure/database.py

```python
"""Database configuration and session management."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


# Create async engine
engine: AsyncEngine = create_async_engine(
    str(settings.database_url),
    echo=settings.debug,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

# Session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for database sessions."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for database sessions (use in services/tasks)."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
```

### app/models/base.py

```python
"""Base model with common fields and utilities."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def generate_uuid() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Base class for all database models."""
    pass


class TimestampMixin:
    """Mixin to add timestamp fields to models."""
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class UUIDMixin:
    """Mixin to use UUID as primary key."""
    
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )


class BaseModel(Base, UUIDMixin, TimestampMixin):
    """Base model with UUID primary key and timestamps.
    
    Inherit from this for standard entities:
    
        class User(BaseModel):
            __tablename__ = "users"
            email: Mapped[str] = mapped_column(String(255), unique=True)
    """
    
    __abstract__ = True
```

### app/models/user.py

```python
"""User model."""

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class User(BaseModel):
    """User account model."""
    
    __tablename__ = "users"
    
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    
    def __repr__(self) -> str:
        return f"<User {self.email}>"
```

### app/api/v1/router.py

```python
"""API v1 router - aggregates all endpoint routers."""

from fastapi import APIRouter

from app.api.v1 import auth, health, users

api_router = APIRouter()

# Health check (no auth required)
api_router.include_router(
    health.router,
    tags=["health"],
)

# Authentication
api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["auth"],
)

# User management
api_router.include_router(
    users.router,
    prefix="/users",
    tags=["users"],
)

# Add feature routers below:
# api_router.include_router(
#     {feature}.router,
#     prefix="/{feature}",
#     tags=["{feature}"],
# )
```

### app/api/v1/health.py

```python
"""Health check endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_db

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response."""
    
    status: str
    timestamp: datetime
    version: str = "0.1.0"
    database: str = "unknown"


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """Full health check including database connectivity."""
    db_status = "healthy"
    
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "unhealthy"
    
    return HealthResponse(
        status="healthy" if db_status == "healthy" else "degraded",
        timestamp=datetime.utcnow(),
        database=db_status,
    )


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """Kubernetes liveness probe - is the process running?"""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Kubernetes readiness probe - can we serve traffic?"""
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        return {"status": "not ready"}
```

### app/middleware/request_id.py

```python
"""Request ID middleware for request tracing."""

import uuid
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Add unique request ID to each request for tracing."""
    
    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        # Check for existing request ID (from load balancer)
        request_id = request.headers.get("X-Request-ID")
        
        if not request_id:
            request_id = str(uuid.uuid4())
        
        # Store in request state for access in routes
        request.state.request_id = request_id
        
        # Process request
        response = await call_next(request)
        
        # Add to response headers
        response.headers["X-Request-ID"] = request_id
        
        return response
```

### app/security.py

```python
"""Security utilities for authentication."""

from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from app.config import settings

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
ALGORITHM = "HS256"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)


def create_access_token(
    subject: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Create JWT access token."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.access_token_expire_minutes
        )
    
    to_encode = {
        "sub": subject,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    """Decode and validate JWT token."""
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
        )
        return payload
    except jwt.PyJWTError:
        return None
```

### app/dependencies.py

```python
"""FastAPI dependencies for authentication and authorization."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_db
from app.models.user import User
from app.security import decode_token

security = HTTPBearer()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Get current authenticated user from JWT token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    token = credentials.credentials
    payload = decode_token(token)
    
    if payload is None:
        raise credentials_exception
    
    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise credentials_exception
    
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if user is None:
        raise credentials_exception
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )
    
    return user


async def get_current_superuser(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require superuser role."""
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superuser required",
        )
    return current_user


# Type aliases for cleaner route signatures
CurrentUser = Annotated[User, Depends(get_current_user)]
SuperUser = Annotated[User, Depends(get_current_superuser)]
DB = Annotated[AsyncSession, Depends(get_db)]
```

---

## GitHub Actions CI/CD

### .github/workflows/ci.yml

```yaml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

env:
  PYTHON_VERSION: "3.11"

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
      
      - name: Set up Python
        run: uv python install ${{ env.PYTHON_VERSION }}
      
      - name: Install dependencies
        run: uv sync --all-extras --dev
        working-directory: backend
      
      - name: Run ruff lint
        run: uv run ruff check .
        working-directory: backend
      
      - name: Run ruff format check
        run: uv run ruff format --check .
        working-directory: backend

  type-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
      
      - name: Set up Python
        run: uv python install ${{ env.PYTHON_VERSION }}
      
      - name: Install dependencies
        run: uv sync --all-extras --dev
        working-directory: backend
      
      - name: Run mypy
        run: uv run mypy app
        working-directory: backend

  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15-alpine
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: test_db
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 5432:5432
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
      
      - name: Set up Python
        run: uv python install ${{ env.PYTHON_VERSION }}
      
      - name: Install dependencies
        run: uv sync --all-extras --dev
        working-directory: backend
      
      - name: Run tests
        run: uv run pytest -v --cov=app --cov-report=xml
        working-directory: backend
        env:
          DATABASE_URL: postgresql+asyncpg://postgres:postgres@localhost:5432/test_db
          SECRET_KEY: test-secret-key
          ENVIRONMENT: test
      
      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          files: backend/coverage.xml
          fail_ci_if_error: false

  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
      
      - name: Set up Python
        run: uv python install ${{ env.PYTHON_VERSION }}
      
      - name: Install dependencies
        run: uv sync --all-extras --dev
        working-directory: backend
      
      - name: Run pip-audit
        run: uv run pip-audit
        working-directory: backend
        continue-on-error: true  # Don't fail on vulnerabilities, just report
```

### .github/workflows/deploy.yml

```yaml
name: Deploy

on:
  push:
    branches: [main]
    paths:
      - 'backend/**'
  workflow_dispatch:

jobs:
  deploy-staging:
    runs-on: ubuntu-latest
    environment: staging
    steps:
      - uses: actions/checkout@v4
      
      - name: Install Railway CLI
        run: npm install -g @railway/cli
      
      - name: Deploy to Railway (staging)
        run: railway up --environment staging
        working-directory: backend
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}

  deploy-production:
    needs: deploy-staging
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: actions/checkout@v4
      
      - name: Install Railway CLI
        run: npm install -g @railway/cli
      
      - name: Deploy to Railway (production)
        run: railway up --environment production
        working-directory: backend
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
```

---

## Service Layer Pattern

### app/services/user_service.py

```python
"""User service - business logic for user operations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate
from app.security import get_password_hash, verify_password


class UserService:
    """Service for user operations."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_by_id(self, user_id: str) -> User | None:
        """Get user by ID."""
        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()
    
    async def get_by_email(self, email: str) -> User | None:
        """Get user by email."""
        result = await self.db.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()
    
    async def create(self, user_in: UserCreate) -> User:
        """Create new user."""
        user = User(
            email=user_in.email,
            hashed_password=get_password_hash(user_in.password),
            full_name=user_in.full_name,
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user
    
    async def update(self, user: User, user_in: UserUpdate) -> User:
        """Update existing user."""
        update_data = user_in.model_dump(exclude_unset=True)
        
        if "password" in update_data:
            update_data["hashed_password"] = get_password_hash(
                update_data.pop("password")
            )
        
        for field, value in update_data.items():
            setattr(user, field, value)
        
        await self.db.commit()
        await self.db.refresh(user)
        return user
    
    async def authenticate(
        self, email: str, password: str
    ) -> User | None:
        """Authenticate user with email and password."""
        user = await self.get_by_email(email)
        
        if not user:
            return None
        
        if not verify_password(password, user.hashed_password):
            return None
        
        return user
    
    async def delete(self, user: User) -> None:
        """Delete user."""
        await self.db.delete(user)
        await self.db.commit()
```

### app/schemas/user.py

```python
"""User schemas for request/response validation."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserBase(BaseModel):
    """Shared user properties."""
    
    email: EmailStr
    full_name: str | None = None


class UserCreate(UserBase):
    """Properties for user creation."""
    
    password: str = Field(..., min_length=8)


class UserUpdate(BaseModel):
    """Properties for user update (all optional)."""
    
    email: EmailStr | None = None
    full_name: str | None = None
    password: str | None = Field(None, min_length=8)


class UserResponse(UserBase):
    """User response schema (excludes sensitive data)."""
    
    id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}


class UserInDB(UserResponse):
    """User with hashed password (internal use only)."""
    
    hashed_password: str
```

---

## Seed Data Script

### scripts/seed.py

```python
#!/usr/bin/env python
"""Seed database with initial data."""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.infrastructure.database import async_session_factory
from app.models.user import User
from app.security import get_password_hash


async def seed_admin_user() -> None:
    """Create default admin user if not exists."""
    async with async_session_factory() as session:
        from sqlalchemy import select
        
        result = await session.execute(
            select(User).where(User.email == "admin@example.com")
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            print("Admin user already exists")
            return
        
        admin = User(
            email="admin@example.com",
            hashed_password=get_password_hash("changeme123"),
            full_name="Admin User",
            is_superuser=True,
        )
        session.add(admin)
        await session.commit()
        print(f"Created admin user: {admin.email}")


async def main() -> None:
    """Run all seed functions."""
    print("Seeding database...")
    await seed_admin_user()
    # Add more seed functions here
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
```

Add to Makefile:

```makefile
seed: ## Seed database with initial data
	$(PYTHON) scripts/seed.py
```

---

## README Template

### backend/README.md

```markdown
# {{project-name}} Backend

FastAPI backend service for {{project-description}}.

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- PostgreSQL 15+

### Local Development

```bash
# Install dependencies
make install

# Copy environment file
cp .env.example .env
# Edit .env with your settings

# Start PostgreSQL (if using docker-compose)
docker-compose up -d db

# Run migrations
make db-migrate

# Seed initial data
make seed

# Start development server
make dev
```

API will be available at http://localhost:8000

- Swagger docs: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### Running Tests

```bash
# All tests with coverage
make test

# Unit tests only (fast)
make test-unit

# Integration tests only
make test-integration

# Run specific test file
uv run pytest tests/unit/test_services.py -v
```

### Code Quality

```bash
# Run all checks (lint + format + type-check + test)
make check

# Individual commands
make lint       # Check linting
make lint-fix   # Auto-fix linting issues
make format     # Format code
make type-check # Run mypy
```

### Database Operations

```bash
# Run migrations
make db-migrate

# Create new migration
make db-migration MSG="add posts table"

# Rollback last migration
make db-rollback

# Reset database
make db-reset
```

## Project Structure

```
backend/
├── app/
│   ├── api/v1/           # API routes
│   ├── models/           # SQLAlchemy models
│   ├── schemas/          # Pydantic schemas
│   ├── services/         # Business logic
│   └── infrastructure/   # Database, external services
├── tests/
│   ├── unit/            # Unit tests
│   ├── integration/     # Integration tests
│   └── conftest.py      # Test fixtures
├── alembic/             # Database migrations
├── scripts/             # Utility scripts
├── pyproject.toml       # Dependencies
├── Makefile            # Development commands
└── Dockerfile          # Production container
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection URL | Required |
| `SECRET_KEY` | JWT signing key | Required |
| `ENVIRONMENT` | `development`, `staging`, `production` | `development` |
| `DEBUG` | Enable debug mode | `false` |
| `CORS_ORIGINS` | Comma-separated allowed origins | `http://localhost:3000` |

## Deployment

### Railway

```bash
# Deploy to staging
make deploy-staging

# Deploy to production
make deploy-prod
```

### Docker

```bash
# Build image
make docker-build

# Run container
make docker-run
```

## API Documentation

Once running, view interactive API documentation:

- **Swagger UI**: `/docs`
- **ReDoc**: `/redoc`
- **OpenAPI JSON**: `/openapi.json`

## License

Proprietary - All rights reserved.
```

---

## Related Skills

- `living-docs` - Document the new service
- `pwa-frontend-lite` - Create frontend for the API
- `compliance-playbook-writer` - Add compliance requirements
- `git-committer` - Commit with conventional commits

---

*Template based on: Interview Simulator, Voice Coach, Study Flow, Allergen Guardian, MVP Validator backends*  
*Updated: February 5, 2026*
