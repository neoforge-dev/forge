#!/usr/bin/env bash
#
# API Integration Test Script
# Usage: test-api.sh [options]
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${TEST_API_BASE_URL:-http://localhost:8000}"
TIMEOUT="${TIMEOUT:-30}"
VERBOSE="${VERBOSE:-false}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Test state
FAILED_TESTS=0

log() {
    if [ "$VERBOSE" = "true" ]; then
        echo "$@"
    fi
}

usage() {
    cat <<EOF
API Integration Test

Usage: test-api.sh [options]

Options:
  --endpoint PATH       API endpoint path (required)
  --method METHOD       HTTP method (default: GET)
  --auth TYPE          Authentication type (bearer|basic|api-key)
  --token TOKEN        Bearer token
  --user USER          Basic auth username
  --pass PASSWORD      Basic auth password
  --key KEY            API key
  --header KEY=VALUE   Custom header (can be repeated)
  --body DATA          Request body (JSON)
  --expect-status N    Expected status code (default: 200)
  --expect-field PATH  Verify JSON response has field
  --expect-value VAL   Verify field equals value
  --max-latency MS     Max acceptable latency in milliseconds
  --rate-limit-test    Test rate limiting
  --requests N         Number of requests for rate limit test
  --help               Show this help

Examples:
  test-api.sh --endpoint /api/v1/health
  test-api.sh --endpoint /api/v1/users --auth bearer --token \$TOKEN
  test-api.sh --endpoint /api/v1/users --method POST --body '{"name":"test"}'

EOF
}

# Parse arguments
parse_args() {
    ENDPOINT=""
    METHOD="GET"
    AUTH_TYPE=""
    TOKEN=""
    USER=""
    PASS=""
    API_KEY=""
    HEADERS=()
    BODY=""
    EXPECT_STATUS=200
    EXPECT_FIELD=""
    EXPECT_VALUE=""
    MAX_LATENCY=""
    RATE_LIMIT_TEST=false
    REQUESTS=100
    
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
            --auth)
                AUTH_TYPE="$2"
                shift 2
                ;;
            --token)
                TOKEN="$2"
                shift 2
                ;;
            --user)
                USER="$2"
                shift 2
                ;;
            --pass)
                PASS="$2"
                shift 2
                ;;
            --key)
                API_KEY="$2"
                shift 2
                ;;
            --header)
                HEADERS+=("$2")
                shift 2
                ;;
            --body)
                BODY="$2"
                shift 2
                ;;
            --expect-status)
                EXPECT_STATUS="$2"
                shift 2
                ;;
            --expect-field)
                EXPECT_FIELD="$2"
                shift 2
                ;;
            --expect-value)
                EXPECT_VALUE="$2"
                shift 2
                ;;
            --max-latency)
                MAX_LATENCY="$2"
                shift 2
                ;;
            --rate-limit-test)
                RATE_LIMIT_TEST=true
                shift
                ;;
            --requests)
                REQUESTS="$2"
                shift 2
                ;;
            --verbose)
                VERBOSE="true"
                shift
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                echo "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done
    
    if [ -z "$ENDPOINT" ]; then
        echo "Error: --endpoint is required"
        usage
        exit 1
    fi
}

# Build curl command
build_curl_cmd() {
    local cmd="curl -s -w \"\\nHTTP_CODE:%{http_code}\\nTIME:%{time_total}\\n\""
    
    # Add timeout
    cmd="$cmd --max-time $TIMEOUT"
    
    # Add method
    cmd="$cmd -X $METHOD"
    
    # Add authentication
    case "$AUTH_TYPE" in
        bearer)
            if [ -n "$TOKEN" ]; then
                cmd="$cmd -H \"Authorization: Bearer $TOKEN\""
            fi
            ;;
        basic)
            if [ -n "$USER" ] && [ -n "$PASS" ]; then
                cmd="$cmd -u \"$USER:$PASS\""
            fi
            ;;
        api-key)
            if [ -n "$API_KEY" ]; then
                cmd="$cmd -H \"X-API-Key: $API_KEY\""
            fi
            ;;
    esac
    
    # Add custom headers
    for header in "${HEADERS[@]}"; do
        cmd="$cmd -H \"$header\""
    done
    
    # Add content-type for body
    if [ -n "$BODY" ]; then
        cmd="$cmd -H \"Content-Type: application/json\""
        cmd="$cmd -d '$BODY'"
    fi
    
    # Add URL
    cmd="$cmd \"${BASE_URL}${ENDPOINT}\""
    
    echo "$cmd"
}

