//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"encoding/json"
	"net/http"
)

// writeJSON sets Content-Type and encodes data as JSON.
// Use in place of: w.Header().Set("Content-Type", "application/json") + json.NewEncoder(w).Encode(...)
func writeJSON(w http.ResponseWriter, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(data); err != nil {
		http.Error(w, "encoding error", http.StatusInternalServerError)
	}
}

// requireMethod checks the request method and returns false (with 405) if it doesn't match.
// Use in place of: if r.Method != http.MethodPost { http.Error(...); return }
func requireMethod(w http.ResponseWriter, r *http.Request, method string) bool {
	if r.Method != method {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return false
	}
	return true
}

// withDB gets the DB connection and calls fn, or returns 503 if DB is unavailable.
// Use in place of: db := getDBConn(); if db == nil { http.Error(..., 503); return }
func withDB(w http.ResponseWriter, fn func(db *sql.DB)) {
	db := getDBConn()
	if db == nil {
		http.Error(w, "database unavailable", http.StatusServiceUnavailable)
		return
	}
	fn(db)
}
