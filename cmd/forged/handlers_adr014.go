//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"os"
	"time"
)

// handleLeadState returns the orchestrator FSM state from the database
// GET /api/lead-state - Returns current orchestrator state summary
func handleLeadState(db *sql.DB) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		ctx := r.Context()

		// Get task summary by state
		rows, err := db.QueryContext(ctx, `
			SELECT state, COUNT(*) as count 
			FROM tasks 
			GROUP BY state
		`)
		if err != nil {
			http.Error(w, "failed to query task states", http.StatusInternalServerError)
			return
		}
		defer rows.Close()

		taskStates := make(map[string]int)
		for rows.Next() {
			var state string
			var count int
			if err := rows.Scan(&state, &count); err != nil {
				continue
			}
			taskStates[state] = count
		}

		// Get agent summary by status
		agentRows, err := db.QueryContext(ctx, `
			SELECT status, COUNT(*) as count 
			FROM agents 
			GROUP BY status
		`)
		if err != nil {
			http.Error(w, "failed to query agent status", http.StatusInternalServerError)
			return
		}
		defer agentRows.Close()

		agentStatus := make(map[string]int)
		for agentRows.Next() {
			var status string
			var count int
			if err := agentRows.Scan(&status, &count); err != nil {
				continue
			}
			agentStatus[status] = count
		}

		// Get pending approvals count
		var pendingApprovals int
		err = db.QueryRowContext(ctx, `
			SELECT COUNT(*) FROM approvals WHERE status = 'pending'
		`).Scan(&pendingApprovals)
		if err != nil {
			pendingApprovals = 0
		}

		// Get queue depth
		var queueDepth int
		err = db.QueryRowContext(ctx, `
			SELECT COUNT(*) FROM tasks WHERE state = 'queued'
		`).Scan(&queueDepth)
		if err != nil {
			queueDepth = 0
		}

		response := map[string]interface{}{
			"timestamp": time.Now().UTC().Format(time.RFC3339),
			"node_id":   os.Getenv("FORGE_NODE_ID"),
			"tasks": map[string]interface{}{
				"by_state":    taskStates,
				"queue_depth": queueDepth,
			},
			"agents": map[string]interface{}{
				"by_status": agentStatus,
			},
			"approvals": map[string]interface{}{
				"pending": pendingApprovals,
			},
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(response)
	}
}

// handleAuthLogin handles login requests
// POST /api/auth/login - Validates webhook token and returns session info
func handleAuthLogin(auth *AuthManager) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		var req struct {
			Token string `json:"token"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid request body", http.StatusBadRequest)
			return
		}

		// Validate against FORGE_WEBHOOK_TOKEN env var
		expectedToken := os.Getenv("FORGE_WEBHOOK_TOKEN")
		if expectedToken == "" {
			http.Error(w, "authentication not configured", http.StatusServiceUnavailable)
			return
		}

		if req.Token != expectedToken {
			http.Error(w, "invalid credentials", http.StatusUnauthorized)
			return
		}

		// Generate a session token
		sessionToken, err := auth.GenerateToken("login-session", []string{"read", "write"})
		if err != nil {
			http.Error(w, "failed to create session", http.StatusInternalServerError)
			return
		}

		response := map[string]interface{}{
			"token":      sessionToken,
			"token_type": "bearer",
			"expires_in": 86400, // 24 hours
			"node_id":    os.Getenv("FORGE_NODE_ID"),
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(response)
	}
}

// handleAuthMe returns current authentication info
// GET /api/auth/me - Returns current node and auth info
func handleAuthMe(auth *AuthManager) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		authMode := "local"
		if auth != nil {
			authMode = auth.config.Mode
		}

		response := map[string]interface{}{
			"node_id":   os.Getenv("FORGE_NODE_ID"),
			"node_name": os.Getenv("FORGE_NODE_NAME"),
			"auth_mode": authMode,
			"version":   "v3.0",
			"timestamp": time.Now().UTC().Format(time.RFC3339),
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(response)
	}
}

// handleAuthRefresh handles token refresh requests
// POST /api/auth/refresh - Refreshes an existing valid token
func handleAuthRefresh(auth *AuthManager) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		// For now, just generate a new token
		// In production, would validate the old token first
		newToken, err := auth.GenerateToken("refresh-session", []string{"read", "write"})
		if err != nil {
			http.Error(w, "failed to refresh token", http.StatusInternalServerError)
			return
		}

		response := map[string]interface{}{
			"token":      newToken,
			"token_type": "bearer",
			"expires_in": 86400,
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(response)
	}
}
