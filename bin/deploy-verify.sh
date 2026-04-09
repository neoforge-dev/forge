#!/usr/bin/env bash
# deploy-verify.sh — Post-deploy smoke test for FORGE landing pages
# Compatible with bash 3.2+ (macOS default)
# Usage: ./bin/deploy-verify.sh [domain|all]
# Exit code: 0 if all pass, 1 if any fail

set -euo pipefail

DOMAINS="codeswiftr.com brandfocus.ai calmconnect.io babybit.es adguild.io neoforge.dev leanvibe.ai leanvibe.dev mumchef.io bogdanveliscu.com"

get_url() {
  case "$1" in
    codeswiftr.com) echo "https://codeswiftr.com" ;;
    brandfocus.ai) echo "https://brandfocus.ai" ;;
    calmconnect.io) echo "https://calmconnect.io" ;;
    babybit.es) echo "https://babybit.es" ;;
    adguild.io) echo "https://adguild.io" ;;
    neoforge.dev) echo "https://neoforge.dev" ;;
    leanvibe.ai) echo "https://leanvibe.ai" ;;
    leanvibe.dev) echo "https://leanvibe.dev" ;;
    mumchef.io) echo "https://mumchef.io" ;;
    bogdanveliscu.com) echo "https://bogdanveliscu.com" ;;
    *) echo "" ;;
  esac
}

get_expected_title() {
  case "$1" in
    codeswiftr.com) echo "Interview Simulator" ;;
    brandfocus.ai) echo "Voice Coach" ;;
    calmconnect.io) echo "CalmConnect" ;;
    babybit.es) echo "BabyBit" ;;
    adguild.io) echo "AdGuild" ;;
    neoforge.dev) echo "NeoForge" ;;
    leanvibe.ai) echo "LeanVibe" ;;
    leanvibe.dev) echo "LeanVibe" ;;
    mumchef.io) echo "MumChef" ;;
    bogdanveliscu.com) echo "Bogdan" ;;
    *) echo "" ;;
  esac
}

PLACEHOLDER_TITLE="Marketing Landing"
TOTAL=0
PASSED=0
FAILED=0

log_pass() { echo "  PASS: $1"; PASSED=$((PASSED + 1)); }
log_fail() { echo "  FAIL: $1"; FAILED=$((FAILED + 1)); }
log_info() { echo "  INFO: $1"; }

smoke_one() {
  domain="$1"
  url=$(get_url "$domain")
  expected_title=$(get_expected_title "$domain")

  TOTAL=$((TOTAL + 1))

  echo ""
  echo "[$domain]"
  echo "  URL: $url"

  # 1. HTTP 200 check
  http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$url" 2>/dev/null || echo "000")
  if [[ "$http_code" == "200" ]]; then
    log_pass "HTTP 200 OK"
  else
    log_fail "HTTP $http_code (expected 200)"
    return 1
  fi

  # 2. Fetch page content
  html=$(curl -s --max-time 15 "$url" 2>/dev/null)

  # 3. Title not placeholder
  title=$(echo "$html" | grep -o '<title>[^<]*</title>' | head -1 | sed 's/<[^>]*>//g')
  if [[ "$title" == "$PLACEHOLDER_TITLE" ]] || [[ -z "$title" ]]; then
    log_fail "Placeholder title detected: '$title'"
    return 1
  else
    log_pass "Title: '$title'"
  fi

  # 4. Title matches expected domain title
  title_ok=$(echo "$title" | grep -c "$expected_title" 2>/dev/null | head -1 || echo "0")
  title_ok=${title_ok%%[^0-9]*}
  if [[ -n "$title_ok" && "$title_ok" -gt 0 ]]; then
    log_pass "Title matches expected: contains '$expected_title'"
  else
    log_fail "Title mismatch: expected '$expected_title' in '$title'"
  fi

  # 5. og:title meta tag present
  og_title=$(echo "$html" | grep -i 'property="og:title"' | grep -o 'content="[^"]*"' | head -1 | sed 's/content="//;s/"$//')
  if [[ -n "$og_title" ]]; then
    log_pass "og:title present: '$og_title'"
  else
    log_fail "og:title meta tag missing"
  fi

  # 6. name="description" meta tag present
  desc=$(echo "$html" | grep -i 'name="description"' | grep -o 'content="[^"]*"' | head -1 | sed 's/content="//;s/"$//')
  if [[ -n "$desc" ]]; then
    log_pass "description meta tag present"
  else
    log_fail "description meta tag missing"
  fi

  # 7. Lead capture form exists
  has_email_input=$(echo "$html" | grep -ci '<input[^>]*type="email"' 2>/dev/null | head -1 || echo "0")
  has_email_input=${has_email_input%%[^0-9]*}
  has_form=$(echo "$html" | grep -ci '<form[^>]*>' 2>/dev/null | head -1 || echo "0")
  has_form=${has_form%%[^0-9]*}
  if [[ "$has_email_input" -gt 0 ]] || [[ "$has_form" -gt 0 ]]; then
    log_pass "Lead capture form present (email inputs: $has_email_input, forms: $has_form)"
  else
    log_info "No lead capture form detected (email inputs: $has_email_input, forms: $has_form)"
  fi

  return 0
}

smoke_all() {
  echo "========================================"
  echo "FORGE Deploy Smoke Test — ALL DOMAINS"
  echo "========================================"
  echo "Started: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  echo ""

  failed_domains=""
  for domain in $DOMAINS; do
    if ! smoke_one "$domain"; then
      failed_domains="$failed_domains $domain"
    fi
  done

  echo ""
  echo "========================================"
  echo "SUMMARY"
  echo "========================================"
  echo "Total domains tested: $TOTAL"
  echo "Passed: $PASSED"
  echo "Failed: $FAILED"
  echo ""

  if [[ $FAILED -eq 0 ]]; then
    echo "ALL SMOKE TESTS PASSED"
    exit 0
  else
    echo "SMOKE TESTS FAILED for:$failed_domains"
    exit 1
  fi
}

smoke_single() {
  domain="$1"
  echo "========================================"
  echo "FORGE Deploy Smoke Test — $domain"
  echo "========================================"
  smoke_one "$domain"
  exit $?
}

known_domain() {
  echo "$DOMAINS" | grep -c "$1" 2>/dev/null || echo "0"
}

main() {
  arg="${1:-all}"

  if [[ "$arg" == "all" ]]; then
    smoke_all
  else
    known=$(known_domain "$arg")
    if [[ "$known" -gt 0 ]]; then
      smoke_single "$arg"
    else
      echo "Unknown domain: $arg"
      echo "Known domains: $DOMAINS"
      echo "Or use 'all'"
      exit 1
    fi
  fi
}

main "$@"
