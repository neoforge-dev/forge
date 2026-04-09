#!/usr/bin/env bash
# stripe-setup.sh — Create Stripe products and prices for FORGE portfolio
# Usage: ./bin/stripe-setup.sh [--live] [STRIPE_SECRET_KEY]
#
# API key precedence:
#   1. Positional argument (non-flag argument)
#   2. STRIPE_SECRET_KEY env var
#
# Flags:
#   --live   Pass-through to stripe CLI (use live mode instead of test mode)

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
LIVE_FLAG=""
API_KEY_ARG=""

for arg in "$@"; do
  case "$arg" in
    --live)
      LIVE_FLAG="--live"
      ;;
    --*)
      echo "ERROR: Unknown flag: $arg" >&2
      echo "Usage: $0 [--live] [STRIPE_SECRET_KEY]" >&2
      exit 1
      ;;
    *)
      if [[ -n "$API_KEY_ARG" ]]; then
        echo "ERROR: Multiple API key arguments supplied." >&2
        exit 1
      fi
      API_KEY_ARG="$arg"
      ;;
  esac
done

# Resolve API key
STRIPE_KEY="${API_KEY_ARG:-${STRIPE_SECRET_KEY:-}}"
if [[ -z "$STRIPE_KEY" ]]; then
  echo "ERROR: No Stripe API key provided." >&2
  echo "Pass it as an argument or set STRIPE_SECRET_KEY in your environment." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
if ! command -v stripe &>/dev/null; then
  echo "ERROR: stripe CLI not found. Install it: https://stripe.com/docs/stripe-cli" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Helper — stripe CLI wrapper (always injects key)
# ---------------------------------------------------------------------------
stripe_cmd() {
  stripe "$@" --api-key "$STRIPE_KEY" $LIVE_FLAG
}

