#!/usr/bin/env bash
# Migration Generator Script
# Generates Alembic migrations with validation

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Defaults
DESCRIPTION=""
PROJECT=""
AUTO=false
DRY_RUN=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --description|-d)
            DESCRIPTION="$2"
            shift 2
            ;;
        --project|-p)
            PROJECT="$2"
            shift 2
            ;;
        --auto|-a)
            AUTO=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            echo "Usage: migrate-generate.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --description, -d    Migration description"
            echo "  --project, -p        Project directory (default: current)"
            echo "  --auto, -a           Skip interactive prompts"
            echo "  --dry-run            Show what would be generated"
            echo "  --help, -h           Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Validate required args
if [[ -z "$DESCRIPTION" ]]; then
    echo -e "${RED}Error: --description is required${NC}"
    exit 1
fi

# Change to project directory if specified
if [[ -n "$PROJECT" ]]; then
    cd "$PROJECT" || { echo -e "${RED}Error: Cannot cd to $PROJECT${NC}"; exit 1; }
fi

# Find alembic.ini
if [[ ! -f "alembic.ini" ]]; then
    # Check common subdirectories
    for dir in backend app/api; do
        if [[ -f "$dir/alembic.ini" ]]; then
            cd "$dir"
            break
        fi
    done
fi

if [[ ! -f "alembic.ini" ]]; then
    echo -e "${RED}Error: alembic.ini not found${NC}"
    exit 1
fi

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Migration Generator${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Get current revision
echo -e "${BLUE}Current revision:${NC}"
alembic current 2>/dev/null || echo "  (no migrations yet)"
echo ""

# Check for model changes
echo -e "${BLUE}Checking for model changes...${NC}"

# Run autogenerate in dry-run mode first
if [[ "$DRY_RUN" == true ]]; then
    echo -e "${YELLOW}DRY RUN - No changes will be made${NC}"
    alembic revision --autogenerate -m "$DESCRIPTION" --sql 2>/dev/null | head -50 || true
    exit 0
fi

# Generate migration
MIGRATION_OUTPUT=$(alembic revision --autogenerate -m "$DESCRIPTION" 2>&1)
MIGRATION_RESULT=$?

if [[ $MIGRATION_RESULT -ne 0 ]]; then
    echo -e "${RED}Error generating migration:${NC}"
    echo "$MIGRATION_OUTPUT"
    exit 1
fi

# Extract migration file path
MIGRATION_FILE=$(echo "$MIGRATION_OUTPUT" | grep -oE 'versions/[a-f0-9_]+\.py' | head -1)

if [[ -z "$MIGRATION_FILE" ]]; then
    echo -e "${YELLOW}No changes detected in models${NC}"
    echo ""
    echo "Tips:"
    echo "  - Ensure models are imported in alembic/env.py"
    echo "  - Check that your model changes are saved"
    echo "  - Run 'alembic history' to see existing migrations"
    exit 0
fi

MIGRATION_PATH="alembic/$MIGRATION_FILE"
echo -e "${GREEN}✓ Generated: $MIGRATION_PATH${NC}"
echo ""

# Display migration content
echo -e "${BLUE}Migration content:${NC}"
echo "---"
head -60 "$MIGRATION_PATH"
echo "---"
echo ""

# Run validation
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALIDATION_OUTPUT=$("$SCRIPT_DIR/migrate-validate.sh" --revision head --silent 2>&1) || true

# Check for breaking changes
if echo "$VALIDATION_OUTPUT" | grep -q "CRITICAL"; then
    echo -e "${RED}⚠️  CRITICAL: Breaking changes detected!${NC}"
    echo ""
    echo "$VALIDATION_OUTPUT" | grep -A5 "CRITICAL"
    echo ""
    echo -e "${YELLOW}Review required before proceeding.${NC}"
    
    if [[ "$AUTO" == false ]]; then
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            # Remove the generated migration
            rm "$MIGRATION_PATH"
            echo -e "${YELLOW}Migration removed. Address breaking changes first.${NC}"
            exit 1
        fi
    fi
elif echo "$VALIDATION_OUTPUT" | grep -q "WARNING"; then
    echo -e "${YELLOW}⚠️  WARNING: Potential issues detected${NC}"
    echo ""
    echo "$VALIDATION_OUTPUT" | grep -A3 "WARNING"
    echo ""
fi

# Show recommendations
echo -e "${BLUE}Recommendations:${NC}"

# Check if downgrade is implemented
if grep -q "pass" "$MIGRATION_PATH" | grep -A10 "def downgrade"; then
    echo -e "  ${YELLOW}• Implement downgrade() before deploying${NC}"
else
    echo -e "  ${GREEN}• Downgrade appears to be implemented${NC}"
fi

# Check migration size
MIGRATION_LINES=$(wc -l < "$MIGRATION_PATH")
if [[ $MIGRATION_LINES -gt 100 ]]; then
    echo -e "  ${YELLOW}• Large migration ($MIGRATION_LINES lines) - consider splitting${NC}"
fi

# Check for data migrations
if grep -q "execute\|insert\|update\|delete" "$MIGRATION_PATH"; then
    echo -e "  ${YELLOW}• Data migration detected - test with copy of production data${NC}"
fi

echo ""
echo -e "${BLUE}Next steps:${NC}"
echo "  1. Review:         cat $MIGRATION_PATH"
echo "  2. Validate:       /migrate validate"
echo "  3. Test:           /migrate test --revision head"
echo "  4. Preview rollback: /migrate rollback --revision head --preview"
echo ""
echo -e "${GREEN}Migration generated successfully!${NC}"
