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

Task type routing: coverage|test→kimi*, research|audit|analysis→gemini*, refactor|multi-file|feature→codex*,
docs|content|runbook→minimax*, edit|format|quick→glm*, plan|recommend→pi*. Default: first available.`,
		RunE: runDispatchAuto,
	}
	autoCmd.Flags().String("task-type", "", "Task type hint for agent selection (coverage, research, refactor, docs, edit, plan, etc.)")

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

	dispatch.AddCommand(sendCmd)
	dispatch.AddCommand(autoCmd)
	dispatch.AddCommand(statusCmd)
	dispatch.AddCommand(showCmd)
	dispatch.AddCommand(cleanCmd)
	return dispatch
}

// taskTypeToAgentPrefix maps task-type hint to preferred agent name prefix (CLAUDE.md capability table).
func taskTypeToAgentPrefix(taskType string) string {
	switch strings.ToLower(strings.TrimSpace(taskType)) {
	case "coverage", "test":
		return "kimi"
	case "research", "audit", "analysis":
		return "gemini"
	case "refactor", "multi-file", "feature":
		return "codex"
	case "docs", "content", "runbook":
		return "minimax"
	case "edit", "format", "quick":
		return "glm"
	case "plan", "recommend":
		return "pi"
	default:
		return ""
	}
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
	tmuxSession := "forge"

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

	prefix := taskTypeToAgentPrefix(taskType)
	var agentID string
	if prefix != "" {
		for _, a := range agentList.Agents {
			// Prefer online/idle; accept any status so we have a fallback
			if strings.HasPrefix(strings.ToLower(a.ID), prefix) {
				agentID = a.ID
				break
			}
		}
	}
	if agentID == "" {
		agentID = agentList.Agents[0].ID
	}

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

	out := io.Writer(os.Stdout)
	if root := cmd.Root(); root != nil {
		out = root.OutOrStdout()
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
	return w.Flush()
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
