#!/usr/bin/env bash
# Migration Validation Script
# Detects breaking changes and migration safety issues

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Defaults
REVISION="head"
STRICT=false
REPORT=""
SILENT=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --revision|-r)
            REVISION="$2"
            shift 2
            ;;
        --strict)
            STRICT=true
            shift
            ;;
        --report)
            REPORT="$2"
            shift 2
            ;;
        --silent)
            SILENT=true
            shift
            ;;
        --help|-h)
            echo "Usage: migrate-validate.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --revision, -r    Revision to validate (default: head)"
            echo "  --strict          Treat warnings as errors"
            echo "  --report FILE     Save report to file"
            echo "  --silent          Minimal output"
            echo "  --help, -h        Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Find alembic
find_alembic() {
    if [[ -f "alembic.ini" ]]; then
        return 0
    fi
    for dir in backend app/api; do
        if [[ -f "$dir/alembic.ini" ]]; then
            cd "$dir"
            return 0
        fi
    done
    return 1
}

find_alembic || { echo -e "${RED}Error: alembic.ini not found${NC}"; exit 1; }

# Initialize report
REPORT_CONTENT=""
add_to_report() {
    REPORT_CONTENT+="$1\n"
}

log() {
    if [[ "$SILENT" == false ]]; then
        echo -e "$1"
    fi
    add_to_report "$1"
}

if [[ "$SILENT" == false ]]; then
    log "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    log "${BLUE}  Migration Validation${NC}"
    log "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    log ""
fi

# Get migration file for revision
MIGRATION_FILE=$(alembic history --rev-range "${REVISION}~1:$REVISION" 2>/dev/null | grep -oE 'versions/[a-f0-9_]+\.py' | tail -1 || true)

if [[ -z "$MIGRATION_FILE" ]]; then
    log "${RED}Error: Could not find migration file for revision $REVISION${NC}"
    exit 1
fi

MIGRATION_PATH="alembic/$MIGRATION_FILE"

if [[ ! -f "$MIGRATION_PATH" ]]; then
    log "${RED}Error: Migration file not found: $MIGRATION_PATH${NC}"
    exit 1
fi

log "${BLUE}Validating:${NC} $MIGRATION_FILE"
log ""

# Track issues
CRITICAL_COUNT=0
WARNING_COUNT=0

# Function to report issue
report_issue() {
    local level="$1"
    local issue="$2"
    local recommendation="$3"
    
    if [[ "$level" == "CRITICAL" ]]; then
        log "${RED}[CRITICAL] $issue${NC}"
        ((CRITICAL_COUNT++)) || true
    else
        log "${YELLOW}[WARNING] $issue${NC}"
        ((WARNING_COUNT++)) || true
    fi
    
    if [[ -n "$recommendation" ]]; then
        log "  → $recommendation"
    fi
    log ""
}

# Check for column drops (CRITICAL)
if grep -q "drop_column" "$MIGRATION_PATH"; then
    DROPPED_COLS=$(grep -E "drop_column\s*\(" "$MIGRATION_PATH" | sed 's/.*drop_column(/  /' | sed 's/).*//' || true)
    report_issue "CRITICAL" "Column drop detected" "Data loss risk - ensure data is backed up or migrated"
    log "  Dropping: $DROPPED_COLS"
fi

# Check for table drops (CRITICAL)
if grep -q "drop_table" "$MIGRATION_PATH"; then
    DROPPED_TABLES=$(grep -E "drop_table\s*\(" "$MIGRATION_PATH" | sed 's/.*drop_table(/  /' | sed 's/).*//' || true)
    report_issue "CRITICAL" "Table drop detected" "Complete data loss - verify backup exists"
    log "  Dropping: $DROPPED_TABLES"
fi

# Check for type changes (WARNING)
if grep -qE "alter.*type|Column.*\(.*type_" "$MIGRATION_PATH"; then
    report_issue "WARNING" "Column type change detected" "May cause data truncation - verify compatibility"
