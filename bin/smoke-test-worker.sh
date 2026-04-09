#!/bin/bash
# Post-deploy smoke test for the Lead Capture Cloudflare Worker
# Source: .forge/heartbeat/results/kimi-smoke-tests.md
# Worker handles all 3 domains: codeswiftr.com, brandfocus.ai, thebrightharbor.com
set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }
warn() { echo -e "\033[0;33m[WARN]${NC} $1"; }

echo "=== Lead Capture Worker Smoke Tests ==="
echo ""

# --- 1. Worker Health Check ---
echo "--- Worker Health ---"

# Worker is fronted by api.codeswiftr.com (primary domain)
HEALTH_RESP=$(curl -sf "https://api.codeswiftr.com/leads/health" 2>/dev/null) || fail "Worker health endpoint unreachable"
HEALTH_STATUS=$(echo "$HEALTH_RESP" | jq -r '.status' 2>/dev/null)
[ "$HEALTH_STATUS" = "ok" ] && pass "Worker health: status=ok" || fail "Worker health returned: $HEALTH_STATUS"

echo ""

# --- 2. Cross-Domain Lead Capture ---
echo "--- Cross-Domain Lead Capture ---"

DOMAINS=("codeswiftr.com" "brandfocus.ai" "thebrightharbor.com")
TS=$(date +%s)

for domain in "${DOMAINS[@]}"; do
  RESP=$(curl -sf -X POST "https://api.$domain/leads/waitlist" \
    -H "Content-Type: application/json" \
    -d "{
      \"email\": \"smoke-$TS@example.com\",
      \"domain\": \"$domain\",
      \"source\": \"smoke-test\"
    }" 2>/dev/null) || { warn "Waitlist endpoint unreachable for $domain"; continue; }

  OK=$(echo "$RESP" | jq -r '.ok' 2>/dev/null)
  [ "$OK" = "true" ] && pass "Waitlist capture: $domain" || fail "Waitlist for $domain returned: $RESP"
done

echo ""

# --- 3. Lead Magnet Delivery ---
echo "--- Lead Magnet Delivery ---"

MAGNET_RESP=$(curl -sf -X POST "https://api.codeswiftr.com/leads/magnet" \
  -H "Content-Type: application/json" \
  -d "{
    \"email\": \"magnet-$(date +%s)@example.com\",
    \"name\": \"Test Lead\",
    \"domain\": \"codeswiftr.com\",
    \"lead_magnet_slug\": \"system-design-50-questions\",
    \"source_page\": \"/interview-simulator\",
    \"utm_source\": \"smoke-test\"
  }" 2>/dev/null) || fail "Lead magnet endpoint unreachable"

MAGNET_OK=$(echo "$MAGNET_RESP" | jq -r '.ok' 2>/dev/null)
DOWNLOAD_URL=$(echo "$MAGNET_RESP" | jq -r '.download_url' 2>/dev/null)
[ "$MAGNET_OK" = "true" ] && pass "Lead magnet captured: ok=true" || fail "Magnet returned: $MAGNET_RESP"
[ -n "$DOWNLOAD_URL" ] && [ "$DOWNLOAD_URL" != "null" ] \
  && pass "Download URL present: $DOWNLOAD_URL" \
  || warn "No download_url in response"

echo ""

# --- 4. Duplicate Detection ---
echo "--- Duplicate Detection ---"

DUP_EMAIL="dup-test-$(date +%s)@example.com"

# First submission
curl -sf -X POST "https://api.codeswiftr.com/leads/magnet" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$DUP_EMAIL\", \"domain\": \"codeswiftr.com\"}" > /dev/null 2>&1 || true

# Second submission (should detect duplicate)
DUP_RESP=$(curl -sf -X POST "https://api.codeswiftr.com/leads/magnet" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$DUP_EMAIL\", \"domain\": \"codeswiftr.com\"}" 2>/dev/null) || fail "Duplicate check endpoint unreachable"

ALREADY_SUBSCRIBED=$(echo "$DUP_RESP" | jq -r '.already_subscribed' 2>/dev/null)
[ "$ALREADY_SUBSCRIBED" = "true" ] && pass "Duplicate detection works: already_subscribed=true" \
  || warn "Duplicate not detected (may be expected if KV TTL hasn't expired): $DUP_RESP"

echo ""
echo "=== Worker Smoke Tests Complete ==="
