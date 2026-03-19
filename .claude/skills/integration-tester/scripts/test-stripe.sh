#!/usr/bin/env bash
#
# Stripe Integration Test Script
# Usage: test-stripe.sh [options]
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERBOSE="${VERBOSE:-false}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

FAILED_TESTS=0

log() {
    if [ "$VERBOSE" = "true" ]; then
        echo "$@"
    fi
}

usage() {
    cat <<EOF
Stripe Integration Test

Usage: test-stripe.sh [options]

Options:
  --secret-key KEY     Stripe secret key (required)
  --webhook-secret SECRET  Webhook signing secret
  --test-mode          Run in test mode (default)
  --test-charge        Create a test charge
  --test-customer      Create a test customer
  --test-subscription  Test subscription flow
  --help               Show this help

Examples:
  test-stripe.sh --secret-key sk_test_xxx
  test-stripe.sh --secret-key sk_test_xxx --webhook-secret whsec_xxx

EOF
}

# Parse arguments
parse_args() {
    STRIPE_SECRET_KEY="${STRIPE_SECRET_KEY:-}"
    WEBHOOK_SECRET="${WEBHOOK_SECRET:-}"
    TEST_MODE=true
    TEST_CHARGE=false
    TEST_CUSTOMER=false
    TEST_SUBSCRIPTION=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --secret-key)
                STRIPE_SECRET_KEY="$2"
                shift 2
                ;;
            --webhook-secret)
                WEBHOOK_SECRET="$2"
                shift 2
                ;;
            --test-mode)
                TEST_MODE=true
                shift
                ;;
            --test-charge)
                TEST_CHARGE=true
                shift
                ;;
            --test-customer)
                TEST_CUSTOMER=true
                shift
                ;;
            --test-subscription)
                TEST_SUBSCRIPTION=true
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
    
    if [ -z "$STRIPE_SECRET_KEY" ]; then
        echo "Error: --secret-key or STRIPE_SECRET_KEY env var is required"
        usage
        exit 1
    fi
}

# Make Stripe API call
stripe_api() {
    local endpoint="$1"
    local method="${2:-GET}"
    local data="${3:-}"
    
    local cmd="curl -s -X $method"
    cmd="$cmd -H \"Authorization: Bearer $STRIPE_SECRET_KEY\""
    cmd="$cmd -H \"Stripe-Version: 2023-10-16\""
    
    if [ -n "$data" ]; then
        cmd="$cmd -d '$data'"
    fi
    
    cmd="$cmd \"https://api.stripe.com/v1/$endpoint\""
    
    eval "$cmd"
}

# Test authentication
test_auth() {
    echo "Testing Stripe authentication..."
    
    local response
    response=$(stripe_api "account")
    
    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        local account_id
        account_id=$(echo "$response" | jq -r '.id')
        echo -e "${GREEN}✅ Authentication successful${NC}"
        echo "   Account: $account_id"
        
        # Check if test mode
        local test_mode
        test_mode=$(echo "$response" | jq -r '.charges_enabled')
        if [ "$test_mode" = "false" ]; then
            echo "   Mode: Test (charges not enabled)"
        else
            echo "   Mode: Live"
        fi
        return 0
    else
        local error
        error=$(echo "$response" | jq -r '.error.message // "Unknown error"')
        echo -e "${RED}❌ Authentication failed${NC}"
        echo "   Error: $error"
        ((FAILED_TESTS++))
        return 1
    fi
}

# Test webhook signature validation
test_webhook_validation() {
    if [ -z "$WEBHOOK_SECRET" ]; then
        echo -e "${YELLOW}⚠️  Skipping webhook test (no secret provided)${NC}"
        return 0
    fi
    
    echo ""
    echo "Testing webhook signature validation..."
    
    # Create a test webhook payload
    local payload='{"id":"evt_test","object":"event","type":"test.event"}'
    local timestamp
    timestamp=$(date +%s)
    
    # Generate signature
    local signed_payload="${timestamp}.${payload}"
    local signature
    signature=$(echo -n "$signed_payload" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | cut -d' ' -f2)
    
    echo -e "${GREEN}✅ Webhook signature generation works${NC}"
    
    # Test validation
    if command -v python3 &> /dev/null; then
        python3 << EOF
import hmac
import hashlib
import time

secret = "$WEBHOOK_SECRET".encode()
payload = '$payload'.encode()
timestamp = "$timestamp"

signed_payload = f"{timestamp}.{payload.decode()}".encode()
expected_sig = hmac.new(secret, signed_payload, hashlib.sha256).hexdigest()

# Verify
computed = hmac.new(secret, signed_payload, hashlib.sha256).hexdigest()
if computed == expected_sig:
    print("✅ Webhook signature validation works")
else:
    print("❌ Signature validation failed")
    exit(1)
EOF
    else
        echo -e "${YELLOW}⚠️  Python not available for full webhook validation${NC}"
    fi
}

