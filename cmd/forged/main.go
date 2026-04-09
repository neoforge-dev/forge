//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/neoforge-dev/forge/cmd/forged/middleware"
	"github.com/spf13/cobra"
	// "github.com/neoforge-dev/forge/cmd/forged/db"
)

// Build-time version metadata — injected via ldflags:
//   -X main.Version=v1.2.3 -X main.GitCommit=abc1234 -X main.BuildTime=2026-03-20T00:00:00Z
var (
	Version   = "dev"
	GitCommit = "unknown"
	BuildTime = "unknown"
)

var hub *Hub
var taskQueue TaskQueue
var planManager PlanManager
var contextManager *ContextManager
var stateMachine *StateMachine
var taskStore *TaskStore // ADR-028: for FSM-aware claim operations
var leaseManager LeaseManager
var globalLaneManager LaneManager
var globalApprovalService *ApprovalService
var globalPatrolSystem *PatrolSystem
var authManager *AuthManager
var worktreeMgr *WorktreeManager

// rootCmd is the root cobra command
var rootCmd = &cobra.Command{
	Use:   "forged",
	Short: "FORGE v3 - Fleet Operations and Resource Governance Engine",
	Long: `FORGE v3 is a distributed task orchestration and agent management system.

It provides CLI commands for managing tasks, agents, nodes, and git operations,
as well as HTTP APIs for integration with external systems.`,
	Run: func(cmd *cobra.Command, args []string) {
		// If no subcommand specified, fall through to legacy main behavior
		// This is handled in main() before rootCmd.Execute() is called
	},
}

// init initializes cobra commands
func init() {
	registerCommands()
}

type Notification struct {
	ID        string    `json:"id"`
	Type      string    `json:"type"`
	Title     string    `json:"title"`
	Message   string    `json:"message"`
	Read      bool      `json:"read"`
	CreatedAt time.Time `json:"created_at"`
}

var (
	notifications  = make(map[string]Notification)
	notificationID = 0
	notifMu        sync.RWMutex
)

func splitLines(s string) []string {
	var lines []string
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			lines = append(lines, s[start:i])
			start = i + 1
		}
	}
	if start < len(s) {
		lines = append(lines, s[start:])
	}
	return lines
}

type AgentContextResponse struct {
	AgentID     string  `json:"agent_id"`
	ContextPct  float64 `json:"context_pct"`
	LastUpdated string  `json:"last_updated"`
}

func findDomainForAgent(agentID string) (string, error) {
	contextDir := filepath.Join(forgeRoot(), ".forge", "context")
	entries, err := os.ReadDir(contextDir)
	if err != nil {
		return "", err
	}

	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		domain := e.Name()
		leadContextPath := filepath.Join(contextDir, domain, "lead-context.md")
		data, err := os.ReadFile(leadContextPath)
		if err != nil {
			continue
		}

		// Look for **Lead:** kimi or similar
		if containsIgnoreCase(string(data), "**Lead:** "+agentID) ||
			containsIgnoreCase(string(data), "**Lead:** "+agentID+" (") {
			return domain, nil
		}
	}

	// Fallback: if agentID is a domain name
	for _, e := range entries {
		if e.IsDir() && e.Name() == agentID {
			return agentID, nil
		}
	}

	return "", os.ErrNotExist
}

func containsIgnoreCase(s, substr string) bool {
	return strings.Contains(strings.ToLower(s), strings.ToLower(substr))
}

func parseContextFromDomain(domain string) (float64, time.Time, error) {
	contextDir := filepath.Join(forgeRoot(), ".forge", "context", domain)

	// Try context_pct file first
	pctPath := filepath.Join(contextDir, "context_pct")
	if data, err := os.ReadFile(pctPath); err == nil {
		if pct, err := parsePct(string(data)); err == nil {
			info, _ := os.Stat(pctPath)
			return pct, info.ModTime(), nil
		}
	}

	// Try lead-context.md for "- Context: 45%"
	mdPath := filepath.Join(contextDir, "lead-context.md")
	if data, err := os.ReadFile(mdPath); err == nil {
		if pct, err := parseMarkdownPct(string(data)); err == nil {
			info, _ := os.Stat(mdPath)
			return pct, info.ModTime(), nil
		}
	}

	// Default
	info, err := os.Stat(contextDir)
	if err != nil {
		return 0.0, time.Time{}, err
	}
	return 0.0, info.ModTime(), nil
}

func parsePct(s string) (float64, error) {
	var pct float64
	_, err := fmt.Sscanf(strings.TrimSpace(s), "%f", &pct)
	return pct, err
}

func parseMarkdownPct(content string) (float64, error) {
	// Look for "- Context: 45%"
	lines := splitLines(content)
	for _, line := range lines {
		if strings.Contains(line, "- Context:") {
			var pct float64
			parts := strings.Split(line, ":")
			if len(parts) > 1 {
				s := strings.TrimSpace(parts[1])
				s = strings.TrimSuffix(s, "%")
				if _, err := fmt.Sscanf(s, "%f", &pct); err == nil {
					return pct, nil
				}
			}
		}
	}
	return 0, os.ErrNotExist
}

// tasksHandler handles both listing (GET) and creating (POST) tasks
func tasksHandler(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		listTasksHandler(w, r)
	case http.MethodPost:
		createTaskHandler(w, r)
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

// UpdateAgentHeartbeat updates agent status in database
func UpdateAgentHeartbeat(agentID, node, status, taskID string, contextPct float64) error {
	_, err := getDBConn().Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, current_task_id, context_pct, last_seen)
		VALUES (?, ?, ?, ?, ?, datetime('now'))
		ON CONFLICT(agent_id) DO UPDATE SET
			node = excluded.node,
			status = excluded.status,
			current_task_id = excluded.current_task_id,
			context_pct = excluded.context_pct,
			last_seen = datetime('now')
	`, agentID, node, status, taskID, contextPct)
	return err
}

// UpdateAgentWorkState sets the work_state column for a single agent.
// workState must be one of: "idle", "working", "blocked".
// Invalid values are silently coerced to "idle".
func UpdateAgentWorkState(agentID, workState string) error {
	if workState != "idle" && workState != "working" && workState != "blocked" {
		workState = "idle"
	}
	db := getDBConn()
	if db == nil {
		return fmt.Errorf("database connection not initialised")
	}
	_, err := db.Exec(`
		UPDATE agent_heartbeats SET work_state = ? WHERE agent_id = ?
	`, workState, agentID)
	return err
}

// UpdateAgentHeartbeatConditional upserts an agent heartbeat row but only updates
// context_pct when the caller explicitly provides a non-nil value.  Passing nil
// preserves the existing context_pct in the database, preventing cron-based
// heartbeats (which omit the field) from resetting the value to 0.
// workState must be "idle", "working", or "blocked"; empty string preserves existing value.
// S188 fix: heartbeats that don't send work_state no longer reset "working" → "idle".
func UpdateAgentHeartbeatConditional(agentID, node, status, taskID, workState string, contextPct *float64) error {
	preserveWorkState := workState == ""
	if workState == "" {
		workState = "idle" // only for INSERT (new agents); UPDATE preserves existing
	}

	if preserveWorkState {
		// Agent didn't send work_state — preserve whatever dispatcher set.
		if contextPct != nil {
			_, err := getDBConn().Exec(`
				INSERT INTO agent_heartbeats (agent_id, node, status, work_state, current_task_id, context_pct, last_seen)
				VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
				ON CONFLICT(agent_id) DO UPDATE SET
					node = excluded.node, status = excluded.status,
					current_task_id = excluded.current_task_id,
					context_pct = excluded.context_pct,
					last_seen = datetime('now')
			`, agentID, node, status, workState, taskID, *contextPct)
			return err
		}
		_, err := getDBConn().Exec(`
			INSERT INTO agent_heartbeats (agent_id, node, status, work_state, current_task_id, context_pct, last_seen)
			VALUES (?, ?, ?, ?, ?, 0, datetime('now'))
			ON CONFLICT(agent_id) DO UPDATE SET
				node = excluded.node, status = excluded.status,
				current_task_id = excluded.current_task_id,
				last_seen = datetime('now')
		`, agentID, node, status, workState, taskID)
		return err
	}

	// Agent explicitly sent work_state — use it.
	if contextPct != nil {
		_, err := getDBConn().Exec(`
			INSERT INTO agent_heartbeats (agent_id, node, status, work_state, current_task_id, context_pct, last_seen)
			VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
			ON CONFLICT(agent_id) DO UPDATE SET
				node = excluded.node, status = excluded.status,
				work_state = excluded.work_state,
				current_task_id = excluded.current_task_id,
				context_pct = excluded.context_pct,
				last_seen = datetime('now')
		`, agentID, node, status, workState, taskID, *contextPct)
		return err
	}
	_, err := getDBConn().Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, work_state, current_task_id, context_pct, last_seen)
		VALUES (?, ?, ?, ?, ?, 0, datetime('now'))
		ON CONFLICT(agent_id) DO UPDATE SET
			node = excluded.node, status = excluded.status,
			work_state = excluded.work_state,
			current_task_id = excluded.current_task_id,
			last_seen = datetime('now')
	`, agentID, node, status, workState, taskID)
	return err
}

