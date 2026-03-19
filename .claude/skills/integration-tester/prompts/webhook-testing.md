# Webhook Integration Testing Guide

Comprehensive guide for testing webhooks in FORGE projects.

## Webhook Testing Strategies

### 1. Local Development Testing

#### Using webhook.site
```bash
# Get temporary webhook URL
curl -X POST https://webhook.site/token

# Use returned UUID as endpoint
# https://webhook.site/#!/12345678-1234-1234-1234-123456789abc

# Send test webhook
curl -X POST https://webhook.site/12345678-1234-1234-1234-123456789abc \
  -H "Content-Type: application/json" \
  -d '{"test": "data"}'
```

#### Using ngrok
```bash
# Start ngrok tunnel to local server
ngrok http 8000

# Use https URL as webhook endpoint
# https://abc123.ngrok.io/webhooks

# Configure webhook provider with ngrok URL
```

#### Using Local Listener
```bash
# Start built-in webhook listener
/test webhook --port 8080 --secret whsec_xxx

# Configure provider to send to http://localhost:8080
```

### 2. Provider-Specific Testing

#### Stripe Webhooks

**Testing with Stripe CLI:**
```bash
# Login
stripe login

# Forward webhooks to local
stripe listen --forward-to localhost:8000/webhooks/stripe

# Trigger test events
stripe trigger payment_intent.succeeded
stripe trigger customer.subscription.created
```

**Verifying Signatures:**
```python
import stripe

endpoint_secret = 'whsec_xxx'
payload = request.body
sig_header = request.headers['Stripe-Signature']

event = stripe.Webhook.construct_event(
    payload, sig_header, endpoint_secret
)
```

**Test Script:**
```bash
/test webhook \
  --url https://api.stripe.com/v1/webhooks \
  --secret whsec_xxx \
  --provider stripe \
  --send-test
```

#### GitHub Webhooks

**Creating Test Webhook:**
```bash
# Create webhook via API
curl -X POST https://api.github.com/repos/owner/repo/hooks \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  -d '{
    "config": {
      "url": "https://your-app.com/webhooks/github",
      "content_type": "json",
      "secret": "your-secret"
    },
    "events": ["push", "pull_request"]
  }'
```

**Verifying Signatures:**
```python
import hmac
import hashlib

def verify_github_signature(payload, signature, secret):
    expected = 'sha256=' + hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

#### Slack Webhooks

**Testing Incoming Webhooks:**
```bash
# Send test message
curl -X POST $SLACK_WEBHOOK_URL \
  -H 'Content-type: application/json' \
  -d '{"text": "Test message from integration test"}'
```

### 3. Webhook Payload Examples

#### Stripe - payment_intent.succeeded
```json
{
  "id": "evt_1234567890",
  "object": "event",
  "api_version": "2023-10-16",
  "created": 1234567890,
  "data": {
    "object": {
      "id": "pi_1234567890",
      "object": "payment_intent",
      "amount": 1000,
      "currency": "usd",
      "status": "succeeded",
      "customer": "cus_1234567890"
    }
  },
  "livemode": false,
  "pending_webhooks": 1,
  "request": {
    "id": "req_1234567890",
    "idempotency_key": null
  },
  "type": "payment_intent.succeeded"
}
```

#### GitHub - push
```json
{
  "ref": "refs/heads/main",
  "before": "abc123",
  "after": "def456",
  "repository": {
    "id": 123456789,
    "name": "repo-name",
    "full_name": "owner/repo-name"
  },
  "pusher": {
    "name": "username",
    "email": "user@example.com"
  },
  "commits": [
    {
      "id": "abc123def456",
      "message": "Commit message",
      "author": {
        "name": "Author Name",
        "email": "author@example.com"
      }
    }
  ]
}
```

### 4. Security Testing

#### Signature Verification Tests
```bash
# Test with valid signature (should pass)
curl -X POST http://localhost:8000/webhooks \
  -H "Stripe-Signature: $(generate_signature valid)" \
  -d '{"test": "data"}'

# Test with invalid signature (should fail with 401)
curl -X POST http://localhost:8000/webhooks \
  -H "Stripe-Signature: invalid" \
  -d '{"test": "data"}'

# Test with missing signature (should fail with 401)
curl -X POST http://localhost:8000/webhooks \
  -d '{"test": "data"}'

# Test with old timestamp (should fail)
curl -X POST http://localhost:8000/webhooks \
  -H "Stripe-Signature: $(generate_signature old_timestamp)" \
  -d '{"test": "data"}'
```

#### Replay Attack Prevention
```bash
# Send same webhook twice
PAYLOAD='{"id": "evt_123", "type": "test"}'
SIG=$(generate_signature "$PAYLOAD")

# First request - should succeed
curl -X POST http://localhost:8000/webhooks \
  -H "Stripe-Signature: $SIG" \
  -d "$PAYLOAD"

# Second request - should be idempotent or rejected
curl -X POST http://localhost:8000/webhooks \
  -H "Stripe-Signature: $SIG" \
  -d "$PAYLOAD"
