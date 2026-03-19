#!/usr/bin/env bash
#
# Database Integration Test Script
# Usage: test-db.sh [options]
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMEOUT="${TIMEOUT:-30}"
VERBOSE="${VERBOSE:-false}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    if [ "$VERBOSE" = "true" ]; then
        echo "$@"
    fi
}

usage() {
    cat <<EOF
Database Integration Test

Usage: test-db.sh [options]

Options:
  --url URL            Database connection URL (required)
  --migrations         Check migrations are up to date
  --alembic            Use Alembic for migrations
  --test-queries       Run test queries
  --connection-pool    Test connection pooling
  --performance-test   Run performance tests
  --max-time MS        Max query time in milliseconds
  --help               Show this help

Examples:
  test-db.sh --url postgresql://user:pass@localhost/db
  test-db.sh --url postgresql://user:pass@localhost/db --migrations --alembic

EOF
}

# Parse arguments
parse_args() {
    DATABASE_URL=""
    CHECK_MIGRATIONS=false
    USE_ALEMBIC=false
    TEST_QUERIES=false
    TEST_CONNECTION_POOL=false
    PERFORMANCE_TEST=false
    MAX_TIME=""
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --url)
                DATABASE_URL="$2"
                shift 2
                ;;
            --migrations)
                CHECK_MIGRATIONS=true
                shift
                ;;
            --alembic)
                USE_ALEMBIC=true
                shift
                ;;
            --test-queries)
                TEST_QUERIES=true
                shift
                ;;
            --connection-pool)
                TEST_CONNECTION_POOL=true
                shift
                ;;
            --performance-test)
                PERFORMANCE_TEST=true
                shift
                ;;
            --max-time)
                MAX_TIME="$2"
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
    
    if [ -z "$DATABASE_URL" ]; then
        echo "Error: --url is required"
        usage
        exit 1
    fi
}

# Extract database type from URL
get_db_type() {
    local url="$1"
    echo "$url" | cut -d: -f1
}

# Test PostgreSQL connection
test_postgresql() {
    local url="$1"
    local start_time end_time duration
    
    echo "Testing PostgreSQL connection..."
    
    start_time=$(date +%s%N)
    
    # Test connection using psql
    if command -v psql &> /dev/null; then
        if psql "$url" -c "SELECT 1;" > /dev/null 2>&1; then
            end_time=$(date +%s%N)
            duration=$(( (end_time - start_time) / 1000000 ))
            echo -e "${GREEN}✅ Connection successful${NC} (${duration}ms)"
        else
            echo -e "${RED}❌ Connection failed${NC}"
            echo "   Error: Could not connect to PostgreSQL"
            echo "   Check:"
            echo "     - Database server is running"
            echo "     - Connection string is correct"
            echo "     - Network/firewall settings"
            return 1
        fi
    else
        echo -e "${YELLOW}⚠️  psql not available, skipping connection test${NC}"
    fi
    
    # Test using Python if available
    if command -v python3 &> /dev/null; then
        python3 << EOF
import asyncio
import asyncpg
import sys

async def test_connection():
    try:
        conn = await asyncpg.connect("$url")
        result = await conn.fetchval("SELECT 1")
        await conn.close()
        if result == 1:
            print("✅ Async connection successful")
            return 0
    except Exception as e:
        print(f"❌ Async connection failed: {e}")
        return 1

exit(asyncio.run(test_connection()))
EOF
    fi
}

# Test MySQL connection
test_mysql() {
    local url="$1"
    
    echo "Testing MySQL connection..."
    
    if command -v mysql &> /dev/null; then
        # Parse URL for mysql command
        if mysql -e "SELECT 1;" > /dev/null 2>&1; then
            echo -e "${GREEN}✅ Connection successful${NC}"
        else
            echo -e "${RED}❌ Connection failed${NC}"
            return 1
        fi
    else
        echo -e "${YELLOW}⚠️  mysql client not available${NC}"
    fi
}

# Test Redis connection
test_redis() {
    local url="$1"
    
    echo "Testing Redis connection..."
    
    if command -v redis-cli &> /dev/null; then
        # Extract host and port from URL
        local host port
        host=$(echo "$url" | sed -E 's|redis://([^:]+):.*|\1|')
        port=$(echo "$url" | sed -E 's|redis://[^:]+:([0-9]+).*|\1|')
        
        if [ -z "$port" ]; then
            port=6379
        fi
        
        if redis-cli -h "$host" -p "$port" ping | grep -q "PONG"; then
            echo -e "${GREEN}✅ Redis connection successful${NC}"
        else
            echo -e "${RED}❌ Redis connection failed${NC}"
            return 1
        fi
    else
        echo -e "${YELLOW}⚠️  redis-cli not available${NC}"
    fi
}

# Test SQLite connection
test_sqlite() {
    local url="$1"
    
    echo "Testing SQLite connection..."
    
    # Extract path from URL
    local db_path
    db_path=$(echo "$url" | sed -E 's|sqlite:///||' | sed -E 's|sqlite://||')
    
    if [ -f "$db_path" ]; then
        echo -e "${GREEN}✅ Database file exists${NC}"
        
        if command -v sqlite3 &> /dev/null; then
            if sqlite3 "$db_path" "SELECT 1;" > /dev/null 2>&1; then
                echo -e "${GREEN}✅ Connection successful${NC}"
            else
                echo -e "${RED}❌ Connection failed${NC}"
                return 1
            fi
        fi
    else
        echo -e "${YELLOW}⚠️  Database file not found: $db_path${NC}"
        return 1
    fi
}

