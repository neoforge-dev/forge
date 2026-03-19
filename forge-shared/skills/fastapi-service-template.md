---
name: fastapi-service-template
description: Scaffold complete FastAPI backends with standard structure, tooling, testing, and deployment configuration
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


## Related Skills

- `living-docs` - Document the new service
- `pwa-frontend-lite` - Create frontend for the API
- `compliance-playbook-writer` - Add compliance requirements
- `git-committer` - Commit with conventional commits

