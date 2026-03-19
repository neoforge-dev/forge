package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// dispatchCmd is the workflow command for sending work to agents.
var dispatchCmd = NewDispatchCmd()

// NewDispatchCmd returns a new dispatch command tree (used by tests to avoid flag reuse).
func NewDispatchCmd() *cobra.Command {
	dispatch := &cobra.Command{
		Use:   "dispatch",
		Short: "Send work to agents",
		Long: `Dispatch tasks to FORGE agents.

  forge dispatch send forge:kimi "Research FastAPI auth patterns"
  forge dispatch send kimi --file .forge/dispatches/auth-research.md
  forge dispatch show 01JQM123ABC
`,
	}
	sendCmd := &cobra.Command{
		Use:   "send [agent] [message]",
		Short: "Send a task to an agent",
		Long: `Create a task, claim it for the given agent, and notify via tmux.

The tmux notification is required for Claude Code agents which don't poll
SQLite — they only see work when a message appears in their window.

Examples:
  forge dispatch send kimi "Research FastAPI auth patterns"
  forge dispatch send forge:kimi --file .forge/dispatches/task.md
  forge dispatch send kimi "task" --no-tmux    # CI / polling agents only`,
		RunE: runDispatchSend,
	}
	sendCmd.Flags().String("file", "", "Read message from file (e.g. .forge/dispatches/task.md)")
	sendCmd.Flags().String("domain", "forge", "Domain for the task")
	sendCmd.Flags().String("project", "dispatch", "Project for the task")
	sendCmd.Flags().String("priority", "medium", "Task priority (low, medium, high, critical)")
	sendCmd.Flags().String("tmux-session", "forge", "tmux session name for agent notification (set empty to skip)")
	sendCmd.Flags().Bool("no-tmux", false, "Skip tmux notification (useful in CI or when agents poll via forge work --daemon)")
	sendCmd.Flags().Bool("wait-ack", false, "Poll relay deliveries for ACK confirmation (30s timeout)")

	autoCmd := &cobra.Command{
		Use:   "auto [message]",
		Short: "Dispatch to best agent by task type",
		Long: `Smart agent selection by task type. Reads agent list from daemon and sends dispatch.

Task type routing: coverage|test→kimi*, research|audit|analysis|plan→gemini*, refactor|multi-file|feature→claude,
docs|content|runbook→minimax*, implementation|scaffold→glm*, edit|format|quick|triage→pi*. Default: first available.`,
		RunE: runDispatchAuto,
	}
	autoCmd.Flags().String("task-type", "", "Task type hint for agent selection (coverage, research, refactor, docs, edit, plan, etc.)")
	autoCmd.Flags().String("domain", "", "Domain name for portfolio-stage-aware routing (reads portfolio-state.yaml)")

	statusCmd := &cobra.Command{
		Use:   "status",
		Short: "Show dispatch feedback (outbox + relay)",
		Long:  "Show what was sent to whom and status. Reads .forge/xnode/lead-outbox/*.jsonl and relay deliveries.",
		RunE:  runDispatchStatus,
	}
	statusCmd.Flags().String("agent", "", "Filter by agent name")

	showCmd := &cobra.Command{
		Use:   "show [task-id]",
		Short: "Show dispatch (task) status",
		Long:  "Display task details for a dispatched task (same as forge task show).",
		Args:  cobra.ExactArgs(1),
		RunE:  runDispatchShow,
	}

	cleanCmd := &cobra.Command{
		Use:   "clean",
		Short: "Archive stale dispatch files",
		Long: `Archive dispatch files older than 7 days that have results,
or files older than 30 days regardless of result status.
Moves files to .forge/dispatches/archive/ directory.`,
		RunE: runDispatchClean,
	}
	cleanCmd.Flags().Bool("dry-run", false, "Show what would be archived without moving files")

	checkResultsCmd := &cobra.Command{
		Use:   "check-results",
		Short: "Quality-gate result files: flag empty, tiny, or structure-missing files",
		Long: `Scan .forge/heartbeat/results/ and flag files that fail quality checks:
  - Too small (below --min-bytes, default 100)
  - Missing status indicator (## Status:, Status:, ✅, ❌, COMPLETE, BLOCKED, FAILED)
  - Exits with code 1 if any files fail (useful in CI / patrol)

Examples:
  forge dispatch check-results
  forge dispatch check-results --min-bytes 50
  forge dispatch check-results --since 2h`,
		RunE: runDispatchCheckResults,
	}
	checkResultsCmd.Flags().Int("min-bytes", 100, "Minimum file size in bytes")
	checkResultsCmd.Flags().Duration("since", 0, "Only check files newer than this duration (e.g. 2h, 30m)")

	reassignCmd := &cobra.Command{
		Use:   "reassign-stale",
		Short: "Re-dispatch tasks whose dispatch files have no result after the timeout",
		Long: `Find .forge/dispatches/*.md files older than --timeout with no result file,
then re-dispatch each to a new agent via 'forge dispatch auto'.
Archives the old dispatch file after reassignment.

Use --dry-run to see what would be reassigned without making changes.

Examples:
  forge dispatch reassign-stale
  forge dispatch reassign-stale --timeout 1h
  forge dispatch reassign-stale --dry-run`,
		RunE: runDispatchReassignStale,
	}
	reassignCmd.Flags().Duration("timeout", 2*time.Hour, "Reassign dispatches with no result after this duration")
	reassignCmd.Flags().Bool("dry-run", false, "Show what would be reassigned without making changes")

	dispatch.AddCommand(sendCmd)
	dispatch.AddCommand(autoCmd)
	dispatch.AddCommand(statusCmd)
	dispatch.AddCommand(showCmd)
	dispatch.AddCommand(cleanCmd)
	dispatch.AddCommand(checkResultsCmd)
	dispatch.AddCommand(reassignCmd)
	return dispatch
}

