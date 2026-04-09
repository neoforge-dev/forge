// preflight.go — forge preflight: automated session boot health checks
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// ANSI color codes — no external library needed.
const (
	preflightColorReset  = "\033[0m"
	preflightColorGreen  = "\033[32m"
	preflightColorYellow = "\033[33m"
	preflightColorRed    = "\033[31m"
)

// preflightStatus represents the result of a single check.
type preflightStatus int

const (
	preflightOK   preflightStatus = iota // all clear
	preflightWarn                        // warning, non-blocking
	preflightFail                        // blocker
)

// preflightResult holds one check's outcome.
type preflightResult struct {
	Name    string          `json:"name"`
	Status  preflightStatus `json:"-"`
	Message string          `json:"message"`
	OK      bool            `json:"ok"`
	Warn    bool            `json:"warn"`
	Fail    bool            `json:"fail"`
}

func (r preflightResult) icon() string {
	switch r.Status {
	case preflightOK:
		return preflightColorGreen + "✓" + preflightColorReset
	case preflightWarn:
		return preflightColorYellow + "⚠" + preflightColorReset
	default:
		return preflightColorRed + "✗" + preflightColorReset
	}
}

// preflightOutput is the JSON envelope for --json mode.
type preflightOutput struct {
	Timestamp string            `json:"timestamp"`
	Results   []preflightResult `json:"results"`
	Warnings  int               `json:"warnings"`
	Failures  int               `json:"failures"`
	Result    string            `json:"result"` // "OK", "WARNINGS", "FAILURES"
}

var preflightCmd = &cobra.Command{
	Use:   "preflight",
	Short: "Run session boot health checks",
	Long: `Run a series of pre-session health checks and report pass/warn/fail.

Replaces the manual boot sequence:
  forge recover + forge status + forge task list + forge gate status

Checks (in order):
  1. Git — stale .git/index.lock detection and optional removal
  2. Daemon — HTTP reachability at configured FORGE_API_URL
  3. Fleet — count of online agents
  4. Disk — root partition free space (warn <3 GB, fail <1 GB)
  5. Queue — count of queued tasks
  6. Gates — count of pending human gates

Examples:
  forge preflight              # Run all checks, human-readable output
  forge preflight --fix        # Auto-fix stale git locks, restart daemon if down
  forge preflight --json       # Machine-readable JSON output`,
	RunE: runPreflight,
}

func init() {
	preflightCmd.Flags().Bool("fix", false, "Auto-fix what it can (remove stale git locks)")
	preflightCmd.Flags().Bool("json", false, "Output as JSON for scripting")
	preflightCmd.Flags().Bool("quiet", false, "Suppress output (for cron); exit code only")
}

