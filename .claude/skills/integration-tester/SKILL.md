---
name: integration-tester
description: Test integrations between services, APIs, and external systems across FORGE projects. Supports API, database, external services, webhooks, queues, and E2E flow testing with retry logic and failure analysis.
trigger: user-invoked
tools: [Bash, Shell, Read, FetchURL]
---

# Integration Tester

Comprehensive integration testing skill for FORGE projects. Tests APIs, databases, external services, webhooks, message queues, and end-to-end user flows with intelligent retry logic and detailed failure analysis.

## When to Use

- **Pre-deployment validation** - Verify all integrations work before shipping
- **After dependency updates** - Ensure updates don't break integrations
- **Debugging production issues** - Isolate integration failures
- **CI/CD pipelines** - Automated integration testing
- **New service onboarding** - Validate new integrations

## Quick Start

```bash
# Test API endpoint
/test api --endpoint /api/v1/users --auth bearer

# Test database connection
/test db --url postgresql://user:pass@localhost/db

# Test external service
/test stripe --secret-key sk_test_xxx

# Test webhook endpoint
/test webhook --url https://api.example.com/webhooks --secret whsec_xxx

# Test message queue
/test queue --broker redis://localhost:6379

# Run E2E flow test
/test e2e --flow signup-to-payment
```

---

## Features

### 1. API Integration Testing

Test REST/GraphQL endpoints with authentication, rate limits, and response validation.

```bash
# Basic endpoint test
/test api --endpoint /api/v1/health

# With authentication
/test api --endpoint /api/v1/users --auth bearer --token $API_TOKEN
/test api --endpoint /api/v1/admin --auth basic --user admin --pass secret
/test api --endpoint /api/v1/webhook --auth api-key --key x-api-key

# Full request test
/test api \
  --endpoint /api/v1/users \
  --method POST \
  --auth bearer \
  --token $TOKEN \
  --body '{"email":"test@example.com"}' \
  --expect-status 201 \
  --expect-field "id"

# Rate limit testing
/test api --endpoint /api/v1/data --rate-limit-test --requests 100
```

**Validation Options:**
| Option | Description |
|--------|-------------|
| `--expect-status` | Expected HTTP status code |
| `--expect-field` | Verify JSON response contains field |
| `--expect-value` | Verify field equals specific value |
| `--expect-schema` | Validate against JSON schema |
| `--max-latency` | Fail if response time exceeds (ms) |

### 2. Database Integration Testing

Test database connections, migrations, and query performance.

```bash
# Connection test
/test db --url postgresql://user:pass@localhost/mydb

# With migration check
/test db --url $DATABASE_URL --migrations --alembic

# Performance test
/test db --url $DATABASE_URL --query "SELECT * FROM users" --max-time 100

# Full validation
/test db \
  --url postgresql://user:pass@localhost/mydb \
  --migrations \
  --test-queries \
  --connection-pool
```

**Database Support:**
- PostgreSQL (with asyncpg)
- MySQL (with aiomysql)
- SQLite
- Redis
- MongoDB

### 3. External Service Testing

Test third-party integrations (Stripe, OpenAI, AssemblyAI, etc.)

```bash
# Stripe
/test stripe --secret-key sk_test_xxx --webhook-secret whsec_xxx
/test stripe --test-mode --test-charge --test-customer

# OpenAI
/test openai --api-key $OPENAI_KEY --model gpt-4 --test-completion

# AssemblyAI
/test assemblyai --api-key $AAI_KEY --test-transcription

# Generic HTTP service
/test service --name "My API" --url https://api.example.com/health
```

**Service-Specific Tests:**

| Service | Tests Performed |
|---------|-----------------|
| Stripe | Auth, webhook validation, test charge, customer create |
| OpenAI | Auth, completion, embedding, rate limits |
| AssemblyAI | Auth, upload, transcript, webhook |
| SendGrid | Auth, send test email |
| AWS S3 | Auth, bucket access, upload test |

### 4. Webhook Testing

Receive and validate webhooks with signature verification.

