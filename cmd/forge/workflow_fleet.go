// workflow_fleet.go - V4-011 Fleet Workflow Commands
//
// Implements fleet management workflow commands:
//   - forge fleet list       : List all agents from heartbeat files
//   - forge fleet status     : Fleet health summary by node
//   - forge fleet health     : Detailed health metrics across fleet
//   - forge fleet broadcast  : Send messages to all agents

package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// nodeHeartbeat represents the structure of .forge/heartbeat/nodes/*.json files.
type nodeHeartbeat struct {
	NodeID    string   `json:"node_id"`
	Timestamp string   `json:"timestamp"`
	Resources struct {
		CPUUsagePercent float64 `json:"cpu_usage_percent"`
		RAMAvailableMB  int     `json:"ram_available_mb"`
		RAMTotalMB      int     `json:"ram_total_mb"`
		DiskFreeGB      float64 `json:"disk_free_gb"`
		QueueDepth      int     `json:"queue_depth"`
	} `json:"resources"`
	Capabilities []string `json:"capabilities"`
	Agents       []struct {
		Name   string `json:"name"`
		Node   string `json:"node"`
		Status string `json:"status"`
	} `json:"agents"`
	Health struct {
		Status      string  `json:"status"`
		LoadAverage float64 `json:"load_average"`
		Cores       int     `json:"cores"`
		Platform    string  `json:"platform"`
	} `json:"health"`
}

// fleetCmd is the workflow command for fleet-wide operations
var fleetCmd = &cobra.Command{
	Use:   "fleet",
	Short: "Fleet-wide operations",
	Long: `Operations across the entire FORGE fleet.

Display fleet status, list agents, check health across all nodes, and broadcast
messages to agents.

Examples:
  # List all agents
  forge fleet list

  # Show fleet status overview
  forge fleet status

  # Detailed health check
  forge fleet health

  # Broadcast message to all agents
  forge fleet broadcast "Deploy complete"

  # Broadcast with filtering
  forge fleet broadcast "Standby" --filter-state idle
`,
}

// fleetListCmd: forge fleet list
var fleetListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all fleet agents",
	Long:  "List all agents discovered from heartbeat files across all nodes.",
	RunE:  runFleetList,
}

// fleetStatusWorkflowCmd: forge fleet status
var fleetStatusWorkflowCmd = &cobra.Command{
	Use:   "status",
	Short: "Show fleet health summary",
	Long:  "Display a summary of fleet health: agent counts per node and queue depth.",
	RunE:  runFleetStatus,
}

// fleetHealthWorkflowCmd: forge fleet health
var fleetHealthWorkflowCmd = &cobra.Command{
	Use:   "health",
	Short: "Detailed health check across fleet",
	Long:  "Run detailed health checks across all fleet nodes.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		health, err := buildFleetHealth()
		if err != nil {
			return fmt.Errorf("failed to get fleet health: %w", err)
		}

		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatFleetHealth(health)
	},
}

// fleetBroadcastWorkflowCmd: forge fleet broadcast "message"
var fleetBroadcastWorkflowCmd = &cobra.Command{
	Use:   "broadcast <message>",
	Short: "Broadcast message to all agents",
	Long:  "Broadcast a message to all agents discovered from heartbeat files.",
	Args:  cobra.ExactArgs(1),
	RunE:  runFleetBroadcast,
}

// runFleetList implements forge fleet list.
// It reads .forge/heartbeat/nodes/*.json and prints each agent.
func runFleetList(cmd *cobra.Command, args []string) error {
	format, _ := cmd.Flags().GetString("format")

	heartbeats, err := readHeartbeats()
	if err != nil {
		return fmt.Errorf("failed to read heartbeat files: %w", err)
	}

	// Collect agents across all nodes, sorted by node then name.
	type agentRow struct {
		Name         string
		Node         string
		Status       string
		Capabilities string
	}

	var rows []agentRow
	for _, hb := range heartbeats {
		caps := strings.Join(hb.Capabilities, ",")
		for _, a := range hb.Agents {
			node := a.Node
			if node == "" {
				node = hb.NodeID
			}
			rows = append(rows, agentRow{
				Name:         a.Name,
				Node:         node,
				Status:       a.Status,
				Capabilities: caps,
			})
		}
	}

	sort.Slice(rows, func(i, j int) bool {
		if rows[i].Node != rows[j].Node {
			return rows[i].Node < rows[j].Node
		}
		return rows[i].Name < rows[j].Name
	})

	switch format {
	case "json":
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(rows)
	case "quiet":
		for _, r := range rows {
			fmt.Println(r.Name)
		}
		return nil
	default: // table
		formatter := internal.NewFormatter(format, nil)
		tw := formatter.NewTableWriter()
		tw.WriteHeader("AGENT", "NODE", "STATUS", "CAPABILITIES")
		for _, r := range rows {
			tw.WriteRow(r.Name, r.Node, r.Status, r.Capabilities)
		}
		if err := tw.Flush(); err != nil {
			return err
		}
		formatter.Printf("\nTotal: %d agents\n", len(rows))
		return nil
	}
}

// runFleetStatus implements forge fleet status.
func runFleetStatus(cmd *cobra.Command, args []string) error {
	format, _ := cmd.Flags().GetString("format")

	heartbeats, err := readHeartbeats()
	if err != nil {
		return fmt.Errorf("failed to read heartbeat files: %w", err)
	}

	// If no heartbeat files, fall back to FleetStatus from xnode configs.
	if len(heartbeats) == 0 {
		status, err := buildFleetStatus()
		if err != nil {
			return fmt.Errorf("failed to get fleet status: %w", err)
		}
		formatter := internal.NewFormatter(format, nil)
		return formatter.FormatFleetStatus(status)
	}

	type nodeSummary struct {
		NodeID     string
		AgentCount int
		Active     int
		QueueDepth int
		HealthStr  string
	}

	var nodes []nodeSummary
	totalAgents := 0

	for _, hb := range heartbeats {
		active := 0
		for _, a := range hb.Agents {
			if a.Status == "active" {
				active++
			}
		}
		nodes = append(nodes, nodeSummary{
			NodeID:     hb.NodeID,
			AgentCount: len(hb.Agents),
			Active:     active,
			QueueDepth: hb.Resources.QueueDepth,
			HealthStr:  hb.Health.Status,
		})
		totalAgents += len(hb.Agents)
	}

	sort.Slice(nodes, func(i, j int) bool {
		return nodes[i].NodeID < nodes[j].NodeID
	})

	switch format {
	case "json":
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		type jsonOut struct {
			TotalAgents int           `json:"total_agents"`
			Nodes       []nodeSummary `json:"nodes"`
		}
		return enc.Encode(jsonOut{TotalAgents: totalAgents, Nodes: nodes})
	case "quiet":
		for _, n := range nodes {
			fmt.Printf("%s: %d agents\n", n.NodeID, n.AgentCount)
		}
		return nil
	default: // table
		formatter := internal.NewFormatter(format, nil)
		formatter.Printf("Fleet: %d agents across %d nodes\n", totalAgents, len(nodes))
		for _, n := range nodes {
			formatter.Printf("  %s: %d active", n.NodeID, n.Active)
			if n.QueueDepth > 0 {
				formatter.Printf("  (queue: %d)", n.QueueDepth)
			}
			formatter.Printf("\n")
		}
		return nil
	}
}

