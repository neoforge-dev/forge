package main

import (
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
)

var portfolioStageOrder = []string{
	"idea",
	"validate",
	"build",
	"deploy",
	"measure",
	"monetize",
	"scale",
	"kill",
}

var portfolioCmd = &cobra.Command{
	Use:   "portfolio",
	Short: "Manage the portfolio operating loop",
	Long: `Track the idea-to-revenue operating loop for FORGE products.

The portfolio state is intentionally small and explicit:
  idea -> validate -> build -> deploy -> measure -> monetize -> scale|kill

Use this command to keep planning, routing, and product decisions tied to the
same machine-readable source of truth.`,
}

var portfolioStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show the portfolio operating-loop summary",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		state, err := loadPortfolioState()
		if err != nil {
			return err
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatPortfolioSummary(buildPortfolioSummary(state))
	},
}

var portfolioListCmd = &cobra.Command{
	Use:   "list",
	Short: "List tracked portfolio products",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")
		stageFilter, _ := cmd.Flags().GetString("stage")

		state, err := loadPortfolioState()
		if err != nil {
			return err
		}

		products := append([]internal.PortfolioProduct(nil), state.Products...)
		sort.Slice(products, func(i, j int) bool {
			left := portfolioStageIndex(products[i].Stage)
			right := portfolioStageIndex(products[j].Stage)
			if left != right {
				return left < right
			}
			return products[i].Key < products[j].Key
		})

		if stageFilter != "" {
			filtered := make([]internal.PortfolioProduct, 0, len(products))
			for _, product := range products {
				if strings.EqualFold(product.Stage, stageFilter) {
					filtered = append(filtered, product)
				}
			}
			products = filtered
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatPortfolioProducts(products)
	},
}

var portfolioShowCmd = &cobra.Command{
	Use:   "show [product-key]",
	Short: "Show portfolio product details",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		state, err := loadPortfolioState()
		if err != nil {
			return err
		}

		product, err := findPortfolioProduct(state.Products, args[0])
		if err != nil {
			return err
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatPortfolioProduct(product)
	},
}

type portfolioStateYAML struct {
	Version   string                      `yaml:"version"`
	Updated   string                      `yaml:"updated"`
	NorthStar string                      `yaml:"north_star"`
	Products  []internal.PortfolioProduct `yaml:"products"`
}

func init() {
	portfolioListCmd.Flags().String("stage", "", "Filter products by stage")

	portfolioCmd.AddCommand(portfolioStatusCmd)
	portfolioCmd.AddCommand(portfolioListCmd)
	portfolioCmd.AddCommand(portfolioShowCmd)
}

// portfolioStatePath is a candidate path for the portfolio state file, tagged with
// whether it is a legacy location that should emit a deprecation warning on use.
type portfolioStatePath struct {
	path              string
	deprecationNotice string // non-empty means emit this warning to stderr before using
}

func loadPortfolioState() (*internal.PortfolioState, error) {
	candidates := portfolioStateCandidates()
	var data []byte
	var source string
	var allPaths []string
	for _, c := range candidates {
		allPaths = append(allPaths, c.path)
		content, err := os.ReadFile(c.path)
		if err == nil {
			if c.deprecationNotice != "" {
				fmt.Fprintln(os.Stderr, c.deprecationNotice)
			}
			data = content
			source = c.path
			break
		}
	}

	if data == nil {
		return nil, fmt.Errorf("portfolio state not found; looked in %s", strings.Join(allPaths, ", "))
	}

	var raw portfolioStateYAML
	if err := yaml.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("parse portfolio state %s: %w", source, err)
	}

	state := &internal.PortfolioState{
		Version:    raw.Version,
		Updated:    raw.Updated,
		NorthStar:  raw.NorthStar,
		Products:   raw.Products,
		StageOrder: append([]string(nil), portfolioStageOrder...),
	}
	return state, nil
}