# Run single test
run_test() {
    local name="$1"
    local cmd
    cmd=$(build_curl_cmd)
    
    log "Running: $name"
    log "Command: $cmd"
    
    local response
    local http_code
    local time_total
    
    # Execute request
    response=$(eval "$cmd" 2>&1) || {
        echo -e "${RED}❌ $name${NC}"
        echo "   Error: Request failed - connection refused or timeout"
        ((FAILED_TESTS++))
        return 1
    }
    
    # Parse response
    http_code=$(echo "$response" | grep "HTTP_CODE:" | cut -d: -f2)
    time_total=$(echo "$response" | grep "TIME:" | cut -d: -f2)
    local time_ms
    time_ms=$(echo "$time_total * 1000" | bc | cut -d. -f1)
    local body
    body=$(echo "$response" | sed '/HTTP_CODE:/d' | sed '/TIME:/d')
    
    # Check status code
    if [ "$http_code" != "$EXPECT_STATUS" ]; then
        echo -e "${RED}❌ $name${NC}"
        echo "   Expected status: $EXPECT_STATUS"
        echo "   Actual status: $http_code"
        echo "   Response: $body"
        ((FAILED_TESTS++))
        return 1
    fi
    
    # Check max latency
    if [ -n "$MAX_LATENCY" ] && [ "$time_ms" -gt "$MAX_LATENCY" ]; then
        echo -e "${YELLOW}⚠️  $name${NC}"
        echo "   Status: $http_code"
        echo "   Latency: ${time_ms}ms (exceeds ${MAX_LATENCY}ms)"
        ((FAILED_TESTS++))
        return 1
    fi
    
    # Check expected field
    if [ -n "$EXPECT_FIELD" ]; then
        if ! echo "$body" | jq -e "$EXPECT_FIELD" > /dev/null 2>&1; then
            echo -e "${RED}❌ $name${NC}"
            echo "   Error: Expected field '$EXPECT_FIELD' not found"
            echo "   Response: $body"
            ((FAILED_TESTS++))
            return 1
        fi
        
        # Check expected value
        if [ -n "$EXPECT_VALUE" ]; then
            local actual_value
            actual_value=$(echo "$body" | jq -r "$EXPECT_FIELD")
            if [ "$actual_value" != "$EXPECT_VALUE" ]; then
                echo -e "${RED}❌ $name${NC}"
                echo "   Error: Field '$EXPECT_FIELD' = '$actual_value', expected '$EXPECT_VALUE'"
                ((FAILED_TESTS++))
                return 1
            fi
        fi
    fi
    
    echo -e "${GREEN}✅ $name${NC}"
    log "   Status: $http_code, Latency: ${time_ms}ms"
    return 0
}

# Rate limit test
run_rate_limit_test() {
    local name="Rate Limit Test ($REQUESTS requests)"
    local success_count=0
    local rate_limited_count=0
    local error_count=0
    
    echo "Running rate limit test: $REQUESTS requests..."
    
    for i in $(seq 1 $REQUESTS); do
        local response
        local http_code
        
        response=$(curl -s -w "\nHTTP_CODE:%{http_code}" \
            --max-time "$TIMEOUT" \
            "${BASE_URL}${ENDPOINT}" 2>&1)
        
        http_code=$(echo "$response" | grep "HTTP_CODE:" | cut -d: -f2)
        
        case "$http_code" in
            200)
                ((success_count++))
                ;;
            429)
                ((rate_limited_count++))
                ;;
            *)
                ((error_count++))
                ;;
        esac
        
        # Show progress
        if [ $((i % 10)) -eq 0 ]; then
            echo "  Progress: $i/$REQUESTS (OK: $success_count, Limited: $rate_limited_count, Error: $error_count)"
        fi
    done
    
    echo ""
    echo "Rate Limit Test Results:"
    echo "  Successful: $success_count"
    echo "  Rate Limited (429): $rate_limited_count"
    echo "  Errors: $error_count"
    
    # Test passes if we got some successful requests and some rate limited
    if [ "$success_count" -gt 0 ] && [ "$rate_limited_count" -gt 0 ]; then
        echo -e "${GREEN}✅ Rate limiting is working correctly${NC}"
        return 0
    elif [ "$success_count" -eq 0 ]; then
        echo -e "${RED}❌ All requests failed${NC}"
        ((FAILED_TESTS++))
        return 1
    else
        echo -e "${YELLOW}⚠️  No rate limiting detected${NC}"
        return 0
    fi
}

# Main
main() {
    parse_args "$@"
    
    echo "API Integration Test"
    echo "===================="
    echo "Base URL: $BASE_URL"
    echo "Endpoint: $ENDPOINT"
    echo "Method: $METHOD"
    echo ""
    
    if [ "$RATE_LIMIT_TEST" = true ]; then
        run_rate_limit_test
    else
        local test_name="$METHOD $ENDPOINT"
        if [ -n "$EXPECT_STATUS" ]; then
            test_name="$test_name (expect $EXPECT_STATUS)"
        fi
        run_test "$test_name"
    fi
    
    if [ $FAILED_TESTS -gt 0 ]; then
        exit 1
    fi
}

main "$@"