```bash
# Start webhook listener
/test webhook --port 8080 --secret whsec_xxx --provider stripe

# Test existing endpoint
/test webhook --url https://api.example.com/webhooks \
  --secret whsec_xxx \
  --send-test \
  --provider stripe

# With custom payload
/test webhook --url https://api.example.com/webhooks \
  --payload '{"event":"test"}' \
  --signature-header "X-Signature"
```

**Supported Providers:**
- Stripe (signature verification)
- GitHub (signature verification)
- Shopify (HMAC verification)
- Custom (configurable)

### 5. Message Queue Testing

Test Redis, Celery, and other message queues.

```bash
# Redis
/test queue --broker redis://localhost:6379 --backend redis

# Celery with Redis
/test queue --broker redis://localhost:6379 \
  --backend redis://localhost:6379/0 \
  --celery \
  --test-task tasks.add

# Test task execution
/test queue --broker redis://localhost:6379 \
  --send-task test.echo \
  --args '{"message":"hello"}' \
  --expect-result "hello"
```

**Queue Tests:**
- Connection establishment
- Message publish/subscribe
- Task enqueue and execution
- Result backend
- Dead letter queue

### 6. E2E Flow Testing

Test complete user journeys across multiple services.

```bash
# Run predefined flow
/test e2e --flow signup-to-payment

# Custom flow from file
/test e2e --file ./flows/onboarding.json

# With specific environment
/test e2e --flow signup-to-payment --env staging
```

**Predefined Flows:**

| Flow | Description | Services Tested |
|------|-------------|-----------------|
| `signup-to-payment` | Full user signup → payment | Auth, DB, Stripe |
| `content-creation` | Create → process → publish | API, Storage, Queue |
| `data-import` | Upload → process → analyze | API, DB, AI service |
| `notification` | Trigger → queue → send | API, Queue, Email |

---

## Usage Reference

### Command Syntax

```bash
/test <type> [options]

Types:
  api         Test API endpoints
  db          Test database connections
  stripe      Test Stripe integration
  openai      Test OpenAI integration
  assemblyai  Test AssemblyAI integration
  webhook     Test webhook endpoints
  queue       Test message queues
  e2e         Run end-to-end flow tests
  all         Run all integration tests
```

### Global Options

| Option | Description |
|--------|-------------|
| `--retry` | Number of retry attempts (default: 3) |
| `--retry-delay` | Delay between retries in seconds (default: 2) |
| `--timeout` | Request timeout in seconds (default: 30) |
| `--verbose` | Show detailed output |
| `--json` | Output results as JSON |
| `--output` | Save results to file |
| `--fail-fast` | Stop on first failure |
| `--parallel` | Run tests in parallel |

### Environment Variables

```bash
# API Testing
TEST_API_BASE_URL=https://api.example.com
TEST_API_TOKEN=xxx

# Database
TEST_DATABASE_URL=postgresql://...

# External Services
STRIPE_SECRET_KEY=sk_test_xxx
STRIPE_WEBHOOK_SECRET=whsec_xxx
OPENAI_API_KEY=sk-xxx
ASSEMBLYAI_API_KEY=xxx

# Webhook Testing
WEBHOOK_SECRET=whsec_xxx
WEBHOOK_PORT=8080

# Queue Testing
REDIS_URL=redis://localhost:6379
CELERY_BROKER_URL=redis://localhost:6379
```

---

## Test Result Reporting

### Console Output

```
╔════════════════════════════════════════════════════════════╗
║           INTEGRATION TEST RESULTS                         ║
╠════════════════════════════════════════════════════════════╣
║ Suite: API Integration Tests                               ║
║ Duration: 4.23s                                            ║
║ Timestamp: 2024-01-15T10:30:00Z                            ║
╠════════════════════════════════════════════════════════════╣
║ ✅ Health Endpoint                                         ║
║    GET /api/v1/health                                      ║
║    Status: 200 OK (45ms)                                   ║
║                                                            ║
║ ✅ User List (Authenticated)                               ║
║    GET /api/v1/users                                       ║
║    Status: 200 OK (120ms)                                  ║
║    Response: {"count": 42, "users": [...]}                 ║
║                                                            ║
║ ❌ Create User (Rate Limited)                              ║
║    POST /api/v1/users                                      ║
║    Status: 429 Too Many Requests (523ms)                   ║
║    Error: Rate limit exceeded, retry after 60s             ║
╠════════════════════════════════════════════════════════════╣
║ RESULTS: 2 passed, 1 failed, 0 skipped                     ║
║ Success Rate: 66.7%                                        ║
╚════════════════════════════════════════════════════════════╝
```

