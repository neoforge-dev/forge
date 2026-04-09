package main

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// taskCmd represents the task noun
var taskCmd = &cobra.Command{
	Use:     "task",
	Aliases: []string{"t"},
	Short:   "Manage tasks - units of work",
	Long: `Tasks are the fundamental unit of work in FORGE.

Each task has:
  - ID: Unique identifier (ULID)
  - Domain: Business domain (e.g., codeswiftr-com)
  - Project: Repository within domain
  - Type: feature, bugfix, research, refactor
  - State: queued, dispatched, running, blocked, completed, failed, approved
  - Priority: Numeric (higher = more important)

Universal verbs:
  list, show, create, update, delete

Workflow verbs:
  claim      Claim a task for an agent
  complete   Mark task as completed

Examples:
  # List all queued tasks
  forge task list --state queued

  # Show task details
  forge task show 01JQM123ABC

  # Create a new task
  forge task create --domain codeswiftr-com --project interview-simulator \
    --title "Fix OAuth2 redirect" --priority high

  # Claim a task for an agent
  forge task claim 01JQM123ABC --agent kimi

  # Complete a task
  forge task complete 01JQM123ABC --result "Fixed in commit abc123"`,
}

// taskListCmd: forge task list
var taskListCmd = &cobra.Command{
	Use:   "list",
	Short: "List active tasks (queued, assigned, running)",
	Long:  "List active tasks with optional filtering by status. Use --all to include completed and failed tasks.",
	RunE: func(cmd *cobra.Command, args []string) error {
		status, _ := cmd.Flags().GetString("status")
		limit, _ := cmd.Flags().GetInt("limit")
		domain, _ := cmd.Flags().GetString("domain")
		format, _ := cmd.Flags().GetString("format")
		showAll, _ := cmd.Flags().GetBool("all")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		result, err := client.ListTasks(ctx, status, limit, domain)
		if err != nil {
			return fmt.Errorf("failed to list tasks: %w", err)
		}

		tasks := result.Tasks

		// Default: hide completed/failed unless --all or --status explicitly set
		if !showAll && status == "" {
			var active []internal.Task
			for _, t := range tasks {
				if t.Status != "completed" && t.Status != "failed" {
					active = append(active, t)
				}
			}
			tasks = active
		}

		formatter := internal.NewFormatter(format, nil)
		if err := formatter.FormatTasks(tasks); err != nil {
			return err
		}

		if format == "table" {
			formatter.Printf("\nTotal: %d tasks\n", len(tasks))
		}
		return nil
	},
}

