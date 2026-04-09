package main

import (
	"context"
	"fmt"
	"os/exec"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

var taskWatchCmd = &cobra.Command{
	Use:   "watch <task-id>",
	Short: "Watch a task until acceptance criteria pass",
	Long: `Monitor a task and verify completion with an acceptance command.

When the task reaches COMPLETED state, runs the acceptance command.
If it passes, exits successfully. If it fails, creates a new follow-up
task with failure context and continues watching.

Exits on: acceptance passes, max duration reached, or max retries exceeded.

Examples:
  forge task watch TASK-ABC --cmd "cd cmd/forged && go test ./..."
  forge task watch TASK-ABC --cmd "curl -sf localhost:8081/health" --interval 2m
  forge task watch TASK-ABC --max 2h --retries 5`,
	Args: cobra.ExactArgs(1),
	RunE: runTaskWatch,
}

func init() {
	taskWatchCmd.Flags().String("cmd", "", "Acceptance command to run when task completes")
	taskWatchCmd.Flags().Duration("interval", 5*time.Minute, "Poll interval")
	taskWatchCmd.Flags().Duration("max", 6*time.Hour, "Maximum watch duration")
	taskWatchCmd.Flags().Int("retries", 10, "Maximum retry attempts")
}

func runTaskWatch(cmd *cobra.Command, args []string) error {
	taskID := args[0]
	acceptCmd, _ := cmd.Flags().GetString("cmd")
	interval, _ := cmd.Flags().GetDuration("interval")
	maxDuration, _ := cmd.Flags().GetDuration("max")
	maxRetries, _ := cmd.Flags().GetInt("retries")

	client := internal.NewClient()
	start := time.Now()
	retries := 0
	currentTaskID := taskID

	fmt.Printf("Watching task %s (interval=%s, max=%s, retries=%d)\n",
		taskID, interval, maxDuration, maxRetries)
	if acceptCmd != "" {
		fmt.Printf("Acceptance: %s\n", acceptCmd)
	}

	for {
		// Check max duration
		if time.Since(start) > maxDuration {
			return fmt.Errorf("max duration %s exceeded after %d retries\n  Tip: increase --max or --retries to extend the watch window", maxDuration, retries)
		}
		// Check max retries
		if retries >= maxRetries {
			return fmt.Errorf("max retries %d exceeded\n  Tip: increase --retries or fix acceptance command failures before retrying", maxRetries)
		}

		// Fetch task state from daemon
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		task, err := client.GetTask(ctx, currentTaskID)
		cancel()

		if err != nil {
			fmt.Printf("[%s] Error fetching task %s: %v (retrying...)\n",
				time.Now().Format("15:04:05"), currentTaskID, err)
			time.Sleep(interval)
			continue
		}

		// Prefer State (V3 field) over Status for state machine checks.
		state := task.State
		if state == "" {
			state = task.Status
		}
		fmt.Printf("[%s] Task %s state=%s\n",
			time.Now().Format("15:04:05"), currentTaskID, state)

		switch state {
		case "completed", "COMPLETED", "approved", "APPROVED":
			if acceptCmd == "" {
				fmt.Printf("Task %s completed (no acceptance cmd). Done.\n", currentTaskID)
				return nil
			}

			// Run acceptance command
			fmt.Printf("Running acceptance: %s\n", acceptCmd)
			output, runErr := runWatchAcceptance(acceptCmd)

			if runErr == nil {
				fmt.Printf("Acceptance PASSED. Task %s verified.\n", currentTaskID)
				return nil
			}

			// Acceptance failed — create retry task
			retries++
			fmt.Printf("Acceptance FAILED (attempt %d/%d): %v\n", retries, maxRetries, runErr)
			fmt.Printf("Output: %s\n", watchTruncOutput(output, 200))

			// Create new task with context
			newTask, createErr := createWatchRetryTask(client, task, retries, output)
			if createErr != nil {
				fmt.Printf("Failed to create retry task: %v\n  Check: forge daemon status\n", createErr)
				time.Sleep(interval)
				continue
			}
			currentTaskID = newTask.ID
			fmt.Printf("Created retry task %s\n", newTask.ID)

		case "failed", "FAILED":
			fmt.Printf("Task %s failed — waiting for auto-requeue patrol...\n", currentTaskID)

		case "queued", "QUEUED", "dispatched", "DISPATCHED", "running", "RUNNING":
			// Still in progress — wait

		default:
			fmt.Printf("Unknown state %q — waiting...\n", state)
		}

		time.Sleep(interval)
	}
}

// runWatchAcceptance runs the acceptance command via bash with a 5-minute timeout.
func runWatchAcceptance(cmd string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	c := exec.CommandContext(ctx, "bash", "-c", cmd)
	output, err := c.CombinedOutput()
	if ctx.Err() == context.DeadlineExceeded {
		return string(output), fmt.Errorf("acceptance command timed out after 5 minutes")
	}
	return string(output), err
}

// watchTruncOutput truncates a string to at most max bytes, appending "..." if truncated.
func watchTruncOutput(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max] + "..."
}

// createWatchRetryTask creates a follow-up task with failure context from the previous attempt.
func createWatchRetryTask(client *internal.Client, orig *internal.Task, attempt int, failOutput string) (*internal.Task, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	desc := fmt.Sprintf("%s\n\n---\n## Retry #%d\n\nPrevious acceptance check failed:\n```\n%s\n```",
		orig.Description, attempt, watchTruncOutput(failOutput, 500))

	// Map the integer priority back to a label so CreateTask can normalize it.
	priorityLabel := internal.PriorityToLabel(orig.Priority)

	req := &internal.CreateTaskRequest{
		Domain:      orig.Domain,
		Project:     orig.Project,
		Type:        orig.TaskType,
		Subject:     orig.Title,
		Description: desc,
		Priority:    priorityLabel,
	}

	return client.CreateTask(ctx, req)
}
