# E2E Flow Test Template

Use this template when creating end-to-end integration tests for FORGE projects.

## Flow Structure

```yaml
name: User Signup to Payment Flow
description: Complete user journey from signup to successful payment
version: "1.0"
environment: staging
timeout: 300
```

## Environment Configuration

```yaml
environments:
  dev:
    api_url: http://localhost:8000
    web_url: http://localhost:3000
    db_url: postgresql://localhost/dev
    
  staging:
    api_url: https://staging-api.example.com
    web_url: https://staging.example.com
    db_url: ${STAGING_DATABASE_URL}
    
  production:
    api_url: https://api.example.com
    web_url: https://example.com
    db_url: ${PROD_DATABASE_URL}
```

## Flow: Signup to Payment

### Step 1: User Registration
```yaml
step:
  name: Create User Account
  action: api
  endpoint: /api/v1/auth/signup
  method: POST
  body:
    email: e2e-test-{{random}}@example.com
    password: TestPassword123!
    first_name: Test
    last_name: User
  expect:
    status: 201
    field: .user.id
    save_as: USER_ID
    field: .user.email_verified
    value: false
```

### Step 2: Email Verification
```yaml
step:
  name: Verify Email
  action: db
  query: |
    SELECT verification_token 
    FROM email_verifications 
    WHERE user_id = '{{USER_ID}}' 
    ORDER BY created_at DESC 
    LIMIT 1
  save_as: VERIFICATION_TOKEN
  
step:
  name: Submit Verification
  action: api
  endpoint: /api/v1/auth/verify-email
  method: POST
  body:
    token: "{{VERIFICATION_TOKEN}}"
  expect:
    status: 200
```

### Step 3: User Login
```yaml
step:
  name: Login
  action: api
  endpoint: /api/v1/auth/login
  method: POST
  body:
    email: "{{USER_EMAIL}}"
    password: TestPassword123!
  expect:
    status: 200
    field: .token
    save_as: AUTH_TOKEN
    field: .user.email_verified
    value: true
```

### Step 4: Create Payment Method
```yaml
step:
  name: Add Payment Method
  action: api
  endpoint: /api/v1/payments/methods
  method: POST
  auth:
    type: bearer
    token: "{{AUTH_TOKEN}}"
  body:
    type: card
    card:
      number: "4242424242424242"
      exp_month: 12
      exp_year: 2030
      cvc: "123"
  expect:
    status: 200
    field: .id
    save_as: PAYMENT_METHOD_ID
```

### Step 5: Create Subscription
```yaml
step:
  name: Create Subscription
  action: api
  endpoint: /api/v1/subscriptions
  method: POST
  auth:
    type: bearer
    token: "{{AUTH_TOKEN}}"
  body:
    price_id: price_test_monthly
    payment_method: "{{PAYMENT_METHOD_ID}}"
  expect:
    status: 200
    field: .status
    value: active
    save_as: SUBSCRIPTION_ID
```

### Step 6: Verify Webhook Received
```yaml
step:
  name: Wait for Webhook
  action: wait
  duration: 5
  
step:
  name: Verify Webhook Processing
  action: db
  query: |
    SELECT COUNT(*) 
    FROM webhook_events 
    WHERE type = 'subscription.created' 
    AND data->>'subscription_id' = '{{SUBSCRIPTION_ID}}'
  expect:
    result: "1"
```

### Step 7: Access Premium Features
```yaml
step:
  name: Access Premium Content
  action: api
  endpoint: /api/v1/premium/features
  method: GET
  auth:
    type: bearer
    token: "{{AUTH_TOKEN}}"
  expect:
    status: 200
    field: .has_access
    value: true
```

### Cleanup
```yaml
step:
  name: Cancel Subscription
  action: api
  endpoint: /api/v1/subscriptions/{{SUBSCRIPTION_ID}}
  method: DELETE
  auth:
    type: bearer
    token: "{{AUTH_TOKEN}}"
  expect:
    status: 200
    
step:
  name: Delete User
  action: api
  endpoint: /api/v1/users/me
  method: DELETE
  auth:
    type: bearer
    token: "{{AUTH_TOKEN}}"
  expect:
    status: 204
```

## Flow: Content Creation

### Step 1: Authenticate
```yaml
step:
  name: Login as Content Creator
  action: api
  endpoint: /api/v1/auth/login
  method: POST
  body:
    email: creator@example.com
    password: password
  expect:
    status: 200
    save_as: CREATOR_TOKEN
```

### Step 2: Upload Media
```yaml
step:
  name: Upload Image
  action: api
  endpoint: /api/v1/media/upload
  method: POST
  auth:
    type: bearer
    token: "{{CREATOR_TOKEN}}"
  multipart:
    - name: file
      filename: test-image.jpg
      content_type: image/jpeg
      path: ./fixtures/test-image.jpg
  expect:
    status: 200
    save_as: MEDIA_ID
```

