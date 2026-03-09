// state.go — forge state: Lead orchestrator FSM state management.
//
// Ports harness/forge_harness/cli_v2/state_manager.py to Go.
// All operations are pure local file I/O — no daemon calls.
//
// File paths (relative to FORGE root):
//
//	.forge/heartbeat/lead_state.json        — current FSM state
//	.forge/heartbeat/transition_log.jsonl   — append-only transition log
//	.forge/heartbeat/lead_nudge.json        — nudge file (anti-thrash)
//	.forge/heartbeat/HUMAN_ATTENTION_REQUIRED — escalation marker
//	.forge/heartbeat/context_percent        — context usage sidecar
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// ---------------------------------------------------------------------------
// Constants and file paths
// ---------------------------------------------------------------------------

const (
	stateFile         = ".forge/heartbeat/lead_state.json"
	transitionLogFile = ".forge/heartbeat/transition_log.jsonl"
	nudgeFile         = ".forge/heartbeat/lead_nudge.json"
	attentionFile     = ".forge/heartbeat/HUMAN_ATTENTION_REQUIRED"
	contextPercentSidecar = ".forge/heartbeat/context_percent"

	transitionHistoryMax      = 10
	antiThrashCooldownSeconds = 120
	contextStaleSeconds = 300
)

// validStates is the canonical set of FSM states.
var validStates = map[string]bool{
	"IDLE":      true,
	"WORKING":   true,
	"BLOCKED":   true,
	"HANDOFF":   true,
	"RETRO":     true,
	"CROSSNODE": true,
	"CRITICAL":  true,
	// Also accept legacy / extended states from Python version
	"DISPATCHING": true,
	"UNKNOWN":     true,
}

// ---------------------------------------------------------------------------
// Data types
// ---------------------------------------------------------------------------

// leadState mirrors the JSON schema written by state_manager.py.
type leadState struct {
	Version           int                      `json:"version"`
	State             string                   `json:"state"`
	PreviousState     string                   `json:"previous_state"`
	EnteredAt         *string                  `json:"entered_at"`
	ContextPercent    int                      `json:"context_percent"`
	CurrentSprint     *string                  `json:"current_sprint"`
	CurrentWave       int                      `json:"current_wave"`
	AgentsDispatched  []string                 `json:"agents_dispatched"`
	AgentsComplete    []string                 `json:"agents_complete"`
	PendingCommits    int                      `json:"pending_commits"`
	LastDispatchTime  *string                  `json:"last_dispatch_time"`
	LastTransition    *transitionEntry         `json:"last_transition"`
	TransitionHistory []transitionHistoryEntry `json:"transition_history"`
}

// transitionEntry is used for last_transition (has "timestamp" key).
type transitionEntry struct {
	From      string `json:"from"`
	To        string `json:"to"`
	Reason    string `json:"reason"`
	Timestamp string `json:"timestamp"`
}

// transitionHistoryEntry is the compact form stored inside transition_history (has "ts" key).
type transitionHistoryEntry struct {
	From   string `json:"from"`
	To     string `json:"to"`
	Reason string `json:"reason"`
	TS     string `json:"ts"`
}

// transitionLogEntry is what gets appended to transition_log.jsonl.
type transitionLogEntry struct {
	TS      string `json:"ts"`
	From    string `json:"from"`
	To      string `json:"to"`
	Reason  string `json:"reason"`
	Version int    `json:"version"`
}

// ---------------------------------------------------------------------------
// detectStateRoot walks up from cwd to find the FORGE root (.forge/ dir).
// Falls back to cwd if not found — mirrors Python _detect_forge_root().
// ---------------------------------------------------------------------------

func detectStateRoot() string {
	// Prefer explicit FORGE_ROOT env var
	if root := os.Getenv("FORGE_ROOT"); root != "" {
		return root
	}
	dir, err := os.Getwd()
	if err != nil {
		return "."
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, ".forge")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return "."
}

// stateFilePath returns an absolute path for a heartbeat file, rooted at the FORGE root.
func stateFilePath(rel string) string {
	root := detectStateRoot()
	return filepath.Join(root, rel)
}

// ---------------------------------------------------------------------------
// Core state I/O
// ---------------------------------------------------------------------------

