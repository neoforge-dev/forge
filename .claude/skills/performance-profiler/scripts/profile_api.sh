#!/usr/bin/env bash
#
# API Latency Profiling
# Measures endpoint performance with various load testing tools
#

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Defaults
ENDPOINT=""
METHOD="GET"
REQUESTS=100
CONCURRENCY=10
TIMEOUT=30
HEADERS=()
DATA=""
BASE_URL="${BASE_URL:-http://localhost:8000}"
OUTPUT_FORMAT="text"
OUTPUT_FILE=""
BASELINE=""
FAIL_P99=""

usage() {
    cat << EOF
Usage: profile_api.sh [options]

Options:
  --endpoint PATH    API endpoint path (e.g., /api/v1/users)
  --method METHOD    HTTP method (default: GET)
  --requests N       Total number of requests (default: 100)
  --concurrency N    Concurrent connections (default: 10)
  --timeout SECS     Request timeout (default: 30)
  --header "K: V"    Add header (can be used multiple times)
  --data JSON        Request body data
  --base-url URL     Base URL (default: http://localhost:8000)
  --format FORMAT    Output: text, json (default: text)
  --output FILE      Save results to file
  --baseline FILE    Compare against baseline
  --fail-if-p99-above MS  Fail if P99 latency exceeds threshold
  --help             Show this help

Examples:
  profile_api.sh --endpoint /api/v1/users
  profile_api.sh --endpoint /api/v1/login --method POST --data '{"email":"test@test.com"}'
  profile_api.sh --endpoint /api/v1/users --requests 1000 --concurrency 50
EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --endpoint)
            ENDPOINT="$2"
            shift 2
            ;;
        --method)
            METHOD="$2"
            shift 2
            ;;
        --requests)
            REQUESTS="$2"
            shift 2
            ;;
        --concurrency)
            CONCURRENCY="$2"
            shift 2
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --header)
            HEADERS+=("$2")
            shift 2
            ;;
        --data)
            DATA="$2"
            shift 2
            ;;
        --base-url)
            BASE_URL="$2"
            shift 2
            ;;
        --format)
            OUTPUT_FORMAT="$2"
            shift 2
            ;;
        --output)
            OUTPUT_FILE="$2"
            shift 2
            ;;
        --baseline)
            BASELINE="$2"
            shift 2
            ;;
        --fail-if-p99-above)
            FAIL_P99="$2"
            shift 2
            ;;
        --help)
            usage
            exit 0
            ;;
        -*)
            echo -e "${RED}Unknown option: $1${NC}" >&2
            exit 1
            ;;
        *)
            echo -e "${RED}Unexpected argument: $1${NC}" >&2
            exit 1
            ;;
    esac
done

if [[ -z "$ENDPOINT" ]]; then
    echo -e "${RED}Error: --endpoint is required${NC}" >&2
    usage >&2
    exit 1
fi

