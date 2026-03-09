//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// AgentHeartbeat represents an agent's current status (used by agentByID, health, list).
type AgentHeartbeat struct {
	AgentID      string    `json:"agent_id"`
	Node         string    `json:"node"`
	Status       string    `json:"status"`
	CurrentTask  *string   `json:"current_task_id,omitempty"`
	ContextPct   float64   `json:"context_pct"`
	Capabilities []string  `json:"capabilities,omitempty"`
	LastSeen     time.Time `json:"last_seen"`
	ConnectedAt  time.Time `json:"connected_at"`
}

func agentContextHandler(w http.ResponseWriter, r *http.Request) {
	agentID := strings.TrimPrefix(r.URL.Path, "/api/agents/")
	agentID = strings.TrimSuffix(agentID, "/context")
	agentID = sanitizeID(agentID)
	if agentID == "" {
		http.Error(w, "agent ID required", http.StatusBadRequest)
		return
	}
	if !isValidID(agentID) {
		http.Error(w, "invalid agent ID format", http.StatusBadRequest)
		return
	}

	domain, err := findDomainForAgent(agentID)
	if err != nil {
		// Try legacy sidecar
		legacyPath := filepath.Join(forgeRoot(), ".forge", "heartbeat", "context_percent")
		if data, err := os.ReadFile(legacyPath); err == nil {
			if pct, err := parsePct(string(data)); err == nil {
				info, _ := os.Stat(legacyPath)
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(AgentContextResponse{
					AgentID:     agentID,
					ContextPct:  pct,
					LastUpdated: info.ModTime().Format(time.RFC3339),
				})
				return
			}
		}
		// Graceful degradation: return 200 with context_pct 0 for unknown agents
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(AgentContextResponse{
			AgentID:     agentID,
			ContextPct:  0,
			LastUpdated: time.Now().Format(time.RFC3339),
		})
		return
	}

	pct, lastUpdated, err := parseContextFromDomain(domain)
	if err != nil {
		http.Error(w, "failed to parse context", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(AgentContextResponse{
		AgentID:     agentID,
		ContextPct:  pct,
		LastUpdated: lastUpdated.Format(time.RFC3339),
	})
}

// agentHeartbeatReceive receives agent heartbeats from the field
func agentHeartbeatReceive(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	agentID := strings.TrimPrefix(r.URL.Path, "/api/agents/")
	agentID = strings.TrimSuffix(agentID, "/heartbeat")
	agentID = sanitizeID(agentID)

	if agentID == "" {
		http.Error(w, "agent ID required", http.StatusBadRequest)
		return
	}

	var hbRequest struct {
		AgentID    string  `json:"agent_id"`
		Node       string  `json:"node"`
		Status     string  `json:"status"`
		TaskID     string  `json:"task_id,omitempty"`
		Progress   int     `json:"progress"`
		Context    float64 `json:"context"`
		ContextPct float64 `json:"context_pct"`
	}

	if err := json.NewDecoder(r.Body).Decode(&hbRequest); err != nil {
		hbRequest.Status = "unknown"
	}

	if hbRequest.AgentID != "" && hbRequest.AgentID != agentID {
		http.Error(w, "agent ID mismatch", http.StatusBadRequest)
		return
	}

	// Accept context_pct as alias for context
	if hbRequest.Context == 0 && hbRequest.ContextPct > 0 {
		hbRequest.Context = hbRequest.ContextPct
	}

	// Resolve node: request body > NODE_ID env > os.Hostname()
	node := hbRequest.Node
	if node == "" {
		node = os.Getenv("NODE_ID")
	}
	if node == "" {
		if h, err := os.Hostname(); err == nil {
			node = h
		} else {
			node = "unknown"
		}
	}

	hb := AgentHeartbeat{
		AgentID:     agentID,
		Status:      hbRequest.Status,
		Node:        node,
		ContextPct:  hbRequest.Context,
		LastSeen:    time.Now(),
		ConnectedAt: time.Now(),
	}

	if hbRequest.TaskID != "" {
		hb.CurrentTask = &hbRequest.TaskID
	}

	if hub != nil {
		hub.mu.Lock()
		if worker, exists := hub.workers[agentID]; exists {
			worker.lastPong = time.Now()
		}
		hub.mu.Unlock()
	}

	log.Printf("💓 Heartbeat from %s: status=%s, task=%s, progress=%d%%, context=%.1f%%",
		agentID, hb.Status, hbRequest.TaskID, hbRequest.Progress, hbRequest.Context)

	if hbRequest.Context > 70 {
		log.Printf("⚠️ Agent %s context critical: %.1f%%", agentID, hbRequest.Context)
	} else if hbRequest.Context > 50 {
		log.Printf("⚡ Agent %s context warning: %.1f%%", agentID, hbRequest.Context)
	}

	if stateMachine != nil && hbRequest.TaskID != "" {
		if currentState, err := stateMachine.GetState(hbRequest.TaskID); err == nil {
			if currentState == StateDispatched {
				if err := stateMachine.Transition(hbRequest.TaskID, StateDispatched, StateRunning, "agent heartbeat received"); err != nil {
					log.Printf("WARN: FSM DISPATCHED→RUNNING failed for task %s: %v", hbRequest.TaskID, err)
				}
			}
		}
	}

	ctx := r.Context()
	taskID := ""
	if hbRequest.TaskID != "" {
		taskID = hbRequest.TaskID
	}

	err := UpdateAgentHeartbeat(agentID, hb.Node, hb.Status, taskID, hbRequest.Context)
	if err != nil {
		log.Printf("WARN: failed to update agent_heartbeats for %s: %v", agentID, err)
	}

	// Default status to 'idle' if empty (DB CHECK constraint requires 'idle', 'busy', or 'wrapup')
	status := hb.Status
	if status == "" {
		status = "idle"
	}

	db := getDBConn()
	if db != nil {
		metadata := map[string]interface{}{"progress": hbRequest.Progress}
		metadataJSON, _ := json.Marshal(metadata)
		_, err = db.ExecContext(ctx,
			`INSERT INTO agent_telemetry (agent_id, node, context_pct, task_id, status, tool, metadata, timestamp)
			 VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))`,
			agentID, hb.Node, hbRequest.Context, taskID, status, "http", string(metadataJSON),
		)
		if err != nil {
			log.Printf("telemetry insert warning: %v", err)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status":    "received",
		"agent_id":  agentID,
		"timestamp": time.Now().Format(time.RFC3339),
	})
}

// agentTelemetryHandler returns recent telemetry rows for an agent (ADR-027).
func agentTelemetryHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	agentID := strings.TrimPrefix(r.URL.Path, "/api/agents/")
	agentID = strings.TrimSuffix(agentID, "/telemetry")
	agentID = sanitizeID(agentID)
	if agentID == "" {
		http.Error(w, "agent ID required", http.StatusBadRequest)
		return
	}
	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		if n, err := strconv.Atoi(l); err == nil && n > 0 && n <= 500 {
			limit = n
		}
	}
	db := getDBConn()
	if db == nil {
		http.Error(w, "db not available", http.StatusServiceUnavailable)
		return
	}
	rows, err := db.QueryContext(r.Context(),
		`SELECT agent_id, node, context_pct, task_id, status, tool, metadata, timestamp
		 FROM agent_telemetry WHERE agent_id = ?
		 ORDER BY timestamp DESC LIMIT ?`, agentID, limit)
	if err != nil {
		http.Error(w, "query failed", http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	type row struct {
		AgentID    string  `json:"agent_id"`
		Node       string  `json:"node"`
		ContextPct float64 `json:"context_pct"`
		TaskID     string  `json:"task_id"`
		Status     string  `json:"status"`
		Tool       string  `json:"tool"`
		Metadata   string  `json:"metadata"`
		Timestamp  string  `json:"timestamp"`
	}
	var results []row
	for rows.Next() {
		var r row
		if err := rows.Scan(&r.AgentID, &r.Node, &r.ContextPct, &r.TaskID, &r.Status, &r.Tool, &r.Metadata, &r.Timestamp); err == nil {
			results = append(results, r)
		}
	}
	if results == nil {
		results = []row{}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"agent_id": agentID,
		"count":    len(results),
		"rows":     results,
	})
}

// agentMetricsHandler returns metrics history for an agent (ADR-014 CC retirement).
func agentMetricsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	agentID := strings.TrimPrefix(r.URL.Path, "/api/agents/")
	agentID = strings.TrimSuffix(agentID, "/metrics")
	agentID = sanitizeID(agentID)
	if agentID == "" {
		http.Error(w, "agent ID required", http.StatusBadRequest)
		return
	}
	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		if n, err := strconv.Atoi(l); err == nil && n > 0 {
			if n > 200 {
				n = 200
			}
			limit = n
		}
	}
	var since string
	if s := r.URL.Query().Get("since"); s != "" {
		if _, err := time.Parse(time.RFC3339, s); err == nil {
			since = s
		}
	}
	db := getDBConn()
	if db == nil {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"agent_id": agentID,
			"count":    0,
			"metrics":  []interface{}{},
		})
		return
	}
	query := `SELECT id, agent_id, node, context_pct, timestamp
		  FROM agent_telemetry WHERE agent_id = ?`
	args := []interface{}{agentID}
	if since != "" {
		query += ` AND timestamp > ?`
		args = append(args, since)
	}
	query += ` ORDER BY timestamp DESC LIMIT ?`
	args = append(args, limit)
	rows, err := db.QueryContext(r.Context(), query, args...)
	if err != nil {
		http.Error(w, "query failed", http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	type metricRow struct {
		ID         int64   `json:"id"`
		AgentID    string  `json:"agent_id"`
		Node       string  `json:"node"`
		ContextPct float64 `json:"context_pct"`
		Timestamp  string  `json:"recorded_at"`
	}
	var metrics []map[string]interface{}
	for rows.Next() {
		var m metricRow
		if err := rows.Scan(&m.ID, &m.AgentID, &m.Node, &m.ContextPct, &m.Timestamp); err != nil {
			continue
		}
		metrics = append(metrics, map[string]interface{}{
			"id":              m.ID,
			"agent_id":        m.AgentID,
			"node":            m.Node,
			"context_pct":     m.ContextPct,
			"tasks_completed": 0,
			"tasks_failed":    0,
			"uptime_seconds":  0,
			"recorded_at":     m.Timestamp,
		})
	}
	if metrics == nil {
		metrics = []map[string]interface{}{}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"agent_id": agentID,
		"count":    len(metrics),
		"metrics":  metrics,
	})
}

