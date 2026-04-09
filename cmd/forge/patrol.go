package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// Patrol represents a background monitoring process
type Patrol struct {
	ID        string                 `json:"id"`
	Name      string                 `json:"name"`
	Type      string                 `json:"type"`      // health, heartbeat, quality, deploy
	Status    string                 `json:"status"`    // running, stopped, error, warning
	Interval  int                    `json:"interval"`  // seconds
	LastRun   time.Time              `json:"last_run"`
	NextRun   time.Time              `json:"next_run,omitempty"`
	Message   string                 `json:"message,omitempty"`
	Config    map[string]interface{} `json:"config,omitempty"`
	Successes int                    `json:"successes"`
	Failures  int                    `json:"failures"`
}

// patrolCmd represents the patrol noun
var patrolCmd = &cobra.Command{
	Use:     "patrol",
	Aliases: []string{"pt"},
	Short:   "Background monitors and patrols",
	Long: `Patrols are background monitoring processes that watch over FORGE operations.

Types:
  - health: Monitor agent health and connectivity
  - heartbeat: Track agent heartbeats and timeouts
  - quality: Monitor code quality gates
  - deploy: Monitor deployment status
  - queue: Monitor task queue depth

Examples:
  # List all patrols
  forge patrol list

  # Show patrol status
  forge patrol status patrol-fleet

  # Show recent logs
  forge patrol logs patrol-fleet --lines 50`,
}

// patrolGroupName maps a patrol type (derived from ID prefix) to one of 6 display groups.
func patrolGroupName(patrolType string) string {
	switch patrolType {
	case "health", "heartbeat", "liveness", "agent", "metrics", "confidence", "node":
		return "health"
	case "task", "approval", "stale":
		return "scheduling"
	case "queue", "fleet", "token":
		return "capacity"
	case "result", "xnode", "dispatch", "inbox", "context":
		return "sync"
	case "git", "data", "worktree", "hygiene", "council", "daily":
		return "hygiene"
	case "work", "auto", "binary", "strategy":
		return "autonomy"
	default:
		return "other"
	}
}

// printPatrolGroups renders the 6-group summary view of patrols.
func printPatrolGroups(patrols []Patrol) {
	type groupStats struct {
		total    int
		running  int
		warnings int
		errors   int
	}
	groups := map[string]*groupStats{}
	order := []string{"health", "scheduling", "capacity", "sync", "hygiene", "autonomy", "other"}
	for _, g := range order {
		groups[g] = &groupStats{}
	}

	for _, p := range patrols {
		g := patrolGroupName(p.Type)
		if _, ok := groups[g]; !ok {
			groups[g] = &groupStats{}
		}
		gs := groups[g]
		gs.total++
		switch p.Status {
		case "running":
			gs.running++
		case "warning":
			gs.warnings++
		case "error":
			gs.errors++
		}
	}

	fmt.Printf("%-12s  %6s  %7s  %8s  %6s\n", "GROUP", "TOTAL", "RUNNING", "WARNINGS", "ERRORS")
	fmt.Printf("%-12s  %6s  %7s  %8s  %6s\n", "-----", "-----", "-------", "--------", "------")
	for _, name := range order {
		gs := groups[name]
		if gs.total == 0 {
			continue
		}
		errMark := ""
		if gs.errors > 0 {
			errMark = " !"
		}
		warnMark := ""
		if gs.warnings > 0 {
			warnMark = " ~"
		}
		fmt.Printf("%-12s  %6d  %7d  %8d  %6d%s%s\n",
			name, gs.total, gs.running, gs.warnings, gs.errors, warnMark, errMark)
	}
	fmt.Printf("\nUse --all to see individual patrols.\n")
}

// patrolListCmd: forge patrol list
var patrolListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all patrols",
	Long:  "List background monitoring patrols grouped by function. Use --all for individual view.",
	RunE: func(cmd *cobra.Command, args []string) error {
		patrolType, _ := cmd.Flags().GetString("type")
		format, _ := cmd.Flags().GetString("format")
		showAll, _ := cmd.Flags().GetBool("all")

		patrols, err := loadPatrols()
		if err != nil {
			return fmt.Errorf("failed to load patrols: %w", err)
		}

		// Filter by type if specified
		if patrolType != "" {
			var filtered []Patrol
			for _, p := range patrols {
				if strings.EqualFold(p.Type, patrolType) {
					filtered = append(filtered, p)
				}
			}
			patrols = filtered
		}

		// Default: grouped view
		if !showAll && format == "table" {
			printPatrolGroups(patrols)
			return nil
		}

		// --all: individual patrol view (or JSON)
		patrolInfos := make([]internal.PatrolInfo, len(patrols))
		for i, p := range patrols {
			patrolInfos[i] = internal.PatrolInfo{
				ID:        p.ID,
				Name:      p.Name,
				Type:      p.Type,
				Status:    p.Status,
				Interval:  p.Interval,
				LastRun:   p.LastRun,
				NextRun:   p.NextRun,
				Message:   p.Message,
				Config:    p.Config,
				Successes: p.Successes,
				Failures:  p.Failures,
			}
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatPatrols(patrolInfos)
	},
}

