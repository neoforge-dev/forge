#!/usr/bin/env bash
# bin/deploy-preflight.sh — Pre-deploy checks for FORGE products
#
# Verifies all CLIs, credentials, and network connectivity are in order
# before attempting any deployment.
#
# Usage: ./bin/deploy-preflight.sh [--quiet]
# Exit code: 0 = all checks pass, 1 = one or more checks failed

set -uo pipefail

QUIET=0
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

# --- Colors ---
NC='\033[0m'
BOLD='\033[1m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'

log()  { [[ "$QUIET" -eq 0 ]] && echo -e "${CYAN}[info]${NC} $*"; true; }
ok()   { echo -e "${GREEN}[pass]${NC} $*"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail() { echo -e "${RED}[fail]${NC} $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; WARN_COUNT=$((WARN_COUNT + 1)); }

section() {
  echo ""
  echo -e "${BOLD}== $* ==${NC}"
}

# Parse args
for arg in "$@"; do
  case "$arg" in
    --quiet) QUIET=1 ;;
    -h|--help)
      echo "Usage: $0 [--quiet]"
      echo "  --quiet   Suppress info messages, only show pass/fail/warn"
      exit 0
      ;;
  esac
done

echo -e "${BOLD}FORGE Deploy Preflight${NC}"
echo "Started: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# =============================================
section "Required CLI Tools"
# =============================================

check_cmd() {
  local cmd="$1"
  local install_hint="$2"
  if command -v "$cmd" >/dev/null 2>&1; then
    local ver
    # openssl version uses 'version' subcommand, not --version flag
    if [[ "$cmd" == "openssl" ]]; then
      ver=$(openssl version 2>&1 | head -1 || echo "unknown")
    else
      ver=$("$cmd" --version 2>&1 | head -1 || echo "unknown")
    fi
    ok "$cmd found — $ver"
  else
    fail "$cmd not found — Install: $install_hint"
  fi
}

check_cmd "railway"  "npm install -g @railway/cli"
check_cmd "wrangler" "npm install -g wrangler"
check_cmd "curl"     "apt-get install curl  OR  brew install curl"
check_cmd "jq"       "apt-get install jq    OR  brew install jq"
check_cmd "openssl"  "apt-get install openssl OR brew install openssl"

# Optional but useful
if command -v "vercel" >/dev/null 2>&1; then
  ok "vercel found (optional, needed for VTO assistant)"
else
  warn "vercel not found — Optional for VTO assistant deploy. Install: npm install -g vercel"
fi

if command -v "npm" >/dev/null 2>&1; then
  ok "npm found — $(npm --version 2>&1)"
else
  warn "npm not found — Required for frontend builds. Install: https://nodejs.org"
fi

# =============================================
section "Railway Authentication"
# =============================================

if command -v "railway" >/dev/null 2>&1; then
  log "Checking railway whoami..."
  RAILWAY_USER=$(railway whoami 2>&1 || true)
  if echo "$RAILWAY_USER" | grep -q "@" 2>/dev/null; then
    ok "Railway authenticated as: $RAILWAY_USER"
  elif echo "$RAILWAY_USER" | grep -qi "not logged in\|not authenticated\|login" 2>/dev/null; then
    fail "Railway not authenticated — Fix: railway login  (opens browser OAuth)"
  else
    warn "Railway auth status unclear: $RAILWAY_USER — Run 'railway whoami' manually"
  fi
else
  warn "Skipping Railway auth check (railway CLI not installed)"
fi

# =============================================
section "Wrangler Authentication"
# =============================================

if command -v "wrangler" >/dev/null 2>&1; then
  log "Checking wrangler whoami..."
  WRANGLER_USER=$(wrangler whoami 2>&1 || true)
  if echo "$WRANGLER_USER" | grep -qi "You are logged in\|account\|email" 2>/dev/null; then
    ok "Wrangler authenticated"
  elif echo "$WRANGLER_USER" | grep -qi "not authenticated\|login\|not logged" 2>/dev/null; then
    fail "Wrangler not authenticated — Fix: wrangler login  (opens browser OAuth)"
  else
    warn "Wrangler auth status unclear — Run 'wrangler whoami' manually to verify"
  fi
else
  warn "Skipping Wrangler auth check (wrangler CLI not installed)"
fi

# =============================================
section "Network Connectivity"
# =============================================

check_url() {
  local label="$1"
  local url="$2"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
  if [[ "$code" == "200" || "$code" == "301" || "$code" == "302" || "$code" == "307" || "$code" == "308" ]]; then
    ok "$label reachable (HTTP $code)"
  else
    fail "$label unreachable (HTTP $code) — Check internet connection or VPN"
  fi
}

check_url "railway.app"     "https://railway.app"
check_url "cloudflare.com"  "https://cloudflare.com"

# =============================================
section "Existing Health Endpoints (Pre-Deploy Status)"
# =============================================

log "Checking which products are already live..."

check_health() {
  local label="$1"
  local url="$2"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
  if [[ "$code" == "200" ]]; then
    ok "$label — ALREADY LIVE at $url"
  elif [[ "$code" == "000" || "$code" == "502" || "$code" == "503" || "$code" == "504" ]]; then
    warn "$label — NOT DEPLOYED (HTTP $code) — deploy needed"
  else
    warn "$label — Unexpected status (HTTP $code) at $url"
  fi
}

check_health "Voice Coach backend"   "https://api.brandfocus.ai/api/health"
check_health "VTO API"               "https://api.forge-tryon.com/health"
check_health "VTO Widget"            "https://widget.forge-tryon.com/forge-tryon.js"
check_health "Septica backend"       "https://septica-api-production.up.railway.app/api/health"
check_health "Interview Simulator"   "https://api.codeswiftr.com/health"
check_health "Mirrably API"          "https://api.mirrably.com/v1/health"

# =============================================
section "Secret Generation Capability"
# =============================================

if command -v "openssl" >/dev/null 2>&1; then
  TEST_SECRET=$(openssl rand -hex 32 2>/dev/null || true)
  if [[ ${#TEST_SECRET} -eq 64 ]]; then
    ok "openssl rand -hex 32 works (64-char hex output confirmed)"
  else
    fail "openssl rand -hex 32 produced unexpected output: '${TEST_SECRET}'"
  fi
else
  fail "openssl not available — cannot auto-generate secrets"
fi

# =============================================
section "Summary"
# =============================================

echo ""
echo "Results:"
echo -e "  ${GREEN}Passed${NC}: $PASS_COUNT"
echo -e "  ${YELLOW}Warnings${NC}: $WARN_COUNT"
echo -e "  ${RED}Failed${NC}: $FAIL_COUNT"
echo ""

if [[ "$FAIL_COUNT" -gt 0 ]]; then
  echo -e "${RED}PREFLIGHT FAILED${NC} — Fix the $FAIL_COUNT failure(s) above before deploying."
  echo "Common fixes:"
  echo "  railway login      # Authenticate Railway"
  echo "  wrangler login     # Authenticate Cloudflare"
  echo "  npm i -g @railway/cli wrangler vercel  # Install all CLIs at once"
  exit 1
elif [[ "$WARN_COUNT" -gt 0 ]]; then
  echo -e "${YELLOW}PREFLIGHT PASSED WITH WARNINGS${NC} — Review the $WARN_COUNT warning(s) above."
  echo "Warnings are non-blocking but may cause issues during deploy."
  exit 0
else
  echo -e "${GREEN}PREFLIGHT PASSED${NC} — All checks passed. Ready to deploy."
  exit 0
fi
