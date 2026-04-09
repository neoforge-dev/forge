package main

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
)

// Gate represents a human gate blocking revenue in the portfolio.
type Gate struct {
	ID       string // e.g. "GATE-VC"
	Duration string // e.g. "15 min"
	What     string // short description
	Domain   string // domain key
	Product  string // product name
	Status   string // PENDING, REMOVED, DONE
	Source   string // file the gate was found in
}

// ValidationItem represents a validation requirement that needs human interviews.
type ValidationItem struct {
	Code        string // short code e.g. "IS", "VC"
	Description string // e.g. "5 engineer interviews (Day 15 GSC check)"
}

// gateCmd is the top-level `forge gate` command.
var gateCmd = &cobra.Command{
	Use:   "gate",
	Short: "Human gates blocking revenue",
	Long: `Gates are human actions required to unblock revenue generation.

Unlike agent tasks, gates require direct human intervention (API keys,
App Store accounts, deploys, etc.) and cannot be automated.

Examples:
  # Show all blocking gates
  forge gate status`,
}

// gateStatusCmd is `forge gate status`.
var gateStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show all human gates blocking revenue",
	Long: `Show all human gates across the portfolio that require action.

Reads domain contexts (.forge/context/*/lead-context.md) and
config/domains.yaml to build a prioritized list of human actions.

Output sections:
  BLOCKING REVENUE   — gates that, when resolved, unlock deploys or revenue
  VALIDATION         — interviews and human validation studies needed`,
	RunE: gateStatusCmdImpl,
}

func init() {
	gateCmd.AddCommand(gateStatusCmd)
}

// gateStatusCmdImpl is the RunE handler for `forge gate status`.
func gateStatusCmdImpl(cmd *cobra.Command, args []string) error {
	root := forgeRootDir()

	// Load domain→product mapping from domains.yaml.
	domainProducts, err := loadDomainProducts(root)
	if err != nil {
		// Non-fatal: we can still show gates without product names.
		domainProducts = map[string]string{}
	}

	// Collect gates from all lead-context.md files.
	gates, err := collectGates(root, domainProducts)
	if err != nil {
		return fmt.Errorf("failed to collect gates: %w\n  Recovery: verify .forge/context/ exists under FORGE_ROOT", err)
	}

	// Collect validation items from context files.
	validations := collectValidations(root)

	printGateStatus(gates, validations)
	return nil
}

// forgeRootDir returns the FORGE repository root.
// Respects FORGE_ROOT env var; falls back to the current working directory.
func forgeRootDir() string {
	if root := os.Getenv("FORGE_ROOT"); root != "" {
		return root
	}
	return findForgeRoot()
}

// domainProductsYAML is used to unmarshal just enough of domains.yaml.
type domainProductsYAML struct {
	Domains map[string]struct {
		DisplayName string   `yaml:"display_name"`
		Active      bool     `yaml:"active"`
		Products    []string `yaml:"products"`
		Business    struct {
			DeployStatus       string   `yaml:"deploy_status"`
			HumanActionsNeeded []string `yaml:"human_actions_needed"`
		} `yaml:"business"`
	} `yaml:"domains"`
}

// loadDomainProducts returns a map of domainKey→firstProduct (display name or key).
func loadDomainProducts(root string) (map[string]string, error) {
	locations := []string{
		filepath.Join(root, "config", "domains.yaml"),
		filepath.Join(root, ".forge", "domains.yaml"),
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
		return nil, fmt.Errorf("no domains.yaml found")
	}

	var registry domainProductsYAML
	if err := yaml.Unmarshal(data, &registry); err != nil {
		return nil, fmt.Errorf("failed to parse domains.yaml: %w", err)
	}

	result := make(map[string]string, len(registry.Domains))
	for key, d := range registry.Domains {
		if len(d.Products) > 0 {
			result[key] = d.Products[0]
		} else {
			result[key] = d.DisplayName
		}
	}
	return result, nil
}

// gateLineRe matches lines that contain a GATE identifier.
// Handles patterns like:
//   - GATE-VC (15 min) — description
//   - **GATE-VC**: description
//   - GATE-VC: description (15 min)
//   - | GATE-VC | 15 min | description | STATUS |
var gateLineRe = regexp.MustCompile(`GATE-([A-Z0-9_-]+)`)

// gateDurationRe extracts a duration hint from a line, e.g. "(15 min)".
var gateDurationRe = regexp.MustCompile(`\((\d+\s*min(?:utes?)?)\)`)

// gateStatusRe detects explicit status tokens (REMOVED, DONE, ~~...~~).
var gateRemovedRe = regexp.MustCompile(`~~GATE-[A-Z0-9_-]+~~|REMOVED|KILLED|DONE`)

