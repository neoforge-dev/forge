package main

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// trinityCmd is the top-level `forge trinity` noun.
var trinityCmd = &cobra.Command{
	Use:   "trinity",
	Short: "Trinity → Prya delegation bridge",
	Long: `Delegate tasks from Trinity to Prya and track the delegation log.

Trinity is the AI assistant that receives Bogdan's Telegram messages.
This command bridges those requests into the FORGE task queue and
sends Telegram confirmation back.

Examples:
  forge trinity delegate "Deploy VC to Railway" --priority high --context "Bogdan asked via Telegram"
  forge trinity list
  forge trinity list --last 20`,
	Run: func(cmd *cobra.Command, args []string) {
		cmd.Help()
	},
}

// trinityDelegateCmd is `forge trinity delegate "task description"`.
var trinityDelegateCmd = &cobra.Command{
	Use:   "delegate [task description]",
	Short: "Delegate a task from Trinity to Prya via the FORGE task queue",
	Long: `Create a task in the FORGE queue with [TRINITY→PRYA] prefix, notify
the prya tmux window, send a Telegram alert, and log the delegation.

Examples:
  forge trinity delegate "Deploy VC to Railway"
  forge trinity delegate "Deploy VC to Railway" --priority high --context "Bogdan asked via Telegram"
  forge trinity delegate "Check deploy status" --notify=false
  forge trinity delegate "What would happen" --dry-run`,
	RunE: runTrinityDelegate,
}

// trinityListCmd is `forge trinity list`.
var trinityListCmd = &cobra.Command{
	Use:   "list",
	Short: "Show recent delegations from the Trinity delegation log",
	Long: `Read .forge/state/trinity/delegations.log and show recent entries.

Examples:
  forge trinity list
  forge trinity list --last 20`,
	RunE: runTrinityList,
}

func init() {
	trinityCmd.Hidden = true
	// Flags for delegate
	trinityDelegateCmd.Flags().String("priority", "medium", "Task priority (high/medium/low)")
	trinityDelegateCmd.Flags().String("context", "", "Summary from Trinity conversation (stored in task description)")
	trinityDelegateCmd.Flags().Bool("notify", true, "Send Telegram alert via 'forge notify alert'")
	trinityDelegateCmd.Flags().Bool("dry-run", false, "Show what would happen without making any changes")

	// Flags for list
	trinityListCmd.Flags().Int("last", 10, "Number of recent entries to show")

	trinityCmd.AddCommand(trinityDelegateCmd)
	trinityCmd.AddCommand(trinityListCmd)
}