# Ensure endpoint starts with /
[[ "$ENDPOINT" != /* ]] && ENDPOINT="/$ENDPOINT"

URL="${BASE_URL}${ENDPOINT}"

echo -e "${BLUE}=== API Latency Profiler ===${NC}"
echo "Endpoint: $METHOD $URL"
echo "Requests: $REQUESTS (Concurrency: $CONCURRENCY)"
echo ""

# Build header args
HEADER_ARGS=""
for header in "${HEADERS[@]}"; do
    HEADER_ARGS="$HEADER_ARGS -H '$header'"
done

# Detect available tool
detect_tool() {
    if command -v wrk &> /dev/null; then
        echo "wrk"
    elif command -v hey &> /dev/null; then
        echo "hey"
    elif command -v ab &> /dev/null; then
        echo "ab"
    else
        echo "curl"
    fi
}

TOOL=$(detect_tool)
echo -e "${YELLOW}Using tool: $TOOL${NC}"

# Run with wrk
run_wrk() {
    local duration=$((REQUESTS / CONCURRENCY))
    [[ $duration -lt 5 ]] && duration=5
    
    local header_str=""
    for header in "${HEADERS[@]}"; do
        header_str="$header_str -H \"$header\""
    done
    
    local script=$(mktemp)
    cat > "$script" << 'WRKSCRIPT'
wrk.format = function(method, path, headers, body)
    return wrk.format(method, path, headers, body)
end

local latencies = {}
local counter = 0

response = function(status, headers, body)
    counter = counter + 1
    latencies[counter] = wrk.thread:get("latency") / 1000  -- convert to ms
end

done = function(summary, latency, requests)
    io.write("\n=== Results ===\n")
    io.write(string.format("Requests: %d\n", summary.requests))
    io.write(string.format("Duration: %.2f seconds\n", summary.duration / 1000000))
    io.write(string.format("RPS: %.2f\n", summary.requests / (summary.duration / 1000000)))
    io.write(string.format("Avg Latency: %.2f ms\n", latency.mean / 1000))
    io.write(string.format("Max Latency: %.2f ms\n", latency.max / 1000))
    io.write(string.format("Stdev: %.2f ms\n", latency.stdev / 1000))
    io.write("\nPercentiles:\n")
    io.write(string.format("  50%%: %.2f ms\n", latency:percentile(50) / 1000))
    io.write(string.format("  75%%: %.2f ms\n", latency:percentile(75) / 1000))
    io.write(string.format("  90%%: %.2f ms\n", latency:percentile(90) / 1000))
    io.write(string.format("  95%%: %.2f ms\n", latency:percentile(95) / 1000))
    io.write(string.format("  99%%: %.2f ms\n", latency:percentile(99) / 1000))
    io.write(string.format("\nErrors: %d (connect: %d, read: %d, write: %d, status: %d, timeout: %d)\n",
        summary.errors.connect + summary.errors.read + summary.errors.write + summary.errors.status + summary.errors.timeout,
        summary.errors.connect, summary.errors.read, summary.errors.write, summary.errors.status, summary.errors.timeout))
end
WRKSCRIPT
    
    eval wrk -t$CONCURRENCY -c$CONCURRENCY -d${duration}s -s "$script" "$header_str" -- "$URL" 2>&1
    rm -f "$script"
}

# Run with hey
run_hey() {
    local header_args=""
    for header in "${HEADERS[@]}"; do
        header_args="$header_args -H '$header'"
    done
    
    local data_arg=""
    [[ -n "$DATA" ]] && data_arg="-d '$DATA'"
    
    eval hey -n $REQUESTS -c $CONCURRENCY -m $METHOD $header_args $data_arg -t ${TIMEOUT}s "$URL"
}

# Run with curl (sequential, for basic checks)
run_curl() {
    echo -e "${YELLOW}Note: Using curl (sequential). For load testing, install wrk or hey.${NC}"
    
    local times_file=$(mktemp)
    local errors=0
    local successes=0
    
    for i in $(seq 1 $REQUESTS); do
        local start_time=$(date +%s%N)
        
        local curl_args="-s -o /dev/null -w %{http_code}"
        [[ -n "$DATA" ]] && curl_args="$curl_args -X $METHOD -d '$DATA'"
        for header in "${HEADERS[@]}"; do
            curl_args="$curl_args -H '$header'"
        done
        
        local status
        status=$(eval curl $curl_args "$URL" 2>/dev/null || echo "000")
        
        local end_time=$(date +%s%N)
        local duration_ms=$(( (end_time - start_time) / 1000000 ))
        
        if [[ "$status" == "200" || "$status" == "201" || "$status" == "204" ]]; then
            echo "$duration_ms" >> "$times_file"
            ((successes++))
        else
            ((errors++))
        fi
    done
    
    # Calculate statistics
    if [[ $successes -gt 0 ]]; then
        sort -n "$times_file" -o "$times_file"
        
        local total=0
        local min=999999
        local max=0
        
        while read -r time; do
            total=$((total + time))
            [[ $time -lt $min ]] && min=$time
            [[ $time -gt $max ]] && max=$time
        done < "$times_file"
        
        local avg=$((total / successes))
        local p50=$(sed -n "$((successes * 50 / 100))p" "$times_file")
        local p95=$(sed -n "$((successes * 95 / 100))p" "$times_file")
        local p99=$(sed -n "$((successes * 99 / 100))p" "$times_file")
        
        echo ""
        echo "=== Results ==="
        echo "Successful: $successes / $REQUESTS"
        echo "Errors: $errors"
        echo ""
        echo "Latency Distribution:"
        echo "  Min: ${min}ms"
        echo "  Avg: ${avg}ms"
        echo "  Max: ${max}ms"
        echo "  P50: ${p50}ms"
        echo "  P95: ${p95}ms"
        echo "  P99: ${p99}ms"
        
        # Check threshold
        if [[ -n "$FAIL_P99" && "$p99" -gt "$FAIL_P99" ]]; then
            echo ""
            echo -e "${RED}FAILED: P99 latency (${p99}ms) exceeds threshold (${FAIL_P99}ms)${NC}"
            rm -f "$times_file"
            exit 1
        fi
    else
        echo -e "${RED}All requests failed!${NC}"
        rm -f "$times_file"
        exit 1
    fi
    
    rm -f "$times_file"
}

# Main execution
case "$TOOL" in
    wrk)
        run_wrk
        ;;
    hey)
        run_hey
        ;;
    curl)
        run_curl
        ;;
    *)
        echo -e "${RED}No suitable tool found${NC}" >&2
        exit 1
        ;;
esac

echo ""
echo -e "${BLUE}=== API Profiling Complete ===${NC}"
