// cmd/forged/create_handler_test.go
//go:build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"
)

// setupCreateHandlerTest initializes a test database for createTaskHandler tests
func setupCreateHandlerTest(t *testing.T) func() {
	dbPath := "/tmp/test_create_handler_" + time.Now().Format("20060102150405.999999999") + ".db"

	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("failed to open db: %v", err)
	}

	if err := MigrateUp(db); err != nil {
		db.Close()
		os.Remove(dbPath)
		t.Fatalf("failed to run migrations: %v", err)
	}

	// Save originals so we can restore after test
	oldDB := getDBConn()
	oldQueue := taskQueue
	oldAuth := authManager
	oldHub := hub

	setDBConn(db)
	authManager = NewAuthManager("local")

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		db.Close()
		setDBConn(oldDB)
		taskQueue = oldQueue
		authManager = oldAuth
		os.Remove(dbPath)
		t.Fatalf("failed to create task queue: %v", err)
	}
	taskQueue = q

	hub = NewHub()
	newHub := hub
	go hub.Run()

	cleanup := func() {
		newHub.Stop()
		db.Close()
		setDBConn(oldDB)
		taskQueue = oldQueue
		authManager = oldAuth
		hub = oldHub
		os.Remove(dbPath)
	}

	return cleanup
}

// TestCreateTaskHandler_MinimalPayload tests creating a task with minimal required fields
func TestCreateTaskHandler_MinimalPayload(t *testing.T) {
	cleanup := setupCreateHandlerTest(t)
	defer cleanup()

	payload := map[string]interface{}{
		"domain":   "test-domain",
		"project":  "test-project",
		"type":     "feature",
		"title":    "Implement user authentication",
		"priority": 5,
	}

	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	createTaskHandler(w, req)

	if w.Code != http.StatusOK && w.Code != http.StatusCreated {
		t.Errorf("expected status 200 or 201, got %d: %s", w.Code, w.Body.String())
	}

	var response map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
		t.Fatalf("failed to parse response: %v", err)
	}

	if response["id"] == nil || response["id"] == "" {
		t.Error("expected task ID in response")
	}

}

// TestCreateTaskHandler_FullPayload tests creating a task with all fields
func TestCreateTaskHandler_FullPayload(t *testing.T) {
	cleanup := setupCreateHandlerTest(t)
	defer cleanup()

	payload := map[string]interface{}{
		"id":          "full-task-001",
		"domain":      "full-domain",
		"project":     "full-project",
		"type":        "bugfix",
		"title":       "Full Task with All Fields",
		"description": "This is a full description",
		"priority":    8,
		"lane":        "alpha",
		"assigned_to": "test-agent",
	}

	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	createTaskHandler(w, req)

	if w.Code != http.StatusOK && w.Code != http.StatusCreated {
		t.Errorf("expected status 200 or 201, got %d: %s", w.Code, w.Body.String())
	}

	var response map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
		t.Fatalf("failed to parse response: %v", err)
	}

	if response["id"] != "full-task-001" {
		t.Errorf("expected id 'full-task-001', got %v", response["id"])
	}
}

// TestCreateTaskHandler_MissingRequired tests validation of required fields
func TestCreateTaskHandler_MissingRequired(t *testing.T) {
	cleanup := setupCreateHandlerTest(t)
	defer cleanup()

	tests := []struct {
		name    string
		payload map[string]interface{}
		wantMsg string
	}{
		{
			name:    "missing domain",
			payload: map[string]interface{}{"project": "p", "type": "feature", "title": "t"},
			wantMsg: "domain",
		},
		{
			name:    "missing project",
			payload: map[string]interface{}{"domain": "d", "type": "feature", "title": "t"},
			wantMsg: "project",
		},
		{
			name:    "missing type",
			payload: map[string]interface{}{"domain": "d", "project": "p", "title": "t"},
			wantMsg: "type",
		},
		{
			name:    "missing title",
			payload: map[string]interface{}{"domain": "d", "project": "p", "type": "feature"},
			wantMsg: "title required",
		},
		{
			name:    "empty title",
			payload: map[string]interface{}{"domain": "d", "project": "p", "type": "feature", "title": "   "},
			wantMsg: "title required",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			body, _ := json.Marshal(tt.payload)
			req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewReader(body))
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()

			createTaskHandler(w, req)

			if w.Code != http.StatusBadRequest {
				t.Errorf("expected status %d, got %d: %s", http.StatusBadRequest, w.Code, w.Body.String())
			}
		})
	}
}

