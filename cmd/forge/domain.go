package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
)

// domainCmd represents the domain noun
var domainCmd = &cobra.Command{
	Use:   "domain",
	Short: "Manage domains - business domains in the portfolio",
	Long: `Domains are business domains in the FORGE portfolio.

Each domain has:
  - Key: Unique identifier (e.g., codeswiftr-com)
  - Display Name: Human-readable name
  - Compliance: List of compliance requirements
  - Products: List of products in this domain
  - Business: Deployment status, MRR, Stripe status

Universal verbs:
  list, show, create

Examples:
  # List all domains
  forge domain list

  # Show domain details
  forge domain show codeswiftr-com

  # Create a new domain
  forge domain create --key newdomain-com --name "New Domain"`,
}

// domainListCmd: forge domain list
var domainListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all domains",
	Long:  "List all domains in the FORGE portfolio.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")
		activeOnly, _ := cmd.Flags().GetBool("active")

		domains, err := loadDomains()
		if err != nil {
			return fmt.Errorf("failed to load domains: %w", err)
		}

		// Filter by active if requested
		if activeOnly {
			var filtered []internal.Domain
			for _, d := range domains {
				if d.Active {
					filtered = append(filtered, d)
				}
			}
			domains = filtered
		}

		formatter := internal.NewFormatter(format, nil)
		if err := formatter.FormatDomains(domains); err != nil {
			return err
		}

		if format == "table" {
			formatter.Printf("\nTotal: %d domains\n", len(domains))
		}
		return nil
	},
}

// domainShowCmd: forge domain show <key>
var domainShowCmd = &cobra.Command{
	Use:   "show [key]",
	Short: "Show domain details",
	Long:  "Display detailed information about a specific domain.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		domainKey := args[0]
		format, _ := cmd.Flags().GetString("format")

		domains, err := loadDomains()
		if err != nil {
			return fmt.Errorf("failed to load domains: %w", err)
		}

		var domain *internal.Domain
		for _, d := range domains {
			if d.Key == domainKey {
				domain = &d
				break
			}
		}

		if domain == nil {
			return fmt.Errorf("domain not found: %s", domainKey)
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatDomainDetail(domain)
	},
}

// domainCreateCmd: forge domain create --key ... --name ...
var domainCreateCmd = &cobra.Command{
	Use:   "create",
	Short: "Create a new domain",
	Long:  "Create a new domain in the FORGE portfolio.",
	RunE: func(cmd *cobra.Command, args []string) error {
		key, _ := cmd.Flags().GetString("key")
		name, _ := cmd.Flags().GetString("name")
		format, _ := cmd.Flags().GetString("format")

		if key == "" {
			return fmt.Errorf("--key is required")
		}
		if name == "" {
			return fmt.Errorf("--name is required")
		}

		// Create domain (in memory for now - would need API endpoint for persistence)
		domain := internal.Domain{
			Key:         key,
			DisplayName: name,
			Active:      true,
		}

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(domain)
		}
		formatter.Printf("Domain created: %s (%s)\n", key, name)
		formatter.Printf("Note: Domain persistence requires API implementation\n")
		return nil
	},
}