// taskShowCmd: forge task show <id>
var taskShowCmd = &cobra.Command{
	Use:   "show [id]",
	Short: "Show task details",
	Long:  "Display detailed information about a specific task.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		taskID := args[0]
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		task, err := client.GetTask(ctx, taskID)
		if err != nil {
			return fmt.Errorf("failed to get task: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatTask(task)
	},
}

// taskCreateCmd: forge task create --title "..."
var taskCreateCmd = &cobra.Command{
	Use:   "create",
	Short: "Create a new task",
	Long:  "Create a new task in the FORGE system.",
	RunE: func(cmd *cobra.Command, args []string) error {
		title, _ := cmd.Flags().GetString("title")
		description, _ := cmd.Flags().GetString("description")
		priority, _ := cmd.Flags().GetString("priority")
		domain, _ := cmd.Flags().GetString("domain")
		// --product is the canonical flag; --project is a deprecated backward-compat alias.
		product, _ := cmd.Flags().GetString("product")
		if legacyProject, _ := cmd.Flags().GetString("project"); legacyProject != "" && product == "" {
			product = legacyProject
		}
		project := product
		taskType, _ := cmd.Flags().GetString("type")
		lane, _ := cmd.Flags().GetString("lane")
		metaSlice, _ := cmd.Flags().GetStringArray("metadata")
		format, _ := cmd.Flags().GetString("format")
		portfolioKey, _ := cmd.Flags().GetString("portfolio")

		if title == "" {
			return fmt.Errorf("--title is required")
		}

		// Route to remote node via XNode if --node is set and not local.
		targetNode, _ := cmd.Flags().GetString("node")
		if targetNode != "" {
			hostname, _ := os.Hostname()
			if targetNode != hostname {
				// Forward via XNode — build summary with full task metadata.
				summary := fmt.Sprintf("[task-forward] title: %s | domain: %s | product: %s | priority: %s",
					title, domain, product, priority)
				if description != "" {
					summary += " | desc: " + description
				}
				payload := &leadForwardPayload{
					TargetNode: targetNode,
					TaskID:     fmt.Sprintf("REMOTE-%s-%d", targetNode, time.Now().Unix()),
					Summary:    summary,
					Durable:    true,
				}
				client := internal.NewClient()
				ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
				defer cancel()
				resp, err := client.Post(ctx, "/xnode/forward", payload)
				if err != nil {
					return fmt.Errorf("forward to %s failed: %w\n  Check: forge node list", targetNode, err)
				}
				defer resp.Body.Close()
				if err := internal.CheckResponse(resp); err != nil {
					return fmt.Errorf("forward to %s rejected: %w", targetNode, err)
				}
				fmt.Printf("Forwarded to %s: %s\n", targetNode, title)
				return nil
			}
			// targetNode == local hostname → fall through to local create
		}

		// Parse key=value metadata pairs
		var metadata map[string]interface{}
		for _, kv := range metaSlice {
			parts := strings.SplitN(kv, "=", 2)
			if len(parts) != 2 {
				return fmt.Errorf("invalid --metadata format %q: expected key=value", kv)
			}
			if metadata == nil {
				metadata = make(map[string]interface{})
			}
			metadata[parts[0]] = parts[1]
		}
		if lane != "" {
			if metadata == nil {
				metadata = make(map[string]interface{})
			}
			metadata["lane"] = lane
		}

		// Resolve portfolio stage from product key, if provided.
		var portfolioStage string
		if portfolioKey != "" {
			state, err := loadPortfolioState()
			if err != nil {
				// Non-fatal: warn and continue without stage routing.
				fmt.Fprintf(os.Stderr, "[portfolio] warning: could not load portfolio state: %v\n", err)
			} else {
				product, err := findPortfolioProduct(state.Products, portfolioKey)
				if err != nil {
					fmt.Fprintf(os.Stderr, "[portfolio] warning: product %q not found: %v\n", portfolioKey, err)
				} else {
					portfolioStage = product.Stage
					// Resolve tier label for the user-facing message.
					tierLabel := portfolioStage
					switch strings.ToLower(portfolioStage) {
					case "idea", "kill":
						tierLabel += " (tier: watch)"
					case "validate", "measure", "scale":
						tierLabel += " (tier: phone)"
					case "build", "deploy", "monetize":
						tierLabel += " (tier: desktop)"
					}
					fmt.Fprintf(os.Stderr, "[portfolio] %s → stage: %s\n", portfolioKey, tierLabel)
				}
			}
		}
		if portfolioStage != "" {
			if metadata == nil {
				metadata = make(map[string]interface{})
			}
			metadata["portfolio_stage"] = portfolioStage
		}

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		req := &internal.CreateTaskRequest{
			Domain:      domain,
			Project:     project,
			Type:        taskType,
			Subject:     title,
			Description: description,
			Priority:    priority,
			Metadata:    metadata,
		}

		task, err := client.CreateTask(ctx, req)
		if err != nil {
			return fmt.Errorf("create task: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(task)
		}
		formatter.Printf("Created: %s\n", task.ID)
		return nil
	},
}

// taskUpdateCmd: forge task update <id>
var taskUpdateCmd = &cobra.Command{
	Use:   "update [id]",
	Short: "Update a task",
	Long:  "Update an existing task's properties.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		taskID := args[0]
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		req := &internal.UpdateTaskRequest{}

		if cmd.Flags().Changed("title") {
			v, _ := cmd.Flags().GetString("title")
			req.Subject = &v
		}
		if cmd.Flags().Changed("description") {
			v, _ := cmd.Flags().GetString("description")
			req.Description = &v
		}
		if cmd.Flags().Changed("priority") {
			v, _ := cmd.Flags().GetString("priority")
			req.Priority = &v
		}
		if cmd.Flags().Changed("status") {
			v, _ := cmd.Flags().GetString("status")
			req.Status = &v
		}

		task, err := client.UpdateTask(ctx, taskID, req)
		if err != nil {
			return fmt.Errorf("failed to update task: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(task)
		}
		formatter.Printf("Updated: %s\n", task.ID)
		return nil
	},
}

// taskDeleteCmd: forge task delete <id>
var taskDeleteCmd = &cobra.Command{
	Use:   "delete [id]",
	Short: "Delete a task",
	Long:  "Delete a task from the FORGE system.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		taskID := args[0]
		format, _ := cmd.Flags().GetString("format")
		force, _ := cmd.Flags().GetBool("force")

		if !force {
			fmt.Printf("Are you sure you want to delete task %s? (y/N): ", taskID)
			var response string
			fmt.Scanln(&response)
			if response != "y" && response != "Y" {
				fmt.Println("Cancelled")
				return nil
			}
		}

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		if err := client.DeleteTask(ctx, taskID); err != nil {
			return fmt.Errorf("failed to delete task: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if format != "json" && format != "quiet" {
			formatter.Printf("Deleted: %s\n", taskID)
		}
		return nil
	},
}

// taskClaimCmd: forge task claim <task-id> --agent <agent-id>
var taskClaimCmd = &cobra.Command{
	Use:   "claim [task-id]",
	Short: "Claim a task for an agent",
	Long: `Claim a task for an agent to work on.

When --next is set, the highest-priority pending task is fetched automatically
and claimed without requiring a task ID argument.`,
	Args: cobra.MaximumNArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		claimNext, _ := cmd.Flags().GetBool("next")
		agentID, _ := cmd.Flags().GetString("agent")
		format, _ := cmd.Flags().GetString("format")

		if agentID == "" {
			// Try to get agent ID from environment
			agentID = os.Getenv("FORGE_AGENT_ID")
			if agentID == "" {
				return fmt.Errorf("--agent is required (or set FORGE_AGENT_ID)")
			}
		}

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		var taskID string

		if claimNext {
			// Fetch the highest-priority pending task and claim it.
			result, err := client.ListTasks(ctx, "pending", 1, "")
			if err != nil {
				return fmt.Errorf("failed to list pending tasks: %w", err)
			}
			if len(result.Tasks) == 0 {
				fmt.Println("No pending tasks available")
				return nil
			}
			taskID = result.Tasks[0].ID
		} else {
			if len(args) == 0 {
				return fmt.Errorf("task-id argument is required (or use --next to claim the highest-priority pending task)")
			}
			taskID = args[0]
		}

		task, err := client.ClaimTask(ctx, taskID, agentID)
		if err != nil {
			return fmt.Errorf("failed to claim task: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(task)
		}
		formatter.Printf("Claimed: %s for agent %s\n", task.ID, agentID)
		return nil
	},
}

// taskAckCmd: forge task ack <task-id> [--agent <agent-name>]
var taskAckCmd = &cobra.Command{
	Use:   "ack TASK_ID",
	Short: "Acknowledge a dispatched task (DISPATCHED → RUNNING)",
	Long: `Acknowledge that work has started on a dispatched task.

This transitions the task from DISPATCHED to RUNNING state, confirming
the agent has received and started executing the task.

Examples:
  forge task ack 01JQM123ABC
  forge task ack 01JQM123ABC --agent kimi`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		taskID := args[0]
		agentID, _ := cmd.Flags().GetString("agent")
		if agentID == "" {
			agentID = os.Getenv("FORGE_AGENT_NAME")
		}
		if agentID == "" {
			agentID, _ = os.Hostname()
		}

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		task, err := client.AckTask(ctx, taskID, agentID)
		if err != nil {
			return fmt.Errorf("failed to ACK task: %w\n  Ensure the task is in DISPATCHED state: forge task logs %s", err, taskID)
		}

		fmt.Printf("ACK'd: %s → RUNNING (agent: %s)\n", task.ID, agentID)
		return nil
	},
}

// taskCompleteCmd: forge task complete <task-id> --result "..."
var taskCompleteCmd = &cobra.Command{
	Use:   "complete [task-id]",
	Short: "Mark a task as complete",
	Long: `Mark a task as completed with an optional result message.

The result content can be supplied inline via --result, read from a file via
--result-file <path>, or piped from stdin using --result-file -.

Examples:
  forge task complete TASK-123 --result "Fixed in commit abc123"
  forge task complete TASK-123 --result-file output.md
  cat output.md | forge task complete TASK-123 --result-file -`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		taskID := args[0]
		result, _ := cmd.Flags().GetString("result")
		resultFile, _ := cmd.Flags().GetString("result-file")
		format, _ := cmd.Flags().GetString("format")

		// --result-file takes precedence over --result when both are provided.
		if resultFile != "" {
			var (
				data []byte
				err  error
			)
			if resultFile == "-" {
				data, err = io.ReadAll(os.Stdin)
				if err != nil {
					return fmt.Errorf("failed to read result from stdin: %w", err)
				}
			} else {
				data, err = os.ReadFile(resultFile)
				if err != nil {
					return fmt.Errorf("failed to read result file %q: %w", resultFile, err)
				}
			}
			result = string(data)
		}

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		task, err := client.CompleteTask(ctx, taskID, result)
		if err != nil {
			return fmt.Errorf("failed to complete task: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(task)
		}
		formatter.Printf("Completed: %s\n", task.ID)
		return nil
	},
}

// taskHistoryCmd: forge task history <task-id>
var taskHistoryCmd = &cobra.Command{
	Use:   "history [task-id]",
	Short: "Show state transition history for a task",
	Long:  "Display the full state transition history for a specific task.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		taskID := args[0]

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		resp, err := client.Get(ctx, "/tasks/"+taskID+"/history")
		if err != nil {
			return fmt.Errorf("failed to get task history: %w", err)
		}
		defer resp.Body.Close()

		if resp.StatusCode == 404 {
			return fmt.Errorf("task %s not found", taskID)
		}
		if resp.StatusCode != 200 {
			return fmt.Errorf("server returned %d", resp.StatusCode)
		}

		var result struct {
			TaskID      string `json:"task_id"`
			Count       int    `json:"count"`
			Transitions []struct {
				ID             string    `json:"id"`
				TaskID         string    `json:"task_id"`
				FromState      string    `json:"from_state"`
				ToState        string    `json:"to_state"`
				Reason         string    `json:"reason"`
				TransitionedAt time.Time `json:"transitioned_at"`
			} `json:"transitions"`
		}
		if err := internal.DecodeJSON(resp, &result); err != nil {
			return fmt.Errorf("failed to decode response: %w", err)
		}

		format, _ := cmd.Flags().GetString("format")
		if format == "json" {
			formatter := internal.NewFormatter(format, nil)
			return formatter.WriteJSON(result)
		}

		fmt.Printf("Task: %s\n\n", result.TaskID)
		if result.Count == 0 {
			fmt.Printf("No state transitions recorded for task %s\n", taskID)
			return nil
		}

		fmt.Printf("STATE TRANSITIONS:\n")
		fmt.Printf("%-22s %-14s %-14s %s\n", "TIMESTAMP", "FROM", "TO", "REASON")
		for _, t := range result.Transitions {
			from := t.FromState
			if from == "" {
				from = "(new)"
			}
			ts := t.TransitionedAt.UTC().Format(time.RFC3339)
			fmt.Printf("%-22s %-14s %-14s %s\n", ts, from, t.ToState, t.Reason)
		}
		return nil
	},
}

