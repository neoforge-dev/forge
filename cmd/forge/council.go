// council.go — forge council commands
//
// Implements council-as-a-service (ADR-035 §Council lifecycle):
//   forge council start  --size N --ttl 30m  Start a council session
//   forge council status                      Show active council sessions
//   forge council stop   [agent]              End a council session early
//   forge council review [TC-ID]              Review proposal votes and consensus

package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
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

// councilProposalDir returns the path to .forge/council/ where TC directories live.
func councilProposalDir() string {
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
	return filepath.Join(forgeRoot, ".forge", "council")
}

// councilRecord holds the parsed state for a single council TC directory.
type councilRecord struct {
	ID        string            `json:"id"`
	Status    string            `json:"status"`
	VoteCount int               `json:"vote_count"`
	VoteTotal int               `json:"vote_total"`
	Consensus string            `json:"consensus"`
	Votes     []councilVoteItem `json:"votes,omitempty"`
	DirPath   string            `json:"dir_path"`
}

// councilVoteItem holds a single agent's vote.
type councilVoteItem struct {
	Agent    string `json:"agent"`
	Vote     string `json:"vote"`
	SourceFile string `json:"source_file"`
}

// voteKeywords is the ordered list of keywords to detect in response files.
var voteKeywords = []string{
	"APPROVE-WITH-CONDITIONS",
	"APPROVE-WITH-CHANGES",
	"APPROVE",
	"REJECT",
	"MODIFY",
}

// detectVoteInFile scans a markdown file and returns the first vote keyword found.
// Returns empty string if no vote keyword is detected.
func detectVoteInFile(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	upper := strings.ToUpper(string(data))
	for _, kw := range voteKeywords {
		if strings.Contains(upper, kw) {
			return kw
		}
	}
	return ""
}

// loadCouncilRecord reads a TC directory and returns a fully-populated councilRecord.
func loadCouncilRecord(tcDir string) councilRecord {
	id := filepath.Base(tcDir)
	rec := councilRecord{
		ID:      id,
		DirPath: tcDir,
	}

	// Determine status based on presence of synthesis.md or response files.
	synthesisPath := filepath.Join(tcDir, "synthesis.md")
	hasSynthesis := false
	if _, err := os.Stat(synthesisPath); err == nil {
		hasSynthesis = true
	}

	// Collect vote files from responses/ and votes/, deduplicating by agent name.
	agentVotes := make(map[string]councilVoteItem) // keyed by agent name

	for _, subdir := range []string{"responses", "votes"} {
		dir := filepath.Join(tcDir, subdir)
		entries, err := os.ReadDir(dir)
		if err != nil {
			continue
		}
		for _, entry := range entries {
			if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".md") {
				continue
			}
			agentName := strings.TrimSuffix(entry.Name(), ".md")
			filePath := filepath.Join(dir, entry.Name())
			relSource := filepath.Join(subdir, entry.Name())

			vote := detectVoteInFile(filePath)
			// Only update if we haven't seen this agent yet, or if we now have a vote
			// and the existing entry had no vote.
			if existing, seen := agentVotes[agentName]; !seen || (existing.Vote == "" && vote != "") {
				agentVotes[agentName] = councilVoteItem{
					Agent:    agentName,
					Vote:     vote,
					SourceFile: relSource,
				}
			}
		}
	}

	// Sort agents for deterministic output.
	agents := make([]string, 0, len(agentVotes))
	for a := range agentVotes {
		agents = append(agents, a)
	}
	sort.Strings(agents)

	for _, a := range agents {
		rec.Votes = append(rec.Votes, agentVotes[a])
	}
	rec.VoteTotal = len(agentVotes)
	for _, v := range rec.Votes {
		if v.Vote != "" {
			rec.VoteCount++
		}
	}

	// Determine status.
	if hasSynthesis {
		rec.Status = "decided"
	} else if rec.VoteTotal > 0 {
		rec.Status = "voting"
	} else {
		rec.Status = "proposed"
	}

	// Determine consensus.
	if hasSynthesis {
		// Extract decision from synthesis.md.
		data, err := os.ReadFile(synthesisPath)
		if err == nil {
			upper := strings.ToUpper(string(data))
			rec.Consensus = "unknown"
			for _, kw := range voteKeywords {
				if strings.Contains(upper, kw) {
					rec.Consensus = kw
					break
				}
			}
		} else {
			rec.Consensus = "unknown"
		}
	} else if rec.VoteCount == 0 {
		rec.Consensus = "pending"
	} else {
		// Tally votes.
		tally := make(map[string]int)
		for _, v := range rec.Votes {
			if v.Vote != "" {
				tally[v.Vote]++
			}
		}
		// Find majority (or the most common vote keyword).
		best := ""
		bestCount := 0
		// Iterate in keyword priority order for ties.
		for _, kw := range voteKeywords {
			if c := tally[kw]; c > bestCount {
				bestCount = c
				best = kw
			}
		}
		if best != "" {
			rec.Consensus = best
		} else {
			rec.Consensus = "pending"
		}
	}

	return rec
}