// collectGates walks .forge/context/*/lead-context.md files and extracts gates.
// It deduplicates by gate ID, preferring the most informative entry.
func collectGates(root string, domainProducts map[string]string) ([]Gate, error) {
	contextDir := filepath.Join(root, ".forge", "context")
	entries, err := os.ReadDir(contextDir)
	if err != nil {
		return nil, fmt.Errorf("cannot read %s: %w", contextDir, err)
	}

	// domainKey → []Gate (one per gate ID found, deduped later)
	seen := map[string]*Gate{} // gate ID → best Gate found so far

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		// Skip symlinks to avoid double-counting.
		info, err := entry.Info()
		if err != nil {
			continue
		}
		if info.Mode()&os.ModeSymlink != 0 {
			continue
		}

		domainKey := entry.Name()
		// Skip infrastructure/node context dirs.
		if isInfraDir(domainKey) {
			continue
		}

		contextFile := filepath.Join(contextDir, domainKey, "lead-context.md")
		content, err := os.ReadFile(contextFile)
		if err != nil {
			continue // no lead-context.md — skip
		}

		gates := parseGatesFromContent(string(content), domainKey, contextFile, domainProducts)
		for _, g := range gates {
			if existing, ok := seen[g.ID]; !ok {
				gCopy := g
				seen[g.ID] = &gCopy
			} else {
				// Prefer PENDING over REMOVED; prefer entries with more detail.
				if g.Status == "PENDING" && existing.Status != "PENDING" {
					gCopy := g
					seen[g.ID] = &gCopy
				}
			}
		}
	}

	// Also scan the forge infra context for the authoritative gate table.
	forgeContext := filepath.Join(contextDir, "forge", "lead-context.md")
	if data, err := os.ReadFile(forgeContext); err == nil {
		gates := parseGatesFromContent(string(data), "forge", forgeContext, domainProducts)
		for _, g := range gates {
			if existing, ok := seen[g.ID]; !ok {
				gCopy := g
				seen[g.ID] = &gCopy
			} else if g.Status == "PENDING" && existing.Status != "PENDING" {
				gCopy := g
				seen[g.ID] = &gCopy
			}
		}
	}

	// Flatten and sort: PENDING first, then by ID.
	var result []Gate
	for _, g := range seen {
		result = append(result, *g)
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Status != result[j].Status {
			return result[i].Status == "PENDING"
		}
		return result[i].ID < result[j].ID
	})
	return result, nil
}

// infraDirs are context dirs that represent nodes/infra, not portfolio domains.
var infraDirs = map[string]bool{
	"forge": false, // handled separately
	"prya":  true,
	"sati":  true,
	"nova":  true,
	"vega":  true,
	"gaea":  true,
	"oc":    true,
}

func isInfraDir(name string) bool {
	v, ok := infraDirs[name]
	return ok && v
}

// parseGatesFromContent extracts Gate entries from markdown content.
func parseGatesFromContent(content, domainKey, sourcePath string, domainProducts map[string]string) []Gate {
	var gates []Gate
	lines := strings.Split(content, "\n")

	product := domainProducts[domainKey]

	for _, line := range lines {
		matches := gateLineRe.FindAllStringSubmatch(line, -1)
		if len(matches) == 0 {
			continue
		}

		// Check for explicit removal markers on this line.
		isRemoved := gateRemovedRe.MatchString(line)

		for _, m := range matches {
			gateID := "GATE-" + m[1]

			// Extract duration.
			duration := ""
			if dm := gateDurationRe.FindStringSubmatch(line); len(dm) > 1 {
				duration = dm[1]
			}

			// Extract description: everything after the gate ID and optional duration.
			what := extractGateDescription(line, gateID)

			status := "PENDING"
			if isRemoved || strings.Contains(line, "~~"+gateID+"~~") {
				status = "REMOVED"
			}
			// Table format: look for a STATUS column value.
			if strings.Contains(line, "| REMOVED") || strings.Contains(line, "| DONE") {
				status = "REMOVED"
			}

			gates = append(gates, Gate{
				ID:       gateID,
				Duration: duration,
				What:     what,
				Domain:   domainKey,
				Product:  product,
				Status:   status,
				Source:   sourcePath,
			})
		}
	}
	return gates
}