// taskTypeToAgentPrefix maps task-type hint to preferred agent name prefix (CLAUDE.md capability table).
func taskTypeToAgentPrefix(taskType string) string {
	switch strings.ToLower(strings.TrimSpace(taskType)) {
	case "coverage", "test":
		return "kimi"
	case "research", "audit", "analysis", "plan":
		return "gemini"
	case "refactor", "multi-file", "feature":
		return "claude"
	case "docs", "content", "runbook":
		return "minimax"
	case "edit", "format", "quick", "triage":
		return "pi"
	case "implementation", "scaffold":
		return "glm"
	case "recommend":
		return "pi"
	default:
		return ""
	}
}

// lookupPortfolioStage reads portfolio-state.yaml and returns the stage for the
// given domain. Returns "" if domain is empty, file is missing, or domain not found.
func lookupPortfolioStage(domain string) string {
	if domain == "" {
		return ""
	}
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = "."
	}
	data, err := os.ReadFile(filepath.Join(root, "config", "portfolio", "portfolio-state.yaml"))
	if err != nil {
		return ""
	}
	// Simple YAML scan for portfolio-state.yaml list format:
	//   products:
	//     - key: "voice-coach"
	//       stage: "deploy"
	// Find a line containing `key: "domain"` or `key: domain`, then grab the
	// next `stage:` line in the same list item.
	lines := strings.Split(string(data), "\n")
	inEntry := false
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		// Detect entry start: `- key: "voice-coach"` or `key: voice-coach`
		keyLine := trimmed
		if strings.HasPrefix(keyLine, "- ") {
			keyLine = strings.TrimSpace(strings.TrimPrefix(keyLine, "- "))
		}
		if strings.HasPrefix(keyLine, "key:") {
			val := strings.TrimSpace(strings.TrimPrefix(keyLine, "key:"))
			val = strings.Trim(val, "\"'")
			if val == domain {
				inEntry = true
				continue
			}
			// Different key — exit any previous entry
			inEntry = false
			continue
		}
		if inEntry {
			if strings.HasPrefix(trimmed, "stage:") {
				parts := strings.SplitN(trimmed, ":", 2)
				if len(parts) == 2 {
					return strings.Trim(strings.TrimSpace(parts[1]), "\"'")
				}
			}
			// New list item (starts with "- ") → exit entry
			if strings.HasPrefix(trimmed, "- ") {
				inEntry = false
			}
		}
	}
	return ""
}

// parseAgentID normalizes "forge:kimi" or "kimi" to "kimi".
func parseAgentID(s string) string {
	s = strings.TrimSpace(s)
	if strings.HasPrefix(s, "forge:") {
		return strings.TrimSpace(strings.TrimPrefix(s, "forge:"))
	}
	return s
}

// notifyAgentViaTmux sends the task prompt to the agent's tmux window.
// Uses the 2-step protocol (text then Enter separately) to avoid race conditions.
// Best-effort: failures are logged as warnings, not fatal errors.
func notifyAgentViaTmux(session, agentID, prompt string) error {
	target := session + ":" + agentID
	// Step 1: send the prompt text (literal, prevents escape sequence misinterpretation)
	if err := exec.Command("tmux", "send-keys", "-t", target, "-l", prompt).Run(); err != nil {
		return fmt.Errorf("tmux send-keys (text) to %s: %w", target, err)
	}
	// Step 2: send Enter as a separate call (required — appending Enter races with buffer)
	time.Sleep(100 * time.Millisecond)
	if err := exec.Command("tmux", "send-keys", "-t", target, "", "Enter").Run(); err != nil {
		return fmt.Errorf("tmux send-keys (Enter) to %s: %w", target, err)
	}
	return nil
}