fi

# Check for NOT NULL additions (WARNING)
if grep -qE "nullable\s*=\s*False|nullable=False" "$MIGRATION_PATH"; then
    report_issue "WARNING" "NOT NULL constraint added" "May fail on existing NULL values - add default or backfill first"
fi

# Check for unique constraint additions (WARNING)
if grep -qE "create_unique_constraint|UniqueConstraint" "$MIGRATION_PATH"; then
    report_issue "WARNING" "Unique constraint added" "May fail on duplicate values - verify data uniqueness"
fi

# Check for index drops (WARNING)
if grep -q "drop_index" "$MIGRATION_PATH"; then
    report_issue "WARNING" "Index drop detected" "May impact query performance - verify usage"
fi

# Check for foreign key changes (WARNING)
if grep -qE "create_foreign_key|drop_constraint.*foreignkey|ForeignKeyConstraint" "$MIGRATION_PATH"; then
    report_issue "WARNING" "Foreign key change detected" "Check referential integrity constraints"
fi

# Check for data migrations (WARNING)
if grep -qE "execute\(.*insert|execute\(.*update|execute\(.*delete" "$MIGRATION_PATH"; then
    report_issue "WARNING" "Data migration detected" "Test thoroughly with production-like data volume"
fi

# Check for missing downgrade (CRITICAL in strict mode)
DOWNGRADE_LINE=$(grep -n "def downgrade" "$MIGRATION_PATH" | head -1 | cut -d: -f1 || true)
if [[ -n "$DOWNGRADE_LINE" ]]; then
    # Check if downgrade just has pass
    DOWNGRADE_CONTENT=$(sed -n "$((DOWNGRADE_LINE + 1)),/^def /p" "$MIGRATION_PATH" | head -20 || true)
    if echo "$DOWNGRADE_CONTENT" | grep -q "^\s*pass\s*$"; then
        if [[ "$STRICT" == true ]]; then
            report_issue "CRITICAL" "Downgrade not implemented (pass only)" "Implement proper downgrade for rollback capability"
        else
            report_issue "WARNING" "Downgrade not implemented (pass only)" "Implement proper downgrade for rollback capability"
        fi
    fi
else
    report_issue "CRITICAL" "Missing downgrade function" "Add downgrade() function"
fi

# Check migration size
MIGRATION_LINES=$(wc -l < "$MIGRATION_PATH")
if [[ $MIGRATION_LINES -gt 150 ]]; then
    report_issue "WARNING" "Large migration ($MIGRATION_LINES lines)" "Consider splitting into multiple migrations"
fi

# Summary
log "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
log "${BLUE}  Validation Summary${NC}"
log "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
log ""

if [[ $CRITICAL_COUNT -gt 0 ]]; then
    log "${RED}Critical issues: $CRITICAL_COUNT${NC}"
fi

if [[ $WARNING_COUNT -gt 0 ]]; then
    log "${YELLOW}Warnings: $WARNING_COUNT${NC}"
fi

if [[ $CRITICAL_COUNT -eq 0 && $WARNING_COUNT -eq 0 ]]; then
    log "${GREEN}✓ No issues detected${NC}"
fi

log ""

# Exit code
if [[ $CRITICAL_COUNT -gt 0 ]]; then
    RESULT="${RED}FAILED${NC}"
    EXIT_CODE=1
elif [[ $WARNING_COUNT -gt 0 && "$STRICT" == true ]]; then
    RESULT="${YELLOW}FAILED (strict mode)${NC}"
    EXIT_CODE=1
else
    RESULT="${GREEN}PASSED${NC}"
    EXIT_CODE=0
fi

log "Result: $RESULT"

# Save report if requested
if [[ -n "$REPORT" ]]; then
    echo -e "$REPORT_CONTENT" > "$REPORT"
    log ""
    log "Report saved to: $REPORT"
fi

exit $EXIT_CODE
