# Integration Test Failure Analysis Guide

Use this guide to analyze and resolve integration test failures.

## Quick Diagnosis Flow

```
Test Failed
    │
    ├── Is it a connection error?
    │   ├── Check service is running
    │   ├── Check network connectivity
    │   └── Check firewall/VPN
    │
    ├── Is it an authentication error?
    │   ├── Check credentials/tokens
    │   ├── Check token expiration
    │   └── Check permissions/scopes
    │
    ├── Is it a timeout?
    │   ├── Check service load
    │   ├── Increase timeout value
    │   └── Check for deadlocks
    │
    └── Is it a validation error?
        ├── Check request format
        ├── Check required fields
        └── Check response schema
```

## Common Errors & Solutions

### Connection Errors

#### `ECONNREFUSED`
```
Error: connect ECONNREFUSED 127.0.0.1:5432
```

**Diagnosis:**
- Service is not running
- Wrong host/port configuration
- Service crashed

**Solutions:**
```bash
# Check if service is running
ps aux | grep postgres
docker ps | grep db

# Start the service
docker-compose up -d db

# Check logs
docker-compose logs db

# Verify configuration
cat .env | grep DATABASE_URL
```

#### `ETIMEDOUT`
```
Error: connect ETIMEDOUT 192.168.1.100:8000
```

**Diagnosis:**
- Network unreachable
- Firewall blocking
- VPN not connected

**Solutions:**
```bash
# Test connectivity
ping 192.168.1.100
telnet 192.168.1.100 8000

# Check VPN
ifconfig | grep tun

# Check firewall
sudo iptables -L | grep 8000
```

#### `ENOTFOUND`
```
Error: getaddrinfo ENOTFOUND api.example.com
```

**Diagnosis:**
- DNS resolution failure
- Wrong hostname

**Solutions:**
```bash
# Test DNS
nslookup api.example.com
dig api.example.com

# Check /etc/hosts
cat /etc/hosts | grep api
```

### Authentication Errors

#### `401 Unauthorized`
```
HTTP 401: Invalid authentication credentials
```

**Diagnosis:**
- Missing credentials
- Expired token
- Wrong token format

**Solutions:**
```bash
# Check token expiration
# Decode JWT to check exp claim
echo $TOKEN | cut -d. -f2 | base64 -d | jq .exp

# Generate new token
# For API keys, check they are active in dashboard

# Verify header format
curl -H "Authorization: Bearer $TOKEN" ...
```

#### `403 Forbidden`
```
HTTP 403: Insufficient permissions
```

**Diagnosis:**
- Token valid but lacks required scope
- User doesn't have permission

**Solutions:**
```bash
# Check token scopes
echo $TOKEN | cut -d. -f2 | base64 -d | jq .scopes

# Use admin/service account token for testing
export API_TOKEN=$ADMIN_TOKEN
```

### Database Errors

#### `ECONNREFUSED` (PostgreSQL)
```
Error: connect ECONNREFUSED /tmp/.s.PGSQL.5432
```

**Solutions:**
```bash
# Start PostgreSQL
brew services start postgresql  # macOS
sudo service postgresql start   # Linux

# Check connection string
# Should be: postgresql://user:pass@host:port/db

# Test with psql
psql $DATABASE_URL -c "SELECT 1;"
```

#### `42P01` (Undefined Table)
```
Error: relation "users" does not exist
```

**Diagnosis:**
- Migrations not run
- Wrong database
- Schema not created

**Solutions:**
```bash
# Run migrations
alembic upgrade head

# Check current database
psql $DATABASE_URL -c "SELECT current_database();"

# List tables
psql $DATABASE_URL -c "\dt"
```

#### `28P01` (Authentication Failed)
```
Error: password authentication failed for user "postgres"
```

**Solutions:**
```bash
# Check credentials in .env
cat .env | grep DATABASE_URL

# Reset password (development only)
psql -U postgres -c "ALTER USER postgres WITH PASSWORD 'newpassword';"
```

### API Errors

#### `422 Unprocessable Entity`
```
HTTP 422: {"detail": [{"loc": ["body", "email"], "msg": "invalid email format"}]}
```

**Diagnosis:**
- Request body validation failed
- Missing required fields
- Wrong data types

**Solutions:**
```bash
# Check request body format
curl -X POST $API_URL \
  -H "Content-Type: application/json" \
  -d '{"email": "valid@example.com"}' \
  -v

# Verify against API schema
# Check docs for required fields
```

#### `429 Too Many Requests`
```
HTTP 429: {"error": "Rate limit exceeded"}
```

**Diagnosis:**
- Too many requests in short time
- Rate limit configuration

**Solutions:**
```bash
# Add delays between requests
sleep 1

# Implement exponential backoff
# Check rate limit headers
# X-RateLimit-Limit, X-RateLimit-Remaining

# Use test-specific rate limits if available
```

