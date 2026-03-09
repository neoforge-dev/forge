package main

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// completeCmd is the top-level "forge complete" command.
// It runs the full task completion workflow: optional tests → git commit → git push → API mark-complete.
var completeCmd = &cobra.Command{
	Use:   "complete <task-id>",
	Short: "Full task completion workflow: test → commit → push → mark complete",
	Long: `forge complete runs the full task completion workflow in one command.

Steps (in order):
  1. Fetch task details from the API (supports 8-char prefix matching)
  2. Optionally run tests (--run-tests): go test ./... or uv run pytest -q
  3. Git add + commit (skipped if nothing to commit)
  4. Git push (unless --no-push)
  5. Mark task complete via POST /api/tasks/{id}/complete

Examples:
  forge complete 01JQM123ABC
  forge complete 01JQM123ABC --run-tests --message "fix: resolve race condition"
  forge complete 01JQM123ABC --no-push --result "manual deploy required"
  forge complete 01JQM123ABC --dry-run`,
	Args: cobra.ExactArgs(1),
	RunE: runComplete,
}

func runComplete(cmd *cobra.Command, args []string) error {
	taskID := args[0]
	runTests, _ := cmd.Flags().GetBool("run-tests")
	message, _ := cmd.Flags().GetString("message")
	noPush, _ := cmd.Flags().GetBool("no-push")
	result, _ := cmd.Flags().GetString("result")
	dryRun, _ := cmd.Flags().GetBool("dry-run")

	if dryRun {
		fmt.Println("[dry-run] forge complete — no changes will be made")
	}

	// Step 1: Resolve task from API
	client := internal.NewClient()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	task, err := client.GetTask(ctx, taskID)
	if err != nil {
		return fmt.Errorf("failed to resolve task %s: %w\n  Recovery: check 'forge task list' and verify the task ID", taskID, err)
	}
	// Use the full ID from the API response (handles prefix lookups)
	taskID = task.ID

	domain := task.Domain
	if domain == "" {
		domain = "forge"
	}
	title := task.DisplayTitle()

	fmt.Printf("Task: %s — %s\n", taskID, title)

	// Step 2: Optionally run tests
	if runTests {
		testSummary, err := runProjectTests(dryRun)
		if err != nil {
			return fmt.Errorf("tests failed — aborting commit\n%w", err)
		}
		fmt.Printf("  Tests passed%s\n", testSummary)
	}

	// Step 3: Git commit
	if message == "" {
		message = fmt.Sprintf("feat(%s): %s [%s]", domain, title, taskID)
	}
	committed, err := completeGitCommit(message, dryRun)
	if err != nil {
		return fmt.Errorf("git commit failed: %w\n  Recovery: resolve conflicts then retry", err)
	}
	if committed {
		fmt.Printf("  Committed: %s\n", message)
	} else {
		fmt.Println("  Nothing to commit (working tree clean)")
	}

	// Step 4: Git push
	if !noPush {
		if err := gitSimplePush(dryRun); err != nil {
			// Non-fatal: warn but continue to mark complete
			fmt.Printf("  WARN: git push failed (%v) — task will still be marked complete\n", err)
		} else {
			fmt.Println("  Pushed to origin")
		}
	}

	// Step 5: Mark task complete via API
	if result == "" {
		result = "completed"
	}
	if dryRun {
		fmt.Printf("  [dry-run] Would mark task %s complete with result: %q\n", taskID, result)
		return nil
	}

	agentID := os.Getenv("FORGE_AGENT_NAME")
	if agentID == "" {
		agentID = os.Getenv("FORGE_AGENT_ID")
	}
	if agentID == "" {
		agentID, _ = os.Hostname()
	}

	apiCtx, apiCancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer apiCancel()

	completedTask, err := client.CompleteTask(apiCtx, taskID, result)
	if err != nil {
		return fmt.Errorf("failed to mark task %s complete: %w\n  Recovery: retry 'forge task complete %s --result %q'", taskID, err, taskID, result)
	}

	fmt.Printf("  Task %s marked complete (state: %s)\n", completedTask.ID, completedTask.State)
	return nil
}