// defaultLeadState returns the canonical default (mirrors Python _default_state_dict).
func defaultLeadState() *leadState {
	return &leadState{
		Version:          0,
		State:            "UNKNOWN",
		PreviousState:    "UNKNOWN",
		EnteredAt:        nil,
		ContextPercent:   0,
		CurrentSprint:    nil,
		CurrentWave:      0,
		AgentsDispatched: []string{},
		AgentsComplete:   []string{},
		PendingCommits:   0,
		LastDispatchTime: nil,
		LastTransition:   nil,
		TransitionHistory: []transitionHistoryEntry{},
	}
}

// readLeadState reads lead_state.json; returns default skeleton on any error.
func readLeadState() *leadState {
	path := stateFilePath(stateFile)
	data, err := os.ReadFile(path)
	if err != nil {
		return defaultLeadState()
	}
	var s leadState
	if err := json.Unmarshal(data, &s); err != nil {
		return defaultLeadState()
	}
	// Ensure slices are non-nil (so JSON output is [] not null)
	if s.AgentsDispatched == nil {
		s.AgentsDispatched = []string{}
	}
	if s.AgentsComplete == nil {
		s.AgentsComplete = []string{}
	}
	if s.TransitionHistory == nil {
		s.TransitionHistory = []transitionHistoryEntry{}
	}
	if s.State == "" || s.State == "null" {
		s.State = "UNKNOWN"
	}
	return &s
}

// writeLeadStateAtomic writes lead_state.json atomically via a tmp file + rename.
func writeLeadStateAtomic(s *leadState) error {
	path := stateFilePath(stateFile)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("mkdir state dir: %w", err)
	}

	tmp := fmt.Sprintf("%s.tmp.%d", path, os.Getpid())
	data, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal state: %w", err)
	}

	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return fmt.Errorf("write tmp state: %w", err)
	}
	if err := os.Rename(tmp, path); err != nil {
		os.Remove(tmp)
		return fmt.Errorf("rename state file: %w", err)
	}
	return nil
}

// appendTransitionLog appends a single JSON line to transition_log.jsonl.
func appendTransitionLog(entry transitionLogEntry) error {
	path := stateFilePath(transitionLogFile)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("mkdir transition log dir: %w", err)
	}

	data, err := json.Marshal(entry)
	if err != nil {
		return fmt.Errorf("marshal transition log entry: %w", err)
	}

	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return fmt.Errorf("open transition log: %w", err)
	}
	defer f.Close()

	_, err = f.WriteString(string(data) + "\n")
	return err
}

// readTransitionLog returns the last limit entries from transition_log.jsonl.
// Skips malformed lines silently, mirroring Python behavior.
func readTransitionLog(limit int) ([]transitionLogEntry, error) {
	path := stateFilePath(transitionLogFile)
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read transition log: %w", err)
	}

	var entries []transitionLogEntry
	scanner := bufio.NewScanner(strings.NewReader(string(data)))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var e transitionLogEntry
		if err := json.Unmarshal([]byte(line), &e); err != nil {
			continue // skip malformed lines
		}
		entries = append(entries, e)
	}

	if len(entries) > limit {
		entries = entries[len(entries)-limit:]
	}
	return entries, nil
}

// ---------------------------------------------------------------------------
// forge state (root command)
// ---------------------------------------------------------------------------

var stateCmd = &cobra.Command{
	Use:   "state",
	Short: "Read and write lead orchestrator FSM state",
	Long: `Manage the FORGE lead orchestrator FSM state.

All operations are pure local file I/O — no daemon required.

File paths (relative to FORGE root):
  .forge/heartbeat/lead_state.json          Current FSM state
  .forge/heartbeat/transition_log.jsonl     Append-only transition log
  .forge/heartbeat/lead_nudge.json          Nudge file (anti-thrash)
  .forge/heartbeat/HUMAN_ATTENTION_REQUIRED Escalation marker
  .forge/heartbeat/context_percent          Context usage sidecar`,
}

func init() {
	stateCmd.AddCommand(stateShowCmd)
	stateCmd.AddCommand(stateSetCmd)
	stateCmd.AddCommand(stateLogCmd)
	stateCmd.AddCommand(stateContextCmd)
	stateCmd.AddCommand(stateNudgeCmd)
	stateCmd.AddCommand(stateEscalateCmd)
}

