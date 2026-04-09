//go:build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
)

// taskLogsCmd: forge task logs [task-id]
//
// Shows granular event logs for a specific task, or recent events across all
// tasks when --recent is used. Designed for debugging dispatch issues.
var taskLogsCmd = &cobra.Command{
	Use:   "logs [task-id]",
	Short: "Show task state transition history",
	Long: `Display the state transition log for a task.

Shows when the task moved between states (QUEUED → DISPATCHED → RUNNING → etc),
who claimed it, and any error/failure context.

When called with a task ID, fetches granular events from the daemon event log
and renders them as a chronological timeline. If no events are stored, falls
back to a synthesised timeline built from the task's created_at / started_at /
updated_at timestamps plus its current state and failure_context.

Examples:
  forge task logs TASK-ABC              # Full event history for one task
  forge task logs TASK-ABC --format json  # Machine-readable JSON output
  forge task logs --recent 20           # Last 20 events across all tasks
  forge task logs --recent 10 --agent kimi  # Filter recent events by agent`,
	Args: cobra.MaximumNArgs(1),
	RunE: runTaskLogs,
}

func init() {
	taskLogsCmd.Flags().Int("recent", 0, "Show last N events across all tasks (no task-id required)")
	taskLogsCmd.Flags().String("agent", "", "Filter events/tasks by agent name")
	taskLogsCmd.Flags().String("format", "table", "Output format: table or json")
}

// taskEvent mirrors the daemon TaskEvent struct for JSON decoding.
type taskEvent struct {
	ID        int64           `json:"id"`
	TaskID    string          `json:"task_id"`
	Domain    string          `json:"domain"`
	Project   string          `json:"project"`
	EventType string          `json:"event_type"`
	Payload   json.RawMessage `json:"payload"`
	CreatedAt time.Time       `json:"created_at"`
}

// taskLogsResponse is the envelope returned by /cli/task/logs.
type taskLogsResponse struct {
	TaskID string      `json:"task_id"`
	Count  int         `json:"count"`
	Events []taskEvent `json:"events"`
}

// taskWithTiming extends internal.Task with the extra fields returned by
// GET /api/tasks/{id} that are not in the shared type.
type taskWithTiming struct {
	internal.Task
	StartedAt      *string `json:"started_at,omitempty"`
	Result         string  `json:"result,omitempty"`
	FailureContext string  `json:"failure_context,omitempty"`
}

func runTaskLogs(cmd *cobra.Command, args []string) error {
	recent, _ := cmd.Flags().GetInt("recent")
	agent, _ := cmd.Flags().GetString("agent")
	format, _ := cmd.Flags().GetString("format")

	// --recent mode: show last N events across tasks
	if recent > 0 || (len(args) == 0 && agent != "") {
		return runRecentLogs(cmd, recent, agent, format)
	}

	if len(args) == 0 {
		return fmt.Errorf("task-id is required\n  Usage: forge task logs TASK-ID\n  Or use --recent N to show recent events across all tasks")
	}
	return runTaskEventLogs(args[0], agent, format)
}

// runTaskEventLogs fetches and displays event logs for a single task.
func runTaskEventLogs(taskID, agent, format string) error {
	client := internal.NewClient()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	resp, err := client.Get(ctx, "/cli/task/logs?id="+taskID)
	if err != nil {
		return fmt.Errorf("failed to fetch task logs: %w\n  Recovery: run 'forge daemon status' to verify the daemon is reachable", err)
	}
	defer resp.Body.Close()

	body, readErr := io.ReadAll(resp.Body)
	if readErr != nil {
		return fmt.Errorf("failed to read response: %w", readErr)
	}

	switch resp.StatusCode {
	case http.StatusBadRequest:
		return fmt.Errorf("bad request: task ID required\n  Usage: forge task logs TASK-ID")
	case http.StatusNotFound:
		return fmt.Errorf("task %s not found\n  Check the task ID with: forge task list", taskID)
	case http.StatusOK:
		// handled below
	default:
		return fmt.Errorf("daemon returned HTTP %d: %s\n  Recovery: run 'forge daemon status'", resp.StatusCode, strings.TrimSpace(string(body)))
	}

	if format == "json" {
		fmt.Println(string(body))
		return nil
	}

	var result taskLogsResponse
	if err := json.Unmarshal(body, &result); err != nil {
		// Daemon returned unexpected format; fall back to raw output with a
		// clear warning so the operator knows what happened.
		fmt.Fprintf(os.Stderr, "[warn] unexpected response format — showing raw output: %v\n", err)
		fmt.Println(string(body))
		return nil
	}

	// If no events are stored, synthesise a timeline from task metadata.
	if len(result.Events) == 0 {
		return runFallbackTimeline(taskID, format)
	}

	// Apply optional agent filter over the payload field.
	events := filterEventsByAgent(result.Events, agent)
	if len(events) == 0 {
		fmt.Printf("No events for task %s", taskID)
		if agent != "" {
			fmt.Printf(" (filtered by agent %q)", agent)
		}
		fmt.Println()
		fmt.Printf("  Use 'forge task history %s' for state transitions\n", taskID)
		return nil
	}

	printEventTimeline(taskID, events)
	return nil
}

