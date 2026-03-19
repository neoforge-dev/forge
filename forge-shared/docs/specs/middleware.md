# Service Specification: Middleware Stack

## Overview
The Middleware stack provides a set of FastAPI/Starlette compatible components to handle common cross-cutting concerns.

## Components

### 1. `RateLimitMiddleware`
- **Algorithm**: Token bucket / Sliding window.
- **Backend**: Redis (optional fallback to in-memory).
- **Configuration**: `requests_per_minute`, `burst_size`.
- **Headers**: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `Retry-After`.

### 2. `SecurityMiddleware`
Adds recommended security headers.
- **Headers**:
  - `Strict-Transport-Security` (HSTS)
  - `Content-Security-Policy` (CSP)
  - `X-Frame-Options`: `SAMEORIGIN`
  - `X-Content-Type-Options`: `nosniff`
  - `Referrer-Policy`: `strict-origin-when-cross-origin`

### 3. `CORSMiddleware`
Handles Cross-Origin Resource Sharing.
- **Configuration**: `allow_origins`, `allow_methods`, `allow_headers`, `allow_credentials`.

### 4. `RequestIDMiddleware`
Generates or propagates a correlation ID.
- **Header**: `X-Request-ID`.
- **Traceability**: ID is stored in `request.state.request_id`.

## `MiddlewareRegistry`
A helper class to apply a standardized stack of middleware to a FastAPI application.

### Methods
- `register_rate_limit()`
- `register_security()`
- `register_cors()`
- `register_request_id()`
- `apply_to_app(app)`
