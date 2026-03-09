//go:build openclaw
// +build openclaw

// TODO(ADR-031): move to plugin package when plugin system is implemented

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// OpenClawMessage represents an incoming message from OpenClaw gateway
type OpenClawMessage struct {
	From   string `json:"from,omitempty"`
	Text   string `json:"text"`
	ChatID string `json:"chat_id,omitempty"`
}

// OpenClawChatResponse represents the response from /api/openclaw/chat
type OpenClawChatResponse struct {
	TaskID  string `json:"task_id"`
	Message string `json:"message"`
}

// OpenClawStatusResponse represents the response from /api/openclaw/status
type OpenClawStatusResponse struct {
	Daemon        string `json:"daemon"`
	Agents        int    `json:"agents"`
	Busy          int    `json:"busy"`
	TasksQueued   int    `json:"tasks_queued"`
	TasksAssigned int    `json:"tasks_assigned"`
}

// OpenClawIngestPayload represents a Trinity completion payload.
type OpenClawIngestPayload struct {
	TaskID      string                 `json:"task_id"`
	Status      string                 `json:"status"`
	Agent       string                 `json:"agent"`
	Node        string                 `json:"node"`
	Summary     string                 `json:"summary"`
	ResultPath  string                 `json:"result_path"`
	Artifacts   []interface{}          `json:"artifacts"`
	Approval    map[string]interface{} `json:"approval,omitempty"`
	CompletedAt string                 `json:"completed_at,omitempty"`
}

// openclawHandler handles /api/openclaw/* routes
func openclawHandler(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		// Delegate to sub-handlers based on path
		if strings.HasSuffix(r.URL.Path, "/status") {
			openclawStatusHandler(w, r)
		} else if strings.HasSuffix(r.URL.Path, "/events") {
			openclawEventsHandler(w, r)
		} else {
			http.Error(w, "not found", http.StatusNotFound)
		}
	case http.MethodPost:
		if strings.HasSuffix(r.URL.Path, "/chat") {
			openclawChatHandler(w, r)
		} else if strings.HasSuffix(r.URL.Path, "/ingest") {
			openclawIngestHandler(w, r)
		} else {
			http.Error(w, "not found", http.StatusNotFound)
		}
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

// openclawIngestHandler handles POST /api/openclaw/ingest
// Persists Trinity completion payloads durably and emits them through task_events.
func openclawIngestHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var payload OpenClawIngestPayload
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		writeJSONError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if strings.TrimSpace(payload.TaskID) == "" ||
		strings.TrimSpace(payload.Status) == "" ||
		strings.TrimSpace(payload.Agent) == "" ||
		strings.TrimSpace(payload.Node) == "" ||
		strings.TrimSpace(payload.Summary) == "" {
		writeJSONError(w, http.StatusBadRequest, "task_id,status,agent,node,summary are required")
		return
	}
	if payload.CompletedAt == "" {
		payload.CompletedAt = time.Now().UTC().Format(time.RFC3339)
	}
	if payload.ResultPath == "" {
		payload.ResultPath = filepath.ToSlash(filepath.Join(".forge", "results", payload.TaskID+".json"))
	}
	if len(payload.Artifacts) == 0 {
		payload.Artifacts = []interface{}{}
	}

	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}
	resultPath := filepath.Join(forgeRoot, ".forge", "results", payload.TaskID+".json")
	if err := os.MkdirAll(filepath.Dir(resultPath), 0o755); err != nil {
		writeJSONError(w, http.StatusInternalServerError, "failed to create results directory")
		return
	}
	resultJSON, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, "failed to encode payload")
		return
	}
	if err := os.WriteFile(resultPath, resultJSON, 0o644); err != nil {
		writeJSONError(w, http.StatusInternalServerError, "failed to persist result file")
		return
	}

	db := getDBConn()
	if db != nil {
		eventType := "task.completed"
		switch payload.Status {
		case "fail":
			eventType = "task.failed"
		case "needs_approval":
			eventType = "task.needs_approval"
		}
		_, err = db.Exec(
			`INSERT INTO task_events (task_id, domain, project, event_type, payload, created_at)
			 VALUES (?, ?, ?, ?, ?, ?)`,
			payload.TaskID,
			"openclaw",
			"trinity",
			eventType,
			string(resultJSON),
			time.Now().UTC().Format(time.RFC3339),
		)
		if err != nil {
			log.Printf("openclaw: failed to persist completion event: %v", err)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"ok":          true,
		"task_id":     payload.TaskID,
		"status":      payload.Status,
		"result_path": filepath.ToSlash(filepath.Join(".forge", "results", payload.TaskID+".json")),
	})
}

