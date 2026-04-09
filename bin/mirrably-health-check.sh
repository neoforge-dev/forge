#!/bin/bash

set -e

# ANSI colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

# Services: "name|url"
API_URL="https://mirrably-api-production.up.railway.app/v1/health"
SERVICES=(
  "API|${API_URL}"
  "Marketing|https://mirrably.com"
  "Demo|https://demo.mirrably.com"
  "Admin|https://admin.mirrably.com"
  "Eyewear|https://mirrably.com/eyewear"
  "Jewelry|https://mirrably.com/jewelry"
  "Makeup|https://mirrably.com/makeup"
  "CDN|https://cdn.mirrably.com/forge-tryon.esm.js"
)

TIMEOUT=10
MAX_RESPONSE_TIME=3.0
MIN_TLS_DAYS=7

results_file=".forge/heartbeat/results/mirrably-health-check.md"

# Header
echo -e "\n+----------------+----------+----------------+----------------+"
echo -e "| Service        | Status   | Response Time  | TLS Days Left  |"
echo -e "+----------------+----------+----------------+----------------+"

overall_status=0

for service in "${SERVICES[@]}"; do
  name="${service%%|*}"
  url="${service#*|}"

  # Extract hostname for TLS check
  hostname=$(echo "$url" | sed -E 's|https?://([^/]+).*|\1|')

  # Check HTTP status + timing
  curl_output=$(curl -sS -o /dev/null -w "%{http_code}|%{time_total}" --connect-timeout "$TIMEOUT" "$url" 2>/dev/null) || true
  http_code="${curl_output%%|*}"
  response_time="${curl_output#*|}"

  # Default values
  status_color=$GREEN
  tls_days="-"
  tls_color=$GREEN

  if [ -n "$curl_output" ]; then
    status="HTTP $http_code"
    # HTTP status check
    if [ "$http_code" != "200" ]; then
      status_color=$RED
      overall_status=1
    fi
    # Response time threshold
    if echo "$response_time" | awk -v max="$MAX_RESPONSE_TIME" '{exit !($1 >= max)}'; then
      status_color=$RED
      overall_status=1
    fi
  else
    status="FAIL"
    status_color=$RED
    overall_status=1
  fi

  # TLS certificate check
  if [ -n "$hostname" ]; then
    tls_info=$(echo | openssl s_client -servername "$hostname" -connect "${hostname}:443" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null) || true
    if [ -n "$tls_info" ]; then
      exp_date=$(echo "$tls_info" | sed 's/notAfter=//')
      if [ -n "$exp_date" ]; then
        # macOS date parsing
        exp_epoch=$(date -j -f "%b %d %T %Y %Z" "$exp_date" +%s 2>/dev/null || date -d "$exp_date" +%s 2>/dev/null || true)
        if [ -n "$exp_epoch" ]; then
          now_epoch=$(date +%s)
          tls_days=$(( (exp_epoch - now_epoch) / 86400 ))
          if [ "$tls_days" -lt "$MIN_TLS_DAYS" ]; then
            tls_color=$RED
            overall_status=1
          fi
        fi
      fi
    else
      tls_days="ERR"
      tls_color=$RED
    fi
  fi

  printf "| %-14s | ${status_color}%-8s${NC} | %-14s | ${tls_color}%-14s${NC} |\n" \
    "$name" "$status" "$(printf "%.3fs" "$response_time" 2>/dev/null || echo "$response_time")" \
    "$(if [[ "$tls_days" =~ ^[0-9]+$ ]]; then echo "${tls_days}d"; else echo "$tls_days"; fi)"
done

echo -e "+----------------+----------+----------------+----------------+\n"

# Write results summary
mkdir -p "$(dirname "$results_file")"
if [ $overall_status -eq 0 ]; then
  echo -e "${GREEN}All services healthy.${NC}"
  {
    echo "# Mirrably Health Check Results"
    echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo ""
    echo "## Summary"
    echo "- Overall Status: **PASS**"
    echo "- Services checked: ${#SERVICES[@]}"
    echo ""
    echo "All services responding HTTP 200, response times < 3s, TLS valid > 7 days."
  } > "$results_file"
  exit 0
else
  echo -e "${RED}One or more health checks failed.${NC}"
  {
    echo "# Mirrably Health Check Results"
    echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo ""
    echo "## Summary"
    echo "- Overall Status: **FAIL**"
    echo "- Services checked: ${#SERVICES[@]}"
    echo ""
    echo "See script output for detected failures."
  } > "$results_file"
  exit 1
fi
