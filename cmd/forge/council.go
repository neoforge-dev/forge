// council.go — forge council commands
//
// Implements council-as-a-service (ADR-035 §Council lifecycle):
//   forge council start  --size N --ttl 30m  Start a council session
//   forge council status                      Show active council sessions
//   forge council stop   [agent]              End a council session early

package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// councilMarker is written to .forge/autoscale/council/{agent}.json.
// The daemon's councilCleanupPatrol reads these files every 2 minutes.
type councilMarker struct {
	Agent     string `json:"agent"`
	Purpose   string `json:"purpose"`
	TTLMin    int    `json:"ttl_minutes"`
	StartedAt string `json:"started_at"`
	ExpiresAt string `json:"expires_at"`
	NodeID    string `json:"node_id"`
}

// defaultCouncilAgents is the ordered priority list used when --size is specified
// without explicit agent names. Lightweights first to stay within budget.
var defaultCouncilAgents = []string{"kimi", "gemini", "claude", "minimax", "pi"}

var councilCmd = &cobra.Command{
	Use:   "council",
	Short: "Council-as-a-service (ADR-035)",
	Long: `Manage bounded-burst council sessions across the fleet.

A council session spawns N agents for focused parallel review. Council agents
are protected from the auto-deflation patrol for the duration of the session TTL.

After the TTL expires, the councilCleanupPatrol removes markers and emits
deflation recommendations for human approval.

Examples:
  # Start a 3-agent council with 30-minute TTL
  forge council start --size 3 --ttl 30m

  # Start with specific agents
  forge council start --agents kimi,gemini,claude --ttl 1h

  # Check active council sessions
  forge council status

  # End a session early
  forge council stop kimi
  forge council stop --all
`,
}

var councilStartCmd = &cobra.Command{
	Use:   "start",
	Short: "Start a council session",
	RunE:  runCouncilStart,
}

var councilStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show active council sessions",
	RunE:  runCouncilStatus,
}

var councilStopCmd = &cobra.Command{
	Use:   "stop [agent]",
	Short: "End a council session (agent or --all)",
	RunE:  runCouncilStop,
}

func runCouncilStart(cmd *cobra.Command, args []string) error {
	size, _ := cmd.Flags().GetInt("size")
	ttlStr, _ := cmd.Flags().GetString("ttl")
	agentList, _ := cmd.Flags().GetString("agents")
	format, _ := cmd.Flags().GetString("format")

	ttl, err := time.ParseDuration(ttlStr)
	if err != nil {
		return fmt.Errorf("invalid --ttl %q: use Go duration format e.g. 30m, 1h, 90m", ttlStr)
	}
	if ttl < time.Minute || ttl > 4*time.Hour {
		return fmt.Errorf("--ttl must be between 1m and 4h (got %s)", ttl)
	}

	// Resolve agent list.
	var agents []string
	if agentList != "" {
		agents = strings.Split(agentList, ",")
		for i, a := range agents {
			agents[i] = strings.TrimSpace(a)
		}
	} else {
		if size < 1 || size > len(defaultCouncilAgents) {
			return fmt.Errorf("--size must be 1–%d (got %d)", len(defaultCouncilAgents), size)
		}
		agents = defaultCouncilAgents[:size]
	}

	markerDir := councilMarkerDir()
	if err := os.MkdirAll(markerDir, 0755); err != nil {
		return fmt.Errorf("create council marker dir: %w", err)
	}

	now := time.Now().UTC()
	expiresAt := now.Add(ttl)
	nodeID := hostShortname()
	ttlMin := int(ttl.Minutes())

	var created []councilMarker
	for _, agent := range agents {
		m := councilMarker{
			Agent:     agent,
			Purpose:   "council",
			TTLMin:    ttlMin,
			StartedAt: now.Format(time.RFC3339),
			ExpiresAt: expiresAt.Format(time.RFC3339),
			NodeID:    nodeID,
		}
		path := filepath.Join(markerDir, agent+".json")
		data, err := json.MarshalIndent(m, "", "  ")
		if err != nil {
			return fmt.Errorf("marshal marker for %s: %w", agent, err)
		}
		if err := os.WriteFile(path, data, 0644); err != nil {
			return fmt.Errorf("write marker for %s: %w", agent, err)
		}
		created = append(created, m)
	}

	if format == "json" {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(map[string]interface{}{
			"started_at": now.Format(time.RFC3339),
			"expires_at": expiresAt.Format(time.RFC3339),
			"ttl":        ttlStr,
			"agents":     agents,
			"node_id":    nodeID,
		})
	}

	fmt.Printf("Council session started\n\n")
	fmt.Printf("  Agents : %s\n", strings.Join(agents, ", "))
	fmt.Printf("  TTL    : %s (expires %s)\n", ttl, expiresAt.Format("15:04:05"))
	fmt.Printf("  Node   : %s\n", nodeID)
	fmt.Printf("  Markers: %s\n\n", markerDir)
	fmt.Printf("Agents are protected from auto-deflation until TTL expires.\n")
	fmt.Printf("To spawn agents: forge dispatch send forge:<agent> \"<task>\"\n")
	fmt.Printf("To end early  : forge council stop --all\n")
	_ = created
	return nil
}

