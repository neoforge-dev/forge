#!/usr/bin/env bash
#
# End-to-End Integration Test Script
# Usage: test-e2e.sh [options]
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERBOSE="${VERBOSE:-false}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

FAILED_TESTS=0
STEP_NUMBER=0

log() {
    if [ "$VERBOSE" = "true" ]; then
        echo "$@"
    fi
}

usage() {
    cat <<EOF
End-to-End Integration Test

Usage: test-e2e.sh [options]

Options:
  --flow NAME          Predefined flow name
  --file PATH          Custom flow definition file
  --env ENV            Environment (dev|staging|prod)
  --step NUMBER        Start from specific step
  --dry-run            Show steps without executing
  --help               Show this help

Predefined Flows:
  signup-to-payment    Full signup → payment flow
  content-creation     Create → process → publish
  data-import          Upload → process → analyze
  notification         Trigger → queue → send

Examples:
  test-e2e.sh --flow signup-to-payment
  test-e2e.sh --file ./flows/custom.json --env staging

EOF
}

# Parse arguments
parse_args() {
    FLOW_NAME=""
    FLOW_FILE=""
    ENV="dev"
    START_STEP=1
    DRY_RUN=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --flow)
                FLOW_NAME="$2"
                shift 2
                ;;
            --file)
                FLOW_FILE="$2"
                shift 2
                ;;
            --env)
                ENV="$2"
                shift 2
                ;;
            --step)
                START_STEP="$2"
                shift 2
                ;;
            --dry-run)
                DRY_RUN=true
                shift
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
}

# Get API base URL by environment
get_api_url() {
    local env="$1"
    case "$env" in
        dev|development)
            echo "${TEST_API_BASE_URL:-http://localhost:8000}"
            ;;
        staging)
            echo "${STAGING_API_URL:-https://staging-api.example.com}"
            ;;
        prod|production)
            echo "${PROD_API_URL:-https://api.example.com}"
            ;;
        *)
            echo "$env"
            ;;
    esac
}

# Execute HTTP request
execute_request() {
    local method="$1"
    local endpoint="$2"
    local data="${3:-}"
    local auth="${4:-}"
    
    local url="${API_BASE_URL}${endpoint}"
    local cmd="curl -s -X $method"
    
    cmd="$cmd -H \"Content-Type: application/json\""
    
    if [ -n "$auth" ]; then
        cmd="$cmd -H \"Authorization: Bearer $auth\""
    fi
    
    if [ -n "$data" ]; then
        cmd="$cmd -d '$data'"
    fi
    
    cmd="$cmd \"$url\""
    
    log "Executing: $method $endpoint"
    eval "$cmd" 2>/dev/null
}

# Extract value from JSON response
extract_value() {
    local response="$1"
    local path="$2"
    echo "$response" | jq -r "$path" 2>/dev/null
}

# Run a single step
run_step() {
    local name="$1"
    local action="$2"
    shift 2
    
    ((STEP_NUMBER++))
    
    if [ $STEP_NUMBER -lt "$START_STEP" ]; then
        log "Skipping step $STEP_NUMBER: $name"
        return 0
    fi
    
    echo ""
    echo -e "${BLUE}Step $STEP_NUMBER: $name${NC}"
    
    if [ "$DRY_RUN" = true ]; then
        echo "   Action: $action"
        echo "   (dry run - not executing)"
        return 0
    fi
    
    case "$action" in
        api)
            run_api_step "$@"
            ;;
        db)
            run_db_step "$@"
            ;;
        queue)
            run_queue_step "$@"
            ;;
        wait)
            run_wait_step "$@"
            ;;
        verify)
            run_verify_step "$@"
            ;;
        *)
            echo -e "${RED}❌ Unknown action: $action${NC}"
            ((FAILED_TESTS++))
            return 1
            ;;
    esac
}

# Run API step
run_api_step() {
    local endpoint="$1"
    local method="${2:-GET}"
    local body="${3:-}"
    local save_field="${4:-}"
    local save_var="${5:-}"
    local expect_status="${6:-200}"
    
    local response
    response=$(execute_request "$method" "$endpoint" "$body" "${AUTH_TOKEN:-}")
    
    # Check for error
    if echo "$response" | jq -e '.error' > /dev/null 2>&1; then
        local error_msg
        error_msg=$(echo "$response" | jq -r '.error.message // .error')
        echo -e "${RED}❌ API Error: $error_msg${NC}"
        ((FAILED_TESTS++))
        return 1
    fi
    
    # Save value if requested
    if [ -n "$save_field" ] && [ -n "$save_var" ]; then
        local value
        value=$(extract_value "$response" "$save_field")
        export "$save_var=$value"
        log "   Saved $save_var=$value"
    fi
    
    echo -e "${GREEN}✅ API call successful${NC}"
    return 0
}

