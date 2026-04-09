#!/usr/bin/env bash
# bin/deploy-gate-runner.sh — Master interactive deploy script for FORGE products
#
# Reduces human deploy time from ~55 min of ad-hoc steps to ~15 min.
# Generates secrets, prompts for only the credentials humans must provide,
# deploys, verifies, and notifies.
#
# Usage:
#   ./bin/deploy-gate-runner.sh                  # Interactive product menu
#   ./bin/deploy-gate-runner.sh vc               # Deploy Voice Coach
#   ./bin/deploy-gate-runner.sh vto              # Deploy VTO (Mirrably/forge-tryon)
#   ./bin/deploy-gate-runner.sh septica          # Deploy Septica
#   ./bin/deploy-gate-runner.sh is               # Deploy Interview Simulator (verify only)
#   ./bin/deploy-gate-runner.sh all              # Deploy all products
#   ./bin/deploy-gate-runner.sh vc --dry-run     # Show what would happen (no deploy)
#   ./bin/deploy-gate-runner.sh all --skip-preflight  # Skip CLI/auth checks

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- Flags ---
DRY_RUN=0
SKIP_PREFLIGHT=0
PRODUCT=""

# --- Colors ---
NC='\033[0m'
BOLD='\033[1m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'

log()  { echo -e "${CYAN}[info]${NC} $*"; }
ok()   { echo -e "${GREEN}[ok]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
die()  { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }
dry()  { echo -e "${MAGENTA}[dry-run]${NC} Would run: $*"; }
step() { echo ""; echo -e "${BOLD}  --> $*${NC}"; }

section() {
  echo ""
  echo -e "${BOLD}============================================================${NC}"
  echo -e "${BOLD}  $*${NC}"
  echo -e "${BOLD}============================================================${NC}"
}

confirm() {
  local prompt="$1"
  echo ""
  read -r -p "$(echo -e "${YELLOW}${prompt}${NC} [y/N]: ")" ans
  case "${ans:-}" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

prompt_secret() {
  local var_name="$1"
  local prompt_text="$2"
  local hint="$3"
  echo ""
  echo -e "${YELLOW}  Required: ${var_name}${NC}"
  echo "  Source:   $hint"
  read -r -s -p "  Paste value (hidden): " val
  echo ""
  if [[ -z "$val" ]]; then
    die "No value provided for $var_name. Cannot continue."
  fi
  # Export to environment so subcommands can use it
  export "${var_name}=${val}"
}

run_cmd() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    dry "$*"
  else
    eval "$*"
  fi
}

# =============================================
# Arg parsing
# =============================================

while [[ $# -gt 0 ]]; do
  case "$1" in
    vc|vto|septica|is|all)
      PRODUCT="$1"
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --skip-preflight)
      SKIP_PREFLIGHT=1
      shift
      ;;
    -h|--help)
      cat <<EOF
Usage: $0 [product] [--dry-run] [--skip-preflight]

Products:
  vc        Voice Coach (Railway backend)
  vto       VTO / forge-tryon (Railway API + Cloudflare Pages widget)
  septica   Septica card game (Railway, Elixir/Phoenix)
  is        Interview Simulator (verify only — already live)
  all       All products in priority order (vto → vc → septica → is)

Options:
  --dry-run        Show what would be deployed without executing
  --skip-preflight Skip CLI tool and auth pre-checks

Examples:
  $0               # Interactive menu
  $0 vc            # Deploy Voice Coach
  $0 all --dry-run # Preview all deploys
EOF
      exit 0
      ;;
    *)
      die "Unknown argument: $1  (run $0 --help for usage)"
      ;;
  esac
done

# =============================================
# Header
# =============================================

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║         FORGE Deploy Gate Runner — Revenue Sprint        ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Repo root:  $ROOT_DIR"
echo "  Dry run:    $([[ $DRY_RUN -eq 1 ]] && echo 'YES (no changes will be made)' || echo 'no')"
echo "  Started:    $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo ""
  echo -e "${MAGENTA}  DRY RUN MODE — all deploy commands will be shown but NOT executed${NC}"