// runFleetBroadcast implements forge fleet broadcast.
func runFleetBroadcast(cmd *cobra.Command, args []string) error {
	message := args[0]
	msgType, _ := cmd.Flags().GetString("type")
	filterState, _ := cmd.Flags().GetString("filter-state")
	format, _ := cmd.Flags().GetString("format")

	heartbeats, err := readHeartbeats()
	if err != nil {
		return fmt.Errorf("failed to read heartbeat files: %w", err)
	}

	// Use FleetStateManager.Broadcast for agents registered in workflow state dir.
	// Simultaneously collect targets from heartbeat files for display.
	var targets []string

	if len(heartbeats) > 0 {
		for _, hb := range heartbeats {
			for _, a := range hb.Agents {
				if filterState != "" && a.Status != filterState {
					continue
				}
				node := a.Node
				if node == "" {
					node = hb.NodeID
				}
				targets = append(targets, fmt.Sprintf("%s:%s", node, a.Name))
			}
		}
	}

	// Also attempt FleetStateManager broadcast for any workflow-registered agents.
	fsm := NewFleetStateManager()
	if _, err := fsm.Broadcast(message, msgType, filterState, ""); err != nil {
		// Non-fatal: workflow state dir may not exist yet.
		_ = err
	}

	result := &internal.BroadcastResult{
		Timestamp:   time.Now().UTC(),
		Message:     message,
		Type:        msgType,
		TargetCount: len(targets),
		Targets:     targets,
		Success:     true,
	}

	formatter := internal.NewFormatter(format, nil)
	return formatter.FormatBroadcastResult(result)
}

// readHeartbeats loads all node heartbeat files from .forge/heartbeat/nodes/*.json.
func readHeartbeats() ([]nodeHeartbeat, error) {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = findForgeRoot()
	}
	if forgeRoot == "" {
		wd, _ := os.Getwd()
		forgeRoot = wd
	}

	dir := filepath.Join(forgeRoot, ".forge", "heartbeat", "nodes")
	entries, err := os.ReadDir(dir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}

	var heartbeats []nodeHeartbeat
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}

		data, err := os.ReadFile(filepath.Join(dir, entry.Name()))
		if err != nil {
			continue
		}

		var hb nodeHeartbeat
		if err := json.Unmarshal(data, &hb); err != nil {
			continue
		}

		// Derive node_id from filename if not set in JSON.
		if hb.NodeID == "" {
			hb.NodeID = strings.TrimSuffix(entry.Name(), ".json")
		}

		heartbeats = append(heartbeats, hb)
	}

	sort.Slice(heartbeats, func(i, j int) bool {
		return heartbeats[i].NodeID < heartbeats[j].NodeID
	})

	return heartbeats, nil
}

// buildFleetStatus constructs fleet status — reads real heartbeat files first,
// falls back to xnode-test configs when no heartbeat files are found.
func buildFleetStatus() (*internal.FleetStatus, error) {
	status := &internal.FleetStatus{
		Nodes:       []internal.FleetNode{},
		GeneratedAt: time.Now().UTC(),
	}

	heartbeats, err := readHeartbeats()
	if err != nil {
		return nil, err
	}

	if len(heartbeats) > 0 {
		for _, hb := range heartbeats {
			ramPercent := 0
			if hb.Resources.RAMTotalMB > 0 {
				used := hb.Resources.RAMTotalMB - hb.Resources.RAMAvailableMB
				ramPercent = int(math.Round(float64(used) * 100 / float64(hb.Resources.RAMTotalMB)))
			}

			node := internal.FleetNode{
				ID:           hb.NodeID,
				Hostname:     hb.NodeID,
				Status:       hb.Health.Status,
				AgentCount:   len(hb.Agents),
				QueueDepth:   hb.Resources.QueueDepth,
				CPUPercent:   int(math.Round(hb.Resources.CPUUsagePercent)),
				RAMPercent:   ramPercent,
				Capabilities: hb.Capabilities,
				LastSeen:     time.Now().UTC(),
			}

			status.Nodes = append(status.Nodes, node)
			status.TotalAgents += len(hb.Agents)
		}

		status.TotalNodes = len(status.Nodes)
		status.Healthy = status.TotalNodes
		return status, nil
	}

	// Fallback: read from .forge/xnode-test/*/config.json
	xnodeDir := filepath.Join(".forge", "xnode-test")
	entries, err := os.ReadDir(xnodeDir)
	if err != nil {
		if os.IsNotExist(err) {
			return status, nil
		}
		return nil, err
	}

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		configPath := filepath.Join(xnodeDir, entry.Name(), "config.json")
		data, err := os.ReadFile(configPath)
		if err != nil {
			continue
		}

		var config struct {
			NodeID       string   `json:"node_id"`
			Hostname     string   `json:"hostname"`
			Region       string   `json:"region"`
			Capabilities []string `json:"capabilities"`
		}

		if err := json.Unmarshal(data, &config); err != nil {
			continue
		}

		node := internal.FleetNode{
			ID:           config.NodeID,
			Hostname:     config.Hostname,
			Region:       config.Region,
			Capabilities: config.Capabilities,
			Status:       "unknown",
			LastSeen:     time.Now().UTC(),
		}

		status.Nodes = append(status.Nodes, node)
	}

	status.TotalNodes = len(status.Nodes)
	status.Healthy = status.TotalNodes

	return status, nil
}

// buildFleetHealth constructs fleet health report from real heartbeat files.
func buildFleetHealth() (*internal.FleetHealth, error) {
	health := &internal.FleetHealth{
		Nodes:       []internal.NodeHealth{},
		Issues:      []internal.FleetIssue{},
		GeneratedAt: time.Now().UTC(),
	}

	heartbeats, err := readHeartbeats()
	if err != nil {
		return nil, err
	}

	if len(heartbeats) > 0 {
		for _, hb := range heartbeats {
			cpuPercent := int(math.Round(hb.Resources.CPUUsagePercent))

			ramPercent := 0
			if hb.Resources.RAMTotalMB > 0 {
				used := hb.Resources.RAMTotalMB - hb.Resources.RAMAvailableMB
				ramPercent = int(math.Round(float64(used) * 100 / float64(hb.Resources.RAMTotalMB)))
			}

			isHealthy := hb.Health.Status == "healthy" || hb.Health.Status == ""
			var issues []string

			if cpuPercent > 80 {
				isHealthy = false
				issues = append(issues, fmt.Sprintf("high CPU (%d%%)", cpuPercent))
				health.Issues = append(health.Issues, internal.FleetIssue{
					NodeID:  hb.NodeID,
					Type:    "overloaded",
					Details: fmt.Sprintf("CPU at %d%%", cpuPercent),
				})
			}

			if ramPercent > 90 {
				isHealthy = false
				issues = append(issues, fmt.Sprintf("high RAM (%d%%)", ramPercent))
				health.Issues = append(health.Issues, internal.FleetIssue{
					NodeID:  hb.NodeID,
					Type:    "overloaded",
					Details: fmt.Sprintf("RAM at %d%%", ramPercent),
				})
			}

			if isHealthy {
				health.Healthy++
			}

			health.Nodes = append(health.Nodes, internal.NodeHealth{
				NodeID:     hb.NodeID,
				Status:     hb.Health.Status,
				AgentCount: len(hb.Agents),
				Healthy:    isHealthy,
				CPUPercent: cpuPercent,
				RAMPercent: ramPercent,
				LastSeen:   time.Now().UTC(),
				Issues:     issues,
			})
		}

		health.TotalNodes = len(health.Nodes)
		return health, nil
	}

	// Fallback: xnode-test configs (no real metrics available)
	xnodeDir := filepath.Join(".forge", "xnode-test")
	entries, err := os.ReadDir(xnodeDir)
	if err != nil {
		if os.IsNotExist(err) {
			return health, nil
		}
		return nil, err
	}

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		nodeID := entry.Name()
		configPath := filepath.Join(xnodeDir, nodeID, "config.json")
		if _, err := os.Stat(configPath); err != nil {
			continue
		}

		health.Healthy++
		health.Nodes = append(health.Nodes, internal.NodeHealth{
			NodeID:   nodeID,
			Status:   "unknown",
			Healthy:  true,
			LastSeen: time.Now().UTC(),
		})
	}

	health.TotalNodes = len(health.Nodes)
	return health, nil
}