func runCouncilStatus(cmd *cobra.Command, args []string) error {
	format, _ := cmd.Flags().GetString("format")

	markerDir := councilMarkerDir()
	entries, err := os.ReadDir(markerDir)
	if err != nil {
		if os.IsNotExist(err) {
			fmt.Println("No active council sessions.")
			return nil
		}
		return fmt.Errorf("read council markers: %w", err)
	}

	var markers []councilMarker
	now := time.Now().UTC()
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		data, err := os.ReadFile(filepath.Join(markerDir, entry.Name()))
		if err != nil {
			continue
		}
		var m councilMarker
		if err := json.Unmarshal(data, &m); err != nil {
			continue
		}
		// Skip expired markers (cleanup patrol removes them, but may lag by 2min).
		if m.ExpiresAt != "" {
			if t, err := time.Parse(time.RFC3339, m.ExpiresAt); err == nil {
				if now.After(t) {
					continue
				}
			}
		}
		markers = append(markers, m)
	}

	if len(markers) == 0 {
		fmt.Println("No active council sessions.")
		return nil
	}

	if format == "json" {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(markers)
	}

	fmt.Printf("Active council agents: %d\n\n", len(markers))
	fmt.Printf("%-12s %-20s %-20s %s\n", "AGENT", "STARTED", "EXPIRES", "REMAINING")
	fmt.Println(strings.Repeat("─", 70))
	for _, m := range markers {
		remaining := "—"
		if m.ExpiresAt != "" {
			if t, err := time.Parse(time.RFC3339, m.ExpiresAt); err == nil {
				remaining = t.Sub(now).Round(time.Second).String()
			}
		}
		started := m.StartedAt
		if t, err := time.Parse(time.RFC3339, m.StartedAt); err == nil {
			started = t.Format("15:04:05")
		}
		expires := m.ExpiresAt
		if t, err := time.Parse(time.RFC3339, m.ExpiresAt); err == nil {
			expires = t.Format("15:04:05")
		}
		fmt.Printf("%-12s %-20s %-20s %s\n", m.Agent, started, expires, remaining)
	}
	return nil
}

func runCouncilStop(cmd *cobra.Command, args []string) error {
	all, _ := cmd.Flags().GetBool("all")

	markerDir := councilMarkerDir()

	if all {
		entries, err := os.ReadDir(markerDir)
		if err != nil {
			if os.IsNotExist(err) {
				fmt.Println("No active council sessions.")
				return nil
			}
			return fmt.Errorf("read council markers: %w", err)
		}
		stopped := 0
		for _, entry := range entries {
			if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
				continue
			}
			if err := os.Remove(filepath.Join(markerDir, entry.Name())); err != nil {
				fmt.Fprintf(os.Stderr, "remove %s: %v\n", entry.Name(), err)
				continue
			}
			stopped++
		}
		fmt.Printf("Stopped %d council agent(s). Run `forge approval list` to see deflation recommendations.\n", stopped)
		return nil
	}

	if len(args) == 0 {
		return fmt.Errorf("specify an agent name or use --all\nExample: forge council stop kimi")
	}

	agent := args[0]
	path := filepath.Join(markerDir, agent+".json")
	if err := os.Remove(path); err != nil {
		if os.IsNotExist(err) {
			return fmt.Errorf("no active council session for agent %q", agent)
		}
		return fmt.Errorf("stop council agent %s: %w", agent, err)
	}
	fmt.Printf("Council session for %s ended. Deflation recommendation will appear in `forge approval list`.\n", agent)
	return nil
}

func councilMarkerDir() string {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		dir, _ := os.Getwd()
		for dir != "/" && dir != "." {
			if _, err := os.Stat(filepath.Join(dir, ".forge")); err == nil {
				forgeRoot = dir
				break
			}
			parent := filepath.Dir(dir)
			if parent == dir {
				break
			}
			dir = parent
		}
	}
	if forgeRoot == "" {
		forgeRoot, _ = os.Getwd()
	}
	return filepath.Join(forgeRoot, ".forge", "autoscale", "council")
}

func init() {
	councilCmd.AddCommand(councilStartCmd)
	councilCmd.AddCommand(councilStatusCmd)
	councilCmd.AddCommand(councilStopCmd)

	councilStartCmd.Flags().Int("size", 3, "Number of council agents (uses default priority list)")
	councilStartCmd.Flags().String("ttl", "30m", "Session TTL (e.g. 30m, 1h, 90m)")
	councilStartCmd.Flags().String("agents", "", "Explicit comma-separated agent list (overrides --size)")

	councilStopCmd.Flags().Bool("all", false, "Stop all active council sessions")
}
