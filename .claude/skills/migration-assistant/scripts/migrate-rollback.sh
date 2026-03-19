#!/usr/bin/env bash
# Rollback Procedure Generator
# Creates detailed rollback steps for migrations

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Defaults
REVISION=""
PREVIEW=false
OUTPUT=""
EXECUTE=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --revision|-r)
            REVISION="$2"
            shift 2
            ;;
        --preview)
            PREVIEW=true
            shift
            ;;
        --output|-o)
            OUTPUT="$2"
            shift 2
            ;;
        --execute)
            EXECUTE=true
            shift
            ;;
        --help|-h)
            echo "Usage: migrate-rollback.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --revision, -r    Revision to rollback to"
            echo "  --preview         Show SQL without executing"
            echo "  --output, -o      Save procedure to file"
            echo "  --execute         Actually perform rollback (use with caution)"
            echo "  --help, -h        Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Validate required args
if [[ -z "$REVISION" ]]; then
    echo -e "${RED}Error: --revision is required${NC}"
    exit 1
fi

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

# Get current and target revisions
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Rollback Procedure Generator${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

CURRENT_REV=$(alembic current 2>/dev/null | awk '{print $1}' | head -1 || echo "unknown")
echo -e "${CYAN}Current revision:${NC} $CURRENT_REV"

# Resolve revision
if [[ "$REVISION" == "head" ]]; then
    TARGET_REV=$(alembic heads 2>/dev/null | awk '{print $1}' | head -1 || echo "unknown")
    echo -e "${CYAN}Target revision:${NC} $TARGET_REV (head)"
    echo -e "${YELLOW}Note: Rolling back from head to previous${NC}"
    REVISION="-1"
else
    TARGET_REV="$REVISION"
    echo -e "${CYAN}Target revision:${NC} $TARGET_REV"
fi

echo ""

# Generate rollback procedure
PROCEDURE="# Database Rollback Procedure
## $(date -u +"%Y-%m-%d %H:%M UTC")

### Rollback Details
- **From:** $CURRENT_REV
- **To:** $TARGET_REV
- **Generated:** $(date)

### Pre-Rollback Checklist

- [ ] **Backup current state**
  \`\`\`bash
  pg_dump \$DATABASE_URL > pre_rollback_backup_$(date +%Y%m%d_%H%M%S).sql
  \`\`\`

- [ ] **Verify backup integrity**
  \`\`\`bash
  pg_restore --list pre_rollback_backup_*.sql | tail -5
  \`\`\`

- [ ] **Check for active connections**
  \`\`\`sql
  SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();
  \`\`\`
  Ensure low activity before proceeding.

- [ ] **Notify stakeholders** (if production)

### Rollback Steps

#### Step 1: Preview Changes (Recommended)

\`\`\`bash
# Generate SQL without executing
alembic downgrade $REVISION --sql > rollback_preview.sql
cat rollback_preview.sql
\`\`\`

Review the generated SQL carefully before proceeding.

#### Step 2: Execute Rollback

\`\`\`bash
# Method A: Using Alembic (recommended)
alembic downgrade $REVISION

# Method B: Manual SQL (if Alembic fails)
# See rollback_preview.sql generated above
\`\`\`

#### Step 3: Verify Rollback

\`\`\`bash
# Check current revision
alembic current

# Should show: $TARGET_REV
\`\`\`

### Post-Rollback Verification

\`\`\`sql
-- Verify table structures
\d table_name

-- Check row counts
SELECT count(*) FROM important_table;

-- Verify constraints
SELECT conname, contype FROM pg_constraint WHERE conrelid = 'table_name'::regclass;
\`\`\`

### Emergency Procedures

#### If Rollback Hangs

1. **Check for locks:**
   \`\`\`sql
   SELECT * FROM pg_locks WHERE NOT granted;
   \`\`\`

2. **Cancel long-running queries:**
   \`\`\`sql
   SELECT pg_cancel_backend(pid) FROM pg_stat_activity 
   WHERE state = 'active' AND pid <> pg_backend_pid();
   \`\`\`

3. **Force terminate if needed:**
   \`\`\`sql
   SELECT pg_terminate_backend(pid) FROM pg_stat_activity 
   WHERE state = 'active' AND pid <> pg_backend_pid();
   \`\`\`

#### If Rollback Fails

1. **Check error log**
2. **Restore from backup:**
   \`\`\`bash
   pg_restore -d \$DATABASE_URL pre_rollback_backup_*.sql
   \`\`\`
3. **Contact database admin if needed**

### Rollback Recovery

To undo this rollback (re-apply the migration):

\`\`\`bash
alembic upgrade $CURRENT_REV
\`\`\`

---

## Quick Reference

\`\`\`bash
# Preview rollback SQL
alembic downgrade $REVISION --sql

# Execute rollback
alembic downgrade $REVISION

# Check status
alembic current
alembic history
\`\`\`
"

# Preview mode
if [[ "$PREVIEW" == true ]]; then
    echo -e "${YELLOW}Preview Mode - Generating rollback SQL...${NC}"
    echo ""
    
    SQL_PREVIEW=$(alembic downgrade "$REVISION" --sql 2>&1) || {
        echo -e "${RED}Error generating preview:${NC}"
        echo "$SQL_PREVIEW"
        exit 1
    }
    
    echo -e "${CYAN}Rollback SQL Preview:${NC}"
    echo "---"
    echo "$SQL_PREVIEW"
    echo "---"
    echo ""
    echo -e "${YELLOW}Review the SQL above before executing rollback${NC}"
    exit 0
fi

# Execute mode
if [[ "$EXECUTE" == true ]]; then
    echo -e "${RED}⚠️  EXECUTE MODE - This will rollback the database!${NC}"
    echo ""
    echo -e "Current: ${YELLOW}$CURRENT_REV${NC}"
    echo -e "Target:  ${YELLOW}$TARGET_REV${NC}"
    echo ""
    
    read -p "Are you sure? Type 'rollback' to confirm: " CONFIRM
    if [[ "$CONFIRM" != "rollback" ]]; then
        echo -e "${YELLOW}Rollback cancelled${NC}"
        exit 0
    fi
    
    echo -e "${BLUE}Executing rollback...${NC}"
    alembic downgrade "$REVISION" || {
        echo -e "${RED}Rollback failed!${NC}"
        exit 1
    }
    
    echo -e "${GREEN}✓ Rollback complete${NC}"
    echo ""
    alembic current
    exit 0
fi

# Output procedure
echo -e "${CYAN}Rollback Procedure:${NC}"
echo ""

if [[ -n "$OUTPUT" ]]; then
    echo "$PROCEDURE" > "$OUTPUT"
    echo -e "${GREEN}✓ Procedure saved to: $OUTPUT${NC}"
else
    echo "$PROCEDURE"
fi

echo ""
echo -e "${BLUE}Next steps:${NC}"
echo "  1. Review the rollback procedure"
echo "  2. Create backup: pg_dump \$DATABASE_URL > backup.sql"
echo "  3. Preview SQL:   alembic downgrade $REVISION --sql"
echo "  4. Execute:       alembic downgrade $REVISION"