fi

# =============================================
# Preflight
# =============================================

if [[ "$SKIP_PREFLIGHT" -eq 0 ]]; then
  section "Preflight Checks"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "Dry-run mode: running preflight in check-only mode"
  fi
  bash "$ROOT_DIR/bin/deploy-preflight.sh" || {
    echo ""
    warn "Preflight reported failures. You can bypass with --skip-preflight"
    confirm "Continue anyway (not recommended)?" || exit 1
  }
fi

# =============================================
# Auto-detect undeployed products
# =============================================

detect_status() {
  local label="$1"
  local url="$2"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
  if [[ "$code" == "200" ]]; then
    echo "LIVE"
  else
    echo "DOWN"
  fi
}

section "Current Deployment Status"

VC_STATUS=$(detect_status "Voice Coach"         "https://api.brandfocus.ai/api/health")
VTO_STATUS=$(detect_status "VTO API"            "https://api.forge-tryon.com/health")
SEPTICA_STATUS=$(detect_status "Septica"        "https://septica-api-production.up.railway.app/api/health")
IS_STATUS=$(detect_status "Interview Simulator" "https://api.codeswiftr.com/health")
MIRRABLY_STATUS=$(detect_status "Mirrably API" "https://api.mirrably.com/v1/health")

status_line() {
  local name="$1"
  local status="$2"
  if [[ "$status" == "LIVE" ]]; then
    echo -e "  ${GREEN}[LIVE]${NC}  $name"
  else
    echo -e "  ${YELLOW}[DOWN]${NC}  $name  ← needs deploy"
  fi
}

echo ""
status_line "VTO (forge-tryon API + widget)"  "$VTO_STATUS"
status_line "Voice Coach (brandfocus.ai)"     "$VC_STATUS"
status_line "Septica (card-games-elixir)"     "$SEPTICA_STATUS"
status_line "Interview Simulator (codeswiftr)" "$IS_STATUS"
status_line "Mirrably API"                    "$MIRRABLY_STATUS"

# =============================================
# Interactive menu (if no product arg)
# =============================================

if [[ -z "$PRODUCT" ]]; then
  section "Select Product to Deploy"
  echo ""
  echo "  1) VTO (Virtual Try-On)   — forge-tryon API + widget"
  echo "  2) Voice Coach            — brandfocus.ai backend"
  echo "  3) Septica                — Romanian card game"
  echo "  4) Interview Simulator    — status check only (already live)"
  echo "  5) All products           — vto → vc → septica"
  echo "  6) Exit"
  echo ""
  read -r -p "$(echo -e "${YELLOW}Select [1-6]:${NC} ")" choice
  case "${choice:-}" in
    1) PRODUCT="vto" ;;
    2) PRODUCT="vc" ;;
    3) PRODUCT="septica" ;;
    4) PRODUCT="is" ;;
    5) PRODUCT="all" ;;
    6) echo "Exiting."; exit 0 ;;
    *) die "Invalid selection: $choice" ;;
  esac
fi

# =============================================
# Secret generation helper
# =============================================

gen_secret_32() { openssl rand -hex 32; }
gen_secret_64() { openssl rand -hex 64; }

