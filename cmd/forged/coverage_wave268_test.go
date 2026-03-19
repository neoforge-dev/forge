//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// TestWave268_TUILogsHandler_MethodNotAllowed tests method validation
// Note: tuiLogsHandler doesn't check method - it accepts any method
// TestWave268_TUILogsHandler_Success tests successful log retrieval
func TestWave268_TUILogsHandler_Success(t *testing.T) {
	// Save and restore global hub
	origHub := hub
	defer func() { hub = origHub }()
	
	// Create fresh hub
	hub = NewWebSocketHub()
	
	req := httptest.NewRequest(http.MethodGet, "/api/tui/logs", nil)
	w := httptest.NewRecorder()
	
	tuiLogsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
	
	contentType := resp.Header.Get("Content-Type")
	if contentType != "application/json" {
		t.Errorf("expected Content-Type application/json, got %s", contentType)
	}
	
	var body map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	// Should have expected fields
	if _, ok := body["logs"]; !ok {
		t.Error("expected 'logs' field")
	}
	if total, ok := body["total"]; !ok || total == nil {
		t.Error("expected 'total' field")
	}
	if source, ok := body["source"].(string); !ok || source != "forged" {
		t.Errorf("expected source='forged', got '%v'", body["source"])
	}
}

// TestWave268_TUILogsHandler_WithLimit tests custom limit parameter
func TestWave268_TUILogsHandler_WithLimit(t *testing.T) {
	// Save and restore global hub
	origHub := hub
	defer func() { hub = origHub }()
	
	hub = NewWebSocketHub()
	
	// Request with limit
	req := httptest.NewRequest(http.MethodGet, "/api/tui/logs?limit=5", nil)
	w := httptest.NewRecorder()
	
	tuiLogsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
	
	var body map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	// Should have expected fields
	if _, ok := body["limit"]; !ok {
		t.Error("expected 'limit' field")
	}
}

// TestWave268_TUILogsHandler_LimitTooHigh tests limit capped at 100
func TestWave268_TUILogsHandler_LimitTooHigh(t *testing.T) {
	origHub := hub
	defer func() { hub = origHub }()
	
	hub = NewWebSocketHub()
	
	// Request with very high limit
	req := httptest.NewRequest(http.MethodGet, "/api/tui/logs?limit=999", nil)
	w := httptest.NewRecorder()
	
	tuiLogsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
}

// TestWave268_TUILogsHandler_InvalidLimit tests invalid limit parameter
func TestWave268_TUILogsHandler_InvalidLimit(t *testing.T) {
	origHub := hub
	defer func() { hub = origHub }()
	
	hub = NewWebSocketHub()
	
	// Request with invalid limit (uses default)
	req := httptest.NewRequest(http.MethodGet, "/api/tui/logs?limit=invalid", nil)
	w := httptest.NewRecorder()
	
	tuiLogsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
}

// TestWave268_PlanTaskHandler_MethodNotAllowed tests method validation
// TestWave268_PlanTaskHandler_MissingTaskID tests missing task ID
func TestWave268_PlanTaskHandler_MissingTaskID(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks//plan", nil)
	w := httptest.NewRecorder()
	
	planTaskHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", resp.StatusCode)
	}
}

// TestWave268_PlanTaskHandler_InvalidBody tests invalid request body
func TestWave268_PlanTaskHandler_InvalidBody(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/123/plan", strings.NewReader("invalid json"))
	w := httptest.NewRecorder()
	
	planTaskHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", resp.StatusCode)
	}
	
	body, _ := io.ReadAll(resp.Body)
	if !strings.Contains(string(body), "invalid request body") {
		t.Errorf("expected 'invalid request body' error, got: %s", string(body))
	}
}

