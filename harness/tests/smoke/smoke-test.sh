#!/bin/bash
#
# FORGE FastAPI Smoke Test Script
# Tests common health endpoints and optional project-specific endpoints
#
# Usage:
#   ./smoke-test.sh <base_url> [options]
#   ./smoke-test.sh https://voice-coach-api-production.up.railway.app
#   ./smoke-test.sh http://localhost:8000 --verbose --timeout 5
#   ./smoke-test.sh https://api.example.com --config ./custom-tests.json
#
# Exit Codes:
#   0 - All tests passed
#   1 - One or more tests failed
#   2 - Invalid usage or configuration error
#

set -e

# Default configuration
TIMEOUT=10
VERBOSE=false
CONFIG_FILE=""
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
# Check for help first
if [ $# -eq 0 ] || [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        # Help was requested
        :
    else
        # No arguments
        echo "Error: Base URL is required"
        echo ""
    fi
    echo "FORGE FastAPI Smoke Test Script"
    echo ""
    echo "Usage: $0 <base_url> [options]"
    echo ""
    echo "Arguments:"
    echo "  base_url            Base URL of the API (e.g., https://api.example.com)"
    echo ""
    echo "Options:"
    echo "  --timeout SECONDS   Request timeout in seconds (default: 10)"
    echo "  --verbose           Show full response bodies"
    echo "  --config FILE       Path to custom test config JSON"
    echo "  --help, -h          Show this help message"
    echo ""
    echo "Config File Format (JSON):"
    echo '  {"tests": ['
    echo '    {"method": "GET", "path": "/api/v1/endpoint", "expect_status": 200},'
    echo '    {"method": "POST", "path": "/api/v1/create", "expect_status": 401}'
    echo '  ]}'
    echo ""
    exit 0
fi

BASE_URL="$1"
shift

while [[ $# -gt 0 ]]; do
    case $1 in
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --verbose)
            VERBOSE=true
            shift
            ;;
        --config)
            CONFIG_FILE="$2"
            shift 2
            ;;
        *)
            echo "Error: Unknown option: $1"
            echo "Run '$0 --help' for usage information"
            exit 2
            ;;
    esac
done

# Strip trailing slash from base URL
BASE_URL="${BASE_URL%/}"

# Function to format duration
format_duration() {
    local ms=$1
    echo "${ms}ms"
}

# Function to run a single test
run_test() {
    local method="$1"
    local path="$2"
    local expect_status="${3:-200}"
    local expect_body="$4"

    TOTAL_TESTS=$((TOTAL_TESTS + 1))

    local url="${BASE_URL}${path}"
    local start_time=$(date +%s%3N)

    # Run curl with timeout and capture response
    local response_file=$(mktemp)
    local http_code

    if [ "$method" = "GET" ]; then
        http_code=$(curl -s -w "%{http_code}" -o "$response_file" \
            --max-time "$TIMEOUT" \
            -X GET "$url" 2>/dev/null || echo "000")
    elif [ "$method" = "POST" ]; then
        http_code=$(curl -s -w "%{http_code}" -o "$response_file" \
            --max-time "$TIMEOUT" \
            -X POST "$url" \
            -H "Content-Type: application/json" \
            2>/dev/null || echo "000")
    else
        http_code=$(curl -s -w "%{http_code}" -o "$response_file" \
            --max-time "$TIMEOUT" \
            -X "$method" "$url" 2>/dev/null || echo "000")
    fi

    local end_time=$(date +%s%3N)
    local duration=$((end_time - start_time))

    local response_body=$(cat "$response_file")
    rm -f "$response_file"

    # Check status code
    local status_ok=false
    if [ "$http_code" = "$expect_status" ]; then
        status_ok=true
    fi

    # Check body if provided
    local body_ok=true
    if [ -n "$expect_body" ] && [ "$status_ok" = true ]; then
        if ! echo "$response_body" | grep -q "$expect_body"; then
            body_ok=false
        fi
    fi

    # Format output
    local status_display
    if [ "$status_ok" = true ] && [ "$body_ok" = true ]; then
        if [ "$http_code" = "000" ]; then
            status_display="${RED}✗${NC} $method $path"
            status_display="$status_display   ${RED}TIMEOUT${NC} (expected $expect_status)"
            FAILED_TESTS=$((FAILED_TESTS + 1))
        else
            status_display="${GREEN}✓${NC} $method $path"
            status_display="$status_display   ${GREEN}$http_code${NC} ($(format_duration $duration))"
            PASSED_TESTS=$((PASSED_TESTS + 1))
        fi
    else
        status_display="${RED}✗${NC} $method $path"
        if [ "$http_code" = "000" ]; then
            status_display="$status_display   ${RED}TIMEOUT${NC} (expected $expect_status)"
        elif [ "$status_ok" = false ]; then
            status_display="$status_display   ${RED}$http_code${NC} (expected $expect_status)"
        else
            status_display="$status_display   ${RED}$http_code${NC} (body check failed)"
        fi
        FAILED_TESTS=$((FAILED_TESTS + 1))
    fi

    printf "%b\n" "$status_display"

    # Show response body in verbose mode
    if [ "$VERBOSE" = true ] && [ -n "$response_body" ]; then
        echo "  Response: $response_body"
    fi
}