#### `500 Internal Server Error`
```
HTTP 500: {"error": "Internal server error"}
```

**Diagnosis:**
- Server-side bug
- Unhandled exception
- Database connection pool exhausted

**Solutions:**
```bash
# Check server logs
docker-compose logs api | tail -100

# Check database connections
psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity;"

# Restart service
docker-compose restart api
```

### External Service Errors

#### Stripe Errors
```
StripeError: No such plan: 'plan_123'
```

**Diagnosis:**
- Using wrong Stripe environment (test vs live)
- Plan doesn't exist

**Solutions:**
```bash
# Check which Stripe key is being used
echo $STRIPE_SECRET_KEY | cut -c1-7
# sk_test_... = test mode
# sk_live_... = live mode

# Verify plan exists in dashboard
# Or create test plan programmatically
```

#### OpenAI Errors
```
Error 429: You exceeded your current quota
```

**Diagnosis:**
- Rate limit hit
- Quota exceeded

**Solutions:**
```bash
# Check usage dashboard
# Implement retry with backoff
# Use different API key for tests
```

### Webhook Errors

#### `Invalid Signature`
```
Error: Webhook signature verification failed
```

**Diagnosis:**
- Wrong webhook secret
- Payload tampered
- Timestamp too old

**Solutions:**
```bash
# Verify webhook secret
echo $WEBHOOK_SECRET | head -c 10

# Check timestamp tolerance
# Should be within 5 minutes

# Regenerate webhook secret if needed
```

#### `No Webhook Received`
```
Error: Timeout waiting for webhook
```

**Diagnosis:**
- Webhook not sent
- Wrong endpoint URL
- Firewall blocking

**Solutions:**
```bash
# Check webhook delivery in dashboard
# Use webhook.site for testing
# Verify endpoint is publicly accessible
```

## Debugging Tools

### Verbose Logging
```bash
# Enable verbose mode
/test api --endpoint /health --verbose

# Add curl verbose flag
curl -v $API_URL/health

# Show full request/response
```

### Network Inspection
```bash
# Monitor traffic
sudo tcpdump -i lo0 port 8000

# Use Wireshark for detailed analysis

# Check proxy settings
env | grep -i proxy
```

### Database Inspection
```bash
# Enable query logging
psql $DATABASE_URL -c "SET log_statement = 'all';"

# Check slow queries
psql $DATABASE_URL -c "SELECT * FROM pg_stat_statements ORDER BY total_time DESC LIMIT 10;"

# Monitor connections
watch 'psql $DATABASE_URL -c "SELECT state, count(*) FROM pg_stat_activity GROUP BY state;"'
```

### Redis Inspection
```bash
# Monitor commands
redis-cli monitor

# Check queue lengths
redis-cli llen celery

# Inspect keys
redis-cli keys "*"
```

## Test Isolation Issues

### Shared State
```
Error: User already exists
```

**Solution:**
```bash
# Use unique identifiers
USER_EMAIL="test-$(date +%s)@example.com"

# Cleanup before test
curl -X DELETE $API_URL/users/$USER_ID || true

# Use test database
dropdb test_db && createdb test_db
```

### Race Conditions
```
Error: Record not found (created in parallel test)
```

**Solution:**
```bash
# Use unique resources per test
# Add delays where needed
# Use database transactions
```

## Performance Issues

### Slow Response Times
```
Error: Request timeout (> 30s)
```

**Diagnosis:**
- Database query slow
- External API slow
- Server overloaded

**Solutions:**
```bash
# Profile database queries
# Add indexes
# Use connection pooling

# Check server load
htop
loadavg

# Monitor external API latency
```

## CI/CD Specific Issues

### Works Locally, Fails in CI
```
Error: Connection refused (in GitHub Actions)
```

**Common Causes:**
- Services not started in CI
- Different environment variables
- Network restrictions

**Solutions:**
```yaml
# .github/workflows/test.yml
- name: Start services
  run: docker-compose up -d

- name: Wait for services
  run: sleep 10

- name: Run tests
  run: /test all
  env:
    DATABASE_URL: postgresql://test:test@localhost/test
```

## Recovery Procedures

### Reset Test Environment
```bash
# Clean slate
docker-compose down -v
docker-compose up -d --build

# Reset database
psql -c "DROP DATABASE IF EXISTS test_db;"
psql -c "CREATE DATABASE test_db;"
alembic upgrade head

# Clear Redis
redis-cli flushall
```

### Retry with Backoff
```bash
# Exponential backoff
for i in 1 2 4 8; do
  /test api --endpoint /health && break
  echo "Retrying in ${i}s..."
  sleep $i
done
```

## Escalation

If issue persists:

1. **Document**: Save logs, request/response, timestamps
2. **Isolate**: Create minimal reproduction case
3. **Notify**: Alert service owner with details
4. **Workaround**: Skip test temporarily with TODO comment
