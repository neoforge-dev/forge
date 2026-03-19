package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// Pattern represents a reusable template or pattern
type Pattern struct {
	ID          string   `json:"id"`
	Name        string   `json:"name"`
	Description string   `json:"description"`
	Category    string   `json:"category"`
	Tags        []string `json:"tags"`
	Template    string   `json:"template"`
	Variables   []string `json:"variables"`
	CreatedAt   string   `json:"created_at"`
	UsageCount  int      `json:"usage_count"`
}

// patternCmd represents the pattern noun
var patternCmd = &cobra.Command{
	Use:   "pattern",
	Short: "Manage reusable patterns and templates",
	Long: `Patterns are reusable templates for common tasks and structures.

Categories:
  - code: Code patterns (functions, classes, APIs)
  - config: Configuration patterns (YAML, JSON, TOML)
  - doc: Documentation patterns (ADRs, READMEs, specs)
  - workflow: Workflow patterns (CI/CD, automation)

Examples:
  # List all patterns
  forge pattern list

  # List patterns by category
  forge pattern list --category code

  # Show pattern details
  forge pattern show fastapi-endpoint

  # Use a pattern (outputs to stdout)
  forge pattern show fastapi-endpoint --render`,
}

// patternListCmd: forge pattern list
var patternListCmd = &cobra.Command{
	Use:   "list",
	Short: "List available patterns",
	Long:  "List all reusable patterns with optional filtering by category.",
	RunE: func(cmd *cobra.Command, args []string) error {
		category, _ := cmd.Flags().GetString("category")
		tag, _ := cmd.Flags().GetString("tag")
		format, _ := cmd.Flags().GetString("format")

		patterns, err := loadPatterns()
		if err != nil {
			return fmt.Errorf("failed to load patterns: %w", err)
		}

		// Filter by category if specified
		if category != "" {
			var filtered []Pattern
			for _, p := range patterns {
				if strings.EqualFold(p.Category, category) {
					filtered = append(filtered, p)
				}
			}
			patterns = filtered
		}

		// Filter by tag if specified
		if tag != "" {
			var filtered []Pattern
			for _, p := range patterns {
				for _, t := range p.Tags {
					if strings.EqualFold(t, tag) {
						filtered = append(filtered, p)
						break
					}
				}
			}
			patterns = filtered
		}

		formatter := internal.NewFormatter(format, nil)

		switch format {
		case "json":
			return formatter.WriteJSON(patterns)
		case "csv":
			headers := []string{"ID", "Name", "Category", "Description", "Tags"}
			rows := make([][]string, len(patterns))
			for i, p := range patterns {
				rows[i] = []string{
				p.ID,
				p.Name,
				p.Category,
				helperTruncate(p.Description, 40),
				strings.Join(p.Tags, ", "),
			}
			}
			return formatter.WriteCSV(headers, rows)
		case "quiet":
			return nil
		default: // table
			tw := formatter.NewTableWriter()
			tw.WriteHeader("ID", "NAME", "CATEGORY", "DESCRIPTION", "TAGS")
			for _, p := range patterns {
			tw.WriteRow(
				helperTruncate(p.ID, 15),
				helperTruncate(p.Name, 20),
				helperTruncate(p.Category, 12),
				helperTruncate(p.Description, 30),
				helperTruncate(strings.Join(p.Tags, ", "), 20),
			)
			}
			tw.Flush()
			formatter.Printf("\nTotal: %d patterns\n", len(patterns))
		}
		return nil
	},
}