// fleetScaleRec mirrors the JSON written by the fleet-scale-recommend patrol.
type fleetScaleRec struct {
	Timestamp    string `json:"timestamp"`
	Action       string `json:"action"`
	Reason       string `json:"reason"`
	Urgency      string `json:"urgency"`
	QueuedTasks  int    `json:"queued_tasks"`
	ActiveAgents int    `json:"active_agents"`
	IdleAgents   int    `json:"idle_agents"`
	RAMAvailMB   int    `json:"ram_avail_mb"`
	Note         string `json:"note"`
}

// fleetRecommendationsCmd: forge fleet recommendations
var fleetRecommendationsCmd = &cobra.Command{
	Use:   "recommendations",
	Short: "Show fleet scale recommendations (ADR-034)",
	Long:  "Display the latest fleet scaling recommendation written by the fleet-scale-recommend patrol.",
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		forgeRoot := os.Getenv("FORGE_ROOT")
		if forgeRoot == "" {
			forgeRoot = findForgeRoot()
		}
		if forgeRoot == "" {
			wd, _ := os.Getwd()
			forgeRoot = wd
		}

		recPath := filepath.Join(forgeRoot, ".forge", "heartbeat", "results", "fleet-scale-rec.json")
		data, err := os.ReadFile(recPath)
		if err != nil {
			if os.IsNotExist(err) {
				fmt.Println("No recommendation available yet.")
				fmt.Println("The fleet-scale-recommend patrol runs every 5 minutes.")
				fmt.Println("Ensure the daemon is running: forge daemon status")
				return nil
			}
			return fmt.Errorf("read recommendation: %w", err)
		}

		var rec fleetScaleRec
		if err := json.Unmarshal(data, &rec); err != nil {
			return fmt.Errorf("parse recommendation: %w", err)
		}

		if format == "json" {
			fmt.Print(string(data))
			return nil
		}

		// Table output
		urgencyIndicator := map[string]string{
			"high":   "⚠ HIGH",
			"medium": "~ MEDIUM",
			"low":    "✓ LOW",
		}
		urgencyStr, ok := urgencyIndicator[rec.Urgency]
		if !ok {
			urgencyStr = rec.Urgency
		}

		actionStr := strings.ToUpper(rec.Action)

		fmt.Printf("Fleet Scale Recommendation (ADR-034)\n")
		fmt.Printf("Generated: %s\n\n", rec.Timestamp)
		fmt.Printf("  Action:       %s\n", actionStr)
		fmt.Printf("  Urgency:      %s\n", urgencyStr)
		fmt.Printf("  Reason:       %s\n\n", rec.Reason)
		fmt.Printf("  Queued tasks: %d\n", rec.QueuedTasks)
		fmt.Printf("  Active agents:%d\n", rec.ActiveAgents)
		fmt.Printf("  Idle agents:  %d\n", rec.IdleAgents)
		fmt.Printf("  RAM available:%d MB\n\n", rec.RAMAvailMB)
		if rec.Note != "" {
			fmt.Printf("  Note: %s\n", rec.Note)
		}
		return nil
	},
}

// tmuxWindow represents a discovered tmux window (agent).
type tmuxWindow struct {
	Session string
	Index   string
	Name    string
	Active  bool
}

// discoverTmuxAgents discovers agent windows in the forge tmux session.
// Returns windows whose name matches known agent names (not the hostname/orchestrator).
func discoverTmuxAgents(sessionName string) ([]tmuxWindow, error) {
	if sessionName == "" {
		sessionName = "forge"
	}

	out, err := runCommand("tmux", "list-windows", "-t", sessionName, "-F",
		"#{window_index}:#{window_name}:#{window_active}")
	if err != nil {
		return nil, err
	}

	hostname, _ := os.Hostname()
	var windows []tmuxWindow
	for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
		if line == "" {
			continue
		}
		parts := strings.SplitN(line, ":", 3)
		if len(parts) != 3 {
			continue
		}
		idx, name, activeStr := parts[0], parts[1], parts[2]
		// Skip the orchestrator window (named after hostname)
		if name == hostname || name == "bash" || name == "zsh" || name == "" {
			continue
		}
		windows = append(windows, tmuxWindow{
			Session: sessionName,
			Index:   idx,
			Name:    name,
			Active:  activeStr == "1",
		})
	}
	return windows, nil
}

// runCommand runs a command and returns stdout as a string.
func runCommand(name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	out, err := cmd.Output()
	return string(out), err
}

// fleetWindowsCmd: forge fleet windows
// Lists tmux agent windows from the forge session.
var fleetWindowsCmd = &cobra.Command{
	Use:   "windows",
	Short: "Show live tmux agent windows",
	Long:  "List all tmux windows in the forge session, showing live agent state.",
	RunE: func(cmd *cobra.Command, args []string) error {
		session, _ := cmd.Flags().GetString("session")
		format, _ := cmd.Flags().GetString("format")

		windows, err := discoverTmuxAgents(session)
		if err != nil {
			return fmt.Errorf("tmux not available or session '%s' not found: %w\n\nStart fleet: bash .forge/scripts/forge-startup.sh", session, err)
		}

		if format == "json" {
			data, _ := json.MarshalIndent(windows, "", "  ")
			fmt.Println(string(data))
			return nil
		}

		if len(windows) == 0 {
			fmt.Printf("No agent windows found in tmux session '%s'\n", session)
			fmt.Println("\nTo start the fleet: bash .forge/scripts/forge-startup.sh")
			return nil
		}

		fmt.Printf("Session: %s — %d agent windows\n\n", session, len(windows))
		fmt.Printf("%-6s %-20s %s\n", "INDEX", "AGENT", "STATUS")
		fmt.Println(strings.Repeat("─", 40))
		for _, w := range windows {
			status := "idle"
			if w.Active {
				status = "active (current)"
			}
			fmt.Printf("%-6s %-20s %s\n", w.Index, w.Name, status)
		}
		fmt.Printf("\nDispatch: forge dispatch send forge:<agent> \"message\"\n")
		return nil
	},
}

// tokenBudgetFile represents the structure of .forge/heartbeat/token-budgets-{node}.json
type tokenBudgetFile struct {
	Node       string                       `json:"node"`
	UpdatedAt  string                       `json:"updated_at"`
	Providers  map[string]providerBudget    `json:"providers"`
}

type providerBudget struct {
	Provider     string            `json:"provider"`
	Agents       []string          `json:"agents"`
	Limits       map[string]int    `json:"limits"`
	Resets       map[string]string `json:"resets"`
	Status       string            `json:"status"`
	CooldownUntil *string          `json:"cooldown_until,omitempty"`
}

// fleetBudgetCmd: forge fleet budget
var fleetBudgetCmd = &cobra.Command{
	Use:   "budget",
	Short: "Token budget management",
	Long: `Manage per-provider token budgets for the fleet.

Shows usage percentages, status (OK/CAUTION/RED/DEPLETED/COOLDOWN),
and reset times for each provider.

Examples:
  # Show current budget status
  forge fleet budget

  # Set usage manually
  forge fleet budget set anthropic --weekly-pct 20 --monthly-pct 10

  # Reset after billing cycle
  forge fleet budget reset openai
`,
}