print_generated_secrets() {
  local product="$1"
  shift
  echo ""
  echo -e "${CYAN}  Auto-generated secrets for $product (store these safely):${NC}"
  while [[ $# -gt 0 ]]; do
    echo "  $1"
    shift
  done
}

# =============================================
# DEPLOY: VTO (forge-tryon)
# =============================================

deploy_vto() {
  section "VTO (Virtual Try-On) Deploy"
  echo "  API source:    apps/forge-tryon-api/"
  echo "  Widget source: apps/forge-tryon/"
  echo "  Assistant:     apps/forge-tryon-assistant/ (Vercel)"
  echo ""
  echo "  Canonical runbook: apps/forge-tryon/DEPLOY.md"

  local VTO_API_DIR="$ROOT_DIR/apps/forge-tryon-api"
  local VTO_WIDGET_DIR="$ROOT_DIR/apps/forge-tryon"

  [[ -d "$VTO_API_DIR" ]]    || die "VTO API dir not found: $VTO_API_DIR"
  [[ -d "$VTO_WIDGET_DIR" ]] || die "VTO widget dir not found: $VTO_WIDGET_DIR"

  # --- Generate secrets ---
  step "Generating auto-managed secrets"
  local ADMIN_API_KEY ASSET_SIGNING_SECRET
  ADMIN_API_KEY=$(gen_secret_64)
  ASSET_SIGNING_SECRET=$(gen_secret_64)

  print_generated_secrets "VTO" \
    "ADMIN_API_KEY=$ADMIN_API_KEY" \
    "ASSET_SIGNING_SECRET=$ASSET_SIGNING_SECRET"

  # --- No human credentials required for minimal VTO deploy ---
  # Stripe is optional (add when billing webhooks ship per deploy-mirrably.sh note)
  # CORS is config, not a secret

  if ! confirm "Proceed with VTO Railway + Cloudflare Pages deploy?"; then
    warn "VTO deploy cancelled."
    return 1
  fi

  # --- Deploy API (Railway) ---
  step "Deploy VTO API to Railway"
  log "Working dir: $VTO_API_DIR"

  run_cmd "cd '$VTO_API_DIR' && railway link"
  run_cmd "cd '$VTO_API_DIR' && railway add --database postgres 2>/dev/null || true"
  run_cmd "cd '$VTO_API_DIR' && railway variables set ADMIN_API_KEY='$ADMIN_API_KEY'"
  run_cmd "cd '$VTO_API_DIR' && railway variables set ASSET_SIGNING_SECRET='$ASSET_SIGNING_SECRET'"
  run_cmd "cd '$VTO_API_DIR' && railway variables set CORS_ORIGINS='https://widget.forge-tryon.com,https://assistant.forge-tryon.com,https://mirrably.com'"
  run_cmd "cd '$VTO_API_DIR' && railway variables set DEBUG=false"
  run_cmd "cd '$VTO_API_DIR' && railway variables set DEMO_SEED=false"
  run_cmd "cd '$VTO_API_DIR' && railway up"

  if [[ "$DRY_RUN" -eq 0 ]]; then
    log "Waiting 15s for Railway deploy to stabilize..."
    sleep 15
  fi

  # --- Verify API ---
  step "Verify VTO API health"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    local RAILWAY_URL
    RAILWAY_URL=$(cd "$VTO_API_DIR" && railway domain 2>/dev/null | grep -o 'https://[^[:space:]]*' | head -1 || true)
    if [[ -n "$RAILWAY_URL" ]]; then
      log "Railway URL: $RAILWAY_URL"
      curl -sf "$RAILWAY_URL/health" >/dev/null && ok "VTO API /health OK" || warn "VTO API /health check failed — check railway logs"
    else
      warn "Could not retrieve Railway URL — verify manually: railway domain"
    fi
  else
    dry "curl -sf <RAILWAY_URL>/health"
  fi

  # --- Deploy Widget (Cloudflare Pages) ---
  step "Build and deploy VTO widget to Cloudflare Pages"
  log "Working dir: $VTO_WIDGET_DIR"

  if [[ ! -d "$VTO_WIDGET_DIR/node_modules" ]]; then
    run_cmd "cd '$VTO_WIDGET_DIR' && npm ci"
  else
    log "node_modules exists, skipping npm ci"
  fi

  run_cmd "cd '$VTO_WIDGET_DIR' && VITE_API_BASE_URL=https://api.forge-tryon.com npm run build"
  run_cmd "cd '$VTO_WIDGET_DIR' && npx wrangler pages deploy dist --project-name forge-tryon"

  # --- Verify Widget ---
  step "Verify VTO widget"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "https://widget.forge-tryon.com/forge-tryon.js" 2>/dev/null || echo "000")
    if [[ "$code" == "200" ]]; then
      ok "VTO widget JS bundle served (HTTP 200)"
    else
      warn "VTO widget not yet reachable (HTTP $code) — Cloudflare Pages propagation may take 1-2 min"
    fi
  else
    dry "curl check: https://widget.forge-tryon.com/forge-tryon.js"
  fi

  ok "VTO deploy complete."
  echo ""
  echo "  Next steps:"
  echo "  1. Deploy VTO assistant to Vercel: cd apps/forge-tryon-assistant && vercel --prod"
  echo "  2. Set custom domains in Railway + Cloudflare Pages dashboards"
  echo "  3. Configure Cloudflare DNS: api.forge-tryon.com CNAME → Railway URL"
  echo "  4. Send first cold email using: .forge/content/vto-outreach-templates-2026-03-30.md"
}

# =============================================
# DEPLOY: Voice Coach
# =============================================

deploy_vc() {
  section "Voice Coach Deploy"
  echo "  Source:  services/voice-coach/app/backend/"
  echo "  Domain:  api.brandfocus.ai"
  echo "  Runbook: docs/DEPLOY_RUNBOOK.md"

  local VC_DIR="$ROOT_DIR/services/voice-coach/app/backend"
  [[ -d "$VC_DIR" ]] || die "Voice Coach dir not found: $VC_DIR"

  # --- Generate secrets ---
  step "Generating auto-managed secrets"
  local JWT_SECRET_KEY PAYG_WEBHOOK_SECRET
  JWT_SECRET_KEY=$(gen_secret_32)
  PAYG_WEBHOOK_SECRET=$(gen_secret_32)

  print_generated_secrets "Voice Coach" \
    "JWT_SECRET_KEY=$JWT_SECRET_KEY" \
    "PAYG_WEBHOOK_SECRET=$PAYG_WEBHOOK_SECRET"

  # --- Human credentials ---
  step "Collecting credentials (human-provided)"
  echo ""
  echo -e "${BOLD}  Voice Coach requires 3 human-provided credentials:${NC}"

  prompt_secret "DEEPGRAM_API_KEY" \
    "Deepgram API key" \
    "https://console.deepgram.com/keys  (free tier available)"

  prompt_secret "OPENROUTER_API_KEY" \
    "OpenRouter API key" \
    "https://openrouter.ai/keys  (free tier available)"

  prompt_secret "STRIPE_SECRET_KEY" \
    "Stripe secret key" \
    "https://dashboard.stripe.com/apikeys  (use test key sk_test_... for first deploy)"

  if ! confirm "Proceed with Voice Coach Railway deploy?"; then
    warn "Voice Coach deploy cancelled."
    return 1
  fi

  # --- Deploy (Railway) ---
  step "Deploy Voice Coach to Railway"
  log "Working dir: $VC_DIR"

  run_cmd "cd '$VC_DIR' && railway link"
  run_cmd "cd '$VC_DIR' && railway add --database postgres 2>/dev/null || true"
  run_cmd "cd '$VC_DIR' && railway add --database redis 2>/dev/null || true"

  # Application config
  run_cmd "cd '$VC_DIR' && railway variables set ENVIRONMENT=production"
  run_cmd "cd '$VC_DIR' && railway variables set DEBUG=false"
  run_cmd "cd '$VC_DIR' && railway variables set API_V1_PREFIX=/api"

  # Auto-generated secrets
  run_cmd "cd '$VC_DIR' && railway variables set JWT_SECRET_KEY='$JWT_SECRET_KEY'"
  run_cmd "cd '$VC_DIR' && railway variables set PAYG_WEBHOOK_SECRET='$PAYG_WEBHOOK_SECRET'"

  # CORS
  run_cmd "cd '$VC_DIR' && railway variables set ALLOWED_ORIGINS='https://voice-coach.brandfocus.ai,https://app.brandfocus.ai'"

  # AI Services (human-provided)
  run_cmd "cd '$VC_DIR' && railway variables set TRANSCRIPTION_PROVIDER=deepgram"
  run_cmd "cd '$VC_DIR' && railway variables set DEEPGRAM_API_KEY='$DEEPGRAM_API_KEY'"
  run_cmd "cd '$VC_DIR' && railway variables set LLM_PROVIDER=openrouter"
  run_cmd "cd '$VC_DIR' && railway variables set LLM_MODEL=google/gemini-3-flash-preview"
  run_cmd "cd '$VC_DIR' && railway variables set OPENROUTER_API_KEY='$OPENROUTER_API_KEY'"

  # Stripe
  run_cmd "cd '$VC_DIR' && railway variables set STRIPE_SECRET_KEY='$STRIPE_SECRET_KEY'"
  run_cmd "cd '$VC_DIR' && railway variables set STRIPE_PRO_PRICE_ID=price_1TEpGFAVILQFrYCPejnKEafC"
  run_cmd "cd '$VC_DIR' && railway variables set STRIPE_PAYG_PRICE_ID=price_1TEpGGAVILQFrYCP5ec7WvUZ"

  # Auth integration
  run_cmd "cd '$VC_DIR' && railway variables set FORGE_CORE_API_URL=https://api.codeswiftr.com"

  # Quota defaults
  run_cmd "cd '$VC_DIR' && railway variables set FREE_TIER_SESSION_LIMIT=10"
  run_cmd "cd '$VC_DIR' && railway variables set PRO_TIER_SESSION_LIMIT=999999"
  run_cmd "cd '$VC_DIR' && railway variables set PAYG_CREDITS_PER_PACK=3"
  run_cmd "cd '$VC_DIR' && railway variables set PAYG_PRICE_PER_PACK=5.0"

  run_cmd "cd '$VC_DIR' && railway up"

  if [[ "$DRY_RUN" -eq 0 ]]; then
    log "Waiting 15s for Railway deploy to stabilize..."
    sleep 15
  fi

  # --- Verify ---
  step "Verify Voice Coach health"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    local RAILWAY_URL
    RAILWAY_URL=$(cd "$VC_DIR" && railway domain 2>/dev/null | grep -o 'https://[^[:space:]]*' | head -1 || true)
    if [[ -n "$RAILWAY_URL" ]]; then
      log "Railway URL: $RAILWAY_URL"
      curl -sf "$RAILWAY_URL/api/health" >/dev/null && ok "VC /api/health OK" || warn "VC /api/health failed — check railway logs"
    else
      warn "Could not retrieve Railway URL — verify manually: railway domain"
    fi
    # Also try production domain if already configured
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "https://api.brandfocus.ai/api/health" 2>/dev/null || echo "000")
    if [[ "$code" == "200" ]]; then
      ok "VC production domain healthy: api.brandfocus.ai"
    fi
  else
    dry "curl -sf <RAILWAY_URL>/api/health"
  fi

  ok "Voice Coach deploy complete."
  echo ""
  echo "  Next steps:"
  echo "  1. Set Stripe webhook in Stripe Dashboard:"
  echo "     Endpoint: https://api.brandfocus.ai/api/billing/webhook"
  echo "     Events: checkout.session.completed, invoice.payment_succeeded,"
  echo "             customer.subscription.updated, customer.subscription.deleted"
  echo "     Then: railway variables set STRIPE_WEBHOOK_SECRET='whsec_...'"
  echo "  2. Set custom domain: railway domain --set api.brandfocus.ai"
  echo "  3. Add DNS CNAME in Cloudflare: api → Railway hostname"
}

