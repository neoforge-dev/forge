# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-01-19

### Added
- Initial release of forge-shared package
- Authentication module with JWT support
  - JWTAuth class for token creation and validation
  - FastAPI dependencies for authentication
  - Role-based access control (RBAC)
  - Permission-based authorization
  - Authentication middleware
- Analytics module with PostHog integration
  - PostHog client for event tracking
  - Event tracking utilities (track, identify, alias)
  - Analytics middleware for automatic tracking
- Middleware module
  - Rate limiting with Redis
  - Security headers middleware
  - CORS middleware with origin validation
  - Request ID middleware for tracing
  - Exception handler middleware
- Configuration module
  - BaseConfig for type-safe settings
  - DomainConfig for domain-specific configuration
  - Configuration loaders (env, file, dict)
- Logging module
  - JSON and text formatters
  - Logging context for request-scoped data
  - Logging middleware for HTTP request logging
- Utils module
  - IP address utilities (validation, extraction)
  - HTTP header utilities
- Comprehensive test suite
  - Unit tests for all modules
  - Pytest configuration
  - Test fixtures
- Documentation
  - README with quick start guide
  - Example FastAPI application
  - Migration guide for existing applications
- Build configuration
  - pyproject.toml with modern Python packaging
  - Ruff configuration for linting
  - mypy configuration for type checking
  - pytest configuration with coverage

### Security
- JWT authentication with configurable algorithms
- Security headers middleware (HSTS, CSP, X-Frame-Options, etc.)
- Rate limiting to prevent abuse
- Input validation via Pydantic

### Performance
- Async/await support throughout
- Redis-based rate limiting
- Efficient logging with contextvars

### Developer Experience
- Type hints throughout
- Comprehensive docstrings
- Example applications
- Migration guide

## [Unreleased]

### Planned
- OAuth2/OIDC support
- GraphQL middleware
- OpenTelemetry integration
- Caching utilities
- Database connection pooling
- Background task utilities
- More comprehensive test coverage
- Performance benchmarks
