// status.go — forge status: morning standup fleet health snapshot (Task #48)
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// agentHealthEntry mirrors the v3 API response for /api/agents
type agentHealthEntry struct {
	AgentID       string    `json:"agent_id"`
	Status        string    `json:"status"`
	ContextPct    float64   `json:"context_pct"`
	CurrentTaskID string    `json:"current_task_id"`
	Node          string    `json:"node"`
	LastSeen      time.Time `json:"last_seen"`
}

// taskStatusSummary holds counts from /api/tasks
type taskStatusSummary struct {
	Queued    int
	Running   int
	Completed int // last 24h
	Failed    int // last 24h
	Total     int
}

// fetchPendingApprovalCount returns the number of pending approvals from
// /api/approvals?status=pending. Returns 0 on any error (daemon offline,
// network issue, etc.) so the status command always prints something useful.
func fetchPendingApprovalCount(apiURL string) int {
	client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(apiURL))
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	approvals, err := client.ListApprovals(ctx, "pending")
	if err != nil {
		return 0
	}
	// Also count "requested" status in case the API uses that term.
	count := 0
	for _, a := range approvals {
		if a.Status == "pending" || a.Status == "requested" {
			count++
		}
	}
	// If the API already filtered to pending, len(approvals) may be the full
	// answer; use max(count, len(approvals)) to handle both cases.
	if len(approvals) > count {
		count = len(approvals)
	}
	return count
}

func init() {
	// Flags for the status command
	statusCmd.Flags().Bool("full", false, "Show full fleet standup (default is compact)")
	statusCmd.Flags().Bool("json", false, "Output as JSON (deprecated: use --format json)")
	statusCmd.Flags().String("format", "table", "Output format: table or json")
}

// detectProfile returns the operational profile based on environment.
// Profiles: "hub" (default for orchestrators), "worker" (fleet agents).
func detectProfile() string {
	if p := os.Getenv("FORGE_PROFILE"); p != "" {
		return p
	}
	switch os.Getenv("FORGE_AGENT_TYPE") {
	case "fleet":
		return "worker"
	case "orchestrator":
		return "hub"
	}
	return "hub" // default: orchestrator node
}

// statusCmdImpl is declared in main.go — we override its RunE here.
// NOTE: statusCmd var is in main.go; we register the full implementation via init().
func statusCmdImpl(cmd *cobra.Command, args []string) error {
	full, _ := cmd.Flags().GetBool("full")
	_ = full // all info shown by default; --full kept for future expansion

	// Resolve output format: --format (global persistent flag) takes precedence;
	// fall back to legacy --json bool flag for backward compatibility.
	format, _ := cmd.Flags().GetString("format")
	if format == "table" || format == "" {
		// Check if the legacy --json flag was explicitly set.
		if jsonOut, _ := cmd.Flags().GetBool("json"); jsonOut {
			format = "json"
		}
	}

	profile := detectProfile()
	now := time.Now().UTC()
	controlPlaneURL := internal.ResolveControlPlaneURL()

	// 1. Check control plane + local daemon.
	controlPlaneOnline := checkControlPlane(controlPlaneURL)
	localAPIPort := internal.ResolveAPIPort()
	localDaemonOnline := isPortListening(localAPIPort)
	pid := 0
	if localDaemonOnline {
		pid, _ = readPIDFile()
		if pid == 0 {
			pid, _ = getPIDFromPort(localAPIPort)
		}
	}

	// 2. Fetch agents
	var agents []agentHealthEntry
	var agentErr error
	if controlPlaneOnline {
		agents, agentErr = fetchAgents(controlPlaneURL)
	}

	// 3. Fetch task counts
	var taskSummary taskStatusSummary
	if controlPlaneOnline {
		taskSummary, _ = fetchTaskSummary(controlPlaneURL)
	}

	// 4. Git log last 3 commits
	commits := fetchRecentCommits(3)

	// 5. Patrol count + recent errors
	patrolCount, patrolErrors := 0, 0
	if controlPlaneOnline {
		patrolCount, patrolErrors = fetchPatrolSummary(controlPlaneURL, 1*time.Hour)
	}

	// 6. Pending dispatch files (no matching result)
	pendingDispatches := countPendingDispatches()

	// 7. Pending approvals (real count from API — not hardcoded)
	pendingApprovals := 0
	if controlPlaneOnline {
		pendingApprovals = fetchPendingApprovalCount(controlPlaneURL)
	}

	switch format {
	case "json":
		return printStatusJSON(now, controlPlaneURL, controlPlaneOnline, localDaemonOnline, pid, agents, agentErr, taskSummary, commits, patrolCount, patrolErrors, pendingDispatches, pendingApprovals)
	case "quiet":
		// Quiet mode: no output — exit 0 if healthy, exit 1 if unhealthy.
		if !controlPlaneOnline {
			os.Exit(1)
		}
		return nil
	default:
		return printStatusTable(profile, now, controlPlaneURL, controlPlaneOnline, localDaemonOnline, pid, agents, agentErr, taskSummary, commits, patrolCount, patrolErrors, pendingDispatches, pendingApprovals)
	}
}

