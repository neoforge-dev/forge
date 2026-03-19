//go:build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestWave114d_HandleTaskCreate(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	tests := []struct {
		name           string
		method         string
		body           interface{}
		expectedStatus int
		contains       string
	}{
		{
			name:           "Method Not Allowed",
			method:         http.MethodGet,
			body:           nil,
			expectedStatus: http.StatusMethodNotAllowed,
			contains:       "method not allowed",
		},
		{
			name:           "Invalid Request Body",
			method:         http.MethodPost,
			body:           "{invalid json",
			expectedStatus: http.StatusBadRequest,
			contains:       "invalid request body",
		},
		{
			name:   "Valid Creation",
			method: http.MethodPost,
			body: map[string]interface{}{
				"task": map[string]interface{}{
					"id":       "wave114d-task-1",
					"domain":   "test",
					"project":  "test-proj",
					"type":     "feature",
					"priority": 10,
					"status":   "pending",
					"title":    "Wave 114d Task",
				},
			},
			expectedStatus: http.StatusOK,
			contains:       "wave114d-task-1",
		},
		{
			name:   "Missing Title",
			method: http.MethodPost,
			body: map[string]interface{}{
				"task": map[string]interface{}{
					"id":       "wave114d-task-missing-title",
					"domain":   "test",
					"project":  "test-proj",
					"type":     "feature",
					"priority": 10,
				},
			},
			expectedStatus: http.StatusBadRequest,
			contains:       "title required",
		},
		{
			name:   "Invalid Task ID",
			method: http.MethodPost,
			body: map[string]interface{}{
				"task": map[string]interface{}{
					"id":       "INVALID ID WITH SPACES",
					"domain":   "test",
					"project":  "test-proj",
					"type":     "feature",
					"priority": 10,
					"title":    "Invalid ID Task",
				},
			},
			expectedStatus: http.StatusBadRequest,
			contains:       "invalid task ID format",
		},
		{
			name:   "Missing Domain",
			method: http.MethodPost,
			body: map[string]interface{}{
				"task": map[string]interface{}{
					"id":       "wave114d-task-missing-domain",
					"project":  "test-proj",
					"type":     "feature",
					"priority": 10,
					"title":    "Missing Domain Task",
				},
			},
			expectedStatus: http.StatusBadRequest,
			contains:       "invalid or missing domain",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var bodyReader *bytes.Reader
			if tt.body != nil {
				if s, ok := tt.body.(string); ok {
					bodyReader = bytes.NewReader([]byte(s))
				} else {
					b, _ := json.Marshal(tt.body)
					bodyReader = bytes.NewReader(b)
				}
			} else {
				bodyReader = bytes.NewReader([]byte(""))
			}

			req := httptest.NewRequest(tt.method, "/cli/task/create", bodyReader)
			w := httptest.NewRecorder()

			handleTaskCreate(w, req)

			if w.Code != tt.expectedStatus {
				t.Errorf("expected status %d, got %d. Body: %s", tt.expectedStatus, w.Code, w.Body.String())
			}
			if tt.contains != "" && !strings.Contains(w.Body.String(), tt.contains) {
				t.Errorf("expected body to contain %q, got %q", tt.contains, w.Body.String())
			}
		})
	}
}

func TestWave114d_HandleAgentList(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Test Empty List
	t.Run("Empty List", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/handleAgentList", nil)
		w := httptest.NewRecorder()
		handleAgentList(w, req)
		if w.Code != http.StatusOK {
			t.Errorf("expected status 200, got %d", w.Code)
		}
		// WriteOutputWithFlag for JSON with null/empty slice might return "null\n" or "[]\n"
		body := strings.TrimSpace(w.Body.String())
		if body != "null" && body != "[]" {
			t.Errorf("expected empty list, got %q", body)
		}
	})

	// Add some agents
	db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, current_task_id, last_seen)
		VALUES ('agent-1', 'prya', 'idle', 0.1, '', datetime('now'))`)
	db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, current_task_id, last_seen)
		VALUES ('agent-2', 'sati', 'busy', 0.8, 'task-123', datetime('now'))`)

	// formats: "json" and "yaml" (table needs terminal which is hard to mock here)
	formats := []struct {
		format   string
		contains string
	}{
		{"json", "agent-1"},
		{"yaml", "agentid: agent-1"}, // As seen in actual output
		{"yml", "agentid: agent-1"},
	}
	for _, tt := range formats {
		t.Run("Format "+tt.format, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/handleAgentList?format="+tt.format, nil)
			w := httptest.NewRecorder()
			handleAgentList(w, req)
			if w.Code != http.StatusOK {
				t.Errorf("expected status 200, got %d", w.Code)
			}
			body := w.Body.String()
			// Normalize body to lowercase to make it case-insensitive for YAML tag comparison if needed
			if !strings.Contains(strings.ToLower(body), strings.ToLower(tt.contains)) {
				t.Errorf("expected body to contain %q, got %q", tt.contains, body)
			}
		})
	}

	// Test DB Error path
	t.Run("DB Error", func(t *testing.T) {
		setDBConn(nil) // Cause getAllAgentsHealth to fail because of nil DB
		defer setDBConn(db)

		req := httptest.NewRequest(http.MethodGet, "/handleAgentList", nil)
		w := httptest.NewRecorder()
		handleAgentList(w, req)
		if w.Code != http.StatusInternalServerError {
			t.Errorf("expected status 500, got %d", w.Code)
		}
	})
}
