//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
)

// lanesHandler handles GET /api/lanes
func lanesHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	rows, err := getDBConn().Query("SELECT id, name, description, capabilities, auto_claim, max_parallel, timeout_minutes, created_at FROM lanes")
	if err != nil {
		http.Error(w, fmt.Sprintf("failed to query lanes: %v", err), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type Lane struct {
		ID             string `json:"id"`
		Name           string `json:"name"`
		Description    string `json:"description"`
		Capabilities   string `json:"capabilities"`
		AutoClaim      bool   `json:"auto_claim"`
		MaxParallel    int    `json:"max_parallel"`
		TimeoutMinutes int    `json:"timeout_minutes"`
		CreatedAt      string `json:"created_at"`
	}

	lanes := []Lane{}
	for rows.Next() {
		var l Lane
		var desc, caps, createdAt sql.NullString
		var autoClaim bool
		err := rows.Scan(&l.ID, &l.Name, &desc, &caps, &autoClaim, &l.MaxParallel, &l.TimeoutMinutes, &createdAt)
		if err != nil {
			http.Error(w, fmt.Sprintf("failed to scan lane: %v", err), http.StatusInternalServerError)
			return
		}
		if desc.Valid {
			l.Description = desc.String
		}
		if caps.Valid {
			l.Capabilities = caps.String
		}
		if createdAt.Valid {
			l.CreatedAt = createdAt.String
		}
		l.AutoClaim = autoClaim
		lanes = append(lanes, l)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"lanes": lanes,
		"count": len(lanes),
	})
}

// laneByIDHandler returns details for a specific lane
func laneByIDHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	path := r.URL.Path
	id := strings.TrimPrefix(path, "/lanes/")
	if strings.HasPrefix(path, "/api/lanes/") {
		id = strings.TrimPrefix(path, "/api/lanes/")
	}

	if id == "" {
		http.Error(w, "missing lane id", http.StatusBadRequest)
		return
	}

	type Lane struct {
		ID             string `json:"id"`
		Name           string `json:"name"`
		Description    string `json:"description"`
		Capabilities   string `json:"capabilities"`
		AutoClaim      bool   `json:"auto_claim"`
		MaxParallel    int    `json:"max_parallel"`
		TimeoutMinutes int    `json:"timeout_minutes"`
		CreatedAt      string `json:"created_at"`
	}

	var l Lane
	var desc, caps, createdAt sql.NullString
	err := getDBConn().QueryRow("SELECT id, name, description, capabilities, auto_claim, max_parallel, timeout_minutes, created_at FROM lanes WHERE id = ?", id).Scan(
		&l.ID, &l.Name, &desc, &caps, &l.AutoClaim, &l.MaxParallel, &l.TimeoutMinutes, &createdAt)

	if err == sql.ErrNoRows {
		http.Error(w, "lane not found", http.StatusNotFound)
		return
	} else if err != nil {
		http.Error(w, fmt.Sprintf("failed to query lane: %v", err), http.StatusInternalServerError)
		return
	}

	if desc.Valid {
		l.Description = desc.String
	}
	if caps.Valid {
		l.Capabilities = caps.String
	}
	if createdAt.Valid {
		l.CreatedAt = createdAt.String
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(l)
}