```

### 5. Error Handling Tests

#### Timeout Scenarios
```bash
# Test webhook endpoint timeout
# Server should respond within 10s (Stripe requirement)

# Simulate slow processing
/test webhook --url http://localhost:8000/slow-webhook \
  --timeout 10
```

#### Malformed Payload Tests
```bash
# Invalid JSON
curl -X POST http://localhost:8000/webhooks \
  -H "Content-Type: application/json" \
  -d 'not valid json'

# Missing required fields
curl -X POST http://localhost:8000/webhooks \
  -H "Content-Type: application/json" \
  -d '{"type": "missing_id"}'

# Wrong content type
curl -X POST http://localhost:8000/webhooks \
  -H "Content-Type: text/plain" \
  -d '{"test": "data"}'
```

### 6. Load Testing Webhooks

#### Concurrent Webhook Delivery
```bash
# Send 100 concurrent webhooks
seq 1 100 | xargs -P 10 -I {} \
  curl -X POST http://localhost:8000/webhooks \
    -H "Stripe-Signature: $(generate_signature)" \
    -d '{"index": "'{}'"}'
```

#### Rate Limiting Test
```bash
# Send webhooks rapidly
for i in {1..1000}; do
  curl -X POST http://localhost:8000/webhooks \
    -d '{"index": '$i'}' &
done
wait
```

### 7. End-to-End Webhook Flow

```yaml
flow: Payment Webhook Flow

steps:
  # 1. Create customer
  - name: Create Customer
    action: api
    endpoint: /api/v1/customers
    method: POST
    body:
      email: webhook-test@example.com
    save: CUSTOMER_ID
    
  # 2. Create payment intent
  - name: Create Payment
    action: api
    endpoint: /api/v1/payments
    method: POST
    body:
      customer: "{{CUSTOMER_ID}}"
      amount: 1000
    save: PAYMENT_INTENT_ID
    
  # 3. Wait for webhook delivery
  - name: Wait for Webhook
    action: wait
    duration: 5
    
  # 4. Verify webhook processed
  - name: Check Payment Status
    action: db
    query: |
      SELECT status 
      FROM payments 
      WHERE stripe_payment_intent_id = '{{PAYMENT_INTENT_ID}}'
    expect: succeeded
    
  # 5. Verify webhook logged
  - name: Verify Webhook Log
    action: db
    query: |
      SELECT COUNT(*) 
      FROM webhook_events 
      WHERE type = 'payment_intent.succeeded'
      AND payment_intent_id = '{{PAYMENT_INTENT_ID}}'
    expect: "1"
```

### 8. Webhook Testing Checklist

- [ ] Webhook endpoint responds with 200
- [ ] Signature verification works correctly
- [ ] Invalid signatures are rejected (401)
- [ ] Missing signatures are rejected (401)
- [ ] Payload parsing handles errors gracefully
- [ ] Idempotency prevents duplicate processing
- [ ] Webhook is processed within timeout window
- [ ] Failed webhooks are retried or logged
- [ ] Webhook events are stored for audit
- [ ] Different event types are handled correctly
- [ ] Concurrent webhooks are processed safely
- [ ] Error responses don't leak sensitive info

### 9. Debugging Failed Webhooks

#### Check Server Logs
```bash
# Look for webhook processing logs
grep "webhook" /var/log/app.log
grep "stripe" /var/log/app.log

# Check for errors
grep "ERROR" /var/log/app.log | grep -i webhook
```

#### Verify Endpoint Accessibility
```bash
# Test from outside network
curl -I https://your-app.com/webhooks

# Check SSL certificate
curl -v https://your-app.com/webhooks 2>&1 | grep -i ssl

# Test with provider's test feature
```

#### Monitor Webhook Delivery
```bash
# Stripe dashboard
# Dashboard > Developers > Webhooks > [endpoint] > Delivery attempts

# Custom logging
tail -f /var/log/webhooks.log
```

### 10. Automated Webhook Testing

```bash
#!/bin/bash
# webhook-test-suite.sh

echo "Running webhook tests..."

# Test 1: Valid webhook
echo "Test 1: Valid webhook"
/test webhook \
  --url http://localhost:8000/webhooks \
  --secret whsec_test \
  --provider stripe \
  --send-test \
  --payload '{"id":"evt_test","type":"test.event"}'

# Test 2: Invalid signature
echo "Test 2: Invalid signature"
response=$(curl -s -w "%{http_code}" -X POST http://localhost:8000/webhooks \
  -H "Stripe-Signature: invalid" \
  -d '{}')
if [ "$response" = "401" ]; then
  echo "✅ Correctly rejected invalid signature"
else
  echo "❌ Should have returned 401, got $response"
fi

# Test 3: Load test
echo "Test 3: Load test"
seq 1 50 | xargs -P 10 -I {} \
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST http://localhost:8000/webhooks \
    -H "Stripe-Signature: $(generate_signature)" \
    -d '{"index": "'{}'"}' | \
  sort | uniq -c

echo "Webhook tests complete"
```
