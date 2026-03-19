#!/usr/bin/env bash
#
# JavaScript/Node.js Profiling Wrapper
# Supports 0x for Node.js profiling
#

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Defaults
MODE="cpu"  # cpu, memory, browser
SCRIPT=""
FUNCTION=""
DURATION=30
OUTPUT_FILE=""
URL=""
HEAP_SNAPSHOT=false

usage() {
    cat << EOF
Usage: profile_js.sh [options] [script.js]

Options:
  --cpu              CPU profiling (default)
  --memory           Memory profiling
  --browser          Profile browser with Chrome DevTools
  --url URL          URL to profile (browser mode)
  --duration SECS    Profiling duration (default: 30)
  --function NAME    Profile specific function
  --flamegraph       Generate flamegraph
  --heap-snapshot    Take heap snapshot
  --output FILE      Output file
  --help             Show this help

Examples:
  profile_js.sh server.js --flamegraph
  profile_js.sh --browser --url http://localhost:3000
  profile_js.sh app.js --heap-snapshot
EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --cpu)
            MODE="cpu"
            shift
            ;;
        --memory)
            MODE="memory"
            shift
            ;;
        --browser)
            MODE="browser"
            shift
            ;;
        --url)
            URL="$2"
            shift 2
            ;;
        --duration)
            DURATION="$2"
            shift 2
            ;;
        --function)
            FUNCTION="$2"
            shift 2
            ;;
        --flamegraph)
            FLAMEGRAPH=true
            shift
            ;;
        --heap-snapshot)
            HEAP_SNAPSHOT=true
            shift
            ;;
        --output)
            OUTPUT_FILE="$2"
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
            SCRIPT="$1"
            shift
            ;;
    esac
done

echo -e "${BLUE}=== JavaScript Profiler ===${NC}"
echo "Mode: $MODE"
[[ -n "$SCRIPT" ]] && echo "Script: $SCRIPT"
echo ""

# Check for 0x
if [[ "$MODE" == "cpu" && -n "${FLAMEGRAPH:-}" ]]; then
    if ! command -v 0x &> /dev/null; then
        echo -e "${YELLOW}Installing 0x...${NC}"
        npm install -g 0x
    fi
fi

# CPU Profiling with 0x
run_cpu_profile() {
    if [[ -z "$SCRIPT" ]]; then
        echo -e "${RED}Error: Script required for CPU profiling${NC}" >&2
        exit 1
    fi
    
    if [[ ! -f "$SCRIPT" ]]; then
        echo -e "${RED}Error: Script not found: $SCRIPT${NC}" >&2
        exit 1
    fi
    
    if [[ -n "${FLAMEGRAPH:-}" ]]; then
        echo -e "${YELLOW}Running 0x flamegraph profiler...${NC}"
        local output_flag=""
        [[ -n "$OUTPUT_FILE" ]] && output_flag="--output-dir $(dirname "$OUTPUT_FILE")"
        
        0x $output_flag -- node "$SCRIPT" 2>&1 || true
        
        echo -e "${GREEN}Flamegraph generated${NC}"
    else
        echo -e "${YELLOW}Running Node.js built-in profiler...${NC}"
        
        # Use Node's built-in profiler
        node --prof "$SCRIPT" 2>&1 || true
        
        # Find and process the log
        local log_file
        log_file=$(ls -t isolate-*.log 2>/dev/null | head -1)
        
        if [[ -n "$log_file" ]]; then
            echo -e "${YELLOW}Processing profile...${NC}"
            node --prof-process "$log_file"
            
            # Clean up
            rm -f "$log_file"
        fi
    fi
}

# Memory profiling
run_memory_profile() {
    if [[ -z "$SCRIPT" ]]; then
        echo -e "${RED}Error: Script required for memory profiling${NC}" >&2
        exit 1
    fi
    
    if [[ "$HEAP_SNAPSHOT" == "true" ]]; then
        echo -e "${YELLOW}Taking heap snapshot...${NC}"
        
        local output="${OUTPUT_FILE:-heap.heapsnapshot}"
        
        node --expose-gc -e "
const v8 = require('v8');
const fs = require('fs');

// Load and run the script
require('./$SCRIPT');

// Force GC and take snapshot
gc();
const snapshot = v8.writeHeapSnapshot('$output');
console.log('Heap snapshot saved to:', snapshot);
"
        echo -e "${GREEN}Heap snapshot saved to: $output${NC}"
        echo "Open in Chrome DevTools > Memory tab to analyze"
    else
        echo -e "${YELLOW}Running with memory tracking...${NC}"
        node --trace-gc "$SCRIPT" 2>&1 | head -100
    fi
}

# Browser profiling
run_browser_profile() {
    if [[ -z "$URL" ]]; then
        echo -e "${RED}Error: --url required for browser profiling${NC}" >&2
        exit 1
    fi
    
    echo -e "${YELLOW}Browser profiling: $URL${NC}"
    
    # Check for Chrome/Chromium
    local chrome_cmd=""
    if command -v google-chrome &> /dev/null; then
        chrome_cmd="google-chrome"
    elif command -v chromium &> /dev/null; then
        chrome_cmd="chromium"
    elif command -v chromium-browser &> /dev/null; then
        chrome_cmd="chromium-browser"
    else
        echo -e "${YELLOW}Chrome not found. Manual profiling instructions:${NC}"
        echo "1. Open Chrome DevTools (F12)"
        echo "2. Go to Performance tab"
        echo "3. Click record and navigate to: $URL"
        echo "4. Stop recording after ${DURATION}s"
        echo "5. Analyze the flamegraph"
        exit 0
    fi
    
    echo "Launching Chrome with debugging enabled..."
    echo "Open chrome://inspect to connect"
    
    $chrome_cmd \
        --remote-debugging-port=9222 \
        --no-first-run \
        --no-default-browser-check \
        --user-data-dir=$(mktemp -d) \
        "$URL" &
    
    local chrome_pid=$!
    
    echo "Chrome started with PID: $chrome_pid"
    echo "Profiling for ${DURATION} seconds..."
    sleep "$DURATION"
    
    # Clean up
    kill $chrome_pid 2>/dev/null || true
    
    echo -e "${GREEN}Browser profiling complete${NC}"
}

# Main execution
case "$MODE" in
    cpu)
        run_cpu_profile
        ;;
    memory)
        run_memory_profile
        ;;
    browser)
        run_browser_profile
        ;;
    *)
        echo -e "${RED}Unknown mode: $MODE${NC}" >&2
        exit 1
        ;;
esac

echo ""
echo -e "${BLUE}=== JavaScript Profiling Complete ===${NC}"