// openclawChatHandler handles POST /api/openclaw/chat
// Parses the message text and creates a task
func openclawChatHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var msg OpenClawMessage
	if err := json.NewDecoder(r.Body).Decode(&msg); err != nil {
		writeJSONError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	if strings.TrimSpace(msg.Text) == "" {
		writeJSONError(w, http.StatusBadRequest, "text required")
		return
	}

	// Parse task title from message
	title := parseTaskFromMessage(msg.Text)

	// Create task with default values for openclaw-sourced tasks
	task := Task{
		ID:       generateTaskID(),
		Title:    title,
		Domain:   "openclaw",
		Project:  "bridge",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}

	// Enqueue the task
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		log.Printf("openclaw: failed to create task: %v", err)
		writeJSONError(w, http.StatusInternalServerError, "failed to create task")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(OpenClawChatResponse{
		TaskID:  task.ID,
		Message: fmt.Sprintf("Task created: %s", task.ID),
	})
}

// parseTaskFromMessage extracts the task title from the message text
// Supports prefixes: "create task:", "task:", "add task:"
func parseTaskFromMessage(text string) string {
	text = strings.TrimSpace(text)
	for _, prefix := range []string{"create task:", "task:", "add task:"} {
		if strings.HasPrefix(strings.ToLower(text), prefix) {
			return strings.TrimSpace(text[len(prefix):])
		}
	}
	// Use full text as title if no prefix found
	return text
}

// openclawStatusHandler handles GET /api/openclaw/status
// Returns daemon status for the bot to forward to Telegram
func openclawStatusHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	if db == nil {
		writeJSONError(w, http.StatusInternalServerError, "database not available")
		return
	}

	// Count total agents
	var totalAgents int
	db.QueryRow("SELECT COUNT(*) FROM agent_heartbeats").Scan(&totalAgents)

	// Count busy agents (those with a current task)
	var busyAgents int
	db.QueryRow("SELECT COUNT(*) FROM agent_heartbeats WHERE current_task_id IS NOT NULL AND current_task_id != ''").Scan(&busyAgents)

	// Count queued tasks
	var tasksQueued int
	db.QueryRow("SELECT COUNT(*) FROM tasks WHERE status = 'queued'").Scan(&tasksQueued)

	// Count assigned tasks
	var tasksAssigned int
	db.QueryRow("SELECT COUNT(*) FROM tasks WHERE status = 'assigned'").Scan(&tasksAssigned)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(OpenClawStatusResponse{
		Daemon:        "ok",
		Agents:        totalAgents,
		Busy:          busyAgents,
		TasksQueued:   tasksQueued,
		TasksAssigned: tasksAssigned,
	})
}

// openclawEventsHandler handles GET /api/openclaw/events
// SSE stream for task status changes and agent state changes
func openclawEventsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	// Send initial connection message
	fmt.Fprintf(w, "event: connected\ndata: {\"status\":\"ok\"}\n\n")
	flusher.Flush()

	// Poll for events every 5 seconds
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	// Track last seen event ID to avoid duplicates
	lastEventID := 0

	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
			db := getDBConn()
			if db == nil {
				continue
			}

			// Get recent task events
			rows, err := db.QueryContext(r.Context(),
				`SELECT id, task_id, event_type, payload, created_at FROM task_events 
				 WHERE id > ? ORDER BY id ASC LIMIT 20`, lastEventID)
			if err != nil {
				continue
			}

			for rows.Next() {
				var eventID int
				var taskID, eventType, createdAt string
				var payloadRaw string
				if err := rows.Scan(&eventID, &taskID, &eventType, &payloadRaw, &createdAt); err == nil {
					lastEventID = eventID
					eventData := map[string]interface{}{
						"event_type": eventType,
						"task_id":    taskID,
						"timestamp":  createdAt,
					}
					if strings.TrimSpace(payloadRaw) != "" {
						var payload interface{}
						if err := json.Unmarshal([]byte(payloadRaw), &payload); err == nil {
							eventData["payload"] = payload
						}
					}
					data, _ := json.Marshal(eventData)
					fmt.Fprintf(w, "event: task_event\ndata: %s\n\n", data)
					if eventType == "task.completed" || eventType == "task.failed" || eventType == "task.needs_approval" {
						fmt.Fprintf(w, "event: completion\ndata: %s\n\n", data)
					}
				}
			}
			rows.Close()

			// Get agent state changes
			agentRows, err := db.QueryContext(r.Context(),
				`SELECT agent_id, status, current_task_id, last_seen FROM agent_heartbeats 
				 WHERE last_seen > datetime('now', '-10 seconds')`)
			if err == nil {
				for agentRows.Next() {
					var agentID, status, taskID, lastSeen string
					if err := agentRows.Scan(&agentID, &status, &taskID, &lastSeen); err == nil {
						data, _ := json.Marshal(map[string]interface{}{
							"agent_id":        agentID,
							"status":          status,
							"current_task_id": taskID,
							"last_seen":       lastSeen,
						})
						fmt.Fprintf(w, "event: agent_update\ndata: %s\n\n", data)
					}
				}
				agentRows.Close()
			}

			flusher.Flush()
		}
	}
}
