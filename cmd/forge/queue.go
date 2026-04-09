package main

import (
	"context"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// queueCmd represents the queue noun.
// Default run (no subcommand): show a summary table of task counts by state.
var queueCmd = &cobra.Command{
	Use:     "queue",
	Aliases: []string{"q"},
	Short:   "Inspect the task queue",
	Long: `Inspect the FORGE task queue.

Subcommands give fleet agents visibility into queue depth and task distribution.

Examples:
  # Queue summary: counts by state
  forge queue

  # List queued tasks, priority-ordered
  forge queue list

  # Just the count (for scripting)
  forge queue depth

  # Change priority of a queued task
  forge queue priority TASK-ID --priority 10

  # Cancel a queued task
  forge queue cancel TASK-ID`,
	RunE: func(cmd *cobra.Command, args []string) error {
		fmt.Fprintln(cmd.ErrOrStderr(), "Note: 'forge queue' is an alias for 'forge task list'")
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		result, err := client.ListTasks(ctx, "", 100, "")
		if err != nil {
			return fmt.Errorf("failed to list tasks: %w\n  Run 'forge daemon status' to check if the daemon is running", err)
		}

		// Count tasks by state (use Status field; fall back to State for v3 daemons).
		counts := map[string]int{
			"queued":    0,
			"running":   0,
			"failed":    0,
			"completed": 0,
		}
		for _, t := range result.Tasks {
			st := t.Status
			if st == "" {
				st = t.State
			}
			st = strings.ToLower(st)
			switch st {
			case "queued", "pending":
				counts["queued"]++
			case "running", "dispatched", "assigned":
				counts["running"]++
			case "failed":
				counts["failed"]++
			case "completed", "done", "approved":
				counts["completed"]++
			}
		}

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(counts)
		}

		formatter.Printf("QUEUE SUMMARY\n")
		formatter.Printf("%-14s %s\n", "STATE", "COUNT")
		formatter.Printf("%-14s %s\n", strings.Repeat("-", 14), strings.Repeat("-", 5))
		formatter.Printf("%-14s %d\n", "queued", counts["queued"])
		formatter.Printf("%-14s %d\n", "running", counts["running"])
		formatter.Printf("%-14s %d\n", "failed", counts["failed"])
		formatter.Printf("%-14s %d\n", "completed (24h)", counts["completed"])
		formatter.Printf("\nTotal fetched: %d\n", len(result.Tasks))
		return nil
	},
}

// queueListCmd: forge queue list
// Lists queued tasks sorted by priority (descending), showing key columns.
var queueListCmd = &cobra.Command{
	Use:   "list",
	Short: "List queued tasks, priority-ordered",
	Long:  "List tasks in queued/pending state, sorted by priority descending. Shows ID, TITLE, PRIORITY, DOMAIN, CREATED_AT.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		result, err := client.ListTasks(ctx, "queued", 50, "")
		if err != nil {
			return fmt.Errorf("failed to list queued tasks: %w\n  Run 'forge daemon status' to check if the daemon is running", err)
		}

		tasks := result.Tasks

		// Sort by priority descending (higher value = dispatched sooner).
		sort.Slice(tasks, func(i, j int) bool {
			return tasks[i].Priority > tasks[j].Priority
		})

		if format == "json" {
			formatter := internal.NewFormatter(format, nil)
			return formatter.WriteJSON(tasks)
		}

		if len(tasks) == 0 {
			fmt.Println("Queue is empty")
			return nil
		}

		// Table header
		fmt.Printf("%-26s %-42s %-10s %-20s %s\n",
			"ID", "TITLE", "PRIORITY", "DOMAIN", "CREATED_AT")
		fmt.Printf("%-26s %-42s %-10s %-20s %s\n",
			strings.Repeat("-", 26),
			strings.Repeat("-", 42),
			strings.Repeat("-", 10),
			strings.Repeat("-", 20),
			strings.Repeat("-", 20),
		)

		for _, t := range tasks {
			title := t.DisplayTitle()
			if len(title) > 40 {
				title = title[:37] + "..."
			}
			priority := internal.PriorityToLabel(t.Priority)
			domain := t.Domain
			if len(domain) > 20 {
				domain = domain[:17] + "..."
			}
			createdAt := t.CreatedAt
			if len(createdAt) > 20 {
				createdAt = createdAt[:20]
			}
			fmt.Printf("%-26s %-42s %-10s %-20s %s\n",
				t.ID, title, priority, domain, createdAt)
		}
		fmt.Printf("\nTotal: %d queued task(s)\n", len(tasks))
		return nil
	},
}

