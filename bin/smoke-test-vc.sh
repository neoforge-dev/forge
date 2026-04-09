#!/bin/bash
# Post-deploy smoke test for Voice Coach (brandfocus.ai)
# Source: .forge/heartbeat/results/kimi-smoke-tests.md
set -e

BASE_URL="${VC_API_URL:-https://voice-coach-web-production.up.railway.app}"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

echo "=== Voice Coach (brandfocus.ai) Smoke Tests ==="
echo "API: $BASE_URL"
echo ""

# --- 1. Health Check ---
echo "--- Health Check ---"

HEALTH_RESP=$(curl -sf "$BASE_URL/api/health" 2>/dev/null) || fail "Health endpoint unreachable"
STATUS=$(echo "$HEALTH_RESP" | jq -r '.status' 2>/dev/null)
( [ "$STATUS" = "ok" ] || [ "$STATUS" = "healthy" ] ) && pass "Health check: status=$STATUS" || fail "Health check returned: $STATUS"

echo ""

# --- 2. API Endpoint Reachability (no auth required) ---
echo "--- API Endpoints ---"

# Blog API (public)
BLOG_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$BASE_URL/api/blog/posts" 2>/dev/null)
[ "$BLOG_HTTP" = "200" ] && pass "Blog posts API (HTTP $BLOG_HTTP)" || fail "Blog API returned HTTP $BLOG_HTTP"

# Coach report (expects 401/403 without auth = endpoint exists)
COACH_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$BASE_URL/api/coach/report" 2>/dev/null)
( [ "$COACH_HTTP" = "401" ] || [ "$COACH_HTTP" = "403" ] || [ "$COACH_HTTP" = "405" ] || [ "$COACH_HTTP" = "200" ] ) \
  && pass "Coach report endpoint exists (HTTP $COACH_HTTP)" || fail "Coach report returned HTTP $COACH_HTTP"

# Voice Coach profile (expects 401 without auth)
PROFILE_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$BASE_URL/api/voice-coach/me" 2>/dev/null)
( [ "$PROFILE_HTTP" = "401" ] || [ "$PROFILE_HTTP" = "403" ] ) \
  && pass "Voice Coach profile (protected, HTTP $PROFILE_HTTP)" || fail "Profile returned HTTP $PROFILE_HTTP"

# Billing (expects 401 without auth)
BILLING_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$BASE_URL/api/voice-coach/billing/subscription" 2>/dev/null)
( [ "$BILLING_HTTP" = "401" ] || [ "$BILLING_HTTP" = "403" ] ) \
  && pass "Billing subscription (protected, HTTP $BILLING_HTTP)" || fail "Billing returned HTTP $BILLING_HTTP"

echo ""

# --- 3. Detailed Health Checks ---
echo "--- Extended Health ---"

LIVE_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$BASE_URL/api/health/live" 2>/dev/null)
[ "$LIVE_HTTP" = "200" ] && pass "Liveness probe (HTTP $LIVE_HTTP)" || fail "Liveness returned HTTP $LIVE_HTTP"

READY_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$BASE_URL/api/health/ready" 2>/dev/null)
[ "$READY_HTTP" = "200" ] && pass "Readiness probe (HTTP $READY_HTTP)" || fail "Readiness returned HTTP $READY_HTTP"

DETAILED_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$BASE_URL/api/health/detailed" 2>/dev/null)
[ "$DETAILED_HTTP" = "200" ] && pass "Detailed health (HTTP $DETAILED_HTTP)" || fail "Detailed health returned HTTP $DETAILED_HTTP"

echo ""
echo "=== VC Smoke Tests Complete ==="