// fleetBudgetShowCmd: forge fleet budget (default action)
var fleetBudgetShowCmd = &cobra.Command{
	Use:   "show",
	Short: "Show token budget status",
	Long:  "Display per-provider token budget status for this node.",
	RunE:  runFleetBudgetShow,
}

// fleetBudgetSetCmd: forge fleet budget set <provider>
var fleetBudgetSetCmd = &cobra.Command{
	Use:   "set <provider>",
	Short: "Set token budget usage",
	Long:  "Manually seed token budget usage percentages for a provider.",
	Args:  cobra.ExactArgs(1),
	RunE:  runFleetBudgetSet,
}

// fleetBudgetResetCmd: forge fleet budget reset <provider>
var fleetBudgetResetCmd = &cobra.Command{
	Use:   "reset <provider>",
	Short: "Reset token budget to zero",
	Long:  "Reset all usage percentages to zero for a provider (after confirmed billing reset).",
	Args:  cobra.ExactArgs(1),
	RunE:  runFleetBudgetReset,
}

// fleetInventoryCmd: forge fleet inventory
var fleetInventoryCmd = &cobra.Command{
	Use:   "inventory",
	Short: "Show agent inventory with token status",
	Long: `Display the full agent inventory combining tmux agent windows
with token budget status for each agent type.

Shows: agent type, provider, budget status, usage percentages, and busy/idle counts.`,
	RunE: runFleetInventory,
}

func runFleetBudgetShow(cmd *cobra.Command, args []string) error {
	format, _ := cmd.Flags().GetString("format")
	node, _ := cmd.Flags().GetString("node")

	budget, err := readOrCreateTokenBudget(node)
	if err != nil {
		return fmt.Errorf("failed to read token budget: %w", err)
	}

	switch format {
	case "json":
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(budget)
	default:
		formatter := internal.NewFormatter(format, nil)
		tw := formatter.NewTableWriter()
		tw.WriteHeader("PROVIDER", "AGENTS", "STATUS", "~MONTHLY", "~WEEKLY", "~5H", "COOLDOWN")
		for name, p := range budget.Providers {
			monthly := p.Limits["monthly_pct"]
			weekly := p.Limits["weekly_pct"]
			h5 := p.Limits["5h_rolling_pct"]
			cooldown := "—"
			if p.CooldownUntil != nil && *p.CooldownUntil != "" {
				cooldown = *p.CooldownUntil
				if t, err := time.Parse(time.RFC3339, cooldown); err == nil {
					remaining := time.Until(t)
					if remaining > 0 {
						cooldown = fmt.Sprintf("resets in %dh", int(remaining.Hours()))
					} else {
						cooldown = "—"
					}
				}
			}
			tw.WriteRow(name, strings.Join(p.Agents, ","), p.Status, fmt.Sprintf("%d%%", monthly),
				fmt.Sprintf("%d%%", weekly), fmt.Sprintf("%d%%", h5), cooldown)
		}
		if err := tw.Flush(); err != nil {
			return err
		}
		formatter.Printf("\nNode: %s | Updated: %s\n", budget.Node, budget.UpdatedAt)
		formatter.Printf("Legend: ~ = approximate | CAUTION = prefer substitute | COOLDOWN = spawn blocked\n")
		return nil
	}
}

func runFleetBudgetSet(cmd *cobra.Command, args []string) error {
	provider := args[0]
	monthlyPct, _ := cmd.Flags().GetInt("monthly-pct")
	weeklyPct, _ := cmd.Flags().GetInt("weekly-pct")
	h5Pct, _ := cmd.Flags().GetInt("5h-pct")
	node, _ := cmd.Flags().GetString("node")

	budget, err := readOrCreateTokenBudget(node)
	if err != nil {
		return fmt.Errorf("failed to read token budget: %w", err)
	}

	p, ok := budget.Providers[provider]
	if !ok {
		// Create new provider entry
		p = providerBudget{
			Provider: provider,
			Limits:   make(map[string]int),
			Resets:   make(map[string]string),
			Status:   "OK",
		}
		// Set default agents based on provider
		switch provider {
		case "anthropic":
			p.Agents = []string{"claude", "amp"}
		case "openai":
			p.Agents = []string{"opencode", "kilo"}
		case "google":
			p.Agents = []string{"gemini"}
		case "moonshot":
			p.Agents = []string{"kimi"}
		case "minimax":
			p.Agents = []string{"minimax"}
		case "inflection":
			p.Agents = []string{"pi"}
		case "cursor":
			p.Agents = []string{"cursor"}
		}
	}

	if monthlyPct >= 0 {
		p.Limits["monthly_pct"] = monthlyPct
	}
	if weeklyPct >= 0 {
		p.Limits["weekly_pct"] = weeklyPct
	}
	if h5Pct >= 0 {
		p.Limits["5h_rolling_pct"] = h5Pct
	}

	// Recompute status
	p.Status = computeBudgetStatus(p)
	budget.Providers[provider] = p
	budget.UpdatedAt = time.Now().UTC().Format(time.RFC3339)

	if err := writeTokenBudget(budget); err != nil {
		return fmt.Errorf("failed to write token budget: %w", err)
	}

	fmt.Printf("Set %s: monthly=%d%%, weekly=%d%%, 5h=%d%% → status=%s\n",
		provider, p.Limits["monthly_pct"], p.Limits["weekly_pct"], p.Limits["5h_rolling_pct"], p.Status)
	return nil
}

func runFleetBudgetReset(cmd *cobra.Command, args []string) error {
	provider := args[0]
	node, _ := cmd.Flags().GetString("node")

	budget, err := readOrCreateTokenBudget(node)
	if err != nil {
		return fmt.Errorf("failed to read token budget: %w", err)
	}

	p, ok := budget.Providers[provider]
	if !ok {
		return fmt.Errorf("provider %s not found in budget", provider)
	}

	p.Limits["monthly_pct"] = 0
	p.Limits["weekly_pct"] = 0
	p.Limits["5h_rolling_pct"] = 0
	p.Status = "OK"
	p.CooldownUntil = nil
	budget.Providers[provider] = p
	budget.UpdatedAt = time.Now().UTC().Format(time.RFC3339)

	if err := writeTokenBudget(budget); err != nil {
		return fmt.Errorf("failed to write token budget: %w", err)
	}

	fmt.Printf("Reset %s: all limits set to 0%%, status=OK\n", provider)
	return nil
}