func runDispatchSend(cmd *cobra.Command, args []string) error {
	filePath, _ := cmd.Flags().GetString("file")
	domain, _ := cmd.Flags().GetString("domain")
	project, _ := cmd.Flags().GetString("project")
	priority, _ := cmd.Flags().GetString("priority")
	tmuxSession, _ := cmd.Flags().GetString("tmux-session")
	noTmux, _ := cmd.Flags().GetBool("no-tmux")
	waitAck, _ := cmd.Flags().GetBool("wait-ack")
	format, _ := cmd.Flags().GetString("format")

	var agentID, message string

	if filePath != "" {
		// forge dispatch send --file path AGENT
		if len(args) < 1 {
			return fmt.Errorf("agent required when using --file (e.g. forge dispatch send --file path forge:kimi)")
		}
		agentID = parseAgentID(args[0])
		data, err := os.ReadFile(filePath)
		if err != nil {
			return fmt.Errorf("read --file: %w", err)
		}
		message = strings.TrimSpace(string(data))
		if message == "" {
			message = filePath
		}
	} else {
		// forge dispatch send AGENT MESSAGE
		if len(args) < 1 {
			return fmt.Errorf("agent and message required (e.g. forge dispatch send forge:kimi \"Task description\")")
		}
		agentID = parseAgentID(args[0])
		if len(args) < 2 {
			return fmt.Errorf("message required (e.g. forge dispatch send forge:kimi \"Task description\")")
		}
		message = strings.Join(args[1:], " ")
	}

	if agentID == "" {
		return fmt.Errorf("agent ID is required")
	}

	var deliveryID string
	if waitAck {
		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		payload := map[string]string{"agent_id": agentID, "message": message}
		resp, err := client.Post(ctx, "/relay/dispatch", payload)
		cancel()
		if err == nil {
			defer resp.Body.Close()
			if resp.StatusCode != http.StatusNotFound && resp.StatusCode != http.StatusMethodNotAllowed {
				if resp.StatusCode == http.StatusCreated {
					var out struct {
						ID string `json:"id"`
					}
					if json.NewDecoder(resp.Body).Decode(&out) == nil && out.ID != "" {
						deliveryID = out.ID
					}
				}
			}
		}
	}

	client := internal.NewClient()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	// Create task with message as subject/description
	subject := message
	if len(subject) > 200 {
		subject = subject[:197] + "..."
	}
	createReq := &internal.CreateTaskRequest{
		Subject:     subject,
		Description: message,
		Priority:    priority,
		Domain:      domain,
		Project:     project,
		Type:        "feature",
	}
	task, err := client.CreateTask(ctx, createReq)
	if err != nil {
		return fmt.Errorf("create task: %w", err)
	}

	// Claim task for agent
	_, err = client.ClaimTask(ctx, task.ID, agentID)
	if err != nil {
		return fmt.Errorf("dispatch to %s: %w", agentID, err)
	}

	// Notify agent via tmux (Claude Code agents don't poll SQLite — they need a push).
	// Two-step protocol required: text first, Enter separately.
	if !noTmux && tmuxSession != "" {
		// Build a prompt that includes the task content so the agent can act immediately.
		tmuxPrompt := message
		if filePath != "" {
			// For file-based dispatches, include the file path so the agent can re-read it.
			tmuxPrompt = fmt.Sprintf("New task %s — %s\n\nSee dispatch file: %s\n\nFull content:\n%s",
				task.ID, subject, filePath, message)
		} else {
			tmuxPrompt = fmt.Sprintf("New task %s: %s", task.ID, message)
		}
		if err := notifyAgentViaTmux(tmuxSession, agentID, tmuxPrompt); err != nil {
			// Non-fatal: agent may not be in a tmux window (e.g. polling via forge work --daemon)
			fmt.Fprintf(os.Stderr, "[warn] tmux notify failed (agent may be polling): %v\n", err)
		}
	}

	if waitAck && deliveryID != "" {
		ackCtx, ackCancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer ackCancel()
		ticker := time.NewTicker(2 * time.Second)
		defer ticker.Stop()
	pollLoop:
		for {
			select {
			case <-ackCtx.Done():
				fmt.Fprintf(os.Stderr, "[warn] ACK not received within 30s (delivery %s)\n", deliveryID)
				break pollLoop
			case <-ticker.C:
				pollCtx, pollCancel := context.WithTimeout(ackCtx, 10*time.Second)
				resp, err := client.Get(pollCtx, "/relay/deliveries?tail=50")
				pollCancel()
				if err != nil {
					continue
				}
				var out struct {
					Deliveries []struct {
						ID     string `json:"id"`
						Status string `json:"status"`
					} `json:"deliveries"`
				}
				if resp.Body != nil {
					_ = json.NewDecoder(resp.Body).Decode(&out)
					resp.Body.Close()
				}
				for _, d := range out.Deliveries {
					if d.ID == deliveryID && d.Status == "acked" {
						out := io.Writer(os.Stdout)
						if root := cmd.Root(); root != nil {
							out = root.OutOrStdout()
						}
						fmt.Fprintf(out, "ACK received for delivery %s\n", deliveryID)
						goto doneWaitAck
					}
				}
			}
		}
	doneWaitAck:
	}

	out := io.Writer(os.Stdout)
	if root := cmd.Root(); root != nil {
		out = root.OutOrStdout()
	}
	formatter := internal.NewFormatter(format, out)
	if format == "json" {
		return formatter.WriteJSON(map[string]string{
			"dispatched": task.ID,
			"agent":     agentID,
			"subject":    subject,
		})
	}
	if format != "quiet" {
		formatter.Printf("Dispatched: %s\n", task.ID)
		formatter.Printf("Agent:      %s\n", agentID)
		if !noTmux && tmuxSession != "" {
			formatter.Printf("Notified:   tmux %s:%s\n", tmuxSession, agentID)
		}
	}
	return nil
}