// patrolStatusCmd: forge patrol status <patrol-id>
var patrolStatusCmd = &cobra.Command{
	Use:   "status <patrol-id>",
	Short: "Show patrol status",
	Long:  "Display detailed status for a specific patrol.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		patrolID := args[0]
		format, _ := cmd.Flags().GetString("format")

		patrols, err := loadPatrols()
		if err != nil {
			return fmt.Errorf("failed to load patrols: %w", err)
		}

		// Find the patrol
		var found *Patrol
		for i := range patrols {
			if patrols[i].ID == patrolID {
				found = &patrols[i]
				break
			}
		}

		if found == nil {
			return fmt.Errorf("patrol not found: %s", patrolID)
		}

		// Convert to PatrolInfo
		patrolInfo := &internal.PatrolInfo{
			ID:        found.ID,
			Name:      found.Name,
			Type:      found.Type,
			Status:    found.Status,
			Interval:  found.Interval,
			LastRun:   found.LastRun,
			NextRun:   found.NextRun,
			Message:   found.Message,
			Config:    found.Config,
			Successes: found.Successes,
			Failures:  found.Failures,
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatPatrolStatus(patrolInfo)
	},
}

// patrolLogsCmd: forge patrol logs <patrol-id>
var patrolLogsCmd = &cobra.Command{
	Use:   "logs <patrol-id>",
	Short: "Show patrol logs",
	Long:  "Display recent logs for a specific patrol.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		patrolID := args[0]
		lines, _ := cmd.Flags().GetInt("lines")
		format, _ := cmd.Flags().GetString("format")

		// Load patrol to verify it exists
		patrols, err := loadPatrols()
		if err != nil {
			return fmt.Errorf("failed to load patrols: %w", err)
		}

		var found *Patrol
		for i := range patrols {
			if patrols[i].ID == patrolID {
				found = &patrols[i]
				break
			}
		}

		if found == nil {
			return fmt.Errorf("patrol not found: %s", patrolID)
		}

		// Generate demo logs
		logs := generatePatrolLogs(found, lines)

		if format == "json" {
			data, _ := json.MarshalIndent(logs, "", "  ")
			fmt.Println(string(data))
		} else {
			fmt.Printf("Logs for %s (%s):\n\n", found.Name, found.ID)
			for _, log := range logs {
				fmt.Printf("[%s] %s %s\n", log.Timestamp.Format(time.RFC3339), log.Level, log.Message)
			}
		}

		return nil
	},
}

// PatrolLog represents a log entry from a patrol
type PatrolLog struct {
	Timestamp time.Time `json:"timestamp"`
	Level     string    `json:"level"` // INFO, WARN, ERROR
	Message   string    `json:"message"`
}

// loadPatrols loads patrols from demo data or config file
func loadPatrols() ([]Patrol, error) {
	// 1. Try live v3 API
	client := internal.NewClient()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if v3Patrols, err := client.ListPatrols(ctx); err == nil && len(v3Patrols) > 0 {
		patrols := make([]Patrol, 0, len(v3Patrols))
		for _, p := range v3Patrols {
			lastRun := time.Time{}
			if p.LastRun != nil {
				if t, err := time.Parse(time.RFC3339, *p.LastRun); err == nil {
					lastRun = t
				}
			}
			// Derive Type from patrol ID prefix (e.g. "health-check" → "health")
			patrolType := strings.SplitN(p.ID, "-", 2)[0]
			if patrolType == "" {
				patrolType = "health"
			}
			patrols = append(patrols, Patrol{
				ID:       p.ID,
				Name:     p.Name,
				Type:     patrolType,
				Status:   p.Status,
				Interval: p.IntervalSeconds,
				LastRun:  lastRun,
				Failures: p.ErrorCount,
			})
		}
		return patrols, nil
	}

	// 2. Try local config file
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = findForgeRoot()
	}
	if forgeRoot != "" {
		configPath := filepath.Join(forgeRoot, ".forge", "patrols.json")
		if data, err := os.ReadFile(configPath); err == nil {
			var patrols []Patrol
			if err := json.Unmarshal(data, &patrols); err == nil {
				return patrols, nil
			}
		}
	}

	// 3. No patrols available — return empty results.
	fmt.Fprintf(os.Stderr, "Warning: daemon unreachable and no local patrols.json — no patrols to show\n  Recovery: forge daemon start\n")
	return []Patrol{}, nil
}


