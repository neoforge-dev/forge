//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"
)

// PWA-compatible message models

type PWAMessage struct {
	ID        string  `json:"id"`
	ThreadID  string  `json:"thread_id"`
	FromAgent string  `json:"from_agent"`
	ToAgent   *string `json:"to_agent"`
	Content   string  `json:"content"`
	Status    string  `json:"status"`
	CreatedAt string  `json:"created_at"`
	ReadAt    *string `json:"read_at"`
}

func messagesHandler(w http.ResponseWriter, r *http.Request) {
	db := getDBConn()
	if db == nil {
		writeJSONError(w, http.StatusServiceUnavailable, "database not available")
		return
	}

	w.Header().Set("Content-Type", "application/json")

	switch r.Method {
	case http.MethodGet:
		// List messages
		rows, err := db.QueryContext(r.Context(), "SELECT id, thread_id, from_agent, to_agent, content, status, created_at, read_at FROM messages ORDER BY created_at DESC LIMIT 100")
		if err != nil {
			writeJSONError(w, http.StatusInternalServerError, err.Error())
			return
		}
		defer rows.Close()

		var messages []PWAMessage
		for rows.Next() {
			var m PWAMessage
			var toAgent, readAt *string
			err := rows.Scan(&m.ID, &m.ThreadID, &m.FromAgent, &toAgent, &m.Content, &m.Status, &m.CreatedAt, &readAt)
			if err == nil {
				m.ToAgent = toAgent
				m.ReadAt = readAt
				messages = append(messages, m)
			}
		}
		if messages == nil {
			messages = []PWAMessage{}
		}
		json.NewEncoder(w).Encode(map[string]interface{}{"success": true, "data": messages})

	case http.MethodPost:
		// Send message
		var req struct {
			ThreadID  string `json:"thread_id"`
			FromAgent string `json:"from_agent"`
			ToAgent   string `json:"to_agent"`
			Content   string `json:"content"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSONError(w, http.StatusBadRequest, "invalid body")
			return
		}

		id := generateULID()
		if req.ThreadID == "" {
			req.ThreadID = id // New thread
		}

		_, err := db.ExecContext(r.Context(), "INSERT INTO messages (id, thread_id, from_agent, to_agent, content, status, created_at) VALUES (?, ?, ?, ?, ?, 'sent', datetime('now'))",
			id, req.ThreadID, req.FromAgent, req.ToAgent, req.Content)
		if err != nil {
			writeJSONError(w, http.StatusInternalServerError, err.Error())
			return
		}

		json.NewEncoder(w).Encode(map[string]interface{}{
			"success": true,
			"data": PWAMessage{
				ID:        id,
				ThreadID:  req.ThreadID,
				FromAgent: req.FromAgent,
				ToAgent:   &req.ToAgent,
				Content:   req.Content,
				Status:    "sent",
				CreatedAt: time.Now().UTC().Format(time.RFC3339),
			},
		})

	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func messageByIDHandler(w http.ResponseWriter, r *http.Request) {
	// Parse path: /api/messages/{message_id} or /api/messages/{message_id}/read
	path := strings.TrimPrefix(r.URL.Path, "/api/messages/")
	parts := strings.Split(path, "/")
	if len(parts) == 0 || parts[0] == "" {
		http.Error(w, "message ID required", http.StatusBadRequest)
		return
	}

	id := parts[0]
	action := ""
	if len(parts) > 1 {
		action = parts[1]
	}

	db := getDBConn()
	if db == nil {
		writeJSONError(w, http.StatusServiceUnavailable, "database not available")
		return
	}

	w.Header().Set("Content-Type", "application/json")

	if action == "read" && r.Method == http.MethodPost {
		_, err := db.ExecContext(r.Context(), "UPDATE messages SET status = 'read', read_at = datetime('now') WHERE id = ?", id)
		if err != nil {
			writeJSONError(w, http.StatusInternalServerError, err.Error())
			return
		}
		json.NewEncoder(w).Encode(map[string]interface{}{"success": true})
		return
	}

	if r.Method == http.MethodGet {
		var m PWAMessage
		var toAgent, readAt *string
		err := db.QueryRowContext(r.Context(), "SELECT id, thread_id, from_agent, to_agent, content, status, created_at, read_at FROM messages WHERE id = ?", id).
			Scan(&m.ID, &m.ThreadID, &m.FromAgent, &toAgent, &m.Content, &m.Status, &m.CreatedAt, &readAt)
		if err != nil {
			writeJSONError(w, http.StatusNotFound, "message not found")
			return
		}
		m.ToAgent = toAgent
		m.ReadAt = readAt
		json.NewEncoder(w).Encode(map[string]interface{}{"success": true, "data": m})
		return
	}

	http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
}