// extractGateDescription pulls a human-readable description from a gate line.
// It strips the gate ID, duration hints, markdown syntax, and table delimiters.
func extractGateDescription(line, gateID string) string {
	// Remove the gate ID itself (including bold wrappers like **GATE-VC**).
	s := regexp.MustCompile(`\*?\*?`+regexp.QuoteMeta(gateID)+`\*?\*?`).ReplaceAllString(line, "")

	// Remove table-style pipes and surrounding cells.
	// Typical: | GATE-VC | 15 min | description | STATUS |
	s = strings.ReplaceAll(s, "|", " ")

	// Remove markdown bold (**text**) and strikethrough (~~text~~).
	s = regexp.MustCompile(`\*\*([^*]*)\*\*`).ReplaceAllString(s, "$1")
	s = regexp.MustCompile(`~~[^~]*~~`).ReplaceAllString(s, "")

	// Remove bullet list markers and numbered list prefixes (e.g. "1. ").
	s = regexp.MustCompile(`^\s*\d+\.\s*`).ReplaceAllString(s, "")
	s = strings.TrimLeft(s, "-* \t")

	// Remove duration hints like "(15 min)" or "(30 minutes)".
	s = gateDurationRe.ReplaceAllString(s, "")

	// Remove time-only fragments like "15 min" or "5 min" that stand alone.
	s = regexp.MustCompile(`\b\d+\s*min(?:utes?)?\b`).ReplaceAllString(s, "")

	// Remove trailing status tokens.
	for _, tok := range []string{"PENDING", "REMOVED", "DONE", "BLOCKED"} {
		s = strings.ReplaceAll(s, tok, "")
	}

	// Remove em-dash, colon, and separator characters at the start.
	s = strings.TrimLeft(s, " \t:—-–")
	s = strings.TrimRight(s, " \t|")

	// Collapse runs of whitespace.
	s = regexp.MustCompile(`\s{2,}`).ReplaceAllString(s, " ")
	return strings.TrimSpace(s)
}

// validationPatterns are regexes that identify interview/validation requirements.
// Each entry maps a short code to a description pattern.
var validationPatterns = []struct {
	code    string
	pattern *regexp.Regexp
}{
	{"IS", regexp.MustCompile(`(?i)engineer\s+interview|IS.*interview`)},
	{"VC", regexp.MustCompile(`(?i)presenter\s+interview|VC.*interview|voice\s+coach.*interview`)},
	{"BB", regexp.MustCompile(`(?i)parent\s+interview|BB.*interview|babybites.*interview`)},
	{"CC", regexp.MustCompile(`(?i)therapist\s+interview|CC.*interview|calmconnect.*interview`)},
	{"KC", regexp.MustCompile(`(?i)parent.*kindly|kindly.*interview`)},
}

// collectValidations scans context files for interview/validation gate mentions.
func collectValidations(root string) []ValidationItem {
	contextDir := filepath.Join(root, ".forge", "context")

	// Aggregate text from all non-infra lead-context.md files.
	var combined strings.Builder
	entries, _ := os.ReadDir(contextDir)
	for _, entry := range entries {
		if !entry.IsDir() || isInfraDir(entry.Name()) {
			continue
		}
		data, err := os.ReadFile(filepath.Join(contextDir, entry.Name(), "lead-context.md"))
		if err == nil {
			combined.Write(data)
			combined.WriteRune('\n')
		}
	}
	// Also check the forge infra context.
	if data, err := os.ReadFile(filepath.Join(contextDir, "forge", "lead-context.md")); err == nil {
		combined.Write(data)
	}

	text := combined.String()
	seen := map[string]bool{}
	var items []ValidationItem

	for _, vp := range validationPatterns {
		if seen[vp.code] {
			continue
		}
		if vp.pattern.MatchString(text) {
			desc := buildValidationDescription(vp.code)
			items = append(items, ValidationItem{Code: vp.code, Description: desc})
			seen[vp.code] = true
		}
	}
	return items
}

// buildValidationDescription returns a canonical description for known validation codes.
func buildValidationDescription(code string) string {
	switch code {
	case "IS":
		return "5 engineer interviews (Day 15 GSC check)"
	case "VC":
		return "5 presenter interviews (Day 30 kill gate)"
	case "BB":
		return "5 parent interviews (Week 2 kill gate)"
	case "CC":
		return "5 therapist interviews (before TestFlight)"
	case "KC":
		return "5 parent interviews (Kids category)"
	default:
		return "interviews required"
	}
}

