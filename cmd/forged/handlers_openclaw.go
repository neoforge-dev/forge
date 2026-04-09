//go:build !tmux_bridge
// +build !tmux_bridge

// ADR-031: will move to plugin package when plugin system is implemented.

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
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

// OpenClawDispatchRequest is the payload for POST /api/openclaw/dispatch.
// OpenClaw is intake-only: it queues a task for the daemon's polling loop
// (forge work --daemon) to assign.  The agent field is intentionally absent
// so that no direct agent-claiming occurs.
type OpenClawDispatchRequest struct {
	Message       string `json:"message"`
	Priority      int    `json:"priority,omitempty"`
	PreferredNode string `json:"preferred_node,omitempty"`
	PreferredRole string `json:"preferred_role,omitempty"`
	SourceChannel string `json:"source_channel,omitempty"`
	ProductKey    string `json:"product_key,omitempty"`
}

// OpenClawDispatchResponse is the response from POST /api/openclaw/dispatch.
type OpenClawDispatchResponse struct {
	TaskID string `json:"task_id"`
	Status string `json:"status"`
}

// OpenClawNotifyRequest is the payload for POST /api/openclaw/notify.
type OpenClawNotifyRequest struct {
	Channel string `json:"channel"`
	Message string `json:"message"`
	Level   string `json:"level,omitempty"`
}

// OpenClawNotifyResponse is the response from POST /api/openclaw/notify.
type OpenClawNotifyResponse struct {
	OK      bool   `json:"ok"`
	Channel string `json:"channel"`
}

// portfolioProduct and portfolioState are defined in stage_gate.go.
// This file uses the extended version from there which includes
// ValidationHypothesis, KillCriteria, DistributionChannel fields.

// portfolioProductJSON is the JSON-enriched view for the portfolio endpoint.
type portfolioProductJSON struct {
	Key                  string  `yaml:"key"                  json:"key"`
	Name                 string  `yaml:"name"                 json:"name"`
	Domain               string  `yaml:"domain"               json:"domain"`
	RepoPath             string  `yaml:"repo_path"            json:"repo_path"`
	Stage                string  `yaml:"stage"                json:"stage"`
	Status               string  `yaml:"status"               json:"status"`
	Owner                string  `yaml:"owner"                json:"owner"`
	ICP                  string  `yaml:"icp"                  json:"icp"`
	CurrentMRR           float64 `yaml:"current_mrr"          json:"current_mrr"`
	TargetMRR            float64 `yaml:"target_mrr"           json:"target_mrr"`
	DeployReady          bool    `yaml:"deploy_ready"         json:"deploy_ready"`
	AnalyticsReady       bool    `yaml:"analytics_ready"      json:"analytics_ready"`
	BillingReady         bool    `yaml:"billing_ready"        json:"billing_ready"`
	NextGate             string  `yaml:"next_gate"            json:"next_gate"`
	NextAction           string  `yaml:"next_action"          json:"next_action"`
	PrimaryMetric        string  `yaml:"primary_metric"       json:"primary_metric"`
	PrimaryRisk          string  `yaml:"primary_risk"         json:"primary_risk"`
	KillCriteria         string  `yaml:"kill_criteria"        json:"kill_criteria,omitempty"`
	DistributionChannel  string  `yaml:"distribution_channel" json:"distribution_channel,omitempty"`
	ValidationHypothesis string  `yaml:"validation_hypothesis" json:"validation_hypothesis,omitempty"`
}