# ---------------------------------------------------------------------------
# Helper — find existing product ID by exact name
# Returns empty string if not found.
# ---------------------------------------------------------------------------
find_product() {
  local name="$1"
  # stripe products list returns JSON lines; we parse with grep + sed
  stripe_cmd products list --limit 100 --expand data.name \
    2>/dev/null \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
target = '''$name'''
for p in data.get('data', []):
    if p.get('name') == target:
        print(p['id'])
        break
" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Helper — find existing price on a product by unit_amount + currency + type
# For recurring: also checks interval and interval_count
# type: 'one_time' | 'recurring'
# interval: 'month' | 'year' (recurring only)
# Returns empty string if not found.
# ---------------------------------------------------------------------------
find_price() {
  local product_id="$1"
  local unit_amount="$2"   # in cents
  local price_type="$3"    # one_time | recurring
  local interval="${4:-}"  # month | year (optional)

  stripe_cmd prices list --product "$product_id" --limit 100 \
    2>/dev/null \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
for p in data.get('data', []):
    if p.get('unit_amount') != $unit_amount:
        continue
    if p.get('type') != '$price_type':
        continue
    if '$price_type' == 'recurring':
        rec = p.get('recurring') or {}
        if rec.get('interval') != '${interval}':
            continue
    print(p['id'])
    break
" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Helper — create or reuse a product
# Sets global PRODUCT_ID
# ---------------------------------------------------------------------------
ensure_product() {
  local name="$1"
  local description="$2"

  echo ""
  echo "--- Product: $name ---"

  PRODUCT_ID=$(find_product "$name")
  if [[ -n "$PRODUCT_ID" ]]; then
    echo "  (exists) $PRODUCT_ID"
  else
    PRODUCT_ID=$(stripe_cmd products create \
      --name "$name" \
      --description "$description" \
      2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
    echo "  (created) $PRODUCT_ID"
  fi
}

# ---------------------------------------------------------------------------
# Helper — create or reuse a recurring price
# Sets global PRICE_ID
# ---------------------------------------------------------------------------
ensure_recurring_price() {
  local product_id="$1"
  local unit_amount="$2"   # cents
  local interval="$3"      # month | year
  local nickname="$4"

  PRICE_ID=$(find_price "$product_id" "$unit_amount" "recurring" "$interval")
  if [[ -n "$PRICE_ID" ]]; then
    echo "  (exists)  $nickname — $PRICE_ID"
  else
    PRICE_ID=$(stripe_cmd prices create \
      --product "$product_id" \
      --unit-amount "$unit_amount" \
      --currency usd \
      --recurring[interval]="$interval" \
      --nickname "$nickname" \
      2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
    echo "  (created) $nickname — $PRICE_ID"
  fi
}

# ---------------------------------------------------------------------------
# Helper — create or reuse a one-time price
# Sets global PRICE_ID
# ---------------------------------------------------------------------------
ensure_onetime_price() {
  local product_id="$1"
  local unit_amount="$2"   # cents
  local nickname="$3"

  PRICE_ID=$(find_price "$product_id" "$unit_amount" "one_time")
  if [[ -n "$PRICE_ID" ]]; then
    echo "  (exists)  $nickname — $PRICE_ID"
  else
    PRICE_ID=$(stripe_cmd prices create \
      --product "$product_id" \
      --unit-amount "$unit_amount" \
      --currency usd \
      --nickname "$nickname" \
      2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
    echo "  (created) $nickname — $PRICE_ID"
  fi
}

# ---------------------------------------------------------------------------
# Mode banner
# ---------------------------------------------------------------------------
MODE="test"
[[ -n "$LIVE_FLAG" ]] && MODE="live"
echo "============================================================"
echo " Stripe Setup — FORGE Portfolio"
echo " Mode: $MODE"
echo "============================================================"

# ===========================================================================
# 1. Interview Simulator (CodeSwiftr)
# ===========================================================================
ensure_product \
  "CodeSwiftr - Interview Simulator" \
  "AI-powered technical interview practice with real-time feedback"

IS_PRODUCT_ID="$PRODUCT_ID"

ensure_recurring_price "$IS_PRODUCT_ID" 2900  "month" "Pro Monthly"
IS_PRICE_PRO_MONTHLY="$PRICE_ID"

ensure_recurring_price "$IS_PRODUCT_ID" 14900 "year"  "Pro Annual"
IS_PRICE_PRO_ANNUAL="$PRICE_ID"

ensure_recurring_price "$IS_PRODUCT_ID" 9900  "month" "Team Monthly"
IS_PRICE_TEAM_MONTHLY="$PRICE_ID"

# ===========================================================================
# 2. Voice Coach (BrandFocus)
# ===========================================================================
ensure_product \
  "BrandFocus - Voice Coach Pro" \
  "AI voice coaching for communication and public speaking skills"

VC_PRODUCT_ID="$PRODUCT_ID"

ensure_recurring_price "$VC_PRODUCT_ID" 1999  "month" "Pro Monthly"
VC_PRICE_PRO_MONTHLY="$PRICE_ID"

ensure_recurring_price "$VC_PRODUCT_ID" 17988 "year"  "Pro Annual"
VC_PRICE_PRO_ANNUAL="$PRICE_ID"

ensure_onetime_price   "$VC_PRODUCT_ID" 500          "PAYG 3 Credits"
VC_PRICE_PAYG="$PRICE_ID"

# ===========================================================================
# 3. Study Flow (BrightHarbor)
# ===========================================================================
# NOTE: Study Flow was killed (Council S159, 4-0). These Stripe products
# should NOT be created for new deployments. Keep for reference only.
ensure_product \
  "BrightHarbor - Study Flow Pro" \
  "Spaced repetition study platform with AI Socratic tutoring"

SF_PRODUCT_ID="$PRODUCT_ID"

ensure_recurring_price "$SF_PRODUCT_ID" 699   "month" "Pro Monthly"
SF_PRICE_PRO_MONTHLY="$PRICE_ID"

ensure_recurring_price "$SF_PRODUCT_ID" 5988  "year"  "Pro Annual"
SF_PRICE_PRO_ANNUAL="$PRICE_ID"

# ===========================================================================
# Summary
# ===========================================================================
echo ""
echo "============================================================"
echo " SUMMARY — Copy-paste into your .env files"
echo " Mode: $MODE"
echo "============================================================"
echo ""
echo "# ── Interview Simulator (services/interview-simulator/backend/.env)"
echo "# Product: $IS_PRODUCT_ID"
echo "STRIPE_PRICE_ID_PRO_MONTHLY=$IS_PRICE_PRO_MONTHLY"
echo "STRIPE_PRICE_ID_PRO_ANNUAL=$IS_PRICE_PRO_ANNUAL"
echo "STRIPE_PRICE_ID_TEAM_MONTHLY=$IS_PRICE_TEAM_MONTHLY"
echo ""
echo "# ── Voice Coach (services/voice-coach/app/backend/.env)"
echo "# Product: $VC_PRODUCT_ID"
echo "STRIPE_PRO_PRICE_ID=$VC_PRICE_PRO_MONTHLY"
echo "STRIPE_PRO_ANNUAL_PRICE_ID=$VC_PRICE_PRO_ANNUAL"
echo "STRIPE_PAYG_PRICE_ID=$VC_PRICE_PAYG"
echo ""
# Study Flow KILLED (Council S159, 4-0) — output disabled
# echo "# ── Study Flow (services/study-flow/backend/.env)"
# echo "# Product: $SF_PRODUCT_ID"
# echo "STRIPE_PRICE_ID_PRO=$SF_PRICE_PRO_MONTHLY"
# echo "STRIPE_PRICE_ID_PREMIUM=$SF_PRICE_PRO_ANNUAL"
echo ""
echo "============================================================"
echo " Done."
echo "============================================================"