// ---------------------------------------------------------------------------
// forge state show [--json]
// ---------------------------------------------------------------------------

var stateShowCmd = &cobra.Command{
	Use:   "show",
	Short: "Display the current FSM state",
	Long: `Print the current FSM state from .forge/heartbeat/lead_state.json.

Without --json prints a human-readable summary.
With --json prints the full state document.

Examples:
  forge state show
  forge state show --json`,
	RunE: func(cmd *cobra.Command, args []string) error {
		asJSON, _ := cmd.Flags().GetBool("json")
		s := readLeadState()

		if asJSON {
			data, err := json.MarshalIndent(s, "", "  ")
			if err != nil {
				return fmt.Errorf("marshal state: %w", err)
			}
			fmt.Println(string(data))
			return nil
		}

		// Human-readable summary
		enteredAt := "(unknown)"
		if s.EnteredAt != nil {
			enteredAt = *s.EnteredAt
		}
		previousState := s.PreviousState
		if previousState == "" {
			previousState = "UNKNOWN"
		}

		fmt.Printf("State:          %s%s%s\n", colorGreen, s.State, colorReset)
		fmt.Printf("Previous state: %s\n", previousState)
		fmt.Printf("Entered at:     %s\n", enteredAt)
		fmt.Printf("Version:        %d\n", s.Version)
		fmt.Printf("Context:        %d%%\n", s.ContextPercent)
		fmt.Printf("Wave:           %d\n", s.CurrentWave)
		if s.CurrentSprint != nil {
			fmt.Printf("Sprint:         %s\n", *s.CurrentSprint)
		}
		fmt.Printf("Pending commits:%d\n", s.PendingCommits)
		if len(s.AgentsDispatched) > 0 {
			fmt.Printf("Dispatched:     %s\n", strings.Join(s.AgentsDispatched, ", "))
		}
		if len(s.AgentsComplete) > 0 {
			fmt.Printf("Complete:       %s\n", strings.Join(s.AgentsComplete, ", "))
		}
		if s.LastTransition != nil {
			fmt.Printf("Last transition:%s -> %s  (%s)\n",
				s.LastTransition.From, s.LastTransition.To, s.LastTransition.Reason)
		}
		return nil
	},
}

func init() {
	stateShowCmd.Flags().Bool("json", false, "Output full state as JSON")
}

// ---------------------------------------------------------------------------
// forge state set <STATE> [--reason TEXT]
// ---------------------------------------------------------------------------

var stateSetCmd = &cobra.Command{
	Use:   "set <STATE>",
	Short: "Transition to a new FSM state",
	Long: `Write a new FSM state to .forge/heartbeat/lead_state.json.
Appends a transition entry to transition_log.jsonl.

Valid states: IDLE, WORKING, BLOCKED, HANDOFF, RETRO, CROSSNODE, CRITICAL

Examples:
  forge state set WORKING --reason "porting state_manager.py"
  forge state set IDLE --reason "sprint complete"
  forge state set HANDOFF --reason "context at 73%"`,
	Args: cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		newState := strings.ToUpper(strings.TrimSpace(args[0]))
		reason, _ := cmd.Flags().GetString("reason")
		asJSON, _ := cmd.Flags().GetBool("json")

		if reason == "" {
			reason = "manual transition"
		}

		now := time.Now().UTC().Format("2006-01-02T15:04:05Z")
		current := readLeadState()
		currentState := current.State
		newVersion := current.Version + 1

		// Build history entry (compact — uses "ts" key)
		historyEntry := transitionHistoryEntry{
			From:   currentState,
			To:     newState,
			Reason: reason,
			TS:     now,
		}

		// Extend and cap history at 10
		history := append(current.TransitionHistory, historyEntry)
		if len(history) > transitionHistoryMax {
			history = history[len(history)-transitionHistoryMax:]
		}

		// last_transition uses "timestamp" key
		lastTransition := &transitionEntry{
			From:      currentState,
			To:        newState,
			Reason:    reason,
			Timestamp: now,
		}

		// Auto-update last_dispatch_time when entering DISPATCHING
		lastDispatchTime := current.LastDispatchTime
		if newState == "DISPATCHING" {
			lastDispatchTime = &now
		}

		updated := &leadState{
			Version:          newVersion,
			State:            newState,
			PreviousState:    currentState,
			EnteredAt:        &now,
			ContextPercent:   current.ContextPercent,
			CurrentSprint:    current.CurrentSprint,
			CurrentWave:      current.CurrentWave,
			AgentsDispatched: current.AgentsDispatched,
			AgentsComplete:   current.AgentsComplete,
			PendingCommits:   current.PendingCommits,
			LastDispatchTime: lastDispatchTime,
			LastTransition:   lastTransition,
			TransitionHistory: history,
		}

		if err := writeLeadStateAtomic(updated); err != nil {
			return err
		}

		// Append to transition log
		logEntry := transitionLogEntry{
			TS:      now,
			From:    currentState,
			To:      newState,
			Reason:  reason,
			Version: newVersion,
		}
		if err := appendTransitionLog(logEntry); err != nil {
			// Non-fatal: log the error but don't fail the transition
			fmt.Fprintf(os.Stderr, colorYellow+"warning: transition log append failed: %v"+colorReset+"\n", err)
		}

		if asJSON {
			data, err := json.MarshalIndent(updated, "", "  ")
			if err != nil {
				return fmt.Errorf("marshal state: %w", err)
			}
			fmt.Println(string(data))
		} else {
			fmt.Printf("State: %s -> %s  (v%d)\n", currentState, newState, newVersion)
		}
		return nil
	},
}

