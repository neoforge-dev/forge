#!/usr/bin/env bash
#
# Integration Test Runner - Main orchestrator
# Usage: test-runner.sh [command] [options]
#

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
RESULTS_FILE="${RESULTS_FILE:-./integration-test-results.json}"
VERBOSE="${VERBOSE:-false}"
JSON_OUTPUT="${JSON_OUTPUT:-false}"
RETRY_COUNT="${RETRY_COUNT:-3}"
RETRY_DELAY="${RETRY_DELAY:-2}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    if [ "$VERBOSE" = "true" ]; then
        echo -e "${BLUE}[INFO]${NC} $1"
    fi
}

log_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

log_error() {
    echo -e "${RED}[FAIL]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Show usage
usage() {
    cat <<EOF
Integration Test Runner

Usage: test-runner.sh <command> [options]

Commands:
  api         Test API endpoints
  db          Test database connections
  stripe      Test Stripe integration
  openai      Test OpenAI integration
  assemblyai  Test AssemblyAI integration
  webhook     Test webhook endpoints
  queue       Test message queues
  e2e         Run end-to-end flow tests
  all         Run all integration tests
  report      Generate test report

Global Options:
  --retry N          Number of retry attempts (default: 3)
  --retry-delay N    Delay between retries in seconds (default: 2)
  --timeout N        Request timeout in seconds (default: 30)
  --verbose          Show detailed output
  --json             Output results as JSON
  --output FILE      Save results to file
  --fail-fast        Stop on first failure
  --help             Show this help message

Examples:
  test-runner.sh api --endpoint /api/v1/health
  test-runner.sh db --url postgresql://localhost/test
  test-runner.sh all --verbose --json --output results.json

EOF
}

# Parse global options
parse_global_options() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --retry)
                RETRY_COUNT="$2"
                shift 2
                ;;
            --retry-delay)
                RETRY_DELAY="$2"
                shift 2
                ;;
            --timeout)
                export TIMEOUT="$2"
                shift 2
                ;;
            --verbose)
                VERBOSE="true"
                shift
                ;;
            --json)
                JSON_OUTPUT="true"
                shift
                ;;
            --output)
                RESULTS_FILE="$2"
                shift 2
                ;;
            --fail-fast)
                export FAIL_FAST="true"
                shift
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                break
                ;;
        esac
    done
}

# Initialize results
init_results() {
    cat > "$RESULTS_FILE" <<EOF
{
  "suite": "Integration Tests",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "duration": 0,
  "summary": {
    "total": 0,
    "passed": 0,
    "failed": 0,
    "skipped": 0
  },
  "tests": []
}
EOF
}

# Add test result
add_result() {
    local name="$1"
    local status="$2"
    local duration="$3"
    local error="${4:-}"
    local suggestion="${5:-}"
    
    local test_json
    test_json=$(jq -n \
        --arg name "$name" \
        --arg status "$status" \
        --argjson duration "$duration" \
        --arg error "$error" \
        --arg suggestion "$suggestion" \
        '{
            name: $name,
            status: $status,
            duration: $duration,
            error: (if $error == "" then null else $error end),
            suggestion: (if $suggestion == "" then null else $suggestion end)
        }')
    
    # Update results file
    jq --argjson test "$test_json" '.tests += [$test]' "$RESULTS_FILE" > "${RESULTS_FILE}.tmp"
    mv "${RESULTS_FILE}.tmp" "$RESULTS_FILE"
}

# Update summary
update_summary() {
    local total=$(jq '.tests | length' "$RESULTS_FILE")
    local passed=$(jq '[.tests[] | select(.status == "passed")] | length' "$RESULTS_FILE")
    local failed=$(jq '[.tests[] | select(.status == "failed")] | length' "$RESULTS_FILE")
    local skipped=$(jq '[.tests[] | select(.status == "skipped")] | length' "$RESULTS_FILE")
    
    jq --argjson total "$total" \
       --argjson passed "$passed" \
       --argjson failed "$failed" \
       --argjson skipped "$skipped" \
       '.summary = {total: $total, passed: $passed, failed: $failed, skipped: $skipped}' \
       "$RESULTS_FILE" > "${RESULTS_FILE}.tmp"
    mv "${RESULTS_FILE}.tmp" "$RESULTS_FILE"
}