### JSON Output

```json
{
  "suite": "API Integration Tests",
  "timestamp": "2024-01-15T10:30:00Z",
  "duration": 4.23,
  "summary": {
    "total": 3,
    "passed": 2,
    "failed": 1,
    "skipped": 0
  },
  "tests": [
    {
      "name": "Health Endpoint",
      "status": "passed",
      "duration": 0.045,
      "request": {
        "method": "GET",
        "url": "/api/v1/health"
      },
      "response": {
        "status": 200,
        "latency_ms": 45
      }
    },
    {
      "name": "Create User",
      "status": "failed",
      "duration": 0.523,
      "error": "Rate limit exceeded",
      "suggestion": "Implement exponential backoff for 429 responses"
    }
  ]
}
```

---

## Failure Analysis

### Automatic Diagnosis

When a test fails, the skill analyzes the failure and suggests fixes:

```
❌ Database Connection Failed
   Error: connection refused
   
   Diagnosis:
   - Database server not running
   - Incorrect connection string
   - Firewall blocking connection
   
   Suggested Fixes:
   1. Start PostgreSQL: docker-compose up -d db
   2. Check connection string in .env
   3. Verify port 5432 is accessible
```

### Common Issues & Solutions

| Issue | Cause | Solution |
|-------|-------|----------|
| `ECONNREFUSED` | Service not running | Start service, check port |
| `ETIMEDOUT` | Network/firewall issue | Check connectivity, VPN |
| `401 Unauthorized` | Invalid credentials | Check API keys, tokens |
| `429 Too Many Requests` | Rate limiting | Add delays, implement backoff |
| `SSL certificate verify failed` | Self-signed cert | Use `--insecure` for testing |
| `Migration checksum mismatch` | Schema drift | Run migrations or reset DB |

---

## Retry Logic

### Default Retry Policy

```python
retry_policy = {
    "max_attempts": 3,
    "backoff_factor": 2,
    "initial_delay": 1,
    "max_delay": 30,
    "retry_on": [408, 429, 500, 502, 503, 504],
    "retry_exceptions": ["ConnectionError", "TimeoutError"]
}
```

### Custom Retry Configuration

```bash
# More aggressive retry
/test api --endpoint /api/v1/data \
  --retry 5 \
  --retry-delay 3 \
  --max-delay 60

# No retry for specific test
/test api --endpoint /api/v1/health --retry 0
```

---

## Configuration File

Create `.integration-test.yml` in project root:

```yaml
# Default settings
defaults:
  retry: 3
  timeout: 30
  verbose: false

# API base URLs by environment
environments:
  development:
    api_base_url: http://localhost:8000
    database_url: postgresql://localhost/dev
  staging:
    api_base_url: https://staging-api.example.com
    database_url: ${STAGING_DATABASE_URL}
  production:
    api_base_url: https://api.example.com

# Predefined test suites
suites:
  smoke:
    - type: api
      endpoint: /api/v1/health
    - type: db
      url: ${DATABASE_URL}
  
  full:
    - type: api
      endpoint: /api/v1/health
    - type: db
      url: ${DATABASE_URL}
      migrations: true
    - type: stripe
      secret_key: ${STRIPE_SECRET_KEY}
    - type: queue
      broker: ${REDIS_URL}

# Custom flows
flows:
  signup-to-payment:
    steps:
      - action: api
        endpoint: /api/v1/auth/signup
        method: POST
        body: '{"email":"test@example.com"}'
      - action: api
        endpoint: /api/v1/payments/checkout
        method: POST
        depends_on: signup
```