# Run DB step
run_db_step() {
    local query="$1"
    local expect_result="${2:-}"
    
    echo "   Query: $query"
    
    # Use psql if available
    if command -v psql &> /dev/null && [ -n "${TEST_DATABASE_URL:-}" ]; then
        local result
        result=$(psql "$TEST_DATABASE_URL" -t -c "$query" 2>/dev/null | xargs)
        
        if [ -n "$expect_result" ]; then
            if [ "$result" = "$expect_result" ]; then
                echo -e "${GREEN}✅ Result matches: $result${NC}"
            else
                echo -e "${RED}❌ Result mismatch: expected '$expect_result', got '$result'${NC}"
                ((FAILED_TESTS++))
                return 1
            fi
        else
            echo -e "${GREEN}✅ Query executed${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️  Database not available${NC}"
    fi
}

# Run queue step
run_queue_step() {
    local task="$1"
    local args="${2:-[]}"
    
    echo "   Task: $task"
    echo "   Args: $args"
    
    if [ -n "${CELERY_BROKER_URL:-}" ] && command -v python3 &> /dev/null; then
        python3 << EOF
from celery import Celery
import json
import sys

app = Celery('e2e', broker='$CELERY_BROKER_URL')

try:
    # Send task
    args = json.loads('$args')
    result = app.send_task('$task', args=args)
    print(f"Task sent: {result.id}")
    sys.exit(0)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
EOF
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✅ Task queued${NC}"
        else
            echo -e "${RED}❌ Failed to queue task${NC}"
            ((FAILED_TESTS++))
            return 1
        fi
    else
        echo -e "${YELLOW}⚠️  Queue not available${NC}"
    fi
}

# Run wait step
run_wait_step() {
    local seconds="$1"
    echo "   Waiting ${seconds}s..."
    sleep "$seconds"
    echo -e "${GREEN}✅ Wait complete${NC}"
}

# Run verification step
run_verify_step() {
    local condition="$1"
    echo "   Verifying: $condition"
    
    if eval "$condition"; then
        echo -e "${GREEN}✅ Verification passed${NC}"
    else
        echo -e "${RED}❌ Verification failed${NC}"
        ((FAILED_TESTS++))
        return 1
    fi
}

# Load predefined flow
load_predefined_flow() {
    local name="$1"
    
    case "$name" in
        signup-to-payment)
            cat <<'FLOW'
step "Create User" api /api/v1/auth/signup POST {"email":"test-e2e@example.com","password":"test123"} .id USER_ID 201
step "Verify Email" wait 1
step "Login" api /api/v1/auth/login POST {"email":"test-e2e@example.com","password":"test123"} .token AUTH_TOKEN 200
step "Get User" api /api/v1/users/me GET "" "" "" 200
step "Create Payment" api /api/v1/payments/checkout POST {"amount":1000,"currency":"usd"} .client_secret PAYMENT_SECRET 200
step "Cleanup" api /api/v1/users/me DELETE "" "" "" 200
FLOW
            ;;
        content-creation)
            cat <<'FLOW'
step "Authenticate" api /api/v1/auth/login POST {"email":"test@example.com","password":"test"} .token AUTH_TOKEN 200
step "Create Content" api /api/v1/content POST {"title":"Test","body":"Content"} .id CONTENT_ID 201
step "Process Content" queue content.process ["$CONTENT_ID"]
step "Wait for Processing" wait 2
step "Verify Status" api /api/v1/content/$CONTENT_ID GET "" .status STATUS 200
step "Verify Complete" verify '[ "$STATUS" = "published" ]'
FLOW
            ;;
        *)
            echo "Unknown flow: $name"
            exit 1
            ;;
    esac
}

# Parse and execute flow
execute_flow() {
    local flow_def="$1"
    
    while IFS= read -r line; do
        # Skip empty lines and comments
        [[ -z "$line" || "$line" =~ ^# ]] && continue
        
        # Parse step definition
        eval "$line"
    done <<< "$flow_def"
}

# Main
main() {
    parse_args "$@"
    
    echo "End-to-End Integration Test"
    echo "==========================="
    echo "Environment: $ENV"
    
    # Set API base URL
    API_BASE_URL=$(get_api_url "$ENV")
    echo "API: $API_BASE_URL"
    echo ""
    
    # Load flow definition
    local flow_def=""
    if [ -n "$FLOW_NAME" ]; then
        echo "Loading flow: $FLOW_NAME"
        flow_def=$(load_predefined_flow "$FLOW_NAME")
    elif [ -n "$FLOW_FILE" ]; then
        if [ -f "$FLOW_FILE" ]; then
            echo "Loading flow from: $FLOW_FILE"
            flow_def=$(cat "$FLOW_FILE")
        else
            echo "Error: Flow file not found: $FLOW_FILE"
            exit 1
        fi
    else
        echo "Error: Either --flow or --file must be specified"
        usage
        exit 1
    fi
    
    if [ "$DRY_RUN" = true ]; then
        echo ""
        echo -e "${YELLOW}DRY RUN MODE${NC}"
    fi
    
    echo ""
    echo "Starting E2E flow..."
    
    # Execute flow
    execute_flow "$flow_def"
    
    echo ""
    echo "==========================="
    if [ $FAILED_TESTS -eq 0 ]; then
        echo -e "${GREEN}✅ All $STEP_NUMBER steps passed!${NC}"
    else
        echo -e "${RED}❌ $FAILED_TESTS step(s) failed out of $STEP_NUMBER${NC}"
        exit 1
    fi
}

# Step helper function
step() {
    run_step "$@"
}

main "$@"
