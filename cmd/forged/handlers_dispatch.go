//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// configHandler handles GET/PUT requests for /config
func configHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	config := map[string]interface{}{
		"api_version": "v3", "server_time": time.Now().Format(time.RFC3339),
		"database_type": "sqlite", "websocket_port": 8082,
		"features": map[string]bool{
			"tasks": true, "agents": true, "projects": true,
			"contexts": true, "lanes": true, "workers": true,
			"notifications": true, "gitguard": true, "patrols": true,
		},
	}
	if r.Method == http.MethodGet {
		json.NewEncoder(w).Encode(map[string]interface{}{"config": config})
		return
	}
	if r.Method == http.MethodPut {
		var updates map[string]interface{}
		if err := json.NewDecoder(r.Body).Decode(&updates); err != nil {
			http.Error(w, "invalid request body", http.StatusBadRequest)
			return
		}
		for k, v := range updates {
			config[k] = v
		}
		json.NewEncoder(w).Encode(map[string]interface{}{"status": "updated", "config": config})
		return
	}
	http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
}

// dispatchHandler handles dispatch operations
func dispatchHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	if r.Method == http.MethodGet {
		rows, err := getDBConn().Query(`
			SELECT id, task_id, type, status, created_at
			FROM git_guard_actions
			ORDER BY created_at DESC
			LIMIT 50
		`)
		if err != nil {
			http.Error(w, fmt.Sprintf("failed to query: %v", err), http.StatusInternalServerError)
			return
		}
		defer rows.Close()

		var dispatches []map[string]interface{}
		for rows.Next() {
			var id, taskID, actionType, status string
			var createdAt time.Time
			if err := rows.Scan(&id, &taskID, &actionType, &status, &createdAt); err != nil {
				continue
			}
			dispatches = append(dispatches, map[string]interface{}{
				"id": id, "task_id": taskID, "type": actionType,
				"status": status, "created_at": createdAt,
			})
		}
		json.NewEncoder(w).Encode(map[string]interface{}{"dispatches": dispatches, "count": len(dispatches)})
		return
	}

	if r.Method == http.MethodPost {
		var req struct {
			TaskID  string `json:"task_id"`
			AgentID string `json:"agent_id"`
			Message string `json:"message"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid request body", http.StatusBadRequest)
			return
		}
		dispatchID := fmt.Sprintf("dispatch-%d", time.Now().UnixNano())
		_, err := getDBConn().Exec(`
			INSERT INTO git_guard_actions (id, task_id, type, payload_hash, status, created_at)
			VALUES (?, ?, 'commit', ?, 'pending', ?)
		`, dispatchID, req.TaskID, dispatchID, time.Now())
		if err != nil {
			http.Error(w, fmt.Sprintf("failed to create dispatch: %v", err), http.StatusInternalServerError)
			return
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status": "created", "dispatch_id": dispatchID, "task_id": req.TaskID, "agent_id": req.AgentID,
		})
		return
	}
	http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
}
