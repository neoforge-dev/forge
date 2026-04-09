package main

import (
	"context"
	"fmt"
	"os"
	"sort"
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
	Use:     "agent",
	Aliases: []string{"ag"},
	Short:   "Manage agents",
	Long:    "List, monitor, and manage FORGE agents.",
}

// agentListCmd: forge agent list
var agentListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all agents",
	Long: `List all connected agents with their status and capabilities.

With --work-state, agents are enriched with derived work_state (idle/working/blocked)
and sorted by relevance: blocked first, then working, then idle.

Examples:
  forge agent list
  forge agent list --work-state
  forge agent list --work-state --format json`,
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")
		showWorkState, _ := cmd.Flags().GetBool("work-state")

		if showWorkState {
			// Enrich with derived work_state and delegate to the status renderer.
			apiURL := internal.ResolveControlPlaneURL()
			agents, err := fetchAgents(apiURL)
			if err != nil {
				return fmt.Errorf("failed to list agents: %w\n  Check FORGE_API_URL or run: forge daemon status", err)
			}

			entries := make([]agentStatusEntry, 0, len(agents))
			for _, a := range agents {
				entries = append(entries, agentStatusEntry{
					agentHealthEntry: a,
					WorkState:        deriveWorkState(a),
				})
			}
			// Sort: blocked(0) > working(1) > idle(2); secondary: most recent heartbeat first.
			sort.SliceStable(entries, func(i, j int) bool {
				ri, rj := workStateRank(entries[i].WorkState), workStateRank(entries[j].WorkState)
				if ri != rj {
					return ri < rj
				}
				return entries[i].LastSeen.After(entries[j].LastSeen)
			})
			switch format {
			case "json":
				return printAgentStatusJSON(entries)
			default:
				return printAgentStatusTable(entries)
			}
		}

		// Default path: plain list via the internal client formatter.
		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		result, err := client.ListAgents(ctx)
		if err != nil {
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
	Long:  "Send a heartbeat to the daemon for the given agent, optionally reporting its context window usage and work state.",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		agentID := args[0]
		contextPct, _ := cmd.Flags().GetFloat64("context-pct")
		workState, _ := cmd.Flags().GetString("work-state")

		// Validate work-state if provided.
		if workState != "" && workState != "idle" && workState != "working" && workState != "blocked" {
			return fmt.Errorf("invalid --work-state %q: must be one of idle, working, blocked", workState)
		}

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		if err := client.SendHeartbeatWithState(ctx, agentID, contextPct, workState); err != nil {
			return fmt.Errorf("heartbeat failed: %w", err)
		}
		if workState != "" {
			fmt.Printf("Heartbeat sent for agent %s (context: %.1f%%, work_state: %s)\n", agentID, contextPct, workState)
		} else {
			fmt.Printf("Heartbeat sent for agent %s (context: %.1f%%)\n", agentID, contextPct)
		}
		return nil
	},
}

func init() {
	// Add commands to agent noun
	agentCmd.AddCommand(agentListCmd)
	agentCmd.AddCommand(agentShowCmd)
	agentCmd.AddCommand(agentTasksCmd)
	agentCmd.AddCommand(agentHeartbeatCmd)
	agentCmd.AddCommand(agentPingCmd)

	// agentListCmd flags
	agentListCmd.Flags().Bool("work-state", false, "Show work_state column (idle/working/blocked) sorted by relevance")

	// agentHeartbeatCmd flags
	agentHeartbeatCmd.Flags().Float64("context-pct", 0.0, "Context window usage percentage (0-100)")
	agentHeartbeatCmd.Flags().String("work-state", "", "Agent work state: idle, working, or blocked")
}
