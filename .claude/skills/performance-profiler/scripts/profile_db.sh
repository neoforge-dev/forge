#!/usr/bin/env bash
#
# Database Query Profiling for PostgreSQL
# Analyzes slow queries, detects N+1 patterns, and suggests indexes
#

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Defaults
MODE="slow-queries"
DB_URL="${DATABASE_URL:-}"
THRESHOLD=100  # ms
TIME_WINDOW="1 hour"
TABLE=""
QUERY=""
LIMIT=20

usage() {
    cat << EOF
Usage: profile_db.sh [options]

Options:
  --slow-queries           Show slow queries (default)
  --threshold MS           Slow query threshold in ms (default: 100)
  --time-window DURATION   Time window (default: 1 hour)
  --table NAME             Filter by table
  --detect-n-plus-1        Detect N+1 query patterns
  --explain "QUERY"        Analyze query execution plan
  --index-usage            Show index usage statistics
  --missing-only           Show only missing indexes
  --limit N                Limit results (default: 20)
  --db-url URL             Database connection URL
  --help                   Show this help
EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --slow-queries)
            MODE="slow-queries"
            shift
            ;;
        --detect-n-plus-1)
            MODE="n-plus-1"
            shift
            ;;
        --explain)
            MODE="explain"
            QUERY="$2"
            shift 2
            ;;
        --index-usage)
            MODE="index-usage"
            shift
            ;;
        --missing-only)
            MODE="missing-only"
            shift
            ;;
        --threshold)
            THRESHOLD="$2"
            shift 2
            ;;
        --time-window)
            TIME_WINDOW="$2"
            shift 2
            ;;
        --table)
            TABLE="$2"
            shift 2
            ;;
        --limit)
            LIMIT="$2"
            shift 2
            ;;
        --db-url)
            DB_URL="$2"
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
            echo -e "${RED}Unexpected argument: $1${NC}" >&2
            exit 1
            ;;
    esac
done

# Check for database URL
if [[ -z "$DB_URL" ]]; then
    if [[ -f .env ]]; then
        DB_URL=$(grep -E "^DATABASE_URL=" .env | cut -d= -f2- | tr -d '"')
    fi
fi

if [[ -z "$DB_URL" ]]; then
    echo -e "${RED}Error: DATABASE_URL not set${NC}" >&2
    exit 1
fi

echo -e "${BLUE}=== Database Query Profiler ===${NC}"
echo "Mode: $MODE"
echo ""

check_pg_stat_statements() {
    local result
    result=$(psql "$DB_URL" -t -c "SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'" 2>/dev/null || echo "")
    if [[ -z "$result" ]]; then
        echo -e "${YELLOW}Warning: pg_stat_statements extension not found${NC}"
        return 1
    fi
    return 0
}

run_slow_queries() {
    echo -e "${YELLOW}Analyzing slow queries...${NC}"
    psql "$DB_URL" -c "
SELECT
    LEFT(query, 60) as query_preview,
    calls,
    ROUND(mean_exec_time::numeric, 2) as avg_ms,
    ROUND(total_exec_time::numeric, 2) as total_ms
FROM pg_stat_statements
WHERE mean_exec_time > $THRESHOLD
ORDER BY total_exec_time DESC
LIMIT $LIMIT;
"
}

run_n_plus_1() {
    echo -e "${YELLOW}Detecting N+1 patterns...${NC}"
    psql "$DB_URL" -c "
SELECT
    REGEXP_REPLACE(query, '\$[0-9]+', '?', 'g') as pattern,
    calls,
    ROUND(mean_exec_time::numeric, 2) as avg_ms
FROM pg_stat_statements
WHERE calls > 100
  AND query ILIKE 'SELECT%WHERE%id =%'
ORDER BY calls DESC
LIMIT $LIMIT;
"
}

run_explain() {
    if [[ -z "$QUERY" ]]; then
        echo -e "${RED}Error: No query provided${NC}" >&2
        exit 1
    fi
    psql "$DB_URL" -c "EXPLAIN (ANALYZE, BUFFERS) $QUERY"
}

run_index_usage() {
    echo -e "${YELLOW}Index usage analysis...${NC}"
    psql "$DB_URL" -c "
SELECT
    schemaname || '.' || relname as table_name,
    indexrelname as index_name,
    idx_scan as scans,
    pg_size_pretty(pg_relation_size(indexrelid)) as size
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
ORDER BY idx_scan ASC
LIMIT $LIMIT;
"
}

run_missing_indexes() {
    echo -e "${YELLOW}Finding missing indexes...${NC}"
    psql "$DB_URL" -c "
SELECT
    relname as table_name,
    seq_scan as seq_scans,
    idx_scan as idx_scans,
    n_live_tup as row_count,
    CASE 
        WHEN seq_scan > 0 AND (idx_scan IS NULL OR idx_scan = 0) THEN 'CRITICAL'
        WHEN seq_scan > idx_scan THEN 'WARNING'
        ELSE 'OK'
    END as status
FROM pg_stat_user_tables
WHERE n_live_tup > 1000
ORDER BY seq_scan DESC
LIMIT $LIMIT;
"
}

case "$MODE" in
    slow-queries)
        check_pg_stat_statements && run_slow_queries
        ;;
    n-plus-1)
        check_pg_stat_statements && run_n_plus_1
        ;;
    explain)
        run_explain
        ;;
    index-usage)
        run_index_usage
        ;;
    missing-only)
        run_missing_indexes
        ;;
esac

echo ""
echo -e "${BLUE}=== Database Profiling Complete ===${NC}"