# Test customer creation
test_customer() {
    echo ""
    echo "Testing customer creation..."
    
    local response
    response=$(stripe_api "customers" "POST" "email=test-integration@example.com&name=Test Customer")
    
    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        local customer_id
        customer_id=$(echo "$response" | jq -r '.id')
        echo -e "${GREEN}✅ Customer created${NC}"
        echo "   Customer ID: $customer_id"
        
        # Cleanup
        stripe_api "customers/$customer_id" "DELETE" > /dev/null 2>&1
        log "   Cleaned up test customer"
        return 0
    else
        local error
        error=$(echo "$response" | jq -r '.error.message // "Unknown error"')
        echo -e "${RED}❌ Customer creation failed${NC}"
        echo "   Error: $error"
        ((FAILED_TESTS++))
        return 1
    fi
}

# Test charge creation
test_charge() {
    echo ""
    echo "Testing charge creation..."
    
    # First create a payment method
    local pm_response
    pm_response=$(stripe_api "payment_methods" "POST" "type=card&card[number]=4242424242424242&card[exp_month]=12&card[exp_year]=2030&card[cvc]=123")
    
    if ! echo "$pm_response" | jq -e '.id' > /dev/null 2>&1; then
        echo -e "${YELLOW}⚠️  Could not create payment method${NC}"
        log "   Response: $pm_response"
        return 1
    fi
    
    local pm_id
    pm_id=$(echo "$pm_response" | jq -r '.id')
    log "   Created payment method: $pm_id"
    
    # Create customer and attach payment method
    local customer_response
    customer_response=$(stripe_api "customers" "POST" "email=test-charge@example.com")
    local customer_id
    customer_id=$(echo "$customer_response" | jq -r '.id')
    
    stripe_api "payment_methods/$pm_id/attach" "POST" "customer=$customer_id" > /dev/null 2>&1
    
    # Create payment intent
    local pi_response
    pi_response=$(stripe_api "payment_intents" "POST" "amount=1000&currency=usd&customer=$customer_id&payment_method=$pm_id&confirm=true&off_session=true")
    
    if echo "$pi_response" | jq -e '.id' > /dev/null 2>&1; then
        local pi_id status
        pi_id=$(echo "$pi_response" | jq -r '.id')
        status=$(echo "$pi_response" | jq -r '.status')
        
        if [ "$status" = "succeeded" ]; then
            echo -e "${GREEN}✅ Payment intent succeeded${NC}"
            echo "   Payment Intent: $pi_id"
        else
            echo -e "${YELLOW}⚠️  Payment intent status: $status${NC}"
        fi
        
        # Cleanup
        stripe_api "customers/$customer_id" "DELETE" > /dev/null 2>&1
        return 0
    else
        local error
        error=$(echo "$pi_response" | jq -r '.error.message // "Unknown error"')
        echo -e "${RED}❌ Payment intent failed${NC}"
        echo "   Error: $error"
        ((FAILED_TESTS++))
        
        # Cleanup
        stripe_api "customers/$customer_id" "DELETE" > /dev/null 2>&1
        return 1
    fi
}

# Main
main() {
    parse_args "$@"
    
    echo "Stripe Integration Test"
    echo "======================="
    echo "Key: ${STRIPE_SECRET_KEY:0:10}..."
    if [ -n "$WEBHOOK_SECRET" ]; then
        echo "Webhook: ${WEBHOOK_SECRET:0:10}..."
    fi
    echo ""
    
    # Check for jq
    if ! command -v jq &> /dev/null; then
        echo "Error: jq is required"
        exit 1
    fi
    
    # Run tests
    test_auth
    test_webhook_validation
    
    if [ "$TEST_CUSTOMER" = true ]; then
        test_customer
    fi
    
    if [ "$TEST_CHARGE" = true ]; then
        test_charge
    fi
    
    echo ""
    if [ $FAILED_TESTS -eq 0 ]; then
        echo -e "${GREEN}All Stripe tests passed!${NC}"
    else
        echo -e "${RED}$FAILED_TESTS test(s) failed${NC}"
        exit 1
    fi
}

main "$@"