// agentTelemetrySummaryHandler returns the latest telemetry row per agent (ADR-027).
func agentTelemetrySummaryHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	db := getDBConn()
	if db == nil {
		http.Error(w, "db not available", http.StatusServiceUnavailable)
		return
	}
	rows, err := db.QueryContext(r.Context(),
		`SELECT t.agent_id, t.node, t.context_pct, t.task_id, t.status, t.tool, t.timestamp
		 FROM agent_telemetry t
		 INNER JOIN (
		     SELECT agent_id, MAX(timestamp) AS max_ts
		     FROM agent_telemetry
		     GROUP BY agent_id
		 ) latest ON t.agent_id = latest.agent_id AND t.timestamp = latest.max_ts
		 ORDER BY t.agent_id`)
	if err != nil {
		http.Error(w, "query failed", http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	type agentRow struct {
		AgentID    string  `json:"agent_id"`
		Node       string  `json:"node"`
		Status     string  `json:"status"`
		ContextPct float64 `json:"context_pct"`
		TaskID     string  `json:"task_id"`
		Tool       string  `json:"tool"`
		Timestamp  string  `json:"timestamp"`
	}
	var agents []agentRow
	for rows.Next() {
		var row agentRow
		if err := rows.Scan(&row.AgentID, &row.Node, &row.ContextPct, &row.TaskID, &row.Status, &row.Tool, &row.Timestamp); err == nil {
			agents = append(agents, row)
		}
	}
	if agents == nil {
		agents = []agentRow{}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"summary_at":  time.Now().UTC().Format(time.RFC3339),
		"agent_count": len(agents),
		"agents":      agents,
	})
}