// patternShowCmd: forge pattern show <id>
var patternShowCmd = &cobra.Command{
	Use:   "show [id]",
	Short: "Show pattern details",
	Long:  "Display detailed information about a specific pattern.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		patternID := args[0]
		format, _ := cmd.Flags().GetString("format")
		render, _ := cmd.Flags().GetBool("render")

		pattern, err := findPattern(patternID)
		if err != nil {
			return fmt.Errorf("pattern not found: %s", patternID)
		}

		formatter := internal.NewFormatter(format, nil)

		// If render flag is set, just output the template
		if render {
			fmt.Println(pattern.Template)
			return nil
		}

		switch format {
		case "json":
			return formatter.WriteJSON(pattern)
		case "csv", "quiet":
			return nil
		default: // table
			formatter.Printf("Pattern: %s\n", pattern.ID)
			formatter.Printf("  Name:        %s\n", pattern.Name)
			formatter.Printf("  Description: %s\n", pattern.Description)
			formatter.Printf("  Category:    %s\n", pattern.Category)
			formatter.Printf("  Tags:        %s\n", strings.Join(pattern.Tags, ", "))
			formatter.Printf("  Variables:   %s\n", strings.Join(pattern.Variables, ", "))
			formatter.Printf("  Usage Count: %d\n", pattern.UsageCount)
			formatter.Printf("  Created:     %s\n", pattern.CreatedAt)
			formatter.Println("")
			formatter.Println("Template:")
			formatter.Println("---")
			fmt.Println(pattern.Template)
			formatter.Println("---")
		}
		return nil
	},
}

// Helper functions

func getPatternsDir() string {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = findForgeRoot()
	}
	if forgeRoot != "" {
		return filepath.Join(forgeRoot, ".forge", "patterns")
	}
	return ".forge/patterns"
}

func loadPatterns() ([]Pattern, error) {
	// 1. Try live v3 API (best effort — daemon may not be running).
	apiClient := internal.NewClient()
	v3Patterns, apiErr := apiClient.ListPatterns(context.Background())
	if apiErr == nil && len(v3Patterns) > 0 {
		// Map v3 wire shape to the CLI Pattern type.
		// Fields not exposed by the API (category, tags, variables, template) are
		// left at zero-value so existing display code continues to work.
		result := make([]Pattern, 0, len(v3Patterns))
		for _, p := range v3Patterns {
			result = append(result, Pattern{
				ID:          p.ID,
				Name:        p.Name,
				Description: p.Domain,
				Category:    "remote",
				Tags:        []string{p.Domain},
				Template:    p.YAMLContent,
				CreatedAt:   p.CreatedAt.Format("2006-01-02"),
			})
		}
		return result, nil
	}

	// Log non-fatal API errors (unreachable daemon is expected in offline use).
	if apiErr != nil {
		var unreachable *internal.DaemonUnreachableError
		if !errors.As(apiErr, &unreachable) {
			fmt.Fprintf(os.Stderr, "warning: pattern API error: %v\n", apiErr)
		}
	}

	// 2. Try local patterns.json file.
	patternsDir := getPatternsDir()
	patternsFile := filepath.Join(patternsDir, "patterns.json")
	if data, err := os.ReadFile(patternsFile); err == nil {
		var patterns []Pattern
		if err := json.Unmarshal(data, &patterns); err == nil {
			return patterns, nil
		}
	}

	// 3. Return built-in patterns as final fallback (never errors).
	return getBuiltInPatterns(), nil
}

func findPattern(id string) (*Pattern, error) {
	patterns, err := loadPatterns()
	if err != nil {
		return nil, err
	}
	
	for _, p := range patterns {
		if strings.EqualFold(p.ID, id) || strings.EqualFold(p.Name, id) {
			return &p, nil
		}
	}
	
	return nil, fmt.Errorf("pattern not found: %s", id)
}

