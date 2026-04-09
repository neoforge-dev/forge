package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/neoforge-dev/forge/internal"
)

// ANSI color codes for work_state column.
const (
	ansiGreen  = "\033[32m"
	ansiYellow = "\033[33m"
	ansiRed    = "\033[31m"
	ansiReset  = "\033[0m"
)

// workStateOrder maps work_state strings to sort priority.
// Lower value = shown first. blocked(0) > working(1) > idle(2) > other(3).
var workStateOrder = map[string]int{
	"blocked": 0,
	"working": 1,
	"idle":    2,
}

// workStateRank returns the sort priority for a work_state value.
func workStateRank(ws string) int {
	if r, ok := workStateOrder[ws]; ok {
		return r
	}
	return 3
}

// colorWorkState wraps a work_state string in the appropriate ANSI color.
// Respects the NO_COLOR environment variable.
func colorWorkState(ws string) string {
	if os.Getenv("NO_COLOR") != "" {
		return ws
	}
	switch ws {
	case "idle":
		return ansiGreen + ws + ansiReset
	case "working":
		return ansiYellow + ws + ansiReset
	case "blocked":
		return ansiRed + ws + ansiReset
	default:
		return ws
	}
}

// deriveWorkState derives the agent's work_state from its status and current_task_id.
//
// Derivation rules:
//   - online + has task  → "working"
//   - online + no task   → "idle"
//   - offline/stale/etc  → "blocked"
func deriveWorkState(a agentHealthEntry) string {
	isOnline := a.Status == "online"
	hasTask := strings.TrimSpace(a.CurrentTaskID) != ""
	switch {
	case isOnline && hasTask:
		return "working"
	case isOnline && !hasTask:
		return "idle"
	default:
		return "blocked"
	}
}

// agentStatusEntry is the enriched view combining agentHealthEntry with work_state.
type agentStatusEntry struct {
	agentHealthEntry
	WorkState string `json:"work_state"`
}

// printAgentStatusTable prints a tabular view with color-coded work_state.
func printAgentStatusTable(entries []agentStatusEntry) error {
	if len(entries) == 0 {
		fmt.Printf("No agents registered.\n")
		fmt.Printf("  Start one with: forge worker up --id %s\n", getLocalAgentID())
		return nil
	}

	w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)

	// Header without color codes so column widths align correctly.
	fmt.Fprintln(w, "AGENT_ID\tNODE\tSTATUS\tWORK_STATE\tCURRENT_TASK\tCTX%\tLAST_SEEN")

	for _, e := range entries {
		taskID := "-"
		if e.CurrentTaskID != "" {
			taskID = truncStr(e.CurrentTaskID, 24)
		}
		lastSeen := "-"
		if !e.LastSeen.IsZero() {
			lastSeen = formatAgentLastSeen(e.LastSeen)
		}

		// Color the agent ID by model prefix and node by node name.
		agentLabel := internal.ColorModel(e.AgentID, truncStr(e.AgentID, 24))
		nodeLabel := internal.ColorNode(e.Node, truncStr(e.Node, 12))
		wsLabel := colorWorkState(e.WorkState)

		fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\t%.1f%%\t%s\n",
			agentLabel,
			nodeLabel,
			truncStr(e.Status, 10),
			wsLabel,
			taskID,
			e.ContextPct,
			lastSeen,
		)
	}

	if err := w.Flush(); err != nil {
		return err
	}

	// Summary counts.
	var blocked, working, idle int
	for _, e := range entries {
		switch e.WorkState {
		case "blocked":
			blocked++
		case "working":
			working++
		case "idle":
			idle++
		}
	}
	fmt.Printf("\nTotal: %d agents  [idle:%d  working:%d  blocked:%d]\n",
		len(entries), idle, working, blocked)

	return nil
}

// printAgentStatusJSON emits a JSON object with enriched agent status entries.
func printAgentStatusJSON(entries []agentStatusEntry) error {
	type jsonEntry struct {
		AgentID       string    `json:"agent_id"`
		Node          string    `json:"node"`
		Status        string    `json:"status"`
		WorkState     string    `json:"work_state"`
		CurrentTaskID string    `json:"current_task_id"`
		ContextPct    float64   `json:"context_pct"`
		LastSeen      time.Time `json:"last_seen"`
	}

	out := make([]jsonEntry, 0, len(entries))
	for _, e := range entries {
		out = append(out, jsonEntry{
			AgentID:       e.AgentID,
			Node:          e.Node,
			Status:        e.Status,
			WorkState:     e.WorkState,
			CurrentTaskID: e.CurrentTaskID,
			ContextPct:    e.ContextPct,
			LastSeen:      e.LastSeen,
		})
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(map[string]interface{}{
		"agents": out,
		"count":  len(out),
	})
}

// formatAgentLastSeen formats a last_seen time as a human-friendly relative string.
func formatAgentLastSeen(t time.Time) string {
	if t.IsZero() {
		return "-"
	}
	d := time.Since(t).Round(time.Second)
	switch {
	case d < time.Minute:
		return fmt.Sprintf("%ds ago", int(d.Seconds()))
	case d < time.Hour:
		return fmt.Sprintf("%dm ago", int(d.Minutes()))
	case d < 24*time.Hour:
		return fmt.Sprintf("%dh ago", int(d.Hours()))
	default:
		return t.Format("2006-01-02")
	}
}

// truncStr clips s to maxLen characters and appends "…" if truncated.
// Used for display in the status table to prevent column overflow.
func truncStr(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen-1] + "…"
}
