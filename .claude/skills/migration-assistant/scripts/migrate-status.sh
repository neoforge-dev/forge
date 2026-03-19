#!/usr/bin/env bash
# Cross-Project Migration Status
# Shows migration status across FORGE projects

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Defaults
PROJECT=""
DOMAIN=""
ALL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --project|-p)
            PROJECT="$2"
            shift 2
            ;;
        --domain|-d)
            DOMAIN="$2"
            shift 2
            ;;
        --all|-a)
            ALL=true
            shift
            ;;
        --help|-h)
            echo "Usage: migrate-status.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --project, -p     Show status for specific project"
            echo "  --domain, -d      Show status for domain"
            echo "  --all, -a         Show all FORGE projects"
            echo "  --help, -h        Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Get FORGE root
FORGE_ROOT="/Users/moltbot/work/FORGE"

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  FORGE Migration Status${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Function to check project status
check_project() {
    local project_dir="$1"
    local project_name=$(basename "$project_dir")
    
    # Find alembic
    local alembic_dir=""
    for subdir in . backend app/api; do
        if [[ -f "$project_dir/$subdir/alembic.ini" ]]; then
            alembic_dir="$project_dir/$subdir"
            break
        fi
    done
    
    if [[ -z "$alembic_dir" ]]; then
        echo -e "  ${YELLOW}$project_name${NC}: No migrations found"
        return
    fi
    
    # Get current revision
    local current=$(cd "$alembic_dir" && alembic current 2>/dev/null | awk '{print $1}' | head -1 || echo "unknown")
    local heads=$(cd "$alembic_dir" && alembic heads 2>/dev/null | awk '{print $1}' | head -1 || echo "unknown")
    
    if [[ "$current" == "$heads" ]]; then
        echo -e "  ${GREEN}$project_name${NC}: Up to date @ ${CYAN}$current${NC}"
    else
        # Count revisions behind
        local behind=$(cd "$alembic_dir" && alembic history 2>/dev/null | grep -c "^" || echo "?")
        echo -e "  ${YELLOW}$project_name${NC}: Behind by revisions @ ${CYAN}$current${NC}"
    fi
}

# Single project mode
if [[ -n "$PROJECT" ]]; then
    PROJECT_DIR="$FORGE_ROOT/$PROJECT"
    if [[ ! -d "$PROJECT_DIR" ]]; then
        echo -e "${RED}Error: Project not found: $PROJECT${NC}"
        exit 1
    fi
    
    check_project "$PROJECT_DIR"
    exit 0
fi

# Domain mode
if [[ -n "$DOMAIN" ]]; then
    echo -e "${CYAN}Domain: $DOMAIN${NC}"
    echo ""
    
    # Find projects in domain
    for project_dir in "$FORGE_ROOT"/*; do
        if [[ -d "$project_dir" ]]; then
            # Check if project belongs to domain (simple heuristic: check CLAUDE.md)
            if [[ -f "$project_dir/CLAUDE.md" ]]; then
                if grep -q "domain.*$DOMAIN\|$DOMAIN.*domain" "$project_dir/CLAUDE.md" 2>/dev/null; then
                    check_project "$project_dir"
                fi
            fi
        fi
    done
    exit 0
fi

# All projects mode
if [[ "$ALL" == true ]]; then
    echo -e "${CYAN}All FORGE Projects:${NC}"
    echo ""
    
    for project_dir in "$FORGE_ROOT"/*; do
        if [[ -d "$project_dir" ]]; then
            project_name=$(basename "$project_dir")
            # Skip non-project directories
            if [[ "$project_name" =~ ^\.|^docs$|^harness$|^scripts$ ]]; then
                continue
            fi
            check_project "$project_dir"
        fi
    done
    exit 0
fi

# Default: current project
echo -e "${CYAN}Current Project:${NC}"
echo ""

# Check current directory
if [[ -f "alembic.ini" ]]; then
    alembic current 2>/dev/null || echo "  Could not determine status"
    echo ""
    alembic history --verbose 2>/dev/null | head -10 || true
else
    # Try subdirectories
    FOUND=false
    for subdir in backend app/api; do
        if [[ -f "$subdir/alembic.ini" ]]; then
            echo -e "${CYAN}Found in $subdir/:${NC}"
            (cd "$subdir" && alembic current 2>/dev/null)
            echo ""
            (cd "$subdir" && alembic history --verbose 2>/dev/null | head -10 || true)
            FOUND=true
            break
        fi
    done
    
    if [[ "$FOUND" == false ]]; then
        echo -e "${YELLOW}No alembic configuration found in current directory${NC}"
        echo ""
        echo "Use --project, --domain, or --all to check other projects"
    fi
fi

echo ""
echo -e "${BLUE}Commands:${NC}"
echo "  /migrate status --project <name>    Check specific project"
echo "  /migrate status --domain <domain>   Check domain projects"
echo "  /migrate status --all               Check all FORGE projects"
