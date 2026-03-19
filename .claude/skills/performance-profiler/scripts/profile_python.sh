#!/usr/bin/env bash
#
# Python Profiling Wrapper
# Supports cProfile (built-in) and py-spy (sampling profiler)
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
USE_PYSPY=false
USE_FLAMEGRAPH=false
OUTPUT_FORMAT="text"
OUTPUT_FILE=""
DURATION=0
FUNCTION=""
SCRIPT=""
SCRIPT_ARGS=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --pyspy)
            USE_PYSPY=true
            shift
            ;;
        --flamegraph)
            USE_FLAMEGRAPH=true
            shift
            ;;
        --format)
            OUTPUT_FORMAT="$2"
            shift 2
            ;;
        --output)
            OUTPUT_FILE="$2"
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
        --help)
            echo "Usage: profile_python.sh [options] <script.py> [script_args...]"
            echo ""
            echo "Options:"
            echo "  --pyspy          Use py-spy sampling profiler (requires install)"
            echo "  --flamegraph     Generate flamegraph output"
            echo "  --format FORMAT  Output format: text, json, html (default: text)"
            echo "  --output FILE    Output file (default: stdout)"
            echo "  --duration SECS  Profile for N seconds (py-spy only)"
            echo "  --function NAME  Profile specific function only"
            echo "  --help           Show this help"
            exit 0
            ;;
        -*)
            echo -e "${RED}Unknown option: $1${NC}" >&2
            exit 1
            ;;
        *)
            if [[ -z "$SCRIPT" ]]; then
                SCRIPT="$1"
            else
                SCRIPT_ARGS="$SCRIPT_ARGS $1"
            fi
            shift
            ;;
    esac
done

if [[ -z "$SCRIPT" ]]; then
    echo -e "${RED}Error: No script specified${NC}" >&2
    echo "Usage: profile_python.sh [options] <script.py> [script_args...]" >&2
    exit 1
fi

if [[ ! -f "$SCRIPT" ]]; then
    echo -e "${RED}Error: Script not found: $SCRIPT${NC}" >&2
    exit 1
fi

echo -e "${BLUE}=== Python Profiler ===${NC}"
echo "Script: $SCRIPT"
echo "Profiler: $([[ "$USE_PYSPY" == "true" ]] && echo "py-spy" || echo "cProfile")"
echo ""

# Function to run cProfile
run_cprofile() {
    local script="$1"
    local args="$2"
    local output="${3:-}"
    local format="$4"
    
    local stats_file
    stats_file=$(mktemp)
    
    echo -e "${YELLOW}Running cProfile...${NC}"
    
    # Run with cProfile
    if [[ -n "$FUNCTION" ]]; then
        # Profile specific function using line_profiler style
        python3 -c "
import sys
sys.path.insert(0, '.')
import cProfile
import pstats
from io import StringIO

# Import the module
module_path = '$script'.replace('/', '.').replace('.py', '')
module = __import__(module_path)

# Get the function
func = getattr(module, '$FUNCTION', None)
if func is None:
    print(f'Function $FUNCTION not found in module')
    sys.exit(1)

# Profile it
profiler = cProfile.Profile()
profiler.enable()
result = func()
profiler.disable()

# Print stats
s = StringIO()
ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
ps.print_stats(20)
print(s.getvalue())
" 2>&1
    else
        # Profile entire script
        python3 -m cProfile -o "$stats_file" "$script" $args 2>&1 || true
        
        # Process stats
        python3 -c "
import pstats
import sys
from io import StringIO

stats = pstats.Stats('$stats_file')
stats.strip_dirs()
stats.sort_stats('cumulative')

if '$format' == 'json':
    import json
    result = []
    for func, (cc, nc, tt, ct, callers) in stats.stats.items():
        result.append({
            'file': func[0],
            'line': func[1],
            'function': func[2],
            'calls': nc,
            'total_time': ct,
            'own_time': tt
        })
    print(json.dumps(result[:50], indent=2))
else:
    s = StringIO()
    stats.stream = s
    stats.print_stats(30)
    print(s.getvalue())
" 2>&1
    fi
    
    rm -f "$stats_file"
}

# Function to run py-spy
run_pyspy() {
    local script="$1"
    local args="$2"
    local output="${3:-}"
    
    if ! command -v py-spy &> /dev/null; then
        echo -e "${RED}Error: py-spy not found. Install with: pip install py-spy${NC}" >&2
        echo -e "${YELLOW}Falling back to cProfile...${NC}"
        USE_PYSPY=false
        return 1
    fi
    
    echo -e "${YELLOW}Running py-spy...${NC}"
    
    local duration_flag=""
    if [[ "$DURATION" -gt 0 ]]; then
        duration_flag="--duration $DURATION"
    fi
    
    local flamegraph_flag=""
    if [[ "$USE_FLAMEGRAPH" == "true" ]]; then
        if [[ -n "$output" ]]; then
            flamegraph_flag="--output $output"
        else
            flamegraph_flag="--output profile_flamegraph.svg"
        fi
        echo -e "${GREEN}Generating flamegraph...${NC}"
    fi
    
    # Run py-spy
    if [[ "$USE_FLAMEGRAPH" == "true" ]]; then
        py-spy record -o "$flamegraph_flag" -- python3 "$script" $args 2>&1 || true
        echo -e "${GREEN}Flamegraph saved to: ${flamegraph_flag#--output }${NC}"
    else
        py-spy top $duration_flag -- python3 "$script" $args 2>&1 || true
    fi
}

# Function to convert to flamegraph from cProfile
convert_to_flamegraph() {
    local stats_file="$1"
    local output="${2:-flamegraph.svg}"
    
    # Check for flameprof
    if python3 -c "import flameprof" 2>/dev/null; then
        python3 -m flameprof "$stats_file" > "$output"
        echo -e "${GREEN}Flamegraph saved to: $output${NC}"
    else
        echo -e "${YELLOW}Note: Install flameprof for flamegraph generation: pip install flameprof${NC}"
    fi
}

# Main execution
main() {
    if [[ "$USE_PYSPY" == "true" ]]; then
        run_pyspy "$SCRIPT" "$SCRIPT_ARGS" "$OUTPUT_FILE" || {
            echo -e "${YELLOW}Falling back to cProfile...${NC}"
            run_cprofile "$SCRIPT" "$SCRIPT_ARGS" "$OUTPUT_FILE" "$OUTPUT_FORMAT"
        }
    else
        run_cprofile "$SCRIPT" "$SCRIPT_ARGS" "$OUTPUT_FILE" "$OUTPUT_FORMAT"
    fi
    
    echo ""
    echo -e "${BLUE}=== Profiling Complete ===${NC}"
}

main "$@"
