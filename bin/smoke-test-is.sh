#!/bin/bash
# Post-deploy smoke test for Interview Simulator (codeswiftr.com)
# Source: .forge/heartbeat/results/kimi-smoke-tests.md
set -e

BASE_URL="${IS_API_URL:-https://api.codeswiftr.com}"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

echo "=== Interview Simulator (codeswiftr.com) Smoke Tests ==="
echo "API: $BASE_URL"
echo ""

# --- 1. Health Checks ---
echo "--- Health Checks ---"

STATUS=$(curl -sf "$BASE_URL/health" | jq -r '.status' 2>/dev/null) || fail "Health endpoint unreachable"
[ "$STATUS" = "healthy" ] && pass "Health check: status=healthy" || fail "Health check returned: $STATUS"

READY=$(curl -sf "$BASE_URL/health/ready" | jq -r '.status' 2>/dev/null) || fail "/health/ready unreachable"
[ "$READY" = "ready" ] && pass "Readiness check: status=ready" || fail "Readiness returned: $READY"

echo ""

# --- 2. Auth: Register + Login ---
echo "--- Auth Flow ---"

TEST_EMAIL="smoke-is-$(date +%s)@test.com"
TEST_PASSWORD="TestPass123!"

REGISTER_RESP=$(curl -sf -X POST "$BASE_URL/api/v1/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$TEST_EMAIL\", \"password\": \"$TEST_PASSWORD\", \"full_name\": \"IS Smoke Test\"}" 2>/dev/null) \
  || fail "Register endpoint unreachable"

ACCESS_TOKEN=$(echo "$REGISTER_RESP" | jq -r '.access_token' 2>/dev/null)
[ -n "$ACCESS_TOKEN" ] && [ "$ACCESS_TOKEN" != "null" ] && pass "User registration returned access token" \
  || fail "Registration did not return access_token. Response: $REGISTER_RESP"

ME=$(curl -sf "$BASE_URL/api/v1/auth/me" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq -r '.email' 2>/dev/null) \
  || fail "/auth/me endpoint unreachable"
[ "$ME" = "$TEST_EMAIL" ] && pass "Auth/me returned correct email" || fail "Auth/me returned: $ME"

echo ""

# --- 3. Core Feature: Create Interview Session ---
echo "--- Core Feature: Interview Session ---"

SESSION_RESP=$(curl -sf -X POST "$BASE_URL/api/v1/interviews" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"type": "behavioral", "question_count": 3}' 2>/dev/null) \
  || fail "Interview creation endpoint unreachable"

SESSION_STATUS=$(echo "$SESSION_RESP" | jq -r '.status' 2>/dev/null)
[ "$SESSION_STATUS" = "created" ] && pass "Interview session created: status=created" \
  || fail "Interview session status: $SESSION_STATUS. Response: $SESSION_RESP"

echo ""

# --- 4. Lead Capture ---
echo "--- Lead Capture ---"

LEAD_RESP=$(curl -sf -X POST "$BASE_URL/leads/magnet" \
  -H "Content-Type: application/json" \
  -d "{
    \"email\": \"lead-is-$(date +%s)@example.com\",
    \"domain\": \"codeswiftr.com\",
    \"lead_magnet_slug\": \"system-design-50-questions\",
    \"source_page\": \"/interview-simulator\",
    \"utm_source\": \"smoke-test\"
  }" 2>/dev/null) || fail "Lead capture endpoint unreachable"

LEAD_OK=$(echo "$LEAD_RESP" | jq -r '.ok' 2>/dev/null)
[ "$LEAD_OK" = "true" ] && pass "Lead capture: ok=true" || fail "Lead capture returned: $LEAD_RESP"

echo ""

# --- 5. Stripe Checkout (structural check only — no real payment) ---
echo "--- Stripe Checkout Endpoint ---"

CHECKOUT_HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/api/v1/subscriptions/checkout" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"price_id": "price_test_smoke"}' 2>/dev/null)

# 200 = success, 422 = validation error (endpoint alive), 404 = not found (fail)
[ "$CHECKOUT_HTTP" = "200" ] || [ "$CHECKOUT_HTTP" = "422" ] \
  && pass "Stripe checkout endpoint reachable (HTTP $CHECKOUT_HTTP)" \
  || fail "Stripe checkout endpoint returned HTTP $CHECKOUT_HTTP"

echo ""
echo "=== IS Smoke Tests Complete ==="
