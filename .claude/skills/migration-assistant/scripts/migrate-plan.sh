#!/usr/bin/env bash
# Data Migration Planning Script
# Generates strategy for large table migrations

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Defaults
TABLE=""
ROWS=""
STRATEGY=""
OUTPUT=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --table|-t)
            TABLE="$2"
            shift 2
            ;;
        --rows|-r)
            ROWS="$2"
            shift 2
            ;;
        --strategy|-s)
            STRATEGY="$2"
            shift 2
            ;;
        --output|-o)
            OUTPUT="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: migrate-plan.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --table, -t       Table name to migrate"
            echo "  --rows, -r        Estimated row count"
            echo "  --strategy, -s    Migration strategy (batch|cte|temp)"
            echo "  --output, -o      Output file for plan"
            echo "  --help, -h        Show this help"
            echo ""
            echo "Strategies:"
            echo "  batch  - Batch updates for < 1M rows"
            echo "  cte    - CTE-based for 1M-10M rows"
            echo "  temp   - Temp table swap for > 10M rows"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Validate required args
if [[ -z "$TABLE" ]]; then
    echo -e "${RED}Error: --table is required${NC}"
    exit 1
fi

if [[ -z "$ROWS" ]]; then
    echo -e "${RED}Error: --rows is required${NC}"
    exit 1
fi

# Convert rows to number
ROWS_NUM=$(echo "$ROWS" | tr -d ',M' | awk '{print $1}')
if [[ "$ROWS" == *"M"* ]]; then
    ROWS_NUM=$(echo "$ROWS_NUM * 1000000" | bc | cut -d. -f1)
fi

# Determine strategy if not specified
if [[ -z "$STRATEGY" ]]; then
    if [[ $ROWS_NUM -lt 1000000 ]]; then
        STRATEGY="batch"
    elif [[ $ROWS_NUM -lt 10000000 ]]; then
        STRATEGY="cte"
    else
        STRATEGY="temp"
    fi
fi

# Generate plan
PLAN="# Data Migration Plan
## Table: \`$TABLE\`

### Migration Parameters
- **Estimated Rows:** $(printf "%'d" $ROWS_NUM)
- **Selected Strategy:** $STRATEGY
- **Generated:** $(date -u +"%Y-%m-%d %H:%M UTC")

### Strategy Overview

"

case $STRATEGY in
    batch)
        PLAN+="**Batch Processing** (Recommended for < 1M rows)

This strategy processes rows in small batches to minimize lock contention.

#### Steps