// domainUpdateCmd: forge domain update <key> --score 85 --status active
var domainUpdateCmd = &cobra.Command{
	Use:   "update <key>",
	Short: "Update domain metadata",
	Long: `Update metadata for a domain. Calls PATCH /api/domains/{key} on the daemon.
At least one of --score or --status must be provided.

Examples:
  forge domain update codeswiftr-com --score 90
  forge domain update leanvibe-ai --status active
  forge domain update brandfocus-ai --score 75 --status inactive`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		domainKey := args[0]
		score, _ := cmd.Flags().GetInt("score")
		status, _ := cmd.Flags().GetString("status")
		format, _ := cmd.Flags().GetString("format")

		scoreChanged := cmd.Flags().Changed("score")
		statusChanged := cmd.Flags().Changed("status")

		if !scoreChanged && !statusChanged {
			return fmt.Errorf("at least one of --score or --status is required\n  Example: forge domain update %s --score 85", domainKey)
		}

		if scoreChanged && (score < 0 || score > 100) {
			return fmt.Errorf("--score must be between 0 and 100 (got %d)", score)
		}

		if statusChanged && status != "active" && status != "inactive" && status != "archived" {
			return fmt.Errorf("--status must be one of: active, inactive, archived (got %q)", status)
		}

		// Build patch payload with only the provided fields.
		patch := make(map[string]interface{})
		if scoreChanged {
			patch["score"] = score
		}
		if statusChanged {
			patch["status"] = status
		}

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		resp, err := client.Patch(ctx, "/domains/"+domainKey, patch)
		if err != nil {
			return fmt.Errorf("domain update: %w\n  Recovery: check daemon with: forge daemon status", err)
		}
		defer resp.Body.Close()

		if resp.StatusCode == http.StatusNotFound {
			return fmt.Errorf("domain not found: %s\n  Recovery: check available domains with: forge domain list", domainKey)
		}

		if err := internal.CheckResponse(resp); err != nil {
			return fmt.Errorf("domain update %s: %w\n  Recovery: verify domain key with: forge domain list", domainKey, err)
		}

		// Decode the updated domain from the response (if body is provided).
		var updated map[string]interface{}
		if decErr := json.NewDecoder(resp.Body).Decode(&updated); decErr != nil {
			// Non-fatal: daemon may return 204 No Content.
			updated = map[string]interface{}{"key": domainKey}
			for k, v := range patch {
				updated[k] = v
			}
		}

		if format == "json" {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(updated)
		}

		fmt.Printf("Domain %q updated.\n", domainKey)
		for k, v := range patch {
			fmt.Printf("  %s: %v\n", k, v)
		}
		fmt.Printf("  Verify: forge domain show %s\n", domainKey)
		return nil
	},
}

// DomainRegistryYAML represents the domain registry from domains.yaml
type DomainRegistryYAML struct {
	Domains map[string]internal.Domain `yaml:"domains"`
}

// loadDomains loads domains from the domains.yaml file.
// Search order:
//  1. config/domains.yaml  (relative to working directory — for packaged installs)
//  2. .forge/domains.yaml  (local override)
func loadDomains() ([]internal.Domain, error) {
	locations := []string{
		"config/domains.yaml",
		".forge/domains.yaml",
	}

	var data []byte

	for _, loc := range locations {
		var err error
		data, err = os.ReadFile(loc)
		if err == nil {
			break
		}
	}

	if data == nil {
		return nil, fmt.Errorf("no domains.yaml found — run: cp config/domains.yaml.example config/domains.yaml")
	}

	var registry DomainRegistryYAML
	if err := yaml.Unmarshal(data, &registry); err != nil {
		return nil, fmt.Errorf("failed to parse domains.yaml: %w", err)
	}

	// Convert map to slice with keys
	domains := make([]internal.Domain, 0, len(registry.Domains))
	for key, domain := range registry.Domains {
		domain.Key = key
		domains = append(domains, domain)
	}

	return domains, nil
}


// validationRoot returns the path to the docs/validation/ directory.
// It respects FORGE_ROOT env var; falls back to the current working directory.
func validationRoot() string {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = "."
	}
	return root + "/docs/validation"
}

// readTrackerFile reads docs/validation/{domain}.md and returns its contents.
func readTrackerFile(domain string) (string, error) {
	path := validationRoot() + "/" + domain + ".md"
	data, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("validation tracker not found for %q: %w\n  Recovery: create docs/validation/%s.md", domain, err, domain)
	}
	return string(data), nil
}

// ValidationMeta holds the header fields parsed from a tracker file.
type ValidationMeta struct {
	Stage    string
	Owner    string
	Score    string
	Deadline string
}