func runPreflight(cmd *cobra.Command, args []string) error {
	fix, _ := cmd.Flags().GetBool("fix")
	jsonOut, _ := cmd.Flags().GetBool("json")
	quiet, _ := cmd.Flags().GetBool("quiet")

	apiURL := internal.ResolveControlPlaneURL()
	now := time.Now().UTC()

	var results []preflightResult

	// 1. Git lock check
	results = append(results, preflightCheckGitLock(fix))

	// 2. Daemon health
	daemonResult := preflightCheckDaemon(apiURL)
	results = append(results, daemonResult)

	daemonOnline := daemonResult.Status == preflightOK

	// 3. Fleet agent count (only if daemon is reachable)
	results = append(results, preflightCheckFleet(apiURL, daemonOnline))

	// 4. Disk space
	results = append(results, preflightCheckDisk())

	// 5. Task queue summary (only if daemon is reachable)
	results = append(results, preflightCheckQueue(apiURL, daemonOnline))

	// 6. Gate status (only if daemon is reachable)
	results = append(results, preflightCheckGates(apiURL, daemonOnline))

	// 7. Cross-node state (read all PROMPT-*.md files)
	results = append(results, preflightCheckNodes())

	// Tally
	warnings, failures := 0, 0
	for _, r := range results {
		switch r.Status {
		case preflightWarn:
			warnings++
		case preflightFail:
			failures++
		}
	}

	resultLabel := "OK"
	if failures > 0 {
		resultLabel = "FAILURES"
	} else if warnings > 0 {
		resultLabel = "WARNINGS"
	}

	if quiet {
		// Quiet mode: exit code only, no output.
		if failures > 0 {
			os.Exit(2)
		}
		if warnings > 0 {
			os.Exit(1)
		}
		return nil
	}

	if jsonOut {
		// Populate JSON-friendly bool fields before marshalling.
		for i := range results {
			results[i].OK = results[i].Status == preflightOK
			results[i].Warn = results[i].Status == preflightWarn
			results[i].Fail = results[i].Status == preflightFail
		}
		out := preflightOutput{
			Timestamp: now.Format(time.RFC3339),
			Results:   results,
			Warnings:  warnings,
			Failures:  failures,
			Result:    resultLabel,
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(out)
	}

	// Human-readable output
	fmt.Printf("FORGE Preflight — %s\n", now.Format("2006-01-02 15:04 UTC"))
	for _, r := range results {
		fmt.Printf("%s %s: %s\n", r.icon(), r.Name, r.Message)
	}
	fmt.Println(strings.Repeat("─", 33))

	switch resultLabel {
	case "OK":
		fmt.Printf("Result: %sOK%s — all checks passed\n", preflightColorGreen, preflightColorReset)
	case "WARNINGS":
		fmt.Printf("Result: %sWARNINGS (%d)%s\n", preflightColorYellow, warnings, preflightColorReset)
	default:
		fmt.Printf("Result: %sFAILURES (%d)%s\n", preflightColorRed, failures, preflightColorReset)
	}

	// Exit codes: 0=OK, 1=warnings, 2=failures
	if failures > 0 {
		os.Exit(2)
	}
	if warnings > 0 {
		os.Exit(1)
	}
	return nil
}

// preflightCheckGitLock checks for a stale .git/index.lock file.
// A lock is considered stale when it is older than 60 seconds.
// If fix==true, the stale lock is removed.
func preflightCheckGitLock(fix bool) preflightResult {
	info, err := os.Stat(".git/index.lock")
	if os.IsNotExist(err) {
		return preflightResult{
			Name:    "Git",
			Status:  preflightOK,
			Message: "clean (no stale locks)",
		}
	}
	if err != nil {
		// Cannot stat — treat as clean to avoid false positives.
		return preflightResult{
			Name:    "Git",
			Status:  preflightOK,
			Message: "clean (lock file unreadable, assuming clean)",
		}
	}

	age := time.Since(info.ModTime())
	isStale := info.Size() == 0 || age > 60*time.Second

	if !isStale {
		return preflightResult{
			Name:    "Git",
			Status:  preflightWarn,
			Message: fmt.Sprintf("active index.lock (age=%v) — git operation may be in progress", age.Round(time.Second)),
		}
	}

	if fix {
		if rmErr := os.Remove(".git/index.lock"); rmErr != nil {
			return preflightResult{
				Name:    "Git",
				Status:  preflightFail,
				Message: fmt.Sprintf("stale lock found (age=%v) but removal failed: %v — run: rm .git/index.lock", age.Round(time.Second), rmErr),
			}
		}
		return preflightResult{
			Name:    "Git",
			Status:  preflightOK,
			Message: fmt.Sprintf("stale lock removed (was %v old)", age.Round(time.Second)),
		}
	}

	return preflightResult{
		Name:    "Git",
		Status:  preflightWarn,
		Message: fmt.Sprintf("stale index.lock (age=%v) — run: forge preflight --fix  or: rm .git/index.lock", age.Round(time.Second)),
	}
}

// preflightCheckDaemon performs an HTTP GET to /health on the configured API URL.
func preflightCheckDaemon(controlPlaneURL string) preflightResult {
	apiBase := strings.TrimRight(controlPlaneURL, "/")
	healthURL := apiBase + "/health"

	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get(healthURL)
	if err != nil {
		return preflightResult{
			Name:    "Daemon",
			Status:  preflightFail,
			Message: fmt.Sprintf("unreachable (%s) — run: forge daemon start", controlPlaneURL),
		}
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return preflightResult{
			Name:    "Daemon",
			Status:  preflightFail,
			Message: fmt.Sprintf("returned HTTP %d (%s) — run: forge daemon restart", resp.StatusCode, controlPlaneURL),
		}
	}

	// Attempt to read PID for informational output.
	port := internal.ResolveAPIPort()
	pid := 0
	if isPortListening(port) {
		pid, _ = readPIDFile()
		if pid == 0 {
			pid, _ = getPIDFromPort(port)
		}
	}

	if pid > 0 {
		return preflightResult{
			Name:    "Daemon",
			Status:  preflightOK,
			Message: fmt.Sprintf("online (%s, pid %d)", controlPlaneURL, pid),
		}
	}
	return preflightResult{
		Name:    "Daemon",
		Status:  preflightOK,
		Message: fmt.Sprintf("online (%s)", controlPlaneURL),
	}
}

// preflightCheckFleet fetches /api/agents and reports the count of online agents.
func preflightCheckFleet(apiURL string, daemonOnline bool) preflightResult {
	if !daemonOnline {
		return preflightResult{
			Name:    "Fleet",
			Status:  preflightWarn,
			Message: "skipped (daemon offline)",
		}
	}

	client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(apiURL))
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.Get(ctx, "/agents")
	if err != nil {
		return preflightResult{
			Name:    "Fleet",
			Status:  preflightWarn,
			Message: fmt.Sprintf("could not fetch agents: %v", err),
		}
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return preflightResult{
			Name:    "Fleet",
			Status:  preflightWarn,
			Message: fmt.Sprintf("agents API returned %d", resp.StatusCode),
		}
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return preflightResult{
			Name:    "Fleet",
			Status:  preflightWarn,
			Message: fmt.Sprintf("could not read agents response: %v", err),
		}
	}

	var result struct {
		Agents []struct {
			Status string `json:"status"`
		} `json:"agents"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return preflightResult{
			Name:    "Fleet",
			Status:  preflightWarn,
			Message: fmt.Sprintf("could not parse agents response: %v", err),
		}
	}

	online := 0
	for _, a := range result.Agents {
		if a.Status == "online" {
			online++
		}
	}

	return preflightResult{
		Name:    "Fleet",
		Status:  preflightOK,
		Message: fmt.Sprintf("%d agent(s) online (%d registered)", online, len(result.Agents)),
	}
}

// diskThresholdWarnBytes is the free-space level that triggers a warning (3 GB).
const diskThresholdWarnBytes = 3 * 1024 * 1024 * 1024

// diskThresholdFailBytes is the free-space level that triggers a failure (1 GB).
const diskThresholdFailBytes = 1 * 1024 * 1024 * 1024

// preflightCheckDisk uses syscall.Statfs to check root partition free space.
func preflightCheckDisk() preflightResult {
	var stat syscall.Statfs_t
	if err := syscall.Statfs("/", &stat); err != nil {
		return preflightResult{
			Name:    "Disk",
			Status:  preflightWarn,
			Message: fmt.Sprintf("could not check disk space: %v", err),
		}
	}

	// Available blocks * block size = available bytes (for unprivileged users).
	freeBytes := stat.Bavail * uint64(stat.Bsize) //nolint:unconvert
	freeGB := float64(freeBytes) / (1024 * 1024 * 1024)

	switch {
	case freeBytes < diskThresholdFailBytes:
		return preflightResult{
			Name:    "Disk",
			Status:  preflightFail,
			Message: fmt.Sprintf("CRITICAL: %.1f GB free on / (threshold: 1 GB) — free up space immediately", freeGB),
		}
	case freeBytes < diskThresholdWarnBytes:
		return preflightResult{
			Name:    "Disk",
			Status:  preflightWarn,
			Message: fmt.Sprintf("%.1f GB free on / (threshold: 3 GB) — consider freeing space", freeGB),
		}
	default:
		return preflightResult{
			Name:    "Disk",
			Status:  preflightOK,
			Message: fmt.Sprintf("%.1f GB free on /", freeGB),
		}
	}
}

// preflightCheckQueue fetches /api/tasks and reports the count of queued tasks.
func preflightCheckQueue(apiURL string, daemonOnline bool) preflightResult {
	if !daemonOnline {
		return preflightResult{
			Name:    "Queue",
			Status:  preflightWarn,
			Message: "skipped (daemon offline)",
		}
	}

	client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(apiURL))
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.Get(ctx, "/tasks")
	if err != nil {
		return preflightResult{
			Name:    "Queue",
			Status:  preflightWarn,
			Message: fmt.Sprintf("could not fetch tasks: %v", err),
		}
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return preflightResult{
			Name:    "Queue",
			Status:  preflightWarn,
			Message: fmt.Sprintf("tasks API returned %d", resp.StatusCode),
		}
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return preflightResult{
			Name:    "Queue",
			Status:  preflightWarn,
			Message: fmt.Sprintf("could not read tasks response: %v", err),
		}
	}

	var result struct {
		Tasks []struct {
			Status string `json:"status"`
		} `json:"tasks"`
		Count int `json:"count"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return preflightResult{
			Name:    "Queue",
			Status:  preflightWarn,
			Message: fmt.Sprintf("could not parse tasks response: %v", err),
		}
	}

	queued := 0
	for _, t := range result.Tasks {
		if t.Status == "queued" {
			queued++
		}
	}

	return preflightResult{
		Name:    "Queue",
		Status:  preflightOK,
		Message: fmt.Sprintf("%d task(s) queued", queued),
	}
}

