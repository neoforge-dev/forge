#!/usr/bin/env bash
#
# OpenAI Integration Test Script
# Usage: test-openai.sh [options]
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
OpenAI Integration Test

Usage: test-openai.sh [options]

Options:
  --api-key KEY        OpenAI API key (required)
  --model MODEL        Model to test (default: gpt-3.5-turbo)
  --test-completion    Test chat completion
  --test-embedding     Test embeddings
  --test-image         Test image generation
  --max-latency MS     Max acceptable latency
  --help               Show this help

Examples:
  test-openai.sh --api-key sk-xxx
  test-openai.sh --api-key sk-xxx --model gpt-4 --test-completion

EOF
}

# Parse arguments
parse_args() {
    OPENAI_API_KEY="${OPENAI_API_KEY:-}"
    MODEL="gpt-3.5-turbo"
    TEST_COMPLETION=false
    TEST_EMBEDDING=false
    TEST_IMAGE=false
    MAX_LATENCY=""
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --api-key)
                OPENAI_API_KEY="$2"
                shift 2
                ;;
            --model)
                MODEL="$2"
                shift 2
                ;;
            --test-completion)
                TEST_COMPLETION=true
                shift
                ;;
            --test-embedding)
                TEST_EMBEDDING=true
                shift
                ;;
            --test-image)
                TEST_IMAGE=true
                shift
                ;;
            --max-latency)
                MAX_LATENCY="$2"
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
    
    if [ -z "$OPENAI_API_KEY" ]; then
        echo "Error: --api-key or OPENAI_API_KEY env var is required"
        usage
        exit 1
    fi
    
    # If no specific tests requested, run all
    if [ "$TEST_COMPLETION" = false ] && [ "$TEST_EMBEDDING" = false ] && [ "$TEST_IMAGE" = false ]; then
        TEST_COMPLETION=true
        TEST_EMBEDDING=true
    fi
}

# Make OpenAI API call
openai_api() {
    local endpoint="$1"
    local data="$2"
    
    curl -s -X POST \
        -H "Authorization: Bearer $OPENAI_API_KEY" \
        -H "Content-Type: application/json" \
        "https://api.openai.com/v1/$endpoint" \
        -d "$data"
}

# Test authentication
test_auth() {
    echo "Testing OpenAI authentication..."
    
    local response
    response=$(curl -s -H "Authorization: Bearer $OPENAI_API_KEY" \
        "https://api.openai.com/v1/models")
    
    if echo "$response" | jq -e '.data' > /dev/null 2>&1; then
        local model_count
        model_count=$(echo "$response" | jq '.data | length')
        echo -e "${GREEN}✅ Authentication successful${NC}"
        echo "   Available models: $model_count"
        
        # Check if requested model is available
        if echo "$response" | jq -e ".data[] | select(.id == \"$MODEL\")" > /dev/null; then
            echo "   Model '$MODEL' is available"
        else
            echo -e "${YELLOW}⚠️  Model '$MODEL' may not be available${NC}"
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

# Test chat completion
test_completion() {
    echo ""
    echo "Testing chat completion ($MODEL)..."
    
    local start_time end_time duration
    start_time=$(date +%s%N)
    
    local response
    response=$(openai_api "chat/completions" "{
        \"model\": \"$MODEL\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Say 'OpenAI test passed'\"}],
        \"max_tokens\": 20
    }")
    
    end_time=$(date +%s%N)
    duration=$(( (end_time - start_time) / 1000000 ))
    
    if echo "$response" | jq -e '.choices[0].message.content' > /dev/null 2>&1; then
        local content
        content=$(echo "$response" | jq -r '.choices[0].message.content')
        echo -e "${GREEN}✅ Completion successful${NC}"
        echo "   Response: $content"
        echo "   Latency: ${duration}ms"
        
        # Check latency
        if [ -n "$MAX_LATENCY" ] && [ "$duration" -gt "$MAX_LATENCY" ]; then
            echo -e "${YELLOW}⚠️  Latency exceeded ${MAX_LATENCY}ms${NC}"
        fi
        
        # Show token usage
        local prompt_tokens completion_tokens
        prompt_tokens=$(echo "$response" | jq -r '.usage.prompt_tokens // "N/A"')
        completion_tokens=$(echo "$response" | jq -r '.usage.completion_tokens // "N/A"')
        log "   Tokens: $prompt_tokens prompt, $completion_tokens completion"
        
        return 0
    else
        local error
        error=$(echo "$response" | jq -r '.error.message // "Unknown error"')
        echo -e "${RED}❌ Completion failed${NC}"
        echo "   Error: $error"
        ((FAILED_TESTS++))
        return 1
    fi
}

# Test embeddings
test_embedding() {
    echo ""
    echo "Testing embeddings..."
    
    local start_time end_time duration
    start_time=$(date +%s%N)
    
    local response
    response=$(openai_api "embeddings" "{
        \"input\": \"OpenAI integration test\",
        \"model\": \"text-embedding-3-small\"
    }")
    
    end_time=$(date +%s%N)
    duration=$(( (end_time - start_time) / 1000000 ))
    
    if echo "$response" | jq -e '.data[0].embedding' > /dev/null 2>&1; then
        local embedding_dim
        embedding_dim=$(echo "$response" | jq '.data[0].embedding | length')
        echo -e "${GREEN}✅ Embedding successful${NC}"
        echo "   Dimensions: $embedding_dim"
        echo "   Latency: ${duration}ms"
        
        # Show token usage
        local tokens
        tokens=$(echo "$response" | jq -r '.usage.total_tokens // "N/A"')
        log "   Tokens: $tokens"
        
        return 0
    else
        local error
        error=$(echo "$response" | jq -r '.error.message // "Unknown error"')
        echo -e "${RED}❌ Embedding failed${NC}"
        echo "   Error: $error"
        ((FAILED_TESTS++))
        return 1
    fi
}

# Main
main() {
    parse_args "$@"
    
    echo "OpenAI Integration Test"
    echo "======================="
    echo "Key: ${OPENAI_API_KEY:0:10}..."
    echo "Model: $MODEL"
    echo ""
    
    # Check for jq
    if ! command -v jq &> /dev/null; then
        echo "Error: jq is required"
        exit 1
    fi
    
    # Run tests
    test_auth
    
    if [ "$TEST_COMPLETION" = true ]; then
        test_completion
    fi
    
    if [ "$TEST_EMBEDDING" = true ]; then
        test_embedding
    fi
    
    echo ""
    if [ $FAILED_TESTS -eq 0 ]; then
        echo -e "${GREEN}All OpenAI tests passed!${NC}"
    else
        echo -e "${RED}$FAILED_TESTS test(s) failed${NC}"
        exit 1
    fi
}

main "$@"
