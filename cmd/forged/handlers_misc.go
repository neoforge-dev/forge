//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"
)

type HealthResponse struct {
	Status    string `json:"status"`
	Timestamp string `json:"timestamp"`
}

type StatusResponse struct {
	Version string `json:"version"`
	Phase   string `json:"phase"`
	Status  string `json:"status"`
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	resp := HealthResponse{
		Status:    "ok",
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

func statusHandler(w http.ResponseWriter, r *http.Request) {
	resp := StatusResponse{
		Version: "3.0.0",
		Phase:   "0.5",
		Status:  "running",
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// qualityGatesHandler handles POST /api/tasks/:id/quality-gates
func qualityGatesHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	taskID := extractTaskID(r.URL.Path, "/quality-gates")
	taskID = sanitizeID(taskID)
	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, "db not initialized", http.StatusServiceUnavailable)
		return
	}

	var existingID string
	if err := db.QueryRow(`SELECT id FROM tasks WHERE id = ?`, taskID).Scan(&existingID); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(NewNotFoundError("task", taskID))
		return
	}

	var req struct {
		TestPassRate float64 `json:"test_pass_rate"`
		CoveragePct  float64 `json:"coverage_pct"`
		LintIssues   int     `json:"lint_issues"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	if req.TestPassRate < 0.0 || req.TestPassRate > 1.0 {
		http.Error(w, "test_pass_rate must be between 0.0 and 1.0", http.StatusBadRequest)
		return
	}
	if req.CoveragePct < 0.0 || req.CoveragePct > 100.0 {
		http.Error(w, "coverage_pct must be between 0.0 and 100.0", http.StatusBadRequest)
		return
	}
	if req.LintIssues < 0 {
		http.Error(w, "lint_issues must be >= 0", http.StatusBadRequest)
		return
	}

	result, err := db.Exec(
		`INSERT INTO quality_gate_results (task_id, test_pass_rate, coverage_pct, lint_issues, created_at)
		 VALUES (?, ?, ?, ?, datetime('now'))`,
		taskID, req.TestPassRate, req.CoveragePct, req.LintIssues,
	)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to insert quality gate result: %v", err), http.StatusInternalServerError)
		return
	}

	insertedID, err := result.LastInsertId()
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to retrieve inserted id: %v", err), http.StatusInternalServerError)
		return
	}

	var row struct {
		ID           int64   `json:"id"`
		TaskID       string  `json:"task_id"`
		TestPassRate float64 `json:"test_pass_rate"`
		CoveragePct  float64 `json:"coverage_pct"`
		LintIssues   int     `json:"lint_issues"`
		CreatedAt    string  `json:"created_at"`
	}
	if err := db.QueryRow(
		`SELECT id, task_id, test_pass_rate, coverage_pct, lint_issues, created_at
		 FROM quality_gate_results WHERE id = ?`, insertedID,
	).Scan(&row.ID, &row.TaskID, &row.TestPassRate, &row.CoveragePct, &row.LintIssues, &row.CreatedAt); err != nil {
		http.Error(w, fmt.Sprintf("failed to read back inserted row: %v", err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(row)
}

// dashboardThroughputHandler returns task throughput metrics for the dashboard.
func dashboardThroughputHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	hours := 24
	if h := r.URL.Query().Get("hours"); h != "" {
		if parsed, err := strconv.Atoi(h); err == nil && parsed > 0 {
			hours = parsed
		}
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, "db not initialized", http.StatusServiceUnavailable)
		return
	}

	var total, completed, failed, inProgress int
	db.QueryRow(`
		SELECT COUNT(*) FROM tasks
		WHERE created_at > datetime('now', ?)
	`, fmt.Sprintf("-%d hours", hours)).Scan(&total)
	db.QueryRow(`
		SELECT COUNT(*) FROM tasks
		WHERE status = 'completed' AND updated_at > datetime('now', ?)
	`, fmt.Sprintf("-%d hours", hours)).Scan(&completed)
	db.QueryRow(`
		SELECT COUNT(*) FROM tasks
		WHERE status IN ('failed', 'abandoned') AND updated_at > datetime('now', ?)
	`, fmt.Sprintf("-%d hours", hours)).Scan(&failed)
	db.QueryRow(`
		SELECT COUNT(*) FROM tasks
		WHERE status IN ('assigned', 'executing') AND updated_at > datetime('now', ?)
	`, fmt.Sprintf("-%d hours", hours)).Scan(&inProgress)

	rows, err := db.Query(`
		SELECT strftime('%Y-%m-%d %H:00', created_at) as hour, COUNT(*) as count
		FROM tasks
		WHERE created_at > datetime('now', ?)
		GROUP BY hour
		ORDER BY hour DESC
		LIMIT ?
	`, fmt.Sprintf("-%d hours", hours), hours)
	if err != nil {
		log.Printf("[dashboard] hourly breakdown error: %v", err)
	}

	hourly := make([]map[string]interface{}, 0)
	if rows != nil {
		defer rows.Close()
		for rows.Next() {
			var hour string
			var count int
			if err := rows.Scan(&hour, &count); err == nil {
				hourly = append(hourly, map[string]interface{}{
					"hour":  hour,
					"count": count,
				})
			}
		}
	}

	throughputPerHour := 0.0
	if hours > 0 {
		throughputPerHour = float64(completed) / float64(hours)
	}

	response := map[string]interface{}{
		"hours":               hours,
		"total_today":         total,
		"completed_today":     completed,
		"failed_today":        failed,
		"in_progress_today":   inProgress,
		"throughput_per_hour": throughputPerHour,
		"hourly_breakdown":    hourly,
		"timestamp":           time.Now().Format(time.RFC3339),
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func releaseTaskHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	taskID := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	taskID = strings.TrimSuffix(taskID, "/release")

	var req struct {
		AgentID string `json:"agent_id"`
		Reason  string `json:"reason"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request", http.StatusBadRequest)
		return
	}

	ctx := context.Background()
	task, err := taskQueue.GetTask(ctx, taskID)
	if err != nil {
		http.Error(w, "task not found", http.StatusNotFound)
		return
	}

	if task.AssignedTo != req.AgentID {
		http.Error(w, "task not assigned to this agent", http.StatusForbidden)
		return
	}

	err = taskQueue.ReleaseTask(ctx, taskID, req.AgentID, req.Reason)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status":  "released",
		"task_id": taskID,
		"reason":  req.Reason,
	})
}

func extendLeaseHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	taskID := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	taskID = strings.TrimSuffix(taskID, "/extend-lease")

	var req struct {
		AgentID string `json:"agent_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request", http.StatusBadRequest)
		return
	}

	ctx := context.Background()
	err := taskQueue.ExtendLease(ctx, taskID, req.AgentID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status":     "lease_extended",
		"task_id":    taskID,
		"expires_at": time.Now().Add(5 * time.Minute).Format(time.RFC3339),
	})
}
