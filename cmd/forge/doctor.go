// doctor.go — forge doctor: full health suite command
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// ANSI color codes — declared as variables so NO_COLOR can zero them at init.
var (
	colorReset  = "\033[0m"
	colorGreen  = "\033[32m"
	colorRed    = "\033[31m"
	colorYellow = "\033[33m"
)

func init() {
	// Honour the NO_COLOR convention (https://no-color.org/).
	// When the variable is set (to any value), strip all ANSI codes.
	if os.Getenv("NO_COLOR") != "" {
		colorReset = ""
		colorGreen = ""
		colorRed = ""
		colorYellow = ""
	}
}

// doctorResult holds the outcome of a single health check
type doctorResult struct {
	Name    string
	Status  string // "ok", "error", "warning", "skip"
	Detail  string
	Message string
}

// symbol returns the colored symbol for the result status
func (r doctorResult) symbol() string {
	switch r.Status {
	case "ok":
		return colorGreen + "✓" + colorReset
	case "error":
		return colorRed + "✗" + colorReset
	case "warning":
		return colorYellow + "⚠" + colorReset
	default: // skip
		return "–"
	}
}

var doctorCmd = &cobra.Command{
	Use:   "doctor",
	Short: "Run full health checks on the FORGE installation",
	Long: `Run a comprehensive health check suite:
  - Daemon connectivity and version
  - SQLite database integrity
  - Git index lock status
  - .forge/ directory structure
  - Fleet agent online count
  - Token budget endpoint availability`,
	RunE: runDoctor,
}

func runDoctor(cmd *cobra.Command, args []string) error {
	apiURL := internal.ResolveControlPlaneURL()
	format, _ := cmd.Flags().GetString("format")

	var results []doctorResult

	results = append(results, checkForgeRoot())
	results = append(results, checkDaemon(apiURL))
	results = append(results, checkSQLite())
	results = append(results, checkGitLock())
	results = append(results, checkForgeStructure())
	results = append(results, checkFleet(apiURL))
	results = append(results, checkTokenBudget(apiURL))

	if format == "json" {
		out := io.Writer(os.Stdout)
		if root := cmd.Root(); root != nil {
			out = root.OutOrStdout()
		}
		encoder := json.NewEncoder(out)
		encoder.SetIndent("", "  ")
		return encoder.Encode(results)
	}

	now := time.Now()
	fmt.Printf("FORGE Doctor — %s\n", now.Format("2006-01-02 15:04"))
	fmt.Println("================================")

	// Print results table
	nameWidth := 14
	for _, r := range results {
		name := r.Name + strings.Repeat(" ", nameWidth-len(r.Name))
		statusLabel := r.Status
		if len(statusLabel) < 5 {
			statusLabel += strings.Repeat(" ", 5-len(statusLabel))
		}
		fmt.Printf("%-16s %s  %-8s %s\n", name, r.symbol(), statusLabel, r.Detail)
		if r.Message != "" {
			fmt.Printf("                    %s  %s\n", colorYellow+"hint"+colorReset, r.Message)
		}
	}

	fmt.Println()

	// Summary
	issues := 0
	for _, r := range results {
		if r.Status == "error" {
			issues++
		}
	}
	if issues == 0 {
		fmt.Println(colorGreen + "All checks passed." + colorReset)
	} else {
		fmt.Printf(colorRed+"%d issue(s) found."+colorReset+"\n", issues)
	}

	return nil
}

// checkForgeRoot validates the FORGE_ROOT environment variable.
func checkForgeRoot() doctorResult {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		return doctorResult{
			Name:    "FORGE_ROOT",
			Status:  "warning",
			Detail:  "not set",
			Message: "FORGE_ROOT not set — using current directory",
		}
	}
	if _, err := os.Stat(root); os.IsNotExist(err) {
		return doctorResult{
			Name:    "FORGE_ROOT",
			Status:  "error",
			Detail:  "missing — " + root,
			Message: "FORGE_ROOT points to non-existent directory — fix in ~/.zshrc or ~/.profile",
		}
	}
	return doctorResult{
		Name:   "FORGE_ROOT",
		Status: "ok",
		Detail: "ok       " + root,
	}
}