// taskAbandonCmd: forge task abandon <task-id>
var taskAbandonCmd = &cobra.Command{
	Use:   "abandon [task-id]",
	Short: "Mark a task as abandoned",
	Long:  "Mark a specific task as abandoned (useful for stuck/zombie tasks).",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		taskID := args[0]
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		resp, err := client.Post(ctx, "/api/tasks/"+taskID+"/abandon", nil)
		if err != nil {
			return fmt.Errorf("failed to abandon task: %w", err)
		}
		defer resp.Body.Close()
		if resp.StatusCode == 404 {
			return fmt.Errorf("task %s not found or already completed/abandoned", taskID)
		}
		if resp.StatusCode != 200 {
			return fmt.Errorf("server returned %d", resp.StatusCode)
		}

		formatter := internal.NewFormatter(format, nil)
		formatter.Printf("Abandoned: %s\n", taskID)
		return nil
	},
}

// taskQualityGateCmd: forge task quality-gate <task-id>
var taskQualityGateCmd = &cobra.Command{
	Use:   "quality-gate [task-id]",
	Short: "Record quality gate results for a task",
	Long:  "Submit test pass rate, coverage, and lint issue counts for a completed task.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		taskID := args[0]
		testPassRate, _ := cmd.Flags().GetFloat64("test-pass-rate")
		coveragePct, _ := cmd.Flags().GetFloat64("coverage")
		lintIssues, _ := cmd.Flags().GetInt("lint-issues")
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		body := map[string]interface{}{
			"test_pass_rate": testPassRate,
			"coverage_pct":   coveragePct,
			"lint_issues":    lintIssues,
		}

		resp, err := client.Post(ctx, "/api/tasks/"+taskID+"/quality-gates", body)
		if err != nil {
			return fmt.Errorf("failed to submit quality gate results: %w", err)
		}
		defer resp.Body.Close()

		if resp.StatusCode == 404 {
			return fmt.Errorf("task %s not found", taskID)
		}
		if resp.StatusCode != 201 {
			return fmt.Errorf("server returned %d", resp.StatusCode)
		}

		var result map[string]interface{}
		if err := internal.DecodeJSON(resp, &result); err != nil {
			return fmt.Errorf("failed to decode response: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(result)
		}
		formatter.Printf("Quality gate recorded for task %s (id: %v)\n", taskID, result["id"])
		formatter.Printf("  test_pass_rate: %v\n", result["test_pass_rate"])
		formatter.Printf("  coverage_pct:   %v\n", result["coverage_pct"])
		formatter.Printf("  lint_issues:    %v\n", result["lint_issues"])
		return nil
	},
}