func runDispatchAuto(cmd *cobra.Command, args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("message required (e.g. forge dispatch auto \"run coverage wave\" --task-type coverage)")
	}
	message := strings.Join(args, " ")
	taskType, _ := cmd.Flags().GetString("task-type")
	domain, _ := cmd.Flags().GetString("domain")
	tmuxSession := "forge"

	// Look up portfolio stage for the given domain (for stage-aware routing).
	portfolioStage := lookupPortfolioStage(domain)

	client := internal.NewClient()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	agentList, err := client.ListAgents(ctx)
	if err != nil {
		return fmt.Errorf("daemon unreachable or agent list failed: %w\n  Check: forge daemon status", err)
	}
	if len(agentList.Agents) == 0 {
		return fmt.Errorf("no agents available (daemon returned empty list)")
	}

	// Try YAML-backed routing engine first (POST /api/routing/resolve).
	// Falls back to legacy prefix matching if endpoint is unavailable.
	var agentID string
	var routingReason string
	var requiredTier string
	if routeResp, routeErr := client.Post(ctx, "/api/routing/resolve", map[string]interface{}{
		"task_type":        taskType,
		"weight":           "light",
		"forbidden_nodes":  []string{},
		"portfolio_stage":  portfolioStage,
	}); routeErr == nil && routeResp != nil {
		defer routeResp.Body.Close()
		if routeResp.StatusCode == http.StatusOK {
			var resolved struct {
				Agent        string `json:"agent"`
				Node         string `json:"node"`
				Reason       string `json:"reason"`
				RequiredTier string `json:"required_tier"`
			}
			if json.NewDecoder(routeResp.Body).Decode(&resolved) == nil && resolved.Agent != "" {
				// Verify the resolved agent is in the live agent list.
				for _, a := range agentList.Agents {
					if strings.EqualFold(a.ID, resolved.Agent) || strings.HasPrefix(strings.ToLower(a.ID), strings.ToLower(resolved.Agent)) {
						agentID = a.ID
						routingReason = resolved.Reason
						requiredTier = resolved.RequiredTier
						break
					}
				}
			}
		}
	}
	if agentID == "" {
		// Legacy fallback: prefix match from CLAUDE.md capability table.
		prefix := taskTypeToAgentPrefix(taskType)
		if prefix != "" {
			for _, a := range agentList.Agents {
				if strings.HasPrefix(strings.ToLower(a.ID), prefix) {
					agentID = a.ID
					break
				}
			}
		}
	}
	if agentID == "" {
		agentID = agentList.Agents[0].ID
	}

	// Stage gate: deploy-stage domains require human notification before dispatch.
	out := io.Writer(os.Stdout)
	if root := cmd.Root(); root != nil {
		out = root.OutOrStdout()
	}
	if requiredTier == "phone" {
		fmt.Fprintf(out, "[GATE] domain=%s stage=deploy requires human approval (tier=phone)\n", domain)
		fmt.Fprintf(out, "       Review and approve via: forge approval list\n")
		fmt.Fprintf(out, "       Routing: agent=%s reason=%s\n", agentID, routingReason)
		fmt.Fprintf(out, "       Proceeding with dispatch — create approval record after task creation.\n")
	}
	_ = routingReason

	subject := message
	if len(subject) > 200 {
		subject = subject[:197] + "..."
	}
	createReq := &internal.CreateTaskRequest{
		Subject:     subject,
		Description: message,
		Priority:    "medium",
		Domain:      "forge",
		Project:     "dispatch",
		Type:        "feature",
	}
	task, err := client.CreateTask(ctx, createReq)
	if err != nil {
		return fmt.Errorf("create task: %w", err)
	}
	_, err = client.ClaimTask(ctx, task.ID, agentID)
	if err != nil {
		return fmt.Errorf("dispatch to %s: %w", agentID, err)
	}

	tmuxPrompt := fmt.Sprintf("New task %s: %s", task.ID, message)
	if err := notifyAgentViaTmux(tmuxSession, agentID, tmuxPrompt); err != nil {
		fmt.Fprintf(os.Stderr, "[warn] tmux notify failed: %v\n", err)
	}

	format, _ := cmd.Flags().GetString("format")
	formatter := internal.NewFormatter(format, out)
	if format == "json" {
		return formatter.WriteJSON(map[string]string{
			"dispatched": task.ID,
			"agent":      agentID,
			"subject":    subject,
		})
	}
	formatter.Printf("Dispatched: %s\n", task.ID)
	formatter.Printf("Agent:      %s\n", agentID)
	return nil
}

// dispatchStatusRow is one row for forge dispatch status output.
type dispatchStatusRow struct {
	Agent   string
	TaskID  string
	Message string
	Status  string
	Sent    time.Time
}

