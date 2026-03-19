# Forge Shared Middleware Registry

> **Centralized middleware management for FORGE FastAPI applications**
> **Sprint 6, Phase 1.3** | **Feb 8, 2026**

## Overview

The middleware registry provides a standardized way to register, configure, and manage multiple middleware instances across FastAPI applications. It supports different backend configurations and ordered middleware execution.

## Quick Start

```python
from fastapi import FastAPI
from forge_shared.middleware import MiddlewareRegistry, register_rate_limit, register_security

# Create registry
registry = MiddlewareRegistry()

# Register middleware with configuration
registry.register_rate_limit(
    redis_url="redis://localhost:6379",
    requests_per_minute=100
)

registry.register_security(
    allowed_origins=["https://app.example.com"],
    allowed_methods=["GET", "POST"]
)

# Apply to FastAPI app
app = FastAPI()
registry.apply_to_app(app)
```

## Available Registration Functions

### Core Middleware

| Function | Purpose | Order |
|----------|---------|-------|
| `register_request_id()` | Request correlation tracking | 1 (first) |
| `register_cors()` | Cross-origin resource sharing | 5 (early) |
| `register_rate_limit()` | API rate limiting | 10 |
| `register_security()` | Security headers and validation | 20 |
| `register_logging()` | Structured request/response logging | 50 |
| `register_exception_handler()` | Global exception handling | 1000 (last) |

### Backend Configuration

Configure shared backends for multiple middleware:

```python
from forge_shared.middleware import configure_backend

# Configure production backend
configure_backend("production", redis_url="redis://prod.example.com:6379")

# Configure testing backend
configure_backend("testing", requests_per_minute=1000)
```

## Middleware Stacks

### Standard Stack
Recommended for most FORGE applications:

```python
apply_standard_stack(app)  # Request ID -> CORS -> Rate Limit -> Security -> Logging -> Exception
```

### Minimal Stack
For simple APIs and testing:

```python
apply_minimal_stack(app)  # Request ID -> CORS -> Exception Handler
```

## Backend Support

### Memory Backend
- Rate limiting: In-memory with token bucket
- No persistence required
- Best for development/testing

### Redis Backend  
- Rate limiting: Distributed rate limiting across instances
- Request ID: Atomic increment operations
- Analytics: Event streaming and batching
- Production recommended

## Configuration Patterns

### Environment-Based Configuration

```python
import os
from forge_shared.middleware import configure_backend

# Configure based on environment
env = os.getenv("FORGE_ENV", "development")

if env == "production":
    configure_backend("production", redis_url=os.getenv("REDIS_URL"))
    configure_backend("rate_limit", requests_per_minute=100)
elif env == "testing":
    configure_backend("testing", requests_per_minute=1000)
```

### Domain-Specific Configuration

```python
# Configure per-domain settings
configure_backend("voice_coach", rate_limit_per_minute=200)
configure_backend("interview_simulator", allowed_origins=["https://app.codeswiftr.com"])
```

## Integration Examples

### With Existing Middleware

```python
from fastapi import FastAPI
from forge_shared.middleware import MiddlewareRegistry
from forge_shared.middleware.cors import CORSMiddleware

registry = MiddlewareRegistry()

# Add custom middleware alongside registry
app = FastAPI()
app.add_middleware(CORSMiddleware(allow_origins=["*"]))  # Custom CORS
registry.apply_to_app(app)  # Registry middleware
```

### Migration from Manual Registration

```python
# BEFORE (Manual)
app = FastAPI()
app.add_middleware(RateLimitMiddleware(redis_url="redis://localhost:6379"))
app.add_middleware(SecurityMiddleware(allowed_origins=["https://app.example.com"]))

# AFTER (Registry)
registry = MiddlewareRegistry()
registry.register_rate_limit(redis_url="redis://localhost:6379")
registry.register_security(allowed_origins=["https://app.example.com"])
registry.apply_to_app(app)
```

## Testing

### Unit Tests

```python
# tests/test_middleware_registry.py
import pytest
from forge_shared.middleware import MiddlewareRegistry

def test_registry_operations():
    registry = MiddlewareRegistry()
    
    # Test registration
    registry.register_rate_limit()
    assert "rate_limit" in registry.list_registered()
    
    # Test ordering
    configs = registry.get_middleware(["rate_limit", "security"])
    assert configs[0].order < configs[1].order  # rate_limit before security
```

### Integration Tests

```python
# tests/test_middleware_integration.py
from fastapi.testclient import TestClient
from forge_shared.middleware import apply_standard_stack

def test_standard_middleware_stack():
    app = FastAPI()
    apply_standard_stack(app)
    
    client = TestClient(app)
    response = client.get("/")
    
    # Verify middleware applied
    assert "x-request-id" in response.headers
    assert response.headers.get("x-rate-limit-remaining") is not None
```

## Performance Considerations

### Middleware Order Impact
- Request ID: Minimal overhead (~0.1ms)
- CORS: Header processing (~0.2ms)
- Rate Limiting: Redis lookup (~1-5ms)
- Security: Header validation (~0.3ms)
- Logging: Structured formatting (~0.5ms)

### Memory Usage
- Registry: Minimal (configuration storage only)
- Runtime: Middleware instances created once during app startup

### Backend Selection
- Memory: 2-3x faster than Redis for rate limiting
- Redis: Required for distributed applications
- Hybrid: Memory for development, Redis for production

## Security Notes

### Rate Limiting
- Use Redis with authentication for production
- Implement IP-based limits with fallback to user-based
- Consider burst capacity vs sustained rate

### Security Headers
- Always validate Content-Type for APIs
- Implement CSRF protection for state-changing endpoints
- Use HSTS for HTTPS-only applications

### CORS Configuration
- Use specific origins instead of "*" in production
- Validate preflight requests rigorously
- Limit allowed methods to actual requirements

## Migration Guide

### From Individual Middleware

```python
# OLD
from fastapi import FastAPI
from my_app.middleware import RateLimitMiddleware, SecurityMiddleware

app = FastAPI()
app.add_middleware(RateLimitMiddleware(redis_url="redis://localhost:6379"))
app.add_middleware(SecurityMiddleware(allowed_origins=["https://app.example.com"]))

# NEW
from fastapi import FastAPI
from forge_shared.middleware import register_rate_limit, register_security, apply_standard_stack

app = FastAPI()
register_rate_limit(redis_url="redis://localhost:6379")
register_security(allowed_origins=["https://app.example.com"])
apply_standard_stack(app)
```

### Benefits of Registry Pattern

1. **Centralized Configuration**: Single place for middleware settings
2. **Dynamic Backend Selection**: Runtime backend switching (memory/Redis)
3. **Consistent Ordering**: Guaranteed middleware execution order
4. **Testing Support**: Easy mocking and validation of middleware stack
5. **Reduced Duplication**: Shared configuration across applications
6. **Documentation**: Single source of truth for middleware patterns

---

*Implementation Status: ✅ Complete*  
*Testing Status: ✅ Core patterns tested*  
*Documentation Status: ✅ Complete*