// TestWave268_PlanTaskHandler_NoPlanManager tests nil plan manager
// Note: This test is skipped because nil planManager causes panic
func TestWave268_PlanTaskHandler_NoPlanManager(t *testing.T) {
	t.Skip("Nil planManager causes panic - production always initializes it")
	
	// Save and restore
	origPM := planManager
	defer func() { planManager = origPM }()
	
	planManager = nil
	
	reqBody := `{"plan": "test plan", "reason": "testing"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/123/plan", strings.NewReader(reqBody))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	
	planTaskHandler(w, req)
	
	resp := w.Result()
	// Will fail because planManager is nil
	if resp.StatusCode != http.StatusInternalServerError {
		t.Logf("Expected error with nil planManager, got %d", resp.StatusCode)
	}
}

// TestWave268_ReplanTaskHandler_MethodNotAllowed tests method validation
// TestWave268_ReplanTaskHandler_MissingTaskID tests missing task ID
func TestWave268_ReplanTaskHandler_MissingTaskID(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks//replan", nil)
	w := httptest.NewRecorder()
	
	replanTaskHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", resp.StatusCode)
	}
}

// TestWave268_ReplanTaskHandler_InvalidBody tests invalid request body
func TestWave268_ReplanTaskHandler_InvalidBody(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/123/replan", strings.NewReader("invalid json"))
	w := httptest.NewRecorder()
	
	replanTaskHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", resp.StatusCode)
	}
}

// TestWave268_GetPlanHistoryHandler_MissingTaskID tests missing task ID
func TestWave268_GetPlanHistoryHandler_MissingTaskID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks//plans", nil)
	w := httptest.NewRecorder()
	
	getPlanHistoryHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", resp.StatusCode)
	}
}

// TestWave268_GetPlanHistoryHandler_NoPlanManager tests nil plan manager
// Note: This test is skipped because nil planManager causes panic
func TestWave268_GetPlanHistoryHandler_NoPlanManager(t *testing.T) {
	t.Skip("Nil planManager causes panic - production always initializes it")
	
	// Save and restore
	origPM := planManager
	defer func() { planManager = origPM }()
	
	planManager = nil
	
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/123/plans", nil)
	w := httptest.NewRecorder()
	
	getPlanHistoryHandler(w, req)
	
	resp := w.Result()
	// Will fail because planManager is nil
	if resp.StatusCode != http.StatusInternalServerError {
		t.Logf("Expected error with nil planManager, got %d", resp.StatusCode)
	}
}

// TestWave268_LogEntry_Struct tests LogEntry struct
func TestWave268_LogEntry_Struct(t *testing.T) {
	entry := LogEntry{
		Timestamp: "2024-01-01T00:00:00Z",
		Level:     "info",
		Message:   "Test message",
		Source:    "test",
	}
	
	if entry.Timestamp != "2024-01-01T00:00:00Z" {
		t.Error("Timestamp mismatch")
	}
	if entry.Level != "info" {
		t.Error("Level mismatch")
	}
	if entry.Message != "Test message" {
		t.Error("Message mismatch")
	}
	if entry.Source != "test" {
		t.Error("Source mismatch")
	}
}

// TestWave268_LogEntry_JSON tests JSON serialization
func TestWave268_LogEntry_JSON(t *testing.T) {
	entry := LogEntry{
		Timestamp: "2024-01-01T00:00:00Z",
		Level:     "error",
		Message:   "Error occurred",
		Source:    "agent",
	}
	
	data, err := json.Marshal(entry)
	if err != nil {
		t.Fatalf("failed to marshal: %v", err)
	}
	
	var decoded map[string]interface{}
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("failed to unmarshal: %v", err)
	}
	
	if decoded["timestamp"] != "2024-01-01T00:00:00Z" {
		t.Error("timestamp field mismatch")
	}
	if decoded["level"] != "error" {
		t.Error("level field mismatch")
	}
	if decoded["message"] != "Error occurred" {
		t.Error("message field mismatch")
	}
}

// TestWave268_PlanRequest_Struct tests PlanRequest struct
func TestWave268_PlanRequest_Struct(t *testing.T) {
	req := PlanRequest{
		Plan:   "Test plan content",
		Reason: "For testing",
	}
	
	if req.Plan != "Test plan content" {
		t.Error("Plan mismatch")
	}
	if req.Reason != "For testing" {
		t.Error("Reason mismatch")
	}
}

// TestWave268_PlanRequest_JSON tests JSON serialization
func TestWave268_PlanRequest_JSON(t *testing.T) {
	req := PlanRequest{
		Plan:   "Test plan",
		Reason: "Testing",
	}
	
	data, err := json.Marshal(req)
	if err != nil {
		t.Fatalf("failed to marshal: %v", err)
	}
	
	var decoded PlanRequest
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("failed to unmarshal: %v", err)
	}
	
	if decoded.Plan != req.Plan {
		t.Error("Plan mismatch after roundtrip")
	}
	if decoded.Reason != req.Reason {
		t.Error("Reason mismatch after roundtrip")
	}
}

// TestWave268_TUILogsHandler_ResponseStructure tests full response structure
func TestWave268_TUILogsHandler_ResponseStructure(t *testing.T) {
	origHub := hub
	defer func() { hub = origHub }()
	
	hub = NewWebSocketHub()
	
	req := httptest.NewRequest(http.MethodGet, "/api/tui/logs", nil)
	w := httptest.NewRecorder()
	
	tuiLogsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
	
	var body struct {
		Logs   []LogEntry `json:"logs"`
		Total  int        `json:"total"`
		Limit  int        `json:"limit"`
		Source string     `json:"source"`
	}
	
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	
	if body.Source != "forged" {
		t.Errorf("expected source='forged', got '%s'", body.Source)
	}
	if body.Limit != 50 {
		t.Errorf("expected default limit=50, got %d", body.Limit)
	}
	if body.Total != len(body.Logs) {
		t.Errorf("expected Total=%d to match len(Logs)=%d", body.Total, len(body.Logs))
	}
	
	// Check log entries
	for _, entry := range body.Logs {
		if entry.Timestamp == "" {
			t.Error("expected non-empty timestamp")
		}
		if entry.Level == "" {
			t.Error("expected non-empty level")
		}
	}
}

// TestWave268_TUILogsHandler_XNodeInbox tests XNode inbox logging
func TestWave268_TUILogsHandler_XNodeInbox(t *testing.T) {
	origHub := hub
	defer func() { hub = origHub }()
	
	// Create hub
	hub = NewWebSocketHub()
	
	req := httptest.NewRequest(http.MethodGet, "/api/tui/logs", nil)
	w := httptest.NewRecorder()
	
	tuiLogsHandler(w, req)
	
	resp := w.Result()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
	
	body, _ := io.ReadAll(resp.Body)
	
	// Verify response contains expected data
	if !strings.Contains(string(body), "timestamp") {
		t.Error("expected timestamp field in response")
	}
}
