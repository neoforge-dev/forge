package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// getTaskStoreForCLI returns a TaskStore for CLI commands.
// It tries the global connection first (if daemon is running),
// otherwise opens a direct database connection.
func getTaskStoreForCLI() (*TaskStore, error) {
	// Try global connection first (daemon mode)
	if db := getDBConn(); db != nil {
		return NewTaskStore(db), nil
	}

	// Open direct connection for standalone CLI usage
	dbPath := os.Getenv("DB_PATH")
	if dbPath == "" {
		dbPath = defaultDBPath
	}

	db, err := OpenDB(dbPath)
	if err != nil {
		return nil, fmt.Errorf("failed to open database: %w (daemon not running and couldn't open DB directly)", err)
	}

	// Don't close the DB here - let it be used for the command lifetime
	// In daemon mode, this is managed by main(). In CLI mode, it lives for the command.
	return NewTaskStore(db), nil
}

// taskCmd manages task operations
var taskCmd = &cobra.Command{
	Use:   "task",
	Short: "Manage tasks",
	Long:  "Create, list, complete, and manage tasks in the queue.",
}

// taskListCmd lists all tasks
var taskListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all tasks",
	RunE: func(cmd *cobra.Command, args []string) error {
		stateStr, _ := cmd.Flags().GetString("state")

		store, err := getTaskStoreForCLI()
		if err != nil {
			return err
		}

		var tasks []Task

		if stateStr != "" {
			tasks, err = store.GetTasksByState(TaskState(stateStr))
		} else {
			// Get all tasks by querying each state
			for _, state := range []TaskState{StateQueued, StateDispatched, StateRunning, StateBlocked, StateCompleted, StateFailed, StateApproved} {
				stateTasks, _ := store.GetTasksByState(state)
				tasks = append(tasks, stateTasks...)
			}
		}

		if err != nil {
			return err
		}

		fmt.Printf("%-20s | %-12s | %-8s | %-20s | %s\n", "ID", "STATE", "PRIORITY", "DOMAIN", "PROJECT")
		fmt.Println("-----------------------------------------------------------------------------------------")
		for _, task := range tasks {
			fmt.Printf("%-20s | %-12s | %-8d | %-20s | %s\n",
				truncate(task.ID, 20),
				task.State,
				task.Priority,
				truncate(task.Domain, 20),
				truncate(task.Project, 30))
		}
		return nil
	},
}

// getAgentID determines the agent ID from flags, env, or tmux window name
func getAgentID(cmd *cobra.Command) (string, error) {
	// 1. Check --agent flag
	agentID, _ := cmd.Flags().GetString("agent")
	if agentID != "" {
		return agentID, nil
	}

	// 2. Check FORGE_AGENT_ID environment variable
	agentID = os.Getenv("FORGE_AGENT_ID")
	if agentID != "" {
		return agentID, nil
	}

	// 3. Check if running in tmux and get window name
	if os.Getenv("TMUX") != "" {
		// Try to get current tmux window name
		out, err := exec.Command("tmux", "display-message", "-p", "#W").Output()
		if err == nil {
			windowName := strings.TrimSpace(string(out))
			// Clean up common tmux window name suffixes/prefixes
			windowName = strings.TrimSuffix(windowName, "-")
			windowName = strings.TrimSuffix(windowName, "*")
			windowName = strings.TrimSuffix(windowName, "Z")
			if windowName != "" && windowName != "bash" && windowName != "zsh" {
				return windowName, nil
			}
		}
	}

	return "", fmt.Errorf("agent ID required. Use --agent, set FORGE_AGENT_ID, or run from a named tmux window")
}

// taskNextCmd gets the next task for an agent
var taskNextCmd = &cobra.Command{
	Use:   "next",
	Short: "Get the next task from the queue",
	Long: `Retrieves the highest priority QUEUED task and assigns it to the calling agent.

Agent ID is determined in this order:
  1. --agent flag
  2. FORGE_AGENT_ID environment variable  
  3. Current tmux window name (if running in tmux)

Examples:
  forge task next --agent=kimi
  FORGE_AGENT_ID=kimi forge task next
  # Inside tmux window named "cursor-2":
  forge task next  # Uses "cursor-2" as agent ID`,
	RunE: func(cmd *cobra.Command, args []string) error {
		agentID, err := getAgentID(cmd)
		if err != nil {
			return err
		}

		store, err := getTaskStoreForCLI()
		if err != nil {
			return err
		}

		// Get next queued task
		tasks, err := store.GetTasksByState(StateQueued)
		if err != nil {
			return err
		}

		if len(tasks) == 0 {
			fmt.Println("{\"status\": \"empty\", \"message\": \"No tasks available in queue\"}")
			return nil
		}

		// Get highest priority task (assuming sorted by priority)
		task := tasks[0]

		// Claim task (assign to agent and transition to DISPATCHED)
		err = store.ClaimTask(task.ID, agentID)
		if err != nil {
			return err
		}

		// Output as JSON
		output := map[string]interface{}{
			"status":    "assigned",
			"task_id":   task.ID,
			"domain":    task.Domain,
			"project":   task.Project,
			"type":      task.Type,
			"priority":  task.Priority,
			"agent":     agentID,
			"timestamp": time.Now().UTC().Format(time.RFC3339),
		}

		jsonBytes, _ := json.MarshalIndent(output, "", "  ")
		fmt.Println(string(jsonBytes))
		return nil
	},
}