// portfolioStateJSON is the full YAML structure for JSON output.
type portfolioStateJSON struct {
	Version   string                 `yaml:"version"    json:"version"`
	Updated   string                 `yaml:"updated"    json:"updated"`
	NorthStar string                 `yaml:"north_star" json:"north_star"`
	Products  []portfolioProductJSON `yaml:"products"   json:"products"`
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
		} else if strings.HasSuffix(r.URL.Path, "/portfolio") {
			openclawPortfolioHandler(w, r)
		} else {
			http.Error(w, "not found", http.StatusNotFound)
		}
	case http.MethodPost:
		if strings.HasSuffix(r.URL.Path, "/chat") {
			openclawChatHandler(w, r)
		} else if strings.HasSuffix(r.URL.Path, "/ingest") {
			openclawIngestHandler(w, r)
		} else if strings.HasSuffix(r.URL.Path, "/dispatch") {
			openclawDispatchHandler(w, r)
		} else if strings.HasSuffix(r.URL.Path, "/notify") {
			openclawNotifyHandler(w, r)
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
	if len(strings.TrimSpace(msg.Text)) < 5 {
		writeJSONError(w, http.StatusBadRequest, "message too short (min 5 chars)")
		return
	}

	// Parse task title from message
	title := parseTaskFromMessage(msg.Text)

	// Attempt to detect a product domain and task type from the message text so
	// that we can run the stage gate. This is best-effort: if no domain is found
	// the check is skipped. The gate is advisory — it adds a warning to the
	// response but never blocks task creation from the chat interface.
	var openclawStageGateWarning string
	if domainHint, typeHint := extractDomainAndTypeFromMessage(msg.Text); domainHint != "" {
		sgResult := EnforceStageGate(domainHint, typeHint)
		if !sgResult.Allowed {
			openclawStageGateWarning = sgResult.BlockReason
		} else if sgResult.Warning != "" {
			openclawStageGateWarning = sgResult.Warning
		}
	}

	// Create task with default values for openclaw-sourced tasks
	task := Task{
		ID:            generateTaskID(),
		Title:         title,
		Domain:        "openclaw",
		Project:       "bridge",
		Type:          TaskTypeFeature,
		Priority:      5,
		Status:        TaskStatusQueued,
		State:         StateQueued,
		Origin:        "openclaw",
		Requester:     msg.From,
		SourceChannel: "telegram",
	}

	// Enqueue the task
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		log.Printf("openclaw: failed to create task: %v", err)
		writeJSONError(w, http.StatusInternalServerError, "failed to create task")
		return
	}

	responseMsg := fmt.Sprintf("Task created: %s", task.ID)
	if openclawStageGateWarning != "" {
		responseMsg = fmt.Sprintf("Task created: %s [stage gate advisory: %s]", task.ID, openclawStageGateWarning)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(OpenClawChatResponse{
		TaskID:  task.ID,
		Message: responseMsg,
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

// extractDomainAndTypeFromMessage attempts to detect a product domain and task
// type from a free-form chat message. Used for stage gate advisory checks.
//
// Recognised patterns (case-insensitive):
//   - "domain:<value>" or "for <value>" — sets domainHint
//   - "type:<value>" or "task type:<value>" — sets typeHint
//
// If no domain can be detected, domainHint is returned as "".
// If no type can be detected, a default of "feature" is returned.
func extractDomainAndTypeFromMessage(text string) (domainHint, typeHint string) {
	lower := strings.ToLower(text)

	// Extract explicit domain: prefix
	for _, prefix := range []string{"domain:", "for domain:", "project:"} {
		if idx := strings.Index(lower, prefix); idx >= 0 {
			rest := strings.TrimSpace(text[idx+len(prefix):])
			// Take the first word/token as the domain
			parts := strings.Fields(rest)
			if len(parts) > 0 {
				domainHint = strings.Trim(parts[0], ",;.")
				break
			}
		}
	}

	// Extract task type
	typeHint = "feature" // default
	for _, prefix := range []string{"task type:", "type:", "task:"} {
		if idx := strings.Index(lower, prefix); idx >= 0 {
			rest := strings.TrimSpace(text[idx+len(prefix):])
			parts := strings.Fields(rest)
			if len(parts) > 0 {
				typeHint = strings.ToLower(strings.Trim(parts[0], ",;."))
				break
			}
		}
	}

	return domainHint, typeHint
}

// openclawStatusHandler handles GET /api/openclaw/status
// Returns daemon status for the bot to forward to Telegram.
// Uses getFleetCounts for consistency with all other status endpoints.
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

	fc := getFleetCounts(r.Context(), db)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(OpenClawStatusResponse{
		Daemon:        "ok",
		Agents:        fc.TotalAgents,
		Busy:          fc.RunningTasks, // tasks running ≈ busy agent slots
		TasksQueued:   fc.QueuedTasks,
		TasksAssigned: fc.RunningTasks,
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

	// Poll for events every 5 seconds (override via FORGE_OPENCLAW_TICK_MS for tests)
	tickInterval := 5 * time.Second
	if v := os.Getenv("FORGE_OPENCLAW_TICK_MS"); v != "" {
		if ms, err := strconv.Atoi(v); err == nil && ms > 0 {
			tickInterval = time.Duration(ms) * time.Millisecond
		}
	}
	ticker := time.NewTicker(tickInterval)
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

// openclawDispatchHandler handles POST /api/openclaw/dispatch.
//
// OpenClaw is intake-only: it creates a task with origin=openclaw metadata and
// returns immediately.  No agent is claimed; the daemon's polling loop
// (forge work --daemon) is responsible for assignment.
//
// Accepted fields:
//
//	message         — required, becomes the task title
//	priority        — optional 1-10 (default 5)
//	preferred_node  — optional routing hint persisted in task_events
//	preferred_role  — optional routing hint persisted in task_events
//	source_channel  — optional (e.g. "telegram", "api")
//	product_key     — optional portfolio product key
func openclawDispatchHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSONError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	var req OpenClawDispatchRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSONError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	if strings.TrimSpace(req.Message) == "" {
		writeJSONError(w, http.StatusBadRequest, "message is required")
		return
	}
	if len(strings.TrimSpace(req.Message)) < 5 {
		writeJSONError(w, http.StatusBadRequest, "message too short (min 5 chars)")
		return
	}

	priority := req.Priority
	if priority <= 0 || priority > 10 {
		priority = 5
	}

	sourceChannel := req.SourceChannel
	if sourceChannel == "" {
		sourceChannel = "telegram"
	}

	task := Task{
		ID:            generateTaskID(),
		Title:         req.Message,
		Domain:        "openclaw",
		Project:       "dispatch",
		Type:          TaskTypeFeature,
		Priority:      priority,
		Status:        TaskStatusQueued,
		State:         StateQueued,
		Origin:        "openclaw",
		SourceChannel: sourceChannel,
	}

	if taskQueue == nil {
		writeJSONError(w, http.StatusInternalServerError, "task queue not available")
		return
	}

	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		log.Printf("openclaw/dispatch: failed to enqueue task: %v", err)
		writeJSONError(w, http.StatusInternalServerError, "failed to create task")
		return
	}

	// Persist openclaw-specific metadata as a task_events record so the polling
	// loop can read routing hints without requiring a schema change on tasks.
	db := getDBConn()
	if db != nil {
		meta := map[string]interface{}{
			"origin":         "openclaw",
			"preferred_node": req.PreferredNode,
			"preferred_role": req.PreferredRole,
			"source_channel": req.SourceChannel,
			"product_key":    req.ProductKey,
		}
		metaJSON, _ := json.Marshal(meta)
		_, err := db.Exec(
			`INSERT INTO task_events (task_id, domain, project, event_type, payload, created_at)
			 VALUES (?, ?, ?, ?, ?, ?)`,
			task.ID,
			"openclaw",
			"dispatch",
			"task.queued.openclaw",
			string(metaJSON),
			time.Now().UTC().Format(time.RFC3339),
		)
		if err != nil {
			log.Printf("openclaw/dispatch: failed to persist origin metadata for task %s: %v", task.ID, err)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(OpenClawDispatchResponse{
		TaskID: task.ID,
		Status: "queued",
	})
}

// openclawNotifyHandler handles POST /api/openclaw/notify.
// Logs the notification and persists it to task_events.
//
// TODO: add Telegram/webhook channel integration when bot token is wired.
func openclawNotifyHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSONError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	var req OpenClawNotifyRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSONError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	if strings.TrimSpace(req.Message) == "" {
		writeJSONError(w, http.StatusBadRequest, "message is required")
		return
	}

	channel := strings.TrimSpace(req.Channel)
	if channel == "" {
		channel = "log"
	}

	level := strings.TrimSpace(req.Level)
	if level == "" {
		level = "info"
	}

	log.Printf("openclaw/notify [%s] [%s]: %s", channel, level, req.Message)

	db := getDBConn()
	if db != nil {
		payload, _ := json.Marshal(map[string]interface{}{
			"channel": channel,
			"level":   level,
			"message": req.Message,
		})
		_, err := db.Exec(
			`INSERT INTO task_events (task_id, domain, project, event_type, payload, created_at)
			 VALUES (?, ?, ?, ?, ?, ?)`,
			"openclaw-notify",
			"openclaw",
			"notify",
			"openclaw.notify",
			string(payload),
			time.Now().UTC().Format(time.RFC3339),
		)
		if err != nil {
			log.Printf("openclaw/notify: failed to persist event: %v", err)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(OpenClawNotifyResponse{
		OK:      true,
		Channel: channel,
	})
}

// openclawPortfolioHandler handles GET /api/openclaw/portfolio.
// Returns the full portfolio state from config/portfolio/portfolio-state.yaml as JSON,
// augmented with computed fields total_mrr and blocked_count.
func openclawPortfolioHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSONError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}

	yamlPath := filepath.Join(forgeRoot, "config", "portfolio", "portfolio-state.yaml")
	data, err := os.ReadFile(yamlPath)
	if err != nil {
		log.Printf("openclaw/portfolio: failed to read portfolio-state.yaml: %v", err)
		writeJSONError(w, http.StatusInternalServerError, "failed to read portfolio state")
		return
	}

	var state portfolioStateJSON
	if err := yaml.Unmarshal(data, &state); err != nil {
		log.Printf("openclaw/portfolio: failed to parse portfolio-state.yaml: %v", err)
		writeJSONError(w, http.StatusInternalServerError, "failed to parse portfolio state")
		return
	}

	// Compute derived fields.
	var totalMRR float64
	var blockedCount int
	for _, p := range state.Products {
		totalMRR += p.CurrentMRR
		if strings.TrimSpace(p.NextGate) != "" {
			blockedCount++
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"version":       state.Version,
		"updated":       state.Updated,
		"north_star":    state.NorthStar,
		"products":      state.Products,
		"total_mrr":     totalMRR,
		"blocked_count": blockedCount,
	})
}