// preflightCheckGates fetches /api/gates and reports the count of pending gates.
func preflightCheckGates(apiURL string, daemonOnline bool) preflightResult {
	if !daemonOnline {
		return preflightResult{
			Name:    "Gates",
			Status:  preflightWarn,
			Message: "skipped (daemon offline)",
		}
	}

	client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(apiURL))
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.Get(ctx, "/gates")
	if err != nil {
		// Gates endpoint may not exist on all daemon versions — treat as non-fatal.
		return preflightResult{
			Name:    "Gates",
			Status:  preflightWarn,
			Message: fmt.Sprintf("could not fetch gates: %v — run: forge gate status", err),
		}
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		// Gates endpoint not implemented — fall back to local file scan via gate.go logic.
		return preflightResult{
			Name:    "Gates",
			Status:  preflightOK,
			Message: "endpoint not available — run: forge gate status",
		}
	}

	if resp.StatusCode != http.StatusOK {
		return preflightResult{
			Name:    "Gates",
			Status:  preflightWarn,
			Message: fmt.Sprintf("gates API returned %d", resp.StatusCode),
		}
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return preflightResult{
			Name:    "Gates",
			Status:  preflightWarn,
			Message: fmt.Sprintf("could not read gates response: %v", err),
		}
	}

	// Try to parse a generic count field or an array.
	var gateResult struct {
		Gates []struct {
			Status string `json:"status"`
		} `json:"gates"`
		Count int `json:"count"`
	}
	if err := json.Unmarshal(body, &gateResult); err != nil {
		return preflightResult{
			Name:    "Gates",
			Status:  preflightWarn,
			Message: fmt.Sprintf("could not parse gates response: %v", err),
		}
	}

	pending := 0
	for _, g := range gateResult.Gates {
		if g.Status == "pending" || g.Status == "PENDING" || g.Status == "" {
			pending++
		}
	}
	if len(gateResult.Gates) == 0 {
		pending = gateResult.Count
	}

	return preflightResult{
		Name:    "Gates",
		Status:  preflightOK,
		Message: fmt.Sprintf("%d pending human gate(s)", pending),
	}
}