// queueCmd manages the queue
var queueCmd = &cobra.Command{
	Use:   "queue",
	Short: "Manage task queue",
	Long:  "Queue operations for task scheduling and prioritization.",
}

// queueDepthCmd shows queue statistics
var queueDepthCmd = &cobra.Command{
	Use:   "depth",
	Short: "Show queue depth by state",
	RunE: func(cmd *cobra.Command, args []string) error {
		store, err := getTaskStoreForCLI()
		if err != nil {
			return err
		}

		states := []TaskState{StateQueued, StateDispatched, StateRunning, StateBlocked, StateCompleted, StateFailed, StateApproved}
		stateNames := []string{"QUEUED", "DISPATCHED", "RUNNING", "BLOCKED", "COMPLETED", "FAILED", "APPROVED"}

		fmt.Println("Queue Statistics:")
		fmt.Println("=================")
		total := 0
		for i, state := range states {
			tasks, err := store.GetTasksByState(state)
			if err != nil {
				return err
			}
			fmt.Printf("  %-12s: %d\n", stateNames[i], len(tasks))
			total += len(tasks)
		}
		fmt.Println("  ----------------")
		fmt.Printf("  %-12s: %d\n", "TOTAL", total)
		return nil
	},
}

// queuePopulateCmd populates tasks from features.json
var queuePopulateCmd = &cobra.Command{
	Use:   "populate",
	Short: "Populate tasks from features.json",
	Long:  `Reads .forge/v3/features-24h-test.json and inserts all DF tasks into the queue.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		featuresPath, _ := cmd.Flags().GetString("features")
		dbPath := os.Getenv("DB_PATH")
		if dbPath == "" {
			dbPath = defaultDBPath
		}

		fmt.Printf("Populating tasks from %s...\n", featuresPath)
		if err := PopulateTasksFromFeatures(dbPath, featuresPath); err != nil {
			return fmt.Errorf("populate tasks: %w", err)
		}
		return nil
	},
}

// queueImportDispatchesCmd imports dispatch files as tasks
var queueImportDispatchesCmd = &cobra.Command{
	Use:   "import-dispatches",
	Short: "Import dispatch files as tasks",
	Long:  `Reads all .md files from .forge/dispatches/ and inserts them as tasks.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		dispatchesDir, _ := cmd.Flags().GetString("dir")
		dbPath := os.Getenv("DB_PATH")
		if dbPath == "" {
			dbPath = defaultDBPath
		}

		fmt.Printf("Importing dispatches from %s...\n", dispatchesDir)
		if err := PopulateTasksFromDispatches(dbPath, dispatchesDir); err != nil {
			return fmt.Errorf("import dispatches: %w", err)
		}
		return nil
	},
}

// Helper to truncate string
func truncate(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen-3] + "..."
}

// Dashboard command
var dashboardCmd = &cobra.Command{
	Use:   "dashboard",
	Short: "Launch TUI dashboard",
	Long:  "Start the terminal user interface dashboard for monitoring FORGE v3.",
	RunE: func(cmd *cobra.Command, args []string) error {
		apiURL, _ := cmd.Flags().GetString("api-url")
		if apiURL == "" {
			apiURL = os.Getenv("FORGE_API_URL")
		}
		if apiURL == "" {
			apiURL = "http://localhost:8081"
		}

		return RunDashboard(apiURL)
	},
}

// registerCommands adds all CLI commands to root
func registerCommands() {
	// Add task subcommands
	taskCmd.AddCommand(taskListCmd)
	taskCmd.AddCommand(taskNextCmd)

	taskListCmd.Flags().String("state", "", "Filter by state (QUEUED, DISPATCHED, RUNNING, etc.)")
	taskNextCmd.Flags().String("agent", "", "Agent ID (auto-detects from tmux window if not set)")

	// Add queue subcommands
	queueCmd.AddCommand(queueDepthCmd)
	queueCmd.AddCommand(queuePopulateCmd)
	queueCmd.AddCommand(queueImportDispatchesCmd)
	queuePopulateCmd.Flags().String("features", ".forge/v3/features-24h-test.json", "Path to features.json file")
	queueImportDispatchesCmd.Flags().String("dir", ".forge/dispatches", "Directory containing dispatch files")

	// Add dashboard command
	dashboardCmd.Flags().String("api-url", "", "FORGE v3 API URL (default: http://localhost:8081 or FORGE_API_URL env)")
	rootCmd.AddCommand(dashboardCmd)

	// Register all commands to root
	rootCmd.AddCommand(taskCmd)
	rootCmd.AddCommand(queueCmd)
}

// init function to handle CLI-only database lifecycle
var cliDB *sql.DB

func init() {
	// Register cleanup for CLI mode
	// Note: In daemon mode, dbConn is managed by main()
}
