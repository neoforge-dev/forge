package main

import (
	"context"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

const agentLivenessWindow = 5 * time.Minute

// AgentLiveness represents the liveness classification of an agent.
type AgentLiveness struct {
	// Status is one of: ALIVE, STALE, OFFLINE, UNKNOWN
	Status      string
	LastSeen    time.Time
	CurrentTask *string
}

// checkAgentLiveness queries the daemon for the given agentID and classifies
// its liveness state. The caller is responsible for setting an appropriate
// deadline on ctx.
//
// Status semantics:
//
//	ALIVE   — status=="online" AND last_seen within the last 5 minutes
//	STALE   — status=="online" BUT last_seen older than 5 minutes
//	OFFLINE — agent found in DB but status != "online"
//	UNKNOWN — agent not registered in the daemon at all (404 or not in list)
func checkAgentLiveness(ctx context.Context, client *internal.Client, agentID string) (AgentLiveness, error) {
	health, err := client.GetAgent(ctx, agentID)
	if err != nil {
		// A 404 from the daemon means the agent is not registered.
		// Return UNKNOWN without propagating the error so callers can still
		// fall back to tmux checks.
		if strings.Contains(err.Error(), "status 404") || strings.Contains(err.Error(), "not found") {
			return AgentLiveness{Status: "UNKNOWN"}, nil
		}
		return AgentLiveness{}, fmt.Errorf("query agent %s: %w", agentID, err)
	}

	liveness := AgentLiveness{
		LastSeen:    health.LastSeen,
		CurrentTask: health.CurrentTask,
	}

	switch {
	case health.Status != "online":
		liveness.Status = "OFFLINE"
	case time.Since(health.LastSeen) > agentLivenessWindow:
		liveness.Status = "STALE"
	default:
		liveness.Status = "ALIVE"
	}

	return liveness, nil
}

// agentPingCmd: forge agent ping <name>
var agentPingCmd = &cobra.Command{
	Use:   "ping <agent-id>",
	Short: "Check if an agent is alive",
	Long: `Query the daemon for the named agent's heartbeat status.

Exit codes:
  0 — ALIVE (online and recent heartbeat)
  1 — not alive (STALE, OFFLINE, or UNKNOWN)

Examples:
  forge agent ping kimi
  forge agent ping forge:glm`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		agentID := parseAgentID(args[0])

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		liveness, err := checkAgentLiveness(ctx, client, agentID)
		if err != nil {
			return fmt.Errorf("ping %s: %w\n  Run 'forge daemon status' to check daemon health", agentID, err)
		}

		switch liveness.Status {
		case "ALIVE":
			fmt.Printf("ALIVE\n")
			fmt.Printf("Agent:     %s\n", agentID)
			fmt.Printf("Last seen: %s (%s ago)\n", liveness.LastSeen.Format(time.RFC3339), time.Since(liveness.LastSeen).Round(time.Second))
			if liveness.CurrentTask != nil && *liveness.CurrentTask != "" {
				fmt.Printf("Task:      %s\n", *liveness.CurrentTask)
			}
			return nil

		case "STALE":
			fmt.Printf("STALE\n")
			fmt.Printf("Agent:     %s\n", agentID)
			fmt.Printf("Last seen: %s (%s ago)\n", liveness.LastSeen.Format(time.RFC3339), time.Since(liveness.LastSeen).Round(time.Second))
			if liveness.CurrentTask != nil && *liveness.CurrentTask != "" {
				fmt.Printf("Task:      %s\n", *liveness.CurrentTask)
			}
			fmt.Fprintf(os.Stderr, "Agent %s has a stale heartbeat (last seen >5 min ago).\n  Use 'forge fleet spawn %s' to restart it.\n", agentID, agentID)
			os.Exit(1)

		case "OFFLINE":
			fmt.Printf("OFFLINE\n")
			fmt.Printf("Agent:     %s\n", agentID)
			if !liveness.LastSeen.IsZero() {
				fmt.Printf("Last seen: %s (%s ago)\n", liveness.LastSeen.Format(time.RFC3339), time.Since(liveness.LastSeen).Round(time.Second))
			}
			fmt.Fprintf(os.Stderr, "Agent %s is offline.\n  Use 'forge fleet spawn %s' to start it.\n", agentID, agentID)
			os.Exit(1)

		case "UNKNOWN":
			fmt.Printf("UNKNOWN\n")
			fmt.Printf("Agent:     %s\n", agentID)
			fmt.Fprintf(os.Stderr, "Agent %s is not registered with the daemon.\n  Use 'forge agent list' to see registered agents.\n", agentID)
			os.Exit(1)
		}

		return nil
	},
}