func fetchAgents(apiURL string) ([]agentHealthEntry, error) {
	client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(apiURL))
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	resp, err := client.Get(ctx, "/agents")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("agents API returned %d", resp.StatusCode)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var result struct {
		Agents []agentHealthEntry `json:"agents"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, err
	}
	return result.Agents, nil
}

func fetchTaskSummary(apiURL string) (taskStatusSummary, error) {
	client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(apiURL))
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	resp, err := client.Get(ctx, "/tasks")
	if err != nil {
		return taskStatusSummary{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return taskStatusSummary{}, fmt.Errorf("tasks API returned %d", resp.StatusCode)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return taskStatusSummary{}, err
	}

	var result struct {
		Tasks []struct {
			Status    string    `json:"status"`
			UpdatedAt time.Time `json:"updated_at"`
		} `json:"tasks"`
		Count int `json:"count"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return taskStatusSummary{}, err
	}

	cutoff := time.Now().Add(-24 * time.Hour)
	var summary taskStatusSummary
	summary.Total = result.Count
	for _, t := range result.Tasks {
		switch t.Status {
		case "queued":
			summary.Queued++
		case "running", "assigned":
			summary.Running++
		case "completed":
			if t.UpdatedAt.After(cutoff) {
				summary.Completed++
			}
		case "failed", "abandoned":
			if t.UpdatedAt.After(cutoff) {
				summary.Failed++
			}
		}
	}
	return summary, nil
}

