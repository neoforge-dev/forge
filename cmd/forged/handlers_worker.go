//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"
)

// workersHandler handles GET requests for /workers
func workersHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Get workers from WebSocket hub
	workers := hub.ListWorkers()

	// Build worker details
	var workerList = []map[string]interface{}{}
	for _, workerID := range workers {
		if worker, exists := hub.workers[workerID]; exists {
			workerList = append(workerList, map[string]interface{}{
				"id":        workerID,
				"status":    "connected",
				"last_seen": worker.lastHeartbeat().Format(time.RFC3339),
			})
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"workers": workerList,
		"count":   len(workerList),
	})
}

// workerByIDHandler returns details for a specific worker
func workerByIDHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	path := r.URL.Path
	id := strings.TrimPrefix(path, "/workers/")
	if strings.HasPrefix(path, "/api/workers/") {
		id = strings.TrimPrefix(path, "/api/workers/")
	}

	if id == "" {
		http.Error(w, "missing worker id", http.StatusBadRequest)
		return
	}

	worker, exists := hub.workers[id]
	if !exists {
		http.Error(w, "worker not found", http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"id":        id,
		"status":    "connected",
		"last_seen": worker.lastHeartbeat().Format(time.RFC3339),
	})
}
