#!/usr/bin/env bash
#
# Generate comprehensive HTML report from profiling results
#

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Defaults
OUTPUT_DIR="./performance-report"
TITLE="Performance Report"
INCLUDE_FILES=()

usage() {
    cat << EOF
Usage: generate_report.sh [options]

Options:
  --output-dir DIR     Output directory (default: ./performance-report)
  --title TITLE        Report title
  --include FILE       Include profiling result file (can use multiple)
  --help               Show this help

Examples:
  generate_report.sh --title "API Performance" --include api_results.json
  generate_report.sh --include profile.stats --include flamegraph.svg
EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --title)
            TITLE="$2"
            shift 2
            ;;
        --include)
            INCLUDE_FILES+=("$2")
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
            INCLUDE_FILES+=("$1")
            shift
            ;;
    esac
done

echo -e "${BLUE}=== Performance Report Generator ===${NC}"
echo "Output: $OUTPUT_DIR"
echo "Title: $TITLE"

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Generate HTML report
generate_html() {
    local output="$OUTPUT_DIR/index.html"
    local timestamp
    timestamp=$(date -Iseconds)
    
    cat > "$output" << HTML
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>$TITLE</title>
    <style>
        :root {
            --bg-primary: #0a0a0b;
            --bg-secondary: #141416;
            --bg-tertiary: #1c1c1f;
            --accent-primary: #3b82f6;
            --accent-secondary: #8b5cf6;
            --text-primary: #fafafa;
            --text-secondary: #a1a1aa;
            --text-muted: #71717a;
            --success: #22c55e;
            --warning: #f59e0b;
            --danger: #ef4444;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem;
        }
        
        header {
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--bg-tertiary);
            padding: 2rem 0;
            margin-bottom: 2rem;
        }
        
        h1 {
            font-size: 2rem;
            font-weight: 700;
            background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .timestamp {
            color: var(--text-muted);
            font-size: 0.875rem;
        }
        
        .section {
            background: var(--bg-secondary);
            border-radius: 0.75rem;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            border: 1px solid var(--bg-tertiary);
        }
        
        .section h2 {
            font-size: 1.25rem;
            margin-bottom: 1rem;
            color: var(--text-primary);
        }
        
        .metric-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 1.5rem;
        }
        
        .metric-card {
            background: var(--bg-tertiary);
            padding: 1rem;
            border-radius: 0.5rem;
            border-left: 3px solid var(--accent-primary);
        }
        
        .metric-label {
            font-size: 0.75rem;
            text-transform: uppercase;
            color: var(--text-muted);
            letter-spacing: 0.05em;
        }
        
        .metric-value {
            font-size: 1.5rem;
            font-weight: 600;
            color: var(--text-primary);
            margin-top: 0.25rem;
        }
        
        .status-good { border-left-color: var(--success); }
        .status-warning { border-left-color: var(--warning); }
        .status-danger { border-left-color: var(--danger); }
        
        pre {
            background: var(--bg-tertiary);
            padding: 1rem;
            border-radius: 0.5rem;
            overflow-x: auto;
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 0.875rem;
            color: var(--text-secondary);
        }
        
        .file-list {
            list-style: none;
        }
        
        .file-list li {
            padding: 0.5rem 0;
            border-bottom: 1px solid var(--bg-tertiary);
        }
        
        .file-list li:last-child {
            border-bottom: none;
        }
        
        .file-list a {
            color: var(--accent-primary);
            text-decoration: none;
        }
        
        .file-list a:hover {
            text-decoration: underline;
        }
        
        .recommendations {
            background: linear-gradient(135deg, rgba(59, 130, 246, 0.1), rgba(139, 92, 246, 0.1));
            border: 1px solid var(--accent-primary);
        }
        
        .recommendations ul {
            list-style: none;
            padding-left: 0;
        }
        
        .recommendations li {
            padding: 0.5rem 0;
            padding-left: 1.5rem;
            position: relative;
        }
        
        .recommendations li::before {
            content: "→";
            position: absolute;
            left: 0;
            color: var(--accent-primary);
        }
        
        .flamegraph-container {
            width: 100%;
            overflow-x: auto;
            margin-top: 1rem;
        }
        
        .flamegraph-container img, .flamegraph-container svg {
            max-width: 100%;
            height: auto;
        }
    </style>
</head>
<body>
    <header>
        <div class="container">
            <h1>$TITLE</h1>
            <p class="timestamp">Generated: $timestamp</p>
        </div>
    </header>
    
    <div class="container">
        <div class="section">
            <h2>📊 Summary</h2>
            <div class="metric-grid">
                <div class="metric-card">
                    <div class="metric-label">Profile Type</div>
                    <div class="metric-value">Performance Analysis</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Files Analyzed</div>
                    <div class="metric-value">${#INCLUDE_FILES[@]}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Status</div>
                    <div class="metric-value">Complete</div>
                </div>
            </div>
        </div>
        
        <div class="section">
            <h2>📁 Included Files</h2>
            <ul class="file-list">
HTML

    # Add file list
    for file in "${INCLUDE_FILES[@]}"; do
        if [[ -f "$file" ]]; then
            local basename
            basename=$(basename "$file")
            # Copy to output dir if it's a visual file
            if [[ "$file" == *.svg || "$file" == *.png || "$file" == *.jpg ]]; then
                cp "$file" "$OUTPUT_DIR/"
                echo "                <li><a href=\"$basename\">$basename</a> (📊 visualization)</li>" >> "$output"
            else
                echo "                <li>$basename</li>" >> "$output"
            fi
        fi
    done
    
    cat >> "$output" << HTML
            </ul>
        </div>
        
        <div class="section recommendations">
            <h2>💡 Recommendations</h2>
            <ul>
                <li>Review the flamegraph to identify hot code paths</li>
                <li>Check database query performance for N+1 patterns</li>
                <li>Consider caching for frequently accessed data</li>
                <li>Monitor memory usage for potential leaks</li>
                <li>Set up continuous profiling in CI/CD pipeline</li>
            </ul>
        </div>
        
        <div class="section">
            <h2>🔧 Next Steps</h2>
            <pre><code># Re-run profile after optimizations
/profile python app.py --flamegraph

# Compare with baseline
/profile api --endpoint /api/v1/users --baseline before.json --compare

# Continuous monitoring
/profile db --slow-queries --threshold 50 --last-hour</code></pre>
        </div>
    </div>
</body>
</html>
HTML

    echo -e "${GREEN}Report generated: $output${NC}"
}

# Copy included files
for file in "${INCLUDE_FILES[@]}"; do
    if [[ -f "$file" ]]; then
        cp "$file" "$OUTPUT_DIR/"
        echo "Copied: $file"
    else
        echo -e "${YELLOW}Warning: File not found: $file${NC}"
    fi
done

generate_html

echo ""
echo -e "${BLUE}=== Report Generation Complete ===${NC}"
echo "Open $OUTPUT_DIR/index.html in your browser"
