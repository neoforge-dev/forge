package main

import (
	"context"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// taskCmd represents the task noun
var taskCmd = &cobra.Command{
	Use:   "task",
	Short: "Manage tasks - units of work",
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
	Short: "List all tasks",
	Long:  "List all tasks with optional filtering by status.",
	RunE: func(cmd *cobra.Command, args []string) error {
		status, _ := cmd.Flags().GetString("status")
		limit, _ := cmd.Flags().GetInt("limit")
		domain, _ := cmd.Flags().GetString("domain")
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		result, err := client.ListTasks(ctx, status, limit, domain)
		if err != nil {
			return fmt.Errorf("failed to list tasks: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if err := formatter.FormatTasks(result.Tasks); err != nil {
			return err
		}

		if format == "table" {
			formatter.Printf("\nTotal: %d tasks\n", len(result.Tasks))
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
		project, _ := cmd.Flags().GetString("project")
		taskType, _ := cmd.Flags().GetString("type")
		lane, _ := cmd.Flags().GetString("lane")
		metaSlice, _ := cmd.Flags().GetStringArray("metadata")
		format, _ := cmd.Flags().GetString("format")

		if title == "" {
			return fmt.Errorf("--title is required")
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

func init() {
	// task list flags
	taskListCmd.Flags().String("status", "", "Filter by status")
	taskListCmd.Flags().String("domain", "", "Filter by domain")
	taskListCmd.Flags().Int("limit", 50, "Maximum number of tasks to show")

	// task create flags
	taskCreateCmd.Flags().String("title", "", "Task title/subject (required)")
	taskCreateCmd.Flags().String("description", "", "Task description")
	taskCreateCmd.Flags().String("priority", "medium", "Task priority (low, medium, high, critical)")
	taskCreateCmd.Flags().String("domain", "", "Domain for the task")
	taskCreateCmd.Flags().String("project", "", "Project for the task")
	taskCreateCmd.Flags().String("type", "feature", "Task type")
	taskCreateCmd.Flags().String("lane", "", "Lane to assign the task to")
	taskCreateCmd.Flags().StringArray("metadata", nil, "Metadata key=value pairs (repeatable)")

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

	// task complete flags
	taskCompleteCmd.Flags().String("result", "", "Result message or summary")
	taskCompleteCmd.Flags().String("result-file", "", "File to read result content from; use '-' for stdin")

	// task quality-gate flags
	taskQualityGateCmd.Flags().Float64("test-pass-rate", 1.0, "Fraction of tests passing (0.0–1.0)")
	taskQualityGateCmd.Flags().Float64("coverage", 0.0, "Code coverage percentage (0.0–100.0)")
	taskQualityGateCmd.Flags().Int("lint-issues", 0, "Number of lint issues found")

	// Add commands to task noun
	taskCmd.AddCommand(taskListCmd)
	taskCmd.AddCommand(taskShowCmd)
	taskCmd.AddCommand(taskCreateCmd)
	taskCmd.AddCommand(taskUpdateCmd)
	taskCmd.AddCommand(taskDeleteCmd)
	taskCmd.AddCommand(taskClaimCmd)
	taskCmd.AddCommand(taskCompleteCmd)
	taskCmd.AddCommand(taskAbandonCmd)
	taskCmd.AddCommand(taskHistoryCmd)
	taskCmd.AddCommand(taskQualityGateCmd)
}