// CLIRouter sets up noun-verb HTTP endpoints for the CLI bridge.
// These endpoints are consumed by the v2 CLI to execute commands like
// `forge task list`, `forge system health`, etc. Handlers are thin
// wrappers around existing API functionality and use the shared
// output and error helpers.
func CLIRouter(mux *http.ServeMux) {
	// Task commands
	mux.HandleFunc("/cli/task/create", handleTaskCreate)
	mux.HandleFunc("/cli/task/list", handleTaskList)
	mux.HandleFunc("/cli/task/show", handleTaskShow)
	mux.HandleFunc("/cli/task/logs", handleTaskLogs)
	// Agent commands
	mux.HandleFunc("/cli/agent/list", handleAgentList)
	mux.HandleFunc("/cli/agent/status", handleAgentStatus)

	// System commands
	mux.HandleFunc("/cli/system/health", handleSystemHealth)

	// Queue commands
	mux.HandleFunc("/cli/queue/depth", handleQueueDepth)
	mux.HandleFunc("/cli/queue/status", handleQueueStatus)
	mux.HandleFunc("/cli/queue/list", handleQueueList)
	mux.HandleFunc("/cli/queue/priority", handleQueuePriority)
	mux.HandleFunc("/cli/queue/cancel", handleQueueCancel)
}

// --- CLI Task Handlers ---

func handleTaskCreate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var wrapper struct {
		Format string `json:"format"`
		Task   Task   `json:"task"`
	}
	if err := json.NewDecoder(r.Body).Decode(&wrapper); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	// Reuse existing createTaskHandler logic by constructing a synthetic request.
	body, _ := json.Marshal(wrapper.Task)
	req, _ := http.NewRequest(http.MethodPost, "/api/tasks", strings.NewReader(string(body)))
	rr := &responseRecorder{header: http.Header{}}
	createTaskHandler(rr, req)

	if rr.status >= 400 {
		w.WriteHeader(rr.status)
		w.Write(rr.body)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.Write(rr.body)
}

