#!/usr/bin/env bash
# deploy-smoke-test.sh — Post-deploy smoke test for IS and VC backends
# Compatible with bash 3.2+ (macOS default)
#
# Usage:
#   ./bin/deploy-smoke-test.sh is
#   ./bin/deploy-smoke-test.sh vc
#   ./bin/deploy-smoke-test.sh all
#
# Options:
#   --base-url URL    Override default base URL for the selected product
#                     (only effective when testing a single product)
#
# Exit code: 0 if all checks pass, 1 if any check fails

set -uo pipefail

# --- Default URLs ---
IS_DEFAULT_URL="https://app.codeswiftr.com"
VC_DEFAULT_URL="https://app.brandfocus.ai"
# --- Globals ---
TOTAL=0
PASSED=0
FAILED=0
FAILED_CHECKS=""

# --- Colors ---
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

log_pass() {
  echo -e "  ${GREEN}PASS${NC}: $1"
  PASSED=$((PASSED + 1))
  TOTAL=$((TOTAL + 1))
}

log_fail() {
  echo -e "  ${RED}FAIL${NC}: $1"
  FAILED=$((FAILED + 1))
  TOTAL=$((TOTAL + 1))
  FAILED_CHECKS="$FAILED_CHECKS\n  - $1"
}

log_info() {
  echo -e "  ${YELLOW}INFO${NC}: $1"
}

# check_status LABEL URL EXPECTED_CODES...
# Makes a GET request and checks the HTTP status code against one or more expected codes.
# EXPECTED_CODES is a space-separated string, e.g. "200 401".
check_status() {
  local label="$1"
  local url="$2"
  local expected_raw="$3"

  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")

  # Check if actual code is in expected list
  local matched=0
  for code in $expected_raw; do
    if [[ "$http_code" == "$code" ]]; then
      matched=1
      break
    fi
  done

  if [[ "$matched" -eq 1 ]]; then
    log_pass "$label — HTTP $http_code (expected: $expected_raw)"
  else
    log_fail "$label — HTTP $http_code (expected: $expected_raw) — URL: $url"
  fi
}

# check_post LABEL URL BODY EXPECTED_CODES...
# Makes a POST request with JSON body and checks the HTTP status code.
check_post() {
  local label="$1"
  local url="$2"
  local body="$3"
  local expected_raw="$4"

  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
    -X POST "$url" \
    -H "Content-Type: application/json" \
    -d "$body" 2>/dev/null || echo "000")

  local matched=0
  for code in $expected_raw; do
    if [[ "$http_code" == "$code" ]]; then
      matched=1
      break
    fi
  done

  if [[ "$matched" -eq 1 ]]; then
    log_pass "$label — HTTP $http_code (expected: $expected_raw)"
  else
    log_fail "$label — HTTP $http_code (expected: $expected_raw) — URL: $url"
  fi
}

# --- IS: Interview Simulator (codeswiftr.com) ---
smoke_is() {
  local base_url="${1:-$IS_DEFAULT_URL}"
  local ts
  ts=$(date +%s)

  echo ""
  echo "[IS] Interview Simulator"
  echo "  Base URL: $base_url"

  check_status "GET /health" "$base_url/health" "200"
  check_status "GET /docs (FastAPI swagger)" "$base_url/docs" "200"
  check_status "GET /api/v1/interviews (auth required)" "$base_url/api/v1/interviews" "401"
  check_post   "POST /api/v1/auth/register (server responds)" \
    "$base_url/api/v1/auth/register" \
    "{\"email\":\"smoke-is-$ts@test.invalid\",\"password\":\"TestPass123!\",\"full_name\":\"Smoke Test\"}" \
    "200 422"
}

# --- VC: Voice Coach (brandfocus.ai) ---
smoke_vc() {
  local base_url="${1:-$VC_DEFAULT_URL}"

  echo ""
  echo "[VC] Voice Coach"
  echo "  Base URL: $base_url"

  check_status "GET /api/health" "$base_url/api/health" "200"
  check_status "GET /api/health/detailed" "$base_url/api/health/detailed" "200"
  check_status "GET /api/docs (FastAPI swagger)" "$base_url/api/docs" "200"
  check_status "GET /api/v1/sessions (auth required)" "$base_url/api/v1/sessions" "401"
  check_status "GET /api/v1/pricing (public endpoint)" "$base_url/api/v1/pricing" "200"
}

# --- Summary ---
print_summary() {
  echo ""
  echo "========================================"
  echo "SUMMARY"
  echo "========================================"
  echo "Total checks : $TOTAL"
  echo "Passed       : $PASSED"
  echo "Failed       : $FAILED"

  if [[ $FAILED -gt 0 ]]; then
    echo ""
    echo "Failed checks:"
    echo -e "$FAILED_CHECKS"
    echo ""
    echo "NOTE: Connection refused = backend not deployed yet (expected before deploy)."
    echo "========================================"
    echo "RESULT: FAIL"
    echo "========================================"
    return 1
  else
    echo ""
    echo "========================================"
    echo "RESULT: ALL CHECKS PASSED"
    echo "========================================"
    return 0
  fi
}

# --- Argument parsing ---
usage() {
  cat <<EOF
Usage: $0 <product> [--base-url URL]

Products:
  is    Interview Simulator (app.codeswiftr.com)
  vc    Voice Coach (app.brandfocus.ai)
  all   Both products

Options:
  --base-url URL    Override base URL (only applies to single-product runs)

Examples:
  $0 all
  $0 is
  $0 vc --base-url http://localhost:8001
EOF
  exit 1
}

main() {
  local product=""
  local base_url_override=""

  # Parse args
  while [[ $# -gt 0 ]]; do
    case "$1" in
      is|vc|all)
        product="$1"
        shift
        ;;
      --base-url)
        if [[ -z "${2:-}" ]]; then
          echo "Error: --base-url requires a value" >&2
          exit 1
        fi
        base_url_override="$2"
        shift 2
        ;;
      -h|--help)
        usage
        ;;
      *)
        echo "Error: unknown argument '$1'" >&2
        usage
        ;;
    esac
  done

  if [[ -z "$product" ]]; then
    echo "Error: product argument required (is, vc, or all)" >&2
    usage
  fi

  if [[ "$product" != "all" && -n "$base_url_override" ]]; then
    log_info "Base URL override: $base_url_override"
  fi

  if [[ -n "$base_url_override" && "$product" == "all" ]]; then
    echo "Warning: --base-url is ignored when product is 'all'" >&2
  fi

  echo "========================================"
  echo "FORGE Backend Smoke Tests"
  echo "========================================"
  echo "Started: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  echo "Product: $product"

  case "$product" in
    is)
      smoke_is "${base_url_override:-$IS_DEFAULT_URL}"
      ;;
    vc)
      smoke_vc "${base_url_override:-$VC_DEFAULT_URL}"
      ;;
    all)
      smoke_is "$IS_DEFAULT_URL"
      smoke_vc "$VC_DEFAULT_URL"
      ;;
  esac

  print_summary
}

main "$@"