// taskPruneCmd: forge task prune
var taskPruneCmd = &cobra.Command{
	Use:   "prune",
	Short: "Prune completed/failed tasks and zombie assigned tasks",
	Long: `Prune stale tasks from the queue.

By default, removes completed/failed tasks older than 48 hours and
abandons assigned tasks with no heartbeat update in the last 2 hours.

Use --older-than to customise the completed/failed retention window.
Use --dry-run to preview what would be pruned without making changes.

Examples:
  forge task prune                    # Prune with defaults (48h retention)
  forge task prune --older-than 24h   # Custom retention for completed/failed
  forge task prune --dry-run          # Preview without changes`,
	RunE: func(cmd *cobra.Command, args []string) error {
		olderThan, _ := cmd.Flags().GetString("older-than")
		dryRun, _ := cmd.Flags().GetBool("dry-run")
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()

		// Build query params
		path := "/tasks/prune"
		params := []string{}
		if olderThan != "" {
			params = append(params, "older_than="+olderThan)
		}
		if dryRun {
			params = append(params, "dry_run=true")
		}
		if len(params) > 0 {
			path += "?" + strings.Join(params, "&")
		}

		resp, err := client.Post(ctx, path, nil)
		if err != nil {
			return fmt.Errorf("prune tasks: %w\n  Check: forge daemon status", err)
		}
		defer resp.Body.Close()

		if resp.StatusCode != 200 {
			body, _ := io.ReadAll(resp.Body)
			return fmt.Errorf("prune failed (HTTP %d): %s\n  Check: forge daemon status", resp.StatusCode, strings.TrimSpace(string(body)))
		}

		var result map[string]interface{}
		if err := internal.DecodeJSON(resp, &result); err != nil {
			return fmt.Errorf("decode prune response: %w", err)
		}

		if format == "json" {
			formatter := internal.NewFormatter(format, nil)
			return formatter.WriteJSON(result)
		}

		pruned, _ := result["pruned"].(float64)
		threshold, _ := result["threshold"].(string)
		if dryRun {
			fmt.Printf("Dry-run: would prune %d task(s)", int(pruned))
		} else {
			fmt.Printf("Pruned %d task(s)", int(pruned))
		}
		if threshold != "" {
			fmt.Printf(" (threshold: %s)", threshold)
		}
		fmt.Println()
		return nil
	},
}

