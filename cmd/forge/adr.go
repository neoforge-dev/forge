// adr.go — forge adr: ADR drift checker
package main

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"text/tabwriter"

	"github.com/spf13/cobra"
)

// ─── Types ────────────────────────────────────────────────────────────────────

// adrEntry holds one parsed row from docs/adr/INDEX.md.
type adrEntry struct {
	Number string
	Title  string
	Status string // raw emoji char(s)
	Notes  string
}

// adrRef is a single extracted reference from Notes.
type adrRef struct {
	Kind  string // "file", "func", "config"
	Value string
}

// adrResult is the drift result for one ADR.
type adrResult struct {
	Entry    adrEntry
	Refs     []adrRef
	Missing  []string
	Verified int
}

// ─── Regex patterns ───────────────────────────────────────────────────────────

var (
	// matches `path/to/file.go` in backticks
	reGoFile = regexp.MustCompile("`([a-zA-Z_][\\w/.]*\\.go)`")
	// matches `FuncName()` in backticks
	reFunc = regexp.MustCompile("`([a-zA-Z_][\\w]*\\(\\))`")
	// matches `config/...` paths in backticks
	reConfig = regexp.MustCompile("`(config/[\\w/.-]+)`")
	// ADR row: starts with | [NNN](...) |
	reADRRow = regexp.MustCompile(`^\|\s*\[(\d+)\]\([^)]+\)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|?\s*$`)
)

// ─── Parsing ──────────────────────────────────────────────────────────────────

// parseADRIndex reads docs/adr/INDEX.md and returns all ADR rows.
func parseADRIndex(indexPath string) ([]adrEntry, error) {
	f, err := os.Open(indexPath)
	if err != nil {
		return nil, fmt.Errorf("open %s: %w", indexPath, err)
	}
	defer f.Close()

	var entries []adrEntry
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		entry, ok := parseADRRow(line)
		if ok {
			entries = append(entries, entry)
		}
	}
	return entries, scanner.Err()
}

// parseADRRow parses one table row. Returns (entry, true) on success.
func parseADRRow(line string) (adrEntry, bool) {
	if !strings.HasPrefix(strings.TrimSpace(line), "| [") {
		return adrEntry{}, false
	}
	m := reADRRow.FindStringSubmatch(line)
	if m == nil {
		return adrEntry{}, false
	}
	num := strings.TrimSpace(m[1])
	title := strings.TrimSpace(m[2])
	status := strings.TrimSpace(m[3])
	notes := strings.TrimSpace(m[4])

	// Normalise status: keep only the first emoji rune cluster
	status = extractFirstEmoji(status)
	return adrEntry{Number: num, Title: title, Status: status, Notes: notes}, true
}

// extractFirstEmoji returns the leading emoji from s (handles multi-byte).
func extractFirstEmoji(s string) string {
	for _, known := range []string{"✅", "🔧", "📋", "⏸", "❌"} {
		if strings.HasPrefix(s, known) {
			return known
		}
	}
	return s
}

// ─── Ref extraction ───────────────────────────────────────────────────────────

// extractRefs pulls file paths, func names, and config paths from a notes string.
func extractRefs(notes string) []adrRef {
	var refs []adrRef
	seen := map[string]bool{}

	add := func(kind, val string) {
		key := kind + ":" + val
		if !seen[key] {
			seen[key] = true
			refs = append(refs, adrRef{Kind: kind, Value: val})
		}
	}

	for _, m := range reGoFile.FindAllStringSubmatch(notes, -1) {
		add("file", m[1])
	}
	for _, m := range reFunc.FindAllStringSubmatch(notes, -1) {
		add("func", m[1])
	}
	for _, m := range reConfig.FindAllStringSubmatch(notes, -1) {
		add("config", m[1])
	}
	return refs
}

// ─── Verification ─────────────────────────────────────────────────────────────

// verifyRef checks whether a reference exists under forgeRoot.
// Returns ("OK", true) or ("MISSING: reason", false).
func verifyRef(ref adrRef, forgeRoot string) (string, bool) {
	switch ref.Kind {
	case "file", "config":
		full := filepath.Join(forgeRoot, ref.Value)
		if _, err := os.Stat(full); err == nil {
			return "OK", true
		}
		return fmt.Sprintf("MISSING: %s", ref.Value), false

	case "func":
		// Strip trailing ()
		name := strings.TrimSuffix(ref.Value, "()")
		pattern := fmt.Sprintf("func %s", name)
		out, _ := exec.Command("grep", "-rn", pattern, filepath.Join(forgeRoot, "cmd")).CombinedOutput()
		if len(strings.TrimSpace(string(out))) > 0 {
			return "OK", true
		}
		return fmt.Sprintf("MISSING: func %s not found in cmd/", name), false
	}
	return "OK", true
}