// agentsHandler handles GET requests for /agents
func agentsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	agents, err := getAllAgentsHealth()
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to list agents: %v", err), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"agents": agents,
		"count":  len(agents),
	})
}

// agentsSSEHandler handles GET /api/agents/stream for real-time SSE updates
func agentsSSEHandler(w http.ResponseWriter, r *http.Request) {
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

	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
			db := getDBConn()
			if db != nil {
				rows, err := db.QueryContext(r.Context(),
					`SELECT agent_id, node, status, context_pct, current_task_id, last_seen
					 FROM agent_heartbeats ORDER BY last_seen DESC LIMIT 50`)
				if err == nil {
					for rows.Next() {
						var agentID, node, status, taskID, lastSeen string
						var ctxPct float64
						if err := rows.Scan(&agentID, &node, &status, &ctxPct, &taskID, &lastSeen); err == nil {
							data, _ := json.Marshal(map[string]interface{}{
								"agent_id":    agentID,
								"node":        node,
								"status":      status,
								"context_pct": ctxPct,
								"task_id":     taskID,
								"last_seen":   lastSeen,
							})
							fmt.Fprintf(w, "event: agent_update\ndata: %s\n\n", data)
						}
					}
					rows.Close()
				}
			}
			fmt.Fprintf(w, "event: heartbeat\ndata: {\"ts\":\"%s\"}\n\n", time.Now().Format(time.RFC3339))
			flusher.Flush()
		}
	}
}