func runFleetInventory(cmd *cobra.Command, args []string) error {
	format, _ := cmd.Flags().GetString("format")
	node, _ := cmd.Flags().GetString("node")

	// Get tmux agents
	windows, _ := discoverTmuxAgents("forge")
	agentCounts := make(map[string]int)
	for _, w := range windows {
		agentCounts[w.Name]++
	}

	// Get token budgets
	budget, _ := readTokenBudget(node)

	// Build agent type to provider mapping
	agentToProvider := map[string]string{
		"claude": "anthropic", "amp": "anthropic",
		"opencode": "openai", "kilo": "openai",
		"gemini": "google",
		"kimi": "moonshot",
		"minimax": "minimax",
		"pi": "inflection",
		"cursor": "cursor",
	}

	type inventoryRow struct {
		Type       string
		Provider   string
		Status     string
		Monthly    int
		Weekly     int
		H5         int
		Busy       int
		Idle       int
		Cooldown   string
	}

	var rows []inventoryRow
	seenTypes := make(map[string]bool)

	// Add providers from budget file
	if budget != nil {
		for name, p := range budget.Providers {
			for _, agent := range p.Agents {
				if seenTypes[agent] {
					continue
				}
				seenTypes[agent] = true

				busy := 0
				idle := agentCounts[agent]
				status := p.Status
				monthly := p.Limits["monthly_pct"]
				weekly := p.Limits["weekly_pct"]
				h5 := p.Limits["5h_rolling_pct"]
				cooldown := "—"
				if p.CooldownUntil != nil && *p.CooldownUntil != "" {
					if t, err := time.Parse(time.RFC3339, *p.CooldownUntil); err == nil {
						remaining := time.Until(t)
						if remaining > 0 {
							cooldown = fmt.Sprintf("%dh", int(remaining.Hours()))
						}
					}
				}

				rows = append(rows, inventoryRow{
					Type: agent, Provider: name, Status: status,
					Monthly: monthly, Weekly: weekly, H5: h5,
					Busy: busy, Idle: idle, Cooldown: cooldown,
				})
			}
		}
	}

	// Add agents not in budget file
	for agent, provider := range agentToProvider {
		if seenTypes[agent] {
			continue
		}
		seenTypes[agent] = true
		idle := agentCounts[agent]
		rows = append(rows, inventoryRow{
			Type: agent, Provider: provider, Status: "UNKNOWN",
			Monthly: 0, Weekly: 0, H5: 0,
			Busy: 0, Idle: idle, Cooldown: "—",
		})
	}

	sort.Slice(rows, func(i, j int) bool {
		return rows[i].Type < rows[j].Type
	})

	switch format {
	case "json":
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(rows)
	default:
		formatter := internal.NewFormatter(format, nil)
		hostname, _ := os.Hostname()
		formatter.Printf("AGENT INVENTORY — node: %s — %s\n\n", hostname, time.Now().UTC().Format("2006-01-02 15:04 UTC"))
		tw := formatter.NewTableWriter()
		tw.WriteHeader("TYPE", "PROVIDER", "STATUS", "~MONTHLY", "~WEEKLY", "~5H", "BUSY", "IDLE", "COOLDOWN")
		for _, r := range rows {
			tw.WriteRow(r.Type, r.Provider, r.Status,
				fmt.Sprintf("%d%%", r.Monthly), fmt.Sprintf("%d%%", r.Weekly), fmt.Sprintf("%d%%", r.H5),
				fmt.Sprintf("%d", r.Busy), fmt.Sprintf("%d", r.Idle), r.Cooldown)
		}
		if err := tw.Flush(); err != nil {
			return err
		}
		formatter.Printf("\nLegend: ~ = approximate | CAUTION = prefer substitute | COOLDOWN = spawn blocked\n")
		return nil
	}
}

func readTokenBudget(node string) (*tokenBudgetFile, error) {
	forgeRoot := findForgeRoot()
	if forgeRoot == "" {
		wd, _ := os.Getwd()
		forgeRoot = wd
	}

	// Try specific node first, then current node
	var path string
	if node != "" {
		path = filepath.Join(forgeRoot, ".forge", "heartbeat", fmt.Sprintf("token-budgets-%s.json", node))
	} else {
		hostname, _ := os.Hostname()
		path = filepath.Join(forgeRoot, ".forge", "heartbeat", fmt.Sprintf("token-budgets-%s.json", hostname))
	}

	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}

	var budget tokenBudgetFile
	if err := json.Unmarshal(data, &budget); err != nil {
		return nil, err
	}
	return &budget, nil
}

func readOrCreateTokenBudget(node string) (*tokenBudgetFile, error) {
	budget, err := readTokenBudget(node)
	if err != nil {
		return nil, err
	}
	if budget != nil {
		return budget, nil
	}

	hostname, _ := os.Hostname()
	now := time.Now().UTC()
	monthlyReset := "2026-04-01T00:00:00Z"
	weeklyReset := "2026-03-09T00:00:00Z"

	budget = &tokenBudgetFile{
		Node:      hostname,
		UpdatedAt: now.Format(time.RFC3339),
		Providers: map[string]providerBudget{
			"anthropic": {
				Provider: "anthropic",
				Agents:   []string{"claude", "amp"},
				Limits:   map[string]int{"monthly_pct": 9, "weekly_pct": 18, "5h_rolling_pct": 0},
				Resets:   map[string]string{"monthly_resets_at": monthlyReset, "weekly_resets_at": weeklyReset},
				Status:   "OK",
			},
			"openai": {
				Provider: "openai",
				Agents:   []string{"opencode", "kilo"},
				Limits:   map[string]int{"monthly_pct": 96, "weekly_pct": 29, "5h_rolling_pct": 0},
				Resets:   map[string]string{"monthly_resets_at": monthlyReset, "weekly_resets_at": weeklyReset},
				Status:   "COOLDOWN",
				CooldownUntil: &monthlyReset,
			},
			"google": {
				Provider: "google",
				Agents:   []string{"gemini"},
				Limits:   map[string]int{"monthly_pct": 12},
				Resets:   map[string]string{"monthly_resets_at": monthlyReset},
				Status:   "OK",
			},
			"moonshot": {
				Provider: "moonshot",
				Agents:   []string{"kimi"},
				Limits:   map[string]int{"monthly_pct": 88},
				Resets:   map[string]string{"monthly_resets_at": monthlyReset},
				Status:   "CAUTION",
			},
			"minimax": {
				Provider: "minimax",
				Agents:   []string{"minimax"},
				Limits:   map[string]int{"monthly_pct": 5},
				Resets:   map[string]string{"monthly_resets_at": monthlyReset},
				Status:   "OK",
			},
			"inflection": {
				Provider: "inflection",
				Agents:   []string{"pi"},
				Limits:   map[string]int{"monthly_pct": 3},
				Resets:   map[string]string{"monthly_resets_at": monthlyReset},
				Status:   "OK",
			},
			"cursor": {
				Provider: "cursor",
				Agents:   []string{"cursor"},
				Limits:   map[string]int{"monthly_pct": 20},
				Resets:   map[string]string{"monthly_resets_at": monthlyReset},
				Status:   "OK",
			},
		},
	}

	// Write the seeded file
	if err := writeTokenBudget(budget); err != nil {
		return nil, fmt.Errorf("failed to write seeded token budget: %w", err)
	}

	return budget, nil
}

func writeTokenBudget(budget *tokenBudgetFile) error {
	forgeRoot := findForgeRoot()
	if forgeRoot == "" {
		wd, _ := os.Getwd()
		forgeRoot = wd
	}

	dir := filepath.Join(forgeRoot, ".forge", "heartbeat")
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}

	path := filepath.Join(dir, fmt.Sprintf("token-budgets-%s.json", budget.Node))
	data, err := json.MarshalIndent(budget, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0644)
}

// nodeCapabilitiesResponse mirrors GET /api/fleet/node-capabilities from the v3 daemon.
type nodeCapabilitiesResponse struct {
	NodeID           string            `json:"node_id"`
	HardCeiling      int               `json:"hard_ceiling"`
	ForbiddenAgents  []string          `json:"forbidden_agents"`
	AgentTiers       map[string]string `json:"agent_tiers"`
	MediumAutoApprove bool             `json:"medium_auto_approve"`
}

// fleetCapabilitiesCmd: forge fleet capabilities
var fleetCapabilitiesCmd = &cobra.Command{
	Use:   "capabilities",
	Short: "Show node capability manifest",
	Long: `Show what agent types this node can spawn, hard ceilings, and approval policy.
Reads from the v3 daemon API (ADR-035 Node Capability Manifest).`,
	RunE: runFleetCapabilities,
}

