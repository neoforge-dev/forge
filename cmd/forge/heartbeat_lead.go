package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
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

var loopStateFile = ".forge/heartbeat/loop_state.json"

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

// autoCommitResults stages and commits new result files written since the last
// commit timestamp recorded in loopState. Returns nil (no-op) when the tree is
// already clean or no new files are present.
func autoCommitResults(s *loopState) error {
	refTime := time.Now().Add(-24 * time.Hour) // safe fallback
	if s.LastCommit != "" {
		if t, err := time.Parse(time.RFC3339, s.LastCommit); err == nil {
			refTime = t
		}
	}

	count, err := newResultFiles(refTime)
	if err != nil {
		return fmt.Errorf("scan results: %w", err)
	}
	if count == 0 {
		return nil
	}

	if out, err := exec.Command("git", "add", ".forge/heartbeat/results/").CombinedOutput(); err != nil {
		return fmt.Errorf("git add: %w\n%s", err, out)
	}

	msg := fmt.Sprintf("chore(heartbeat): commit %d result file(s) [HB #%d]", count, s.HeartbeatCount)
	if out, err := exec.Command("git", "commit", "-m", msg).CombinedOutput(); err != nil {
		// "nothing to commit" is not a real error — git exits 1 with that message.
		if strings.Contains(string(out), "nothing to commit") {
			return nil
		}
		return fmt.Errorf("git commit: %w\n%s", err, out)
	}

	fmt.Printf("[run] committed %d result file(s): %s\n", count, msg)
	s.LastCommit = time.Now().UTC().Format(time.RFC3339)
	return writeLoopState(s)
}

var heartbeatRunCmd = &cobra.Command{
	Use:   "run",
	Short: "Autonomous eval loop: tick → check triggers → (optionally) commit results",
	Long: `Run the lead orchestrator heartbeat loop.

Each cycle:
  1. Increment heartbeat counter and print trigger recommendations (same as 'eval')
  2. If --commit is set and new result files exist, stage and commit them
  3. Wait for --interval, then repeat

Safety guardrails:
  --max-iterations N  stop after N cycles (0 = run until Ctrl+C)
  --context-max N     stop if $FORGE_CONTEXT_PCT exceeds N% (default 75)

Examples:
  forge heartbeat run                      # eval loop, no auto-commit
  forge heartbeat run --interval 15m       # 15-minute eval cycle
  forge heartbeat run --commit             # also auto-commit result files
  forge heartbeat run --max-iterations 8   # stop after 8 cycles`,
	RunE: runHeartbeatRun,
}

func init() {
	heartbeatRunCmd.Flags().Duration("interval", 15*time.Minute, "Interval between heartbeat cycles")
	heartbeatRunCmd.Flags().Int("max-iterations", 0, "Maximum number of cycles to run (0 = unlimited)")
	heartbeatRunCmd.Flags().Bool("commit", false, "Auto-commit new result files after each eval")
	heartbeatRunCmd.Flags().Float64("context-max", 75.0, "Stop if context window exceeds this percentage")
}

func runHeartbeatRun(cmd *cobra.Command, args []string) error {
	interval, _ := cmd.Flags().GetDuration("interval")
	maxIter, _ := cmd.Flags().GetInt("max-iterations")
	doCommit, _ := cmd.Flags().GetBool("commit")
	contextMax, _ := cmd.Flags().GetFloat64("context-max")

	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	defer signal.Stop(sigs)

	fmt.Printf("Heartbeat loop started\n")
	fmt.Printf("  Interval:  %s\n", interval)
	if maxIter > 0 {
		fmt.Printf("  Max iter:  %d\n", maxIter)
	}
	fmt.Printf("  Auto-commit: %v\n", doCommit)
	fmt.Printf("  Context max: %.0f%%\n", contextMax)
	fmt.Printf("  Press Ctrl+C to stop\n\n")

	iter := 0
	for {
		// Safety: stop if context window is too full.
		if pct := readContextPct(); pct > contextMax {
			fmt.Printf("[run] context %.1f%% exceeds max %.1f%% — stopping\n", pct, contextMax)
			return nil
		}

		// Eval: increment counter and print triggers.
		s, err := readLoopState()
		if err != nil {
			fmt.Fprintf(os.Stderr, "[run] read loop state: %v\n", err)
		} else {
			s.HeartbeatCount++
			if werr := writeLoopState(s); werr != nil {
				fmt.Fprintf(os.Stderr, "[run] write loop state: %v\n", werr)
			} else {
				if perr := printEvalOutput(s); perr != nil {
					fmt.Fprintf(os.Stderr, "[run] eval output: %v\n", perr)
				}
			}

			// Auto-commit result files if requested.
			if doCommit {
				if cerr := autoCommitResults(s); cerr != nil {
					fmt.Fprintf(os.Stderr, "[run] commit: %v\n", cerr)
				}
			}
		}

		iter++
		if maxIter > 0 && iter >= maxIter {
			fmt.Printf("\n[run] reached max iterations (%d) — stopping\n", maxIter)
			return nil
		}

		// Wait for next cycle, checking for shutdown every 200ms.
		deadline := time.Now().Add(interval)
		for time.Now().Before(deadline) {
			remaining := time.Until(deadline)
			wait := remaining
			if wait > 200*time.Millisecond {
				wait = 200 * time.Millisecond
			}
			select {
			case sig := <-sigs:
				fmt.Printf("\n[run] received %s — shutting down after %d iteration(s)\n", sig, iter)
				return nil
			case <-time.After(wait):
			}
		}
	}
}

func init() {
	heartbeatCmd.AddCommand(heartbeatEvalCmd)
	heartbeatCmd.AddCommand(heartbeatStatusCmd)
	heartbeatCmd.AddCommand(heartbeatResetCmd)
	heartbeatCmd.AddCommand(heartbeatRetroCmd)
	heartbeatCmd.AddCommand(heartbeatCrossNodeCmd)
	heartbeatCmd.AddCommand(heartbeatRunCmd)
}