func init() {
	stateSetCmd.Flags().String("reason", "", "Human-readable reason for the transition")
	stateSetCmd.Flags().Bool("json", false, "Output result as JSON")
}

// ---------------------------------------------------------------------------
// forge state log [--limit N]
// ---------------------------------------------------------------------------

var stateLogCmd = &cobra.Command{
	Use:   "log",
	Short: "Show recent FSM transitions",
	Long: `Read .forge/heartbeat/transition_log.jsonl and print the most recent entries.

Examples:
  forge state log
  forge state log --limit 20
  forge state log --json`,
	RunE: func(cmd *cobra.Command, args []string) error {
		limit, _ := cmd.Flags().GetInt("limit")
		asJSON, _ := cmd.Flags().GetBool("json")

		entries, err := readTransitionLog(limit)
		if err != nil {
			return err
		}

		if asJSON {
			type logOutput struct {
				Transitions []transitionLogEntry `json:"transitions"`
				Count       int                  `json:"count"`
			}
			out := logOutput{Transitions: entries, Count: len(entries)}
			data, err := json.MarshalIndent(out, "", "  ")
			if err != nil {
				return fmt.Errorf("marshal log: %w", err)
			}
			fmt.Println(string(data))
			return nil
		}

		if len(entries) == 0 {
			fmt.Println("No transitions recorded yet.")
			return nil
		}

		for _, e := range entries {
			version := fmt.Sprintf("v%d", e.Version)
			fmt.Printf("  [%s] %s: %s -> %s  (%s)\n",
				e.TS, version, e.From, e.To, e.Reason)
		}
		return nil
	},
}

func init() {
	stateLogCmd.Flags().IntP("limit", "n", 10, "Number of recent transitions to show")
	stateLogCmd.Flags().Bool("json", false, "Output as JSON")
}

// ---------------------------------------------------------------------------
// forge state context [--pct FLOAT]
// ---------------------------------------------------------------------------