// taskResultsCmd: forge task results — show fleet agent result files
var taskResultsCmd = &cobra.Command{
	Use:   "results",
	Short: "Show fleet agent result files",
	Long:  "List result files from .forge/heartbeat/results/ — fleet agent deliverables.",
	RunE: func(cmd *cobra.Command, args []string) error {
		limit, _ := cmd.Flags().GetInt("limit")
		matches, err := filepath.Glob(".forge/heartbeat/results/*.md")
		if err != nil || len(matches) == 0 {
			fmt.Println("No result files found in .forge/heartbeat/results/")
			return nil
		}

		// Sort by modification time (newest first)
		type fileEntry struct {
			path    string
			modTime time.Time
			title   string
		}
		var entries []fileEntry
		for _, path := range matches {
			info, err := os.Stat(path)
			if err != nil {
				continue
			}
			// Read first non-empty line for title
			f, err := os.Open(path)
			if err != nil {
				continue
			}
			scanner := bufio.NewScanner(f)
			title := "(empty)"
			for scanner.Scan() {
				line := strings.TrimSpace(scanner.Text())
				if line != "" && !strings.HasPrefix(line, "---") {
					title = strings.TrimPrefix(line, "# ")
					break
				}
			}
			f.Close()
			if len(title) > 60 {
				title = title[:57] + "..."
			}
			entries = append(entries, fileEntry{path: filepath.Base(path), modTime: info.ModTime(), title: title})
		}

		// Sort newest first
		for i := 0; i < len(entries); i++ {
			for j := i + 1; j < len(entries); j++ {
				if entries[j].modTime.After(entries[i].modTime) {
					entries[i], entries[j] = entries[j], entries[i]
				}
			}
		}

		if limit > 0 && len(entries) > limit {
			entries = entries[:limit]
		}

		w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
		fmt.Fprintf(w, "FILE\tAGE\tTITLE\n")
		now := time.Now()
		for _, e := range entries {
			age := now.Sub(e.modTime).Truncate(time.Minute)
			fmt.Fprintf(w, "%s\t%s\t%s\n", e.path, formatAge(age), e.title)
		}
		w.Flush()
		fmt.Printf("\nTotal: %d result(s)\n", len(entries))
		return nil
	},
}