// pendingDispatchRow is one row from the .forge/dispatches/ directory scan.
type pendingDispatchRow struct {
	File      string
	Agent     string
	TaskID    string
	Age       time.Duration
	HasResult bool
}

// parseDispatchFilename extracts agent and taskID from a dispatch filename (no extension).
// Strips trailing -YYYY-MM-DD date suffix when present.
func parseDispatchFilename(base string) (agent, taskID string) {
	// Strip -YYYY-MM-DD (11 chars: dash + 10-char date)
	if len(base) > 11 {
		suffix := base[len(base)-11:]
		if suffix[0] == '-' {
			datelike := true
			for i, c := range []byte(suffix[1:]) {
				if i == 4 || i == 7 {
					if c != '-' {
						datelike = false
						break
					}
				} else if c < '0' || c > '9' {
					datelike = false
					break
				}
			}
			if datelike {
				base = base[:len(base)-11]
			}
		}
	}
	parts := strings.SplitN(base, "-", 2)
	if len(parts) == 2 {
		return parts[0], parts[1]
	}
	return base, ""
}

// readPendingDispatches scans .forge/dispatches/ for *.md files and checks if a
// corresponding result file exists in .forge/heartbeat/results/.
func readPendingDispatches(forgeRoot string) ([]pendingDispatchRow, error) {
	dispDir := filepath.Join(forgeRoot, ".forge", "dispatches")
	resultsDir := filepath.Join(forgeRoot, ".forge", "heartbeat", "results")

	entries, err := os.ReadDir(dispDir)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}

	skipDirs := map[string]bool{
		"archive": true, "idle": true,
		"batch1": true, "batch2": true,
		"v3-implementation": true, "v3-phase1-implementation": true,
	}

	now := time.Now()
	var rows []pendingDispatchRow
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".md") {
			continue
		}
		if skipDirs[e.Name()] {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}

		base := strings.TrimSuffix(e.Name(), ".md")
		agent, taskID := parseDispatchFilename(base)

		// Check for result file: AGENT-TASKID.md in results dir.
		hasResult := false
		if taskID != "" {
			resultName := agent + "-" + taskID + ".md"
			if _, serr := os.Stat(filepath.Join(resultsDir, resultName)); serr == nil {
				hasResult = true
			}
		}

		rows = append(rows, pendingDispatchRow{
			File:      e.Name(),
			Agent:     agent,
			TaskID:    taskID,
			Age:       now.Sub(info.ModTime()),
			HasResult: hasResult,
		})
	}
	return rows, nil
}

