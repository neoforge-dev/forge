# forge-shared

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Coverage](https://img.shields.io/badge/coverage-80%25-brightgreen.svg)](badge.svg)

Shared services library for the FORGE portfolio - providing authentication, analytics, middleware, and utilities for production-ready FastAPI applications.

## Overview

`forge-shared` is a comprehensive Python library that consolidates common functionality across all FORGE portfolio projects. It provides:

- **Authentication**: JWT-based auth with role-based access control
- **Analytics**: PostHog integration with automatic event tracking
- **Middleware**: Rate limiting, security headers, CORS, request ID
- **Configuration**: Hierarchical config management with environment overrides
- **Logging**: Structured logging with request context
- **Utilities**: IP address handling, HTTP header utilities

## Installation

```bash
# Basic installation
pip install forge-shared

# With development dependencies
pip install "forge-shared[dev]"

# With all optional dependencies
pip install "forge-shared[all]"
```

## Quick Start

### Authentication

```python
from fastapi import FastAPI, Depends
from forge_shared.auth import JWTAuth, get_current_user
from forge_shared.auth.models import User

app = FastAPI()

# Initialize JWT authentication
auth = JWTAuth(
    secret_key="your-secret-key",
    algorithm="HS256",
    access_token_expire_minutes=30
)

# Protected route
@app.get("/protected")
async def protected_route(user: User = Depends(get_current_user)):
    return {"message": f"Hello, {user.email}"}
```

### Analytics

```python
from forge_shared.analytics import PostHogClient
from forge_shared.analytics.middleware import AnalyticsMiddleware

app = FastAPI()

# Add analytics middleware
app.add_middleware(
    AnalyticsMiddleware,
    posthog_api_key="your-api-key",
    posthog_host="https://app.posthog.com"
)

# Track custom events
from forge_shared.analytics import track_event

@app.post("/signup")
async def signup(email: str):
    # Track signup event
    await track_event("user_signup", {"email": email})
    return {"status": "success"}
```

### Middleware

```python
from fastapi import FastAPI
from forge_shared.middleware import (
    RateLimitMiddleware,
    SecurityMiddleware,
    RequestIDMiddleware
)

app = FastAPI()

# Add rate limiting (Redis required)
app.add_middleware(
    RateLimitMiddleware,
    redis_url="redis://localhost:6379",
    requests_per_minute=60
)

# Add security headers
app.add_middleware(SecurityMiddleware)

# Add request ID tracking
app.add_middleware(RequestIDMiddleware)
```

### Configuration

```python
from forge_shared.config import BaseConfig, Field
from pydantic_settings import BaseSettings

class AppConfig(BaseConfig):
    database_url: str = Field(..., description="Database connection URL")
    redis_url: str = Field(default="redis://localhost:6379")
    debug: bool = Field(default=False)

# Load from environment variables
config = AppConfig()  # Automatically loads from env vars

# Or use a specific .env file
config = AppConfig(_env_file="production.env")
```

## Documentation

Full documentation is available at [forge-shared.readthedocs.io](https://forge-shared.readthedocs.io).

### API Reference

- [Authentication](docs/auth.md) - JWT auth, dependencies, models
- [Analytics](docs/analytics.md) - PostHog integration, event tracking
- [Middleware](docs/middleware.md) - Rate limiting, security, CORS
- [Configuration](docs/config.md) - Settings management
- [Logging](docs/logging.md) - Structured logging setup
- [Utilities](docs/utils.md) - Helper functions

### Examples

See the [examples](examples/) directory for complete usage examples:

- [FastAPI Integration](examples/fastapi_app.py) - Full app setup
- [Migration Guide](examples/migration_guide.py) - Migrating existing apps

## Development

```bash
# Clone repository
git clone https://github.com/forge-dev/forge-shared.git
cd forge-shared

# Install development dependencies
uv pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=forge_shared --cov-report=html

# Run linting
ruff check forge_shared tests

# Format code
ruff format forge_shared tests

# Type checking
mypy forge_shared
```

## Contributing

We welcome contributions! Please see our [Contributing Guidelines](CONTRIBUTING.md) for details.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes with tests
4. Run the test suite (`pytest`)
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## Code Quality

This project maintains high code quality standards:

- **Test Coverage**: Minimum 80% coverage
- **Type Hints**: 100% type coverage with mypy strict mode
- **Linting**: Ruff with strict rules
- **Documentation**: Full docstring coverage

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

- **Documentation**: [forge-shared.readthedocs.io](https://forge-shared.readthedocs.io)
- **Issues**: [GitHub Issues](https://github.com/forge-dev/forge-shared/issues)
- **Discussions**: [GitHub Discussions](https://github.com/forge-dev/forge-shared/discussions)

## Roadmap

- [ ] Add OAuth2/OIDC support
- [ ] Add GraphQL middleware
- [ ] Add OpenTelemetry integration
- [ ] Add caching utilities
- [ ] Add database connection pooling
- [ ] Add background task utilities

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history.

---

Made with ❤️ by the FORGE team
