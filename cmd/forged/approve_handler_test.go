package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

func TestApproveTaskHandler(t *testing.T) {
	// Setup in-memory DB
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	defer db.Close()

	// Setup schema
	_, err = db.Exec(`
		CREATE TABLE tasks (
			id TEXT PRIMARY KEY,
			domain TEXT NOT NULL,
			project TEXT NOT NULL,
			type TEXT NOT NULL,
			priority INTEGER DEFAULT 0,
			status TEXT DEFAULT 'requested',
			state TEXT DEFAULT 'QUEUED',
			lane TEXT,
			assigned_to TEXT,
			plan_version INTEGER DEFAULT 0,
			plan_id TEXT,
			envelope_id TEXT,
			created_at TEXT DEFAULT (datetime('now')),
			updated_at TEXT DEFAULT (datetime('now'))
		);

		CREATE TABLE task_state_transitions (
			id TEXT PRIMARY KEY,
			task_id TEXT NOT NULL,
			from_state TEXT,
			to_state TEXT NOT NULL,
			reason TEXT,
			transitioned_at TEXT NOT NULL DEFAULT (datetime('now')),
			FOREIGN KEY (task_id) REFERENCES tasks(id)
		);
	`)
	if err != nil {
		t.Fatalf("failed to create tables: %v", err)
	}

	setDBConn(db)
	store := NewTaskStore(db)
	stateMachine = NewStateMachine(store, db)

	// Create a test task in COMPLETED state
	taskID := "test-task-approve-1"
	_, err = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state) 
		VALUES (?, 'test', 'test', 'feature', 1, 'completed', 'COMPLETED')`, taskID)
	if err != nil {
		t.Fatalf("Failed to create test task: %v", err)
	}

	// 1. Test Successful Approval
	body := map[string]string{
		"approved_by": "test-user",
		"note":        "looks good",
	}
	jsonBody, _ := json.Marshal(body)
	req, _ := http.NewRequest("POST", "/api/tasks/"+taskID+"/approve", bytes.NewBuffer(jsonBody))
	rr := httptest.NewRecorder()

	approveTaskHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("Expected status 200, got %d. Body: %s", rr.Code, rr.Body.String())
	}

	// Verify state in DB
	var state string
	err = db.QueryRow("SELECT state FROM tasks WHERE id = ?", taskID).Scan(&state)
	if err != nil {
		t.Fatalf("Failed to query task state: %v", err)
	}
	if state != "APPROVED" {
		t.Errorf("Expected state APPROVED, got %s", state)
	}

	// 2. Test Invalid Transition (from QUEUED)
	taskID2 := "test-task-queued-1"
	_, err = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state) 
		VALUES (?, 'test', 'test', 'feature', 1, 'queued', 'QUEUED')`, taskID2)
	if err != nil {
		t.Fatalf("Failed to create test task 2: %v", err)
	}

	req2, _ := http.NewRequest("POST", "/api/tasks/"+taskID2+"/approve", bytes.NewBuffer(jsonBody))
	rr2 := httptest.NewRecorder()
	approveTaskHandler(rr2, req2)

	if rr2.Code != http.StatusConflict {
		t.Errorf("Expected status 409 Conflict for invalid transition, got %d", rr2.Code)
	}
}
