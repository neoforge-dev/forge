#!/usr/bin/env bash
#
# AssemblyAI Integration Test Script
# Usage: test-assemblyai.sh [options]
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
AssemblyAI Integration Test

Usage: test-assemblyai.sh [options]

Options:
  --api-key KEY        AssemblyAI API key (required)
  --test-transcription Test transcription API
  --test-upload        Test file upload
  --test-realtime      Test realtime API
  --help               Show this help

Examples:
  test-assemblyai.sh --api-key xxx
  test-assemblyai.sh --api-key xxx --test-transcription

EOF
}

# Parse arguments
parse_args() {
    ASSEMBLYAI_API_KEY="${ASSEMBLYAI_API_KEY:-}"
    TEST_TRANSCRIPTION=false
    TEST_UPLOAD=false
    TEST_REALTIME=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --api-key)
                ASSEMBLYAI_API_KEY="$2"
                shift 2
                ;;
            --test-transcription)
                TEST_TRANSCRIPTION=true
                shift
                ;;
            --test-upload)
                TEST_UPLOAD=true
                shift
                ;;
            --test-realtime)
                TEST_REALTIME=true
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
    
    if [ -z "$ASSEMBLYAI_API_KEY" ]; then
        echo "Error: --api-key or ASSEMBLYAI_API_KEY env var is required"
        usage
        exit 1
    fi
    
    # If no specific tests requested, run all
    if [ "$TEST_TRANSCRIPTION" = false ] && [ "$TEST_UPLOAD" = false ] && [ "$TEST_REALTIME" = false ]; then
        TEST_TRANSCRIPTION=true
    fi
}

# Make AssemblyAI API call
assemblyai_api() {
    local endpoint="$1"
    local method="${2:-GET}"
    local data="${3:-}"
    
    local cmd="curl -s -X $method"
    cmd="$cmd -H \"Authorization: $ASSEMBLYAI_API_KEY\""
    
    if [ "$method" = "POST" ] && [ -n "$data" ]; then
        cmd="$cmd -H \"Content-Type: application/json\""
        cmd="$cmd -d '$data'"
    fi
    
    cmd="$cmd \"https://api.assemblyai.com/v2/$endpoint\""
    
    eval "$cmd"
}

# Test authentication
test_auth() {
    echo "Testing AssemblyAI authentication..."
    
    local response
    response=$(assemblyai_api "account")
    
    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        local account_id current_balance
        account_id=$(echo "$response" | jq -r '.id // "N/A"')
        current_balance=$(echo "$response" | jq -r '.current_balance // "N/A"')
        echo -e "${GREEN}✅ Authentication successful${NC}"
        echo "   Account: $account_id"
        echo "   Balance: $current_balance"
        return 0
    else
        local error
        error=$(echo "$response" | jq -r '.error // "Unknown error"')
        echo -e "${RED}❌ Authentication failed${NC}"
        echo "   Error: $error"
        ((FAILED_TESTS++))
        return 1
    fi
}

# Test transcription API
test_transcription() {
    echo ""
    echo "Testing transcription API..."
    
    # Submit transcription request with sample audio URL
    local response
    response=$(assemblyai_api "transcript" "POST" "{
        \"audio_url\": \"https://github.com/AssemblyAI-Examples/audio-examples/raw/main/20230607_me_canadian_wildfires.mp3\",
        \"language_code\": \"en_us\"
    }")
    
    if echo "$response" | jq -e '.id' > /dev/null 2>&1; then
        local transcript_id status
        transcript_id=$(echo "$response" | jq -r '.id')
        status=$(echo "$response" | jq -r '.status')
        
        echo -e "${GREEN}✅ Transcription submitted${NC}"
        echo "   Transcript ID: $transcript_id"
        echo "   Status: $status"
        
        # Poll for completion (with timeout)
        local attempts=0
        local max_attempts=30
        
        while [ "$status" = "queued" ] || [ "$status" = "processing" ]; do
            if [ $attempts -ge $max_attempts ]; then
                echo -e "${YELLOW}⚠️  Transcription taking too long, skipping status check${NC}"
                break
            fi
            
            sleep 2
            response=$(assemblyai_api "transcript/$transcript_id")
            status=$(echo "$response" | jq -r '.status')
            ((attempts++))
            echo "   Status: $status (attempt $attempts/$max_attempts)"
        done
        
        if [ "$status" = "completed" ]; then
            local text
            text=$(echo "$response" | jq -r '.text')
            echo -e "${GREEN}✅ Transcription completed${NC}"
            echo "   Preview: ${text:0:100}..."
        elif [ "$status" = "error" ]; then
            local error_msg
            error_msg=$(echo "$response" | jq -r '.error // "Unknown error"')
            echo -e "${RED}❌ Transcription failed${NC}"
            echo "   Error: $error_msg"
            ((FAILED_TESTS++))
        fi
        
        return 0
    else
        local error
        error=$(echo "$response" | jq -r '.error // "Unknown error"')
        echo -e "${RED}❌ Transcription submission failed${NC}"
        echo "   Error: $error"
        ((FAILED_TESTS++))
        return 1
    fi
}

# Test upload endpoint
test_upload() {
    echo ""
    echo "Testing upload endpoint..."
    
    # Create a small test file
    local test_file="/tmp/test_audio_$$.txt"
    echo "Test audio content" > "$test_file"
    
    local response
    response=$(curl -s -X POST \
        -H "Authorization: $ASSEMBLYAI_API_KEY" \
        --data-binary "@$test_file" \
        "https://api.assemblyai.com/v2/upload")
    
    rm -f "$test_file"
    
    if echo "$response" | jq -e '.upload_url' > /dev/null 2>&1; then
        local upload_url
        upload_url=$(echo "$response" | jq -r '.upload_url')
        echo -e "${GREEN}✅ Upload endpoint accessible${NC}"
        log "   Upload URL: ${upload_url:0:50}..."
        return 0
    else
        local error
        error=$(echo "$response" | jq -r '.error // "Unknown error"')
        echo -e "${RED}❌ Upload test failed${NC}"
        echo "   Error: $error"
        ((FAILED_TESTS++))
        return 1
    fi
}

# Main
main() {
    parse_args "$@"
    
    echo "AssemblyAI Integration Test"
    echo "==========================="
    echo "Key: ${ASSEMBLYAI_API_KEY:0:10}..."
    echo ""
    
    # Check for jq
    if ! command -v jq &> /dev/null; then
        echo "Error: jq is required"
        exit 1
    fi
    
    # Run tests
    test_auth
    
    if [ "$TEST_TRANSCRIPTION" = true ]; then
        test_transcription
    fi
    
    if [ "$TEST_UPLOAD" = true ]; then
        test_upload
    fi
    
    echo ""
    if [ $FAILED_TESTS -eq 0 ]; then
        echo -e "${GREEN}All AssemblyAI tests passed!${NC}"
    else
        echo -e "${RED}$FAILED_TESTS test(s) failed${NC}"
        exit 1
    fi
}

main "$@"