func portfolioStateCandidates() []portfolioStatePath {
	// 1. Explicit env var — honour both the new and legacy names.
	if explicit := os.Getenv("FORGE_PORTFOLIO_STATE"); explicit != "" {
		return []portfolioStatePath{{path: explicit}}
	}
	if explicit := os.Getenv("FORGE_PORTFOLIO_FILE"); explicit != "" {
		return []portfolioStatePath{{path: explicit}}
	}

	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = findForgeRoot()
	}

	type rawCandidate struct {
		rel    string
		notice string
	}
	raw := []rawCandidate{
		// 2. New canonical location (no warning).
		{"config/portfolio/portfolio-state.yaml", ""},
		// 3. Legacy docs/ location (deprecation warning).
		{
			"docs/portfolio/portfolio-state.yaml",
			"Warning: using legacy path docs/portfolio/portfolio-state.yaml — move to config/portfolio/",
		},
		// 4. Legacy .forge/ location (no warning — silent fallback).
		{".forge/portfolio/portfolio-state.yaml", ""},
	}

	seen := map[string]struct{}{}
	var candidates []portfolioStatePath
	for _, r := range raw {
		if forgeRoot != "" {
			abs := filepath.Join(forgeRoot, r.rel)
			if _, ok := seen[abs]; !ok {
				seen[abs] = struct{}{}
				candidates = append(candidates, portfolioStatePath{path: abs, deprecationNotice: r.notice})
			}
		}
		// Also include the relative path for when cwd == repo root.
		if _, ok := seen[r.rel]; !ok {
			seen[r.rel] = struct{}{}
			candidates = append(candidates, portfolioStatePath{path: r.rel, deprecationNotice: r.notice})
		}
	}
	return candidates
}

func buildPortfolioSummary(state *internal.PortfolioState) *internal.PortfolioSummary {
	summary := &internal.PortfolioSummary{
		Updated:          state.Updated,
		NorthStar:        state.NorthStar,
		ProductCount:     len(state.Products),
		StageCounts:      map[string]int{},
		PriorityProducts: make([]internal.PortfolioProduct, 0, len(state.Products)),
	}

	products := append([]internal.PortfolioProduct(nil), state.Products...)
	sort.Slice(products, func(i, j int) bool {
		left := portfolioPriorityScore(products[i])
		right := portfolioPriorityScore(products[j])
		if left != right {
			return left > right
		}
		return products[i].Key < products[j].Key
	})

	for _, product := range state.Products {
		summary.StageCounts[product.Stage]++
		if product.CurrentMRR > 0 {
			summary.RevenueProductCount++
		}
		if product.DeployReady {
			summary.DeploymentReadyCount++
		}
		if product.AnalyticsReady {
			summary.AnalyticsReadyCount++
		}
		if product.BillingReady {
			summary.BillingReadyCount++
		}
	}

	if len(products) > 3 {
		products = products[:3]
	}
	summary.PriorityProducts = products
	return summary
}

func findPortfolioProduct(products []internal.PortfolioProduct, key string) (*internal.PortfolioProduct, error) {
	for _, product := range products {
		if product.Key == key {
			found := product
			return &found, nil
		}
	}
	return nil, fmt.Errorf("portfolio product not found: %s", key)
}

func portfolioPriorityScore(product internal.PortfolioProduct) int {
	score := 0
	switch strings.ToLower(product.Stage) {
	case "measure":
		score += 70
	case "deploy":
		score += 60
	case "monetize":
		score += 55
	case "build":
		score += 45
	case "validate":
		score += 35
	case "scale":
		score += 25
	case "idea":
		score += 15
	}
	if strings.EqualFold(product.Status, "blocked") {
		score += 20
	}
	if !product.AnalyticsReady {
		score += 15
	}
	if !product.BillingReady && product.Stage != "idea" && product.Stage != "validate" {
		score += 10
	}
	if product.CurrentMRR > 0 && product.CurrentMRR < product.TargetMRR {
		score += 20
	}
	if product.CurrentMRR == 0 && (product.Stage == "deploy" || product.Stage == "measure") {
		score += 20
	}
	return score
}

func portfolioStageIndex(stage string) int {
	for index, known := range portfolioStageOrder {
		if strings.EqualFold(stage, known) {
			return index
		}
	}
	return len(portfolioStageOrder) + 1
}

func dedupeStrings(values []string) []string {
	seen := make([]string, 0, len(values))
	for _, value := range values {
		if value == "" || slices.Contains(seen, value) {
			continue
		}
		seen = append(seen, value)
	}
	return seen
}