// generatePatrolLogs generates demo log entries for a patrol
func generatePatrolLogs(patrol *Patrol, lines int) []PatrolLog {
	// Handle edge cases
	if lines <= 0 {
		return []PatrolLog{}
	}

	logs := make([]PatrolLog, 0, lines)
	now := time.Now()

	messages := []struct {
		level   string
		message string
	}{
		{"INFO", fmt.Sprintf("Patrol %s started check", patrol.Name)},
		{"INFO", "Checking all registered agents"},
		{"INFO", fmt.Sprintf("Found %d active agents", patrol.Successes)},
	}

	if patrol.Status == "warning" {
		messages = append(messages, struct {
			level   string
			message string
		}{"WARN", patrol.Message})
	}

	for i := 0; i < lines && i < len(messages)*3; i++ {
		msg := messages[i%len(messages)]
		logs = append(logs, PatrolLog{
			Timestamp: now.Add(-time.Duration(i*patrol.Interval) * time.Second),
			Level:     msg.level,
			Message:   msg.message,
		})
	}

	// Logs are already newest first (i=0 = now, i=1 = now-interval, etc.)
	return logs
}

// patrolTaskTimeoutCmd: forge patrol task-timeout
// Prunes stale assigned tasks and optionally loops.
var patrolTaskTimeoutCmd = &cobra.Command{
	Use:   "task-timeout",
	Short: "Abandon stale assigned tasks (auto-recovery patrol)",
	Long: `Abandon tasks stuck in 'assigned' state for longer than the threshold.

Calls POST /api/tasks/prune on the v3 daemon, which marks all assigned tasks
older than 2 hours as 'abandoned'. Use --daemon to run continuously.

Examples:
  forge patrol task-timeout              # Run once
  forge patrol task-timeout --daemon     # Run every 10 minutes forever
  forge patrol task-timeout --daemon --interval 5m
`,
	RunE: func(cmd *cobra.Command, args []string) error {
		daemon, _ := cmd.Flags().GetBool("daemon")
		interval, _ := cmd.Flags().GetDuration("interval")
		format, _ := cmd.Flags().GetString("format")
		client := internal.NewClient()

		runOnce := func() error {
			ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			defer cancel()

			resp, err := client.Post(ctx, "/tasks/prune", nil)
			if err != nil {
				return fmt.Errorf("prune failed: %w", err)
			}
			defer resp.Body.Close()
			if err := internal.CheckResponse(resp); err != nil {
				return err
			}
			var result map[string]interface{}
			internal.DecodeJSON(resp, &result)

			ts := time.Now().Format("15:04:05")
			if format == "json" {
				result["timestamp"] = ts
				return json.NewEncoder(os.Stdout).Encode(result)
			}
			if abandoned, ok := result["abandoned"].(float64); ok && abandoned > 0 {
				fmt.Printf("[%s] Pruned %.0f stale assigned tasks\n", ts, abandoned)
			} else {
				fmt.Printf("[%s] No stale tasks (queue healthy)\n", ts)
			}
			return nil
		}

		if !daemon {
			return runOnce()
		}

		fmt.Printf("Task timeout patrol started (interval: %s)\n", interval)
		fmt.Printf("Press Ctrl+C to stop\n\n")
		runOnce()

		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for range ticker.C {
			if err := runOnce(); err != nil {
				fmt.Fprintf(os.Stderr, "patrol error: %v\n", err)
			}
		}
		return nil
	},
}

func init() {
	patrolCmd.AddCommand(patrolListCmd)
	patrolCmd.AddCommand(patrolStatusCmd)
	patrolCmd.AddCommand(patrolLogsCmd)
	patrolCmd.AddCommand(patrolTaskTimeoutCmd)

	// Flags for list
	patrolListCmd.Flags().String("type", "", "Filter by patrol type (health, heartbeat, quality, deploy)")
	patrolListCmd.Flags().Bool("all", false, "Show individual patrols instead of grouped summary")

	// Flags for logs
	patrolLogsCmd.Flags().IntP("lines", "n", 20, "Number of log lines to show")

	// Flags for task-timeout
	patrolTaskTimeoutCmd.Flags().Bool("daemon", false, "Run continuously on a schedule")
	patrolTaskTimeoutCmd.Flags().Duration("interval", 10*time.Minute, "How often to run in daemon mode")
}
