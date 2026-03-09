package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// loopState is the persistent session counter for the lead orchestrator loop.
type loopState struct {
	HeartbeatCount int    `json:"heartbeat_count"`
	SessionStart   string `json:"session_start"`
	LastRetro      int    `json:"last_retro"`
	LastCrossNode  int    `json:"last_crossnode"`
	LastCommit     string `json:"last_commit"`
}

const loopStateFile = ".forge/heartbeat/loop_state.json"

func readLoopState() (*loopState, error) {
	data, err := os.ReadFile(loopStateFile)
	if os.IsNotExist(err) {
		now := time.Now().UTC().Format(time.RFC3339)
		return &loopState{
			HeartbeatCount: 0,
			SessionStart:   now,
			LastRetro:      0,
			LastCrossNode:  0,
			LastCommit:     now,
		}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read loop state: %w", err)
	}
	var s loopState
	if err := json.Unmarshal(data, &s); err != nil {
		return nil, fmt.Errorf("parse loop state: %w", err)
	}
	return &s, nil
}

func writeLoopState(s *loopState) error {
	if err := os.MkdirAll(filepath.Dir(loopStateFile), 0o755); err != nil {
		return fmt.Errorf("mkdir loop state dir: %w", err)
	}
	data, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(loopStateFile, data, 0o644)
}

// gitDirty returns number of changed files (0 = clean tree).
func gitDirty() (int, error) {
	out, err := exec.Command("git", "status", "--short").Output()
	if err != nil {
		return 0, err
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	count := 0
	for _, l := range lines {
		if strings.TrimSpace(l) != "" {
			count++
		}
	}
	return count, nil
}

// idleAgentsAndPending returns (idleCount, pendingTasks, skipReason).
func idleAgentsAndPending() (int, int, string) {
	client := internal.NewClient()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	agentList, err := client.ListAgents(ctx)
	if err != nil {
		return 0, 0, "daemon unreachable"
	}

	idle := 0
	for _, a := range agentList.Agents {
		if a.CurrentTask == nil && a.Status == "online" {
			idle++
		}
	}

	taskList, err := client.ListTasks(ctx, "pending", 100, "")
	if err != nil {
		return idle, 0, ""
	}

	return idle, len(taskList.Tasks), ""
}

// newResultFiles returns count of result files newer than refTime.
func newResultFiles(refTime time.Time) (int, error) {
	dir := ".forge/heartbeat/results"
	entries, err := os.ReadDir(dir)
	if os.IsNotExist(err) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	count := 0
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		if info.ModTime().After(refTime) {
			count++
		}
	}
	return count, nil
}

func printEvalOutput(s *loopState) error {
	now := time.Now().UTC()
	hb := s.HeartbeatCount
	ts := now.Format("2006-01-02 15:04")

	fmt.Printf("Heartbeat #%d — %s\n", hb, ts)
	fmt.Println("─────────────────────────────────")
	fmt.Println("TRIGGERS:")

	// COMMIT
	dirtyCount, err := gitDirty()
	if err != nil {
		fmt.Printf("  ? COMMIT    — git unavailable (%v)\n", err)
	} else if dirtyCount > 0 {
		fmt.Printf("  ✓ COMMIT    — %d file(s) changed since last commit\n", dirtyCount)
	} else {
		fmt.Println("  – COMMIT    — tree is clean")
	}

	// DISPATCH
	idle, pending, skipReason := idleAgentsAndPending()
	if skipReason != "" {
		fmt.Printf("  ? DISPATCH  — skipped (%s)\n", skipReason)
	} else if idle > 0 && pending > 0 {
		fmt.Printf("  ✓ DISPATCH  — %d agent(s) idle, queue has %d pending task(s)\n", idle, pending)
	} else {
		fmt.Printf("  – DISPATCH  — %d idle agent(s), %d pending task(s)\n", idle, pending)
	}

	// RETRO
	sinceRetro := hb - s.LastRetro
	if sinceRetro >= 3 {
		fmt.Printf("  ✓ RETRO     — %d heartbeats since last retro\n", sinceRetro)
	} else {
		nextRetro := s.LastRetro + 3
		remaining := nextRetro - hb
		fmt.Printf("  – RETRO     — next in %d heartbeat(s) (HB %d)\n", remaining, nextRetro)
	}

	// CROSSNODE
	sinceCrossNode := hb - s.LastCrossNode
	if sinceCrossNode >= 5 {
		fmt.Printf("  ✓ CROSSNODE — %d heartbeats since last crossnode sync\n", sinceCrossNode)
	} else {
		nextCross := s.LastCrossNode + 5
		remaining := nextCross - hb
		fmt.Printf("  – CROSSNODE — next in %d heartbeat(s) (HB %d)\n", remaining, nextCross)
	}

	// RESULTS — compare against last_commit timestamp (or 15 min ago as fallback)
	refTime := now.Add(-15 * time.Minute)
	if s.LastCommit != "" {
		if t, err := time.Parse(time.RFC3339, s.LastCommit); err == nil {
			refTime = t
		}
	}
	newFiles, err := newResultFiles(refTime)
	if err != nil {
		fmt.Printf("  ? RESULTS   — error reading results dir (%v)\n", err)
	} else if newFiles > 0 {
		fmt.Printf("  ✓ RESULTS   — %d new result file(s) since last commit\n", newFiles)
	} else {
		fmt.Println("  – RESULTS   — no new result files")
	}

	return nil
}