func getBuiltInPatterns() []Pattern {
	return []Pattern{
		{
			ID:          "fastapi-endpoint",
			Name:        "FastAPI Endpoint",
			Description: "Standard FastAPI endpoint with CRUD operations",
			Category:    "code",
			Tags:        []string{"python", "api", "fastapi"},
			Variables:   []string{"resource_name", "model_name"},
			CreatedAt:   time.Now().Format("2006-01-02"),
			UsageCount:  0,
			Template: `@router.get("/{{resource_name}}")
async def list_{{resource_name}}(
    db: Session = Depends(get_db)
):
    """List all {{resource_name}}."""
    return db.query({{model_name}}).all()

@router.post("/{{resource_name}}")
async def create_{{resource_name}}(
    item: {{model_name}}Create,
    db: Session = Depends(get_db)
):
    """Create a new {{resource_name}}."""
    db_item = {{model_name}}(**item.dict())
    db.add(db_item)
    db.commit()
    return db_item`,
		},
		{
			ID:          "cobra-command",
			Name:        "Cobra Command",
			Description: "Standard Cobra CLI command structure",
			Category:    "code",
			Tags:        []string{"go", "cli", "cobra"},
			Variables:   []string{"command_name", "noun"},
			CreatedAt:   time.Now().Format("2006-01-02"),
			UsageCount:  0,
			Template: `// {{command_name}}Cmd represents the {{noun}} {{command_name}} command
var {{command_name}}Cmd = &cobra.Command{
	Use:   "{{command_name}}",
	Short: "{{command_name}} {{noun}}",
	Long:  "{{command_name}} a {{noun}} in the system.",
	RunE: func(cmd *cobra.Command, args []string) error {
		// Implementation here
		return nil
	},
}`,
		},
		{
			ID:          "adr",
			Name:        "Architecture Decision Record",
			Description: "Standard ADR template",
			Category:    "doc",
			Tags:        []string{"documentation", "architecture", "adr"},
			Variables:   []string{"number", "title", "date"},
			CreatedAt:   time.Now().Format("2006-01-02"),
			UsageCount:  0,
			Template: `# ADR-{{number}}: {{title}}

**Date:** {{date}}
**Status:** Proposed

## Context

What is the issue that we're seeing that is motivating this decision?

## Decision

What is the change that we're proposing or have agreed to implement?

## Consequences

What becomes easier or more difficult to do and any risks introduced?
`,
		},
		{
			ID:          "github-workflow",
			Name:        "GitHub Actions Workflow",
			Description: "Standard CI/CD workflow for testing and deployment",
			Category:    "workflow",
			Tags:        []string{"ci", "cd", "github", "automation"},
			Variables:   []string{"workflow_name", "branch"},
			CreatedAt:   time.Now().Format("2006-01-02"),
			UsageCount:  0,
			Template: `name: {{workflow_name}}

on:
  push:
    branches: [{{branch}}]
  pull_request:
    branches: [{{branch}}]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up environment
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: pip install -r requirements.txt
    
    - name: Run tests
      run: pytest -xvs`,
		},
		{
			ID:          "dockerfile",
			Name:        "Multi-stage Dockerfile",
			Description: "Production-ready multi-stage Docker build",
			Category:    "config",
			Tags:        []string{"docker", "container", "deployment"},
			Variables:   []string{"app_name", "port"},
			CreatedAt:   time.Now().Format("2006-01-02"),
			UsageCount:  0,
			Template: `# Build stage
FROM python:3.11-slim as builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --user -r requirements.txt

# Production stage
FROM python:3.11-slim

WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY . .

ENV PATH=/root/.local/bin:$PATH
ENV PORT={{port}}

EXPOSE {{port}}

CMD ["python", "-m", "{{app_name}}"]`,
		},
	}
}

// helperTruncate truncates a string to maxLen
func helperTruncate(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen-3] + "..."
}

func init() {
	patternCmd.Hidden = true
	// Add flags
	patternListCmd.Flags().String("category", "", "Filter by category")
	patternListCmd.Flags().String("tag", "", "Filter by tag")

	patternShowCmd.Flags().Bool("render", false, "Render template to stdout")

	// Add commands
	patternCmd.AddCommand(patternListCmd)
	patternCmd.AddCommand(patternShowCmd)
}