// printGateStatus renders the gate status output to stdout.
func printGateStatus(gates []Gate, validations []ValidationItem) {
	fmt.Println("FORGE Gate Status")
	fmt.Println("=================")

	// Show human interface node from domains.yaml fleet config.
	if gateNode := readHumanGateNode(); gateNode != "" {
		fmt.Printf("\nHuman interface node: %s (all gates route here)\n", gateNode)
	}
	fmt.Println()

	// Separate PENDING from REMOVED.
	var pending, removed []Gate
	for _, g := range gates {
		if g.Status == "PENDING" {
			pending = append(pending, g)
		} else {
			removed = append(removed, g)
		}
	}

	// BLOCKING REVENUE section.
	if len(pending) == 0 {
		fmt.Println("BLOCKING REVENUE: none — all gates resolved!")
	} else {
		fmt.Printf("BLOCKING REVENUE (%d gate", len(pending))
		if len(pending) != 1 {
			fmt.Print("s")
		}
		fmt.Println("):")

		for _, g := range pending {
			// Header line: GATE-ID (duration) — description
			if g.Duration != "" {
				fmt.Printf("  %s (%s)", g.ID, g.Duration)
			} else {
				fmt.Printf("  %s", g.ID)
			}
			if g.What != "" {
				fmt.Printf(" — %s", g.What)
			}
			fmt.Println()

			// Domain / product line.
			if g.Domain != "" && g.Domain != "forge" {
				if g.Product != "" {
					fmt.Printf("    Domain: %s | Product: %s\n", g.Domain, g.Product)
				} else {
					fmt.Printf("    Domain: %s\n", g.Domain)
				}
			}

			// Checklist hint.
			checklist := findChecklist(g.Domain, g.Product)
			if checklist != "" {
				fmt.Printf("    Checklist: %s\n", checklist)
			}
			fmt.Println()
		}
	}

	// VALIDATION section.
	if len(validations) > 0 {
		fmt.Println("VALIDATION (human interviews needed):")
		totalInterviews := 0
		for _, v := range validations {
			fmt.Printf("  %s: %s\n", v.Code, v.Description)
			totalInterviews += 5 // each validation requires 5 interviews by convention
		}
		fmt.Println()

		// Summary.
		gateMinutes := estimateGateMinutes(pending)
		interviewHours := (totalInterviews * 30) / 60 // ~30 min per interview
		fmt.Printf("Total human time: ~%d min (gates) + ~%dh (interviews)\n", gateMinutes, interviewHours)
	} else if len(pending) > 0 {
		gateMinutes := estimateGateMinutes(pending)
		fmt.Printf("Total human time: ~%d min (gates)\n", gateMinutes)
	}

	// Resolved gates footer (quiet, for completeness).
	if len(removed) > 0 {
		fmt.Println()
		fmt.Printf("Resolved / removed gates: ")
		ids := make([]string, 0, len(removed))
		for _, g := range removed {
			ids = append(ids, g.ID)
		}
		fmt.Println(strings.Join(ids, ", "))
	}
}

// durationMinutesRe extracts the numeric part of a duration string like "15 min".
var durationMinutesRe = regexp.MustCompile(`(\d+)`)

// estimateGateMinutes sums up duration hints across pending gates.
// Returns 0 if no hints were found.
func estimateGateMinutes(gates []Gate) int {
	total := 0
	for _, g := range gates {
		if m := durationMinutesRe.FindString(g.Duration); m != "" {
			n := 0
			fmt.Sscanf(m, "%d", &n)
			total += n
		} else {
			total += 15 // default estimate when no duration is specified
		}
	}
	return total
}

// findChecklist looks for a DEPLOY_CHECKLIST.md near the given domain/product.
// Returns the relative path if found, empty string otherwise.
func findChecklist(domain, product string) string {
	root := forgeRootDir()
	if root == "" {
		root = "."
	}

	candidates := []string{}
	if product != "" {
		candidates = append(candidates,
			filepath.Join("services", product, "DEPLOY_CHECKLIST.md"),
			filepath.Join("services", product, "backend", "DEPLOY_CHECKLIST.md"),
			filepath.Join("apps", product, "DEPLOY_CHECKLIST.md"),
		)
	}
	if domain != "" {
		candidates = append(candidates,
			filepath.Join("services", domain, "DEPLOY_CHECKLIST.md"),
		)
	}

	for _, rel := range candidates {
		abs := filepath.Join(root, rel)
		if _, err := os.Stat(abs); err == nil {
			return rel
		}
	}
	return ""
}

// readHumanGateNode reads fleet.human_gate_node from config/domains.yaml.
func readHumanGateNode() string {
	// Try repo root (may be invoked from subdirectory).
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		// Walk up to find config/domains.yaml
		if dir, err := os.Getwd(); err == nil {
			for d := dir; d != "/"; d = filepath.Dir(d) {
				if _, err := os.Stat(filepath.Join(d, "config", "domains.yaml")); err == nil {
					root = d
					break
				}
			}
		}
	}
	candidates := []string{
		"config/domains.yaml",
		filepath.Join(root, "config", "domains.yaml"),
	}
	var data []byte
	var err error
	for _, path := range candidates {
		data, err = os.ReadFile(path)
		if err == nil {
			break
		}
	}
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(data), "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "human_gate_node:") {
			val := strings.TrimSpace(strings.TrimPrefix(trimmed, "human_gate_node:"))
			// Strip inline YAML comments.
			if idx := strings.Index(val, "#"); idx > 0 {
				val = strings.TrimSpace(val[:idx])
			}
			return val
		}
	}
	return ""
}
