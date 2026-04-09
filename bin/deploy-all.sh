#!/usr/bin/env bash
# deploy-all.sh — Master deploy script (Interview Simulator)
# Interactive, colorized, and intentionally conservative.
#
# What it does:
# 1) Checks railway + wrangler are installed and authenticated
# 2) Runs Interview Simulator backend tests (quick)
# 3) Builds Interview Simulator frontend
# 4) Asks for confirmation
# 5) Deploys Interview Simulator backend via Railway
# 6) Deploys Interview Simulator frontend via Cloudflare Pages (wrangler)
# 7) Runs a simple smoke test (curl health endpoints)
#
# NOTE: This script performs real deploys. Use with care.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IS_DIR="$ROOT_DIR/services/interview-simulator"
IS_BACKEND_DIR="$IS_DIR/backend"
IS_FRONTEND_DIR="$IS_DIR/frontend"

API_BASE_URL="${IS_API_URL:-https://api.codeswiftr.com}"
RAILWAY_HEALTH_URL="${IS_RAILWAY_HEALTH_URL:-}"

# Cloudflare Pages project name used in research/runbooks
CF_PAGES_PROJECT="${IS_CF_PAGES_PROJECT:-interview-simulator}"
CF_PAGES_BRANCH="${IS_CF_PAGES_BRANCH:-main}"

# Colors
NC='\033[0m'
BOLD='\033[1m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'

log() { echo -e "${CYAN}[info]${NC} $*"; }
ok() { echo -e "${GREEN}[ok]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
die() { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

section() {
  echo ""
  echo -e "${BOLD}== $* ==${NC}"
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

check_auth() {
  section "Auth checks"

  need_cmd railway
  need_cmd wrangler
  need_cmd curl

  log "Checking Railway authentication..."
  if railway whoami >/dev/null 2>&1; then
    ok "Railway authenticated"
  else
    die "Railway not authenticated. Run: railway login"
  fi

  log "Checking Wrangler authentication..."
  if wrangler whoami >/dev/null 2>&1; then
    ok "Wrangler authenticated"
  else
    die "Wrangler not authenticated. Run: wrangler login"
  fi
}

run_tests_quick() {
  section "Backend tests (quick)"
  need_cmd make

  log "Running backend tests via make test..."
  (cd "$IS_DIR" && make test)
  ok "Backend tests passed"
}

build_frontend() {
  section "Frontend build"
  need_cmd npm

  log "Installing frontend deps (if needed)..."
  if [[ ! -d "$IS_FRONTEND_DIR/node_modules" ]]; then
    (cd "$IS_FRONTEND_DIR" && npm install)
  else
    log "node_modules exists; skipping npm install"
  fi

  log "Building frontend (npm run build)..."
  (cd "$IS_DIR" && make build)

  [[ -d "$IS_FRONTEND_DIR/dist" ]] || die "Build output missing: $IS_FRONTEND_DIR/dist"
  ok "Frontend build complete"
}

deploy_backend_railway() {
  section "Deploy backend (Railway)"
  log "Deploying from: $IS_BACKEND_DIR"
  (cd "$IS_BACKEND_DIR" && railway up)
  ok "Railway deploy command completed"
}

deploy_frontend_pages() {
  section "Deploy frontend (Cloudflare Pages)"
  log "Deploying $IS_FRONTEND_DIR/dist to Pages project '$CF_PAGES_PROJECT' (branch: $CF_PAGES_BRANCH)"
  (cd "$IS_FRONTEND_DIR" && wrangler pages deploy dist --project-name="$CF_PAGES_PROJECT" --branch="$CF_PAGES_BRANCH")
  ok "Cloudflare Pages deploy completed"
}

smoke_test() {
  section "Smoke test (curl health endpoint)"

  local base="$API_BASE_URL"
  if [[ -n "$RAILWAY_HEALTH_URL" ]]; then
    warn "Using IS_RAILWAY_HEALTH_URL override for smoke test: $RAILWAY_HEALTH_URL"
    base="$RAILWAY_HEALTH_URL"
  fi

  log "Health: $base/health"
  curl -sf "$base/health" >/dev/null && ok "GET /health OK" || die "GET /health failed"

  log "Ready:  $base/health/ready"
  curl -sf "$base/health/ready" >/dev/null && ok "GET /health/ready OK" || die "GET /health/ready failed"
}

main() {
  echo -e "${BOLD}FORGE — deploy-all (Interview Simulator)${NC}"
  echo "Repo: $ROOT_DIR"
  echo "API base: $API_BASE_URL"
  echo "Pages project: $CF_PAGES_PROJECT (branch: $CF_PAGES_BRANCH)"

  check_auth
  run_tests_quick
  build_frontend

  if ! confirm "Proceed with deploy to Railway + Cloudflare Pages?"; then
    warn "Cancelled. No deploy executed."
    exit 0
  fi

  deploy_backend_railway
  deploy_frontend_pages
  smoke_test

  echo ""
  ok "All done."
}

main "$@"