// runProjectTests detects the project type and runs the appropriate test command.
// Returns a summary string like " (42 tests, 1.2s)" on success.
func runProjectTests(dryRun bool) (string, error) {
	testCmd, testArgs := detectTestCommand()
	if dryRun {
		fmt.Printf("  [dry-run] Would run: %s %s\n", testCmd, strings.Join(testArgs, " "))
		return "", nil
	}

	fmt.Printf("  Running: %s %s\n", testCmd, strings.Join(testArgs, " "))
	start := time.Now()
	c := exec.Command(testCmd, testArgs...)
	var out bytes.Buffer
	c.Stdout = &out
	c.Stderr = &out
	err := c.Run()
	elapsed := time.Since(start)

	if err != nil {
		return "", fmt.Errorf("%s\n%s", err.Error(), out.String())
	}

	return fmt.Sprintf(" (%.1fs)", elapsed.Seconds()), nil
}

// detectTestCommand returns the appropriate test command for the current directory.
func detectTestCommand() (string, []string) {
	if _, err := os.Stat("go.mod"); err == nil {
		return "go", []string{"test", "./..."}
	}
	if _, err := os.Stat("pyproject.toml"); err == nil {
		return "uv", []string{"run", "pytest", "-q"}
	}
	if _, err := os.Stat("setup.py"); err == nil {
		return "uv", []string{"run", "pytest", "-q"}
	}
	// Default fallback
	return "go", []string{"test", "./..."}
}

// completeGitCommit stages all changes and commits with the provided message.
// Returns (true, nil) if a commit was made, (false, nil) if nothing to commit.
func completeGitCommit(message string, dryRun bool) (bool, error) {
	// Check for any changes (staged or unstaged)
	statusOut, err := runGitCmd("status", "--porcelain")
	if err != nil {
		return false, fmt.Errorf("git status: %w", err)
	}
	if strings.TrimSpace(statusOut) == "" {
		return false, nil
	}

	if dryRun {
		fmt.Printf("  [dry-run] Would run: git add -A && git commit -m %q\n", message)
		return true, nil
	}

	if _, err := runGitCmd("add", "-A"); err != nil {
		return false, fmt.Errorf("git add -A: %w", err)
	}

	// Re-check after staging (in case only untracked files were present)
	statusOut, err = runGitCmd("status", "--porcelain")
	if err != nil {
		return false, fmt.Errorf("git status: %w", err)
	}
	if strings.TrimSpace(statusOut) == "" {
		return false, nil
	}

	if _, err := runGitCmd("commit", "-m", message); err != nil {
		return false, err
	}
	return true, nil
}

// gitSimplePush does a plain `git push` (no rebase logic).
// For fleet-safe retry with rebase use `forge git push` instead.
func gitSimplePush(dryRun bool) error {
	if dryRun {
		fmt.Println("  [dry-run] Would run: git push")
		return nil
	}

	var out bytes.Buffer
	c := exec.Command("git", "push")
	c.Stdout = &out
	c.Stderr = &out
	if err := c.Run(); err != nil {
		return fmt.Errorf("git push: %w\n  %s", err, strings.TrimSpace(out.String()))
	}
	return nil
}

func init() {
	completeCmd.Flags().Bool("run-tests", false, "Run tests before committing (go test ./... or uv run pytest -q)")
	completeCmd.Flags().StringP("message", "m", "", "Commit message (default: auto-generated from task title)")
	completeCmd.Flags().Bool("no-push", false, "Skip git push")
	completeCmd.Flags().String("result", "", "Result summary passed to API (default: \"completed\")")
	completeCmd.Flags().Bool("dry-run", false, "Show what would happen without making any changes")

	rootCmd.AddCommand(completeCmd)
}