### Step 3: Create Content
```yaml
step:
  name: Create Article
  action: api
  endpoint: /api/v1/content
  method: POST
  auth:
    type: bearer
    token: "{{CREATOR_TOKEN}}"
  body:
    title: E2E Test Article
    body: This is a test article created during E2E testing
    media_ids:
      - "{{MEDIA_ID}}"
    status: draft
  expect:
    status: 201
    save_as: CONTENT_ID
```

### Step 4: Process Content (Async)
```yaml
step:
  name: Submit for Processing
  action: queue
  task: content.process
  args:
    - "{{CONTENT_ID}}"
  
step:
  name: Wait for Processing
  action: wait
  duration: 10
  
step:
  name: Check Processing Status
  action: api
  endpoint: /api/v1/content/{{CONTENT_ID}}/status
  method: GET
  auth:
    type: bearer
    token: "{{CREATOR_TOKEN}}"
  expect:
    status: 200
    field: .status
    value: processed
```

### Step 5: Publish
```yaml
step:
  name: Publish Content
  action: api
  endpoint: /api/v1/content/{{CONTENT_ID}}/publish
  method: POST
  auth:
    type: bearer
    token: "{{CREATOR_TOKEN}}"
  expect:
    status: 200
    field: .status
    value: published
    field: .published_at
    not_null: true
```

### Step 6: Verify Public Access
```yaml
step:
  name: View Public Content
  action: api
  endpoint: /api/v1/public/content/{{CONTENT_ID}}
  method: GET
  expect:
    status: 200
    field: .title
    value: E2E Test Article
```

## Flow: Data Import

### Step 1: Upload Import File
```yaml
step:
  name: Upload CSV
  action: api
  endpoint: /api/v1/imports
  method: POST
  auth:
    type: bearer
    token: "{{AUTH_TOKEN}}"
  multipart:
    - name: file
      filename: data.csv
      content_type: text/csv
      path: ./fixtures/import-data.csv
  expect:
    status: 200
    save_as: IMPORT_ID
```

### Step 2: Validate Import
```yaml
step:
  name: Check Validation
  action: api
  endpoint: /api/v1/imports/{{IMPORT_ID}}/validate
  method: POST
  auth:
    type: bearer
    token: "{{AUTH_TOKEN}}"
  expect:
    status: 200
    field: .valid
    value: true
    field: .row_count
    value: 100
```

### Step 3: Process Import
```yaml
step:
  name: Start Import
  action: api
  endpoint: /api/v1/imports/{{IMPORT_ID}}/process
  method: POST
  auth:
    type: bearer
    token: "{{AUTH_TOKEN}}"
  expect:
    status: 202
    
step:
  name: Wait for Processing
  action: poll
  endpoint: /api/v1/imports/{{IMPORT_ID}}/status
  method: GET
  auth:
    type: bearer
    token: "{{AUTH_TOKEN}}"
  until:
    field: .status
    in: [completed, failed]
  timeout: 300
  expect:
    status: 200
    field: .status
    value: completed
    field: .processed_rows
    value: 100
```

### Step 4: Verify Data
```yaml
step:
  name: Verify Imported Records
  action: db
  query: |
    SELECT COUNT(*) 
    FROM imported_records 
    WHERE import_id = '{{IMPORT_ID}}'
  expect:
    result: "100"
```

## Error Recovery

### Retry Failed Step
```yaml
step:
  name: flaky_operation
  action: api
  endpoint: /api/v1/flaky
  method: GET
  retry:
    count: 3
    delay: 2
    on_status: [502, 503, 504]
  expect:
    status: 200
```

### Conditional Steps
```yaml
step:
  name: Conditional Cleanup
  action: api
  endpoint: /api/v1/cleanup
  method: POST
  condition: "{{SUBSCRIPTION_ID}} != ''"
  body:
    subscription_id: "{{SUBSCRIPTION_ID}}"
```

## Validation Patterns

### Database State Verification
```yaml
step:
  name: Verify User Created
  action: db
  query: |
    SELECT COUNT(*) 
    FROM users 
    WHERE id = '{{USER_ID}}' 
    AND email_verified = true
  expect:
    result: "1"
```

### External Service Verification
```yaml
step:
  name: Verify Stripe Customer
  action: external
  service: stripe
  operation: retrieve_customer
  args:
    customer_id: "{{STRIPE_CUSTOMER_ID}}"
  expect:
    email: "{{USER_EMAIL}}"
```

### Queue Verification
```yaml
step:
  name: Verify Task Queued
  action: queue_check
  queue: default
  filter:
    task_name: email.send
    args:
      to: "{{USER_EMAIL}}"
  expect:
    count: 1
```

## Flow Checklist

- [ ] All steps have clear names
- [ ] Environment variables documented
- [ ] Data dependencies between steps identified
- [ ] Cleanup steps included
- [ ] Error handling and retries configured
- [ ] Assertions validate business logic, not just status codes
- [ ] Test data is isolated (unique identifiers)
- [ ] No hardcoded secrets or credentials
- [ ] Flow completes within timeout
- [ ] Results are logged for debugging
