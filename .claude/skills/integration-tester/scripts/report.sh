#!/usr/bin/env bash
#
# Integration Test Report Generator
# Usage: report.sh [options]
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

usage() {
    cat <<EOF
Integration Test Report Generator

Usage: report.sh [options]

Options:
  --input FILE         Input JSON results file
  --format FORMAT      Output format (console|json|html|markdown)
  --output FILE        Output file (default: stdout)
  --trend              Show trend from previous runs
  --help               Show this help

Examples:
  report.sh --input results.json --format html --output report.html
  report.sh --input results.json --format markdown

EOF
}

# Parse arguments
parse_args() {
    INPUT_FILE="./integration-test-results.json"
    FORMAT="console"
    OUTPUT_FILE=""
    SHOW_TREND=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --input)
                INPUT_FILE="$2"
                shift 2
                ;;
            --format)
                FORMAT="$2"
                shift 2
                ;;
            --output)
                OUTPUT_FILE="$2"
                shift 2
                ;;
            --trend)
                SHOW_TREND=true
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
}

# Console report
generate_console() {
    if [ ! -f "$INPUT_FILE" ]; then
        echo "Error: Input file not found: $INPUT_FILE"
        exit 1
    fi
    
    local suite timestamp duration total passed failed
    suite=$(jq -r '.suite // "Integration Tests"' "$INPUT_FILE")
    timestamp=$(jq -r '.timestamp // "Unknown"' "$INPUT_FILE")
    duration=$(jq -r '.duration // 0' "$INPUT_FILE")
    total=$(jq -r '.summary.total // 0' "$INPUT_FILE")
    passed=$(jq -r '.summary.passed // 0' "$INPUT_FILE")
    failed=$(jq -r '.summary.failed // 0' "$INPUT_FILE")
    
    cat <<EOF
╔════════════════════════════════════════════════════════════╗
║           INTEGRATION TEST REPORT                          ║
╠════════════════════════════════════════════════════════════╣
║ Suite:    $suite
║ Time:     $timestamp
║ Duration: ${duration}s
╠════════════════════════════════════════════════════════════╣
║ SUMMARY                                                    ║
║                                                            ║
║   Total Tests:   $total
║   Passed:       $passed
║   Failed:       $failed
║   Success Rate: $(echo "scale=1; $passed * 100 / ($total > 0 ? $total : 1)" | bc)%
╠════════════════════════════════════════════════════════════╣
EOF

    # List passed tests
    if [ "$passed" -gt 0 ]; then
        echo "║ PASSED TESTS                                              ║"
        jq -r '.tests[] | select(.status == "passed") | "║   ✅ \(.name)"' "$INPUT_FILE"
        echo "║                                                            ║"
    fi
    
    # List failed tests
    if [ "$failed" -gt 0 ]; then
        echo "║ FAILED TESTS                                              ║"
        jq -r '.tests[] | select(.status == "failed") | "║   ❌ \(.name)\n║      Error: \(.error)"' "$INPUT_FILE"
        echo "║                                                            ║"
    fi
    
    cat <<EOF
╚════════════════════════════════════════════════════════════╝
EOF
}

# JSON report
generate_json() {
    if [ -f "$INPUT_FILE" ]; then
        cat "$INPUT_FILE"
    else
        echo "Error: Input file not found: $INPUT_FILE"
        exit 1
    fi
}

