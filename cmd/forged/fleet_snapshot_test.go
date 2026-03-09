package main

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

func TestFleetSnapshotHandler(t *testing.T) {
	// Setup in-memory DB
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	defer db.Close()

	// Create agent_heartbeats and tasks tables
	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS agent_heartbeats (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			agent_id TEXT NOT NULL UNIQUE,
			node TEXT NOT NULL,
			status TEXT NOT NULL DEFAULT 'idle',
			current_task_id TEXT,
			context_pct REAL DEFAULT 0,
			capabilities TEXT,
			last_seen TEXT NOT NULL DEFAULT (datetime('now')),
			connected_at TEXT NOT NULL DEFAULT (datetime('now'))
		);
		CREATE TABLE IF NOT EXISTS tasks (
			id TEXT PRIMARY KEY,
			domain TEXT NOT NULL,
			project TEXT NOT NULL,
			type TEXT NOT NULL,
			status TEXT DEFAULT 'requested'
		);
	`)
	if err != nil {
		t.Fatalf("failed to create tables: %v", err)
	}

	// Insert 2 test agents in different nodes
	_, err = db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, current_task_id, context_pct, last_seen)
		VALUES ('agent-a', 'prya', 'idle', NULL, 0, datetime('now')),
		       ('agent-b', 'sati', 'active', 'task-1', 45.5, datetime('now'))
	`)
	if err != nil {
		t.Fatalf("failed to insert agents: %v", err)
	}

	origDB := getDBConn()
	setDBConn(db)
	defer func() { setDBConn(origDB) }()

	req, err := http.NewRequest("GET", "/api/fleet/snapshot", nil)
	if err != nil {
		t.Fatalf("failed to create request: %v", err)
	}
	rr := httptest.NewRecorder()

	fleetSnapshotHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d. Body: %s", rr.Code, rr.Body.String())
	}
	if ct := rr.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("expected Content-Type application/json, got %s", ct)
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	totalAgents, _ := resp["total_agents"].(float64)
	if totalAgents != 2 {
		t.Errorf("expected total_agents == 2, got %v", totalAgents)
	}

	nodes, ok := resp["nodes"].([]interface{})
	if !ok || len(nodes) == 0 {
		t.Errorf("expected nodes not empty, got %v", resp["nodes"])
	}
}