// runFallbackTimeline fetches the task itself and renders a synthesised
// timeline from its timestamp + state fields, including failure_context.
func runFallbackTimeline(taskID, format string) error {
	client := internal.NewClient()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	resp, err := client.Get(ctx, "/api/tasks/"+taskID)
	if err != nil {
		return fmt.Errorf("failed to fetch task details: %w\n  Recovery: run 'forge daemon status'", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode == http.StatusNotFound {
		return fmt.Errorf("task %s not found\n  Check the task ID with: forge task list", taskID)
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("daemon returned HTTP %d\n  Recovery: run 'forge daemon status'", resp.StatusCode)
	}

	var t taskWithTiming
	if err := json.Unmarshal(body, &t); err != nil {
		return fmt.Errorf("failed to parse task response: %w", err)
	}

	if format == "json" {
		type fallbackEntry struct {
			Timestamp string `json:"timestamp"`
			Event     string `json:"event"`
			Detail    string `json:"detail,omitempty"`
		}
		var entries []fallbackEntry
		if t.CreatedAt != "" {
			entries = append(entries, fallbackEntry{Timestamp: t.CreatedAt, Event: "created", Detail: "task queued"})
		}
		if t.StartedAt != nil && *t.StartedAt != "" {
			entries = append(entries, fallbackEntry{Timestamp: *t.StartedAt, Event: "started", Detail: "agent claimed task"})
		}
		if t.UpdatedAt != "" && t.UpdatedAt != t.CreatedAt {
			detail := fmt.Sprintf("state=%s status=%s", t.State, t.Status)
			entries = append(entries, fallbackEntry{Timestamp: t.UpdatedAt, Event: "updated", Detail: detail})
		}
		if t.FailureContext != "" {
			entries = append(entries, fallbackEntry{Timestamp: t.UpdatedAt, Event: "failure", Detail: parseFailureContext(t.FailureContext)})
		}
		out := map[string]interface{}{
			"task_id":  taskID,
			"fallback": true,
			"timeline": entries,
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(out)
	}

	fmt.Printf("Task: %s  (no event log — showing synthesised timeline)\n\n", taskID)
	fmt.Printf("%-28s %-14s %s\n", "TIMESTAMP", "EVENT", "DETAIL")
	fmt.Println(strings.Repeat("-", 72))

	if t.CreatedAt != "" {
		printTimelineRow(t.CreatedAt, "created", "task queued")
	}
	if t.StartedAt != nil && *t.StartedAt != "" {
		printTimelineRow(*t.StartedAt, "started", "agent claimed task")
	}
	if t.UpdatedAt != "" && t.UpdatedAt != t.CreatedAt {
		detail := fmt.Sprintf("state=%-10s  status=%s", t.State, t.Status)
		printTimelineRow(t.UpdatedAt, "updated", detail)
	}
	if t.FailureContext != "" {
		fmt.Println()
		fmt.Println("FAILURE CONTEXT:")
		fmt.Println(strings.Repeat("-", 72))
		printFailureContext(t.FailureContext)
	}
	if t.Result != "" {
		fmt.Println()
		fmt.Printf("RESULT:\n  %s\n", strings.TrimSpace(t.Result))
	}
	return nil
}

// runRecentLogs shows last N task events by listing tasks and their states.
func runRecentLogs(cmd *cobra.Command, recent int, agent, format string) error {
	if recent <= 0 {
		recent = 20
	}

	client := internal.NewClient()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	result, err := client.ListTasks(ctx, "", recent, "")
	if err != nil {
		return fmt.Errorf("failed to list tasks: %w\n  Recovery: run 'forge daemon status'", err)
	}

	tasks := result.Tasks

	// Apply agent filter if requested.
	if agent != "" {
		var filtered []internal.Task
		for _, t := range tasks {
			if t.AssignedTo == agent || t.AssignedAgent != nil && *t.AssignedAgent == agent {
				filtered = append(filtered, t)
			}
		}
		tasks = filtered
	}

	if len(tasks) == 0 {
		msg := fmt.Sprintf("No tasks found")
		if agent != "" {
			msg += fmt.Sprintf(" for agent %q", agent)
		}
		fmt.Println(msg)
		fmt.Println("  Use 'forge task list --all' to see all tasks")
		return nil
	}

	if format == "json" {
		formatter := internal.NewFormatter(format, nil)
		return formatter.WriteJSON(map[string]interface{}{
			"recent": recent,
			"agent":  agent,
			"tasks":  tasks,
		})
	}

	header := fmt.Sprintf("Recent tasks (last %d)", recent)
	if agent != "" {
		header += fmt.Sprintf(" — agent: %s", agent)
	}
	fmt.Println(header)
	fmt.Println()
	fmt.Printf("%-26s %-12s %-12s %-22s %s\n", "TASK-ID", "STATE", "STATUS", "UPDATED", "TITLE")
	fmt.Println(strings.Repeat("-", 100))
	for _, t := range tasks {
		state := t.State
		if state == "" {
			state = "-"
		}
		updated := t.UpdatedAt
		if updated == "" {
			updated = t.CreatedAt
		}
		title := t.DisplayTitle()
		if len(title) > 40 {
			title = title[:37] + "..."
		}
		fmt.Printf("%-26s %-12s %-12s %-22s %s\n", t.ID, state, t.Status, updated, title)
	}
	return nil
}

// printEventTimeline renders a sorted list of events as a timeline table.
func printEventTimeline(taskID string, events []taskEvent) {
	fmt.Printf("Task: %s\n\n", taskID)
	fmt.Printf("%-28s %-22s %-18s %s\n", "TIMESTAMP", "EVENT", "DOMAIN/PROJECT", "PAYLOAD")
	fmt.Println(strings.Repeat("-", 90))
	for _, e := range events {
		ts := e.CreatedAt.UTC().Format("2006-01-02T15:04:05Z")
		scope := e.Domain
		if e.Project != "" {
			scope = e.Domain + "/" + e.Project
		}
		if len(scope) > 18 {
			scope = scope[:15] + "..."
		}
		payload := summarisePayload(e.Payload)
		if len(payload) > 50 {
			payload = payload[:47] + "..."
		}
		fmt.Printf("%-28s %-22s %-18s %s\n", ts, e.EventType, scope, payload)
	}
}

// printTimelineRow formats a single synthesised timeline row.
func printTimelineRow(ts, event, detail string) {
	// Normalise timestamp display — if it's RFC3339 already, just truncate.
	normalised := ts
	if len(ts) > 26 {
		normalised = ts[:26]
	}
	fmt.Printf("%-28s %-14s %s\n", normalised, event, detail)
}

// filterEventsByAgent returns only events whose payload mentions the agent,
// or all events when agent is empty.
func filterEventsByAgent(events []taskEvent, agent string) []taskEvent {
	if agent == "" {
		return events
	}
	var out []taskEvent
	for _, e := range events {
		if strings.Contains(strings.ToLower(string(e.Payload)), strings.ToLower(agent)) {
			out = append(out, e)
		}
	}
	return out
}

// summarisePayload extracts a short human-readable summary from a JSON payload.
// Falls back to the raw bytes when the payload is not a JSON object.
func summarisePayload(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var m map[string]interface{}
	if err := json.Unmarshal(raw, &m); err != nil {
		// Not an object — return truncated raw string
		s := string(raw)
		s = strings.Trim(s, `"`)
		return s
	}
	// Prefer common keys in priority order
	for _, key := range []string{"message", "msg", "reason", "error", "agent_id", "status", "state"} {
		if v, ok := m[key]; ok {
			return fmt.Sprintf("%s=%v", key, v)
		}
	}
	// Fallback: first key=value pair
	for k, v := range m {
		return fmt.Sprintf("%s=%v", k, v)
	}
	return string(raw)
}

// parseFailureContext returns a cleaned single-line version of failure_context
// for use in JSON timeline entries.
func parseFailureContext(fc string) string {
	// Attempt to decode as JSON for a structured summary.
	var m map[string]interface{}
	if err := json.Unmarshal([]byte(fc), &m); err == nil {
		parts := make([]string, 0, len(m))
		for k, v := range m {
			parts = append(parts, fmt.Sprintf("%s=%v", k, v))
		}
		return strings.Join(parts, "; ")
	}
	// Plain text — return as-is (trimmed)
	return strings.TrimSpace(fc)
}

// printFailureContext pretty-prints failure_context, handling both JSON and
// plain-text forms.
func printFailureContext(fc string) {
	var m map[string]interface{}
	if err := json.Unmarshal([]byte(fc), &m); err == nil {
		for k, v := range m {
			fmt.Printf("  %-20s %v\n", k+":", v)
		}
		return
	}
	// Plain text: indent each line
	for _, line := range strings.Split(strings.TrimSpace(fc), "\n") {
		fmt.Printf("  %s\n", line)
	}
}