// parseTrackerMeta extracts Stage, Owner, Score, and Deadline from tracker content.
func parseTrackerMeta(content string) ValidationMeta {
	var m ValidationMeta

	if re := regexp.MustCompile(`\*\*Stage:\*\*\s*(.+)`); re.MatchString(content) {
		m.Stage = strings.TrimSpace(re.FindStringSubmatch(content)[1])
	}
	if re := regexp.MustCompile(`\*\*Owner:\*\*\s*([^\|]+)`); re.MatchString(content) {
		m.Owner = strings.TrimSpace(re.FindStringSubmatch(content)[1])
	}
	if re := regexp.MustCompile(`\*\*Score:\*\*\s*([^\(]+(?:\([^\)]*\))?)`); re.MatchString(content) {
		m.Score = strings.TrimSpace(re.FindStringSubmatch(content)[1])
	}
	if re := regexp.MustCompile(`\*\*Deadline:\*\*\s*([^\n]+)`); re.MatchString(content) {
		m.Deadline = strings.TrimSpace(re.FindStringSubmatch(content)[1])
	}

	return m
}

// Assumption holds a single row parsed from the P0 assumptions table.
type Assumption struct {
	ID         string
	Assumption string
	Status     string
	Evidence   string
}

// parseAssumptions extracts assumption rows from the P0 assumptions table.
// Rows match the pattern: | ID | Assumption text | Status | Evidence |
func parseAssumptions(content string) []Assumption {
	// Match rows that start with a pipe and have an ID like IS-1, IS-2, etc.
	re := regexp.MustCompile(`\|\s*([A-Z]+-\d+)\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|`)
	matches := re.FindAllStringSubmatch(content, -1)

	assumptions := make([]Assumption, 0, len(matches))
	for _, m := range matches {
		assumptions = append(assumptions, Assumption{
			ID:         strings.TrimSpace(m[1]),
			Assumption: strings.TrimSpace(m[2]),
			Status:     strings.TrimSpace(m[3]),
			Evidence:   strings.TrimSpace(m[4]),
		})
	}
	return assumptions
}

// domainValidateCmd: forge domain validate <domain>
var domainValidateCmd = &cobra.Command{
	Use:   "validate <domain>",
	Short: "Show validation stage and blockers for a domain",
	Long: `Read the validation tracker for a domain and display its current stage,
owner, score, deadline, and blocker summary.

The tracker file is expected at: docs/validation/{domain}.md

Examples:
  forge domain validate codeswiftr-com
  forge domain validate brandfocus-ai`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		domain := args[0]

		content, err := readTrackerFile(domain)
		if err != nil {
			return err
		}

		meta := parseTrackerMeta(content)

		fmt.Printf("Domain:   %s\n", domain)
		if meta.Stage != "" {
			fmt.Printf("Stage:    %s\n", meta.Stage)
		} else {
			fmt.Printf("Stage:    (not set)\n")
		}
		if meta.Owner != "" {
			fmt.Printf("Owner:    %s\n", meta.Owner)
		}
		if meta.Score != "" {
			fmt.Printf("Score:    %s\n", meta.Score)
		}
		if meta.Deadline != "" {
			fmt.Printf("Deadline: %s\n", meta.Deadline)
		}

		assumptions := parseAssumptions(content)
		untested := 0
		for _, a := range assumptions {
			if strings.EqualFold(a.Status, "UNTESTED") {
				untested++
			}
		}
		fmt.Printf("\nP0 Assumptions: %d total, %d untested\n", len(assumptions), untested)
		if untested > 0 {
			fmt.Printf("Blocker: %d untested P0 assumption(s) — run: forge domain assumptions %s\n", untested, domain)
		}
		return nil
	},
}

// domainAssumptionsCmd: forge domain assumptions <domain>
var domainAssumptionsCmd = &cobra.Command{
	Use:   "assumptions <domain>",
	Short: "List P0 assumptions with test status",
	Long: `Read the validation tracker for a domain and display its P0 assumptions table.

The tracker file is expected at: docs/validation/{domain}.md

Examples:
  forge domain assumptions codeswiftr-com`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		domain := args[0]

		content, err := readTrackerFile(domain)
		if err != nil {
			return err
		}

		assumptions := parseAssumptions(content)
		if len(assumptions) == 0 {
			fmt.Printf("No P0 assumptions found in tracker for %q\n", domain)
			fmt.Printf("  Expected format: | IS-1 | description | STATUS | evidence |\n")
			return nil
		}

		// Print header
		fmt.Printf("%-6s  %-50s  %-10s  %s\n", "ID", "Assumption", "Status", "Evidence")
		fmt.Printf("%s  %s  %s  %s\n", strings.Repeat("-", 6), strings.Repeat("-", 50), strings.Repeat("-", 10), strings.Repeat("-", 20))

		for _, a := range assumptions {
			assumption := a.Assumption
			if len(assumption) > 50 {
				assumption = assumption[:47] + "..."
			}
			fmt.Printf("%-6s  %-50s  %-10s  %s\n", a.ID, assumption, a.Status, a.Evidence)
		}

		fmt.Printf("\nTotal: %d assumption(s)\n", len(assumptions))
		return nil
	},
}