# =============================================
# DEPLOY: Septica
# =============================================

deploy_septica() {
  section "Septica Deploy (Elixir/Phoenix)"
  echo "  Source:  games/card-games-elixir/"
  echo "  Health:  /api/health"
  echo "  Ref:     docs/DEPLOY-GATES-CHECKLIST.md § 3"

  local SEPTICA_DIR="$ROOT_DIR/games/card-games-elixir"
  [[ -d "$SEPTICA_DIR" ]] || die "Septica dir not found: $SEPTICA_DIR"

  # --- Generate secrets ---
  step "Generating auto-managed secrets"
  # Phoenix needs 64-byte (128-char hex) SECRET_KEY_BASE
  local SECRET_KEY_BASE GUARDIAN_SECRET_KEY
  SECRET_KEY_BASE=$(gen_secret_64)
  GUARDIAN_SECRET_KEY=$(gen_secret_32)

  print_generated_secrets "Septica" \
    "SECRET_KEY_BASE=$SECRET_KEY_BASE" \
    "GUARDIAN_SECRET_KEY=$GUARDIAN_SECRET_KEY"

  # --- Human credentials ---
  step "Collecting credentials (human-provided)"
  echo ""
  echo -e "${BOLD}  Septica requires Stripe credentials for in-app purchases:${NC}"
  echo "  (Skip with Ctrl-C if deploying free tier first)"

  prompt_secret "STRIPE_SECRET_KEY" \
    "Stripe secret key" \
    "https://dashboard.stripe.com/apikeys"

  if ! confirm "Proceed with Septica Railway deploy?"; then
    warn "Septica deploy cancelled."
    return 1
  fi

  # --- Deploy (Railway) ---
  step "Deploy Septica to Railway"
  log "Working dir: $SEPTICA_DIR"

  run_cmd "cd '$SEPTICA_DIR' && railway link"
  run_cmd "cd '$SEPTICA_DIR' && railway add --database postgres 2>/dev/null || true"

  # Phoenix production env
  run_cmd "cd '$SEPTICA_DIR' && railway variables set MIX_ENV=prod"
  run_cmd "cd '$SEPTICA_DIR' && railway variables set PHX_SERVER=true"
  run_cmd "cd '$SEPTICA_DIR' && railway variables set POOL_SIZE=10"
  run_cmd "cd '$SEPTICA_DIR' && railway variables set SECRET_KEY_BASE='$SECRET_KEY_BASE'"
  run_cmd "cd '$SEPTICA_DIR' && railway variables set GUARDIAN_SECRET_KEY='$GUARDIAN_SECRET_KEY'"

  # Set PHX_HOST — Railway provides RAILWAY_PUBLIC_DOMAIN but we set explicitly
  run_cmd "cd '$SEPTICA_DIR' && railway variables set PHX_HOST=septica-api-production.up.railway.app"
  run_cmd "cd '$SEPTICA_DIR' && railway variables set CORS_ORIGINS='https://septica.codeswiftr.com,https://codeswiftr.com'"

  # Stripe
  run_cmd "cd '$SEPTICA_DIR' && railway variables set STRIPE_SECRET_KEY='$STRIPE_SECRET_KEY'"

  run_cmd "cd '$SEPTICA_DIR' && railway up"

  if [[ "$DRY_RUN" -eq 0 ]]; then
    log "Waiting 20s for Elixir/Phoenix deploy to stabilize (first deploy includes migration)..."
    sleep 20
  fi

  # --- Verify ---
  step "Verify Septica health"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    local RAILWAY_URL
    RAILWAY_URL=$(cd "$SEPTICA_DIR" && railway domain 2>/dev/null | grep -o 'https://[^[:space:]]*' | head -1 || true)
    if [[ -n "$RAILWAY_URL" ]]; then
      log "Railway URL: $RAILWAY_URL"
      curl -sf "$RAILWAY_URL/api/health" >/dev/null && ok "Septica /api/health OK" || warn "Septica health check failed — check: railway logs"
    else
      warn "Could not retrieve Railway URL — verify manually: railway domain"
    fi
  else
    dry "curl -sf <RAILWAY_URL>/api/health"
  fi

  ok "Septica deploy complete."
  echo ""
  echo "  Next steps:"
  echo "  1. Create Stripe products in dashboard:"
  echo "     - Ad-free: \$2.99 one-time"
  echo "     - Premium: \$4.99/month subscription"
  echo "  2. Set webhook: railway variables set STRIPE_WEBHOOK_SECRET='whsec_...'"
  echo "  3. Set custom domain if desired: railway domain --set septica.codeswiftr.com"
}