func runDispatchStatus(cmd *cobra.Command, args []string) error {
	agentFilter, _ := cmd.Flags().GetString("agent")
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}
	outboxDir := filepath.Join(forgeRoot, ".forge", "xnode", "lead-outbox")

	var rows []dispatchStatusRow

	// Outbox: read *.jsonl files, last 20 lines each
	entries, _ := os.ReadDir(outboxDir)
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".jsonl") {
			continue
		}
		agentFromFile := strings.TrimSuffix(e.Name(), ".jsonl")
		fpath := filepath.Join(outboxDir, e.Name())
		f, err := os.Open(fpath)
		if err != nil {
			continue
		}
		scanner := bufio.NewScanner(f)
		var lines []string
		for scanner.Scan() {
			lines = append(lines, scanner.Text())
		}
		f.Close()
		start := 0
		if len(lines) > 20 {
			start = len(lines) - 20
		}
		for i := start; i < len(lines); i++ {
			var ob map[string]interface{}
			if json.Unmarshal([]byte(lines[i]), &ob) != nil {
				continue
			}
			taskID := "-"
			if p, _ := ob["payload"].(map[string]interface{}); p != nil {
				if t, _ := p["task_id"].(string); t != "" {
					taskID = t
				}
			}
			msg := "-"
			if p, _ := ob["payload"].(map[string]interface{}); p != nil {
				if s, _ := p["summary"].(string); s != "" {
					msg = s
				} else if in, _ := p["intent"].(string); in != "" {
					msg = in
				}
			}
			agent := agentFromFile
			if t, _ := ob["target"].(string); t != "" {
				agent = t
			}
			sent := time.Now()
			if ts, _ := ob["ts"].(string); ts != "" {
				if t, err := time.Parse(time.RFC3339, ts); err == nil {
					sent = t
				}
			}
			if sa, _ := ob["sent_at"].(string); sa != "" {
				if t, err := time.Parse(time.RFC3339, sa); err == nil {
					sent = t
				}
			}
			rows = append(rows, dispatchStatusRow{Agent: agent, TaskID: taskID, Message: msg, Status: "pending", Sent: sent})
		}
	}

	// Relay deliveries: last 20
	client := internal.NewClient()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	resp, err := client.Get(ctx, "/relay/deliveries?tail=20")
	cancel()
	if err == nil && resp != nil {
		defer resp.Body.Close()
		if resp.StatusCode == http.StatusOK {
			var out struct {
				Deliveries []struct {
					ID        string `json:"id"`
					AgentID   string `json:"agent_id"`
					Message   string `json:"message"`
					Status    string `json:"status"`
					CreatedAt string `json:"created_at"`
				} `json:"deliveries"`
			}
			if json.NewDecoder(resp.Body).Decode(&out) == nil {
				for _, d := range out.Deliveries {
					sent := time.Now()
					if d.CreatedAt != "" {
						if t, err := time.Parse(time.RFC3339, d.CreatedAt); err == nil {
							sent = t
						}
					}
					msg := d.Message
					if len(msg) > 35 {
						msg = msg[:32] + "..."
					}
					rows = append(rows, dispatchStatusRow{Agent: d.AgentID, TaskID: "-", Message: msg, Status: d.Status, Sent: sent})
				}
			}
		}
	}

	// Sort by sent desc
	sort.Slice(rows, func(i, j int) bool { return rows[i].Sent.After(rows[j].Sent) })
	if agentFilter != "" {
		var filtered []dispatchStatusRow
		for _, r := range rows {
			if strings.EqualFold(r.Agent, agentFilter) {
				filtered = append(filtered, r)
			}
		}
		rows = filtered
	}

	out := io.Writer(os.Stdout)
	if root := cmd.Root(); root != nil {
		out = root.OutOrStdout()
	}
	w := tabwriter.NewWriter(out, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "AGENT\tTASK-ID\tMESSAGE\tSTATUS\tSENT")
	for _, r := range rows {
		msg := r.Message
		if len(msg) > 32 {
			msg = msg[:29] + "..."
		}
		sentStr := formatRelativeTime(r.Sent)
		fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\n", r.Agent, r.TaskID, msg, r.Status, sentStr)
	}
	if err := w.Flush(); err != nil {
		return err
	}

	// Dispatch files section: .forge/dispatches/*.md with result status.
	dispFiles, err := readPendingDispatches(forgeRoot)
	if err != nil {
		fmt.Fprintf(out, "\n[dispatch files] error reading dispatches: %v\n", err)
		return nil
	}
	if agentFilter != "" {
		var filtered []pendingDispatchRow
		for _, r := range dispFiles {
			if strings.EqualFold(r.Agent, agentFilter) {
				filtered = append(filtered, r)
			}
		}
		dispFiles = filtered
	}

	fmt.Fprintf(out, "\nDISPATCH FILES (%d)\n", len(dispFiles))
	if len(dispFiles) == 0 {
		fmt.Fprintln(out, "  (none)")
	} else {
		const timeoutThreshold = 2 * time.Hour
		dw := tabwriter.NewWriter(out, 0, 0, 2, ' ', 0)
		fmt.Fprintln(dw, "FILE\tAGENT\tTASK-ID\tAGE\tRESULT")
		for _, r := range dispFiles {
			resultStr := "✗ pending"
			if r.HasResult {
				resultStr = "✓ done"
			} else if r.Age > timeoutThreshold {
				resultStr = "⚠ TIMEOUT"
			}
			fmt.Fprintf(dw, "%s\t%s\t%s\t%s\t%s\n",
				r.File, r.Agent, r.TaskID, formatDuration(r.Age), resultStr)
		}
		_ = dw.Flush()
	}
	return nil
}

// formatDuration formats a duration as a human-readable age string.
func formatDuration(d time.Duration) string {
	if d < time.Minute {
		return "just now"
	}
	if d < time.Hour {
		return fmt.Sprintf("%dm", int(d.Minutes()))
	}
	if d < 24*time.Hour {
		return fmt.Sprintf("%dh", int(d.Hours()))
	}
	return fmt.Sprintf("%dd", int(d.Hours()/24))
}

func formatRelativeTime(t time.Time) string {
	d := time.Since(t)
	if d < time.Minute {
		return "just now"
	}
	if d < time.Hour {
		return fmt.Sprintf("%dm ago", int(d.Minutes()))
	}
	if d < 24*time.Hour {
		return fmt.Sprintf("%dh ago", int(d.Hours()))
	}
	return fmt.Sprintf("%dd ago", int(d.Hours()/24))
}

func runDispatchShow(cmd *cobra.Command, args []string) error {
	taskID := args[0]
	format, _ := cmd.Flags().GetString("format")

	client := internal.NewClient()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	task, err := client.GetTask(ctx, taskID)
	if err != nil {
		return fmt.Errorf("task %s: %w", taskID, err)
	}

	out := io.Writer(os.Stdout)
	if root := cmd.Root(); root != nil {
		out = root.OutOrStdout()
	}
	formatter := internal.NewFormatter(format, out)
	return formatter.FormatTask(task)
}

// resultQualityIssue describes one quality problem found in a result file.
type resultQualityIssue struct {
	File   string
	Reason string
}

// statusIndicators are patterns that indicate a well-formed result file.
var statusIndicators = []string{
	"## Status", "Status:", "COMPLETE", "BLOCKED", "FAILED",
	"✅", "❌", "## Summary", "## Deliverables",
	"Generated:", "## Tasks", "## Digest", "# FORGE",
}