// checkDaemon pings GET /health on the API URL
func checkDaemon(apiURL string) doctorResult {
	healthURL := strings.TrimRight(apiURL, "/") + "/health"
	client := &http.Client{Timeout: 4 * time.Second}

	resp, err := client.Get(healthURL)
	if err != nil {
		return doctorResult{
			Name:    "Daemon",
			Status:  "error",
			Detail:  "down — " + apiURL,
			Message: "run: forge daemon start",
		}
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return doctorResult{
			Name:    "Daemon",
			Status:  "error",
			Detail:  fmt.Sprintf("HTTP %d — %s", resp.StatusCode, apiURL),
			Message: "run: forge daemon status",
		}
	}

	// Try to parse version/uptime from health response
	body, _ := io.ReadAll(resp.Body)
	var health struct {
		Version string `json:"version"`
		Uptime  string `json:"uptime"`
		Status  string `json:"status"`
	}
	_ = json.Unmarshal(body, &health)

	detail := "running  " + apiURL
	if health.Version != "" {
		detail += " (v" + health.Version + ")"
	}
	if health.Uptime != "" {
		detail += " up " + health.Uptime
	}

	return doctorResult{
		Name:   "Daemon",
		Status: "ok",
		Detail: detail,
	}
}

// sqliteMagic is the 16-byte header that every valid SQLite3 file starts with.
var sqliteMagic = []byte("SQLite format 3\x00")

// checkSQLite verifies the SQLite DB exists and has a valid SQLite3 header.
// We avoid importing a CGo or pure-Go SQLite driver to keep the forge CLI
// dependency-free. A header check is sufficient to detect corruption caused
// by truncation or accidental overwrite.
func checkSQLite() doctorResult {
	dbPath := forgeDBPath()

	info, err := os.Stat(dbPath)
	if os.IsNotExist(err) {
		return doctorResult{
			Name:    "SQLite",
			Status:  "warning",
			Detail:  "missing — " + dbPath,
			Message: "DB will be created on first daemon start",
		}
	}
	if err != nil {
		return doctorResult{
			Name:    "SQLite",
			Status:  "error",
			Detail:  "stat failed — " + dbPath,
			Message: err.Error(),
		}
	}

	// Verify SQLite3 magic header
	f, err := os.Open(dbPath)
	if err != nil {
		return doctorResult{
			Name:    "SQLite",
			Status:  "error",
			Detail:  "cannot open — " + dbPath,
			Message: err.Error(),
		}
	}
	defer f.Close()

	header := make([]byte, len(sqliteMagic))
	n, err := io.ReadFull(f, header)
	if err != nil || n < len(sqliteMagic) {
		return doctorResult{
			Name:    "SQLite",
			Status:  "error",
			Detail:  "corrupt or too small — " + dbPath,
			Message: "file does not contain a valid SQLite3 header",
		}
	}
	for i, b := range sqliteMagic {
		if header[i] != b {
			return doctorResult{
				Name:    "SQLite",
				Status:  "error",
				Detail:  "corrupt — " + dbPath,
				Message: "invalid SQLite3 magic header",
			}
		}
	}

	sizeKB := info.Size() / 1024
	detail := fmt.Sprintf("ok       %s (%d KB)", dbPath, sizeKB)
	return doctorResult{
		Name:   "SQLite",
		Status: "ok",
		Detail: detail,
	}
}

// checkGitLock checks for a stale .git/index.lock file
func checkGitLock() doctorResult {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}
	lockPath := filepath.Join(forgeRoot, ".git", "index.lock")

	if _, err := os.Stat(lockPath); err == nil {
		return doctorResult{
			Name:    "Git lock",
			Status:  "error",
			Detail:  "locked — " + lockPath,
			Message: "remove with: rm -f " + lockPath,
		}
	}

	return doctorResult{
		Name:   "Git lock",
		Status: "ok",
		Detail: "clean",
	}
}