var stateContextCmd = &cobra.Command{
	Use:   "context",
	Short: "Show or write context window usage percentage",
	Long: `Read or write .forge/heartbeat/context_percent.

Without --pct, shows the current value (0 if absent or stale > 300s).
With --pct, writes the value to disk.

Examples:
  forge state context
  forge state context --pct 73`,
	RunE: func(cmd *cobra.Command, args []string) error {
		pctFlag, _ := cmd.Flags().GetFloat64("pct")
		hasPct := cmd.Flags().Changed("pct")
		asJSON, _ := cmd.Flags().GetBool("json")

		path := stateFilePath(contextPercentSidecar)

		if hasPct {
			if pctFlag < 0 || pctFlag > 100 {
				return fmt.Errorf("--pct must be between 0 and 100, got %.1f", pctFlag)
			}
			if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
				return fmt.Errorf("mkdir context dir: %w", err)
			}
			val := fmt.Sprintf("%d", int(pctFlag))
			if err := os.WriteFile(path, []byte(val+"\n"), 0o644); err != nil {
				return fmt.Errorf("write context_percent: %w", err)
			}
			if asJSON {
				fmt.Printf(`{"context_percent":%d}`+"\n", int(pctFlag))
			} else {
				fmt.Printf("context_percent set to %d\n", int(pctFlag))
			}
			return nil
		}

		// Read mode
		pct := readHeartbeatContextPercent(path)

		if asJSON {
			fmt.Printf(`{"context_percent":%d}`+"\n", pct)
		} else {
			fmt.Println(pct)
		}
		return nil
	},
}

// readHeartbeatContextPercent reads the context_percent sidecar file.
// Returns 0 when absent, stale (>300s), or invalid — mirrors Python check_context_threshold.
func readHeartbeatContextPercent(path string) int {
	info, err := os.Stat(path)
	if err != nil {
		return 0
	}
	// Staleness check
	if time.Since(info.ModTime()).Seconds() > contextStaleSeconds {
		return 0
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return 0
	}
	raw := strings.TrimSpace(string(data))
	var val int
	if _, err := fmt.Sscanf(raw, "%d", &val); err != nil {
		return 0
	}
	if val < 0 || val > 100 {
		return 0
	}
	return val
}

func init() {
	stateContextCmd.Flags().Float64("pct", 0, "Write context percentage to disk (0-100)")
	stateContextCmd.Flags().Bool("json", false, "Output as JSON")
}

// ---------------------------------------------------------------------------
// forge state nudge [--clear]
// ---------------------------------------------------------------------------

var stateNudgeCmd = &cobra.Command{
	Use:   "nudge",
	Short: "Show or clear the lead nudge file (anti-thrash mechanism)",
	Long: `Read or clear .forge/heartbeat/lead_nudge.json.

The nudge file is an anti-thrash guard: it prevents re-dispatching when the
cooldown (default 120s) has not yet elapsed since the last dispatch.

Without --clear, displays the nudge file contents and whether cooldown is active.
With --clear, removes the nudge file.

Examples:
  forge state nudge
  forge state nudge --clear`,
	RunE: func(cmd *cobra.Command, args []string) error {
		clear, _ := cmd.Flags().GetBool("clear")
		asJSON, _ := cmd.Flags().GetBool("json")

		path := stateFilePath(nudgeFile)

		if clear {
			if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
				return fmt.Errorf("remove nudge file: %w", err)
			}
			if asJSON {
				fmt.Println(`{"cleared":true}`)
			} else {
				fmt.Println("Nudge file cleared.")
			}
			return nil
		}

		// Read nudge
		data, err := os.ReadFile(path)
		if os.IsNotExist(err) {
			if asJSON {
				fmt.Println(`{"nudge":null,"exists":false}`)
			} else {
				fmt.Println("No nudge file found.")
			}
			return nil
		}
		if err != nil {
			return fmt.Errorf("read nudge file: %w", err)
		}

		// Check anti-thrash cooldown using lead_state.json last_dispatch_time
		s := readLeadState()
		cooldownActive := false
		var elapsed float64
		if s.LastDispatchTime != nil && *s.LastDispatchTime != "" {
			t, err := time.Parse("2006-01-02T15:04:05Z", *s.LastDispatchTime)
			if err == nil {
				elapsed = time.Since(t).Seconds()
				cooldownActive = elapsed < antiThrashCooldownSeconds
			}
		}

		if asJSON {
			var nudgeData interface{}
			_ = json.Unmarshal(data, &nudgeData)
			out := map[string]interface{}{
				"nudge":          nudgeData,
				"exists":         true,
				"cooldown_active": cooldownActive,
				"elapsed_seconds": int(elapsed),
			}
			outData, _ := json.MarshalIndent(out, "", "  ")
			fmt.Println(string(outData))
		} else {
			fmt.Printf("Nudge file: %s\n", path)
			fmt.Printf("Contents:   %s\n", strings.TrimSpace(string(data)))
			if cooldownActive {
				fmt.Printf("Cooldown:   %sACTIVE%s (%.0fs elapsed, need %ds)\n",
					colorYellow, colorReset, elapsed, antiThrashCooldownSeconds)
			} else {
				fmt.Printf("Cooldown:   %sOK%s — safe to dispatch\n", colorGreen, colorReset)
			}
		}
		return nil
	},
}