// checkResultFile returns a non-empty reason string if the file fails quality checks.
func checkResultFile(path string, minBytes int) string {
	info, err := os.Stat(path)
	if err != nil {
		return "unreadable"
	}
	if info.Size() < int64(minBytes) {
		return fmt.Sprintf("too small (%d bytes < %d)", info.Size(), minBytes)
	}

	data, err := os.ReadFile(path)
	if err != nil {
		return "unreadable"
	}
	content := string(data)
	for _, indicator := range statusIndicators {
		if strings.Contains(content, indicator) {
			return "" // passes
		}
	}
	return "missing status indicator (## Status / COMPLETE / BLOCKED / FAILED / ✅ / ❌)"
}

func runDispatchCheckResults(cmd *cobra.Command, args []string) error {
	minBytes, _ := cmd.Flags().GetInt("min-bytes")
	since, _ := cmd.Flags().GetDuration("since")

	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}
	resultsDir := filepath.Join(forgeRoot, ".forge", "heartbeat", "results")

	entries, err := os.ReadDir(resultsDir)
	if os.IsNotExist(err) {
		fmt.Fprintln(os.Stdout, "No results directory found — nothing to check.")
		return nil
	}
	if err != nil {
		return fmt.Errorf("read results dir: %w", err)
	}

	var cutoff time.Time
	if since > 0 {
		cutoff = time.Now().Add(-since)
	}

	out := io.Writer(os.Stdout)
	if root := cmd.Root(); root != nil {
		out = root.OutOrStdout()
	}

	var issues []resultQualityIssue
	checked := 0
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".md") {
			continue
		}
		fpath := filepath.Join(resultsDir, e.Name())
		if since > 0 {
			info, err := e.Info()
			if err != nil || !info.ModTime().After(cutoff) {
				continue
			}
		}
		checked++
		if reason := checkResultFile(fpath, minBytes); reason != "" {
			issues = append(issues, resultQualityIssue{File: e.Name(), Reason: reason})
		}
	}

	fmt.Fprintf(out, "Checked %d result file(s)", checked)
	if since > 0 {
		fmt.Fprintf(out, " (last %s)", since)
	}
	fmt.Fprintln(out)

	if len(issues) == 0 {
		fmt.Fprintln(out, "✅ All files pass quality checks.")
		return nil
	}

	w := tabwriter.NewWriter(out, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "FILE\tISSUE")
	for _, iss := range issues {
		fmt.Fprintf(w, "%s\t%s\n", iss.File, iss.Reason)
	}
	_ = w.Flush()
	fmt.Fprintf(out, "\n❌ %d file(s) failed quality checks.\n", len(issues))
	return fmt.Errorf("%d result file(s) failed quality checks", len(issues))
}

func runDispatchClean(cmd *cobra.Command, args []string) error {
	dryRun, _ := cmd.Flags().GetBool("dry-run")
	format, _ := cmd.Flags().GetString("format")

	// Determine FORGE_ROOT
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		// Default to current directory
		forgeRoot = "."
	}

	dispatchesDir := filepath.Join(forgeRoot, ".forge", "dispatches")
	archiveDir := filepath.Join(forgeRoot, ".forge", "dispatches", "archive")
	resultsDir := filepath.Join(forgeRoot, ".forge", "heartbeat", "results")

	// Check dispatches directory exists
	if _, err := os.Stat(dispatchesDir); os.IsNotExist(err) {
		return fmt.Errorf("dispatches dir does not exist: %s", dispatchesDir)
	}

	// Create archive directory if needed
	if !dryRun {
		if err := os.MkdirAll(archiveDir, 0755); err != nil {
			return fmt.Errorf("create archive dir: %w", err)
		}
	}

	// Read dispatches directory
	dispatchFiles, err := os.ReadDir(dispatchesDir)
	if err != nil {
		return fmt.Errorf("read dispatches: %w", err)
	}

	now := time.Now()
	sevenDays := 7 * 24 * time.Hour
	thirtyDays := 30 * 24 * time.Hour

	var archived []string
	var kept []string

	// Skip these directories
	skipDirs := map[string]bool{
		"archive":                true,
		"batch1":                 true,
		"batch2":                 true,
		"idle":                   true,
		"v3-implementation":       true,
		"v3-phase1-implementation": true,
	}

	// First: process .md files directly in dispatches folder
	for _, f := range dispatchFiles {
		if f.IsDir() {
			continue
		}
		if !strings.HasSuffix(f.Name(), ".md") {
			continue
		}

		filePath := filepath.Join(dispatchesDir, f.Name())
		info, err := f.Info()
		if err != nil {
			continue
		}

		age := now.Sub(info.ModTime())

		// Check if result exists
		resultName := strings.TrimSuffix(f.Name(), ".md")
		resultPath := filepath.Join(resultsDir, resultName)
		_, resultExists := os.Stat(resultPath)

		// Archive if: (result exists AND age > 7 days) OR age > 30 days
		shouldArchive := (resultExists == nil && age > sevenDays) || age > thirtyDays

		if shouldArchive {
			archived = append(archived, filePath)
			if !dryRun {
				destPath := filepath.Join(archiveDir, f.Name())
				os.Rename(filePath, destPath)
			}
		} else {
			kept = append(kept, filePath)
		}
	}

	// Second: process subdirectories
	for _, f := range dispatchFiles {
		if !f.IsDir() {
			continue
		}
		if skipDirs[f.Name()] {
			continue
		}

		dirPath := filepath.Join(dispatchesDir, f.Name())
		subEntries, err := os.ReadDir(dirPath)
		if err != nil {
			continue
		}

		for _, sf := range subEntries {
			if sf.IsDir() || !strings.HasSuffix(sf.Name(), ".md") {
				continue
			}

			filePath := filepath.Join(dirPath, sf.Name())
			info, err := sf.Info()
			if err != nil {
				continue
			}

			age := now.Sub(info.ModTime())

			// Check if result exists
			resultName := strings.TrimSuffix(sf.Name(), ".md")
			resultPath := filepath.Join(resultsDir, resultName)
			_, resultExists := os.Stat(resultPath)

			// Archive if: (result exists AND age > 7 days) OR age > 30 days
			shouldArchive := (resultExists == nil && age > sevenDays) || age > thirtyDays

			if shouldArchive {
				archived = append(archived, filePath)
				if !dryRun {
					destPath := filepath.Join(archiveDir, f.Name(), sf.Name())
					os.MkdirAll(filepath.Join(archiveDir, f.Name()), 0755)
					os.Rename(filePath, destPath)
				}
			} else {
				kept = append(kept, filePath)
			}
		}
	}

	out := io.Writer(os.Stdout)
	if root := cmd.Root(); root != nil {
		out = root.OutOrStdout()
	}
	formatter := internal.NewFormatter(format, out)

	if format == "json" {
		return formatter.WriteJSON(map[string]interface{}{
			"archived": len(archived),
			"kept":     len(kept),
			"files":    archived,
		})
	}

	formatter.Printf("Dispatch clean complete:\n")
	formatter.Printf("  Archived: %d\n", len(archived))
	formatter.Printf("  Kept: %d\n", len(kept))
	if dryRun {
		formatter.Printf("  (dry-run - no files moved)\n")
	}
	return nil
}