# =============================================
# DEPLOY: Interview Simulator (verify only)
# =============================================

deploy_is() {
  section "Interview Simulator — Status Check Only"
  echo "  IS is live at codeswiftr.com — no deploy needed."
  echo "  Use bin/deploy-all.sh for IS re-deploys."
  echo ""
  log "Running IS smoke test..."

  if [[ "$DRY_RUN" -eq 0 ]]; then
    bash "$ROOT_DIR/bin/deploy-smoke-test.sh" is || warn "IS smoke test had failures — investigate"
  else
    dry "bash $ROOT_DIR/bin/deploy-smoke-test.sh is"
  fi

  echo ""
  echo "  GSC Day-7 Checkpoint:"
  echo "  1. Open: https://search.google.com/search-console/performance"
  echo "  2. Domain: codeswiftr.com"
  echo "  3. Note top queries + impressions"
  echo "  4. Update: .forge/context/codeswiftr/lead-context.md"
}

# =============================================
# Post-deploy notification
# =============================================

notify_complete() {
  local product="$1"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    dry "forge notify alert 'Deployed: $product'"
    return
  fi
  if command -v "forge" >/dev/null 2>&1; then
    forge notify alert "Deployed: $product" 2>/dev/null || true
    log "Notification sent: Deployed $product"
  else
    warn "forge CLI not found — notification skipped"
  fi
}