// agentByIDHandler returns details for a specific agent
func agentByIDHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	path := r.URL.Path
	id := strings.TrimPrefix(path, "/agents/")
	if strings.HasPrefix(path, "/api/agents/") {
		id = strings.TrimPrefix(path, "/api/agents/")
	}
	id = strings.Split(id, "/")[0]

	if id == "" {
		http.Error(w, "missing agent id", http.StatusBadRequest)
		return
	}

	var a AgentHeartbeat
	var currentTask sql.NullString
	var capabilities sql.NullString
	var lastSeen, connectedAt string

	err := getDBConn().QueryRow(`
		SELECT agent_id, node, status, current_task_id, context_pct, capabilities, last_seen, connected_at
		FROM agent_heartbeats
		WHERE agent_id = ?`, id).Scan(&a.AgentID, &a.Node, &a.Status, &currentTask, &a.ContextPct, &capabilities, &lastSeen, &connectedAt)

	if err == sql.ErrNoRows {
		http.Error(w, "agent not found", http.StatusNotFound)
		return
	} else if err != nil {
		http.Error(w, fmt.Sprintf("failed to query agent: %v", err), http.StatusInternalServerError)
		return
	}

	if currentTask.Valid {
		a.CurrentTask = &currentTask.String
	}
	if capabilities.Valid {
		a.Capabilities = strings.Split(capabilities.String, ",")
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(a)
}

