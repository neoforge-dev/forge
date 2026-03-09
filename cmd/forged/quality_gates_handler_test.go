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

func setupQualityGateDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
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

		CREATE TABLE quality_gate_results (
			id          INTEGER PRIMARY KEY AUTOINCREMENT,
			task_id     TEXT    NOT NULL,
			test_pass_rate  REAL    NOT NULL DEFAULT 0.0,
			coverage_pct    REAL    NOT NULL DEFAULT 0.0,
			lint_issues     INTEGER NOT NULL DEFAULT 0,
			created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
		);
	`)
	if err != nil {
		t.Fatalf("failed to create tables: %v", err)
	}
	setDBConn(db)
	return db
}

func TestQualityGatesHandler_Success(t *testing.T) {
	db := setupQualityGateDB(t)
	defer db.Close()

	taskID := "test-task-qg-1"
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state)
		VALUES (?, 'test', 'test', 'feature', 1, 'completed', 'COMPLETED')`, taskID)
	if err != nil {
		t.Fatalf("failed to insert test task: %v", err)
	}

	body := map[string]interface{}{
		"test_pass_rate": 0.95,
		"coverage_pct":   82.5,
		"lint_issues":    3,
	}
	jsonBody, _ := json.Marshal(body)
	req, _ := http.NewRequest("POST", "/api/tasks/"+taskID+"/quality-gates", bytes.NewBuffer(jsonBody))
	rr := httptest.NewRecorder()

	qualityGatesHandler(rr, req)

	if rr.Code != http.StatusCreated {
		t.Fatalf("expected status 201, got %d. Body: %s", rr.Code, rr.Body.String())
	}

	var result map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&result); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if result["task_id"] != taskID {
		t.Errorf("expected task_id %q, got %v", taskID, result["task_id"])
	}
	if result["test_pass_rate"] != 0.95 {
		t.Errorf("expected test_pass_rate 0.95, got %v", result["test_pass_rate"])
	}
	if result["coverage_pct"] != 82.5 {
		t.Errorf("expected coverage_pct 82.5, got %v", result["coverage_pct"])
	}
	if result["lint_issues"] == nil {
		t.Error("expected lint_issues in response")
	}
	if result["created_at"] == nil {
		t.Error("expected created_at in response")
	}
	if result["id"] == nil {
		t.Error("expected id in response")
	}
}

func TestQualityGatesHandler_TaskNotFound(t *testing.T) {
	db := setupQualityGateDB(t)
	defer db.Close()

	body := map[string]interface{}{
		"test_pass_rate": 0.9,
		"coverage_pct":   80.0,
		"lint_issues":    1,
	}
	jsonBody, _ := json.Marshal(body)
	req, _ := http.NewRequest("POST", "/api/tasks/NONEXISTENT/quality-gates", bytes.NewBuffer(jsonBody))
	rr := httptest.NewRecorder()

	qualityGatesHandler(rr, req)

	if rr.Code != http.StatusNotFound {
		t.Errorf("expected status 404, got %d. Body: %s", rr.Code, rr.Body.String())
	}
}

func TestQualityGatesHandler_MethodNotAllowed(t *testing.T) {
	db := setupQualityGateDB(t)
	defer db.Close()

	req, _ := http.NewRequest("GET", "/api/tasks/some-task/quality-gates", nil)
	rr := httptest.NewRecorder()

	qualityGatesHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

func TestQualityGatesHandler_ValidationErrors(t *testing.T) {
	db := setupQualityGateDB(t)
	defer db.Close()

	taskID := "test-task-qg-val"
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state)
		VALUES (?, 'test', 'test', 'feature', 1, 'completed', 'COMPLETED')`, taskID)

	cases := []struct {
		name string
		body map[string]interface{}
	}{
		{"test_pass_rate too high", map[string]interface{}{"test_pass_rate": 1.5, "coverage_pct": 50.0, "lint_issues": 0}},
		{"test_pass_rate negative", map[string]interface{}{"test_pass_rate": -0.1, "coverage_pct": 50.0, "lint_issues": 0}},
		{"coverage_pct too high", map[string]interface{}{"test_pass_rate": 1.0, "coverage_pct": 101.0, "lint_issues": 0}},
		{"coverage_pct negative", map[string]interface{}{"test_pass_rate": 1.0, "coverage_pct": -1.0, "lint_issues": 0}},
		{"lint_issues negative", map[string]interface{}{"test_pass_rate": 1.0, "coverage_pct": 50.0, "lint_issues": -1}},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			jsonBody, _ := json.Marshal(tc.body)
			req, _ := http.NewRequest("POST", "/api/tasks/"+taskID+"/quality-gates", bytes.NewBuffer(jsonBody))
			rr := httptest.NewRecorder()

			qualityGatesHandler(rr, req)

			if rr.Code != http.StatusBadRequest {
				t.Errorf("expected status 400, got %d. Body: %s", rr.Code, rr.Body.String())
			}
		})
	}
}

func TestQualityGatesHandler_InvalidJSON(t *testing.T) {
	db := setupQualityGateDB(t)
	defer db.Close()

	taskID := "test-task-qg-json"
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state)
		VALUES (?, 'test', 'test', 'feature', 1, 'completed', 'COMPLETED')`, taskID)

	req, _ := http.NewRequest("POST", "/api/tasks/"+taskID+"/quality-gates",
		bytes.NewBufferString("not-valid-json"))
	rr := httptest.NewRecorder()

	qualityGatesHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", rr.Code)
	}
}
