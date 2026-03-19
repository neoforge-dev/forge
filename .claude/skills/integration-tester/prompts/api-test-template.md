# API Integration Test Template

Use this template when creating API integration tests for FORGE projects.

## Test Structure

```yaml
name: API Test Suite
description: Describe what this test suite validates
target:
  base_url: ${API_BASE_URL}
  timeout: 30
  retry: 3
```

## Authentication Tests

### Bearer Token
```yaml
test:
  name: Protected Endpoint with Bearer Token
  endpoint: /api/v1/protected
  method: GET
  auth:
    type: bearer
    token: ${API_TOKEN}
  expect:
    status: 200
    field: .user.id
```

### Basic Auth
```yaml
test:
  name: Admin Endpoint with Basic Auth
  endpoint: /api/v1/admin
  method: GET
  auth:
    type: basic
    username: ${ADMIN_USER}
    password: ${ADMIN_PASS}
  expect:
    status: 200
```

### API Key
```yaml
test:
  name: External API with Key
  endpoint: /api/v1/data
  method: GET
  headers:
    X-API-Key: ${API_KEY}
  expect:
    status: 200
```

## CRUD Operations

### Create (POST)
```yaml
test:
  name: Create Resource
  endpoint: /api/v1/resources
  method: POST
  body:
    name: Test Resource
    description: Integration test resource
  expect:
    status: 201
    field: .id
    save: RESOURCE_ID
```

### Read (GET)
```yaml
test:
  name: Get Resource
  endpoint: /api/v1/resources/${RESOURCE_ID}
  method: GET
  expect:
    status: 200
    field: .name
    value: Test Resource
```

### Update (PUT/PATCH)
```yaml
test:
  name: Update Resource
  endpoint: /api/v1/resources/${RESOURCE_ID}
  method: PATCH
  body:
    name: Updated Resource
  expect:
    status: 200
    field: .name
    value: Updated Resource
```

### Delete (DELETE)
```yaml
test:
  name: Delete Resource
  endpoint: /api/v1/resources/${RESOURCE_ID}
  method: DELETE
  expect:
    status: 204
```

## Error Handling Tests

### Validation Error
```yaml
test:
  name: Validation Error
  endpoint: /api/v1/resources
  method: POST
  body:
    name: ""  # Invalid: empty name
  expect:
    status: 422
    field: .error.code
    value: VALIDATION_ERROR
```

### Authentication Error
```yaml
test:
  name: Missing Authentication
  endpoint: /api/v1/protected
  method: GET
  expect:
    status: 401
```

### Authorization Error
```yaml
test:
  name: Insufficient Permissions
  endpoint: /api/v1/admin
  method: GET
  auth:
    type: bearer
    token: ${USER_TOKEN}  # Non-admin token
  expect:
    status: 403
```

### Not Found
```yaml
test:
  name: Resource Not Found
  endpoint: /api/v1/resources/invalid-id
  method: GET
  expect:
    status: 404
```

## Rate Limiting Tests

```yaml
test:
  name: Rate Limit Test
  endpoint: /api/v1/public
  method: GET
  rate_limit:
    requests: 100
    window: 60
  expect:
    - status: 200  # First requests succeed
    - status: 429  # Eventually rate limited
```

## Response Validation

### JSON Schema Validation
```yaml
test:
  name: Response Schema
  endpoint: /api/v1/users
  method: GET
  expect:
    status: 200
    schema:
      type: object
      required: [users, total]
      properties:
        users:
          type: array
        total:
          type: integer
```

### Field Types
```yaml
test:
  name: Field Type Validation
  endpoint: /api/v1/users/1
  method: GET
  expect:
    status: 200
    fields:
      - path: .id
        type: string
      - path: .email
        type: string
        format: email
      - path: .created_at
        type: string
        format: datetime
```

## Performance Tests

### Latency Check
```yaml
test:
  name: Response Time Check
  endpoint: /api/v1/health
  method: GET
  expect:
    status: 200
    max_latency_ms: 100
```

### Concurrent Load
```yaml
test:
  name: Concurrent Requests
  endpoint: /api/v1/data
  method: GET
  load:
    concurrency: 10
    requests: 100
  expect:
    success_rate: 100
    avg_latency_ms: 200
    p99_latency_ms: 500
```

## WebSocket Tests

```yaml
test:
  name: WebSocket Connection
  type: websocket
  url: ws://localhost:8000/ws
  steps:
    - send: '{"action": "subscribe", "channel": "updates"}'
    - expect:
        type: message
        field: .status
        value: subscribed
```

## File Upload Tests

```yaml
test:
  name: File Upload
  endpoint: /api/v1/upload
  method: POST
  multipart:
    - name: file
      filename: test.pdf
      content_type: application/pdf
      path: ./fixtures/test.pdf
  expect:
    status: 200
    field: .url
```

## Test Checklist

- [ ] All endpoints return expected status codes
- [ ] Authentication works for protected endpoints
- [ ] Error responses match API specification
- [ ] Rate limiting is enforced
- [ ] Response times are acceptable
- [ ] Response schemas are valid
- [ ] CORS headers are present (if applicable)
- [ ] Content-Type headers are correct