// agentsListHandler returns JSON list of all connected agents
func agentsListHandler(w http.ResponseWriter, r *http.Request) {
	hub.mu.RLock()
	defer hub.mu.RUnlock()

	agents := make([]map[string]interface{}, 0, len(hub.workers))
	for id, worker := range hub.workers {
		agents = append(agents, map[string]interface{}{
			"id":        id,
			"node":      worker.Node,
			"tier":      worker.Tier,
			"status":    "connected",
			"last_pong": worker.lastPong,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"agents": agents,
		"count":  len(agents),
	})
}

func agentsHealthHandler(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/agents/")

	if path == "" {
		agents, err := getAllAgentsHealth()
		if err != nil {
			http.Error(w, fmt.Sprintf("failed to get agents: %v", err), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"agents": agents,
			"count":  len(agents),
		})
		return
	}

	agentID := strings.TrimSuffix(path, "/health")
	health, err := getAgentHealth(agentID)
	if err != nil {
		http.Error(w, "agent not found", http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(health)
}

func getAllAgentsHealth() ([]AgentHeartbeat, error) {
	rows, err := getDBConn().Query(`
		SELECT agent_id, node, status, current_task_id, context_pct, capabilities, last_seen, connected_at
		FROM agent_heartbeats
		ORDER BY last_seen DESC
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	liveWorkers := make(map[string]bool)
	if hub != nil {
		for _, id := range hub.ListWorkers() {
			liveWorkers[id] = true
		}
	}

	var agents []AgentHeartbeat
	seenInDB := make(map[string]bool)
	for rows.Next() {
		var a AgentHeartbeat
		var currentTask sql.NullString
		var capabilities sql.NullString
		var lastSeen, connectedAt string

		err := rows.Scan(&a.AgentID, &a.Node, &a.Status, &currentTask, &a.ContextPct, &capabilities, &lastSeen, &connectedAt)
		if err != nil {
			continue
		}

		if currentTask.Valid {
			a.CurrentTask = &currentTask.String
		}
		if capabilities.Valid {
			a.Capabilities = strings.Split(capabilities.String, ",")
		}
		a.LastSeen, _ = time.Parse("2006-01-02 15:04:05", lastSeen)
		a.ConnectedAt, _ = time.Parse("2006-01-02 15:04:05", connectedAt)

		if liveWorkers[a.AgentID] {
			a.Status = "connected"
			a.LastSeen = time.Now().UTC()
		}

		seenInDB[a.AgentID] = true
		agents = append(agents, a)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	now := time.Now().UTC()
	for id := range liveWorkers {
		if !seenInDB[id] {
			agents = append(agents, AgentHeartbeat{
				AgentID:     id,
				Node:        "unknown",
				Status:      "connected",
				LastSeen:    now,
				ConnectedAt: now,
			})
		}
	}

	return agents, nil
}

func getAgentHealth(agentID string) (*AgentHeartbeat, error) {
	var a AgentHeartbeat
	var currentTask sql.NullString
	var capabilities sql.NullString
	var lastSeen, connectedAt string

	err := getDBConn().QueryRow(`
		SELECT agent_id, node, status, current_task_id, context_pct, capabilities, last_seen, connected_at
		FROM agent_heartbeats
		WHERE agent_id = ?
	`, agentID).Scan(&a.AgentID, &a.Node, &a.Status, &currentTask, &a.ContextPct, &capabilities, &lastSeen, &connectedAt)

	if err != nil {
		return nil, err
	}

	if currentTask.Valid {
		a.CurrentTask = &currentTask.String
	}
	if capabilities.Valid {
		a.Capabilities = strings.Split(capabilities.String, ",")
	}
	a.LastSeen, _ = time.Parse("2006-01-02 15:04:05", lastSeen)
	a.ConnectedAt, _ = time.Parse("2006-01-02 15:04:05", connectedAt)

	return &a, nil
}

// nodeCapabilitiesHandler serves GET /api/fleet/node-capabilities.
func nodeCapabilitiesHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	nodeID := nodeIDFromEnv()

	ceiling, ok := NodeHardCeilings[nodeID]
	if !ok {
		ceiling = 2
	}

	forbidden := make([]string, 0)
	if f, ok := ForbiddenAgentTypes[nodeID]; ok {
		for agentType := range f {
			forbidden = append(forbidden, agentType)
		}
	}

	allAgents := []string{"kimi", "minimax", "pi", "gemini", "claude", "cursor", "amp", "opencode", "kilo"}
	tierMap := make(map[string]string, len(allAgents))
	for _, a := range allAgents {
		tierMap[a] = agentTier(a)
	}

	resp := map[string]interface{}{
		"node_id":             nodeID,
		"hard_ceiling":        ceiling,
		"forbidden_agents":    forbidden,
		"agent_tiers":         tierMap,
		"medium_auto_approve": mediumTierAutoApproveNodes[nodeID],
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(resp); err != nil {
		log.Printf("[nodeCapabilities] encode error: %v", err)
	}
}

func parityHandler(db *sql.DB) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		res, err := ParityCheck(db)
		if err != nil {
			http.Error(w, fmt.Sprintf("Parity check failed: %v", err), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(res)
	}
}

// agentFleetSummaryResponse is the simple flat response for agentFleetSummaryHandler_V2.
type agentFleetSummaryResponse struct {
	TotalAgents         int `json:"total_agents"`
	ActiveAgents        int `json:"active_agents"`
	IdleAgents          int `json:"idle_agents"`
	OfflineAgents       int `json:"offline_agents"`
	TasksQueued         int `json:"tasks_queued"`
	TasksAssigned       int `json:"tasks_assigned"`
	TasksCompletedToday int `json:"tasks_completed_today"`
	NodeCount           int `json:"node_count"`
}

// agentFleetSummaryHandler_V2 serves a simplified GET /api/fleet/summary shape.
// The canonical PWA handler is fleetSummaryHandler in handlers_pwa_bridge.go.
func agentFleetSummaryHandler_V2(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, "database not available", http.StatusServiceUnavailable)
		return
	}

	ctx := r.Context()
	cutoff := time.Now().UTC().Add(-5 * time.Minute).Format("2006-01-02 15:04:05")

	var summary agentFleetSummaryResponse
	db.QueryRowContext(ctx, `
		SELECT
			COUNT(*),
			COALESCE(SUM(CASE WHEN last_seen > ? THEN 1 ELSE 0 END), 0),
			COALESCE(SUM(CASE WHEN last_seen > ? AND (current_task_id IS NULL OR current_task_id = '') THEN 1 ELSE 0 END), 0),
			COALESCE(SUM(CASE WHEN last_seen <= ? THEN 1 ELSE 0 END), 0),
			COUNT(DISTINCT node)
		FROM agent_heartbeats
	`, cutoff, cutoff, cutoff).Scan(
		&summary.TotalAgents, &summary.ActiveAgents, &summary.IdleAgents,
		&summary.OfflineAgents, &summary.NodeCount,
	)
	db.QueryRowContext(ctx, `
		SELECT
			COALESCE(SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END), 0),
			COALESCE(SUM(CASE WHEN status = 'assigned' THEN 1 ELSE 0 END), 0),
			COALESCE(SUM(CASE WHEN status = 'completed' AND updated_at >= date('now') THEN 1 ELSE 0 END), 0)
		FROM tasks
	`).Scan(&summary.TasksQueued, &summary.TasksAssigned, &summary.TasksCompletedToday)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(summary)
}