// checkADR verifies all refs for one entry.
func checkADR(entry adrEntry, forgeRoot string, verbose bool) adrResult {
	refs := extractRefs(entry.Notes)
	result := adrResult{Entry: entry, Refs: refs}

	for _, ref := range refs {
		msg, ok := verifyRef(ref, forgeRoot)
		if verbose {
			fmt.Printf("  [%s] %s → %s\n", ref.Kind, ref.Value, msg)
		}
		if ok {
			result.Verified++
		} else {
			result.Missing = append(result.Missing, msg)
		}
	}
	return result
}

// ─── Command ──────────────────────────────────────────────────────────────────

var adrCmd = &cobra.Command{
	Use:   "adr",
	Short: "ADR management and drift checking",
	Long:  "Commands for working with Architecture Decision Records.",
}

var adrStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Check ADR drift — verify claimed artifacts exist",
	Long: `Reads docs/adr/INDEX.md, parses each ADR row, and checks whether
code artifacts referenced in the Notes column actually exist.

Exits 1 when --fail-on-drift is set and any ✅ (Implemented) ADR has missing refs.`,
	RunE: runADRStatus,
}

func init() {
	adrStatusCmd.Flags().Bool("fail-on-drift", false, "exit 1 if any ✅ ADR has missing refs")
	adrStatusCmd.Flags().Bool("verbose", false, "print every ref checked")
	adrStatusCmd.Flags().String("only", "", "check single ADR by number (e.g. 009)")
	adrCmd.AddCommand(adrStatusCmd)
}

func runADRStatus(cmd *cobra.Command, _ []string) error {
	failOnDrift, _ := cmd.Flags().GetBool("fail-on-drift")
	verbose, _ := cmd.Flags().GetBool("verbose")
	only, _ := cmd.Flags().GetString("only")

	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		// Walk up from cwd until we find docs/adr/INDEX.md
		forgeRoot = findForgeRoot()
	}

	indexPath := filepath.Join(forgeRoot, "docs", "adr", "INDEX.md")
	entries, err := parseADRIndex(indexPath)
	if err != nil {
		return fmt.Errorf("read ADR index: %w\n\nTip: set FORGE_ROOT to the repo root or run from within the repo.", err)
	}

	// Filter by --only
	if only != "" {
		var filtered []adrEntry
		for _, e := range entries {
			if e.Number == only || fmt.Sprintf("%03s", only) == e.Number {
				filtered = append(filtered, e)
			}
		}
		if len(filtered) == 0 {
			return fmt.Errorf("no ADR found with number %q", only)
		}
		entries = filtered
	}

	// Run checks
	var results []adrResult
	for _, e := range entries {
		if verbose {
			fmt.Printf("\nChecking ADR-%s %s\n", e.Number, e.Title)
		}
		results = append(results, checkADR(e, forgeRoot, verbose))
	}

	// Print table
	printADRTable(results)

	// Summary
	total := len(results)
	okCount, driftCount := 0, 0
	hasDrift := false
	for _, r := range results {
		if len(r.Missing) == 0 {
			okCount++
		} else {
			driftCount++
			if r.Entry.Status == "✅" {
				hasDrift = true
			}
		}
	}
	fmt.Printf("---\nTotal: %d ADRs scanned, %d OK, %d drift\n", total, okCount, driftCount)

	if failOnDrift && hasDrift {
		return &adrDriftError{}
	}
	return nil
}

// adrDriftError signals drift exit-1 without printing "Error: ..." to stderr.
type adrDriftError struct{}

func (e *adrDriftError) Error() string { return "drift detected in ✅ ADRs (--fail-on-drift)" }

// printADRTable writes the drift table to stdout.
func printADRTable(results []adrResult) {
	w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "ADR\tStatus\tTitle\tDrift")
	for _, r := range results {
		drift := driftSummary(r)
		shortTitle := r.Entry.Title
		if len(shortTitle) > 38 {
			shortTitle = shortTitle[:35] + "..."
		}
		fmt.Fprintf(w, "%s\t%s\t%-38s\t%s\n",
			r.Entry.Number,
			r.Entry.Status,
			shortTitle,
			drift,
		)
	}
	w.Flush()
}

// driftSummary returns the Drift column string for one result.
func driftSummary(r adrResult) string {
	total := len(r.Refs)
	if total == 0 {
		return "no refs"
	}
	if len(r.Missing) == 0 {
		return fmt.Sprintf("OK (%d/%d refs verified)", r.Verified, total)
	}
	// Return first missing item (keeps table readable)
	return fmt.Sprintf("DRIFT: %s", strings.TrimPrefix(r.Missing[0], "MISSING: "))
}