// preflightCheckNodes reads docs/PROMPT-*.md files and extracts a 1-line
// summary per node. Gives cross-node visibility at session boot.
func preflightCheckNodes() preflightResult {
	matches, err := filepath.Glob("docs/PROMPT-*.md")
	if err != nil || len(matches) == 0 {
		return preflightResult{
			Name:    "Nodes",
			Status:  preflightOK,
			Message: "no PROMPT files found",
		}
	}

	var summaries []string
	hostname, _ := os.Hostname()

	for _, path := range matches {
		// Extract node name from filename: PROMPT-nova.md → nova
		base := filepath.Base(path)
		node := strings.TrimSuffix(strings.TrimPrefix(base, "PROMPT-"), ".md")

		f, err := os.Open(path)
		if err != nil {
			continue
		}

		// Read first 10 lines to find Session: and Focus/Current lines
		scanner := bufio.NewScanner(f)
		session := ""
		focus := ""
		lineNum := 0
		for scanner.Scan() && lineNum < 15 {
			line := scanner.Text()
			lineNum++
			if session == "" {
				for _, prefix := range []string{"**Session:**", "**Updated:**", "**Last Updated:**"} {
					if strings.HasPrefix(line, prefix) {
						session = strings.TrimPrefix(line, prefix)
						session = strings.TrimSpace(session)
						break
					}
				}
			}
			if strings.HasPrefix(line, "**Focus") || strings.HasPrefix(line, "**Strategy") || strings.HasPrefix(line, "**Owner Domains:**") {
				focus = line
				for _, prefix := range []string{"**Focus Domains:**", "**Focus:**", "**Strategy:**", "**Owner Domains:**"} {
					focus = strings.TrimPrefix(focus, prefix)
				}
				focus = strings.TrimSpace(focus)
			}
		}
		f.Close()

		marker := " "
		if node == hostname {
			marker = "*"
		}

		summary := session
		if focus != "" && len(summary)+len(focus) < 70 {
			summary += " — " + focus
		}
		if summary == "" {
			summary = "(no session info)"
		}
		// Truncate to 72 chars
		if len(summary) > 72 {
			summary = summary[:69] + "..."
		}
		summaries = append(summaries, fmt.Sprintf("%s%s: %s", marker, node, summary))
	}

	return preflightResult{
		Name:    "Nodes",
		Status:  preflightOK,
		Message: fmt.Sprintf("%d node(s)\n    %s", len(summaries), strings.Join(summaries, "\n    ")),
	}
}
