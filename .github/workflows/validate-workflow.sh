#!/bin/bash
# Workflow Validation Script
# Usage: ./validate-workflow.sh <workflow-file>

set -e

WORKFLOW_FILE="${1:-.github/workflows/*.yml}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================="
echo "GitHub Actions Workflow Validator"
echo "========================================="
echo ""

# Function to check if file exists
check_file() {
    local file=$1
    if [[ ! -f "$file" ]]; then
        echo -e "${RED}✗ File not found: $file${NC}"
        return 1
    fi
    echo -e "${GREEN}✓ File exists: $file${NC}"
    return 0
}

# Function to validate YAML syntax
validate_yaml() {
    local file=$1
    echo -n "  Checking YAML syntax... "

    if python3 -c "import yaml; yaml.safe_load(open('$file'))" 2>/dev/null; then
        echo -e "${GREEN}✓${NC}"
        return 0
    else
        echo -e "${RED}✗${NC}"
        echo -e "${RED}  YAML parsing failed!${NC}"
        python3 -c "import yaml; yaml.safe_load(open('$file'))" 2>&1 | sed 's/^/  /'
        return 1
    fi
}

# Function to check required fields
check_required_fields() {
    local file=$1
    local errors=0

    echo "  Checking required fields..."

    # Check for 'name' field
    if grep -q "^name:" "$file"; then
        echo -e "    ${GREEN}✓${NC} Has 'name' field"
    else
        echo -e "    ${RED}✗${NC} Missing 'name' field"
        ((errors++))
    fi

    # Check for 'on' field
    if grep -q "^on:" "$file"; then
        echo -e "    ${GREEN}✓${NC} Has 'on' trigger"
    else
        echo -e "    ${RED}✗${NC} Missing 'on' trigger"
        ((errors++))
    fi

    # Check for 'jobs' field
    if grep -q "^jobs:" "$file"; then
        echo -e "    ${GREEN}✓${NC} Has 'jobs' section"
    else
        echo -e "    ${RED}✗${NC} Missing 'jobs' section"
        ((errors++))
    fi

    return $errors
}

# Function to check for reusable workflow usage
check_reusable_usage() {
    local file=$1

    echo "  Checking reusable workflow usage..."

    if grep -q "uses:.*reusable-backend-ci.yml" "$file"; then
        echo -e "    ${GREEN}✓${NC} Uses reusable backend CI template"
    fi

    if grep -q "uses:.*reusable-frontend-ci.yml" "$file"; then
        echo -e "    ${GREEN}✓${NC} Uses reusable frontend CI template"
    fi

    if ! grep -q "uses:.*reusable-" "$file"; then
        echo -e "    ${YELLOW}⚠${NC} Not using reusable templates (consider migration)"
    fi
}

# Function to check best practices
check_best_practices() {
    local file=$1
    local warnings=0

    echo "  Checking best practices..."

    # Check for path triggers including workflow file
    if grep -A 10 "^on:" "$file" | grep -q "paths:"; then
        if grep -A 20 "paths:" "$file" | grep -q "\.github/workflows/"; then
            echo -e "    ${GREEN}✓${NC} Path triggers include workflow file"
        else
            echo -e "    ${YELLOW}⚠${NC} Path triggers should include workflow file"
            ((warnings++))
        fi
    fi

    # Check for actions versions
    if grep -q "actions/checkout@v4" "$file"; then
        echo -e "    ${GREEN}✓${NC} Using latest checkout action (v4)"
    elif grep -q "actions/checkout@v[0-3]" "$file"; then
        echo -e "    ${YELLOW}⚠${NC} Consider upgrading to checkout@v4"
        ((warnings++))
    fi

    # Check for setup-python version
    if grep -q "setup-python@v5" "$file"; then
        echo -e "    ${GREEN}✓${NC} Using latest setup-python (v5)"
    elif grep -q "setup-python@v[0-4]" "$file"; then
        echo -e "    ${YELLOW}⚠${NC} Consider upgrading to setup-python@v5"
        ((warnings++))
    fi

    # Check for setup-node version
    if grep -q "setup-node@v4" "$file"; then
        echo -e "    ${GREEN}✓${NC} Using latest setup-node (v4)"
    elif grep -q "setup-node@v[0-3]" "$file"; then
        echo -e "    ${YELLOW}⚠${NC} Consider upgrading to setup-node@v4"
        ((warnings++))
    fi

    return $warnings
}

# Function to check for common issues
check_common_issues() {
    local file=$1
    local issues=0

    echo "  Checking for common issues..."

    # Check for hardcoded secrets
    if grep -i "password.*:" "$file" | grep -v "POSTGRES_PASSWORD\|secrets\."; then
        echo -e "    ${RED}✗${NC} Possible hardcoded password detected"
        ((issues++))
    fi

    # Check for working-directory without defaults
    if grep -q "working-directory:" "$file" && ! grep -q "defaults:" "$file"; then
        echo -e "    ${YELLOW}⚠${NC} Consider using 'defaults.run.working-directory'"
        ((issues++))
    fi

    # Check for missing continue-on-error on optional steps
    if grep -q "# Optional" "$file"; then
        if ! grep -q "continue-on-error: true" "$file"; then
            echo -e "    ${YELLOW}⚠${NC} Optional steps should have 'continue-on-error: true'"
        fi
    fi

    if [[ $issues -eq 0 ]]; then
        echo -e "    ${GREEN}✓${NC} No common issues detected"
    fi

    return $issues
}

# Main validation function
validate_workflow_file() {
    local file=$1
    local total_errors=0
    local total_warnings=0

    echo ""
    echo "Validating: $file"
    echo "-----------------------------------------"

    # Check if file exists
    if ! check_file "$file"; then
        return 1
    fi

    # Validate YAML syntax
    if ! validate_yaml "$file"; then
        ((total_errors++))
        return $total_errors
    fi

    # Check required fields
    check_required_fields "$file"
    ((total_errors+=$?))

    # Check for reusable workflow usage
    check_reusable_usage "$file"

    # Check best practices
    check_best_practices "$file"
    ((total_warnings+=$?))

    # Check for common issues
    check_common_issues "$file"
    ((total_warnings+=$?))

    echo ""
    if [[ $total_errors -eq 0 ]] && [[ $total_warnings -eq 0 ]]; then
        echo -e "${GREEN}✓ Workflow validation passed!${NC}"
        return 0
    elif [[ $total_errors -eq 0 ]]; then
        echo -e "${YELLOW}⚠ Validation passed with $total_warnings warning(s)${NC}"
        return 0
    else
        echo -e "${RED}✗ Validation failed with $total_errors error(s) and $total_warnings warning(s)${NC}"
        return 1
    fi
}

# Main script
main() {
    local exit_code=0

    if [[ $# -eq 0 ]]; then
        echo "Usage: $0 <workflow-file.yml>"
        echo ""
        echo "Examples:"
        echo "  $0 .github/workflows/my-project-ci.yml"
        echo "  $0 .github/workflows/reusable-backend-ci.yml"
        echo ""
        exit 1
    fi

    # If wildcard, expand it
    if [[ "$1" == *"*"* ]]; then
        for file in $1; do
            validate_workflow_file "$file"
            ((exit_code+=$?))
        done
    else
        validate_workflow_file "$1"
        exit_code=$?
    fi

    echo ""
    echo "========================================="
    if [[ $exit_code -eq 0 ]]; then
        echo -e "${GREEN}All validations passed!${NC}"
    else
        echo -e "${RED}Some validations failed.${NC}"
    fi
    echo "========================================="

    return $exit_code
}

# Run main function
main "$@"