// queueDepthCmd: forge queue depth
// Outputs only the count of queued tasks — designed for scripting (no headers).
var queueDepthCmd = &cobra.Command{
	Use:   "depth",
	Short: "Print the number of queued tasks (for scripting)",
	Long: `Print just the count of tasks in queued/pending state.

Outputs a bare integer with no headers, suitable for use in scripts:
  COUNT=$(forge queue depth)
  echo "Tasks waiting: $COUNT"`,
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		result, err := client.ListTasks(ctx, "queued", 1000, "")
		if err != nil {
			return fmt.Errorf("failed to get queue depth: %w\n  Run 'forge daemon status' to check if the daemon is running", err)
		}

		depth := len(result.Tasks)

		if format == "json" {
			formatter := internal.NewFormatter(format, nil)
			return formatter.WriteJSON(map[string]int{"depth": depth})
		}

		// Bare integer output — no newline decoration beyond \n — for scripting.
		fmt.Println(depth)
		return nil
	},
}

// queueStatusCmd: forge queue status
// Kept for backward compatibility with existing scripts and tests.
var queueStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show queue status summary (alias for 'forge queue')",
	Long:  "Display a summary of the current queue state including counts by status. This is an alias for 'forge queue' with no subcommand.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		result, err := client.ListTasks(ctx, "", 100, "")
		if err != nil {
			return fmt.Errorf("failed to get queue status: %w\n  Run 'forge daemon status' to check if the daemon is running", err)
		}

		counts := map[string]int{
			"queued":    0,
			"running":   0,
			"failed":    0,
			"completed": 0,
		}
		for _, t := range result.Tasks {
			st := t.Status
			if st == "" {
				st = t.State
			}
			st = strings.ToLower(st)
			switch st {
			case "queued", "pending":
				counts["queued"]++
			case "running", "dispatched", "assigned":
				counts["running"]++
			case "failed":
				counts["failed"]++
			case "completed", "done", "approved":
				counts["completed"]++
			}
		}

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(counts)
		}

		formatter.Printf("QUEUE SUMMARY\n")
		formatter.Printf("%-14s %s\n", "STATE", "COUNT")
		formatter.Printf("%-14s %s\n", strings.Repeat("-", 14), strings.Repeat("-", 5))
		formatter.Printf("%-14s %d\n", "queued", counts["queued"])
		formatter.Printf("%-14s %d\n", "running", counts["running"])
		formatter.Printf("%-14s %d\n", "failed", counts["failed"])
		formatter.Printf("%-14s %d\n", "completed (24h)", counts["completed"])
		formatter.Printf("\nTotal fetched: %d\n", len(result.Tasks))
		return nil
	},
}

