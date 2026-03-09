//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
)

// contextsHandler handles GET /api/contexts
func contextsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Parse query parameters
	agentID := r.URL.Query().Get("agent_id")
	domain := r.URL.Query().Get("domain")
	project := r.URL.Query().Get("project")
	taskID := r.URL.Query().Get("task_id")

	// Build query
	query := "SELECT id, agent_id, domain, project, task_id, summary, content, created_at, expires_at FROM context_envelopes WHERE 1=1"
	args := []interface{}{}

	if agentID != "" {
		query += " AND agent_id = ?"
		args = append(args, agentID)
	}
	if domain != "" {
		query += " AND domain = ?"
		args = append(args, domain)
	}
	if project != "" {
		query += " AND project = ?"
		args = append(args, project)
	}
	if taskID != "" {
		query += " AND task_id = ?"
		args = append(args, taskID)
	}

	query += " ORDER BY created_at DESC"

	rows, err := getDBConn().Query(query, args...)
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to query contexts: %v", err), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type Context struct {
		ID        string `json:"id"`
		AgentID   string `json:"agent_id"`
		Domain    string `json:"domain"`
		Project   string `json:"project"`
		TaskID    string `json:"task_id"`
		Summary   string `json:"summary"`
		Content   string `json:"content"`
		CreatedAt string `json:"created_at"`
		ExpiresAt string `json:"expires_at"`
	}

	contexts := []Context{}
	for rows.Next() {
		var c Context
		var summary, content, createdAt, expiresAt sql.NullString
		err := rows.Scan(&c.ID, &c.AgentID, &c.Domain, &c.Project, &c.TaskID, &summary, &content, &createdAt, &expiresAt)
		if err != nil {
			http.Error(w, fmt.Sprintf("failed to scan context: %v", err), http.StatusInternalServerError)
			return
		}
		if summary.Valid {
			c.Summary = summary.String
		}
		if content.Valid {
			c.Content = content.String
		}
		if createdAt.Valid {
			c.CreatedAt = createdAt.String
		}
		if expiresAt.Valid {
			c.ExpiresAt = expiresAt.String
		}
		contexts = append(contexts, c)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"contexts": contexts,
		"count":    len(contexts),
	})
}

func contextByIDHandler(w http.ResponseWriter, r *http.Request) {
	contextManager.EnvelopeByIDHandler(w, r)
}
