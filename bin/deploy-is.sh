#!/usr/bin/env bash
# deploy-is.sh — Deploy Interview Simulator backend to Railway
#
# Usage:
#   bash bin/deploy-is.sh              # deploy backend only
#   bash bin/deploy-is.sh --frontend   # deploy backend + frontend
#   bash bin/deploy-is.sh --smoke      # post-deploy smoke test only
#
# Prerequisites:
#   railway CLI installed (npm i -g @railway/cli)
#   railway login
#   ANTHROPIC_API_KEY, STRIPE_SECRET_KEY, RESEND_API_KEY set in Railway env

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/services/interview-simulator/backend"
FRONTEND_DIR="$REPO_ROOT/services/interview-simulator/frontend"
LANDING_DIR="$REPO_ROOT/apps/codeswiftr-landing"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[IS deploy]${NC} $*"; }
warn()  { echo -e "${YELLOW}[IS deploy]${NC} $*"; }
error() { echo -e "${RED}[IS deploy]${NC} $*" >&2; }

FRONTEND=0
SMOKE_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --frontend)   FRONTEND=1 ;;
    --smoke)      SMOKE_ONLY=1 ;;
    --help|-h)
      echo "Usage: bash bin/deploy-is.sh [--frontend] [--smoke]"
      exit 0 ;;
  esac
done

# ── Smoke-only mode ───────────────────────────────────────────────────────────
if [[ "$SMOKE_ONLY" == "1" ]]; then
  info "Running post-deploy smoke tests..."
  bash "$REPO_ROOT/bin/smoke-test-is.sh"
  exit 0
fi

# ── Preflight ─────────────────────────────────────────────────────────────────
info "Checking railway CLI..."
if ! command -v railway &>/dev/null; then
  error "railway CLI not found. Install with: npm i -g @railway/cli"
  exit 1
fi

info "Railway login status..."
railway whoami 2>/dev/null || { error "Not logged in. Run: railway login"; exit 1; }

# ── Required env vars check ───────────────────────────────────────────────────
MISSING=()
for var in ANTHROPIC_API_KEY STRIPE_SECRET_KEY; do
  [[ -z "${!var:-}" ]] && MISSING+=("$var")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  warn "Missing env vars (set in Railway dashboard OR export locally):"
  for v in "${MISSING[@]}"; do warn "  $v"; done
  warn "Continuing — Railway will use vars already set in the project."
fi

# ── Backend deploy ────────────────────────────────────────────────────────────
info "Deploying IS backend to Railway..."
cd "$BACKEND_DIR"

# Run migrations before starting server
info "Running DB migrations..."
railway run uv run alembic upgrade head || warn "Migration failed — DB may need manual setup on first deploy"

info "Deploying backend service..."
railway up --detach

info "Backend deploy triggered. Check Railway dashboard for status."
info "API will be at: https://api.codeswiftr.com (after DNS propagation)"

# ── Required Railway env vars reference ───────────────────────────────────────
cat <<'ENV_REF'

── Required Railway env vars (set in Railway dashboard) ──────────────────────
  SECRET_KEY          = $(openssl rand -hex 32)
  DATABASE_URL        = postgresql+asyncpg://... (Railway provides)
  ANTHROPIC_API_KEY   = sk-ant-...
  OPENAI_API_KEY      = sk-...         (Whisper transcription)
  STRIPE_SECRET_KEY   = sk_live_...
  STRIPE_WEBHOOK_SECRET = whsec_...    (after webhook creation)
  RESEND_API_KEY      = re_...
  CORS_ORIGINS        = https://app.codeswiftr.com,https://codeswiftr.com
  ENVIRONMENT         = production
  FRONTEND_URL        = https://app.codeswiftr.com
  REDIS_URL           = redis://...    (Railway provides)

── Stripe webhook ─────────────────────────────────────────────────────────────
  URL:    https://api.codeswiftr.com/api/v1/subscriptions/webhook
  Events: checkout.session.completed
          customer.subscription.updated
          customer.subscription.deleted
          invoice.payment_failed

ENV_REF

# ── Frontend deploy (optional) ────────────────────────────────────────────────
if [[ "$FRONTEND" == "1" ]]; then
  info "Building IS frontend..."
  cd "$FRONTEND_DIR"
  if [[ ! -f ".env.production" ]]; then
    error ".env.production not found. Copy .env.example and set VITE_API_URL=https://api.codeswiftr.com"
    exit 1
  fi
  SKIP_PRERENDER=true npm run build
  info "Deploying frontend to Cloudflare Pages..."
  wrangler pages deploy dist --project-name=interview-simulator --branch=main
  info "Frontend deployed to app.codeswiftr.com"

  info "Deploying landing page to Cloudflare Pages..."
  cd "$LANDING_DIR"
  wrangler pages deploy . --project-name=codeswiftr-landing --branch=main
  info "Landing page deployed to codeswiftr.com"
fi

# ── Post-deploy smoke test ────────────────────────────────────────────────────
info "Waiting 15s for service to start..."
sleep 15
info "Running smoke tests..."
IS_API_URL="https://api.codeswiftr.com" bash "$REPO_ROOT/bin/smoke-test-is.sh" \
  && info "All smoke tests passed ✓" \
  || warn "Smoke tests failed — check Railway logs: railway logs"