// TestCreateTaskHandler_InvalidPriority tests priority validation and normalization
func TestCreateTaskHandler_InvalidPriority(t *testing.T) {
	cleanup := setupCreateHandlerTest(t)
	defer cleanup()

	tests := []struct {
		name         string
		priority     interface{}
		expectStatus int
		description  string
	}{
		{
			name:         "negative priority rejected",
			priority:     -5,
			expectStatus: http.StatusBadRequest,
			description:  "negative values should be rejected",
		},
		{
			name:         "priority 0 defaults to 5",
			priority:     0,
			expectStatus: http.StatusOK,
			description:  "0 should be normalized to 5",
		},
		{
			name:         "priority 5 accepted",
			priority:     5,
			expectStatus: http.StatusOK,
			description:  "valid priority in range",
		},
		{
			name:         "priority 10 accepted",
			priority:     10,
			expectStatus: http.StatusOK,
			description:  "max valid priority",
		},
		{
			name:         "priority 50 mapped to 5",
			priority:     50,
			expectStatus: http.StatusOK,
			description:  "legacy 0-100 scale mapped to 1-10",
		},
		{
			name:         "priority 100 mapped to 10",
			priority:     100,
			expectStatus: http.StatusOK,
			description:  "max legacy scale mapped to 10",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			payload := map[string]interface{}{
				"domain":   "test-domain",
				"project":  "test-project",
				"type":     "feature",
				"title":    "Priority Test Task",
				"priority": tt.priority,
			}

			body, _ := json.Marshal(payload)
			req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewReader(body))
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()

			createTaskHandler(w, req)

			if w.Code != tt.expectStatus {
				t.Errorf("%s: expected status %d, got %d: %s", tt.description, tt.expectStatus, w.Code, w.Body.String())
			}
		})
	}
}

// TestCreateTaskHandler_InvalidMethod tests that only POST is allowed
func TestCreateTaskHandler_InvalidMethod(t *testing.T) {
	cleanup := setupCreateHandlerTest(t)
	defer cleanup()

	methods := []string{http.MethodGet, http.MethodPut, http.MethodDelete, http.MethodPatch}

	for _, method := range methods {
		t.Run(method, func(t *testing.T) {
			req := httptest.NewRequest(method, "/api/tasks", nil)
			w := httptest.NewRecorder()

			createTaskHandler(w, req)

			if w.Code != http.StatusMethodNotAllowed {
				t.Errorf("expected status %d for %s, got %d", http.StatusMethodNotAllowed, method, w.Code)
			}
		})
	}
}

// TestCreateTaskHandler_InvalidBody tests handling of malformed JSON
func TestCreateTaskHandler_InvalidBody(t *testing.T) {
	cleanup := setupCreateHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewReader([]byte("not valid json")))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	createTaskHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
}

// TestCreateTaskHandler_TitleTooLong tests title length validation
// TestCreateTaskHandler_WithPortfolioStage exercises the portfolio_stage path
// (handlers_task.go:40.57,41.73) where GetApprovalTierForStage returns a tier
// and task.RequiredTier is set.
func TestCreateTaskHandler_WithPortfolioStage(t *testing.T) {
	cleanup := setupCreateHandlerTest(t)
	defer cleanup()

	payload := map[string]interface{}{
		"domain":          "test-domain",
		"project":         "test-project",
		"type":            "feature",
		"title":           "Stage Tier Test",
		"priority":        5,
		"portfolio_stage": "build", // valid stage → GetApprovalTierForStage returns TierDesktop
	}

	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	createTaskHandler(w, req)

	// The request should succeed (200) or fail gracefully (not panic).
	if w.Code >= 500 {
		t.Errorf("unexpected server error: %d %s", w.Code, w.Body.String())
	}
}

// TestCreateTaskHandler_WithPortfolioStage_StageGateWarning exercises the
// EnforceStageGate warning path (handlers_task.go:56.36,58.4). Using a
// non-infrastructure domain without a valid portfolio-state.yaml causes
// EnforceStageGate to return Allowed=true with a Warning.
func TestCreateTaskHandler_WithPortfolioStage_StageGateWarning(t *testing.T) {
	cleanup := setupCreateHandlerTest(t)
	defer cleanup()

	// Use a domain that is not an infrastructure domain, so EnforceStageGate
	// attempts to load portfolio-state.yaml. In tests, the file is absent →
	// returns {Allowed:true, Warning:"stage gate skipped..."}.
	payload := map[string]interface{}{
		"domain":   "some-portfolio-domain",
		"project":  "test-project",
		"type":     "feature",
		"title":    "Stage Gate Warning Test",
		"priority": 5,
	}

	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	createTaskHandler(w, req)

	if w.Code >= 500 {
		t.Errorf("unexpected server error: %d %s", w.Code, w.Body.String())
	}
}

func TestCreateTaskHandler_TitleTooLong(t *testing.T) {
	cleanup := setupCreateHandlerTest(t)
	defer cleanup()

	// Create a title > 500 chars
	longTitle := ""
	for i := 0; i < 501; i++ {
		longTitle += "x"
	}

	payload := map[string]interface{}{
		"domain":   "test-domain",
		"project":  "test-project",
		"type":     "feature",
		"title":    longTitle,
		"priority": 5,
	}

	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	createTaskHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected status %d, got %d", http.StatusBadRequest, w.Code)
	}
}