---

## Integration with CI/CD

### GitHub Actions

```yaml
name: Integration Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Run Integration Tests
        run: |
          /test all --json --output results.json
        env:
          DATABASE_URL: postgresql://test:test@localhost/test
          STRIPE_SECRET_KEY: ${{ secrets.STRIPE_TEST_KEY }}
      
      - name: Upload Results
        uses: actions/upload-artifact@v4
        with:
          name: test-results
          path: results.json
```

### Makefile Integration

```makefile
.PHONY: test-integration test-api test-db test-e2e

test-integration:
	@test all --fail-fast

test-api:
	@test api --endpoint /api/v1/health

test-db:
	@test db --url ${DATABASE_URL}

test-e2e:
	@test e2e --flow signup-to-payment
```

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/test-api.sh` | API endpoint testing |
| `scripts/test-db.sh` | Database connection testing |
| `scripts/test-stripe.sh` | Stripe integration testing |
| `scripts/test-openai.sh` | OpenAI integration testing |
| `scripts/test-assemblyai.sh` | AssemblyAI testing |
| `scripts/test-webhook.sh` | Webhook receiver/testing |
| `scripts/test-queue.sh` | Message queue testing |
| `scripts/test-e2e.sh` | End-to-end flow testing |
| `scripts/test-runner.sh` | Main test orchestrator |
| `scripts/report.sh` | Generate test reports |

---

## Prompts Reference

| Prompt | Purpose |
|--------|---------|
| `prompts/api-test-template.md` | API test structure template |
| `prompts/e2e-flow-template.md` | E2E test flow template |
| `prompts/failure-analysis.md` | Failure analysis guide |
| `prompts/webhook-testing.md` | Webhook testing guide |

---

## Workflow

1. **Pre-test Setup**
   - Load configuration from `.integration-test.yml` or environment
   - Validate all required environment variables
   - Check service availability

2. **Test Execution**
   - Run tests with retry logic
   - Collect metrics (latency, status codes)
   - Capture errors and stack traces

3. **Result Analysis**
   - Aggregate test results
   - Generate failure analysis
   - Suggest fixes for failures

4. **Reporting**
   - Output results (console/JSON/file)
   - Update CI status
   - Notify on failures (optional)

---

## Checklist

- [ ] Configuration file created or environment variables set
- [ ] All required services are running
- [ ] Test credentials/secrets are configured
- [ ] Tests pass in development environment
- [ ] Tests pass in staging environment
- [ ] Failure analysis provides actionable insights
- [ ] Results are documented/output properly

---

## Examples

### Complete API Test Suite

```bash
#!/bin/bash

# Health checks
/test api --endpoint /api/v1/health --expect-status 200
/test api --endpoint /api/v1/ready --expect-status 200

# Auth endpoints
/test api \
  --endpoint /api/v1/auth/login \
  --method POST \
  --body '{"email":"test@test.com","password":"test"}' \
  --expect-status 200 \
  --expect-field "token"

# Protected endpoints
/test api \
  --endpoint /api/v1/users/me \
  --auth bearer \
  --token $TEST_TOKEN \
  --expect-field "id"

# Rate limiting
/test api --endpoint /api/v1/public --rate-limit-test --requests 50
```

### Complete Database Test

```bash
#!/bin/bash

/test db \
  --url ${DATABASE_URL} \
  --migrations \
  --connection-pool \
  --test-queries \
  --performance-test
```

### CI/CD Integration Test

```bash
#!/bin/bash
set -e

echo "Running integration tests..."

# Smoke tests first
/test api --endpoint /api/v1/health --fail-fast

# Full suite
/test all \
  --retry 3 \
  --json \
  --output integration-results.json

# Check results
if [ $(jq '.summary.failed' integration-results.json) -gt 0 ]; then
  echo "Integration tests failed!"
  jq '.tests[] | select(.status == "failed")' integration-results.json
  exit 1
fi

echo "All integration tests passed!"
```

---

**Remember**: Integration tests verify that your services work together. Run them before deployment and monitor them in production for early issue detection.
