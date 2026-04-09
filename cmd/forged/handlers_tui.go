//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"
)

// LogEntry represents a single log entry for TUI
type LogEntry struct {
	Timestamp string `json:"timestamp"`
	Level     string `json:"level"`
	Message   string `json:"message"`
	Source    string `json:"source,omitempty"`
}

// TUI logs handler - returns recent log entries
func tuiLogsHandler(w http.ResponseWriter, r *http.Request) {
	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		if parsed, err := fmt.Sscanf(l, "%d", &limit); err == nil && parsed > 0 {
			if limit > 100 {
				limit = 100
			}
		}
	}

	var workers []string
	if hub != nil {
		workers = hub.ListWorkers()
	}
	var entries []LogEntry
	now := time.Now().UTC()

	entries = append(entries, LogEntry{
		Timestamp: now.Format(time.RFC3339),
		Level:     "info",
		Message:   "Server running",
		Source:    "server",
	})

	for _, worker := range workers {
		entries = append(entries, LogEntry{
			Timestamp: now.Format(time.RFC3339),
			Level:     "info",
			Message:   fmt.Sprintf("Agent connected: %s", worker),
			Source:    "agent",
		})
	}

	if taskQueue != nil {
		entries = append(entries, LogEntry{
			Timestamp: now.Format(time.RFC3339),
			Level:     "info",
			Message:   "Task queue initialized",
			Source:    "queue",
		})
	}

	inboxCount := 0
	if dirEntries, err := os.ReadDir(XNodeInboxDir); err == nil {
		inboxCount = len(dirEntries)
		entries = append(entries, LogEntry{
			Timestamp: now.Format(time.RFC3339),
			Level:     "info",
			Message:   fmt.Sprintf("XNode inboxes: %d nodes", inboxCount),
			Source:    "xnode",
		})
	}

	if len(entries) > limit {
		entries = entries[len(entries)-limit:]
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"logs":   entries,
		"total":  len(entries),
		"limit":  limit,
		"source": "forged",
	})
}

type PlanRequest struct {
	Plan   string `json:"plan"`
	Reason string `json:"reason"`
}

func planTaskHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	taskID := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	taskID = strings.TrimSuffix(taskID, "/plan")
	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}

	var req PlanRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	planID, err := planManager.CreatePlan(context.Background(), taskID, req.Plan, req.Reason)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to create plan: %v", err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"plan_id": planID, "status": string(TaskStatusPlanned)})
}

func replanTaskHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	taskID := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	taskID = strings.TrimSuffix(taskID, "/replan")
	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}

	var req PlanRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	planID, err := planManager.RevisePlan(context.Background(), taskID, req.Plan, req.Reason)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to revise plan: %v", err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"plan_id": planID, "status": string(TaskStatusPlanned)})
}

func getPlanHistoryHandler(w http.ResponseWriter, r *http.Request) {
	taskID := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	taskID = strings.TrimSuffix(taskID, "/plans")
	if taskID == "" {
		http.Error(w, "task ID required", http.StatusBadRequest)
		return
	}

	history, err := planManager.GetPlanHistory(context.Background(), taskID)
	if err != nil {
		http.Error(w, "failed to get plan history", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(history)
}