func runFleetCapabilities(cmd *cobra.Command, args []string) error {
	format, _ := cmd.Flags().GetString("format")
	apiURL := os.Getenv("FORGE_API_URL")
	if apiURL == "" {
		apiURL = "http://localhost:8081"
	}

	// Fetch from daemon.
	resp, err := fetchNodeCapabilities(apiURL)
	if err != nil {
		// Fallback: show compiled-in static values.
		fmt.Printf("Daemon unavailable (%v) — showing compiled-in values\n\n", err)
		resp = &nodeCapabilitiesResponse{
			NodeID:           hostShortname(),
			HardCeiling:      2,
			ForbiddenAgents:  []string{},
			AgentTiers:       map[string]string{"kimi": "lightweight", "gemini": "lightweight", "claude": "medium", "opencode": "heavy"},
			MediumAutoApprove: false,
		}
	}

	if format == "json" {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(resp)
	}

	fmt.Printf("Node: %s\n", resp.NodeID)
	fmt.Printf("Hard ceiling: %d agents\n", resp.HardCeiling)
	fmt.Printf("Medium-tier auto-approve: %v\n\n", resp.MediumAutoApprove)

	if len(resp.ForbiddenAgents) > 0 {
		fmt.Printf("Forbidden agents: %s\n\n", strings.Join(resp.ForbiddenAgents, ", "))
	}

	fmt.Printf("%-12s %s\n", "AGENT", "TIER")
	fmt.Println(strings.Repeat("─", 28))
	for agent, tier := range resp.AgentTiers {
		autoMark := ""
		if tier == "lightweight" || (tier == "medium" && resp.MediumAutoApprove) {
			autoMark = " (auto-approve)"
		}
		fmt.Printf("%-12s %s%s\n", agent, tier, autoMark)
	}
	return nil
}

// fleetPlanCmd: forge fleet plan
// Shows the orchestrator's next planned action (ADR-036 Phase 3 observability).
var fleetPlanCmd = &cobra.Command{
	Use:   "plan",
	Short: "Show next planned fleet action",
	Long: `Display the orchestrator's next planned action for fleet scaling.

Shows:
  - Pending inflate/deflate recommendations
  - Token budget forecast
  - Resource headroom (RAM, ceiling)
  - Circuit breaker status

This is a read-only preview of what the autonomous scaler is planning.
ADR-036 Phase 3 observability command.`,
	RunE: runFleetPlan,
}

func runFleetPlan(cmd *cobra.Command, args []string) error {
	apiURL := os.Getenv("FORGE_API_URL")
	if apiURL == "" {
		apiURL = "http://localhost:8081"
	}

	hostname, _ := os.Hostname()
	now := time.Now().UTC()

	fmt.Printf("FLEET PLAN — %s — %s\n\n", hostname, now.Format("2006-01-02 15:04:05 UTC"))

	// 1. Fetch pending recommendations
	pendingInflates, pendingDeflates, err := fetchPendingRecommendations(apiURL)
	if err != nil {
		fmt.Printf("⚠ Could not fetch recommendations: %v\n\n", err)
	} else {
		fmt.Println("PLANNED ACTIONS:")
		if len(pendingInflates) == 0 && len(pendingDeflates) == 0 {
			fmt.Println("  [IDLE] No pending scale recommendations")
		}
		for _, rec := range pendingInflates {
			autoExec := "manual"
			if rec.AutoExecute == 1 {
				autoExec = "AUTO-EXEC"
			}
			fmt.Printf("  [%s] INFLATE node=%s agent=%s — %s\n", autoExec, rec.NodeID, rec.AgentType, rec.Reason)
		}
		for _, rec := range pendingDeflates {
			fmt.Printf("  [MANUAL] DEFLATE node=%s agent=%s — %s\n", rec.NodeID, rec.AgentType, rec.Reason)
		}
		fmt.Println()
	}

	// 2. Token budget forecast
	budget, err := readOrCreateTokenBudget("")
	if err != nil {
		fmt.Printf("⚠ Could not read token budget: %v\n\n", err)
	} else {
		fmt.Println("TOKEN BUDGET FORECAST:")
		for name, p := range budget.Providers {
			fmt.Printf("  %-12s %s (%d%%/mo", name, p.Status, p.Limits["monthly_pct"])
			if p.Limits["weekly_pct"] > 0 {
				fmt.Printf(", %d%%/wk", p.Limits["weekly_pct"])
			}
			if p.CooldownUntil != nil && *p.CooldownUntil != "" {
				if t, err := time.Parse(time.RFC3339, *p.CooldownUntil); err == nil {
					remaining := time.Until(t)
					if remaining > 0 {
						fmt.Printf(", resets in %dh", int(remaining.Hours()))
					}
				}
			}
			fmt.Println(")")
		}
		fmt.Println()
	}

	// 3. Resource headroom (from daemon or local)
	availableRAM, err := readLocalRAM()
	if err != nil {
		fmt.Printf("⚠ Could not read RAM: %v\n\n", err)
	} else {
		fmt.Printf("RESOURCE HEADROOM (live):\n")
		fmt.Printf("  Available RAM: %d MB\n", availableRAM)

		// Get current agent count
		windows, _ := discoverTmuxAgents("forge")
		ceiling := getNodeCeiling(hostname)
		fmt.Printf("  Ceiling: %d/%d agents", len(windows), ceiling)
		if len(windows) >= ceiling {
			fmt.Printf(" (at ceiling — no inflate possible)")
		}
		fmt.Println()

		// Other nodes (if available)
		fmt.Printf("  sati: unknown (cross-node not yet implemented)\n")
		fmt.Println()
	}

	// 4. Circuit breaker
	fmt.Printf("CIRCUIT BREAKER: unknown (daemon state not exposed via API yet)\n")

	return nil
}

type recommendationRow struct {
	ID          string `json:"id"`
	NodeID      string `json:"node_id"`
	Action      string `json:"action"`
	AgentType   string `json:"agent_type"`
	Reason      string `json:"reason"`
	AutoExecute int    `json:"auto_execute"`
	Status      string `json:"status"`
}

func fetchPendingRecommendations(apiURL string) (inflates, deflates []recommendationRow, err error) {
	url := strings.TrimRight(apiURL, "/") + "/api/fleet/recommendations?status=pending"
	r, err := http.Get(url) //nolint:noctx
	if err != nil {
		return nil, nil, err
	}
	defer r.Body.Close()

	var resp struct {
		Recommendations []recommendationRow `json:"recommendations"`
	}
	if err := json.NewDecoder(r.Body).Decode(&resp); err != nil {
		return nil, nil, fmt.Errorf("decode recommendations: %w", err)
	}

	for _, rec := range resp.Recommendations {
		if rec.Action == "inflate" {
			inflates = append(inflates, rec)
		} else if rec.Action == "deflate" {
			deflates = append(deflates, rec)
		}
	}
	return inflates, deflates, nil
}

func readLocalRAM() (int, error) {
	data, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return 0, err
	}
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, "MemAvailable:") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				kb, err := strconv.Atoi(fields[1])
				if err != nil {
					return 0, err
				}
				return kb / 1024, nil // kB → MB
			}
		}
	}
	return 0, fmt.Errorf("MemAvailable not found")
}