func init() {
	stateNudgeCmd.Flags().Bool("clear", false, "Remove the nudge file")
	stateNudgeCmd.Flags().Bool("json", false, "Output as JSON")
}

// ---------------------------------------------------------------------------
// forge state escalate [--clear]
// ---------------------------------------------------------------------------

var stateEscalateCmd = &cobra.Command{
	Use:   "escalate",
	Short: "Show or set the HUMAN_ATTENTION_REQUIRED escalation marker",
	Long: `Show or set .forge/heartbeat/HUMAN_ATTENTION_REQUIRED.

Without flags, shows whether the escalation marker exists and its contents.
Without --clear, sets the marker (appends a timestamped line).
With --clear, removes the marker file.

Examples:
  forge state escalate
  forge state escalate --message "All agents circuit-broken"
  forge state escalate --clear`,
	RunE: func(cmd *cobra.Command, args []string) error {
		clear, _ := cmd.Flags().GetBool("clear")
		message, _ := cmd.Flags().GetString("message")
		asJSON, _ := cmd.Flags().GetBool("json")

		path := stateFilePath(attentionFile)

		if clear {
			if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
				return fmt.Errorf("remove attention file: %w", err)
			}
			if asJSON {
				fmt.Println(`{"cleared":true}`)
			} else {
				fmt.Println("Escalation marker cleared.")
			}
			return nil
		}

		if message != "" {
			// Append timestamped escalation message
			if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
				return fmt.Errorf("mkdir attention dir: %w", err)
			}
			ts := time.Now().UTC().Format("2006-01-02T15:04:05Z")
			line := fmt.Sprintf("%s: %s\n", ts, message)

			f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
			if err != nil {
				return fmt.Errorf("open attention file: %w", err)
			}
			defer f.Close()
			if _, err := f.WriteString(line); err != nil {
				return fmt.Errorf("write attention file: %w", err)
			}

			if asJSON {
				fmt.Printf(`{"written":true,"message":%q}`+"\n", message)
			} else {
				fmt.Printf("Attention written: %s\n", message)
			}
			return nil
		}

		// Read / show escalation status
		_, err := os.Stat(path)
		exists := err == nil

		if asJSON {
			if !exists {
				fmt.Println(`{"human_attention_required":false,"messages":[]}`)
				return nil
			}
			data, _ := os.ReadFile(path)
			lines := strings.Split(strings.TrimSpace(string(data)), "\n")
			var msgs []string
			for _, l := range lines {
				if strings.TrimSpace(l) != "" {
					msgs = append(msgs, l)
				}
			}
			if msgs == nil {
				msgs = []string{}
			}
			out := map[string]interface{}{
				"human_attention_required": true,
				"messages":                 msgs,
			}
			outData, _ := json.MarshalIndent(out, "", "  ")
			fmt.Println(string(outData))
			return nil
		}

		if !exists {
			fmt.Printf("Human attention required: %sNO%s\n", colorGreen, colorReset)
			return nil
		}

		data, err := os.ReadFile(path)
		if err != nil {
			return fmt.Errorf("read attention file: %w", err)
		}
		fmt.Printf("Human attention required: %sYES%s\n", colorRed, colorReset)
		fmt.Printf("Marker file: %s\n", path)
		lines := strings.Split(strings.TrimSpace(string(data)), "\n")
		for _, l := range lines {
			if strings.TrimSpace(l) != "" {
				fmt.Printf("  %s\n", l)
			}
		}
		return nil
	},
}

func init() {
	stateEscalateCmd.Flags().Bool("clear", false, "Remove the HUMAN_ATTENTION_REQUIRED marker")
	stateEscalateCmd.Flags().String("message", "", "Escalation message to append (sets the marker)")
	stateEscalateCmd.Flags().Bool("json", false, "Output as JSON")
}