func fetchRecentCommits(n int) []string {
	cmd := exec.Command("git", "log", "--oneline", fmt.Sprintf("-%d", n))
	out, err := cmd.Output()
	if err != nil {
		return nil
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	var result []string
	for _, l := range lines {
		if l != "" {
			result = append(result, l)
		}
	}
	return result
}

func fetchPatrolSummary(apiURL string, window time.Duration) (count, errors int) {
	client := internal.NewClientWithURL(internal.NormalizeAPIBaseURL(apiURL))
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	resp, err := client.Get(ctx, "/patrols")
	if err != nil {
		return 0, 0
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 0, 0
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, 0
	}

	var result struct {
		Count   int `json:"count"`
		Patrols []struct {
			LastError  string    `json:"last_error"`
			LastRun    time.Time `json:"last_run"`
			ErrorCount int       `json:"error_count"`
		} `json:"patrols"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return 0, 0
	}

	cutoff := time.Now().Add(-window)
	errCount := 0
	for _, p := range result.Patrols {
		if p.LastError != "" && p.LastRun.After(cutoff) {
			errCount++
		}
	}
	return result.Count, errCount
}

// countPendingDispatches counts .forge/dispatches/*.md that have no matching result file.
func countPendingDispatches() int {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}
	dispatchDir := filepath.Join(forgeRoot, ".forge", "dispatches")
	resultsDir := filepath.Join(forgeRoot, ".forge", "heartbeat", "results")

	entries, err := os.ReadDir(dispatchDir)
	if err != nil {
		return 0
	}

	// Build set of result file basenames (without extension, lowercase)
	resultSet := map[string]bool{}
	if rEntries, err := os.ReadDir(resultsDir); err == nil {
		for _, e := range rEntries {
			if !e.IsDir() {
				base := strings.ToLower(strings.TrimSuffix(e.Name(), ".md"))
				resultSet[base] = true
			}
		}
	}

	pending := 0
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".md") {
			continue
		}
		name := strings.ToLower(strings.TrimSuffix(e.Name(), ".md"))
		// Skip archive entries
		if strings.Contains(name, "archive") || strings.Contains(name, "council") ||
			strings.Contains(name, "adr") || strings.Contains(name, "infra") ||
			strings.Contains(name, "coverage") || strings.Contains(name, "git") ||
			strings.Contains(name, "batch") || strings.Contains(name, "idle") ||
			strings.Contains(name, "df-") || strings.Contains(name, "feature") ||
			strings.Contains(name, "nova") || strings.Contains(name, "onboard") ||
			strings.Contains(name, "p0-") {
			continue
		}
		// Also accept TIMEOUT variant as a completed result
		if !resultSet[name] && !resultSet[name+"-timeout"] {
			pending++
		}
	}
	return pending
}

func checkControlPlane(apiURL string) bool {
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get(strings.TrimRight(apiURL, "/") + "/health")
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode == http.StatusOK
}

func printStatusTable(profile string, now time.Time, controlPlaneURL string, controlPlaneOnline, localDaemonOnline bool, pid int, agents []agentHealthEntry,
	agentErr error, tasks taskStatusSummary, commits []string,
	patrolCount, patrolErrors, pendingDispatches, pendingApprovals int) error {

	profileLabel := "Lead Orchestrator"
	if profile == "worker" {
		profileLabel = "Fleet Agent"
	}
	fmt.Printf("FORGE — %s — %s\n", profileLabel, now.Format("2006-01-02 15:04 UTC"))
	fmt.Printf("==========================================\n")

	if controlPlaneOnline {
		fmt.Printf("Control Plane: online (%s)\n", controlPlaneURL)
	} else {
		fmt.Printf("Control Plane: offline (%s)\n", controlPlaneURL)
		fmt.Printf("\nCheck FORGE_API_URL or run: forge config set control_plane.url %s\n", internal.DefaultControlPlaneURL)
		// Return a non-nil error so Cobra exits with code 1.
		// main() will print "Error: <msg>" — keep the message terse since the
		// recovery hint is already printed above.
		return fmt.Errorf("control plane unreachable; see hints above")
	}

	if localDaemonOnline {
		if pid > 0 {
			fmt.Printf("Local Daemon: running (localhost:%s, pid %d)\n", internal.ResolveAPIPort(), pid)
		} else {
			fmt.Printf("Local Daemon: running (localhost:%s)\n", internal.ResolveAPIPort())
		}
	} else {
		fmt.Printf("Local Daemon: offline\n")
	}

	fmt.Println()

	// Agents
	if agentErr != nil {
		fmt.Printf("Agents: (error: %v)\n", agentErr)
	} else {
		online := 0
		for _, a := range agents {
			if a.Status == "online" {
				online++
			}
		}
		fmt.Printf("Agents (%d active / %d registered):\n", online, len(agents))
		for _, a := range agents {
			taskPart := ""
			if a.CurrentTaskID != "" {
				taskPart = fmt.Sprintf("  task: %s", a.CurrentTaskID)
			}
			status := a.Status
			if len(status) < 7 {
				status = status + strings.Repeat(" ", 7-len(status))
			}
			// Color the agent ID by model prefix, node label by node name — subtle hints only.
			agentLabel := internal.ColorModel(a.AgentID, fmt.Sprintf("%-12s", a.AgentID))
			nodeLabel := ""
			if a.Node != "" {
				nodeLabel = " " + internal.ColorNode(a.Node, fmt.Sprintf("[%s]", a.Node))
			}
			fmt.Printf("  %s %s%s  ctx:%.1f%%%s\n", agentLabel, status, nodeLabel, a.ContextPct, taskPart)
		}
	}

	fmt.Println()

	// Queue
	fmt.Printf("Queue:\n")
	fmt.Printf("  queued:%d  running:%d  completed:%d (24h)  failed:%d (24h)\n",
		tasks.Queued, tasks.Running, tasks.Completed, tasks.Failed)

	// Commits
	if len(commits) > 0 {
		fmt.Printf("\nRecent Commits (%d):\n", len(commits))
		for _, c := range commits {
			fmt.Printf("  %s\n", c)
		}
	}

	// Patrols
	fmt.Printf("\nPatrols: %d active, %d errors (last 1h)\n", patrolCount, patrolErrors)

	// Pending results
	if pendingDispatches > 0 {
		fmt.Printf("\nPending Results: %d dispatch files awaiting results\n", pendingDispatches)
	}

	// Profile-aware NEXT actions
	fmt.Printf("\nNEXT:\n")
	if profile == "worker" {
		if tasks.Queued > 0 {
			fmt.Printf("  forge task claim              # %d tasks queued\n", tasks.Queued)
		} else {
			fmt.Printf("  forge work --daemon           # Autonomous claim loop\n")
		}
		fmt.Printf("  forge task list               # Browse full queue\n")
	} else {
		// hub / portfolio
		if tasks.Queued > 0 {
			fmt.Printf("  forge task list               # %d tasks queued\n", tasks.Queued)
		} else {
			fmt.Printf("  forge task create --title \"...\"  # Add work to the queue\n")
		}
		if pendingApprovals > 0 {
			fmt.Printf("  forge approval list           # %d pending approvals\n", pendingApprovals)
		} else {
			fmt.Printf("  forge approval list           # Review human gates\n")
		}
	}

	return nil
}

// statusJSON is the structured JSON output for `forge status --format json`.
type statusJSON struct {
	Timestamp    string            `json:"timestamp"`
	ControlPlane statusControlPlane `json:"control_plane"`
	Daemon       statusDaemon       `json:"daemon"`
	Agents       []statusAgent      `json:"agents"`
	Queue        statusQueue        `json:"queue"`
	Patrols      statusPatrols      `json:"patrols"`
	RecentCommits []string          `json:"recent_commits,omitempty"`
	PendingResults int              `json:"pending_results"`
	PendingApprovals int            `json:"pending_approvals"`
	AgentError   string            `json:"agent_error,omitempty"`
}

type statusControlPlane struct {
	URL    string `json:"url"`
	Status string `json:"status"`
}

type statusDaemon struct {
	PID    int    `json:"pid"`
	Status string `json:"status"`
}

type statusAgent struct {
	ID         string  `json:"id"`
	Node       string  `json:"node"`
	Status     string  `json:"status"`
	ContextPct float64 `json:"context_pct"`
}

type statusQueue struct {
	Queued      int `json:"queued"`
	Running     int `json:"running"`
	Completed24h int `json:"completed_24h"`
	Failed24h   int `json:"failed_24h"`
}

type statusPatrols struct {
	Active int `json:"active"`
	Errors int `json:"errors"`
}

func printStatusJSON(now time.Time, controlPlaneURL string, controlPlaneOnline, localDaemonOnline bool, pid int, agents []agentHealthEntry,
	agentErr error, tasks taskStatusSummary, commits []string,
	patrolCount, patrolErrors, pendingDispatches, pendingApprovals int) error {

	cpStatus := "offline"
	if controlPlaneOnline {
		cpStatus = "online"
	}
	daemonStatus := "offline"
	if localDaemonOnline {
		daemonStatus = "running"
	}

	agentList := make([]statusAgent, 0, len(agents))
	for _, a := range agents {
		agentList = append(agentList, statusAgent{
			ID:         a.AgentID,
			Node:       a.Node,
			Status:     a.Status,
			ContextPct: a.ContextPct,
		})
	}

	out := statusJSON{
		Timestamp: now.UTC().Format(time.RFC3339),
		ControlPlane: statusControlPlane{
			URL:    controlPlaneURL,
			Status: cpStatus,
		},
		Daemon: statusDaemon{
			PID:    pid,
			Status: daemonStatus,
		},
		Agents: agentList,
		Queue: statusQueue{
			Queued:       tasks.Queued,
			Running:      tasks.Running,
			Completed24h: tasks.Completed,
			Failed24h:    tasks.Failed,
		},
		Patrols: statusPatrols{
			Active: patrolCount,
			Errors: patrolErrors,
		},
		RecentCommits:    commits,
		PendingResults:   pendingDispatches,
		PendingApprovals: pendingApprovals,
	}
	if agentErr != nil {
		out.AgentError = agentErr.Error()
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(out)
}
