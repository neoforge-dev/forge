package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// getLocalAgentID returns a suggested worker ID for hint messages.
// Uses the hostname so the suggestion is immediately actionable.
func getLocalAgentID() string {
	if h, err := os.Hostname(); err == nil {
		return h
	}
	return "my-worker"
}

// agentCmd represents the agent noun
var agentCmd = &cobra.Command{
	Use:   "agent",
	Short: "Manage agents",
	Long:  "List, monitor, and manage FORGE agents.",
}

// agentListCmd: forge agent list
var agentListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all agents",
	Long:  "List all connected agents with their status and capabilities.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		result, err := client.ListAgents(ctx)
		if err != nil {
			var derr *internal.DaemonUnreachableError
			if errors.As(err, &derr) {
				return fmt.Errorf(
					"daemon not reachable at %s\n  Recovery: forge daemon start\n           OR check: curl http://localhost:8081/api/tasks",
					derr.URL,
				)
			}
			return fmt.Errorf("failed to list agents: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if err := formatter.FormatAgents(result.Agents); err != nil {
			return err
		}

		if format == "table" {
			if len(result.Agents) == 0 {
				formatter.Printf("\nTotal: 0 agents (no workers connected — start one with: forge worker up --id %s)\n", getLocalAgentID())
			} else {
				formatter.Printf("\nTotal: %d agents\n", len(result.Agents))
			}
		}
		return nil
	},
}

// agentShowCmd: forge agent show <agent-id>
var agentShowCmd = &cobra.Command{
	Use:   "show <agent-id>",
	Short: "Show agent details",
	Long:  "Display detailed information about a specific agent.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		agentID := args[0]
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		health, err := client.GetAgent(ctx, agentID)
		if err != nil {
			return fmt.Errorf("failed to get agent: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatAgentHealth(health)
	},
}

// agentTasksCmd: forge agent tasks <agent-id>
var agentTasksCmd = &cobra.Command{
	Use:   "tasks <agent-id>",
	Short: "List tasks for an agent",
	Long:  "List all tasks assigned to or claimed by a specific agent.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		agentID := args[0]
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		result, err := client.GetAgentTasks(ctx, agentID)
		if err != nil {
			return fmt.Errorf("failed to get agent tasks: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		if err := formatter.FormatTasks(result.Tasks); err != nil {
			return err
		}

		if format == "table" {
			formatter.Printf("\nTotal: %d tasks for agent %s\n", len(result.Tasks), agentID)
		}
		return nil
	},
}

// agentHeartbeatCmd: forge agent heartbeat <agent-id>
var agentHeartbeatCmd = &cobra.Command{
	Use:   "heartbeat <agent-id>",
	Short: "Send a heartbeat for an agent",
	Long:  "Send a heartbeat to the daemon for the given agent, optionally reporting its context window usage.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		agentID := args[0]
		contextPct, _ := cmd.Flags().GetFloat64("context-pct")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		if err := client.SendHeartbeat(ctx, agentID, contextPct); err != nil {
			return fmt.Errorf("heartbeat failed: %w", err)
		}
		fmt.Printf("Heartbeat sent for agent %s (context: %.1f%%)\n", agentID, contextPct)
		return nil
	},
}

func init() {
	// Add commands to agent noun
	agentCmd.AddCommand(agentListCmd)
	agentCmd.AddCommand(agentShowCmd)
	agentCmd.AddCommand(agentTasksCmd)
	agentCmd.AddCommand(agentHeartbeatCmd)

	agentHeartbeatCmd.Flags().Float64("context-pct", 0.0, "Context window usage percentage (0-100)")
}