// checkForgeStructure verifies key .forge/ directories exist
func checkForgeStructure() doctorResult {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}

	requiredDirs := []string{
		filepath.Join(forgeRoot, ".forge", "dispatches"),
		filepath.Join(forgeRoot, ".forge", "heartbeat", "results"),
		filepath.Join(forgeRoot, ".forge", "context"),
	}

	var missing []string
	var found []string
	for _, d := range requiredDirs {
		base := filepath.Base(d)
		if _, err := os.Stat(d); os.IsNotExist(err) {
			missing = append(missing, base+"/")
		} else {
			found = append(found, base+"/")
		}
	}

	if len(missing) > 0 {
		return doctorResult{
			Name:    ".forge/",
			Status:  "warning",
			Detail:  "missing dirs: " + strings.Join(missing, " "),
			Message: "run: forge init  to create missing directories",
		}
	}

	return doctorResult{
		Name:   ".forge/",
		Status: "ok",
		Detail: "ok       " + strings.Join(found, " "),
	}
}

// checkFleet calls GET /api/agents and counts online vs total
func checkFleet(apiURL string) doctorResult {
	client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(apiURL))
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()

	resp, err := client.Get(ctx, "/agents")
	if err != nil {
		return doctorResult{
			Name:    "Fleet",
			Status:  "warning",
			Detail:  "cannot reach agents endpoint",
			Message: "daemon may be offline",
		}
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return doctorResult{
			Name:    "Fleet",
			Status:  "warning",
			Detail:  fmt.Sprintf("HTTP %d from /agents", resp.StatusCode),
		}
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return doctorResult{
			Name:    "Fleet",
			Status:  "warning",
			Detail:  "failed to read agents response",
		}
	}

	var result struct {
		Agents []agentHealthEntry `json:"agents"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return doctorResult{
			Name:    "Fleet",
			Status:  "warning",
			Detail:  "failed to parse agents response",
		}
	}

	online := 0
	for _, a := range result.Agents {
		if a.Status == "online" {
			online++
		}
	}

	total := len(result.Agents)
	detail := fmt.Sprintf("%d/%d online", online, total)
	status := "ok"
	if total == 0 {
		status = "warning"
		detail = "0 agents registered"
	}

	return doctorResult{
		Name:   "Fleet",
		Status: status,
		Detail: detail,
	}
}

// checkTokenBudget calls GET /api/token-budget; skips gracefully if missing
func checkTokenBudget(apiURL string) doctorResult {
	client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(apiURL))
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()

	resp, err := client.Get(ctx, "/token-budget")
	if err != nil {
		return doctorResult{
			Name:   "Token budget",
			Status: "skip",
			Detail: "skip     (endpoint not available)",
		}
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		return doctorResult{
			Name:   "Token budget",
			Status: "skip",
			Detail: "skip     (endpoint not available)",
		}
	}

	if resp.StatusCode != http.StatusOK {
		return doctorResult{
			Name:    "Token budget",
			Status:  "warning",
			Detail:  fmt.Sprintf("HTTP %d from /token-budget", resp.StatusCode),
		}
	}

	body, _ := io.ReadAll(resp.Body)
	var budget struct {
		Used      int     `json:"used"`
		Total     int     `json:"total"`
		Remaining int     `json:"remaining"`
		Pct       float64 `json:"pct"`
	}
	if err := json.Unmarshal(body, &budget); err != nil {
		return doctorResult{
			Name:   "Token budget",
			Status: "ok",
			Detail: "ok       (response received)",
		}
	}

	detail := fmt.Sprintf("ok       %.1f%% used (%d/%d)", budget.Pct, budget.Used, budget.Total)
	status := "ok"
	if budget.Pct > 75 {
		status = "warning"
		detail = fmt.Sprintf("%.1f%% used — CONTEXT_CRITICAL", budget.Pct)
	}

	return doctorResult{
		Name:   "Token budget",
		Status: status,
		Detail: detail,
	}
}