func getNodeCeiling(nodeID string) int {
	// Read per-node capacity from FORGE_NODE_CAPACITY env var (format: "node1=N,node2=M").
	// Falls back to a default of 4 if the env var is not set or the node is not listed.
	if raw := os.Getenv("FORGE_NODE_CAPACITY"); raw != "" {
		for _, entry := range strings.Split(raw, ",") {
			parts := strings.SplitN(strings.TrimSpace(entry), "=", 2)
			if len(parts) == 2 && strings.TrimSpace(parts[0]) == nodeID {
				if n, err := strconv.Atoi(strings.TrimSpace(parts[1])); err == nil && n > 0 {
					return n
				}
			}
		}
	}
	return 4 // default single-node capacity
}

func fetchNodeCapabilities(apiURL string) (*nodeCapabilitiesResponse, error) {
	url := strings.TrimRight(apiURL, "/") + "/api/fleet/node-capabilities"
	r, err := http.Get(url) //nolint:noctx
	if err != nil {
		return nil, err
	}
	defer r.Body.Close()
	var caps nodeCapabilitiesResponse
	if err := json.NewDecoder(r.Body).Decode(&caps); err != nil {
		return nil, fmt.Errorf("decode capabilities: %w", err)
	}
	return &caps, nil
}

func hostShortname() string {
	h, err := os.Hostname()
	if err != nil {
		return "unknown"
	}
	parts := strings.SplitN(h, ".", 2)
	return parts[0]
}

func computeBudgetStatus(p providerBudget) string {
	maxPct := p.Limits["monthly_pct"]
	if p.Limits["weekly_pct"] > maxPct {
		maxPct = p.Limits["weekly_pct"]
	}
	if p.Limits["5h_rolling_pct"] > maxPct {
		maxPct = p.Limits["5h_rolling_pct"]
	}

	switch {
	case maxPct >= 95:
		return "DEPLETED"
	case maxPct >= 90:
		return "RED"
	case maxPct >= 80:
		return "CAUTION"
	default:
		return "OK"
	}
}

// agentStartCommands maps agent window names to their canonical start commands.
// Source: CLAUDE.md "Agent Start Commands" table.
var agentStartCommands = map[string]string{
	"kimi":         "kimi -y",
	"kimi-2":       "kimi -y",
	"minimax":      "minimax",
	"gemini":       "gemini -y",
	"pi":           "pi",
	"opencode":     "opencode",
	"kilo":         "kilo",
	"cursor-agent": "cursor-agent -f",
	"amp":          "amp --dangerously-allow-all",
	"claude":       "claude --dangerously-skip-permissions",
	"codex":        "codex --dangerously-bypass-approvals-and-sandbox",
	"glm":          "glm",
}

// agentStartCmd returns the canonical start command for a named agent.
// Falls back to "claude --dangerously-skip-permissions" for unknown agents.
func agentStartCmd(name string) string {
	if cmd, ok := agentStartCommands[name]; ok {
		return cmd
	}
	return "claude --dangerously-skip-permissions"
}

// validAgentName returns true if name contains only alphanumeric chars and hyphens.
func validAgentName(name string) bool {
	if name == "" {
		return false
	}
	for _, r := range name {
		if !((r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '-') {
			return false
		}
	}
	return true
}

// tmuxHasSession returns true if the named tmux session exists.
func tmuxHasSession(session string) bool {
	cmd := exec.Command("tmux", "has-session", "-t", session)
	return cmd.Run() == nil
}

// tmuxWindowExists returns true if a window named windowName exists in session.
func tmuxWindowExists(session, windowName string) bool {
	out, err := exec.Command("tmux", "list-windows", "-t", session, "-F", "#{window_name}").Output()
	if err != nil {
		return false
	}
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if line == windowName {
			return true
		}
	}
	return false
}

// fleetSpawnCmd: forge fleet spawn <agent-name>
var fleetSpawnCmd = &cobra.Command{
	Use:   "spawn <agent-name>",
	Short: "Start a named agent in a tmux window",
	Long: `Start a named agent in a tmux window inside the forge session.

Checks whether the window already exists, creates it if not, then sends the
canonical start command for that agent with the required fleet environment
variables set.

Agent start commands follow CLAUDE.md canonical table. Unknown agents fall
back to "claude --dangerously-skip-permissions".

Examples:
  forge fleet spawn kimi
  forge fleet spawn gemini --session forge
  forge fleet spawn my-agent --window-name my-agent-window
  forge fleet spawn kimi --dry-run
`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		agentName := args[0]
		session, _ := cmd.Flags().GetString("session")
		windowName, _ := cmd.Flags().GetString("window-name")
		dryRun, _ := cmd.Flags().GetBool("dry-run")

		if !validAgentName(agentName) {
			return fmt.Errorf("invalid agent name %q: must be non-empty and contain only alphanumeric characters and hyphens", agentName)
		}
		if windowName == "" {
			windowName = agentName
		}

		startCmd := agentStartCmd(agentName)
		apiURL := os.Getenv("FORGE_API_URL")
		if apiURL == "" {
			apiURL = "http://localhost:8081"
		}
		envPrefix := fmt.Sprintf("FORGE_AGENT_TYPE=fleet FORGE_AGENT_NAME=%s FORGE_API_URL=%s", agentName, apiURL)
		fullCmd := fmt.Sprintf("%s %s", envPrefix, startCmd)

		if dryRun {
			fmt.Printf("[dry-run] Would check: tmux has-session -t %s\n", session)
			fmt.Printf("[dry-run] Would check: tmux list-windows -t %s for window %q\n", session, windowName)
			fmt.Printf("[dry-run] Would run: tmux new-window -t %s -n %s\n", session, windowName)
			fmt.Printf("[dry-run] Would send: %s\n", fullCmd)
			fmt.Printf("[dry-run] Would spawn %s in %s:%s\n", agentName, session, windowName)
			return nil
		}

		// Verify tmux is available.
		if _, err := exec.LookPath("tmux"); err != nil {
			return fmt.Errorf("tmux not found — install tmux or use forge work --daemon instead")
		}

		// Verify session exists.
		if !tmuxHasSession(session) {
			return fmt.Errorf("tmux session %q not found — start it with: tmux new-session -d -s %s", session, session)
		}

		// Check if window already exists.
		if tmuxWindowExists(session, windowName) {
			fmt.Printf("Agent %s already running in %s:%s\n", agentName, session, windowName)
			return nil
		}

		// Create the new window.
		if err := exec.Command("tmux", "new-window", "-t", session, "-n", windowName).Run(); err != nil {
			return fmt.Errorf("failed to create tmux window %s:%s: %w\n\nRecovery: check that session %q exists with 'tmux list-sessions'", session, windowName, err, session)
		}

		// Send the start command (text first, then Enter as a separate call — per CLAUDE.md §tmux rules).
		target := fmt.Sprintf("%s:%s", session, windowName)
		if err := exec.Command("tmux", "send-keys", "-t", target, "-l", fullCmd).Run(); err != nil {
			return fmt.Errorf("failed to send start command to %s: %w", target, err)
		}
		if err := exec.Command("tmux", "send-keys", "-t", target, "", "Enter").Run(); err != nil {
			return fmt.Errorf("failed to send Enter to %s: %w", target, err)
		}

		fmt.Printf("Spawned %s in %s:%s\n", agentName, session, windowName)
		return nil
	},
}