func runDispatchReassignStale(cmd *cobra.Command, args []string) error {
	timeout, _ := cmd.Flags().GetDuration("timeout")
	dryRun, _ := cmd.Flags().GetBool("dry-run")

	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}

	// Read pending dispatch files.
	dispFiles, err := readPendingDispatches(forgeRoot)
	if err != nil {
		return fmt.Errorf("read dispatches: %w", err)
	}

	archiveDir := filepath.Join(forgeRoot, ".forge", "dispatches", "archive")

	out := io.Writer(os.Stdout)
	if root := cmd.Root(); root != nil {
		out = root.OutOrStdout()
	}

	reassigned := 0
	for _, r := range dispFiles {
		if r.HasResult || r.Age <= timeout {
			continue
		}

		dispPath := filepath.Join(forgeRoot, ".forge", "dispatches", r.File)
		msgBytes, err := os.ReadFile(dispPath)
		if err != nil {
			fmt.Fprintf(out, "  skip %s: cannot read (%v)\n", r.File, err)
			continue
		}
		// Extract first non-empty line as the re-dispatch message.
		msg := extractFirstLine(string(msgBytes))
		if msg == "" {
			msg = fmt.Sprintf("Re-dispatch: %s (timed out after %s)", r.File, formatDuration(r.Age))
		}

		fmt.Fprintf(out, "  reassign: %s (agent=%s, age=%s)\n", r.File, r.Agent, formatDuration(r.Age))
		fmt.Fprintf(out, "    message: %s\n", msg)

		if !dryRun {
			// Re-dispatch to best agent (excluding the original timed-out agent).
			reCmd := exec.Command("forge", "dispatch", "auto", msg)
			reCmd.Stdout = out
			reCmd.Stderr = os.Stderr
			if rerr := reCmd.Run(); rerr != nil {
				fmt.Fprintf(out, "    ⚠ redispatch failed: %v\n", rerr)
				continue
			}

			// Archive old dispatch file.
			if merr := os.MkdirAll(archiveDir, 0o755); merr == nil {
				dest := filepath.Join(archiveDir, r.File)
				_ = os.Rename(dispPath, dest)
			}
		}
		reassigned++
	}

	if reassigned == 0 {
		fmt.Fprintln(out, "No stale dispatches found.")
	} else {
		if dryRun {
			fmt.Fprintf(out, "\n%d stale dispatch(es) found (dry-run — no changes made).\n", reassigned)
		} else {
			fmt.Fprintf(out, "\n%d dispatch(es) reassigned.\n", reassigned)
		}
	}
	return nil
}

// extractFirstLine returns the first non-empty, non-comment line from text.
func extractFirstLine(text string) string {
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if line != "" && !strings.HasPrefix(line, "#") && !strings.HasPrefix(line, "---") {
			// Truncate to reasonable message length.
			if len(line) > 120 {
				line = line[:117] + "..."
			}
			return line
		}
	}
	return ""
}