1. **Add new column(s)**
   \`\`\`sql
   ALTER TABLE $TABLE ADD COLUMN new_field TYPE;
   \`\`\`
   - Duration: ~1 second
   - Lock: Brief ACCESS EXCLUSIVE

2. **Create index on filter column** (if needed)
   \`\`\`sql
   CREATE INDEX CONCURRENTLY idx_${TABLE}_filter ON $TABLE(filter_column) WHERE condition;
   \`\`\`
   - Duration: Varies by data
   - Lock: None (concurrent)

3. **Backfill in batches**
   \`\`\`python
   BATCH_SIZE = 10000
   while True:
       result = conn.execute(\"\"\"
           UPDATE $TABLE 
           SET new_field = transform(old_field)
           WHERE id IN (
               SELECT id FROM $TABLE 
               WHERE new_field IS NULL
               LIMIT :batch_size
           )
           RETURNING id
       \"\"\", {\"batch_size\": BATCH_SIZE})
       
       if result.rowcount == 0:
           break
       # Sleep briefly to reduce load
       time.sleep(0.1)
   \`\`\`
   - Duration: ~$(( ROWS_NUM / 10000 * 2 / 60 )) minutes (estimated)
   - Lock: Row-level only

4. **Add constraints/indexes**
   \`\`\`sql
   ALTER TABLE $TABLE ALTER COLUMN new_field SET NOT NULL;
   CREATE INDEX CONCURRENTLY idx_${TABLE}_new ON $TABLE(new_field);
   \`\`\`

#### Estimated Timeline
- Column addition: 1 second
- Backfill: $(( ROWS_NUM / 10000 * 2 / 60 )) minutes
- Index creation: 5-30 minutes (concurrent)
- **Total: ~$(( ROWS_NUM / 10000 * 2 / 60 + 30 )) minutes**

#### Rollback
\`\`\`sql
-- Remove new column
ALTER TABLE $TABLE DROP COLUMN IF EXISTS new_field;
\`\`\`
Duration: ~1 second
"
        ;;
    
    cte)
        PLAN+="**CTE-Based Processing** (Recommended for 1M-10M rows)

Uses Common Table Expressions for efficient bulk updates with progress tracking.

#### Steps

1. **Add new column(s)**
   \`\`\`sql
   ALTER TABLE $TABLE ADD COLUMN new_field TYPE;
   \`\`\`

2. **Process in CTE batches**
   \`\`\`sql
   WITH batch AS (
       SELECT id 
       FROM $TABLE 
       WHERE new_field IS NULL
       LIMIT 50000
   )
   UPDATE $TABLE t
   SET new_field = transform(t.old_field)
   FROM batch b
   WHERE t.id = b.id;
   \`\`\`
   - Run repeatedly until 0 rows affected
   - Duration: ~$(( ROWS_NUM / 50000 * 5 / 60 )) minutes (estimated)

3. **Verify completion**
   \`\`\`sql
   SELECT COUNT(*) FROM $TABLE WHERE new_field IS NULL;
   -- Should return 0
   \`\`\`

4. **Add final constraints**
   \`\`\`sql
   ALTER TABLE $TABLE ALTER COLUMN new_field SET NOT NULL;
   CREATE INDEX CONCURRENTLY idx_${TABLE}_new ON $TABLE(new_field);
   \`\`\`

#### Monitoring Query
\`\`\`sql
SELECT 
    COUNT(*) FILTER (WHERE new_field IS NULL) as pending,
    COUNT(*) FILTER (WHERE new_field IS NOT NULL) as completed,
    ROUND(100.0 * COUNT(*) FILTER (WHERE new_field IS NOT NULL) / COUNT(*), 2) as pct_complete
FROM $TABLE;
\`\`\`

#### Estimated Timeline
- Column addition: 1 second
- Backfill: $(( ROWS_NUM / 50000 * 5 / 60 )) minutes
- Constraints: 10-30 minutes
- **Total: ~$(( ROWS_NUM / 50000 * 5 / 60 + 30 )) minutes**

#### Rollback
\`\`\`sql
-- Cancel any running backfill
-- Drop column
ALTER TABLE $TABLE DROP COLUMN IF EXISTS new_field;
\`\`\`
"
        ;;
    
    temp)
        PLAN+="**Temp Table Swap** (Recommended for > 10M rows, zero downtime)

Creates new table structure, migrates data, then atomically swaps.

#### Steps

1. **Create new table structure**
   \`\`\`sql
   CREATE TABLE ${TABLE}_new (LIKE $TABLE INCLUDING ALL);
   -- Add/modify columns as needed
   ALTER TABLE ${TABLE}_new ADD COLUMN new_field TYPE;
   \`\`\`

2. **Set up replication trigger** (if near-zero downtime required)
   \`\`\`sql
   CREATE OR REPLACE FUNCTION sync_${TABLE}_new()
   RETURNS TRIGGER AS \$\$
   BEGIN
       IF TG_OP = 'INSERT' THEN
           INSERT INTO ${TABLE}_new VALUES (NEW.*);
           RETURN NEW;
       ELSIF TG_OP = 'UPDATE' THEN
           UPDATE ${TABLE}_new SET = NEW.* WHERE id = NEW.id;
           RETURN NEW;
       ELSIF TG_OP = 'DELETE' THEN
           DELETE FROM ${TABLE}_new WHERE id = OLD.id;
           RETURN OLD;
       END IF;
   END;
   \$\$ LANGUAGE plpgsql;
   
   CREATE TRIGGER ${TABLE}_sync_trigger
   AFTER INSERT OR UPDATE OR DELETE ON $TABLE
   FOR EACH ROW EXECUTE FUNCTION sync_${TABLE}_new();
   \`\`\`

3. **Backfill existing data in chunks**
   \`\`\`sql
   INSERT INTO ${TABLE}_new 
   SELECT *, transform(old_field) as new_field
   FROM $TABLE
   WHERE id BETWEEN :start AND :end;
   \`\`\`
   - Process in 100K-500K row chunks
   - Duration: ~$(( ROWS_NUM / 100000 * 10 / 60 )) minutes (estimated)

4. **Verify data consistency**
   \`\`\`sql
   SELECT COUNT(*) FROM $TABLE;
   SELECT COUNT(*) FROM ${TABLE}_new;
   -- Should match
   \`\`\`

5. **Atomic swap**
   \`\`\`sql
   BEGIN;
   -- Brief lock window starts here
   ALTER TABLE $TABLE RENAME TO ${TABLE}_old;
   ALTER TABLE ${TABLE}_new RENAME TO $TABLE;
   COMMIT;
   -- Lock released
   \`\`\`
   - Duration: < 1 second
   - Lock: Brief ACCESS EXCLUSIVE

6. **Cleanup**
   \`\`\`sql
   DROP TABLE ${TABLE}_old;
   -- Or keep for 24h as safety
   -- DROP TRIGGER ${TABLE}_sync_trigger ON $TABLE;
   \`\`\`

#### Pre-Swap Checklist
- [ ] Row counts match: \`SELECT COUNT(*)\` on both tables
- [ ] Sample data verified
- [ ] Application ready to reconnect
- [ ] Rollback plan tested
- [ ] Maintenance window announced (if applicable)

#### Estimated Timeline
- Setup: 5 minutes
- Backfill: $(( ROWS_NUM / 100000 * 10 / 60 )) minutes
- Verification: 10 minutes
- Swap: < 1 second
- Cleanup: 1 minute
- **Total: ~$(( ROWS_NUM / 100000 * 10 / 60 + 20 )) minutes (mostly backfill)**

#### Rollback
\`\`\`sql
-- If swap not yet done:
DROP TABLE ${TABLE}_new;

-- If swap done but issue detected:
BEGIN;
ALTER TABLE $TABLE RENAME TO ${TABLE}_new;
ALTER TABLE ${TABLE}_old RENAME TO $TABLE;
COMMIT;
\`\`\`
"
        ;;
esac

PLAN+="

### Safety Recommendations

1. **Always backup before starting**
   \`\`\`bash
   pg_dump -t $TABLE \$DATABASE_URL > backup_${TABLE}_$(date +%Y%m%d).sql
   \`\`\`

2. **Test on copy first**
   - Restore backup to staging
   - Run full migration
   - Verify application works

3. **Monitor during migration**
   \`\`\`sql
   -- Check locks
   SELECT * FROM pg_locks WHERE NOT granted;
   
   -- Check long queries
   SELECT * FROM pg_stat_activity WHERE state = 'active' AND query LIKE '%$TABLE%';
   \`\`\`

4. **Have rollback ready**
   - Document exact rollback steps
   - Test rollback procedure
   - Set rollback trigger criteria

### Post-Migration Verification

\`\`\`sql
-- Row count
SELECT COUNT(*) FROM $TABLE;

-- Check for nulls in required columns
SELECT COUNT(*) FROM $TABLE WHERE new_field IS NULL;

-- Sample data spot check
SELECT * FROM $TABLE ORDER BY RANDOM() LIMIT 10;
\`\`\`

---
*Generated by FORGE Migration Assistant*
"

# Output plan
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Data Migration Plan${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${CYAN}Table:${NC} $TABLE"
echo -e "${CYAN}Rows:${NC} $(printf "%'d" $ROWS_NUM)"
echo -e "${CYAN}Strategy:${NC} $STRATEGY"
echo ""

# Print summary
case $STRATEGY in
    batch)
        echo -e "${GREEN}✓ Recommended for smaller tables (< 1M rows)${NC}"
        echo "  • Simple batch processing"
        echo "  • Brief locks during schema changes"
        echo "  • ~$(( ROWS_NUM / 10000 * 2 / 60 + 30 )) minutes estimated"
        ;;
    cte)
        echo -e "${GREEN}✓ Recommended for medium tables (1M-10M rows)${NC}"
        echo "  • CTE-based batch processing"
        echo "  • Good progress visibility"
        echo "  • ~$(( ROWS_NUM / 50000 * 5 / 60 + 30 )) minutes estimated"
        ;;
    temp)
        echo -e "${GREEN}✓ Recommended for large tables (> 10M rows)${NC}"
        echo "  • Near-zero downtime with temp table swap"
        echo "  • Replication trigger keeps data in sync"
        echo "  • ~$(( ROWS_NUM / 100000 * 10 / 60 + 20 )) minutes estimated"
        ;;
esac

echo ""

# Save to file or output
if [[ -n "$OUTPUT" ]]; then
    echo -e "$PLAN" > "$OUTPUT"
    echo -e "${GREEN}✓ Plan saved to: $OUTPUT${NC}"
else
    echo -e "$PLAN"
fi

echo ""
echo -e "${BLUE}Next steps:${NC}"
echo "  1. Review the generated plan"
echo "  2. Test on copy of production data"
echo "  3. Schedule migration window if needed"
echo "  4. Execute with monitoring"