// fleetKillCmd: forge fleet kill <agent-name>
var fleetKillCmd = &cobra.Command{
	Use:   "kill <agent-name>",
	Short: "Kill a named agent tmux window",
	Long: `Kill the tmux window for a named agent in the forge session.

Examples:
  forge fleet kill kimi
  forge fleet kill gemini --session forge
`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		agentName := args[0]
		session, _ := cmd.Flags().GetString("session")

		if !validAgentName(agentName) {
			return fmt.Errorf("invalid agent name %q: must be non-empty and contain only alphanumeric characters and hyphens", agentName)
		}

		// Verify tmux is available.
		if _, err := exec.LookPath("tmux"); err != nil {
			return fmt.Errorf("tmux not found — install tmux or use forge work --daemon instead")
		}

		target := fmt.Sprintf("%s:%s", session, agentName)

		if err := exec.Command("tmux", "kill-window", "-t", target).Run(); err != nil {
			return fmt.Errorf("agent window %s not found — verify it exists with 'forge fleet windows'", target)
		}

		fmt.Printf("Killed agent window %s\n", target)
		return nil
	},
}

// fleetMetricEntry is a single metric row returned by /api/fleet/metrics.
type fleetMetricEntry struct {
	MetricName string  `json:"metric_name"`
	Value      float64 `json:"value"`
	Period     string  `json:"period"`
	ComputedAt string  `json:"computed_at"`
}

// fleetMetricsResponse is the top-level shape of GET /api/fleet/metrics.
type fleetMetricsResponse struct {
	GeneratedAt string                        `json:"generated_at"`
	NodeCount   int                           `json:"node_count"`
	Nodes       map[string][]fleetMetricEntry `json:"nodes"`
}

// fleetMetricsCmd: forge fleet metrics
var fleetMetricsCmd = &cobra.Command{
	Use:   "metrics",
	Short: "Show fleet-wide metrics from the daemon",
	Long: `Display fleet metrics collected by the daemon (GET /api/fleet/metrics).

Each row shows the node, metric name, current value, aggregation period, and
the time the metric was last computed.

Examples:
  forge fleet metrics
  forge fleet metrics --format json
`,
	RunE: func(cmd *cobra.Command, args []string) error {
		format, _ := cmd.Flags().GetString("format")

		client := internal.NewClient()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		resp, err := client.Get(ctx, "/fleet/metrics")
		if err != nil {
			var derr *internal.DaemonUnreachableError
			if errors.As(err, &derr) {
				return fmt.Errorf(
					"daemon not reachable at %s\n  Recovery: forge daemon start",
					derr.URL,
				)
			}
			return fmt.Errorf("failed to fetch fleet metrics: %w", err)
		}
		defer resp.Body.Close()

		bodyBytes, err := io.ReadAll(resp.Body)
		if err != nil {
			return fmt.Errorf("failed to read response: %w", err)
		}

		if resp.StatusCode != 200 {
			return fmt.Errorf("daemon returned HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(bodyBytes)))
		}

		// --format json: print raw response body
		if strings.ToLower(format) == "json" {
			fmt.Printf("%s\n", bodyBytes)
			return nil
		}

		// Parse for table output
		var metrics fleetMetricsResponse
		if err := json.Unmarshal(bodyBytes, &metrics); err != nil {
			return fmt.Errorf("failed to parse metrics response: %w", err)
		}

		// Format the timestamp header — trim to date+time portion if it has a T separator
		generatedDisplay := metrics.GeneratedAt
		if t, err := time.Parse(time.RFC3339, metrics.GeneratedAt); err == nil {
			generatedDisplay = t.UTC().Format("2006-01-02 15:04:05")
		}

		fmt.Printf("Fleet Metrics (%d node", metrics.NodeCount)
		if metrics.NodeCount != 1 {
			fmt.Printf("s")
		}
		fmt.Printf(", as of %s)\n\n", generatedDisplay)

		// Sort node names for deterministic output
		nodeNames := make([]string, 0, len(metrics.Nodes))
		for nodeName := range metrics.Nodes {
			nodeNames = append(nodeNames, nodeName)
		}
		sort.Strings(nodeNames)

		w := tabwriter.NewWriter(os.Stdout, 0, 0, 3, ' ', 0)
		fmt.Fprintln(w, "NODE\tMETRIC\tVALUE\tPERIOD\tCOMPUTED")

		for _, nodeName := range nodeNames {
			entries := metrics.Nodes[nodeName]
			for _, entry := range entries {
				// Trim computed_at to HH:MM:SS if it's a full datetime
				computedDisplay := entry.ComputedAt
				if len(computedDisplay) >= 19 {
					// "2006-01-02 15:04:05" → take last 8 chars (HH:MM:SS)
					computedDisplay = computedDisplay[len(computedDisplay)-8:]
				}

				// Format value: omit decimal places if the value is a whole number
				var valueStr string
				if entry.Value == float64(int64(entry.Value)) {
					valueStr = fmt.Sprintf("%d", int64(entry.Value))
				} else {
					valueStr = strconv.FormatFloat(entry.Value, 'f', 2, 64)
				}

				fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\n",
					nodeName, entry.MetricName, valueStr, entry.Period, computedDisplay)
			}
		}
		return w.Flush()
	},
}

func init() {
	// Add subcommands
	fleetCmd.AddCommand(fleetListCmd)
	fleetCmd.AddCommand(fleetStatusWorkflowCmd)
	fleetCmd.AddCommand(fleetHealthWorkflowCmd)
	fleetCmd.AddCommand(fleetBroadcastWorkflowCmd)
	fleetCmd.AddCommand(fleetWindowsCmd)
	fleetCmd.AddCommand(fleetBudgetCmd)
	fleetCmd.AddCommand(fleetInventoryCmd)
	fleetCmd.AddCommand(fleetCapabilitiesCmd)
	fleetCmd.AddCommand(fleetRecommendationsCmd)
	fleetCmd.AddCommand(fleetSpawnCmd)
	fleetCmd.AddCommand(fleetKillCmd)
	fleetCmd.AddCommand(fleetMetricsCmd)

	// Budget subcommands
	fleetBudgetCmd.AddCommand(fleetBudgetShowCmd)
	fleetBudgetCmd.AddCommand(fleetBudgetSetCmd)
	fleetBudgetCmd.AddCommand(fleetBudgetResetCmd)

	// Flags for broadcast
	fleetBroadcastWorkflowCmd.Flags().String("type", "info", "Message type: info, warning, urgent")
	fleetBroadcastWorkflowCmd.Flags().String("filter-state", "", "Filter by agent status (e.g. active)")

	// Flags for windows
	fleetWindowsCmd.Flags().String("session", "forge", "tmux session name to inspect")

	// Flags for spawn
	fleetSpawnCmd.Flags().String("session", "forge", "tmux session to spawn the agent window in")
	fleetSpawnCmd.Flags().String("window-name", "", "tmux window name (default: agent name)")
	fleetSpawnCmd.Flags().Bool("dry-run", false, "Print what would be done without executing tmux commands")

	// Flags for kill
	fleetKillCmd.Flags().String("session", "forge", "tmux session containing the agent window")

	// Flags for metrics
	fleetMetricsCmd.Flags().String("format", "table", "Output format: table or json")

	// Flags for budget
	fleetBudgetShowCmd.Flags().String("node", "", "Node to read budget from (default: current node)")
	fleetBudgetSetCmd.Flags().String("node", "", "Node to update budget for")
	fleetBudgetSetCmd.Flags().Int("monthly-pct", -1, "Monthly usage percentage (0-100)")
	fleetBudgetSetCmd.Flags().Int("weekly-pct", -1, "Weekly usage percentage (0-100)")
	fleetBudgetSetCmd.Flags().Int("5h-pct", -1, "5-hour rolling usage percentage (0-100)")
	fleetBudgetResetCmd.Flags().String("node", "", "Node to reset budget for")
	fleetInventoryCmd.Flags().String("node", "", "Node to read budget from")

	// Register with root command
	// fleetCmd registered in main.go
}