// domainDecideCmd: forge domain decide <domain> GO|KILL
var domainDecideCmd = &cobra.Command{
	Use:   "decide <domain> <GO|KILL>",
	Short: "Record a go/kill decision in the validation tracker",
	Long: `Append a GO or KILL decision to the validation tracker for a domain.

The decision is appended under a ## Decision section in the tracker file at
docs/validation/{domain}.md. If the file does not exist, the command fails.

Examples:
  forge domain decide codeswiftr-com GO
  forge domain decide brandfocus-ai KILL`,
	Args: cobra.ExactArgs(2),
	RunE: func(cmd *cobra.Command, args []string) error {
		domain := args[0]
		decision := strings.ToUpper(strings.TrimSpace(args[1]))

		if decision != "GO" && decision != "KILL" {
			return fmt.Errorf("decision must be GO or KILL (got %q)\n  Example: forge domain decide %s GO", args[1], domain)
		}

		// Ensure the tracker file exists before appending.
		content, err := readTrackerFile(domain)
		if err != nil {
			return err
		}

		hostname, _ := os.Hostname()
		datestamp := time.Now().UTC().Format("2006-01-02")
		entry := fmt.Sprintf("\n- [x] %s — recorded %s by %s\n", decision, datestamp, hostname)

		// Append the decision entry. If a ## Decision section already exists,
		// append after it; otherwise add a new section at the end.
		path := validationRoot() + "/" + domain + ".md"
		var updated string
		if strings.Contains(content, "## Decision") {
			// Append after the last line of the existing Decision section.
			idx := strings.LastIndex(content, "## Decision")
			// Find the end of the section (next ## heading or EOF).
			rest := content[idx:]
			nextSection := regexp.MustCompile(`\n## `).FindStringIndex(rest[3:])
			if nextSection != nil {
				insertAt := idx + 3 + nextSection[0]
				updated = content[:insertAt] + entry + content[insertAt:]
			} else {
				updated = strings.TrimRight(content, "\n") + entry
			}
		} else {
			updated = strings.TrimRight(content, "\n") + "\n\n## Decision\n" + entry
		}

		if err := os.WriteFile(path, []byte(updated), 0644); err != nil {
			return fmt.Errorf("failed to write tracker file %s: %w", path, err)
		}

		fmt.Printf("Decision recorded: %s — %s (%s by %s)\n", domain, decision, datestamp, hostname)
		fmt.Printf("  File: %s\n", path)
		return nil
	},
}

func init() {
	// domainCmd visible — domain management is user-facing
	// domain list flags
	domainListCmd.Flags().Bool("active", false, "Show only active domains")

	// domain create flags
	domainCreateCmd.Flags().String("key", "", "Domain key (e.g., codeswiftr-com)")
	domainCreateCmd.Flags().String("name", "", "Domain display name")

	// domain update flags
	domainUpdateCmd.Flags().Int("score", 0, "Domain score (0-100)")
	domainUpdateCmd.Flags().String("status", "", "Domain status (active, inactive, archived)")

	// Add commands to domain noun
	domainCmd.AddCommand(domainListCmd)
	domainCmd.AddCommand(domainShowCmd)
	domainCmd.AddCommand(domainCreateCmd)
	domainCmd.AddCommand(domainUpdateCmd)
	domainCmd.AddCommand(domainValidateCmd)
	domainCmd.AddCommand(domainAssumptionsCmd)
	domainCmd.AddCommand(domainDecideCmd)
}
