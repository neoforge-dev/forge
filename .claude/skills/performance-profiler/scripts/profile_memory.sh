#!/usr/bin/env bash
#
# Memory Profiling Wrapper
# Supports memory_profiler and tracemalloc
#

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Defaults
MODE="line"  # line, snapshot, diff
OUTPUT_FILE=""
FUNCTION=""
INTERVAL=0.1
BEFORE=""
AFTER=""
TOP_N=20

usage() {
    cat << EOF
Usage: profile_memory.sh [options] [module.py]

Options:
  --line-by-line     Profile line-by-line memory usage (default)
  --snapshot         Take memory snapshot with tracemalloc
  --diff             Compare two snapshots
  --before FILE      Before snapshot for diff mode
  --after FILE       After snapshot for diff mode
  --function NAME    Profile specific function only
  --interval SECS    Sampling interval (default: 0.1)
  --output FILE      Output file
  --top N            Show top N entries (default: 20)
  --help             Show this help

Examples:
  profile_memory.sh app/services.py --line-by-line
  profile_memory.sh --snapshot --output baseline.snapshot
  profile_memory.sh --diff --before before.snapshot --after after.snapshot
EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --line-by-line)
            MODE="line"
            shift
            ;;
        --snapshot)
            MODE="snapshot"
            shift
            ;;
        --diff)
            MODE="diff"
            shift
            ;;
        --before)
            BEFORE="$2"
            shift 2
            ;;
        --after)
            AFTER="$2"
            shift 2
            ;;
        --function)
            FUNCTION="$2"
            shift 2
            ;;
        --interval)
            INTERVAL="$2"
            shift 2
            ;;
        --output)
            OUTPUT_FILE="$2"
            shift 2
            ;;
        --top)
            TOP_N="$2"
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
            MODULE="$1"
            shift
            ;;
    esac
done

echo -e "${BLUE}=== Memory Profiler ===${NC}"
echo "Mode: $MODE"
[[ -n "${MODULE:-}" ]] && echo "Module: $MODULE"
[[ -n "$FUNCTION" ]] && echo "Function: $FUNCTION"
echo ""

# Check for memory_profiler
if [[ "$MODE" == "line" ]] && ! python3 -c "import memory_profiler" 2>/dev/null; then
    echo -e "${YELLOW}Installing memory_profiler...${NC}"
    pip install memory_profiler --quiet
fi

# Line-by-line profiling
run_line_profiler() {
    local module="$1"
    local func="$2"
    
    if [[ ! -f "$module" ]]; then
        echo -e "${RED}Error: Module not found: $module${NC}" >&2
        exit 1
    fi
    
    echo -e "${YELLOW}Running line-by-line memory profiler...${NC}"
    
    if [[ -n "$func" ]]; then
        # Profile specific function
        python3 -c "
from memory_profiler import profile
import sys
sys.path.insert(0, '.')

# Read and compile the module
with open('$module') as f:
    source = f.read()

# Create a profiled version of the target function
exec(compile(source, '$module', 'exec'))

if '$func' in dir():
    profiled_func = profile(dir()['$func'])
    result = profiled_func()
else:
    print(f'Function $func not found')
    sys.exit(1)
"
    else
        # Profile entire module
        python3 -m memory_profiler "$module"
    fi
}

# Take memory snapshot
run_snapshot() {
    local output="${1:-memory.snapshot}"
    
    echo -e "${YELLOW}Taking memory snapshot...${NC}"
    
    python3 -c "
import tracemalloc
import pickle
import sys

tracemalloc.start()

# Take snapshot
snapshot = tracemalloc.take_snapshot()

# Save to file
with open('$output', 'wb') as f:
    pickle.dump(snapshot, f)

# Print top stats
top_stats = snapshot.statistics('lineno')
print(f'Top $TOP_N memory allocations:')
print('=' * 60)
for stat in top_stats[:$TOP_N]:
    print(f'{stat.size / 1024 / 1024:.2f} MB: {stat.traceback.format()[-1]}')

print(f'\\nSnapshot saved to: $output')
print(f'Total allocated: {tracemalloc.get_traced_memory()[1] / 1024 / 1024:.2f} MB')
"
    
    echo -e "${GREEN}Snapshot saved to: $output${NC}"
}

# Compare snapshots
run_diff() {
    local before="$1"
    local after="$2"
    
    if [[ ! -f "$before" ]]; then
        echo -e "${RED}Error: Before snapshot not found: $before${NC}" >&2
        exit 1
    fi
    
    if [[ ! -f "$after" ]]; then
        echo -e "${RED}Error: After snapshot not found: $after${NC}" >&2
        exit 1
    fi
    
    echo -e "${YELLOW}Comparing memory snapshots...${NC}"
    
    python3 -c "
import tracemalloc
import pickle

# Load snapshots
with open('$before', 'rb') as f:
    before_snap = pickle.load(f)
with open('$after', 'rb') as f:
    after_snap = pickle.load(f)

# Compare
diff = after_snap.compare_to(before_snap, 'lineno')

print('Memory Changes (positive = growth, negative = freed):')
print('=' * 70)
for stat in diff[:$TOP_N]:
    size_change = stat.size_diff / 1024 / 1024
    count_change = stat.count_diff
    sign = '+' if size_change > 0 else ''
    print(f'{sign}{size_change:.2f} MB ({sign}{count_change}): {stat.traceback.format()[-1]}')

# Summary
before_size = sum(stat.size for stat in before_snap.statistics('lineno'))
after_size = sum(stat.size for stat in after_snap.statistics('lineno'))
total_change = (after_size - before_size) / 1024 / 1024

print(f'\\nTotal memory change: {total_change:+.2f} MB')
if total_change > 50:
    print('WARNING: Significant memory growth detected!')
elif total_change > 10:
    print('CAUTION: Moderate memory growth')
else:
    print('OK: Memory stable')
"
}

# Main execution
case "$MODE" in
    line)
        if [[ -z "${MODULE:-}" ]]; then
            echo -e "${RED}Error: Module required for line-by-line profiling${NC}" >&2
            usage >&2
            exit 1
        fi
        run_line_profiler "$MODULE" "$FUNCTION"
        ;;
    snapshot)
        run_snapshot "${OUTPUT_FILE:-memory.snapshot}"
        ;;
    diff)
        if [[ -z "$BEFORE" || -z "$AFTER" ]]; then
            echo -e "${RED}Error: --before and --after required for diff mode${NC}" >&2
            exit 1
        fi
        run_diff "$BEFORE" "$AFTER"
        ;;
    *)
        echo -e "${RED}Unknown mode: $MODE${NC}" >&2
        exit 1
        ;;
esac

echo ""
echo -e "${BLUE}=== Memory Profiling Complete ===${NC}"