// ─── subcommands ────────────────────────────────────────────────────────────

var heartbeatEvalCmd = &cobra.Command{
	Use:   "eval",
	Short: "Increment session counter and print trigger recommendations",
	RunE: func(cmd *cobra.Command, args []string) error {
		s, err := readLoopState()
		if err != nil {
			return err
		}
		s.HeartbeatCount++
		if err := writeLoopState(s); err != nil {
			return err
		}
		return printEvalOutput(s)
	},
}

var heartbeatStatusCmd = &cobra.Command{
	Use:   "status",
	Short: "Print current heartbeat state without incrementing counter",
	RunE: func(cmd *cobra.Command, args []string) error {
		s, err := readLoopState()
		if err != nil {
			return err
		}
		return printEvalOutput(s)
	},
}

var heartbeatResetCmd = &cobra.Command{
	Use:   "reset",
	Short: "Zero all counters and start a new session",
	RunE: func(cmd *cobra.Command, args []string) error {
		now := time.Now().UTC().Format(time.RFC3339)
		s := &loopState{
			HeartbeatCount: 0,
			SessionStart:   now,
			LastRetro:      0,
			LastCrossNode:  0,
			LastCommit:     now,
		}
		if err := writeLoopState(s); err != nil {
			return err
		}
		fmt.Println("Heartbeat state reset. Starting at HB #0.")
		return nil
	},
}

var heartbeatRetroCmd = &cobra.Command{
	Use:   "retro",
	Short: "Mark a retro as done at the current heartbeat count",
	RunE: func(cmd *cobra.Command, args []string) error {
		s, err := readLoopState()
		if err != nil {
			return err
		}
		s.LastRetro = s.HeartbeatCount
		if err := writeLoopState(s); err != nil {
			return err
		}
		fmt.Printf("Retro marked at HB #%d.\n", s.HeartbeatCount)
		return nil
	},
}

var heartbeatCrossNodeCmd = &cobra.Command{
	Use:   "crossnode",
	Short: "Mark a crossnode sync as done at the current heartbeat count",
	RunE: func(cmd *cobra.Command, args []string) error {
		s, err := readLoopState()
		if err != nil {
			return err
		}
		s.LastCrossNode = s.HeartbeatCount
		if err := writeLoopState(s); err != nil {
			return err
		}
		fmt.Printf("Crossnode sync marked at HB #%d.\n", s.HeartbeatCount)
		return nil
	},
}

func init() {
	heartbeatCmd.AddCommand(heartbeatEvalCmd)
	heartbeatCmd.AddCommand(heartbeatStatusCmd)
	heartbeatCmd.AddCommand(heartbeatResetCmd)
	heartbeatCmd.AddCommand(heartbeatRetroCmd)
	heartbeatCmd.AddCommand(heartbeatCrossNodeCmd)
}