// queuePriorityCmd: forge queue priority TASK_ID --priority N
var queuePriorityCmd = &cobra.Command{
	Use:   "priority TASK_ID",
	Short: "Change the priority of a queued task",
	Long: `Update the priority of a task in the queue.

Higher priority values cause a task to be dispatched sooner.
Use this to bump urgent work to the front of the queue.

Examples:
  forge queue priority 01JQM123ABC --priority 10
  forge queue priority 01JQM123ABC --priority 1`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		taskID := args[0]
		priority, _ := cmd.Flags().GetInt("priority")
		format, _ := cmd.Flags().GetString("format")

		if !cmd.Flags().Changed("priority") {
			return fmt.Errorf("--priority is required\n  Example: forge queue priority %s --priority 5", taskID)
		}

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		body := map[string]interface{}{
			"task_id":  taskID,
			"priority": priority,
		}

		resp, err := client.Post(ctx, "/cli/queue/priority", body)
		if err != nil {
			return fmt.Errorf("failed to update queue priority: %w\n  Run 'forge daemon status' to check if the daemon is running", err)
		}
		defer resp.Body.Close()

		if resp.StatusCode == 404 {
			return fmt.Errorf("task %s not found in queue\n  Use 'forge queue list' to see queued tasks", taskID)
		}
		if resp.StatusCode != 200 {
			body, _ := io.ReadAll(resp.Body)
			return fmt.Errorf("priority update failed (HTTP %d): %s\n  Run 'forge daemon status' to check if the daemon is running",
				resp.StatusCode, strings.TrimSpace(string(body)))
		}

		var result map[string]interface{}
		if err := internal.DecodeJSON(resp, &result); err != nil {
			return fmt.Errorf("failed to decode priority response: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(result)
		}
		formatter.Printf("Priority updated: %s → %d\n", taskID, priority)
		return nil
	},
}

// queueCancelCmd: forge queue cancel TASK_ID
var queueCancelCmd = &cobra.Command{
	Use:   "cancel TASK_ID",
	Short: "Cancel a queued task",
	Long: `Remove a task from the queue and mark it as cancelled.

Only tasks currently in queued/pending state can be cancelled this way.
Use 'forge task abandon' for tasks that are already assigned.

Examples:
  forge queue cancel 01JQM123ABC`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		taskID := args[0]
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		body := map[string]interface{}{
			"task_id": taskID,
		}

		resp, err := client.Post(ctx, "/cli/queue/cancel", body)
		if err != nil {
			return fmt.Errorf("failed to cancel queued task: %w\n  Run 'forge daemon status' to check if the daemon is running", err)
		}
		defer resp.Body.Close()

		if resp.StatusCode == 404 {
			return fmt.Errorf("task %s not found in queue\n  Use 'forge queue list' to see queued tasks, or 'forge task abandon' for assigned tasks", taskID)
		}
		if resp.StatusCode != 200 {
			body, _ := io.ReadAll(resp.Body)
			return fmt.Errorf("queue cancel failed (HTTP %d): %s\n  Run 'forge daemon status' to check if the daemon is running",
				resp.StatusCode, strings.TrimSpace(string(body)))
		}

		var result map[string]interface{}
		if err := internal.DecodeJSON(resp, &result); err != nil {
			return fmt.Errorf("failed to decode cancel response: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if format == "json" {
			return formatter.WriteJSON(result)
		}
		formatter.Printf("Cancelled: %s\n", taskID)
		return nil
	},
}

func init() {
	queueCmd.Hidden = true
	// queue (default run) flags
	queueCmd.Flags().String("format", "", "Output format (table, json)")

	// queue status flags
	queueStatusCmd.Flags().String("format", "", "Output format (table, json)")

	// queue depth flags
	queueDepthCmd.Flags().String("format", "", "Output format (table, json)")

	// queue list flags
	queueListCmd.Flags().String("format", "", "Output format (table, json)")
	queueListCmd.Flags().Int("limit", 50, "Maximum number of tasks to show")

	// queue priority flags
	queuePriorityCmd.Flags().Int("priority", 0, "New priority value (higher = dispatched sooner)")
	queuePriorityCmd.Flags().String("format", "", "Output format (table, json)")

	// queue cancel flags
	queueCancelCmd.Flags().String("format", "", "Output format (table, json)")

	// Add subcommands to queue noun
	queueCmd.AddCommand(queueStatusCmd)
	queueCmd.AddCommand(queueDepthCmd)
	queueCmd.AddCommand(queueListCmd)
	queueCmd.AddCommand(queuePriorityCmd)
	queueCmd.AddCommand(queueCancelCmd)
}