# Print header
echo ""
echo "Smoke Test: $BASE_URL"
echo "================================================"

# Standard health check endpoints
run_test "GET" "/api/health" "200" '"status"'
run_test "GET" "/api/health/live" "200"
run_test "GET" "/api/health/ready" "200"
run_test "GET" "/docs" "200"

# Load and run custom tests if config provided
if [ -n "$CONFIG_FILE" ]; then
    if [ ! -f "$CONFIG_FILE" ]; then
        echo ""
        echo "${YELLOW}Warning: Config file not found: $CONFIG_FILE${NC}"
    else
        # Check if jq is available
        if command -v jq >/dev/null 2>&1; then
            # Parse JSON config with jq
            test_count=$(jq -r '.tests | length' "$CONFIG_FILE" 2>/dev/null || echo "0")

            if [ "$test_count" -gt 0 ]; then
                for i in $(seq 0 $((test_count - 1))); do
                    method=$(jq -r ".tests[$i].method" "$CONFIG_FILE")
                    path=$(jq -r ".tests[$i].path" "$CONFIG_FILE")
                    expect_status=$(jq -r ".tests[$i].expect_status // 200" "$CONFIG_FILE")
                    expect_body=$(jq -r ".tests[$i].expect_body // \"\"" "$CONFIG_FILE")

                    run_test "$method" "$path" "$expect_status" "$expect_body"
                done
            fi
        else
            echo ""
            echo "${YELLOW}Warning: jq not installed. Skipping custom tests.${NC}"
            echo "Install jq to use custom test configs: brew install jq"
        fi
    fi
fi

# Auto-detect project-specific config
AUTO_CONFIG=""
if [ -z "$CONFIG_FILE" ]; then
    # Try to find smoke-test.config.json in common locations
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    FORGE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

    # Search pattern: any backend directory with smoke-test.config.json
    while IFS= read -r -d '' config_path; do
        AUTO_CONFIG="$config_path"
        break  # Use first match
    done < <(find "$FORGE_ROOT" -type f -name "smoke-test.config.json" -print0 2>/dev/null)

    if [ -n "$AUTO_CONFIG" ]; then
        echo ""
        echo "${BLUE}Found auto-config: $AUTO_CONFIG${NC}"

        if command -v jq >/dev/null 2>&1; then
            test_count=$(jq -r '.tests | length' "$AUTO_CONFIG" 2>/dev/null || echo "0")

            if [ "$test_count" -gt 0 ]; then
                for i in $(seq 0 $((test_count - 1))); do
                    method=$(jq -r ".tests[$i].method" "$AUTO_CONFIG")
                    path=$(jq -r ".tests[$i].path" "$AUTO_CONFIG")
                    expect_status=$(jq -r ".tests[$i].expect_status // 200" "$AUTO_CONFIG")
                    expect_body=$(jq -r ".tests[$i].expect_body // \"\"" "$AUTO_CONFIG")

                    run_test "$method" "$path" "$expect_status" "$expect_body"
                done
            fi
        fi
    fi
fi

# Print summary
echo "================================================"

if [ $FAILED_TESTS -eq 0 ]; then
    printf "%b\n" "${GREEN}Result: $PASSED_TESTS/$TOTAL_TESTS passed${NC}"
    echo ""
    exit 0
else
    printf "%b\n" "${RED}Result: $PASSED_TESTS/$TOTAL_TESTS passed, $FAILED_TESTS failed${NC}"
    echo ""
    exit 1
fi