func formatAge(d time.Duration) string {
	if d < time.Hour {
		return fmt.Sprintf("%dm", int(d.Minutes()))
	}
	if d < 24*time.Hour {
		return fmt.Sprintf("%dh", int(d.Hours()))
	}
	return fmt.Sprintf("%dd", int(d.Hours()/24))
}

func init() {
	// task results flags
	taskResultsCmd.Flags().Int("limit", 20, "Maximum number of results to show")

	// task list flags
	taskListCmd.Flags().String("status", "", "Filter by status")
	taskListCmd.Flags().String("domain", "", "Filter by domain")
	taskListCmd.Flags().Int("limit", 50, "Maximum number of tasks to show")
	taskListCmd.Flags().Bool("all", false, "Show all tasks including completed and failed")

	// task create flags
	taskCreateCmd.Flags().String("title", "", "Task title/subject (required)")
	taskCreateCmd.Flags().String("description", "", "Task description")
	taskCreateCmd.Flags().String("priority", "medium", "Task priority (low, medium, high, critical)")
	taskCreateCmd.Flags().String("domain", "", "Domain for the task")
	taskCreateCmd.Flags().String("product", "", "Product (repository/project) for the task")
	taskCreateCmd.Flags().String("project", "", "Deprecated: use --product instead")
	taskCreateCmd.Flags().Lookup("project").Hidden = true
	taskCreateCmd.Flags().String("type", "feature", "Task type")
	taskCreateCmd.Flags().String("lane", "", "Lane to assign the task to")
	taskCreateCmd.Flags().StringArray("metadata", nil, "Metadata key=value pairs (repeatable)")
	taskCreateCmd.Flags().String("portfolio", "", "Portfolio product key (sets stage-aware routing)")
	taskCreateCmd.Flags().String("node", "", "Target node — routes task via XNode if remote (e.g. --node sati)")

	// task update flags
	taskUpdateCmd.Flags().String("subject", "", "New subject/title")
	taskUpdateCmd.Flags().String("description", "", "New description")
	taskUpdateCmd.Flags().String("priority", "", "New priority")
	taskUpdateCmd.Flags().String("status", "", "New status")

	// task delete flags
	taskDeleteCmd.Flags().Bool("force", false, "Skip confirmation")

	// task claim flags
	taskClaimCmd.Flags().String("agent", "", "Agent ID to claim the task (or FORGE_AGENT_ID env var)")
	taskClaimCmd.Flags().Bool("next", false, "Claim the highest-priority pending task (no task-id argument needed)")

	// task ack flags
	taskAckCmd.Flags().String("agent", "", "Agent name to ACK as (default: FORGE_AGENT_NAME or hostname)")

	// task complete flags
	taskCompleteCmd.Flags().String("result", "", "Result message or summary")
	taskCompleteCmd.Flags().String("result-file", "", "File to read result content from; use '-' for stdin")

	// task quality-gate flags
	taskQualityGateCmd.Flags().Float64("test-pass-rate", 1.0, "Fraction of tests passing (0.0–1.0)")
	taskQualityGateCmd.Flags().Float64("coverage", 0.0, "Code coverage percentage (0.0–100.0)")
	taskQualityGateCmd.Flags().Int("lint-issues", 0, "Number of lint issues found")

	// task prune flags
	taskPruneCmd.Flags().String("older-than", "", "Prune completed/failed tasks older than this duration (e.g. 24h, 7d)")
	taskPruneCmd.Flags().Bool("dry-run", false, "Preview what would be pruned without making changes")

	// Add commands to task noun
	taskCmd.AddCommand(taskListCmd)
	taskCmd.AddCommand(taskShowCmd)
	taskCmd.AddCommand(taskCreateCmd)
	taskCmd.AddCommand(taskUpdateCmd)
	taskCmd.AddCommand(taskDeleteCmd)
	taskCmd.AddCommand(taskClaimCmd)
	taskCmd.AddCommand(taskAckCmd)
	taskCmd.AddCommand(taskCompleteCmd)
	taskCmd.AddCommand(taskAbandonCmd)
	taskCmd.AddCommand(taskHistoryCmd)
	taskCmd.AddCommand(taskLogsCmd)
	taskCmd.AddCommand(taskQualityGateCmd)
	taskCmd.AddCommand(taskPruneCmd)
	taskCmd.AddCommand(taskResultsCmd)
	taskCmd.AddCommand(taskWatchCmd)
}