func handleTaskList(w http.ResponseWriter, r *http.Request) {
	// Delegate directly to listTasksHandler, but honor format flag if present.
	format := r.URL.Query().Get("format")
	rr := &responseRecorder{header: http.Header{}}
	listTasksHandler(rr, r)

	if rr.status >= 400 {
		w.WriteHeader(rr.status)
		w.Write(rr.body)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := WriteOutputWithFlag(w, format, json.RawMessage(rr.body)); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func handleTaskShow(w http.ResponseWriter, r *http.Request) {
	taskID := r.URL.Query().Get("id")
	if taskID == "" {
		http.Error(w, "task id required", http.StatusBadRequest)
		return
	}
	format := r.URL.Query().Get("format")

	req, _ := http.NewRequest(http.MethodGet, "/api/tasks/"+taskID, nil)
	rr := &responseRecorder{header: http.Header{}}
	getTaskHandler(rr, req)

	if rr.status >= 400 {
		w.WriteHeader(rr.status)
		w.Write(rr.body)
		return
	}

	if err := WriteOutputWithFlag(w, format, json.RawMessage(rr.body)); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func handleTaskLogs(w http.ResponseWriter, r *http.Request) {
	taskID := r.URL.Query().Get("id")
	if taskID == "" {
		http.Error(w, "task id required", http.StatusBadRequest)
		return
	}
	format := r.URL.Query().Get("format")

	req, _ := http.NewRequest(http.MethodGet, "/api/tasks/"+taskID+"/events", r.Body)
	rr := &responseRecorder{header: http.Header{}}
	getTaskEventsHandler(rr, req)

	if rr.status >= 400 {
		w.WriteHeader(rr.status)
		w.Write(rr.body)
		return
	}

	if err := WriteOutputWithFlag(w, format, json.RawMessage(rr.body)); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

// --- CLI Agent/Fleet/System/Git/Queue Handlers ---

func handleAgentList(w http.ResponseWriter, r *http.Request) {
	format := r.URL.Query().Get("format")
	agents, err := getAllAgentsHealth()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	if err := WriteOutputWithFlag(w, format, agents); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func handleAgentStatus(w http.ResponseWriter, r *http.Request) {
	id := r.URL.Query().Get("id")
	if id == "" {
		http.Error(w, "agent id required", http.StatusBadRequest)
		return
	}
	format := r.URL.Query().Get("format")
	health, err := getAgentHealth(id)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	if err := WriteOutputWithFlag(w, format, health); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func handleSystemHealth(w http.ResponseWriter, r *http.Request) {
	format := r.URL.Query().Get("format")
	rr := &responseRecorder{header: http.Header{}}
	healthHandler(rr, r)
	if rr.status >= 400 {
		w.WriteHeader(rr.status)
		w.Write(rr.body)
		return
	}
	if err := WriteOutputWithFlag(w, format, json.RawMessage(rr.body)); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func handleQueueDepth(w http.ResponseWriter, r *http.Request) {
	format := r.URL.Query().Get("format")
	if taskQueue == nil {
		ExitWithError(NewUnavailableError("task queue"))
		return
	}
	// Simple depth approximation using claimable tasks.
	tasks, err := taskQueue.GetClaimableTasks(context.Background(), 100)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	payload := map[string]any{
		"depth": len(tasks),
	}
	if err := WriteOutputWithFlag(w, format, payload); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

// handleQueueStatus returns aggregate queue statistics for CLI consumption.
func handleQueueStatus(w http.ResponseWriter, r *http.Request) {
	format := r.URL.Query().Get("format")
	if taskQueue == nil {
		http.Error(w, "task queue not initialized", http.StatusServiceUnavailable)
		return
	}

	ctx := context.Background()
	tasks, err := taskQueue.ListAllTasks(ctx, 1000)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	now := time.Now()
	var pending, inProgress, failed, completedLastHour int
	var waits []time.Duration

	for _, t := range tasks {
		switch t.Status {
		case TaskStatusRequested, TaskStatusQueued:
			pending++
		case TaskStatusAssigned, TaskStatusExecuting:
			inProgress++
		case TaskStatusFailed:
			failed++
		}

		if t.Status == TaskStatusCompleted && t.UpdatedAt.After(now.Add(-1*time.Hour)) {
			completedLastHour++
		}

		// Approximate wait time as time from creation to first work (started_at or updated_at).
		if !t.CreatedAt.IsZero() {
			start := t.UpdatedAt
			if t.StartedAt != nil {
				start = *t.StartedAt
			}
			if !start.IsZero() && start.After(t.CreatedAt) {
				waits = append(waits, start.Sub(t.CreatedAt))
			}
		}
	}

	var avgWaitSeconds float64
	if len(waits) > 0 {
		var sum time.Duration
		for _, d := range waits {
			sum += d
		}
		avgWaitSeconds = sum.Seconds() / float64(len(waits))
	}

	payload := map[string]any{
		"total":               len(tasks),
		"pending":             pending,
		"in_progress":         inProgress,
		"failed":              failed,
		"completed_last_hour": completedLastHour,
		"avg_wait_seconds":    avgWaitSeconds,
	}

	if err := WriteOutputWithFlag(w, format, payload); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

// handleQueueList returns a list of tasks ordered by priority/age.
func handleQueueList(w http.ResponseWriter, r *http.Request) {
	format := r.URL.Query().Get("format")
	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		fmt.Sscanf(l, "%d", &limit)
		if limit <= 0 {
			limit = 50
		}
		if limit > 500 {
			limit = 500
		}
	}

	if taskQueue == nil {
		http.Error(w, "task queue not initialized", http.StatusServiceUnavailable)
		return
	}

	tasks, err := taskQueue.ListAllTasks(context.Background(), limit)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	// Order by priority (desc) then created_at (asc) to match queue semantics.
	// ListAllTasks already normalizes timestamps for us.
	if len(tasks) > 1 {
		// Simple insertion sort to avoid extra imports.
		for i := 1; i < len(tasks); i++ {
			j := i
			for j > 0 {
				a := tasks[j-1]
				b := tasks[j]
				swap := false
				if a.Priority < b.Priority {
					swap = true
				} else if a.Priority == b.Priority && a.CreatedAt.After(b.CreatedAt) {
					swap = true
				}
				if !swap {
					break
				}
				tasks[j-1], tasks[j] = tasks[j], tasks[j-1]
				j--
			}
		}
	}

	payload := map[string]any{
		"tasks": tasks,
		"count": len(tasks),
	}

	if err := WriteOutputWithFlag(w, format, payload); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

// handleQueuePriority updates the priority for a specific task.
func handleQueuePriority(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	db := getDBConn()
	if db == nil {
		http.Error(w, "database not initialized", http.StatusServiceUnavailable)
		return
	}

	var req struct {
		ID       string `json:"id"`
		Priority string `json:"priority"`
		Format   string `json:"format"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}
	if req.ID == "" || req.Priority == "" {
		http.Error(w, "id and priority are required", http.StatusBadRequest)
		return
	}

	level := strings.ToLower(req.Priority)
	var pri int
	switch level {
	case "high":
		pri = 90
	case "medium":
		pri = 50
	case "low":
		pri = 10
	default:
		http.Error(w, "priority must be one of: high, medium, low", http.StatusBadRequest)
		return
	}

	now := time.Now().Format(time.RFC3339)
	res, err := db.Exec(
		"UPDATE tasks SET priority = ?, updated_at = ? WHERE id = ?",
		pri, now, req.ID,
	)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to update priority: %v", err), http.StatusInternalServerError)
		return
	}
	rows, _ := res.RowsAffected()
	if rows == 0 {
		http.Error(w, "task not found", http.StatusNotFound)
		return
	}

	updated, err := taskQueue.GetTask(context.Background(), req.ID)
	if err != nil {
		http.Error(w, fmt.Sprintf("priority updated but failed to reload task: %v", err), http.StatusInternalServerError)
		return
	}

	payload := map[string]any{
		"status":   "ok",
		"task":     updated,
		"priority": pri,
		"level":    level,
	}
	if err := WriteOutputWithFlag(w, req.Format, payload); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

// handleQueueCancel marks a pending task as failed/cancelled.
func handleQueueCancel(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if taskQueue == nil {
		http.Error(w, "task queue not initialized", http.StatusServiceUnavailable)
		return
	}

	var req struct {
		ID     string `json:"id"`
		Reason string `json:"reason"`
		Format string `json:"format"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}
	if req.ID == "" {
		http.Error(w, "id is required", http.StatusBadRequest)
		return
	}
	if req.Reason == "" {
		req.Reason = "Cancelled by user via CLI"
	}

	ctx := context.Background()
	task, err := taskQueue.GetTask(ctx, req.ID)
	if err != nil {
		http.Error(w, "task not found", http.StatusNotFound)
		return
	}

	if task.Status != TaskStatusRequested && task.Status != TaskStatusQueued && task.Status != TaskStatusPlanned {
		http.Error(w, fmt.Sprintf("only pending tasks can be cancelled (current status: %s)", task.Status), http.StatusBadRequest)
		return
	}

	if err := taskQueue.Fail(ctx, req.ID, req.Reason); err != nil {
		http.Error(w, fmt.Sprintf("failed to cancel task: %v", err), http.StatusInternalServerError)
		return
	}

	updated, err := taskQueue.GetTask(ctx, req.ID)
	if err != nil {
		http.Error(w, fmt.Sprintf("cancelled but failed to reload task: %v", err), http.StatusInternalServerError)
		return
	}

	payload := map[string]any{
		"status": "cancelled",
		"task":   updated,
		"reason": req.Reason,
	}
	if err := WriteOutputWithFlag(w, req.Format, payload); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

// responseRecorder is a minimal http.ResponseWriter used to capture
// responses from internal handlers without starting a real server.
type responseRecorder struct {
	header http.Header
	body   []byte
	status int
}

func (r *responseRecorder) Header() http.Header {
	return r.header
}

func (r *responseRecorder) Write(b []byte) (int, error) {
	r.body = append(r.body, b...)
	if r.status == 0 {
		r.status = http.StatusOK
	}
	return len(b), nil
}

func (r *responseRecorder) WriteHeader(statusCode int) {
	r.status = statusCode
}

func withTimeout(h http.HandlerFunc, timeout time.Duration) http.Handler {
	return middleware.TimeoutMiddleware(timeout)(h)
}

// versionHeaderMiddleware injects the daemon build version into every response
// so CLI clients can detect version mismatches early.
func versionHeaderMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Forge-Version", Version)
		next.ServeHTTP(w, r)
	})
}

func main() {
	hub = NewHub()

	// ADR-030: load forge.toml and set env from config when not already set
	cfg := loadForgeConfig()
	if cfg.Daemon.Port != 0 && os.Getenv("PORT") == "" {
		os.Setenv("PORT", strconv.Itoa(cfg.Daemon.Port))
	}
	if cfg.Daemon.WSPort != 0 && os.Getenv("WS_PORT") == "" {
		os.Setenv("WS_PORT", strconv.Itoa(cfg.Daemon.WSPort))
	}
	if cfg.Daemon.DBPath != "" && os.Getenv("DB_PATH") == "" {
		os.Setenv("DB_PATH", cfg.Daemon.DBPath)
	}
	if cfg.Daemon.NodeID != "" && os.Getenv("NODE_ID") == "" {
		os.Setenv("NODE_ID", cfg.Daemon.NodeID)
	}
	if cfg.Daemon.ForgeRoot != "" && os.Getenv("FORGE_ROOT") == "" {
		os.Setenv("FORGE_ROOT", cfg.Daemon.ForgeRoot)
	}
	if cfg.Auth.Mode != "" && os.Getenv("FORGE_AUTH_MODE") == "" {
		os.Setenv("FORGE_AUTH_MODE", cfg.Auth.Mode)
	}
	if cfg.Auth.APIToken != "" && os.Getenv("FORGE_API_TOKEN") == "" {
		os.Setenv("FORGE_API_TOKEN", cfg.Auth.APIToken)
	}

	// Migrate subcommand: run migrations then exit
	var migrateUp, migrateDown bool
	var migrateDownSteps int
	var dbPath string
	for i := 1; i < len(os.Args); i++ {
		switch os.Args[i] {
		case "migrate":
			if i+1 < len(os.Args) {
				switch os.Args[i+1] {
				case "up":
					migrateUp = true
				case "down":
					migrateDown = true
					if i+2 < len(os.Args) {
						fmt.Sscanf(os.Args[i+2], "%d", &migrateDownSteps)
					}
					if migrateDownSteps <= 0 {
						migrateDownSteps = 1
					}
				}
			}
			i++
		case "-db-path":
			if i+1 < len(os.Args) {
				dbPath = os.Args[i+1]
				i++
			}
		}
	}
	if migrateUp || migrateDown {
		if dbPath == "" {
			dbPath = os.Getenv("DB_PATH")
		}
		if dbPath == "" {
			dbPath = defaultDBPath
		}
		database, err := OpenDB(dbPath)
		if err != nil {
			log.Fatalf("failed to open database: %v", err)
		}
		defer database.Close()
		if migrateUp {
			if err := MigrateUp(database); err != nil {
				log.Fatalf("migrate up: %v", err)
			}
			log.Println("migrations applied")
			return
		}
		if migrateDown {
			if err := MigrateDown(database, migrateDownSteps); err != nil {
				log.Fatalf("migrate down: %v", err)
			}
			log.Println("migrations rolled back")
			return
		}
	}

	// ============================================================
	// CLI Subcommand Handling (noun-verb pattern)
	// ============================================================
	// Handle commands like: forge system patrol, forge git guard
	// These commands exit immediately and don't start the server

	if len(os.Args) >= 2 {
		noun := os.Args[1]

		switch noun {
		case "help", "--help", "-h":
			// Handle help command and flags
			RunHelp(os.Args[2:])
			os.Exit(0)
		case "completion":
			NewCompletionManager().HandleCompletion(os.Args[2:])
			os.Exit(0)
		case "system":
			// forge system [health|metrics|patrol]
			if len(os.Args) < 3 {
				fmt.Println("forge system: subcommand required")
				fmt.Println("Available: health, metrics, patrol")
				os.Exit(1)
			}
			verb := os.Args[2]
			switch verb {
			case "health":
				// Quick health check without full server startup
				fmt.Println("=== FORGE System Health ===")
				dbPath := os.Getenv("DB_PATH")
				if dbPath == "" {
					dbPath = defaultDBPath
				}
				if _, err := os.Stat(dbPath); os.IsNotExist(err) {
					fmt.Printf("Database: MISSING (%s)\n", dbPath)
				} else {
					fmt.Printf("Database: OK (%s)\n", dbPath)
				}
				forges := []string{".forge/context", ".forge/dispatches", ".forge/heartbeat"}
				for _, f := range forges {
					if _, err := os.Stat(filepath.Join(forgeRoot(), f)); os.IsNotExist(err) {
						fmt.Printf("%s: MISSING\n", f)
					} else {
						fmt.Printf("%s: OK\n", f)
					}
				}
				fmt.Println("\nStatus: healthy")
				os.Exit(0)
			case "metrics":
				fmt.Println("=== FORGE System Metrics ===")
				fmt.Println("(Metrics endpoint available at /metrics when server running)")
				fmt.Println("\nUse 'forge system patrol' to run maintenance checks")
				os.Exit(0)
			case "patrol":
				// Open database and run patrol checks once
				dbPath := os.Getenv("DB_PATH")
				if dbPath == "" {
					dbPath = defaultDBPath
				}
				database, err := OpenDB(dbPath)
				if err != nil {
					fmt.Fprintf(os.Stderr, "Failed to open database: %v\n", err)
					os.Exit(1)
				}
				defer database.Close()
				if err := MigrateUp(database); err != nil {
					fmt.Fprintf(os.Stderr, "Failed to run migrations: %v\n", err)
					os.Exit(1)
				}
				patrolSystem := NewPatrolSystem(database)
				fmt.Println("=== Running FORGE System Patrol ===")
				ctx := context.Background()
				for _, patrol := range patrolSystem.patrols {
					fmt.Printf("Running: %s (%s)... ", patrol.Name, patrol.ID)
					if err := patrol.Action(ctx, database); err != nil {
						fmt.Printf("ERROR: %v\n", err)
					} else {
						fmt.Println("OK")
					}
				}
				fmt.Println("\nPatrol run complete")
				os.Exit(0)
			default:
				fmt.Printf("Unknown system command: %s\n", verb)
				fmt.Println("Available: health, metrics, patrol")
				os.Exit(1)
			}

		case "git":
			// forge git [guard|status|commit]
			if len(os.Args) < 3 {
				fmt.Println("forge git: subcommand required")
				fmt.Println("Available: guard, status, commit")
				os.Exit(1)
			}
			verb := os.Args[2]
			switch verb {
			case "guard":
				dbPath := os.Getenv("DB_PATH")
				if dbPath == "" {
					dbPath = defaultDBPath
				}
				database, err := OpenDB(dbPath)
				if err != nil {
					fmt.Fprintf(os.Stderr, "Failed to open database: %v\n", err)
					os.Exit(1)
				}
				defer database.Close()
				if err := MigrateUp(database); err != nil {
					fmt.Fprintf(os.Stderr, "Failed to run migrations: %v\n", err)
					os.Exit(1)
				}
				gitGuard := NewGitGuard(database, forgeRoot())
				holder, err := gitGuard.GetBranchLock(context.Background(), "")
				if err != nil && err.Error() != "no lock held" {
					fmt.Fprintf(os.Stderr, "Failed to check git lock: %v\n", err)
					os.Exit(1)
				}
				if holder != "" {
					fmt.Printf("Git Guard: LOCKED by %s\n", holder)
					os.Exit(1)
				} else {
					fmt.Println("Git Guard: UNLOCKED (available)")
					os.Exit(0)
				}
			case "status":
				fmt.Println("=== Git Status ===")
				fmt.Println("(Use 'forge git guard' for single-writer operations)")
				fmt.Println("\nStub: Full git status integration coming soon")
				os.Exit(0)
			case "commit":
				fmt.Println("=== Git Commit ===")
				fmt.Println("(Use 'forge git guard --action commit' for safe commits)")
				fmt.Println("\nStub: Full git commit integration coming soon")
				os.Exit(0)
			default:
				fmt.Printf("Unknown git command: %s\n", verb)
				fmt.Println("Available: guard, status, commit")
				os.Exit(1)
			}

		case "docs":
			// forge docs [quickstart|patterns|examples]
			hs := NewHelpSystem()
			if len(os.Args) < 3 {
				hs.ShowDocsHelp()
				os.Exit(0)
			}
			verb := os.Args[2]
			switch verb {
			case "quickstart":
				hs.RunQuickstart()
			case "patterns":
				hs.ShowPatterns()
			case "examples":
				fmt.Println("=== FORGE Examples ===")
				fmt.Println("\nSee 'forge help <topic>' for more examples")
				os.Exit(0)
			default:
				fmt.Printf("Unknown docs command: %s\n", verb)
				fmt.Println("Available: quickstart, patterns, examples")
				os.Exit(1)
			}
			os.Exit(0)

		case "task", "queue", "node", "agent":
			// Use cobra for new CLI commands
			if err := rootCmd.Execute(); err != nil {
				fmt.Fprintf(os.Stderr, "Error: %v\n", err)
				os.Exit(1)
			}
			os.Exit(0)

		case "dashboard":
			// forge dashboard [--api-url URL]
			apiURL := os.Getenv("FORGE_API_URL")
			if apiURL == "" {
				apiURL = "http://localhost:8081"
			}
			// Check for --api-url flag
			for i := 2; i < len(os.Args); i++ {
				if os.Args[i] == "--api-url" && i+1 < len(os.Args) {
					apiURL = os.Args[i+1]
					break
				}
			}
			fmt.Println("Starting FORGE Dashboard...")
			fmt.Printf("Connecting to: %s\n", apiURL)
			if err := RunDashboard(apiURL); err != nil {
				fmt.Fprintf(os.Stderr, "Dashboard error: %v\n", err)
				os.Exit(1)
			}
			os.Exit(0)
		}
		// If noun doesn't match any known command, fall through to server startup
		// This maintains backward compatibility
	}

	var database *sql.DB
	var err error

	// Use SQLite
	dbPath = os.Getenv("DB_PATH")
	if dbPath == "" {
		dbPath = defaultDBPath
	}
	database, err = OpenDB(dbPath)
	if err != nil {
		log.Fatalf("failed to open database: %v", err)
	}
	defer database.Close()
	log.Printf("DB: %s", dbPath)

	// Run migrations
	if err := MigrateUp(database); err != nil {
		log.Fatalf("failed to run migrations: %v", err)
	}

	// S173 E1.1: Reset all agents to offline on daemon startup.
	// Agents will re-register via heartbeat when they reconnect.
	// This prevents ghost agents from surviving daemon restarts.
	if result, err := database.Exec(`UPDATE agent_heartbeats SET status = 'offline' WHERE status IN ('connected', 'online')`); err != nil {
		log.Printf("[startup] WARNING: failed to reset agent statuses: %v", err)
	} else if n, _ := result.RowsAffected(); n > 0 {
		log.Printf("[startup] Reset %d agent(s) to offline (will re-register via heartbeat)", n)
	}

	// Prune old completed/failed tasks on startup (48h retention)
	pruned, pruneErr := pruneOldTasks(database, 48*time.Hour)
	if pruneErr != nil {
		log.Printf("[startup] task prune failed: %v", pruneErr)
	} else if pruned > 0 {
		log.Printf("[startup] pruned %d completed/failed tasks older than 48h", pruned)
	}

	// Initialize task queue
	q, err := NewTaskQueueFromDB(database)
	if err != nil {
		log.Fatalf("failed to create task queue: %v", err)
	}
	taskQueue = q
	setDBConn(database)

	// Init StateMachine (ADR-028)
	taskStore = NewTaskStore(database)
	stateMachine = NewStateMachine(taskStore, database)
	log.Println("StateMachine initialized")

	// Init LeaseManager (ADR-010)
	dbPath = os.Getenv("DB_PATH")
	if dbPath == "" {
		dbPath = ".forge/forge-v3.db"
	}
	if lm, err := NewLeaseManager(dbPath); err != nil {
		log.Printf("WARN: LeaseManager init failed: %v — lease enforcement disabled", err)
	} else {
		leaseManager = lm
		log.Println("LeaseManager initialized")
	}

	startStateSyncJob(context.Background(), database, 10*time.Minute)
	log.Println("State sync job started (10m interval)")

	// Start lease recovery job — scans for expired leases every 5 minutes
	// and transitions orphaned tasks (DISPATCHED/RUNNING with expired lease) to FAILED.
	// This is critical for task recovery when agents die unexpectedly.
	go func() {
		ticker := time.NewTicker(5 * time.Minute)
		defer ticker.Stop()
		for range ticker.C {
			if leaseManager != nil {
				recovered, err := leaseManager.Recover(context.Background())
				if err != nil {
					log.Printf("[LeaseRecovery] error: %v", err)
				} else if len(recovered) > 0 {
					log.Printf("[LeaseRecovery] recovered %d expired leases", len(recovered))
				}
			}
		}
	}()
	log.Println("Lease recovery job started (5m interval)")

	// Initialize auth manager.
	// Default: "api" if FORGE_API_TOKEN is set, otherwise "local" with a warning.
	// Set FORGE_AUTH_MODE=local explicitly to suppress the warning in dev.
	authMode := os.Getenv("FORGE_AUTH_MODE")
	if authMode == "" {
		if os.Getenv("FORGE_API_TOKEN") != "" {
			authMode = "api"
		} else {
			authMode = "local"
			log.Println("SECURITY WARNING: running in unauthenticated 'local' mode — set FORGE_API_TOKEN + FORGE_AUTH_MODE=api for production")
		}
	}
	// Initialize harness reverse proxy (ADR-014 Option C).
	// Set FORGE_HARNESS_URL=http://localhost:8080 to forward unknown routes to harness.
	initHarnessProxy()

	authManager = NewAuthManager(authMode)
	if authMode == "api" {
		if seedToken := os.Getenv("FORGE_API_TOKEN"); seedToken != "" {
			authManager.mu.Lock()
			authManager.config.APITokens[seedToken] = TokenInfo{
				Token:       seedToken,
				Description: "env-seeded",
				CreatedAt:   time.Now(),
				Scopes:      []string{"*"},
			}
			authManager.mu.Unlock()
			log.Printf("Auth: seeded API token from FORGE_API_TOKEN")
		}
	}
	log.Printf("Auth mode: %s", authMode)

	// Initialize plan manager
	planManager = NewPlanManager(database)

	// Initialize agent registry with 5-minute staleness window
	registry := NewAgentRegistry(database, 5*time.Minute)
	registry.StartCleanup(time.Minute)

	_ = registry // currently unused by HTTP handlers; reserved for Phase 2 integration

	// Initialize metrics
	InitMetrics()

	// Set up metrics callbacks for queue depth and active leases
	SetQueueDepthFunc(func() int64 {
		db := getDBConn()
		if db == nil {
			return 0
		}
		var count int64
		err := db.QueryRow("SELECT COUNT(*) FROM tasks WHERE state = 'QUEUED'").Scan(&count)
		if err != nil {
			return 0
		}
		return count
	})

	SetActiveLeasesFunc(func() int64 {
		db := getDBConn()
		if db == nil {
			return 0
		}
		var count int64
		err := db.QueryRow("SELECT COUNT(*) FROM leases WHERE expires_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now')").Scan(&count)
		if err != nil {
			return 0
		}
		return count
	})

	// Initialize context manager first (needed by patrol system)
	contextManager = NewContextManager(database, ".forge/context")
	log.Println("Context manager initialized")

	// Initialize worktree manager for ADR-024 worktree tracking
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot, _ = os.Getwd()
	}
	worktreeMgr = NewWorktreeManager(forgeRoot, "", database)
	log.Println("WorktreeManager initialized")

	// Initialize Royal Jelly
	royalJelly := NewRoyalJelly(database, contextManager)
	royalJelly.RegisterHook("context_threshold", &ContextThresholdHook{cm: contextManager})

	// Initialize and start patrol system
	patrolSystem := NewPatrolSystem(database)
	patrolSystem.SetContextManager(contextManager)
	patrolSystem.SetRoyalJelly(royalJelly)

	patrolSystem.Start()
	defer patrolSystem.Stop()
	globalPatrolSystem = patrolSystem // expose to HTTP handler (GET /api/patrols)
	log.Println("Patrol system started with 7 patrols (including context threshold)")

	// Initialize XNodeController
	nodeID := os.Getenv("NODE_ID")
	if nodeID == "" {
		// Default to actual hostname — never hardcode a node name
		if h, err := os.Hostname(); err == nil {
			nodeID = h
		} else {
			nodeID = "prya" // last-resort fallback
		}
	}
	xnodeController, err := NewXNodeController(database, nodeID)
	if err != nil {
		log.Fatalf("Failed to initialize XNode controller: %v", err)
	}

	// ADR-038: self-register on startup so node is immediately visible to fleet
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := xnodeController.Heartbeat(ctx, nodeID); err != nil {
			log.Printf("[ADR-038] self-registration failed: %v", err)
		} else {
			log.Printf("[ADR-038] node %s self-registered", nodeID)
		}
	}()

	xnodeController.StartHeartbeatMonitor(30 * time.Second)

	// Start XNode outbox serialization worker (ADR-023)
	xnodeController.StartSerializationWorker(context.Background(), database)

	// Ingest incoming messages from inbox on startup (ADR-023)
	if err := xnodeController.IngestIncomingMessages(context.Background(), database); err != nil {
		log.Printf("[XNode] Error ingesting incoming messages: %v", err)
	}

	// Continuously poll inbox for new JSONL messages (ADR-023)
	xnodeController.StartInboxWorker(context.Background())

	log.Println("XNode controller initialized for node:", nodeID)

	// Self-register this node in the local DB on startup.
	// NODE_ADDRESS must be set to this node's reachable Tailscale IP:port (e.g. 100.80.39.128:8081).
	selfAddress := os.Getenv("NODE_ADDRESS")
	if selfAddress == "" {
		// Auto-detect Tailscale IP; fall back to first non-loopback LAN IP.
		apiPort := os.Getenv("PORT")
		if apiPort == "" {
			apiPort = "8081"
		}
		if tsOut, err := exec.Command("tailscale", "ip", "-4").Output(); err == nil {
			ip := strings.TrimSpace(string(tsOut))
			if ip != "" {
				selfAddress = ip + ":" + apiPort
			}
		}
		if selfAddress == "" {
			if ifaces, err := net.Interfaces(); err == nil {
				for _, iface := range ifaces {
					addrs, _ := iface.Addrs()
					for _, addr := range addrs {
						if ipnet, ok := addr.(*net.IPNet); ok && !ipnet.IP.IsLoopback() && ipnet.IP.To4() != nil {
							selfAddress = ipnet.IP.String() + ":" + apiPort
							break
						}
					}
					if selfAddress != "" {
						break
					}
				}
			}
		}
		if selfAddress == "" {
			selfAddress = "127.0.0.1:" + apiPort
		}
	}
	selfNode := Node{
		ID:            nodeID,
		Hostname:      nodeID,
		Address:       selfAddress,
		Status:        "online",
		LastHeartbeat: time.Now(),
		Version:       Version,
		GitCommit:     GitCommit,
	}
	if err := xnodeController.RegisterNode(context.Background(), selfNode); err != nil {
		log.Printf("WARN: failed to self-register node %s: %v", nodeID, err)
	} else {
		log.Printf("Node %s self-registered at %s", nodeID, selfAddress)
	}

	// Keep this node's own DB entry fresh so HeartbeatMonitor (2-min threshold) never marks us offline.
	go func() {
		ticker := time.NewTicker(90 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			selfNode.LastHeartbeat = time.Now()
			if err := xnodeController.RegisterNode(context.Background(), selfNode); err != nil {
				log.Printf("[XNode] self-heartbeat refresh error: %v", err)
			}
		}
	}()

	// Announce this node to known peers over HTTP (Tailscale).
	// KNOWN_PEERS is a comma-separated list of peer HTTP base URLs,
	// e.g. "http://sati:8081,http://nova:8081"
	// Falls back to FORGE_API_URL if KNOWN_PEERS is empty (remote nodes).
	// Skips if FORGE_API_URL points to localhost (hub doesn't announce to itself).
	// Fires immediately at startup, then repeats every 5 minutes to keep status "online".
	getPeers := func() []string {
		peersEnv := os.Getenv("KNOWN_PEERS")
		if peersEnv != "" {
			return strings.Split(peersEnv, ",")
		}
		// Fallback to FORGE_API_URL for remote nodes
		apiURL := os.Getenv("FORGE_API_URL")
		if apiURL == "" {
			return nil
		}
		// Skip localhost - hub doesn't need to announce to itself
		if strings.Contains(apiURL, "localhost") || strings.Contains(apiURL, "127.0.0.1") {
			return nil
		}
		return []string{apiURL}
	}
	go func() {
		peers := getPeers()
		if len(peers) == 0 {
			return
		}
		announceToPeers := func() {
			// Refresh heartbeat timestamp so HeartbeatMonitor never marks us offline.
			selfNode.LastHeartbeat = time.Now()
			// Re-register self locally to update last_heartbeat in the DB.
			if regErr := xnodeController.RegisterNode(context.Background(), selfNode); regErr != nil {
				log.Printf("[XNode] self-registration refresh failed: %v", regErr)
			}
			for _, peer := range peers {
				peer = strings.TrimSpace(peer)
				if peer != "" {
					announceNodeToPeer(peer, selfNode)
				}
			}
		}
		announceToPeers()
		ticker := time.NewTicker(5 * time.Minute)
		defer ticker.Stop()
		for range ticker.C {
			announceToPeers()
		}
	}()

	// Periodic peer re-announcement (mesh auto-heal)
	go func() {
		ticker := time.NewTicker(60 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			peers := getPeers()
			for _, peer := range peers {
				peer = strings.TrimSpace(peer)
				if peer == "" {
					continue
				}
				announceNodeToPeer(peer, selfNode)
			}
		}
	}()

	// Initialize GitGuard for single-writer git operations
	gitGuard := NewGitGuard(database, forgeRoot)
	_ = gitGuard // Used by gitguardHandler

	// Initialize TUI dashboard
	tuiDashboard := NewTUI(database, hub, taskQueue)

	mux := http.NewServeMux()

	// Initialize fleet health dashboard (ADR-027)
	fleetDashboard := NewDashboard(database, hub)
	fleetDashboard.RegisterRoutes(mux)

	// Initialize approval system
	approvalStore := NewApprovalStore(database)
	approvalService := NewApprovalService(approvalStore)
	approvalHandler := NewApprovalHandler(approvalService)
	approvalHandler.RegisterRoutes(mux)

	// Initialize handoff system
	handoffStore := NewHandoffStore(database)
	handoffService := NewHandoffService(handoffStore)
	handoffHandler := NewHandoffHandler(handoffService)
	handoffHandler.RegisterRoutes(mux)

	// Initialize PWA dashboard bridge (ADR-014)
	pwaDashboardHandler := NewPWADashboardHandler(fleetDashboard)
	pwaDashboardHandler.RegisterRoutes(mux)

	// Initialize lane manager
	laneConfigPath := filepath.Join(forgeRoot, "lane_config.yaml")
	laneConfigs, laneConfigErr := LoadLaneConfig(laneConfigPath)
	if laneConfigErr != nil {
		log.Printf("[warn] lane_config.yaml not loaded: %v — all quality gates disabled", laneConfigErr)
	}
	laneManager := NewLaneManager(taskQueue, approvalService, laneConfigs)
	globalLaneManager = laneManager          // expose to patrol.go (ADR-033 auto-promote patrol)
	globalApprovalService = approvalService  // expose to patrol.go (ADR-033 confidence-approve patrol)

	// Start background expiration checker
	go approvalHandler.RunExpirationCheck(context.Background(), 5*time.Minute)
	log.Println("Approval system initialized with confidence scoring")

	// ADR-018: load git-tracked patterns from docs/patterns/ into DB
	loadPatternsFromDocs(database)

	mux.HandleFunc("/api/gitguard", gitguardHandler(gitGuard))
	mux.HandleFunc("/api/health", healthHandler)
	mux.HandleFunc("/api/events", eventsHandler)
	mux.HandleFunc("/health", healthHandler)

	mux.HandleFunc("/api/health/detailed", DetailedHealthHandler)
	mux.HandleFunc("/api/status", statusHandler)
	mux.HandleFunc("/api/nodes/health", nodesHealthHandler)
	mux.HandleFunc("/api/patrols", patrolsHandler)
	mux.HandleFunc("/api/patrols/", patrolRunHandler)
	mux.HandleFunc("/api/patrol-executions", patrolExecutionsHandler)
	mux.HandleFunc("/dash", dashHandler)
	mux.HandleFunc("/ui/patrol/", uiPatrolDrillDownHandler)
	mux.HandleFunc("/ui/domains", uiDomainsHandler)
	mux.HandleFunc("/ui", uiFleetHandler)
	mux.HandleFunc("/api/fleet/snapshot", fleetSnapshotHandler)
	mux.HandleFunc("/api/fleet/recommendations", fleetRecommendationsHandler)
	mux.HandleFunc("/api/fleet/summary", fleetSummaryHandler)
	mux.HandleFunc("/api/blueprints", blueprintsHandler)
	mux.HandleFunc("/api/blueprints/runs", blueprintRunsHandler)
	mux.HandleFunc("/api/blueprints/runs/", blueprintRunByIDHandler)
	mux.HandleFunc("/api/routing/resolve", routingResolveHandler)
	mux.HandleFunc("/api/lead-state", leadStateHandler)
	mux.HandleFunc("/api/messages", messagesHandler)
	mux.HandleFunc("/api/messages/", messageByIDHandler)
	mux.HandleFunc("/api/tui/logs", tuiLogsHandler)
	mux.HandleFunc("/tui", tuiDashboard.Handler())
	mux.HandleFunc("/api/tui/dashboard", tuiDashboard.JSONHandler())
	mux.HandleFunc("/api/dashboard/throughput", dashboardThroughputHandler)
	mux.HandleFunc("/api/parity", parityHandler(database))
	// /api/agents/telemetry/summary and /api/agents/stream must be before /api/agents/ wildcard (net/http longest-match)
	mux.HandleFunc("/api/agents/telemetry/summary", agentTelemetrySummaryHandler)
	// agentMetricsHandler: /api/agents/:id/metrics — registered via wildcard below with suffix check
	mux.HandleFunc("/api/agents/", func(w http.ResponseWriter, r *http.Request) {
		path := r.URL.Path
		if path == "/api/agents" || path == "/api/agents/" {
			agentsHandler(w, r)
			return
		}
		if path == "/api/agents/health" {
			agentsHealthHandler(w, r)
			return
		}
		if strings.HasSuffix(path, "/context") {
			agentContextHandler(w, r)
			return
		}
		if strings.HasSuffix(path, "/heartbeat") {
			agentHeartbeatReceive(w, r)
			return
		}
		if strings.HasSuffix(path, "/telemetry") {
			agentTelemetryHandler(w, r)
			return
		}
		if strings.HasSuffix(path, "/metrics") {
			agentMetricsHandler(w, r)
			return
		}
		if strings.HasSuffix(path, "/tasks") {
			agentTasksHandler(w, r)
			return
		}
		if strings.HasSuffix(path, "/cooldown") {
			agentCooldownHandler(w, r)
			return
		}
		// Try agent by ID
		agentByIDHandler(w, r)
	})
	// ADR-014: SSE stream must be registered before /api/agents wildcard
	mux.HandleFunc("/api/agents/stream", agentsSSEHandler)
	mux.HandleFunc("/api/agents/cooldowns", agentCooldownsHandler)
	mux.HandleFunc("/api/agents", agentsHandler)
	mux.HandleFunc("/api/notifications", notificationsHandler)
	mux.HandleFunc("/api/notifications/", notificationActionHandler)
	mux.HandleFunc("/api/openclaw", openclawHandler)
	mux.HandleFunc("/api/openclaw/", openclawHandler)
	mux.HandleFunc("/api/tasks", tasksHandler)
	mux.HandleFunc("/api/tasks/", func(w http.ResponseWriter, r *http.Request) {
		path := r.URL.Path
		taskID := strings.TrimPrefix(path, "/api/tasks/")

		// Check for action endpoints (must come before other suffix checks)
		if strings.HasSuffix(taskID, "/approve") {
			approveTaskHandler(w, r)
			return
		}
		if strings.HasSuffix(taskID, "/pause") {
			pauseTaskHandler(w, r)
			return
		}
		if strings.HasSuffix(taskID, "/resume") {
			resumeTaskHandler(w, r)
			return
		}
		if strings.HasSuffix(taskID, "/complete") {
			completeTaskHandler(w, r)
			return
		}
		if strings.HasSuffix(taskID, "/complete-with-approval") {
			completeTaskWithApprovalHandler(w, r)
			return
		}
		if strings.HasSuffix(taskID, "/claim") {
			claimTaskHandler(w, r)
			return
		}
		if strings.HasSuffix(taskID, "/ack") {
			ackTaskHandler(w, r)
			return
		}
		if strings.HasSuffix(taskID, "/abandon") {
			abandonTaskHandler(w, r)
			return
		}
		if strings.HasSuffix(taskID, "/release") {
			releaseTaskHandler(w, r)
			return
		}
		if strings.HasSuffix(taskID, "/extend-lease") {
			extendLeaseHandler(w, r)
			return
		}
		if strings.HasSuffix(taskID, "/events") {
			getTaskEventsHandler(w, r)
			return
		}
		if strings.HasSuffix(taskID, "/history") {
			taskHistoryHandler(w, r)
			return
		}
		if strings.HasSuffix(taskID, "/quality-gates") {
			qualityGatesHandler(w, r)
			return
		}
		if strings.HasSuffix(taskID, "/lane/complete") {
			laneManager.HandleLaneComplete(w, r)
			return
		}
		if strings.HasSuffix(taskID, "/lane/status") {
			laneManager.HandleLaneStatus(w, r)
			return
		}

		if strings.HasSuffix(path, "/plan") {
			planTaskHandler(w, r)
		} else if strings.HasSuffix(path, "/replan") {
			replanTaskHandler(w, r)
		} else if strings.HasSuffix(path, "/plans") {
			getPlanHistoryHandler(w, r)
		} else if strings.HasSuffix(path, "/queue") {
			queueTaskHandler(w, r)
		} else if r.Method == http.MethodPut {
			taskUpdateHandler(w, r)
		} else if r.Method == http.MethodDelete {
			taskDeleteHandler(w, r)
		} else {
			getTaskHandler(w, r)
		}
	})
	mux.HandleFunc("/api/tasks/approve", approveTaskHandler) // guard for bare /approve path
	mux.HandleFunc("/api/tasks/claimable", claimableTasksHandler)
	mux.HandleFunc("/api/tasks/prune", pruneTasksHandler)

	// Projects API
	mux.HandleFunc("/api/projects", projectsHandler)
	mux.HandleFunc("/api/projects/", projectByIDHandler)

	// Domains API
	mux.HandleFunc("/api/domains/", PatchDomainHandler)

	// Agents API handled via /api/agents/ above

	// Workers API
	mux.HandleFunc("/api/workers", workersHandler)
	mux.HandleFunc("/api/workers/", workerByIDHandler)

	// Config API
	mux.HandleFunc("/api/config", configHandler)

	// Dispatch API
	mux.HandleFunc("/api/dispatch", dispatchHandler)

	// GitHub webhook (no auth required — GitHub calls this externally)
	mux.HandleFunc("/api/github/webhook", githubWebhookHandler)

	// Lanes API
	mux.HandleFunc("/api/lanes", lanesHandler)
	mux.HandleFunc("/api/lanes/", laneByIDHandler)

	// Contexts API
	mux.HandleFunc("/api/contexts", contextsHandler)
	mux.HandleFunc("/api/contexts/", contextByIDHandler)

	// Pattern library routes (ADR-014 CC retirement gate #2)
	mux.HandleFunc("/api/patterns", patternsHandler)
	mux.HandleFunc("/api/patterns/", patternByIDOrRunsHandler)

	// Note: /api/agents/health already registered above
	// Context envelope APIs (plural and singular path for convenience)
	mux.HandleFunc("/api/context/envelopes", contextManager.EnvelopesHandler)
	mux.HandleFunc("/api/context/envelope", contextManager.EnvelopesHandler)
	mux.HandleFunc("/api/context/envelopes/", contextManager.EnvelopeByIDHandler)
	mux.HandleFunc("/api/context/bootstrap", contextManager.BootstrapHandler)

	// Coordination dashboard endpoint
	coordinationDashboard := NewCoordinationDashboard(database)
	mux.Handle("/api/coordination/status", coordinationDashboard)

	// XNode (cross-node) routes
	log.Println("Registering XNode routes...")
	xnodeController.RegisterRoutes(mux)
	log.Println("XNode routes registered")

	mux.HandleFunc("/api/metrics", MetricsHandler)
	mux.HandleFunc("/api/debug", DebugHandler)
	mux.HandleFunc("/api/auth/tokens", authTokensHandler(authManager))

	// ADR-035 P1: Node Capability Manifest — lets orchestrators know what
	// agent types each node can spawn (respects ForbiddenAgentTypes + ceilings).
	mux.HandleFunc("/api/fleet/node-capabilities", nodeCapabilitiesHandler)

	// Apple Watch FORGE Terminal: push device registry + watch-optimized summary.
	mux.HandleFunc("/api/push/register", pushRegisterHandler)
	mux.HandleFunc("/api/push/devices", pushDevicesHandler)
	mux.HandleFunc("/api/watch/summary", watchSummaryHandler)

	// Wire watch summary handler to the already-initialized approval service.
	watchSummaryApprovalService = approvalService

	// Voice mode: NLU command dispatch, vocabulary, transcription stub, analytics.
	mux.HandleFunc("/api/voice/command", voiceCommandHandler)
	mux.HandleFunc("/api/voice/vocabulary", voiceVocabularyHandler)
	mux.HandleFunc("/api/voice/transcribe", voiceTranscribeHandler)
	mux.HandleFunc("/api/voice/analytics", voiceAnalyticsHandler)

	// ADR-014: Relay delivery endpoints.
	mux.HandleFunc("/api/relay/deliveries", relayDeliveriesHandler) // GET /api/relay/deliveries
	mux.HandleFunc("/api/relay/dispatch", relayDispatchHandler)     // POST /api/relay/dispatch
	mux.HandleFunc("/api/relay/", relayPrefixHandler)               // POST /api/relay/{id}/ack

	// ADR-027: Cross-node metric aggregation.
	// Worker nodes POST their local rollups here; /api/fleet/metrics aggregates.
	mux.HandleFunc("/api/nodes/", nodeMetricsReceiveHandler)      // POST /api/nodes/{id}/metrics
	mux.HandleFunc("/api/fleet/metrics", fleetMetricsHandler)     // GET /api/fleet/metrics
	mux.HandleFunc("/api/fleet/aggregate", fleetAggregateHandler) // GET /api/fleet/aggregate (ADR-027 fan-out)

	// CLI noun-verb router endpoints consumed by forge v2 CLI.
	CLIRouter(mux)

	// Catch-all: proxy to harness if FORGE_HARNESS_URL is set, else 404 with hint.
	// Must be registered last so all specific routes take priority (Go ServeMux longest-match).
	mux.HandleFunc("/", harnessFallbackHandler)

	// Get port from environment or default
	port := os.Getenv("PORT")
	if port == "" {
		port = "8081"
	}

	server := &http.Server{
		Addr:    ":" + port,
		Handler: versionHeaderMiddleware(middleware.RateLimitMiddleware(middleware.TimeoutMiddleware(middleware.DefaultTimeout)(LoggingMiddleware(AuthMiddleware(authManager)(mux))))),
	}

	// Start WebSocket hub for real-time multinode communication
	// Use the global hub variable so debug handler can see workers
	go hub.Run()

	// WebSocket endpoint
	mux.HandleFunc("/ws", WebSocketHandler(hub))
	log.Println("WebSocket endpoint registered at /ws")

	// Start WebSocket server on port 8082
	wsPort := os.Getenv("WS_PORT")
	if wsPort == "" {
		wsPort = "8082"
	}
	wsServer := &http.Server{
		Addr:    ":" + wsPort,
		Handler: WebSocketHandler(hub),
	}

	go func() {
		log.Printf("FORGE v3 WebSocket server starting on :%s", wsPort)
		if err := wsServer.ListenAndServe(); err != http.ErrServerClosed {
			log.Printf("WebSocket server error: %v", err)
		}
	}()

	// Graceful shutdown with 30-second drain window.
	// On SIGINT/SIGTERM:
	//   1. Flip serverHealthy to false so /health returns 503 immediately,
	//      signalling load balancers to stop routing new traffic.
	//   2. Attempt a context-bounded graceful shutdown (30 s).
	//   3. If the timeout expires before all connections drain, force-close.
	go func() {
		sigChan := make(chan os.Signal, 1)
		signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
		sig := <-sigChan

		shutdownStart := time.Now()
		log.Printf("Shutdown signal received (%s). Marking server unhealthy and beginning 30s drain...", sig)

		// Signal the health endpoint to return 503 so load balancers drain us.
		serverHealthy.Store(false)

		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()

		// Shut down both servers concurrently and collect errors.
		httpErr := make(chan error, 1)
		wsErr := make(chan error, 1)
		go func() { httpErr <- server.Shutdown(ctx) }()
		go func() { wsErr <- wsServer.Shutdown(ctx) }()

		httpShutErr := <-httpErr
		wsShutErr := <-wsErr

		elapsed := time.Since(shutdownStart).Round(time.Millisecond)

		if httpShutErr == context.DeadlineExceeded || wsShutErr == context.DeadlineExceeded {
			log.Printf("Graceful shutdown timed out after %s — forcing connection close", elapsed)
			server.Close()
			wsServer.Close()
		} else {
			if httpShutErr != nil {
				log.Printf("HTTP server shutdown error: %v", httpShutErr)
			}
			if wsShutErr != nil {
				log.Printf("WebSocket server shutdown error: %v", wsShutErr)
			}
			log.Printf("Servers shut down cleanly in %s", elapsed)
		}
	}()

	log.Printf("FORGE v3 Status API starting on :%s", port)
	if err := server.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatalf("Server error: %v", err)
	}
}

// pruneOldTasks deletes completed/failed tasks older than the given retention period.
// Called once on daemon startup to keep the tasks table lean.
// Returns the number of rows deleted and any error.
func pruneOldTasks(db *sql.DB, retention time.Duration) (int, error) {
	threshold := time.Now().Add(-retention).Format(time.RFC3339)
	result, err := db.Exec(
		`DELETE FROM tasks WHERE status IN ('completed', 'failed') AND updated_at < ?`,
		threshold,
	)
	if err != nil {
		return 0, err
	}
	n, _ := result.RowsAffected()
	return int(n), nil
}

// announceNodeToPeer POSTs this node's registration to a peer daemon over Tailscale HTTP.
// Called at startup for each entry in KNOWN_PEERS.
func announceNodeToPeer(peerURL string, self Node) {
	body, err := json.Marshal(self)
	if err != nil {
		log.Printf("[XNode] failed to marshal self for peer %s: %v", peerURL, err)
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		peerURL+"/api/xnode/nodes/register", bytes.NewReader(body))
	if err != nil {
		log.Printf("[XNode] failed to build request for peer %s: %v", peerURL, err)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Printf("[XNode] failed to announce to peer %s: %v", peerURL, err)
		return
	}
	defer resp.Body.Close()
	log.Printf("[XNode] announced to peer %s — status %d", peerURL, resp.StatusCode)
}