# Print results
print_results() {
    if [ "$JSON_OUTPUT" = "true" ]; then
        cat "$RESULTS_FILE"
        return
    fi
    
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║           INTEGRATION TEST RESULTS                         ║"
    echo "╠════════════════════════════════════════════════════════════╣"
    
    local total failed passed
    total=$(jq '.summary.total' "$RESULTS_FILE")
    passed=$(jq '.summary.passed' "$RESULTS_FILE")
    failed=$(jq '.summary.failed' "$RESULTS_FILE")
    
    jq -r '.tests[] | 
        if .status == "passed" then "║ ✅ \(.name)" 
        elif .status == "failed" then "║ ❌ \(.name)\n║    Error: \(.error)"
        else "║ ⏭️  \(.name)" 
        end' "$RESULTS_FILE"
    
    echo "╠════════════════════════════════════════════════════════════╣"
    printf "║ RESULTS: %d passed, %d failed, %d skipped                ║\n" "$passed" "$failed" "$(jq '.summary.skipped' "$RESULTS_FILE")"
    printf "║ Success Rate: %.1f%%                                      ║\n" "$(( passed * 100 / (total > 0 ? total : 1) ))"
    echo "╚════════════════════════════════════════════════════════════╝"
}

# Run test with retry
run_with_retry() {
    local cmd="$1"
    local name="$2"
    local attempt=1
    local exit_code=0
    local start_time end_time duration
    
    start_time=$(date +%s.%N)
    
    while [ $attempt -le "$RETRY_COUNT" ]; do
        log_info "Attempt $attempt/$RETRY_COUNT: $name"
        
        if eval "$cmd"; then
            end_time=$(date +%s.%N)
            duration=$(echo "$end_time - $start_time" | bc)
            add_result "$name" "passed" "$duration"
            log_success "$name"
            return 0
        fi
        
        exit_code=$?
        
        if [ $attempt -lt "$RETRY_COUNT" ]; then
            log_warn "Attempt $attempt failed, retrying in ${RETRY_DELAY}s..."
            sleep "$RETRY_DELAY"
        fi
        
        ((attempt++))
    done
    
    end_time=$(date +%s.%N)
    duration=$(echo "$end_time - $start_time" | bc)
    add_result "$name" "failed" "$duration" "Failed after $RETRY_COUNT attempts"
    log_error "$name"
    
    if [ "${FAIL_FAST:-false}" = "true" ]; then
        echo "Failing fast due to --fail-fast flag"
        exit 1
    fi
    
    return $exit_code
}

# Main command dispatcher
main() {
    if [ $# -eq 0 ]; then
        usage
        exit 1
    fi
    
    local command="$1"
    shift
    
    # Parse global options
    parse_global_options "$@"
    
    # Initialize results
    init_results
    
    local start_time end_time duration
    start_time=$(date +%s.%N)
    
    case "$command" in
        api)
            "$SCRIPT_DIR/test-api.sh" "$@"
            ;;
        db)
            "$SCRIPT_DIR/test-db.sh" "$@"
            ;;
        stripe)
            "$SCRIPT_DIR/test-stripe.sh" "$@"
            ;;
        openai)
            "$SCRIPT_DIR/test-openai.sh" "$@"
            ;;
        assemblyai)
            "$SCRIPT_DIR/test-assemblyai.sh" "$@"
            ;;
        webhook)
            "$SCRIPT_DIR/test-webhook.sh" "$@"
            ;;
        queue)
            "$SCRIPT_DIR/test-queue.sh" "$@"
            ;;
        e2e)
            "$SCRIPT_DIR/test-e2e.sh" "$@"
            ;;
        all)
            run_all_tests "$@"
            ;;
        report)
            "$SCRIPT_DIR/report.sh" "$@"
            exit 0
            ;;
        *)
            log_error "Unknown command: $command"
            usage
            exit 1
            ;;
    esac
    
    end_time=$(date +%s.%N)
    duration=$(echo "$end_time - $start_time" | bc)
    
    # Update duration in results
    jq --argjson duration "$duration" '.duration = $duration' "$RESULTS_FILE" > "${RESULTS_FILE}.tmp"
    mv "${RESULTS_FILE}.tmp" "$RESULTS_FILE"
    
    update_summary
    print_results
    
    # Exit with error if any tests failed
    local failed
    failed=$(jq '.summary.failed' "$RESULTS_FILE")
    if [ "$failed" -gt 0 ]; then
        exit 1
    fi
}

# Run all tests
run_all_tests() {
    log_info "Running all integration tests..."
    
    # API tests
    if [ -n "${TEST_API_BASE_URL:-}" ]; then
        log_info "Running API tests..."
        "$SCRIPT_DIR/test-api.sh" --endpoint /api/v1/health || true
    fi
    
    # DB tests
    if [ -n "${TEST_DATABASE_URL:-}" ]; then
        log_info "Running database tests..."
        "$SCRIPT_DIR/test-db.sh" --url "$TEST_DATABASE_URL" || true
    fi
    
    # Stripe tests
    if [ -n "${STRIPE_SECRET_KEY:-}" ]; then
        log_info "Running Stripe tests..."
        "$SCRIPT_DIR/test-stripe.sh" --secret-key "$STRIPE_SECRET_KEY" || true
    fi
    
    # Queue tests
    if [ -n "${REDIS_URL:-}" ]; then
        log_info "Running queue tests..."
        "$SCRIPT_DIR/test-queue.sh" --broker "$REDIS_URL" || true
    fi
}

main "$@"