# HTML report
generate_html() {
    if [ ! -f "$INPUT_FILE" ]; then
        echo "Error: Input file not found: $INPUT_FILE"
        exit 1
    fi
    
    local suite timestamp duration total passed failed
    suite=$(jq -r '.suite // "Integration Tests"' "$INPUT_FILE")
    timestamp=$(jq -r '.timestamp // "Unknown"' "$INPUT_FILE")
    duration=$(jq -r '.duration // 0' "$INPUT_FILE")
    total=$(jq -r '.summary.total // 0' "$INPUT_FILE")
    passed=$(jq -r '.summary.passed // 0' "$INPUT_FILE")
    failed=$(jq -r '.summary.failed // 0' "$INPUT_FILE")
    
    cat <<HTML
<!DOCTYPE html>
<html>
<head>
    <title>Integration Test Report - $suite</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .summary {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            margin-bottom: 20px;
        }
        .stat-card {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            text-align: center;
        }
        .stat-value {
            font-size: 2em;
            font-weight: bold;
            color: #333;
        }
        .stat-label {
            color: #666;
            font-size: 0.9em;
        }
        .passed { color: #28a745; }
        .failed { color: #dc3545; }
        .test-list {
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        .test-item {
            padding: 15px 20px;
            border-bottom: 1px solid #eee;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .test-item:last-child {
            border-bottom: none;
        }
        .test-item.passed {
            background: #f8fff8;
        }
        .test-item.failed {
            background: #fff8f8;
        }
        .status-icon {
            font-size: 1.2em;
        }
        .test-details {
            flex: 1;
        }
        .test-name {
            font-weight: 500;
        }
        .test-error {
            font-size: 0.85em;
            color: #dc3545;
            margin-top: 5px;
        }
        .test-duration {
            color: #666;
            font-size: 0.85em;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🧪 Integration Test Report</h1>
        <p>$suite • $timestamp</p>
    </div>
    
    <div class="summary">
        <div class="stat-card">
            <div class="stat-value">$total</div>
            <div class="stat-label">Total Tests</div>
        </div>
        <div class="stat-card">
            <div class="stat-value passed">$passed</div>
            <div class="stat-label">Passed</div>
        </div>
        <div class="stat-card">
            <div class="stat-value failed">$failed</div>
            <div class="stat-label">Failed</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">$(echo "scale=0; $passed * 100 / ($total > 0 ? $total : 1)" | bc)%</div>
            <div class="stat-label">Success Rate</div>
        </div>
    </div>
    
    <div class="test-list">
        $(jq -r '.tests[] | 
            if .status == "passed" then
                "<div class=\"test-item passed\"><span class=\"status-icon passed\">✅</span><div class=\"test-details\"><div class=\"test-name\">\(.name)</div><div class=\"test-duration\">\(.duration)s</div></div></div>"
            else
                "<div class=\"test-item failed\"><span class=\"status-icon failed\">❌</span><div class=\"test-details\"><div class=\"test-name\">\(.name)</div><div class=\"test-error\">\(.error // \"Unknown error\")</div></div></div>"
            end' "$INPUT_FILE")
    </div>
</body>
</html>
HTML
}

# Markdown report
generate_markdown() {
    if [ ! -f "$INPUT_FILE" ]; then
        echo "Error: Input file not found: $INPUT_FILE"
        exit 1
    fi
    
    local suite timestamp duration total passed failed
    suite=$(jq -r '.suite // "Integration Tests"' "$INPUT_FILE")
    timestamp=$(jq -r '.timestamp // "Unknown"' "$INPUT_FILE")
    duration=$(jq -r '.duration // 0' "$INPUT_FILE")
    total=$(jq -r '.summary.total // 0' "$INPUT_FILE")
    passed=$(jq -r '.summary.passed // 0' "$INPUT_FILE")
    failed=$(jq -r '.summary.failed // 0' "$INPUT_FILE")
    
    cat <<MD
# Integration Test Report

**Suite:** $suite  
**Time:** $timestamp  
**Duration:** ${duration}s

## Summary

| Metric | Value |
|--------|-------|
| Total Tests | $total |
| Passed | ✅ $passed |
| Failed | ❌ $failed |
| Success Rate | $(echo "scale=1; $passed * 100 / ($total > 0 ? $total : 1)" | bc)% |

## Test Results

### Passed

$(jq -r '.tests[] | select(.status == "passed") | "- ✅ **\(.name)** (\(.duration)s)"' "$INPUT_FILE")

### Failed

$(jq -r '.tests[] | select(.status == "failed") | "- ❌ **\(.name)**\n  - Error: \(.error // \"Unknown error\")\n  - Suggestion: \(.suggestion // \"N/A\")"' "$INPUT_FILE")

---

*Generated by integration-tester*
MD
}

# Main
main() {
    parse_args "$@"
    
    # Generate report
    local output=""
    case "$FORMAT" in
        console)
            output=$(generate_console)
            ;;
        json)
            output=$(generate_json)
            ;;
        html)
            output=$(generate_html)
            ;;
        markdown|md)
            output=$(generate_markdown)
            ;;
        *)
            echo "Unknown format: $FORMAT"
            exit 1
            ;;
    esac
    
    # Output to file or stdout
    if [ -n "$OUTPUT_FILE" ]; then
        echo "$output" > "$OUTPUT_FILE"
        echo "Report saved to: $OUTPUT_FILE"
    else
        echo "$output"
    fi
}

main "$@"