# =============================================
# Main dispatch
# =============================================

DEPLOYED=()
FAILED=()

run_product() {
  local p="$1"
  case "$p" in
    vto)     deploy_vto     && DEPLOYED+=("vto")     || FAILED+=("vto") ;;
    vc)      deploy_vc      && DEPLOYED+=("vc")      || FAILED+=("vc") ;;
    septica) deploy_septica && DEPLOYED+=("septica") || FAILED+=("septica") ;;
    is)      deploy_is      && DEPLOYED+=("is")      || FAILED+=("is") ;;
    *)       die "Unknown product: $p" ;;
  esac
  notify_complete "$p"
}

case "$PRODUCT" in
  vto|vc|septica|is)
    run_product "$PRODUCT"
    ;;
  all)
    # Priority order per DEPLOY-GATES-CHECKLIST.md
    run_product "vto"
    run_product "vc"
    run_product "septica"
    run_product "is"
    ;;
  *)
    die "Unknown product: $PRODUCT"
    ;;
esac

# =============================================
# Final summary
# =============================================

section "Deploy Summary"
echo ""
if [[ ${#DEPLOYED[@]} -gt 0 ]]; then
  echo -e "${GREEN}  Deployed successfully:${NC}"
  for p in "${DEPLOYED[@]}"; do
    echo -e "    ${GREEN}[ok]${NC} $p"
  done
fi
if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo ""
  echo -e "${RED}  Failed:${NC}"
  for p in "${FAILED[@]}"; do
    echo -e "    ${RED}[fail]${NC} $p"
  done
  echo ""
  echo "  Troubleshooting:"
  echo "    railway logs           # Check deploy logs"
  echo "    railway status         # Check project status"
  echo "    wrangler tail          # Check Cloudflare worker logs"
fi
echo ""
echo "  Completed: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

if [[ ${#FAILED[@]} -gt 0 ]]; then
  exit 1
fi
exit 0
