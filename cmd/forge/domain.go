package main

import (
	"fmt"
	"os"
	"path/filepath"

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

// DomainRegistryYAML represents the domain registry from domains.yaml
type DomainRegistryYAML struct {
	Domains map[string]internal.Domain `yaml:"domains"`
}

// loadDomains loads domains from the domains.yaml file.
// Search order:
//  1. $FORGE_ROOT/harness/forge_harness/domains.yaml  (FORGE_ROOT env var)
//  2. config/domains.yaml  (relative to working directory — for packaged installs)
//  3. harness/forge_harness/domains.yaml  (relative to working directory)
//  4. .forge/domains.yaml  (local override)
func loadDomains() ([]internal.Domain, error) {
	var locations []string

	if forgeRoot := os.Getenv("FORGE_ROOT"); forgeRoot != "" {
		locations = append(locations, filepath.Join(forgeRoot, "harness", "forge_harness", "domains.yaml"))
	}
	locations = append(locations,
		"config/domains.yaml",
		"harness/forge_harness/domains.yaml",
		".forge/domains.yaml",
	)

	var data []byte

	for _, loc := range locations {
		var err error
		data, err = os.ReadFile(loc)
		if err == nil {
			break
		}
	}

	if data == nil {
		// No domains.yaml found — return empty list gracefully
		return []internal.Domain{}, nil
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

func init() {
	// domain list flags
	domainListCmd.Flags().Bool("active", false, "Show only active domains")

	// domain create flags
	domainCreateCmd.Flags().String("key", "", "Domain key (e.g., codeswiftr-com)")
	domainCreateCmd.Flags().String("name", "", "Domain display name")

	// Add commands to domain noun
	domainCmd.AddCommand(domainListCmd)
	domainCmd.AddCommand(domainShowCmd)
	domainCmd.AddCommand(domainCreateCmd)
}
