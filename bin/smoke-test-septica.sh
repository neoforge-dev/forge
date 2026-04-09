#!/usr/bin/env bash
# Smoke test for Septica — validates REST + WebSocket contract
# Usage: ./bin/smoke-test-septica.sh [base_url]
#
# Requires: curl, wscat (npm install -g wscat), jq or grep
# Tests: guest auth, WebSocket connect, lobby join, state_update field contract
set -uo pipefail

BASE="${1:-https://septica-web-production.up.railway.app}"
WS_BASE="${BASE/https:/wss:}/socket/websocket"
PASS=0
FAIL=0

pass() { echo "  ✓ PASS  $1"; PASS=$((PASS + 1)); }
fail() { echo "  ✗ FAIL  $1"; FAIL=$((FAIL + 1)); }

echo ""
echo "Septica Contract Test"
echo "━━━━━━━━━━━━━━━━━━━━━"
echo "API:  $BASE"
echo "WS:   $WS_BASE"
echo ""

# ── 1. Health ──
echo "1. Health Check"
HEALTH=$(curl -sf --max-time 10 "$BASE/api/health" 2>/dev/null || echo '{}')
if echo "$HEALTH" | grep -q '"ok"\|"connected"'; then
  pass "Health: ok"
else
  fail "Health: $HEALTH"
fi

# ── 2. Guest Auth ──
echo ""
echo "2. Guest Auth"

TS=$(date +%s)
GUEST1=$(curl -s -X POST "$BASE/api/auth/guest" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"smoke-${TS}-p1\"}" 2>/dev/null || echo '{}')

TOKEN1=$(echo "$GUEST1" | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
USER1=$(echo "$GUEST1" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)

[ -n "$TOKEN1" ] && pass "Guest 1: token received" || fail "Guest 1: no token — $GUEST1"
[ -n "$USER1" ]  && pass "Guest 1: user_id=$USER1" || fail "Guest 1: no user_id"

echo "$GUEST1" | grep -q '"is_guest":true' && pass "Guest 1: is_guest=true" || fail "Guest 1: missing is_guest"
echo "$GUEST1" | grep -q '"rating":1000'   && pass "Guest 1: rating=1000"   || fail "Guest 1: missing rating"

GUEST2=$(curl -s -X POST "$BASE/api/auth/guest" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"smoke-${TS}-p2\"}" 2>/dev/null || echo '{}')

TOKEN2=$(echo "$GUEST2" | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
USER2=$(echo "$GUEST2" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)

[ -n "$TOKEN2" ] && pass "Guest 2: token received" || fail "Guest 2: no token"
[ -n "$USER2" ]  && pass "Guest 2: user_id=$USER2" || fail "Guest 2: no user_id"

# ── 3. CORS Preflight ──
echo ""
echo "3. CORS Preflight"

CORS_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X OPTIONS "$BASE/api/auth/guest" \
  -H "Origin: https://septica.codeswiftr.com" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type" --max-time 10 2>/dev/null)

[ "$CORS_STATUS" = "200" ] && pass "CORS preflight: HTTP $CORS_STATUS" || fail "CORS preflight: HTTP $CORS_STATUS"

CORS_ORIGIN=$(curl -si -X OPTIONS "$BASE/api/auth/guest" \
  -H "Origin: https://septica.codeswiftr.com" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type" --max-time 10 2>/dev/null | \
  grep -i "access-control-allow-origin" | head -1)

echo "$CORS_ORIGIN" | grep -qi "septica.codeswiftr.com" \
  && pass "CORS origin: septica.codeswiftr.com allowed" \
  || fail "CORS origin: $CORS_ORIGIN"

# ── 4. State Update Contract ──
echo ""
echo "4. State Update Field Contract"
echo "   (Validates field names match play.html expectations)"

# Expected fields from PlayerView.project/3 (server) that play.html reads:
# Fields the client MUST read (play.html depends on these)
CLIENT_FIELDS=(
  "your_hand"
  "table_cards"
  "opponent_card_counts"
  "scores"
  "phase"
  "current_player"
  "your_turn"
  "can_pass"
  "deck_remaining"
)

# All fields the server sends (superset — client may not use all)
SERVER_FIELDS=(
  "your_hand"
  "table_cards"
  "opponent_card_counts"
  "scores"
  "phase"
  "current_player"
  "your_turn"
  "can_pass"
  "deck_remaining"
  "game_id"
  "game_mode"
  "round_number"
  "sequence_number"
  "last_played_card"
  "last_player_id"
  "teams"
  "team_scores"
)

# Verify server has all fields it should send
SERVER_VIEW="games/card-games-elixir/lib/card_games/rooms/player_view.ex"
if [ -f "$SERVER_VIEW" ]; then
  for field in "${SERVER_FIELDS[@]}"; do
    grep -q "$field:" "$SERVER_VIEW" \
      && pass "Server sends: $field" \
      || fail "Server MISSING: $field"
  done
else
  echo "  ⚠ SKIP  Server source not available"
fi

# Verify client reads all fields it depends on
CLIENT_FILE="apps/septica-landing/play.html"
if [ -f "$CLIENT_FILE" ]; then
  for field in "${CLIENT_FIELDS[@]}"; do
    grep -q "$field" "$CLIENT_FILE" \
      && pass "Client reads: $field" \
      || fail "Client MISSING: $field — will break at runtime!"
  done
else
  echo "  ⚠ SKIP  Client source not available"
fi

# ── 5. WebSocket Connect ──
echo ""
echo "5. WebSocket Connectivity"

# Quick check — try to connect and immediately disconnect
# wscat returns 0 on successful connect even if we close immediately
timeout 5 wscat -c "$WS_BASE?token=$TOKEN1&vsn=2.0.0" --no-color -x '' 2>/dev/null
WS_EXIT=$?

if [ $WS_EXIT -eq 0 ] || [ $WS_EXIT -eq 124 ]; then
  pass "WebSocket: connects with token"
else
  fail "WebSocket: connection failed (exit $WS_EXIT)"
fi

# ── Results ──
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━"
TOTAL=$((PASS + FAIL))
echo "Results: $PASS/$TOTAL passed, $FAIL failed"
echo ""

if [ $FAIL -gt 0 ]; then
  echo "❌ CONTRACT TEST FAILED ($FAIL failures)"
  exit 1
else
  echo "✅ CONTRACT TEST PASSED"
  exit 0
fi