var councilReviewCmd = &cobra.Command{
	Use:   "review [TC-ID]",
	Short: "Review council proposals and vote summaries",
	Long: `Review council proposal files from .forge/council/.

Without an argument, lists all councils with vote status.
With a TC-ID argument, shows detailed vote breakdown for that council.

Examples:
  # List all councils
  forge council review

  # Show detail for a specific council
  forge council review TC-CLI-AUDIT

  # Machine-readable output
  forge council review --format json
  forge council review TC-CLI-AUDIT --format json
`,
	RunE: runCouncilReview,
}

func runCouncilReview(cmd *cobra.Command, args []string) error {
	format, _ := cmd.Flags().GetString("format")
	proposalDir := councilProposalDir()

	// Detail mode: a specific TC-ID was provided.
	if len(args) > 0 {
		tcID := args[0]
		tcDir := filepath.Join(proposalDir, tcID)
		if _, err := os.Stat(tcDir); os.IsNotExist(err) {
			return fmt.Errorf(
				"council directory %q not found\n"+
					"  Expected path: %s\n"+
					"  Recovery: run `forge council review` (no args) to list available councils",
				tcID, tcDir,
			)
		}

		rec := loadCouncilRecord(tcDir)

		if format == "json" {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(rec)
		}

		// Human-readable detail view.
		proposalPath := filepath.Join(tcDir, "proposal.md")
		proposalRef := proposalPath
		if _, err := os.Stat(proposalPath); os.IsNotExist(err) {
			proposalRef = "(not found)"
		}

		fmt.Printf("Council: %s\n", rec.ID)
		fmt.Printf("Status:  %s\n", rec.Status)
		fmt.Printf("Proposal: %s\n\n", proposalRef)

		if len(rec.Votes) == 0 {
			fmt.Printf("Votes: none recorded\n")
		} else {
			fmt.Printf("Votes (%d/%d):\n", rec.VoteCount, rec.VoteTotal)
			for _, v := range rec.Votes {
				voteDisplay := v.Vote
				if voteDisplay == "" {
					voteDisplay = "(pending)"
				}
				fmt.Printf("  %-14s  %-28s  (%s)\n", v.Agent, voteDisplay, v.SourceFile)
			}
		}

		fmt.Printf("\nConsensus: %s\n", rec.Consensus)

		if rec.Status == "decided" {
			fmt.Printf("Synthesis: %s\n", filepath.Join(tcDir, "synthesis.md"))
		}
		return nil
	}

	// List mode: show all councils.
	entries, err := os.ReadDir(proposalDir)
	if err != nil {
		if os.IsNotExist(err) {
			fmt.Println("No council proposals found.")
			fmt.Printf("Expected directory: %s\n", proposalDir)
			fmt.Printf("Recovery: create a council proposal in .forge/council/TC-<TOPIC>/proposal.md\n")
			return nil
		}
		return fmt.Errorf(
			"read council proposal directory: %w\n"+
				"  Path: %s\n"+
				"  Recovery: verify FORGE_ROOT is set correctly or run from your project root",
			err, proposalDir,
		)
	}

	var records []councilRecord
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		name := entry.Name()
		// Skip hidden dirs and INDEX.md (it's a file, but guard anyway).
		if strings.HasPrefix(name, ".") {
			continue
		}
		tcDir := filepath.Join(proposalDir, name)
		records = append(records, loadCouncilRecord(tcDir))
	}

	if len(records) == 0 {
		fmt.Println("No council proposals found.")
		fmt.Printf("Recovery: create a council proposal in .forge/council/TC-<TOPIC>/proposal.md\n")
		return nil
	}

	if format == "json" {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(records)
	}

	// Human-readable table.
	fmt.Printf("%-42s %-10s %-8s %s\n", "COUNCIL ID", "STATUS", "VOTES", "CONSENSUS")
	fmt.Println(strings.Repeat("─", 78))
	for _, rec := range records {
		votes := fmt.Sprintf("%d/%d", rec.VoteCount, rec.VoteTotal)
		fmt.Printf("%-42s %-10s %-8s %s\n", rec.ID, rec.Status, votes, rec.Consensus)
	}
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
	councilCmd.Hidden = true
	councilCmd.AddCommand(councilStartCmd)
	councilCmd.AddCommand(councilStatusCmd)
	councilCmd.AddCommand(councilStopCmd)
	councilCmd.AddCommand(councilReviewCmd)

	councilStartCmd.Flags().Int("size", 3, "Number of council agents (uses default priority list)")
	councilStartCmd.Flags().String("ttl", "30m", "Session TTL (e.g. 30m, 1h, 90m)")
	councilStartCmd.Flags().String("agents", "", "Explicit comma-separated agent list (overrides --size)")

	councilStopCmd.Flags().Bool("all", false, "Stop all active council sessions")

	councilReviewCmd.Flags().String("format", "table", "Output format: table, json")
	councilStatusCmd.Flags().String("format", "table", "Output format: table, json")
}
