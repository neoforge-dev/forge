//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"
)

// agentCooldownsHandler handles GET /api/agents/cooldowns — list all active cooldowns.
func agentCooldownsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	entries, err := GetCooldownAgents(db)
	if err != nil {
		http.Error(w, `{"error":"failed to get cooldowns: `+err.Error()+`"}`, http.StatusInternalServerError)
		return
	}
	if entries == nil {
		entries = []CooldownEntry{}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"count":     len(entries),
		"cooldowns": entries,
	})
}

// agentCooldownHandler handles POST/DELETE /api/agents/{id}/cooldown.
func agentCooldownHandler(w http.ResponseWriter, r *http.Request) {
	db := getDBConn()
	if db == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	// Extract agent ID from path: /api/agents/{id}/cooldown
	path := strings.TrimPrefix(r.URL.Path, "/api/agents/")
	agentID := strings.TrimSuffix(path, "/cooldown")
	if agentID == "" {
		http.Error(w, `{"error":"agent ID required"}`, http.StatusBadRequest)
		return
	}

	switch r.Method {
	case http.MethodPost:
		var req struct {
			Reason          string `json:"reason"`
			DurationSeconds int    `json:"duration_seconds"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, `{"error":"invalid JSON body"}`, http.StatusBadRequest)
			return
		}
		if req.Reason == "" {
			req.Reason = "manual"
		}
		if req.DurationSeconds <= 0 {
			req.DurationSeconds = 300 // default 5 minutes
		}
		duration := time.Duration(req.DurationSeconds) * time.Second
		if err := SetAgentCooldown(db, agentID, req.Reason, duration); err != nil {
			http.Error(w, `{"error":"failed to set cooldown: `+err.Error()+`"}`, http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status":    "cooldown_set",
			"agent_id":  agentID,
			"reason":    req.Reason,
			"duration_s": req.DurationSeconds,
		})

	case http.MethodDelete:
		if err := ClearAgentCooldown(db, agentID); err != nil {
			http.Error(w, `{"error":"failed to clear cooldown: `+err.Error()+`"}`, http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status":   "cooldown_cleared",
			"agent_id": agentID,
		})

	case http.MethodGet:
		inCooldown, err := IsAgentInCooldown(db, agentID)
		if err != nil {
			http.Error(w, `{"error":"failed to check cooldown: `+err.Error()+`"}`, http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"agent_id":    agentID,
			"in_cooldown": inCooldown,
		})

	default:
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
	}
}