// runTrinityDelegate implements `forge trinity delegate`.
func runTrinityDelegate(cmd *cobra.Command, args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("task description required\n  Usage: forge trinity delegate \"Deploy VC\" --priority high")
	}

	taskDesc := args[0]
	priority, _ := cmd.Flags().GetString("priority")
	contextStr, _ := cmd.Flags().GetString("context")
	notify, _ := cmd.Flags().GetBool("notify")
	dryRun, _ := cmd.Flags().GetBool("dry-run")

	// Validate priority value
	switch priority {
	case "high", "medium", "low":
		// valid
	default:
		return fmt.Errorf("invalid priority %q — must be high, medium, or low\n  Recovery: use --priority high, --priority medium, or --priority low", priority)
	}

	title := fmt.Sprintf("[TRINITY→PRYA] %s", taskDesc)

	out := cmd.OutOrStdout()
	if dryRun {
		fmt.Fprintf(out, "[dry-run] Would create task: %s\n", title)
		fmt.Fprintf(out, "[dry-run] Priority: %s\n", priority)
		if contextStr != "" {
			fmt.Fprintf(out, "[dry-run] Context: %s\n", contextStr)
		}
		fmt.Fprintf(out, "[dry-run] Would notify tmux window forge:prya\n")
		if notify {
			fmt.Fprintf(out, "[dry-run] Would send Telegram alert via 'forge notify alert'\n")
		}
		fmt.Fprintf(out, "[dry-run] Would log to .forge/state/trinity/delegations.log\n")
		return nil
	}

	// Step 1: Create task via daemon API
	var taskID string
	description := title
	if contextStr != "" {
		description = fmt.Sprintf("%s\n\nContext: %s", title, contextStr)
	}

	client := internal.NewClient()
	apiCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	req := &internal.CreateTaskRequest{
		Domain:      "forge",
		Project:     "trinity",
		Type:        "delegation",
		Subject:     title,
		Description: description,
		Priority:    priority,
	}

	task, err := client.CreateTask(apiCtx, req)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Warning: failed to create task via daemon: %v\n  Recovery: check 'forge daemon status' and ensure daemon is running\n", err)
	} else {
		taskID = task.ID
		fmt.Fprintf(out, "Task created: %s\n", taskID)
	}

	// Step 2: Notify forge:prya tmux window
	tmuxMsg := fmt.Sprintf("Trinity delegated: %s", title)
	if err := notifyAgentViaTmux("forge", "prya", tmuxMsg); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: tmux notification to forge:prya failed: %v\n  Recovery: check that 'forge:prya' tmux window exists\n", err)
	} else {
		fmt.Fprintln(out, "Tmux notification sent to forge:prya")
	}

	// Step 3: Send Telegram alert
	if notify {
		alertMsg := fmt.Sprintf("Trinity delegated: %s (priority: %s)", taskDesc, priority)
		if taskID != "" {
			alertMsg = fmt.Sprintf("Trinity delegated: %s (priority: %s, task: %s)", taskDesc, priority, taskID)
		}
		notifyExec := exec.Command("forge", "notify", "alert", alertMsg)
		notifyExec.Stderr = os.Stderr
		if err := notifyExec.Run(); err != nil {
			fmt.Fprintf(os.Stderr, "Warning: Telegram notification failed: %v\n  Recovery: run 'forge notify test' to verify openclaw setup\n", err)
		} else {
			fmt.Fprintln(out, "Telegram alert sent")
		}
	}

	// Step 4: Log delegation
	if err := logDelegation(title, priority, contextStr, taskID); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: failed to log delegation: %v\n", err)
	} else {
		fmt.Fprintln(out, "Delegation logged")
	}

	return nil
}

// runTrinityList implements `forge trinity list`.
func runTrinityList(cmd *cobra.Command, args []string) error {
	last, _ := cmd.Flags().GetInt("last")
	out := cmd.OutOrStdout()

	forgeRoot := forgeRootDir()
	logPath := filepath.Join(forgeRoot, ".forge", "state", "trinity", "delegations.log")

	data, err := os.ReadFile(logPath)
	if err != nil {
		if os.IsNotExist(err) {
			fmt.Fprintln(out, "No delegations logged yet.")
			fmt.Fprintf(out, "Log will be created at: %s\n", logPath)
			return nil
		}
		return fmt.Errorf("failed to read delegation log: %w\n  Recovery: check that %s is readable", err, logPath)
	}

	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(lines) == 0 || (len(lines) == 1 && lines[0] == "") {
		fmt.Fprintln(out, "No delegations logged yet.")
		return nil
	}

	// Show the last N lines
	start := 0
	if len(lines) > last {
		start = len(lines) - last
	}
	recent := lines[start:]

	fmt.Fprintf(out, "Recent delegations (last %d):\n\n", len(recent))
	for _, line := range recent {
		fmt.Fprintln(out, line)
	}
	return nil
}

// logDelegation appends a structured entry to the delegation log.
// Creates the log directory and file if they do not exist.
func logDelegation(title, priority, contextStr, taskID string) error {
	root := forgeRootDir()
	logDir := filepath.Join(root, ".forge", "state", "trinity")

	if err := os.MkdirAll(logDir, 0755); err != nil {
		return fmt.Errorf("create log directory %s: %w", logDir, err)
	}

	logPath := filepath.Join(logDir, "delegations.log")
	f, err := os.OpenFile(logPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return fmt.Errorf("open delegation log %s: %w", logPath, err)
	}
	defer f.Close()

	w := bufio.NewWriter(f)
	ts := time.Now().UTC().Format("2006-01-02 15:04:05 UTC")

	fmt.Fprintf(w, "[%s] priority=%s", ts, priority)
	if taskID != "" {
		fmt.Fprintf(w, " task=%s", taskID)
	}
	fmt.Fprintf(w, " | %s", title)
	if contextStr != "" {
		fmt.Fprintf(w, " | context: %s", contextStr)
	}
	fmt.Fprintln(w)

	return w.Flush()
}
