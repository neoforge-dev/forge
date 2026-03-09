//go:build !tmux_bridge
// +build !tmux_bridge

package main

// ADR-014: Relay delivery endpoints — minimal Go equivalent of the Python
// harness relay.py. Backs the /api/relay/* routes with SQLite storage.
//
//   GET  /api/relay/deliveries    — list relay deliveries
//   POST /api/relay/dispatch      — create a relay delivery record
//   POST /api/relay/{id}/ack      — mark delivery as acknowledged

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"
)

// RelayDelivery mirrors the relay_deliveries table row.
type RelayDelivery struct {
	ID        string  `json:"id"`
	AgentID   string  `json:"agent_id"`
	Message   string  `json:"message"`
	Status    string  `json:"status"`
	CreatedAt string  `json:"created_at"`
	AckedAt   *string `json:"acked_at,omitempty"`
}

// RelayDispatchRequest is the POST /api/relay/dispatch body.
type RelayDispatchRequest struct {
	AgentID string `json:"agent_id"`
	Message string `json:"message"`
}

// relayDeliveriesHandler handles GET /api/relay/deliveries.
// Returns all relay delivery records, newest first.
func relayDeliveriesHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, `{"error":"db not ready"}`, http.StatusServiceUnavailable)
		return
	}

	rows, err := db.QueryContext(r.Context(),
		`SELECT id, agent_id, message, status, created_at, acked_at
		 FROM relay_deliveries ORDER BY created_at DESC LIMIT 500`)
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":%q}`, err.Error()), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	deliveries := []RelayDelivery{}
	for rows.Next() {
		var d RelayDelivery
		if err := rows.Scan(&d.ID, &d.AgentID, &d.Message, &d.Status, &d.CreatedAt, &d.AckedAt); err != nil {
			continue
		}
		deliveries = append(deliveries, d)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"deliveries": deliveries,
		"count":      len(deliveries),
	})
}

// relayDispatchHandler handles POST /api/relay/dispatch.
// Creates a new relay delivery record in status "pending".
func relayDispatchHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, `{"error":"db not ready"}`, http.StatusServiceUnavailable)
		return
	}

	var req RelayDispatchRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, `{"error":"invalid JSON body"}`, http.StatusBadRequest)
		return
	}
	if req.AgentID == "" || req.Message == "" {
		http.Error(w, `{"error":"agent_id and message are required"}`, http.StatusBadRequest)
		return
	}

	id := uuid.New().String()
	now := time.Now().UTC().Format(time.RFC3339)

	_, err := db.ExecContext(r.Context(),
		`INSERT INTO relay_deliveries (id, agent_id, message, status, created_at)
		 VALUES (?, ?, ?, 'pending', ?)`,
		id, req.AgentID, req.Message, now)
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":%q}`, err.Error()), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"id":       id,
		"agent_id": req.AgentID,
		"status":   "pending",
	})
}

// relayAckHandler handles POST /api/relay/{id}/ack.
// Marks a delivery record as acknowledged.
func relayAckHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	db := getDBConn()
	if db == nil {
		http.Error(w, `{"error":"db not ready"}`, http.StatusServiceUnavailable)
		return
	}

	// Extract id from /api/relay/{id}/ack
	path := strings.TrimPrefix(r.URL.Path, "/api/relay/")
	id := strings.TrimSuffix(path, "/ack")
	if id == "" {
		http.Error(w, `{"error":"missing delivery id"}`, http.StatusBadRequest)
		return
	}

	now := time.Now().UTC().Format(time.RFC3339)
	res, err := db.ExecContext(r.Context(),
		`UPDATE relay_deliveries SET status='acked', acked_at=? WHERE id=? AND status='pending'`,
		now, id)
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":%q}`, err.Error()), http.StatusInternalServerError)
		return
	}

	n, _ := res.RowsAffected()
	if n == 0 {
		// Check if it exists at all
		var exists int
		_ = db.QueryRowContext(r.Context(), `SELECT COUNT(*) FROM relay_deliveries WHERE id=?`, id).Scan(&exists)
		if exists == 0 {
			http.Error(w, `{"error":"delivery not found"}`, http.StatusNotFound)
			return
		}
		http.Error(w, `{"error":"delivery not in pending state"}`, http.StatusConflict)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"id":     id,
		"status": "acked",
	})
}

// relayPrefixHandler is the catch-all for /api/relay/ prefix routes.
// Routes /api/relay/{id}/ack to relayAckHandler.
func relayPrefixHandler(w http.ResponseWriter, r *http.Request) {
	if strings.HasSuffix(r.URL.Path, "/ack") {
		relayAckHandler(w, r)
		return
	}
	http.NotFound(w, r)
}
