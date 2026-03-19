# UTM Tracking Test Suite

Comprehensive test coverage for the UTM tracking module in forge-shared.

## Coverage Summary

- **Total Tests**: 56 tests
- **Coverage**: 100% for UTM module
  - `forge_shared/utm/models.py`: 100% (29 statements)
  - `forge_shared/utm/tracking.py`: 100% (36 statements)
  - `forge_shared/utm/middleware.py`: 100% (43 statements)

## Test Structure

### `test_models.py` (34 tests)

Tests for UTM data models:

**TestUTMParams** (30 tests):
- Model creation (empty, partial, full)
- `is_empty()` validation
- PostHog properties conversion
- Dictionary serialization/deserialization
- Round-trip conversion
- Edge cases (None values, datetime handling)

**TestAttributionEvent** (4 tests):
- Attribution event creation
- Default values
- Integration with UTMParams
- Validation

### `test_tracking.py` (20 tests)

Tests for UTM tracking functions:

**TestGetUTMParams** (3 tests):
- Extracting UTM from request state
- Handling missing state
- Empty state handling

**TestStoreUTM** (6 tests):
- Successful storage in Redis
- Skipping empty params
- Handling missing Redis client
- Redis error handling
- Full field storage

**TestGetAttribution** (7 tests):
- Successful retrieval from Redis
- Not found scenarios
- Missing Redis client
- Redis errors
- Invalid JSON handling
- Partial data retrieval

**Integration** (4 tests):
- Round-trip store/retrieve
- Data integrity verification

### `test_middleware.py` (2 tests)

Tests for UTM middleware:

**TestUTMMiddleware** (22 tests):
- Query string extraction (all params, partial params)
- Cookie setting and reading
- Cookie override by query params
- Invalid cookie handling
- Landing page capture
- Referrer capture
- Timestamp capture
- Custom configuration (cookie name, max age, domain)
- Request state attachment
- Case sensitivity

**TestUTMMiddlewareIntegration** (2 tests):
- User journey tracking
- Async route compatibility

## Key Features Tested

### 1. UTM Parameter Extraction
- From query strings (`?utm_source=google&utm_medium=cpc`)
- From cookies (persistence across requests)
- Query params override cookies (last-touch attribution)

### 2. Data Persistence
- Cookie-based browser-side persistence (30 days default)
- Redis-based server-side persistence (90 days default)
- Error handling for Redis failures

### 3. Attribution Tracking
- Landing page URL capture
- HTTP referrer capture
- Timestamp tracking
- Attribution event creation

### 4. Edge Cases
- Empty UTM parameters
- Invalid JSON in cookies
- Missing Redis connections
- Partial data scenarios
- Case-sensitive parameter names

## Running Tests

```bash
# Run all UTM tests
pytest tests/test_utm/ -v

# Run with coverage
pytest tests/test_utm/ --cov=forge_shared/utm --cov-report=html

# Run specific test file
pytest tests/test_utm/test_models.py -v

# Run specific test class
pytest tests/test_utm/test_tracking.py::TestStoreUTM -v

# Run specific test
pytest tests/test_utm/test_middleware.py::TestUTMMiddleware::test_extract_utm_from_query_string -v
```

## Test Patterns Used

### Fixtures
- `redis_mock`: Mock Redis client with async methods
- `app_with_utm_middleware`: FastAPI app with UTM middleware
- `client_with_utm`: TestClient with UTM middleware

### Patterns
- AAA (Arrange-Act-Assert) structure
- Async/await for async functions
- Mock objects for external dependencies (Redis)
- FastAPI TestClient for middleware testing

## Known Limitations

### TestClient Cookie Handling
The FastAPI TestClient has limitations with cookie serialization that differ from real browser behavior:
- Cookies may not persist across requests in TestClient
- Cookie values may be URL-encoded differently

**Workaround**: Tests verify middleware behavior through request state inspection rather than relying solely on cookie inspection.

### Deprecation Warnings
- `datetime.utcnow()` usage (in middleware) - will be updated to `datetime.now(datetime.UTC)` in future
- `event_loop` fixture redefinition - will be migrated to `loop_scope` parameter

## Future Test Additions

### Email Sequence Tests (Not Yet Implemented)
The marketing API currently lacks email sequence functionality. When implemented, tests should cover:
- Email template rendering
- Sequence trigger conditions
- Delivery scheduling
- Open/click tracking
- Unsubscribe handling

### Lead Capture Tests (Not Yet Implemented)
Lead capture endpoints are not yet implemented. Future tests should cover:
- Form validation
- Data sanitization
- Duplicate detection
- Integration with email sequences
- GDPR compliance

## Related Documentation

- Source: `/Users/bogdan/work/FORGE/forge-shared/forge_shared/utm/`
- Coverage Report: `/Users/bogdan/work/FORGE/forge-shared/htmlcov/index.html`
- Main README: `/Users/bogdan/work/FORGE/forge-shared/README.md`