# Check migrations
check_migrations() {
    echo ""
    echo "Checking migrations..."
    
    if [ "$USE_ALEMBIC" = true ]; then
        if [ -f "alembic.ini" ]; then
            if python3 -m alembic current > /dev/null 2>&1; then
                local current
                current=$(python3 -m alembic current 2>/dev/null | head -1)
                echo "Current revision: $current"
                
                if python3 -m alembic check > /dev/null 2>&1; then
                    echo -e "${GREEN}✅ Migrations up to date${NC}"
                else
                    echo -e "${YELLOW}⚠️  Migrations pending${NC}"
                    echo "   Run: alembic upgrade head"
                fi
            else
                echo -e "${RED}❌ Could not check migrations${NC}"
            fi
        else
            echo -e "${YELLOW}⚠️  alembic.ini not found${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️  Migration check requires --alembic flag${NC}"
    fi
}

# Run test queries
run_test_queries() {
    echo ""
    echo "Running test queries..."
    
    local db_type
    db_type=$(get_db_type "$DATABASE_URL")
    
    case "$db_type" in
        postgresql)
            if command -v psql &> /dev/null; then
                # List tables
                local tables
                tables=$(psql "$DATABASE_URL" -t -c "SELECT tablename FROM pg_tables WHERE schemaname='public';" 2>/dev/null | xargs)
                echo "Tables: $tables"
                
                # Count tables
                local count
                count=$(psql "$DATABASE_URL" -t -c "SELECT COUNT(*) FROM pg_tables WHERE schemaname='public';" 2>/dev/null | xargs)
                echo -e "${GREEN}✅ Found $count tables${NC}"
            fi
            ;;
        sqlite)
            local db_path
            db_path=$(echo "$DATABASE_URL" | sed -E 's|sqlite:///||' | sed -E 's|sqlite://||')
            if command -v sqlite3 &> /dev/null; then
                local tables
                tables=$(sqlite3 "$db_path" ".tables" 2>/dev/null)
                echo "Tables: $tables"
                echo -e "${GREEN}✅ Database accessible${NC}"
            fi
            ;;
        *)
            echo -e "${YELLOW}⚠️  Test queries not implemented for $db_type${NC}"
            ;;
    esac
}

# Test connection pooling
test_connection_pool() {
    echo ""
    echo "Testing connection pool..."
    
    if command -v python3 &> /dev/null; then
        python3 << EOF
import asyncio
import asyncpg
import time

async def test_pool():
    start = time.time()
    connections = []
    
    try:
        # Open multiple connections
        for i in range(5):
            conn = await asyncpg.connect("$DATABASE_URL")
            connections.append(conn)
        
        # Verify all connections work
        for i, conn in enumerate(connections):
            result = await conn.fetchval("SELECT 1")
            if result != 1:
                print(f"❌ Connection {i} failed")
                return 1
        
        # Close all connections
        for conn in connections:
            await conn.close()
        
        elapsed = (time.time() - start) * 1000
        print(f"✅ Pool test passed ({elapsed:.0f}ms for 5 connections)")
        return 0
        
    except Exception as e:
        print(f"❌ Pool test failed: {e}")
        return 1

exit(asyncio.run(test_pool()))
EOF
    else
        echo -e "${YELLOW}⚠️  Python not available for pool test${NC}"
    fi
}

# Performance test
run_performance_test() {
    echo ""
    echo "Running performance tests..."
    
    if command -v python3 &> /dev/null; then
        python3 << EOF
import asyncio
import asyncpg
import time

async def perf_test():
    try:
        conn = await asyncpg.connect("$DATABASE_URL")
        
        # Simple query timing
        start = time.time()
        await conn.fetchval("SELECT 1")
        simple_time = (time.time() - start) * 1000
        print(f"Simple query: {simple_time:.2f}ms")
        
        # Table count timing
        start = time.time()
        await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        tables_time = (time.time() - start) * 1000
        print(f"List tables: {tables_time:.2f}ms")
        
        await conn.close()
        
        if ${MAX_TIME:-0} > 0:
            if simple_time > ${MAX_TIME}:
                print(f"❌ Simple query exceeded ${MAX_TIME}ms")
                return 1
        
        print("✅ Performance test passed")
        return 0
        
    except Exception as e:
        print(f"❌ Performance test failed: {e}")
        return 1

exit(asyncio.run(perf_test()))
EOF
    fi
}

# Main
main() {
    parse_args "$@"
    
    echo "Database Integration Test"
    echo "========================="
    echo "URL: ${DATABASE_URL%%://*}://***@${DATABASE_URL#*@}"
    echo ""
    
    local db_type
    db_type=$(get_db_type "$DATABASE_URL")
    
    # Run connection test based on database type
    case "$db_type" in
        postgresql)
            test_postgresql "$DATABASE_URL"
            ;;
        mysql)
            test_mysql "$DATABASE_URL"
            ;;
        redis)
            test_redis "$DATABASE_URL"
            ;;
        sqlite)
            test_sqlite "$DATABASE_URL"
            ;;
        *)
            echo "Unsupported database type: $db_type"
            exit 1
            ;;
    esac
    
    # Run additional tests
    if [ "$CHECK_MIGRATIONS" = true ]; then
        check_migrations
    fi
    
    if [ "$TEST_QUERIES" = true ]; then
        run_test_queries
    fi
    
    if [ "$TEST_CONNECTION_POOL" = true ]; then
        test_connection_pool
    fi
    
    if [ "$PERFORMANCE_TEST" = true ]; then
        run_performance_test
    fi
    
    echo ""
    echo "Database tests complete."
}

main "$@"
